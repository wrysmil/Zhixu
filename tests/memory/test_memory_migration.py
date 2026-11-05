"""旧 MEMORY.md → 记忆文件：一次性迁移，幂等，带备份。"""

from __future__ import annotations

from pathlib import Path

from nanobot.memory.files import (
    GLOBAL_SCOPE,
    PROJECT_SCOPE,
    MemoryFileStore,
    migrate_legacy_memory_dir,
)

LEGACY = """# 核心记忆

<!-- zhixu:core-view:begin 自动导出区 -->
## 规则
- 提交信息统一用中文

## 偏好
- 用户偏好简洁直接的机制说明

## 事实
- 本项目使用 bun 作为包管理器
<!-- zhixu:core-view:end -->

<!-- zhixu:core-note:begin 手工笔记区 -->
## 特别说明

不要在周报里提竞品
<!-- zhixu:core-note:end -->
"""


def _store(tmp_path: Path) -> MemoryFileStore:
    return MemoryFileStore(
        global_dir=tmp_path / "home" / "memory",
        project_dir=tmp_path / "home" / "projects" / "ws-1" / "memory",
    )


def test_migration_splits_facts_into_files(tmp_path: Path) -> None:
    legacy_dir = tmp_path / "ws" / "memory"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "MEMORY.md").write_text(LEGACY, encoding="utf-8")

    store = _store(tmp_path)
    written = migrate_legacy_memory_dir(legacy_dir, store)

    assert written
    global_names = {m.name for m in store.list_memories(GLOBAL_SCOPE)}
    project_names = {m.name for m in store.list_memories(PROJECT_SCOPE)}
    assert global_names  # 规则 / 偏好 / 手写笔记
    assert project_names  # 事实
    bodies = [m.content for m in store.list_memories(GLOBAL_SCOPE)]
    assert any("提交信息统一用中文" in body for body in bodies)
    assert any("偏好简洁直接" in body for body in bodies)
    assert any("不要在周报里提竞品" in body for body in bodies)
    project_bodies = [m.content for m in store.list_memories(PROJECT_SCOPE)]
    assert any("bun" in body for body in project_bodies)


def test_migration_backs_up_and_is_idempotent(tmp_path: Path) -> None:
    legacy_dir = tmp_path / "ws" / "memory"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "MEMORY.md").write_text(LEGACY, encoding="utf-8")

    store = _store(tmp_path)
    migrate_legacy_memory_dir(legacy_dir, store)

    backup = store.global_dir / "legacy" / "MEMORY.md"
    assert backup.exists()
    assert "提交信息统一用中文" in backup.read_text(encoding="utf-8")

    before = len(store.list_memories(GLOBAL_SCOPE)) + len(store.list_memories(PROJECT_SCOPE))
    assert migrate_legacy_memory_dir(legacy_dir, store) == []
    after = len(store.list_memories(GLOBAL_SCOPE)) + len(store.list_memories(PROJECT_SCOPE))
    assert before == after
