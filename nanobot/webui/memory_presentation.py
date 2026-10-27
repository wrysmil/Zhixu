"""Read-only provenance projection and filters for the current memory store.

Project paths are display metadata, never database locations or file-read inputs.
Only persisted snapshots or explicitly linked session metadata establish ownership.
"""

from __future__ import annotations

from typing import Any, cast

from loguru import logger

from nanobot.memory.models import Episode, Memory
from nanobot.memory.provenance import workspace_from_path
from nanobot.webui.memory_services import MemoryServices
from nanobot.webui.settings_contracts import WebUISettingsError

_AI_SOURCES = {
    "extraction", "consolidation", "session_end", "context_compress", "topic_change",
    "daily_consolidation", "deletion", "idle",
}
_AUTOMATIC_REASONS = {
    "session_end", "context_compress", "topic_change", "daily_consolidation", "idle",
}


def _object_dict(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def provenance_for(item: Memory | Episode) -> dict[str, Any]:
    return _object_dict(_object_dict(item.metadata).get("provenance"))


def episode_in_store(item: Episode, services: MemoryServices) -> bool:
    """Legacy episodes belong to their physical store; new rows also record its ID."""
    owner = provenance_for(item).get("store_workspace_id")
    return owner is None or owner == services.workspace_id


class PresentationContext:
    """Request-local resolver, including one metadata lookup per linked session."""

    def __init__(self, services: MemoryServices | None, episodes: list[Episode]) -> None:
        self.services = services
        self.episodes = {
            ep.id: ep for ep in episodes if services is None or episode_in_store(ep, services)
        }
        self.linked_episodes: dict[str, list[Episode]] = {}
        self.session_workspaces: dict[str, list[dict[str, str]]] = {}
        for episode in self.episodes.values():
            for memory_id in episode.linked_memory_ids:
                self.linked_episodes.setdefault(memory_id, []).append(episode)

    def _session_workspaces(self, session_id: str) -> list[dict[str, str]]:
        if session_id not in self.session_workspaces:
            self.session_workspaces[session_id] = []
            if self.services is not None and self.services.session_metadata is not None:
                try:
                    snapshot = self.services.session_metadata(session_id)
                except Exception as exc:  # Metadata failure must not hide existing records.
                    logger.warning("memory session metadata unavailable: {}", type(exc).__name__)
                    snapshot = None
                metadata = _object_dict(_object_dict(snapshot).get("metadata"))
                scope = _object_dict(metadata.get("workspace_scope"))
                path = scope.get("project_path")
                if isinstance(path, str) and path.strip():
                    try:
                        self.session_workspaces[session_id] = [workspace_from_path(path)]
                    except ValueError:
                        pass
        return self.session_workspaces[session_id]

    def workspaces(self, item: Memory | Episode) -> list[dict[str, str]]:
        provenance = provenance_for(item)
        snapshots = provenance.get("workspaces")
        if isinstance(snapshots, list):
            result: dict[str, dict[str, str]] = {}
            for snapshot in cast(list[object], snapshots):
                path = _object_dict(snapshot).get("path")
                if isinstance(path, str) and path.strip():
                    try:
                        workspace = workspace_from_path(path)
                    except ValueError:
                        continue
                    result[workspace["id"]] = workspace
            return list(result.values())
        session_id = provenance.get("session_id")
        if isinstance(session_id, str) and session_id:
            return self._session_workspaces(session_id)
        if isinstance(item, Episode):
            return self._session_workspaces(item.session_id)
        episodes = self.linked_episodes.get(item.id, [])
        if item.source_episode_id in self.episodes:
            episodes = [self.episodes[item.source_episode_id], *episodes]
        result = {}
        for episode in episodes:
            for workspace in self.workspaces(episode):
                result[workspace["id"]] = workspace
        return list(result.values())

    def fields(self, item: Memory | Episode) -> dict[str, Any]:
        provenance = provenance_for(item)
        source = item.source if isinstance(item, Memory) else item.source.value
        method = provenance.get("generation_method")
        if not isinstance(method, str) or method not in {"ai", "manual", "unknown"}:
            method = "manual" if source == "manual" else "ai" if source in _AI_SOURCES else "unknown"
        trigger = provenance.get("trigger_mode")
        if not isinstance(trigger, str) or trigger not in {"automatic", "manual", "unknown"}:
            if source == "manual":
                trigger = "manual"
            elif isinstance(item, Episode) and source in _AUTOMATIC_REASONS:
                trigger = "automatic"
            else:
                trigger = "unknown"
        origin = "manual" if trigger == "manual" or method == "manual" else method
        reason = provenance.get("trigger_reason")
        return {
            "origin_kind": origin,
            "generation_method": method,
            "trigger_mode": trigger,
            "trigger_reason": reason if isinstance(reason, str) else source,
            "workspaces": self.workspaces(item),
        }


def workspace_options(
    services: MemoryServices, items: list[dict[str, Any]],
) -> list[dict[str, str]]:
    default = workspace_from_path(str(services.database.workspace))
    options = {default["id"]: default}
    for item in items:
        for workspace in item["workspaces"]:
            options[workspace["id"]] = workspace
    return sorted(options.values(), key=lambda workspace: (workspace["name"].casefold(), workspace["path"]))


def filter_result(
    items: list[dict[str, Any]], *, options: list[dict[str, str]],
    origin: str | None, workspace: str | None, limit: int | None,
) -> dict[str, Any]:
    if origin not in {None, "", "ai", "manual", "unknown"}:
        raise WebUISettingsError("invalid origin")
    if limit is not None and limit < 1:
        raise WebUISettingsError("limit must be >= 1")
    if workspace and workspace != "unassigned" and workspace not in {option["id"] for option in options}:
        raise WebUISettingsError("invalid workspace")
    if workspace:
        items = [
            item for item in items
            if (not item["workspaces"] if workspace == "unassigned" else
                any(option["id"] == workspace for option in item["workspaces"]))
        ]
    counts = {"all": len(items), "ai": 0, "manual": 0, "unknown": 0}
    for item in items:
        counts[item["origin_kind"]] += 1
    selected = [item for item in items if not origin or item["origin_kind"] == origin]
    return {
        "items": selected if limit is None else selected[:limit],
        "total": len(selected), "source_counts": counts,
        "workspace_options": options, "scope": "current_store",
    }
