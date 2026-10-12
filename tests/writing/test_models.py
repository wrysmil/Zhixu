from __future__ import annotations

import pytest

from nanobot.writing.models import (
    ArticleLimitError,
    normalize_article_fields,
    normalize_snapshot,
    snapshot_digest,
)


def _snapshot(**overrides: object) -> dict:
    base: dict = {
        "title": "标题", "writing_type": "tech-explainer", "summary": "",
        "brief": {}, "body": "正文", "outline": [], "materials": [], "tags": [],
        "status": "draft",
    }
    base.update(overrides)
    return base


def test_digest_is_stable_across_key_order() -> None:
    first = normalize_snapshot({"status": "draft", "body": "正文", "title": "标题"})
    second = normalize_snapshot({"body": "正文", "title": "标题", "status": "draft"})
    assert snapshot_digest(first) == snapshot_digest(second)


def test_digest_ignores_revision_and_timestamps() -> None:
    assert snapshot_digest(normalize_snapshot(_snapshot())) == snapshot_digest(
        normalize_snapshot(_snapshot(revision=7, updated_at="2026-01-01T00:00:00+00:00"))
    )


def test_empty_title_rejected() -> None:
    with pytest.raises(ArticleLimitError, match="article title is required"):
        normalize_article_fields({"title": "  "})


def test_body_limit_rejects_without_truncating() -> None:
    with pytest.raises(ArticleLimitError) as excinfo:
        normalize_article_fields({"title": "t", "body": "x" * 200_001})
    assert excinfo.value.status == 413
    assert str(excinfo.value) == "article body is too long"


def test_material_url_must_be_http_https() -> None:
    with pytest.raises(ArticleLimitError, match="article material url must be http\\(s\\)"):
        normalize_article_fields({
            "title": "t",
            "materials": [{
                "id": "m1", "type": "link", "title": "l", "content": "",
                "url": "javascript:alert(1)", "included": True, "order": 0,
            }],
        })


def test_status_enum_is_enforced() -> None:
    with pytest.raises(ArticleLimitError, match="unsupported article status"):
        normalize_article_fields({"title": "t", "status": "已完成"})


def test_outline_item_ids_are_generated_not_index_based() -> None:
    fields = normalize_article_fields({
        "title": "t", "outline": [{"text": "第一节"}, {"text": "第二节"}],
    })
    assert fields["outline"][0]["id"] != fields["outline"][1]["id"]
    assert [item["order"] for item in fields["outline"]] == [0, 1]
