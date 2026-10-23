"""Spawn tool for creating background subagents."""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context
from nanobot.agent.tools.execution import current_tool_call_id
from nanobot.agent.tools.schema import (
    BooleanSchema,
    NumberSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.agents.runtime import AgentProfileRuntime
from nanobot.security.workspace_access import current_workspace_scope

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager
    from nanobot.agent.tools.context import ToolContext


@tool_parameters(
    tool_parameters_schema(
        task=StringSchema("The task for the subagent to complete"),
        label=StringSchema("Optional short label for the task (for display)"),
        temperature=NumberSchema(
            description=(
                "Optional sampling temperature for the subagent "
                "(0.0 = deterministic, higher = more creative). "
                "Defaults to the provider's configured temperature."
            ),
            minimum=0.0,
            maximum=2.0,
        ),
        wait=BooleanSchema(
            description=(
                "Wait for the subagent and return its result directly. Use this for a "
                "blocking consultation that must inform the current turn. Defaults to "
                "false for background execution."
            ),
            default=False,
        ),
        agent=StringSchema(
            description=(
                "Optional id of a configured Agent profile. When given, the subagent "
                "runs with that profile's prompt, tool set and skill set. Omit to use "
                "the default subagent configuration."
            ),
        ),
        required=["task"],
    )
)
class SpawnTool(Tool):
    """Tool to spawn a subagent for background task execution."""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._agent_profiles: AgentProfileRuntime | None = None
        try:
            self._agent_profiles = AgentProfileRuntime(Path(manager.workspace))
        except (TypeError, AttributeError):
            # manager 没有 workspace（测试替身等）——描述里就不枚举档案。
            self._agent_profiles = None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        manager = ctx.subagent_manager
        if manager is None:
            raise RuntimeError("SpawnTool requires an initialized subagent manager")
        return cls(manager=manager)

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        base = (
            "Spawn a subagent to handle a task in the background. "
            "Use this for complex or time-consuming tasks that can run independently. "
            "Set wait=true for a consultation whose result must inform the current turn. "
            "The subagent will complete the task and report back when done. "
            "For deliverables or existing projects, inspect the workspace first "
            "and use a dedicated subdirectory when helpful."
        )
        available = self._available_agent_ids()
        if not available:
            return base
        return (
            f"{base} Pass agent=<id> to run it as a configured Agent profile. "
            f"Available profiles: {', '.join(available)}."
        )

    def _available_agent_ids(self) -> list[str]:
        """当前可用的档案 id 列表；读不到时返回空列表（描述退回基础文案）。

        排除 ``hidden`` 的：这份清单会进 LLM 的工具描述，用户隐藏档案的意图
        就是不让它出现在被枚举的范围里。
        """
        if self._agent_profiles is None:
            return []
        try:
            from nanobot.agents.store import AgentStore  # local: 避免导入环

            return [
                p.id
                for p in AgentStore(self._agent_profiles.workspace).list_profiles(
                    include_hidden=False
                )
            ]
        except (OSError, ValueError):
            return []

    @property
    def concurrency_safe(self) -> bool:
        """Each call owns its task state; the manager serializes capacity admission."""
        return True

    async def execute(
        self,
        task: str,
        label: str | None = None,
        temperature: float | None = None,
        wait: bool = False,
        agent: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Spawn a subagent to execute the given task."""
        request_ctx = current_request_context()
        if request_ctx is None or request_ctx.runtime is None:
            return ToolResult.error("Error: spawn requires an active model runtime")
        origin_channel = request_ctx.channel
        origin_chat_id = request_ctx.chat_id
        session_key = request_ctx.session_key or f"{origin_channel}:{origin_chat_id}"
        method = self._manager.run_inline if wait else self._manager.spawn
        # 只在真的要按档案跑时才传 agent_id：省略时保持旧调用形状，
        # 第三方替身 / 子类若还没跟上新签名也不会被这一个可选参数打爆。
        extra: dict[str, Any] = {} if agent is None else {"agent_id": agent}
        invocation_id = None
        if hasattr(self._manager, "create_execution"):
            invocation_id = await self._manager.create_execution(
                request_ctx, parent_tool_call_id=current_tool_call_id() or "",
                label=label, agent_id=agent, wait=wait,
            )
            if invocation_id is not None:
                extra["invocation_id"] = invocation_id

        # 后台路径的失败要等任务跑完才通过 announce 回到 LLM 那儿，模型会先
        # 收到一句「started」然后等上几十秒才知道白等。这里在派发前预检，把
        # 档案 id 拼错这类错误当场回给 LLM，让它能立刻改用正确的 id。
        if agent is not None and hasattr(self._manager, "_prepare_agent"):
            prep = self._manager._prepare_agent(agent)  # type: ignore[attr-defined]
            if prep.rejected:
                if invocation_id is not None:
                    await self._manager.reject_execution(invocation_id)
                return ToolResult.error(prep.error or "Error: Agent 档案无效")

        return await method(
            task=task,
            runtime=request_ctx.runtime,
            label=label,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            session_key=session_key,
            origin_message_id=request_ctx.message_id,
            temperature=temperature,
            workspace_scope=current_workspace_scope(),
            **extra,
        )
