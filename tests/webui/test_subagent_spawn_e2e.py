"""子 Agent 真的被 spawn 出来、并拿到档案给的配置。

这一组是**端到端防线**，与 ``test_subagent_profile_fail_closed.py`` 分工：

- fail_closed 那组验「解析失败时不给危险工具」
- 本组验「解析成功时配置真的流到了 runner.run() 收到的那份 AgentRunSpec」

只断言 ``_prepare_agent`` / ``_build_tools`` 的返回值是不够的——中间任何
一段没接上，单元测试照样绿。这里把 ``AgentRunner.run`` 换成探针，直接看
子 Agent 实际收到的 system prompt 与工具表。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from nanobot.agent.subagent import SubagentManager
from nanobot.agents.models import AgentProfile, AgentSelection
from nanobot.agents.runtime import AgentProfileRuntime
from nanobot.agents.store import AgentStore
from nanobot.bus.queue import MessageBus
from nanobot.nanobot import RunResult
from nanobot.providers.base import GenerationSettings
from nanobot.utils.llm_runtime import LLMRuntime

DANGEROUS = {"exec", "write_file", "edit_file", "apply_patch", "run_cli_app"}


class _SpawnProbe:
    """替换掉 runner.run，截获子 Agent 实际收到的 AgentRunSpec。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def install(self, manager: SubagentManager) -> None:
        # 闭包捕获 probe，**不要**用 fake.__get__(runner) 绑定 —— 那会把
        # fake_run 的 self 设成 runner 实例，self.calls 拿到的是 runner 的属性。
        probe = self

        async def _fake_run(spec):  # noqa: ANN001
            probe.calls.append(
                {
                    "system": spec.initial_messages[0]["content"],
                    "user": spec.initial_messages[1]["content"],
                    "tools": sorted(spec.tools.names()),
                    "max_iterations": spec.max_iterations,
                    "session_key": spec.session_key,
                }
            )
            # 必须返回 RunResult：调用方会读 result.stop_reason / result.content，
            # 返字符串会让 subagent.py:616 抛 AttributeError，
            # 那样测试失败在探针之外的真实代码上，看不出被测行为。
            return RunResult(content="PROBE_DONE", stop_reason="stop")

        # 实例属性直接覆盖，runner.run(self, spec) 调用时只会传 spec。
        manager.runner.run = _fake_run  # type: ignore[method-assign]

    @property
    def last(self) -> dict[str, Any]:
        assert self.calls, "runner.run 未被调用——子 Agent 根本没被 spawn 出来"
        return self.calls[-1]


def _runtime(model: str = "test-model") -> LLMRuntime:
    provider = MagicMock()
    provider.generation = GenerationSettings()
    return LLMRuntime.capture(provider, model, context_window_tokens=128_000)


def _manager(tmp_path: Path) -> tuple[SubagentManager, _SpawnProbe]:
    manager = SubagentManager(
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=4000,
        agent_profiles=AgentProfileRuntime(tmp_path),
    )
    probe = _SpawnProbe()
    probe.install(manager)
    return manager, probe


# --- 子 Agent 真的被 spawn 出来 ---------------------------------------------


@pytest.mark.asyncio
async def test_subagent_is_actually_spawned(tmp_path: Path) -> None:
    """最基础的一条：传了合法档案，子 Agent 真的会跑起来。"""
    manager, probe = _manager(tmp_path)
    result = await manager.run_inline(
        task="审查这段代码", agent_id="code-reviewer", runtime=_runtime()
    )
    assert probe.calls, "子 Agent 未被 spawn——runner.run 从未被调用"
    assert result  # run_inline 返回子 Agent 的结果文本
    assert probe.last["user"] == "审查这段代码", "任务描述应原样传给子 Agent"


@pytest.mark.asyncio
async def test_spawned_subagent_receives_profile_toolset(tmp_path: Path) -> None:
    """档案的工具集必须真的落到 AgentRunSpec 上，而不只是存在某个变量里。"""
    manager, probe = _manager(tmp_path)
    await manager.run_inline(task="t", agent_id="code-reviewer", runtime=_runtime())
    tools = set(probe.last["tools"])
    assert tools == {"read_file", "list_dir", "grep", "find_files"}, (
        f"只读档案拿到的工具集不对：{sorted(tools)}"
    )
    assert not (DANGEROUS & tools), "只读档案绝不能拿到写/执行工具"


@pytest.mark.asyncio
async def test_spawned_subagent_receives_profile_prompt(tmp_path: Path) -> None:
    """档案的提示词必须真的进入子 Agent 的 system prompt。"""
    AgentStore(tmp_path).save_profile(
        AgentProfile(
            id="guard-reader",
            name="守门员",
            description="只读地检查改动。",
            type="custom",
            prompt="职责边界：只看，不改。任何结论都要标出 file:line。",
        )
    )
    manager, probe = _manager(tmp_path)
    await manager.run_inline(task="检查", agent_id="guard-reader", runtime=_runtime())
    system = probe.last["system"]
    assert system.startswith("你是守门员"), f"身份行应在最前，实际开头：{system[:60]!r}"
    assert "职责边界：只看，不改" in system
    assert "file:line" in system, "档案 prompt 应完整注入"
    assert "# Subagent" in system, "出厂模板仍应保留在后面"


@pytest.mark.asyncio
async def test_factory_profile_prompt_is_injected_before_the_template(tmp_path: Path) -> None:
    """出厂档案自带提示词：身份行在最前，档案正文排在出厂模板之前。"""
    manager, probe = _manager(tmp_path)
    await manager.run_inline(task="t", agent_id="code-reviewer", runtime=_runtime())
    system = probe.last["system"]
    assert system.startswith("你是代码评审"), f"身份行应在最前，实际：{system[:60]!r}"
    assert "# 回报格式" in system, "出厂提示词正文应完整注入"
    assert "# Subagent" in system, "出厂模板仍应保留在后面"


@pytest.mark.asyncio
async def test_omitting_agent_spawns_with_full_default_toolset(tmp_path: Path) -> None:
    """未传档案时子 Agent 仍要正常 spawn，并拿到 subagent 范围的默认工具集。"""
    manager, probe = _manager(tmp_path)
    await manager.run_inline(task="t", agent_id=None, runtime=_runtime())
    tools = set(probe.last["tools"])
    assert len(tools) >= 13, f"默认工具集应完整，实际 {len(tools)} 个：{sorted(tools)}"
    assert DANGEROUS & tools, "默认路径本就含写/执行工具，这是二期之前的既有行为"
    assert probe.last["system"].startswith("# Subagent")


# --- skill 也真的生效 --------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_skill_selection_reaches_subagent_prompt(tmp_path: Path) -> None:
    """档案的 skills 裁剪要体现在子 Agent 实际收到的 prompt 里。

    与工具同理：断言 _prepare_agent 的返回值不够，要看 runner 收到的东西。
    """
    manager, probe = _manager(tmp_path)
    await manager.run_inline(task="t", agent_id="code-reviewer", runtime=_runtime())
    system = probe.last["system"]
    # code-reviewer 出厂 skills=include[] → 摘要里不该有任何技能条目
    assert "Built-in skills" not in system, "include 空列表时不应出现技能清单"

    manager2, probe2 = _manager(tmp_path / "second")
    AgentStore(tmp_path / "second").save_profile(
        AgentProfile(
            id="all-skills",
            name="全能",
            type="custom",
            prompt="",
            skills=AgentSelection("all"),
        )
    )
    await manager2.run_inline(task="t", agent_id="all-skills", runtime=_runtime())
    assert "Built-in skills" in probe2.last["system"], "skills=all 时技能清单应出现"


# --- 拒绝路径：子 Agent 压根不该被启动 ---------------------------------------


@pytest.mark.asyncio
async def test_rejected_agent_never_starts_a_subagent(tmp_path: Path) -> None:
    """fail-closed 的真正含义是「不执行」，不是「执行但不裁工具」。"""
    manager, probe = _manager(tmp_path)
    result = await manager.run_inline(
        task="t", agent_id="code-reviwer", runtime=_runtime()
    )
    assert not probe.calls, "档案非法时仍启动了子 Agent——这是 fail-open"
    assert "code-reviwer" in result
    assert "code-reviewer" in result, "错误信息应让 LLM 能自行纠正"
