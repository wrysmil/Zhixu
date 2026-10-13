"""Encode typed outbound events for the WebUI wire protocol."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypeAlias, TypedDict

from nanobot.bus.outbound_events import (
    ContextCompactionEvent,
    RecoveryStateEvent,
    RetryStatusEvent,
    TurnEndEvent,
)
from nanobot.events import AgentEvent
from nanobot.webui.metadata import WEBUI_TURN_METADATA_KEY


class _ChatWirePayload(TypedDict):
    chat_id: str
    turn_id: NotRequired[str]


class RecoveryStateWirePayload(_ChatWirePayload):
    event: Literal["recovery_state"]
    status: str
    recovery_id: str
    attempts: int
    reason: NotRequired[str]
    can_continue: NotRequired[bool]


class RetryStatusWirePayload(_ChatWirePayload):
    event: Literal["retry_status"]
    state: str
    attempt: int
    max_attempts: NotRequired[int]
    error_kind: str
    retry_after_s: NotRequired[float]


class TurnEndWirePayload(_ChatWirePayload):
    event: Literal["turn_end"]
    latency_ms: NotRequired[int]
    goal_state: NotRequired[dict[str, Any]]
    usage: NotRequired[dict[str, int]]
    round_usages: NotRequired[list[dict[str, int]]]
    context_window_tokens: NotRequired[int]
    outcome: NotRequired[str]
    failure_kind: NotRequired[str]
    failure_error_kind: NotRequired[str]
    failure_attempts: NotRequired[int]
    failure_message: NotRequired[str]


class ContextCompactionWirePayload(_ChatWirePayload):
    event: Literal["context_compaction"]
    compaction_id: str
    phase: Literal["started", "succeeded", "failed", "cancelled"]


TerminalWireStatus: TypeAlias = Literal["ok", "already_running", "limit_reached", "error"]
TerminalOutputStream: TypeAlias = Literal["stdout", "stderr"]


class TerminalStartedWirePayload(TypedDict):
    # ``type`` mirrors ``event`` so the frame matches contract §1.2 verbatim
    # while keeping the repository-wide ``event`` discriminator convention.
    event: Literal["terminal_started"]
    type: Literal["terminal_started"]
    terminal_id: str
    shell: str
    cwd: str
    status: TerminalWireStatus
    message: NotRequired[str]
    pty: bool


class TerminalOutputWirePayload(TypedDict):
    event: Literal["terminal_output"]
    type: Literal["terminal_output"]
    terminal_id: str
    seq: int
    stream: TerminalOutputStream
    data: str


class TerminalExitWirePayload(TypedDict):
    event: Literal["terminal_exit"]
    type: Literal["terminal_exit"]
    terminal_id: str
    exit_code: int | None


class TerminalListEntryWire(TypedDict):
    terminal_id: str
    status: Literal["running", "exited"]
    shell: str
    cwd: str


class TerminalListResultWirePayload(TypedDict):
    event: Literal["terminal_list_result"]
    type: Literal["terminal_list_result"]
    terminals: list[TerminalListEntryWire]


WebUIWirePayload: TypeAlias = (
    RetryStatusWirePayload | ContextCompactionWirePayload | RecoveryStateWirePayload | TurnEndWirePayload
)
WebUIWirePersistence: TypeAlias = Literal[
    "transient",
    "turn_activity",
    "turn_complete",
]


def encode_recovery_state(
    chat_id: str,
    event: RecoveryStateEvent,
) -> RecoveryStateWirePayload:
    """Project one transient recovery transition onto its stable wire shape."""
    payload: RecoveryStateWirePayload = {
        "event": "recovery_state",
        "chat_id": chat_id,
        "status": event.status,
        "recovery_id": event.recovery_id,
        "attempts": event.attempts,
    }
    if event.reason:
        payload["reason"] = event.reason
    if event.can_continue is not None:
        payload["can_continue"] = event.can_continue
    return payload


def encode_context_compaction(
    chat_id: str,
    event: ContextCompactionEvent,
) -> ContextCompactionWirePayload:
    """Project one summary-free compaction transition onto its stable wire shape."""
    payload: ContextCompactionWirePayload = {
        "event": "context_compaction",
        "chat_id": chat_id,
        "compaction_id": event.compaction_id,
        "phase": event.phase,
    }
    return payload


@dataclass(frozen=True)
class NotificationProjection:
    """Allowlisted public payload and its durability policy."""

    payload: WebUIWirePayload
    persistence: WebUIWirePersistence = "transient"
    deliver_offline: bool = False
    attach_turn_metadata: bool = False


def project_notification(
    chat_id: str, event: AgentEvent | None, metadata: Mapping[str, object] | None = None,
) -> NotificationProjection | None:
    """Keep notification serialization and persistence decisions at one boundary."""
    if isinstance(event, ContextCompactionEvent):
        return NotificationProjection(
            encode_context_compaction(chat_id, event),
            persistence="transient" if event.phase == "started" else "turn_activity",
            deliver_offline=True,
            attach_turn_metadata=True,
        )
    if isinstance(event, RetryStatusEvent):
        return NotificationProjection(encode_retry_status(chat_id, event, metadata))
    if isinstance(event, RecoveryStateEvent):
        return NotificationProjection(encode_recovery_state(chat_id, event))
    return None


def encode_retry_status(
    chat_id: str,
    event: RetryStatusEvent,
    metadata: Mapping[str, object] | None = None,
) -> RetryStatusWirePayload:
    """Project one transient retry transition onto its stable wire shape."""
    payload: RetryStatusWirePayload = {
        "event": "retry_status",
        "chat_id": chat_id,
        "state": event.state,
        "attempt": event.attempt,
        "error_kind": event.error_kind,
    }
    turn_id = (metadata or {}).get(WEBUI_TURN_METADATA_KEY)
    if isinstance(turn_id, str) and turn_id:
        payload["turn_id"] = turn_id
    if event.max_attempts is not None:
        payload["max_attempts"] = event.max_attempts
    if event.next_retry_at is not None:
        payload["retry_after_s"] = max(0.0, event.next_retry_at - time.time())
    return payload
def encode_turn_end(
    chat_id: str,
    event: TurnEndEvent,
    metadata: Mapping[str, object] | None = None,
) -> TurnEndWirePayload:
    """Project a completed turn without leaking internal metadata onto the wire."""
    payload: TurnEndWirePayload = {
        "event": "turn_end",
        "chat_id": chat_id,
    }
    turn_id = (metadata or {}).get(WEBUI_TURN_METADATA_KEY)
    if isinstance(turn_id, str) and turn_id:
        payload["turn_id"] = turn_id
    if event.latency_ms is not None:
        payload["latency_ms"] = int(event.latency_ms)
    if event.goal_state is not None:
        payload["goal_state"] = event.goal_state
    if event.usage is not None:
        payload["usage"] = event.usage.to_turn_dict()
    if event.round_usages:
        payload["round_usages"] = [item.to_turn_dict() for item in event.round_usages]
    if event.context_window_tokens is not None:
        payload["context_window_tokens"] = int(event.context_window_tokens)
    if event.outcome != "completed":
        payload["outcome"] = event.outcome
    if event.failure_kind is not None:
        payload["failure_kind"] = event.failure_kind
    if event.failure_error_kind is not None:
        payload["failure_error_kind"] = event.failure_error_kind
    if event.failure_attempts is not None:
        payload["failure_attempts"] = int(event.failure_attempts)
    if event.failure_message is not None:
        payload["failure_message"] = event.failure_message
    return payload


def encode_terminal_started(
    *,
    terminal_id: str,
    shell: str,
    cwd: str,
    status: TerminalWireStatus,
    message: str | None = None,
    pty: bool = False,
) -> TerminalStartedWirePayload:
    """Project one terminal_start answer onto the contract wire shape."""
    payload: TerminalStartedWirePayload = {
        "event": "terminal_started",
        "type": "terminal_started",
        "terminal_id": terminal_id,
        "shell": shell,
        "cwd": cwd,
        "status": status,
        "pty": bool(pty),
    }
    if message:
        payload["message"] = message
    return payload


def encode_terminal_output(
    *,
    terminal_id: str,
    seq: int,
    stream: TerminalOutputStream,
    data: str,
) -> TerminalOutputWirePayload:
    """Project one ordered terminal output chunk onto the wire."""
    payload: TerminalOutputWirePayload = {
        "event": "terminal_output",
        "type": "terminal_output",
        "terminal_id": terminal_id,
        "seq": int(seq),
        "stream": stream,
        "data": data,
    }
    return payload


def encode_terminal_exit(
    *,
    terminal_id: str,
    exit_code: int | None,
) -> TerminalExitWirePayload:
    """Project one terminal termination; ``exit_code`` None means expired/unknown."""
    payload: TerminalExitWirePayload = {
        "event": "terminal_exit",
        "type": "terminal_exit",
        "terminal_id": terminal_id,
        "exit_code": exit_code,
    }
    return payload


def encode_terminal_list_result(
    terminals: list[TerminalListEntryWire],
) -> TerminalListResultWirePayload:
    """Project the visible terminal inventory for one WebUI connection."""
    payload: TerminalListResultWirePayload = {
        "event": "terminal_list_result",
        "type": "terminal_list_result",
        "terminals": list(terminals),
    }
    return payload
