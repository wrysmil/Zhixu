from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest
from websockets.datastructures import Headers

from nanobot.config.loader import get_config_path
from nanobot.webui.http_utils import http_json_response
from nanobot.webui.mcp_presets_api import custom_mcp_action
from nanobot.webui.settings_routes import WebUISettingsRouter
from nanobot.webui.settings_services import WebUISettingsServices
from nanobot.webui.teams_routes import TeamSettingsOperations


def _router(
    *,
    authorized: bool = True,
    config_path: Path | None = None,
    mcp_runtime_status: Callable[[], Mapping[str, str]] | None = None,
    mcp_reload: Callable[[], Awaitable[dict[str, object]]] | None = None,
    rename_model_preset: Callable[[str, str], int] | None = None,
    refresh_runtime_config: Callable[[], None] | None = None,
    team_operations: TeamSettingsOperations | None = None,
) -> WebUISettingsRouter:
    return WebUISettingsRouter(
        settings=WebUISettingsServices.create(
            config_path or get_config_path(),
            rename_model_preset=rename_model_preset,
            refresh_runtime_config=refresh_runtime_config,
        ),
        bus=SimpleNamespace(),
        logger=SimpleNamespace(exception=lambda *_args: None),
        check_api_token=lambda _request: authorized,
        parse_query=lambda path: parse_qs(urlsplit(path).query),
        json_response=http_json_response,
        error_response=lambda status, message: http_json_response(
            {"error": message},
            status=status,
        ),
        runtime_surface="browser",
        runtime_capabilities={},
        mcp_runtime_status=mcp_runtime_status,
        mcp_reload=mcp_reload,
        mcp_oauth_redirect_uri=lambda _request: "https://gateway.example/auth/mcp/callback",
        team_operations=team_operations,
    )


def _mutation_request(path: str, payload: dict[str, object]) -> SimpleNamespace:
    request = SimpleNamespace(path=path, headers=Headers())
    request._nanobot_webui_mutation_request = True
    request._nanobot_webui_mutation_payload = payload
    request._nanobot_trusted_proxy_authenticated = True
    return request


@pytest.mark.asyncio
async def test_mcp_list_serializes_local_runtime_failure_snapshot(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    custom_mcp_action(
        "custom",
        {
            "name": ["team-docs"],
            "transport": ["streamableHttp"],
            "url": ["https://mcp.example.com/mcp"],
        },
        config_path=config_path,
    )
    snapshot_calls = 0

    def runtime_snapshot() -> Mapping[str, str]:
        nonlocal snapshot_calls
        snapshot_calls += 1
        return {"team-docs": "failed"}

    router = _router(
        config_path=config_path,
        mcp_runtime_status=runtime_snapshot,
    )
    request = SimpleNamespace(
        path="/api/settings/mcp-presets",
        headers=Headers(),
    )

    response = await router.dispatch(None, request, "/api/settings/mcp-presets")

    assert response is not None
    assert response.status_code == 200
    payload = json.loads(response.body)
    row = next(item for item in payload["presets"] if item["name"] == "team-docs")
    assert row["status"] == "configured"
    assert row["runtime_status"] == "failed"
    assert b'"runtime_status": "failed"' in response.body
    assert snapshot_calls == 1


@pytest.mark.asyncio
async def test_usage_query_runs_off_the_event_loop(monkeypatch) -> None:
    calling_thread = threading.get_ident()
    worker_threads: list[int] = []

    def usage_payload(**_kwargs):
        worker_threads.append(threading.get_ident())
        return {"days": []}

    monkeypatch.setattr("nanobot.webui.settings_routes.settings_usage_payload", usage_payload)
    request = SimpleNamespace(path="/api/settings/usage", headers=Headers())

    response = await _router().dispatch(None, request, request.path)

    assert response is not None
    assert response.status_code == 200
    assert worker_threads and worker_threads[0] != calling_thread


@pytest.mark.asyncio
async def test_full_settings_query_runs_off_the_event_loop(monkeypatch) -> None:
    calling_thread = threading.get_ident()
    worker_threads: list[int] = []
    router = _router()

    def settings_response():
        worker_threads.append(threading.get_ident())
        return http_json_response({"ok": True})

    monkeypatch.setattr(router, "_handle_settings", settings_response)
    request = SimpleNamespace(path="/api/settings", headers=Headers())

    response = await router.dispatch(None, request, request.path)

    assert response is not None
    assert response.status_code == 200
    assert worker_threads and worker_threads[0] != calling_thread


@pytest.mark.asyncio
async def test_mcp_reload_callback_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def reload_mcp() -> dict[str, object]:
        started.set()
        await asyncio.Event().wait()
        return {"ok": True}

    monkeypatch.setattr(
        "nanobot.webui.settings_routes._MCP_RELOAD_TIMEOUT_SECONDS",
        0.01,
    )
    router = _router(mcp_reload=reload_mcp)

    result = await router._reload_mcp_runtime()

    assert started.is_set()
    assert result == {
        "ok": False,
        "message": "MCP hot reload timed out. Restart nanobot to pick up changes.",
        "requires_restart": True,
    }


@pytest.mark.asyncio
async def test_mcp_oauth_start_uses_gateway_callback_and_requires_api_auth(monkeypatch) -> None:
    config = SimpleNamespace(
        type="streamableHttp",
        auth="oauth",
        url="https://app.xmind.com/api/mcp",
    )
    monkeypatch.setattr(
        "nanobot.webui.settings_routes.ensure_mcp_oauth_server",
        lambda _query, *, config_path=None: ("xmind", config),
    )
    router = _router()
    start = AsyncMock(return_value={
        "status": "authorization_required",
        "flow_id": "flow-123",
        "name": "xmind",
        "authorization_url": "https://xmind.example/authorize?state=state-123",
    })
    router._mcp_oauth = SimpleNamespace(start=start)
    request = _mutation_request(
        "/api/settings/mcp-oauth/start",
        {"name": "xmind"},
    )

    response = await router.dispatch(None, request, "/api/settings/mcp-oauth/start")

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body)["flow_id"] == "flow-123"
    start.assert_awaited_once_with(
        "xmind",
        config,
        "https://gateway.example/auth/mcp/callback",
        reload_mcp=ANY,
        reset_credentials=False,
    )

    denied = _router(authorized=False)
    denied_response = await denied.dispatch(None, request, "/api/settings/mcp-oauth/start")
    assert denied_response is not None
    assert denied_response.status_code == 401

    failed = _router()
    failed._mcp_oauth = SimpleNamespace(
        start=AsyncMock(side_effect=RuntimeError("upstream secret response"))
    )
    failed_response = await failed.dispatch(None, request, "/api/settings/mcp-oauth/start")
    assert failed_response is not None
    assert failed_response.status_code == 500
    assert json.loads(failed_response.body) == {"error": "MCP OAuth start failed"}
    assert b"upstream secret response" not in failed_response.body


@pytest.mark.asyncio
async def test_mcp_oauth_callback_is_state_authenticated_and_returns_close_page() -> None:
    router = _router(authorized=False)
    submit = MagicMock(return_value="xmind")
    router._mcp_oauth = SimpleNamespace(submit_callback=submit)
    request = SimpleNamespace(
        path="/auth/mcp/callback?code=oauth-code&state=state-123",
        headers=Headers(),
    )

    response = await router.dispatch(None, request, "/auth/mcp/callback")

    assert response is not None
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/html; charset=utf-8"
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert b"window.close" in response.body
    assert b"Authorization received" in response.body
    assert b"oauth-code" not in response.body
    submit.assert_called_once_with(state="state-123", code="oauth-code", error=None)


@pytest.mark.asyncio
async def test_mcp_oauth_manual_completion_reads_websocket_payload() -> None:
    callback_url = (
        "http://127.0.0.1:8765/auth/mcp/callback?code=oauth-code&state=state-123"
    )
    router = _router()
    submit = MagicMock(
        return_value={
            "flow_id": "flow-123",
            "name": "linear",
            "status": "connecting",
            "expires_in": 299,
            "completion_input": "callback_url",
        }
    )
    router._mcp_oauth = SimpleNamespace(submit_callback_url=submit)
    request = _mutation_request(
        "/api/settings/mcp-oauth/complete",
        {"flow_id": "flow-123", "callback_url": callback_url},
    )

    response = await router.dispatch(None, request, "/api/settings/mcp-oauth/complete")

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body)["status"] == "connecting"
    assert b"oauth-code" not in response.body
    submit.assert_called_once_with(flow_id="flow-123", callback_url=callback_url)

    denied = _router(authorized=False)
    denied_response = await denied.dispatch(
        None,
        request,
        "/api/settings/mcp-oauth/complete",
    )
    assert denied_response is not None
    assert denied_response.status_code == 401


@pytest.mark.parametrize(
    ("provider", "authorization_response"),
    [
        ("xai_grok", "secret"),
        (
            "openai_codex",
            "http://localhost:1455/auth/callback?code=secret&state=test",
        ),
    ],
)
@pytest.mark.asyncio
async def test_oauth_completion_reads_websocket_payload(
    monkeypatch,
    provider: str,
    authorization_response: str,
) -> None:
    captured: dict[str, object] = {}

    def complete(
        query,
        authorization_response=None,
        *,
        oauth_flows=None,
        config_path=None,
    ):
        captured.update(query=query, authorization_response=authorization_response)
        return {
            "status": "pending",
            "provider": provider,
            "flow_id": "flow-123",
        }

    monkeypatch.setattr("nanobot.webui.settings_routes.complete_oauth_provider", complete)
    router = _router()
    request = _mutation_request(
        "/api/settings/provider/oauth-login/complete",
        {
            "provider": provider,
            "flow_id": "flow-123",
            "authorization_response": authorization_response,
        },
    )

    response = await router.dispatch(
        None,
        request,
        "/api/settings/provider/oauth-login/complete",
    )

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body) == {
        "status": "pending",
        "provider": provider,
        "flow_id": "flow-123",
    }
    assert captured == {
        "query": {"provider": [provider], "flow_id": ["flow-123"]},
        "authorization_response": authorization_response,
    }
    assert request.path == "/api/settings/provider/oauth-login/complete"
    assert not request.headers


@pytest.mark.parametrize(
    ("route_path", "function_name", "payload", "expected_query"),
    [
        (
            "/api/settings/update",
            "update_agent_settings",
            {"model_preset": "Codex"},
            {"model_preset": ["Codex"]},
        ),
        (
            "/api/settings/model-configurations/create",
            "create_model_configuration",
            {"name": "Codex", "model": "openai-codex/gpt-5.6"},
            {"name": ["Codex"], "model": ["openai-codex/gpt-5.6"]},
        ),
        (
            "/api/settings/model-configurations/delete",
            "delete_model_configuration",
            {"name": "spare"},
            {"name": ["spare"]},
        ),
        (
            "/api/settings/model-configurations/migrate",
            "migrate_model_configurations",
            {},
            {},
        ),
        (
            "/api/settings/model-call-order/update",
            "update_model_call_order",
            {"order": ["backup"]},
            {"order": ['["backup"]']},
        ),
        (
            "/api/settings/provider/create",
            "create_provider_settings",
            {"name": "team", "api_base": "https://llm.example/v1"},
            {"name": ["team"], "api_base": ["https://llm.example/v1"]},
        ),
        (
            "/api/settings/provider/update",
            "update_provider_settings",
            {"provider": "team", "api_base": "https://llm.example/v2"},
            {"provider": ["team"], "api_base": ["https://llm.example/v2"]},
        ),
    ],
)
@pytest.mark.asyncio
async def test_runtime_config_mutation_routes_refresh_live_runtime(
    monkeypatch,
    route_path: str,
    function_name: str,
    payload: dict[str, object],
    expected_query: dict[str, list[str]],
) -> None:
    captured: dict[str, object] = {}
    refresh_runtime_config = MagicMock()

    def mutate(query, *, config_path=None):
        captured["query"] = query
        return {"routed": function_name}

    monkeypatch.setattr(f"nanobot.webui.settings_routes.{function_name}", mutate)
    request = _mutation_request(route_path, payload)

    response = await _router(
        refresh_runtime_config=refresh_runtime_config,
    ).dispatch(None, request, route_path)

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body)["routed"] == function_name
    assert captured["query"] == expected_query
    refresh_runtime_config.assert_called_once_with()


@pytest.mark.asyncio
async def test_model_update_route_forwards_session_rename_dependency(monkeypatch) -> None:
    rename_model_preset = MagicMock(return_value=2)
    refresh_runtime_config = MagicMock()
    captured: dict[str, object] = {}

    def update(query, *, config_path=None, rename_model_preset=None):
        captured.update(query=query, rename_model_preset=rename_model_preset)
        return {"updated": True}

    monkeypatch.setattr("nanobot.webui.settings_routes.update_model_configuration", update)
    path = "/api/settings/model-configurations/update"
    request = _mutation_request(path, {"name": "openai", "new_name": "Codex"})

    response = await _router(
        rename_model_preset=rename_model_preset,
        refresh_runtime_config=refresh_runtime_config,
    ).dispatch(
        None,
        request,
        path,
    )

    assert response is not None
    assert response.status_code == 200
    assert captured == {
        "query": {"name": ["openai"], "new_name": ["Codex"]},
        "rename_model_preset": rename_model_preset,
    }
    refresh_runtime_config.assert_called_once_with()


# ------------------------------------------------------- 团队运行控制（契约 §5）


def _team_operations(
    calls: list[tuple[str, dict[str, object]]], **overrides: object
) -> TeamSettingsOperations:
    """只把控制三条接上真实现，其余留空返回——这里测的是路由与线程口径。"""

    def _sync(name: str) -> Callable[..., dict[str, object]]:
        def _inner(**kwargs: object) -> dict[str, object]:
            calls.append((name, kwargs))
            return {}

        return _inner

    async def _cancel_run(*, run_id: str) -> dict[str, object]:
        calls.append(("cancel_run", {"run_id": run_id}))
        return {"runId": run_id, "status": "cancelling"}

    async def _cancel_task(*, run_id: str, task_id: str) -> dict[str, object]:
        calls.append(("cancel_task", {"run_id": run_id, "task_id": task_id}))
        return {"taskId": task_id, "status": "cancelling"}

    async def _retry_run(*, run_id: str, request_id: str) -> dict[str, object]:
        calls.append(("retry_run", {"run_id": run_id, "request_id": request_id}))
        return {"newRunId": "run-2", "turnId": "retry:abc"}

    base = TeamSettingsOperations(
        list_teams=_sync("list_teams"),
        get_detail=_sync("get_detail"),
        list_revisions=_sync("list_revisions"),
        save_team=_sync("save_team"),
        archive_team=_sync("archive_team"),
        clone_team=_sync("clone_team"),
        list_runs=_sync("list_runs"),
        run_detail=_sync("run_detail"),
        run_events=_sync("run_events"),
        cancel_run=_cancel_run,
        cancel_task=_cancel_task,
        retry_run=_retry_run,
    )
    return replace(base, **overrides)  # pyright: ignore[reportArgumentType]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload", "expected_name", "expected_body"),
    [
        (
            "/api/team-runs/cancel",
            {"requestId": "r1", "runId": "run-1"},
            "cancel_run",
            {"runId": "run-1", "status": "cancelling"},
        ),
        (
            "/api/team-tasks/cancel",
            {"requestId": "r2", "runId": "run-1", "taskId": "task-1"},
            "cancel_task",
            {"taskId": "task-1", "status": "cancelling"},
        ),
        (
            "/api/team-runs/retry",
            {"requestId": "r3", "runId": "run-1"},
            "retry_run",
            {"newRunId": "run-2", "turnId": "retry:abc"},
        ),
    ],
)
async def test_team_control_mutations_reach_the_controller(
    path: str,
    payload: dict[str, object],
    expected_name: str,
    expected_body: dict[str, object],
) -> None:
    """控制动作必须由不包 ``to_thread`` 的分支送达：线程里叫不醒在场的运行。"""
    calls: list[tuple[str, dict[str, object]]] = []
    router = _router(team_operations=_team_operations(calls))

    response = await router.dispatch(None, _mutation_request(path, payload), path)

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body) == expected_body
    assert [name for name, _ in calls] == [expected_name]


@pytest.mark.asyncio
async def test_team_control_mutation_without_request_id_is_rejected() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    router = _router(team_operations=_team_operations(calls))
    path = "/api/team-runs/retry"

    response = await router.dispatch(None, _mutation_request(path, {"runId": "run-1"}), path)

    assert response is not None
    assert response.status_code == 400
    assert calls == []


@pytest.mark.asyncio
async def test_team_run_reads_are_dispatched_off_the_event_loop() -> None:
    """读走 ``to_thread``：阻塞 SQLite 不能占住事件循环。"""
    calling_thread = threading.get_ident()
    worker_threads: list[int] = []

    def list_runs(*, chat_id: str, limit: int, cursor: str | None) -> dict[str, object]:
        worker_threads.append(threading.get_ident())
        return {"runs": [], "nextCursor": None, "chatId": chat_id}

    router = _router(team_operations=_team_operations([], list_runs=list_runs))
    path = "/api/team-runs"
    request = SimpleNamespace(path=f"{path}?chatId=chat-1", headers=Headers())

    response = await router.dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body)["chatId"] == "chat-1"
    assert worker_threads and worker_threads[0] != calling_thread


@pytest.mark.asyncio
async def test_settings_get_mutation_route_is_method_not_allowed() -> None:
    path = "/api/settings/provider/update"
    request = SimpleNamespace(
        path=f"{path}?provider=openrouter&api_key=must-not-run",
        headers=Headers(),
    )

    response = await _router().dispatch(None, request, path)

    assert response is not None
    assert response.status_code == 405
    assert json.loads(response.body) == {
        "error": "WebUI mutations require an authenticated WebSocket"
    }


@pytest.mark.parametrize(
    ("update_info", "expected"),
    [
        (None, {"updateAvailable": None}),
        (
            {
                "currentVersion": "1.2.0",
                "latestVersion": "1.3.0",
                "pypiUrl": "https://pypi.org/project/nanobot-ai/",
            },
            {
                "updateAvailable": {
                    "currentVersion": "1.2.0",
                    "latestVersion": "1.3.0",
                    "pypiUrl": "https://pypi.org/project/nanobot-ai/",
                }
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_version_check_route_returns_stable_payload(
    monkeypatch: pytest.MonkeyPatch,
    update_info: dict[str, str] | None,
    expected: dict[str, object],
) -> None:
    monkeypatch.setattr(
        "nanobot.webui.settings_routes.check_for_update",
        lambda: update_info,
    )
    request = SimpleNamespace(path="/api/settings/version-check", headers=Headers())

    response = await _router().dispatch(None, request, request.path)

    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body) == expected


@pytest.mark.asyncio
async def test_version_check_route_enforces_auth_and_bounds_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check = MagicMock(side_effect=RuntimeError("upstream secret body"))
    monkeypatch.setattr("nanobot.webui.settings_routes.check_for_update", check)
    request = SimpleNamespace(path="/api/settings/version-check", headers=Headers())

    unauthorized = await _router(authorized=False).dispatch(None, request, request.path)
    assert unauthorized is not None
    assert unauthorized.status_code == 401
    check.assert_not_called()

    failed = await _router().dispatch(None, request, request.path)
    assert failed is not None
    assert failed.status_code == 500
    assert json.loads(failed.body) == {"error": "version check failed"}
    assert "upstream secret body" not in failed.body.decode()
