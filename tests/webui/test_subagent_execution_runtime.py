"""普通 spawn 的真实观察接线，隐私边界与兼容性。"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.runner import AgentRunResult
from nanobot.agent.subagent import SubagentExecutionResult, SubagentManager
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.execution import execute_tool_calls
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agents.runtime import AgentProfileRuntime
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings, LLMProvider, LLMResponse, ToolCallRequest
from nanobot.subagent_executions import ExecutionRepository
from nanobot.utils.llm_runtime import LLMRuntime


def runtime():
    provider = MagicMock(spec=LLMProvider)
    provider.generation = GenerationSettings()
    return LLMRuntime.capture(provider, "test", context_window_tokens=128_000)


def context(**overrides):
    values = dict(channel="websocket", chat_id="chat-1", session_key="websocket:chat-1",
                  runtime=runtime(), metadata={"webui_turn_id": "visible-turn"})
    values.update(overrides)
    return RequestContext(**values)


def manager(tmp_path, ledger, **kwargs):
    return SubagentManager(workspace=tmp_path, bus=MessageBus(), max_tool_result_chars=1000,
                           execution_ledger=ledger, **kwargs)


async def drain(sm):
    await asyncio.gather(*list(sm._running_tasks.values()), return_exceptions=True)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_background_is_queued_then_running_and_finishes_with_public_events(tmp_path):
    ledger, events = ExecutionRepository(), []
    sm = manager(tmp_path, ledger, execution_event_sink=events.append)
    entered, release = asyncio.Event(), asyncio.Event()

    async def run(spec):
        entered.set()
        await release.wait()
        return AgentRunResult(final_content="公开答案", messages=[], stop_reason="completed")

    sm.runner.run = run
    with request_context(context()):
        result = await SpawnTool(sm).execute("PRIVATE_RAW_TASK", label="公开任务")
    record = ledger.list("chat-1").items[0]
    assert "started" in result
    assert record.status == "queued"
    assert record.parent_turn_id == "visible-turn"
    assert record.mode == "background"
    await entered.wait()
    assert ledger.detail("chat-1", record.invocation_id).status == "running"
    release.set()
    await drain(sm)
    record = ledger.detail("chat-1", record.invocation_id)
    assert record.status == "succeeded"
    assert record.result["summary"] == "公开答案"
    assert [event.seq for event in events] == list(range(1, record.last_seq + 1))
    assert events[-1].type == "execution.finished"
    assert "PRIVATE_RAW_TASK" not in json.dumps(record.to_dict(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_parallel_spawn_tool_call_ids_are_isolated_and_invocations_unique(tmp_path):
    ledger = ExecutionRepository()
    sm = manager(tmp_path, ledger)
    sm.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="ok", messages=[], stop_reason="completed"))
    registry = ToolRegistry()
    registry.register(SpawnTool(sm))
    calls = [ToolCallRequest(id=f"call-{i}", name="spawn", arguments={"task": "secret"})
             for i in range(3)]
    with request_context(context()):
        await execute_tool_calls(registry, calls, concurrent=True, external_lookup_counts={},
                                 workspace_violation_counts={}, hook=AgentHook(),
                                 context=AgentHookContext(iteration=1, messages=[]))
    await drain(sm)
    records = ledger.list("chat-1").items
    assert len({record.invocation_id for record in records}) == 3
    assert {record.parent_tool_call_id for record in records} == {call.id for call in calls}


@pytest.mark.asyncio
async def test_invalid_profile_is_recorded_before_dispatch(tmp_path):
    ledger, events = ExecutionRepository(), []
    sm = manager(tmp_path, ledger, agent_profiles=AgentProfileRuntime(tmp_path),
                 execution_event_sink=events.append)
    sm.runner.run = AsyncMock()
    with request_context(context()):
        result = await SpawnTool(sm).execute("PRIVATE_RAW_TASK", agent="not-an-agent")
    record = ledger.list("chat-1").items[0]
    assert record.status == "failed"
    assert record.result["errorCode"] == "invalid_profile"
    assert record.result["errorMessage"]
    assert "started" not in result
    sm.runner.run.assert_not_awaited()
    assert events[-1].type == "execution.finished"
    assert "PRIVATE_RAW_TASK" not in json.dumps(record.to_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_reason,content", [("max_iterations", None),
                                                   ("empty_final_response", None),
                                                   ("error", None)])
async def test_inline_failed_conclusions_do_not_become_success(tmp_path, stop_reason, content):
    ledger = ExecutionRepository()
    sm = manager(tmp_path, ledger)
    sm.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content=content, messages=[], stop_reason=stop_reason, error="PRIVATE_MODEL_ERROR"))
    with request_context(context()):
        await SpawnTool(sm).execute("PRIVATE_RAW_TASK", wait=True)
    record = ledger.list("chat-1").items[0]
    assert record.status == "failed"
    assert record.mode == "wait"
    assert record.result["stopReason"] == stop_reason
    assert "PRIVATE_MODEL_ERROR" not in json.dumps(record.to_dict())


@pytest.mark.asyncio
async def test_tools_are_live_sanitized_and_failure_can_recover(tmp_path):
    ledger, events = ExecutionRepository(), []
    sm = manager(tmp_path, ledger, execution_event_sink=events.append)

    async def run(spec):
        ctx = AgentHookContext(iteration=2, messages=[])
        await spec.hook.before_iteration(ctx)
        call = ToolCallRequest(id="child-tool", name="read_file", arguments={"secret": "ARG"})
        ctx.tool_calls = [call]
        await spec.hook.before_execute_tools(ctx)
        await spec.hook.before_execute_tool(ctx, call, None, call.arguments)
        live = ledger.list("chat-1").items[0]
        assert live.phase == "tools"
        assert live.activities[0].status == "running"
        await spec.hook.on_execute_tool_error(
            ctx, call, None, call.arguments, "no such file token=SENSITIVE_TOKEN",
        )
        return AgentRunResult(final_content="recovered", messages=[], stop_reason="completed")

    sm.runner.run = run
    with request_context(context()):
        assert await SpawnTool(sm).execute("PRIVATE_RAW_TASK", wait=True) == "recovered"
    record = ledger.list("chat-1").items[0]
    assert record.status == "succeeded"
    assert record.activities[0].status == "failed"
    assert record.completed_tool_count == 1
    assert record.phase == "finalizing"
    wire = json.dumps([event.to_dict() for event in events])
    # 原始参数与任务正文仍然不外传；失败原因只以脱敏后的投影形式出现（契约 §5）。
    assert "SENSITIVE_TOKEN" not in wire
    assert "PRIVATE_RAW_TASK" not in wire
    assert '"secret"' not in wire
    assert "error=no such file" in wire


@pytest.mark.asyncio
async def test_cancellation_closes_running_and_queued_invocations(tmp_path):
    ledger = ExecutionRepository()
    sm = manager(tmp_path, ledger, max_concurrent_subagents=1)
    entered = asyncio.Event()

    async def run(spec):
        entered.set()
        await asyncio.Event().wait()

    sm.runner.run = run
    with request_context(context()):
        await SpawnTool(sm).execute("one")
        await entered.wait()
        await SpawnTool(sm).execute("two")
    assert {record.status for record in ledger.list("chat-1").items} == {"running", "queued"}
    await sm.cancel_by_session("websocket:chat-1")
    await drain(sm)
    assert {record.status for record in ledger.list("chat-1").items} == {"cancelled"}


@pytest.mark.asyncio
@pytest.mark.parametrize("ctx", [context(channel="cli"), context(metadata={})])
async def test_non_webui_has_no_observable_record(tmp_path, ctx):
    ledger = ExecutionRepository()
    sm = manager(tmp_path, ledger)
    sm.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="old result", messages=[], stop_reason="completed"))
    with request_context(ctx):
        assert await SpawnTool(sm).execute("task", wait=True) == "old result"
    assert not ledger.list("chat-1").items


@pytest.mark.asyncio
async def test_ledger_or_sink_failures_preserve_execution_semantics(tmp_path):
    ledger = ExecutionRepository()

    async def broken_sink(event):
        raise RuntimeError("observer unavailable")

    sm = manager(tmp_path, ledger, execution_event_sink=broken_sink)
    sm.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="old result", messages=[], stop_reason="completed"))
    with request_context(context()):
        assert await SpawnTool(sm).execute("task", wait=True) == "old result"
    assert ledger.list("chat-1").items[0].status == "succeeded"
    ledger.close()
    with request_context(context()):
        assert await SpawnTool(sm).execute("task", wait=True) == "old result"


@pytest.mark.asyncio
async def test_profile_name_is_frozen_in_public_snapshot(tmp_path):
    ledger = ExecutionRepository()
    sm = manager(tmp_path, ledger, agent_profiles=AgentProfileRuntime(tmp_path))
    expected = sm._prepare_agent("code-reviewer").agent.name
    sm.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="ok", messages=[], stop_reason="completed"))
    with request_context(context()):
        await SpawnTool(sm).execute("task", agent="code-reviewer", wait=True)
    assert ledger.list("chat-1").items[0].agent_name == expected


@pytest.mark.asyncio
async def test_real_runner_records_preparation_error_and_later_success(tmp_path):
    ledger = ExecutionRepository()
    sm = manager(tmp_path, ledger)
    model_runtime = runtime()
    model_runtime.provider.chat_with_retry = AsyncMock(side_effect=[
        LLMResponse(content=None, tool_calls=[ToolCallRequest(
            id="bad-call", name="read_file", arguments={},
        )]),
        LLMResponse(content="recovered", tool_calls=[]),
    ])
    with request_context(context(runtime=model_runtime)):
        result = await SpawnTool(sm).execute("PRIVATE_RAW_TASK", wait=True)
    record = ledger.list("chat-1").items[0]
    assert result == "recovered"
    assert record.status == "succeeded"
    assert len(record.activities) == 1
    assert record.activities[0].status == "failed"


@pytest.mark.asyncio
async def test_model_exception_is_public_failure_without_raw_details(tmp_path):
    ledger = ExecutionRepository()
    sm = manager(tmp_path, ledger)
    sm.runner.run = AsyncMock(side_effect=RuntimeError("PRIVATE_EXCEPTION"))
    with request_context(context()):
        result = await SpawnTool(sm).execute("task", wait=True)
    assert "PRIVATE_EXCEPTION" in result  # 旧字符串执行接口保持不变。
    record = ledger.list("chat-1").items[0]
    assert record.status == "failed"
    assert record.result["errorCode"] == "subagent_exception"
    assert "PRIVATE_EXCEPTION" not in json.dumps(record.to_dict())


@pytest.mark.asyncio
async def test_immediate_cancellation_before_coroutine_start_is_recorded(tmp_path):
    ledger = ExecutionRepository()
    sm = manager(tmp_path, ledger)
    with request_context(context()):
        await SpawnTool(sm).execute("task")
    await sm.cancel_by_session("websocket:chat-1")
    assert ledger.list("chat-1").items[0].status == "cancelled"


@pytest.mark.asyncio
async def test_real_runner_timeout_keeps_legacy_text_and_public_timeout_reason(tmp_path):
    ledger = ExecutionRepository()
    sm = manager(tmp_path, ledger, llm_wall_timeout_for_session=lambda _: 0.01)
    model_runtime = runtime()

    async def hung_model(**kwargs):
        await asyncio.Event().wait()

    model_runtime.provider.chat_with_retry = hung_model
    with request_context(context(runtime=model_runtime)):
        result = await asyncio.wait_for(
            SpawnTool(sm).execute("PRIVATE_RAW_TASK", wait=True), timeout=1,
        )
    assert result == "Error calling LLM: timed out after 0.01s"
    record = ledger.list("chat-1").items[0]
    assert record.status == "failed"
    assert record.result["stopReason"] == "timeout"
    assert record.result["errorCode"] == "timeout"
    assert record.result["errorMessage"] == "模型请求超时，请稍后重试。"
    assert "PRIVATE_RAW_TASK" not in json.dumps(record.to_dict())


@pytest.mark.asyncio
async def test_execution_observation_finish_is_idempotent(tmp_path):
    """三条收口路径可能先后触发 finish，只允许第一次真正写库（审查 P1-2）。"""
    ledger, events = ExecutionRepository(), []
    sm = manager(tmp_path, ledger, execution_event_sink=events.append)
    with request_context(context()):
        invocation_id = await sm.create_execution(context(), label="任务")
    assert invocation_id is not None
    observation = sm._execution_observations[invocation_id]
    cancelled = SubagentExecutionResult(invocation_id, "cancelled", "cancelled", "")
    await observation.finish(cancelled)
    await observation.finish(cancelled)
    finished = [event for event in events if event.type == "execution.finished"]
    assert len(finished) == 1
    assert ledger.detail("chat-1", invocation_id).status == "cancelled"
