"""WU-A Task 4：``AgentDefaults.memory_idle_seconds`` 与 ``MemoryExtractionHook`` override 测试。

覆盖:
- ``AgentDefaults().memory_idle_seconds`` 默认值 120。
- ``memory_idle_seconds`` 可被显式赋值并被序列化(驼峰别名)。
- ``MemoryExtractionHook(idle_seconds=...)`` 覆盖 ``IDLE_THRESHOLD_SECONDS`` 实例属性。
- 默认构造时 ``IDLE_THRESHOLD_SECONDS`` 仍是类属性 120.0。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.agent.hooks.memory_extraction import MemoryExtractionHook
from nanobot.config.schema import AgentDefaults, Config


class TestAgentDefaultsMemoryIdleSeconds:
    def test_default_value_is_120(self):
        """新实例默认 ``memory_idle_seconds=120``(2 分钟)。"""
        defaults = AgentDefaults()
        assert defaults.memory_idle_seconds == 120

    def test_explicit_override_persists(self):
        defaults = AgentDefaults(memory_idle_seconds=120)
        assert defaults.memory_idle_seconds == 120

    def test_zero_or_negative_is_rejected(self):
        """``ge=1`` 约束:0 或负值必须被 Pydantic 拒绝。"""
        with pytest.raises(Exception):
            AgentDefaults(memory_idle_seconds=0)
        with pytest.raises(Exception):
            AgentDefaults(memory_idle_seconds=-1)

    def test_serialization_uses_camel_alias(self):
        defaults = AgentDefaults(memory_idle_seconds=300)
        dumped = defaults.model_dump(by_alias=True)
        assert dumped["memoryIdleSeconds"] == 300

    def test_load_from_camel_alias(self):
        """JSON 配置驼峰 ``memoryIdleSeconds`` 必须被识别。"""
        raw = {"memoryIdleSeconds": 900}
        defaults = AgentDefaults.model_validate(raw)
        assert defaults.memory_idle_seconds == 900

    def test_full_config_load(self):
        """通过顶层 ``Config`` 加载时也工作。"""
        config = Config()
        assert config.agents.defaults.memory_idle_seconds == 120


class TestMemoryExtractionHookIdleOverride:
    def test_class_attribute_default_is_120(self):
        """``MemoryExtractionHook.IDLE_THRESHOLD_SECONDS`` 默认 120.0。"""
        assert MemoryExtractionHook.IDLE_THRESHOLD_SECONDS == 120.0

    def test_init_param_overrides_instance_attribute(self, tmp_path: Path):
        """``idle_seconds=0.05`` 必须把实例属性覆写。"""
        from nanobot.agent.hooks.memory_extraction import (
            _PENDING_IDLE_TIMERS,
        )

        try:
            hook = MemoryExtractionHook(
                extractor=_FakeExtractor(),
                session_key="s_idle_cfg",
                scratchpad_writer=_FakeWriter(),
                idle_seconds=0.05,
            )
            assert hook.IDLE_THRESHOLD_SECONDS == 0.05
            # 类属性不变,避免污染其他测试。
            assert MemoryExtractionHook.IDLE_THRESHOLD_SECONDS == 120.0
        finally:
            # 清理可能残留的 idle 任务。
            for t in list(_PENDING_IDLE_TIMERS.values()):
                if not t.done():
                    t.cancel()
            _PENDING_IDLE_TIMERS.clear()

    def test_no_idle_seconds_keeps_default(self):
        """不传 ``idle_seconds`` 时,实例属性等于类属性(120.0)。"""
        hook = MemoryExtractionHook(
            extractor=_FakeExtractor(),
            session_key="s_idle_default",
            scratchpad_writer=_FakeWriter(),
        )
        assert hook.IDLE_THRESHOLD_SECONDS == 120.0


# ---------------------------------------------------------------------------
# 本地 fakes(避免循环 import tests/memory/* 的测试夹具)
# ---------------------------------------------------------------------------


class _FakeExtractor:
    """最小化 fake: hook 不会在测试期间实际触发 idle 路径。"""

    async def run_idle_extraction(self, _session: object) -> None:
        return None

    async def extract_session(self, _session: object, *, source: str = "session_end") -> None:
        return None

    async def extract_incremental(self, _session: object, _idx: int) -> None:
        return None


class _FakeWriter:
    async def update_focus(self, _focus: str) -> None:
        return None
