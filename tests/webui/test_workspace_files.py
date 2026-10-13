from pathlib import Path

import pytest

from nanobot.security.workspace_access import default_workspace_scope
from nanobot.webui.workspace_files import WebUIFilePreviewError, workspace_directory_payload


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / ".git").mkdir()
    (workspace / "node_modules").mkdir()
    (workspace / "b.txt").write_text("b", encoding="utf-8")
    (workspace / "a.md").write_text("hello", encoding="utf-8")
    (workspace / "src" / "main.py").write_text("print()", encoding="utf-8")
    return workspace


def test_root_listing_lists_directories_first(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)

    payload = workspace_directory_payload(None, scope=scope)

    names = [entry["name"] for entry in payload["entries"]]
    assert names == ["src", "a.md", "b.txt"]
    assert payload["entries"][0]["is_dir"] is True
    assert payload["entries"][1]["display_path"] == "a.md"
    assert payload["entries"][1]["size"] == 5
    assert payload["parent_path"] is None
    assert payload["truncated"] is False


def test_subdirectory_listing_exposes_parent(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)

    payload = workspace_directory_payload(str(workspace / "src"), scope=scope)

    assert [entry["name"] for entry in payload["entries"]] == ["main.py"]
    assert Path(payload["parent_path"]) == workspace


def test_listing_rejects_paths_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setattr(
        "nanobot.webui.workspace_files.get_media_dir",
        lambda: tmp_path / "media",
    )
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)

    with pytest.raises(WebUIFilePreviewError, match="outside the current workspace") as exc_info:
        workspace_directory_payload(str(tmp_path), scope=scope)

    assert exc_info.value.status == 403


def test_listing_rejects_file_paths(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)

    with pytest.raises(WebUIFilePreviewError, match="not a directory") as exc_info:
        workspace_directory_payload(str(workspace / "a.md"), scope=scope)

    assert exc_info.value.status == 400
