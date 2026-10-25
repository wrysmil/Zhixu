"""评估可观测读模型：旧 Trace 兼容、调用去重、分页和 Token 主账本。"""

from __future__ import annotations

from uuid import UUID

import pytest

from nanobot.evaluation.schemas import TraceEvent


ATTEMPT_ID = "00000000-0000-4000-8000-000000000001"
RUN_ID = "00000000-0000-4000-8000-000000000010"


def _subject() -> object:
    from nanobot.evaluation import observability

    return observability


def _event(
    seq: int,
    kind: str,
    *,
    call_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> TraceEvent:
    return TraceEvent.model_validate(
        {
            "attempt_id": ATTEMPT_ID,
            "seq": seq,
            "event_id": str(UUID(int=seq)),
            "type": kind,
            "turn": 0,
            "iteration": 0,
            "call_id": call_id,
            "ts": "2026-10-25T00:00:00+00:00",
            "elapsed_ms": seq * 10,
            "payload": payload or {},
        }
    )


def test_tool_calls_pair_started_and_completed_once_and_page_by_calls() -> None:
    events = [
        _event(1, "tool.started", call_id="call-a", payload={"name": "web_search"}),
        _event(
            2,
            "tool.completed",
            call_id="call-a",
            payload={"name": "web_search", "duration_ms": 15, "is_error": False},
        ),
        _event(3, "tool.started", call_id="call-b", payload={"name": "read_file"}),
        _event(
            4,
            "tool.failed",
            call_id="call-b",
            payload={"name": "read_file", "duration_ms": 9, "is_error": True},
        ),
    ]

    first = _subject().build_tool_calls_page(events, attempt_id=ATTEMPT_ID, limit=1)
    assert first["total_count"] == 2
    assert len(first["data"]) == 1
    assert first["data"][0]["call_id"] == "call-a"
    assert first["data"][0]["tool_name"] == "web_search"
    assert first["data"][0]["status"] == "succeeded"
    assert first["next_after_key"] is not None

    second = _subject().build_tool_calls_page(
        events,
        attempt_id=ATTEMPT_ID,
        limit=1,
        after_key=first["next_after_key"],
    )
    assert [item["call_id"] for item in second["data"]] == ["call-b"]
    assert second["data"][0]["status"] == "failed"
    assert second["data"][0]["error"]
    assert second["next_after_key"] is None


def test_tool_call_rows_expose_the_contract_field_names() -> None:
    events = [
        _event(
            1,
            "tool.started",
            call_id="call-a",
            payload={
                "name": "web_fetch",
                "execution_id": "exec-main",
                "agent_id": "agent-main",
                "agent_name": "Main agent",
                "phase_id": "research",
                "arguments": {"url": "example.com"},
            },
        ),
        _event(
            2,
            "tool.completed",
            call_id="call-a",
            payload={"name": "web_fetch", "duration_ms": 12, "is_error": False, "result": "ok"},
        ),
    ]

    row = _subject().build_tool_calls(events, attempt_id=ATTEMPT_ID)[0]
    assert set(row) == {
        "call_id",
        "attempt_id",
        "execution_id",
        "phase_id",
        "tool_name",
        "agent_id",
        "agent_name",
        "status",
        "started_at",
        "duration_ms",
        "input_summary",
        "output_summary",
        "error",
        "retry_of_call_id",
        "evidence_refs",
    }
    assert row["execution_id"] == "exec-main"
    assert row["phase_id"] == "research"
    assert row["input_summary"] == '{"url": "example.com"}'
    assert row["output_summary"] == "ok"


def test_tool_calls_reject_cursor_from_another_attempt() -> None:
    events = [
        _event(1, "tool.started", call_id="call-a", payload={"name": "exec"}),
        _event(2, "tool.completed", call_id="call-a", payload={"name": "exec"}),
        _event(3, "tool.started", call_id="call-b", payload={"name": "exec"}),
        _event(4, "tool.completed", call_id="call-b", payload={"name": "exec"}),
    ]
    cursor = _subject().build_tool_calls_page(events, attempt_id=ATTEMPT_ID, limit=1)[
        "next_after_key"
    ]
    with pytest.raises(ValueError, match="CURSOR_INVALID"):
        _subject().build_tool_calls_page(
            events,
            attempt_id="00000000-0000-4000-8000-000000000099",
            limit=1,
            after_key=cursor,
        )


def test_tool_calls_filter_by_status_and_query_only_search_public_fields() -> None:
    events = [
        _event(
            1,
            "tool.started",
            call_id="call-a",
            payload={"name": "web_search", "agent_name": "Main agent"},
        ),
        _event(
            2,
            "tool.completed",
            call_id="call-a",
            payload={"name": "web_search", "is_error": False},
        ),
        _event(
            3,
            "tool.started",
            call_id="call-b",
            payload={"name": "read_file", "arguments": {"path": "secret.txt"}},
        ),
        _event(
            4,
            "tool.failed",
            call_id="call-b",
            payload={"name": "read_file", "is_error": True},
        ),
    ]

    failed = _subject().build_tool_calls_page(events, attempt_id=ATTEMPT_ID, status="failed")
    assert [row["call_id"] for row in failed["data"]] == ["call-b"]

    by_agent = _subject().build_tool_calls_page(events, attempt_id=ATTEMPT_ID, q="Main agent")
    assert [row["call_id"] for row in by_agent["data"]] == ["call-a"]

    # 敏感原始参数不是搜索字段：按参数内容搜不到任何调用。
    by_secret = _subject().build_tool_calls_page(events, attempt_id=ATTEMPT_ID, q="secret.txt")
    assert by_secret["data"] == []


def test_execution_page_emits_one_tool_call_node_per_call() -> None:
    events: list[TraceEvent] = []
    for index in range(45):
        seq = index * 2 + 1
        events.extend(
            [
                _event(seq, "tool.started", call_id=f"c-{index}", payload={"name": "read_file"}),
                _event(
                    seq + 1,
                    "tool.completed",
                    call_id=f"c-{index}",
                    payload={"name": "read_file", "duration_ms": index},
                ),
            ]
        )

    page = _subject().build_execution_page(events, after_seq=0, limit=1000, sealed=True)
    tool_nodes = [node for node in page["data"]["nodes"] if node["kind"] == "tool_call"]
    assert len(tool_nodes) == 45
    assert tool_nodes[0]["id"].startswith("tool:")
    # 工具本体不是模型请求：Token 三项恒为 null，不能把返回文本长度折算成用量。
    assert tool_nodes[0]["total_tokens"] is None
    assert tool_nodes[0]["usage_source"] == "unavailable"
    assert page["next_after_seq"] == 90
    assert page["has_more"] is False
    assert page["coverage"]["identity"] == "unavailable"


def test_execution_page_links_known_identities_without_inventing_parents() -> None:
    events = [
        _event(
            1,
            "tool.started",
            call_id="c-1",
            payload={
                "name": "web_search",
                "execution_id": "exec-child",
                "parent_execution_id": "exec-root",
                "agent_id": "agent-child",
                "agent_name": "Child agent",
            },
        ),
        _event(
            2,
            "tool.completed",
            call_id="c-1",
            payload={"name": "web_search", "execution_id": "exec-child", "is_error": False},
        ),
    ]

    page = _subject().build_execution_page(events, after_seq=0, limit=200, sealed=True)
    node_ids = {node["id"] for node in page["data"]["nodes"]}
    assert "agent:exec-child" in node_ids
    # 父执行没有任何真实事件：不得凭空造出父节点，也不得造出委派边。
    assert "agent:exec-root" not in node_ids
    assert all(edge["kind"] != "delegation" for edge in page["data"]["edges"])
    assert page["coverage"]["identity"] == "complete"


def test_execution_page_marks_model_calls_and_keeps_null_distinct_from_zero() -> None:
    events = [
        _event(
            1,
            "model.call_completed",
            call_id="m-1",
            payload={
                "model_call_id": "m-1",
                "role": "agent",
                "execution_id": "exec-main",
                "agent_id": "agent-main",
                "agent_name": "Main agent",
                "model": "demo-fast",
                "input_tokens": 0,
                "output_tokens": 320,
                "total_tokens": None,
                "reported_tokens": True,
            },
        )
    ]

    page = _subject().build_execution_page(events, after_seq=0, limit=200, sealed=True)
    model_nodes = [node for node in page["data"]["nodes"] if node["kind"] == "model_call"]
    assert len(model_nodes) == 1
    node = model_nodes[0]
    assert node["input_tokens"] == 0
    assert node["output_tokens"] == 320
    assert node["total_tokens"] == 320
    assert node["usage_source"] == "reported"
    assert node["model"] == "demo-fast"
    assert page["coverage"]["usage_attribution"] == "complete"


def test_usage_groups_physical_model_calls_without_replay_double_counting() -> None:
    first = _event(
        1,
        "model.call_completed",
        payload={
            "model_call_id": "request-1",
            "role": "agent",
            "agent_id": "agent-main",
            "provider": "controlled",
            "model": "eval-model",
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 14,
            "reported_tokens": True,
        },
    )
    replay = first.model_copy(update={"seq": 2, "event_id": UUID(int=2)})
    judge = _event(
        3,
        "model.call_completed",
        payload={
            "model_call_id": "request-2",
            "role": "judge",
            "provider": "controlled",
            "model": "judge-model",
            "input_tokens": 5,
            "output_tokens": 1,
            "total_tokens": 6,
            "estimated_tokens": True,
        },
    )

    page = _subject().build_usage_page(
        [(ATTEMPT_ID, [first, replay, judge])],
        run_id=RUN_ID,
        group_by="role",
        limit=50,
    )
    assert page["totals"]["physical_requests"] == 2
    assert page["totals"]["total_tokens"] == 20
    assert [(row["role"], row["physical_requests"]) for row in page["data"]] == [
        ("agent", 1),
        ("judge", 1),
    ]
    assert page["coverage"]["usage"] == "complete"


def test_usage_totals_cover_the_whole_run_not_only_the_current_page() -> None:
    events = [
        _event(
            index,
            "model.call_completed",
            payload={
                "model_call_id": f"request-{index}",
                "role": "agent",
                "agent_id": "agent-main",
                "input_tokens": index,
                "output_tokens": index,
                "total_tokens": index * 2,
                "reported_tokens": True,
            },
        )
        for index in range(1, 6)
    ]

    page = _subject().build_usage_page(
        [(ATTEMPT_ID, events)], run_id=RUN_ID, group_by="agent", limit=1
    )
    assert len(page["data"]) == 1
    assert page["data"][0]["physical_requests"] == 5
    assert page["totals"]["physical_requests"] == 5
    assert page["totals"]["total_tokens"] == 30
    assert page["next_after_key"] is None
