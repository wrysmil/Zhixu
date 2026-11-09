"""T02 建库层：schema_version、外键/WAL/busy_timeout、不可变 revision 与串行执行器。

这些是仓储正确性的地基——如果 revision 行可以被原地 UPDATE，历史快照就不可信；
如果连接不带 busy_timeout，多进程/多线程写会直接抛 database is locked。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from nanobot.evaluation import database as database_module
from nanobot.evaluation.database import SCHEMA_VERSION, EvaluationDatabase

EXPECTED_TABLES = {
    "suites", "cases", "case_revisions", "runs", "attempts", "idempotency",
    "sample_imports", "reviews", "artifacts", "calibration_results",
    "gold_samples", "gold_state", "calibration_jobs", "calibration_items",
}

#: 迁移 4 新增的六张记忆专项表（contract §7）。
MEMORY_TABLES = {
    "memory_dataset_revisions", "memory_scenarios", "memory_import_jobs",
    "memory_experiments", "memory_experiment_runs", "memory_comparisons",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def test_init_schema_creates_every_contract_table(evaluation_database: EvaluationDatabase) -> None:
    with evaluation_database.connect() as conn:
        assert EXPECTED_TABLES <= _tables(conn)


def test_init_schema_creates_memory_tables(evaluation_database: EvaluationDatabase) -> None:
    """contract §7：迁移 4 追加六张记忆专项表。"""
    with evaluation_database.connect() as conn:
        assert MEMORY_TABLES <= _tables(conn)


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


# --------------------------------------------------------------------- 迁移 4


def _init_at_version(
    database: EvaluationDatabase, version: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """把库初始化到指定的历史版本。

    只让版本 ≤ N 的迁移正文执行，造出来的"旧库"与线上真实旧库同构：结构来自
    同一批迁移语句，不是手抄的近似结构；也不会提前带上后续版本才引入的表/列
    （只改版本号会让全新库跑完全部迁移，结构与版本号对不上）。
    """
    original_migrations = database_module._MIGRATIONS
    original_version = database_module.SCHEMA_VERSION
    subset = tuple(
        (item_version, statements)
        for item_version, statements in original_migrations
        if int(item_version) <= int(version)
    )
    monkeypatch.setattr(database_module, "_MIGRATIONS", subset)
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", version)
    try:
        database.init_schema()
    finally:
        monkeypatch.setattr(database_module, "_MIGRATIONS", original_migrations)
        monkeypatch.setattr(database_module, "SCHEMA_VERSION", original_version)


def _seed_legacy_run(database: EvaluationDatabase, result_json: str) -> None:
    with database.connect() as conn:
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
            " VALUES ('c1', 1, '{}', 't0')"
        )
        conn.execute(
            "INSERT INTO runs (id, suite_id, status, snapshot_json, request_hash,"
            " is_deleted, created_at) VALUES ('r1', 's1', 'completed', '{}', 'h', 0, 't0')"
        )
        conn.execute(
            "INSERT INTO attempts (id, run_id, position, case_id, case_revision,"
            " case_snapshot_json, execution_status, grading_status, verdict, result_json)"
            " VALUES ('a1', 'r1', 1, 'c1', 1, '{}', 'succeeded', 'completed', 'passed', ?)",
            (result_json,),
        )


def test_migration_four_upgrades_a_version_three_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = EvaluationDatabase(tmp_path / "evaluation.db")
    _init_at_version(database, "3", monkeypatch)
    assert database.schema_version() == 3
    database.init_schema()
    assert database.schema_version() == int(SCHEMA_VERSION)
    with database.connect() as conn:
        assert MEMORY_TABLES <= _tables(conn)


def test_migration_four_upgrades_a_version_two_database_with_comparisons_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实世界存在"版本号仍是 2、但 comparisons 表已经建好"的旧库。"""
    database = EvaluationDatabase(tmp_path / "evaluation.db")
    _init_at_version(database, "2", monkeypatch)
    with database.connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS comparisons ("
            " id TEXT PRIMARY KEY, scope TEXT NOT NULL, baseline_run_id TEXT NOT NULL,"
            " candidate_run_id TEXT NOT NULL, mode TEXT NOT NULL, compatibility TEXT NOT NULL,"
            " result_json TEXT, created_at TEXT NOT NULL)"
        )
    database.init_schema()
    assert database.schema_version() == int(SCHEMA_VERSION)
    with database.connect() as conn:
        assert MEMORY_TABLES <= _tables(conn)
        assert "comparisons" in _tables(conn)


def test_migration_four_keeps_existing_run_payload_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """升级前的 run/result_json 必须逐字不变。"""
    payload = '{"final_answer": "青岚城", "usage": {"total_tokens": 12}}'
    database = EvaluationDatabase(tmp_path / "evaluation.db")
    _init_at_version(database, "3", monkeypatch)
    _seed_legacy_run(database, payload)
    with database.connect() as conn:
        before_run = conn.execute(
            "SELECT snapshot_json FROM runs WHERE id = 'r1'"
        ).fetchone()[0]
    database.init_schema()
    with database.connect() as conn:
        after_run = conn.execute(
            "SELECT snapshot_json FROM runs WHERE id = 'r1'"
        ).fetchone()[0]
        after_result = conn.execute(
            "SELECT result_json FROM attempts WHERE id = 'a1'"
        ).fetchone()[0]
    assert after_run == before_run
    assert after_result == payload


def test_migration_four_adds_nullable_memory_ref_column_to_reviews(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = EvaluationDatabase(tmp_path / "evaluation.db")
    _init_at_version(database, "3", monkeypatch)
    database.init_schema()
    with database.connect() as conn:
        columns = {
            row["name"]: row for row in conn.execute("PRAGMA table_info(reviews)")
        }
    assert "memory_ref_json" in columns
    assert columns["memory_ref_json"]["notnull"] == 0


def test_migration_four_is_idempotent_when_column_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """契约 §7 / plan §3 Step 4：迁移 4 重复执行无副作用。

    迁移器只按 ``_schema_meta.version`` 跳过已执行版本；版本号落后于实际结构的
    旧库（手工改过版本号、或迁移中途失败留下半套结构）会让迁移 4 重跑，而
    ``ALTER TABLE ... ADD COLUMN`` 没有 IF NOT EXISTS，裸写会抛
    ``duplicate column name``。
    """
    database = EvaluationDatabase(tmp_path / "evaluation.db")
    _init_at_version(database, "3", monkeypatch)
    with database.connect() as conn:
        conn.execute("ALTER TABLE reviews ADD COLUMN memory_ref_json TEXT")

    database.init_schema()
    assert database.schema_version() == int(SCHEMA_VERSION)

    # 已经升到当前版本的库再初始化一次，结构不得变化。
    database.init_schema()
    assert database.schema_version() == int(SCHEMA_VERSION)
    with database.connect() as conn:
        columns = [
            row["name"] for row in conn.execute("PRAGMA table_info(reviews)")
        ]
    assert columns.count("memory_ref_json") == 1


def test_memory_scenarios_reject_orphan_dataset_revision(
    evaluation_database: EvaluationDatabase,
) -> None:
    with evaluation_database.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO memory_scenarios (dataset_revision_id, scenario_id, revision,"
                " split, template_family, cluster_id, execution_json, gold_json, created_at)"
                " VALUES ('missing-revision', 'fact-001', 1, 'regression', 't', 'c',"
                " '{}', '{}', 't0')"
            )


def test_memory_experiment_runs_reject_duplicate_batch(
    evaluation_database: EvaluationDatabase,
) -> None:
    """contract §7：同一实验的同一候选同一批次只能有一条子 run。"""
    with evaluation_database.connect() as conn:
        conn.execute(
            "INSERT INTO suites (id, revision, name, description, tags_json,"
            " is_deleted, created_at, updated_at)"
            " VALUES ('s1', 1, 's', '', '[]', 0, 't0', 't0')"
        )
        conn.execute(
            "INSERT INTO memory_dataset_revisions (id, suite_id, dataset_version, status,"
            " split_counts_json, source_json, definition_digest, created_at, updated_at)"
            " VALUES ('dr1', 's1', 'v1', 'ready', '{}', '{}', 'd', 't0', 't0')"
        )
        conn.execute(
            "INSERT INTO memory_experiments (id, suite_id, dataset_revision_id, split, track,"
            " candidates_json, repetitions, model_mode, budgets_json, primary_metric,"
            " policy_json, manifest_json, status, measurement_status, planned_trials,"
            " created_at, updated_at)"
            " VALUES ('e1', 's1', 'dr1', 'regression', 'end_to_end', '[]', 1, 'real',"
            " '{}', 'qa_accuracy', '{}', '{}', 'queued', 'incomplete', 1, 't0', 't0')"
        )
        conn.execute(
            "INSERT INTO memory_experiment_runs (id, experiment_id, candidate_id,"
            " batch_index, run_id, scenario_ids_json, status, created_at)"
            " VALUES ('er1', 'e1', 'cand-1', 0, 'run-1', '[]', 'queued', 't0')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO memory_experiment_runs (id, experiment_id, candidate_id,"
                " batch_index, run_id, scenario_ids_json, status, created_at)"
                " VALUES ('er2', 'e1', 'cand-1', 0, 'run-2', '[]', 'queued', 't0')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO memory_experiment_runs (id, experiment_id, candidate_id,"
                " batch_index, run_id, scenario_ids_json, status, created_at)"
                " VALUES ('er3', 'e1', 'cand-2', 0, 'run-1', '[]', 'queued', 't0')"
            )
        count = conn.execute(
            "SELECT COUNT(*) FROM memory_experiment_runs"
        ).fetchone()[0]
    assert count == 1


def test_concurrent_same_key_insert_does_not_duplicate_rows(tmp_path: Path) -> None:
    """并发写同一个唯一键不得产生重复行（contract §7）。"""
    path = tmp_path / "evaluation.db"
    seed = EvaluationDatabase(path)
    seed.init_schema()
    with seed.connect() as conn:
        conn.execute(
            "INSERT INTO suites (id, revision, name, description, tags_json,"
            " is_deleted, created_at, updated_at)"
            " VALUES ('s1', 1, 's', '', '[]', 0, 't0', 't0')"
        )
        conn.execute(
            "INSERT INTO memory_dataset_revisions (id, suite_id, dataset_version, status,"
            " split_counts_json, source_json, definition_digest, created_at, updated_at)"
            " VALUES ('dr1', 's1', 'v1', 'ready', '{}', '{}', 'd', 't0', 't0')"
        )
        conn.execute(
            "INSERT INTO memory_experiments (id, suite_id, dataset_revision_id, split, track,"
            " candidates_json, repetitions, model_mode, budgets_json, primary_metric,"
            " policy_json, manifest_json, status, measurement_status, planned_trials,"
            " created_at, updated_at)"
            " VALUES ('e1', 's1', 'dr1', 'regression', 'end_to_end', '[]', 1, 'real',"
            " '{}', 'qa_accuracy', '{}', '{}', 'queued', 'incomplete', 1, 't0', 't0')"
        )
    seed.close()

    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def _worker(index: int) -> None:
        database = EvaluationDatabase(path)
        try:
            barrier.wait(timeout=10)
            with database.connect(immediate=True) as conn:
                conn.execute(
                    "INSERT INTO memory_experiment_runs (id, experiment_id, candidate_id,"
                    " batch_index, run_id, scenario_ids_json, status, created_at)"
                    " VALUES (?, 'e1', 'cand-1', 0, ?, '[]', 'queued', 't0')",
                    (f"er{index}", f"run-{index}"),
                )
            outcomes.append("inserted")
        except sqlite3.IntegrityError:
            outcomes.append("rejected")
        finally:
            database.close()

    threads = [threading.Thread(target=_worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes) == ["inserted", "rejected"]
    check = EvaluationDatabase(path)
    try:
        with check.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM memory_experiment_runs"
            ).fetchone()[0]
    finally:
        check.close()
    assert count == 1

