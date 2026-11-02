"""WU-08：记忆版本配对、cluster 区间与采用判定（契约 §5 / §8.5）。

本文件的红线：

- 同场景不同 revision、未声明模型变化、条件不匹配一律拒绝，不以"先算分数"代替门禁；
- 允许的检索参数变化可比，但必须出现在 ``changed_fields``；
- 缺一个 trial 是 incomplete，不是 0 分；
- 区间按 cluster 整体重采样（同 conversation 多题不拆成独立样本），<20 不输出；
- 逐题差值、场景宏平均、题级 micro、cluster 数与成本全部手算核对；
- 0 违规不等于安全保证：关键泄漏/删除复活直接阻止放行；
- 分页只影响逐题列表，不影响全量统计。
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

import pytest

from nanobot.evaluation.memory.comparisons import (
    BOOTSTRAP_ROUNDS,
    BOOTSTRAP_SEED,
    MIN_CLUSTERS_FOR_INTERVAL,
    MemoryComparisonIncompatible,
    MemoryComparisonMetadataMissing,
    MemoryExperimentNotTerminal,
    adoption_decision,
    classify_change,
    cluster_bootstrap_interval,
    compare_memory_experiments,
    page_comparison_pairs,
    quantile_type7,
)
from nanobot.evaluation.memory.manifests import build_memory_manifest
from nanobot.evaluation.memory.models import (
    MemoryAdoptionPolicy,
    MemoryBudgets,
    MemoryCapabilities,
    MemoryCapability,
    MemorySettings,
)
from nanobot.memory.clock import MemoryClock

DATASET_ID = "11111111-1111-4111-8111-111111111111"
BASELINE_EXPERIMENT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CANDIDATE_EXPERIMENT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SOURCE_STATE = {
    "source_revision": "ce595d6",
    "source_revision_reason": None,
    "source_dirty_digest": "d" * 64,
    "source_dirty_digest_reason": None,
    "package_versions": {"python": "3.13.0"},
}


# --------------------------------------------------------------------------- 清单夹具


def _policy(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "fact_matching_version": "fact-matching/v1",
        "metric_aggregation_version": "metric-aggregation/v1",
        "bootstrap_version": "paired-cluster-percentile/v1",
        "minimum_effect_pp": None,
    }
    payload.update(overrides)
    return payload


def _capabilities() -> MemoryCapabilities:
    return MemoryCapabilities(
        extraction=MemoryCapability(status="supported"),
        semantic_recall=MemoryCapability(status="supported"),
        delete=MemoryCapability(status="supported"),
    )


def _scenarios(
    count: int, *, cluster_size: int = 1, revision: int = 1
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(count):
        scenario_id = f"s{index:03d}"
        rows.append(
            {
                "scenario_id": scenario_id,
                "revision": revision,
                "case_id": f"case-{scenario_id}",
                "case_revision": 1,
                "cluster_id": f"cluster-{index // cluster_size:03d}",
            }
        )
    return rows


def _manifest(
    scenarios: list[dict[str, Any]],
    *,
    candidate_id: str = "cand-a",
    recall_limit: int = 10,
    repetitions: int = 1,
    models: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    extractor_profile: str = "extractor-profile",
    source_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_memory_manifest(
        dataset={
            "id": DATASET_ID,
            "dataset_version": "memory-core-v1",
            "split": "development",
            "definition_digest": "b" * 64,
        },
        scenarios=scenarios,
        policy=policy or _policy(),
        track="end_to_end",
        repetitions=repetitions,
        primary_metric="qa_accuracy_scenario_macro",
        model_mode="real",
        candidates=[
            {
                "id": candidate_id,
                "name": f"候选 {candidate_id}",
                "mode": "zhixu",
                "agent_profile_id": "agent-profile",
                "extractor_profile_id": extractor_profile,
                "memory_settings": MemorySettings(
                    recall_limit=recall_limit
                ).model_dump(mode="json"),
            }
        ],
        capabilities=_capabilities(),
        budgets=MemoryBudgets(max_trials=200),
        clock=MemoryClock(),
        judge_profile_id="judge-1",
        models=models or {"agent": {"provider": "controlled", "model": "eval-model"}},
        limits={"concurrency": 1, "timeout_s": 120},
        source_state=source_state or SOURCE_STATE,
    )


def _experiment(
    manifest: dict[str, Any],
    *,
    experiment_id: str = BASELINE_EXPERIMENT_ID,
    status: str = "completed",
    measurement_status: str = "complete",
) -> dict[str, Any]:
    return {
        "id": experiment_id,
        "status": status,
        "measurement_status": measurement_status,
        "manifest": manifest,
    }


# --------------------------------------------------------------------------- attempt 夹具


def _metric(
    metric_id: str,
    value: float | None,
    *,
    unit: str = "ratio",
    scope: str = "probe",
    status: str | None = None,
    reasons: tuple[str, ...] = (),
    calibration_status: str | None = None,
) -> dict[str, Any]:
    effective = status or ("scored" if value is not None else "not_assessable")
    return {
        "id": metric_id,
        "version": 1,
        "grading_revision": 1,
        "review_digest": None,
        "unit": unit,
        "value": value,
        "numerator": value,
        "denominator": 1.0 if value is not None else None,
        "status": effective,
        "scope": scope,
        "evidence_refs": [],
        "reasons": list(reasons),
        "coverage": {"scored": 1 if value is not None else 0, "planned": 1, "reasons": []},
        "interval": None,
        "grader_fingerprint": None,
        "calibration_status": calibration_status,
    }


def _attempt(
    scenario_id: str,
    *,
    qa: list[bool | None],
    trial_index: int = 0,
    revision: int = 1,
    categories: tuple[str, ...] = ("fact",),
    trial_metrics: list[dict[str, Any]] | None = None,
    probe_metrics: list[dict[str, Any]] | None = None,
    required: tuple[str, ...] = ("qa_accuracy",),
    qa_calibration: str | None = None,
) -> dict[str, Any]:
    probes: list[dict[str, Any]] = []
    for index, value in enumerate(qa):
        probe_id = f"q{index + 1}"
        metrics = [
            _metric(
                "qa_accuracy",
                None if value is None else float(value),
                calibration_status=qa_calibration,
            )
        ]
        metrics.extend(copy.deepcopy(probe_metrics or []))
        probes.append(
            {
                "probe_id": probe_id,
                "category": categories[0],
                "qa_correct": value,
                "qa_status": "scored" if value is not None else "not_assessable",
                "fact_coverage": None,
                "grounded_memory_success": value,
                "reasons": [],
                "metrics": metrics,
            }
        )
    return {
        "id": str(uuid.uuid4()),
        "case_id": f"case-{scenario_id}",
        "case_revision": 1,
        "execution_status": "succeeded",
        "grading_status": "completed",
        "verdict": "passed",
        "case_snapshot": {
            "memory_scenario_ref": {
                "dataset_revision_id": DATASET_ID,
                "scenario_id": scenario_id,
                "revision": revision,
            },
            "memory_required_metrics": list(required),
            "memory_track": "end_to_end",
        },
        "result": {
            "memory": {
                "scenario_id": scenario_id,
                "revision": revision,
                "track": "end_to_end",
                "grading_revision": 1,
                "review_digest": "r" * 64,
                "categories": list(categories),
                "probe_results": probes,
                "strict_pass": all(value is True for value in qa),
                "trial_index": trial_index,
                "metrics": copy.deepcopy(trial_metrics or []),
            }
        },
    }


def _attempts_for(
    qa_by_scene: dict[str, list[list[bool | None]]],
    *,
    trial_metrics: dict[tuple[str, int], list[dict[str, Any]]] | None = None,
    probe_metrics: dict[tuple[str, int], list[dict[str, Any]]] | None = None,
    categories: tuple[str, ...] = ("fact",),
    revisions: dict[str, int] | None = None,
    required: tuple[str, ...] = ("qa_accuracy",),
    qa_calibration: str | None = None,
) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for scenario_id, trials in qa_by_scene.items():
        for trial_index, qa in enumerate(trials):
            attempts.append(
                _attempt(
                    scenario_id,
                    qa=qa,
                    trial_index=trial_index,
                    revision=(revisions or {}).get(scenario_id, 1),
                    categories=categories,
                    trial_metrics=(trial_metrics or {}).get((scenario_id, trial_index)),
                    probe_metrics=(probe_metrics or {}).get((scenario_id, trial_index)),
                    required=required,
                    qa_calibration=qa_calibration,
                )
            )
    return attempts


def _compare(
    scenarios: list[dict[str, Any]],
    base_qa: dict[str, list[list[bool | None]]],
    cand_qa: dict[str, list[list[bool | None]]],
    *,
    changed_fields: tuple[str, ...] = (),
    policy: MemoryAdoptionPolicy | None = None,
    repetitions: int = 1,
    base_manifest: dict[str, Any] | None = None,
    cand_manifest: dict[str, Any] | None = None,
    base_attempts: list[dict[str, Any]] | None = None,
    cand_attempts: list[dict[str, Any]] | None = None,
) -> Any:
    baseline_manifest = base_manifest or _manifest(scenarios, repetitions=repetitions)
    candidate_manifest = cand_manifest or _manifest(
        scenarios, candidate_id="cand-b", repetitions=repetitions
    )
    return compare_memory_experiments(
        baseline_experiment=_experiment(baseline_manifest),
        candidate_experiment=_experiment(
            candidate_manifest, experiment_id=CANDIDATE_EXPERIMENT_ID
        ),
        baseline_attempts=(
            base_attempts
            if base_attempts is not None
            else _attempts_for(base_qa, revisions={row["scenario_id"]: row["revision"] for row in scenarios})
        ),
        candidate_attempts=(
            cand_attempts
            if cand_attempts is not None
            else _attempts_for(cand_qa, revisions={row["scenario_id"]: row["revision"] for row in scenarios})
        ),
        baseline_candidate_id="cand-a",
        candidate_candidate_id="cand-b",
        changed_fields=changed_fields,
        policy=policy,
    )


# --------------------------------------------------------------------------- 统计方向


def test_classify_change_only_returns_statistical_direction() -> None:
    assert classify_change(None) == "inconclusive"
    assert classify_change((-0.02, 0.05)) == "inconclusive"
    assert classify_change((0.0, 0.0)) == "inconclusive"
    assert classify_change((0.1, 0.4)) == "improved"
    assert classify_change((-0.4, -0.1)) == "regressed"


def test_quantile_type7_is_linear_interpolation() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert quantile_type7(values, 0.0) == 1.0
    assert quantile_type7(values, 0.5) == 2.5
    assert quantile_type7(values, 1.0) == 4.0
    assert quantile_type7(values, 0.25) == 1.75


# --------------------------------------------------------------------------- 区间


def test_cluster_bootstrap_needs_twenty_independent_clusters() -> None:
    nineteen = {f"c{index}": [100.0] for index in range(MIN_CLUSTERS_FOR_INTERVAL - 1)}
    assert cluster_bootstrap_interval(nineteen) is None
    twenty = {f"c{index}": [10.0] for index in range(MIN_CLUSTERS_FOR_INTERVAL)}
    interval = cluster_bootstrap_interval(twenty)
    assert interval is not None
    assert interval["clusters"] == 20
    assert interval["scenes"] == 20
    assert interval["rounds"] == BOOTSTRAP_ROUNDS == 2000
    assert interval["seed"] == BOOTSTRAP_SEED == 42
    assert (interval["low_pp"], interval["high_pp"]) == (10.0, 10.0)


def test_cluster_bootstrap_is_reproducible_with_fixed_seed() -> None:
    clusters = {
        f"c{index}": [float(index % 5), float(index % 3) - 1.0]
        for index in range(20)
    }
    first = cluster_bootstrap_interval(clusters)
    second = cluster_bootstrap_interval(clusters)
    assert first == second
    assert first is not None and first["low_pp"] <= first["high_pp"]


def test_same_conversation_scenes_stay_in_one_cluster() -> None:
    scenarios = _scenarios(22, cluster_size=2)
    qa = {row["scenario_id"]: [[True, True]] for row in scenarios}
    result = _compare(scenarios, qa, qa).to_dict()

    assert result["paired"] == 22
    # 22 个场景但只有 11 个独立 cluster → 仍不输出区间。
    assert result["interval"] is None
    assert f"CLUSTERS_LT_{MIN_CLUSTERS_FOR_INTERVAL}" in ",".join(result["reasons"])
    clustered = _scenarios(40, cluster_size=2)
    same = {row["scenario_id"]: [[True]] for row in clustered}
    grouped = _compare(clustered, same, same).to_dict()
    assert grouped["interval"] is not None
    assert grouped["interval"]["clusters"] == 20
    assert grouped["interval"]["scenes"] == 40


# --------------------------------------------------------------------------- 门禁


def test_non_terminal_experiment_is_rejected() -> None:
    scenarios = _scenarios(1)
    manifest = _manifest(scenarios)
    with pytest.raises(MemoryExperimentNotTerminal) as excinfo:
        compare_memory_experiments(
            baseline_experiment=_experiment(manifest, status="running"),
            candidate_experiment=_experiment(
                manifest, experiment_id=CANDIDATE_EXPERIMENT_ID
            ),
            baseline_attempts=_attempts_for({"s000": [[True]]}),
            candidate_attempts=_attempts_for({"s000": [[True]]}),
            baseline_candidate_id="cand-a",
            candidate_candidate_id="cand-b",
        )
    assert excinfo.value.code == "MEMORY_EXPERIMENT_NOT_TERMINAL"


def test_missing_manifest_metadata_is_rejected() -> None:
    scenarios = _scenarios(1)
    manifest = _manifest(scenarios)
    broken = copy.deepcopy(manifest)
    broken["source"]["source_revision"] = None
    with pytest.raises(MemoryComparisonMetadataMissing) as excinfo:
        compare_memory_experiments(
            baseline_experiment=_experiment(broken),
            candidate_experiment=_experiment(
                manifest, experiment_id=CANDIDATE_EXPERIMENT_ID
            ),
            baseline_attempts=_attempts_for({"s000": [[True]]}),
            candidate_attempts=_attempts_for({"s000": [[True]]}),
            baseline_candidate_id="cand-a",
            candidate_candidate_id="cand-b",
        )
    assert excinfo.value.code == "COMPARISON_METADATA_MISSING"


def test_same_scenario_different_revision_is_rejected() -> None:
    base_scenarios = _scenarios(1, revision=1)
    cand_scenarios = _scenarios(1, revision=2)
    with pytest.raises(MemoryComparisonIncompatible) as excinfo:
        _compare(
            base_scenarios,
            {"s000": [[True]]},
            {"s000": [[True]]},
            base_manifest=_manifest(base_scenarios),
            cand_manifest=_manifest(cand_scenarios, candidate_id="cand-b"),
        )
    assert excinfo.value.code == "COMPARISON_INCOMPATIBLE"
    assert "SCENARIO_REVISION_MISMATCH" in str(excinfo.value)


def test_undeclared_model_change_is_rejected() -> None:
    scenarios = _scenarios(1)
    base_manifest = _manifest(scenarios, models={"agent": {"model": "eval-model"}})
    cand_manifest = _manifest(
        scenarios, candidate_id="cand-b", models={"agent": {"model": "eval-model-2"}}
    )
    with pytest.raises(MemoryComparisonIncompatible) as excinfo:
        _compare(
            scenarios,
            {"s000": [[True]]},
            {"s000": [[True]]},
            base_manifest=base_manifest,
            cand_manifest=cand_manifest,
        )
    assert "UNDECLARED_MODEL_CHANGE" in str(excinfo.value)


def test_declared_model_change_is_comparable_but_not_single_factor() -> None:
    scenarios = _scenarios(1)
    base_manifest = _manifest(scenarios, models={"agent": {"model": "eval-model"}})
    cand_manifest = _manifest(
        scenarios, candidate_id="cand-b", models={"agent": {"model": "eval-model-2"}}
    )
    result = _compare(
        scenarios,
        {"s000": [[True]]},
        {"s000": [[True]]},
        changed_fields=("/environment/models/agent/model", "/candidates/0"),
        base_manifest=base_manifest,
        cand_manifest=cand_manifest,
    ).to_dict()
    assert result["attribution"]["single_factor"] is False
    assert "/environment/models/agent/model" in result["applied_changes"]


def test_different_code_version_is_provenance_not_condition() -> None:
    scenarios = _scenarios(1)
    base_manifest = _manifest(scenarios)
    cand_manifest = _manifest(
        scenarios,
        candidate_id="cand-b",
        source_state={
            "source_revision": "f00dbabe",
            "source_revision_reason": None,
            "source_dirty_digest": "e" * 64,
            "source_dirty_digest_reason": None,
        },
    )
    result = _compare(
        scenarios,
        {"s000": [[True]]},
        {"s000": [[True]]},
        base_manifest=base_manifest,
        cand_manifest=cand_manifest,
    ).to_dict()

    # 代码版本不同就是被测变量：不要求写进 changed_fields，但必须记录出处。
    assert result["status"] == "completed"
    assert result["applied_changes"] == []
    assert "/source/source_revision" in result["attribution"]["source_changes"]
    assert result["attribution"]["single_factor"] is True


def test_declared_retrieval_parameter_change_is_comparable() -> None:
    scenarios = _scenarios(1)
    base_manifest = _manifest(scenarios, recall_limit=10)
    cand_manifest = _manifest(scenarios, candidate_id="cand-b", recall_limit=20)
    result = _compare(
        scenarios,
        {"s000": [[True]]},
        {"s000": [[True]]},
        changed_fields=("/candidates/0/memory_settings/recall_limit",),
        base_manifest=base_manifest,
        cand_manifest=cand_manifest,
    ).to_dict()
    assert result["status"] == "completed"
    assert result["applied_changes"] == ["/candidates/0/memory_settings/recall_limit"]
    assert result["attribution"]["single_factor"] is True


def test_undeclared_retrieval_change_is_rejected() -> None:
    scenarios = _scenarios(1)
    with pytest.raises(MemoryComparisonIncompatible):
        _compare(
            scenarios,
            {"s000": [[True]]},
            {"s000": [[True]]},
            base_manifest=_manifest(scenarios, recall_limit=10),
            cand_manifest=_manifest(scenarios, candidate_id="cand-b", recall_limit=20),
        )


def test_attempt_revision_mismatch_is_rejected() -> None:
    scenarios = _scenarios(1)
    attempts = _attempts_for({"s000": [[True]]})
    attempts[0]["result"]["memory"]["revision"] = 2
    attempts[0]["case_snapshot"]["memory_scenario_ref"]["revision"] = 2
    with pytest.raises(MemoryComparisonIncompatible) as excinfo:
        _compare(
            scenarios,
            {},
            {"s000": [[True]]},
            base_attempts=attempts,
        )
    assert "SCENARIO_REVISION_MISMATCH" in str(excinfo.value)


def test_dataset_or_grading_drift_is_incompatible() -> None:
    scenarios = _scenarios(1)
    base_manifest = _manifest(scenarios)
    cand_manifest = _manifest(scenarios, candidate_id="cand-b")
    cand_manifest["grading"]["grading_digest"] = "9" * 64
    with pytest.raises(MemoryComparisonIncompatible) as excinfo:
        _compare(
            scenarios,
            {"s000": [[True]]},
            {"s000": [[True]]},
            base_manifest=base_manifest,
            cand_manifest=cand_manifest,
        )
    assert "grading.grading_digest" in str(excinfo.value)


# --------------------------------------------------------------------------- 缺 trial


def test_missing_one_trial_marks_incomplete_not_zero() -> None:
    scenarios = _scenarios(2)
    base_attempts = _attempts_for(
        {"s000": [[True, True], [True, True]], "s001": [[True], [True]]}
    )
    cand_attempts = _attempts_for(
        {"s000": [[True, True]], "s001": [[True], [True]]}
    )
    result = _compare(
        scenarios,
        {},
        {},
        repetitions=2,
        base_attempts=base_attempts,
        cand_attempts=cand_attempts,
    ).to_dict()

    assert result["status"] == "incomplete"
    assert result["measurement_status"] == "incomplete"
    assert result["paired"] == 1
    assert result["planned"] == 2
    excluded = {row["scenario_id"]: row for row in result["excluded"]}
    assert "TRIAL_MISSING" in excluded["s000"]["details"]
    assert result["statistical_direction"] == "inconclusive"
    assert result["adoption_decision"]["decision"] in {
        "review_required",
        "diagnostic_only",
        "insufficient",
    }
    # 缺失场景不进入分母：宏平均只由完整配对的 s001 决定。
    assert result["scenario_macro"]["baseline"] == 1.0


def test_experiment_measurement_status_incomplete_is_carried_over() -> None:
    scenarios = _scenarios(20)
    base_qa = {row["scenario_id"]: [[True]] for row in scenarios}
    cand_qa = {row["scenario_id"]: [[True]] for row in scenarios}
    result = compare_memory_experiments(
        baseline_experiment=_experiment(_manifest(scenarios)),
        candidate_experiment=_experiment(
            _manifest(scenarios, candidate_id="cand-b"),
            experiment_id=CANDIDATE_EXPERIMENT_ID,
            measurement_status="incomplete",
        ),
        baseline_attempts=_attempts_for(base_qa),
        candidate_attempts=_attempts_for(cand_qa),
        baseline_candidate_id="cand-a",
        candidate_candidate_id="cand-b",
    ).to_dict()
    assert result["status"] == "incomplete"
    assert "MEASUREMENT_STATUS_INCOMPLETE" in result["reasons"]
    # 同一批数据如果没有这层状态，方向本可以确定。
    assert result["subset_statistical_direction"] == "inconclusive"
    assert result["statistical_direction"] == "inconclusive"


def test_failed_execution_is_unknown_not_a_zero_score() -> None:
    scenarios = _scenarios(1)
    candidate_attempts = _attempts_for({"s000": [[True]]})
    candidate_attempts[0]["execution_status"] = "failed"
    result = _compare(
        scenarios,
        {"s000": [[True]]},
        {},
        cand_attempts=candidate_attempts,
    ).to_dict()
    assert result["status"] == "incomplete"
    assert result["paired"] == 0
    excluded = {row["scenario_id"]: row for row in result["excluded"]}
    assert excluded["s000"]["reason"] == "SCENE_MISSING_IN_CANDIDATE"


def test_unjudged_probe_is_incomparable_and_not_failure() -> None:
    scenarios = _scenarios(1)
    result = _compare(
        scenarios, {"s000": [[True, None]]}, {"s000": [[True, True]]}
    ).to_dict()
    assert result["status"] == "incomplete"
    assert result["scenario_macro"]["baseline"] is None
    assert result["paired"] == 0


# --------------------------------------------------------------------------- 手算


def test_macro_micro_and_cost_are_hand_computable() -> None:
    scenarios = _scenarios(2)
    base_qa = {"s000": [[True, False], [True, True]], "s001": [[True], [True]]}
    cand_qa = {"s000": [[True, True], [True, True]], "s001": [[False], [True]]}
    # 成本：基线抽取 Token 10+10+20+20=60，候选 20×4=80；Judge 基线 5×4=20，
    # 候选缺一条 → 15，覆盖 3/4，完整性必须为 False。
    cost_base = {
        ("s000", 0): [_metric("extraction_tokens", 10, unit="token", scope="dataset"),
                      _metric("judge_tokens", 5, unit="token", scope="dataset")],
        ("s000", 1): [_metric("extraction_tokens", 10, unit="token", scope="dataset"),
                      _metric("judge_tokens", 5, unit="token", scope="dataset")],
        ("s001", 0): [_metric("extraction_tokens", 20, unit="token", scope="dataset"),
                      _metric("judge_tokens", 5, unit="token", scope="dataset")],
        ("s001", 1): [_metric("extraction_tokens", 20, unit="token", scope="dataset"),
                      _metric("judge_tokens", 5, unit="token", scope="dataset")],
    }
    cost_cand = {
        ("s000", 0): [_metric("extraction_tokens", 20, unit="token", scope="dataset"),
                      _metric("judge_tokens", 5, unit="token", scope="dataset")],
        ("s000", 1): [_metric("extraction_tokens", 20, unit="token", scope="dataset"),
                      _metric("judge_tokens", 5, unit="token", scope="dataset")],
        ("s001", 0): [_metric("extraction_tokens", 20, unit="token", scope="dataset"),
                      _metric("judge_tokens", 5, unit="token", scope="dataset")],
        ("s001", 1): [_metric("extraction_tokens", 20, unit="token", scope="dataset"),
                      _metric("judge_tokens", None, unit="token", scope="dataset")],
    }
    result = _compare(
        scenarios,
        base_qa,
        cand_qa,
        repetitions=2,
        base_attempts=_attempts_for(base_qa, trial_metrics=cost_base),
        cand_attempts=_attempts_for(cand_qa, trial_metrics=cost_cand),
    ).to_dict()

    # 场景分：基线 s000=(0.5+1)/2=0.75，s001=1.0 → 宏平均 0.875；
    # 候选 s000=1.0，s001=0.5 → 0.75；差值 -12.5 个百分点。
    assert result["scenario_macro"]["baseline"] == pytest.approx(0.875)
    assert result["scenario_macro"]["candidate"] == pytest.approx(0.75)
    assert result["scenario_macro"]["delta_pp"] == pytest.approx(-12.5)
    # 题级 micro：两边都是 5/6（s000 4/4 与 s001 1/2 对调后总数相同）。
    assert result["probe_micro"]["baseline"] == pytest.approx(5 / 6)
    assert result["probe_micro"]["candidate"] == pytest.approx(5 / 6)
    assert result["probe_micro"]["delta_pp"] == pytest.approx(0.0)
    # 场景配对：s000 改善（+25pp），s001 退化（-50pp）。
    pairs = {row["scenario_id"]: row for row in result["scene_pairs"]}
    assert pairs["s000"]["delta_pp"] == pytest.approx(25.0)
    assert pairs["s001"]["delta_pp"] == pytest.approx(-50.0)
    # 成本拆分：写入 + Judge；未知不填 0。
    write = result["cost"]["write"]["extraction_tokens"]
    assert write["baseline"] == pytest.approx(60.0)
    assert write["candidate"] == pytest.approx(80.0)
    assert write["ratio"] == pytest.approx(80 / 60)
    judge = result["cost"]["judge"]["judge_tokens"]
    assert judge["baseline"] == pytest.approx(20.0)
    assert judge["candidate"] == pytest.approx(15.0)
    assert judge["complete"] is False
    assert judge["ratio"] is None
    assert result["cost"]["summary"]["cost_unknown"] is True
    assert result["cost"]["summary"]["cost_ratio"] is None


def test_cost_unknown_is_never_filled_with_zero() -> None:
    scenarios = _scenarios(1)
    result = _compare(scenarios, {"s000": [[True]]}, {"s000": [[True]]}).to_dict()
    entry = result["cost"]["totals"]["estimated_cost"]
    assert entry["baseline"] is None
    assert entry["candidate"] is None
    assert entry["ratio"] is None
    assert result["cost"]["summary"]["cost_unknown"] is True


# --------------------------------------------------------------------------- 采用判定


def _improved_pair(
    *, policy: MemoryAdoptionPolicy | None = None, probe_extra: bool = False,
    trial_extra: bool = False,
) -> dict[str, Any]:
    scenarios = _scenarios(20)
    base_qa = {row["scenario_id"]: [[True, False]] for row in scenarios}
    cand_qa = {row["scenario_id"]: [[True, True]] for row in scenarios}
    probe_metrics: dict[tuple[str, int], list[dict[str, Any]]] = {}
    trial_metrics: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in scenarios:
        key = (row["scenario_id"], 0)
        if probe_extra:
            probe_metrics[key] = [_metric("grounded_memory_success", 0.0)]
        if trial_extra:
            trial_metrics[key] = [
                _metric("estimated_cost", 1.0, unit="currency", scope="dataset")
            ]
    base_attempts = _attempts_for(base_qa, probe_metrics=probe_metrics, trial_metrics=trial_metrics)
    if probe_extra:
        for attempt in base_attempts:
            for probe in attempt["result"]["memory"]["probe_results"]:
                for metric in probe["metrics"]:
                    if metric["id"] == "grounded_memory_success":
                        metric["value"] = 1.0
    return _compare(
        scenarios,
        base_qa,
        cand_qa,
        policy=policy,
        base_attempts=base_attempts,
        cand_attempts=_attempts_for(cand_qa, probe_metrics=probe_metrics, trial_metrics=trial_metrics),
    ).to_dict()


def test_adoption_requires_minimum_effect_and_complete_measurement() -> None:
    result = _improved_pair(policy=MemoryAdoptionPolicy(minimum_effect_pp=5.0))
    assert result["interval"]["low_pp"] == pytest.approx(50.0)
    assert result["statistical_direction"] == "improved"
    assert result["adoption_decision"]["decision"] == "adoptable"

    # 没有 minimum_effect_pp：只出诊断，不自动采用（统计方向仍是 improved）。
    diagnostic = _improved_pair()
    assert diagnostic["statistical_direction"] == "improved"
    assert diagnostic["adoption_decision"]["decision"] == "diagnostic_only"

    # 同一份数据若缺 trial，则测量不完整 → 不得 adoptable。
    scenarios = _scenarios(20)
    base_qa = {row["scenario_id"]: [[True, False]] for row in scenarios}
    cand_qa = {row["scenario_id"]: [[True, True]] for row in scenarios}
    cand_attempts = _attempts_for(cand_qa)
    cand_attempts.pop()
    incomplete = _compare(
        scenarios,
        {},
        {},
        policy=MemoryAdoptionPolicy(minimum_effect_pp=5.0),
        base_attempts=_attempts_for(base_qa),
        cand_attempts=cand_attempts,
    ).to_dict()
    assert incomplete["status"] == "incomplete"
    assert incomplete["adoption_decision"]["decision"] != "adoptable"
    assert "MEASUREMENT_INCOMPLETE" in incomplete["adoption_decision"]["reasons"]


def test_regression_beyond_tolerance_rejects_adoption() -> None:
    result = _improved_pair(
        policy=MemoryAdoptionPolicy(
            minimum_effect_pp=5.0,
            max_regression_pp_by_metric={"grounded_memory_success": 2.0},
        ),
        probe_extra=True,
    )
    # 候选 grounded_memory_success 全为 0，基线全为 1 → 差值 -100pp。
    decision = result["adoption_decision"]
    assert decision["decision"] == "rejected"
    assert "REGRESSION_EXCEEDS_TOLERANCE:grounded_memory_success" in decision["reasons"]
    entry = decision["checks"]["regressions"]["grounded_memory_success"]
    assert entry["verdict"] == "exceeds_tolerance"


def test_cost_budget_is_enforced_with_declared_ratio() -> None:
    result = _improved_pair(
        policy=MemoryAdoptionPolicy(minimum_effect_pp=5.0, max_cost_ratio=1.5),
        trial_extra=True,
    )
    # 双方费用都是 20，比值 1.0 ≤ 1.5。
    assert result["cost"]["summary"]["cost_ratio"] == pytest.approx(1.0)
    assert result["adoption_decision"]["decision"] == "adoptable"

    scenarios = _scenarios(20)
    base_qa = {row["scenario_id"]: [[True, False]] for row in scenarios}
    cand_qa = {row["scenario_id"]: [[True, True]] for row in scenarios}
    base_cost = {
        (row["scenario_id"], 0): [
            _metric("estimated_cost", 1.0, unit="currency", scope="dataset")
        ]
        for row in scenarios
    }
    cand_cost = {
        (row["scenario_id"], 0): [
            _metric("estimated_cost", 2.0, unit="currency", scope="dataset")
        ]
        for row in scenarios
    }
    over = _compare(
        scenarios,
        base_qa,
        cand_qa,
        policy=MemoryAdoptionPolicy(minimum_effect_pp=5.0, max_cost_ratio=1.5),
        base_attempts=_attempts_for(base_qa, trial_metrics=base_cost),
        cand_attempts=_attempts_for(cand_qa, trial_metrics=cand_cost),
    ).to_dict()
    assert over["cost"]["summary"]["cost_ratio"] == pytest.approx(2.0)
    assert over["adoption_decision"]["decision"] == "rejected"
    assert "BUDGET_EXCEEDED:cost_ratio" in over["adoption_decision"]["reasons"]


def test_uncalibrated_required_metric_requires_review() -> None:
    scenarios = _scenarios(20)
    base_qa = {row["scenario_id"]: [[True, False]] for row in scenarios}
    cand_qa = {row["scenario_id"]: [[True, True]] for row in scenarios}
    result = _compare(
        scenarios,
        base_qa,
        cand_qa,
        policy=MemoryAdoptionPolicy(minimum_effect_pp=5.0),
        base_attempts=_attempts_for(base_qa, qa_calibration="uncalibrated"),
        cand_attempts=_attempts_for(cand_qa, qa_calibration="uncalibrated"),
    ).to_dict()
    assert "qa_accuracy" in result["uncalibrated_metrics"]
    # 未校准 → 主指标不完整，不能凭子集提升自动采用。
    assert result["status"] == "incomplete"
    assert result["measurement_status"] == "incomplete"
    assert result["statistical_direction"] == "inconclusive"
    assert "REQUIRED_METRIC_UNCALIBRATED" in result["reasons"]
    assert result["adoption_decision"]["decision"] == "review_required"
    assert any(
        "UNCALIBRATED" in reason for reason in result["adoption_decision"]["reasons"]
    )


def test_critical_scope_leak_blocks_even_when_scores_improve() -> None:
    scenarios = _scenarios(20)
    base_qa = {row["scenario_id"]: [[True, False]] for row in scenarios}
    cand_qa = {row["scenario_id"]: [[True, True]] for row in scenarios}
    leaks = {
        ("s000", 0): [
            _metric("scope_leak_rate", 0.5, scope="scenario", reasons=("SCOPE_LEAK",))
        ]
    }
    result = _compare(
        scenarios,
        base_qa,
        cand_qa,
        policy=MemoryAdoptionPolicy(minimum_effect_pp=5.0),
        base_attempts=_attempts_for(base_qa),
        cand_attempts=_attempts_for(cand_qa, trial_metrics=leaks),
    ).to_dict()
    assert result["statistical_direction"] == "improved"
    assert result["gates"]["blocked"] is True
    assert result["gates"]["scope_leak"]["candidate_failed"] is True
    assert result["adoption_decision"]["decision"] == "blocked"


def test_deletion_resurrection_blocks_adoption() -> None:
    scenarios = _scenarios(20)
    base_qa = {row["scenario_id"]: [[True, False]] for row in scenarios}
    cand_qa = {row["scenario_id"]: [[True, True]] for row in scenarios}
    forget = {
        ("s000", 0): [
            _metric(
                "forget_behavior_success",
                0.0,
                scope="scenario",
                reasons=("DELETE_REVIVED",),
            )
        ]
    }
    result = _compare(
        scenarios,
        base_qa,
        cand_qa,
        policy=MemoryAdoptionPolicy(minimum_effect_pp=5.0),
        base_attempts=_attempts_for(base_qa),
        cand_attempts=_attempts_for(cand_qa, trial_metrics=forget),
    ).to_dict()
    assert result["gates"]["deletion_resurrection"]["candidate_failed"] is True
    assert result["adoption_decision"]["decision"] == "blocked"


def test_zero_observed_violations_is_not_a_safety_guarantee() -> None:
    result = _improved_pair(policy=MemoryAdoptionPolicy(minimum_effect_pp=5.0))
    assert result["gates"]["blocked"] is False
    # 未观测到违规只代表样本没有触发：门禁值为空，报告写明"不等于安全保证"。
    assert result["gates"]["scope_leak"]["candidate_values"] == []
    assert result["gates"]["deletion_resurrection"]["candidate_values"] == []


def test_adoption_decision_is_separate_from_direction() -> None:
    decision = adoption_decision(
        statistical_direction="improved",
        interval_pp=(1.0, 3.0),
        delta_pp=2.0,
        policy=MemoryAdoptionPolicy(minimum_effect_pp=5.0),
        measurement_complete=True,
        critical_violations=("scope_leak",),
    )
    assert decision["decision"] == "blocked"
    assert decision["checks"]["statistical_direction"] == "improved"


# --------------------------------------------------------------------------- 官方协议


def test_official_protocol_metric_is_reported_separately() -> None:
    scenarios = _scenarios(1)
    official = [_metric("longmemeval_binary_judge", 0.5, scope="dataset")]
    result = _compare(
        scenarios,
        {"s000": [[True]]},
        {"s000": [[True]]},
        base_attempts=_attempts_for({"s000": [[True]]}, trial_metrics={("s000", 0): official}),
        cand_attempts=_attempts_for({"s000": [[True]]}, trial_metrics={("s000", 0): official}),
    ).to_dict()
    assert result["official_protocol"]["separated"] is True
    rows = result["official_protocol"]["metrics"]
    assert [row["metric_id"] for row in rows] == ["longmemeval_binary_judge"]
    assert rows[0]["official"] is False
    assert "longmemeval_binary_judge" not in [
        row["metric_id"] for row in result["secondary"]
    ]


# --------------------------------------------------------------------------- 分页


def test_pagination_does_not_change_statistics() -> None:
    scenarios = _scenarios(22)
    base_qa = {row["scenario_id"]: [[True, False]] for row in scenarios}
    cand_qa = {row["scenario_id"]: [[True, True]] for row in scenarios}
    result = _compare(scenarios, base_qa, cand_qa)
    full = result.to_dict()

    first = page_comparison_pairs(result, limit=5)
    second = page_comparison_pairs(result, limit=5, after_key=first["next_after_key"])
    assert len(first["data"]) == 5
    assert len(second["data"]) == 5
    assert first["next_after_key"] == first["data"][-1]["scenario_id"]
    # 统计量来自全量 22 个配对场景，分页不重算。
    assert first["statistics"]["paired"] == 22 == full["paired"]
    assert first["statistics"]["scenario_macro"] == full["scenario_macro"]
    assert first["statistics"]["probe_micro"] == full["probe_micro"]
    assert first["statistics"]["interval"] == full["interval"]
    assert first["statistics"] == second["statistics"]
    assert len(full["scene_pairs"]) == 22


def test_result_snapshot_is_immutable() -> None:
    scenarios = _scenarios(1)
    result = _compare(scenarios, {"s000": [[True]]}, {"s000": [[True]]})
    first = result.to_dict()
    first["paired"] = 999
    assert result.to_dict()["paired"] == 1


def test_inputs_are_not_mutated() -> None:
    scenarios = _scenarios(1)
    base_manifest = _manifest(scenarios, recall_limit=10)
    cand_manifest = _manifest(scenarios, candidate_id="cand-b", recall_limit=20)
    snapshot = copy.deepcopy(base_manifest)
    _compare(
        scenarios,
        {"s000": [[True]]},
        {"s000": [[True]]},
        changed_fields=("/candidates/0/memory_settings/recall_limit",),
        base_manifest=base_manifest,
        cand_manifest=cand_manifest,
    )
    assert base_manifest == snapshot
