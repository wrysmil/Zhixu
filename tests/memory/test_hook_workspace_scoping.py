"""提取 hook 重建 Session 时必须带上归属工作区（跨工作区泄漏回归）。

2026-10-27 代码审查 P1：``MemoryExtractionHook`` 的 idle / 增量提取会**重建**
``Session``，原先只复制 messages，不带 ``metadata.workspace_scope``。于是提取
落库时 ``session_memory_workspace_id`` 退化成 ``'default'``，本工作区的记忆被
标成全局记忆 → 其它工作区也能召回（跨工作区泄漏）。

本文件锁定：重建出来的 Session 必须携带归属工作区。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from nanobot.agent.hook import AgentRunHookContext, AgentTurnHookContext
from nanobot.agent.hooks.memory_extraction import (
    _PENDING_IDLE_TIMERS,
    MemoryExtractionHook,
)
from nanobot.agent.loop import AgentLoop
from nanobot.memory.provenance import memory_workspace_id
from nanobot.security.workspace_access import WorkspaceScopeResolver
from tests.memory.test_hook_memory_extraction import _FakeExtractor, _FakeScratchpadWriter


@pytest.fixture(autouse=True)
async def _cleanup_idle_timers():
    yield
    for task in list(_PENDING_IDLE_TIMERS.values()):
        if not task.done():
            task.cancel()
    await asyncio.sleep(0)
    _PENDING_IDLE_TIMERS.clear()


def _hook(extractor: _FakeExtractor, project: Path) -> MemoryExtractionHook:
    return MemoryExtractionHook(
        extractor,
        "websocket:s1",
        _FakeScratchpadWriter(),
        idle_seconds=5.0,
        workspace_scope={"project_path": str(project)},
    )


async def test_idle_extraction_session_carries_workspace(tmp_path: Path) -> None:
    """idle 提取重建的 Session 必须携带 workspace_scope。"""
    project = tmp_path / "project"
    project.mkdir()
    extractor = _FakeExtractor()
    hook = _hook(extractor, project)

    await hook.after_run(AgentRunHookContext(messages=[{"role": "user", "content": "你好"}]))
    await hook._run_idle_extraction(
        AgentRunHookContext(messages=[{"role": "user", "content": "你好"}])
    )
    await hook.on_finally(AgentRunHookContext(messages=[]))

    assert len(extractor.idle_calls) == 1
    scope = extractor.idle_calls[0].metadata.get("workspace_scope")
    assert isinstance(scope, dict), "idle 提取重建的 Session 丢失了 workspace_scope"
    assert scope["project_path"] == str(project)
    # 归属键必须与写入侧一致
    assert memory_workspace_id(scope["project_path"]) == memory_workspace_id(str(project))


async def test_incremental_extraction_session_carries_workspace(tmp_path: Path) -> None:
    """增量（话题切换）提取重建的 Session 同样必须携带 workspace_scope。"""
    project = tmp_path / "project"
    project.mkdir()
    extractor = _FakeExtractor()
    hook = _hook(extractor, project)

    await hook._run_incremental_extraction([{"role": "user", "content": "你好"}], 0)
    await hook.on_finally(AgentRunHookContext(messages=[]))

    assert len(extractor.incremental_calls) == 1
    session = extractor.incremental_calls[0][0]
    scope = session.metadata.get("workspace_scope")
    assert isinstance(scope, dict), "增量提取重建的 Session 丢失了 workspace_scope"
    assert scope["project_path"] == str(project)


async def test_hook_without_workspace_keeps_global_scope(tmp_path: Path) -> None:
    """没有归属时不得凭空造出工作区（保持全局）。"""
    extractor = _FakeExtractor()
    hook = MemoryExtractionHook(
        extractor, "websocket:s1", _FakeScratchpadWriter(), idle_seconds=5.0
    )
    await hook._run_idle_extraction(AgentRunHookContext(messages=[]))
    await hook.on_finally(AgentRunHookContext(messages=[]))

    assert len(extractor.idle_calls) == 1
    assert extractor.idle_calls[0].metadata.get("workspace_scope") is None


# ---------------------------------------------------------------------------
# loop 侧：归属解析必须是读写两侧共用的唯一真相源
# ---------------------------------------------------------------------------


def _loop_with_scopes(workspace: Path) -> AgentLoop:
    loop = object.__new__(AgentLoop)
    loop.workspace_scopes = WorkspaceScopeResolver(workspace, False)
    loop.sessions = cast(Any, type("_S", (), {"get_cached": staticmethod(lambda _k: None)})())
    return loop


def test_scope_provider_uses_message_scope_for_scoped_channel(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    loop = _loop_with_scopes(tmp_path)
    context = cast(Any, AgentTurnHookContext(
        channel="websocket",
        session_key="s1",
        metadata={"workspace_scope": {"project_path": str(project)}},
    ))
    assert loop._memory_workspace_scope_for_context(context) == {"project_path": str(project)}


def test_scope_provider_for_other_channel_uses_session_scope_only(tmp_path: Path) -> None:
    """渠道默认执行目录不是归属；只认会话显式记录的 workspace_scope。"""
    project = tmp_path / "project"
    project.mkdir()
    loop = _loop_with_scopes(tmp_path)

    session = cast(Any, type(
        "_S", (), {"metadata": {"workspace_scope": {"project_path": str(project)}}}
    )())
    loop.sessions = cast(Any, type(
        "_S", (), {"get_cached": staticmethod(lambda _k: session)}
    )())

    context = cast(Any, AgentTurnHookContext(channel="telegram", session_key="s1", metadata={}))
    assert loop._memory_workspace_scope_for_context(context) == {"project_path": str(project)}


def test_scope_provider_returns_none_without_ownership(tmp_path: Path) -> None:
    loop = _loop_with_scopes(tmp_path)
    context = cast(Any, AgentTurnHookContext(channel="telegram", session_key="s1", metadata={}))
    assert loop._memory_workspace_scope_for_context(context) is None
