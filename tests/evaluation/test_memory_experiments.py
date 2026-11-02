"""WU-07：候选冻结、分批实验、预算与恢复（契约 §2 / §5 / §7）。

这里钉死的六条：

1. 120×3×3 生成 1080 planned trials / 9 个子 run，每批 40 场景×3 重复=120 trials；
2. 跨批总预算在入队前拒绝（默认上限 200，核心 1080 必须显式提高）；
3. 同一幂等 key 不重复建 run，进程重启只补计划里**缺的**子 run；
4. 取消后停止新批、回收在飞进程，剩余批次不会在 resume 时复活；
5. 子 run 共享实验组的并发闸（不是每个 run 各拿一份）；
6. 预算在实际请求前预留，失败/重试按全成本结算，未知不当 0；货币预算缺单价拒绝放行。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.memory import experiments as experiments_module
from nanobot.evaluation.memory import repository as memory_repository_module
from nanobot.evaluation.memory.experiments import (
    MemoryBudgetExceeded,
    MemoryBudgetGuard,
    MemoryBudgetUsage,
    MemoryExperimentCoordinator,
    MemoryUnitPrice,
    assert_group_trial_budget,
    build_batch_plan,
    measurement_status_of,
    plan_batches,
    validate_primary_metric,
)
from nanobot.evaluation.memory.models import (
    MemoryBudgets,
    MemoryCapabilities,
    MemoryCapability,
)
from nanobot.evaluation.memory.repository import MemoryRepository
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.scheduler import (
    AttemptOutcome,
    CalibrationItemOutcome,
    Scheduler,
    SchedulerConfig,
)
from nanobot.evaluation.schemas import RunCreate
from nanobot.evaluation.services import EvaluationService, Profile, ServiceError
from nanobot.memory.clock import MemoryClock

from .conftest import make_run_payload, make_suite_payload
from .memory_fixtures import (
    make_memory_candidate,
    make_memory_case_payload,
    make_memory_dataset_payload,
    make_memory_scenario,
)

SOURCE_STATE = {
    "source_revision": "ce595d6",
    "source_revision_reason": None,
    "source_dirty_digest": "d" * 64,
    "source_dirty_digest_reason": None,
}


# --------------------------------------------------------------------------- 夹具


@pytest.fixture
def memory_repository(evaluation_database: EvaluationDatabase) -> MemoryRepository:
    return MemoryRepository(evaluation_database)


@pytest.fixture
def suite_id(evaluation_repository: EvaluationRepository) -> str:
    return str(evaluation_repository.create_suite(make_suite_payload())["id"])


def _policy() -> dict[str, Any]:
    return {
        "fact_matching_version": "fact-matching/v1",
        "metric_aggregation_version": "metric-aggregation/v1",
        "bootstrap_version": "paired-cluster-percentile/v1",
    }


def _import(
    memory_repository: MemoryRepository, suite_id: str, *, count: int
) -> dict[str, Any]:
    scenarios = [
        make_memory_scenario(
            scenario_id=f"s{index:03d}",
            cluster_id=f"cluster-{index:03d}",
            template_family=f"family-{index:03d}",
        )
        for index in range(count)
    ]
    return memory_repository.import_revision(
        make_memory_dataset_payload(suite_id=suite_id, scenarios=scenarios)
    )["dataset_revision"]


def _capabilities() -> MemoryCapabilities:
    return MemoryCapabilities(
        extraction=MemoryCapability(status="supported"),
        semantic_recall=MemoryCapability(status="supported"),
        episodes=MemoryCapability(status="supported"),
        working_memory=MemoryCapability(status="supported"),
        profile=MemoryCapability(status="supported"),
        delete=MemoryCapability(status="supported"),
        rebuild=MemoryCapability(status="supported"),
        restart=MemoryCapability(status="supported"),
        clock_coverage=MemoryCapability(status="supported"),
        scope_coverage=MemoryCapability(status="supported"),
    )


def _coordinator(
    repository: EvaluationRepository,
    memory_repository: MemoryRepository,
    *,
    prices: MemoryUnitPrice | None = None,
    clock: MemoryClock | None = None,
) -> MemoryExperimentCoordinator:
    return MemoryExperimentCoordinator(
        repository=repository,
        memory_repository=memory_repository,
        clock=clock or MemoryClock(),
        capabilities=_capabilities(),
        prices=prices,
        source_state_provider=lambda: dict(SOURCE_STATE),
        profile_resolver=lambda profile_id: {
            "id": profile_id,
            "provider": "controlled",
            "model": "eval-model",
        },
    )


def _candidates(count: int = 1) -> list[dict[str, Any]]:
    return [
        make_memory_candidate(id=f"cand-{index}", name=f"候选 {index}")
        for index in range(count)
    ]


def _request(
    revision_id: str, suite_id: str, **overrides: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "suite_id": suite_id,
        "dataset_revision_id": revision_id,
        "split": "regression",
        "track": "end_to_end",
        "candidates": _candidates(),
        "repetitions": 1,
        "model_mode": "real",
        "judge_profile_id": None,
        "budgets": {"max_trials": 200},
        "primary_metric": "qa_accuracy_scenario_macro",
        "policy": _policy(),
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- 分批


def test_plan_batches_is_reexported_from_the_single_source() -> None:
    assert experiments_module.plan_batches is memory_repository_module.plan_batches
    assert "plan_batches" in experiments_module.__all__
    batches = plan_batches([f"s{index}" for index in range(120)], 3)
    assert [len(batch) for batch in batches] == [40, 40, 40]


def test_build_batch_plan_generates_1080_trials_in_nine_runs() -> None:
    plan = build_batch_plan(
        _candidates(3), [f"s{index:03d}" for index in range(120)], 3
    )
    assert len(plan) == 9
    assert {(item.candidate_id, item.batch_index) for item in plan} == {
        (f"cand-{candidate}", batch) for candidate in range(3) for batch in range(3)
    }
    assert sum(item.planned_trials for item in plan) == 1080
    assert all(item.planned_trials <= 200 for item in plan)
    assert plan[0].scenario_ids[0] == "s000"
    assert plan[0].scenario_ids[-1] == "s039"


def test_group_budget_rejects_1080_trials_at_the_default_cap() -> None:
    plan = build_batch_plan(
        _candidates(3), [f"s{index:03d}" for index in range(120)], 3
    )
    with pytest.raises(MemoryBudgetExceeded) as excinfo:
        assert_group_trial_budget(plan, MemoryBudgets())
    assert excinfo.value.code == "MEMORY_BUDGET_EXCEEDED"
    assert assert_group_trial_budget(plan, MemoryBudgets(max_trials=1080)) == 1080


def test_build_batch_plan_rejects_out_of_range_repetitions() -> None:
    with pytest.raises(ValueError, match="REPETITIONS"):
        build_batch_plan(_candidates(1), ["s000"], 0)


def test_primary_metric_must_belong_to_its_track() -> None:
    validate_primary_metric("end_to_end", "qa_accuracy_scenario_macro")
    validate_primary_metric("extraction", "extraction_recall")
    validate_primary_metric("retrieval", "evidence_recall_at_k")
    with pytest.raises(ValueError, match="MEMORY_SCHEMA_INVALID"):
        validate_primary_metric("extraction", "qa_accuracy_scenario_macro")


# --------------------------------------------------------------------------- 入队与冻结


def test_create_experiment_enqueues_nine_child_runs_with_frozen_manifest(
    evaluation_repository: EvaluationRepository,
    memory_repository: MemoryRepository,
    suite_id: str,
) -> None:
    revision = _import(memory_repository, suite_id, count=120)
    coordinator = _coordinator(evaluation_repository, memory_repository)
    plan = coordinator.create_experiment(
        _request(
            revision["id"],
            suite_id,
            candidates=_candidates(3),
            repetitions=3,
            budgets={"max_trials": 1080, "concurrency": 3},
        )
    )
    experiment = plan["experiment"]
    assert experiment["planned_trials"] == 1080
    assert experiment["status"] in ("queued", "running")
    assert experiment["measurement_status"] == "incomplete"
    child_runs = plan["child_runs"]
    assert len(child_runs) == 9
    for child in child_runs:
        run = evaluation_repository.get_run(child["run_id"])
        assert len(run["attempts"]) == len(child["scenario_ids"]) * 3
        assert len(run["attempts"]) <= 200
    assert experiment["manifest"]["version"] == 1
    assert len(experiment["manifest"]["dataset"]["scenarios"]) == 120
    assert experiment["manifest"]["grading"]["policy_digest"]
    assert experiment["manifest"]["source"]["source_revision"] == "ce595d6"


def test_same_plan_key_does_not_duplicate_runs(
    evaluation_repository: EvaluationRepository,
    memory_repository: MemoryRepository,
    suite_id: str,
) -> None:
    revision = _import(memory_repository, suite_id, count=4)
    coordinator = _coordinator(evaluation_repository, memory_repository)
    request = _request(revision["id"], suite_id, candidates=_candidates(2), plan_key="p1")
    first = coordinator.create_experiment(request)
    second = coordinator.create_experiment(request)
    assert second["experiment"]["id"] == first["experiment"]["id"]
    assert [child["run_id"] for child in second["child_runs"]] == [
        child["run_id"] for child in first["child_runs"]
    ]
    total_attempts = sum(
        len(evaluation_repository.get_run(child["run_id"])["attempts"])
        for child in second["child_runs"]
    )
    assert total_attempts == first["experiment"]["planned_trials"]


def test_restart_resume_only_fills_missing_child_runs(
    evaluation_repository: EvaluationRepository,
    memory_repository: MemoryRepository,
    suite_id: str,
) -> None:
    revision = _import(memory_repository, suite_id, count=4)
    coordinator = _coordinator(evaluation_repository, memory_repository)
    plan = coordinator.create_experiment(
        _request(revision["id"], suite_id, candidates=_candidates(2)), enqueue=False
    )
    experiment_id = plan["experiment"]["id"]
    assert all(
        evaluation_repository.run_status(child["run_id"]) is None
        for child in plan["child_runs"]
    )

    resumed = coordinator.resume(experiment_id)
    assert all(
        evaluation_repository.run_status(child["run_id"]) is not None
        for child in resumed["child_runs"]
    )
    run_ids = [child["run_id"] for child in resumed["child_runs"]]
    again = coordinator.resume(experiment_id)
    assert [child["run_id"] for child in again["child_runs"]] == run_ids
    total = sum(
        len(evaluation_repository.get_run(run_id)["attempts"]) for run_id in run_ids
    )
    assert total == plan["experiment"]["planned_trials"]


def test_coordinator_refuses_group_budget_before_freezing_the_plan(
    evaluation_repository: EvaluationRepository,
    memory_repository: MemoryRepository,
    suite_id: str,
) -> None:
    revision = _import(memory_repository, suite_id, count=120)
    coordinator = _coordinator(evaluation_repository, memory_repository)
    with pytest.raises(MemoryBudgetExceeded):
        coordinator.create_experiment(
            _request(revision["id"], suite_id, candidates=_candidates(3), repetitions=3)
        )
    # 拒绝发生在落库之前：没有留下半个实验计划。
    assert evaluation_repository.list_memory_experiments(suite_id=suite_id) == []


# --------------------------------------------------------------------------- 取消与并发


def test_resume_of_a_cancelling_experiment_settles_the_remaining_batches(
    evaluation_repository: EvaluationRepository,
    memory_repository: MemoryRepository,
    suite_id: str,
) -> None:
    """重启时撞上「取消中」：补建被禁止，剩余批次直接收口成 cancelled。"""
    revision = _import(memory_repository, suite_id, count=2)
    coordinator = _coordinator(evaluation_repository, memory_repository)
    plan = coordinator.create_experiment(
        _request(revision["id"], suite_id, candidates=_candidates(2)), enqueue=False
    )
    experiment_id = plan["experiment"]["id"]
    evaluation_repository.set_memory_experiment_status(experiment_id, "cancelling")

    resumed = coordinator.resume(experiment_id)
    assert resumed["experiment"]["status"] == "cancelled"
    assert resumed["experiment"]["measurement_status"] == "incomplete"
    assert all(
        evaluation_repository.run_status(child["run_id"]) is None
        for child in resumed["child_runs"]
    )


@dataclass
class _CountingPipeline:
    """记录并发峰值；``hold`` 让用例停在执行中，便于测取消。"""

    delay: float = 0.01
    hold: asyncio.Event | None = None
    live: int = 0
    peak: int = 0
    entered: list[str] = field(default_factory=list)

    async def execute(
        self, attempt: Mapping[str, Any], *, run: Mapping[str, Any]
    ) -> AttemptOutcome:
        self.entered.append(str(attempt["id"]))
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            if self.hold is not None:
                await self.hold.wait()
            await asyncio.sleep(self.delay)
            return AttemptOutcome.passed()
        finally:
            self.live -= 1

    async def calibrate(self, claimed: Mapping[str, Any]) -> CalibrationItemOutcome:
        raise AssertionError("记忆实验不产生校准项")

    def settle_calibration(self, job_id: str) -> None:
        return None


async def _wait_until(predicate: Any, message: str) -> None:
    deadline = asyncio.get_running_loop().time() + 30
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timeout waiting for {message}")


def _scheduler(
    repository: EvaluationRepository,
    pipeline: Any,
    tmp_path: Path,
    coordinator: MemoryExperimentCoordinator,
    *,
    concurrency: int = 3,
) -> Scheduler:
    return Scheduler(
        repository=repository,
        pipeline=pipeline,
        config=SchedulerConfig(
            data_dir=tmp_path / "service", concurrency=concurrency, poll_interval_s=0.01
        ),
        group_status_provider=coordinator.experiment_status,
        run_settled=coordinator.on_run_settled,
    )


async def test_cancel_stops_new_batches_and_reclaims_processes(
    evaluation_repository: EvaluationRepository,
    memory_repository: MemoryRepository,
    suite_id: str,
    tmp_path: Path,
) -> None:
    revision = _import(memory_repository, suite_id, count=2)
    coordinator = _coordinator(evaluation_repository, memory_repository)
    pipeline = _CountingPipeline(hold=asyncio.Event(), delay=0)
    scheduler = _scheduler(evaluation_repository, pipeline, tmp_path, coordinator)
    plan = coordinator.create_experiment(
        _request(
            revision["id"],
            suite_id,
            candidates=_candidates(2),
            budgets={"max_trials": 200, "concurrency": 1},
        )
    )
    experiment_id = plan["experiment"]["id"]

    async with scheduler:
        await _wait_until(lambda: pipeline.live == 1, "first attempt started")
        cancelled = await coordinator.cancel(experiment_id, scheduler=scheduler)
        assert pipeline.live == 0
        assert scheduler.active_task_count == 0

    assert cancelled["experiment"]["status"] == "cancelled"
    assert cancelled["experiment"]["measurement_status"] == "incomplete"
    for child in cancelled["child_runs"]:
        run = evaluation_repository.get_run(child["run_id"])
        assert run["status"] == "cancelled"
        assert {item["execution_status"] for item in run["attempts"]} == {"cancelled"}

    # 取消后 resume 不得复活剩余批次。
    resumed = coordinator.resume(experiment_id)
    assert resumed["experiment"]["status"] == "cancelled"
    assert [child["run_id"] for child in resumed["child_runs"]] == [
        child["run_id"] for child in cancelled["child_runs"]
    ]
    assert all(
        evaluation_repository.run_status(child["run_id"]) == "cancelled"
        for child in resumed["child_runs"]
    )


async def test_child_runs_share_the_group_concurrency_gate(
    evaluation_repository: EvaluationRepository,
    memory_repository: MemoryRepository,
    suite_id: str,
    tmp_path: Path,
) -> None:
    """每个子 run 自带 limit=3，但实验组只允许 1 个在飞。"""
    revision = _import(memory_repository, suite_id, count=3)
    cases = [
        evaluation_repository.save_case(
            make_memory_case_payload(
                suite_id=suite_id,
                dataset_revision_id=revision["id"],
                memory_scenario_ref={
                    "dataset_revision_id": revision["id"],
                    "scenario_id": f"s{index:03d}",
                    "revision": 1,
                },
            )
        )
        for index in range(3)
    ]
    group = {"experiment_id": "exp-gate", "concurrency": 1}
    runs = [
        evaluation_repository.create_run(
            scope="local-user",
            idempotency_key=f"gate-{index}",
            request=RunCreate.model_validate(
                make_run_payload(
                    [case["id"] for case in cases],
                    suite_id=suite_id,
                    limits={"concurrency": 3, "max_iterations": 12, "timeout_s": 120},
                )
            ),
            agent={"id": "agent-profile", "provider": "controlled", "model": "eval-model"},
            judge=None,
            rules_version="rules-v1",
            grader_version="judge-v1",
            snapshot_extra={"memory_experiment": group},
        )
        for index in range(2)
    ]

    pipeline = _CountingPipeline(delay=0.02)
    scheduler = Scheduler(
        repository=evaluation_repository,
        pipeline=pipeline,
        config=SchedulerConfig(
            data_dir=tmp_path / "service", concurrency=3, poll_interval_s=0.01
        ),
    )
    async with scheduler:
        await _wait_until(
            lambda: all(
                evaluation_repository.run_status(run["id"]) == "completed"
                for run in runs
            ),
            "all child runs completed",
        )
    assert pipeline.peak == 1
    assert len(pipeline.entered) == 6


async def test_batches_complete_with_planned_attempts_and_stay_incomplete(
    evaluation_repository: EvaluationRepository,
    memory_repository: MemoryRepository,
    suite_id: str,
    tmp_path: Path,
) -> None:
    """45 场景×1 重复 → 40+5 两批；伪流水线无记忆指标 → measurement 仍不完整。"""
    revision = _import(memory_repository, suite_id, count=45)
    coordinator = _coordinator(evaluation_repository, memory_repository)
    plan = coordinator.create_experiment(_request(revision["id"], suite_id))
    assert [len(child["scenario_ids"]) for child in plan["child_runs"]] == [40, 5]

    pipeline = _CountingPipeline(delay=0)
    scheduler = _scheduler(evaluation_repository, pipeline, tmp_path, coordinator)
    async with scheduler:
        await _wait_until(
            lambda: coordinator.experiment_status(plan["experiment"]["id"]) == "completed",
            "experiment completed",
        )
    detail = memory_repository.get_experiment(plan["experiment"]["id"])
    assert detail["experiment"]["measurement_status"] == "incomplete"
    assert len(pipeline.entered) == 45


# --------------------------------------------------------------------------- 预算


def test_budget_guard_reserves_before_requests_and_charges_failures_in_full() -> None:
    guard = MemoryBudgetGuard(MemoryBudgets(max_model_requests=2, max_trials=2))
    first = guard.reserve(key="a1", model_requests=1, input_tokens=None, output_tokens=None)
    guard.charge_failed(first)
    assert guard.spent.model_requests == 1
    assert guard.spent.trials == 1

    second = guard.reserve(key="a2", model_requests=1, input_tokens=None, output_tokens=None)
    guard.settle(second)  # 实际用量未知 → 按预留全成本结算，不当 0
    assert guard.spent.model_requests == 2

    with pytest.raises(MemoryBudgetExceeded):
        guard.reserve(key="a3", model_requests=1, input_tokens=None, output_tokens=None)


def test_budget_guard_refuses_unknown_token_counts_instead_of_counting_zero() -> None:
    guard = MemoryBudgetGuard(MemoryBudgets(max_input_tokens=1000))
    with pytest.raises(MemoryBudgetExceeded) as excinfo:
        guard.reserve(key="a1", model_requests=1, input_tokens=None, output_tokens=None)
    assert "MEMORY_TOKEN_ESTIMATE_REQUIRED" in str(excinfo.value)
    reservation = guard.reserve(
        key="a2", model_requests=1, input_tokens=600, output_tokens=None
    )
    assert guard.spent.input_tokens == 0
    guard.release(reservation)
    assert guard.spent.input_tokens == 0


def test_currency_budget_without_unit_price_refuses_and_priced_budget_settles() -> None:
    budget = MemoryBudgets(max_total_cost=0.5)
    with pytest.raises(MemoryBudgetExceeded) as excinfo:
        MemoryBudgetGuard(budget).reserve(
            key="x", model_requests=1, input_tokens=1000, output_tokens=100
        )
    assert "UNIT_PRICE_REQUIRED" in str(excinfo.value)

    prices = MemoryUnitPrice(
        currency="USD", input_per_million=1.0, output_per_million=2.0, request_price=0.0
    )
    guard = MemoryBudgetGuard(budget, prices=prices)
    reservation = guard.reserve(
        key="x", model_requests=1, input_tokens=1000, output_tokens=100
    )
    guard.settle(
        reservation,
        MemoryBudgetUsage(
            trials=1, model_requests=1, input_tokens=1000, output_tokens=100
        ),
    )
    assert guard.spent.total_cost == pytest.approx(0.0012)
    assert guard.remaining()["max_total_cost"] == pytest.approx(0.4988)


async def test_exhausted_group_budget_blocks_the_attempt_before_any_request(
    evaluation_repository: EvaluationRepository,
    memory_repository: MemoryRepository,
    suite_id: str,
    tmp_path: Path,
) -> None:
    revision = _import(memory_repository, suite_id, count=1)
    coordinator = _coordinator(evaluation_repository, memory_repository)
    plan = coordinator.create_experiment(
        _request(
            revision["id"],
            suite_id,
            budgets={"max_trials": 10, "max_model_requests": 1},
        )
    )
    child = plan["child_runs"][0]
    service = EvaluationService(
        repository=evaluation_repository,
        data_dir=tmp_path,
        profiles=(
            Profile(
                id="agent-profile",
                label="受控执行器",
                provider="controlled",
                model="eval-model",
                kind="agent",
            ),
        ),
    )
    guard = service.memory_budget_guard(
        plan["experiment"]["id"], {"max_model_requests": 1}
    )
    guard.reserve(key="pre", model_requests=1, input_tokens=None, output_tokens=None)

    attempt = evaluation_repository.list_run_attempts(
        child["run_id"], include_snapshot=True
    )[0]
    run = evaluation_repository.get_run(child["run_id"])
    with pytest.raises(ServiceError) as excinfo:
        await service.execute(attempt, run=run)
    assert excinfo.value.code == "MEMORY_BUDGET_EXCEEDED"
    assert evaluation_repository.run_status(child["run_id"]) == "queued"


# --------------------------------------------------------------------------- 可评分性


def _graded_attempt(
    repository: EvaluationRepository,
    attempt_id: str,
    *,
    metrics: Sequence[Mapping[str, Any]],
) -> None:
    repository.finish_attempt(
        attempt_id,
        result={
            "execution_status": "succeeded",
            "grading_status": "completed",
            "verdict": "passed",
            "result": {
                "memory": {
                    "scenario_id": "s000",
                    "revision": 1,
                    "track": "end_to_end",
                    "metrics": {"metrics": [dict(item) for item in metrics]},
                }
            },
        },
    )


def test_measurement_status_treats_unknown_metrics_as_incomplete() -> None:
    attempt = {
        "execution_status": "succeeded",
        "grading_status": "completed",
        "case_snapshot": {
            "memory_required_metrics": ["qa_accuracy", "grounded_memory_success"]
        },
        "result": {
            "memory": {
                "metrics": {
                    "metrics": [
                        {"id": "qa_accuracy", "status": "scored", "value": 1.0},
                        {
                            "id": "grounded_memory_success",
                            "status": "not_assessable",
                            "value": None,
                        },
                    ]
                }
            }
        },
    }
    assert measurement_status_of([attempt], planned_trials=1) == "incomplete"
    assert measurement_status_of([], planned_trials=1) == "incomplete"
    assert measurement_status_of([attempt], planned_trials=2) == "incomplete"
    attempt["result"]["memory"]["metrics"]["metrics"][1]["status"] = "scored"
    attempt["result"]["memory"]["metrics"]["metrics"][1]["value"] = 1.0
    assert measurement_status_of([attempt], planned_trials=1) == "complete"


def test_refresh_marks_complete_only_after_every_trial_is_scorable(
    evaluation_repository: EvaluationRepository,
    memory_repository: MemoryRepository,
    suite_id: str,
) -> None:
    revision = _import(memory_repository, suite_id, count=2)
    coordinator = _coordinator(evaluation_repository, memory_repository)
    plan = coordinator.create_experiment(_request(revision["id"], suite_id))
    child = plan["child_runs"][0]
    attempts = evaluation_repository.list_run_attempts(child["run_id"])
    assert len(attempts) == 2

    scored = [
        {"id": "qa_accuracy", "status": "scored", "value": 1.0},
        {"id": "grounded_memory_success", "status": "scored", "value": 1.0},
    ]
    _graded_attempt(evaluation_repository, attempts[0]["id"], metrics=scored)
    partial = coordinator.refresh(plan["experiment"]["id"])
    assert partial["experiment"]["measurement_status"] == "incomplete"

    _graded_attempt(evaluation_repository, attempts[1]["id"], metrics=scored)
    complete = coordinator.refresh(plan["experiment"]["id"])
    assert complete["experiment"]["status"] == "completed"
    assert complete["experiment"]["measurement_status"] == "complete"


def test_trial_identity_is_stable_across_retries() -> None:
    """trial 身份 = (experiment, candidate, scenario, revision, trial_index)。"""
    plan = build_batch_plan(_candidates(2), ["s000", "s001"], 2)
    identities = {
        (item.candidate_id, scenario_id, trial_index)
        for item in plan
        for scenario_id in item.scenario_ids
        for trial_index in range(item.repetitions)
    }
    assert len(identities) == 2 * 2 * 2
    assert sum(1 for item in plan for _ in item.scenario_ids) == 4
