"""Lifecycle projection for real tool activity."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from nanobot.agent.hook import AgentHook, AgentHookContext, AgentRunHookContext
from nanobot.providers.base import ToolCallRequest
from nanobot.session.workbench_observations import TurnObservationRecorder


class WorkbenchActivityHook(AgentHook):
    def __init__(self, recorder: TurnObservationRecorder, *, manager: Any = None) -> None:
        super().__init__()
        self.recorder = recorder
        self.manager = manager
        self._pending: dict[str, str] = {}

    def _record(self, call_id: str, label: str, status: str) -> None:
        safe_label = label if re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', label) else 'tool'
        digest = hashlib.sha256(call_id.encode()).hexdigest()
        self.recorder.record({'id': f'tool:{digest}', 'call_id': call_id, 'kind': 'tool',
                              'status': status, 'label': safe_label,
                              'at': datetime.now(timezone.utc).isoformat()})
        if self.manager is not None:
            self.recorder.persist_to_manager(self.manager)

    def _phase(self, phase: str, status: str) -> None:
        self.recorder.record({'id': 'phase:run', 'kind': 'phase', 'status': status,
                              'label': phase, 'at': datetime.now(timezone.utc).isoformat()})
        if self.manager is not None:
            self.recorder.persist_to_manager(self.manager)

    async def before_run(self, context: AgentRunHookContext) -> None:
        self._phase('processing', 'running')

    async def before_execute_tool(self, context: AgentHookContext, tool_call: ToolCallRequest,
                                  tool: Any, params: Any) -> None:
        self._pending[tool_call.id] = tool_call.name
        self._record(tool_call.id, tool_call.name, 'running')

    async def after_execute_tool(self, context: AgentHookContext, tool_call: ToolCallRequest,
                                 tool: Any, params: Any, result: Any) -> None:
        self._pending.pop(tool_call.id, None)
        self._record(tool_call.id, tool_call.name, 'succeeded')

    async def on_execute_tool_error(self, context: AgentHookContext, tool_call: ToolCallRequest,
                                    tool: Any, params: Any, error: Any) -> None:
        self._pending.pop(tool_call.id, None)
        self._record(tool_call.id, tool_call.name, 'failed')

    async def on_finally(self, context: AgentRunHookContext) -> None:
        status = 'cancelled' if context.stop_reason == 'cancelled' else 'failed'
        for call_id, label in list(self._pending.items()):
            self._record(call_id, label, status)
        self._pending.clear()
        if context.stop_reason == 'cancelled':
            self._phase('interrupted', 'cancelled')
        elif context.error or context.exception or context.stop_reason == 'error':
            self._phase('failed', 'failed')
        else:
            self._phase('completed', 'succeeded')
