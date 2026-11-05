"""实际输出/注入的引用计数：使用真实 SQLite，候选查询保持只读。"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.agent.context import ContextBuilder
from nanobot.agent.memory import Consolidator, MemoryStore
from nanobot.agent.tools.memory_search import MemorySearchTool
from nanobot.memory.context import RecalledMemorySection
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Episode, Memory
from nanobot.memory.repository import add_episode, add_memory, get_episode, get_memory
from nanobot.memory.retrieval.candidate import hit_from_episode, hit_from_memory
from nanobot.memory.retrieval.engine import RetrievalEngine
from nanobot.memory.retrieval.store_adapter import MemoryStoreAdapter
from nanobot.session.manager import Session

STAMP = "2026-11-02T10:00:00+00:00"


@pytest.fixture
def records(tmp_path: Path):
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    fact = Memory(
        id="shared", content="Python preference", created_at=STAMP,
        updated_at=STAMP, scope="user", importance_score=0.9,
    )
    other = replace(fact, id="other", content="Python other preference")
    episode = Episode(
        id="shared", session_id="session", summary="Python history",
        started_at=STAMP, ended_at=STAMP, linked_memory_ids=["other"],
    )
    with database.connect() as conn:
        add_memory(conn, fact)
        add_memory(conn, other)
        add_episode(conn, episode)
    return database, fact, other, episode


def _counts(database: MemoryDatabase) -> tuple[int, int, int]:
    with database.connect() as conn:
        fact = get_memory(conn, "shared")
        other = get_memory(conn, "other")
        episode = get_episode(conn, "shared")
    assert fact and other and episode
    return fact.access_count, other.access_count, episode.access_count


async def test_tool_output_counts_facts_and_episodes(records):
    database, *_ = records
    engine = RetrievalEngine(store=MemoryStoreAdapter(database))
    result = await MemorySearchTool(lambda: engine).execute(query="Python")
    assert "Python preference" in result and "Python history" in result
    assert _counts(database) == (1, 1, 1)


async def test_search_candidates_and_budget_drops_are_not_counted(records):
    database, *_ = records
    engine = RetrievalEngine(store=MemoryStoreAdapter(database))
    assert len(await engine.search_hits(query="Python", recent_messages=[])) == 3
    assert _counts(database) == (0, 0, 0)
    result = await engine.retrieve(query="Python", recent_messages=[], max_tokens=70)
    assert result
    assert sum(_counts(database)) == 1


def test_adapter_deduplicates_kind_id_and_does_not_cascade(records):
    database, fact, _, episode = records
    fact_hit, episode_hit = hit_from_memory(fact), hit_from_episode(episode)
    MemoryStoreAdapter(database).record_accesses([fact_hit, fact_hit, episode_hit, episode_hit])
    assert _counts(database) == (1, 0, 1)
    with database.connect() as conn:
        stored = get_memory(conn, fact.id)
    assert stored is not None
    assert stored.last_accessed_at is not None
    assert stored.updated_at == STAMP


def test_adapter_rejects_workspace_mismatch_and_missing_ids(records):
    database, fact, *_ = records
    hit = hit_from_memory(fact)
    MemoryStoreAdapter(database).record_accesses([
        replace(hit, workspace_id="other-workspace"), replace(hit, record_id="missing"),
    ])
    assert _counts(database) == (0, 0, 0)


def test_access_increments_are_atomic_across_independent_connections(records):
    database, fact, *_ = records
    hit = hit_from_memory(fact)

    def record(_index):
        independent = MemoryDatabase(database.workspace)
        MemoryStoreAdapter(independent).record_accesses([hit])

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(record, range(12)))
    assert _counts(database) == (12, 0, 0)


def test_empty_access_selection_is_noop(records):
    database, *_ = records
    MemoryStoreAdapter(database).record_accesses([])
    assert _counts(database) == (0, 0, 0)


def test_final_context_counts_once_after_core_recall_deduplication(records, tmp_path):
    database, fact, _, episode = records
    # 核心事实注入路径（文件模式下核心事实不进 prompt）。
    builder = ContextBuilder(workspace=tmp_path, memory_file_mode=False)
    recalled = RecalledMemorySection("", [hit_from_memory(fact), hit_from_episode(episode)])
    result = builder.build_system_prompt(
        include_memory=True, retrieved_memory_section=recalled,
    )
    assert result.count(fact.content) == 1
    assert _counts(database) == (1, 1, 1)


async def test_preliminary_context_selection_does_not_count(records, tmp_path):
    database, *_ = records
    builder = ContextBuilder(
        workspace=tmp_path, active_retrieval_enabled=True,
        retrieval_engine=RetrievalEngine(store=MemoryStoreAdapter(database)),
    )
    section, _ = await builder._build_memory_section_with_ids(
        query="Python", recent_messages=[],
    )
    assert _counts(database) == (0, 0, 0)
    builder.build_system_prompt(include_memory=False, retrieved_memory_section=section)
    assert _counts(database) == (1, 1, 1)


def test_context_does_not_count_budget_dropped_hits(records, tmp_path, monkeypatch):
    database, fact, *_ = records
    monkeypatch.setattr("nanobot.agent.context.MEMORY_CONTEXT_MAX_TOKENS", 1)
    result = ContextBuilder(workspace=tmp_path).build_system_prompt(
        include_memory=False,
        retrieved_memory_section=RecalledMemorySection("", [hit_from_memory(fact)]),
    )
    assert fact.content not in result
    assert _counts(database) == (0, 0, 0)


async def test_statistics_failure_does_not_break_tool_or_context(records, tmp_path, monkeypatch):
    database, fact, *_ = records

    def fail(*_args, **_kwargs):
        raise RuntimeError("statistics unavailable")

    monkeypatch.setattr(MemoryStoreAdapter, "record_accesses", fail)
    engine = RetrievalEngine(store=MemoryStoreAdapter(database))
    assert fact.content in await MemorySearchTool(lambda: engine).execute(query="Python")
    result = ContextBuilder(workspace=tmp_path).build_system_prompt(
        include_memory=False,
        retrieved_memory_section=RecalledMemorySection("", [hit_from_memory(fact)]),
    )
    assert fact.content in result


def test_token_estimate_includes_memory_without_recording_access(records, tmp_path, monkeypatch):
    database, fact, *_ = records
    # 核心事实注入路径（文件模式下核心事实不进 prompt）。
    builder = ContextBuilder(workspace=tmp_path, memory_file_mode=False)
    probe_prompts = []

    def estimate(_provider, _model, messages, _tools):
        probe_prompts.append(messages[0]["content"])
        return 123, "test"

    monkeypatch.setattr("nanobot.agent.memory.estimate_prompt_tokens_chain", estimate)
    consolidator = Consolidator(
        store=MemoryStore(tmp_path), sessions=SimpleNamespace(),
        build_messages=builder.build_messages, get_tool_definitions=lambda: [],
    )
    session = Session(key="websocket:status")
    session.add_message("user", "hello")
    runtime = SimpleNamespace(provider=None, model="test")
    assert consolidator.estimate_session_prompt_tokens(session, runtime=runtime) == (123, "test")
    assert fact.content in probe_prompts[0]
    assert _counts(database) == (0, 0, 0)
    # 同一条真实 prompt 路径仍然计数，探测开关不应修改共享 builder 状态。
    builder.build_messages(history=[], current_message="hello")
    assert _counts(database) == (1, 1, 0)
