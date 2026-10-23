"""Agent 档案域的接线检查（WU-06）。

``test_agents_routes.py`` 证明 handler 本身正确，本文件证明它*外面*的三张表
对得上：读路径表、写路径白名单、WebSocket action 翻译表，外加 gateway 的绑定。

这三张表漏一条都不会在 import 期报错：路径漏登记 404，写路径漏进白名单则
未认证的普通 GET 就能改磁盘，WS action 漏登记则前端提交时报 404。
"""

from __future__ import annotations

import inspect
import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Response

from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import Config
from nanobot.webui import agents_api
from nanobot.webui.agents_routes import (
    AGENTS_ACTION_NAMES,
    AgentSettingsHandler,
    AgentSettingsOperations,
)
from nanobot.webui.gateway_services import build_agents_operations, build_gateway_services
from nanobot.webui.http_utils import http_json_response
from nanobot.webui.settings_contracts import SettingsRequest
from nanobot.webui.settings_routes import (
    _AGENTS_MUTATION_PATHS,
    _SETTINGS_MUTATION_PATHS,
    _SYSTEM_ROUTES,
    WebUISettingsRouter,
    _null_agents_operations,
)
from nanobot.webui.settings_services import WebUISettingsServices
from nanobot.webui.ws_http import _WEBUI_MUTATION_PATHS, GatewayHTTPHandler

# 契约 §9.1：读走 HTTP GET，路径 → action 逐字固定。
EXPECTED_READ_ROUTES = {
    "/api/settings/agents": "agents-list",
    "/api/settings/agents/catalog": "agents-catalog",
}

# 契约 §9.2：写走 WebSocket mutation，四条固定。
EXPECTED_WRITE_PATHS = {
    "/api/settings/agents/save",
    "/api/settings/agents/delete",
    "/api/settings/agents/reset",
    "/api/settings/agents/visibility",
}

# 契约 §9.2：前端 action → HTTP 路径。
EXPECTED_WS_ACTIONS = {
    "agents.save": "/api/settings/agents/save",
    "agents.delete": "/api/settings/agents/delete",
    "agents.reset": "/api/settings/agents/reset",
    "agents.visibility": "/api/settings/agents/visibility",
}

# 读写都要能在 _SYSTEM_ROUTES 里查到：WS mutation 翻译出的路径最终仍要
# 经 _route() 落到 handler，漏登记就 404。
EXPECTED_ROUTE_ACTIONS = {**EXPECTED_READ_ROUTES, **{p: f"agents-{n}" for p, n in (
    ("/api/settings/agents/save", "save"),
    ("/api/settings/agents/delete", "delete"),
    ("/api/settings/agents/reset", "reset"),
    ("/api/settings/agents/visibility", "visibility"),
)}}


# ---- R1: _SYSTEM_ROUTES（读路径 + 写路径落地）------------------------------


@pytest.mark.parametrize("path,action", sorted(EXPECTED_ROUTE_ACTIONS.items()))
def test_routes_are_registered(path: str, action: str) -> None:
    assert _SYSTEM_ROUTES.get(path) == action
    assert WebUISettingsRouter._route(path) == ("system", action)


def test_system_routes_has_exactly_the_expected_agents_entries() -> None:
    assert {
        path for path in _SYSTEM_ROUTES if "/agents" in path
    } == set(EXPECTED_ROUTE_ACTIONS)


def test_reads_are_not_mutations() -> None:
    """读路径走普通 GET；误登记会让整个 agents 页只能从 WS 调用。"""
    for path in EXPECTED_READ_ROUTES:
        assert path not in _SETTINGS_MUTATION_PATHS
        assert not WebUISettingsRouter.is_mutation_path(path)


# ---- R2: _SETTINGS_MUTATION_PATHS（写路径门禁）-----------------------------


@pytest.mark.parametrize("path", sorted(EXPECTED_WRITE_PATHS))
def test_write_paths_require_authenticated_websocket(path: str) -> None:
    assert path in _SETTINGS_MUTATION_PATHS
    assert WebUISettingsRouter.is_mutation_path(path)


def test_agents_mutation_set_matches_registered_writes() -> None:
    assert _AGENTS_MUTATION_PATHS == EXPECTED_WRITE_PATHS


# ---- R3: _WEBUI_MUTATION_PATHS（WS action → HTTP 路径）---------------------


@pytest.mark.parametrize("action,path", sorted(EXPECTED_WS_ACTIONS.items()))
def test_ws_action_maps_to_http_path(action: str, path: str) -> None:
    assert _WEBUI_MUTATION_PATHS.get(action) == path
    assert GatewayHTTPHandler._webui_mutation_path(action, {}) == path


def test_ws_action_names_match_frontend_contract() -> None:
    assert {a for a in _WEBUI_MUTATION_PATHS if a.startswith("agents.")} == set(
        EXPECTED_WS_ACTIONS
    )


def test_unregistered_ws_action_is_404() -> None:
    result = GatewayHTTPHandler._webui_mutation_path("agents.frobnicate", {})
    assert isinstance(result, Response)
    assert result.status_code == 404


def test_ws_mapped_paths_are_known_mutations() -> None:
    """翻译表与 mutation 名单必须对得上，否则跳到目标路径会被 405 弹回。"""
    for path in EXPECTED_WS_ACTIONS.values():
        assert WebUISettingsRouter.is_mutation_path(path)


# ---- R4: action 名与 handler 对齐 ------------------------------------------


def test_registered_actions_all_belong_to_the_domain() -> None:
    """路由表里出现的 action 必须是 AGENTS_ACTION_NAMES 的成员，否则会掉进
    system handler 而不是 agents handler。"""
    assert set(EXPECTED_ROUTE_ACTIONS.values()) == AGENTS_ACTION_NAMES


# ---- R5: ToolRegistry.names() ----------------------------------------------


def test_registry_names_is_empty_before_registration() -> None:
    assert ToolRegistry().names() == []


def test_registry_names_is_sorted_and_tracks_registration() -> None:
    registry = ToolRegistry()
    tool = SimpleNamespace(name="web_fetch")
    registry.register(tool)  # type: ignore[arg-type]
    registry.register(SimpleNamespace(name="exec"))  # type: ignore[arg-type]
    assert registry.names() == ["exec", "web_fetch"]
    # 纯新增的只读方法：既有的 tool_names 语义不能被改动。
    assert sorted(registry.tool_names) == registry.names()


# ---- R6: gateway 绑定 -------------------------------------------------------


def test_build_agents_operations_binds_the_real_api(tmp_path: Path) -> None:
    ops = build_agents_operations(workspace_path=tmp_path, load_config=Config)

    payload = ops.list_profiles()
    assert "agents" in payload
    # 出厂预置只进内存，读一次列表不产生任何磁盘文件。
    assert not (tmp_path / "agents").exists()


def test_build_agents_operations_wires_the_catalog(tmp_path: Path) -> None:
    """catalog 走注入的零参 ``load_config``；没注入时回落进程级配置路径。"""
    ops = build_agents_operations(workspace_path=tmp_path, load_config=Config)

    catalog = ops.list_catalog()

    assert set(catalog) == {"tools", "skills", "models", "mcpServers", "categories"}
    assert "exec" in {tool["name"] for tool in catalog["tools"]}
    # 读目录也不能顺手建目录。
    assert not (tmp_path / "agents").exists()


def test_build_agents_operations_writes_through_to_the_workspace(tmp_path: Path) -> None:
    ops = build_agents_operations(workspace_path=tmp_path)

    saved = ops.save_profile({"id": "my-agent", "name": "我的助理", "type": "custom"})

    assert saved["agent"]["id"] == "my-agent"
    assert (tmp_path / "agents" / "profiles" / "my-agent.json").exists()


def test_gateway_services_wires_agents_operations() -> None:
    """build_gateway_services 必须把 agents_operations 传进 HTTP handler。"""
    assert "agents_operations=" in inspect.getsource(build_gateway_services)


def test_gateway_http_handler_forwards_agents_operations() -> None:
    assert "agents_operations=self.agents_operations" in inspect.getsource(
        GatewayHTTPHandler
    )


# ---- R7: null 兜底 ----------------------------------------------------------


def test_null_agents_operations_returns_503_for_every_action() -> None:
    handler = AgentSettingsHandler(_null_agents_operations())
    # 输入必须合法，否则 dispatch 会先在参数校验上返回 400，碰不到 operations。
    cases = [
        ("agents-list", SettingsRequest(query={})),
        ("agents-catalog", SettingsRequest(query={})),
        ("agents-save", SettingsRequest(query={}, payload={"agent": {"id": "a"}})),
        ("agents-delete", SettingsRequest(query={}, payload={"id": "a"})),
        ("agents-reset", SettingsRequest(query={}, payload={"id": "a"})),
        (
            "agents-visibility",
            SettingsRequest(query={}, payload={"id": "a", "hidden": True}),
        ),
    ]
    for action, request in cases:
        result = handler.handle(action, request)
        assert result.status == 503, action
        assert result.error == "agent service is not configured"
        assert result.payload is None


# ---- R8: router 端到端 ------------------------------------------------------

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


def _router(config_path: Path, operations: AgentSettingsOperations | None):
    return WebUISettingsRouter(
        settings=WebUISettingsServices.create(config_path),
        agents_operations=operations,
        **WS_BASE_ARGS,
    )


def _operations(workspace: Path) -> AgentSettingsOperations:
    return AgentSettingsOperations(
        list_profiles=partial(agents_api.agents_list, workspace),
        save_profile=partial(agents_api.agents_save, workspace),
        delete_profile=partial(agents_api.agents_delete, workspace),
        reset_profile=partial(agents_api.agents_reset, workspace),
        set_visibility=partial(agents_api.agents_visibility, workspace),
    )


def _mutation_request(path: str, payload: dict[str, Any]) -> SimpleNamespace:
    request = SimpleNamespace(path=path, headers=Headers())
    request._nanobot_webui_mutation_request = True
    request._nanobot_webui_mutation_payload = payload
    request._nanobot_trusted_proxy_authenticated = True
    return request


@pytest.mark.asyncio
async def test_router_serves_agents_list_over_get(tmp_path: Path) -> None:
    router = _router(tmp_path / "config.json", _operations(tmp_path))
    path = "/api/settings/agents"
    request = SimpleNamespace(path=path, headers=Headers())

    response = await router.dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 200
    body = json.loads(response.body)
    # 写作团队五档案（契约 §7）已并入出厂目录，列表接口如实多返回五项。
    assert sorted(a["id"] for a in body["agents"]) == [
        "code-reviewer",
        "commander",
        "general-assistant",
        "ops-runner",
        "researcher",
        "writing-architect",
        "writing-author",
        "writing-editor",
        "writing-researcher",
        "writing-reviewer",
    ]


@pytest.mark.asyncio
async def test_router_dispatches_agents_write_over_websocket(tmp_path: Path) -> None:
    router = _router(tmp_path / "config.json", _operations(tmp_path))
    path = "/api/settings/agents/save"
    request = _mutation_request(path, {"agent": {"id": "my-agent", "name": "我的助理"}})

    response = await router.dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body)["agent"]["id"] == "my-agent"
    assert (tmp_path / "agents" / "profiles" / "my-agent.json").exists()


@pytest.mark.asyncio
async def test_agents_mutation_without_websocket_is_405(tmp_path: Path) -> None:
    """写路径必须被 WS gating 拦住，普通 HTTP 不能改档案。"""
    router = _router(tmp_path / "config.json", _operations(tmp_path))
    path = "/api/settings/agents/save"
    request = SimpleNamespace(path=path, headers=Headers())

    response = await router.dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 405


@pytest.mark.asyncio
async def test_router_without_operations_serves_503_not_500(tmp_path: Path) -> None:
    """没注入 operations 时走 null 兜底：可读的 503，而不是崩掉或 500。"""
    router = _router(tmp_path / "config.json", None)
    path = "/api/settings/agents"
    request = SimpleNamespace(path=path, headers=Headers())

    response = await router.dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 503
    assert json.loads(response.body) == {"error": "agent service is not configured"}
