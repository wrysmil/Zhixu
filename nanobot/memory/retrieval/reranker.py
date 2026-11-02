"""综合重排：score = 0.4×Rel + 0.2×Rec + 0.2×Imp + 0.2×AccessFreq
   + 焦点增强（×1.0~1.35）
   + 动作词惩罚（fact 且起始动作词 × 0.3）
   + 冷启动豁免（recency ≥ 0.99）
   + 最小阈值（0.35）。"""
from __future__ import annotations

import math
import re
from dataclasses import replace as _replace
from datetime import datetime

from nanobot.memory.retrieval.candidate import RetrievalCandidate

_W_RELEVANCE = 0.40
_W_RECENCY = 0.20
_W_IMPORTANCE = 0.20
_W_ACCESS = 0.20

_FOCUS_BOOST_LO = 1.0
_FOCUS_BOOST_HI = 1.35
_ACTION_PENALTY = 0.30
_COLD_START_RECENCY = 0.99
_MIN_COMPOSITE = 0.35

_CJK_PREFIXES = (
    "打开", "启动", "运行", "执行", "调用", "安装", "部署", "下载",
    "上传", "删除", "修改", "更新", "重启", "关闭", "退出",
)
_ASCII_PREFIXES = (
    "open", "run", "execute", "install", "deploy", "download", "delete",
)
_ACTION_PREFIXES = _CJK_PREFIXES + _ASCII_PREFIXES

_ACTION_PATTERN = re.compile(
    r"^\s*(?:"
    + "|".join(re.escape(p) for p in _CJK_PREFIXES)
    + r"|"
    + "|".join(rf"{re.escape(p)}\b" for p in _ASCII_PREFIXES)
    + r")",
    re.IGNORECASE,
)


def _compute_recency(updated_at: datetime, *, now: datetime | None = None) -> float:
    """指数衰减的新鲜度分。

    ``now`` 由调用方注入（评测时是 :class:`~nanobot.memory.clock.MemoryClock`
    的虚拟日期）；缺省时用真实时间，行为与改造前一致。naive / aware 混用会被
    归一到同一时区口径，避免比较时抛 ``TypeError``。
    """
    if now is None:
        base = datetime.now(updated_at.tzinfo) if updated_at.tzinfo else datetime.now()
    else:
        base = now
        if updated_at.tzinfo is None and base.tzinfo is not None:
            # naive 存的时间戳按本地时间解读（与 datetime.now() 一致）。
            base = base.astimezone().replace(tzinfo=None)
        elif updated_at.tzinfo is not None and base.tzinfo is None:
            base = base.replace(tzinfo=updated_at.tzinfo)
    days = (base - updated_at).total_seconds() / 86400.0
    return math.exp(-0.1 * max(0.0, days))


class Reranker:
    def __init__(self, *, fact_type_names: tuple[str, ...] = ("fact", "FACT")):
        self._fact_types = set(fact_type_names)

    def rerank(
        self,
        candidates: list[RetrievalCandidate],
        *,
        query: str,
        persona,
        focus_terms: list[str],
    ) -> list[RetrievalCandidate]:
        scored: list[RetrievalCandidate] = []
        for c in candidates:
            # 先计算基础得分
            base = (
                _W_RELEVANCE * c.relevance
                + _W_RECENCY * c.recency_score
                + _W_IMPORTANCE * c.importance_score
                + _W_ACCESS * c.access_frequency_score
            )
            # 焦点增强
            if focus_terms and any(ft.lower() in c.content.lower() for ft in focus_terms):
                hits = sum(1 for ft in focus_terms if ft.lower() in c.content.lower())
                boost = min(_FOCUS_BOOST_HI, _FOCUS_BOOST_LO + 0.1 * min(hits, 4))
                base *= boost
            # 动作词惩罚（仅对 fact 类型）
            raw = getattr(c.raw, "type", None)
            type_name = getattr(raw, "value", raw) if raw is not None else None
            if type_name in self._fact_types and _ACTION_PATTERN.match(c.content):
                base *= _ACTION_PENALTY
            scored.append(_replace(c, composite_score=base))
        # 冷启动豁免 + 阈值过滤
        out: list[RetrievalCandidate] = []
        for c in scored:
            if c.composite_score >= _MIN_COMPOSITE or c.recency_score >= _COLD_START_RECENCY:
                out.append(c)
        out.sort(key=lambda x: x.composite_score, reverse=True)
        return out
