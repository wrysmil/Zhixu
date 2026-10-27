"""记忆可见集合的工作区解析与请求透传。"""
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from nanobot.agent.context import ContextBuilder
from nanobot.agent.loop import AgentLoop
from nanobot.memory.provenance import memory_workspace_id
from nanobot.security.workspace_access import WorkspaceScopeResolver


def test_visibility_includes_global_and_project(tmp_path: Path) -> None:
    ids = AgentLoop._memory_workspace_ids_for_path(str(tmp_path))
    assert ids == ("default", memory_workspace_id(str(tmp_path)))
    assert len(ids) == 2


def test_visibility_without_path_is_global_only() -> None:
    assert AgentLoop._memory_workspace_ids_for_path(None) == ("default",)


@pytest.mark.parametrize("cached", [False, True])
def test_tool_visibility_uses_only_cached_session(tmp_path: Path, cached: bool) -> None:
    """会话替身仅提供 get_cached，禁止额外读取磁盘会话。"""
    calls: list[str] = []

    def get_cached(key: str):
        calls.append(key)
        if not cached:
            return None
        return SimpleNamespace(metadata={"workspace_scope": {"project_path": str(tmp_path)}})

    loop = object.__new__(AgentLoop)
    loop.sessions = cast(Any, SimpleNamespace(get_cached=get_cached))
    ids = loop._memory_workspace_ids_for_key("s1")
    assert calls == ["s1"]
    assert ids == (("default", memory_workspace_id(str(tmp_path))) if cached else ("default",))


def test_turn_visibility_uses_current_message_scope(tmp_path: Path) -> None:
    """消息工作区覆盖会话中上一轮的工作区。"""
    loop = object.__new__(AgentLoop)
    previous = tmp_path / "previous"
    previous.mkdir()
    loop.workspace_scopes = WorkspaceScopeResolver(previous, False)
    ctx = cast(Any, SimpleNamespace(
        msg=SimpleNamespace(channel="websocket", metadata={
            "workspace_scope": {"project_path": str(tmp_path)},
        }),
        session=SimpleNamespace(metadata={
            "workspace_scope": {"project_path": str(previous)},
        }),
    ))
    assert loop._memory_workspace_ids_for_turn(ctx) == (
        "default", memory_workspace_id(str(tmp_path))
    )


def test_channel_without_workspace_sees_only_global(tmp_path: Path) -> None:
    """渠道默认执行目录不能被当作会话记忆归属。"""
    loop = object.__new__(AgentLoop)
    loop.workspace_scopes = WorkspaceScopeResolver(tmp_path, False)
    ctx = cast(Any, SimpleNamespace(
        msg=SimpleNamespace(channel="telegram", metadata={}),
        session=SimpleNamespace(metadata={}),
    ))
    assert loop._memory_workspace_ids_for_turn(ctx) == ("default",)


@pytest.mark.asyncio
async def test_retrieval_section_forwards_visibility_to_engine(tmp_path: Path) -> None:
    """loop → context → engine 的自动注入保留可见集合。"""
    seen = []

    class _Engine:
        async def retrieve_with_ids(self, *, query, recent_messages, workspace_ids=None):
            seen.append(workspace_ids)
            return "命中", ["m1"]

    loop = object.__new__(AgentLoop)
    loop.context = ContextBuilder(
        tmp_path, active_retrieval_enabled=True, retrieval_engine=cast(Any, _Engine()),
    )
    assert await loop._compute_retrieval_section(
        query="创作灵感", recent_messages=[], workspace_ids=("default", "ws-a"),
    ) == ("命中", ["m1"])
    assert seen == [("default", "ws-a")]


def test_scoped_channel_without_explicit_scope_sees_only_global(tmp_path: Path) -> None:
    """受管渠道没有显式作用域时，不得把默认执行目录当成记忆归属。

    默认执行目录与写入侧（会话 ``workspace_scope``）不是同一口径，若读侧回退到
    默认目录，同一会话会出现两套可见集合（D3：没有工作区的会话只见全局）。
    """
    loop = object.__new__(AgentLoop)
    loop.workspace_scopes = WorkspaceScopeResolver(tmp_path, False)
    ctx = cast(Any, SimpleNamespace(
        msg=SimpleNamespace(channel="websocket", metadata={}),
        session=SimpleNamespace(metadata={}),
    ))
    assert loop._memory_workspace_ids_for_turn(ctx) == ("default",)


def test_scoped_channel_uses_session_scope_when_message_has_none(tmp_path: Path) -> None:
    """消息没有作用域时，回落到会话记录的作用域。"""
    project = tmp_path / "project"
    project.mkdir()
    loop = object.__new__(AgentLoop)
    loop.workspace_scopes = WorkspaceScopeResolver(tmp_path, False)
    ctx = cast(Any, SimpleNamespace(
        msg=SimpleNamespace(channel="websocket", metadata={}),
        session=SimpleNamespace(metadata={
            "workspace_scope": {"project_path": str(project)},
        }),
    ))
    assert loop._memory_workspace_ids_for_turn(ctx) == (
        "default", memory_workspace_id(str(project))
    )
