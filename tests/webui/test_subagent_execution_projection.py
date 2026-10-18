"""WU-03 普通子 Agent 执行实时帧与 hydrate 投影。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.loader import get_config_path
from nanobot.subagent_executions import ExecutionRepository
from nanobot.webui.http_utils import http_json_response
from nanobot.webui.outbound_projection import WebUIOutboundProjector
from nanobot.webui.outbound_wire import (
    SubagentExecutionWireEvent,
    encode_subagent_execution_event,
    encode_subagent_execution_snapshot,
)
from nanobot.webui.settings_routes import WebUISettingsRouter
from nanobot.webui.settings_services import WebUISettingsServices
from nanobot.webui.subagent_execution_routes import build_subagent_execution_operations


class _FakeTransport:
    def __init__(self, subscribers: int = 1) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.persistence: list[str] = []
        self.subscribers = [object()] * subscribers

    def webui_subscribers(self, chat_id: str) -> tuple[Any, ...]:
        return tuple(self.subscribers if chat_id == "chat-1" else ())

    async def send_payload(
        self,
        chat_id: str,
        payload: dict[str, Any],
        *,
        persistence: str,
        metadata: dict[str, Any] | None = None,
        turn_owner: str | None = None,
    ) -> None:
        self.payloads.append(dict(payload))
        self.persistence.append(persistence)


class _FakeSessionProjection:
    def hydration_events(self, session_key: str, chat_id: str) -> list[dict[str, Any]]:
        return []


def _event_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "invocationId": "00000000-0000-4000-8000-000000000001",
        "chatId": "chat-1",
        "parentTurnId": "turn-1",
        "parentToolCallId": "tool-1",
        "seq": 3,
        "occurredAt": "2026-10-18T00:00:00+00:00",
        "type": "execution.activity",
        "data": {
            "execution": {
                "invocationId": "00000000-0000-4000-8000-000000000001",
                "chatId": "chat-1",
                "parentTurnId": "turn-1",
                "parentToolCallId": "tool-1",
                "agentName": "研究员",
                "title": "查资料",
                "status": "running",
                "phase": "tools",
                "lastSeq": 3,
                "token": "SENSITIVE_TOKEN",
                "private_prompt": "SENSITIVE_PROMPT",
            },
            "activity": {
                "activityId": "activity-1",
                "kind": "tool",
                "toolName": "read_file",
                "status": "running",
                "label": "读取文件",
                "startedAt": "2026-10-18T00:00:00+00:00",
                "finishedAt": None,
                "output": "SENSITIVE_OUTPUT",
            },
        },
    }
    payload.update(overrides)
    return payload


def test_realtime_encoder_is_allowlisted_and_carries_outer_identity() -> None:
    frame = encode_subagent_execution_event(
        "chat-1", SubagentExecutionWireEvent(data=_event_payload()),
    )
    assert frame is not None
    assert frame["event"] == "subagent_execution_event"
    assert frame["type"] == "subagent_execution_event"
    assert frame["chat_id"] == "chat-1"
    assert frame["turn_id"] == "turn-1"
    serialized = json.dumps(frame, ensure_ascii=False)
    assert "SENSITIVE_TOKEN" not in serialized
    assert "SENSITIVE_PROMPT" not in serialized
    assert "SENSITIVE_OUTPUT" not in serialized
    assert "token" not in frame["data"]["data"]["execution"]
    assert "output" not in frame["data"]["data"]["activity"]


def test_unknown_schema_is_ignored_without_touching_the_main_stream() -> None:
    frame = encode_subagent_execution_event(
        "chat-1", SubagentExecutionWireEvent(data=_event_payload(schemaVersion=99)),
    )
    assert frame is None
    assert encode_subagent_execution_event(
        "chat-2", SubagentExecutionWireEvent(data=_event_payload()),
    ) is None


async def test_projector_delivers_realtime_event_only_to_subscribers() -> None:
    event = SubagentExecutionWireEvent(data=_event_payload())
    projector = WebUIOutboundProjector(  # pyright: ignore[reportArgumentType]
        _FakeTransport(subscribers=0), _FakeSessionProjection(),  # pyright: ignore[reportArgumentType]
    )
    await projector.send(OutboundMessage(
        channel="websocket", chat_id="chat-1", content="", event=event,
    ))
    assert projector._transport.payloads == []  # pyright: ignore[reportAttributeAccessIssue]

    connected = WebUIOutboundProjector(  # pyright: ignore[reportArgumentType]
        _FakeTransport(), _FakeSessionProjection(),  # pyright: ignore[reportArgumentType]
    )
    await connected.send(OutboundMessage(
        channel="websocket", chat_id="chat-1", content="", event=event,
    ))
    assert [item["event"] for item in connected._transport.payloads] == [  # pyright: ignore[reportAttributeAccessIssue]
        "subagent_execution_event"
    ]
    assert connected._transport.persistence == ["transient"]  # pyright: ignore[reportAttributeAccessIssue]


async def test_hydrate_replays_list_shaped_snapshot() -> None:
    snapshot = {
        "executions": [{"invocationId": "inv-1", "lastSeq": 4}],
        "nextCursor": None,
        "hasMore": False,
        "serverNow": "2026-10-18T00:00:00+00:00",
    }

    async def hydrate(chat_id: str) -> dict[str, Any]:
        assert chat_id == "chat-1"
        return snapshot

    transport = _FakeTransport()
    projector = WebUIOutboundProjector(  # pyright: ignore[reportArgumentType]
        transport, _FakeSessionProjection(),  # pyright: ignore[reportArgumentType]
        subagent_hydration=hydrate,
    )
    await projector.hydrate("chat-1")
    assert transport.payloads == [{
        "event": "subagent_execution_snapshot",
        "type": "subagent_execution_snapshot",
        "chat_id": "chat-1",
        "data": snapshot,
    }]
    assert transport.persistence == ["transient"]


def test_snapshot_encoder_shape() -> None:
    assert encode_subagent_execution_snapshot("chat-1", {"executions": []}) == {
        "event": "subagent_execution_snapshot",
        "type": "subagent_execution_snapshot",
        "chat_id": "chat-1",
        "data": {"executions": []},
    }


def test_capability_is_declared_only_when_operations_are_wired() -> None:
    repo = ExecutionRepository()
    try:
        declared: dict[str, Any] = {}
        WebUISettingsRouter(
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
            runtime_capabilities=declared,
            subagent_execution_operations=build_subagent_execution_operations(repo),
        )
        assert declared["subagentExecutionEventsV1"] is True
    finally:
        repo.close()

    undeclared: dict[str, Any] = {}
    WebUISettingsRouter(
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
        runtime_capabilities=undeclared,
    )
    assert "subagentExecutionEventsV1" not in undeclared
