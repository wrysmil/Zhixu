"""记忆文件层：文件是记忆本体，``MEMORY.md`` 只是目录。

每个作用域一个目录：

* 全局：``~/.nanobot/memory/``
* 项目：``~/.nanobot/projects/<workspace_id>/memory/``

目录里一条记忆一个 md 文件（YAML frontmatter + 正文），另有一份 ``MEMORY.md``
索引，一行一条。对话内主动写、定时抽取、Dream 三个写入方共用本模块的函数，
避免出现第二个整份覆盖的写手。
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from nanobot.memory.provenance import GLOBAL_MEMORY_WORKSPACE_ID, memory_workspace_id

logger = logging.getLogger(__name__)

GLOBAL_SCOPE = "global"
PROJECT_SCOPE = "project"

#: 两份索引注入 prompt 的字符上限合计。
MEMORY_INDEX_MAX_CHARS = 2000
#: 正文模式下的 MEMORY.md 字符上限（按作用域）。
MEMORY_DOC_MAX_CHARS: dict[str, int] = {GLOBAL_SCOPE: 3000, PROJECT_SCOPE: 6000}
#: 写全局目录的 importance 门槛。
GLOBAL_IMPORTANCE_THRESHOLD = 0.7
#: 写项目目录的 importance 门槛。
PROJECT_IMPORTANCE_THRESHOLD = 0.5

#: 记忆文件 frontmatter 里允许的 ``metadata.type``。
MEMORY_TYPES: tuple[str, ...] = ("user", "feedback", "project", "reference")

#: 记忆库类型 → 文件层类型。
_TYPE_MAP: dict[str, str] = {
    "preference": "user",
    "rule": "feedback",
    "experience": "feedback",
    "error": "feedback",
    "fact": "project",
    "skill": "project",
}

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_TITLE_MAX_CHARS = 60

#: 三个写入方（对话内主动写 / 定时抽取 / Dream）共用一把写锁。
_WRITE_LOCK = threading.RLock()


@contextmanager
def memory_file_lock() -> Generator[None, None, None]:
    """记忆文件写锁：把并发写入串行化。"""
    with _WRITE_LOCK:
        yield


@dataclass(frozen=True)
class MemoryFile:
    """一条记忆文件。"""

    name: str
    path: Path
    description: str
    type: str
    content: str

    @property
    def title(self) -> str:
        """正文首行（去掉 markdown 记号），没有正文时回落 name。"""
        for line in self.content.splitlines():
            text = line.strip().lstrip("#").strip()
            if text:
                return text
        return self.name

    def index_title(self) -> str:
        """索引行里的标题：description 优先（它就是一句话钩子），否则取正文首行。"""
        label = self.description.strip() or self.title
        if len(label) > _TITLE_MAX_CHARS:
            label = label[: _TITLE_MAX_CHARS - 1].rstrip() + "…"
        return label


def global_memory_dir() -> Path:
    """全局记忆目录：``~/.nanobot/memory/``。"""
    return Path.home() / ".nanobot" / "memory"


def project_memory_dir(workspace_id: str) -> Path:
    """项目记忆目录：``~/.nanobot/projects/<workspace_id>/memory/``。"""
    key = (workspace_id or "").strip() or GLOBAL_MEMORY_WORKSPACE_ID
    return Path.home() / ".nanobot" / "projects" / key / "memory"


def normalize_memory_name(name: str) -> str:
    """记忆名规范成 kebab-case 文件名；非法字符换成 ``-``。

    全中文等非 ASCII 名称（常见于中文事实）会退化成内容哈希：同一段文本
    永远映射到同一个文件名，追加语义得以保留。
    """
    raw = (name or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    if slug:
        return slug[:64]
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"m-{digest}"


def _type_value(memory_type: object) -> str:
    """记忆类型 → 小写字符串（兼容枚举与裸字符串）。"""
    value = getattr(memory_type, "value", memory_type)
    return str(value or "").strip().lower()


def file_type_for(memory_type: object) -> str:
    """记忆库类型 → 文件层类型。"""
    return _TYPE_MAP.get(_type_value(memory_type), "project")


def scope_for_memory_type(memory_type: object) -> str:
    """作用域判定：偏好与规则属于人，项目事实属于项目。"""
    key = _type_value(memory_type)
    return GLOBAL_SCOPE if key in ("preference", "rule") else PROJECT_SCOPE


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """拆出 frontmatter 与正文。frontmatter 损坏时按「无 frontmatter」处理。"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        loaded: Any = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(loaded, dict):
        return {}, text
    raw_meta = cast("dict[Any, Any]", loaded)
    meta: dict[str, Any] = {str(key): value for key, value in raw_meta.items()}
    return meta, parts[2].lstrip("\n")


def render_memory_file(
    *,
    name: str,
    description: str,
    memory_type: str,
    content: str,
) -> str:
    """渲染记忆文件全文（frontmatter + 正文）。"""
    meta = {
        "name": name,
        "description": description,
        "metadata": {"type": memory_type},
    }
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    body = content.strip()
    return f"---\n{front}\n---\n\n{body}\n"


class MemoryFileStore:
    """两个作用域的记忆目录读写。

    写操作持模块级写锁；索引在每次记忆文件写入后同步维护。
    """

    def __init__(self, global_dir: Path, project_dir: Path | None = None) -> None:
        self._global_dir = Path(global_dir)
        self._project_dir = Path(project_dir) if project_dir is not None else None

    @classmethod
    def for_project(cls, project_path: str | None) -> MemoryFileStore:
        """按项目路径解析工作区 id 并构造 store。"""
        workspace_id = memory_workspace_id(project_path)
        return cls(global_memory_dir(), project_memory_dir(workspace_id))

    @property
    def global_dir(self) -> Path:
        return self._global_dir

    @property
    def project_dir(self) -> Path | None:
        return self._project_dir

    def dir_for(self, scope: str) -> Path:
        """作用域 → 目录。项目目录缺失时回落到全局目录。"""
        if scope == PROJECT_SCOPE:
            if self._project_dir is None:
                return self._global_dir
            return self._project_dir
        return self._global_dir

    # -- 读 ---------------------------------------------------------------

    def list_memories(self, scope: str) -> list[MemoryFile]:
        """列出目录下全部记忆文件（按写入时间排序）。"""
        directory = self.dir_for(scope)
        if not directory.is_dir():
            return []
        memories: list[MemoryFile] = []
        for path in sorted(directory.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            parsed = self._read_path(path)
            if parsed is not None:
                memories.append(parsed)
        memories.sort(key=lambda item: (item.path.stat().st_mtime, item.name))
        return memories

    def read_memory(self, scope: str, name: str) -> MemoryFile | None:
        """按 name 读一条记忆。"""
        return self._read_path(self.dir_for(scope) / f"{name}.md")

    # -- MEMORY.md 正文（索引模式的替代） --------------------------------

    def read_memory_doc(self, scope: str) -> str:
        """读 ``MEMORY.md`` 正文；不存在返回空串。"""
        try:
            return (self.dir_for(scope) / "MEMORY.md").read_text(encoding="utf-8")
        except OSError:
            return ""

    def write_memory_doc(self, scope: str, content: str) -> None:
        """整篇写入 ``MEMORY.md``，超过作用域上限直接拒绝。"""
        text = content.strip()
        limit = MEMORY_DOC_MAX_CHARS.get(scope, MEMORY_DOC_MAX_CHARS[PROJECT_SCOPE])
        if len(text) > limit:
            raise ValueError(
                f"记忆内容超过上限：{len(text)} > {limit} 字符；"
                "请把长文拆到同目录的独立文件，并在 MEMORY.md 留一行指针"
            )
        with memory_file_lock():
            path = self.dir_for(scope) / "MEMORY.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(text + "\n", encoding="utf-8")
            tmp.replace(path)

    def append_memory_section(self, scope: str, title: str, body: str) -> None:
        """在 ``MEMORY.md`` 末尾追加一个二级标题小节。"""
        current = self.read_memory_doc(scope).rstrip()
        section = f"## {title.strip()}\n{body.strip()}"
        prefix = f"{current}\n\n" if current else "# 记忆\n\n"
        self.write_memory_doc(scope, f"{prefix}{section}")

    def _read_path(self, path: Path) -> MemoryFile | None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        meta, body = _parse_frontmatter(text)
        metadata: Any = meta.get("metadata")
        memory_type = "project"
        if isinstance(metadata, dict):
            raw_type: Any = cast("dict[str, Any]", metadata).get("type")
            if isinstance(raw_type, str) and raw_type.strip():
                memory_type = raw_type.strip()
        description = meta.get("description")
        return MemoryFile(
            name=str(meta.get("name") or path.stem),
            path=path,
            description=description.strip() if isinstance(description, str) else "",
            type=memory_type,
            content=body.strip(),
        )

    # -- 写 ---------------------------------------------------------------

    def write_memory(
        self,
        scope: str,
        name: str,
        content: str,
        *,
        description: str = "",
        memory_type: str = "project",
        append: bool = False,
    ) -> MemoryFile:
        """写入一条记忆并同步索引。

        ``append=True`` 时同名文件追加正文（带日期），否则覆盖。
        """
        slug = normalize_memory_name(name)
        if not _NAME_RE.match(slug):
            raise ValueError(f"非法的记忆名：{name!r}")
        if memory_type not in MEMORY_TYPES:
            memory_type = "project"
        directory = self.dir_for(scope)
        with memory_file_lock():
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{slug}.md"
            existing = self._read_path(path)
            if existing is not None and append:
                body = f"{existing.content}\n\n{content.strip()}".strip()
                desc = description.strip() or existing.description
            else:
                body = content.strip()
                desc = description.strip() or (existing.description if existing else "")
            path.write_text(
                render_memory_file(
                    name=slug,
                    description=desc,
                    memory_type=memory_type,
                    content=body,
                ),
                encoding="utf-8",
            )
            self._sync_index(scope)
        return MemoryFile(
            name=slug,
            path=path,
            description=desc,
            type=memory_type,
            content=body,
        )

    def delete_memory(self, scope: str, name: str) -> bool:
        """删除一条记忆并同步索引。"""
        slug = normalize_memory_name(name)
        with memory_file_lock():
            path = self.dir_for(scope) / f"{slug}.md"
            if not path.is_file():
                return False
            path.unlink()
            self._sync_index(scope)
        return True

    # -- 索引 -------------------------------------------------------------

    def read_index(self, scope: str) -> str:
        """读 ``MEMORY.md`` 索引文本（不存在返回空串）。"""
        path = self.dir_for(scope) / "MEMORY.md"
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def index_lines(self, scope: str) -> list[str]:
        """索引行（一行一条，不含标题）。"""
        return [line for line in self.read_index(scope).splitlines() if line.startswith("- [")]

    def rebuild_index(self, scope: str) -> str:
        """按现有记忆文件重建索引（文件被外部改动后的一致性修复）。"""
        with memory_file_lock():
            text = self._render_index(scope, self.list_memories(scope))
            path = self.dir_for(scope) / "MEMORY.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return text

    def upsert_index_entry(self, scope: str, name: str, description: str) -> None:
        """确保索引里有这条（不存在则追加到末尾）。"""
        slug = normalize_memory_name(name)
        with memory_file_lock():
            lines = self.index_lines(scope)
            if any(f"({slug}.md)" in line for line in lines):
                return
            self._write_index(scope, self._render_index(scope, self.list_memories(scope)))

    def _sync_index(self, scope: str) -> None:
        self._write_index(scope, self._render_index(scope, self.list_memories(scope)))

    def _write_index(self, scope: str, text: str) -> None:
        path = self.dir_for(scope) / "MEMORY.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _render_index(self, scope: str, memories: Sequence[MemoryFile]) -> str:
        title = "# 全局记忆" if scope == GLOBAL_SCOPE else "# 项目记忆"
        directory = self.dir_for(scope)
        lines = [title, "", f"目录：{directory}", ""]
        for memory in memories:
            label = memory.index_title()
            hook = memory.description if memory.description and memory.description != label else ""
            line = f"- [{label}]({memory.name}.md)"
            if hook:
                line = f"{line} — {hook}"
            lines.append(line)
        if not memories:
            lines.append("（暂无记忆）")
        return "\n".join(lines).rstrip() + "\n"


def render_index_block(store: MemoryFileStore) -> str:
    """渲染注入用的索引块（两份索引 + 目录路径 + 取用说明）。

    索引是**目录**：模型需要正文时按路径 ``read_file``，更早的记忆用
    ``memory_search``。超预算时从末尾丢弃条目并标注。
    """
    sections: list[str] = []
    for scope in (GLOBAL_SCOPE, PROJECT_SCOPE):
        directory = store.dir_for(scope)
        header = "全局记忆" if scope == GLOBAL_SCOPE else "项目记忆"
        sections.append(f"## {header}目录\n目录：{directory}")
        for line in store.index_lines(scope):
            sections.append(line)
    if len(sections) <= 2:  # 两级都没有索引行：不注入空块
        return ""
    block = "\n".join(sections)
    if len(block) > MEMORY_INDEX_MAX_CHARS:
        clipped: list[str] = []
        used = 0
        for line in sections:
            if used + len(line) > MEMORY_INDEX_MAX_CHARS:
                clipped.append("- …（索引已截断，完整目录见 MEMORY.md）")
                break
            clipped.append(line)
            used += len(line)
        block = "\n".join(clipped)
    return (
        "# 记忆索引（目录，不是内容）\n\n"
        f"{block}\n\n"
        "需要具体内容时用 read_file 读上面目录里的对应文件；"
        "更早的记忆用 memory_search 检索。"
    )


def _is_legacy_index(text: str) -> bool:
    """旧索引：没有 ``## `` 小节，正文由目录行和 ``- [标题](file.md)`` 组成。"""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if any(line.startswith("## ") for line in lines):
        return False
    bullets = [line for line in lines if line.startswith("- [")]
    if not bullets:
        return False
    return all(
        line.startswith("#")
        or line.startswith("- [")
        or line.startswith("目录：")
        or line.startswith("（暂无记忆）")
        for line in lines
    )


def render_memory_content_block(store: MemoryFileStore) -> str:
    """渲染注入用的 MEMORY.md 正文块（全局 + 当前项目）。

    旧索引格式返回空串，让调用方回退到索引块。
    """
    parts: list[str] = []
    for scope, title in ((GLOBAL_SCOPE, "全局长期记忆"), (PROJECT_SCOPE, "项目长期记忆")):
        if scope == PROJECT_SCOPE and store.project_dir is None:
            continue
        content = store.read_memory_doc(scope).strip()
        if content and not _is_legacy_index(content):
            parts.append(f"## {title}\n目录：{store.dir_for(scope)}\n\n{content}")
    if not parts:
        return ""
    return "# 长期记忆（正文）\n\n" + "\n\n".join(parts)


def memory_dirs_for(workspace: str | Path | None) -> list[Path]:
    """模型可读写的记忆目录：全局 + 当前项目。

    这两个目录是「记忆本体」的唯一合法落点，工具白名单按此精确放行。
    """
    dirs = [global_memory_dir()]
    workspace_id = memory_workspace_id(str(workspace) if workspace else None)
    dirs.append(project_memory_dir(workspace_id))
    return dirs


def memory_dir_snapshot(
    store: MemoryFileStore,
) -> dict[str, str]:
    """记忆目录快照：文件路径 → 内容摘要。

    Dream 审计用它做 diff 驱动计数：新增文件 = added、修改 = updated、
    删除 = deleted ——不依赖模型自述，也不依赖工具回调。
    """
    snapshot: dict[str, str] = {}
    for scope in (GLOBAL_SCOPE, PROJECT_SCOPE):
        for memory in store.list_memories(scope):
            try:
                digest = hashlib.sha1(memory.path.read_bytes()).hexdigest()
            except OSError:
                continue
            snapshot[str(memory.path)] = digest
    return snapshot


def diff_snapshots(
    before: dict[str, str],
    after: dict[str, str],
) -> tuple[int, int, int]:
    """两份快照的差异 → ``(added, updated, deleted)``。"""
    added = len(set(after) - set(before))
    deleted = len(set(before) - set(after))
    updated = sum(
        1 for path, digest in after.items() if path in before and before[path] != digest
    )
    return added, updated, deleted


def migrate_legacy_memory_dir(
    legacy_dir: Path,
    store: MemoryFileStore,
) -> list[Path]:
    """把旧的 ``<workspace>/memory/MEMORY.md`` 迁进记忆目录。

    旧文件先备份到 ``<全局记忆目录>/legacy/MEMORY.md``；备份已存在即视为已迁移
    （幂等）。核心视图里的每条事实拆成一条记忆文件：规则 / 偏好进全局目录，
    事实 / 教训 / 技能 / 经验进项目目录；手写笔记区落成一条全局记忆。

    返回本次新写入的记忆文件路径。
    """
    legacy_index = legacy_dir / "MEMORY.md"
    if not legacy_index.is_file():
        return []
    with memory_file_lock():
        try:
            text = legacy_index.read_text(encoding="utf-8")
        except OSError:
            return []
        backup_dir = store.global_dir / "legacy"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / "MEMORY.md"
        if backup.exists():
            return []  # 已迁移过
        backup.write_text(text, encoding="utf-8")

        written: list[Path] = []
        for index, (scope, memory_type, content) in enumerate(_parse_legacy_facts(text), start=1):
            name = f"legacy-{memory_type}-{index}"
            if store.read_memory(scope, name) is not None:
                continue
            memory = store.write_memory(
                scope,
                name,
                content,
                description=content[:60],
                memory_type=memory_type,
            )
            written.append(memory.path)

        note = _extract_legacy_note(text)
        if note:
            name = "legacy-memory-notes"
            if store.read_memory(GLOBAL_SCOPE, name) is None:
                memory = store.write_memory(
                    GLOBAL_SCOPE,
                    name,
                    note,
                    description="旧版 MEMORY.md 手写笔记区迁移而来",
                    memory_type="project",
                )
                written.append(memory.path)
        return written


#: 旧 MEMORY.md 分区 → （作用域, 文件层类型）。
_LEGACY_SECTION_MAP: dict[str, tuple[str, str]] = {
    "规则": (GLOBAL_SCOPE, "feedback"),
    "偏好": (GLOBAL_SCOPE, "user"),
    "事实": (PROJECT_SCOPE, "project"),
    "教训": (PROJECT_SCOPE, "feedback"),
    "技能": (PROJECT_SCOPE, "project"),
    "经验": (PROJECT_SCOPE, "feedback"),
}


def _parse_legacy_facts(text: str) -> list[tuple[str, str, str]]:
    """旧 MEMORY.md → ``[(scope, type, 内容)]``。"""
    view = _extract_legacy_block(text, "zhixu:core-view")
    if not view:
        return []
    facts: list[tuple[str, str, str]] = []
    section = ""
    for line in view.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            section = stripped[3:].strip()
            continue
        if not stripped.startswith("- "):
            continue
        content = stripped[2:].strip()
        if not content:
            continue
        scope, memory_type = _LEGACY_SECTION_MAP.get(section, (PROJECT_SCOPE, "project"))
        facts.append((scope, memory_type, content))
    return facts


def _extract_legacy_block(text: str, marker: str) -> str:
    start = text.find(f"<!-- {marker}:begin")
    end = text.find(f"<!-- {marker}:end -->")
    if start == -1 or end == -1 or end < start:
        return ""
    return text[start:end]


def _extract_legacy_note(text: str) -> str:
    """旧 MEMORY.md 的手写笔记区正文。"""
    block = _extract_legacy_block(text, "zhixu:core-note")
    if not block:
        return ""
    lines = [
        line
        for line in block.splitlines()
        if line.strip() and not line.strip().startswith("<!--") and not line.strip().startswith("#")
    ]
    return "\n".join(lines).strip()
