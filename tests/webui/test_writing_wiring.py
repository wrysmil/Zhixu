"""写作域接线检查（A1.6）。

``test_writing_routes.py`` 证明 handler 本身正确，本文件证明它*外面*的三张表
对得上：``_SYSTEM_ROUTES`` 路径表、``_SETTINGS_MUTATION_PATHS`` WS-only 门禁、
``_WEBUI_MUTATION_PATHS`` WS action 翻译表，外加 gateway 的装配与 router 端到端。

这三张表漏一条都不会在 import 期报错：路径漏登记 404，写路径漏进白名单则未认证
的普通 GET 就能改库，WS action 漏登记则前端提交时报 404。
"""

from __future__ import annotations

import inspect
import json
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Response

from nanobot.webui import settings_routes, ws_http
from nanobot.webui import writing_routes
from nanobot.webui.gateway_services import build_gateway_services, build_writing_operations
from nanobot.webui.http_utils import http_json_response
from nanobot.webui.settings_contracts import SettingsRequest
from nanobot.webui.settings_routes import (
    _SETTINGS_MUTATION_PATHS,
    _SYSTEM_ROUTES,
    _WRITING_MUTATION_PATHS,
    _null_writing_operations,
    WebUISettingsRouter,
)
from nanobot.webui.settings_services import WebUISettingsServices
from nanobot.webui.writing_routes import (
    WRITING_ACTION_NAMES,
    WritingSettingsHandler,
    WritingSettingsOperations,
)
from nanobot.webui.ws_http import _WEBUI_MUTATION_PATHS, GatewayHTTPHandler

# 契约 §4/§5：读走 HTTP GET，写走 WS mutation，路径与 action 逐字固定。
EXPECTED_READ_ROUTES = {
    "/api/writing/articles": "article.list",
    "/api/writing/articles/get": "article.get",
}

EXPECTED_WRITE_ROUTES = {
    "/api/writing/articles/create": "article.create",
    "/api/writing/articles/save": "article.save",
    "/api/writing/articles/duplicate": "article.duplicate",
    "/api/writing/articles/delete": "article.delete",
}

EXPECTED_SYSTEM_ROUTES = {**EXPECTED_READ_ROUTES, **EXPECTED_WRITE_ROUTES}

EXPECTED_WRITE_PATHS = frozenset(EXPECTED_WRITE_ROUTES)

# 契约 §5：前端 action → HTTP 路径（点号，与领域动作名同名）。
EXPECTED_WS_ACTIONS = {
    "article.create": "/api/writing/articles/create",
    "article.save": "/api/writing/articles/save",
    "article.duplicate": "/api/writing/articles/duplicate",
    "article.delete": "/api/writing/articles/delete",
}


# ---- R1: _SYSTEM_ROUTES（读路径 + 写路径落地）------------------------------


@pytest.mark.parametrize("path,action", sorted(EXPECTED_SYSTEM_ROUTES.items()))
def test_routes_are_registered(path: str, action: str) -> None:
    assert _SYSTEM_ROUTES.get(path) == action
    assert WebUISettingsRouter._route(path) == ("system", action)


def test_system_routes_has_exactly_the_expected_writing_entries() -> None:
    assert {path for path in _SYSTEM_ROUTES if "/api/writing" in path} == set(
        EXPECTED_SYSTEM_ROUTES
    )


def test_registered_actions_all_belong_to_the_domain() -> None:
    """路由表里出现的 action 必须是 WRITING_ACTION_NAMES 的成员，否则会掉进
    system handler 而不是 writing handler。"""
    assert set(EXPECTED_SYSTEM_ROUTES.values()) == WRITING_ACTION_NAMES


def test_reads_are_not_mutations() -> None:
    """读路径走普通 GET；误登记会让文章列表只能从 WS 拉取。"""
    for path in EXPECTED_READ_ROUTES:
        assert path not in _SETTINGS_MUTATION_PATHS
        assert not WebUISettingsRouter.is_mutation_path(path)


# ---- R2: _SETTINGS_MUTATION_PATHS（WS-only 门禁）---------------------------


@pytest.mark.parametrize("path", sorted(EXPECTED_WRITE_PATHS))
def test_write_paths_require_authenticated_websocket(path: str) -> None:
    assert path in _SETTINGS_MUTATION_PATHS
    assert WebUISettingsRouter.is_mutation_path(path)


def test_writing_mutation_set_matches_registered_writes() -> None:
    assert _WRITING_MUTATION_PATHS == EXPECTED_WRITE_PATHS


def test_writing_mutations_do_not_leak_into_other_domains() -> None:
    assert not (
        _WRITING_MUTATION_PATHS
        & (
            settings_routes._MEMORY_MUTATION_PATHS
            | settings_routes._IDENTITY_MUTATION_PATHS
            | settings_routes._AGENTS_MUTATION_PATHS
        )
    )


# ---- R3: _WEBUI_MUTATION_PATHS（WS action → HTTP 路径）---------------------


@pytest.mark.parametrize("action,path", sorted(EXPECTED_WS_ACTIONS.items()))
def test_ws_action_maps_to_http_path(action: str, path: str) -> None:
    assert _WEBUI_MUTATION_PATHS.get(action) == path
    assert GatewayHTTPHandler._webui_mutation_path(action, {}) == path


def test_ws_action_names_match_frontend_contract() -> None:
    assert {a for a in _WEBUI_MUTATION_PATHS if a.startswith("article.")} == set(
        EXPECTED_WS_ACTIONS
    )


def test_unregistered_ws_action_is_404() -> None:
    result = GatewayHTTPHandler._webui_mutation_path("article.frobnicate", {})
    assert isinstance(result, Response)
    assert result.status_code == 404


def test_ws_mutation_actions_are_exactly_the_write_actions() -> None:
    """契约 §5：只有四个写动作有 WS 翻译，读动作不进入 mutation 通道。

    ``WRITING_MUTATION_ACTION_NAMES``（handler 侧）与 ``_WEBUI_MUTATION_PATHS``
    （传输侧）必须同一口径，否则前端能提交一个 handler 不认的动作名。
    """
    assert writing_routes.WRITING_MUTATION_ACTION_NAMES == frozenset(EXPECTED_WS_ACTIONS)
    assert (
        WRITING_ACTION_NAMES - writing_routes.WRITING_MUTATION_ACTION_NAMES
        == frozenset(EXPECTED_READ_ROUTES.values())
    )


def test_ws_mapped_paths_are_known_mutations() -> None:
    """翻译表与 mutation 名单必须对得上，否则跳到目标路径会被 405 弹回。"""
    for path in EXPECTED_WS_ACTIONS.values():
        assert WebUISettingsRouter.is_mutation_path(path)


# ---- R4: gateway 装配 -------------------------------------------------------


def test_build_writing_operations_is_fully_bound(tmp_path: Path) -> None:
    ops = build_writing_operations(workspace_id="default", workspace_path=tmp_path)
    for field in fields(WritingSettingsOperations):
        assert callable(getattr(ops, field.name))


def test_build_writing_operations_writes_through_to_the_workspace(
    tmp_path: Path,
) -> None:
    ops = build_writing_operations(workspace_id="default", workspace_path=tmp_path)
    created = ops.create_article(client_request_id="r-1", title="标题")
    assert created["article"]["revision"] == 1
    assert (tmp_path / "writing" / "state.db").exists()
    assert ops.list_articles(query=None, status=None, limit=None, offset=None)["total"] == 1


def test_gateway_services_wires_writing_operations() -> None:
    """build_gateway_services 必须把 writing_operations 传进 HTTP handler。"""
    assert "writing_operations=" in inspect.getsource(build_gateway_services)


def test_gateway_http_handler_forwards_writing_operations() -> None:
    assert "writing_operations=self.writing_operations" in inspect.getsource(
        GatewayHTTPHandler
    )


# ---- R5: null 兜底 ----------------------------------------------------------


def test_null_writing_operations_returns_503_for_every_action() -> None:
    handler = WritingSettingsHandler(_null_writing_operations())
    # 输入必须合法，否则 dispatch 会先在参数校验上返回 400，碰不到 operations。
    cases = [
        ("article.list", SettingsRequest(query={})),
        ("article.get", SettingsRequest(query={}, payload={"id": "a"})),
        ("article.create", SettingsRequest(
            query={}, payload={"client_request_id": "r-1", "title": "标题"},
        )),
        ("article.save", SettingsRequest(
            query={},
            payload={
                "client_request_id": "r-1", "article_id": "a",
                "base_revision": 1, "snapshot": {"title": "标题"},
            },
        )),
        ("article.duplicate", SettingsRequest(
            query={},
            payload={"client_request_id": "r-1", "article_id": "a", "base_revision": 1},
        )),
        ("article.delete", SettingsRequest(
            query={},
            payload={"client_request_id": "r-1", "article_id": "a", "base_revision": 1},
        )),
    ]
    for action, request in cases:
        result = handler.handle(action, request)
        assert result.status == 503, action
        assert result.error == "writing service is not configured"
        assert result.payload is None


# ---- R6: router 端到端 ------------------------------------------------------

WS_BASE_ARGS: dict[str, Any] = {
    "bus": SimpleNamespace(),
    "logger": SimpleNamespace(exception=lambda *_args: None),
    "check_api_token": lambda _request: True,
    "parse_query": lambda path: parse_qs(urlsplit(path).query),
    "json_response": http_json_response,
    "error_response": lambda status, message: http_json_response(
        {"error": message}, status=status
    ),
    "runtime_surface": "browser",
    "runtime_capabilities": {},
}


def _router(config_path: Path, operations: WritingSettingsOperations | None):
    return WebUISettingsRouter(
        settings=WebUISettingsServices.create(config_path),
        writing_operations=operations,
        **WS_BASE_ARGS,
    )


def _get(path: str) -> SimpleNamespace:
    return SimpleNamespace(path=path, headers=Headers())


def _mutation_request(path: str, payload: dict[str, Any]) -> SimpleNamespace:
    request = _get(path)
    request._nanobot_webui_mutation_request = True
    request._nanobot_webui_mutation_payload = payload
    request._nanobot_trusted_proxy_authenticated = True
    return request


@pytest.mark.asyncio
async def test_router_serves_article_list_over_get(tmp_path: Path) -> None:
    router = _router(tmp_path / "config.json", build_writing_operations(
        workspace_id="default", workspace_path=tmp_path,
    ))
    # 真实链路上 ws_http 会把「带 query 的完整路径」放进 request.path，而
    # dispatch 收到的是已剥掉 query 的路由路径。
    response = await router.dispatch(
        None, _get("/api/writing/articles?limit=10"), "/api/writing/articles",
    )

    assert response is not None
    assert response.status_code == 200
    body = json.loads(response.body)
    assert body == {"articles": [], "total": 0, "limit": 10, "offset": 0}


@pytest.mark.asyncio
async def test_router_serves_article_get_over_http(tmp_path: Path) -> None:
    """读动作可由 HTTP GET + Bearer token 调用（契约 §1 现有通道依据）。"""
    router = _router(tmp_path / "config.json", build_writing_operations(
        workspace_id="default", workspace_path=tmp_path,
    ))
    created = await router.dispatch(
        None,
        _mutation_request(
            "/api/writing/articles/create",
            {"client_request_id": "r-1", "title": "标题"},
        ),
        "/api/writing/articles/create",
    )
    article_id = json.loads(created.body)["article"]["id"]  # type: ignore[union-attr]

    response = await router.dispatch(
        None,
        _get(f"/api/writing/articles/get?id={article_id}"),
        "/api/writing/articles/get",
    )

    assert response is not None and response.status_code == 200
    assert json.loads(response.body)["article"]["id"] == article_id


@pytest.mark.asyncio
async def test_router_dispatches_article_write_over_websocket(tmp_path: Path) -> None:
    router = _router(tmp_path / "config.json", build_writing_operations(
        workspace_id="default", workspace_path=tmp_path,
    ))
    path = "/api/writing/articles/create"
    request = _mutation_request(path, {"client_request_id": "r-1", "title": "标题"})

    response = await router.dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body)["article"]["revision"] == 1
    assert (tmp_path / "writing" / "state.db").exists()


@pytest.mark.asyncio
async def test_writing_mutation_without_websocket_is_405(tmp_path: Path) -> None:
    """写路径必须被 WS gating 拦住，普通 HTTP 不能改文章。"""
    router = _router(tmp_path / "config.json", build_writing_operations(
        workspace_id="default", workspace_path=tmp_path,
    ))
    path = "/api/writing/articles/save"
    response = await router.dispatch(None, _get(path), path)

    assert response is not None
    assert response.status_code == 405


@pytest.mark.asyncio
async def test_router_without_operations_serves_503_not_500(tmp_path: Path) -> None:
    """没注入 operations 时走 null 兜底：可读的 503，而不是崩掉或 500。"""
    router = _router(tmp_path / "config.json", None)
    path = "/api/writing/articles"
    response = await router.dispatch(None, _get(path), path)

    assert response is not None
    assert response.status_code == 503
    assert json.loads(response.body) == {"error": "writing service is not configured"}


@pytest.mark.asyncio
async def test_router_conflict_error_is_plain_text_message(tmp_path: Path) -> None:
    """409 的 message 必须原样出现在错误体里，前端按契约正则解析。"""
    router = _router(tmp_path / "config.json", build_writing_operations(
        workspace_id="default", workspace_path=tmp_path,
    ))
    created = await router.dispatch(
        None,
        _mutation_request(
            "/api/writing/articles/create", {"client_request_id": "r-1", "title": "标题"},
        ),
        "/api/writing/articles/create",
    )
    article_id = json.loads(created.body)["article"]["id"]  # type: ignore[union-attr]

    save = {"client_request_id": "r-2", "article_id": article_id,
            "base_revision": 1, "snapshot": {"title": "标题", "body": "甲"}}
    first = await router.dispatch(
        None, _mutation_request("/api/writing/articles/save", save),
        "/api/writing/articles/save",
    )
    assert first is not None and first.status_code == 200

    # 内容不同的晚到保存（否则会命中 §5.1 步骤 2 的摘要短路，返回 200 no_change）。
    stale = dict(save, client_request_id="r-3",
                 snapshot={"title": "标题", "body": "乙"})
    conflict = await router.dispatch(
        None, _mutation_request("/api/writing/articles/save", stale),
        "/api/writing/articles/save",
    )
    assert conflict is not None
    assert conflict.status_code == 409
    assert json.loads(conflict.body)["error"] == (
        f"article_conflict current_revision=2 article_id={article_id}"
    )


@pytest.mark.asyncio
async def test_router_plain_get_on_write_path_is_405(tmp_path: Path) -> None:
    """契约 §5：四条写路径即使带合法 query 也不接受 HTTP 直连。"""
    router = _router(tmp_path / "config.json", build_writing_operations(
        workspace_id="default", workspace_path=tmp_path,
    ))
    for path in sorted(EXPECTED_WRITE_PATHS):
        response = await router.dispatch(None, _get(path), path)
        assert response is not None, path
        assert response.status_code == 405, path
        assert json.loads(response.body)["error"] == (
            "WebUI mutations require an authenticated WebSocket"
        )
