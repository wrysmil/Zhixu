"""产品记忆处理的完成信号与结构化 observer（契约 §3「完成屏障」）。

为什么需要
----------
产品链路的记忆处理是**后台异步**的：idle 定时器、增量抽取、索引写入都会在
agent 主循环之外完成。评测若只能「等几秒再查」，就分不清「真的处理完了」与
「碰巧还没写完」——空结果既可能是「处理完成但没抽到东西」，也可能是「根本没
处理」。本模块提供可等待的完成屏障：

- :class:`MemoryOperationRegistry`：按 ``(namespace, session, op_id)`` 登记操作
  生命周期，提供 ``start / finish / wait / snapshot`` 与可选 event sink。
  支持多轨完成、错误、取消与 ``index_ready``。
- :func:`flush_session_memory`：公共协调入口 —— 等待 pending 的 idle 任务，
  或触发**真实**的会话结束抽取；复用 idle 抽取的同一套逻辑。

硬约束
------
1. 订阅者收到的是**真实**的 completed / failed / skipped —— 绝不能从空结果
   推断成功（空结果一律记 ``skipped``）。
2. observer（event sink）抛异常**不能**破坏主流程，但要让评测证据标 ``partial``。
3. 事件载荷**不携带 gold** —— 评测私有数据不得流向产品链路。
4. ``flush_session_memory`` **不借删除会话触发抽取**（删除会改变被测状态），
   也**不重复抽取**。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from loguru import logger

from nanobot.memory.clock import MemoryClock

__all__ = [
    "IDLE_EXTRACTION_OP_ID",
    "STATUS_CANCELLED",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_PARTIAL",
    "STATUS_PENDING",
    "STATUS_SKIPPED",
    "TERMINAL_STATUSES",
    "MemoryFlushOutcome",
    "MemoryOperationKey",
    "MemoryOperationRegistry",
    "MemoryOperationState",
    "classify_extraction_result",
    "flush_session_memory",
]

#: 操作状态。
STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_CANCELLED = "cancelled"
#: 部分轨道失败（其余成功）。评测证据里必须与 completed 区分。
STATUS_PARTIAL = "partial"

TERMINAL_STATUSES = frozenset(
    {STATUS_COMPLETED, STATUS_FAILED, STATUS_SKIPPED, STATUS_CANCELLED, STATUS_PARTIAL}
)

#: idle 增量抽取在 registry 中的固定 op_id（hook 与 flush 共用）。
IDLE_EXTRACTION_OP_ID = "idle_extraction"

#: 事件载荷里禁止出现的键（评测私有 gold 不得流入产品链路）。
_FORBIDDEN_EVENT_KEYS = frozenset(
    {
        "gold",
        "required_facts",
        "forbidden_current_values",
        "answerability",
        "expected_absence",
        "allowed_evidence_sets",
        "state_expectations",
    }
)

#: 保留的事件条数上限（防长跑进程内存无限增长）。超出时丢弃最旧的，
#: 并在快照里报 ``events_dropped`` —— 丢了多少必须可见，不能静默。
_MAX_RETAINED_EVENTS = 2000


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryOperationKey:
    """操作键：runtime namespace + session + op_id，三者缺一不可。"""

    namespace: str
    session_key: str
    op_id: str

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.namespace, self.session_key, self.op_id)

    def __str__(self) -> str:
        return f"{self.namespace}/{self.session_key}/{self.op_id}"


@dataclass
class MemoryOperationState:
    """单个记忆处理操作的状态快照。"""

    key: MemoryOperationKey
    status: str = STATUS_PENDING
    tracks: dict[str, str] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    #: observer 失败时置位 —— 证据不完整，不能当「干净通过」。
    partial: bool = False
    index_ready: bool = False
    started_at: float = 0.0
    finished_at: float | None = None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.key.namespace,
            "session_key": self.key.session_key,
            "op_id": self.key.op_id,
            "status": self.status,
            "tracks": dict(self.tracks),
            "detail": dict(self.detail),
            "error": self.error,
            "partial": self.partial,
            "index_ready": self.index_ready,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class MemoryFlushOutcome:
    """``flush_session_memory`` 的结果。"""

    status: str
    tracks: dict[str, str] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    partial: bool = False

    @property
    def completed(self) -> bool:
        return self.status == STATUS_COMPLETED


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _strip_gold(value: Any) -> tuple[Any, list[str]]:
    """递归剔除 gold 类键；返回 ``(净化后的值, 被剔除的键名列表)``。"""
    if isinstance(value, Mapping):
        mapping = cast("Mapping[str, Any]", value)
        clean: dict[str, Any] = {}
        dropped: list[str] = []
        for key, item in mapping.items():
            if key in _FORBIDDEN_EVENT_KEYS:
                dropped.append(key)
                continue
            sub, sub_dropped = _strip_gold(item)
            dropped.extend(sub_dropped)
            clean[key] = sub
        return clean, dropped
    if isinstance(value, (list, tuple)):
        items: list[Any] = []
        dropped_items: list[str] = []
        for item in cast("list[Any]", value):
            sub, sub_dropped = _strip_gold(item)
            dropped_items.extend(sub_dropped)
            items.append(sub)
        return items, dropped_items
    return value, []


def classify_extraction_result(
    result: Any,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """把抽取结果分类为 ``(status, tracks, detail)``。

    - ``failed_tracks`` 非空 → ``partial``（部分轨道失败，不是干净成功）。
    - 空结果（既无记忆也无情节）→ ``skipped``：**不能从空结果推断成功**。
    - 其余 → ``completed``。
    """
    memory_ids = list(getattr(result, "memory_ids", None) or [])
    episode_ids = list(getattr(result, "episode_ids", None) or [])
    failed_tracks = list(getattr(result, "failed_tracks", None) or [])
    detail: dict[str, Any] = {
        "memory_count": len(memory_ids),
        "episode_count": len(episode_ids),
        "skipped": int(getattr(result, "skipped", 0) or 0),
    }
    if failed_tracks:
        detail["failed_tracks"] = failed_tracks
        return STATUS_PARTIAL, {"extraction": STATUS_PARTIAL}, detail
    if not memory_ids and not episode_ids:
        detail["reason"] = "no_result"
        return STATUS_SKIPPED, {"extraction": STATUS_SKIPPED}, detail
    return STATUS_COMPLETED, {"extraction": STATUS_COMPLETED}, detail


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class MemoryOperationRegistry:
    """记忆处理操作的生命周期登记簿（可等待）。

    - 类级 singleton：:meth:`shared`。不同 runtime namespace 之间的 pending
      任务互不可见。
    - ``finish`` 采用「**首个终态生效**」：一旦某操作进入终态，后续的重复上报
      不会覆盖它（避免迟到的空结果把已完成改写成 skipped）。
    - observer 抛异常不影响主流程，但会把证据标成 ``partial``。
    """

    _shared: "MemoryOperationRegistry | None" = None

    def __init__(
        self,
        *,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        clock: MemoryClock | None = None,
    ) -> None:
        self._clock = clock if clock is not None else MemoryClock()
        self._sinks: list[Callable[[dict[str, Any]], None]] = []
        if event_sink is not None:
            self._sinks.append(event_sink)
        self._operations: dict[MemoryOperationKey, MemoryOperationState] = {}
        self._events: list[dict[str, Any]] = []
        self._event_seq = 0
        self._pending: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._waiters: dict[MemoryOperationKey, list[asyncio.Future[None]]] = {}
        self._observer_failures = 0
        self._events_dropped = 0
        self._closed = False

    # ------------------------------------------------------------------
    # singleton
    # ------------------------------------------------------------------

    @classmethod
    def shared(cls) -> "MemoryOperationRegistry":
        """返回类级 singleton；已 close 时重新创建。"""
        if cls._shared is None or cls._shared._closed:
            cls._shared = cls()
        return cls._shared

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(
        self,
        key: MemoryOperationKey,
        *,
        tracks: Iterable[str] = (),
        detail: Mapping[str, Any] | None = None,
    ) -> MemoryOperationState:
        """登记一个操作并置为 pending；重复 start 不重置已有状态。"""
        if self._closed:
            raise RuntimeError("MemoryOperationRegistry is closed")
        existing = self._operations.get(key)
        if existing is not None:
            return existing
        state = MemoryOperationState(
            key=key,
            tracks={name: STATUS_PENDING for name in tracks},
            started_at=self._clock.monotonic(),
        )
        if detail:
            state.detail.update(self._sanitize(detail)[0])
        self._operations[key] = state
        self._emit("memory_operation_started", state)
        return state

    def finish(
        self,
        key: MemoryOperationKey,
        *,
        status: str,
        tracks: Mapping[str, str] | None = None,
        detail: Mapping[str, Any] | None = None,
        error: str | None = None,
        index_ready: bool = False,
    ) -> MemoryOperationState:
        """把操作置为终态。**首个终态生效**，后续重复上报不覆盖。"""
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"invalid terminal status: {status!r}")
        state = self._ensure(key)
        if state.terminal:
            return state
        state.status = status
        if tracks:
            state.tracks.update(tracks)
        if detail:
            state.detail.update(self._sanitize(detail)[0])
        if error is not None:
            state.error = error
        if index_ready:
            state.index_ready = True
        state.finished_at = self._clock.monotonic()
        self._emit("memory_operation_finished", state)
        self._resolve(key)
        return state

    def cancel(self, key: MemoryOperationKey, *, reason: str | None = None) -> MemoryOperationState:
        """把操作置为 cancelled。"""
        return self.finish(
            key,
            status=STATUS_CANCELLED,
            detail={"reason": reason} if reason else None,
        )

    def track(
        self,
        key: MemoryOperationKey,
        name: str,
        status: str,
        *,
        error: str | None = None,
    ) -> MemoryOperationState:
        """记录单条轨道的完成状态（多轨完成 / 错误）。"""
        state = self._ensure(key)
        state.tracks[name] = status
        if error:
            errors = state.detail.setdefault("track_errors", {})
            errors[name] = error
        self._emit("memory_track_finished", state, track=name, track_status=status)
        return state

    def mark_index_ready(self, key: MemoryOperationKey) -> MemoryOperationState:
        """标记索引已可见（``index_ready`` 阶段事件）。"""
        state = self._ensure(key)
        state.index_ready = True
        self._emit("memory_index_ready", state)
        return state

    async def wait(
        self,
        key: MemoryOperationKey,
        *,
        timeout: float | None = None,
    ) -> MemoryOperationState:
        """等待操作进入终态。

        超时是**真实执行超时**（``asyncio.wait_for`` 用事件循环时钟），虚拟时钟
        跳跃不会影响它。超时抛 :class:`asyncio.TimeoutError`。
        """
        state = self._ensure(key)
        if state.terminal:
            return state
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[None] = loop.create_future()
        self._waiters.setdefault(key, []).append(waiter)
        try:
            if timeout is None:
                await waiter
            else:
                await asyncio.wait_for(waiter, timeout=timeout)
        finally:
            pending = self._waiters.get(key)
            if pending is not None and waiter in pending:
                pending.remove(waiter)
        return self._operations[key]

    def operation(self, key: MemoryOperationKey) -> MemoryOperationState | None:
        """返回操作状态；不存在时返回 ``None``。"""
        return self._operations.get(key)

    def snapshot(self) -> dict[str, Any]:
        """返回可序列化的证据快照。"""
        return {
            "operations": [state.to_dict() for state in self._operations.values()],
            "events": list(self._events),
            "events_dropped": self._events_dropped,
            "pending": sorted(f"{ns}/{session}" for ns, session in self._pending),
            "observer_failures": self._observer_failures,
            "evidence_status": "partial" if self._observer_failures else "complete",
            "closed": self._closed,
        }

    # ------------------------------------------------------------------
    # pending 任务（按 namespace 隔离）
    # ------------------------------------------------------------------

    def register_pending(
        self,
        namespace: str,
        session_key: str,
        task: asyncio.Task[Any],
    ) -> None:
        """登记某 namespace 下某会话的后台任务，使其可被等待。"""
        key = (namespace, session_key)
        self._pending[key] = task

        def _drop(finished: asyncio.Task[Any]) -> None:
            if self._pending.get(key) is finished:
                self._pending.pop(key, None)

        task.add_done_callback(_drop)

    def pending_task(
        self, namespace: str, session_key: str
    ) -> asyncio.Task[Any] | None:
        """返回该 namespace / session 当前的 pending 任务。"""
        return self._pending.get((namespace, session_key))

    def cancel_pending(self, namespace: str, session_key: str) -> asyncio.Task[Any] | None:
        """取消并弹出 pending 任务（避免稍后重复抽取）。"""
        task = self._pending.pop((namespace, session_key), None)
        if task is not None and not task.done():
            task.cancel()
        return task

    async def wait_pending(
        self,
        namespace: str,
        session_key: str,
        *,
        timeout: float | None = None,
    ) -> str:
        """等待 pending 任务结束。

        Returns:
            ``"completed"``（跑完）/ ``"cancelled"``（被取消）/ ``"timeout"``
            （真实超时）/ ``"absent"``（没有 pending 任务）。
        """
        task = self.pending_task(namespace, session_key)
        if task is None:
            return "absent"
        try:
            if timeout is None:
                await asyncio.shield(task)
            else:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            return "timeout"
        except asyncio.CancelledError:
            if task.cancelled():
                return "cancelled"
            raise
        except Exception:
            # pending 任务自身抛错：它已经结束（失败），由调用方查状态。
            return "completed"
        return "completed"

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------

    def close(self) -> None:
        """清空全部状态并释放类级 singleton（close 后无残留）。"""
        for task in list(self._pending.values()):
            if not task.done():
                task.cancel()
        self._pending.clear()
        self._operations.clear()
        self._events.clear()
        self._event_seq = 0
        self._waiters.clear()
        self._observer_failures = 0
        self._events_dropped = 0
        self._closed = True
        if MemoryOperationRegistry._shared is self:
            MemoryOperationRegistry._shared = None

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _ensure(self, key: MemoryOperationKey) -> MemoryOperationState:
        state = self._operations.get(key)
        if state is not None:
            return state
        if self._closed:
            raise RuntimeError("MemoryOperationRegistry is closed")
        return self.start(key)

    def _resolve(self, key: MemoryOperationKey) -> None:
        waiters = self._waiters.pop(key, None)
        if not waiters:
            return
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)

    def _sanitize(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
        clean, dropped = _strip_gold(dict(payload))
        if dropped:
            logger.warning(
                "memory operation event dropped gold-like fields (keys={})",
                sorted(set(dropped)),
            )
        return clean, dropped

    def _emit(self, event: str, state: MemoryOperationState, **extra: Any) -> None:
        payload: dict[str, Any] = {
            "event": event,
            "namespace": state.key.namespace,
            "session_key": state.key.session_key,
            "op_id": state.key.op_id,
            "status": state.status,
            "seq": self._event_seq,
        }
        self._event_seq += 1
        payload.update(extra)
        clean, dropped = self._sanitize(payload)
        if dropped:
            state.partial = True
        self._events.append(clean)
        while len(self._events) > _MAX_RETAINED_EVENTS:
            self._events.pop(0)
            self._events_dropped += 1
        for sink in list(self._sinks):
            try:
                sink(clean)
            except Exception as exc:  # noqa: BLE001 - observer 失败不得破坏主流程
                self._observer_failures += 1
                state.partial = True
                logger.warning(
                    "memory operation observer failed for {}/{}: {}",
                    state.key.namespace,
                    state.key.session_key,
                    exc,
                )


# ---------------------------------------------------------------------------
# 公共协调入口
# ---------------------------------------------------------------------------


def _outcome_from_state(state: MemoryOperationState) -> MemoryFlushOutcome:
    return MemoryFlushOutcome(
        status=state.status,
        tracks=dict(state.tracks),
        detail=dict(state.detail),
        error=state.error,
        partial=state.partial,
    )


async def flush_session_memory(
    session_key: str,
    *,
    extractor: Any | None = None,
    session: Any | None = None,
    registry: MemoryOperationRegistry | None = None,
    namespace: str = "default",
    op_id: str | None = None,
    timeout_s: float | None = None,
    cited_memory_ids: list[str] | None = None,
    run_extraction: Callable[[], Awaitable[Any]] | None = None,
    wait_pending: bool = True,
) -> MemoryFlushOutcome:
    """公共协调入口：等真实的记忆处理完成。

    语义（契约 §3）：

    1. 若该会话有 pending 的 idle 抽取任务 → **等它跑完**（真实超时口径）；
       跑完后从 registry 读出**真实**的 completed / failed / skipped。
    2. 否则触发**真实**的会话结束抽取 —— 复用 idle 抽取的**同一套逻辑**
       （``MemoryExtractor.run_idle_extraction``），不另写一份抽取。
    3. **不借删除会话触发抽取**（删除会改变被测状态）。
    4. **不重复抽取**：pending 任务已经跑过抽取时不再触发第二次。

    Args:
        session_key: 目标会话。
        extractor: 提供 ``run_idle_extraction`` 的提取器（与 idle 路径同一个）。
        session: 传给提取器的会话对象。
        registry: 完成信号登记簿；缺省用类级 singleton。
        namespace: runtime namespace（pending 任务按它隔离）。
        op_id: 本次 flush 的 op_id；缺省 ``"flush"``。
        timeout_s: 真实超时（秒）。
        cited_memory_ids: 传给 idle 抽取的引用记忆 id（与 hook 同口径）。
        run_extraction: 自定义抽取协程（缺省用 idle 抽取逻辑）。
        wait_pending: 是否先等 pending 任务。``False`` 时直接触发抽取，并取消
            仍待机的 idle 定时器（避免稍后重复抽取）。

    Returns:
        :class:`MemoryFlushOutcome`。空结果记 ``skipped``，**不记 completed**。
    """
    reg = registry if registry is not None else MemoryOperationRegistry.shared()
    key = MemoryOperationKey(
        namespace=namespace, session_key=session_key, op_id=op_id or "flush"
    )
    reg.start(key, tracks=("extraction",))
    idle_key = MemoryOperationKey(
        namespace=namespace, session_key=session_key, op_id=IDLE_EXTRACTION_OP_ID
    )
    idle_state = reg.operation(idle_key)

    if wait_pending:
        if reg.pending_task(namespace, session_key) is not None:
            pending_state = await reg.wait_pending(
                namespace, session_key, timeout=timeout_s
            )
            if pending_state == "timeout":
                reg.finish(
                    key,
                    status=STATUS_FAILED,
                    tracks={"extraction": STATUS_PENDING},
                    error="flush_timeout",
                    detail={"reason": "pending_extraction_timeout"},
                )
                return MemoryFlushOutcome(
                    status=STATUS_FAILED,
                    tracks={"extraction": STATUS_PENDING},
                    detail={"reason": "pending_extraction_timeout"},
                    error="flush_timeout",
                )
            if pending_state == "completed":
                idle_state = reg.operation(idle_key)
                if idle_state is not None and idle_state.terminal:
                    outcome = _outcome_from_state(idle_state)
                    reg.finish(
                        key,
                        status=outcome.status,
                        tracks=outcome.tracks,
                        detail=outcome.detail,
                        error=outcome.error,
                    )
                    return outcome
        elif idle_state is not None and idle_state.terminal:
            # 没有 pending 任务 → 最近一次抽取的结果就是当前状态（不重复抽取）。
            outcome = _outcome_from_state(idle_state)
            reg.finish(
                key,
                status=outcome.status,
                tracks=outcome.tracks,
                detail=outcome.detail,
                error=outcome.error,
            )
            return outcome
    else:
        # 不等 pending：取消仍待机的 idle 定时器，避免稍后重复抽取。
        reg.cancel_pending(namespace, session_key)

    runner: Callable[[], Awaitable[Any]]
    if run_extraction is not None:
        runner = run_extraction
    elif extractor is not None and session is not None:

        async def _idle_runner() -> Any:
            """与 idle 抽取**同一套**逻辑（不另写一份抽取）。"""
            return await extractor.run_idle_extraction(
                session, cited_memory_ids=cited_memory_ids
            )

        runner = _idle_runner
    else:
        reg.finish(
            key,
            status=STATUS_SKIPPED,
            tracks={"extraction": STATUS_SKIPPED},
            detail={"reason": "no_extractor"},
        )
        return MemoryFlushOutcome(
            status=STATUS_SKIPPED,
            tracks={"extraction": STATUS_SKIPPED},
            detail={"reason": "no_extractor"},
        )

    try:
        result = await runner()
    except asyncio.CancelledError:
        reg.finish(key, status=STATUS_CANCELLED, error="flush_cancelled")
        raise
    except Exception as exc:  # noqa: BLE001 - 失败必须显式可见
        reg.finish(key, status=STATUS_FAILED, error=str(exc))
        return MemoryFlushOutcome(status=STATUS_FAILED, error=str(exc))

    status, tracks, detail = classify_extraction_result(result)
    reg.finish(key, status=status, tracks=tracks, detail=detail)
    # 同步登记到 idle 操作位：任何读者都能看到「该会话最近一次抽取」的真实结果。
    reg.finish(idle_key, status=status, tracks=tracks, detail=detail)
    return MemoryFlushOutcome(status=status, tracks=tracks, detail=detail)
