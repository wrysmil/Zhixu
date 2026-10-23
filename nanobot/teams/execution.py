"""团队子执行适配：把冻结档案交给共享 Runner，拿回结构化结果。

这一层存在的理由（契约 §5）：团队需要的是「这次执行到底成没成、为什么停、用了多少」，
而 ``spawn`` / ``run_inline`` 的契约是一段字符串。两者共用 **同一个** ``SubagentManager``
执行路径和同一个全局配额（``_run_slots``），这里只做三件事：

1. 把 ``TeamExecutionRequest`` 里的可信上下文（run/task/attempt、冻结 ``ResolvedAgent``、
   runtime、workspace scope）转成一次 ``execute_frozen`` 调用 —— 不再按 ``agentId`` 读盘。
2. ``announce=False``：成员结果不回注成 system 消息，避免一份结果唤醒两个 Leader 回合。
3. 把公开的工具活动转发给 ``EventSink``；参数与输出不过去（契约 §7）。

第二套 LLM 循环是本功能的明确禁止项，因此这里不出现 provider 调用、不出现消息拼装。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from nanobot.agent.subagent import (
    SubagentExecutionResult,
    SubagentManager,
    SubagentOrigin,
)
from nanobot.agents.runtime import ResolvedAgent
from nanobot.security.workspace_access import WorkspaceScope
from nanobot.utils.llm_runtime import LLMRuntime


class EventSink(Protocol):
    """子执行活动的接收面。持久化与节流属于 WU-07，这里只定义投递口径。"""

    async def task_activity(self, **payload: Any) -> None: ...


class _NoEvents:
    """不做任何事的 sink：单测与不需要活动的调用方用它，而不是传 ``None`` 再分支。"""

    async def task_activity(self, **payload: Any) -> None:
        return None


NO_EVENTS = _NoEvents()


@dataclass(frozen=True)
class TeamExecutionRequest:
    """一次成员执行所需的全部可信输入；字段都来自运行快照，不是模型可控值。"""

    run_id: str
    task_id: str
    attempt_id: str
    member_id: str
    agent_id: str
    instruction: str
    acceptance: str
    agent: ResolvedAgent
    runtime: LLMRuntime
    workspace: Path
    workspace_scope: WorkspaceScope | None = None
    origin_message_id: str | None = None
    session_key: str | None = None
    # 可信运行上下文注入的工具（写作会话）；不是模型可控值，也不受档案白名单裁剪。
    extra_tools: tuple[Any, ...] = ()


class TeamMemberExecutor:
    """把 :class:`TeamExecutionRequest` 跑成 :class:`SubagentExecutionResult`。"""

    def __init__(self, manager: SubagentManager) -> None:
        self._manager = manager

    async def execute_member(
        self,
        request: TeamExecutionRequest,
        *,
        events: EventSink = NO_EVENTS,
    ) -> SubagentExecutionResult:
        if request.agent.tool_names is None:
            # ``None`` 在档案域表示「没约束、全量放行」，但团队里「空白名单」和
            # 「未配置」必须区分开：后者一律拒绝起跑，避免静默扩权（验收 A19）。
            raise ValueError(
                f"invalid_profile: frozen profile for {request.agent_id!r} "
                "has no resolved tool allow-list"
            )
        origin: SubagentOrigin = {
            "channel": "team",
            "chat_id": request.run_id,
            "session_key": request.session_key,
        }

        async def _on_activity(payload: Mapping[str, Any]) -> None:
            await events.task_activity(**payload)

        return await self._manager.execute_frozen(
            request.instruction,
            execution_id=request.attempt_id,
            label=f"{request.member_id}: {request.instruction[:30]}",
            origin=origin,
            agent=request.agent,
            runtime=request.runtime,
            origin_message_id=request.origin_message_id,
            workspace_scope=request.workspace_scope,
            extra_tools=request.extra_tools,
            on_activity=_on_activity,
        )
