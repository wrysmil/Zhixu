"""Tests for the ask_question tool, question registry, and WebUI projection."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nanobot.agent.questions import (
    forget_question,
    pending_for_chat,
    register_question,
    resolve_answer,
)
from nanobot.agent.tools.ask_question import AskQuestionTool
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.bus.events import OutboundMessage
from nanobot.bus.outbound_events import QuestionRequestedEvent
from nanobot.webui.outbound_projection import WebUIOutboundProjector

_OPTIONS = [
    {"label": "Continue phase 2", "description": "Follow the spec order", "recommended": True},
    {"label": "Verify phase 1"},
]


def _ws_context() -> RequestContext:
    return RequestContext(channel="websocket", chat_id="chat-1", session_key="websocket:chat-1")


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[OutboundMessage] = []

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        self.published.append(msg)


async def _run_tool(bus: _FakeBus, **kwargs: Any) -> Any:
    tool = AskQuestionTool(publish_outbound=bus.publish_outbound)
    with request_context(_ws_context()):
        return asyncio.create_task(tool.execute(**kwargs))


async def test_non_interactive_channel_returns_error() -> None:
    bus = _FakeBus()
    tool = AskQuestionTool(publish_outbound=bus.publish_outbound)
    with request_context(RequestContext(channel="cli", chat_id="direct")):
        result = await tool.execute(question="Pick one", options=_OPTIONS)
    assert "interactive" in str(result)
    assert bus.published == []


async def test_execute_pushes_question_and_waits_for_answer() -> None:
    bus = _FakeBus()
    task = await _run_tool(bus, question="Where to focus?", options=_OPTIONS, header="Next step")
    await asyncio.sleep(0)
    assert len(bus.published) == 1
    msg = bus.published[0]
    assert msg.channel == "websocket" and msg.chat_id == "chat-1"
    assert isinstance(msg.event, QuestionRequestedEvent)
    payload = msg.event.question
    assert payload["question"] == "Where to focus?"
    assert payload["header"] == "Next step"
    assert payload["options"][0]["recommended"] is True
    assert pending_for_chat("chat-1")

    assert resolve_answer(payload["question_id"], "Verify phase 1") is True
    result = await task
    assert "Verify phase 1" in result
    assert pending_for_chat("chat-1") == []


async def test_second_recommended_option_dropped() -> None:
    bus = _FakeBus()
    options = [
        {"label": "A", "recommended": True},
        {"label": "B", "recommended": True},
        {"label": "C"},
    ]
    task = await _run_tool(bus, question="Q?", options=options)
    await asyncio.sleep(0)
    payload = bus.published[0].event.question  # type: ignore[union-attr]
    assert payload["options"][0].get("recommended") is True
    assert "recommended" not in payload["options"][1]
    resolve_answer(payload["question_id"], "A")
    await task


async def test_single_option_rejected() -> None:
    bus = _FakeBus()
    tool = AskQuestionTool(publish_outbound=bus.publish_outbound)
    with request_context(_ws_context()):
        result = await tool.execute(question="Q?", options=[{"label": "only"}])
    assert "at least 2" in str(result)
    assert bus.published == []


async def test_timeout_forgets_question(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nanobot.agent.tools.ask_question._ANSWER_TIMEOUT_SECONDS", 0.05)
    bus = _FakeBus()
    task = await _run_tool(bus, question="Q?", options=_OPTIONS)
    result = await task
    assert "did not answer" in str(result)
    assert pending_for_chat("chat-1") == []


async def test_forget_cancels_future() -> None:
    pending = register_question(
        channel="websocket",
        chat_id="chat-x",
        session_key=None,
        payload={"question_id": "q1", "question": "Q?", "options": _OPTIONS},
    )
    forget_question("q1")
    assert pending.future.cancelled()
    assert resolve_answer("q1", "late") is False


class _FakeTransport:
    def __init__(self) -> None:
        self.questions: list[tuple[str, dict[str, Any]]] = []
        self.subscribers: dict[str, list[str]] = {}

    def webui_subscribers(self, chat_id: str) -> tuple[Any, ...]:
        return tuple(self.subscribers.get(chat_id, ()))

    async def send_question_requested(self, chat_id: str, question: dict[str, Any]) -> None:
        self.questions.append((chat_id, question))


class _FakeSessionProjection:
    def hydration_events(self, session_key: str, chat_id: str) -> list[dict[str, Any]]:
        return []


async def test_projector_sends_question_and_hydrates_pending() -> None:
    transport = _FakeTransport()
    projector = WebUIOutboundProjector(transport, _FakeSessionProjection())  # type: ignore[arg-type]
    payload = {"question_id": "q9", "question": "Q?", "options": _OPTIONS}
    register_question(
        channel="websocket",
        chat_id="chat-h",
        session_key=None,
        payload=payload,
    )
    try:
        await projector.send(
            OutboundMessage(
                channel="websocket",
                chat_id="chat-1",
                content="",
                event=QuestionRequestedEvent(question=payload),
            )
        )
        assert transport.questions == []  # no subscribers -> dropped silently
        transport.subscribers["chat-1"] = ["conn"]
        await projector.send(
            OutboundMessage(
                channel="websocket",
                chat_id="chat-1",
                content="",
                event=QuestionRequestedEvent(question=payload),
            )
        )
        assert transport.questions[0][0] == "chat-1"
        transport.questions.clear()
        await projector.hydrate("chat-h")
        assert transport.questions[0][1]["question_id"] == "q9"
    finally:
        forget_question("q9")
