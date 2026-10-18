import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from nanobot.subagent_executions import ExecutionRepository, WriterBusy


def create(repo: ExecutionRepository, chat: str = "chat"):
    return repo.create(
        chat_id=chat,
        parent_turn_id="turn",
        parent_tool_call_id="call",
        agent_id="agent",
        name="same",
        title="公开标题",
        mode="background",
    )


def test_distinct_invocations_lifecycle_and_activity():
    repo = ExecutionRepository()
    first, second = create(repo), create(repo)
    assert first.invocation_id != second.invocation_id
    assert str(UUID(first.invocation_id)) == first.invocation_id
    started = repo.start(first.invocation_id)
    assert started.status == "running" and started.queue_duration_ms is not None
    activity = repo.start_activity(first.invocation_id, "read_file")
    repo.finish_activity(first.invocation_id, activity.activity_id)
    finished = repo.finish(first.invocation_id, "succeeded", result={"summary": "完成"})
    assert finished.finished_at and finished.duration_ms is not None
    assert finished.completed_tool_count == 1
    repo.start(first.invocation_id)
    assert repo.detail("chat", first.invocation_id).status == "succeeded"
    events = repo.events("chat", first.invocation_id).items
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert events[0].type == "execution.created"
    assert events[-1].type == "execution.finished"
    tool_events = [event for event in events if "activity" in event.data]
    assert [event.type for event in tool_events] == ["execution.activity", "execution.activity"]
    assert [event.data["activity"]["status"] for event in tool_events] == ["running", "succeeded"]
    assert len(repo.detail("chat", first.invocation_id).activities) == 1
    assert [event.to_dict()["data"]["activity"]["activityId"] for event in tool_events] == [
        activity.activity_id,
        activity.activity_id,
    ]


def test_activity_keeps_projected_detail_and_output_across_restart(tmp_path: Path):
    repo = ExecutionRepository(tmp_path)
    item = create(repo)
    repo.start(item.invocation_id)
    activity = repo.start_activity(item.invocation_id, "read_file")
    repo.finish_activity(
        item.invocation_id, activity.activity_id,
        detail="path=src/app.py", output="lines=12",
    )
    repo.close()

    recovered = ExecutionRepository(tmp_path)
    stored = recovered.detail("chat", item.invocation_id).activities[0]
    assert (stored.detail, stored.output) == ("path=src/app.py", "lines=12")
    recovered.close()


def test_activity_without_projection_stays_null():
    repo = ExecutionRepository()
    item = create(repo)
    repo.start(item.invocation_id)
    activity = repo.start_activity(item.invocation_id, "spawn")
    repo.finish_activity(item.invocation_id, activity.activity_id)

    stored = repo.detail("chat", item.invocation_id).activities[0]
    assert stored.detail is None and stored.output is None


def test_chat_isolation_and_active_items_do_not_consume_history_limit():
    repo = ExecutionRepository()
    active = create(repo)
    for _ in range(3):
        item = create(repo)
        repo.finish(item.invocation_id, "succeeded")
    other = create(repo, "other")
    page = repo.list("chat", limit=1)
    assert len(page.items) == 2
    assert active.invocation_id in [item.invocation_id for item in page.items]
    assert page.has_more and page.next_cursor
    next_page = repo.list("chat", limit=1, cursor=page.next_cursor)
    assert len(next_page.items) == 2
    assert repo.detail("chat", other.invocation_id) is None
    assert repo.events("chat", other.invocation_id).items == []
    assert repo.list("other").items == [other]
    assert repo.events("chat", active.invocation_id, after_seq=999).reset_required


def test_recovery_and_writer_lock(tmp_path: Path):
    repo = ExecutionRepository(tmp_path)
    assert repo.db_path == tmp_path / "runtime" / "agent-executions.db"
    assert repo.db_path.is_file()
    assert not (tmp_path / "subagent-executions").exists()
    item = create(repo)
    repo.start(item.invocation_id)
    activity = repo.start_activity(item.invocation_id, "read_file")
    with pytest.raises(WriterBusy):
        ExecutionRepository(tmp_path)
    repo.close()
    recovered = ExecutionRepository(tmp_path)
    detail = recovered.detail("chat", item.invocation_id)
    assert detail.status == "interrupted"
    assert detail.activities[0].activity_id == activity.activity_id
    assert detail.activities[0].status == "interrupted"
    assert recovered.events("chat", item.invocation_id).items[-1].type == "execution.finished"
    recovered.close()


def test_interrupted_recovery_uses_wall_clock_duration(tmp_path: Path):
    """进程重启收口旧 running 时，没有 monotonic 起点也必须给出真实耗时。"""
    repo = ExecutionRepository(tmp_path)
    item = create(repo)
    repo.start(item.invocation_id)
    db_path = repo.db_path
    assert db_path is not None
    repo.close()
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT payload FROM executions WHERE invocation_id=?", (item.invocation_id,),
        ).fetchone()
        payload = json.loads(row[0])
        payload["startedAt"] = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat()
        conn.execute(
            "UPDATE executions SET payload=? WHERE invocation_id=?",
            (json.dumps(payload, ensure_ascii=False), item.invocation_id),
        )
    recovered = ExecutionRepository(tmp_path)
    detail = recovered.detail("chat", item.invocation_id)
    assert detail is not None
    assert detail.status == "interrupted"
    assert detail.duration_ms is not None and detail.duration_ms >= 4000
    recovered.close()


def test_private_result_fields_never_persist_or_project(tmp_path: Path):
    repo = ExecutionRepository(tmp_path)
    item = create(repo)
    repo.finish(
        item.invocation_id,
        "failed",
        result={
            "summary": "失败",
            "token": "SENSITIVE",
            "task": "SENSITIVE",
            "toolArguments": {"secret": "SENSITIVE"},
            "output": "SENSITIVE",
        },
    )
    wire = json.dumps(repo.detail("chat", item.invocation_id).to_dict())
    wire += json.dumps(repo.events("chat", item.invocation_id).to_dict())
    assert "SENSITIVE" not in wire
    repo.close()
    assert all(b"SENSITIVE" not in path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())


def test_tool_boundaries_survive_progress_throttling(monkeypatch):
    monkeypatch.setattr("nanobot.subagent_executions.events.time.monotonic", lambda: 10.0)
    repo = ExecutionRepository()
    item = create(repo)
    repo.set_phase(item.invocation_id, "preparing", last_action="准备")
    repo.set_phase(item.invocation_id, "preparing", last_action="已节流")
    activity = repo.start_activity(item.invocation_id, "read_file")
    repo.finish_activity(item.invocation_id, activity.activity_id)
    repo.finish(item.invocation_id, "succeeded")
    events = repo.events("chat", item.invocation_id).items
    tool_events = [event for event in events if "activity" in event.data]
    assert [event.type for event in tool_events] == ["execution.activity", "execution.activity"]
    assert [event.data["activity"]["status"] for event in tool_events] == ["running", "succeeded"]
    assert len({event.data["activity"]["activityId"] for event in tool_events}) == 1
    assert len([event for event in events if event.type == "execution.activity"]) == 3
    assert events[-1].type == "execution.finished"
    repo.close()
