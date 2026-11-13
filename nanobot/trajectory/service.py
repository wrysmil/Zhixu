"""轨迹服务：按 workspace 管理存储、写入队列与只读查询。

Gateway 在启动时用配置初始化单例；AgentLoop / Runner / Provider / 工具通过
``capture`` 门面写事件。只读路由必须复用本进程的 store（writer 唯一），
跨进程只读则打开不经 writer 锁的只读连接。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

from nanobot.trajectory.models import CommitBatch, TraceContext
from nanobot.trajectory.policy import (
    DEFAULT_BODY_LIMIT_BYTES,
    DEFAULT_RUN_BODY_BUDGET_BYTES,
    ContentPolicy,
)
from nanobot.trajectory.recorder import TraceRecorder
from nanobot.trajectory.store import (
    TrajectoryStore,
    TrajectoryUnavailable,
    TrajectoryWriterBusy,
)

VALID_MODES = frozenset({"off", "metadata", "standard", "raw-chunks"})


@dataclass(slots=True)
class TrajectorySettings:
    enabled: bool = True
    mode: str = "standard"
    retention_days: int = 30
    max_body_mb: int = 8
    max_run_body_mb: int = 100

    def normalized_mode(self) -> str:
        if not self.enabled:
            return "off"
        return self.mode if self.mode in VALID_MODES else "standard"


class TrajectoryService:
    def __init__(self, settings: TrajectorySettings | None = None) -> None:
        self._settings = settings or TrajectorySettings()
        self._lock = threading.RLock()
        self._stores: dict[str, TrajectoryStore] = {}
        self._memory_stores: dict[str, TrajectoryStore] = {}
        self._listeners: list[Callable[[CommitBatch], None]] = []
        self._degraded: dict[str, str] = {}
        self._closed = False

    # --------------------------------------------------------------- settings

    @property
    def settings(self) -> TrajectorySettings:
        return self._settings

    @property
    def mode(self) -> str:
        return self._settings.normalized_mode()

    def configure(self, settings: TrajectorySettings) -> None:
        with self._lock:
            previous = self._settings.normalized_mode()
            self._settings = settings
            current = settings.normalized_mode()
        if previous != current:
            self.flush_all()
        if current == "off":
            logger.info("trajectory capture disabled")

    # --------------------------------------------------------------- listeners

    def add_commit_listener(self, listener: Callable[[CommitBatch], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def _fanout(self, batch: CommitBatch) -> None:
        for listener in list(self._listeners):
            try:
                listener(batch)
            except Exception:
                logger.exception("trajectory commit listener failed")

    # ------------------------------------------------------------------ stores

    def _policy(self) -> ContentPolicy:
        mode = self.mode
        return ContentPolicy(
            content_mode=mode,
            body_limit_bytes=self._settings.max_body_mb * 1024 * 1024,
            run_body_budget_bytes=(
                self._settings.max_run_body_mb * 1024 * 1024
                if self._settings.max_run_body_mb > 0 else DEFAULT_RUN_BODY_BUDGET_BYTES
            ),
            store_bodies=mode in {"standard", "raw-chunks"},
        )

    def _create_persistent(self, workspace: str) -> TrajectoryStore:
        store = TrajectoryStore(
            workspace,
            retention_days=self._settings.retention_days,
            policy=self._policy(),
        )
        store.add_listener(self._fanout)
        return store

    def store_for_write(self, workspace: str) -> TrajectoryStore | None:
        if self.mode == "off" or self._closed:
            return None
        key = str(Path(workspace).expanduser().resolve())
        with self._lock:
            existing = self._stores.get(key)
            if existing is not None:
                return existing
            if key in self._degraded:
                return None
            try:
                store = self._create_persistent(key)
            except (TrajectoryWriterBusy, TrajectoryUnavailable) as exc:
                self._degraded[key] = str(exc)
                logger.warning("trajectory store unavailable for {}: {}", key, exc)
                return None
            except Exception:
                logger.exception("trajectory store creation failed for {}", key)
                self._degraded[key] = "creation_failed"
                return None
            self._stores[key] = store
            return store

    def memory_store_for(self, session_id: str) -> TrajectoryStore | None:
        if self.mode == "off" or self._closed:
            return None
        with self._lock:
            existing = self._memory_stores.get(session_id)
            if existing is not None:
                return existing
            store = TrajectoryStore(
                in_memory=True,
                retention_days=self._settings.retention_days,
                policy=self._policy(),
            )
            store.add_listener(self._fanout)
            self._memory_stores[session_id] = store
            return store

    def _reader(self, workspace: str) -> TrajectoryStore | None:
        key = str(Path(workspace).expanduser().resolve())
        with self._lock:
            existing = self._stores.get(key)
            if existing is not None:
                return existing
        db_path = Path(key) / "runtime" / "trajectory.db"
        if not db_path.is_file():
            return None
        try:
            return TrajectoryStore(workspace=key, read_only=True)
        except TrajectoryUnavailable:
            return None
        except Exception:
            logger.exception("trajectory reader open failed for {}", key)
            return None

    def _session_store(self, workspace: str, session_id: str) -> TrajectoryStore | None:
        with self._lock:
            memory = self._memory_stores.get(session_id)
        if memory is not None:
            return memory
        return self._reader(workspace)

    # ------------------------------------------------------------------ run

    def begin_run(
        self,
        *,
        workspace: str,
        session_id: str,
        turn_id: str | None,
        source: str = "user",
        ephemeral: bool = False,
        purpose: str | None = None,
        visible: bool = False,
        execution_id: str | None = None,
        parent_recorder: TraceRecorder | None = None,
        parent_span_id: str | None = None,
        agent_id: str | None = None,
        agent_name: str | None = None,
    ) -> TraceRecorder | None:
        store = (
            self.memory_store_for(session_id) if ephemeral
            else self.store_for_write(workspace)
        )
        if store is None:
            return None
        parent_ctx = parent_recorder.context if parent_recorder is not None else None
        context = TraceContext(
            workspace=str(Path(workspace).expanduser().resolve()),
            workspace_id=str(Path(workspace).expanduser().resolve()),
            session_id=session_id,
            trace_id=parent_ctx.trace_id if parent_ctx else uuid4().hex,
            run_id=uuid4().hex,
            turn_id=turn_id,
            execution_id=execution_id,
            parent_span_id=parent_span_id or (parent_ctx.parent_span_id if parent_ctx else None),
            source=source,
            ephemeral=ephemeral,
            agent_id=agent_id,
            agent_name=agent_name,
            parent_run_id=parent_ctx.run_id if parent_ctx else None,
        )
        policy = self._policy()
        recorder = TraceRecorder(
            context,
            policy=policy,
            sink=store.enqueue,
            run_body_budget_bytes=policy.run_body_budget_bytes,
        )
        recorder.start(purpose=purpose, visible=visible)
        return recorder

    # ------------------------------------------------------------------- read

    def summary(self, workspace: str, session_id: str) -> dict[str, Any] | None:
        store = self._session_store(workspace, session_id)
        if store is None:
            return None
        return store.summary(session_id)

    def records_page(
        self, workspace: str, session_id: str, *, before_cursor: str | None = None,
        limit: int = 200,
    ) -> Any:
        store = self._session_store(workspace, session_id)
        if store is None:
            return None
        return store.records_page(session_id, before_cursor=before_cursor, limit=limit)

    def events_page(
        self, workspace: str, session_id: str, *, after_seq: int | None = None,
        before_seq: int | None = None, limit: int = 200,
    ) -> Any:
        store = self._session_store(workspace, session_id)
        if store is None:
            return None
        return store.events_page(
            session_id, after_seq=after_seq, before_seq=before_seq, limit=limit,
        )

    def span_detail(self, workspace: str, session_id: str, span_id: str) -> Any:
        store = self._session_store(workspace, session_id)
        if store is None:
            return None
        return store.span_detail(session_id, span_id)

    def body_slice(
        self, workspace: str, session_id: str, body_id: str, *,
        offset_bytes: int = 0, limit_bytes: int = 65536,
    ) -> Any:
        store = self._session_store(workspace, session_id)
        if store is None:
            return None
        return store.body_slice(
            session_id, body_id, offset_bytes=offset_bytes, limit_bytes=limit_bytes,
        )

    # ---------------------------------------------------------------- lifecycle

    def flush_all(self, timeout: float = 3.0) -> bool:
        with self._lock:
            stores = [*self._stores.values(), *self._memory_stores.values()]
        ok = True
        for store in stores:
            ok = store.flush(timeout=timeout) and ok
        return ok

    def drop_session(self, session_id: str) -> None:
        with self._lock:
            store = self._memory_stores.pop(session_id, None)
            stores = list(self._stores.values())
        if store is not None:
            store.close()
        for persistent in stores:
            try:
                persistent.purge_session(session_id)
            except Exception:
                logger.exception("trajectory session purge failed for {}", session_id)

    def delete_session(self, session_id: str) -> None:
        """Delete trajectory state when the owning chat session is removed."""
        self.drop_session(session_id)

    def close_all(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            stores = [*self._stores.values(), *self._memory_stores.values()]
            self._stores.clear()
            self._memory_stores.clear()
        for store in stores:
            store.close()


_service: TrajectoryService | None = None
_service_lock = threading.Lock()


def trajectory_service() -> TrajectoryService:
    global _service
    with _service_lock:
        if _service is None:
            _service = TrajectoryService()
        return _service


def configure_trajectory(settings: TrajectorySettings) -> TrajectoryService:
    service = trajectory_service()
    service.configure(settings)
    return service


__all__ = [
    "DEFAULT_BODY_LIMIT_BYTES",
    "DEFAULT_RUN_BODY_BUDGET_BYTES",
    "TrajectoryService",
    "TrajectorySettings",
    "configure_trajectory",
    "trajectory_service",
]
