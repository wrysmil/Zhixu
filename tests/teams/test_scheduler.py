"""团队调度器的行为测试：DAG 校验、真实并行、配额、取消、幂等与超时。

装置全部是内存替身：``FakeExecutor`` 记录启动顺序与输入，可由屏障、失败名单和
放行闸门编排，不接任何真实 provider，也不发真实网络请求。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from nanobot.agent.subagent import SubagentExecutionResult
from nanobot.agents.runtime import ResolvedAgent
from nanobot.teams.models import (
    AgentProfileSnapshot,
    TeamBinding,
    TeamLimits,
    TeamTaskInput,
    TeamValidationError,
)
from nanobot.teams.repository import DispatchKeyConflict, TeamRepository
from nanobot.teams.scheduler import TeamScheduler

BINDING = TeamBinding(team_id="content-team", team_revision=1, team_name="内容团队")


def task(
    key: str,
    member_id: str,
    *,
    depends_on: list[str] | None = None,
    resource_keys: list[str] | None = None,
) -> TeamTaskInput:
    return TeamTaskInput(
        key=key,
        member_id=member_id,
        title=f"任务 {key}",
        instruction=f"执行 {key}",
        acceptance="给出结果",
        depends_on=list(depends_on or []),
        resource_keys=list(resource_keys or []),
    )


@dataclass
class FakeExecutor:
    """共享子执行替身：用任务标题认任务。"""

    repository: TeamRepository
    started: list[str] = field(default_factory=list)
    finished: list[str] = field(default_factory=list)
    instructions: dict[str, str] = field(default_factory=dict)
    running: int = 0
    peak: int = 0
    barrier: asyncio.Event | None = None
    fail_for: set[str] = field(default_factory=set)
    block_for: set[str] = field(default_factory=set)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_observed: list[str] = field(default_factory=list)

    async def execute_member(
        self, request: Any, *, events: Any = None
    ) -> SubagentExecutionResult:
        title = self.repository.get_task(request.task_id).title
        self.instructions[title] = request.instruction
        self.started.append(title)
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            if self.barrier is not None and len(self.started) >= 2:
                self.barrier.set()
            if self.barrier is not None:
                await asyncio.wait_for(self.barrier.wait(), timeout=2)
            if title in self.block_for:
                await asyncio.wait_for(self.release.wait(), timeout=5)
            if title in self.fail_for:
                return SubagentExecutionResult(
                    execution_id=request.attempt_id,
                    outcome="failed",
                    stop_reason="error",
                    final_content="成员失败了",
                    error_code="provider_error",
                )
            return SubagentExecutionResult(
                execution_id=request.attempt_id,
                outcome="succeeded",
                stop_reason="completed",
                final_content=f"{title} 的结果",
            )
        except asyncio.CancelledError:
            self.cancel_observed.append(title)
            raise
        finally:
            self.running -= 1
            self.finished.append(title)


class FakeMembers:
    """冻结档案 → 运行时约束：只搬工具白名单，不按 agentId 重读盘。"""

    def resolve(self, profile: AgentProfileSnapshot, *, workspace: Any) -> ResolvedAgent:
        return ResolvedAgent(
            id=profile.id,
            name=profile.name,
            description="",
            model_id=profile.model_id,
            prompt=profile.prompt,
            tool_names=frozenset(profile.tools.entries),
        )


@pytest.fixture
def executor(repository: TeamRepository) -> FakeExecutor:
    return FakeExecutor(repository=repository)


def build_scheduler(
    repository: TeamRepository, executor: FakeExecutor, **overrides: Any
) -> TeamScheduler:
    kwargs: dict[str, Any] = {
        "repository": repository,
        "executor": executor,  # pyright: ignore[reportArgumentType] 替身只需同名方法
        "members": FakeMembers(),
        "runtime": object(),  # pyright: ignore[reportArgumentType] 测试不碰真实模型
    }
    kwargs.update(overrides)
    return TeamScheduler(**kwargs)


@pytest.fixture
def scheduler(repository: TeamRepository, executor: FakeExecutor) -> TeamScheduler:
    return build_scheduler(repository, executor)


async def start_run(scheduler: TeamScheduler) -> Any:
    return await scheduler.start_run(
        binding=BINDING,
        chat_id="chat-1",
        session_key="websocket:chat-1",
        turn_id="turn-1",
        internal_turn_id="internal-1",
    )


async def wait_until(predicate: Any, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("等待的条件没有在超时前成立")
        await asyncio.sleep(0.01)


# ------------------------------------------------------------------ 整批拒绝


async def test_unknown_member_starts_nothing(scheduler: TeamScheduler, executor: FakeExecutor):
    handle = await start_run(scheduler)
    with pytest.raises(TeamValidationError, match="member"):
        await handle.dispatch(batch_id="batch-1", tasks=[task("research", "outsider")])
    assert executor.started == []
    assert handle.run().tasks == []


async def test_cycle_is_rejected_before_any_persistence(
    scheduler: TeamScheduler, executor: FakeExecutor
):
    handle = await start_run(scheduler)
    with pytest.raises(TeamValidationError, match="dependsOn"):
        await handle.dispatch(
            batch_id="batch-1",
            tasks=[
                task("a", "m-researcher", depends_on=["b"]),
                task("b", "m-writer", depends_on=["a"]),
            ],
        )
    assert executor.started == []
    assert handle.run().tasks == []


async def test_dependency_outside_batch_is_rejected(
    scheduler: TeamScheduler, executor: FakeExecutor
):
    handle = await start_run(scheduler)
    with pytest.raises(TeamValidationError, match="dependsOn"):
        await handle.dispatch(
            batch_id="batch-1", tasks=[task("a", "m-researcher", depends_on=["ghost"])]
        )
    assert executor.started == []


async def test_duplicate_key_and_over_limit_are_rejected(
    scheduler: TeamScheduler, executor: FakeExecutor
):
    handle = await start_run(scheduler)
    with pytest.raises(TeamValidationError, match="key"):
        await handle.dispatch(
            batch_id="batch-1", tasks=[task("a", "m-researcher"), task("a", "m-writer")]
        )
    with pytest.raises(TeamValidationError, match="maxTasks"):
        await handle.dispatch(
            batch_id="batch-2",
            tasks=[task(f"t{index}", "m-researcher") for index in range(17)],
        )
    assert executor.started == []


async def test_task_budget_accumulates_across_batches(
    repository: TeamRepository, executor: FakeExecutor
):
    """任务数预算按**运行**累计：换 batchId 不能把一次运行撑到 4 倍（契约 §5 预算表）。

    三批 × 5 条 = 15 已落地，第四批再要 2 条就越过 16 → 整批拒绝且一条不起；
    第四批只要 1 条（累计 16）仍合法。批次轮数上限也是 4，正好卡在边界上。
    """
    scheduler = build_scheduler(repository, executor)
    handle = await start_run(scheduler)
    limit = TeamLimits().max_tasks
    for index in range(3):
        await handle.dispatch(
            batch_id=f"batch-{index}",
            tasks=[task(f"t{index}-{n}", "m-researcher") for n in range(5)],
        )
    assert repository.task_count(handle.run_id) == 15

    with pytest.raises(TeamValidationError, match="maxTasks"):
        await handle.dispatch(
            batch_id="batch-over",
            tasks=[task("x0", "m-researcher"), task("x1", "m-researcher")],
        )
    assert repository.task_count(handle.run_id) == 15, "被拒的批次不能落任何任务"

    results = await handle.dispatch(
        batch_id="batch-fit", tasks=[task("y0", "m-researcher")]
    )
    assert [item.status for item in results] == ["succeeded"]
    assert repository.task_count(handle.run_id) == limit


async def test_empty_batch_is_rejected(scheduler: TeamScheduler, executor: FakeExecutor):
    handle = await start_run(scheduler)
    with pytest.raises(TeamValidationError, match="tasks"):
        await handle.dispatch(batch_id="batch-1", tasks=[])
    assert executor.started == []


# ------------------------------------------------------------------ 执行顺序


async def test_disjoint_write_sets_run_in_parallel(scheduler: TeamScheduler, executor: FakeExecutor):
    executor.barrier = asyncio.Event()
    handle = await start_run(scheduler)
    results = await handle.dispatch(
        batch_id="batch-1",
        tasks=[
            task("research", "m-researcher", resource_keys=["file:research.md"]),
            task("draft", "m-writer", resource_keys=["file:draft.md"]),
        ],
    )
    assert executor.peak == 2, "显式互不相交的写集必须真的同时在场，而不是排队"
    assert [result.status for result in results] == ["succeeded", "succeeded"]
    assert [result.task_id for result in results] == [item.task_id for item in handle.run().tasks]


async def test_undeclared_write_set_is_serialized(scheduler: TeamScheduler, executor: FakeExecutor):
    """契约 §7：没声明写集的任务一律共用 workspace-write 锁，宁慢不冲突。"""
    handle = await start_run(scheduler)
    await handle.dispatch(
        batch_id="batch-1",
        tasks=[task("research", "m-researcher"), task("draft", "m-writer")],
    )
    assert executor.peak == 1


async def test_dependent_task_waits_for_upstream(scheduler: TeamScheduler, executor: FakeExecutor):
    handle = await start_run(scheduler)
    await handle.dispatch(
        batch_id="batch-1",
        tasks=[
            task("draft", "m-writer", depends_on=["research"]),
            task("research", "m-researcher"),
        ],
    )
    assert executor.started == ["任务 research", "任务 draft"]
    # 上游结果必须进到下游输入里，否则「依赖」只剩一个排序标签。
    assert "任务 research 的结果" in executor.instructions["任务 draft"]


async def test_downstream_of_failure_is_blocked_and_siblings_keep_results(
    scheduler: TeamScheduler, executor: FakeExecutor
):
    executor.fail_for = {"任务 research"}
    handle = await start_run(scheduler)
    results = await handle.dispatch(
        batch_id="batch-1",
        tasks=[
            task("research", "m-researcher"),
            task("draft", "m-writer", depends_on=["research"]),
            task("aside", "m-writer"),
        ],
    )
    assert {result.status for result in results} == {"failed", "blocked", "succeeded"}
    assert {item.status for item in handle.run().tasks} == {"failed", "blocked", "succeeded"}
    assert "任务 aside" in executor.finished, "失败成员不该拦下无关的兄弟任务"


async def test_same_member_is_serialized(scheduler: TeamScheduler, executor: FakeExecutor):
    handle = await start_run(scheduler)
    await handle.dispatch(
        batch_id="batch-1",
        tasks=[task("a", "m-researcher"), task("b", "m-researcher"), task("c", "m-researcher")],
    )
    assert executor.peak == 1
    assert executor.started == ["任务 a", "任务 b", "任务 c"]


async def test_shared_resource_serializes_across_members(
    scheduler: TeamScheduler, executor: FakeExecutor
):
    handle = await start_run(scheduler)
    await handle.dispatch(
        batch_id="batch-1",
        tasks=[
            task("a", "m-researcher", resource_keys=["file:out.md"]),
            task("b", "m-writer", resource_keys=["file:out.md"]),
        ],
    )
    assert executor.peak == 1


# ------------------------------------------------------------------ 限额与超时


async def test_dispatch_round_limit(scheduler: TeamScheduler):
    handle = await start_run(scheduler)
    for index in range(4):
        await handle.dispatch(batch_id=f"batch-{index}", tasks=[task("a", "m-researcher")])
    with pytest.raises(TeamValidationError, match="maxDispatchRounds"):
        await handle.dispatch(batch_id="batch-5", tasks=[task("a", "m-researcher")])


async def test_task_timeout_only_kills_that_task(repository: TeamRepository):
    executor = FakeExecutor(repository=repository)
    # 0.2s 只用于测试：慢成员必然超时，快成员在同一个事件循环轮次内就返回了。
    scheduler = build_scheduler(repository, executor, task_timeout_seconds=0.2)
    handle = await start_run(scheduler)
    executor.block_for = {"任务 slow"}
    results = await handle.dispatch(
        batch_id="batch-1",
        tasks=[task("slow", "m-researcher"), task("fast", "m-writer")],
    )
    assert {result.status for result in results} == {"failed", "succeeded"}
    assert next(item for item in results if item.status == "failed").error_code == "timeout"


# ------------------------------------------------------------------ 幂等


async def test_repeated_batch_id_is_idempotent(scheduler: TeamScheduler, executor: FakeExecutor):
    handle = await start_run(scheduler)
    tasks = [task("research", "m-researcher")]
    first = await handle.dispatch(batch_id="batch-1", tasks=tasks)
    second = await handle.dispatch(batch_id="batch-1", tasks=tasks)
    assert [item.task_id for item in first] == [item.task_id for item in second]
    assert executor.started == ["任务 research"]


async def test_same_batch_id_with_other_payload_conflicts(scheduler: TeamScheduler):
    handle = await start_run(scheduler)
    await handle.dispatch(batch_id="batch-1", tasks=[task("research", "m-researcher")])
    with pytest.raises(DispatchKeyConflict):
        await handle.dispatch(batch_id="batch-1", tasks=[task("draft", "m-writer")])


# ------------------------------------------------------------------ 取消与收口


async def test_parent_cancel_waits_for_children(
    scheduler: TeamScheduler, executor: FakeExecutor
):
    handle = await start_run(scheduler)
    executor.block_for = {"任务 a", "任务 b"}
    dispatch = asyncio.create_task(
        handle.dispatch(
            batch_id="batch-1",
            tasks=[
                task("a", "m-researcher", resource_keys=["file:a.md"]),
                task("b", "m-writer", resource_keys=["file:b.md"]),
            ],
        )
    )
    await wait_until(lambda: len(executor.started) == 2)
    snapshot = await handle.cancel()
    assert snapshot.status == "cancelling"
    results = await dispatch
    assert {result.status for result in results} == {"cancelled"}
    assert set(executor.cancel_observed) == {"任务 a", "任务 b"}
    assert handle.run().status == "cancelled"


async def test_single_task_cancel_does_not_touch_siblings(
    scheduler: TeamScheduler, executor: FakeExecutor
):
    handle = await start_run(scheduler)
    executor.block_for = {"任务 a"}
    dispatch = asyncio.create_task(
        handle.dispatch(
            batch_id="batch-1",
            tasks=[
                task("a", "m-researcher", resource_keys=["file:a.md"]),
                task("b", "m-writer", resource_keys=["file:b.md"]),
            ],
        )
    )
    await wait_until(lambda: "任务 b" in executor.finished)
    hanging = next(item for item in handle.run().tasks if item.title == "任务 a")
    await handle.cancel_task(hanging.task_id)
    results = await dispatch
    assert {result.status for result in results} == {"cancelled", "succeeded"}
    assert executor.cancel_observed == ["任务 a"]


async def test_cancelled_task_blocks_its_dependents(scheduler: TeamScheduler, executor: FakeExecutor):
    handle = await start_run(scheduler)
    executor.block_for = {"任务 a"}
    dispatch = asyncio.create_task(
        handle.dispatch(
            batch_id="batch-1",
            tasks=[task("a", "m-researcher"), task("b", "m-writer", depends_on=["a"])],
        )
    )
    await wait_until(lambda: "任务 a" in executor.started)
    hanging = next(item for item in handle.run().tasks if item.title == "任务 a")
    await handle.cancel_task(hanging.task_id)
    results = await dispatch
    assert {result.status for result in results} == {"cancelled", "blocked"}


async def test_finalize_closes_the_run_and_blocks_late_dispatch(
    scheduler: TeamScheduler, executor: FakeExecutor
):
    handle = await start_run(scheduler)
    await handle.dispatch(batch_id="batch-1", tasks=[task("a", "m-researcher")])
    run = await handle.finalize(delivered=True)
    assert run.status == "completed"
    assert run.finished_at is not None
    with pytest.raises(TeamValidationError, match="finished"):
        await handle.dispatch(batch_id="batch-2", tasks=[task("b", "m-writer")])
    assert executor.started == ["任务 a"]


async def test_finalize_without_dispatch_completes_run(scheduler: TeamScheduler):
    handle = await start_run(scheduler)
    run = await handle.finalize(delivered=True)
    assert run.status == "completed"
    assert run.leader_action == "idle"


async def test_finalize_after_error_fails_run(scheduler: TeamScheduler):
    handle = await start_run(scheduler)
    run = await handle.finalize(delivered=False)
    assert run.status == "failed"


async def test_finalize_mixed_results_is_partial(scheduler: TeamScheduler, executor: FakeExecutor):
    executor.fail_for = {"任务 a"}
    handle = await start_run(scheduler)
    await handle.dispatch(
        batch_id="batch-1", tasks=[task("a", "m-researcher"), task("b", "m-writer")]
    )
    run = await handle.finalize(delivered=True)
    assert run.status == "partial"


async def test_run_deadline_stops_queued_tasks(repository: TeamRepository):
    executor = FakeExecutor(repository=repository)
    clock = {"now": 1000.0}
    scheduler = build_scheduler(repository, executor, clock=lambda: clock["now"])
    handle = await start_run(scheduler)
    executor.block_for = {"任务 a"}
    dispatch = asyncio.create_task(
        handle.dispatch(
            batch_id="batch-1",
            tasks=[task("a", "m-researcher"), task("b", "m-writer", depends_on=["a"])],
        )
    )
    await wait_until(lambda: bool(executor.started))
    # 越过 runTimeoutSeconds（fixture 为 1800）之后再放行：排队中的任务不得绕过运行超时。
    clock["now"] += 1801
    executor.release.set()
    results = await dispatch
    assert "任务 b" not in executor.started
    assert {result.status for result in results} == {"succeeded", "cancelled"}
