"""记忆专项模型契约测试（contract §1 / §3 / §4 / §8.1–8.3）。

这些用例钉死的是**跨 WU 冻结的接口基线**：WU-02/03/04/05/06/07/08/09 都复用这里的
字段名与不变量。断言全部对着真实结构（重复 ID、悬空证据、非法 split、bool 伪装 int、
缺时区、gold 泄漏进执行载荷），不接受"按示意数据硬编码返回值"。
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from pydantic import ValidationError

from nanobot.evaluation.memory.models import (
    MemoryCandidate,
    MemoryCapabilities,
    MemoryComparison,
    MemoryDatasetRevision,
    MemoryExecutionInput,
    MemoryExperiment,
    MemoryGold,
    MemoryGradingPolicy,
    MemoryMetricResult,
    MemoryProbe,
    MemoryScenario,
    MemorySessionEvent,
    build_execution_input,
)
from nanobot.evaluation.memory.protocol import MemoryAdapter

from .memory_fixtures import (
    make_memory_candidate,
    make_memory_dataset_payload,
    make_memory_dataset_source,
    make_memory_scenario,
)

# --------------------------------------------------------------------------- 场景


def test_scenario_accepts_the_contract_example() -> None:
    scenario = MemoryScenario.model_validate(make_memory_scenario())
    assert scenario.schema_version == "memory-scenario/v1"
    assert scenario.scenario_id == "fact-001"
    assert scenario.revision == 1
    assert scenario.split == "regression"
    assert scenario.template_family == "fact-city-a"
    assert scenario.cluster_id == "conversation-a"
    assert scenario.scope.subject_id == "synthetic-u1"
    assert [event.type for event in scenario.events] == ["session", "settle"]
    assert isinstance(scenario.events[0], MemorySessionEvent)
    assert [probe.id for probe in scenario.probes] == ["q1"]
    assert scenario.gold.probes["q1"].answerability == "answerable"


def test_scenario_rejects_unknown_field() -> None:
    payload = make_memory_scenario()
    payload["unknown_field"] = "x"
    with pytest.raises(ValidationError):
        MemoryScenario.model_validate(payload)


def test_scenario_rejects_duplicate_probe_id() -> None:
    payload = make_memory_scenario()
    payload["probes"] = [*payload["probes"], dict(payload["probes"][0])]
    with pytest.raises(ValidationError, match="DUPLICATE_PROBE_ID"):
        MemoryScenario.model_validate(payload)


def test_scenario_rejects_duplicate_message_id() -> None:
    payload = make_memory_scenario()
    payload["events"][0]["messages"].append(
        {"id": "m1", "role": "user", "content": "重复的消息 id"}
    )
    with pytest.raises(ValidationError, match="DUPLICATE_MESSAGE_ID"):
        MemoryScenario.model_validate(payload)


def test_scenario_rejects_dangling_evidence_message_id() -> None:
    payload = make_memory_scenario()
    payload["gold"]["q1"]["required_facts"][0]["evidence_message_ids"] = ["m404"]
    with pytest.raises(ValidationError, match="DANGLING_EVIDENCE"):
        MemoryScenario.model_validate(payload)


def test_scenario_rejects_gold_key_without_probe() -> None:
    payload = make_memory_scenario()
    payload["gold"]["q404"] = {"answerability": "answerable"}
    with pytest.raises(ValidationError, match="GOLD_WITHOUT_PROBE"):
        MemoryScenario.model_validate(payload)


def test_scenario_rejects_invalid_split() -> None:
    payload = make_memory_scenario(split="production")
    with pytest.raises(ValidationError):
        MemoryScenario.model_validate(payload)


def test_scenario_rejects_bool_revision() -> None:
    """契约 §2：整数与布尔严格区分，``True`` 不能当 revision=1。"""
    payload = make_memory_scenario(revision=True)
    with pytest.raises(ValidationError):
        MemoryScenario.model_validate(payload)


def test_scenario_rejects_naive_timestamp() -> None:
    """契约 §1：session 的 ``at`` 必须带时区。"""
    payload = make_memory_scenario()
    payload["events"][0]["at"] = "2026-11-01T09:00:00"
    with pytest.raises(ValidationError, match="TIMEZONE_REQUIRED"):
        MemoryScenario.model_validate(payload)


def test_scenario_rejects_time_going_backwards() -> None:
    payload = make_memory_scenario()
    payload["events"].insert(
        0,
        {
            "type": "session",
            "id": "s0",
            "at": "2026-11-03T09:00:00+08:00",
            "messages": [{"id": "m0", "role": "user", "content": "更晚的会话"}],
        },
    )
    with pytest.raises(ValidationError, match="TIME_REVERSED"):
        MemoryScenario.model_validate(payload)


def test_settle_must_reference_an_earlier_session() -> None:
    payload = make_memory_scenario()
    payload["events"] = [{"type": "settle", "after_session": "s404"}]
    with pytest.raises(ValidationError, match="SETTLE_WITHOUT_SESSION"):
        MemoryScenario.model_validate(payload)


def test_settle_timeout_range_is_enforced() -> None:
    payload = make_memory_scenario()
    payload["events"][1]["timeout_s"] = 901
    with pytest.raises(ValidationError):
        MemoryScenario.model_validate(payload)


def test_probe_requires_timezone_and_fresh_session_flag() -> None:
    payload = make_memory_scenario()
    payload["probes"][0]["at"] = "2026-11-02T09:00:00"
    with pytest.raises(ValidationError, match="TIMEZONE_REQUIRED"):
        MemoryScenario.model_validate(payload)


def test_probe_cannot_inject_system_role() -> None:
    """契约 §1：system/tool 角色不允许用户注入。"""
    payload = make_memory_scenario()
    payload["events"][0]["messages"][0]["role"] = "system"
    with pytest.raises(ValidationError):
        MemoryScenario.model_validate(payload)


def test_cluster_id_defaults_to_scenario_id() -> None:
    payload = make_memory_scenario()
    payload.pop("cluster_id")
    scenario = MemoryScenario.model_validate(payload)
    assert scenario.cluster_id == scenario.scenario_id


# --------------------------------------------------------------------------- gold


def test_gold_defaults_to_targeted_annotation_scope() -> None:
    """契约 §8.2：旧记录默认 targeted，不能自动当完整抽取标注。"""
    scenario = MemoryScenario.model_validate(make_memory_scenario())
    assert scenario.gold.annotation_scope == "targeted"
    assert scenario.gold.extraction_facts == []


def test_closed_world_gold_requires_extraction_facts() -> None:
    payload = make_memory_scenario()
    payload["gold"]["annotation_scope"] = "closed_world"
    with pytest.raises(ValidationError, match="CLOSED_WORLD_WITHOUT_FACTS"):
        MemoryScenario.model_validate(payload)


def test_closed_world_gold_keeps_extraction_facts_separate_from_required_facts() -> None:
    payload = make_memory_scenario()
    payload["gold"]["annotation_scope"] = "closed_world"
    payload["gold"]["extraction_facts"] = [
        {"key": "city", "value": "青岚城", "evidence_message_ids": ["m1"]}
    ]
    scenario = MemoryScenario.model_validate(payload)
    assert scenario.gold.annotation_scope == "closed_world"
    assert len(scenario.gold.extraction_facts) == 1
    assert len(scenario.gold.probes["q1"].required_facts) == 1


def test_gold_model_rejects_unknown_probe_level_field() -> None:
    with pytest.raises(ValidationError):
        MemoryGold.model_validate(
            {"q1": {"answerability": "answerable", "surprise": 1}}
        )


def test_forbidden_current_values_and_evidence_sets_are_typed() -> None:
    payload = make_memory_scenario()
    payload["gold"]["q1"]["forbidden_current_values"] = [
        {"key": "city", "value": "旧城"}
    ]
    payload["gold"]["q1"]["allowed_evidence_sets"] = [
        [{"key": "city", "value": "青岚城"}],
        [{"key": "city", "value": "青岚城别名"}],
    ]
    scenario = MemoryScenario.model_validate(payload)
    gold = scenario.gold.probes["q1"]
    assert gold.forbidden_current_values[0].value == "旧城"
    assert len(gold.allowed_evidence_sets) == 2


# --------------------------------------------------------------- 执行载荷与 gold 隔离


def test_execution_input_excludes_gold_and_source_and_criteria() -> None:
    """契约 §1：执行端只拿到 scope/events/probes/reference_state/track/候选。"""
    scenario = MemoryScenario.model_validate(make_memory_scenario())
    execution = build_execution_input(
        scenario, track="end_to_end", candidates=[MemoryCandidate.model_validate(
            make_memory_candidate())]
    )
    assert isinstance(execution, MemoryExecutionInput)
    payload = execution.model_dump(mode="json")
    assert "gold" not in payload
    assert "source" not in payload
    assert "license" not in payload
    assert "grading" not in payload
    # gold 的结构与判据不得出现（会话原文里出现同一个词是正常的——那是历史本身）。
    for leaked in ("required_facts", "answerability", "annotation_scope",
                   "evidence_message_ids", "forbidden_current_values"):
        assert leaked not in str(payload)
    assert payload["scenario_id"] == "fact-001"
    assert payload["track"] == "end_to_end"
    assert payload["scope"]["subject_id"] == "synthetic-u1"
    assert [event["type"] for event in payload["events"]] == ["session", "settle"]


def test_execution_input_keeps_probe_questions_but_not_gold_answers() -> None:
    scenario = MemoryScenario.model_validate(make_memory_scenario())
    execution = build_execution_input(scenario, track="retrieval", candidates=[])
    assert execution.probes[0].question == "青岚城"
    assert "answerability" not in execution.model_dump(mode="json")


def test_execution_input_rejects_unknown_track() -> None:
    scenario = MemoryScenario.model_validate(make_memory_scenario())
    with pytest.raises(ValueError):
        build_execution_input(scenario, track="whatever", candidates=[])


# --------------------------------------------------------------------------- 指标


def test_metric_result_keeps_missing_value_as_null_not_zero() -> None:
    """契约 §4/§8.4：字段缺失不是 0。"""
    metric = MemoryMetricResult.model_validate(
        {
            "id": "extraction_precision",
            "version": 1,
            "grading_revision": 3,
            "review_digest": None,
            "unit": "ratio",
            "value": None,
            "numerator": None,
            "denominator": None,
            "status": "not_assessable",
            "scope": "probe",
            "evidence_refs": [],
            "reasons": ["GOLD_TARGETED_ONLY"],
            "coverage": {"scored": 0, "planned": 1, "reasons": ["GOLD_TARGETED_ONLY"]},
            "interval": None,
        }
    )
    assert metric.value is None
    assert metric.numerator is None
    assert metric.denominator is None
    assert metric.status == "not_assessable"
    assert metric.review_digest is None


def test_metric_result_rejects_unknown_unit_and_scope() -> None:
    base: dict[str, Any] = {
        "id": "qa_accuracy",
        "version": 1,
        "grading_revision": 1,
        "unit": "ratio",
        "value": 1.0,
        "numerator": 1,
        "denominator": 1,
        "status": "scored",
        "scope": "probe",
        "coverage": {"scored": 1, "planned": 1},
    }
    with pytest.raises(ValidationError):
        MemoryMetricResult.model_validate({**base, "unit": "percent"})
    with pytest.raises(ValidationError):
        MemoryMetricResult.model_validate({**base, "scope": "experiment"})
    with pytest.raises(ValidationError):
        MemoryMetricResult.model_validate({**base, "version": 2})


def test_metric_result_rejects_scored_without_value() -> None:
    with pytest.raises(ValidationError, match="SCORED_WITHOUT_VALUE"):
        MemoryMetricResult.model_validate(
            {
                "id": "qa_accuracy",
                "version": 1,
                "grading_revision": 1,
                "unit": "ratio",
                "value": None,
                "status": "scored",
                "scope": "probe",
                "coverage": {"scored": 1, "planned": 1},
            }
        )


def test_metric_result_accepts_semantic_grader_fingerprint() -> None:
    metric = MemoryMetricResult.model_validate(
        {
            "id": "qa_accuracy",
            "version": 1,
            "grading_revision": 1,
            "unit": "ratio",
            "value": 1.0,
            "numerator": 1,
            "denominator": 1,
            "status": "scored",
            "scope": "probe",
            "coverage": {"scored": 1, "planned": 1},
            "grader_fingerprint": "judge/v1",
            "calibration_status": "calibrated",
        }
    )
    assert metric.grader_fingerprint == "judge/v1"
    assert metric.calibration_status == "calibrated"


# ------------------------------------------------------------- 集合修订 / 实验 / 比较


def test_dataset_revision_requires_full_source_block() -> None:
    source = make_memory_dataset_source()
    source.pop("license")
    with pytest.raises(ValidationError):
        MemoryDatasetRevision.model_validate(
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "suite_id": "22222222-2222-4222-8222-222222222222",
                "dataset_version": "memory-core-v1",
                "status": "staging",
                "split_counts": {"regression": 1},
                "source": source,
                "definition_digest": "d" * 64,
            }
        )


def test_dataset_revision_status_is_closed_set() -> None:
    with pytest.raises(ValidationError):
        MemoryDatasetRevision.model_validate(
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "suite_id": "22222222-2222-4222-8222-222222222222",
                "dataset_version": "memory-core-v1",
                "status": "running",
                "split_counts": {},
                "source": make_memory_dataset_source(),
                "definition_digest": "d" * 64,
            }
        )


def test_grading_policy_carries_algorithm_versions_and_adoption_threshold() -> None:
    policy = MemoryGradingPolicy.model_validate(
        {
            "fact_matching_version": "fact-matching/v1",
            "metric_aggregation_version": "metric-aggregation/v1",
            "bootstrap_version": "paired-cluster-percentile/v1",
            "minimum_effect_pp": None,
            "calibration": {
                "min_samples": 30,
                "min_per_class": 5,
                "min_agreement": 0.8,
                "max_false_pass_rate": 0.05,
            },
        }
    )
    assert policy.fact_matching_version == "fact-matching/v1"
    assert policy.metric_aggregation_version == "metric-aggregation/v1"
    assert policy.bootstrap_version == "paired-cluster-percentile/v1"
    assert policy.minimum_effect_pp is None
    assert policy.calibration.min_agreement == 0.8
    assert policy.calibration.max_false_pass_rate == 0.05


def test_experiment_and_comparison_models_are_constructible() -> None:
    experiment = MemoryExperiment.model_validate(
        {
            "id": "33333333-3333-4333-8333-333333333333",
            "suite_id": "22222222-2222-4222-8222-222222222222",
            "dataset_revision_id": "44444444-4444-4444-8444-444444444444",
            "split": "regression",
            "track": "end_to_end",
            "candidates": [make_memory_candidate()],
            "repetitions": 3,
            "model_mode": "real",
            "judge_profile_id": None,
            "budgets": {"max_trials": 200, "per_trial_timeout_s": 120},
            "primary_metric": "qa_accuracy_scenario_macro",
            "policy": {
                "fact_matching_version": "fact-matching/v1",
                "metric_aggregation_version": "metric-aggregation/v1",
                "bootstrap_version": "paired-cluster-percentile/v1",
                "calibration": {
                    "min_samples": 30,
                    "min_per_class": 5,
                    "min_agreement": 0.8,
                    "max_false_pass_rate": 0.05,
                },
            },
            "status": "queued",
            "measurement_status": "incomplete",
            "planned_trials": 3,
        }
    )
    assert experiment.candidates[0].id == "cand-current"
    assert experiment.measurement_status == "incomplete"

    comparison = MemoryComparison.model_validate(
        {
            "id": "55555555-5555-4555-8555-555555555555",
            "baseline_experiment_id": "33333333-3333-4333-8333-333333333333",
            "candidate_experiment_id": "66666666-6666-4666-8666-666666666666",
            "baseline_candidate_id": "cand-baseline",
            "candidate_candidate_id": "cand-current",
            "changed_fields": ["/memory_settings/recall_limit"],
            "policy": {"minimum_effect_pp": 2.0},
            "status": "completed",
            "result": {"delta_pp": 3.5},
            "snapshot": {"scenarios": []},
        }
    )
    assert comparison.changed_fields == ["/memory_settings/recall_limit"]
    assert comparison.result["delta_pp"] == 3.5


def test_capabilities_declare_each_dimension_explicitly() -> None:
    capabilities = MemoryCapabilities.model_validate({})
    for name in (
        "extraction", "semantic_recall", "episodes", "working_memory", "profile",
        "delete", "rebuild", "restart", "clock_coverage", "scope_coverage",
    ):
        assert getattr(capabilities, name).status == "unsupported"


def test_dataset_payload_fixture_is_importable_shape() -> None:
    payload = make_memory_dataset_payload(suite_id="s1")
    assert payload["scenarios"][0]["schema_version"] == "memory-scenario/v1"
    assert payload["source"]["grading_protocol"] == "memory-grading/v1"


def test_probe_model_is_exported_for_the_adapter_protocol() -> None:
    probe = MemoryProbe.model_validate(
        {
            "id": "q1",
            "question": "我住在哪个城市？",
            "at": "2026-11-02T09:00:00+08:00",
            "fresh_session": True,
        }
    )
    assert probe.fresh_session is True
    assert probe.checkpoint_id is None
    assert probe.scope is None


# --------------------------------------------------------------------------- 协议


def test_adapter_protocol_declares_every_contract_signature() -> None:
    """契约 §3：adapter 的方法名与异步性是跨 WU 基线。"""
    expected = [
        "reset", "ingest_session", "settle", "snapshot", "restore", "restart",
        "recall", "answer", "forget", "rebuild", "close",
    ]
    for name in expected:
        assert hasattr(MemoryAdapter, name), name
        assert inspect.iscoroutinefunction(getattr(MemoryAdapter, name)), name


def test_adapter_protocol_declares_capabilities_attribute() -> None:
    assert "capabilities" in MemoryAdapter.__annotations__
