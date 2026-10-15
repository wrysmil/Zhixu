"""GET /webui/terminal/shells 单元测试（契约 v2：shell 白名单目录）。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from websockets.datastructures import Headers

from nanobot.webui.ws_http import GatewayHTTPHandler


def _handler(allowed: bool) -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.config = SimpleNamespace()
    handler.check_api_token = lambda request: allowed
    return handler


def _request(path: str) -> SimpleNamespace:
    return SimpleNamespace(path=path, headers=Headers())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_windows", "shell", "shell_path"),
    [(False, "zsh", "/bin/zsh"), (True, "cmd", "C:/Windows/System32/cmd.exe")],
)
async def test_shells_route_lists_whitelisted_installed_shells(
    monkeypatch, is_windows, shell, shell_path
):
    monkeypatch.setattr("nanobot.webui.pty_terminal._IS_WINDOWS", is_windows)
    monkeypatch.setattr(
        "nanobot.webui.pty_terminal.shutil.which",
        lambda name: shell_path if name == shell else None,
    )
    monkeypatch.setattr("nanobot.webui.pty_terminal.os.environ.get", lambda key, **k: None)
    handler = _handler(True)

    response = await handler._dispatch_misc_routes(
        None, _request("/webui/terminal/shells"), "/webui/terminal/shells"
    )
    assert response is not None and response.status_code == 200
    payload = json.loads(response.body)
    assert [entry["id"] for entry in payload["shells"]] == [shell]
    assert payload["default"] == shell
    for entry in payload["shells"]:
        assert set(entry) == {"id", "label", "path"}


@pytest.mark.asyncio
async def test_shells_route_requires_api_token(monkeypatch):
    handler = _handler(False)
    response = await handler._dispatch_misc_routes(
        None, _request("/webui/terminal/shells"), "/webui/terminal/shells"
    )
    assert response is not None
    assert response.status_code == 401
