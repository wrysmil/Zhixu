"""轨迹 WebSocket 帧形状测试。"""

from __future__ import annotations

from nanobot.trajectory.models import (
    SCHEMA_VERSION,
    CommitBatch,
    TrajectoryEvent,
    TrajectoryRecord,
)
from nanobot.webui.outbound_wire import encode_trajectory_batch


def test_encode_trajectory_batch_uses_snake_frame_and_camel_payload() -> None:
    event = TrajectoryEvent(
        schema_version=SCHEMA_VERSION,
        event_id="e1",
        session_id="websocket:chat-1",
        seq=7,
        trace_id="t1",
        run_id="r1",
        turn_id="turn-1",
        step=0,
        span_id=None,
        parent_span_id=None,
        execution_id=None,
        logical_request_id="req-1",
        model_call_id=None,
        tool_call_id=None,
        type="model.requested",
        ignorable=False,
        time_ms=1,
        payload={},
    )
    record = TrajectoryRecord(
        record_id="assistant:req-1",
        session_id="websocket:chat-1",
        kind="assistant",
        run_id="r1",
        turn_id="turn-1",
        step=0,
        span_id="req-1",
        parent_span_id=None,
        model_order=0,
        status="succeeded",
        started_at_ms=1,
        finished_at_ms=2,
        duration_ms=1,
        anchor_seq=7,
        record_revision=7,
        block_summaries=[],
        request_ref=None,
        schema_ref=None,
    )
    batch = CommitBatch(
        workspace="/tmp/ws",
        session_id="websocket:chat-1",
        from_seq=7,
        to_seq=7,
        durable_seq=9,
        history_generation=1,
        events=(event,),
        record_updates=(record,),
    )
    payload = encode_trajectory_batch("chat-1", {
        "sessionId": batch.session_id,
        "fromSeq": batch.from_seq,
        "toSeq": batch.to_seq,
        "durableSeq": batch.durable_seq,
        "historyGeneration": batch.history_generation,
        "events": [item.to_dict() for item in batch.events],
        "recordUpdates": [item.to_dict() for item in batch.record_updates],
    })
    assert payload["event"] == "trajectory_batch"
    assert payload["type"] == "trajectory_batch"
    assert payload["chat_id"] == "chat-1"
    assert payload["from_seq"] == 7 and payload["to_seq"] == 7
    assert payload["durable_seq"] == 9
    assert payload["events"][0]["eventId"] == "e1"
    assert payload["record_updates"][0]["recordId"] == "assistant:req-1"
