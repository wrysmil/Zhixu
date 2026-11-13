"""Runner + tool 边界的轨迹采集集成测试。"""

from __future__ import annotations

from typing import Any

import pytest

from nanobot.agent.runner import AgentRunner, AgentRunSpec
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import (
    GenerationSettings,
    LLMProvider,
    LLMResponse,
    LLMUsage,
    ToolCallRequest,
)
from nanobot.trajectory.context import bind_trace_recorder, reset_trace_recorder
from nanobot.trajectory.service import TrajectoryService, TrajectorySettings
from nanobot.utils.llm_runtime import LLMRuntime

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars

if True:  # keep imports grouped before fixtures
    pass


class _ScriptedProvider(LLMProvider):
    """最小 provider：只实现 chat()，retry/observer 走真实基类边界。"""

    def __init__(self, responses: list[LLMResponse], model: str = "test-model") -> None:
        super().__init__(provider_name="scripted")
        self._responses = responses
        self._model = model
        self.generation = GenerationSettings()

    def get_default_model(self) -> str:
        return self._model

    async def chat(self, **kwargs: Any) -> LLMResponse:
        return self._responses.pop(0)


class _EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "echo input"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, **kwargs: Any) -> str:
        return f"echo:{kwargs.get('text')}"


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_EchoTool())
    return registry


@pytest.mark.asyncio
async def test_runner_and_tool_boundaries_are_captured(tmp_path) -> None:
    service = TrajectoryService(TrajectorySettings(mode="standard"))
    recorder = service.begin_run(
        workspace=str(tmp_path), session_id="s", turn_id="turn-1", source="user",
    )
    assert recorder is not None
    token = bind_trace_recorder(recorder)
    provider = _ScriptedProvider([
        LLMResponse(
            content="working",
            tool_calls=[ToolCallRequest(id="call_1", name="echo", arguments={"text": "hi"})],
            usage=LLMUsage.reported(input_tokens=5, output_tokens=2),
        ),
        LLMResponse(content="done", usage=LLMUsage.reported(input_tokens=6, output_tokens=3)),
    ])
    try:
        recorder.input_recorded("请回声")
        result = await AgentRunner().run(AgentRunSpec(
            initial_messages=[
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "请回声"},
            ],
            tools=_registry(),
            runtime=LLMRuntime(
                provider=provider,  # type: ignore[arg-type]
                model="test-model",
                generation=GenerationSettings(),
                context_window_tokens=128_000,
            ),
            max_iterations=3,
            max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
            session_key="s",
            workspace=tmp_path,
        ))
    finally:
        reset_trace_recorder(token)
        recorder.finish()
    assert result.final_content == "done"
    assert service.flush_all(timeout=5.0)

    page = service.records_page(str(tmp_path), "s", limit=20)
    assert page is not None
    kinds = [record.kind for record in page.records]
    assert kinds == ["user", "assistant", "tool", "assistant"]
    assistants = [record for record in page.records if record.kind == "assistant"]
    assert [record.step for record in assistants] == [0, 1]
    assert all(record.status == "succeeded" for record in assistants)
    assert assistants[0].extra.get("attempts")
    assert assistants[0].request_ref is not None
    tool = next(record for record in page.records if record.kind == "tool")
    assert tool.status == "succeeded"
    assert tool.duration_ms is not None and tool.duration_ms >= 0
    result_block = next(
        (block for block in tool.block_summaries if block["kind"] == "result"), None,
    )
    assert result_block is not None
    result_ref = result_block["content"]
    body = service.body_slice(str(tmp_path), "s", result_ref["bodyId"])
    assert body is not None and body["text"] == "echo:hi"

    events = service.events_page(str(tmp_path), "s", after_seq=0, limit=200)
    assert events is not None
    types = [event.type for event in events.events]
    assert types.count("model.attempt.started") == 2
    assert types.count("model.attempt.finished") == 2
    assert "tool.requested" in types and "tool.started" in types
    assert types.index("tool.requested") < types.index("tool.started")
    assert types.index("tool.started") < types.index("tool.finished")

    request_body = service.body_slice(
        str(tmp_path), "s", assistants[0].request_ref.body_id,  # type: ignore[union-attr]
    )
    assert request_body is not None
    assert "system prompt" in request_body["text"]
