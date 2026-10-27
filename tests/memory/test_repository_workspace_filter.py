"""Tests for Task 3: 记忆与情节查询支持按可见集合（workspace_ids）过滤。

可见集合语义（plan §2 D3）：
- ``workspace_ids is None`` → 不过滤（管理面 / 全量读）；
- ``workspace_ids`` 为空元组 → **恒假**（什么都看不到），不得退化成不过滤，
  否则会泄漏其它工作区的记忆。
"""

from __future__ import annotations

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Episode, Memory, MemoryType
from nanobot.memory.repository import (
    add_episode,
    add_memory,
    known_workspace_ids,
    list_episodes,
    list_memories,
    query_semantic,
    search_episodes,
    search_memories,
    search_semantic_scored,
)


def _seed(tmp_path):
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        for index, workspace in enumerate(("default", "ws-a", "ws-b")):
            add_memory(conn, Memory(
                id=f"m-{index}",
                content=f"创作灵感 项目{index}",
                created_at="2026-10-27T00:00:00+00:00",
                updated_at="2026-10-27T00:00:00+00:00",
                type=MemoryType.FACT,
                workspace_id=workspace,
            ))
        for index, workspace in enumerate(("default", "ws-a")):
            add_episode(conn, Episode(
                id=f"ep-{index}",
                session_id="websocket:a",
                summary=f"创作灵感 情节{index}",
                started_at="2026-10-27T00:00:00+00:00",
                ended_at="2026-10-27T00:01:00+00:00",
                workspace_id=workspace,
            ))
    return db


def test_search_memories_filters_by_workspace_ids(tmp_path) -> None:
    db = _seed(tmp_path)
    with db.connect() as conn:
        hits = search_memories(conn, "创作灵感", workspace_ids=("default", "ws-a"))
    assert {m.workspace_id for m in hits} == {"default", "ws-a"}


def test_semantic_and_recent_respect_workspace_ids(tmp_path) -> None:
    db = _seed(tmp_path)
    with db.connect() as conn:
        scored = search_semantic_scored(conn, "创作灵感", workspace_ids=("default",))
        recent = query_semantic(conn, min_importance=0.0, since_days=3650, limit=10,
                                workspace_ids=("ws-a",))
    assert {m.workspace_id for m, _ in scored} == {"default"}
    assert {m.id for m in recent} == {"m-1"}


def test_empty_workspace_ids_matches_nothing(tmp_path) -> None:
    db = _seed(tmp_path)
    with db.connect() as conn:
        hits = list_memories(conn, workspace_ids=())
    assert hits == []


def test_episodes_filter_by_workspace_ids(tmp_path) -> None:
    db = _seed(tmp_path)
    with db.connect() as conn:
        rows = list_episodes(conn, workspace_ids=("ws-a",))
        entity_hits = search_episodes(conn, entity="创作灵感", workspace_ids=("default",))
    assert {r.workspace_id for r in rows} == {"ws-a"}
    assert {r.id for r in entity_hits} == {"ep-0"}


def test_known_workspace_ids_lists_store_workspaces(tmp_path) -> None:
    db = _seed(tmp_path)
    with db.connect() as conn:
        ids = known_workspace_ids(conn)
    assert ids == {"default", "ws-a", "ws-b"}


def test_empty_workspace_ids_on_episodes_matches_nothing(tmp_path) -> None:
    """空可见集合必须恒假——否则会泄漏其它工作区的情节。"""
    db = _seed(tmp_path)
    with db.connect() as conn:
        rows = list_episodes(conn, workspace_ids=())
        entity_hits = search_episodes(conn, entity="创作灵感", workspace_ids=())
        hits = search_memories(conn, "创作灵感", workspace_ids=())
        scored = search_semantic_scored(conn, "创作灵感", workspace_ids=())
        recent = query_semantic(conn, min_importance=0.0, since_days=3650, limit=10,
                                workspace_ids=())
    assert rows == []
    assert entity_hits == []
    assert hits == []
    assert scored == []
    assert recent == []


def test_workspace_ids_none_keeps_legacy_behaviour(tmp_path) -> None:
    """``workspace_ids=None`` → 不过滤（管理面 / 全量读，向后兼容）。"""
    db = _seed(tmp_path)
    with db.connect() as conn:
        assert len(list_memories(conn)) == 3
        assert len(list_memories(conn, workspace_ids=None)) == 3
        assert len(list_episodes(conn, workspace_ids=None)) == 2
        assert len(search_memories(conn, "创作灵感", workspace_ids=None)) == 3


def test_known_workspace_ids_tolerates_missing_tables(tmp_path) -> None:
    """缺表场景容错：返回空集合而不是抛异常。"""
    db = MemoryDatabase(tmp_path)
    with db.connect() as conn:
        assert known_workspace_ids(conn) == set()
