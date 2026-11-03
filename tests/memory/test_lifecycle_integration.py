"""端到端集成测试：MEMORY.md SQLite 派生完整路径（WU-10）。

测试场景：
1. refresh → MEMORY.md 文件存在
2. truncate：大量 fact 条目 → 总字符 ≤ 1500
3. Dream 降级：write_file("MEMORY.md") 被拒绝
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from nanobot.memory import (
    Memory,
    MemoryDatabase,
    MemoryType,
    add_memory,
)
from nanobot.memory.lifecycle import MemoryLifecycle
from nanobot.webui.memory_services import MemoryServices

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ws_id() -> str:
    """每个测试用唯一 workspace_id，避免单例污染。"""
    return f"ws-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    db_ = MemoryDatabase(tmp_path)
    db_.ensure_schema()
    return db_


@pytest.fixture
def services(db: MemoryDatabase, tmp_path: Path, ws_id: str) -> MemoryServices:
    db.workspace = tmp_path
    return MemoryServices(workspace_id=ws_id, database=db)


@pytest.fixture
def lifecycle(services: MemoryServices) -> MemoryLifecycle:
    return MemoryLifecycle.for_workspace(
        workspace_id=services.workspace_id,
        services=services,
    )


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    d = tmp_path / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def memory_store(tmp_path: Path):
    """创建 MemoryStore 实例用于 Dream 工具测试。"""
    from nanobot.agent.memory import MemoryStore

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    # MemoryStore 只需要 workspace，memory_dir 从 workspace/memory 推导
    return MemoryStore(workspace)


# ---------------------------------------------------------------------------
# 1. refresh → MEMORY.md 写入
# ---------------------------------------------------------------------------


def test_refresh_generates_memory_md_file(
    db: MemoryDatabase,
    services: MemoryServices,
    lifecycle: MemoryLifecycle,
    memory_dir: Path,
):
    """验证 refresh_memory_md_sync 后，MEMORY.md 文件被正确生成。"""
    memory_file = memory_dir / "MEMORY.md"

    now = "2026-09-18T12:00:00Z"
    with db.connect() as conn:
        add_memory(conn, Memory(
            id="fact-1",
            content="用户叫张三",
            type=MemoryType.FACT,
            subject="用户",
            predicate="名字",
            importance_score=0.8,
            created_at=now,
            updated_at=now,
            scope="user",
            workspace_id=services.workspace_id,
        ))
        add_memory(conn, Memory(
            id="rule-1",
            content="不要在周末打扰用户",
            type=MemoryType.RULE,
            subject="打扰",
            predicate="时间限制",
            importance_score=0.9,
            created_at=now,
            updated_at=now,
            scope="user",
            workspace_id=services.workspace_id,
        ))

    result = lifecycle.refresh_memory_md_sync(services.workspace_id)

    assert result["status"] == "ok"
    assert memory_file.exists(), "MEMORY.md 应该被创建"
    content = memory_file.read_text(encoding="utf-8")
    assert "张三" in content
    assert "不要在周末打扰用户" in content
    assert len(content) <= 1500, f"应 ≤ 1500 字符，实际 {len(content)}"


# ---------------------------------------------------------------------------
# 2. Truncate：大量条目时字符数封顶
# ---------------------------------------------------------------------------


def test_truncate_keeps_memory_md_under_1500_chars(
    db: MemoryDatabase,
    services: MemoryServices,
    memory_dir: Path,
):
    """大量 fact 条目时，MEMORY.md 总字符数应 ≤ 1500。"""
    lifecycle = MemoryLifecycle.for_workspace(
        workspace_id=services.workspace_id,
        services=services,
    )

    now = "2026-09-18T12:00:00Z"
    with db.connect() as conn:
        for i in range(30):
            add_memory(conn, Memory(
                id=f"bulk-{i}",
                content=f"这是第 {i} 个事实描述内容。" * 5,
                type=MemoryType.FACT,
                importance_score=0.6,
                created_at=now,
                updated_at=now,
                scope="user",
                workspace_id=services.workspace_id,
            ))

    result = lifecycle.refresh_memory_md_sync(services.workspace_id)
    assert result["status"] == "ok"

    content = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert len(content) <= 1500, f"应 ≤ 1500 字符，实际 {len(content)}"


# ---------------------------------------------------------------------------
# 3. Dream 工具白名单：三个记忆文件都写不了
# ---------------------------------------------------------------------------


def test_dream_tools_exclude_all_memory_files(memory_store):
    """P1 之后 Dream 对 SOUL.md / USER.md / MEMORY.md 一个都写不了。

    ``build_dream_tools`` 不再有 ``extra_write_allowed_files``，写目录被限制在
    ``skills/``；想改记忆只能走 ``add_memory`` / ``memory_delete``。
    """
    registry = memory_store.build_dream_tools()
    assert registry is not None

    write_tool = next(t for t in registry._tools.values() if "write" in t.name.lower())
    assert not write_tool._extra_write_allowed_files
    assert str(write_tool._allowed_dir).endswith("skills")


def test_draft_file_attribute_is_gone(memory_store):
    """draft 机制已删除：MemoryStore 上不再有 draft_file 属性。"""
    assert not hasattr(memory_store, "draft_file")
    assert not (memory_store.memory_dir / "MEMORY.md.draft").exists()


def test_legacy_draft_is_archived(memory_store):
    """启动时把遗留的 MEMORY.md.draft 归档到 memory/legacy/，不删除。"""
    from nanobot.agent.memory import MemoryStore

    draft = memory_store.memory_dir / "MEMORY.md.draft"
    draft.write_text("候选：用户偏好简洁回复", encoding="utf-8")

    MemoryStore(memory_store.workspace)  # 重新构造即触发归档

    assert not draft.exists()
    archived = memory_store.memory_dir / "legacy" / "MEMORY.md.draft"
    assert archived.exists()
    assert "候选" in archived.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. get_memory_md_content 端到端
# ---------------------------------------------------------------------------


def test_get_memory_md_content_returns_memory_md(
    db: MemoryDatabase,
    services: MemoryServices,
    memory_dir: Path,
):
    """验证 get_memory_md_content 返回 MEMORY.md 内容（draft 已移除）。"""
    # 先通过 lifecycle 创建实例（让 _instances 有记录）
    lifecycle = MemoryLifecycle.for_workspace(
        workspace_id=services.workspace_id,
        services=services,
    )

    # 通过 lifecycle 路径写入文件
    lifecycle.memory_file.write_text(
        "# Core Memory\n\n## Facts\n- test fact", encoding="utf-8"
    )

    from nanobot.webui.memory_api import get_memory_md_content

    result = get_memory_md_content(services)

    assert result["memory_md_exists"] is True
    assert "test fact" in (result["memory_md"] or "")
    # draft 相关字段已随机制一并移除
    assert "draft" not in result
    assert "draft_exists" not in result


# ---------------------------------------------------------------------------
# 5. 1500 字符截断时规则段落优先保留
# ---------------------------------------------------------------------------


def test_truncate_preserves_rule_sections(
    db: MemoryDatabase,
    services: MemoryServices,
    memory_dir: Path,
):
    """当需要截断时，规则段落应优先保留。"""
    lifecycle = MemoryLifecycle.for_workspace(
        workspace_id=services.workspace_id,
        services=services,
    )

    now = "2026-09-18T12:00:00Z"
    with db.connect() as conn:
        for i in range(20):
            add_memory(conn, Memory(
                id=f"fact-{i}",
                content=f"这是第 {i} 个事实描述内容。" * 8,
                type=MemoryType.FACT,
                importance_score=0.5,
                created_at=now,
                updated_at=now,
                scope="user",
                workspace_id=services.workspace_id,
            ))
        for i in range(3):
            add_memory(conn, Memory(
                id=f"rule-{i}",
                content=f"这是一个重要的规则 number {i}",
                type=MemoryType.RULE,
                importance_score=0.9,
                created_at=now,
                updated_at=now,
                scope="user",
                workspace_id=services.workspace_id,
            ))

    result = lifecycle.refresh_memory_md_sync(services.workspace_id)
    assert result["status"] == "ok"

    content = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "## 规则" in content or "## 教训" in content
    assert len(content) <= 1500, f"应 ≤ 1500 字符，实际 {len(content)}"
