"""T06：Worker 编排（``run_worker`` / CLI 帧协议）。

这里全部使用离线 provider 与受控 SDK：验证的是**帧顺序、seq 唯一来源、终态与证据密封**，
不是模型能力。三条关键口径：

- 父进程收到的每条 Trace 帧都必须能在磁盘上读到同一个 ``seq``（``TraceCollector`` 是唯一
  编号者），否则判分侧的 ``trace:<attempt>:<seq>`` 引用会指向不存在的证据。
- 超时/取消/异常都要先清理再落终态，多项错误按发生顺序保留，不能相互覆盖。
- result 帧之外的 stdout 必须干净；凭据既不能进帧也不能进盘。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from nanobot.agent.hook import AgentHookContext
from nanobot.agent.tools.base import ToolResult
from nanobot.config import loader
from nanobot.config.schema import Config
from nanobot.evaluation import worker as worker_mod
from nanobot.evaluation.evidence import EvidenceArtifact, build_attempt_evidence
from nanobot.evaluation.fixtures import ControlledClock
from nanobot.evaluation.graders.rules import evaluate_rules
from nanobot.evaluation.graders.verdict import derive_verdict
from nanobot.evaluation.ipc import ParentIpc, read_frame_lines
from nanobot.evaluation.runtime_factory import AttemptPaths, AttemptRuntime
from nanobot.evaluation.schemas import (
    AgentWorkerBoot,
    AgentWorkerResult,
    JudgeWorkerBoot,
    TraceEvent,
)
from nanobot.evaluation.trace import read_trace
from nanobot.evaluation.worker import (
    REDACTED,
    WorkerChannel,
    WorkerError,
    evidence_from_payload,
    main,
    read_boot_line,
    run_agent_worker,
    run_judge_worker,
)
from nanobot.llm_usage.models import LLMCallRecord
from nanobot.providers.base import GenerationSettings, LLMUsage
from nanobot.sdk.types import RunResult

from .conftest import make_case_payload, make_rubric_payload

API_KEY = "sk-worker-secret-7777"


class StubProvider:
    """只提供 worker 真正依赖的表面：唯一的 observer 槽位。

    用真实类而不是 MagicMock：``set_llm_call_observer`` 的语义是「只有一个槽位、后装覆盖前装」，
    mock 会把它退化成任意属性赋值，测不出串接是否真的生效。
    """

    def __init__(self) -> None:
        self.observer: Callable[[LLMCallRecord], None] | None = None
        self.generation = GenerationSettings()

    def get_default_model(self) -> str:
        return "eval-model"

    def set_llm_call_observer(self, observer: Callable[[LLMCallRecord], None] | None) -> None:
        self.observer = observer

    def emit(self, record: Any) -> None:
        """模拟 provider 完成一次物理调用后回调 observer。"""
        if self.observer is not None:
            self.observer(record)


def fake_provider() -> StubProvider:
    return StubProvider()


def make_usage(input_tokens: int, output_tokens: int) -> LLMUsage:
    """reported 与 estimated 必须恰好划分 total，否则 ``LLMUsage.__post_init__`` 直接拒收。"""
    total = input_tokens + output_tokens
    return LLMUsage(input_tokens, output_tokens, total, reported_tokens=total)


def profile_bundle() -> dict[str, Any]:
    return {
        "config": {
            "agents": {"defaults": {"model": "eval-model", "provider": "openai"}},
            "providers": {"openai": {"apiKey": API_KEY}},
        }
    }


def agent_boot(root: Path, attempt_id: str, **overrides: Any) -> AgentWorkerBoot:
    payload: dict[str, Any] = {
        "type": "boot",
        "role": "agent",
        "protocol_version": 1,
        "attempt_id": attempt_id,
        "runtime_root": str(root / "runtime"),
        "workspace": str(root / "workspace"),
        "profile_bundle": profile_bundle(),
        "case_snapshot": make_case_payload(),
    }
    payload.update(overrides)
    return AgentWorkerBoot.model_validate(payload)


class StubRegistry:
    def __init__(self, names: list[str]) -> None:
        self.tool_names = names


class StubBot:
    """只实现 worker 用到的 SDK 表面：有序 turns + ``aclose``。

    ``script`` 每项描述一次 turn：``content``/``stop_reason``/``error``/``iterations``/
    ``tools``（``(name, ToolResult|str)``）/``bad_record``。工具生命周期与 ``LLMCallRecord``
    都由这里主动推给 hook 与 provider observer，等价于真实 runner 的调用序列。
    """

    def __init__(self, script: list[dict[str, Any]], *, provider: Any) -> None:
        self._script = script
        self._provider = provider
        self.calls: list[tuple[str, str]] = []
        self.closed = 0
        self.session_keys: list[str] = []

    async def aclose(self) -> None:
        self.closed += 1

    async def run(
        self, message: str, *, session_key: str = "sdk:default", hooks: list[Any] | None = None
    ) -> RunResult:
        self.calls.append((message, session_key))
        self.session_keys.append(session_key)
        hook = (hooks or [None])[0]
        spec = self._script[len(self.calls) - 1]
        iterations = int(spec.get("iterations", 1))
        usage = spec.get("usage")
        for iteration in range(iterations):
            context = AgentHookContext(
                iteration=iteration,
                messages=[{"role": "user", "content": message}],
            )
            if hook is not None:
                await hook.before_iteration(context)
            for name, result in spec.get("tools", []):
                tool_call = _ToolCall(id=f"call-{iteration}-{name}", name=name, arguments={"q": name})
                if hook is not None:
                    await hook.before_execute_tool(context, tool_call, None, tool_call.arguments)
                    if isinstance(result, ToolResult) and result.is_error:
                        await hook.on_execute_tool_error(
                            context, tool_call, None, tool_call.arguments, result
                        )
                    else:
                        await hook.after_execute_tool(
                            context, tool_call, None, tool_call.arguments, result
                        )
            if hook is not None:
                await hook.after_iteration(context)
            # 每次迭代对应一次物理调用：observer 是账本与 model.call_completed 的唯一来源。
            self._provider.emit(
                "not-a-record"
                if spec.get("bad_record")
                else LLMCallRecord(
                    started_at_ms=0,
                    duration_ms=5,
                    provider="openai",
                    model="eval-model",
                    source="user",
                    stream=False,
                    finish_reason="stop",
                    usage=usage if usage is not None else make_usage(10, 2),
                )
            )
        return RunResult(
            content=spec.get("content", ""),
            stop_reason=spec.get("stop_reason", "stop"),
            error=spec.get("error"),
            usage=usage if usage is not None else make_usage(10, 2),
        )


class _ToolCall:
    def __init__(self, *, id: str, name: str, arguments: dict[str, Any]) -> None:
        self.id = id
        self.name = name
        self.arguments = arguments


class _StubLoop:
    def __init__(self, provider: Any) -> None:
        self.provider = provider


def stub_runtime(
    *,
    attempt_id: str,
    root: Path,
    script: list[dict[str, Any]],
    provider: Any,
    role: str = "agent",
    allowed: list[str] | None = None,
) -> AttemptRuntime:
    paths = AttemptPaths(
        attempt_id=attempt_id,
        root=root,
        runtime_root=root / "runtime",
        workspace=root / "workspace",
        config_path=root / "runtime" / "config.json",
        sessions_dir=root / "runtime" / "sessions",
        trace_dir=root / "trace",
        artifact_dir=root / "artifacts",
        log_path=root / "worker.log",
    )
    paths.create()
    config = Config()
    config.bind_source_path(paths.config_path)
    bot = StubBot(script, provider=provider)
    registry = StubRegistry(allowed or ["read_file"])
    runtime = AttemptRuntime(
        attempt_id=attempt_id,
        role=role,
        paths=paths,
        config=config,
        bot=bot,  # pyright: ignore[reportArgumentType]
        loop=_StubLoop(provider),  # pyright: ignore[reportArgumentType]
        registry=registry,  # pyright: ignore[reportArgumentType]
        capability={
            "role": role,
            "model": "eval-model",
            "provider": "openai",
            "tool_mode": "fixture",
            "allowed_tools": allowed or ["read_file"],
        },
        session_key=f"eval:{attempt_id}",
        clock=ControlledClock(clock_iso=None),
    )
    return runtime


def make_channel(attempt_id: str) -> tuple[WorkerChannel, list[bytes]]:
    written: list[bytes] = []
    channel = WorkerChannel(attempt_id=attempt_id, sink=written.append)
    return channel, written


@pytest.fixture(autouse=True)
def _restore_config_path():
    previous = loader._current_config_path  # pyright: ignore[reportPrivateUsage]
    yield
    loader.set_config_path(previous)  # pyright: ignore[reportArgumentType]


def patch_create_attempt(monkeypatch: pytest.MonkeyPatch, runtime: AttemptRuntime) -> None:
    """注入桩 runtime：observer 安装仍走 worker 真实代码路径，只替换 SDK 构造。"""

    async def _create(boot: Any, **kwargs: Any) -> AttemptRuntime:
        return runtime

    monkeypatch.setattr(worker_mod, "create_attempt", _create)


def frames_of(written: list[bytes]) -> list[dict[str, Any]]:
    return read_frame_lines(b"".join(written))


# --------------------------------------------------------------------------- 帧顺序


async def test_ready_then_traces_then_result_and_parent_accepts_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_id = str(uuid.uuid4())
    provider = fake_provider()
    runtime = stub_runtime(
        attempt_id=attempt_id,
        root=tmp_path,
        script=[{"content": "alpha", "tools": [("read_file", ToolResult("alpha\n"))]}],
        provider=provider,
    )
    patch_create_attempt(monkeypatch, runtime)
    channel, written = make_channel(attempt_id)

    result = await run_agent_worker(agent_boot(tmp_path, attempt_id), channel=channel)

    frames = frames_of(written)
    assert frames[0]["type"] == "ready"
    assert frames[0]["protocol_version"] == 1
    assert frames[-1]["type"] == "result"
    assert {frame["attempt_id"] for frame in frames} == {attempt_id}
    assert isinstance(result, AgentWorkerResult)
    assert result.execution_status == "succeeded"
    assert result.final_answer == "alpha"

    parent = ParentIpc(attempt_id=attempt_id)
    for frame in frames:
        parent.accept(frame)
    assert parent.ready_received is True
    assert parent.can_seal_succeeded(exit_code=0) is True


async def test_every_forwarded_event_exists_on_disk_with_same_seq(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IPC 与落盘共用同一个 seq 源，父进程的 evidence_ref 不可能指向缺行。"""
    attempt_id = str(uuid.uuid4())
    runtime = stub_runtime(
        attempt_id=attempt_id,
        root=tmp_path,
        script=[{"content": "one"}, {"content": "two"}],
        provider=fake_provider(),
    )
    patch_create_attempt(monkeypatch, runtime)
    channel, written = make_channel(attempt_id)

    await run_agent_worker(
        agent_boot(
            tmp_path,
            attempt_id,
            case_snapshot=make_case_payload(
                turns=[{"content": "第一问"}, {"content": "第二问"}]
            ),
        ),
        channel=channel,
    )

    frames = frames_of(written)
    forwarded = [frame["event"] for frame in frames if frame["type"] == "trace"]
    stored = read_trace(runtime.paths.trace_dir)
    assert stored.sealed is True
    assert stored.trace_complete is True
    assert [event.seq for event in stored.events] == [frame["seq"] for frame in forwarded]
    assert [str(event.event_id) for event in stored.events] == [
        frame["event_id"] for frame in forwarded
    ]
    assert stored.events[0].type == "attempt.started"
    assert stored.events[-1].type == "attempt.completed"
    assert {event.type for event in stored.events} >= {
        "attempt.started",
        "turn.started",
        "iteration.started",
        "model.request_summary",
        "model.call_completed",
        "iteration.completed",
        "turn.completed",
        "attempt.completed",
    }
    # turns 顺序、session_key 与会话隔离口径
    assert runtime.bot.calls == [("第一问", f"eval:{attempt_id}"), ("第二问", f"eval:{attempt_id}")]  # pyright: ignore[reportGeneralTypeIssues]


# --------------------------------------------------------------------------- 用量账本


async def test_physical_calls_are_the_only_usage_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_id = str(uuid.uuid4())
    runtime = stub_runtime(
        attempt_id=attempt_id,
        root=tmp_path,
        # 两次迭代各自上报 10/2/12，但 SDK 的 round usage 只有最后一次 —— 账本不得相加两轮。
        script=[{"content": "x", "iterations": 2, "usage": make_usage(10, 2)}],
        provider=fake_provider(),
    )
    patch_create_attempt(monkeypatch, runtime)
    channel, _ = make_channel(attempt_id)

    result = await run_agent_worker(agent_boot(tmp_path, attempt_id), channel=channel)

    assert result.usage.physical_requests == 2
    assert result.usage.total_tokens == 24
    assert result.usage.iterations == 2
    assert result.usage.source == "reported"
    assert result.usage.coverage == "complete"
    events = [event for event in read_trace(runtime.paths.trace_dir).events]
    calls = [event for event in events if event.type == "model.call_completed"]
    assert [call.payload["role"] for call in calls] == ["agent", "agent"]
    assert [call.payload["scope"] for call in calls] == ["main", "main"]


async def test_observer_failure_becomes_a_recorded_error_not_a_silent_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """provider 侧 fail-open 会吞掉 observer 异常，账本必须靠 sticky 错误把它送回终态。"""
    attempt_id = str(uuid.uuid4())
    runtime = stub_runtime(
        attempt_id=attempt_id,
        root=tmp_path,
        # observer 收到非 LLMCallRecord 对象：账本的 _append 会拒收并抛错。
        script=[{"content": "x", "bad_record": True}],
        provider=fake_provider(),
    )
    patch_create_attempt(monkeypatch, runtime)
    channel, _ = make_channel(attempt_id)

    result = await run_agent_worker(agent_boot(tmp_path, attempt_id), channel=channel)

    assert result.usage.physical_requests == 0
    assert result.usage.coverage == "unavailable"
    failure = next(error for error in result.errors if error.code == "USAGE_OBSERVER_FAILED")
    assert failure.phase == "execution"
    assert "USAGE_RECORD_INVALID" in failure.message


# --------------------------------------------------------------------------- 工具证据


async def test_tool_error_uses_structured_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_id = str(uuid.uuid4())
    runtime = stub_runtime(
        attempt_id=attempt_id,
        root=tmp_path,
        script=[{"content": "nope", "tools": [("read_file", ToolResult.error("Error: missing"))]}],
        provider=fake_provider(),
    )
    patch_create_attempt(monkeypatch, runtime)
    channel, written = make_channel(attempt_id)

    await run_agent_worker(agent_boot(tmp_path, attempt_id), channel=channel)

    events = [frame["event"] for frame in frames_of(written) if frame["type"] == "trace"]
    started = next(event for event in events if event["type"] == "tool.started")
    failed = next(event for event in events if event["type"] == "tool.failed")
    assert started["payload"]["result"] is None
    assert started["payload"]["is_error"] is None
    assert failed["payload"]["is_error"] is True
    assert "missing" in failed["payload"]["result"]


# --------------------------------------------------------------------------- 终态与清理


async def test_timeout_clears_up_first_then_reports_timed_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_id = str(uuid.uuid4())
    runtime = stub_runtime(
        attempt_id=attempt_id, root=tmp_path, script=[{"content": "x"}], provider=fake_provider()
    )
    patch_create_attempt(monkeypatch, runtime)
    case = make_case_payload(execution={
        "tool_mode": "fixture",
        "allowed_tools": ["read_file"],
        "max_iterations": 4,
        "timeout_s": 5,
    })
    boot = agent_boot(tmp_path, attempt_id, case_snapshot=case)
    # 用 0.01s 覆盖配置层下限，专测超时分支而不是等 5 秒。
    boot.case_snapshot.execution.timeout_s = 0.01  # pyright: ignore[reportGeneralTypeIssues]

    async def slow_run(*args: Any, **kwargs: Any) -> RunResult:
        await asyncio.sleep(0.2)
        return RunResult(content="late")

    runtime.bot.run = slow_run  # pyright: ignore[reportGeneralTypeIssues]
    channel, _ = make_channel(attempt_id)

    result = await run_agent_worker(boot, channel=channel)

    assert result.execution_status == "timed_out"
    assert [error.code for error in result.errors] == ["WORKER_TIMED_OUT"]
    assert runtime.bot.closed == 1  # pyright: ignore[reportGeneralTypeIssues]
    stored = read_trace(runtime.paths.trace_dir)
    assert stored.sealed is True
    assert stored.events[-1].type == "attempt.failed"


async def test_cancellation_reports_cancelled_and_still_emits_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_id = str(uuid.uuid4())
    runtime = stub_runtime(
        attempt_id=attempt_id, root=tmp_path, script=[{"content": "x"}], provider=fake_provider()
    )
    patch_create_attempt(monkeypatch, runtime)

    async def cancelled_run(*args: Any, **kwargs: Any) -> RunResult:
        raise asyncio.CancelledError

    runtime.bot.run = cancelled_run  # pyright: ignore[reportGeneralTypeIssues]
    channel, written = make_channel(attempt_id)

    result = await run_agent_worker(agent_boot(tmp_path, attempt_id), channel=channel)

    assert result.execution_status == "cancelled"
    assert result.errors[0].code == "WORKER_CANCELLED"
    assert frames_of(written)[-1]["type"] == "result"


async def test_setup_failure_emits_result_without_any_trace_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_id = str(uuid.uuid4())

    async def broken_create(boot: Any, **kwargs: Any) -> AttemptRuntime:
        raise ValueError("SANDBOX_VIOLATION: exec")

    monkeypatch.setattr(worker_mod, "create_attempt", broken_create)
    channel, written = make_channel(attempt_id)

    result = await run_agent_worker(agent_boot(tmp_path, attempt_id), channel=channel)

    assert result.execution_status == "setup_error"
    assert result.errors[0].code == "SANDBOX_VIOLATION"
    assert result.errors[0].phase == "setup"
    assert result.trace_complete is False
    frames = frames_of(written)
    assert [frame["type"] for frame in frames] == ["ready", "result"]


async def test_cleanup_failure_is_kept_alongside_execution_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多项错误按发生顺序追加：清理失败不得覆盖执行失败。"""
    attempt_id = str(uuid.uuid4())
    runtime = stub_runtime(
        attempt_id=attempt_id, root=tmp_path, script=[{"content": "x"}], provider=fake_provider()
    )
    patch_create_attempt(monkeypatch, runtime)

    async def failing_run(*args: Any, **kwargs: Any) -> RunResult:
        raise RuntimeError("provider exploded")

    async def failing_close() -> None:
        raise RuntimeError("provider connection stuck")

    runtime.bot.run = failing_run  # pyright: ignore[reportGeneralTypeIssues]
    runtime.bot.aclose = failing_close  # pyright: ignore[reportGeneralTypeIssues]
    channel, _ = make_channel(attempt_id)

    result = await run_agent_worker(agent_boot(tmp_path, attempt_id), channel=channel)

    phases = [(error.phase, error.code) for error in result.errors]
    assert phases[0] == ("execution", "RuntimeError")
    assert "provider exploded" in result.errors[0].message
    assert ("cleanup", "RuntimeError") in phases


async def test_turn_error_is_recorded_but_does_not_stop_later_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_id = str(uuid.uuid4())
    runtime = stub_runtime(
        attempt_id=attempt_id,
        root=tmp_path,
        script=[{"content": "", "error": "provider rejected the request"}, {"content": "two"}],
        provider=fake_provider(),
    )
    patch_create_attempt(monkeypatch, runtime)
    boot = agent_boot(
        tmp_path,
        attempt_id,
        case_snapshot=make_case_payload(turns=[{"content": "a"}, {"content": "b"}]),
    )
    channel, _ = make_channel(attempt_id)

    result = await run_agent_worker(boot, channel=channel)

    assert result.execution_status == "failed"
    assert [error.code for error in result.errors] == ["WORKER_TURN_FAILED"]
    assert result.final_answer == "two"


# --------------------------------------------------------------------------- 密封与秘密


async def test_declared_artifacts_are_indexed_and_missing_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_id = str(uuid.uuid4())
    runtime = stub_runtime(
        attempt_id=attempt_id, root=tmp_path, script=[{"content": "x"}], provider=fake_provider()
    )
    patch_create_attempt(monkeypatch, runtime)
    (runtime.paths.workspace / "output.txt").write_text("alpha\n", encoding="utf-8")
    boot = agent_boot(
        tmp_path,
        attempt_id,
        case_snapshot=make_case_payload(artifact_paths=["output.txt", "ghost.txt"]),
    )
    channel, _ = make_channel(attempt_id)

    await run_agent_worker(boot, channel=channel)

    index = json.loads(
        (runtime.paths.artifact_dir / "manifest.json").read_text(encoding="utf-8")
    )
    workspace_entries = [item for item in index["artifacts"] if item["kind"] == "workspace"]
    assert [item["relative_path"] for item in workspace_entries] == ["workspace/output.txt"]
    assert workspace_entries[0]["sha256"]
    assert index["missing"] == ["ghost.txt"]
    # 越界声明在 boot 校验层就被拒，不可能等到 worker 才处理。
    with pytest.raises(ValidationError, match="PATH_OUTSIDE_ROOT"):
        agent_boot(
            tmp_path,
            attempt_id,
            case_snapshot=make_case_payload(artifact_paths=["../outside.txt"]),
        )


async def test_artifacts_and_frames_never_carry_the_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_id = str(uuid.uuid4())
    runtime = stub_runtime(
        attempt_id=attempt_id,
        root=tmp_path,
        # 回答与工具结果里都带密钥原文，验证的是脱敏而不是碰巧没有。
        script=[{"content": API_KEY, "tools": [("read_file", ToolResult(API_KEY))]}],
        provider=fake_provider(),
    )
    runtime.config.providers.openai.api_key = API_KEY
    patch_create_attempt(monkeypatch, runtime)
    channel, written = make_channel(attempt_id)

    await run_agent_worker(
        agent_boot(
            tmp_path,
            attempt_id,
            case_snapshot=make_case_payload(turns=[{"content": f"回显 {API_KEY}"}]),
        ),
        channel=channel,
    )

    assert API_KEY in str(runtime.bot.calls)  # 执行侧确实见过它
    assert API_KEY not in b"".join(written).decode("utf-8")
    assert channel.sent[-1]["final_answer"] == REDACTED
    events = read_trace(runtime.paths.trace_dir).events
    tool_event = next(event for event in events if event.type == "tool.completed")
    assert tool_event.payload["result"] == REDACTED
    leaked = [
        path
        for path in tmp_path.rglob("*")
        if path.is_file() and API_KEY in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert leaked == []


# --------------------------------------------------------------------------- 判分接缝


async def test_sealed_trace_feeds_rules_and_final_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GROUP-2 屏障：worker 交出的证据必须能被 WU-04 判分读取。"""
    attempt_id = str(uuid.uuid4())
    runtime = stub_runtime(
        attempt_id=attempt_id,
        root=tmp_path,
        script=[{"content": "alpha", "tools": [("read_file", ToolResult("alpha\n"))]}],
        provider=fake_provider(),
    )
    patch_create_attempt(monkeypatch, runtime)
    channel, _ = make_channel(attempt_id)
    case = make_case_payload(
        assertions=[
            {"id": "a1", "kind": "answer_contains", "required": True, "text": "alpha"},
            {
                "id": "t1",
                "kind": "tool_called",
                "required": True,
                "name": "read_file",
                "arguments_subset": {"q": "read_file"},
                "min_calls": 1,
                "max_calls": None,
            },
        ],
        artifact_paths=["output.txt"],
    )
    (runtime.paths.workspace / "output.txt").write_text("alpha\n", encoding="utf-8")

    result = await run_agent_worker(
        agent_boot(tmp_path, attempt_id, case_snapshot=case), channel=channel
    )

    stored = read_trace(runtime.paths.trace_dir)
    tool_seq = next(event.seq for event in stored.events if event.type == "tool.completed")
    evidence = build_attempt_evidence(
        attempt_id=attempt_id,
        case_snapshot=case,
        workspace=runtime.paths.workspace,
        trace_events=stored.events,
        artifacts=(
            EvidenceArtifact(
                relative_path="output.txt",
                media_type="text/plain",
                size_bytes=6,
                sha256="0" * 64,
                artifact_id=uuid.uuid4(),
            ),
        ),
        final_answer=result.final_answer,
        stop_reason=result.stop_reason,
        iterations=result.usage.iterations,
        execution_status=result.execution_status,
    )
    outcomes = evaluate_rules(case, evidence)
    assert all(item.passed for item in outcomes)
    assert evidence.resolve_ref(f"trace:{attempt_id}:{tool_seq}") is not None
    assert (
        derive_verdict(
            result.execution_status,
            [item.passed for item in outcomes if item.required],
            "disabled",
            result.trace_complete,
        )
        == "passed"
    )


# --------------------------------------------------------------------------- judge


def judge_payload(root: Path, *, agent_attempt_id: str, judge_attempt_id: str) -> dict[str, Any]:
    event = TraceEvent(
        schema_version=1,
        attempt_id=uuid.UUID(agent_attempt_id),
        seq=1,
        event_id=uuid.uuid4(),
        type="turn.completed",
        turn=1,
        iteration=1,
        call_id=None,
        ts="2026-10-15T10:00:00+00:00",
        elapsed_ms=12,
        payload={"stop_reason": "stop"},
    )
    return {
        "type": "boot",
        "role": "judge",
        "protocol_version": 1,
        "attempt_id": judge_attempt_id,
        "runtime_root": str(root / "judge-runtime"),
        "workspace": str(root / "judge-workspace"),
        "profile_bundle": profile_bundle(),
        "rubric": make_rubric_payload(),
        "evidence": {
            "attempt_id": agent_attempt_id,
            "case_snapshot": {"name": "读取 input.txt", "description": "复述标识"},
            "final_answer": "alpha",
            "stop_reason": "stop",
            "iterations": 1,
            "trace_events": [event.model_dump(mode="json")],
            "artifacts": [],
            "trace_complete": True,
            "execution_status": "succeeded",
            "workspace": str(root / "agent-workspace"),
        },
    }


class JudgeProvider:
    """受控评分 provider：第一次返回非法 JSON，第二次返回完整维度（正好两次物理请求）。"""

    def __init__(self, dimensions_refs: list[str]) -> None:
        self._refs = dimensions_refs
        self._calls = 0
        self.observer: Any = None

    def get_default_model(self) -> str:
        return "eval-model"

    def set_llm_call_observer(self, observer: Any) -> None:
        self.observer = observer

    async def chat_with_retry(
        self, messages: Any, tools: Any = None, model: Any = None, **kwargs: Any
    ) -> Any:
        self._calls += 1
        if self.observer is not None:
            self.observer(
                LLMCallRecord(
                    started_at_ms=0,
                    duration_ms=3,
                    provider="openai",
                    model="eval-model",
                    source="system",
                    stream=False,
                    finish_reason="stop",
                    usage=make_usage(7, 3),
                )
            )
        if self._calls == 1:
            return _Response("not json at all")
        return _Response(
            json.dumps(
                {
                    "dimensions": [
                        {"id": "task_completion", "score": 4, "reason": "完整完成", "evidence_refs": self._refs},
                        {"id": "correctness", "score": 3, "reason": "结果正确", "evidence_refs": self._refs},
                    ]
                }
            )
        )


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls: list[Any] = []


async def test_judge_worker_retries_once_and_only_counts_judge_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_attempt_id = str(uuid.uuid4())
    judge_attempt_id = str(uuid.uuid4())
    payload = judge_payload(tmp_path, agent_attempt_id=agent_attempt_id, judge_attempt_id=judge_attempt_id)
    boot = JudgeWorkerBoot.model_validate(payload)
    provider = JudgeProvider([f"trace:{agent_attempt_id}:1"])
    runtime = stub_runtime(
        attempt_id=judge_attempt_id,
        root=tmp_path / "judge",
        script=[],
        provider=provider,
        role="judge",
        allowed=[],
    )
    patch_create_attempt(monkeypatch, runtime)
    channel, written = make_channel(judge_attempt_id)

    result = await run_judge_worker(boot, channel=channel)

    assert result.execution_status == "succeeded"
    assert result.final_answer is None and result.stop_reason is None
    assert result.judge is not None
    assert result.judge.status == "completed"
    # 第一次坏输出 + 第二次修复：正好两次物理请求，且都只进 judge 账本。
    assert provider._calls == 2
    assert result.usage.physical_requests == 2
    assert result.usage.total_tokens == 20
    assert result.judge.calibrated is False
    assert [item.score for item in result.judge.dimensions] == [4, 3]

    frames = frames_of(written)
    parent = ParentIpc(attempt_id=judge_attempt_id)
    for frame in frames:
        parent.accept(frame)
    assert parent.can_seal_succeeded(exit_code=0) is True
    events = [frame["event"] for frame in frames if frame["type"] == "trace"]
    calls = [event for event in events if event["type"] == "model.call_completed"]
    assert {event["payload"]["role"] for event in calls} == {"judge"}


async def test_judge_worker_rejects_evidence_without_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_attempt_id = str(uuid.uuid4())
    judge_attempt_id = str(uuid.uuid4())
    payload = judge_payload(tmp_path, agent_attempt_id=agent_attempt_id, judge_attempt_id=judge_attempt_id)
    del payload["evidence"]["workspace"]
    boot = JudgeWorkerBoot.model_validate(payload)
    runtime = stub_runtime(
        attempt_id=judge_attempt_id,
        root=tmp_path / "judge",
        script=[],
        provider=JudgeProvider([]),
        role="judge",
        allowed=[],
    )
    patch_create_attempt(monkeypatch, runtime)
    channel, written = make_channel(judge_attempt_id)

    result = await run_judge_worker(boot, channel=channel)

    assert result.execution_status == "setup_error"
    assert result.errors[0].code == "JUDGE_BOOT_EVIDENCE_INVALID"
    assert frames_of(written)[-1]["type"] == "result"


def test_evidence_from_payload_keeps_path_containment(tmp_path: Path) -> None:
    agent_attempt_id = str(uuid.uuid4())
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    payload = {
        "attempt_id": agent_attempt_id,
        "trace_events": [],
        "artifacts": [
            {
                "relative_path": "../secret.txt",
                "media_type": "text/plain",
                "size_bytes": 1,
                "sha256": "0" * 64,
                "artifact_id": str(uuid.uuid4()),
            }
        ],
        "workspace": str(tmp_path / "ws"),
    }
    (tmp_path / "ws").mkdir()
    with pytest.raises(ValueError, match="PATH_OUTSIDE_ROOT"):
        evidence_from_payload(payload, workspace=tmp_path / "ws")


# --------------------------------------------------------------------------- CLI


def test_read_boot_line_rejects_foreign_attempt(tmp_path: Path) -> None:
    attempt_id = str(uuid.uuid4())
    raw = json.dumps(agent_boot(tmp_path, attempt_id).model_dump(mode="json"))
    assert str(read_boot_line(raw, attempt_id=attempt_id).attempt_id) == attempt_id
    with pytest.raises(WorkerError, match="WORKER_ATTEMPT_MISMATCH"):
        read_boot_line(raw, attempt_id=str(uuid.uuid4()))
    with pytest.raises(WorkerError, match="WORKER_BOOT_MISSING"):
        read_boot_line("   ", attempt_id=attempt_id)
    with pytest.raises(WorkerError, match="WORKER_BOOT_INVALID_JSON"):
        read_boot_line("{oops", attempt_id=attempt_id)
    with pytest.raises(WorkerError, match="WORKER_BOOT_INVALID"):
        read_boot_line('{"type":"trace"}', attempt_id=attempt_id)


def test_cli_writes_only_frames_to_stdout_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    attempt_id = str(uuid.uuid4())
    boot = agent_boot(tmp_path, attempt_id)
    runtime = stub_runtime(
        attempt_id=attempt_id, root=tmp_path, script=[{"content": "alpha"}], provider=fake_provider()
    )
    patch_create_attempt(monkeypatch, runtime)
    out = _Stdout()
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(boot.model_dump(mode="json")) + "\n"))
    monkeypatch.setattr("sys.stdout", out)

    assert main([attempt_id]) == worker_mod.WORKER_EXIT_OK

    frames = frames_of(out.frames)
    assert frames[0]["type"] == "ready"
    assert frames[-1]["type"] == "result"
    assert frames[-1]["execution_status"] == "succeeded"
    # 帧之外的 stdout 必须为空：SDK 的 print 全部改道 stderr。
    assert out.extra == []
    assert "Traceback" not in capsys.readouterr().out


def test_cli_exits_nonzero_when_boot_belongs_to_another_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    boot = agent_boot(tmp_path, str(uuid.uuid4()))
    out = _Stdout()
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(boot.model_dump(mode="json")) + "\n"))
    monkeypatch.setattr("sys.stdout", out)

    assert main([str(uuid.uuid4())]) == worker_mod.WORKER_EXIT_NO_RESULT
    assert out.frames == []
    assert "WORKER_ATTEMPT_MISMATCH" in capsys.readouterr().err


class _Stdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def readline(self) -> str:
        text, self._text = self._text, ""
        return text


class _Stdout:
    """假 stdout：``.buffer`` 收帧、``.write`` 收文本，两者分开才测得出 stdout 纯净性。"""

    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.extra: list[str] = []

    @property
    def buffer(self) -> "_Stdout":
        return self

    def write(self, raw: Any) -> int:
        target = self.frames if isinstance(raw, bytes) else self.extra
        target.append(raw if isinstance(raw, bytes) else str(raw))
        return len(raw)

    def flush(self) -> None:
        return None
