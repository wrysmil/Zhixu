"""P2 每日盘账：衰减、归档、规则级去重、checkpoint 续跑与预算裁剪。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from nanobot.memory import decay
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.lifecycle import MEMORY_MD_MAX_CHARS, MemoryLifecycle
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.repository import (
    add_memory,
    get_memory,
    list_memories,
    touch_memories,
)
from nanobot.webui.memory_services import MemoryServices

WS = "test-ws"


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def services(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    db = MemoryDatabase(ws)
    db.init_schema()
    return MemoryServices(workspace_id=WS, database=db)


def _mem(
    content: str,
    mtype: MemoryType = MemoryType.FACT,
    *,
    importance: float = 0.3,
    decay_rate: float = 0.1,
    created_days_ago: float = 60,
    last_accessed_days_ago: float | None = None,
) -> Memory:
    created = _iso(created_days_ago)
    return Memory(
        id=str(uuid.uuid4()),
        content=content,
        created_at=created,
        updated_at=created,
        type=mtype,
        priority=MemoryPriority.LONG_TERM,
        source="manual",
        importance_score=importance,
        decay_rate=decay_rate,
        last_accessed_at=(
            _iso(last_accessed_days_ago) if last_accessed_days_ago is not None else None
        ),
        scope="global",
        workspace_id=WS,
    )


def _seed(services: MemoryServices, *memories: Memory) -> None:
    with services.database.connect() as conn:
        for memory in memories:
            add_memory(conn, memory)


def _get(services: MemoryServices, memory_id: str) -> Memory:
    with services.database.connect() as conn:
        memory = get_memory(conn, memory_id)
    assert memory is not None
    return memory


# ---------------------------------------------------------------------------
# 衰减公式与豁免
# ---------------------------------------------------------------------------


def test_old_low_importance_is_archived(services):
    memory = _mem("这是一条两个月没用的普通事实")
    _seed(services, memory)

    report = decay.run_consolidation(services.database, workspace_id=WS)

    stored = _get(services, memory.id)
    assert report.stats.archived == 1
    assert report.stats.decayed == 1
    assert stored.expires_at is not None
    assert stored.metadata["decay_archived"]["importance_score"] == 0.3
    # 归档不是删除：行数据仍在。
    with services.database.connect() as conn:
        assert len(list_memories(conn, workspace_id=WS, include_inactive=True)) == 1


def test_preference_is_exempt_from_decay(services):
    memory = _mem("用户偏好简短回复", MemoryType.PREFERENCE)
    _seed(services, memory)

    report = decay.run_consolidation(services.database, workspace_id=WS)

    assert report.stats.decayed == 0
    assert report.stats.archived == 0
    assert _get(services, memory.id).expires_at is None


def test_rule_is_exempt_from_decay(services):
    memory = _mem("先读源码再改代码", MemoryType.RULE)
    _seed(services, memory)

    decay.run_consolidation(services.database, workspace_id=WS)

    assert _get(services, memory.id).expires_at is None


def test_high_importance_is_exempt(services):
    memory = _mem("用户要求永久记住的生日", importance=0.95)
    _seed(services, memory)

    report = decay.run_consolidation(services.database, workspace_id=WS)

    assert report.stats.archived == 0
    assert _get(services, memory.id).expires_at is None


def test_recent_retrieval_raises_effective_score(services):
    memory = _mem("经常被检索的事实", importance=0.5, last_accessed_days_ago=2)
    _seed(services, memory)

    before = decay.effective_importance(memory, now=datetime.now(timezone.utc))
    with services.database.connect() as conn:
        assert touch_memories(conn, [memory.id]) == 1
    touched = _get(services, memory.id)
    after = decay.effective_importance(touched, now=datetime.now(timezone.utc))

    assert touched.access_count == 1
    assert touched.last_accessed_at is not None
    assert after > before
    # 刚被访问 → 不该归档。
    report = decay.run_consolidation(services.database, workspace_id=WS)
    assert report.stats.archived == 0


# ---------------------------------------------------------------------------
# 规则级去重
# ---------------------------------------------------------------------------


def test_identical_rows_merge_into_one_canonical(services):
    low = _mem("用户用 pnpm 安装依赖", importance=0.4)
    high = _mem("用户用 pnpm 安装依赖", importance=0.8)
    _seed(services, low, high)

    report = decay.run_consolidation(services.database, workspace_id=WS)

    canonical = _get(services, high.id)
    duplicate = _get(services, low.id)
    assert report.stats.merged == 1
    assert canonical.superseded_by in (None, "")
    assert duplicate.superseded_by == high.id


# ---------------------------------------------------------------------------
# dry-run 与审计
# ---------------------------------------------------------------------------


def test_dry_run_reports_without_writing(services):
    memory = _mem("预演时不该被归档")
    _seed(services, memory)

    report = decay.run_consolidation(
        services.database, workspace_id=WS, dry_run=True
    )

    assert report.dry_run is True
    assert report.stats.archived == 1
    assert report.archived_preview[0]["id"] == memory.id
    assert _get(services, memory.id).expires_at is None
    # 审计行照常落库，并标记 dry_run。
    runs = decay.list_recent_runs(services.database, WS)
    assert runs and runs[0]["dry_run"] == 1
    assert runs[0]["status"] == "completed"


def test_run_writes_audit_row(services):
    _seed(services, _mem("会被归档的老事实"))

    report = decay.run_consolidation(services.database, workspace_id=WS)

    runs = decay.list_recent_runs(services.database, WS)
    assert runs[0]["run_id"] == report.run_id
    assert runs[0]["archived"] == 1
    assert runs[0]["status"] == "completed"


# ---------------------------------------------------------------------------
# checkpoint 续跑
# ---------------------------------------------------------------------------


def test_interrupted_run_resumes_from_checkpoint(services, monkeypatch):
    first = _mem("第一条老事实", created_days_ago=90)
    second = _mem("第二条老事实", created_days_ago=80)
    third = _mem("第三条老事实", created_days_ago=70)
    _seed(services, first, second, third)

    monkeypatch.setattr(decay, "_SCAN_BATCH", 1)
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(decay, "_monotonic", lambda: next(ticks, 100.0))

    first_run = decay.run_consolidation(
        services.database, workspace_id=WS, time_budget_seconds=30
    )
    assert first_run.status == "interrupted"
    assert first_run.stats.archived == 1
    assert first_run.checkpoint.last_id == first.id
    interrupted = decay.latest_interrupted_run(services.database, WS)
    assert interrupted is not None and interrupted["run_id"] == first_run.run_id

    # 续跑：已处理的第一条不重复归档，剩下的继续。
    monkeypatch.setattr(decay, "_monotonic", lambda: 0.0)
    second_run = decay.run_consolidation(
        services.database, workspace_id=WS, time_budget_seconds=30
    )
    assert second_run.status == "completed"
    assert second_run.stats.archived == 2
    assert _get(services, first.id).expires_at is not None
    assert _get(services, second.id).expires_at is not None
    assert _get(services, third.id).expires_at is not None


# ---------------------------------------------------------------------------
# MEMORY.md 预算：规则优先保留
# ---------------------------------------------------------------------------


def test_memory_md_trims_facts_before_rules():
    """超 1500 字符时规则段仍在，尾部事实段被裁。"""
    lifecycle = MemoryLifecycle.__new__(MemoryLifecycle)
    rule = _mem(
        "规则：任何写操作前必须确认目标路径，禁止无确认的批量删除",
        MemoryType.RULE,
        importance=0.6,
    )
    facts = [
        _mem(
            f"事实 {index}：" + "这是一条很长的项目背景说明。" * 40,
            MemoryType.FACT,
            importance=0.5,
        )
        for index in range(20)
    ]

    rendered = lifecycle._render_export([*facts, rule], None)

    assert len(rendered) <= MEMORY_MD_MAX_CHARS
    assert "## 规则" in rendered
    assert "禁止无确认的批量删除" in rendered
    assert "超出字符上限" in rendered
