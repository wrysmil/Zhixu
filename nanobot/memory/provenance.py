"""Persisted generation/trigger information and project snapshots.

These helpers never resolve a project from a conversation's prose or inspect
the filesystem. A snapshot must come from the session's explicit workspace scope.

The workspace-scoping helpers at the bottom of this module are the exception:
they derive the memory isolation key from the stable workspace identity, which
reads (and creates) ``<workspace>/.nanobot/workspace-id``.
"""

from __future__ import annotations

import hashlib
import ntpath
import posixpath
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from nanobot.workspace_identity import workspace_identity_id

if TYPE_CHECKING:
    from nanobot.session.manager import Session

_AUTOMATIC_SOURCES = frozenset({
    "session_end", "idle", "context_compress", "topic_change", "daily_consolidation",
})


def workspace_from_path(path: str) -> dict[str, str]:
    """Create a stable identity for an absolute, already trusted project path."""
    if not path.strip() or "\x00" in path:
        raise ValueError("workspace path must be an absolute path")
    windows_path = PureWindowsPath(path)
    if windows_path.is_absolute():
        normalized = ntpath.normpath(path)
        identity = ntpath.normcase(normalized)
        name = PureWindowsPath(normalized).name or normalized
    elif PurePosixPath(path).is_absolute():
        normalized = posixpath.normpath(path)
        identity = normalized
        name = PurePosixPath(normalized).name or normalized
    else:
        raise ValueError("workspace path must be an absolute path")
    return {
        "id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        "name": name,
        "path": normalized,
    }


def extraction_provenance(
    session: Session, source: str, *, workspace_id: str | None = None
) -> dict[str, Any]:
    """捕获提取来源与工作区快照，显式归全局时不保留工作区。

    调用方传入落库隔离键时，快照 id 与该键一致；省略参数保留旧快照行为。
    """
    trigger_mode = (
        "automatic" if source in _AUTOMATIC_SOURCES
        else "manual" if source == "manual"
        else "unknown"
    )
    workspaces: list[dict[str, str]] = []
    if workspace_id != GLOBAL_MEMORY_WORKSPACE_ID:
        scope = session.metadata.get("workspace_scope")
        if isinstance(scope, dict):
            project_path = cast(dict[str, Any], scope).get("project_path")
            if isinstance(project_path, str):
                try:
                    snapshot = workspace_from_path(project_path)
                    if workspace_id is not None:
                        snapshot["id"] = workspace_id
                    workspaces.append(snapshot)
                except ValueError:
                    pass
    return {
        "origin_kind": "manual" if trigger_mode == "manual" else "ai",
        "generation_method": "ai",
        "trigger_mode": trigger_mode,
        "trigger_reason": source,
        "workspaces": workspaces,
        "session_id": session.key,
    }


def merge_workspace_provenance(
    metadata: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
    """Add contributing projects without rewriting a memory's original source."""
    merged = dict(metadata)
    existing_raw = merged.get("provenance")
    existing = dict(cast(dict[str, Any], existing_raw)) if isinstance(existing_raw, dict) else {}
    projects = existing.get("workspaces")
    workspaces: list[dict[str, Any]] = []
    known_ids: set[str] = set()
    for raw_list in (projects, incoming.get("workspaces")):
        if not isinstance(raw_list, list):
            continue
        for raw_item in cast(list[object], raw_list):
            if not isinstance(raw_item, dict):
                continue
            item = cast(dict[str, Any], raw_item)
            workspace_id = item.get("id")
            if isinstance(workspace_id, str) and workspace_id not in known_ids:
                workspaces.append(dict(item))
                known_ids.add(workspace_id)
    if workspaces or existing:
        existing["workspaces"] = workspaces
        merged["provenance"] = existing
    return merged


GLOBAL_MEMORY_WORKSPACE_ID = "default"


def memory_workspace_id(project_path: str | None) -> str:
    """工作区路径 → 记忆隔离键。

    委托稳定工作区身份（``nanobot.workspace_identity``）：id 写在工作区内的
    ``.nanobot/workspace-id``，改名/移动后不变，复制目录会换新 id。缺失或非法
    路径（空串、相对路径、含空字符）→ ``'default'``（全局记忆）。

    任何异常都不得冒泡：记忆链路不能因为身份解析失败而整体中断。
    """
    if not isinstance(project_path, str):
        return GLOBAL_MEMORY_WORKSPACE_ID
    candidate = project_path.strip()
    if not candidate or "\x00" in candidate:
        return GLOBAL_MEMORY_WORKSPACE_ID
    try:
        snapshot = workspace_from_path(candidate)
        return workspace_identity_id(Path(snapshot["path"]))
    except Exception as exc:  # noqa: BLE001 - 隔离键推导永不抛异常
        logger.warning(
            "memory workspace id resolution failed, falling back to global scope: "
            "path={} error={}",
            project_path,
            exc,
        )
        return GLOBAL_MEMORY_WORKSPACE_ID


def session_memory_workspace_id(session: "Session") -> str:
    """会话 → 记忆隔离键（读 ``session.metadata.workspace_scope.project_path``）。

    会话没有工作区（非 WebUI 渠道等）→ ``'default'``（全局记忆）。
    """
    metadata: dict[str, Any] | None = getattr(session, "metadata", None)
    if not isinstance(metadata, dict):
        return GLOBAL_MEMORY_WORKSPACE_ID
    scope = metadata.get("workspace_scope")
    if not isinstance(scope, dict):
        return GLOBAL_MEMORY_WORKSPACE_ID
    project_path = cast(dict[str, Any], scope).get("project_path")
    if not isinstance(project_path, str):
        return GLOBAL_MEMORY_WORKSPACE_ID
    return memory_workspace_id(project_path)


def memory_visibility_ids(workspace_id: str) -> tuple[str, ...]:
    """某工作区内可见的记忆隔离键集合：全局 + 本工作区。"""
    key = (workspace_id or "").strip() or GLOBAL_MEMORY_WORKSPACE_ID
    if key == GLOBAL_MEMORY_WORKSPACE_ID:
        return (GLOBAL_MEMORY_WORKSPACE_ID,)
    return (GLOBAL_MEMORY_WORKSPACE_ID, key)
