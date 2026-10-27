"""Regression coverage for memory presentation, combined filters and ownership."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Episode, EpisodeSource, Memory, MemoryPriority
from nanobot.memory.provenance import workspace_from_path
from nanobot.memory.repository import add_episode, add_memory, get_memory
from nanobot.webui import memory_api
from nanobot.webui.memory_services import MemoryServices


@pytest.fixture
def services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryServices:
    database = MemoryDatabase(tmp_path)
    database.ensure_schema()
    monkeypatch.setattr(memory_api, "index_memory_best_effort", lambda _: None)
    monkeypatch.setattr(memory_api, "remove_memory_best_effort", lambda _: None)
    monkeypatch.setattr(memory_api, "_refresh_memory_md_after_mutation", lambda *_: None)
    return MemoryServices("default", database)


def memory(identifier: str, *, source: str = "extraction", **kwargs: object) -> Memory:
    values = {
        "id": identifier,
        "content": "Python 项目约定",
        "created_at": "2026-10-27T10:00:00+00:00",
        "updated_at": "2026-10-27T11:00:00+00:00",
        "source": source,
        **kwargs,
    }
    return Memory(**values)  # type: ignore[arg-type]


def seed(services: MemoryServices, *items: Memory | Episode) -> None:
    with services.database.connect() as conn:
        for item in items:
            if isinstance(item, Memory):
                add_memory(conn, item)
            else:
                add_episode(conn, item)


def test_filters_compose_before_limit_and_counts_ignore_origin(services: MemoryServices):
    seed(
        services,
        memory("auto"),
        memory("manual-ai", metadata={"provenance": {"trigger_mode": "manual"}}),
        memory("manual", source="manual"),
        memory("other-query", content="Rust 项目"),
        memory("short", priority=MemoryPriority.SHORT_TERM),
    )
    result = memory_api.list_memories_payload(
        services, q="Python", priority="long_term", origin="manual", limit=1,
    )
    assert len(result["items"]) == 1
    assert result["total"] == 2
    assert result["source_counts"] == {"all": 3, "ai": 1, "manual": 2, "unknown": 0}
    assert result["scope"] == "current_store"
    assert result["items"][0]["origin_kind"] == "manual"


def test_workspaces_recover_only_from_saved_session_metadata_and_cache(services: MemoryServices):
    calls: list[str] = []

    def metadata(key: str) -> dict[str, object]:
        calls.append(key)
        return {"metadata": {"workspace_scope": {"project_path": "D:/projects/zhixu"}}}

    scoped = replace(services, session_metadata=metadata)
    seed(
        scoped,
        Episode("ep", "websocket:chat", "完成任务", "2026-10-27", "2026-10-27"),
        memory("a", source_episode_id="ep"),
        memory("b", source_episode_id="ep"),
        memory("unknown"),
    )
    result = memory_api.list_memories_payload(scoped)
    workspace = next(item for item in result["items"] if item["id"] == "a")["workspaces"][0]
    assert workspace["name"] == "zhixu"
    assert workspace in result["workspace_options"]
    assert calls == ["websocket:chat"]
    selected = memory_api.list_memories_payload(scoped, workspace=workspace["id"])
    assert {item["id"] for item in selected["items"]} == {"a", "b"}
    missing = memory_api.list_memories_payload(scoped, workspace="unassigned")
    assert [item["id"] for item in missing["items"]] == ["unknown"]


def test_no_session_scope_does_not_fabricate_default_project(services: MemoryServices):
    scoped = replace(services, session_metadata=lambda _: {"metadata": {}})
    seed(scoped, Episode("ep", "old-chat", "旧对话", "2026-10-27", "2026-10-27"))
    result = memory_api.list_episodes_payload(scoped)
    assert result["items"][0]["workspaces"] == []


def test_episode_filters_search_goal_and_include_human_ai_provenance(services: MemoryServices):
    seed(
        services,
        Episode(
            "auto", "chat-a", "summary", "2026-10-27", "2026-10-27",
            goal="Python 改造", source=EpisodeSource.IDLE,
        ),
        Episode(
            "manual", "chat-b", "Python", "2026-10-27", "2026-10-27",
            metadata={"provenance": {"trigger_mode": "manual", "generation_method": "ai"}},
        ),
    )
    result = memory_api.list_episodes_payload(services, q="Python", origin="manual")
    assert [item["id"] for item in result["items"]] == ["manual"]
    assert result["source_counts"]["ai"] == 1
    assert result["items"][0]["generation_method"] == "ai"


@pytest.mark.parametrize("filters", [
    {"origin": "made-up"}, {"workspace": "D:/arbitrary-path"},
    {"priority": "forever"}, {"order": "random"}, {"limit": 0},
])
def test_invalid_filter_rejected(services: MemoryServices, filters: dict[str, object]):
    with pytest.raises(memory_api.WebUIMemoryError):
        memory_api.list_memories_payload(services, **filters)  # type: ignore[arg-type]


def test_manual_create_default_workspace_and_edit_preserves_provenance(services: MemoryServices):
    created = memory_api.create_memory(services, content="需要记住")
    row = created["memory"]
    assert row["trigger_mode"] == "manual"
    assert row["workspaces"][0]["path"] == str(services.database.workspace)
    updated = memory_api.update_memory(
        services, row["id"], content="编辑后的事实", metadata={"provenance": {"trigger_mode": "automatic"}},
    )
    assert updated["memory"]["trigger_mode"] == "manual"
    assert updated["memory"]["workspaces"] == row["workspaces"]
    unassigned = memory_api.create_memory(services, content="无工作区", workspace="unassigned")
    assert unassigned["memory"]["workspaces"] == []


def test_memory_get_update_delete_are_store_scoped(services: MemoryServices):
    seed(services, memory("foreign", workspace_id="another-store"))
    for action in (
        lambda: memory_api.fetch_memory_payload(services, "foreign"),
        lambda: memory_api.update_memory(services, "foreign", content="bad"),
        lambda: memory_api.delete_memory(services, "foreign"),
    ):
        with pytest.raises(memory_api.WebUIMemoryError) as exc:
            action()
        assert exc.value.status == 404
    with services.database.connect() as conn:
        assert get_memory(conn, "foreign").content == "Python 项目约定"


def test_search_combines_priority_origin_and_keeps_unknown_source(services: MemoryServices):
    seed(services, memory("known"), memory("unknown", source="custom-source"))
    result = memory_api.search_memories_payload(services, "Python", origin="unknown")
    assert [item["id"] for item in result["items"]] == ["unknown"]
    assert result["items"][0]["trigger_mode"] == "unknown"


def test_saved_project_snapshot_wins_over_changed_session(services: MemoryServices):
    scoped = replace(services, session_metadata=lambda _: {
        "metadata": {"workspace_scope": {"project_path": "D:/projects/new"}},
    })
    seed(scoped, memory("old", metadata={"provenance": {
        "session_id": "chat", "workspaces": [{"path": "D:/projects/original"}],
    }}))
    result = memory_api.fetch_memory_payload(scoped, "old")
    assert result["memory"]["workspaces"][0]["name"] == "original"


def test_invalid_persisted_provenance_is_displayed_as_unknown_not_crash(services: MemoryServices):
    seed(services, memory("bad", source="unrecognized", metadata={"provenance": {
        "generation_method": {}, "trigger_mode": [],
        "workspaces": [{"path": "relative/path"}, {"path": None}],
    }}))
    result = memory_api.fetch_memory_payload(services, "bad")["memory"]
    assert result["origin_kind"] == "unknown"
    assert result["workspaces"] == []


def test_episode_owner_checked_for_all_record_operations(services: MemoryServices):
    seed(services, Episode(
        "foreign-episode", "chat", "untouched", "2026-10-27", "2026-10-27",
        metadata={"provenance": {"store_workspace_id": "foreign-store"}},
    ))
    assert memory_api.list_episodes_payload(services)["items"] == []
    for action in (
        lambda: memory_api.fetch_episode_payload(services, "foreign-episode"),
        lambda: memory_api.update_episode(services, "foreign-episode", summary="changed"),
        lambda: memory_api.delete_episode(services, "foreign-episode"),
    ):
        with pytest.raises(memory_api.WebUIMemoryError) as exc:
            action()
        assert exc.value.status == 404


def test_create_rejects_unregistered_project_id(services: MemoryServices):
    with pytest.raises(memory_api.WebUIMemoryError):
        memory_api.create_memory(services, content="no", workspace="D:/not-registered")


def test_created_and_updated_orders_compare_real_times(services: MemoryServices):
    seed(
        services,
        memory("earlier", created_at="2026-10-27T12:00:00+02:00", updated_at="2026-10-27T20:00:00+09:00"),
        memory("later", created_at="2026-10-27T11:00:00Z", updated_at="2026-10-27T12:00:00Z"),
    )
    for order in ("created", "updated"):
        result = memory_api.list_memories_payload(services, order=order)
        assert result["items"][0]["id"] == "later"


@pytest.mark.parametrize("search_endpoint", [False, True])
def test_importance_order_preserves_combined_filters_and_counts(
    services: MemoryServices, search_endpoint: bool,
):
    workspace = workspace_from_path("D:/projects/selected")
    provenance = {"provenance": {"workspaces": [workspace]}}
    other = {"provenance": {"workspaces": [{"path": "D:/projects/other"}]}}
    seed(
        services,
        memory("low", importance_score=0.1, metadata=provenance),
        memory("high", importance_score=0.95, metadata=provenance),
        memory("middle", importance_score=0.72, metadata=provenance),
        memory("manual", source="manual", importance_score=1.0, metadata=provenance),
        memory("other-project", importance_score=1.0, metadata=other),
        memory("other-query", content="Rust", importance_score=1.0, metadata=provenance),
    )
    filters = {
        "order": "importance", "origin": "ai", "workspace": workspace["id"],
        "priority": "long_term", "limit": 2,
    }
    if search_endpoint:
        result = memory_api.search_memories_payload(services, "Python", **filters)
    else:
        result = memory_api.list_memories_payload(services, q="Python", **filters)
    assert [item["id"] for item in result["items"]] == ["high", "middle"]
    assert result["total"] == 3
    assert result["source_counts"] == {"all": 4, "ai": 3, "manual": 1, "unknown": 0}


def test_episode_importance_sort_works_with_combined_filters(services: MemoryServices):
    workspace = workspace_from_path("D:/projects/selected")
    provenance = {"provenance": {"workspaces": [workspace]}}
    seed(
        services,
        Episode(
            "low", "chat-a", "Python", "2026-10-27", "2026-10-27",
            importance_score=0.12, metadata=provenance,
        ),
        Episode(
            "high", "chat-b", "Python", "2026-10-26", "2026-10-26",
            importance_score=0.91, metadata=provenance,
        ),
        Episode(
            "manual", "chat-c", "Python", "2026-10-27", "2026-10-27",
            importance_score=1.0, metadata={"provenance": {
                "workspaces": [workspace], "trigger_mode": "manual",
            }},
        ),
        Episode(
            "wrong-query", "chat-d", "Rust", "2026-10-27", "2026-10-27",
            importance_score=1.0, metadata=provenance,
        ),
    )
    result = memory_api.list_episodes_payload(
        services, order="importance", origin="ai", workspace=workspace["id"], q="Python", limit=1,
    )
    assert [item["id"] for item in result["items"]] == ["high"]
    assert result["total"] == 2
    assert result["source_counts"] == {"all": 3, "ai": 2, "manual": 1, "unknown": 0}


def test_episode_rejects_unrecognized_sort(services: MemoryServices):
    with pytest.raises(memory_api.WebUIMemoryError):
        memory_api.list_episodes_payload(services, order="random")
