"""仅投影公开字段；进度节流不会删除生命周期事件。"""
from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any, cast

from nanobot.subagent_executions.models import ExecutionEvent

ACTIVITY_MAX_DATA_BYTES = 4096
ACTIVITY_MIN_INTERVAL_SECONDS = 0.25
EXECUTION_FIELDS = (
    'invocationId', 'chatId', 'parentTurnId', 'parentToolCallId', 'agentId', 'agentName',
    'title', 'mode', 'status', 'phase', 'createdAt', 'startedAt', 'finishedAt',
    'queueDurationMs', 'durationMs', 'lastAction', 'completedToolCount', 'lastSeq',
)
ACTIVITY_FIELDS = ('activityId', 'kind', 'toolName', 'status', 'label', 'startedAt', 'finishedAt')
ENVELOPE_FIELDS = ('schemaVersion', 'invocationId', 'chatId', 'parentTurnId',
                   'parentToolCallId', 'seq', 'occurredAt', 'createdAt', 'type')
EVENT_TYPES = frozenset({'execution.created', 'execution.started', 'execution.phase_changed',
                         'execution.activity', 'execution.finished'})


def _pick(source: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, Mapping):
        return {}
    values = cast('Mapping[str, Any]', source)
    return {key: value for key in fields if key in values
            and isinstance(value := values[key], (str, int, float, bool, type(None)))}


def project_execution_event(event: ExecutionEvent | Mapping[str, Any]) -> dict[str, Any] | None:
    source = event.to_dict() if isinstance(event, ExecutionEvent) else event
    if source.get('schemaVersion') != 1 or source.get('type') not in EVENT_TYPES:
        return None
    wire = _pick(source, ENVELOPE_FIELDS)
    source_data = source.get('data')
    data: dict[str, Any] = {}
    if isinstance(source_data, Mapping):
        if 'execution' in source_data:
            data['execution'] = _pick(source_data['execution'], EXECUTION_FIELDS)
        if 'activity' in source_data:
            data['activity'] = _pick(source_data['activity'], ACTIVITY_FIELDS)
    wire['data'] = data
    # 先限制展示文案，再逐步去掉可补拉字段；身份、状态和序号不删除。
    for key, limit in (('agentName', 80), ('title', 120), ('lastAction', 240),
                       ('label', 240), ('toolName', 120)):
        for section in data.values():
            if isinstance(section.get(key), str):
                section[key] = section[key][:limit]
    if len(json.dumps(wire, ensure_ascii=False).encode()) <= ACTIVITY_MAX_DATA_BYTES:
        return wire
    for section in data.values():
        for key in ('agentName', 'title', 'lastAction', 'label', 'toolName'):
            section.pop(key, None)
    if len(json.dumps(wire, ensure_ascii=False).encode()) <= ACTIVITY_MAX_DATA_BYTES:
        return wire
    return None


class ActivityThrottle:
    def __init__(self) -> None:
        self._last: dict[str, float] = {}

    def allow(self, invocation_id: str, event_type: str, *, now: float | None = None) -> bool:
        if event_type != 'execution.activity':
            if event_type == 'execution.finished':
                self._last.pop(invocation_id, None)
            return True
        current = time.monotonic() if now is None else now
        previous = self._last.get(invocation_id)
        if previous is not None and current - previous < ACTIVITY_MIN_INTERVAL_SECONDS:
            return False
        self._last[invocation_id] = current
        return True
