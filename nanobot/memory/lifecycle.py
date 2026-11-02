"""MEMORY.md：核心视图的导出（契约 §5 / spec §7）。

真值只有一处——SQLite。MEMORY.md 是「当前有效核心事实 + 用户手工 core-note」
的导出：按需或在写操作之后刷新；刷新失败不影响下一轮聊天，因为注入侧每轮
直接读 SQLite，从不读这个文件。

导出格式把两块内容分开，边界由 HTML 注释标记：

* 自动导出区（``core-view``）：来自 SQLite 的当前有效事实；
* 手工笔记区（``core-note``）：用户全文编辑的内容，存为 memories 里的一条
  内部 core-note 记录（按作用域唯一），与自动派生内容分开更新。

Public API:
    MemoryLifecycle
"""

from __future__ import annotations

import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from loguru import logger

from nanobot.memory.clock import MemoryClock
from nanobot.memory.context import CORE_NOTE_RECORD_ID_PREFIX, is_core_note_record
from nanobot.memory.filters import content_hash_legacy
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.repository import (
    add_memory,
    current_memory_sql,
    delete_memory,
    get_memory,
    list_memories,
    update_memory,
)
from nanobot.utils.helpers import load_bundled_template
from nanobot.webui.memory_services import MemoryServices

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MEMORY_MD_MAX_CHARS = 1500

#: 核心视图：沿用既有「scope=user + 重要性阈值」选择规则，不扩成全库。
CORE_SCOPE = "user"
CORE_MIN_IMPORTANCE = 0.5
#: 单次核心视图最多取多少条；扫描上限只为「有效/重复」过滤留余量。
CORE_MAX_ITEMS = 12
CORE_SCAN_LIMIT = 60
TOP_K_PER_SECTION = 4

#: 手工 core-note：按作用域唯一的内部记录，不是事实。
CORE_NOTE_SOURCE = "core_note"
CORE_NOTE_IMPORTANCE = 1.0
CORE_NOTE_PLACEHOLDER = "（暂无手工笔记）"
CORE_NOTE_REVIEW_MARK = "<!-- 待核对旧稿：确认内容无误后保存一次即转为正式手工笔记 -->"

CORE_VIEW_HEADING = "# 核心记忆"
CORE_VIEW_BEGIN = (
    "<!-- zhixu:core-view:begin 自动导出区：来自 SQLite 当前有效事实"
    "（未删除/未过期/未被替代），请勿手改 -->"
)
CORE_VIEW_END = "<!-- zhixu:core-view:end -->"
CORE_NOTE_BEGIN = (
    "<!-- zhixu:core-note:begin 手工笔记区：本区全文保存为 memory 库中的 "
    "core-note 记录，可自由编辑 -->"
)
CORE_NOTE_END = "<!-- zhixu:core-note:end -->"
CORE_NOTE_HEADING = "## 手工笔记"

_TRUNCATION_MARKER = "...(规则被截断)"

# Priority sections: order matters for display
_MEMORY_TYPE_TO_SECTION: dict[MemoryType, str] = {
    MemoryType.RULE: "规则",
    MemoryType.PREFERENCE: "偏好",
    MemoryType.FACT: "事实",
    MemoryType.ERROR: "教训",
    MemoryType.SKILL: "技能",
    MemoryType.EXPERIENCE: "经验",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso8601_now() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def core_note_id(workspace_id: str) -> str:
    """手工 core-note 的记录 id：由作用域派生，天然唯一。"""
    return f"{CORE_NOTE_RECORD_ID_PREFIX}{workspace_id}"


def _current_ids(conn: Any, ids: Iterable[str]) -> set[str]:
    """批量取出「当前有效」的子集。

    「有效」的谓词只认 ``repository.current_memory_sql``（契约 §3：SQL 候选与
    向量回表共用一处），这里不另写一套判定。
    """
    id_list = list(ids)
    if not id_list:
        return set()
    placeholders = ", ".join("?" for _ in id_list)
    rows = conn.execute(
        f"SELECT id FROM memories WHERE id IN ({placeholders}) "
        f"AND {current_memory_sql('memories')}",
        id_list,
    ).fetchall()
    return {str(row["id"]) for row in rows}


def select_core_facts(
    conn: Any,
    *,
    workspace_id: str,
    limit: int = CORE_MAX_ITEMS,
    include_note: bool = True,
) -> list[Memory]:
    """核心视图：当前有效、``scope=user``、重要性达阈值的记忆。

    ``include_note`` 为真时把已确认的手工 core-note 放在最前面——它是用户
    维护的内容，与自动派生事实分开呈现；**待核对**旧稿不进注入（文件里可见，
    但不冒充事实）。
    """
    note = read_core_note(conn, workspace_id) if include_note else None
    if note is not None and note.metadata.get("needs_review"):
        note = None

    rows = list_memories(
        conn,
        workspace_id=workspace_id,
        scope=CORE_SCOPE,
        min_importance=CORE_MIN_IMPORTANCE,
        order_by="importance",
        limit=CORE_SCAN_LIMIT,
    )
    candidates = [memory for memory in rows if not is_core_note_record(memory.id)]
    valid: set[str] = _current_ids(conn, [memory.id for memory in candidates])

    picked: list[Memory] = []
    seen: set[tuple[MemoryType, str]] = set()
    for memory in candidates:
        if memory.id not in valid:
            continue
        key = (memory.type, content_hash_legacy(memory.content))
        if key in seen:
            continue
        seen.add(key)
        picked.append(memory)
        if len(picked) >= limit:
            break
    return ([note] if note is not None else []) + picked


def read_core_note(conn: Any, workspace_id: str) -> Memory | None:
    """按作用域读手工 core-note；没有则 ``None``。"""
    return get_memory(conn, core_note_id(workspace_id))


def save_core_note(
    conn: Any,
    *,
    workspace_id: str,
    content: str,
    needs_review: bool = False,
) -> Memory:
    """按作用域唯一地写入手工 core-note（存在即更新）。"""
    text = content.strip()
    metadata = {"kind": "core_note", "needs_review": bool(needs_review)}
    existing = read_core_note(conn, workspace_id)
    if existing is None:
        memory = Memory(
            id=core_note_id(workspace_id),
            content=text,
            created_at=_iso8601_now(),
            type=MemoryType.FACT,
            priority=MemoryPriority.LONG_TERM,
            source=CORE_NOTE_SOURCE,
            importance_score=CORE_NOTE_IMPORTANCE,
            scope=CORE_SCOPE,
            workspace_id=workspace_id,
            metadata=metadata,
        )
        add_memory(conn, memory)
        return memory
    update_memory(
        conn,
        existing.id,
        content=text,
        importance_score=CORE_NOTE_IMPORTANCE,
        metadata={**existing.metadata, **metadata},
    )
    return read_core_note(conn, workspace_id) or existing


def delete_core_note(conn: Any, workspace_id: str) -> None:
    """删除手工 core-note（用户清空手工区时调用）。"""
    existing = read_core_note(conn, workspace_id)
    if existing is not None:
        delete_memory(conn, existing.id)


def extract_core_note_text(content: str) -> str | None:
    """取出导出文本中手工笔记区的内容；没有该区时返回 ``None``。"""
    start = content.find(CORE_NOTE_BEGIN)
    end = content.find(CORE_NOTE_END)
    if start < 0 or end < start:
        return None
    body = content[start + len(CORE_NOTE_BEGIN): end]
    lines = [
        line for line in body.splitlines()
        if line.strip() and not line.strip().startswith("<!--")
        and line.strip() != CORE_NOTE_HEADING
    ]
    text = "\n".join(lines).strip()
    if not text or text == CORE_NOTE_PLACEHOLDER:
        return ""
    return text


def looks_like_generated_export(content: str, memories: Iterable[Memory]) -> bool:
    """判断一段旧稿是不是「自动导出」，而不是用户手写内容。

    首次迁移不许把当前的自动生成列表复制成手工笔记（契约 §5）。判据是
    确定性的：整份文本只由标题行与 ``- `` 条目组成，且每一条的内容都能在
    现有记忆里找到——这就是导出，不是手稿。
    """
    text = content.strip()
    if not text:
        return True
    if CORE_VIEW_BEGIN in text or CORE_NOTE_BEGIN in text:
        return True
    known = {memory.content.strip() for memory in memories if memory.content.strip()}
    body = [line.strip() for line in text.splitlines() if line.strip()]
    bullets = [line[2:].strip() for line in body if line.startswith("- ")]
    if not bullets:
        return False
    for bullet in bullets:
        if bullet.endswith(_TRUNCATION_MARKER):
            bullet = bullet[: -len(_TRUNCATION_MARKER)].strip()
        if not any(bullet and (bullet == known_text or bullet in known_text)
                   for known_text in known):
            return False
    return all(line.startswith("#") or line.startswith("- ") for line in body)


# ---------------------------------------------------------------------------
# MemoryLifecycle
# ---------------------------------------------------------------------------


class MemoryLifecycle:
    """Derives the MEMORY.md export of a workspace's core view.

    Class-level state for the per-workspace singleton and the last-refresh
    stats the WebUI reads. Nothing here is on the chat read path: a stale or
    failed export never changes what the model sees.
    """

    # Per-workspace singletons
    _instances: dict[str, "MemoryLifecycle"] = {}

    # Export stats（WebUI 展示用；不参与任何缓存或注入决策）
    _last_refresh_iso: dict[str, str] = {}
    _last_refresh_trigger: dict[str, str] = {}

    def __init__(
        self,
        workspace_id: str,
        services: MemoryServices,
        *,
        clock: MemoryClock | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.services = services
        # 评测可注入的时钟；缺省时全部走真实时间。
        self._clock = clock
        self.memory_dir: Path = services.database.workspace / "memory"
        self.memory_file: Path = self.memory_dir / "MEMORY.md"
        self.draft_file: Path = self.memory_dir / "MEMORY.md.draft"
        # Instance-level lock: each workspace has its own lock.
        self._derive_lock = threading.Lock()

    def _now_iso(self) -> str:
        """当前 UTC 时间戳；注入 clock 时使用虚拟墙上时间。"""
        if self._clock is not None:
            return self._clock.wall_now().strftime("%Y-%m-%dT%H:%M:%SZ")
        return _iso8601_now()

    @classmethod
    def for_workspace(
        cls,
        workspace_id: str,
        services: MemoryServices,
        *,
        clock: MemoryClock | None = None,
    ) -> "MemoryLifecycle":
        """Return the singleton MemoryLifecycle for the given workspace."""
        if workspace_id not in cls._instances:
            cls._instances[workspace_id] = cls(workspace_id, services, clock=clock)
        return cls._instances[workspace_id]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh_memory_md_sync(self, workspace_id: str) -> dict[str, Any]:
        """Re-export MEMORY.md from SQLite (on demand / after mutations).

        Thread-safe, backup-on-write, and failure-isolated: any error is
        logged and reported as ``{"status": "error"}``; callers of chat never
        depend on this file.
        """
        with self._derive_lock:
            try:
                with self.services.database.connect() as conn:
                    self._migrate_legacy_memory_md(conn)
                    facts = select_core_facts(
                        conn, workspace_id=workspace_id, include_note=False
                    )
                    note = read_core_note(conn, workspace_id)

                content = self._render_export(facts, note)
                if self.memory_file.exists() and self.memory_file.read_text(
                    encoding="utf-8"
                ) == content:
                    return {"status": "skipped", "reason": "no_change"}

                self._safe_write_with_backup(self.memory_file, content)
                self._last_refresh_iso[workspace_id] = self._now_iso()
                self._last_refresh_trigger[workspace_id] = "manual"
                return {"status": "ok", "chars": len(content)}

            except Exception:
                logger.exception(
                    "Failed to refresh MEMORY.md for workspace {}",
                    workspace_id,
                )
                return {"status": "error", "reason": "write_failed"}

    def write_memory_md(self, content: str) -> None:
        """接收用户对 MEMORY.md 的全文编辑（现有保存路由）。

        手工内容进 memories 里的 core-note 记录（按作用域唯一），随后文件按
        「自动导出区 + 手工笔记区」重新导出。长度超 ``MEMORY_MD_MAX_CHARS``
        时抛 ``ValueError``（由调用方转成 4xx）。

        迁移语义（契约 §5）：不含手工区标记的旧稿若无法确认，保留为**待核对**
        版本，不静默重写成事实；若它本来就是导出（或出厂模板），则不动手工笔记。
        """
        # 边界校验：内容来自 WebUI 的 JSON 载荷，不能只靠类型注解。
        if not isinstance(content, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("MEMORY.md 内容必须是字符串")
        if len(content) > MEMORY_MD_MAX_CHARS:
            raise ValueError(
                f"内容超过 {MEMORY_MD_MAX_CHARS} 字符上限（当前 {len(content)}）"
            )

        with self._derive_lock:
            try:
                with self.services.database.connect() as conn:
                    note_text, needs_review = self._resolve_saved_note(conn, content)
                    if note_text is None:
                        pass  # 保存的是导出版本/模板：手工笔记维持原样
                    elif note_text:
                        save_core_note(
                            conn,
                            workspace_id=self.workspace_id,
                            content=note_text,
                            needs_review=needs_review,
                        )
                    else:
                        delete_core_note(conn, self.workspace_id)
                    facts = select_core_facts(
                        conn, workspace_id=self.workspace_id, include_note=False
                    )
                    note = read_core_note(conn, self.workspace_id)

                self._safe_write_with_backup(
                    self.memory_file,
                    self._render_export(facts, note),
                )
                self._last_refresh_iso[self.workspace_id] = self._now_iso()
                self._last_refresh_trigger[self.workspace_id] = "manual"
            except ValueError:
                raise
            except Exception as exc:
                logger.exception(
                    "MEMORY.md manual write failed for workspace {}",
                    self.workspace_id,
                )
                raise OSError(f"MEMORY.md 写入失败: {exc}") from exc

            logger.info(
                "MEMORY.md manually written for workspace {} ({} chars)",
                self.workspace_id,
                len(content),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _migrate_legacy_memory_md(self, conn: Any) -> None:
        """首次导出前把旧版全文迁移成 core-note / 待核对稿（契约 §5）。

        - 出厂模板、自动导出：不迁移（绝不把自动列表复制成手工笔记）；
        - 其它无法确认的旧稿：保留为待核对版本，不重写成事实。
        """
        if read_core_note(conn, self.workspace_id) is not None:
            return
        if not self.memory_file.exists():
            return
        try:
            legacy = self.memory_file.read_text(encoding="utf-8")
        except OSError:
            logger.warning(
                "MEMORY.md migration skipped: unreadable file for workspace {}",
                self.workspace_id,
            )
            return
        if not legacy.strip() or self._is_template_content(legacy):
            return
        facts = select_core_facts(conn, workspace_id=self.workspace_id, include_note=False)
        if looks_like_generated_export(legacy, facts):
            return
        save_core_note(
            conn,
            workspace_id=self.workspace_id,
            content=legacy,
            needs_review=True,
        )
        logger.info(
            "MEMORY.md legacy content kept as an unconfirmed core-note for workspace {}",
            self.workspace_id,
        )

    def _resolve_saved_note(
        self, conn: Any, content: str
    ) -> tuple[str | None, bool]:
        """把一次保存的全文解析成 ``(手工笔记正文, 是否待核对)``。

        ``None`` 表示「这不是对手工笔记的编辑」——保存的是导出版本本身或出厂
        模板，手工笔记维持原样；空串表示用户清空了手工区。

        保存动作本身就是用户的明确意图，所以这里一律视为**已确认**；
        「待核对」只来自旧稿迁移（见 ``_migrate_legacy_memory_md``）。
        """
        explicit = extract_core_note_text(content)
        if explicit is not None:
            return explicit, False
        if not content.strip() or self._is_template_content(content):
            return None, False
        facts = select_core_facts(conn, workspace_id=self.workspace_id, include_note=False)
        if looks_like_generated_export(content, facts):
            return None, False
        return _strip_note_heading(content), False

    def _is_template_content(self, content: str) -> bool:
        template = load_bundled_template("memory/MEMORY.md")
        return bool(template) and content.strip() == template.strip()

    def _render_export(self, facts: list[Memory], note: Memory | None) -> str:
        """渲染导出文件：自动导出区 + 手工笔记区，并压在字符上限内。"""
        note_text = (note.content.strip() if note is not None else "")
        needs_review = bool(note.metadata.get("needs_review")) if note is not None else False
        content = self._render_memory_md(facts, note_text, needs_review=needs_review)
        omitted = 0
        while facts and len(content) > MEMORY_MD_MAX_CHARS:
            facts = facts[:-1]
            omitted += 1
            content = self._render_memory_md(
                facts, note_text, needs_review=needs_review, omitted=omitted
            )
        if len(content) > MEMORY_MD_MAX_CHARS:
            # 只剩笔记本身超限：文件里截断显示，完整正文仍保存在库里。
            note_text = note_text[: max(0, MEMORY_MD_MAX_CHARS // 2)] + "…（文件显示已截断）"
            content = self._render_memory_md(
                facts, note_text, needs_review=needs_review, omitted=omitted
            )
        return content

    def _render_memory_md(
        self,
        memories: list[Memory],
        note_text: str = "",
        *,
        needs_review: bool = False,
        omitted: int = 0,
    ) -> str:
        """把核心事实与手工笔记渲染成固定格式的导出文本。"""
        grouped: dict[MemoryType, list[Memory]] = {}
        for memory in memories:
            if is_core_note_record(memory.id):
                continue
            grouped.setdefault(memory.type, []).append(memory)

        lines: list[str] = [CORE_VIEW_HEADING, "", CORE_VIEW_BEGIN]
        for memory_type, label in _MEMORY_TYPE_TO_SECTION.items():
            items = sorted(
                grouped.get(memory_type, []),
                key=lambda memory: memory.importance_score,
                reverse=True,
            )[:TOP_K_PER_SECTION]
            if not items:
                continue
            lines.append(f"## {label}")
            lines.extend(f"- {_inline(memory.content)}" for memory in items)
            lines.append("")
        if omitted:
            lines.append(f"<!-- 超出字符上限，已省略 {omitted} 条核心事实 -->")
        lines.append(CORE_VIEW_END)
        lines.extend(["", CORE_NOTE_BEGIN, CORE_NOTE_HEADING])
        if needs_review:
            lines.append(CORE_NOTE_REVIEW_MARK)
        lines.append(note_text.strip() or CORE_NOTE_PLACEHOLDER)
        lines.append(CORE_NOTE_END)
        return "\n".join(lines).rstrip() + "\n"

    def _safe_write_with_backup(self, path: Path, content: str) -> None:
        """Write content to path with .bak backup; restore on failure."""
        backup = path.with_suffix(path.suffix + ".bak")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, backup)
        try:
            path.write_text(content, encoding="utf-8")
        except Exception:
            if backup.exists():
                shutil.copy2(backup, path)
            raise


def _inline(content: str) -> str:
    """导出条目压成一行，避免一条记忆伪造出新的段头/条目。"""
    return " ".join(content.split())


def _strip_note_heading(text: str) -> str:
    """去掉用户粘贴进来的「## 手工笔记」标题行（导出格式自带该标题）。"""
    lines = text.strip().splitlines()
    while lines and lines[0].strip() == CORE_NOTE_HEADING:
        lines.pop(0)
    return "\n".join(lines).strip()
