"""Tests for MemoryExtractionHook（plan §10 Task 7 / Task 12 / WU-A）。

覆盖四个生命周期回调：
- T0  ``after_run``      意图门 + scratchpad.current_focus 写入
- T0' ``on_error``       仅紧急保存 focus（不调 LLM / extractor）
- T1  ``after_run``/``on_finally``  WU-A idle 定时器注册 + 取消

（原 T5 ``before_iteration`` 话题切换检测已随功能一并移除，相关用例已删除。）

extractor / scratchpad_writer / runtime 全部用鸭子类型 fake，不依赖真实 provider。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from nanobot.agent.hook import AgentRunHookContext, AgentTurnHookContext
from nanobot.agent.hooks.memory_extraction import (
    _PENDING_IDLE_TIMERS,
    MemoryExtractionHook,
    create_memory_extraction_hook_factory,
)

# ---------------------------------------------------------------------------
# Fakes（鸭子类型）
# ---------------------------------------------------------------------------


class _FakeExtractionResult:
    memory_ids: list[str] = []
    episode_ids: list[str] = []
    skipped = 0
    failed_tracks: list[str] = []


class _FakeExtractor:
    """记录调用；``delay`` 模拟慢提取，``raise_exc`` 模拟失败。"""

    def __init__(self, *, delay: float = 0.0, raise_exc: BaseException | None = None) -> None:
        self.calls: list[tuple[Any, str]] = []
        self.incremental_calls: list[tuple[Any, int]] = []
        self.idle_calls: list[Any] = []
        self.delay = delay
        self.raise_exc = raise_exc

    async def extract_session(self, session: Any, *, source: str = "session_end") -> Any:
        self.calls.append((session, source))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc
        return _FakeExtractionResult()

    async def extract_incremental(self, session: Any, last_extracted_index: int) -> Any:
        self.incremental_calls.append((session, last_extracted_index))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc
        return _FakeExtractionResult()

    async def run_idle_extraction(self, session: Any) -> Any:
        """WU-A: 新增的 idle 入口,与 extract_session 共享延迟/异常语义。"""
        self.idle_calls.append(session)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc
        return _FakeExtractionResult()


class _FakeScratchpadWriter:
    def __init__(self) -> None:
        self.focus_calls: list[str] = []
        self.archive_calls: list[list[str]] = []

    async def update_focus(self, new_focus: str) -> None:
        self.focus_calls.append(new_focus)

    async def archive_completed(self, resolved_items: list[str]) -> None:
        self.archive_calls.append(list(resolved_items))


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 512
    reasoning_effort = None


class _FakeProvider:
    def __init__(
        self,
        result: str | None = None,
        *,
        exc: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self.result = result
        self.exc = exc
        self.delay = delay
        self.calls: list[list[dict[str, Any]]] = []

    async def chat_with_retry(
        self, *, messages: list[dict[str, Any]], **_kwargs: Any
    ) -> _FakeResponse:
        self.calls.append(messages)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return _FakeResponse(self.result)


class _FakeRuntime:
    def __init__(self, provider: _FakeProvider, model: str = "fake-model") -> None:
        self.provider = provider
        self.model = model
        self.generation = _FakeGeneration()


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _cleanup_background_tasks():
    """每个用例结束后回收模块级 idle 定时器，避免跨用例泄漏。"""
    yield
    # WU-A: 清理 idle 定时器。
    for task in list(_PENDING_IDLE_TIMERS.values()):
        if not task.done():
            task.cancel()
    await asyncio.sleep(0)
    _PENDING_IDLE_TIMERS.clear()


def _run_ctx(*user_messages: str) -> AgentRunHookContext:
    return AgentRunHookContext(
        messages=[{"role": "user", "content": m} for m in user_messages]
    )


def _make_hook(
    *,
    extractor: _FakeExtractor | None = None,
    writer: Any | None = None,
    runtime: _FakeRuntime | None = None,
    session_key: str = "s1",
    idle_seconds: float | None = None,
) -> MemoryExtractionHook:
    return MemoryExtractionHook(
        extractor or _FakeExtractor(),
        session_key,
        writer or _FakeScratchpadWriter(),
        runtime=runtime,
        idle_seconds=idle_seconds,
    )


# ---------------------------------------------------------------------------
# T0：after_run 意图门
# ---------------------------------------------------------------------------


class TestAfterRunIntentGate:
    async def test_chat_intent_skips_focus_write(self):
        writer = _FakeScratchpadWriter()
        hook = _make_hook(writer=writer)
        await hook.after_run(_run_ctx("你好"))
        assert writer.focus_calls == []
        await hook.on_finally(_run_ctx())

    async def test_task_intent_writes_focus(self):
        writer = _FakeScratchpadWriter()
        hook = _make_hook(writer=writer)
        await hook.after_run(_run_ctx("帮我实现一个爬虫"))
        assert writer.focus_calls == ["帮我实现一个爬虫"]
        await hook.on_finally(_run_ctx())

    async def test_focus_truncated_to_200_chars(self):
        writer = _FakeScratchpadWriter()
        hook = _make_hook(writer=writer)
        message = "帮我实现" + "x" * 500
        await hook.after_run(_run_ctx(message))
        assert writer.focus_calls == [message[:200]]
        assert len(writer.focus_calls[0]) == 200
        await hook.on_finally(_run_ctx())

    async def test_missing_user_message_is_noop(self):
        writer = _FakeScratchpadWriter()
        hook = _make_hook(writer=writer)
        await hook.after_run(AgentRunHookContext(messages=[{"role": "assistant", "content": "hi"}]))
        assert writer.focus_calls == []
        await hook.on_finally(_run_ctx())

    async def test_scratchpad_failure_does_not_raise(self):
        class _BoomWriter(_FakeScratchpadWriter):
            async def update_focus(self, new_focus: str) -> None:
                raise RuntimeError("db down")

        extractor = _FakeExtractor()
        hook = MemoryExtractionHook(extractor, "s1", _BoomWriter())
        await hook.after_run(_run_ctx("帮我实现一个爬虫"))  # 不抛
        await hook.on_finally(_run_ctx())
        # WU-A: T0 失败隔离 — idle 仍未触发(因 on_finally 立即取消)。
        # 旧测试断言 extractor.calls == 1(旧 T1 路径),该语义已被 idle 定时器替代。
        assert len(extractor.idle_calls) == 0
        assert len(extractor.calls) == 0


# ---------------------------------------------------------------------------
# WU-A T1：idle 定时器替代原同步 + on_finally await 模式
# ---------------------------------------------------------------------------


class TestRunExtraction:
    async def test_after_run_arms_idle_timer(self):
        """after_run 注册 idle 任务,不立即调 extractor(等阈值)。"""
        extractor = _FakeExtractor()
        hook = _make_hook(extractor=extractor, idle_seconds=5.0)
        await hook.after_run(_run_ctx("帮我实现一个爬虫"))
        try:
            # 任务已登记,且 extract_session / run_idle_extraction 都未调。
            assert "s1" in _PENDING_IDLE_TIMERS
            assert not _PENDING_IDLE_TIMERS["s1"].done()
            assert extractor.calls == []
            assert extractor.idle_calls == []
        finally:
            await hook.on_finally(_run_ctx())

    async def test_idle_threshold_triggers_idle_extraction(self):
        """IDLE_THRESHOLD_SECONDS 触发后调 run_idle_extraction,而非 extract_session。"""
        extractor = _FakeExtractor()
        hook = _make_hook(extractor=extractor, idle_seconds=0.05)
        await hook.after_run(_run_ctx("帮我实现一个爬虫"))

        # 等到 idle 自然触发。
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            if not _PENDING_IDLE_TIMERS:
                break
            await asyncio.sleep(0.01)
        assert len(extractor.idle_calls) == 1
        assert extractor.calls == []  # 不再调旧的 extract_session 路径
        assert extractor.idle_calls[0].key == "s1"

    async def test_empty_messages_still_arms_idle(self):
        """空 messages 不再「跳过注册」;idle 路径不依赖 messages 非空。"""
        extractor = _FakeExtractor()
        hook = _make_hook(extractor=extractor, idle_seconds=5.0)
        await hook.after_run(AgentRunHookContext(messages=[]))
        try:
            # 旧测试断言 extractor.calls == [],本测试验证 idle 仍注册。
            assert "s1" in _PENDING_IDLE_TIMERS
        finally:
            await hook.on_finally(_run_ctx())

    async def test_on_finally_keeps_pending_idle(self):
        """on_finally 必须保留 idle 任务:它是一次 run 的收尾,不是空闲的开始。

        after_run 与 on_finally 相隔微秒(同一次 ``AgentRunner.run``),若在此取消,
        idle 提取永不触发。这里只断言"on_finally 不改动定时器且不阻塞"。
        """
        extractor = _FakeExtractor(delay=10.0)
        hook = _make_hook(extractor=extractor, idle_seconds=5.0)
        await hook.after_run(_run_ctx("帮我实现一个爬虫"))
        assert "s1" in _PENDING_IDLE_TIMERS
        timer_task = _PENDING_IDLE_TIMERS["s1"]

        started = time.monotonic()
        await hook.on_finally(_run_ctx())
        # 立即返回(< 1s),不阻塞。
        assert time.monotonic() - started < 1.0
        # 定时器仍在 dict 中,且就是同一个任务对象。
        assert _PENDING_IDLE_TIMERS.get("s1") is timer_task
        assert not timer_task.done()
        # 尚未到阈值,慢提取不应被调起。
        assert len(extractor.idle_calls) == 0

        # 清理:显式取消(阈值 5s 太长,不该让测试等)。
        timer_task.cancel()
        try:
            await timer_task
        except (asyncio.CancelledError, Exception):
            pass

    async def test_on_finally_without_pending_idle_is_noop(self):
        hook = _make_hook()
        await hook.on_finally(_run_ctx())  # 不抛

    async def test_idle_extraction_failure_does_not_raise(self):
        """idle 路径抛错时,_run_idle_extraction 内部 try/except 隔离。"""
        extractor = _FakeExtractor(raise_exc=RuntimeError("idle boom"))
        hook = _make_hook(extractor=extractor, idle_seconds=0.05)
        await hook.after_run(_run_ctx("帮我实现一个爬虫"))

        # 等 idle 触发并完成(失败隔离)。
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            if "s1" not in _PENDING_IDLE_TIMERS:
                break
            await asyncio.sleep(0.01)
        # run_idle 被调一次,但 hook 不抛。
        assert len(extractor.idle_calls) == 1


# ---------------------------------------------------------------------------
# T0'：on_error 紧急保存
# ---------------------------------------------------------------------------


class TestOnError:
    async def test_on_error_only_writes_scratchpad(self):
        extractor = _FakeExtractor()
        writer = _FakeScratchpadWriter()
        provider = _FakeProvider(result='{"same_topic": true}')
        runtime = _FakeRuntime(provider)
        hook = _make_hook(extractor=extractor, writer=writer, runtime=runtime)

        await hook.on_error(_run_ctx("帮我实现一个爬虫"))

        assert writer.focus_calls == ["帮我实现一个爬虫"]
        assert extractor.calls == []  # 不调 extractor
        assert provider.calls == []  # 不调 LLM

    async def test_on_error_without_user_message_is_noop(self):
        writer = _FakeScratchpadWriter()
        hook = _make_hook(writer=writer)
        await hook.on_error(AgentRunHookContext(messages=[{"role": "tool", "content": "x"}]))
        assert writer.focus_calls == []

    async def test_on_error_failure_does_not_raise(self):
        class _BoomWriter(_FakeScratchpadWriter):
            async def update_focus(self, new_focus: str) -> None:
                raise RuntimeError("db down")

        hook = MemoryExtractionHook(_FakeExtractor(), "s1", _BoomWriter())
        await hook.on_error(_run_ctx("帮我实现一个爬虫"))  # 不抛


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


class TestFactory:
    def test_factory_builds_hook_from_turn_context(self):
        extractor = _FakeExtractor()
        writer = _FakeScratchpadWriter()
        factory = create_memory_extraction_hook_factory(
            extractor_provider=lambda session_key: extractor,
            scratchpad_writer=writer,
        )

        hook = factory(AgentTurnHookContext(session_key="s1"))

        assert isinstance(hook, MemoryExtractionHook)
        assert hook._session_key == "s1"

    def test_factory_returns_none_without_session_key(self):
        factory = create_memory_extraction_hook_factory(
            extractor_provider=lambda session_key: _FakeExtractor(),
            scratchpad_writer=_FakeScratchpadWriter(),
        )
        assert factory(AgentTurnHookContext(session_key=None)) is None

    def test_factory_returns_none_when_provider_returns_none(self):
        factory = create_memory_extraction_hook_factory(
            extractor_provider=lambda session_key: None,
            scratchpad_writer=_FakeScratchpadWriter(),
        )
        assert factory(AgentTurnHookContext(session_key="s1")) is None

    def test_factory_survives_extractor_provider_failure(self):
        def _boom(session_key: str) -> Any:
            raise RuntimeError("no extractor")

        factory = create_memory_extraction_hook_factory(
            extractor_provider=_boom,
            scratchpad_writer=_FakeScratchpadWriter(),
        )
        assert factory(AgentTurnHookContext(session_key="s1")) is None

    def test_factory_wires_runtime_provider(self):
        extractor = _FakeExtractor()
        provider = _FakeProvider(result='{"same_topic": true}')
        runtime = _FakeRuntime(provider)
        factory = create_memory_extraction_hook_factory(
            extractor_provider=lambda session_key: extractor,
            scratchpad_writer=_FakeScratchpadWriter(),
            runtime_provider=lambda session_key: runtime,
        )

        hook = factory(AgentTurnHookContext(session_key="s1"))

        assert isinstance(hook, MemoryExtractionHook)
        assert hook._runtime is runtime

    def test_factory_survives_runtime_provider_failure(self):
        def _boom(session_key: str) -> Any:
            raise RuntimeError("no runtime")

        factory = create_memory_extraction_hook_factory(
            extractor_provider=lambda session_key: _FakeExtractor(),
            scratchpad_writer=_FakeScratchpadWriter(),
            runtime_provider=_boom,
        )

        hook = factory(AgentTurnHookContext(session_key="s1"))

        assert isinstance(hook, MemoryExtractionHook)
        assert hook._runtime is None
