"""聚合指标与导出的红灯测试（WU-04 / T07 第 5–6 项）。

分母口径全部来自契约 §4 的 Metrics 定义；黄金组合是 plan T07 钉死的数字，任何实现改动
都必须保持它成立。导出侧覆盖 spec §9「CSV 文本需防公式注入」与「导出不含秘密」。
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from typing import Any

import pytest

from nanobot.evaluation.reports import (
    aggregate_run,
    baseline_key,
    export_run_csv,
    export_run_json,
    render_summary_html,
)


def usage(
    *,
    input_tokens: int | None = 100,
    output_tokens: int | None = 20,
    total_tokens: int | None = None,
    source: str = "reported",
    coverage: str = "complete",
    physical_requests: int = 1,
    iterations: int = 1,
    missing_usage_calls: int = 0,
) -> dict[str, Any]:
    known_total = (
        input_tokens is not None
        and output_tokens is not None
        and total_tokens is None
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens if known_total else total_tokens,
        "source": source,
        "coverage": coverage,
        "physical_requests": physical_requests,
        "iterations": iterations,
        "missing_usage_calls": missing_usage_calls,
    }


def assertion_result(
    assertion_id: str,
    passed: bool | None,
    *,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "id": assertion_id,
        "passed": passed,
        "required": required,
        "message": "",
        "evidence_refs": [],
    }


def tool_assertion(assertion_id: str, passed: bool | None, *, required: bool = True) -> dict[str, Any]:
    return assertion_result(assertion_id, passed, required=required)


def judge_payload(scores: dict[str, int], *, calibrated: bool = True, status: str = "completed") -> dict[str, Any]:
    return {
        "status": status,
        "model": "judge-model",
        "version": "judge-v1",
        "calibrated": calibrated,
        "error": None,
        "dimensions": [
            {"id": key, "score": score, "reason": "理由", "evidence_refs": []}
            for key, score in scores.items()
        ],
    }


RUBRIC = {
    "version": "general-v1",
    "required": True,
    "dimensions": [
        {"id": "task_completion", "name": "任务完成", "anchors": ["a"] * 5, "pass_score": 3},
        {"id": "correctness", "name": "正确性", "anchors": ["a"] * 5, "pass_score": 3},
    ],
}


def attempt(
    verdict: str = "passed",
    *,
    position: int = 1,
    assertions: list[dict[str, Any]] | None = None,
    tool_assertions: list[dict[str, Any]] | None = None,
    rubric: dict[str, Any] | None = None,
    judge: dict[str, Any] | None = None,
    agent_ms: int | None = 1000,
    grading_ms: int | None = 0,
    agent_usage: dict[str, Any] | None = None,
    judge_usage: dict[str, Any] | None = None,
    case_name: str = "用例",
    execution_status: str = "succeeded",
    grading_status: str = "completed",
    trace_complete: bool = True,
) -> dict[str, Any]:
    results = list(
        assertions if assertions is not None else [assertion_result("a1", verdict == "passed")]
    )
    tool_results = list(tool_assertions or [])
    results = results + tool_results
    tool_ids = {item["id"] for item in tool_results}
    # 冻结快照里的断言定义带 kind，报告侧据此区分「工具断言」与回答/文件断言。
    definitions = [
        {
            "id": item["id"],
            "kind": "tool_called" if item["id"] in tool_ids else "answer_contains",
            "required": item["required"],
        }
        for item in results
    ]
    return {
        "id": str(uuid.uuid4()),
        "run_id": "run-1",
        "position": position,
        "case_id": str(uuid.uuid4()),
        "case_revision": 1,
        "case_name": case_name,
        "execution_status": execution_status,
        "grading_status": grading_status,
        "verdict": verdict,
        "started_at": "2026-10-15T00:00:00Z",
        "finished_at": "2026-10-15T00:00:05Z",
        "final_answer": "结论",
        "stop_reason": "stop",
        "errors": [],
        "assertions": results,
        "judge": judge,
        "usage": {"agent": agent_usage, "judge": judge_usage},
        "timing": {
            "queue_ms": 10,
            "agent_ms": agent_ms,
            "grading_ms": grading_ms,
            "total_ms": 5000,
        },
        "trace_complete": trace_complete,
        "artifacts": [],
        "reviews": [],
        "case_snapshot": {"assertions": definitions, "rubric": rubric},
    }


# ------------------------------------------------------------------ 黄金组合（plan T07）


def golden_metrics_attempts() -> list[dict[str, Any]]:
    return [
        attempt("passed", position=1),
        attempt("passed", position=2),
        attempt("failed", position=3),
        attempt("review_required", position=4),
        attempt("not_evaluated", position=5, agent_ms=None, grading_ms=None),
    ]


def test_golden_combination_matches_contract_formula() -> None:
    metrics = aggregate_run(golden_metrics_attempts(), planned_count=5)

    assert metrics["planned"] == 5
    assert metrics["passed"] == 2
    assert metrics["failed"] == 1
    assert metrics["review_required"] == 1
    assert metrics["not_evaluated"] == 1
    assert metrics["overall_pass_rate"] == pytest.approx(0.4)
    assert metrics["evaluated_pass_rate"] == pytest.approx(2 / 3)
    assert metrics["evaluation_coverage"] == pytest.approx(0.6)


def test_empty_attempt_set_has_null_ratios() -> None:
    metrics = aggregate_run([], planned_count=0)

    assert metrics["overall_pass_rate"] is None
    assert metrics["evaluated_pass_rate"] is None
    assert metrics["evaluation_coverage"] is None
    assert metrics["tool_case_pass_rate"] is None
    assert metrics["agent_duration"] == {"median_ms": None, "p95_ms": None, "count": 0}
    assert metrics["quality"] == []


def test_zero_denominator_with_planned_still_nulls_evaluated_rate() -> None:
    """planned>0 但没有任何 passed/failed 时，已评通过率仍是 null，不填 0。"""
    metrics = aggregate_run(
        [attempt("review_required", position=1), attempt("not_evaluated", position=2)],
        planned_count=2,
    )

    assert metrics["evaluated_pass_rate"] is None
    assert metrics["overall_pass_rate"] == pytest.approx(0.0)
    assert metrics["evaluation_coverage"] == pytest.approx(0.0)


def test_counts_come_from_verdict_not_execution_status() -> None:
    metrics = aggregate_run(
        [attempt("failed", position=1, execution_status="timed_out")], planned_count=1
    )

    assert metrics["failed"] == 1
    assert metrics["overall_pass_rate"] == pytest.approx(0.0)


# ------------------------------------------------------------------- 中位数与 P95


def test_median_even_count_and_nearest_rank_p95() -> None:
    attempts = [attempt("passed", position=index + 1, agent_ms=ms) for index, ms in enumerate([40, 10, 30, 20])]

    duration = aggregate_run(attempts, planned_count=4)["agent_duration"]

    assert duration == {"median_ms": 25.0, "p95_ms": 40, "count": 4}


def test_median_odd_count_picks_middle_and_p95_rounds_up() -> None:
    attempts = [attempt("passed", position=index + 1, agent_ms=ms) for index, ms in enumerate([10, 30, 20])]

    duration = aggregate_run(attempts, planned_count=3)["agent_duration"]

    assert duration["median_ms"] == 20.0
    assert duration["p95_ms"] == 30
    assert duration["count"] == 3


def test_single_sample_duration() -> None:
    duration = aggregate_run([attempt("passed", agent_ms=7)], planned_count=1)["agent_duration"]

    assert duration == {"median_ms": 7.0, "p95_ms": 7, "count": 1}


def test_missing_duration_samples_are_excluded_from_count() -> None:
    """未采到的耗时不补零，只按有效样本计算并注明样本数。"""
    attempts = [attempt("passed", position=1, agent_ms=10), attempt("not_evaluated", position=2, agent_ms=None)]

    duration = aggregate_run(attempts, planned_count=2)["agent_duration"]

    assert duration == {"median_ms": 10.0, "p95_ms": 10, "count": 1}


# --------------------------------------------------------------- 工具用例正确率分母


def test_tool_case_pass_rate_excludes_unjudged_and_non_required() -> None:
    """分母只含「全部 required 工具断言都已有效判分」的用例，与工具执行错误率无关。"""
    attempts = [
        # A：required 工具断言通过 → 有效且通过
        attempt("passed", position=1, tool_assertions=[tool_assertion("t1", True)]),
        # B：required 工具断言失败 → 有效但不通过
        attempt("failed", position=2, tool_assertions=[tool_assertion("t1", False)]),
        # C：required 工具断言读不到证据 → 不计入分母
        attempt("review_required", position=3, tool_assertions=[tool_assertion("t1", None)]),
        # D：没有工具断言 → 不 eligible
        attempt("passed", position=4),
        # E：只有非 required 工具断言 → 不 eligible
        attempt(
            "passed",
            position=5,
            tool_assertions=[tool_assertion("t1", False, required=False)],
        ),
        # F：一个 required 有效 + 一个 required 读不到 → 整体不 valid
        attempt(
            "review_required",
            position=6,
            tool_assertions=[tool_assertion("t1", True), tool_assertion("t2", None)],
        ),
    ]

    metrics = aggregate_run(attempts, planned_count=6)

    assert metrics["tool_eligible_cases"] == 4  # A/B/C/F 至少有一条 required 工具断言
    assert metrics["tool_valid_cases"] == 2  # C、F 有读不到证据的 required 工具断言 → 不进分母
    assert metrics["tool_case_pass_rate"] == pytest.approx(0.5)


def test_tool_rate_is_null_when_no_eligible_case() -> None:
    metrics = aggregate_run([attempt("passed")], planned_count=1)

    assert metrics["tool_eligible_cases"] == 0
    assert metrics["tool_valid_cases"] == 0
    assert metrics["tool_case_pass_rate"] is None


# ---------------------------------------------------------------------- 回答质量


def test_quality_means_only_over_valid_scores() -> None:
    attempts = [
        attempt("passed", position=1, rubric=RUBRIC, judge=judge_payload({"task_completion": 3, "correctness": 4})),
        # 缺失评分不填零：judge=None 只进入 eligible，不进 mean。
        attempt("review_required", position=2, rubric=RUBRIC, judge=None),
        # 未校准的原始分数依然展示（spec §3.1：校准失败仍展示原始评分），门禁由 verdict 负责。
        attempt(
            "review_required",
            position=3,
            rubric=RUBRIC,
            judge=judge_payload({"task_completion": 4, "correctness": 4}, calibrated=False),
        ),
    ]

    quality = aggregate_run(attempts, planned_count=3)["quality"]

    assert {item["dimension_id"] for item in quality} == {"task_completion", "correctness"}
    completion = next(item for item in quality if item["dimension_id"] == "task_completion")
    assert completion["valid_count"] == 2
    assert completion["eligible_count"] == 3
    assert completion["mean"] == pytest.approx(3.5)


def test_quality_mean_is_null_when_no_valid_score() -> None:
    attempts = [attempt("review_required", rubric=RUBRIC, judge=None)]

    quality = aggregate_run(attempts, planned_count=1)["quality"]

    assert quality == [
        {"dimension_id": "task_completion", "mean": None, "valid_count": 0, "eligible_count": 1},
        {"dimension_id": "correctness", "mean": None, "valid_count": 0, "eligible_count": 1},
    ]


def test_judge_error_dimensions_are_not_counted() -> None:
    attempts = [
        attempt(
            "review_required",
            rubric=RUBRIC,
            judge=judge_payload({}, status="judge_error"),
        )
    ]

    quality = aggregate_run(attempts, planned_count=1)["quality"]

    assert all(item["valid_count"] == 0 and item["mean"] is None for item in quality)


# ------------------------------------------------------------------------ Token 账本


def test_usage_ledger_keeps_agent_and_judge_separate() -> None:
    attempts = [
        attempt(
            "passed",
            agent_usage=usage(input_tokens=100, output_tokens=20, physical_requests=1, iterations=1),
            judge_usage=usage(input_tokens=30, output_tokens=5, physical_requests=1, source="estimated"),
        ),
        attempt(
            "passed",
            position=2,
            agent_usage=usage(input_tokens=50, output_tokens=10, physical_requests=2, source="estimated", coverage="partial", missing_usage_calls=1),
        ),
    ]

    metrics = aggregate_run(attempts, planned_count=2)["usage"]

    assert metrics["agent"] == {
        "input_tokens": 150,
        "output_tokens": 30,
        "total_tokens": 180,
        "source": "mixed",
        "coverage": "partial",
        "physical_requests": 3,
        # iterations 是 Runner 循环轮数，与物理请求数分别统计（spec §7.3）。
        "iterations": 2,
        "missing_usage_calls": 1,
    }
    assert metrics["judge"]["input_tokens"] == 30
    assert metrics["judge"]["physical_requests"] == 1


def test_unknown_usage_is_not_zero_filled() -> None:
    metrics = aggregate_run(
        [attempt("passed", agent_usage=usage(input_tokens=None, output_tokens=None, source="unavailable", coverage="unavailable", physical_requests=2, iterations=2))],
        planned_count=1,
    )["usage"]["agent"]

    assert metrics["input_tokens"] is None
    assert metrics["output_tokens"] is None
    assert metrics["total_tokens"] is None
    assert metrics["source"] == "unavailable"
    assert metrics["coverage"] == "unavailable"
    assert metrics["physical_requests"] == 2


def test_all_reported_usage_is_reported_and_complete() -> None:
    metrics = aggregate_run(
        [attempt("passed", agent_usage=usage(input_tokens=1, output_tokens=2))], planned_count=1
    )["usage"]["agent"]

    assert metrics["source"] == "reported"
    assert metrics["coverage"] == "complete"


# ------------------------------------------------------------------------ 基线不合并


def test_baseline_key_separates_tool_mode_and_model() -> None:
    """契约 §4：不同工具模式/模型的历史数据不得合并成同一基线。"""
    fixture_key = baseline_key(tool_mode="fixture", provider="openai", model="a", grader_version="judge-v1")
    other_mode = baseline_key(tool_mode="live", provider="openai", model="a", grader_version="judge-v1")
    other_model = baseline_key(tool_mode="fixture", provider="openai", model="b", grader_version="judge-v1")
    same = baseline_key(tool_mode="fixture", provider="openai", model="a", grader_version="judge-v1")

    assert fixture_key == same
    assert fixture_key != other_mode
    assert fixture_key != other_model


# ------------------------------------------------------------------------ 导出


def test_csv_export_escapes_formula_injection() -> None:
    names = ["=1+1", "+SUM(A1:A2)", "-2+3", "@SUM(1+1)", "\ttab开头", "\rCR开头", "正常名称"]
    attempts = [
        attempt("failed", position=index + 1, case_name=name)
        for index, name in enumerate(names)
    ]
    metrics = aggregate_run(attempts, planned_count=len(attempts))

    text = export_run_csv({"id": "run-1"}, metrics, attempts)

    rows = list(csv.reader(io.StringIO(text)))
    header, *body = rows
    names = [row[header.index("case_name")] for row in body]
    assert names == [
        "'=1+1",
        "'+SUM(A1:A2)",
        "'-2+3",
        "'@SUM(1+1)",
        "'\ttab开头",
        "'\rCR开头",
        "正常名称",
    ]


def test_csv_export_has_one_row_per_attempt_and_metrics_header_row() -> None:
    attempts = [attempt("passed", position=1), attempt("failed", position=2)]
    metrics = aggregate_run(attempts, planned_count=2)

    rows = list(csv.reader(io.StringIO(export_run_csv({"id": "run-1"}, metrics, attempts))))

    assert rows[0][0] == "run_id"
    assert len(rows) == 1 + 2


def test_json_export_is_free_of_secrets() -> None:
    """导出只按已知字段构造：未知键（含密钥、boot profile_bundle）一律不带出。"""
    attempts = [attempt("passed", agent_usage=usage(), judge_usage=usage(input_tokens=1, output_tokens=1))]
    attempts[0]["api_key"] = "sk-live-secret"
    attempts[0]["case_snapshot"]["profile_bundle"] = {"api_key": "sk-live-secret"}
    attempts[0]["usage"]["agent"]["authorization"] = "Bearer sk-live-secret"
    attempts[0]["final_answer"] = "含 sk-live-secret 的原始回答"
    metrics = aggregate_run(attempts, planned_count=1)

    text = export_run_json({"id": "run-1", "snapshot": {"api_key": "sk-live-secret"}}, metrics, attempts)

    assert "sk-live-secret" not in text
    assert "api_key" not in text
    assert "profile_bundle" not in text
    payload = json.loads(text)
    assert payload["run_id"] == "run-1"
    assert payload["metrics"]["overall_pass_rate"] == pytest.approx(1.0)
    assert payload["attempts"][0]["case_name"] == "用例"


def test_export_rejects_unknown_run_id_type() -> None:
    with pytest.raises((TypeError, KeyError, ValueError)):
        export_run_json(None, aggregate_run([], planned_count=0), [])  # type: ignore[arg-type]


# ------------------------------------------------------------------ HTML 汇总报告（WU-07）


def test_summary_html_is_self_contained() -> None:
    html = render_summary_html(
        title="评估汇总",
        generated_at="2026-10-23T00:00:00Z",
        sections=[{"heading": "通过率", "rows": [["run-1", "4/6", "0.667"]]}],
    )

    assert "<html" in html and "run-1" in html
    # 自包含：不引外部资源（http/https），也不依赖脚本。
    assert "http://" not in html and "https://" not in html
    assert "<script" not in html


def test_summary_html_escapes_special_characters() -> None:
    """sections 里的 HTML 特殊字符（含标题/表头/单元格）必须被转义，防止注入。"""
    html = render_summary_html(
        title="<b>加粗</b> & \"引号\"",
        generated_at="2026-10-23T00:00:00Z",
        sections=[
            {
                "heading": "结论 <img src=x onerror=alert(1)>",
                "headers": ["用例", "<i>状态</i>"],
                "rows": [["<script>alert(1)</script>", "a&b"]],
            },
        ],
    )

    # 原始标签不得原样出现在输出里
    assert "<script>" not in html
    assert "<img" not in html
    assert "<i>" not in html
    # 转义后的文本仍然可见（内容不丢，只做转义）
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;i&gt;状态&lt;/i&gt;" in html
    assert "&lt;b&gt;加粗&lt;/b&gt; &amp; &quot;引号&quot;" in html
