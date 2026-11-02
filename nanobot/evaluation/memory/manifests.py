"""记忆实验冻结清单（契约 §5「对比与冻结」）。

``memory_manifest version=1`` 是旧运行清单（:mod:`nanobot.evaluation.manifests`）的
**子结构**：只冻结可比较的条件，不复制旧清单已有的字段。

冻结的六块内容与它们为什么必须冻结：

1. **dataset**：数据集修订 + 每个场景的 revision 与绑定 case（gold 版本随之固定）。
2. **grading**：判分策略、指标聚合与主要指标——评分口径变了，分数变化就不能
   归因于记忆算法（契约 §8）。
3. **environment**：轨道、重复次数、模型模式、预算、限额、时钟模式、能力声明与
   实际模型。环境摘要**不含具体墙钟时刻**：每次运行都有自己的日期，把 wall_now
   算进摘要会让所有历史比较都变成「环境不同」。
4. **candidates**：每个候选的记忆设置（检索通道/权重/阈值/召回上限/注入预算/
   embedding/索引版本）与摘要。
5. **source**：实际源码 SHA 与 dirty 摘要。拿不到时**显式 null + 原因**——
   「未知」不是「干净」，更不允许被当成同一版本做严格比较。
6. **manifest_digest**：以上内容的稳定摘要（脱敏、排序）。

两条硬边界：

- 本模块**不读环境变量、不加载 .env**：清单只能反映调用方显式注入的事实。
  否则同一份实验在不同机器上算出不同摘要，比较就失去意义。
- 缺实际值（源码 SHA/dirty、数据集摘要、判分摘要）时 :func:`require_strict_metadata`
  直接拒绝严格比较，而不是拿空串凑一个「看起来一致」的摘要。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import Field, StrictFloat, StrictStr

from nanobot.evaluation.manifests import (
    collect_source_state,
    digest_of,
    redact,
)
from nanobot.evaluation.memory.models import (
    Digest,
    JsonModel,
    MemoryBudgets,
    MemoryCandidate,
    MemoryCapabilities,
    MemoryGradingPolicy,
    MemoryTrack,
    NonNegativeInt,
    PositiveInt,
    ShortId,
)
from nanobot.memory.clock import MemoryClock

#: 契约 §5：清单版本。字段变动必须升版本，旧清单不能被新代码静默重解释。
MEMORY_MANIFEST_VERSION = 1


class MemoryManifestError(ValueError):
    """清单门禁错误：``code`` 直接对应契约 §6 的错误码。"""

    code = "MEMORY_SCHEMA_INVALID"

    def __init__(self, message: str = "") -> None:
        super().__init__(f"{self.code}: {message}" if message else self.code)


class MemoryManifestIncomplete(MemoryManifestError):  # noqa: N818 - 名称由错误码映射锁定
    """缺少实际值（源码 SHA/dirty、数据集/判分摘要）→ 422。"""

    code = "COMPARISON_METADATA_MISSING"


class MemoryManifestIncompatible(MemoryManifestError):  # noqa: N818 - 名称由错误码映射锁定
    """实际变化超出声明白名单 → 409。"""

    code = "COMPARISON_INCOMPATIBLE"


# --------------------------------------------------------------------------- 模型


class MemoryManifestScenario(JsonModel):
    """清单里的一个场景：revision 与绑定的 case 一起冻结。"""

    scenario_id: ShortId
    revision: PositiveInt
    case_id: StrictStr
    case_revision: PositiveInt
    cluster_id: StrictStr


class MemoryManifestDataset(JsonModel):
    dataset_revision_id: StrictStr
    dataset_version: StrictStr
    split: StrictStr
    definition_digest: Digest
    scenario_count: NonNegativeInt
    #: 场景下标摘要：只覆盖 (scenario_id, revision, case_id, case_revision, cluster_id)。
    scenario_digest: Digest
    scenarios: list[MemoryManifestScenario] = Field(default_factory=list)


class MemoryManifestGrading(JsonModel):
    track: MemoryTrack
    primary_metric: ShortId
    policy: MemoryGradingPolicy
    policy_digest: Digest
    #: 判分口径摘要 = 策略摘要 + 轨道 + 主要指标（改了任一项都不能归因记忆算法）。
    grading_digest: Digest
    judge_profile_id: StrictStr | None = None


class MemoryManifestClock(JsonModel):
    """时钟冻结记录：``mode`` 进环境摘要，具体时刻只作证据不参与比较。"""

    mode: Literal["real", "frozen"]
    wall_now: StrictStr | None = None
    monotonic_start: StrictFloat | None = None


class MemoryManifestCandidate(JsonModel):
    id: ShortId
    name: StrictStr
    mode: Literal["zhixu", "no_persistent_memory"]
    agent_profile_id: ShortId
    extractor_profile_id: ShortId | None = None
    memory_settings: dict[str, Any] = Field(default_factory=dict)
    settings_digest: Digest
    candidate_digest: Digest


class MemoryManifestEnvironment(JsonModel):
    model_mode: Literal["real", "scripted"]
    repetitions: PositiveInt
    budgets: MemoryBudgets
    limits: dict[str, Any] = Field(default_factory=dict)
    clock: MemoryManifestClock
    capabilities: MemoryCapabilities
    capabilities_digest: Digest
    models: dict[str, Any] = Field(default_factory=dict)
    #: 单 trial 预算预留估计（请求/Token）。缺省为空 = 各维未知，预算按未知处理。
    trial_estimate: dict[str, Any] = Field(default_factory=dict)
    environment_digest: Digest


class MemoryManifestSource(JsonModel):
    source_revision: StrictStr | None = None
    source_revision_reason: StrictStr | None = None
    source_dirty_digest: StrictStr | None = None
    source_dirty_digest_reason: StrictStr | None = None
    package_versions: dict[str, StrictStr] = Field(default_factory=dict)


class MemoryManifest(JsonModel):
    """契约 §5：``memory_manifest version=1``。"""

    version: Literal[1] = MEMORY_MANIFEST_VERSION
    dataset: MemoryManifestDataset
    grading: MemoryManifestGrading
    candidates: list[MemoryManifestCandidate] = Field(default_factory=list)
    environment: MemoryManifestEnvironment
    source: MemoryManifestSource
    manifest_digest: Digest


# --------------------------------------------------------------------------- 构造


def _scenario_index(scenarios: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    index = [
        {
            "scenario_id": str(item["scenario_id"]),
            "revision": int(item["revision"]),
            "case_id": str(item.get("case_id") or ""),
            "case_revision": int(item.get("case_revision") or 0),
            "cluster_id": str(item.get("cluster_id") or item["scenario_id"]),
        }
        for item in scenarios
    ]
    index.sort(key=lambda row: (row["scenario_id"], row["revision"]))
    return index


def policy_digest(policy: MemoryGradingPolicy) -> str:
    return digest_of(policy.model_dump(mode="json"))


def settings_digest(candidate: MemoryCandidate) -> str:
    return digest_of(
        {
            "mode": candidate.mode,
            "agent_profile_id": candidate.agent_profile_id,
            "extractor_profile_id": candidate.extractor_profile_id,
            "memory_settings": candidate.memory_settings.model_dump(mode="json"),
        }
    )


def _clock_record(clock: MemoryClock | None) -> MemoryManifestClock:
    if clock is None:
        return MemoryManifestClock(mode="real")
    if clock.frozen:
        return MemoryManifestClock(
            mode="frozen",
            wall_now=clock.wall_now().isoformat(),
            monotonic_start=float(clock.monotonic()),
        )
    return MemoryManifestClock(mode="real", wall_now=clock.wall_now().isoformat())


def _environment_view(environment: MemoryManifestEnvironment) -> dict[str, Any]:
    """环境摘要视图：**不含**具体墙钟时刻，避免每次运行都被判成环境不同。"""
    payload = environment.model_dump(mode="json", exclude={"environment_digest"})
    clock = dict(payload.get("clock") or {})
    payload["clock"] = {"mode": clock.get("mode")}
    return payload


def build_memory_manifest(
    *,
    dataset: Mapping[str, Any],
    scenarios: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any] | MemoryGradingPolicy,
    track: MemoryTrack,
    repetitions: int,
    primary_metric: str,
    model_mode: str,
    candidates: Sequence[Mapping[str, Any] | MemoryCandidate],
    capabilities: MemoryCapabilities,
    budgets: Mapping[str, Any] | MemoryBudgets,
    clock: MemoryClock | None = None,
    judge_profile_id: str | None = None,
    models: Mapping[str, Any] | None = None,
    limits: Mapping[str, Any] | None = None,
    source_state: Mapping[str, Any] | None = None,
    trial_estimate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造并冻结一份记忆实验清单（纯函数：所有事实由调用方显式传入）。"""
    parsed_policy = (
        policy if isinstance(policy, MemoryGradingPolicy)
        else MemoryGradingPolicy.model_validate(policy)
    )
    parsed_budgets = (
        budgets if isinstance(budgets, MemoryBudgets) else MemoryBudgets.model_validate(budgets)
    )
    parsed_candidates = [
        item if isinstance(item, MemoryCandidate) else MemoryCandidate.model_validate(item)
        for item in candidates
    ]
    index = _scenario_index(scenarios)
    dataset_block = MemoryManifestDataset(
        dataset_revision_id=str(dataset["id"]),
        dataset_version=str(dataset["dataset_version"]),
        split=str(dataset.get("split") or ""),
        definition_digest=str(dataset["definition_digest"]),
        scenario_count=len(index),
        scenario_digest=digest_of(index),
        scenarios=[MemoryManifestScenario.model_validate(row) for row in index],
    )
    grading_block = MemoryManifestGrading(
        track=track,
        primary_metric=primary_metric,
        policy=parsed_policy,
        policy_digest=policy_digest(parsed_policy),
        grading_digest=digest_of(
            {
                "track": track,
                "primary_metric": primary_metric,
                "policy": parsed_policy.model_dump(mode="json"),
            }
        ),
        judge_profile_id=judge_profile_id,
    )
    candidate_blocks = [
        MemoryManifestCandidate(
            id=item.id,
            name=item.name,
            mode=item.mode,
            agent_profile_id=item.agent_profile_id,
            extractor_profile_id=item.extractor_profile_id,
            memory_settings=item.memory_settings.model_dump(mode="json"),
            settings_digest=settings_digest(item),
            candidate_digest=digest_of(
                {
                    "id": item.id,
                    "mode": item.mode,
                    "settings": item.memory_settings.model_dump(mode="json"),
                }
            ),
        )
        for item in parsed_candidates
    ]
    environment_draft = MemoryManifestEnvironment(
        model_mode=model_mode,
        repetitions=int(repetitions),
        budgets=parsed_budgets,
        limits=redact(dict(limits or {})),
        clock=_clock_record(clock),
        capabilities=capabilities,
        capabilities_digest=digest_of(capabilities.model_dump(mode="json")),
        models=redact(dict(models or {})),
        trial_estimate=dict(trial_estimate or {}),
        environment_digest="0" * 64,
    )
    environment_block = environment_draft.model_copy(
        update={"environment_digest": digest_of(_environment_view(environment_draft))}
    )
    source = dict(source_state if source_state is not None else collect_source_state())
    source_block = MemoryManifestSource.model_validate(
        redact(
            {
                "source_revision": source.get("source_revision"),
                "source_revision_reason": source.get("source_revision_reason")
                or (None if source.get("source_revision") else "NOT_COLLECTED"),
                "source_dirty_digest": source.get("source_dirty_digest"),
                "source_dirty_digest_reason": source.get("source_dirty_digest_reason")
                or (None if source.get("source_dirty_digest") else "NOT_COLLECTED"),
                "package_versions": {
                    str(key): str(value)
                    for key, value in dict(source.get("package_versions") or {}).items()
                },
            }
        )
    )
    manifest = MemoryManifest(
        dataset=dataset_block,
        grading=grading_block,
        candidates=candidate_blocks,
        environment=environment_block,
        source=source_block,
        manifest_digest="0" * 64,
    )
    digest = memory_manifest_digest(manifest.model_dump(mode="json"))
    return manifest.model_copy(update={"manifest_digest": digest}).model_dump(mode="json")


def memory_manifest_digest(manifest: Mapping[str, Any]) -> str:
    """清单摘要：脱敏 + 键排序 + 去掉自身摘要与易变时钟字段。"""
    return digest_of(redact(_comparable_view(manifest)))


def _comparable_view(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """比较视图：去掉 ``manifest_digest``，并把时钟记录压成 ``mode``。

    wall_now / monotonic_start 是**每次运行各自的证据**，不是被测条件。把它们算进
    摘要会让「同一份配置的两次运行」永远判成环境不同、清单不可比。
    """
    payload = {
        key: value for key, value in manifest.items() if key != "manifest_digest"
    }
    environment = payload.get("environment")
    if isinstance(environment, Mapping):
        clock = environment.get("clock")
        normalized = (
            {"mode": dict(clock).get("mode")} if isinstance(clock, Mapping) else clock
        )
        payload = {
            **payload,
            "environment": {**environment, "clock": normalized},
        }
    return payload


# --------------------------------------------------------------------------- 比较门禁

#: 由其它冻结值算出来的摘要字段。它们是冗余的派生量，`changed_fields` 声明的是
#: **源字段**（例如 ``/candidates/0/memory_settings``），不是这些影子摘要。
_DERIVED_DIGEST_KEYS = frozenset(
    {
        "scenario_digest",
        "policy_digest",
        "grading_digest",
        "capabilities_digest",
        "environment_digest",
        "settings_digest",
        "candidate_digest",
    }
)


def _pointer_view(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return _strip_derived(_comparable_view(manifest))


def _strip_derived(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_derived(item)
            for key, item in value.items()
            if str(key) not in _DERIVED_DIGEST_KEYS
        }
    if isinstance(value, list):
        return [_strip_derived(item) for item in value]
    return value


def changed_pointers(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> list[str]:
    """两份清单的 JSON Pointer 差异（摘要、派生摘要与易变时钟字段不参与）。"""
    return _diff(_pointer_view(baseline), _pointer_view(candidate), "")


def _diff(left: Any, right: Any, path: str) -> list[str]:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        pointers: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}/{key}"
            if key not in left or key not in right:
                pointers.append(child)
                continue
            pointers.extend(_diff(left[key], right[key], child))
        return pointers
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return [path or "/"]
        pointers = []
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            pointers.extend(_diff(left_item, right_item, f"{path}/{index}"))
        return pointers
    if left == right:
        return []
    return [path or "/"]


def require_declared_changes(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    declared_fields: Sequence[str],
) -> list[str]:
    """实际变化必须是 ``changed_fields`` 白名单的子集（契约 §5）。

    声明父指针即覆盖其子树：声明 ``/candidates/0/memory_settings`` 后，其中每个
    字段都可以变；但清单里出现了并未声明的其它差异时一律按条件不匹配拒绝。
    """
    changes = changed_pointers(baseline, candidate)
    declared = [str(item) for item in declared_fields]
    undeclared = [
        pointer
        for pointer in changes
        if not any(
            pointer == root or pointer.startswith(root.rstrip("/") + "/")
            for root in declared
        )
    ]
    if undeclared:
        raise MemoryManifestIncompatible(
            "UNDECLARED_CHANGE " + ",".join(sorted(undeclared)[:8])
        )
    return changes


def require_strict_metadata(manifest: Mapping[str, Any]) -> None:
    """严格比较前的元数据门禁：缺实际值就拒绝，而不是拿空值凑一致。"""
    problems: list[str] = []
    if manifest.get("version") != MEMORY_MANIFEST_VERSION:
        problems.append("version")
    dataset = manifest.get("dataset") or {}
    if len(str(dataset.get("definition_digest") or "")) != 64:
        problems.append("dataset.definition_digest")
    if len(str(dataset.get("scenario_digest") or "")) != 64:
        problems.append("dataset.scenario_digest")
    grading = manifest.get("grading") or {}
    for key in ("policy_digest", "grading_digest"):
        if len(str(grading.get(key) or "")) != 64:
            problems.append(f"grading.{key}")
    environment = manifest.get("environment") or {}
    if not (environment.get("capabilities_digest") or ""):
        problems.append("environment.capabilities_digest")
    if not ((environment.get("clock") or {}).get("mode")):
        problems.append("environment.clock.mode")
    for index, candidate in enumerate(manifest.get("candidates") or []):
        if len(str((candidate or {}).get("settings_digest") or "")) != 64:
            problems.append(f"candidates/{index}/settings_digest")
    source = manifest.get("source") or {}
    if not source.get("source_revision"):
        problems.append("source.source_revision")
    if not source.get("source_dirty_digest"):
        problems.append("source.source_dirty_digest")
    if problems:
        raise MemoryManifestIncomplete("MISSING " + ",".join(problems))


def source_state_or_unknown(
    source_root: str | None = None,
) -> dict[str, Any]:
    """收集源码状态；拿不到 git 时**显式**返回 null + 原因（不伪造 SHA）。"""
    return collect_source_state(source_root)


__all__ = [
    "MEMORY_MANIFEST_VERSION",
    "MemoryManifest",
    "MemoryManifestCandidate",
    "MemoryManifestClock",
    "MemoryManifestDataset",
    "MemoryManifestEnvironment",
    "MemoryManifestError",
    "MemoryManifestGrading",
    "MemoryManifestIncompatible",
    "MemoryManifestIncomplete",
    "MemoryManifestScenario",
    "MemoryManifestSource",
    "build_memory_manifest",
    "changed_pointers",
    "memory_manifest_digest",
    "policy_digest",
    "require_declared_changes",
    "require_strict_metadata",
    "settings_digest",
    "source_state_or_unknown",
]
