from __future__ import annotations

from nanobot.knowledge.embedding import NoneEmbedder
from nanobot.knowledge.graph.models import GraphExpansion
from nanobot.knowledge.graph.repo import GraphRepository
from nanobot.knowledge.graph.route import GraphRoute
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
        conn.execute("UPDATE documents SET parse_status='ready' WHERE id=?", (doc.id,))
    graph_repo = GraphRepository(repo.database)
    graph_repo.replace_document_graph(
        doc.id,
        kb.id,
        entities=[("灰度发布", "concept", ""), ("值班二号", "person", "")],
        relations=[("灰度发布", "值班二号", "执行人", "", 7.0)],
        mentions=[("灰度发布", "chk-1"), ("值班二号", "chk-1")],
    )
    return service, repo, graph_repo, kb.id


def _retriever(service, repo, graph_repo, store, *, enabled: bool = True, **overrides):
    settings = RetrieverSettings(graph_enabled=enabled, **overrides)
    route = GraphRoute(
        repo=graph_repo,
        knowledge_repo=repo,
        store=store,
        settings=settings,
    )
    return Retriever(
        repo,
        embedder=NoneEmbedder(),
        vector_store=None,
        settings=settings,
        tokenizer_mode="jieba",
        graph_route=route,
    )


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
