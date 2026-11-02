"""WU-02：产品链路的完成信号与结构化 observer（契约 §3「完成屏障」）。

覆盖：

- ``MemoryOperationRegistry`` 的 ``start / finish / wait / snapshot``。
- key 必须含 runtime namespace / session / op_id；pending 任务按 namespace 隔离。
- 订阅者收到**真实**的 completed / failed / skipped —— 不能从空结果推断成功。
- observer 抛异常不破坏主流程，但评测证据标 ``partial``。
- 公共 ``flush_session_memory``：复用 idle 抽取的同一套逻辑、等 pending 任务、
  不借删除会话触发、不重复抽取。
- 事件载荷不携带 gold。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from nanobot.agent.hook import AgentRunHookContext
from nanobot.agent.hooks.memory_extraction import MemoryExtractionHook
from nanobot.memory.signals import (
    IDLE_EXTRACTION_OP_ID,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_SKIPPED,
    MemoryOperationKey,
    MemoryOperationRegistry,
    flush_session_memory,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeResult:
    memory_ids: list[str] = field(default_factory=list)
    episode_ids: list[str] = field(default_factory=list)
    skipped: int = 0
    failed_tracks: list[str] = field(default_factory=list)


class _FakeExtractor:
    """鸭子类型的 extractor：记录调用，支持抛错与自定义结果。"""

    def __init__(
        self,
        *,
        result: _FakeResult | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self.result = result if result is not None else _FakeResult(memory_ids=["m1"])
        self.raise_exc = raise_exc
        self.calls: list[Any] = []

    async def run_idle_extraction(self, session: Any, *, cited_memory_ids: Any = None) -> Any:
        self.calls.append(session)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.result

    async def extract_session(self, session: Any, *, source: str = "session_end") -> Any:
        return _FakeResult()

    async def extract_incremental(self, session: Any, last_extracted_index: int) -> Any:
        return _FakeResult()


class _FakeWriter:
    async def update_focus(self, session_key: str, new_focus: str) -> None:
        return None


class _FakeSession:
    """带删除标记的会话替身：flush 不得借删除会话触发抽取。"""

    def __init__(self, key: str = "s1") -> None:
        self.key = key
        self.messages = [{"role": "user", "content": "记住我的偏好"}]
        self.metadata: dict[str, Any] = {}
        self.deleted = False

    def delete(self) -> None:
        self.deleted = True


def _ctx() -> AgentRunHookContext:
    return AgentRunHookContext(messages=[{"role": "user", "content": "记住我的偏好"}])


def _hook(
    extractor: _FakeExtractor,
    registry: MemoryOperationRegistry,
    *,
    namespace: str = "ns1",
    session_key: str = "s1",
    idle_seconds: float | None = 0.05,
) -> MemoryExtractionHook:
    return MemoryExtractionHook(
        extractor,
        session_key,
        _FakeWriter(),
        idle_seconds=idle_seconds,
        operation_registry=registry,
        namespace=namespace,
    )


@pytest.fixture(autouse=True)
def _close_shared_registry():
    """类级 singleton 跨测试必须复位，避免 namespace 泄漏。"""
    yield
    shared = MemoryOperationRegistry._shared
    if shared is not None:
        shared.close()


# ---------------------------------------------------------------------------
# Registry 基础契约
# ---------------------------------------------------------------------------


def test_key_contains_namespace_session_and_op_id():
    a = MemoryOperationKey("ns1", "s1", "op1")
    b = MemoryOperationKey("ns2", "s1", "op1")
    c = MemoryOperationKey("ns1", "s2", "op1")
    d = MemoryOperationKey("ns1", "s1", "op2")
    assert len({a.as_tuple(), b.as_tuple(), c.as_tuple(), d.as_tuple()}) == 4


def test_subscriber_receives_real_completed_event():
    events: list[dict[str, Any]] = []
    registry = MemoryOperationRegistry(event_sink=events.append)
    key = MemoryOperationKey("ns1", "s1", "op1")

    registry.start(key)
    registry.finish(key, status=STATUS_COMPLETED)

    kinds = [event["event"] for event in events]
    assert "memory_operation_started" in kinds
    assert "memory_operation_finished" in kinds
    finished = events[-1]
    assert finished["status"] == STATUS_COMPLETED
    assert finished["namespace"] == "ns1"
    assert finished["session_key"] == "s1"
    assert finished["op_id"] == "op1"


def test_subscriber_receives_failed_and_skipped():
    for status in (STATUS_FAILED, STATUS_SKIPPED, STATUS_CANCELLED, STATUS_PARTIAL):
        events: list[dict[str, Any]] = []
        registry = MemoryOperationRegistry(event_sink=events.append)
        key = MemoryOperationKey("ns", "s", f"op-{status}")
        registry.start(key)
        registry.finish(key, status=status)
        assert events[-1]["status"] == status


@pytest.mark.asyncio
async def test_wait_returns_terminal_state():
    registry = MemoryOperationRegistry()
    key = MemoryOperationKey("ns", "s", "op")
    registry.start(key)

    async def _finish() -> None:
        await asyncio.sleep(0.01)
        registry.finish(key, status=STATUS_COMPLETED)

    asyncio.create_task(_finish())
    state = await registry.wait(key, timeout=2.0)
    assert state.status == STATUS_COMPLETED


@pytest.mark.asyncio
async def test_wait_times_out_without_finishing():
    registry = MemoryOperationRegistry()
    key = MemoryOperationKey("ns", "s", "op")
    registry.start(key)
    with pytest.raises(asyncio.TimeoutError):
        await registry.wait(key, timeout=0.05)


def test_cancel_records_cancelled_terminal_state():
    registry = MemoryOperationRegistry()
    key = MemoryOperationKey("ns", "s", "op")
    registry.start(key)
    state = registry.cancel(key, reason="shutdown")
    assert state.status == STATUS_CANCELLED
    assert state.detail["reason"] == "shutdown"
    assert registry.snapshot()["operations"][0]["status"] == STATUS_CANCELLED


def test_snapshot_reports_tracks_and_index_ready():
    registry = MemoryOperationRegistry()
    key = MemoryOperationKey("ns", "s", "op")
    registry.start(key, tracks=("episode", "profile"))
    registry.track(key, "episode", STATUS_COMPLETED)
    registry.track(key, "profile", STATUS_FAILED)
    registry.mark_index_ready(key)

    snapshot = registry.snapshot()
    assert snapshot["evidence_status"] == "complete"
    operation = snapshot["operations"][0]
    assert operation["tracks"] == {"episode": STATUS_COMPLETED, "profile": STATUS_FAILED}
    assert operation["index_ready"] is True


def test_observer_exception_does_not_break_flow_but_marks_partial():
    def _sink(_payload: dict[str, Any]) -> None:
        raise RuntimeError("observer down")

    registry = MemoryOperationRegistry(event_sink=_sink)
    key = MemoryOperationKey("ns", "s", "op")

    registry.start(key)  # 不得抛
    registry.finish(key, status=STATUS_COMPLETED)  # 不得抛

    snapshot = registry.snapshot()
    assert snapshot["observer_failures"] >= 1
    assert snapshot["evidence_status"] == "partial"
    assert registry.operation(key).partial is True


def test_events_carry_no_gold():
    registry = MemoryOperationRegistry()
    key = MemoryOperationKey("ns", "s", "op")
    registry.start(key)
    registry.finish(
        key,
        status=STATUS_COMPLETED,
        detail={"gold": {"required_facts": ["x"]}, "memory_ids": ["m1"]},
    )
    for event in registry.snapshot()["events"]:
        assert "gold" not in event
        assert "required_facts" not in event
    assert registry.operation(key).detail.get("memory_ids") == ["m1"]


# ---------------------------------------------------------------------------
# namespace 隔离 / singleton / close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_tasks_isolated_by_namespace():
    registry = MemoryOperationRegistry()
    extractor = _FakeExtractor()
    hook_a = _hook(extractor, registry, namespace="nsA", idle_seconds=30.0)
    hook_b = _hook(extractor, registry, namespace="nsB", idle_seconds=30.0)

    await hook_a.after_run(_ctx())
    await hook_b.after_run(_ctx())

    task_a = registry.pending_task("nsA", "s1")
    task_b = registry.pending_task("nsB", "s1")
    assert task_a is not None
    assert task_b is not None
    assert task_a is not task_b

    for task in (task_a, task_b):
        task.cancel()
        try:
            await task
        except BaseException:
            pass


def test_close_leaves_no_residue():
    registry = MemoryOperationRegistry()
    key = MemoryOperationKey("ns", "s", "op")
    registry.start(key)
    registry.finish(key, status=STATUS_COMPLETED)

    registry.close()

    snapshot = registry.snapshot()
    assert snapshot["operations"] == []
    assert snapshot["events"] == []
    assert snapshot["pending"] == []
    assert registry.pending_task("ns", "s") is None


def test_class_level_singleton_is_released_after_close():
    first = MemoryOperationRegistry.shared()
    assert MemoryOperationRegistry.shared() is first
    first.close()
    assert MemoryOperationRegistry.shared() is not first


# ---------------------------------------------------------------------------
# hook 接线：idle 抽取与后台任务进入 registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hook_registers_pending_task_into_registry():
    registry = MemoryOperationRegistry()
    hook = _hook(_FakeExtractor(), registry)
    await hook.after_run(_ctx())
    task = registry.pending_task("ns1", "s1")
    assert task is not None
    assert await registry.wait_pending("ns1", "s1", timeout=2.0) == "completed"


@pytest.mark.asyncio
async def test_hook_reports_idle_extraction_completion():
    registry = MemoryOperationRegistry()
    extractor = _FakeExtractor(result=_FakeResult(memory_ids=["m1"], episode_ids=["e1"]))
    hook = _hook(extractor, registry)
    await hook.after_run(_ctx())
    await registry.wait_pending("ns1", "s1", timeout=2.0)

    state = registry.operation(MemoryOperationKey("ns1", "s1", IDLE_EXTRACTION_OP_ID))
    assert state is not None
    assert state.status == STATUS_COMPLETED


@pytest.mark.asyncio
async def test_hook_reports_idle_extraction_failure_not_success():
    registry = MemoryOperationRegistry()
    extractor = _FakeExtractor(raise_exc=RuntimeError("idle boom"))
    hook = _hook(extractor, registry)
    await hook.after_run(_ctx())
    await registry.wait_pending("ns1", "s1", timeout=2.0)

    state = registry.operation(MemoryOperationKey("ns1", "s1", IDLE_EXTRACTION_OP_ID))
    assert state is not None
    assert state.status == STATUS_FAILED
    assert state.status != STATUS_COMPLETED


@pytest.mark.asyncio
async def test_hook_reports_partial_when_some_tracks_failed():
    registry = MemoryOperationRegistry()
    extractor = _FakeExtractor(
        result=_FakeResult(memory_ids=["m1"], failed_tracks=["semantic"])
    )
    hook = _hook(extractor, registry)
    await hook.after_run(_ctx())
    await registry.wait_pending("ns1", "s1", timeout=2.0)

    state = registry.operation(MemoryOperationKey("ns1", "s1", IDLE_EXTRACTION_OP_ID))
    assert state is not None
    assert state.status == STATUS_PARTIAL
    assert state.detail.get("failed_tracks") == ["semantic"]


# ---------------------------------------------------------------------------
# flush_session_memory：公共协调入口
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flush_waits_for_pending_task_and_reuses_idle_logic():
    registry = MemoryOperationRegistry()
    extractor = _FakeExtractor()
    hook = _hook(extractor, registry)
    await hook.after_run(_ctx())

    outcome = await flush_session_memory(
        "s1", registry=registry, namespace="ns1", timeout_s=2.0
    )

    assert outcome.status == STATUS_COMPLETED
    assert len(extractor.calls) == 1


@pytest.mark.asyncio
async def test_flush_triggers_real_extraction_when_no_pending_task():
    registry = MemoryOperationRegistry()
    extractor = _FakeExtractor()
    session = _FakeSession()

    outcome = await flush_session_memory(
        "s1",
        extractor=extractor,
        session=session,
        registry=registry,
        namespace="ns1",
    )

    assert outcome.status == STATUS_COMPLETED
    assert len(extractor.calls) == 1


@pytest.mark.asyncio
async def test_flush_does_not_extract_twice():
    registry = MemoryOperationRegistry()
    extractor = _FakeExtractor()
    hook = _hook(extractor, registry)
    await hook.after_run(_ctx())
    await registry.wait_pending("ns1", "s1", timeout=2.0)
    assert len(extractor.calls) == 1

    outcome = await flush_session_memory(
        "s1", registry=registry, namespace="ns1", timeout_s=1.0
    )

    assert outcome.status == STATUS_COMPLETED
    assert len(extractor.calls) == 1


@pytest.mark.asyncio
async def test_flush_never_deletes_session():
    registry = MemoryOperationRegistry()
    extractor = _FakeExtractor()
    session = _FakeSession()

    await flush_session_memory(
        "s1",
        extractor=extractor,
        session=session,
        registry=registry,
        namespace="ns1",
    )

    assert session.deleted is False


@pytest.mark.asyncio
async def test_flush_reports_skipped_instead_of_success_for_empty_result():
    registry = MemoryOperationRegistry()
    extractor = _FakeExtractor(result=_FakeResult())

    outcome = await flush_session_memory(
        "s1",
        extractor=extractor,
        session=_FakeSession(),
        registry=registry,
        namespace="ns1",
    )

    assert outcome.status == STATUS_SKIPPED
    assert outcome.status != STATUS_COMPLETED


@pytest.mark.asyncio
async def test_flush_reports_failed_on_extraction_error():
    registry = MemoryOperationRegistry()
    extractor = _FakeExtractor(raise_exc=RuntimeError("boom"))

    outcome = await flush_session_memory(
        "s1",
        extractor=extractor,
        session=_FakeSession(),
        registry=registry,
        namespace="ns1",
    )

    assert outcome.status == STATUS_FAILED
    assert outcome.error


@pytest.mark.asyncio
async def test_flush_records_operation_in_registry():
    registry = MemoryOperationRegistry()
    extractor = _FakeExtractor()

    outcome = await flush_session_memory(
        "s1",
        extractor=extractor,
        session=_FakeSession(),
        registry=registry,
        namespace="ns1",
        op_id="flush-1",
    )

    state = registry.operation(MemoryOperationKey("ns1", "s1", "flush-1"))
    assert state is not None
    assert state.status == outcome.status


@pytest.mark.asyncio
async def test_flush_observer_failure_marks_partial_not_success():
    def _sink(_payload: dict[str, Any]) -> None:
        raise RuntimeError("observer down")

    registry = MemoryOperationRegistry(event_sink=_sink)
    extractor = _FakeExtractor()

    outcome = await flush_session_memory(
        "s1",
        extractor=extractor,
        session=_FakeSession(),
        registry=registry,
        namespace="ns1",
    )

    assert outcome.status == STATUS_COMPLETED
    assert registry.snapshot()["evidence_status"] == "partial"
