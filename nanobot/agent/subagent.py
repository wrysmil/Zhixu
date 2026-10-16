"""Subagent manager for background task execution."""

import asyncio
import json
import time
import uuid
import warnings
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, NotRequired, TypedDict

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.runner import AgentRunner, AgentRunSpec
from nanobot.agent.tools.base import ToolResult
from nanobot.agent.tools.context import (
    RequestContext,
    ToolContext,
    bind_request_context,
    reset_request_context,
)
from nanobot.agent.tools.exec_session import ExecSessionManager
from nanobot.agent.tools.file_state import FileStates
from nanobot.agent.tools.loader import ToolLoader
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agents.runtime import AgentProfileRuntime, ResolvedAgent
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import AgentDefaults, ToolsConfig
from nanobot.llm_usage.context import LLMUsageSource, current_llm_usage_source
from nanobot.providers.base import LLMProvider, LLMUsage
from nanobot.security.workspace_access import (
    WorkspaceScope,
    bind_workspace_scope,
    reset_workspace_scope,
    workspace_sandbox_status,
)
from nanobot.utils.llm_runtime import LLMRuntime
from nanobot.utils.prompt_templates import render_template


class SubagentOrigin(TypedDict):
    channel: str
    chat_id: str
    session_key: str | None
    llm_usage_source: NotRequired[LLMUsageSource]


#: 宿主（写作协作批次）在派发期间提供的**已冻结 Skill 正文**：(name, body) 对。
#: 正文只活在这段运行期上下文里：既不进 ``TeamExecutionRequest``，也不落任何库。
#: 用 ContextVar 而不是参数，是为了不改 ``execute_frozen`` 的冻结签名与成员执行适配层
#: （``TeamExecutionRequest`` 属 Team 域，写作域不扩它的字段）。
_FROZEN_SKILLS: ContextVar[tuple[tuple[str, str], ...]] = ContextVar(
    "nanobot_frozen_skills", default=()
)


def current_frozen_skills() -> tuple[tuple[str, str], ...]:
    """当前作用域里的冻结 Skill 正文；没有则为空元组。"""
    return _FROZEN_SKILLS.get()


@contextmanager
def frozen_skills_context(skills: Iterable[tuple[str, str]]) -> Iterator[None]:
    """把已冻结的 Skill 正文挂到当前（及派生）任务上；退出即还原。

    ``asyncio.create_task`` 会复制当前上下文，因此宿主在 ``await`` 派发之前挂上、
    在派发返回之后摘掉，就能保证「批内同一个 Skill 不会在后台被换掉」。
    """
    token = _FROZEN_SKILLS.set(tuple((str(name), str(body)) for name, body in skills))
    try:
        yield
    finally:
        _FROZEN_SKILLS.reset(token)


def render_frozen_skills_section(skills: Iterable[tuple[str, str]] | None = None) -> str:
    """把冻结正文渲染成成员提示词里的一段只读附注；没有正文时返回空串。"""
    entries = list(_FROZEN_SKILLS.get() if skills is None else skills)
    if not entries:
        return ""
    blocks = [
        f"### {name}\n\n{body.strip()}\n" for name, body in entries if body.strip()
    ]
    if not blocks:
        return ""
    return (
        "## 本批次冻结的技能正文（只读）\n\n"
        "以下是本次任务被冻结的技能正文，逐字使用；不要改写到工作区，也不要在回复里复述它。\n\n"
        + "\n".join(blocks)
    )


@dataclass(frozen=True, slots=True)
class _AgentPreparation:
    """``agent`` 参数的解析结果。

    区分两件必须分开的事：

    - **没传 agent**（``agent_id`` 为 None）→ :attr:`allowed_tools` 为 None，
      工具集不裁剪。这是二期之前就有的默认行为，不是漏洞。
    - **传了但解析不出来** → :attr:`rejected` 为 True、:attr:`allowed_tools`
      为空集。绝不能退化成上一条。

    漏掉这个区分的后果：用户在档案页配了只读档案，LLM 把 id 拼错一个字母，
    解析失败后子 Agent 却拿到 subagent 范围的全量 13 个工具（含 exec /
    write_file / edit_file），用户画的能力边界被静默丢弃，且没有任何信号。
    """

    rejected: bool
    error: str | None = None
    agent: "ResolvedAgent | None" = None
    allowed_tools: frozenset[str] | None = None


@dataclass(slots=True)
class SubagentStatus:
    """Real-time status of a running subagent."""

    task_id: str
    label: str
    task_description: str
    started_at: float          # time.monotonic()
    # queued | initializing | awaiting_tools | tools_completed | final_response | done | error
    phase: str = "initializing"
    iteration: int = 0
    tool_events: list[dict[str, str]] = field(default_factory=list)
    usage: LLMUsage | None = None
    stop_reason: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SubagentExecutionResult:
    """一次子执行的结构化结论（契约 §5）。

    存在的理由是 **判定不能靠读字符串**：旧路径把 ``max_iterations`` 也当成成功，
    只在文本里留一句兜底说明，调用方只能 ``"Error" in text`` 猜。团队执行需要真实
    的成败、停止原因与用量，因此这里把 ``outcome`` 收紧成四值，并把兜底文案与
    ``stop_reason`` 分开保存 —— 文本仍然给旧调用方用，语义不变。

    ``execution_id`` 由调用方给定（团队的 attemptId），这样事件流、任务表和这次执行
    能对上同一个 id，而不是内部随机短 id。
    """

    execution_id: str
    outcome: str  # succeeded | failed | cancelled | interrupted
    stop_reason: str
    final_content: str
    usage: LLMUsage | None = None
    error_code: str | None = None


class _SubagentHook(AgentHook):
    """Hook for subagent execution — logs tool calls and updates status."""

    def __init__(
        self,
        task_id: str,
        status: SubagentStatus | None = None,
        on_activity: "Callable[[Mapping[str, Any]], Any] | None" = None,
    ) -> None:
        super().__init__()
        self._task_id = task_id
        self._status = status
        self._on_activity = on_activity

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for tool_call in context.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.debug(
                "Subagent [{}] executing: {} with arguments: {}",
                self._task_id, tool_call.name, args_str,
            )

    async def after_iteration(self, context: AgentHookContext) -> None:
        if self._status is not None:
            self._status.iteration = context.iteration
            self._status.tool_events = list(context.tool_events)
            self._status.usage = context.usage
            if context.error:
                self._status.error = str(context.error)
        if self._on_activity is None:
            return
        # 公开摘要只取白名单字段：参数与输出一律不外传（契约 §7）。
        for event in context.tool_events:
            name = event.get("name") or event.get("tool") or ""
            if not name:
                continue
            await self._on_activity({
                "toolName": name,
                "toolStatus": event.get("status") or "",
                "iteration": context.iteration,
            })


class SubagentManager:
    """Manages background subagent execution."""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        workspace: Path | None = None,
        bus: MessageBus | None = None,
        max_tool_result_chars: int | None = None,
        model: str | None = None,
        tools_config: ToolsConfig | None = None,
        restrict_to_workspace: bool = False,
        disabled_skills: list[str] | None = None,
        max_iterations: int | None = None,
        max_concurrent_subagents: int | None = None,
        llm_wall_timeout_for_session: Callable[[str | None], float | None] | None = None,
        agent_profiles: AgentProfileRuntime | None = None,
        model_resolver: Callable[[str], LLMRuntime | None] | None = None,
    ):
        if workspace is None:
            raise TypeError("SubagentManager.__init__() missing required argument: 'workspace'")
        if bus is None:
            raise TypeError("SubagentManager.__init__() missing required argument: 'bus'")
        if max_tool_result_chars is None:
            raise TypeError(
                "SubagentManager.__init__() missing required argument: 'max_tool_result_chars'"
            )
        if model is not None and provider is None:
            raise TypeError("SubagentManager model compatibility argument requires provider")

        defaults = AgentDefaults()
        self._compat_runtime: LLMRuntime | None = None
        if provider is not None:
            warnings.warn(
                "SubagentManager provider/model constructor arguments are deprecated; "
                "pass runtime=... to spawn() instead",
                DeprecationWarning,
                stacklevel=2,
            )
            self._compat_runtime = LLMRuntime.capture(
                provider,
                model or provider.get_default_model(),
                context_window_tokens=defaults.context_window_tokens,
            )
        self.workspace = workspace
        self.bus = bus
        self.tools_config = tools_config or ToolsConfig()
        self.max_tool_result_chars = max_tool_result_chars
        self.restrict_to_workspace = restrict_to_workspace
        self.disabled_skills = set(disabled_skills or [])
        self.max_iterations = (
            max_iterations
            if max_iterations is not None
            else defaults.max_tool_iterations
        )
        self.max_concurrent_subagents = (
            max_concurrent_subagents
            if max_concurrent_subagents is not None
            else defaults.max_concurrent_subagents
        )
        self._run_slots = asyncio.Semaphore(self.max_concurrent_subagents)
        self.runner = AgentRunner()
        self._exec_session_manager = ExecSessionManager()
        self._llm_wall_timeout_for_session = llm_wall_timeout_for_session
        # 二期：Agent 档案。默认 None = 完全保持一期之前的子 agent 行为。
        self._agent_profiles = agent_profiles
        self._model_resolver = model_resolver
        self._running_tasks: dict[str, asyncio.Task[str]] = {}
        self._task_statuses: dict[str, SubagentStatus] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}

    def runtime_statuses(self) -> Mapping[str, SubagentStatus]:
        """Return the observable task statuses used by runtime-control snapshots."""
        return self._task_statuses

    def set_provider(self, provider: LLMProvider, model: str) -> None:
        """Update the deprecated runtime source used by legacy ``spawn`` calls."""
        warnings.warn(
            "SubagentManager.set_provider() is deprecated; pass runtime=... to spawn() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        context_window_tokens = (
            self._compat_runtime.context_window_tokens
            if self._compat_runtime is not None
            else AgentDefaults().context_window_tokens
        )
        self._compat_runtime = LLMRuntime.capture(
            provider,
            model,
            context_window_tokens=context_window_tokens,
        )

    def _compat_spawn_runtime(self) -> LLMRuntime:
        runtime = self._compat_runtime
        if runtime is None:
            raise TypeError(
                "SubagentManager.spawn() missing required keyword-only argument: 'runtime'"
            )
        warnings.warn(
            "SubagentManager.spawn() without runtime is deprecated; pass runtime=... explicitly",
            DeprecationWarning,
            stacklevel=3,
        )
        return LLMRuntime.capture(
            runtime.provider,
            runtime.model,
            context_window_tokens=runtime.context_window_tokens,
        )

    def _resolve_agent(self, agent_id: str | None) -> ResolvedAgent | None:
        """把档案 id 解析成运行时约束。未注入 / id 非法 / 档案不存在 → None。

        仅保留给「未注入 agent_profiles 的宿主」和测试用。**运行路径请用
        :meth:`_prepare_agent`** —— 它会区分「没传」与「传了但解析失败」，
        这里的 None 无法表达这个区别。
        """
        if agent_id is None or self._agent_profiles is None:
            return None
        try:
            return self._agent_profiles.resolve(
                agent_id, workspace=self.workspace, tools_config=self.tools_config
            )
        except Exception:  # noqa: BLE001 - 调用方需自行决定降级策略
            logger.warning("Agent 档案 {} 解析异常", agent_id, exc_info=True)
            return None

    def _available_agent_ids(self) -> list[str]:
        """当前可用的档案 id，供错误信息列出可选值。

        排除 ``hidden`` 的：用户隐藏档案的意图是不让它出现在被枚举的清单里，
        而这份清单会进 LLM 的工具描述与报错信息。
        """
        if self._agent_profiles is None:
            return []
        try:
            from nanobot.agents.store import AgentStore  # local: 避免导入环

            return [
                p.id
                for p in AgentStore(self.workspace).list_profiles(include_hidden=False)
            ]
        except (OSError, ValueError):
            return []

    def _prepare_agent(self, agent_id: str | None) -> _AgentPreparation:
        """解析 ``agent`` 参数，解析不出来就 fail-closed。

        ``agent_id`` 是 LLM 传的**不可信输入**（见 nanobot 的 spawn schema），
        最常见的错误是拼错一个字母。若此时退回「无档案」路径，子 Agent 会拿到
        subagent 范围的全量工具，用户显式配置的只读约束被静默丢弃且无任何
        信号——所以这里拒绝执行，并把可选 id 一并回给 LLM 让它能自行纠正。

        空格、空白串同样按非法处理：LLM 传空值本身就是异常信号，静默当
        「未传」会掩盖问题。
        """
        if agent_id is None:
            return _AgentPreparation(rejected=False, allowed_tools=None)

        if self._agent_profiles is None:
            # 宿主没注入档案能力。保留一期行为：此时 agent 参数本就无从解析，
            # 拒绝会让所有旧宿主直接不可用。
            return _AgentPreparation(rejected=False, allowed_tools=None)

        if not agent_id.strip():
            logger.warning("spawn 收到空的 agent 参数，按非法档案拒绝")
            return self._reject_agent(agent_id, "档案 id 为空")

        try:
            resolved = self._agent_profiles.resolve(
                agent_id, workspace=self.workspace, tools_config=self.tools_config
            )
        except Exception:  # noqa: BLE001 - 解析异常同样按拒绝处理
            logger.warning("Agent 档案 {} 解析异常，已拒绝", agent_id, exc_info=True)
            return self._reject_agent(agent_id, "档案读取失败")

        if resolved is None:
            return self._reject_agent(agent_id, "找不到该档案")

        return _AgentPreparation(
            rejected=False, agent=resolved, allowed_tools=resolved.tool_names
        )

    def _reject_agent(self, agent_id: str, reason: str) -> _AgentPreparation:
        """构造拒绝结果：记录日志、列出可用 id 供 LLM 纠正。"""
        available = self._available_agent_ids()
        logger.warning(
            "spawn 收到无法解析的 agent={!r}（{}），已拒绝执行。"
            "可用档案：{}",
            agent_id,
            reason,
            ", ".join(available) or "（无）",
        )
        hint = f"可用档案：{', '.join(available)}" if available else "当前没有可用档案"
        return _AgentPreparation(
            rejected=True,
            error=(
                f"Error: 无法使用 Agent 档案 {agent_id!r}——{reason}。"
                f"{hint}。"
                "请改用其中一个 id，或省略 agent 参数以使用默认子 Agent。"
            ),
            agent=None,
            # 空集：万一有调用方忽略了 rejected 标记，工具也不会被放开。
            allowed_tools=frozenset(),
        )

    def _apply_model_override(self, runtime: LLMRuntime, model_id: str | None) -> LLMRuntime:
        """按档案的 ``modelId`` 换 runtime；未注入 resolver 或解析不出则原样返回。

        provider/凭据的解析不属于 subagent 的职责，宿主注入 resolver 才有覆盖。
        """
        if model_id is None or self._model_resolver is None:
            return runtime
        resolved = self._model_resolver(model_id)
        return resolved if resolved is not None else runtime

    def _subagent_tools_config(self) -> ToolsConfig:
        """Build a ToolsConfig scoped for subagent use."""
        return ToolsConfig(
            exec=self.tools_config.exec,
            web=self.tools_config.web,
            file=self.tools_config.file,
            restrict_to_workspace=self.restrict_to_workspace,
        )

    def subagent_tool_names(self) -> list[str]:
        """子执行真实存在的工具名（``scope="subagent"``）；团队档案求交要用活目录。"""
        return list(self._build_tools().names())

    def _build_tools(
        self,
        workspace: Path | None = None,
        tools_config: ToolsConfig | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> ToolRegistry:
        """Build an isolated subagent tool registry via ToolLoader."""
        root = self.workspace if workspace is None else workspace
        registry = ToolRegistry()
        cfg = tools_config if tools_config is not None else self._subagent_tools_config()
        ctx = ToolContext(
            config=cfg,
            workspace=str(root.resolve()),
            exec_session_manager=self._exec_session_manager,
            file_state_store=FileStates(),
            workspace_sandbox=workspace_sandbox_status(
                restrict_to_workspace=cfg.restrict_to_workspace,
                workspace=root,
            ),
        )
        ToolLoader().load(ctx, registry, scope="subagent")
        if allowed_tools is not None:
            for name in registry.names():
                if name not in allowed_tools:
                    registry.unregister(name)
        return registry

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        origin_message_id: str | None = None,
        temperature: float | None = None,
        workspace_scope: WorkspaceScope | None = None,
        agent_id: str | None = None,
        *,
        runtime: LLMRuntime | None = None,
    ) -> str:
        """Spawn a subagent to execute a task in the background."""
        if runtime is None:
            runtime = self._compat_spawn_runtime()
        if temperature is not None:
            runtime = runtime.with_generation_overrides(temperature=temperature)
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin: SubagentOrigin = {
            "channel": origin_channel,
            "chat_id": origin_chat_id,
            "session_key": session_key,
            "llm_usage_source": current_llm_usage_source(),
        }

        status = SubagentStatus(
            task_id=task_id,
            label=display_label,
            task_description=task,
            started_at=time.monotonic(),
        )
        self._task_statuses[task_id] = status

        bg_task = asyncio.create_task(
            self._run_subagent(
                task_id,
                task,
                display_label,
                origin,
                status,
                runtime,
                origin_message_id,
                workspace_scope,
                agent_id=agent_id,
            )
        )
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task[str]) -> None:
            self._running_tasks.pop(task_id, None)
            self._task_statuses.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)

        logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def run_inline(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        origin_message_id: str | None = None,
        temperature: float | None = None,
        workspace_scope: WorkspaceScope | None = None,
        agent_id: str | None = None,
        *,
        runtime: LLMRuntime | None = None,
    ) -> str:
        """Run a subagent synchronously and return its result to the caller."""
        if runtime is None:
            runtime = self._compat_spawn_runtime()
        if temperature is not None:
            runtime = runtime.with_generation_overrides(temperature=temperature)
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin: SubagentOrigin = {
            "channel": origin_channel,
            "chat_id": origin_chat_id,
            "session_key": session_key,
            "llm_usage_source": current_llm_usage_source(),
        }
        status = SubagentStatus(
            task_id=task_id,
            label=display_label,
            task_description=task,
            started_at=time.monotonic(),
        )
        self._task_statuses[task_id] = status
        logger.info("Running inline subagent [{}]: {}", task_id, display_label)
        inline_task = asyncio.create_task(
            self._run_subagent(
                task_id,
                task,
                display_label,
                origin,
                status,
                runtime,
                origin_message_id,
                workspace_scope,
                agent_id=agent_id,
                announce=False,
            )
        )
        self._running_tasks[task_id] = inline_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)
        try:
            result = await inline_task
            if status.phase == "error" or status.stop_reason == "error":
                return ToolResult.error(result)
            return result
        finally:
            self._running_tasks.pop(task_id, None)
            self._task_statuses.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: SubagentOrigin,
        status: SubagentStatus,
        runtime: LLMRuntime,
        origin_message_id: str | None = None,
        workspace_scope: WorkspaceScope | None = None,
        agent_id: str | None = None,
        *,
        announce: bool = True,
    ) -> str:
        """Wait for capacity, then execute one subagent task."""
        status.phase = "queued"
        async with self._run_slots:
            status.phase = "initializing"
            return await self._run_admitted_subagent(
                task_id,
                task,
                label,
                origin,
                status,
                runtime,
                origin_message_id,
                workspace_scope,
                agent_id=agent_id,
                announce=announce,
            )

    async def _run_admitted_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: SubagentOrigin,
        status: SubagentStatus,
        runtime: LLMRuntime,
        origin_message_id: str | None = None,
        workspace_scope: WorkspaceScope | None = None,
        agent_id: str | None = None,
        *,
        announce: bool = True,
    ) -> str:
        """旧字符串接口：内部与团队路径共用 :meth:`_execute_admitted`。"""
        result = await self._execute_admitted(
            task_id,
            task,
            label,
            origin,
            status,
            runtime,
            origin_message_id,
            workspace_scope,
            agent_id,
            announce=announce,
        )
        return result.final_content

    async def execute_frozen(
        self,
        task: str,
        *,
        execution_id: str,
        label: str,
        origin: SubagentOrigin,
        agent: ResolvedAgent,
        runtime: LLMRuntime,
        origin_message_id: str | None = None,
        workspace_scope: WorkspaceScope | None = None,
        on_activity: "Callable[[Mapping[str, Any]], Any] | None" = None,
    ) -> SubagentExecutionResult:
        """可信冻结档案执行入口（契约 §5）：Team 成员走这里，绝不按 agentId 重读盘。

        与 ``spawn`` / ``run_inline`` 共用同一个 ``_run_slots`` 配额和同一个 Runner，
        所以不存在第二条 LLM 循环；差别只在结果形态（typed）与 ``announce=False``
        （成员结果不再唤醒 Leader）。取消在排队阶段或执行阶段都收敛成
        ``outcome="cancelled"``，让调度器能区分「被停掉」与「跑失败」。
        """
        status = SubagentStatus(
            task_id=execution_id,
            label=label,
            task_description=task,
            started_at=time.monotonic(),
        )
        self._task_statuses[execution_id] = status
        try:
            async with self._run_slots:
                status.phase = "initializing"
                return await self._execute_admitted(
                    execution_id,
                    task,
                    label,
                    origin,
                    status,
                    runtime,
                    origin_message_id=origin_message_id,
                    workspace_scope=workspace_scope,
                    announce=False,
                    frozen_agent=agent,
                    on_activity=on_activity,
                    require_model_preset=True,
                )
        except asyncio.CancelledError:
            status.phase = "error"
            return SubagentExecutionResult(
                execution_id=execution_id,
                outcome="cancelled",
                stop_reason="cancelled",
                final_content="",
                usage=status.usage,
            )
        finally:
            self._task_statuses.pop(execution_id, None)

    async def _execute_admitted(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: SubagentOrigin,
        status: SubagentStatus,
        runtime: LLMRuntime,
        origin_message_id: str | None = None,
        workspace_scope: WorkspaceScope | None = None,
        agent_id: str | None = None,
        *,
        announce: bool = True,
        frozen_agent: ResolvedAgent | None = None,
        on_activity: "Callable[[Mapping[str, Any]], Any] | None" = None,
        require_model_preset: bool = False,
    ) -> SubagentExecutionResult:
        """Execute the subagent task and announce the result."""
        logger.info("Subagent [{}] starting task: {}", task_id, label)

        async def _on_checkpoint(payload: dict[str, Any]) -> None:
            status.phase = payload.get("phase", status.phase)
            status.iteration = payload.get("iteration", status.iteration)

        def _typed(
            outcome: str,
            stop_reason: str,
            content: str,
            usage: LLMUsage | None = None,
            error_code: str | None = None,
        ) -> SubagentExecutionResult:
            return SubagentExecutionResult(
                execution_id=task_id,
                outcome=outcome,
                stop_reason=stop_reason,
                final_content=content,
                usage=usage,
                error_code=error_code,
            )

        try:
            root = workspace_scope.project_path if workspace_scope is not None else self.workspace
            cfg = None
            if workspace_scope is not None:
                cfg = self._subagent_tools_config()
                cfg.restrict_to_workspace = workspace_scope.restrict_to_workspace
            # 一次解析，工具与提示词共用同一个档案 —— 否则两者会各说各话。
            # 解析失败必须在这里短路：再往下走一步，allowed_tools 就是 None，
            # 裁剪被跳过，子 Agent 会拿到全量工具。
            if frozen_agent is not None:
                # 可信路径：档案已在运行创建时冻结，这里既不改读盘也不接受 None。
                agent = frozen_agent
                allowed_tools = frozen_agent.tool_names
            else:
                prep = self._prepare_agent(agent_id)
                if prep.rejected:
                    logger.info("Subagent [{}] 因档案无效被拒绝：{}", task_id, prep.error)
                    return _typed(
                        "failed", "invalid_profile",
                        prep.error or "Error: Agent 档案无效",
                        error_code="invalid_profile",
                    )
                agent = prep.agent
                allowed_tools = prep.allowed_tools
            # Construct from the agent workspace; the bound scope below supplies the project cwd.
            tools = self._build_tools(
                tools_config=cfg,
                allowed_tools=allowed_tools,
            )
            system_prompt = self._build_subagent_prompt(workspace=root, agent=agent)
            if require_model_preset and agent is not None and agent.model_id is not None:
                # 档案指定了 preset 却解析不出来：这必须是失败。悄悄沿用默认模型会让
                # 「界面显示 opus、实际跑 mini」变成不可见错误（验收 A17）。
                resolved_runtime = (
                    self._model_resolver(agent.model_id) if self._model_resolver else None
                )
                if resolved_runtime is None:
                    return _typed(
                        "failed", "invalid_profile",
                        f"Error: 模型 preset {agent.model_id!r} 无法解析，请重新选择可用模型。",
                        error_code="model_preset_unavailable",
                    )
                runtime = resolved_runtime
            else:
                runtime = self._apply_model_override(runtime, agent.model_id if agent else None)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]

            sess_key = origin.get("session_key")
            llm_timeout = (
                self._llm_wall_timeout_for_session(sess_key)
                if self._llm_wall_timeout_for_session
                else None
            )
            request_token = bind_request_context(RequestContext(
                channel=origin["channel"],
                chat_id=origin["chat_id"],
                message_id=origin_message_id,
                session_key=sess_key,
                runtime=runtime,
            ))
            token = bind_workspace_scope(workspace_scope) if workspace_scope is not None else None
            try:
                result = await self.runner.run(AgentRunSpec(
                    initial_messages=messages,
                    tools=tools,
                    runtime=runtime,
                    max_iterations=self.max_iterations,
                    max_tool_result_chars=self.max_tool_result_chars,
                    hook=_SubagentHook(task_id, status, on_activity),
                    max_iterations_message="Task completed but no final response was generated.",
                    finalize_on_max_iterations=False,
                    error_message=None,
                    checkpoint_callback=_on_checkpoint,
                    session_key=sess_key,
                    workspace=root,
                    llm_timeout_s=llm_timeout,
                    llm_usage_source=origin.get(
                        "llm_usage_source",
                        current_llm_usage_source(),
                    ),
                ))
            finally:
                if token is not None:
                    reset_workspace_scope(token)
                reset_request_context(request_token)
            status.phase = "done"
            status.stop_reason = result.stop_reason

            stop_reason = result.stop_reason
            if stop_reason == "error":
                final_result = result.error or "Error: subagent execution failed."
                final_status = "error"
                outcome = "failed"
                error_code: str | None = "subagent_error"
            else:
                final_result = result.final_content or "Task completed but no final response was generated."
                final_status = "ok"
                error_code = None
                # succeeded 只留给「runner 正常收尾且真的有最终答案」。
                # max_iterations / empty_final_response 沿用同一句兜底文案给旧调用方，
                # 但在结构化结果里必须是失败 —— 否则团队会把跑满轮次的半成品当成功汇总。
                outcome = (
                    "succeeded" if stop_reason == "completed" and result.final_content else "failed"
                )
                if outcome == "succeeded":
                    logger.info("Subagent [{}] completed successfully", task_id)
                else:
                    logger.warning(
                        "Subagent [{}] 未产出最终答案（stop_reason={}），按失败记",
                        task_id, stop_reason,
                    )
            # 先把结果对象算完再 announce：否则构造结果时一旦抛错，except 分支会
            # 再 announce 一次，同一份产出被回注两遍（对团队就是两个 Leader 回合）。
            typed = _typed(outcome, stop_reason, final_result, result.usage, error_code)
            if announce:
                await self._announce_result(
                    task_id,
                    label,
                    task,
                    final_result,
                    origin,
                    final_status,
                    origin_message_id,
                )
            return typed

        except Exception as e:
            status.phase = "error"
            status.error = str(e)
            logger.exception("Subagent [{}] failed", task_id)
            final_result = f"Error: {e}"
            if announce:
                await self._announce_result(
                    task_id,
                    label,
                    task,
                    final_result,
                    origin,
                    "error",
                    origin_message_id,
                )
            return _typed("failed", "error", final_result, error_code="subagent_exception")

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: SubagentOrigin,
        status: str,
        origin_message_id: str | None = None,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = "completed successfully" if status == "ok" else "failed"

        announce_content = render_template(
            "agent/subagent_announce.md",
            label=label,
            status_text=status_text,
            task=task,
            result=result,
        )

        # Inject as system message to trigger main agent.
        # Use session_key_override to align with the main agent's effective
        # session key (which accounts for unified sessions) so the result is
        # routed to the correct pending queue (mid-turn injection) instead of
        # being dispatched as a competing independent task.
        override = origin.get("session_key") or f"{origin['channel']}:{origin['chat_id']}"
        metadata: dict[str, Any] = {
            "injected_event": "subagent_result",
            "subagent_task_id": task_id,
        }
        if origin_message_id:
            metadata["origin_message_id"] = origin_message_id
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
            session_key_override=override,
            metadata=metadata,
        )

        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}", task_id, origin['channel'], origin['chat_id'])

    def _build_subagent_prompt(
        self,
        workspace: Path | None = None,
        agent: ResolvedAgent | None = None,
    ) -> str:
        """Build a focused system prompt for the subagent.

        给了档案时，在出厂模板前面拼一段身份 + 档案自定义提示词；技能摘要按
        档案的 ``skills`` 选择裁剪。没给档案时行为与一期完全一致。
        """
        from nanobot.agent.skills import SkillsLoader

        agent_workspace = self.workspace.expanduser().resolve()
        project_workspace = workspace.expanduser().resolve() if workspace else agent_workspace
        exclude = set(agent.skill_exclude) if agent is not None else None
        skills_summary = SkillsLoader(
            self.workspace,
            disabled_skills=self.disabled_skills,
        ).build_skills_summary(exclude=exclude, workspace=project_workspace)
        history_log = (
            str(agent_workspace / "memory" / "history.jsonl")
            if agent_workspace != project_workspace
            else "memory/history.jsonl"
        )
        base = render_template(
            "agent/subagent_system.md",
            workspace=str(project_workspace),
            agent_workspace=str(agent_workspace),
            history_log=history_log,
            skills_summary=skills_summary or "",
        )
        frozen = render_frozen_skills_section()
        if frozen:
            base = f"{base}\n\n{frozen}"
        if agent is None or not agent.prompt:
            return base
        identity = (
            f"你是{agent.name}，{agent.description}。"
            if agent.description
            else f"你是{agent.name}。"
        )
        return f"{identity}\n\n{agent.prompt}\n\n{base}"

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        tasks = [self._running_tasks[tid] for tid in self._session_tasks.get(session_key, [])
                 if tid in self._running_tasks and not self._running_tasks[tid].done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._exec_session_manager.terminate_by_owner(session_key)
        return len(tasks)

    async def close(self) -> None:
        """Cancel running subagents and close their shared exec sessions."""
        tasks = [task for task in self._running_tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._exec_session_manager.close_all()

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)

    def get_running_count_by_session(self, session_key: str) -> int:
        """Return the number of currently running subagents for a session."""
        tids = self._session_tasks.get(session_key, set())
        return sum(
            1 for tid in tids
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        )
