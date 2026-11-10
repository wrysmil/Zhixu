"""融合层：三分支归一化 + 三级去重 + 邻块下限 / 相关性带 / MMR。

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


@dataclass(frozen=True, slots=True)
class SearchResult:
    """一次检索的完整结果；degraded/reason 用于向调用方显式上报降级与空因。

    `routes` 记录**实际产出候选**的路由（顺序恒为 vector → keyword），
    让调用方知道证据来自哪一路（spec §7.3 R9）；空结果时为 `()`。
    """

    hits: tuple[SearchHit, ...]
    mode_used: str
    degraded: tuple[str, ...] = ()
    reason: str | None = None
    routes: tuple[str, ...] = ()
    rerank: str | None = None


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
    if k < 0:
        raise ValueError(f"rrf k must be non-negative: {k}")
    total_weight = w_v + w_k
    if not vector_ids and not keyword_ids:
        return {}
    if total_weight <= 0.0:
        return {}
    max_rrf = total_weight / (k + 1)
    scores: dict[str, float] = {}
    for ids, weight in ((vector_ids, w_v), (keyword_ids, w_k)):
        seen: set[str] = set()
        for rank, chunk_id in enumerate(ids, 1):
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (k + rank)
    # §5.3：融合分取 9 位小数，消掉浮点尾数抖动，并让排序可复现。
    fused = {chunk_id: round(score / max_rrf, 9) for chunk_id, score in scores.items()}
    return _ranked(fused)


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
    ordered = sorted(hits, key=lambda hit: (-hit.score, hit.chunk_id))
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
    kept.sort(key=lambda hit: (-hit.score, hit.chunk_id))
    return kept


def _similarity(
    left: SearchHit,
    left_tokens: frozenset[str],
    right: SearchHit,
    right_tokens: frozenset[str],
    adjacent_similarity_floor: float,
) -> float:
    """MMR 相似度：词面 Jaccard；同文档邻块抬到下限（spec §5.5）。"""
    similarity = _jaccard(left_tokens, right_tokens)
    if left.doc_id == right.doc_id and abs(left.chunk_index - right.chunk_index) <= 1:
        similarity = max(similarity, adjacent_similarity_floor)
    return similarity


def mmr_rerank(
    hits: Sequence[SearchHit],
    *,
    limit: int,
    lambda_: float = 0.7,
    relevance_band: float = 0.15,
    adjacent_similarity_floor: float = 0.25,
) -> list[SearchHit]:
    """相关性带过滤 + MMR 贪心选择；展示顺序按融合分降序（spec §5.5/§5.6/§5.7）。

    `mmr = λ·rel − (1−λ)·max_sim`，`rel = fused`；候选必须满足
    `fused >= fused_max − relevance_band`，且同文档邻块的相似度不低于
    `adjacent_similarity_floor`（压制 overlap 产生的冗余，但不硬删除）。
    MMR 只决定选哪些，展示顺序仍是 `fused` 降序（tie-break `chunk_id` 升序），
    故输出分数单调不增。
    """
    if limit <= 0 or not hits:
        return []
    ordered = sorted(hits, key=lambda hit: (-hit.score, hit.chunk_id))
    cutoff = round(round(ordered[0].score, 9) - relevance_band, 9)
    candidates = [hit for hit in ordered if round(hit.score, 9) >= cutoff]
    # WeKnora §8：MMR 的 token 来自 enriched passage（标题/面包屑 + 正文），不是纯正文。
    tokens = [_tokens(f"{hit.heading_path}\n{hit.content}") for hit in candidates]
    remaining = list(range(len(candidates)))
    selected: list[int] = []
    while remaining and len(selected) < limit:
        best_position = 0
        best_score = float("-inf")
        for position, index in enumerate(remaining):
            similarity = max(
                (
                    _similarity(
                        candidates[index],
                        tokens[index],
                        candidates[chosen],
                        tokens[chosen],
                        adjacent_similarity_floor,
                    )
                    for chosen in selected
                ),
                default=0.0,
            )
            score = lambda_ * candidates[index].score - (1.0 - lambda_) * similarity
            if score > best_score:
                best_score = score
                best_position = position
        selected.append(remaining.pop(best_position))
    picked = [candidates[index] for index in selected]
    picked.sort(key=lambda hit: (-hit.score, hit.chunk_id))
    return picked
