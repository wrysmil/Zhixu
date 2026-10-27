"""MemorySearchTool：会话注入 + 检索参数透传（RCA 2026-09-15 根因 3）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.memory_search import MemorySearchTool


class _FakeSessions:
    """最小 SessionManager 替身：只实现 ``get_cached``。"""

    def __init__(self, session):
        self._session = session
        self.calls: list[str] = []

    def get_cached(self, key: str):
        self.calls.append(key)
        return self._session


class _FakeEngine:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def retrieve(
        self, *, query: str, recent_messages: list, max_tokens: int, workspace_ids=None
    ):
        self.calls.append(
            {"query": query, "recent_messages": recent_messages, "max_tokens": max_tokens,
             "workspace_ids": workspace_ids}
        )
        return "## 相关记忆（自动检索）\n- 命中"


def _ctx(sessions, engine) -> SimpleNamespace:
    return SimpleNamespace(sessions=sessions, attributes={"retrieval_engine": engine})


def test_create_injects_sessions():
    """``create`` 必须把 ToolContext.sessions 注入实例（此前恒缺失）。"""
    tool = MemorySearchTool.create(_ctx(_FakeSessions(None), _FakeEngine()))
    assert isinstance(tool, MemorySearchTool)
    assert tool._sessions is not None


def test_create_tolerates_missing_sessions():
    """ToolContext.sessions 为 None 时不应崩。"""
    tool = MemorySearchTool.create(SimpleNamespace(attributes={}))
    assert tool._sessions is None


@pytest.mark.asyncio
async def test_execute_tolerates_session_not_cached():
    """``get_cached`` 返回 ``None``（会话不在缓存）时 ``recent`` 应为 ``[]``。

    该分支在修复前**从未被执行**（``hasattr(self, "_sessions")`` 恒 False），
    修复后首次可达——补一条边界守卫，防止未来有人在未判空的情况下取
    ``session.messages``。
    """
    sessions = _FakeSessions(None)
    engine = _FakeEngine()
    tool = MemorySearchTool.create(_ctx(sessions, engine))

    ctx = RequestContext(channel="websocket", chat_id="c1", session_key="s1")
    with request_context(ctx):
        await tool.execute(query="记忆")

    assert sessions.calls == ["s1"]
    assert engine.calls[0]["recent_messages"] == []


@pytest.mark.asyncio
async def test_tool_scopes_retrieval_to_session_workspace():
    """create 从 attributes 解析当前请求会话的可见集合。"""
    engine = _FakeEngine()
    seen = []

    def resolve(key):
        seen.append(key)
        return ("default", "ws-a")

    tool = MemorySearchTool.create(SimpleNamespace(
        sessions=None,
        attributes={"retrieval_engine": engine, "memory_workspace_ids": resolve},
    ))
    with request_context(RequestContext(channel="websocket", chat_id="c1", session_key="s1")):
        result = await tool.execute(query="创作灵感")
    assert result.startswith("## 相关记忆")
    assert seen == ["s1"]
    assert engine.calls[0]["workspace_ids"] == ("default", "ws-a")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "error", "no_session"])
async def test_tool_visibility_falls_back_to_global(mode):
    """缺解析器、解析异常或无请求会话时仅检索全局。"""
    engine = _FakeEngine()

    def resolve(key):
        raise RuntimeError("无法解析工作区")

    tool = MemorySearchTool(
        retrieval_engine_provider=lambda: engine,
        workspace_ids_provider=None if mode == "missing" else resolve,
    )
    with request_context(RequestContext(
        channel="websocket", chat_id="c1", session_key=None if mode == "no_session" else "s1"
    )):
        result = await tool.execute(query="创作灵感")
    assert result.startswith("## 相关记忆")
    assert engine.calls[0]["workspace_ids"] == ("default",)


@pytest.mark.asyncio
async def test_execute_passes_session_history_as_recent_messages():
    """有会话缓存时，最近 10 条消息必须作为 ``recent_messages`` 传给引擎——
    这同时也是绕过 ``short_without_context`` 门禁的前提。"""
    history = [{"role": "user", "content": f"m{i}"} for i in range(15)]
    sessions = _FakeSessions(SimpleNamespace(messages=history))
    engine = _FakeEngine()
    tool = MemorySearchTool.create(_ctx(sessions, engine))

    ctx = RequestContext(channel="websocket", chat_id="c1", session_key="s1")
    with request_context(ctx):
        out = await tool.execute(query="记忆")

    assert out.startswith("## 相关记忆")
    assert sessions.calls == ["s1"]
    assert engine.calls[0]["recent_messages"] == history[-10:]


@pytest.mark.asyncio
async def test_execute_without_session_context_passes_empty_recent():
    """无 request context 时不取会话，``recent_messages`` 为空列表。"""
    sessions = _FakeSessions(SimpleNamespace(messages=[{"role": "user", "content": "x"}]))
    engine = _FakeEngine()
    tool = MemorySearchTool.create(_ctx(sessions, engine))

    await tool.execute(query="记忆")

    assert sessions.calls == []
    assert engine.calls[0]["recent_messages"] == []
