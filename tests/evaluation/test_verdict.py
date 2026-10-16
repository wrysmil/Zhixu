"""verdict 合并的红灯测试（WU-04 / T07 第 4 项）。

plan T07 给出的优先级是硬口径，本文件按「全部组合」覆盖：8 个 execution_status ×
7 组必过规则结论 × 6 个 judge_state × 2 个 Trace 完整性。断言写成与实现无关的不变量
（失败优先、未形成结果、复核条件、通过条件），而不是把实现里的 if 链抄一遍。
"""

from __future__ import annotations

from itertools import product
from typing import Any

import pytest

from nanobot.evaluation.graders.verdict import derive_verdict, judge_state_of
from nanobot.evaluation.schemas import JudgeResult

EXECUTION_STATUSES = [
    "queued",
    "running",
    "succeeded",
    "failed",
    "timed_out",
    "cancelled",
    "interrupted",
    "setup_error",
]
JUDGE_STATES = ["disabled", "passed", "failed", "missing", "error", "uncalibrated"]
RULE_SETS: list[list[Any]] = [
    [],
    [True],
    [False],
    [None],
    [True, False],
    [True, None],
    [True, True],
]
TRACE_FLAGS = [True, False]


def _call(status: str, rules: list[Any], state: str, trace: bool) -> str:
    return derive_verdict(status, list(rules), state, trace)


# ------------------------------------------------------------------ 全组合不变量


def test_execution_failure_and_timeout_always_yield_failed() -> None:
    """第一优先级：执行侧明确失败时，任何规则/Judge/Trace 组合都不得翻案。"""
    for status, rules, state, trace in product(
        ["failed", "timed_out"], RULE_SETS, JUDGE_STATES, TRACE_FLAGS
    ):
        assert _call(status, rules, state, trace) == "failed", (status, rules, state, trace)


def test_non_succeeded_execution_is_not_evaluated() -> None:
    """第二优先级：queued/running/cancelled/interrupted/setup_error 未形成可判结果。"""
    for status, rules, state, trace in product(
        ["queued", "running", "cancelled", "interrupted", "setup_error"],
        RULE_SETS,
        JUDGE_STATES,
        TRACE_FLAGS,
    ):
        assert _call(status, rules, state, trace) == "not_evaluated", (status, rules, state, trace)


def test_succeeded_with_false_required_rule_or_failed_judge_is_failed() -> None:
    for rules, state, trace in product(RULE_SETS, JUDGE_STATES, TRACE_FLAGS):
        expected_failed = False in rules or state == "failed"
        result = _call("succeeded", rules, state, trace)
        if expected_failed:
            assert result == "failed", (rules, state, trace)


def test_succeeded_review_conditions_cover_every_combination() -> None:
    """第三优先级：Trace 不完整 / 有 None / judge 缺失、异常、未校准 → review_required。

    更高优先级的「必过 False 或 judge 失败」先判 failed，不参与这条不变量。
    """
    for rules, state, trace in product(RULE_SETS, JUDGE_STATES, TRACE_FLAGS):
        result = _call("succeeded", rules, state, trace)
        has_failure = False in rules or state == "failed"
        review_expected = (
            not has_failure
            and (not trace or None in rules or state in {"missing", "error", "uncalibrated"})
        )
        if review_expected:
            assert result == "review_required", (rules, state, trace)


def test_succeeded_all_green_is_passed() -> None:
    for rules, state, trace in product(RULE_SETS, JUDGE_STATES, TRACE_FLAGS):
        result = _call("succeeded", rules, state, trace)
        if trace and state in {"disabled", "passed"} and all(item is True for item in rules):
            assert result == "passed", (rules, state, trace)


def test_every_combination_produces_a_contract_verdict() -> None:
    for status, rules, state, trace in product(
        EXECUTION_STATUSES, RULE_SETS, JUDGE_STATES, TRACE_FLAGS
    ):
        assert _call(status, rules, state, trace) in {
            "passed",
            "failed",
            "review_required",
            "not_evaluated",
        }


# ------------------------------------------------------------------- 关键单例口径


def test_uncalibrated_required_judge_never_gates_as_passed() -> None:
    """spec 验收 10：未校准结果不得当作通过，只能进入人工复核。"""
    assert derive_verdict("succeeded", [True], "uncalibrated", True) == "review_required"


def test_incomplete_trace_cannot_be_passed() -> None:
    assert derive_verdict("succeeded", [True], "passed", False) == "review_required"


def test_unreadable_rule_result_is_review_not_failure() -> None:
    assert derive_verdict("succeeded", [None], "disabled", True) == "review_required"


def test_optional_judge_failure_does_not_change_rule_conclusion() -> None:
    """可选 Judge 的异常在传入前归一为 disabled，因此不会把 passed 变 failed。"""
    assert derive_verdict("succeeded", [True], "disabled", True) == "passed"


def test_rejects_unknown_inputs_at_the_boundary() -> None:
    with pytest.raises(ValueError, match="JUDGE_STATE"):
        derive_verdict("succeeded", [True], "completed", True)
    with pytest.raises(ValueError, match="EXECUTION_STATUS"):
        derive_verdict("succeeding", [True], "disabled", True)


# ------------------------------------------------------------- judge_state 归一化


def _rubric(required: bool) -> dict[str, Any]:
    return {
        "version": "general-v1",
        "required": required,
        "dimensions": [
            {
                "id": "task_completion",
                "name": "任务完成",
                "anchors": ["a0", "a1", "a2", "a3", "a4"],
                "pass_score": 3,
            },
            {
                "id": "correctness",
                "name": "正确性",
                "anchors": ["a0", "a1", "a2", "a3", "a4"],
                "pass_score": 4,
            },
        ],
    }


def _judge(scores: dict[str, int] | None, **overrides: Any) -> JudgeResult | None:
    if scores is None:
        return None
    payload: dict[str, Any] = {
        "status": "completed",
        "model": "judge-model",
        "version": "judge-v1",
        "calibrated": True,
        "error": None,
        "dimensions": [
            {"id": key, "score": value, "reason": "理由", "evidence_refs": []}
            for key, value in scores.items()
        ],
    }
    payload.update(overrides)
    return JudgeResult.model_validate(payload)


def test_no_rubric_means_judge_disabled() -> None:
    assert judge_state_of(None, None, None) == "disabled"


def test_required_judge_missing_is_missing() -> None:
    assert judge_state_of(_rubric(True), None, None) == "missing"


def test_required_judge_error_state() -> None:
    judge = _judge({}, status="judge_error", error="TIMEOUT", calibrated=False)
    assert judge_state_of(_rubric(True), judge, None) == "error"


def test_required_judge_without_calibration_is_uncalibrated() -> None:
    judge = _judge({"task_completion": 4, "correctness": 4}, calibrated=False)
    assert judge_state_of(_rubric(True), judge, None) == "uncalibrated"


def test_required_judge_below_any_pass_score_is_failed() -> None:
    judge = _judge({"task_completion": 4, "correctness": 3})
    assert judge_state_of(_rubric(True), judge, None) == "failed"


def test_required_judge_meeting_every_pass_score_is_passed() -> None:
    judge = _judge({"task_completion": 3, "correctness": 4})
    assert judge_state_of(_rubric(True), judge, None) == "passed"


def test_optional_judge_error_is_disabled_but_keeps_original_error() -> None:
    judge = _judge({}, status="judge_error", error="PROVIDER_502", calibrated=False)
    state = judge_state_of(_rubric(False), judge, None)
    assert state == "disabled"
    assert judge is not None and judge.error == "PROVIDER_502"


def test_optional_uncalibrated_score_still_requires_review() -> None:
    """开放式任务用了未校准评分：不门禁为通过，按 spec 验收 10 进入复核。"""
    judge = _judge({"task_completion": 4, "correctness": 4}, calibrated=False)
    assert judge_state_of(_rubric(False), judge, None) == "uncalibrated"


def test_incomplete_dimension_coverage_is_error() -> None:
    judge = _judge({"task_completion": 4})
    assert judge_state_of(_rubric(True), judge, None) == "error"


def test_calibration_lookup_can_override_uncalibrated_judge() -> None:
    """JudgeResult.calibrated 为 False 时，只有当前指纹的达标校准记录才能放行。"""
    judge = _judge({"task_completion": 4, "correctness": 4}, calibrated=False)
    assert judge_state_of(_rubric(True), judge, {"passed": True}) == "passed"
    assert judge_state_of(_rubric(True), judge, {"passed": False}) == "uncalibrated"
    assert judge_state_of(_rubric(True), judge, {"passed": None}) == "uncalibrated"
