"""WebUI settings domain handler for the writing (article store) domain.

Mirrors ``memory_routes.py`` / ``identity_routes.py`` / ``agents_routes.py``:
a transport-neutral handler whose ``handle(action, request)`` dispatches
against injected operations and returns a ``SettingsRouteResult``. The router
(``settings_routes.py``) owns path → action mapping, WebSocket vs HTTP gating
and the transport layer.

Action names are **dotted** in both namespaces (contract §4): the WebSocket
transport enforces ``[a-z][a-z0-9_.]{0,127}`` on ``webui_request.action`` and
the domain action names registered in ``_SYSTEM_ROUTES`` use the very same
strings, so ``article.create`` is simultaneously the WS action name and the
domain action name — no hyphen/dotted double mapping to get wrong.

Read actions (``article.list`` / ``article.get``) are allowed over plain HTTP
GET (subject to ``check_api_token``). The four write actions only arrive via an
authenticated WebSocket ``requestMutation`` — enforced upstream by the
``_SETTINGS_MUTATION_PATHS`` allowlist in ``settings_routes.py``. This module
neither knows about the WebSocket transport nor about persistence; it only
calls the ``WritingSettingsOperations`` protocol, and the contract §6 error
messages are produced one layer down in ``writing_api.py``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable, cast

from nanobot.webui.settings_contracts import (
    SettingsRequest,
    SettingsRouteResult,
    WebUISettingsError,
    query_first,
)

# 与传输层 request_id 同正则（契约 §5）。业务级幂等需要 payload 内显式携带
# ``client_request_id``：传输层的 request_id 不会透传到领域 handler（契约 §1 推论 1）。
_CLIENT_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")

# Canonical set of actions this domain understands; the settings router uses
# it to route ``("system", action)`` into this handler.
WRITING_ACTION_NAMES = frozenset({
    "article.list", "article.get", "article.create",
    "article.save", "article.duplicate", "article.delete",
})

# 写入动作都要求 client_request_id；缺失即 400，让 handler 而不是 repository
# 负责协议级校验（与 memory_routes 里 "content is required" 同层）。
WRITING_MUTATION_ACTION_NAMES = frozenset({
    "article.create", "article.save", "article.duplicate", "article.delete",
})

# 快照允许透传给仓储的字段（契约 §5）；其余键忽略，未知类型由 models 层报
# ``unsupported article field: <name>``。
_SNAPSHOT_FIELDS = (
    "title", "writing_type", "template_id", "status", "summary",
    "brief", "body", "outline", "materials", "tags",
)


@dataclass(frozen=True)
class WritingSettingsOperations:
    """Transport-neutral entry points injected into the handler.

    Kept as a plain protocol so tests can substitute in-memory doubles and the
    gateway can wire the real ``writing_api`` actions. Every callable takes the
    workspace-scoped ``WritingServices`` as its first argument (pre-bound with
    ``functools.partial``).
    """

    list_articles: Callable[..., dict[str, Any]]
    fetch_article: Callable[[Any], dict[str, Any]]
    create_article: Callable[..., dict[str, Any]]
    save_article: Callable[..., dict[str, Any]]
    duplicate_article: Callable[..., dict[str, Any]]
    delete_article: Callable[..., dict[str, Any]]


class WritingSettingsHandler:
    """Route a single writing-domain action against injected operations."""

    def __init__(self, operations: WritingSettingsOperations) -> None:
        self._ops = operations

    def handle(
        self,
        action: str,
        request: SettingsRequest,
    ) -> SettingsRouteResult:
        if action not in WRITING_ACTION_NAMES:
            return SettingsRouteResult.failure(404, f"unknown writing action: {action}")
        try:
            payload = dispatch(self._ops, action, request)
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        return SettingsRouteResult.success(payload)


# ---- per-action dispatch ---------------------------------------------------


def dispatch(
    operations: WritingSettingsOperations,
    action: str,
    request: SettingsRequest,
) -> dict[str, Any]:
    query = request.query
    payload = request.payload or {}

    if action == "article.list":
        return operations.list_articles(
            query=query_first(query, "q"),
            status=query_first(query, "status"),
            limit=query_first(query, "limit"),
            offset=query_first(query, "offset"),
        )

    if action == "article.get":
        return operations.fetch_article(_article_id(query, payload))

    if action == "article.create":
        return operations.create_article(
            client_request_id=_client_request_id(payload),
            title=_title(payload),
            template_id=_text_field(payload, "template_id"),
            writing_type=_text_field(payload, "writing_type"),
            summary=_text_field(payload, "summary"),
            brief=_optional_mapping_field(payload, "brief"),
            body=_text_field(payload, "body"),
            outline=_optional_sequence_field(payload, "outline"),
            materials=_optional_sequence_field(payload, "materials"),
            tags=_optional_sequence_field(payload, "tags"),
            status=payload.get("status", "idea"),
        )

    if action == "article.save":
        return operations.save_article(
            client_request_id=_client_request_id(payload),
            article_id=_mutation_article_id(payload),
            base_revision=_base_revision(payload),
            snapshot=_snapshot(payload),
        )

    if action == "article.duplicate":
        return operations.duplicate_article(
            client_request_id=_client_request_id(payload),
            article_id=_mutation_article_id(payload),
            base_revision=_base_revision(payload),
        )

    if action == "article.delete":
        return operations.delete_article(
            client_request_id=_client_request_id(payload),
            article_id=_mutation_article_id(payload),
            base_revision=_base_revision(payload),
        )

    # Unreachable for known actions; kept as a guard against future additions.
    raise WebUISettingsError(f"unsupported writing action: {action}")


def _client_request_id(payload: dict[str, Any]) -> str:
    raw = payload.get("client_request_id")
    if not isinstance(raw, str) or not _CLIENT_REQUEST_ID_RE.fullmatch(raw):
        raise WebUISettingsError("client_request_id is required")
    return raw


def _base_revision(payload: dict[str, Any]) -> int:
    raw = payload.get("base_revision")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise WebUISettingsError("base_revision is required")
    return raw


def _article_id(query: dict[str, list[str]], payload: dict[str, Any]) -> str:
    raw = query_first(query, "id") or payload.get("id") or payload.get("article_id")
    return _checked_article_id(raw)


def _mutation_article_id(payload: dict[str, Any]) -> str:
    return _checked_article_id(payload.get("article_id"))


def _checked_article_id(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise WebUISettingsError("article id is required")
    return raw.strip()


def _title(payload: dict[str, Any]) -> str:
    if "title" not in payload:
        raise WebUISettingsError("article title is required")
    raw = payload["title"]
    if not isinstance(raw, str):
        raise WebUISettingsError("unsupported article field: title")
    if not raw.strip():
        raise WebUISettingsError("article title is required")
    return raw


def _text_field(payload: dict[str, Any], key: str) -> Any:
    """缺省即空字符串；类型原样透传，由 models 层判定 ``unsupported article field``。"""
    return payload.get(key, "")


def _optional_mapping_field(
    payload: dict[str, Any], key: str
) -> dict[str, Any] | None:
    raw = payload.get(key)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise WebUISettingsError(f"unsupported article field: {key}")
    data: Mapping[str, Any] = cast(Mapping[str, Any], raw)
    return dict(data)


def _optional_sequence_field(
    payload: dict[str, Any], key: str
) -> list[Any] | None:
    raw = payload.get(key)
    if raw is None:
        return None
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise WebUISettingsError(f"unsupported article field: {key}")
    entries: Sequence[Any] = cast(Sequence[Any], raw)
    return list(entries)


def _snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """严格策略：前端必须发完整快照字段，缺失即 400，不做服务端补齐。"""
    raw = payload.get("snapshot")
    if raw is None:
        raise WebUISettingsError("article snapshot is required")
    if not isinstance(raw, Mapping):
        raise WebUISettingsError("unsupported article field: snapshot")
    data: Mapping[str, Any] = cast(Mapping[str, Any], raw)
    if "title" not in data:
        raise WebUISettingsError("article title is required")
    return {key: data[key] for key in _SNAPSHOT_FIELDS if key in data}
