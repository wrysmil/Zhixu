from nanobot.webui.transcript import replay_transcript_to_ui_messages


def test_feedback_event_marks_assistant_message() -> None:
    trace_id = "a" * 32
    lines = [
        {"event": "user", "text": "hi", "turn_id": "t1", "turn_phase": "user", "turn_seq": 1},
        {
            "event": "stream_end",
            "text": "answer",
            "turn_id": "t1",
            "turn_phase": "answer",
            "turn_seq": 2,
            "trace_id": trace_id,
        },
        {
            "event": "feedback",
            "turn_id": "t1",
            "trace_id": trace_id,
            "helpful": True,
        },
    ]

    messages = replay_transcript_to_ui_messages(lines)
    assistant = [m for m in messages if m["role"] == "assistant"][-1]

    assert assistant["traceId"] == trace_id
    assert assistant["feedback"] == "up"


def test_latest_feedback_wins() -> None:
    trace_id = "b" * 32
    lines = [
        {"event": "user", "text": "hi", "turn_id": "t2", "turn_phase": "user", "turn_seq": 1},
        {
            "event": "message",
            "text": "answer",
            "turn_id": "t2",
            "turn_phase": "answer",
            "turn_seq": 2,
            "trace_id": trace_id,
        },
        {"event": "feedback", "turn_id": "t2", "trace_id": trace_id, "helpful": True},
        {"event": "feedback", "turn_id": "t2", "trace_id": trace_id, "helpful": False},
    ]

    messages = replay_transcript_to_ui_messages(lines)
    assistant = [m for m in messages if m["role"] == "assistant"][-1]

    assert assistant["feedback"] == "down"
