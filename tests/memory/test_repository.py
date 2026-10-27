"""Tests for the extended repository CRUD: update_memory / update_episode / delete_episode / list_episodes."""

from __future__ import annotations

import uuid

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import (
    Episode,
    EpisodeOutcome,
    EpisodeSource,
    Memory,
    MemoryPriority,
    MemoryType,
)
from nanobot.memory.repository import (
    add_episode,
    add_memory,
    delete_episode,
    delete_memory,
    get_episode,
    get_memory,
    list_episodes,
    list_memories,
    update_episode,
    update_memory,
)


@pytest.fixture
def db(tmp_path):
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


def _make_memory(tmp_path, **overrides) -> Memory:
    base = dict(
        id=str(uuid.uuid4()),
        content="用户偏好 Python 编程语言",
        created_at="2026-09-08T10:00:00+00:00",
        updated_at="2026-09-08T10:00:00+00:00",
        type=MemoryType.PREFERENCE,
        priority=MemoryPriority.LONG_TERM,
        source="manual",
        importance_score=0.5,
        tags=["python"],
        subject="用户",
        predicate="偏好",
        workspace_id="default",
    )
    base.update(overrides)
    return Memory(**base)


def _make_episode(session_id: str = "telegram:chat-1", **overrides) -> Episode:
    base = dict(
        id=str(uuid.uuid4()),
        session_id=session_id,
        summary="用户在 Windows 11 配置 uv",
        started_at="2026-09-08T10:00:00+00:00",
        ended_at="2026-09-08T10:30:00+00:00",
        goal="让 Agent 用 uv 管理依赖",
        outcome=EpisodeOutcome.COMPLETED,
        source=EpisodeSource.SESSION_END,
    )
    base.update(overrides)
    return Episode(**base)


# ---- update_memory ---------------------------------------------------------


def test_update_memory_changes_content_and_bumps_updated_at(db):
    m = _make_memory(db.workspace)
    with db.connect() as conn:
        add_memory(conn, m)
        update_memory(conn, m.id, content="用户偏好 Go 编程语言")

    with db.connect() as conn:
        roundtrip = get_memory(conn, m.id)
        assert roundtrip is not None
        assert roundtrip.content == "用户偏好 Go 编程语言"
        assert roundtrip.updated_at >= m.updated_at


def test_update_memory_keeps_fts_in_sync(db):
    m = _make_memory(db.workspace, content="用户偏好 Python", tags=["programming"])
    with db.connect() as conn:
        add_memory(conn, m)
        update_memory(conn, m.id, content="用户偏好 Rust 编程语言")

    with db.connect() as conn:
        results = conn.execute(
            "SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'rust'",
        ).fetchall()
        assert len(results) == 1
        # Old keyword must NOT appear
        results = conn.execute(
            "SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'python'",
        ).fetchall()
        assert len(results) == 0


def test_update_memory_unknown_id_raises(db):
    with db.connect() as conn:
        with pytest.raises(KeyError):
            update_memory(conn, "no-such-id", content="x")


# ---- update_episode --------------------------------------------------------


def test_update_episode_changes_summary_and_tags(db):
    ep = _make_episode()
    with db.connect() as conn:
        add_episode(conn, ep)
        update_episode(conn, ep.id, summary="用户在 Windows 11 配置 go", tags=["go", "win"])

    with db.connect() as conn:
        roundtrip = get_episode(conn, ep.id)
        assert roundtrip is not None
        assert roundtrip.summary == "用户在 Windows 11 配置 go"
        assert roundtrip.tags == ["go", "win"]


def test_update_episode_unknown_id_raises(db):
    with db.connect() as conn:
        with pytest.raises(KeyError):
            update_episode(conn, "no-such-id", summary="x")


# ---- delete_episode --------------------------------------------------------


def test_delete_episode_removes_row(db):
    ep = _make_episode()
    with db.connect() as conn:
        add_episode(conn, ep)
        delete_episode(conn, ep.id)

    with db.connect() as conn:
        assert get_episode(conn, ep.id) is None


def test_delete_episode_unknown_id_raises(db):
    with db.connect() as conn:
        with pytest.raises(KeyError):
            delete_episode(conn, "no-such-id")


# ---- list_episodes (all sessions) ------------------------------------------


def test_list_episodes_returns_all_sessions_ordered(db):
    a = _make_episode(session_id="telegram:chat-1", started_at="2026-09-07T10:00:00+00:00")
    b = _make_episode(session_id="cli:local-2", started_at="2026-09-09T10:00:00+00:00")
    c = _make_episode(session_id="cli:local-2", started_at="2026-09-08T10:00:00+00:00")
    with db.connect() as conn:
        for ep in (a, b, c):
            add_episode(conn, ep)

    with db.connect() as conn:
        all_eps = list_episodes(conn)
        assert [e.id for e in all_eps] == [b.id, c.id, a.id]  # most recent first


def test_list_episodes_filter_by_session(db):
    a = _make_episode(session_id="telegram:chat-1")
    b = _make_episode(session_id="cli:local-2")
    with db.connect() as conn:
        add_episode(conn, a)
        add_episode(conn, b)

    with db.connect() as conn:
        only_telegram = list_episodes(conn, session_id="telegram:chat-1")
        assert [e.id for e in only_telegram] == [a.id]


def test_list_episodes_respects_limit(db):
    for i in range(5):
        ep = _make_episode(started_at=f"2026-09-0{i + 1}T10:00:00+00:00")
        with db.connect() as conn:
            add_episode(conn, ep)

    with db.connect() as conn:
        assert len(list_episodes(conn, limit=3)) == 3


# ---- existing get_memory / delete_memory still work (regression) -----------


def test_get_memory_returns_none_when_missing(db):
    with db.connect() as conn:
        assert get_memory(conn, "missing") is None


def test_delete_memory_unknown_id_is_silent(db):
    with db.connect() as conn:
        delete_memory(conn, "missing")  # must not raise; legacy behavior


def test_list_memories_with_workspace_filter(db):
    m1 = _make_memory(db.workspace, workspace_id="ws-A")
    m2 = _make_memory(db.workspace, workspace_id="ws-B")
    with db.connect() as conn:
        add_memory(conn, m1)
        add_memory(conn, m2)
        only_a = list_memories(conn, workspace_id="ws-A")
    assert len(only_a) == 1 and only_a[0].id == m1.id


# ---- episode 工作区列 -------------------------------------------------------


def test_episode_roundtrip_keeps_workspace_id(db):
    """Episode 携带工作区列：写入 → 读回不丢归属（D6）。"""
    ep = _make_episode(workspace_id="ws-abc")
    with db.connect() as conn:
        add_episode(conn, ep)
        loaded = get_episode(conn, ep.id)
    assert loaded is not None
    assert loaded.workspace_id == "ws-abc"


def test_episode_defaults_to_global_workspace(db):
    """未显式指定时归属全局（``default``）。"""
    ep = _make_episode()
    with db.connect() as conn:
        add_episode(conn, ep)
        loaded = get_episode(conn, ep.id)
    assert loaded is not None
    assert loaded.workspace_id == "default"


def test_episode_to_row_and_from_row_roundtrip_workspace_id():
    """``to_row`` / ``from_row`` 同步工作区列（fallback replay 依赖该对称性）。"""
    ep = _make_episode(workspace_id="ws-roundtrip")
    restored = Episode.from_row(ep.to_row())
    assert restored.workspace_id == "ws-roundtrip"
    assert restored.metadata == {}
