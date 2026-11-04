"""Context builder for assembling agent prompts."""

import base64
import mimetypes
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence, cast

from loguru import logger

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.agent.tools import image_generation as image_generation_tools
from nanobot.agent.tools import mcp as mcp_tools
from nanobot.agent.tools import sessions as session_tools
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.apps.cli import utils as cli_app_utils
from nanobot.bus.events import (
    INBOUND_META_RUNTIME_CONTROL,
    RUNTIME_CONTROL_SESSION_DISCARD,
    InboundMessage,
)
from nanobot.identity.catalog import (
    PERSONA_STATE_FILE,
    PERSONAS_SUBDIR,
    POLICIES_MD_NAME,
    is_safe_persona_stem,
    resolve_identity_dir,
)
from nanobot.identity.compiler import read_compiled
from nanobot.identity.store import IdentityStore, IdentityStoreError
from nanobot.memory import obs
from nanobot.memory.context import (
    RecalledMemorySection,
    select_memory_context,
)
from nanobot.memory.retrieval import trace as memory_trace
from nanobot.memory.retrieval.candidate import MemoryHit, hit_from_memory
from nanobot.runtime_context import (
    RUNTIME_CONTEXT_MESSAGE_META,
    RuntimeContextBlock,
    append_runtime_context,
)
from nanobot.security.workspace_access import WorkspaceScopeResolver
from nanobot.session.keys import last_channel_from_metadata
from nanobot.session.manager import Session
from nanobot.session.summary import SessionSummary
from nanobot.utils.helpers import (
    detect_image_mime,
    estimate_text_tokens,
    load_bundled_template,
)
from nanobot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from nanobot.memory.retrieval import RetrievalEngine
else:
    RetrievalEngine = Any  # type: ignore[misc,assignment]


#: 一次注入给记忆块的总预算（工作状态 + 核心事实 + 检索结果共用一份）。
MEMORY_CONTEXT_MAX_TOKENS = 700
#: 检索侧最多取多少条候选；真正的取舍由组装侧的预算决定。
MEMORY_CONTEXT_HIT_LIMIT = 10


def _strip_leading_h1(content: str) -> str:
    """剥掉正文首个一级标题——persona 文件用标题行承载展示名，注入时不需要。"""
    lines = content.splitlines()
    if lines and lines[0].lstrip().startswith("# "):
        return "\n".join(lines[1:])
    return content


PERSONA_HEADING = "## 当前人格"


def session_extra(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return persisted kwargs for turn-attached capabilities."""
    return (
        cli_app_utils.session_extra(metadata)
        | mcp_tools.session_extra(metadata)
        | session_tools.session_extra(metadata)
    )


async def handle_runtime_control(state: Any, msg: InboundMessage, tools: ToolRegistry) -> bool:
    from nanobot.memory.reload import RUNTIME_CONTROL_MEMORY_RELOAD as _MEM_RELOAD
    from nanobot.memory.reload import handle_memory_reload as _handle_mem_reload
    ctrl = msg.metadata.get(INBOUND_META_RUNTIME_CONTROL)
    if ctrl == RUNTIME_CONTROL_SESSION_DISCARD:
        await state.discard_session(msg.session_key)
        return True
    if ctrl == _MEM_RELOAD:
        await _handle_mem_reload(msg.metadata)
        return True
    return await image_generation_tools.handle_runtime_control(state, msg, tools)


@dataclass(frozen=True, slots=True)
class PersistedPromptContextResolver:
    """Restore prompt routing context when no inbound message is available."""

    workspace_scopes: WorkspaceScopeResolver
    unified_session: bool = False

    def __call__(self, session: Session) -> tuple[str | None, Path]:
        channel = session.key.split(":", 1)[0] if ":" in session.key else None
        if self.unified_session:
            route = last_channel_from_metadata(session.metadata)
            if route is not None:
                channel = route[0]
        scope = self.workspace_scopes.for_turn(
            channel=channel,
            message_metadata=None,
            session_metadata=session.metadata,
        )
        return channel, scope.project_path


@dataclass(frozen=True, slots=True)
class TranscriptInput:
    """Raw turn inputs from which ``ContextBuilder`` assembles a transcript."""

    history: list[dict[str, Any]]
    current_message: str | None
    media: Sequence[str] | None = None
    current_role: str = "user"
    session_summary: SessionSummary | None = None
    runtime_context_blocks: Sequence[RuntimeContextBlock] | None = None

    @property
    def message_count(self) -> int:
        """Number of boundary-preserving messages in the assembled transcript."""
        return 1 + len(self.history) + (self.current_message is not None)


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "AGENT.md", "USER.md"]
    # 出厂模板也照常注入的文件：SOUL.md（本体）与 AGENT.md（方法论）是知序出厂
    # 就带的行为基线，不该因为「用户没改过」而从系统提示里消失——没改过正是
    # 出厂状态的定义。AGENTS.md 是项目级指令，USER.md 是派生档案，两者仍跳过
    # 出厂态（前者归项目目录，后者由记忆系统生成）。
    _SKIPPABLE_DEFAULTS = {"AGENTS.md", "USER.md"}

    def __init__(
        self,
        workspace: Path,
        timezone: str | None = None,
        disabled_skills: list[str] | None = None,
        active_retrieval_enabled: bool = False,
        retrieval_engine: RetrievalEngine | None = None,
        memory_enabled_provider: "Callable[[], bool] | None" = None,
    ):
        self.workspace = workspace
        self.timezone = timezone
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace, disabled_skills=set(disabled_skills) if disabled_skills else None)
        # Phase 3 Layer 4 Active Retrieval (T-13): disabled by default; the
        # ``RetrievalEngine`` instance is supplied externally (see
        # ``AgentLoop``) so this builder stays free of retrieval dependencies.
        self._active_retrieval_enabled = active_retrieval_enabled
        self._retrieval_engine = retrieval_engine
        # Hot-switchable user-facing memory toggle. ``None`` preserves legacy
        # behaviour (always-on when the loop registered extraction). When set,
        # every retrieval decision reads the latest ``memory_enabled`` value
        # via the provider closure, so toggling takes effect without restarting
        # the gateway.
        self._memory_enabled_provider = memory_enabled_provider
        # 核心事实的库句柄：与 AgentLoop 的回退装配同源，按需解析并缓存。
        self._core_services: Any | None = None

    def _should_retrieve(self) -> bool:
        """Combine wiring flags with the hot-switchable user toggle.

        Returns ``True`` only when Layer 4 retrieval is wired AND the user's
        ``memory_enabled`` toggle is on (or no provider is wired, in which case
        we preserve the original ``active_retrieval_enabled`` semantics).
        """
        if not self._active_retrieval_enabled or self._retrieval_engine is None:
            return False
        if self._memory_enabled_provider is not None and not self._memory_enabled_provider():
            return False
        return True

    async def _build_memory_section_with_ids(
        self,
        *,
        query: str,
        recent_messages: list[Any],
        workspace_ids: Sequence[str] | None = None,
    ) -> tuple[str, list[str]]:
        """检索 + 核心事实在这里组装成一段记忆块，并返回本次注入的事实 id。

        返回值第一项是 :class:`RecalledMemorySection`：字符串值可直接拼接，
        ``hits`` 携带结构化条目，供同步的 prompt 构建器连同工作状态与核心
        事实一起走统一的组装函数（去重 / 信任说明 / 总预算只有一处）。

        Failure-isolated: 检索异常被吞掉并记日志，system prompt 构建绝不因
        检索失败而中断。未启用 / 引擎缺失 → ``("", [])``。
        """
        if not self._should_retrieve():
            return "", []
        engine = cast(Any, self._retrieval_engine)
        if engine is None:
            return "", []

        try:
            hits = await engine.search_hits(
                query=query,
                recent_messages=recent_messages,
                workspace_ids=workspace_ids,
                limit=MEMORY_CONTEXT_HIT_LIMIT,
            )
            # search_hits 内部已调用 trace.start；在这里**立刻**取 id，避免同一
            # 回合后续的工具检索覆盖 ContextVar 后串错关联。
            trace_id = memory_trace.current_id()
        except Exception as exc:  # noqa: BLE001 - 记忆注入失败不该打断对话
            logger.warning(
                "Active retrieval failed; continuing without recalled memory: {}: {!r}",
                type(exc).__name__,
                exc,
            )
            return "", []

        recalled: list[MemoryHit] = list(hits or ())
        selection = select_memory_context(
            core_hits=self._load_core_hits(),
            working_text="",
            recalled_hits=recalled,
            max_tokens=MEMORY_CONTEXT_MAX_TOKENS,
            count_tokens=estimate_text_tokens,
        )
        # 只有「事实」类召回参与引用评分；情节条目的 id 不是记忆 id。
        recalled_fact_keys: set[tuple[str, str]] = {
            (hit.kind, hit.record_id) for hit in recalled if hit.kind == "memory"
        }
        cited = [
            hit.record_id
            for hit in selection.included
            if (hit.kind, hit.record_id) in recalled_fact_keys
        ]
        section = RecalledMemorySection(selection.text, recalled)
        # 关联 id 与 query 随载体透传：注入侧打日志时才能与检索侧对上。
        section.query = query
        section.trace_id = trace_id
        return section, cited

    def _core_memory_services(self) -> Any | None:
        """核心事实的来源：与 ``AgentLoop`` 回退装配同一个库（``workspace_id=default``）。

        不在这里建库：库不存在就说明还没有事实，无需为构建 prompt 创建库。库
        一旦存在就缓存服务实例，避免每轮重复建 schema。
        """
        if self._core_services is not None:
            return self._core_services
        if not (self.workspace / "memory" / "state.db").exists():
            return None
        try:
            from nanobot.memory.provenance import GLOBAL_MEMORY_WORKSPACE_ID
            from nanobot.webui.memory_services import MemoryServices

            self._core_services = MemoryServices.for_workspace(
                GLOBAL_MEMORY_WORKSPACE_ID, self.workspace
            )
        except Exception as exc:  # noqa: BLE001 - 记忆读失败不该打断对话
            logger.warning(
                "Core memory services unavailable; injecting no core facts: {}: {!r}",
                type(exc).__name__,
                exc,
            )
            return None
        return self._core_services

    def _load_core_hits(self) -> list[MemoryHit]:
        """每轮从 SQLite 当前有效记录选取核心事实（workspace + 有效性 + 重要性）。"""
        if self._memory_enabled_provider is not None and not self._memory_enabled_provider():
            return []
        services = self._core_memory_services()
        if services is None:
            return []
        try:
            from nanobot.memory.lifecycle import select_core_facts

            with services.database.connect() as conn:
                facts = select_core_facts(conn, workspace_id=services.workspace_id)
            return [hit_from_memory(fact) for fact in facts]
        except Exception as exc:  # noqa: BLE001 - 记忆读失败不该打断对话
            logger.warning(
                "Core memory read failed; injecting no core facts: {}: {!r}",
                type(exc).__name__,
                exc,
            )
            return []

    def _compose_memory_block(
        self,
        *,
        working_text: str | None,
        recalled_section: str | None,
        include_memory: bool,
        track_memory_access: bool = True,
    ) -> str:
        """唯一的记忆组装入口：工作状态 + 核心事实 + 检索结果。

        ``recalled_section`` 只接受检索引擎给出的 :class:`RecalledMemorySection`
        （带结构化条目）。没有结构化条目的已渲染段落不再注入——注入路径只有
        这一条，否则去重与预算就形同虚设。
        """
        recalled = getattr(recalled_section, "hits", None)
        if recalled_section and recalled is None:
            logger.debug(
                "Ignoring caller-rendered memory section without structured hits; "
                "recalled memory must come from the retrieval engine"
            )
        hits = list(cast(Sequence[MemoryHit], recalled)) if recalled is not None else []
        selection = select_memory_context(
            core_hits=self._load_core_hits() if include_memory else [],
            working_text=working_text,
            recalled_hits=hits,
            max_tokens=MEMORY_CONTEXT_MAX_TOKENS,
            count_tokens=estimate_text_tokens,
        )
        self._log_injection(
            selection,
            query=getattr(recalled_section, "query", ""),
            trace_id=getattr(recalled_section, "trace_id", ""),
        )
        if track_memory_access and selection.included:
            try:
                from nanobot.memory.retrieval.store_adapter import MemoryStoreAdapter

                services = self._core_memory_services()
                if services is not None:
                    MemoryStoreAdapter(services.database).record_accesses(selection.included)
            except Exception as exc:  # noqa: BLE001 - 统计失败不能影响 prompt
                logger.warning("Memory reference tracking failed during injection: {!r}", exc)
        return selection.text

    @staticmethod
    def _log_injection(selection: Any, *, query: str, trace_id: str = "") -> None:
        """注入环节唯一日志点：最终块 + included/dropped + 预算提示。

        检索侧 ``trace.injection`` 只覆盖召回块；这里才是「工作状态 + 核心事实 +
        召回」去重、预算裁剪之后真正进入 system prompt 的内容。两者用同一个
        ``trace_id`` 串联。
        """
        trace_id = trace_id or memory_trace.current_id()
        included = list(getattr(selection, "included", ()))
        dropped = list(getattr(selection, "dropped", ()))
        text = getattr(selection, "text", "") or ""
        hint = bool(getattr(selection, "hint", False))
        if not text and not dropped and not query and not trace_id:
            # 没有检索（无 query、无 trace）也没有条目：本轮不需要记忆，不刷日志。
            return
        if not text and not dropped:
            obs.emit(
                "injected",
                "【记忆注入·组装】空（无记忆进入 system prompt）",
                trace_id=trace_id,
                source="agent_context",
                query=obs.clip(query, limit=120),
                ids=[],
                dropped=0,
                hint=hint,
            )
            return
        lines = [
            "【记忆注入·组装】"
            f"trace={trace_id or '-'} 注入={len(included)} 条 "
            f"丢弃={len(dropped)} 条 预算提示={'是' if hint else '否'} "
            f"字符={len(text)} 估算tokens=~{len(text) // 2}",
        ]
        if included:
            lines.append("  · 进入上下文:")
            for hit in included:
                lines.append(f"      [{hit.kind}:{hit.record_id[:8]}] {obs.clip(hit.content)}")
        if dropped:
            lines.append("  · 被去重/预算丢弃:")
            for hit in dropped:
                lines.append(f"      [丢弃] [{hit.kind}:{hit.record_id[:8]}] {obs.clip(hit.content)}")
        if text:
            lines.append("  ---------- 注入内容开始 ----------")
            lines.extend(f"  {line}" for line in text.splitlines())
            lines.append("  ---------- 注入内容结束 ----------")
        obs.emit(
            "injected",
            "\n".join(lines),
            trace_id=trace_id,
            source="agent_context",
            query=obs.clip(query, limit=120),
            ids=[hit.record_id for hit in included],
            dropped_ids=[hit.record_id for hit in dropped],
            dropped=len(dropped),
            hint=hint,
            chars=len(text),
            est_tokens=len(text) // 2,
        )

    def build_system_prompt(
        self,
        *,
        channel: str | None = None,
        session_summary: SessionSummary | None = None,
        workspace: Path | None = None,
        include_memory: bool = True,
        track_memory_access: bool = True,
        retrieved_memory_section: str | None = None,
        working_memory_section: str | None = None,
        role_section: str | None = None,
        skill_exclude: set[str] | None = None,
    ) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills.

        ``role_section`` / ``skill_exclude`` 是**纯参数**：Team Leader 用它们注入角色与
        裁剪技能，不改 ``self.skills`` —— 那个加载器是所有会话共享的，写回去等于让
        一个团队回合影响普通聊天。
        """
        root = workspace or self.workspace
        parts = [self._get_identity(channel=channel, workspace=root)]
        if role_section:
            # 角色排在身份之后、bootstrap 之前：它框定的是「这一回合我以什么身份行事」，
            # 放在规则文件之后会让下面的通用规则读起来像被角色覆盖。
            parts.append(role_section)

        bootstrap = self._load_bootstrap_files(root)
        if bootstrap:
            parts.append(bootstrap)

        policies = self._load_policies_section()
        if policies:
            parts.append(policies)

        parts.append(render_template("agent/tool_contract.md"))

        project_path = root.expanduser().resolve()
        if project_path != self.workspace.expanduser().resolve():
            parts.append(
                "# Current Project\n\n"
                f"Working directory: {project_path}\n"
                "Use it as the default root for project files and relative tool paths."
            )

        # Working Memory：排在长期记忆**之前**——「我现在在做什么」是
        # 「我平时知道什么」的语境，反过来会让长期记忆抢掉当前任务的前景。
        # 门控（开关 / subagent / dream）全在 ``AgentLoop`` 侧求值完毕，这里
        # 只组装：工作状态、核心事实、检索结果由同一个函数去重并共用一份预算。
        memory_block = self._compose_memory_block(
            working_text=working_memory_section,
            recalled_section=retrieved_memory_section,
            include_memory=include_memory,
            track_memory_access=track_memory_access,
        )
        if memory_block:
            parts.append(memory_block)

        excluded_skills = skill_exclude or set()
        active_skills = [
            name for name in self.skills.get_always_skills() if name not in excluded_skills
        ]
        if active_skills:
            active_content = self.skills.load_skills_for_context(active_skills)
            if active_content:
                parts.append(f"# Active Skills\n\n{active_content}")

        skills_summary = self.skills.build_skills_summary(
            exclude=set(active_skills) | excluded_skills,
            workspace=root,
        )
        if skills_summary:
            parts.append(render_template("agent/skills_section.md", skills_summary=skills_summary))

        if session_summary:
            parts.append(
                "[Archived Context Summary]\n\n"
                f"Previous conversation summary (last active {session_summary['last_active']}):\n"
                f"{session_summary['text']}"
            )

        return "\n\n---\n\n".join(parts)

    def _get_identity(self, channel: str | None = None, workspace: Path | None = None) -> str:
        """Get the core identity section."""
        root = workspace or self.workspace
        workspace_path = str(root.expanduser().resolve())
        agent_workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return render_template(
            "agent/identity.md",
            workspace_path=workspace_path,
            agent_workspace_path=agent_workspace_path,
            runtime=runtime,
            platform_policy=render_template("agent/platform_policy.md", system=system),
            channel=channel or "",
        )

    @staticmethod
    def _merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
        if isinstance(left, str) and isinstance(right, str):
            if not left:
                return right
            if not right:
                return left
            return f"{left}\n\n{right}"

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [
                    cast(dict[str, Any], item)
                    if isinstance(item, dict)
                    else {"type": "text", "text": str(item)}
                    for item in cast(list[Any], value)
                ]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)

    def _load_bootstrap_files(self, workspace: Path | None = None) -> str:
        """Load project instructions plus the agent's global profile files.

        Identity files resolve to their rule-compiled ``identity/runtime/``
        products when that set is fresh, and fall back to the source file's
        full text otherwise (never compiled, stale, or unreadable). The
        fallback is the safe default: an edit that has not been recompiled
        still takes effect, so compilation is a token optimization rather than
        a switch that can silently silence a user's persona.
        """
        parts: list[str] = []
        project_root = workspace or self.workspace
        identity_dir = resolve_identity_dir(self.workspace)
        compiled = read_compiled(self.workspace)
        sources: list[tuple[str, Path, str | None]] = [
            ("AGENTS.md", project_root, None),  # project-level instructions: do not move
            # Identity files: prefer identity/ dir, fall back to workspace root for unmigrated old workspaces
            (
                "SOUL.md",
                identity_dir if (identity_dir / "SOUL.md").exists() else self.workspace,
                "identity_core",
            ),
            ("AGENT.md", identity_dir, "agent_behavior"),
            (
                "USER.md",
                identity_dir if (identity_dir / "USER.md").exists() else self.workspace,
                "user_profile_core",
            ),
        ]

        for filename, root, compiled_key in sources:
            section = self._render_bootstrap_file(filename, root, compiled, compiled_key)
            if section:
                parts.append(section)
            if filename == "SOUL.md":
                persona = self._load_persona_section()
                if persona:
                    parts.append(persona)

        return "\n\n".join(parts) if parts else ""

    def _render_bootstrap_file(
        self,
        filename: str,
        root: Path,
        compiled: dict[str, str] | None,
        compiled_key: str | None,
    ) -> str:
        """渲染单个 bootstrap 段；出厂未定制或缺失时返回空串。"""
        if compiled is not None and compiled_key is not None:
            body = compiled.get(compiled_key, "")
            if body.strip():
                return f"## {filename}\n\n{body}"
        file_path = root / filename
        if not file_path.exists():
            return ""
        content = file_path.read_text(encoding="utf-8")
        if filename == "SOUL.md" and self._is_template_content(content, "legacy/SOUL.md"):
            content = load_bundled_template("SOUL.md") or content
        if not content.strip():
            return ""
        if filename in self._SKIPPABLE_DEFAULTS and self._is_template_content(content, filename):
            return ""
        return f"## {filename}\n\n{content}"

    def _load_persona_section(self) -> str:
        """把激活的 persona 渲染成表现层段落。

        persona 是可切换的表现层，SOUL 是不可切换的本体，两者分段注入让
        「改本体」与「切表现」互不干扰。文件内容原样注入、只剥一级标题，
        不解析字段——用户改标题不会让 persona 静默失效。未激活 / 目标缺失 /
        空文件时静默跳过，不报错。

        这里**不**跳过出厂 persona 预设，与 SOUL 走同一套判断
        （``_SKIPPABLE_DEFAULTS`` 同样不含 SOUL.md）：出厂 persona 同样是用户
        显式激活才生效的东西，而「是否激活」已由 ``active_persona`` 决定。
        早先这里对 persona 额外做了 ``_is_template_content`` 跳过，结果所有
        未改过的出厂 persona 全部不注入——用户选了 companion 却毫无反应，
        必须手动改一个字才生效。是否注入只该由激活态决定，不该由内容像不像
        出厂模板决定。
        """
        identity_dir = resolve_identity_dir(self.workspace)
        try:
            stem = (identity_dir / PERSONA_STATE_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
        # 状态文件是用户可写的，不能当路径用：谓词挡住分隔符/NUL/点号名，
        # 随后的 read 失败再兜住其余越界形态。
        if not is_safe_persona_stem(stem):
            return ""
        try:
            content = (identity_dir / PERSONAS_SUBDIR / f"{stem}.md").read_text(encoding="utf-8")
        except OSError:
            return ""
        if not content.strip():
            return ""
        body = _strip_leading_h1(content).strip()
        if not body:
            return ""
        return f"{PERSONA_HEADING}：{stem}\n\n{body}"

    def _load_policies_section(self) -> str:
        """注入 ``identity/prompts/policies.md`` 作为策略段落。

        出厂模板是空壳（只有引导语），按模板跳过——所以未定制过的 workspace 零成本。
        只做追加，不实现文件自称的「覆写内置默认」：知序 没有可被覆写的内置默认
        节，建一套没有对手的覆盖机制是空转。语义写清楚比假装支持更诚实。
        """
        try:
            content = IdentityStore(self.workspace).read_file(POLICIES_MD_NAME)
        except IdentityStoreError:
            return ""
        if not content.strip():
            return ""
        if self._is_template_content(content, POLICIES_MD_NAME):
            return ""
        return f"## Policies\n\n{content}"

    @staticmethod
    def _is_template_content(content: str, template_path: str) -> bool:
        """Check if *content* is identical to the bundled template (user hasn't customized it)."""
        tpl = load_bundled_template(template_path)
        if tpl is not None:
            return content.strip() == tpl.strip()
        return False

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str | None,
        *,
        media: list[str] | None = None,
        channel: str | None = None,
        current_role: str = "user",
        session_summary: SessionSummary | None = None,
        runtime_context_blocks: Sequence[RuntimeContextBlock] | None = None,
        workspace: Path | None = None,
        include_memory: bool = True,
        track_memory_access: bool = True,
        retrieved_memory_section: str | None = None,
        working_memory_section: str | None = None,
    ) -> list[dict[str, Any]]:
        """Compatibility wrapper for callers that need merged adjacent roles."""
        messages = self.build_transcript(
            TranscriptInput(
                history=history,
                current_message=current_message,
                media=media,
                current_role=current_role,
                session_summary=session_summary,
                runtime_context_blocks=runtime_context_blocks,
            ),
            channel=channel,
            workspace=workspace,
            include_memory=include_memory,
            track_memory_access=track_memory_access,
            retrieved_memory_section=retrieved_memory_section,
            working_memory_section=working_memory_section,
        )
        if current_message is None:
            return messages
        current = messages[-1]
        if len(messages) < 2 or messages[-2].get("role") != current.get("role"):
            return messages

        merged = dict(messages[-2])
        merged["content"] = self._merge_message_content(
            merged.get("content"),
            current.get("content"),
        )
        current_meta = current.get("_meta")
        if current.get("role") == "user" and isinstance(current_meta, dict):
            internal_meta = dict(merged.get("_meta") or {})
            internal_meta.update(cast(dict[str, Any], current_meta))
            merged["_meta"] = internal_meta
        return [*messages[:-2], merged]

    def build_transcript(
        self,
        transcript: TranscriptInput,
        *,
        channel: str | None = None,
        workspace: Path | None = None,
        include_memory: bool = True,
        track_memory_access: bool = True,
        retrieved_memory_section: str | None = None,
        working_memory_section: str | None = None,
        role_section: str | None = None,
        skill_exclude: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build a model transcript while preserving the fresh-turn boundary."""
        root = workspace or self.workspace
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self.build_system_prompt(
                    channel=channel,
                    session_summary=transcript.session_summary,
                    workspace=root,
                    include_memory=include_memory,
                    track_memory_access=track_memory_access,
                    retrieved_memory_section=retrieved_memory_section,
                    working_memory_section=working_memory_section,
                    role_section=role_section,
                    skill_exclude=skill_exclude,
                ),
            },
            *transcript.history,
        ]
        if transcript.current_message is None:
            return messages

        current = self.build_current_message(
            transcript.current_message,
            media=list(transcript.media) if transcript.media else None,
            current_role=transcript.current_role,
            runtime_context_blocks=transcript.runtime_context_blocks,
        )
        messages.append(current)
        return messages

    def build_current_message(
        self,
        current_message: str,
        *,
        media: list[str] | None = None,
        current_role: str = "user",
        runtime_context_blocks: Sequence[RuntimeContextBlock] | None = None,
    ) -> dict[str, Any]:
        """Build only the fresh turn message without merging it into history."""
        content = self.build_user_content(current_message, image_paths=media)
        blocks: list[RuntimeContextBlock] = []
        if current_role == "user":
            blocks.extend(runtime_context_blocks or ())
            skill_context = self.skills.build_explicit_skill_runtime_context(current_message)
            if skill_context is not None and skill_context not in blocks:
                blocks.append(skill_context)
        merged, runtime_context_meta = append_runtime_context(content, blocks)
        current: dict[str, Any] = {"role": current_role, "content": merged}
        if current_role == "user" and runtime_context_meta is not None:
            current["_meta"] = {
                RUNTIME_CONTEXT_MESSAGE_META: runtime_context_meta,
            }
        return current

    def build_user_content(
        self,
        text: str,
        image_paths: list[str] | None,
    ) -> str | list[dict[str, Any]]:
        """Build user message content from prefiltered image paths."""
        if not image_paths:
            return text

        image_blocks: list[dict[str, Any]] = []
        for path in image_paths:
            p = Path(path)
            if not p.is_file():
                continue
            raw = p.read_bytes()
            # Re-detect from the bytes used for the request: the file may have
            # changed since attachment routing, and the data URL needs its MIME.
            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(raw).decode()
            image_blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
                "_meta": {"path": str(p)},
            })

        if not image_blocks:
            return text
        return image_blocks + [{"type": "text", "text": text}]
