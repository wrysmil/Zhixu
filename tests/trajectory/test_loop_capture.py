"""AgentLoop 轮次边界创建轨迹记录的单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop, TurnKind
from nanobot.bus.queue import MessageBus
from nanobot.trajectory import TrajectorySettings, configure_trajectory, trajectory_service


@pytest.fixture
def loop(tmp_path):
    configure_trajectory(TrajectorySettings(mode="standard", retention_days=30))
    instance = AgentLoop(
        bus=MessageBus(),
        provider=MagicMock(),
        workspace=tmp_path,
        model="test-model",
    )
    yield instance
    trajectory_service().close_all()
    configure_trajectory(TrajectorySettings(mode="off"))


def test_start_turn_trace_records_user_input(loop, tmp_path) -> None:
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(
            channel="websocket",
            chat_id="chat-1",
            session_key="websocket:chat-1",
            metadata={"webui_turn_id": "turn-1"},
        ),
        session=SimpleNamespace(key="websocket:chat-1", metadata={"webui": True}),
        session_key="websocket:chat-1",
        turn_id="turn-1",
        ephemeral=False,
        agent=None,
        msg=SimpleNamespace(content="排查构建失败"),
        kind=TurnKind.USER,
        original_user_text=None,
    )

    recorder = loop._start_turn_trace(ctx)
    assert recorder is not None
    recorder.finish()
    service = trajectory_service()
    assert service.flush_all(timeout=5.0)

    summary = service.summary(str(tmp_path), "websocket:chat-1")
    assert summary is not None
    assert summary["durableSeq"] > 0

    page = service.records_page(str(tmp_path), "websocket:chat-1", limit=10)
    assert page is not None
    assert [record.kind for record in page.records] == ["user"]
    body = service.body_slice(
        str(tmp_path), "websocket:chat-1",
        page.records[0].block_summaries[0]["content"]["bodyId"],
    )
    assert body is not None and "排查构建失败" in body["text"]
