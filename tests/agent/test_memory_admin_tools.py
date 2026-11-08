"""P1：记忆写入工具（add_memory / memory_delete / memory_stats / consolidate_memory）。

重点钉住三件事：

1. **写的是库，不是文件**——工具返回后 ``memories`` 表里有行，且
   ``MEMORY.md`` / ``USER.md`` 由 lifecycle 重算，而不是被工具直接改；
2. **删除两次确认**——没有 ``confirm_token`` 一律拒绝；``dry_run=True``
   不产生任何写入；
3. **幂等**——同一内容重复 ``add_memory`` 不新增行。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nanobot.agent.tools.memory_admin import (
    AddMemoryTool,
    ConsolidateMemoryTool,
    MemoryDeleteTool,
    MemoryStatsTool,
)
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.repository import add_memory, get_memory, list_memories, search_memories
from nanobot.webui.memory_services import MemoryServices

pytestmark = pytest.mark.asyncio


@pytest.fixture
def tmp_ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    return ws


@pytest.fixture
def services(tmp_ws):
    db = MemoryDatabase(tmp_ws)
    db.init_schema()
    return MemoryServices(workspace_id="test-ws", database=db)


@pytest.fixture(autouse=True)
def _fresh_lifecycle_singletons():
    """清掉上个用例留下的 MemoryLifecycle 单例。

    ``for_workspace`` 按 ``workspace_id`` 缓存实例，而所有用例共用同一个 id
    （``test-ws``）却各有各的 tmp 目录——不清就会写到上一个用例的工作区里。
    """
    from nanobot.memory.lifecycle import MemoryLifecycle

    MemoryLifecycle._instances.clear()
    MemoryLifecycle._last_refresh_iso.clear()
    MemoryLifecycle._last_refresh_trigger.clear()
    yield
    MemoryLifecycle._instances.clear()


def _stale_memory(*, workspace_id: str) -> Memory:
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    return Memory(
        id=f"stale-{workspace_id}",
        content=f"{workspace_id} 两个月没用的普通事实",
        created_at=old,
        updated_at=old,
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        source="manual",
        importance_score=0.3,
        decay_rate=0.1,
        last_accessed_at=None,
        workspace_id=workspace_id,
    )


def _tool(cls, services):
    return cls(services, source="manual", trigger="tool")


# ---------------------------------------------------------------------------
# add_memory
# ---------------------------------------------------------------------------


class TestAddMemory:
    async def test_write_lands_in_the_store(self, services):
        tool = _tool(AddMemoryTool, services)
        out = await tool.execute(content="用户不吃香菜", type="preference")

        assert "已记住" in out
        with services.database.connect() as conn:
            rows = list_memories(conn, workspace_id="test-ws")
        assert len(rows) == 1
        assert rows[0].content == "用户不吃香菜"
        assert rows[0].type.value == "preference"
        assert rows[0].source == "manual"

    async def test_search_can_recall_it(self, services):
        tool = _tool(AddMemoryTool, services)
        await tool.execute(content="用户不吃香菜", type="preference")

        with services.database.connect() as conn:
            hits = search_memories(conn, "香菜", workspace_id="test-ws")
        assert [m.content for m in hits] == ["用户不吃香菜"]

    async def test_duplicate_content_does_not_add_a_row(self, services):
        tool = _tool(AddMemoryTool, services)
        first = await tool.execute(content="用户不吃香菜", type="preference")
        second = await tool.execute(content="用户不吃香菜", type="preference")

        assert "已记住" in first
        assert "已经在记忆库里" in second
        with services.database.connect() as conn:
            rows = list_memories(conn, workspace_id="test-ws")
        assert len(rows) == 1

    async def test_explicit_retention_long_term(self, services):
        tool = _tool(AddMemoryTool, services)
        await tool.execute(
            content="以后都用 pnpm", type="rule", retention="long_term", importance=0.9
        )
        with services.database.connect() as conn:
            rows = list_memories(conn, workspace_id="test-ws")
        assert rows[0].priority.value == "long_term"
        assert rows[0].expires_at is None
        assert rows[0].importance_score == 0.9

    async def test_session_retention_expires(self, services):
        tool = _tool(AddMemoryTool, services)
        await tool.execute(content="先记一下待会儿要用", retention="session")

        with services.database.connect() as conn:
            rows = list_memories(conn, workspace_id="test-ws")
        assert rows[0].priority.value == "short_term"
        assert rows[0].expires_at is not None

    async def test_invalid_type_reports_the_reason(self, services):
        tool = _tool(AddMemoryTool, services)
        out = await tool.execute(content="x", type="nonsense")

        assert out.is_error
        assert "type 必须是" in out
        with services.database.connect() as conn:
            assert list_memories(conn, workspace_id="test-ws") == []

    async def test_empty_content_is_rejected(self, services):
        tool = _tool(AddMemoryTool, services)
        out = await tool.execute(content="   ")
        assert out.is_error
        assert "content 不能为空" in out

    async def test_missing_services_reports_unavailable(self, tmp_ws):
        tool = _tool(AddMemoryTool, None)
        out = await tool.execute(content="用户不吃香菜")
        assert out.is_error
        assert "记忆库不可用" in out

    async def test_provenance_records_the_origin(self, services):
        tool = _tool(AddMemoryTool, services)
        await tool.execute(content="用户不吃香菜", type="preference")

        with services.database.connect() as conn:
            rows = list_memories(conn, workspace_id="test-ws")
        provenance = rows[0].metadata.get("provenance", {})
        assert provenance.get("origin_kind") == "manual"
        assert provenance.get("trigger_reason") == "tool"

    async def test_dream_source_is_labelled_consolidation(self, services):
        tool = AddMemoryTool(services, source="consolidation", trigger="dream")
        await tool.execute(content="项目用 pnpm 管包", type="fact")

        with services.database.connect() as conn:
            rows = list_memories(conn, workspace_id="test-ws")
        assert rows[0].source == "consolidation"
        assert rows[0].metadata["provenance"]["origin_kind"] == "ai"


# ---------------------------------------------------------------------------
# memory_delete
# ---------------------------------------------------------------------------


class TestMemoryDelete:
    async def _seed(self, services, *, count=2):
        tool = _tool(AddMemoryTool, services)
        for i in range(count):
            await tool.execute(content=f"用户不吃香菜{i}", type="preference")

    async def test_dry_run_previews_without_deleting(self, services):
        await self._seed(services)
        tool = _tool(MemoryDeleteTool, services)

        out = await tool.execute(query="香菜", dry_run=True)

        assert "将删除" in out
        assert "confirm_token=" in out
        with services.database.connect() as conn:
            assert len(list_memories(conn, workspace_id="test-ws")) == 2

    async def test_delete_without_token_is_refused(self, services):
        await self._seed(services)
        tool = _tool(MemoryDeleteTool, services)

        out = await tool.execute(query="香菜", dry_run=False)

        assert out.is_error
        assert "confirm_token" in out
        with services.database.connect() as conn:
            assert len(list_memories(conn, workspace_id="test-ws")) == 2

    async def test_delete_with_token_succeeds(self, services):
        await self._seed(services)
        tool = _tool(MemoryDeleteTool, services)

        preview = await tool.execute(query="香菜", dry_run=True)
        token = preview.split("confirm_token=")[1].strip()
        out = await tool.execute(query="香菜", dry_run=False, confirm_token=token)

        assert "已删除 2 条" in out
        with services.database.connect() as conn:
            assert list_memories(conn, workspace_id="test-ws") == []

    async def test_stale_token_is_refused(self, services):
        await self._seed(services)
        tool = _tool(MemoryDeleteTool, services)

        preview = await tool.execute(query="香菜", dry_run=True)
        token = preview.split("confirm_token=")[1].strip()
        # 再写一条，候选集变了 → 同一个 token 不再匹配。
        await _tool(AddMemoryTool, services).execute(content="用户不吃香菜新", type="preference")

        out = await tool.execute(query="香菜", dry_run=False, confirm_token=token)

        assert out.is_error
        assert "不匹配" in out

    async def test_no_match_deletes_nothing(self, services):
        await self._seed(services)
        tool = _tool(MemoryDeleteTool, services)
        out = await tool.execute(query="完全不相关的词")

        assert "没有找到" in out
        with services.database.connect() as conn:
            assert len(list_memories(conn, workspace_id="test-ws")) == 2


# ---------------------------------------------------------------------------
# memory_stats / consolidate_memory
# ---------------------------------------------------------------------------


class TestStatsAndConsolidate:
    async def test_stats_reports_counts(self, services):
        add = _tool(AddMemoryTool, services)
        await add.execute(content="用户不吃香菜", type="preference")
        await add.execute(content="项目用 pnpm", type="fact")

        out = await _tool(MemoryStatsTool, services).execute()

        assert "记忆库共 2 条" in out
        assert "preference 1" in out
        assert "fact 1" in out

    async def test_consolidate_regenerates_projection_files(self, services, tmp_ws):
        add = _tool(AddMemoryTool, services)
        await add.execute(content="用户不吃香菜", type="preference")

        out = await _tool(ConsolidateMemoryTool, services).execute()

        assert "MEMORY.md" in out
        assert "整理完成" in out
        assert (tmp_ws / "memory" / "MEMORY.md").exists()

    async def test_consolidate_archives_stale_memories(self, services, tmp_ws):
        stale = _stale_memory(workspace_id=services.workspace_id)
        with services.database.connect() as conn:
            add_memory(conn, stale)

        out = await _tool(ConsolidateMemoryTool, services).execute()

        assert "归档" in out
        assert (tmp_ws / "memory" / "MEMORY.md").exists()
        with services.database.connect() as conn:
            stored = get_memory(conn, stale.id)
        assert stored is not None
        assert stored.expires_at is not None

    async def test_consolidate_covers_current_project_scope(
        self, services, monkeypatch, tmp_path
    ):
        from types import SimpleNamespace

        from nanobot.agent.tools import memory_admin

        project = tmp_path / "proj"
        project.mkdir()
        monkeypatch.setattr(
            memory_admin,
            "current_workspace_scope",
            lambda: SimpleNamespace(project_path=project),
        )
        monkeypatch.setattr(memory_admin, "memory_workspace_id", lambda _path: "proj-ws")
        stale = _stale_memory(workspace_id="proj-ws")
        with services.database.connect() as conn:
            add_memory(conn, stale)

        out = await _tool(ConsolidateMemoryTool, services).execute()

        assert "项目" in out
        with services.database.connect() as conn:
            stored = get_memory(conn, stale.id)
        assert stored is not None
        assert stored.expires_at is not None

    async def test_consolidation_workspace_ids_dedupe_global(self, services, monkeypatch):
        from nanobot.agent.tools import memory_admin

        monkeypatch.setattr(
            memory_admin,
            "current_workspace_scope",
            lambda: type("S", (), {"project_path": "/tmp/whatever"})(),
        )
        monkeypatch.setattr(
            memory_admin, "memory_workspace_id", lambda _path: services.workspace_id
        )

        assert memory_admin._consolidation_workspace_ids(services) == [services.workspace_id]

    async def test_stats_and_consolidate_need_services(self, tmp_ws):
        assert (await _tool(MemoryStatsTool, None).execute()).is_error
        assert (await _tool(ConsolidateMemoryTool, None).execute()).is_error


# ---------------------------------------------------------------------------
# 门控与 schema
# ---------------------------------------------------------------------------


class TestToolWiring:
    def test_tools_are_read_only_only_for_stats(self):
        assert _tool(MemoryStatsTool, None).read_only is True
        assert _tool(AddMemoryTool, None).read_only is False

    def test_gate_closed_hides_the_tools(self):
        from nanobot.agent.tools.context import ToolContext

        ctx = ToolContext(
            config=None,
            workspace=".",
            attributes={"memory_write_enabled": lambda: False},
        )
        assert AddMemoryTool.enabled(ctx) is False
        assert MemoryDeleteTool.enabled(ctx) is False

    def test_gate_open_shows_the_tools(self):
        from nanobot.agent.tools.context import ToolContext

        ctx = ToolContext(
            config=None,
            workspace=".",
            attributes={"memory_write_enabled": lambda: True},
        )
        assert AddMemoryTool.enabled(ctx) is True
        assert MemoryStatsTool.enabled(ctx) is True

    def test_schema_declares_required_params(self):
        params = AddMemoryTool(None).parameters
        assert params["required"] == ["content"]
        assert set(params["properties"]) == {
            "content",
            "type",
            "retention",
            "importance",
            "subject",
            "predicate",
        }
        delete_params = MemoryDeleteTool(None).parameters
        assert delete_params["required"] == ["query"]
        assert delete_params["properties"]["dry_run"]["default"] is True
