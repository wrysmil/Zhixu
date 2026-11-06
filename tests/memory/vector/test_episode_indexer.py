"""Episodes 向量索引：写入、对账与删除。"""

from __future__ import annotations

from types import SimpleNamespace

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.experience import record_experience
from nanobot.memory.vector.indexer import (
    EpisodeVectorIndexer,
    set_active_episode_indexer,
)


class _FakeStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.deleted: list[str] = []

    def upsert_episode(self, episode_id, content, metadata):
        self.rows[episode_id] = {"content": content, "metadata": metadata}
        return True

    def remove_episode(self, episode_id):
        self.rows.pop(episode_id, None)
        self.deleted.append(episode_id)
        return True

    def search_episodes(self, query, *, limit=15):
        return [(eid, 0.9) for eid in list(self.rows)[:limit]]

    def list_episode_ids(self):
        return list(self.rows)

    def delete_episode_ids(self, ids):
        for eid in ids:
            self.rows.pop(eid, None)
        self.deleted.extend(ids)
        return len(ids)


def _db(tmp_path) -> MemoryDatabase:
    return MemoryDatabase(tmp_path, db_path=tmp_path / "state.db")


def test_indexer_sync_adds_missing_and_removes_stale(tmp_path):
    db = _db(tmp_path)
    record_experience(
        db, workspace_id="default", session_id="s1", title="a",
        situation="情况 A", solution="做法 A",
    )
    record_experience(
        db, workspace_id="default", session_id="s1", title="b",
        kind="pitfall", symptom="报错 B", fix="修 B",
    )
    store = _FakeStore()
    store.rows["stale-id"] = {"content": "旧", "metadata": {}}
    indexer = EpisodeVectorIndexer(store, db)

    result = indexer.sync_from_sqlite()

    assert result["indexed"] == 2
    assert result["deleted"] == 1
    assert "stale-id" not in store.rows
    assert len(store.rows) == 2
    kinds = {row["metadata"]["kind"] for row in store.rows.values()}
    assert kinds == {"experience", "pitfall"}


def test_record_experience_pushes_to_active_indexer(tmp_path):
    db = _db(tmp_path)
    store = _FakeStore()
    set_active_episode_indexer(EpisodeVectorIndexer(store, db))
    try:
        episode = record_experience(
            db, workspace_id="default", session_id="s1", title="踩坑",
            kind="pitfall", symptom="数据库连接超时", fix="等健康检查",
        )
    finally:
        set_active_episode_indexer(None)

    assert episode.id in store.rows
    assert store.rows[episode.id]["metadata"]["kind"] == "pitfall"
    assert "症状：数据库连接超时" in store.rows[episode.id]["content"]


def test_indexer_remove(tmp_path):
    store = _FakeStore()
    indexer = EpisodeVectorIndexer(store, _db(tmp_path))
    store.rows["e1"] = {"content": "x", "metadata": {}}
    assert indexer.remove("e1") is True
    assert store.deleted == ["e1"]
