"""T06：Trace 采集与分段持久化（契约 §5 / spec §9）。

口径全部来自契约硬数字：事件正文 16 KiB、分段 1 MiB、单 attempt 20 MiB、seq 从 1 严格
递增、重复 event_id 不重复写入、截断/坏行必须让 ``trace_complete`` 变 false。

设计取舍：

- **落盘即刷盘**：每条事件写完立刻 ``flush``，段只在 ``seal()`` 时 ``fsync``。崩溃后活动段
  允许出现半行，读取侧把它当作不完整证据，而不是丢数据。
- **正文外移**：超 16 KiB 的事件正文整体落到 ``artifacts/<artifact_id>.txt``，trace 行只保留
  ``truncated`` 与 ``artifact_id``；这样单行永远不可能顶破 1 MiB 分段，也不会把敏感大块留在
  索引里。
- **预算是足迹而非段大小**：20 MiB 上限统计 trace 段 + 外移 artifact 的合计字节，触顶后停止
  落盘并把 ``trace.incomplete`` 缓存在内存里，由 ``seal()`` 作为唯一例外补写，保证标记不丢。
- 目录/文件路径由服务侧生成，本模块不解释用户输入；写盘失败抛 ``TRACE_WRITE_FAILED`` 并把
  trace 标记为不完整，调用方（worker）负责保留原始执行错误。
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nanobot.evaluation.schemas import TraceEvent

#: 契约 §5：事件正文最多 16 KiB，超出写受控文本 artifact。
MAX_EVENT_BODY_BYTES = 16 * 1024
#: 契约 §5：分段 JSONL 每段最大 1 MiB。
MAX_SEGMENT_BYTES = 1024 * 1024
#: 契约 §5：单 attempt Trace（含外移正文）上限 20 MiB。
MAX_TRACE_BYTES = 20 * 1024 * 1024

SEGMENT_PREFIX = "trace-"
SEGMENT_SUFFIX = ".jsonl"
ARTIFACTS_DIRNAME = "artifacts"
MANIFEST_NAME = "manifest.json"
_INCOMPLETE_MARKER_TYPE = "trace.incomplete"


class TraceError(RuntimeError):
    """Trace 采集/落盘错误；``code`` 是 worker 与报告使用的稳定事件名。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class SegmentInfo:
    """manifest 中一条段的索引。"""

    name: str
    event_count: int
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class TraceManifest:
    """``seal()`` 的结果，服务侧据此登记 artifact 索引并判定成功终态。"""

    attempt_id: str
    manifest_path: Path
    trace_complete: bool
    event_count: int
    total_bytes: int
    segments: tuple[SegmentInfo, ...]
    artifact_count: int


@dataclass(frozen=True)
class TraceRead:
    """读取侧结果；``events`` 只含完整且可校验的行。"""

    events: tuple[TraceEvent, ...]
    trace_complete: bool
    sealed: bool


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _segment_name(index: int) -> str:
    return f"{SEGMENT_PREFIX}{index:06d}{SEGMENT_SUFFIX}"


def _dump_line(event: TraceEvent) -> str:
    return json.dumps(
        event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    )


class TraceCollector:
    """单个 attempt 的 Trace 写入器。

    依赖显式注入（attempt_id + trace_dir），没有全局状态，因此可以在测试里直接构造两个
    attempt 的采集器互不干扰。
    """

    def __init__(self, *, attempt_id: str, trace_dir: Path) -> None:
        try:
            parsed = uuid.UUID(attempt_id)
        except (TypeError, ValueError) as exc:  # pragma: no cover - 防御性
            raise TraceError("TRACE_INVALID_ATTEMPT_ID", str(exc)) from exc
        self._attempt_id = str(parsed)
        self._trace_dir = Path(trace_dir)
        self._artifacts_dir = self._trace_dir / ARTIFACTS_DIRNAME
        self._segments: list[SegmentInfo] = []
        self._segment_counts: dict[str, int] = {}
        self._segment_index = 0
        self._active_path: Path | None = None
        self._active_size = 0
        self._handle: Any = None
        self._event_ids: set[str] = set()
        self._event_count = 0
        self._artifact_count = 0
        self._total_bytes = 0
        self._next_seq = 1
        self._trace_complete = True
        self._limit_reached = False
        self._incomplete_events: list[TraceEvent] = []
        self._manifest: TraceManifest | None = None

    # ------------------------------------------------------------------ 属性

    @property
    def attempt_id(self) -> str:
        return self._attempt_id

    @property
    def trace_dir(self) -> Path:
        return self._trace_dir

    @property
    def trace_complete(self) -> bool:
        """出现预算触顶、写盘失败或坏行时变 false；契约 §5 禁止此时判通过。"""
        return self._trace_complete

    @property
    def limit_reached(self) -> bool:
        return self._limit_reached

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def next_seq(self) -> int:
        """调用方（worker）用它给事件编号，保证 seq 严格递增。"""
        return self._next_seq

    @property
    def buffered_incomplete_events(self) -> tuple[TraceEvent, ...]:
        """触顶后停止落盘，``trace.incomplete`` 先留在内存，由 ``seal()`` 补写。"""
        return tuple(self._incomplete_events)

    # ------------------------------------------------------------------ 写入

    def append(self, event: TraceEvent) -> TraceEvent | None:
        """写入一条事件。

        返回持久化后的事件；重复 ``event_id`` 返回 ``None``（契约 §5：重发不重复写入）；
        预算触顶后同样返回 ``None``，但已经把 ``trace.incomplete`` 记进缓存。
        """
        if str(event.attempt_id) != self._attempt_id:
            raise TraceError(
                "TRACE_ATTEMPT_MISMATCH", f"event belongs to {event.attempt_id}"
            )
        event_id = str(event.event_id)
        if event_id in self._event_ids:
            return None
        if self._limit_reached:
            # 触顶后一律拒绝：调用方拿到 None 就知道证据已经不再增长。
            return None
        if event.seq != self._next_seq:
            raise TraceError(
                "TRACE_SEQ_OUT_OF_ORDER", f"expected {self._next_seq}, got {event.seq}"
            )

        payload_text = json.dumps(event.payload, ensure_ascii=False)
        body_bytes = len(payload_text.encode("utf-8"))
        if body_bytes > MAX_EVENT_BODY_BYTES:
            artifact_id = uuid.uuid4()
            pending = event.model_copy(
                update={
                    "payload": {
                        "truncated": True,
                        "artifact_id": str(artifact_id),
                        "body_bytes": body_bytes,
                    }
                }
            )
            line_bytes = len(_dump_line(pending).encode("utf-8")) + 1
            projected = body_bytes + line_bytes
        else:
            artifact_id = None
            pending = event
            line_bytes = len(_dump_line(pending).encode("utf-8")) + 1
            projected = line_bytes

        # 预算按“外移正文 + trace 行”的合计足迹预留，触顶后不再写任何字节。
        if self._total_bytes + projected > MAX_TRACE_BYTES:
            self._limit_reached = True
            self.mark_incomplete(
                "TRACE_BUDGET_EXCEEDED",
                detail={
                    "dropped_seq": event.seq,
                    "dropped_type": event.type,
                    "bytes": self._total_bytes,
                },
            )
            return None
        if artifact_id is not None:
            self._write_artifact(artifact_id, payload_text)
        if not self._write_line(raw=_dump_line(pending).encode("utf-8") + b"\n"):
            return None
        self._event_ids.add(event_id)
        self._event_count += 1
        self._next_seq += 1
        return pending

    def mark_incomplete(self, reason: str, *, detail: dict[str, Any] | None = None) -> TraceEvent:
        """登记 Trace 不完整标记；下一次 ``seal()`` 会把标记补进日志。"""
        payload: dict[str, Any] = {"reason": reason}
        if detail:
            payload.update(detail)
        event = TraceEvent(
            schema_version=1,
            attempt_id=uuid.UUID(self._attempt_id),
            seq=self._next_seq,
            event_id=uuid.uuid4(),
            type=_INCOMPLETE_MARKER_TYPE,
            turn=None,
            iteration=None,
            call_id=None,
            ts=_utc_now(),
            elapsed_ms=0,
            payload=payload,
        )
        self._trace_complete = False
        self._incomplete_events.append(event)
        return event

    def seal(self) -> TraceManifest:
        """flush + fsync 活动段，原子写 manifest；重复调用返回同一份结果。"""
        if self._manifest is not None:
            return self._manifest
        self._flush_buffered_incomplete_events()
        self._close_active()
        manifest_path = self._trace_dir / MANIFEST_NAME
        if manifest_path.exists():
            raise TraceError("TRACE_ALREADY_SEALED", str(manifest_path))
        self._trace_dir.mkdir(parents=True, exist_ok=True)
        body = {
            "schema_version": 1,
            "attempt_id": self._attempt_id,
            "trace_complete": self._trace_complete,
            "event_count": self._event_count,
            "total_bytes": self._total_bytes,
            "artifact_count": self._artifact_count,
            "segments": [
                {
                    "name": item.name,
                    "event_count": item.event_count,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in self._segments
            ],
            "sealed_at": _utc_now(),
        }
        tmp = manifest_path.with_name(f"{MANIFEST_NAME}.tmp-{uuid.uuid4()}")
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(body, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, manifest_path)
        except OSError as exc:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            self._trace_complete = False
            raise TraceError("TRACE_WRITE_FAILED", str(exc)) from exc
        self._manifest = TraceManifest(
            attempt_id=self._attempt_id,
            manifest_path=manifest_path,
            trace_complete=self._trace_complete,
            event_count=self._event_count,
            total_bytes=self._total_bytes,
            segments=tuple(self._segments),
            artifact_count=self._artifact_count,
        )
        return self._manifest

    # ------------------------------------------------------------- 内部实现

    def _write_artifact(self, artifact_id: uuid.UUID, text: str) -> None:
        self._ensure_trace_dir()
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = self._artifacts_dir / f"{artifact_id}.txt"
        data = text.encode("utf-8")
        try:
            with path.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            self._trace_complete = False
            raise TraceError("TRACE_WRITE_FAILED", str(exc)) from exc
        self._artifact_count += 1
        self._total_bytes += len(data)

    def _write_line(self, *, raw: bytes) -> bool:
        """写一行到活动段；跨 1 MiB 时轮转下一段。"""
        self._ensure_trace_dir()
        if self._handle is None:
            self._open_next_segment()
        assert self._handle is not None and self._active_path is not None
        if self._active_size + len(raw) > MAX_SEGMENT_BYTES:
            self._close_active()
            self._open_next_segment()
        try:
            self._handle.write(raw)
            self._handle.flush()
        except OSError as exc:
            self._trace_complete = False
            raise TraceError("TRACE_WRITE_FAILED", str(exc)) from exc
        self._active_size += len(raw)
        name = self._active_path.name
        self._segment_counts[name] = self._segment_counts.get(name, 0) + 1
        self._total_bytes += len(raw)
        return True

    def _ensure_trace_dir(self) -> None:
        try:
            self._trace_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._trace_complete = False
            raise TraceError("TRACE_WRITE_FAILED", str(exc)) from exc

    def _open_next_segment(self) -> None:
        self._segment_index += 1
        path = self._trace_dir / _segment_name(self._segment_index)
        try:
            self._handle = path.open("ab")
        except OSError as exc:
            self._handle = None
            self._trace_complete = False
            raise TraceError("TRACE_WRITE_FAILED", str(exc)) from exc
        self._active_path = path
        self._active_size = 0

    def _close_active(self) -> None:
        if self._handle is not None:
            try:
                self._handle.flush()
                os.fsync(self._handle.fileno())
            except OSError as exc:
                self._trace_complete = False
                raise TraceError("TRACE_WRITE_FAILED", str(exc)) from exc
            finally:
                self._handle.close()
            self._handle = None
        if self._active_path is not None:
            data = self._active_path.read_bytes()
            self._segments.append(
                SegmentInfo(
                    name=self._active_path.name,
                    event_count=self._segment_counts.get(self._active_path.name, 0),
                    size_bytes=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
            )
            self._active_path = None
            self._active_size = 0

    def _flush_buffered_incomplete_events(self) -> None:
        pending = list(self._incomplete_events)
        self._incomplete_events.clear()
        for event in pending:
            raw = _dump_line(event).encode("utf-8") + b"\n"
            self._ensure_trace_dir()
            if self._handle is None:
                self._open_next_segment()
            assert self._handle is not None and self._active_path is not None
            if self._active_size + len(raw) > MAX_SEGMENT_BYTES:
                self._close_active()
                self._open_next_segment()
            try:
                self._handle.write(raw)
                self._handle.flush()
            except OSError as exc:
                self._trace_complete = False
                raise TraceError("TRACE_WRITE_FAILED", str(exc)) from exc
            self._active_size += len(raw)
            name = self._active_path.name
            self._segment_counts[name] = self._segment_counts.get(name, 0) + 1
            self._total_bytes += len(raw)
            self._event_count += 1
            self._event_ids.add(str(event.event_id))
            self._next_seq += 1


def _segment_paths(trace_dir: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in trace_dir.glob(f"{SEGMENT_PREFIX}*{SEGMENT_SUFFIX}")
            if path.is_file()
        ),
        key=lambda path: path.name,
    )


def read_trace(trace_dir: Path) -> TraceRead:
    """按完整行读取分段 JSONL。

    坏行/半行只忽略不报错，但必须让 ``trace_complete`` 为 false —— 契约 §5 规定证据不足
    时不得判通过。未密封（无 manifest）时只反映日志自身的完整性。
    """
    directory = Path(trace_dir)
    events: list[TraceEvent] = []
    complete = True
    seen: set[str] = set()
    for path in _segment_paths(directory):
        text = path.read_text(encoding="utf-8", errors="replace")
        if text and not text.endswith("\n"):
            # 活动段被崩溃切断：最后一段没有换行，属于半行证据。
            complete = False
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                event = TraceEvent.model_validate(json.loads(line))
            except (ValueError, TypeError):
                complete = False
                continue
            event_id = str(event.event_id)
            if event_id in seen:
                continue
            seen.add(event_id)
            if event.type == _INCOMPLETE_MARKER_TYPE:
                complete = False
            events.append(event)
    manifest_path = directory / MANIFEST_NAME
    sealed = False
    if manifest_path.exists():
        sealed = True
        try:
            body = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ValueError, TypeError):
            return TraceRead(events=tuple(events), trace_complete=False, sealed=False)
        complete = complete and bool(body.get("trace_complete", False))
    return TraceRead(events=tuple(events), trace_complete=complete, sealed=sealed)
