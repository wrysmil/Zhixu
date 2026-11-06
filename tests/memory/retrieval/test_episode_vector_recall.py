"""Episodes 通道：词法 + 向量 RRF 融合与回表过滤。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from nanobot.memory.retrieval.channels.episodes import search_episodes


def _row(eid: str, summary: str, ended_at: str = "2026-11-06T10:00:00+00:00"):
    return SimpleNamespace(
        id=eid,
        summary=summary,
        session_id="s1",
        workspace_id="default",
        ended_at=ended_at,
        metadata=json.dumps({"title": eid, "kind": "pitfall", "status": "active"}),
    )


class _Store:
    def __init__(self, lexical, vector, fetched):
        self._lexical = lexical
        self._vector = vector
        self._fetched = fetched
        self.filters: dict = {}

    def search_episodes(self, *, entity, limit, **filters):
        self.filters = filters
        return list(self._lexical)

    def search_episodes_vector(self, query, *, limit=15):
        return list(self._vector)

    def get_episodes_by_ids(self, ids, **filters):
        self.filters = filters
        return [row for row in self._fetched if row.id in ids]


def test_vector_only_episode_is_recalled_after_backfill():
    lexical = [_row("e1", "depends_on 配置")]
    fetched = [_row("e2", "容器没有等待数据库就绪", ended_at="2026-11-07T10:00:00+00:00")]
    store = _Store(lexical=lexical, vector=[("e2", 0.95)], fetched=fetched)

    candidates = search_episodes(
        store,
        query="服务启动时连不上数据库",
        keywords=["数据库"],
        limit=5,
        kind="pitfall",
    )

    assert [c.hit.record_id for c in candidates] == ["e2", "e1"]
    assert all(c.hit.entry_kind == "pitfall" for c in candidates)
    assert store.filters.get("kind") == "pitfall"


def test_vector_hits_are_deduped_with_lexical():
    row = _row("e1", "depends_on 配置")
    store = _Store(lexical=[row], vector=[("e1", 0.9)], fetched=[row])

    candidates = search_episodes(
        store, query="depends_on", keywords=["depends_on"], limit=5
    )

    assert [c.hit.record_id for c in candidates] == ["e1"]


def test_without_vector_store_keeps_lexical_order():
    lexical = [_row("e1", "a"), _row("e2", "b")]
    store = _Store(lexical=lexical, vector=[], fetched=[])

    candidates = search_episodes(
        store, query="a b", keywords=["a"], limit=5
    )

    assert [c.hit.record_id for c in candidates] == ["e1", "e2"]
