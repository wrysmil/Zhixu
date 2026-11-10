"""评估域 SQLite 入口：连接、schema 迁移与串行执行器。

契约/plan §3 的三条硬约束都落在这里：

- **schema_version 迁移器**：结构只增不改；库版本比代码新时直接拒绝启动，
  绝不在未知结构上继续写。
- **连接口径固定**：``foreign_keys=ON`` / ``busy_timeout=5000`` / ``journal_mode=WAL``，
  且每次连接都显式 ``BEGIN``/``COMMIT``（``isolation_level=None``），事务边界不交给
  sqlite3 的隐式管理。
- **同步操作离开事件循环**：HTTP 层通过 :meth:`arun` 把仓储调用丢进单线程执行器，
  串行执行的同时不阻塞 aiohttp。

本模块只管连接与事务原语；业务事务的边界归 ``repository.py``。
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import ParamSpec, TypeVar

SCHEMA_VERSION = "5"

BUSY_TIMEOUT_MS = 5000

P = ParamSpec("P")
T = TypeVar("T")

#: 迁移条目：普通 DDL 字符串，或需要按库现状判断的幂等步骤（callable）。
#: ``ALTER TABLE ... ADD COLUMN`` 没有 IF NOT EXISTS，重复执行会抛
#: ``duplicate column name``；迁移器只按 ``_schema_meta.version`` 跳过已执行版本，
#: 遇到"版本号落后于实际结构"的旧库（手工改过版本号、或迁移中途失败留下半套结构）
#: 会重跑该版本，这类步骤必须自带存在性判断。
_MigrationStatement = str | Callable[[sqlite3.Connection], None]


def _add_memory_ref_column(conn: sqlite3.Connection) -> None:
    """幂等地给 reviews 追加可空的 memory_ref_json 列（契约 §7）。

    列已存在时直接跳过，重复执行无副作用。
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(reviews)")}
    if "memory_ref_json" in columns:
        return
    conn.execute("ALTER TABLE reviews ADD COLUMN memory_ref_json TEXT")


#: 每个版本的 DDL 一次性建齐（首版即 14 张表，无中间态）。新增版本只能追加新元组，
#: 绝不能改写已发布版本——否则已部署的库会被按新结构解读。
_MIGRATIONS: tuple[tuple[str, tuple[_MigrationStatement, ...]], ...] = (
    (
        "1",
        (
            # ----- 版本元信息 -----
            "CREATE TABLE IF NOT EXISTS _schema_meta ("
            " key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            # ----- 用例集与用例 -----
            """
            CREATE TABLE IF NOT EXISTS suites (
              id          TEXT    PRIMARY KEY,
              revision    INTEGER NOT NULL DEFAULT 1,
              name        TEXT    NOT NULL,
              description TEXT    NOT NULL DEFAULT '',
              tags_json   TEXT    NOT NULL DEFAULT '[]',
              is_deleted  INTEGER NOT NULL DEFAULT 0,
              created_at  TEXT    NOT NULL,
              updated_at  TEXT    NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS cases (
              id              TEXT    PRIMARY KEY,
              suite_id        TEXT    NOT NULL,
              latest_revision INTEGER NOT NULL DEFAULT 1,
              is_deleted      INTEGER NOT NULL DEFAULT 0,
              created_at      TEXT    NOT NULL,
              updated_at      TEXT    NOT NULL,
              FOREIGN KEY (suite_id) REFERENCES suites(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS cases_suite_idx ON cases (suite_id, is_deleted)",
            # definition_json 是不可变快照：允许插入，不允许原地 UPDATE/DELETE，
            # 否则运行中途改用例会连带改掉已经冻结的历史正文。
            """
            CREATE TABLE IF NOT EXISTS case_revisions (
              case_id         TEXT    NOT NULL,
              revision        INTEGER NOT NULL,
              definition_json TEXT    NOT NULL,
              created_at      TEXT    NOT NULL,
              PRIMARY KEY (case_id, revision),
              FOREIGN KEY (case_id) REFERENCES cases(id)
            )
            """,
            """
            CREATE TRIGGER IF NOT EXISTS case_revisions_no_update
            BEFORE UPDATE ON case_revisions
            BEGIN
              SELECT RAISE(ABORT, 'CASE_REVISION_IMMUTABLE');
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS case_revisions_no_delete
            BEFORE DELETE ON case_revisions
            BEGIN
              SELECT RAISE(ABORT, 'CASE_REVISION_IMMUTABLE');
            END
            """,
            # ----- 运行与尝试 -----
            """
            CREATE TABLE IF NOT EXISTS runs (
              id           TEXT    PRIMARY KEY,
              suite_id     TEXT    NOT NULL,
              status       TEXT    NOT NULL DEFAULT 'queued',
              snapshot_json TEXT   NOT NULL DEFAULT '{}',
              request_hash TEXT    NOT NULL,
              parent_run_id TEXT,
              is_deleted   INTEGER NOT NULL DEFAULT 0,
              created_at   TEXT    NOT NULL,
              started_at   TEXT,
              finished_at  TEXT,
              FOREIGN KEY (suite_id) REFERENCES suites(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS runs_queue_idx"
            " ON runs (status, created_at)",
            """
            CREATE TABLE IF NOT EXISTS attempts (
              id                TEXT    PRIMARY KEY,
              run_id            TEXT    NOT NULL,
              position          INTEGER NOT NULL,
              case_id           TEXT    NOT NULL,
              case_revision     INTEGER NOT NULL,
              case_snapshot_json TEXT   NOT NULL,
              execution_status  TEXT    NOT NULL DEFAULT 'queued',
              grading_status    TEXT    NOT NULL DEFAULT 'pending',
              verdict           TEXT    NOT NULL DEFAULT 'not_evaluated',
              result_json       TEXT,
              pid               INTEGER,
              process_nonce     TEXT,
              started_at        TEXT,
              finished_at       TEXT,
              UNIQUE (run_id, position),
              FOREIGN KEY (run_id) REFERENCES runs(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS attempts_claim_idx"
            " ON attempts (execution_status, run_id, position)",
            # ----- 幂等 -----
            # 记录与运行同样长：软删运行不释放 key，否则重放会命中已删对象。
            """
            CREATE TABLE IF NOT EXISTS idempotency (
              scope         TEXT NOT NULL,
              endpoint      TEXT NOT NULL,
              key           TEXT NOT NULL,
              request_hash  TEXT NOT NULL,
              run_id        TEXT,
              response_json TEXT,
              created_at    TEXT NOT NULL,
              PRIMARY KEY (scope, endpoint, key)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idempotency_run_idx ON idempotency (run_id)",
            # ----- 样本导入 -----
            """
            CREATE TABLE IF NOT EXISTS sample_imports (
              sample_id  TEXT    PRIMARY KEY,
              suite_id   TEXT    NOT NULL,
              created_at TEXT    NOT NULL,
              FOREIGN KEY (suite_id) REFERENCES suites(id)
            )
            """,
            # ----- 人工复核与产物（只追加） -----
            """
            CREATE TABLE IF NOT EXISTS reviews (
              id         TEXT    PRIMARY KEY,
              attempt_id TEXT    NOT NULL,
              verdict    TEXT    NOT NULL,
              comment    TEXT    NOT NULL DEFAULT '',
              reviewer   TEXT    NOT NULL,
              created_at TEXT    NOT NULL,
              FOREIGN KEY (attempt_id) REFERENCES attempts(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS reviews_attempt_idx ON reviews (attempt_id)",
            """
            CREATE TABLE IF NOT EXISTS artifacts (
              id            TEXT    PRIMARY KEY,
              attempt_id    TEXT    NOT NULL,
              relative_path TEXT    NOT NULL,
              media_type    TEXT    NOT NULL,
              size_bytes    INTEGER NOT NULL,
              sha256        TEXT    NOT NULL,
              FOREIGN KEY (attempt_id) REFERENCES attempts(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS artifacts_attempt_idx ON artifacts (attempt_id)",
            # ----- 人工确认的 gold 样本与校准 -----
            """
            CREATE TABLE IF NOT EXISTS gold_samples (
              id           TEXT    PRIMARY KEY,
              revision     INTEGER NOT NULL,
              definition_json TEXT NOT NULL,
              label_status TEXT    NOT NULL DEFAULT 'proposed',
              reviewer     TEXT,
              comment      TEXT    NOT NULL DEFAULT '',
              updated_at   TEXT    NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS gold_state (
              id       INTEGER PRIMARY KEY CHECK (id = 1),
              revision INTEGER NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS calibration_results (
              id                TEXT    PRIMARY KEY,
              judge_fingerprint TEXT    NOT NULL,
              rubric_version    TEXT    NOT NULL,
              gold_revision     INTEGER NOT NULL,
              total             INTEGER NOT NULL,
              matched           INTEGER NOT NULL,
              agreement         REAL,
              passed            INTEGER,
              created_at        TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS calibration_results_lookup_idx"
            " ON calibration_results (judge_fingerprint, rubric_version, gold_revision)",
            """
            CREATE TABLE IF NOT EXISTS calibration_jobs (
              id           TEXT    PRIMARY KEY,
              status       TEXT    NOT NULL DEFAULT 'queued',
              snapshot_json TEXT   NOT NULL,
              result_json  TEXT,
              created_at   TEXT    NOT NULL,
              started_at   TEXT,
              finished_at  TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS calibration_items (
              id             TEXT    PRIMARY KEY,
              job_id         TEXT    NOT NULL,
              position       INTEGER NOT NULL,
              status         TEXT    NOT NULL DEFAULT 'queued',
              result_json    TEXT,
              pid            INTEGER,
              process_nonce  TEXT,
              UNIQUE (job_id, position),
              FOREIGN KEY (job_id) REFERENCES calibration_jobs(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS calibration_items_claim_idx"
            " ON calibration_items (status, job_id, position)",
        ),
    ),
    (
        "2",
        (
            "ALTER TABLE attempts ADD COLUMN trial_index INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE attempts ADD COLUMN source_attempt_id TEXT",
            "CREATE UNIQUE INDEX IF NOT EXISTS attempts_run_case_trial_idx"
            " ON attempts (run_id, case_id, trial_index)",
        ),
    ),
    (
        "3",
        (
            """
            CREATE TABLE IF NOT EXISTS comparisons (
              id              TEXT    PRIMARY KEY,
              scope           TEXT    NOT NULL,
              baseline_run_id TEXT    NOT NULL,
              candidate_run_id TEXT   NOT NULL,
              mode            TEXT    NOT NULL,
              compatibility   TEXT    NOT NULL,
              result_json     TEXT,
              created_at      TEXT    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS comparisons_scope_idx"
            " ON comparisons (scope, created_at)",
        ),
    ),
    (
        "4",
        (
            # ----- 记忆专项：集合版本 -----
            """
            CREATE TABLE IF NOT EXISTS memory_dataset_revisions (
              id                TEXT    PRIMARY KEY,
              suite_id          TEXT    NOT NULL,
              dataset_version   TEXT    NOT NULL,
              status            TEXT    NOT NULL DEFAULT 'staging',
              split_counts_json TEXT    NOT NULL DEFAULT '{}',
              source_json       TEXT    NOT NULL DEFAULT '{}',
              definition_digest TEXT    NOT NULL,
              created_at        TEXT    NOT NULL,
              updated_at        TEXT    NOT NULL,
              UNIQUE (suite_id, dataset_version),
              FOREIGN KEY (suite_id) REFERENCES suites(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS memory_dataset_revisions_status_idx"
            " ON memory_dataset_revisions (suite_id, status, created_at)",
            # execution_json 与 gold_json 分列存储：执行端只读前者。
            """
            CREATE TABLE IF NOT EXISTS memory_scenarios (
              dataset_revision_id TEXT    NOT NULL,
              scenario_id         TEXT    NOT NULL,
              revision            INTEGER NOT NULL,
              case_id             TEXT,
              case_revision       INTEGER,
              split               TEXT    NOT NULL,
              template_family     TEXT    NOT NULL,
              cluster_id          TEXT    NOT NULL,
              execution_json      TEXT    NOT NULL,
              gold_json           TEXT    NOT NULL,
              created_at          TEXT    NOT NULL,
              PRIMARY KEY (dataset_revision_id, scenario_id, revision),
              UNIQUE (case_id, case_revision),
              FOREIGN KEY (dataset_revision_id) REFERENCES memory_dataset_revisions(id),
              FOREIGN KEY (case_id) REFERENCES cases(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS memory_scenarios_lookup_idx"
            " ON memory_scenarios (dataset_revision_id, scenario_id, revision)",
            # ----- 记忆专项：导入作业 -----
            """
            CREATE TABLE IF NOT EXISTS memory_import_jobs (
              id                  TEXT    PRIMARY KEY,
              suite_id            TEXT    NOT NULL,
              status              TEXT    NOT NULL DEFAULT 'staging',
              expected_count      INTEGER,
              observed_count      INTEGER,
              dataset_revision_id TEXT,
              source_json         TEXT    NOT NULL DEFAULT '{}',
              errors_json         TEXT    NOT NULL DEFAULT '[]',
              created_at          TEXT    NOT NULL,
              updated_at          TEXT    NOT NULL,
              FOREIGN KEY (suite_id) REFERENCES suites(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS memory_import_jobs_status_idx"
            " ON memory_import_jobs (suite_id, status, created_at)",
            # ----- 记忆专项：实验与子 run -----
            """
            CREATE TABLE IF NOT EXISTS memory_experiments (
              id                  TEXT    PRIMARY KEY,
              suite_id            TEXT    NOT NULL,
              dataset_revision_id TEXT    NOT NULL,
              split               TEXT    NOT NULL,
              track               TEXT    NOT NULL,
              candidates_json     TEXT    NOT NULL DEFAULT '[]',
              repetitions         INTEGER NOT NULL DEFAULT 1,
              model_mode          TEXT    NOT NULL DEFAULT 'real',
              judge_profile_id    TEXT,
              budgets_json        TEXT    NOT NULL DEFAULT '{}',
              primary_metric      TEXT    NOT NULL,
              policy_json         TEXT    NOT NULL DEFAULT '{}',
              manifest_json       TEXT    NOT NULL DEFAULT '{}',
              status              TEXT    NOT NULL DEFAULT 'queued',
              measurement_status  TEXT    NOT NULL DEFAULT 'incomplete',
              planned_trials      INTEGER NOT NULL DEFAULT 0,
              plan_key            TEXT,
              created_at          TEXT    NOT NULL,
              updated_at          TEXT    NOT NULL,
              FOREIGN KEY (suite_id) REFERENCES suites(id),
              FOREIGN KEY (dataset_revision_id) REFERENCES memory_dataset_revisions(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS memory_experiments_status_idx"
            " ON memory_experiments (suite_id, status, created_at)",
            # 计划幂等键：同一 (suite_id, plan_key) 只允许一条实验计划。
            "CREATE UNIQUE INDEX IF NOT EXISTS memory_experiments_plan_key_idx"
            " ON memory_experiments (suite_id, plan_key)",
            """
            CREATE TABLE IF NOT EXISTS memory_experiment_runs (
              id                 TEXT    PRIMARY KEY,
              experiment_id      TEXT    NOT NULL,
              candidate_id       TEXT    NOT NULL,
              batch_index        INTEGER NOT NULL,
              run_id             TEXT    NOT NULL UNIQUE,
              scenario_ids_json  TEXT    NOT NULL DEFAULT '[]',
              status             TEXT    NOT NULL DEFAULT 'queued',
              created_at         TEXT    NOT NULL,
              UNIQUE (experiment_id, candidate_id, batch_index),
              FOREIGN KEY (experiment_id) REFERENCES memory_experiments(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS memory_experiment_runs_experiment_idx"
            " ON memory_experiment_runs (experiment_id, candidate_id, batch_index)",
            # ----- 记忆专项：配对比较（结果与引用快照一起冻结） -----
            """
            CREATE TABLE IF NOT EXISTS memory_comparisons (
              id                       TEXT    PRIMARY KEY,
              baseline_experiment_id    TEXT    NOT NULL,
              candidate_experiment_id   TEXT    NOT NULL,
              baseline_candidate_id     TEXT    NOT NULL,
              candidate_candidate_id    TEXT    NOT NULL,
              changed_fields_json       TEXT    NOT NULL DEFAULT '[]',
              policy_json               TEXT    NOT NULL DEFAULT '{}',
              status                    TEXT    NOT NULL DEFAULT 'pending',
              result_json               TEXT    NOT NULL DEFAULT '{}',
              snapshot_json             TEXT    NOT NULL DEFAULT '{}',
              created_at                TEXT    NOT NULL,
              FOREIGN KEY (baseline_experiment_id) REFERENCES memory_experiments(id),
              FOREIGN KEY (candidate_experiment_id) REFERENCES memory_experiments(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS memory_comparisons_created_idx"
            " ON memory_comparisons (created_at)",
            # ----- 逐 probe 复核引用（旧记录保持 NULL） -----
            # 幂等步骤而非裸 ALTER：列已存在时跳过（契约 §7 要求迁移 4 可重复执行）。
            _add_memory_ref_column,
        ),
    ),
    (
        "5",
        (
            # ----- 知识检索专项：数据集 -----
            """
            CREATE TABLE IF NOT EXISTS knowledge_eval_datasets (
              id         TEXT    PRIMARY KEY,
              name       TEXT    NOT NULL,
              notes      TEXT    NOT NULL DEFAULT '',
              revision   INTEGER NOT NULL DEFAULT 1,
              cases_json TEXT    NOT NULL DEFAULT '[]',
              created_at TEXT    NOT NULL,
              updated_at TEXT    NOT NULL
            )
            """,
            # ----- 知识检索专项：运行 -----
            """
            CREATE TABLE IF NOT EXISTS knowledge_eval_runs (
              id            TEXT    PRIMARY KEY,
              dataset_id    TEXT    NOT NULL,
              status        TEXT    NOT NULL DEFAULT 'queued',
              configs_json  TEXT    NOT NULL DEFAULT '[]',
              judge_json    TEXT    NOT NULL DEFAULT '{}',
              budgets_json  TEXT    NOT NULL DEFAULT '{}',
              metrics_json  TEXT    NOT NULL DEFAULT '{}',
              pool_json     TEXT    NOT NULL DEFAULT '{}',
              progress_json TEXT    NOT NULL DEFAULT '{}',
              error         TEXT,
              created_at    TEXT    NOT NULL,
              finished_at   TEXT,
              FOREIGN KEY (dataset_id) REFERENCES knowledge_eval_datasets(id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS knowledge_eval_runs_status_idx"
            " ON knowledge_eval_runs (dataset_id, status, created_at)",
            # ----- 知识检索专项：逐题结果 -----
            # case_id 是「用例 id::配置 id」复合形式：同一条查询在多个检索配置下
            # 各存一行，PRIMARY KEY (run_id, case_id) 让重跑天然幂等。
            """
            CREATE TABLE IF NOT EXISTS knowledge_eval_results (
              run_id       TEXT NOT NULL,
              case_id      TEXT NOT NULL,
              status       TEXT NOT NULL,
              label_source TEXT NOT NULL,
              hits_json    TEXT NOT NULL DEFAULT '[]',
              metrics_json TEXT NOT NULL DEFAULT '{}',
              error        TEXT,
              PRIMARY KEY (run_id, case_id),
              FOREIGN KEY (run_id) REFERENCES knowledge_eval_runs(id)
            )
            """,
        ),
    ),
)

def _read_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = '_schema_meta'"
    ).fetchone()
    if row is None:
        return None
    record = conn.execute(
        "SELECT value FROM _schema_meta WHERE key = 'version'"
    ).fetchone()
    return None if record is None else int(record[0])


class EvaluationDatabase:
    """评估域唯一 SQLite 入口：持有连接口径、迁移器与串行执行器。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        # RLock 而非 Lock：仓储方法在同线程内不得重入 connect()，但异常路径上的
        # 嵌套重入会直接自锁死；RLK 至少把这类错误变成可见的死锁而不是静默卡住。
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="evaluation-db"
        )
        self._closed = False

    @contextmanager
    def connect(self, *, immediate: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """取一个连接并包住一个显式事务。

        ``immediate=True`` 在 BEGIN 阶段就拿写锁，这是 ``claim_attempt`` 能原子领取的
        前提；普通读路径用默认的延迟 BEGIN。
        """
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, isolation_level=None)
            try:
                # journal_mode 不能在事务内设置，必须先落 pragma 再 BEGIN。
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                try:
                    yield conn
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
            finally:
                conn.close()

    def init_schema(self) -> None:
        """把库推进到当前代码版本；已是最新则无操作。

        迁移条目必须幂等：跳过与否只看 ``_schema_meta.version``，当版本号落后于
        实际结构时同一版本会被重跑（契约 §7）。
        """
        with self.connect() as conn:
            current = _read_version(conn)
            if current is not None and current > int(SCHEMA_VERSION):
                raise RuntimeError(
                    f"SCHEMA_VERSION_TOO_NEW db={current} code={SCHEMA_VERSION}"
                )
            for version, statements in _MIGRATIONS:
                if current is not None and int(version) <= current:
                    continue
                for statement in statements:
                    # 条目可以是 DDL 字符串，也可以是需要按库现状判断的幂等步骤。
                    if callable(statement):
                        statement(conn)
                    else:
                        conn.execute(statement)
            conn.execute(
                "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES ('version', ?)",
                (SCHEMA_VERSION,),
            )

    def schema_version(self) -> int | None:
        with self.connect() as conn:
            return _read_version(conn)

    async def arun(self, func: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """在单线程执行器里跑同步仓储调用，异常原样抛回调用方。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, lambda: func(*args, **kwargs)
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=True)
