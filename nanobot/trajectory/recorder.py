"""轨迹记录器：把运行时观察点转成规范化事件候选。

Recorder 不做 IO 阻塞：事件进入 store 的有界队列，由单 writer 批量提交。
所有方法都是 fail-open 的——轨迹采集失败不能改变模型调用或工具执行语义。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any, cast
from uuid import uuid4

from loguru import logger

from nanobot.trajectory.models import (
    BodyRow,
    ContentMode,
    ContentRef,
    Coverage,
    EventCandidate,
    TraceContext,
    unavailable_ref,
)
from nanobot.trajectory.policy import ContentPolicy

_MAX_PREVIEW_ACCUMULATION = 4000
_DELTA_FLUSH_CHARS = 512
_DELTA_FLUSH_SECONDS = 0.25


class TraceRecorder:
    """一次 run 的观察窗口。"""

    def __init__(
        self,
        context: TraceContext,
        *,
        policy: ContentPolicy,
        sink: Callable[[EventCandidate], bool],
        run_body_budget_bytes: int,
    ) -> None:
        self.context = context
        self.policy = policy
        self._sink = sink
        self._body_budget = run_body_budget_bytes
        self._body_bytes = 0
        self._degraded_reason: str | None = None
        self._gap_emitted = False
        self._run_finished = False
        self._started = False
        self._turn_open = False
        self._step: int | None = None
        self._model_orders: dict[int, int] = {}
        self._logical_by_call: dict[str, str | None] = {}
        self._attempt_index: dict[str, int] = {}
        self._preview: dict[tuple[str, int], str] = {}
        self._block_kind: dict[tuple[str, int], str] = {}
        self._pending: dict[tuple[str, int], list[str]] = {}
        self._last_flush: dict[tuple[str, int], float] = {}
        self._started_monotonic = time.monotonic()
        self._started_ms = context_start_ms()

    # ------------------------------------------------------------------ utils

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _body(
        self, value: object, content_type: str = "text/plain",
    ) -> tuple[ContentRef, BodyRow | None]:
        if self._degraded_reason is not None:
            return unavailable_ref("budget_exceeded", content_type), None
        try:
            ref, body = self.policy.make_body(value, content_type)
        except Exception:
            logger.exception("trajectory body normalization failed")
            return unavailable_ref("capture_failed", content_type), None
        size = body.stored_bytes if body is not None else 0
        if size and self._body_bytes + size > self._body_budget:
            self._degrade("budget_exceeded")
            return unavailable_ref("budget_exceeded", content_type), None
        self._body_bytes += size
        return ref, body

    def _degrade(self, reason: str) -> None:
        if self._degraded_reason is None:
            self._degraded_reason = reason
        if self._gap_emitted:
            return
        self._gap_emitted = True
        self._emit(
            "trace.gap",
            {"reasons": [reason], "coverage": self.coverage().to_dict()},
        )

    def coverage(self) -> Coverage:
        mode = cast(ContentMode, self.policy.content_mode)
        if self._degraded_reason is None:
            return Coverage(
                status="complete",
                content_mode=mode,
                attempt_visibility="provider_call",
            )
        return Coverage(
            status="partial",
            reasons=(self._degraded_reason,),
            content_mode=mode,
            attempt_visibility="provider_call",
        )

    def _emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        bodies: Iterable[BodyRow] = (),
        step: int | None | object = ...,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        logical_request_id: str | None = None,
        model_call_id: str | None = None,
        tool_call_id: str | None = None,
        execution_id: str | None = None,
        ignorable: bool = False,
        time_ms: int | None = None,
    ) -> None:
        try:
            resolved_step = self._step if step is ... else step
            candidate = EventCandidate(
                session_id=self.context.session_id,
                trace_id=self.context.trace_id,
                run_id=self.context.run_id,
                turn_id=self.context.turn_id,
                step=resolved_step if isinstance(resolved_step, int) else None,
                span_id=span_id,
                parent_span_id=parent_span_id or self.context.parent_span_id,
                execution_id=execution_id or self.context.execution_id,
                logical_request_id=logical_request_id,
                model_call_id=model_call_id,
                tool_call_id=tool_call_id,
                type=event_type,
                ignorable=ignorable,
                time_ms=time_ms if time_ms is not None else self._now_ms(),
                payload=payload,
                bodies=tuple(bodies),
            )
            if not self._sink(candidate):
                self._degrade("queue_overflow")
        except Exception:
            logger.exception("trajectory emit failed for {}", event_type)

    # ------------------------------------------------------------- run / turn

    def start(self, *, purpose: str | None = None, visible: bool = False) -> None:
        if self._started:
            return
        self._started = True
        ctx = self.context
        payload = {
            "runId": ctx.run_id,
            "traceId": ctx.trace_id,
            "source": ctx.source,
            "purpose": purpose,
            "visible": visible,
            "parentRunId": ctx.parent_run_id,
            "agentId": ctx.agent_id,
            "agentName": ctx.agent_name,
            "ephemeral": ctx.ephemeral,
            "coverage": self.coverage().to_dict(),
        }
        self._emit("run.started", payload, span_id=ctx.run_id, step=None)
        if ctx.turn_id:
            self._turn_open = True
            self._emit(
                "turn.started",
                {"source": ctx.source, "turnId": ctx.turn_id},
                step=None,
            )

    def input_recorded(
        self, text: object, *, source: str = "user", media_refs: list[Any] | None = None,
    ) -> str:
        input_id = uuid4().hex
        ref, body = self._body(text)
        self._emit(
            "input.recorded",
            {
                "inputId": input_id,
                "contentRef": ref.to_dict(),
                "source": source,
                "mediaRefs": media_refs or [],
            },
            bodies=(body,) if body else (),
            step=-1,
        )
        return input_id

    def context_injected(
        self, kind: str, text: object, *, source: str | None = None,
        index: int = 0, logical_request_id: str | None = None,
    ) -> None:
        if not _has_content(text):
            return
        ref, body = self._body(text)
        self._emit(
            "context.injected",
            {
                "kind": kind,
                "source": source or kind,
                "index": index,
                "contentRef": ref.to_dict(),
                "logicalRequestId": logical_request_id,
            },
            bodies=(body,) if body else (),
            span_id=logical_request_id,
            logical_request_id=logical_request_id,
        )

    def turn_finished(self, outcome: str | None) -> None:
        if not self.context.turn_id or not self._turn_open:
            return
        self._turn_open = False
        self._emit(
            "turn.finished",
            {"outcome": outcome, "turnId": self.context.turn_id, "source": self.context.source},
            step=None,
        )

    def finish(self, *, status: str = "succeeded", stop_reason: str | None = None) -> None:
        if self._run_finished:
            return
        self._flush_all_deltas()
        if self._step is not None:
            self.step_finished(
                self._step, "failed" if status not in {"succeeded", "cancelled"} else status,
            )
        duration = max(0, int((time.monotonic() - self._started_monotonic) * 1000))
        self._emit(
            "run.finished",
            {
                "runId": self.context.run_id,
                "status": status,
                "stopReason": stop_reason,
                "durationMs": duration,
                "coverage": self.coverage().to_dict(),
            },
            step=None,
            time_ms=self._now_ms(),
        )
        self.turn_finished(stop_reason or status)
        self._run_finished = True

    # ----------------------------------------------------------------- steps

    def step_started(self, step: int) -> None:
        if self._step is not None and self._step != step:
            self.step_finished(self._step)
        self._step = step
        self._emit("step.started", {"step": step, "status": "running"}, step=step,
                   span_id=f"step:{self.context.run_id}:{step}")

    def step_finished(self, step: int, status: str = "succeeded") -> None:
        if self._step == step:
            self._step = None
        self._emit("step.finished", {"step": step, "status": status}, step=step,
                   span_id=f"step:{self.context.run_id}:{step}")

    # ---------------------------------------------------------------- models

    def model_requested(
        self,
        logical_request_id: str,
        *,
        request_snapshot: object,
        purpose: str | None = None,
        completeness: str = "normalized",
    ) -> None:
        step = self._step if self._step is not None else 0
        order = self._model_orders.get(step, 0)
        self._model_orders[step] = order + 1
        ref, body = self._body(request_snapshot, "application/json")
        self._emit(
            "model.requested",
            {
                "logicalRequestId": logical_request_id,
                "modelOrder": order,
                "purpose": purpose,
                "requestCompleteness": completeness,
                "requestRef": ref.to_dict(),
            },
            bodies=(body,) if body else (),
            span_id=logical_request_id,
            logical_request_id=logical_request_id,
            step=step,
        )

    def attempt_started(
        self,
        model_call_id: str,
        *,
        attempt: int,
        provider: str,
        model: str,
        retry_of_call_id: str | None = None,
        attempt_visibility: str = "provider_call",
        logical_request_id: str | None = None,
    ) -> None:
        request_id = logical_request_id
        self._logical_by_call[model_call_id] = request_id
        self._attempt_index[model_call_id] = attempt
        self._emit(
            "model.attempt.started",
            {
                "logicalRequestId": request_id,
                "modelCallId": model_call_id,
                "attempt": attempt,
                "retryOfCallId": retry_of_call_id,
                "provider": provider,
                "model": model,
                "attemptVisibility": attempt_visibility,
            },
            span_id=model_call_id,
            parent_span_id=request_id,
            logical_request_id=request_id,
            model_call_id=model_call_id,
        )

    def model_delta(self, model_call_id: str, block_index: int, block_kind: str,
                    text: str) -> None:
        if not text:
            return
        key = (model_call_id, block_index)
        self._block_kind[key] = block_kind
        accumulated = self._preview.get(key, "")
        if len(accumulated) < _MAX_PREVIEW_ACCUMULATION:
            self._preview[key] = accumulated + text[:_MAX_PREVIEW_ACCUMULATION - len(accumulated)]
        pending = self._pending.setdefault(key, [])
        pending.append(text)
        buffered = sum(len(item) for item in pending)
        last = self._last_flush.get(key, 0.0)
        if buffered >= _DELTA_FLUSH_CHARS or (time.monotonic() - last) >= _DELTA_FLUSH_SECONDS:
            self._flush_delta(model_call_id, block_index, block_kind)

    def _flush_delta(self, model_call_id: str, block_index: int, block_kind: str) -> None:
        key = (model_call_id, block_index)
        pending = self._pending.pop(key, None)
        if not pending:
            return
        self._last_flush[key] = time.monotonic()
        text = "".join(pending)
        ref, body = self._body(text)
        preview = self._preview.get(key, "").strip()
        self._emit(
            "model.output.delta",
            {
                "logicalRequestId": self._logical_by_call.get(model_call_id),
                "modelCallId": model_call_id,
                "attempt": self._attempt_index.get(model_call_id),
                "blockIndex": block_index,
                "blockKind": block_kind,
                "contentRef": ref.to_dict(),
                "preview": preview[-_MAX_PREVIEW_ACCUMULATION:],
            },
            bodies=(body,) if body else (),
            span_id=model_call_id,
            parent_span_id=self._logical_by_call.get(model_call_id),
            logical_request_id=self._logical_by_call.get(model_call_id),
            model_call_id=model_call_id,
        )

    def _flush_all_deltas(self) -> None:
        for key in list(self._pending):
            self._flush_delta(key[0], key[1], self._block_kind.get(key, "text"))

    def attempt_finished(
        self,
        model_call_id: str,
        *,
        status: str,
        finish_reason: str | None,
        blocks: Iterable[tuple[str, object]],
        usage: dict[str, Any] | None = None,
        ttft_ms: int | None = None,
        generation_ms: int | None = None,
        error_code: str | None = None,
        error_kind: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        provider_response_id: str | None = None,
        started_at_ms: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        for key in [key for key in self._pending if key[0] == model_call_id]:
            self._flush_delta(model_call_id, key[1], self._block_kind.get(key, "text"))
        block_payload: list[dict[str, Any]] = []
        bodies: list[BodyRow] = []
        for index, (kind, value) in enumerate(blocks):
            if not _has_content(value):
                continue
            ref, body = self._body(value)
            if body is not None:
                bodies.append(body)
            block_payload.append({"index": index, "kind": kind, "content": ref.to_dict()})
        self._emit(
            "model.attempt.finished",
            {
                "logicalRequestId": self._logical_by_call.get(model_call_id),
                "modelCallId": model_call_id,
                "attempt": self._attempt_index.get(model_call_id),
                "status": status,
                "finishReason": finish_reason,
                "blocks": block_payload,
                "usage": usage,
                "ttftMs": ttft_ms,
                "generationMs": generation_ms,
                "errorCode": error_code,
                "errorKind": error_kind,
                "provider": provider,
                "model": model,
                "providerResponseId": provider_response_id,
                "attemptVisibility": "provider_call",
                "startedAtMs": started_at_ms,
                "durationMs": duration_ms,
            },
            bodies=tuple(bodies),
            span_id=model_call_id,
            parent_span_id=self._logical_by_call.get(model_call_id),
            logical_request_id=self._logical_by_call.get(model_call_id),
            model_call_id=model_call_id,
        )
        self._logical_by_call.pop(model_call_id, None)
        self._attempt_index.pop(model_call_id, None)
        for key in [key for key in self._preview if key[0] == model_call_id]:
            self._preview.pop(key, None)
            self._block_kind.pop(key, None)

    def retry_scheduled(
        self, *, previous_call_id: str, attempt: int, delay_ms: int | None,
        error_kind: str | None, logical_request_id: str | None,
    ) -> None:
        self._emit(
            "model.retry.scheduled",
            {
                "logicalRequestId": logical_request_id,
                "previousCallId": previous_call_id,
                "attempt": attempt,
                "delayMs": delay_ms,
                "errorKind": error_kind,
            },
            logical_request_id=logical_request_id,
        )

    def route_changed(
        self, *, logical_request_id: str | None, previous: str | None,
        current: str | None, reason: str | None = None,
    ) -> None:
        self._emit(
            "model.route.changed",
            {
                "logicalRequestId": logical_request_id,
                "from": previous,
                "to": current,
                "reason": reason,
            },
            logical_request_id=logical_request_id,
        )

    # ----------------------------------------------------------------- tools

    def tool_requested(
        self,
        *,
        tool_call_id: str,
        provider_call_id: str | None,
        name: str,
        arguments: object,
        schema: object | None,
        model_order: int,
        origin: str = "agent",
    ) -> None:
        args_ref, args_body = self._body(arguments, "application/json")
        schema_ref = None
        schema_body = None
        if schema is not None:
            schema_ref, schema_body = self._body(schema, "application/json")
        bodies = tuple(item for item in (args_body, schema_body) if item is not None)
        self._emit(
            "tool.requested",
            {
                "toolCallId": tool_call_id,
                "providerCallId": provider_call_id,
                "name": name,
                "origin": origin,
                "argumentsRef": args_ref.to_dict(),
                "schemaRef": schema_ref.to_dict() if schema_ref else None,
                "modelOrder": model_order,
            },
            bodies=bodies,
            span_id=tool_call_id,
            tool_call_id=tool_call_id,
        )

    def tool_started(
        self, *, tool_call_id: str, effective_arguments: object | None = None,
        queue_ms: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {"toolCallId": tool_call_id, "queueMs": queue_ms}
        bodies: tuple[BodyRow, ...] = ()
        if effective_arguments is not None:
            ref, body = self._body(effective_arguments, "application/json")
            payload["effectiveArgumentsRef"] = ref.to_dict()
            if body is not None:
                bodies = (body,)
        self._emit(
            "tool.started", payload, bodies=bodies, span_id=tool_call_id,
            tool_call_id=tool_call_id,
        )

    def tool_finished(
        self,
        *,
        tool_call_id: str,
        status: str,
        result: object | None = None,
        error: object | None = None,
        duration_ms: int | None = None,
        side_effect_outcome: str | None = None,
        skipped_reason: str | None = None,
        model_observation: object | None = None,
    ) -> None:
        bodies: list[BodyRow] = []
        payload: dict[str, Any] = {
            "toolCallId": tool_call_id,
            "status": status,
            "durationMs": duration_ms,
            "sideEffectOutcome": side_effect_outcome,
            "skippedReason": skipped_reason,
        }
        if result is not None:
            ref, body = self._body(result)
            payload["resultRef"] = ref.to_dict()
            if body is not None:
                bodies.append(body)
        if error is not None:
            ref, body = self._body(error)
            payload["errorRef"] = ref.to_dict()
            if body is not None:
                bodies.append(body)
        if model_observation is not None:
            ref, body = self._body(model_observation)
            payload["modelObservationRef"] = ref.to_dict()
            if body is not None:
                bodies.append(body)
        self._emit(
            "tool.finished", payload, bodies=tuple(bodies), span_id=tool_call_id,
            tool_call_id=tool_call_id,
        )

    def execution_linked(
        self, *, tool_call_id: str, invocation_id: str,
        child_run_id: str | None, child_session_id: str | None = None,
    ) -> None:
        self._emit(
            "execution.linked",
            {
                "toolCallId": tool_call_id,
                "invocationId": invocation_id,
                "childRunId": child_run_id,
                "childSessionId": child_session_id,
                "parentToolCallId": tool_call_id,
            },
            span_id=tool_call_id,
            tool_call_id=tool_call_id,
            execution_id=invocation_id,
        )

    # ------------------------------------------------------------ compaction

    def compaction_started(
        self, *, compaction_id: str, reason: str | None,
        input_range: object | None, logical_request_id: str | None,
    ) -> None:
        self._emit(
            "compaction.started",
            {
                "compactionId": compaction_id,
                "reason": reason,
                "inputRange": input_range,
                "logicalRequestId": logical_request_id,
            },
            span_id=compaction_id,
            logical_request_id=logical_request_id,
        )

    def compaction_finished(
        self, *, compaction_id: str, summary: object | None,
        status: str = "succeeded", logical_request_id: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "compactionId": compaction_id,
            "status": status,
            "logicalRequestId": logical_request_id,
        }
        bodies: tuple[BodyRow, ...] = ()
        if summary is not None:
            ref, body = self._body(summary)
            payload["summaryRef"] = ref.to_dict()
            if body is not None:
                bodies = (body,)
        self._emit(
            "compaction.finished", payload, bodies=bodies, span_id=compaction_id,
            logical_request_id=logical_request_id,
        )


def context_start_ms() -> int:
    return int(time.time() * 1000)


def _has_content(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return len(cast(dict[object, object], value)) > 0
    if isinstance(value, (list, tuple)):
        return len(cast(list[object] | tuple[object, ...], value)) > 0
    return True


__all__ = ["TraceRecorder"]
