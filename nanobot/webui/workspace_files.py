"""Workspace-scoped directory listing and bounded text search for the WebUI."""

from __future__ import annotations

import os
import time
from collections import deque
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

HIDDEN_ENTRY_NAMES = frozenset({"node_modules"})
SEARCH_SKIP_DIR_NAMES = frozenset({
    ".git", ".next", ".turbo", ".idea", ".cache",
    "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", "target",
})

MAX_SEARCH_QUERY_CHARS = 200
MAX_SEARCH_RESULTS = 100
MAX_SEARCH_FILES = 2000
MAX_SEARCH_FILE_BYTES = 256 * 1024
MAX_SEARCH_SNIPPET_CHARS = 240
MAX_SEARCH_MATCHES_PER_FILE = 5
SEARCH_DEADLINE_S = 2.0

WORKSPACE_DEFAULT_SCOPE_PARAM = "default"


def is_hidden_entry_name(name: str) -> bool:
    """Dot-prefixed names and dependency noise are hidden by default."""
    return name in HIDDEN_ENTRY_NAMES or name.startswith(".")


def resolve_workspace_anchor(
    scope_param: str | None,
    *,
    default_scope: WorkspaceScope,
) -> WorkspaceScope | None:
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
    show_hidden: bool = False,
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
        hidden = is_hidden_entry_name(entry.name)
        if hidden and not show_hidden:
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
                "hidden": hidden,
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


def workspace_search_payload(
    raw_query: str | None,
    *,
    scope: WorkspaceScope,
    show_hidden: bool = False,
) -> dict[str, Any]:
    """Search file names and UTF-8 text under the scoped workspace, bounded."""

    query = (raw_query or "").strip()
    if not query:
        raise WebUIFilePreviewError(400, "query is required")
    if len(query) > MAX_SEARCH_QUERY_CHARS:
        raise WebUIFilePreviewError(400, "query is too long")

    root = scope.project_path
    if not root.is_dir():
        raise WebUIFilePreviewError(404, "workspace not found")

    needle = query.casefold()
    deadline = time.monotonic() + SEARCH_DEADLINE_S
    results: list[dict[str, Any]] = []
    scanned_files = 0
    truncated = False
    queue: deque[Path] = deque([root])

    while queue and not truncated:
        directory = queue.popleft()
        if time.monotonic() >= deadline:
            truncated = True
            break
        try:
            with os.scandir(directory) as scan:
                entries = sorted(scan, key=lambda item: item.name.casefold())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.name in SEARCH_SKIP_DIR_NAMES:
                continue
            hidden = is_hidden_entry_name(entry.name)
            if hidden and not show_hidden:
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                queue.append(Path(entry.path))
                continue
            if scanned_files >= MAX_SEARCH_FILES:
                truncated = True
                break
            scanned_files += 1
            entry_path = Path(entry.path)

            if needle in entry.name.casefold():
                results.append(
                    {
                        "name": entry.name,
                        "path": str(entry_path),
                        "display_path": _display_path(entry_path, root),
                        "line": 0,
                        "column": 0,
                        "snippet": entry.name,
                        "match": entry.name,
                        "hidden": hidden,
                    }
                )
                if len(results) >= MAX_SEARCH_RESULTS:
                    truncated = True
                    break

            file_hits, timed_out = _search_file(
                entry_path, needle, query, hidden, root, deadline
            )
            results.extend(file_hits)
            if timed_out or len(results) >= MAX_SEARCH_RESULTS:
                truncated = True
                break

    return {
        "query": query,
        "results": results[:MAX_SEARCH_RESULTS],
        "truncated": truncated,
        "scanned_files": scanned_files,
    }


def _search_file(
    path: Path,
    needle: str,
    query: str,
    hidden: bool,
    root: Path,
    deadline: float,
) -> tuple[list[dict[str, Any]], bool]:
    try:
        if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
            return [], False
        raw = path.read_bytes()
    except OSError:
        return [], False
    if b"\x00" in raw[:8192]:
        return [], False

    hits: list[dict[str, Any]] = []
    text = raw.decode("utf-8", "replace")
    for line_number, line in enumerate(text.splitlines(), start=1):
        if time.monotonic() >= deadline:
            return hits, True
        index = line.casefold().find(needle)
        if index < 0:
            continue
        hits.append(
            {
                "name": path.name,
                "path": str(path),
                "display_path": _display_path(path, root),
                "line": line_number,
                "column": index + 1,
                "snippet": _search_snippet(line, index),
                "match": line[index:index + len(query)],
                "hidden": hidden,
            }
        )
        if len(hits) >= MAX_SEARCH_MATCHES_PER_FILE:
            break
    return hits, False


def _search_snippet(line: str, index: int) -> str:
    if len(line) <= MAX_SEARCH_SNIPPET_CHARS:
        return line.strip()
    start = max(0, index - MAX_SEARCH_SNIPPET_CHARS // 3)
    window = line[start:start + MAX_SEARCH_SNIPPET_CHARS].strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if start + MAX_SEARCH_SNIPPET_CHARS < len(line) else ""
    return f"{prefix}{window}{suffix}"


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
