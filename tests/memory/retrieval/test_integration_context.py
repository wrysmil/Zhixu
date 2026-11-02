"""检索 → 统一记忆组装的接线测试（契约 §4 / spec §7）。

覆盖：
1. ``active_retrieval_enabled=True`` + 引擎 → 检索结果进入统一记忆块；
2. 未启用 / 引擎缺失 → 不调用引擎，也不产出记忆块；
3. 引擎抛异常 → 失败隔离，system prompt 照常构建；
4. ``_build_memory_section_with_ids`` 返回 ``(RecalledMemorySection, 引用 id)``；
5. 核心事实与召回同一条事实只出现一次（去重在组装侧）；
6. 没有结构化条目的已渲染段落不再注入（注入路径只有一条）。
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from nanobot.agent.context import ContextBuilder
from nanobot.memory.context import RecalledMemorySection
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory, MemoryType
from nanobot.memory.repository import add_memory
from nanobot.memory.retrieval.candidate import MemoryHit


def _hit(record_id: str = "m1", content: str = "Python 爬虫项目", *, kind: str = "memory"):
    return MemoryHit(
        kind=kind,  # type: ignore[arg-type]
        record_id=record_id,
        content=content,
        workspace_id="default",
    )


class _Engine:
    """检索引擎替身：只实现结构化入口 ``search_hits``。"""

    def __init__(self, hits: list[MemoryHit] | None = None, error: Exception | None = None):
        self.hits = hits if hits is not None else [_hit()]
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def search_hits(self, *, query, recent_messages, workspace_ids=None, limit=10):
        self.calls.append(
            {
                "query": query,
                "recent_messages": recent_messages,
                "workspace_ids": workspace_ids,
                "limit": limit,
            }
        )
        if self.error is not None:
            raise self.error
        return list(self.hits)


def _builder(tmp_path: Path, **kw: Any) -> ContextBuilder:
    return ContextBuilder(workspace=tmp_path, **kw)


def _seed_fact(workspace: Path, content: str, *, record_id: str | None = None) -> str:
    db = MemoryDatabase(workspace)
    db.init_schema()
    fact_id = record_id or str(uuid.uuid4())
    with db.connect() as conn:
        add_memory(conn, Memory(
            id=fact_id,
            content=content,
            type=MemoryType.FACT,
            importance_score=0.9,
            scope="user",
            workspace_id="default",
            created_at="2026-11-02T10:00:00Z",
            updated_at="2026-11-02T10:00:00Z",
        ))
    return fact_id


# ---------------------------------------------------------------------------
# 1) enabled + engine → 记忆块进入 prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enabled_with_engine_injects_composed_block(tmp_path: Path):
    engine = _Engine([_hit("m1", "Python 爬虫项目")])
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=engine)

    section, ids = await builder._build_memory_section_with_ids(
        query="我之前写的 Python 爬虫", recent_messages=[]
    )
    prompt = builder.build_system_prompt(
        workspace=tmp_path, include_memory=False, retrieved_memory_section=section
    )

    assert isinstance(section, RecalledMemorySection)
    assert "Python 爬虫项目" in prompt
    assert "## 相关记忆（自动检索）" in prompt
    assert ids == ["m1"]


@pytest.mark.asyncio
async def test_engine_call_forwards_query_messages_and_limit(tmp_path: Path):
    engine = _Engine()
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=engine)
    recent = [{"role": "user", "content": "之前的话题"}]

    await builder._build_memory_section_with_ids(
        query="我们之前讨论过的内容是什么",
        recent_messages=recent,
        workspace_ids=("default", "ws-a"),
    )

    call = engine.calls[0]
    assert call["query"] == "我们之前讨论过的内容是什么"
    assert call["recent_messages"] is recent
    assert call["workspace_ids"] == ("default", "ws-a")
    assert call["limit"] >= 1


# ---------------------------------------------------------------------------
# 2) 未启用 / 引擎缺失
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_does_not_call_engine(tmp_path: Path):
    engine = _Engine()
    builder = _builder(tmp_path, active_retrieval_enabled=False, retrieval_engine=engine)

    section, ids = await builder._build_memory_section_with_ids(
        query="Python 爬虫", recent_messages=[]
    )

    assert (section, ids) == ("", [])
    assert engine.calls == []


@pytest.mark.asyncio
async def test_engine_none_does_not_call(tmp_path: Path):
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=None)

    assert await builder._build_memory_section_with_ids(
        query="Python 爬虫", recent_messages=[]
    ) == ("", [])


@pytest.mark.asyncio
async def test_memory_toggle_off_disables_retrieval(tmp_path: Path):
    engine = _Engine()
    builder = _builder(
        tmp_path,
        active_retrieval_enabled=True,
        retrieval_engine=engine,
        memory_enabled_provider=lambda: False,
    )

    assert await builder._build_memory_section_with_ids(
        query="Python 爬虫", recent_messages=[]
    ) == ("", [])
    assert engine.calls == []


# ---------------------------------------------------------------------------
# 3) 失败隔离
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_exception_is_isolated(tmp_path: Path):
    engine = _Engine(error=RuntimeError("retrieval engine down"))
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=engine)

    section, ids = await builder._build_memory_section_with_ids(
        query="Python 爬虫", recent_messages=[]
    )

    assert (section, ids) == ("", [])
    prompt = builder.build_system_prompt(
        workspace=tmp_path, include_memory=False, retrieved_memory_section=section
    )
    assert "retrieval engine down" not in prompt


# ---------------------------------------------------------------------------
# 4) 空结果
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_hits_returns_empty_section(tmp_path: Path):
    builder = _builder(
        tmp_path, active_retrieval_enabled=True, retrieval_engine=_Engine([])
    )

    section, ids = await builder._build_memory_section_with_ids(
        query="Python 爬虫", recent_messages=[]
    )

    assert (section, ids) == ("", [])
    assert builder.build_system_prompt(
        workspace=tmp_path, include_memory=False, retrieved_memory_section=section
    ).count("## 相关记忆") == 0


# ---------------------------------------------------------------------------
# 5) 核心事实 + 召回去重
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_core_fact_and_recalled_hit_injected_once(tmp_path: Path):
    fact_id = _seed_fact(tmp_path, "用户偏好中文回答")
    engine = _Engine([_hit(fact_id, "用户偏好中文回答")])
    builder = _builder(tmp_path, active_retrieval_enabled=True, retrieval_engine=engine)

    section, _ids = await builder._build_memory_section_with_ids(
        query="用户偏好什么语言", recent_messages=[]
    )
    prompt = builder.build_system_prompt(
        workspace=tmp_path, include_memory=True, retrieved_memory_section=section
    )

    assert prompt.count("用户偏好中文回答") == 1
    assert "## 核心事实（当前有效记录）" in prompt


def test_core_facts_injected_without_retrieval(tmp_path: Path):
    _seed_fact(tmp_path, "用户使用 Windows 11")
    builder = _builder(tmp_path)

    prompt = builder.build_system_prompt(workspace=tmp_path, include_memory=True)

    assert "用户使用 Windows 11" in prompt
    assert "## 核心事实（当前有效记录）" in prompt


def test_include_memory_false_omits_core_facts(tmp_path: Path):
    _seed_fact(tmp_path, "用户使用 Windows 11")
    builder = _builder(tmp_path)

    prompt = builder.build_system_prompt(workspace=tmp_path, include_memory=False)

    assert "用户使用 Windows 11" not in prompt


# ---------------------------------------------------------------------------
# 6) 注入路径唯一
# ---------------------------------------------------------------------------


def test_plain_rendered_section_is_not_injected(tmp_path: Path):
    """没有结构化条目的已渲染段落不再注入：注入只能经组装函数。"""
    builder = _builder(tmp_path)

    prompt = builder.build_system_prompt(
        workspace=tmp_path,
        include_memory=False,
        retrieved_memory_section="## 相关记忆\n- legacy block",
    )

    assert "legacy block" not in prompt


# ---------------------------------------------------------------------------
# 7) transcript / messages 透传
# ---------------------------------------------------------------------------


def test_build_transcript_forwards_carrier(tmp_path: Path):
    from nanobot.agent.context import TranscriptInput

    builder = _builder(tmp_path)
    section = RecalledMemorySection("", [_hit("m1", "透传条目")])

    messages = builder.build_transcript(
        TranscriptInput(history=[], current_message=None),
        workspace=tmp_path,
        include_memory=False,
        retrieved_memory_section=section,
    )

    assert "透传条目" in messages[0]["content"]


def test_build_messages_forwards_carrier(tmp_path: Path):
    builder = _builder(tmp_path)
    section = RecalledMemorySection("", [_hit("m1", "兼容透传")])

    messages = builder.build_messages(
        history=[],
        current_message="hi",
        workspace=tmp_path,
        include_memory=False,
        retrieved_memory_section=section,
    )

    assert "兼容透传" in messages[0]["content"]
