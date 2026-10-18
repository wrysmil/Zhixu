"""普通子 Agent 执行的公开快照，不存储任务原文或工具输入输出。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

TERMINAL_STATUSES = frozenset({'succeeded', 'failed', 'cancelled', 'interrupted'})
PHASES = frozenset({'preparing', 'model', 'tools', 'finalizing'})


def camel(key: str) -> str:
    head, *tail = key.split('_')
    return head + ''.join(part.title() for part in tail)


@dataclass
class ExecutionActivity:
    activity_id: str
    tool_name: str | None
    label: str
    started_at: str
    kind: str = 'tool'
    status: str = 'running'
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {camel(key): value for key, value in asdict(self).items()}


@dataclass
class ExecutionSnapshot:
    invocation_id: str
    chat_id: str
    parent_turn_id: str
    parent_tool_call_id: str
    agent_id: str | None
    agent_name: str
    title: str
    instruction_summary: str
    mode: str
    created_at: str
    status: str = 'queued'
    phase: str = 'preparing'
    started_at: str | None = None
    finished_at: str | None = None
    queue_duration_ms: int | None = None
    duration_ms: int | None = None
    last_action: str | None = None
    completed_tool_count: int = 0
    last_seq: int = 0
    result: dict[str, Any] | None = None
    activities: list[ExecutionActivity] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        value = {camel(key): val for key, val in asdict(self).items()}
        value['activities'] = [activity.to_dict() for activity in self.activities]
        return value


@dataclass
class ExecutionEvent:
    invocation_id: str
    chat_id: str
    parent_turn_id: str
    parent_tool_call_id: str
    seq: int
    occurred_at: str
    type: str
    data: dict[str, Any]
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {camel(key): value for key, value in asdict(self).items()}


@dataclass
class ExecutionPage:
    items: list[ExecutionSnapshot]
    next_cursor: str | None = None
    has_more: bool = False
    server_now: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {'executions': [item.to_dict() for item in self.items],
                'nextCursor': self.next_cursor, 'hasMore': self.has_more,
                'serverNow': self.server_now}


@dataclass
class EventPage:
    items: list[ExecutionEvent]
    last_seq: int = 0
    has_more: bool = False
    reset_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {'events': [item.to_dict() for item in self.items], 'lastSeq': self.last_seq,
                'hasMore': self.has_more, 'resetRequired': self.reset_required}
