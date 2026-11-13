"""轨迹 SQLite 存储：单 writer、事件事实与查询投影同事务提交。

本模块只被 ``nanobot.trajectory`` 内部与只读 WebUI 适配器使用。所有读取都
按 session 过滤；调用方必须先完成授权。查询不会为不存在的 session 建库
（服务层在配置关闭时直接返回不可用）。
"""

from __future__ import annotations

import base64
import json
import queue
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from filelock import FileLock, Timeout
from loguru import logger

from nanobot.trajectory.models import (
    SCHEMA_VERSION,
    BodyRow,
    CommitBatch,
    ContentRef,
    Coverage,
    EventCandidate,
    EventPage,
    RecordPage,
    TrajectoryEvent,
    TrajectoryRecord,
    unavailable_ref,
)
from nanobot.trajectory.policy import ContentPolicy

SCHEMA_VERSION_KEY = "schema_version"
TRACE_SCHEMA_VERSION = 1
_ACTIVE_RUN_STATUSES = ("running", "requested", "interrupted")
_MAX_BATCH_EVENTS = 128
_BATCH_WINDOW_S = 0.2
_QUEUE_CAPACITY = 8192


class TrajectoryWriterBusy(RuntimeError):  # noqa: N818 - 与现有子 Agent 账本命名一致
    """当前 workspace 已被另一个轨迹 writer 占有。"""


class TrajectoryUnavailable(RuntimeError):  # noqa: N818 - 与领域错误命名保持一致
    """轨迹存储不可用（锁冲突、迁移版本未知或磁盘失败）。"""


class TrajectoryCursorExpired(RuntimeError):  # noqa: N818 - 与领域错误命名保持一致
    """客户端水位早于保留前缀或历史代已变化。"""


class TrajectoryInvalidCursor(ValueError):  # noqa: N818 - 与领域错误命名保持一致
    """cursor / seq 参数非法。"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _coverage_from_payload(payload: dict[str, Any]) -> Coverage:
    raw = payload.get("coverage")
    if not isinstance(raw, dict):
        return Coverage()
    data = cast(dict[str, Any], raw)
    reasons_raw = data.get("reasons")
    reasons: tuple[str, ...] = ()
    if isinstance(reasons_raw, list):
        reasons = tuple(str(item) for item in cast(list[object], reasons_raw))
    status_raw = data.get("status")
    status = cast(
        Any, status_raw if status_raw in {"complete", "partial", "unknown"} else "complete",
    )
    mode_raw = data.get("contentMode")
    content_mode = cast(
        Any,
        mode_raw if mode_raw in {"off", "metadata", "standard", "raw-chunks"} else "standard",
    )
    return Coverage(
        status=status,
        reasons=reasons,
        content_mode=content_mode,
        attempt_visibility=str(data.get("attemptVisibility") or "provider_call"),
    )


def _encode_cursor(payload: list[Any]) -> str:
    return base64.urlsafe_b64encode(_dumps(payload).encode()).decode()


def _decode_cursor(cursor: str) -> list[Any]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise TrajectoryInvalidCursor("invalid cursor") from exc
    if not isinstance(raw, list):
        raise TrajectoryInvalidCursor("invalid cursor")
    return cast(list[Any], raw)


class TrajectoryStore:
    """一个 workspace 的轨迹库。

    ``in_memory=True`` 用于临时聊天：不落盘、不参与 retention，也不对外
    暴露持久化能力。普通模式持有 ``runtime/trajectory.writer.lock``。
    """

    def __init__(
        self,
        workspace: Path | str | None = None,
        *,
        in_memory: bool = False,
        read_only: bool = False,
        retention_days: int = 30,
        policy: ContentPolicy | None = None,
        queue_capacity: int = _QUEUE_CAPACITY,
        batch_window_s: float = _BATCH_WINDOW_S,
    ) -> None:
        self.workspace = str(Path(workspace).expanduser().resolve()) if workspace else ""
        self.in_memory = in_memory
        self.read_only = read_only
        self.retention_days = max(1, retention_days)
        self.policy = policy or ContentPolicy()
        self.db_path: Path | None = None
        self._lock = threading.RLock()
        self._writer_lock: FileLock | None = None
        self._closed = False
        self._error: str | None = None
        self._queue: queue.Queue[EventCandidate | None] = queue.Queue(
            maxsize=queue_capacity,
        )
        self._idle = threading.Event()
        self._idle.set()
        self._pending = 0
        self._listeners: list[Callable[[CommitBatch], None]] = []
        self._batch_window_s = batch_window_s
        self._schema_ok = False
        self._thread: threading.Thread | None = None

        if read_only:
            if workspace is None:
                raise ValueError("workspace is required for a read-only trajectory store")
            self.db_path = Path(self.workspace) / "runtime" / "trajectory.db"
            if not self.db_path.is_file():
                raise TrajectoryUnavailable("TRAJECTORY_NOT_FOUND: no trajectory database")
            try:
                self._conn = sqlite3.connect(
                    f"file:{self.db_path.as_posix()}?mode=ro",
                    uri=True,
                    check_same_thread=False,
                )
            except sqlite3.Error as exc:
                raise TrajectoryUnavailable(
                    "TRAJECTORY_UNAVAILABLE: trajectory database is unreadable",
                ) from exc
            self._conn.execute("PRAGMA busy_timeout=5000")
            try:
                row = self._conn.execute(
                    "SELECT value FROM schema_meta WHERE key=?", (SCHEMA_VERSION_KEY,),
                ).fetchone()
            except sqlite3.Error as exc:
                self._conn.close()
                raise TrajectoryUnavailable(
                    "TRAJECTORY_UNAVAILABLE: trajectory schema is missing",
                ) from exc
            if row is None or _safe_int(row[0]) > TRACE_SCHEMA_VERSION:
                self._conn.close()
                raise TrajectoryUnavailable(
                    "TRAJECTORY_VERSION_UNSUPPORTED: newer trajectory schema",
                )
            self._schema_ok = True
            return

        if not in_memory:
            if workspace is None:
                raise ValueError("workspace is required for a persistent trajectory store")
            directory = Path(self.workspace) / "runtime"
            directory.mkdir(parents=True, exist_ok=True)
            self.db_path = directory / "trajectory.db"
            self._writer_lock = FileLock(
                str(directory / "trajectory.writer.lock"), thread_local=False,
            )
            try:
                self._writer_lock.acquire(timeout=0)
            except Timeout:
                raise TrajectoryWriterBusy(
                    "TRAJECTORY_WRITER_BUSY: workspace writer already held",
                ) from None
        try:
            self._conn = sqlite3.connect(
                str(self.db_path) if self.db_path else ":memory:",
                check_same_thread=False,
            )
            if not in_memory:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            with self._lock, self._conn:
                self._create_schema()
                self._migrate()
            self._prune()
            self._thread = threading.Thread(
                target=self._writer_loop, name="trajectory-writer", daemon=True,
            )
            self._thread.start()
        except BaseException:
            if hasattr(self, "_conn"):
                self._conn.close()
            if self._writer_lock is not None:
                self._writer_lock.release()
            raise

    # ------------------------------------------------------------------ schema

    def _create_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trace_heads (
                session_id TEXT PRIMARY KEY,
                durable_seq INTEGER NOT NULL DEFAULT 0,
                retained_from_seq INTEGER NOT NULL DEFAULT 1,
                history_generation INTEGER NOT NULL DEFAULT 1,
                coverage_json TEXT NOT NULL DEFAULT '{}',
                updated_at_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trace_events (
                session_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_id TEXT NOT NULL UNIQUE,
                trace_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                turn_id TEXT,
                step INTEGER,
                span_id TEXT,
                parent_span_id TEXT,
                execution_id TEXT,
                logical_request_id TEXT,
                model_call_id TEXT,
                tool_call_id TEXT,
                type TEXT NOT NULL,
                ignorable INTEGER NOT NULL DEFAULT 0,
                time_ms INTEGER NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY(session_id, seq)
            );
            CREATE INDEX IF NOT EXISTS trace_events_run_span
                ON trace_events(run_id, span_id);
            CREATE INDEX IF NOT EXISTS trace_events_session_turn
                ON trace_events(session_id, turn_id);
            CREATE INDEX IF NOT EXISTS trace_events_request
                ON trace_events(logical_request_id, model_call_id);
            CREATE TABLE IF NOT EXISTS trace_bodies (
                body_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                content_type TEXT NOT NULL,
                text TEXT NOT NULL,
                stored_bytes INTEGER NOT NULL,
                original_bytes INTEGER NOT NULL,
                redacted INTEGER NOT NULL,
                truncated INTEGER NOT NULL,
                preview TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trace_runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                turn_id TEXT,
                parent_run_id TEXT,
                execution_id TEXT,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at_ms INTEGER NOT NULL,
                finished_at_ms INTEGER,
                duration_ms INTEGER,
                coverage_json TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS trace_runs_session
                ON trace_runs(session_id, started_at_ms);
            CREATE TABLE IF NOT EXISTS trace_spans (
                span_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                parent_span_id TEXT,
                kind TEXT NOT NULL,
                name TEXT,
                status TEXT NOT NULL,
                started_at_ms INTEGER,
                finished_at_ms INTEGER,
                duration_ms INTEGER,
                logical_request_id TEXT,
                model_call_id TEXT,
                tool_call_id TEXT,
                record_id TEXT,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS trace_spans_run ON trace_spans(run_id, span_id);
            CREATE TABLE IF NOT EXISTS trace_records (
                record_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                run_id TEXT NOT NULL,
                turn_id TEXT,
                step INTEGER,
                span_id TEXT,
                parent_span_id TEXT,
                model_order INTEGER,
                status TEXT NOT NULL,
                started_at_ms INTEGER,
                finished_at_ms INTEGER,
                duration_ms INTEGER,
                anchor_seq INTEGER NOT NULL,
                record_revision INTEGER NOT NULL,
                turn_started_ms INTEGER NOT NULL DEFAULT 0,
                run_started_ms INTEGER NOT NULL DEFAULT 0,
                blocks_json TEXT NOT NULL,
                request_ref_json TEXT,
                schema_ref_json TEXT,
                extra_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS trace_records_session
                ON trace_records(session_id, turn_started_ms, run_started_ms,
                                 step, model_order, anchor_seq, record_id);
            CREATE TABLE IF NOT EXISTS trace_turns (
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                source TEXT,
                started_at_ms INTEGER NOT NULL,
                finished_at_ms INTEGER,
                outcome TEXT,
                PRIMARY KEY(session_id, turn_id)
            );
            CREATE TABLE IF NOT EXISTS trace_steps (
                run_id TEXT NOT NULL,
                step INTEGER NOT NULL,
                started_at_ms INTEGER NOT NULL,
                finished_at_ms INTEGER,
                status TEXT NOT NULL,
                PRIMARY KEY(run_id, step)
            );
            """
        )

    def _migrate(self) -> None:
        row = self._conn.execute(
            "SELECT value FROM schema_meta WHERE key=?", (SCHEMA_VERSION_KEY,),
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES(?, ?)",
                (SCHEMA_VERSION_KEY, str(TRACE_SCHEMA_VERSION)),
            )
        else:
            try:
                version = int(row[0])
            except (TypeError, ValueError) as exc:
                raise TrajectoryUnavailable("TRAJECTORY_VERSION_UNSUPPORTED: bad schema") from exc
            if version > TRACE_SCHEMA_VERSION:
                raise TrajectoryUnavailable(
                    "TRAJECTORY_VERSION_UNSUPPORTED: newer trajectory schema",
                )
        self._schema_ok = True

    # ------------------------------------------------------------------ writer

    def add_listener(self, listener: Callable[[CommitBatch], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def enqueue(self, candidate: EventCandidate) -> bool:
        if self.read_only or self._closed or not self._schema_ok:
            return False
        with self._lock:
            if self._error is not None:
                return False
            self._pending += 1
            self._idle.clear()
        try:
            self._queue.put_nowait(candidate)
            return True
        except queue.Full:
            with self._lock:
                self._pending = max(0, self._pending - 1)
                self._error = "queue_overflow"
            logger.warning("trajectory queue overflow; capture degraded for {}", self.workspace)
            return False

    def _writer_loop(self) -> None:
        while True:
            try:
                first = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._closed:
                    return
                self._idle.set()
                continue
            if first is None:  # close/flush sentinel
                self._idle.set()
                if self._closed:
                    return
                continue
            batch: list[EventCandidate] = [first]
            deadline = time.monotonic() + self._batch_window_s
            while len(batch) < _MAX_BATCH_EVENTS:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    break
                try:
                    item = self._queue.get(timeout=timeout)
                except queue.Empty:
                    break
                if item is None:
                    continue
                batch.append(item)
            try:
                self._commit(batch)
            except Exception:
                logger.exception("trajectory writer failed for {}", self.workspace)
                with self._lock:
                    self._error = "write_failed"
            finally:
                with self._lock:
                    self._pending = max(0, self._pending - len(batch))
                    if self._pending == 0:
                        self._idle.set()

    def flush(self, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._pending == 0:
                    return True
            self._idle.wait(timeout=min(0.05, max(0.0, deadline - time.monotonic())))
        return False

    def _commit(self, batch: list[EventCandidate]) -> None:
        events: list[TrajectoryEvent] = []
        touched: set[str] = set()
        with self._lock, self._conn:
            for candidate in batch:
                head = self._head(candidate.session_id)
                seq = int(head["durable_seq"]) + 1
                for body in candidate.bodies:
                    self._insert_body(candidate.session_id, body)
                event_id = uuid4().hex
                self._conn.execute(
                    "INSERT INTO trace_events(session_id, seq, event_id, trace_id, run_id,"
                    " turn_id, step, span_id, parent_span_id, execution_id,"
                    " logical_request_id, model_call_id, tool_call_id, type, ignorable,"
                    " time_ms, payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        candidate.session_id, seq, event_id, candidate.trace_id,
                        candidate.run_id, candidate.turn_id, candidate.step,
                        candidate.span_id, candidate.parent_span_id,
                        candidate.execution_id, candidate.logical_request_id,
                        candidate.model_call_id, candidate.tool_call_id, candidate.type,
                        1 if candidate.ignorable else 0, candidate.time_ms,
                        _dumps(candidate.payload),
                    ),
                )
                self._conn.execute(
                    "UPDATE trace_heads SET durable_seq=?, updated_at_ms=? WHERE session_id=?",
                    (seq, _now_ms(), candidate.session_id),
                )
                events.append(TrajectoryEvent(
                    schema_version=SCHEMA_VERSION,
                    event_id=event_id,
                    session_id=candidate.session_id,
                    seq=seq,
                    trace_id=candidate.trace_id,
                    run_id=candidate.run_id,
                    turn_id=candidate.turn_id,
                    step=candidate.step,
                    span_id=candidate.span_id,
                    parent_span_id=candidate.parent_span_id,
                    execution_id=candidate.execution_id,
                    logical_request_id=candidate.logical_request_id,
                    model_call_id=candidate.model_call_id,
                    tool_call_id=candidate.tool_call_id,
                    type=candidate.type,
                    ignorable=candidate.ignorable,
                    time_ms=candidate.time_ms,
                    payload=candidate.payload,
                ))
                touched |= self._project(candidate, seq)

        if not events:
            return
        session_id = events[0].session_id
        head = self._read_head(session_id)
        updates = tuple(
            record for record_id in sorted(touched)
            if (record := self._load_record(record_id)) is not None
        )
        commit = CommitBatch(
            workspace=self.workspace,
            session_id=session_id,
            from_seq=events[0].seq,
            to_seq=events[-1].seq,
            durable_seq=int(head["durable_seq"]),
            history_generation=int(head["history_generation"]),
            events=tuple(events),
            record_updates=updates,
        )
        for listener in list(self._listeners):
            try:
                listener(commit)
            except Exception:
                logger.exception("trajectory listener failed")

    def _insert_body(self, session_id: str, body: BodyRow) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO trace_bodies(body_id, session_id, content_type, text,"
            " stored_bytes, original_bytes, redacted, truncated, preview, created_at_ms)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                body.body_id, session_id, body.content_type, body.text,
                body.stored_bytes, body.original_bytes, 1 if body.redacted else 0,
                1 if body.truncated else 0, body.preview, _now_ms(),
            ),
        )

    # ------------------------------------------------------------------ heads

    def _ensure_head(self, session_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO trace_heads(session_id, durable_seq, retained_from_seq,"
            " history_generation, coverage_json, updated_at_ms) VALUES(?,?,?,?,?,?)",
            (session_id, 0, 1, 1, "{}", _now_ms()),
        )

    def _head(self, session_id: str) -> dict[str, Any]:
        if not self.read_only:
            self._ensure_head(session_id)
        row = self._conn.execute(
            "SELECT durable_seq, retained_from_seq, history_generation, coverage_json"
            " FROM trace_heads WHERE session_id=?", (session_id,),
        ).fetchone()
        if row is None:
            return {
                "session_id": session_id,
                "durable_seq": 0,
                "retained_from_seq": 1,
                "history_generation": 1,
                "coverage": {},
            }
        return {
            "session_id": session_id,
            "durable_seq": int(row[0]),
            "retained_from_seq": int(row[1]),
            "history_generation": int(row[2]),
            "coverage": json.loads(row[3]) if row[3] else {},
        }

    def _read_head(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            return self._head(session_id)

    def _update_coverage(self, session_id: str, payload: dict[str, Any]) -> None:
        head = self._head(session_id)
        coverage = _coverage_from_payload(payload)
        merged = {}
        if head["coverage"]:
            previous = _coverage_from_payload(head["coverage"])
            reasons = list(dict.fromkeys((*previous.reasons, *coverage.reasons)))
            status = "partial" if "partial" in {previous.status, coverage.status} else coverage.status
            merged = Coverage(
                status=cast(Any, status),
                reasons=tuple(reasons),
                content_mode=coverage.content_mode,
                attempt_visibility=coverage.attempt_visibility,
            ).to_dict()
        else:
            merged = coverage.to_dict()
        self._conn.execute(
            "UPDATE trace_heads SET coverage_json=?, updated_at_ms=? WHERE session_id=?",
            (_dumps(merged), _now_ms(), session_id),
        )

    # -------------------------------------------------------------- projection

    def _record_id_for(self, event_type: str, payload: dict[str, Any],
                       candidate: EventCandidate | None = None) -> str | None:
        if event_type == "run.started":
            return f"run:{payload.get('runId') or ''}" if payload.get("visible") else None
        if event_type == "input.recorded":
            return f"user:{payload.get('inputId')}"
        if event_type == "context.injected":
            return f"ctx:{payload.get('logicalRequestId')}:{payload.get('index', 0)}"
        if event_type == "model.requested":
            return f"assistant:{payload.get('logicalRequestId')}"
        if event_type.startswith("model."):
            request_id = payload.get("logicalRequestId")
            return f"assistant:{request_id}" if request_id else None
        if event_type.startswith("tool.") or event_type == "execution.linked":
            return f"tool:{payload.get('toolCallId')}"
        if event_type.startswith("compaction."):
            return f"compaction:{payload.get('compactionId')}"
        return None

    def _project(self, candidate: EventCandidate, seq: int) -> set[str]:
        payload = candidate.payload
        event_type = candidate.type
        if event_type == "run.started":
            self._project_run_started(candidate, seq)
        elif event_type == "run.finished":
            self._project_run_finished(candidate, seq)
        elif event_type == "turn.started":
            self._conn.execute(
                "INSERT INTO trace_turns(session_id, turn_id, source, started_at_ms)"
                " VALUES(?,?,?,?) ON CONFLICT(session_id, turn_id) DO UPDATE SET"
                " source=excluded.source, started_at_ms=excluded.started_at_ms",
                (candidate.session_id, candidate.turn_id or "", payload.get("source"),
                 candidate.time_ms),
            )
        elif event_type == "turn.finished":
            self._conn.execute(
                "UPDATE trace_turns SET finished_at_ms=?, outcome=? WHERE session_id=?"
                " AND turn_id=?",
                (candidate.time_ms, payload.get("outcome"), candidate.session_id,
                 candidate.turn_id or ""),
            )
        elif event_type == "step.started":
            self._conn.execute(
                "INSERT INTO trace_steps(run_id, step, started_at_ms, status) VALUES(?,?,?,?)"
                " ON CONFLICT(run_id, step) DO UPDATE SET started_at_ms=excluded.started_at_ms",
                (candidate.run_id, candidate.step if candidate.step is not None else 0,
                 candidate.time_ms, "running"),
            )
        elif event_type == "step.finished":
            self._conn.execute(
                "UPDATE trace_steps SET finished_at_ms=?, status=? WHERE run_id=? AND step=?",
                (candidate.time_ms, payload.get("status") or "succeeded",
                 candidate.run_id, candidate.step if candidate.step is not None else 0),
            )
        elif event_type == "trace.gap":
            self._update_coverage(candidate.session_id, {
                "coverage": {
                    "status": "partial",
                    "reasons": list(payload.get("reasons") or ["trace_gap"]),
                    "contentMode": self.policy.content_mode,
                    "attemptVisibility": "provider_call",
                },
            })

        record_id = self._record_id_for(event_type, payload, candidate)
        if record_id is None:
            return set()
        if event_type == "run.started":
            self._project_system_record(candidate, record_id, seq)
        elif event_type == "input.recorded":
            self._project_input_record(candidate, record_id, seq)
        elif event_type == "context.injected":
            self._project_context_record(candidate, record_id, seq)
        elif event_type == "model.requested":
            self._project_assistant_record(candidate, record_id, seq)
        elif event_type in {
            "model.attempt.started", "model.output.delta", "model.attempt.finished",
            "model.retry.scheduled", "model.route.changed",
        }:
            self._project_assistant_update(candidate, record_id, seq)
        elif event_type in {"tool.requested", "tool.started", "tool.finished",
                            "execution.linked"}:
            self._project_tool_update(candidate, record_id, seq)
        elif event_type == "compaction.started":
            self._project_compaction_record(candidate, record_id, seq)
        elif event_type == "compaction.finished":
            self._project_compaction_update(candidate, record_id, seq)
        return {record_id}

    def _anchors(self, candidate: EventCandidate) -> tuple[int, int]:
        turn_started = 0
        if candidate.turn_id:
            row = self._conn.execute(
                "SELECT started_at_ms FROM trace_turns WHERE session_id=? AND turn_id=?",
                (candidate.session_id, candidate.turn_id),
            ).fetchone()
            if row is not None:
                turn_started = int(row[0])
        run_started = 0
        row = self._conn.execute(
            "SELECT started_at_ms FROM trace_runs WHERE run_id=?", (candidate.run_id,),
        ).fetchone()
        if row is not None:
            run_started = int(row[0])
        if turn_started == 0:
            turn_started = run_started
        return turn_started, run_started

    def _project_run_started(self, candidate: EventCandidate, seq: int) -> None:
        payload = candidate.payload
        self._conn.execute(
            "INSERT INTO trace_runs(run_id, session_id, trace_id, turn_id, parent_run_id,"
            " execution_id, source, status, started_at_ms, coverage_json, payload)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET"
            " status=excluded.status, payload=excluded.payload",
            (
                candidate.run_id, candidate.session_id, candidate.trace_id,
                candidate.turn_id, candidate.parent_span_id, candidate.execution_id,
                str(payload.get("source") or "user"), "running", candidate.time_ms,
                _dumps(payload.get("coverage") or {}), _dumps(payload),
            ),
        )
        self._update_coverage(candidate.session_id, payload)

    def _project_run_finished(self, candidate: EventCandidate, seq: int) -> None:
        payload = candidate.payload
        started_row = self._conn.execute(
            "SELECT started_at_ms FROM trace_runs WHERE run_id=?", (candidate.run_id,),
        ).fetchone()
        started = int(started_row[0]) if started_row else candidate.time_ms
        status = str(payload.get("status") or "succeeded")
        self._conn.execute(
            "UPDATE trace_runs SET status=?, finished_at_ms=?, duration_ms=?, payload=?"
            " WHERE run_id=?",
            (status, candidate.time_ms, max(0, candidate.time_ms - started),
             _dumps(payload), candidate.run_id),
        )
        self._update_coverage(candidate.session_id, payload)
        record_id = f"run:{candidate.run_id}"
        record = self._load_record(record_id)
        if record is not None:
            record.status = status
            record.finished_at_ms = candidate.time_ms
            record.duration_ms = max(0, candidate.time_ms - (record.started_at_ms or started))
            record.record_revision = seq
            self._save_record(record)

    def _project_system_record(
        self, candidate: EventCandidate, record_id: str, seq: int,
    ) -> None:
        payload = candidate.payload
        turn_started, run_started = self._anchors(candidate)
        record = TrajectoryRecord(
            record_id=record_id,
            session_id=candidate.session_id,
            kind="system",
            run_id=candidate.run_id,
            turn_id=candidate.turn_id,
            step=-1,
            span_id=candidate.run_id,
            parent_span_id=None,
            model_order=-1,
            status="running",
            started_at_ms=candidate.time_ms,
            finished_at_ms=None,
            duration_ms=None,
            anchor_seq=seq,
            record_revision=seq,
            block_summaries=[],
            request_ref=None,
            schema_ref=None,
            turn_started_ms=turn_started,
            run_started_ms=run_started,
            extra={
                "source": payload.get("source"),
                "purpose": payload.get("purpose"),
                "agentId": payload.get("agentId"),
                "agentName": payload.get("agentName"),
            },
        )
        self._save_record(record)
        self._upsert_span(
            span_id=candidate.run_id, candidate=candidate, kind="run",
            name=str(payload.get("agentName") or payload.get("source") or "run"),
            status="running", started_at_ms=candidate.time_ms, record_id=record_id,
        )

    def _project_input_record(
        self, candidate: EventCandidate, record_id: str, seq: int,
    ) -> None:
        payload = candidate.payload
        ref = ContentRef.from_dict(cast(dict[str, Any], payload.get("contentRef") or {}))
        turn_started, run_started = self._anchors(candidate)
        record = TrajectoryRecord(
            record_id=record_id,
            session_id=candidate.session_id,
            kind="user",
            run_id=candidate.run_id,
            turn_id=candidate.turn_id,
            step=-1,
            span_id=record_id,
            parent_span_id=None,
            model_order=-1,
            status="succeeded",
            started_at_ms=candidate.time_ms,
            finished_at_ms=candidate.time_ms,
            duration_ms=0,
            anchor_seq=seq,
            record_revision=seq,
            block_summaries=[{"index": 0, "kind": "text", "content": ref.to_dict()}],
            request_ref=None,
            schema_ref=None,
            turn_started_ms=turn_started,
            run_started_ms=run_started,
            extra={"source": payload.get("source"), "mediaRefs": payload.get("mediaRefs") or []},
        )
        self._save_record(record)

    def _project_context_record(
        self, candidate: EventCandidate, record_id: str, seq: int,
    ) -> None:
        payload = candidate.payload
        ref = ContentRef.from_dict(cast(dict[str, Any], payload.get("contentRef") or {}))
        turn_started, run_started = self._anchors(candidate)
        record = TrajectoryRecord(
            record_id=record_id,
            session_id=candidate.session_id,
            kind="context",
            run_id=candidate.run_id,
            turn_id=candidate.turn_id,
            step=candidate.step if candidate.step is not None else 0,
            span_id=candidate.logical_request_id,
            parent_span_id=None,
            model_order=-1,
            status="succeeded",
            started_at_ms=candidate.time_ms,
            finished_at_ms=candidate.time_ms,
            duration_ms=0,
            anchor_seq=seq,
            record_revision=seq,
            block_summaries=[{"index": 0, "kind": "text", "content": ref.to_dict()}],
            request_ref=None,
            schema_ref=None,
            turn_started_ms=turn_started,
            run_started_ms=run_started,
            extra={"contextKind": payload.get("kind"), "source": payload.get("source")},
        )
        self._save_record(record)

    def _project_assistant_record(
        self, candidate: EventCandidate, record_id: str, seq: int,
    ) -> None:
        existing = self._load_record(record_id)
        if existing is not None:
            existing.record_revision = seq
            self._save_record(existing)
            return
        payload = candidate.payload
        turn_started, run_started = self._anchors(candidate)
        request_ref = _optional_ref(payload.get("requestRef"))
        record = TrajectoryRecord(
            record_id=record_id,
            session_id=candidate.session_id,
            kind="assistant",
            run_id=candidate.run_id,
            turn_id=candidate.turn_id,
            step=candidate.step if candidate.step is not None else 0,
            span_id=candidate.logical_request_id,
            parent_span_id=None,
            model_order=_opt_int(payload.get("modelOrder")) or 0,
            status="requested",
            started_at_ms=None,
            finished_at_ms=None,
            duration_ms=None,
            anchor_seq=seq,
            record_revision=seq,
            block_summaries=[],
            request_ref=request_ref,
            schema_ref=None,
            turn_started_ms=turn_started,
            run_started_ms=run_started,
            extra={
                "logicalRequestId": candidate.logical_request_id,
                "purpose": payload.get("purpose"),
                "requestCompleteness": payload.get("requestCompleteness"),
            },
        )
        self._save_record(record)
        self._upsert_span(
            span_id=candidate.logical_request_id or record_id, candidate=candidate,
            kind="model", name=str(payload.get("purpose") or "model"),
            status="requested", started_at_ms=candidate.time_ms, record_id=record_id,
            logical_request_id=candidate.logical_request_id,
        )

    def _project_assistant_update(
        self, candidate: EventCandidate, record_id: str, seq: int,
    ) -> None:
        record = self._load_record(record_id)
        if record is None:
            return
        payload = candidate.payload
        event_type = candidate.type
        if event_type == "model.attempt.started":
            if record.started_at_ms is None:
                record.started_at_ms = candidate.time_ms
            record.status = "running"
            attempts_raw = record.extra.get("attempts")
            attempts: list[dict[str, Any]] = (
                cast(list[dict[str, Any]], attempts_raw)
                if isinstance(attempts_raw, list) else []
            )
            record.extra["attempts"] = attempts
            attempts.append({
                "modelCallId": candidate.model_call_id,
                "attempt": payload.get("attempt"),
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "visibility": payload.get("attemptVisibility"),
                "retryOfCallId": payload.get("retryOfCallId"),
                "startedAtMs": candidate.time_ms,
            })
            self._upsert_span(
                span_id=candidate.model_call_id or record_id, candidate=candidate,
                kind="attempt", name=str(payload.get("model") or "attempt"),
                status="running", started_at_ms=candidate.time_ms, record_id=record_id,
                logical_request_id=candidate.logical_request_id,
                model_call_id=candidate.model_call_id,
            )
        elif event_type == "model.output.delta":
            index = _opt_int(payload.get("blockIndex")) or 0
            ref = _optional_ref(payload.get("contentRef"))
            blocks = {
                _opt_int(item.get("index")) or 0: dict(item)
                for item in record.block_summaries
            }
            if ref is not None:
                blocks[index] = {
                    "index": index,
                    "kind": payload.get("blockKind") or "text",
                    "content": ref.to_dict(),
                }
            record.block_summaries = [blocks[key] for key in sorted(blocks)]
        elif event_type == "model.attempt.finished":
            record.status = str(payload.get("status") or "succeeded")
            record.finished_at_ms = candidate.time_ms
            started = record.started_at_ms or candidate.time_ms
            record.duration_ms = max(0, candidate.time_ms - started)
            blocks = payload.get("blocks")
            if isinstance(blocks, list):
                record.block_summaries = [
                    {
                        "index": _opt_int(item.get("index")) or 0,
                        "kind": item.get("kind") or "text",
                        "content": (item.get("content") or {}),
                    }
                    for item in cast(list[dict[str, Any]], blocks)
                ]
            for key in ("finishReason", "usage", "provider", "model", "attempt",
                        "ttftMs", "generationMs", "errorCode", "errorKind",
                        "providerResponseId", "attemptVisibility"):
                if key in payload:
                    record.extra[key] = payload[key]
            self._upsert_span(
                span_id=candidate.model_call_id or record_id, candidate=candidate,
                kind="attempt", name=str(payload.get("model") or "attempt"),
                status=record.status,
                started_at_ms=_opt_int(payload.get("startedAtMs")),
                finished_at_ms=candidate.time_ms, record_id=record_id,
                duration_ms=_opt_int(payload.get("durationMs")),
                logical_request_id=candidate.logical_request_id,
                model_call_id=candidate.model_call_id,
            )
        elif event_type == "model.retry.scheduled":
            retries_raw = record.extra.get("retries")
            retries: list[dict[str, Any]] = (
                cast(list[dict[str, Any]], retries_raw)
                if isinstance(retries_raw, list) else []
            )
            record.extra["retries"] = retries
            retries.append({
                "attempt": payload.get("attempt"),
                "previousCallId": payload.get("previousCallId"),
                "delayMs": payload.get("delayMs"),
                "errorKind": payload.get("errorKind"),
                "atMs": candidate.time_ms,
            })
        elif event_type == "model.route.changed":
            routes_raw = record.extra.get("routes")
            routes: list[dict[str, Any]] = (
                cast(list[dict[str, Any]], routes_raw)
                if isinstance(routes_raw, list) else []
            )
            record.extra["routes"] = routes
            routes.append({
                "from": payload.get("from"),
                "to": payload.get("to"),
                "reason": payload.get("reason"),
                "atMs": candidate.time_ms,
            })
        record.record_revision = seq
        self._save_record(record)

    def _project_tool_update(
        self, candidate: EventCandidate, record_id: str, seq: int,
    ) -> None:
        payload = candidate.payload
        event_type = candidate.type
        record = self._load_record(record_id)
        if event_type == "execution.linked":
            if record is None:
                return
            record.extra["childRunId"] = payload.get("childRunId")
            record.extra["childExecutionId"] = candidate.execution_id
            record.extra["childSessionId"] = payload.get("childSessionId")
            record.record_revision = seq
            self._save_record(record)
            return
        if record is None:
            turn_started, run_started = self._anchors(candidate)
            record = TrajectoryRecord(
                record_id=record_id,
                session_id=candidate.session_id,
                kind="tool",
                run_id=candidate.run_id,
                turn_id=candidate.turn_id,
                step=candidate.step if candidate.step is not None else 0,
                span_id=candidate.tool_call_id,
                parent_span_id=candidate.span_id,
                model_order=_opt_int(payload.get("modelOrder")),
                status="requested",
                started_at_ms=None,
                finished_at_ms=None,
                duration_ms=None,
                anchor_seq=seq,
                record_revision=seq,
                block_summaries=[],
                request_ref=_optional_ref(payload.get("argumentsRef")),
                schema_ref=_optional_ref(payload.get("schemaRef")),
                turn_started_ms=turn_started,
                run_started_ms=run_started,
                extra={
                    "name": payload.get("name"),
                    "origin": payload.get("origin") or "agent",
                    "providerCallId": payload.get("providerCallId"),
                },
            )
            self._upsert_span(
                span_id=candidate.tool_call_id or record_id, candidate=candidate,
                kind="tool", name=str(payload.get("name") or "tool"),
                status="requested", started_at_ms=None, record_id=record_id,
                tool_call_id=candidate.tool_call_id,
            )
        if event_type == "tool.started":
            record.status = "running"
            record.started_at_ms = candidate.time_ms
            effective = _optional_ref(payload.get("effectiveArgumentsRef"))
            if effective is not None:
                record.extra["effectiveArgumentsRef"] = effective.to_dict()
            if payload.get("queueMs") is not None:
                record.extra["queueMs"] = payload.get("queueMs")
        elif event_type == "tool.finished":
            record.status = str(payload.get("status") or "succeeded")
            record.finished_at_ms = candidate.time_ms
            record.duration_ms = _opt_int(payload.get("durationMs"))
            if record.duration_ms is None and record.started_at_ms is not None:
                record.duration_ms = max(0, candidate.time_ms - record.started_at_ms)
            blocks: list[dict[str, Any]] = []
            result_ref = _optional_ref(payload.get("resultRef"))
            error_ref = _optional_ref(payload.get("errorRef"))
            if result_ref is not None:
                blocks.append({"index": 0, "kind": "result", "content": result_ref.to_dict()})
            if error_ref is not None:
                blocks.append({"index": 1, "kind": "error", "content": error_ref.to_dict()})
            if blocks:
                record.block_summaries = blocks
            for key in ("sideEffectOutcome", "modelObservationRef", "skippedReason"):
                if key in payload:
                    record.extra[key] = payload[key]
        record.record_revision = seq
        self._save_record(record)
        self._upsert_span(
            span_id=candidate.tool_call_id or record_id, candidate=candidate,
            kind="tool", name=str(record.extra.get("name") or "tool"),
            status=record.status, started_at_ms=record.started_at_ms,
            finished_at_ms=record.finished_at_ms, record_id=record_id,
            duration_ms=record.duration_ms, tool_call_id=candidate.tool_call_id,
        )

    def _project_compaction_record(
        self, candidate: EventCandidate, record_id: str, seq: int,
    ) -> None:
        payload = candidate.payload
        turn_started, run_started = self._anchors(candidate)
        ref = _optional_ref(payload.get("summaryRef"))
        record = TrajectoryRecord(
            record_id=record_id,
            session_id=candidate.session_id,
            kind="compaction",
            run_id=candidate.run_id,
            turn_id=candidate.turn_id,
            step=candidate.step if candidate.step is not None else 0,
            span_id=payload.get("compactionId"),
            parent_span_id=None,
            model_order=-1,
            status="running",
            started_at_ms=candidate.time_ms,
            finished_at_ms=None,
            duration_ms=None,
            anchor_seq=seq,
            record_revision=seq,
            block_summaries=([{"index": 0, "kind": "summary", "content": ref.to_dict()}]
                             if ref is not None else []),
            request_ref=None,
            schema_ref=None,
            turn_started_ms=turn_started,
            run_started_ms=run_started,
            extra={
                "reason": payload.get("reason"),
                "inputRange": payload.get("inputRange"),
                "logicalRequestId": payload.get("logicalRequestId"),
            },
        )
        self._save_record(record)

    def _project_compaction_update(
        self, candidate: EventCandidate, record_id: str, seq: int,
    ) -> None:
        record = self._load_record(record_id)
        if record is None:
            return
        payload = candidate.payload
        record.status = str(payload.get("status") or "succeeded")
        record.finished_at_ms = candidate.time_ms
        if record.started_at_ms is not None:
            record.duration_ms = max(0, candidate.time_ms - record.started_at_ms)
        ref = _optional_ref(payload.get("summaryRef"))
        if ref is not None:
            record.block_summaries = [{"index": 0, "kind": "summary", "content": ref.to_dict()}]
        record.record_revision = seq
        self._save_record(record)

    # ------------------------------------------------------------ record rows

    def _load_record(self, record_id: str) -> TrajectoryRecord | None:
        row = self._conn.execute(
            "SELECT session_id, kind, run_id, turn_id, step, span_id, parent_span_id,"
            " model_order, status, started_at_ms, finished_at_ms, duration_ms, anchor_seq,"
            " record_revision, turn_started_ms, run_started_ms, blocks_json,"
            " request_ref_json, schema_ref_json, extra_json FROM trace_records"
            " WHERE record_id=?", (record_id,),
        ).fetchone()
        if row is None:
            return None
        return _record_from_row(record_id, row)

    def _save_record(self, record: TrajectoryRecord) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO trace_records(record_id, session_id, kind, run_id,"
            " turn_id, step, span_id, parent_span_id, model_order, status, started_at_ms,"
            " finished_at_ms, duration_ms, anchor_seq, record_revision, turn_started_ms,"
            " run_started_ms, blocks_json, request_ref_json, schema_ref_json, extra_json)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.record_id, record.session_id, record.kind, record.run_id,
                record.turn_id, record.step, record.span_id, record.parent_span_id,
                record.model_order, record.status, record.started_at_ms,
                record.finished_at_ms, record.duration_ms, record.anchor_seq,
                record.record_revision, record.turn_started_ms, record.run_started_ms,
                _dumps(record.block_summaries),
                _dumps(record.request_ref.to_dict()) if record.request_ref else None,
                _dumps(record.schema_ref.to_dict()) if record.schema_ref else None,
                _dumps(record.extra),
            ),
        )

    def _upsert_span(
        self,
        *,
        span_id: str,
        candidate: EventCandidate,
        kind: str,
        name: str,
        status: str,
        started_at_ms: int | None,
        record_id: str,
        finished_at_ms: int | None = None,
        duration_ms: int | None = None,
        logical_request_id: str | None = None,
        model_call_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO trace_spans(span_id, session_id, run_id, parent_span_id,"
            " kind, name, status, started_at_ms, finished_at_ms, duration_ms,"
            " logical_request_id, model_call_id, tool_call_id, record_id, payload)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                span_id, candidate.session_id, candidate.run_id, candidate.parent_span_id,
                kind, name, status, started_at_ms, finished_at_ms, duration_ms,
                logical_request_id, model_call_id, tool_call_id, record_id,
                _dumps(candidate.payload),
            ),
        )

    # ----------------------------------------------------------------- queries

    def summary(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            head = self._head(session_id)
            runs = self._conn.execute(
                "SELECT run_id, trace_id, turn_id, source, status, started_at_ms,"
                " finished_at_ms, duration_ms, coverage_json FROM trace_runs"
                " WHERE session_id=? ORDER BY started_at_ms ASC, run_id ASC",
                (session_id,),
            ).fetchall()
            coverage = _coverage_from_payload(head["coverage"])
            return {
                "schemaVersion": SCHEMA_VERSION,
                "sessionId": session_id,
                "mode": self.policy.content_mode,
                "available": self._error is None,
                "persistence": "memory" if self.in_memory else "sqlite",
                "retainedFromSeq": head["retained_from_seq"],
                "durableSeq": head["durable_seq"],
                "historyGeneration": head["history_generation"],
                "coverage": coverage.to_dict(),
                "runs": [
                    {
                        "runId": row[0],
                        "traceId": row[1],
                        "turnId": row[2],
                        "source": row[3],
                        "status": row[4],
                        "startedAtMs": _opt_int(row[5]),
                        "finishedAtMs": _opt_int(row[6]),
                        "durationMs": _opt_int(row[7]),
                        "coverage": _coverage_from_payload(
                            json.loads(row[8]) if row[8] else {},
                        ).to_dict(),
                    }
                    for row in runs
                ],
                "capabilities": {"events": True, "bodies": True, "memory": self.in_memory},
            }

    def records_page(
        self, session_id: str, *, before_cursor: str | None = None, limit: int = 200,
    ) -> RecordPage:
        if not 1 <= limit <= 500:
            raise TrajectoryInvalidCursor("limit must be between 1 and 500")
        with self._lock:
            head = self._head(session_id)
            params: list[Any] = [session_id]
            clause = ""
            if before_cursor:
                boundary = _decode_cursor(before_cursor)
                if len(boundary) != 8 or boundary[0] != session_id:
                    raise TrajectoryInvalidCursor("invalid cursor scope")
                if boundary[1] != head["history_generation"]:
                    raise TrajectoryCursorExpired("history generation changed")
                clause = (
                    " AND (turn_started_ms, run_started_ms, step, model_order,"
                    " anchor_seq, record_id) < (?,?,?,?,?,?)"
                )
                params.extend(boundary[2:8])
            rows = self._conn.execute(
                "SELECT record_id, session_id, kind, run_id, turn_id, step, span_id,"
                " parent_span_id, model_order, status, started_at_ms, finished_at_ms,"
                " duration_ms, anchor_seq, record_revision, turn_started_ms,"
                " run_started_ms, blocks_json, request_ref_json, schema_ref_json,"
                " extra_json FROM trace_records WHERE session_id=?" + clause +
                " ORDER BY turn_started_ms DESC, run_started_ms DESC, step DESC,"
                " model_order DESC, anchor_seq DESC, record_id DESC LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            has_older = len(rows) > limit
            selected = rows[:limit]
            records = [_record_from_row(row[0], row[1:]) for row in reversed(selected)]
            next_cursor = None
            if has_older and records:
                first = records[0]
                next_cursor = _encode_cursor([
                    session_id, head["history_generation"], first.turn_started_ms,
                    first.run_started_ms, first.step if first.step is not None else -1,
                    first.model_order if first.model_order is not None else -1,
                    first.anchor_seq, first.record_id,
                ])
            stubs: list[dict[str, Any]] = []
            for record in records:
                parent = record.parent_span_id
                if parent and not any(item["spanId"] == parent for item in stubs):
                    stubs.append({
                        "spanId": parent,
                        "parentSpanId": None,
                        "label": "parent",
                    })
            return RecordPage(
                records=records,
                snapshot_seq=head["durable_seq"],
                history_generation=head["history_generation"],
                retained_from_seq=head["retained_from_seq"],
                has_older=has_older,
                next_before_cursor=next_cursor,
                ancestor_stubs=stubs,
                coverage=_coverage_from_payload(head["coverage"]),
            )

    def events_page(
        self, session_id: str, *, after_seq: int | None = None,
        before_seq: int | None = None, limit: int = 200,
    ) -> EventPage:
        if not 1 <= limit <= 500:
            raise TrajectoryInvalidCursor("limit must be between 1 and 500")
        if after_seq is not None and before_seq is not None:
            raise TrajectoryInvalidCursor("afterSeq and beforeSeq are exclusive")
        with self._lock:
            head = self._head(session_id)
            snapshot = int(head["durable_seq"])
            retained = int(head["retained_from_seq"])
            generation = int(head["history_generation"])
            if after_seq is not None:
                if after_seq > snapshot:
                    raise TrajectoryInvalidCursor("afterSeq is ahead of durableSeq")
                if after_seq < retained - 1:
                    raise TrajectoryCursorExpired("afterSeq precedes retained history")
                rows = self._conn.execute(
                    "SELECT * FROM trace_events WHERE session_id=? AND seq>? AND seq<=?"
                    " ORDER BY seq ASC LIMIT ?",
                    (session_id, after_seq, snapshot, limit + 1),
                ).fetchall()
                has_more = len(rows) > limit
                rows = rows[:limit]
                next_after = rows[-1][1] if rows else after_seq
                next_before = rows[0][1] if rows else None
                has_older = bool(rows) and rows[0][1] > retained
            else:
                upper = snapshot if before_seq is None else before_seq
                if before_seq is not None and before_seq <= retained:
                    raise TrajectoryCursorExpired("beforeSeq precedes retained history")
                rows = self._conn.execute(
                    "SELECT * FROM trace_events WHERE session_id=? AND seq<? "
                    "ORDER BY seq DESC LIMIT ?",
                    (session_id, upper, limit + 1),
                ).fetchall()
                has_more = False
                rows = list(reversed(rows[:limit]))
                next_before = rows[0][1] if rows else None
                next_after = rows[-1][1] if rows else min(upper - 1, snapshot)
                has_older = bool(rows) and rows[0][1] > retained
            events = [_event_from_row(row) for row in rows]
            touched: set[str] = set()
            for event in events:
                record_id = self._record_id_for(event.type, event.payload)
                if record_id:
                    touched.add(record_id)
            updates = tuple(
                record for record_id in sorted(touched)
                if (record := self._load_record(record_id)) is not None
            )
            return EventPage(
                events=events,
                record_updates=list(updates),
                snapshot_seq=snapshot,
                retained_from_seq=retained,
                durable_seq=snapshot,
                history_generation=generation,
                has_older=has_older,
                has_more=has_more,
                next_before_seq=next_before,
                next_after_seq=next_after,
                coverage=_coverage_from_payload(head["coverage"]),
            )

    def span_detail(self, session_id: str, span_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT span_id, run_id, parent_span_id, kind, name, status,"
                " started_at_ms, finished_at_ms, duration_ms, logical_request_id,"
                " model_call_id, tool_call_id, record_id, payload FROM trace_spans"
                " WHERE session_id=? AND span_id=?", (session_id, span_id),
            ).fetchone()
            if row is None:
                return None
            record = self._load_record(row[12]) if row[12] else None
            body = None
            if row[13]:
                try:
                    body = json.loads(row[13])
                except json.JSONDecodeError:
                    body = None
            return {
                "spanId": row[0],
                "runId": row[1],
                "parentSpanId": row[2],
                "kind": row[3],
                "name": row[4],
                "status": row[5],
                "startedAtMs": _opt_int(row[6]),
                "finishedAtMs": _opt_int(row[7]),
                "durationMs": _opt_int(row[8]),
                "logicalRequestId": row[9],
                "modelCallId": row[10],
                "toolCallId": row[11],
                "record": record.to_dict() if record else None,
                "payload": body,
            }

    def body_slice(
        self, session_id: str, body_id: str, *, offset_bytes: int = 0,
        limit_bytes: int = 65536,
    ) -> dict[str, Any] | None:
        if offset_bytes < 0 or not 1 <= limit_bytes <= 256 * 1024:
            raise TrajectoryInvalidCursor("invalid body range")
        with self._lock:
            row = self._conn.execute(
                "SELECT content_type, text, stored_bytes, original_bytes, redacted,"
                " truncated FROM trace_bodies WHERE session_id=? AND body_id=?",
                (session_id, body_id),
            ).fetchone()
            if row is None:
                return None
            text = str(row[1])
            encoded = text.encode("utf-8")
            start = min(offset_bytes, len(encoded))
            end = min(len(encoded), start + limit_bytes)
            while start > 0 and start < len(encoded):
                try:
                    encoded[start:start + 1].decode("utf-8")
                    break
                except UnicodeDecodeError:
                    start += 1
            while end < len(encoded):
                try:
                    encoded[:end].decode("utf-8")
                    break
                except UnicodeDecodeError:
                    end -= 1
            chunk = encoded[start:end].decode("utf-8", errors="ignore")
            return {
                "bodyId": body_id,
                "contentType": row[0],
                "text": chunk,
                "offsetBytes": start,
                "nextOffsetBytes": end,
                "hasMore": end < len(encoded),
                "storedBytes": int(row[2]),
                "originalBytes": int(row[3]),
                "redacted": bool(row[4]),
                "truncated": bool(row[5]),
            }

    def purge_session(self, session_id: str) -> None:
        """Delete one session's trajectory facts and bodies (idempotent)."""
        with self._lock, self._conn:
            for table in (
                "trace_events", "trace_records", "trace_spans", "trace_turns",
                "trace_bodies", "trace_runs", "trace_heads",
            ):
                self._conn.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))

    def prune(self, *, now_ms: int | None = None) -> None:
        """Public retention entry used by tests and explicit maintenance."""
        self._prune(now_ms)

    # --------------------------------------------------------------- retention

    def _prune(self, now_ms: int | None = None) -> None:
        cutoff = (now_ms if now_ms is not None else _now_ms()) - (
            self.retention_days * 24 * 60 * 60 * 1000
        )
        with self._lock, self._conn:
            sessions = self._conn.execute(
                "SELECT DISTINCT session_id FROM trace_runs"
            ).fetchall()
            for (session_id,) in sessions:
                head = self._head(session_id)
                durable = int(head["durable_seq"])
                kept = [
                    row[0] for row in self._conn.execute(
                        "SELECT run_id FROM trace_runs WHERE session_id=?"
                        " AND (status IN ('running','requested') OR finished_at_ms IS NULL"
                        " OR finished_at_ms>=?)",
                        (session_id, cutoff),
                    ).fetchall()
                ]
                if kept:
                    placeholders = ",".join("?" for _ in kept)
                    row = self._conn.execute(
                        f"SELECT MIN(seq) FROM trace_events WHERE session_id=?"
                        f" AND run_id IN ({placeholders})",
                        (session_id, *kept),
                    ).fetchone()
                    min_kept = _opt_int(row[0]) if row is not None else None
                    if min_kept is None:
                        # 保留的 run 还没有事件时无法证明前缀安全，跳过本会话。
                        continue
                    prefix_end = min_kept - 1
                else:
                    prefix_end = durable
                if prefix_end <= 0:
                    continue
                placeholders = ",".join("?" for _ in kept) if kept else None
                self._conn.execute(
                    "DELETE FROM trace_events WHERE session_id=? AND seq<=?",
                    (session_id, prefix_end),
                )
                if kept and placeholders is not None:
                    self._conn.execute(
                        f"DELETE FROM trace_records WHERE session_id=?"
                        f" AND anchor_seq<=? AND record_revision<=?"
                        f" AND run_id NOT IN ({placeholders})",
                        (session_id, prefix_end, prefix_end, *kept),
                    )
                    self._conn.execute(
                        f"DELETE FROM trace_runs WHERE session_id=?"
                        f" AND run_id NOT IN ({placeholders}) AND finished_at_ms<?",
                        (session_id, *kept, cutoff),
                    )
                else:
                    self._conn.execute(
                        "DELETE FROM trace_records WHERE session_id=?",
                        (session_id,),
                    )
                    self._conn.execute(
                        "DELETE FROM trace_runs WHERE session_id=? AND finished_at_ms<?",
                        (session_id, cutoff),
                    )
                self._conn.execute(
                    "UPDATE trace_heads SET retained_from_seq=?, history_generation=?,"
                    " updated_at_ms=? WHERE session_id=?",
                    (prefix_end + 1, int(head["history_generation"]) + 1, _now_ms(), session_id),
                )

    # ------------------------------------------------------------------- close

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout=5.0)
        with self._lock:
            self._conn.close()
            if self._writer_lock is not None:
                self._writer_lock.release()


def _opt_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _optional_ref(value: object) -> ContentRef | None:
    if isinstance(value, dict):
        return ContentRef.from_dict(cast(dict[str, Any], value))
    return None


def _record_from_row(record_id: str, row: tuple[Any, ...]) -> TrajectoryRecord:
    return TrajectoryRecord(
        record_id=record_id,
        session_id=str(row[0]),
        kind=str(row[1]),
        run_id=str(row[2]),
        turn_id=row[3] if isinstance(row[3], str) else None,
        step=_opt_int(row[4]),
        span_id=row[5] if isinstance(row[5], str) else None,
        parent_span_id=row[6] if isinstance(row[6], str) else None,
        model_order=_opt_int(row[7]),
        status=str(row[8]),
        started_at_ms=_opt_int(row[9]),
        finished_at_ms=_opt_int(row[10]),
        duration_ms=_opt_int(row[11]),
        anchor_seq=int(row[12]),
        record_revision=int(row[13]),
        turn_started_ms=int(row[14] or 0),
        run_started_ms=int(row[15] or 0),
        block_summaries=json.loads(row[16]) if row[16] else [],
        request_ref=_optional_ref(json.loads(row[17]) if row[17] else None),
        schema_ref=_optional_ref(json.loads(row[18]) if row[18] else None),
        extra=json.loads(row[19]) if row[19] else {},
    )


def _event_from_row(row: Iterable[Any]) -> TrajectoryEvent:
    values = list(row)
    return TrajectoryEvent(
        schema_version=SCHEMA_VERSION,
        event_id=str(values[2]),
        session_id=str(values[0]),
        seq=int(values[1]),
        trace_id=str(values[3]),
        run_id=str(values[4]),
        turn_id=values[5] if isinstance(values[5], str) else None,
        step=_opt_int(values[6]),
        span_id=values[7] if isinstance(values[7], str) else None,
        parent_span_id=values[8] if isinstance(values[8], str) else None,
        execution_id=values[9] if isinstance(values[9], str) else None,
        logical_request_id=values[10] if isinstance(values[10], str) else None,
        model_call_id=values[11] if isinstance(values[11], str) else None,
        tool_call_id=values[12] if isinstance(values[12], str) else None,
        type=str(values[13]),
        ignorable=bool(values[14]),
        time_ms=int(values[15]),
        payload=json.loads(values[16]) if values[16] else {},
    )


__all__ = [
    "TrajectoryStore",
    "TrajectoryWriterBusy",
    "TrajectoryUnavailable",
    "TrajectoryCursorExpired",
    "TrajectoryInvalidCursor",
    "unavailable_ref",
]
