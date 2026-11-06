"""定时抽取：作用域判定 + importance 门槛 + 抽取产物写进记忆文件。"""

from __future__ import annotations

from pathlib import Path

from nanobot.memory.extract_writer import write_extracted_memories
from nanobot.memory.files import GLOBAL_SCOPE, PROJECT_SCOPE, MemoryFileStore
from nanobot.memory.models import Memory, MemoryType


def _store(tmp_path: Path) -> MemoryFileStore:
    return MemoryFileStore(
        global_dir=tmp_path / "global",
        project_dir=tmp_path / "project",
    )


def _memory(
    content: str,
    *,
    memory_type: MemoryType = MemoryType.FACT,
    importance: float = 0.8,
    subject: str = "",
    predicate: str = "",
) -> Memory:
    return Memory(
        id="m1",
        content=content,
        type=memory_type,
        importance_score=importance,
        subject=subject,
        predicate=predicate,
        created_at="2026-11-05T00:00:00Z",
        updated_at="2026-11-05T00:00:00Z",
    )


def test_preference_goes_to_global_scope(tmp_path: Path) -> None:
    store = _store(tmp_path)
    written = write_extracted_memories(
        store,
        [_memory("用户偏好简洁回复", memory_type=MemoryType.PREFERENCE, importance=0.8)],
    )

    assert written
    assert "用户偏好简洁回复" in store.read_memory_doc(GLOBAL_SCOPE)
    assert store.read_memory_doc(PROJECT_SCOPE).strip() == ""


def test_fact_goes_to_project_scope(tmp_path: Path) -> None:
    store = _store(tmp_path)
    write_extracted_memories(
        store, [_memory("本项目使用 bun", memory_type=MemoryType.FACT, importance=0.6)]
    )

    assert store.read_memory_doc(GLOBAL_SCOPE).strip() == ""
    assert "本项目使用 bun" in store.read_memory_doc(PROJECT_SCOPE)


def test_importance_threshold_filters_low_value_facts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    written = write_extracted_memories(
        store,
        [
            _memory("低价值事实", memory_type=MemoryType.FACT, importance=0.4),
            _memory("够格的偏好", memory_type=MemoryType.PREFERENCE, importance=0.6),
        ],
    )

    # 项目门槛 0.5 / 全局门槛 0.7：两条都不该落盘
    assert written == []
    assert store.list_memories(GLOBAL_SCOPE) == []
    assert store.list_memories(PROJECT_SCOPE) == []


def test_same_subject_appends_to_same_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    write_extracted_memories(
        store,
        [
            _memory("第一条", subject="用户", predicate="时区"),
            _memory("第二条", subject="用户", predicate="时区"),
        ],
    )

    content = store.read_memory_doc(PROJECT_SCOPE)
    assert "第一条" in content
    assert "第二条" in content
    assert content.count("## 项目事实") == 1
