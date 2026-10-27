"""WebUI settings domain handler for memory management.

Mirrors ``settings_models.py`` / ``settings_system.py``: a transport-neutral
handler whose ``handle(action, request)`` dispatches against injected
operations and returns a ``SettingsRouteResult``. The router
(``settings_routes.py``) owns path → action mapping, WebSocket vs HTTP gating
and the transport layer.

Read actions are allowed over plain HTTP GET (subject to ``check_api_token``).
Write actions only via an authenticated WebSocket ``requestMutation`` —
enforced upstream by the ``_SETTINGS_MUTATION_PATHS`` allowlist in
``settings_routes.py``. This module neither knows about the WebSocket transport
nor about the concrete persistence implementation; it only calls the
``MemorySettingsOperations`` protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from nanobot.webui.settings_contracts import (
    SettingsRequest,
    SettingsRouteResult,
    WebUISettingsError,
    query_first,
)


@dataclass(frozen=True)
class MemorySettingsOperations:
    """Transport-neutral entry points injected into the handler.

    Kept as a plain protocol so tests can substitute in-memory doubles and the
    gateway can wire the real ``memory_api`` actions. Every callable takes the
    workspace-scoped ``MemoryServices`` as its first argument.
    """

    list_memories: Callable[..., dict[str, Any]]
    search_memories: Callable[..., dict[str, Any]]
    fetch_memory: Callable[[str], dict[str, Any]]
    list_episodes: Callable[..., dict[str, Any]]
    fetch_episode: Callable[[str], dict[str, Any]]
    fetch_scratchpad: Callable[..., dict[str, Any]]
    fetch_stats: Callable[[], dict[str, Any]]

    create_memory: Callable[..., dict[str, Any]]
    update_memory: Callable[..., dict[str, Any]]
    delete_memory: Callable[[str], dict[str, Any]]
    update_episode: Callable[..., dict[str, Any]]
    delete_episode: Callable[[str], dict[str, Any]]
    save_scratchpad: Callable[..., dict[str, Any]]
    reindex_vector: Callable[[], dict[str, Any]]
    sync_vector: Callable[[], dict[str, Any]]
    refresh_memory_md: Callable[..., dict[str, Any]]  # 🆕 WU-4
    get_memory_md_content: Callable[..., dict[str, Any]]  # 🆕 WU-6: 读取 MEMORY.md 内容


# Canonical set of actions this domain understands; the settings router uses
# it to route ``("system", action)`` into this handler.
MEMORY_ACTION_NAMES = frozenset({
    "memory-list", "memory-search", "memory-get", "memory-stats",
    "memory-create", "memory-update", "memory-delete",
    "episode-list", "episode-get", "episode-update", "episode-delete",
    "scratchpad-get", "scratchpad-save",
    "vector-reindex", "vector-sync",
    "memory-refresh-md",  # 🆕 WU-4
    "memory-get-md-content",  # 🆕 WU-6: 读取 MEMORY.md 内容
})


class MemorySettingsHandler:
    """Route a single memory-domain action against injected operations."""

    def __init__(self, operations: MemorySettingsOperations) -> None:
        self._ops = operations

    def handle(
        self,
        action: str,
        request: SettingsRequest,
    ) -> SettingsRouteResult:
        if action not in MEMORY_ACTION_NAMES:
            return SettingsRouteResult.failure(404, f"unknown memory action: {action}")
        try:
            payload = dispatch(self._ops, action, request)
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        return SettingsRouteResult.success(payload)


# ---- per-action dispatch ---------------------------------------------------


def dispatch(
    operations: MemorySettingsOperations,
    action: str,
    request: SettingsRequest,
) -> dict[str, Any]:
    query = request.query
    payload = request.payload or {}

    if action == "memory-list":
        return operations.list_memories(
            type=query_first(query, "type"),
            order=query_first(query, "order") or "created",
            limit=_maybe_int(query_first(query, "limit")),
            priority=query_first(query, "priority"),
            origin=query_first(query, "origin"),
            workspace=query_first(query, "workspace"),
            q=query_first(query, "q"),
        )

    if action == "memory-search":
        text = (query_first(query, "q") or "").strip()
        if not text:
            raise WebUISettingsError("q must be a non-empty string")
        return operations.search_memories(
            text,
            type=query_first(query, "type"),
            limit=_maybe_int(query_first(query, "limit")),
            priority=query_first(query, "priority"),
            origin=query_first(query, "origin"),
            workspace=query_first(query, "workspace"),
            order=query_first(query, "order") or "created",
        )

    if action == "memory-get":
        return operations.fetch_memory(_id(query, payload))

    if action == "memory-stats":
        return operations.fetch_stats()

    if action == "vector-reindex":
        return operations.reindex_vector()

    if action == "vector-sync":
        return operations.sync_vector()

    if action == "episode-list":
        return operations.list_episodes(
            session_id=query_first(query, "session_id"),
            limit=_maybe_int(query_first(query, "limit")),
            origin=query_first(query, "origin"),
            workspace=query_first(query, "workspace"),
            q=query_first(query, "q"),
            order=query_first(query, "order") or "created",
        )

    if action == "episode-get":
        return operations.fetch_episode(_id(query, payload))

    if action == "scratchpad-get":
        return operations.fetch_scratchpad(user_id=payload.get("user_id") or "default")

    if action == "memory-create":
        if "content" not in payload:
            raise WebUISettingsError("content is required")
        return operations.create_memory(
            content=payload["content"],
            type=payload.get("type", "fact"),
            priority=payload.get("priority", "long_term"),
            importance_score=payload.get("importance_score", 0.5),
            tags=payload.get("tags") or [],
            subject=payload.get("subject", ""),
            predicate=payload.get("predicate", ""),
            metadata=payload.get("metadata"),
            workspace=payload.get("workspace"),
        )

    if action == "memory-update":
        kwargs = {
            key: payload[key]
            for key in (
                "content", "type", "priority", "importance_score",
                "tags", "subject", "predicate", "metadata",
            )
            if key in payload
        }
        return operations.update_memory(_id(query, payload), **kwargs)

    if action == "memory-delete":
        return operations.delete_memory(_id(query, payload))

    if action == "episode-update":
        kwargs = {
            key: payload[key]
            for key in ("summary", "goal", "tags", "importance_score")
            if key in payload
        }
        return operations.update_episode(_id(query, payload), **kwargs)

    if action == "episode-delete":
        return operations.delete_episode(_id(query, payload))

    if action == "scratchpad-save":
        return operations.save_scratchpad(
            user_id=payload.get("user_id") or "default",
            content=payload.get("content", ""),
            active_projects=payload.get("active_projects") or [],
            current_focus=payload.get("current_focus", ""),
            open_questions=payload.get("open_questions") or [],
            next_steps=payload.get("next_steps") or [],
        )

    if action == "memory-refresh-md":
        return operations.refresh_memory_md()

    if action == "memory-get-md-content":  # 🆕 WU-6
        return operations.get_memory_md_content()

    # Unreachable for known actions; kept as a guard against future additions.
    raise WebUISettingsError(f"unsupported memory action: {action}")


def _id(query: dict[str, list[str]], payload: dict[str, Any]) -> str:
    raw = query_first(query, "id") or payload.get("id")
    if not isinstance(raw, str) or not raw.strip():
        raise WebUISettingsError("id is required")
    return raw.strip()


def _maybe_int(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise WebUISettingsError("limit must be an integer")
