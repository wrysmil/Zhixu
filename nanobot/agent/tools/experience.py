"""经验库工具：记录失败/任务经验，按需检索。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.context import ToolContext, current_request_session_key
from nanobot.memory.experience import list_experiences, record_experience

if TYPE_CHECKING:
    from nanobot.webui.memory_services import MemoryServices

ServicesHolder = "MemoryServices | Callable[[], MemoryServices | None] | None"


def _resolve_services(holder: ServicesHolder) -> "MemoryServices | None":
    if holder is None:
        return None
    if callable(holder):
        try:
            return holder()
        except Exception as exc:  # noqa: BLE001 - 解析失败不当成工具崩溃
            logger.warning("experience tool: services resolution failed: {!r}", exc)
            return None
    return holder


class ExperienceRecordTool(Tool):
    name: ClassVar[str] = "experience_record"
    description: ClassVar[str] = (
        "记录一条任务经验或失败：发生了什么、做了什么、结果、根因、解法、验证方式。"
        "只写经验库（SQLite），不要写进 MEMORY.md；"
        "长期规则和项目约定用 memory_write。"
    )

    def __init__(self, services: ServicesHolder = None) -> None:
        self._services_holder = services

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        attrs = getattr(ctx, "attributes", {}) or {}
        return cls(attrs.get("memory_services"))

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        attrs = getattr(ctx, "attributes", {}) or {}
        gate = attrs.get("memory_write_enabled")
        if callable(gate):
            try:
                return bool(gate())
            except Exception:  # noqa: BLE001 - 门控异常按关闭处理
                return False
        return attrs.get("memory_services") is not None

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "situation": {"type": "string", "description": "发生了什么"},
                "action": {"type": "string", "description": "你做了什么"},
                "outcome": {
                    "type": "string",
                    "enum": ["failed", "partial", "completed"],
                },
                "root_cause": {"type": "string"},
                "solution": {"type": "string"},
                "verification": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "situation", "outcome"],
        }

    async def execute(
        self,
        *,
        title: str,
        situation: str,
        action: str = "",
        outcome: str = "failed",
        root_cause: str = "",
        solution: str = "",
        verification: str = "",
        tags: Sequence[str] | None = None,
        **_: Any,
    ) -> str:
        services = _resolve_services(self._services_holder)
        if services is None:
            raise RuntimeError("经验库不可用（记忆功能可能已关闭）")
        session_key = current_request_session_key() or "unknown"
        episode = record_experience(
            services.database,
            workspace_id=services.workspace_id,
            session_id=session_key,
            title=title,
            situation=situation,
            action=action,
            outcome=outcome,
            root_cause=root_cause,
            solution=solution,
            verification=verification,
            tags=tuple(tags or ()),
        )
        return f"已记录经验：{title}（{episode.id[:8]}）"


class ExperienceSearchTool(Tool):
    name: ClassVar[str] = "experience_search"
    description: ClassVar[str] = (
        "从经验库检索过去做过的任务、失败、根因与解法。"
        "长期规则和项目约定不在经验库，直接读 MEMORY.md。"
    )

    def __init__(
        self,
        services: ServicesHolder = None,
        workspace_ids_provider: Callable[[str], Sequence[str]] | None = None,
    ) -> None:
        self._services_holder = services
        self._workspace_ids_provider = workspace_ids_provider

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        attrs = getattr(ctx, "attributes", {}) or {}
        return cls(
            attrs.get("memory_services"),
            workspace_ids_provider=attrs.get("memory_workspace_ids"),
        )

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        attrs = getattr(ctx, "attributes", {}) or {}
        return attrs.get("memory_services") is not None

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "outcome": {
                    "type": "string",
                    "enum": ["failed", "partial", "completed"],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
        }

    def _workspace_ids(self, session_key: str | None) -> tuple[str, ...]:
        if self._workspace_ids_provider is None or not session_key:
            return ("default",)
        try:
            return tuple(self._workspace_ids_provider(session_key))
        except Exception as exc:  # noqa: BLE001 - 退化为全局，但必须留痕
            logger.warning("experience search workspace resolution failed: {!r}", exc)
            return ("default",)

    async def execute(
        self,
        *,
        query: str,
        outcome: str | None = None,
        limit: int = 10,
        **_: Any,
    ) -> str:
        services = _resolve_services(self._services_holder)
        if services is None:
            return ""
        session_key = current_request_session_key()
        rows = list_experiences(
            services.database,
            workspace_ids=self._workspace_ids(session_key),
            query=query,
            outcome=outcome,
            limit=limit,
        )
        if not rows:
            return ""
        lines = ["# 经验库检索结果"]
        for row in rows:
            title = row.title or row.summary.splitlines()[0]
            lines.append(f"- [{row.outcome}] {title}（{row.ended_at[:10]}）")
            if row.root_cause:
                lines.append(f"  根因：{row.root_cause}")
            if row.solution:
                lines.append(f"  解法：{row.solution}")
        return "\n".join(lines)
