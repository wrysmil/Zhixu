"""SQLite 连接管理 + schema 初始化。"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

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
    # 记忆减法（契约 §2.3）：``generation`` / ``last_seq`` 是新的进度坐标，
    # ``last_count`` 等旧字段只为兼容保留，不再是另一条进度链。旧库由
    # ``init_schema`` 的 ALTER 迁移补齐这两列。
    """
    CREATE TABLE IF NOT EXISTS session_extraction_state (
        session_key      TEXT    PRIMARY KEY,
        last_count       INTEGER NOT NULL,
        last_source      TEXT    NOT NULL,
        last_extracted_at TEXT   NOT NULL,
        updated_at       TEXT    NOT NULL,
        generation       TEXT    NOT NULL DEFAULT '',
        last_seq         INTEGER NOT NULL DEFAULT 0
    )
    """,
    # ----- memory_extraction_batches -----
    # 契约 §2.3：一张批次表保存边界（区间 (start_seq, end_seq]）、证据摘要、
    # 两阶段输出与 checkpoint、状态、claim/lease 与错误。``status`` 取值见
    # ``models.BatchReceipt``（pending/partial/failed/completed）。
    # 消费键 + 固定边界唯一，保证同一批次不会因重试产生第二行。
    """
    CREATE TABLE IF NOT EXISTS memory_extraction_batches (
        batch_id          TEXT    PRIMARY KEY,
        workspace_id      TEXT    NOT NULL,
        session_key       TEXT    NOT NULL,
        generation        TEXT    NOT NULL,
        reason            TEXT    NOT NULL DEFAULT '',
        start_seq         INTEGER NOT NULL,
        end_seq           INTEGER NOT NULL,
        evidence_digest   TEXT    NOT NULL,
        status            TEXT    NOT NULL DEFAULT 'pending',
        semantic_output   TEXT    NOT NULL DEFAULT '',
        episode_output    TEXT    NOT NULL DEFAULT '',
        semantic_checkpoint_at TEXT NOT NULL DEFAULT '',
        episode_checkpoint_at  TEXT NOT NULL DEFAULT '',
        claim_owner       TEXT    NOT NULL DEFAULT '',
        claimed_at        TEXT    NOT NULL DEFAULT '',
        lease_expires_at  TEXT    NOT NULL DEFAULT '',
        error_code        TEXT    NOT NULL DEFAULT '',
        error_detail      TEXT    NOT NULL DEFAULT '',
        created_at        TEXT    NOT NULL,
        updated_at        TEXT    NOT NULL,
        UNIQUE (workspace_id, session_key, generation, start_seq, end_seq)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_batches_scope ON memory_extraction_batches(workspace_id, session_key, generation, status)",
    "CREATE INDEX IF NOT EXISTS idx_batches_lease ON memory_extraction_batches(status, lease_expires_at)",
    # ----- memory_evidence_suppressions -----
    # 契约 §3：遗忘登记按作用域 + 证据键保存，只留最小必要摘要（内容指纹），
    # 不保留被遗忘正文。writer 写入前查这张表，阻止同一旧证据批次复活事实。
    """
    CREATE TABLE IF NOT EXISTS memory_evidence_suppressions (
        workspace_id  TEXT NOT NULL,
        evidence_key  TEXT NOT NULL,
        memory_id     TEXT NOT NULL DEFAULT '',
        summary       TEXT NOT NULL DEFAULT '',
        forgotten_at  TEXT NOT NULL,
        PRIMARY KEY (workspace_id, evidence_key)
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
    # ----- memory_dream_runs -----
    # P1：Dream 从「改文件」改成「写库」之后，git diff 不再是可靠的审计面
    # （库里多一行，工作树可能一个字都不变）。这张表替代 git 承担
    # 「昨晚它动了什么」的问答：一次运行一行，计数来自真实写入决策而非
    # 模型自述（dream.md 第 133 行的验证纪律）。
    """
    CREATE TABLE IF NOT EXISTS memory_dream_runs (
        run_id        TEXT    PRIMARY KEY,
        workspace_id  TEXT    NOT NULL,
        trigger       TEXT    NOT NULL DEFAULT '',
        status        TEXT    NOT NULL DEFAULT 'running',
        entries_read  INTEGER NOT NULL DEFAULT 0,
        added         INTEGER NOT NULL DEFAULT 0,
        updated       INTEGER NOT NULL DEFAULT 0,
        deleted       INTEGER NOT NULL DEFAULT 0,
        skipped       INTEGER NOT NULL DEFAULT 0,
        model         TEXT    NOT NULL DEFAULT '',
        error         TEXT    NOT NULL DEFAULT '',
        started_at    TEXT    NOT NULL,
        finished_at   TEXT    NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_dream_runs_scope ON memory_dream_runs(workspace_id, started_at DESC)",
    # ----- memory_consolidate_runs -----
    # P2：每日盘账（memory_consolidate job）的审计表。衰减/归档是批量副作用，
    # 必须有「昨天动了多少条」的账可查；checkpoint 列存中断时的续跑指针。
    """
    CREATE TABLE IF NOT EXISTS memory_consolidate_runs (
        run_id        TEXT    PRIMARY KEY,
        workspace_id  TEXT    NOT NULL,
        trigger       TEXT    NOT NULL DEFAULT '',
        status        TEXT    NOT NULL DEFAULT 'running',
        dry_run       INTEGER NOT NULL DEFAULT 0,
        processed     INTEGER NOT NULL DEFAULT 0,
        merged        INTEGER NOT NULL DEFAULT 0,
        decayed       INTEGER NOT NULL DEFAULT 0,
        archived      INTEGER NOT NULL DEFAULT 0,
        checkpoint    TEXT    NOT NULL DEFAULT '',
        error         TEXT    NOT NULL DEFAULT '',
        semantic_dedup TEXT   NOT NULL DEFAULT '',
        started_at    TEXT    NOT NULL,
        finished_at   TEXT    NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_consolidate_runs_scope ON memory_consolidate_runs(workspace_id, started_at DESC)",
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
        # ``_lock`` 在整个 ``with connect()`` 期间持有；同一线程再调一次
        # ``connect()`` 会与它自锁（0 超时、0 报错，表现为永久挂起）。这里用
        # 线程本地深度把这类重入挡在锁之前，给出可操作的错误。
        self._connect_depth = threading.local()
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
        """获取一个 SQLite 连接；同一进程内由锁串行化。

        同一线程内嵌套调用会被直接拒绝：锁在整个 ``yield`` 期间持有，重入就是
        永久挂起。需要第二个查询时复用当前连接，或把查询挪到 with 块外。
        """
        depth = getattr(self._connect_depth, "value", 0)
        if depth:
            raise RuntimeError(
                "nested MemoryDatabase.connect() on the same instance would deadlock; "
                "reuse the open connection or move the query outside the with-block"
            )
        self._connect_depth.value = depth + 1
        try:
            with self._lock:
                # SQLite 单写者：后台提取/索引提交时，WebUI 的删除/编辑会撞写锁。
                # 默认 5s 会太早放弃（真机日志里表现为 delete 报 "WebUI mutation failed"），
                # 这里放宽到 15s；仍超时则由 WebUI 路由映射成可重试的 503。
                conn = sqlite3.connect(self.db_path, timeout=15.0)
                try:
                    conn.execute("PRAGMA foreign_keys = ON")
                    conn.execute("PRAGMA busy_timeout = 15000")
                    conn.row_factory = sqlite3.Row
                    yield conn
                    conn.commit()
                finally:
                    conn.close()
        finally:
            self._connect_depth.value = depth

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
            # 记忆减法（契约 §2.3）：``session_extraction_state`` 的进度坐标是
            # 可回退的新增列；旧库（含 WU-A 时期建的表）在这里补列，旧字段原样
            # 保留，仅用于兼容读取。
            extraction_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(session_extraction_state)")
            }
            if "generation" not in extraction_columns:
                conn.execute(
                    "ALTER TABLE session_extraction_state "
                    "ADD COLUMN generation TEXT NOT NULL DEFAULT ''"
                )
            if "last_seq" not in extraction_columns:
                conn.execute(
                    "ALTER TABLE session_extraction_state "
                    "ADD COLUMN last_seq INTEGER NOT NULL DEFAULT 0"
                )
            # 盘账审计补「语义去重是否真的跑了」这一列；旧库补列后历史行取空串。
            run_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(memory_consolidate_runs)")
            }
            if "semantic_dedup" not in run_columns:
                conn.execute(
                    "ALTER TABLE memory_consolidate_runs "
                    "ADD COLUMN semantic_dedup TEXT NOT NULL DEFAULT ''"
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
        """导入旧版本遗留的逐条 fallback JSON 文件（一次性兼容）。

        记忆减法 D07 之后，新写入失败不再落单条 fallback 文件，而是按批次幂等
        重试（``memory_extraction_batches``）。本方法只保留给**升级前**已经落盘
        的 ``_memory_fallback/*.json``：把它们导入 SQLite 后删除文件，避免旧数据
        丢失；它不再承担新失败的重放职责。

        - 单文件失败仅 ``logger.warning``,**绝不**抛出,以保证启动路径不被污染。
        - 文件名按字典序排序以保证顺序;失败文件保留以便下次再试。
        - 返回成功导入的数量。
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


# ---------------------------------------------------------------------------
# memory_extraction_batches 行读写（契约 §2.3）
# ---------------------------------------------------------------------------
# 批次行是本轮提取的唯一调度载体：边界、证据摘要、两轨 checkpoint、状态、
# claim/lease 与错误都在一行里。提取器（extractor.py）只通过本节的函数读写，
# 不直接拼 SQL；事务边界由调用方持有（同一 ``conn`` = 同一事务）。

_BATCH_COLUMNS = (
    "batch_id",
    "workspace_id",
    "session_key",
    "generation",
    "reason",
    "start_seq",
    "end_seq",
    "evidence_digest",
    "status",
    "semantic_output",
    "episode_output",
    "semantic_checkpoint_at",
    "episode_checkpoint_at",
    "claim_owner",
    "claimed_at",
    "lease_expires_at",
    "error_code",
    "error_detail",
    "created_at",
    "updated_at",
)

#: 允许通过 :func:`update_extraction_batch` 修改的列。边界、消费键与证据摘要
#: 是批次的身份，一旦写入不再变化（否则幂等键会被绕过）。
_BATCH_UPDATABLE_COLUMNS = frozenset(
    {
        "status",
        "semantic_output",
        "episode_output",
        "semantic_checkpoint_at",
        "episode_checkpoint_at",
        "claim_owner",
        "claimed_at",
        "lease_expires_at",
        "error_code",
        "error_detail",
        "updated_at",
    }
)


@dataclass(frozen=True)
class ExtractionBatchRow:
    """``memory_extraction_batches`` 的一行（契约 §2.3）。"""

    batch_id: str
    workspace_id: str
    session_key: str
    generation: str
    reason: str
    start_seq: int
    end_seq: int
    evidence_digest: str
    status: str
    semantic_output: str
    episode_output: str
    semantic_checkpoint_at: str
    episode_checkpoint_at: str
    claim_owner: str
    claimed_at: str
    lease_expires_at: str
    error_code: str
    error_detail: str
    created_at: str
    updated_at: str


def _row_to_batch(row: sqlite3.Row) -> ExtractionBatchRow:
    return ExtractionBatchRow(
        batch_id=row["batch_id"],
        workspace_id=row["workspace_id"],
        session_key=row["session_key"],
        generation=row["generation"],
        reason=row["reason"],
        start_seq=int(row["start_seq"]),
        end_seq=int(row["end_seq"]),
        evidence_digest=row["evidence_digest"],
        status=row["status"],
        semantic_output=row["semantic_output"],
        episode_output=row["episode_output"],
        semantic_checkpoint_at=row["semantic_checkpoint_at"],
        episode_checkpoint_at=row["episode_checkpoint_at"],
        claim_owner=row["claim_owner"],
        claimed_at=row["claimed_at"],
        lease_expires_at=row["lease_expires_at"],
        error_code=row["error_code"],
        error_detail=row["error_detail"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_extraction_batch(
    conn: sqlite3.Connection,
    batch_id: str,
) -> ExtractionBatchRow | None:
    """按稳定 batch_id 读取一行；不存在返回 ``None``。"""
    columns = ", ".join(_BATCH_COLUMNS)
    row = conn.execute(
        f"SELECT {columns} FROM memory_extraction_batches WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()
    return _row_to_batch(row) if row is not None else None


def list_incomplete_extraction_batches(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    session_key: str,
    generation: str,
    after_seq: int,
) -> list[ExtractionBatchRow]:
    """列出同一消费键下尚未完成、且上界超过 ``after_seq`` 的批次。

    按 ``start_seq`` 升序返回：调用方必须先续跑最早的一段，再处理新上界，
    保证 ``last_seq`` 单调推进、批与批之间不重叠。
    """
    columns = ", ".join(_BATCH_COLUMNS)
    rows = conn.execute(
        f"SELECT {columns} FROM memory_extraction_batches "
        "WHERE workspace_id = ? AND session_key = ? AND generation = ? "
        "AND status != 'completed' AND end_seq > ? "
        "ORDER BY start_seq ASC, end_seq ASC",
        (workspace_id, session_key, generation, int(after_seq)),
    ).fetchall()
    return [_row_to_batch(row) for row in rows]


def find_extraction_batch_by_bounds(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    session_key: str,
    generation: str,
    end_seq: int,
    start_seq: int | None = None,
) -> ExtractionBatchRow | None:
    """按消费键 + 边界查询（幂等重放时定位既有批次）。

    ``start_seq`` 省略时只按 ``end_seq`` 找最近一条：重复请求只带同一个上界，
    起始边界由当时的进度决定，不能由调用方假设。
    """
    columns = ", ".join(_BATCH_COLUMNS)
    clauses = [
        "workspace_id = ?",
        "session_key = ?",
        "generation = ?",
        "end_seq = ?",
    ]
    params: list[Any] = [workspace_id, session_key, generation, int(end_seq)]
    if start_seq is not None:
        clauses.append("start_seq = ?")
        params.append(int(start_seq))
    row = conn.execute(
        f"SELECT {columns} FROM memory_extraction_batches "
        f"WHERE {' AND '.join(clauses)} ORDER BY start_seq DESC LIMIT 1",
        params,
    ).fetchone()
    return _row_to_batch(row) if row is not None else None


def insert_extraction_batch(conn: sqlite3.Connection, batch: ExtractionBatchRow) -> bool:
    """插入批次行；唯一约束冲突（并发重复创建）返回 ``False`` 而不是抛错。"""
    placeholders = ", ".join(f":{column}" for column in _BATCH_COLUMNS)
    try:
        conn.execute(
            f"INSERT INTO memory_extraction_batches ({', '.join(_BATCH_COLUMNS)}) "
            f"VALUES ({placeholders})",
            {column: getattr(batch, column) for column in _BATCH_COLUMNS},
        )
    except sqlite3.IntegrityError:
        return False
    return True


def update_extraction_batch(
    conn: sqlite3.Connection,
    batch_id: str,
    *,
    updates: dict[str, Any],
    require_owner: str | None = None,
) -> bool:
    """按列局部更新批次行；``require_owner`` 非空时只在 owner 匹配时生效。

    Returns:
        True 表示确实更新了行；False 表示行不存在或 owner 已过期（认领丢失），
        调用方必须放弃本次提交。
    """
    unknown = set(updates) - _BATCH_UPDATABLE_COLUMNS
    if unknown:
        raise ValueError(f"extraction batch columns are not updatable: {sorted(unknown)}")
    if not updates:
        return get_extraction_batch(conn, batch_id) is not None
    sets = ", ".join(f"{column} = :{column}" for column in updates)
    params: dict[str, Any] = dict(updates)
    params["batch_id"] = batch_id
    where = "batch_id = :batch_id"
    if require_owner is not None:
        where += " AND claim_owner = :claim_owner"
        params["claim_owner"] = require_owner
    cursor = conn.execute(
        f"UPDATE memory_extraction_batches SET {sets} WHERE {where}",
        params,
    )
    return cursor.rowcount > 0
