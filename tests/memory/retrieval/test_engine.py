"""Tests for RetrievalEngine (T-12)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.memory.retrieval.engine import RetrievalEngine


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _StubStore:
    """Stub 取代真实 SQLite：验证 4 通道都被调用 + rerank + formatter 走通。"""

    def __init__(self) -> None:
        self.search_semantic_scored_called = 0
        self.search_episodes_called = 0
        self.query_semantic_called = 0
        self.search_attachments_called = 0
        # 默认：semantic 返回 1 条候选；其余 3 路空
        self.semantic_payload: list[tuple[Any, float]] = [
            (
                SimpleNamespace(
                    id="s1",
                    content="Python 爬虫",
                    importance_score=0.8,
                    updated_at=_now(),
                    access_count=2,
                    type=None,
                    source="user",
                    subject=None,
                    predicate=None,
                    confidence=1.0,
                ),
                0.9,
            )
        ]
        # 控制 semantic 抛异常的开关（用于测试 4）
        self.semantic_should_raise: bool = False

    def search_semantic_scored(self, q, *, keywords=None, limit=None):
        self.search_semantic_scored_called += 1
        if self.semantic_should_raise:
            raise RuntimeError("semantic down")
        return self.semantic_payload

    def search_episodes(self, *, entity, limit=None):
        self.search_episodes_called += 1
        return []

    def query_semantic(
        self, *, min_importance: float, since_days: int, limit: int
    ) -> list[Any]:
        self.query_semantic_called += 1
        return []

    def search_attachments(
        self, term: str, *, intent: str, limit: int
    ) -> list[Any]:
        self.search_attachments_called += 1
        return []


# ---- Gate 短路 ----


@pytest.mark.asyncio
async def test_engine_returns_empty_when_gate_skips():
    """控制词 ``好`` → gate skip → 空字符串；4 通道都不调用。"""
    store = _StubStore()
    engine = RetrievalEngine(store=store, brain=None)
    out = await engine.retrieve(query="好", recent_messages=[], max_tokens=500)
    assert out == ""
    assert store.search_semantic_scored_called == 0
    assert store.search_episodes_called == 0
    assert store.query_semantic_called == 0
    assert store.search_attachments_called == 0


# ---- 全路径 ----


@pytest.mark.asyncio
async def test_engine_full_path_with_keywords():
    """非空 + 非控制词 + 含文件扩展名 → semantic/episodes/recent 都被调用。

    注：
    - episodes 通道仅在 query 含路径/扩展名时触发（``_extract_query_entities``），
      故 query 中带 ``main.py`` 以保证 episodes 通道真正进入 store 层。
    - attachments 通道有「媒体闸门」（``_MEDIA_KEYWORDS`` 或 ``intent=search_file``），
      无媒体暗示时不调用 ``store.search_attachments``，故此处不 assert == 1。
      媒体闸门由 ``test_engine_attachments_only_on_media_hint`` 专项验证。
    """
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
    assert store.query_semantic_called == 1


# ---- 附件闸门 ----


@pytest.mark.asyncio
async def test_engine_attachments_only_on_media_hint():
    """无媒体暗示 → attachments 不调用；有 → 至少调用一次。

    注：
    - query 必须非短（≤12 字 + 无 recent_messages 会被 preprocessor gate 跳过），
      故提供 ``recent_messages`` 让 gate 通过；attachments 闸门仅检 query 中的媒体词。
    - attachments 通道会按 ``[raw_query] + keywords`` 多次调用 ``store.search_attachments``
      （每个 term 一次），故用 ``>= 1`` 断言。
    """
    store = _StubStore()
    engine = RetrievalEngine(store=store, brain=None)
    ctx = [{"role": "user", "content": "context"}]
    # 无媒体暗示
    await engine.retrieve(query="随便聊", recent_messages=ctx, max_tokens=500)
    assert store.search_attachments_called == 0
    # 有媒体暗示（图）
    await engine.retrieve(
        query="找一下之前的图片", recent_messages=ctx, max_tokens=500,
    )
    assert store.search_attachments_called >= 1


# ---- 异常隔离 ----


@pytest.mark.asyncio
async def test_engine_one_channel_failure_isolated():
    """semantic 抛异常 → 不阻断其余通道，返回值为字符串（无候选时为 ``""``）。

    注：query 含 ``main.py`` 以触发 episodes 通道；无媒体暗示 → attachments 不调用。
    """
    store = _StubStore()
    store.semantic_should_raise = True
    engine = RetrievalEngine(store=store, brain=None)
    out = await engine.retrieve(
        query="我之前写的 Python 爬虫脚本 main.py 还能用吗",
        recent_messages=[{"role": "user", "content": "..."}],
        max_tokens=500,
    )
    assert isinstance(out, str)
    # semantic 已尝试；episodes/recent 仍被调用；attachments 被闸门拦截
    assert store.search_semantic_scored_called == 1
    assert store.search_episodes_called >= 1  # keywords 兜底后可能有多个 entity
    assert store.query_semantic_called == 1
    assert store.search_attachments_called == 0


# ---- 去重 ----


@pytest.mark.asyncio
async def test_engine_dedup_by_memory_id_keeps_highest_relevance():
    """同一 ``memory_id`` 多条 → 保留最高 relevance 那条。"""
    store = _StubStore()
    store.semantic_payload = [
        (
            SimpleNamespace(
                id="dup",
                content="low version",
                importance_score=0.5,
                updated_at=_now(),
                access_count=0,
                type=None,
                source="user",
                subject=None,
                predicate=None,
                confidence=1.0,
            ),
            0.3,
        ),
        (
            SimpleNamespace(
                id="dup",
                content="high version",
                importance_score=0.9,
                updated_at=_now(),
                access_count=5,
                type=None,
                source="user",
                subject=None,
                predicate=None,
                confidence=1.0,
            ),
            0.8,
        ),
    ]
    engine = RetrievalEngine(store=store, brain=None)
    out = await engine.retrieve(
        query="我之前写的 Python 爬虫脚本 main.py 还能用吗",
        recent_messages=[{"role": "user", "content": "..."}],
        max_tokens=500,
    )
    assert "high version" in out
    assert "low version" not in out


# ---- 输出是 markdown 字符串 ----


@pytest.mark.asyncio
async def test_engine_output_is_markdown_string():
    """返回值是 markdown 字符串：含 ``## 相关记忆`` header + 换行 + bullet list。"""
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
    """两个检索入口均将可见集合传至三条记忆通道。"""
    seen: dict[str, object] = {}

    class _Store:
        def search_semantic_scored(self, query, *, keywords=None, limit=30, workspace_ids=None):
            seen["semantic"] = workspace_ids
            return []

        def search_episodes(self, *, entity, limit=5, workspace_ids=None):
            seen["episodes"] = workspace_ids
            return []

        def query_semantic(self, *, min_importance, since_days, limit, workspace_ids=None):
            seen["recent"] = workspace_ids
            return []

        def search_attachments(self, term, *, intent, limit=5):
            return []

    engine = RetrievalEngine(store=_Store())
    await getattr(engine, method)(
        query="创作灵感", recent_messages=[], precomputed_keywords=["创作灵感"],
        workspace_ids=("default", "ws-a"),
    )
    assert seen == dict.fromkeys(("semantic", "episodes", "recent"), ("default", "ws-a"))
