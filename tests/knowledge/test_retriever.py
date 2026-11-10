"""检索层测试（spec §9.4 + §3 四态 / §4.2 identity 分组 / §4.3 双池 / §6 降级矩阵）。

全部使用内存假件（repo / embedder / vector store），不碰 SQLite / Chroma / 网络。
夹具正文用空格分隔的 ASCII 词构造，token 集合可控，避免去重误合并。
"""

from __future__ import annotations

from typing import Any

import pytest

from nanobot.knowledge.embedding import EmbeddingError, EmbeddingUnavailable
from nanobot.knowledge.fusion import SearchResult
from nanobot.knowledge.models import KnowledgeBase
from nanobot.knowledge.repository import FtsHit, StoredChunk
from nanobot.knowledge.rerank import RankResult
from nanobot.knowledge.retriever import (
    KnowledgeSearchError,
    Retriever,
    RetrieverSettings,
    _query_variants,
)

_UNSET = object()


def _kb(
    kb_id: str = "kb_1",
    *,
    backend: str = "local",
    model: str = "m",
    dims: int = 2,
    enabled: bool = True,
    visibility: str = "workspace",
    workspace_id: str = "ws1",
) -> KnowledgeBase:
    return KnowledgeBase(
        id=kb_id,
        name=kb_id,
        description="",
        workspace_id=workspace_id,
        visibility=visibility,
        chunk_size=800,
        chunk_overlap=120,
        embedding_backend=backend,
        embedding_model=model,
        embedding_dimensions=dims,
        is_enabled=enabled,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _fts(
    chunk_id: str,
    content: str,
    score: float,
    *,
    kb_id: str = "kb_1",
    doc_id: str = "doc_1",
    chunk_index: int = 0,
    content_hash: str = "",
) -> FtsHit:
    return FtsHit(
        chunk_id=chunk_id,
        doc_id=doc_id,
        kb_id=kb_id,
        chunk_index=chunk_index,
        heading_path="",
        page=None,
        locator="",
        content=content,
        char_count=len(content),
        content_hash=content_hash,
        score=score,
    )


def _stored(
    chunk_id: str,
    content: str,
    *,
    kb_id: str = "kb_1",
    doc_id: str = "doc_1",
    chunk_index: int = 0,
    content_hash: str = "",
    is_enabled: bool = True,
) -> StoredChunk:
    return StoredChunk(
        row_id=1,
        id=chunk_id,
        doc_id=doc_id,
        kb_id=kb_id,
        workspace_id="ws1",
        chunk_index=chunk_index,
        content=content,
        heading_path="",
        page=None,
        locator="",
        start_offset=0,
        end_offset=len(content),
        char_count=len(content),
        content_hash=content_hash,
        is_enabled=is_enabled,
    )


class FakeRepo:
    def __init__(
        self,
        *,
        kbs: list[KnowledgeBase] | None = None,
        fts: list[FtsHit] | None = None,
        chunks: dict[str, StoredChunk] | None = None,
        fts_error: Exception | None = None,
        question_fts: list[FtsHit] | None = None,
        question_fts_error: Exception | None = None,
        fts_handler: Any = None,
    ) -> None:
        self.kbs = list(kbs or [])
        self.fts_hits = list(fts or [])
        self.question_hits = list(question_fts or [])
        self.fts_handler = fts_handler
        self.chunks = dict(chunks or {})
        self.fts_error = fts_error
        self.question_fts_error = question_fts_error
        self.list_calls: list[list[str]] = []
        self.fts_calls: list[dict[str, Any]] = []
        self.question_fts_calls: list[dict[str, Any]] = []
        self.fetch_calls: list[str] = []

    def list_kbs(
        self, *, workspace_ids: list[str], include_global: bool = True
    ) -> list[KnowledgeBase]:
        self.list_calls.append(list(workspace_ids))
        return list(self.kbs)

    def search_fts(
        self,
        match_expr: str,
        *,
        workspace_ids: list[str],
        limit: int = 20,
        kb_ids: list[str] | None = None,
    ) -> list[FtsHit]:
        self.fts_calls.append(
            {
                "match_expr": match_expr,
                "workspace_ids": list(workspace_ids),
                "limit": limit,
                "kb_ids": None if kb_ids is None else list(kb_ids),
            }
        )
        if self.fts_error is not None:
            raise self.fts_error
        if self.fts_handler is not None:
            return list(self.fts_handler(match_expr, kb_ids))
        hits = (
            self.fts_hits
            if kb_ids is None
            else [hit for hit in self.fts_hits if hit.kb_id in kb_ids]
        )
        return list(hits[:limit])

    def search_questions_fts(
        self,
        match_expr: str,
        *,
        workspace_ids: list[str],
        limit: int = 20,
        kb_ids: list[str] | None = None,
    ) -> list[FtsHit]:
        self.question_fts_calls.append(
            {
                "match_expr": match_expr,
                "workspace_ids": list(workspace_ids),
                "limit": limit,
                "kb_ids": None if kb_ids is None else list(kb_ids),
            }
        )
        if self.question_fts_error is not None:
            raise self.question_fts_error
        hits = (
            self.question_hits
            if kb_ids is None
            else [hit for hit in self.question_hits if hit.kb_id in kb_ids]
        )
        return list(hits[:limit])

    def fetch_chunk(self, chunk_id: str) -> StoredChunk | None:
        self.fetch_calls.append(chunk_id)
        return self.chunks.get(chunk_id)

    def list_document_titles(self, doc_ids: Any) -> dict[str, str]:
        titles: dict[str, str] = {}
        for doc_id in doc_ids:
            chunk = next(
                (item for item in self.chunks.values() if item.doc_id == doc_id), None
            )
            titles[doc_id] = f"title-{doc_id}" if chunk is not None else ""
        return titles

    def fetch_neighbors(
        self, chunk: StoredChunk, *, before: int = 1, after: int = 1
    ) -> list[StoredChunk]:
        return [
            item
            for item in sorted(self.chunks.values(), key=lambda c: c.chunk_index)
            if item.doc_id == chunk.doc_id
            and chunk.chunk_index - before <= item.chunk_index <= chunk.chunk_index + after
        ]


class FakeEmbedder:
    def __init__(
        self,
        identity: tuple[str, str, int] = ("local", "m", 2),
        *,
        identity_error: Exception | None = None,
        query_error: Exception | None = None,
    ) -> None:
        self._identity = identity
        self.identity_error = identity_error
        self.query_error = query_error
        self.query_calls: list[str] = []

    @property
    def identity(self) -> tuple[str, str, int]:
        if self.identity_error is not None:
            raise self.identity_error
        return self._identity

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        if self.query_error is not None:
            raise self.query_error
        return [1.0, 0.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class FakeVector:
    def __init__(
        self,
        hits: list[tuple[str, float]] | None = None,
        *,
        available: bool = True,
        error: Exception | None = None,
        identity: tuple[str, str, int] = ("local", "m", 2),
    ) -> None:
        self.hits = list(hits or [])
        self.available = available
        self.identity = identity
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def query(
        self,
        vector: list[float],
        *,
        kb_ids: list[str],
        limit: int = 10,
        where: Any = None,
    ) -> list[tuple[str, float]]:
        self.calls.append(
            {
                "vector": list(vector),
                "kb_ids": list(kb_ids),
                "limit": limit,
                "where": where,
            }
        )
        if self.error is not None:
            raise self.error
        return list(self.hits[:limit])


def _retriever(
    repo: FakeRepo,
    *,
    embedder: FakeEmbedder | None = None,
    vector: FakeVector | None | object = _UNSET,
    identity_resolver: Any = None,
    **settings: Any,
) -> Retriever:
    store: FakeVector | None = (
        FakeVector() if vector is _UNSET else vector  # type: ignore[assignment]
    )
    return Retriever(
        repo,  # type: ignore[arg-type]
        embedder=embedder or FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=store,  # type: ignore[arg-type]
        settings=RetrieverSettings(**settings) if settings else None,
        identity_resolver=identity_resolver,
    )


def _long_text(prefix: str, tag: str) -> str:
    """≥ parent_min_chars 的正文，避免装配阶段触发父块回填。"""
    return prefix + " " + " ".join(f"{tag}{index}" for index in range(120))


def _hybrid_fixture() -> tuple[FakeRepo, FakeEmbedder, FakeVector]:
    """两路都命中同一对块（顺序相反），让 RRF 分都落在相关性带内。

    两块正文都长于 `parent_min_chars` 且序号不相邻，避免装配阶段回填/合并，
    让本组用例只验证「两路融合」这一层。
    """
    keyword_text = _long_text("alpha beta gamma", "k")
    vector_text = _long_text("delta epsilon zeta", "v")
    repo = FakeRepo(
        kbs=[_kb()],
        fts=[
            _fts("chk_k", keyword_text, 9.0, content_hash="hk", chunk_index=0),
            _fts("chk_v", vector_text, 7.0, content_hash="hv", chunk_index=5),
        ],
        chunks={
            "chk_k": _stored(
                "chk_k", keyword_text, content_hash="hk", chunk_index=0
            ),
            "chk_v": _stored("chk_v", vector_text, content_hash="hv", chunk_index=5),
        },
    )
    embedder = FakeEmbedder()
    vector = FakeVector(hits=[("chk_v", 0.91), ("chk_k", 0.82)])
    return repo, embedder, vector


# ---------- §9.4 混合 / 单路 ----------


def test_hybrid_merges_both_routes() -> None:
    repo, embedder, vector = _hybrid_fixture()
    result = _retriever(repo, embedder=embedder, vector=vector).search(
        "alpha delta", workspace_ids=["ws1"]
    )

    assert {hit.chunk_id for hit in result.hits} == {"chk_k", "chk_v"}
    assert result.mode_used == "hybrid"
    assert result.routes == ("vector", "keyword")
    assert result.degraded == ()
    assert result.reason is None
    scores = [hit.score for hit in result.hits]
    assert scores == sorted(scores, reverse=True)


def test_vector_only_is_semantic_mode() -> None:
    repo = FakeRepo(
        kbs=[_kb()],
        chunks={"chk_v": _stored("chk_v", "delta epsilon", content_hash="hv")},
    )
    vector = FakeVector(hits=[("chk_v", 0.87)])
    result = _retriever(repo, vector=vector).search(
        "delta", workspace_ids=["ws1"], mode="semantic"
    )

    assert repo.fts_calls == []
    assert result.mode_used == "semantic"
    assert result.routes == ("vector",)
    assert [hit.chunk_id for hit in result.hits] == ["chk_v"]
    assert result.hits[0].score == pytest.approx(0.87)
    assert result.hits[0].content_hash == "hv"


def test_keyword_only_is_keyword_mode() -> None:
    repo = FakeRepo(
        kbs=[_kb()],
        fts=[_fts("chk_k", "alpha beta", 5.0, content_hash="hk")],
    )
    vector = FakeVector(hits=[])
    result = _retriever(repo, vector=vector).search("alpha", workspace_ids=["ws1"])

    assert result.mode_used == "keyword"
    assert result.routes == ("keyword",)
    assert [hit.chunk_id for hit in result.hits] == ["chk_k"]
    assert result.hits[0].score == pytest.approx(1.0)
    assert result.degraded == ()


# ---------- doc2query：问题行命中回填父块 ----------


def test_question_keyword_hit_returns_parent_chunk() -> None:
    parent = _stored(
        "chk_p", "部署失败后的回滚步骤说明", kb_id="kb_1", doc_id="doc_q"
    )
    repo = FakeRepo(
        kbs=[_kb()],
        question_fts=[
            _fts("chk_p", parent.content, 6.0, kb_id="kb_1", doc_id="doc_q")
        ],
        chunks={"chk_p": parent},
    )
    result = _retriever(repo, vector=None).search(
        "回滚怎么做", workspace_ids=["ws1"], mode="keyword"
    )

    assert repo.question_fts_calls
    assert [hit.chunk_id for hit in result.hits] == ["chk_p"]
    assert result.hits[0].content == parent.content


def test_question_vector_hit_maps_to_parent_chunk() -> None:
    parent = _stored(
        "chk_p", _long_text("rollback steps", "r"), kb_id="kb_1", doc_id="doc_q"
    )
    repo = FakeRepo(kbs=[_kb()], chunks={"chk_p": parent})
    vector = FakeVector(hits=[("chk_p-q0", 0.9)])
    result = _retriever(repo, vector=vector).search(
        "回滚", workspace_ids=["ws1"], mode="semantic"
    )

    assert [hit.chunk_id for hit in result.hits] == ["chk_p"]
    assert result.hits[0].score == pytest.approx(0.9)


def test_question_hit_dedupes_with_chunk_hit() -> None:
    parent = _stored("chk_p", _long_text("alpha", "p"), kb_id="kb_1")
    repo = FakeRepo(
        kbs=[_kb()],
        fts=[_fts("chk_p", parent.content, 5.0)],
        question_fts=[_fts("chk_p", parent.content, 7.0)],
        chunks={"chk_p": parent},
    )
    result = _retriever(repo, vector=None).search(
        "alpha", workspace_ids=["ws1"], mode="keyword"
    )

    assert [hit.chunk_id for hit in result.hits] == ["chk_p"]


def test_question_index_failure_keeps_chunk_hits() -> None:
    repo = FakeRepo(
        kbs=[_kb()],
        fts=[_fts("chk_k", "alpha", 3.0)],
        question_fts_error=RuntimeError("question fts broken"),
    )
    result = _retriever(repo, vector=None).search(
        "alpha", workspace_ids=["ws1"], mode="keyword"
    )

    assert [hit.chunk_id for hit in result.hits] == ["chk_k"]


def test_hybrid_union_when_disjoint_routes() -> None:
    repo, embedder, vector = _hybrid_fixture()
    result = _retriever(repo, embedder=embedder, vector=vector).search(
        "alpha delta", workspace_ids=["ws1"]
    )

    assert {hit.chunk_id for hit in result.hits} == {"chk_k", "chk_v"}
    assert result.routes == ("vector", "keyword")


def test_ordering_is_deterministic() -> None:
    repo, embedder, vector = _hybrid_fixture()
    retriever = _retriever(repo, embedder=embedder, vector=vector)
    first = retriever.search("alpha delta", workspace_ids=["ws1"])
    second = retriever.search("alpha delta", workspace_ids=["ws1"])
    assert [hit.chunk_id for hit in first.hits] == [hit.chunk_id for hit in second.hits]


# ---------- §4.3 候选池 ----------


def test_pool_limits_passed_down() -> None:
    repo, embedder, vector = _hybrid_fixture()
    _retriever(repo, embedder=embedder, vector=vector).search(
        "alpha", workspace_ids=["ws1"], limit=8
    )

    assert repo.fts_calls[0]["limit"] == 100  # min(max(80, 100), 300)
    assert vector.calls[0]["limit"] == 50  # min(max(40, 50), 200)


def test_pool_hard_caps() -> None:
    repo, embedder, vector = _hybrid_fixture()
    repo.kbs = [_kb("kb_1"), _kb("kb_2")]
    _retriever(
        repo,
        embedder=embedder,
        vector=vector,
        keyword_pool_size=500,
        pool_size=400,
    ).search("alpha", workspace_ids=["ws1"], limit=20)

    # 超取池 = max(topK*5, 下限) × len(KBs)，硬上限 500。
    assert repo.fts_calls[0]["limit"] == 500  # min(max(100, 500) × 2, 500)
    assert vector.calls[0]["limit"] == 500  # min(max(100, 400) × 2, 500)


def test_limit_clamped_to_max() -> None:
    repo, embedder, vector = _hybrid_fixture()
    _retriever(repo, embedder=embedder, vector=vector).search(
        "alpha", workspace_ids=["ws1"], limit=999
    )

    assert repo.fts_calls[0]["limit"] == 100  # limit 被夹到 20：max(100, 100) × 1
    assert vector.calls[0]["limit"] == 100  # max(100, 50) × 1


def test_limit_clamped_to_min() -> None:
    repo, embedder, vector = _hybrid_fixture()
    _retriever(repo, embedder=embedder, vector=vector).search(
        "alpha", workspace_ids=["ws1"], limit=0
    )

    assert repo.fts_calls[0]["limit"] == 100  # limit 被夹到 1
    assert vector.calls[0]["limit"] == 50


def test_rerank_top_k_truncates_before_mmr() -> None:
    # §10.2：精排后先进 MMR 的池截到 rerank_top_k，再按 limit 选。
    chunks = {
        f"chk_{index}": _stored(
            f"chk_{index}",
            _long_text(f"w{index}", f"f{index}"),
            chunk_index=index * 10,
        )
        for index in range(6)
    }
    repo = FakeRepo(kbs=[_kb()], chunks=chunks)
    vector = FakeVector(hits=[(f"chk_{index}", 0.9 - index * 0.01) for index in range(6)])
    result = _retriever(repo, vector=vector, rerank_top_k=3).search(
        "alpha", workspace_ids=["ws1"], limit=6
    )

    assert [hit.chunk_id for hit in result.hits] == ["chk_0", "chk_1", "chk_2"]


class _PassReranker:
    """所有候选都打高分（0.9）：阈值全部通过，截断只发生在 top_k。"""

    @property
    def model_name(self) -> str:
        return "pass"

    @property
    def model_id(self) -> str:
        return "pass"

    def rerank(self, query: str, documents: Any) -> list[RankResult]:
        return [RankResult(index=index, relevance_score=0.9) for index in range(len(documents))]


def test_rerank_cut_counts_final_hits_not_threshold_passes() -> None:
    chunks = {
        f"chk_{index}": _stored(
            f"chk_{index}",
            _long_text(f"w{index}", f"f{index}"),
            chunk_index=index * 10,
        )
        for index in range(6)
    }
    repo = FakeRepo(kbs=[_kb()], chunks=chunks)
    vector = FakeVector(hits=[(f"chk_{index}", 0.9 - index * 0.01) for index in range(6)])
    retriever = Retriever(
        repo,  # type: ignore[arg-type]
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=vector,  # type: ignore[arg-type]
        settings=RetrieverSettings(rerank_top_k=3),
        reranker=_PassReranker(),
    )
    result = retriever.search("alpha", workspace_ids=["ws1"], limit=6)

    # 「N 进 M 出」的 M 是最终返回条数：6 条全过阈值，但 top_k 只留 3 条。
    assert result.candidates_in == 6
    assert result.candidates_kept == len(result.hits) == 3


# ---------- §6 降级矩阵 ----------


def test_vector_unavailable_degrades_to_keyword() -> None:
    repo = FakeRepo(kbs=[_kb()], fts=[_fts("chk_k", "alpha", 3.0)])
    embedder = FakeEmbedder(identity_error=EmbeddingUnavailable("off"))
    vector = FakeVector()
    result = _retriever(repo, embedder=embedder, vector=vector).search(
        "alpha", workspace_ids=["ws1"]
    )

    assert result.mode_used == "keyword"
    assert result.routes == ("keyword",)
    assert result.degraded == ("vector_unavailable",)
    assert vector.calls == []
    assert embedder.query_calls == []


def test_missing_vector_store_degrades_to_keyword() -> None:
    repo = FakeRepo(kbs=[_kb()], fts=[_fts("chk_k", "alpha", 3.0)])
    result = _retriever(repo, vector=None).search("alpha", workspace_ids=["ws1"])

    assert result.mode_used == "keyword"
    assert result.degraded == ("vector_unavailable",)


def test_unavailable_vector_store_degrades_to_keyword() -> None:
    repo = FakeRepo(kbs=[_kb()], fts=[_fts("chk_k", "alpha", 3.0)])
    result = _retriever(repo, vector=FakeVector(available=False)).search(
        "alpha", workspace_ids=["ws1"]
    )

    assert result.degraded == ("vector_unavailable",)
    assert result.mode_used == "keyword"


def test_semantic_mode_downgrade_is_marked() -> None:
    repo = FakeRepo(kbs=[_kb()], fts=[_fts("chk_k", "alpha", 3.0)])
    embedder = FakeEmbedder(identity_error=EmbeddingUnavailable("off"))
    result = _retriever(repo, embedder=embedder).search(
        "alpha", workspace_ids=["ws1"], mode="semantic"
    )

    assert result.mode_used == "keyword"
    assert result.degraded == ("vector_unavailable", "mode_downgraded")


def test_none_backend_kb_is_vector_disabled_not_mismatch() -> None:
    repo = FakeRepo(
        kbs=[_kb(backend="none", model="", dims=0)],
        fts=[_fts("chk_k", "alpha", 3.0)],
    )
    vector = FakeVector()
    result = _retriever(repo, vector=vector).search("alpha", workspace_ids=["ws1"])

    assert vector.calls == []
    assert result.degraded == ("vector_disabled:kb_1",)
    assert not any(item.startswith("embedding_mismatch") for item in result.degraded)


def test_partial_identity_mismatch_queries_consistent_subset() -> None:
    repo = FakeRepo(
        kbs=[
            _kb("kb_a"),
            _kb("kb_b", backend="remote", model="other", dims=4),
            _kb("kb_c", backend="none", model="", dims=0),
        ],
        fts=[
            _fts("chk_k", "alpha beta", 4.0, kb_id="kb_a"),
            _fts("chk_stale", "alpha stale", 3.0, kb_id="kb_b", doc_id="doc_2"),
        ],
        chunks={
            "chk_v": _stored("chk_v", "gamma delta", kb_id="kb_a"),
            "chk_stale": _stored(
                "chk_stale", "alpha stale", kb_id="kb_b", doc_id="doc_2"
            ),
        },
    )
    vector = FakeVector(hits=[("chk_v", 0.9)])
    result = _retriever(repo, vector=vector, expand_on_low_recall=False).search(
        "alpha", workspace_ids=["ws1"]
    )

    assert vector.calls[0]["kb_ids"] == ["kb_a"]
    # 关键词路按 embedding 空间分组，每个可见库恰好被覆盖一次。
    covered = [kb_id for call in repo.fts_calls for kb_id in call["kb_ids"]]
    assert sorted(covered) == ["kb_a", "kb_b", "kb_c"]
    assert len(covered) == len(set(covered))
    assert result.degraded == ("embedding_mismatch:kb_b", "vector_disabled:kb_c")
    # 向量索引过期（kb_b）不代表连关键词都搜不到：它的命中必须还在。
    assert {hit.chunk_id for hit in result.hits} == {"chk_k", "chk_stale", "chk_v"}
    assert result.routes == ("vector", "keyword")


def test_identity_resolver_fans_out_per_kb() -> None:
    repo = FakeRepo(
        kbs=[
            _kb("kb_a"),
            _kb("kb_b", backend="remote", model="other", dims=3),
        ],
        chunks={
            "chk_a": _stored("chk_a", _long_text("alpha", "a"), kb_id="kb_a"),
            "chk_b": _stored(
                "chk_b", _long_text("beta", "b"), kb_id="kb_b", doc_id="doc_2"
            ),
        },
    )
    embedder_a = FakeEmbedder(identity=("local", "m", 2))
    embedder_b = FakeEmbedder(identity=("remote", "other", 3))
    store_a = FakeVector(hits=[("chk_a", 0.8)], identity=("local", "m", 2))
    store_b = FakeVector(hits=[("chk_b", 0.9)], identity=("remote", "other", 3))
    resolved = {"kb_a": (embedder_a, store_a), "kb_b": (embedder_b, store_b)}

    def resolve(kb: KnowledgeBase) -> tuple[FakeEmbedder, FakeVector] | None:
        return resolved.get(kb.id)

    result = _retriever(repo, identity_resolver=resolve).search(
        "alpha", workspace_ids=["ws1"], mode="semantic"
    )

    # 两个库各用各的 embedding 空间，互不标 mismatch。
    assert result.degraded == ()
    assert result.routes == ("vector",)
    assert {hit.chunk_id for hit in result.hits} == {"chk_a", "chk_b"}
    assert embedder_a.query_calls == ["alpha"]
    assert embedder_b.query_calls == ["alpha"]
    assert store_a.calls[0]["kb_ids"] == ["kb_a"]
    assert store_b.calls[0]["kb_ids"] == ["kb_b"]


def test_resolver_none_keeps_kb_in_keyword_route() -> None:
    repo = FakeRepo(
        kbs=[_kb("kb_a", backend="remote", model="x", dims=3)],
        fts=[_fts("chk_k", "alpha", 3.0, kb_id="kb_a")],
        chunks={"chk_k": _stored("chk_k", "alpha", kb_id="kb_a")},
    )
    result = _retriever(repo, identity_resolver=lambda kb: None).search(
        "alpha", workspace_ids=["ws1"]
    )

    assert result.mode_used == "keyword"
    assert result.degraded == ("embedding_unavailable:kb_a",)
    assert [hit.chunk_id for hit in result.hits] == ["chk_k"]


def test_all_identity_mismatch_skips_vector_route() -> None:
    repo = FakeRepo(
        kbs=[_kb(backend="remote", model="other", dims=4)],
        fts=[_fts("chk_k", "alpha", 3.0)],
    )
    vector = FakeVector()
    result = _retriever(repo, vector=vector).search("alpha", workspace_ids=["ws1"])

    assert vector.calls == []
    assert result.mode_used == "keyword"
    assert result.degraded == ("embedding_mismatch:kb_1",)


def test_embedding_error_is_labelled_with_class_name() -> None:
    repo = FakeRepo(kbs=[_kb()], fts=[_fts("chk_k", "alpha", 3.0)])
    embedder = FakeEmbedder(query_error=EmbeddingError("boom"))
    result = _retriever(repo, embedder=embedder).search("alpha", workspace_ids=["ws1"])

    assert result.mode_used == "keyword"
    assert result.degraded == ("embedding_error:EmbeddingError",)


def test_vector_route_error_falls_back_to_keyword() -> None:
    repo = FakeRepo(kbs=[_kb()], fts=[_fts("chk_k", "alpha", 3.0)])
    vector = FakeVector(error=RuntimeError("chroma down"))
    result = _retriever(repo, vector=vector).search("alpha", workspace_ids=["ws1"])

    assert result.mode_used == "keyword"
    assert result.routes == ("keyword",)
    assert result.degraded == ("vector_unavailable",)


def test_keyword_route_error_keeps_vector_result() -> None:
    repo = FakeRepo(
        kbs=[_kb()],
        fts_error=RuntimeError("db locked"),
        chunks={"chk_v": _stored("chk_v", "delta epsilon")},
    )
    vector = FakeVector(hits=[("chk_v", 0.9)])
    result = _retriever(repo, vector=vector).search("alpha", workspace_ids=["ws1"])

    assert result.mode_used == "semantic"
    assert result.routes == ("vector",)
    assert result.degraded == ("keyword_error",)
    assert [hit.chunk_id for hit in result.hits] == ["chk_v"]


def test_keyword_mode_error_raises() -> None:
    repo = FakeRepo(kbs=[_kb()], fts_error=RuntimeError("db locked"))
    with pytest.raises(KnowledgeSearchError):
        _retriever(repo).search("alpha", workspace_ids=["ws1"], mode="keyword")


def test_keyword_mode_never_touches_vector_route() -> None:
    repo = FakeRepo(kbs=[_kb()], fts=[_fts("chk_k", "alpha", 3.0)])
    embedder = FakeEmbedder(identity_error=AssertionError("identity must not be read"))
    vector = FakeVector(hits=[("chk_v", 0.9)])
    result = _retriever(repo, embedder=embedder, vector=vector).search(
        "alpha", workspace_ids=["ws1"], mode="keyword"
    )

    assert vector.calls == []
    assert embedder.query_calls == []
    assert result.mode_used == "keyword"
    assert result.routes == ("keyword",)
    assert result.degraded == ()
    # 池公式不随 mode 变化（spec §4.3）。
    assert repo.fts_calls[0]["limit"] == 100


def test_both_routes_failing_raises_not_empty() -> None:
    repo = FakeRepo(kbs=[_kb()], fts_error=RuntimeError("db locked"))
    embedder = FakeEmbedder(query_error=EmbeddingError("boom"))
    with pytest.raises(KnowledgeSearchError):
        _retriever(repo, embedder=embedder).search("alpha", workspace_ids=["ws1"])


# ---------- 作用域与越权 ----------


def test_unauthorized_explicit_kb_raises() -> None:
    repo = FakeRepo(kbs=[_kb("kb_a")])
    with pytest.raises(KnowledgeSearchError):
        _retriever(repo).search("alpha", workspace_ids=["ws1"], kb_ids=["kb_hidden"])
    assert repo.fts_calls == []


def test_explicit_kb_must_be_enabled() -> None:
    repo = FakeRepo(kbs=[_kb("kb_a", enabled=False)])
    with pytest.raises(KnowledgeSearchError):
        _retriever(repo).search("alpha", workspace_ids=["ws1"], kb_ids=["kb_a"])


def test_explicit_kb_narrows_scope() -> None:
    repo = FakeRepo(
        kbs=[_kb("kb_a"), _kb("kb_b")],
        fts=[_fts("chk_k", "alpha", 3.0, kb_id="kb_a")],
    )
    result = _retriever(repo).search(
        "alpha", workspace_ids=["ws1"], kb_ids=["kb_a"], mode="keyword"
    )

    assert repo.fts_calls[0]["kb_ids"] == ["kb_a"]
    assert result.routes == ("keyword",)


def test_disabled_kbs_are_not_visible() -> None:
    repo = FakeRepo(kbs=[_kb(enabled=False)])
    result = _retriever(repo).search("alpha", workspace_ids=["ws1"])

    assert result.hits == ()
    assert result.reason == "no_scope"
    assert repo.fts_calls == []


# ---------- 四态语义 ----------


def test_no_visible_kb_is_no_scope() -> None:
    repo = FakeRepo(kbs=[])
    vector = FakeVector()
    result = _retriever(repo, vector=vector).search("alpha", workspace_ids=["ws1"])

    assert result.reason == "no_scope"
    assert result.hits == ()
    assert result.mode_used == "keyword"
    assert result.routes == ()
    assert vector.calls == []


def test_no_candidates_reason() -> None:
    repo = FakeRepo(kbs=[_kb()])
    result = _retriever(repo, vector=FakeVector(hits=[])).search(
        "alpha", workspace_ids=["ws1"]
    )

    assert result.hits == ()
    assert result.reason == "no_candidates"
    assert result.routes == ()


def test_empty_query_tokens_skips_fts() -> None:
    repo = FakeRepo(kbs=[_kb()])
    embedder = FakeEmbedder(identity_error=EmbeddingUnavailable("off"))
    result = _retriever(repo, embedder=embedder).search("！！！", workspace_ids=["ws1"])

    assert repo.fts_calls == []
    assert result.hits == ()
    assert result.reason == "empty_query_tokens"
    assert "empty_query_tokens" in result.degraded


def test_empty_query_tokens_still_uses_vector_route() -> None:
    repo = FakeRepo(kbs=[_kb()], chunks={"chk_v": _stored("chk_v", "delta")})
    vector = FakeVector(hits=[("chk_v", 0.8)])
    result = _retriever(repo, vector=vector).search("！！！", workspace_ids=["ws1"])

    assert repo.fts_calls == []
    assert result.routes == ("vector",)
    assert result.mode_used == "semantic"
    assert result.reason is None
    assert result.degraded == ("empty_query_tokens",)


def test_orphan_vector_hits_are_filtered_out() -> None:
    repo = FakeRepo(
        kbs=[_kb()],
        chunks={"chk_good": _stored("chk_good", "delta epsilon")},
    )
    vector = FakeVector(hits=[("chk_good", 0.9), ("chk_orphan", 0.95)])
    result = _retriever(repo, vector=vector).search(
        "alpha", workspace_ids=["ws1"], mode="semantic"
    )

    assert [hit.chunk_id for hit in result.hits] == ["chk_good"]
    # 前两次是向量路回表（含被过滤的孤儿），装配阶段会再次读取命中块。
    assert repo.fetch_calls[:2] == ["chk_good", "chk_orphan"]


def test_disabled_vector_chunk_is_filtered_out() -> None:
    repo = FakeRepo(
        kbs=[_kb()],
        chunks={"chk_off": _stored("chk_off", "delta", is_enabled=False)},
    )
    vector = FakeVector(hits=[("chk_off", 0.9)])
    result = _retriever(repo, vector=vector).search(
        "alpha", workspace_ids=["ws1"], mode="semantic"
    )

    assert result.hits == ()
    assert result.reason == "filtered"


def test_filtered_reason_when_all_vector_hits_dropped() -> None:
    repo = FakeRepo(kbs=[_kb()])
    vector = FakeVector(hits=[("chk_orphan", 0.9)])
    result = _retriever(repo, vector=vector).search("alpha", workspace_ids=["ws1"])

    assert result.hits == ()
    assert result.reason == "filtered"
    assert result.mode_used == "keyword"
    assert result.routes == ()


# ---------- 配置 ----------


def test_retriever_settings_defaults() -> None:
    settings = RetrieverSettings()
    assert settings.vector_weight == 0.7
    assert settings.keyword_weight == 0.3
    assert settings.rrf_k == 60
    assert settings.pool_size == 50
    assert settings.keyword_pool_size == 100
    assert settings.default_limit == 8
    assert settings.max_limit == 20
    assert settings.mmr_lambda == 0.7
    assert settings.rerank_top_k == 10
    assert settings.parent_max_chars == 2400
    assert settings.parent_min_chars == 600
    assert settings.short_chunk_chars == 200
    assert settings.dedup_containment == 0.85
    assert settings.dedup_unique_ratio == 0.02
    assert settings.dedup_jaccard == 0.6
    assert settings.dedup_length_ratio == 3.0
    assert settings.vector_threshold == 0.15
    assert settings.keyword_threshold == 0.3
    assert settings.expand_on_low_recall is True
    assert settings.expansion_limit == 5


def test_default_limit_is_applied() -> None:
    repo, embedder, vector = _hybrid_fixture()
    result = _retriever(repo, embedder=embedder, vector=vector).search(
        "alpha", workspace_ids=["ws1"]
    )
    assert repo.fts_calls[0]["limit"] == 100
    assert vector.calls[0]["limit"] == 50
    assert result.reason is None


def test_search_result_routes_defaults_to_empty() -> None:
    result = SearchResult(hits=(), mode_used="keyword")
    assert result.routes == ()


# ---------- 召回阈值（WeKnora retrieval_config） ----------


def test_vector_threshold_drops_low_similarity() -> None:
    repo = FakeRepo(
        kbs=[_kb()],
        chunks={
            "chk_hi": _stored("chk_hi", _long_text("alpha", "h")),
            "chk_lo": _stored(
                "chk_lo", _long_text("beta", "l"), doc_id="doc_2", chunk_index=1
            ),
        },
    )
    vector = FakeVector(hits=[("chk_hi", 0.9), ("chk_lo", 0.1)])
    result = _retriever(repo, vector=vector, expand_on_low_recall=False).search(
        "alpha", workspace_ids=["ws1"], mode="semantic"
    )

    assert [hit.chunk_id for hit in result.hits] == ["chk_hi"]
    assert result.reason is None


def test_vector_threshold_zero_keeps_everything() -> None:
    repo = FakeRepo(
        kbs=[_kb()],
        chunks={
            "chk_hi": _stored("chk_hi", _long_text("alpha", "h")),
            "chk_lo": _stored(
                "chk_lo", _long_text("beta", "l"), doc_id="doc_2", chunk_index=1
            ),
        },
    )
    vector = FakeVector(hits=[("chk_hi", 0.9), ("chk_lo", 0.1)])
    result = _retriever(
        repo, vector=vector, expand_on_low_recall=False, vector_threshold=0.0
    ).search("alpha", workspace_ids=["ws1"], mode="semantic")

    assert {hit.chunk_id for hit in result.hits} == {"chk_hi", "chk_lo"}


def test_all_vector_hits_below_threshold_reports_filtered() -> None:
    repo = FakeRepo(
        kbs=[_kb()],
        chunks={"chk_lo": _stored("chk_lo", _long_text("alpha", "l"))},
    )
    vector = FakeVector(hits=[("chk_lo", 0.05)])
    result = _retriever(repo, vector=vector, expand_on_low_recall=False).search(
        "alpha", workspace_ids=["ws1"], mode="semantic"
    )

    assert result.hits == ()
    assert result.reason == "filtered"


def test_keyword_threshold_is_relative_to_group_best() -> None:
    repo = FakeRepo(
        kbs=[_kb()],
        fts=[
            _fts("chk_a", _long_text("alpha", "a"), 10.0, content_hash="ha"),
            _fts(
                "chk_b",
                _long_text("beta", "b"),
                2.0,
                doc_id="doc_2",
                chunk_index=1,
                content_hash="hb",
            ),
        ],
        chunks={
            "chk_a": _stored("chk_a", _long_text("alpha", "a"), content_hash="ha"),
            "chk_b": _stored(
                "chk_b",
                _long_text("beta", "b"),
                doc_id="doc_2",
                chunk_index=1,
                content_hash="hb",
            ),
        },
    )
    kept = _retriever(repo, vector=None, expand_on_low_recall=False).search(
        "alpha", workspace_ids=["ws1"], mode="keyword"
    )
    everything = _retriever(
        repo, vector=None, expand_on_low_recall=False, keyword_threshold=0.0
    ).search("alpha", workspace_ids=["ws1"], mode="keyword")

    assert [hit.chunk_id for hit in kept.hits] == ["chk_a"]
    assert {hit.chunk_id for hit in everything.hits} == {"chk_a", "chk_b"}


# ---------- 低召回查询扩展（WeKnora query_expansion） ----------


def test_query_variants_strip_stopwords_and_question_words() -> None:
    variants = _query_variants(
        "请问线上版本出问题了怎么回滚", mode="jieba", limit=5
    )

    assert variants
    assert len(variants) <= 5
    assert len(set(variants)) == len(variants)
    assert all(len(variant) >= 3 for variant in variants)
    assert any("回滚" in variant for variant in variants)


def test_low_recall_triggers_keyword_expansion() -> None:
    hit = _fts("chk_x", "回滚流程说明", 5.0, doc_id="doc_x")
    calls: list[str] = []

    def handler(match_expr: str, kb_ids: list[str] | None) -> list[FtsHit]:
        calls.append(match_expr)
        # 原查询返回空；只有变体（第二次及以后）命中。
        return [] if len(calls) == 1 else [hit]

    repo = FakeRepo(
        kbs=[_kb()],
        chunks={"chk_x": _stored("chk_x", "回滚流程说明", doc_id="doc_x")},
        fts_handler=handler,
    )
    result = _retriever(repo, vector=None).search(
        "线上版本出问题了怎么回滚", workspace_ids=["ws1"], mode="keyword"
    )

    assert len(calls) > 1
    assert [item.chunk_id for item in result.hits] == ["chk_x"]


def test_expansion_can_be_disabled() -> None:
    hit = _fts("chk_x", "回滚流程说明", 5.0, doc_id="doc_x")
    calls: list[str] = []

    def handler(match_expr: str, kb_ids: list[str] | None) -> list[FtsHit]:
        calls.append(match_expr)
        return [] if len(calls) == 1 else [hit]

    repo = FakeRepo(
        kbs=[_kb()],
        chunks={"chk_x": _stored("chk_x", "回滚流程说明", doc_id="doc_x")},
        fts_handler=handler,
    )
    result = _retriever(repo, vector=None, expand_on_low_recall=False).search(
        "线上版本出问题了怎么回滚", workspace_ids=["ws1"], mode="keyword"
    )

    assert len(calls) == 1
    assert result.hits == ()
    assert result.reason == "no_candidates"
