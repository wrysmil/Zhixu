"""T06：Worker 私有 NDJSON 管道（契约 §5）。

父子进程只通过 stdin/stdout 的 UTF-8 NDJSON 通信，日志只进 stderr。父进程这一侧必须做到：

- 只接受**当前 attempt_id + 协议版本 1 + 状态机合法**的帧，其余显式失败；
- 重复 ``event_id`` 的 Trace 帧不重复写入（返回 ``duplicated``，不算错误）；
- 超长帧（>3 MiB）、半行 JSONL、空行与非法 JSON 都要报出具体错误码，不能吞成一条正常事件；
- 「成功」终态必须同时满足 result 已收到 + 退出码为 0（Trace/产物密封由上层再查），
  因此这里提供 :meth:`ParentIpc.can_seal_succeeded` 与 :meth:`ParentIpc.exit_reason_without_result`。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from nanobot.evaluation.schemas import AttemptError, TraceEvent, WorkerResult

Mib = 1024 * 1024
#: 契约 §5：stdin/stdout 私有管道的行上限。
MAX_FRAME_BYTES = 3 * Mib
IPC_PROTOCOL_VERSION = 1

FrameType = Literal["ready", "trace", "result"]
FRAME_TYPES: frozenset[str] = frozenset({"ready", "trace", "result"})

_RESULT_ADAPTER = TypeAdapter(WorkerResult)


class IpcError(ValueError):
    """带错误码的 IPC 失败；码字进入 ``str(exc)``，供上层原样落 ``errors[].code``。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class IpcOutcome:
    """一帧的处理结果。``duplicated`` 为真表示该 Trace 已按 event_id 落过。"""

    frame_type: str
    duplicated: bool = False
    event: TraceEvent | None = None


def encode_frame(payload: Mapping[str, Any]) -> bytes:
    """编码为单行 UTF-8 NDJSON；超上限立刻失败，不做静默截断。"""
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(raw) > MAX_FRAME_BYTES:
        raise IpcError("IPC_FRAME_TOO_LARGE", f"{len(raw)} bytes")
    return raw + b"\n"


def encode_boot(boot: Mapping[str, Any]) -> bytes:
    """父→子只有一帧 boot，发出后 stdin 立即关闭；profile_bundle 只存在于管道里。"""
    if boot.get("type") != "boot":
        raise IpcError("IPC_FRAME_TYPE_UNKNOWN", str(boot.get("type")))
    if boot.get("role") not in {"agent", "judge"}:
        raise IpcError("IPC_BOOT_ROLE_UNKNOWN", str(boot.get("role")))
    if boot.get("protocol_version") != IPC_PROTOCOL_VERSION:
        raise IpcError(
            "IPC_PROTOCOL_MISMATCH", f"boot={boot.get('protocol_version')!r}"
        )
    attempt_id = str(boot.get("attempt_id") or "")
    if not attempt_id.strip():
        raise IpcError("IPC_ATTEMPT_MISMATCH", "boot carries no attempt_id")
    return encode_frame(boot)


def read_frame_lines(stream: bytes) -> list[dict[str, Any]]:
    """把一段管道输出解码成帧列表。

    末尾没有换行的残留只有「能解析成对象」才接受，否则算半行 —— 活动段按完整行读取，
    截断行必须被识别出来而不是当成一条正常事件。
    """
    parts = stream.split(b"\n")
    remainder = parts[-1]
    frames: list[dict[str, Any]] = []
    for index, line in enumerate(parts[:-1]):
        if not line.strip():
            raise IpcError("IPC_INVALID_JSON", f"blank line at position {index}")
        frames.append(_decode_line(line, truncated=False))
    if remainder:
        frames.append(_decode_line(remainder, truncated=True))
    return frames


def _decode_line(line: bytes, *, truncated: bool) -> dict[str, Any]:
    if len(line) > MAX_FRAME_BYTES:
        raise IpcError("IPC_FRAME_TOO_LARGE", f"{len(line)} bytes")
    try:
        decoded = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        if truncated:
            raise IpcError("IPC_TRUNCATED_FRAME", str(exc)) from exc
        raise IpcError("IPC_INVALID_JSON", str(exc)) from exc
    if not isinstance(decoded, dict):
        raise IpcError("IPC_INVALID_JSON", "frame is not a JSON object")
    return decoded


class ParentIpc:
    """父进程侧的帧校验与状态机。"""

    def __init__(self, *, attempt_id: str) -> None:
        if not attempt_id.strip():
            raise IpcError("IPC_ATTEMPT_MISMATCH", "parent needs an attempt_id")
        self._attempt_id = attempt_id
        self._events: list[TraceEvent] = []
        self._event_ids: set[str] = set()
        self._result: dict[str, Any] | None = None
        self._result_errors: tuple[AttemptError, ...] = ()
        self._ready = False

    @property
    def attempt_id(self) -> str:
        return self._attempt_id

    @property
    def ready_received(self) -> bool:
        return self._ready

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    @property
    def result(self) -> dict[str, Any] | None:
        return self._result

    @property
    def result_errors(self) -> tuple[AttemptError, ...]:
        """按发生顺序保留的多项错误；后发生的不得替换先发生的。"""
        return self._result_errors

    def accept(self, frame: Mapping[str, Any]) -> IpcOutcome:
        frame_type = str(frame.get("type") or "")
        if frame_type not in FRAME_TYPES:
            raise IpcError("IPC_FRAME_TYPE_UNKNOWN", frame_type or "<missing>")
        self._check_attempt(frame)
        if frame_type == "ready":
            return self._accept_ready(frame)
        if not self._ready:
            raise IpcError("IPC_STATE_ORDER", f"{frame_type} arrives before ready")
        if frame_type == "trace":
            return self._accept_trace(frame)
        return self._accept_result(frame)

    def can_seal_succeeded(self, *, exit_code: int) -> bool:
        """成功终态的必要条件（Trace/artifact 密封由调用方在同一判断里再查）。"""
        return exit_code == 0 and self._result is not None and (
            self._result.get("execution_status") == "succeeded"
        )

    def exit_reason_without_result(self) -> str | None:
        return None if self._result is not None else "IPC_RESULT_MISSING"

    # ------------------------------------------------------------------ 分支

    def _check_attempt(self, frame: Mapping[str, Any]) -> None:
        attempt_id = str(frame.get("attempt_id") or "")
        if attempt_id != self._attempt_id:
            raise IpcError(
                "IPC_ATTEMPT_MISMATCH", f"frame={attempt_id!r} expected={self._attempt_id!r}"
            )

    def _accept_ready(self, frame: Mapping[str, Any]) -> IpcOutcome:
        if frame.get("protocol_version") != IPC_PROTOCOL_VERSION:
            raise IpcError(
                "IPC_PROTOCOL_MISMATCH",
                f"ready={frame.get('protocol_version')!r}",
            )
        if self._ready or self._result is not None:
            raise IpcError("IPC_STATE_ORDER", "ready already received")
        self._ready = True
        return IpcOutcome(frame_type="ready")

    def _accept_trace(self, frame: Mapping[str, Any]) -> IpcOutcome:
        if self._result is not None:
            raise IpcError("IPC_STATE_ORDER", "trace arrives after result")
        raw = frame.get("event")
        if not isinstance(raw, dict):
            raise IpcError("IPC_TRACE_INVALID", "event is missing or not an object")
        try:
            event = TraceEvent.model_validate(raw)
        except ValidationError as exc:
            raise IpcError("IPC_TRACE_INVALID", str(exc)) from exc
        if str(event.attempt_id) != self._attempt_id:
            raise IpcError(
                "IPC_ATTEMPT_MISMATCH", f"event={event.attempt_id} expected={self._attempt_id}"
            )
        key = str(event.event_id)
        if key in self._event_ids:
            return IpcOutcome(frame_type="trace", duplicated=True)
        self._event_ids.add(key)
        self._events.append(event)
        return IpcOutcome(frame_type="trace", duplicated=False, event=event)

    def _accept_result(self, frame: Mapping[str, Any]) -> IpcOutcome:
        if self._result is not None:
            raise IpcError("IPC_STATE_ORDER", "result already received")
        try:
            parsed = _RESULT_ADAPTER.validate_python(dict(frame))
        except ValidationError as exc:
            raise IpcError("IPC_RESULT_INVALID", str(exc)) from exc
        self._result = parsed.model_dump(mode="json")
        self._result_errors = tuple(parsed.errors)
        return IpcOutcome(frame_type="result")
