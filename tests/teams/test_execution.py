"""子执行结构化结果与冻结档案（plan Task 3）。

三条不可退让的口径（plan §3.3、契约 §5）：

1. **结果不靠字符串猜**：``max_iterations`` / ``invalid_profile`` / runner error 都是
   失败，只有正常最终答案才是 succeeded。
2. **冻结档案不被临场覆盖**：执行用的 tools/prompt/model 全部来自传入的
   ``ResolvedAgent``；即使宿主上还挂着能读到「同名但已改动」档案的 profile 来源，
   本次执行也不许去重读。
3. **旧 API 原样**：``spawn`` / ``run_inline`` / ``_run_subagent`` 仍返回字符串，
   输出文本与改动前一致；团队路径共用同一个 Runner，不存在第二个 LLM 循环。

runner 全部用 AsyncMock 替身，不发真实模型请求。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.hook import AgentHookContext
from nanobot.agent.runner import AgentRunResult
from nanobot.agent.subagent import (
    SubagentExecutionResult,
    SubagentManager,
    SubagentStatus,
)
from nanobot.agents.runtime import ResolvedAgent
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings, LLMProvider, LLMUsage
from nanobot.teams.execution import NO_EVENTS, TeamExecutionRequest, TeamMemberExecutor
from nanobot.utils.llm_runtime import LLMRuntime


class ListSink:
    """把公开活动记下来的替身；契约 §7 只允许白名单摘要进入事件流。"""

    def __init__(self) -> None:
        self.activities: list[dict[str, Any]] = []

    async def task_activity(self, **payload: Any) -> None:
        self.activities.append(payload)


def _runtime(*, model: str = "team-model") -> LLMRuntime:
    provider = MagicMock(spec=LLMProvider)
    provider.generation = GenerationSettings(temperature=0.1, max_tokens=4096)
    return LLMRuntime.capture(provider, model, context_window_tokens=128_000)


def _frozen_agent(**overrides: Any) -> ResolvedAgent:
    defaults: dict[str, Any] = dict(
        id="researcher",
        name="研究员",
        description="检索与核对来源",
        model_id=None,
        prompt="你只负责检索与核对来源。",
        tool_names=frozenset({"read_file", "list_dir"}),
    )
    defaults.update(overrides)
    return ResolvedAgent(**defaults)


def _request(tmp_path: Path, agent: ResolvedAgent | None = None, **overrides: Any) -> TeamExecutionRequest:
    defaults: dict[str, Any] = dict(
        run_id="run-1",
        task_id="task-1",
        attempt_id="att-1",
        member_id="m-researcher",
        agent_id="researcher",
        instruction="检索文档并给出来源清单",
        acceptance="每条结论都带可核对链接",
        agent=_frozen_agent() if agent is None else agent,
        runtime=_runtime(),
        workspace=tmp_path,
    )
    defaults.update(overrides)
    return TeamExecutionRequest(**defaults)


class SpyProfileRuntime:
    """记录是否被调用；契约 §5 要求团队执行**不得**按 agentId 临场重读档案。"""

    def __init__(self, resolves_to: ResolvedAgent | None = None) -> None:
        self.calls: list[str] = []
        self._resolves_to = resolves_to

    def resolve(self, agent_id: str, *, workspace: Any = None, tools_config: Any = None) -> Any:
        self.calls.append(agent_id)
        return self._resolves_to


@pytest.fixture
def manager(tmp_path: Path) -> SubagentManager:
    return SubagentManager(workspace=tmp_path, bus=MessageBus(), max_tool_result_chars=16_000)


@pytest.fixture
def executor(manager: SubagentManager) -> TeamMemberExecutor:
    return TeamMemberExecutor(manager)


# ----------------------------------------------------------- 结构化结果判定


async def test_final_answer_is_success(
    executor: TeamMemberExecutor, manager: SubagentManager, tmp_path: Path
) -> None:
    manager.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="三条来源已核对", messages=[], stop_reason="completed",
    ))
    result = await executor.execute_member(_request(tmp_path), events=NO_EVENTS)
    assert result.outcome == "succeeded"
    assert result.stop_reason == "completed"
    assert result.final_content == "三条来源已核对"
    assert result.error_code is None


async def test_iteration_limit_is_not_success(
    executor: TeamMemberExecutor, manager: SubagentManager, tmp_path: Path
) -> None:
    """迭代耗尽不是「做完了」：必须失败，不能被兜底文案伪装成成功。"""
    manager.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content=None, messages=[], stop_reason="max_iterations",
    ))
    result = await executor.execute_member(_request(tmp_path), events=NO_EVENTS)
    assert result.outcome == "failed"
    assert result.stop_reason == "max_iterations"


async def test_empty_final_response_is_not_success(
    executor: TeamMemberExecutor, manager: SubagentManager, tmp_path: Path
) -> None:
    manager.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="", messages=[], stop_reason="empty_final_response",
    ))
    result = await executor.execute_member(_request(tmp_path), events=NO_EVENTS)
    assert result.outcome == "failed"
    assert result.stop_reason == "empty_final_response"


async def test_runner_error_is_failure_not_an_ok_string(
    executor: TeamMemberExecutor, manager: SubagentManager, tmp_path: Path
) -> None:
    manager.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content=None, messages=[], stop_reason="error", error="provider 502",
    ))
    result = await executor.execute_member(_request(tmp_path), events=NO_EVENTS)
    assert result.outcome == "failed"
    assert result.stop_reason == "error"
    assert result.error_code == "subagent_error"
    # 可读原因留在 final_content，但判定不依赖它是否含 "Error"。
    assert "provider 502" in result.final_content


async def test_exception_is_failure(
    executor: TeamMemberExecutor, manager: SubagentManager, tmp_path: Path
) -> None:
    manager.runner.run = AsyncMock(side_effect=RuntimeError("LLM down"))
    result = await executor.execute_member(_request(tmp_path), events=NO_EVENTS)
    assert result.outcome == "failed"
    assert result.stop_reason == "error"


async def test_cancellation_keeps_cancelled_semantics(
    executor: TeamMemberExecutor, manager: SubagentManager, tmp_path: Path
) -> None:
    """取消既不记成功也不记失败：父取消要能区分它并等子任务清理干净。"""
    manager.runner.run = AsyncMock(side_effect=asyncio.CancelledError())
    result = await executor.execute_member(_request(tmp_path), events=NO_EVENTS)
    assert result.outcome == "cancelled"
    assert result.stop_reason == "cancelled"


async def test_result_carries_the_attempt_as_real_execution_id(
    executor: TeamMemberExecutor, manager: SubagentManager, tmp_path: Path
) -> None:
    manager.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="done", messages=[], stop_reason="completed",
    ))
    result = await executor.execute_member(_request(tmp_path), events=NO_EVENTS)
    assert result.execution_id == "att-1"


async def test_missing_frozen_profile_fails_closed(
    executor: TeamMemberExecutor, tmp_path: Path
) -> None:
    """``tool_names=None`` 表示「档案没约束」，团队里不允许这种模糊状态起跑。"""
    with pytest.raises(ValueError, match="invalid_profile"):
        await executor.execute_member(
            _request(tmp_path, agent=_frozen_agent(tool_names=None)), events=NO_EVENTS
        )


# ------------------------------------------------------------- 冻结档案不被覆盖


async def test_frozen_profile_is_never_reread_from_the_store(
    tmp_path: Path
) -> None:
    """传入冻结档案时，宿主上的 profile 来源一次都不许被调用。

    否则「运行中有人改了档案」会静默改变正在执行的那次的权限与提示词。
    """
    spy = SpyProfileRuntime(resolves_to=_frozen_agent(prompt="运行后被改新的提示词"))
    manager = SubagentManager(
        workspace=tmp_path, bus=MessageBus(), max_tool_result_chars=16_000, agent_profiles=spy
    )
    seen: dict[str, Any] = {}

    async def capture(spec: Any) -> AgentRunResult:
        seen["system_prompt"] = spec.initial_messages[0]["content"]
        seen["tools"] = spec.tools.names()
        return AgentRunResult(final_content="ok", messages=[], stop_reason="completed")

    manager.runner.run = AsyncMock(side_effect=capture)
    result = await TeamMemberExecutor(manager).execute_member(
        _request(tmp_path, _frozen_agent()), events=NO_EVENTS
    )
    assert result.outcome == "succeeded"
    assert spy.calls == []
    assert seen["system_prompt"].count("运行后被改新的提示词") == 0
    assert "你只负责检索与核对来源。" in seen["system_prompt"]
    assert set(seen["tools"]) == {"read_file", "list_dir"}


async def test_model_override_still_applies_to_the_frozen_agent(
    tmp_path: Path
) -> None:
    """冻结档案里的 ``modelId`` 是 preset 名：由宿主 resolver 解析，不读裸模型名。"""
    resolved = _runtime(model="opus-high-resolved")
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
        model_resolver=lambda _name: resolved,
    )
    seen: dict[str, Any] = {}

    async def capture(spec: Any) -> AgentRunResult:
        seen["model"] = spec.runtime.model
        return AgentRunResult(final_content="ok", messages=[], stop_reason="completed")

    manager.runner.run = AsyncMock(side_effect=capture)
    await TeamMemberExecutor(manager).execute_member(
        _request(tmp_path, _frozen_agent(model_id="opus-high")), events=NO_EVENTS
    )
    assert seen["model"] == "opus-high-resolved"


async def test_preset_that_cannot_be_resolved_is_reported_not_silently_defaulted(
    tmp_path: Path
) -> None:
    """resolver 解析不出档案指定的 preset：plan §3.3 要求这是失败，不能悄悄改用默认模型。"""
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=16_000,
        model_resolver=lambda _name: None,
    )
    seen: dict[str, Any] = {}

    async def capture(spec: Any) -> AgentRunResult:
        seen["model"] = spec.runtime.model
        return AgentRunResult(final_content="ok", messages=[], stop_reason="completed")

    manager.runner.run = AsyncMock(side_effect=capture)
    result = await TeamMemberExecutor(manager).execute_member(
        _request(tmp_path, _frozen_agent(model_id="gone-preset")), events=NO_EVENTS
    )
    assert result.outcome == "failed"
    assert result.stop_reason == "invalid_profile"
    assert "runner.run" not in seen or seen["model"] == "team-model"


async def test_team_execution_never_announces(
    executor: TeamMemberExecutor, manager: SubagentManager, tmp_path: Path
) -> None:
    """announce=False：成员结果不重新唤醒 Leader，否则一份结果会起两个回合。"""
    manager.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="done", messages=[], stop_reason="completed",
    ))
    with patch.object(manager, "_announce_result", new_callable=AsyncMock) as announce:
        await executor.execute_member(_request(tmp_path), events=NO_EVENTS)
        announce.assert_not_called()


async def test_child_scope_excludes_spawn_and_dispatch_tools(
    executor: TeamMemberExecutor, manager: SubagentManager, tmp_path: Path
) -> None:
    """一层委派：成员即使档案放行，也拿不到 spawn / team_dispatch。"""
    manager.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="done", messages=[], stop_reason="completed",
    ))
    frozen = _frozen_agent(tool_names=frozenset({"read_file", "spawn", "team_dispatch"}))
    seen: dict[str, Any] = {}

    async def capture(spec: Any) -> AgentRunResult:
        seen["tools"] = set(spec.tools.names())
        return AgentRunResult(final_content="done", messages=[], stop_reason="completed")

    manager.runner.run = AsyncMock(side_effect=capture)
    await executor.execute_member(_request(tmp_path, frozen), events=NO_EVENTS)
    assert "spawn" not in seen["tools"]
    assert "team_dispatch" not in seen["tools"]


async def test_activity_events_carry_tool_name_only(
    executor: TeamMemberExecutor, manager: SubagentManager, tmp_path: Path
) -> None:
    """公开摘要只取白名单字段：工具参数与输出不得进入事件流（契约 §7）。"""

    async def run_with_tools(spec: Any) -> AgentRunResult:
        context = AgentHookContext(
            iteration=1,
            tool_calls=[],
            tool_events=[{
                "name": "read_file",
                "status": "ok",
                "arguments": '{"path": "/etc/passwd"}',
                "output": "root:x:0:0",
            }],
            messages=[],
            usage=None,
            error=None,
            stop_reason="completed",
            final_content="done",
        )
        await spec.hook.after_iteration(context)
        return AgentRunResult(final_content="done", messages=[], stop_reason="completed")

    manager.runner.run = AsyncMock(side_effect=run_with_tools)
    sink = ListSink()
    await executor.execute_member(_request(tmp_path), events=sink)
    assert sink.activities, "工具活动应当产生至少一条公开摘要"
    rendered = str(sink.activities)
    assert "/etc/passwd" not in rendered
    assert "root:x:0:0" not in rendered


# ----------------------------------------------------------------- 旧 API 兼容


async def test_legacy_run_subagent_still_returns_the_same_strings(
    manager: SubagentManager, tmp_path: Path
) -> None:
    """``_run_subagent`` 的字符串契约与 max_iterations 兜底文案都不许变。"""
    manager.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content=None, messages=[], stop_reason="max_iterations",
    ))
    status = SubagentStatus(
        task_id="t1", label="label", task_description="do task", started_at=time.monotonic()
    )
    with patch.object(manager, "_announce_result", new_callable=AsyncMock):
        text = await manager._run_subagent(
            "t1", "do task", "label",
            {"channel": "cli", "chat_id": "direct", "session_key": None},
            status, _runtime(),
        )
    assert isinstance(text, str)
    assert text == "Task completed but no final response was generated."


async def test_legacy_error_path_still_wraps_in_error_prefix(
    manager: SubagentManager, tmp_path: Path
) -> None:
    manager.runner.run = AsyncMock(side_effect=RuntimeError("LLM down"))
    status = SubagentStatus(
        task_id="t1", label="label", task_description="do task", started_at=time.monotonic()
    )
    with patch.object(manager, "_announce_result", new_callable=AsyncMock):
        text = await manager._run_subagent(
            "t1", "do task", "label",
            {"channel": "cli", "chat_id": "direct", "session_key": None},
            status, _runtime(),
        )
    assert text.startswith("Error:")


async def test_typed_result_carries_real_usage(
    executor: TeamMemberExecutor, manager: SubagentManager, tmp_path: Path
) -> None:
    usage = LLMUsage(
        input_tokens=120,
        output_tokens=34,
        total_tokens=154,
        reported_tokens=154,
        estimated_tokens=0,
    )
    manager.runner.run = AsyncMock(return_value=AgentRunResult(
        final_content="done", messages=[], stop_reason="completed", usage=usage,
    ))
    result = await executor.execute_member(_request(tmp_path), events=NO_EVENTS)
    assert isinstance(result, SubagentExecutionResult)
    assert result.usage is not None
    assert result.usage.total_tokens == 154
