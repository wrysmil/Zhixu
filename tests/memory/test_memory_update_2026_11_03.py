"""11-03 记忆更新的集成测试。

单独测每一段已有覆盖；这里钉的是**段与段之间的口径**：

* 抽出来的事实能不能被检索到 → 检索命中是否算进访问计数 →
  访问计数能不能救回一条本来会被归档的记忆；
* 删记忆的两段确认在候选集变化后是否真的拦得住；
* 盘账的归档只拨过期时间、不删行，且结果能在统计接口里看到。

覆盖的 11-03 改动：
``4abd8364``（USER.md 投影 / 记忆写入工具 / Dream 审计）、
``a71f2cdf``（每日全库盘账 + 衰减归档 + 注入预算）、
``5b1220b7``（抽取口径收紧 + 访问计数链路修复）。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from nanobot.agent.tools.memory_admin import AddMemoryTool, MemoryDeleteTool
from nanobot.memory import decay
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.lifecycle import MemoryLifecycle
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.profile_extractor import ProfileExtractor
from nanobot.memory.repository import add_memory, get_memory, list_memories
from nanobot.memory.retrieval.candidate import hit_from_memory
from nanobot.memory.retrieval.engine import RetrievalEngine
from nanobot.memory.retrieval.store_adapter import MemoryStoreAdapter
from nanobot.webui.memory_api import stats_payload
from nanobot.webui.memory_services import MemoryServices

WS = "test-ws-1103"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    return ws


@pytest.fixture
def services(workspace):
    database = MemoryDatabase(workspace)
    database.init_schema()
    return MemoryServices(workspace_id=WS, database=database)


@pytest.fixture(autouse=True)
def _fresh_lifecycle_singletons():
    """MemoryLifecycle 按 workspace_id 缓存单例，清掉避免串到别的用例。"""
    MemoryLifecycle._instances.clear()
    MemoryLifecycle._last_refresh_iso.clear()
    MemoryLifecycle._last_refresh_trigger.clear()
    yield
    MemoryLifecycle._instances.clear()


def _profile_runtime(items: list[dict]):
    """假 LLM：把给定的候选原样吐回来。"""

    class Provider:
        async def chat_with_retry(self, *, messages, **kwargs):
            payload = {"memories": items, "experiences": []}
            return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))

    return SimpleNamespace(
        provider=Provider(),
        model="fake",
        generation=SimpleNamespace(
            temperature=0.0, max_tokens=2000, reasoning_effort=None
        ),
    )


def _seed(services: MemoryServices, content: str, **kwargs) -> Memory:
    now = datetime.now(timezone.utc).isoformat()
    memory = Memory(
        id=uuid.uuid4().hex,
        content=content,
        created_at=kwargs.pop("created_at", now),
        updated_at=now,
        type=kwargs.pop("type", MemoryType.FACT),
        priority=MemoryPriority.LONG_TERM,
        source="manual",
        importance_score=kwargs.pop("importance", 0.3),
        decay_rate=kwargs.pop("decay_rate", 0.1),
        last_accessed_at=kwargs.pop("last_accessed_at", None),
        scope="global",
        workspace_id=WS,
        **kwargs,
    )
    with services.database.connect() as conn:
        add_memory(conn, memory)
    return memory


def _rows(services: MemoryServices, *, include_inactive: bool = False) -> list[Memory]:
    with services.database.connect() as conn:
        return list(
            list_memories(
                conn,
                workspace_id=WS,
                include_inactive=include_inactive,
            )
        )


# ---------------------------------------------------------------------------
# 抽取口径 → 入库 → 检索 → 访问计数 → 衰减
# ---------------------------------------------------------------------------


async def test_extracted_fact_is_retrievable_and_counted(services):
    """抽取口径收紧后仍要能落库并被检索命中，且命中要算一次访问。"""
    extracted = await ProfileExtractor(
        _profile_runtime(
            [
                {
                    "content": "纸舟客服已接入邮件通道",
                    "type": "FACT",
                    "evidence": "纸舟客服已经接入邮件通道了",
                },
                {
                    "content": "用户曾经确认过旧方案的负责人",
                    "type": "FACT",
                    "evidence": "你能找回当时确认的负责人吗",
                },
            ]
        )
    ).extract(
        [{"role": "user", "content": "纸舟客服已经接入邮件通道了。你能找回当时确认的负责人吗？"}],
        "episode-1",
    )

    # 问句前提没有被当成事实收下。
    assert [item.content for item in extracted.items] == ["纸舟客服已接入邮件通道"]
    assert extracted.rejected_memories >= 1

    tool = AddMemoryTool(services, source="manual", trigger="tool")
    out = await tool.execute(content="纸舟客服已接入邮件通道", type="fact")
    assert "已记住" in out

    engine = RetrievalEngine(store=MemoryStoreAdapter(services.database))
    rows = _rows(services)
    assert len(rows) == 1

    # 检索命中，并走完整注入路径：只有最终入选的条目才记访问。
    section, injected_ids = await engine.retrieve_with_ids(
        query="纸舟客服 邮件", recent_messages=[], max_tokens=700
    )
    assert section
    assert rows[0].id in injected_ids

    with services.database.connect() as conn:
        stored = get_memory(conn, rows[0].id)
    assert stored is not None
    assert stored.access_count >= 1
    assert stored.last_accessed_at is not None


def test_recently_accessed_memory_is_not_archived(services):
    """访问计数链路修好后，刚召回过的记忆不该在盘账里被归档。"""
    memory = _seed(
        services,
        "纸舟客服的工单重复问题",
        importance=0.3,
        decay_rate=0.1,
        created_at=(datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),
    )

    MemoryStoreAdapter(services.database).record_accesses([hit_from_memory(memory)])

    report = decay.run_consolidation(services.database, workspace_id=WS)

    assert report.stats.processed >= 1
    assert report.stats.archived == 0
    with services.database.connect() as conn:
        stored = get_memory(conn, memory.id)
    assert stored is not None
    assert stored.expires_at is None


# ---------------------------------------------------------------------------
# 每日盘账：归档只拨过期时间 + 结果进统计接口
# ---------------------------------------------------------------------------


def test_consolidation_expires_row_and_exposes_stats(services):
    memory = _seed(
        services,
        "两个月没人提起的一条普通事实",
        importance=0.3,
        decay_rate=0.1,
        created_at=(datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),
    )

    report = decay.run_consolidation(services.database, workspace_id=WS)

    assert report.stats.archived == 1
    assert report.stats.decayed == 1
    with services.database.connect() as conn:
        stored = get_memory(conn, memory.id)
    assert stored is not None
    assert stored.expires_at is not None
    # 归档不是删除：行还在，只是被拨了过期时间。
    assert len(_rows(services, include_inactive=True)) == 1

    payload = stats_payload(services)
    daily = payload["memory_md"]["last_daily_consolidation"]
    assert daily is not None
    assert daily["archived"] == 1
    assert daily["processed"] >= 1
    assert daily["dry_run"] is False
    assert daily["status"] == "completed"


def test_consolidation_dry_run_does_not_write(services):
    memory = _seed(
        services,
        "dry run 预演用的一条事实",
        importance=0.2,
        decay_rate=0.1,
        created_at=(datetime.now(timezone.utc) - timedelta(days=90)).isoformat(),
    )

    report = decay.run_consolidation(services.database, workspace_id=WS, dry_run=True)

    assert report.dry_run is True
    assert report.stats.archived == 1
    with services.database.connect() as conn:
        stored = get_memory(conn, memory.id)
    assert stored is not None
    assert stored.expires_at is None


# ---------------------------------------------------------------------------
# 记忆工具：两段确认 + 投影重算
# ---------------------------------------------------------------------------


async def test_stale_confirm_token_is_rejected(services):
    add = AddMemoryTool(services, source="manual", trigger="tool")
    await add.execute(content="用户偏好使用 pnpm 管理依赖", type="preference")

    delete = MemoryDeleteTool(services, source="manual", trigger="tool")
    preview = await delete.execute(query="pnpm", dry_run=True)
    assert "confirm_token=" in preview
    stale_token = preview.split("confirm_token=")[1].strip()

    # 候选集变了：再写一条能被同一条 query 命中的记忆。
    await add.execute(content="用户偏好使用 pnpm 安装全局工具", type="preference")

    rejected = await delete.execute(
        query="pnpm", dry_run=False, confirm_token=stale_token
    )
    assert "confirm_token" in rejected
    assert len(_rows(services)) == 2

    fresh_preview = await delete.execute(query="pnpm", dry_run=True)
    fresh_token = fresh_preview.split("confirm_token=")[1].strip()
    assert fresh_token != stale_token

    deleted = await delete.execute(query="pnpm", dry_run=False, confirm_token=fresh_token)
    assert "已删除" in deleted
    assert len(_rows(services)) == 0


async def test_delete_refreshes_projection_and_stats(services):
    add = AddMemoryTool(services, source="manual", trigger="tool")
    await add.execute(content="用户每天早上先看 CI 失败列表", type="preference")

    memory_md = services.database.workspace / "memory" / "MEMORY.md"
    assert memory_md.exists()
    assert "CI" in memory_md.read_text(encoding="utf-8")
    assert stats_payload(services)["total"] == 1

    delete = MemoryDeleteTool(services, source="manual", trigger="tool")
    preview = await delete.execute(query="CI", dry_run=True)
    token = preview.split("confirm_token=")[1].strip()
    await delete.execute(query="CI", dry_run=False, confirm_token=token)

    assert stats_payload(services)["total"] == 0
    assert "CI" not in memory_md.read_text(encoding="utf-8")
