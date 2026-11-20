from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage


def test_assemble_outbound_carries_trace_id() -> None:
    loop = object.__new__(AgentLoop)
    msg = InboundMessage(channel="websocket", chat_id="c1", sender_id="u1", content="hi")

    out = loop._assemble_outbound(msg, "answer", "stop", False, trace_id="b" * 32)

    assert out is not None
    assert out.metadata["trace_id"] == "b" * 32


def test_assemble_outbound_without_trace_id() -> None:
    loop = object.__new__(AgentLoop)
    msg = InboundMessage(channel="websocket", chat_id="c1", sender_id="u1", content="hi")

    out = loop._assemble_outbound(msg, "answer", "stop", False)

    assert out is not None
    assert "trace_id" not in out.metadata
