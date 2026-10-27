"""记忆隔离键与可见集合的推导规则（Task 1b）。

隔离键来自**稳定工作区身份**（``nanobot.workspace_identity``），不再是 path
哈希：工作区改名/移动后键不变，复制目录会换新键。

测试全程把身份注册表重定向到临时目录，避免写进真实的 ``~/.nanobot``。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from nanobot import workspace_identity
from nanobot.memory.provenance import (
    GLOBAL_MEMORY_WORKSPACE_ID,
    memory_visibility_ids,
    memory_workspace_id,
    session_memory_workspace_id,
)
from nanobot.workspace_identity import workspace_identity_id

_ID_RE = re.compile(r"^[0-9a-f]{32}$")


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect the identity registry away from the real runtime data dir."""
    registry_root = tmp_path / "registry"

    def runtime_subdir(name: str) -> Path:
        path = registry_root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(workspace_identity, "get_runtime_subdir", runtime_subdir)
    workspace_identity.reset_workspace_identity_cache()
    yield
    workspace_identity.reset_workspace_identity_cache()


def test_workspace_id_comes_from_stable_identity(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    key = memory_workspace_id(str(project))

    assert _ID_RE.fullmatch(key)
    assert key != GLOBAL_MEMORY_WORKSPACE_ID
    assert key == workspace_identity_id(project)


def test_workspace_identity_marker_is_reused(tmp_path: Path) -> None:
    """键必须来自身份 marker，而不是本地再算一份 path 哈希。"""
    project = tmp_path / "proj"
    project.mkdir()

    key = memory_workspace_id(str(project))

    marker = project / ".nanobot" / "workspace-id"
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8").strip() == key
    assert memory_workspace_id(str(project)) == key


def test_missing_or_invalid_path_is_global() -> None:
    assert memory_workspace_id(None) == GLOBAL_MEMORY_WORKSPACE_ID
    assert memory_workspace_id("") == GLOBAL_MEMORY_WORKSPACE_ID
    assert memory_workspace_id("relative/path") == GLOBAL_MEMORY_WORKSPACE_ID
    assert memory_workspace_id("   ") == GLOBAL_MEMORY_WORKSPACE_ID
    assert memory_workspace_id("bad\x00path") == GLOBAL_MEMORY_WORKSPACE_ID


def test_relative_path_does_not_touch_the_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对路径属于非法输入：不得在当前目录落 marker（否则污染工作区）。"""
    monkeypatch.chdir(tmp_path)

    assert memory_workspace_id("relative/path") == GLOBAL_MEMORY_WORKSPACE_ID
    assert not (tmp_path / "relative").exists()


def test_default_workspace_is_not_special_cased(tmp_path: Path) -> None:
    """取消“默认工作区”概念：默认工作区与任何项目目录一样按自己的身份归属。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    key = memory_workspace_id(str(workspace))

    assert key != GLOBAL_MEMORY_WORKSPACE_ID
    assert _ID_RE.fullmatch(key)


def test_identity_failure_degrades_to_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """身份解析异常不得冒泡——记忆链路不能因身份解析失败而中断。"""
    project = tmp_path / "proj"
    project.mkdir()

    def _boom(_workspace: Path) -> str:
        raise RuntimeError("identity resolution failed")

    monkeypatch.setattr("nanobot.memory.provenance.workspace_identity_id", _boom)

    assert memory_workspace_id(str(project)) == GLOBAL_MEMORY_WORKSPACE_ID


def test_visibility_always_includes_global() -> None:
    assert memory_visibility_ids(GLOBAL_MEMORY_WORKSPACE_ID) == ("default",)
    assert memory_visibility_ids("abc123") == ("default", "abc123")
    assert memory_visibility_ids("") == ("default",)


def test_session_workspace_id_reads_session_scope(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    class _Session:
        metadata: dict = {"workspace_scope": {"project_path": str(project)}}

    assert session_memory_workspace_id(_Session()) == memory_workspace_id(str(project))


def test_session_without_scope_is_global() -> None:
    class _Session:
        metadata: dict = {}

    assert session_memory_workspace_id(_Session()) == GLOBAL_MEMORY_WORKSPACE_ID


def test_session_scope_without_usable_path_is_global() -> None:
    class _NonStringPath:
        metadata: dict = {"workspace_scope": {"project_path": 42}}

    class _MalformedScope:
        metadata: dict = {"workspace_scope": "not-a-dict"}

    assert session_memory_workspace_id(_NonStringPath()) == GLOBAL_MEMORY_WORKSPACE_ID
    assert session_memory_workspace_id(_MalformedScope()) == GLOBAL_MEMORY_WORKSPACE_ID
