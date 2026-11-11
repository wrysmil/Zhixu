from __future__ import annotations

from nanobot.knowledge.database import (
    _V1_STATEMENTS,
    _V2_STATEMENTS,
    SCHEMA_VERSION,
    KnowledgeDatabase,
)
from nanobot.knowledge.graph.repo import GraphRepository
from nanobot.knowledge.repository import KnowledgeRepository

GRAPH_TABLES = {
    "graph_entities",
    "graph_relations",
    "graph_mentions",
    "graph_relation_evidence",
    "graph_extraction_state",
}


def _db(tmp_path) -> tuple[KnowledgeDatabase, KnowledgeRepository, GraphRepository]:
    db = KnowledgeDatabase(tmp_path / "state.db")
    db.init_schema()
    return db, KnowledgeRepository(db), GraphRepository(db)


def _add_doc_with_chunk(repo: KnowledgeRepository, kb_id: str, doc_id: str = "doc-1"):
    document = repo.insert_document(
        doc_id=doc_id,
        kb_id=kb_id,
        workspace_id="default",
        title="d",
        source_kind="external",
        source_path=f"/tmp/{doc_id}.md",
        original_name=f"{doc_id}.md",
        file_hash=f"hash-{doc_id}",
        file_size=1,
    )
    with repo.database.connect(immediate=True) as conn:
        conn.execute(
            "INSERT INTO document_chunks"
            " (id,doc_id,kb_id,workspace_id,chunk_index,content,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (f"chk-{doc_id}", doc_id, kb_id, "default", 0, "x", "now"),
        )
    return document


def test_schema_v3_creates_graph_tables(tmp_path):
    db, _, _ = _db(tmp_path)
    assert SCHEMA_VERSION == "3"
    with db.connect() as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert GRAPH_TABLES <= names


def test_v2_database_upgrades_to_v3(tmp_path):
    path = tmp_path / "legacy.db"
    db = KnowledgeDatabase(path)
    with db.connect(immediate=True) as conn:
        for statement in (*_V1_STATEMENTS, *_V2_STATEMENTS):
            conn.execute(statement)
        conn.execute(
            "INSERT OR REPLACE INTO _schema_meta(key, value) VALUES ('version','2')"
        )
    db.init_schema()
    with db.connect() as conn:
        version = conn.execute(
            "SELECT value FROM _schema_meta WHERE key='version'"
        ).fetchone()[0]
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert version == "3"
    assert GRAPH_TABLES <= names


def test_replace_document_graph_is_idempotent(tmp_path):
    _, repo, graph = _db(tmp_path)
    kb = repo.create_kb(name="k", workspace_id="default")
    doc = _add_doc_with_chunk(repo, kb.id)
    entities = [("web-api", "service", "网关"), ("值班二号", "person", "")]
    relations = [("web-api", "值班二号", "升级给", "", 8.0)]
    mentions = [("web-api", f"chk-{doc.id}"), ("值班二号", f"chk-{doc.id}")]
    for _ in range(2):
        graph.replace_document_graph(
            doc.id, kb.id, entities=entities, relations=relations, mentions=mentions
        )
    assert graph.count_entities(kb.id) == 2
    with repo.database.connect() as conn:
        mention_rows = conn.execute("SELECT COUNT(*) FROM graph_mentions").fetchone()[0]
        relation_rows = conn.execute(
            "SELECT COUNT(*) FROM graph_relations"
        ).fetchone()[0]
    assert mention_rows == 2
    assert relation_rows == 1
    entity = graph.list_entities(kb.id)[0]
    assert entity.mention_count == 1


def test_delete_document_cascades_and_cleans_orphans(tmp_path):
    _, repo, graph = _db(tmp_path)
    kb = repo.create_kb(name="k", workspace_id="default")
    doc = _add_doc_with_chunk(repo, kb.id)
    graph.replace_document_graph(
        doc.id,
        kb.id,
        entities=[("web-api", "service", "网关")],
        relations=[],
        mentions=[("web-api", f"chk-{doc.id}")],
    )
    assert graph.count_entities(kb.id) == 1
    entity_ids = graph.entity_ids_for_document(doc.id)
    assert entity_ids == [graph.list_entities(kb.id)[0].id]

    repo.delete_document(doc.id)
    graph.cleanup_orphans(entity_ids)
    assert graph.count_entities(kb.id) == 0
    with repo.database.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM graph_mentions").fetchone()[0] == 0


def test_state_machine_attempt_guard(tmp_path):
    _, repo, graph = _db(tmp_path)
    kb = repo.create_kb(name="k", workspace_id="default")
    doc = _add_doc_with_chunk(repo, kb.id)

    graph.enqueue_document(doc.id, model="m", prompt_version="graph1")
    state = graph.claim_document(doc.id)
    assert state is not None and state.status == "processing"
    assert graph.claim_document(doc.id) is None

    assert graph.finish_document(
        doc.id, state.attempt, status="ready", entity_count=1
    )
    # 旧 attempt 不得覆盖新状态
    assert not graph.finish_document(doc.id, state.attempt - 1, status="ready")
    assert graph.get_state(doc.id).status == "ready"


def test_cleanup_relations_removes_evidence_starved_relation(tmp_path):
    _, repo, graph = _db(tmp_path)
    kb = repo.create_kb(name="k", workspace_id="default")
    first = _add_doc_with_chunk(repo, kb.id, "doc-1")
    second = _add_doc_with_chunk(repo, kb.id, "doc-2")
    for doc in (first, second):
        chunk_id = f"chk-{doc.id}"
        graph.replace_document_graph(
            doc.id,
            kb.id,
            entities=[("A", "concept", ""), ("B", "concept", "")],
            relations=[("A", "B", "依赖", "", 5.0)],
            mentions=[("A", chunk_id), ("B", chunk_id)],
        )
    # 两个文档都给关系提供了证据；删掉第一份后仍应保留。
    repo.delete_document(first.id)
    graph.cleanup_relations(kb.id)
    assert len(graph.list_relations(kb.id)) == 1
    # 再删最后一份，关系应被清理。
    repo.delete_document(second.id)
    graph.cleanup_relations(kb.id)
    assert graph.list_relations(kb.id) == []


def test_purge_kb_clears_derived_rows(tmp_path):
    _, repo, graph = _db(tmp_path)
    kb = repo.create_kb(name="k", workspace_id="default")
    doc = _add_doc_with_chunk(repo, kb.id)
    graph.replace_document_graph(
        doc.id,
        kb.id,
        entities=[("web-api", "service", "")],
        relations=[],
        mentions=[("web-api", f"chk-{doc.id}")],
    )
    graph.enqueue_document(doc.id, model="m", prompt_version="graph1")
    graph.purge_kb(kb.id)
    assert graph.count_entities(kb.id) == 0
    assert graph.get_state(doc.id) is None
