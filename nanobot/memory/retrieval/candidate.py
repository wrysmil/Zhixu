"""检索条目与召回候选的数据类。

``MemoryHit`` 是检索输出的**唯一**结构化条目（契约 §4）：kind / id / content /
证据来源 / 时间 / 作用域。下游不再从无类型 dict 里猜字段。

``RetrievalCandidate`` 是召回阶段的薄包装：条目之外只保留「来自哪一路召回」和
原始行对象（格式化层要用它取记忆 type）。排序分只有 ``composite_score`` 一个，
由 RRF 名次融合产生（见 ``reranker.py``），不是跨查询通用的相关概率。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

HitKind = Literal["memory", "episode"]
SourceChannel = Literal["semantic", "episodes", "time"]

_VALID_KINDS = frozenset({"memory", "episode"})
_VALID_CHANNELS = frozenset({"semantic", "episodes", "time"})


@dataclass(frozen=True)
class MemoryHit:
    """一条检索条目：事实或情节，同一形状，按 ``(kind, record_id)`` 定位。"""

    kind: HitKind
    record_id: str
    content: str
    workspace_id: str
    source_session: str | None = None
    source_refs: tuple[str, ...] = ()
    occurred_at: str | None = None
    rank: int = 0

    def __post_init__(self) -> None:
        if self.kind not in _VALID_KINDS:
            raise ValueError(
                f"kind must be one of {_VALID_KINDS}, got {self.kind!r}"
            )

    @property
    def key(self) -> tuple[str, str]:
        """去重键：同一裸 ID 落在不同 kind 上是两条不同记录。"""
        return (self.kind, self.record_id)


@dataclass(frozen=True)
class RetrievalCandidate:
    """召回候选：结构化条目 + 召回路 + 原始行（供格式化层读 type）。"""

    hit: MemoryHit
    source_channel: SourceChannel
    raw: Any = None
    composite_score: float = 0.0

    def __post_init__(self) -> None:
        if self.source_channel not in _VALID_CHANNELS:
            raise ValueError(
                f"source_channel must be one of {_VALID_CHANNELS}, "
                f"got {self.source_channel!r}"
            )

    @property
    def memory_id(self) -> str:
        """兼容既有渲染/日志：条目的记录 id（事实或情节）。"""
        return self.hit.record_id

    @property
    def content(self) -> str:
        return self.hit.content


def hit_from_memory(memory: Any) -> MemoryHit:
    """事实行 → ``MemoryHit``；来源会话取已持久化的 provenance。"""
    metadata = getattr(memory, "metadata", None)
    session_id: Any = None
    if isinstance(metadata, dict):
        provenance = cast(dict[str, Any], metadata).get("provenance")
        if isinstance(provenance, dict):
            session_id = cast(dict[str, Any], provenance).get("session_id")
    return MemoryHit(
        kind="memory",
        record_id=str(getattr(memory, "id", "") or ""),
        content=str(getattr(memory, "content", "") or ""),
        workspace_id=str(getattr(memory, "workspace_id", "") or "default"),
        source_session=str(session_id) if session_id else None,
        # 现有持久化没有消息级引用，明确为空，不伪造
        source_refs=(),
        occurred_at=_text_or_none(getattr(memory, "created_at", None)),
    )


def hit_from_episode(episode: Any) -> MemoryHit:
    """情节行 → ``MemoryHit``；情节的发生时间取结束时间。"""
    return MemoryHit(
        kind="episode",
        record_id=str(getattr(episode, "id", "") or ""),
        content=str(getattr(episode, "summary", "") or ""),
        workspace_id=str(getattr(episode, "workspace_id", "") or "default"),
        source_session=_text_or_none(getattr(episode, "session_id", None)),
        source_refs=(),
        occurred_at=_text_or_none(
            getattr(episode, "ended_at", None) or getattr(episode, "updated_at", None)
        ),
    )


def _text_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
