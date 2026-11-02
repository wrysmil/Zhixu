"""``MemoryExtractionHook._compute_incremental_start_index`` 的窗口推导测试。

原 T5 话题切换检测（``TestTopicChangeIncrementalExtraction``）已随功能移除；
``_compute_incremental_start_index`` 作为增量抽取窗口工具方法保留，
对应单元测试保留。
"""

from __future__ import annotations

from typing import Any

from nanobot.agent.hooks.memory_extraction import MemoryExtractionHook


class _FakeExtractor:
    """默认构造；``_compute_incremental_start_index`` 不依赖具体实现。"""

    async def extract_session(self, session: Any, *, source: str = "session_end") -> None:
        return None

    async def extract_incremental(self, session: Any, last_extracted_index: int) -> None:
        return None

    async def run_idle_extraction(self, session: Any) -> None:
        return None


class _FakeScratchpadWriter:
    async def update_focus(self, new_focus: str) -> None:
        return None


def _make_hook() -> MemoryExtractionHook:
    return MemoryExtractionHook(
        _FakeExtractor(),
        "s1",
        _FakeScratchpadWriter(),
    )


class TestComputeIncrementalStartIndex:
    def test_returns_after_second_last_user_message(self):
        hook = _make_hook()
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "second"},
            {"role": "tool", "content": "tool output"},
            {"role": "user", "content": "third"},
            {"role": "assistant", "content": "done"},
        ]

        assert hook._compute_incremental_start_index(messages) == 3

    def test_single_user_message_returns_zero(self):
        hook = _make_hook()
        messages = [{"role": "user", "content": "only"}]

        assert hook._compute_incremental_start_index(messages) == 0

    def test_empty_user_message_is_skipped(self):
        hook = _make_hook()
        messages = [
            {"role": "user", "content": "first"},
            {"role": "user", "content": ""},
            {"role": "user", "content": "third"},
        ]

        assert hook._compute_incremental_start_index(messages) == 1

    def test_no_user_message_returns_zero(self):
        hook = _make_hook()
        messages = [{"role": "assistant", "content": "no user here"}]

        assert hook._compute_incremental_start_index(messages) == 0
