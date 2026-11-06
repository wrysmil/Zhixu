"""LLM-callable tool for proactive long-term memory retrieval.

This is the "active recall" path (vs. Layer 4 automatic injection):
- Layer 4: passive, every turn, capped by token budget
- Tool: proactive, LLM-decided, no token budget cap, good for "search 3 weeks ago"
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Callable, ClassVar

from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.context import ToolContext, current_request_session_key
from nanobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    from nanobot.memory.retrieval import RetrievalEngine
    from nanobot.session.manager import SessionManager
    from nanobot.webui.memory_services import MemoryServices


@tool_parameters(
    tool_parameters_schema(
        query=StringSchema(
            "记忆检索关键词（可包含主题、工具名、项目名等）",
            min_length=1,
            max_length=200,
        ),
        max_tokens=IntegerSchema(
            description="最大输出 token 数（默认 500）",
            minimum=50,
            maximum=2000,
        ),
        outcome=StringSchema(
            "只查某种结果：failed / partial / completed",
            enum=("failed", "partial", "completed"),
            nullable=True,
        ),
        kind=StringSchema(
            "只查某类沉淀条目：experience / skill / pitfall / decision / reference",
            enum=("experience", "skill", "pitfall", "decision", "reference"),
            nullable=True,
        ),
        status=StringSchema(
            "只查某种状态：active / deprecated / superseded",
            enum=("active", "deprecated", "superseded"),
            nullable=True,
        ),
        required=["query"],
    )
)
class MemorySearchTool(Tool):
    """Proactive retrieval over the experience library.

    Provide a ``retrieval_engine_provider`` that returns the current
    :class:`RetrievalEngine` instance, or ``None`` when retrieval is
    disabled.  The provider is called at every ``execute()`` so that the
    engine is always fresh (e.g. when the loop re-creates it after config
    changes).
    """

    name: ClassVar[str] = "memory_search"
    description: ClassVar[str] = (
        "记忆检索工具（主动检索，补充每轮的自动注入）：从经验库检索过去做过的任务、"
        "失败、根因与解法。当用户要求查找过去的信息、特定项目细节、历史决策或工具"
        "使用经验时调用；可按 kind（experience/skill/pitfall/decision/reference）、"
        "status、outcome 过滤。长期规则和项目约定不在经验库，直接读 MEMORY.md。"
        "返回格式化的 markdown 结果。"
    )

    def __init__(
        self,
        retrieval_engine_provider: Callable[[], "RetrievalEngine | None"],
        sessions: "SessionManager | None" = None,
        workspace_ids_provider: Callable[[str], Sequence[str]] | None = None,
        services_provider: Callable[[], "MemoryServices | None"] | None = None,
    ) -> None:
        self._get_engine = retrieval_engine_provider
        # RCA 2026-09-15 根因 3：此前 ``execute`` 里判断的是
        # ``hasattr(self, "_sessions")``，但全仓库没有任何代码给该工具实例注入
        # ``_sessions``（实测 instance dict keys == ['_get_engine']）→ 该分支恒
        # False → ``recent`` 恒为 ``[]``，工具拿不到会话上下文，随之撞上根因 2 的
        # ``short_without_context`` 门禁。改为在 ``create`` 阶段经
        # ``ToolContext.sessions`` 注入，与 ``long_task.py::_GoalToolsMixin`` 同模式。
        self._sessions = sessions
        self._workspace_ids_provider = workspace_ids_provider
        self._services_provider = services_provider

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:

        def provider() -> "RetrievalEngine | None":  # noqa: F401
            # RetrievalEngine type needed for annotation; imported inside to avoid circular
            attrs = getattr(ctx, "attributes", {})
            return attrs.get("retrieval_engine")

        def services_provider() -> "MemoryServices | None":
            holder = getattr(ctx, "attributes", {}).get("memory_services")
            if callable(holder):
                try:
                    return holder()
                except Exception as exc:  # noqa: BLE001 - 解析失败按不可用处理
                    logger.warning("memory_search services resolution failed: {!r}", exc)
                    return None
            return holder

        def workspace_ids_provider(session_key: str) -> tuple[str, ...]:
            resolve = getattr(ctx, "attributes", {}).get("memory_workspace_ids")
            if resolve is None:
                return ("default",)
            return tuple(resolve(session_key))

        return cls(
            retrieval_engine_provider=provider,
            sessions=getattr(ctx, "sessions", None),
            workspace_ids_provider=workspace_ids_provider,
            services_provider=services_provider,
        )

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        attrs = getattr(ctx, "attributes", {})
        return attrs.get("retrieval_engine") is not None or attrs.get("memory_services") is not None

    async def execute(
        self,
        *,
        query: str,
        max_tokens: int = 500,
        outcome: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        **kwargs: Any,
    ) -> str:
        session_key = current_request_session_key()
        workspace_ids = self._workspace_ids_for(session_key)
        engine = self._get_engine()

        if engine is not None:
            recent: list[dict[str, Any]] = []
            if session_key and self._sessions is not None:
                session = self._sessions.get_cached(session_key)
                if session is not None:
                    recent = session.messages[-10:]  # last 10 messages as context
            try:
                filters = {
                    name: value
                    for name, value in (
                        ("kind", kind),
                        ("status", status),
                        ("outcome", outcome),
                    )
                    if value
                }
                result = await engine.retrieve(
                    query=query,
                    recent_messages=recent,
                    max_tokens=max_tokens,
                    workspace_ids=workspace_ids,
                    **filters,
                )
                return result if result else ""
            except Exception as exc:  # noqa: BLE001 - 检索失败不应打断对话，但必须留痕
                # 静默返回空串会让模型以为「记忆里没有」，进而放弃记忆改用文件搜索
                # 之类的笨办法；异常必须落到日志里才能定位。
                logger.warning(
                    "memory_search retrieval failed: query={!r} session={} error={}: {!r}",
                    query,
                    session_key,
                    type(exc).__name__,
                    exc,
                )
                return ""

        # 无检索引擎时的结构化兜底：只服务带过滤条件的查询。
        if not (kind or status or outcome):
            return ""
        services = self._services_provider() if self._services_provider else None
        if services is None:
            return ""
        from nanobot.memory.experience import list_experiences

        rows = list_experiences(
            services.database,
            workspace_ids=workspace_ids,
            query=query,
            outcome=outcome,
            kind=kind,
            status=status,
            limit=10,
        )
        if not rows:
            return ""
        lines = ["# 经验库检索结果"]
        for row in rows:
            title = row.title or row.summary.splitlines()[0]
            suffix = f"/{row.status}" if row.status != "active" else ""
            lines.append(f"- [{row.kind}{suffix}] {title}（{row.ended_at[:10]}）")
            if row.root_cause:
                lines.append(f"  根因：{row.root_cause}")
            if row.solution:
                lines.append(f"  解法：{row.solution}")
            elif row.metadata.get("fix"):
                lines.append(f"  修复：{row.metadata['fix']}")
        return "\n".join(lines)

    def _workspace_ids_for(self, session_key: str | None) -> tuple[str, ...]:
        """解析当前会话可见集合，解析器缺失或异常时只检索全局。"""
        if self._workspace_ids_provider is None or not session_key:
            return ("default",)
        try:
            return tuple(self._workspace_ids_provider(session_key))
        except Exception as exc:  # noqa: BLE001 - 退化为全局，但必须留痕
            logger.warning(
                "memory_search workspace scope resolution failed, degrading to global: "
                "session={} error={}: {!r}",
                session_key,
                type(exc).__name__,
                exc,
            )
            return ("default",)

    @property
    def read_only(self) -> bool:
        return True
