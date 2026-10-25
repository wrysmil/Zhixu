"""T06 Trace 采集与分段持久化（契约 §5 / spec §9）。

红灯口径全部来自契约硬数字：16 KiB 事件正文、1 MiB 分段、20 MiB 单 attempt 上限、
seq 严格递增、重复 event_id 不重复写入、截断行必须 trace_complete=false。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from nanobot.evaluation.schemas import TraceEvent
from nanobot.evaluation.trace import (
    MAX_EVENT_BODY_BYTES,
    MAX_SEGMENT_BYTES,
    MAX_TRACE_BYTES,
    TraceCollector,
    TraceError,
    read_trace,
)


def make_event(
    collector: TraceCollector,
    seq: int,
    event_type: str = "turn.started",
    payload: dict[str, Any] | None = None,
    *,
    event_id: str | None = None,
) -> TraceEvent:
    return TraceEvent.model_validate(
        {
            "schema_version": 1,
            "attempt_id": collector.attempt_id,
            "seq": seq,
            "event_id": event_id or str(uuid.uuid4()),
            "type": event_type,
            "turn": 0,
            "iteration": None,
            "call_id": None,
            "ts": "2026-10-15T10:00:00+00:00",
            "elapsed_ms": seq * 10,
            "payload": payload or {},
        }
    )


@pytest.fixture
def trace_dir(tmp_path: Path) -> Path:
    path = tmp_path / "trace"
    path.mkdir()
    return path


@pytest.fixture
def collector(trace_dir: Path) -> TraceCollector:
    return TraceCollector(attempt_id=str(uuid.uuid4()), trace_dir=trace_dir)


def test_append_writes_complete_lines_to_active_segment(
    collector: TraceCollector, trace_dir: Path
) -> None:
    collector.append(make_event(collector, 1, "attempt.started"))
    collector.append(make_event(collector, 2, "turn.started"))

    segment = trace_dir / "trace-000001.jsonl"
    assert segment.exists()
    lines = segment.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["seq"] for line in lines] == [1, 2]


def test_duplicate_event_id_is_not_written_twice(
    collector: TraceCollector, trace_dir: Path
) -> None:
    event = make_event(collector, 1, "attempt.started")
    assert collector.append(event) is event
    assert collector.append(event) is None

    lines = (trace_dir / "trace-000001.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_event_from_other_attempt_is_rejected(collector: TraceCollector) -> None:
    foreign = TraceEvent.model_validate(
        {
            "schema_version": 1,
            "attempt_id": str(uuid.uuid4()),
            "seq": 1,
            "event_id": str(uuid.uuid4()),
            "type": "attempt.started",
            "ts": "2026-10-15T10:00:00+00:00",
            "elapsed_ms": 0,
            "payload": {},
        }
    )
    with pytest.raises(TraceError, match="TRACE_ATTEMPT_MISMATCH"):
        collector.append(foreign)


def test_seq_must_increase_strictly(collector: TraceCollector) -> None:
    collector.append(make_event(collector, 1))
    collector.append(make_event(collector, 2))
    # 同 seq 重放（不同 event_id）与跳号都不接受：seq 是证据完整性判定依据。
    with pytest.raises(TraceError, match="TRACE_SEQ_OUT_OF_ORDER"):
        collector.append(make_event(collector, 2))
    with pytest.raises(TraceError, match="TRACE_SEQ_OUT_OF_ORDER"):
        collector.append(make_event(collector, 5))


def test_oversized_payload_spills_to_controlled_artifact(
    collector: TraceCollector, trace_dir: Path
) -> None:
    big = "x" * (MAX_EVENT_BODY_BYTES + 1024)
    collector.append(make_event(collector, 1, "model.request_summary", {"messages": big}))

    persisted = read_trace(trace_dir).events[0]
    assert persisted.payload["truncated"] is True
    assert uuid.UUID(str(persisted.payload["artifact_id"]))
    assert "messages" not in persisted.payload

    artifact = (trace_dir / "artifacts" / f"{persisted.payload['artifact_id']}.txt").read_text(
        encoding="utf-8"
    )
    assert json.loads(artifact) == {"messages": big}


def test_body_exactly_at_limit_stays_inline(
    collector: TraceCollector, trace_dir: Path
) -> None:
    exact = "x" * (MAX_EVENT_BODY_BYTES - 64)
    collector.append(make_event(collector, 1, "model.request_summary", {"messages": exact}))

    persisted = read_trace(trace_dir).events[0]
    assert "truncated" not in persisted.payload
    assert not (trace_dir / "artifacts").exists()


def test_segment_rotates_at_one_mebibyte(collector: TraceCollector, trace_dir: Path) -> None:
    # 每行约 15 KiB（未触发 16 KiB 外移），写 100 行必然跨过 1 MiB 分段边界。
    chunk = "y" * 15_000
    for seq in range(1, 101):
        collector.append(make_event(collector, seq, "tool.completed", {"blob": chunk}))

    segments = sorted(path.name for path in trace_dir.glob("trace-*.jsonl"))
    assert len(segments) > 1
    for name in segments:
        assert (trace_dir / name).stat().st_size <= MAX_SEGMENT_BYTES
    assert [event.seq for event in read_trace(trace_dir).events] == list(range(1, 101))


def test_trace_budget_stop_keeps_trace_incomplete(collector: TraceCollector) -> None:
    chunk = {"blob": "z" * 200_000}
    seq = 1
    while not collector.limit_reached and seq < 2000:
        collector.append(make_event(collector, seq, "tool.completed", chunk))
        seq += 1

    assert collector.limit_reached is True
    assert collector.trace_complete is False
    assert collector.total_bytes <= MAX_TRACE_BYTES
    types = [event.type for event in collector.buffered_incomplete_events]
    assert "trace.incomplete" in types
    # 触顶后继续写入被拒绝，而不是静默丢证据后假装完整。
    assert collector.append(make_event(collector, seq, "tool.completed", chunk)) is None


def test_seal_writes_manifest_with_flush_and_fsync(
    collector: TraceCollector, trace_dir: Path
) -> None:
    collector.append(make_event(collector, 1))
    manifest = collector.seal()

    assert manifest.manifest_path == trace_dir / "manifest.json"
    body = json.loads((trace_dir / "manifest.json").read_text(encoding="utf-8"))
    assert body["attempt_id"] == collector.attempt_id
    assert body["trace_complete"] is True
    assert body["event_count"] == 1
    assert body["segments"][0]["sha256"]
    assert manifest.trace_complete is True

    read = read_trace(trace_dir)
    assert read.sealed is True
    assert read.trace_complete is True


def test_seal_is_atomic_and_idempotent(collector: TraceCollector, trace_dir: Path) -> None:
    collector.append(make_event(collector, 1))
    first = collector.seal()
    second = collector.seal()

    assert first.event_count == second.event_count == 1
    assert not list(trace_dir.glob("manifest.json.tmp*"))
    assert json.loads((trace_dir / "manifest.json").read_text(encoding="utf-8"))["event_count"] == 1


def test_write_failure_raises_trace_error(collector: TraceCollector, tmp_path: Path) -> None:
    blocked = tmp_path / "locked"
    blocked.mkdir()
    # 段文件名被目录占住：跨平台都必然写失败，不依赖 chmod 在 Windows 上的语义。
    (blocked / "trace-000001.jsonl").mkdir()
    failing = TraceCollector(attempt_id=collector.attempt_id, trace_dir=blocked)
    with pytest.raises(TraceError, match="TRACE_WRITE_FAILED"):
        failing.append(make_event(failing, 1))
    assert failing.trace_complete is False


def test_partial_trailing_line_marks_trace_incomplete(trace_dir: Path) -> None:
    collector = TraceCollector(attempt_id=str(uuid.uuid4()), trace_dir=trace_dir)
    collector.append(make_event(collector, 1))
    collector.append(make_event(collector, 2))

    active = trace_dir / "trace-000001.jsonl"
    text = active.read_text(encoding="utf-8")
    active.write_text(text + '{"seq": 3, "att', encoding="utf-8")

    read = read_trace(trace_dir)
    assert [event.seq for event in read.events] == [1, 2]
    assert read.trace_complete is False


def test_garbled_line_marks_trace_incomplete(trace_dir: Path) -> None:
    collector = TraceCollector(attempt_id=str(uuid.uuid4()), trace_dir=trace_dir)
    collector.append(make_event(collector, 1))
    active = trace_dir / "trace-000001.jsonl"
    active.write_text(
        active.read_text(encoding="utf-8") + '{"seq": 2}\n', encoding="utf-8"
    )

    read = read_trace(trace_dir)
    assert read.trace_complete is False
    assert [event.seq for event in read.events] == [1]


def test_unsealed_trace_is_reported_as_not_sealed(collector: TraceCollector, trace_dir: Path) -> None:
    collector.append(make_event(collector, 1))
    assert read_trace(trace_dir).sealed is False


def test_legacy_event_without_execution_identity_remains_valid() -> None:
    event = TraceEvent.model_validate(
        {
            "schema_version": 1,
            "attempt_id": str(uuid.uuid4()),
            "seq": 1,
            "event_id": str(uuid.uuid4()),
            "type": "model.call_completed",
            "ts": "2026-10-15T10:00:00+00:00",
            "elapsed_ms": 5,
            "payload": {"role": "agent", "scope": "main"},
        }
    )

    assert event.call_id is None
    assert "execution_id" not in event.payload
