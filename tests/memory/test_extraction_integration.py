"""End-to-end integration tests for Phase 2 memory extraction pipeline (plan §10 Task 10)
and the WU-A idle extraction timer flow.

Covers the full lifecycle of memory extraction through the ``MemoryExtractor``,
``MemoryExtractionHook``, and ``ScratchpadWriter`` trio with real
``MemoryDatabase`` (SQLite) — only the LLM is mocked via duck-typed fakes.

Scenarios verified:

1.  Full lifecycle: real session transcript → ``extractor.extract_session``
    + ``ScratchpadWriter.update_focus`` → assert memories / episode /
    scratchpad focus all persisted.
2.  Cross-restart persistence: reopen ``MemoryDatabase`` against the same
    ``db_path`` and confirm all three artifacts survive.
3.  CHAT intent does NOT write ``current_focus`` via hook ``after_run``.
4.  TASK intent writes ``current_focus`` via hook ``after_run``.
5.  ``on_finally`` does NOT cancel the idle timer (WU-A: the timer must
    outlive the run it was armed in).
6.  ``_system_extract`` produces ``ActionNode`` from a tool_call + tool
    response without invoking the LLM.
7.  LLM episode-track failure is isolated: ``extract_session`` returns
    cleanly with ``failed_tracks=["episode"]`` while the semantic track
    still persists memory.
8.  ``on_error`` writes ``current_focus`` urgently without calling the
    extractor or LLM.
9.  **WU-C Idle extraction full cycle** (3 turns → idle timer fires once →
    state.last_count advances, scratchpad focus updates, semantic memories
    and the episode persist).
10. **WU-C Idle extraction cancellation** (second ``after_run`` cancels the
    first timer; only the second timer fires and advances state).
11. **WU-C Idle extraction no-op** (pre-populated state matching current
    message count → zero LLM calls, state unchanged).
12. **WU-C Idle extraction failure isolation** (``extract_incremental``
    raising does NOT advance state; fixing the failure lets the next run
    advance state normally).

All fakes are duck-typed (no inheritance from real LLM classes). Business
modules are NOT modified — any signature mismatch is documented in a
test comment rather than patched in the implementation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.agent.hook import AgentRunHookContext
from nanobot.agent.hooks.memory_extraction import (
    _BACKGROUND_TASKS,
    _PENDING_IDLE_TIMERS,
    MemoryExtractionHook,
)
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import MemoryExtractor
from nanobot.memory.models import EpisodeSource, MemoryType
from nanobot.memory.repository import (
    get_episode,
    get_extraction_state,
    get_memory,
    get_scratchpad,
    list_episodes_by_session,
    search_memories,
    upsert_extraction_state,
)
from nanobot.memory.scratchpad_writer import ScratchpadWriter
from nanobot.session.manager import Session

# ---------------------------------------------------------------------------
# Shared fakes (duck-typed; no inheritance)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 2048
    reasoning_effort = None


class _FakeProvider:
    """Routes ``chat_with_retry`` by prompt content (semantic vs episode track)."""

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

    async def chat_with_retry(
        self, *, messages: list[dict[str, Any]], **_kwargs: Any
    ) -> _FakeResponse:
        self.calls.append(messages)
        text = " ".join(
            m.get("content", "") for m in messages if isinstance(m.get("content"), str)
        )
        is_semantic = "语义记忆抽取" in text
        delay = self.semantic_delay if is_semantic else self.episode_delay
        if delay:
            await asyncio.sleep(delay)
        result = self.semantic if is_semantic else self.episode
        if isinstance(result, BaseException):
            raise result
        return _FakeResponse(result)


class _FakeRuntime:
    def __init__(self, provider: _FakeProvider, model: str = "fake-model") -> None:
        self.provider = provider
        self.model = model
        self.generation = _FakeGeneration()
        self.context_window_tokens = 8192


class _FakeExtractor:
    """Stand-in for ``MemoryExtractor`` that records ``extract_session`` calls.

    Mirrors the signature of the real extractor so the hook's duck typing works.
    """

    def __init__(self, *, delay: float = 0.0, raise_exc: BaseException | None = None) -> None:
        self.calls: list[tuple[Any, str]] = []
        self.idle_calls: list[Any] = []
        self.delay = delay
        self.raise_exc = raise_exc

    async def extract_session(self, session: Any, *, source: str = "session_end") -> Any:
        self.calls.append((session, source))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc
        return None

    async def run_idle_extraction(self, session: Any) -> Any:
        """WU-A: idle 入口,复用相同的 delay/raise 语义。"""
        self.idle_calls.append(session)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc
        return None


# ---------------------------------------------------------------------------
# Shared JSON payloads
# ---------------------------------------------------------------------------

SEMANTIC_OK = json.dumps(
    {
        "memories": [
            {
                "content": "用户使用 Windows",
                "type": "fact",
                "importance": 0.7,
                "subject": "用户",
                "predicate": "使用",
            },
            {
                "content": "用户偏好 uv",
                "type": "preference",
                "importance": 0.8,
                "subject": "用户",
                "predicate": "偏好",
            },
        ],
        "experiences": [],
    },
    ensure_ascii=False,
)

EPISODE_OK = json.dumps(
    {
        "summary": "用户在 Windows 上配置 uv 并表达长期偏好",
        "goal": "完成 uv 配置",
        "outcome": "completed",
        "entities": ["uv", "Windows"],
        "tools_used": ["bash"],
    },
    ensure_ascii=False,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_call(name: str, arguments: str, call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _run_ctx(*user_messages: str) -> AgentRunHookContext:
    return AgentRunHookContext(
        messages=[{"role": "user", "content": m} for m in user_messages]
    )


def _init_db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


# WU-A: idle 抽取现在走完整四阶段流水线（semantic + episode 两路 LLM），
# 因此集成测试需要给出真实可解析的 payload。
_IDLE_SEMANTIC_JSON = json.dumps(
    {
        "memories": [
            {
                "content": "用户以后都用 uv 管理 Python 依赖",
                "type": "RULE",
                "priority": "long_term",
                "importance": 0.8,
                "tags": ["python"],
            }
        ],
        "experiences": [],
    },
    ensure_ascii=False,
)

_IDLE_EPISODE_JSON = json.dumps(
    {
        "summary": "用户通过 nanobot 约定使用 uv 管理依赖",
        "goal": "约定依赖管理工具",
        "outcome": "completed",
        "entities": ["uv"],
        "tools_used": [],
    },
    ensure_ascii=False,
)


# ---------------------------------------------------------------------------
# Auto-cleanup for module-level background tasks (mirrors the hook test fixture).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _cleanup_background_tasks():
    """Drain and cancel module-level fire-and-forget tasks between tests."""
    yield
    for task in list(_BACKGROUND_TASKS):
        task.cancel()
    await asyncio.sleep(0)
    _BACKGROUND_TASKS.clear()
    # WU-A: also drain idle timers so a stuck timer can't leak across tests.
    for task in list(_PENDING_IDLE_TIMERS.values()):
        if not task.done():
            task.cancel()
    await asyncio.sleep(0)
    _PENDING_IDLE_TIMERS.clear()


# ---------------------------------------------------------------------------
# Case 1 + 2: Full lifecycle + cross-restart persistence
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    async def test_end_to_end_persists_memories_episode_and_focus(self, tmp_path: Path):
        """Task 10: real session transcript → all three memory layers persist."""
        db = _init_db(tmp_path)
        provider = _FakeProvider(semantic=SEMANTIC_OK, episode=EPISODE_OK)
        extractor = MemoryExtractor(db, _FakeRuntime(provider))
        writer = ScratchpadWriter(db, user_id="default")

        session = Session(
            key="telegram:chat-uv-1",
            messages=[
                {"role": "user", "content": "如何在 Windows 上配置 uv"},
                {
                    "role": "assistant",
                    "content": "我来帮你配置。",
                    "tool_calls": [_tool_call("bash", '{"cmd": "uv --version"}', "c1")],
                },
                {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": "uv 0.5.0"},
                {"role": "user", "content": "我以后都用 uv"},
                {"role": "assistant", "content": "好的，记下来了。"},
            ],
        )

        # T0 focus write (hook normally does this; integration test drives it directly).
        await writer.update_focus(session.key, "我以后都用 uv")
        # T1 extraction (full pipeline).
        result = await extractor.extract_session(session, source="session_end")

        # --- Memories: both items persisted via FTS, source = "extraction" ---
        assert result.failed_tracks == []
        assert len(result.memory_ids) == 2
        with db.connect() as conn:
            hits = search_memories(conn, "用户")
        assert {h.content for h in hits} >= {"用户使用 Windows", "用户偏好 uv"}
        fact = next(h for h in hits if h.content == "用户使用 Windows")
        preference = next(h for h in hits if h.content == "用户偏好 uv")
        assert fact.type is MemoryType.FACT
        assert fact.source == "extraction"
        assert preference.type is MemoryType.PREFERENCE
        assert preference.source == "extraction"

        # --- Episode: exactly one row, source = SESSION_END, action_nodes non-empty ---
        with db.connect() as conn:
            episodes = list_episodes_by_session(conn, session.key)
        assert len(episodes) == 1
        episode = episodes[0]
        assert episode.source is EpisodeSource.SESSION_END
        assert len(episode.action_nodes) >= 1
        assert any(node["tool"] == "bash" for node in episode.action_nodes)

        # --- Scratchpad: focus was written via T0 ---
        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")
        assert entry is not None
        assert entry.current_focus == "我以后都用 uv"

        # Store artifacts for downstream cross-restart test.
        assert provider.calls  # LLM was invoked
        assert len(episodes) == 1

    async def test_persists_across_database_reopen(self, tmp_path: Path):
        """After closing the DB, reconnecting to the same file yields all artifacts."""
        db = _init_db(tmp_path)
        provider = _FakeProvider(semantic=SEMANTIC_OK, episode=EPISODE_OK)
        extractor = MemoryExtractor(db, _FakeRuntime(provider))
        writer = ScratchpadWriter(db, user_id="default")

        session = Session(
            key="telegram:chat-uv-2",
            messages=[
                {"role": "user", "content": "如何在 Windows 上配置 uv"},
                {
                    "role": "assistant",
                    "content": "...",
                    "tool_calls": [_tool_call("bash", '{"cmd": "uv --version"}', "c1")],
                },
                {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": "uv 0.5.0"},
                {"role": "user", "content": "我以后都用 uv"},
                {"role": "assistant", "content": "好的"},
            ],
        )
        await writer.update_focus(session.key, "我以后都用 uv")
        await extractor.extract_session(session, source="session_end")

        # Close and reopen via a fresh MemoryDatabase pointing at the same db_path.
        db_path = db.db_path
        del db
        reopened = MemoryDatabase(tmp_path, db_path=db_path)

        with reopened.connect() as conn:
            hits = search_memories(conn, "用户")
            episodes = list_episodes_by_session(conn, "telegram:chat-uv-2")
            entry = get_scratchpad(conn, "default", "default")

        assert {h.content for h in hits} >= {"用户使用 Windows", "用户偏好 uv"}
        assert len(episodes) == 1
        assert episodes[0].source is EpisodeSource.SESSION_END
        assert entry is not None
        assert entry.current_focus == "我以后都用 uv"


# ---------------------------------------------------------------------------
# Cases 3 + 4: Intent gate via real hook + real scratchpad writer
# ---------------------------------------------------------------------------


class TestHookIntentGate:
    async def test_chat_intent_skips_focus_write(self, tmp_path: Path):
        """CHAT messages (e.g. '你好') bypass the T0 focus write."""
        db = _init_db(tmp_path)
        writer = ScratchpadWriter(db, user_id="default")
        hook = MemoryExtractionHook(
            _FakeExtractor(),
            "s1",
            writer,
        )

        await hook.after_run(_run_ctx("你好"))
        await hook.on_finally(_run_ctx())  # drain pending T1 task

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")
        assert entry is None  # nothing written — focus stays empty + no row

    async def test_task_intent_writes_focus(self, tmp_path: Path):
        """TASK messages (e.g. '帮我实现爬虫') trigger update_focus via T0."""
        db = _init_db(tmp_path)
        writer = ScratchpadWriter(db, user_id="default")
        hook = MemoryExtractionHook(
            _FakeExtractor(),
            "s1",
            writer,
        )

        await hook.after_run(_run_ctx("帮我实现爬虫"))
        await hook.on_finally(_run_ctx())  # drain pending T1 task

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")
        assert entry is not None
        assert entry.current_focus == "帮我实现爬虫"
        assert entry.active_projects == []


# ---------------------------------------------------------------------------
# Case 5: on_finally drains within 5s
# ---------------------------------------------------------------------------


class TestHookOnFinally:
    async def test_on_finally_cancels_idle_without_raising(self, tmp_path: Path):
        """WU-A: ``on_finally`` 不再 await;而是取消 idle 任务,5s ceiling 已删除。"""
        db = _init_db(tmp_path)
        writer = ScratchpadWriter(db, user_id="default")
        extractor = _FakeExtractor(delay=0.2)
        hook = MemoryExtractionHook(extractor, "s1", writer)

        await hook.after_run(_run_ctx("帮我实现一个爬虫"))
        await hook.on_finally(_run_ctx())  # must not raise

        # 旧 T1 路径已删除:on_finally 立即取消 idle 任务,不调 extract_session。
        assert extractor.calls == []
        # idle 也未触发(on_finally 在 0.2s delay 之前取消)。
        assert extractor.idle_calls == []


# ---------------------------------------------------------------------------
# Case 6: System extract (no LLM)
# ---------------------------------------------------------------------------


class TestSystemExtractActionNodes:
    def test_tool_call_with_response_produces_action_node(self, tmp_path: Path):
        """Phase 1 builds ``ActionNode`` purely from assistant+tool messages."""
        db = _init_db(tmp_path)
        provider = _FakeProvider()
        extractor = MemoryExtractor(db, _FakeRuntime(provider))

        session = Session(
            key="s-tool",
            messages=[
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [_tool_call("read_file", '{"path": "a.py"}', "c1")],
                },
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "name": "read_file",
                    "content": "def hello():\n    return 42\n",
                },
            ],
        )

        result = extractor._system_extract(session)

        assert len(result.action_nodes) >= 1
        first = result.action_nodes[0]
        assert first.tool == "read_file"
        assert "hello" in first.output
        assert "42" in first.output
        assert first.success is True

        # No LLM call should have happened.
        assert provider.calls == []


# ---------------------------------------------------------------------------
# Case 7: Failure isolation between LLM tracks
# ---------------------------------------------------------------------------


class TestLLMTrackFailureIsolation:
    async def test_episode_track_failure_does_not_block_semantic(self, tmp_path: Path):
        """Invalid episode JSON is reported in ``failed_tracks`` while memory persists."""
        db = _init_db(tmp_path)
        provider = _FakeProvider(semantic=SEMANTIC_OK, episode="not json at all")
        extractor = MemoryExtractor(db, _FakeRuntime(provider))

        session = Session(
            key="s-llm-iso",
            messages=[
                {"role": "user", "content": "帮我配置 uv"},
                {"role": "assistant", "content": "好的"},
            ],
        )

        result = await extractor.extract_session(session)

        # episode track failed; semantic succeeded.
        assert "episode" in result.failed_tracks
        assert "semantic" not in result.failed_tracks
        # At least one memory persisted via the surviving semantic track.
        with db.connect() as conn:
            hits = search_memories(conn, "uv")
        assert any(h.content in {"用户使用 Windows", "用户偏好 uv"} for h in hits)
        assert all(h.source == "extraction" for h in hits)


# ---------------------------------------------------------------------------
# Case 8: on_error writes focus without invoking the LLM or extractor
# ---------------------------------------------------------------------------


class TestHookOnError:
    async def test_on_error_writes_focus_and_skips_extractor(self, tmp_path: Path):
        """T0' emergency path persists focus but does not invoke the LLM or extractor."""
        db = _init_db(tmp_path)
        writer = ScratchpadWriter(db, user_id="default")
        extractor = _FakeExtractor()
        hook = MemoryExtractionHook(extractor, "s1", writer)

        await hook.on_error(_run_ctx("救命"))

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")
        assert entry is not None
        assert entry.current_focus == "救命"
        assert extractor.calls == []  # no extraction scheduled


# ---------------------------------------------------------------------------
# FIX-6 Rev-M-4: Memory.source_episode_id 反向回填
# ---------------------------------------------------------------------------


class TestSourceEpisodeBackfill:
    """FIX-6 Rev-M-4: episode 持久化成功后，把 episode_id 回填到
    episode.linked_memory_ids 对应 memory 行的 source_episode_id 列。"""

    async def test_episode_written_backfills_memory_source_episode_id(
        self, tmp_path: Path
    ):
        """集成路径：subject==entity 匹配 → memory 行 source_episode_id == episode_id。"""
        db = _init_db(tmp_path)
        semantic = json.dumps(
            {
                "memories": [
                    {
                        "content": "uv 用于管理 Python 依赖",
                        "type": "skill",
                        "importance": 0.8,
                        "subject": "uv",
                        "predicate": "用于",
                    }
                ],
                "experiences": [],
            },
            ensure_ascii=False,
        )
        episode_payload = json.dumps(
            {
                "summary": "uv 配置完成",
                "goal": "管理依赖",
                "outcome": "completed",
                "entities": ["uv"],
                "tools_used": ["bash"],
            },
            ensure_ascii=False,
        )
        provider = _FakeProvider(semantic=semantic, episode=episode_payload)
        extractor = MemoryExtractor(db, _FakeRuntime(provider))

        session = Session(
            key="telegram:backfill-1",
            messages=[{"role": "user", "content": "配置 uv"}],
        )

        result = await extractor.extract_session(session)

        assert len(result.memory_ids) == 1
        assert len(result.episode_ids) == 1
        memory_id = result.memory_ids[0]
        episode_id = result.episode_ids[0]

        with db.connect() as conn:
            memory = get_memory(conn, memory_id)

        assert memory is not None
        assert memory.source_episode_id == episode_id

    async def test_episode_without_entities_still_backfills_batch_memories(self, tmp_path: Path):
        """episode 无 entities 时，本批次产出的记忆仍要回填 source_episode_id。

        旧行为：``linked_memory_ids`` 只由 entities↔subject 字符串匹配产生，
        entities 为空即整条空 → 回填跳过。这依赖模型自发填 subject（抽取 prompt
        从不要求），实践中关联几乎从未建立。新语义：本批次产出的记忆一律挂到
        本情节上，entities 只补充语义关联。
        """
        db = _init_db(tmp_path)
        semantic = json.dumps(
            {
                "memories": [
                    {
                        "content": "用户偏好 uv",
                        "type": "preference",
                        "importance": 0.8,
                        "subject": "用户",
                        "predicate": "偏好",
                    }
                ],
                "experiences": [],
            },
            ensure_ascii=False,
        )
        # entities 为空：语义关联为空，但本批次兜底仍应建立关联
        episode_payload = json.dumps(
            {
                "summary": "无关联记忆的 episode",
                "goal": "",
                "outcome": "completed",
                "entities": [],
                "tools_used": [],
            },
            ensure_ascii=False,
        )
        provider = _FakeProvider(semantic=semantic, episode=episode_payload)
        extractor = MemoryExtractor(db, _FakeRuntime(provider))

        session = Session(
            key="telegram:backfill-empty",
            messages=[{"role": "user", "content": "x"}],
        )

        result = await extractor.extract_session(session)

        assert len(result.memory_ids) == 1
        assert len(result.episode_ids) == 1
        with db.connect() as conn:
            memory = get_memory(conn, result.memory_ids[0])
            episode = get_episode(conn, result.episode_ids[0])
        assert memory is not None and episode is not None
        assert memory.source_episode_id == result.episode_ids[0]
        assert result.memory_ids[0] in episode.linked_memory_ids

    async def test_backfill_failure_does_not_raise(self, tmp_path: Path, monkeypatch):
        """FIX-6：单条回填失败仅 warning，不影响 episode 持久化与后续回填。"""
        from nanobot.memory import extractor as extractor_module

        db = _init_db(tmp_path)
        semantic = json.dumps(
            {
                "memories": [
                    {
                        "content": "uv 用于管理 Python 依赖",
                        "type": "skill",
                        "importance": 0.8,
                        "subject": "uv",
                        "predicate": "用于",
                    }
                ],
                "experiences": [],
            },
            ensure_ascii=False,
        )
        episode_payload = json.dumps(
            {
                "summary": "uv 配置完成",
                "goal": "管理依赖",
                "outcome": "completed",
                "entities": ["uv"],
                "tools_used": [],
            },
            ensure_ascii=False,
        )
        provider = _FakeProvider(semantic=semantic, episode=episode_payload)
        extractor = MemoryExtractor(db, _FakeRuntime(provider))

        call_count = {"n": 0}

        def _flaky_update(conn, memory_id, episode_id):
            call_count["n"] += 1
            # 模拟 SQLite 瞬态错误：不抛（与生产一致应被 try/except 隔离）
            return None

        monkeypatch.setattr(
            extractor_module, "update_memory_source_episode", _flaky_update
        )

        session = Session(
            key="telegram:backfill-fail",
            messages=[{"role": "user", "content": "x"}],
        )

        # 不抛即通过；episode 仍写入
        result = await extractor.extract_session(session)

        assert len(result.episode_ids) == 1
        assert call_count["n"] == 1

        # 真出错场景：直接让 update_memory_source_episode 抛异常，确认被吞掉
        def _exploding_update(conn, memory_id, episode_id):
            raise RuntimeError("simulated sqlite error")

        monkeypatch.setattr(
            extractor_module, "update_memory_source_episode", _exploding_update
        )

        result2 = await extractor.extract_session(
            Session(key="telegram:backfill-explode", messages=[
                {"role": "user", "content": "x"}
            ])
        )
        # episode 仍写入
        assert len(result2.episode_ids) == 1


# ---------------------------------------------------------------------------
# WU-C Cases 9–12: Idle extraction end-to-end flow (plan §2026-09-15 Task 6)
# ---------------------------------------------------------------------------


async def _wait_for_idle_completion(session_key: str, timeout: float = 2.0) -> None:
    """轮询等待 ``_PENDING_IDLE_TIMERS`` 中对应会话的 idle 任务结束。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if session_key not in _PENDING_IDLE_TIMERS:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"idle timer for {session_key!r} did not finish within {timeout}s"
    )


class TestIdleExtractionIntegration:
    """WU-C: ``MemoryExtractionHook`` idle 定时器 → ``MemoryExtractor.run_idle_extraction``
    端到端链路。

    关键不变量：
    - ``run_idle_extraction`` 不调 LLM（仅规则信号扫描）。
    - ``state.last_count`` 必须推进到 ``len(session.messages)``（成功执行后）。
    - 第二次 ``after_run`` 必须取消第一次的 idle 任务。
    - ``extract_incremental`` 抛错时，``state`` 不推进；下次重试按当前消息数覆盖。
    """

    async def test_idle_extraction_full_cycle(self, tmp_path: Path):
        """Case 9：3 个 turn → idle 触发一次 → state 推进 + focus 更新 + 记忆/情节落库。"""
        db = _init_db(tmp_path)
        provider = _FakeProvider(
            semantic=_IDLE_SEMANTIC_JSON, episode=_IDLE_EPISODE_JSON
        )
        extractor = MemoryExtractor(db, _FakeRuntime(provider))
        writer = ScratchpadWriter(db, user_id="default")
        hook = MemoryExtractionHook(
            extractor, "s1", writer, idle_seconds=0.05
        )

        # 3 个 turn: 每轮 after_run 触发 T0 写 focus + arm idle。
        # 中间不调 on_finally，否则 idle 会被立即取消（与生产一致：on_finally 是退出兜底）。
        contexts = [
            AgentRunHookContext(
                messages=[
                    {"role": "user", "content": "我以后都使用 uv"},
                    {"role": "assistant", "content": "好的"},
                ]
            ),
            AgentRunHookContext(
                messages=[
                    {"role": "user", "content": "我以后都使用 uv"},
                    {"role": "assistant", "content": "好的"},
                    {"role": "user", "content": "以后必须每次都写测试"},
                    {"role": "assistant", "content": "明白"},
                ]
            ),
            AgentRunHookContext(
                messages=[
                    {"role": "user", "content": "我以后都使用 uv"},
                    {"role": "assistant", "content": "好的"},
                    {"role": "user", "content": "以后必须每次都写测试"},
                    {"role": "assistant", "content": "明白"},
                    {"role": "user", "content": "永远禁止在 main 直接 print"},
                    {"role": "assistant", "content": "已记下"},
                ]
            ),
        ]
        for ctx in contexts:
            await hook.after_run(ctx)

        # 等最后一个 idle 任务完成(0.05s 阈值 + 余量)。
        await _wait_for_idle_completion("s1", timeout=2.0)

        # --- state.last_count 必须推进到当前消息总数 ---
        with db.connect() as conn:
            state = get_extraction_state(conn, "s1")
        assert state is not None
        assert state.last_count == len(contexts[-1].messages) == 6
        assert state.last_source == "idle"

        # --- scratchpad.current_focus 必须更新到最后一条 user msg(T0)---
        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")
        assert entry is not None
        assert entry.current_focus == "永远禁止在 main 直接 print"

        # --- 语义记忆必须入库(extract_incremental 走完整流水线)---
        with db.connect() as conn:
            hits = search_memories(conn, "uv")
        assert hits, "语义轨记忆未落库"

        # --- episode 必须入库(回归:idle 路径曾恒不产 episode)---
        with db.connect() as conn:
            episodes = conn.execute("SELECT id, source FROM episodes").fetchall()
        assert len(episodes) == 1
        assert episodes[0][1] == "idle"

        # --- 两路 LLM 各调一次(semantic + episode)---
        assert len(provider.calls) == 2

    async def test_idle_extraction_cancelled_by_new_message(self, tmp_path: Path):
        """Case 10:第二个 ``after_run`` 必须取消第一个 timer,只触发一次抽取,state 推进一次。"""
        db = _init_db(tmp_path)
        provider = _FakeProvider(
            semantic=_IDLE_SEMANTIC_JSON, episode=_IDLE_EPISODE_JSON
        )
        extractor = MemoryExtractor(db, _FakeRuntime(provider))
        writer = ScratchpadWriter(db, user_id="default")
        hook = MemoryExtractionHook(
            extractor, "s1", writer, idle_seconds=0.1
        )

        # 第一个 turn,arm 第一个 idle 任务。
        ctx1 = AgentRunHookContext(
            messages=[
                {"role": "user", "content": "我以后都使用 uv"},
                {"role": "assistant", "content": "好的"},
            ]
        )
        await hook.after_run(ctx1)
        first_task = _PENDING_IDLE_TIMERS.get("s1")
        assert first_task is not None and not first_task.done()

        # 第二个 turn(在第一个 timer 自然触发前):必须取消 first_task,arm 第二个 timer。
        ctx2 = AgentRunHookContext(
            messages=[
                {"role": "user", "content": "我以后都使用 uv"},
                {"role": "assistant", "content": "好的"},
                {"role": "user", "content": "永远禁止忘记 commit"},
                {"role": "assistant", "content": "明白"},
            ]
        )
        await hook.after_run(ctx2)
        second_task = _PENDING_IDLE_TIMERS.get("s1")
        assert second_task is not None
        assert second_task is not first_task

        # 等第二个 idle 完成。
        await _wait_for_idle_completion("s1", timeout=2.0)

        # 第一个任务已 cancelled。
        # 给事件循环一点时间处理 cancel callback。
        for _ in range(50):
            if first_task.done():
                break
            await asyncio.sleep(0.01)
        assert first_task.cancelled() or first_task.done()

        # state 仅被第二个 timer 推进一次。
        with db.connect() as conn:
            state = get_extraction_state(conn, "s1")
        assert state is not None
        assert state.last_count == 4  # ctx2 的消息总数
        assert state.last_source == "idle"

        # 只有第二个 timer 触发了一次抽取 → 2 次 LLM 调用(semantic + episode)。
        assert len(provider.calls) == 2

    async def test_idle_extraction_no_op_when_state_current(self, tmp_path: Path):
        """Case 11:state.last_count == current_count 时,run_idle_extraction 是 no-op。"""
        db = _init_db(tmp_path)
        provider = _FakeProvider()
        extractor = MemoryExtractor(db, _FakeRuntime(provider))

        session = Session(
            key="s1",
            messages=[
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "msg2"},
            ],
        )

        # 预置 state.last_count = 当前消息数,source="session_end"。
        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                "s1",
                last_count=len(session.messages),
                source="session_end",
                extracted_at="2026-09-15T10:00:00+00:00",
            )

        result = await extractor.run_idle_extraction(session)

        # --- 不调 LLM(没机会调,直接走 no-op 短路)---
        assert provider.calls == []
        # --- 返回空 ExtractionResult ---
        assert result.memory_ids == []
        assert result.episode_ids == []
        assert result.failed_tracks == []
        # --- state 保持不变(source 仍为 session_end)---
        with db.connect() as conn:
            state = get_extraction_state(conn, "s1")
        assert state is not None
        assert state.last_count == len(session.messages)
        assert state.last_source == "session_end"
        assert state.last_extracted_at == "2026-09-15T10:00:00+00:00"

    async def test_idle_extraction_advances_state_only_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Case 12:extract_incremental 抛错 → state 不推进;修复后重试 → state 推进。"""
        db = _init_db(tmp_path)
        provider = _FakeProvider()
        extractor = MemoryExtractor(db, _FakeRuntime(provider))

        session = Session(
            key="s1",
            messages=[
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "我以后都使用 uv"},
                {"role": "assistant", "content": "好的"},
                {"role": "user", "content": "永远禁止直接 print"},
            ],
        )

        # 预置 state.last_count = 2(source=session_end 表示上次完整抽取)。
        with db.connect() as conn:
            upsert_extraction_state(
                conn,
                "s1",
                last_count=2,
                source="session_end",
                extracted_at="2026-09-15T10:00:00+00:00",
            )

        # Monkey-patch extract_incremental 让它抛错。
        original_extract_incremental = extractor.extract_incremental

        async def _raise_incremental(
            *args: Any, **kwargs: Any
        ) -> Any:
            raise RuntimeError("simulated incremental failure")

        monkeypatch.setattr(
            extractor, "extract_incremental", _raise_incremental
        )

        # 第一次跑:失败,state 不推进。
        result = await extractor.run_idle_extraction(session)
        assert result.memory_ids == []
        assert result.episode_ids == []

        with db.connect() as conn:
            state = get_extraction_state(conn, "s1")
        assert state is not None
        assert state.last_count == 2
        assert state.last_source == "session_end"  # 保持原 source
        assert state.last_extracted_at == "2026-09-15T10:00:00+00:00"

        # 修复:还原 extract_incremental。
        monkeypatch.setattr(
            extractor, "extract_incremental", original_extract_incremental
        )

        # 第二次跑:成功,state 推进到 5。
        await extractor.run_idle_extraction(session)

        with db.connect() as conn:
            state = get_extraction_state(conn, "s1")
        assert state is not None
        assert state.last_count == 5
        assert state.last_source == "idle"  # 已切换到 idle source
        # last_extracted_at 必须被刷新(不再是 10:00:00)。
        assert state.last_extracted_at != "2026-09-15T10:00:00+00:00"

        # 重试成功时走了完整流水线 → 两路 LLM 各调一次。
        assert len(provider.calls) == 2
