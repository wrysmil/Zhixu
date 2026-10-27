"""vector_sync_state 表读写契约（Q2 选新表）。"""
from __future__ import annotations

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.repository import get_vector_sync_state, upsert_vector_sync_state


def test_sync_state_roundtrip(tmp_path):
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        assert get_vector_sync_state(conn) is None
        upsert_vector_sync_state(
            conn, cursor="2026-09-16T10:00:00+00:00", indexed=3, deleted=1, last_error=""
        )
    with db.connect() as conn:
        state = get_vector_sync_state(conn)
    assert state is not None
    assert state.cursor == "2026-09-16T10:00:00+00:00"
    assert state.indexed == 3
    assert state.deleted == 1


def test_sync_state_records_error(tmp_path):
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        upsert_vector_sync_state(conn, cursor="", indexed=0, deleted=0, last_error="boom")
    with db.connect() as conn:
        assert get_vector_sync_state(conn).last_error == "boom"


def test_schema_version_bumped(tmp_path):
    """ensure_schema 后 _schema_meta.version 必须是 "3"（episodes 增加 workspace_id 列）。"""
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT value FROM _schema_meta WHERE key = 'version'"
        ).fetchone()
    assert row[0] == "3"
