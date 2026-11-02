"""idle 抽取的信号门槛契约测试。

背景：idle 定时器是正常聊天**唯一**的提取入口，但修复前只判断「有没有新消息」
（``current_count <= last_count``），不判条数与内容 —— 一句「你好」加助手回复
（2 条消息）也会触发两路 LLM 提取（semantic + episode）。

覆盖契约:
- 条数下限 2：只有一条尚未得到回复的 user 消息 → 0 LLM 调用，state 不推进；
- L2：切片只有一条 user 消息、内容 < 10 字符且无工具调用 → 0 LLM 调用；
- L2 边界：user 内容**恰好 10 字符**放行（``<`` 的另一侧）；
- L2 豁免：切片内有非空 ``tool_calls`` → 放行；``tool_calls: []`` **不**豁免；
- 「一轮实质对话」（user + assistant 共 2 条、user 内容 >= 10 字符）→ 放行；
- 正常 2 轮对话（4 条）→ 放行，2 次 LLM；
- 跳过**不推进** state，被跳过的消息由后续真实对话的切片一并抽取；
- content 为 content-block 列表 / ``None`` 时不抛 ``AttributeError``。

条数下限取 2（一轮 user + assistant 即完整切片），理由见
``MemoryExtractor.IDLE_MIN_NEW_MESSAGES`` 注释。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import MemoryExtractor
from nanobot.memory.repository import get_extraction_state, upsert_extraction_state
from nanobot.session.manager import Session

# ---------------------------------------------------------------------------
# Fakes（与 test_extractor_incremental.py 同形）
# ---------------------------------------------------------------------------

_SEMANTIC_MARKER = "语义记忆抽取"
_EPISODE_MARKER = "情节记忆抽取"

_SEMANTIC_PAYLOAD: dict[str, Any] = {
    "memories": [
        {
            "content": "用户是前端工程师",
            "type": "FACT",
            "priority": "long_term",
            "importance": 0.9,
            "tags": ["身份"],
        }
    ],
    "experiences": [],
}

_EPISODE_PAYLOAD: dict[str, Any] = {
    "summary": "用户与助手寒暄并介绍背景。",
    "goal": "了解用户",
    "outcome": "completed",
    "entities": [],
    "tools_used": [],
}


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 512
    reasoning_effort = None


class _FakeProvider:
    """按 prompt 内容分辨 semantic / episode 轨，返回对应 JSON。"""

    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    def prompts(self) -> list[str]:
        return [
            msg["content"]
            for call in self.calls
            for msg in call
            if msg.get("role") == "user"
        ]

    async def chat_with_retry(
        self, *, messages: list[dict[str, Any]], **_kwargs: Any
    ) -> _FakeResponse:
        self.calls.append(messages)
        body = messages[0].get("content") or ""
        if _SEMANTIC_MARKER in body:
            return _FakeResponse(json.dumps(_SEMANTIC_PAYLOAD, ensure_ascii=False))
        if _EPISODE_MARKER in body:
            return _FakeResponse(json.dumps(_EPISODE_PAYLOAD, ensure_ascii=False))
        return _FakeResponse(None)


class _FakeRuntime:
    def __init__(self, provider: _FakeProvider) -> None:
        self.provider = provider
        self.model = "fake-model"
        self.generation = _FakeGeneration()


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


def _make_extractor(db: MemoryDatabase, provider: _FakeProvider) -> MemoryExtractor:
    return MemoryExtractor(db, _FakeRuntime(provider), user_id="u1", workspace_id="ws")


def _session(messages: list[dict[str, Any]], *, key: str) -> Session:
    return Session(key=key, messages=list(messages))


def _state(db: MemoryDatabase, key: str) -> Any:
    with db.connect() as conn:
        return get_extraction_state(conn, key)


def _user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def _assistant(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


# ---------------------------------------------------------------------------
# 条数下限
# ---------------------------------------------------------------------------


class TestSliceCountFloor:
    async def test_single_unanswered_user_message_skipped(self, db: MemoryDatabase):
        """只有一条 user 消息（尚无回复）→ 0 LLM 调用，state 不推进。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)
        session = _session([_user("我以后都用 uv 管理依赖")], key="s_lone")

        result = await extractor.run_idle_extraction(session)

        assert provider.calls == []
        assert result.memory_ids == []
        assert _state(db, "s_lone") is None

    async def test_substantive_two_message_slice_passes(self, db: MemoryDatabase):
        """一轮实质对话（2 条、user 内容 >= 10 字符）→ 放行，2 次 LLM。

        这是条数下限取 2 而非 3 的直接理由：照搬 L1=3 会让这段内容永不入库。
        """
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)
        session = _session(
            [
                _user("我以后都用 uv 管理依赖"),
                _assistant("好的，已记住"),
            ],
            key="s_pair",
        )

        result = await extractor.run_idle_extraction(session)

        assert len(provider.calls) == 2
        assert result.memory_ids
        post = _state(db, "s_pair")
        assert post is not None
        assert post.last_count == 2


# ---------------------------------------------------------------------------
# L2：单条短 user 消息门槛
# ---------------------------------------------------------------------------


class TestL2ShortSingleTurnGate:
    async def test_greeting_only_triggers_no_llm(self, db: MemoryDatabase):
        """「你好」+ 助手回复 → 2 条且 user 内容仅 2 字符 → 0 LLM，state 不推进。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)
        session = _session(
            [_user("你好"), _assistant("你好！有什么可以帮你的？")],
            key="s_greet",
        )

        result = await extractor.run_idle_extraction(session)

        assert provider.calls == []
        assert result.memory_ids == []
        assert result.episode_ids == []
        assert _state(db, "s_greet") is None

    async def test_three_messages_with_short_single_turn_skipped(
        self, db: MemoryDatabase
    ):
        """3 条消息但只有一条短 user 消息且无工具调用 → 0 LLM 调用。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)
        session = _session(
            [
                _user("在吗"),
                _assistant("在的"),
                _assistant("有什么需要？"),
            ],
            key="s_short",
        )

        result = await extractor.run_idle_extraction(session)

        assert provider.calls == []
        assert result.memory_ids == []
        assert _state(db, "s_short") is None

    async def test_exactly_ten_chars_passes(self, db: MemoryDatabase):
        """user 内容**恰好 10 字符**放行 —— 边界 ``< 10`` 的另一侧。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)
        content = "一二三四五六七八九十"
        assert len(content) == 10
        session = _session([_user(content), _assistant("收到")], key="s_exact10")

        result = await extractor.run_idle_extraction(session)

        assert len(provider.calls) == 2
        assert result.memory_ids

    async def test_tool_call_exempts_l2(self, db: MemoryDatabase):
        """短 user 消息但切片内有非空 tool_calls → L2 豁免，正常提取。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)
        session = _session(
            [
                _user("跑一下"),
                {"role": "assistant", "content": None, "tool_calls": [{"id": "t1"}]},
                {"role": "tool", "content": "ok", "tool_call_id": "t1"},
            ],
            key="s_tool",
        )

        result = await extractor.run_idle_extraction(session)

        assert len(provider.calls) == 2
        assert result.memory_ids

    async def test_empty_tool_calls_does_not_exempt(self, db: MemoryDatabase):
        """``tool_calls: []`` 是 falsy → **不**豁免，短消息仍被拦。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)
        session = _session(
            [
                _user("在吗"),
                {"role": "assistant", "content": None, "tool_calls": []},
                _assistant("有什么需要？"),
            ],
            key="s_empty_tools",
        )

        result = await extractor.run_idle_extraction(session)

        assert provider.calls == []
        assert result.memory_ids == []
        assert _state(db, "s_empty_tools") is None

    async def test_none_content_single_user_turn_skipped(self, db: MemoryDatabase):
        """单条 user 消息 content 为 ``None`` → 归一化为空串 → 被拦，且不抛异常。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)
        session = _session(
            [
                {"role": "user", "content": None},
                _assistant("?"),
                _assistant("有什么需要？"),
            ],
            key="s_none",
        )

        result = await extractor.run_idle_extraction(session)

        assert provider.calls == []
        assert result.memory_ids == []

    async def test_content_block_list_does_not_raise(self, db: MemoryDatabase):
        """content 为 content-block 列表时归一化后判定，不抛 AttributeError。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)
        session = _session(
            [
                {"role": "user", "content": [{"type": "text", "text": "嗨"}]},
                _assistant("嗨"),
                _assistant("有什么需要？"),
            ],
            key="s_blocks",
        )

        result = await extractor.run_idle_extraction(session)

        assert provider.calls == []
        assert result.memory_ids == []


# ---------------------------------------------------------------------------
# 放行路径
# ---------------------------------------------------------------------------


class TestPassesGate:
    async def test_two_full_turns_pass(self, db: MemoryDatabase):
        """正常 2 轮对话（4 条、2 条 user）→ 放行，2 次 LLM。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)
        session = _session(
            [
                _user("我平时主要用 React 写前端"),
                _assistant("React 很主流"),
                _user("顺便问下 hooks 的依赖数组"),
                _assistant("依赖数组要写全"),
            ],
            key="s_two_turns",
        )

        result = await extractor.run_idle_extraction(session)

        assert len(provider.calls) == 2
        assert result.memory_ids
        assert _state(db, "s_two_turns") is not None


# ---------------------------------------------------------------------------
# 跳过语义：不推进 state，被跳过的消息由后续切片一并抽取
# ---------------------------------------------------------------------------


class TestSkipPreservesState:
    async def test_skipped_slice_folded_into_next_extraction(self, db: MemoryDatabase):
        """跳过不动 state → 后续真实对话的切片把被跳过的短消息一起抽走。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)
        key = "s_fold"

        greet = _session([_user("你好"), _assistant("你好！")], key=key)
        assert (await extractor.run_idle_extraction(greet)).memory_ids == []
        assert provider.calls == []
        assert _state(db, key) is None

        real = _session(
            [
                _user("你好"),
                _assistant("你好！"),
                _user("我是一名前端工程师，主要写 React"),
                _assistant("记下了"),
            ],
            key=key,
        )
        result = await extractor.run_idle_extraction(real)

        assert len(provider.calls) == 2
        assert result.memory_ids
        joined = "\n".join(provider.prompts())
        assert "你好" in joined, "被跳过的短消息应随新切片一并喂给 LLM"
        assert "我是一名前端工程师" in joined
        post = _state(db, key)
        assert post is not None
        assert post.last_count == len(real.messages)

    async def test_gate_does_not_clobber_existing_state(self, db: MemoryDatabase):
        """已有 state 时被门槛跳过，游标保持原值（不前进也不回退）。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)
        key = "s_keep"

        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                key,
                last_count=20,
                source="idle",
                extracted_at="2026-09-17T08:00:00+00:00",
            )

        prefix: list[dict[str, Any]] = []
        for _ in range(10):
            prefix.append(_user("谢谢"))
            prefix.append(_assistant("不客气"))
        session = _session(prefix + [_user("嗯"), _assistant("嗯嗯")], key=key)
        assert len(session.messages) == 22

        result = await extractor.run_idle_extraction(session)

        assert provider.calls == []
        assert result.memory_ids == []
        post = _state(db, key)
        assert post is not None
        assert post.last_count == 20
        assert post.last_extracted_at == "2026-09-17T08:00:00+00:00"
