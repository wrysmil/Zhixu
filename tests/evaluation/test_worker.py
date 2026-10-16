"""T05/T06：Worker 编排与契约 §6.1 ``AttemptEvidence`` 接缝。

``evidence.py`` 是 WU-03 产出、WU-04 消费的跨 WU 接缝，因此这里的用例逐条对准契约 §6.1
的硬约束：三个读取入口都必须走 ``resolve_inside``，越界抛 ``PATH_OUTSIDE_ROOT``，未登记产物
（``artifact_id=None``）不可读取；``resolve_ref`` 只认两种前缀且必须属于当前 attempt。
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from nanobot.evaluation.evidence import (
    ARTIFACT_NOT_REGISTERED,
    AttemptEvidence,
    EvidenceArtifact,
    EvidenceError,
    ToolCallEvidence,
    build_attempt_evidence,
    tool_calls_from_events,
)
from nanobot.evaluation.schemas import ExecutionStatus, TraceEvent
from nanobot.evaluation.validation import PATH_OUTSIDE_ROOT


def make_event(
    attempt_id: str,
    seq: int,
    event_type: str = "turn.started",
    payload: dict[str, Any] | None = None,
    *,
    call_id: str | None = None,
    turn: int | None = 0,
) -> TraceEvent:
    return TraceEvent(
        schema_version=1,
        attempt_id=uuid.UUID(attempt_id),
        seq=seq,
        event_id=uuid.uuid4(),
        type=event_type,
        turn=turn,
        iteration=None,
        call_id=call_id,
        ts="2026-10-15T10:00:00+00:00",
        elapsed_ms=seq * 5,
        payload=payload or {},
    )


@pytest.fixture
def attempt_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "out.txt").write_text("hello", encoding="utf-8")
    (root / "data.json").write_text(json.dumps({"total": 42}), encoding="utf-8")
    return root


def artifact_for(workspace: Path, relative: str, *, registered: bool = True) -> EvidenceArtifact:
    payload = (workspace / relative).read_bytes()
    return EvidenceArtifact(
        relative_path=relative,
        media_type="text/plain",
        size_bytes=len(payload),
        sha256="0" * 64,
        artifact_id=uuid.uuid4() if registered else None,
    )


def make_evidence(
    attempt_id: str,
    workspace: Path,
    events: list[TraceEvent] | None = None,
    artifacts: list[EvidenceArtifact] | None = None,
) -> AttemptEvidence:
    return build_attempt_evidence(
        attempt_id=attempt_id,
        case_snapshot={"name": "样例用例", "turns": [{"content": "hi"}]},
        workspace=workspace,
        trace_events=events or [make_event(attempt_id, 1)],
        artifacts=artifacts
        if artifacts is not None
        else [
            artifact_for(workspace, "out.txt"),
            artifact_for(workspace, "data.json"),
        ],
        final_answer="42",
        stop_reason="stop",
        iterations=2,
    )


# --------------------------------------------------------------------------- 读取入口


def test_read_text_and_json_stay_inside_workspace(
    attempt_id: str, workspace: Path
) -> None:
    evidence = make_evidence(attempt_id, workspace)
    assert evidence.read_text("out.txt") == "hello"
    assert evidence.read_json("data.json") == {"total": 42}
    assert evidence.exists("out.txt") is True
    # 未登记路径不开放探测：三个入口只认登记表里的条目。
    with pytest.raises(EvidenceError, match=ARTIFACT_NOT_REGISTERED):
        evidence.exists("missing.txt")


def test_registered_but_absent_artifact_reports_missing(
    attempt_id: str, workspace: Path
) -> None:
    ghost = EvidenceArtifact(
        relative_path="ghost.txt",
        media_type="text/plain",
        size_bytes=0,
        sha256="0" * 64,
        artifact_id=uuid.uuid4(),
    )
    evidence = make_evidence(attempt_id, workspace, artifacts=[ghost])
    assert evidence.exists("ghost.txt") is False
    with pytest.raises(OSError):
        evidence.read_text("ghost.txt")


@pytest.mark.parametrize("relative", ["../escape.txt", "/etc/passwd", "C:\\Windows\\a.txt", "a/../../b"])
def test_all_read_entries_reject_outside_paths(
    attempt_id: str, workspace: Path, relative: str
) -> None:
    evidence = make_evidence(attempt_id, workspace)
    for read in (
        lambda: evidence.read_text(relative),
        lambda: evidence.read_json(relative),
        lambda: evidence.exists(relative),
    ):
        with pytest.raises(ValueError, match=PATH_OUTSIDE_ROOT):
            read()


def test_unregistered_artifact_is_not_readable(attempt_id: str, workspace: Path) -> None:
    evidence = make_evidence(
        attempt_id,
        workspace,
        artifacts=[artifact_for(workspace, "out.txt", registered=False)],
    )
    assert evidence.artifact("out.txt").artifact_id is None
    with pytest.raises(ValueError, match=ARTIFACT_NOT_REGISTERED):
        evidence.read_text("out.txt")
    with pytest.raises(EvidenceError, match=ARTIFACT_NOT_REGISTERED):
        evidence.exists("out.txt")


def test_path_escaping_through_link_is_rejected(
    attempt_id: str, workspace: Path, tmp_path: Path
) -> None:
    """外指链接（Windows junction / POSIX symlink）解析后必须落在 workspace 内。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    relative = "link/secret.txt"
    if os.name == "nt":
        # junction 不需要管理员权限或开发者模式，能稳定复现"链接外指"。
        import _winapi

        _winapi.CreateJunction(str(outside), str(workspace / "link"))
    else:  # pragma: posix
        (workspace / "link").symlink_to(outside)
    entry = EvidenceArtifact(
        relative_path=relative,
        media_type="text/plain",
        size_bytes=secret.stat().st_size,
        sha256="0" * 64,
        artifact_id=uuid.uuid4(),
    )
    evidence = make_evidence(attempt_id, workspace, artifacts=[entry])
    with pytest.raises(ValueError, match=PATH_OUTSIDE_ROOT):
        evidence.read_text(relative)


def test_read_json_rejects_non_json_content(attempt_id: str, workspace: Path) -> None:
    evidence = make_evidence(attempt_id, workspace)
    with pytest.raises(EvidenceError, match="FILE_NOT_JSON"):
        evidence.read_json("out.txt")


# --------------------------------------------------------------------------- evidence_refs


def test_resolve_trace_ref_requires_current_attempt_and_seq(
    attempt_id: str, workspace: Path
) -> None:
    evidence = make_evidence(
        attempt_id, workspace, events=[make_event(attempt_id, 1), make_event(attempt_id, 2, "turn.completed")]
    )
    assert evidence.resolve_ref(f"trace:{attempt_id}:2").seq == 2
    assert evidence.resolve_ref(f"trace:{attempt_id}:9") is None
    assert evidence.resolve_ref(f"trace:{uuid.uuid4()}:1") is None
    assert evidence.resolve_ref("artifact:not-a-uuid") is None
    assert evidence.resolve_ref("message:1") is None


def test_resolve_artifact_ref_rejects_unregistered_id(attempt_id: str, workspace: Path) -> None:
    registered = artifact_for(workspace, "out.txt")
    anonymous = artifact_for(workspace, "data.json", registered=False)
    evidence = make_evidence(attempt_id, workspace, artifacts=[registered, anonymous])
    assert evidence.resolve_ref(f"artifact:{registered.artifact_id}") is registered
    assert evidence.resolve_ref(f"artifact:{uuid.uuid4()}") is None
    assert evidence.resolve_ref("artifact:null") is None


def test_trace_ref_of_foreign_attempt_is_not_constructed(attempt_id: str, workspace: Path) -> None:
    foreign = make_event(str(uuid.uuid4()), 1)
    with pytest.raises(EvidenceError, match="EVIDENCE_ATTEMPT_MISMATCH"):
        make_evidence(attempt_id, workspace, events=[foreign])


def test_trace_events_must_be_strictly_ascending(attempt_id: str, workspace: Path) -> None:
    with pytest.raises(EvidenceError, match="EVIDENCE_SEQ_OUT_OF_ORDER"):
        AttemptEvidence(
            attempt_id=attempt_id,
            case_snapshot={},
            final_answer=None,
            stop_reason=None,
            iterations=0,
            trace_events=(make_event(attempt_id, 2), make_event(attempt_id, 1)),
            tool_calls=(),
            artifacts=(),
            workspace=workspace,
            trace_complete=True,
            execution_status="succeeded",
        )


# --------------------------------------------------------------------------- 工具调用派生


def test_tool_calls_group_started_with_terminal_event(
    attempt_id: str, workspace: Path
) -> None:
    events = [
        make_event(
            attempt_id,
            1,
            "tool.started",
            {"name": "test_lookup", "arguments": {"q": "a"}, "result": None, "is_error": None},
            call_id="call-1",
        ),
        make_event(
            attempt_id,
            2,
            "tool.completed",
            {"name": "test_lookup", "arguments": {"q": "a"}, "result": {"v": 1}, "is_error": False},
            call_id="call-1",
        ),
    ]
    calls = tool_calls_from_events(events)
    assert len(calls) == 1
    assert calls[0] == ToolCallEvidence(
        name="test_lookup",
        arguments={"q": "a"},
        result={"v": 1},
        is_error=False,
        seq=1,
        turn=0,
        iteration=None,
    )


def test_is_error_comes_from_payload_not_text(
    attempt_id: str, workspace: Path
) -> None:
    events = [
        make_event(
            attempt_id,
            1,
            "tool.failed",
            {"name": "test_unstable", "arguments": {}, "result": "Error: boom", "is_error": True},
            call_id="call-9",
        )
    ]
    call = tool_calls_from_events(events)[0]
    assert call.is_error is True
    assert call.result == "Error: boom"


def test_interrupted_tool_call_keeps_result_none(attempt_id: str, workspace: Path) -> None:
    events = [
        make_event(
            attempt_id,
            1,
            "tool.started",
            {"name": "read_file", "arguments": {"path": "a.txt"}},
            call_id="call-x",
        )
    ]
    call = tool_calls_from_events(events)[0]
    assert call.result is None
    assert call.is_error is False


def test_evidence_status_and_iteration_fields_pass_through(
    attempt_id: str, workspace: Path
) -> None:
    status: ExecutionStatus = "timed_out"
    evidence = build_attempt_evidence(
        attempt_id=attempt_id,
        case_snapshot={},
        workspace=workspace,
        trace_events=[make_event(attempt_id, 1)],
        execution_status=status,
        iterations=7,
        trace_complete=False,
    )
    assert evidence.execution_status == status
    assert evidence.iterations == 7
    assert evidence.trace_complete is False
    assert evidence.final_answer is None
