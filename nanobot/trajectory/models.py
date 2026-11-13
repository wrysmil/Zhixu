"""轨迹域的稳定值对象与事件词汇。

轨迹是观察域：这里的对象只描述「已经观察到了什么」，不承担调度、
授权或聊天历史语义。字段名使用 Python snake_case，出网时再转 camelCase。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SCHEMA_VERSION = 1

EventType = Literal[
    "run.started",
    "run.finished",
    "turn.started",
    "turn.finished",
    "input.recorded",
    "context.injected",
    "step.started",
    "step.finished",
    "model.requested",
    "model.attempt.started",
    "model.output.delta",
    "model.attempt.finished",
    "model.retry.scheduled",
    "model.route.changed",
    "tool.requested",
    "tool.started",
    "tool.finished",
    "execution.linked",
    "compaction.started",
    "compaction.finished",
    "session.forked",
    "trace.gap",
]

SpanStatus = Literal[
    "requested",
    "running",
    "succeeded",
    "failed",
    "denied",
    "cancelled",
    "interrupted",
    "skipped",
]

RecordKind = Literal[
    "system",
    "user",
    "context",
    "assistant",
    "tool",
    "subtool",
    "compaction",
]

ContentMode = Literal["off", "metadata", "standard", "raw-chunks"]
CoverageStatus = Literal["complete", "partial", "unknown"]

_UNAVAILABLE = Literal[
    "not_recorded",
    "provider_managed",
    "expired",
    "budget_exceeded",
    "capture_failed",
]


@dataclass(frozen=True, slots=True)
class Coverage:
    status: CoverageStatus = "complete"
    reasons: tuple[str, ...] = ()
    content_mode: ContentMode = "standard"
    attempt_visibility: str = "provider_call"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "contentMode": self.content_mode,
            "attemptVisibility": self.attempt_visibility,
        }


@dataclass(frozen=True, slots=True)
class ContentRef:
    body_id: str | None
    preview: str
    content_type: str
    stored_bytes: int
    original_bytes: int | None = None
    redacted: bool = False
    truncated: bool = False
    unavailable_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bodyId": self.body_id,
            "preview": self.preview,
            "contentType": self.content_type,
            "storedBytes": self.stored_bytes,
            "originalBytes": self.original_bytes,
            "redacted": self.redacted,
            "truncated": self.truncated,
            "unavailableReason": self.unavailable_reason,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ContentRef":
        return cls(
            body_id=_opt_str(raw.get("bodyId")),
            preview=_str(raw.get("preview")),
            content_type=_str(raw.get("contentType")) or "text/plain",
            stored_bytes=_int(raw.get("storedBytes")),
            original_bytes=_opt_int(raw.get("originalBytes")),
            redacted=bool(raw.get("redacted")),
            truncated=bool(raw.get("truncated")),
            unavailable_reason=_opt_str(raw.get("unavailableReason")),
        )


def unavailable_ref(reason: str, content_type: str = "text/plain") -> ContentRef:
    return ContentRef(
        body_id=None,
        preview="",
        content_type=content_type,
        stored_bytes=0,
        original_bytes=None,
        unavailable_reason=reason,
    )


@dataclass(slots=True)
class BodyRow:
    body_id: str
    content_type: str
    text: str
    stored_bytes: int
    original_bytes: int
    redacted: bool
    truncated: bool
    preview: str


@dataclass(frozen=True, slots=True)
class TraceContext:
    workspace: str
    workspace_id: str
    session_id: str
    trace_id: str
    run_id: str
    turn_id: str | None = None
    execution_id: str | None = None
    parent_span_id: str | None = None
    source: str = "user"
    ephemeral: bool = False
    agent_id: str | None = None
    agent_name: str | None = None
    parent_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class EventCandidate:
    """提交前的不可变事件候选；seq 由单 writer 在事务内分配。"""

    session_id: str
    trace_id: str
    run_id: str
    type: str
    payload: dict[str, Any]
    time_ms: int
    turn_id: str | None = None
    step: int | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    execution_id: str | None = None
    logical_request_id: str | None = None
    model_call_id: str | None = None
    tool_call_id: str | None = None
    ignorable: bool = False
    bodies: tuple[BodyRow, ...] = field(default_factory=tuple)


@dataclass(slots=True)
class TrajectoryEvent:
    schema_version: int
    event_id: str
    session_id: str
    seq: int
    trace_id: str
    run_id: str
    turn_id: str | None
    step: int | None
    span_id: str | None
    parent_span_id: str | None
    execution_id: str | None
    logical_request_id: str | None
    model_call_id: str | None
    tool_call_id: str | None
    type: str
    ignorable: bool
    time_ms: int
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "eventId": self.event_id,
            "sessionId": self.session_id,
            "seq": self.seq,
            "traceId": self.trace_id,
            "runId": self.run_id,
            "turnId": self.turn_id,
            "step": self.step,
            "spanId": self.span_id,
            "parentSpanId": self.parent_span_id,
            "executionId": self.execution_id,
            "logicalRequestId": self.logical_request_id,
            "modelCallId": self.model_call_id,
            "toolCallId": self.tool_call_id,
            "type": self.type,
            "ignorable": self.ignorable,
            "timeMs": self.time_ms,
            "payload": self.payload,
        }


@dataclass(slots=True)
class TrajectoryRecord:
    record_id: str
    session_id: str
    kind: str
    run_id: str
    turn_id: str | None
    step: int | None
    span_id: str | None
    parent_span_id: str | None
    model_order: int | None
    status: str
    started_at_ms: int | None
    finished_at_ms: int | None
    duration_ms: int | None
    anchor_seq: int
    record_revision: int
    block_summaries: list[dict[str, Any]]
    request_ref: ContentRef | None
    schema_ref: ContentRef | None
    turn_started_ms: int = 0
    run_started_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recordId": self.record_id,
            "kind": self.kind,
            "runId": self.run_id,
            "turnId": self.turn_id,
            "step": self.step,
            "spanId": self.span_id,
            "parentSpanId": self.parent_span_id,
            "modelOrder": self.model_order,
            "status": self.status,
            "startedAtMs": self.started_at_ms,
            "finishedAtMs": self.finished_at_ms,
            "durationMs": self.duration_ms,
            "anchorSeq": self.anchor_seq,
            "recordRevision": self.record_revision,
            "blockSummaries": self.block_summaries,
            "requestRef": self.request_ref.to_dict() if self.request_ref else None,
            "schemaRef": self.schema_ref.to_dict() if self.schema_ref else None,
            "extra": self.extra,
        }


@dataclass(slots=True)
class RecordPage:
    records: list[TrajectoryRecord]
    snapshot_seq: int
    history_generation: int
    retained_from_seq: int
    has_older: bool
    next_before_cursor: str | None
    ancestor_stubs: list[dict[str, Any]] = field(default_factory=list)
    coverage: Coverage = field(default_factory=Coverage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "records": [item.to_dict() for item in self.records],
            "ancestorStubs": self.ancestor_stubs,
            "snapshotSeq": self.snapshot_seq,
            "historyGeneration": self.history_generation,
            "nextBeforeCursor": self.next_before_cursor,
            "hasOlder": self.has_older,
            "retainedFromSeq": self.retained_from_seq,
            "coverage": self.coverage.to_dict(),
        }


@dataclass(slots=True)
class EventPage:
    events: list[TrajectoryEvent]
    record_updates: list[TrajectoryRecord]
    snapshot_seq: int
    retained_from_seq: int
    durable_seq: int
    history_generation: int
    has_older: bool
    has_more: bool
    next_before_seq: int | None
    next_after_seq: int
    coverage: Coverage = field(default_factory=Coverage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "events": [item.to_dict() for item in self.events],
            "recordUpdates": [item.to_dict() for item in self.record_updates],
            "snapshotSeq": self.snapshot_seq,
            "retainedFromSeq": self.retained_from_seq,
            "durableSeq": self.durable_seq,
            "historyGeneration": self.history_generation,
            "nextBeforeSeq": self.next_before_seq,
            "nextAfterSeq": self.next_after_seq,
            "hasOlder": self.has_older,
            "hasMore": self.has_more,
            "coverage": self.coverage.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CommitBatch:
    workspace: str
    session_id: str
    from_seq: int
    to_seq: int
    durable_seq: int
    history_generation: int
    events: tuple[TrajectoryEvent, ...]
    record_updates: tuple[TrajectoryRecord, ...]


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _opt_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
