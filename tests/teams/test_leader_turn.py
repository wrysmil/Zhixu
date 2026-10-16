"""Leader 回合装配：绑定 → 运行 → 回合上下文，以及回合结束的收口与停止桥接。

这里只测 :class:`TeamLeaderTurns` 本身（真实 SQLite + 假执行器）；loop 侧的装配隔离、
registry 裁剪与续接在 ``test_leader_context.py`` 里测，两半合起来才是契约 §5 的口径。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.agent.subagent import SubagentExecutionResult
from nanobot.agents.runtime import ResolvedAgent
from nanobot.teams.binding import SESSION_TEAM_BINDING_KEY, TeamBinding
from nanobot.teams.execution import TeamExecutionRequest
from nanobot.teams.repository import TeamRepository
from nanobot.teams.scheduler import TeamScheduler
from nanobot.teams.service import TeamProfileUnavailable
from nanobot.teams.turn import TeamLeaderTurns, bind_team_turns, build_team_turns

BINDING = TeamBinding(team_id="content-team", team_revision=1, team_name="内容团队")
TOOL_NAMES = ("read_file", "write_file", "exec", "spawn")


class FakeExecutor:
    """回合装配不该碰执行细节，只需要一个能把运行撑起来的执行器。"""

    async def execute_member(self, request: TeamExecutionRequest, *, events: Any = None) -> Any:
        return SubagentExecutionResult(
            execution_id=request.attempt_id,
            outcome="succeeded",
            stop_reason="completed",
            final_content="做完了",
        )


class StubService:
    """准入检查替身：档案可用性、归档与开关由 WU-03/04 的服务层测试覆盖。"""

    def __init__(self, repository: TeamRepository, *, error: Exception | None = None) -> None:
        self.repository = repository
        self._error = error
        self.calls: list[tuple[str, int | None]] = []

    def prepare_new_run(self, team_id: str, *, revision: int | None = None) -> Any:
        self.calls.append((team_id, revision))
        if self._error is not None:
            raise self._error
        return self.repository.run_binding_snapshot(BINDING)


class StubCatalog:
    """成员执行用的目录替身：回合装配只在意 Leader 一侧，成员怎么跑由调度器测试覆盖。"""

    def resolve(self, profile: Any, *, workspace: Path) -> ResolvedAgent:
        return ResolvedAgent(
            id=profile.id,
            name=profile.name,
            description="",
            model_id=profile.model_id,
            prompt=profile.prompt,
            tool_names=frozenset({"read_file"}),
            skill_exclude=frozenset(),
        )


@pytest.fixture
def session() -> SimpleNamespace:
    return SimpleNamespace(
        key="websocket:chat-1",
        metadata={SESSION_TEAM_BINDING_KEY: BINDING.to_dict()},
    )


@pytest.fixture
def plain_session() -> SimpleNamespace:
    return SimpleNamespace(key="websocket:chat-1", metadata={})


@pytest.fixture
def msg() -> SimpleNamespace:
    return SimpleNamespace(
        chat_id="chat-1",
        metadata={"webui_turn_id": "turn-visible", "message_id": "msg-1"},
    )


@pytest.fixture
def service(repository: TeamRepository) -> StubService:
    return StubService(repository)


@pytest.fixture
def scheduler(repository: TeamRepository) -> TeamScheduler:
    return TeamScheduler(
        repository=repository,
        executor=FakeExecutor(),  # pyright: ignore[reportArgumentType] 替身只需同名方法
        members=StubCatalog(),  # pyright: ignore[reportArgumentType]
        runtime=object(),  # pyright: ignore[reportArgumentType] 测试不碰真实模型
    )


@pytest.fixture
def turns(
    service: StubService, scheduler: TeamScheduler, team_workspace: Path
) -> TeamLeaderTurns:
    return TeamLeaderTurns(
        service=service,
        scheduler=scheduler,
        workspace=team_workspace,
        tool_names=lambda: list(TOOL_NAMES),
        skill_names=lambda: ["cron", "github"],
    )


async def test_plain_chat_session_starts_no_run(
    turns: TeamLeaderTurns,
    plain_session: SimpleNamespace,
    msg: SimpleNamespace,
    service: StubService,
) -> None:
    assert await turns(msg, plain_session, "t-1") is None
    assert service.calls == []
    assert service.repository.list_runs("chat-1")["runs"] == []


async def test_recovery_continuation_never_gets_dispatch_tool(
    turns: TeamLeaderTurns,
    session: SimpleNamespace,
    msg: SimpleNamespace,
    service: StubService,
) -> None:
    """恢复续接不得重放 team_dispatch（契约 §6 末句）。

    已跑过的成员可能已经写盘或发过消息；「点继续」只能按普通回合回答，重新派活要走
    面板上的重试入口。所以这里既不能开新运行，也不能给出派活工具。
    """
    resumed = SimpleNamespace(**vars(msg))
    resumed.metadata = {**msg.metadata, "_webui_recovery_id": "recovery-1"}

    assert await turns(resumed, session, "internal-recovery") is None
    assert service.calls == []
    assert turns.scheduler.repository.list_runs("chat-1")["runs"] == []

    # 对照：同一会话的普通首条消息仍然照常装配。
    assert await turns(msg, session, "internal-1") is not None


async def test_team_turn_creates_one_run_and_binds_dispatch_tool(
    turns: TeamLeaderTurns, session: SimpleNamespace, msg: SimpleNamespace, service: StubService
) -> None:
    context = await turns(msg, session, "internal-1")

    assert context is not None
    assert service.calls == [("content-team", 1)]
    assert context.team_id == "content-team"
    assert context.team_revision == 1
    assert context.turn_id == "turn-visible", "run.turnId 必须是服务端确认的可见轮次"
    assert context.dispatch_tool is not None
    assert context.dispatch_tool.name == "team_dispatch"
    # 白名单来自冻结的 Leader 档案（include read_file/list_dir），与本机真存在的工具求交。
    assert context.tool_allow == frozenset({"read_file"})
    assert context.model_id == "opus-high"
    assert context.skill_exclude == frozenset()
    assert "内容团队" in context.role_prompt
    assert "研究员" in context.role_prompt and "撰稿人" in context.role_prompt
    repository = turns.scheduler.repository
    runs = repository.list_runs("chat-1")["runs"]
    assert [run.run_id for run in runs] == [context.run_id]
    assert runs[0].status == "running"


async def test_continuation_reuses_the_same_visible_run(
    turns: TeamLeaderTurns, session: SimpleNamespace, msg: SimpleNamespace, service: StubService
) -> None:
    first = await turns(msg, session, "internal-1")
    second = await turns(msg, session, "internal-2")

    assert first is not None and second is not None
    assert second.run_id == first.run_id
    assert service.calls == [("content-team", 1)], "续接不该再准入一次并开出第二条运行"
    assert len(turns.scheduler.repository.list_runs("chat-1")["runs"]) == 1


async def test_new_visible_turn_starts_a_new_run(
    turns: TeamLeaderTurns, session: SimpleNamespace, msg: SimpleNamespace
) -> None:
    first = await turns(msg, session, "internal-1")
    assert first is not None and first.turn_finalizer is not None
    await first.turn_finalizer(True)

    msg.metadata["webui_turn_id"] = "turn-next"
    second = await turns(msg, session, "internal-2")
    assert second is not None
    assert second.run_id != first.run_id
    assert len(turns.scheduler.repository.list_runs("chat-1")["runs"]) == 2


async def test_finalizer_closes_the_run_and_forgets_the_handle(
    turns: TeamLeaderTurns, session: SimpleNamespace, msg: SimpleNamespace
) -> None:
    context = await turns(msg, session, "internal-1")
    assert context is not None and context.turn_finalizer is not None

    await context.turn_finalizer(True)
    repository = turns.scheduler.repository
    run = repository.get_run(context.run_id)
    assert run.status == "completed"
    assert run.leader_action == "idle"
    assert turns.scheduler.get(context.run_id) is None


async def test_finalizer_marks_an_undelivered_turn_failed(
    turns: TeamLeaderTurns, session: SimpleNamespace, msg: SimpleNamespace
) -> None:
    context = await turns(msg, session, "internal-1")
    assert context is not None and context.turn_finalizer is not None

    await context.turn_finalizer(False)
    assert turns.scheduler.repository.get_run(context.run_id).status == "failed"


async def test_finalizer_keeps_the_first_terminal_state(
    turns: TeamLeaderTurns, session: SimpleNamespace, msg: SimpleNamespace
) -> None:
    context = await turns(msg, session, "internal-1")
    assert context is not None and context.turn_finalizer is not None

    await context.turn_finalizer(True)
    await context.turn_finalizer(False)
    assert turns.scheduler.repository.get_run(context.run_id).status == "completed"


async def test_admission_failure_is_loud_and_persists_nothing(
    turns: TeamLeaderTurns, session: SimpleNamespace, msg: SimpleNamespace, scheduler: TeamScheduler
) -> None:
    turns.service = StubService(
        scheduler.repository, error=TeamProfileUnavailable("agent researcher is missing")
    )

    with pytest.raises(TeamProfileUnavailable):
        await turns(msg, session, "internal-1")
    assert scheduler.repository.list_runs("chat-1")["runs"] == []


async def test_cancel_bridge_stops_the_sessions_runs(
    turns: TeamLeaderTurns, session: SimpleNamespace, msg: SimpleNamespace
) -> None:
    context = await turns(msg, session, "internal-1")
    assert context is not None

    assert await turns.cancel_runs("websocket:chat-1") == 1
    assert turns.scheduler.repository.get_run(context.run_id).status == "cancelling"
    assert await turns.cancel_runs("websocket:other") == 0


class StubHost:
    """gateway 组装层的宿主替身：只暴露两份工具目录、技能目录与默认运行时。"""

    def __init__(self) -> None:
        self.subagents = SimpleNamespace(subagent_tool_names=lambda: ["read_file", "exec"])
        self.context = SimpleNamespace(
            skills=SimpleNamespace(
                list_skills=lambda filter_unavailable=True: [
                    {"name": "cron", "path": "", "source": "builtin"},
                    {"name": "github", "path": "", "source": "workspace"},
                ]
            )
        )
        self.runtime_resolver = SimpleNamespace(runtime=object())
        self.leader_calls = 0

    def tool_names(self) -> list[str]:
        # 主循环 core registry：spawn 只存在于这一侧。
        self.leader_calls += 1
        return ["read_file", "write_file", "list_dir", "spawn"]


async def test_build_team_turns_keeps_leader_and_member_directories_apart(
    service: StubService, team_workspace: Path, session: SimpleNamespace, msg: SimpleNamespace
) -> None:
    """工厂装出来的栈：Leader 求交 core 目录，成员求交 subagent 目录（计划 §3.1）。"""
    host = StubHost()
    built = build_team_turns(service=service, host=host, workspace=team_workspace)

    context = await built(msg, session, "internal-1")
    assert context is not None
    # list_dir 只在 core 目录里出现，Leader 白名单认它；成员目录没有它，成员拿不到。
    assert context.tool_allow == frozenset({"read_file", "list_dir"})
    assert context.dispatch_tool is not None
    assert built.scheduler.repository.get_run(context.run_id).status == "running"
    assert host.leader_calls >= 1, "Leader 目录必须每次装配现取，不能在装配时抓死"


def test_bind_team_turns_is_a_noop_without_a_stack() -> None:
    agent = SimpleNamespace(team_context_provider=None, team_run_cancel_hook=None)
    bind_team_turns(agent, None)
    assert agent.team_context_provider is None
    assert agent.team_run_cancel_hook is None
