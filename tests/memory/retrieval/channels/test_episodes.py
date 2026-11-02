"""Tests for the episodes recall channel.

情节的可检索投影是 ``summary`` / ``goal`` / ``entities`` / ``tools_used``
（spec §6.1）；查询侧只做确定性投影，**不做**中文 n-gram / 2~4 字硬切碎片。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nanobot.memory.retrieval.channels.episodes import (
    _MAX_QUERY_TERMS,
    _extract_query_entities,
    _projection_query_terms,
    search_episodes,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("帮我打开 C:\\Users\\foo\\bar.py", ["C:\\Users\\foo\\bar.py"]),
        ("看一下 main.py 文件", ["main.py"]),
        ("那个 build.sh 还在吗", ["build.sh"]),
        ("普通聊天没有任何路径", []),
    ],
)
def test_extract_query_entities(text, expected):
    entities = _extract_query_entities(text)
    for e in expected:
        assert e in entities


# ---------------------------------------------------------------------------
# 查询侧投影：确定性、无碎片
# ---------------------------------------------------------------------------


def test_projection_terms_keep_chinese_phrase_whole():
    """中文原样整段作为子串检索词，不硬切 2~4 字碎片（spec §6.2）。"""
    terms = _projection_query_terms("帮我重构一下记忆系统", None)
    assert terms == ["帮我重构一下记忆系统"]


def test_projection_terms_have_no_fragment_terms():
    """不得产出跨词的短碎片：``帮我`` / ``一下`` 之类不能单独占一个检索槽。"""
    terms = _projection_query_terms("帮我重构一下记忆系统", None)
    assert "帮我" not in terms
    assert "一下" not in terms
    # 每个检索词都必须真的出现在 query 里（可检索投影，不是模型生成物）
    assert all(term in "帮我重构一下记忆系统" for term in terms)


def test_projection_terms_drop_fragments_covered_by_filename():
    """被文件名覆盖的碎片（storage / .py）不重复占配额。"""
    terms = _projection_query_terms("storage.py 那个文件上次是不是改过？", None)
    assert terms[0] == "storage.py"
    assert "storage" not in terms


def test_projection_terms_include_tool_and_path_identifiers():
    """工具名 / 路径这类 ``tools_used`` / ``summary`` 里会出现的词必须进检索面。"""
    terms = _projection_query_terms("write_file 改过 D:\\repo\\storage.py 吗", None)
    assert "write_file" in terms
    assert "D:\\repo\\storage.py" in terms


def test_projection_terms_are_capped():
    """检索词有界：每个词一次 5 列 LIKE 查询。"""
    terms = _projection_query_terms("a.py b.py c.py d.py e.py", ["a.py b.py c.py d.py e.py"])
    assert len(terms) <= _MAX_QUERY_TERMS
    for name in ("a.py", "b.py", "c.py", "d.py", "e.py"):
        assert name in terms


# ---------------------------------------------------------------------------
# 通道行为
# ---------------------------------------------------------------------------


def test_search_episodes_returns_structured_episode():
    class _FakeStore:
        def __init__(self, mapping):
            self.mapping = mapping

        def search_episodes(self, entity, limit):
            return self.mapping.get(entity, [])

    fake_episode = SimpleNamespace(
        id="ep-1",
        summary="用户配置了 uv 环境",
        session_id="s-1",
        workspace_id="ws-a",
        ended_at="2026-11-01T11:00:00+00:00",
    )
    store = _FakeStore({"main.py": [fake_episode]})
    cands = search_episodes(store, query="main.py 还在吗", limit=5)
    assert len(cands) == 1
    cand = cands[0]
    assert cand.source_channel == "episodes"
    assert cand.hit.kind == "episode"
    assert cand.memory_id == "ep-1"
    assert cand.hit.content == "用户配置了 uv 环境"
    assert cand.hit.source_session == "s-1"
    assert cand.hit.occurred_at == "2026-11-01T11:00:00+00:00"
    assert cand.composite_score == 0.0


def test_search_episodes_dedupes_same_episode_across_terms():
    """多个检索词命中同一条 episode 时只保留一条（否则白占注入预算）。"""
    episode = SimpleNamespace(
        id="ep-1", summary="重构 storage.py", session_id="s-1",
        workspace_id="ws-a", ended_at="2026-11-01T11:00:00+00:00",
    )

    class _AlwaysHitStore:
        def __init__(self):
            self.calls: list[str] = []

        def search_episodes(self, entity, limit):
            self.calls.append(entity)
            return [episode]

    store = _AlwaysHitStore()
    cands = search_episodes(store, query="storage.py 那个文件上次是不是改过？", limit=5)
    assert [c.memory_id for c in cands] == ["ep-1"]
    assert len(store.calls) >= 2, "前置条件：query 应产出多个检索词才能验证去重"


def test_search_episodes_tries_chinese_phrase_not_fragments():
    """中文 query 只按整段短语查询，不制造碎片检索词。"""
    class _CountingStore:
        def __init__(self):
            self.calls: list[str] = []

        def search_episodes(self, entity, limit):
            self.calls.append(entity)
            return []

    store = _CountingStore()
    search_episodes(store, query="帮我重构一下记忆系统", keywords=[], limit=5)
    assert store.calls == ["帮我重构一下记忆系统"]


def test_search_episodes_forwards_workspace_ids():
    class _Store:
        def __init__(self):
            self.seen: list[object] = []

        def search_episodes(self, entity, limit, workspace_ids=None):
            self.seen.append(workspace_ids)
            return []

    store = _Store()
    search_episodes(store, query="main.py", limit=5, workspace_ids=("ws-a",))
    assert store.seen == [("ws-a",)]
