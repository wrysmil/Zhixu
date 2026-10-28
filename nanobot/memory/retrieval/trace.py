"""记忆检索链路日志（切面模块）。

**为什么单独成文件**：把「检索过程可观测性」这个横切关注点从业务代码里摘出来。
``engine.py`` / ``store_adapter.py`` 只保留**一行** ``trace.recall(...)`` 调用，
所有格式化、中文文案、截断策略都在这里，因此：

- 调试时想加/改内容 → 只动本文件；
- 不想看了 → 把 :data:`ENABLED` 改成 ``False``（零成本），或直接注释掉调用点那一行，
  或整文件删除 + 删掉各调用点。

**注意**：通道异常（``AttributeError: ... has no attribute 'search_semantic_scored'``
这类装配级故障）由调用方用 ``logger.warning`` 独立记录，**不受** :data:`ENABLED`
影响——那是 RCA 要求的常驻信号，不是调试噪音（2026-09-15 根因 1）。
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from loguru import logger

from nanobot.memory.retrieval.candidate import RetrievalCandidate

#: 总开关。改 False 即可全局静默本模块的所有输出。
ENABLED = True

#: 单条记忆内容在日志里的展示上限（超出截断，避免刷屏）
CONTENT_CHARS = 60

#: 每路/每段最多展示多少条明细
MAX_ITEMS = 8

_CHANNEL_CN: dict[str, str] = {
    "semantic": "语义通道",
    "episodes": "情节通道",
    "recent": "近期通道",
    "attachments": "附件通道",
}


def _one_line(text: Any, *, limit: int = CONTENT_CHARS) -> str:
    """把记忆内容压成单行（折叠换行 + 截断），供日志展示。"""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _fmt_item(c: RetrievalCandidate) -> str:
    """单条候选的一行摘要：id + 各路分数 + 内容。"""
    return (
        f"[{c.memory_id[:8]}] 相关={c.relevance:.3f} 新鲜={c.recency_score:.2f} "
        f"重要={c.importance_score:.2f} 频次={c.access_frequency_score:.2f} "
        f"| {_one_line(c.content)}"
    )


def _emit(lines: list[str]) -> None:
    """统一出口：一个检索阶段打成一条多行日志，便于整段复制/折叠。"""
    if not ENABLED or not lines:
        return
    logger.info("\n".join(lines))


# ---- 各阶段入口（业务代码只调这些） ----------------------------------------


def start(
    *,
    raw_query: str,
    cleaned_query: str,
    recent_count: int,
    tokens: int,
    workspace_ids: Sequence[str] | None = None,
) -> None:
    """阶段零：一次检索的开始（原始 query → 清洗后 query）。"""
    if not ENABLED:
        return
    lines = [
        "【记忆检索·开始】",
        f"  入参 query  : {raw_query!r}",
        f"  清洗后 query: {cleaned_query!r}（调用方已清洗时两者相同）",
        f"  上下文消息数: {recent_count} 条",
        f"  token 预算  : {tokens}",
        f"  可见集合    : {_fmt_workspace_ids(workspace_ids)}",
    ]
    _emit(lines)


def _fmt_workspace_ids(workspace_ids: Sequence[str] | None) -> str:
    """可见集合 → 日志文本。``None`` = 管理面全量读（不过滤）。"""
    if workspace_ids is None:
        return "（未限定 = 全量读，不过滤工作区）"
    ids = [str(item) for item in workspace_ids if str(item)]
    if not ids:
        return "（空集 = 什么都看不到）"
    return ", ".join(ids)


def elapsed(*, stage: str, ms: float) -> None:
    """阶段耗时（ms）。"""
    if not ENABLED:
        return
    _emit([f"【记忆检索·耗时】{stage} {ms:.1f}ms"])


def skipped(*, query: str, reason: str) -> None:
    """检索被门禁跳过（无注入）。用于排障「为什么这轮没有记忆」。"""
    if not ENABLED:
        return
    _emit([f"【记忆检索·跳过】查询={query!r}", f"  原因: {reason}"])


def injection(*, block: str, ids: list[str]) -> None:
    """最终产物：真正拼进 system prompt 的 markdown 原文。

    这是排障时最该看的一段——上游所有分数/筛选都只是中间过程，
    **只有这里的内容会真正进入模型上下文**。
    """
    if not ENABLED:
        return
    if not block:
        _emit(["【记忆检索·注入块】空（无内容注入 system prompt）"])
        return
    lines = [
        "【记忆检索·注入块】以下 markdown 原样拼进 system prompt:",
        f"  注入条数: {len(ids)}  字符数: {len(block)}  估算 tokens: ~{len(block) // 2}",
        "  ---------- 注入内容开始 ----------",
    ]
    lines.extend(f"  {line}" for line in block.splitlines())
    lines.append("  ---------- 注入内容结束 ----------")
    _emit(lines)


def recall(
    *,
    query: str,
    keywords: list[str],
    channel_items: dict[str, list[RetrievalCandidate]],
    channel_errors: dict[str, str],
) -> None:
    """阶段一：四路召回明细（每路条数 + 每条内容）。"""
    if not ENABLED:
        return
    total = sum(len(v) for v in channel_items.values())
    lines = [f"【记忆检索·召回】查询={query!r}", f"  关键词: {keywords}"]
    for key, cn in _CHANNEL_CN.items():
        if key in channel_errors:
            lines.append(f"  · {cn}: 失败（见上方 WARNING）")
            continue
        items = channel_items.get(key, [])
        lines.append(f"  · {cn}: {len(items)} 条")
        for c in items[:MAX_ITEMS]:
            lines.append(f"      {_fmt_item(c)}")
        if len(items) > MAX_ITEMS:
            lines.append(f"      … 另有 {len(items) - MAX_ITEMS} 条省略")
    lines.append(f"  原始候选合计: {total} 条（含跨通道重复）")
    _emit(lines)


def dedupe(
    *,
    before: int,
    unique: list[RetrievalCandidate],
    dup_groups: dict[str, list[str]],
) -> None:
    """阶段二：按 memory_id 去重明细（哪些记忆被多路重复命中）。"""
    if not ENABLED:
        return
    lines = [f"【记忆检索·去重】{before} 条 → {len(unique)} 条"]
    if not dup_groups:
        lines.append("  无重复命中")
        _emit(lines)
        return
    merged = sum(len(v) - 1 for v in dup_groups.values())
    lines.append(f"  合并 {merged} 条重复（同一记忆被多路命中，取最高相关分）:")
    for mid, channels in list(dup_groups.items())[:MAX_ITEMS]:
        lines.append(f"      [{mid[:8]}] 命中于 {'+'.join(channels)}")
    if len(dup_groups) > MAX_ITEMS:
        lines.append(f"      … 另有 {len(dup_groups) - MAX_ITEMS} 条省略")
    _emit(lines)


def rank(
    *,
    unique: list[RetrievalCandidate],
    ranked: list[RetrievalCandidate],
    dropped: list[RetrievalCandidate],
    items: list[dict],
    truncated: list[RetrievalCandidate],
    limit: int,
    tokens: int,
) -> None:
    """阶段三+四：重排打分、阈值过滤、预算截断、最终注入。

    ``dropped`` 来自去重后的原始候选（未被 Reranker 写入 ``composite_score``），
    故只展示 ``相关`` 原始分，不展示综合分——综合分由 Reranker 内部计算，
    这里拿不到，硬凑等于复制一份打分公式，会与真实实现漂移。
    """
    if not ENABLED:
        return
    lines = [
        f"【记忆检索·重排】入排 {len(unique)} 条 → 通过阈值 {len(ranked)} 条"
        f"（综合=相关×0.4+新鲜×0.2+重要×0.2+频次×0.2 ≥ 0.35，冷启动豁免）",
    ]
    for i, c in enumerate(ranked[:MAX_ITEMS], 1):
        lines.append(f"      {i}. 综合={c.composite_score:.3f} {_fmt_item(c)}")
    if len(ranked) > MAX_ITEMS:
        lines.append(f"      … 另有 {len(ranked) - MAX_ITEMS} 条省略")

    if dropped:
        lines.append(f"  被阈值过滤 {len(dropped)} 条:")
        for c in dropped[:MAX_ITEMS]:
            lines.append(f"      [丢弃] 相关={c.relevance:.3f} | {_one_line(c.content)}")

    lines.append(
        f"【记忆检索·注入】{len(items)} 条进入 system prompt"
        f"（预算 {tokens} tokens → 上限 {limit} 条）"
    )
    for i, it in enumerate(items, 1):
        lines.append(
            f"      {i}. [{str(it.get('memory_id', ''))[:8]}] "
            f"分={it.get('score', 0.0)} {it.get('reason', '')} "
            f"| {_one_line(it.get('content', ''))}"
        )
    if truncated:
        lines.append(f"  被预算截断 {len(truncated)} 条:")
        for c in truncated[:MAX_ITEMS]:
            lines.append(f"      [截断] 综合={c.composite_score:.3f} | {_one_line(c.content)}")
    _emit(lines)


def hybrid(
    *,
    query: str,
    fts5_hits: list[tuple[Any, float]],
    vector_hits: list[tuple[Any, float]],
    merged: dict[str, float],
    out: list[tuple[Any, float]],
    content_by_id: dict[str, str],
    discarded_inactive: list[str],
    discarded_invisible: list[str],
    unfiltered_matches: dict[str, int] | None = None,
    limit: int,
) -> None:
    """语义路融合明细：FTS5 ∪ 向量（同 id 取最高分）→ 活性过滤 → 返回。

    ``discarded_inactive`` 与 ``discarded_invisible`` 分开统计：前者是行没了/
    作废/过期，后者是**命中了但不在可见集合内**——后者才是「工作区隔离挡掉了」。

    ``unfiltered_matches`` 只在可见集合内结果为空时给出（忽略可见性条件的
    文本命中数），用来回答「0 条」是没匹配上还是被隔离挡掉。
    """
    if not ENABLED:
        return
    fts5_ids = [mem.id for mem, _ in fts5_hits]
    vector_ids = [str(mid) for mid, _ in vector_hits]

    lines = [f"【混合检索·语义路】查询={query!r}（FTS5 ∪ 向量，同 id 取最高分）"]

    lines.append(f"  · FTS5(BM25) {len(fts5_hits)} 条")
    if not fts5_hits:
        lines.append("      （无命中：FTS5 用 unicode61 分词器，不切分中文词，"
                     "中文查询通常只能靠向量路）")
    for mem, score in fts5_hits[:MAX_ITEMS]:
        lines.append(f"      [{mem.id[:8]}] 分={score:.3f} | {_one_line(mem.content)}")
    if len(fts5_hits) > MAX_ITEMS:
        lines.append(f"      … 另有 {len(fts5_hits) - MAX_ITEMS} 条省略")

    lines.append(f"  · 向量(余弦) {len(vector_hits)} 条")
    for mid, score in vector_hits[:MAX_ITEMS]:
        content = content_by_id.get(str(mid), "")
        suffix = f" | {_one_line(content)}" if content else ""
        lines.append(f"      [{str(mid)[:8]}] 分={score:.3f}{suffix}")
    if len(vector_hits) > MAX_ITEMS:
        lines.append(f"      … 另有 {len(vector_hits) - MAX_ITEMS} 条省略")

    fts5_set, vector_set = set(fts5_ids), set(vector_ids)
    both = fts5_set & vector_set
    lines.append(
        f"  · 并集 {len(merged)} 条（两路都命中 {len(both)} / "
        f"仅 FTS5 {len(fts5_set - vector_set)} / 仅向量 {len(vector_set - fts5_set)}）"
    )

    if discarded_inactive:
        lines.append(f"  · 活性过滤丢弃 {len(discarded_inactive)} 条（已作废 / 已过期 / 行已删）:")
        for mid in discarded_inactive[:MAX_ITEMS]:
            lines.append(f"      [丢弃] [{str(mid)[:8]}]")
    if discarded_invisible:
        lines.append(
            f"  · 工作区可见性丢弃 {len(discarded_invisible)} 条"
            "（命中但不在可见集合内）:"
        )
        for mid in discarded_invisible[:MAX_ITEMS]:
            lines.append(f"      [丢弃] [{str(mid)[:8]}]")

    if unfiltered_matches is not None:
        fts5_total = int(unfiltered_matches.get("fts5", 0))
        like_total = int(unfiltered_matches.get("like", 0))
        if fts5_total or like_total:
            lines.append(
                f"  · 诊断: 文本命中 FTS5={fts5_total} / LIKE={like_total}"
                "（忽略可见集合）→ 命中但被工作区可见性挡掉"
            )
        else:
            lines.append(
                "  · 诊断: 忽略可见集合也 0 条文本命中 → 与工作区无关，是词没匹配上"
            )

    lines.append(f"  · 按分排序取前 {limit} → 返回 {len(out)} 条")
    for mem, score in out:
        lines.append(f"      [{mem.id[:8]}] 分={score:.3f} | {_one_line(mem.content)}")
    _emit(lines)
