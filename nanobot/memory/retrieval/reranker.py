"""名次融合与排序：只保留一个职责。

1. **RRF 名次融合**（Elasticsearch 默认 ``k=60``）合并各召回通道的名次；
   语义路内部 FTS/向量融合也用同一实现（见 ``store_adapter.py``）。
2. **相关性筛选**：条目必须有 id 与非空 content；没有有效条目 → 返回空。
   不做跨通道分数比较、不加最小分会。
3. **时效只做次级排序**：相关名次相同时，新记录在前。

已删除（spec §6.3 / 契约 §4）：多权重自造综合分、关键词重复 boost、冷启动
豁免、动作词补罚、访问次数强化、自评 usefulness 加权。RRF 分不是相关概率，
下游不得把它当阈值或展示为「高度相关」。
"""
from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any, TypeVar

from nanobot.memory.retrieval.candidate import RetrievalCandidate

_RRF_K = 60

_KeyT = TypeVar("_KeyT", bound=Hashable)


def rrf_scores(
    rankings: Sequence[Sequence[_KeyT]], *, k: int = _RRF_K
) -> dict[_KeyT, float]:
    """合并多路名次为 RRF 分：``Σ 1/(k + rank)``。

    同一路里重复出现的 key 只按首次名次计一次，避免重复命中把分数刷高。
    """
    scores: dict[_KeyT, float] = {}
    for ranking in rankings:
        seen: set[_KeyT] = set()
        for rank, key in enumerate(ranking, start=1):
            if key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return scores


class Reranker:
    """通道级 RRF 融合 + 相关性筛选 + 时效次级排序。"""

    def rerank(
        self, channels: dict[str, list[RetrievalCandidate]]
    ) -> list[RetrievalCandidate]:
        """``{通道名: 按名次排好的候选}`` → 融合、筛选、排序后的候选。"""
        first: dict[tuple[str, str], RetrievalCandidate] = {}
        rankings: list[list[tuple[str, str]]] = []
        for items in channels.values():
            ranking: list[tuple[str, str]] = []
            for candidate in items:
                if not _is_valid(candidate):
                    continue
                key = candidate.hit.key
                if key not in first:
                    first[key] = candidate
                    ranking.append(key)
                elif key not in ranking:
                    ranking.append(key)
            rankings.append(ranking)

        scores = rrf_scores(rankings)
        ordered = sorted(
            first,
            key=lambda key: (
                -scores[key],
                -_occurred_ts(first[key].hit.occurred_at),
                key,
            ),
        )
        return [
            replace(
                first[key],
                composite_score=scores[key],
                hit=replace(first[key].hit, rank=rank),
            )
            for rank, key in enumerate(ordered, start=1)
        ]


def _is_valid(candidate: RetrievalCandidate) -> bool:
    """相关性筛选的最小条件：有记录 id 且有可注入内容。"""
    return bool(candidate.hit.record_id) and bool(candidate.hit.content.strip())


def _occurred_ts(value: Any) -> float:
    """发生时间 → 排序用时间戳；缺失或无法解析视为最早。"""
    if not value:
        return float("-inf")
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return float("-inf")
