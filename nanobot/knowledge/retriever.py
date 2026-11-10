"""检索：作用域 → 两路召回 → 三分支融合 → 去重 → MMR（spec §3–§7）。

口径全部来自 `.ai-runtime-artifacts/specs/2026-11-09-knowledge-retrieval-spec.md`：

- 作用域：`list_kbs` 不筛 `is_enabled`，本层补筛；显式 `kb_ids` 越权/不存在直接报错，
  绝不静默降级成「搜不到」（§3 ①）。
- 分词：`tokenize_query` 与索引同一函数；空表达式跳过关键词路，**绝不发送空 MATCH**（§4.1）。
- 候选池不对称：关键词 `min(max(limit×10, keyword_pool_size), 300)`，
  向量 `min(max(limit×5, pool_size), 200)`（§4.3）。
- 向量路按 KB 的 `(backend, model, dimensions)` 与当前 embedder 三元组分组：
  `embedding_backend='none'` 记 `vector_disabled:<kb>`，身份不一致记
  `embedding_mismatch:<kb>`，其余只对一致子集查询（§4.2 R13）。
- 融合交给 `fusion` 的三条互斥分支；本层只负责把候选装配成 `SearchHit` 并标注
  `mode_used` / `routes` / `degraded` / `reason`（§5.2、§6）。
- 四态语义：失败抛 `KnowledgeSearchError`，空结果 / 被过滤 / 无查询词各给独立 `reason`（§3）。

性能：v1 两路**顺序**执行（先向量后关键词），不引入并发——向量路失败时语义模式可直接
退回关键词单路，避免无谓的 FTS 查询；端到端预算见 spec §8。检索路径只读，不写 SQLite。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from loguru import logger

from nanobot.knowledge.assemble import assemble_context
from nanobot.knowledge.embedding import Embedder, EmbeddingError, EmbeddingUnavailable
from nanobot.knowledge.fusion import (
    RouteSpec,
    SearchHit,
    SearchResult,
    dedup_partial,
    hit_sort_key,
    mmr_rerank,
    single_keyword_scores_multi,
    single_vector_scores_multi,
    weighted_rrf_multi,
)
from nanobot.knowledge.models import KnowledgeBase
from nanobot.knowledge.questions import parent_chunk_id
from nanobot.knowledge.repository import FtsHit, KnowledgeRepository, StoredChunk
from nanobot.knowledge.rerank import (
    OUTCOME_ALL_BELOW_THRESHOLD,
    OUTCOME_FALLBACK_TOP1,
    OUTCOME_MODEL_ERROR,
    OUTCOME_MODEL_UNAVAILABLE,
    OUTCOME_NO_CANDIDATES,
    OUTCOME_NO_MODEL,
    OUTCOME_THRESHOLD_DEGRADED,
    Reranker,
    RerankerUnavailableError,
    RerankSettings,
    apply_threshold,
    composite_score,
)
from nanobot.knowledge.text import build_passage, passage_char_limit
from nanobot.knowledge.tokenizer import tokenize_index, tokenize_query
from nanobot.knowledge.vector import VectorStore

_MODES = frozenset({"hybrid", "semantic", "keyword"})

#: §3.2 超取池硬上限（常量，不进配置）：`max(topK*5,50)*len(KBs)` 再截到 500。
_POOL_CAP = 500

VectorIdentity = tuple[str, str, int]


@dataclass(frozen=True, slots=True)
class VectorGroup:
    """一个向量空间的召回组（§3.1）。

    同一 embedding 身份的多个 KB 归为一组：共用一次 query embedding、一次向量查询。
    每组自带 embedder 与向量库，因此不同 KB 用不同 embedding 模型也能各自被检索。
    """

    identity: VectorIdentity
    kb_ids: tuple[str, ...]
    embedder: Embedder
    store: VectorStore


class KnowledgeSearchError(RuntimeError):
    """检索失败（参与的路由全部抛错）或作用域校验失败；**不是**空结果。"""


@dataclass(frozen=True, slots=True)
class RetrieverSettings:
    """spec §5.8 参数表；全部可配置，池硬上限不在其中。"""

    vector_weight: float = 0.7
    keyword_weight: float = 0.3
    rrf_k: int = 60
    pool_size: int = 50
    keyword_pool_size: int = 100
    default_limit: int = 8
    max_limit: int = 20
    mmr_lambda: float = 0.7
    rerank_top_k: int = 10
    parent_max_chars: int = 2400
    parent_min_chars: int = 600
    short_chunk_chars: int = 200
    dedup_containment: float = 0.85
    dedup_unique_ratio: float = 0.02
    # 兼容旧配置：WeKnora 去重不使用 Jaccard，字段保留但不再参与判定。
    dedup_jaccard: float = 0.6
    dedup_length_ratio: float = 3.0
    #: 召回阈值（0 = 不过滤）：向量按 cosine 原值，关键词按组内最高分相对计算。
    vector_threshold: float = 0.15
    keyword_threshold: float = 0.3
    #: 低召回时的本地查询扩展（只走关键词路，不调用模型）。
    expand_on_low_recall: bool = True
    expansion_limit: int = 5


def _from_fts(hit: FtsHit) -> SearchHit:
    """关键词召回结果 → 统一读模型（score 稍后由融合层覆盖）。"""
    return SearchHit(
        chunk_id=hit.chunk_id,
        doc_id=hit.doc_id,
        kb_id=hit.kb_id,
        chunk_index=hit.chunk_index,
        heading_path=hit.heading_path,
        page=hit.page,
        locator=hit.locator,
        content=hit.content,
        char_count=hit.char_count,
        content_hash=hit.content_hash,
    )


def _from_chunk(chunk: StoredChunk, score: float) -> SearchHit:
    """向量召回回表后的块 → 统一读模型；`score` 是该块的余弦相似度。"""
    return SearchHit(
        chunk_id=chunk.id,
        doc_id=chunk.doc_id,
        kb_id=chunk.kb_id,
        chunk_index=chunk.chunk_index,
        heading_path=chunk.heading_path,
        page=chunk.page,
        locator=chunk.locator,
        content=chunk.content,
        char_count=chunk.char_count,
        content_hash=chunk.content_hash,
        score=score,
    )


def _dedup_labels(labels: list[str]) -> tuple[str, ...]:
    """`degraded` 去重且保持首次出现顺序。"""
    return tuple(dict.fromkeys(labels))


# ---------- 召回阈值与本地查询扩展（WeKnora 对齐） ----------
_STOPWORDS = frozenset(
    {
        "的", "是", "在", "了", "和", "与", "或", "吗", "呢", "吧", "啊",
        "我", "你", "他", "她", "它", "我们", "你们", "他们",
        "这", "那", "这个", "那个", "一下", "请问",
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "must", "can", "to", "of", "in", "for",
        "on", "with", "at",
    }
)
_QUESTION_WORDS = ("什么", "如何", "怎么", "为什么", "哪个", "哪些", "是否", "能否")
_VARIANT_DELIMITERS = re.compile(r"[，。！？；、,.!?;:：\n\r\t]+")
_QUOTED_PHRASE = re.compile(r"[“\"']([^”\"']{2,40})[”\"']")


def _query_variants(query: str, *, mode: str, limit: int) -> list[str]:
    """本地生成查询变体（≤limit），只用于关键词路（WeKnora query_expansion）。

    规则：去停用词后的 token 串、引号短语、分隔片段、去疑问词；
    变体不重新算 embedding，避免无谓的向量调用。
    """
    base = query.strip()
    if not base or limit <= 0:
        return []
    seen = {base.lower()}
    variants: list[str] = []

    def add(text: str) -> None:
        text = " ".join(text.split())
        if len(text) < 3:
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        variants.append(text)

    tokens = [
        token
        for token in tokenize_index(base, mode=mode).split()
        if token.lower() not in _STOPWORDS
    ]
    if len(tokens) >= 2:
        add(" ".join(tokens))
    for phrase in _QUOTED_PHRASE.findall(base):
        add(phrase)
    for segment in _VARIANT_DELIMITERS.split(base):
        if len(segment.strip()) > 5:
            add(segment)
    cleaned = base
    for word in _QUESTION_WORDS:
        cleaned = cleaned.replace(word, " ")
    add(cleaned)
    return variants[:limit]


def _filter_keyword_hits(hits: list[FtsHit], threshold: float) -> list[FtsHit]:
    """关键词阈值：相对该组最高分（0 = 不过滤）。

    SQLite 的 `-bm25()` 无上界、跨 collection 不可比，绝对阈值等于不过滤；
    WeKnora 的 0.3 也是相对「该列表自身最好分」才有意义。
    """
    if threshold <= 0 or not hits:
        return hits
    peak = max(hit.score for hit in hits)
    if peak <= 0:
        return hits
    floor = peak * min(threshold, 1.0)
    return [hit for hit in hits if hit.score >= floor]


class Retriever:
    """两路召回 + 融合 + 去冗余。线程内同步执行，调用方用 `asyncio.to_thread` 包裹。"""

    def __init__(
        self,
        repository: KnowledgeRepository,
        *,
        embedder: Embedder,
        vector_store: VectorStore | None,
        settings: RetrieverSettings | None = None,
        reranker: Reranker | None = None,
        rerank_settings: RerankSettings | None = None,
        tokenizer_mode: str = "jieba",
        identity_resolver: Callable[[KnowledgeBase], tuple[Embedder, VectorStore] | None]
        | None = None,
    ) -> None:
        self._repo = repository
        self._embedder = embedder
        self._vector = vector_store
        self._settings = settings or RetrieverSettings()
        self._reranker = reranker
        self._rerank_settings = rerank_settings or RerankSettings()
        self._tokenizer_mode = tokenizer_mode
        self._identity_resolver = identity_resolver

    # ---------- 入口 ----------
    def search(
        self,
        query: str,
        *,
        workspace_ids: list[str],
        kb_ids: list[str] | None = None,
        mode: str = "hybrid",
        limit: int | None = None,
        extra_context: Sequence[SearchHit] = (),
    ) -> SearchResult:
        """执行一次检索。

        `mode`：`hybrid`（默认）/ `semantic` / `keyword`；`kb_ids` 为显式作用域，
        任一 id 不可见（不存在、已停用或不在工作区）即抛 `KnowledgeSearchError`。
        `extra_context` 是调用方注入的补充上下文（§9.1 ③ 历史引用），只读、不参与排序。
        """
        normalized_mode = mode.strip().lower()
        if normalized_mode not in _MODES:
            raise ValueError(f"unsupported knowledge search mode: {mode!r}")
        settings = self._settings
        effective_limit = self._clamp_limit(limit)

        visible = self._visible_kbs(workspace_ids, kb_ids)
        if not visible:
            # 作用域为空：没有任何可见库，谈不上「搜不到」（spec §3 四态）。
            return SearchResult(hits=(), mode_used="keyword", reason="no_scope")
        # §3.2 超取池按**组内**可见库数量放大：`max(topK*5,50)*len(KBs)`，上限 500。

        degraded: list[str] = []
        match_expr = tokenize_query(query, mode=self._tokenizer_mode)
        if not match_expr:
            degraded.append("empty_query_tokens")

        vector_error: Exception | None = None
        vector_hits: list[SearchHit] = []
        vector_lists: list[list[tuple[str, float]]] = []
        vector_raw_count = 0
        vector_ok = False
        groups: list[VectorGroup] = []
        if normalized_mode in ("hybrid", "semantic"):
            groups = self._vector_groups(visible, degraded)
        if groups:
            try:
                vector_hits, vector_lists, vector_raw_count = self._run_vector_route(
                    query, groups, limit=effective_limit
                )
            except EmbeddingUnavailable as exc:
                vector_error = exc
                degraded.append("vector_unavailable")
                logger.warning("knowledge vector route unavailable: {}", exc)
            except EmbeddingError as exc:
                vector_error = exc
                degraded.append(f"embedding_error:{type(exc).__name__}")
                logger.warning("knowledge embedding failed: {}", exc)
            except Exception as exc:  # noqa: BLE001 - 向量是派生层，任何失败都只降级
                vector_error = exc
                degraded.append("vector_unavailable")
                logger.warning("knowledge vector route failed: {}", exc)
            else:
                vector_ok = True

        # 语义模式在向量路不可用时退回关键词单路（§6）。
        want_keyword = normalized_mode != "semantic" or not vector_ok
        keyword_hits: list[FtsHit] = []
        keyword_lists: list[list[tuple[str, float]]] = []
        keyword_groups: list[tuple[str, ...]] = []
        keyword_error: Exception | None = None
        keyword_ok = False
        if want_keyword and match_expr:
            # §3.1：按 embedding 空间分组做检索；向量不可用的库与无向量的库各自成组。
            # 每个可见库必须恰好落进一个关键词组——否则「向量索引过期」的库会
            # 连关键词路也搜不到（它只是不能走向量，不该从检索里消失）。
            covered: set[str] = set()
            for group in groups:
                keyword_groups.append(group.kb_ids)
                covered.update(group.kb_ids)
            leftover = tuple(kb.id for kb in visible if kb.id not in covered)
            if leftover:
                keyword_groups.append(leftover)
            for group_kb_ids in keyword_groups:
                if not group_kb_ids:
                    continue
                pool = self._keyword_pool(effective_limit, len(group_kb_ids))
                try:
                    group_hits = self._repo.search_fts(
                        match_expr,
                        workspace_ids=list(workspace_ids),
                        limit=pool,
                        kb_ids=list(group_kb_ids),
                    )
                except Exception as exc:  # noqa: BLE001 - 关键词路的失败由下方统一裁决
                    keyword_error = exc
                    logger.warning("knowledge keyword route failed: {}", exc)
                    if normalized_mode == "keyword":
                        # 没有第二条路可退：失败必须抛，不能返回空结果冒充「没搜到」（§6）。
                        raise KnowledgeSearchError(
                            "knowledge keyword retrieval failed"
                        ) from exc
                    break
                question_hits = self._question_keyword_hits(
                    match_expr,
                    workspace_ids=list(workspace_ids),
                    kb_ids=list(group_kb_ids),
                    limit=pool,
                )
                merged = self._merge_group_hits(
                    group_hits,
                    question_hits,
                    limit=pool,
                    threshold=settings.keyword_threshold,
                )
                keyword_hits.extend(merged)
                keyword_lists.append(
                    [(hit.chunk_id, hit.score) for hit in merged]
                )
            else:
                keyword_ok = True

        # 召回不足时用本地变体补关键词路（WeKnora query_expansion）：不重建 embedding。
        if (
            want_keyword
            and match_expr
            and keyword_error is None
            and settings.expand_on_low_recall
        ):
            known = {hit.chunk_id for hit in keyword_hits}
            known.update(hit.chunk_id for hit in vector_hits)
            if len(known) < max(1, settings.pool_size):
                expanded = self._expansion_hits(
                    query,
                    keyword_groups,
                    workspace_ids=list(workspace_ids),
                    limit=effective_limit,
                )
                if expanded:
                    keyword_hits.extend(expanded)
                    keyword_lists.append(
                        [(hit.chunk_id, hit.score) for hit in expanded]
                    )
                    keyword_ok = True

        # 失败 ≠ 空结果：参与的路由全部失败才抛（§3、§6）。
        if keyword_error is not None and not vector_ok:
            raise KnowledgeSearchError("all attempted knowledge routes failed") from keyword_error
        if vector_error is not None and not keyword_ok:
            raise KnowledgeSearchError("all attempted knowledge routes failed") from vector_error

        if keyword_error is not None:
            degraded.append("keyword_error")
        if normalized_mode == "semantic" and not vector_ok:
            degraded.append("mode_downgraded")

        if not keyword_hits and not vector_hits:
            reason = self._empty_reason(match_expr, vector_raw_count)
            return SearchResult(
                hits=(),
                mode_used="keyword",
                degraded=_dedup_labels(degraded),
                reason=reason,
            )

        scores, routes, mode_used = self._fuse(vector_lists, keyword_lists)
        candidates = self._assemble(scores, vector_hits, keyword_hits)
        candidates = self._with_titles(candidates)
        deduped = dedup_partial(
            candidates,
            length_ratio=settings.dedup_length_ratio,
            containment=settings.dedup_containment,
            unique_ratio=settings.dedup_unique_ratio,
        )
        (
            reranked,
            rerank_labels,
            rerank_outcome,
            candidates_in,
            candidates_kept,
        ) = self._apply_rerank(query, deduped, explicit_scope=kb_ids is not None)
        degraded.extend(rerank_labels)
        # §9.2 FILTER_TOP_K：先进 MMR 的池截到 rerank_top_k，再由 MMR 去冗余。
        top_pool = reranked[: max(1, settings.rerank_top_k)]
        ranked = mmr_rerank(
            top_pool,
            limit=min(effective_limit, max(1, settings.rerank_top_k)),
            lambda_=settings.mmr_lambda,
        )
        # §9.1 CHUNK_MERGE：把「一条 chunk」变成「一段读得通的上下文」。
        assembled = self._assemble_context(ranked, extra_context=list(extra_context))
        if rerank_outcome != OUTCOME_NO_MODEL:
            # §7 RecordRetrievalCut：M 必须是最终返回条数（top_k / MMR / 装配之后），
            # 否则阈值全部通过时模型永远看不到「结果只是候选子集」。
            candidates_kept = len(assembled)
        return SearchResult(
            hits=tuple(assembled),
            mode_used=mode_used,
            degraded=_dedup_labels(degraded),
            reason=None,
            routes=routes,
            rerank=rerank_outcome,
            candidates_in=candidates_in,
            candidates_kept=candidates_kept,
        )

    # ---------- 精排（WeKnora §7） ----------
    def _apply_rerank(
        self,
        query: str,
        candidates: list[SearchHit],
        *,
        explicit_scope: bool,
    ) -> tuple[list[SearchHit], list[str], str, int, int]:
        """精排是增强项：任何失败都退回召回顺序，不抛错、不改变可用性。

        返回 `(命中, 降级标签, outcome, 候选总数, 过阈值条数)`；检索层在
        top_k / MMR / 装配之后会把「过阈值条数」覆盖成最终返回条数，
        这样 §7 RecordRetrievalCut 的「N 进 M 出」才反映真实截断。
        """
        if self._reranker is None:
            return candidates, [], OUTCOME_NO_MODEL, 0, 0
        if not candidates:
            return candidates, [], OUTCOME_NO_CANDIDATES, 0, 0
        settings = self._rerank_settings
        candidates_in = len(candidates)
        pool = candidates[: max(1, settings.max_candidates)]
        titles = self._repo.list_document_titles({hit.doc_id for hit in pool})
        limit = passage_char_limit(
            query,
            max_passage_chars=settings.max_passage_chars,
            max_request_chars=settings.max_request_chars,
        )
        passages: list[str] = []
        indexed: list[tuple[int, SearchHit]] = []
        for hit in pool:
            passage = self._passage(hit, titles.get(hit.doc_id, ""), limit)
            if not passage.strip():
                continue  # §7.3 空 passage 跳过，不参与打分
            indexed.append((len(passages), hit))
            passages.append(passage)
        if not passages:
            return candidates, [], OUTCOME_NO_CANDIDATES, candidates_in, candidates_in
        try:
            results = self._reranker.rerank(query, passages)
        except RerankerUnavailableError as exc:
            logger.warning("knowledge rerank model unavailable: {}", exc)
            return (
                candidates,
                ["rerank_model_unavailable"],
                OUTCOME_MODEL_UNAVAILABLE,
                candidates_in,
                len(pool),
            )
        except Exception as exc:  # noqa: BLE001 - 精排失败只降级
            logger.warning("knowledge rerank failed: {!r}", exc)
            return candidates, ["rerank_model_error"], OUTCOME_MODEL_ERROR, candidates_in, len(pool)
        scores = {
            result.index: result.relevance_score
            for result in results
            if 0 <= result.index < len(passages)
        }
        if not scores:
            return candidates, ["rerank_model_error"], OUTCOME_MODEL_ERROR, candidates_in, len(pool)
        kept, outcome = apply_threshold(
            scores,
            threshold=settings.threshold,
            fallback_min_score=settings.fallback_min_score,
            explicit_scope=explicit_scope,
        )
        if not kept:
            return (
                [],
                ["rerank_all_below_threshold"],
                OUTCOME_ALL_BELOW_THRESHOLD,
                candidates_in,
                0,
            )
        labels: list[str] = []
        if outcome == OUTCOME_THRESHOLD_DEGRADED:
            labels.append("rerank_threshold_degraded")
        elif outcome == OUTCOME_FALLBACK_TOP1:
            labels.append("rerank_fallback_top1")
        scored: list[SearchHit] = []
        for passage_index in kept:
            _position, hit = indexed[passage_index]
            composite = composite_score(
                scores[passage_index],
                hit.score,
                source_weight_value=1.0,
                model_weight=settings.model_weight,
                base_weight=settings.base_weight,
                source_weight=settings.source_weight,
            )
            scored.append(replace(hit, score=composite))
        scored.sort(key=hit_sort_key)
        return scored, labels, outcome, len(passages), len(scored)

    def _passage(self, hit: SearchHit, title: str, limit: int) -> str:
        """passage = 标题 + 面包屑 + 清洗正文；超长从尾部截断（§7.3）。"""
        return build_passage(
            title=title,
            heading_path=hit.heading_path,
            content=hit.content,
            limit=limit,
        )

    # ---------- 装配（WeKnora §9.1） ----------
    def _with_titles(self, hits: list[SearchHit]) -> list[SearchHit]:
        """给命中块补文档标题：精排 passage 与 MMR token 都要用（§7.3 / §8.2）。"""
        titles = self._repo.list_document_titles({hit.doc_id for hit in hits})
        if not titles:
            return hits
        return [
            replace(hit, title=titles.get(hit.doc_id, hit.title)) for hit in hits
        ]

    def _assemble_context(
        self, hits: list[SearchHit], *, extra_context: list[SearchHit] | None = None
    ) -> list[SearchHit]:
        """上下文装配：父块回填 + 连续正文合并 + 短块邻居扩展 + 最终去重（§9.1）。"""
        if not hits and not extra_context:
            return []
        chunks: dict[str, StoredChunk] = {}
        for hit in hits:
            chunk = self._repo.fetch_chunk(hit.chunk_id)
            if chunk is not None:
                chunks[hit.chunk_id] = chunk
        titles = self._repo.list_document_titles({hit.doc_id for hit in hits})

        def load_neighbors(chunk: StoredChunk, before: int, after: int) -> list[StoredChunk]:
            return self._repo.fetch_neighbors(chunk, before=before, after=after)

        def load_title(doc_id: str) -> str:
            return titles.get(doc_id, "")

        return assemble_context(
            hits,
            chunks=chunks,
            load_neighbors=load_neighbors,
            load_title=load_title,
            parent_max_chars=self._settings.parent_max_chars,
            parent_min_chars=self._settings.parent_min_chars,
            short_chunk_chars=self._settings.short_chunk_chars,
            extra_blocks=tuple(extra_context or ()),
        )

    # ---------- 作用域 ----------
    def _visible_kbs(
        self, workspace_ids: list[str], kb_ids: list[str] | None
    ) -> list[KnowledgeBase]:
        """可见 = 工作区/全局可见 **且** `is_enabled`（`list_kbs` 不筛启用状态）。"""
        visible = [
            kb
            for kb in self._repo.list_kbs(workspace_ids=list(workspace_ids))
            if kb.is_enabled
        ]
        if kb_ids is None:
            return visible
        # 显式 kb 必须是可见子集：越权/已删除/已停用一律报错，不静默降级（§3 ①）。
        visible_by_id = {kb.id: kb for kb in visible}
        missing = [kb_id for kb_id in kb_ids if kb_id not in visible_by_id]
        if missing:
            raise KnowledgeSearchError(
                f"knowledge base not in scope: {', '.join(missing)}"
            )
        wanted = set(kb_ids)
        return [kb for kb in visible if kb.id in wanted]

    # ---------- 向量作用域（按 embedding 空间分组） ----------
    def _vector_groups(
        self, visible: list[KnowledgeBase], degraded: list[str]
    ) -> list[VectorGroup]:
        """按 KB 自己的 embedding 身份分组（§3.1）。

        同一向量空间的多个 KB 只算一次 query embedding；不同空间的 KB 各查各的
        collection。每组自带 embedder 与向量库，因此「每个库配自己的 embedding 模型」
        不再需要 reindex 才能被检索到。
        """
        default_identity: VectorIdentity | None = None
        if self._identity_resolver is None:
            try:
                default_identity = self._embedder.identity
            except EmbeddingUnavailable as exc:
                degraded.append("vector_unavailable")
                logger.warning("knowledge embedding backend unavailable: {}", exc)
                return []
            except EmbeddingError as exc:
                degraded.append(f"embedding_error:{type(exc).__name__}")
                logger.warning("knowledge embedding identity failed: {}", exc)
                return []
            except Exception as exc:  # noqa: BLE001
                degraded.append("vector_unavailable")
                logger.warning("knowledge embedding identity failed: {}", exc)
                return []
            if default_identity[0] == "none":
                degraded.append("vector_unavailable")
                return []
            if self._vector is None or not self._vector.available:
                degraded.append("vector_unavailable")
                return []

        groups: dict[VectorIdentity, VectorGroup] = {}
        for kb in visible:
            if kb.embedding_backend == "none":
                # 该库本就是关键词模式，属配置事实而非索引漂移（§4.2 第 1 条）。
                degraded.append(f"vector_disabled:{kb.id}")
                continue
            kb_identity = (
                kb.embedding_backend,
                kb.embedding_model,
                kb.embedding_dimensions,
            )
            if self._identity_resolver is not None:
                try:
                    resolved = self._identity_resolver(kb)
                except Exception as exc:  # noqa: BLE001 - 单库装配失败只影响该库
                    logger.warning(
                        "knowledge embedding resolve failed: kb={} error={}",
                        kb.id,
                        exc,
                    )
                    resolved = None
                if resolved is None:
                    degraded.append(f"embedding_unavailable:{kb.id}")
                    continue
                embedder, store = resolved
                if not store.available:
                    degraded.append(f"vector_unavailable:{kb.id}")
                    continue
                identity = store.identity
            else:
                if default_identity is None or kb_identity != default_identity:
                    # 索引过期：需 reindex；其余一致的 KB 仍走向量路（§4.2 第 3 条）。
                    degraded.append(f"embedding_mismatch:{kb.id}")
                    continue
                assert self._vector is not None
                embedder, store = self._embedder, self._vector
                identity = default_identity
            group = groups.get(identity)
            kb_ids = (group.kb_ids if group else ()) + (kb.id,)
            groups[identity] = VectorGroup(identity, kb_ids, embedder, store)
        return list(groups.values())

    def _question_keyword_hits(
        self,
        match_expr: str,
        *,
        workspace_ids: list[str],
        kb_ids: list[str],
        limit: int,
    ) -> list[FtsHit]:
        """问题索引的关键词召回；问题索引是增强项，失败只丢这一路。"""
        try:
            return self._repo.search_questions_fts(
                match_expr,
                workspace_ids=workspace_ids,
                limit=limit,
                kb_ids=kb_ids,
            )
        except Exception as exc:  # noqa: BLE001 - 问题索引坏了不该让检索整体失败
            logger.warning("knowledge question keyword route failed: {}", exc)
            return []

    def _expansion_hits(
        self,
        query: str,
        groups: list[tuple[str, ...]],
        *,
        workspace_ids: list[str],
        limit: int,
    ) -> list[FtsHit]:
        """低召回时的关键词扩展：本地变体 × 每个 KB 组，阈值按 0.8 放宽。"""
        variants = _query_variants(
            query, mode=self._tokenizer_mode, limit=self._settings.expansion_limit
        )
        if not variants:
            return []
        pool = max(limit * 2, self._settings.rerank_top_k * 2)
        threshold = self._settings.keyword_threshold * 0.8
        best: dict[str, FtsHit] = {}
        for variant in variants:
            expr = tokenize_query(variant, mode=self._tokenizer_mode)
            if not expr:
                continue
            for group_kb_ids in groups:
                if not group_kb_ids:
                    continue
                try:
                    group_hits = self._repo.search_fts(
                        expr,
                        workspace_ids=workspace_ids,
                        limit=pool,
                        kb_ids=list(group_kb_ids),
                    )
                except Exception as exc:  # noqa: BLE001 - 扩展是增强项，失败只丢变体
                    logger.warning("knowledge expansion keyword route failed: {}", exc)
                    continue
                question_hits = self._question_keyword_hits(
                    expr,
                    workspace_ids=workspace_ids,
                    kb_ids=list(group_kb_ids),
                    limit=pool,
                )
                merged = self._merge_group_hits(
                    group_hits, question_hits, limit=pool, threshold=threshold
                )
                for hit in merged:
                    current = best.get(hit.chunk_id)
                    if current is None or hit.score > current.score:
                        best[hit.chunk_id] = hit
        ranked = sorted(best.values(), key=lambda hit: (-hit.score, hit.chunk_id))
        return ranked[:pool]

    @staticmethod
    def _merge_group_hits(
        chunk_hits: list[FtsHit],
        question_hits: list[FtsHit],
        *,
        limit: int,
        threshold: float = 0.0,
    ) -> list[FtsHit]:
        """块命中与问题命中都映射到父块：同一块取最高分，再按阈值与分数截断。"""
        best: dict[str, FtsHit] = {}
        for hit in (*chunk_hits, *question_hits):
            current = best.get(hit.chunk_id)
            if current is None or hit.score > current.score:
                best[hit.chunk_id] = hit
        ranked = sorted(best.values(), key=lambda hit: (-hit.score, hit.chunk_id))
        return _filter_keyword_hits(ranked, threshold)[:limit]

    def _run_vector_route(
        self,
        query: str,
        groups: list[VectorGroup],
        *,
        limit: int,
    ) -> tuple[list[SearchHit], list[list[tuple[str, float]]], int]:
        """每组一次 query embedding + 一次向量查询；命中回表过滤孤儿/停用块（§4.2）。

        返回 `(hits, 每组的 (chunk_id, cosine) 列表, raw_count)`；每组一张列表是
        bestRanks 的输入，`raw_count` 用于区分「本来就没有候选」与「候选全被过滤」。
        """
        hits: list[SearchHit] = []
        lists: list[list[tuple[str, float]]] = []
        raw_count = 0
        failures = 0
        last_error: Exception | None = None
        threshold = max(0.0, self._settings.vector_threshold)
        for group in groups:
            # 一个库的 embedding/向量库坏了，不该拖垮其他组的向量召回。
            try:
                query_vector = group.embedder.embed_query(query)
                raw_hits = group.store.query(
                    query_vector,
                    kb_ids=list(group.kb_ids),
                    limit=self._vector_pool(limit, len(group.kb_ids)),
                )
            except Exception as exc:  # noqa: BLE001 - 组级失败统一裁决
                failures += 1
                last_error = exc
                logger.warning(
                    "knowledge vector group failed: kbs={} error={}",
                    ",".join(group.kb_ids),
                    exc,
                )
                continue
            raw_count += len(raw_hits)
            # 问题行（`<chunk>-qN`）映射回父块：模型要看到答案，不是看到问题。
            group_chunks: dict[str, StoredChunk] = {}
            group_best: dict[str, float] = {}
            for source_id, cosine in raw_hits:
                # 阈值裁在回表之前：弱相关块不必再查 SQLite（raw_count 仍记裁前数量）。
                if threshold > 0 and cosine < threshold:
                    continue
                parent = parent_chunk_id(source_id)
                stored = self._repo.fetch_chunk(parent or source_id)
                if stored is None or not stored.is_enabled:
                    continue
                current = group_best.get(stored.id)
                if current is None or cosine > current:
                    group_best[stored.id] = cosine
                    group_chunks[stored.id] = stored
            group_scores: list[tuple[str, float]] = []
            for chunk_id, cosine in sorted(
                group_best.items(), key=lambda item: (-item[1], item[0])
            ):
                hits.append(_from_chunk(group_chunks[chunk_id], cosine))
                group_scores.append((chunk_id, cosine))
            if group_scores:
                # 空列表不进融合：一张全空的「向量路」会让 mode_used 误报 hybrid。
                lists.append(group_scores)
        if groups and failures == len(groups) and last_error is not None:
            raise last_error
        return hits, lists, raw_count

    # ---------- 融合 ----------
    def _fuse(
        self,
        vector_lists: list[list[tuple[str, float]]],
        keyword_lists: list[list[tuple[str, float]]],
    ) -> tuple[dict[str, float], tuple[str, ...], str]:
        """§5.2 三条互斥归一化路径；只返回融合分与路由此刻的形态。"""
        settings = self._settings
        if vector_lists and keyword_lists:
            scores = weighted_rrf_multi(
                (
                    RouteSpec(
                        settings.vector_weight,
                        tuple(tuple(cid for cid, _ in ids) for ids in vector_lists),
                    ),
                    RouteSpec(
                        settings.keyword_weight,
                        tuple(tuple(cid for cid, _ in ids) for ids in keyword_lists),
                    ),
                ),
                k=settings.rrf_k,
            )
            return scores, ("vector", "keyword"), "hybrid"
        if vector_lists:
            scores = single_vector_scores_multi(vector_lists)
            return scores, ("vector",), "semantic"
        scores = single_keyword_scores_multi(keyword_lists)
        return scores, ("keyword",), "keyword"

    @staticmethod
    def _assemble(
        scores: dict[str, float],
        vector_hits: list[SearchHit],
        keyword_hits: list[FtsHit],
    ) -> list[SearchHit]:
        """每个候选一条 `SearchHit`：同一块两路命中时优先用关键词读模型（字段等价）。"""
        by_id: dict[str, SearchHit] = {hit.chunk_id: hit for hit in vector_hits}
        for keyword_hit in keyword_hits:
            by_id[keyword_hit.chunk_id] = _from_fts(keyword_hit)
        return [
            replace(by_id[chunk_id], score=score) for chunk_id, score in scores.items()
        ]

    # ---------- 空结果 ----------
    @staticmethod
    def _empty_reason(match_expr: str, vector_raw_count: int) -> str:
        """四态语义里的三种「非失败」空因（§3）。"""
        if not match_expr:
            return "empty_query_tokens"
        if vector_raw_count > 0:
            # 有候选但回表时全部被停用/删除过滤掉。
            return "filtered"
        return "no_candidates"

    # ---------- 池与 limit ----------
    def _clamp_limit(self, limit: int | None) -> int:
        settings = self._settings
        raw = settings.default_limit if limit is None else limit
        return max(1, min(raw, settings.max_limit))

    def _keyword_pool(self, limit: int, kb_count: int) -> int:
        """§3.2 超取池：`(max(topK*5, 关键词下限) * len(KBs))`，上限 500。"""
        size = max(limit * 5, self._settings.keyword_pool_size) * max(1, kb_count)
        return min(size, _POOL_CAP)

    def _vector_pool(self, limit: int, kb_count: int) -> int:
        """§3.2 超取池：`(max(topK*5, 向量下限) * len(KBs))`，上限 500。"""
        size = max(limit * 5, self._settings.pool_size) * max(1, kb_count)
        return min(size, _POOL_CAP)
