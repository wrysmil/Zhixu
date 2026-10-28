"""Read-only provenance projection and filters for the current memory store.

Project paths are display metadata, never database locations or file-read inputs.
Only persisted snapshots or explicitly linked session metadata establish ownership.
"""

from __future__ import annotations

from typing import Any, cast

from loguru import logger

from nanobot.memory.models import Episode, Memory
from nanobot.memory.provenance import workspace_from_path
from nanobot.memory.repository import known_workspace_ids
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
        self._session_titles: dict[str, str | None] = {}
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

    def session_label(self, session_id: str | None) -> str | None:
        """会话 id → 可读标题；取不到返回 ``None``（调用方降级显示 key 截断）。

        同一次请求内按 session_id 缓存，避免 N 条记忆各查一次会话元数据。
        """
        if not session_id:
            return None
        if session_id not in self._session_titles:
            self._session_titles[session_id] = self._resolve_session_title(session_id)
        return self._session_titles[session_id]

    def _resolve_session_title(self, session_id: str) -> str | None:
        if self.services is None or self.services.session_metadata is None:
            return None
        try:
            payload = self.services.session_metadata(session_id)
        except Exception as exc:  # 标题解析失败不得隐藏记录本身
            logger.warning("memory session title unavailable: {}", type(exc).__name__)
            return None
        # ``SessionMetadataPayload`` 是 {key, created_at, updated_at, metadata}，
        # 标题在 ``metadata.title`` 里，顶层没有 title。没有自动命名/未改名的会话
        # 只有 ``session_handle``（聊天头显示的那个 @handle），用它兜底。
        metadata = _object_dict(_object_dict(payload).get("metadata"))
        for field in ("title", "session_handle"):
            raw = metadata.get(field)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return None

    def workspaces(self, item: Memory | Episode) -> list[dict[str, str]]:
        """该条记录的归属工作区。

        **归属以行的 ``workspace_id`` 列为准**（列与快照同源，D4）：快照只用来补
        名称与路径，不决定归属。否则会出现「列是全局、快照指向项目」这类历史数据
        被显示成项目记忆（实际却全局可见），旧快照 id 还会进筛选下拉却筛不出东西。
        """
        row_workspace_id = getattr(item, "workspace_id", None) or "default"
        snapshots = self._snapshot_workspaces(item)
        if row_workspace_id == "default":
            return []
        matched = next(
            (workspace for workspace in snapshots if workspace["id"] == row_workspace_id),
            None,
        )
        if matched is not None:
            return [matched]
        return [
            {
                "id": row_workspace_id,
                "name": f"未登记工作区 {row_workspace_id[:8]}",
                "path": "",
            }
        ]

    def _snapshot_workspaces(self, item: Memory | Episode) -> list[dict[str, str]]:
        provenance = provenance_for(item)
        snapshots = provenance.get("workspaces")
        if isinstance(snapshots, list):
            result: dict[str, dict[str, str]] = {}
            for snapshot in cast(list[object], snapshots):
                saved = _object_dict(snapshot)
                workspace_id = saved.get("id")
                path = saved.get("path")
                if isinstance(workspace_id, str) and workspace_id and isinstance(path, str):
                    name = saved.get("name")
                    result[workspace_id] = {
                        "id": workspace_id,
                        "name": name if isinstance(name, str) and name else f"未登记工作区 {workspace_id[:8]}",
                        "path": path,
                    }
                    continue
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
            for workspace in self._snapshot_workspaces(episode):
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
    global_option = {"id": "default", "name": "全局记忆", "path": ""}
    options = {"default": global_option}
    for item in items:
        for workspace in item["workspaces"]:
            if workspace["id"] != "default":
                options[workspace["id"]] = workspace
    with services.database.connect() as conn:
        for workspace_id in sorted(known_workspace_ids(conn)):
            if workspace_id not in options:
                options[workspace_id] = {
                    "id": workspace_id, "name": f"未登记工作区 {workspace_id[:8]}", "path": "",
                }
    return [global_option, *sorted(
        (option for key, option in options.items() if key != "default"),
        key=lambda workspace: (workspace["name"].casefold(), workspace["path"]),
    )]


def filter_result(
    items: list[dict[str, Any]], *, options: list[dict[str, str]],
    origin: str | None, workspace: str | None, limit: int | None,
) -> dict[str, Any]:
    if origin not in {None, "", "ai", "manual", "unknown"}:
        raise WebUISettingsError("invalid origin")
    if limit is not None and limit < 1:
        raise WebUISettingsError("limit must be >= 1")
    if workspace and workspace not in {option["id"] for option in options}:
        raise WebUISettingsError("invalid workspace")
    if workspace:
        items = [item for item in items if item.get("workspace_id") == workspace]
    counts = {"all": len(items), "ai": 0, "manual": 0, "unknown": 0}
    for item in items:
        counts[item["origin_kind"]] += 1
    selected = [item for item in items if not origin or item["origin_kind"] == origin]
    return {
        "items": selected if limit is None else selected[:limit],
        "total": len(selected), "source_counts": counts,
        "workspace_options": options, "scope": "current_store",
    }
