"""团队域 SQLite 入口：连接管理、schema 初始化、writer 独占锁。

口径沿用 ``nanobot/writing/database.py``（同一套 PRAGMA + 单进程串行化 + 惰性建目录），
但按本功能契约补两点：``busy_timeout``（契约 §2 要求）与 **workspace 独占 writer 锁**
（契约 §6「暂不支持多 gateway 同 workspace 调度，第二 writer 拒绝启动团队调度」）。

**读路径不落盘**：``connect(create=False)`` 是默认，库文件不存在时抛
:class:`DatabaseAbsent`，由仓储层把它翻译成「空列表 / 未找到」。团队域因此可以在
只读调用（列团队、读历史、配置校验）时不在用户磁盘上留下空 ``teams/`` 目录。

库路径唯一出口是 ``get_teams_dir``（契约 §2 的 ``<agent-workspace>/teams/state.db``）；
``db_path`` 参数只服务测试，生产调用方不能指定任意 DB 路径。
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from nanobot.config.paths import get_teams_dir

SCHEMA_VERSION = "1"
DEFAULT_BUSY_TIMEOUT_MS = 5000

# writer 锁文件名放在库目录里，与库文件同生命周期。
WRITER_LOCK_NAME = "state.db.writer.lock"


class DatabaseAbsent(RuntimeError):  # noqa: N818  # 语义是「无数据」而非「出错」，Error 后缀会误导
    """库文件还不存在：只读调用方应当把它当作「没有任何团队数据」，而不是错误。"""


class WriterBusy(RuntimeError):  # noqa: N818  # 与契约 §3 的 503 语义对齐，由仓储层翻译
    """另一个进程已经持有该 workspace 的团队调度 writer 锁。"""


class StorageUnavailable(RuntimeError):  # noqa: N818  # 对应契约 §3 的 503，名称即错误码口径
    """持久层不可用：写锁超时或库被别的连接占住。

    放在数据库层是因为只有这儿能区分「SQLite 忙」与领域冲突；仓储层直接复用同一类型，
    避免调用方要按异常来自哪个模块分支处理。
    """


def compute_workspace_key(workspace: Path | str) -> str:
    """工作区作用域键：同一路径稳定同值，客户端无法伪造。

    与 ``nanobot.writing.database.compute_workspace_key`` 同口径；这里独立实现是为了
    不让团队域依赖写作域（两个域的版本迁移节奏不同）。
    """
    resolved = Path(workspace).expanduser().resolve()
    return hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]


def _is_lock_error(exc: sqlite3.OperationalError) -> bool:
    """只把「被别人占住」当成存储不可用；schema/SQL 错误必须原样暴露。"""
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS teams (
      workspace_key TEXT    NOT NULL,
      team_id       TEXT    NOT NULL,
      revision      INTEGER NOT NULL,
      archived      INTEGER NOT NULL DEFAULT 0,
      name          TEXT    NOT NULL DEFAULT '',
      payload       TEXT    NOT NULL,
      created_at    TEXT    NOT NULL,
      updated_at    TEXT    NOT NULL,
      PRIMARY KEY (workspace_key, team_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS teams_updated_idx"
    " ON teams (workspace_key, updated_at DESC, team_id)",
    """
    CREATE TABLE IF NOT EXISTS team_revisions (
      workspace_key TEXT    NOT NULL,
      team_id       TEXT    NOT NULL,
      revision      INTEGER NOT NULL,
      snapshot      TEXT    NOT NULL,
      created_at    TEXT    NOT NULL,
      PRIMARY KEY (workspace_key, team_id, revision)
    )
    """,
    # 契约 §2：修订历史只追加，不原地修改。用触发器把它变成数据库级不变量，
    # 这样任何绕过仓储层的写入也改不掉已冻结的执行上下文。
    """
    CREATE TRIGGER IF NOT EXISTS team_revisions_no_update
    BEFORE UPDATE ON team_revisions
    BEGIN
      SELECT RAISE(ABORT, 'team_revisions is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS team_revisions_no_delete
    BEFORE DELETE ON team_revisions
    BEGIN
      SELECT RAISE(ABORT, 'team_revisions is append-only');
    END
    """,
    """
    CREATE TABLE IF NOT EXISTS team_runs (
      run_id           TEXT    PRIMARY KEY,
      workspace_key    TEXT    NOT NULL,
      session_key      TEXT    NOT NULL,
      chat_id          TEXT    NOT NULL,
      turn_id          TEXT    NOT NULL,
      internal_turn_id TEXT    NOT NULL,
      team_id          TEXT    NOT NULL,
      team_revision    INTEGER NOT NULL,
      status           TEXT    NOT NULL,
      leader_action    TEXT    NOT NULL DEFAULT 'planning',
      last_seq         INTEGER NOT NULL DEFAULT 0,
      revision_snapshot TEXT   NOT NULL,
      started_at       TEXT    NOT NULL,
      finished_at      TEXT,
      parent_run_id    TEXT
    )
    """,
    # 契约 §2：同一 sessionKey + 可见 turnId 最多一个运行。
    "CREATE UNIQUE INDEX IF NOT EXISTS team_runs_turn_idx"
    " ON team_runs (workspace_key, session_key, turn_id)",
    "CREATE INDEX IF NOT EXISTS team_runs_chat_idx"
    " ON team_runs (workspace_key, chat_id, started_at DESC, run_id)",
    """
    CREATE TABLE IF NOT EXISTS team_tasks (
      task_id       TEXT    PRIMARY KEY,
      workspace_key TEXT    NOT NULL,
      run_id        TEXT    NOT NULL REFERENCES team_runs (run_id) ON DELETE CASCADE,
      batch_id      TEXT,
      member_id     TEXT    NOT NULL,
      agent_id      TEXT    NOT NULL,
      title         TEXT    NOT NULL,
      acceptance    TEXT    NOT NULL,
      instruction   TEXT    NOT NULL DEFAULT '',
      depends_on    TEXT    NOT NULL DEFAULT '[]',
      resource_keys TEXT    NOT NULL DEFAULT '[]',
      status        TEXT    NOT NULL,
      attempt_id    TEXT,
      last_action   TEXT,
      started_at    TEXT,
      finished_at   TEXT,
      result        TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS team_tasks_run_idx ON team_tasks (run_id)",
    # 契约 §2：归属（属于哪个 run / 哪个成员槽位）创建后不可改。
    """
    CREATE TRIGGER IF NOT EXISTS team_tasks_ownership_no_update
    BEFORE UPDATE OF run_id, member_id, agent_id ON team_tasks
    BEGIN
      SELECT RAISE(ABORT, 'team task ownership is immutable');
    END
    """,
    """
    CREATE TABLE IF NOT EXISTS team_attempts (
      attempt_id    TEXT    PRIMARY KEY,
      workspace_key TEXT    NOT NULL,
      task_id       TEXT    NOT NULL REFERENCES team_tasks (task_id) ON DELETE CASCADE,
      run_id        TEXT    NOT NULL,
      outcome       TEXT    NOT NULL DEFAULT '',
      stop_reason   TEXT    NOT NULL DEFAULT '',
      error_code    TEXT,
      usage         TEXT,
      started_at    TEXT    NOT NULL,
      finished_at   TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS team_attempts_task_idx ON team_attempts (task_id)",
    """
    CREATE TABLE IF NOT EXISTS team_events (
      run_id      TEXT    NOT NULL,
      seq         INTEGER NOT NULL,
      type        TEXT    NOT NULL,
      task_id     TEXT,
      attempt_id  TEXT,
      occurred_at TEXT    NOT NULL,
      event       TEXT    NOT NULL,
      PRIMARY KEY (run_id, seq)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS team_dispatches (
      run_id       TEXT    NOT NULL,
      batch_id     TEXT    NOT NULL,
      payload_hash TEXT    NOT NULL,
      task_ids     TEXT    NOT NULL DEFAULT '[]',
      result       TEXT,
      created_at   TEXT    NOT NULL,
      PRIMARY KEY (run_id, batch_id)
    )
    """,
    # mutation 幂等表：requestId 是领域键，跨连接重连仍然只生效一次（契约 §3）。
    """
    CREATE TABLE IF NOT EXISTS team_requests (
      workspace_key TEXT    NOT NULL,
      request_id    TEXT    NOT NULL,
      op            TEXT    NOT NULL,
      payload_hash  TEXT    NOT NULL,
      response      TEXT    NOT NULL,
      created_at    TEXT    NOT NULL,
      PRIMARY KEY (workspace_key, request_id)
    )
    """,
    "CREATE TABLE IF NOT EXISTS _schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)


class TeamDatabase:
    """团队域 SQLite 入口；一个 workspace 一个实例。"""

    def __init__(self, workspace: Path | str, *, db_path: Path | None = None) -> None:
        self.workspace = Path(workspace)
        self.db_path = Path(db_path) if db_path is not None else get_teams_dir(self.workspace) / "state.db"
        self.workspace_key = compute_workspace_key(self.workspace)
        self.busy_timeout_ms = DEFAULT_BUSY_TIMEOUT_MS
        self._lock = threading.Lock()

    def exists(self) -> bool:
        return self.db_path.exists()

    @contextmanager
    def connect(self, *, create: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """取一个连接。``create=False`` 时库文件必须已存在，否则抛 :class:`DatabaseAbsent`。

        ``create=True`` 顺带保证 schema 就位（幂等 DDL），这样「新工作空间的第一次写入」
        不会因为调用方忘了 ``init_schema`` 而撞 ``no such table``。
        """
        if not create and not self.db_path.exists():
            raise DatabaseAbsent(str(self.db_path))
        with self._lock:
            if create:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._open()
            try:
                if create and not self._schema_ready(conn):
                    self._apply_schema(conn)
                yield conn
                conn.commit()
            except sqlite3.OperationalError as exc:
                conn.rollback()
                if _is_lock_error(exc):
                    raise StorageUnavailable(f"team storage is busy: {exc}") from exc
                raise
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=self.busy_timeout_ms / 1000)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _schema_ready(conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name = 'teams'"
        ).fetchone()
        return bool(row["n"])

    def _apply_schema(self, conn: sqlite3.Connection) -> None:
        for stmt in _SCHEMA_STATEMENTS:
            conn.execute(stmt)
        conn.execute(
            "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES (?, ?)",
            ("version", SCHEMA_VERSION),
        )

    def init_schema(self) -> None:
        with self.connect(create=True) as conn:
            self._apply_schema(conn)

    def ensure_schema(self) -> None:
        """幂等补建表与索引；DDL 全为 ``IF NOT EXISTS``，任何状态都能重跑。"""
        self.init_schema()

    @property
    def writer_lock_path(self) -> Path:
        return self.db_path.parent / WRITER_LOCK_NAME

    @contextmanager
    def claim_writer(self, *, timeout: float = 0.0) -> Generator[None, None, None]:
        """持有本 workspace 的团队调度独占锁。

        默认 ``timeout=0``：抢不到就立刻失败，而不是排队等一个可能永远不放手的
        前一个 gateway。未持锁的服务不得改写遗留运行（契约 §6）。
        """
        self.writer_lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(self.writer_lock_path), timeout=timeout)
        try:
            lock.acquire()
        except Timeout:
            raise WriterBusy(f"team writer already owned for {self.db_path.parent}") from None
        try:
            yield None
        finally:
            lock.release()
