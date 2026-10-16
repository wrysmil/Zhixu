"""聚合指标与导出（契约 §4 Metrics + spec §7.3 指标口径 + §9 导出安全）。

设计取舍：

- **分母口径写死在函数里**：``overall=passed/planned``、``evaluated=passed/(passed+failed)``、
  ``coverage=(passed+failed)/planned``，零分母一律 ``None``；``tool_case_pass_rate`` 的分母只含
  「该用例全部 required 工具断言都已获得有效判分」的用例，与工具执行错误率完全无关。
- **不猜缺失值**：耗时/Token/评分的缺失样本只减少样本数，不填 0；``UsageAggregate`` 沿用契约 §4
  的模型构造，物理请求数为主账本，agent 与 judge 分开累计。
- **导出用白名单**：JSON/CSV 只按已知字段构造摘要（spec §9「脱敏摘要」），未知键——包括
  ``api_key``、``profile_bundle``、原始回答与理由文本——一律不带出；文本单元格再叠加防公式注入前缀。

工具断言的 kind 来自 attempt 携带的**冻结用例快照**（DB 列 ``case_snapshot_json``）：契约 §4 的
``AttemptRead`` 不暴露 kind，而 ``AssertionResult`` 只有 id，因此报告侧必须能从快照反查 kind。
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from nanobot.evaluation.graders.rules import TOOL_ASSERTION_KINDS
from nanobot.evaluation.schemas import UsageAggregate

VERDICTS = ("passed", "failed", "review_required", "not_evaluated")
#: 快照里允许进入导出的字段（契约 §4：不含密钥、配置路径与 profile_bundle）。
SNAPSHOT_FIELDS = (
    "tool_mode",
    "agent_profile_id",
    "judge_profile_id",
    "provider",
    "model",
    "generation",
    "rules_version",
    "grader_version",
    "case_revisions",
)
#: 防公式注入的开头字符（spec §9）。
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class QualityEntry(TypedDict):
    dimension_id: str
    mean: float | None
    valid_count: int
    eligible_count: int


class DurationStats(TypedDict):
    median_ms: float | None
    p95_ms: float | None
    count: int


class RunMetrics(TypedDict):
    planned: int
    passed: int
    failed: int
    review_required: int
    not_evaluated: int
    overall_pass_rate: float | None
    evaluated_pass_rate: float | None
    evaluation_coverage: float | None
    tool_case_pass_rate: float | None
    tool_eligible_cases: int
    tool_valid_cases: int
    quality: list[QualityEntry]
    agent_duration: DurationStats
    usage: dict[str, Any]


def aggregate_run(
    attempts: Sequence[Mapping[str, Any]],
    planned_count: int,
) -> RunMetrics:
    """按契约 §4 聚合单个运行的 attempt 结果；不依赖 UI，也不跨运行合并数据。"""
    if not isinstance(planned_count, int) or isinstance(planned_count, bool) or planned_count < 0:
        raise ValueError(f"PLANNED_COUNT_INVALID:{planned_count!r}")
    counts = {verdict: 0 for verdict in VERDICTS}
    for attempt in attempts:
        verdict = attempt.get("verdict")
        if verdict not in counts:
            raise ValueError(f"VERDICT_UNSUPPORTED:{verdict!r}")
        counts[verdict] += 1
    passed, failed = counts["passed"], counts["failed"]
    tool_pass, tool_valid, tool_eligible = _tool_case_stats(attempts)
    return RunMetrics(
        planned=planned_count,
        passed=passed,
        failed=failed,
        review_required=counts["review_required"],
        not_evaluated=counts["not_evaluated"],
        overall_pass_rate=_ratio(passed, planned_count),
        evaluated_pass_rate=_ratio(passed, passed + failed),
        evaluation_coverage=_ratio(passed + failed, planned_count),
        tool_case_pass_rate=_ratio(tool_pass, tool_valid),
        tool_eligible_cases=tool_eligible,
        tool_valid_cases=tool_valid,
        quality=_quality_stats(attempts),
        agent_duration=_duration_stats(attempts),
        usage={
            "agent": _usage_ledger(attempts, "agent"),
            "judge": _usage_ledger(attempts, "judge"),
        },
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


# ------------------------------------------------------------------------ 工具用例比率


def _assertion_kinds(attempt: Mapping[str, Any]) -> dict[str, str]:
    """从冻结快照取 ``assertion id -> kind``；缺快照时不猜（该用例不进工具分母）。"""
    snapshot = attempt.get("case_snapshot")
    kinds: dict[str, str] = {}
    if isinstance(snapshot, Mapping):
        for definition in snapshot.get("assertions") or ():
            if isinstance(definition, Mapping) and isinstance(definition.get("id"), str):
                kinds[definition["id"]] = str(definition.get("kind") or "")
    return kinds


def _tool_case_stats(attempts: Sequence[Mapping[str, Any]]) -> tuple[int, int, int]:
    passing = valid = eligible = 0
    for attempt in attempts:
        kinds = _assertion_kinds(attempt)
        required_tool = [
            result
            for result in attempt.get("assertions") or ()
            if isinstance(result, Mapping)
            and result.get("required") is True
            and kinds.get(str(result.get("id"))) in TOOL_ASSERTION_KINDS
        ]
        if not required_tool:
            continue
        eligible += 1
        if all(result.get("passed") is not None for result in required_tool):
            valid += 1
            if all(result.get("passed") is True for result in required_tool):
                passing += 1
    return passing, valid, eligible


# ------------------------------------------------------------------------ 耗时与评分


def _duration_stats(attempts: Sequence[Mapping[str, Any]]) -> DurationStats:
    samples = [
        value
        for value in (_timing(attempt, "agent_ms") for attempt in attempts)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not samples:
        return DurationStats(median_ms=None, p95_ms=None, count=0)
    ordered = sorted(samples)
    return DurationStats(
        median_ms=_median(ordered), p95_ms=_p95_nearest_rank(ordered), count=len(ordered)
    )


def _timing(attempt: Mapping[str, Any], key: str) -> Any:
    timing = attempt.get("timing")
    return timing.get(key) if isinstance(timing, Mapping) else None


def _median(ordered: Sequence[float]) -> float:
    """偶数样本取中间两项平均（契约 §4）。"""
    count = len(ordered)
    middle = count // 2
    if count % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def _p95_nearest_rank(ordered: Sequence[float]) -> float:
    """nearest-rank：第 ``ceil(0.95 * n)`` 项（1 基），不做插值。"""
    rank = math.ceil(0.95 * len(ordered))
    return float(ordered[max(rank - 1, 0)])


def _rubric_dimension_ids(attempt: Mapping[str, Any]) -> list[str]:
    snapshot = attempt.get("case_snapshot")
    rubric = snapshot.get("rubric") if isinstance(snapshot, Mapping) else None
    if not isinstance(rubric, Mapping):
        return []
    ids: list[str] = []
    for dimension in rubric.get("dimensions") or ():
        if isinstance(dimension, Mapping) and isinstance(dimension.get("id"), str):
            ids.append(dimension["id"])
    return ids


def _valid_scores(attempt: Mapping[str, Any]) -> dict[str, int]:
    """有效评分：Judge 已完成、无错误，且分数是量表维度上的 0–4 整数；其余不填零。"""
    judge = attempt.get("judge")
    if not isinstance(judge, Mapping) or judge.get("status") != "completed" or judge.get("error") is not None:
        return {}
    scores: dict[str, int] = {}
    for dimension in judge.get("dimensions") or ():
        if not isinstance(dimension, Mapping):
            continue
        score = dimension.get("score")
        dimension_id = dimension.get("id")
        if isinstance(dimension_id, str) and isinstance(score, int) and not isinstance(score, bool):
            if 0 <= score <= 4:
                scores[dimension_id] = score
    return scores


def _quality_stats(attempts: Sequence[Mapping[str, Any]]) -> list[QualityEntry]:
    ordered_ids: list[str] = []
    for attempt in attempts:
        for dimension_id in _rubric_dimension_ids(attempt) + sorted(_valid_scores(attempt)):
            if dimension_id not in ordered_ids:
                ordered_ids.append(dimension_id)
    entries: list[QualityEntry] = []
    for dimension_id in ordered_ids:
        values = [
            scores[dimension_id]
            for scores in (_valid_scores(attempt) for attempt in attempts)
            if dimension_id in scores
        ]
        eligible = sum(dimension_id in _rubric_dimension_ids(attempt) for attempt in attempts)
        entries.append(
            QualityEntry(
                dimension_id=dimension_id,
                mean=None if not values else sum(values) / len(values),
                valid_count=len(values),
                eligible_count=eligible,
            )
        )
    return entries


# -------------------------------------------------------------------------- Token 账本


def _side_usage(attempt: Mapping[str, Any], side: str) -> Mapping[str, Any] | None:
    usage = attempt.get("usage")
    if not isinstance(usage, Mapping):
        return None
    value = usage.get(side)
    return value if isinstance(value, Mapping) else None


def _known(values: list[int | None]) -> list[int]:
    return [value for value in values if isinstance(value, int) and not isinstance(value, bool)]


def _usage_ledger(attempts: Sequence[Mapping[str, Any]], side: str) -> dict[str, Any]:
    """以物理请求为主账本累加；未知量保持 ``None``，不补零、不重复相加。"""
    ledgers = [ledger for ledger in (_side_usage(attempt, side) for attempt in attempts) if ledger]
    if not ledgers:
        return UsageAggregate(
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            source="unavailable",
            coverage="unavailable",
            physical_requests=0,
            iterations=0,
            missing_usage_calls=0,
        ).model_dump()

    def summed(key: str) -> int | None:
        known = _known([ledger.get(key) for ledger in ledgers])
        return sum(known) if known else None

    input_tokens = summed("input_tokens")
    output_tokens = summed("output_tokens")
    total_tokens = summed("total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    sources = {str(ledger.get("source")) for ledger in ledgers} - {"None", "unavailable"}
    if not sources:
        source = "unavailable"
    elif len(sources) > 1:
        source = "mixed"
    else:
        source = next(iter(sources))
    coverages = {str(ledger.get("coverage")) for ledger in ledgers} - {"None"}
    missing = summed("missing_usage_calls") or 0
    if coverages == {"unavailable"}:
        coverage = "unavailable"
    elif coverages == {"complete"} and missing == 0:
        coverage = "complete"
    else:
        coverage = "partial"

    return UsageAggregate(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        source=source,  # pyright: ignore[reportArgumentType] - 上面已归一到四个闭集值
        coverage=coverage,  # pyright: ignore[reportArgumentType] - 上面已归一到三个闭集值
        physical_requests=summed("physical_requests") or 0,
        iterations=summed("iterations") or 0,
        missing_usage_calls=missing,
    ).model_dump()


# ---------------------------------------------------------------------------- 导出


def baseline_key(
    *,
    tool_mode: str,
    provider: str | None,
    model: str | None,
    rules_version: str | None = None,
    grader_version: str | None = None,
) -> str:
    """基线标识：工具模式或模型不同就不是同一条基线（契约 §4「不合并历史数据」）。"""
    parts = [tool_mode, provider or "", model or "", rules_version or "", grader_version or ""]
    return "|".join(parts)


def _safe_text(value: Any) -> str:
    """CSV 防公式注入：以 ``= + - @ Tab CR`` 开头的文本加前导单引号。"""
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return f"'{text}"
    return text


CSV_COLUMNS: tuple[str, ...] = (
    "run_id",
    "attempt_id",
    "position",
    "case_id",
    "case_revision",
    "case_name",
    "execution_status",
    "grading_status",
    "verdict",
    "trace_complete",
    "agent_ms",
    "grading_ms",
    "total_ms",
    "agent_input_tokens",
    "agent_output_tokens",
    "agent_total_tokens",
    "agent_usage_source",
    "agent_usage_coverage",
    "agent_physical_requests",
    "judge_input_tokens",
    "judge_output_tokens",
    "judge_total_tokens",
    "judge_usage_source",
    "judge_usage_coverage",
    "judge_physical_requests",
    "judge_status",
    "judge_calibrated",
    "assertion_passed",
    "assertion_failed",
    "assertion_unreadable",
    "review_count",
)


def _attempt_row(run_id: str, attempt: Mapping[str, Any]) -> dict[str, Any]:
    usage = attempt.get("usage") if isinstance(attempt.get("usage"), Mapping) else {}
    agent = usage.get("agent") if isinstance(usage, Mapping) else None
    judge = usage.get("judge") if isinstance(usage, Mapping) else None
    judge_result = attempt.get("judge") if isinstance(attempt.get("judge"), Mapping) else None
    results = [item for item in attempt.get("assertions") or () if isinstance(item, Mapping)]
    counts = {
        "assertion_passed": sum(1 for item in results if item.get("passed") is True),
        "assertion_failed": sum(1 for item in results if item.get("passed") is False),
        "assertion_unreadable": sum(1 for item in results if item.get("passed") is None),
    }

    def tokens(ledger: Any, key: str) -> Any:
        return ledger.get(key) if isinstance(ledger, Mapping) else None

    row: dict[str, Any] = {
        "run_id": run_id,
        "attempt_id": attempt.get("id"),
        "position": attempt.get("position"),
        "case_id": attempt.get("case_id"),
        "case_revision": attempt.get("case_revision"),
        "case_name": attempt.get("case_name"),
        "execution_status": attempt.get("execution_status"),
        "grading_status": attempt.get("grading_status"),
        "verdict": attempt.get("verdict"),
        "trace_complete": attempt.get("trace_complete"),
        "agent_ms": _timing(attempt, "agent_ms"),
        "grading_ms": _timing(attempt, "grading_ms"),
        "total_ms": _timing(attempt, "total_ms"),
        "agent_input_tokens": tokens(agent, "input_tokens"),
        "agent_output_tokens": tokens(agent, "output_tokens"),
        "agent_total_tokens": tokens(agent, "total_tokens"),
        "agent_usage_source": tokens(agent, "source"),
        "agent_usage_coverage": tokens(agent, "coverage"),
        "agent_physical_requests": tokens(agent, "physical_requests"),
        "judge_input_tokens": tokens(judge, "input_tokens"),
        "judge_output_tokens": tokens(judge, "output_tokens"),
        "judge_total_tokens": tokens(judge, "total_tokens"),
        "judge_usage_source": tokens(judge, "source"),
        "judge_usage_coverage": tokens(judge, "coverage"),
        "judge_physical_requests": tokens(judge, "physical_requests"),
        "judge_status": judge_result.get("status") if judge_result else None,
        "judge_calibrated": judge_result.get("calibrated") if judge_result else None,
        "review_count": len(attempt.get("reviews") or ()),
    }
    row.update(counts)
    return row


def _export_rows(
    run: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(run, Mapping):
        raise TypeError("RUN_MAPPING_REQUIRED")
    run_id = str(run.get("id") or "")
    ordered = sorted(attempts, key=lambda item: (item.get("position") or 0, str(item.get("id"))))
    return [_attempt_row(run_id, attempt) for attempt in ordered]


def export_run_csv(
    run: Mapping[str, Any],
    metrics: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
) -> str:
    """CSV 摘要：一行一个 attempt，文本单元格防公式注入。"""
    del metrics  # 指标以 JSON 导出为准；CSV 只给逐用例摘要行。
    rows = _export_rows(run, attempts)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow([_safe_text(row[column]) for column in CSV_COLUMNS])
    return buffer.getvalue()


def export_run_json(
    run: Mapping[str, Any],
    metrics: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
) -> str:
    """JSON 摘要：字段白名单构造，因此不可能带出密钥、profile_bundle 或原始回答。"""
    rows = _export_rows(run, attempts)
    run_id = str(run.get("id") or "")
    snapshot = run.get("snapshot")
    payload = {
        "run_id": run_id,
        "status": run.get("status"),
        "suite_id": run.get("suite_id"),
        "suite_name": run.get("suite_name"),
        "parent_run_id": run.get("parent_run_id"),
        "created_at": run.get("created_at"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "snapshot": (
            {key: snapshot[key] for key in SNAPSHOT_FIELDS if key in snapshot}
            if isinstance(snapshot, Mapping)
            else {}
        ),
        "metrics": dict(metrics),
        "attempts": rows,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
