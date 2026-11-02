"""Tests for the semantic recall channel."""
from __future__ import annotations

from types import SimpleNamespace

from nanobot.memory.retrieval.channels.semantic import search_semantic


class _FakeStore:
    def __init__(self, rows):
        self._rows = rows

    def search_semantic_scored(self, query, *, keywords=None, limit=None):
        return self._rows


def test_returns_structured_candidates():
    raw_memory = SimpleNamespace(
        id="m-1",
        content="Python 爬虫",
        workspace_id="ws-a",
        created_at="2026-11-01T10:00:00+00:00",
        metadata={"provenance": {"session_id": "s-1"}},
    )
    store = _FakeStore([(raw_memory, 0.9)])
    cands = search_semantic(store, query="Python 爬虫", limit=10)
    assert len(cands) == 1
    c = cands[0]
    assert c.source_channel == "semantic"
    assert c.hit.kind == "memory"
    assert c.memory_id == "m-1"
    assert c.hit.workspace_id == "ws-a"
    assert c.hit.source_session == "s-1"
    assert c.raw is raw_memory


def test_channel_does_not_invent_scores():
    """通道不再计算相关分：融合分由 RRF 统一写入（默认 0.0）。"""
    raw_memory = SimpleNamespace(id="m-1", content="Python 爬虫")
    cands = search_semantic(_FakeStore([(raw_memory, 0.9)]), query="x", limit=10)
    assert cands[0].composite_score == 0.0


def test_empty_results():
    assert search_semantic(_FakeStore([]), query="x", limit=10) == []
