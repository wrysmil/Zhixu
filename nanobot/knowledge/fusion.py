"""融合层：三分支归一化 + 三级去重 + MMR。

归一化（检索 spec §5.2，三条互斥路径；**不**做「按参与路由重新归一化」）：
  A 只有向量路：fused = 原始 cosine（`max(0, 1 - distance)`），不缩放、不进 RRF；
  B 只有关键词路：fused = score / max(score)，仅在 max > 1 时除；
  C 两路都有：
      RRF(c)   = w_v/(k + rank_v) + w_k/(k + rank_k)   # 只在该路命中时才加该项
      maxRRF   = (w_v + w_k) / (k + 1)
      fused(c) = RRF(c) / maxRRF ∈ [0,1]

分支 C 的单路天花板（仅向量 rank1 = 0.7、仅关键词 rank1 = 0.3）是刻意设计：
它保住「双路共识才有最高分」的语义（spec §2.3 / §5.2）。三条分支量纲不同、
跨模式不可比，由调用方的 `mode_used` / `routes` 标注承担。
融合分只由名次决定，不引入 cosine / bm25 量纲；输出 id 一定来自输入。
所有出口都按 `(-score, chunk_id)` 写全序，保证同输入同输出（spec §5.3）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nanobot.knowledge.tokenizer import tokenize_index

if TYPE_CHECKING:
    from nanobot.knowledge.repository import FtsHit

#: §5.4 保留规则：相对分数差在此以内视为「同分」，改取更长块。
_SCORE_TIE_TOLERANCE = 0.05


@dataclass(frozen=True, slots=True)
class SearchHit:
    """检索命中的统一读模型。

    `score` 是该分支（cosine / BM25 相对分 / RRF 归一化分）下的展示分，
    只表示「当前模式下的相对排序」，不是置信度。

    `title` 参与 enriched passage（§8.2）：标题相同的块本来就更可能冗余，
    精排与 MMR 用的是同一份 enriched text。
    `source_chunk_ids` 记录装配后的来源块（§9.1 合并连续正文时多于一块）。
    """

    chunk_id: str
    doc_id: str
    kb_id: str
    chunk_index: int
    heading_path: str
    page: int | None
    locator: str
    content: str
    char_count: int
    content_hash: str = ""
    score: float = 0.0
    title: str = ""
    source_chunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchResult:
    """一次检索的完整结果；degraded/reason 用于向调用方显式上报降级与空因。

    `routes` 记录**实际产出候选**的路由（顺序恒为 vector → keyword），
    让调用方知道证据来自哪一路（spec §7.3 R9）；空结果时为 `()`。

    `candidates_in` / `candidates_kept` 记录精排的进出条数（§7 RecordRetrievalCut）：
    模型必须知道结果是候选的子集，否则会把「被截断」误读成「知识库里只有这些」。
    """

    hits: tuple[SearchHit, ...]
    mode_used: str
    degraded: tuple[str, ...] = ()
    reason: str | None = None
    routes: tuple[str, ...] = ()
    rerank: str | None = None
    candidates_in: int = 0
    candidates_kept: int = 0


def hit_sort_key(hit: SearchHit) -> tuple[float, str, int, str]:
    """确定性排序键（§9.2）：Score → KnowledgeID → ChunkIndex → ID。

    同样的请求连跑两次，同分结果的顺序必须一致——否则用户会看到「答案在变」，
    调试也无法复现。`chunk_index` 进键之前，同文档的块只能靠 chunk_id（哈希）
    决胜，顺序与文档语义无关。
    """
    return (-hit.score, hit.kb_id, hit.chunk_index, hit.chunk_id)


def _ranked(scores: dict[str, float]) -> dict[str, float]:
    """按 (-score, chunk_id) 重排，把全序写进 dict 的插入顺序。"""
    return dict(sorted(scores.items(), key=lambda item: (-item[1], item[0])))


def weighted_rrf(
    vector_ids: Sequence[str],
    keyword_ids: Sequence[str],
    *,
    w_v: float = 0.7,
    w_k: float = 0.3,
    k: int = 60,
) -> dict[str, float]:
    """分支 C：两路都有候选时的加权 RRF，映射到 [0,1]（spec §5.2）。

    `RRF(c) = w_v/(k+rank_v) + w_k/(k+rank_k)`，分母固定为 `maxRRF = (w_v+w_k)/(k+1)`。
    因此仅向量 rank1 = 0.7、仅关键词 rank1 = 0.3——**不按参与路由重新归一化**
    （spec §5.2 明确否决该做法）。同一路内的重复 id 只认首次名次；权重同比例
    缩放不改变结果（分子与分母同比例）。两路都为空返回 `{}`。
    """
    return weighted_rrf_multi(
        (
            RouteSpec(w_v, (tuple(vector_ids),)),
            RouteSpec(w_k, (tuple(keyword_ids),)),
        ),
        k=k,
    )


@dataclass(frozen=True, slots=True)
class RouteSpec:
    """一路召回：权重 + 该路的多张列表（每 KB / 每引擎一张）。

    多张列表对应文章 §5.4 的场景：同一块可能在多条列表里出现（多个 KB、多个引擎），
    它的名次取「在任意一张列表里的最好名次」，而不是把名次相加。
    """

    weight: float
    lists: tuple[tuple[str, ...], ...]


def best_ranks(lists: Sequence[Sequence[str]]) -> dict[str, int]:
    """跨列表取最佳名次（§5.4 bestRanks）。

    名次是「相对排序」，跨列表可比；分数是「绝对强度」，跨列表不可比。
    拼接后再排名是错的：第二张列表的第一名会排到第一张列表全部条目之后。
    """
    best: dict[str, int] = {}
    for ids in lists:
        for rank, chunk_id in enumerate(ids, 1):
            current = best.get(chunk_id)
            if current is None or rank < current:
                best[chunk_id] = rank
    return best


def weighted_rrf_multi(routes: Sequence[RouteSpec], *, k: int = 60) -> dict[str, float]:
    """多路加权 RRF（§5.3）：`RRF(c) = Σ_route w_route/(k + best_rank_route(c))`。

    每路的权重之和是天花板：`maxRRF = Σw/(k+1)`，除以它把分数映射到 [0,1]，
    「各路都排第一」恰好是 1.0。仅一路命中时封顶就是那路的权重——
    这是「双路共识才有最高分」的语义，不是 bug。
    """
    if k < 0:
        raise ValueError(f"rrf k must be non-negative: {k}")
    total_weight = sum(route.weight for route in routes)
    if total_weight <= 0.0:
        return {}
    max_rrf = total_weight / (k + 1)
    scores: dict[str, float] = {}
    for route in routes:
        if route.weight <= 0.0:
            continue
        for chunk_id, rank in best_ranks(route.lists).items():
            scores[chunk_id] = scores.get(chunk_id, 0.0) + route.weight / (k + rank)
    if not scores:
        return {}
    # §5.3：融合分取 9 位小数，消掉浮点尾数抖动，并让排序可复现。
    fused = {chunk_id: round(score / max_rrf, 9) for chunk_id, score in scores.items()}
    return _ranked(fused)


def single_vector_scores_multi(
    lists: Sequence[Sequence[tuple[str, float]]],
) -> dict[str, float]:
    """分支 A 的多列表形态：cosine 已是可比量纲，跨列表取最大值。

    不同 embedding 模型的余弦尺度接近但不等价；取最大值而不是相加——
    相加会让「两个模型都认为相关」的块凭空翻倍。
    """
    best: dict[str, float] = {}
    for hits in lists:
        for chunk_id, score in hits:
            current = best.get(chunk_id)
            if current is None or score > current:
                best[chunk_id] = float(score)
    return _ranked(best)


def single_keyword_scores_multi(
    lists: Sequence[Sequence[FtsHit | tuple[str, float]]],
) -> dict[str, float]:
    """分支 B 的多列表形态：每张列表各自除以自身最大值，再跨列表取最大值（§5.2）。

    BM25 无上界且不同引擎/索引的绝对值不可比；「各自的 max」才是合理参照。
    """
    best: dict[str, float] = {}
    for hits in lists:
        local: dict[str, float] = {}
        for hit in hits:
            chunk_id, score = (
                hit if isinstance(hit, tuple) else (hit.chunk_id, hit.score)
            )
            current = local.get(chunk_id)
            if current is None or score > current:
                local[chunk_id] = float(score)
        if not local:
            continue
        peak = max(local.values())
        if peak > 1.0:
            local = {chunk_id: score / peak for chunk_id, score in local.items()}
        for chunk_id, score in local.items():
            current = best.get(chunk_id)
            if current is None or score > current:
                best[chunk_id] = score
    return _ranked(best)


def single_vector_scores(vector_hits: Sequence[tuple[str, float]]) -> dict[str, float]:
    """分支 A：只有向量路时保留原始 cosine（spec §5.2）。

    输入是 `(chunk_id, cosine)`（cosine 已是 `max(0, 1 - distance)`）；同一 id 取最大分，
    不做 RRF、不除以 max（缩放会破坏绝对相似度语义），按 (-score, chunk_id) 排序。
    """
    best: dict[str, float] = {}
    for chunk_id, score in vector_hits:
        current = best.get(chunk_id)
        if current is None or score > current:
            best[chunk_id] = float(score)
    return _ranked(best)


def single_keyword_scores(
    keyword_hits: Sequence[FtsHit | tuple[str, float]],
) -> dict[str, float]:
    """分支 B：只有关键词路时把 BM25 分除以该列表自身最大值（spec §5.2）。

    输入可以是 `FtsHit`（`score = -bm25()`）或 `(chunk_id, bm25_score)`；同一 id 取最大分，
    再按最大值归一化（仅当 max > 1，否则原样保留），输出按 (-score, chunk_id) 排序。
    BM25 无上界且随语料漂移，因此「各自的 max」才是合理参照。
    """
    best: dict[str, float] = {}
    for hit in keyword_hits:
        chunk_id, score = (hit if isinstance(hit, tuple) else (hit.chunk_id, hit.score))
        current = best.get(chunk_id)
        if current is None or score > current:
            best[chunk_id] = float(score)
    if not best:
        return {}
    peak = max(best.values())
    if peak > 1.0:
        best = {chunk_id: score / peak for chunk_id, score in best.items()}
    return _ranked(best)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(tokenize_index(text).split())


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left and not right:
        return 0.0
    return len(left & right) / len(left | right)


def jaccard(left: str, right: str) -> float:
    """词面 Jaccard；空集（含两侧都空）记 0。"""
    return _jaccard(_tokens(left), _tokens(right))


def _preferred(left: SearchHit, right: SearchHit) -> SearchHit:
    """§5.4 保留规则：分数差 > 5% 取高分；否则取更长块；再同取 chunk_id 小者。"""
    if left.score < right.score:
        left, right = right, left
    if left.score - right.score > _SCORE_TIE_TOLERANCE * left.score:
        return left
    if left.char_count != right.char_count:
        return left if left.char_count > right.char_count else right
    return left if left.chunk_id <= right.chunk_id else right


def _is_partial_duplicate(
    left_text: str,
    right_text: str,
    left: frozenset[str],
    right: frozenset[str],
    *,
    length_ratio: float,
    containment_threshold: float,
    unique_ratio: float,
) -> bool:
    """级别 ③：长度比 + 子串 / token 覆盖（WeKnora §6）。

    WeKnora 口径：
    ```
    ratio       = len(B)/len(A)          # A = token 集合更小的一侧
    short_text 是 long_text 的子串       → 重复
    或 token 覆盖率 >= 0.85 且短文本独有 token <= 2% → 重复
    且 ratio <= 3
    ```
    长度比按 token 集合大小；空集不判重。v1 曾用「包含率 0.98 或 Jaccard 0.6」，
    与 WeKnora 不一致，已按文章口径收敛。
    """
    if not left or not right:
        return False
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(longer) / len(shorter) > length_ratio:
        return False
    short_text, long_text = (
        (left_text, right_text) if len(left_text) <= len(right_text) else (right_text, left_text)
    )
    if short_text and short_text in long_text:
        return True
    containment = len(shorter & longer) / len(shorter)
    return containment >= containment_threshold and (1.0 - containment) <= unique_ratio + 1e-9


def dedup_partial(
    hits: Sequence[SearchHit],
    *,
    length_ratio: float = 3.0,
    containment: float = 0.85,
    unique_ratio: float = 0.02,
) -> list[SearchHit]:
    """三级去重（spec §5.4）：① ID 合并（在 RRF 内完成）→ ② content_hash → ③ 部分包含。

    级别 ② `content_hash` 相同且非空即重复；级别 ③ 见 `_is_partial_duplicate`。
    保留规则见 `_preferred`（分数差 > 5% 取高分，否则取更长块，再按 chunk_id）。
    按 (-score, chunk_id) 处理并在末尾重排，因此结果与输入顺序无关。
    绝不使用 doc_id / chunk_index：同文档邻块必须留给 MMR 的邻块下限（§5.5），
    不能在这里硬删除。
    """
    if not hits:
        return []
    ordered = sorted(hits, key=hit_sort_key)
    kept: list[SearchHit] = []
    kept_tokens: list[frozenset[str]] = []
    for hit in ordered:
        tokens = _tokens(hit.content)
        absorbed = False
        for index, other in enumerate(kept):
            same_hash = bool(hit.content_hash) and hit.content_hash == other.content_hash
            duplicate = same_hash or _is_partial_duplicate(
                hit.content,
                other.content,
                tokens,
                kept_tokens[index],
                length_ratio=length_ratio,
                containment_threshold=containment,
                unique_ratio=unique_ratio,
            )
            if not duplicate:
                continue
            if _preferred(other, hit) is hit:
                kept[index] = hit
                kept_tokens[index] = tokens
            absorbed = True
            break
        if not absorbed:
            kept.append(hit)
            kept_tokens.append(tokens)
    kept.sort(key=hit_sort_key)
    return kept


def _enriched_tokens(hit: SearchHit) -> frozenset[str]:
    """enriched passage 的 token：标题 + 面包屑 + 正文（§8.2）。"""
    return _tokens(f"{hit.title}\n{hit.heading_path}\n{hit.content}")


def mmr_rerank(
    hits: Sequence[SearchHit],
    *,
    limit: int,
    lambda_: float = 0.7,
) -> list[SearchHit]:
    """MMR 贪心选择（§8）：`MMR(i) = λ·score(i) − (1−λ)·max_{j∈已选} Jaccard(i, j)`。

    λ=0.7；相似度用 enriched passage 的词面 Jaccard，不用向量余弦——余弦衡量
    「语义接近」而不是「内容重复」。用倒排表（token → 候选下标）维护共享边，
    复杂度从 O(k·n²) 降到 O(k·共享边)；k=1 时直接取最高分，不做分词。
    输出顺序仍按 `score` 降序（MMR 只决定选哪些），故输出分数单调不增。
    """
    if limit <= 0 or not hits:
        return []
    ordered = sorted(hits, key=hit_sort_key)
    if limit == 1:
        # 没有冗余项，直接取最高分，避免无谓的分词和倒排构建（§8.4）。
        return [ordered[0]]
    tokens = [_enriched_tokens(hit) for hit in ordered]
    postings: dict[str, list[int]] = {}
    for index, token_set in enumerate(tokens):
        for token in token_set:
            postings.setdefault(token, []).append(index)
    remaining = set(range(len(ordered)))
    max_similarity: dict[int, float] = {index: 0.0 for index in remaining}
    selected: list[int] = []
    while remaining and len(selected) < limit:
        best_index = max(
            remaining,
            key=lambda index: (
                lambda_ * ordered[index].score - (1.0 - lambda_) * max_similarity[index],
                -index,
            ),
        )
        selected.append(best_index)
        remaining.discard(best_index)
        chosen_tokens = tokens[best_index]
        for token in chosen_tokens:
            for candidate in postings.get(token, ()):
                if candidate not in remaining:
                    continue
                similarity = _jaccard(chosen_tokens, tokens[candidate])
                if similarity > max_similarity[candidate]:
                    max_similarity[candidate] = similarity
    picked = [ordered[index] for index in selected]
    picked.sort(key=hit_sort_key)
    return picked
