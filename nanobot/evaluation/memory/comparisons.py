"""记忆版本配对比较、cluster 区间与采用判定（契约 §5 / §8.5）。

本模块只做**只读聚合**：输入是两份已冻结的实验记录与它们的 attempt，输出是一份
不可变比较快照（:class:`MemoryComparisonResult`）。它不读数据库、不读环境变量、
不发起模型请求，因此同一份输入两次调用得到逐字节相同的输出。

四条硬约束（契约 §8.5 / §5）：

1. **条件先于分数**：双方实验必须终态；冻结清单必须完整；数据集修订、轨道、
   模型模式、重复次数、gold/判分摘要一致；场景 revision 不得不同。任一不满足
   直接拒绝（``COMPARISON_INCOMPATIBLE`` / ``COMPARISON_METADATA_MISSING`` /
   ``MEMORY_EXPERIMENT_NOT_TERMINAL``），而不是先算个看起来不错的差值。
2. **实际变化必须是声明白名单的子集**：声明父指针即覆盖子树。未声明的模型/
   抽取/embedding 变化单独报 ``UNDECLARED_MODEL_CHANGE``——整体 memory-stack
   变更不能声称单因素因果。
3. **区间只按 cluster 整体重采样**：同一 conversation/cluster 的多道题一起进
   样本，固定 seed=42、2000 轮、95%、type7 分位数（``paired-cluster-percentile/v1``）。
   独立 cluster < 20 不输出区间；区间不提供确定性保证。
4. **统计方向与采用建议分开**：:func:`classify_change` 只返回
   ``improved/regressed/inconclusive``，区间跨 0 一律 inconclusive；
   :func:`adoption_decision` 另看 ``minimum_effect_pp``、分项退化容忍度、成本/
   延迟预算与关键门禁。缺题、缺重复、未校准 → 主指标不完整，不能用子集提升
   自动采用；关键作用域泄漏/删除复活直接 ``blocked``。

分页只影响逐题列表：:func:`page_comparison_pairs` 从同一份结果里切页，统计量
永远基于全量配对场景，不会因为分页或"未判定"改变分母。
"""

from __future__ import annotations

import copy
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from nanobot.evaluation.memory.manifests import (
    MemoryManifestIncompatible,
    MemoryManifestIncomplete,
    changed_pointers,
    require_declared_changes,
    require_strict_metadata,
)
from nanobot.evaluation.memory.metrics import (
    DERIVATION_TOKENS,
    EMBEDDING_CALLS,
    EMBEDDING_TOKENS,
    ESTIMATED_COST,
    EXTRACTION_TOKENS,
    FORGET_BEHAVIOR_SUCCESS,
    INDEX_LATENCY_P50_MS,
    INDEX_LATENCY_P95_MS,
    INJECT_LATENCY_P50_MS,
    INJECT_LATENCY_P95_MS,
    JUDGE_TOKENS,
    METRIC_SPECS,
    MODEL_REQUESTS,
    QA_ACCURACY,
    QA_ACCURACY_CATEGORY_MACRO,
    QA_ACCURACY_PROBE_MICRO,
    QA_ACCURACY_SCENARIO_MACRO,
    READER_TOKENS,
    RETRIEVE_LATENCY_P50_MS,
    RETRIEVE_LATENCY_P95_MS,
    SCOPE_LEAK_RATE,
    STORAGE_BYTES,
    WRITE_LATENCY_P50_MS,
    WRITE_LATENCY_P95_MS,
)
from nanobot.evaluation.memory.models import MemoryAdoptionPolicy

#: 契约 §8.5：配对 cluster 区间算法版本（与 grading.BOOTSTRAP_VERSION 一致）。
BOOTSTRAP_VERSION = "paired-cluster-percentile/v1"
BOOTSTRAP_SEED = 42
BOOTSTRAP_ROUNDS = 2000
BOOTSTRAP_CONFIDENCE = 0.95
#: 显示门槛：独立 cluster 少于 20 个不输出区间（不是显著性保证）。
MIN_CLUSTERS_FOR_INTERVAL = 20

#: 终态实验：只有终态才允许比较（进行中比较会读到半成品）。
TERMINAL_EXPERIMENT_STATUSES = frozenset(
    {"completed", "cancelled", "interrupted", "timed_out"}
)

#: 平台近似 Judge 指标：单独隔离，绝不与通用判分层/官方口径混算（契约 §9.1）。
OFFICIAL_ISOLATED_METRICS = frozenset({"longmemeval_binary_judge"})

#: 场景级主指标：由逐 probe 的 qa_correct 聚合（契约 §8.4）。
_QA_SCENE_METRICS = frozenset(
    {QA_ACCURACY_SCENARIO_MACRO, QA_ACCURACY_PROBE_MICRO, QA_ACCURACY_CATEGORY_MACRO, QA_ACCURACY}
)

IMPROVED = "improved"
REGRESSED = "regressed"
UNCHANGED = "unchanged"
INCOMPARABLE = "incomparable"

ADOPTABLE = "adoptable"
BLOCKED = "blocked"
REJECTED = "rejected"
REVIEW_REQUIRED = "review_required"
INSUFFICIENT = "insufficient"
DIAGNOSTIC_ONLY = "diagnostic_only"

#: 候选身份字段只作选择标签，不是"被测条件"，不要求声明。
_CANDIDATE_IDENTITY_SUFFIXES = ("/id", "/name")

#: 看起来像模型/抽取/嵌入的指针：这类变化必须显式声明（契约 §5）。
_MODEL_POINTER_HINTS = ("model", "embedding", "extractor", "reader")

#: 成本拆分的三组（plan Task 8 Step 5：写入 / 查询 / Judge）。
COST_GROUPS: dict[str, tuple[str, ...]] = {
    "write": (
        WRITE_LATENCY_P50_MS,
        WRITE_LATENCY_P95_MS,
        INDEX_LATENCY_P50_MS,
        INDEX_LATENCY_P95_MS,
        EXTRACTION_TOKENS,
        DERIVATION_TOKENS,
        EMBEDDING_TOKENS,
        EMBEDDING_CALLS,
        STORAGE_BYTES,
    ),
    "query": (
        RETRIEVE_LATENCY_P50_MS,
        RETRIEVE_LATENCY_P95_MS,
        INJECT_LATENCY_P50_MS,
        INJECT_LATENCY_P95_MS,
        READER_TOKENS,
    ),
    "judge": (JUDGE_TOKENS,),
    "totals": (MODEL_REQUESTS, ESTIMATED_COST),
}

#: 可累加的绝对量指标；其余成本指标（时延）按 trial 值取平均。
_SUM_METRICS = frozenset(
    {
        EXTRACTION_TOKENS,
        DERIVATION_TOKENS,
        EMBEDDING_TOKENS,
        READER_TOKENS,
        JUDGE_TOKENS,
        EMBEDDING_CALLS,
        MODEL_REQUESTS,
        STORAGE_BYTES,
        ESTIMATED_COST,
    }
)

#: 时延指标：数据集级数值取 trial 值的平均（不伪造 p95，字段里写明聚合方式）。
_LATENCY_METRICS = frozenset(
    name
    for name in (
        WRITE_LATENCY_P50_MS,
        WRITE_LATENCY_P95_MS,
        INDEX_LATENCY_P50_MS,
        INDEX_LATENCY_P95_MS,
        RETRIEVE_LATENCY_P50_MS,
        RETRIEVE_LATENCY_P95_MS,
        INJECT_LATENCY_P50_MS,
        INJECT_LATENCY_P95_MS,
    )
)


# =========================================================================== 错误


class MemoryComparisonError(RuntimeError):
    """比较门禁错误；``code`` 与契约 §6 的错误码一致。"""

    code = "COMPARISON_INCOMPATIBLE"

    def __init__(self, message: str = "") -> None:
        super().__init__(f"{self.code}: {message}" if message else self.code)
        self.message = message


class MemoryComparisonMetadataMissing(MemoryComparisonError):  # noqa: N818 - 名称由错误码映射锁定
    """冻结清单缺实际值（数据集/判分/源码）→ 422。"""

    code = "COMPARISON_METADATA_MISSING"


class MemoryComparisonIncompatible(MemoryComparisonError):  # noqa: N818 - 名称由错误码映射锁定
    """条件不匹配，或实际变化超出声明白名单 → 409。"""

    code = "COMPARISON_INCOMPATIBLE"


class MemoryExperimentNotTerminal(MemoryComparisonError):  # noqa: N818 - 名称由错误码映射锁定
    """实验尚未终态 → 409。"""

    code = "MEMORY_EXPERIMENT_NOT_TERMINAL"


# =========================================================================== 读模型


@dataclass(frozen=True)
class _TrialView:
    """一次 trial 的只读视图（按 trial_index 去重后）。"""

    scenario_id: str
    revision: int
    trial_index: int
    categories: tuple[str, ...]
    probes: tuple[Mapping[str, Any], ...]
    metrics: Mapping[str, Mapping[str, Any]]
    required_metrics: tuple[str, ...]


@dataclass(frozen=True)
class _SceneScore:
    """场景级聚合结论：值、覆盖与原因。"""

    value: float | None
    trials: int
    probes_scored: int
    probes_planned: int
    probe_rates: Mapping[str, float | None] = field(default_factory=dict)
    micro_correct: int | None = None
    micro_total: int | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenePair:
    """一个配对场景的逐题变化。"""

    scenario_id: str
    revision: int
    cluster_id: str
    category: str
    categories: tuple[str, ...]
    status: str
    baseline: float | None
    candidate: float | None
    delta: float | None
    delta_pp: float | None
    baseline_trials: int
    candidate_trials: int
    probe_changes: tuple[Mapping[str, Any], ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "revision": self.revision,
            "cluster_id": self.cluster_id,
            "category": self.category,
            "categories": list(self.categories),
            "status": self.status,
            "baseline": self.baseline,
            "candidate": self.candidate,
            "delta": self.delta,
            "delta_pp": self.delta_pp,
            "baseline_trials": self.baseline_trials,
            "candidate_trials": self.candidate_trials,
            "probe_changes": [dict(item) for item in self.probe_changes],
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class MemoryComparisonResult:
    """不可变比较结果；``to_dict`` 返回深拷贝，调用方改不动快照。"""

    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self.payload))

    @property
    def status(self) -> str:
        return str(self.payload.get("status") or "")

    @property
    def statistical_direction(self) -> str:
        return str(self.payload.get("statistical_direction") or INCOMPARABLE)

    @property
    def adoption_decision(self) -> str:
        adoption = self.payload.get("adoption_decision") or {}
        return str((adoption or {}).get("decision") or "")


# =========================================================================== 小工具


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _ratio(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None or baseline == 0:
        return None
    return candidate / baseline


def _experiment_record(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """接受 ``{"experiment": ..., "child_runs": ...}`` 或实验记录本身。"""
    nested = value.get("experiment")
    return _mapping(nested) if isinstance(nested, Mapping) else value


def _manifest_of(record: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(record.get("manifest"))


def _scenario_index(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    dataset = _mapping(manifest.get("dataset"))
    index: dict[str, Mapping[str, Any]] = {}
    for item in _sequence(dataset.get("scenarios")):
        row = _mapping(item)
        scenario_id = str(row.get("scenario_id") or "")
        if scenario_id:
            index[scenario_id] = row
    return index


def _cluster_of(index: Mapping[str, Mapping[str, Any]], scenario_id: str) -> str:
    row = index.get(scenario_id) or {}
    return str(row.get("cluster_id") or scenario_id)


def _candidate_block(manifest: Mapping[str, Any], candidate_id: str | None) -> Mapping[str, Any]:
    blocks = [_mapping(item) for item in _sequence(manifest.get("candidates"))]
    if not blocks:
        raise MemoryComparisonMetadataMissing("manifest.candidates")
    if candidate_id is None:
        return blocks[0]
    for block in blocks:
        if str(block.get("id") or "") == candidate_id:
            return block
    raise MemoryComparisonIncompatible(f"CANDIDATE_NOT_IN_MANIFEST:{candidate_id}")


def _restricted_manifest(
    manifest: Mapping[str, Any], candidate_id: str | None
) -> dict[str, Any]:
    """把清单收窄到实际参与比较的那一个候选（避免别的候选差异干扰判定）。"""
    view = copy.deepcopy(dict(manifest))
    view["candidates"] = [copy.deepcopy(dict(_candidate_block(manifest, candidate_id)))]
    return view


def _is_ignored_pointer(pointer: str) -> bool:
    return pointer.endswith(_CANDIDATE_IDENTITY_SUFFIXES)


def _is_model_pointer(pointer: str) -> bool:
    lowered = pointer.lower()
    return any(hint in lowered for hint in _MODEL_POINTER_HINTS)


def _covered_by(pointer: str, declared: Sequence[str]) -> bool:
    return any(
        pointer == root or pointer.startswith(root.rstrip("/") + "/") for root in declared
    )


def _filter_condition_pointers(pointers: Sequence[str]) -> list[str]:
    return sorted(pointer for pointer in pointers if not _is_ignored_pointer(pointer))


_CANDIDATE_PREFIX_RE = re.compile(r"^/candidates/\d+(?=/|$)")


def _normalize_declared(pointer: str) -> str:
    """声明里的候选下标不参与匹配：比较视图只保留"被选中的那个候选"。

    否则用户按原始清单声明 ``/candidates/1/...`` 时会因为候选被收窄到下标 0
    而被误判成未声明。
    """
    return _CANDIDATE_PREFIX_RE.sub("/candidates/0", pointer)


# =========================================================================== 门禁


def _require_terminal(record: Mapping[str, Any], side: str) -> None:
    status = str(record.get("status") or "")
    if status not in TERMINAL_EXPERIMENT_STATUSES:
        raise MemoryExperimentNotTerminal(f"{side}={status or 'unknown'}")


def _require_manifest(record: Mapping[str, Any], side: str) -> Mapping[str, Any]:
    manifest = _manifest_of(record)
    if not manifest:
        raise MemoryComparisonMetadataMissing(f"{side}.manifest")
    try:
        require_strict_metadata(manifest)
    except MemoryManifestIncomplete as exc:
        raise MemoryComparisonMetadataMissing(str(exc)) from exc
    except MemoryManifestIncompatible as exc:  # pragma: no cover - 防御性分支
        raise MemoryComparisonIncompatible(str(exc)) from exc
    return manifest


def _require_same_condition(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> None:
    """不可声明为"被测变量"的条件必须完全一致（数据集/gold/轨道/重复）。"""
    base_dataset = _mapping(baseline.get("dataset"))
    cand_dataset = _mapping(candidate.get("dataset"))
    base_grading = _mapping(baseline.get("grading"))
    cand_grading = _mapping(candidate.get("grading"))
    base_env = _mapping(baseline.get("environment"))
    cand_env = _mapping(candidate.get("environment"))
    pairs = (
        ("dataset.dataset_revision_id", base_dataset.get("dataset_revision_id"),
         cand_dataset.get("dataset_revision_id")),
        ("dataset.dataset_version", base_dataset.get("dataset_version"),
         cand_dataset.get("dataset_version")),
        ("dataset.split", base_dataset.get("split"), cand_dataset.get("split")),
        ("dataset.definition_digest", base_dataset.get("definition_digest"),
         cand_dataset.get("definition_digest")),
        ("dataset.scenario_digest", base_dataset.get("scenario_digest"),
         cand_dataset.get("scenario_digest")),
        ("grading.track", base_grading.get("track"), cand_grading.get("track")),
        ("grading.primary_metric", base_grading.get("primary_metric"),
         cand_grading.get("primary_metric")),
        ("grading.grading_digest", base_grading.get("grading_digest"),
         cand_grading.get("grading_digest")),
        ("environment.model_mode", base_env.get("model_mode"), cand_env.get("model_mode")),
        ("environment.repetitions", base_env.get("repetitions"), cand_env.get("repetitions")),
        ("environment.clock.mode", _mapping(base_env.get("clock")).get("mode"),
         _mapping(cand_env.get("clock")).get("mode")),
        ("environment.capabilities_digest", base_env.get("capabilities_digest"),
         cand_env.get("capabilities_digest")),
    )
    mismatched = [
        name for name, left, right in pairs if left is None or right is None or left != right
    ]
    if mismatched:
        raise MemoryComparisonIncompatible("CONDITION_MISMATCH " + ",".join(mismatched))


def _require_scenario_revisions(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> None:
    """同场景不同 revision 一律拒绝（gold/事件可能已经换过）。"""
    base_index = _scenario_index(baseline)
    cand_index = _scenario_index(candidate)
    for scenario_id in sorted(set(base_index) & set(cand_index)):
        left = int(_mapping(base_index[scenario_id]).get("revision") or 0)
        right = int(_mapping(cand_index[scenario_id]).get("revision") or 0)
        if left != right:
            raise MemoryComparisonIncompatible(
                f"SCENARIO_REVISION_MISMATCH:{scenario_id}:{left}!={right}"
            )


def _require_declared(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    baseline_candidate_id: str | None,
    candidate_candidate_id: str | None,
    declared_fields: Sequence[str],
) -> tuple[list[str], list[str], list[str]]:
    """返回 (条件变化, 模型变化, 源码状态变化)；未声明的条件一律拒绝。

    源码 SHA / dirty 摘要是**出处信息**，不是运行条件：比较"昨天基线 vs 今天候选"
    时代码本来就会不同（那正是被测变量），因此不要求写进 ``changed_fields``；
    但仍完整记录在 ``attribution.source_changes`` 里，绝不静默丢弃。
    """
    base_view = _restricted_manifest(baseline, baseline_candidate_id)
    cand_view = _restricted_manifest(candidate, candidate_candidate_id)
    raw_changes = _filter_condition_pointers(changed_pointers(base_view, cand_view))
    source_changes = [
        pointer for pointer in raw_changes if pointer.startswith("/source")
    ]
    changes = [
        pointer for pointer in raw_changes if not pointer.startswith("/source")
    ]
    declared = [_normalize_declared(str(item)) for item in declared_fields]
    model_changes = [pointer for pointer in changes if _is_model_pointer(pointer)]
    undeclared_models = [
        pointer for pointer in model_changes if not _covered_by(pointer, declared)
    ]
    if undeclared_models:
        raise MemoryComparisonIncompatible(
            "UNDECLARED_MODEL_CHANGE " + ",".join(undeclared_models[:8])
        )
    # 候选 id/name 只是"选了谁"的标签，不是被测条件；源码状态见上。
    effective = [
        *declared,
        "/candidates/0/id",
        "/candidates/0/name",
        "/source",
    ]
    try:
        require_declared_changes(base_view, cand_view, effective)
    except MemoryManifestIncompatible as exc:
        raise MemoryComparisonIncompatible(str(exc)) from exc
    except MemoryManifestIncomplete as exc:  # pragma: no cover - 防御性分支
        raise MemoryComparisonMetadataMissing(str(exc)) from exc
    return changes, model_changes, source_changes


# =========================================================================== attempt 读取


def _memory_grade(attempt: Mapping[str, Any]) -> Mapping[str, Any] | None:
    result = _mapping(attempt.get("result"))
    memory = _mapping(result.get("memory"))
    if not memory or memory.get("status") == "grading_error":
        return None
    return memory


def _attempt_scenario(attempt: Mapping[str, Any]) -> tuple[str, int]:
    memory = _memory_grade(attempt) or {}
    scenario_id = str(memory.get("scenario_id") or "")
    revision = memory.get("revision")
    if not scenario_id:
        ref = _mapping(_mapping(attempt.get("case_snapshot")).get("memory_scenario_ref"))
        scenario_id = str(ref.get("scenario_id") or "")
        revision = ref.get("revision")
    return scenario_id, int(revision or 0)


def _required_metrics(attempt: Mapping[str, Any]) -> tuple[str, ...]:
    snapshot = _mapping(attempt.get("case_snapshot"))
    return tuple(
        str(item) for item in _sequence(snapshot.get("memory_required_metrics")) if str(item)
    )


def _flatten_memory_result(memory: Mapping[str, Any]) -> Mapping[str, Any]:
    """把服务层存的 ``{"scenario_id",...,"metrics": <MemoryTrialGrade>}`` 摊平。

    WU-09 的评分端把整份 ``MemoryTrialGrade`` 放在 ``result.memory.metrics`` 里，
    而本模块的比较视图消费的是摊平后的 trial（``metrics`` 是列表、``probe_results``
    在顶层）。不摊平就会「指标一个都看不见」——比较仍然给出数字，只是全是未判定，
    等于把「没有测量」伪装成「没有差异」。
    """
    graded = memory.get("metrics")
    if not isinstance(graded, Mapping):
        return memory
    flattened = dict(graded)
    for key in ("scenario_id", "revision", "track"):
        if flattened.get(key) is None and memory.get(key) is not None:
            flattened[key] = memory.get(key)
    return flattened


def _trial_view(attempt: Mapping[str, Any]) -> _TrialView | None:
    # 技术失败不是分数：execution 未成功或评分失败的 attempt 一律不进聚合。
    execution_status = attempt.get("execution_status")
    if execution_status is not None and str(execution_status) != "succeeded":
        return None
    memory = _memory_grade(attempt)
    if memory is None:
        return None
    memory = _flatten_memory_result(memory)
    scenario_id, revision = _attempt_scenario(attempt)
    if not scenario_id:
        return None
    metrics: dict[str, Mapping[str, Any]] = {}
    for item in _sequence(memory.get("metrics")):
        row = _mapping(item)
        metric_id = str(row.get("id") or "")
        if metric_id:
            metrics[metric_id] = row
    probes = tuple(_mapping(item) for item in _sequence(memory.get("probe_results")))
    return _TrialView(
        scenario_id=scenario_id,
        revision=revision,
        trial_index=int(memory.get("trial_index") or 0),
        categories=tuple(str(item) for item in _sequence(memory.get("categories"))),
        probes=probes,
        metrics=metrics,
        required_metrics=_required_metrics(attempt),
    )


@dataclass
class _Collected:
    """一个实验的全部 trial（已按 trial_index 去重，重试单独计数）。"""

    trials: dict[str, list[_TrialView]]
    attempts: int
    graded: int
    duplicates: int
    ungraded: int
    revisions: dict[str, int]

    def values_of(self) -> list[_TrialView]:
        return [item for rows in self.trials.values() for item in rows]


def _collect(attempts: Sequence[Mapping[str, Any]]) -> _Collected:
    """按 (scenario_id, trial_index) 去重；重复 attempt 只算成本不参与评分。

    去重规则固定为"给定顺序里的第一条"，不挑最好一次（契约 §8.4）。
    """
    trials: dict[str, list[_TrialView]] = {}
    seen: set[tuple[str, int]] = set()
    revisions: dict[str, int] = {}
    duplicates = 0
    ungraded = 0
    graded = 0
    for attempt in attempts:
        view = _trial_view(attempt)
        if view is None:
            ungraded += 1
            continue
        graded += 1
        key = (view.scenario_id, view.trial_index)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        trials.setdefault(view.scenario_id, []).append(view)
        revisions.setdefault(view.scenario_id, view.revision)
    for rows in trials.values():
        rows.sort(key=lambda item: item.trial_index)
    return _Collected(
        trials=trials,
        attempts=len(attempts),
        graded=graded,
        duplicates=duplicates,
        ungraded=ungraded,
        revisions=revisions,
    )


def _probe_metric(probe: Mapping[str, Any], metric_id: str) -> Mapping[str, Any] | None:
    for item in _sequence(probe.get("metrics")):
        row = _mapping(item)
        if str(row.get("id") or "") == metric_id:
            return row
    return None


def _scored_value(metric: Mapping[str, Any]) -> float | None:
    if metric.get("status") != "scored":
        return None
    return _number(metric.get("value"))


def _metric_scope(metric_id: str) -> str | None:
    spec = METRIC_SPECS.get(metric_id)
    return spec.scope if spec is not None else None


def _scene_score(
    trials: Sequence[_TrialView], metric_id: str, repetitions: int
) -> _SceneScore:
    """把一个场景的 K 次 trial 聚成一个场景分（契约 §8.4）。"""
    reasons: list[str] = []
    probes_planned = sum(len(trial.probes) for trial in trials)
    if len(trials) < repetitions:
        reasons.append("TRIAL_MISSING")
    if len(trials) > repetitions:
        reasons.append("TRIAL_COUNT_MISMATCH")
    if reasons:
        return _SceneScore(
            value=None,
            trials=len(trials),
            probes_scored=0,
            probes_planned=probes_planned,
            reasons=tuple(reasons),
        )

    scope = _metric_scope(metric_id)
    if metric_id in _QA_SCENE_METRICS or scope == "probe":
        return _probe_based_score(trials, metric_id, metric_id in _QA_SCENE_METRICS)
    if scope == "scenario":
        return _trial_metric_score(trials, metric_id)
    raise MemoryComparisonIncompatible(f"PRIMARY_METRIC_NOT_SCENE_LEVEL:{metric_id}")


def _qa_value(probe: Mapping[str, Any]) -> float | None:
    """qa_correct 严格转 0/1；null（无法评分）保持 None，不补 0。"""
    raw = probe.get("qa_correct")
    if raw is True:
        return 1.0
    if raw is False:
        return 0.0
    return None


def _probe_based_score(
    trials: Sequence[_TrialView], metric_id: str, qa_based: bool
) -> _SceneScore:
    """按逐 probe 值聚合；qa 主指标读 ``qa_correct``，其它 probe 指标读同名 metric。"""
    reasons: list[str] = []
    probe_ids = [str(probe.get("probe_id") or "") for probe in trials[0].probes]
    if not probe_ids or any(not item for item in probe_ids):
        return _SceneScore(
            value=None,
            trials=len(trials),
            probes_scored=0,
            probes_planned=sum(len(trial.probes) for trial in trials),
            reasons=("SCENE_WITHOUT_PROBE",),
        )
    for trial in trials[1:]:
        other = [str(probe.get("probe_id") or "") for probe in trial.probes]
        if other != probe_ids:
            reasons.append("SCENE_PROBE_SET_MISMATCH")
            break

    probe_values: dict[str, list[float]] = {probe_id: [] for probe_id in probe_ids}
    micro_correct = 0
    micro_total = 0
    probes_scored = 0
    trial_means: list[float] = []
    for trial in trials:
        trial_values: list[float] = []
        for probe in trial.probes:
            probe_id = str(probe.get("probe_id") or "")
            if qa_based:
                value = _qa_value(probe)
            else:
                metric = _probe_metric(probe, metric_id)
                value = None if metric is None else _scored_value(metric)
            if value is None:
                reasons.append(f"METRIC_NOT_SCORED:{probe_id}")
                continue
            probes_scored += 1
            trial_values.append(value)
            probe_values.setdefault(probe_id, []).append(value)
            if qa_based:
                micro_total += 1
                micro_correct += int(value > 0)
        if len(trial_values) != len(probe_ids):
            reasons.append("SCENE_NOT_FULLY_JUDGEABLE")
        elif trial_values:
            trial_means.append(sum(trial_values) / len(trial_values))
    if reasons:
        return _SceneScore(
            value=None,
            trials=len(trials),
            probes_scored=probes_scored,
            probes_planned=sum(len(trial.probes) for trial in trials),
            probe_rates={key: _mean(values) for key, values in probe_values.items()},
            reasons=tuple(dict.fromkeys(reasons)),
        )
    return _SceneScore(
        value=_mean(trial_means),
        trials=len(trials),
        probes_scored=probes_scored,
        probes_planned=sum(len(trial.probes) for trial in trials),
        probe_rates={key: _mean(values) for key, values in probe_values.items()},
        micro_correct=micro_correct if qa_based else None,
        micro_total=micro_total if qa_based else None,
    )


def _trial_metric_score(trials: Sequence[_TrialView], metric_id: str) -> _SceneScore:
    """scenario 级指标：每次 trial 一个值，再对 trial 平均。"""
    values: list[float] = []
    reasons: list[str] = []
    probes_scored = 0
    for trial in trials:
        metric = trial.metrics.get(metric_id)
        value = None if metric is None else _scored_value(metric)
        if value is None:
            reasons.append(f"METRIC_NOT_SCORED:{trial.trial_index}")
            continue
        values.append(value)
        probes_scored += sum(
            1
            for probe in trial.probes
            if (item := _probe_metric(probe, metric_id)) is not None
            and _scored_value(item) is not None
        )
    if reasons:
        return _SceneScore(
            value=None,
            trials=len(trials),
            probes_scored=probes_scored,
            probes_planned=sum(len(trial.probes) for trial in trials),
            reasons=tuple(reasons),
        )
    return _SceneScore(
        value=_mean(values),
        trials=len(trials),
        probes_scored=probes_scored,
        probes_planned=sum(len(trial.probes) for trial in trials),
    )


# =========================================================================== 区间


def quantile_type7(ordered: Sequence[float], probability: float) -> float:
    """type7 线性插值分位数（``h = (n-1)p``）。"""
    values = sorted(float(item) for item in ordered)
    if not values:
        raise ValueError("QUANTILE_OF_EMPTY")
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + fraction * (values[upper] - values[lower])


def cluster_bootstrap_interval(
    cluster_deltas: Mapping[str, Sequence[float]],
    *,
    seed: int = BOOTSTRAP_SEED,
    rounds: int = BOOTSTRAP_ROUNDS,
    confidence: float = BOOTSTRAP_CONFIDENCE,
) -> dict[str, Any] | None:
    """按 cluster 整体重采样的配对差值区间（契约 §8.5 第 3/4 条）。

    每轮有放回抽 C 个 cluster（C = 独立 cluster 数），把被抽 cluster 里**全部**
    配对场景的差值串联后求均值；不先按 cluster 求均值再等权。固定 seed 与轮数，
    区间取 type7 的 2.5%/97.5% 分位。独立 cluster 少于 20 返回 ``None``。
    """
    clusters = [list(values) for _, values in sorted(cluster_deltas.items()) if values]
    if len(clusters) < MIN_CLUSTERS_FOR_INTERVAL:
        return None
    count = len(clusters)
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(rounds):
        total = 0.0
        size = 0
        for _ in range(count):
            picked = clusters[rng.randrange(count)]
            total += sum(picked)
            size += len(picked)
        means.append(total / size)
    alpha = (1.0 - confidence) / 2.0
    all_values = [value for cluster in clusters for value in cluster]
    return {
        "version": BOOTSTRAP_VERSION,
        "mean_delta_pp": sum(all_values) / len(all_values),
        "low_pp": quantile_type7(means, alpha),
        "high_pp": quantile_type7(means, 1.0 - alpha),
        "confidence": confidence,
        "rounds": rounds,
        "seed": seed,
        "clusters": count,
        "scenes": len(all_values),
    }


def classify_change(interval: tuple[float, float] | None) -> str:
    """只回答统计方向：``improved`` / ``regressed`` / ``inconclusive``。

    区间跨 0（或没有区间）一律 ``inconclusive``——"跨 0"不等于"没有变化"，
    只是这份数据还没法把方向说死。
    """
    if interval is None:
        return "inconclusive"
    low, high = interval
    if low > 0:
        return IMPROVED
    if high < 0:
        return REGRESSED
    return "inconclusive"


# =========================================================================== 采用判定


def _policy_dict(policy: MemoryAdoptionPolicy | Mapping[str, Any] | None) -> MemoryAdoptionPolicy:
    if isinstance(policy, MemoryAdoptionPolicy):
        return policy
    return MemoryAdoptionPolicy.model_validate(dict(policy or {}))


def adoption_decision(
    *,
    statistical_direction: str,
    interval_pp: Sequence[float] | None,
    delta_pp: float | None,
    policy: MemoryAdoptionPolicy | Mapping[str, Any] | None = None,
    measurement_complete: bool = True,
    metric_intervals_pp: Mapping[str, Sequence[float] | None] | None = None,
    uncalibrated_metrics: Sequence[str] = (),
    critical_violations: Sequence[str] = (),
    cost_ratio: float | None = None,
    p95_latency_ratio: float | None = None,
    default_minimum_effect_pp: float | None = None,
) -> dict[str, Any]:
    """采用建议（契约 §8.5 第 6/7 条），与 :func:`classify_change` 分开。

    只有同时满足：主指标改善区间下界超过 ``minimum_effect_pp``、所有声明的分项
    退化在容忍度内、成本/延迟预算通过、无关键边界失败，且测量完整，才返回
    ``adoptable``。缺阈值只出诊断（``diagnostic_only``），缺证是
    ``review_required``，明确越界是 ``rejected``，关键泄漏/删除复活直接
    ``blocked``——即使平均分上升。
    """
    parsed = _policy_dict(policy)
    minimum = parsed.minimum_effect_pp
    if minimum is None:
        minimum = default_minimum_effect_pp
    # 区间可能是持久化后的 JSON（list 或缺失项），先规范成两个数，否则按未知处理。
    pair = [value for value in map(_number, _sequence(interval_pp)) if value is not None]
    interval_pair: tuple[float, float] | None = (
        (pair[0], pair[1]) if len(pair) == 2 else None
    )
    reasons: list[str] = []
    checks: dict[str, Any] = {
        "minimum_effect_pp": minimum,
        "statistical_direction": statistical_direction,
        "delta_pp": delta_pp,
        "interval_pp": None if interval_pair is None else list(interval_pair),
        "critical_violations": list(critical_violations),
        "cost_ratio": cost_ratio,
        "p95_latency_ratio": p95_latency_ratio,
    }
    intervals: dict[str, list[float] | None] = {}
    for key, value in (metric_intervals_pp or {}).items():
        numbers = [item for item in map(_number, _sequence(value)) if item is not None]
        intervals[str(key)] = [numbers[0], numbers[1]] if len(numbers) == 2 else None

    # 1. 关键门禁压过一切：泄漏/删除复活是"已知失败"，不是统计问题。
    if critical_violations:
        return {
            "decision": BLOCKED,
            "reasons": [*reasons, *[f"CRITICAL:{item}" for item in critical_violations]],
            "checks": checks,
        }

    review: list[str] = []
    violated: list[str] = []

    # 2. 完整性与证据：缺题/缺重复/未校准 → review_required。
    if not measurement_complete:
        review.append("MEASUREMENT_INCOMPLETE")
    if interval_pair is None:
        review.append("INTERVAL_UNAVAILABLE")
    for metric_id in sorted({str(item) for item in uncalibrated_metrics}):
        review.append(f"UNCALIBRATED:{metric_id}")

    # 3. 分项退化容忍度：用差值区间下界评估；样本不足不能自动通过。
    regressions: dict[str, Any] = {}
    for metric_id, tolerance in sorted(parsed.max_regression_pp_by_metric.items()):
        interval = intervals.get(str(metric_id))
        entry: dict[str, Any] = {"tolerance_pp": tolerance, "interval_pp": interval}
        if interval is None:
            entry["verdict"] = "insufficient_sample"
            review.append(f"REGRESSION_INTERVAL_UNAVAILABLE:{metric_id}")
        elif interval[0] < -abs(float(tolerance)):
            entry["verdict"] = "exceeds_tolerance"
            violated.append(f"REGRESSION_EXCEEDS_TOLERANCE:{metric_id}")
        else:
            entry["verdict"] = "within_tolerance"
        regressions[str(metric_id)] = entry
    checks["regressions"] = regressions

    # 4. 成本/延迟预算：未知预算不能算通过。
    budgets: dict[str, Any] = {}
    for name, limit, actual in (
        ("cost_ratio", parsed.max_cost_ratio, cost_ratio),
        ("p95_latency_ratio", parsed.max_p95_latency_ratio, p95_latency_ratio),
    ):
        if limit is None:
            continue
        entry = {"limit": limit, "actual": actual}
        if actual is None:
            entry["verdict"] = "unknown"
            review.append(f"BUDGET_UNKNOWN:{name}")
        elif actual > limit:
            entry["verdict"] = "exceeded"
            violated.append(f"BUDGET_EXCEEDED:{name}")
        else:
            entry["verdict"] = "within_limit"
        budgets[name] = entry
    checks["budgets"] = budgets

    if violated:
        return {"decision": REJECTED, "reasons": [*reasons, *violated], "checks": checks}
    if review:
        return {"decision": REVIEW_REQUIRED, "reasons": [*reasons, *review], "checks": checks}

    # 5. 缺 minimum_effect_pp：只出诊断，绝不自动采用。
    if minimum is None:
        return {
            "decision": DIAGNOSTIC_ONLY,
            "reasons": [*reasons, "MINIMUM_EFFECT_PP_UNCONFIGURED"],
            "checks": checks,
        }

    assert interval_pair is not None  # 已由 review 分支排除
    low, high = interval_pair
    if low > float(minimum):
        return {
            "decision": ADOPTABLE,
            "reasons": [*reasons, "PRIMARY_GAIN_EXCEEDS_MINIMUM"],
            "checks": checks,
        }
    if high < -float(minimum):
        return {
            "decision": REJECTED,
            "reasons": [*reasons, "MEANINGFUL_REGRESSION"],
            "checks": checks,
        }
    return {
        "decision": INSUFFICIENT,
        "reasons": [*reasons, "INSUFFICIENT_EVIDENCE"],
        "checks": checks,
    }


# =========================================================================== 成本


def _metric_values(
    attempts: Sequence[Mapping[str, Any]], metric_id: str
) -> tuple[list[float], int]:
    """收集一个数据集级成本指标的全部已评分值；返回 (values, planned_trials)。"""
    values: list[float] = []
    planned = 0
    for attempt in attempts:
        memory = _memory_grade(attempt)
        if memory is None:
            continue
        planned += 1
        for item in _sequence(memory.get("metrics")):
            row = _mapping(item)
            if str(row.get("id") or "") != metric_id:
                continue
            value = _scored_value(row)
            if value is not None:
                values.append(value)
    return values, planned


def _cost_entry(
    baseline_attempts: Sequence[Mapping[str, Any]],
    candidate_attempts: Sequence[Mapping[str, Any]],
    metric_id: str,
) -> dict[str, Any]:
    base_values, base_planned = _metric_values(baseline_attempts, metric_id)
    cand_values, cand_planned = _metric_values(candidate_attempts, metric_id)
    if metric_id in _SUM_METRICS:
        baseline = sum(base_values) if base_values else None
        candidate = sum(cand_values) if cand_values else None
        aggregation = "sum"
    else:
        baseline = _mean(base_values)
        candidate = _mean(cand_values)
        aggregation = "mean_of_trial_values"
    spec = METRIC_SPECS.get(metric_id)
    complete = (
        base_planned > 0
        and cand_planned > 0
        and len(base_values) == base_planned
        and len(cand_values) == cand_planned
    )
    return {
        "baseline": baseline,
        "candidate": candidate,
        "delta": None if baseline is None or candidate is None else candidate - baseline,
        # 部分样本的比值会误导：不完整就不给 ratio（未知不是 0）。
        "ratio": _ratio(candidate, baseline) if complete else None,
        "unit": spec.unit if spec is not None else None,
        "direction": spec.direction if spec is not None else None,
        "aggregation": aggregation,
        "coverage": {
            "baseline": {"scored": len(base_values), "planned": base_planned},
            "candidate": {"scored": len(cand_values), "planned": cand_planned},
        },
        "complete": complete,
    }


def _complete_ratio(entry: Mapping[str, Any]) -> float | None:
    """只有双方覆盖都完整时才给比值；部分样本的比值会误导。"""
    return entry.get("ratio") if entry.get("complete") else None


def _cost_block(
    baseline_attempts: Sequence[Mapping[str, Any]],
    candidate_attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for group, metric_ids in COST_GROUPS.items():
        groups[group] = {
            metric_id: _cost_entry(baseline_attempts, candidate_attempts, metric_id)
            for metric_id in metric_ids
        }
    cost_entry = groups["totals"][ESTIMATED_COST]
    p95_entry = groups["query"][RETRIEVE_LATENCY_P95_MS]
    groups["summary"] = {
        "cost_ratio": _complete_ratio(cost_entry),
        "p95_latency_ratio": _complete_ratio(p95_entry),
        "cost_unknown": cost_entry["baseline"] is None or cost_entry["candidate"] is None,
        "note": "未知成本保持 null，不填 0；时延为 trial 值平均，不是数据集分位数。",
    }
    return groups


# =========================================================================== 关键门禁


def _gate_block(
    baseline: _Collected, candidate: _Collected
) -> dict[str, Any]:
    def values(collected: _Collected, metric_id: str) -> list[float]:
        found: list[float] = []
        for trial in collected.values_of():
            metric = trial.metrics.get(metric_id)
            value = None if metric is None else _scored_value(metric)
            if value is not None:
                found.append(value)
        return found

    def reason_hits(collected: _Collected, needles: Sequence[str]) -> list[str]:
        hits: list[str] = []
        for trial in collected.values_of():
            for metric in trial.metrics.values():
                for reason in _sequence(metric.get("reasons")):
                    text = str(reason).upper()
                    if any(needle in text for needle in needles):
                        hits.append(f"{trial.scenario_id}:{reason}")
        return hits

    base_scope = values(baseline, SCOPE_LEAK_RATE)
    cand_scope = values(candidate, SCOPE_LEAK_RATE)
    base_forget = values(baseline, FORGET_BEHAVIOR_SUCCESS)
    cand_forget = values(candidate, FORGET_BEHAVIOR_SUCCESS)
    resurrection_needles = ("RESURRECT", "REVIVED", "DELETE_REVIVED")
    cand_scope_reasons = reason_hits(candidate, ("SCOPE_LEAK",))
    cand_resurrection = reason_hits(candidate, resurrection_needles)
    scope_failed = any(value > 0 for value in cand_scope) or bool(cand_scope_reasons)
    deletion_failed = any(value <= 0 for value in cand_forget) or bool(cand_resurrection)
    return {
        "scope_leak": {
            "baseline_failed": any(value > 0 for value in base_scope),
            "candidate_failed": scope_failed,
            "baseline_values": base_scope,
            "candidate_values": cand_scope,
            "evidence": cand_scope_reasons,
        },
        "deletion_resurrection": {
            "baseline_failed": any(value <= 0 for value in base_forget),
            "candidate_failed": deletion_failed,
            "baseline_values": base_forget,
            "candidate_values": cand_forget,
            "evidence": cand_resurrection,
        },
    }


def _critical_violations(gates: Mapping[str, Any]) -> list[str]:
    violations: list[str] = []
    if _mapping(gates.get("scope_leak")).get("candidate_failed"):
        violations.append("scope_leak")
    if _mapping(gates.get("deletion_resurrection")).get("candidate_failed"):
        violations.append("deletion_resurrection")
    return violations


# =========================================================================== 未校准


def _uncalibrated_metrics(
    baseline: _Collected, candidate: _Collected, metric_ids: Sequence[str]
) -> list[str]:
    """契约 §8.5：所需语义指标未校准 → 不能自动放行。"""
    wanted = {str(item) for item in metric_ids}
    found: set[str] = set()
    for collected in (baseline, candidate):
        for trial in collected.values_of():
            candidates: list[Mapping[str, Any]] = list(trial.metrics.values())
            for probe in trial.probes:
                candidates.extend(
                    row for row in map(_mapping, _sequence(probe.get("metrics")))
                )
            for metric in candidates:
                metric_id = str(metric.get("id") or "")
                if metric_id not in wanted:
                    continue
                status = metric.get("calibration_status")
                if status is not None and str(status) not in {
                    "calibrated",
                    "not_required",
                    "not_applicable",
                }:
                    found.add(metric_id)
    return sorted(found)


# =========================================================================== 聚合


def _category_macro(
    paired: Sequence[tuple[ScenePair, float, float]],
) -> dict[str, Any]:
    """类别宏平均：各类别内部先按场景平均，再对类别平均（与 grading 一致）。"""
    buckets: dict[str, list[tuple[float, float]]] = {}
    for pair, base, cand in paired:
        for category in pair.categories or (pair.category,):
            buckets.setdefault(category, []).append((base, cand))
    rows: list[dict[str, Any]] = []
    for category in sorted(buckets):
        values = buckets[category]
        base_mean = _mean([value[0] for value in values])
        cand_mean = _mean([value[1] for value in values])
        rows.append(
            {
                "category": category,
                "scenes": len(values),
                "baseline": base_mean,
                "candidate": cand_mean,
                "delta_pp": (
                    None
                    if base_mean is None or cand_mean is None
                    else 100.0 * (cand_mean - base_mean)
                ),
            }
        )
    overall_base = _mean([row["baseline"] for row in rows if row["baseline"] is not None])
    overall_cand = _mean(
        [row["candidate"] for row in rows if row["candidate"] is not None]
    )
    return {
        "overall": {
            "baseline": overall_base,
            "candidate": overall_cand,
            "delta_pp": (
                None
                if overall_base is None or overall_cand is None
                else 100.0 * (overall_cand - overall_base)
            ),
            "categories": len(rows),
        },
        "categories": rows,
    }


def _secondary_metrics(
    baseline: _Collected,
    candidate: _Collected,
    *,
    primary_metric: str,
    repetitions: int,
    paired_scene_ids: Sequence[str],
    index: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """逐分项差值 + cluster 区间；未声明的指标不算、不补 0。"""
    metric_ids: set[str] = set()
    for collected in (baseline, candidate):
        for trial in collected.values_of():
            metric_ids.update(trial.metrics)
            metric_ids.update(trial.required_metrics)
            for probe in trial.probes:
                for item in _sequence(probe.get("metrics")):
                    metric_id = str(_mapping(item).get("id") or "")
                    if metric_id:
                        metric_ids.add(metric_id)
    metric_ids.discard(primary_metric)
    metric_ids.difference_update(OFFICIAL_ISOLATED_METRICS)
    metric_ids = {
        metric_id
        for metric_id in metric_ids
        if metric_id and _metric_scope(metric_id) in {"probe", "scenario"}
    }
    rows: list[dict[str, Any]] = []
    for metric_id in sorted(metric_ids):
        spec = METRIC_SPECS.get(metric_id)
        cluster_deltas: dict[str, list[float]] = {}
        base_values: list[float] = []
        cand_values: list[float] = []
        missing = 0
        for scenario_id in paired_scene_ids:
            base_score = _scene_score(
                baseline.trials.get(scenario_id, []), metric_id, repetitions
            )
            cand_score = _scene_score(
                candidate.trials.get(scenario_id, []), metric_id, repetitions
            )
            if base_score.value is None or cand_score.value is None:
                missing += 1
                continue
            base_values.append(base_score.value)
            cand_values.append(cand_score.value)
            delta_pp = 100.0 * (cand_score.value - base_score.value)
            cluster_deltas.setdefault(_cluster_of(index, scenario_id), []).append(delta_pp)
        interval = cluster_bootstrap_interval(cluster_deltas)
        base_mean = _mean(base_values)
        cand_mean = _mean(cand_values)
        direction = spec.direction if spec is not None else "higher_is_better"
        delta_pp = (
            None if base_mean is None or cand_mean is None else 100.0 * (cand_mean - base_mean)
        )
        improvement_pp = (
            None
            if delta_pp is None
            else (delta_pp if direction == "higher_is_better" else -delta_pp)
        )
        rows.append(
            {
                "metric_id": metric_id,
                "unit": spec.unit if spec is not None else None,
                "direction": direction,
                "baseline": base_mean,
                "candidate": cand_mean,
                "delta_pp": delta_pp,
                "improvement_pp": improvement_pp,
                "interval_pp": (
                    None
                    if interval is None
                    else [interval["low_pp"], interval["high_pp"]]
                ),
                "improvement_interval_pp": (
                    None
                    if interval is None
                    else (
                        [interval["low_pp"], interval["high_pp"]]
                        if direction == "higher_is_better"
                        else [-interval["high_pp"], -interval["low_pp"]]
                    )
                ),
                "statistical_direction": classify_change(
                    None
                    if interval is None
                    else (
                        (interval["low_pp"], interval["high_pp"])
                        if direction == "higher_is_better"
                        else (-interval["high_pp"], -interval["low_pp"])
                    )
                ),
                "pairs": len(base_values),
                "missing": missing,
                "clusters": None if interval is None else interval["clusters"],
            }
        )
    return rows


def _official_protocol_block(
    baseline: _Collected, candidate: _Collected
) -> dict[str, Any]:
    """官方协议另区：平台近似 Judge 只作诊断，不与通用分数混算。"""
    rows: list[dict[str, Any]] = []
    for metric_id in sorted(OFFICIAL_ISOLATED_METRICS):
        base_values, base_planned = _metrics_from_trials(baseline, metric_id)
        cand_values, cand_planned = _metrics_from_trials(candidate, metric_id)
        if not base_values and not cand_values:
            continue
        rows.append(
            {
                "metric_id": metric_id,
                "official": False,
                "note": "平台近似判定，非官方口径；不与主指标或其他分项合并。",
                "baseline": _mean(base_values),
                "candidate": _mean(cand_values),
                "coverage": {
                    "baseline": {"scored": len(base_values), "planned": base_planned},
                    "candidate": {"scored": len(cand_values), "planned": cand_planned},
                },
            }
        )
    return {"separated": True, "metrics": rows}


def _metrics_from_trials(
    collected: _Collected, metric_id: str
) -> tuple[list[float], int]:
    values: list[float] = []
    planned = 0
    for trial in collected.values_of():
        planned += 1
        metric = trial.metrics.get(metric_id)
        value = None if metric is None else _scored_value(metric)
        if value is not None:
            values.append(value)
    return values, planned


def _scene_pair(
    scenario_id: str,
    *,
    index: Mapping[str, Mapping[str, Any]],
    baseline_score: _SceneScore,
    candidate_score: _SceneScore,
    base_trials: Sequence[_TrialView],
    cand_trials: Sequence[_TrialView],
) -> ScenePair:
    row = index.get(scenario_id) or {}
    categories = tuple(sorted(set(base_trials[0].categories if base_trials else ())))
    category = categories[0] if categories else "untagged"
    if baseline_score.value is None or candidate_score.value is None:
        status = INCOMPARABLE
        reasons = tuple(
            dict.fromkeys([*baseline_score.reasons, *candidate_score.reasons])
        ) or ("NO_JUDGEABLE_SCORE",)
        delta = None
        delta_pp = None
    else:
        delta = candidate_score.value - baseline_score.value
        delta_pp = 100.0 * delta
        status = IMPROVED if delta > 0 else (REGRESSED if delta < 0 else UNCHANGED)
        reasons = ()
    probe_ids = sorted(set(baseline_score.probe_rates) | set(candidate_score.probe_rates))
    probe_changes = tuple(
        {
            "probe_id": probe_id,
            "baseline": baseline_score.probe_rates.get(probe_id),
            "candidate": candidate_score.probe_rates.get(probe_id),
        }
        for probe_id in probe_ids
    )
    return ScenePair(
        scenario_id=scenario_id,
        revision=int(row.get("revision") or 0),
        cluster_id=_cluster_of(index, scenario_id),
        category=category,
        categories=categories,
        status=status,
        baseline=baseline_score.value,
        candidate=candidate_score.value,
        delta=delta,
        delta_pp=delta_pp,
        baseline_trials=len(base_trials),
        candidate_trials=len(cand_trials),
        probe_changes=probe_changes,
        reasons=reasons,
    )


# =========================================================================== 主流程


def compare_memory_experiments(
    *,
    baseline_experiment: Mapping[str, Any],
    candidate_experiment: Mapping[str, Any],
    baseline_attempts: Sequence[Mapping[str, Any]],
    candidate_attempts: Sequence[Mapping[str, Any]],
    baseline_candidate_id: str,
    candidate_candidate_id: str,
    changed_fields: Sequence[str] = (),
    policy: MemoryAdoptionPolicy | Mapping[str, Any] | None = None,
    comparison_id: str = "",
) -> MemoryComparisonResult:
    """按契约 §5/§8.5 比较两个已冻结实验，返回不可变比较快照。

    输入 ``*_experiment`` 可以是 ``{"experiment": ..., "child_runs": ...}``
    或实验记录本身；``*_attempts`` 是仓库的 attempt 行（需带 ``case_snapshot``）。
    """
    baseline_record = _experiment_record(baseline_experiment)
    candidate_record = _experiment_record(candidate_experiment)
    _require_terminal(baseline_record, "baseline")
    _require_terminal(candidate_record, "candidate")
    baseline_manifest = _require_manifest(baseline_record, "baseline")
    candidate_manifest = _require_manifest(candidate_record, "candidate")
    # 场景 revision 先查：它比 dataset 摘要更具体，先给出可定位的拒绝原因。
    _require_scenario_revisions(baseline_manifest, candidate_manifest)
    _require_same_condition(baseline_manifest, candidate_manifest)
    changes, model_changes, source_changes = _require_declared(
        baseline_manifest,
        candidate_manifest,
        baseline_candidate_id,
        candidate_candidate_id,
        changed_fields,
    )
    index = _scenario_index(baseline_manifest)
    base_index = _scenario_index(candidate_manifest)

    base_grading = _mapping(baseline_manifest.get("grading"))
    repetitions = int(_mapping(baseline_manifest.get("environment")).get("repetitions") or 1)
    primary_metric = str(base_grading.get("primary_metric") or "")
    track = str(base_grading.get("track") or "")
    split = str(_mapping(baseline_manifest.get("dataset")).get("split") or "")
    if primary_metric in OFFICIAL_ISOLATED_METRICS:
        raise MemoryComparisonIncompatible(
            f"PRIMARY_METRIC_NOT_COMPARABLE:{primary_metric}"
        )

    baseline = _collect(baseline_attempts)
    candidate = _collect(candidate_attempts)

    # 场景 revision 与清单不一致：事件/gold 可能已换版本，拒绝而不是"尽力比较"。
    for side, collected in (("baseline", baseline), ("candidate", candidate)):
        for scenario_id, revision in sorted(collected.revisions.items()):
            expected = int(_mapping(index.get(scenario_id) or base_index.get(scenario_id) or {})
                           .get("revision") or 0)
            if expected and revision and int(revision) != expected:
                raise MemoryComparisonIncompatible(
                    f"SCENARIO_REVISION_MISMATCH:{side}:{scenario_id}:{revision}!={expected}"
                )

    planned = len(index)
    scene_pairs: list[ScenePair] = []
    excluded: list[dict[str, Any]] = []
    paired_values: list[tuple[ScenePair, float, float]] = []
    cluster_deltas: dict[str, list[float]] = {}
    micro_base_correct = 0
    micro_base_total = 0
    micro_cand_correct = 0
    micro_cand_total = 0
    base_probes_scored = 0
    base_probes_planned = 0
    cand_probes_scored = 0
    cand_probes_planned = 0
    graded_trials_base = len(baseline.values_of())
    graded_trials_cand = len(candidate.values_of())

    for scenario_id in sorted(set(baseline.trials) | set(candidate.trials)):
        base_trials = baseline.trials.get(scenario_id, [])
        cand_trials = candidate.trials.get(scenario_id, [])
        if not base_trials or not cand_trials:
            excluded.append(
                {
                    "scenario_id": scenario_id,
                    "reason": (
                        "SCENE_MISSING_IN_BASELINE"
                        if not base_trials
                        else "SCENE_MISSING_IN_CANDIDATE"
                    ),
                }
            )
            continue
        base_score = _scene_score(base_trials, primary_metric, repetitions)
        cand_score = _scene_score(cand_trials, primary_metric, repetitions)
        base_probes_scored += base_score.probes_scored
        base_probes_planned += base_score.probes_planned
        cand_probes_scored += cand_score.probes_scored
        cand_probes_planned += cand_score.probes_planned
        pair = _scene_pair(
            scenario_id,
            index=index,
            baseline_score=base_score,
            candidate_score=cand_score,
            base_trials=base_trials,
            cand_trials=cand_trials,
        )
        scene_pairs.append(pair)
        if pair.status == INCOMPARABLE:
            excluded.append(
                {
                    "scenario_id": scenario_id,
                    "reason": "SCENE_NOT_FULLY_JUDGEABLE",
                    "details": list(pair.reasons),
                }
            )
            continue
        # 非 incomparable 的配对一定两侧都有值（上面的分支已排除缺失）。
        assert pair.baseline is not None and pair.candidate is not None
        assert pair.delta_pp is not None
        paired_values.append((pair, float(pair.baseline), float(pair.candidate)))
        cluster_deltas.setdefault(pair.cluster_id, []).append(float(pair.delta_pp))
        if base_score.micro_total is not None and cand_score.micro_total is not None:
            micro_base_correct += int(base_score.micro_correct or 0)
            micro_base_total += int(base_score.micro_total)
            micro_cand_correct += int(cand_score.micro_correct or 0)
            micro_cand_total += int(cand_score.micro_total)

    paired = len(paired_values)
    base_macro = _mean([value[1] for value in paired_values])
    cand_macro = _mean([value[2] for value in paired_values])
    delta_pp = (
        None if base_macro is None or cand_macro is None else 100.0 * (cand_macro - base_macro)
    )
    interval = cluster_bootstrap_interval(cluster_deltas)
    subset_direction = classify_change(
        None if interval is None else (interval["low_pp"], interval["high_pp"])
    )

    secondary = _secondary_metrics(
        baseline,
        candidate,
        primary_metric=primary_metric,
        repetitions=repetitions,
        paired_scene_ids=[pair.scenario_id for pair, _, _ in paired_values],
        index=index,
    )
    costs = _cost_block(baseline_attempts, candidate_attempts)
    gates = _gate_block(baseline, candidate)
    critical = _critical_violations(gates)

    base_status = str(baseline_record.get("status") or "")
    cand_status = str(candidate_record.get("status") or "")
    base_measurement = str(baseline_record.get("measurement_status") or "incomplete")
    cand_measurement = str(candidate_record.get("measurement_status") or "incomplete")
    reasons: list[str] = []
    if base_measurement != "complete" or cand_measurement != "complete":
        reasons.append("MEASUREMENT_STATUS_INCOMPLETE")
    if base_status != "completed" or cand_status != "completed":
        reasons.append("EXPERIMENT_NOT_COMPLETED")
    if paired < planned:
        reasons.append("INCOMPLETE_PAIRING")
    if interval is None:
        reasons.append(f"INTERVALS_SKIPPED:CLUSTERS_LT_{MIN_CLUSTERS_FOR_INTERVAL}")
    if baseline.duplicates or candidate.duplicates:
        reasons.append("RETRIED_ATTEMPTS_COUNTED")
    if baseline.ungraded or candidate.ungraded:
        reasons.append("UNGRADED_ATTEMPTS_EXCLUDED")
    required_missing: list[str] = []
    for side, collected in (("baseline", baseline), ("candidate", candidate)):
        for trial in collected.values_of():
            scored = {
                metric_id
                for metric_id, metric in trial.metrics.items()
                if metric.get("status") == "scored"
            }
            for probe in trial.probes:
                for item in _sequence(probe.get("metrics")):
                    row = _mapping(item)
                    if row.get("status") == "scored":
                        scored.add(str(row.get("id") or ""))
            for metric_id in trial.required_metrics:
                if metric_id not in scored:
                    required_missing.append(f"{side}:{trial.scenario_id}:{metric_id}")
    if required_missing:
        reasons.append("REQUIRED_METRICS_MISSING")

    # 未校准的所需指标：从 case_snapshot.memory_required_metrics 汇总。
    required_all = sorted(
        {
            metric_id
            for collected in (baseline, candidate)
            for trial in collected.values_of()
            for metric_id in trial.required_metrics
        }
    )
    uncalibrated = _uncalibrated_metrics(baseline, candidate, required_all)
    if uncalibrated:
        reasons.append("REQUIRED_METRIC_UNCALIBRATED")

    measurement_complete = (
        paired == planned
        and planned > 0
        and not required_missing
        and not uncalibrated
        and base_measurement == "complete"
        and cand_measurement == "complete"
        and base_status == "completed"
        and cand_status == "completed"
    )
    status = "completed" if measurement_complete else "incomplete"
    if not measurement_complete:
        statistical_direction = "inconclusive"
    else:
        statistical_direction = subset_direction
    grading_policy = _mapping(base_grading.get("policy"))
    default_minimum = _number(grading_policy.get("minimum_effect_pp"))
    adoption = adoption_decision(
        statistical_direction=statistical_direction,
        interval_pp=(
            None if interval is None else (interval["low_pp"], interval["high_pp"])
        ),
        delta_pp=delta_pp,
        policy=policy,
        measurement_complete=measurement_complete,
        metric_intervals_pp={
            row["metric_id"]: row["improvement_interval_pp"] for row in secondary
        },
        uncalibrated_metrics=uncalibrated,
        critical_violations=critical,
        cost_ratio=_mapping(costs.get("summary")).get("cost_ratio"),
        p95_latency_ratio=_mapping(costs.get("summary")).get("p95_latency_ratio"),
        default_minimum_effect_pp=default_minimum,
    )

    categories = _category_macro(paired_values)
    probe_micro = {
        "baseline": (
            None if micro_base_total == 0 else micro_base_correct / micro_base_total
        ),
        "candidate": (
            None if micro_cand_total == 0 else micro_cand_correct / micro_cand_total
        ),
        "delta_pp": (
            None
            if micro_base_total == 0 or micro_cand_total == 0
            else 100.0
            * (micro_cand_correct / micro_cand_total - micro_base_correct / micro_base_total)
        ),
        "coverage": {
            "baseline": {"scored": micro_base_total, "correct": micro_base_correct},
            "candidate": {"scored": micro_cand_total, "correct": micro_cand_correct},
        },
    }

    payload: dict[str, Any] = {
        "id": comparison_id,
        "status": status,
        "measurement_status": "complete" if measurement_complete else "incomplete",
        "compatibility": "strict",
        "baseline_experiment_id": str(baseline_record.get("id") or ""),
        "candidate_experiment_id": str(candidate_record.get("id") or ""),
        "baseline_candidate_id": baseline_candidate_id,
        "candidate_candidate_id": candidate_candidate_id,
        "track": track,
        "split": split,
        "primary_metric": primary_metric,
        "repetitions": repetitions,
        "changed_fields": [str(item) for item in changed_fields],
        "applied_changes": list(changes),
        "model_changes": list(model_changes),
        "attribution": {
            "single_factor": not model_changes,
            "reasons": (
                [] if not model_changes else ["MODEL_OR_EXTRACTOR_CHANGED"]
            ),
            "model_changes": list(model_changes),
            "source_changes": list(source_changes),
            "source_note": (
                "源码/依赖状态差异只作出处记录，不要求声明；它本身就是被测变量。"
                "模型、抽取器或 embedding 变化仍必须显式声明。"
            ),
        },
        "paired": paired,
        "planned": planned,
        "excluded": excluded,
        "coverage": {
            "scenes": {"paired": paired, "planned": planned},
            "trials": {
                "baseline": {
                    "graded": graded_trials_base,
                    "planned": planned * repetitions,
                    "duplicates": baseline.duplicates,
                    "ungraded": baseline.ungraded,
                },
                "candidate": {
                    "graded": graded_trials_cand,
                    "planned": planned * repetitions,
                    "duplicates": candidate.duplicates,
                    "ungraded": candidate.ungraded,
                },
            },
            "probes": {
                "baseline": {"scored": base_probes_scored, "planned": base_probes_planned},
                "candidate": {"scored": cand_probes_scored, "planned": cand_probes_planned},
            },
        },
        "scenario_macro": {
            "baseline": base_macro,
            "candidate": cand_macro,
            "delta": (
                None if base_macro is None or cand_macro is None else cand_macro - base_macro
            ),
            "delta_pp": delta_pp,
        },
        "probe_micro": probe_micro,
        "category_macro": categories,
        "primary": {
            "metric_id": primary_metric,
            "scope": "scenario",
            "baseline": base_macro,
            "candidate": cand_macro,
            "delta_pp": delta_pp,
            "delta": (
                None if base_macro is None or cand_macro is None else cand_macro - base_macro
            ),
            "statistical_direction": statistical_direction,
            "subset_direction": subset_direction,
            "interval_pp": (
                None if interval is None else [interval["low_pp"], interval["high_pp"]]
            ),
        },
        "interval": interval,
        "secondary": secondary,
        "cost": costs,
        "gates": {**gates, "blocked": bool(critical), "critical_violations": critical},
        "statistical_direction": statistical_direction,
        "subset_statistical_direction": subset_direction,
        "adoption_decision": adoption,
        "uncalibrated_metrics": uncalibrated,
        "official_protocol": _official_protocol_block(baseline, candidate),
        "scene_pairs": [pair.to_dict() for pair in scene_pairs],
        "reasons": reasons,
        "required_metrics_missing": required_missing[:50],
        "bootstrap": {
            "version": BOOTSTRAP_VERSION,
            "seed": BOOTSTRAP_SEED,
            "rounds": BOOTSTRAP_ROUNDS,
            "confidence": BOOTSTRAP_CONFIDENCE,
            "min_clusters": MIN_CLUSTERS_FOR_INTERVAL,
        },
    }
    return MemoryComparisonResult(payload=payload)


# =========================================================================== 分页


def page_comparison_pairs(
    result: MemoryComparisonResult | Mapping[str, Any],
    *,
    limit: int = 50,
    after_key: str | None = None,
) -> dict[str, Any]:
    """从同一份比较快照里切出逐题页；统计量始终是全量的。

    游标是稳定的 ``scenario_id``；分页不重算、不缩减任何统计分母。
    """
    payload = result.to_dict() if isinstance(result, MemoryComparisonResult) else dict(result)
    size = max(1, min(int(limit), 200))
    pairs = sorted(
        (dict(item) for item in _sequence(payload.get("scene_pairs"))),
        key=lambda item: str(item.get("scenario_id") or ""),
    )
    if after_key:
        pairs = [
            item for item in pairs if str(item.get("scenario_id") or "") > str(after_key)
        ]
    page = pairs[:size]
    statistics = {
        key: copy.deepcopy(payload.get(key))
        for key in (
            "status",
            "measurement_status",
            "paired",
            "planned",
            "coverage",
            "scenario_macro",
            "probe_micro",
            "category_macro",
            "primary",
            "interval",
            "statistical_direction",
            "subset_statistical_direction",
            "adoption_decision",
            "cost",
            "gates",
            "reasons",
        )
    }
    return {
        "data": page,
        "next_after_key": (
            str(page[-1].get("scenario_id")) if len(pairs) > size and page else None
        ),
        "statistics": statistics,
    }


__all__ = [
    "ADOPTABLE",
    "BLOCKED",
    "BOOTSTRAP_CONFIDENCE",
    "BOOTSTRAP_ROUNDS",
    "BOOTSTRAP_SEED",
    "BOOTSTRAP_VERSION",
    "COST_GROUPS",
    "DIAGNOSTIC_ONLY",
    "IMPROVED",
    "INCOMPARABLE",
    "INSUFFICIENT",
    "MIN_CLUSTERS_FOR_INTERVAL",
    "MemoryComparisonError",
    "MemoryComparisonIncompatible",
    "MemoryComparisonMetadataMissing",
    "MemoryComparisonResult",
    "MemoryExperimentNotTerminal",
    "OFFICIAL_ISOLATED_METRICS",
    "REGRESSED",
    "REJECTED",
    "REVIEW_REQUIRED",
    "ScenePair",
    "UNCHANGED",
    "adoption_decision",
    "classify_change",
    "cluster_bootstrap_interval",
    "compare_memory_experiments",
    "page_comparison_pairs",
    "quantile_type7",
]
