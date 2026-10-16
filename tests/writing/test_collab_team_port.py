"""WU-07 / plan Task 8：``TeamPort`` 生产实现（内置团队播种、限额强制、起跑、取消、事件桥接）。

覆盖四条关键不变量：

1. **限额在发起前真正强制**（契约 §9：8 任务 / 2 轮派工 / 2 并发 / 300 秒成员超时），
   违反时在**任何 run 落库之前**报错；
2. ``start_batch`` 返回 ``run_id`` 且**不阻塞**（真正执行在后台任务里跑完并收口）；
3. 冻结的 Skill 正文只进运行期上下文，绝不落库（协作批次与 Team 侧都不含正文）；
4. ``on_team_event`` 不在事件循环上阻塞（端口桥接层用 ``asyncio.to_thread``）。
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.agent.subagent import SubagentManager, current_frozen_skills, frozen_skills_context
from nanobot.agents.catalog import FACTORY_PROFILES
from nanobot.teams.database import TeamDatabase
from nanobot.teams.models import TeamBinding, TeamDefinition, TeamLimits, TeamMember
from nanobot.teams.repository import TeamRepository
from nanobot.teams.service import ProfileRecord, TeamService
from nanobot.teams.turn import build_team_turns
from nanobot.webui.session_identity import WEBUI_SESSION_STORAGE_PREFIX
from nanobot.writing.builtin import WRITING_TEAM_LOGICAL_ID, BuiltinAssetRegistry
from nanobot.writing.collab.envelope import EnvelopeArtifactSink
from nanobot.writing.collab.recipe import (
    ARCHITECT_ROLE,
    AUTHOR_ROLE,
    EDITOR_ROLE,
    RESEARCHER_ROLE,
    REVIEWER_ROLE,
    WRITING_SKILLS,
)
from nanobot.writing.collab.repository import CollabRepository
from nanobot.writing.collab.runner import CollabRunner, StartCollabRequest
from nanobot.writing.collab.team_port import (
    COLLAB_LIMITS,
    TeamPortLimitError,
    TeamWritingPort,
)
from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import snapshot_digest
from nanobot.writing.repository import ArticleRepository

# 契约 §9 的固定限额（也是写入 TeamDefinition.limits 的那一份）。
SECRET_SKILL_BODY = "技能正文-机密-不可落库"

CONTRACT_LIMITS = {
    "max_concurrency": 2,
    "max_tasks": 8,
    "max_dispatch_rounds": 2,
    "task_timeout_seconds": 300,
    "run_timeout_seconds": 900,
}


# ---- 成员替身：不做 LLM，只按角色吐 envelope 文本 -------------------------------


@dataclass
class _FakeSubagents:
    scripted: dict[str, list[str]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def subagent_tool_names(self) -> list[str]:
        return ["read_file", "list_dir", "grep", "find_files"]

    async def execute_frozen(self, task: str, **kwargs: Any) -> Any:
        label = str(kwargs.get("label") or "")
        member_id = label.split(":", 1)[0]
        self.calls.append(
            {
                "member_id": member_id,
                "task": task,
                "agent_id": getattr(kwargs.get("agent"), "id", ""),
                "thread": threading.get_ident(),
                # 运行期上下文里的冻结正文：端口必须在 dispatch 之前挂上。
                "frozen": current_frozen_skills(),
            }
        )
        queue = self.scripted.get(member_id) or self.scripted.get("*") or []
        content = queue.pop(0) if queue else ""
        return SimpleNamespace(
            outcome="succeeded" if content else "failed",
            final_content=content,
            stop_reason="completed",
            usage=None,
            error_code=None if content else "member_failed",
        )


class _FakeSkills:
    def list_skills(self, *, filter_unavailable: bool = True) -> list[dict[str, Any]]:
        return [{"name": name, "source": "builtin", "path": f"skills/{name}.md"} for name in WRITING_SKILLS]


@dataclass
class _FakeHost:
    subagents: _FakeSubagents
    context: Any = field(default_factory=lambda: SimpleNamespace(skills=_FakeSkills()))
    runtime_resolver: Any = field(
        default_factory=lambda: SimpleNamespace(runtime="runtime", resolve_preset=lambda _name: None)
    )
    tool_names: Sequence[str] = ("read_file", "list_dir", "team_dispatch")
    bus: Any = None


class _FactoryDirectory:
    """按出厂档案返回 ``ProfileRecord``（生产由 AgentStore 适配）。"""

    def __init__(self) -> None:
        self._profiles = {profile.id: profile for profile in FACTORY_PROFILES}

    def get(self, agent_id: str) -> ProfileRecord | None:
        profile = self._profiles.get(agent_id)
        if profile is None:
            return None
        return ProfileRecord(
            id=profile.id,
            name=profile.name,
            prompt=profile.prompt,
            model_id=None,
            tools=profile.tools,
            skills=profile.skills,
            sub_agents=profile.sub_agents,
            hidden=False,
            updated_at="2026-10-17T00:00:00Z",
        )


@dataclass
class _FakeSessions:
    sessions: dict[str, Any] = field(default_factory=dict)
    saved: list[str] = field(default_factory=list)

    def load(self, key: str) -> Any:
        return self.sessions.get(key)

    def save(self, session: Any, *, fsync: bool = False) -> None:
        self.saved.append(session.key)


def _outline_wire(*, text: str = "第一节正文") -> str:
    payload = {
        "kind": "outline",
        "roleId": ARCHITECT_ROLE,
        "summary": "三段式提纲",
        "sections": [{"id": "s1", "title": "引子", "text": text}],
        "sourceIds": [],
        "gaps": [],
    }
    return "思路说明。\n```writing-envelope\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def _frozen(role_skill: str = "article-outline", *, body: str = SECRET_SKILL_BODY) -> dict[str, Any]:
    return {
        "skills": (
            {
                "name": role_skill,
                "path": f"skills/{role_skill}.md",
                "sha256": "a" * 8,
                "body": body,
            },
        ),
        "model_id": "model-x",
        "source_scope": {},
    }


def _preparer(
    *,
    recipe: Any = None,
    article_snapshot: Mapping[str, Any] | None = None,
    material_ids: Sequence[str] = (),
    collaboration_id: str = "",
    workspace_key: str = "",
    article_id: str = "",
    batch_id: str = "",
) -> dict[str, Any]:
    return _frozen()


# ---- 装置 --------------------------------------------------------------------


@dataclass
class _Harness:
    workspace: Path
    writing_db: WritingDatabase
    collab: CollabRepository
    articles: ArticleRepository
    team_db: TeamDatabase
    teams: TeamRepository
    service: TeamService
    registry: BuiltinAssetRegistry
    sessions: _FakeSessions
    subagents: _FakeSubagents
    host: _FakeHost
    port: TeamWritingPort
    runner: CollabRunner
    article: dict[str, Any]
    published: list[tuple[str, dict[str, Any]]]

    def request(self, *, action: str = "outline", **kwargs: Any) -> StartCollabRequest:
        return StartCollabRequest(
            client_request_id=kwargs.pop("request_id", "req-start-1"),
            article_id=str(self.article["id"]),
            chat_id="chat-1",
            action=action,
            base_revision=int(self.article["revision"]),
            snapshot_digest=snapshot_digest(self.article),
            **kwargs,
        )

    async def drain(self) -> None:
        await self.port.drain()
        await self.runner.drain()


def _harness(
    tmp_path: Path,
    *,
    scripted: dict[str, list[str]] | None = None,
    session_key_present: bool = True,
) -> _Harness:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    writing_db = WritingDatabase(workspace)
    collab = CollabRepository(writing_db)
    articles = ArticleRepository(writing_db)
    created = articles.create_article(
        "req-article-1", {"title": "标题", "body": "原有正文", "materials": []}
    )
    article = dict(created["article"])

    team_db = TeamDatabase(workspace)
    team_db.init_schema()
    teams = TeamRepository(team_db)
    registry = BuiltinAssetRegistry(writing_db)
    service = TeamService(
        teams, _FactoryDirectory(), enabled=True, builtin_registry=registry
    )

    subagents = _FakeSubagents(scripted=dict(scripted or {ARCHITECT_ROLE: [_outline_wire()]}))
    host = _FakeHost(subagents=subagents)
    sessions = _FakeSessions()
    if session_key_present:
        sessions.sessions[f"{WEBUI_SESSION_STORAGE_PREFIX}chat-1"] = SimpleNamespace(
            key=f"{WEBUI_SESSION_STORAGE_PREFIX}chat-1", metadata={}
        )

    port = TeamWritingPort(
        service=service,
        workspace=workspace,
        host=host,
        registry=registry,
        session_store=sessions,
    )
    sink = EnvelopeArtifactSink(
        repository=collab,
        teams=teams,
        articles=articles,
        workspace_key=writing_db.workspace_key,
    )
    published: list[tuple[str, dict[str, Any]]] = []

    def _publish(name: str, body: Mapping[str, Any]) -> None:
        published.append((name, dict(body)))

    runner = CollabRunner(
        repository=collab,
        workspace_key=writing_db.workspace_key,
        articles=articles,
        team=port,
        artifacts=sink,
        recipes=None,
        preparer=_preparer,
        publisher=_publish,
        logger=SimpleNamespace(
            bind=lambda **_fields: SimpleNamespace(
                info=lambda *_a: None,
                warning=lambda *_a: None,
                debug=lambda *_a: None,
                exception=lambda *_a: None,
            )
        ),
        converge=False,
    )
    port.set_event_sink(runner.on_team_event)
    return _Harness(
        workspace=workspace,
        writing_db=writing_db,
        collab=collab,
        articles=articles,
        team_db=team_db,
        teams=teams,
        service=service,
        registry=registry,
        sessions=sessions,
        subagents=subagents,
        host=host,
        port=port,
        runner=runner,
        article=article,
        published=published,
    )


def _run_tasks(harness: _Harness, run_id: str) -> list[Any]:
    run = harness.teams.get_run(run_id)
    return list(run.tasks)


async def _planned_tasks(harness: _Harness, run_id: str, *, count: int) -> list[Any]:
    """等后台 ``_drive`` 把整批任务派进运行再读任务表。

    ``start_batch`` 用 ``asyncio.create_task`` 起 ``_drive``，后者一次性
    ``dispatch`` 全部 planned 任务；调用方紧接着读运行快照会与派工竞争。
    """
    for _ in range(400):
        tasks = _run_tasks(harness, run_id)
        if len(tasks) >= count:
            return tasks
        await asyncio.sleep(0.005)
    return _run_tasks(harness, run_id)


# ---- 内置团队播种与限额 -------------------------------------------------------


def test_collab_limits_match_contract_section_9() -> None:
    assert COLLAB_LIMITS == CONTRACT_LIMITS


def test_builtin_team_is_seeded_with_contract_limits(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    definition = harness.port.resolve_team()

    assert definition.leader_agent_id == EDITOR_ROLE
    assert {member.member_id for member in definition.members} == {
        RESEARCHER_ROLE,
        ARCHITECT_ROLE,
        AUTHOR_ROLE,
        REVIEWER_ROLE,
    }
    assert definition.limits.to_dict() == {
        "maxConcurrency": 2,
        "maxTasks": 8,
        "maxDispatchRounds": 2,
        "taskTimeoutSeconds": 300,
        "runTimeoutSeconds": 900,
    }
    # 幂等：第二次解析不再新建团队，物理 ID 固定。
    again = harness.port.resolve_team()
    assert again.id == definition.id
    assert harness.registry.physical_id("team", WRITING_TEAM_LOGICAL_ID) == definition.id
    assert [item["id"] for item in harness.teams.list_teams()["teams"]] == [definition.id]


def test_user_team_named_writing_team_is_not_overwritten(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    user_definition = TeamDefinition(
        id=WRITING_TEAM_LOGICAL_ID,
        revision=1,
        name="作者自己的同名团队",
        description="",
        instructions="",
        leader_agent_id=EDITOR_ROLE,
        members=[TeamMember(member_id=AUTHOR_ROLE, agent_id=AUTHOR_ROLE, role=AUTHOR_ROLE)],
        limits=TeamLimits(),
        archived=False,
        created_at="2026-10-17T00:00:00Z",
        updated_at="2026-10-17T00:00:00Z",
    )
    harness.teams.save_team(
        definition=user_definition,
        snapshot=harness.service.freeze(user_definition),
        expected_revision=None,
        request_id="user-team",
    )

    definition = harness.port.resolve_team()

    assert definition.id != WRITING_TEAM_LOGICAL_ID
    assert definition.id.startswith(f"{WRITING_TEAM_LOGICAL_ID}-builtin")
    assert harness.teams.get_team(WRITING_TEAM_LOGICAL_ID).name == "作者自己的同名团队"


def test_limits_are_enforced_before_any_run_starts(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    definition = harness.port.resolve_team()

    with pytest.raises(TeamPortLimitError) as too_many_tasks:
        harness.port.enforce_limits(definition=definition, member_tasks=9, rounds=1)
    assert "maxMemberTasksPerBatch=8" in str(too_many_tasks.value)

    with pytest.raises(TeamPortLimitError) as too_many_rounds:
        harness.port.enforce_limits(definition=definition, member_tasks=1, rounds=3)
    assert "maxAutoDispatchRounds=2" in str(too_many_rounds.value)

    loose = TeamDefinition.from_dict(
        {
            **definition.to_dict(),
            "limits": TeamLimits(
                max_tasks=64, max_dispatch_rounds=8, max_concurrency=8
            ).to_dict(),
        }
    )
    with pytest.raises(TeamPortLimitError) as unbounded:
        harness.port.enforce_limits(definition=loose, member_tasks=1, rounds=1)
    assert "maxTasks=64" in str(unbounded.value)


async def test_start_batch_refuses_before_creating_a_run(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    definition = harness.port.resolve_team()
    loosened = TeamDefinition.from_dict(
        {
            **definition.to_dict(),
            "revision": 2,
            "limits": TeamLimits(max_tasks=64).to_dict(),
        }
    )
    harness.teams.save_team(
        definition=loosened,
        snapshot=harness.service.freeze(loosened),
        expected_revision=1,
        request_id="loosen-limits",
    )

    with pytest.raises(TeamPortLimitError):
        await harness.port.start_batch(
            collaboration_id="collab-1",
            batch_id="batch-1",
            attempt=1,
            action="outline",
            instruction="",
            session_key=f"{WEBUI_SESSION_STORAGE_PREFIX}chat-1",
            chat_id="chat-1",
            frozen=_frozen(),
            selection=None,
            material_ids=(),
        )
    assert harness.teams.list_runs("chat-1")["runs"] == []


# ---- 起跑 / 绑定 / 收口 -------------------------------------------------------


async def test_start_batch_returns_run_id_and_binds_session(tmp_path: Path) -> None:
    harness = _harness(tmp_path)

    handle = await harness.runner.start(harness.request(action="outline"))

    run_id = harness.port.last_run_id()
    assert run_id
    assert harness.teams.get_run(run_id).binding.team_id != ""
    session = harness.sessions.sessions[f"{WEBUI_SESSION_STORAGE_PREFIX}chat-1"]
    assert session.metadata["team_binding"]["teamId"] == harness.teams.get_run(run_id).binding.team_id
    assert session.metadata["team_binding"]["teamRevision"] == 1
    assert harness.sessions.saved == [session.key]

    await harness.drain()

    collaboration = harness.collab.get_collaboration(handle.collaboration_id)
    # 提纲交付 → 交接 → 批次结束、协作进入 waiting_author（与 runner 的收口一致）。
    assert collaboration.status == "waiting_author"
    handoffs = harness.collab.list_handoffs(handle.collaboration_id)
    assert [item.kind for item in handoffs] == ["outline_confirm"]
    artifacts = harness.collab.list_artifacts(handle.collaboration_id)
    assert len(artifacts) == 1
    stored = harness.collab.get_artifact(next(iter(artifacts)))
    assert stored.content["body"] == "第一节正文"
    assert [name for name, _ in harness.published] == ["writing_artifact", "writing_handoff"]


async def test_batch_plan_follows_recipe_roles_and_stays_in_budget(tmp_path: Path) -> None:
    harness = _harness(
        tmp_path,
        scripted={
            RESEARCHER_ROLE: [_outline_wire(text="资料")],
            ARCHITECT_ROLE: [_outline_wire(text="提纲")],
            AUTHOR_ROLE: [_outline_wire(text="初稿")],
            REVIEWER_ROLE: [_outline_wire(text="审校")],
        },
    )

    await harness.runner.start(harness.request(action="start_full"))
    run_id = harness.port.last_run_id()
    tasks = await _planned_tasks(harness, run_id, count=4)

    assert 0 < len(tasks) <= 8
    assert {task.member_id for task in tasks} == {
        RESEARCHER_ROLE,
        ARCHITECT_ROLE,
        AUTHOR_ROLE,
        REVIEWER_ROLE,
    }
    await harness.drain()
    # 提纲一交付就交接给作者：批次结束，后续任务被停掉（waiting_author 语义）。
    run = harness.teams.get_run(run_id)
    assert {task.status for task in run.tasks} <= {"succeeded", "cancelled"}
    assert not harness.port.pending_runs()


async def test_ideate_uses_the_lead_role_envelope(tmp_path: Path) -> None:
    harness = _harness(
        tmp_path,
        scripted={
            "*": [
                "```writing-envelope\n"
                + json.dumps(
                    {
                        "kind": "brief",
                        "roleId": EDITOR_ROLE,
                        "summary": "三个角度",
                        "sections": [{"id": "s1", "text": "角度一"}],
                    },
                    ensure_ascii=False,
                )
                + "\n```"
            ]
        },
    )

    await harness.runner.start(harness.request(action="ideate"))
    run_id = harness.port.last_run_id()
    tasks = await _planned_tasks(harness, run_id, count=1)
    assert len(tasks) == 1
    # 配方里 ideate 没有成员角色（brief 由主编产出），因此交付物要求记的是主编角色。
    # 断言 `acceptance` 而不是 `instruction`：`TeamTaskSnapshot` 只暴露 title/acceptance。
    assert EDITOR_ROLE in tasks[0].acceptance
    assert tasks[0].member_id in {
        item.member_id for item in harness.port.resolve_team().members
    }
    await harness.drain()
    artifacts = harness.collab.list_artifacts(
        harness.collab.get_by_article(str(harness.article["id"])).id
    )
    assert len(artifacts) == 1
    stored = harness.collab.get_artifact(next(iter(artifacts)))
    assert (stored.kind, stored.role_id) == ("brief", EDITOR_ROLE)


async def test_cancel_run_forwards_to_the_scheduler(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    harness.subagents.scripted[ARCHITECT_ROLE] = []
    harness.subagents.scripted["*"] = ["无 envelope"]

    await harness.runner.start(harness.request(action="outline"))
    run_id = harness.port.last_run_id()

    snapshot = await harness.port.cancel_run(run_id)

    assert snapshot is not None
    assert snapshot.status in {"cancelling", "cancelled"}
    await harness.drain()


# ---- 冻结正文只进运行期上下文 --------------------------------------------------


async def test_frozen_skill_body_never_reaches_persistence(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    secret = SECRET_SKILL_BODY

    with frozen_skills_context((("article-outline", secret),)):
        assert current_frozen_skills() == (("article-outline", secret),)
        prompt = SubagentManager._build_subagent_prompt(  # noqa: SLF001 - 直击最小接入点
            SimpleNamespace(workspace=harness.workspace, disabled_skills=set()),
            workspace=harness.workspace,
            agent=None,
        )
        assert secret in prompt

    assert current_frozen_skills() == ()

    await harness.runner.start(harness.request(action="outline"))
    run_id = harness.port.last_run_id()
    batch = harness.collab.list_batches(
        harness.collab.get_by_article(str(harness.article["id"])).id
    )[0]

    # 协作侧落库的 frozen 只有 name/path/sha256。
    assert "body" not in json.dumps(batch.frozen, ensure_ascii=False)
    assert secret not in json.dumps(batch.frozen, ensure_ascii=False)
    await harness.drain()

    # 成员执行时正文确实在运行期上下文里……
    assert harness.subagents.calls
    assert all(call["frozen"] == (("article-outline", secret),) for call in harness.subagents.calls)
    # ……但 Team 侧落库的任务指令里没有正文。
    for call in harness.subagents.calls:
        assert secret not in call["task"]
    run = harness.teams.get_run(run_id)
    for task in run.tasks:
        assert secret not in task.acceptance


# ---- on_team_event 不在事件循环上阻塞 ------------------------------------------


async def test_team_event_bridge_stays_on_the_loop_and_yields(tmp_path: Path) -> None:
    """收口桥接必须在**事件循环线程**上同步调用 sink，且两条事件之间让出循环。

    为什么不能像代码审查建议的那样丢进 ``asyncio.to_thread``：冻结的收口路径会在提纲
    交付时走到 ``CollabRunner._release_handle``，那里用 ``asyncio.create_task`` 去停团队
    运行 —— 在没有运行中事件循环的线程上会直接 ``RuntimeError``。所以隔离口径定为
    「同步短事务（单条 SQLite 读写在毫秒级）+ 每条之间 ``await asyncio.sleep(0)``」，
    本用例把这两点都锁死：线程必须是 loop 线程，事件之间必须有真实推进。
    """
    harness = _harness(tmp_path)
    loop_thread = threading.get_ident()
    seen: list[int] = []
    observed: list[str] = []
    ticks = 0

    def _sink(event: Any) -> None:
        seen.append(threading.get_ident())
        observed.append(str(getattr(event, "run_id", "")))

    harness.port.set_event_sink(_sink)

    async def _ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0)
            ticks += 1

    ticker = asyncio.create_task(_ticker())
    try:
        await harness.port.observe_team_events(
            [SimpleNamespace(run_id=f"run-{index}") for index in range(4)]
        )
    finally:
        ticker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await ticker

    # 逐条、按序交付。
    assert observed == [f"run-{index}" for index in range(4)]
    assert seen and all(thread == loop_thread for thread in seen)
    # 让出循环：观察 4 条事件期间 ticker 有真实推进（不是被独占）。
    assert ticks > 0


async def test_observers_are_wired_into_the_scheduler(tmp_path: Path) -> None:
    """调度器必须把事件交给协作桥接（否则 CollabRunner 永远收不到收口事件）。"""
    harness = _harness(tmp_path)
    turns = build_team_turns(service=harness.service, host=harness.host, workspace=harness.workspace)

    observed: list[Any] = []

    async def _observer(events: Sequence[Any]) -> None:
        observed.extend(events)

    definition = harness.port.resolve_team()
    turns.scheduler.observe(_observer)
    handle = await turns.scheduler.start_run(
        binding=TeamBinding(
            team_id=definition.id,
            team_revision=definition.revision,
            team_name=definition.name,
        ),
        chat_id="chat-observe",
        session_key=f"{WEBUI_SESSION_STORAGE_PREFIX}chat-observe",
        turn_id="turn-observe",
        internal_turn_id="turn-observe",
        snapshot=harness.service.prepare_new_run(definition.id),
    )
    await handle.finalize(delivered=True)

    assert observed, "调度器没有把事件交给观察者"
    assert {event.type for event in observed} >= {"run.started", "run.finished"}
