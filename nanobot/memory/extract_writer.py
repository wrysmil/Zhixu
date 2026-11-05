"""抽取产物 → 记忆文件（文件模式的第二出口）。

SQLite 只服务检索，记忆本体是文件：定时抽取跑完 LLM 抽取后，把落库的每条事实
再写成一条记忆文件（同 ``name`` 追加），并由文件层维护索引。

门槛与作用域判定：

* 作用域：``preference`` / ``rule`` → 全局目录；其余 → 当前项目目录。
* 门槛：全局目录要求 ``importance >= 0.7``，项目目录要求 ``>= 0.5``。
  门槛用 ``importance`` 而不是 ``confidence``——抽取产物没有 confidence 字段。
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

#: 记忆库类型 → 文件层类型。
_TYPE_MAP: dict[str, str] = {
    "preference": "user",
    "rule": "feedback",
    "experience": "feedback",
    "error": "feedback",
    "fact": "project",
    "skill": "project",
}


def file_type_for(memory_type: str) -> str:
    """记忆库类型 → 文件层类型（``user`` / ``feedback`` / ``project``）。"""
    value = getattr(memory_type, "value", memory_type)
    return _TYPE_MAP.get(str(value or "").strip().lower(), "project")


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
    """把抽取产物写进记忆文件，返回写入的文件名列表。

    低于门槛的条目直接丢弃（全局 0.7 / 项目 0.5）。
    """
    written: list[str] = []
    for memory in memories:
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
        description = (memory.content or "").strip().splitlines()[0][:60] if memory.content else ""
        try:
            store.write_memory(
                scope,
                name,
                memory.content,
                description=description,
                memory_type=file_type_for(memory.type),
                append=append,
            )
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
