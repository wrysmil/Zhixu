"""WU-A Task 1：``session_extraction_state`` 表的 CRUD 仓库函数测试。

覆盖:
- CRUD roundtrip: get 缺失返回 None,upsert 后 get 返回一致内容。
- 多次 upsert 推进 last_count / 更新 last_extracted_at。
- reset 真正删除行(reset 后 get 返回 None)。
- 缺失 key 的 get 返回 None(独立 case)。
- 记忆减法（契约 §2.3）：``generation`` / ``last_seq`` 列的读写与旧列镜像。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


class TestExtractionStateCRUD:
    def test_get_returns_none_when_missing(self, db: MemoryDatabase):
        from nanobot.memory.repository import get_extraction_state

        with db.connect() as conn:
            assert get_extraction_state(conn, "nope") is None

    def test_upsert_then_get_roundtrip(self, db: MemoryDatabase):
        from nanobot.memory.repository import (
            get_extraction_state,
            upsert_extraction_state,
        )

        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                "s1",
                last_count=5,
                source="idle",
                extracted_at="2026-09-15T10:00:00+00:00",
            )
            row = get_extraction_state(conn, "s1")
        assert row is not None
        assert row.session_key == "s1"
        assert row.last_count == 5
        assert row.last_source == "idle"
        assert row.last_extracted_at == "2026-09-15T10:00:00+00:00"
        assert row.updated_at == "2026-09-15T10:00:00+00:00"

    def test_upsert_advances_cursor(self, db: MemoryDatabase):
        """第二次 upsert 必须把 last_count 推进,last_extracted_at 刷新。"""
        from nanobot.memory.repository import (
            get_extraction_state,
            upsert_extraction_state,
        )

        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                "s1",
                last_count=3,
                source="idle",
                extracted_at="2026-09-15T10:00:00+00:00",
            )
            upsert_extraction_state(
                conn,
                "s1",
                last_count=10,
                source="idle",
                extracted_at="2026-09-15T10:05:00+00:00",
            )
            row = get_extraction_state(conn, "s1")
        assert row is not None
        assert row.last_count == 10
        assert row.last_extracted_at == "2026-09-15T10:05:00+00:00"

    def test_reset_removes_row(self, db: MemoryDatabase):
        from nanobot.memory.repository import (
            get_extraction_state,
            reset_extraction_state,
            upsert_extraction_state,
        )

        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                "s2",
                last_count=7,
                source="idle",
                extracted_at="2026-09-15T10:00:00+00:00",
            )
            assert get_extraction_state(conn, "s2") is not None
            reset_extraction_state(conn, "s2")
            assert get_extraction_state(conn, "s2") is None

    def test_reset_missing_key_is_noop(self, db: MemoryDatabase):
        """reset 一个不存在的 key 不应抛异常,且后续 get 仍返回 None。"""
        from nanobot.memory.repository import (
            get_extraction_state,
            reset_extraction_state,
        )

        with db.connect() as conn:
            reset_extraction_state(conn, "never-existed")
            assert get_extraction_state(conn, "never-existed") is None

    def test_schema_table_exists(self, db: MemoryDatabase):
        """``session_extraction_state`` 表已建好，且旧列 + 坐标列齐全。"""
        with db.connect() as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='session_extraction_state'"
            )
            assert cur.fetchone() is not None
            cols = {row[1] for row in conn.execute("PRAGMA table_info(session_extraction_state)").fetchall()}
        # 记忆减法（契约 §2.3）：旧列只为兼容保留，进度列是新加的。
        assert cols == {
            "session_key",
            "last_count",
            "last_source",
            "last_extracted_at",
            "updated_at",
            "generation",
            "last_seq",
        }


class TestGenerationScopedSeq:
    """``get_extraction_seq`` / ``set_extraction_seq``（按 generation 读写进度）。"""

    def test_missing_row_is_zero(self, db: MemoryDatabase):
        from nanobot.memory.repository import get_extraction_seq

        with db.connect() as conn:
            assert get_extraction_seq(conn, "nope", "gen-1") == 0

    def test_roundtrip_and_generation_isolation(self, db: MemoryDatabase):
        from nanobot.memory.repository import get_extraction_seq, set_extraction_seq

        with db.connect() as conn:
            set_extraction_seq(
                conn,
                "s1",
                generation="gen-1",
                last_seq=4,
                source="idle",
                extracted_at="2026-11-02T10:00:00+00:00",
                last_count=4,
            )
            assert get_extraction_seq(conn, "s1", "gen-1") == 4
            # fork / 清空后的新代必须从头消费。
            assert get_extraction_seq(conn, "s1", "gen-2") == 0

    def test_legacy_row_seeds_from_last_count(self, db: MemoryDatabase):
        from nanobot.memory.repository import (
            get_extraction_seq,
            upsert_extraction_state,
        )

        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                "s-legacy",
                last_count=6,
                source="idle",
                extracted_at="2026-09-15T10:00:00+00:00",
            )
            # 升级前的行没有 generation：last_count 只作为迁移种子。
            assert get_extraction_seq(conn, "s-legacy", "gen-1") == 6

    def test_write_mirrors_legacy_columns(self, db: MemoryDatabase):
        from nanobot.memory.repository import (
            get_extraction_state,
            set_extraction_seq,
        )

        with db.connect() as conn:
            set_extraction_seq(
                conn,
                "s2",
                generation="gen-1",
                last_seq=3,
                source="session_end",
                extracted_at="2026-11-02T11:00:00+00:00",
                last_count=2,
            )
            row = get_extraction_state(conn, "s2")
        assert row is not None
        assert row.generation == "gen-1"
        assert row.last_seq == 3
        # 旧字段仍被刷新（兼容只读方），但不构成第二条进度链。
        assert row.last_count == 2
        assert row.last_source == "session_end"
