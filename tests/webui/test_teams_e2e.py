"""Agent Team 端到端集成：真实服务 + 真实调度器 + 真实投影（plan Task 11 / 契约 §4–§7）。

这里刻意**不使用**任何测试替身来替代被验证的对象本身：团队修订、运行、任务、事件、
seq、取消状态机和 WebUI 帧投影全部走生产代码；只有两处是真的假的——

1. Leader（不跑真实模型）：测试直接调用本回合注入的 ``team_dispatch`` 工具，等价于
   模型在回合里派单；
2. 成员（不跑真实 provider）：``FakeMemberExecutor`` 按任务标题给结论，用来编排成功、
   失败、阻塞与屏障。

覆盖计划 Task 11 要求的五轮：全成功、一轮失败→partial、一轮取消、一轮重连、一轮重启。
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.agent.subagent import SubagentExecutionResult
from nanobot.agents.models import AgentSelection
from nanobot.agents.runtime import ResolvedAgent
from nanobot.bus.events import OutboundMessage
from nanobot.teams.binding import SESSION_TEAM_BINDING_KEY, TeamBinding
from nanobot.teams.database import TeamDatabase
from nanobot.teams.events import TeamRunUpdateEvent, project_run_snapshot
from nanobot.teams.models import (
    AgentProfileSnapshot,
    TeamDefinition,
    TeamRevisionMember,
    TeamRevisionSnapshot,
    TeamTaskInput,
)
from nanobot.teams.repository import TeamRepository
from nanobot.teams.runs import CrossSessionForbidden, TeamRunController
from nanobot.teams.service import ProfileRecord, TeamService
from nanobot.teams.turn import TeamLeaderTurns, build_team_turns
from nanobot.webui.outbound_projection import WebUIOutboundProjector

CHAT_ID = "chat-e2e"
SESSION_KEY = "websocket:chat-e2e"
FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "teams"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class FakeSubagentManager:
    """``SubagentManager.execute_frozen`` 的替身：共享 Runner 那一层不跑真实模型。

    只实现团队用到的这一入口，因此**真的** :class:`TeamMemberExecutor`、冻结档案解析、
    活动转发和配额收口都在链路里跑，只有最后那次 LLM 往返是假的。
    """

    def __init__(self, repository: TeamRepository) -> None:
        self.repository = repository
        self.started: list[str] = []
        self.finished: list[str] = []
        self.fail_for: set[str] = set()
        self.running = 0
        self.peak = 0
        self.barrier: asyncio.Event | None = None
        self.release: asyncio.Event = asyncio.Event()
        self.release.set()
        self.cancelled: list[str] = []
        self.activities: list[str] = []

    def block(self) -> None:
        """从现在起成员停在执行里，直到测试放行；用来观察真实的并行与取消。"""
        self.barrier = asyncio.Event()
        self.release = asyncio.Event()
        self.activities: list[str] = []

    def _title(self, instruction: str) -> str:
        """本文件的标题与指令同源（``任务 {key}`` / ``执行 {key} …``），据此认出任务。

        只认开头：调度器会给依赖任务追加上游结论块，尾部不再是 key。
        """
        match = re.match(r"执行 (\S+)", instruction)
        return f"任务 {match.group(1)}" if match else "任务 ?"

    def subagent_tool_names(self) -> list[str]:
        return ["read_file"]

    async def execute_frozen(
        self,
        instruction: str,
        *,
        execution_id: str,
        label: str,
        origin: Any = None,
        agent: Any = None,
        runtime: Any = None,
        origin_message_id: str | None = None,
        workspace_scope: Any = None,
        on_activity: Any = None,
    ) -> SubagentExecutionResult:
        title = self._title(instruction)
        self.started.append(title)
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            if on_activity is not None:
                # 与真实子执行同一形状（契约 §7：只传白名单摘要，不传参数与输出）。
                await on_activity({"toolName": "read_file", "toolStatus": "ok", "iteration": 1})
                self.activities.append(title)
            if self.barrier is not None:
                self.barrier.set()
                await asyncio.wait_for(self.release.wait(), timeout=5)
            if title in self.fail_for:
                return SubagentExecutionResult(
                    execution_id=execution_id,
                    outcome="failed",
                    stop_reason="error",
                    final_content=f"{title} 失败",
                    error_code="provider_error",
                )
            return SubagentExecutionResult(
                execution_id=execution_id,
                outcome="succeeded",
                stop_reason="completed",
                final_content=f"{title} 的结论",
            )
        except asyncio.CancelledError:
            self.cancelled.append(title)
            raise
        finally:
            self.running -= 1
            self.finished.append(title)


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


class FakeSkills:
    def list_skills(self, *, filter_unavailable: bool = True) -> list[dict[str, Any]]:
        return []


class FakeBus:
    """只做一件事：把总线事件交给 WebUI 投影器，和真实 gateway 的一条链路同形。"""

    def __init__(self, projector: WebUIOutboundProjector) -> None:
        self._projector = projector
        self.published: list[TeamRunUpdateEvent] = []

    async def publish_event(self, event: Any, *, channel: str, chat_id: str) -> None:
        self.published.append(event)
        await self._projector.send(
            OutboundMessage(channel=channel, chat_id=chat_id, content="", event=event)
        )


class FakeTransport:
    """已认证 WebUI 连接：只收帧，帧就是前端唯一能看到的东西。"""

    def __init__(self, chat_id: str, *, connected: bool = True) -> None:
        self.chat_id = chat_id
        self.connected = connected
        self.frames: list[dict[str, Any]] = []

    def webui_subscribers(self, chat_id: str) -> tuple[Any, ...]:
        return ("connection",) if (self.connected and chat_id == self.chat_id) else ()

    async def send_payload(
        self,
        chat_id: str,
        payload: dict[str, Any],
        *,
        persistence: str,
        metadata: dict[str, Any] | None = None,
        turn_owner: str | None = None,
    ) -> None:
        self.frames.append(dict(payload))

    def events_of(self, event_type: str) -> list[dict[str, Any]]:
        return [item for item in self.frames if item.get("event") == event_type]


class TeamGateway:
    """一次团队聊天用到的全部真实部件，只有 Leader 与成员是替身。"""

    def __init__(self, workspace: Path, transport: FakeTransport) -> None:
        self.workspace = workspace
        self.transport = transport
        self.projector = WebUIOutboundProjector(  # pyright: ignore[reportArgumentType]
            transport,  # pyright: ignore[reportArgumentType]
            SimpleNamespace(hydration_events=lambda *_a: []),
            team_hydration=self._hydrate,
        )
        self.bus = FakeBus(self.projector)
        self.definition = TeamDefinition.from_dict(_fixture("definition.json"))
        self.snapshot = self._frozen_snapshot()

        self.db = TeamDatabase(workspace)
        self.db.init_schema()
        assert self.db.hold_writer() is True
        self.repository = TeamRepository(self.db)
        self.repository.save_team(
            definition=self.definition,
            snapshot=self.snapshot,
            expected_revision=None,
            request_id="req-e2e-seed",
        )
        self.service = TeamService(self.repository, FakeProfileDirectory(_profile_records()))
        self.executor = FakeSubagentManager(self.repository)
        self.host = SimpleNamespace(
            subagents=self.executor,
            context=SimpleNamespace(skills=FakeSkills()),
            runtime_resolver=SimpleNamespace(
                runtime=object(), resolve_preset=lambda _name: object()
            ),
            tool_names=["read_file", "team_dispatch"],
            bus=self.bus,
        )
        self.turns: TeamLeaderTurns = build_team_turns(
            service=self.service, host=self.host, workspace=workspace
        )
        self.controller = TeamRunController(
            service=self.service,
            scheduler=self.turns.scheduler,
            runtime_provider=lambda: object(),
            preset_resolver=self.host.runtime_resolver.resolve_preset,
            workspace=workspace,
        )

    async def _hydrate(self, chat_id: str) -> list[dict[str, Any]]:
        """重连补发：只回本会话**未终态**的运行快照（契约 §6）。"""
        page = self.repository.list_runs(chat_id)
        return [
            project_run_snapshot(run)
            for run in page["runs"]
            if run.status not in {"completed", "partial", "failed", "cancelled", "interrupted"}
        ]

    def _frozen_snapshot(self) -> TeamRevisionSnapshot:
        payload = _fixture("snapshot.json")
        leader = AgentProfileSnapshot.from_dict(payload["leader"])
        members = [
            TeamRevisionMember(
                member=self.definition.members[index],
                profile=AgentProfileSnapshot.from_dict(item["profile"]),
            )
            for index, item in enumerate(payload["members"])
        ]
        return TeamRevisionSnapshot(definition=self.definition, leader=leader, members=members)

    async def leader_turn(self, batch_id: str, tasks: list[TeamTaskInput]) -> dict[str, Any]:
        """跑一个 Leader 回合：装配 → team_dispatch → 收口。"""
        binding = TeamBinding(
            team_id=self.definition.id,
            team_revision=self.definition.revision,
            team_name=self.definition.name,
        )
        session = SimpleNamespace(
            key=SESSION_KEY, metadata={SESSION_TEAM_BINDING_KEY: binding.to_dict()}
        )
        message = SimpleNamespace(
            chat_id=CHAT_ID, metadata={"webui_turn_id": "turn-e2e", "message_id": "msg-e2e"}
        )
        context = await self.turns(message, session, "turn-e2e-internal")
        assert context is not None
        outcome = await context.dispatch_tool.execute(  # pyright: ignore[reportAttributeAccessIssue]
            batchId=batch_id,
            tasks=[item.to_dict() for item in tasks],
        )
        await context.turn_finalizer(True)
        return {"outcome": outcome, "context": context}

    async def reconnect(self) -> None:
        await self.projector.hydrate(CHAT_ID)

    @classmethod
    def after_restart(cls, workspace: Path, transport: FakeTransport) -> TeamGateway:
        """新进程视角：同一 workspace 上全新的连接句柄、执行器与服务。"""
        revived = cls.__new__(cls)
        revived.workspace = workspace
        revived.transport = transport
        revived.db = TeamDatabase(workspace)
        revived.repository = TeamRepository(revived.db)
        revived.definition = TeamDefinition.from_dict(_fixture("definition.json"))
        revived.service = TeamService(
            revived.repository, FakeProfileDirectory(_profile_records())
        )
        revived.executor = FakeSubagentManager(revived.repository)
        return revived

    def tasks_of(self, run_id: str) -> list[dict[str, Any]]:
        return self.service.get_run(run_id)["run"]["tasks"]

    def status_of(self, run_id: str) -> str:
        return str(self.service.get_run(run_id)["run"]["status"])


def _profile_records() -> dict[str, ProfileRecord]:
    snapshot = _fixture("snapshot.json")
    records = {
        str(snapshot["leader"]["id"]): ProfileRecord(
            id=str(snapshot["leader"]["id"]),
            name=str(snapshot["leader"]["name"]),
            prompt=str(snapshot["leader"].get("prompt") or ""),
            model_id=snapshot["leader"].get("model_id"),
            tools=AgentSelection(mode="include", entries=["read_file"]),
            skills=AgentSelection(mode="all", entries=[]),
            sub_agents=AgentSelection(mode="all", entries=[]),
            hidden=False,
            updated_at=str(snapshot["leader"]["updatedAt"]),
        )
    }
    for item in snapshot["members"]:
        profile = item["profile"]
        agent_id = str(profile["id"])
        records[agent_id] = ProfileRecord(
            id=agent_id,
            name=str(profile["name"]),
            prompt=str(profile.get("prompt") or ""),
            model_id=profile.get("model_id"),
            tools=AgentSelection(mode="include", entries=["read_file"]),
            skills=AgentSelection(mode="all", entries=[]),
            sub_agents=AgentSelection(mode="all", entries=[]),
            hidden=False,
            updated_at=str(profile["updatedAt"]),
        )
    return records


async def _wait_started(gateway: TeamGateway, count: int) -> None:
    """等到 ``count`` 个成员真的进入执行；不用 sleep 赌时序。"""
    for _ in range(500):
        if len(gateway.executor.started) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {count} members started, saw {gateway.executor.started}")


def _task(key: str, member_id: str, *, depends_on: list[str] | None = None,
          resource: str | None = None) -> TeamTaskInput:
    return TeamTaskInput(
        key=key,
        member_id=member_id,
        title=f"任务 {key}",
        instruction=f"执行 {key}",
        acceptance="给出结果",
        depends_on=list(depends_on or []),
        resource_keys=[resource] if resource else [],
    )


@pytest.fixture
def member_ids() -> list[str]:
    definition = TeamDefinition.from_dict(_fixture("definition.json"))
    return [member.member_id for member in definition.members]


@pytest.fixture
async def gateway(tmp_path: Path) -> TeamGateway:
    """一次完整链路：绑定 → Leader 回合 → 成员并行 → 帧投递 → 终态。"""
    transport = FakeTransport(CHAT_ID)
    return TeamGateway(tmp_path / "team-e2e", transport)


# --------------------------------------------------------------------- 全成功


async def test_end_to_end_team_flow(
    gateway: TeamGateway, member_ids: list[str]
) -> None:
    """Fake Leader 派两单并行 + 一个依赖任务；成员并行、依赖有序、只汇总一次。"""
    gateway.executor.block()
    tasks = [
        _task("a", member_ids[0], resource="file:a"),
        _task("b", member_ids[1], resource="file:b"),
        _task("c", member_ids[0], depends_on=["a"], resource="file:c"),
    ]
    dispatch = asyncio.create_task(gateway.leader_turn("batch-1", tasks))

    await _wait_started(gateway, 2)
    # 两个成员真的并行：A、B 同时在跑。
    assert sorted(gateway.executor.started) == ["任务 a", "任务 b"]
    assert gateway.executor.peak == 2
    gateway.executor.release.set()

    await asyncio.wait_for(dispatch, timeout=10)
    run_id = gateway.turns.scheduler.repository.list_runs(CHAT_ID)["runs"][0].run_id

    assert gateway.status_of(run_id) == "completed"
    assert [item["status"] for item in gateway.tasks_of(run_id)] == ["succeeded"] * 3

    # 依赖顺序：c 一定在 a 之后开始。
    order = [title for title in gateway.executor.started]
    assert gateway.executor.activities == ["任务 a", "任务 b", "任务 c"]
    actions = {item["title"]: item["lastAction"] for item in gateway.tasks_of(run_id)}
    assert actions["任务 a"] == "read_file ok #1", "工具活动摘要要能进卡片"
    assert order.index("任务 c") > order.index("任务 a")

    # 前端只看得到这两种帧（契约 §6）。
    assert gateway.transport.events_of("team_event"), "没有收到任何团队增量帧"
    types = {item["data"]["type"] for item in gateway.transport.events_of("team_event")}
    assert {"task.started", "task.finished", "run.finished"} <= types
    assert gateway.transport.events_of("team_snapshot") == []
    seqs = [item["data"]["seq"] for item in gateway.transport.events_of("team_event")]
    assert seqs == sorted(seqs), "seq 必须严格递增，客户端才能顺序应用"


# --------------------------------------------------------------------- partial


async def test_member_failure_yields_partial_and_blocks_dependents(
    gateway: TeamGateway, member_ids: list[str]
) -> None:
    """一轮失败 → 运行 partial；依赖它的任务 blocked，成功兄弟的结果保留。"""
    gateway.executor.fail_for.add("任务 b")
    tasks = [
        _task("a", member_ids[0], resource="file:a"),
        _task("b", member_ids[1], resource="file:b"),
        _task("c", member_ids[0], depends_on=["b"], resource="file:c"),
    ]
    await asyncio.wait_for(gateway.leader_turn("batch-1", tasks), timeout=10)
    run_id = gateway.turns.scheduler.repository.list_runs(CHAT_ID)["runs"][0].run_id

    statuses = {item["title"]: item["status"] for item in gateway.tasks_of(run_id)}
    assert statuses == {"任务 a": "succeeded", "任务 b": "failed", "任务 c": "blocked"}
    assert gateway.status_of(run_id) == "partial"

    # 成功兄弟的结论仍然可读，不因为同伴失败被抹掉（契约 §6）。
    succeeded = next(item for item in gateway.tasks_of(run_id) if item["title"] == "任务 a")
    assert succeeded["result"]["summary"] == "任务 a 的结论"


# ---------------------------------------------------------------------- 取消


async def test_cancel_run_is_idempotent_and_reports_real_status(
    gateway: TeamGateway, member_ids: list[str]
) -> None:
    """取消先回 cancelling、终态是 cancelled；再取消一次照实回终态。"""
    gateway.executor.block()
    dispatch = asyncio.create_task(
        gateway.leader_turn("batch-1", [_task("a", member_ids[0], resource="file:a")])
    )
    await _wait_started(gateway, 1)

    run_id = gateway.turns.scheduler.repository.list_runs(CHAT_ID)["runs"][0].run_id
    first = await gateway.controller.cancel_run(run_id, caller_chats=(CHAT_ID,))
    assert first["status"] == "cancelling"

    gateway.executor.release.set()
    await asyncio.wait_for(dispatch, timeout=10)

    assert gateway.status_of(run_id) == "cancelled"
    again = await gateway.controller.cancel_run(run_id, caller_chats=(CHAT_ID,))
    assert again == {"runId": run_id, "status": "cancelled"}
    assert "任务 a" in gateway.executor.cancelled


async def test_cancel_from_a_foreign_session_is_refused(
    gateway: TeamGateway, member_ids: list[str]
) -> None:
    """别的会话拿着 runId 也停不了这条运行（契约 §3/§5，验收 A18）。"""
    dispatch = asyncio.create_task(
        gateway.leader_turn("batch-1", [_task("a", member_ids[0], resource="file:a")])
    )
    await _wait_started(gateway, 1)
    run_id = gateway.turns.scheduler.repository.list_runs(CHAT_ID)["runs"][0].run_id

    with pytest.raises(CrossSessionForbidden):
        await gateway.controller.cancel_run(run_id, caller_chats=("chat-not-mine",))
    with pytest.raises(CrossSessionForbidden):
        await gateway.controller.cancel_run(run_id)
    assert gateway.status_of(run_id) == "running"

    await gateway.controller.cancel_run(run_id, caller_chats=(CHAT_ID,))
    await asyncio.wait_for(dispatch, timeout=10)


# ---------------------------------------------------------------------- 重连


async def test_reconnect_hydrates_open_runs_and_replays_events(
    gateway: TeamGateway, member_ids: list[str]
) -> None:
    """重连先收快照，再按 seq 补拉；重复/旧事件不把状态推回去（契约 §6）。"""
    gateway.executor.block()
    dispatch = asyncio.create_task(
        gateway.leader_turn("batch-1", [_task("a", member_ids[0], resource="file:a")])
    )
    await _wait_started(gateway, 1)
    run_id = gateway.turns.scheduler.repository.list_runs(CHAT_ID)["runs"][0].run_id

    # 重连补发：hydrate 直接把未终态运行的整份快照推给刚连上的连接。
    await gateway.reconnect()
    snapshots = gateway.transport.events_of("team_snapshot")
    assert len(snapshots) == 1
    assert snapshots[0]["data"]["runId"] == run_id
    assert snapshots[0]["data"]["lastSeq"] >= 1

    gateway.executor.release.set()
    await asyncio.wait_for(dispatch, timeout=10)

    page = gateway.service.run_events(run_id, after_seq=0, limit=200)
    seqs = [event["seq"] for event in page["events"]]
    assert seqs == list(range(1, len(seqs) + 1)), "补拉页必须从 seq=1 连续"
    assert page["lastSeq"] == seqs[-1]
    assert page["resetRequired"] is False
    # 已收到的旧事件再补拉一次也不会倒退状态。
    assert gateway.status_of(run_id) == "completed"


# ---------------------------------------------------------------------- 重启


async def test_restart_interrupts_open_run_without_repeating_work(tmp_path: Path) -> None:
    """进程重启：遗留 running 落 interrupted，成员不再被执行一次（契约 §6）。"""
    transport = FakeTransport(CHAT_ID)
    first = TeamGateway(tmp_path / "team-e2e", transport)
    first.executor.block()
    dispatch = asyncio.create_task(
        first.leader_turn("batch-1", [_task("a", "m-researcher", resource="file:a")])
    )
    await _wait_started(first, 1)
    run_id = first.turns.scheduler.repository.list_runs(CHAT_ID)["runs"][0].run_id

    # 模拟进程死掉：直接丢掉句柄，不收口、不发任何后续事件。
    dispatch.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dispatch
    first.repository.release_writer()

    # 新进程：全新连接句柄 + 全新执行器。
    restarted = TeamGateway.after_restart(first.workspace, transport)

    recovery = restarted.service.recover_after_restart()
    assert recovery["interruptedRuns"] == [run_id]
    assert restarted.repository.get_run(run_id).status == "interrupted"
    assert restarted.executor.started == [], "重启不得重放成员执行"

    # 重连补拉能拿到终态，UI 才有手动重试入口。
    assert restarted.service.get_run(run_id)["run"]["status"] == "interrupted"
    page = restarted.service.run_events(run_id, after_seq=0, limit=200)
    assert page["events"][-1]["type"] == "run.finished"
