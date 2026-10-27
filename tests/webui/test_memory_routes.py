"""Integration tests for the memory settings domain handler.

These exercise ``MemorySettingsHandler.handle`` directly with real operations
backed by a temporary ``MemoryDatabase``: reads go through ``list_memories`` /
type in ``memory_api`` and mutations through ``create_memory`` / etc.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Episode, EpisodeOutcome, EpisodeSource
from nanobot.memory.repository import add_episode
from nanobot.webui import memory_api
from nanobot.webui.memory_routes import (
    MemorySettingsHandler,
    MemorySettingsOperations,
)
from nanobot.webui.memory_services import MemoryServices
from nanobot.webui.settings_contracts import SettingsRequest, SettingsRouteResult


@pytest.fixture
def services(tmp_path: Path) -> MemoryServices:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return MemoryServices(workspace_id="default", database=database)


@pytest.fixture
def operations(services: MemoryServices) -> MemorySettingsOperations:
    return MemorySettingsOperations(
        list_memories=partial(memory_api.list_memories_payload, services),
        search_memories=partial(memory_api.search_memories_payload, services),
        fetch_memory=partial(memory_api.fetch_memory_payload, services),
        list_episodes=partial(memory_api.list_episodes_payload, services),
        fetch_episode=partial(memory_api.fetch_episode_payload, services),
        fetch_scratchpad=partial(memory_api.scratchpad_payload, services),
        fetch_stats=partial(memory_api.stats_payload, services),
        create_memory=partial(memory_api.create_memory, services),
        update_memory=partial(memory_api.update_memory, services),
        delete_memory=partial(memory_api.delete_memory, services),
        update_episode=partial(memory_api.update_episode, services),
        delete_episode=partial(memory_api.delete_episode, services),
        save_scratchpad=partial(memory_api.save_scratchpad, services),
        # reindex/sync 不注入依赖：走 memory_api 内部进程级 fallback，与生产同构。
        reindex_vector=partial(memory_api.reindex_vector, services),
        sync_vector=partial(memory_api.sync_vector, services),
        refresh_memory_md=partial(memory_api.refresh_memory_md, services),  # 🆕 WU-4
        get_memory_md_content=partial(memory_api.get_memory_md_content, services),  # 🆕 WU-6
    )


@pytest.fixture
def handler(operations: MemorySettingsOperations) -> MemorySettingsHandler:
    return MemorySettingsHandler(operations)


def _request(
    query: Mapping[str, list[str]] | None = None,
    payload: dict[str, Any] | None = None,
) -> SettingsRequest:
    return SettingsRequest(query=dict(query or {}), payload=payload)


def _seed_episode(services: MemoryServices, session_id: str = "telegram:chat-1") -> str:
    episode_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with services.database.connect() as conn:
        add_episode(
            conn,
            Episode(
                id=episode_id,
                session_id=session_id,
                summary="配置 uv",
                started_at=now,
                ended_at=now,
                goal="让 Agent 管理依赖",
                outcome=EpisodeOutcome.COMPLETED,
                source=EpisodeSource.SESSION_END,
            ),
        )
    return episode_id


# ---- reads ------------------------------------------------------------------


def test_handle_memory_list_returns_items(handler, operations):
    mid = operations.create_memory(content="hi", type="fact")["memory"]["id"]
    result = handler.handle("memory-list", _request())
    assert isinstance(result, SettingsRouteResult)
    assert result.error is None
    assert any(item["id"] == mid for item in result.payload["items"])


def test_handle_memory_search_accepts_q(handler, operations):
    operations.create_memory(content="Python 偏好", type="fact")
    result = handler.handle("memory-search", _request(query={"q": ["Python"]}))
    assert result.error is None
    assert result.payload["query"] == "Python"
    assert len(result.payload["items"]) >= 1


def test_handle_memory_search_rejects_empty_q(handler):
    result = handler.handle("memory-search", _request(query={"q": [""]}))
    assert result.status == 400
    assert result.error


def test_handle_memory_get_unknown_id_returns_404(handler):
    result = handler.handle("memory-get", _request(query={"id": ["missing"]}))
    assert result.status == 404


def test_handle_memory_fetch_episode(handler, services):
    ep_id = _seed_episode(services)
    result = handler.handle("episode-get", _request(query={"id": [ep_id]}))
    assert result.error is None
    assert result.payload["episode"]["id"] == ep_id


def test_handle_memory_stats(handler, operations):
    operations.create_memory(content="a", type="fact")
    operations.create_memory(content="b", type="preference")
    result = handler.handle("memory-stats", _request())
    assert result.payload["total"] == 2
    assert result.payload["by_type"]["fact"] == 1
    assert result.payload["by_type"]["preference"] == 1


# ---- mutations --------------------------------------------------------------


def test_handle_memory_create_via_payload(handler):
    result = handler.handle(
        "memory-create",
        _request(payload={"content": "新", "type": "rule", "priority": "long_term"}),
    )
    assert result.error is None
    assert result.payload["memory"]["content"] == "新"
    assert result.payload["memory"]["source"] == "manual"


def test_handle_memory_create_rejects_invalid_type(handler):
    result = handler.handle(
        "memory-create",
        _request(payload={"content": "x", "type": "garbage"}),
    )
    assert result.status == 400


def test_handle_memory_update_with_payload(handler, operations):
    mem_id = operations.create_memory(content="原", type="fact")["memory"]["id"]
    result = handler.handle(
        "memory-update",
        _request(query={"id": [mem_id]}, payload={"content": "新", "importance_score": 0.9}),
    )
    assert result.error is None
    assert result.payload["memory"]["content"] == "新"
    assert abs(result.payload["memory"]["importance_score"] - 0.9) < 1e-9


def test_handle_memory_delete_via_query(handler, operations):
    mem_id = operations.create_memory(content="a", type="fact")["memory"]["id"]
    result = handler.handle("memory-delete", _request(query={"id": [mem_id]}))
    assert result.error is None
    assert result.payload["ok"] is True


def test_handle_scratchpad_save_round_trip(handler):
    save = handler.handle(
        "scratchpad-save",
        _request(
            payload={
                "content": "## 当前项目",
                "active_projects": ["a"],
                "current_focus": "f",
                "open_questions": ["q"],
                "next_steps": ["n"],
            }
        ),
    )
    assert save.error is None
    fetched = handler.handle("scratchpad-get", _request())
    assert fetched.payload["scratchpad"]["content"] == "## 当前项目"


def test_handle_memory_unknown_action_returns_404(handler):
    result = handler.handle("memory-frobnicate", _request())
    assert result.status == 404


def test_handle_memory_list_combines_all_query_filters(handler, operations):
    created = operations.create_memory(content="Python 规则", type="rule")
    project = created["memory"]["workspaces"][0]["id"]
    operations.create_memory(content="Rust 规则", type="rule")
    result = handler.handle("memory-list", _request(query={
        "q": ["Python"], "origin": ["manual"], "workspace": [project],
        "priority": ["long_term"], "type": ["rule"], "order": ["updated"],
    }))
    assert result.status == 200
    assert result.payload["total"] == 1
    assert result.payload["items"][0]["id"] == created["memory"]["id"]


def test_handle_memory_filters_reject_arbitrary_workspace_path(handler):
    result = handler.handle("memory-list", _request(query={"workspace": ["D:/private"]}))
    assert result.status == 400


def test_handle_memory_create_allows_explicit_unassigned_workspace(handler):
    result = handler.handle("memory-create", _request(payload={
        "content": "不属于项目的记忆", "workspace": "unassigned",
    }))
    assert result.status == 200
    assert result.payload["memory"]["workspaces"] == []
