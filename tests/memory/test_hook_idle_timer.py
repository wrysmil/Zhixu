"""WU-A Task 3：``MemoryExtractionHook`` 的 idle 定时器契约测试。

覆盖 plan 描述的 4 条契约:
- after_run 注册 idle 任务到 ``_PENDING_IDLE_TIMERS``;
- 第二次 after_run 取消前一个任务;
- ``IDLE_THRESHOLD_SECONDS`` 触发后调用 ``run_idle_extraction``;
- on_finally **不**取消当前会话的 idle 任务(定时器须跨 run 存活)。

测试不依赖真实 provider / LLM,extractor 用 fake (duck-typed) 替代,
``_run_idle_extraction`` 内部调 ``run_idle_extraction`` 用 ``asyncio.sleep``
缩短等待时间(``hook.IDLE_THRESHOLD_SECONDS = 0.05``)。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nanobot.agent.hook import AgentRunHookContext
from nanobot.agent.hooks.memory_extraction import (
    _PENDING_IDLE_TIMERS,
    MemoryExtractionHook,
)

# ---------------------------------------------------------------------------
# Fakes（鸭子类型）
# ---------------------------------------------------------------------------


class _FakeScratchpadWriter:
    def __init__(self) -> None:
        self.focus_calls: list[str] = []

    async def update_focus(self, new_focus: str) -> None:
        self.focus_calls.append(new_focus)


class _FakeExtractor:
    """记录 ``run_idle_extraction`` 调用,支持 ``raise_exc`` 模拟失败。"""

    def __init__(self, *, raise_exc: BaseException | None = None) -> None:
        self.idle_calls: list[Any] = []
        self.raise_exc = raise_exc

    async def run_idle_extraction(self, session: Any) -> Any:
        self.idle_calls.append(session)
        if self.raise_exc is not None:
            raise self.raise_exc
        return None

    # 兼容 hook 工厂的 duck-typed 调用(不会触发,但保留防止误用)。
    async def extract_session(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def extract_incremental(self, *_args: Any, **_kwargs: Any) -> None:
        return None


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _run_ctx(messages: list[dict[str, Any]] | None = None) -> AgentRunHookContext:
    return AgentRunHookContext(
        messages=messages
        or [{"role": "user", "content": "以后都用 uv 管理依赖"}],
    )


def _make_hook(
    *,
    extractor: _FakeExtractor | None = None,
    writer: _FakeScratchpadWriter | None = None,
    session_key: str = "s1",
    idle_seconds: float | None = None,
) -> MemoryExtractionHook:
    return MemoryExtractionHook(
        extractor or _FakeExtractor(),
        session_key,
        writer or _FakeScratchpadWriter(),
        idle_seconds=idle_seconds,
    )


async def _drain_pending(timeout: float = 2.0) -> None:
    """等待所有 _PENDING_IDLE_TIMERS 中的任务完成,或超时。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while _PENDING_IDLE_TIMERS and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.01)
    if _PENDING_IDLE_TIMERS:
        for task in list(_PENDING_IDLE_TIMERS.values()):
            task.cancel()
        raise AssertionError(
            f"idle tasks did not finish within {timeout}s: {list(_PENDING_IDLE_TIMERS)}"
        )


@pytest.fixture(autouse=True)
async def _cleanup_pending_timers():
    """测试前后清空 _PENDING_IDLE_TIMERS,防止泄漏到其他测试。"""
    yield
    for task in list(_PENDING_IDLE_TIMERS.values()):
        if not task.done():
            task.cancel()
    # 给 cancel 一点时间落地。
    for _ in range(50):
        if all(t.done() for t in _PENDING_IDLE_TIMERS.values()):
            break
        await asyncio.sleep(0.01)
    _PENDING_IDLE_TIMERS.clear()


# ---------------------------------------------------------------------------
# 契约测试
# ---------------------------------------------------------------------------


class TestIdleTimerContract:
    async def test_after_run_arms_idle_timer(self):
        """after_run 后 ``_PENDING_IDLE_TIMERS[self._session_key]`` 必须非空。"""
        hook = _make_hook()
        ctx = _run_ctx()
        await hook.after_run(ctx)

        assert hook._session_key in _PENDING_IDLE_TIMERS
        task = _PENDING_IDLE_TIMERS[hook._session_key]
        assert not task.done()

        # 清理:cancel + 等待。
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def test_after_run_cancels_previous_idle_timer(self):
        """第二次 after_run 必须取消第一个任务并替换。"""
        hook = _make_hook()
        ctx1 = _run_ctx([{"role": "user", "content": "first"}])
        ctx2 = _run_ctx([{"role": "user", "content": "second"}])

        await hook.after_run(ctx1)
        first_task = _PENDING_IDLE_TIMERS[hook._session_key]
        assert first_task is not None
        assert not first_task.done()

        await hook.after_run(ctx2)
        second_task = _PENDING_IDLE_TIMERS[hook._session_key]
        # 新任务已替换旧任务,且二者是不同对象。
        assert second_task is not first_task
        # 旧任务已 cancelled。
        # wait 给事件循环一点时间处理 cancel。
        for _ in range(50):
            if first_task.done():
                break
            await asyncio.sleep(0.01)
        assert first_task.cancelled() or first_task.done()

        # 清理。
        second_task.cancel()
        try:
            await second_task
        except (asyncio.CancelledError, Exception):
            pass

    async def test_idle_timer_fires_extraction(self):
        """``IDLE_THRESHOLD_SECONDS`` 到达后,``run_idle_extraction`` 必须被调一次。"""
        extractor = _FakeExtractor()
        hook = _make_hook(extractor=extractor, idle_seconds=0.05)
        ctx = _run_ctx()
        await hook.after_run(ctx)

        # 等待阈值 + 余量。
        await _drain_pending(timeout=2.0)

        assert len(extractor.idle_calls) == 1
        session = extractor.idle_calls[0]
        assert session.key == hook._session_key

    async def test_on_finally_does_not_cancel_idle_timer(self):
        """on_finally 必须**保留** idle 任务。

        回归:``after_run`` 与 ``on_finally`` 同属一次 ``AgentRunner.run``(after_run
        之后紧跟 finally 块),若 on_finally 取消定时器,协程会在被事件循环首次调度
        前就被 cancel,idle 提取永不触发(线上表现:日志停在 "idle timer armed")。
        """
        extractor = _FakeExtractor()
        hook = _make_hook(extractor=extractor, idle_seconds=0.05)
        ctx = _run_ctx()
        await hook.after_run(ctx)

        timer_task = _PENDING_IDLE_TIMERS[hook._session_key]
        assert not timer_task.done()

        await hook.on_finally(ctx)

        # 定时器必须仍存活。
        assert not timer_task.done()
        assert _PENDING_IDLE_TIMERS.get(hook._session_key) is timer_task

        # 且必须在阈值后真正触发提取。
        await _drain_pending(timeout=2.0)
        assert len(extractor.idle_calls) == 1

    async def test_idle_timer_survives_full_run_lifecycle(self):
        """复刻 runner 顺序 after_run → finally:on_finally,提取必须触发。"""
        extractor = _FakeExtractor()
        hook = _make_hook(extractor=extractor, idle_seconds=0.05)
        ctx = _run_ctx([{"role": "user", "content": "用 uv 管理依赖"}])

        await hook.after_run(ctx)
        await hook.on_finally(ctx)  # runner.py:352 的 finally 块

        await _drain_pending(timeout=2.0)
        assert len(extractor.idle_calls) == 1

    async def test_multiple_sessions_have_independent_timers(self):
        """两个不同 session_key 的 hook 必须各自维护独立 idle 任务。"""
        hook_a = _make_hook(session_key="sA", idle_seconds=0.05)
        hook_b = _make_hook(session_key="sB", idle_seconds=5.0)

        await hook_a.after_run(_run_ctx([{"role": "user", "content": "a"}]))
        await hook_b.after_run(_run_ctx([{"role": "user", "content": "b"}]))

        assert "sA" in _PENDING_IDLE_TIMERS
        assert "sB" in _PENDING_IDLE_TIMERS
        assert _PENDING_IDLE_TIMERS["sA"] is not _PENDING_IDLE_TIMERS["sB"]

        # 让 A 自然触发;B 还在等(超时很长,不会触发)。
        # 轮询直到 sA 任务完成(已 callback 弹出 dict),但 sB 还在。
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            if "sA" not in _PENDING_IDLE_TIMERS and "sB" in _PENDING_IDLE_TIMERS:
                break
            await asyncio.sleep(0.01)
        assert "sA" not in _PENDING_IDLE_TIMERS
        assert "sB" in _PENDING_IDLE_TIMERS

        # 清理 B。
        b_task = _PENDING_IDLE_TIMERS["sB"]
        b_task.cancel()
        try:
            await b_task
        except (asyncio.CancelledError, Exception):
            pass

    async def test_extractor_exception_does_not_break_idle_loop(self):
        """``run_idle_extraction`` 抛错时,idle 路径不应挂起或上抛。"""
        extractor = _FakeExtractor(raise_exc=RuntimeError("idle boom"))
        hook = _make_hook(extractor=extractor, idle_seconds=0.05)
        ctx = _run_ctx()
        await hook.after_run(ctx)

        # 等到定时器触发并完成(失败隔离后正常返回)。
        await _drain_pending(timeout=2.0)

        # run_idle 已被调,且未导致 dict 残留。
        assert len(extractor.idle_calls) == 1
        assert hook._session_key not in _PENDING_IDLE_TIMERS
