"""主回合的 Agent 接管：默认主 Agent、显式 @ 档案、团队优先。

全部用内存 provider 替身，不发真实网络请求、不读本机配置。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agents.models import AgentProfile, AgentSelection
from nanobot.agents.store import AgentStore
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ModelPresetConfig
from nanobot.providers.base import LLMResponse
from nanobot.teams.leader import LeaderTurnContext


class _RecordingProvider:
    def __init__(self, default_model: str = "test-model") -> None:
        self.last_tool_definitions: list[Any] = []
        self.last_system_prompt = ""
        self.last_model: Any = None
        self.calls = 0
        self.chat_with_retry = AsyncMock(side_effect=self._respond)
        self.get_default_model = MagicMock(return_value=default_model)
        self.generation = MagicMock(max_tokens=4096, temperature=0.1, reasoning_effort=None)
        self.estimate_prompt_tokens = MagicMock(return_value=(1_000, "test"))
        self.can_resume_conversation_state = MagicMock(return_value=False)

    async def _respond(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        self.last_tool_definitions = list(kwargs.get("tools") or [])
        self.last_model = kwargs.get("model")
        messages = kwargs.get("messages") or []
        self.last_system_prompt = str(messages[0]["content"]) if messages else ""
        return LLMResponse(content="done", tool_calls=[])


def _make_loop(tmp_path: Path, provider: _RecordingProvider) -> AgentLoop:
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )


def _names(definitions: list[Any]) -> list[str]:
    return [str(item["function"]["name"]) for item in definitions]


async def _dispatch(loop: AgentLoop, *, content: str, metadata: dict[str, Any] | None = None) -> None:
    await loop._dispatch(
        InboundMessage(
            channel="websocket",
            sender_id="user-1",
            chat_id="chat-1",
            content=content,
            metadata=metadata or {},
        )
    )


def _save_agent(tmp_path: Path, profile: AgentProfile) -> None:
    AgentStore(tmp_path).save_profile(profile)


@pytest.mark.asyncio
async def test_plain_turn_is_taken_over_by_the_default_commander(tmp_path: Path) -> None:
    provider = _RecordingProvider()
    loop = _make_loop(tmp_path, provider)

    await _dispatch(loop, content="帮我看下这个项目")

    assert "知序指挥官" in provider.last_system_prompt
    # 全量选择不改变本回合工具集：仍是 loop 的真实注册表。
    assert sorted(_names(provider.last_tool_definitions)) == sorted(loop.tool_names)


@pytest.mark.asyncio
async def test_mentioned_agent_applies_prompt_tools_and_model(tmp_path: Path) -> None:
    _save_agent(
        tmp_path,
        AgentProfile(
            id="code-helper",
            name="代码助手",
            type="custom",
            prompt="你是代码助手，只读地分析代码。",
            model_id="fast",
            tools=AgentSelection("include", ["read_file"]),
            skills=AgentSelection("include", []),
        ),
    )
    provider = _RecordingProvider()
    loop = _make_loop(tmp_path, provider)
    loop.runtime_resolver._model_presets["fast"] = ModelPresetConfig.model_validate(
        {"model": "fast-model"}
    )

    await _dispatch(loop, content="@code-helper 看看入口", metadata={"agent_id": "code-helper"})

    assert "你是代码助手，只读地分析代码。" in provider.last_system_prompt
    assert "知序指挥官" not in provider.last_system_prompt
    assert _names(provider.last_tool_definitions) == ["read_file"]
    assert provider.last_model == "fast-model"


def test_loop_wires_the_subagent_model_resolver(tmp_path: Path) -> None:
    """装配锁：``AgentLoop`` 必须把 ``model_resolver`` 接给子 Agent 管理器。

    全仓只有这一处构造 ``SubagentManager``。漏接时档案的 ``modelId`` 在子 Agent 侧
    是惰性的——界面显示选了模型、实际跑父会话的模型，且没有任何信号。
    """
    provider = _RecordingProvider()
    loop = _make_loop(tmp_path, provider)
    loop.runtime_resolver._model_presets["fast"] = ModelPresetConfig.model_validate(
        {"model": "fast-model"}
    )

    resolver = loop.subagents._model_resolver
    assert resolver is not None, "AgentLoop 漏接 model_resolver，档案的 modelId 会静默失效"
    assert resolver("fast") is not None
    assert resolver("fast").model == "fast-model"
    # 不可解析必须收敛成 None 交给调用方判失败，而不是把 KeyError 抛进子 Agent 循环。
    assert resolver("no-such-preset") is None


@pytest.mark.asyncio
async def test_team_assembly_wins_over_the_mentioned_agent(tmp_path: Path) -> None:
    _save_agent(
        tmp_path,
        AgentProfile(
            id="code-helper",
            name="代码助手",
            type="custom",
            prompt="你是代码助手。",
            tools=AgentSelection("include", ["read_file"]),
        ),
    )
    provider = _RecordingProvider()
    loop = _make_loop(tmp_path, provider)
    loop.team_context_provider = lambda msg, session, turn_id: LeaderTurnContext(
        team_id="team-1",
        team_revision=1,
        team_name="内容团队",
        run_id="run-1",
        turn_id=turn_id,
        role_prompt="你是团队 Leader。",
        tool_allow=frozenset({"list_dir"}),
    )

    await _dispatch(loop, content="@code-helper 派活", metadata={"agent_id": "code-helper"})

    assert "你是团队 Leader。" in provider.last_system_prompt
    assert "你是代码助手。" not in provider.last_system_prompt
    assert _names(provider.last_tool_definitions) == ["list_dir"]
