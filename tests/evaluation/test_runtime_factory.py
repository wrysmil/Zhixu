"""T05：隔离运行环境与 SDK 适配。

覆盖 spec §8 / plan T05 的硬约束：先设配置上下文再构造 SDK、深复制 Config 只在内存注入凭据、
默认注册工具必须被显式 unregister（而不是只在界面隐藏）、fixture/live 两种工具模式、受控工具的错误
走项目 ``ToolResult.is_error``、固定时钟只影响可控工具、两个 attempt 的工作区/会话/记忆互不串，
以及日常用户数据目录零写入。

provider 一律用 ``MagicMock``：本文件是离线测试，不消耗真实模型调用（spec §11 区分口径）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from nanobot.agent.tools.base import ToolResult
from nanobot.config import loader
from nanobot.config.schema import Config
from nanobot.evaluation.fixtures import (
    CLOCK_TOKEN,
    ControlledClock,
    materialize_fixtures,
)
from nanobot.evaluation.runtime_factory import (
    ATTEMPT_SESSION_PREFIX,
    FORBIDDEN_TOOLS,
    create_attempt,
)
from nanobot.evaluation.schemas import AgentWorkerBoot, CaseWrite, JudgeWorkerBoot

from .conftest import make_case_payload

API_KEY = "sk-attempt-secret-4242"


def fake_provider() -> MagicMock:
    from nanobot.providers.base import GenerationSettings

    provider = MagicMock()
    provider.get_default_model.return_value = "eval-model"
    provider.generation = GenerationSettings()
    return provider


def profile_bundle(**extra: Any) -> dict[str, Any]:
    bundle: dict[str, Any] = {
        "config": {
            "agents": {"defaults": {"model": "eval-model", "provider": "openai"}},
            "providers": {"openai": {"apiKey": API_KEY}},
        }
    }
    bundle.update(extra)
    return bundle


def boot_payload(
    root: Path,
    *,
    attempt_id: str,
    case: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": "boot",
        "role": "agent",
        "protocol_version": 1,
        "attempt_id": attempt_id,
        "runtime_root": str(root / "runtime"),
        "workspace": str(root / "workspace"),
        "profile_bundle": profile if profile is not None else profile_bundle(),
        "case_snapshot": case if case is not None else make_case_payload(),
    }


def boot(root: Path, attempt_id: str, **kwargs: Any) -> AgentWorkerBoot:
    return AgentWorkerBoot.model_validate(boot_payload(root, attempt_id=attempt_id, **kwargs))


async def make_runtime(tmp_path: Path, attempt_id: str, **kwargs: Any):
    runtime = await create_attempt(
        boot(tmp_path, attempt_id, **kwargs), provider=fake_provider()
    )
    return runtime


@pytest.fixture(autouse=True)
def _restore_config_path():
    """create_attempt 会改全局配置上下文，测试之间必须还原，避免污染同进程的其他用例。"""
    previous = loader._current_config_path  # pyright: ignore[reportPrivateUsage]
    yield
    loader.set_config_path(previous)  # pyright: ignore[reportArgumentType]


def snapshot_tree(root: Path) -> dict[str, int]:
    if not root.exists():
        return {}
    return {
        str(item.relative_to(root)): item.stat().st_size
        for item in sorted(root.rglob("*"))
        if item.is_file()
    }


# ------------------------------------------------------------------ 配置与上下文


async def test_config_is_bound_to_attempt_root_and_never_written(tmp_path: Path) -> None:
    attempt_id = "3f1a2b4c-5d6e-7f80-91a2-b3c4d5e6f701"
    runtime = await make_runtime(tmp_path, attempt_id)

    assert runtime.config.runtime_data_dir == (tmp_path / "runtime").resolve()
    assert runtime.config.workspace_path == (tmp_path / "workspace").resolve()
    # 原始 boot 与合并后的配置都不落盘：凭据只在内存里。
    assert not (tmp_path / "runtime" / "config.json").exists()
    leaked = [
        path for path in tmp_path.rglob("*")
        if path.is_file() and API_KEY in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert leaked == []
    # 先设上下文再构造 SDK：任何按 get_config_path() 派生路径的代码都指向 attempt 目录。
    assert loader.get_config_path() == (tmp_path / "runtime" / "config.json").resolve()


async def test_profile_is_merged_into_a_deep_copy_of_the_base_config(tmp_path: Path) -> None:
    base = Config()
    base.agents.defaults.model = "user-own-model"
    base.tools.restrict_to_workspace = False
    before = base.model_dump(mode="json", by_alias=True)

    runtime = await create_attempt(
        boot(tmp_path, "3f1a2b4c-5d6e-7f80-91a2-b3c4d5e6f702"),
        provider=fake_provider(),
        base_config=base,
    )
    assert runtime.config is not base
    assert runtime.config.agents.defaults.model == "eval-model"
    assert base.model_dump(mode="json", by_alias=True) == before


async def test_case_limits_reach_the_sdk_config(tmp_path: Path) -> None:
    case = make_case_payload(execution={"tool_mode": "fixture", "allowed_tools": ["read_file"],
                                       "max_iterations": 3, "timeout_s": 30})
    runtime = await make_runtime(tmp_path, "3f1a2b4c-5d6e-7f80-91a2-b3c4d5e6f703", case=case)
    assert runtime.config.agents.defaults.max_tool_iterations == 3
    assert runtime.bot._loop.max_iterations == 3  # pyright: ignore[reportPrivateUsage]


# ------------------------------------------------------------------ 工具白名单


async def test_default_tools_are_unregistered_not_hidden(tmp_path: Path) -> None:
    runtime = await make_runtime(tmp_path, "3f1a2b4c-5d6e-7f80-91a2-b3c4d5e6f704")
    names = set(runtime.registry.tool_names)
    allowed = set(make_case_payload()["execution"]["allowed_tools"])

    assert names == allowed
    for name in names:
        assert runtime.registry.get(name) is not None
    # 配置层先关掉的（原生 shell / 自修改 / cli 应用）根本没进入注册表。
    assert not names & {"exec", "exec_session", "my", "run_cli_app"}
    assert runtime.capability["removed_tools"]  # 注册后才被显式注销的那批
    assert {"spawn", "message", "apply_patch"} <= set(runtime.capability["removed_tools"])
    assert not names & FORBIDDEN_TOOLS


def test_network_tool_in_fixture_mode_is_rejected_before_any_setup() -> None:
    with pytest.raises(ValidationError, match="TOOL_NOT_ALLOWED:web_fetch"):
        CaseWrite.model_validate(
            make_case_payload(
                execution={"tool_mode": "fixture",
                           "allowed_tools": ["read_file", "web_fetch"],
                           "max_iterations": 6, "timeout_s": 60}
            )
        )


async def test_network_tools_are_absent_in_fixture_mode_and_present_in_live(tmp_path: Path) -> None:
    live_case = make_case_payload(
        execution={"tool_mode": "live", "allowed_tools": ["read_file", "web_search", "web_fetch"],
                   "max_iterations": 6, "timeout_s": 60}
    )
    live = await make_runtime(
        tmp_path, "3f1a2b4c-5d6e-7f80-91a2-b3c4d5e6f705", case=live_case
    )
    assert {"web_search", "web_fetch"} <= set(live.registry.tool_names)
    assert live.capability["external_dependencies"] is True

    fixture = await make_runtime(tmp_path, "3f1a2b4c-5d6e-7f80-91a2-b3c4d5e6f706")
    assert "web_search" not in fixture.registry.tool_names
    assert fixture.capability["external_dependencies"] is False


async def test_judge_attempt_gets_an_empty_registry(tmp_path: Path) -> None:
    payload = {
        "type": "boot",
        "role": "judge",
        "protocol_version": 1,
        "attempt_id": "3f1a2b4c-5d6e-7f80-91a2-b3c4d5e6f707",
        "runtime_root": str(tmp_path / "runtime"),
        "workspace": str(tmp_path / "workspace"),
        "profile_bundle": profile_bundle(),
        "rubric": json.loads(json.dumps(make_case_payload()["rubric"] or {
            "version": "general-v1", "required": True,
            "dimensions": [{"id": "task_completion", "name": "任务完成",
                            "anchors": ["1", "2", "3", "4", "5"]}],
        })),
        "evidence": {"final_answer": "x"},
    }
    judge_boot = JudgeWorkerBoot.model_validate(payload)
    runtime = await create_attempt(judge_boot, provider=fake_provider())
    assert runtime.registry.tool_names == []
    assert runtime.capability["allowed_tools"] == []
    assert runtime.config.runtime_data_dir == (tmp_path / "runtime").resolve()


# ------------------------------------------------------------------ 受控工具


async def test_controlled_tool_consumes_fixtures_in_order(tmp_path: Path) -> None:
    case = make_case_payload(
        assertions=[{"id": "t1", "kind": "tool_called", "required": True,
                     "name": "test_lookup", "arguments_subset": {"city": "北京"},
                     "min_calls": 1, "max_calls": None}],
        tool_fixtures=[
            {"tool_name": "test_lookup", "arguments_match": {"city": "北京"},
             "result": {"temperature_c": 18}, "is_error": False},
            {"tool_name": "test_lookup", "arguments_match": {"city": "北京"},
             "result": {"temperature_c": 19}, "is_error": False},
        ],
        execution={"tool_mode": "fixture",
                   "allowed_tools": ["read_file", "test_lookup", "test_unstable"],
                   "max_iterations": 6, "timeout_s": 60},
    )
    runtime = await make_runtime(
        tmp_path, "3f1a2b4c-5d6e-7f80-91a2-b3c4d5e6f708", case=case
    )
    lookup = runtime.registry.get("test_lookup")
    assert lookup is not None

    first = json.loads(await lookup.execute(city="北京"))
    second = json.loads(await lookup.execute(city="北京"))
    assert first["temperature_c"] == 18 and second["temperature_c"] == 19

    third = await lookup.execute(city="北京")
    assert isinstance(third, ToolResult) and third.is_error


async def test_controlled_tool_error_uses_tool_result_is_error(tmp_path: Path) -> None:
    case = make_case_payload(
        assertions=[{"id": "t1", "kind": "tool_called", "required": True,
                     "name": "test_unstable", "arguments_subset": {},
                     "min_calls": 1, "max_calls": None}],
        tool_fixtures=[
            {"tool_name": "test_unstable", "arguments_match": {},
             "result": "upstream refused the request", "is_error": True},
        ],
        execution={"tool_mode": "fixture", "allowed_tools": ["test_unstable"],
                   "max_iterations": 6, "timeout_s": 60},
    )
    runtime = await make_runtime(
        tmp_path, "3f1a2b4c-5d6e-7f80-91a2-b3c4d5e6f709", case=case
    )
    tool = runtime.registry.get("test_unstable")
    assert tool is not None
    result = await tool.execute(any_value="1")
    assert isinstance(result, ToolResult)
    assert result.is_error is True
    # 项目口径：错误必须由结构化标志表达，文本前缀只是附带信息。
    assert "upstream refused" in str(result)


async def test_arguments_mismatch_is_a_controlled_error(tmp_path: Path) -> None:
    case = make_case_payload(
        tool_fixtures=[
            {"tool_name": "test_lookup", "arguments_match": {"city": "北京"},
             "result": {"temperature_c": 18}, "is_error": False},
        ],
        execution={"tool_mode": "fixture", "allowed_tools": ["test_lookup"],
                   "max_iterations": 6, "timeout_s": 60},
    )
    runtime = await make_runtime(
        tmp_path, "3f1a2b4c-5d6e-7f80-91a2-b3c4d5e6f70a", case=case
    )
    tool = runtime.registry.get("test_lookup")
    assert tool is not None
    result = await tool.execute(city="上海")
    assert isinstance(result, ToolResult) and result.is_error
    assert "TOOL_FIXTURE_NOT_MATCHED" in str(result)


async def test_fixed_clock_only_reaches_controlled_tools(tmp_path: Path) -> None:
    clock_iso = "2026-10-15T09:30:00+08:00"
    case = make_case_payload(
        clock_iso=clock_iso,
        tool_fixtures=[
            {"tool_name": "test_lookup", "arguments_match": {},
             "result": {"as_of": CLOCK_TOKEN, "temperature_c": 18}, "is_error": False},
        ],
        execution={"tool_mode": "fixture", "allowed_tools": ["test_lookup"],
                   "max_iterations": 6, "timeout_s": 60},
    )
    runtime = await make_runtime(
        tmp_path, "3f1a2b4c-5d6e-7f80-91a2-b3c4d5e6f70b", case=case
    )
    tool = runtime.registry.get("test_lookup")
    assert tool is not None
    payload = json.loads(await tool.execute(city="北京"))
    assert payload["as_of"] == clock_iso

    # 进程真实时钟不能被改：固定时钟只是可控工具的输出内容。
    assert datetime.now(timezone.utc).year <= 2027


def test_controlled_clock_without_case_clock_uses_real_time() -> None:
    clock = ControlledClock(None)
    assert clock.now_iso().startswith(str(datetime.now(timezone.utc).year))
    fixed = ControlledClock("2026-01-02T03:04:05+00:00")
    assert fixed.now_iso() == "2026-01-02T03:04:05+00:00"
    assert fixed.render({"nested": [CLOCK_TOKEN]}) == {"nested": ["2026-01-02T03:04:05+00:00"]}


# ------------------------------------------------------------------ 隔离


async def test_two_attempts_isolate_workspace_session_and_memory(tmp_path: Path) -> None:
    first_root = tmp_path / "a"
    second_root = tmp_path / "b"
    first_id = "3f1a2b4c-5d6e-7f80-91a2-b3c4d5e6f70c"
    second_id = "3f1a2b4c-5d6e-7f80-91a2-b3c4d5e6f70d"

    first = await create_attempt(
        boot(
            first_root, first_id,
            case=make_case_payload(
                fixtures=[{"path": "input.txt", "content": "alpha\n", "encoding": "utf-8"}],
                memory_seed=[{"kind": "semantic", "content": "只有第一个 attempt 知道的标识 alpha"}],
            ),
        ),
        provider=fake_provider(),
    )
    second = await create_attempt(
        boot(
            second_root, second_id,
            case=make_case_payload(
                fixtures=[{"path": "input.txt", "content": "bravo\n", "encoding": "utf-8"}],
                memory_seed=[{"kind": "semantic", "content": "只有第二个 attempt 知道的标识 bravo"}],
            ),
        ),
        provider=fake_provider(),
    )

    # 同一相对路径写入不同值：各自读到自己的内容。
    assert (first.paths.workspace / "input.txt").read_text(encoding="utf-8") == "alpha\n"
    assert (second.paths.workspace / "input.txt").read_text(encoding="utf-8") == "bravo\n"

    assert "alpha" in first.bot.memory.read() and "bravo" not in first.bot.memory.read()
    assert "bravo" in second.bot.memory.read() and "alpha" not in second.bot.memory.read()

    assert first.session_key == f"{ATTEMPT_SESSION_PREFIX}{first_id}"
    assert first.session_key != second.session_key
    # 会话必须落在各自 attempt 的 runtime root 下（只换 workspace 是不够的）。
    assert first.paths.sessions_dir == (first_root / "runtime" / "sessions").resolve()
    for runtime, root in ((first, first_root), (second, second_root)):
        loop_dir = runtime.bot._loop.sessions.sessions_dir.resolve()  # pyright: ignore[reportPrivateUsage]
        assert loop_dir.is_relative_to((root / "runtime").resolve())
    assert first.bot._loop.sessions.sessions_dir != second.bot._loop.sessions.sessions_dir

    await first.aclose()
    await second.aclose()


async def test_daily_user_data_directory_gets_no_evaluation_writes(tmp_path: Path) -> None:
    home_dir = Path.home() / ".nanobot"
    before = snapshot_tree(home_dir)

    runtime = await make_runtime(tmp_path, "3f1a2b4c-5d6e-7f80-91a2-b3c4d5e6f70e")
    (runtime.paths.workspace / "notes.txt").write_text("x", encoding="utf-8")
    await runtime.aclose()

    after = snapshot_tree(home_dir)
    assert after == before


def test_materialize_fixtures_rejects_paths_outside_workspace(tmp_path: Path) -> None:
    from nanobot.evaluation.schemas import Fixture

    escaping = Fixture.model_construct(
        path="../../escape.txt", content="x", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="PATH_OUTSIDE_ROOT"):
        materialize_fixtures(tmp_path / "workspace", [escaping])

    nested = Fixture.model_construct(path="data/sales.csv", content="a,b\n", encoding="utf-8")
    written = materialize_fixtures(tmp_path / "workspace", [nested])
    assert written == ["data/sales.csv"]
    assert (tmp_path / "workspace" / "data" / "sales.csv").is_file()


# ------------------------------------------------------------------ 清理


async def test_attempt_close_releases_the_loop(tmp_path: Path) -> None:
    runtime = await make_runtime(tmp_path, "3f1a2b4c-5d6e-7f80-91a2-b3c4d5e6f70f")
    loop = runtime.bot._loop  # pyright: ignore[reportPrivateUsage]
    closed: list[str] = []
    original = loop.aclose

    async def _spy() -> None:
        closed.append("closed")
        await original()

    loop.aclose = _spy  # pyright: ignore[reportIncompatibleMethodOverride]
    await runtime.aclose()
    assert closed == ["closed"]
    assert runtime.closed is True
    # 复用现有 hook factories，而不是自己另搭一套构造链。
    assert loop._hook_factories  # pyright: ignore[reportPrivateUsage]
