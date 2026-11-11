from __future__ import annotations

import pytest
from pydantic import ValidationError

from nanobot.evaluation.knowledge.models import (
    ALLOWED_RETRIEVAL_FIELDS,
    KnowledgeRetrievalConfig,
)
from nanobot.evaluation.knowledge.runner import _retriever_settings


def test_graph_fields_are_whitelisted():
    assert {
        "graph_enabled",
        "graph_hops",
        "graph_seed_limit",
        "graph_weight",
        "graph_max_chunks",
    } <= ALLOWED_RETRIEVAL_FIELDS


def test_graph_hops_out_of_range_is_rejected():
    with pytest.raises(ValidationError):
        KnowledgeRetrievalConfig(graph_hops=3)


def test_graph_weight_out_of_range_is_rejected():
    with pytest.raises(ValidationError):
        KnowledgeRetrievalConfig(graph_weight=1.5)


def test_overrides_map_to_retriever_settings():
    settings = _retriever_settings(
        {
            "graph_enabled": True,
            "graph_hops": 2,
            "graph_seed_limit": 8,
            "graph_weight": 0.4,
            "graph_max_chunks": 80,
        }
    )
    assert settings.graph_enabled is True
    assert settings.graph_hops == 2
    assert settings.graph_seed_limit == 8
    assert settings.graph_weight == 0.4
    assert settings.graph_max_chunks == 80


def test_graph_defaults_stay_off():
    settings = _retriever_settings({})
    assert settings.graph_enabled is False
    assert settings.graph_hops == 1
    assert settings.graph_weight == 0.25
