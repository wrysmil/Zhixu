"""工具报错时主动检索踩坑经验，把一行提示附到对应工具结果后面。

默认关闭（``agents.defaults.memoryPitfallHint``）。只读、最多提示 2 条/轮，
同一错误文本一轮只提示一次；不替换原始报错，只追加“仅供参考”的线索。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext

_MAX_HINTS_PER_TURN = 2
_MAX_ERROR_CHARS = 500
_HINT_HEADER = "\n\n[经验库提示 · 仅供参考]\n"


class PitfallRecallHook(AgentHook):
    """把 ``on_execute_tool_error`` 的错误文本变成下一轮的踩坑提示。"""

    def __init__(self, lookup: Callable[[str], str | None]) -> None:
        super().__init__()
        self._lookup = lookup
        self._pending: list[tuple[str, str]] = []
        self._seen: set[str] = set()

    async def on_execute_tool_error(
        self,
        context: AgentHookContext,
        tool_call: Any,
        tool: Any,
        params: Any,
        error: Any,
    ) -> None:
        text = str(error or "").strip()
        if not text:
            return
        signature = text[:_MAX_ERROR_CHARS]
        if signature in self._seen or len(self._pending) >= _MAX_HINTS_PER_TURN:
            return
        self._seen.add(signature)
        self._pending.append((str(getattr(tool_call, "id", "") or ""), signature))

    async def after_iteration(self, context: AgentHookContext) -> None:
        pending, self._pending = self._pending, []
        if not pending:
            return
        hints: dict[str, str] = {}
        for call_id, text in pending:
            try:
                hint = await asyncio.to_thread(self._lookup, text)
            except Exception as exc:  # noqa: BLE001 - 提示失败不能影响对话
                logger.warning("pitfall hint lookup failed: {}", exc)
                continue
            if hint:
                hints[call_id] = hint
        if not hints:
            return
        for message in context.messages:
            if message.get("role") != "tool":
                continue
            call_id = str(message.get("tool_call_id") or "")
            if call_id not in hints:
                continue
            content = str(message.get("content") or "")
            message["content"] = content + _HINT_HEADER + hints.pop(call_id)
        self._seen.clear()
