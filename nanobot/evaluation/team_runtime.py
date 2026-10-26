"""隔离团队运行时（契约 §2 / plan T04）。

团队目标必须与 Agent/Skill 走同一条路：隔离 worker、独立 workspace、
完整 Trace、真实证据。评估服务不再保留"团队专用旁路"。

三层职责：

- build_frozen_team：纯解析，把冻结快照变成可校验的团队定义；
- IsolatedTeamRuntime：在 attempt workspace 里重建团队栈并执行全部轮次；
- TeamRunOutcome：执行结果（状态、最终回答、错误、证据引用）。

隔离的硬边界：

- 团队库、成员档案、产物全部落在 attempt workspace，不碰用户日常团队库；
- 冻结之后源团队被改、被归档都不影响本次运行；
- 成员能力 = 冻结档案 ∩ 用例白名单 ∩ 评估支持集，任何一层都不放宽另一层。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

TEAM_EVENT_TYPES = ("team.dispatch", "member.started", "member.completed")

#: 隔离副本的服务端时间戳占位：内容可复现，不随运行时刻漂移。
_FROZEN_TIMESTAMP = "2026-01-01T00:00:00+00:00"


class TeamRuntimeError(RuntimeError):
    """团队运行时错误；code 是稳定的错误码。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class FrozenMember:
    """一个成员槽位与其冻结档案。"""

    member_id: str
    agent_id: str
    profile: Mapping[str, Any]

    @property
    def name(self) -> str:
        raw = self.profile.get("name")
        return str(raw) if isinstance(raw, str) and raw else self.agent_id


@dataclass(frozen=True)
class FrozenTeam:
    """冻结后的团队：定义、Leader 档案与成员档案。"""

    team_id: str
    revision: int
    name: str
    leader: Mapping[str, Any]
    members: tuple[FrozenMember, ...]
    #: 冻结的团队定义原文（隔离副本按它物化）。
    definition: Mapping[str, Any] = field(default_factory=dict)

    def member_ids(self) -> tuple[str, ...]:
        return tuple(item.member_id for item in self.members)

    def agent_ids(self) -> tuple[str, ...]:
        return tuple(item.agent_id for item in self.members)

    def capability(self) -> dict[str, Any]:
        """团队级能力摘要：写进 Trace，判分侧据此核对"成员是否真跑了"。"""
        return {
            "team_id": self.team_id,
            "revision": self.revision,
            "leader_agent_id": str(self.leader.get("id") or ""),
            "members": [
                {
                    "member_id": item.member_id,
                    "agent_id": item.agent_id,
                    "name": item.name,
                }
                for item in self.members
            ],
        }


def build_frozen_team(snapshot: Mapping[str, Any] | None) -> FrozenTeam:
    """解析冻结的团队快照。

    字段缺失一律报错——宁可入队失败，也不能拿半截团队去跑：缺成员档案的团队
    跑出来的"成功"没有任何证据支撑。
    """
    if not isinstance(snapshot, Mapping):
        raise TeamRuntimeError("TEAM_SNAPSHOT_MISSING")
    if str(snapshot.get("kind") or "") != "team":
        raise TeamRuntimeError("TEAM_SNAPSHOT_KIND_INVALID")
    team = snapshot.get("team")
    if not isinstance(team, Mapping):
        raise TeamRuntimeError("TEAM_SNAPSHOT_MISSING")
    definition = team.get("definition")
    if not isinstance(definition, Mapping):
        raise TeamRuntimeError("TEAM_DEFINITION_MISSING")
    revision = definition.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise TeamRuntimeError("TEAM_REVISION_INVALID")
    declared = team.get("revision")
    if isinstance(declared, int) and not isinstance(declared, bool) and declared != revision:
        raise TeamRuntimeError("TEAM_REVISION_MISMATCH")
    leader = team.get("leader")
    if not isinstance(leader, Mapping) or not leader.get("id"):
        raise TeamRuntimeError("TEAM_LEADER_MISSING")
    leader_agent_id = str(definition.get("leaderAgentId") or "")
    if leader_agent_id and str(leader.get("id")) != leader_agent_id:
        raise TeamRuntimeError("TEAM_LEADER_MISMATCH")
    raw_members = team.get("members")
    if not isinstance(raw_members, Sequence) or isinstance(raw_members, (str, bytes)):
        raise TeamRuntimeError("TEAM_MEMBERS_MISSING")
    members: list[FrozenMember] = []
    for item in raw_members:
        if not isinstance(item, Mapping):
            raise TeamRuntimeError("TEAM_MEMBER_INVALID")
        member = item.get("member")
        profile = item.get("profile")
        if not isinstance(member, Mapping) or not isinstance(profile, Mapping):
            raise TeamRuntimeError("TEAM_MEMBER_INVALID")
        member_id = str(member.get("memberId") or member.get("id") or "")
        agent_id = str(member.get("agentId") or "")
        if not member_id or not agent_id:
            raise TeamRuntimeError("TEAM_MEMBER_INVALID")
        members.append(
            FrozenMember(member_id=member_id, agent_id=agent_id, profile=dict(profile))
        )
    if not members:
        raise TeamRuntimeError("TEAM_MEMBERS_EMPTY")
    return FrozenTeam(
        team_id=str(snapshot.get("id") or ""),
        revision=int(revision),
        name=str(definition.get("name") or ""),
        leader=dict(leader),
        members=tuple(members),
        definition=dict(definition),
    )


@dataclass(frozen=True)
class TeamRunRequest:
    """一次团队执行所需的全部输入（全部来自冻结快照与用例，不含模型可控值）。"""

    attempt_id: str
    team: FrozenTeam
    turns: tuple[str, ...]
    workspace: Path
    timeout_s: int = 120
    max_iterations: int = 12
    allowed_tools: tuple[str, ...] = ()
    model_mode: str = "real"
    tool_mode: str = "fixture"


@dataclass
class TeamRunOutcome:
    """团队执行结果。"""

    status: str
    final_answer: str | None = None
    stop_reason: str | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    answer_ref: str | None = None


class TeamTurnRunner(Protocol):
    """跑一轮 Leader 回合的注入点。

    生产由 gateway 注入隔离的团队执行栈；测试注入替身，
    不触碰日常团队库。返回最终回答文本，异常按失败收口。
    """

    async def __call__(
        self, *, turn_index: int, prompt: str, request: TeamRunRequest
    ) -> str | None: ...


class TeamEventSink(Protocol):
    """Trace 事件出口；与 Agent 路径共用同一个采集器。"""

    def emit(
        self, event_type: str, *, payload: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> Any: ...


class IsolatedTeamRuntime:
    """在 attempt workspace 里执行冻结团队的全部轮次。

    顺序是刻意安排的：先把冻结的成员档案与团队定义落到 attempt workspace，
    再开始跑——否则第一轮就会去读用户日常团队库。
    """

    def __init__(
        self,
        *,
        request: TeamRunRequest,
        turn_runner: TeamTurnRunner,
        events: TeamEventSink | None = None,
    ) -> None:
        self._request = request
        self._turn_runner = turn_runner
        self._events = events

    async def execute(self) -> TeamRunOutcome:
        request = self._request
        if not request.turns:
            return TeamRunOutcome(
                status="failed",
                errors=[{"phase": "setup", "code": "TEAM_TURNS_EMPTY", "message": ""}],
            )
        try:
            self._materialize()
        except TeamRuntimeError as exc:
            return TeamRunOutcome(
                status="setup_error",
                errors=[{"phase": "setup", "code": exc.code, "message": str(exc)}],
            )
        answer: str | None = None
        errors: list[dict[str, Any]] = []
        for index, prompt in enumerate(request.turns, start=1):
            self._emit(
                "team.dispatch",
                payload={
                    "turn": index,
                    "team_id": request.team.team_id,
                    "team_revision": request.team.revision,
                    "capability": request.team.capability(),
                    "prompt_chars": len(prompt),
                },
            )
            try:
                answer = await self._turn_runner(
                    turn_index=index, prompt=prompt, request=request
                )
            except Exception as exc:  # noqa: BLE001 - 回合异常按执行失败收口
                errors.append(
                    {"phase": "execution", "code": "TEAM_TURN_FAILED", "message": str(exc)}
                )
                return TeamRunOutcome(
                    status="failed",
                    final_answer=answer,
                    stop_reason="team_turn_failed",
                    errors=errors,
                )
        if answer is None:
            return TeamRunOutcome(
                status="failed",
                stop_reason="team_no_answer",
                errors=errors
                or [{"phase": "execution", "code": "TEAM_NO_ANSWER", "message": ""}],
            )
        return TeamRunOutcome(status="succeeded", final_answer=answer, errors=errors)

    # ------------------------------------------------------------------ 内部

    def _emit(self, event_type: str, *, payload: Mapping[str, Any] | None = None) -> None:
        if self._events is None:
            return
        self._events.emit(event_type, payload=dict(payload or {}))

    def _materialize(self) -> None:
        """把冻结的团队与成员档案写进 attempt workspace。"""
        workspace = Path(self._request.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        self._materialize_profiles(workspace)
        self._materialize_team(workspace)

    def _materialize_profiles(self, workspace: Path) -> None:
        from nanobot.agents.models import AgentProfile, AgentSelection
        from nanobot.agents.store import AgentStore

        store = AgentStore(workspace)
        profiles = [
            self._request.team.leader,
            *(item.profile for item in self._request.team.members),
        ]
        for raw in profiles:
            agent_id = str(raw.get("id") or "")
            if not agent_id:
                raise TeamRuntimeError("TEAM_PROFILE_ID_MISSING")
            profile = AgentProfile(
                id=agent_id,
                name=str(raw.get("name") or agent_id),
                prompt=str(raw.get("prompt") or ""),
                model_id=(
                    str(raw.get("modelId"))
                    if isinstance(raw.get("modelId"), str) and raw.get("modelId")
                    else None
                ),
                tools=AgentSelection.from_dict(raw.get("tools")),
                skills=AgentSelection.from_dict(raw.get("skills")),
                sub_agents=AgentSelection.from_dict(raw.get("subAgents")),
                updated_at=str(raw.get("updatedAt") or ""),
            )
            store.save_profile(profile)

    def _materialize_team(self, workspace: Path) -> None:
        from nanobot.teams.database import TeamDatabase
        from nanobot.teams.repository import TeamRepository

        database = TeamDatabase(workspace)
        database.ensure_schema()
        repository = TeamRepository(database)
        team_id = self._request.team.team_id
        try:
            repository.get_team(team_id)
        except Exception:  # noqa: BLE001 - 首次运行时本来就没有团队
            pass
        else:
            return
        repository.save_team(
            definition=self._team_definition(),
            snapshot=self._team_snapshot(),
            expected_revision=None,
            request_id=f"eval-{self._request.attempt_id}",
        )

    def _team_definition(self) -> Any:
        """隔离副本的定义。

        修订号必须重置为 1：隔离副本是新写入的一条记录，
        冻结的原始修订号仍记在 Trace 的 capability 里。
        """
        from nanobot.teams.models import TeamDefinition

        team = self._request.team
        payload: dict[str, Any] = dict(team.definition)
        if not payload:
            payload = {
                "id": team.team_id,
                "name": team.name or team.team_id,
                "leaderAgentId": str(team.leader.get("id") or ""),
                "members": [
                    {
                        "memberId": item.member_id,
                        "agentId": item.agent_id,
                        "role": str(item.profile.get("role") or item.member_id),
                    }
                    for item in team.members
                ],
            }
        payload["revision"] = 1
        payload.setdefault("createdAt", _FROZEN_TIMESTAMP)
        payload.setdefault("updatedAt", _FROZEN_TIMESTAMP)
        return TeamDefinition.from_dict(payload)

    def _member_payload(self, item: FrozenMember) -> dict[str, Any]:
        """成员槽位优先用冻结定义里的原文，避免两处描述漂移。"""
        for raw in self._request.team.definition.get("members") or []:
            if isinstance(raw, Mapping) and str(raw.get("memberId") or "") == item.member_id:
                return dict(raw)
        return {
            "memberId": item.member_id,
            "agentId": item.agent_id,
            "role": str(item.profile.get("role") or item.member_id),
        }

    def _team_snapshot(self) -> Any:
        from nanobot.teams.models import (
            AgentProfileSnapshot,
            TeamMember,
            TeamRevisionMember,
            TeamRevisionSnapshot,
        )

        definition = self._team_definition()
        leader = AgentProfileSnapshot.from_dict(dict(self._request.team.leader))
        members = [
            TeamRevisionMember(
                member=TeamMember.from_dict(self._member_payload(item)),
                profile=AgentProfileSnapshot.from_dict(dict(item.profile)),
            )
            for item in self._request.team.members
        ]
        return TeamRevisionSnapshot(
            definition=definition, leader=leader, members=members
        )
