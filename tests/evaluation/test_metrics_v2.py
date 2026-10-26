"""T08/T09 指标口径与重复可靠性。

红线：

- 任务成功率 ≠ 综合合格率：两者分母与分子都不同，不能互相冒充；
- 缺任务结果的旧记录只影响覆盖度，不补 0 假装失败；
- 重复试验按 case 聚合，"r 次至少一次/全部成功"只对 r 相同且全部可判的 case 报。
"""

from __future__ import annotations

from typing import Any

from nanobot.evaluation.reports import (
    aggregate_qualification,
    aggregate_reliability,
    aggregate_task_outcomes,
)


def _attempt(
    case_id: str,
    *,
    verdict: str,
    task_outcome: str | None = None,
    qualification: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "case_id": case_id,
        "verdict": verdict,
        "execution_status": "succeeded",
    }
    if task_outcome is not None or qualification is not None:
        payload["evaluation"] = {
            "task_outcome": task_outcome,
            "qualification": qualification,
        }
    return payload


def test_task_success_rate_is_not_qualification_rate() -> None:
    attempts = [
        _attempt("a", verdict="passed", task_outcome="completed", qualification="qualified"),
        _attempt("b", verdict="failed", task_outcome="completed", qualification="disqualified"),
        _attempt("c", verdict="failed", task_outcome="partial", qualification="disqualified"),
        _attempt("d", verdict="failed", task_outcome="failed", qualification="disqualified"),
        _attempt("e", verdict="review_required", qualification="review_required"),
        _attempt("f", verdict="not_evaluated"),
    ]
    outcomes = aggregate_task_outcomes(attempts, 6)
    qualification = aggregate_qualification(attempts, 6)

    assert outcomes["task_success_rate"] == 2 / 6
    assert outcomes["partial_completion_rate"] == 1 / 6
    assert outcomes["task_assessment_coverage"] == 4 / 6
    assert qualification["qualification_rate"] == 1 / 6
    assert qualification["disqualified"] == 3


def test_legacy_attempts_only_lower_coverage() -> None:
    attempts = [_attempt("a", verdict="passed")]
    outcomes = aggregate_task_outcomes(attempts, 1)
    assert outcomes["task_success_rate"] is None
    assert outcomes["unknown"] == 1
    assert outcomes["task_assessment_coverage"] == 0.0


def test_reliability_groups_by_repetition_count() -> None:
    attempts = [
        _attempt("case-a", verdict="passed"),
        _attempt("case-a", verdict="failed"),
        _attempt("case-b", verdict="passed"),
        _attempt("case-b", verdict="passed"),
    ]
    summary = aggregate_reliability(attempts)
    group = summary["groups"][0]
    assert group["repetitions"] == 2
    assert group["cases"] == 2
    assert group["at_least_once_cases"] == 2
    assert group["all_success_cases"] == 1
    assert group["at_least_once_rate"] == 1.0
    assert group["all_success_rate"] == 0.5


def test_reliability_ignores_unjudged_trials_in_all_success() -> None:
    attempts = [
        _attempt("case-a", verdict="passed"),
        _attempt("case-a", verdict="not_evaluated"),
    ]
    group = aggregate_reliability(attempts)["groups"][0]
    assert group["at_least_once_cases"] == 1
    # 存在未判定试验时不能说"全部成功"。
    assert group["all_success_cases"] == 0
