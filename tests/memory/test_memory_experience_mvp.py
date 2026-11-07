"""MVP 验证：MEMORY.md 正文注入 + SQLite 经验库，两条链路互不复制。"""

from __future__ import annotations

import pytest

from nanobot.agent.context import ContextBuilder
from nanobot.agent.tools.experience import ExperienceRecordTool
from nanobot.agent.tools.memory_search import MemorySearchTool
from nanobot.agent.tools.memory_write import MemoryReadTool, MemoryWriteTool
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.decay import run_consolidation
from nanobot.memory.experience import list_experiences, record_experience
from nanobot.memory.files import MemoryFileStore, render_memory_content_block
from nanobot.webui.memory_services import MemoryServices


def _store(tmp_path):
    return MemoryFileStore(tmp_path / "global", tmp_path / "project")


def test_memory_doc_roundtrip_and_render(tmp_path):
    store = _store(tmp_path)
    store.write_memory_doc("global", "# 全局记忆\n\n## 偏好\n- 用中文回复\n")
    assert "用中文回复" in store.read_memory_doc("global")

    block = render_memory_content_block(store)
    assert "用中文回复" in block
    assert "长期记忆（正文）" in block


def test_memory_doc_cap_and_section_append(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="上限"):
        store.write_memory_doc("global", "x" * 3001)

    store.append_memory_section("project", "构建", "- 用 bun")
    store.append_memory_section("project", "模型", "- 默认 doubao")
    text = store.read_memory_doc("project")
    assert "## 构建" in text and "## 模型" in text


def test_context_builder_injects_memory_doc(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.write_memory_doc("global", "# 全局记忆\n\n## 偏好\n- 用中文回复\n")
    monkeypatch.setattr("nanobot.memory.files.global_memory_dir", lambda: tmp_path / "global")
    monkeypatch.setattr(
        "nanobot.memory.files.project_memory_dir", lambda _workspace_id: tmp_path / "project"
    )

    builder = ContextBuilder(tmp_path, memory_file_mode=True)
    block = builder._load_memory_content_block(tmp_path)
    assert "用中文回复" in block


async def test_memory_write_tool(tmp_path):
    tool = MemoryWriteTool(_store(tmp_path))
    result = await tool.execute(scope="global", section="偏好", content="- 用中文回复")
    assert "已写入" in result
    assert str(tmp_path / "global" / "MEMORY.md") in result
    assert "用中文回复" in tool.store.read_memory_doc("global")


async def test_memory_read_tool_reads_both_scopes(tmp_path):
    store = _store(tmp_path)
    store.append_memory_section("global", "偏好", "- 用中文回复")
    store.append_memory_section("project", "构建", "- 用 bun")
    store.write_memory("project", "long-note", "细节正文", description="长文")
    tool = MemoryReadTool(store)

    both = await tool.execute(scope="both")
    assert "用中文回复" in both and "用 bun" in both
    assert str(tmp_path / "global" / "MEMORY.md") in both
    assert str(tmp_path / "project" / "MEMORY.md") in both

    single = await tool.execute(scope="project", name="long-note")
    assert "细节正文" in single
    assert "未找到" in await tool.execute(scope="project", name="missing")
    # 路径穿越按普通名字规范化，不会逃出记忆目录
    assert "未找到" in await tool.execute(scope="project", name="../../etc/passwd")


def test_record_and_search_experience(tmp_path):
    db = MemoryDatabase(tmp_path, db_path=tmp_path / "state.db")
    episode = record_experience(
        db,
        workspace_id="default",
        session_id="s1",
        title="bun 构建失败",
        situation="bun run build 报模块缺失",
        action="直接重跑",
        outcome="failed",
        root_cause="依赖未安装",
        solution="bun install 后重跑",
        verification="bun run build 通过",
        tags=["bun", "build"],
    )

    rows = list_experiences(db, workspace_ids=["default"], query="bun")
    assert any(row.id == episode.id for row in rows)
    assert rows[0].root_cause == "依赖未安装"


def test_experience_workspace_isolation(tmp_path):
    db = MemoryDatabase(tmp_path, db_path=tmp_path / "state.db")
    record_experience(
        db,
        workspace_id="proj-a",
        session_id="s1",
        title="构建失败",
        situation="缺少依赖",
        action="重跑",
        outcome="failed",
    )
    assert list_experiences(db, workspace_ids=["proj-b"], query="依赖") == []


def test_consolidation_does_not_touch_experiences(tmp_path):
    db = MemoryDatabase(tmp_path, db_path=tmp_path / "state.db")
    record_experience(
        db,
        workspace_id="default",
        session_id="s1",
        title="构建失败",
        situation="缺少依赖",
        action="重跑",
        outcome="failed",
    )
    run_consolidation(db, workspace_id="default")
    with db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE source='experience'"
        ).fetchone()[0]
    assert count == 1


async def test_experience_tools(tmp_path):
    db = MemoryDatabase(tmp_path, db_path=tmp_path / "state.db")
    services = MemoryServices(workspace_id="default", database=db)

    record_tool = ExperienceRecordTool(services)
    result = await record_tool.execute(
        title="构建失败",
        situation="缺少依赖",
        action="重跑",
        outcome="failed",
        root_cause="未安装",
        solution="安装后重跑",
        tags=["bun"],
    )
    assert "已记录经验" in result

    search_tool = MemorySearchTool(
        retrieval_engine_provider=lambda: None,
        workspace_ids_provider=lambda _session: ("default",),
        services_provider=lambda: services,
    )
    output = await search_tool.execute(query="依赖", outcome="failed")
    assert "构建失败" in output
