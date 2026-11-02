"""情节通道：把查询投影成确定性检索词 → 关联 Episode。

情节的**可检索投影**是 ``summary`` / ``goal`` / ``entities`` / ``tools_used``
（SQL 侧见 ``repository.search_episodes``）。``action_nodes`` 是**按需证据**：
既不进投影做匹配，也不随候选整包注入——候选内容只用 ``summary``（spec §6.1）。

查询侧只做确定性投影：路径 / 文件名 / 英文标识符 / 中文片段（原样整段，**不**
硬切 2~4 字碎片、不做 CJK n-gram），最后回退到查询准备给出的 ``keywords``。
命中只说明「检索词是投影里的子串」，不自造相关分；跨通道排序统一交给
``reranker`` 的 RRF 名次融合。
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from nanobot.memory.retrieval.candidate import (
    RetrievalCandidate,
    hit_from_episode,
)

_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+|/[^\s\"']+\.\w{1,5}\b")
_EXT_RE = re.compile(r"\b[\w一-鿿\-]+\.\w{1,5}\b")
# 英文标识符（项目名 / 工具名 / snake_case / 点分名）：整词保留，不拆驼峰
_IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{1,40}")
# 中文片段：整段保留（含标点即断开），作为子串去匹配 summary / goal / entities
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")

# 单次检索的投影词上限：每个词一次 SQL（5 列 LIKE）。episodes 按会话增长、
# 行数远小于 memories，8 次仍可接受；再放大就该换 entity 侧表或 FTS。
_MAX_QUERY_TERMS = 8
# 每个投影词最多取几条 episode：同词命中多时取最近几条（SQL 已按时间倒序）
_EPISODES_PER_TERM = 3


def _extract_query_entities(query: str) -> list[str]:
    """从 query 抽出路径/文件名类实体（去重，保持出现顺序）。"""
    entities: list[str] = []
    for pat in (_PATH_RE, _EXT_RE):
        for m in pat.finditer(query):
            tok = m.group(0)
            if tok not in entities:
                entities.append(tok)
    return entities


def _projection_query_terms(query: str, keywords: Sequence[str] | None) -> list[str]:
    """按情节投影构造确定性检索词：由具体到宽泛，去冗余后截断。

    顺序即精度顺序：路径/文件名 → 英文标识符 → 中文片段 → 查询准备给的
    keywords。被已有更具体词覆盖的碎片直接丢弃（``storage`` 在 ``storage.py``
    之后不再占位），否则宽词会把配额吃光、只多带回噪声。

    中文**不切分**：``记忆系统`` 这类片段原样作为子串匹配，跨词的 2~4 字碎片
    （``帮我重构``）在没有分词依据时纯属噪声，spec §6.2 明确移除该策略。
    """
    terms: list[str] = []

    def _add(token: str) -> None:
        token = token.strip()
        if not token or token in terms:
            return
        if any(token in existing for existing in terms):
            return
        terms.append(token)

    for token in _extract_query_entities(query):
        _add(token)
    for match in _IDENT_RE.finditer(query):
        _add(match.group(0))
    for match in _CJK_RUN_RE.finditer(query):
        _add(match.group(0))
    for word in keywords or ():
        _add(str(word))
    return terms[:_MAX_QUERY_TERMS]


def search_episodes(
    store,  # 提供 search_episodes(entity=..., limit=..., workspace_ids=...)
    *,
    query: str,
    keywords: list[str] | None = None,
    limit: int,
    workspace_ids: Sequence[str] | None = None,
) -> list[RetrievalCandidate]:
    """按投影检索词逐条召回 Episode；同一 episode 只保留首次命中。"""
    candidates: list[RetrievalCandidate] = []
    seen: set[str] = set()
    for term in _projection_query_terms(query, keywords):
        eps = store.search_episodes(
            entity=term, limit=_EPISODES_PER_TERM,
            **({"workspace_ids": workspace_ids} if workspace_ids is not None else {}),
        )
        for ep in eps:
            episode_id = str(getattr(ep, "id", "") or "")
            if not episode_id or episode_id in seen:
                continue
            seen.add(episode_id)
            candidates.append(
                RetrievalCandidate(
                    hit=hit_from_episode(ep),
                    source_channel="episodes",
                    raw=ep,
                )
            )
    return candidates[:limit]
