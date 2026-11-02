"""检索前置守卫：跳过空/控制词；反注入清洗。

符合 docs/记忆系统/记忆检索.md Layer 4 Active Retrieval 入口 Gate 要求。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 控制词白名单（不含 ? ！吗 呢 吧 等短提示词字符）
_CONTROL_ONLY = frozenset({
    "好", "嗯", "哦", "啊", "呃", "嘿", "哎",
    "可以", "行", "继续", "收到", "好的", "ok", "okay",
    "yes", "no", "yep", "nope", "stop", "halt",
    "thanks", "thank", "thx", "ty",
})

# 反注入正则黑名单（section header 段落用 re.split 方式精确处理）
_INJECTION_BLOCK_PATTERNS = (
    r"(?is)<vault-context>.*?</vault-context>",
    r"(?is)<memory>.*?</memory>",
    r"(?is)<long-term-memory>.*?</long-term-memory>",
)
# Section-header 段落专用：split + 保留尾部内容
_SECTION_SPLITTER = re.compile(r"(?im)^##\s*(?:相关记忆|核心记忆|长期记忆)\s*$")


@dataclass(frozen=True)
class PreparedQuery:
    """预处理结果容器。"""
    skip: bool
    reason: str
    cleaned_query: str


class MemoryQueryPreprocessor:
    """检索前置守卫：决定是否跳过 + 清洗历史 Prompt 注入。"""

    @classmethod
    def should_skip_retrieval(cls, query: str | None) -> tuple[bool, str]:
        """只拦「空文本」与「纯控制词」。

        不再按长度拒绝短查询（D10 / spec §6.2）：``memory_search`` 是模型
        明确发起的检索调用，是否值得检索由调用方决定；长度规则会把「知序」
        「健身」这类中文子串查询整类挡掉。
        """
        text = (query or "").strip()
        if not text:
            return True, "empty"
        if text.lower() in cls._CONTROL_ONLY:
            return True, "control_only"
        return False, ""

    @classmethod
    def clean_query(cls, query: str) -> str:
        # 1. 先处理自闭合标签块
        out = query
        for pat in _INJECTION_BLOCK_PATTERNS:
            out = re.sub(pat, "", out)
        # 2. 用 split 方式移除 section header 段落，保留 header 后的尾部内容
        parts = _SECTION_SPLITTER.split(out)
        # sections[0] 是 header 之前的内容；后续每个 part 前的 header 已被移除
        # 但每个 part 的开头可能含 header 行的尾部空白，我们strip每段
        out = "".join(p.strip() for p in parts).strip()
        return out

    @classmethod
    def prepare(cls, query: str) -> PreparedQuery:
        cleaned = cls.clean_query(query or "")
        skip, reason = cls.should_skip_retrieval(cleaned)
        return PreparedQuery(skip=skip, reason=reason, cleaned_query=cleaned)

    _CONTROL_ONLY = _CONTROL_ONLY  # 暴露供测试遍历
