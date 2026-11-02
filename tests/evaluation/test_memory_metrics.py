"""记忆专项指标口径（契约 §4 / §8.4）与通用 Agent 口径补充。

红线：

- 指标 ID 与契约逐字一致（含 §8.4 新增主指标 ``qa_accuracy_scenario_macro``）；
- 字段缺失不是 0：算不出来就是 ``not_assessable`` + null 值；
- F1 用计数直接算（``2TP/(2TP+FP+FN)``），不因 P/R 为 null 把漏记藏起来；
- 旧 ``task_success_rate``（completed/planned）分母不变；新口径用新字段名；
- 工具决策/参数准确率不能拿工具执行成功率顶替。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from nanobot.evaluation.memory.metrics import (
    METRIC_IDS,
    METRIC_SPECS,
    build_metric,
    coverage_of,
    extraction_prf,
    latency_metric,
    memory_lift,
    ratio_metric,
    total_metric,
)
from nanobot.evaluation.reports import (
    aggregate_judged_task_outcomes,
    aggregate_task_outcomes,
    aggregate_tool_decision_accuracy,
)

#: 契约 §4 的 21 个指标 ID + §8.4 新增主指标。
CONTRACT_METRIC_IDS = (
    "extraction_precision",
    "extraction_recall",
    "extraction_f1",
    "unsupported_memory_rate",
    "evidence_recall_at_k",
    "retrieval_precision_returned_at_k",
    "mrr",
    "injection_recall",
    "qa_accuracy",
    "preference_compliance",
    "update_accuracy",
    "stale_use_rate",
    "abstention_accuracy",
    "false_abstention_rate",
    "cross_session_accuracy",
    "restart_accuracy",
    "forget_behavior_success",
    "forget_state_success",
    "scope_leak_rate",
    "memory_lift",
    "qa_accuracy_scenario_macro",
)


# --------------------------------------------------------------------------- F1 口径


def test_missing_all_expected_facts_is_zero_recall() -> None:
    """plan §7 给死的口径：一条都没抽到 → 召回 0、F1 0，precision 分母为空 → null。"""
    assert extraction_prf(0, 0, 2) == (None, 0, 0)


def test_extraction_prf_all_zero_denominators_are_null() -> None:
    """三个计数全零 → P/R/F1 全是 null（不是 0）。"""
    assert extraction_prf(0, 0, 0) == (None, None, None)


def test_extraction_prf_hand_computed_counts() -> None:
    """审查文档 §5.1 的例子：TP=8、FP=4、FN=2 → P=8/12、R=8/10、F1=16/22。"""
    precision, recall, f1 = extraction_prf(8, 4, 2)
    assert precision == pytest.approx(8 / 12)
    assert recall == pytest.approx(8 / 10)
    assert f1 == pytest.approx(16 / 22)


def test_extraction_prf_uses_counts_not_averaged_p_and_r() -> None:
    """F1 直接由计数算：P/R 一旦有一个被截断，F1 不能跟着漂。"""
    assert extraction_prf(3, 0, 3)[2] == pytest.approx(6 / 9)


# --------------------------------------------------------------------------- 指标 ID


def test_contract_metric_ids_are_registered() -> None:
    """契约 §4/§8.4 点名的指标一个都不能少，也不能改名。"""
    missing = [item for item in CONTRACT_METRIC_IDS if item not in METRIC_SPECS]
    assert missing == []
    assert "qa_accuracy_scenario_macro" in METRIC_IDS


def test_metric_specs_declare_unit_and_direction() -> None:
    """每个指标都要说清单位与方向（方向错会把"越低越好"读反）。"""
    for metric_id, spec in METRIC_SPECS.items():
        assert spec.id == metric_id
        assert spec.unit in {
            "ratio",
            "count",
            "ms",
            "token",
            "bytes",
            "currency",
        }
        assert spec.direction in {"higher_is_better", "lower_is_better"}
        assert spec.description.strip()


def test_primary_metric_is_scenario_macro() -> None:
    """主指标是 ``qa_accuracy_scenario_macro``，不是 probe micro。"""
    spec = METRIC_SPECS["qa_accuracy_scenario_macro"]
    assert spec.scope == "dataset"
    assert spec.direction == "higher_is_better"


# --------------------------------------------------------------------------- 指标构建


def test_ratio_metric_with_unknown_denominator_is_not_assessable() -> None:
    """分母缺失不是 0：返回 null + not_assessable。"""
    metric = ratio_metric(
        "extraction_precision",
        numerator=3,
        denominator=None,
        grading_revision=1,
        reasons=["DENOMINATOR_UNKNOWN"],
    )
    assert metric.status == "not_assessable"
    assert metric.value is None
    assert metric.numerator == 3
    assert metric.denominator is None


def test_ratio_metric_with_empty_denominator_is_not_applicable() -> None:
    """分母为 0 是"无可判定项"，不是失败，也不是 0%。"""
    metric = ratio_metric(
        "extraction_precision", numerator=0, denominator=0, grading_revision=1
    )
    assert metric.status == "not_applicable"
    assert metric.value is None
    assert "EMPTY_DENOMINATOR" in metric.reasons


def test_ratio_metric_carries_value_numerator_denominator_and_version() -> None:
    metric = ratio_metric(
        "extraction_recall", numerator=8, denominator=10, grading_revision=7
    )
    assert metric.status == "scored"
    assert metric.value == pytest.approx(0.8)
    assert metric.numerator == 8
    assert metric.denominator == 10
    assert metric.version == 1
    assert metric.grading_revision == 7


def test_scored_metric_without_value_is_rejected() -> None:
    """状态与值必须自洽：scored 不允许 null 值。"""
    with pytest.raises(ValidationError):
        build_metric(
            "qa_accuracy",
            value=None,
            status="scored",
            scope="probe",
            grading_revision=1,
        )


def test_unknown_metric_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="METRIC_UNKNOWN"):
        build_metric(
            "not_a_contract_metric", value=1.0, status="scored", scope="probe", grading_revision=1
        )


def test_latency_metric_ignores_unknown_samples_but_reports_coverage() -> None:
    """未知时延不补 0：只统计已知样本，覆盖度如实下降。"""
    metric = latency_metric(
        "write_latency_p50_ms", samples=[100, None, 200, 300], planned=4, grading_revision=1
    )
    assert metric.status == "scored"
    assert metric.value == pytest.approx(200.0)  # 已知样本 [100,200,300] 的中位数
    assert metric.numerator == pytest.approx(600.0)
    assert metric.denominator == pytest.approx(3.0)
    assert metric.coverage.scored == 3
    assert metric.coverage.planned == 4
    assert "UNKNOWN_SAMPLES_EXCLUDED" in metric.reasons


def test_latency_metric_without_any_known_sample_is_not_assessable() -> None:
    metric = latency_metric(
        "write_latency_p50_ms", samples=[None, None], planned=2, grading_revision=1
    )
    assert metric.status == "not_assessable"
    assert metric.value is None
    assert metric.coverage.scored == 0
    assert metric.coverage.planned == 2


def test_total_metric_keeps_unknown_cost_null() -> None:
    """未知费用为 null，不填 0。"""
    metric = total_metric("estimated_cost", value=None, grading_revision=1)
    assert metric.status == "not_assessable"
    assert metric.value is None
    assert metric.unit == "currency"


def test_coverage_of_reports_reasons() -> None:
    coverage = coverage_of(2, 5, ["PROBE_NOT_EXECUTED"])
    assert coverage.scored == 2
    assert coverage.planned == 5
    assert coverage.reasons == ["PROBE_NOT_EXECUTED"]
    assert coverage.complete is False


# --------------------------------------------------------------------------- Memory Lift


def test_memory_lift_is_paired_difference_in_ratio() -> None:
    """配对差值：候选场景分减基线场景分，按配对数等权平均。"""
    metric = memory_lift([(0.75, 0.85), (0.5, 0.5)], grading_revision=1)
    assert metric.status == "scored"
    assert metric.value == pytest.approx(0.05)  # (0.10 + 0.00) / 2
    assert metric.denominator == pytest.approx(2.0)


def test_memory_lift_without_pairs_is_not_assessable() -> None:
    metric = memory_lift([], grading_revision=1)
    assert metric.status == "not_assessable"
    assert metric.value is None


# --------------------------------------------------------------------------- 通用层口径


def _attempt(
    *,
    task_outcome: str | None = None,
    qualification: str | None = None,
    annotations: list[dict[str, Any]] | None = None,
    snapshot_assertions: list[dict[str, Any]] | None = None,
    assertion_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "attempt-1",
        "case_id": "case-1",
        "verdict": "passed",
        "execution_status": "succeeded",
        "case_snapshot": {"assertions": list(snapshot_assertions or [])},
        "assertions": list(assertion_results or []),
    }
    if task_outcome is not None or qualification is not None:
        payload["evaluation"] = {
            "task_outcome": task_outcome,
            "qualification": qualification,
        }
    if annotations is not None:
        payload["decision_annotations"] = annotations
    return payload


def test_task_success_rate_judged_uses_judged_denominator() -> None:
    """已判定试验分母：completed / (completed + partial + failed)。"""
    attempts = [
        _attempt(task_outcome="completed"),
        _attempt(task_outcome="partial"),
        _attempt(task_outcome="failed"),
        _attempt(task_outcome=None),
    ]
    summary = aggregate_judged_task_outcomes(attempts, 4)
    assert summary["judged"] == 3
    assert summary["task_success_rate_judged"] == pytest.approx(1 / 3)
    assert summary["judged_coverage"] == pytest.approx(3 / 4)


def test_task_success_rate_judged_is_null_without_judged_attempts() -> None:
    summary = aggregate_judged_task_outcomes([_attempt(task_outcome=None)], 1)
    assert summary["task_success_rate_judged"] is None
    assert summary["judged"] == 0
    assert summary["judged_coverage"] == pytest.approx(0.0)


def test_legacy_task_success_rate_denominator_is_unchanged() -> None:
    """旧口径 completed/planned 一字不改：新字段不能顶掉旧字段。"""
    attempts = [
        _attempt(task_outcome="completed"),
        _attempt(task_outcome="partial"),
        _attempt(task_outcome="failed"),
        _attempt(task_outcome=None),
    ]
    legacy = aggregate_task_outcomes(attempts, 4)
    assert legacy["task_success_rate"] == pytest.approx(1 / 4)
    assert legacy["partial_completion_rate"] == pytest.approx(1 / 4)
    judged = aggregate_judged_task_outcomes(attempts, 4)
    assert judged["task_success_rate_judged"] == pytest.approx(1 / 3)


def test_tool_decision_accuracy_is_null_without_annotations() -> None:
    """没有人工/声明式决策标注 → null + 覆盖说明，不拿工具执行成功率顶替。"""
    summary = aggregate_tool_decision_accuracy([_attempt()])
    assert summary["tool_decision_accuracy"] is None
    assert summary["tool_argument_accuracy"] is None
    assert summary["annotation_coverage"] is None
    assert "NO_DECISION_ANNOTATIONS" in summary["reasons"]


def test_tool_decision_accuracy_counts_annotated_decision_points() -> None:
    """显式标注的决策点：选择与参数分开统计。"""
    attempts = [
        _attempt(
            annotations=[
                {"id": "d1", "kind": "tool_selection", "passed": True},
                {"id": "d2", "kind": "tool_selection", "passed": False},
                {"id": "d3", "kind": "tool_arguments", "passed": True},
                {"id": "d4", "kind": "tool_arguments", "passed": None},
            ]
        )
    ]
    summary = aggregate_tool_decision_accuracy(attempts)
    assert summary["tool_decision_accuracy"] == pytest.approx(0.5)
    assert summary["tool_argument_accuracy"] == pytest.approx(1.0)
    # 4 个已标注决策点，3 个可判定（d4 未知），其中 2 个判定正确。
    assert summary["annotation_coverage"] == pytest.approx(3 / 4)
    assert summary["decision_points"] == 4
    assert summary["judgeable_decision_points"] == 3
    assert summary["correct_decision_points"] == 2
    assert summary["selection"] == {"annotated": 2, "judgeable": 2, "correct": 1}
    assert summary["arguments"] == {"annotated": 2, "judgeable": 1, "correct": 1}


def test_tool_argument_accuracy_is_not_replaced_by_tool_execution_rate() -> None:
    """声明了"选对工具但参数错"的用例时：选择 1.0、参数 0.0，不能混成一个数。"""
    attempts = [
        _attempt(
            snapshot_assertions=[
                {
                    "id": "a1",
                    "kind": "tool_called",
                    "required": True,
                    "name": "read_file",
                    "arguments_subset": {"path": "a.txt"},
                }
            ],
            assertion_results=[
                {"id": "a1", "passed": False, "required": True, "message": ""}
            ],
        )
    ]
    summary = aggregate_tool_decision_accuracy(attempts)
    # 这条断言同时判选择与参数，属于混合决策点：不进两个准确率，只进覆盖分母。
    assert summary["tool_decision_accuracy"] is None
    assert summary["tool_argument_accuracy"] is None
    assert summary["mixed_decision_points"] == 1
    assert summary["annotation_coverage"] == pytest.approx(0.0)


def test_pure_selection_declaration_feeds_decision_accuracy() -> None:
    """``arguments_subset`` 为空的 tool_called 断言是纯选择决策点。"""
    attempts = [
        _attempt(
            snapshot_assertions=[
                {
                    "id": "a1",
                    "kind": "tool_called",
                    "required": True,
                    "name": "list_dir",
                    "arguments_subset": {},
                }
            ],
            assertion_results=[
                {"id": "a1", "passed": True, "required": True, "message": ""}
            ],
        )
    ]
    summary = aggregate_tool_decision_accuracy(attempts)
    assert summary["tool_decision_accuracy"] == pytest.approx(1.0)
    assert summary["tool_argument_accuracy"] is None
    assert summary["annotation_coverage"] == pytest.approx(1.0)
