from __future__ import annotations

import pytest
from pydantic import ValidationError

from nanobot.config.schema import (
    KnowledgeConfig,
    KnowledgeGraphConfig,
    KnowledgeGraphExtractionConfig,
    KnowledgeGraphPromptsConfig,
)


def test_graph_defaults_are_enabled():
    config = KnowledgeConfig()
    assert isinstance(config.graph, KnowledgeGraphConfig)
    assert config.graph.enabled is True
    assert config.graph.backend == "neo4j"
    assert isinstance(config.graph.extraction, KnowledgeGraphExtractionConfig)
    assert isinstance(config.graph.prompts, KnowledgeGraphPromptsConfig)
    assert config.graph.extraction.thinking == "disabled"
    assert "<<content>>" in config.graph.prompts.extraction
    assert config.graph.prompts.query_entities
    assert "<<query>>" in config.graph.prompts.query_user
    assert config.graph.extraction.prompt_version == "graph1"
    assert config.graph.expansion.hops == 1
    assert config.graph.expansion.max_chunks == 30
    assert config.graph.expansion.query_entity_extraction is True
    assert config.graph.expansion.query_extraction_timeout_seconds == 5.0


def test_graph_camel_case_roundtrip():
    config = KnowledgeConfig.model_validate(
        {
            "graph": {
                "enabled": True,
                "uri": "bolt://localhost:7687",
                "extraction": {"concurrency": 4, "maxChunksPerDoc": 50},
                "expansion": {
                    "seedLimit": 8,
                    "maxChunks": 40,
                    "queryEntityExtraction": False,
                    "queryExtractionTimeoutSeconds": 2.5,
                },
            }
        }
    )
    assert config.graph.extraction.concurrency == 4
    assert config.graph.extraction.max_chunks_per_doc == 50
    assert config.graph.expansion.seed_limit == 8
    assert config.graph.expansion.max_chunks == 40
    assert config.graph.expansion.query_entity_extraction is False
    assert config.graph.expansion.query_extraction_timeout_seconds == 2.5
    dumped = config.model_dump(by_alias=True)
    assert dumped["graph"]["expansion"]["seedLimit"] == 8
    assert dumped["graph"]["expansion"]["queryEntityExtraction"] is False


def test_graph_password_not_in_repr():
    config = KnowledgeGraphConfig(password="secret-value")
    assert "secret-value" not in repr(config)


def test_graph_prompts_camel_case_roundtrip():
    config = KnowledgeConfig.model_validate(
        {
            "graph": {
                "prompts": {
                    "extraction": "标题 <<heading>> 正文 <<content>>",
                    "queryEntities": "SYS",
                    "queryUser": "Q: <<query>>",
                }
            }
        }
    )
    assert config.graph.prompts.extraction == "标题 <<heading>> 正文 <<content>>"
    assert config.graph.prompts.query_entities == "SYS"
    dumped = config.model_dump(by_alias=True)
    assert dumped["graph"]["prompts"]["queryEntities"] == "SYS"


def test_graph_prompts_require_placeholders():
    with pytest.raises(ValidationError):
        KnowledgeConfig.model_validate(
            {"graph": {"prompts": {"extraction": "没有正文占位符"}}}
        )
    with pytest.raises(ValidationError):
        KnowledgeConfig.model_validate(
            {"graph": {"prompts": {"queryUser": "没有 query 占位符"}}}
        )
