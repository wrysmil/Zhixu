"""记忆检索链路日志（切面模块）。

**为什么单独成文件**：把「检索过程可观测性」这个横切关注点从业务代码里摘出来。
``engine.py`` / ``store_adapter.py`` 只保留**一行** ``trace.*(...)`` 调用，
所有格式化、中文文案、截断策略都在这里，因此：

- 调试时想加/改内容 → 只动本文件；
- 开关与截断 → 由 ``agents.defaults.memoryLogging`` 控制（``obs`` 模块），
  不再依赖改代码；
- 文本之外同时产出结构化事件（``memory-events.jsonl``），供测评/脚本消费。

**注意**：通道异常（``AttributeError: ... has no attribute 'search_semantic_scored'``
这类装配级故障）由调用方用 ``logger.warning`` 独立记录，**不受**日志开关
影响——那是 RCA 要求的常驻信号，不是调试噪音（2026-09-15 根因 1）。
"""
from __future__ import annotations

from collections.abc import Sequence
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

from nanobot.memory import obs
from nanobot.memory.retrieval.candidate import RetrievalCandidate

#: 兼容旧调用：``trace.ENABLED = False`` 仍可全局静默本模块。
#: 新代码请用配置项 ``memoryLogging.enabled``。
ENABLED = True

#: 单条记忆内容在日志里的展示上限（兼容常量；实际以配置为准）
CONTENT_CHARS = 240

#: 每路/每段最多展示多少条明细（兼容常量；实际以配置为准）
MAX_ITEMS = 8

_CHANNEL_CN: dict[str, str] = {
    "semantic": "语义通道",
    "episodes": "情节通道",
}

#: 同一轮「检索 → 注入」共享的关联 id（8 位十六进制）。
_TRACE_ID: ContextVar[str] = ContextVar("memory_trace_id", default="")


def current_id() -> str:
    """当前检索的关联 id；不在检索轮次内时返回 ``-``。"""
    return _TRACE_ID.get() or "-"


def _one_line(text: Any, *, limit: int | None = None) -> str:
    """把记忆内容压成单行（折叠换行 + 截断），供日志展示。"""
    return obs.clip(text, limit=limit)


def _fmt_item(c: RetrievalCandidate) -> str:
    """单条候选的一行摘要：kind + id + 融合名次 + 内容。"""
    return (
        f"[{c.hit.kind}:{c.memory_id[:8]}] 名次={c.hit.rank} "
        f"融合={c.composite_score:.4f} | {_one_line(c.content)}"
    )


def _limit_items() -> int:
    return obs.max_items()


def _emit(kind: str, lines: list[str], **fields: Any) -> None:
    """统一出口：一个阶段打成一条多行文本 + 一条结构化事件。"""
    if not ENABLED or not lines:
        return
    obs.emit(kind, "\n".join(lines), **fields)


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
    trace_id = uuid4().hex[:8]
    _TRACE_ID.set(trace_id)
    if not ENABLED:
        return
    lines = [
        f"【记忆检索·开始】trace={trace_id}",
        f"  入参 query  : {raw_query!r}",
        f"  清洗后 query: {cleaned_query!r}（调用方已清洗时两者相同）",
        f"  上下文消息数: {recent_count} 条",
        f"  token 预算  : {tokens}",
        f"  可见集合    : {_fmt_workspace_ids(workspace_ids)}",
    ]
    obs.emit(
        "retrieval_started",
        "\n".join(lines),
        trace_id=trace_id,
        query=obs.clip(cleaned_query, limit=120),
        recent_count=recent_count,
        tokens=tokens,
        workspace_ids=list(workspace_ids) if workspace_ids is not None else None,
    )


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
    _emit(
        "retrieval_elapsed",
        [f"【记忆检索·耗时】{stage} {ms:.1f}ms"],
        stage=stage,
        ms=round(ms, 1),
    )


def skipped(*, query: str, reason: str) -> None:
    """检索被门禁跳过（无注入）。用于排障「为什么这轮没有记忆」。"""
    _emit(
        "retrieval_skipped",
        [f"【记忆检索·跳过】查询={query!r}", f"  原因: {reason}"],
        trace_id=current_id(),
        query=obs.clip(query, limit=120),
        reason=reason,
    )


def time_browse(*, query_window_days: int, items: list[RetrievalCandidate]) -> None:
    """显式纯时间查询：受限时间列表（不是相关性匹配）。"""
    lines = [
        f"【记忆检索·时间浏览】窗口={query_window_days} 天 → {len(items)} 条"
        "（按保存时间倒序，未按相关性筛选）",
    ]
    for c in items[:_limit_items()]:
        lines.append(f"      - [{c.memory_id[:8]}] | {_one_line(c.content)}")
    if len(items) > _limit_items():
        lines.append(f"      … 另有 {len(items) - _limit_items()} 条省略")
    _emit(
        "retrieval_time_browse",
        lines,
        trace_id=current_id(),
        window_days=query_window_days,
        ids=[c.memory_id for c in items],
    )


def injection(*, block: str, ids: list[str]) -> None:
    """最终产物：真正拼进 system prompt 的 markdown 原文。

    这是排障时最该看的一段——上游所有分数/筛选都只是中间过程，
    **只有这里的内容会真正进入模型上下文**。
    """
    if not block:
        _emit(
            "injected",
            ["【记忆检索·注入块】空（无内容注入 system prompt）"],
            trace_id=current_id(),
            source="retrieval",
            ids=[],
            chars=0,
            est_tokens=0,
        )
        return
    lines = [
        "【记忆检索·注入块】以下 markdown 原样拼进 system prompt:",
        f"  注入条数: {len(ids)}  字符数: {len(block)}  估算 tokens: ~{len(block) // 2}",
        "  ---------- 注入内容开始 ----------",
    ]
    lines.extend(f"  {line}" for line in block.splitlines())
    lines.append("  ---------- 注入内容结束 ----------")
    _emit(
        "injected",
        lines,
        trace_id=current_id(),
        source="retrieval",
        ids=list(ids),
        chars=len(block),
        est_tokens=len(block) // 2,
    )


def recall(
    *,
    query: str,
    keywords: list[str],
    channel_items: dict[str, list[RetrievalCandidate]],
    channel_errors: dict[str, str],
) -> None:
    """阶段一：两路召回明细（每路条数 + 每条内容）。"""
    total = sum(len(v) for v in channel_items.values())
    lines = [f"【记忆检索·召回】查询={query!r}", f"  关键词: {keywords}"]
    for key, cn in _CHANNEL_CN.items():
        if key in channel_errors:
            lines.append(f"  · {cn}: 失败（见上方 WARNING）")
            continue
        items = channel_items.get(key, [])
        lines.append(f"  · {cn}: {len(items)} 条")
        for c in items[:_limit_items()]:
            lines.append(f"      {_fmt_item(c)}")
        if len(items) > _limit_items():
            lines.append(f"      … 另有 {len(items) - _limit_items()} 条省略")
    lines.append(f"  原始候选合计: {total} 条（含跨通道重复）")
    _emit(
        "recall_candidates",
        lines,
        trace_id=current_id(),
        query=obs.clip(query, limit=120),
        keywords=list(keywords),
        channels={key: len(value) for key, value in channel_items.items()},
        errors=dict(channel_errors),
        candidate_ids=[c.memory_id for items in channel_items.values() for c in items],
    )


def dedupe(
    *,
    before: int,
    unique: list[RetrievalCandidate],
    multi_channel: dict[tuple[str, str], list[str]],
) -> None:
    """阶段二：按 ``(kind, id)`` 融合去重明细（哪些条目被多路命中）。"""
    lines = [f"【记忆检索·去重】原始 {before} 条 → 融合后 {len(unique)} 条"]
    if not multi_channel:
        lines.append("  无跨通道重复命中")
        _emit(
            "retrieval_deduped",
            lines,
            trace_id=current_id(),
            before=before,
            unique=len(unique),
            multi_channel=0,
        )
        return
    lines.append("  同一 (kind, id) 被多路命中（RRF 累计名次）:")
    for (kind, record_id), channels in list(multi_channel.items())[:_limit_items()]:
        lines.append(f"      [{kind}:{record_id[:8]}] 命中于 {'+'.join(channels)}")
    if len(multi_channel) > _limit_items():
        lines.append(f"      … 另有 {len(multi_channel) - _limit_items()} 条省略")
    _emit(
        "retrieval_deduped",
        lines,
        trace_id=current_id(),
        before=before,
        unique=len(unique),
        multi_channel=len(multi_channel),
    )


def rank(
    *,
    ranked: list[RetrievalCandidate],
    items: list[dict[str, Any]],
    truncated: list[RetrievalCandidate],
    limit: int,
    tokens: int,
    mode: str = "relevance",
) -> None:
    """阶段三+四：融合排序、相关性筛选、预算截断、最终注入。

    RRF 分只表示名次融合，不是相关概率；这里如实打印名次与融合分，不换算成
    「高度相关」一类标签。
    """
    mode_cn = "时间浏览" if mode == "time" else "相关性检索"
    lines = [
        f"【记忆检索·重排】模式={mode_cn} 有效候选 {len(ranked)} 条"
        "（RRF 名次融合 → 相关性筛选 → 时效次级排序）"
    ]
    for i, c in enumerate(ranked[:_limit_items()], 1):
        lines.append(f"      {i}. {_fmt_item(c)}")
    if len(ranked) > _limit_items():
        lines.append(f"      … 另有 {len(ranked) - _limit_items()} 条省略")

    lines.append(
        f"【记忆检索·注入】{len(items)} 条进入 system prompt"
        f"（预算 {tokens} tokens → 上限 {limit} 条）"
    )
    for i, it in enumerate(items, 1):
        lines.append(
            f"      {i}. [{str(it.get('memory_id', ''))[:8]}] "
            f"{it.get('type', '')} | {_one_line(it.get('content', ''))}"
        )
    if truncated:
        lines.append(f"  被预算截断 {len(truncated)} 条:")
        for c in truncated[:_limit_items()]:
            lines.append(f"      [截断] {_fmt_item(c)}")
    _emit(
        "reranked",
        lines,
        trace_id=current_id(),
        mode=mode,
        ranked=len(ranked),
        injected=len(items),
        truncated=len(truncated),
        limit=limit,
        tokens=tokens,
        ranked_ids=[c.memory_id for c in ranked],
        injected_ids=[str(it.get("memory_id", "")) for it in items],
    )


def shortlist(
    *,
    ranked: list[RetrievalCandidate],
    selected: list[RetrievalCandidate],
    limit: int,
    tokens: int,
    mode: str = "relevance",
) -> None:
    """结构化检索的收尾：返回给调用方的命中（尚未进入 prompt）。

    与 :func:`rank` 的区别只在文案与字段：``search_hits`` 的结果还要经注入侧
    ``select_memory_context`` 去重与预算裁剪，不能在这里声称「已进入 system prompt」。
    """
    truncated = ranked[len(selected):]
    mode_cn = "时间浏览" if mode == "time" else "相关性检索"
    lines = [
        f"【记忆检索·筛选】模式={mode_cn} 有效候选 {len(ranked)} 条 → 返回 {len(selected)} 条"
        f"（上限 {limit} 条，后续由注入侧统一去重与预算）"
    ]
    for i, c in enumerate(selected[:_limit_items()], 1):
        lines.append(f"      {i}. {_fmt_item(c)}")
    if len(selected) > _limit_items():
        lines.append(f"      … 另有 {len(selected) - _limit_items()} 条省略")
    _emit(
        "reranked",
        lines,
        trace_id=current_id(),
        surface="hits",
        mode=mode,
        ranked=len(ranked),
        selected=len(selected),
        truncated=len(truncated),
        limit=limit,
        tokens=tokens,
        selected_ids=[c.memory_id for c in selected],
    )


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
    """语义路融合明细：FTS5/LIKE ∪ 向量（RRF 融合）→ 有效性/可见性过滤 → 返回。

    ``discarded_inactive`` 与 ``discarded_invisible`` 分开统计：前者是行没了/
    作废/过期，后者是**命中了但不在可见集合内**——后者才是「工作区隔离挡掉了」。

    ``unfiltered_matches`` 只在可见集合内结果为空时给出（忽略可见性条件的
    文本命中数），用来回答「0 条」是没匹配上还是被隔离挡掉。
    """
    fts5_ids = [mem.id for mem, _ in fts5_hits]
    vector_ids = [str(mid) for mid, _ in vector_hits]

    lines = [f"【混合检索·语义路】查询={query!r}（FTS5/LIKE ∪ 向量，RRF 名次融合）"]

    lines.append(f"  · FTS5/LIKE {len(fts5_hits)} 条")
    if not fts5_hits:
        lines.append("      （无命中：FTS5 用 unicode61 分词器，不切分中文词，"
                     "中文查询通常只能靠 LIKE 回退或向量路）")
    for mem, score in fts5_hits[:_limit_items()]:
        lines.append(f"      [{mem.id[:8]}] 分={score:.3f} | {_one_line(mem.content)}")
    if len(fts5_hits) > _limit_items():
        lines.append(f"      … 另有 {len(fts5_hits) - _limit_items()} 条省略")

    lines.append(f"  · 向量(余弦) {len(vector_hits)} 条")
    for mid, score in vector_hits[:_limit_items()]:
        content = content_by_id.get(str(mid), "")
        suffix = f" | {_one_line(content)}" if content else ""
        lines.append(f"      [{str(mid)[:8]}] 分={score:.3f}{suffix}")
    if len(vector_hits) > _limit_items():
        lines.append(f"      … 另有 {len(vector_hits) - _limit_items()} 条省略")

    fts5_set, vector_set = set(fts5_ids), set(vector_ids)
    both = fts5_set & vector_set
    lines.append(
        f"  · 并集 {len(merged)} 条（两路都命中 {len(both)} / "
        f"仅 FTS5 {len(fts5_set - vector_set)} / 仅向量 {len(vector_set - fts5_set)}）"
    )

    if discarded_inactive:
        lines.append(f"  · 有效性过滤丢弃 {len(discarded_inactive)} 条（已作废 / 已过期 / 行已删）:")
        for mid in discarded_inactive[:_limit_items()]:
            lines.append(f"      [丢弃] [{str(mid)[:8]}]")
    if discarded_invisible:
        lines.append(
            f"  · 工作区可见性丢弃 {len(discarded_invisible)} 条"
            "（命中但不在可见集合内）:"
        )
        for mid in discarded_invisible[:_limit_items()]:
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

    lines.append(f"  · 按融合名次取前 {limit} → 返回 {len(out)} 条")
    for mem, score in out:
        lines.append(f"      [{mem.id[:8]}] 分={score:.3f} | {_one_line(mem.content)}")
    _emit(
        "retrieval_hybrid",
        lines,
        trace_id=current_id(),
        query=obs.clip(query, limit=120),
        fts5=len(fts5_hits),
        vector=len(vector_hits),
        merged=len(merged),
        returned=len(out),
        discarded_inactive=len(discarded_inactive),
        discarded_invisible=len(discarded_invisible),
    )


__all__ = [
    "ENABLED",
    "CONTENT_CHARS",
    "MAX_ITEMS",
    "current_id",
    "start",
    "elapsed",
    "skipped",
    "time_browse",
    "injection",
    "recall",
    "dedupe",
    "rank",
    "shortlist",
    "hybrid",
]
