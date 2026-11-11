from __future__ import annotations

from typing import Any

import pytest

from nanobot.knowledge.embedding import NoneEmbedder
from nanobot.knowledge.fusion import SearchHit
from nanobot.knowledge.graph.models import GraphEvidencePayload, GraphExpansion
from nanobot.knowledge.graph.repo import GraphRepository
from nanobot.knowledge.graph.route import GraphRoute
from nanobot.knowledge.rerank import RankResult, RerankSettings
from nanobot.knowledge.retriever import Retriever, RetrieverSettings
from nanobot.knowledge.service import KnowledgeService

from .graph_fakes import FakeGraphStore


def _setup(tmp_path):
    service = KnowledgeService.local(tmp_path / "state.db")
    repo = service._repo
    kb = service.create_kb(name="k", workspace_id="default")
    doc = repo.insert_document(
        doc_id="doc-1",
        kb_id=kb.id,
        workspace_id="default",
        title="灰度文档",
        source_kind="external",
        source_path=str(tmp_path / "a.md"),
        original_name="a.md",
        file_hash="h1",
        file_size=1,
    )
    with repo.database.connect(immediate=True) as conn:
        conn.execute(
            "INSERT INTO document_chunks"
            " (id,doc_id,kb_id,workspace_id,chunk_index,content,heading_path,"
            "  char_count,search_text,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "chk-1",
                doc.id,
                kb.id,
                "default",
                0,
                "灰度发布流程由值班二号执行",
                "",
                14,
                "灰度 发布 流程 值班 二号",
                "now",
            ),
        )
        conn.execute(
            "INSERT INTO document_chunks"
            " (id,doc_id,kb_id,workspace_id,chunk_index,content,heading_path,"
            "  char_count,search_text,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "chk-2",
                doc.id,
                kb.id,
                "default",
                5,
                "另一段只被图谱引用的正文",
                "附录",
                14,
                "另一段 图谱 引用",
                "now",
            ),
        )
        conn.execute("UPDATE documents SET parse_status='ready' WHERE id=?", (doc.id,))
    graph_repo = GraphRepository(repo.database)
    graph_repo.replace_document_graph(
        doc.id,
        kb.id,
        entities=[("灰度发布", "concept", ""), ("值班二号", "person", "")],
        relations=[("灰度发布", "值班二号", "执行人", "", 7.0)],
        mentions=[("灰度发布", "chk-1"), ("灰度发布", "chk-2"), ("值班二号", "chk-1")],
    )
    return service, repo, graph_repo, kb.id


def _retriever(
    service,
    repo,
    graph_repo,
    store,
    *,
    enabled: bool = True,
    reranker: Any = None,
    rerank_settings: RerankSettings | None = None,
    query_entities: Any = None,
    **overrides,
):
    settings = RetrieverSettings(graph_enabled=enabled, **overrides)
    route = GraphRoute(
        repo=graph_repo,
        knowledge_repo=repo,
        store=store,
        settings=settings,
        query_entities=query_entities,
    )
    return Retriever(
        repo,
        embedder=NoneEmbedder(),
        vector_store=None,
        settings=settings,
        reranker=reranker,
        rerank_settings=rerank_settings,
        tokenizer_mode="jieba",
        graph_route=route,
    )


class _RecordingReranker:
    """按正文打固定分，并记录精排实际收到的 passage。"""

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores
        self.passages: list[str] = []

    @property
    def model_name(self) -> str:
        return "recording"

    @property
    def model_id(self) -> str:
        return "recording"

    def rerank(self, query: str, documents: Any) -> list[RankResult]:
        self.passages = list(documents)
        results: list[RankResult] = []
        for index, passage in enumerate(documents):
            score = 0.5
            for marker, value in self.scores.items():
                if marker in passage:
                    score = value
                    break
            results.append(RankResult(index=index, relevance_score=score))
        return results


class _PassReranker:
    def __init__(self, score: float = 0.9) -> None:
        self.score = score

    @property
    def model_name(self) -> str:
        return "pass"

    @property
    def model_id(self) -> str:
        return "pass"

    def rerank(self, query: str, documents: Any) -> list[RankResult]:
        return [
            RankResult(index=index, relevance_score=self.score)
            for index in range(len(documents))
        ]


def test_graph_disabled_keeps_two_route_behavior(tmp_path):
    service, repo, graph_repo, kb_id = _setup(tmp_path)
    store = FakeGraphStore()
    retriever = _retriever(
        service, repo, graph_repo, store, enabled=False
    )
    result = retriever.search("灰度发布", workspace_ids=["default"])
    assert "graph" not in result.routes
    assert all(hit.graph_evidence is None for hit in result.hits)


def test_graph_route_adds_route_and_evidence(tmp_path):
    service, repo, graph_repo, kb_id = _setup(tmp_path)
    store = FakeGraphStore()
    store.expansions = [
        GraphExpansion(entity_ids=("e2",), edges=(), chunk_ids=("chk-1",))
    ]
    retriever = _retriever(service, repo, graph_repo, store)
    result = retriever.search("灰度发布", workspace_ids=["default"])
    assert "graph" in result.routes
    assert any(hit.graph_evidence is not None for hit in result.hits)


def test_graph_backend_failure_degrades_not_raises(tmp_path):
    service, repo, graph_repo, kb_id = _setup(tmp_path)
    store = FakeGraphStore()
    store.available = False
    retriever = _retriever(service, repo, graph_repo, store)
    result = retriever.search("灰度发布", workspace_ids=["default"])
    assert "graph_unavailable" in result.degraded
    assert result.hits  # 关键词路照常返回


def test_graph_truncated_label(tmp_path):
    service, repo, graph_repo, kb_id = _setup(tmp_path)
    store = FakeGraphStore()
    store.expansions = [
        GraphExpansion(entity_ids=("e2",), edges=(), chunk_ids=("chk-1",), truncated=True)
    ]
    result = _retriever(service, repo, graph_repo, store).search(
        "灰度发布", workspace_ids=["default"]
    )
    assert "graph_truncated" in result.degraded


def test_graph_enabled_without_route_degrades(tmp_path):
    service, repo, graph_repo, kb_id = _setup(tmp_path)
    settings = RetrieverSettings(graph_enabled=True)
    retriever = Retriever(
        repo,
        embedder=NoneEmbedder(),
        vector_store=None,
        settings=settings,
        tokenizer_mode="jieba",
    )
    result = retriever.search("灰度发布", workspace_ids=["default"])
    assert "graph_unavailable" in result.degraded


def test_graph_only_hit_gets_zero_score_and_text_hit_keeps_its_score(tmp_path):
    service, repo, graph_repo, kb_id = _setup(tmp_path)
    store = FakeGraphStore()
    result = _retriever(service, repo, graph_repo, store).search(
        "灰度发布", workspace_ids=["default"]
    )
    by_id = {hit.chunk_id: hit for hit in result.hits}
    # chk-2 只有图谱命中：图没有相似度分，按 WeKnora 口径记 0，不参与 RRF。
    assert by_id["chk-2"].score == 0.0
    assert by_id["chk-2"].graph_evidence is not None
    # 同时被关键词和图谱命中的块保留关键词路分数。
    assert by_id["chk-1"].score > 0.0
    assert set(result.routes) == {"keyword", "graph"}


def test_llm_extracted_entities_seed_graph_route(tmp_path):
    service, repo, graph_repo, kb_id = _setup(tmp_path)
    calls: list[str] = []

    def extract(query: str) -> tuple[str, ...]:
        calls.append(query)
        return ("值班二号",)

    retriever = _retriever(
        service, repo, graph_repo, FakeGraphStore(), query_entities=extract
    )
    result = retriever.search("谁负责执行", workspace_ids=["default"])

    assert calls == ["谁负责执行"]
    assert "graph" in result.routes
    assert any(hit.graph_evidence is not None for hit in result.hits)


def test_unmatched_llm_entities_fall_back_to_local_linker(tmp_path):
    service, repo, graph_repo, kb_id = _setup(tmp_path)

    def extract(query: str) -> tuple[str, ...]:
        return ("发布负责人",)  # 图里没有这个实体

    retriever = _retriever(
        service, repo, graph_repo, FakeGraphStore(), query_entities=extract
    )
    result = retriever.search("灰度发布", workspace_ids=["default"])

    assert "graph" in result.routes
    assert any(hit.graph_evidence is not None for hit in result.hits)


def test_query_entity_extraction_failure_falls_back_to_local_linker(tmp_path):
    service, repo, graph_repo, kb_id = _setup(tmp_path)

    def fail(query: str) -> tuple[str, ...]:
        raise RuntimeError("model down")

    retriever = _retriever(
        service, repo, graph_repo, FakeGraphStore(), query_entities=fail
    )
    result = retriever.search("灰度发布", workspace_ids=["default"])

    # 抽实体失败不能拖垮图路：回退本地匹配，仍然能产出 graph 路由。
    assert "graph" in result.routes
    assert "graph_unavailable" not in result.degraded


def test_graph_hit_uses_model_score_as_rerank_base(tmp_path):
    service, repo, graph_repo, kb_id = _setup(tmp_path)
    graph = GraphEvidencePayload(seeds=("灰度发布",), path=(), hop=1, entity_ids=())
    text_hit = SearchHit(
        chunk_id="chk-1",
        doc_id="doc-1",
        kb_id=kb_id,
        chunk_index=0,
        heading_path="",
        page=None,
        locator="",
        content="关键词命中的正文",
        char_count=8,
        score=0.2,
    )
    graph_hit = SearchHit(
        chunk_id="chk-2",
        doc_id="doc-1",
        kb_id=kb_id,
        chunk_index=5,
        heading_path="附录",
        page=None,
        locator="",
        content="纯图谱命中的正文",
        char_count=8,
        score=0.0,
        graph_evidence=graph,
    )
    retriever = _retriever(
        service,
        repo,
        graph_repo,
        FakeGraphStore(),
        reranker=_PassReranker(0.5),
        rerank_settings=RerankSettings(max_candidates=10, threshold=0.0),
    )
    scored, _labels, _outcome, _in, _kept = retriever._apply_rerank(
        "灰度发布", [text_hit, graph_hit], explicit_scope=False
    )
    by_id = {hit.chunk_id: hit for hit in scored}
    # 0.6*0.5 + 0.3*0.5 + 0.1（纯图 base 用 model_score）
    assert by_id["chk-2"].score == pytest.approx(0.55)
    # 0.6*0.5 + 0.3*0.2 + 0.1（图+文本保留文本分）
    assert by_id["chk-1"].score == pytest.approx(0.46)


def test_full_search_flow_sends_graph_only_hit_to_rerank(tmp_path):
    service, repo, graph_repo, kb_id = _setup(tmp_path)
    with repo.database.connect(immediate=True) as conn:
        conn.execute(
            "INSERT INTO document_chunks"
            " (id,doc_id,kb_id,workspace_id,chunk_index,content,heading_path,"
            "  char_count,search_text,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "chk-3",
                "doc-1",
                kb_id,
                "default",
                2,
                "灰度发布值班说明：第三块只走关键词",
                "附录二",
                24,
                "灰度 发布 值班",
                "now",
            ),
        )
    extract_calls: list[str] = []

    def extract(query: str) -> tuple[str, ...]:
        extract_calls.append(query)
        return ("灰度发布",)

    reranker = _RecordingReranker(
        {
            "只被图谱引用": 0.9,
            "只走关键词": 0.7,
            "灰度发布流程": 0.6,
        }
    )
    retriever = _retriever(
        service,
        repo,
        graph_repo,
        FakeGraphStore(),
        query_entities=extract,
        reranker=reranker,
        rerank_settings=RerankSettings(max_candidates=1, threshold=0.0),
        rerank_top_k=10,
    )
    result = retriever.search("灰度发布", workspace_ids=["default"], limit=8)

    assert extract_calls == ["灰度发布"]
    # max_candidates=1 时，纯图命中 chk-2 仍必须进入精排，而不是被按分截掉。
    assert len(reranker.passages) == 3
    assert any("只被图谱引用" in passage for passage in reranker.passages)
    by_id = {hit.chunk_id: hit for hit in result.hits}
    assert set(by_id) == {"chk-1", "chk-2", "chk-3"}
    assert by_id["chk-2"].graph_evidence is not None
    # 纯图命中：0.6*0.9 + 0.3*0.9 + 0.1 = 0.91（base 用 model_score）。
    assert by_id["chk-2"].score == pytest.approx(0.91)
    # 图+关键词命中保留文本 base，分数低于纯图命中。
    assert by_id["chk-1"].score < by_id["chk-2"].score
    assert result.hits[0].chunk_id == "chk-2"
    assert result.rerank == "ok"
    assert "graph" in result.routes
    assert result.candidates_in == 3
    assert result.candidates_kept == 3


def test_graph_only_hit_is_exempt_from_rerank_candidate_cap(tmp_path):
    service, repo, graph_repo, kb_id = _setup(tmp_path)
    store = FakeGraphStore()
    retriever = _retriever(
        service,
        repo,
        graph_repo,
        store,
        reranker=_PassReranker(),
        rerank_settings=RerankSettings(max_candidates=1, threshold=0.0),
        rerank_top_k=10,
    )
    result = retriever.search("灰度发布", workspace_ids=["default"], limit=8)
    by_id = {hit.chunk_id: hit for hit in result.hits}
    # 图命中不进「按分取前 N」，否则 score=0 会被直接截掉；精排后它应拿到复合分。
    assert by_id["chk-2"].score > 0.0
    assert result.candidates_in >= 2
