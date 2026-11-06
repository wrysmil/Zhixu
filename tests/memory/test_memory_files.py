"""记忆文件层单测：文件是本体，索引是目录。"""

from __future__ import annotations

from pathlib import Path

from nanobot.memory.files import (
    GLOBAL_SCOPE,
    PROJECT_SCOPE,
    MemoryFileStore,
    file_type_for,
    normalize_memory_name,
    render_index_block,
    render_memory_content_block,
    scope_for_memory_type,
)


def _store(tmp_path: Path) -> MemoryFileStore:
    return MemoryFileStore(global_dir=tmp_path / "g", project_dir=tmp_path / "p")


def test_write_memory_creates_file_without_index(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write_memory(
        GLOBAL_SCOPE,
        "prefer-direct",
        "解释机制先讲因果\n不要绕弯",
        description="用户讨厌绕弯解释",
        memory_type="user",
    )

    files = store.list_memories(GLOBAL_SCOPE)
    assert [item.name for item in files] == ["prefer-direct"]
    assert files[0].type == "user"
    assert files[0].description == "用户讨厌绕弯解释"
    assert store.read_index(GLOBAL_SCOPE) == ""


def test_write_memory_appends_same_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write_memory(PROJECT_SCOPE, "fact-a", "第一条", description="d")
    store.write_memory(PROJECT_SCOPE, "fact-a", "第二条", description="d", append=True)

    items = store.list_memories(PROJECT_SCOPE)
    assert len(items) == 1
    assert "第一条" in items[0].content
    assert "第二条" in items[0].content


def test_project_scope_does_not_leak_into_global(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write_memory(PROJECT_SCOPE, "only-project", "项目事实", description="d")

    assert store.list_memories(GLOBAL_SCOPE) == []
    assert len(store.list_memories(PROJECT_SCOPE)) == 1


def test_delete_memory_removes_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write_memory(GLOBAL_SCOPE, "gone", "内容", description="d")
    assert store.delete_memory(GLOBAL_SCOPE, "gone") is True
    assert store.list_memories(GLOBAL_SCOPE) == []


def test_memory_content_block_lists_both_scopes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write_memory_doc(GLOBAL_SCOPE, "# 全局记忆\n\n## 规则\n- 全局一条")
    store.write_memory_doc(PROJECT_SCOPE, "# 项目记忆\n\n## 约定\n- 项目一条")

    block = render_memory_content_block(store)
    assert "全局长期记忆" in block
    assert "项目长期记忆" in block
    assert "全局一条" in block
    assert "项目一条" in block
    assert str(store.global_dir) in block


def test_index_block_empty_when_no_memories(tmp_path: Path) -> None:
    assert render_index_block(_store(tmp_path)) == ""


def test_type_mapping() -> None:
    assert file_type_for("preference") == "user"
    assert file_type_for("rule") == "feedback"
    assert file_type_for("experience") == "feedback"
    assert file_type_for("error") == "feedback"
    assert file_type_for("fact") == "project"
    assert scope_for_memory_type("preference") == GLOBAL_SCOPE
    assert scope_for_memory_type("rule") == GLOBAL_SCOPE
    assert scope_for_memory_type("fact") == PROJECT_SCOPE


def test_normalize_memory_name() -> None:
    assert normalize_memory_name("Prefer Direct!") == "prefer-direct"
    # 全中文名称退化成内容哈希：同一段文本始终映射到同一个文件。
    assert normalize_memory_name("用户偏好简洁") == normalize_memory_name("用户偏好简洁")
    assert normalize_memory_name("用户偏好简洁") != normalize_memory_name("用户偏好详细")
