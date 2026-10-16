"""T05：用例文件种子与受控工具（fixture 模式的工具侧）。

契约 §2 把可控能力限定成两个内置工具 ``test_lookup`` / ``test_unstable``，并规定：

- 命中**按声明顺序消费**，每条 fixture 只用一次（否则"失败恢复"用例会被同一条结果永远满足）；
- 未命中返回**固定受控错误**，用项目 ``ToolResult.is_error`` 表达，而不是把 ``Error`` 写进普通文本；
- ``clock_iso`` 只影响可控工具的输出内容，进程真实时钟不受任何影响（不做全局 monkeypatch，
  否则被测 Agent 的时间相关代码路径就不是它平时的样子了）。

产物路径由 :func:`materialize_fixtures` 走 ``resolve_inside``：即使某个 ``Fixture`` 绕过了
模型层校验，越界路径在这里仍然必须失败。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.evaluation.schemas import Fixture, ToolFixture
from nanobot.evaluation.validation import resolve_inside

#: 可控工具结果里的固定时钟占位符；只有本模块会替换它。
CLOCK_TOKEN = "{{clock}}"
TOOL_FIXTURE_NOT_MATCHED = "TOOL_FIXTURE_NOT_MATCHED"
CONTROLLED_TOOL_NAMES: frozenset[str] = frozenset({"test_lookup", "test_unstable"})

_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": True,
}

_DESCRIPTIONS = {
    "test_lookup": (
        "受控查询工具：结果由测试用例预先声明，不访问网络或外部服务。"
        "参数会与用例声明的 arguments 匹配后按顺序返回固定结果。"
    ),
    "test_unstable": (
        "受控不稳定工具：用于复现工具失败与恢复路径，结果（含错误）由测试用例预先声明。"
    ),
}


@dataclass(frozen=True)
class ControlledClock:
    """用例声明的固定时钟；``None`` 时如实返回当前 UTC 时间。"""

    clock_iso: str | None

    def now_iso(self) -> str:
        if self.clock_iso:
            return self.clock_iso
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def render(self, value: Any) -> Any:
        """把结果里的 :data:`CLOCK_TOKEN` 替换成固定时钟；只作用于可控工具的输出。"""
        if isinstance(value, str):
            return value.replace(CLOCK_TOKEN, self.now_iso())
        if isinstance(value, list):
            return [self.render(item) for item in value]
        if isinstance(value, dict):
            return {key: self.render(item) for key, item in value.items()}
        return value


@dataclass
class _Entry:
    fixture: ToolFixture
    consumed: bool = False


@dataclass
class FixtureTable:
    """受控工具结果表：按声明顺序消费，命中一次即标记已用。"""

    clock: ControlledClock = field(default_factory=lambda: ControlledClock(None))
    _entries: list[_Entry] = field(default_factory=list)

    @classmethod
    def from_fixtures(
        cls, fixtures: list[ToolFixture], *, clock: ControlledClock | None = None
    ) -> FixtureTable:
        return cls(clock=clock or ControlledClock(None), _entries=[_Entry(item) for item in fixtures])

    def __len__(self) -> int:
        return len(self._entries)

    def take(self, tool_name: str, arguments: dict[str, Any]) -> ToolFixture | None:
        for entry in self._entries:
            if entry.consumed or entry.fixture.tool_name != tool_name:
                continue
            if _matches(entry.fixture.arguments_match, arguments):
                entry.consumed = True
                return entry.fixture
        return None


def _matches(expected: dict[str, Any], arguments: dict[str, Any]) -> bool:
    return all(
        key in arguments and arguments[key] == value for key, value in expected.items()
    )


def _as_tool_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@tool_parameters(_TOOL_PARAMETERS)
class ControlledTool(Tool):
    """``test_lookup`` / ``test_unstable`` 的共用实现：只查表，不触网。"""

    def __init__(self, tool_name: str, table: FixtureTable) -> None:
        self._tool_name = tool_name
        self._table = table

    @property
    def name(self) -> str:
        return self._tool_name

    @property
    def description(self) -> str:
        return _DESCRIPTIONS[self._tool_name]

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        fixture = self._table.take(self._tool_name, dict(kwargs))
        if fixture is None:
            return ToolResult.error(
                f"{TOOL_FIXTURE_NOT_MATCHED}: no declared result left for "
                f"{self._tool_name}({sorted(kwargs)})"
            )
        text = _as_tool_text(self._clock().render(fixture.result))
        if fixture.is_error:
            # 项目口径：工具失败必须是结构化 is_error，Agent 的失败恢复才有依据。
            return ToolResult.error(text)
        return text

    def _clock(self) -> ControlledClock:
        return self._table.clock


def register_controlled_tools(
    registry: Any, table: FixtureTable, wanted: list[str]
) -> list[str]:
    """只注册用例白名单里出现的受控工具，返回实际注册名。"""
    added: list[str] = []
    for name in wanted:
        if name not in CONTROLLED_TOOL_NAMES or registry.has(name):
            continue
        registry.register(ControlledTool(name, table))
        added.append(name)
    return added


def materialize_fixtures(workspace: Path, fixtures: list[Fixture]) -> list[str]:
    """把文件种子写进本 attempt 的工作区，越界路径一律拒绝。"""
    written: list[str] = []
    for fixture in fixtures:
        target = resolve_inside(workspace, fixture.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(fixture.content, encoding=fixture.encoding)
        written.append(fixture.path)
    return written


def seed_memory(memory: Any, seeds: list[Any], *, session_key: str) -> int:
    """记忆种子只进入当前 attempt 的记忆库；返回写入条数。"""
    if not seeds:
        return 0
    existing = memory.read().rstrip()
    lines = [f"- [eval:{session_key}] {seed.content}" for seed in seeds]
    memory.write("\n".join([existing, *lines]) if existing else "\n".join(lines) + "\n")
    return len(lines)
