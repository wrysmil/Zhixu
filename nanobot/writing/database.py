"""写作域 SQLite 入口：连接管理、schema 初始化、工作区作用域键。"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from nanobot.config.paths import get_writing_dir

SCHEMA_VERSION = "1"

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS articles (
      id             TEXT    NOT NULL,
      workspace_key  TEXT    NOT NULL,
      title          TEXT    NOT NULL,
      writing_type   TEXT    NOT NULL DEFAULT '',
      template_id    TEXT    NOT NULL DEFAULT '',
      status         TEXT    NOT NULL DEFAULT 'idea',
      summary        TEXT    NOT NULL DEFAULT '',
      brief          TEXT    NOT NULL DEFAULT '{}',
      body           TEXT    NOT NULL DEFAULT '',
      outline        TEXT    NOT NULL DEFAULT '[]',
      materials      TEXT    NOT NULL DEFAULT '[]',
      tags           TEXT    NOT NULL DEFAULT '[]',
      revision       INTEGER NOT NULL DEFAULT 1,
      content_digest TEXT    NOT NULL,
      created_at     TEXT    NOT NULL,
      updated_at     TEXT    NOT NULL,
      PRIMARY KEY (workspace_key, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS articles_updated_idx"
    " ON articles (workspace_key, updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS article_requests (
      request_id    TEXT NOT NULL,
      workspace_key TEXT NOT NULL,
      article_id    TEXT NOT NULL,
      op            TEXT NOT NULL,
      response      TEXT NOT NULL,
      created_at    TEXT NOT NULL,
      PRIMARY KEY (workspace_key, request_id)
    )
    """,
    "CREATE TABLE IF NOT EXISTS _schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)


def compute_workspace_key(workspace: Path | str) -> str:
    """工作区作用域键：同一路径稳定同值，客户端无法伪造。"""
    resolved = Path(workspace).expanduser().resolve()
    return hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]


class WritingDatabase:
    """写作域 SQLite 入口，模式与 ``nanobot/memory/database.py`` 一致。"""

    def __init__(self, workspace: Path, *, db_path: Path | None = None) -> None:
        self.workspace = Path(workspace)
        # 默认库路径的唯一出口是 get_writing_dir（契约 §2 的 <workspace>/writing/state.db）；
        # 本构造只做路径推导，不建目录——真正要连接时才建（见 connect）。
        self.db_path = (
            Path(db_path)
            if db_path is not None
            else get_writing_dir(self.workspace) / "state.db"
        )
        self.workspace_key = compute_workspace_key(self.workspace)
        self._lock = threading.Lock()

    @contextmanager  # pyright: ignore[reportDeprecated] 与 memory/database.py 同款连接模式
    def connect(self) -> Iterator[sqlite3.Connection]:
        """取一个 SQLite 连接；同一进程内由锁串行化，事务由调用方决定。"""
        with self._lock:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.row_factory = sqlite3.Row
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            for stmt in _SCHEMA_STATEMENTS:
                conn.execute(stmt)
            conn.execute(
                "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES (?, ?)",
                ("version", SCHEMA_VERSION),
            )

    def ensure_schema(self) -> None:
        """幂等补建表与索引；DDL 全为 IF NOT EXISTS，任何状态都能重跑。"""
        self.init_schema()
