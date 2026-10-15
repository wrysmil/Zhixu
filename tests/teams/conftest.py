"""Agent Team 域测试共享装置。

形状锚点是 ``tests/fixtures/teams/*.json``：Python 与 TS 侧（WU-08/10）共用同一组
文件，所以装置只负责**加载与深拷贝**，绝不在测试里改写文件内容。三个文件的分工：

- ``definition.json`` —— 一个 :class:`TeamDefinition` 线格式对象（版本字段 ``revision``）。
- ``snapshot.json`` —— 一个 :class:`TeamRevisionSnapshot` 线格式对象（版本取
  ``definition.revision``）。Leader 档案 id 为 ``leader``，成员档案 id 为
  ``researcher`` / ``writer``。
- ``event.json`` —— :class:`TeamEvent` 数组，覆盖契约 §6 的全部 9 个 ``type``；
  :class:`TeamRunSnapshot` 与 :class:`TeamTaskSnapshot` 的锚点取自其中
  ``run.finished`` / ``task.*`` 事件。

所有装置都是内存替身：``FakeProfileProvider`` 只返回上面 fixture 里的档案快照，
不接任何真实 provider、不发真实网络请求。autouse 装置把 HOME/USERPROFILE 指向
``tmp_path``，路径推导测试因此不可能碰到用户真实的 ``~/.nanobot/workspace``。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from nanobot.teams.database import TeamDatabase
from nanobot.teams.models import (
    AgentProfileSnapshot,
    TeamBinding,
    TeamDefinition,
    TeamRevisionMember,
    TeamRevisionSnapshot,
    TeamTaskSnapshot,
)
from nanobot.teams.repository import TeamRepository

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "teams"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _isolate_nanobot_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    yield


@pytest.fixture
def definition_payload() -> dict[str, Any]:
    return deepcopy(load_fixture("definition.json"))


@pytest.fixture
def team_payload(definition_payload: dict[str, Any]) -> dict[str, Any]:
    return definition_payload


@pytest.fixture
def team_definition(team_payload: dict[str, Any]) -> TeamDefinition:
    return TeamDefinition.from_dict(team_payload)


@pytest.fixture
def revision_snapshot_payload() -> dict[str, Any]:
    return deepcopy(load_fixture("snapshot.json"))


@pytest.fixture
def run_snapshot_payload() -> dict[str, Any]:
    events = load_fixture("event.json")
    finished = next(event for event in events if event["type"] == "run.finished")
    return deepcopy(finished["data"]["run"])


@pytest.fixture
def event_payloads() -> list[dict[str, Any]]:
    return deepcopy(load_fixture("event.json"))


@dataclass(frozen=True)
class FakeProfileProvider:
    """按 agentId 返回固定档案快照的测试替身。

    真实装配（WU-03/04）才读取 AgentStore 与 provider 配置；这里只保证
    ``TeamRevisionSnapshot`` 能在无真实 provider 的情况下被构造与断言。
    """

    profiles: Mapping[str, AgentProfileSnapshot]

    def profile(self, agent_id: str) -> AgentProfileSnapshot:
        try:
            return self.profiles[agent_id]
        except KeyError:
            raise AssertionError(f"unknown agent profile: {agent_id}") from None

    def revision_snapshot(self, definition: TeamDefinition) -> TeamRevisionSnapshot:
        return TeamRevisionSnapshot(
            definition=definition,
            leader=self.profile(definition.leader_agent_id),
            members=[
                TeamRevisionMember(member=member, profile=self.profile(member.agent_id))
                for member in definition.members
            ],
        )


@pytest.fixture
def fake_profiles(revision_snapshot_payload: dict[str, Any]) -> FakeProfileProvider:
    leader = AgentProfileSnapshot.from_dict(revision_snapshot_payload["leader"])
    members = {
        str(item["profile"]["id"]): AgentProfileSnapshot.from_dict(item["profile"])
        for item in revision_snapshot_payload["members"]
    }
    return FakeProfileProvider(profiles={leader.id: leader, **members})


# ----------------------------------------------------------- 存储层（WU-02）


@pytest.fixture
def team_workspace(tmp_path: Path) -> Path:
    """一个不存在的工作空间目录：读路径必须能在它缺席时工作且不把它建出来。"""
    return tmp_path / "team-workspace"


@pytest.fixture
def team_db(team_workspace: Path) -> TeamDatabase:
    """已建库的连接（写路径装置）；``team_db_uninitialized`` 才是未初始化状态。"""
    db = TeamDatabase(team_workspace)
    db.init_schema()
    return db


@pytest.fixture
def team_db_uninitialized(team_workspace: Path) -> TeamDatabase:
    return TeamDatabase(team_workspace)


@pytest.fixture
def repository(team_db: TeamDatabase, team_definition: TeamDefinition) -> TeamRepository:
    """预置一个已保存的团队，revision=1。"""
    repo = TeamRepository(team_db)
    snapshot = FakeProfileProvider(
        profiles=_profiles_from_snapshot(team_definition)
    ).revision_snapshot(team_definition)
    repo.save_team(
        definition=team_definition,
        snapshot=snapshot,
        expected_revision=None,
        request_id="req-seed",
    )
    return repo


@dataclass(frozen=True)
class CreatedTask:
    """存储层测试的任务把手：``run_id`` 不属于契约里的 TeamTaskSnapshot 线格式，
    所以不在领域类型上挂它，改由测试把手提供（plan Task 2 锚点用 ``running_task.run_id``）。"""

    run_id: str
    task_id: str
    snapshot: TeamTaskSnapshot


@pytest.fixture
def running_task(
    repository: TeamRepository, team_definition: TeamDefinition
) -> CreatedTask:
    """一个 running 团队运行里的单条 running 任务（plan Task 2 锚点装置）。"""
    binding = TeamBinding(
        team_id=team_definition.id,
        team_revision=team_definition.revision,
        team_name=team_definition.name,
    )
    snapshot = repository.run_binding_snapshot(binding)
    run = repository.create_run(
        binding=binding,
        chat_id="chat-1",
        session_key="session-1",
        turn_id="turn-1",
        internal_turn_id="internal-turn-1",
        snapshot=snapshot,
    )
    task = repository.create_task(
        run_id=run.run_id,
        member=team_definition.members[0],
        title="查阅资料",
        acceptance="给出来源",
        instruction="检索文档",
        status="running",
    )
    return CreatedTask(run_id=run.run_id, task_id=task.task_id, snapshot=task)


def _profiles_from_snapshot(definition: TeamDefinition) -> dict[str, AgentProfileSnapshot]:
    """从 fixture 取档案快照，保证存储层测试用的就是跨语言锚点里的同一份数据。"""
    payload = json.loads((FIXTURE_DIR / "snapshot.json").read_text(encoding="utf-8"))
    leader = AgentProfileSnapshot.from_dict(payload["leader"])
    profiles = {leader.id: leader}
    for item in payload["members"]:
        profile = AgentProfileSnapshot.from_dict(item["profile"])
        profiles[profile.id] = profile
    return {agent_id: profiles[agent_id] for agent_id in _definition_agent_ids(definition)}


def _definition_agent_ids(definition: TeamDefinition) -> list[str]:
    return [definition.leader_agent_id, *[member.agent_id for member in definition.members]]

