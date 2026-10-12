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


# ---- 契约 §5.3 上限：models 是唯一出口，超限一律拒绝（绝不静默截断）--------


def _assert_limit(payload: dict, message: str, status: int = 400) -> None:
    with pytest.raises(ArticleLimitError) as excinfo:
        normalize_article_fields(payload)
    assert str(excinfo.value) == message
    assert excinfo.value.status == status


def test_tag_over_char_limit_is_rejected_not_dropped() -> None:
    """单条 >50 字符属超限：拒绝写入，不得过滤丢弃（契约 §5.3「绝不截断保存」）。"""
    _assert_limit({"title": "t", "tags": ["t" * 51]}, "article tags limit exceeded")


def test_tag_count_over_limit_is_rejected() -> None:
    _assert_limit(
        {"title": "t", "tags": ["t" for _ in range(21)]},
        "article tags limit exceeded",
    )


def test_tags_within_limits_are_preserved_verbatim() -> None:
    fields = normalize_article_fields({"title": "t", "tags": ["t" * 50, "x"]})
    assert fields["tags"] == ["t" * 50, "x"]


def test_title_over_limit_uses_contract_message() -> None:
    _assert_limit({"title": "长" * 201}, "article title is too long")


def test_summary_over_limit_uses_contract_message() -> None:
    _assert_limit({"title": "t", "summary": "长" * 501}, "article summary is too long")


def test_outline_count_over_limit_is_rejected() -> None:
    _assert_limit(
        {"title": "t", "outline": [{"text": "条目"}] * 201},
        "article outline limit exceeded",
    )


def test_outline_item_over_char_limit_is_rejected() -> None:
    _assert_limit(
        {"title": "t", "outline": [{"text": "条" * 201}]},
        "article outline limit exceeded",
    )


def test_material_content_over_limit_uses_contract_message() -> None:
    """契约对 materials 只登记一条 message，不出现字段级变体。"""
    _assert_limit(
        {"title": "t", "materials": [{"type": "note", "content": "x" * 20_001}]},
        "article materials limit exceeded",
    )


def test_material_url_over_limit_uses_contract_message() -> None:
    _assert_limit(
        {"title": "t", "materials": [{"type": "link", "url": "https://" + "u" * 2_000}]},
        "article materials limit exceeded",
    )


def test_material_count_over_limit_uses_contract_message() -> None:
    _assert_limit(
        {"title": "t", "materials": [{"type": "note"}] * 101},
        "article materials limit exceeded",
    )


def test_snapshot_over_one_mebibyte_is_413() -> None:
    """§5.3 末行：单个字段都不超限，但整快照序列化 >1 MiB 由 ``snapshot_digest`` 拒绝。"""
    fields = normalize_article_fields({
        "title": "t",
        "materials": [{"type": "note", "content": "字" * 20_000} for _ in range(60)],
    })
    with pytest.raises(ArticleLimitError) as excinfo:
        snapshot_digest(fields)
    assert str(excinfo.value) == "article snapshot is too large"
    assert excinfo.value.status == 413
