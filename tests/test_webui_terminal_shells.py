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


@pytest.mark.parametrize(
    ("is_windows", "expected_id"),
    [(False, "zsh"), (True, "pwsh")],
)
@pytest.mark.asyncio
async def test_shells_route_lists_whitelisted_installed_shells(
    monkeypatch, is_windows, expected_id
):
    """路由只暴露本平台白名单里确实装了的 shell。

    白名单按平台切换，所以这里显式钉住 ``_IS_WINDOWS``，让两个分支在任意
    操作系统上都被覆盖，而不是只测到当前平台那一个。
    """
    monkeypatch.setattr("nanobot.webui.pty_terminal._IS_WINDOWS", is_windows)
    monkeypatch.setattr(
        "nanobot.webui.pty_terminal.shutil.which",
        lambda name: f"/bin/{name}" if name == expected_id else None,
    )
    monkeypatch.setattr("nanobot.webui.pty_terminal.os.environ.get", lambda key, **k: None)
    handler = _handler(True)

    response = await handler._dispatch_misc_routes(
        None, _request("/webui/terminal/shells"), "/webui/terminal/shells"
    )
    assert response is not None and response.status_code == 200
    payload = json.loads(response.body)
    assert [entry["id"] for entry in payload["shells"]] == [expected_id]
    assert payload["default"] == expected_id
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
