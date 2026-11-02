"""WU-07：记忆实验冻结清单（契约 §5「对比与冻结」）。

清单只冻结**可比较的条件**：数据集修订、gold/判分策略、环境（时钟/能力/模型/
限额）、候选设置与源码状态。三条硬约束在这里被钉死：

1. 摘要里不出现凭据与绝对路径，模块本身不读 ``.env``（结构检查，不靠人工保证）。
2. 拿不到源码 SHA / dirty 摘要时**显式记 null + 原因**，并且禁止严格比较。
3. 实际变化必须是 ``changed_fields`` 白名单的子集，否则按「条件不匹配」拒绝。
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

import pytest

from nanobot.evaluation.memory import manifests as memory_manifests
from nanobot.evaluation.memory.manifests import (
    MEMORY_MANIFEST_VERSION,
    MemoryManifest,
    MemoryManifestIncompatible,
    MemoryManifestIncomplete,
    build_memory_manifest,
    changed_pointers,
    memory_manifest_digest,
    require_declared_changes,
    require_strict_metadata,
)
from nanobot.evaluation.memory.models import (
    MemoryBudgets,
    MemoryCapabilities,
    MemoryCapability,
    MemoryGradingPolicy,
    MemorySettings,
)
from nanobot.memory.clock import MemoryClock

SOURCE_STATE = {
    "source_revision": "ce595d6",
    "source_revision_reason": None,
    "source_dirty_digest": "d" * 64,
    "source_dirty_digest_reason": None,
    "package_versions": {"python": "3.13.0"},
}


def _policy(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "fact_matching_version": "fact-matching/v1",
        "metric_aggregation_version": "metric-aggregation/v1",
        "bootstrap_version": "paired-cluster-percentile/v1",
        "minimum_effect_pp": None,
    }
    payload.update(overrides)
    return payload


def _dataset(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "11111111-1111-4111-8111-111111111111",
        "suite_id": "22222222-2222-4222-8222-222222222222",
        "dataset_version": "memory-core-v1",
        "status": "ready",
        "split_counts": {"regression": 2},
        "source": {
            "repo_url": "https://example.invalid/zhixu/memory-core-v1",
            "revision": "r1",
            "sha256": "a" * 64,
            "license": "CC-BY-4.0",
            "original_ids": ["core-001"],
            "loader_version": "longmemeval-loader/1",
            "grading_protocol": "memory-grading/v1",
        },
        "definition_digest": "b" * 64,
    }
    payload.update(overrides)
    return payload


def _scenarios() -> list[dict[str, Any]]:
    return [
        {
            "scenario_id": "s000",
            "revision": 1,
            "case_id": "33333333-3333-4333-8333-333333333333",
            "case_revision": 1,
            "cluster_id": "cluster-0",
        },
        {
            "scenario_id": "s001",
            "revision": 2,
            "case_id": "44444444-4444-4444-8444-444444444444",
            "case_revision": 1,
            "cluster_id": "cluster-1",
        },
    ]


def _capabilities() -> MemoryCapabilities:
    return MemoryCapabilities(
        extraction=MemoryCapability(status="supported"),
        semantic_recall=MemoryCapability(status="supported"),
        episodes=MemoryCapability(status="partial", detail="场景级索引"),
        delete=MemoryCapability(status="unsupported"),
    )


def _manifest(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "dataset": _dataset(),
        "scenarios": _scenarios(),
        "policy": _policy(),
        "track": "end_to_end",
        "repetitions": 1,
        "primary_metric": "qa_accuracy_scenario_macro",
        "model_mode": "real",
        "candidates": [
            {
                "id": "cand-current",
                "name": "当前候选",
                "mode": "zhixu",
                "agent_profile_id": "agent-profile",
                "extractor_profile_id": "extractor-profile",
                "memory_settings": MemorySettings(recall_limit=10).model_dump(mode="json"),
            }
        ],
        "capabilities": _capabilities(),
        "budgets": MemoryBudgets(max_trials=200),
        "clock": MemoryClock(),
        "judge_profile_id": "judge-1",
        "models": {"agent": {"provider": "controlled", "model": "eval-model"}},
        "limits": {"concurrency": 1, "max_iterations": 12, "timeout_s": 120},
        "source_state": SOURCE_STATE,
    }
    kwargs.update(overrides)
    return build_memory_manifest(**kwargs)


# --------------------------------------------------------------------------- 冻结内容


def test_manifest_freezes_dataset_grading_environment_candidate_and_source() -> None:
    manifest = _manifest()
    assert manifest["version"] == MEMORY_MANIFEST_VERSION == 1
    assert manifest["dataset"]["definition_digest"] == "b" * 64
    assert manifest["dataset"]["scenario_count"] == 2
    assert len(manifest["dataset"]["scenario_digest"]) == 64
    assert manifest["grading"]["track"] == "end_to_end"
    assert manifest["grading"]["primary_metric"] == "qa_accuracy_scenario_macro"
    assert len(manifest["grading"]["policy_digest"]) == 64
    assert len(manifest["grading"]["grading_digest"]) == 64
    assert manifest["environment"]["clock"]["mode"] == "real"
    assert manifest["environment"]["capabilities"]["delete"]["status"] == "unsupported"
    assert len(manifest["environment"]["capabilities_digest"]) == 64
    assert manifest["candidates"][0]["memory_settings"]["recall_limit"] == 10
    assert len(manifest["candidates"][0]["settings_digest"]) == 64
    assert manifest["source"]["source_revision"] == "ce595d6"
    assert len(manifest["manifest_digest"]) == 64
    MemoryManifest.model_validate(manifest)


def test_manifest_digest_changes_with_retrieval_parameters() -> None:
    base = _manifest()
    changed = _manifest(
        candidates=[
            {
                "id": "cand-current",
                "name": "当前候选",
                "mode": "zhixu",
                "agent_profile_id": "agent-profile",
                "extractor_profile_id": "extractor-profile",
                "memory_settings": MemorySettings(recall_limit=20).model_dump(mode="json"),
            }
        ]
    )
    assert changed["candidates"][0]["settings_digest"] != base["candidates"][0]["settings_digest"]
    assert changed["manifest_digest"] != base["manifest_digest"]
    assert "/candidates/0/memory_settings/recall_limit" in changed_pointers(base, changed)


def test_manifest_digest_changes_with_clock_and_capabilities() -> None:
    frozen = _manifest(
        clock=MemoryClock(frozen_at=datetime(2026, 11, 2, tzinfo=timezone.utc))
    )
    real = _manifest(clock=MemoryClock())
    assert frozen["environment"]["clock"]["mode"] == "frozen"
    assert frozen["environment"]["environment_digest"] != real["environment"]["environment_digest"]

    degraded = _manifest(
        capabilities=MemoryCapabilities(extraction=MemoryCapability(status="partial"))
    )
    assert degraded["environment"]["capabilities_digest"] != real["environment"]["capabilities_digest"]


def test_manifest_excludes_credentials_and_absolute_paths() -> None:
    manifest = _manifest(
        source_state={
            **SOURCE_STATE,
            "package_versions": {
                "python": "3.13.0",
                "secret_path": r"D:\studyspace\project\Zhixu\config.json",
            },
        }
    )
    body = str(manifest)
    assert "sk-sentinel" not in body
    assert r"D:\studyspace" not in body
    # 结构检查：本模块不允许读环境变量/加载 .env（冻结不得被本机配置改写）。
    source = memory_manifests.__file__
    text = open(source, encoding="utf-8").read()
    assert "os.environ" not in text
    assert "dotenv" not in text


def test_manifest_rejects_unknown_fields() -> None:
    manifest = copy.deepcopy(_manifest())
    manifest["extra"] = 1
    with pytest.raises(ValueError):
        MemoryManifest.model_validate(manifest)


# --------------------------------------------------------------------------- 严格比较门禁


def test_missing_source_state_blocks_strict_metadata() -> None:
    manifest = _manifest(
        source_state={
            "source_revision": None,
            "source_revision_reason": "GIT_UNAVAILABLE",
            "source_dirty_digest": None,
            "source_dirty_digest_reason": "GIT_UNAVAILABLE",
        }
    )
    assert manifest["source"]["source_revision"] is None
    assert manifest["source"]["source_revision_reason"] == "GIT_UNAVAILABLE"
    with pytest.raises(MemoryManifestIncomplete) as excinfo:
        require_strict_metadata(manifest)
    assert excinfo.value.code == "COMPARISON_METADATA_MISSING"


def test_complete_source_state_passes_strict_metadata() -> None:
    require_strict_metadata(_manifest())


def test_undeclared_change_is_rejected_as_incompatible() -> None:
    base = _manifest()
    changed = _manifest(policy=_policy(fact_matching_version="fact-matching/v2"))
    changes = changed_pointers(base, changed)
    assert "/grading/policy/fact_matching_version" in changes
    with pytest.raises(MemoryManifestIncompatible) as excinfo:
        require_declared_changes(base, changed, [])
    assert excinfo.value.code == "COMPARISON_INCOMPATIBLE"
    assert require_declared_changes(
        base, changed, ["/grading/policy/fact_matching_version"]
    ) == changes


def test_declared_parent_pointer_covers_child_changes() -> None:
    base = _manifest()
    changed = _manifest(
        candidates=[
            {
                "id": "cand-current",
                "name": "当前候选",
                "mode": "zhixu",
                "agent_profile_id": "agent-profile",
                "extractor_profile_id": "extractor-profile",
                "memory_settings": MemorySettings(recall_limit=20).model_dump(mode="json"),
            }
        ]
    )
    require_declared_changes(base, changed, ["/candidates/0/memory_settings"])


def test_declared_dataset_change_is_compatible_but_grading_drift_is_not() -> None:
    base = _manifest()
    other_dataset = _manifest(
        dataset=_dataset(id="55555555-5555-4555-8555-555555555555")
    )
    require_declared_changes(base, other_dataset, ["/dataset"])
    with pytest.raises(MemoryManifestIncompatible):
        require_declared_changes(base, other_dataset, ["/grading"])


def test_manifest_digest_is_stable_and_matches_field() -> None:
    manifest = _manifest()
    assert memory_manifest_digest(manifest) == manifest["manifest_digest"]
    assert memory_manifest_digest(_manifest()) == manifest["manifest_digest"]


def test_policy_digest_is_versioned_by_grading_policy() -> None:
    policy = MemoryGradingPolicy.model_validate(_policy())
    manifest = _manifest(policy=policy.model_dump(mode="json"))
    assert manifest["grading"]["policy_digest"] == memory_manifests.policy_digest(policy)
