"""Stable workspace identity module tests.

Every test resolves identities inside ``tmp_path`` and redirects the runtime
registry to a temporary directory, so the suite never writes into the real
``~/.nanobot`` data directory.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from nanobot import workspace_identity
from nanobot.workspace_identity import (
    path_fallback_id,
    reset_workspace_identity_cache,
    workspace_identity_id,
    workspace_marker_path,
)

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_FALLBACK_RE = re.compile(r"^[0-9a-f]{24}$")


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect the identity registry away from the real runtime data dir."""
    registry_root = tmp_path / "registry"

    def runtime_subdir(name: str) -> Path:
        path = registry_root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(workspace_identity, "get_runtime_subdir", runtime_subdir)
    reset_workspace_identity_cache()
    yield
    reset_workspace_identity_cache()


def test_first_call_creates_marker_and_returns_stable_id(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    first = workspace_identity_id(project)

    assert _ID_RE.fullmatch(first)
    marker = workspace_marker_path(project)
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8").strip() == first
    assert workspace_identity_id(project) == first


def test_renamed_workspace_keeps_identity(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    original = workspace_identity_id(project)

    moved = tmp_path / "proj-renamed"
    shutil.move(str(project), str(moved))

    assert workspace_identity_id(moved) == original
    assert workspace_marker_path(moved).read_text(encoding="utf-8").strip() == original


def test_copied_workspace_gets_a_new_identity(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    original = workspace_identity_id(project)

    copied = tmp_path / "proj-copy"
    shutil.copytree(str(project), str(copied))

    copy_identity = workspace_identity_id(copied)
    assert copy_identity != original
    assert _ID_RE.fullmatch(copy_identity)
    assert workspace_marker_path(copied).read_text(encoding="utf-8").strip() == copy_identity
    # The original workspace keeps its own identity.
    assert workspace_identity_id(project) == original


def test_missing_workspace_is_not_created_on_disk(tmp_path: Path) -> None:
    """不存在的工作区不得被 marker 写入凭空造出来。

    ``_write_marker`` 用 ``mkdir(parents=True)`` 建 ``.nanobot/``：如果工作区
    目录本身不存在，就会连工作区一起凭空创建——POSIX 上 ``C:\\Work\\A`` 这类
    Windows 路径被 ``pathlib`` 当成相对目录，直接在进程 cwd 里长出一层同名
    目录。身份解析必须退化为 path 哈希，且不碰文件系统。
    """
    missing = tmp_path / "does-not-exist"

    identity = workspace_identity_id(missing)

    assert _FALLBACK_RE.fullmatch(identity)
    assert identity == path_fallback_id(missing)
    assert not missing.exists()
    assert not workspace_marker_path(missing).exists()


def test_unwritable_workspace_falls_back_to_path_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    original = workspace_identity_id(project)

    readonly = tmp_path / "readonly"
    readonly.mkdir()

    def _deny(marker: Path, value: str) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(workspace_identity, "_write_marker", _deny)
    fallback = workspace_identity_id(readonly)

    assert _FALLBACK_RE.fullmatch(fallback)
    assert not _ID_RE.fullmatch(fallback)
    assert fallback != original
    assert fallback == path_fallback_id(readonly)
    assert not workspace_marker_path(readonly).exists()


def test_resolution_failure_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    def _boom(marker: Path) -> str | None:
        raise OSError("marker unreadable")

    monkeypatch.setattr(workspace_identity, "_read_marker", _boom)
    fallback = workspace_identity_id(project)

    assert _FALLBACK_RE.fullmatch(fallback)


def test_cached_identity_is_returned_without_disk_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    first = workspace_identity_id(project)

    def _boom(marker: Path) -> str | None:
        raise AssertionError("marker must not be read again")

    monkeypatch.setattr(workspace_identity, "_read_marker", _boom)
    assert workspace_identity_id(project) == first


def test_reset_cache_allows_fresh_resolution(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    first = workspace_identity_id(project)

    reset_workspace_identity_cache()

    assert workspace_identity_id(project) == first


def test_registry_records_the_workspace_path(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    identity = workspace_identity_id(project)

    record = tmp_path / "registry" / "workspace-registry" / f"{identity}.json"
    assert record.is_file()
    payload = json.loads(record.read_text(encoding="utf-8"))
    recorded = Path(payload["path"])
    assert recorded.resolve(strict=False) == project.resolve(strict=False)
