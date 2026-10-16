"""契约 §6.1：``AttemptEvidence``（WU-03 产出 → WU-04 消费）。

这是跨 WU 接缝，字段名与语义逐字照抄契约，不自行加料也不改名：判分侧只看见本对象持有的
证据，读取只能走 ``read_text`` / ``read_json`` / ``exists`` 三个入口，三者都必须先经
``resolve_inside`` 做目录包含判定，越界抛 ``PATH_OUTSIDE_ROOT``。

设计取舍：

- 对象 **frozen**，集合一律 ``tuple``：判分过程不能反向修改证据，也避免 WU-04 拿到中途被
  worker 追加的列表。
- 未登记产物（``artifact_id=None``）只能判失败、不可读取内容：三个入口先查登记表，未登记
  直接 ``ARTIFACT_NOT_REGISTERED``，不做"路径看起来在 workspace 里就读得到"的宽松处理。
- ``resolve_ref`` 未命中返回 ``None``（契约规定调用方把 ``None`` 当作证据不完整），不抛异常，
  否则判分侧要为每个坏引用写 try 分支。
- 模块只在服务进程内使用，不进 IPC、不进数据库，因此这里不引入 repository 依赖。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nanobot.evaluation.schemas import (
    ExecutionStatus,
    JsonObject,
    JsonValue,
    TraceEvent,
)
from nanobot.evaluation.validation import (
    resolve_inside,
    validate_relative_path,
)

ARTIFACT_NOT_REGISTERED = "ARTIFACT_NOT_REGISTERED"
EVIDENCE_ATTEMPT_MISMATCH = "EVIDENCE_ATTEMPT_MISMATCH"
EVIDENCE_SEQ_OUT_OF_ORDER = "EVIDENCE_SEQ_OUT_OF_ORDER"
REF_TRACE_PREFIX = "trace:"
REF_ARTIFACT_PREFIX = "artifact:"
TOOL_EVENT_TYPES = frozenset({"tool.started", "tool.completed", "tool.failed"})


class EvidenceError(ValueError):
    """证据对象构造或读取失败；``str(exc)`` 一定带错误码，供调用方直接映射。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class ToolCallEvidence:
    """一次工具调用的判分视图（契约 §6.1）。"""

    name: str
    arguments: JsonObject
    result: JsonValue | None
    is_error: bool
    seq: int
    turn: int | None = None
    iteration: int | None = None


@dataclass(frozen=True)
class EvidenceArtifact:
    """产物索引条目；``artifact_id=None`` 表示未登记，只能判失败。"""

    relative_path: str
    media_type: str
    size_bytes: int
    sha256: str
    artifact_id: uuid.UUID | None = None


@dataclass(frozen=True)
class AttemptEvidence:
    """本 attempt 判分可见的全部证据。"""

    attempt_id: str
    case_snapshot: dict[str, Any]
    final_answer: str | None
    stop_reason: str | None
    iterations: int
    trace_events: tuple[TraceEvent, ...]
    tool_calls: tuple[ToolCallEvidence, ...]
    artifacts: tuple[EvidenceArtifact, ...]
    workspace: Path
    trace_complete: bool
    execution_status: ExecutionStatus
    _by_path: Mapping[str, EvidenceArtifact] = field(
        init=False, default_factory=dict, repr=False
    )

    def __post_init__(self) -> None:
        try:
            parsed = uuid.UUID(self.attempt_id)
        except (TypeError, ValueError) as exc:
            raise EvidenceError("EVIDENCE_INVALID_ATTEMPT_ID", str(exc)) from exc
        object.__setattr__(self, "attempt_id", str(parsed))
        workspace = Path(self.workspace)
        if not workspace.is_absolute():
            raise EvidenceError("EVIDENCE_WORKSPACE_NOT_RESOLVED", str(workspace))
        object.__setattr__(self, "workspace", workspace.resolve())
        object.__setattr__(self, "trace_events", tuple(self.trace_events))
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        object.__setattr__(self, "artifacts", tuple(self.artifacts))

        last_seq = 0
        for event in self.trace_events:
            if str(event.attempt_id) != self.attempt_id:
                raise EvidenceError(
                    EVIDENCE_ATTEMPT_MISMATCH, f"event seq={event.seq} 属于 {event.attempt_id}"
                )
            if event.seq <= last_seq:
                raise EvidenceError(
                    EVIDENCE_SEQ_OUT_OF_ORDER, f"seq={event.seq} 未严格递增（上一条 {last_seq}）"
                )
            last_seq = event.seq

        registered: dict[str, EvidenceArtifact] = {}
        for artifact in self.artifacts:
            validate_relative_path(artifact.relative_path)
            registered[artifact.relative_path] = artifact
        object.__setattr__(self, "_by_path", registered)

    # ------------------------------------------------------------------ 读取入口

    def exists(self, relative_path: str) -> bool:
        """路径是否指向本 attempt 内一个真实存在的已登记产物。"""
        target = self._resolve_registered(relative_path)
        return target.is_file()

    def read_text(self, relative_path: str) -> str:
        target = self._resolve_registered(relative_path)
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise EvidenceError("FILE_NOT_TEXT", f"{relative_path}: {exc}") from exc

    def read_json(self, relative_path: str) -> JsonValue:
        text = self.read_text(relative_path)
        try:
            return json.loads(text)
        except (ValueError, TypeError) as exc:
            raise EvidenceError("FILE_NOT_JSON", f"{relative_path}: {exc}") from exc

    # ------------------------------------------------------------------ 引用解析

    def resolve_ref(self, ref: str) -> TraceEvent | EvidenceArtifact | None:
        """把 ``trace:<attempt_id>:<seq>`` / ``artifact:<artifact_id>`` 解析为证据对象。"""
        if not isinstance(ref, str):
            return None
        if ref.startswith(REF_TRACE_PREFIX):
            return self._resolve_trace_ref(ref[len(REF_TRACE_PREFIX) :])
        if ref.startswith(REF_ARTIFACT_PREFIX):
            return self._resolve_artifact_ref(ref[len(REF_ARTIFACT_PREFIX) :])
        return None

    def trace_event(self, seq: int) -> TraceEvent | None:
        for event in self.trace_events:
            if event.seq == seq:
                return event
        return None

    def artifact(self, relative_path: str) -> EvidenceArtifact | None:
        return self._by_path.get(relative_path)

    def _resolve_trace_ref(self, remainder: str) -> TraceEvent | None:
        parts = remainder.split(":")
        if len(parts) != 2:
            return None
        attempt_part, seq_part = parts
        try:
            if uuid.UUID(attempt_part) != uuid.UUID(self.attempt_id):
                return None
            seq = int(seq_part)
        except (TypeError, ValueError):
            return None
        return self.trace_event(seq)

    def _resolve_artifact_ref(self, remainder: str) -> EvidenceArtifact | None:
        try:
            artifact_id = uuid.UUID(remainder)
        except (TypeError, ValueError):
            return None
        for artifact in self.artifacts:
            if artifact.artifact_id == artifact_id:
                return artifact
        return None

    def _resolve_registered(self, relative_path: str) -> Path:
        """已登记且属于当前 attempt 的产物才允许读取。"""
        # 顺序要紧：先做目录包含判定（契约 §6.1 要求越界抛 PATH_OUTSIDE_ROOT），
        # 再判登记状态，否则越界尝试会被 ARTIFACT_NOT_REGISTERED 掩盖成"没这个产物"。
        target = resolve_inside(self.workspace, relative_path)
        artifact = self._by_path.get(relative_path)
        if artifact is None or artifact.artifact_id is None:
            raise EvidenceError(ARTIFACT_NOT_REGISTERED, relative_path)
        return target


def tool_calls_from_events(events: Sequence[TraceEvent]) -> tuple[ToolCallEvidence, ...]:
    """从 ``tool.started`` / ``tool.completed`` / ``tool.failed`` 派生调用视图。

    同一次调用按 ``call_id`` 归并成一条，计数才不会把"开始 + 结束"数成两次调用；未收到终态
    事件的调用仍然出现一条，``result`` 保持 ``None``（未采集不补零）。``is_error`` 直接取事件
    里项目 ``ToolResult.is_error`` 的布尔值，绝不靠文本前缀猜。
    """
    ordered: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.type not in TOOL_EVENT_TYPES:
            continue
        payload = event.payload
        name = payload.get("name")
        if not isinstance(name, str):
            continue
        call_id = event.call_id or f"seq-{event.seq}"
        entry = ordered.setdefault(
            call_id,
            {"name": name, "arguments": {}, "result": None, "is_error": False, "seq": event.seq},
        )
        entry["seq"] = min(entry["seq"], event.seq)
        arguments = payload.get("arguments")
        if isinstance(arguments, Mapping):
            entry["arguments"] = dict(arguments)
        if event.type in ("tool.completed", "tool.failed"):
            entry["result"] = payload.get("result")
            entry["is_error"] = bool(payload.get("is_error", False))
            entry["turn"] = event.turn
            entry["iteration"] = event.iteration
        else:
            entry.setdefault("turn", event.turn)
            entry.setdefault("iteration", event.iteration)
    return tuple(
        ToolCallEvidence(
            name=str(item["name"]),
            arguments=item["arguments"],
            result=item["result"],
            is_error=bool(item["is_error"]),
            seq=int(item["seq"]),
            turn=item.get("turn"),
            iteration=item.get("iteration"),
        )
        for item in ordered.values()
    )


def build_attempt_evidence(
    *,
    attempt_id: str,
    case_snapshot: Mapping[str, Any],
    workspace: Path,
    trace_events: Sequence[TraceEvent],
    artifacts: Sequence[EvidenceArtifact] = (),
    final_answer: str | None = None,
    stop_reason: str | None = None,
    iterations: int = 0,
    trace_complete: bool = True,
    execution_status: ExecutionStatus = "succeeded",
) -> AttemptEvidence:
    """服务侧组装入口：调用视图只从已校验事件派生，不接受外部直接构造。"""
    events = tuple(sorted(trace_events, key=lambda event: event.seq))
    return AttemptEvidence(
        attempt_id=attempt_id,
        case_snapshot=dict(case_snapshot),
        final_answer=final_answer,
        stop_reason=stop_reason,
        iterations=iterations,
        trace_events=events,
        tool_calls=tool_calls_from_events(events),
        artifacts=tuple(artifacts),
        workspace=Path(workspace),
        trace_complete=trace_complete,
        execution_status=execution_status,
    )
