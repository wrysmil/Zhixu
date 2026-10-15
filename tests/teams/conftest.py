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

from nanobot.teams.models import (
    AgentProfileSnapshot,
    TeamDefinition,
    TeamRevisionMember,
    TeamRevisionSnapshot,
)

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
