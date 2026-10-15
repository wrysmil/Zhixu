"""Leader 每回合装配测试（plan Task 4）。

三条口径各自钉住一处真实缺陷：

1. **空 registry 不回退**：``ToolRegistry.__len__`` 让空集合变成假值，
   ``tools or self.tools`` 会把「本轮零工具」静默扩成 loop 的全量工具。
2. **每回合 registry 按调用方给的算**：非空也要赢过 ``self.tools``，否则团队 Leader
   的白名单会被普通工具集盖掉。
3. **同一工具实例的上下文不串会话**：Leader 与普通聊天共用同名工具实例时，
   per-turn registry 必须是新对象，不能靠 ``unregister(self.tools)`` 再恢复。

全部用内存 provider 替身，不发真实网络请求、不读本机配置。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse


class _RecordingProvider:
    """记下 provider 实际收到的 tools，用来判定「模型可见工具集」。"""

    def __init__(self, default_model: str = "test-model") -> None:
        self.last_tool_definitions: list[Any] = []
        self.chat_with_retry = AsyncMock(side_effect=self._respond)
        self.get_default_model = MagicMock(return_value=default_model)
        self.generation = MagicMock(max_tokens=4096, temperature=0.1, reasoning_effort=None)
        self.estimate_prompt_tokens = MagicMock(return_value=(1_000, "test"))
        self.can_resume_conversation_state = MagicMock(return_value=False)

    async def _respond(self, **kwargs: Any) -> LLMResponse:
        self.last_tool_definitions = list(kwargs.get("tools") or [])
        return LLMResponse(content="done", tool_calls=[])


def _make_loop(tmp_path: Any, provider: _RecordingProvider) -> AgentLoop:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    # loop 默认全量工具集里必须真有名目，否则「不回退」这一条无从判定。
    assert len(loop.tools) > 0
    return loop


def _names(definitions: list[Any]) -> list[str]:
    """provider 收到的是 OpenAI function schema：名字在 ``function.name`` 里。"""
    return [str(item["function"]["name"]) for item in definitions]


@pytest.mark.asyncio
async def test_empty_registry_stays_empty(tmp_path: Any) -> None:
    """plan Task 4 锚点：零工具授权不能被静默解释成「全量放行」。"""
    provider = _RecordingProvider()
    loop = _make_loop(tmp_path, provider)

    await loop.process_direct("hello", tools=ToolRegistry())

    assert provider.last_tool_definitions == []


@pytest.mark.asyncio
async def test_per_turn_registry_wins_over_loop_default(tmp_path: Any) -> None:
    """反向口径：调用方给了非空 registry 时，用它的定义而不是 loop 的全量集合。"""
    provider = _RecordingProvider()
    loop = _make_loop(tmp_path, provider)
    scoped = ToolRegistry()
    for name in loop.tools.tool_names[:1]:
        tool = loop.tools.get(name)
        assert tool is not None
        scoped.register(tool)

    await loop.process_direct("hello", tools=scoped)

    assert _names(provider.last_tool_definitions) == scoped.tool_names
    assert len(provider.last_tool_definitions) < len(loop.tools.get_definitions())


@pytest.mark.asyncio
async def test_restore_turn_keeps_the_caller_registry_identity(tmp_path: Any) -> None:
    """无会话级限制时 per-turn registry 必须原样留着，不能换成 loop 的那个或复制一份。"""
    provider = _RecordingProvider()
    loop = _make_loop(tmp_path, provider)
    scoped = ToolRegistry()
    captured: dict[str, Any] = {}

    async def stop_after_restore(ctx: Any) -> None:
        captured["tools"] = ctx.tools
        raise RuntimeError("captured after restore")

    loop._restore_turn = stop_after_restore  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="captured after restore"):
        await loop.process_direct("hello", tools=scoped)

    assert captured["tools"] is scoped
