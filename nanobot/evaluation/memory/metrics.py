"""记忆专项指标定义、构建与口径（契约 §4 / §8.1–§8.4）。

三条硬约束：

1. **字段缺失不是 0**：``value`` / ``numerator`` / ``denominator`` 允许为 null，
   由 ``status`` 区分 ``not_assessable``（算不出来）与 ``not_applicable``（不适用）。
   缺样本、缺后端状态、缺人工标注一律 null，绝不补 0。
2. **指标 ID 与契约逐字一致**：契约 §4 的 21 个 ID + §8.4 新增主指标
   ``qa_accuracy_scenario_macro``，另加分阶段时延/Token/嵌入/存储成本指标。
   改名或改分母等于悄悄换口径，契约 §8 明确禁止。
3. **F1 用计数直接算**：``2TP/(2TP+FP+FN)``，P/R 为 null 时不会把漏记藏起来
   （``extraction_prf(0, 0, 2) == (None, 0, 0)``）。

本模块只负责**口径与构建**，不做语义判断；事实匹配与逐题判定在
:mod:`nanobot.evaluation.memory.grading`。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from nanobot.evaluation.memory.models import (
    MemoryCoverage,
    MemoryMetricResult,
    MemoryMetricScope,
    MemoryMetricStatus,
    MemoryMetricUnit,
)

#: 指标结果的版本号（契约 §4：``version=1``）。
METRICS_VERSION = 1

# --------------------------------------------------------------------------- 指标 ID


EXTRACTION_PRECISION = "extraction_precision"
EXTRACTION_RECALL = "extraction_recall"
EXTRACTION_F1 = "extraction_f1"
UNSUPPORTED_MEMORY_RATE = "unsupported_memory_rate"
NEGATIVE_WRITE_SUCCESS = "negative_write_success"
DUPLICATE_MEMORY_RATE = "duplicate_memory_rate"
EVIDENCE_RECALL_AT_K = "evidence_recall_at_k"
RETRIEVAL_PRECISION_RETURNED_AT_K = "retrieval_precision_returned_at_k"
MRR = "mrr"
INJECTION_RECALL = "injection_recall"
QA_ACCURACY = "qa_accuracy"
GROUNDED_MEMORY_SUCCESS = "grounded_memory_success"
FACT_COVERAGE = "fact_coverage"
QA_ACCURACY_SCENARIO_MACRO = "qa_accuracy_scenario_macro"
QA_ACCURACY_PROBE_MICRO = "qa_accuracy_probe_micro"
QA_ACCURACY_CATEGORY_MACRO = "qa_accuracy_category_macro"
STRICT_SCENARIO_PASS_RATE = "strict_scenario_pass_rate"
REPEAT_STABILITY = "repeat_stability"
PREFERENCE_COMPLIANCE = "preference_compliance"
UPDATE_ACCURACY = "update_accuracy"
STALE_USE_RATE = "stale_use_rate"
ABSTENTION_ACCURACY = "abstention_accuracy"
FALSE_ABSTENTION_RATE = "false_abstention_rate"
CROSS_SESSION_ACCURACY = "cross_session_accuracy"
RESTART_ACCURACY = "restart_accuracy"
FORGET_BEHAVIOR_SUCCESS = "forget_behavior_success"
FORGET_STATE_SUCCESS = "forget_state_success"
SCOPE_LEAK_RATE = "scope_leak_rate"
MEMORY_LIFT = "memory_lift"

WRITE_LATENCY_P50_MS = "write_latency_p50_ms"
WRITE_LATENCY_P95_MS = "write_latency_p95_ms"
INDEX_LATENCY_P50_MS = "index_latency_p50_ms"
INDEX_LATENCY_P95_MS = "index_latency_p95_ms"
RETRIEVE_LATENCY_P50_MS = "retrieve_latency_p50_ms"
RETRIEVE_LATENCY_P95_MS = "retrieve_latency_p95_ms"
INJECT_LATENCY_P50_MS = "inject_latency_p50_ms"
INJECT_LATENCY_P95_MS = "inject_latency_p95_ms"
EXTRACTION_TOKENS = "extraction_tokens"
DERIVATION_TOKENS = "derivation_tokens"
EMBEDDING_TOKENS = "embedding_tokens"
READER_TOKENS = "reader_tokens"
JUDGE_TOKENS = "judge_tokens"
EMBEDDING_CALLS = "embedding_calls"
MODEL_REQUESTS = "model_requests"
STORAGE_BYTES = "storage_bytes"
ESTIMATED_COST = "estimated_cost"


MetricDirection = Literal["higher_is_better", "lower_is_better"]


@dataclass(frozen=True)
class MetricSpec:
    """一个指标的静态口径。"""

    id: str
    unit: MemoryMetricUnit
    scope: MemoryMetricScope
    direction: MetricDirection
    description: str
    stage: str = "behavior"


def _spec(
    metric_id: str,
    unit: MemoryMetricUnit,
    scope: MemoryMetricScope,
    direction: MetricDirection,
    description: str,
    stage: str = "behavior",
) -> tuple[str, MetricSpec]:
    return metric_id, MetricSpec(
        id=metric_id,
        unit=unit,
        scope=scope,
        direction=direction,
        description=description,
        stage=stage,
    )


#: 全部指标口径；键与值里的 ``id`` 一致（避免注册表与自身漂移）。
METRIC_SPECS: dict[str, MetricSpec] = dict(
    (
        _spec(
            EXTRACTION_PRECISION,
            "ratio",
            "scenario",
            "higher_is_better",
            "抽取精确率 TP/(TP+FP)；仅 closed_world 标注可输出，targeted 返回 not_assessable。",
            "extraction",
        ),
        _spec(
            EXTRACTION_RECALL,
            "ratio",
            "scenario",
            "higher_is_better",
            "抽取召回率 TP/(TP+FN)；targeted 下表示已标目标覆盖率。",
            "extraction",
        ),
        _spec(
            EXTRACTION_F1,
            "ratio",
            "scenario",
            "higher_is_better",
            "抽取 F1 = 2TP/(2TP+FP+FN)，用计数直接计算。",
            "extraction",
        ),
        _spec(
            UNSUPPORTED_MEMORY_RATE,
            "ratio",
            "scenario",
            "lower_is_better",
            "无源会话依据的新增事实 / 全部新增事实；缺源会话材料时未知。",
            "extraction",
        ),
        _spec(
            NEGATIVE_WRITE_SUCCESS,
            "ratio",
            "scenario",
            "higher_is_better",
            "纯负例场景中没有写出任何事实的比例（拒答文本不算删除成功）。",
            "extraction",
        ),
        _spec(
            DUPLICATE_MEMORY_RATE,
            "ratio",
            "scenario",
            "lower_is_better",
            "重复事实占比；重复不增加 TP，单列不混进 FP。",
            "extraction",
        ),
        _spec(
            EVIDENCE_RECALL_AT_K,
            "ratio",
            "probe",
            "higher_is_better",
            "top-k 去重后覆盖的 gold 证据单元 / gold 证据单元。",
            "retrieval",
        ),
        _spec(
            RETRIEVAL_PRECISION_RETURNED_AT_K,
            "ratio",
            "probe",
            "higher_is_better",
            "实际返回条数中的相关条数占比；分母是实际返回数，不是固定 k。",
            "retrieval",
        ),
        _spec(
            MRR,
            "ratio",
            "probe",
            "higher_is_better",
            "首条相关结果的排名倒数；无相关 gold 时 not_applicable。",
            "retrieval",
        ),
        _spec(
            INJECTION_RECALL,
            "ratio",
            "probe",
            "higher_is_better",
            "实际注入模型上下文的必要证据覆盖 / 所需必要证据。",
            "injection",
        ),
        _spec(
            QA_ACCURACY,
            "ratio",
            "probe",
            "higher_is_better",
            "逐题二值正确率：必需事实全覆盖且无禁用值、无关键矛盾、无作用域违反。",
            "answer",
        ),
        _spec(
            GROUNDED_MEMORY_SUCCESS,
            "ratio",
            "probe",
            "higher_is_better",
            "答对且全部必要证据在该 probe 实际可用的合法记忆上下文中；答对无证据只算答对。",
            "answer",
        ),
        _spec(
            FACT_COVERAGE,
            "ratio",
            "probe",
            "higher_is_better",
            "事实覆盖分：已覆盖的必需事实 / 必需事实；部分正确只记这里，不四舍五入。",
            "answer",
        ),
        _spec(
            QA_ACCURACY_SCENARIO_MACRO,
            "ratio",
            "dataset",
            "higher_is_better",
            "主指标：probe→trial→scene 等权后的场景宏平均 S=(1/N)Σs_i。",
            "answer",
        ),
        _spec(
            QA_ACCURACY_PROBE_MICRO,
            "ratio",
            "dataset",
            "higher_is_better",
            "题级 micro：所有可判题的正确率，作为主指标的补充。",
            "answer",
        ),
        _spec(
            QA_ACCURACY_CATEGORY_MACRO,
            "ratio",
            "dataset",
            "higher_is_better",
            "类别宏平均：各类别内部按场景平均后再平均。",
            "answer",
        ),
        _spec(
            STRICT_SCENARIO_PASS_RATE,
            "ratio",
            "dataset",
            "higher_is_better",
            "严格场景通过率：所有必需 probe 与硬条件均通过的 trial 比例（与主指标不可互换）。",
            "answer",
        ),
        _spec(
            REPEAT_STABILITY,
            "ratio",
            "dataset",
            "higher_is_better",
            "重复稳定性：K 次全部通过的场景 / 完整可判 K 次的场景（与主指标不可互换）。",
            "answer",
        ),
        _spec(
            PREFERENCE_COMPLIANCE,
            "ratio",
            "probe",
            "higher_is_better",
            "偏好遵循率：偏好类场景中 qa_correct=1 的题占比。",
            "answer",
        ),
        _spec(
            UPDATE_ACCURACY,
            "ratio",
            "probe",
            "higher_is_better",
            "最新有效事实正确率：更新类场景中 qa_correct=1 的题占比。",
            "answer",
        ),
        _spec(
            STALE_USE_RATE,
            "ratio",
            "probe",
            "lower_is_better",
            "过期事实误用率：把失效值断言为当前值的题 / 可判当前状态题；仅提到旧值不算误用。",
            "answer",
        ),
        _spec(
            ABSTENTION_ACCURACY,
            "ratio",
            "probe",
            "higher_is_better",
            "正确拒答率：无答案题中正确承认信息不足的比例。",
            "answer",
        ),
        _spec(
            FALSE_ABSTENTION_RATE,
            "ratio",
            "probe",
            "lower_is_better",
            "误拒答率：有答案题中错误拒答的比例；防止全拒答刷分。",
            "answer",
        ),
        _spec(
            CROSS_SESSION_ACCURACY,
            "ratio",
            "probe",
            "higher_is_better",
            "跨会话保持率：换 session 后正确的题 / 对应可判题。",
            "answer",
        ),
        _spec(
            RESTART_ACCURACY,
            "ratio",
            "probe",
            "higher_is_better",
            "重启保持率：重启并从持久化恢复后正确的题 / 对应可判题。",
            "answer",
        ),
        _spec(
            FORGET_BEHAVIOR_SUCCESS,
            "ratio",
            "scenario",
            "higher_is_better",
            "行为遗忘成功率：删除后所有 probe 均未暴露目标事实的场景比例。",
            "behavior",
        ),
        _spec(
            FORGET_STATE_SUCCESS,
            "ratio",
            "scenario",
            "higher_is_better",
            "状态删除完整率：声明范围内存储/索引/派生/重建检查全部通过；无后端状态时未知。",
            "behavior",
        ),
        _spec(
            SCOPE_LEAK_RATE,
            "ratio",
            "scenario",
            "lower_is_better",
            "跨作用域泄漏率：候选/注入/回答任一阶段越界的场景 / 可观察隔离场景。",
            "isolation",
        ),
        _spec(
            MEMORY_LIFT,
            "ratio",
            "dataset",
            "higher_is_better",
            "净收益参考：同一生成模型下有记忆与无持久记忆的配对场景分差值。",
            "answer",
        ),
        _spec(
            WRITE_LATENCY_P50_MS,
            "ms",
            "dataset",
            "lower_is_better",
            "写入阶段时延 p50；未知样本不补 0，只减少样本数。",
            "cost",
        ),
        _spec(
            WRITE_LATENCY_P95_MS,
            "ms",
            "dataset",
            "lower_is_better",
            "写入阶段时延 p95（nearest-rank）。",
            "cost",
        ),
        _spec(
            INDEX_LATENCY_P50_MS,
            "ms",
            "dataset",
            "lower_is_better",
            "索引阶段时延 p50。",
            "cost",
        ),
        _spec(
            INDEX_LATENCY_P95_MS,
            "ms",
            "dataset",
            "lower_is_better",
            "索引阶段时延 p95（nearest-rank）。",
            "cost",
        ),
        _spec(
            RETRIEVE_LATENCY_P50_MS,
            "ms",
            "dataset",
            "lower_is_better",
            "召回阶段时延 p50。",
            "cost",
        ),
        _spec(
            RETRIEVE_LATENCY_P95_MS,
            "ms",
            "dataset",
            "lower_is_better",
            "召回阶段时延 p95（nearest-rank）。",
            "cost",
        ),
        _spec(
            INJECT_LATENCY_P50_MS,
            "ms",
            "dataset",
            "lower_is_better",
            "注入阶段时延 p50。",
            "cost",
        ),
        _spec(
            INJECT_LATENCY_P95_MS,
            "ms",
            "dataset",
            "lower_is_better",
            "注入阶段时延 p95（nearest-rank）。",
            "cost",
        ),
        _spec(
            EXTRACTION_TOKENS,
            "token",
            "dataset",
            "lower_is_better",
            "抽取阶段 Token 合计；未知保持 null。",
            "cost",
        ),
        _spec(
            DERIVATION_TOKENS,
            "token",
            "dataset",
            "lower_is_better",
            "派生阶段 Token 合计；未知保持 null。",
            "cost",
        ),
        _spec(
            EMBEDDING_TOKENS,
            "token",
            "dataset",
            "lower_is_better",
            "嵌入阶段 Token 合计；未知保持 null。",
            "cost",
        ),
        _spec(
            READER_TOKENS,
            "token",
            "dataset",
            "lower_is_better",
            "阅读/回答阶段 Token 合计；未知保持 null。",
            "cost",
        ),
        _spec(
            JUDGE_TOKENS,
            "token",
            "dataset",
            "lower_is_better",
            "Judge 阶段 Token 合计；未知保持 null。",
            "cost",
        ),
        _spec(
            EMBEDDING_CALLS,
            "count",
            "dataset",
            "lower_is_better",
            "嵌入调用次数合计。",
            "cost",
        ),
        _spec(
            MODEL_REQUESTS,
            "count",
            "dataset",
            "lower_is_better",
            "物理模型请求数合计（含重试）。",
            "cost",
        ),
        _spec(
            STORAGE_BYTES,
            "bytes",
            "dataset",
            "lower_is_better",
            "记忆存储字节合计；未知保持 null。",
            "cost",
        ),
        _spec(
            ESTIMATED_COST,
            "currency",
            "dataset",
            "lower_is_better",
            "估算费用；单价未知时为 null，不填 0。",
            "cost",
        ),
    )
)

#: 全部指标 ID（排序固定，便于快照与报告比对）。
METRIC_IDS: tuple[str, ...] = tuple(sorted(METRIC_SPECS))


# --------------------------------------------------------------------------- 计数口径


def extraction_prf(
    tp: int, fp: int, fn: int
) -> tuple[float | None, float | None, float | None]:
    """抽取 P/R/F1（plan §7 钉死的口径）。

    ``precision = TP/(TP+FP)``、``recall = TP/(TP+FN)``、``f1 = 2TP/(2TP+FP+FN)``。
    F1 用计数直接算：P/R 任一为 null 时也不会把漏记藏起来。
    分母为 0 时返回 ``None``——不返回 0。
    """
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    return precision, recall, f1


# --------------------------------------------------------------------------- 构建


def coverage_of(
    scored: int, planned: int, reasons: Sequence[str] = ()
) -> MemoryCoverage:
    """覆盖度：已评分 / 计划；未知原因逐条留痕。"""
    return MemoryCoverage(scored=scored, planned=planned, reasons=list(reasons))


def build_metric(
    metric_id: str,
    *,
    value: float | None,
    status: MemoryMetricStatus,
    scope: MemoryMetricScope | None = None,
    numerator: float | None = None,
    denominator: float | None = None,
    grading_revision: int = 1,
    coverage: MemoryCoverage | None = None,
    reasons: Sequence[str] = (),
    evidence_refs: Sequence[str] = (),
    grader_fingerprint: str | None = None,
    calibration_status: str | None = None,
    review_digest: str | None = None,
) -> MemoryMetricResult:
    """按注册表口径构造一个指标结果。"""
    spec = METRIC_SPECS.get(metric_id)
    if spec is None:
        raise ValueError(f"METRIC_UNKNOWN:{metric_id}")
    return MemoryMetricResult(
        id=spec.id,
        version=METRICS_VERSION,
        grading_revision=grading_revision,
        review_digest=review_digest,
        unit=spec.unit,
        value=value,
        numerator=numerator,
        denominator=denominator,
        status=status,
        scope=scope or spec.scope,
        evidence_refs=list(evidence_refs),
        reasons=list(reasons),
        coverage=coverage or MemoryCoverage(),
        grader_fingerprint=grader_fingerprint,
        calibration_status=calibration_status,
    )


def ratio_metric(
    metric_id: str,
    *,
    numerator: float | int | None,
    denominator: float | int | None,
    status: MemoryMetricStatus | None = None,
    reasons: Sequence[str] = (),
    **kwargs: object,
) -> MemoryMetricResult:
    """比例指标。

    - 分母未知（``None``）→ ``not_assessable``：缺证不是 0；
    - 分母为 0 → ``not_applicable``：这一类里没有可判定项；
    - 其余 → ``scored``，值 = 分子/分母。
    """
    if status is not None:
        if status == "scored":
            if numerator is None or denominator is None or denominator <= 0:
                return build_metric(
                    metric_id,
                    value=None,
                    numerator=None if numerator is None else float(numerator),
                    denominator=None if denominator is None else float(denominator),
                    status="not_assessable",
                    reasons=[*reasons, "UNKNOWN_DENOMINATOR"],
                    **kwargs,  # type: ignore[arg-type]
                )
            return build_metric(
                metric_id,
                value=float(numerator) / float(denominator),
                numerator=float(numerator),
                denominator=float(denominator),
                status="scored",
                reasons=list(reasons),
                **kwargs,  # type: ignore[arg-type]
            )
        return build_metric(
            metric_id,
            value=None,
            numerator=None if numerator is None else float(numerator),
            denominator=None if denominator is None else float(denominator),
            status=status,
            reasons=list(reasons),
            **kwargs,  # type: ignore[arg-type]
        )
    if numerator is None or denominator is None:
        return build_metric(
            metric_id,
            value=None,
            numerator=None if numerator is None else float(numerator),
            denominator=None if denominator is None else float(denominator),
            status="not_assessable",
            reasons=[*reasons, "UNKNOWN_DENOMINATOR"],
            **kwargs,  # type: ignore[arg-type]
        )
    if denominator <= 0:
        return build_metric(
            metric_id,
            value=None,
            numerator=float(numerator),
            denominator=float(denominator),
            status="not_applicable",
            reasons=[*reasons, "EMPTY_DENOMINATOR"],
            **kwargs,  # type: ignore[arg-type]
        )
    return build_metric(
        metric_id,
        value=float(numerator) / float(denominator),
        numerator=float(numerator),
        denominator=float(denominator),
        status="scored",
        reasons=list(reasons),
        **kwargs,  # type: ignore[arg-type]
    )


def total_metric(
    metric_id: str,
    *,
    value: float | int | None,
    reasons: Sequence[str] = (),
    **kwargs: object,
) -> MemoryMetricResult:
    """绝对量指标（Token / 字节 / 次数 / 费用）。

    未知就是 ``None`` + ``not_assessable``：费用未知不填 0。
    """
    if value is None:
        return build_metric(
            metric_id,
            value=None,
            status="not_assessable",
            reasons=[*reasons, "UNKNOWN_VALUE"],
            **kwargs,  # type: ignore[arg-type]
        )
    return build_metric(
        metric_id,
        value=float(value),
        numerator=float(value),
        status="scored",
        reasons=list(reasons),
        **kwargs,  # type: ignore[arg-type]
    )


def latency_metric(
    metric_id: str,
    *,
    samples: Sequence[float | int | None],
    planned: int | None = None,
    quantile: Literal["p50", "p95"] = "p50",
    reasons: Sequence[str] = (),
    **kwargs: object,
) -> MemoryMetricResult:
    """时延指标。

    未知样本被排除而不是补 0；覆盖度记录"计划了几次、测到几次"。
    """
    known = [
        float(item) for item in samples if isinstance(item, (int, float)) and not isinstance(item, bool)
    ]
    expected = len(samples) if planned is None else int(planned)
    all_reasons = [*reasons]
    if len(known) != len(samples):
        all_reasons.append("UNKNOWN_SAMPLES_EXCLUDED")
    coverage = coverage_of(len(known), expected, all_reasons)
    if not known:
        return build_metric(
            metric_id,
            value=None,
            status="not_assessable",
            coverage=coverage,
            reasons=all_reasons,
            **kwargs,  # type: ignore[arg-type]
        )
    ordered = sorted(known)
    value = _median(ordered) if quantile == "p50" else _p95_nearest_rank(ordered)
    return build_metric(
        metric_id,
        value=value,
        numerator=sum(known),
        denominator=float(len(known)),
        status="scored",
        coverage=coverage,
        reasons=all_reasons,
        **kwargs,  # type: ignore[arg-type]
    )


def memory_lift(
    pairs: Sequence[tuple[float, float]],
    **kwargs: object,
) -> MemoryMetricResult:
    """Memory Lift：配对场景分的候选减基线差值（等权平均）。

    每个元素是 ``(baseline_scene_score, candidate_scene_score)``；返回值是差值的
    平均（比例，报告按百分点展示）。没有配对时未知，不返回 0。
    """
    if not pairs:
        return build_metric(
            MEMORY_LIFT,
            value=None,
            status="not_assessable",
            reasons=["NO_PAIRS"],
            **kwargs,  # type: ignore[arg-type]
        )
    deltas = [float(candidate) - float(baseline) for baseline, candidate in pairs]
    return build_metric(
        MEMORY_LIFT,
        value=sum(deltas) / len(deltas),
        numerator=sum(deltas),
        denominator=float(len(deltas)),
        status="scored",
        coverage=coverage_of(len(deltas), len(deltas)),
        **kwargs,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- 小工具


def _median(ordered: Sequence[float]) -> float:
    """偶数样本取中间两项平均（与 reports.py 的口径一致）。"""
    count = len(ordered)
    middle = count // 2
    if count % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def _p95_nearest_rank(ordered: Sequence[float]) -> float:
    """nearest-rank：第 ``ceil(0.95 * n)`` 项（1 基），不做插值。"""
    rank = math.ceil(0.95 * len(ordered))
    return float(ordered[max(rank - 1, 0)])


def iter_specs(stage: str | None = None) -> Iterable[MetricSpec]:
    """按阶段过滤指标（成本类指标单独取用）。"""
    for metric_id in METRIC_IDS:
        spec = METRIC_SPECS[metric_id]
        if stage is None or spec.stage == stage:
            yield spec


__all__ = [
    "METRIC_IDS",
    "METRIC_SPECS",
    "METRICS_VERSION",
    "MetricSpec",
    "build_metric",
    "coverage_of",
    "extraction_prf",
    "iter_specs",
    "latency_metric",
    "memory_lift",
    "ratio_metric",
    "total_metric",
]
