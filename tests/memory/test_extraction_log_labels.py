"""记忆抽取日志必须带「会话摘要」（2026-09-15 spec §4）。

摘要口径 = WebUI 侧边栏会话列表那一行：``metadata["title"]`` 优先，
为空回退首条用户消息（见 ``nanobot.session.labels``）。

背景：`Processing message from websocket:anon-…` 打的是 sender_id，
两个会话在日志里长得一模一样——本次排障就因此误判成「同一段对话」。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from nanobot.agent.hook import AgentRunHookContext
from nanobot.agent.hooks.memory_extraction import (
    _PENDING_IDLE_TIMERS,
    MemoryExtractionHook,
)
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import MemoryExtractor
from nanobot.session.manager import Session

_TITLE = "初次问候与助手介绍"
_FIRST_MESSAGE = "你好，我的名字叫做黄启华"


class _FakeExtractionResult:
    memory_ids: list[str] = []
    episode_ids: list[str] = []
    skipped = 0
    failed_tracks: list[str] = []


class _FakeExtractor:
    def __init__(self) -> None:
        self.idle_calls: list[Any] = []

    async def run_idle_extraction(self, session: Any) -> Any:
        self.idle_calls.append(session)
        return _FakeExtractionResult()


class _FakeScratchpadWriter:
    async def update_focus(self, new_focus: str) -> None:
        return None

    async def archive_completed(self, resolved_items: list[str]) -> None:
        return None


class _EmptyResponse:
    content = ""
    finish_reason = "stop"


class _EmptyProvider:
    async def chat_with_retry(self, **_kwargs: Any) -> Any:
        return _EmptyResponse()


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 512
    reasoning_effort = None


class _FakeRuntime:
    def __init__(self) -> None:
        self.provider = _EmptyProvider()
        self.model = "fake-model"
        self.generation = _FakeGeneration()
        self.context_window_tokens = 8192


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    for task in list(_PENDING_IDLE_TIMERS.values()):
        if not task.done():
            task.cancel()
    _PENDING_IDLE_TIMERS.clear()


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


def _capture(records: list[str], level: str = "INFO") -> int:
    return logger.add(records.append, level=level, format="{message}")


# ---------------------------------------------------------------------------
# hook 侧：idle timer fired / idle memory extraction finished
# ---------------------------------------------------------------------------


class TestHookLogsCarryLabel:
    async def test_idle_timer_fired_logs_label(self):
        records: list[str] = []
        hook = MemoryExtractionHook(
            _FakeExtractor(),
            "websocket:a589b74f",
            _FakeScratchpadWriter(),
            runtime=_FakeRuntime(),
            label=_FIRST_MESSAGE,
            idle_seconds=0.05,
        )
        sink = _capture(records)
        try:
            await hook.after_run(AgentRunHookContext(messages=[{"role": "user", "content": _FIRST_MESSAGE}]))
            for _ in range(50):
                if any("idle timer fired" in r for r in records):
                    break
                await asyncio.sleep(0.01)
        finally:
            logger.remove(sink)
        fired = [r for r in records if "idle timer fired" in r]
        assert fired, records
        assert f"[摘要: {_FIRST_MESSAGE}]" in fired[0]

    async def test_idle_finished_logs_label(self):
        records: list[str] = []
        hook = MemoryExtractionHook(
            _FakeExtractor(),
            "websocket:a589b74f",
            _FakeScratchpadWriter(),
            runtime=_FakeRuntime(),
            label=_TITLE,
            idle_seconds=0.05,
        )
        sink = _capture(records)
        try:
            await hook.after_run(AgentRunHookContext(messages=[{"role": "user", "content": "hi"}]))
            for _ in range(50):
                if any("idle memory extraction finished" in r for r in records):
                    break
                await asyncio.sleep(0.01)
        finally:
            logger.remove(sink)
        finished = [r for r in records if "idle memory extraction finished" in r]
        assert finished, records
        assert f"[摘要: {_TITLE}]" in finished[0]

    async def test_empty_label_omits_bracket(self):
        records: list[str] = []
        hook = MemoryExtractionHook(
            _FakeExtractor(),
            "s-empty",
            _FakeScratchpadWriter(),
            runtime=_FakeRuntime(),
            idle_seconds=0.05,
        )
        sink = _capture(records)
        try:
            await hook.after_run(AgentRunHookContext(messages=[{"role": "user", "content": "hi"}]))
            for _ in range(50):
                if any("idle timer fired" in r for r in records):
                    break
                await asyncio.sleep(0.01)
        finally:
            logger.remove(sink)
        fired = [r for r in records if "idle timer fired" in r]
        assert fired, records
        assert "摘要" not in fired[0]


# ---------------------------------------------------------------------------
# extractor 侧：idle extraction start / 结果行
# ---------------------------------------------------------------------------


class TestExtractorLogsCarryLabel:
    async def test_idle_extraction_start_logs_title(self, db):
        records: list[str] = []
        session = Session(
            key="websocket:a589b74f",
            messages=[{"role": "user", "content": _FIRST_MESSAGE}],
            metadata={"title": _TITLE},
        )
        extractor = MemoryExtractor(db, _FakeRuntime())
        sink = _capture(records)
        try:
            await extractor.run_idle_extraction(session)
        finally:
            logger.remove(sink)
        start = [r for r in records if "idle extraction start" in r]
        assert start, records
        assert f"[摘要: {_TITLE}]" in start[0]

    async def test_idle_extraction_start_falls_back_to_first_message(self, db):
        records: list[str] = []
        session = Session(
            key="websocket:a589b74f",
            messages=[{"role": "user", "content": _FIRST_MESSAGE}],
            metadata={},
        )
        extractor = MemoryExtractor(db, _FakeRuntime())
        sink = _capture(records)
        try:
            await extractor.run_idle_extraction(session)
        finally:
            logger.remove(sink)
        start = [r for r in records if "idle extraction start" in r]
        assert start, records
        assert f"[摘要: {_FIRST_MESSAGE}]" in start[0]
