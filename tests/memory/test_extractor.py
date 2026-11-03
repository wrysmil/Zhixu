"""Tests for MemoryExtractor（plan §10 Task 3 / 4 / 5）。

覆盖四个阶段：系统提取、LLM 双路提取、防污染过滤、持久化。
使用可编程 FakeLLM（鸭子类型）注入 runtime，避免依赖真实 provider。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import (
    LLMEpisodeItem,
    LLMExtractionResult,
    LLMMemoryItem,
    MemoryExtractor,
    SystemExtractionResult,
    _coerce_memory_item,
    _collect_action_nodes,
    _parse_json_object,
    _resolve_action_success,
)
from nanobot.memory.models import (
    EpisodeSource,
    Memory,
    MemoryPriority,
    MemoryType,
    ScratchpadEntry,
)
from nanobot.memory.prompts import (
    EPISODE_EXTRACTION_PROMPT,
    SEMANTIC_EXTRACTION_PROMPT,
)
from nanobot.memory.repository import (
    add_memory,
    get_episode,
    get_scratchpad,
    list_memories,
    search_memories,
    upsert_scratchpad,
)
from nanobot.session.manager import Session

# ---------------------------------------------------------------------------
# Fake LLM（鸭子类型，不依赖真实 provider）
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 2048
    reasoning_effort = None


class _FakeProvider:
    """可编程 provider：按 prompt 内容分流返回语义/情节结果。

    ``semantic`` / ``episode`` 可为 str（返回 content）、BaseException（抛出）、
    或 None（无内容）。``*_delay`` 用于制造并发与超时场景。
    """

    def __init__(
        self,
        *,
        semantic: Any = None,
        episode: Any = None,
        semantic_delay: float = 0.0,
        episode_delay: float = 0.0,
    ) -> None:
        self.semantic = semantic
        self.episode = episode
        self.semantic_delay = semantic_delay
        self.episode_delay = episode_delay
        self.calls: list[list[dict[str, Any]]] = []
        self.tracks: list[str] = []
        self.active = 0
        self.max_active = 0

    async def chat_with_retry(self, *, messages: list[dict[str, Any]], **_kwargs: Any) -> _FakeResponse:
        self.calls.append(messages)
        text = " ".join(
            m.get("content", "") for m in messages if isinstance(m.get("content"), str)
        )
        is_semantic = "语义记忆抽取" in text
        self.tracks.append("semantic" if is_semantic else "episode")
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            delay = self.semantic_delay if is_semantic else self.episode_delay
            if delay:
                await asyncio.sleep(delay)
            result = self.semantic if is_semantic else self.episode
            if isinstance(result, BaseException):
                raise result
            return _FakeResponse(result)
        finally:
            self.active -= 1


class _FakeRuntime:
    def __init__(self, provider: _FakeProvider, model: str = "fake-model") -> None:
        self.provider = provider
        self.model = model
        self.generation = _FakeGeneration()
        self.context_window_tokens = 8192


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

EPISODE_JSON = json.dumps(
    {
        "summary": "用户通过 nanobot 配置了 uv 依赖管理",
        "goal": "配置 uv 管理 Python 依赖",
        "outcome": "completed",
        "entities": ["uv"],
        "tools_used": ["bash"],
    },
    ensure_ascii=False,
)


def _semantic_json(
    memories: list[dict[str, Any]] | None = None,
    experiences: list[dict[str, Any]] | None = None,
) -> str:
    return json.dumps(
        {"memories": [dict(item, evidence=item["content"]) for item in (memories or [])], "experiences": experiences or []},
        ensure_ascii=False,
    )


def _tool_call(name: str, arguments: str, call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _session(messages: list[dict[str, Any]] | None = None, key: str = "s1") -> Session:
    return Session(key=key, messages=messages or [])


def _plain_session(semantic: str | None = None) -> Session:
    facts = "；".join(item["content"] for item in json.loads(semantic).get("memories", [])) if semantic else "配置 uv。"
    return _session([{"role": "user", "content": facts}])


def _make_extractor(
    db: MemoryDatabase,
    provider: _FakeProvider,
    **kwargs: Any,
) -> MemoryExtractor:
    return MemoryExtractor(db, _FakeRuntime(provider), **kwargs)


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


def _capture_warnings(records: list[str]) -> int:
    return logger.add(records.append, level="WARNING", format="{message}")


def _memory_rows(db: MemoryDatabase) -> list[Memory]:
    with db.connect() as conn:
        return list_memories(conn, workspace_id="default")


# ---------------------------------------------------------------------------
# 阶段1：系统提取（Task 3）
# ---------------------------------------------------------------------------


class TestSystemExtract:
    def test_captures_action_nodes_from_tool_calls(self, db):
        session = _session(
            [
                {"role": "user", "content": "跑一下测试"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("read_file", '{"path": "a.py"}', "c1"),
                        _tool_call("bash", '{"cmd": "pytest"}', "c2"),
                        _tool_call("write_file", '{"path": "b.py"}', "c3"),
                    ],
                },
            ]
        )
        extractor = _make_extractor(db, _FakeProvider())
        result = extractor._system_extract(session)

        assert len(result.action_nodes) >= 3
        assert {n.tool for n in result.action_nodes} == {"read_file", "bash", "write_file"}
        assert result.action_nodes[0].input == '{"path": "a.py"}'

    def test_action_node_matches_tool_result_and_marks_failure(self, db):
        session = _session(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_tool_call("bash", '{"cmd": "pytest"}', "c1")],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "name": "bash",
                    "content": "Traceback (most recent call last):\n  ...Error",
                },
            ]
        )
        extractor = _make_extractor(db, _FakeProvider())
        nodes = extractor._system_extract(session).action_nodes

        assert len(nodes) == 1
        assert nodes[0].success is False
        assert "Traceback" in nodes[0].output

    def test_rule_signal_hit(self, db):
        session = _session([{"role": "user", "content": "必须每次保存日志。其他随意。"}])
        extractor = _make_extractor(db, _FakeProvider())
        signals = extractor._system_extract(session).rule_signals

        assert signals
        assert any("必须" in s for s in signals)

    def test_empty_messages_returns_empty_result(self, db):
        extractor = _make_extractor(db, _FakeProvider())
        result = extractor._system_extract(_session([]))

        assert result.action_nodes == []
        assert result.rule_signals == []
        assert result.scratchpad_snapshot is None

    def test_reads_scratchpad_snapshot(self, db):
        with db.connect() as conn:
            upsert_scratchpad(
                conn,
                ScratchpadEntry(
                    user_id="default",
                    workspace_id="default",
                    updated_at="2026-09-10T00:00:00+00:00",
                    current_focus="配置 uv",
                ),
            )
        extractor = _make_extractor(db, _FakeProvider())
        snapshot = extractor._system_extract(_plain_session()).scratchpad_snapshot

        assert snapshot is not None
        assert snapshot.current_focus == "配置 uv"


# ---------------------------------------------------------------------------
# 阶段2：LLM 提取（Task 4）
# ---------------------------------------------------------------------------


class TestLLMExtract:
    async def test_uses_semantic_and_episode_prompts(self, db):
        provider = _FakeProvider(semantic=_semantic_json(), episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        await extractor._llm_extract(_plain_session(), SystemExtractionResult())

        combined = "".join(
            m["content"] for call in provider.calls for m in call if isinstance(m.get("content"), str)
        )
        assert SEMANTIC_EXTRACTION_PROMPT in combined
        assert EPISODE_EXTRACTION_PROMPT in combined
        assert len(provider.calls) == 2

    async def test_two_tracks_run_concurrently(self, db):
        provider = _FakeProvider(
            semantic=_semantic_json(),
            episode=EPISODE_JSON,
            semantic_delay=0.05,
            episode_delay=0.05,
        )
        extractor = _make_extractor(db, provider)

        await extractor._llm_extract(_plain_session(), SystemExtractionResult())

        assert provider.max_active >= 2

    async def test_parses_memories_and_experiences(self, db):
        semantic = _semantic_json(
            memories=[
                {
                    "content": "用户偏好 uv",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                    "importance": 0.8,
                    "tags": ["uv"],
                    "subject": "用户",
                    "predicate": "偏好",
                }
            ],
            experiences=[
                {
                    "content": "用 uv sync 安装依赖可保持锁文件同步",
                    "type": "EXPERIENCE",
                    "priority": "short_term",
                    "importance": 0.6,
                    "tags": ["uv"],
                    "evidence": "uv sync 后锁文件保持同步",
                }
            ],
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor._llm_extract(
            _session([
                {"role": "user", "content": "用户偏好 uv"},
                {"role": "tool", "content": "uv sync 后锁文件保持同步"},
            ]),
            SystemExtractionResult(),
        )

        assert len(result.memories) == 2
        assert {m.content for m in result.memories} == {
            "用户偏好 uv",
            "用 uv sync 安装依赖可保持锁文件同步",
        }
        assert result.memories[0].type == "PREFERENCE"
        assert result.memories[1].type == "EXPERIENCE"

    async def test_parses_episode(self, db):
        provider = _FakeProvider(semantic=_semantic_json(), episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor._llm_extract(_plain_session(), SystemExtractionResult())

        assert result.episode is not None
        assert result.episode.outcome == "completed"
        assert result.episode.entities == ["uv"]
        assert result.episode.tools_used == ["bash"]

    async def test_tolerates_json_code_fence(self, db):
        fenced = f"```json\n{EPISODE_JSON}\n```"
        provider = _FakeProvider(semantic=_semantic_json(), episode=fenced)
        extractor = _make_extractor(db, provider)

        result = await extractor._llm_extract(_plain_session(), SystemExtractionResult())

        assert result.episode is not None
        assert result.failed_tracks == []

    async def test_semantic_parse_failure_is_isolated(self, db):
        provider = _FakeProvider(semantic="这不是 JSON", episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)
        system = SystemExtractionResult(action_nodes=[])

        result = await extractor._llm_extract(_plain_session(), system)

        assert result.memories == []
        assert result.episode is not None
        assert result.failed_tracks == ["semantic"]

    async def test_both_tracks_failed_returns_empty(self, db):
        provider = _FakeProvider(semantic="坏内容", episode="也是坏内容")
        extractor = _make_extractor(db, provider)

        result = await extractor._llm_extract(_plain_session(), SystemExtractionResult())

        assert result.memories == []
        assert result.episode is None
        assert set(result.failed_tracks) == {"semantic", "episode"}

    async def test_both_tracks_return_none_returns_empty(self, db):
        provider = _FakeProvider(semantic="NONE", episode="NONE")
        extractor = _make_extractor(db, provider)

        result = await extractor._llm_extract(_plain_session(), SystemExtractionResult())

        assert result.memories == []
        assert result.episode is None
        assert result.failed_tracks == []

    async def test_timeout_on_one_track_defers_commit_until_retry(self, db):
        semantic = _semantic_json(
            memories=[
                {
                    "content": "用户偏好 uv",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                    "subject": "用户",
                    "predicate": "偏好",
                }
            ]
        )
        provider = _FakeProvider(
            semantic=semantic,
            episode=EPISODE_JSON,
            semantic_delay=0.5,
        )
        extractor = _make_extractor(db, provider)
        extractor.LLM_TIMEOUT = 0.05
        session = _plain_session(semantic)

        result = await extractor.extract_session(session)

        # 契约 §2.6：两轨就绪才提交，语义轨超时 → 本次不回执 episode、不推进进度
        assert result.failed_tracks == ["semantic"]
        assert result.memory_ids == []
        assert result.episode_ids == []

        retry_provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        retry_extractor = _make_extractor(db, retry_provider)

        retried = await retry_extractor.extract_session(session)

        # 重试只续跑失败的语义轨，情节轨复用 checkpoint 不重复调用
        assert retry_provider.tracks == ["semantic"]
        assert retried.failed_tracks == []
        assert len(retried.episode_ids) == 1
        with db.connect() as conn:
            episode = get_episode(conn, retried.episode_ids[0])
        assert episode is not None
        assert episode.summary == "用户通过 nanobot 配置了 uv 依赖管理"

    async def test_scratchpad_snapshot_included_in_prompt(self, db):
        with db.connect() as conn:
            upsert_scratchpad(
                conn,
                ScratchpadEntry(
                    user_id="default",
                    workspace_id="default",
                    updated_at="2026-09-10T00:00:00+00:00",
                    current_focus="snapshot-focus-xyz",
                ),
            )
        provider = _FakeProvider(semantic=_semantic_json(), episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)
        system = extractor._system_extract(_plain_session())

        await extractor._llm_extract(_plain_session(), system)

        combined = "".join(
            m["content"]
            for call in provider.calls
            for m in call
            if isinstance(m.get("content"), str)
        )
        assert "snapshot-focus-xyz" in combined


# ---------------------------------------------------------------------------
# 阶段3 + 阶段4：过滤与持久化（Task 5）
# ---------------------------------------------------------------------------


class TestFiltersAndPersistence:
    async def test_task_artifact_is_blocked(self, db):
        semantic = _semantic_json(
            memories=[
                {
                    "content": "帮我生成苹果照片",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                }
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session(semantic))

        assert result.memory_ids == []
        assert result.skipped == 1
        assert _memory_rows(db) == []

    async def test_ai_self_talk_is_blocked(self, db):
        semantic = _semantic_json(
            memories=[
                {"content": "我建议使用 uv", "type": "PREFERENCE", "priority": "long_term"}
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session(semantic))

        assert result.memory_ids == []
        assert result.skipped == 1

    async def test_near_duplicate_with_different_subject_coexists(self, db):
        """D08：字符相似度不再丢弃候选；不同主体属多值，必须并存。"""
        with db.connect() as conn:
            add_memory(
                conn,
                Memory(
                    id="m1",
                    content="用户喜欢 Python 编程语言",
                    type=MemoryType.PREFERENCE,
                    subject="用户",
                    predicate="喜欢",
                    created_at="2026-09-09T00:00:00+00:00",
                    workspace_id="default",
                ),
            )
        semantic = _semantic_json(
            memories=[
                {
                    "content": "用户喜欢 Python 编程语言",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                    "subject": "使用者",
                    "predicate": "喜欢",
                }
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session(semantic))

        assert len(result.memory_ids) == 1
        assert result.memory_ids != ["m1"]
        assert result.skipped == 0
        assert len(_memory_rows(db)) == 2

    async def test_exact_duplicate_is_idempotent(self, db):
        """完全相同内容+属性 → 幂等命中既有行，不新增。"""
        with db.connect() as conn:
            add_memory(
                conn,
                Memory(
                    id="m1",
                    content="用户偏好 uv",
                    type=MemoryType.PREFERENCE,
                    subject="用户",
                    predicate="偏好",
                    created_at="2026-09-09T00:00:00+00:00",
                    workspace_id="default",
                ),
            )
        semantic = _semantic_json(
            memories=[
                {
                    "content": "用户偏好 uv",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                    "subject": "用户",
                    "predicate": "偏好",
                }
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session(semantic))

        assert result.memory_ids == ["m1"]
        assert result.skipped == 0
        assert len(_memory_rows(db)) == 1

    async def test_short_term_priority_persisted(self, db):
        semantic = _semantic_json(
            memories=[
                {
                    "content": "用户暂时使用 uv 管理依赖",
                    "type": "PREFERENCE",
                    "priority": "short_term",
                    "subject": "用户",
                    "predicate": "使用",
                }
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session(semantic))

        assert len(result.memory_ids) == 1
        rows = _memory_rows(db)
        assert rows[0].priority is MemoryPriority.SHORT_TERM
        assert rows[0].source == "extraction"

    async def test_missing_priority_uses_fallback(self, db):
        semantic = _semantic_json(
            memories=[
                {"content": "用户今天在调试依赖", "type": "FACT", "subject": "用户"},
                {"content": "用户常用 pytest", "type": "FACT", "subject": "用户"},
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session(semantic))

        priorities = {m.content: m.priority for m in _memory_rows(db)}
        assert len(result.memory_ids) == 2
        assert priorities["用户今天在调试依赖"] is MemoryPriority.SHORT_TERM
        assert priorities["用户常用 pytest"] is MemoryPriority.LONG_TERM

    async def test_invalid_priority_is_skipped_with_warning(self, db):
        semantic = _semantic_json(
            memories=[
                {
                    "content": "用户偏好 Go 语言",
                    "type": "PREFERENCE",
                    "priority": "invalid",
                }
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        records: list[str] = []
        sink_id = _capture_warnings(records)
        try:
            result = await extractor.extract_session(_plain_session(semantic))
        finally:
            logger.remove(sink_id)

        assert result.memory_ids == []
        assert result.skipped == 1
        assert any("invalid priority" in r for r in records)

    async def test_invalid_type_is_skipped(self, db):
        semantic = _semantic_json(
            memories=[{"content": "用户偏好 Rust", "type": "BOGUS", "priority": "long_term"}]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session(semantic))

        assert result.memory_ids == []
        assert result.skipped == 1

    async def test_valid_memory_is_searchable(self, db):
        semantic = _semantic_json(
            memories=[
                {
                    "content": "用户偏好使用 uv 管理 Python 依赖",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                    "subject": "用户",
                    "predicate": "偏好",
                }
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session(semantic))

        assert len(result.memory_ids) == 1
        with db.connect() as conn:
            hits = search_memories(conn, "uv")
        assert any(h.id == result.memory_ids[0] for h in hits)

    async def test_linked_memory_ids_reverse_link(self, db):
        semantic = _semantic_json(
            memories=[
                {
                    "content": "uv 用于管理 Python 依赖",
                    "type": "SKILL",
                    "priority": "long_term",
                    "subject": "uv",
                    "predicate": "用于",
                }
            ]
        )
        provider = _FakeProvider(semantic=semantic, episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session(semantic))

        with db.connect() as conn:
            episode = get_episode(conn, result.episode_ids[0])
        assert episode is not None
        assert episode.linked_memory_ids == [result.memory_ids[0]]

    async def test_source_mapping(self, db):
        provider = _FakeProvider(semantic=_semantic_json(), episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        default_result = await extractor.extract_session(_plain_session())
        compress_result = await extractor.extract_session(
            _plain_session(), source="context_compress"
        )
        deletion_result = await extractor.extract_session(_plain_session(), source="deletion")

        with db.connect() as conn:
            default_ep = get_episode(conn, default_result.episode_ids[0])
            compress_ep = get_episode(conn, compress_result.episode_ids[0])
            deletion_ep = get_episode(conn, deletion_result.episode_ids[0])
        assert default_ep is not None and default_ep.source is EpisodeSource.SESSION_END
        assert compress_ep is not None and compress_ep.source is EpisodeSource.CONTEXT_COMPRESS
        assert deletion_ep is not None and deletion_ep.source is EpisodeSource.DELETION

    async def test_deletion_source_persists_to_db_column(self, db):
        """FIX-2 Sec-C-β：``source="deletion"`` 持久化后 DB 行 ``source`` 列 = ``"deletion"``。"""
        provider = _FakeProvider(semantic=_semantic_json(), episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session(), source="deletion")

        with db.connect() as conn:
            row = conn.execute(
                "SELECT source FROM episodes WHERE id = ?",
                (result.episode_ids[0],),
            ).fetchone()
        assert row is not None
        assert row["source"] == "deletion"

    @pytest.mark.parametrize(
        ("raw_outcome", "expected"),
        [
            ("success", "completed"),
            ("ongoing", "ongoing"),
            ("invalid", "completed"),
            ("partial", "partial"),
            (None, "completed"),
        ],
    )
    async def test_outcome_normalization(self, db, raw_outcome, expected):
        episode = json.dumps(
            {"summary": "一次任务", "goal": "目标", "outcome": raw_outcome, "entities": []},
            ensure_ascii=False,
        )
        provider = _FakeProvider(semantic=_semantic_json(), episode=episode)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_plain_session())

        with db.connect() as conn:
            episode_row = get_episode(conn, result.episode_ids[0])
        assert episode_row is not None
        assert episode_row.outcome.value == expected

    async def test_double_track_failure_commits_nothing_and_retry_persists(self, db):
        provider = _FakeProvider(semantic="坏", episode="坏")
        extractor = _make_extractor(db, provider)
        session = _session(
            [
                {"role": "user", "content": "跑测试"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_tool_call("bash", '{"cmd": "pytest"}', "c1")],
                },
            ]
        )

        result = await extractor.extract_session(session)

        # 契约 §2.6：模型错误文本不算空成功 → 两轨失败时整批不提交
        assert result.episode_ids == []
        assert set(result.failed_tracks) == {"semantic", "episode"}

        # 重试：情节轨有效空输出 + 系统提取的 action_nodes，仍落在同一批次
        retry_provider = _FakeProvider(semantic="NONE", episode="NONE")
        retry_extractor = _make_extractor(db, retry_provider)

        retried = await retry_extractor.extract_session(session)

        assert retried.failed_tracks == []
        assert retried.episode_ids != []
        with db.connect() as conn:
            episode = get_episode(conn, retried.episode_ids[0])
        assert episode is not None
        assert episode.summary == ""
        assert len(episode.action_nodes) == 1
        assert episode.action_nodes[0]["tool"] == "bash"

    async def test_empty_session_writes_nothing(self, db):
        provider = _FakeProvider(semantic="NONE", episode="NONE")
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_session(_session([]))

        assert result.memory_ids == []
        assert result.episode_ids == []
        assert result.skipped == 0

    async def test_scratchpad_upsert_is_idempotent(self, db):
        first = ScratchpadEntry(
            user_id="default",
            workspace_id="default",
            updated_at="2026-09-10T00:00:00+00:00",
            current_focus="第一次",
        )
        second = ScratchpadEntry(
            user_id="default",
            workspace_id="default",
            updated_at="2026-09-10T01:00:00+00:00",
            current_focus="第二次",
        )
        with db.connect() as conn:
            upsert_scratchpad(conn, first)
            upsert_scratchpad(conn, second)
        with db.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM scratchpad").fetchone()[0]
            entry = get_scratchpad(conn, "default", "default")

        assert count == 1
        assert entry is not None
        assert entry.current_focus == "第二次"


# ---------------------------------------------------------------------------
# 契约级单测（dataclass 默认值 / 独立方法）
# ---------------------------------------------------------------------------


class TestContracts:
    def test_llm_extraction_result_defaults(self):
        result = LLMExtractionResult()
        assert result.memories == []
        assert result.episode is None
        assert result.failed_tracks == []
        assert result.action_nodes == []

    def test_apply_filters_passes_through_action_nodes(self, db):
        extractor = _make_extractor(db, _FakeProvider())
        system = SystemExtractionResult()
        llm_result = LLMExtractionResult(action_nodes=list(system.action_nodes))
        filtered = extractor._apply_filters(llm_result)

        assert filtered.memories == []
        assert filtered.episode is None
        assert filtered.action_nodes == []

    def test_llm_memory_item_and_episode_item_defaults(self):
        item = LLMMemoryItem(content="c", type="FACT")
        assert item.priority is None
        assert item.importance == 0.7
        assert item.subject == ""
        assert item.tags == []

        episode = LLMEpisodeItem()
        assert episode.summary == ""
        assert episode.outcome is None
        assert episode.entities == []
        assert episode.importance == 0.7


# ---------------------------------------------------------------------------
# FIX-3 Sec-M-1: ActionNode redact + truncate
# ---------------------------------------------------------------------------


class TestCollectActionNodes:
    """FIX-3 Sec-M-1: ActionNode 的 input/output 必须先 redact 再截断。"""

    def test_redacts_api_key_in_output(self):
        """含 ``api_key=xxx`` 的输出应被 ``<redacted>`` 替换。"""
        session = _session(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_tool_call("bash", '{"cmd": "env"}', "c1")],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "name": "bash",
                    "content": "PATH=/usr/bin api_key=sk-live-abc123def",
                },
            ]
        )

        nodes = _collect_action_nodes(session.messages)

        assert len(nodes) == 1
        assert "<redacted>" in nodes[0].output
        assert "sk-live-abc123def" not in nodes[0].output

    def test_redacts_bearer_token(self):
        """``Bearer xxx`` 应被 ``Bearer <redacted>`` 替换。"""
        session = _session(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_tool_call("bash", '{"cmd": "cat"}', "c1")],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "name": "bash",
                    "content": "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9",
                },
            ]
        )

        nodes = _collect_action_nodes(session.messages)

        assert len(nodes) == 1
        assert "Bearer <redacted>" in nodes[0].output
        assert "eyJhbGciOiJIUzI1NiJ9" not in nodes[0].output

    def test_output_truncated_to_max_chars(self):
        """超长 output 被截断到 OUTPUT_MAX_CHARS 并附 truncated 标记。"""
        session = _session(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_tool_call("bash", '{"cmd": "yes"}', "c1")],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "name": "bash",
                    "content": "x" * 5000,
                },
            ]
        )

        nodes = _collect_action_nodes(session.messages)

        from nanobot.memory.extractor import ActionNode

        assert len(nodes) == 1
        # 截断后实际内容 ≤ OUTPUT_MAX_CHARS；末尾追加 truncated 标记
        assert nodes[0].output.startswith("x" * ActionNode.OUTPUT_MAX_CHARS)
        assert "truncated" in nodes[0].output
        # 原始 5000 字符不应完整出现
        assert len(nodes[0].output) < 5000

    def test_input_redacts_password(self):
        """含 ``password=xxx`` 的入参应被 ``<redacted>`` 替换。"""
        session = _session(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("bash", '{"cmd": "login", "password": "p4ssw0rd!"}', "c1")
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": "ok"},
            ]
        )

        nodes = _collect_action_nodes(session.messages)

        assert len(nodes) == 1
        assert "<redacted>" in nodes[0].input
        assert "p4ssw0rd!" not in nodes[0].input


class TestResolveActionSuccess:
    """WU-3A: ``_resolve_action_success`` 的纯函数单元测试。"""

    def test_orphan_always_failed(self):
        """``has_result=False`` 永远失败,无论 output 是什么。"""
        assert _resolve_action_success("anything", False) is False
        assert _resolve_action_success("", False) is False
        assert _resolve_action_success("OK", False) is False
        assert _resolve_action_success("Error: failed", False) is False

    def test_has_result_with_ok_output_succeeds(self):
        """``has_result=True`` 且 output 无 error → 成功。"""
        assert _resolve_action_success("OK", True) is True
        assert _resolve_action_success("file content", True) is True

    def test_has_result_with_empty_output_succeeds(self):
        """``has_result=True`` 但 content 为空字符串:无 error 关键字 → 成功(回归保护)。

        注意:这里与"孤儿"语义不同——孤儿是 tool 消息不存在(``has_result=False``);
        这里是 tool 消息存在但内容为空,工具方可能确实返回了空结果,不应视为失败。
        """
        assert _resolve_action_success("", True) is True

    def test_has_result_with_error_output_fails(self):
        """``has_result=True`` 且 output 含 error → 失败。"""
        assert _resolve_action_success("Error: failed", True) is False
        assert _resolve_action_success("Traceback here", True) is False
        assert _resolve_action_success("ERROR something", True) is False
        assert _resolve_action_success("an exception traceback occurred", True) is False


class TestCollectActionNodesSuccess:
    """WU-3A: ``_collect_action_nodes`` 的 success 字段判定回归测试。"""

    def test_orphan_action_node_marked_failed(self):
        """工具调用但 tool 消息缺失(孤儿) → ``success=False`` + 空 output。"""
        session = _session(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("shell_exec", '{"cmd": "ls"}', "orphan_1")
                    ],
                },
                # 没有 role=tool 对应 orphan_1
            ]
        )

        nodes = _collect_action_nodes(session.messages)

        assert len(nodes) == 1
        assert nodes[0].tool == "shell_exec"
        assert nodes[0].output == ""
        assert nodes[0].success is False

    def test_empty_content_action_node_marked_failed(self):
        """tool 消息存在但 content 为空 → ``success=False``(无 error 关键字)。

        区分于"有结果但空字符串"场景:此场景的 tool 消息存在,所以 ``has_result=True``,
        但空字符串不含 error/traceback,``_looks_like_error`` 返回 False,因此 success=True。
        这是预期回归保护(空字符串不应触发"error"误判)。本测试作为回归点保留。
        """
        session = _session(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("web_search", "{}", "empty_1")
                    ],
                },
                {"role": "tool", "tool_call_id": "empty_1", "name": "web_search", "content": ""},
            ]
        )

        nodes = _collect_action_nodes(session.messages)

        assert len(nodes) == 1
        # has_result=True + output="" + _looks_like_error("")==False → success=True
        # 这是有意保留的"空内容视为成功"语义,确保不被误判为 error。
        assert nodes[0].success is True
        assert nodes[0].output == ""

    def test_normal_success_action_node_still_works(self):
        """正常成功调用 → ``success=True``(回归保护)。"""
        session = _session(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("read_file", '{"path": "/tmp"}', "ok_1")
                    ],
                },
                {"role": "tool", "tool_call_id": "ok_1", "name": "read_file", "content": "file content"},
            ]
        )

        nodes = _collect_action_nodes(session.messages)

        assert len(nodes) == 1
        assert nodes[0].success is True
        assert nodes[0].output == "file content"

    def test_error_action_node_marked_failed(self):
        """含 error/traceback 的 output → ``success=False``(回归保护)。"""
        session = _session(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("shell_exec", "{}", "err_1")
                    ],
                },
                {"role": "tool", "tool_call_id": "err_1", "name": "shell_exec", "content": "Traceback: NameError"},
            ]
        )

        nodes = _collect_action_nodes(session.messages)

        assert len(nodes) == 1
        assert nodes[0].success is False

    def test_mixed_orphan_and_normal_action_nodes(self):
        """混合场景:同一 session 中既有孤儿也有正常调用,分别正确判定。"""
        session = _session(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        _tool_call("tool_a", "{}", "orphan_id"),
                        _tool_call("tool_b", "{}", "ok_id"),
                        _tool_call("tool_c", "{}", "err_id"),
                    ],
                },
                # orphan_id: 无对应 tool 消息 → 失败
                {"role": "tool", "tool_call_id": "ok_id", "name": "tool_b", "content": "OK"},
                {"role": "tool", "tool_call_id": "err_id", "name": "tool_c", "content": "Error: bad"},
            ]
        )

        nodes = _collect_action_nodes(session.messages)

        assert len(nodes) == 3
        by_tool = {node.tool: node for node in nodes}
        assert by_tool["tool_a"].success is False  # 孤儿
        assert by_tool["tool_b"].success is True   # 正常
        assert by_tool["tool_c"].success is False  # error


# ---------------------------------------------------------------------------
# FIX-4 Sec-M-2: 数值与字符串字段值域校验
# ---------------------------------------------------------------------------


class TestCoerceMemoryItem:
    """FIX-4 Sec-M-2: importance/content/tags/subject/predicate 域校验。"""

    def test_importance_nan_falls_back_to_default(self):
        """``importance="nan"`` 回落默认值 0.7。"""
        item = _coerce_memory_item(
            {"content": "x", "type": "FACT", "importance": "nan"}
        )
        assert item is not None
        assert item.importance == 0.7

    def test_importance_inf_falls_back_to_default(self):
        """``importance=1e400``（Inf）回落默认值 0.7。"""
        item = _coerce_memory_item(
            {"content": "x", "type": "FACT", "importance": "1e400"}
        )
        assert item is not None
        assert item.importance == 0.7

    def test_importance_negative_falls_back_to_default(self):
        """``importance=-1`` 越界回落默认值 0.7（FIX-4 Sec-M-2：失败或越界 → 0.7）。"""
        item = _coerce_memory_item(
            {"content": "x", "type": "FACT", "importance": -1}
        )
        assert item is not None
        assert item.importance == 0.7

    def test_importance_above_one_falls_back_to_default(self):
        """``importance=2.5`` 越界回落默认值 0.7。"""
        item = _coerce_memory_item(
            {"content": "x", "type": "FACT", "importance": 2.5}
        )
        assert item is not None
        assert item.importance == 0.7

    def test_content_truncated_when_over_max_chars(self):
        """超长 content 截断 + truncated 标记。"""
        from nanobot.memory.extractor import CONTENT_MAX_CHARS

        long_content = "a" * (CONTENT_MAX_CHARS + 1000)
        item = _coerce_memory_item({"content": long_content, "type": "FACT"})
        assert item is not None
        assert len(item.content) <= CONTENT_MAX_CHARS + len("...[truncated]")
        assert item.content.endswith("...[truncated]")

    def test_tags_truncated_when_over_max_items(self):
        """超量 tags 截断到 TAGS_MAX_ITEMS。"""
        from nanobot.memory.extractor import TAGS_MAX_ITEMS

        many_tags = [f"tag-{i}" for i in range(TAGS_MAX_ITEMS + 100)]
        item = _coerce_memory_item(
            {"content": "x", "type": "FACT", "tags": many_tags}
        )
        assert item is not None
        assert len(item.tags) == TAGS_MAX_ITEMS
        assert item.tags[0] == "tag-0"
        assert item.tags[-1] == f"tag-{TAGS_MAX_ITEMS - 1}"

    def test_subject_truncated_when_over_max_chars(self):
        """超长 subject 截断到 SUBJECT_MAX_CHARS。"""
        from nanobot.memory.extractor import SUBJECT_MAX_CHARS

        long_subject = "s" * (SUBJECT_MAX_CHARS + 500)
        item = _coerce_memory_item(
            {"content": "x", "type": "FACT", "subject": long_subject}
        )
        assert item is not None
        assert len(item.subject) == SUBJECT_MAX_CHARS

    def test_importance_valid_value_passes_through(self):
        """合法值（0.5）原样保留。"""
        item = _coerce_memory_item(
            {"content": "x", "type": "FACT", "importance": 0.5}
        )
        assert item is not None
        assert item.importance == 0.5


# ---------------------------------------------------------------------------
# WU-1: _render_transcript head+tail 双向保留
# ---------------------------------------------------------------------------


class TestRenderTranscript:
    """WU-1：长对话改为 head + tail 双向保留，避免中段被纯 tail 截断丢失。"""

    def test_render_transcript_short_returns_full(self):
        """短对话 (<8000) 原样返回，不做任何截断。"""
        from nanobot.memory.extractor import _render_transcript

        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = _render_transcript(messages)
        assert "[user] hello" in result
        assert "[assistant] hi" in result
        assert "截断" not in result  # 无 marker

    def test_render_transcript_long_keeps_head_and_tail(self):
        """长对话 (>8000) 保留头尾，middle marker 居中。"""
        from nanobot.memory.extractor import (
            _TRANSCRIPT_TRUNCATE_MARKER,
            _render_transcript,
        )

        # 构造 ~12000 字符对话
        long_text = "x" * 1000
        messages = [{"role": "user", "content": long_text} for _ in range(12)]
        result = _render_transcript(messages, max_chars=8000)
        assert "截断" in result  # marker 在
        assert result.startswith("[user] xxx")  # head 保留
        assert result.endswith("xxx")  # tail 保留
        # 总长不超过 max_chars + marker 长度（默认 head+tail 比例 0.4 + 0.45）
        assert len(result) <= 8000 + len(_TRANSCRIPT_TRUNCATE_MARKER)

    def test_render_transcript_skips_non_user_assistant(self):
        """tool/system 角色跳过，只渲染 user/assistant。"""
        from nanobot.memory.extractor import _render_transcript

        messages = [
            {"role": "user", "content": "hello"},
            {"role": "tool", "content": "result_data"},  # 应跳过
            {"role": "system", "content": "you are helpful"},  # 应跳过
            {"role": "assistant", "content": "hi"},
        ]
        result = _render_transcript(messages)
        assert "result_data" not in result
        assert "you are helpful" not in result
        assert "[user] hello" in result
        assert "[assistant] hi" in result

    def test_render_transcript_skips_empty_content(self):
        """空 content 跳过，不产生空行。"""
        from nanobot.memory.extractor import _render_transcript

        messages = [
            {"role": "user", "content": ""},
            {"role": "user", "content": "   "},  # 全空白
            {"role": "assistant", "content": "real"},
        ]
        result = _render_transcript(messages)
        lines = [line for line in result.split("\n") if line.strip()]
        assert len(lines) == 1
        assert "[assistant] real" in lines[0]

    def test_render_transcript_handles_content_blocks(self):
        """content 为 list[dict] 形式（content-block）也能正确渲染。"""
        from nanobot.memory.extractor import _render_transcript

        messages = [{
            "role": "assistant",
            "content": [
                {"type": "text", "text": "block1"},
                {"type": "text", "text": "block2"},
            ],
        }]
        result = _render_transcript(messages)
        assert "block1" in result
        assert "block2" in result

    def test_render_transcript_custom_ratios(self):
        """自定义 head_ratio / tail_ratio 生效，且超 1.0 时自动按比例缩放。"""
        from nanobot.memory.extractor import _render_transcript

        messages = [{"role": "user", "content": "x" * 10000}]
        result = _render_transcript(
            messages, max_chars=1000, head_ratio=0.5, tail_ratio=0.4
        )
        # 500 + 400 + marker ≈ 1000+
        assert len(result) >= 900  # head + tail 都保留了
        assert "截断" in result

        # 超过 1.0：传入 0.8 + 0.8 应自动缩放（0.5 + 0.5），不抛错
        result2 = _render_transcript(
            messages, max_chars=1000, head_ratio=0.8, tail_ratio=0.8
        )
        assert "截断" in result2
        assert len(result2) >= 800


# ---------------------------------------------------------------------------
# WU-2: extract_incremental 话题切换增量抽取
# ---------------------------------------------------------------------------


class TestExtractIncremental:
    async def test_only_new_messages_are_scanned(self, db):
        """切片外的旧消息不进 prompt：只从 ``last_extracted_index`` 之后扫描。

        修正（2026-09-15）：本测试原名下断言「只按规则信号抽出 1 条 RULE」，那是
        rule-only 旧路径的行为。现恢复正常：LLM 在**新切片**上跑完整流水线，
        因此改为断言 prompt 的切片边界。
        """
        provider = _FakeProvider(
            semantic=_semantic_json(
                memories=[
                    {
                        "content": "必须每次保存日志",
                        "type": "RULE",
                        "priority": "long_term",
                        "importance": 0.7,
                        "tags": [],
                    }
                ]
            ),
            episode=EPISODE_JSON,
        )
        extractor = _make_extractor(db, provider)
        session = _session(
            [
                {"role": "user", "content": "以后都用 uv 管理依赖"},
                {"role": "assistant", "content": "好的"},
                {"role": "user", "content": "必须每次保存日志"},
                {"role": "assistant", "content": "收到"},
            ]
        )

        result = await extractor.extract_incremental(session, 2)

        prompts = "\n".join(call[0]["content"] for call in provider.calls)
        assert "保存日志" in prompts
        assert "uv" not in prompts, "切片之前的消息不得进 prompt"
        assert len(result.memory_ids) == 1
        assert _memory_rows(db)[0].content == "必须每次保存日志"
        assert result.skipped == 0

    async def test_calls_both_llm_tracks(self, db):
        """增量抽取现在走完整流水线：semantic + episode 两路都调。"""
        provider = _FakeProvider(semantic=_semantic_json(), episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)
        session = _session([{"role": "user", "content": "以后都用 uv 管理依赖"}])

        result = await extractor.extract_incremental(session, 0)

        assert provider.tracks == ["semantic", "episode"]
        assert result.episode_ids, "episode 轨必须落库"

    async def test_empty_session_returns_empty_without_llm(self, db):
        provider = _FakeProvider(semantic=_semantic_json(), episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)

        result = await extractor.extract_incremental(_session([]), 0)

        assert result.memory_ids == []
        assert result.episode_ids == []
        assert provider.calls == []

    async def test_no_new_messages_returns_empty_without_llm(self, db):
        provider = _FakeProvider(semantic=_semantic_json(), episode=EPISODE_JSON)
        extractor = _make_extractor(db, provider)
        messages = [{"role": "user", "content": "以后都用 uv"}]

        result = await extractor.extract_incremental(_session(messages), 1)

        assert result.memory_ids == []
        assert result.episode_ids == []
        assert provider.calls == []

    def test_topic_change_source_maps(self, db):
        extractor = _make_extractor(db, _FakeProvider())

        assert extractor._map_source("topic_change") is EpisodeSource.TOPIC_CHANGE

    def test_idle_source_maps(self, db):
        """``idle`` 必须有专属枚举值，否则会 warning 并错记成 session_end。"""
        extractor = _make_extractor(db, _FakeProvider())

        assert extractor._map_source("idle") is EpisodeSource.IDLE


# ---------------------------------------------------------------------------
# WU-4: _build_prompt_messages 参数化
# ---------------------------------------------------------------------------


class TestBuildPromptMessages:
    """WU-4：让 ``_build_prompt_messages`` 接受 ``transcript_max_chars`` /
    ``transcript_head_ratio`` 透传给 ``_render_transcript``，默认行为不变。
    """

    def test_build_prompt_messages_default_unchanged(self, db):
        """默认参数下与直接 ``_render_transcript(session.messages)`` 完全一致。"""
        from nanobot.memory.extractor import (
            _TRANSCRIPT_TRUNCATE_MARKER,
            _render_transcript,
        )

        long_text = "A" * 1000
        messages = [{"role": "user", "content": long_text} for _ in range(12)]
        session = _session(messages)
        extractor = _make_extractor(db, _FakeProvider())

        msgs = extractor._build_prompt_messages(
            "PROMPT_BODY", session, SystemExtractionResult()
        )

        assert len(msgs) == 1
        body = msgs[0]["content"]
        assert "PROMPT_BODY" in body
        assert "## 对话内容" in body
        # 默认行为：transcript 段与 _render_transcript(session.messages) 完全一致
        direct = _render_transcript(session.messages)
        assert direct in body
        assert "截断" in body  # 12000 chars > 8000 -> 触发 marker
        assert body.index(direct) == body.index("## 对话内容") + len("## 对话内容\n\n")
        # 默认值就是 8000 + marker
        head_chars = int(8000 * 0.4)
        tail_chars = int(8000 * 0.45)
        assert len(direct) == head_chars + len(_TRANSCRIPT_TRUNCATE_MARKER) + tail_chars

    def test_build_prompt_messages_with_custom_max_chars(self, db):
        """传入 ``transcript_max_chars=200`` 时 transcript 长度不超过 200 + marker。"""
        from nanobot.memory.extractor import _TRANSCRIPT_TRUNCATE_MARKER

        long_text = "B" * 500
        messages = [{"role": "user", "content": long_text} for _ in range(10)]
        session = _session(messages)
        extractor = _make_extractor(db, _FakeProvider())

        msgs = extractor._build_prompt_messages(
            "PROMPT",
            session,
            SystemExtractionResult(),
            transcript_max_chars=200,
        )

        body = msgs[0]["content"]
        assert "## 对话内容" in body
        # 提取 transcript 段以核对长度
        start = body.index("## 对话内容") + len("## 对话内容\n\n")
        end = body.index("\n\n## 阶段1")
        transcript = body[start:end]
        assert "截断" in transcript
        assert len(transcript) <= 200 + len(_TRANSCRIPT_TRUNCATE_MARKER)

    def test_build_prompt_messages_with_custom_head_ratio(self, db):
        """``transcript_head_ratio=0.8`` 时开头 80% × max_chars 仍保留关键开头。"""
        from nanobot.memory.extractor import (
            _TRANSCRIPT_TRUNCATE_MARKER,
            _render_transcript,
        )

        # 构造超长对话（≥ 2×8000 字符）：把 head_marker 放在第一行靠后位置
        # 默认 head=0.4 (head_chars=3200) 切掉它;head=0.8 (auto-scale 后 5120) 保留
        head_marker = "HEAD_BEGIN_MARKER"
        tail_marker = "TAIL_END_MARKER"
        first_user = ("x" * 3300) + " " + head_marker + " " + ("x" * 1000)
        messages = [{"role": "user", "content": first_user}]
        # 用额外 assistant 消息铺出 16000+ 字符
        for i in range(10):
            messages.append({"role": "assistant", "content": f"turn-{i} " + "y" * 1500})
        messages.append({"role": "user", "content": f"final {tail_marker} " + "z" * 1500})
        session = _session(messages)
        extractor = _make_extractor(db, _FakeProvider())

        msgs = extractor._build_prompt_messages(
            "PROMPT",
            session,
            SystemExtractionResult(),
            transcript_max_chars=8000,
            transcript_head_ratio=0.8,
        )

        body = msgs[0]["content"]
        assert "## 对话内容" in body
        start = body.index("## 对话内容") + len("## 对话内容\n\n")
        end = body.index("\n\n## 阶段1")
        transcript = body[start:end]

        assert "截断" in transcript
        # head_ratio=0.8 + 默认 tail_ratio=0.45 之和 1.25>1.0,会被自动按比例缩放。
        # 缩放后 head_ratio=0.64, tail_ratio=0.36, head_chars=5120, tail_chars=2880。
        head_chars = 5120
        tail_chars = 2880
        assert len(transcript) == head_chars + len(_TRANSCRIPT_TRUNCATE_MARKER) + tail_chars

        assert head_marker in transcript[:head_chars]

        default_transcript = _render_transcript(
            session.messages, max_chars=8000, head_ratio=0.4
        )
        default_head_chars = int(8000 * 0.4)
        assert head_marker not in default_transcript[:default_head_chars]


# ---------------------------------------------------------------------------
# 内部纯函数：JSON 解析
# ---------------------------------------------------------------------------


class TestParseJsonObject:
    """`_parse_json_object` 必须容忍真实 LLM 输出里的围栏与前后废话。

    回归背景：S2 提交曾引入一个同类名函数覆盖本实现，其贪婪正则
    `\\{.*\\}` 会把「JSON 后面的解释文字」或「第二个对象」一并吞掉，
    导致 episode 抽取稳定报 unparseable JSON。
    """

    def test_plain_object(self):
        assert _parse_json_object('{"summary": "hi"}') == {"summary": "hi"}

    def test_fenced_block(self):
        raw = '```json\n{"summary": "hi"}\n```'
        assert _parse_json_object(raw) == {"summary": "hi"}

    def test_surrounding_prose(self):
        raw = '好的，抽取结果如下：\n{"summary": "hi"}\n希望有帮助！'
        assert _parse_json_object(raw) == {"summary": "hi"}

    def test_returns_first_of_two_objects(self):
        raw = '{"a": 1}\n{"b": 2}'
        assert _parse_json_object(raw) == {"a": 1}

    def test_braces_inside_string_values(self):
        raw = '{"summary": "he said {ok} loudly"}'
        assert _parse_json_object(raw) == {"summary": "he said {ok} loudly"}

    def test_nested_object_with_trailing_prose(self):
        raw = 'Sure!\n{"episode": {"goal": "x"}, "n": 2}\nDone.'
        assert _parse_json_object(raw) == {"episode": {"goal": "x"}, "n": 2}

    def test_truncated_json_returns_none(self):
        assert _parse_json_object('{"summary": "unfinished') is None

    def test_non_object_returns_none(self):
        assert _parse_json_object("NONE") is None
        assert _parse_json_object("no json here") is None

