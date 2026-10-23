"""T06：Worker 进程内编排与 CLI 入口（契约 §5 / §6）。

职责边界：``run_worker`` 只负责「执行有序 turns → 采集证据 → 密封 → 交出 result」，
调度、领取任务与写数据库都不在这里（属 WU-05 调度器）。三条硬约束决定了实现形状：

- **stdout 只能是帧**。SDK 与第三方库会往 stdout 打印，因此 CLI 入口把 ``sys.stdout``
  重定向到 stderr，帧一律写到先前抓到的二进制缓冲区；任何杂散输出都会被父进程判成坏帧。
- **seq 只有一个来源**。事件编号取自 :class:`TraceCollector`，落盘与 IPC 转发共用同一条
  事件，父进程不可能看到磁盘上没有的事件（``resolve_ref`` 因此永远能命中）。
- **result 必须如实**。超时/取消/异常都先清理再落终态，多项错误按发生顺序追加；
  判分侧看到的 ``trace_complete`` 来自密封结果，而不是执行是否顺利。

Judge 分支同样走 ready/trace/result：``evidence`` 是服务侧密封后的证据字典，本模块把它
还原成契约 §6.1 的 ``AttemptEvidence``（路径仍受 workspace 包含判定约束），再交给
``evaluate_judge``。量表校准状态由服务侧查询后合并，Worker 不自称已校准。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import re
import sys
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.evaluation.evidence import (
    AttemptEvidence,
    EvidenceArtifact,
    build_attempt_evidence,
)
from nanobot.evaluation.graders.judge import DEFAULT_TIMEOUT_S, evaluate_judge
from nanobot.evaluation.ipc import IPC_PROTOCOL_VERSION, encode_frame
from nanobot.evaluation.runtime_factory import AttemptRuntime, create_attempt
from nanobot.evaluation.schemas import (
    AgentWorkerBoot,
    AgentWorkerResult,
    AttemptError,
    AttemptTiming,
    JudgeWorkerBoot,
    JudgeWorkerResult,
    TraceEvent,
    WorkerBoot,
    WorkerResult,
)
from nanobot.evaluation.trace import TraceCollector, TraceError, TraceManifest
from nanobot.evaluation.usage import UsageLedger, install_usage_observer
from nanobot.evaluation.validation import resolve_inside
from nanobot.llm_usage.models import LLMCallRecord

WORKER_EXIT_OK = 0
#: 没交出 result 帧的退出（崩溃或 boot 不属于本 attempt）；父进程据此判 interrupted/setup_error。
WORKER_EXIT_NO_RESULT = 1
WORKER_EXIT_USAGE = 2

WORKER_BOOT_MISSING = "WORKER_BOOT_MISSING"
WORKER_BOOT_INVALID_JSON = "WORKER_BOOT_INVALID_JSON"
WORKER_BOOT_INVALID = "WORKER_BOOT_INVALID"
WORKER_ATTEMPT_MISMATCH = "WORKER_ATTEMPT_MISMATCH"
WORKER_SETUP_FAILED = "WORKER_SETUP_FAILED"
WORKER_TURN_FAILED = "WORKER_TURN_FAILED"
WORKER_TIMED_OUT = "WORKER_TIMED_OUT"
WORKER_CANCELLED = "WORKER_CANCELLED"
WORKER_TRACE_FAILED = "WORKER_TRACE_FAILED"
WORKER_USAGE_FAILED = "WORKER_USAGE_FAILED"
WORKER_IPC_WRITE_FAILED = "WORKER_IPC_WRITE_FAILED"
WORKER_OBSERVER_UNAVAILABLE = "WORKER_OBSERVER_UNAVAILABLE"
JUDGE_BOOT_EVIDENCE_INVALID = "JUDGE_BOOT_EVIDENCE_INVALID"
REDACTED = "***"
_HASH_CHUNK_BYTES = 1024 * 1024
ARTIFACT_INDEX_NAME = "manifest.json"

FrameSink = Callable[[bytes], None]

#: 项目里以 ``ValueError("CODE: …")`` 形式抛出的错误码形状（validation/runtime_factory 都用它）。
_ERROR_CODE_RE = re.compile(r"[A-Z][A-Z0-9_]{2,}")

_MEDIA_TYPES = {
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".txt": "text/plain",
}


class WorkerError(RuntimeError):
    """带错误码的 Worker 失败；``code`` 原样进入 ``errors[].code``。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def json_safe(value: Any) -> Any:
    """把任意工具返回值收敛成可进 JSON 帧的形状（不做静默丢弃）。"""
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    return value


def _error_text(exc: BaseException) -> str:
    """优先取子系统错误码对象；纯 ``ValueError("CODE: …")`` 也按同一个码上报。"""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    head, sep, _ = str(exc).partition(": ")
    if sep and _ERROR_CODE_RE.fullmatch(head):
        return head
    return type(exc).__name__


# --------------------------------------------------------------------------- 帧写出


def redact_json(value: Any, secrets: Sequence[str]) -> Any:
    """把凭据从任意 JSON 负载里替换掉；帧与落盘共用一份实现。

    走 JSON 往返而不是逐字段遍历，是因为工具结果可以是任意深度结构，漏一个字段就是漏一个
    出口。契约要求凭据既不进管道也不进 Trace，所以两侧都必须先过这里。
    """
    if not secrets:
        return value
    text = json.dumps(value, ensure_ascii=False)
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return json.loads(text)


class WorkerChannel:
    """子→父帧写出：写失败只留痕不抛，保证执行侧的原始错误不被覆盖。"""

    def __init__(self, *, attempt_id: str, sink: FrameSink) -> None:
        self.attempt_id = attempt_id
        self._sink = sink
        # 只留最后一帧：Trace 预算 20 MiB，把发过的每帧都攒在内存里等于再复制一份 Trace。
        self.frame_count = 0
        self.last_frame: dict[str, Any] | None = None
        self.failures: list[str] = []
        self._secrets: tuple[str, ...] = ()

    def set_secrets(self, values: Iterable[str]) -> None:
        """登记凭据；写出的每一帧与落盘的每条事件都先做替换（契约 §5）。"""
        self._secrets = tuple(value for value in values if value)

    @property
    def secrets(self) -> tuple[str, ...]:
        return self._secrets

    def redact(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return redact_json(dict(payload), self._secrets)

    def send(self, frame: Mapping[str, Any]) -> bool:
        payload = self.redact(frame)
        try:
            raw = encode_frame(payload)
            self._sink(raw)
        except Exception as exc:  # noqa: BLE001 - 管道坏了也必须继续清理
            self.failures.append(f"{getattr(exc, 'code', type(exc).__name__)}: {exc}")
            return False
        self.frame_count += 1
        self.last_frame = payload
        return True

    def ready(self) -> bool:
        return self.send(
            {"type": "ready", "protocol_version": IPC_PROTOCOL_VERSION, "attempt_id": self.attempt_id}
        )

    def trace(self, event: TraceEvent) -> bool:
        return self.send(
            {
                "type": "trace",
                "attempt_id": self.attempt_id,
                "event": event.model_dump(mode="json"),
            }
        )

    def result(self, result: WorkerResult) -> bool:
        return self.send(json.loads(result.model_dump_json()))


def stdout_sink(stream: Any) -> FrameSink:
    def sink(raw: bytes) -> None:
        stream.write(raw)
        stream.flush()

    return sink


# --------------------------------------------------------------------------- Trace


@dataclass
class TraceRecorder:
    """seq 分配 + 落盘 + IPC 转发的唯一出口。"""

    attempt_id: str
    collector: TraceCollector
    channel: WorkerChannel
    started_at: float
    trace_errors: list[AttemptError]

    def emit(
        self,
        event_type: str,
        *,
        turn: int | None = None,
        iteration: int | None = None,
        call_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> TraceEvent | None:
        event = TraceEvent(
            schema_version=1,
            attempt_id=uuid.UUID(self.attempt_id),
            seq=self.collector.next_seq,
            event_id=uuid.uuid4(),
            type=event_type,  # pyright: ignore[reportArgumentType] - 闭集由模型校验
            turn=turn,
            iteration=iteration,
            call_id=call_id,
            ts=_now_iso(),
            elapsed_ms=max(0, int((time.monotonic() - self.started_at) * 1000)),
            # 先脱敏再落盘：Trace 目录会被证据读取与导出复用，凭据不能只挡在 IPC 这一层。
            payload=self.channel.redact(json_safe(dict(payload or {}))),
        )
        try:
            written = self.collector.append(event)
        except TraceError as exc:
            self.trace_errors.append(
                AttemptError(phase="trace", code=exc.code, message=str(exc))
            )
            self.collector.mark_incomplete(exc.code)
            return None
        if written is None:
            # 预算触顶或重复 event_id：磁盘上没有这条，父进程也不能收到。
            return None
        self.channel.trace(written)
        return written

    def seal(self) -> TraceManifest:
        return self.collector.seal()


def make_model_call_observer(
    recorder: TraceRecorder, *, role: str, scope: str
) -> Callable[[LLMCallRecord], None]:
    """把每次物理调用映射成 ``model.call_completed``；字段全部来自 ``LLMCallRecord``。"""

    def observer(record: LLMCallRecord) -> None:
        usage = record.usage
        recorder.emit(
            "model.call_completed",
            payload={
                "role": role,
                "scope": scope,
                "provider": record.provider,
                "model": record.model,
                "source": record.source,
                "stream": record.stream,
                "finish_reason": record.finish_reason,
                "started_at_ms": record.started_at_ms,
                "duration_ms": record.duration_ms,
                "error_status_code": record.error_status_code,
                "error_kind": record.error_kind,
                "input_tokens": None if usage is None else usage.input_tokens,
                "output_tokens": None if usage is None else usage.output_tokens,
                "total_tokens": None if usage is None else usage.total_tokens,
                "reported_tokens": None if usage is None else usage.reported_tokens,
                "estimated_tokens": None if usage is None else usage.estimated_tokens,
            },
        )

    return observer


def install_worker_observers(
    runtime: AttemptRuntime,
    *,
    ledger: UsageLedger,
    recorder: TraceRecorder,
) -> bool:
    """只在 attempt 自己的 provider 实例上装 observer，绝不改全局 provider。"""
    provider = runtime.provider
    if not hasattr(provider, "set_llm_call_observer"):
        ledger.mark_coverage_gap(
            runtime.role, reason=f"{WORKER_OBSERVER_UNAVAILABLE}: {type(provider).__name__}"
        )
        return False
    install_usage_observer(
        provider,
        ledger=ledger,
        role=runtime.role,
        scope="main",
        previous=make_model_call_observer(recorder, role=runtime.role, scope="main"),
    )
    return True


class WorkerTraceHook(AgentHook):
    """记录 SDK/Hook 可见的轮次、请求摘要与工具生命周期。

    ``reraise=True``：采集证据失败必须冒泡到执行侧，由 Worker 落 ``trace`` 阶段错误并把
    ``trace_complete`` 置为 false —— 静默吞掉会让判分侧拿到一份看起来完整、实际缺证据的 Trace。
    """

    def __init__(self, recorder: TraceRecorder, *, turn: int, runtime: AttemptRuntime) -> None:
        super().__init__(reraise=True)
        self._recorder = recorder
        self._turn = turn
        self._runtime = runtime
        self._open_calls: dict[str, tuple[int, float]] = {}
        self.iterations = 0
        self.tools_used: list[str] = []

    async def before_iteration(self, context: AgentHookContext) -> None:
        self.iterations = max(self.iterations, int(context.iteration) + 1)
        self._recorder.emit(
            "iteration.started",
            turn=self._turn,
            iteration=context.iteration,
            payload={"messages": len(context.messages)},
        )
        self._recorder.emit(
            "model.request_summary",
            turn=self._turn,
            iteration=context.iteration,
            payload={
                "summary_kind": "hook_visible",
                "model": self._runtime.capability.get("model"),
                "provider": self._runtime.capability.get("provider"),
                "tool_names": sorted(self._runtime.registry.tool_names),
                "message_count": len(context.messages),
                "messages": [
                    {"index": index, "role": str(item.get("role")), "chars": _message_chars(item)}
                    for index, item in enumerate(context.messages)
                ],
            },
        )

    async def after_iteration(self, context: AgentHookContext) -> None:
        self._recorder.emit(
            "iteration.completed",
            turn=self._turn,
            iteration=context.iteration,
            payload={
                "stop_reason": context.stop_reason,
                "tool_calls": len(context.tool_calls),
                "error": context.error,
            },
        )

    async def before_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: Any,
        tool: Any,
        params: Any,
    ) -> None:
        call_id = str(getattr(tool_call, "id", "") or f"seq-{context.iteration}")
        self.tools_used.append(str(getattr(tool_call, "name", "")))
        self._open_calls[call_id] = (context.iteration, time.monotonic())
        self._recorder.emit(
            "tool.started",
            turn=self._turn,
            iteration=context.iteration,
            call_id=call_id,
            payload={
                "name": str(getattr(tool_call, "name", "")),
                "arguments": json_safe(params if isinstance(params, Mapping) else {}),
                "result": None,
                "is_error": None,
                "duration_ms": None,
            },
        )

    async def after_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: Any,
        tool: Any,
        params: Any,
        result: Any,
    ) -> None:
        self._emit_tool_result(tool_call, context, result, failed=False)

    async def on_execute_tool_error(
        self,
        context: AgentHookContext,
        tool_call: Any,
        tool: Any,
        params: Any,
        error: Any,
    ) -> None:
        self._emit_tool_result(tool_call, context, error, failed=True)

    def _emit_tool_result(self, tool_call: Any, context: AgentHookContext, value: Any, *, failed: bool) -> None:
        call_id = str(getattr(tool_call, "id", "") or f"seq-{context.iteration}")
        iteration, started = self._open_calls.pop(call_id, (context.iteration, time.monotonic()))
        # is_error 取项目 ToolResult 的结构化布尔值；抛出的异常同样算错误结果。
        is_error = bool(getattr(value, "is_error", True)) if failed else bool(getattr(value, "is_error", False))
        self._recorder.emit(
            "tool.failed" if failed else "tool.completed",
            turn=self._turn,
            iteration=iteration,
            call_id=call_id,
            payload={
                "name": str(getattr(tool_call, "name", "")),
                "arguments": json_safe(getattr(tool_call, "arguments", None)),
                "result": json_safe(value if isinstance(value, (str, int, float, bool)) or value is None else str(value)),
                "is_error": is_error,
                "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
            },
        )


def _message_chars(message: Mapping[str, Any]) -> int:
    content = message.get("content")
    if isinstance(content, str):
        return len(content)
    if content is None:
        return 0
    return len(json.dumps(content, ensure_ascii=False, default=str))


# --------------------------------------------------------------------------- 产物密封


def media_type_of(path: Path) -> str:
    return _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _index_file(root: Path, path: Path, *, kind: str) -> dict[str, Any]:
    """流式哈希：产物可以是任意大小，不能为了算 sha256 把整个文件读进内存。"""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "media_type": media_type_of(path),
        "size_bytes": size,
        "sha256": digest.hexdigest(),
        "kind": kind,
    }


def seal_attempt_artifacts(
    runtime: AttemptRuntime, *, declared: Sequence[str] = ()
) -> dict[str, Any]:
    """原子写产物索引：声明产物 + Trace 文件，缺失项显式列出。"""
    paths = runtime.paths
    entries: list[dict[str, Any]] = []
    missing: list[str] = []
    for relative in declared:
        try:
            target = resolve_inside(paths.workspace, relative)
        except ValueError:
            missing.append(relative)
            continue
        if not target.is_file():
            missing.append(relative)
            continue
        entries.append(_index_file(paths.root, target, kind="workspace"))
    for trace_file in sorted(paths.trace_dir.rglob("*")):
        if trace_file.is_file():
            entries.append(_index_file(paths.root, trace_file, kind="trace"))

    index = {
        "schema_version": 1,
        "attempt_id": runtime.attempt_id,
        "trace_complete": True,
        "sealed_at": _now_iso(),
        "artifacts": entries,
        "missing": missing,
    }
    paths.artifact_dir.mkdir(parents=True, exist_ok=True)
    target = paths.artifact_dir / ARTIFACT_INDEX_NAME
    tmp = target.with_name(f"{ARTIFACT_INDEX_NAME}.tmp-{uuid.uuid4()}")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(index, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return index


# --------------------------------------------------------------------------- 编排


async def run_worker(
    boot: WorkerBoot,
    *,
    channel: WorkerChannel,
    provider: Any | None = None,
    base_config: Any | None = None,
) -> WorkerResult:
    """契约 §6 的入口：按 boot.role 分派到 agent / judge 执行路径。"""
    if boot.role == "agent":
        return await run_agent_worker(
            boot, channel=channel, provider=provider, base_config=base_config
        )
    return await run_judge_worker(
        boot, channel=channel, provider=provider, base_config=base_config
    )


@contextlib.asynccontextmanager
async def guarded_close(
    runtime: AttemptRuntime, errors: list[AttemptError]
) -> AsyncGenerator[None]:
    """清理必须发生，但清理失败只能追加错误，不能遮蔽在飞的执行异常。

    ``finally`` 里自捕获不会再向外抛，因此原始异常（含 ``bot.run`` 的报错）保持原样冒泡；
    关闭只走 ``runtime.aclose()`` 一处，避免 SDK 被关两次。
    """
    try:
        yield
    finally:
        try:
            await runtime.aclose()
        except Exception as exc:  # noqa: BLE001 - 清理失败降级为 cleanup 错误
            errors.append(
                AttemptError(phase="cleanup", code=_error_text(exc), message=str(exc))
            )


async def run_agent_worker(
    boot: AgentWorkerBoot,
    *,
    channel: WorkerChannel,
    provider: Any | None = None,
    base_config: Any | None = None,
) -> AgentWorkerResult:
    """执行有序 turns，密封 Trace 与产物，并交出 result 帧。"""
    channel.ready()
    errors: list[AttemptError] = []
    started = time.monotonic()
    ledger = UsageLedger()
    try:
        runtime = await create_attempt(
            boot, provider=provider, base_config=base_config
        )
    except Exception as exc:  # noqa: BLE001 - 设置失败也要给确定终态
        errors.append(
            AttemptError(phase="setup", code=_error_text(exc), message=str(exc))
        )
        return _emit(
            channel,
            AgentWorkerResult(
                attempt_id=boot.attempt_id,
                execution_status="setup_error",
                usage=ledger.aggregate("agent"),
                timing=AttemptTiming(agent_ms=_ms(started)),
                errors=errors,
                trace_complete=False,
            ),
        )

    channel.set_secrets(_secret_values(runtime))
    recorder = TraceRecorder(
        attempt_id=runtime.attempt_id,
        collector=TraceCollector(attempt_id=runtime.attempt_id, trace_dir=runtime.paths.trace_dir),
        channel=channel,
        started_at=started,
        trace_errors=[],
    )
    install_worker_observers(runtime, ledger=ledger, recorder=recorder)

    case = boot.case_snapshot
    status: str = "succeeded"
    final_answer: str | None = None
    stop_reason: str | None = None
    iterations = 0
    target = getattr(boot, "target", None) or {}
    # Agent 目标：注入 agent_id，让 loop 的档案解析接管本轮提示词/工具/技能。
    extra_metadata = (
        {"agent_id": str(target.get("id"))}
        if isinstance(target, dict) and target.get("kind") == "agent"
        else None
    )
    recorder.emit(
        "attempt.started",
        payload={
            "role": runtime.role,
            "tool_mode": case.execution.tool_mode,
            "model": runtime.capability.get("model"),
            "turns": len(case.turns),
            "max_iterations": case.execution.max_iterations,
            "timeout_s": case.execution.timeout_s,
        },
    )
    # 异常处理必须排在 guarded_close 内侧：先记执行错误，再记清理错误，顺序才等于发生顺序。
    async with guarded_close(runtime, errors):
        try:
            async with asyncio.timeout(case.execution.timeout_s):
                for index, turn in enumerate(case.turns, start=1):
                    hook = WorkerTraceHook(recorder, turn=index, runtime=runtime)
                    content = turn.content
                    recorder.emit(
                        "turn.started",
                        turn=index,
                        payload={"content_chars": len(content)},
                    )
                    # 仅在 Agent 目标时传 metadata：测试替身的 bot.run 不带该参数，
                    # 无目标（默认 None）保持原调用形状。
                    run_kwargs: dict[str, Any] = {
                        "session_key": runtime.session_key, "hooks": [hook],
                    }
                    if extra_metadata is not None:
                        run_kwargs["metadata"] = extra_metadata
                    result = await runtime.bot.run(content, **run_kwargs)
                    iterations += hook.iterations
                    final_answer = result.content
                    stop_reason = result.stop_reason
                    if result.error:
                        status = "failed"
                        errors.append(
                            AttemptError(
                                phase="execution",
                                code=WORKER_TURN_FAILED,
                                message=str(result.error),
                            )
                        )
                    recorder.emit(
                        "turn.completed",
                        turn=index,
                        payload={
                            "stop_reason": result.stop_reason,
                            "iterations": hook.iterations,
                            "tools_used": hook.tools_used,
                            "answer_chars": len(result.content or ""),
                            "error": result.error,
                        },
                    )
        except TimeoutError:
            status = "timed_out"
            errors.append(
                AttemptError(
                    phase="execution",
                    code=WORKER_TIMED_OUT,
                    message=f"attempt exceeded {case.execution.timeout_s}s",
                )
            )
        except asyncio.CancelledError:
            # 取消来自 worker 自身的任务调度；先落终态再返回，父进程仍可读到 result。
            status = "cancelled"
            errors.append(
                AttemptError(phase="execution", code=WORKER_CANCELLED, message="attempt cancelled")
            )
        except Exception as exc:  # noqa: BLE001 - 保留原始异常文本
            status = "failed"
            errors.append(
                AttemptError(phase="execution", code=_error_text(exc), message=str(exc))
            )

    if status in {"timed_out", "cancelled"} and final_answer is None:
        # 未产出任何回答的执行不可能判通过；停止原因留成状态名，报告侧不再猜。
        stop_reason = stop_reason or status

    recorder.emit(
        "attempt.completed" if status == "succeeded" else "attempt.failed",
        payload={"execution_status": status, "stop_reason": stop_reason, "iterations": iterations},
    )
    errors.extend(recorder.trace_errors)
    trace_complete = True
    try:
        manifest = recorder.seal()
        trace_complete = manifest.trace_complete
    except TraceError as exc:
        trace_complete = False
        errors.append(AttemptError(phase="trace", code=exc.code, message=str(exc)))

    try:
        index = seal_attempt_artifacts(runtime, declared=case.artifact_paths)
        index["trace_complete"] = trace_complete
    except OSError as exc:
        trace_complete = False
        errors.append(
            AttemptError(phase="trace", code="ARTIFACT_INDEX_WRITE_FAILED", message=str(exc))
        )

    try:
        ledger.ensure_clean()
    except RuntimeError as exc:
        errors.append(
            AttemptError(phase="execution", code="USAGE_OBSERVER_FAILED", message=str(exc))
        )

    return _emit(
        channel,
        AgentWorkerResult(
            attempt_id=boot.attempt_id,
            execution_status=status,  # pyright: ignore[reportArgumentType]
            final_answer=final_answer,
            stop_reason=stop_reason,
            usage=ledger.aggregate("agent", iterations=iterations),
            timing=AttemptTiming(agent_ms=_ms(started)),
            errors=errors,
            trace_complete=trace_complete,
        ),
    )


async def run_judge_worker(
    boot: JudgeWorkerBoot,
    *,
    channel: WorkerChannel,
    provider: Any | None = None,
    base_config: Any | None = None,
) -> JudgeWorkerResult:
    """评分子进程：还原证据 → 无工具评分 → 只记 judge 账本。"""
    channel.ready()
    errors: list[AttemptError] = []
    started = time.monotonic()
    ledger = UsageLedger()
    try:
        runtime = await create_attempt(
            boot, provider=provider, base_config=base_config
        )
        evidence = evidence_from_payload(
            boot.evidence,
            # 证据属于被测 attempt，引用校验必须用它自己的 workspace，不能用评分 scratch 目录。
            workspace=_evidence_workspace(boot),
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(AttemptError(phase="setup", code=_error_text(exc), message=str(exc)))
        return _emit(
            channel,
            JudgeWorkerResult(
                attempt_id=boot.attempt_id,
                execution_status="setup_error",
                usage=ledger.aggregate("judge"),
                timing=AttemptTiming(grading_ms=_ms(started)),
                errors=errors,
                trace_complete=False,
            ),
        )

    channel.set_secrets(_secret_values(runtime))
    recorder = TraceRecorder(
        attempt_id=runtime.attempt_id,
        collector=TraceCollector(attempt_id=runtime.attempt_id, trace_dir=runtime.paths.trace_dir),
        channel=channel,
        started_at=started,
        trace_errors=[],
    )
    install_worker_observers(runtime, ledger=ledger, recorder=recorder)
    recorder.emit(
        "attempt.started",
        payload={"role": "judge", "rubric_version": boot.rubric.version, "model": runtime.capability.get("model")},
    )

    grading_started = time.monotonic()
    judge = None
    try:
        async with guarded_close(runtime, errors):
            judge = await evaluate_judge(
                boot.rubric,
                evidence,
                runtime.provider,
                model=str(runtime.capability.get("model") or "") or None,
                calibrated=False,
                timeout_s=DEFAULT_TIMEOUT_S,
            )
    except Exception as exc:  # noqa: BLE001 - evaluate_judge 设计上不抛，抛了就是运行故障
        errors.append(AttemptError(phase="grading", code=_error_text(exc), message=str(exc)))
    status = "succeeded" if judge is not None and judge.status == "completed" else "failed"
    recorder.emit(
        "attempt.completed" if status == "succeeded" else "attempt.failed",
        payload={"execution_status": status, "judge_status": None if judge is None else judge.status},
    )
    errors.extend(recorder.trace_errors)
    trace_complete = True
    try:
        trace_complete = recorder.seal().trace_complete
    except TraceError as exc:
        trace_complete = False
        errors.append(AttemptError(phase="trace", code=exc.code, message=str(exc)))
    try:
        ledger.ensure_clean()
    except RuntimeError as exc:
        errors.append(AttemptError(phase="grading", code="USAGE_OBSERVER_FAILED", message=str(exc)))

    return _emit(
        channel,
        JudgeWorkerResult(
            attempt_id=boot.attempt_id,
            execution_status=status,  # pyright: ignore[reportArgumentType]
            usage=ledger.aggregate("judge"),
            timing=AttemptTiming(grading_ms=_ms(grading_started)),
            errors=errors,
            trace_complete=trace_complete,
            judge=judge,
        ),
    )


def evidence_from_payload(payload: Mapping[str, Any], *, workspace: Path) -> AttemptEvidence:
    """把 Judge boot 的证据字典还原成契约 §6.1 对象（字段非法即拒，不做宽松处理）。"""
    attempt_id = str(payload.get("attempt_id") or "")
    if not attempt_id:
        raise WorkerError(JUDGE_BOOT_EVIDENCE_INVALID, "attempt_id is missing")
    events_raw = payload.get("trace_events") or []
    artifacts_raw = payload.get("artifacts") or []
    if not isinstance(events_raw, list) or not isinstance(artifacts_raw, list):
        raise WorkerError(JUDGE_BOOT_EVIDENCE_INVALID, "trace_events/artifacts must be lists")
    try:
        artifacts = [
            EvidenceArtifact(
                relative_path=str(item["relative_path"]),
                media_type=str(item["media_type"]),
                size_bytes=int(item["size_bytes"]),
                sha256=str(item["sha256"]),
                artifact_id=None if item.get("artifact_id") is None else uuid.UUID(str(item["artifact_id"])),
            )
            for item in artifacts_raw
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkerError(JUDGE_BOOT_EVIDENCE_INVALID, f"artifacts: {exc}") from exc
    try:
        events = [TraceEvent.model_validate(item) for item in events_raw]
    except Exception as exc:  # noqa: BLE001 - pydantic 细节不该外泄给调用方
        raise WorkerError(JUDGE_BOOT_EVIDENCE_INVALID, f"trace_events: {exc}") from exc
    case_raw = payload.get("case_snapshot") or {}
    if not isinstance(case_raw, Mapping):
        raise WorkerError(JUDGE_BOOT_EVIDENCE_INVALID, "case_snapshot must be an object")
    return build_attempt_evidence(
        attempt_id=attempt_id,
        case_snapshot=dict(case_raw),
        workspace=workspace,
        trace_events=events,
        artifacts=artifacts,
        final_answer=payload.get("final_answer"),
        stop_reason=payload.get("stop_reason"),
        iterations=int(payload.get("iterations") or 0),
        trace_complete=bool(payload.get("trace_complete", False)),
        execution_status=payload.get("execution_status", "succeeded"),  # pyright: ignore[reportArgumentType]
    )


def _evidence_workspace(boot: JudgeWorkerBoot) -> Path:
    raw = boot.evidence.get("workspace")
    if isinstance(raw, str) and raw.strip():
        return Path(raw)
    raise WorkerError(JUDGE_BOOT_EVIDENCE_INVALID, "workspace is missing")


def _secret_values(runtime: AttemptRuntime) -> list[str]:
    values: list[str] = []
    for part in runtime.config.providers.model_dump().values():
        if not isinstance(part, dict):
            continue
        for key in ("api_key", "access_key", "secret_value", "app_secret", "client_secret"):
            value = part.get(key)
            if isinstance(value, str) and value:
                values.append(value)
    return values


def _ms(since: float) -> int:
    return max(0, int((time.monotonic() - since) * 1000))


WorkerResultT = TypeVar("WorkerResultT", bound=WorkerResult)


def _emit(channel: WorkerChannel, result: WorkerResultT) -> WorkerResultT:
    """管道写坏了也要把错误留在结果里：否则父进程以为什么都没发生。"""
    channel.result(result)
    if channel.failures:
        for detail in channel.failures:
            result.errors.append(
                AttemptError(phase="cleanup", code=WORKER_IPC_WRITE_FAILED, message=detail)
            )
        channel.failures.clear()
    return result


# --------------------------------------------------------------------------- CLI


def read_boot_line(raw: str, *, attempt_id: str) -> WorkerBoot:
    """解析 stdin 的唯一一帧 boot，并核对它属于本进程被要求执行的 attempt。"""
    line = raw.strip()
    if not line:
        raise WorkerError(WORKER_BOOT_MISSING, "stdin carried no boot frame")
    try:
        frame = json.loads(line)
    except ValueError as exc:
        raise WorkerError(WORKER_BOOT_INVALID_JSON, str(exc)) from exc
    if not isinstance(frame, dict) or frame.get("type") != "boot":
        raise WorkerError(WORKER_BOOT_INVALID, "first frame is not a boot frame")
    if str(frame.get("attempt_id")) != str(attempt_id):
        raise WorkerError(
            WORKER_ATTEMPT_MISMATCH, f"boot={frame.get('attempt_id')} expected={attempt_id}"
        )
    try:
        return AgentWorkerBoot.model_validate(frame) if frame.get("role") == "agent" else (
            JudgeWorkerBoot.model_validate(frame)
        )
    except Exception as exc:  # noqa: BLE001 - 校验细节留在 stderr
        raise WorkerError(WORKER_BOOT_INVALID, str(exc)) from exc


async def _amain(attempt_id: str, *, out_stream: Any) -> WorkerResult | None:
    channel = WorkerChannel(attempt_id=attempt_id, sink=stdout_sink(out_stream))
    boot = read_boot_line(sys.stdin.readline(), attempt_id=attempt_id)
    # 帧之外不允许任何 stdout 输出：SDK 的 print 全部改道 stderr。
    with contextlib.redirect_stdout(sys.stderr):
        if boot.role == "agent":
            return await run_agent_worker(boot, channel=channel)
        return await run_judge_worker(boot, channel=channel)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nanobot.evaluation.worker")
    parser.add_argument("attempt_id", help="本进程负责的 attempt UUID")
    args = parser.parse_args(list(argv) if argv is not None else None)
    out_stream = sys.stdout.buffer
    try:
        result = asyncio.run(_amain(args.attempt_id, out_stream=out_stream))
    except WorkerError as exc:
        # boot 非法 / attempt 不匹配：不写任何帧，让父进程按 setup_error 处理。
        print(f"worker failed: {exc}", file=sys.stderr)
        return WORKER_EXIT_NO_RESULT
    except Exception as exc:  # noqa: BLE001 - 崩溃只报 stderr，父进程按无 result 处理
        print(f"worker crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return WORKER_EXIT_NO_RESULT
    return WORKER_EXIT_OK if result is not None else WORKER_EXIT_NO_RESULT


if __name__ == "__main__":
    raise SystemExit(main())
