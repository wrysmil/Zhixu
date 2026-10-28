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
    project = workspace_from_path("D:/projects/zhixu")

    def metadata(key: str) -> dict[str, object]:
        calls.append(key)
        return {"metadata": {"workspace_scope": {"project_path": "D:/projects/zhixu"}}}

    scoped = replace(services, session_metadata=metadata)
    seed(
        scoped,
        Episode("ep", "websocket:chat", "完成任务", "2026-10-27", "2026-10-27"),
        memory("a", source_episode_id="ep", workspace_id=project["id"]),
        memory("b", source_episode_id="ep", workspace_id=project["id"]),
        memory("unknown"),
    )
    result = memory_api.list_memories_payload(scoped)
    workspace = next(item for item in result["items"] if item["id"] == "a")["workspaces"][0]
    assert workspace["name"] == "zhixu"
    assert workspace in result["workspace_options"]
    assert calls == ["websocket:chat"]
    selected = memory_api.list_memories_payload(scoped, workspace=workspace["id"])
    assert {item["id"] for item in selected["items"]} == {"a", "b"}
    missing = memory_api.list_memories_payload(scoped, workspace="default")
    assert [item["id"] for item in missing["items"]] == ["unknown"]


def test_memory_payload_exposes_source_session(services: MemoryServices):
    """记忆条目顶层带来源会话与可读标题；取不到标题时为 None。"""
    titles = {"websocket:deploy": "部署流程讨论"}

    def metadata(key: str) -> dict[str, object] | None:
        if key == "websocket:handle-only":
            return {"key": key, "created_at": None, "updated_at": None,
                    "metadata": {"session_handle": "kevu"}}
        if key not in titles:
            return None
        # SessionMetadataPayload: 标题在 metadata.title，顶层没有 title
        return {"key": key, "created_at": None, "updated_at": None,
                "metadata": {"title": titles[key]}}

    scoped = replace(services, session_metadata=metadata)
    seed(
        scoped,
        memory("m1", metadata={"provenance": {"session_id": "websocket:deploy"}}),
        memory("m2", metadata={"provenance": {"session_id": "websocket:deleted"}}),
        memory("m3"),
        memory("m4", metadata={"provenance": {"session_id": "websocket:handle-only"}}),
    )

    items = {item["id"]: item for item in memory_api.list_memories_payload(scoped)["items"]}

    assert items["m1"]["session_id"] == "websocket:deploy"
    assert items["m1"]["session_title"] == "部署流程讨论"
    assert items["m2"]["session_id"] == "websocket:deleted"
    assert items["m2"]["session_title"] is None
    assert items["m3"]["session_id"] is None
    assert items["m3"]["session_title"] is None
    assert items["m4"]["session_title"] == "kevu"


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


def test_manual_create_global_workspace_and_edit_preserves_provenance(services: MemoryServices):
    created = memory_api.create_memory(services, content="需要记住")
    row = created["memory"]
    assert row["trigger_mode"] == "manual"
    assert row["workspace_id"] == "default"
    assert row["workspaces"] == []
    updated = memory_api.update_memory(
        services, row["id"], content="编辑后的事实", metadata={"provenance": {"trigger_mode": "automatic"}},
    )
    assert updated["memory"]["trigger_mode"] == "manual"
    assert updated["memory"]["workspaces"] == row["workspaces"]
    unassigned = memory_api.create_memory(services, content="无工作区", workspace="unassigned")
    assert unassigned["memory"]["workspaces"] == []


def test_memory_get_update_delete_accept_all_rows_in_database(services: MemoryServices):
    seed(services, memory("project", workspace_id="another-workspace"))
    assert memory_api.fetch_memory_payload(services, "project")["memory"]["workspace_id"] == "another-workspace"
    updated = memory_api.update_memory(services, "project", content="changed")["memory"]
    assert updated["workspace_id"] == "another-workspace"
    assert updated["content"] == "changed"
    assert memory_api.delete_memory(services, "project") == {"ok": True}
    with services.database.connect() as conn:
        assert get_memory(conn, "project") is None


def test_search_combines_priority_origin_and_keeps_unknown_source(services: MemoryServices):
    seed(services, memory("known"), memory("unknown", source="custom-source"))
    result = memory_api.search_memories_payload(services, "Python", origin="unknown")
    assert [item["id"] for item in result["items"]] == ["unknown"]
    assert result["items"][0]["trigger_mode"] == "unknown"


def test_saved_project_snapshot_wins_over_changed_session(services: MemoryServices):
    """已保存的项目快照优先于会话当前作用域（会话后来换了项目）。"""
    scoped = replace(services, session_metadata=lambda _: {
        "metadata": {"workspace_scope": {"project_path": "D:/projects/new"}},
    })
    original_id = workspace_from_path("D:/projects/original")["id"]
    seed(scoped, memory("old", workspace_id=original_id, metadata={"provenance": {
        "session_id": "chat", "workspaces": [{"path": "D:/projects/original"}],
    }}))
    result = memory_api.fetch_memory_payload(scoped, "old")
    assert result["memory"]["workspaces"][0]["name"] == "original"


def test_ownership_follows_column_not_snapshot(services: MemoryServices):
    """归属以行的 ``workspace_id`` 列为准。

    历史数据可能「列是全局、快照指向项目」：这类记忆实际全局可见，若按快照
    显示成项目记忆就是误导（也会让旧快照 id 进筛选下拉却筛不出东西）。
    """
    seed(services, memory("legacy", metadata={"provenance": {
        "session_id": "chat", "workspaces": [{"path": "D:/projects/original"}],
    }}))
    result = memory_api.fetch_memory_payload(services, "legacy")
    assert result["memory"]["workspace_id"] == "default"
    assert result["memory"]["workspaces"] == []


def test_ownership_without_snapshot_falls_back_to_column_placeholder(
    services: MemoryServices,
) -> None:
    """非全局行没有快照时，用归属列派生一个占位归属。"""
    seed(services, memory("orphan", workspace_id="ws-a"))
    result = memory_api.fetch_memory_payload(services, "orphan")
    workspaces = result["memory"]["workspaces"]
    assert [item["id"] for item in workspaces] == ["ws-a"]
    assert workspaces[0]["name"].startswith("未登记工作区")


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
        memory("low", importance_score=0.1, metadata=provenance, workspace_id=workspace["id"]),
        memory("high", importance_score=0.95, metadata=provenance, workspace_id=workspace["id"]),
        memory("middle", importance_score=0.72, metadata=provenance, workspace_id=workspace["id"]),
        memory("manual", source="manual", importance_score=1.0, metadata=provenance, workspace_id=workspace["id"]),
        memory("other-project", importance_score=1.0, metadata=other),
        memory("other-query", content="Rust", importance_score=1.0, metadata=provenance, workspace_id=workspace["id"]),
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
            importance_score=0.12, metadata=provenance, workspace_id=workspace["id"],
        ),
        Episode(
            "high", "chat-b", "Python", "2026-10-26", "2026-10-26",
            importance_score=0.91, metadata=provenance, workspace_id=workspace["id"],
        ),
        Episode(
            "manual", "chat-c", "Python", "2026-10-27", "2026-10-27",
            importance_score=1.0, workspace_id=workspace["id"], metadata={"provenance": {
                "workspaces": [workspace], "trigger_mode": "manual",
            }},
        ),
        Episode(
            "wrong-query", "chat-d", "Rust", "2026-10-27", "2026-10-27",
            importance_score=1.0, metadata=provenance, workspace_id=workspace["id"],
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


def test_workspace_options_keep_global_first_and_include_ids_without_snapshots(services: MemoryServices):
    snapshot = {"id": "stable-id", "name": "AAA 项目", "path": "/projects/stable"}
    seed(services,
         memory("known", workspace_id="stable-id", metadata={"provenance": {"workspaces": [snapshot]}}),
         memory("missing", workspace_id="no-snapshot"),
         Episode("ep-only", "chat", "episode", "now", "now", workspace_id="episode-only"))
    result = memory_api.list_memories_payload(services)
    assert result["workspace_options"][0] == {"id": "default", "name": "全局记忆", "path": ""}
    assert {item["id"] for item in result["workspace_options"]} == {
        "default", "stable-id", "no-snapshot", "episode-only",
    }
    assert snapshot in result["workspace_options"]
    missing = next(item for item in result["workspace_options"] if item["id"] == "no-snapshot")
    assert missing == {"id": "no-snapshot", "name": "未登记工作区 no-snaps", "path": ""}


def test_workspace_filter_uses_row_ownership_instead_of_snapshot(services: MemoryServices):
    snapshot = {"id": "ws-a", "name": "项目 A", "path": "/projects/a"}
    seed(services,
         memory("global", metadata={"provenance": {"workspaces": [snapshot]}}),
         memory("scoped", workspace_id="ws-a"))
    result = memory_api.list_memories_payload(services, workspace="ws-a")
    assert [item["id"] for item in result["items"]] == ["scoped"]
    result = memory_api.list_memories_payload(services, workspace="default")
    assert [item["id"] for item in result["items"]] == ["global"]
    with pytest.raises(memory_api.WebUIMemoryError, match="invalid workspace"):
        memory_api.list_memories_payload(services, workspace="unassigned")
