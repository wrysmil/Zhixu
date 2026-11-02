"""idle 计时器：新消息归零 + 被打断的轮次重新装备（2026-09-15 spec §3）。

现状缺口：``_arm_idle_timer`` 只在 ``after_run`` 调用，而轮次被取消时
``AgentRunner`` 直接 ``raise CancelledError``，不走 ``after_run``（``runner.py``）。
于是计时器量的是「距上一轮**正常结束**」，不是「距用户最后一条消息」——
用户连续发消息打断每一轮时，计时器仍从更早的那一轮往下数。

目标语义：触发时刻 = ``max(最后一条用户消息, 最后一轮结束) + 阈值``，
且**轮次进行中绝不触发**。
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
# Fakes
# ---------------------------------------------------------------------------


class _FakeExtractionResult:
    memory_ids: list[str] = []
    episode_ids: list[str] = []
    skipped = 0
    failed_tracks: list[str] = []


class _FakeExtractor:
    def __init__(self) -> None:
        self.idle_calls: list[Any] = []

    async def run_idle_extraction(self, session: Any) -> Any:
        self.idle_calls.append(session)
        return _FakeExtractionResult()


class _FakeScratchpadWriter:
    async def update_focus(self, new_focus: str) -> None:
        return None

    async def archive_completed(self, resolved_items: list[str]) -> None:
        return None


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 512
    reasoning_effort = None


class _FakeProvider:
    async def chat_with_retry(self, **_kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("本文件不应触发 LLM 调用")


class _FakeRuntime:
    def __init__(self) -> None:
        self.provider = _FakeProvider()
        self.model = "fake-model"
        self.generation = _FakeGeneration()


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    for task in list(_PENDING_IDLE_TIMERS.values()):
        if not task.done():
            task.cancel()
    await asyncio.sleep(0)
    _PENDING_IDLE_TIMERS.clear()


def _ctx(*user_messages: str, stop_reason: str | None = None) -> AgentRunHookContext:
    return AgentRunHookContext(
        messages=[{"role": "user", "content": m} for m in user_messages],
        stop_reason=stop_reason,
    )


def _make_hook(*, idle_seconds: float = 30.0) -> MemoryExtractionHook:
    return MemoryExtractionHook(
        _FakeExtractor(),
        "s1",
        _FakeScratchpadWriter(),
        runtime=_FakeRuntime(),
        idle_seconds=idle_seconds,
    )


# ---------------------------------------------------------------------------
# before_run：新消息归零
# ---------------------------------------------------------------------------


class TestNewMessageResetsTimer:
    async def test_new_message_cancels_pending_idle_timer(self):
        hook = _make_hook()
        await hook.after_run(_ctx("第一条"))
        armed = _PENDING_IDLE_TIMERS.get("s1")
        assert armed is not None

        await hook.before_run(_ctx("第二条"))

        assert "s1" not in _PENDING_IDLE_TIMERS
        assert armed.cancelled() or armed.cancelling()

    async def test_before_run_without_pending_timer_is_noop(self):
        hook = _make_hook()
        await hook.before_run(_ctx("第一条"))  # 不抛即通过
        assert "s1" not in _PENDING_IDLE_TIMERS

    async def test_before_run_does_not_arm_new_timer(self):
        """归零 ≠ 重新装备：``before_run`` 时刻这一轮还没跑完，装备会在对话进行中触发。"""
        hook = _make_hook()
        await hook.before_run(_ctx("第一条"))
        assert "s1" not in _PENDING_IDLE_TIMERS

    async def test_repeated_interruptions_never_trigger_extraction(self):
        """连续被打断的那几个轮次，全程不触发抽取。"""
        extractor = _FakeExtractor()
        hook = MemoryExtractionHook(
            extractor,
            "s1",
            _FakeScratchpadWriter(),
            runtime=_FakeRuntime(),
            idle_seconds=0.05,
        )
        await hook.after_run(_ctx("A"))  # 装备
        await hook.before_run(_ctx("B"))  # 归零
        await hook.before_run(_ctx("C"))  # 再归零
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert extractor.idle_calls == []

    async def test_memory_disabled_skips_reset(self):
        """记忆总开关关闭时 ``before_run`` 直接返回，不触碰计时器。"""
        # 先由「开启」的实例装一个计时器。
        enabled = _make_hook()
        await enabled.after_run(_ctx("第一条"))
        armed = _PENDING_IDLE_TIMERS["s1"]

        disabled = MemoryExtractionHook(
            _FakeExtractor(),
            "s1",
            _FakeScratchpadWriter(),
            runtime=_FakeRuntime(),
            memory_enabled_provider=lambda: False,
            idle_seconds=30.0,
        )
        await disabled.before_run(_ctx("第二条"))

        assert _PENDING_IDLE_TIMERS.get("s1") is armed


# ---------------------------------------------------------------------------
# on_finally：被打断/失败的轮次重新装备
# ---------------------------------------------------------------------------


class TestCancelledTurnRearmsTimer:
    async def test_cancelled_run_rearms_timer(self):
        hook = _make_hook()
        await hook.before_run(_ctx("被打断的那条"))
        assert "s1" not in _PENDING_IDLE_TIMERS

        await hook.on_finally(_ctx("被打断的那条", stop_reason="cancelled"))

        assert "s1" in _PENDING_IDLE_TIMERS

    async def test_error_run_rearms_timer(self):
        hook = _make_hook()
        await hook.before_run(_ctx("报错的那条"))
        await hook.on_finally(_ctx("报错的那条", stop_reason="error"))
        assert "s1" in _PENDING_IDLE_TIMERS

    async def test_normal_run_does_not_overwrite_after_run_timer(self):
        """正常结束由 ``after_run`` 装备；``on_finally`` 不得替换它（二者相隔微秒）。"""
        hook = _make_hook()
        await hook.after_run(_ctx("正常一轮"))
        original = _PENDING_IDLE_TIMERS["s1"]

        await hook.on_finally(_ctx("正常一轮", stop_reason="stop"))

        assert _PENDING_IDLE_TIMERS.get("s1") is original

    async def test_finally_without_stop_reason_is_noop(self):
        hook = _make_hook()
        await hook.on_finally(_ctx("没有 stop_reason"))
        assert "s1" not in _PENDING_IDLE_TIMERS

    async def test_cancelled_run_fires_after_threshold(self):
        extractor = _FakeExtractor()
        hook = MemoryExtractionHook(
            extractor,
            "s1",
            _FakeScratchpadWriter(),
            runtime=_FakeRuntime(),
            idle_seconds=0.05,
        )
        await hook.before_run(_ctx("被打断的那条"))
        await hook.on_finally(_ctx("被打断的那条", stop_reason="cancelled"))

        for _ in range(20):
            if extractor.idle_calls:
                break
            await asyncio.sleep(0.01)
        assert len(extractor.idle_calls) == 1
