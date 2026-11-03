"""store 适配层：把 ``MemoryDatabase`` 适配成召回通道期望的 store 契约。

背景（RCA 2026-09-15 根因 1）：``RetrievalEngine`` 原先直接持有
``MemoryDatabase``，而各通道以**方法**形式调用 store
（``store.search_semantic_scored(...)`` 等）；真实实现却是
``nanobot.memory.repository`` 的**模块级函数**（首参 ``conn``）。
两者对不上 → 每次召回都抛 ``AttributeError`` → ``candidates`` 恒空 →
``retrieve_with_ids`` 恒返回 ``("", [])``。而该异常在 ``engine.py`` 里被
静默 ``continue`` 吞掉，所以故障多年不可见。

本模块是二者之间**唯一**的适配点：对外暴露通道期望的方法，对内按调用
粒度开关 ``MemoryDatabase.connect()``，复用 repository 的既有 SQL。

语义方法（``search_semantic_scored``）做**FTS5/LIKE ∪ 向量名次 RRF 融合**
（spec §6.1）：传入 ``vector_store`` 时两路各取 ``limit*3`` 候选，按名次融合；
不传时行为与纯文本路径一致。

有效性语义只有一处（契约 §3）：SQL 候选由 ``repository.current_memory_sql``
过滤，向量独有 id 回表由 ``repository.get_current_memory`` 用同一谓词判定——
本模块不再自带 ``_is_live`` / ``_is_visible`` 第二套判定。

注（D09 2026-11-02）：独立 recent 通道已删除；「最近」改由 ``engine`` 的显式
时间浏览（``list_recent_memories``）与 ``reranker`` 的时效次级排序承担。
"""
from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from nanobot.memory import repository
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.retrieval import trace
from nanobot.memory.retrieval.reranker import rrf_scores

_RRF_K = 60
# 两路皆第 1 名的理论上界 ≈ 0.032787：用于把融合分归一化到 (0, 1]
_RRF_MAX = 2.0 / (_RRF_K + 1)


class MemoryStoreAdapter:
    """把 ``MemoryDatabase`` 适配为检索通道期望的 store 契约。

    方法契约（与 ``nanobot/memory/retrieval/channels/*.py`` 调用点对应）：

    - ``search_semantic_scored(query, *, keywords, limit, workspace_ids,
      include_inactive) -> list[(Memory, float)]``
    - ``search_episodes(*, entity, limit, workspace_ids) -> list[_EpisodeRow]``
    - ``list_recent_memories(*, since_days, limit, workspace_ids,
      include_inactive) -> list[Memory]``

    测试替身见 ``tests/memory/retrieval/test_engine.py::_StubStore``——
    本类与它实现同一方法集。
    """

    def __init__(
        self,
        database: MemoryDatabase,
        *,
        vector_store: Any = None,
        clock: Any = None,
    ) -> None:
        self._database = database
        self._vector_store = vector_store
        self._clock = clock

    def _now(self) -> datetime:
        """当前时间；注入 clock 时使用虚拟墙上时间。"""
        if self._clock is not None:
            return self._clock.wall_now()
        return datetime.now(timezone.utc)

    # ----- 语义通道 -----
    def search_semantic_scored(
        self,
        query: str,
        *,
        keywords: list[str] | None = None,
        limit: int = 30,
        workspace_ids: Sequence[str] | None = None,
        include_inactive: bool = False,
    ) -> list[tuple[Any, float]]:
        """语义召回：FTS5/LIKE ∪ 向量，按 RRF 名次融合（spec §6.1）。

        RRF 只看名次不消费分数，因此 FTS5 侧页内 min-max 归一化与向量侧
        距离翻转均为「内部实现细节」，融合结果与两路分数分布无关。
        单条候选的 relevance < 1.0（绝对归一化）。

        关键词通过 keywords 参数下沉（FTS5/LIKE 逐词 OR）；不传时退化为
        [query]（行为与改造前一致）。

        有效性（契约 §3）：默认只看当前有效事实；``include_inactive=True``
        供管理查询放开。向量独有 id 回表复用同一 SQL 谓词。
        """
        fts5_hits: list[tuple[Any, float]] = []
        with self._database.connect() as conn:
            fts5_started = time.perf_counter()
            fts5_hits = repository.search_semantic_scored(
                conn,
                query,
                keywords=keywords,
                limit=limit * 3,
                workspace_ids=workspace_ids,
                include_inactive=include_inactive,
            )
            fts5_ms = (time.perf_counter() - fts5_started) * 1000

        # 向量路：故障时静默降级为纯文本（D4）。
        vector_hits: list[tuple[Any, float]] = []
        if self._vector_store is not None:
            try:
                vector_hits = self._vector_store.search(query, limit=limit * 3)
            except Exception as exc:  # noqa: BLE001 - 向量故障绝不上抛
                logger.warning("vector channel failed, degrading to fts5: {}", exc)
                vector_hits = []

        rrf = rrf_scores(
            [
                [mem.id for mem, _score in fts5_hits],
                [str(mid) for mid, _score in vector_hits],
            ],
            k=_RRF_K,
        )
        ordered = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)[:limit]

        out: list[tuple[Any, float]] = []
        fts5_by_id = {mem.id: mem for mem, _s in fts5_hits}
        content_by_id = {mem.id: mem.content for mem, _ in fts5_hits}
        # 丢弃分两类：行没了/作废/过期（有效性），以及命中但不在可见集合内。
        discarded_inactive: list[str] = []
        discarded_invisible: list[str] = []
        with self._database.connect() as conn:
            for mid, rrf_score in ordered:
                mem = fts5_by_id.get(mid)
                if mem is None:
                    # 向量独有 id：回查 SQLite 权威行，有效性/可见性交给同一谓词
                    mem = repository.get_current_memory(
                        conn,
                        mid,
                        workspace_ids=workspace_ids,
                        include_inactive=include_inactive,
                    )
                    if mem is None:
                        # 仅此诊断路径多一次查询：区分「被作用域挡掉」与「已失效」
                        visible_probe = repository.get_current_memory(
                            conn, mid, workspace_ids=None,
                            include_inactive=include_inactive,
                        )
                        if visible_probe is not None:
                            discarded_invisible.append(mid)
                        else:
                            discarded_inactive.append(mid)
                        continue
                    content_by_id[mid] = mem.content
                # 绝对归一化：单条候选 relevance < 1.0
                out.append((mem, min(1.0, rrf_score / _RRF_MAX)))

        # 诊断：可见集合内一条都没有时，回答「是没匹配上还是被工作区挡掉」。
        # 文本侧的工作区过滤是 SQL 预过滤，被挡的行不进命中列表 → 只看结果
        # 分辨不出来，故补一次**忽略可见性**的计数（仅此空结果路径付开销）。
        unfiltered_matches: dict[str, int] | None = None
        if not out and workspace_ids is not None:
            with self._database.connect() as conn:
                unfiltered_matches = repository.count_semantic_matches_unfiltered(
                    conn, query, keywords=keywords
                )

        trace.hybrid(
            query=query,
            fts5_hits=fts5_hits,
            vector_hits=vector_hits,
            merged=rrf,
            out=out,
            content_by_id=content_by_id,
            discarded_inactive=discarded_inactive,
            discarded_invisible=discarded_invisible,
            unfiltered_matches=unfiltered_matches,
            limit=limit,
        )
        trace.elapsed(stage="语义路 FTS5 查询", ms=fts5_ms)
        return out

    # ----- 访问统计（衰减的输入）-----
    def record_accesses(self, hits: Sequence[Any]) -> None:
        """检索命中即刷新访问统计（P2）。

        注入侧与工具检索都会调用本方法。``memory`` 条目更新
        ``last_accessed_at`` + ``access_count``——每日盘账的
        ``days_since_last_access`` 就按这里的热路径算，常用的条目因此自动
        远离归档线；``episode`` 侧只累加 ``access_count``（表里没有
        ``last_accessed_at`` 列，情节不参与衰减）。
        """
        memory_ids: list[str] = []
        episode_ids: list[str] = []
        for hit in hits or ():
            kind = getattr(hit, "kind", "")
            record_id = str(getattr(hit, "record_id", "") or "")
            if not record_id:
                continue
            if kind == "memory":
                memory_ids.append(record_id)
            elif kind == "episode":
                episode_ids.append(record_id)
        if not memory_ids and not episode_ids:
            return
        with self._database.connect() as conn:
            if memory_ids:
                repository.touch_memories(conn, memory_ids)
            for episode_id in episode_ids:
                conn.execute(
                    "UPDATE episodes SET access_count = access_count + 1 WHERE id = ?",
                    (episode_id,),
                )

    # ----- 情节通道 -----
    def search_episodes(
        self, *, entity: str, limit: int = 5, workspace_ids: Sequence[str] | None = None
    ) -> list[Any]:
        """按实体名召回 episode。"""
        with self._database.connect() as conn:
            return repository.search_episodes(
                conn, entity=entity, limit=limit, workspace_ids=workspace_ids
            )

    # ----- 显式时间浏览（不是召回通道）-----
    def list_recent_memories(
        self,
        *,
        since_days: int,
        limit: int,
        workspace_ids: Sequence[str] | None = None,
        include_inactive: bool = False,
    ) -> list[Any]:
        """受限时间列表：窗口内当前有效事实，按保存时间倒序。"""
        with self._database.connect() as conn:
            return repository.list_recent_memories(
                conn,
                since_days=since_days,
                limit=limit,
                workspace_ids=workspace_ids,
                include_inactive=include_inactive,
            )
