"""评估 worker 的物理模型调用与执行身份关联。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from nanobot.evaluation.worker import WorkerTraceHook, make_model_call_observer
from nanobot.llm_usage.models import LLMCallRecord


def call_record(*, model: str = "demo") -> LLMCallRecord:
    return LLMCallRecord(
        started_at_ms=0,
        duration_ms=2,
        provider="demo",
        model=model,
        source="user",
        stream=False,
        finish_reason="stop",
    )


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event_type: str, **kwargs: Any) -> None:
        self.events.append((event_type, kwargs))


def test_physical_model_call_id_is_stable_per_record_and_unique_per_retry() -> None:
    recorder = Recorder()
    observer = make_model_call_observer(recorder, role="agent", scope="main")  # type: ignore[arg-type]
    first = call_record()
    retry = replace(first, duration_ms=3)

    observer(first)
    observer(first)
    observer(retry)

    ids = [event[1]["payload"]["model_call_id"] for event in recorder.events]
    assert ids[0] == ids[1]
    assert ids[2] != ids[0]
    assert [event[1]["call_id"] for event in recorder.events] == ids


def test_model_call_event_propagates_explicit_execution_identity() -> None:
    recorder = Recorder()
    observer = make_model_call_observer(
        recorder,  # type: ignore[arg-type]
        role="agent",
        scope="auxiliary",
        agent_id="researcher",
        agent_name="Researcher",
        execution_id="exec-child",
        parent_execution_id="exec-root",
    )

    observer(call_record())

    payload = recorder.events[0][1]["payload"]
    assert payload["agent_id"] == "researcher"
    assert payload["agent_name"] == "Researcher"
    assert payload["execution_id"] == "exec-child"
    assert payload["parent_execution_id"] == "exec-root"


def test_model_call_event_keeps_unknown_identity_null() -> None:
    recorder = Recorder()
    observer = make_model_call_observer(recorder, role="judge", scope="main")  # type: ignore[arg-type]

    observer(call_record(model="judge"))

    payload = recorder.events[0][1]["payload"]
    assert payload["agent_id"] is None
    assert payload["agent_name"] is None
    assert payload["execution_id"] is None
    assert payload["parent_execution_id"] is None


async def test_same_local_tool_id_is_namespaced_by_execution() -> None:
    first_recorder = Recorder()
    second_recorder = Recorder()
    first_recorder.execution_id = "exec-a"  # type: ignore[attr-defined]
    second_recorder.execution_id = "exec-b"  # type: ignore[attr-defined]
    runtime = SimpleNamespace(capability={}, registry=SimpleNamespace(tool_names=[]))
    context = SimpleNamespace(iteration=0)
    tool_call = SimpleNamespace(id="local-1", name="read_file", arguments={"path": "a.txt"})

    first = WorkerTraceHook(first_recorder, turn=1, runtime=runtime)  # type: ignore[arg-type]
    second = WorkerTraceHook(second_recorder, turn=1, runtime=runtime)  # type: ignore[arg-type]
    await first.before_execute_tool(context, tool_call, None, {"path": "a.txt"})
    await first.after_execute_tool(context, tool_call, None, {"path": "a.txt"}, "ok")
    await second.before_execute_tool(context, tool_call, None, {"path": "a.txt"})

    first_ids = [kwargs["call_id"] for _, kwargs in first_recorder.events]
    second_id = second_recorder.events[0][1]["call_id"]
    assert first_ids == ["exec-a:local-1", "exec-a:local-1"]
    assert second_id == "exec-b:local-1"
    assert first_recorder.events[0][1]["payload"]["provider_call_id"] == "local-1"
