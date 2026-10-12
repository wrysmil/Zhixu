"""写作域 payload 层（``writing_api`` / ``writing_services``）单测。

契约 ``.ai-runtime-artifacts/contracts/2026-10-12-contract-writing-article-store.md``
的 §4 / §5 / §5.3 / §6 是唯一口径：本文件逐条断言返回形状与错误 message 原文，
不依赖 handler（handler 侧的取参校验见 ``test_writing_routes.py``）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nanobot.webui import writing_api
from nanobot.webui.settings_contracts import WebUISettingsError
from nanobot.webui.writing_services import WritingServices


@pytest.fixture
def services(tmp_path: Path) -> WritingServices:
    return WritingServices.for_workspace("default", tmp_path)


def _created(services: WritingServices, title: str = "标题", **kwargs: Any) -> dict[str, Any]:
    return writing_api.create_article(
        services, client_request_id=kwargs.pop("client_request_id", "r-1"),
        title=title, **kwargs,
    )


# ---- §5 写入形状 ------------------------------------------------------------


def test_create_payload_shape(services: WritingServices) -> None:
    payload = writing_api.create_article(
        services, client_request_id="r-1", title="标题", template_id="tech-explainer",
    )
    article = payload["article"]
    assert set(article) >= {
        "id", "workspace_key", "title", "writing_type", "template_id", "status",
        "summary", "brief", "body", "outline", "materials", "tags", "revision",
        "created_at", "updated_at",
    }
    assert article["revision"] == 1
    assert article["status"] == "idea"


def test_create_is_idempotent_on_client_request_id(services: WritingServices) -> None:
    first = _created(services, "甲", client_request_id="r-1")
    again = _created(services, "甲", client_request_id="r-1")
    assert again == first
    listed = writing_api.list_articles(
        services, query=None, status=None, limit=None, offset=None,
    )
    assert listed["total"] == 1


def test_replay_with_different_payload_is_409(services: WritingServices) -> None:
    _created(services, "甲", client_request_id="r-1")
    with pytest.raises(WebUISettingsError) as excinfo:
        _created(services, "乙", client_request_id="r-1")
    assert excinfo.value.status == 409
    assert excinfo.value.message == (
        "request_id was already used for a different WebUI mutation"
    )


def test_save_payload_shape_and_revision_bump(services: WritingServices) -> None:
    article = _created(services)["article"]
    payload = writing_api.save_article(
        services, client_request_id="r-2", article_id=article["id"], base_revision=1,
        snapshot={"title": "标题", "body": "正文"},
    )
    assert payload["article_id"] == article["id"]
    assert payload["revision"] == 2
    assert payload["no_change"] is False
    assert set(payload) == {"article_id", "revision", "updated_at", "digest", "no_change"}


def test_same_content_save_does_not_advance_revision(services: WritingServices) -> None:
    article = _created(services)["article"]
    snapshot = {"title": "标题", "body": "正文", "status": "idea", "tags": []}
    writing_api.save_article(
        services, client_request_id="r-2", article_id=article["id"], base_revision=1,
        snapshot=snapshot,
    )
    again = writing_api.save_article(
        services, client_request_id="r-3", article_id=article["id"], base_revision=2,
        snapshot=snapshot,
    )
    assert again["no_change"] is True
    assert again["revision"] == 2


def test_duplicate_resets_revision_and_new_id(services: WritingServices) -> None:
    article = _created(services)["article"]
    payload = writing_api.duplicate_article(
        services, client_request_id="r-2", article_id=article["id"], base_revision=1,
    )
    copy = payload["article"]
    assert copy["id"] != article["id"]
    assert copy["revision"] == 1


def test_delete_payload_shape(services: WritingServices) -> None:
    article = _created(services)["article"]
    payload = writing_api.delete_article(
        services, client_request_id="r-2", article_id=article["id"], base_revision=1,
    )
    assert payload == {"deleted_article_id": article["id"], "revision": 1}


# ---- §4 读取形状 ------------------------------------------------------------


def test_list_payload_has_paging(services: WritingServices) -> None:
    _created(services, "甲")
    payload = writing_api.list_articles(
        services, query=None, status=None, limit=None, offset=None,
    )
    assert payload["total"] == 1 and payload["limit"] == 50 and payload["offset"] == 0
    assert "body" not in payload["articles"][0]
    assert set(payload["articles"][0]) == {
        "id", "title", "summary", "status", "writing_type", "tags", "revision",
        "created_at", "updated_at",
    }


def test_list_limit_is_capped_at_200(services: WritingServices) -> None:
    payload = writing_api.list_articles(
        services, query=None, status=None, limit=5000, offset=0,
    )
    assert payload["limit"] == 200


def test_fetch_returns_full_article(services: WritingServices) -> None:
    article = _created(services)["article"]
    payload = writing_api.fetch_article(services, article["id"])
    assert payload["article"]["id"] == article["id"]
    assert set(payload["article"]) >= {"body", "outline", "materials", "brief"}


# ---- §6 错误契约原文 --------------------------------------------------------


def test_conflict_becomes_contract_message(services: WritingServices) -> None:
    article = _created(services, "甲")["article"]
    writing_api.save_article(
        services, client_request_id="r-2", article_id=article["id"], base_revision=1,
        snapshot={"title": "甲", "body": "一"},
    )
    with pytest.raises(WebUISettingsError) as excinfo:
        writing_api.save_article(
            services, client_request_id="r-3", article_id=article["id"], base_revision=1,
            snapshot={"title": "甲", "body": "二"},
        )
    assert excinfo.value.status == 409
    assert excinfo.value.message == (
        f"article_conflict current_revision=2 article_id={article['id']}"
    )


def test_get_missing_article_is_404(services: WritingServices) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        writing_api.fetch_article(services, "nope")
    assert (excinfo.value.status, excinfo.value.message) == (404, "article not found")


def test_missing_article_id_is_400(services: WritingServices) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        writing_api.fetch_article(services, "   ")
    assert (excinfo.value.status, excinfo.value.message) == (400, "article id is required")


def test_deleted_article_late_save_is_409(services: WritingServices) -> None:
    article = _created(services)["article"]
    writing_api.delete_article(
        services, client_request_id="r-2", article_id=article["id"], base_revision=1,
    )
    with pytest.raises(WebUISettingsError) as excinfo:
        writing_api.save_article(
            services, client_request_id="r-3", article_id=article["id"], base_revision=1,
            snapshot={"title": "标题", "body": "复活"},
        )
    assert (excinfo.value.status, excinfo.value.message) == (409, "article deleted")


def test_bad_client_request_id_is_400(services: WritingServices) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        writing_api.create_article(services, client_request_id="bad id!", title="标题")
    assert (excinfo.value.status, excinfo.value.message) == (
        400, "client_request_id is required",
    )


def test_non_integer_limit_is_400(services: WritingServices) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        writing_api.list_articles(
            services, query=None, status=None, limit="abc", offset=None,
        )
    assert (excinfo.value.status, excinfo.value.message) == (400, "limit must be an integer")


def test_unsupported_status_is_400(services: WritingServices) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        writing_api.list_articles(
            services, query=None, status="已完成", limit=None, offset=None,
        )
    assert (excinfo.value.status, excinfo.value.message) == (400, "unsupported article status")


def test_empty_title_is_400(services: WritingServices) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        _created(services, "   ")
    assert (excinfo.value.status, excinfo.value.message) == (400, "article title is required")


# ---- §5.3 上限（拒绝写入并保留原稿）----------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "status", "message"),
    [
        pytest.param("title", "长" * 201, 400, "article title is too long", id="title"),
        pytest.param("summary", "长" * 501, 400, "article summary is too long", id="summary"),
        pytest.param(
            "tags", ["t" * 51], 400, "article tags limit exceeded", id="tag-chars",
        ),
        pytest.param(
            "tags", ["t" for _ in range(21)], 400, "article tags limit exceeded",
            id="tag-count",
        ),
        pytest.param("body", "字" * 200_001, 413, "article body is too long", id="body"),
        pytest.param(
            "outline", [{"text": "条目"}] * 201, 400, "article outline limit exceeded",
            id="outline-count",
        ),
        pytest.param(
            "outline", [{"text": "条" * 201}], 400, "article outline limit exceeded",
            id="outline-chars",
        ),
        pytest.param(
            "materials", [{"type": "note", "content": "x" * 20_001}],
            400, "article materials limit exceeded", id="material-content",
        ),
        pytest.param(
            "materials", [{"type": "note"}] * 101,
            400, "article materials limit exceeded", id="material-count",
        ),
        pytest.param(
            "materials", [{"type": "link", "url": "https://" + "u" * 2_000}],
            400, "article materials limit exceeded", id="material-url",
        ),
    ],
)
def test_create_limit_errors(
    services: WritingServices, field: str, value: Any, status: int, message: str,
) -> None:
    """超限一律拒绝写入（契约 §5.3），错误 message 用契约原文而非字段级变体。"""
    with pytest.raises(WebUISettingsError) as excinfo:
        writing_api.create_article(
            services, client_request_id="r-1",
            **({"title": value} if field == "title" else {
                "title": "标题", field: value,
            }),
        )
    assert (excinfo.value.status, excinfo.value.message) == (status, message)


def test_snapshot_too_large_is_413(services: WritingServices) -> None:
    article = _created(services)["article"]
    with pytest.raises(WebUISettingsError) as excinfo:
        writing_api.save_article(
            services, client_request_id="r-2", article_id=article["id"], base_revision=1,
            snapshot={"title": "标题", "body": "字" * 200_001},
        )
    assert (excinfo.value.status, excinfo.value.message) == (413, "article body is too long")


def test_material_url_must_be_http(services: WritingServices) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        writing_api.create_article(
            services, client_request_id="r-1", title="标题",
            materials=[{"type": "link", "url": "ftp://x/y"}],
        )
    assert (excinfo.value.status, excinfo.value.message) == (
        400, "article material url must be http(s)",
    )


def test_bad_field_type_is_400(services: WritingServices) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        writing_api.create_article(
            services, client_request_id="r-1", title="标题", body=123,
        )
    assert (excinfo.value.status, excinfo.value.message) == (
        400, "unsupported article field: body",
    )


# ---- services 边界 ----------------------------------------------------------


def test_services_are_workspace_scoped(tmp_path: Path) -> None:
    """不同工作区各自一个库：读写都带 workspace_key，跨区不可见。"""
    left = WritingServices.for_workspace("left", tmp_path / "left")
    right = WritingServices.for_workspace("right", tmp_path / "right")
    created = writing_api.create_article(left, client_request_id="r-1", title="仅左区")
    assert writing_api.list_articles(
        right, query=None, status=None, limit=None, offset=None,
    )["total"] == 0
    with pytest.raises(WebUISettingsError) as excinfo:
        writing_api.fetch_article(right, created["article"]["id"])
    assert (excinfo.value.status, excinfo.value.message) == (404, "article not found")
    assert writing_api.fetch_article(
        left, created["article"]["id"]
    )["article"]["title"] == "仅左区"
