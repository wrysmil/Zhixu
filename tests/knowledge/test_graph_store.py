from __future__ import annotations

import os

import pytest

from nanobot.knowledge.graph.models import (
    GraphEntity,
    GraphHealth,
    GraphRelation,
)
from nanobot.knowledge.graph.neo4j_store import Neo4jGraphStore

from .graph_fakes import FakeGraphStore


def test_fake_store_health_is_ready():
    assert FakeGraphStore().health() == GraphHealth("ready")


def test_fake_store_reports_unavailable():
    store = FakeGraphStore()
    store.available = False
    assert store.health() == GraphHealth("degraded", "neo4j_unavailable")


@pytest.mark.neo4j
@pytest.mark.skipif(not os.environ.get("NEO4J_TEST_URI"), reason="no neo4j")
def test_neo4j_upsert_and_expand():
    store = Neo4jGraphStore(
        uri=os.environ["NEO4J_TEST_URI"],
        username=os.environ.get("NEO4J_TEST_USER", "neo4j"),
        password=os.environ.get("NEO4J_TEST_PASSWORD", "password"),
        database=os.environ.get("NEO4J_TEST_DATABASE", "neo4j"),
        timeout=5.0,
    )
    store.ensure_schema()
    kb = "kb_test"
    store.delete_kb(kb)
    store.upsert_subgraph(
        kb,
        [
            GraphEntity("e1", kb, "A", "a", "concept", "", 1, ("c1",)),
            GraphEntity("e2", kb, "B", "b", "concept", "", 1, ("c2",)),
            GraphEntity("e3", kb, "C", "c", "concept", "", 1, ("c3",)),
        ],
        [
            GraphRelation("r1", kb, "e1", "e2", "依赖", "", 7.0, 1),
            GraphRelation("r2", kb, "e2", "e3", "依赖", "", 5.0, 1),
            GraphRelation("r3", kb, "e2", "e1", "回指", "", 6.0, 1),
        ],
    )
    one_hop = store.expand(kb, ["e1"], hops=1, max_entities=20, max_chunks=50)
    assert "e2" in one_hop.entity_ids
    assert "e3" not in one_hop.entity_ids
    assert {"c1", "c2"} <= set(one_hop.chunk_ids)

    induced = store.expand(
        kb, ["e1"], hops=1, max_entities=20, max_chunks=50,
        include_inner_edges=True,
    )
    triples = {
        (edge.source_entity_id, edge.target_entity_id, edge.relation)
        for edge in induced.edges
    }
    assert ("e1", "e2", "依赖") in triples
    assert ("e2", "e1", "回指") in triples

    two_hop = store.expand(kb, ["e1"], hops=2, max_entities=20, max_chunks=50)
    assert {"e2", "e3"} <= set(two_hop.entity_ids)
    assert {"c1", "c2", "c3"} <= set(two_hop.chunk_ids)

    # 种子是入边的终点时也必须能扩展（WeKnora 用无向 (n)-[r]-(m)）。
    incoming = store.expand(kb, ["e3"], hops=1, max_entities=20, max_chunks=50)
    assert "e2" in incoming.entity_ids
    assert {"c2", "c3"} <= set(incoming.chunk_ids)
    assert any(
        edge.source_entity_id == "e2"
        and edge.target_entity_id == "e3"
        and edge.relation == "依赖"
        for edge in incoming.edges
    )
