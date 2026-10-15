"""TeamService：gateway 注入口面，负责「保存前把引用校验干净」。

职责边界（契约 §2）：服务层持有 workspace、解析引用、盖章服务端字段；仓储层只负责
落盘原子性。因此**引用校验失败时一张表都不会写**，不会出现「团队建了一半」。

引用校验口径（契约 §1）：

- Leader 与全部成员档案必须存在且未隐藏；隐藏档案不能作为**新**选择项，已冻结的
  历史修订仍可读。
- ``subAgents`` 在 Team Leader 下取「团队成员 ∩ Leader 档案 ``resolve_selection`` 结果」，
  不越过档案自身的 include/exclude。被 Leader 排除的成员直接报错并指出档案。
- 模型 preset 的可用性在每次运行时重新解析（WU-04 的 ``ModelRuntimeResolver``）；
  本层只确认「档案还在、没被隐藏」，不读凭据、不退回默认模型。

同步方法是真实实现，``a_*`` 前缀是给 gateway 的短 ``to_thread`` 入口（plan Task 2
Step 3）：SQLite 读写不进事件循环，也不在持锁期间 await 网络。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from nanobot.agents.models import AgentSelection
from nanobot.agents.runtime import resolve_selection
from nanobot.teams.models import (
    SERVER_OWNED_DEFINITION_FIELDS,
    AgentProfileSnapshot,
    TeamDefinition,
    TeamRevisionMember,
    TeamRevisionSnapshot,
    TeamValidationError,
)
from nanobot.teams.repository import TeamRepository, new_id, utc_now_iso


class TeamDisabled(RuntimeError):  # noqa: N818  # 映射 503 TEAMS_DISABLED
    """``agents.teams.enabled=false``：禁止新运行，历史仍可读（plan §4 的回滚开关）。"""


class TeamArchived(RuntimeError):  # noqa: N818  # 传输层按契约 §4 映射为 TEAM_NOT_FOUND
    """团队已归档：活动运行可结束，但不再接受新运行。"""


class TeamReferenceError(ValueError):  # noqa: N818  # 映射 400 TEAM_INVALID
    """成员被 Leader 档案排除，或团队引用关系不成立。"""


class TeamProfileUnavailable(ValueError):  # noqa: N818  # 映射 409 TEAM_MEMBER_UNAVAILABLE
    """被引用的 Agent 档案缺失或已隐藏。"""


@dataclass(frozen=True)
class ProfileRecord:
    """服务层需要的最小档案视图；字段与 ``AgentProfileSnapshot`` 一一对应。

    刻意不包含 api key、provider 配置或父聊天内容——快照永远不该带上它们。
    """

    id: str
    name: str
    prompt: str = ""
    model_id: str | None = None
    tools: AgentSelection | None = None
    skills: AgentSelection | None = None
    sub_agents: AgentSelection | None = None
    hidden: bool = False
    updated_at: str = ""


class ProfileDirectory(Protocol):
    """档案来源。WU-05 用 AgentStore 适配它，测试用内存替身。"""

    def get(self, agent_id: str) -> ProfileRecord | None: ...


_DEFAULT_SELECTION = AgentSelection(mode="all")


def _snapshot_of(record: ProfileRecord) -> AgentProfileSnapshot:
    """把档案视图压成冻结快照（契约 §1：只冻结约束，不冻结凭据）。"""
    return AgentProfileSnapshot.from_dict(
        {
            "id": record.id,
            "name": record.name,
            "prompt": record.prompt,
            "modelId": record.model_id,
            "tools": (record.tools or _DEFAULT_SELECTION).to_dict(),
            "skills": (record.skills or _DEFAULT_SELECTION).to_dict(),
            "subAgents": (record.sub_agents or _DEFAULT_SELECTION).to_dict(),
            "updatedAt": record.updated_at or utc_now_iso(),
        }
    )


class TeamService:
    """一个 workspace 一个实例；由 gateway 注入，不通过模块全局读取 Loop。"""

    def __init__(
        self,
        repository: TeamRepository,
        profiles: ProfileDirectory,
        *,
        enabled: bool = True,
    ) -> None:
        self.repository = repository
        self.profiles = profiles
        self.enabled = enabled

    # ------------------------------------------------------------------ 写入

    def save_team(
        self,
        payload: Mapping[str, Any],
        *,
        expected_revision: int | None,
        request_id: str,
    ) -> dict[str, Any]:
        """创建或更新团队修订；引用校验通过后一次性落盘。"""
        data = dict(payload)
        if expected_revision is None:
            # 创建路径原样交给 from_create_payload：客户端伪造服务端字段要报错，
            # 不是悄悄丢掉，否则前端拼错的载荷会被当成合法请求。
            definition = TeamDefinition.from_create_payload(data, now=utc_now_iso())
        else:
            editable = {
                key: value
                for key, value in data.items()
                if key not in SERVER_OWNED_DEFINITION_FIELDS
            }
            team_id = editable.get("id")
            if not isinstance(team_id, str) or not team_id:
                raise TeamValidationError("team update requires an id")
            previous = self.repository.get_team(team_id)
            if previous.archived:
                raise TeamArchived(f"team {previous.id} is archived")
            definition = TeamDefinition.from_dict(
                {
                    **editable,
                    "revision": int(expected_revision) + 1,
                    "createdAt": previous.created_at,
                    "updatedAt": utc_now_iso(),
                }
            )
        snapshot = self.freeze(definition)
        return self.repository.save_team(
            definition=definition,
            snapshot=snapshot,
            expected_revision=expected_revision,
            request_id=request_id,
        )

    def archive_team(self, team_id: str, *, expected_revision: int, request_id: str) -> dict[str, Any]:
        return self.repository.set_archived(
            team_id, expected_revision=expected_revision, request_id=request_id
        )

    def clone_team(self, team_id: str, *, name: str, request_id: str) -> dict[str, Any]:
        source = self.repository.get_team(team_id)
        snapshot = self.freeze(source)
        return self.repository.clone_team(
            source,
            snapshot=snapshot,
            team_id=new_id("team"),
            name=name,
            request_id=request_id,
        )

    # ------------------------------------------------------------------ 冻结

    def freeze(self, definition: TeamDefinition) -> TeamRevisionSnapshot:
        """解析并冻结 Leader 与成员的档案约束。

        这一步同时是引用校验：任何缺失、隐藏或被 Leader 排除的档案都在这儿抛错，
        因此失败时数据库里还什么都没有。
        """
        leader = self._require_available(definition.leader_agent_id)
        allowed = self._leader_allowed_members(leader, definition)
        members: list[TeamRevisionMember] = []
        for member in definition.members:
            # 先确认档案真的存在：把「档案已删除」报成「被 Leader 策略排除」会把用户
            # 引向错误的修复方向（该重建档案，而不是去改 subAgents 选择）。
            record = self._require_available(member.agent_id)
            if member.agent_id not in allowed:
                raise TeamReferenceError(
                    f"member {member.agent_id!r} is not allowed by Leader profile "
                    f"{leader.id!r} subAgents selection"
                )
            members.append(TeamRevisionMember(member=member, profile=_snapshot_of(record)))
        return TeamRevisionSnapshot(
            definition=definition, leader=_snapshot_of(leader), members=members
        )

    def _require_available(self, agent_id: str) -> ProfileRecord:
        record = self.profiles.get(agent_id)
        if record is None:
            raise TeamProfileUnavailable(f"agent profile {agent_id!r} does not exist")
        if record.hidden:
            raise TeamProfileUnavailable(f"agent profile {agent_id!r} is hidden")
        return record

    def _leader_allowed_members(
        self, leader: ProfileRecord, definition: TeamDefinition
    ) -> set[str]:
        candidate_ids = [member.agent_id for member in definition.members]
        return resolve_selection(
            leader.sub_agents or _DEFAULT_SELECTION, candidate_ids
        )

    # ------------------------------------------------------------------ 读取

    def list_teams(self, *, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        return self.repository.list_teams(limit=limit, cursor=cursor)

    def get_team_detail(self, team_id: str, *, revision: int | None = None) -> dict[str, Any]:
        definition = self.repository.get_team(team_id)
        target = revision if revision is not None else definition.revision
        snapshot = self.repository.get_revision_snapshot(team_id, revision=target)
        return {"team": definition.to_dict(), "snapshot": snapshot.to_dict()}

    def teams_referencing_profile(self, agent_id: str) -> list[str]:
        """给 Agent 删除动作用：被未归档团队引用时应返回 409 AGENT_IN_USE_BY_TEAM。"""
        return self.repository.teams_referencing_profile(agent_id)

    def prepare_new_run(self, team_id: str, *, revision: int | None = None) -> TeamRevisionSnapshot:
        """新运行的准入检查：开关、归档与档案可用性；返回该修订的冻结快照。"""
        if not self.enabled:
            raise TeamDisabled("agents.teams.enabled is false")
        definition = self.repository.get_team(team_id)
        if definition.archived:
            raise TeamArchived(f"team {definition.id} is archived")
        target = revision if revision is not None else definition.revision
        snapshot = self.repository.get_revision_snapshot(team_id, revision=target)
        self._require_available(definition.leader_agent_id)
        for member in definition.members:
            self._require_available(member.agent_id)
        return snapshot

    # -------------------------------------------------------------- 异步入口

    async def a_save_team(
        self,
        payload: Mapping[str, Any],
        *,
        expected_revision: int | None,
        request_id: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.save_team, payload, expected_revision=expected_revision, request_id=request_id
        )

    async def a_list_teams(self, *, limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self.list_teams, limit=limit, cursor=cursor)

    async def a_get_team_detail(self, team_id: str, *, revision: int | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self.get_team_detail, team_id, revision=revision)

    async def a_archive_team(
        self, team_id: str, *, expected_revision: int, request_id: str
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.archive_team, team_id, expected_revision=expected_revision, request_id=request_id
        )

    async def a_clone_team(self, team_id: str, *, name: str, request_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.clone_team, team_id, name=name, request_id=request_id)

    async def a_prepare_new_run(
        self, team_id: str, *, revision: int | None = None
    ) -> TeamRevisionSnapshot:
        return await asyncio.to_thread(self.prepare_new_run, team_id, revision=revision)
