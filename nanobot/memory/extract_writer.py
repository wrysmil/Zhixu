"""抽取产物 → MEMORY.md 正文（文件模式的唯一落点）。

长期抽取产物只写 ``MEMORY.md`` 正文；不再写 ``memories`` 表，也不再维护索引。
经验/错误类型由抽取器走经验库，不进入本模块。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from nanobot.memory.files import (
    GLOBAL_IMPORTANCE_THRESHOLD,
    GLOBAL_SCOPE,
    PROJECT_IMPORTANCE_THRESHOLD,
    MemoryFileStore,
    normalize_memory_name,
    scope_for_memory_type,
)
from nanobot.memory.models import Memory

logger = logging.getLogger(__name__)

#: 只有这三类进入长期记忆正文；experience/error 走经验库，skill 走技能。
FILE_MEMORY_TYPES = frozenset({"fact", "preference", "rule"})


def memory_name_for(memory: Memory) -> str:
    """记忆文件名：主体 + 谓词（没有主体时退回内容摘要）。"""
    subject = (memory.subject or "").strip()
    predicate = (memory.predicate or "").strip()
    if subject and predicate:
        return normalize_memory_name(f"{subject}-{predicate}")
    if subject:
        return normalize_memory_name(subject)
    return normalize_memory_name(memory.content[:32])


def write_extracted_memories(
    store: MemoryFileStore,
    memories: Sequence[Memory],
    *,
    append: bool = True,
) -> list[str]:
    """把抽取产物追加到 MEMORY.md 正文，返回写入的记忆名列表。

    低于门槛的条目直接丢弃（全局 0.7 / 项目 0.5）；experience/error/skill
    不进入长期记忆正文。
    """
    written: list[str] = []
    for memory in memories:
        memory_type = str(getattr(memory.type, "value", memory.type)).lower()
        if memory_type not in FILE_MEMORY_TYPES:
            continue
        scope = scope_for_memory_type(memory.type)
        threshold = (
            GLOBAL_IMPORTANCE_THRESHOLD
            if scope == GLOBAL_SCOPE
            else PROJECT_IMPORTANCE_THRESHOLD
        )
        importance = float(memory.importance_score or 0.0)
        if importance < threshold:
            logger.debug(
                "memory file export skipped: importance={} < threshold={} scope={}",
                importance,
                threshold,
                scope,
            )
            continue
        name = memory_name_for(memory)
        section = "用户偏好与规则" if scope == GLOBAL_SCOPE else "项目事实"
        created_at = str(getattr(memory, "created_at", "") or "")
        date = created_at[:10] if created_at else ""
        bullet = memory.content.strip()
        if date:
            bullet = f"{bullet}（{date}）"
        try:
            store.append_memory_bullet(scope, section, bullet)
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响其它条目
            logger.warning("memory file export failed for %s: {!r}", name, exc)
            continue
        written.append(name)
    return written


def build_file_writer(
    store: MemoryFileStore,
) -> Callable[[list[Memory], object], None]:
    """给抽取器用的回调：``(memories, session) -> None``。"""

    def _writer(memories: list[Memory], _session: object) -> None:
        write_extracted_memories(store, memories)

    return _writer
