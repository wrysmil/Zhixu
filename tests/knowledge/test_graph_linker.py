from __future__ import annotations

import pytest

from nanobot.knowledge.database import KnowledgeDatabase
from nanobot.knowledge.graph.linker import find_seed_entities
from nanobot.knowledge.graph.normalize import normalize_entity_name
from nanobot.knowledge.graph.repo import GraphRepository
from nanobot.knowledge.repository import KnowledgeRepository


def test_normalize_nfkc_casefold_whitespace():
    assert normalize_entity_name("  Ｗｅｂ   ＡＰＩ ") == "web api"


@pytest.fixture
def graph_repo_with_entities(tmp_path):
    db = KnowledgeDatabase(tmp_path / "state.db")
    db.init_schema()
    repo = KnowledgeRepository(db)
    graph = GraphRepository(db)
    kb = repo.create_kb(name="k", workspace_id="default")
    documents = []
    chunks: list[tuple[str, str]] = []
    for index in range(9):
        doc = repo.insert_document(
            doc_id=f"doc-{index}",
            kb_id=kb.id,
            workspace_id="default",
            title=f"d{index}",
            source_kind="external",
            source_path=f"/tmp/d{index}.md",
            original_name=f"d{index}.md",
            file_hash=f"h{index}",
            file_size=1,
        )
        documents.append(doc)
        chunk_id = f"chk-{index}"
        chunks.append((doc.id, chunk_id))
        with db.connect(immediate=True) as conn:
            conn.execute(
                "INSERT INTO document_chunks"
                " (id,doc_id,kb_id,workspace_id,chunk_index,content,created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (chunk_id, doc.id, kb.id, "default", 0, "x", "now"),
            )
    graph.replace_document_graph(
        documents[0].id,
        kb.id,
        entities=[
            ("灰度发布", "concept", ""),
            ("灰度发布流程", "concept", ""),
            ("值班二号", "person", ""),
            ("孤立实体", "concept", ""),
        ],
        relations=[
            ("灰度发布", "值班二号", "执行人", "", 7.0),
            ("灰度发布流程", "灰度发布", "包含", "", 6.0),
        ],
        mentions=[
            *[("灰度发布", chunk_id) for _, chunk_id in chunks],
            *[("灰度发布流程", chunk_id) for _, chunk_id in chunks[:3]],
            ("值班二号", chunks[0][1]),
            ("孤立实体", chunks[1][1]),
        ],
    )
    return graph, kb.id


def test_linker_exact_beats_partial(graph_repo_with_entities):
    graph, kb_id = graph_repo_with_entities
    found = find_seed_entities(graph, kb_id, "灰度发布", limit=5)
    assert found[0].name == "灰度发布"
    assert [entity.name for entity in found[:2]] == ["灰度发布", "灰度发布流程"]


def test_linker_chinese_bigram_hit(graph_repo_with_entities):
    graph, kb_id = graph_repo_with_entities
    found = find_seed_entities(graph, kb_id, "灰度流程", limit=5)
    assert any(entity.name == "灰度发布流程" for entity in found)


def test_linker_respects_limit(graph_repo_with_entities):
    graph, kb_id = graph_repo_with_entities
    assert len(find_seed_entities(graph, kb_id, "灰度", limit=1)) == 1


def test_linker_skips_relationless_entities(graph_repo_with_entities):
    # WeKnora 口径：无边实体不做种子，否则占名额却不贡献任何关系。
    graph, kb_id = graph_repo_with_entities
    assert find_seed_entities(graph, kb_id, "孤立实体", limit=5) == []
