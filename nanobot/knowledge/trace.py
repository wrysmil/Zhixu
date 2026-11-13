"""检索链路 trace：记录每个关键步骤的耗时、指标与可视化详情。

只在调用方显式传入 ``SearchTrace`` 时采集；未传时检索路径每个埋点都走
``if trace is not None`` 短路，不构造详情对象。所有输出均为 JSON 安全的
有界结构（每节点最多 8 条明细、文本截断），供 WebUI 画布渲染节点与详情。
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

TRACE_MAX_ITEMS = 8
TRACE_TEXT_LIMIT = 240


class TraceStage:
    """一个链路节点的可变载荷。"""

    __slots__ = ("kind", "status", "metrics", "detail", "duration_ms")

    def __init__(self, kind: str, status: str = "ok") -> None:
        self.kind = kind
        self.status = status
        self.metrics: dict[str, Any] = {}
        self.detail: dict[str, Any] | None = None
        self.duration_ms = 0

    def set(
        self,
        *,
        status: str | None = None,
        metrics: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> TraceStage:
        if status:
            self.status = status
        if metrics:
            self.metrics.update(metrics)
        if detail is not None:
            self.detail = detail
        return self

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "status": self.status,
            "durationMs": int(self.duration_ms),
            "metrics": dict(self.metrics),
        }
        if self.detail is not None:
            payload["detail"] = self.detail
        return payload


class SearchTrace:
    """一次检索的链路采集器；阶段按执行顺序追加。"""

    def __init__(self, *, query: str, mode: str, limit: int) -> None:
        self.query = _clip(query)
        self.mode = mode
        self.limit = int(limit)
        self.stages: list[dict[str, Any]] = []
        self._started = time.perf_counter()

    @contextmanager
    def stage(self, kind: str, *, status: str = "ok") -> Iterator[TraceStage]:
        stage = TraceStage(kind, status)
        started = time.perf_counter()
        try:
            yield stage
        except BaseException:
            if stage.status == "ok":
                stage.status = "error"
            raise
        finally:
            stage.duration_ms = int((time.perf_counter() - started) * 1000)
            self.stages.append(stage.to_payload())

    def add(
        self,
        kind: str,
        *,
        status: str = "ok",
        metrics: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
        duration_ms: int = 0,
    ) -> None:
        stage = TraceStage(kind, status).set(metrics=metrics, detail=detail)
        stage.duration_ms = int(duration_ms)
        self.stages.append(stage.to_payload())

    def to_payload(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "mode": self.mode,
            "limit": self.limit,
            "totalMs": int((time.perf_counter() - self._started) * 1000),
            "stages": list(self.stages),
        }


class _NullStage:
    """未开启 trace 时的占位节点，``set`` 全部丢弃。"""

    __slots__ = ()

    def set(self, **_kwargs: Any) -> _NullStage:
        return self


NULL_STAGE = _NullStage()


def hit_rows(
    hits: Sequence[Any],
    *,
    limit: int = TRACE_MAX_ITEMS,
) -> list[dict[str, Any]]:
    """命中明细：只保留画布能展示的短字段，分数四舍五入到 4 位。"""
    rows: list[dict[str, Any]] = []
    for hit in list(hits)[:limit]:
        rows.append(
            {
                "chunkId": str(getattr(hit, "chunk_id", "")),
                "docId": str(getattr(hit, "doc_id", "")),
                "score": _score(getattr(hit, "score", 0.0)),
            }
        )
    return rows


def score_delta_rows(
    before: Sequence[Any],
    after: Sequence[Any],
    *,
    limit: int = TRACE_MAX_ITEMS,
) -> list[dict[str, Any]]:
    """精排前后分数对比，按精排后的顺序取前 N 条。"""
    before_scores = {
        str(getattr(hit, "chunk_id", "")): _score(getattr(hit, "score", 0.0))
        for hit in before
    }
    rows: list[dict[str, Any]] = []
    for hit in list(after)[:limit]:
        chunk_id = str(getattr(hit, "chunk_id", ""))
        previous = before_scores.get(chunk_id)
        current = _score(getattr(hit, "score", 0.0))
        rows.append(
            {
                "chunkId": chunk_id,
                "before": previous,
                "after": current,
                "delta": None if previous is None else round(current - previous, 4),
            }
        )
    return rows


def _score(value: Any) -> float:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return 0.0


def _clip(text: Any) -> str:
    value = str(text or "")
    if len(value) <= TRACE_TEXT_LIMIT:
        return value
    return value[:TRACE_TEXT_LIMIT] + "…"
