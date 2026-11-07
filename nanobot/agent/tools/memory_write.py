"""长期记忆读写工具：直接读写 MEMORY.md 正文，不再维护索引。"""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

from typing import Any, ClassVar

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.context import ToolContext
from nanobot.memory.files import MemoryFileStore, normalize_memory_name


class MemoryWriteTool(Tool):
    """把“以后都成立”的规则/约定写进 MEMORY.md。"""

    name: ClassVar[str] = "memory_write"
    description: ClassVar[str] = (
        "写入长期记忆（以后都成立的规则、项目约定、用户偏好）。"
        "直接写入对应 scope 的 MEMORY.md 正文；超过字数上限时拒绝，"
        "请把长文拆到同目录的独立文件，并在 MEMORY.md 留指针。"
        "经验、失败、这次任务的经过不要写这里。"
    )

    def __init__(self, store: MemoryFileStore) -> None:
        self.store = store

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(store=MemoryFileStore.for_project(str(ctx.workspace)))

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        attrs = getattr(ctx, "attributes", {}) or {}
        if not attrs.get("memory_file_mode"):
            return False
        gate = attrs.get("memory_write_enabled")
        if callable(gate):
            try:
                return bool(gate())
            except Exception:  # noqa: BLE001 - 门控异常按关闭处理
                return False
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["global", "project"]},
                "section": {
                    "type": "string",
                    "description": "小节名，如 项目约定 / 构建与运行",
                },
                "content": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["append", "replace"],
                    "default": "append",
                },
            },
            "required": ["scope", "section", "content"],
        }

    async def execute(
        self,
        *,
        scope: str,
        section: str,
        content: str,
        mode: str = "append",
        **_: Any,
    ) -> str:
        if scope not in ("global", "project"):
            raise ValueError(f"scope 必须是 global 或 project，收到 {scope!r}")
        if mode not in ("append", "replace"):
            raise ValueError(f"mode 必须是 append 或 replace，收到 {mode!r}")
        if mode == "replace":
            self.store.write_memory_doc(scope, content)
        else:
            if not section.strip():
                raise ValueError("append 模式必须提供 section")
            self.store.append_memory_section(scope, section, content)
        path = self.store.dir_for(scope) / "MEMORY.md"
        return f"已写入{scope}长期记忆：{section}（文件：{path}）"


class MemoryReadTool(Tool):
    """读回 MEMORY.md 正文，或按名字读一条独立记忆文件。"""

    name: ClassVar[str] = "memory_read"
    description: ClassVar[str] = (
        "读回长期记忆正文（全局 / 项目 MEMORY.md），不依赖工作区文件权限。"
        "写完记忆要核实、或需要确认项目约定时调用；按 name 可读同目录下的单条记忆文件。"
    )

    def __init__(self, store: MemoryFileStore) -> None:
        self.store = store

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(store=MemoryFileStore.for_project(str(ctx.workspace)))

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        attrs = getattr(ctx, "attributes", {}) or {}
        if not attrs.get("memory_file_mode"):
            return False
        gate = attrs.get("memory_write_enabled")
        if callable(gate):
            try:
                return bool(gate())
            except Exception:  # noqa: BLE001 - 门控异常按关闭处理
                return False
        return True

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["both", "global", "project"],
                    "default": "both",
                },
                "name": {
                    "type": "string",
                    "description": "可选：读单条记忆文件的名字（不含 .md）。",
                },
            },
        }

    async def execute(
        self,
        *,
        scope: str = "both",
        name: str = "",
        **_: Any,
    ) -> str:
        if scope not in ("both", "global", "project"):
            raise ValueError(f"scope 必须是 both/global/project，收到 {scope!r}")
        if name.strip():
            if scope == "both":
                raise ValueError("读取单条记忆时 scope 必须是 global 或 project")
            slug = normalize_memory_name(name)
            memory = self.store.read_memory(scope, slug)
            if memory is None:
                return f"未找到记忆文件：{scope}/{slug}.md"
            return f"# {memory.title}\n\n{memory.content}"

        scopes = ("global", "project") if scope == "both" else (scope,)
        parts: list[str] = []
        for item in scopes:
            title = "全局长期记忆" if item == "global" else "项目长期记忆"
            path = self.store.dir_for(item) / "MEMORY.md"
            body = self.store.read_memory_doc(item).strip()
            parts.append(f"## {title}\n文件：{path}\n\n{body or '（暂无内容）'}")
        return "\n\n".join(parts)
