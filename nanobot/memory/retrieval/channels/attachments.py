"""附件通道：仅在 intent=search_file 或 query 含媒体词时触发。"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from nanobot.memory.retrieval.candidate import RetrievalCandidate

_MEDIA_KEYWORDS = (
    "图片",
    "照片",
    "视频",
    "文件",
    "音频",
    "pdf",
    "PDF",
    "image",
    "photo",
    "video",
    "audio",
    "file",
)
_ATTACHMENT_RELEVANCE = 0.5
_DEFAULT_IMPORTANCE = 0.5


def search_attachments(
    store,  # 提供 search_attachments(term, *, intent, limit)
    *,
    raw_query: str,
    keywords: list[str],
    intent: str,
    limit: int,
    compute_recency: Callable[[datetime], float],
    now: datetime | None = None,
) -> list[RetrievalCandidate]:
    """媒体闸门：intent=search_file 或 query 命中媒体词才召回附件。

    逐 term 向 store 索取候选，按 id 去重后包装为 RetrievalCandidate。
    term 匹配语义由 store 实现方（T-12 repository）负责。

    ``now`` 是「行没有 updated_at 时」用的默认时间戳（评测时注入虚拟日期）；
    缺省时用真实时间，行为与改造前一致。
    """
    has_media_hint = intent == "search_file" or any(
        kw in raw_query for kw in _MEDIA_KEYWORDS
    )
    if not has_media_hint:
        return []

    default_updated_at = now if now is not None else datetime.now()
    search_terms = [raw_query] + list(keywords)
    candidates: list[RetrievalCandidate] = []
    seen: set[str] = set()
    for term in search_terms:
        for item in store.search_attachments(term, intent=intent, limit=limit):
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            candidates.append(
                RetrievalCandidate(
                    memory_id=item["id"],
                    content=item["content"],
                    source_channel="attachments",
                    relevance=_ATTACHMENT_RELEVANCE,
                    recency_score=compute_recency(
                        item.get("updated_at", default_updated_at)
                    ),
                    importance_score=float(
                        item.get("importance_score", _DEFAULT_IMPORTANCE)
                    ),
                    access_frequency_score=0.0,
                    raw=item,
                )
            )
    return candidates[:limit]
