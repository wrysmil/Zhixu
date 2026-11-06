"""经验库工具：记录失败/任务经验。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.context import ToolContext, current_request_session_key
from nanobot.memory.experience import list_experiences, record_experience

if TYPE_CHECKING:
    from nanobot.memory.database import MemoryDatabase
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
        "记录一条沉淀条目到经验库（SQLite），按 kind 区分："
        "experience=一次任务怎么做的/成败原因；"
        "skill=可复用的标准步骤（需要 capability + steps）；"
        "pitfall=踩坑（需要 symptom + fix，按报错/症状召回）；"
        "decision=方案取舍（需要 context + choice，支持 supersede）；"
        "reference=外部参考指针（需要 pointer，不复制正文）。"
        "只写经验库，不要写进 MEMORY.md；长期规则和项目约定用 memory_write。"
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
                "kind": {
                    "type": "string",
                    "enum": ["experience", "skill", "pitfall", "decision", "reference"],
                    "description": "条目类型；按上面的说明选择",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "deprecated", "superseded"],
                },
                "title": {"type": "string"},
                "situation": {"type": "string", "description": "experience：发生了什么"},
                "action": {"type": "string", "description": "你做了什么"},
                "outcome": {
                    "type": "string",
                    "enum": ["failed", "partial", "completed"],
                },
                "root_cause": {"type": "string"},
                "solution": {"type": "string"},
                "verification": {"type": "string"},
                "capability": {"type": "string", "description": "skill：这项技能解决什么"},
                "prerequisites": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "skill：前置条件",
                },
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "skill：可执行步骤",
                },
                "symptom": {"type": "string", "description": "pitfall：症状/报错文本"},
                "fix": {"type": "string", "description": "pitfall：修复方式"},
                "context": {"type": "string", "description": "decision：背景"},
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "pros": {"type": "string"},
                            "cons": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                    "description": "decision：候选方案",
                },
                "choice": {"type": "string", "description": "decision：最终结论"},
                "rationale": {"type": "string", "description": "decision：选择理由"},
                "pointer": {
                    "type": "string",
                    "description": "reference：文件路径或链接",
                },
                "what_for": {"type": "string", "description": "reference：这个指针用来查什么"},
                "version": {"type": "string", "description": "skill：版本"},
                "supersedes": {
                    "type": "string",
                    "description": "被本条目替代的旧条目 id（skill/decision）",
                },
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "kind"],
        }

    async def execute(
        self,
        *,
        title: str,
        kind: str = "experience",
        status: str = "active",
        situation: str = "",
        action: str = "",
        outcome: str | None = None,
        root_cause: str = "",
        solution: str = "",
        verification: str = "",
        capability: str = "",
        prerequisites: Sequence[str] | None = None,
        steps: Sequence[str] | None = None,
        symptom: str = "",
        fix: str = "",
        context: str = "",
        options: Sequence[dict[str, Any]] | None = None,
        choice: str = "",
        rationale: str = "",
        pointer: str = "",
        what_for: str = "",
        version: str = "",
        supersedes: str = "",
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
            kind=kind,
            status=status,
            situation=situation,
            action=action,
            outcome=outcome,
            root_cause=root_cause,
            solution=solution,
            verification=verification,
            capability=capability,
            prerequisites=tuple(prerequisites or ()),
            steps=tuple(steps or ()),
            symptom=symptom,
            fix=fix,
            context=context,
            options=tuple(options or ()),
            choice=choice,
            rationale=rationale,
            pointer=pointer,
            what_for=what_for,
            version=version,
            supersedes=supersedes,
            tags=tuple(tags or ()),
        )
        label = {
            "experience": "经验",
            "skill": "技能",
            "pitfall": "踩坑",
            "decision": "决策",
            "reference": "参考",
        }.get(kind, "条目")
        return f"已记录{label}：{title}（{episode.id[:8]}）"


class ExperienceQueryTool(Tool):
    """Dream 专用的只读经验检索；不进入主 Agent 的自动发现。"""

    _plugin_discoverable = False
    name: ClassVar[str] = "experience_query"
    description: ClassVar[str] = (
        "只读检索经验库中的任务、失败、根因与解法。用于 Dream 复盘，不写数据。"
    )

    def __init__(
        self,
        database: "MemoryDatabase",
        workspace_ids: Sequence[str] = ("default",),
    ) -> None:
        self._database = database
        self._workspace_ids = tuple(workspace_ids) or ("default",)

    @property
    def read_only(self) -> bool:
        return True

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
                "kind": {
                    "type": "string",
                    "enum": ["experience", "skill", "pitfall", "decision", "reference"],
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "deprecated", "superseded"],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
        }

    async def execute(
        self,
        *,
        query: str,
        outcome: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 20,
        **_: Any,
    ) -> str:
        rows = list_experiences(
            self._database,
            workspace_ids=self._workspace_ids,
            query=query,
            outcome=outcome,
            kind=kind,
            status=status,
            limit=limit,
        )
        if not rows:
            return ""
        lines = ["# 经验库"]
        for row in rows:
            title = row.title or row.summary.splitlines()[0]
            lines.append(
                f"- [{row.kind}/{row.status}] {title}（{row.ended_at[:10]}）"
                if row.status != "active"
                else f"- [{row.kind}] {title}（{row.ended_at[:10]}）"
            )
            if row.root_cause:
                lines.append(f"  根因：{row.root_cause}")
            if row.solution:
                lines.append(f"  解法：{row.solution}")
            elif row.metadata.get("fix"):
                lines.append(f"  修复：{row.metadata['fix']}")
        return "\n".join(lines)
