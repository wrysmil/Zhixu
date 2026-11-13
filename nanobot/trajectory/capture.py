"""运行时采集门面。

所有入口 fail-open：没有 recorder、轨迹关闭或任何异常时都退化为 no-op，
绝不改变模型调用、流式回调或工具结果。provider / runner / tool 只依赖本
模块，不直接接触存储实现。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import uuid4

from loguru import logger

from nanobot.trajectory.context import (
    bind_logical_request,
    current_logical_request_id,
    current_trace_recorder,
    reset_logical_request,
)
from nanobot.trajectory.recorder import TraceRecorder

BLOCK_REASONING = 0
BLOCK_TEXT = 1
BLOCK_TOOL_CALL = 2
BLOCK_MEDIA = 3
_BLOCK_KIND = {
    BLOCK_REASONING: "reasoning",
    BLOCK_TEXT: "text",
    BLOCK_TOOL_CALL: "tool-call",
    BLOCK_MEDIA: "media",
}


def recorder() -> TraceRecorder | None:
    return current_trace_recorder()


def _safe(call: Any, *args: Any, **kwargs: Any) -> None:
    if current_trace_recorder() is None:
        return
    try:
        call(*args, **kwargs)
    except Exception:
        logger.exception("trajectory capture failed at {}", getattr(call, "__name__", call))


def new_logical_request_id() -> str:
    return uuid4().hex


def bind_request(request_id: str) -> Any:
    return bind_logical_request(request_id)


def reset_request(token: Any) -> None:
    reset_logical_request(token)


# --------------------------------------------------------------------- run/turn


def run_started(*, purpose: str | None = None, visible: bool = False) -> None:
    rec = current_trace_recorder()
    if rec is not None:
        _safe(rec.start, purpose=purpose, visible=visible)


def run_finished(*, status: str = "succeeded", stop_reason: str | None = None) -> None:
    rec = current_trace_recorder()
    if rec is not None:
        _safe(rec.finish, status=status, stop_reason=stop_reason)


def input_recorded(text: object, *, source: str = "user") -> None:
    rec = current_trace_recorder()
    if rec is not None:
        _safe(rec.input_recorded, text, source=source)


def context_injected(
    kind: str, text: object, *, source: str | None = None, index: int = 0,
) -> None:
    rec = current_trace_recorder()
    if rec is not None:
        _safe(
            rec.context_injected, kind, text, source=source, index=index,
            logical_request_id=current_logical_request_id(),
        )


def step_started(step: int) -> None:
    rec = current_trace_recorder()
    if rec is not None:
        _safe(rec.step_started, step)


def step_finished(step: int, status: str = "succeeded") -> None:
    rec = current_trace_recorder()
    if rec is not None:
        _safe(rec.step_finished, step, status)


def model_requested(
    logical_request_id: str, *, request_snapshot: object,
    purpose: str | None = None, completeness: str = "normalized",
) -> None:
    rec = current_trace_recorder()
    if rec is not None:
        _safe(
            rec.model_requested, logical_request_id,
            request_snapshot=request_snapshot, purpose=purpose, completeness=completeness,
        )


# ------------------------------------------------------------------ attempts


class AttemptScope:
    """一次可观测 provider attempt 的采集边界。"""

    def __init__(self, *, provider: str, model: str, attempt: int) -> None:
        self._recorder = current_trace_recorder()
        self._provider = provider
        self._model = model
        self._attempt = attempt
        self._started_monotonic = time.monotonic()
        self._started_ms = int(time.time() * 1000)
        self.model_call_id = uuid4().hex
        self._logical_request_id = current_logical_request_id()
        self._finished = False
        if self._recorder is not None:
            _safe(
                self._recorder.attempt_started,
                self.model_call_id,
                attempt=attempt,
                provider=provider,
                model=model,
                logical_request_id=self._logical_request_id,
            )

    @property
    def active(self) -> bool:
        return self._recorder is not None

    def wrap_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if self._recorder is None:
            return kwargs
        recorder = self._recorder
        model_call_id = self.model_call_id
        content_callback = kwargs.get("on_content_delta")
        thinking_callback = kwargs.get("on_thinking_delta")
        if not callable(content_callback) and not callable(thinking_callback):
            return kwargs
        wrapped = dict(kwargs)
        if callable(content_callback):
            typed_content = cast(Callable[[str], Awaitable[None]], content_callback)

            async def _content(delta: str) -> None:
                _safe(recorder.model_delta, model_call_id, BLOCK_TEXT, "text", delta)
                await typed_content(delta)

            wrapped["on_content_delta"] = _content
        if callable(thinking_callback):
            typed_thinking = cast(Callable[[str], Awaitable[None]], thinking_callback)

            async def _thinking(delta: str) -> None:
                _safe(recorder.model_delta, model_call_id, BLOCK_REASONING, "reasoning", delta)
                await typed_thinking(delta)

            wrapped["on_thinking_delta"] = _thinking
        return wrapped

    def finish(self, response: Any) -> None:
        if self._recorder is None or self._finished:
            return
        self._finished = True
        duration_ms = max(0, int((time.monotonic() - self._started_monotonic) * 1000))
        finish_reason = getattr(response, "finish_reason", None)
        status = "succeeded"
        if finish_reason == "cancelled":
            status = "cancelled"
        elif finish_reason == "error":
            status = "failed"
        blocks = _response_blocks(response)
        _safe(
            self._recorder.attempt_finished,
            self.model_call_id,
            status=status,
            finish_reason=finish_reason,
            blocks=blocks,
            usage=_usage_dict(getattr(response, "usage", None)),
            ttft_ms=_opt_int(getattr(response, "ttft_ms", None)),
            generation_ms=_opt_int(getattr(response, "generation_ms", None)),
            error_code=_error_code(response),
            error_kind=getattr(response, "error_kind", None),
            provider=self._provider,
            model=self._model,
            provider_response_id=getattr(response, "provider_response_id", None),
            started_at_ms=self._started_ms,
            duration_ms=duration_ms,
        )

    def fail(self, exc: BaseException) -> None:
        if self._recorder is None or self._finished:
            return
        self._finished = True
        duration_ms = max(0, int((time.monotonic() - self._started_monotonic) * 1000))
        _safe(
            self._recorder.attempt_finished,
            self.model_call_id,
            status="failed",
            finish_reason="error",
            blocks=(),
            error_code=type(exc).__name__,
            error_kind="exception",
            provider=self._provider,
            model=self._model,
            started_at_ms=self._started_ms,
            duration_ms=duration_ms,
        )

    def cancelled(self) -> None:
        if self._recorder is None or self._finished:
            return
        self._finished = True
        duration_ms = max(0, int((time.monotonic() - self._started_monotonic) * 1000))
        _safe(
            self._recorder.attempt_finished,
            self.model_call_id,
            status="cancelled",
            finish_reason="cancelled",
            blocks=(),
            error_kind="cancelled",
            provider=self._provider,
            model=self._model,
            started_at_ms=self._started_ms,
            duration_ms=duration_ms,
        )


def begin_attempt(*, provider: str, model: str, attempt: int) -> AttemptScope | None:
    if current_trace_recorder() is None:
        return None
    try:
        return AttemptScope(provider=provider, model=model, attempt=attempt)
    except Exception:
        logger.exception("trajectory attempt scope failed")
        return None


def retry_scheduled(
    *, previous_call_id: str, attempt: int, delay_ms: int | None, error_kind: str | None,
) -> None:
    rec = current_trace_recorder()
    if rec is not None:
        _safe(
            rec.retry_scheduled,
            previous_call_id=previous_call_id,
            attempt=attempt,
            delay_ms=delay_ms,
            error_kind=error_kind,
            logical_request_id=current_logical_request_id(),
        )


def route_changed(*, previous: str | None, current: str | None,
                  reason: str | None = None) -> None:
    rec = current_trace_recorder()
    if rec is not None:
        _safe(
            rec.route_changed,
            logical_request_id=current_logical_request_id(),
            previous=previous,
            current=current,
            reason=reason,
        )


# ---------------------------------------------------------------------- tools


def tool_requested(
    *, tool_call_id: str, provider_call_id: str | None, name: str,
    arguments: object, schema: object | None, model_order: int,
) -> None:
    rec = current_trace_recorder()
    if rec is not None:
        _safe(
            rec.tool_requested,
            tool_call_id=tool_call_id,
            provider_call_id=provider_call_id,
            name=name,
            arguments=arguments,
            schema=schema,
            model_order=model_order,
        )


def tool_started(
    *, tool_call_id: str, effective_arguments: object | None = None,
    queue_ms: int | None = None,
) -> None:
    rec = current_trace_recorder()
    if rec is not None:
        _safe(
            rec.tool_started,
            tool_call_id=tool_call_id,
            effective_arguments=effective_arguments,
            queue_ms=queue_ms,
        )


def tool_finished(
    *, tool_call_id: str, status: str, result: object | None = None,
    error: object | None = None, duration_ms: int | None = None,
    side_effect_outcome: str | None = None, skipped_reason: str | None = None,
    model_observation: object | None = None,
) -> None:
    rec = current_trace_recorder()
    if rec is not None:
        _safe(
            rec.tool_finished,
            tool_call_id=tool_call_id,
            status=status,
            result=result,
            error=error,
            duration_ms=duration_ms,
            side_effect_outcome=side_effect_outcome,
            skipped_reason=skipped_reason,
            model_observation=model_observation,
        )


def execution_linked(
    *, tool_call_id: str, invocation_id: str, child_run_id: str | None,
    child_session_id: str | None = None,
) -> None:
    rec = current_trace_recorder()
    if rec is not None:
        _safe(
            rec.execution_linked,
            tool_call_id=tool_call_id,
            invocation_id=invocation_id,
            child_run_id=child_run_id,
            child_session_id=child_session_id,
        )


# ----------------------------------------------------------------- compaction


def compaction_started(
    *, compaction_id: str, reason: str | None, input_range: object | None,
) -> None:
    rec = current_trace_recorder()
    if rec is not None:
        _safe(
            rec.compaction_started,
            compaction_id=compaction_id,
            reason=reason,
            input_range=input_range,
            logical_request_id=current_logical_request_id(),
        )


def compaction_finished(
    *, compaction_id: str, summary: object | None, status: str = "succeeded",
) -> None:
    rec = current_trace_recorder()
    if rec is not None:
        _safe(
            rec.compaction_finished,
            compaction_id=compaction_id,
            summary=summary,
            status=status,
            logical_request_id=current_logical_request_id(),
        )


class _CompactionTap:
    """把 ContextCompactionEvent 旁路给轨迹 recorder，再原样交给内层 sink。"""

    def __init__(self, inner: Any, event_type: type) -> None:
        self._inner = inner
        self._event_type = event_type

    async def emit(self, event: Any) -> None:
        if isinstance(event, self._event_type):
            rec = current_trace_recorder()
            if rec is not None:
                compaction_id = str(getattr(event, "compaction_id", "") or "")
                phase = str(getattr(event, "phase", "") or "")
                if phase == "started":
                    _safe(
                        rec.compaction_started,
                        compaction_id=compaction_id,
                        reason=None,
                        input_range=None,
                        logical_request_id=current_logical_request_id(),
                    )
                else:
                    status = (
                        "failed" if phase == "failed"
                        else "cancelled" if phase == "cancelled"
                        else "succeeded"
                    )
                    _safe(
                        rec.compaction_finished,
                        compaction_id=compaction_id,
                        summary=None,
                        status=status,
                        logical_request_id=current_logical_request_id(),
                    )
        emit = getattr(self._inner, "emit", None)
        if callable(emit):
            await cast(Callable[[Any], Awaitable[None]], emit)(event)

    def accepts(self, event_type: type) -> bool:
        accepts = getattr(self._inner, "accepts", None)
        if callable(accepts) and accepts(event_type):
            return True
        return event_type is self._event_type

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def wrap_trajectory_events(events: Any) -> Any:
    """Wrap an EventSink so compaction facts reach the active recorder."""
    if current_trace_recorder() is None:
        return events
    try:
        from nanobot.events import ContextCompactionEvent

        return _CompactionTap(events, ContextCompactionEvent)
    except Exception:
        logger.exception("trajectory event sink wrap failed")
        return events


# -------------------------------------------------------------------- helpers


def _response_blocks(response: Any) -> list[tuple[str, object]]:
    blocks: list[tuple[str, object]] = []
    reasoning = getattr(response, "reasoning_content", None)
    thinking_blocks = getattr(response, "thinking_blocks", None)
    if reasoning:
        blocks.append(("reasoning", reasoning))
    elif thinking_blocks:
        blocks.append(("reasoning", thinking_blocks))
    content = getattr(response, "content", None)
    if content:
        blocks.append(("text", content))
    tool_calls = getattr(response, "tool_calls", None)
    if tool_calls:
        blocks.append((
            "tool-call",
            [
                {"id": getattr(call, "id", None), "name": getattr(call, "name", None),
                 "arguments": getattr(call, "arguments", None)}
                for call in tool_calls
            ],
        ))
    return blocks


def _usage_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    reported = _opt_int(getattr(usage, "reported_tokens", 0)) or 0
    estimated = _opt_int(getattr(usage, "estimated_tokens", 0)) or 0
    if reported and estimated:
        source = "mixed"
    elif reported:
        source = "reported"
    elif estimated:
        source = "estimated"
    else:
        source = "missing"
    return {
        "inputTokens": _opt_int(getattr(usage, "input_tokens", None)),
        "outputTokens": _opt_int(getattr(usage, "output_tokens", None)),
        "totalTokens": _opt_int(getattr(usage, "total_tokens", None)),
        "cacheReadTokens": _opt_int(getattr(usage, "cache_read_tokens", None)),
        "cacheWriteTokens": _opt_int(getattr(usage, "cache_write_tokens", None)),
        "source": source,
    }


def _error_code(response: Any) -> str | None:
    kind = getattr(response, "error_kind", None)
    status = getattr(response, "error_status_code", None)
    if kind:
        return str(kind)
    return f"http_{status}" if status else None


def _opt_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = [
    "AttemptScope",
    "begin_attempt",
    "bind_request",
    "compaction_finished",
    "compaction_started",
    "context_injected",
    "execution_linked",
    "input_recorded",
    "model_requested",
    "new_logical_request_id",
    "recorder",
    "reset_request",
    "retry_scheduled",
    "route_changed",
    "run_finished",
    "run_started",
    "step_finished",
    "step_started",
    "tool_finished",
    "tool_requested",
    "tool_started",
    "wrap_trajectory_events",
]
