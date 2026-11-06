"""把旧 ``memories`` 表里的长期事实迁到 MEMORY.md，并归档旧行。

用法：
    python -m nanobot.memory.migrate_memories --dry-run
    python -m nanobot.memory.migrate_memories --apply --workspace-id default

原则：
- 先预演，后执行；``--apply`` 才写文件/库。
- 长期事实写 MEMORY.md 正文；experience/error 写经验库；skill 只报告。
- 旧行不物理删除，设 ``expires_at`` 为过去时间并写迁移指针。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.experience import record_experience
from nanobot.memory.files import (
    GLOBAL_SCOPE,
    PROJECT_SCOPE,
    MemoryFileStore,
    global_memory_dir,
    project_memory_dir,
)

ARCHIVED_AT = "1970-01-01T00:00:00Z"
FILE_TYPES = frozenset({"fact", "preference", "rule"})
EXPERIENCE_TYPES = frozenset({"experience", "error"})


class Action(str, Enum):
    FILE = "file"
    EXPERIENCE = "experience"
    ARCHIVE_ONLY = "archive-only"
    REPORT = "report"


@dataclass
class MigrationItem:
    memory_id: str
    content: str
    memory_type: str
    priority: str
    workspace_id: str
    action: Action
    scope: str = ""
    section: str = ""


def _row_metadata(raw: object) -> dict[str, object]:
    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
        except ValueError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _is_expired(raw: object) -> bool:
    text = str(raw or "").strip()
    if not text:
        return False
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return True
    return value <= datetime.now(timezone.utc)


def _classify(row: sqlite3.Row) -> MigrationItem:
    memory_type = str(row["type"] or "").strip().lower()
    priority = str(row["priority"] or "").strip().lower()
    content = str(row["content"] or "").strip()
    workspace_id = str(row["workspace_id"] or "default")
    expired = _is_expired(row["expires_at"])

    if expired or priority == "short_term":
        action = Action.ARCHIVE_ONLY
    elif memory_type in FILE_TYPES:
        action = Action.FILE
    elif memory_type in EXPERIENCE_TYPES:
        action = Action.EXPERIENCE
    else:
        action = Action.REPORT

    scope = GLOBAL_SCOPE if memory_type in {"preference", "rule"} else PROJECT_SCOPE
    section = "用户偏好与规则" if scope == GLOBAL_SCOPE else "项目事实"
    return MigrationItem(
        memory_id=str(row["id"]),
        content=content,
        memory_type=memory_type,
        priority=priority,
        workspace_id=workspace_id,
        action=action,
        scope=scope,
        section=section,
    )


def plan_migration(
    db: MemoryDatabase,
    *,
    workspace_id: str | None = None,
) -> list[MigrationItem]:
    db.ensure_schema()
    conditions = ["1=1"]
    params: list[object] = []
    if workspace_id:
        conditions.append("workspace_id = ?")
        params.append(workspace_id)
    sql = (
        "SELECT id, content, type, priority, workspace_id, expires_at, metadata "
        f"FROM memories WHERE {' AND '.join(conditions)} ORDER BY created_at"
    )
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_classify(row) for row in rows if str(row["content"] or "").strip()]


def _store_for(workspace_id: str) -> MemoryFileStore:
    return MemoryFileStore(global_memory_dir(), project_memory_dir(workspace_id))


def _archive(
    db: MemoryDatabase,
    item: MigrationItem,
    *,
    pointer: dict[str, object],
) -> None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT metadata FROM memories WHERE id = ?", (item.memory_id,)
        ).fetchone()
        metadata = _row_metadata(row["metadata"] if row is not None else "{}")
        metadata["migrated"] = pointer
        conn.execute(
            "UPDATE memories SET expires_at = ?, metadata = ? WHERE id = ?",
            (ARCHIVED_AT, json.dumps(metadata, ensure_ascii=False), item.memory_id),
        )
    try:
        from nanobot.memory.vector.indexer import remove_memory_best_effort

        remove_memory_best_effort(item.memory_id)
    except Exception:  # noqa: BLE001 - 向量清理失败不影响归档
        pass


def apply_migration(
    db: MemoryDatabase,
    items: list[MigrationItem],
    *,
    now: str | None = None,
) -> dict[str, int]:
    now = now or datetime.now(timezone.utc).isoformat()
    counts = {"file": 0, "experience": 0, "archive": 0, "report": 0}

    for item in items:
        if not item.content:
            continue
        if item.action is Action.FILE:
            store = _store_for(item.workspace_id)
            current = store.read_memory_doc(item.scope)
            if item.content in current:
                _archive(
                    db,
                    item,
                    pointer={"scope": item.scope, "duplicate": True},
                )
                counts["archive"] += 1
                continue
            store.append_memory_bullet(
                item.scope,
                item.section,
                f"{item.content}（{now[:10]}）",
            )
            _archive(
                db,
                item,
                pointer={
                    "scope": item.scope,
                    "file": str(store.dir_for(item.scope) / "MEMORY.md"),
                    "section": item.section,
                },
            )
            counts["file"] += 1
            continue

        if item.action is Action.EXPERIENCE:
            episode = record_experience(
                db,
                workspace_id=item.workspace_id,
                session_id="migration",
                title=item.content[:60],
                situation=item.content,
                action="",
                outcome="failed" if item.memory_type == "error" else "completed",
            )
            _archive(
                db,
                item,
                pointer={"episode_id": episode.id},
            )
            counts["experience"] += 1
            continue

        if item.action is Action.ARCHIVE_ONLY:
            _archive(db, item, pointer={"reason": "expired_or_short_term"})
            counts["archive"] += 1
            continue

        counts["report"] += 1

    return counts


def _print_plan(items: list[MigrationItem], *, apply: bool) -> None:
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] memories={len(items)}")
    for action in Action:
        group = [item for item in items if item.action is action]
        print(f"  {action.value}: {len(group)}")
        for item in group[:5]:
            print(f"    - {item.memory_type}/{item.priority} {item.content[:60]}")
        if len(group) > 5:
            print(f"    ... 还有 {len(group) - 5} 条")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy memories to MEMORY.md")
    parser.add_argument("--db", type=Path, default=Path.home() / ".nanobot" / "memory" / "state.db")
    parser.add_argument("--workspace-id", default=None)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    args = parser.parse_args(argv)

    db = MemoryDatabase(args.db.parent, db_path=args.db)
    items = plan_migration(db, workspace_id=args.workspace_id)
    _print_plan(items, apply=args.apply)
    if not args.apply:
        return 0
    counts = apply_migration(db, items)
    print(f"applied: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
