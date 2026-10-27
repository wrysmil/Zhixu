"""情节通道：从 query 抽实体（路径/扩展名）→ 关联 Episode，固定分 0.6。"""
from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Callable

from nanobot.memory.retrieval.candidate import RetrievalCandidate

_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+|/[^\s\"']+\.\w{1,5}\b")
_EXT_RE = re.compile(r"\b[\w一-鿿\-]+\.\w{1,5}\b")
_EPISODE_BASE_SCORE = 0.6


def _extract_query_entities(query: str) -> list[str]:
    """从 query 抽出路径/文件名类实体（去重，保持出现顺序）。"""
    entities: list[str] = []
    for pat in (_PATH_RE, _EXT_RE):
        for m in pat.finditer(query):
            tok = m.group(0)
            if tok not in entities:
                entities.append(tok)
    return entities


def search_episodes(
    store,  # 提供 search_episodes(entity, limit)
    *,
    query: str,
    keywords: list[str] | None = None,
    limit: int,
    compute_recency: Callable[[datetime], float],
    workspace_ids: Sequence[str] | None = None,
) -> list[RetrievalCandidate]:
    """按 query 中的实体名关联 Episode，命中固定给 0.6 相关性。"""
    entities = _extract_query_entities(query)[:3]
    # 关键词兜底：keywords 由 QueryDecomposer 产出，普通中文对话也能命中 episode
    if keywords:
        for kw in keywords:
            if kw and kw not in entities:
                entities.append(kw)
    entities = entities[:3]
    candidates: list[RetrievalCandidate] = []
    for entity in entities:
        eps = store.search_episodes(
            entity=entity, limit=3,
            **({"workspace_ids": workspace_ids} if workspace_ids is not None else {}),
        )
        for ep in eps:
            candidates.append(
                RetrievalCandidate(
                    memory_id=ep.id,
                    content=getattr(ep, "summary", ""),
                    source_channel="episodes",
                    relevance=_EPISODE_BASE_SCORE,
                    recency_score=compute_recency(
                        getattr(ep, "updated_at", datetime.now())
                    ),
                    importance_score=0.5,
                    access_frequency_score=0.0,
                    raw=ep,
                )
            )
    return candidates[:limit]
