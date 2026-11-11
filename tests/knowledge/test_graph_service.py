from __future__ import annotations

from types import SimpleNamespace

import pytest

from nanobot.knowledge.graph.extractor import ExtractionResult, GraphExtractionError
from nanobot.knowledge.graph.models import GraphExpansion
from nanobot.knowledge.graph.repo import GraphRepository
from nanobot.knowledge.graph.service import GraphService
from nanobot.knowledge.service import KnowledgeService

from .graph_fakes import FakeGraphStore


class ScriptedExtractor:
    model_id = "scripted#m"

    def __init__(self, result: ExtractionResult | None = None) -> None:
        self.result = result or ExtractionResult(
            entities=(("web-api", "service", "网关"), ("值班二号", "person", "")),
            relations=(("web-api", "值班二号", "升级给", "", 8.0),),
        )

    def extract(self, heading_path: str, content: str) -> ExtractionResult:
        return self.result


class FailingExtractor:
    model_id = "scripted#fail"

    def extract(self, heading_path: str, content: str) -> ExtractionResult:
        raise GraphExtractionError("http_4xx")


def _graph_config() -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True,
        backend="neo4j",
        extraction=SimpleNamespace(
            enabled=True,
            concurrency=1,
            max_chunks_per_doc=200,
            max_entities_per_chunk=20,
            max_relations_per_chunk=30,
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


@pytest.fixture
def graph_env(tmp_path):
    service = KnowledgeService.local(tmp_path / "state.db")
    kb = service.create_kb(name="k", workspace_id="default")
    doc = service._repo.insert_document(
        doc_id="doc-1",
        kb_id=kb.id,
        workspace_id="default",
        title="网关文档",
        source_kind="external",
        source_path=str(tmp_path / "a.md"),
        original_name="a.md",
        file_hash="h1",
        file_size=1,
    )
    with service._db.connect(immediate=True) as conn:
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
                "网关升级由值班二号处理",
                "",
                12,
                "网关 升级 值班",
                "now",
            ),
        )
        conn.execute(
            "UPDATE documents SET parse_status='ready' WHERE id=?", (doc.id,)
        )

    store = FakeGraphStore()
    graph = GraphService(
        repo=GraphRepository(service._repo.database),
        knowledge_repo=service._repo,
        store=store,
        extractor=ScriptedExtractor(),
        config=_graph_config(),
    )
    service.attach_graph(graph)
    return SimpleNamespace(
        service=service, graph=graph, store=store, repo=graph.repo, kb=kb, doc=doc
    )


def test_process_document_writes_and_mirrors(graph_env):
    graph_env.graph.enqueue_document(graph_env.doc.id)
    graph_env.graph.process_document(graph_env.doc.id)

    assert graph_env.repo.count_entities(graph_env.kb.id) == 2
    assert graph_env.repo.get_state(graph_env.doc.id).status == "ready"
    mirrored = graph_env.store.entities[graph_env.kb.id]
    assert {entity.name for entity in mirrored} == {"web-api", "值班二号"}
    assert mirrored[0].chunk_ids == ("chk-1",)


def test_extract_query_entities_returns_names(graph_env):
    graph_env.graph.enqueue_document(graph_env.doc.id)
    graph_env.graph.process_document(graph_env.doc.id)
    assert graph_env.graph.extract_query_entities("谁负责升级") == (
        "web-api",
        "值班二号",
    )


def test_extract_query_entities_uses_dedicated_query_prompt(graph_env):
    class QueryExtractor:
        model_id = "query#m"

        def extract(self, heading_path: str, content: str):
            raise AssertionError("不应使用入库 chunk prompt")

        def extract_query(self, query: str) -> tuple[str, ...]:
            assert query == "谁负责升级"
            return ("值班二号",)

    graph_env.graph._extractor = QueryExtractor()
    assert graph_env.graph.extract_query_entities("谁负责升级") == ("值班二号",)


def test_extract_query_entities_failure_is_empty(graph_env):
    graph_env.graph._extractor = FailingExtractor()
    assert graph_env.graph.extract_query_entities("谁负责升级") == ()


def test_query_related_returns_relations_and_live_evidence(graph_env):
    graph_env.graph.enqueue_document(graph_env.doc.id)
    graph_env.graph.process_document(graph_env.doc.id)
    entities = {
        entity.name: entity for entity in graph_env.repo.list_entities(graph_env.kb.id)
    }
    graph_env.store.expansions = [
        GraphExpansion(entity_ids=(entities["值班二号"].id,))
    ]

    result = graph_env.graph.query_related(graph_env.kb.id, "web-api")

    assert {entity.name for entity in result.entities} == {"web-api", "值班二号"}
    assert [relation.relation for relation in result.relations] == ["升级给"]
    assert [chunk.id for chunk in result.chunks] == ["chk-1"]
    assert result.relations_total == 1
    assert result.chunks_total == 1
    assert result.truncated is False


def test_extractor_failure_does_not_fail_document(graph_env):
    graph_env.graph._extractor = FailingExtractor()
    graph_env.graph.enqueue_document(graph_env.doc.id)
    graph_env.graph.process_document(graph_env.doc.id)

    assert graph_env.repo.get_state(graph_env.doc.id).status == "failed"
    document = graph_env.service.get_document(graph_env.doc.id)
    assert document is not None and document.parse_status == "ready"


def test_empty_entities_is_skipped(graph_env):
    graph_env.graph._extractor = ScriptedExtractor(ExtractionResult((), (), declined=True))
    graph_env.graph.enqueue_document(graph_env.doc.id)
    graph_env.graph.process_document(graph_env.doc.id)
    assert graph_env.repo.get_state(graph_env.doc.id).status == "skipped"


def test_rebuild_purge_clears_derived_rows(graph_env):
    graph_env.graph.enqueue_document(graph_env.doc.id)
    graph_env.graph.process_document(graph_env.doc.id)
    result = graph_env.graph.rebuild_kb(graph_env.kb.id, purge=True)

    assert result["queued"] == 1
    assert graph_env.kb.id in graph_env.store.deleted_kbs
    assert graph_env.repo.count_entities(graph_env.kb.id) == 0
    assert graph_env.repo.get_state(graph_env.doc.id).status == "pending"


def test_reprocess_is_idempotent(graph_env):
    for _ in range(2):
        graph_env.graph.enqueue_document(graph_env.doc.id, force=True)
        graph_env.graph.process_document(graph_env.doc.id)
    assert graph_env.repo.count_entities(graph_env.kb.id) == 2
    relation = graph_env.repo.list_relations(graph_env.kb.id)[0]
    assert relation.mention_count == 1
