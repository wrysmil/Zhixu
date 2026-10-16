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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Protocol

from nanobot.agents.models import AgentSelection
from nanobot.agents.runtime import resolve_selection
from nanobot.teams.database import StorageUnavailable
from nanobot.teams.events import project_run_snapshot
from nanobot.teams.models import (
    SERVER_OWNED_DEFINITION_FIELDS,
    AgentProfileSnapshot,
    TeamDefinition,
    TeamRevisionMember,
    TeamRevisionSnapshot,
    TeamValidationError,
)
from nanobot.teams.repository import DEFAULT_EVENT_LIMIT, TeamRepository, new_id, utc_now_iso

if TYPE_CHECKING:  # pragma: no cover - 只在类型检查期解析
    from nanobot.writing.builtin import BuiltinAssetRegistry

# 内置资产类别；与契约 §7 的清单口径一致。
_BUILTIN_TEAM_KIND = "team"


def _builtin_team_logical_ids() -> tuple[str, ...]:
    """内置 Team 的逻辑 ID 清单（数据源只有 ``nanobot.writing.builtin``，惰引入）。

    口径与 ``nanobot/agents/store.py`` 的 ``_builtin_asset_registry`` 一致：本模块
    **不得**在导入期 import ``nanobot.writing``。那条链
    （``nanobot.writing.database`` → ``nanobot.config.paths`` → ``nanobot.config`` →
    ``nanobot.config.loader`` → ``nanobot.config.schema`` 的
    ``_resolve_tool_config_refs()`` → ``nanobot.agent.tools.*``）会把 ``nanobot.config``
    拖进环里，``nanobot.agent`` 的初始化在环中抛 ImportError 且被 schema 的
    ``except ImportError`` 吞掉，于是 ``nanobot.agent`` 从 ``sys.modules`` 消失、只留
    子模块 —— 之后 ``mock.patch("nanobot.agent.tools.mcp.connect_mcp_servers")``
    这类按名 patch 会直接 ``AttributeError``。
    """
    from nanobot.writing.builtin import WRITING_BUILTIN_ASSETS

    return tuple(
        asset.logical_id
        for asset in WRITING_BUILTIN_ASSETS
        if asset.kind == _BUILTIN_TEAM_KIND and asset.protected
    )


def _content_key(definition: TeamDefinition) -> dict[str, Any]:
    """内容指纹：排除服务端盖章字段与物理 ID，用于判断内置记录是否要迁移。"""
    return {
        key: value
        for key, value in definition.to_dict().items()
        if key not in SERVER_OWNED_DEFINITION_FIELDS and key != "id"
    }


class TeamDisabled(RuntimeError):  # noqa: N818  # 映射 503 TEAMS_DISABLED
    """``agents.teams.enabled=false``：禁止新运行，历史仍可读（plan §4 的回滚开关）。"""


class TeamArchived(RuntimeError):  # noqa: N818  # 传输层按契约 §4 映射为 TEAM_NOT_FOUND
    """团队已归档：活动运行可结束，但不再接受新运行。"""


class TeamReferenceError(ValueError):  # noqa: N818  # 映射 400 TEAM_INVALID
    """成员被 Leader 档案排除，或团队引用关系不成立。"""


class TeamProfileUnavailable(ValueError):  # noqa: N818  # 映射 409 TEAM_MEMBER_UNAVAILABLE
    """被引用的 Agent 档案缺失或已隐藏。"""


class CrossSessionForbidden(PermissionError):  # noqa: N818  # 映射 403 CROSS_SESSION_FORBIDDEN
    """调用方所在会话不拥有这条运行/任务：拒绝跨会话读写控制（契约 §3/§5）。"""


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
    """一个 workspace 一个实例；由 gateway 注入，不通过模块全局读取 Loop。

    ``builtin_registry`` 是创作空间内置资产的权威登记处（``nanobot.writing.builtin``）。
    **生产装配路径必须注入它**（``nanobot.webui.teams_api.build_service``）：未注入时
    :meth:`_require_team_writable` 无从判定内置身份，契约 §4 的 409
    ``BUILTIN_RESOURCE_PROTECTED`` 不会产生。未注入只应出现在测试替身里。
    """

    def __init__(
        self,
        repository: TeamRepository,
        profiles: ProfileDirectory,
        *,
        enabled: bool = True,
        writer_owned: bool = True,
        builtin_registry: BuiltinAssetRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.profiles = profiles
        self.enabled = enabled
        self.writer_owned = writer_owned
        self.builtin_registry = builtin_registry

    # ------------------------------------------------------------------ 内置保护

    def _require_team_writable(self, team_id: str) -> None:
        """内置团队（``asset_kind="team"``）受保护：覆盖 / 归档一律拒绝。

        抛 :class:`~nanobot.writing.builtin.BuiltinResourceProtected`（业务码
        ``BUILTIN_RESOURCE_PROTECTED``、HTTP 409、message 见契约 §4）。

        判据是**逻辑 ID 与已登记的物理 ID 双查**：内置记录按 allocator 协议退让到
        ``<logical_id>-builtin`` 时（逻辑 ID 被用户团队占着），只查逻辑 ID 会漏掉
        真正的内置记录，等于给攻击者留了后门。两处命中都按逻辑 ID 报错，message
        因此始终是 ``team/<logical_id>``。
        """
        registry = self.builtin_registry
        if registry is None:
            return
        for logical_id in _builtin_team_logical_ids():
            if team_id == logical_id or (
                registry.physical_id(_BUILTIN_TEAM_KIND, logical_id) == team_id
            ):
                registry.ensure_writable(_BUILTIN_TEAM_KIND, logical_id)

    # ------------------------------------------------------- 内置记录播种 / 迁移

    def ensure_builtin_team(
        self,
        template: TeamDefinition,
        *,
        request_id: str,
    ) -> str | None:
        """播种 / 刷新内置团队，返回它的物理 ID；未接登记处时返回 ``None``。

        **不受保护的内部写路径**：内置记录自己就在受保护清单里，若走公开的
        :meth:`save_team` 会被自己的守卫挡住（自锁），因此播种与迁移只能走这里。

        ``template.id`` 是**逻辑 ID**（产品身份）；物理 ID 由
        :meth:`~nanobot.writing.builtin.BuiltinAssetRegistry.prepare` 的 allocator
        协议决定（契约 §7）：逻辑 ID 被用户对象占用时 allocator 交出空串，registry
        追加 ``-builtin`` 后缀重试，**绝不覆盖用户资产**。重复调用幂等。
        """
        registry = self.builtin_registry
        if registry is None:
            return None
        logical_id = template.id
        registered = registry.physical_id(_BUILTIN_TEAM_KIND, logical_id)
        if registered is not None:
            # 已登记：内容变了就按内部路径推进一版（迁移），否则原样返回。
            self._refresh_builtin_team(template, team_id=registered, request_id=request_id)
            return registered

        def allocator(candidate: str) -> str:
            """空串 = 候选物理 ID 已被用户对象占用，交给 registry 重试后缀。"""
            if self._team_exists(candidate):
                return ""
            self._write_builtin_team(template, team_id=candidate, request_id=request_id)
            return candidate

        return registry.prepare(_BUILTIN_TEAM_KIND, logical_id, allocator=allocator)

    def _team_exists(self, team_id: str) -> bool:
        from nanobot.teams.repository import TeamNotFound

        try:
            self.repository.get_team(team_id)
        except TeamNotFound:
            return False
        return True

    def _write_builtin_team(
        self, template: TeamDefinition, *, team_id: str, request_id: str
    ) -> dict[str, Any]:
        """内部路径创建内置记录（``expected_revision=None`` ⇒ 必须盖章 revision=1）。"""
        now = utc_now_iso()
        definition = replace(
            template, id=team_id, revision=1, created_at=now, updated_at=now
        )
        return self.repository.save_team(
            definition=definition,
            snapshot=self.freeze(definition),
            expected_revision=None,
            request_id=f"{request_id}:{team_id}",
        )

    def _refresh_builtin_team(
        self, template: TeamDefinition, *, team_id: str, request_id: str
    ) -> None:
        """内容迁移：只在可写字段真的变了时推进一版，避免每次启动都刷修订号。"""
        from nanobot.teams.repository import TeamNotFound

        try:
            current = self.repository.get_team(team_id)
        except TeamNotFound:
            # 登记表里有、团队表里没有（被外部删掉）：按内部路径重建。
            self._write_builtin_team(template, team_id=team_id, request_id=request_id)
            return
        if _content_key(current) == _content_key(template):
            return
        # 内容取自模板（成员/上限要保留 dataclass 形状），服务端字段沿用当前记录。
        definition = replace(
            template,
            id=team_id,
            revision=current.revision + 1,
            created_at=current.created_at,
            updated_at=utc_now_iso(),
        )
        self.repository.save_team(
            definition=definition,
            snapshot=self.freeze(definition),
            expected_revision=current.revision,
            request_id=f"{request_id}:{team_id}",
        )

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
            self._require_team_writable(definition.id)
        else:
            editable = {
                key: value
                for key, value in data.items()
                if key not in SERVER_OWNED_DEFINITION_FIELDS
            }
            team_id = editable.get("id")
            if not isinstance(team_id, str) or not team_id:
                raise TeamValidationError("team update requires an id")
            self._require_team_writable(team_id)
            previous = self.repository.get_team(team_id)
            # 归档团队只留一条可写路径：显式把 archived 置回 false（取消归档）。其余编辑仍
            # 被挡在门外，让「已归档」保持内容冻结；契约 §4 的硬不变量是禁止新运行，那条
            # 拦截在 prepare_new_run，与本处无关。
            if previous.archived and editable.get("archived") is not False:
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
        self._require_team_writable(team_id)
        return self.repository.set_archived(
            team_id, expected_revision=expected_revision, request_id=request_id
        )

    def clone_team(self, team_id: str, *, name: str, request_id: str) -> dict[str, Any]:
        """内置团队**可以**复制为自定义：只读源定义，新 id 由服务端分配。"""
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

    def list_revisions(self, team_id: str) -> dict[str, Any]:
        """历史修订清单：编辑器展示「基于哪一版」，以及归档痕迹。"""
        self.repository.get_team(team_id)
        return {"revisions": self.repository.list_revisions(team_id)}

    def teams_referencing_profile(self, agent_id: str) -> list[str]:
        """给 Agent 删除动作用：被未归档团队引用时应返回 409 AGENT_IN_USE_BY_TEAM。"""
        return self.repository.teams_referencing_profile(agent_id)

    def prepare_new_run(self, team_id: str, *, revision: int | None = None) -> TeamRevisionSnapshot:
        """新运行的准入检查：开关、归档与档案可用性；返回该修订的冻结快照。"""
        if not self.enabled:
            raise TeamDisabled("agents.teams.enabled is false")
        if not self.writer_owned:
            # 另一个 gateway 已经持有本 workspace 的调度权（契约 §6：不支持双 writer）。
            raise StorageUnavailable("team scheduling is owned by another gateway")
        definition = self.repository.get_team(team_id)
        if definition.archived:
            raise TeamArchived(f"team {definition.id} is archived")
        target = revision if revision is not None else definition.revision
        snapshot = self.repository.get_revision_snapshot(team_id, revision=target)
        self._require_available(definition.leader_agent_id)
        for member in definition.members:
            self._require_available(member.agent_id)
        return snapshot

    def list_runs(
        self, chat_id: str, *, limit: int = 20, cursor: str | None = None
    ) -> dict[str, Any]:
        """某次聊天的运行清单（契约 §5 ``GET /api/team-runs``）；只投影白名单字段。"""
        page = self.repository.list_runs(chat_id, limit=limit, cursor=cursor)
        return {
            "runs": [project_run_snapshot(run) for run in page["runs"]],
            "nextCursor": page["nextCursor"],
        }

    def get_run(
        self, run_id: str, *, caller_chats: Sequence[str] = ()
    ) -> dict[str, Any]:
        self.require_run_session(run_id, caller_chats)
        return {"run": project_run_snapshot(self.repository.get_run(run_id))}

    def run_events(
        self, run_id: str, *, after_seq: int = 0, limit: int = DEFAULT_EVENT_LIMIT
    ) -> dict[str, Any]:
        """按 seq 补拉事件页；跨工作空间在这里就抛 RUN_NOT_FOUND。"""
        return self.repository.events_page(run_id, after_seq=after_seq, limit=limit)

    def require_run_session(self, run_id: str, caller_chats: Sequence[str]) -> None:
        """这条运行属于调用方的会话吗（契约 §3/§5 的会话访问检查 → 403）。

        ``caller_chats`` 是服务端从连接推导的，客户端自称的 chatId 覆盖不了。推导不出
        身份时**不拦**：读动作也能走普通 HTTP GET，那条路上没有聊天身份，边界是工作空间。
        控制动作（取消/重试）只走已认证 WS，那边由 :class:`~nanobot.teams.runs.TeamRunController`
        按同一套 run_context 比对，推导不出就拒。
        """
        if not caller_chats:
            return
        context = self.repository.run_context(run_id)
        owner = str(context.get("chatId") or "")
        if owner and owner in caller_chats:
            return
        raise CrossSessionForbidden(f"run {run_id} does not belong to the calling session")

    def recover_after_restart(self) -> dict[str, list[str]]:
        """gateway 启动清理（契约 §6/§7）。

        遗留的 ``running`` / ``cancelling`` 一律落 ``interrupted``：本机没有活着的子进程，
        重放只会二次执行工具。没有 writer 权的一方什么都不做——那是别的 gateway 的权威数据。
        """
        if not self.writer_owned:
            return {"interruptedRuns": [], "prunedRuns": []}
        with self.repository.claim_writer():
            interrupted = self.repository.mark_interrupted_runs()
            pruned = self.repository.prune_activity_events()
        return {"interruptedRuns": interrupted, "prunedRuns": pruned}

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
