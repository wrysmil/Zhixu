"""写作域 handler（``writing_routes``）单测。

口径来自契约 ``.ai-runtime-artifacts/contracts/2026-10-12-contract-writing-article-store.md``
§4 / §5 / §6：动作名一律点号；协议级校验（``client_request_id``、
``article_id``、``base_revision``、query 取参）在本层负责，§6 的 8 条 message
逐字断言。
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

import pytest

from nanobot.webui import writing_api
from nanobot.webui.settings_contracts import SettingsRequest
from nanobot.webui.writing_routes import (
    WRITING_ACTION_NAMES,
    WritingSettingsHandler,
    WritingSettingsOperations,
)
from nanobot.webui.writing_services import WritingServices
from nanobot.writing import apply as writing_apply
from nanobot.writing.apply import WritingActor


@pytest.fixture
def services(tmp_path: Path) -> WritingServices:
    return WritingServices.for_workspace("default", tmp_path)


@pytest.fixture
def operations(services: WritingServices) -> WritingSettingsOperations:
    return WritingSettingsOperations(
        list_articles=partial(writing_api.list_articles, services),
        fetch_article=partial(writing_api.fetch_article, services),
        create_article=partial(writing_api.create_article, services),
        save_article=partial(writing_api.save_article, services),
        duplicate_article=partial(writing_api.duplicate_article, services),
        delete_article=partial(writing_api.delete_article, services),
        undo_change=partial(writing_api.undo_change, services),
    )


@pytest.fixture
def handler(operations: WritingSettingsOperations) -> WritingSettingsHandler:
    return WritingSettingsHandler(operations)


def _request(
    payload: dict[str, Any] | None = None,
    *,
    query: dict[str, list[str]] | None = None,
) -> SettingsRequest:
    return SettingsRequest(query=query or {}, payload=payload)


def _article(handler: WritingSettingsHandler, title: str = "标题") -> dict[str, Any]:
    result = handler.handle(
        "article.create", _request({"client_request_id": "r-1", "title": title}),
    )
    assert result.status == 200, result.error
    return dict(result.payload["article"])


# ---- 动作名单 ---------------------------------------------------------------


def test_action_names_are_dotted_and_exactly_seven() -> None:
    """契约 §4/§5：动作名统一取点号（连字符会被 WS 传输层 400 拒掉）。"""
    assert WRITING_ACTION_NAMES == frozenset({
        "article.list", "article.get", "article.create",
        "article.save", "article.duplicate", "article.delete",
        "writing.undo_change",
    })
    assert all("-" not in action for action in WRITING_ACTION_NAMES)


# ---- 六个动作的正常路径 ----------------------------------------------------


def test_handler_dispatches_all_article_actions(handler: WritingSettingsHandler) -> None:
    created = handler.handle(
        "article.create", _request({"client_request_id": "r-1", "title": "标题"}),
    )
    assert created.status == 200 and created.payload["article"]["revision"] == 1

    article_id = created.payload["article"]["id"]
    listed = handler.handle("article.list", _request({}, query={"limit": ["10"]}))
    assert listed.payload["total"] == 1

    fetched = handler.handle("article.get", _request({}, query={"id": [article_id]}))
    assert fetched.payload["article"]["id"] == article_id

    saved = handler.handle("article.save", _request({
        "client_request_id": "r-2", "article_id": article_id, "base_revision": 1,
        "snapshot": {"title": "标题", "body": "正文"},
    }))
    assert saved.payload["revision"] == 2

    duplicated = handler.handle("article.duplicate", _request({
        "client_request_id": "r-3", "article_id": article_id, "base_revision": 2,
    }))
    assert duplicated.payload["article"]["revision"] == 1

    deleted = handler.handle("article.delete", _request({
        "client_request_id": "r-4", "article_id": article_id, "base_revision": 2,
    }))
    assert deleted.payload["deleted_article_id"] == article_id


def test_undo_change_restores_team_write(
    handler: WritingSettingsHandler, services: WritingServices
) -> None:
    """契约 §5：撤销只回滚该次写入，并产生 revision+1；客户端拿 change_id 即可。"""
    created = handler.handle("article.create", _request({
        "client_request_id": "r-1", "title": "原文", "body": "原文正文",
    }))
    article = dict(created.payload["article"])
    assert services.database is not None
    applied = writing_apply.apply_change(
        services.database,
        article_id=article["id"],
        base_revision=1,
        base_digest=article["digest"],
        change={"kind": "body", "mode": "replace_body", "content": "团队改写"},
        actor=WritingActor(member_id="writing-author"),
        request_fingerprint="fp-1",
        client_request_id="apply-1",
    )
    assert applied["revision"] == 2

    undone = handler.handle("writing.undo_change", _request({
        "client_request_id": "undo-1",
        "change_id": applied["change_id"],
        "base_revision": 2,
    }))
    assert undone.status == 200, undone.error
    assert undone.payload["revision"] == 3
    fetched = handler.handle("article.get", _request({}, query={"id": [article["id"]]}))
    assert fetched.payload["article"]["body"] == "原文正文"

    revived = handler.handle("writing.undo_change", _request({
        "client_request_id": "undo-2",
        "change_id": applied["change_id"],
        "base_revision": 3,
    }))
    assert revived.status == 409
    assert revived.error == "writing change already reverted"


def test_undo_requires_wired_database() -> None:
    """未注入撤销操作时回 503，而不是 AttributeError。"""
    handler = WritingSettingsHandler(WritingSettingsOperations(
        list_articles=lambda **_kwargs: {},
        fetch_article=lambda _article_id: {},
        create_article=lambda **_kwargs: {},
        save_article=lambda **_kwargs: {},
        duplicate_article=lambda **_kwargs: {},
        delete_article=lambda **_kwargs: {},
    ))
    result = handler.handle("writing.undo_change", _request({
        "client_request_id": "undo-1", "change_id": "c-1", "base_revision": 1,
    }))
    assert result.status == 503
    assert result.error == "writing service is not configured"


def test_reads_accept_query_filters(handler: WritingSettingsHandler) -> None:
    """契约 §4 的 q/status/limit/offset 全部从 query 取。"""
    created = _article(handler, "深度学习笔记")
    assert created["id"]
    handler.handle(
        "article.create", _request({"client_request_id": "r-2", "title": "读书摘要"}),
    )
    searched = handler.handle("article.list", _request({}, query={"q": ["深度"]}))
    assert [a["title"] for a in searched.payload["articles"]] == ["深度学习笔记"]

    filtered = handler.handle(
        "article.list", _request({}, query={"status": ["idea"], "limit": ["1"], "offset": ["1"]}),
    )
    assert filtered.payload["total"] == 2
    assert filtered.payload["limit"] == 1 and filtered.payload["offset"] == 1
    assert len(filtered.payload["articles"]) == 1


def test_get_falls_back_to_payload_id(handler: WritingSettingsHandler) -> None:
    article = _article(handler)
    result = handler.handle("article.get", _request({"id": article["id"]}))
    assert result.payload["article"]["id"] == article["id"]


def test_create_forwards_optional_fields(handler: WritingSettingsHandler) -> None:
    result = handler.handle("article.create", _request({
        "client_request_id": "r-1", "title": "标题", "template_id": "tech-explainer",
        "writing_type": "技术解读", "summary": "摘要", "status": "draft",
        "brief": {"goal": "讲清楚"}, "body": "正文",
        "outline": [{"text": "一"}], "materials": [{"type": "note", "content": "素材"}],
        "tags": ["ai"],
    }))
    article = result.payload["article"]
    assert article["template_id"] == "tech-explainer"
    assert article["status"] == "draft"
    assert article["brief"] == {"goal": "讲清楚"}
    assert article["outline"][0]["text"] == "一"
    assert article["materials"][0]["content"] == "素材"
    assert article["tags"] == ["ai"]


# ---- 契约 §6：8 条 message 逐字 -------------------------------------------


def test_unknown_action_is_404(handler: WritingSettingsHandler) -> None:
    result = handler.handle("article.teleport", _request({}))
    assert (result.status, result.error) == (404, "unknown writing action: article.teleport")


def test_missing_client_request_id_is_400(handler: WritingSettingsHandler) -> None:
    result = handler.handle(
        "article.save",
        _request({"article_id": "a", "base_revision": 1, "snapshot": {}}),
    )
    assert (result.status, result.error) == (400, "client_request_id is required")


@pytest.mark.parametrize("bad", ["", "has space", "中文", "a" * 129])
def test_illegal_client_request_id_is_400(
    handler: WritingSettingsHandler, bad: str,
) -> None:
    result = handler.handle(
        "article.create", _request({"client_request_id": bad, "title": "标题"}),
    )
    assert (result.status, result.error) == (400, "client_request_id is required")


def test_missing_base_revision_is_400(handler: WritingSettingsHandler) -> None:
    result = handler.handle(
        "article.save",
        _request({"client_request_id": "r", "article_id": "a"}),
    )
    assert (result.status, result.error) == (400, "base_revision is required")


def test_missing_article_id_is_400(handler: WritingSettingsHandler) -> None:
    result = handler.handle(
        "article.delete", _request({"client_request_id": "r", "base_revision": 1}),
    )
    assert (result.status, result.error) == (400, "article id is required")


def test_missing_article_is_404(handler: WritingSettingsHandler) -> None:
    result = handler.handle("article.get", _request({}, query={"id": ["nope"]}))
    assert (result.status, result.error) == (404, "article not found")


def test_conflict_message_is_contract_literal(handler: WritingSettingsHandler) -> None:
    """409 + 契约 §6 原文：article_conflict current_revision=<int> article_id=<id>。"""
    article = _article(handler)
    handler.handle(
        "article.save",
        _request({
            "client_request_id": "r-2", "article_id": article["id"],
            "base_revision": 1, "snapshot": {"title": "标题", "body": "新"},
        }),
    )
    stale = handler.handle(
        "article.save",
        _request({
            "client_request_id": "r-3", "article_id": article["id"],
            "base_revision": 1, "snapshot": {"title": "标题", "body": "旧"},
        }),
    )
    assert stale.status == 409
    assert stale.error == f"article_conflict current_revision=2 article_id={article['id']}"


def test_deleted_article_message_is_409(handler: WritingSettingsHandler) -> None:
    """A-T10：删除后晚到保存不得复活文章（契约 §6 ``article deleted``）。"""
    article = _article(handler)
    handler.handle(
        "article.delete",
        _request({"client_request_id": "r-2", "article_id": article["id"], "base_revision": 1}),
    )
    late = handler.handle(
        "article.save",
        _request({
            "client_request_id": "r-3", "article_id": article["id"],
            "base_revision": 1, "snapshot": {"title": "标题", "body": "复活"},
        }),
    )
    assert (late.status, late.error) == (409, "article deleted")


def test_unsupported_field_is_400(handler: WritingSettingsHandler) -> None:
    result = handler.handle("article.create", _request({
        "client_request_id": "r-1", "title": "标题", "outline": "不是列表",
    }))
    assert (result.status, result.error) == (400, "unsupported article field: outline")


def test_limit_query_non_integer_is_400(handler: WritingSettingsHandler) -> None:
    result = handler.handle("article.list", _request({}, query={"limit": ["abc"]}))
    assert (result.status, result.error) == (400, "limit must be an integer")


def test_unsupported_status_query_is_400(handler: WritingSettingsHandler) -> None:
    result = handler.handle("article.list", _request({}, query={"status": ["已完成"]}))
    assert (result.status, result.error) == (400, "unsupported article status")


# ---- 缺参的严格策略 --------------------------------------------------------


def test_missing_snapshot_is_400(handler: WritingSettingsHandler) -> None:
    """保存不做服务端补齐：缺 snapshot 直接 400，避免半保存。"""
    article = _article(handler)
    result = handler.handle("article.save", _request({
        "client_request_id": "r-2", "article_id": article["id"], "base_revision": 1,
    }))
    assert (result.status, result.error) == (400, "article snapshot is required")


def test_missing_title_is_400(handler: WritingSettingsHandler) -> None:
    result = handler.handle("article.create", _request({"client_request_id": "r-1"}))
    assert (result.status, result.error) == (400, "article title is required")


def test_null_payload_is_400(handler: WritingSettingsHandler) -> None:
    result = handler.handle("article.create", SettingsRequest(query={}, payload=None))
    assert (result.status, result.error) == (400, "client_request_id is required")


def test_replay_conflict_message_is_contract_literal(
    handler: WritingSettingsHandler,
) -> None:
    """同一 client_request_id 换内容 → 409（契约 §1 推论 1：业务级去重靠 payload）。"""
    handler.handle("article.create", _request({"client_request_id": "r-1", "title": "甲"}))
    result = handler.handle(
        "article.create", _request({"client_request_id": "r-1", "title": "乙"}),
    )
    assert (result.status, result.error) == (
        409, "request_id was already used for a different WebUI mutation",
    )
