from __future__ import annotations

from pathlib import Path

from nanobot.writing.database import WritingDatabase, compute_workspace_key


def _tables(db: WritingDatabase) -> set[str]:
    with db.connect() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row["name"] for row in rows}


def test_ensure_schema_is_idempotent(tmp_path: Path) -> None:
    db = WritingDatabase(tmp_path)
    db.ensure_schema()
    db.ensure_schema()
    assert {"articles", "article_requests", "_schema_meta"} <= _tables(db)


def test_composite_primary_key_isolates_workspaces(tmp_path: Path) -> None:
    db = WritingDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        for key in ("a" * 16, "b" * 16):
            conn.execute(
                "INSERT INTO articles (id, workspace_key, title, content_digest,"
                " created_at, updated_at) VALUES (?, ?, '标题', 'x', 't1', 't1')",
                ("same-id", key),
            )
        rows = conn.execute(
            "SELECT workspace_key FROM articles WHERE id = 'same-id' ORDER BY workspace_key",
        ).fetchall()
    assert [row["workspace_key"] for row in rows] == ["a" * 16, "b" * 16]


def test_workspace_key_is_path_scoped_hex(tmp_path: Path) -> None:
    first = compute_workspace_key(tmp_path)
    assert len(first) == 16
    assert first == compute_workspace_key(tmp_path)
    assert first != compute_workspace_key(tmp_path / "other")
