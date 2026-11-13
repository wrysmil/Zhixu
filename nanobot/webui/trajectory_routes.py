"""轨迹只读 WebUI 适配器。

只提供认证 HTTP GET：会话概要、记录页、增量事件页、span 详情与正文分片。
所有 session 身份由服务器解析；没有 workspace 归属证据时不暴露存在性。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from nanobot.trajectory.service import TrajectoryService
from nanobot.trajectory.store import (
    TrajectoryCursorExpired,
    TrajectoryInvalidCursor,
)
from nanobot.webui.settings_contracts import (
    SettingsRequest,
    SettingsRouteResult,
    WebUISettingsError,
    query_first,
)

TRAJECTORY_SESSIONS_PATH = "/api/trajectory/sessions"
TRAJECTORY_SESSION = "trajectory-session"
TRAJECTORY_RECORDS = "trajectory-records"
TRAJECTORY_EVENTS = "trajectory-events"
TRAJECTORY_SPAN = "trajectory-span"
TRAJECTORY_BODY = "trajectory-body"
TRAJECTORY_ACTION_NAMES = frozenset({
    TRAJECTORY_SESSION,
    TRAJECTORY_RECORDS,
    TRAJECTORY_EVENTS,
    TRAJECTORY_SPAN,
    TRAJECTORY_BODY,
})

_DEFAULT_LIMIT = 200
_MAX_LIMIT = 500
_MAX_BODY_BYTES = 256 * 1024
_SESSION_MAX = 256


def match_route(path: str) -> tuple[str, dict[str, str]] | None:
    if not path.startswith(f"{TRAJECTORY_SESSIONS_PATH}/"):
        return None
    parts = path.removeprefix(f"{TRAJECTORY_SESSIONS_PATH}/").split("/")
    if not parts or not parts[0]:
        return None
    session_id = unquote(parts[0])
    if len(parts) == 1:
        return TRAJECTORY_SESSION, {"sessionId": session_id}
    if len(parts) == 2 and parts[1] == "records":
        return TRAJECTORY_RECORDS, {"sessionId": session_id}
    if len(parts) == 2 and parts[1] == "events":
        return TRAJECTORY_EVENTS, {"sessionId": session_id}
    if len(parts) == 3 and parts[1] == "spans" and parts[2]:
        return TRAJECTORY_SPAN, {"sessionId": session_id, "spanId": unquote(parts[2])}
    if len(parts) == 3 and parts[1] == "bodies" and parts[2]:
        return TRAJECTORY_BODY, {"sessionId": session_id, "bodyId": unquote(parts[2])}
    return None


def _not_found() -> WebUISettingsError:
    return WebUISettingsError(
        "TRAJECTORY_NOT_FOUND: trajectory is not available for this session",
        status=404,
    )


def _chat_id(session_id: str) -> str:
    if session_id.startswith("websocket:"):
        return session_id.split(":", 1)[1]
    return session_id


def _ensure_scope(session_id: str, caller_chats: tuple[str, ...]) -> None:
    if not caller_chats:
        return
    chat_id = _chat_id(session_id)
    if chat_id in caller_chats or session_id in caller_chats:
        return
    raise _not_found()


def _valid_session(raw: str | None) -> str:
    if raw is None:
        raise TrajectoryInvalidCursor("missing session id")
    value = raw.strip()
    if not value or len(value) > _SESSION_MAX or "\x00" in value:
        raise TrajectoryInvalidCursor("invalid session id")
    return value


@dataclass(frozen=True)
class TrajectoryOperations:
    summary: Callable[..., Any]
    records_page: Callable[..., Any]
    events_page: Callable[..., Any]
    span_detail: Callable[..., Any]
    body_slice: Callable[..., Any]


def build_trajectory_operations(
    service: TrajectoryService,
    workspace_for_session: Callable[[str], str | None],
) -> TrajectoryOperations:
    def _workspace(session_id: str) -> str:
        workspace = workspace_for_session(session_id)
        if not workspace:
            raise _not_found()
        return workspace

    def summary(*, session_id: str) -> dict[str, Any]:
        payload = service.summary(_workspace(session_id), session_id)
        if payload is None:
            raise _not_found()
        return payload

    def records_page(
        *, session_id: str, before_cursor: str | None = None, limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        page = service.records_page(
            _workspace(session_id), session_id,
            before_cursor=before_cursor, limit=limit,
        )
        if page is None:
            raise _not_found()
        return page.to_dict()

    def events_page(
        *, session_id: str, after_seq: int | None = None,
        before_seq: int | None = None, limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        page = service.events_page(
            _workspace(session_id), session_id,
            after_seq=after_seq, before_seq=before_seq, limit=limit,
        )
        if page is None:
            raise _not_found()
        return page.to_dict()

    def span_detail(*, session_id: str, span_id: str) -> dict[str, Any]:
        payload = service.span_detail(_workspace(session_id), session_id, span_id)
        if payload is None:
            raise _not_found()
        return payload

    def body_slice(
        *, session_id: str, body_id: str, offset_bytes: int = 0,
        limit_bytes: int = 65536,
    ) -> dict[str, Any]:
        payload = service.body_slice(
            _workspace(session_id), session_id, body_id,
            offset_bytes=offset_bytes, limit_bytes=limit_bytes,
        )
        if payload is None:
            raise WebUISettingsError(
                "TRAJECTORY_BODY_UNAVAILABLE: body content is unavailable",
                status=410,
            )
        return payload

    return TrajectoryOperations(
        summary=summary,
        records_page=records_page,
        events_page=events_page,
        span_detail=span_detail,
        body_slice=body_slice,
    )


def _query(request: SettingsRequest, key: str) -> str | None:
    return query_first(request.query, key)


def _int_query(
    request: SettingsRequest, key: str, *, default: int,
    minimum: int = 0, maximum: int | None = None,
) -> int:
    raw = _query(request, key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise WebUISettingsError(f"TRAJECTORY_INVALID_CURSOR: invalid {key}", status=400) from exc
    if value < minimum or (maximum is not None and value > maximum):
        raise WebUISettingsError(f"TRAJECTORY_INVALID_CURSOR: invalid {key}", status=400)
    return value


class TrajectorySettingsHandler:
    def __init__(self, operations: TrajectoryOperations) -> None:
        self._ops = operations

    def handle(
        self,
        action: str,
        request: SettingsRequest,
        *,
        params: dict[str, str] | None = None,
    ) -> SettingsRouteResult:
        if action not in TRAJECTORY_ACTION_NAMES:
            return SettingsRouteResult.failure(404, f"unknown trajectory action: {action}")
        params = params or {}
        try:
            session_id = _valid_session(params.get("sessionId"))
            _ensure_scope(session_id, request.caller_chats)
            if action == TRAJECTORY_SESSION:
                payload = self._ops.summary(session_id=session_id)
            elif action == TRAJECTORY_RECORDS:
                payload = self._ops.records_page(
                    session_id=session_id,
                    before_cursor=_query(request, "beforeCursor"),
                    limit=_int_query(
                        request, "limit", default=_DEFAULT_LIMIT, minimum=1, maximum=_MAX_LIMIT,
                    ),
                )
            elif action == TRAJECTORY_EVENTS:
                after_raw = _query(request, "afterSeq")
                before_raw = _query(request, "beforeSeq")
                payload = self._ops.events_page(
                    session_id=session_id,
                    after_seq=(
                        _int_query(request, "afterSeq", default=0)
                        if after_raw is not None else None
                    ),
                    before_seq=(
                        _int_query(request, "beforeSeq", default=0)
                        if before_raw is not None else None
                    ),
                    limit=_int_query(
                        request, "limit", default=_DEFAULT_LIMIT, minimum=1, maximum=_MAX_LIMIT,
                    ),
                )
            elif action == TRAJECTORY_SPAN:
                payload = self._ops.span_detail(
                    session_id=session_id, span_id=params.get("spanId", ""),
                )
            else:
                payload = self._ops.body_slice(
                    session_id=session_id,
                    body_id=params.get("bodyId", ""),
                    offset_bytes=_int_query(request, "offsetBytes", default=0, minimum=0),
                    limit_bytes=_int_query(
                        request, "limitBytes", default=65536, minimum=1,
                        maximum=_MAX_BODY_BYTES,
                    ),
                )
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        except TrajectoryCursorExpired as exc:
            return SettingsRouteResult.failure(410, f"TRAJECTORY_CURSOR_EXPIRED: {exc}")
        except TrajectoryInvalidCursor as exc:
            return SettingsRouteResult.failure(400, f"TRAJECTORY_INVALID_CURSOR: {exc}")
        except PermissionError:
            return SettingsRouteResult.failure(
                404, "TRAJECTORY_NOT_FOUND: trajectory is not available for this session",
            )
        except Exception:
            return SettingsRouteResult.failure(
                503, "TRAJECTORY_UNAVAILABLE: trajectory storage is unavailable",
            )
        return SettingsRouteResult.success(payload)


__all__ = [
    "TRAJECTORY_ACTION_NAMES",
    "TRAJECTORY_SESSIONS_PATH",
    "TrajectoryOperations",
    "TrajectorySettingsHandler",
    "build_trajectory_operations",
    "match_route",
]
