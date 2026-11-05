"""P1：Dream 审计表与 Dream 工具白名单。

钉住两件事：

1. **Dream 再也碰不到记忆文件**——``build_dream_tools`` 里没有任何工具能写
   ``SOUL.md`` / ``USER.md`` / ``MEMORY.md``；能写的只有 ``skills/``；
2. **审计表记账来自真实写入决策**——``add_memory`` 在 Dream 运行期间被调用时
   计数进 ``memory_dream_runs``，主 Agent 的写入不计数。
"""

from __future__ import annotations

import pytest

from nanobot.agent.memory import MemoryStore
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.dream_audit import (
    active_run,
    begin_run,
    end_run,
    list_recent_runs,
    reset_active_runs,
)
from nanobot.webui.memory_services import MemoryServices


@pytest.fixture(autouse=True)
def _clean_runs():
    reset_active_runs()
    yield
    reset_active_runs()


@pytest.fixture
def ws(tmp_path):
    path = tmp_path / "ws"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def services(ws):
    db = MemoryDatabase(ws)
    db.init_schema()
    return MemoryServices(workspace_id="default", database=db)


class TestDreamAudit:
    def test_begin_and_end_records_one_row(self, services):
        audit = begin_run(services.database, "default", trigger="cron", model="m1")
        audit.record_added()
        audit.record_added()
        audit.record_updated()
        audit.record_deleted(2)
        audit.record_skipped()
        end_run("default", status="completed")

        runs = list_recent_runs(services.database, "default")
        assert len(runs) == 1
        row = runs[0]
        assert row["status"] == "completed"
        assert row["trigger"] == "cron"
        assert row["model"] == "m1"
        assert row["added"] == 2
        assert row["updated"] == 1
        assert row["deleted"] == 2
        assert row["skipped"] == 1
        assert row["finished_at"]

    def test_failed_run_keeps_its_error(self, services):
        begin_run(services.database, "default", trigger="manual")
        end_run("default", status="failed", error="boom")

        row = list_recent_runs(services.database, "default")[0]
        assert row["status"] == "failed"
        assert "boom" in row["error"]

    def test_active_run_is_scoped_by_workspace(self, services):
        begin_run(services.database, "default", trigger="cron")
        assert active_run("default") is not None
        assert active_run("other-ws") is None

    def test_end_without_begin_is_a_noop(self, services):
        assert end_run("default", status="completed") is None
        assert list_recent_runs(services.database, "default") == []

    def test_summary_is_human_readable(self, services):
        audit = begin_run(services.database, "default", trigger="cron")
        assert audit.summary() == "没有记忆变化"
        audit.record_added()
        audit.record_updated()
        assert audit.summary() == "新增 1 条、更新 1 条"


class TestDreamToolWhitelist:
    def test_no_tool_can_write_identity_files(self, ws):
        store = MemoryStore(ws)
        tools = store.build_dream_tools()

        for name in ("write_file", "edit_file", "apply_patch"):
            tool = tools.get(name)
            assert tool is not None, f"{name} 应保留（技能与记忆文件需要）"
            allowed = getattr(tool, "_extra_write_allowed_files", []) or []
            assert store.soul_file not in allowed
            assert store.user_file not in allowed
            assert (ws / "memory" / "MEMORY.md") not in allowed
            # 写目录：skills/ + 两个记忆目录
            assert str(tool._allowed_dir).endswith("skills")
            write_dirs = [str(p) for p in getattr(tool, "_extra_write_allowed_dirs", []) or []]
            assert any(path.endswith("memory") for path in write_dirs)

    def test_draft_file_is_gone(self, ws):
        store = MemoryStore(ws)
        assert not hasattr(store, "draft_file")
        assert not (ws / "memory" / "MEMORY.md.draft").exists()

    def test_memory_db_tools_are_not_registered(self, ws):
        """Dream 是文件整理者：记忆库写入工具不再是它的出口。"""
        store = MemoryStore(ws)
        tools = store.build_dream_tools()
        assert tools.get("add_memory") is None
        assert tools.get("memory_delete") is None
        assert tools.get("memory_stats") is None
        assert tools.get("consolidate_memory") is None

    def test_legacy_draft_is_archived_not_deleted(self, ws):
        draft = ws / "memory" / "MEMORY.md.draft"
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text("候选：用户偏好简洁回复", encoding="utf-8")

        MemoryStore(ws)  # 构造即触发归档

        assert not draft.exists()
        archived = ws / "memory" / "legacy" / "MEMORY.md.draft"
        assert archived.exists()
        assert "候选" in archived.read_text(encoding="utf-8")

    def test_audit_helpers_are_on_the_store(self, ws):
        store = MemoryStore(ws)
        audit = store.begin_dream_run(trigger="manual", model="m")
        audit.record_added()
        store.end_dream_run(status="completed")

        runs = store.recent_dream_runs()
        assert len(runs) == 1
        assert runs[0]["added"] == 1


class TestDreamPrompt:
    def test_prompt_no_longer_mentions_draft(self, ws):
        store = MemoryStore(ws)
        prompt = store.default_dream_prompt()
        assert "MEMORY.md.draft" not in prompt
        assert "add_memory" not in prompt

    def test_prompt_routes_to_memory_directories(self, ws):
        store = MemoryStore(ws)
        prompt = store.default_dream_prompt()
        assert "Global memory directory" in prompt
        assert "Project memory directory" in prompt
        assert "must not write MEMORY.md" not in prompt
