"""Workspace-scoped directory listing payloads for the WebUI file tree."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from nanobot.config.paths import get_media_dir
from nanobot.security.workspace_access import WorkspaceScope
from nanobot.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path
from nanobot.webui.file_preview import (
    WebUIFilePreviewError,
    _clean_preview_path,
    _display_path,
)

MAX_WORKSPACE_LIST_ENTRIES = 400

# Never surfaced in the file tree: noise that users cannot act on from the UI.
HIDDEN_ENTRY_NAMES = frozenset({".git", "node_modules"})

WORKSPACE_DEFAULT_SCOPE_PARAM = "default"


def resolve_workspace_anchor(
    scope_param: str | None,
    *,
    default_scope: WorkspaceScope,
) -> WorkspaceScope | None:
    """Resolve the read-only anchor for workspace-dimension WebUI routes.

    Only ``scope=default`` (or a missing parameter) is supported; the anchor is
    the default workspace/project scope. Returns ``None`` when an unknown scope
    is requested or the default workspace directory does not exist, so callers
    answer 404 ``no_default_workspace`` per the contract.
    """

    requested = (scope_param or WORKSPACE_DEFAULT_SCOPE_PARAM).strip().lower()
    if requested != WORKSPACE_DEFAULT_SCOPE_PARAM:
        return None
    if not default_scope.project_path.is_dir():
        return None
    return default_scope


def workspace_directory_payload(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
) -> dict[str, Any]:
    """List one directory allowed by the session workspace scope (directories first)."""

    resolved = _resolve_directory_path(raw_path, scope=scope)

    try:
        with os.scandir(resolved) as scan:
            scanned = list(scan)
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to read directory") from e

    scanned.sort(key=lambda entry: (not entry.is_dir(), entry.name.lower()))

    entries: list[dict[str, Any]] = []
    truncated = False
    for entry in scanned:
        if entry.name in HIDDEN_ENTRY_NAMES:
            continue
        if len(entries) >= MAX_WORKSPACE_LIST_ENTRIES:
            truncated = True
            break
        try:
            is_dir = entry.is_dir()
            size = 0 if is_dir else entry.stat().st_size
        except OSError:
            continue
        entry_path = Path(entry.path)
        entries.append(
            {
                "name": entry.name,
                "path": str(entry_path),
                "display_path": _display_path(entry_path, scope.project_path),
                "is_dir": is_dir,
                "size": size,
            }
        )

    return {
        "path": str(resolved),
        "display_path": _display_path(resolved, scope.project_path),
        "project_path": str(scope.project_path),
        "parent_path": _parent_path(resolved, scope.project_path),
        "entries": entries,
        "truncated": truncated,
    }


def _resolve_directory_path(raw_path: str | None, *, scope: WorkspaceScope) -> Path:
    path = _clean_preview_path(raw_path)
    if not path:
        if not scope.project_path.is_dir():
            raise WebUIFilePreviewError(404, "workspace not found")
        return scope.project_path
    if len(path) > 4096:
        raise WebUIFilePreviewError(400, "path is too long")

    try:
        extra_roots = [get_media_dir()] if scope.restrict_to_workspace else None
        resolved = resolve_allowed_path(
            path,
            workspace=scope.project_path,
            allowed_root=scope.project_path if scope.restrict_to_workspace else None,
            extra_allowed_roots=extra_roots,
            strict=True,
        )
    except FileNotFoundError as e:
        raise WebUIFilePreviewError(404, "directory not found") from e
    except WorkspaceBoundaryError as e:
        raise WebUIFilePreviewError(
            403,
            "directory is outside the current workspace",
        ) from e
    except OSError as e:
        raise WebUIFilePreviewError(400, "invalid path") from e

    if not resolved.is_dir():
        raise WebUIFilePreviewError(400, "path is not a directory")
    return resolved


def _parent_path(path: Path, root: Path) -> str | None:
    if path == root:
        return None
    parent = path.parent
    if parent == path:
        return None
    return str(parent)
