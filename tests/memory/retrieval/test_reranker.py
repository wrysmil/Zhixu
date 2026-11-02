"""Tests for Reranker：RRF 名次融合 + 相关性筛选 + 时效次级排序（D11）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nanobot.memory.retrieval.candidate import MemoryHit, RetrievalCandidate
from nanobot.memory.retrieval.reranker import Reranker, rrf_scores


def _cand(
    record_id: str,
    *,
    channel: str = "semantic",
    kind: str = "memory",
    content: str = "x",
    occurred_at: str | None = None,
    raw=None,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        hit=MemoryHit(
            kind=kind,
            record_id=record_id,
            content=content,
            workspace_id="default",
            occurred_at=occurred_at,
        ),
        source_channel=channel,
        raw=raw,
    )


def test_rrf_scores_sum_across_rankings():
    scores = rrf_scores([["a", "b"], ["b"]], k=60)
    assert scores["a"] == pytest.approx(1 / 61)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)


def test_rerank_fuses_channel_ranks_with_rrf():
    channels = {
        "semantic": [_cand("sem", content="语义命中")],
        "episodes": [_cand("ep", channel="episodes", kind="episode", content="情节命中")],
    }
    out = Reranker().rerank(channels)
    assert {c.hit.key for c in out} == {("memory", "sem"), ("episode", "ep")}
    assert sorted(c.hit.rank for c in out) == [1, 2]


def test_rerank_multi_channel_hit_outranks_single_channel_hit():
    """RRF 的职责：被两路同时命中的条目累计名次，排在只被一路命中的前面。"""
    channels = {
        "semantic": [_cand("both", content="两路"), _cand("sem-only", content="单路")],
        "episodes": [_cand("both", channel="episodes", content="两路")],
    }
    out = Reranker().rerank(channels)
    assert [c.memory_id for c in out] == ["both", "sem-only"]
    assert out[0].hit.rank == 1
    assert out[1].hit.rank == 2


def test_rerank_dedupes_same_kind_and_id():
    channels = {
        "semantic": [_cand("dup", content="同一个")],
        "episodes": [_cand("dup", channel="episodes", content="同一个")],
    }
    out = Reranker().rerank(channels)
    assert len(out) == 1


def test_rerank_keeps_same_id_with_different_kind():
    """同一裸 ID 的事实与情节是两条记录，不能合成一条。"""
    channels = {
        "semantic": [_cand("same-id", content="事实")],
        "episodes": [_cand("same-id", channel="episodes", kind="episode", content="情节")],
    }
    out = Reranker().rerank(channels)
    assert {c.hit.key for c in out} == {("memory", "same-id"), ("episode", "same-id")}
    assert len(out) == 2


def test_rerank_drops_invalid_candidates():
    """相关性筛选：空 id 或空内容不算有效候选；全无效 → 空结果。"""
    channels = {
        "semantic": [
            _cand("", content="没有 id"),
            _cand("empty", content="   "),
        ]
    }
    assert Reranker().rerank(channels) == []


def test_rerank_empty_channels_returns_empty():
    assert Reranker().rerank({}) == []
    assert Reranker().rerank({"semantic": [], "episodes": []}) == []


def test_rerank_within_channel_keeps_retriever_order():
    """同一路内保留召回名次；时间只在跨路同分时做次级排序。"""
    channels = {
        "semantic": [
            _cand("old", occurred_at="2020-01-01T00:00:00+00:00"),
            _cand("new", occurred_at="2026-11-01T00:00:00+00:00"),
        ]
    }
    out = Reranker().rerank(channels)
    assert [c.memory_id for c in out] == ["old", "new"]


def test_rerank_tiebreak_uses_time_when_scores_equal_across_channels():
    channels = {
        "semantic": [_cand("old", occurred_at="2020-01-01T00:00:00+00:00")],
        "episodes": [
            _cand(
                "new", channel="episodes", kind="episode",
                occurred_at="2026-11-01T00:00:00+00:00",
            )
        ],
    }
    out = Reranker().rerank(channels)
    # 两路都只有第 1 名 → 同分；时效次级排序让新记录在前
    assert [c.memory_id for c in out] == ["new", "old"]


def test_rerank_ignores_access_count():
    """访问次数不再影响排序（D11）：raw.access_count 差异不改变顺序。"""
    hot = SimpleNamespace(id="hot", access_count=10_000)
    cold = SimpleNamespace(id="cold", access_count=0)
    cold_first = {"semantic": [_cand("second", raw=cold), _cand("first", raw=hot)]}
    out = Reranker().rerank(cold_first)
    assert [c.memory_id for c in out] == ["second", "first"]


def test_rerank_scores_are_rrf_not_probability():
    """融合分只反映名次，不冒充「高度相关」的概率阈值。"""
    channels = {"semantic": [_cand("a"), _cand("b")]}
    out = Reranker().rerank(channels)
    assert out[0].composite_score == pytest.approx(1 / 61)
    assert out[1].composite_score == pytest.approx(1 / 62)
    assert out[0].composite_score < 1.0
