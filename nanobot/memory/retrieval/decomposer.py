# nanobot/memory/retrieval/decomposer.py
"""查询准备：只做确定性的输入整理，不做模型改写。

原句本身即第一条检索输入（下游 ``repository._search_terms`` 按原句优先去重），
本模块只补「原句里已经写明的具体实体」：路径、文件名、英文标识符；只有当原句
用「那个项目 / 上次那件事」这类不自足指代、且最近对话明确点名过项目时，才补一个
项目名。中文不分词：硬切 2~4 字会产出跨词碎片（「脚本怎么」），对 FTS5/LIKE 与
episode 实体匹配都是噪声。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 绝对路径（Windows 盘符 / POSIX），原样保留；POSIX 分支排除 URL 的「//host/path」
_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"'，。；、]+|(?<![A-Za-z0-9_:/])(?:/[\w.\-]+){2,}/?")
# 带扩展名的文件名
_FILENAME_RE = re.compile(r"[\w\-]+\.\w{1,5}\b")
# 英文标识符（项目名/工具名/snake_case/点分名）：整词保留，不拆驼峰
_IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{1,40}")
# 不自足指代：自身不点名检索对象
_ANAPHORA_MARKERS = (
    "那个项目", "这个项目", "该项目", "那项目",
    "上次那个项目", "上次的项目",
    "那件事", "这件事", "上次那件事", "上次的事", "那个东西",
)
# 最近对话里「明确点名项目」：英文名靠「X 项目」的词边界，中文名靠「项目叫 X」+ 断词
_PROJECT_NAME_STOPWORDS = frozenset({
    "这个", "那个", "该", "上次", "这次", "每个", "一个", "什么", "哪个", "整个",
})
_PROJECT_NAMED_RE = re.compile(r"([A-Za-z][\w.\-]{1,40})\s*(?:项目|工程)")
_PROJECT_CALLED_RE = re.compile(
    r"(?:项目|工程)\s*(?:叫|是|名为|：|:)\s*[「“\"']?"
    r"([A-Za-z][\w.\-]{1,40}|[一-鿿]{2,4})(?=[的，。；、\s]|$)"
)
# 关键词有界：原句 + 至多 7 个实体，避免超长 query 撑出大词表
_MAX_KEYWORDS = 8


@dataclass
class DecompositionResult:
    keywords: list[str]
    # 兼容字段：唯一消费者 attachments 通道已删除（D04），恒为 "general"
    intent: str = "general"
    # 兼容字段：本模块已无模型路径，恒为 False
    used_llm: bool = False


class QueryDecomposer:
    """确定性查询准备：原句 + 路径/文件名/英文标识符 + 必要的指代补全。"""

    def __init__(self, brain: object | None = None) -> None:
        # ``brain`` 只为保持 ``RetrievalEngine(brain=...)`` 的既有构造签名，
        # 本类不再读取它（也不缓存：拆解是纯函数，缓存没有收益）。
        self._brain = brain

    async def decompose(self, query: str, recent: list | None) -> DecompositionResult:
        """``async`` 仅为兼容既有调用契约；本方法无 IO。"""
        text = (query or "").strip()
        if not text:
            return DecompositionResult(keywords=[])

        keywords = [text]
        for token in _PATH_RE.findall(text) + _FILENAME_RE.findall(text) + _IDENT_RE.findall(text):
            if token not in keywords:
                keywords.append(token)

        if any(m in text for m in _ANAPHORA_MARKERS) and not _IDENT_RE.search(text):
            project = _project_from_recent(recent or [])
            if project and project not in keywords:
                keywords.append(project)

        return DecompositionResult(keywords=keywords[:_MAX_KEYWORDS])


def _project_from_recent(recent: list) -> str | None:
    """从最近对话里找**明确点名**的项目，找不到就返回 None。

    只回填一个项目名，不把历史原样拼进检索输入；无法确定对象时保持不确定。
    """
    for msg in reversed(recent):
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if not isinstance(content, str) or not content.strip():
            continue
        for pattern in (_PROJECT_CALLED_RE, _PROJECT_NAMED_RE):
            for match in pattern.finditer(content):
                name = match.group(1).strip()
                if name and name not in _PROJECT_NAME_STOPWORDS:
                    return name
        match = _PATH_RE.search(content) or _FILENAME_RE.search(content)
        if match:
            return match.group(0)
    return None
