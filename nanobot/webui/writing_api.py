"""写作域 payload 层：契约 §4/§5 的输入输出形状 + §6 的错误 message。

本模块是 repository 异常与 WebUI 错误（``WebUISettingsError``）之间的**唯一**
翻译点——handler（``writing_routes.py``）不再二次包装，因此异常翻译表就是：

| repository 异常                  | status | message                                        |
| -------------------------------- | ------ | ---------------------------------------------- |
| ``ArticleNotFound``              | 404    | ``article not found``                          |
| ``ArticleConflict``              | 409    | ``article_conflict current_revision=<n> article_id=<id>`` |
| ``ArticleLimitError("article deleted")`` | 409 | ``article deleted``                       |
| 其他 ``ArticleLimitError``       | ``exc.status`` | ``exc.message``（契约 §5.3 的上限文案由 ``models`` 直接给出） |
| ``ArticleRequestReplay``         | 409    | ``request_id was already used for a different WebUI mutation`` |

协议级校验（``client_request_id`` / ``article_id`` 的形状）在本层兜底，因为
传输层不会把 ``request_id`` 透传给领域 handler（契约 §1 推论 1）：业务级幂等靠
payload 内显式的 ``client_request_id``。大小上限与字段类型由
``nanobot/writing/models.py`` 单点判定（契约 §5.3），本层不重复实现、也不再做
文案归一或边界拦截——超限的 message 与 status 都在 ``models`` 里定型。
"""

from __future__ import annotations

import re
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import Any, cast

from nanobot.webui.settings_contracts import WebUISettingsError
from nanobot.webui.writing_services import WritingServices
from nanobot.writing import apply as writing_apply
from nanobot.writing.models import ArticleLimitError
from nanobot.writing.repository import (
    ArticleConflict,
    ArticleNotFound,
    ArticleRepository,
    ArticleRequestReplay,
)

# 与传输层 request_id 同正则（契约 §5）。
_CLIENT_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_MAX_ID_CHARS = 128

LIST_DEFAULT_LIMIT = 50


@contextmanager
def _translated() -> Generator[None, None, None]:
    """把仓储异常翻译成契约 §6 的 status + message 原文。"""
    try:
        yield
    except ArticleNotFound as exc:
        raise WebUISettingsError("article not found", status=404) from exc
    except ArticleConflict as exc:
        raise WebUISettingsError(str(exc), status=409) from exc
    except ArticleRequestReplay as exc:
        raise WebUISettingsError(
            "request_id was already used for a different WebUI mutation", status=409,
        ) from exc
    except ArticleLimitError as exc:
        raise WebUISettingsError(exc.message, status=exc.status) from exc


def _check_client_request_id(raw: Any) -> str:
    if not isinstance(raw, str) or not _CLIENT_REQUEST_ID_RE.fullmatch(raw):
        raise WebUISettingsError("client_request_id is required")
    return raw


def _check_article_id(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise WebUISettingsError("article id is required")
    value = raw.strip()
    if len(value) > _MAX_ID_CHARS:
        raise WebUISettingsError("article id is required")
    return value


def _check_change_id(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise WebUISettingsError("change_id is required")
    return raw.strip()


def _check_base_revision(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise WebUISettingsError("base_revision is required")
    return raw


def _coerce_int(raw: Any, field: str) -> int | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise WebUISettingsError(f"{field} must be an integer")
    if isinstance(raw, int):
        return raw
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        raise WebUISettingsError(f"{field} must be an integer")


def _repository(services: WritingServices) -> ArticleRepository:
    return services.repository


# ---- 读取（契约 §4）---------------------------------------------------------


def list_articles(
    services: WritingServices,
    *,
    query: Any,
    status: Any,
    limit: Any,
    offset: Any,
) -> dict[str, Any]:
    """``article.list``：列表投影不含正文（契约 §4 ArticleSummary）。

    入参一律按「未校验的外部输入」处理（query 值来自 HTTP/WS，类型不受信任）：
    ``q``/``status`` 非字符串视作未提供，``limit``/``offset`` 非整数直接 400。
    """
    resolved_limit = _coerce_int(limit, "limit")
    resolved_offset = _coerce_int(offset, "offset")
    with _translated():
        return _repository(services).list_articles(
            query=query if isinstance(query, str) else None,
            status=status if isinstance(status, str) else None,
            limit=resolved_limit if resolved_limit is not None else LIST_DEFAULT_LIMIT,
            offset=resolved_offset if resolved_offset is not None else 0,
        )


def fetch_article(services: WritingServices, article_id: Any) -> dict[str, Any]:
    """``article.get``：返回完整文章快照。"""
    with _translated():
        return _repository(services).get_article(_check_article_id(article_id))


# ---- 写入（契约 §5，仅 WebSocket mutation）---------------------------------


def create_article(
    services: WritingServices,
    *,
    client_request_id: str,
    title: str,
    template_id: str = "",
    writing_type: str = "",
    summary: str = "",
    brief: dict[str, str] | None = None,
    body: str = "",
    outline: list[dict[str, Any]] | None = None,
    materials: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
    status: str = "idea",
) -> dict[str, Any]:
    """``article.create``：服务端生成 id 与时间戳，``revision = 1``。"""
    payload: dict[str, Any] = {
        "title": title,
        "template_id": template_id,
        "writing_type": writing_type,
        "summary": summary,
        "status": status,
        "body": body,
    }
    for key, value in (
        ("brief", brief), ("outline", outline), ("materials", materials), ("tags", tags),
    ):
        if value is not None:
            payload[key] = value
    with _translated():
        return _repository(services).create_article(
            _check_client_request_id(client_request_id), payload,
        )


def save_article(
    services: WritingServices,
    *,
    client_request_id: str,
    article_id: str,
    base_revision: int,
    snapshot: Any,
) -> dict[str, Any]:
    """``article.save``：单事务内 CAS；相同内容不推进 revision（契约 §5.1）。"""
    if not isinstance(snapshot, Mapping):
        raise WebUISettingsError("unsupported article field: snapshot")
    data: Mapping[str, Any] = cast(Mapping[str, Any], snapshot)
    with _translated():
        return _repository(services).save_snapshot(
            _check_article_id(article_id),
            _check_base_revision(base_revision),
            dict(data),
            client_request_id=_check_client_request_id(client_request_id),
        )


def duplicate_article(
    services: WritingServices,
    *,
    client_request_id: str,
    article_id: str,
    base_revision: int,
) -> dict[str, Any]:
    """``article.duplicate``：新 id、``revision = 1``，素材/提纲项 id 全部重生成。"""
    with _translated():
        return _repository(services).duplicate_article(
            _check_article_id(article_id),
            _check_base_revision(base_revision),
            client_request_id=_check_client_request_id(client_request_id),
        )


def delete_article(
    services: WritingServices,
    *,
    client_request_id: str,
    article_id: str,
    base_revision: int,
) -> dict[str, Any]:
    """``article.delete``：同样要求 ``base_revision`` 与服务端一致。"""
    with _translated():
        return _repository(services).delete_article(
            _check_article_id(article_id),
            _check_base_revision(base_revision),
            client_request_id=_check_client_request_id(client_request_id),
        )


def undo_change(
    services: WritingServices,
    *,
    client_request_id: str,
    change_id: str,
    base_revision: int,
) -> dict[str, Any]:
    """``writing.undo_change``：只在当前版本仍是该次写入结果时还原字段。"""
    database = services.database
    if database is None:
        raise WebUISettingsError("writing service is not configured", status=503)
    with _translated():
        return writing_apply.undo_change(
            database,
            change_id=_check_change_id(change_id),
            base_revision=_check_base_revision(base_revision),
            client_request_id=_check_client_request_id(client_request_id),
        )
