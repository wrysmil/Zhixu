"""旧 memories → MEMORY.md / 经验库迁移测试。"""

from __future__ import annotations

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.migrate_memories import (
    ARCHIVED_AT,
    Action,
    apply_migration,
    plan_migration,
)
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.repository import add_memory


def _seed(db: MemoryDatabase) -> None:
    now = "2026-11-01T00:00:00+00:00"
    rows = [
        Memory(
            id="m-fact",
            content="项目构建用 bun",
            type=MemoryType.FACT,
            priority=MemoryPriority.LONG_TERM,
            created_at=now,
            updated_at=now,
            workspace_id="default",
        ),
        Memory(
            id="m-pref",
            content="用户偏好中文",
            type=MemoryType.PREFERENCE,
            priority=MemoryPriority.LONG_TERM,
            created_at=now,
            updated_at=now,
            workspace_id="default",
        ),
        Memory(
            id="m-exp",
            content="构建失败：缺少依赖",
            type=MemoryType.EXPERIENCE,
            priority=MemoryPriority.LONG_TERM,
            created_at=now,
            updated_at=now,
            workspace_id="default",
        ),
        Memory(
            id="m-short",
            content="本周临时事项",
            type=MemoryType.FACT,
            priority=MemoryPriority.SHORT_TERM,
            created_at=now,
            updated_at=now,
            workspace_id="default",
        ),
    ]
    with db.connect() as conn:
        for row in rows:
            add_memory(conn, row)


def test_plan_classifies_rows(tmp_path):
    db = MemoryDatabase(tmp_path, db_path=tmp_path / "state.db")
    db.init_schema()
    _seed(db)

    items = {item.memory_id: item for item in plan_migration(db, workspace_id="default")}

    assert items["m-fact"].action is Action.FILE
    assert items["m-fact"].scope == "project"
    assert items["m-pref"].action is Action.FILE
    assert items["m-pref"].scope == "global"
    assert items["m-exp"].action is Action.EXPERIENCE
    assert items["m-short"].action is Action.ARCHIVE_ONLY


def test_apply_writes_docs_and_archives_rows(tmp_path, monkeypatch):
    db = MemoryDatabase(tmp_path, db_path=tmp_path / "state.db")
    db.init_schema()
    _seed(db)
    global_dir = tmp_path / "global"
    project_dir = tmp_path / "project"
    monkeypatch.setattr(
        "nanobot.memory.migrate_memories.global_memory_dir", lambda: global_dir
    )
    monkeypatch.setattr(
        "nanobot.memory.migrate_memories.project_memory_dir", lambda _wid: project_dir
    )

    counts = apply_migration(db, plan_migration(db, workspace_id="default"))

    assert counts["file"] == 2
    assert counts["experience"] == 1
    assert "项目构建用 bun" in (project_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "用户偏好中文" in (global_dir / "MEMORY.md").read_text(encoding="utf-8")

    with db.connect() as conn:
        archived = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE expires_at = ?", (ARCHIVED_AT,)
        ).fetchone()[0]
        experiences = conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE source='experience'"
        ).fetchone()[0]
    assert archived == 4
    assert experiences == 1
