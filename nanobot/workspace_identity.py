"""Stable workspace identity.

Mirrors the session namespace mechanism in ``nanobot.session.manager``: the
identity lives in ``.nanobot/workspace-id`` inside the workspace (32 hex
characters), so **renaming or moving a workspace keeps its id**, while copying
the directory elsewhere rotates a new id (the copy does not inherit the
original workspace's memories).

A read-only workspace cannot persist the marker, so the identity degrades to a
path hash — that ownership is *derived*, gets lost on rename, and the UI has
to label it as such.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import posixpath
import re
import secrets
import threading
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, cast

from loguru import logger

from nanobot.config.paths import get_runtime_subdir

_MARKER_DIR = ".nanobot"
_MARKER_NAME = "workspace-id"
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_FALLBACK_LEN = 24
_UNKNOWN_FALLBACK_ID = "0" * _FALLBACK_LEN
_LOCK = threading.Lock()
_CACHE: dict[str, str] = {}


def workspace_marker_path(workspace: Path) -> Path:
    """Marker path of the workspace identity: ``<workspace>/.nanobot/workspace-id``."""
    return Path(workspace) / _MARKER_DIR / _MARKER_NAME


def path_fallback_id(workspace: Path) -> str:
    """Degraded identity for workspaces that cannot persist a marker.

    Hash of the normalized path: 24 hex characters, so it never collides with
    the 32 character marker identity.
    """
    raw = str(Path(workspace).expanduser())
    windows_path = PureWindowsPath(raw)
    if windows_path.is_absolute():
        identity = ntpath.normcase(ntpath.normpath(raw))
    elif PurePosixPath(raw).is_absolute():
        identity = posixpath.normpath(raw)
    else:
        identity = raw
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:_FALLBACK_LEN]


def workspace_identity_id(workspace: Path) -> str:
    """Workspace -> stable identity id.

    Order: existing marker -> copy detection -> create marker -> path hash when
    the workspace is not writable. No exception may escape: the memory chain
    must not break because identity resolution failed.
    """
    try:
        target = Path(workspace).expanduser()
        if not str(target).strip():
            return _safe_fallback_id(target)
        cache_key = _canonical_key(target)
        cached = _CACHE.get(cache_key)
        if cached is not None:
            return cached
        with _LOCK:
            cached = _CACHE.get(cache_key)
            if cached is not None:
                return cached
            value = _resolve(target)
            _CACHE[cache_key] = value
            return value
    except Exception as exc:  # noqa: BLE001 - identity resolution never breaks callers
        logger.warning(
            "workspace identity resolution failed, using path hash: workspace={} error={}",
            workspace,
            exc,
        )
        return _safe_fallback_id(workspace)


def reset_workspace_identity_cache() -> None:
    """Clear the in-process identity cache (config reload / tests)."""
    with _LOCK:
        _CACHE.clear()


def _resolve(workspace: Path) -> str:
    marker = workspace_marker_path(workspace)
    existing = _read_marker(marker)
    if existing is not None:
        if not _is_claimed_by_other_path(existing, workspace):
            _claim(existing, workspace)
            return existing
        logger.warning(
            "workspace identity rotated because the marker is claimed by another path: workspace={}",
            workspace,
        )
    elif marker.exists():
        logger.warning("workspace identity marker is invalid, rotating: workspace={}", workspace)
    created = secrets.token_hex(16)
    try:
        _write_marker(marker, created)
    except OSError as exc:
        logger.warning(
            "workspace identity marker is not writable, using path hash: workspace={} error={}",
            workspace,
            exc,
        )
        return path_fallback_id(workspace)
    _claim(created, workspace)
    return created


def _read_marker(marker: Path) -> str | None:
    if marker.is_symlink():
        return None
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return value if _ID_RE.fullmatch(value) else None


def _write_marker(marker: Path, value: str) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_name(f".{marker.name}.{secrets.token_hex(8)}.tmp")
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, flags, 0o600)
        try:
            payload = f"{value}\n".encode("ascii")
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, marker)
    finally:
        tmp.unlink(missing_ok=True)


def _registry_dir() -> Path:
    return Path(get_runtime_subdir("workspace-registry"))


def _claim(workspace_id: str, workspace: Path) -> None:
    """Bind the identity to the current path (registry drives copy detection)."""
    try:
        directory = _registry_dir()
        directory.mkdir(parents=True, exist_ok=True)
        recorded = _read_registry_path(workspace_id)
        target = str(_absolute_path(workspace))
        if recorded == target:
            return
        (directory / f"{workspace_id}.json").write_text(
            json.dumps({"path": target}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning(
            "workspace identity registry claim failed: workspace={} error={}",
            workspace,
            exc,
        )


def _read_registry_path(workspace_id: str) -> str | None:
    try:
        raw = (_registry_dir() / f"{workspace_id}.json").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    try:
        data: Any = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    path = cast(dict[str, Any], data).get("path")
    if not isinstance(path, str) or not path:
        return None
    return path


def _is_claimed_by_other_path(workspace_id: str, workspace: Path) -> bool:
    """Copy detection: the same id is held by another path that still exists.

    A vanished path means the workspace was renamed or moved, so the id stays.
    """
    recorded_text = _read_registry_path(workspace_id)
    if recorded_text is None:
        return False
    recorded = Path(recorded_text)
    if _canonical_key(recorded) == _canonical_key(workspace):
        return False
    try:
        if not recorded.exists():
            return False
        return not recorded.samefile(workspace)
    except OSError:
        return False


def _absolute_path(workspace: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(workspace))))


def _canonical_key(workspace: Path) -> str:
    return os.path.normcase(str(_absolute_path(workspace)))


def _safe_fallback_id(workspace: Path) -> str:
    try:
        return path_fallback_id(workspace)
    except Exception:  # noqa: BLE001 - the fallback must never raise either
        return _UNKNOWN_FALLBACK_ID
