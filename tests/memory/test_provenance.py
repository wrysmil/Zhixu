"""记忆来源和项目快照必须跨 SQLite、增量切片与旧库迁移保留。"""

from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import FilteredExtractionResult, LLMEpisodeItem, LLMMemoryItem
from nanobot.memory.models import Episode
from nanobot.memory.repository import (
    add_episode,
    get_episode,
    list_memories,
    list_memories_by_session,
)
from nanobot.session.manager import Session
from tests.memory.test_extractor import _FakeProvider, _make_extractor


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.ensure_schema()
    return database


def test_episode_metadata_round_trip_and_legacy_fallback(db: MemoryDatabase):
    episode = Episode("e", "websocket:s", "summary", "start", "end")
    episode.metadata = {"provenance": {"trigger_mode": "manual"}}
    with db.connect() as conn:
        add_episode(conn, episode)
        restored = get_episode(conn, "e")
    assert restored is not None
    assert restored.metadata == episode.metadata
    old_payload = episode.to_row()
    old_payload.pop("metadata")
    assert Episode.from_row(old_payload).metadata == {}


def test_schema_upgrades_old_episode_table_idempotently(db: MemoryDatabase):
    with db.connect() as conn:
        add_episode(conn, Episode("before", "s", "existing summary", "start", "end"))
        conn.execute("ALTER TABLE episodes DROP COLUMN metadata")
    db.ensure_schema()
    db.ensure_schema()
    with db.connect() as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(episodes)")]
        assert columns.count("metadata") == 1
        assert get_episode(conn, "before").summary == "existing summary"
        assert get_episode(conn, "before").metadata == {}
        episode = Episode("old", "s", "kept", "start", "end")
        add_episode(conn, episode)
        assert get_episode(conn, "old").metadata == {}


def test_workspace_id_uses_normalized_path_without_touching_filesystem():
    from nanobot.memory.provenance import workspace_from_path

    first = workspace_from_path("C:/Work/Project/")
    same = workspace_from_path("c:/work/project")
    assert first["id"] == same["id"]
    assert first["name"] == "Project"
    assert workspace_from_path(r"D:\Work\Project")["id"] != first["id"]
    with pytest.raises(ValueError):
        workspace_from_path("../untrusted")


@pytest.mark.parametrize(
    ("source", "expected"), [("idle", "automatic"), ("manual", "manual"), ("future", "unknown")]
)
def test_extraction_provenance_does_not_infer_unknown_triggers(source: str, expected: str):
    from nanobot.memory.provenance import extraction_provenance

    result = extraction_provenance(Session(key="s"), source)
    assert result["trigger_mode"] == expected
    assert result["generation_method"] == "ai"
    assert result["workspaces"] == []
    assert result["session_id"] == "s"


def test_persist_freezes_workspace_and_original_trigger(db: MemoryDatabase):
    """同一工作区内重复落库：仍是一行，原 trigger 与工作区快照保持冻结。

    隔离语义（plan R6）下画像合并只在**同一工作区**内发生；跨工作区的重复
    落库另立一行，见 ``test_cross_workspace_persist_does_not_merge_profiles``。
    """
    extractor = _make_extractor(db, _FakeProvider())
    session = Session(key="websocket:s", metadata={"workspace_scope": {"project_path": r"C:\Work\A"}})
    item = LLMMemoryItem(content="prefer concise responses", type="preference", subject="user", predicate="style")
    result = extractor._persist(FilteredExtractionResult(memories=[item], episode=LLMEpisodeItem(summary="summary")), session, "manual")
    extractor._persist(FilteredExtractionResult(memories=[item]), session, "idle")
    with db.connect() as conn:
        rows = list_memories(conn)
        episode = get_episode(conn, result.episode_ids[0])
    assert len(rows) == 1
    provenance = rows[0].metadata["provenance"]
    assert provenance["trigger_mode"] == "manual"
    assert {item["name"] for item in provenance["workspaces"]} == {"A"}
    assert episode.metadata["provenance"]["workspaces"][0]["name"] == "A"
    assert episode.metadata["provenance"]["trigger_mode"] == "manual"


def test_cross_workspace_persist_does_not_merge_profiles(db: MemoryDatabase):
    """跨工作区的同一 subject+predicate 不合并：各工作区各一行、各自归属。

    旧契约是「跨项目合并成一行并累积项目快照」；工作区隔离后该行为被推翻
    （plan §6 R6）：归属不同的记忆互不污染。
    """
    from nanobot.memory.provenance import memory_workspace_id

    extractor = _make_extractor(db, _FakeProvider())
    session = Session(key="websocket:s", metadata={"workspace_scope": {"project_path": r"C:\Work\A"}})
    item = LLMMemoryItem(content="prefer concise responses", type="preference", subject="user", predicate="style")
    extractor._persist(FilteredExtractionResult(memories=[item]), session, "manual")
    session.metadata["workspace_scope"]["project_path"] = r"D:\Work\B"
    extractor._persist(FilteredExtractionResult(memories=[item]), session, "idle")

    with db.connect() as conn:
        rows = list_memories(conn)

    assert len(rows) == 2
    assert {row.workspace_id for row in rows} == {
        memory_workspace_id(r"C:\Work\A"),
        memory_workspace_id(r"D:\Work\B"),
    }
    by_workspace = {row.workspace_id: row for row in rows}
    first = by_workspace[memory_workspace_id(r"C:\Work\A")]
    second = by_workspace[memory_workspace_id(r"D:\Work\B")]
    assert first.metadata["provenance"]["trigger_mode"] == "manual"
    assert second.metadata["provenance"]["trigger_mode"] == "automatic"
    assert second.metadata["provenance"]["trigger_reason"] == "idle"
    assert {item["name"] for item in first.metadata["provenance"]["workspaces"]} == {"A"}
    assert {item["name"] for item in second.metadata["provenance"]["workspaces"]} == {"B"}


async def test_incremental_slice_preserves_session_workspace(db: MemoryDatabase):
    from unittest.mock import patch

    extractor = _make_extractor(db, _FakeProvider())
    session = Session(key="websocket:s", messages=[{"role": "user", "content": "remember this"}], metadata={"workspace_scope": {"project_path": r"C:\Work\A"}})
    filtered = FilteredExtractionResult(memories=[LLMMemoryItem(content="retained fact", type="fact")])
    with patch.object(extractor, "_apply_filters", return_value=filtered):
        await extractor.extract_incremental(session, 0, source="idle")
    with db.connect() as conn:
        rows = list_memories(conn)
    assert rows[0].metadata["provenance"]["workspaces"][0]["name"] == "A"


async def test_generate_episode_keeps_manual_trigger_without_inventing_workspace(db: MemoryDatabase):
    extractor = _make_extractor(db, _FakeProvider())
    episode = await extractor.generate_episode(
        [{"role": "user", "content": "Please keep this conversation"}],
        "websocket:manual-session", source="manual",
    )
    assert episode is not None
    assert episode.metadata["provenance"]["trigger_mode"] == "manual"
    assert episode.metadata["provenance"]["workspaces"] == []


def test_list_memories_by_session_filters_by_provenance_and_workspace(db: MemoryDatabase):
    """按 provenance.session_id 反查，并受可见集合约束。

    这条链路不依赖 episode 的 linked_memory_ids（那条依赖 LLM 填 subject，
    历史数据普遍为空），是工作台「本会话产出的记忆」的可靠依据。
    """
    extractor = _make_extractor(db, _FakeProvider())
    for key, content in (
        ("websocket:session-a", "会话 A 产出的偏好"),
        ("websocket:session-b", "会话 B 产出的偏好"),
    ):
        extractor._persist(
            FilteredExtractionResult(memories=[LLMMemoryItem(content=content, type="preference")]),
            Session(key=key),
            "idle",
        )

    with db.connect() as conn:
        rows = list_memories_by_session(conn, "websocket:session-a")
        hidden = list_memories_by_session(
            conn, "websocket:session-a", workspace_ids=("not-visible",)
        )
        manual = list_memories_by_session(conn, "manual-entry")

    assert [row.content for row in rows] == ["会话 A 产出的偏好"]
    assert hidden == []
    assert manual == []
