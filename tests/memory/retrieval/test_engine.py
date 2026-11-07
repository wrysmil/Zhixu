"""Tests for RetrievalEngine：两路召回 + 时间浏览 + 结构化命中（D09/D11）。"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.memory.retrieval.engine import RetrievalEngine, _time_browse_window


def _memory(memory_id: str, content: str, **kw) -> SimpleNamespace:
    base = dict(
        id=memory_id,
        content=content,
        workspace_id="default",
        created_at="2026-11-01T10:00:00+00:00",
        metadata={},
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _StubStore:
    """Stub 取代真实 SQLite：验证两路召回 + 时间浏览 + 格式化走通。"""

    def __init__(self) -> None:
        self.search_semantic_scored_called = 0
        self.search_episodes_called = 0
        self.list_recent_memories_called = 0
        self.semantic_payload: list[tuple[Any, float]] = [(_memory("s1", "Python 爬虫"), 0.9)]
        self.episodes_payload: list[Any] = []
        self.recent_payload: list[Any] = []
        self.semantic_should_raise = False
        self.recent_kwargs: dict[str, Any] = {}

    def search_semantic_scored(
        self, q, *, keywords=None, limit=None, workspace_ids=None,
        include_inactive=False,
    ):
        self.search_semantic_scored_called += 1
        if self.semantic_should_raise:
            raise RuntimeError("semantic down")
        return self.semantic_payload

    def search_episodes(self, *, entity, limit=None, workspace_ids=None, **filters):
        self.search_episodes_called += 1
        self.episode_filters = filters
        return self.episodes_payload

    def list_recent_memories(self, *, since_days, limit, workspace_ids=None):
        self.list_recent_memories_called += 1
        self.recent_kwargs = {
            "since_days": since_days,
            "limit": limit,
            "workspace_ids": workspace_ids,
        }
        return self.recent_payload


# ---- Gate 短路 ----

@pytest.mark.asyncio
async def test_engine_returns_empty_when_gate_skips():
    """控制词 ``好`` → gate skip → 空字符串；两路都不调用。"""
    store = _StubStore()
    engine = RetrievalEngine(store=store, brain=None)
    out = await engine.retrieve(query="好", recent_messages=[], max_tokens=500)
    assert out == ""
    assert store.search_semantic_scored_called == 0
    assert store.search_episodes_called == 0
    assert store.list_recent_memories_called == 0


# ---- 全路径 ----

@pytest.mark.asyncio
async def test_engine_relevance_path_calls_two_channels():
    """相关性路径只调度 semantic / episodes；recent 通道已删除（D09）。"""
    store = _StubStore()
    engine = RetrievalEngine(store=store, brain=None)
    out = await engine.retrieve(
        query="我之前写的 Python 爬虫脚本 main.py 还能用吗",
        recent_messages=[{"role": "user", "content": "..."}],
        max_tokens=500,
    )
    assert "相关记忆" in out
    assert "Python 爬虫" in out
    assert store.search_semantic_scored_called == 1
    assert store.search_episodes_called >= 1  # keywords 兜底后可能有多个 entity
    assert store.list_recent_memories_called == 0


@pytest.mark.asyncio
async def test_engine_no_hits_returns_empty():
    """无有效候选 → 返回空，不注入空段头（D11）。"""
    store = _StubStore()
    store.semantic_payload = []
    engine = RetrievalEngine(store=store, brain=None)
    block, ids = await engine.retrieve_with_ids(
        query="完全不存在的主题",
        recent_messages=[{"role": "user", "content": "在吗"}],
    )
    assert block == ""
    assert ids == []


# ---- 异常隔离 ----

@pytest.mark.asyncio
async def test_engine_one_channel_failure_isolated():
    """semantic 抛异常 → 不阻断 episodes，返回值为字符串（无候选时为 ``""``）。"""
    store = _StubStore()
    store.semantic_should_raise = True
    engine = RetrievalEngine(store=store, brain=None)
    out = await engine.retrieve(
        query="我之前写的 Python 爬虫脚本 main.py 还能用吗",
        recent_messages=[{"role": "user", "content": "..."}],
        max_tokens=500,
    )
    assert isinstance(out, str)
    assert store.search_semantic_scored_called == 1
    assert store.search_episodes_called >= 1


# ---- 去重 ----

@pytest.mark.asyncio
async def test_engine_dedup_by_kind_and_id():
    """同一 ``(kind, id)`` 多条 → 只保留首次出现的一条。"""
    store = _StubStore()
    store.semantic_payload = [
        (_memory("dup", "low version"), 0.3),
        (_memory("dup", "high version"), 0.8),
    ]
    engine = RetrievalEngine(store=store, brain=None)
    out = await engine.retrieve(
        query="Python 爬虫脚本", recent_messages=[], max_tokens=500,
    )
    assert "low version" in out
    assert "high version" not in out


@pytest.mark.asyncio
async def test_engine_keeps_same_id_different_kind():
    """事实与情节同裸 ID 不去重：按 (kind, id) 各自保留。"""
    store = _StubStore()
    store.semantic_payload = [(_memory("shared", "事实内容"), 0.9)]
    store.episodes_payload = [
        SimpleNamespace(
            id="shared",
            summary="情节内容",
            session_id="s-1",
            workspace_id="default",
            ended_at="2026-11-01T11:00:00+00:00",
        )
    ]
    engine = RetrievalEngine(store=store, brain=None)
    hits = await engine.search_hits(
        query="main.py 的历史", recent_messages=[], limit=10,
    )
    assert {hit.key for hit in hits} == {("memory", "shared"), ("episode", "shared")}


@pytest.mark.asyncio
async def test_engine_labels_episode_and_keeps_it_out_of_memory_ids():
    """注入块里情节标注为 Episode；第二个返回值只含事实 id。"""
    store = _StubStore()
    store.semantic_payload = [(_memory("m1", "事实内容"), 0.9)]
    store.episodes_payload = [
        SimpleNamespace(
            id="e1",
            summary="情节内容",
            session_id="s-1",
            workspace_id="default",
            ended_at="2026-11-01T11:00:00+00:00",
        )
    ]
    engine = RetrievalEngine(store=store, brain=None)
    block, ids = await engine.retrieve_with_ids(
        query="main.py 的历史", recent_messages=[],
    )
    assert "事实内容" in block and "情节内容" in block
    assert "(ID: m1)" in block
    assert "(Episode: e1)" in block
    assert ids == ["m1"]


@pytest.mark.asyncio
async def test_engine_shortens_episode_id_in_block():
    """长 UUID 只展示前 8 位，避免模型抄写 32 位 id 时出错。"""
    episode_id = "0123456789abcdef0123456789abcdef"
    store = _StubStore()
    store.episodes_payload = [
        SimpleNamespace(
            id=episode_id,
            summary="情节内容",
            session_id="s-1",
            workspace_id="default",
            ended_at="2026-11-01T11:00:00+00:00",
        )
    ]
    engine = RetrievalEngine(store=store, brain=None)
    block, _ = await engine.retrieve_with_ids(
        query="main.py 的历史", recent_messages=[],
    )
    assert "(Episode: 01234567)" in block
    assert episode_id not in block


# ---- 纯时间查询 ----

@pytest.mark.parametrize(
    "query,days",
    [
        ("最近保存的记忆", 7),
        ("最近几天的记忆", 3),
        ("今天有什么新内容", 1),
        ("上周记录的内容", 7),
        ("这个月保存的", 30),
    ],
)
def test_time_browse_window_detection(query, days):
    assert _time_browse_window(query) == days


@pytest.mark.parametrize(
    "query",
    ["最近的 Python 记忆", "上周部署失败的原因", "Python 爬虫", "记忆是什么"],
)
def test_topic_query_is_not_time_browse(query):
    assert _time_browse_window(query) is None


@pytest.mark.asyncio
async def test_engine_pure_time_query_browses_recent_list():
    """明确纯时间查询走受限时间列表，并标注为时间浏览。"""
    store = _StubStore()
    store.recent_payload = [
        _memory("newer", "昨天记下的事"),
        _memory("older", "上周记下的事"),
    ]
    engine = RetrievalEngine(store=store, brain=None)
    block, ids = await engine.retrieve_with_ids(
        query="最近保存的记忆", recent_messages=[], workspace_ids=("default",),
    )
    assert store.list_recent_memories_called == 1
    assert store.recent_kwargs["since_days"] == 7
    assert store.recent_kwargs["workspace_ids"] == ("default",)
    assert store.search_semantic_scored_called == 0
    assert block.startswith("## 最近记忆（时间浏览）")
    assert "时间浏览" in block and "未按相关性筛选" in block
    assert "相关记忆（自动检索）" not in block
    assert ids == ["newer", "older"]


@pytest.mark.asyncio
async def test_engine_time_browse_skips_empty_content():
    store = _StubStore()
    store.recent_payload = [_memory("blank", "   "), _memory("ok", "有效内容")]
    engine = RetrievalEngine(store=store, brain=None)
    hits = await engine.search_hits(query="最近记录的记忆", recent_messages=[], limit=10)
    assert [hit.record_id for hit in hits] == ["ok"]
    assert hits[0].rank == 1


# ---- 结构化结果 ----

@pytest.mark.asyncio
async def test_search_hits_returns_structured_hits():
    store = _StubStore()
    store.semantic_payload = [
        (_memory("m1", "用户在杭州", metadata={"provenance": {"session_id": "s-1"}}), 0.9)
    ]
    engine = RetrievalEngine(store=store, brain=None)
    hits = await engine.search_hits(
        query="用户住在哪里", recent_messages=[], workspace_ids=("default",), limit=3,
    )
    assert len(hits) == 1
    hit = hits[0]
    assert hit.kind == "memory"
    assert hit.record_id == "m1"
    assert hit.workspace_id == "default"
    assert hit.source_session == "s-1"
    assert hit.source_refs == ()
    assert hit.rank == 1


# ---- 输出是 markdown 字符串 ----

@pytest.mark.asyncio
async def test_engine_output_is_markdown_string():
    store = _StubStore()
    engine = RetrievalEngine(store=store, brain=None)
    out = await engine.retrieve(
        query="我之前写的 Python 爬虫脚本 main.py 还能用吗",
        recent_messages=[{"role": "user", "content": "..."}],
        max_tokens=500,
    )
    assert isinstance(out, str)
    assert "## 相关记忆" in out
    assert "\n" in out
    assert "- " in out  # markdown bullet


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["retrieve", "retrieve_with_ids"])
async def test_engine_passes_workspace_ids_to_channels(method):
    """两个检索入口均将可见集合传至两路召回。"""
    seen: dict[str, object] = {}

    class _Store:
        def search_semantic_scored(
            self, query, *, keywords=None, limit=30, workspace_ids=None,
            include_inactive=False,
        ):
            seen["semantic"] = workspace_ids
            return []

        def search_episodes(self, *, entity, limit=5, workspace_ids=None):
            seen["episodes"] = workspace_ids
            return []

    engine = RetrievalEngine(store=_Store())
    await getattr(engine, method)(
        query="创作灵感", recent_messages=[], precomputed_keywords=["创作灵感"],
        workspace_ids=("default", "ws-a"),
    )
    assert seen == dict.fromkeys(("semantic", "episodes"), ("default", "ws-a"))


# ---- 经验库类型过滤 ----

def _episode(eid: str, summary: str, metadata: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=eid,
        summary=summary,
        workspace_id="default",
        ended_at="2026-11-06T10:00:00+00:00",
        session_id="s1",
        metadata=metadata,
    )


@pytest.mark.asyncio
async def test_kind_filter_restricts_to_episodes_channel():
    store = _StubStore()
    store.episodes_payload = [
        _episode(
            "e1",
            "症状：数据库连接超时\n根因：mysql 未就绪\n修复：service_healthy",
            {"title": "连接超时", "kind": "pitfall", "status": "active"},
        )
    ]
    engine = RetrievalEngine(store=store, brain=None)
    out = await engine.retrieve(
        query="数据库连接超时怎么办",
        recent_messages=[],
        precomputed_keywords=["数据库连接超时"],
        kind="pitfall",
    )
    assert store.search_semantic_scored_called == 0
    assert store.episode_filters.get("kind") == "pitfall"
    assert "[踩坑]" in out


@pytest.mark.asyncio
async def test_non_active_status_is_labeled():
    store = _StubStore()
    store.episodes_payload = [
        _episode(
            "e1",
            "能力：初始化项目\n步骤：1. uv init",
            {"title": "初始化项目", "kind": "skill", "status": "deprecated"},
        )
    ]
    engine = RetrievalEngine(store=store, brain=None)
    out = await engine.retrieve(
        query="初始化项目",
        recent_messages=[],
        precomputed_keywords=["初始化项目"],
        kind="skill",
        status="deprecated",
    )
    assert store.episode_filters.get("status") == "deprecated"
    assert "[技能·已弃用]" in out


@pytest.mark.asyncio
async def test_outcome_filter_skips_semantic_channel():
    store = _StubStore()
    engine = RetrievalEngine(store=store, brain=None)
    await engine.retrieve(
        query="构建失败",
        recent_messages=[],
        precomputed_keywords=["构建失败"],
        outcome="failed",
    )
    assert store.search_semantic_scored_called == 0
    assert store.episode_filters.get("outcome") == "failed"
