"""Natural-language Chinese recall regressions.

The project memory used by these tests mirrors the PZ-INC-027 audit case:
the record contains a project name and operational keywords, while the user
asks a long natural-language question without quoting the stored wording.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory, MemoryType
from nanobot.memory.repository import add_memory, search_memories
from nanobot.memory.retrieval.engine import RetrievalEngine
from nanobot.memory.retrieval.store_adapter import MemoryStoreAdapter

_PROJECT_MEMORY = (
    "【纸舟客服 · 事故 PZ-INC-027】多租户工单系统出现重复工单。"
    "根因是网关超时重投，修复位置是 ticket_ingest.py::create_ticket_from_event。"
)
_NATURAL_QUERY = (
    "纸舟客服新增了邮件接入。今天网关超时后的重投，又产生了两张工单。"
    "我们以前处理过类似问题，你能找回当时确认的方案，并说明这次需要先核对哪些证据吗？"
)


@pytest.fixture
def project_db(tmp_path: Path) -> MemoryDatabase:
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        add_memory(
            conn,
            Memory(
                id="project-memory",
                content=_PROJECT_MEMORY,
                type=MemoryType.FACT,
                subject="纸舟客服事故 PZ-INC-027",
                predicate="根因与修复",
                importance_score=0.9,
                created_at="2026-11-02T15:23:18+00:00",
                updated_at="2026-11-02T15:23:18+00:00",
                workspace_id="default",
            ),
        )
    return db


def test_search_memories_recalls_project_from_natural_language(
    project_db: MemoryDatabase,
) -> None:
    with project_db.connect() as conn:
        hits = search_memories(conn, _NATURAL_QUERY, limit=10)
    assert [memory.id for memory in hits] == ["project-memory"]


@pytest.mark.asyncio
async def test_retrieval_engine_recalls_project_from_natural_language(
    project_db: MemoryDatabase,
) -> None:
    engine = RetrievalEngine(store=MemoryStoreAdapter(project_db), brain=None)
    block, ids = await engine.retrieve_with_ids(
        query=_NATURAL_QUERY,
        recent_messages=[],
        max_tokens=800,
        workspace_ids=("default",),
    )
    assert ids == ["project-memory"]
    assert "纸舟客服" in block


def test_unrelated_natural_language_query_stays_empty(
    project_db: MemoryDatabase,
) -> None:
    with project_db.connect() as conn:
        hits = search_memories(conn, "今天天气很好，适合出去散步", limit=10)
    assert hits == []
