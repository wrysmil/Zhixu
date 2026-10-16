"""T02 建库层：schema_version、外键/WAL/busy_timeout、不可变 revision 与串行执行器。

这些是仓储正确性的地基——如果 revision 行可以被原地 UPDATE，历史快照就不可信；
如果连接不带 busy_timeout，多进程/多线程写会直接抛 database is locked。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from nanobot.evaluation.database import SCHEMA_VERSION, EvaluationDatabase

EXPECTED_TABLES = {
    "suites", "cases", "case_revisions", "runs", "attempts", "idempotency",
    "sample_imports", "reviews", "artifacts", "calibration_results",
    "gold_samples", "gold_state", "calibration_jobs", "calibration_items",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def test_init_schema_creates_every_contract_table(evaluation_database: EvaluationDatabase) -> None:
    with evaluation_database.connect() as conn:
        assert EXPECTED_TABLES <= _tables(conn)


def test_schema_version_is_recorded(evaluation_database: EvaluationDatabase) -> None:
    with evaluation_database.connect() as conn:
        value = conn.execute(
            "SELECT value FROM _schema_meta WHERE key = 'version'"
        ).fetchone()[0]
    assert value == SCHEMA_VERSION


def test_init_schema_is_idempotent(tmp_path: Path) -> None:
    database = EvaluationDatabase(tmp_path / "evaluation.db")
    database.init_schema()
    database.init_schema()
    with database.connect() as conn:
        assert EXPECTED_TABLES <= _tables(conn)


def test_connection_pragmas_are_enforced(tmp_path: Path) -> None:
    database = EvaluationDatabase(tmp_path / "evaluation.db")
    database.init_schema()
    with database.connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    """外键没开时，孤儿 attempt 会静默写进去，重启后才发现调度链断裂。"""
    database = EvaluationDatabase(tmp_path / "evaluation.db")
    database.init_schema()
    with database.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO attempts (id, run_id, position, case_id, case_revision,"
                " case_snapshot_json, execution_status, grading_status, verdict)"
                " VALUES ('a1', 'missing-run', 1, 'c1', 1, '{}', 'queued',"
                " 'pending', 'not_evaluated')"
            )


def test_case_revisions_cannot_be_updated_in_place(
    evaluation_database: EvaluationDatabase,
) -> None:
    """契约 §2/plan §3：revision 是不可变快照，历史运行必须永远能读到当初的正文。"""
    with evaluation_database.connect() as conn:
        conn.execute(
            "INSERT INTO suites (id, revision, name, description, tags_json,"
            " is_deleted, created_at, updated_at)"
            " VALUES ('s1', 1, 's', '', '[]', 0, 't0', 't0')"
        )
        conn.execute(
            "INSERT INTO cases (id, suite_id, latest_revision, is_deleted, created_at,"
            " updated_at) VALUES ('c1', 's1', 1, 0, 't0', 't0')"
        )
        conn.execute(
            "INSERT INTO case_revisions (case_id, revision, definition_json, created_at)"
            " VALUES ('c1', 1, '{\"name\":\"v1\"}', 't0')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE case_revisions SET definition_json = '{\"name\":\"v2\"}'"
                " WHERE case_id = 'c1' AND revision = 1"
            )


def test_attempt_position_is_unique_per_run(evaluation_database: EvaluationDatabase) -> None:
    with evaluation_database.connect() as conn:
        conn.execute(
            "INSERT INTO suites (id, revision, name, description, tags_json,"
            " is_deleted, created_at, updated_at)"
            " VALUES ('s1', 1, 's', '', '[]', 0, 't0', 't0')"
        )
        conn.execute(
            "INSERT INTO cases (id, suite_id, latest_revision, is_deleted, created_at,"
            " updated_at) VALUES ('c1', 's1', 1, 0, 't0', 't0')"
        )
        conn.execute(
            "INSERT INTO runs (id, suite_id, status, snapshot_json, request_hash,"
            " is_deleted, created_at) VALUES ('r1', 's1', 'queued', '{}', 'h', 0, 't0')"
        )
        conn.execute(
            "INSERT INTO attempts (id, run_id, position, case_id, case_revision,"
            " case_snapshot_json, execution_status, grading_status, verdict)"
            " VALUES ('a1', 'r1', 1, 'c1', 1, '{}', 'queued', 'pending', 'not_evaluated')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO attempts (id, run_id, position, case_id, case_revision,"
                " case_snapshot_json, execution_status, grading_status, verdict)"
                " VALUES ('a2', 'r1', 1, 'c1', 1, '{}', 'queued', 'pending',"
                " 'not_evaluated')"
            )


def test_idempotency_key_is_unique_per_scope_and_endpoint(
    evaluation_database: EvaluationDatabase,
) -> None:
    with evaluation_database.connect() as conn:
        conn.execute(
            "INSERT INTO idempotency (scope, endpoint, key, request_hash, created_at)"
            " VALUES ('user-1', 'POST /runs', 'k1', 'h1', 't0')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO idempotency (scope, endpoint, key, request_hash, created_at)"
                " VALUES ('user-1', 'POST /runs', 'k1', 'h2', 't0')"
            )


def test_case_revision_primary_key_rejects_duplicate(
    evaluation_database: EvaluationDatabase,
) -> None:
    with evaluation_database.connect() as conn:
        conn.execute(
            "INSERT INTO suites (id, revision, name, description, tags_json,"
            " is_deleted, created_at, updated_at)"
            " VALUES ('s1', 1, 's', '', '[]', 0, 't0', 't0')"
        )
        conn.execute(
            "INSERT INTO cases (id, suite_id, latest_revision, is_deleted, created_at,"
            " updated_at) VALUES ('c1', 's1', 1, 0, 't0', 't0')"
        )
        conn.execute(
            "INSERT INTO case_revisions (case_id, revision, definition_json,"
            " created_at) VALUES ('c1', 1, '{}', 't0')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO case_revisions (case_id, revision, definition_json,"
                " created_at) VALUES ('c1', 1, '{}', 't0')"
            )


@pytest.mark.asyncio
async def test_arun_executes_off_the_event_loop(
    evaluation_database: EvaluationDatabase,
) -> None:
    """契约/plan §3：同步 SQLite 操作必须离开 aiohttp 事件循环，否则单次写会拖住轮询。"""
    event_loop_thread = threading.get_ident()
    result = await evaluation_database.arun(threading.get_ident)
    assert result != event_loop_thread


@pytest.mark.asyncio
async def test_arun_propagates_exceptions(
    evaluation_database: EvaluationDatabase,
) -> None:
    def _boom() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await evaluation_database.arun(_boom)


def test_connect_rolls_back_on_error(tmp_path: Path) -> None:
    database = EvaluationDatabase(tmp_path / "evaluation.db")
    database.init_schema()
    with pytest.raises(RuntimeError):
        with database.connect() as conn:
            conn.execute(
                "INSERT INTO suites (id, revision, name, description, tags_json,"
                " is_deleted, created_at, updated_at)"
                " VALUES ('s1', 1, 's', '', '[]', 0, 't0', 't0')"
            )
            raise RuntimeError("abort")
    with database.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM suites WHERE id = 's1'"
        ).fetchone()[0]
    assert count == 0


def test_migrate_rejects_unknown_future_version(tmp_path: Path) -> None:
    """库文件版本比代码新时必须拒绝启动，而不是继续往未知结构上写。"""
    database = EvaluationDatabase(tmp_path / "evaluation.db")
    database.init_schema()
    with database.connect() as conn:
        conn.execute(
            "UPDATE _schema_meta SET value = '9999' WHERE key = 'version'"
        )
    with pytest.raises(RuntimeError, match="SCHEMA_VERSION_TOO_NEW"):
        database.init_schema()
