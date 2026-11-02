"""综合重排：score = 0.4×Rel + 0.2×Rec + 0.2×Imp + 0.2×AccessFreq
   + 焦点增强（×1.0~1.35）
   + 动作词惩罚（fact 且起始动作词、且缺少合法主语 × 0.3）
   + 冷启动豁免（recency ≥ 0.99）
   + 最小阈值（0.35）。"""
from __future__ import annotations

import math
import re
from dataclasses import replace as _replace
from datetime import datetime
from typing import Any

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

_ACTION_PATTERN = re.compile(
    r"^\s*(?:"
    + "|".join(re.escape(p) for p in _CJK_PREFIXES)
    + r"|"
    + "|".join(rf"{re.escape(p)}\b" for p in _ASCII_PREFIXES)
    + r")",
    re.IGNORECASE,
)

# 主语指向助手/第二人称：这类主语是"执行者"而不是"这件事的主体"，
# 说明内容仍是一句让人去做的话，不是可复用的事实。
_ASSISTANT_SUBJECT_CJK = ("你", "您", "助手", "助理", "智能体")
_ASSISTANT_SUBJECT_ASCII = ("ai", "agent", "assistant", "chatbot", "llm")
_ASSISTANT_SUBJECT_RE = re.compile(
    r"\b(?:" + "|".join(_ASSISTANT_SUBJECT_ASCII) + r")\b",
    re.IGNORECASE,
)


def _subject_is_assistant(subject: str) -> bool:
    """主语是否指向助手/第二人称（"你"、"助手"、"AI" 等）。"""
    text = subject.strip()
    if not text:
        return False
    if any(hint in text for hint in _ASSISTANT_SUBJECT_CJK):
        return True
    return bool(_ASSISTANT_SUBJECT_RE.search(text))


def _has_legit_subject(raw: Any) -> bool:
    """记忆是否带"合法主语"（非空、且不指向助手）。

    合法主语是"这件事的主体"（用户 / 项目 alpha-app / 部署流程）。抽取契约要求
    content 以主语开头，因此"以操作动词开头 + 没有合法主语"≈ 一条混进 fact 的
    操作指令；反过来，有合法主语的内容是陈述事实，不该被动作词惩罚误伤。
    """
    subject = str(getattr(raw, "subject", "") or "")
    return bool(subject.strip()) and not _subject_is_assistant(subject)


def _compute_recency(updated_at: datetime, *, now: datetime | None = None) -> float:
    base = now or datetime.now(updated_at.tzinfo) if updated_at.tzinfo else (now or datetime.now())
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
            # 动作词惩罚（仅对 fact 类型，且缺少合法主语）
            type_raw = getattr(c.raw, "type", None)
            type_name = getattr(type_raw, "value", type_raw) if type_raw is not None else None
            if (
                type_name in self._fact_types
                and _ACTION_PATTERN.match(c.content)
                and not _has_legit_subject(c.raw)
            ):
                base *= _ACTION_PENALTY
            scored.append(_replace(c, composite_score=base))
        # 冷启动豁免 + 阈值过滤
        out: list[RetrievalCandidate] = []
        for c in scored:
            if c.composite_score >= _MIN_COMPOSITE or c.recency_score >= _COLD_START_RECENCY:
                out.append(c)
        out.sort(key=lambda x: x.composite_score, reverse=True)
        return out
