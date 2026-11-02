"""语义通道：经 store_adapter 语义召回（FTS/LIKE ∪ 向量，RRF 融合）。"""
from __future__ import annotations

from collections.abc import Sequence

from nanobot.memory.retrieval.candidate import (
    RetrievalCandidate,
    hit_from_memory,
)


def search_semantic(
    store,
    *,
    query: str,
    keywords: list[str] | None = None,
    limit: int,
    workspace_ids: Sequence[str] | None = None,
) -> list[RetrievalCandidate]:
    """从 store 取语义召回结果并包装为 ``RetrievalCandidate``。

    store 需提供 ``search_semantic_scored(query, *, keywords, limit, workspace_ids)
    -> list[(Memory, rrf_score)]``，真实实现在 ``store_adapter.py``（无向量环境
    自动只走 FTS5/LIKE 降级路径）。候选的相关性完全由召回结果的名次决定，
    本通道不再计算综合分。
    """
    scored = store.search_semantic_scored(
        query, keywords=keywords, limit=limit * 3,
        **({"workspace_ids": workspace_ids} if workspace_ids is not None else {}),
    )
    return [
        RetrievalCandidate(
            hit=hit_from_memory(mem),
            source_channel="semantic",
            raw=mem,
        )
        for mem, _raw_score in scored[:limit]
    ]
