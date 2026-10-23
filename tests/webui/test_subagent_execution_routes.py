"""WU-03 普通子 Agent 执行只读查询的路由契约。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from websockets.datastructures import Headers

from nanobot.bus.queue import MessageBus
from nanobot.config.loader import get_config_path
from nanobot.subagent_executions import ExecutionRepository, WriterBusy
from nanobot.webui.http_utils import http_json_response
from nanobot.webui.settings_contracts import SettingsRequest
from nanobot.webui.settings_routes import WebUISettingsRouter
from nanobot.webui.settings_services import WebUISettingsServices
from nanobot.webui.subagent_execution_routes import (
    SUBAGENT_EXECUTION_ACTION_NAMES,
    SubagentExecutionSettingsHandler,
    SubagentExecutionSettingsOperations,
    build_subagent_execution_operations,
    match_route,
)


def _request(
    *,
    query: dict[str, Any] | None = None,
    caller_chats: tuple[str, ...] = (),
) -> SettingsRequest:
    return SettingsRequest(
        query={key: [str(value)] for key, value in (query or {}).items()},
        caller_chats=caller_chats,
    )


def _create(repo: ExecutionRepository, chat_id: str = "chat-1"):
    return repo.create(
        chat_id=chat_id,
        parent_turn_id="turn-1",
        parent_tool_call_id="tool-1",
        agent_id=None,
        name="研究员",
        title="查资料",
        mode="background",
    )


def _handler(
    repo: ExecutionRepository | None = None,
    *,
    operations: SubagentExecutionSettingsOperations | None = None,
) -> SubagentExecutionSettingsHandler:
    return SubagentExecutionSettingsHandler(
        operations if operations is not None else build_subagent_execution_operations(repo)
    )


def test_action_names_and_dynamic_routes_are_frozen() -> None:
    assert SUBAGENT_EXECUTION_ACTION_NAMES == frozenset({
        "subagent-executions-list",
        "subagent-executions-detail",
        "subagent-executions-events",
    })
    assert match_route("/api/subagent-executions") == (
        "subagent-executions-list", None,
    )
    assert match_route("/api/subagent-executions/inv-1") == (
        "subagent-executions-detail", "inv-1",
    )
    assert match_route("/api/subagent-executions/inv-1/events") == (
        "subagent-executions-events", "inv-1",
    )
    assert match_route("/api/subagent-executions/inv-1/nope") is None


def test_agent_loop_injects_workspace_execution_ledger_for_websocket(tmp_path) -> None:
    from types import SimpleNamespace

    from nanobot.agent.loop import AgentLoop

    ledger = AgentLoop._build_execution_ledger(
        tmp_path,
        SimpleNamespace(websocket=SimpleNamespace(enabled=True)),
    )
    try:
        assert ledger is not None
        assert ledger.db_path == tmp_path / "runtime" / "agent-executions.db"
    finally:
        if ledger is not None:
            ledger.close()


def test_list_without_a_ledger_is_empty_and_does_not_create_storage() -> None:
    result = _handler(None).handle(
        "subagent-executions-list",
        _request(query={"chatId": "chat-1"}),
    )
    assert result.status == 200
    assert result.payload is not None
    assert set(result.payload) == {"executions", "nextCursor", "serverNow"}
    assert result.payload["executions"] == []
    assert result.payload["nextCursor"] is None
    assert result.payload["serverNow"]


def test_list_validation_and_real_repository_pagination() -> None:
    repo = ExecutionRepository()
    try:
        first = _create(repo)
        second = _create(repo)
        repo.finish(first.invocation_id, "succeeded")
        repo.finish(second.invocation_id, "succeeded")

        missing = _handler(repo).handle("subagent-executions-list", _request())
        assert missing.status == 400

        bad_limit = _handler(repo).handle(
            "subagent-executions-list",
            _request(query={"chatId": "chat-1", "limit": "101"}),
        )
        assert bad_limit.status == 400

        bad_cursor = _handler(repo).handle(
            "subagent-executions-list",
            _request(query={"chatId": "chat-1", "cursor": "not-a-cursor"}),
        )
        assert bad_cursor.status == 400

        page = _handler(repo).handle(
            "subagent-executions-list",
            _request(query={"chatId": "chat-1", "limit": "1"}),
        )
        assert page.status == 200
        assert page.payload is not None
        assert set(page.payload) == {"executions", "nextCursor", "serverNow"}
        assert len(page.payload["executions"]) == 1
        assert page.payload["nextCursor"]

        next_page = _handler(repo).handle(
            "subagent-executions-list",
            _request(query={
                "chatId": "chat-1",
                "limit": "1",
                "cursor": page.payload["nextCursor"],
            }),
        )
        assert next_page.status == 200
        assert next_page.payload is not None
        assert len(next_page.payload["executions"]) == 1
    finally:
        repo.close()


def test_detail_and_events_use_invocation_scoped_storage() -> None:
    repo = ExecutionRepository()
    try:
        item = _create(repo)
        repo.start(item.invocation_id)
        activity = repo.start_activity(item.invocation_id, "read_file")
        repo.finish_activity(item.invocation_id, activity.activity_id)
        repo.finish(item.invocation_id, "succeeded", result={"summary": "完成"})

        detail = _handler(repo).handle(
            "subagent-executions-detail",
            _request(),
            invocation_id=item.invocation_id,
        )
        assert detail.status == 200
        assert detail.payload is not None
        assert detail.payload["execution"]["invocationId"] == item.invocation_id
        assert detail.payload["execution"]["result"]["summary"] == "完成"
        assert detail.payload["serverNow"]

        events = _handler(repo).handle(
            "subagent-executions-events",
            _request(query={"afterSeq": "1", "throughSeq": "3", "limit": "2"}),
            invocation_id=item.invocation_id,
        )
        assert events.status == 200
        assert events.payload is not None
        assert [event["seq"] for event in events.payload["events"]] == [2, 3]
        assert events.payload["hasMore"] is False
    finally:
        repo.close()


def test_invalid_route_and_event_parameters_are_rejected() -> None:
    handler = _handler(ExecutionRepository())
    bad_id = handler.handle(
        "subagent-executions-detail",
        _request(),
        invocation_id="not-a-uuid",
    )
    assert bad_id.status == 400
    bad_after = handler.handle(
        "subagent-executions-events",
        _request(query={"afterSeq": "-1"}),
        invocation_id="00000000-0000-4000-8000-000000000001",
    )
    assert bad_after.status == 400
    bad_through = handler.handle(
        "subagent-executions-events",
        _request(query={"throughSeq": "x"}),
        invocation_id="00000000-0000-4000-8000-000000000001",
    )
    assert bad_through.status == 400
    bad_event_limit = handler.handle(
        "subagent-executions-events",
        _request(query={"limit": "201"}),
        invocation_id="00000000-0000-4000-8000-000000000001",
    )
    assert bad_event_limit.status == 400


def test_scope_and_error_prefixes_follow_contract() -> None:
    repo = ExecutionRepository()
    try:
        item = _create(repo)
        forbidden = _handler(repo).handle(
            "subagent-executions-detail",
            _request(caller_chats=("chat-other",)),
            invocation_id=item.invocation_id,
        )
        assert forbidden.status == 403
        assert (forbidden.error or "").startswith("EXECUTION_NOT_FOUND")
    finally:
        repo.close()

    def _boom(**_kwargs: Any) -> dict[str, Any]:
        raise WriterBusy("workspace writer already held")

    busy = SubagentExecutionSettingsOperations(
        list_executions=_boom,
        get_execution=_boom,
        list_events=_boom,
    )
    result = _handler(operations=busy).handle(
        "subagent-executions-list",
        _request(query={"chatId": "chat-1"}),
    )
    assert result.status == 503
    assert (result.error or "").startswith("EXECUTION_WRITER_BUSY")

    def _storage(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("sqlite unavailable")

    storage = SubagentExecutionSettingsOperations(
        list_executions=_storage,
        get_execution=_storage,
        list_events=_storage,
    )
    unavailable = _handler(operations=storage).handle(
        "subagent-executions-list",
        _request(query={"chatId": "chat-1"}),
    )
    assert unavailable.status == 503
    assert (unavailable.error or "").startswith("EXECUTION_STORAGE_UNAVAILABLE")
    assert "sqlite unavailable" not in (unavailable.error or "")

    not_found = _handler(ExecutionRepository()).handle(
        "subagent-executions-detail",
        _request(),
        invocation_id="00000000-0000-4000-8000-000000000001",
    )
    assert not_found.status == 404
    assert (not_found.error or "").startswith("EXECUTION_NOT_FOUND")


def test_settings_router_capability() -> None:
    repo = ExecutionRepository()
    try:
        capabilities: dict[str, Any] = {}
        WebUISettingsRouter(
            settings=WebUISettingsServices.create(get_config_path()),
            bus=MessageBus(),
            logger=SimpleNamespace(exception=lambda *_args: None),
            check_api_token=lambda _request: False,
            parse_query=lambda _path: {},
            json_response=http_json_response,
            error_response=lambda status, message: http_json_response(
                {"error": message}, status=status,
            ),
            runtime_surface="browser",
            runtime_capabilities=capabilities,
            subagent_execution_operations=build_subagent_execution_operations(repo),
        )
    finally:
        repo.close()

    # Capability is true only on the new gateway wiring.
    assert capabilities.get("subagentExecutionEventsV1") is True


@pytest.mark.asyncio
async def test_settings_router_rejects_unauthorized_and_non_get() -> None:
    operations = build_subagent_execution_operations(None)
    router = WebUISettingsRouter(
        settings=WebUISettingsServices.create(get_config_path()),
        bus=MessageBus(),
        logger=SimpleNamespace(exception=lambda *_args: None),
        check_api_token=lambda _request: False,
        parse_query=lambda _path: {},
        json_response=http_json_response,
        error_response=lambda status, message: http_json_response(
            {"error": message}, status=status,
        ),
        runtime_surface="browser",
        runtime_capabilities={},
        subagent_execution_operations=operations,
    )
    request = SimpleNamespace(
        path="/api/subagent-executions",
        method="GET",
        headers=Headers(),
    )
    response = await router.dispatch(None, request, request.path)
    assert response is not None and response.status_code == 401

    authorized = WebUISettingsRouter(
        settings=WebUISettingsServices.create(get_config_path()),
        bus=MessageBus(),
        logger=SimpleNamespace(exception=lambda *_args: None),
        check_api_token=lambda _request: True,
        parse_query=lambda _path: {},
        json_response=http_json_response,
        error_response=lambda status, message: http_json_response(
            {"error": message}, status=status,
        ),
        runtime_surface="browser",
        runtime_capabilities={},
        subagent_execution_operations=operations,
    )
    request.method = "POST"
    response = await authorized.dispatch(None, request, request.path)
    assert response is not None and response.status_code == 405
