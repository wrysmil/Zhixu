"""记忆专项仓储层：版本化导入、gold 隔离、执行载荷、实验计划与比较快照。

契约 §1（版本与 ref）、§7（存储与迁移）、§8.1（输入输出闭环）在这里落地成可执行
不变量：gold 不进执行载荷、导入幂等、跨 split 不复用模板、批次按公式切分、
比较只读快照不重算当前 case。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.memory.repository import MemoryRepository
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.schemas import RunCreate

from .conftest import make_case_payload, make_run_payload, make_suite_payload
from .memory_fixtures import (
    make_memory_candidate,
    make_memory_case_payload,
    make_memory_dataset_payload,
    make_memory_review_ref,
    make_memory_scenario,
)


@pytest.fixture
def memory_repository(evaluation_database: EvaluationDatabase) -> MemoryRepository:
    return MemoryRepository(evaluation_database)


@pytest.fixture
def suite_id(evaluation_repository: EvaluationRepository) -> str:
    return str(evaluation_repository.create_suite(make_suite_payload())["id"])


def _import(memory_repository: MemoryRepository, suite_id: str, **overrides: Any) -> dict[str, Any]:
    payload = make_memory_dataset_payload(suite_id=suite_id, **overrides)
    return memory_repository.import_revision(payload)


# --------------------------------------------------------------------------- 导入


def test_import_revision_publishes_ready_revision(
    memory_repository: MemoryRepository, suite_id: str
) -> None:
    result = _import(memory_repository, suite_id)
    revision = result["dataset_revision"]
    assert revision["status"] == "ready"
    assert revision["dataset_version"] == "memory-core-v1"
    assert revision["split_counts"] == {"regression": 1}
    assert len(revision["definition_digest"]) == 64
    assert result["scenario_count"] == 1


def test_import_revision_is_idempotent_for_the_same_digest(
    memory_repository: MemoryRepository, suite_id: str
) -> None:
    first = _import(memory_repository, suite_id)
    second = _import(memory_repository, suite_id)
    assert second["dataset_revision"]["id"] == first["dataset_revision"]["id"]
    assert second["scenario_count"] == first["scenario_count"]


def test_import_revision_rejects_same_version_with_different_digest(
    memory_repository: MemoryRepository, suite_id: str
) -> None:
    _import(memory_repository, suite_id)
    changed = make_memory_scenario(scenario_id="fact-002")
    with pytest.raises(ValueError, match="REVISION_CONFLICT"):
        _import(memory_repository, suite_id, scenarios=[changed])


def test_import_revision_rejects_template_family_reused_across_splits(
    memory_repository: MemoryRepository, suite_id: str
) -> None:
    """契约 §1：跨 split 复用 template_family 一律拒绝。"""
    scenarios = [
        make_memory_scenario(scenario_id="fact-001", split="regression"),
        make_memory_scenario(scenario_id="fact-002", split="calibration"),
    ]
    with pytest.raises(ValueError, match="TEMPLATE_FAMILY_CROSS_SPLIT"):
        _import(memory_repository, suite_id, scenarios=scenarios)


def test_import_revision_rejects_invalid_scenario_without_publishing(
    memory_repository: MemoryRepository, suite_id: str
) -> None:
    broken = make_memory_scenario()
    broken["events"][0]["messages"][0]["role"] = "system"
    with pytest.raises(ValueError):
        _import(memory_repository, suite_id, scenarios=[broken])
    listed = memory_repository.list_dataset_revisions(suite_id)
    assert listed == []


# --------------------------------------------------------------------------- 读出


def test_get_scenario_returns_the_full_scenario(
    memory_repository: MemoryRepository, suite_id: str
) -> None:
    revision = _import(memory_repository, suite_id)["dataset_revision"]
    scenario = memory_repository.get_scenario(revision["id"], "fact-001")
    assert scenario["scenario_id"] == "fact-001"
    assert scenario["gold"]["probes"]["q1"]["answerability"] == "answerable"


def test_get_scenario_raises_for_unknown_scenario(
    memory_repository: MemoryRepository, suite_id: str
) -> None:
    revision = _import(memory_repository, suite_id)["dataset_revision"]
    with pytest.raises(LookupError, match="MEMORY_SCENARIO_NOT_FOUND"):
        memory_repository.get_scenario(revision["id"], "fact-404")


def test_get_execution_input_strips_gold_and_source(
    memory_repository: MemoryRepository, suite_id: str
) -> None:
    revision = _import(memory_repository, suite_id)["dataset_revision"]
    execution = memory_repository.get_execution_input(
        revision["id"], "fact-001", track="end_to_end",
        candidates=[make_memory_candidate()],
    )
    assert "gold" not in execution
    for leaked in ("required_facts", "answerability", "annotation_scope",
                   "evidence_message_ids"):
        assert leaked not in str(execution)
    assert execution["track"] == "end_to_end"
    assert execution["scope"]["subject_id"] == "synthetic-u1"
    assert execution["candidates"][0]["id"] == "cand-current"


def test_get_gold_returns_the_private_gold(
    memory_repository: MemoryRepository, suite_id: str
) -> None:
    revision = _import(memory_repository, suite_id)["dataset_revision"]
    gold = memory_repository.get_gold(revision["id"], "fact-001")
    assert gold["annotation_scope"] == "targeted"
    assert gold["probes"]["q1"]["required_facts"][0]["value"] == "青岚城"


# --------------------------------------------------------------------------- 实验


def _plan_payload(revision_id: str, suite: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "suite_id": suite,
        "dataset_revision_id": revision_id,
        "split": "regression",
        "track": "end_to_end",
        "candidates": [make_memory_candidate()],
        "repetitions": 1,
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
    }
    payload.update(overrides)
    return payload


def test_create_experiment_plan_freezes_experiment_and_child_runs(
    memory_repository: MemoryRepository, suite_id: str
) -> None:
    revision = _import(memory_repository, suite_id)["dataset_revision"]
    plan = memory_repository.create_experiment_plan(
        _plan_payload(revision["id"], suite_id)
    )
    experiment = plan["experiment"]
    assert experiment["status"] == "queued"
    assert experiment["measurement_status"] == "incomplete"
    assert experiment["planned_trials"] == 1
    assert len(plan["child_runs"]) == 1
    assert plan["child_runs"][0]["scenario_ids"] == ["fact-001"]
    assert plan["child_runs"][0]["batch_index"] == 0


def test_create_experiment_plan_splits_batches_by_the_contract_formula(
    memory_repository: MemoryRepository, suite_id: str
) -> None:
    scenarios = [
        make_memory_scenario(scenario_id=f"fact-{index:03d}") for index in range(45)
    ]
    revision = _import(memory_repository, suite_id, scenarios=scenarios)["dataset_revision"]
    plan = memory_repository.create_experiment_plan(
        _plan_payload(revision["id"], suite_id, repetitions=1)
    )
    batches = [run["scenario_ids"] for run in plan["child_runs"]]
    assert [len(batch) for batch in batches] == [40, 5]
    assert plan["experiment"]["planned_trials"] == 45


def test_create_experiment_plan_is_idempotent_per_plan_key(
    memory_repository: MemoryRepository, suite_id: str
) -> None:
    revision = _import(memory_repository, suite_id)["dataset_revision"]
    payload = _plan_payload(revision["id"], suite_id)
    payload["plan_key"] = "plan-001"
    first = memory_repository.create_experiment_plan(payload)
    second = memory_repository.create_experiment_plan(payload)
    assert second["experiment"]["id"] == first["experiment"]["id"]
    assert len(second["child_runs"]) == len(first["child_runs"])


def test_get_experiment_returns_frozen_plan(
    memory_repository: MemoryRepository, suite_id: str
) -> None:
    revision = _import(memory_repository, suite_id)["dataset_revision"]
    plan = memory_repository.create_experiment_plan(
        _plan_payload(revision["id"], suite_id)
    )
    experiment = memory_repository.get_experiment(plan["experiment"]["id"])
    assert experiment["experiment"]["id"] == plan["experiment"]["id"]
    assert len(experiment["child_runs"]) == 1


# --------------------------------------------------------------------------- 比较


def test_save_comparison_stores_result_and_reference_snapshot(
    memory_repository: MemoryRepository, suite_id: str
) -> None:
    revision = _import(memory_repository, suite_id)["dataset_revision"]
    plan = memory_repository.create_experiment_plan(
        _plan_payload(revision["id"], suite_id)
    )
    comparison = memory_repository.save_comparison(
        {
            "baseline_experiment_id": plan["experiment"]["id"],
            "candidate_experiment_id": plan["experiment"]["id"],
            "baseline_candidate_id": "cand-current",
            "candidate_candidate_id": "cand-current",
            "changed_fields": ["/memory_settings/recall_limit"],
            "policy": {"minimum_effect_pp": 2.0},
            "result": {"delta_pp": 1.25, "pairs": [{"scenario_id": "fact-001"}]},
            "snapshot": {"scenarios": ["fact-001"], "grading_revision": 3},
        }
    )
    stored = memory_repository.get_comparison(comparison["id"])
    assert stored["result"]["delta_pp"] == 1.25
    assert stored["snapshot"]["grading_revision"] == 3
    assert stored["status"] == "completed"


def test_comparison_read_does_not_recompute_from_current_scenario(
    memory_repository: MemoryRepository, suite_id: str
) -> None:
    """契约 §7：比较读快照，不读当前可变 case 重算历史报告。"""
    revision = _import(memory_repository, suite_id)["dataset_revision"]
    plan = memory_repository.create_experiment_plan(
        _plan_payload(revision["id"], suite_id)
    )
    comparison = memory_repository.save_comparison(
        {
            "baseline_experiment_id": plan["experiment"]["id"],
            "candidate_experiment_id": plan["experiment"]["id"],
            "baseline_candidate_id": "cand-current",
            "candidate_candidate_id": "cand-current",
            "changed_fields": [],
            "policy": {},
            "result": {"delta_pp": 4.0},
            "snapshot": {"scenarios": ["fact-001"]},
        }
    )
    memory_repository.save_scenario(
        revision["id"], "fact-001", make_memory_scenario(revision=2)
    )
    stored = memory_repository.get_comparison(comparison["id"])
    assert stored["result"]["delta_pp"] == 4.0
    assert stored["snapshot"]["scenarios"] == ["fact-001"]


# --------------------------------------------------------------------------- 并发


def test_concurrent_import_of_the_same_revision_creates_one_row(
    tmp_path: Path, suite_id: str
) -> None:
    """同一 dataset_version 并发导入不得产生重复修订行。"""
    path = tmp_path / "evaluation.db"
    seed = EvaluationDatabase(path)
    seed.init_schema()
    EvaluationRepository(seed).create_suite(make_suite_payload())
    seed.close()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    ids: list[str] = []

    def _worker() -> None:
        database = EvaluationDatabase(path)
        repository = MemoryRepository(database)
        try:
            barrier.wait(timeout=10)
            result = repository.import_revision(
                make_memory_dataset_payload(suite_id=suite_id)
            )
            ids.append(result["dataset_revision"]["id"])
        except BaseException as exc:  # noqa: BLE001 - 测试要看到真实异常
            errors.append(exc)
        finally:
            database.close()

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(set(ids)) == 1
    check = EvaluationDatabase(path)
    try:
        with check.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM memory_dataset_revisions"
            ).fetchone()[0]
    finally:
        check.close()
    assert count == 1


# --------------------------------------------------------------- 专项 case 与复核


def test_memory_case_requires_a_resolvable_scenario_ref(
    evaluation_repository: EvaluationRepository,
    memory_repository: MemoryRepository,
    suite_id: str,
) -> None:
    revision = _import(memory_repository, suite_id)["dataset_revision"]
    payload = make_memory_case_payload(
        suite_id=suite_id, dataset_revision_id=revision["id"]
    )
    saved = evaluation_repository.save_case(payload)
    assert saved["memory_scenario_ref"]["scenario_id"] == "fact-001"
    assert saved["turns"] == []
    assert saved["assertions"] == []


def test_memory_case_rejects_unresolvable_ref(
    evaluation_repository: EvaluationRepository, suite_id: str
) -> None:
    payload = make_memory_case_payload(
        suite_id=suite_id, dataset_revision_id="44444444-4444-4444-8444-444444444444"
    )
    with pytest.raises(LookupError, match="MEMORY_SCENARIO_NOT_FOUND"):
        evaluation_repository.save_case(payload)


def test_memory_case_rejects_mixing_with_ordinary_case_content(
    evaluation_repository: EvaluationRepository,
    memory_repository: MemoryRepository,
    suite_id: str,
) -> None:
    revision = _import(memory_repository, suite_id)["dataset_revision"]
    payload = make_memory_case_payload(
        suite_id=suite_id,
        dataset_revision_id=revision["id"],
        turns=[{"content": "普通 case 的轮次"}],
    )
    with pytest.raises(ValueError, match="MEMORY_CASE_MIXED_CONTENT"):
        evaluation_repository.save_case(payload)


def test_memory_case_rejects_memory_seed_and_script(
    evaluation_repository: EvaluationRepository,
    memory_repository: MemoryRepository,
    suite_id: str,
) -> None:
    revision = _import(memory_repository, suite_id)["dataset_revision"]
    seeded = make_memory_case_payload(
        suite_id=suite_id,
        dataset_revision_id=revision["id"],
        memory_seed=[{"kind": "semantic", "content": "伪造成记忆"}],
    )
    with pytest.raises(ValueError, match="MEMORY_SEED_WITH_SCENARIO_REF"):
        evaluation_repository.save_case(seeded)
    scripted = make_memory_case_payload(
        suite_id=suite_id,
        dataset_revision_id=revision["id"],
        script=[{"text": "脚本回复"}],
    )
    with pytest.raises(ValueError, match="MEMORY_SCRIPT_WITH_SCENARIO_REF"):
        evaluation_repository.save_case(scripted)


def test_ordinary_case_still_requires_rule_and_turns(
    evaluation_repository: EvaluationRepository, suite_id: str
) -> None:
    """专项能力不能削弱普通 case 的旧校验语义。"""
    payload = make_case_payload(suite_id=suite_id)
    payload["assertions"] = []
    with pytest.raises(ValueError):
        evaluation_repository.save_case(payload)
    payload["assertions"] = [
        {"id": "a1", "kind": "answer_contains", "required": False,
         "text": "alpha", "case_sensitive": True}
    ]
    payload["rubric"] = None
    with pytest.raises(ValueError, match="REQUIRED_RULE_OR_RUBRIC"):
        evaluation_repository.save_case(payload)


def _seed_attempt(evaluation_repository: EvaluationRepository, suite_id: str) -> str:
    case = evaluation_repository.save_case(make_case_payload(suite_id=suite_id))
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite_id)
    )
    evaluation_repository.create_run(
        scope="user-1",
        idempotency_key="memory-review-key",
        request=request,
        agent={"id": "agent-profile", "provider": "openai", "model": "gpt-test"},
        judge=None,
        rules_version="rules-v1",
        grader_version="judge-v1",
    )
    claimed = evaluation_repository.claim_attempt()
    assert claimed is not None
    return str(claimed["id"])


def test_append_review_keeps_old_call_signature(
    evaluation_repository: EvaluationRepository, suite_id: str
) -> None:
    attempt_id = _seed_attempt(evaluation_repository, suite_id)
    review = evaluation_repository.append_review(
        attempt_id, "passed", "整体通过", reviewer="local-user"
    )
    assert review["memory_ref"] is None
    assert review["verdict"] == "passed"


def test_probe_review_does_not_count_as_whole_attempt_verdict(
    evaluation_repository: EvaluationRepository, suite_id: str
) -> None:
    """契约 §4：单 probe 通过不能当整任务通过。"""
    attempt_id = _seed_attempt(evaluation_repository, suite_id)
    evaluation_repository.append_review(
        attempt_id, "passed", "该 probe 通过",
        reviewer="local-user", memory_ref=make_memory_review_ref(),
    )
    assert evaluation_repository.overall_review_verdict(attempt_id) is None
    reviews = evaluation_repository.list_reviews(attempt_id)
    assert reviews[0]["memory_ref"]["probe_id"] == "q1"


def test_overall_review_verdict_reads_only_whole_attempt_reviews(
    evaluation_repository: EvaluationRepository, suite_id: str
) -> None:
    attempt_id = _seed_attempt(evaluation_repository, suite_id)
    evaluation_repository.append_review(
        attempt_id, "passed", "该 probe 通过",
        reviewer="local-user", memory_ref=make_memory_review_ref(),
    )
    evaluation_repository.append_review(
        attempt_id, "failed", "整体复核不通过", reviewer="local-user"
    )
    assert evaluation_repository.overall_review_verdict(attempt_id) == "failed"
