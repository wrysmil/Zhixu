"""T06：Worker 私有 NDJSON 管道（契约 §5）。

父进程只接受「当前 attempt_id + 协议版本 + 状态机合法」的帧；重复 event_id 不重复写入；
超长帧与半行必须显式失败，不能被当成一条正常事件吃掉。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from nanobot.evaluation.ipc import (
    MAX_FRAME_BYTES,
    ParentIpc,
    encode_boot,
    encode_frame,
    read_frame_lines,
)
from nanobot.evaluation.schemas import TraceEvent


def usage_payload() -> dict[str, Any]:
    return {
        "input_tokens": 10,
        "output_tokens": 2,
        "total_tokens": 12,
        "source": "reported",
        "coverage": "complete",
        "physical_requests": 1,
        "iterations": 1,
        "missing_usage_calls": 0,
    }


def result_payload(attempt_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "result",
        "attempt_id": attempt_id,
        "execution_status": "succeeded",
        "final_answer": "done",
        "stop_reason": "end_turn",
        "usage": usage_payload(),
        "timing": {"queue_ms": 1, "agent_ms": 2, "grading_ms": None, "total_ms": 3},
        "errors": [],
        "trace_complete": True,
    }
    payload.update(overrides)
    return payload


def trace_payload(attempt_id: str, seq: int, **overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "seq": seq,
        "event_id": "11111111-1111-1111-1111-111111111111",
        "type": "turn.started",
        "turn": 0,
        "iteration": None,
        "call_id": None,
        "ts": "2026-10-16T00:00:00+00:00",
        "elapsed_ms": seq,
        "payload": {"content": "hello"},
    }
    event.update(overrides)
    return {"type": "trace", "attempt_id": attempt_id, "event": event}


def boot_payload(attempt_id: str) -> dict[str, Any]:
    return {
        "type": "boot",
        "role": "agent",
        "protocol_version": 1,
        "attempt_id": attempt_id,
        "runtime_root": "C:/eval/attempt/runtime",
        "workspace": "C:/eval/attempt/workspace",
        "profile_bundle": {"provider": "openai", "model": "gpt-test"},
        "case_snapshot": {},
    }


# ---------------------------------------------------------------------- 编码


def test_encode_frame_is_single_line_utf8_ndjson() -> None:
    raw = encode_frame({"type": "ready", "protocol_version": 1, "attempt_id": "a"})
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1
    assert json.loads(raw.decode("utf-8"))["type"] == "ready"


def test_encode_frame_rejects_oversized_payload() -> None:
    huge = {"type": "trace", "blob": "x" * (MAX_FRAME_BYTES + 1)}
    with pytest.raises(ValueError, match="IPC_FRAME_TOO_LARGE"):
        encode_frame(huge)


def test_encode_boot_carries_profile_bundle_without_secrets() -> None:
    raw = encode_boot(boot_payload("attempt-1"))
    decoded = json.loads(raw.decode("utf-8"))
    assert decoded["role"] == "agent"
    assert decoded["profile_bundle"] == {"provider": "openai", "model": "gpt-test"}


def test_encode_boot_rejects_unknown_protocol() -> None:
    payload = boot_payload("attempt-1")
    payload["protocol_version"] = 2
    with pytest.raises(ValueError, match="IPC_PROTOCOL_MISMATCH"):
        encode_boot(payload)


# ------------------------------------------------------------------ 行解码


def test_read_frame_lines_splits_complete_lines_only() -> None:
    stream = b'{"a":1}\n{"b":2}\n'
    assert read_frame_lines(stream) == [{"a": 1}, {"b": 2}]


def test_read_frame_lines_rejects_trailing_partial_frame() -> None:
    stream = b'{"a":1}\n{"b":2'
    with pytest.raises(ValueError, match="IPC_TRUNCATED_FRAME"):
        read_frame_lines(stream)


def test_read_frame_lines_rejects_blank_or_malformed_line() -> None:
    with pytest.raises(ValueError, match="IPC_INVALID_JSON"):
        read_frame_lines(b'{"a":1}\n\n')
    with pytest.raises(ValueError, match="IPC_INVALID_JSON"):
        read_frame_lines(b"not-json\n")


def test_read_frame_lines_rejects_frame_over_the_line_cap() -> None:
    oversized = b'{"blob":"' + b"x" * (MAX_FRAME_BYTES + 10) + b'"}\n'
    with pytest.raises(ValueError, match="IPC_FRAME_TOO_LARGE"):
        read_frame_lines(oversized)


# ---------------------------------------------------------------- 父进程状态机


def test_parent_accepts_ready_then_trace_then_result() -> None:
    attempt = "22222222-2222-2222-2222-222222222222"
    parent = ParentIpc(attempt_id=attempt)
    parent.accept({"type": "ready", "protocol_version": 1, "attempt_id": attempt})
    outcome = parent.accept(trace_payload(attempt, 1))
    assert outcome.duplicated is False
    assert len(parent.events) == 1
    assert isinstance(parent.events[0], TraceEvent)
    parent.accept(result_payload(attempt))
    assert parent.result is not None
    assert parent.result["execution_status"] == "succeeded"


def test_parent_rejects_foreign_attempt_id() -> None:
    attempt = "22222222-2222-2222-2222-222222222222"
    other = "33333333-3333-3333-3333-333333333333"
    parent = ParentIpc(attempt_id=attempt)
    with pytest.raises(ValueError, match="IPC_ATTEMPT_MISMATCH"):
        parent.accept({"type": "ready", "protocol_version": 1, "attempt_id": other})


def test_parent_rejects_unsupported_protocol_version() -> None:
    attempt = "22222222-2222-2222-2222-222222222222"
    parent = ParentIpc(attempt_id=attempt)
    with pytest.raises(ValueError, match="IPC_PROTOCOL_MISMATCH"):
        parent.accept({"type": "ready", "protocol_version": 2, "attempt_id": attempt})


def test_parent_rejects_unknown_frame_type() -> None:
    attempt = "22222222-2222-2222-2222-222222222222"
    parent = ParentIpc(attempt_id=attempt)
    with pytest.raises(ValueError, match="IPC_FRAME_TYPE_UNKNOWN"):
        parent.accept({"type": "log", "attempt_id": attempt, "text": "hello"})


def test_parent_rejects_trace_before_ready() -> None:
    attempt = "22222222-2222-2222-2222-222222222222"
    parent = ParentIpc(attempt_id=attempt)
    with pytest.raises(ValueError, match="IPC_STATE_ORDER"):
        parent.accept(trace_payload(attempt, 1))


def test_parent_rejects_second_ready_and_second_result() -> None:
    attempt = "22222222-2222-2222-2222-222222222222"
    parent = ParentIpc(attempt_id=attempt)
    parent.accept({"type": "ready", "protocol_version": 1, "attempt_id": attempt})
    with pytest.raises(ValueError, match="IPC_STATE_ORDER"):
        parent.accept({"type": "ready", "protocol_version": 1, "attempt_id": attempt})
    parent.accept(result_payload(attempt))
    with pytest.raises(ValueError, match="IPC_STATE_ORDER"):
        parent.accept(result_payload(attempt))


def test_parent_accepts_ready_after_boot_without_trailing_newline() -> None:
    attempt = "22222222-2222-2222-2222-222222222222"
    parent = ParentIpc(attempt_id=attempt)
    assert read_frame_lines(
        b'{"type":"ready","protocol_version":1,"attempt_id":"'
        + attempt.encode()
        + b'"}'
    ) == [{"type": "ready", "protocol_version": 1, "attempt_id": attempt}]
    parent.accept({"type": "ready", "protocol_version": 1, "attempt_id": attempt})
    assert parent.ready_received is True


def test_parent_deduplicates_replayed_event_id_without_error() -> None:
    attempt = "22222222-2222-2222-2222-222222222222"
    parent = ParentIpc(attempt_id=attempt)
    parent.accept({"type": "ready", "protocol_version": 1, "attempt_id": attempt})
    first = parent.accept(trace_payload(attempt, 1))
    again = parent.accept(trace_payload(attempt, 1))
    assert first.duplicated is False
    assert again.duplicated is True
    assert len(parent.events) == 1


def test_parent_rejects_trace_event_of_another_attempt() -> None:
    attempt = "22222222-2222-2222-2222-222222222222"
    other = "33333333-3333-3333-3333-333333333333"
    parent = ParentIpc(attempt_id=attempt)
    parent.accept({"type": "ready", "protocol_version": 1, "attempt_id": attempt})
    frame = trace_payload(attempt, 1)
    frame["event"]["attempt_id"] = other
    with pytest.raises(ValueError, match="IPC_ATTEMPT_MISMATCH"):
        parent.accept(frame)


def test_parent_rejects_invalid_event_shape() -> None:
    attempt = "22222222-2222-2222-2222-222222222222"
    parent = ParentIpc(attempt_id=attempt)
    parent.accept({"type": "ready", "protocol_version": 1, "attempt_id": attempt})
    with pytest.raises(ValueError, match="IPC_TRACE_INVALID"):
        parent.accept(trace_payload(attempt, 0))
    bad_type = trace_payload(attempt, 1)
    bad_type["event"]["type"] = "tool.renamed"
    with pytest.raises(ValueError, match="IPC_TRACE_INVALID"):
        parent.accept(bad_type)


def test_parent_rejects_malformed_result_frame() -> None:
    attempt = "22222222-2222-2222-2222-222222222222"
    parent = ParentIpc(attempt_id=attempt)
    parent.accept({"type": "ready", "protocol_version": 1, "attempt_id": attempt})
    with pytest.raises(ValueError, match="IPC_RESULT_INVALID"):
        parent.accept(result_payload(attempt, execution_status="done"))
    with pytest.raises(ValueError, match="IPC_RESULT_INVALID"):
        parent.accept(result_payload(attempt, unknown_field=1))


def test_parent_result_frame_keeps_multiple_errors_in_order() -> None:
    attempt = "22222222-2222-2222-2222-222222222222"
    parent = ParentIpc(attempt_id=attempt)
    parent.accept({"type": "ready", "protocol_version": 1, "attempt_id": attempt})
    errors = [
        {"phase": "execution", "code": "TOOL_FAILED", "message": "first"},
        {"phase": "cleanup", "code": "PROCESS_TREE_RESIDUE", "message": "second"},
    ]
    parent.accept(result_payload(attempt, execution_status="failed", errors=errors))
    assert parent.result is not None
    assert [item.code for item in parent.result_errors] == ["TOOL_FAILED", "PROCESS_TREE_RESIDUE"]


def test_parent_requires_result_before_terminal_success() -> None:
    attempt = "22222222-2222-2222-2222-222222222222"
    parent = ParentIpc(attempt_id=attempt)
    parent.accept({"type": "ready", "protocol_version": 1, "attempt_id": attempt})
    assert parent.can_seal_succeeded(exit_code=0) is False
    parent.accept(result_payload(attempt))
    assert parent.can_seal_succeeded(exit_code=0) is True
    assert parent.can_seal_succeeded(exit_code=1) is False


def test_parent_reports_missing_result_as_terminal_reason() -> None:
    attempt = "22222222-2222-2222-2222-222222222222"
    parent = ParentIpc(attempt_id=attempt)
    parent.accept({"type": "ready", "protocol_version": 1, "attempt_id": attempt})
    assert parent.exit_reason_without_result() == "IPC_RESULT_MISSING"
