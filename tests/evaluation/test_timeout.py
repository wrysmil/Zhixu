"""run_timeout_s 总时限：到点由调度器批量停止，in-flight attempt 收口 timed_out。

契约要点（WU-03）：

- 不限时（None）的运行不受影响。
- 到点触发的是 ``cancel_run(run_id, reason="timeout")``：排队中的项与在飞项都按
  ``timed_out`` 收口，run 终态写 ``timed_out``，与用户取消（cancelled）区分。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.scheduler import Scheduler, SchedulerConfig
from nanobot.evaluation.schemas import RunCreate
from nanobot.evaluation.services import EvaluationService

from .conftest import (
    make_case_payload,
    make_run_payload,
    make_rubric_payload,
    make_suite_payload,
)


@pytest.fixture
def repository(evaluation_repository: EvaluationRepository) -> EvaluationRepository:
    return evaluation_repository


def _seed_judge_blocked_run(
    repository: EvaluationRepository,
    service: EvaluationService,
    *,
    run_timeout_s: int | None,
    key: str,
) -> dict[str, Any]:
    """建一个「Agent 秒过、Judge 永远挂起」的运行：attempt 稳定停在在飞状态。

    Agent 阶段走受控 provider（用例声明 script），评分阶段注入永不返回的
    ``judge_runner``——总时限 watchdog 因此有稳定的拦截对象，也不依赖任何网络。
    """
    suite = repository.create_suite(make_suite_payload())
    payload = make_case_payload(
        suite_id=suite["id"],
        script=[{"text": "标识是 alpha。"}],
        rubric=make_rubric_payload(),
    )
    case = repository.save_case(payload)
    body: dict[str, Any] = {
        **make_run_payload(
            [case["id"]],
            suite_id=suite["id"],
            agent_profile_id="controlled-agent",
            judge_profile_id="controlled-judge",
        )
    }
    if run_timeout_s is not None:
        body["run_timeout_s"] = run_timeout_s
    return service.create_run(
        RunCreate.model_validate(body), idempotency_key=key, scope="t"
    )


async def _wait_for_status(
    repository: EvaluationRepository, run_id: str, status: str, *, seconds: float
) -> str:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        if repository.get_run(run_id)["status"] == status:
            return status
        await asyncio.sleep(0.1)
    return repository.get_run(run_id)["status"]


async def test_deadline_marks_inflight_attempt_timed_out(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    async def blocked_judge(**_kwargs: Any) -> Any:
        await asyncio.Event().wait()  # 永不完成：模拟评分阶段的长任务

    service = EvaluationService(
        repository=repository, data_dir=tmp_path, judge_runner=blocked_judge
    )
    scheduler = Scheduler(
        repository=repository,
        pipeline=service,
        config=SchedulerConfig(
            data_dir=tmp_path, concurrency=1, poll_interval_s=0.05
        ),
    )
    run = _seed_judge_blocked_run(repository, service, run_timeout_s=5, key="t1")
    async with scheduler:
        status = await _wait_for_status(
            repository, run["id"], "timed_out", seconds=30
        )
    assert status == "timed_out"
    detail = repository.get_run(run["id"])
    attempt = detail["attempts"][0]
    assert attempt["execution_status"] == "timed_out"
    assert attempt["grading_status"] == "not_applicable"
    assert attempt["verdict"] == "not_evaluated"


async def test_run_without_timeout_settles_normally(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    """不限时（None）的运行照常收口 completed，watchdog 不介入。"""
    service = EvaluationService(repository=repository, data_dir=tmp_path)
    scheduler = Scheduler(
        repository=repository,
        pipeline=service,
        config=SchedulerConfig(
            data_dir=tmp_path, concurrency=1, poll_interval_s=0.05
        ),
    )
    run = _seed_judge_blocked_run(repository, service, run_timeout_s=None, key="t2")
    # 同一形状但把 judge 换成立即可完成：不限时路径要能正常跑完。
    async def instant_judge(**_kwargs: Any) -> Any:
        from nanobot.evaluation.schemas import JudgeDimensionResult, JudgeResult

        return JudgeResult(
            status="completed", model="eval-model", version="judge-v1",
            dimensions=[
                JudgeDimensionResult(id="task_completion", score=4, reason="ok"),
                JudgeDimensionResult(id="correctness", score=4, reason="ok"),
            ],
            calibrated=False,
        )

    service._judge_runner = instant_judge  # noqa: SLF001 - 测试替换注入点
    async with scheduler:
        status = await _wait_for_status(
            repository, run["id"], "completed", seconds=30
        )
    assert status == "completed"
