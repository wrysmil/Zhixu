"""检索结果后处理：将 Reranker 排序后的候选格式化为可注入 system prompt 的 dict。

契约：
- 输入：``list[RetrievalCandidate]``（来自 Reranker，已写 ``composite_score``）
- 输出：``list[dict]``，每项含 ``content`` / ``type`` / ``score`` / ``reason`` /
  ``memory_id``（WU-B：供注入块暴露 ID，保证引用评分闭环能拿到记忆标识）
- 行为：按 ``composite_score`` 降序取前 ``limit`` 个；空输入或 ``limit=0`` → ``[]``；
  严格按分截断，不去重、不抛异常。
"""
from __future__ import annotations

from typing import Any

from nanobot.memory.retrieval.candidate import RetrievalCandidate

_HIGH_LABEL = "高度相关"
_MID_LABEL = "中等相关"
_LOW_LABEL = "弱相关"

_HIGH_THRESHOLD = 0.8
_MID_THRESHOLD = 0.5


def _extract_type_value(raw: Any) -> str:
    """从候选 ``raw``（Memory / Episode 等）抽取类型字符串。

    - ``raw is None`` → 空串
    - ``raw.type`` 缺失 → 空串
    - ``raw.type`` 为 ``MemoryType`` 枚举（带 ``.value``） → 返回 ``.value``
    - ``raw.type`` 为普通字符串（某些测试 stub） → 返回 ``str(t)``
    """
    if raw is None:
        return ""
    t = getattr(raw, "type", None)
    if t is None:
        return ""
    value = getattr(t, "value", None)
    if value is not None:
        return str(value)
    return str(t)


class RetrievalFormatter:
    """检索结果后处理：按 composite_score 降序截断 + 字段格式化。"""

    def __init__(self, limit: int = 10) -> None:
        if limit < 0:
            raise ValueError(f"limit must be >= 0, got {limit}")
        self._limit = limit

    @property
    def limit(self) -> int:
        return self._limit

    def format(self, candidates: list[RetrievalCandidate]) -> list[dict]:
        """按 ``composite_score`` 降序取前 ``limit`` 个，输出 dict 列表。

        - 空输入或 ``limit == 0`` → ``[]``
        - 严格按 score 截断，不去重、不抛异常
        - ``reason`` 由未取整的 score 通过 ``_score_label`` 派生，确保边界稳定
        """
        if not candidates or self._limit == 0:
            return []
        ordered = sorted(candidates, key=lambda c: c.composite_score, reverse=True)
        top = ordered[: self._limit]
        out: list[dict] = []
        for c in top:
            raw_score = float(c.composite_score)
            out.append({
                "content": c.content,
                "type": _extract_type_value(c.raw),
                "score": round(raw_score, 2),
                "reason": self._score_label(raw_score),
                "memory_id": c.memory_id,
                "entry_kind": c.hit.entry_kind,
                "entry_status": c.hit.entry_status,
            })
        return out

    @staticmethod
    def _score_label(score: float) -> str:
        """根据 score 返回中文相关性标签。

        - ``score >= 0.8`` → ``"高度相关"``
        - ``0.5 <= score < 0.8`` → ``"中等相关"``
        - ``score < 0.5`` → ``"弱相关"``
        """
        if score >= _HIGH_THRESHOLD:
            return _HIGH_LABEL
        if score >= _MID_THRESHOLD:
            return _MID_LABEL
        return _LOW_LABEL
