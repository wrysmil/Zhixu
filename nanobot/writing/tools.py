"""创作团队专属工具：读文章、建素材、建/调提纲、更新正文。

这些工具**不进**通用 ToolLoader 目录：只有写作会话的可信运行上下文才会实例化，
普通聊天与克隆团队拿不到名字。所有身份（workspace / article / chat / session /
team / run / turn / member）由宿主构造，模型参数里一个都没有。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.schema import (
    ArraySchema,
    NumberSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.writing.apply import WritingActor, apply_change
from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import ArticleLimitError
from nanobot.writing.repository import (
    ArticleConflict,
    ArticleNotFound,
    ArticleRepository,
    ArticleRequestReplay,
)

READ_ROLES = frozenset({
    "writing-editor", "writing-researcher", "writing-architect",
    "writing-author", "writing-reviewer",
})
MATERIAL_ROLES = frozenset({"writing-editor", "writing-researcher"})
OUTLINE_ROLES = frozenset({"writing-editor", "writing-architect"})
ARTICLE_WRITE_ROLES = frozenset({"writing-editor", "writing-author"})


@dataclass
class WritingToolRuntime:
    """一次工具调用可用的可信上下文；字段全部来自服务端绑定与运行冻结快照。

    ``base_revision/base_digest`` 是写工具的 CAS 基线：运行开始时取本轮开工快照，
    读工具拿到最新版本后会刷新它，使「读新版本 → 重试」遵循方案 §6.4。
    """

    db: WritingDatabase
    article_id: str
    chat_id: str
    session_key: str
    team_id: str
    team_revision: int
    run_id: str
    turn_id: str
    member_id: str
    base_revision: int
    base_digest: str
    origin_message_id: str = ""
    cancelled: Callable[[], bool] = field(default=lambda: False)
    on_change: Callable[[Mapping[str, Any]], None] | None = None

    def guard(self, roles: frozenset[str]) -> ToolResult | None:
        if self.cancelled():
            return ToolResult.error("Error: writing_run_cancelled")
        if self.member_id not in roles:
            return ToolResult.error(
                f"Error: writing_permission_denied member={self.member_id}"
            )
        return None


class _WritingTool(Tool):
    _plugin_discoverable = False

    def __init__(self, runtime: WritingToolRuntime) -> None:
        self._runtime = runtime

    @property
    def read_only(self) -> bool:
        return False

    def _request_id(self, payload: Mapping[str, Any]) -> tuple[str, str]:
        encoded = json.dumps(
            dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = hashlib.sha256(encoded).hexdigest()
        return fingerprint, (
            f"tool:{self._runtime.run_id}:{self._runtime.turn_id}:"
            f"{self._runtime.member_id}:{fingerprint[:24]}"
        )

    def _apply(self, change: Mapping[str, Any]) -> ToolResult:
        runtime = self._runtime
        fingerprint, request_id = self._request_id(change)
        actor = WritingActor(
            member_id=runtime.member_id,
            run_id=runtime.run_id,
            turn_id=runtime.turn_id,
            message_id=runtime.origin_message_id,
        )
        try:
            result = apply_change(
                runtime.db,
                article_id=runtime.article_id,
                base_revision=runtime.base_revision,
                base_digest=runtime.base_digest,
                change=change,
                actor=actor,
                request_fingerprint=fingerprint,
                client_request_id=request_id,
            )
        except ArticleConflict as exc:
            return ToolResult.error(
                "Error: writing_conflict the article changed while this run was working; "
                "re-read it and try again. " + str(exc)
            )
        except ArticleRequestReplay:
            return ToolResult.error(
                "Error: writing_request_replay this call was already used with different content"
            )
        except ArticleLimitError as exc:
            return ToolResult.error(f"Error: writing_invalid {exc.message}")
        except ArticleNotFound:
            return ToolResult.error("Error: writing_article_not_found")
        if result.get("change_id") and runtime.on_change is not None:
            runtime.on_change({**result, "actor": runtime.member_id})
        return ToolResult(json.dumps(result, ensure_ascii=False))


@tool_parameters(
    tool_parameters_schema(
        required=[],
        additional_properties=False,
    )
)
class WritingReadArticleTool(_WritingTool):
    @property
    def name(self) -> str:
        return "writing_read_article"

    @property
    def read_only(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return (
            "Read the current article bound to this writing conversation: title, summary, "
            "body, outline, materials, revision and digest. Use the returned revision/digest "
            "as the basis for any edit. This tool never writes."
        )

    async def execute(self, **_kwargs: Any) -> ToolResult:
        denied = self._runtime.guard(READ_ROLES)
        if denied is not None:
            return denied
        try:
            article = ArticleRepository(self._runtime.db).get_article(
                self._runtime.article_id
            )["article"]
        except ArticleNotFound:
            return ToolResult.error("Error: writing_article_not_found")
        # 方案 §6.4：Agent 读到新版本后，后续写入应以该版本为基线重试。
        self._runtime.base_revision = int(article["revision"])
        self._runtime.base_digest = str(article["digest"])
        return ToolResult(json.dumps(article, ensure_ascii=False))


@tool_parameters(
    tool_parameters_schema(
        type=StringSchema(
            "Material kind; default note.",
            enum=["note", "text", "code", "link"],
        ),
        title=StringSchema("Short title for this material."),
        content=StringSchema("Material content; cite exact text rather than paraphrasing."),
        url=StringSchema("http(s) URL when type is link; otherwise empty."),
        source=StringSchema("Where this material came from."),
        note=StringSchema("Why it matters / what still needs checking."),
        required=["title", "content"],
    )
)
class WritingCreateMaterialTool(_WritingTool):
    @property
    def name(self) -> str:
        return "writing_create_material"

    @property
    def description(self) -> str:
        return (
            "Create one material item on the current article and persist it immediately. "
            "Only the editor-in-chief and the researcher may call this. Writes are rejected "
            "if anyone else changed the article since your last successful write in this turn."
        )

    async def execute(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        title: str,
        content: str,
        type: str = "note",  # noqa: A002 - 模型可见参数名
        url: str = "",
        source: str = "",
        note: str = "",
        **_kwargs: Any,
    ) -> ToolResult:
        denied = self._runtime.guard(MATERIAL_ROLES)
        if denied is not None:
            return denied
        material = {
            "type": type,
            "title": title,
            "content": content,
            "url": url,
            "source": source,
            "note": note,
        }
        return self._apply({"kind": "material", "material": material})


@tool_parameters(
    tool_parameters_schema(
        items=ArraySchema(
            ObjectSchema(
                text=StringSchema("One outline item; one claim or section per item."),
            ),
            description="Outline items in order.",
            min_items=1,
        ),
        mode=StringSchema(
            "replace the whole outline or append after the existing items.",
            enum=["replace", "append"],
        ),
        required=["items"],
    )
)
class WritingCreateOutlineTool(_WritingTool):
    @property
    def name(self) -> str:
        return "writing_create_outline"

    @property
    def description(self) -> str:
        return (
            "Create or adjust the article outline and persist it immediately. "
            "Only the editor-in-chief and the structure editor may call this. "
            "Use replace for a full revised outline, append to add sections. "
            "Writes are rejected if anyone else changed the article since your last "
            "successful write in this turn."
        )

    async def execute(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        items: list[Any],
        mode: str = "replace",
        **_kwargs: Any,
    ) -> ToolResult:
        denied = self._runtime.guard(OUTLINE_ROLES)
        if denied is not None:
            return denied
        return self._apply({"kind": "outline", "items": items, "mode": mode})


@tool_parameters(
    tool_parameters_schema(
        content=StringSchema("Text to write. Never include the untouched surrounding text."),
        mode=StringSchema(
            "append to the end, replace the selected range (requires start/end), "
            "or replace the entire body.",
            enum=["append", "replace_range", "replace_body"],
        ),
        start=NumberSchema(
            description="Selection start in Python code points (only replace_range)."
        ),
        end=NumberSchema(
            description="Selection end in Python code points (only replace_range)."
        ),
        summary=StringSchema("One sentence describing what changed and why."),
        required=["content", "mode"],
    )
)
class WritingUpdateArticleTool(_WritingTool):
    @property
    def name(self) -> str:
        return "writing_update_article"

    @property
    def description(self) -> str:
        return (
            "Write text into the current article and persist it immediately. "
            "Only the editor-in-chief and the writer may call this. "
            "For a selection rewrite use replace_range with the exact code-point range from "
            "the author's selection; append only adds a new section at the end; replace_body "
            "requires an explicit whole-article request. Writes are rejected if anyone else "
            "changed the article since your last successful write in this turn, so the "
            "author's newer edits are never overwritten."
        )

    async def execute(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        content: str,
        mode: str,
        start: int | None = None,
        end: int | None = None,
        summary: str = "",
        **_kwargs: Any,
    ) -> ToolResult:
        denied = self._runtime.guard(ARTICLE_WRITE_ROLES)
        if denied is not None:
            return denied
        change: dict[str, Any] = {"kind": "body", "mode": mode, "content": content}
        if mode == "replace_range":
            change["start"] = start
            change["end"] = end
        if summary.strip():
            change["summary"] = summary.strip()
        return self._apply(change)


def build_writing_tools(runtime: WritingToolRuntime) -> Sequence[Tool]:
    """按角色装配工具；未授权角色拿到空列表（不暴露工具名）。"""
    # ``@tool_parameters`` 在运行时注入 parameters 并摘掉 abstractmethods，
    # basedpyright 不识别这种动态实现，和仓库内既有装饰工具同样按行忽略。
    tools: list[Tool] = [
        WritingReadArticleTool(runtime)  # pyright: ignore[reportAbstractUsage]
    ]
    if runtime.member_id in MATERIAL_ROLES:
        tools.append(
            WritingCreateMaterialTool(runtime)  # pyright: ignore[reportAbstractUsage]
        )
    if runtime.member_id in OUTLINE_ROLES:
        tools.append(
            WritingCreateOutlineTool(runtime)  # pyright: ignore[reportAbstractUsage]
        )
    if runtime.member_id in ARTICLE_WRITE_ROLES:
        tools.append(
            WritingUpdateArticleTool(runtime)  # pyright: ignore[reportAbstractUsage]
        )
    return tools
