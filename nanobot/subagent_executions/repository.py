"""独占 writer 的 SQLite 执行账本；状态与事件在同一事务落盘。"""
from __future__ import annotations

import base64
import json
import sqlite3
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from filelock import FileLock, Timeout

from nanobot.subagent_executions.events import ActivityThrottle, project_execution_event
from nanobot.subagent_executions.models import (
    PHASES,
    TERMINAL_STATUSES,
    EventPage,
    ExecutionActivity,
    ExecutionEvent,
    ExecutionPage,
    ExecutionSnapshot,
    camel,
)


class WriterBusy(RuntimeError):  # noqa: N818 - 与领域契约和现有团队域名称一致
    """当前 workspace 已被另一个执行观察 writer 占有。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed(start: str, end: str) -> int:
    return max(0, int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000))


def _text(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ''


def _result(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    summary = _text(value.get('summary'), 8000)
    return {
        'summary': summary,
        'truncated': bool(value.get('truncated')) or len(_text(value.get('summary'), 8001)) > 8000,
        'stopReason': _text(value.get('stopReason'), 120),
        'errorCode': _text(value.get('errorCode'), 120) or None,
        'errorMessage': _text(value.get('errorMessage'), 1000) or None,
    }


def _decode_snapshot(payload: str) -> ExecutionSnapshot:
    data = json.loads(payload)
    activities = data.pop('activities', [])
    values = {key: data[camel(key)] for key in ExecutionSnapshot.__dataclass_fields__
              if key != 'activities' and camel(key) in data}
    snapshot = ExecutionSnapshot(**values)
    snapshot.activities = [ExecutionActivity(**{
        key: item[camel(key)] for key in ExecutionActivity.__dataclass_fields__
        if camel(key) in item
    }) for item in activities]
    return snapshot


def _decode_event(payload: str) -> ExecutionEvent:
    data = json.loads(payload)
    return ExecutionEvent(**{key: data[camel(key)] for key in ExecutionEvent.__dataclass_fields__
                             if camel(key) in data})


class ExecutionRepository:
    """无 workspace 时完全内存运行；持久库与 memory/state.db 无关联。"""

    def __init__(self, workspace: Path | str | None = None) -> None:
        self._lock = threading.RLock()
        self._writer: FileLock | None = None
        self._closed = False
        self._started: dict[str, float] = {}
        self._throttle = ActivityThrottle()
        self._next_prune = 0.0
        self.db_path: Path | None = None
        if workspace is not None:
            directory = Path(workspace).expanduser().resolve() / 'runtime'
            directory.mkdir(parents=True, exist_ok=True)
            self.db_path = directory / 'agent-executions.db'
            # 仓储可跨线程关闭；锁上下文需共享，生命周期由 self._lock 串行保护。
            self._writer = FileLock(
                str(directory / 'agent-executions.writer.lock'), thread_local=False,
            )
            try:
                self._writer.acquire(timeout=0)
            except Timeout:
                raise WriterBusy('EXECUTION_WRITER_BUSY: workspace writer already held') from None
        try:
            self._conn = sqlite3.connect(
                str(self.db_path) if self.db_path else ':memory:', check_same_thread=False,
            )
            self._conn.execute('PRAGMA foreign_keys=ON')
            self._conn.executescript('''
                CREATE TABLE IF NOT EXISTS executions (
                    invocation_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS executions_chat_idx
                    ON executions(chat_id, created_at DESC, invocation_id DESC);
                CREATE TABLE IF NOT EXISTS execution_events (
                    invocation_id TEXT NOT NULL REFERENCES executions(invocation_id),
                    seq INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(invocation_id, seq)
                );
            ''')
            # 只有已取得 writer 独占权的实例才能收口旧执行。
            for row in self._conn.execute(
                "SELECT invocation_id FROM executions WHERE status IN ('queued','running')"
            ).fetchall():
                self.finish(row[0], 'interrupted')
            self.prune()
        except BaseException:
            if hasattr(self, '_conn'):
                self._conn.close()
            if self._writer is not None:
                self._writer.release()
            raise

    def _get(self, invocation_id: str) -> ExecutionSnapshot:
        row = self._conn.execute(
            'SELECT payload FROM executions WHERE invocation_id=?', (invocation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(invocation_id)
        return _decode_snapshot(row[0])

    def _save_event(
        self, snapshot: ExecutionSnapshot, event_type: str,
        activity: ExecutionActivity | None = None,
    ) -> None:
        snapshot.last_seq += 1
        public = snapshot.to_dict()
        for key in ('instructionSummary', 'result', 'activities'):
            public.pop(key, None)
        data: dict[str, Any] = {'execution': public}
        if activity is not None:
            data['activity'] = activity.to_dict()
        event = ExecutionEvent(
            invocation_id=snapshot.invocation_id, chat_id=snapshot.chat_id,
            parent_turn_id=snapshot.parent_turn_id,
            parent_tool_call_id=snapshot.parent_tool_call_id,
            seq=snapshot.last_seq, occurred_at=_now(), type=event_type, data=data,
        )
        projected = project_execution_event(event)
        if projected is None:
            raise ValueError('public execution event exceeds size budget')
        self._conn.execute(
            'INSERT INTO executions VALUES(?,?,?,?,?) ON CONFLICT(invocation_id) '
            'DO UPDATE SET status=excluded.status, payload=excluded.payload',
            (snapshot.invocation_id, snapshot.chat_id, snapshot.created_at, snapshot.status,
             json.dumps(snapshot.to_dict(), ensure_ascii=False)),
        )
        self._conn.execute(
            'INSERT INTO execution_events VALUES(?,?,?)',
            (snapshot.invocation_id, snapshot.last_seq, json.dumps(projected, ensure_ascii=False)),
        )

    def create(
        self, *, chat_id: str, parent_turn_id: str, parent_tool_call_id: str,
        agent_id: str | None = None, name: str = '', title: str = '',
        mode: str = 'background', instruction_summary: str = '',
    ) -> ExecutionSnapshot:
        """摘要必须由调用者完成公开投影；此 API 不接受原始 task。"""
        if mode not in {'background', 'wait'}:
            raise ValueError('invalid execution mode')
        snapshot = ExecutionSnapshot(
            invocation_id=str(uuid4()), chat_id=chat_id, parent_turn_id=parent_turn_id,
            parent_tool_call_id=parent_tool_call_id, agent_id=agent_id,
            agent_name=_text(name, 80), title=_text(title, 120),
            instruction_summary=_text(instruction_summary, 2000), mode=mode, created_at=_now(),
        )
        with self._lock, self._conn:
            if time.monotonic() >= self._next_prune:
                self.prune()
            self._save_event(snapshot, 'execution.created')
        return snapshot

    def start(self, invocation_id: str) -> ExecutionSnapshot:
        with self._lock, self._conn:
            snapshot = self._get(invocation_id)
            if snapshot.status != 'queued':
                return snapshot
            snapshot.status = 'running'
            snapshot.started_at = _now()
            snapshot.queue_duration_ms = _elapsed(snapshot.created_at, snapshot.started_at)
            self._save_event(snapshot, 'execution.started')
            self._started[invocation_id] = time.monotonic()
            return snapshot

    def set_phase(
        self, invocation_id: str, phase: str, *, last_action: str | None = None,
    ) -> ExecutionSnapshot:
        if phase not in PHASES:
            raise ValueError('invalid execution phase')
        with self._lock, self._conn:
            snapshot = self._get(invocation_id)
            if snapshot.status in TERMINAL_STATUSES:
                return snapshot
            changed = snapshot.phase != phase
            event_type = 'execution.phase_changed' if changed else 'execution.activity'
            # 节流命中时不修改内存，保证返回值与落库值一致（审查 P2-3）。
            if not self._throttle.allow(invocation_id, event_type):
                return snapshot
            snapshot.phase = phase
            if last_action is not None:
                snapshot.last_action = _text(last_action, 240)
            self._save_event(snapshot, event_type)
            return snapshot

    def start_activity(
        self, invocation_id: str, tool_name: str | None, *, activity_id: str | None = None,
        label: str | None = None,
    ) -> ExecutionActivity:
        with self._lock, self._conn:
            snapshot = self._get(invocation_id)
            if snapshot.status in TERMINAL_STATUSES:
                raise ValueError('execution already finished')
            identifier = activity_id or str(uuid4())
            for existing in snapshot.activities:
                if existing.activity_id == identifier:
                    return existing
            activity = ExecutionActivity(
                activity_id=identifier, tool_name=_text(tool_name, 120) or None,
                label=_text(label if label is not None else tool_name, 240), started_at=_now(),
            )
            snapshot.activities.append(activity)
            snapshot.last_action = activity.label
            # 工具生命周期边界直接落盘，不经过进度节流闸门。
            self._save_event(snapshot, 'execution.activity', activity)
            return activity

    def finish_activity(
        self, invocation_id: str, activity_id: str, status: str = 'succeeded',
        *, detail: str | None = None, output: str | None = None,
    ) -> ExecutionActivity:
        if status not in TERMINAL_STATUSES:
            raise ValueError('invalid activity terminal status')
        with self._lock, self._conn:
            snapshot = self._get(invocation_id)
            activity = next((a for a in snapshot.activities if a.activity_id == activity_id), None)
            if activity is None:
                raise KeyError(activity_id)
            if activity.status != 'running' or snapshot.status in TERMINAL_STATUSES:
                return activity
            activity.status, activity.finished_at = status, _now()
            if detail is not None:
                activity.detail = detail
            if output is not None:
                activity.output = output
            snapshot.completed_tool_count += 1
            self._save_event(snapshot, 'execution.activity', activity)
            return activity

    def finish(
        self, invocation_id: str, status: str, *, result: Mapping[str, Any] | None = None,
    ) -> ExecutionSnapshot:
        if status not in TERMINAL_STATUSES:
            raise ValueError('invalid execution terminal status')
        with self._lock, self._conn:
            snapshot = self._get(invocation_id)
            if snapshot.status in TERMINAL_STATUSES:
                return snapshot
            snapshot.status, snapshot.finished_at = status, _now()
            started = self._started.get(invocation_id)
            if started is not None:
                snapshot.duration_ms = max(0, int((time.monotonic() - started) * 1000))
            elif snapshot.started_at and snapshot.finished_at:
                # 进程重启后的 interrupted 收口没有 monotonic 起点，退回墙钟差值，
                # 否则用户会看到跑了几分钟的任务耗时 0 毫秒（审查 P1-1）。
                snapshot.duration_ms = _elapsed(snapshot.started_at, snapshot.finished_at)
            else:
                snapshot.duration_ms = 0
            snapshot.result = _result(result)
            if status != 'succeeded':
                for activity in snapshot.activities:
                    if activity.status == 'running':
                        activity.status = status
                        activity.finished_at = snapshot.finished_at
                        snapshot.completed_tool_count += 1
                        self._save_event(snapshot, 'execution.activity', activity)
            self._save_event(snapshot, 'execution.finished')
            self._started.pop(invocation_id, None)
            self._throttle.allow(invocation_id, 'execution.finished')
            return snapshot

    def detail(self, chat_id: str, invocation_id: str) -> ExecutionSnapshot | None:
        with self._lock:
            row = self._conn.execute(
                'SELECT payload FROM executions WHERE chat_id=? AND invocation_id=?',
                (chat_id, invocation_id),
            ).fetchone()
            return _decode_snapshot(row[0]) if row else None

    def list(
        self, chat_id: str, *, limit: int = 20, cursor: str | None = None,
        parent_turn_id: str | None = None,
    ) -> ExecutionPage:
        if not 1 <= limit <= 100:
            raise ValueError('limit must be between 1 and 100')
        with self._lock:
            rows = self._conn.execute(
                'SELECT payload FROM executions WHERE chat_id=? '
                'ORDER BY created_at DESC, invocation_id DESC', (chat_id,),
            ).fetchall()
            snapshots = [_decode_snapshot(row[0]) for row in rows]
            if parent_turn_id is not None:
                snapshots = [item for item in snapshots if item.parent_turn_id == parent_turn_id]
            active = [item for item in snapshots if item.status not in TERMINAL_STATUSES]
            history = [item for item in snapshots if item.status in TERMINAL_STATUSES]
            if cursor is not None:
                try:
                    boundary = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                    if (not isinstance(boundary, list) or len(cast("list[Any]", boundary)) != 4
                            or boundary[:2] != [chat_id, parent_turn_id]):
                        raise ValueError('invalid cursor scope')
                    index = next(i for i, item in enumerate(history)
                                 if [item.created_at, item.invocation_id] == boundary[2:])
                except (ValueError, UnicodeError, StopIteration) as exc:
                    raise ValueError('invalid or expired execution cursor') from exc
                history = history[index + 1:]
            selected = history[:limit]
            has_more = len(history) > limit
            next_cursor = None
            if has_more:
                last = selected[-1]
                next_cursor = base64.urlsafe_b64encode(json.dumps(
                    [chat_id, parent_turn_id, last.created_at, last.invocation_id],
                ).encode()).decode()
            return ExecutionPage(active + selected, next_cursor, has_more, _now())

    def events(
        self, chat_id: str, invocation_id: str, *, after_seq: int = 0,
        through_seq: int | None = None, limit: int = 100,
    ) -> EventPage:
        if after_seq < 0 or (through_seq is not None and through_seq < 0) or not 1 <= limit <= 200:
            raise ValueError('invalid event range or limit')
        with self._lock:
            snapshot = self.detail(chat_id, invocation_id)
            if snapshot is None:
                return EventPage([])
            end = min(through_seq, snapshot.last_seq) if through_seq is not None else snapshot.last_seq
            rows = self._conn.execute(
                'SELECT payload FROM execution_events WHERE invocation_id=? AND seq>? AND seq<=? '
                'ORDER BY seq LIMIT ?', (invocation_id, after_seq, end, limit + 1),
            ).fetchall()
            earliest = self._conn.execute(
                'SELECT MIN(seq) FROM execution_events WHERE invocation_id=?', (invocation_id,),
            ).fetchone()[0]
            reset = (after_seq > snapshot.last_seq
                     or (earliest is None and after_seq < snapshot.last_seq)
                     or (earliest is not None and after_seq < earliest - 1))
            return EventPage([_decode_event(row[0]) for row in rows[:limit]], snapshot.last_seq,
                             len(rows) > limit, reset)

    def prune(self, *, now: datetime | None = None) -> None:
        """终态事件保留 7 天，快照保留 30 天；非终态执行不清理。"""
        current = now if now is not None else datetime.now(timezone.utc)
        event_cutoff = current - timedelta(days=7)
        snapshot_cutoff = current - timedelta(days=30)
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT payload FROM executions WHERE status NOT IN ('queued','running')"
            ).fetchall()
            for row in rows:
                snapshot = _decode_snapshot(row[0])
                if snapshot.finished_at is None:
                    continue
                finished = datetime.fromisoformat(snapshot.finished_at)
                if finished < event_cutoff:
                    self._conn.execute('DELETE FROM execution_events WHERE invocation_id=?',
                                       (snapshot.invocation_id,))
                if finished < snapshot_cutoff:
                    self._conn.execute('DELETE FROM executions WHERE invocation_id=?',
                                       (snapshot.invocation_id,))
            self._next_prune = time.monotonic() + 3600

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._conn.close()
            self._closed = True
            if self._writer is not None:
                self._writer.release()
