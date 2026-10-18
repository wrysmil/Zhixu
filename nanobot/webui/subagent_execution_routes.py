"""普通子 Agent 执行账本的只读 WebUI 适配器。

本模块只提供认证 HTTP GET 需要的能力：列表、详情、事件分页。写生命周期全部由
``nanobot.subagent_executions`` 与运行时 observation 负责，这里不新增 mutation。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from nanobot.subagent_executions import ExecutionRepository, WriterBusy
from nanobot.webui.settings_contracts import (
    SettingsRequest,
    SettingsRouteResult,
    WebUISettingsError,
    query_first,
    query_first_alias,
)

SUBAGENT_EXECUTIONS_PATH = "/api/subagent-executions"
SUBAGENT_EXECUTION_LIST = "subagent-executions-list"
SUBAGENT_EXECUTION_DETAIL = "subagent-executions-detail"
SUBAGENT_EXECUTION_EVENTS = "subagent-executions-events"
SUBAGENT_EXECUTION_ACTION_NAMES = frozenset({
    SUBAGENT_EXECUTION_LIST,
    SUBAGENT_EXECUTION_DETAIL,
    SUBAGENT_EXECUTION_EVENTS,
})

_DEFAULT_LIST_LIMIT = 20
_MAX_LIST_LIMIT = 100
_DEFAULT_EVENT_LIMIT = 100
_MAX_EVENT_LIMIT = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def match_route(path: str) -> tuple[str, str | None] | None:
    """Match the frozen execution routes, including the dynamic invocation id."""
    if path == SUBAGENT_EXECUTIONS_PATH:
        return SUBAGENT_EXECUTION_LIST, None
    prefix = f"{SUBAGENT_EXECUTIONS_PATH}/"
    if not path.startswith(prefix):
        return None
    parts = path.removeprefix(prefix).split("/")
    if len(parts) == 1 and parts[0]:
        return SUBAGENT_EXECUTION_DETAIL, parts[0]
    if len(parts) == 2 and parts[0] and parts[1] == "events":
        return SUBAGENT_EXECUTION_EVENTS, parts[0]
    return None


def _empty_page() -> dict[str, Any]:
    return {
        "executions": [],
        "nextCursor": None,
        "serverNow": _now(),
    }


def _fixed_error(message: str, *, status: int) -> WebUISettingsError:
    return WebUISettingsError(message, status=status)


def _storage_error(exc: Exception) -> WebUISettingsError:
    if isinstance(exc, WriterBusy):
        return _fixed_error(
            "EXECUTION_WRITER_BUSY: workspace writer is already held",
            status=503,
        )
    return _fixed_error(
        "EXECUTION_STORAGE_UNAVAILABLE: execution query storage is unavailable",
        status=503,
    )


def _not_found() -> WebUISettingsError:
    return _fixed_error("EXECUTION_NOT_FOUND: execution does not exist", status=404)


def _chat_id_for_invocation(repo: ExecutionRepository, invocation_id: str) -> str | None:
    """Resolve the workspace-scoped invocation to its chat before repository filtering.

    ``ExecutionRepository.detail`` intentionally takes ``chat_id`` to enforce chat
    isolation.  The HTTP contract addresses detail/events by globally unique
    invocation id, so this adapter performs the missing read-only key lookup; all
    subsequent reads still go through the repository's chat-scoped methods.
    """
    with repo._lock:  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        row = repo._conn.execute(  # pyright: ignore[reportPrivateUsage]
            "SELECT chat_id FROM executions WHERE invocation_id=?",
            (invocation_id,),
        ).fetchone()
    return str(row[0]) if row is not None else None


def _ensure_scope(chat_id: str, caller_chats: tuple[str, ...]) -> None:
    if caller_chats and chat_id not in caller_chats:
        raise _fixed_error(
            "EXECUTION_NOT_FOUND: execution is not visible to this session",
            status=403,
        )


def build_subagent_execution_operations(
    repository: ExecutionRepository | None,
) -> "SubagentExecutionSettingsOperations":
    """Bind one workspace repository to the three read actions.

    A missing repository is a valid observed state (for example a gateway built
    without a WebSocket host).  List then returns an empty page without creating
    a database; detail/events return the contract's not-found error.
    """

    def list_executions(
        *,
        chat_id: str,
        limit: int = _DEFAULT_LIST_LIMIT,
        cursor: str | None = None,
        parent_turn_id: str | None = None,
    ) -> dict[str, Any]:
        if repository is None:
            return _empty_page()
        try:
            page = repository.list(
                chat_id,
                limit=limit,
                cursor=cursor,
                parent_turn_id=parent_turn_id,
            ).to_dict()
            return {
                "executions": page.get("executions", []),
                "nextCursor": page.get("nextCursor"),
                "serverNow": page.get("serverNow") or _now(),
            }
        except ValueError as exc:
            raise WebUISettingsError("invalid or expired cursor", status=400) from exc
        except Exception as exc:
            raise _storage_error(exc) from exc

    def get_execution(
        *,
        invocation_id: str,
        chat_id: str | None = None,
        caller_chats: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if repository is None:
            raise _not_found()
        try:
            resolved_chat_id = chat_id or _chat_id_for_invocation(repository, invocation_id)
            if resolved_chat_id is None:
                raise _not_found()
            _ensure_scope(resolved_chat_id, caller_chats)
            snapshot = repository.detail(resolved_chat_id, invocation_id)
            if snapshot is None:
                raise _not_found()
            return {"execution": snapshot.to_dict(), "serverNow": _now()}
        except WebUISettingsError:
            raise
        except Exception as exc:
            raise _storage_error(exc) from exc

    def list_events(
        *,
        invocation_id: str,
        after_seq: int = 0,
        through_seq: int | None = None,
        limit: int = _DEFAULT_EVENT_LIMIT,
        chat_id: str | None = None,
        caller_chats: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if repository is None:
            raise _not_found()
        try:
            resolved_chat_id = chat_id or _chat_id_for_invocation(repository, invocation_id)
            if resolved_chat_id is None:
                raise _not_found()
            _ensure_scope(resolved_chat_id, caller_chats)
            page = repository.events(
                resolved_chat_id,
                invocation_id,
                after_seq=after_seq,
                through_seq=through_seq,
                limit=limit,
            )
            if not page.items and repository.detail(resolved_chat_id, invocation_id) is None:
                raise _not_found()
            return page.to_dict()
        except WebUISettingsError:
            raise
        except Exception as exc:
            raise _storage_error(exc) from exc

    return SubagentExecutionSettingsOperations(
        list_executions=list_executions,
        get_execution=get_execution,
        list_events=list_events,
    )


@dataclass(frozen=True)
class SubagentExecutionSettingsOperations:
    list_executions: Callable[..., dict[str, Any]]
    get_execution: Callable[..., dict[str, Any]]
    list_events: Callable[..., dict[str, Any]]


def _query(request: SettingsRequest, key: str) -> str | None:
    return query_first(request.query, key)


def _int_query(
    request: SettingsRequest,
    key: str,
    *,
    default: int,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    raw = _query(request, key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise WebUISettingsError(f"invalid {key}", status=400) from exc
    if value < minimum or (maximum is not None and value > maximum):
        raise WebUISettingsError(f"invalid {key}", status=400)
    return value


def _invocation_id(raw: str | None) -> str:
    if raw is None or not raw.strip():
        raise WebUISettingsError("invalid invocationId", status=400)
    try:
        parsed = UUID(raw)
    except (ValueError, AttributeError) as exc:
        raise WebUISettingsError("invalid invocationId", status=400) from exc
    return str(parsed)


def _chat_id(request: SettingsRequest, *, required: bool) -> str | None:
    raw = query_first_alias(request.query, "chatId", "chat_id")
    if raw is None or not raw.strip():
        if required:
            raise WebUISettingsError("chatId is required", status=400)
        return None
    value = raw.strip()
    if len(value) > 256 or "\x00" in value:
        raise WebUISettingsError("invalid chatId", status=400)
    return value


class SubagentExecutionSettingsHandler:
    """Translate one execution read action into its transport-neutral payload."""

    def __init__(self, operations: SubagentExecutionSettingsOperations) -> None:
        self._ops = operations

    def handle(
        self,
        action: str,
        request: SettingsRequest,
        *,
        invocation_id: str | None = None,
    ) -> SettingsRouteResult:
        if action not in SUBAGENT_EXECUTION_ACTION_NAMES:
            return SettingsRouteResult.failure(404, f"unknown execution action: {action}")
        try:
            if action == SUBAGENT_EXECUTION_LIST:
                chat_id = _chat_id(request, required=True)
                assert chat_id is not None
                payload = self._ops.list_executions(
                    chat_id=chat_id,
                    limit=_int_query(
                        request,
                        "limit",
                        default=_DEFAULT_LIST_LIMIT,
                        minimum=1,
                        maximum=_MAX_LIST_LIMIT,
                    ),
                    cursor=_query(request, "cursor"),
                    parent_turn_id=query_first_alias(
                        request.query,
                        "parentTurnId",
                        "parent_turn_id",
                    ),
                )
            elif action == SUBAGENT_EXECUTION_DETAIL:
                payload = self._ops.get_execution(
                    invocation_id=_invocation_id(invocation_id),
                    chat_id=_chat_id(request, required=False),
                    caller_chats=request.caller_chats,
                )
            else:
                payload = self._ops.list_events(
                    invocation_id=_invocation_id(invocation_id),
                    after_seq=_int_query(request, "afterSeq", default=0),
                    through_seq=(
                        _int_query(request, "throughSeq", default=0)
                        if _query(request, "throughSeq") is not None
                        else None
                    ),
                    limit=_int_query(
                        request,
                        "limit",
                        default=_DEFAULT_EVENT_LIMIT,
                        minimum=1,
                        maximum=_MAX_EVENT_LIMIT,
                    ),
                    chat_id=_chat_id(request, required=False),
                    caller_chats=request.caller_chats,
                )
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        except WriterBusy:
            return SettingsRouteResult.failure(
                503,
                "EXECUTION_WRITER_BUSY: workspace writer is already held",
            )
        except Exception:
            return SettingsRouteResult.failure(
                503,
                "EXECUTION_STORAGE_UNAVAILABLE: execution query storage is unavailable",
            )
        return SettingsRouteResult.success(payload)
