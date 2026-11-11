from __future__ import annotations

from types import SimpleNamespace

import pytest

from nanobot.knowledge.graph.extractor import ExtractionResult
from nanobot.knowledge.graph.repo import GraphRepository
from nanobot.knowledge.graph.service import GraphService
from nanobot.knowledge.service import KnowledgeService
from nanobot.webui import knowledge_routes as routes
from nanobot.webui.knowledge_api import KnowledgeApi, KnowledgeApiError
from nanobot.webui.knowledge_services import KnowledgeServices

from .graph_fakes import FakeGraphStore


class _Extractor:
    model_id = "scripted#m"

    def extract(self, heading_path: str, content: str) -> ExtractionResult:
        return ExtractionResult((), (), declined=True)


def _graph_config():
    return SimpleNamespace(
        enabled=True,
        backend="neo4j",
        extraction=SimpleNamespace(
            enabled=True,
            concurrency=1,
            max_chunks_per_doc=200,
            require_confirmation=True,
        ),
        expansion=SimpleNamespace(
            enabled=True,
            weight=0.25,
            hops=1,
            seed_limit=5,
            max_entities=20,
            max_chunks=50,
            timeout_seconds=0.8,
        ),
    )


def _build(tmp_path, *, with_graph: bool = True, available: bool = True):
    service = KnowledgeService.local(tmp_path / "state.db")
    kb = service.create_kb(name="k", workspace_id="default")
    if with_graph:
        store = FakeGraphStore()
        store.available = available
        graph = GraphService(
            repo=GraphRepository(service._repo.database),
            knowledge_repo=service._repo,
            store=store,
            extractor=_Extractor(),
            config=_graph_config(),
        )
        service.attach_graph(graph)
    services = KnowledgeServices(
        service=service, config=SimpleNamespace(enabled=True)
    )
    return KnowledgeApi(services), service, kb, graph if with_graph else None


def _add_entity(service, kb, *, name: str = "灰度发布", workspace: str | None = None):
    doc = service._repo.insert_document(
        doc_id=f"doc-{name}",
        kb_id=kb.id,
        workspace_id="default",
        title=name,
        source_kind="external",
        source_path=f"/tmp/{name}.md",
        original_name=f"{name}.md",
        file_hash=f"h-{name}",
        file_size=1,
    )
    with service._repo.database.connect(immediate=True) as conn:
        conn.execute(
            "INSERT INTO document_chunks"
            " (id,doc_id,kb_id,workspace_id,chunk_index,content,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (f"chk-{name}", doc.id, kb.id, "default", 0, "x", "now"),
        )
    graph_repo = GraphRepository(service._repo.database)
    graph_repo.replace_document_graph(
        doc.id,
        kb.id,
        entities=[(name, "concept", "")],
        relations=[],
        mentions=[(name, f"chk-{name}")],
    )
    return graph_repo.list_entities(kb.id)[0]


def test_graph_actions_registered():
    assert {
        routes.GRAPH_STATS,
        routes.GRAPH_ENTITIES,
        routes.GRAPH_ENTITY,
        routes.GRAPH_SUBGRAPH,
    } <= routes.READ_ACTIONS
    assert {routes.GRAPH_EXTRACT, routes.GRAPH_REBUILD} <= routes.WRITE_ACTIONS
    assert (
        routes.READ_PATHS["/api/settings/knowledge/graph/stats"]
        == routes.GRAPH_STATS
    )
    assert (
        routes.WRITE_PATHS["/api/settings/knowledge/graph/rebuild"]
        == routes.GRAPH_REBUILD
    )


def test_graph_disabled_returns_409(tmp_path):
    api, _service, kb, _graph = _build(tmp_path, with_graph=False)
    with pytest.raises(KnowledgeApiError) as exc:
        api.handle(routes.GRAPH_STATS, {"kbId": kb.id})
    assert exc.value.code == "KNOWLEDGE_GRAPH_DISABLED"


def test_graph_stats_and_entities(tmp_path):
    api, service, kb, _graph = _build(tmp_path)
    _add_entity(service, kb)
    stats = api.handle(routes.GRAPH_STATS, {"kbId": kb.id})
    assert stats["enabled"] is True
    assert stats["entities"] == 1
    page = api.handle(routes.GRAPH_ENTITIES, {"kbId": kb.id})
    assert page["total"] == 1
    assert page["entities"][0]["name"] == "灰度发布"


def test_graph_entity_out_of_scope_returns_404(tmp_path):
    api, service, kb, _graph = _build(tmp_path)
    entity = _add_entity(service, kb)
    # 换一个看不到该工作区的服务容器
    from nanobot.webui.knowledge_services import KnowledgeServices as Services

    api = KnowledgeApi(
        Services(service=service, config=SimpleNamespace(enabled=True),
                 workspace_id="other")
    )
    with pytest.raises(KnowledgeApiError) as exc:
        api.handle(routes.GRAPH_ENTITY, {"id": entity.id})
    assert exc.value.code == "KNOWLEDGE_GRAPH_ENTITY_NOT_FOUND"


def test_graph_entity_detail_in_scope(tmp_path):
    api, service, kb, _graph = _build(tmp_path)
    entity = _add_entity(service, kb)
    detail = api.handle(routes.GRAPH_ENTITY, {"id": entity.id})
    assert detail["entity"]["name"] == "灰度发布"
    assert detail["mentions"][0]["chunkId"] == "chk-灰度发布"


def test_extract_requires_confirm_for_kb_backfill(tmp_path):
    api, _service, kb, _graph = _build(tmp_path)
    with pytest.raises(KnowledgeApiError) as exc:
        api.handle(routes.GRAPH_EXTRACT, {"kbId": kb.id})
    assert exc.value.code == "KNOWLEDGE_GRAPH_CONFIRM_REQUIRED"


def test_rebuild_requires_confirm(tmp_path):
    api, _service, kb, _graph = _build(tmp_path)
    with pytest.raises(KnowledgeApiError) as exc:
        api.handle(routes.GRAPH_REBUILD, {"kbId": kb.id})
    assert exc.value.code == "KNOWLEDGE_GRAPH_CONFIRM_REQUIRED"


def test_rebuild_backend_unavailable_returns_503(tmp_path):
    api, _service, kb, _graph = _build(tmp_path, available=False)
    with pytest.raises(KnowledgeApiError) as exc:
        api.handle(routes.GRAPH_REBUILD, {"kbId": kb.id, "confirm": True})
    assert exc.value.code == "KNOWLEDGE_GRAPH_BACKEND_UNAVAILABLE"
