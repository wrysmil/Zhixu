"""证据覆盖度（契约 §2）。

覆盖度回答的是「这次运行到底采到了什么」，不是「跑得好不好」。

三条硬规则：

- 缺任何一类证据都必须落成 missing/partial 并写明原因，不允许补 0 假装已知；
- Trace 完整只来自密封与生命周期检查，不接受外部传进来的 override；
- 「要求轨迹的维度缺 Trace 必须不可判」——覆盖度是判分侧唯一的缺证入口。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

TRACE = "trace"
IDENTITY = "identity"
USAGE = "usage"
ARTIFACTS = "artifacts"

COMPLETE = "complete"
PARTIAL = "partial"
MISSING = "missing"
NOT_APPLICABLE = "not_applicable"

#: 执行生命周期事件：缺了它们就不知道"谁跑的、跑到哪一步"。
_LIFECYCLE_TYPES = frozenset(
    {"agent.started", "agent.completed", "agent.failed", "attempt.started",
     "attempt.completed", "attempt.failed"}
)
_TURN_TYPES = frozenset({"turn.started", "turn.completed"})
_DELEGATION_TYPES = frozenset({"team.dispatch", "member.started", "member.completed"})


def _payload(event: Any) -> Mapping[str, Any]:
    payload = getattr(event, "payload", None)
    return payload if isinstance(payload, Mapping) else {}


def _type_of(event: Any) -> str:
    return str(getattr(event, "type", ""))


def _trace_coverage(
    events: Sequence[Any], *, sealed: bool, complete: bool
) -> tuple[str, list[str]]:
    if not events:
        return MISSING, ["TRACE_MISSING"]
    reasons: list[str] = []
    if not sealed:
        reasons.append("TRACE_NOT_SEALED")
    if not complete:
        reasons.append("TRACE_INCOMPLETE")
    seen = {_type_of(event) for event in events}
    if not seen & _LIFECYCLE_TYPES:
        reasons.append("LIFECYCLE_EVENTS_MISSING")
    if reasons:
        return PARTIAL, reasons
    return COMPLETE, []


def _identity_coverage(events: Sequence[Any]) -> tuple[str, list[str]]:
    """执行身份：谁跑的、用什么被测对象。"""
    started = [event for event in events if _type_of(event) == "agent.started"]
    if not started:
        if not events:
            return MISSING, ["IDENTITY_MISSING"]
        return MISSING, ["AGENT_LIFECYCLE_MISSING"]
    payload = _payload(started[0])
    reasons: list[str] = []
    if not payload.get("execution_id"):
        reasons.append("EXECUTION_ID_MISSING")
    if payload.get("target_kind") == "skill" and not payload.get("skill_id"):
        reasons.append("SKILL_ID_MISSING")
    if payload.get("target_kind") == "agent" and not payload.get("agent_id"):
        reasons.append("AGENT_ID_MISSING")
    if reasons:
        return PARTIAL, reasons
    return COMPLETE, []


def _usage_coverage(usage: Mapping[str, Any] | None) -> tuple[str, list[str]]:
    if not usage:
        return MISSING, ["USAGE_MISSING"]
    source = str(usage.get("source") or "unavailable")
    coverage = str(usage.get("coverage") or "unavailable")
    if coverage == COMPLETE and source in ("reported", "mixed"):
        return COMPLETE, []
    if coverage == PARTIAL:
        return PARTIAL, ["USAGE_PARTIAL"]
    if source == "estimated":
        return PARTIAL, ["USAGE_ESTIMATED"]
    return MISSING, ["USAGE_MISSING"]


def _artifact_coverage(
    declared: Sequence[str], registered: Sequence[Mapping[str, Any]]
) -> tuple[str, list[str]]:
    if not declared:
        return NOT_APPLICABLE, []
    known = {
        str(item.get("relative_path") or item.get("name") or "")
        for item in registered
    }
    missing = [name for name in declared if name not in known]
    if not missing:
        return COMPLETE, []
    reasons = [f"ARTIFACT_MISSING:{name}" for name in missing]
    if len(missing) >= len(declared):
        return MISSING, reasons
    return PARTIAL, reasons


def _required_evidence_coverage(
    required: Sequence[str],
    *,
    trace_status: str,
    usage_status: str,
    artifact_status: str,
    events: Sequence[Any],
    final_answer: str | None,
) -> tuple[str, list[str]]:
    """用例要求的证据是否齐备；缺一项就写一条原因。"""
    reasons: list[str] = []
    types = {_type_of(event) for event in events}
    for item in required:
        if item == "final_answer":
            if not final_answer:
                reasons.append("REQUIRED_EVIDENCE_MISSING:final_answer")
        elif item == "turns":
            if not types & _TURN_TYPES:
                reasons.append("REQUIRED_EVIDENCE_MISSING:turns")
        elif item == "artifacts":
            if artifact_status in (MISSING, PARTIAL):
                reasons.append("REQUIRED_EVIDENCE_MISSING:artifacts")
        elif item == "trace":
            if trace_status != COMPLETE:
                reasons.append("REQUIRED_EVIDENCE_MISSING:trace")
        elif item == "usage":
            if usage_status != COMPLETE:
                reasons.append("REQUIRED_EVIDENCE_MISSING:usage")
        elif item == "delegation":
            if not types & _DELEGATION_TYPES:
                reasons.append("REQUIRED_EVIDENCE_MISSING:delegation")
    if reasons:
        return PARTIAL, reasons
    return COMPLETE, []


def compute_coverage(
    *,
    trace_events: Sequence[Any],
    trace_sealed: bool,
    trace_complete: bool,
    usage: Mapping[str, Any] | None,
    declared_artifacts: Sequence[str] = (),
    registered_artifacts: Sequence[Mapping[str, Any]] = (),
    required_evidence: Sequence[str] = (),
    final_answer: str | None = None,
) -> dict[str, Any]:
    """算出一次 attempt 的证据覆盖度。

    返回结构与契约 §2 一致：四个维度各取 complete/partial/missing，
    reasons 给出可读原因（供界面显示"为什么待复核"）。
    """
    events = list(trace_events)
    trace_status, trace_reasons = _trace_coverage(
        events, sealed=trace_sealed, complete=trace_complete
    )
    identity_status, identity_reasons = _identity_coverage(events)
    usage_status, usage_reasons = _usage_coverage(usage)
    artifact_status, artifact_reasons = _artifact_coverage(
        declared_artifacts, registered_artifacts
    )
    required_status, required_reasons = _required_evidence_coverage(
        required_evidence,
        trace_status=trace_status,
        usage_status=usage_status,
        artifact_status=artifact_status,
        events=events,
        final_answer=final_answer,
    )
    reasons = [
        *trace_reasons,
        *identity_reasons,
        *usage_reasons,
        *artifact_reasons,
        *required_reasons,
    ]
    return {
        TRACE: trace_status,
        IDENTITY: identity_status,
        USAGE: usage_status,
        ARTIFACTS: artifact_status,
        "required_evidence": required_status,
        "reasons": reasons,
    }


def summarize_coverage(coverages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """运行级覆盖度汇总：每个维度各有多少 attempt 是 complete/partial/missing。"""
    summary: dict[str, Any] = {"attempts": len(coverages)}
    for key in (TRACE, IDENTITY, USAGE, ARTIFACTS):
        counts = {COMPLETE: 0, PARTIAL: 0, MISSING: 0, NOT_APPLICABLE: 0}
        for item in coverages:
            value = str(item.get(key) or MISSING)
            counts[value] = counts.get(value, 0) + 1
        summary[key] = counts
    return summary


def evaluation_capability(
    *,
    manifest: Mapping[str, Any] | None,
    cases_have_criteria: bool,
) -> str:
    """评估能力等级：full / smoke / legacy。

    - full：v2 清单且用例带任务判据；
    - smoke：能跑但缺任务判据，只能显示规则通过率；
    - legacy：没有 v2 清单的老记录，不冒充新口径。
    """
    if not manifest or int(manifest.get("version") or 0) < 2:
        return "legacy"
    return "full" if cases_have_criteria else "smoke"


def coverage_is_sufficient(coverage: Mapping[str, Any] | None) -> bool:
    """缺证时不能判通过：Trace 或身份缺失一律视为证据不足。"""
    if not coverage:
        return False
    return (
        str(coverage.get(TRACE)) == COMPLETE
        and str(coverage.get(IDENTITY)) == COMPLETE
    )
