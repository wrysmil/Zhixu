from __future__ import annotations

import pytest

from nanobot.security.workspace_access import build_workspace_scope
from nanobot.webui.file_preview import (
    WebUIFilePreviewError,
    file_save_payload,
)


def _scope(tmp_path):
    return build_workspace_scope(tmp_path, "restricted")


def test_file_save_payload_overwrites_workspace_file(tmp_path) -> None:
    target = tmp_path / "notes.md"
    target.write_text("old\n", encoding="utf-8")

    payload = file_save_payload(str(target), "new\nline\n", scope=_scope(tmp_path))

    assert payload["display_path"] == "notes.md"
    assert payload["size"] == len("new\nline\n".encode())
    assert target.read_text(encoding="utf-8") == "new\nline\n"


def test_file_save_payload_rejects_paths_outside_workspace(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("keep\n", encoding="utf-8")

    with pytest.raises(WebUIFilePreviewError) as excinfo:
        file_save_payload(str(outside), "overwrite\n", scope=_scope(workspace))

    assert excinfo.value.status == 403
    assert outside.read_text(encoding="utf-8") == "keep\n"


def test_file_save_payload_rejects_non_text_content(tmp_path) -> None:
    target = tmp_path / "notes.md"
    target.write_text("old\n", encoding="utf-8")

    with pytest.raises(WebUIFilePreviewError) as excinfo:
        file_save_payload(str(target), b"bytes", scope=_scope(tmp_path))

    assert excinfo.value.status == 400


def test_file_save_payload_rejects_oversized_content(tmp_path) -> None:
    target = tmp_path / "notes.md"
    target.write_text("old\n", encoding="utf-8")

    with pytest.raises(WebUIFilePreviewError) as excinfo:
        file_save_payload(str(target), "abcdef", scope=_scope(tmp_path), max_bytes=3)

    assert excinfo.value.status == 413
    assert target.read_text(encoding="utf-8") == "old\n"
