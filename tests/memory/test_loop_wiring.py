"""WU-07 装配测试：Phase 2 记忆提取接入 AgentLoop / SessionManager。

覆盖三块：
- ``SessionManager`` 删除观察者：删除前捕获 Session、既有单槽位观察者不回归、
  非阻塞派发、观察者异常与未缓存静默跳过。
- ``AgentLoop`` 默认关闭：不注册 hook 工厂、不注册删除观察者、AutoCompact 无
  Quick Facts 回调（零副作用）。
- ``AgentLoop`` opt-in：注册 hook 工厂 / 删除观察者 / Quick Facts 回调，且删除
  会话确实派发一个携带删除前转录的后台提取任务（source="deletion"）。

extractor 在 opt-in 删除路径用假实例替换（``nanobot.memory.extractor.MemoryExtractor``），
避免测试真的调 LLM；其余装配路径使用真实 ``MemoryDatabase``（``tmp_path``）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest

from nanobot.agent.hook import AgentTurnHookContext
from nanobot.agent.hooks.memory_extraction import MemoryExtractionHook
from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.memory.scratchpad_writer import ScratchpadWriter
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import Session, SessionManager
from nanobot.webui.memory_services import MemoryServices

# ---------------------------------------------------------------------------
# 测试替身与构造辅助
# ---------------------------------------------------------------------------


class _RecordingExtractor:
    """替代 MemoryExtractor：记录提取调用，不触达 LLM。"""

    calls: ClassVar[list[tuple[str, str, list[dict[str, Any]]]]] = []

    def __init__(
        self,
        database: Any,
        runtime: Any,
        workspace_id: str = "default",
        user_id: str = "default",
    ) -> None:
        self.database = database
        self.runtime = runtime
        self.workspace_id = workspace_id
        self.user_id = user_id

    async def extract_session(self, session: Session, *, source: str = "session_end") -> Any:
        type(self).calls.append((session.key, source, list(session.messages)))
        return SimpleNamespace(memory_ids=[], episode_ids=[], skipped=0, failed_tracks=[])

    def extract_quick_facts(self, session: Session) -> int:
        return 0


def _make_provider(default_model: str = "test-model") -> MagicMock:
    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = default_model
    provider.generation = SimpleNamespace(max_tokens=4096, temperature=0.1, reasoning_effort=None)
    provider.estimate_prompt_tokens = MagicMock(return_value=(10_000, "test"))
    return provider


def _make_loop(tmp_path: Path, **overrides: Any) -> AgentLoop:
    kwargs: dict[str, Any] = dict(
        bus=MessageBus(),
        provider=_make_provider(),
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=128_000,
    )
    kwargs.update(overrides)
    return AgentLoop(**kwargs)


def _seed_session(sm: SessionManager, key: str, text: str = "remember: run ruff") -> Session:
    session = sm.get_or_create(key)
    session.messages.append({"role": "user", "content": text})
    sm.save(session)
    return session


# ---------------------------------------------------------------------------
# SessionManager：删除观察者
# ---------------------------------------------------------------------------


def test_delete_session_observer_receives_pre_deletion_session(tmp_path: Path) -> None:
    """观察者收到删除前的 Session（含原 messages），既有单槽位观察者不受影响。"""
    sm = SessionManager(tmp_path)
    key = "websocket:abc"
    _seed_session(sm, key, "hello memory")

    deleted_keys: list[str] = []
    captured: list[Session] = []
    sm.set_delete_observer(deleted_keys.append)
    sm.set_delete_session_observer(captured.append)

    assert sm.delete_session(key) is True

    assert deleted_keys == [key]
    assert len(captured) == 1
    assert captured[0].key == key
    # 契约 §1：保存后消息带持久坐标（memory_seq），删除观察者看到的是最终形状。
    assert captured[0].messages == [
        {"role": "user", "content": "hello memory", "memory_seq": 1}
    ]


def test_delete_session_observer_absent_when_not_registered(tmp_path: Path) -> None:
    """未注册时删除照常返回，不引入任何副作用。"""
    sm = SessionManager(tmp_path)
    key = "websocket:plain"
    _seed_session(sm, key)

    assert sm._delete_session_observer is None
    assert sm.delete_session(key) is True
    assert sm.delete_session("websocket:missing") is False


async def test_delete_session_does_not_block_on_observer(tmp_path: Path) -> None:
    """观察者只派发任务；delete_session 立即返回，不等后台工作完成。"""
    sm = SessionManager(tmp_path)
    key = "websocket:slow"
    _seed_session(sm, key)

    progressed: list[str] = []

    async def _work() -> None:
        progressed.append("done")

    def _observer(session: Session) -> None:
        asyncio.get_running_loop().create_task(_work())

    sm.set_delete_session_observer(_observer)

    assert sm.delete_session(key) is True
    assert progressed == []  # 未等待后台任务
    await asyncio.sleep(0)
    assert progressed == ["done"]


def test_delete_session_survives_observer_failure(tmp_path: Path) -> None:
    """观察者抛异常时 delete_session 仍正常返回 True/False。"""
    sm = SessionManager(tmp_path)
    key = "websocket:boom"
    _seed_session(sm, key)

    def _boom(session: Session) -> None:
        raise RuntimeError("observer exploded")

    sm.set_delete_session_observer(_boom)

    assert sm.delete_session(key) is True
    assert sm.delete_session("websocket:missing") is False


def test_delete_session_skips_observer_without_cached_session(tmp_path: Path) -> None:
    """未缓存（如进程重启后直接从磁盘删除）时静默跳过观察者。"""
    sm = SessionManager(tmp_path)
    key = "websocket:ondisk"
    _seed_session(sm, key)
    sm.invalidate(key)

    captured: list[Session] = []
    sm.set_delete_session_observer(captured.append)

    assert sm.delete_session(key) is True
    assert captured == []


# ---------------------------------------------------------------------------
# AgentLoop：默认关闭零副作用
# ---------------------------------------------------------------------------


def test_memory_extraction_disabled_by_default(tmp_path: Path) -> None:
    """默认关闭：无 hook 工厂、无删除观察者、AutoCompact 无 Quick Facts 回调。"""
    loop = _make_loop(tmp_path)

    assert loop._hook_factories == []
    assert loop._memory_extraction_tasks == set()
    assert loop.sessions._delete_session_observer is None
    assert loop.auto_compact._quick_facts_hook is None
    # 未启用抽取时 API 装配拿不到结束 flush 的 provider，端点按契约返回 503。
    assert loop.memory_extractor_provider is None


def test_memory_extraction_disabled_leaves_turn_hook_chain_clean(tmp_path: Path) -> None:
    """默认关闭时按轮工厂链不产出 MemoryExtractionHook。"""
    loop = _make_loop(tmp_path)

    created = [
        factory(AgentTurnHookContext(session_key="websocket:abc"))
        for factory in loop._hook_factories
    ]
    assert not any(isinstance(hook, MemoryExtractionHook) for hook in created)


# ---------------------------------------------------------------------------
# AgentLoop：opt-in 装配
# ---------------------------------------------------------------------------


def test_memory_extraction_enabled_wires_all_three_points(tmp_path: Path) -> None:
    """开启后：hook 工厂 / 删除观察者 / Quick Facts 回调三处均接线。"""
    services = MemoryServices.for_workspace("default", tmp_path)
    loop = _make_loop(
        tmp_path,
        memory_extraction_enabled=True,
        memory_services=services,
    )

    assert len(loop._hook_factories) == 1
    assert loop.sessions._delete_session_observer is not None
    assert loop.auto_compact._quick_facts_hook is not None
    # API 装配（nanobot api）用同一 provider 与作用域构造 SessionEndFlushConsumer。
    assert loop.memory_extractor_provider is not None
    assert loop.memory_extraction_workspace_id == "default"

    created = loop._hook_factories[0](AgentTurnHookContext(session_key="websocket:abc"))
    assert isinstance(created, MemoryExtractionHook)


async def test_deletion_dispatches_background_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删除会话产生后台提取任务，任务携带删除前的转录且 source="deletion"。"""
    _RecordingExtractor.calls = []
    monkeypatch.setattr("nanobot.memory.extractor.MemoryExtractor", _RecordingExtractor)

    services = MemoryServices.for_workspace("default", tmp_path)
    loop = _make_loop(
        tmp_path,
        memory_extraction_enabled=True,
        memory_services=services,
    )
    key = "websocket:deleted"
    _seed_session(loop.sessions, key, "remember: prefer ruff over flake8")

    assert loop.sessions.delete_session(key) is True

    tasks = list(loop._memory_extraction_tasks)
    assert len(tasks) == 1, "删除应派发恰好一个提取任务"
    await asyncio.gather(*tasks)

    assert _RecordingExtractor.calls == [
        (
            key,
            "deletion",
            [
                {
                    "role": "user",
                    "content": "remember: prefer ruff over flake8",
                    "memory_seq": 1,
                }
            ],
        ),
    ]


# ---------------------------------------------------------------------------
# AgentLoop：MemoryExtractor 的 user_id 必须由 session_key 派生
# ---------------------------------------------------------------------------


class _UserIdCapturingExtractor(_RecordingExtractor):
    """记录每次构造的 ``user_id``/``workspace_id``，用于校验装配侧传参。"""

    built: ClassVar[list[tuple[str, str]]] = []

    def __init__(
        self,
        database: Any,
        runtime: Any,
        workspace_id: str = "default",
        user_id: str = "default",
    ) -> None:
        super().__init__(database, runtime, workspace_id=workspace_id, user_id=user_id)
        type(self).built.append((user_id, workspace_id))


def _make_wired_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AgentLoop:
    """装配一个 opt-in 记忆提取的 loop，MemoryExtractor 替换为记录型替身。"""
    _UserIdCapturingExtractor.built = []
    _UserIdCapturingExtractor.calls = []
    monkeypatch.setattr(
        "nanobot.memory.extractor.MemoryExtractor",
        _UserIdCapturingExtractor,
    )
    services = MemoryServices.for_workspace("default", tmp_path)
    return _make_loop(
        tmp_path,
        memory_extraction_enabled=True,
        memory_services=services,
    )


def test_hook_extractor_user_id_derived_from_session_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """调用点 1：hook 主路径的 extractor 按 session_key 派生 user_id。"""
    loop = _make_wired_loop(tmp_path, monkeypatch)

    hook = loop._hook_factories[0](AgentTurnHookContext(session_key="webui:12345"))
    assert isinstance(hook, MemoryExtractionHook)

    assert hook._extractor.user_id == ScratchpadWriter.user_id_for_key("webui:12345")
    assert hook._extractor.user_id == "12345"
    assert hook._extractor.user_id != "default"


async def test_deletion_extractor_user_id_derived_from_session_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """调用点 2：删除会话路径的 extractor 用 ``session.key`` 派生 user_id。"""
    loop = _make_wired_loop(tmp_path, monkeypatch)
    key = "webui:777"
    _seed_session(loop.sessions, key)

    assert loop.sessions.delete_session(key) is True
    await asyncio.gather(*loop._memory_extraction_tasks)

    expected = ScratchpadWriter.user_id_for_key(key)
    assert expected == "777"
    assert _UserIdCapturingExtractor.built[-1] == (expected, "default")


def test_quick_facts_hook_extractor_user_id_derived_from_session_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """调用点 3：Quick Facts 回调在收到 Session 后才建 extractor，user_id 取自其 key。"""
    loop = _make_wired_loop(tmp_path, monkeypatch)
    key = "webui:24680"
    session = _seed_session(loop.sessions, key)

    hook = loop.auto_compact._quick_facts_hook
    assert hook is not None
    assert hook(session) == 0

    expected = ScratchpadWriter.user_id_for_key(key)
    assert expected == "24680"
    assert _UserIdCapturingExtractor.built[-1] == (expected, "default")


async def test_deletion_extraction_failure_is_contained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提取任务失败不得冒泡；删除调用方无感。"""

    class _FailingExtractor(_RecordingExtractor):
        async def extract_session(self, session: Session, *, source: str = "session_end") -> Any:
            raise RuntimeError("LLM down")

    monkeypatch.setattr("nanobot.memory.extractor.MemoryExtractor", _FailingExtractor)

    services = MemoryServices.for_workspace("default", tmp_path)
    loop = _make_loop(
        tmp_path,
        memory_extraction_enabled=True,
        memory_services=services,
    )
    _seed_session(loop.sessions, "websocket:failing")

    assert loop.sessions.delete_session("websocket:failing") is True
    tasks = list(loop._memory_extraction_tasks)
    assert len(tasks) == 1
    await asyncio.gather(*tasks)  # 异常被任务内部吞掉，不在此重抛
    assert all(task.done() and task.exception() is None for task in tasks)
