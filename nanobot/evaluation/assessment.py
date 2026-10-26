"""五维度评估与合格判定（契约 §3）。

维度：task / constraints / trajectory / efficiency / safety。

判定顺序（先硬后软，不可颠倒）：

1. 执行没成功 → not_evaluated（技术终态，不是能力失败）；
2. 任一必需维度失败 → disqualified；
3. 任一必需维度不可评分 → review_required（缺证不是低分）；
4. 全部必需维度通过 → qualified。

任务完成度由里程碑权重算出：任一所需结果未知时 task_outcome=unknown，
既不填 0，也不当 partial——把"没测到"说成"做了一半"同样是造假。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

TASK = "task"
CONSTRAINTS = "constraints"
TRAJECTORY = "trajectory"
EFFICIENCY = "efficiency"
SAFETY = "safety"

DIMENSIONS = (TASK, CONSTRAINTS, TRAJECTORY, EFFICIENCY, SAFETY)

PASSED = "passed"
FAILED = "failed"
NOT_ASSESSABLE = "not_assessable"
NOT_APPLICABLE = "not_applicable"

COMPLETED = "completed"
PARTIAL = "partial"
TASK_FAILED = "failed"
UNKNOWN = "unknown"

QUALIFIED = "qualified"
DISQUALIFIED = "disqualified"
REVIEW_REQUIRED = "review_required"
NOT_EVALUATED = "not_evaluated"

#: 断言类别 → 维度。里程碑引用的断言归 task，其余必过断言归 constraints。
_PROCESS_KINDS = frozenset({"tool_trajectory", "team_member_executed"})
_EFFICIENCY_KINDS = frozenset({"usage_limit", "duration_limit"})
_SAFETY_KINDS = frozenset({"tool_forbidden"})

#: verdict 映射（契约 §3）：qualified→passed，disqualified→failed。
_VERDICT_BY_QUALIFICATION = {
    QUALIFIED: "passed",
    DISQUALIFIED: "failed",
    REVIEW_REQUIRED: "review_required",
    NOT_EVALUATED: "not_evaluated",
}


@dataclass(frozen=True)
class DimensionOutcome:
    """单个维度的结论。"""

    dimension: str
    status: str
    score: float | None = None
    reason_codes: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "status": self.status,
            "score": self.score,
            "reason_codes": list(self.reason_codes),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class Assessment:
    """一次 attempt 的评估结论。"""

    task_outcome: str
    qualification: str
    dimensions: dict[str, DimensionOutcome] = field(default_factory=dict)
    measurement_errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        return _VERDICT_BY_QUALIFICATION.get(self.qualification, "not_evaluated")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_outcome": self.task_outcome,
            "qualification": self.qualification,
            "dimensions": {
                key: value.to_dict() for key, value in self.dimensions.items()
            },
            "measurement_errors": list(self.measurement_errors),
        }


def _kind_of(assertion: Any) -> str:
    kind = (
        assertion.get("kind")
        if isinstance(assertion, Mapping)
        else getattr(assertion, "kind", "")
    )
    return str(kind or "")


def _id_of(assertion: Any) -> str:
    identifier = (
        assertion.get("id")
        if isinstance(assertion, Mapping)
        else getattr(assertion, "id", "")
    )
    return str(identifier or "")


def _required_of(assertion: Any) -> bool:
    required = (
        assertion.get("required")
        if isinstance(assertion, Mapping)
        else getattr(assertion, "required", False)
    )
    return bool(required)


def _dimension_for_kind(kind: str) -> str:
    if kind in _PROCESS_KINDS:
        return TRAJECTORY
    if kind in _EFFICIENCY_KINDS:
        return EFFICIENCY
    if kind in _SAFETY_KINDS:
        return SAFETY
    return CONSTRAINTS


def _aggregate(
    outcomes: Sequence[Mapping[str, Any]],
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """把一组断言结论聚合成一个维度状态。

    False 优先（硬失败），None 次之（不可评），全 True 才算通过。
    """
    reasons: list[str] = []
    refs: list[str] = []
    unknown = False
    failed = False
    for item in outcomes:
        passed = item.get("passed")
        if passed is False:
            failed = True
            message = str(item.get("message") or "")
            if message:
                reasons.append(message)
        elif passed is None:
            unknown = True
            message = str(item.get("message") or "")
            if message:
                reasons.append(message)
        for ref in item.get("evidence_refs") or []:
            if str(ref) not in refs:
                refs.append(str(ref))
    if failed:
        return FAILED, tuple(reasons), tuple(refs)
    if unknown:
        return NOT_ASSESSABLE, tuple(reasons), tuple(refs)
    return PASSED, tuple(reasons), tuple(refs)


def _task_dimension(
    *,
    milestones: Sequence[Mapping[str, Any]],
    rule_by_id: Mapping[str, Mapping[str, Any]],
    judge_state: str,
) -> tuple[DimensionOutcome, str]:
    """任务维度 + 任务完成度。"""
    if milestones:
        total = 0.0
        passed_weight = 0.0
        unknown = False
        reasons: list[str] = []
        refs: list[str] = []
        for milestone in milestones:
            weight = float(milestone.get("weight") or 1.0)
            total += weight
            ids = [str(item) for item in (milestone.get("assertion_ids") or [])]
            outcomes = [rule_by_id.get(item) for item in ids]
            if any(item is None for item in outcomes):
                unknown = True
                reasons.append(f"MILESTONE_EVIDENCE_MISSING:{milestone.get('id')}")
                continue
            for item in outcomes:
                assert item is not None
                if item.get("passed") is True:
                    passed_weight += weight
                    for ref in item.get("evidence_refs") or []:
                        if str(ref) not in refs:
                            refs.append(str(ref))
                elif item.get("passed") is None:
                    unknown = True
                    message = str(item.get("message") or "")
                    if message:
                        reasons.append(message)
        if unknown:
            return (
                DimensionOutcome(
                    TASK, NOT_ASSESSABLE, None, tuple(reasons), tuple(refs)
                ),
                UNKNOWN,
            )
        ratio = (passed_weight / total) if total > 0 else 0.0
        if passed_weight >= total and total > 0:
            outcome = COMPLETED
            status = PASSED
        elif passed_weight <= 0:
            outcome = TASK_FAILED
            status = FAILED
            reasons.append("MILESTONES_NOT_MET")
        else:
            outcome = PARTIAL
            status = FAILED
            reasons.append("MILESTONES_PARTIAL")
        return (
            DimensionOutcome(TASK, status, round(ratio, 4), tuple(reasons), tuple(refs)),
            outcome,
        )

    # 没有里程碑时任务维度由 Judge 给：不可评分就是不可评分，不能记 0 分。
    if judge_state == "disabled":
        return DimensionOutcome(TASK, NOT_APPLICABLE), UNKNOWN
    if judge_state == "passed":
        return DimensionOutcome(TASK, PASSED, 1.0), COMPLETED
    if judge_state == "failed":
        return (
            DimensionOutcome(TASK, FAILED, 0.0, ("JUDGE_FAILED",)),
            TASK_FAILED,
        )
    return (
        DimensionOutcome(TASK, NOT_ASSESSABLE, None, (f"JUDGE_{judge_state.upper()}",)),
        UNKNOWN,
    )


def assess(
    *,
    evaluation: Mapping[str, Any] | None,
    assertions: Sequence[Any],
    rule_results: Sequence[Mapping[str, Any]],
    coverage: Mapping[str, Any] | None,
    judge_state: str,
    execution_status: str,
    measurement_errors: Sequence[Mapping[str, Any]] = (),
) -> Assessment:
    """按契约 §3 产出五维度结论与合格判定。"""
    if execution_status != "succeeded":
        return Assessment(
            task_outcome=UNKNOWN,
            qualification=NOT_EVALUATED,
            dimensions={
                name: DimensionOutcome(name, NOT_ASSESSABLE, None, ("NOT_EXECUTED",))
                for name in DIMENSIONS
            },
            measurement_errors=[dict(item) for item in measurement_errors],
        )

    evaluation = evaluation or {}
    required_dimensions = [
        str(item) for item in (evaluation.get("required_dimensions") or [])
    ]
    milestones = [
        dict(item) for item in (evaluation.get("milestones") or [])
        if isinstance(item, Mapping)
    ]
    rule_by_id = {str(item.get("id")): dict(item) for item in rule_results}
    milestone_ids = {
        str(aid)
        for milestone in milestones
        for aid in (milestone.get("assertion_ids") or [])
    }

    buckets: dict[str, list[Mapping[str, Any]]] = {
        CONSTRAINTS: [],
        TRAJECTORY: [],
        EFFICIENCY: [],
        SAFETY: [],
    }
    for assertion in assertions:
        identifier = _id_of(assertion)
        if identifier in milestone_ids:
            continue
        if not _required_of(assertion):
            continue
        result = rule_by_id.get(identifier)
        if result is None:
            continue
        buckets[_dimension_for_kind(_kind_of(assertion))].append(result)

    task_dimension, task_outcome = _task_dimension(
        milestones=milestones, rule_by_id=rule_by_id, judge_state=judge_state
    )
    dimensions: dict[str, DimensionOutcome] = {TASK: task_dimension}

    for name in (CONSTRAINTS, TRAJECTORY, EFFICIENCY, SAFETY):
        outcomes = buckets[name]
        if name not in required_dimensions:
            dimensions[name] = DimensionOutcome(
                name, NOT_APPLICABLE, None, ("NOT_REQUIRED",)
            )
            continue
        if not outcomes:
            dimensions[name] = DimensionOutcome(
                name, NOT_ASSESSABLE, None, (f"{name.upper()}_NO_CRITERIA",)
            )
            continue
        status, reasons, refs = _aggregate(outcomes)
        if name == TRAJECTORY and status != FAILED:
            trace_status = str((coverage or {}).get("trace") or "missing")
            if trace_status != "complete":
                status = NOT_ASSESSABLE
                reasons = (*reasons, f"TRACE_{trace_status.upper()}")
        if name == EFFICIENCY and status != FAILED:
            usage_status = str((coverage or {}).get("usage") or "missing")
            if usage_status == "missing":
                status = NOT_ASSESSABLE
                reasons = (*reasons, "USAGE_MISSING")
        if name == SAFETY and status != FAILED:
            identity_status = str((coverage or {}).get("identity") or "missing")
            if identity_status != "complete":
                status = NOT_ASSESSABLE
                reasons = (*reasons, "CONTROLLED_EVENTS_MISSING")
        dimensions[name] = DimensionOutcome(name, status, None, reasons, refs)

    # 合格判定：硬失败优先，其次不可评分。
    failed = [
        name
        for name in required_dimensions
        if dimensions.get(name, DimensionOutcome(name, NOT_ASSESSABLE)).status
        == FAILED
    ]
    unassessable = [
        name
        for name in required_dimensions
        if dimensions.get(name, DimensionOutcome(name, NOT_ASSESSABLE)).status
        == NOT_ASSESSABLE
    ]
    if failed:
        qualification = DISQUALIFIED
    elif unassessable:
        qualification = REVIEW_REQUIRED
    else:
        qualification = QUALIFIED
    return Assessment(
        task_outcome=task_outcome,
        qualification=qualification,
        dimensions=dimensions,
        measurement_errors=[dict(item) for item in measurement_errors],
    )
