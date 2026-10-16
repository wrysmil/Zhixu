"""采纳与撤销事务（契约 §5.3 / §5.4 / spec §13 写入动作）。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from nanobot.writing.collab.apply import apply_batch, apply_suggestion, undo
from nanobot.writing.collab.database import ensure_collab_schema
from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import ArticleLimitError, new_id, utc_now_iso
from nanobot.writing.repository import ArticleConflict, ArticleRepository

BODY = "第一段。\n第二段。"
FIRST_SPAN = (0, 4)
SECOND_SPAN = (5, 9)


def _snapshot(**overrides: Any) -> dict[str, Any]:
    base = {
        "title": "标题", "writing_type": "", "template_id": "", "status": "draft",
        "summary": "", "brief": {}, "body": BODY, "outline": [], "materials": [],
        "tags": [],
    }
    base.update(overrides)
    return base


@pytest.fixture
def store(tmp_path):
    db = WritingDatabase(tmp_path)
    db.ensure_schema()
    ensure_collab_schema(db)
    articles = ArticleRepository(db)
    article = articles.create_article("create-1", _snapshot())["article"]
    return db, articles, article


def _replace(span: tuple[int, int], expected: str) -> dict[str, Any]:
    return {
        "kind": "replace_range", "start": span[0], "end": span[1],
        "expected_before": expected,
    }


def _add_suggestion(
    db: WritingDatabase,
    article: dict[str, Any],
    *,
    target: dict[str, Any],
    after: str,
    status: str = "pending",
    base_revision: int | None = None,
) -> str:
    suggestion_id = new_id()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO writing_collab_suggestions (id, workspace_key, collaboration_id,"
            " artifact_id, article_id, base_revision, snapshot_digest, target_json,"
            " before_text, after_text, summary, notes, source_material_ids, status,"
            " created_at) VALUES (?, ?, 'c-1', '', ?, ?, '', ?, '', ?, '', '', '[]', ?, ?)",
            (
                suggestion_id, db.workspace_key, article["id"],
                article["revision"] if base_revision is None else base_revision,
                json.dumps(target, ensure_ascii=False), after, status, utc_now_iso(),
            ),
        )
    return suggestion_id


def _suggestion_status(db: WritingDatabase, suggestion_id: str) -> str:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT status, applied_revision FROM writing_collab_suggestions"
            " WHERE workspace_key = ? AND id = ?",
            (db.workspace_key, suggestion_id),
        ).fetchone()
    return str(row["status"])


def _applied_revision(db: WritingDatabase, suggestion_id: str) -> int | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT applied_revision FROM writing_collab_suggestions"
            " WHERE workspace_key = ? AND id = ?",
            (db.workspace_key, suggestion_id),
        ).fetchone()
    return None if row["applied_revision"] is None else int(row["applied_revision"])


def _article_state(db: WritingDatabase, article_id: str) -> tuple[int, str, str]:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT revision, content_digest, body FROM articles"
            " WHERE workspace_key = ? AND id = ?",
            (db.workspace_key, article_id),
        ).fetchone()
    return int(row["revision"]), str(row["content_digest"]), str(row["body"])


def _undo_reverted(db: WritingDatabase, undo_id: str) -> int:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT reverted, reverted_at FROM writing_collab_undo_entries"
            " WHERE workspace_key = ? AND id = ?",
            (db.workspace_key, undo_id),
        ).fetchone()
    if int(row["reverted"]):
        assert row["reverted_at"]
    return int(row["reverted"])


def test_apply_advances_revision_once(store) -> None:
    db, articles, article = store
    suggestion_id = _add_suggestion(
        db, article, target=_replace(FIRST_SPAN, "第一段。"), after="改写段。",
    )

    applied = apply_suggestion(
        db, suggestion_id, article_id=article["id"], base_revision=1,
        client_request_id="ap-1",
    )

    assert applied["suggestion_id"] == suggestion_id
    assert applied["article_id"] == article["id"]
    assert applied["revision"] == 2
    assert applied["undo_id"]
    assert applied["staled_suggestion_ids"] == []
    revision, digest, body = _article_state(db, article["id"])
    assert revision == 2
    assert body == "改写段。\n第二段。"
    assert applied["digest"] == digest
    assert articles.get_article(article["id"])["article"]["revision"] == 2
    assert _suggestion_status(db, suggestion_id) == "applied"
    assert _applied_revision(db, suggestion_id) == 2


def test_apply_marks_same_base_suggestions_stale(store) -> None:
    db, _articles, article = store
    first = _add_suggestion(db, article, target=_replace(FIRST_SPAN, "第一段。"), after="改写段。")
    second = _add_suggestion(db, article, target=_replace(SECOND_SPAN, "第二段。"), after="换过的。")

    applied = apply_suggestion(
        db, first, article_id=article["id"], base_revision=1, client_request_id="ap-1",
    )

    assert applied["staled_suggestion_ids"] == [second]
    assert _suggestion_status(db, first) == "applied"
    assert _suggestion_status(db, second) == "stale"


def test_apply_rejects_non_pending(store) -> None:
    db, _articles, article = store
    suggestion_id = _add_suggestion(
        db, article, target=_replace(FIRST_SPAN, "第一段。"), after="改写段。",
    )
    apply_suggestion(
        db, suggestion_id, article_id=article["id"], base_revision=1,
        client_request_id="ap-1",
    )

    with pytest.raises(ArticleLimitError) as error:
        apply_suggestion(
            db, suggestion_id, article_id=article["id"], base_revision=1,
            client_request_id="ap-2",
        )
    assert (error.value.status, error.value.message) == (409, "suggestion is not pending")
    assert _article_state(db, article["id"])[0] == 2


def test_apply_rejects_stale_base_revision(store) -> None:
    db, articles, article = store
    suggestion_id = _add_suggestion(
        db, article, target=_replace(FIRST_SPAN, "第一段。"), after="改写段。",
    )
    articles.save_snapshot(
        article["id"], 1, _snapshot(body="别人改过。\n第二段。"), client_request_id="other-1",
    )

    with pytest.raises(ArticleConflict) as error:
        apply_suggestion(
            db, suggestion_id, article_id=article["id"], base_revision=1,
            client_request_id="ap-1",
        )
    assert str(error.value) == f"article_conflict current_revision=2 article_id={article['id']}"
    assert _suggestion_status(db, suggestion_id) == "pending"


def test_batch_apply_single_transaction(store) -> None:
    db, _articles, article = store
    first = _add_suggestion(db, article, target=_replace(FIRST_SPAN, "第一段。"), after="改写段。")
    second = _add_suggestion(db, article, target=_replace(SECOND_SPAN, "第二段。"), after="换过的。")

    applied = apply_batch(
        db, [first, second], article_id=article["id"], base_revision=1,
        client_request_id="ap-1",
    )

    assert applied["suggestion_ids"] == [first, second]
    assert applied["revision"] == 2  # 多个不重叠补丁只推进一次 revision
    assert _article_state(db, article["id"]) == (
        2, applied["digest"], "改写段。\n换过的。",
    )
    assert _suggestion_status(db, first) == "applied"
    assert _suggestion_status(db, second) == "applied"
    assert applied["staled_suggestion_ids"] == []


def test_undo_blocks_on_newer_edit(store) -> None:
    db, articles, article = store
    suggestion_id = _add_suggestion(
        db, article, target=_replace(FIRST_SPAN, "第一段。"), after="改写段。",
    )
    applied = apply_suggestion(
        db, suggestion_id, article_id=article["id"], base_revision=1,
        client_request_id="ap-1",
    )
    articles.save_snapshot(
        article["id"], 2, _snapshot(body="改写段。\n第二段。\n补充。"), client_request_id="edit-1",
    )

    with pytest.raises(ArticleLimitError) as error:
        undo(
            db, applied["undo_id"], base_revision=applied["revision"],
            client_request_id="un-1",
        )
    assert (error.value.status, error.value.message) == (409, "undo blocked by newer edits")
    assert _article_state(db, article["id"])[0] == 3
    assert _suggestion_status(db, suggestion_id) == "applied"
    assert _undo_reverted(db, applied["undo_id"]) == 0


def test_undo_creates_new_revision(store) -> None:
    db, _articles, article = store
    suggestion_id = _add_suggestion(
        db, article, target=_replace(FIRST_SPAN, "第一段。"), after="改写段。",
    )
    applied = apply_suggestion(
        db, suggestion_id, article_id=article["id"], base_revision=1,
        client_request_id="ap-1",
    )

    undone = undo(
        db, applied["undo_id"], base_revision=applied["revision"], client_request_id="un-1",
    )

    assert undone["undo_id"] == applied["undo_id"]
    assert undone["suggestion_id"] == suggestion_id
    assert undone["article_id"] == article["id"]
    assert undone["revision"] == applied["revision"] + 1 == 3  # 版本只前进，不回拨
    revision, digest, body = _article_state(db, article["id"])
    assert revision == 3
    assert body == BODY
    assert undone["digest"] == digest
    assert _suggestion_status(db, suggestion_id) == "reverted"
    assert _undo_reverted(db, applied["undo_id"]) == 1

    with pytest.raises(ArticleLimitError) as error:
        undo(db, applied["undo_id"], base_revision=3, client_request_id="un-2")
    assert (error.value.status, error.value.message) == (409, "undo already applied")


def test_apply_request_id_idempotent(store) -> None:
    db, _articles, article = store
    suggestion_id = _add_suggestion(
        db, article, target=_replace(FIRST_SPAN, "第一段。"), after="改写段。",
    )

    first = apply_suggestion(
        db, suggestion_id, article_id=article["id"], base_revision=1,
        client_request_id="ap-1",
    )
    replay = apply_suggestion(
        db, suggestion_id, article_id=article["id"], base_revision=1,
        client_request_id="ap-1",
    )

    assert replay == first
    assert _article_state(db, article["id"]) == (2, first["digest"], "改写段。\n第二段。")
    assert _suggestion_status(db, suggestion_id) == "applied"
    assert _applied_revision(db, suggestion_id) == 2
