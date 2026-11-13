"""知识库运行期参数（WebUI 设置面板）单测。

覆盖：默认值读取、部分更新、参数校验、跨字段一致性、落盘回调、
热应用钩子、以及 ``knowledge.enabled=false`` 下的读写行为。
"""

from __future__ import annotations

from typing import Any

import pytest

from nanobot.config.schema import Config, KnowledgeConfig
from nanobot.webui import knowledge_routes as routes
from nanobot.webui.knowledge_api import KnowledgeApi, KnowledgeApiError
from nanobot.webui.knowledge_services import KnowledgeServices


class FakeService:
    def __init__(self) -> None:
        self.applied: list[tuple[Any, Any]] = []

    def apply_retrieval_settings(self, retrieval: Any, rerank: Any) -> None:
        self.applied.append((retrieval, rerank))


def _api(
    *,
    config: KnowledgeConfig | None = None,
    service: Any = None,
    saved: list[Any] | None = None,
) -> tuple[KnowledgeApi, FakeService, list[Any]]:
    fake = service or FakeService()
    writes: list[Any] = saved if saved is not None else []
    api = KnowledgeApi(
        KnowledgeServices(
            service=fake,
            config=config or Config().knowledge,
            config_writer=writes.append,
        )
    )
    return api, fake, writes


def test_settings_get_returns_defaults() -> None:
    api, _, _ = _api()
    payload = api.handle(routes.SETTINGS_GET)
    settings = payload["settings"]
    assert settings["retrieval"]["vectorWeight"] == 0.7
    assert settings["retrieval"]["keywordWeight"] == 0.3
    assert settings["retrieval"]["rrfK"] == 60
    assert settings["retrieval"]["defaultLimit"] == 8
    assert settings["rerank"]["enabled"] is False
    assert settings["chunking"]["chunkSize"] == 800
    assert settings["scope"]["retrieval"] == "live"
    assert settings["scope"]["chunking"] == "requires_reindex"


def test_settings_update_writes_and_applies() -> None:
    api, fake, writes = _api()
    payload = api.handle(
        routes.SETTINGS_UPDATE,
        {
            "retrieval": {
                "vectorWeight": 0.6,
                "keywordWeight": 0.4,
                "vectorThreshold": 0.2,
                "mmrLambda": 0.6,
            },
            "rerank": {"enabled": True, "threshold": 0.35, "model": "bge-reranker-v2-m3"},
        },
    )
    assert payload["applied"] is True
    assert payload["requiresReindex"] is False
    assert "retrieval.vector_weight" in payload["changed"]
    assert "rerank.threshold" in payload["changed"]
    assert payload["settings"]["retrieval"]["vectorWeight"] == 0.6
    assert payload["settings"]["rerank"]["enabled"] is True
    assert len(writes) == 1
    assert writes[0].retrieval.vector_weight == 0.6
    assert len(fake.applied) == 1
    assert fake.applied[0][0].keyword_weight == 0.4


def test_settings_update_without_fields_is_noop() -> None:
    api, fake, writes = _api()
    payload = api.handle(routes.SETTINGS_UPDATE, {})
    assert payload["changed"] == []
    assert payload["applied"] is False
    assert writes == []
    assert fake.applied == []


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"retrieval": {"vectorWeight": 1.5}}, "vectorWeight"),
        ({"retrieval": {"vectorWeight": "0.5"}}, "vectorWeight"),
        ({"retrieval": {"rrfK": 0}}, "rrfK"),
        ({"retrieval": {"defaultLimit": 30, "maxLimit": 10}}, "defaultLimit"),
        ({"retrieval": {"expandOnLowRecall": "yes"}}, "expandOnLowRecall"),
        ({"rerank": {"enabled": "true"}}, "enabled"),
        ({"retrieval": []}, "retrieval"),
    ],
)
def test_settings_update_rejects_invalid(payload: dict[str, Any], message: str) -> None:
    api, fake, writes = _api()
    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle(routes.SETTINGS_UPDATE, payload)
    assert message in str(excinfo.value)
    assert writes == []
    assert fake.applied == []


def test_settings_update_does_not_mutate_on_failure() -> None:
    config = Config().knowledge
    api, _, writes = _api(config=config)
    with pytest.raises(KnowledgeApiError):
        api.handle(
            routes.SETTINGS_UPDATE,
            {"retrieval": {"vectorWeight": 0.55, "rrfK": 0}},
        )
    assert config.retrieval.vector_weight == 0.7
    assert writes == []


def test_settings_disabled_reads_but_rejects_write() -> None:
    config = Config().knowledge
    config.enabled = False
    api, _, _ = _api(config=config)
    read = api.handle(routes.SETTINGS_GET)
    assert read["enabled"] is False
    with pytest.raises(KnowledgeApiError) as excinfo:
        api.handle(routes.SETTINGS_UPDATE, {"retrieval": {"vectorWeight": 0.5}})
    assert str(excinfo.value.code) == "KNOWLEDGE_DISABLED"
