"""D11 收口：引用评分（usefulness）写回链路已删除。

删除前的闭环是「LLM 逐条评判注入记忆是否 useful → useful=true 自增
``access_count`` → reranker 的访问次数项抬升排序」。WU-10 把排序收敛为 RRF 名次
融合后，``access_count`` 不再参与排序（见
``tests/memory/retrieval/test_reranker.py::test_rerank_ignores_access_count``），
整条链路只剩成本没有收益，故在 D11 一并删除。

本文件守护三件事：
- semantic prompt 不再追加引用评分段（``cited_memory_ids`` 形参也已删除）；
- LLM 输出里即便出现 ``citation_scores`` / ``useful``，也**不会**写回
  ``access_count``；
- 相关字段与 ``repository.bump_access_count`` 已不存在（防止回归）。
"""

from __future__ import annotations

import inspect
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from nanobot.memory import repository
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import LLMExtractionResult, MemoryExtractor
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.repository import add_memory, get_memory
from nanobot.session.manager import Session

# ---------------------------------------------------------------------------
# Fakes（鸭子类型，与 test_extractor_incremental.py 同款）
# ---------------------------------------------------------------------------

_SEMANTIC_MARKER = "语义记忆抽取"
_EPISODE_MARKER = "情节记忆抽取"


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 512
    reasoning_effort = None


class _FakeProvider:
    """semantic 轨返回 memories（+ 模型自愿多给的 ``citation_scores``）。"""

    def __init__(
        self,
        *,
        semantic: dict[str, Any],
        episode: dict[str, Any] | None = None,
    ) -> None:
        self.semantic = semantic
        self.episode = episode or {
            "summary": "s", "goal": "g", "outcome": "completed",
            "entities": [], "tools_used": [],
        }
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


def _add_memory(db: MemoryDatabase, *, content: str) -> str:
    memory_id = str(uuid.uuid4())
    memory = Memory(
        id=memory_id,
        content=content,
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        source="manual",
        importance_score=0.7,
        subject="",
        predicate="",
        workspace_id="default",
        created_at="2026-09-15T10:00:00+00:00",
        updated_at="2026-09-15T10:00:00+00:00",
    )
    with db.connect() as conn:
        add_memory(conn, memory)
    return memory_id


def _session(messages: list[dict[str, Any]] | None = None, *, key: str = "s1") -> Session:
    return Session(
        key=key,
        messages=messages
        or [
            {"role": "user", "content": "以后都用 uv 管理依赖"},
            {"role": "assistant", "content": "好的，已记住"},
        ],
    )


def _semantic_with_scores(mem_id: str, *, useful: bool = True) -> dict[str, Any]:
    """semantic 输出：1 条画像 + 模型自愿返回的引用评分。"""
    return {
        "memories": [
            {
                "content": "用户偏好用 uv 管理 Python 依赖",
                "evidence": "以后都用 uv 管理依赖",
                "subject": "用户",
                "predicate": "偏好",
                "type": "PREFERENCE",
                "importance": 0.6,
            }
        ],
        "citation_scores": [{"memory_id": mem_id, "useful": useful}],
    }


def _extractor(db: MemoryDatabase, provider: _FakeProvider) -> MemoryExtractor:
    return MemoryExtractor(db, _FakeRuntime(provider), user_id="u1", workspace_id="default")


def _access_count(db: MemoryDatabase, memory_id: str) -> int:
    with db.connect() as conn:
        memory = get_memory(conn, memory_id)
    assert memory is not None
    return int(memory.access_count)


# ---------------------------------------------------------------------------
# 写回已删除
# ---------------------------------------------------------------------------


class TestNoUsefulnessWriteBack:
    async def test_useful_score_in_llm_output_is_ignored(self, db: MemoryDatabase):
        """模型返回 useful=true 也不写回 ``access_count``（写回链路已删除）。"""
        mem_id = _add_memory(db, content="用户住在杭州")
        provider = _FakeProvider(semantic=_semantic_with_scores(mem_id, useful=True))
        extractor = _extractor(db, provider)

        result = await extractor.run_idle_extraction(_session())

        assert result.failed_tracks == []
        assert _access_count(db, mem_id) == 0

    async def test_extraction_never_mentions_citation_scores(self, db: MemoryDatabase):
        """semantic prompt 里不再出现引用评分相关的任何字样。"""
        mem_id = _add_memory(db, content="用户住在杭州")
        provider = _FakeProvider(semantic=_semantic_with_scores(mem_id))
        extractor = _extractor(db, provider)

        await extractor.run_idle_extraction(_session())

        semantic_prompt = provider.prompts()[0]
        assert _SEMANTIC_MARKER in semantic_prompt
        assert "citation" not in semantic_prompt
        assert "useful" not in semantic_prompt
        assert f"ID={mem_id}" not in semantic_prompt

    async def test_extraction_without_cited_ids_is_unchanged(self, db: MemoryDatabase):
        """常规路径不受影响：记忆照常落库。"""
        provider = _FakeProvider(semantic=_semantic_with_scores("ignored"))
        extractor = _extractor(db, provider)

        result = await extractor.run_idle_extraction(_session())

        assert result.memory_ids, "常规语义抽取不应受影响"
        assert "citation" not in "\n".join(provider.prompts())


class TestResidueRemoved:
    def test_bump_access_count_helper_is_gone(self):
        """无人调用的 helper 一并删除，避免有人再把它接回排序。"""
        assert not hasattr(repository, "bump_access_count")

    def test_idle_extraction_signature_has_no_cited_ids(self):
        """惰性 ``cited_memory_ids`` 形参已删除（D13），不再接收注入 id。"""
        assert "cited_memory_ids" not in inspect.signature(
            MemoryExtractor.run_idle_extraction
        ).parameters

    def test_extraction_results_have_no_citation_fields(self):
        """字段本身删除：checkpoint payload 里不会再出现 citation 键。"""
        fields = LLMExtractionResult.__dataclass_fields__
        assert "citation_scores" not in fields
        assert "cited_memory_ids" not in fields
