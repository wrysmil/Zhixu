"""MEMORY.md synchronous derivation and async refresh scheduling.

Public API:
    MemoryLifecycle
"""

from __future__ import annotations

import asyncio
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.memory.clock import MemoryClock
from nanobot.memory.filters import content_hash_legacy
from nanobot.memory.models import Memory, MemoryType
from nanobot.memory.repository import list_memories
from nanobot.webui.memory_services import MemoryServices

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MEMORY_MD_MAX_CHARS = 1500
MIN_CONTENT_CHARS = 10
TOP_K_PER_SECTION = 4
REFRESH_DEBOUNCE_SECONDS = 60.0

# Priority sections: order matters for truncation fallback
_MEMORY_TYPE_TO_SECTION: dict[MemoryType, str] = {
    MemoryType.PREFERENCE: "偏好",
    MemoryType.RULE: "规则",
    MemoryType.FACT: "事实",
    MemoryType.ERROR: "教训",
    MemoryType.SKILL: "技能",
    MemoryType.EXPERIENCE: "经验",
}

# Rule-related section keywords (case-insensitive match in truncate_memory_md)
_RULE_SECTION_KEYWORDS: frozenset[str] = frozenset({
    "重要规则", "规则", "rules", "行为规则", "用户规则",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso8601_now() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# MemoryLifecycle
# ---------------------------------------------------------------------------

class MemoryLifecycle:
    """Manages the derivation and refresh of MEMORY.md for a workspace.

    Class-level state for singleton pattern and refresh task tracking.
    """

    # Per-workspace singletons
    _instances: dict[str, "MemoryLifecycle"] = {}

    # Async refresh task management
    _refresh_tasks: dict[str, asyncio.Task] = {}
    _last_refresh_at: dict[str, float] = {}  # monotonic, debounce
    _last_refresh_iso: dict[str, str] = {}   # ISO8601 UTC, stats
    _last_refresh_trigger: dict[str, str] = {}  # "manual" | "auto"
    # NOTE: _derive_lock moved to instance-level in __init__ (fixes cross-workspace blocking)

    def __init__(
        self,
        workspace_id: str,
        services: MemoryServices,
        *,
        clock: MemoryClock | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.services = services
        self.memory_dir: Path = services.database.workspace / "memory"
        self.memory_file: Path = self.memory_dir / "MEMORY.md"
        self.draft_file: Path = self.memory_dir / "MEMORY.md.draft"
        # Instance-level lock: each workspace has its own lock (fixes cross-workspace blocking)
        self._derive_lock = threading.Lock()
        # 评测时钟（可选）：派生文件刷新时间戳与去抖间隔走注入时钟；
        # 缺省时与生产一致（真实时间）。
        self._clock = clock

    def _now_iso(self) -> str:
        """当前 UTC 时间戳（注入时钟时为虚拟日期）。"""
        if self._clock is not None:
            return self._clock.wall_now().strftime("%Y-%m-%dT%H:%M:%SZ")
        return _iso8601_now()

    def _monotonic(self) -> float:
        """策略单调时钟（去抖间隔口径）。"""
        if self._clock is not None:
            return self._clock.monotonic()
        return time.monotonic()

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
    # Public sync API
    # ------------------------------------------------------------------

    def refresh_memory_md_sync(self, workspace_id: str) -> dict[str, Any]:
        """Synchronously derive MEMORY.md from user-scope memories.

        Thread-safe. Writes MEMORY.md with backup/restore on failure.
        Returns {"status": "ok", "chars": <len>} or
        {"status": "skipped", "reason": "content_too_short"}.
        """
        with self._derive_lock:
            try:
                with self.services.database.connect() as conn:
                    # Try WU-2 signature first; fallback to Python-side filtering.
                    try:
                        memories = list_memories(
                            conn,
                            workspace_id=workspace_id,  # 🆕 Critical fix: 按 workspace 隔离
                            scope="user",
                            min_importance=0.5,
                            limit=200,
                            order_by="importance",
                        )
                    except TypeError:
                        # WU-2 not yet merged: use current signature + Python filter
                        all_memories = list_memories(
                            conn, workspace_id=workspace_id, limit=200, order_by="importance"
                        )
                        memories = [
                            m
                            for m in all_memories
                            if m.scope == "user" and m.importance_score >= 0.5
                        ]

                content = self._render_memory_md(memories)

                if len(content) < MIN_CONTENT_CHARS:
                    return {"status": "skipped", "reason": "content_too_short"}

                if len(content) > MEMORY_MD_MAX_CHARS:
                    content = self.truncate_memory_md(content, MEMORY_MD_MAX_CHARS)

                self._safe_write_with_backup(self.memory_file, content)

                self._last_refresh_iso[workspace_id] = self._now_iso()
                # Only stamp "manual" when called directly (sync path);
                # async schedule_refresh_md pre-sets "auto" and we preserve it.
                if self._last_refresh_trigger.get(workspace_id) != "auto":
                    self._last_refresh_trigger[workspace_id] = "manual"

                return {"status": "ok", "chars": len(content)}

            except Exception:
                logger.exception(
                    "Failed to refresh MEMORY.md for workspace {}",
                    workspace_id,
                )
                # No exception propagates: callers get the ok/skipped dict only.
                return {"status": "error", "reason": "write_failed"}

    def write_memory_md(self, content: str) -> None:
        """写入用户手工编辑的 MEMORY.md，带 .bak 备份。

        长度超 MEMORY_MD_MAX_CHARS 时抛 ValueError（由调用方转成 4xx）。
        注意：下一次 refresh_memory_md_sync 会自动覆盖本方法写入的内容。

        这是 MEMORY.md 除 Dream 派生之外的第二个（也是唯一一个）写入口：
        ``IdentityStore.write_file`` 对 MEMORY.md 一律拒绝，手工编辑必须走这里，
        才能保证「改前先备份」的语义不丢失。
        """
        with self._derive_lock:
            if not isinstance(content, str):
                raise ValueError("MEMORY.md 内容必须是字符串")
            if len(content) > MEMORY_MD_MAX_CHARS:
                raise ValueError(
                    f"内容超过 {MEMORY_MD_MAX_CHARS} 字符上限（当前 {len(content)}）"
                )
            self.memory_file.parent.mkdir(parents=True, exist_ok=True)
            self._safe_write_with_backup(self.memory_file, content)
            logger.info(
                "MEMORY.md manually written for workspace {} ({} chars)",
                self.workspace_id,
                len(content),
            )

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def schedule_refresh_md(
        self, workspace_id: str, debounce_seconds: float = REFRESH_DEBOUNCE_SECONDS
    ) -> None:
        """Schedule a debounced async refresh of MEMORY.md.

        Only triggers a real refresh if debounce_seconds have elapsed since
        the last trigger. Cancels any in-flight refresh task.
        """
        now = self._monotonic()
        last = self._last_refresh_at.get(workspace_id, 0.0)

        if now - last < debounce_seconds:
            return  # debounced

        self._last_refresh_at[workspace_id] = now
        self._last_refresh_trigger[workspace_id] = "auto"

        # Cancel previous task if still running
        old = self._refresh_tasks.pop(workspace_id, None)
        if old and not old.done():
            old.cancel()

        task = asyncio.create_task(
            asyncio.to_thread(self.refresh_memory_md_sync, workspace_id)
        )
        self._refresh_tasks[workspace_id] = task

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _safe_write_with_backup(self, path: Path, content: str) -> None:
        """Write content to path with .bak backup; restore on failure."""
        backup = path.with_suffix(path.suffix + ".bak")
        if path.exists():
            shutil.copy2(path, backup)
        try:
            path.write_text(content, encoding="utf-8")
        except Exception:
            if backup.exists():
                shutil.copy2(backup, path)
            raise

    def _render_memory_md(self, memories: list[Memory]) -> str:
        """Render memories into fixed-format markdown grouped by type.

        Each section gets at most TOP_K_PER_SECTION items sorted by
        importance_score descending. Duplicate (type, content_hash_legacy)
        entries are collapsed to one.
        """
        # Deduplicate: same (type, content_hash_legacy) -> keep first (highest importance)
        seen: dict[tuple[MemoryType, str], Memory] = {}
        for m in memories:
            key = (m.type, content_hash_legacy(m.content))
            if key not in seen:
                seen[key] = m

        # Group by type
        grouped: dict[MemoryType, list[Memory]] = {t: [] for t in MemoryType}
        for m in seen.values():
            grouped[m.type].append(m)

        # Sort each group by importance descending, take top-K
        for t in grouped:
            grouped[t].sort(key=lambda m: m.importance_score, reverse=True)
            grouped[t] = grouped[t][:TOP_K_PER_SECTION]

        # Render
        lines: list[str] = ["# 核心记忆", ""]
        for memory_type, label in _MEMORY_TYPE_TO_SECTION.items():
            items = grouped.get(memory_type, [])
            if not items:
                continue
            lines.append(f"## {label}")
            for m in items:
                lines.append(f"- {m.content}")
            lines.append("")  # blank line between sections

        return "\n".join(lines).rstrip() + "\n"

    def truncate_memory_md(
        self, content: str, max_chars: int = MEMORY_MD_MAX_CHARS
    ) -> str:
        """Truncate markdown content by paragraph priority.

        High-priority paragraphs (rule sections) are preserved first.
        When a high-priority section exceeds budget, it is truncated
        with a "...(规则被截断)" marker.
        """
        if len(content) <= max_chars:
            return content

        # Regex: capture level-1 header, then each ## section as (heading, body)
        # Pattern:
        #   ^# [^\n]+\n?          -> level-1 header (optional)
        #   (?:^## ([^\n]+)\n(.*?)(?=\n## |\n# |${content})) -> ## section
        # Use DOTALL so body can span multiple lines
        h1_pattern = re.compile(r"^(# [^\n]+\n?)", re.MULTILINE)
        section_pattern = re.compile(
            r"(?:^|\n)(## [^\n]+)\n((?:.*?\n?)*?)(?=\n## |$)", re.DOTALL | re.MULTILINE
        )

        header = ""
        h1_match = h1_pattern.match(content)
        if h1_match:
            header = h1_match.group(1)

        # Parse all sections
        sections: list[tuple[str, str, bool]] = []  # (heading, body, is_high_priority)
        for m in section_pattern.finditer(content):
            heading = m.group(1).strip()
            body = m.group(2).strip()
            # Strip '## ' prefix before keyword matching
            heading_keyword = heading[2:].strip() if heading.startswith("##") else heading
            is_high = heading_keyword.lower() in _RULE_SECTION_KEYWORDS
            sections.append((heading, body, is_high))

        # Build result within budget
        result_parts: list[str] = []
        used = len(header)

        if header:
            result_parts.append(header)
            used += 2  # separator \n\n

        # Sort: high priority first
        high = [(h, b) for h, b, hp in sections if hp]
        normal = [(h, b) for h, b, hp in sections if not hp]

        def try_add(heading: str, body: str, is_high: bool) -> bool:
            """Add section within budget. Returns False if truncated mid-section."""
            nonlocal used
            sep = 2
            if heading:
                line = f"{heading}\n{body}"
            else:
                line = body
            line_len = len(line)

            if used + sep + line_len <= max_chars:
                result_parts.append(line)
                used += sep + line_len
                return True

            if is_high:
                # Truncate with marker
                TRUNCATE_MARKER = "...(规则被截断)"
                marker_len = len(TRUNCATE_MARKER)
                available = max_chars - used - sep - marker_len
                if available <= 0:
                    return False
                if heading:
                    overhead = len(f"{heading}\n")
                    body_space = max(0, available - overhead)
                    truncated_body = body[:body_space]
                    truncated_line = f"{heading}\n{truncated_body}{TRUNCATE_MARKER}"
                else:
                    truncated_line = body[:available] + TRUNCATE_MARKER
                result_parts.append(truncated_line)
                used += sep + len(truncated_line)
                return False

            # Normal: skip if doesn't fit
            return True

        for heading, body in high:
            if not try_add(heading, body, is_high=True):
                break

        for heading, body in normal:
            if not try_add(heading, body, is_high=False):
                break

        return ("\n\n".join(result_parts)).rstrip() + "\n"
