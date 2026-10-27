"""store 适配层：把 ``MemoryDatabase`` 适配成四路通道期望的 store 契约。

背景（RCA 2026-09-15 根因 1）：``RetrievalEngine`` 原先直接持有
``MemoryDatabase``，而四个通道以**方法**形式调用 store
（``store.search_semantic_scored(...)`` 等）；真实实现却是
``nanobot.memory.repository`` 的**模块级函数**（首参 ``conn``）。
两者对不上 → 每次召回都抛 ``AttributeError`` → ``candidates`` 恒空 →
``retrieve_with_ids`` 恒返回 ``("", [])``。而该异常在 ``engine.py`` 里被
静默 ``continue`` 吞掉，所以故障多年不可见。

本模块是二者之间**唯一**的适配点：对外暴露通道期望的 4 个方法，对内按调用
粒度开关 ``MemoryDatabase.connect()``，复用 repository 的既有 SQL。

语义方法（``search_semantic_scored``）自 2026-09-16 起做**向量 ∪ FTS5 最高分
并集**（spec §4.6）：传入 ``vector_store`` 时两路各取 ``limit*3`` 候选按 id 融合，
不传时行为与改造前完全一致（R2 向后兼容）。
"""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from nanobot.memory import repository
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.retrieval import trace


class MemoryStoreAdapter:
    """把 ``MemoryDatabase`` 适配为检索通道期望的 store 契约。

    方法契约（与 ``nanobot/memory/retrieval/channels/*.py`` 调用点对应）：

    - ``search_semantic_scored(query, *, limit) -> list[(Memory, float)]``
      向量 ∪ FTS5 最高分并集（需注入 ``vector_store``；未注入 = 纯 FTS5，行为不变）
    - ``search_episodes(*, entity, limit) -> list[_EpisodeRow]``
    - ``query_semantic(*, min_importance, since_days, limit) -> list[_MemoryRow]``
    - ``search_attachments(term, *, intent, limit) -> list[_AttachmentRow]``
      ⚠️ **元素形状与通道不符**（见该方法的 docstring）——通道按下标/字典协议
      取值（``item["id"]`` / ``item.get(...)``），而这里返回属性协议 dataclass。
      因 ``attachments`` 表尚未进 schema v1，该通道**当前不可达**，故本条只记录
      缺口、不在本 WU 内改返回类型（改契约属独立决策）。

    测试替身见 ``tests/memory/retrieval/test_engine.py::_StubStore``——
    本类与它实现同一方法集（``_StubStore`` 的 attachments 同样返回 ``list[Any]``）。
    """

    def __init__(
        self, database: MemoryDatabase, *, vector_store: Any = None
    ) -> None:
        self._database = database
        self._vector_store = vector_store

    # ----- 语义通道 -----
    def search_semantic_scored(
        self,
        query: str,
        *,
        keywords: list[str] | None = None,
        limit: int = 30,
        workspace_ids: Sequence[str] | None = None,
    ) -> list[tuple[Any, float]]:
        """语义召回：FTS5 ∪ 向量，按 RRF 融合（spec §3.4）。

        融合策略 v2 = RRF（Elasticsearch 默认 k=60），替换 v1 的 max()。
        RRF 只看名次不消费分数，因此 FTS5 侧页内 min-max 归一化与向量侧
        距离翻转均为「内部实现细节」，融合结果与两路分数分布无关。
        单条候选的 relevance < 1.0（绝对归一化）。

        关键词通过 keywords 参数下沉（FTS5/LIKE 逐词 OR）；不传时退化为
        [query]（行为与改造前一致）。
        """
        # RRF 融合（Elasticsearch 默认 k=60）
        k = 60
        rrf_max = 2.0 / (k + 1)  # 两路皆第 1 名的理论上界 ≈ 0.032787

        rrf: dict[str, float] = {}

        # FTS5 路
        fts5_hits: list[tuple[Any, float]] = []
        with self._database.connect() as conn:
            fts5_hits = repository.search_semantic_scored(
                conn, query, keywords=keywords, limit=limit * 3, workspace_ids=workspace_ids
            )
        for rank, (mem, _score) in enumerate(fts5_hits, start=1):
            rrf[mem.id] = rrf.get(mem.id, 0.0) + 1.0 / (k + rank)

        # 向量路：故障时静默降级为纯 FTS5（D4）。
        vector_hits: list[tuple[Any, float]] = []
        if self._vector_store is not None:
            try:
                vector_hits = self._vector_store.search(query, limit=limit * 3)
            except Exception as exc:  # noqa: BLE001 - 向量故障绝不上抛
                logger.warning("vector channel failed, degrading to fts5: {}", exc)
                vector_hits = []
            for rank, (mid, _score) in enumerate(vector_hits, start=1):
                rrf[str(mid)] = rrf.get(str(mid), 0.0) + 1.0 / (k + rank)

        # 按 RRF 归一化分排序
        ordered = sorted(rrf.items(), key=lambda kv: kv[1], reverse=True)[:limit]

        out: list[tuple[Any, float]] = []
        fts5_by_id = {mem.id: mem for mem, _s in fts5_hits}
        content_by_id = {mem.id: mem.content for mem, _ in fts5_hits}
        # 向量独有 id 在活性或可见性过滤中被丢弃的
        discarded_ids: list[str] = []
        with self._database.connect() as conn:
            for mid, rrf_score in ordered:
                mem = fts5_by_id.get(mid)
                if mem is None:
                    # 向量独有 id：回查 SQLite 权威行并做活性与可见性过滤
                    mem = repository.get_memory(conn, mid)
                    if mem is None or not _is_live(mem) or not _is_visible(mem, workspace_ids):
                        discarded_ids.append(mid)
                        continue
                    content_by_id[mid] = mem.content
                elif not _is_visible(mem, workspace_ids):
                    discarded_ids.append(mid)
                    continue
                # 绝对归一化：单条候选 relevance < 1.0
                relevance = min(1.0, rrf_score / rrf_max)
                out.append((mem, relevance))

        trace.hybrid(
            query=query,
            fts5_hits=fts5_hits,
            vector_hits=vector_hits,
            merged=rrf,
            out=out,
            content_by_id=content_by_id,
            discarded_ids=discarded_ids,
            limit=limit,
        )
        return out

    # ----- 情节通道 -----
    def search_episodes(
        self, *, entity: str, limit: int = 5, workspace_ids: Sequence[str] | None = None
    ) -> list[Any]:
        """按实体名召回 episode。"""
        with self._database.connect() as conn:
            return repository.search_episodes(
                conn, entity=entity, limit=limit, workspace_ids=workspace_ids
            )

    # ----- 近期通道 -----
    def query_semantic(
        self, *, min_importance: float, since_days: int, limit: int,
        workspace_ids: Sequence[str] | None = None,
    ) -> list[Any]:
        """近期高重要性记忆。"""
        with self._database.connect() as conn:
            return repository.query_semantic(
                conn,
                min_importance=min_importance,
                since_days=since_days,
                limit=limit,
                workspace_ids=workspace_ids,
            )

    # ----- 附件通道 -----
    def search_attachments(
        self, term: str, *, intent: str, limit: int = 5
    ) -> list[Any]:
        """附件搜索。

        ``attachments`` 表尚未进入 schema v1（``repository.search_attachments``
        的 docstring 已声明「调用方须保证 schema 已扩展」）。表缺失时返回 ``[]``，
        而不是让 ``OperationalError`` 冒泡——该通道对普通 query 本就是条件性空
        操作（媒体词闸门，见 ``channels/attachments.py:41-45``），此处消化的只是
        「表还没建」这一结构性缺失，而非掩盖真实故障。真实故障仍会经
        ``engine.py`` 的 ``logger.warning`` 暴露。

        ⚠️ 已记录的契约缺口（2026-09-15 审查发现，**当前不可达**）：
        ``channels/attachments.py:52-66`` 用**下标/字典协议**消费元素
        （``item["id"]`` / ``item["content"]`` / ``item.get("updated_at", ...)`` /
        ``item.get("importance_score", ...)``），而 ``repository.search_attachments``
        返回的是属性协议 dataclass ``_AttachmentRow``。一旦建表让本方法不再抛
        ``OperationalError``，通道会立刻在 ``item["id"]`` 上抛
        ``TypeError: '_AttachmentRow' object is not subscriptable``——与 RCA 根因 1
        同类的 store↔channel 契约错位，只是被「表不存在」挡住了。
        修法二选一（属独立决策，不在本 WU 范围）：本方法把行转成 dict，或改通道
        用属性访问；建议连同 ``type: Protocol`` 一起收口，让静态检查能提前发现。
        """
        try:
            with self._database.connect() as conn:
                return repository.search_attachments(
                    conn, term=term, intent=intent, limit=limit
                )
        except sqlite3.OperationalError as exc:
            logger.debug("attachments channel unavailable: {}", exc)
            return []


def _is_visible(memory: Any, workspace_ids: Sequence[str] | None) -> bool:
    """回表后按权威行判定可见性；FTS5 侧已由 SQL 过滤。"""
    if workspace_ids is None:
        return True
    allowed = {str(item) for item in workspace_ids}
    return str(getattr(memory, "workspace_id", "default") or "default") in allowed


def _is_live(memory: Any) -> bool:
    """向量独有候选的活性判定（spec §4.6）。

    Chroma 的 ``where`` 表达力不足，scope / superseded / expired 三类校验只能
    在 SQLite 侧做。
    """
    if getattr(memory, "superseded_by", None):
        return False
    expires_at = getattr(memory, "expires_at", None)
    if expires_at:
        try:
            if datetime.fromisoformat(str(expires_at)) < datetime.now(timezone.utc):
                return False
        except (ValueError, TypeError):
            # 无法解析的 expires_at 视为未过期（宽松），避免误杀
            pass
    return True
