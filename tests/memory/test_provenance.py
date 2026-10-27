"""记忆来源和项目快照必须跨 SQLite、增量切片与旧库迁移保留。"""

from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import FilteredExtractionResult, LLMEpisodeItem, LLMMemoryItem
from nanobot.memory.models import Episode
from nanobot.memory.repository import add_episode, get_episode, list_memories
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
    extractor = _make_extractor(db, _FakeProvider())
    session = Session(key="websocket:s", metadata={"workspace_scope": {"project_path": r"C:\Work\A"}})
    item = LLMMemoryItem(content="prefer concise responses", type="preference", subject="user", predicate="style")
    result = extractor._persist(FilteredExtractionResult(memories=[item], episode=LLMEpisodeItem(summary="summary")), session, "manual")
    session.metadata["workspace_scope"]["project_path"] = r"D:\Work\B"
    extractor._persist(FilteredExtractionResult(memories=[item]), session, "idle")
    with db.connect() as conn:
        rows = list_memories(conn)
        episode = get_episode(conn, result.episode_ids[0])
    assert len(rows) == 1
    provenance = rows[0].metadata["provenance"]
    assert provenance["trigger_mode"] == "manual"
    assert {item["name"] for item in provenance["workspaces"]} == {"A", "B"}
    assert episode.metadata["provenance"]["workspaces"][0]["name"] == "A"
    assert episode.metadata["provenance"]["trigger_mode"] == "manual"


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
