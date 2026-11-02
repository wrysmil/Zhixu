"""Tests for MemoryHit / RetrievalCandidate（契约 §4）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nanobot.memory.retrieval.candidate import (
    MemoryHit,
    RetrievalCandidate,
    hit_from_episode,
    hit_from_memory,
)


def _hit(**kw) -> MemoryHit:
    base = dict(
        kind="memory",
        record_id="m-1",
        content="Python 爬虫脚本",
        workspace_id="default",
    )
    base.update(kw)
    return MemoryHit(**base)


def test_hit_constructor_fields():
    hit = _hit(
        source_session="s-1",
        source_refs=("msg-1",),
        occurred_at="2026-11-01T10:00:00+00:00",
        rank=2,
    )
    assert hit.kind == "memory"
    assert hit.record_id == "m-1"
    assert hit.workspace_id == "default"
    assert hit.source_session == "s-1"
    assert hit.source_refs == ("msg-1",)
    assert hit.occurred_at == "2026-11-01T10:00:00+00:00"
    assert hit.rank == 2


def test_hit_defaults_are_explicit_empty():
    hit = _hit()
    assert hit.source_session is None
    assert hit.source_refs == ()
    assert hit.occurred_at is None
    assert hit.rank == 0


def test_hit_key_includes_kind():
    """同一裸 ID 落在不同 kind 上是两条记录，键必须带 kind。"""
    assert _hit(kind="memory", record_id="x").key == ("memory", "x")
    assert _hit(kind="episode", record_id="x").key == ("episode", "x")
    assert _hit(kind="memory", record_id="x").key != _hit(
        kind="episode", record_id="x"
    ).key


def test_hit_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind"):
        _hit(kind="bogus")


def test_candidate_wraps_hit_and_defaults():
    hit = _hit()
    candidate = RetrievalCandidate(hit=hit, source_channel="semantic")
    assert candidate.hit is hit
    assert candidate.memory_id == "m-1"
    assert candidate.content == "Python 爬虫脚本"
    assert candidate.composite_score == 0.0
    assert candidate.raw is None


def test_candidate_keeps_raw_row():
    raw = SimpleNamespace(id="m-1", type="fact")
    candidate = RetrievalCandidate(hit=_hit(), source_channel="episodes", raw=raw)
    assert candidate.raw is raw


def test_candidate_rejects_unknown_channel():
    with pytest.raises(ValueError, match="source_channel"):
        RetrievalCandidate(hit=_hit(), source_channel="recent")


def test_hit_from_memory_reads_provenance_and_workspace():
    memory = SimpleNamespace(
        id="m-9",
        content="用户住在杭州",
        workspace_id="ws-a",
        created_at="2026-11-01T08:00:00+00:00",
        metadata={"provenance": {"session_id": "websocket:abc"}},
    )
    hit = hit_from_memory(memory)
    assert hit.kind == "memory"
    assert hit.record_id == "m-9"
    assert hit.workspace_id == "ws-a"
    assert hit.source_session == "websocket:abc"
    assert hit.occurred_at == "2026-11-01T08:00:00+00:00"


def test_hit_from_memory_without_provenance_leaves_refs_empty():
    """旧数据没有消息引用时明确为空，不伪造。"""
    memory = SimpleNamespace(id="m-1", content="x", created_at=None)
    hit = hit_from_memory(memory)
    assert hit.workspace_id == "default"
    assert hit.source_session is None
    assert hit.source_refs == ()
    assert hit.occurred_at is None


def test_hit_from_episode_maps_summary_and_time():
    episode = SimpleNamespace(
        id="e-1",
        summary="调试 main.py 脚本",
        session_id="s-9",
        workspace_id="ws-b",
        ended_at="2026-11-01T11:00:00+00:00",
    )
    hit = hit_from_episode(episode)
    assert hit.kind == "episode"
    assert hit.record_id == "e-1"
    assert hit.content == "调试 main.py 脚本"
    assert hit.workspace_id == "ws-b"
    assert hit.source_session == "s-9"
    assert hit.occurred_at == "2026-11-01T11:00:00+00:00"
