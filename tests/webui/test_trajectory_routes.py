"""轨迹只读路由契约测试。"""

from __future__ import annotations

from typing import Any

from nanobot.trajectory.context import bind_trace_recorder, reset_trace_recorder
from nanobot.trajectory.service import TrajectoryService, TrajectorySettings
from nanobot.webui.settings_contracts import SettingsRequest
from nanobot.webui.settings_routes import WebUISettingsRouter
from nanobot.webui.trajectory_routes import (
    TRAJECTORY_ACTION_NAMES,
    TrajectorySettingsHandler,
    build_trajectory_operations,
    match_route,
)


def _request(
    *, query: dict[str, Any] | None = None, caller_chats: tuple[str, ...] = (),
) -> SettingsRequest:
    return SettingsRequest(
        query={key: [str(value)] for key, value in (query or {}).items()},
        caller_chats=caller_chats,
    )


def _seed(tmp_path) -> TrajectoryService:
    service = TrajectoryService(TrajectorySettings(mode="standard"))
    recorder = service.begin_run(
        workspace=str(tmp_path), session_id="websocket:chat-1", turn_id="turn-1",
    )
    assert recorder is not None
    token = bind_trace_recorder(recorder)
    try:
        recorder.input_recorded("hello")
        recorder.step_started(0)
        recorder.model_requested("req-1", request_snapshot={"system": "s"})
        recorder.attempt_started("call-1", attempt=1, provider="p", model="m",
                                 logical_request_id="req-1")
        recorder.attempt_finished(
            "call-1", status="succeeded", finish_reason="stop",
            blocks=[("text", "world")], provider="p", model="m",
        )
        recorder.finish()
    finally:
        reset_trace_recorder(token)
    assert service.flush_all(timeout=5.0)
    return service


def _handler(service: TrajectoryService, workspace: str) -> TrajectorySettingsHandler:
    return TrajectorySettingsHandler(build_trajectory_operations(
        service, lambda _session_id: workspace,
    ))


def test_routes_are_frozen_and_registered() -> None:
    assert TRAJECTORY_ACTION_NAMES == frozenset({
        "trajectory-session",
        "trajectory-records",
        "trajectory-events",
        "trajectory-span",
        "trajectory-body",
    })
    assert match_route("/api/trajectory/sessions/chat-1") == (
        "trajectory-session", {"sessionId": "chat-1"},
    )
    assert match_route("/api/trajectory/sessions/chat-1/records") == (
        "trajectory-records", {"sessionId": "chat-1"},
    )
    assert match_route("/api/trajectory/sessions/chat-1/events") == (
        "trajectory-events", {"sessionId": "chat-1"},
    )
    assert match_route("/api/trajectory/sessions/chat-1/spans/req-1") == (
        "trajectory-span", {"sessionId": "chat-1", "spanId": "req-1"},
    )
    assert match_route("/api/trajectory/sessions/chat-1/bodies/b1") == (
        "trajectory-body", {"sessionId": "chat-1", "bodyId": "b1"},
    )
    assert match_route("/api/trajectory/sessions/chat-1/nope") is None
    assert WebUISettingsRouter._route("/api/trajectory/sessions/chat-1") == (
        "trajectory", "trajectory-session",
    )


def test_handler_returns_summary_records_and_events(tmp_path) -> None:
    service = _seed(tmp_path)
    handler = _handler(service, str(tmp_path))
    session_id = "websocket:chat-1"

    summary = handler.handle(
        "trajectory-session", _request(caller_chats=("chat-1",)),
        params={"sessionId": session_id},
    )
    assert summary.status == 200 and summary.payload is not None
    assert summary.payload["sessionId"] == session_id
    assert summary.payload["durableSeq"] > 0

    records = handler.handle(
        "trajectory-records", _request(caller_chats=("chat-1",)),
        params={"sessionId": session_id},
    )
    assert records.status == 200 and records.payload is not None
    assert [record["kind"] for record in records.payload["records"]] == [
        "user", "assistant",
    ]

    events = handler.handle(
        "trajectory-events", _request(query={"afterSeq": 0}, caller_chats=("chat-1",)),
        params={"sessionId": session_id},
    )
    assert events.status == 200 and events.payload is not None
    assert events.payload["events"]

    span = handler.handle(
        "trajectory-span", _request(caller_chats=("chat-1",)),
        params={"sessionId": session_id, "spanId": "req-1"},
    )
    assert span.status == 200 and span.payload is not None
    assert span.payload["record"]["recordId"] == "assistant:req-1"

    assistant_body = records.payload["records"][1]["blockSummaries"][0]["content"]["bodyId"]
    body = handler.handle(
        "trajectory-body", _request(caller_chats=("chat-1",)),
        params={"sessionId": session_id, "bodyId": assistant_body},
    )
    assert body.status == 200 and body.payload is not None
    assert body.payload["text"] == "world"


def test_handler_hides_other_sessions_and_rejects_bad_limit(tmp_path) -> None:
    service = _seed(tmp_path)
    handler = _handler(service, str(tmp_path))
    hidden = handler.handle(
        "trajectory-session", _request(caller_chats=("other-chat",)),
        params={"sessionId": "websocket:chat-1"},
    )
    assert hidden.status == 404
    assert hidden.error is not None and hidden.error.startswith("TRAJECTORY_NOT_FOUND")

    bad_limit = handler.handle(
        "trajectory-records", _request(query={"limit": 900}, caller_chats=("chat-1",)),
        params={"sessionId": "websocket:chat-1"},
    )
    assert bad_limit.status == 400
    assert bad_limit.error is not None
    assert bad_limit.error.startswith("TRAJECTORY_INVALID_CURSOR")

    missing = handler.handle(
        "trajectory-records", _request(caller_chats=("missing",)),
        params={"sessionId": "websocket:missing"},
    )
    assert missing.status == 200  # same workspace store exists; empty session page
    assert missing.payload is not None and missing.payload["records"] == []
