"""SQLite 连接管理 + schema 初始化。"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from loguru import logger

from nanobot.memory.repository import (
    add_episode,
    add_memory,
    update_memory_source_episode,
)

_SCHEMA_VERSION = "3"

_SCHEMA_STATEMENTS = [
    # ----- _schema_meta -----
    """
    CREATE TABLE IF NOT EXISTS _schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    # ----- memories -----
    """
    CREATE TABLE IF NOT EXISTS memories (
        id              TEXT    PRIMARY KEY,
        content         TEXT    NOT NULL,
        type            TEXT    NOT NULL DEFAULT 'fact',
        priority        TEXT    NOT NULL DEFAULT 'long_term',
        source          TEXT    NOT NULL DEFAULT 'manual',
        importance_score REAL    NOT NULL DEFAULT 0.5,
        access_count    INTEGER NOT NULL DEFAULT 0,
        tags            TEXT    NOT NULL DEFAULT '[]',
        subject         TEXT    NOT NULL DEFAULT '',
        predicate       TEXT    NOT NULL DEFAULT '',
        confidence      REAL    NOT NULL DEFAULT 0.5,
        decay_rate      REAL    NOT NULL DEFAULT 0.1,
        expires_at      TEXT,
        last_accessed_at TEXT,
        superseded_by   TEXT,
        source_episode_id TEXT,
        scope           TEXT    NOT NULL DEFAULT 'global',
        scope_owner     TEXT    NOT NULL DEFAULT '',
        agent_id        TEXT    NOT NULL DEFAULT '',
        user_id         TEXT    NOT NULL DEFAULT 'default',
        workspace_id    TEXT    NOT NULL DEFAULT 'default',
        metadata        TEXT    NOT NULL DEFAULT '{}',
        created_at      TEXT    NOT NULL,
        updated_at      TEXT    NOT NULL,
        FOREIGN KEY (superseded_by)    REFERENCES memories(id),
        FOREIGN KEY (source_episode_id) REFERENCES episodes(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_memories_owner       ON memories(workspace_id, user_id, scope, scope_owner)",
    "CREATE INDEX IF NOT EXISTS idx_memories_type        ON memories(type)",
    "CREATE INDEX IF NOT EXISTS idx_memories_importance  ON memories(importance_score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_memories_created     ON memories(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_memories_episode     ON memories(source_episode_id)",
    "CREATE INDEX IF NOT EXISTS idx_memories_super       ON memories(superseded_by)",
    # ----- episodes -----
    """
    CREATE TABLE IF NOT EXISTS episodes (
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
        workspace_snapshot_id    TEXT NOT NULL DEFAULT '',
        workspace_id            TEXT NOT NULL DEFAULT 'default',
        metadata                 TEXT NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_episodes_session    ON episodes(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_time       ON episodes(started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_outcome    ON episodes(outcome)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_importance ON episodes(importance_score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_episodes_workspace  ON episodes(workspace_id)",
    # ----- scratchpad -----
    """
    CREATE TABLE IF NOT EXISTS scratchpad (
        user_id         TEXT    NOT NULL,
        workspace_id    TEXT    NOT NULL,
        content         TEXT    NOT NULL DEFAULT '',
        active_projects TEXT    NOT NULL DEFAULT '[]',
        current_focus   TEXT    NOT NULL DEFAULT '',
        open_questions  TEXT    NOT NULL DEFAULT '[]',
        next_steps      TEXT    NOT NULL DEFAULT '[]',
        updated_at      TEXT    NOT NULL,
        PRIMARY KEY (user_id, workspace_id)
    )
    """,
    # ----- memories_fts + triggers -----
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        content,
        subject,
        predicate,
        tags,
        content='memories',
        content_rowid='rowid',
        tokenize='unicode61 remove_diacritics 2'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, content, subject, predicate, tags)
        VALUES (new.rowid, new.content, new.subject, new.predicate, new.tags);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content, subject, predicate, tags)
        VALUES ('delete', old.rowid, old.content, old.subject, old.predicate, old.tags);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content, subject, predicate, tags)
        VALUES ('delete', old.rowid, old.content, old.subject, old.predicate, old.tags);
        INSERT INTO memories_fts(rowid, content, subject, predicate, tags)
        VALUES (new.rowid, new.content, new.subject, new.predicate, new.tags);
    END
    """,
    # ----- session_extraction_state -----
    # WU-A: idle 增量提取的 per-session 游标；由 ``MemoryExtractor.run_idle_extraction``
    # 读写；表名 ``session_extraction_state`` 已在全仓库 grep 过,无冲突。
    """
    CREATE TABLE IF NOT EXISTS session_extraction_state (
        session_key      TEXT    PRIMARY KEY,
        last_count       INTEGER NOT NULL,
        last_source      TEXT    NOT NULL,
        last_extracted_at TEXT   NOT NULL,
        updated_at       TEXT    NOT NULL
    )
    """,
    # ----- vector_sync_state -----
    # 向量索引增量同步游标（spec §5.2 Q2 选新表）。单行表：id 恒为 1。
    # 表名 vector_sync_state 已在全仓库 grep 过，无冲突。
    """
    CREATE TABLE IF NOT EXISTS vector_sync_state (
        id           INTEGER PRIMARY KEY CHECK (id = 1),
        cursor       TEXT    NOT NULL DEFAULT '',
        indexed      INTEGER NOT NULL DEFAULT 0,
        deleted      INTEGER NOT NULL DEFAULT 0,
        last_error   TEXT    NOT NULL DEFAULT '',
        updated_at   TEXT    NOT NULL
    )
    """,
]


def _execute_schema_statement(conn: sqlite3.Connection, stmt: str) -> None:
    """执行一条 schema 语句；索引语句遇到「列不存在」时降级跳过。

    旧库里可能存在**残缺**的表（例如只建了主键的 ``episodes``），按列建索引会
    抛 ``no such column``。索引缺失只影响查询性能、不影响正确性，因此这里降级
    为告警并继续——绝不让 schema 初始化失败把整个 gateway 卡在起不来。表结构
    本身的缺陷仍会在后续查询时暴露。

    非索引语句的异常照旧冒泡：那是真正的 schema 缺陷，必须让调用方看到。
    """
    try:
        conn.execute(stmt)
    except sqlite3.OperationalError as exc:
        if stmt.lstrip().upper().startswith("CREATE INDEX") and "no such column" in str(exc):
            logger.warning("memory schema index skipped (legacy table missing column): {}", exc)
            return
        raise


class MemoryDatabase:
    """nanobot 记忆系统的 SQLite 入口。

    负责连接管理、schema 初始化、迁移钩子。
    所有读写通过 :meth:`connect` 获取的 ``sqlite3.Connection`` 进行。
    """

    def __init__(self, workspace: Path, *, db_path: Path | None = None) -> None:
        self.workspace = Path(workspace)
        self.db_path = Path(db_path) if db_path is not None else (self.workspace / "memory" / "state.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # WU-3B: 写入失败时的 fallback 目录（同级 _memory_fallback/）。
        # 构造时即 mkdir，确保 _safe_write_with_fallback / replay_fallback
        # 在数据库不可写时也能落地 JSON 文件。
        self.fallback_dir = self.db_path.parent / "_memory_fallback"
        self.fallback_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # MAJOR #3: 构造时自动 replay 一次,与 MemoryExtractor.__init__ 端的
        # 调用形成双调幂等(第一次清空,第二次 glob 空 list 直接 no-op)。
        # replay_fallback 内部已对单文件 try/except 隔离,此处再加 try/except
        # 是双保险:启动路径绝不被 fallback 队列污染。
        try:
            self.replay_fallback()
        except Exception as exc:  # noqa: BLE001 - 启动路径绝不被污染
            logger.warning("memory fallback replay at init failed: {}", exc)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """获取一个 SQLite 连接；同一进程内由锁串行化。"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.row_factory = sqlite3.Row
                yield conn
                conn.commit()
            finally:
                conn.close()

    def init_schema(self) -> None:
        """幂等地创建所有表、索引与版本元数据。"""
        with self.connect() as conn:
            # Serialize schema discovery + ALTER across gateway/worker processes.
            conn.execute("BEGIN IMMEDIATE")
            for stmt in _SCHEMA_STATEMENTS:
                _execute_schema_statement(conn, stmt)
            # CREATE TABLE IF NOT EXISTS does not add columns to legacy stores.
            episode_columns = {row[1] for row in conn.execute("PRAGMA table_info(episodes)")}
            if "metadata" not in episode_columns:
                conn.execute("ALTER TABLE episodes ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
            if "workspace_id" not in episode_columns:
                conn.execute(
                    "ALTER TABLE episodes ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'default'"
                )
            # 旧库补列后重建工作区索引：语句列表里的同名索引在旧库上因列不存在
            # 被跳过，这里补建（``IF NOT EXISTS`` 保证幂等）。
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_episodes_workspace ON episodes(workspace_id)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES (?, ?)",
                ("version", _SCHEMA_VERSION),
            )

    def ensure_schema(self) -> None:
        """幂等补建所有表、索引与元数据。

        ``_SCHEMA_STATEMENTS`` 全部使用 ``CREATE TABLE IF NOT EXISTS`` /
        ``CREATE INDEX IF NOT EXISTS``，对已初始化库重跑也是 no-op，因此本方法
        无论库处于任何状态都直接走 ``init_schema``：旧库（如 v1）能自动补建后
        续 WU 新增的表/索引，不会因 ``_schema_meta`` 已存在而被跳过。
        """
        self.init_schema()

    def replay_fallback(self) -> int:
        """把 ``fallback_dir`` 中的所有 JSON 载荷重新执行一次,成功后删除文件。

        设计依据:``_safe_write_with_fallback`` 在 DB 写入失败时把整条 payload 落盘
        到 ``fallback_dir``。下次启动 / 下次 _persist 调用前调用本方法把队列重放,
        避免 LLM 已经成功抽取但写入失败导致数据丢失。

        - 单文件失败仅 ``logger.warning``,**绝不**抛出,以保证启动路径不被污染。
        - 文件名按字典序排序以保证顺序;失败文件保留以便下次再试。
        - 返回成功重放的数量。
        """
        if not self.fallback_dir.exists():
            return 0
        succeeded = 0
        # 按文件名排序(ISO ts 前缀保证顺序);glob 一次拿全避免迭代中新增干扰。
        paths = sorted(self.fallback_dir.glob("*.json"))
        for path in paths:
            try:
                payload_raw = path.read_text(encoding="utf-8")
                payload = json.loads(payload_raw)
                kind = payload.get("kind")
                item = payload.get("item")
                if not isinstance(item, dict):
                    raise ValueError(f"fallback payload missing/invalid 'item': {path.name}")
                indexed_memory = None
                with self.connect() as conn:
                    if kind == "memory":
                        from nanobot.memory.models import Memory

                        memory = Memory.from_row(item)
                        add_memory(conn, memory)
                        indexed_memory = memory
                    elif kind == "episode":
                        from nanobot.memory.models import Episode

                        episode = Episode.from_row(item)
                        add_episode(conn, episode)
                    elif kind == "backfill":
                        memory_id = str(item.get("memory_id") or "")
                        episode_id = str(item.get("episode_id") or "")
                        if not memory_id or not episode_id:
                            raise ValueError(
                                f"backfill payload missing ids: {path.name}"
                            )
                        update_memory_source_episode(conn, memory_id, episode_id)
                    else:
                        raise ValueError(f"unknown fallback kind: {kind!r}")
                # 向量挂钩必须在 connect() **之外**调用：connect() 持非可重入锁，
                # 持锁期间调 indexer（将来若触达 DB）会自死锁（WU-07 踩过的坑）。
                # 局部 import：避免核心安装经 vector 包拉入重依赖，且绕开
                # database <-> indexer 的模块级 import 环。
                if indexed_memory is not None:
                    from nanobot.memory.vector.indexer import (
                        index_memory_best_effort,
                    )

                    index_memory_best_effort(indexed_memory)
                path.unlink()
                succeeded += 1
            except Exception as exc:  # noqa: BLE001 - 单文件失败隔离
                logger.warning(
                    "memory fallback replay skipped for {}: {}",
                    path.name,
                    exc,
                )
        return succeeded


def bm25_rank_to_score(rank: float) -> float:
    """FTS5 bm25() 值 -> [0,1] 相关性。rank 越小越相关。"""
    return 1.0 / (1.0 + max(0.0, rank))
