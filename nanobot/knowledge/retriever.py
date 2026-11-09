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

from dataclasses import dataclass, replace

from loguru import logger

from nanobot.knowledge.embedding import Embedder, EmbeddingError, EmbeddingUnavailable
from nanobot.knowledge.fusion import (
    SearchHit,
    SearchResult,
    dedup_partial,
    mmr_rerank,
    single_keyword_scores,
    single_vector_scores,
    weighted_rrf,
)
from nanobot.knowledge.models import KnowledgeBase
from nanobot.knowledge.repository import FtsHit, KnowledgeRepository, StoredChunk
from nanobot.knowledge.tokenizer import tokenize_query
from nanobot.knowledge.vector import VectorStore

_MODES = frozenset({"hybrid", "semantic", "keyword"})

#: §4.3 池硬上限（常量，不进配置）。
_KEYWORD_POOL_CAP = 300
_VECTOR_POOL_CAP = 200

VectorIdentity = tuple[str, str, int]


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
    relevance_band: float = 0.15
    adjacent_similarity_floor: float = 0.25
    dedup_containment: float = 0.98
    dedup_unique_ratio: float = 0.02
    dedup_jaccard: float = 0.6
    dedup_length_ratio: float = 5.0


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


class Retriever:
    """两路召回 + 融合 + 去冗余。线程内同步执行，调用方用 `asyncio.to_thread` 包裹。"""

    def __init__(
        self,
        repository: KnowledgeRepository,
        *,
        embedder: Embedder,
        vector_store: VectorStore | None,
        settings: RetrieverSettings | None = None,
    ) -> None:
        self._repo = repository
        self._embedder = embedder
        self._vector = vector_store
        self._settings = settings or RetrieverSettings()

    # ---------- 入口 ----------
    def search(
        self,
        query: str,
        *,
        workspace_ids: list[str],
        kb_ids: list[str] | None = None,
        mode: str = "hybrid",
        limit: int | None = None,
    ) -> SearchResult:
        """执行一次检索。

        `mode`：`hybrid`（默认）/ `semantic` / `keyword`；`kb_ids` 为显式作用域，
        任一 id 不可见（不存在、已停用或不在工作区）即抛 `KnowledgeSearchError`。
        """
        normalized_mode = mode.strip().lower()
        if normalized_mode not in _MODES:
            raise ValueError(f"unsupported knowledge search mode: {mode!r}")
        settings = self._settings
        effective_limit = self._clamp_limit(limit)
        keyword_pool = self._keyword_pool(effective_limit)
        vector_pool = self._vector_pool(effective_limit)

        visible = self._visible_kbs(workspace_ids, kb_ids)
        if not visible:
            # 作用域为空：没有任何可见库，谈不上「搜不到」（spec §3 四态）。
            return SearchResult(hits=(), mode_used="keyword", reason="no_scope")
        visible_ids = [kb.id for kb in visible]

        degraded: list[str] = []
        match_expr = tokenize_query(query)
        if not match_expr:
            degraded.append("empty_query_tokens")

        identity: VectorIdentity | None = None
        vector_kb_ids: list[str] = []
        vector_error: Exception | None = None
        if normalized_mode in ("hybrid", "semantic"):
            identity, vector_kb_ids, vector_error = self._vector_scope(visible, degraded)

        vector_hits: list[SearchHit] = []
        vector_raw_count = 0
        vector_ok = False
        groups = self._identity_groups(identity, vector_kb_ids)
        if groups:
            try:
                vector_hits, vector_raw_count = self._run_vector_route(
                    query, groups, limit=vector_pool
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
        keyword_error: Exception | None = None
        keyword_ok = False
        if want_keyword and match_expr:
            try:
                keyword_hits = self._repo.search_fts(
                    match_expr,
                    workspace_ids=list(workspace_ids),
                    limit=keyword_pool,
                    kb_ids=visible_ids,
                )
            except Exception as exc:  # noqa: BLE001 - 关键词路的失败由下方统一裁决
                keyword_error = exc
                logger.warning("knowledge keyword route failed: {}", exc)
                if normalized_mode == "keyword":
                    # 没有第二条路可退：失败必须抛，不能返回空结果冒充「没搜到」（§6）。
                    raise KnowledgeSearchError(
                        "knowledge keyword retrieval failed"
                    ) from exc
            else:
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

        scores, routes, mode_used = self._fuse(vector_hits, keyword_hits)
        candidates = self._assemble(scores, vector_hits, keyword_hits)
        deduped = dedup_partial(
            candidates,
            length_ratio=settings.dedup_length_ratio,
            jaccard=settings.dedup_jaccard,
            containment=settings.dedup_containment,
            unique_ratio=settings.dedup_unique_ratio,
        )
        ranked = mmr_rerank(
            deduped,
            limit=effective_limit,
            lambda_=settings.mmr_lambda,
            relevance_band=settings.relevance_band,
            adjacent_similarity_floor=settings.adjacent_similarity_floor,
        )
        return SearchResult(
            hits=tuple(ranked),
            mode_used=mode_used,
            degraded=_dedup_labels(degraded),
            reason=None,
            routes=routes,
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

    # ---------- 向量作用域 ----------
    def _vector_scope(
        self, visible: list[KnowledgeBase], degraded: list[str]
    ) -> tuple[VectorIdentity | None, list[str], Exception | None]:
        """解析当前 embedder 身份并把可见 KB 分成「可向量」与「降级」两组（§4.2）。

        返回 `(identity, vector_kb_ids, error)`；`error` 非空表示身份解析阶段已经失败。
        """
        try:
            identity = self._embedder.identity
        except EmbeddingUnavailable as exc:
            degraded.append("vector_unavailable")
            logger.warning("knowledge embedding backend unavailable: {}", exc)
            return None, [], None
        except EmbeddingError as exc:
            degraded.append(f"embedding_error:{type(exc).__name__}")
            logger.warning("knowledge embedding identity failed: {}", exc)
            return None, [], exc
        except Exception as exc:  # noqa: BLE001
            degraded.append("vector_unavailable")
            logger.warning("knowledge embedding identity failed: {}", exc)
            return None, [], exc
        if identity[0] == "none":
            degraded.append("vector_unavailable")
            return None, [], None
        if self._vector is None or not self._vector.available:
            degraded.append("vector_unavailable")
            return None, [], None
        return identity, self._group_kbs(visible, identity, degraded), None

    @staticmethod
    def _group_kbs(
        visible: list[KnowledgeBase], identity: VectorIdentity, degraded: list[str]
    ) -> list[str]:
        """按 KB 的 embedding 指纹筛出可向量子集；两类排除原因必须区分（R13）。"""
        kb_ids: list[str] = []
        for kb in visible:
            if kb.embedding_backend == "none":
                # 该库本就是关键词模式，属配置事实而非索引漂移（§4.2 第 1 条）。
                degraded.append(f"vector_disabled:{kb.id}")
                continue
            kb_identity = (kb.embedding_backend, kb.embedding_model, kb.embedding_dimensions)
            if kb_identity != identity:
                # 索引过期：需 reindex；其余一致的 KB 仍走向量路（§4.2 第 3 条）。
                degraded.append(f"embedding_mismatch:{kb.id}")
                continue
            kb_ids.append(kb.id)
        return kb_ids

    @staticmethod
    def _identity_groups(
        identity: VectorIdentity | None, vector_kb_ids: list[str]
    ) -> list[tuple[VectorIdentity, list[str]]]:
        """v1 只有一个活动 embedder → 单组；保留分组循环以承接 v2 的每库多模型。"""
        if identity is None or not vector_kb_ids:
            return []
        return [(identity, vector_kb_ids)]

    def _run_vector_route(
        self,
        query: str,
        groups: list[tuple[VectorIdentity, list[str]]],
        *,
        limit: int,
    ) -> tuple[list[SearchHit], int]:
        """每组一次 query embedding + 一次向量查询；命中回表过滤孤儿/停用块（§4.2）。

        返回 `(hits, raw_count)`；`raw_count` 用于区分「本来就没有候选」与「候选全被过滤」。
        """
        hits: list[SearchHit] = []
        raw_count = 0
        for _group_identity, group_kb_ids in groups:
            query_vector = self._embedder.embed_query(query)
            assert self._vector is not None
            raw_hits = self._vector.query(
                query_vector, kb_ids=list(group_kb_ids), limit=limit
            )
            raw_count += len(raw_hits)
            for chunk_id, cosine in raw_hits:
                stored = self._repo.fetch_chunk(chunk_id)
                if stored is None or not stored.is_enabled:
                    continue
                hits.append(_from_chunk(stored, cosine))
        return hits, raw_count

    # ---------- 融合 ----------
    def _fuse(
        self, vector_hits: list[SearchHit], keyword_hits: list[FtsHit]
    ) -> tuple[dict[str, float], tuple[str, ...], str]:
        """§5.2 三条互斥归一化路径；只返回融合分与路由此刻的形态。"""
        settings = self._settings
        if vector_hits and keyword_hits:
            scores = weighted_rrf(
                [hit.chunk_id for hit in vector_hits],
                [hit.chunk_id for hit in keyword_hits],
                w_v=settings.vector_weight,
                w_k=settings.keyword_weight,
                k=settings.rrf_k,
            )
            return scores, ("vector", "keyword"), "hybrid"
        if vector_hits:
            scores = single_vector_scores([(hit.chunk_id, hit.score) for hit in vector_hits])
            return scores, ("vector",), "semantic"
        scores = single_keyword_scores(keyword_hits)
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

    def _keyword_pool(self, limit: int) -> int:
        size = max(limit * 10, self._settings.keyword_pool_size)
        return min(size, _KEYWORD_POOL_CAP)

    def _vector_pool(self, limit: int) -> int:
        size = max(limit * 5, self._settings.pool_size)
        return min(size, _VECTOR_POOL_CAP)
