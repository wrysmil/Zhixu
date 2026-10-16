"""T09 调度器：领取、并发预算、取消与重启恢复。

调度器是**唯一的结果提交者**（plan T09）。这里用一个假流水线替身注入
``AttemptPipeline`` 协议：调度器的职责只有领取、并发控制、取消与恢复，真实执行
在 services 里。分开的好处是这些用例不必拉起子进程就能把并发语义钉死。

锁死的几条最容易悄悄失效的规则：

1. 领取不重复——两个调度器同时抢同一个 attempt，只能有一个拿到。
2. 服务级并发总上限 3——两个 run 各自的 limit 都不能突破这个总闸。
3. Judge 与 Agent 共用同一个进程信号量，且**切换阶段先释放再申请**；
   否则 concurrency=1 的运行在等 Judge 槽位时会死锁。
4. cancelling 必须先等 owner 回收完进程再落 cancelled，不能先改状态后清理。
5. 重启对残留运行标 interrupted，不自动补跑；旧 PID 身份不可验证时
   只记录人工处置提示，绝不按进程名杀。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.scheduler import (
    AttemptOutcome,
    CalibrationItemOutcome,
    RecoveryNotice,
    Scheduler,
    SchedulerAlreadyRunningError,
    SchedulerConfig,
)
from nanobot.evaluation.schemas import RunCreate

from .conftest import make_case_payload, make_run_payload, make_suite_payload

# --------------------------------------------------------------------- 假流水线


@dataclass
class _FakePipeline:
    """按 case 名决定行为；``gate`` 让用例停在某个阶段，便于测取消。"""

    hold: dict[str, asyncio.Event] = field(default_factory=dict)
    entered: list[str] = field(default_factory=list)
    live: int = 0
    peak_live: int = 0
    judge_calls: list[str] = field(default_factory=list)
    judge_gate: dict[str, asyncio.Event] = field(default_factory=dict)
    outcome_override: dict[str, AttemptOutcome] = field(default_factory=dict)
    calibration_hold: asyncio.Event | None = None
    calibration_hold: asyncio.Event | None = None
    calibration_entered: list[str] = field(default_factory=list)
    settled_jobs: list[str] = field(default_factory=list)

    async def execute(
        self, attempt: Mapping[str, Any], *, run: Mapping[str, Any]
    ) -> AttemptOutcome:
        # 领取带回的是冻结快照，用例名在快照里（运行中途改名不影响历史报告）。
        name = str(attempt["case_snapshot"]["name"])
        self.entered.append(name)
        self.live += 1
        self.peak_live = max(self.peak_live, self.live)
        try:
            gate = self.hold.get(name)
            if gate is not None:
                await gate.wait()
            jgate = self.judge_gate.get(name)
            if jgate is not None:
                await jgate.wait()
            if name in self.judge_calls:
                self.judge_calls.remove(name)
            return self.outcome_override.get(name, AttemptOutcome.passed())
        finally:
            self.live -= 1

    async def calibrate(self, claimed: Mapping[str, Any]) -> CalibrationItemOutcome:
        """校准项替身：和 attempt 一样占一个槽位，用来验证两者共用总闸。"""
        self.calibration_entered.append(str(claimed["id"]))
        if self.calibration_hold is not None:
            await self.calibration_hold.wait()
        return CalibrationItemOutcome(status="completed", result={"matched": True})

    def settle_calibration(self, job_id: str) -> None:
        self.settled_jobs.append(job_id)

    def blocked(self, name: str) -> asyncio.Event:
        gate = asyncio.Event()
        self.hold[name] = gate
        return gate

    def judge_blocked(self, name: str) -> asyncio.Event:
        gate = asyncio.Event()
        self.judge_gate[name] = gate
        self.judge_calls.append(name)
        return gate


async def _wait_until(predicate, *, timeout: float = 5.0, message: str = "") -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timeout waiting for {message or predicate!r}")


# --------------------------------------------------------------------- 夹具


@pytest.fixture
def repository(evaluation_repository: EvaluationRepository) -> EvaluationRepository:
    return evaluation_repository


def _seed_run(
    repository: EvaluationRepository,
    *,
    case_count: int = 1,
    concurrency: int = 1,
    key: str = "sched-1",
) -> dict[str, Any]:
    suite = repository.create_suite(make_suite_payload())
    cases = [
        repository.save_case(make_case_payload(suite_id=suite["id"], name=f"case-{i}"))
        for i in range(case_count)
    ]
    request = RunCreate.model_validate(
        make_run_payload(
            [case["id"] for case in cases],
            suite_id=suite["id"],
            limits={
                "concurrency": concurrency,
                "max_iterations": 12,
                "timeout_s": 120,
            },
        )
    )
    return repository.create_run(
        scope="local-user",
        idempotency_key=key,
        request=request,
        agent={"provider": "controlled", "model": "eval-model"},
        judge=None,
        rules_version="rules-v1",
        grader_version="judge-v1",
    )


def _make_scheduler(
    repository: EvaluationRepository,
    pipeline: _FakePipeline,
    tmp_path: Path,
    *,
    concurrency: int = 3,
) -> Scheduler:
    config = SchedulerConfig(
        data_dir=tmp_path / "service",
        concurrency=concurrency,
        poll_interval_s=0.01,
    )
    return Scheduler(repository=repository, pipeline=pipeline, config=config)


# --------------------------------------------------------------------- 领取


async def test_scheduler_runs_queued_attempts_to_completion(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    pipeline = _FakePipeline()
    run = _seed_run(repository, case_count=2)
    scheduler = _make_scheduler(repository, pipeline, tmp_path)

    async with scheduler:
        await _wait_until(
            lambda: repository.get_run(run["id"])["status"] == "completed",
            message="run completed",
        )

    detail = repository.get_run(run["id"])
    assert [item["execution_status"] for item in detail["attempts"]] == ["succeeded"] * 2
    assert all(item["verdict"] == "passed" for item in detail["attempts"])
    assert sorted(pipeline.entered) == ["case-0", "case-1"]


async def test_two_schedulers_never_claim_the_same_attempt(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    """服务独占锁挡住第二个实例；即便绕过锁，领取本身也必须是原子的。"""
    pipeline = _FakePipeline()
    run = _seed_run(repository, case_count=4)
    first = _make_scheduler(repository, pipeline, tmp_path, concurrency=3)
    second = Scheduler(
        repository=repository,
        pipeline=pipeline,
        config=SchedulerConfig(
            data_dir=tmp_path / "service", concurrency=3, poll_interval_s=0.01
        ),
    )

    async with first:
        with pytest.raises(SchedulerAlreadyRunningError):
            await second.start()

    async with first:
        await _wait_until(
            lambda: repository.get_run(run["id"])["status"] == "completed",
            message="run completed",
        )
        attempts = repository.get_run(run["id"])["attempts"]
        assert sorted(pipeline.entered) == sorted(
            f"case-{i}" for i in range(4)
        )
        assert len(attempts) == 4


async def test_service_wide_concurrency_never_exceeds_three(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    pipeline = _FakePipeline()
    for index in range(6):
        pipeline.blocked(f"case-{index}")
    first = _seed_run(repository, case_count=3, concurrency=3, key="sched-a")
    second = _seed_run(repository, case_count=3, concurrency=3, key="sched-b")
    scheduler = _make_scheduler(repository, pipeline, tmp_path, concurrency=3)

    async with scheduler:
        await _wait_until(
            lambda: pipeline.live == 3, message="three workers in flight"
        )
        # 两个 run 的 limit 都是 3，但服务总闸是 3：不能同时跑 6 个。
        await asyncio.sleep(0.05)
        assert pipeline.live == 3
        assert pipeline.peak_live <= 3
        for index in range(6):
            pipeline.hold[f"case-{index}"].set()

    for run in (first, second):
        assert repository.get_run(run["id"])["status"] == "completed"


async def test_judge_phase_reuses_the_same_process_budget(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    """Agent 阶段结束才申请 Judge 槽位；concurrency=1 时不得自锁。"""
    pipeline = _FakePipeline()
    gate = pipeline.judge_blocked("case-0")
    run = _seed_run(repository, case_count=1, concurrency=1)
    scheduler = _make_scheduler(repository, pipeline, tmp_path, concurrency=1)

    async with scheduler:
        await _wait_until(lambda: "case-0" in pipeline.judge_calls,
                          message="judge phase entered")
        # 卡在 Judge 阶段时调度器仍在心跳，没有死锁。
        await asyncio.sleep(0.05)
        assert repository.get_run(run["id"])["status"] != "completed"
        gate.set()
        await _wait_until(
            lambda: repository.get_run(run["id"])["status"] == "completed",
            message="run completed after judge",
        )

    assert run["status"] == "queued"


# --------------------------------------------------------------------- 取消


async def test_cancelling_a_queued_run_finishes_without_running(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    pipeline = _FakePipeline()
    gate = pipeline.blocked("case-0")
    run = _seed_run(repository, case_count=2, concurrency=1)
    scheduler = _make_scheduler(repository, pipeline, tmp_path)

    async with scheduler:
        await _wait_until(lambda: pipeline.live == 1, message="first worker started")
        await scheduler.cancel_run(run["id"])
        gate.set()
        await _wait_until(
            lambda: repository.get_run(run["id"])["status"] == "cancelled",
            message="run cancelled",
        )

    detail = repository.get_run(run["id"])
    assert {item["execution_status"] for item in detail["attempts"]} == {"cancelled"}
    assert all(item["verdict"] == "not_evaluated" for item in detail["attempts"])
    # 第二个用例从未被执行：没有第二个 entered。
    assert pipeline.entered == ["case-0"]


async def test_cancelling_waits_for_process_cleanup_before_final_state(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    """先回收再落 cancelled：不能在进程还活着时就把状态写成已取消。"""
    pipeline = _FakePipeline()
    gate = pipeline.blocked("case-0")
    run = _seed_run(repository, case_count=1)
    scheduler = _make_scheduler(repository, pipeline, tmp_path)
    observed: list[str] = []

    async with scheduler:
        await _wait_until(lambda: pipeline.live == 1, message="worker started")
        cancel_task = asyncio.create_task(scheduler.cancel_run(run["id"]))
        await asyncio.sleep(0.02)
        observed.append(repository.get_run(run["id"])["status"])
        gate.set()
        await cancel_task
        await _wait_until(
            lambda: repository.get_run(run["id"])["status"] == "cancelled",
            message="finalized",
        )
        observed.append(repository.get_run(run["id"])["status"])

    # 回收期间停在 cancelling，终态只在 owner 收干净之后出现。
    assert "cancelled" in observed
    assert repository.get_run(run["id"])["status"] == "cancelled"


async def test_cancelling_during_judge_phase_still_terminates(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    pipeline = _FakePipeline()
    gate = pipeline.judge_blocked("case-0")
    run = _seed_run(repository, case_count=1)
    scheduler = _make_scheduler(repository, pipeline, tmp_path)

    async with scheduler:
        await _wait_until(lambda: "case-0" in pipeline.judge_calls,
                          message="judge phase entered")
        await scheduler.cancel_run(run["id"])
        gate.set()
        await _wait_until(
            lambda: repository.get_run(run["id"])["status"] == "cancelled",
            message="cancelled during judge",
        )


async def test_cancel_run_rejects_terminal_runs(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    pipeline = _FakePipeline()
    run = _seed_run(repository, case_count=1)
    scheduler = _make_scheduler(repository, pipeline, tmp_path)

    async with scheduler:
        await _wait_until(
            lambda: repository.get_run(run["id"])["status"] == "completed",
            message="run completed",
        )
        from nanobot.evaluation.repository import StateConflict

        with pytest.raises(StateConflict):
            await scheduler.cancel_run(run["id"])


# ----------------------------------------------------------------- 重启恢复


async def test_restart_marks_residual_runs_interrupted_without_rerun(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    run = _seed_run(repository, case_count=2, concurrency=1)
    # 模拟上次进程被杀：已领取但没提交，运行留在 running。
    repository.claim_attempt()
    assert repository.get_run(run["id"])["status"] == "running"

    second_pipeline = _FakePipeline()
    second = _make_scheduler(repository, second_pipeline, tmp_path)
    async with second:
        detail = repository.get_run(run["id"])
        assert detail["status"] == "interrupted"
        assert {item["execution_status"] for item in detail["attempts"]} == {
            "interrupted"
        }
        await asyncio.sleep(0.05)
        # 不自动补跑。
        assert second_pipeline.entered == []


async def test_restart_reports_unverifiable_pid_without_killing(
    repository: EvaluationRepository, tmp_path: Path, monkeypatch: Any
) -> None:
    """旧 PID 的启动身份不可验证时只提示人工处置，禁止按进程名杀。"""
    pipeline = _FakePipeline()
    run = _seed_run(repository, case_count=1)
    repository.claim_attempt()
    attempt = repository.get_run(run["id"])["attempts"][0]
    repository.set_attempt_process(attempt["id"], pid=4242, nonce="nonce-from-old-run")

    killed: list[int] = []
    monkeypatch.setattr(
        "nanobot.evaluation.scheduler.kill_verified_process",
        lambda pid: killed.append(pid),
    )
    monkeypatch.setattr(
        "nanobot.evaluation.scheduler.verify_launch_identity",
        lambda identity, *, strict=False: False,
    )

    scheduler = _make_scheduler(repository, pipeline, tmp_path)
    notices: list[RecoveryNotice] = []
    async with scheduler:
        notices = scheduler.recovery_notices

    assert killed == []
    assert notices
    assert notices[0].run_id == run["id"]
    assert notices[0].pid == 4242
    assert "人工" in notices[0].message
    assert repository.get_run(run["id"])["status"] == "interrupted"


async def test_restart_kills_process_whose_identity_still_verifies(
    repository: EvaluationRepository, tmp_path: Path, monkeypatch: Any
) -> None:
    pipeline = _FakePipeline()
    run = _seed_run(repository, case_count=1)
    repository.claim_attempt()
    attempt = repository.get_run(run["id"])["attempts"][0]
    repository.set_attempt_process(attempt["id"], pid=777, nonce="nonce-live")

    killed: list[int] = []
    monkeypatch.setattr(
        "nanobot.evaluation.scheduler.kill_verified_process",
        lambda pid: killed.append(pid),
    )
    monkeypatch.setattr(
        "nanobot.evaluation.scheduler.verify_launch_identity",
        lambda identity, *, strict=False: True,
    )

    scheduler = _make_scheduler(repository, pipeline, tmp_path)
    async with scheduler:
        pass

    assert killed == [777]
    assert list(scheduler.recovery_notices) == []
    assert repository.get_run(run["id"])["status"] == "interrupted"


async def test_recovery_also_settles_residual_calibrations(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    repository.create_calibration(
        snapshot={"judge_fingerprint": "fp", "rubric_version": "general-v1"},
        sample_ids=[f"g{i}" for i in range(3)],
    )
    repository.claim_calibration_item()
    scheduler = _make_scheduler(repository, _FakePipeline(), tmp_path)
    async with scheduler:
        pass
    assert repository.open_calibrations() == []


# --------------------------------------------------------------------- 校准派发


def _seed_calibration_job(
    repository: EvaluationRepository, *, item_count: int = 1
) -> dict[str, Any]:
    return repository.create_calibration(
        snapshot={"judge_profile_id": "judge-1", "gold_revision": 1,
                  "sample_ids": [f"g{i}" for i in range(item_count)]},
        sample_ids=[f"g{i}" for i in range(item_count)],
    )


async def test_calibration_waits_for_a_slot_held_by_an_attempt(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    """总闸只有一个：attempt 攥着唯一槽位时，校准项必须原地排队，不能被领取。"""
    pipeline = _FakePipeline()
    gate = pipeline.blocked("case-0")
    _seed_run(repository, case_count=1, concurrency=1)
    job = _seed_calibration_job(repository)
    scheduler = _make_scheduler(repository, pipeline, tmp_path, concurrency=1)

    async with scheduler:
        await _wait_until(lambda: pipeline.entered == ["case-0"], message="attempt in flight")
        await asyncio.sleep(0.05)
        # 领取只是把行标成 running，真正执行还要等槽位：entered 里必须还没有校准项。
        assert pipeline.calibration_entered == []
        assert [item["status"] for item in repository.list_calibration_items(job["id"])] == [
            "running"
        ]

        gate.set()
        await _wait_until(
            lambda: pipeline.calibration_entered != [], message="calibration item claimed"
        )
        await _wait_until(
            lambda: job["id"] in pipeline.settled_jobs, message="job settled"
        )

    assert repository.get_calibration(job["id"])["status"] == "completed"
    assert repository.list_calibration_items(job["id"])[0]["status"] == "completed"


async def test_inflight_calibration_item_cannot_resurrect_a_cancelled_job(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    """取消先落终态：最后一个在飞项跑完也不能把作业改回 completed 并盖上通过章。"""
    pipeline = _FakePipeline()
    pipeline.calibration_hold = asyncio.Event()
    job = _seed_calibration_job(repository)
    scheduler = _make_scheduler(repository, pipeline, tmp_path, concurrency=1)

    async with scheduler:
        await _wait_until(
            lambda: pipeline.calibration_entered != [], message="calibration item in flight"
        )
        repository.cancel_calibration(job["id"])
        pipeline.calibration_hold.set()
        await asyncio.sleep(0.1)

    detail = repository.get_calibration(job["id"])
    assert detail["status"] == "cancelled"
    assert detail["result"] is None
    assert job["id"] not in pipeline.settled_jobs
    assert [item["status"] for item in repository.list_calibration_items(job["id"])] == [
        "cancelled"
    ]


# --------------------------------------------------------------------- 收尾


async def test_stopping_the_scheduler_leaves_no_pending_tasks(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    pipeline = _FakePipeline()
    gate = pipeline.blocked("case-0")
    run = _seed_run(repository, case_count=1)
    scheduler = _make_scheduler(repository, pipeline, tmp_path)

    await scheduler.start()
    await _wait_until(lambda: pipeline.live == 1, message="worker started")
    gate.set()
    await scheduler.stop()
    await _wait_until(
        lambda: repository.get_run(run["id"])["status"] in ("completed", "cancelled"),
        message="run settled before shutdown",
    )
    assert scheduler.active_task_count == 0


async def test_pipeline_failure_is_recorded_not_swallowed(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    class Boom:
        async def execute(self, attempt, *, run):
            raise RuntimeError("pipeline exploded")

    run = _seed_run(repository, case_count=1)
    scheduler = Scheduler(
        repository=repository,
        pipeline=Boom(),  # type: ignore[arg-type]
        config=SchedulerConfig(
            data_dir=tmp_path / "service", concurrency=2, poll_interval_s=0.01
        ),
    )
    async with scheduler:
        await _wait_until(
            lambda: repository.get_run(run["id"])["status"] == "completed",
            message="run settled",
        )

    attempt = repository.get_run(run["id"])["attempts"][0]
    assert attempt["execution_status"] == "failed"
    assert attempt["verdict"] == "failed"
    result = attempt["result"]
    assert any("pipeline exploded" in item.get("message", "") for item in result["errors"])


async def test_cancelled_attempt_is_never_submitted_as_passed(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    pipeline = _FakePipeline()
    gate = pipeline.blocked("case-0")
    run = _seed_run(repository, case_count=1)
    scheduler = _make_scheduler(repository, pipeline, tmp_path)

    async with scheduler:
        await _wait_until(lambda: pipeline.live == 1, message="worker started")
        await scheduler.cancel_run(run["id"])
        gate.set()
        await _wait_until(
            lambda: repository.get_run(run["id"])["status"] == "cancelled",
            message="cancelled",
        )

    attempt = repository.get_run(run["id"])["attempts"][0]
    assert attempt["verdict"] == "not_evaluated"
