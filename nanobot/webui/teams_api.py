"""Agent Team 的传输无关绑定层：把 TeamService 接成 settings handler 需要的 operations。

与 ``agents_api.py`` 同分层：本模块不认 HTTP 也不认 WebSocket，只负责「领域服务 →
动作入口」和「档案存储 → 服务层视图」两段适配。gateway 装配时调一次
:func:`build_team_operations`，测试可以直接拿内存替身喂 handler。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from nanobot.agents.models import AgentProfile
from nanobot.agents.store import AgentStore, AgentStoreError
from nanobot.teams.database import TeamDatabase
from nanobot.teams.repository import TeamRepository
from nanobot.teams.service import ProfileRecord, TeamService
from nanobot.webui.teams_routes import TeamSettingsOperations


class AgentProfileDirectory:
    """把 ``AgentStore`` 压成服务层需要的最小档案视图（契约 §1：只冻结约束）。

    ``description`` / ``icon`` / ``color`` 这类展示字段**故意不搬**：它们进快照等于
    把档案文案冻在每一次运行里，档案改了历史运行还显示旧句子。凭据字段更不可能过来。
    """

    def __init__(self, store: AgentStore) -> None:
        self._store = store

    def get(self, agent_id: str) -> ProfileRecord | None:
        try:
            profile = self._store.get_profile(agent_id)
        except AgentStoreError as error:
            if error.status == 404:
                # 「没有这个档案」是服务层要的回答：翻译成 TEAM_MEMBER_UNAVAILABLE。
                return None
            # 非法 id 走到这里说明团队记录本身坏了，不能当成「查无此人」。
            raise
        return _record_of(profile)


def _record_of(profile: AgentProfile) -> ProfileRecord:
    return ProfileRecord(
        id=profile.id,
        name=profile.name,
        prompt=profile.prompt,
        model_id=profile.model_id,
        tools=profile.tools,
        skills=profile.skills,
        sub_agents=profile.sub_agents,
        hidden=profile.hidden,
        updated_at=profile.updated_at,
    )


def build_service(workspace: Path, *, enabled: bool = True) -> TeamService:
    """装配一个按 workspace 绑定的 :class:`TeamService`。

    库路径只由 workspace 推导（契约 §2）：调用方不能指定 DB 路径，否则上游一个
    透传参数的 bug 就能读到别的工作空间的数据。
    """
    repository = TeamRepository(TeamDatabase(Path(workspace)))
    return TeamService(
        repository,
        AgentProfileDirectory(AgentStore(Path(workspace))),
        enabled=enabled,
    )


def build_team_operations(service: TeamService) -> TeamSettingsOperations:
    """领域服务 → handler operations。同步入口：路由层已经用 ``asyncio.to_thread`` 包过。

    写成带标注的具名闭包而不是 ``lambda **kw``：关键字写错要立刻在调用点炸出来，
    而不是在 ``Callable[...]`` 的宽松签名下变成运行时 ``TypeError``。
    """

    def list_teams(*, limit: int, cursor: str | None) -> dict[str, Any]:
        return service.list_teams(limit=limit, cursor=cursor)

    def get_detail(*, team_id: str, revision: int | None) -> dict[str, Any]:
        return service.get_team_detail(team_id, revision=revision)

    def list_revisions(*, team_id: str) -> dict[str, Any]:
        return service.list_revisions(team_id)

    def save_team(
        *, payload: dict[str, Any], expected_revision: int | None, request_id: str
    ) -> dict[str, Any]:
        return service.save_team(
            payload, expected_revision=expected_revision, request_id=request_id
        )

    def archive_team(*, team_id: str, expected_revision: int, request_id: str) -> dict[str, Any]:
        return service.archive_team(
            team_id, expected_revision=expected_revision, request_id=request_id
        )

    def clone_team(*, team_id: str, name: str, request_id: str) -> dict[str, Any]:
        return service.clone_team(team_id, name=name, request_id=request_id)

    return TeamSettingsOperations(
        list_teams=list_teams,
        get_detail=get_detail,
        list_revisions=list_revisions,
        save_team=save_team,
        archive_team=archive_team,
        clone_team=clone_team,
    )


def team_reference_lookup(workspace: Path) -> Callable[[str], list[str]]:
    """给 Agent 删除动作用：返回引用该档案的**未归档**团队 id 列表（契约 §3）。

    只读，不建库；库不存在时返回「没有引用」，因为「从没用过团队」是合法状态。
    """
    repository = TeamRepository(TeamDatabase(Path(workspace)))

    def _lookup(agent_id: str) -> list[str]:
        return repository.teams_referencing_profile(agent_id)

    return _lookup
