"""运行级控制与重启恢复：取消、单任务取消、重试、writer 独占与中断清理（契约 §6）。

执行器全是内存替身：``FakeExecutor`` 只记账，不接任何 provider，也发不出网络请求；
断言的重点是「哪些任务被执行过」，而不是它们跑得多好。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from nanobot.agent.subagent import SubagentExecutionResult
from nanobot.agents.models import AgentSelection
from nanobot.agents.runtime import ResolvedAgent
from nanobot.teams.binding import TeamBinding
from nanobot.teams.database import StorageUnavailable, TeamDatabase
from nanobot.teams.models import (
    AgentProfileSnapshot,
    TeamDefinition,
    TeamTaskInput,
    TeamValidationError,
)
from nanobot.teams.repository import TeamRepository
from nanobot.teams.runs import CrossSessionForbidden, TeamRunController
from nanobot.teams.scheduler import TeamScheduler
from nanobot.teams.service import ProfileRecord, TeamService

CHAT_ID = "chat-1"
SESSION_KEY = "websocket:chat-1"


class FakeExecutor:
    """共享子执行替身：按任务标题记账，用来证明「重启后没人再跑一遍」。"""

    def __init__(self, repository: TeamRepository) -> None:
        self.repository = repository
        self.started: list[str] = []
        self.fail_for: set[str] = set()
        self.gate = asyncio.Event()
        self.gate.set()

    async def execute_member(self, request: Any, *, events: Any = None) -> SubagentExecutionResult:
        title = self.repository.get_task(request.task_id).title
        self.started.append(title)
        await self.gate.wait()
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


class FakeMembers:
    def resolve(self, profile: AgentProfileSnapshot, *, workspace: Path) -> ResolvedAgent:
        return ResolvedAgent(
            id=profile.id,
            name=profile.name,
            description="",
            model_id=profile.model_id,
            prompt=profile.prompt,
            tool_names=frozenset({"read_file"}),
            skill_exclude=frozenset(),
        )


class FakeProfileDirectory:
    def __init__(self, records: dict[str, ProfileRecord]) -> None:
        self.records = records

    def get(self, agent_id: str) -> ProfileRecord | None:
        return self.records.get(agent_id)


def _selection(value: Any) -> AgentSelection:
    return AgentSelection(mode=value.mode, entries=list(value.entries))


def task(
    key: str, member_id: str, *, depends_on: list[str] | None = None, resource: str | None = None
) -> TeamTaskInput:
    """默认不声明写集：调度器会把它们排到同一把 ``workspace-write`` 锁上串行跑。

    要真并行就必须显式给互不相交的写集（契约 §7）。
    """
    return TeamTaskInput(
        key=key,
        member_id=member_id,
        title=f"任务 {key}",
        instruction=f"执行 {key}",
        acceptance="给出结果",
        depends_on=list(depends_on or []),
        resource_keys=[resource] if resource is not None else [],
    )


def binding_of(definition: TeamDefinition) -> TeamBinding:
    return TeamBinding(
        team_id=definition.id,
        team_revision=definition.revision,
        team_name=definition.name,
    )


@pytest.fixture
def profiles(fake_profiles: Any) -> FakeProfileDirectory:
    records: dict[str, ProfileRecord] = {}
    for agent_id, snapshot in fake_profiles.profiles.items():
        records[agent_id] = ProfileRecord(
            id=agent_id,
            name=snapshot.name,
            prompt=snapshot.prompt,
            model_id=snapshot.model_id,
            tools=_selection(snapshot.tools),
            skills=_selection(snapshot.skills),
            sub_agents=_selection(snapshot.sub_agents),
            hidden=False,
            updated_at=snapshot.updated_at,
        )
    return FakeProfileDirectory(records)


@pytest.fixture
def executor(repository: TeamRepository) -> FakeExecutor:
    return FakeExecutor(repository)


@pytest.fixture
def scheduler(repository: TeamRepository, executor: FakeExecutor) -> TeamScheduler:
    return TeamScheduler(
        repository=repository,
        executor=executor,  # pyright: ignore[reportArgumentType] 替身只需同名方法
        members=FakeMembers(),  # pyright: ignore[reportArgumentType]
        runtime=object(),  # pyright: ignore[reportArgumentType] 测试不碰真实模型
    )


@pytest.fixture
def service(team_db: TeamDatabase, profiles: FakeProfileDirectory) -> TeamService:
    return TeamService(TeamRepository(team_db), profiles)  # type: ignore[arg-type]


@pytest.fixture
def controller(service: TeamService, scheduler: TeamScheduler) -> TeamRunController:
    return TeamRunController(service=service, scheduler=scheduler)


async def _start_run(scheduler: TeamScheduler, definition: TeamDefinition, *, turn_id: str) -> Any:
    binding = binding_of(definition)
    return await scheduler.start_run(
        binding=binding,
        chat_id=CHAT_ID,
        session_key=SESSION_KEY,
        turn_id=turn_id,
        internal_turn_id=turn_id,
        snapshot=scheduler.repository.run_binding_snapshot(binding),
    )


async def _wait_started(executor: FakeExecutor, count: int) -> None:
    for _ in range(200):
        if len(executor.started) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {count} started tasks, saw {executor.started}")


async def _wait_terminal(repository: TeamRepository, run_id: str, *, rounds: int = 300) -> Any:
    for _ in range(rounds):
        run = repository.get_run(run_id)
        if run.status in {"completed", "partial", "failed", "cancelled", "interrupted"}:
            return run
        await asyncio.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach a terminal status")


async def _dispatch_and_finish(
    scheduler: TeamScheduler,
    definition: TeamDefinition,
    tasks: list[TeamTaskInput],
    turn_id: str,
) -> str:
    handle = await _start_run(scheduler, definition, turn_id=turn_id)
    await handle.dispatch(batch_id="batch-1", tasks=tasks)
    await handle.finalize(delivered=True)
    return handle.run_id


# --------------------------------------------------------------------- 取消


async def test_cancel_live_run_reports_cancelling_not_stopped(
    scheduler: TeamScheduler,
    executor: FakeExecutor,
    team_definition: TeamDefinition,
    controller: TeamRunController,
) -> None:
    """在场运行只回 ``cancelling``：契约不允许提前宣称全部停止。"""
    executor.gate.clear()
    handle = await _start_run(scheduler, team_definition, turn_id="turn-cancel")
    driver = asyncio.create_task(
        handle.dispatch(batch_id="b1", tasks=[task("a", team_definition.members[0].member_id)])
    )
    await _wait_started(executor, 1)

    response = await controller.cancel_run(handle.run_id, caller_chats=(CHAT_ID,))
    assert response["status"] == "cancelling"

    executor.gate.set()
    await driver
    assert scheduler.repository.get_run(handle.run_id).status == "cancelled"


async def test_cancel_task_not_in_run_is_forbidden(
    scheduler: TeamScheduler,
    executor: FakeExecutor,
    team_definition: TeamDefinition,
    controller: TeamRunController,
) -> None:
    """任务不属于所声明的运行就是跨会话控制，403（契约 §5）。"""
    await _dispatch_and_finish(
        scheduler, team_definition, [task("a", team_definition.members[0].member_id)], "turn-1"
    )
    run_id = await _dispatch_and_finish(
        scheduler, team_definition, [task("b", team_definition.members[0].member_id)], "turn-2"
    )
    with pytest.raises(CrossSessionForbidden):
        await controller.cancel_task(
            run_id=run_id, task_id="task-from-another-run", caller_chats=(CHAT_ID,)
        )


async def test_control_from_another_session_is_forbidden(
    scheduler: TeamScheduler,
    executor: FakeExecutor,
    team_definition: TeamDefinition,
    controller: TeamRunController,
) -> None:
    """另一个会话拿着 runId 也取消不了、重试不了（契约 §3/§5 的会话访问检查，验收 A18）。

    这条是授权面本身：没有它，「已认证」就等于「能控制同一工作空间里的任何运行」，
    而且 ``retry_run`` 还会拿原运行的 chatId 去建新运行，等于替别人开会话。
    """
    run_id = await _dispatch_and_finish(
        scheduler, team_definition, [task("a", team_definition.members[0].member_id)], "turn-1"
    )
    # 没挂着任何聊天的连接：推不出归属，按拒绝处理。
    with pytest.raises(CrossSessionForbidden):
        await controller.cancel_run(run_id)
    with pytest.raises(CrossSessionForbidden):
        await controller.cancel_run(run_id, caller_chats=("chat-someone-else",))
    with pytest.raises(CrossSessionForbidden):
        await controller.cancel_task(
            run_id=run_id,
            task_id=scheduler.repository.get_run(run_id).tasks[0].task_id,
            caller_chats=("chat-someone-else",),
        )
    with pytest.raises(CrossSessionForbidden):
        await controller.retry_run(
            run_id, request_id="req-foreign", caller_chats=("chat-someone-else",)
        )
    # 被拒的请求不能留下痕迹：运行状态与任务状态都没被改动。
    assert scheduler.repository.get_run(run_id).status == "completed"


async def test_read_actions_reject_foreign_session_but_serve_own(
    scheduler: TeamScheduler,
    executor: FakeExecutor,
    team_definition: TeamDefinition,
    controller: TeamRunController,
) -> None:
    """读动作同样过会话检查（契约 §5「会话访问检查」）；推不出身份时不拦。"""
    service = controller.service
    run_id = await _dispatch_and_finish(
        scheduler, team_definition, [task("a", team_definition.members[0].member_id)], "turn-1"
    )
    assert service.get_run(run_id, caller_chats=(CHAT_ID,))["run"]["runId"] == run_id
    with pytest.raises(CrossSessionForbidden):
        service.get_run(run_id, caller_chats=("chat-someone-else",))
    with pytest.raises(CrossSessionForbidden):
        service.require_run_session(run_id, ("chat-someone-else",))
    # 普通 HTTP GET 没有聊天身份，边界是工作空间：不拦。
    assert service.get_run(run_id)["run"]["runId"] == run_id


async def test_cancel_single_task_keeps_succeeded_siblings(
    scheduler: TeamScheduler,
    executor: FakeExecutor,
    team_definition: TeamDefinition,
    controller: TeamRunController,
) -> None:
    """单任务取消不动成功兄弟（契约 §6）。"""
    executor.gate.clear()
    handle = await _start_run(scheduler, team_definition, turn_id="turn-single")
    first, second = team_definition.members[0], team_definition.members[1]
    driver = asyncio.create_task(
        handle.dispatch(
            batch_id="b1",
            tasks=[
                task("a", first.member_id, resource="file:a"),
                task("b", second.member_id, resource="file:b"),
            ],
        )
    )
    await _wait_started(executor, 2)

    target = next(
        item.task_id
        for item in scheduler.repository.get_run(handle.run_id).tasks
        if item.title == "任务 a"
    )
    response = await controller.cancel_task(
        run_id=handle.run_id, task_id=target, caller_chats=(CHAT_ID,)
    )
    assert response["status"] in {"cancelling", "cancelled"}

    executor.gate.set()
    await driver
    statuses = {
        item.title: item.status for item in scheduler.repository.get_run(handle.run_id).tasks
    }
    assert statuses["任务 a"] == "cancelled"
    assert statuses["任务 b"] == "succeeded"


# --------------------------------------------------------------------- 重试


async def _half_failed_run(
    scheduler: TeamScheduler,
    executor: FakeExecutor,
    definition: TeamDefinition,
) -> str:
    """a 成功、b 失败、c 因依赖 b 被 blocked 的一条终态运行。"""
    executor.fail_for.add("任务 b")
    handle = await _start_run(scheduler, definition, turn_id="turn-retry")
    member_ids = [member.member_id for member in definition.members]
    await handle.dispatch(
        batch_id="b1",
        tasks=[
            task("a", member_ids[0], resource="file:a"),
            task("b", member_ids[1], resource="file:b"),
            task("c", member_ids[0], depends_on=["b"], resource="file:c"),
        ],
    )
    await handle.finalize(delivered=True)
    return handle.run_id


async def test_retry_copies_only_failed_subgraph_with_readonly_references(
    scheduler: TeamScheduler,
    executor: FakeExecutor,
    team_definition: TeamDefinition,
    controller: TeamRunController,
) -> None:
    """重试只复制失败子图；已成功前置以只读引用进 instruction（契约 §5）。"""
    run_id = await _half_failed_run(scheduler, executor, team_definition)
    origin = scheduler.repository.get_run(run_id)
    assert {item.title: item.status for item in origin.tasks} == {
        "任务 a": "succeeded",
        "任务 b": "failed",
        "任务 c": "blocked",
    }

    executor.started.clear()
    executor.fail_for.clear()
    response = await controller.retry_run(
        run_id, request_id="req-retry-1", caller_chats=(CHAT_ID,)
    )
    retried = await _wait_terminal(scheduler.repository, response["newRunId"])

    assert retried.binding.team_revision == team_definition.revision
    assert [item.title for item in retried.tasks] == ["任务 b", "任务 c"]
    assert [item.status for item in retried.tasks] == ["succeeded", "succeeded"]
    assert sorted(executor.started) == ["任务 b", "任务 c"]
    # 原运行不得被改写：终态不可回退（契约 §6）。
    assert scheduler.repository.get_run(run_id).status == origin.status


async def test_retry_is_idempotent_per_request_id(
    scheduler: TeamScheduler,
    executor: FakeExecutor,
    team_definition: TeamDefinition,
    controller: TeamRunController,
) -> None:
    """同一 ``requestId`` 重放只起一条新运行（契约 §5）。"""
    run_id = await _half_failed_run(scheduler, executor, team_definition)
    first = await controller.retry_run(run_id, request_id="req-same", caller_chats=(CHAT_ID,))
    await _wait_terminal(scheduler.repository, first["newRunId"])
    second = await controller.retry_run(
        run_id, request_id="req-same", caller_chats=(CHAT_ID,)
    )
    assert second["newRunId"] == first["newRunId"]
    assert second["taskIds"] == first["taskIds"]


async def test_retry_rejects_non_terminal_run(
    scheduler: TeamScheduler,
    executor: FakeExecutor,
    team_definition: TeamDefinition,
    controller: TeamRunController,
) -> None:
    executor.gate.clear()
    handle = await _start_run(scheduler, team_definition, turn_id="turn-live")
    driver = asyncio.create_task(
        handle.dispatch(batch_id="b1", tasks=[task("a", team_definition.members[0].member_id)])
    )
    await _wait_started(executor, 1)
    with pytest.raises(TeamValidationError):
        await controller.retry_run(handle.run_id, request_id="req-live", caller_chats=(CHAT_ID,))
    executor.gate.set()
    await driver


async def test_retry_refuses_unavailable_model_preset(
    scheduler: TeamScheduler,
    executor: FakeExecutor,
    team_definition: TeamDefinition,
    controller: TeamRunController,
) -> None:
    """preset 没了要在建新运行之前就拒绝（契约 §5）。"""
    run_id = await _half_failed_run(scheduler, executor, team_definition)

    def _missing(_name: str) -> Any:
        raise ValueError("preset is gone")

    refusing = TeamRunController(
        service=controller.service, scheduler=scheduler, preset_resolver=_missing
    )
    with pytest.raises(Exception, match="MODEL_PRESET_UNAVAILABLE"):
        await refusing.retry_run(
            run_id, request_id="req-preset", caller_chats=(CHAT_ID,)
        )


# --------------------------------------------------------- 重启恢复与 writer


async def test_restart_marks_legacy_runs_interrupted_without_reexecuting(
    repository: TeamRepository,
    team_definition: TeamDefinition,
    profiles: FakeProfileDirectory,
) -> None:
    """进程死掉留下的 running 必须在重启时落 interrupted，且谁都不许再执行一次。"""
    repository.release_writer()
    binding = binding_of(team_definition)
    run = repository.create_run(
        binding=binding,
        chat_id=CHAT_ID,
        session_key=SESSION_KEY,
        turn_id="turn-dead",
        internal_turn_id="turn-dead",
        snapshot=repository.run_binding_snapshot(binding),
    )
    task_row = repository.create_task(
        run_id=run.run_id,
        member=team_definition.members[0],
        title="重启前就在跑",
        acceptance="给出结果",
        instruction="别再跑我",
        status="running",
    )

    # 新进程：全新 repository 实例（重启后没有任何子进程活着）。
    fresh_repository = TeamRepository(TeamDatabase(repository.db.workspace))
    fresh_service = TeamService(fresh_repository, profiles)  # type: ignore[arg-type]
    assert fresh_repository.hold_writer() is True
    fresh_executor = FakeExecutor(fresh_repository)

    recovery = fresh_service.recover_after_restart()
    assert recovery["interruptedRuns"] == [run.run_id]

    assert fresh_repository.get_run(run.run_id).status == "interrupted"
    assert fresh_repository.get_task(task_row.task_id).status == "interrupted"
    assert fresh_executor.started == []
    # 终态事件可供客户端补拉，UI 才拿得到手动重试入口。
    assert any(event.type == "run.finished" for event in fresh_repository.list_events(run.run_id))


async def test_second_writer_cannot_start_scheduling(
    team_db: TeamDatabase, profiles: FakeProfileDirectory
) -> None:
    """第二个 gateway 抢不到 writer 就只能读，不能起运行（契约 §6）。"""
    holder = TeamRepository(team_db)
    assert holder.hold_writer() is True

    rival = TeamRepository(TeamDatabase(team_db.workspace))
    assert rival.hold_writer() is False
    rival_service = TeamService(rival, profiles, writer_owned=False)  # type: ignore[arg-type]
    assert rival_service.recover_after_restart() == {"interruptedRuns": [], "prunedRuns": []}
    with pytest.raises(StorageUnavailable):
        rival_service.prepare_new_run("content-team")

    holder.release_writer()
    assert rival.hold_writer() is True


async def test_startup_cleanup_requires_the_writer(team_db: TeamDatabase) -> None:
    """没有 writer 的连接连遗留状态都不许改写。"""
    repository = TeamRepository(team_db)
    with pytest.raises(StorageUnavailable):
        repository.mark_interrupted_runs()
    with pytest.raises(StorageUnavailable):
        repository.prune_activity_events()
