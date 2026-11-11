from __future__ import annotations

from nanobot.config.schema import (
    KnowledgeConfig,
    KnowledgeGraphConfig,
    KnowledgeGraphExtractionConfig,
)


def test_graph_defaults_are_disabled():
    config = KnowledgeConfig()
    assert isinstance(config.graph, KnowledgeGraphConfig)
    assert config.graph.enabled is False
    assert config.graph.backend == "neo4j"
    assert isinstance(config.graph.extraction, KnowledgeGraphExtractionConfig)
    assert config.graph.extraction.prompt_version == "graph1"
    assert config.graph.expansion.hops == 1
    assert config.graph.expansion.weight == 0.25


def test_graph_camel_case_roundtrip():
    config = KnowledgeConfig.model_validate(
        {
            "graph": {
                "enabled": True,
                "uri": "bolt://localhost:7687",
                "extraction": {"concurrency": 4, "maxChunksPerDoc": 50},
                "expansion": {"seedLimit": 8, "weight": 0.4},
            }
        }
    )
    assert config.graph.extraction.concurrency == 4
    assert config.graph.extraction.max_chunks_per_doc == 50
    assert config.graph.expansion.seed_limit == 8
    dumped = config.model_dump(by_alias=True)
    assert dumped["graph"]["expansion"]["seedLimit"] == 8


def test_graph_password_not_in_repr():
    config = KnowledgeGraphConfig(password="secret-value")
    assert "secret-value" not in repr(config)
