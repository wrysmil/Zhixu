"""Phase 2 防污染过滤器：任务产物 / AI 自答 / 精确哈希。

写入去重、纠正与冲突判定统一由 :mod:`nanobot.memory.writer` 的写入决策表负责
（D08）：本模块只保留内容合法性判断和哈希工具，不再用字符相似度丢弃候选。

Public API:
    is_task_artifact()
    is_ai_self_talk()
    compute_content_hash()
"""

from __future__ import annotations

import hashlib
import re

# ---------------------------------------------------------------------------
# 正则规则
# ---------------------------------------------------------------------------

# 任务指示器：用户正在请求 AI 执行操作，而非陈述事实
_TASK_INDICATORS: list[re.Pattern[str]] = [
    # 中文任务请求
    re.compile(r"帮我[做给查找]|帮我生成|帮我写|帮我创建|帮我搜索|帮我查找"),
    re.compile(r"请帮我|能不能帮我|可以帮我"),
    re.compile(r"帮我分析一下|帮我整理一下|帮我总结一下"),
    re.compile(r"帮我翻译|帮我解释|帮我推荐"),
    re.compile(r"帮我设计|帮我规划|帮我安排"),
    re.compile(r"帮我做|帮我执行|帮我完成"),
    # 中文任务关键词
    re.compile(r"搜索[^\s]{1,20}并|搜索[^\s]{1,20}后|搜索[^\s]{1,20}的"),
    re.compile(r"生成[^\s]{1,30}报告|生成[^\s]{1,30}列表|生成[^\s]{1,30}代码"),
    re.compile(r"写[^\s]{1,30}代码|写[^\s]{1,30}脚本|写[^\s]{1,30}文档"),
    re.compile(r"查找[^\s]{1,30}文件|查找[^\s]{1,30}信息"),
    # 英文常见任务请求
    re.compile(r"\b(search|find|look up|generate|create|write|build|make|do)\b.*\b(for me|for me,|please)\b", re.IGNORECASE),
    re.compile(r"\bcan you\b.*\b(search|find|generate|create|write)\b", re.IGNORECASE),
    re.compile(r"\bplease (search|find|generate|create|write|look up)\b", re.IGNORECASE),
    re.compile(r"\bi want you to\b", re.IGNORECASE),
]

# AI 自答指示器：内容来自 AI 自身视角，而非用户输入
_AI_SELF_TALK: list[re.Pattern[str]] = [
    # 中文 AI 自我引用
    re.compile(r"^作为AI|^作为一个人工智能|^作为语言模型"),
    re.compile(r"^我建议|^我推荐|^我认为|^我分析"),
    re.compile(r"^根据我的|^基于我的|^在我的分析中"),
    re.compile(r"^AI(语言模型|助手|系统)|^(大型)?语言模型"),
    re.compile(r"^我的建议是|^以下是|^下面"),
    re.compile(r"^根据搜索结果|^搜索显示|^查询结果显示"),
    # 英文 AI 自我引用
    re.compile(r"^as an? (ai|language model|assistant)", re.IGNORECASE),
    re.compile(r"^i (suggest|recommend|analyze|think|believe)\b", re.IGNORECASE),
    re.compile(r"^based on (my|the) (analysis|knowledge|understanding)", re.IGNORECASE),
    re.compile(r"^here(?:'s| is) (my|the) (suggestion|recommendation|advice)", re.IGNORECASE),
    re.compile(r"^according to (my|the) (analysis|knowledge)", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# 内容过滤器
# ---------------------------------------------------------------------------


def is_task_artifact(content: str) -> bool:
    """判断内容是否为任务产物（用户请求 AI 执行操作），任务产物不应该进入记忆中。

    任务产物是指用户在请求 AI 完成某项操作，而非陈述一个事实或偏好。
    这类内容不应存入记忆系统，因为它们是过程性而非陈述性的。

    Args:
        content: 待检测的文本内容。

    Returns:
        True 如果内容是任务请求/产物，False 如果是普通陈述。
    """
    if not content or not content.strip():
        return False

    stripped = content.strip()

    for pattern in _TASK_INDICATORS:
        if pattern.search(stripped):
            return True

    return False


def is_ai_self_talk(content: str) -> bool:
    """判断内容是否为 AI 自答（AI 视角的回应，而非用户输入）。

    AI 自答是指 AI 以第一人称生成的回应性内容，这类内容：
    1. 混淆了记忆的来源归属
    2. 可能是中间推理过程，不应固化为记忆
    3. 会造成记忆系统中的"镜像污染"

    Args:
        content: 待检测的文本内容。

    Returns:
        True 如果内容是 AI 自答，False 如果是用户输入。
    """
    if not content or not content.strip():
        return False

    stripped = content.strip()

    for pattern in _AI_SELF_TALK:
        if pattern.search(stripped):
            return True

    return False


# ---------------------------------------------------------------------------
# 哈希去重
# ---------------------------------------------------------------------------


def compute_content_hash(content: str, subject: str, predicate: str) -> str:
    """计算内容的 SHA-1 哈希值（用于精确去重）。

    哈希因子包含：
    - content: 记忆主体内容
    - subject: 主语（避免"用户喜欢 X"和"用户不喜欢 X"被错误去重）
    - predicate: 谓词（进一步区分语义）

    Args:
        content: 记忆内容。
        subject: 主语。
        predicate: 谓词。

    Returns:
        SHA-1 hex digest of ``content|subject|predicate`` (40 chars).
    """
    normalized = f"{content.strip()}|{subject.strip()}|{predicate.strip()}"
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def content_hash_legacy(content: str) -> str:
    """SHA-1 hex digest of stripped content.

    Used by ``lifecycle.refresh_memory_md`` for content-level deduplication
    when deriving MEMORY.md. Differs from ``compute_content_hash`` in that it
    only hashes the stripped content string (no subject/predicate), matching
    the legacy dedup strategy from the original lifecycle implementation.
    """
    return hashlib.sha1(content.strip().encode("utf-8")).hexdigest()

