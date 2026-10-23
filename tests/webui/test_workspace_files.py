from pathlib import Path

import pytest

from nanobot.security.workspace_access import default_workspace_scope
from nanobot.webui.workspace_files import (
    MAX_SEARCH_FILE_BYTES,
    MAX_SEARCH_QUERY_CHARS,
    WebUIFilePreviewError,
    workspace_directory_payload,
    workspace_search_payload,
)


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


def test_listing_hides_dotfiles_by_default_and_marks_hidden(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / ".env").write_text("SECRET=1", encoding="utf-8")
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)

    default = workspace_directory_payload(None, scope=scope)
    assert [entry["name"] for entry in default["entries"]] == ["src", "a.md", "b.txt"]

    revealed = workspace_directory_payload(None, scope=scope, show_hidden=True)
    by_name = {entry["name"]: entry for entry in revealed["entries"]}
    assert by_name[".env"]["hidden"] is True
    assert by_name[".git"]["hidden"] is True
    assert by_name["node_modules"]["hidden"] is True
    assert by_name["a.md"]["hidden"] is False


def test_search_reports_name_and_content_hits_but_skips_hidden(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / "src" / "main.py").write_text("first\nprint('needle')\n", encoding="utf-8")
    (workspace / ".env").write_text("needle=1", encoding="utf-8")
    (workspace / "needle.txt").write_text("name hit\n", encoding="utf-8")
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)

    payload = workspace_search_payload("needle", scope=scope)
    paths = {hit["display_path"].replace("\\", "/") for hit in payload["results"]}
    assert payload["query"] == "needle"
    assert payload["truncated"] is False
    assert payload["scanned_files"] >= 3
    assert {"src/main.py", "needle.txt"} <= paths
    assert ".env" not in paths

    content = next(
        hit for hit in payload["results"]
        if hit["display_path"].replace("\\", "/") == "src/main.py"
    )
    assert content["line"] == 2
    assert content["column"] == 8
    assert content["match"] == "needle"
    assert "needle" in content["snippet"]

    name = next(
        hit for hit in payload["results"]
        if hit["display_path"].replace("\\", "/") == "needle.txt"
    )
    assert (name["line"], name["column"]) == (0, 0)
    assert name["match"] == "needle.txt"

    revealed = workspace_search_payload("needle", scope=scope, show_hidden=True)
    assert any(
        hit["display_path"].replace("\\", "/") == ".env" for hit in revealed["results"]
    )


def test_search_rejects_empty_and_oversized_queries(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)

    with pytest.raises(WebUIFilePreviewError) as empty:
        workspace_search_payload("   ", scope=scope)
    assert empty.value.status == 400

    with pytest.raises(WebUIFilePreviewError) as oversized:
        workspace_search_payload("x" * (MAX_SEARCH_QUERY_CHARS + 1), scope=scope)
    assert oversized.value.status == 400


def test_search_skips_binary_and_oversized_files(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    (workspace / "blob.bin").write_bytes(b"needle\x00binary")
    (workspace / "huge.txt").write_bytes(b"needle\n" + b"x" * MAX_SEARCH_FILE_BYTES)
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)

    payload = workspace_search_payload("needle", scope=scope)

    paths = {hit["display_path"].replace("\\", "/") for hit in payload["results"]}
    assert "blob.bin" not in paths
    assert "huge.txt" not in paths
