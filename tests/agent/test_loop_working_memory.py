"""工作记忆读侧链路（Task D2-D4）：门控、跳过条件与失败隔离。

覆盖四组语义：
- 门控短路：抽取总开关 / 用户记忆开关 / subagent / dream 各自关闭时返回空串。
- 命中路径：普通 turn 能读到写入侧那一行，并渲染成 markdown。
- 失败隔离：数据库读炸了、门控 provider 炸了，都只降级为空串，绝不阻断对话。
- 接线：``Consolidator`` 拿到的是**可调用对象**而非求值结果，token 探针在
  运行时才取工作记忆（见 ``test_consolidator_receives_callable_not_value``）。

``_compute_working_memory_section`` 保持同步：``estimate_session_prompt_tokens``
 是同步方法，D5 需要的 callable 必须是同步的。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from nanobot.agent.context import TranscriptInput
from nanobot.agent.loop import AgentLoop
from nanobot.agent.runner import AgentRunResult
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.scratchpad_writer import ScratchpadWriter
from nanobot.providers.base import LLMProvider
from nanobot.webui.memory_services import MemoryServices

_SESSION_KEY = "cli:direct"


# ---------------------------------------------------------------------------
# 测试替身与构造辅助
# ---------------------------------------------------------------------------


def _make_provider() -> MagicMock:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(
        max_tokens=4096, temperature=0.1, reasoning_effort=None
    )
    provider.estimate_prompt_tokens = MagicMock(return_value=(10_000, "test"))
    return provider


def _make_services(tmp_path: Path) -> MemoryServices:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return MemoryServices(workspace_id="default", database=database)


async def _seed_focus(services: MemoryServices, session_key: str, focus: str) -> None:
    """走**真实写入侧**落一行 scratchpad。

    刻意不直接 ``upsert_scratchpad``：那样等于在测试里复刻一遍 user_id 派生，
    写侧改了测试照样绿。走 ``ScratchpadWriter.update_focus`` 才是真正的
    「读写同源」验证——读侧若查错行，这里立刻读不到。
    """
    writer = ScratchpadWriter(
        services.database,
        user_id=ScratchpadWriter.user_id_for_key(session_key),
        workspace_id=services.workspace_id,
    )
    await writer.update_focus(focus)


def _stub_run_result() -> AgentRunResult:
    """接线测试只需要 turn 跑完的骨架，不关心模型输出。"""
    return AgentRunResult(
        final_content="ok",
        messages=[{"role": "assistant", "content": "ok"}],
        stop_reason="completed",
    )


def _make_loop(tmp_path: Path, **overrides: Any) -> AgentLoop:
    kwargs: dict[str, Any] = dict(
        bus=MessageBus(),
        provider=_make_provider(),
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=128_000,
    )
    kwargs.update(overrides)
    return AgentLoop(**kwargs)


def _enabled_loop(tmp_path: Path, **overrides: Any) -> AgentLoop:
    """开启抽取 + 注入记忆服务的最小 loop。"""
    kwargs: dict[str, Any] = dict(
        memory_extraction_enabled=True,
        memory_services=_make_services(tmp_path),
    )
    kwargs.update(overrides)
    return _make_loop(tmp_path, **kwargs)


# ---------------------------------------------------------------------------
# 命中路径
# ---------------------------------------------------------------------------


class TestHitPath:
    async def test_renders_seeded_focus(self, tmp_path):
        services = _make_services(tmp_path)
        await _seed_focus(services, _SESSION_KEY, "重写注入链路")
        loop = _enabled_loop(tmp_path, memory_services=services)

        section = loop._compute_working_memory_section(_SESSION_KEY)

        assert "# Working Memory" in section
        assert "重写注入链路" in section

    async def test_reads_the_row_the_writer_wrote(self, tmp_path):
        """user_id 必须与写入侧同源，否则查到的是另一行（恒空）。"""
        services = _make_services(tmp_path)
        await _seed_focus(services, _SESSION_KEY, "同源检查")
        loop = _enabled_loop(tmp_path, memory_services=services)

        assert "同源检查" in loop._compute_working_memory_section(_SESSION_KEY)

    async def test_no_row_returns_empty_string(self, tmp_path):
        loop = _enabled_loop(tmp_path)

        assert loop._compute_working_memory_section(_SESSION_KEY) == ""

    async def test_blank_row_renders_nothing(self, tmp_path):
        """行存在但两字段皆空时不得产生空壳段落。"""
        services = _make_services(tmp_path)
        await _seed_focus(services, _SESSION_KEY, "")
        loop = _enabled_loop(tmp_path, memory_services=services)

        assert loop._compute_working_memory_section(_SESSION_KEY) == ""


# ---------------------------------------------------------------------------
# 门控短路
# ---------------------------------------------------------------------------


class TestGates:
    async def test_extraction_disabled_returns_empty(self, tmp_path):
        """总开关关：即便传了 services 也不读。"""
        services = _make_services(tmp_path)
        await _seed_focus(services, _SESSION_KEY, "不该被读到")
        loop = _make_loop(tmp_path, memory_services=services)

        assert loop._compute_working_memory_section(_SESSION_KEY) == ""

    async def test_extraction_disabled_ignores_services(self, tmp_path):
        """反例防线：没装配抽取却注入陈旧工作记忆。"""
        services = _make_services(tmp_path)
        await _seed_focus(services, _SESSION_KEY, "陈旧焦点")
        loop = _make_loop(
            tmp_path,
            memory_extraction_enabled=False,
            memory_services=services,
        )

        assert loop._compute_working_memory_section(_SESSION_KEY) == ""

    async def test_memory_toggle_off_returns_empty(self, tmp_path):
        services = _make_services(tmp_path)
        await _seed_focus(services, _SESSION_KEY, "用户关了记忆")
        loop = _enabled_loop(
            tmp_path, memory_services=services, memory_enabled_provider=lambda: False
        )

        assert loop._compute_working_memory_section(_SESSION_KEY) == ""

    async def test_memory_toggle_on_injects(self, tmp_path):
        services = _make_services(tmp_path)
        await _seed_focus(services, _SESSION_KEY, "用户开着记忆")
        loop = _enabled_loop(
            tmp_path, memory_services=services, memory_enabled_provider=lambda: True
        )

        assert "用户开着记忆" in loop._compute_working_memory_section(_SESSION_KEY)

    async def test_dream_session_is_skipped(self, tmp_path):
        """dream key 恰好查不到行，但那是巧合不是设计，必须显式跳过。"""
        services = _make_services(tmp_path)
        await _seed_focus(services, "dream:nightly", "不该注入 dream")
        loop = _enabled_loop(tmp_path, memory_services=services)

        assert loop._compute_working_memory_section("dream:nightly") == ""

    async def test_subagent_turn_is_skipped(self, tmp_path):
        """子代理复用父 session_key，会拿到父任务焦点——错误语境。"""
        services = _make_services(tmp_path)
        await _seed_focus(services, _SESSION_KEY, "父任务焦点")
        loop = _enabled_loop(tmp_path, memory_services=services)

        assert loop._compute_working_memory_section(_SESSION_KEY, is_subagent=True) == ""

    async def test_non_subagent_same_key_injects(self, tmp_path):
        """对照组：同 key 非 subagent 仍要注入，证明门控只认 is_subagent。"""
        services = _make_services(tmp_path)
        await _seed_focus(services, _SESSION_KEY, "父任务焦点")
        loop = _enabled_loop(tmp_path, memory_services=services)

        assert "父任务焦点" in loop._compute_working_memory_section(
            _SESSION_KEY, is_subagent=False
        )


# ---------------------------------------------------------------------------
# 失败隔离
# ---------------------------------------------------------------------------


class TestFailureIsolation:
    async def test_database_error_is_swallowed(self, tmp_path):
        services = _make_services(tmp_path)
        await _seed_focus(services, _SESSION_KEY, "会被炸掉的读")
        loop = _enabled_loop(tmp_path, memory_services=services)

        def _boom() -> Any:
            raise RuntimeError("database exploded")

        services.database.connect = _boom  # type: ignore[method-assign]

        assert loop._compute_working_memory_section(_SESSION_KEY) == ""

    async def test_gate_provider_error_is_swallowed(self, tmp_path):
        """门控 provider 抛异常不能击穿 prompt 构建。"""
        services = _make_services(tmp_path)
        await _seed_focus(services, _SESSION_KEY, "不该被读到")

        def _gate() -> bool:
            raise RuntimeError("toggle exploded")

        loop = _enabled_loop(
            tmp_path, memory_services=services, memory_enabled_provider=_gate
        )

        assert loop._compute_working_memory_section(_SESSION_KEY) == ""

    async def test_missing_services_is_not_an_error(self, tmp_path):
        """抽取开着但没传 services：回退装配，不该炸。"""
        loop = _make_loop(tmp_path, memory_extraction_enabled=True)

        assert loop._compute_working_memory_section(_SESSION_KEY) == ""

    async def test_fallback_services_are_built_once(self, tmp_path):
        """``for_workspace`` 会 mkdir + replay + 全量建表 DDL；读侧每轮调一次
        就等于每轮重放一遍 schema。回退实例必须缓存。"""
        loop = _make_loop(tmp_path, memory_extraction_enabled=True)

        first = loop._resolve_memory_services(None)
        second = loop._resolve_memory_services(None)

        assert first is second

    async def test_explicit_services_win_over_fallback(self, tmp_path):
        services = _make_services(tmp_path)
        loop = _enabled_loop(tmp_path, memory_services=services)

        assert loop._resolve_memory_services(None) is not services
        assert loop._resolve_memory_services(services) is services


# ---------------------------------------------------------------------------
# 接线
# ---------------------------------------------------------------------------


class TestWiring:
    """接线覆盖：``_build_turn`` 预计算 → ``_run_turn`` 透传 → partial → prompt。

    这一组是本 WU 的存在理由所在。缺了它，删掉 ``_build_turn`` 里的赋值或
    ``_run_agent_loop`` 的透传，其余全部单测照样绿，特性会静默退回「只写不读」。
    """

    async def test_consolidator_receives_callable_not_value(self, tmp_path):
        """D5 前提：Consolidator 构造早于记忆装配，只能收延迟求值的 callable。"""
        loop = _enabled_loop(tmp_path)

        assert callable(loop.consolidator._working_memory_section_for_key)

    async def test_build_turn_computes_and_run_turn_forwards(self, tmp_path):
        services = _make_services(tmp_path)
        await _seed_focus(services, "feishu:c1", "接线验证")
        loop = _enabled_loop(tmp_path, memory_services=services)
        captured: dict[str, Any] = {}

        async def _fake_run_agent_loop(_transcript, **kwargs):
            captured.update(kwargs)
            return _stub_run_result()

        loop._run_agent_loop = _fake_run_agent_loop  # type: ignore[method-assign]

        await loop._dispatch(
            InboundMessage(
                channel="feishu", sender_id="u1", chat_id="c1", content="hi"
            )
        )

        assert "接线验证" in captured["working_memory_section"]

    async def test_section_reaches_the_system_prompt(self, tmp_path):
        """最后一跳：``_run_agent_loop`` 参数 → partial → ``build_system_prompt`` 文本。"""
        services = _make_services(tmp_path)
        await _seed_focus(services, "feishu:c1", "进 prompt 了")
        loop = _enabled_loop(tmp_path, memory_services=services)
        captured: dict[str, Any] = {}

        async def _fake_runner_run(spec):
            # partial 由 runner 经 ContextCompactionState 调起，这里复现同一次调用。
            captured["messages"] = list(spec.transcript_builder(spec.transcript_input))
            return _stub_run_result()

        loop.runner.run = _fake_runner_run  # type: ignore[method-assign]

        await loop._run_agent_loop(
            TranscriptInput(history=[], current_message="hi"),
            runtime=loop.llm_runtime(),
            working_memory_section=loop._compute_working_memory_section("feishu:c1"),
        )

        system_prompt = captured["messages"][0]["content"]
        assert "# Working Memory" in system_prompt
        assert "进 prompt 了" in system_prompt

    async def test_empty_section_reaches_prompt_as_no_block(self, tmp_path):
        """对照组：门控关时同一路径拿到空串，prompt 里没有工作记忆段。"""
        loop = _make_loop(tmp_path)
        captured: dict[str, Any] = {}

        async def _fake_runner_run(spec):
            captured["messages"] = list(spec.transcript_builder(spec.transcript_input))
            return _stub_run_result()

        loop.runner.run = _fake_runner_run  # type: ignore[method-assign]

        await loop._run_agent_loop(
            TranscriptInput(history=[], current_message="hi"),
            runtime=loop.llm_runtime(),
            working_memory_section=loop._compute_working_memory_section("feishu:c1"),
        )

        assert "# Working Memory" not in captured["messages"][0]["content"]
