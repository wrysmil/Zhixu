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
