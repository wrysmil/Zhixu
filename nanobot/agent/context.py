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
from nanobot.runtime_context import (
    RUNTIME_CONTEXT_MESSAGE_META,
    RuntimeContextBlock,
    append_runtime_context,
)
from nanobot.security.workspace_access import WorkspaceScopeResolver
from nanobot.session.keys import last_channel_from_metadata
from nanobot.session.manager import Session
from nanobot.session.summary import SessionSummary
from nanobot.utils.helpers import detect_image_mime, load_bundled_template
from nanobot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from nanobot.memory.retrieval import RetrievalEngine
    from nanobot.memory.retrieval.preprocessor import MemoryQueryPreprocessor
else:
    RetrievalEngine = Any  # type: ignore[misc,assignment]
    MemoryQueryPreprocessor = Any  # type: ignore[misc,assignment]


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
    _SKIPPABLE_DEFAULTS = {"AGENTS.md", "AGENT.md", "USER.md"}

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

    async def _build_memory_section(
        self,
        *,
        query: str,
        recent_messages: list[Any],
    ) -> str:
        """Return Layer 4 retrieval block (markdown), or ``""`` when disabled.

        Failure-isolated: any retrieval exception is swallowed and logged so
        the system prompt build never breaks because of retrieval.

        兼容薄壳：只返回注入块。需要本次注入的 ``memory_id`` 集合的调用方
        （WU-B 引用评分闭环）应使用 :meth:`_build_memory_section_with_ids`，
        以在 idle 提取时把同一批记忆作为 ``cited_memories`` 送进评分 prompt。
        """
        block, _ids = await self._build_memory_section_with_ids(
            query=query,
            recent_messages=recent_messages,
        )
        return block

    async def _build_memory_section_with_ids(
        self,
        *,
        query: str,
        recent_messages: list[Any],
        workspace_ids: Sequence[str] | None = None,
    ) -> tuple[str, list[str]]:
        """Return ``(retrieval block, 本次注入的 memory_id 列表)``。

        Failure-isolated: any retrieval exception is swallowed and logged so
        the system prompt build never breaks because of retrieval. Disabled /
        engine missing / gate skip → ``("", [])``。
        """
        if not self._should_retrieve():
            return "", []
        # Lazy import: keeps ``nanobot.agent.context`` importable without
        # pulling the retrieval module eagerly (preserves the historical
        # sync-only path for callers that never enable Layer 4).
        from nanobot.memory.retrieval.preprocessor import MemoryQueryPreprocessor

        try:
            prepared = MemoryQueryPreprocessor.prepare(query, recent_messages)
            if prepared.skip:
                return "", []
            retrieved = await self._retrieval_engine.retrieve_with_ids(
                query=prepared.cleaned_query,
                recent_messages=recent_messages,
                workspace_ids=workspace_ids,
            )
        except Exception:
            logger.warning(
                "Active retrieval failed; continuing without Layer 4 block"
            )
            return "", []
        block, ids = retrieved
        return block or "", list(ids)

    def build_system_prompt(
        self,
        *,
        channel: str | None = None,
        session_summary: SessionSummary | None = None,
        workspace: Path | None = None,
        include_memory: bool = True,
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
        # 只判空：空串与 None 同样不 append，避免拼出只有分隔线的空壳段落。
        if working_memory_section:
            parts.append(working_memory_section)

        if include_memory:
            memory = self.memory.read_memory()
            if memory and not self._is_template_content(memory, "memory/MEMORY.md"):
                parts.append(f"# Memory\n\n## Long-term Memory\n{memory}")

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

        # Layer 4 Active Retrieval block (T-13): only appended when the
        # feature is enabled AND the engine is wired AND a non-empty section
        # is supplied by the caller (typically pre-computed in
        # ``AgentLoop._build_turn`` to keep the sync prompt builder API).
        if (
            self._should_retrieve()
            and retrieved_memory_section
        ):
            parts.append(retrieved_memory_section)

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
