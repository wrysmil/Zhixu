"""SubagentManager 按档案裁剪工具与注入 prompt。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.runner import AgentRunResult
from nanobot.agent.subagent import SubagentManager, SubagentStatus
from nanobot.agents.models import AgentProfile, AgentSelection
from nanobot.agents.runtime import AgentProfileRuntime, ResolvedAgent
from nanobot.agents.store import AgentStore
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings, LLMProvider
from nanobot.utils.llm_runtime import LLMRuntime


def _runtime(model: str = "test-model") -> LLMRuntime:
    provider = MagicMock(spec=LLMProvider)
    provider.generation = GenerationSettings()
    return LLMRuntime.capture(provider, model, context_window_tokens=128_000)


def _manager(tmp_path: Path, **kwargs: object) -> SubagentManager:
    return SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=4000,
        **kwargs,  # type: ignore[arg-type]
    )


def test_default_manager_has_no_profile_runtime(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    assert manager._agent_profiles is None


def test_resolve_returns_none_without_runtime(tmp_path: Path) -> None:
    assert _manager(tmp_path)._resolve_agent(None) is None
    assert _manager(tmp_path)._resolve_agent("ghost") is None


def test_resolve_finds_injected_profile(tmp_path: Path) -> None:
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("code-reviewer")
    assert resolved is not None
    assert resolved.id == "code-reviewer"


def test_build_tools_without_filter_keeps_everything(tmp_path: Path) -> None:
    registry = _manager(tmp_path)._build_tools()
    assert "read_file" in registry.names()
    assert "exec" in registry.names()


def test_build_tools_applies_allowed_names(tmp_path: Path) -> None:
    registry = _manager(tmp_path)._build_tools(allowed_tools=frozenset({"read_file"}))
    assert registry.names() == ["read_file"]


def test_build_tools_with_empty_allowed_removes_all(tmp_path: Path) -> None:
    assert _manager(tmp_path)._build_tools(allowed_tools=frozenset()).names() == []


def test_build_prompt_without_agent_keeps_template(tmp_path: Path) -> None:
    prompt = _manager(tmp_path)._build_subagent_prompt(workspace=tmp_path)
    assert prompt
    assert "nanobot" in prompt.lower() or "subagent" in prompt.lower()


def test_build_prompt_prepends_agent_identity(tmp_path: Path) -> None:
    """身份行拼在出厂模板之前；档案给了 prompt 才拼。"""
    AgentStore(tmp_path).save_profile(
        AgentProfile(
            id="reviewer",
            name="代码评审",
            description="只读地审查改动，指出缺陷与风险，不动手改代码。",
            prompt="审查时逐条列出问题，不要给修复代码。",
        )
    )
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("reviewer")
    prompt = manager._build_subagent_prompt(workspace=tmp_path, agent=resolved)
    assert prompt.startswith("你是代码评审，只读地审查改动")
    assert "审查时逐条列出问题" in prompt
    assert "# Subagent" in prompt


def test_build_prompt_without_description_omits_empty_clause(tmp_path: Path) -> None:
    AgentStore(tmp_path).save_profile(
        AgentProfile(id="silent", name="沉默", prompt="只做事。")
    )
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("silent")
    assert manager._build_subagent_prompt(
        workspace=tmp_path, agent=resolved
    ).startswith("你是沉默。")


def test_build_prompt_adds_identity_when_prompt_empty(tmp_path: Path) -> None:
    """提示词为空也要拼身份行。

    不拼的话，选中这个档案与不传 agent 参数之间，模型侧只剩几个工具的差别——
    档案名和描述只活在 UI 上，等于白配。
    """
    AgentStore(tmp_path).save_profile(
        AgentProfile(id="blank", name="空白档案", description="没写提示词的自建档案")
    )
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("blank")
    prompt = manager._build_subagent_prompt(workspace=tmp_path, agent=resolved)
    assert prompt.startswith("你是空白档案，没写提示词的自建档案。")
    assert "# Subagent" in prompt


def test_prompt_excludes_skills_not_enabled(tmp_path: Path) -> None:
    AgentStore(tmp_path).save_profile(
        AgentProfile(id="narrow", name="窄", skills=AgentSelection("include", []))
    )
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("narrow")
    prompt = manager._build_subagent_prompt(workspace=tmp_path, agent=resolved)
    # include 空列表 = 一个技能都不要；摘要里不该出现任何技能条目
    assert "Built-in skills" not in prompt


@pytest.mark.asyncio
async def test_profile_model_replaces_the_runtime_before_the_runner(tmp_path: Path) -> None:
    """档案指定的 preset 必须真的成为这次执行用的 runtime。"""
    AgentStore(tmp_path).save_profile(
        AgentProfile(id="preset-user", name="带模型档案", model_id="preset-a", prompt="做事。")
    )
    replacement = _runtime("resolved-model")
    manager = _manager(
        tmp_path,
        agent_profiles=AgentProfileRuntime(tmp_path),
        model_resolver=lambda model_id: replacement if model_id == "preset-a" else None,
    )
    manager.runner.run = AsyncMock(  # type: ignore[method-assign]
        return_value=AgentRunResult(final_content="done", messages=[], stop_reason="completed")
    )

    await manager.run_inline(task="t", agent_id="preset-user", runtime=_runtime("parent-model"))

    spec = manager.runner.run.await_args.args[0]
    assert spec.runtime.model == "resolved-model"
    assert spec.runtime.provider is replacement.provider


@pytest.mark.asyncio
async def test_unresolvable_profile_model_fails_without_calling_the_runner(tmp_path: Path) -> None:
    """preset 已被删除时当场失败，不悄悄换成父会话的模型，也不白烧一次推理。"""
    AgentStore(tmp_path).save_profile(
        AgentProfile(id="ghost", name="幽灵模型档案", model_id="gone", prompt="做事。")
    )
    manager = _manager(
        tmp_path,
        agent_profiles=AgentProfileRuntime(tmp_path),
        model_resolver=lambda _model_id: None,
    )
    manager.runner.run = AsyncMock(  # type: ignore[method-assign]
        return_value=AgentRunResult(final_content="done", messages=[], stop_reason="completed")
    )

    result = await manager.run_inline(task="t", agent_id="ghost", runtime=_runtime("parent-model"))

    assert "无法解析" in result
    manager.runner.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_frozen_profile_model_fails_the_same_way(tmp_path: Path) -> None:
    """团队路径与普通 spawn 路径同一个口径：两条都不许静默降级。"""
    origin = {"channel": "team", "chat_id": "run-1", "session_key": None}
    manager = _manager(
        tmp_path,
        agent_profiles=AgentProfileRuntime(tmp_path),
        model_resolver=lambda _model_id: None,
    )
    manager.runner.run = AsyncMock(  # type: ignore[method-assign]
        return_value=AgentRunResult(final_content="done", messages=[], stop_reason="completed")
    )

    result = await manager.execute_frozen(
        "t",
        execution_id="att-1",
        label="lbl",
        origin=origin,  # type: ignore[arg-type]
        agent=ResolvedAgent(
            id="ghost", name="幽灵", description="", model_id="gone", prompt="做事。",
            tool_names=frozenset({"read_file"}),
        ),
        runtime=_runtime("parent-model"),
    )

    assert result.outcome == "failed"
    assert result.error_code == "model_preset_unavailable"
    manager.runner.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_id_reaches_admitted_subagent(tmp_path: Path) -> None:
    """agent_id 必须一路从 run_inline 传到 _run_admitted_subagent 的解析层。

    传递链 4 跳：run_inline → _run_subagent → _run_admitted_subagent →
    _prepare_agent。这里 patch 的是链路终点而非中间方法，后续真实流程
    （announce 等）照常走完。

    探针是 ``_prepare_agent`` 而非 ``_resolve_agent``：运行路径走前者
    （后者只服务「未注入档案的宿主」与测试）。
    """
    manager = _manager(tmp_path)
    seen: list[str | None] = []
    real = manager._prepare_agent

    def _spy(agent_id: str | None):
        seen.append(agent_id)
        return real(agent_id)

    with patch.object(manager, "_prepare_agent", side_effect=_spy):
        await manager.run_inline(task="t", agent_id="code-reviewer", runtime=_runtime())
    assert seen == ["code-reviewer"]


@pytest.mark.asyncio
async def test_agent_id_reaches_background_subagent(tmp_path: Path) -> None:
    """后台 spawn 走 _run_subagent 中转，agent_id 不能在这一跳丢掉。"""
    manager = _manager(tmp_path)
    origin = {"channel": "cli", "chat_id": "c1", "session_key": "cli:c1"}
    status = SubagentStatus(task_id="tid", label="lbl", task_description="t", started_at=0.0)
    seen: list[str | None] = []
    real = manager._prepare_agent

    def _spy(agent_id: str | None):
        seen.append(agent_id)
        return real(agent_id)

    with patch.object(manager, "_prepare_agent", side_effect=_spy):
        async with manager._run_slots:
            await manager._run_subagent(
                "tid", "t", "lbl", origin, status, _runtime(),  # type: ignore[arg-type]
                agent_id="researcher",
            )
    assert seen == ["researcher"]


def test_tools_and_skills_are_trimmed_together(tmp_path: Path) -> None:
    """回归防线：档案的 tools 与 skills 必须在同一次装配里都被应用。

    只裁工具不裁技能（或反之）会让 prompt 说一套、实际能做另一套。
    """
    AgentStore(tmp_path).save_profile(
        AgentProfile(
            id="narrow",
            name="窄",
            tools=AgentSelection("include", ["read_file"]),
            skills=AgentSelection("include", []),
        )
    )
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("narrow")
    assert resolved is not None
    registry = manager._build_tools(allowed_tools=resolved.tool_names)
    prompt = manager._build_subagent_prompt(workspace=tmp_path, agent=resolved)
    assert registry.names() == ["read_file"]
    assert "Built-in skills" not in prompt


def test_subagent_never_sees_spawn(tmp_path: Path) -> None:
    """防递归回归防线：子 Agent 的工具集里不能出现 spawn。

    依据是 ``SpawnTool`` 未声明 ``_scopes``，走 ``Tool.base`` 默认的
    ``{"core"}``，在 ``loader.py:100`` 的 scope 过滤处即被剔除。
    若本测试失败，说明 scope 机制被意外改动，必须回滚排查。
    """
    manager = _manager(tmp_path, agent_profiles=AgentProfileRuntime(tmp_path))
    resolved = manager._resolve_agent("code-reviewer")
    registry = manager._build_tools(
        allowed_tools=resolved.tool_names if resolved is not None else None
    )
    assert "spawn" not in registry.names()
