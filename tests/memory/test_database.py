"""Tests for MemoryDatabase connection management."""

from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


class TestDatabasePath:
    def test_default_db_path_is_under_workspace_memory(self, workspace: Path):
        db = MemoryDatabase(workspace)
        assert db.db_path == workspace / "memory" / "state.db"

    def test_memory_directory_is_created(self, workspace: Path):
        MemoryDatabase(workspace)
        assert (workspace / "memory").is_dir()

    def test_db_file_is_created_after_connect(self, workspace: Path):
        db = MemoryDatabase(workspace)
        with db.connect() as conn:
            pass
        assert db.db_path.is_file()

    def test_custom_db_path_overrides_default(self, workspace: Path):
        custom = workspace / "custom.db"
        db = MemoryDatabase(workspace, db_path=custom)
        assert db.db_path == custom


class TestDatabaseConnection:
    def test_connect_returns_sqlite_connection(self, workspace: Path):
        import sqlite3
        db = MemoryDatabase(workspace)
        with db.connect() as conn:
            assert isinstance(conn, sqlite3.Connection)

    def test_connect_enables_foreign_keys(self, workspace: Path):
        db = MemoryDatabase(workspace)
        with db.connect() as conn:
            cur = conn.execute("PRAGMA foreign_keys")
            assert cur.fetchone()[0] == 1

    def test_concurrent_connects_share_lock(self, workspace: Path):
        import threading
        db = MemoryDatabase(workspace)
        errors: list[Exception] = []
        def worker():
            try:
                with db.connect() as conn:
                    conn.execute("SELECT 1").fetchone()
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


class TestSchema:
    def test_init_schema_creates_three_core_tables(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('memories', 'episodes', 'scratchpad') ORDER BY name"
            ).fetchall()
        assert [r[0] for r in rows] == ['episodes', 'memories', 'scratchpad']

    def test_init_schema_creates_indexes(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        with db.connect() as conn:
            idx_rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name IN ('memories', 'episodes', 'scratchpad')"
            ).fetchall()
            idx_names = {r[0] for r in idx_rows}
        assert 'idx_memories_owner' in idx_names
        assert 'idx_episodes_session' in idx_names

    def test_init_schema_records_version_1(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        with db.connect() as conn:
            ver = conn.execute(
                "SELECT value FROM _schema_meta WHERE key='version'"
            ).fetchone()[0]
        assert ver == '3'

    def test_init_schema_is_idempotent(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        db.init_schema()  # 第二次不能报错
        db.init_schema()  # 第三次也不能报错
        with db.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='memories'"
            ).fetchone()[0]
        assert count == 1

    def test_init_schema_creates_on_connect_via_ensure_schema(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.ensure_schema()
        with db.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='memories'"
            ).fetchone()[0]
        assert count == 1

    def test_ensure_schema_backfills_missing_tables_on_existing_db(self, workspace: Path):
        """旧库（_schema_meta 已存在）调用 ensure_schema 必须补建缺失表。

        场景：v1 DB（_schema_meta + memories/episodes/scratchpad）升级到包含
        session_extraction_state 的代码后，首次 ensure_schema 必须自动补建该表，
        否则 idle 路径调 get_extraction_state 会因 no such table 抛异常。
        """
        import sqlite3

        db_path = workspace / "memory" / "state.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. 模拟"用老代码创建的库"：完整 init_schema 后删掉 session_extraction_state
        #    （保持 v1 时代的状态：_schema_meta 已存在、新表未建）。
        db = MemoryDatabase(workspace)
        db.init_schema()
        with sqlite3.connect(db_path) as raw:
            raw.execute("DROP TABLE session_extraction_state")
            # _schema_meta 必须存在（v1 时代已建），确保不走老的"未初始化"分支。
            rows = raw.execute(
                "SELECT value FROM _schema_meta WHERE key='version'"
            ).fetchall()
            raw.commit()
            assert rows and rows[0][0] == "3", "_schema_meta 应升至 v3"
        # 关闭旧连接
        del db

        # 2. 用新代码的 ensure_schema 打开同一个文件
        db2 = MemoryDatabase(workspace)
        db2.ensure_schema()

        # 3. 验证：旧表还在（数据未丢），新表被补建
        with db2.connect() as conn:
            legacy_intact = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name IN ('_schema_meta', 'memories', 'episodes', 'scratchpad')"
            ).fetchone()[0]
            new_table = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name='session_extraction_state'"
            ).fetchone()[0]
        assert legacy_intact == 4, "旧库表应保留"
        assert new_table == 1, "session_extraction_state 必须被补建"

    def test_ensure_schema_is_idempotent_on_existing_db(self, workspace: Path):
        """ensure_schema 连续调用多次：表不重复、数据不丢。"""
        db = MemoryDatabase(workspace)
        db.ensure_schema()
        db.ensure_schema()
        db.ensure_schema()
        with db.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='memories'"
            ).fetchone()[0]
        assert count == 1

    def test_memories_table_has_required_columns(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        with db.connect() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
        required = {
            'id', 'content', 'type', 'priority', 'source',
            'importance_score', 'access_count', 'tags', 'subject',
            'predicate', 'confidence', 'decay_rate', 'expires_at',
            'last_accessed_at', 'superseded_by', 'source_episode_id',
            'scope', 'scope_owner', 'agent_id', 'user_id', 'workspace_id',
            'metadata', 'created_at', 'updated_at',
        }
        assert required.issubset(cols)

    def test_scratchpad_primary_key_is_composite(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        with db.connect() as conn:
            pk_cols = [
                r['name'] for r in
                conn.execute("PRAGMA table_info(scratchpad)").fetchall()
                if r['pk'] > 0
            ]
        assert pk_cols == ['user_id', 'workspace_id']

    def test_episodes_table_has_workspace_id_column(self, workspace: Path):
        """episodes 表带工作区列（D6：情节通道同样要能隔离）。"""
        db = MemoryDatabase(workspace)
        db.init_schema()
        with db.connect() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(episodes)").fetchall()}
        assert 'workspace_id' in cols

    def test_legacy_episodes_table_gets_workspace_id_column(self, workspace: Path):
        """旧库（无 workspace_id 列）必须被幂等 ALTER 补齐。"""
        db = MemoryDatabase(workspace)
        with db.connect() as conn:
            conn.execute("CREATE TABLE episodes (id TEXT PRIMARY KEY)")  # 极简旧表
        db.ensure_schema()
        with db.connect() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(episodes)")}
        assert "workspace_id" in columns

    def test_legacy_episodes_migration_is_idempotent(self, workspace: Path):
        """重复 ensure_schema 不会因已存在该列而报错。"""
        db = MemoryDatabase(workspace)
        db.ensure_schema()
        db.ensure_schema()
        with db.connect() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(episodes)")}
        assert "workspace_id" in columns

    def test_realistic_legacy_episodes_table_gets_workspace_id(self, workspace: Path):
        """真实旧库（v2 完整表结构、缺 workspace_id/metadata）迁移后列齐全且数据不丢。"""
        db = MemoryDatabase(workspace)
        with db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE episodes (
                    id          TEXT    PRIMARY KEY,
                    session_id  TEXT    NOT NULL,
                    summary     TEXT    NOT NULL,
                    goal        TEXT    NOT NULL DEFAULT '',
                    outcome     TEXT    NOT NULL DEFAULT 'completed',
                    source      TEXT    NOT NULL DEFAULT 'session_end',
                    started_at  TEXT    NOT NULL,
                    ended_at    TEXT    NOT NULL,
                    action_nodes      TEXT NOT NULL DEFAULT '[]',
                    entities          TEXT NOT NULL DEFAULT '[]',
                    tools_used        TEXT NOT NULL DEFAULT '[]',
                    linked_memory_ids TEXT NOT NULL DEFAULT '[]',
                    tags              TEXT NOT NULL DEFAULT '[]',
                    importance_score  REAL NOT NULL DEFAULT 0.5,
                    access_count      INTEGER NOT NULL DEFAULT 0,
                    compaction_checkpoint_id TEXT NOT NULL DEFAULT '',
                    workspace_snapshot_id    TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                "INSERT INTO episodes (id, session_id, summary, started_at, ended_at) "
                "VALUES ('ep-old', 'ws:1', '旧情节', '2026-01-01T00:00:00+00:00', "
                "'2026-01-01T00:01:00+00:00')"
            )
        db.ensure_schema()
        with db.connect() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(episodes)")}
            row = conn.execute(
                "SELECT id, workspace_id FROM episodes WHERE id = 'ep-old'"
            ).fetchone()
            idx_names = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='episodes'"
                ).fetchall()
            }
        assert "workspace_id" in columns
        assert "metadata" in columns
        assert row is not None and row["workspace_id"] == "default"
        assert 'idx_episodes_workspace' in idx_names

    def test_init_schema_creates_workspace_index(self, workspace: Path):
        """工作区列有索引（避免过滤查询全表扫描）。"""
        db = MemoryDatabase(workspace)
        db.init_schema()
        with db.connect() as conn:
            idx_names = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='episodes'"
                ).fetchall()
            }
        assert 'idx_episodes_workspace' in idx_names


class TestFts5:
    def test_memories_fts_table_exists(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        with db.connect() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts'"
            ).fetchone()
        assert row is not None

    def test_memories_fts_insert_trigger_works(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        now = "2026-09-07T10:00:00"
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO memories (id, content, subject, predicate, tags, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("m1", "用户喜欢 Python", "用户", "喜欢", '["编程"]', now, now),
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?",
                ("Python",),
            ).fetchone()[0]
        assert count == 1

    def test_memories_fts_delete_trigger_removes_entry(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        now = "2026-09-07T10:00:00"
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO memories (id, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("m1", "用户喜欢 Python", now, now),
            )
            conn.execute("DELETE FROM memories WHERE id = 'm1'")
            count = conn.execute(
                "SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?",
                ("Python",),
            ).fetchone()[0]
        assert count == 0

    def test_memories_fts_update_trigger_reindexes(self, workspace: Path):
        db = MemoryDatabase(workspace)
        db.init_schema()
        now = "2026-09-07T10:00:00"
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO memories (id, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("m1", "用户喜欢 Python", now, now),
            )
            conn.execute(
                "UPDATE memories SET content = ? WHERE id = ?",
                ("用户喜欢 Rust", "m1"),
            )
            py_count = conn.execute(
                "SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?",
                ("Python",),
            ).fetchone()[0]
            rust_count = conn.execute(
                "SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?",
                ("Rust",),
            ).fetchone()[0]
        assert py_count == 0
        assert rust_count == 1
