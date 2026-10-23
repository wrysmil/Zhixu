"""Task 6 确定性 gateway 端到端：真实 AgentLoop 接线 → 执行账本 → 只读 HTTP → WS 帧。

不依赖真实付费模型：子 Agent 的 runner 被替换成本地确定性协程，工具生命周期、
ledger 写入、只读查询与实时帧全部走生产代码路径。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.runner import AgentRunResult
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings, LLMProvider
from nanobot.subagent_executions import ExecutionRepository
from nanobot.utils.llm_runtime import LLMRuntime
from nanobot.webui.outbound_wire import (
    SubagentExecutionWireEvent,
    encode_subagent_execution_event,
)
from nanobot.webui.subagent_execution_routes import build_subagent_execution_operations


def _provider() -> MagicMock:
    provider = MagicMock(spec=LLMProvider)
    provider.generation = GenerationSettings()
    provider.get_default_model.return_value = "test-model"
    return provider


def _loop(workspace: Path, provider: MagicMock, *, websocket: bool = True) -> AgentLoop:
    channels: object | None = {"websocket": {"enabled": True}} if websocket else None
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        model="test-model",
        channels_config=channels,
    )


def _context(provider: MagicMock) -> RequestContext:
    return RequestContext(
        channel="websocket",
        chat_id="chat-1",
        session_key="websocket:chat-1",
        runtime=LLMRuntime.capture(provider, "test-model", context_window_tokens=128_000),
        metadata={"webui_turn_id": "turn-1"},
    )


def _slow_runner(entered: asyncio.Event, release: asyncio.Event, answer: str = "公开答案"):
    async def run(_spec: object) -> AgentRunResult:
        entered.set()
        await release.wait()
        return AgentRunResult(final_content=answer, messages=[], stop_reason="completed")

    return run


def _immediate_runner(answer: str = "ok"):
    async def run(_spec: object) -> AgentRunResult:
        return AgentRunResult(final_content=answer, messages=[], stop_reason="completed")

    return run


async def _drain(loop: AgentLoop) -> None:
    await asyncio.gather(*list(loop.subagents._running_tasks.values()), return_exceptions=True)
    await asyncio.sleep(0)


async def _wire_payloads(loop: AgentLoop) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    while loop.bus.outbound_size:
        message = await loop.bus.consume_outbound()
        if isinstance(message.event, SubagentExecutionWireEvent):
            encoded = encode_subagent_execution_event(message.chat_id, message.event)
            if encoded is not None:
                payloads.append(dict(encoded))
    return payloads


@pytest.mark.asyncio
async def test_background_spawn_wires_ledger_ws_frames_and_http_recovery(tmp_path: Path):
    provider = _provider()
    loop = _loop(tmp_path, provider)
    ledger = loop.execution_ledger
    assert isinstance(ledger, ExecutionRepository)
    assert loop.subagents.execution_ledger is ledger

    entered, release = asyncio.Event(), asyncio.Event()
    loop.subagents.runner.run = _slow_runner(entered, release)

    with request_context(_context(provider)):
        result = await SpawnTool(loop.subagents).execute("PRIVATE_RAW_TASK", label="公开任务")

    record = ledger.list("chat-1").items[0]
    assert "started" in result
    # P0-01：后台派发返回后不能显示成功，仍是 queued/running。
    assert record.status == "queued"
    assert record.mode == "background"
    assert record.parent_turn_id == "turn-1"

    await asyncio.wait_for(entered.wait(), timeout=5)
    operations = build_subagent_execution_operations(ledger)
    running = operations.get_execution(invocation_id=record.invocation_id, chat_id="chat-1")
    assert running["execution"]["status"] == "running"

    release.set()
    await _drain(loop)

    payloads = await _wire_payloads(loop)
    kinds = [payload["data"]["type"] for payload in payloads]  # type: ignore[index]
    assert kinds[0] == "execution.created"
    assert kinds[-1] == "execution.finished"
    assert all(payload["chat_id"] == "chat-1" for payload in payloads)
    assert all(payload["data"]["schemaVersion"] == 1 for payload in payloads)  # type: ignore[index]

    # 刷新/重连恢复：列表与详情都能读回终态与公开结果。
    listing = operations.list_executions(chat_id="chat-1")
    assert [item["invocationId"] for item in listing["executions"]] == [record.invocation_id]
    detail = operations.get_execution(invocation_id=record.invocation_id, chat_id="chat-1")
    execution = detail["execution"]
    assert execution["status"] == "succeeded"
    assert execution["result"]["summary"] == "公开答案"
    events = operations.list_events(invocation_id=record.invocation_id, chat_id="chat-1")
    assert events["lastSeq"] == execution["lastSeq"]
    assert [event["seq"] for event in events["events"]] == list(range(1, events["lastSeq"] + 1))
    assert "PRIVATE_RAW_TASK" not in str(detail)
    loop.execution_ledger.close()


@pytest.mark.asyncio
async def test_wait_spawn_observes_running_then_succeeded(tmp_path: Path):
    provider = _provider()
    loop = _loop(tmp_path, provider)
    ledger = loop.execution_ledger
    assert isinstance(ledger, ExecutionRepository)

    entered, release = asyncio.Event(), asyncio.Event()
    loop.subagents.runner.run = _slow_runner(entered, release)

    async def scenario() -> str:
        with request_context(_context(provider)):
            return await SpawnTool(loop.subagents).execute("任务", label="等待任务", wait=True)

    task = asyncio.create_task(scenario())
    await asyncio.wait_for(entered.wait(), timeout=5)
    record = ledger.list("chat-1").items[0]
    # P0-02：wait=true 期间主 Agent 等待，但状态仍可观察。
    assert record.mode == "wait"
    assert record.status == "running"
    assert record.started_at is not None

    release.set()
    assert await task == "公开答案"
    final = ledger.detail("chat-1", record.invocation_id)
    assert final is not None
    assert final.status == "succeeded"
    assert final.finished_at is not None
    assert final.result is not None and final.result["summary"] == "公开答案"
    loop.execution_ledger.close()


@pytest.mark.asyncio
async def test_ledger_is_absent_without_websocket_channel(tmp_path: Path):
    provider = _provider()
    loop = _loop(tmp_path, provider, websocket=False)
    # P0-23：没有 WebUI 通道时不建账本，也不阻断既有 spawn 语义。
    assert loop.execution_ledger is None
    assert loop.subagents.execution_ledger is None

    loop.subagents.runner.run = _immediate_runner("ok")
    with request_context(_context(provider)):
        result = await SpawnTool(loop.subagents).execute("任务", wait=True)
    assert result == "ok"
    assert loop.bus.outbound_size == 0
