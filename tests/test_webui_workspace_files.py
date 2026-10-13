"""workspace 维度只读文件端点单元测试（契约 §2：/api/workspace/files + preview scope 参数）。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from websockets.datastructures import Headers

from nanobot.security.workspace_access import WorkspaceScope, default_workspace_scope
from nanobot.webui.workspace_files import (
    resolve_workspace_anchor,
    workspace_directory_payload,
)
from nanobot.webui.ws_http import GatewayHTTPHandler

_SESSION_KEY = "websocket:chat-1"


def _make_workspace(tmp_path: Path, name: str = "workspace", marker: str = "root") -> Path:
    workspace = tmp_path / name
    (workspace / "src").mkdir(parents=True)
    (workspace / "a.md").write_text(marker, encoding="utf-8")
    return workspace


def _handler(workspaces: object) -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.config = SimpleNamespace()
    handler.workspaces = workspaces
    handler.check_api_token = lambda request: True
    return handler


def _request(path: str) -> SimpleNamespace:
    return SimpleNamespace(path=path, headers=Headers())


def _controller(scope: WorkspaceScope, session_scope: WorkspaceScope | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        default_scope=lambda: scope,
        scope_for_session_key=lambda key: session_scope if session_scope is not None else scope,
    )


# -- resolve_workspace_anchor ------------------------------------------------


def test_anchor_default_scope_param_returns_default_scope(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)

    assert resolve_workspace_anchor("default", default_scope=scope) is scope


def test_anchor_missing_param_defaults_to_default_scope(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)

    assert resolve_workspace_anchor(None, default_scope=scope) is scope


def test_anchor_unknown_scope_param_returns_none(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)

    assert resolve_workspace_anchor("project", default_scope=scope) is None


def test_anchor_returns_none_when_default_workspace_missing(tmp_path: Path) -> None:
    missing = tmp_path / "not-created"
    scope = default_workspace_scope(missing, restrict_to_workspace=True)

    assert resolve_workspace_anchor("default", default_scope=scope) is None


# -- GET /api/workspace/files ------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_files_route_lists_default_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setattr("nanobot.webui.workspace_files.get_media_dir", lambda: tmp_path / "media")
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)
    handler = _handler(_controller(scope))

    response = await handler._dispatch_misc_routes(
        None, _request("/api/workspace/files?scope=default"), "/api/workspace/files"
    )

    assert response is not None
    assert response.status_code == 200
    payload = json.loads(response.body)
    # 与 GET /api/sessions/{key}/files 同构（同一 payload 函数、同一锚点）。
    assert payload == workspace_directory_payload(None, scope=scope)
    assert [entry["name"] for entry in payload["entries"]] == ["src", "a.md"]
    assert Path(payload["project_path"]) == workspace.resolve()


@pytest.mark.asyncio
async def test_workspace_files_route_isomorphic_with_session_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setattr("nanobot.webui.workspace_files.get_media_dir", lambda: tmp_path / "media")
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)
    handler = _handler(_controller(scope))

    workspace_resp = await handler._dispatch_misc_routes(
        None, _request("/api/workspace/files?scope=default"), "/api/workspace/files"
    )
    session_resp = handler._handle_session_files(
        _request(f"/api/sessions/{quote(_SESSION_KEY, safe='')}/files"), _SESSION_KEY
    )

    assert workspace_resp is not None and workspace_resp.status_code == 200
    assert session_resp.status_code == 200
    assert json.loads(workspace_resp.body) == json.loads(session_resp.body)


@pytest.mark.asyncio
async def test_workspace_files_route_without_default_workspace_returns_404_json(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "not-created"
    scope = default_workspace_scope(missing, restrict_to_workspace=True)
    handler = _handler(_controller(scope))

    response = await handler._dispatch_misc_routes(
        None, _request("/api/workspace/files?scope=default"), "/api/workspace/files"
    )

    assert response is not None
    assert response.status_code == 404
    assert json.loads(response.body) == {"error": "no_default_workspace"}


@pytest.mark.asyncio
async def test_workspace_files_route_rejects_path_escape_with_400_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _make_workspace(tmp_path)
    monkeypatch.setattr("nanobot.webui.workspace_files.get_media_dir", lambda: tmp_path / "media")
    scope = default_workspace_scope(workspace, restrict_to_workspace=True)
    handler = _handler(_controller(scope))

    response = await handler._dispatch_misc_routes(
        None,
        _request("/api/workspace/files?path=../../etc&scope=default"),
        "/api/workspace/files",
    )

    assert response is not None
    assert response.status_code == 400
    assert json.loads(response.body) == {"error": "invalid_path"}


# -- file-preview scope 参数 -------------------------------------------------


def test_file_preview_scope_default_anchors_to_default_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_ws = _make_workspace(tmp_path, "session-ws", marker="session")
    default_ws = _make_workspace(tmp_path, "default-ws", marker="default-note")
    monkeypatch.setattr("nanobot.webui.file_preview.get_media_dir", lambda: tmp_path / "media")
    session_scope = default_workspace_scope(session_ws, restrict_to_workspace=True)
    default_scope = default_workspace_scope(default_ws, restrict_to_workspace=True)
    handler = _handler(_controller(default_scope, session_scope=session_scope))
    key = quote(_SESSION_KEY, safe="")

    without_scope = handler._handle_file_preview(
        _request(f"/api/sessions/{key}/file-preview?path={default_ws / 'a.md'}"),
        _SESSION_KEY,
    )
    assert without_scope.status_code in {403, 404}

    with_scope = handler._handle_file_preview(
        _request(f"/api/sessions/{key}/file-preview?path={default_ws / 'a.md'}&scope=default"),
        _SESSION_KEY,
    )
    assert with_scope.status_code == 200
    payload = json.loads(with_scope.body)
    assert payload["content"] == "default-note"
    assert payload["display_path"] == "a.md"


def test_file_preview_scope_default_missing_workspace_returns_404_json(
    tmp_path: Path,
) -> None:
    session_ws = _make_workspace(tmp_path, "session-ws")
    missing = tmp_path / "not-created"
    session_scope = default_workspace_scope(session_ws, restrict_to_workspace=True)
    default_scope = default_workspace_scope(missing, restrict_to_workspace=True)
    handler = _handler(_controller(default_scope, session_scope=session_scope))
    key = quote(_SESSION_KEY, safe="")

    response = handler._handle_file_preview(
        _request(f"/api/sessions/{key}/file-preview?path=x.md&scope=default"),
        _SESSION_KEY,
    )

    assert response.status_code == 404
    assert json.loads(response.body) == {"error": "no_default_workspace"}


def test_file_preview_empty_scope_param_falls_back_to_session_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit-but-empty ``?scope=`` must mean "absent" (session anchor), not "default"."""
    session_ws = _make_workspace(tmp_path, "session-ws", marker="session-note")
    default_ws = _make_workspace(tmp_path, "default-ws", marker="default-note")
    monkeypatch.setattr("nanobot.webui.file_preview.get_media_dir", lambda: tmp_path / "media")
    session_scope = default_workspace_scope(session_ws, restrict_to_workspace=True)
    default_scope = default_workspace_scope(default_ws, restrict_to_workspace=True)
    handler = _handler(_controller(default_scope, session_scope=session_scope))
    key = quote(_SESSION_KEY, safe="")

    without_scope = handler._handle_file_preview(
        _request(f"/api/sessions/{key}/file-preview?path={default_ws / 'a.md'}"),
        _SESSION_KEY,
    )
    empty_scope = handler._handle_file_preview(
        _request(f"/api/sessions/{key}/file-preview?path={default_ws / 'a.md'}&scope="),
        _SESSION_KEY,
    )

    assert without_scope.status_code in {403, 404}
    assert empty_scope.status_code == without_scope.status_code
