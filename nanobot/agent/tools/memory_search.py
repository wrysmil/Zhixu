"""LLM-callable tool for proactive long-term memory retrieval.

This is the "active recall" path (vs. Layer 4 automatic injection):
- Layer 4: passive, every turn, capped by token budget
- Tool: proactive, LLM-decided, no token budget cap, good for "search 3 weeks ago"
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Callable, ClassVar

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.context import ToolContext, current_request_session_key
from nanobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    from nanobot.memory.retrieval import RetrievalEngine
    from nanobot.session.manager import SessionManager


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
        required=["query"],
    )
)
class MemorySearchTool(Tool):
    """Proactive memory search tool callable by the LLM.

    Provide a ``retrieval_engine_provider`` that returns the current
    :class:`RetrievalEngine` instance, or ``None`` when retrieval is
    disabled.  The provider is called at every ``execute()`` so that the
    engine is always fresh (e.g. when the loop re-creates it after config
    changes).
    """

    name: ClassVar[str] = "memory_search"
    description: ClassVar[str] = (
        "从长期记忆中主动检索相关内容。当用户明确要求查找过去的信息、"
        "特定项目细节、历史决策或工具使用经验时调用此工具。"
        "此工具与 Layer 4 自动注入互补：自动注入每轮无感覆盖，"
        "此工具供 LLM 在多步推理中显式补查，不受 token 预算限制。"
        "返回格式化的 markdown 记忆列表。"
    )

    def __init__(
        self,
        retrieval_engine_provider: Callable[[], "RetrievalEngine | None"],
        sessions: "SessionManager | None" = None,
        workspace_ids_provider: Callable[[str], Sequence[str]] | None = None,
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

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:

        def provider() -> "RetrievalEngine | None":  # noqa: F401
            # RetrievalEngine type needed for annotation; imported inside to avoid circular
            attrs = getattr(ctx, "attributes", {})
            return attrs.get("retrieval_engine")

        def workspace_ids_provider(session_key: str) -> tuple[str, ...]:
            resolve = getattr(ctx, "attributes", {}).get("memory_workspace_ids")
            if resolve is None:
                return ("default",)
            return tuple(resolve(session_key))

        return cls(
            retrieval_engine_provider=provider,
            sessions=getattr(ctx, "sessions", None),
            workspace_ids_provider=workspace_ids_provider,
        )

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        attrs = getattr(ctx, "attributes", {})
        return attrs.get("retrieval_engine") is not None

    async def execute(self, *, query: str, max_tokens: int = 500, **kwargs: Any) -> str:
        engine = self._get_engine()
        if engine is None:
            return ""

        recent: list[dict[str, Any]] = []
        session_key = current_request_session_key()
        if session_key and self._sessions is not None:
            session = self._sessions.get_cached(session_key)
            if session is not None:
                recent = session.messages[-10:]  # last 10 messages as context

        try:
            result = await engine.retrieve(
                query=query,
                recent_messages=recent,
                max_tokens=max_tokens,
                workspace_ids=self._workspace_ids_for(session_key),
            )
            return result if result else ""
        except Exception:
            return ""

    def _workspace_ids_for(self, session_key: str | None) -> tuple[str, ...]:
        """解析当前会话可见集合，解析器缺失或异常时只检索全局。"""
        if self._workspace_ids_provider is None or not session_key:
            return ("default",)
        try:
            return tuple(self._workspace_ids_provider(session_key))
        except Exception:
            return ("default",)

    @property
    def read_only(self) -> bool:
        return True
