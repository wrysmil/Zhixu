"""知识检索指标口径（契约 §3.2 / 知识检索评测台契约）。

只做算术，不做语义判定——相关性标签由 ``judge`` 或 ``expect`` 给出。

四条口径硬约束：

1. ``recall_at_k`` 的分母是**候选池中** grade≥2 的块数（池相对），
   不是全库相关块数。
2. 池里没有相关块 → ``recall_at_k`` 为 ``None`` 且该题 ``not_assessable``；
   其它指标（hit/precision/mrr）仍照常给值。
3. 聚合按 ``label_source`` 分组，**不跨组混算**。
4. 缺证据是 ``None``，绝不补 0。

聚合方式：``precision_at_k`` / ``recall_at_k`` / ``mrr`` / ``ndcg_at_k`` 是
macro（逐题值再平均）；``hit_at_k`` / ``pool_coverage`` / ``unscored_rate``
是 micro（整体比值）。

本模块的 :data:`METRIC_SPECS` 是**指标公式的唯一真相源**：后端用它算、
``GET /knowledge/metrics`` 用它下发、首页公式区渲染它、报告用它标注。
公式串是契约的一部分，改公式必须先改契约。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

#: 相关性阈值：grade >= 2 视为相关（对齐 UMBRELA 的 >=2 二值化）。
RELEVANT_GRADE = 2

# --------------------------------------------------------------------------- 指标 ID

HIT_AT_K = "hit_at_k"
PRECISION_AT_K = "precision_at_k"
MRR = "mrr"
NDCG_AT_K = "ndcg_at_k"
RECALL_AT_K = "recall_at_k"
POOL_COVERAGE = "pool_coverage"
UNSCORED_RATE = "unscored_rate"

#: 全部指标 ID（顺序固定，便于快照与报告比对）。
METRIC_IDS: tuple[str, ...] = (
    HIT_AT_K,
    PRECISION_AT_K,
    MRR,
    NDCG_AT_K,
    RECALL_AT_K,
    POOL_COVERAGE,
    UNSCORED_RATE,
)

#: 逐题值再平均（macro）的指标。
MACRO_METRIC_IDS: tuple[str, ...] = (
    PRECISION_AT_K,
    RECALL_AT_K,
    MRR,
    NDCG_AT_K,
)

#: 整体比值（micro）的指标。
MICRO_METRIC_IDS: tuple[str, ...] = (
    HIT_AT_K,
    POOL_COVERAGE,
    UNSCORED_RATE,
)

MetricDirection = Literal["higher_is_better", "lower_is_better"]


@dataclass(frozen=True)
class MetricSpec:
    """一个指标的静态口径——**公式的唯一真相源**。"""

    id: str
    label: str
    formula: str
    numerator: str
    denominator: str
    direction: MetricDirection
    description: str


#: 指标口径注册表：后端用它算、``GET /knowledge/metrics`` 用它下发、
#: 首页公式区渲染它。前端**不得**在 i18n 里另抄一份公式。
METRIC_SPECS: dict[str, MetricSpec] = {
    spec.id: spec
    for spec in (
        MetricSpec(
            id=PRECISION_AT_K,
            label="准确率",
            formula="相关块数(top-k) ÷ k",
            numerator="top-k 中 grade≥2 的块数",
            denominator="k",
            direction="higher_is_better",
            description="返回结果里有多少块真的回答了问题。",
        ),
        MetricSpec(
            id=RECALL_AT_K,
            label="召回率",
            formula="相关块数(top-k) ÷ 相关块数(pool)",
            numerator="top-k 中 grade≥2 的块数",
            denominator="候选池中 grade≥2 的块数",
            direction="higher_is_better",
            description="池相对召回：分母是本次候选池中判定为相关的块数，不是全库相关块总数。",
        ),
        MetricSpec(
            id=HIT_AT_K,
            label="命中率",
            formula="命中查询数 ÷ 可评查询数",
            numerator="top-k 含 ≥1 相关块的查询数",
            denominator="全部可评查询数",
            direction="higher_is_better",
            description="有多少条查询至少找到一条相关块。",
        ),
        MetricSpec(
            id=MRR,
            label="首个命中倒数",
            formula="平均( 1 ÷ 首个相关块名次 )",
            numerator="—",
            denominator="—",
            direction="higher_is_better",
            description="相关块排得越靠前越高；无命中记 0。",
        ),
        MetricSpec(
            id=NDCG_AT_K,
            label="归一化折损累计增益",
            formula="DCG ÷ IDCG，其中 DCG = Σ (2^grade − 1) ÷ log₂(名次+1)",
            numerator="—",
            denominator="—",
            direction="higher_is_better",
            description="兼顾分级相关性与排名位置。",
        ),
        MetricSpec(
            id=POOL_COVERAGE,
            label="候选池相关占比",
            formula="相关块数(pool) ÷ 池候选总数",
            numerator="池中 grade≥2 的块数",
            denominator="池候选总数",
            direction="higher_is_better",
            description="池本身的质量；它太低时召回率没有意义。",
        ),
        MetricSpec(
            id=UNSCORED_RATE,
            label="未评率",
            formula="not_assessable 查询数 ÷ 总查询数",
            numerator="not_assessable 查询数",
            denominator="全部查询数",
            direction="lower_is_better",
            description="缺证据的查询占比；这些题不参与其它指标的平均。",
        ),
    )
}


@dataclass(frozen=True)
class KnowledgeCaseScore:
    """单题得分。

    ``pool_relevant`` / ``pool_size`` 是候选池的原始计数：micro 聚合
    （``pool_coverage``）需要整体比值，不能只靠逐题比值还原。
    """

    case_id: str
    label_source: str
    status: str
    metrics: dict[str, float | None] = field(default_factory=dict)
    reason: str = ""
    pool_relevant: int = 0
    pool_size: int = 0


# --------------------------------------------------------------------------- 内部工具


def _is_relevant(grade: int | None) -> bool:
    """grade >= 2 视为相关。"""
    return isinstance(grade, int) and grade >= RELEVANT_GRADE


def _dcg(grades: Sequence[int]) -> float:
    """DCG = Σ (2^grade − 1) ÷ log₂(名次+1)（名次从 1 开始）。"""
    return sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades))


def pool_coverage(pool_grades: dict[str, int], pool_size: int) -> float | None:
    """池中相关块数 / 池候选总数。池为空 → ``None``（缺证据不是 0）。"""
    if pool_size <= 0:
        return None
    relevant = sum(1 for grade in pool_grades.values() if _is_relevant(grade))
    return relevant / pool_size


# --------------------------------------------------------------------------- 逐题打分


def case_score(
    *,
    case_id: str,
    label_source: str,
    top_k: list[tuple[str, int]],
    pool_grades: dict[str, int],
    k: int,
    pool_size: int | None = None,
) -> KnowledgeCaseScore:
    """给单题打分。

    ``top_k`` 是按名次排列的 ``(chunk_id, grade)``；``pool_grades`` 是整池的
    ``chunk_id -> grade``（池相对 recall 的分母来源）。``pool_size`` 是池候选
    总数，默认取 ``len(pool_grades)``；池里有未打标候选时可显式传入。

    - ``recall_at_k`` 的分母是**候选池中** grade≥2 的块数；池里没有相关块 →
      ``recall_at_k`` 为 ``None`` 且该题 ``not_assessable``，其它指标照常给值。
    - ``mrr`` 取**首个**相关块的倒数名次（不是多个相关块的平均）。
    - ``ndcg_at_k`` 用指数增益 ``2^grade − 1``；IDCG 用池内 grade 降序的
      理想排列前 k 个，IDCG 为 0 时记 0.0。
    """
    if k <= 0:
        raise ValueError("k must be positive")

    ranked = list(top_k)[:k]
    grades = [grade for _, grade in ranked]
    relevant_hits = sum(1 for grade in grades if _is_relevant(grade))

    pool_relevant = sum(1 for grade in pool_grades.values() if _is_relevant(grade))
    total_pool_size = len(pool_grades) if pool_size is None else pool_size

    metrics: dict[str, float | None] = {
        HIT_AT_K: 1.0 if relevant_hits > 0 else 0.0,
        PRECISION_AT_K: relevant_hits / k,
        MRR: 0.0,
        NDCG_AT_K: 0.0,
        RECALL_AT_K: None,
        POOL_COVERAGE: pool_coverage(pool_grades, total_pool_size),
    }

    for index, grade in enumerate(grades):
        if _is_relevant(grade):
            metrics[MRR] = 1.0 / (index + 1)
            break

    if pool_relevant > 0:
        metrics[RECALL_AT_K] = relevant_hits / pool_relevant
    ideal = sorted(pool_grades.values(), reverse=True)[:k]
    idcg = _dcg(ideal)
    metrics[NDCG_AT_K] = (_dcg(grades) / idcg) if idcg > 0 else 0.0

    if pool_relevant > 0:
        status = "scored"
        reason = ""
    else:
        # 池里没有相关块：recall 无意义，题目不可评；其它指标照常给值。
        status = "not_assessable"
        reason = "POOL_HAS_NO_RELEVANT"

    return KnowledgeCaseScore(
        case_id=case_id,
        label_source=label_source,
        status=status,
        metrics=metrics,
        reason=reason,
        pool_relevant=pool_relevant,
        pool_size=total_pool_size,
    )


# --------------------------------------------------------------------------- 聚合


def aggregate_metrics(scores: list[KnowledgeCaseScore]) -> dict[str, dict[str, Any]]:
    """按标注来源分组聚合；组内再按指标口径聚合。

    - macro（``precision_at_k`` / ``recall_at_k`` / ``mrr`` / ``ndcg_at_k``）：
      逐题值再平均；``not_assessable`` 的题不参与平均。
    - micro（``hit_at_k`` / ``pool_coverage`` / ``unscored_rate``）：整体比值。
      ``hit_at_k`` = 命中查询数 ÷ 可评查询数；``pool_coverage`` =
      池中相关块数合计 ÷ 池候选总数合计；``unscored_rate`` =
      ``not_assessable`` 查询数 ÷ 总查询数。
    - 缺证据（无值、无可评查询）记 ``None``，绝不补 0。
    """
    buckets: dict[str, dict[str, Any]] = {}
    for score in scores:
        bucket = buckets.setdefault(
            score.label_source,
            {
                "total_queries": 0,
                "scored_queries": 0,
                "hit_queries": 0,
                "pool_relevant": 0,
                "pool_size": 0,
                "values": {},
            },
        )
        bucket["total_queries"] += 1
        if score.status != "scored":
            continue
        bucket["scored_queries"] += 1
        hit = score.metrics.get(HIT_AT_K)
        if hit is not None and hit > 0:
            bucket["hit_queries"] += 1
        bucket["pool_relevant"] += score.pool_relevant
        bucket["pool_size"] += score.pool_size
        for metric_id, value in score.metrics.items():
            if value is None:
                continue
            bucket["values"].setdefault(metric_id, []).append(value)

    groups: dict[str, dict[str, Any]] = {}
    for label_source, bucket in buckets.items():
        total = bucket["total_queries"]
        scored = bucket["scored_queries"]
        unscored_rate = (total - scored) / total if total else None

        metrics: dict[str, float | None] = {}
        for metric_id in METRIC_IDS:
            if metric_id == HIT_AT_K:
                # micro：命中查询数 ÷ 可评查询数
                metrics[metric_id] = (
                    bucket["hit_queries"] / scored if scored else None
                )
            elif metric_id == POOL_COVERAGE:
                # micro：池中相关块数合计 ÷ 池候选总数合计
                pool_size = bucket["pool_size"]
                metrics[metric_id] = (
                    bucket["pool_relevant"] / pool_size if pool_size else None
                )
            elif metric_id == UNSCORED_RATE:
                metrics[metric_id] = unscored_rate
            else:
                # macro：逐题值再平均
                values = bucket["values"].get(metric_id, [])
                metrics[metric_id] = (
                    sum(values) / len(values) if values else None
                )

        groups[label_source] = {
            "total_queries": total,
            "scored_queries": scored,
            "metrics": metrics,
            "metric_status": {
                metric_id: ("scored" if metrics[metric_id] is not None else "not_assessable")
                for metric_id in METRIC_IDS
            },
            "unscored_rate": unscored_rate,
        }
    return groups


__all__ = [
    "HIT_AT_K",
    "KnowledgeCaseScore",
    "MACRO_METRIC_IDS",
    "METRIC_IDS",
    "METRIC_SPECS",
    "MICRO_METRIC_IDS",
    "MRR",
    "NDCG_AT_K",
    "POOL_COVERAGE",
    "PRECISION_AT_K",
    "RECALL_AT_K",
    "RELEVANT_GRADE",
    "UNSCORED_RATE",
    "aggregate_metrics",
    "case_score",
    "pool_coverage",
]
