"""T03 证据覆盖度与执行身份。

三条底线：

- 缺证不能通过：Trace/身份缺失时覆盖度必须显式为 missing/partial；
- 缺 usage 与缺 Trace 分开标记，不能混成"缺证据"；
- 身份必须能回答"谁跑的、跑的是哪个被测对象"。
"""

from __future__ import annotations

import uuid
from typing import Any

from nanobot.evaluation.coverage import (
    compute_coverage,
    evaluation_capability,
    summarize_coverage,
)
from nanobot.evaluation.schemas import TraceEvent
from nanobot.evaluation.worker import resolve_target_identity

ATTEMPT_ID = str(uuid.uuid4())


def event(seq: int, type_: str, payload: dict[str, Any] | None = None) -> TraceEvent:
    return TraceEvent(
        attempt_id=ATTEMPT_ID,
        seq=seq,
        event_id=uuid.uuid4(),
        type=type_,  # type: ignore[arg-type]
        ts="2026-10-26T00:00:00+00:00",
        elapsed_ms=0,
        payload=payload or {},
    )


def _lifecycle_events() -> list[TraceEvent]:
    return [
        event(1, "attempt.started", {"execution_id": "exec-1"}),
        event(
            2,
            "agent.started",
            {"execution_id": "exec-1", "target_kind": "agent", "agent_id": "writer"},
        ),
        event(3, "agent.completed", {"answer_sha256": "0" * 64}),
        event(4, "attempt.completed", {"final_answer_ref": "final:x:3"}),
    ]


def test_complete_trace_and_identity_are_reported_as_complete() -> None:
    coverage = compute_coverage(
        trace_events=_lifecycle_events(),
        trace_sealed=True,
        trace_complete=True,
        usage={"source": "reported", "coverage": "complete"},
        declared_artifacts=[],
        registered_artifacts=[],
        final_answer="一句话结论",
    )
    assert coverage["trace"] == "complete"
    assert coverage["identity"] == "complete"
    assert coverage["usage"] == "complete"
    assert coverage["artifacts"] == "not_applicable"
    assert coverage["reasons"] == []


def test_missing_trace_is_not_silently_passed() -> None:
    coverage = compute_coverage(
        trace_events=[],
        trace_sealed=False,
        trace_complete=False,
        usage=None,
    )
    assert coverage["trace"] == "missing"
    assert coverage["identity"] == "missing"
    assert coverage["usage"] == "missing"
    assert "TRACE_MISSING" in coverage["reasons"]


def test_unsealed_trace_is_partial_not_complete() -> None:
    coverage = compute_coverage(
        trace_events=_lifecycle_events(),
        trace_sealed=False,
        trace_complete=False,
        usage={"source": "reported", "coverage": "complete"},
    )
    assert coverage["trace"] == "partial"
    assert "TRACE_NOT_SEALED" in coverage["reasons"]
    assert "TRACE_INCOMPLETE" in coverage["reasons"]


def test_missing_usage_is_separate_from_missing_trace() -> None:
    coverage = compute_coverage(
        trace_events=_lifecycle_events(),
        trace_sealed=True,
        trace_complete=True,
        usage=None,
    )
    assert coverage["trace"] == "complete"
    assert coverage["usage"] == "missing"
    assert "USAGE_MISSING" in coverage["reasons"]
    assert "TRACE_MISSING" not in coverage["reasons"]


def test_missing_artifact_names_the_missing_file() -> None:
    coverage = compute_coverage(
        trace_events=_lifecycle_events(),
        trace_sealed=True,
        trace_complete=True,
        usage={"source": "reported", "coverage": "complete"},
        declared_artifacts=["output.txt", "summary.json"],
        registered_artifacts=[{"relative_path": "output.txt"}],
    )
    assert coverage["artifacts"] == "partial"
    assert "ARTIFACT_MISSING:summary.json" in coverage["reasons"]


def test_required_evidence_missing_is_reported() -> None:
    coverage = compute_coverage(
        trace_events=_lifecycle_events(),
        trace_sealed=True,
        trace_complete=True,
        usage={"source": "reported", "coverage": "complete"},
        required_evidence=["final_answer", "delegation"],
        final_answer=None,
    )
    assert coverage["required_evidence"] == "partial"
    assert "REQUIRED_EVIDENCE_MISSING:final_answer" in coverage["reasons"]
    assert "REQUIRED_EVIDENCE_MISSING:delegation" in coverage["reasons"]


def test_coverage_summary_counts_each_dimension() -> None:
    coverages = [
        {"trace": "complete", "identity": "complete", "usage": "complete",
         "artifacts": "not_applicable"},
        {"trace": "partial", "identity": "complete", "usage": "missing",
         "artifacts": "missing"},
    ]
    summary = summarize_coverage(coverages)
    assert summary["attempts"] == 2
    assert summary["trace"] == {"complete": 1, "partial": 1, "missing": 0,
                                "not_applicable": 0}
    assert summary["usage"]["missing"] == 1


def test_evaluation_capability_distinguishes_full_smoke_legacy() -> None:
    assert evaluation_capability(manifest=None, cases_have_criteria=True) == "legacy"
    assert evaluation_capability(manifest={"version": 2}, cases_have_criteria=False) == "smoke"
    assert evaluation_capability(manifest={"version": 2}, cases_have_criteria=True) == "full"


def test_agent_target_identity_comes_from_frozen_profile() -> None:
    identity = resolve_target_identity(
        {
            "kind": "agent",
            "id": "writer",
            "agent": {"profile": {"id": "writer", "name": "写手"}},
        }
    )
    assert identity.agent_id == "writer"
    assert identity.agent_name == "写手"
    assert identity.skill_id is None


def test_skill_target_does_not_impersonate_a_user_agent() -> None:
    identity = resolve_target_identity(
        {"kind": "skill", "id": "summarizer", "skill": {"name": "summarizer"}}
    )
    assert identity.skill_id == "summarizer"
    assert identity.agent_id is None
    assert identity.agent_name is None
