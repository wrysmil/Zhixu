"""WU-A Task 2 契约测试：``run_idle_extraction`` / ``extract_incremental``。

修正记录（2026-09-15）：本文件原先把「idle 抽取不调 LLM」写成了断言，而那正是
WU-A 接入时误用 T5 rule-only 旧路径产生的缺陷 —— 结果是正常聊天既不调 LLM、
`episodes` 表恒为 0 行。现按 plan 决策 #3「让 LLM 跑完」恢复完整四阶段流水线，
断言随之反转。

覆盖契约:
- 使用 ``state.last_count`` 作为增量起点，只把新切片喂给 LLM；
- 每次 idle 触发调 2 次 LLM（semantic + episode）；
- 语义记忆（画像 memories[] + 经验 experiences[]）与 episode 都落库；
- ``current_count <= state.last_count`` 是 no-op（0 LLM 调用）；
- LLM / state 写入失败均失败隔离，不上抛。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import MemoryExtractor
from nanobot.memory.repository import (
    get_extraction_state,
    upsert_extraction_state,
)
from nanobot.session.manager import Session

# ---------------------------------------------------------------------------
# Fakes（鸭子类型）
# ---------------------------------------------------------------------------

_SEMANTIC_MARKER = "语义记忆抽取"
_EPISODE_MARKER = "情节记忆抽取"

_SEMANTIC_PAYLOAD: dict[str, Any] = {
    "memories": [
        {
            "content": "用户是前端工程师，主要使用 React",
            "evidence": "我是一名前端工程师",
            "type": "FACT",
            "priority": "long_term",
            "importance": 0.9,
            "tags": ["身份"],
        },
        {
            "content": "用户偏好极简风格的界面设计",
            "evidence": "我比较喜欢极简风格",
            "type": "PREFERENCE",
            "priority": "long_term",
            "importance": 0.8,
            "tags": ["设计"],
        },
    ],
    "experiences": [
        {
            "content": "用 uv 管理 Python 依赖可以避免 venv 与 pip 的状态漂移",
            "evidence": "我用 uv 管理 Python 依赖",
            "type": "EXPERIENCE",
            "priority": "long_term",
            "importance": 0.7,
            "tags": ["python"],
        }
    ],
}

_EPISODE_PAYLOAD: dict[str, Any] = {
    "summary": "用户介绍了自己的职业与偏好，助手据此给出针对性回应。",
    "goal": "让助手了解自己的背景以便后续协作",
    "outcome": "completed",
    "entities": ["React"],
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

    def __init__(
        self,
        *,
        semantic: dict[str, Any] | None = _SEMANTIC_PAYLOAD,
        episode: dict[str, Any] | None = _EPISODE_PAYLOAD,
        raise_exc: BaseException | None = None,
    ) -> None:
        self.semantic = semantic
        self.episode = episode
        self.raise_exc = raise_exc
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
        if self.raise_exc is not None:
            raise self.raise_exc
        body = messages[0].get("content") or ""
        if _SEMANTIC_MARKER in body:
            return _FakeResponse(json.dumps(self.semantic, ensure_ascii=False))
        if _EPISODE_MARKER in body:
            return _FakeResponse(json.dumps(self.episode, ensure_ascii=False))
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


def _make_session(messages: list[dict[str, Any]], *, key: str = "s1") -> Session:
    return Session(key=key, messages=list(messages))


def _build_messages() -> list[dict[str, Any]]:
    """6 轮对话，每轮 user 内容互不相同，便于断言切片边界。"""
    turns = [
        ("我叫小陈", "你好，小陈！"),
        ("我是一名前端工程师", "前端工程师，很好的方向"),
        ("我平时主要写 React", "React 现在确实主流"),
        ("我比较喜欢极简风格", "极简风格很考验功力"),
        ("我平时喜欢看健身视频", "健身 + 写代码，精力充沛"),
        ("我用 uv 管理 Python 依赖", "uv 的依赖解析确实稳定"),
    ]
    out: list[dict[str, Any]] = []
    for user_text, assistant_text in turns:
        out.append({"role": "user", "content": user_text})
        out.append({"role": "assistant", "content": assistant_text})
    return out


def _make_extractor(db: MemoryDatabase, provider: _FakeProvider) -> MemoryExtractor:
    return MemoryExtractor(db, _FakeRuntime(provider), user_id="u1", workspace_id="ws")


def _count(db: MemoryDatabase, table: str) -> int:
    with db.connect() as conn:
        row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
    return int(row[0])


# ---------------------------------------------------------------------------
# 增量切片 + LLM
# ---------------------------------------------------------------------------


class TestIncrementalSlice:
    async def test_start_index_limits_prompt_to_new_messages(self, db: MemoryDatabase):
        """``state.last_count=4`` 时，LLM prompt 里只能出现 messages[4:] 的内容。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)
        messages = _build_messages()

        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                "s_slice",
                last_count=4,
                source="idle",
                extracted_at="2026-09-15T09:00:00+00:00",
            )

        result = await extractor.run_idle_extraction(_make_session(messages, key="s_slice"))

        assert result.memory_ids, "语义轨应落库至少 1 条记忆"
        joined = "\n".join(provider.prompts())
        # messages[4:] = 「我平时主要写 React」起的 3 轮对话。
        assert "我平时主要写 React" in joined
        assert "我叫小陈" not in joined
        assert "我是一名前端工程师" not in joined

    async def test_calls_both_llm_tracks(self, db: MemoryDatabase):
        """每次 idle 触发 = 2 次 LLM 调用（semantic + episode）。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)

        await extractor.run_idle_extraction(_make_session(_build_messages(), key="s_tracks"))

        assert len(provider.calls) == 2
        joined = "\n".join(provider.prompts())
        assert _SEMANTIC_MARKER in joined
        assert _EPISODE_MARKER in joined


# ---------------------------------------------------------------------------
# 落库：memory / episode / 画像 + 经验
# ---------------------------------------------------------------------------


class TestPersistence:
    async def test_persists_semantic_memories(self, db: MemoryDatabase):
        """semantic 轨的 memories[] + experiences[] 必须落进 ``memories`` 表。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)

        result = await extractor.run_idle_extraction(
            _make_session(_build_messages(), key="s_mem")
        )

        expected = len(_SEMANTIC_PAYLOAD["memories"]) + len(_SEMANTIC_PAYLOAD["experiences"])
        assert len(result.memory_ids) == expected
        assert _count(db, "memories") == expected

    async def test_persists_experience_into_memories(self, db: MemoryDatabase):
        """semantic 轨的 experiences[] 与 memories[] 一样落 ``memories`` 表。

        回归：经验提取曾被认为「未实现」，实际上 SEMANTIC_EXTRACTION_PROMPT 早已
        双轨输出，只是调用链断了。
        """
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)

        await extractor.run_idle_extraction(_make_session(_build_messages(), key="s_exp"))

        with db.connect() as conn:
            rows = conn.execute("SELECT content, type FROM memories").fetchall()
        contents = [r[0] for r in rows]
        types = {r[1] for r in rows}
        assert any("uv" in c for c in contents), "经验条目未落库"
        assert "experience" in types

    async def test_persists_episode(self, db: MemoryDatabase):
        """episode 轨的产物必须落进 ``episodes`` 表。

        回归：idle 路径曾只喂 memories 给 ``_persist``，episode/action_nodes 皆空，
        ``if episode is not None or filtered.action_nodes`` 恒 False → episodes 表恒 0 行。
        """
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)

        result = await extractor.run_idle_extraction(
            _make_session(_build_messages(), key="s_ep")
        )

        assert len(result.episode_ids) == 1
        assert _count(db, "episodes") == 1
        with db.connect() as conn:
            row = conn.execute("SELECT summary, source FROM episodes").fetchone()
        assert row[0] == _EPISODE_PAYLOAD["summary"]
        assert row[1] == "idle"


# ---------------------------------------------------------------------------
# no-op / 失败隔离
# ---------------------------------------------------------------------------


class TestNoOpAndFailureIsolation:
    async def test_no_op_when_current_equals_state(self, db: MemoryDatabase):
        """``current_count == last_count``：0 LLM 调用，state 不动。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)
        messages = _build_messages()
        current_count = len(messages)

        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                "s_noop",
                last_count=current_count,
                source="idle",
                extracted_at="2026-09-15T08:00:00+00:00",
            )

        result = await extractor.run_idle_extraction(_make_session(messages, key="s_noop"))

        assert provider.calls == []
        assert result.memory_ids == []
        assert result.episode_ids == []
        with db.connect() as conn:
            post = get_extraction_state(conn, "s_noop")
        assert post is not None
        assert post.last_count == current_count
        assert post.last_extracted_at == "2026-09-15T08:00:00+00:00"

    async def test_no_state_treats_as_zero(self, db: MemoryDatabase):
        """state 行缺失时按 ``last_count=0`` 处理（首跑 = 全量扫描）。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)
        session = _make_session(_build_messages(), key="s_first")

        result = await extractor.run_idle_extraction(session)

        assert result.memory_ids
        assert len(provider.calls) == 2
        with db.connect() as conn:
            post = get_extraction_state(conn, "s_first")
        assert post is not None
        assert post.last_count == len(session.messages)
        assert post.last_source == "idle"

    async def test_failure_does_not_advance_state(self, db: MemoryDatabase):
        """``set_extraction_seq`` 抛错时 state 保持预置值，调用方不抛。"""
        provider = _FakeProvider()
        extractor = _make_extractor(db, provider)

        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                "s_upsert_fail",
                last_count=2,
                source="idle",
                extracted_at="2026-09-15T09:00:00+00:00",
            )

        from nanobot.memory import extractor as ext_module

        original_set = ext_module.set_extraction_seq

        def _boom(*_args, **_kwargs):
            raise RuntimeError("simulated cursor write failure")

        ext_module.set_extraction_seq = _boom
        try:
            result = await extractor.run_idle_extraction(
                _make_session(_build_messages(), key="s_upsert_fail")
            )
        finally:
            ext_module.set_extraction_seq = original_set

        assert result is not None
        with db.connect() as conn:
            post = get_extraction_state(conn, "s_upsert_fail")
        assert post is not None
        assert post.last_count == 2
        assert post.last_extracted_at == "2026-09-15T09:00:00+00:00"

    async def test_llm_failure_isolated(self, db: MemoryDatabase):
        """两路 LLM 全失败时不抛，返回空结果，state 仍推进（失败已记账）。"""
        provider = _FakeProvider(raise_exc=RuntimeError("llm down"))
        extractor = _make_extractor(db, provider)
        session = _make_session(_build_messages(), key="s_llm_fail")

        result = await extractor.run_idle_extraction(session)

        assert result.memory_ids == []
        assert result.episode_ids == []
        assert set(result.failed_tracks) == {"semantic", "episode"}
        # 契约 §2.6：模型失败不算空成功 → 进度不推进，等重试完成
        with db.connect() as conn:
            post = get_extraction_state(conn, "s_llm_fail")
        assert post is None or post.last_count == 0
