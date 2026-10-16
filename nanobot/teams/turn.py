"""Leader 回合装配：一条团队会话的消息 → 一个运行 + 一次可派活的回合。

``AgentLoop`` 只认识两个注入点（``team_context_provider`` / ``team_run_cancel_hook``），
团队的概念一个都不许渗进通用回合管线；反过来，这一层不碰 WebSocket、不碰前端。

三条口径（契约 §2、§4、§6）：

1. **绑定是唯一开关**：``session.metadata`` 里没有团队绑定就是普通聊天，返回 ``None``，
   一行团队代码都不跑。绑定由 WU-05 的首条消息路径落盘，这里只读。
2. **一个可见轮次一个运行**：内部续接带着同一个 ``webui_turn_id`` 再来一回合时必须复用
   运行（``team_runs`` 上有唯一索引），否则一次用户提问会被拆成两条互不相干的任务流水。
3. **准入失败就失败**：团队关了/归档了/档案没了，直接抛出，让这一回合明确报错。
   静默退回普通聊天等于给一个团队会话换上全默认权限的 agent，那是扩权不是降级。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from loguru import logger

from nanobot.agent.tools.team import TeamDispatchTool
from nanobot.teams.binding import binding_from_metadata
from nanobot.teams.events import (
    TeamEventPublisher,
    TeamPublisherFactory,
    bus_publisher,
)
from nanobot.teams.execution import TeamMemberExecutor
from nanobot.teams.leader import (
    LeaderTurnContext,
    ModelPresetUnavailable,
    render_role_prompt,
)
from nanobot.teams.models import TeamRevisionSnapshot
from nanobot.teams.scheduler import SnapshotMemberCatalog, TeamRunHandle, TeamScheduler

# 服务端确认的 WebUI 可见轮次 id；run.turnId 用它，loop 的内部 id 另存一列（契约 §4）。
VISIBLE_TURN_ID_METADATA_KEY = "webui_turn_id"


class _AdmittingService(Protocol):
    """回合装配只用到准入检查，不依赖整个 TeamService。"""

    def prepare_new_run(self, team_id: str, *, revision: int | None = None) -> TeamRevisionSnapshot: ...


class AgentLike(Protocol):
    """桥接需要碰到的 loop 表面。"""

    team_context_provider: Callable[..., Any] | None
    team_run_cancel_hook: Callable[[str], Any] | None


class TeamHost(Protocol):
    """装配团队栈需要的宿主表面：子执行器 + 两份工具目录 + 默认运行时。"""

    subagents: Any
    context: Any
    runtime_resolver: Any
    # ``AgentLoop`` 把 ``tool_names`` 暴露成 property，测试宿主多用方法。
    tool_names: Sequence[str] | Callable[[], Sequence[str]]


def _metadata_value(metadata: dict[str, Any] | None, key: str) -> str | None:
    raw = metadata.get(key) if isinstance(metadata, dict) else None
    return str(raw) if isinstance(raw, str) and raw.strip() else None


def _is_recovery_continuation(msg: Any) -> bool:
    """这条入站是「点继续」的重放吗（``nanobot/session/recovery.py`` 盖的 metadata）。

    键名在这里复述成字面量而不是 import 常量：团队域不该为了读一个标记就把会话恢复
    模块拖进依赖图（那边反过来依赖 loop）。标记语义变了，恢复侧与这里会一起被测试逼出来。
    """
    metadata = getattr(msg, "metadata", None)
    if not isinstance(metadata, dict):
        return False
    return bool(cast("Mapping[str, Any]", metadata).get("_webui_recovery_id"))


@dataclass
class TeamLeaderTurns:
    """loop 注入点本体：``await turns(msg, session, turn_id)`` 得到本回合的团队装配。"""

    service: _AdmittingService
    scheduler: TeamScheduler
    workspace: Path
    tool_names: Callable[[], Sequence[str]] = field(default=lambda: ())
    skill_names: Callable[[], Sequence[str]] = field(default=lambda: ())
    # 每次开新运行取「当回合的默认执行配置」；装配时抓一份会在换模型后过期。
    runtime_provider: Callable[[], Any] | None = None
    # 重试前的 preset 可用性检查要用；按名字解析且不改默认选择（契约 §5）。
    preset_resolver: Callable[[str], Any] | None = None

    async def __call__(self, msg: Any, session: Any, turn_id: str) -> LeaderTurnContext | None:
        if _is_recovery_continuation(msg):
            # 契约 §6：恢复续接**不得**重放 team_dispatch。已跑过的成员可能已经写盘、
            # 发过消息，续接只能按普通回合回答；要重新派活由面板上的重试入口显式发起。
            logger.info(
                "team dispatch is blocked on a recovery continuation: "
                f"session={getattr(session, 'key', '')}"
            )
            return None
        binding = binding_from_metadata(getattr(session, "metadata", None))
        if binding is None:
            return None
        session_key = str(getattr(session, "key", "") or "")
        run_turn_id = (
            _metadata_value(getattr(msg, "metadata", None), VISIBLE_TURN_ID_METADATA_KEY) or turn_id
        )
        handle = self.scheduler.open_run(session_key=session_key, turn_id=run_turn_id)
        if handle is None:
            snapshot = await asyncio.to_thread(
                self.service.prepare_new_run, binding.team_id, revision=binding.team_revision
            )
            handle = await self.scheduler.start_run(
                binding=binding,
                chat_id=str(getattr(msg, "chat_id", "") or session_key),
                session_key=session_key,
                turn_id=run_turn_id,
                internal_turn_id=turn_id,
                workspace=self.workspace,
                origin_message_id=_metadata_value(getattr(msg, "metadata", None), "message_id"),
                snapshot=snapshot,
                runtime=self.runtime_provider() if self.runtime_provider is not None else None,
            )
        return self._context_for(handle)

    async def cancel_runs(self, session_key: str) -> int:
        """停止桥接：聊天被停掉时，该会话在场的团队运行必须一起停（契约 §6）。"""
        return await self.scheduler.cancel_session(session_key)

    def _context_for(self, handle: TeamRunHandle) -> LeaderTurnContext:
        revision = handle.revision
        definition = revision.definition
        catalog = SnapshotMemberCatalog(
            known_tools=list(self.tool_names()),
            known_skills=list(self.skill_names()),
        )
        leader = catalog.resolve(revision.leader, workspace=self.workspace)
        return LeaderTurnContext(
            team_id=definition.id,
            team_revision=definition.revision,
            team_name=definition.name,
            run_id=handle.run_id,
            turn_id=handle.turn_id,
            role_prompt=render_role_prompt(
                team_name=definition.name,
                team_revision=definition.revision,
                instructions=definition.instructions,
                leader_prompt=leader.prompt,
                members=[(item.profile.name, item.member.role) for item in revision.members],
            ),
            tool_allow=leader.tool_names or frozenset(),
            skill_exclude=leader.skill_exclude,
            model_id=leader.model_id,
            dispatch_tool=TeamDispatchTool(  # pyright: ignore[reportAbstractUsage]
                handle
            ),
            turn_finalizer=lambda delivered: self._finalize(handle, delivered),
        )

    async def _finalize(self, handle: TeamRunHandle, delivered: bool) -> None:
        await handle.finalize(delivered=delivered)
        self.scheduler.forget(handle.run_id)


def bind_team_turns(agent: AgentLike, turns: TeamLeaderTurns | None) -> None:
    """把回合装配挂到 loop 上；没有团队栈时什么都不做（普通 gateway 一字不改）。"""
    if turns is None:
        return
    agent.team_context_provider = turns
    agent.team_run_cancel_hook = turns.cancel_runs


class _StackService(Protocol):
    """工厂需要 service 同时暴露 repository（``TeamService`` 满足）。"""

    repository: Any

    def prepare_new_run(
        self, team_id: str, *, revision: int | None = None
    ) -> TeamRevisionSnapshot: ...


def _publisher_factory(host: TeamHost) -> TeamPublisherFactory | None:
    """有总线就给出按运行投递的工厂；没有（测试宿主/未启用 webui）保持 ``None``。

    ``None`` 时调度器完全不投影、不投递，执行语义与 WU-06 一致：事件是旁路，
    不能因为出口缺失而改变任务怎么跑。
    """
    bus: Any = getattr(host, "bus", None)
    candidate: Any = getattr(bus, "publish_event", None)
    if not callable(candidate):
        return None
    publish_event: Any = candidate

    def factory(session_key: str, chat_id: str) -> TeamEventPublisher:
        return bus_publisher(publish_event, session_key, chat_id)

    return factory


def build_team_turns(
    *, service: _StackService, host: TeamHost, workspace: Path | str
) -> TeamLeaderTurns:
    """把一个 workspace 的团队执行栈装成 loop 注入点；只由 gateway 组装层调用。

    Leader 与成员用**两份不同的工具目录**：成员求交的是 ``scope="subagent"`` 的真实
    子执行工具集，Leader 求交的是主循环 core registry。混成一份会让 Leader 看见子
    Agent 专属工具（计划 §3.1），而那是权限扩张。
    """
    root = Path(workspace)

    def leader_tools() -> Sequence[str]:
        # AgentLoop.tool_names 是 property，别的宿主可能是方法：这一层是域外边界，
        # 两种都接下来，装配才不至于在 gateway 启动那一刻才炸。
        names = host.tool_names
        return list(names() if callable(names) else names)

    def member_tools() -> Sequence[str]:
        return list(host.subagents.subagent_tool_names())

    def skill_names() -> Sequence[str]:
        skills = getattr(host.context, "skills", None)
        if skills is None:
            return ()
        # filter_unavailable=False：档案显式点名的技能即使本机缺依赖也要进目录，
        # 由 resolve_selection 决定放行还是排除，而不是当作「本机没有这个名字」。
        return [str(item["name"]) for item in skills.list_skills(filter_unavailable=False)]

    def current_runtime() -> Any:
        return host.runtime_resolver.runtime

    def resolve_preset(name: str) -> Any:
        resolver = getattr(host.runtime_resolver, "resolve_preset", None)
        if resolver is None:
            raise ModelPresetUnavailable("preset resolution is not configured")
        return resolver(name)

    scheduler = TeamScheduler(
        repository=service.repository,
        executor=TeamMemberExecutor(host.subagents),
        members=SnapshotMemberCatalog(known_tools=member_tools, known_skills=skill_names),
        runtime=current_runtime(),
        workspace=root,
        publisher=_publisher_factory(host),
    )
    return TeamLeaderTurns(
        service=service,
        scheduler=scheduler,
        workspace=root,
        tool_names=leader_tools,
        skill_names=skill_names,
        runtime_provider=current_runtime,
        preset_resolver=resolve_preset,
    )
