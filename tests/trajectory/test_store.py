"""轨迹存储与投影的最小事实闭环测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.trajectory.context import bind_trace_recorder, reset_trace_recorder
from nanobot.trajectory.policy import redact_text
from nanobot.trajectory.service import TrajectoryService, TrajectorySettings


def _service(tmp_path: Path) -> TrajectoryService:
    return TrajectoryService(TrajectorySettings(
        mode="standard", retention_days=30, max_body_mb=8, max_run_body_mb=64,
    ))


def test_record_projection_and_pagination(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workspace = str(tmp_path)
    recorder = service.begin_run(
        workspace=workspace,
        session_id="websocket:chat-1",
        turn_id="turn-1",
        source="user",
        visible=False,
    )
    assert recorder is not None
    token = bind_trace_recorder(recorder)
    try:
        recorder.input_recorded("请检查构建失败")
        recorder.step_started(0)
        recorder.model_requested(
            "req-1",
            request_snapshot={"system": "sys", "messages": [{"role": "user", "content": "hi"}]},
            purpose="chat",
        )
        recorder.attempt_started("call-1", attempt=1, provider="p", model="m",
                                 logical_request_id="req-1")
        recorder.model_delta("call-1", 1, "text", "我先看")
        recorder.attempt_finished(
            "call-1",
            status="succeeded",
            finish_reason="tool_calls",
            blocks=[("text", "我先看日志"), ("tool-call", [{"name": "exec"}])],
            usage={"inputTokens": 10, "outputTokens": 3, "totalTokens": 13,
                   "source": "reported"},
            ttft_ms=120,
            generation_ms=300,
            provider="p",
            model="m",
            started_at_ms=1,
            duration_ms=420,
        )
        recorder.tool_requested(
            tool_call_id="tool-1", provider_call_id="provider-1", name="exec",
            arguments={"command": "npm test"}, schema={"type": "object"}, model_order=1,
        )
        recorder.tool_started(tool_call_id="tool-1")
        recorder.tool_finished(
            tool_call_id="tool-1", status="failed",
            error="Error: exit 1", duration_ms=37, side_effect_outcome="none",
        )
        recorder.step_finished(0, "failed")
        recorder.finish(status="failed", stop_reason="error")
    finally:
        reset_trace_recorder(token)

    assert service.flush_all(timeout=5.0)

    page = service.records_page(workspace, "websocket:chat-1", limit=10)
    assert page is not None
    kinds = [record.kind for record in page.records]
    assert kinds == ["user", "assistant", "tool"]
    user, assistant, tool = page.records
    assert user.step == -1
    assert assistant.status == "succeeded"
    assert assistant.request_ref is not None
    assert tool.status == "failed"
    assert tool.duration_ms == 37
    assert page.snapshot_seq >= 8

    body = service.body_slice(
        workspace, "websocket:chat-1", user.block_summaries[0]["content"]["bodyId"],
    )
    assert body is not None
    assert "构建失败" in body["text"]

    # 向前补页必须保持稳定身份且不重复。
    older = service.records_page(
        workspace, "websocket:chat-1", before_cursor=page.next_before_cursor, limit=1,
    ) if page.next_before_cursor else None
    if older is not None:
        assert all(record.record_id not in {r.record_id for r in page.records}
                   for record in older.records)

    events = service.events_page(workspace, "websocket:chat-1", after_seq=0, limit=100)
    assert events is not None
    seqs = [event.seq for event in events.events]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))
    assert events.events[0].type == "run.started"


def test_redaction_never_persists_known_secret(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workspace = str(tmp_path)
    recorder = service.begin_run(
        workspace=workspace, session_id="s1", turn_id=None, source="system",
    )
    assert recorder is not None
    token = bind_trace_recorder(recorder)
    try:
        recorder.input_recorded("Authorization: Bearer abcdefghijklmnop123456")
        recorder.tool_requested(
            tool_call_id="t1", provider_call_id=None, name="http",
            arguments={"api_key": "sk-secretsecretsecret", "url": "https://x?token=abc123"},
            schema=None, model_order=1,
        )
        recorder.tool_finished(tool_call_id="t1", status="succeeded", result="ok")
        recorder.finish()
    finally:
        reset_trace_recorder(token)
    assert service.flush_all(timeout=5.0)

    events = service.events_page(workspace, "s1", after_seq=0, limit=100)
    assert events is not None
    encoded = repr([event.payload for event in events.events])
    assert "sk-secretsecretsecret" not in encoded
    assert "abcdefghijklmnop123456" not in encoded
    assert "[REDACTED]" in encoded

    page = service.records_page(workspace, "s1", limit=10)
    assert page is not None
    tool = next(record for record in page.records if record.kind == "tool")
    args_body = service.body_slice(
        workspace, "s1", tool.request_ref.body_id,  # type: ignore[union-attr]
    )
    assert args_body is not None
    assert "sk-secretsecretsecret" not in args_body["text"]
    assert "abc123" not in args_body["text"]


@pytest.mark.parametrize("mode", ["off", "metadata"])
def test_disabled_and_metadata_modes(tmp_path: Path, mode: str) -> None:
    service = TrajectoryService(TrajectorySettings(mode=mode))
    recorder = service.begin_run(
        workspace=str(tmp_path), session_id="s", turn_id=None, source="system",
    )
    if mode == "off":
        assert recorder is None
        return
    assert recorder is not None
    token = bind_trace_recorder(recorder)
    try:
        recorder.input_recorded("正文不应进入 metadata 轨迹")
        recorder.finish()
    finally:
        reset_trace_recorder(token)
    assert service.flush_all(timeout=5.0)
    page = service.records_page(str(tmp_path), "s", limit=10)
    assert page is not None
    ref = page.records[0].block_summaries[0]["content"]
    assert ref["unavailableReason"] == "not_recorded"
    assert ref["preview"] == ""
    assert ref["bodyId"] is None


def test_redact_text_variants() -> None:
    text, redacted = redact_text("token=abcdef123456 and sk-abcdefghijklmnopqrstuvwx")
    assert redacted
    assert "abcdef123456" not in text
    assert "sk-abcdefghijklmnopqrstuvwx" not in text


def test_retention_removes_only_contiguous_prefix(tmp_path: Path) -> None:
    from nanobot.trajectory.models import TraceContext
    from nanobot.trajectory.policy import ContentPolicy
    from nanobot.trajectory.recorder import TraceRecorder
    from nanobot.trajectory.store import TrajectoryCursorExpired, TrajectoryStore

    store = TrajectoryStore(tmp_path, retention_days=1)
    policy = ContentPolicy()

    def recorder(run_id: str) -> TraceRecorder:
        return TraceRecorder(
            TraceContext(
                workspace=str(tmp_path), workspace_id=str(tmp_path),
                session_id="s", trace_id="t", run_id=run_id,
            ),
            policy=policy, sink=store.enqueue, run_body_budget_bytes=1_000_000,
        )

    old = recorder("run-old")
    old.start()
    old.input_recorded("old input")
    old.finish()
    store.flush(timeout=5.0)
    with store._lock, store._conn:  # noqa: SLF001 - retention fixture
        store._conn.execute(  # noqa: SLF001
            "UPDATE trace_runs SET finished_at_ms=1 WHERE run_id='run-old'",
        )
    fresh = recorder("run-new")
    fresh.start()
    fresh.input_recorded("new input")
    fresh.finish()
    store.flush(timeout=5.0)

    store.prune(now_ms=3 * 24 * 60 * 60 * 1000)

    page = store.records_page("s", limit=10)
    assert [record.block_summaries[0]["content"]["preview"] for record in page.records] == [
        "new input",
    ]
    assert page.retained_from_seq > 1
    with pytest.raises(TrajectoryCursorExpired):
        store.events_page("s", after_seq=0)
    store.close()


def test_purge_session_removes_all_facts(tmp_path: Path) -> None:
    service = _service(tmp_path)
    recorder = service.begin_run(
        workspace=str(tmp_path), session_id="s", turn_id=None, source="system",
    )
    assert recorder is not None
    recorder.input_recorded("hello")
    recorder.finish()
    assert service.flush_all(timeout=5.0)
    store = service._stores[str(tmp_path)]  # noqa: SLF001 - purge fixture
    store.purge_session("s")
    page = service.records_page(str(tmp_path), "s", limit=10)
    assert page is not None
    assert page.records == []
    assert page.snapshot_seq == 0


@pytest.mark.asyncio
async def test_compaction_events_become_records(tmp_path: Path) -> None:
    from nanobot.events import NO_EVENTS, ContextCompactionEvent
    from nanobot.trajectory import capture as trace_capture

    service = _service(tmp_path)
    recorder = service.begin_run(
        workspace=str(tmp_path), session_id="s", turn_id="turn-1", source="user",
    )
    assert recorder is not None
    token = bind_trace_recorder(recorder)
    try:
        sink = trace_capture.wrap_trajectory_events(NO_EVENTS)
        await sink.emit(ContextCompactionEvent(compaction_id="c1", phase="started"))
        await sink.emit(ContextCompactionEvent(compaction_id="c1", phase="succeeded"))
    finally:
        reset_trace_recorder(token)
        recorder.finish()
    assert service.flush_all(timeout=5.0)

    page = service.records_page(str(tmp_path), "s", limit=10)
    assert page is not None
    compaction = next(record for record in page.records if record.kind == "compaction")
    assert compaction.status == "succeeded"
    assert compaction.record_id == "compaction:c1"
