"""近期通道：近 3 天 + importance ≥ 0.6。recency < 0.3 跳过。命中关键词 0.5~0.7；未命中 0.2。"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Callable

from nanobot.memory.retrieval.candidate import RetrievalCandidate

_MIN_IMPORTANCE = 0.6
_RECENCY_FLOOR = 0.3
_RELEVANCE_HIT_LO, _RELEVANCE_HIT_HI = 0.5, 0.7
_RELEVANCE_MISS = 0.2


def search_recent(
    store,  # 提供 query_semantic(min_importance, since_days, limit)
    *,
    query: str,
    keywords: list[str],
    limit: int,
    compute_recency: Callable[[datetime], float],
    workspace_ids: Sequence[str] | None = None,
) -> list[RetrievalCandidate]:
    """召回近 3 天的高重要性记忆；recency 过低直接丢弃，命中关键词加权。"""
    memories = store.query_semantic(
        min_importance=_MIN_IMPORTANCE,
        since_days=3,
        limit=limit * 2,
        **({"workspace_ids": workspace_ids} if workspace_ids is not None else {}),
    )
    candidates: list[RetrievalCandidate] = []
    for mem in memories:
        recency = compute_recency(mem.updated_at)
        if recency < _RECENCY_FLOOR:
            continue
        hit = any(kw.lower() in mem.content.lower() for kw in keywords)
        relevance = (
            _RELEVANCE_HIT_HI
            if hit and keywords
            else (_RELEVANCE_HIT_LO if hit else _RELEVANCE_MISS)
        )
        candidates.append(
            RetrievalCandidate(
                memory_id=mem.id,
                content=mem.content,
                source_channel="recent",
                relevance=relevance,
                recency_score=recency,
                importance_score=float(mem.importance_score),
                access_frequency_score=0.0,
                raw=mem,
            )
        )
    return candidates[:limit]
