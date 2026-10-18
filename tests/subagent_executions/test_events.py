import json

from nanobot.subagent_executions.events import ActivityThrottle, project_execution_event


def test_activity_throttle_preserves_lifecycle_and_separate_invocations():
    throttle = ActivityThrottle()
    assert throttle.allow("one", "execution.activity", now=0)
    assert not throttle.allow("one", "execution.activity", now=0.1)
    assert throttle.allow("two", "execution.activity", now=0.1)
    assert throttle.allow("one", "execution.activity", now=0.25)
    assert throttle.allow("one", "execution.finished", now=0.28)


def test_projection_is_whitelisted_and_bounded_without_losing_identity():
    event = {
        "schemaVersion": 1,
        "invocationId": "id",
        "chatId": "chat",
        "seq": 1,
        "type": "execution.activity",
        "createdAt": "now",
        "token": "SENSITIVE",
        "data": {
            "execution": {
                "invocationId": "id",
                "status": "running",
                "lastAction": "读" * 10_000,
                "task": "SENSITIVE",
            },
            "activity": {
                "activityId": "a",
                "status": "running",
                "toolName": "read",
                "arguments": "SENSITIVE",
            },
        },
    }
    projected = project_execution_event(event)
    assert projected is not None
    assert len(json.dumps(projected, ensure_ascii=False).encode()) <= 4096
    assert "SENSITIVE" not in json.dumps(projected)
    assert projected["invocationId"] == "id"
    assert projected["data"]["execution"]["status"] == "running"
    assert projected["data"]["activity"]["activityId"] == "a"


def test_non_contract_tool_event_types_are_ignored():
    for event_type in ("activity.started", "activity.finished"):
        assert project_execution_event({"schemaVersion": 1, "type": event_type}) is None


def test_activity_projection_keeps_detail_and_output_within_budget():
    activity = {
        "activityId": "a",
        "kind": "tool",
        "toolName": "read_file",
        "status": "succeeded",
        "label": "read_file",
        "startedAt": "now",
        "finishedAt": "now",
        "detail": "path=" + "a" * 900,
        "output": "lines=12",
    }
    wire = project_execution_event({
        "schemaVersion": 1,
        "type": "execution.activity",
        "data": {"execution": {"invocationId": "id", "status": "running"}, "activity": activity},
    })

    assert wire is not None
    projected = wire["data"]["activity"]
    # 超长字段按 600 字截断，而不是整条事件被丢掉。
    assert len(projected["detail"]) == 600
    assert projected["output"] == "lines=12"


def test_activity_detail_survives_a_full_width_cjk_payload():
    # 两条 600 字的中文摘要 + 240 字 label 必须仍在 8192 字节预算之内。
    activity = {
        "activityId": "a",
        "kind": "tool",
        "toolName": "read_file",
        "status": "failed",
        "label": "读" * 240,
        "startedAt": "now",
        "finishedAt": "now",
        "detail": "path=" + "路" * 594,
        "output": "error=" + "错" * 594,
    }
    wire = project_execution_event({
        "schemaVersion": 1,
        "type": "execution.activity",
        "data": {"execution": {"invocationId": "id", "status": "running"}, "activity": activity},
    })

    assert wire is not None
    assert "detail" in wire["data"]["activity"]
    assert "output" in wire["data"]["activity"]
