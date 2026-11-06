"""经验库类型扩展：kind/status 写入、校验与过滤。"""

from __future__ import annotations

import json

import pytest

from nanobot.agent.tools.experience import ExperienceRecordTool
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.experience import list_experiences, record_experience
from nanobot.webui.memory_services import MemoryServices


def _db(tmp_path) -> MemoryDatabase:
    return MemoryDatabase(tmp_path, db_path=tmp_path / "state.db")


def _record(db: MemoryDatabase, kind: str, **kwargs):
    base = {
        "workspace_id": "default",
        "session_id": "s1",
        "title": f"{kind} title",
        "kind": kind,
    }
    return record_experience(db, **base, **kwargs)


def test_kind_specific_required_fields(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(ValueError, match="capability"):
        _record(db, "skill", steps=["do it"])
    with pytest.raises(ValueError, match="步骤"):
        _record(db, "skill", capability="deploy")
    with pytest.raises(ValueError, match="symptom"):
        _record(db, "pitfall", fix="restart")
    with pytest.raises(ValueError, match="fix"):
        _record(db, "pitfall", symptom="db timeout")
    with pytest.raises(ValueError, match="context"):
        _record(db, "decision", choice="use postgres")
    with pytest.raises(ValueError, match="choice"):
        _record(db, "decision", context="database choice")
    with pytest.raises(ValueError, match="pointer"):
        _record(db, "reference")


def test_skill_stores_steps_and_metadata(tmp_path):
    db = _db(tmp_path)
    episode = _record(
        db,
        "skill",
        capability="初始化一个 Python 项目",
        prerequisites=["已安装 uv"],
        steps=["uv init", "uv add pytest", "uv run pytest"],
        verification="pytest 通过",
        version="1",
    )
    rows = list_experiences(db, workspace_ids=["default"], kind="skill")
    assert [row.id for row in rows] == [episode.id]
    row = rows[0]
    assert row.kind == "skill"
    assert row.status == "active"
    assert row.version == "1"
    assert "能力：初始化一个 Python 项目" in row.summary
    assert "1. uv init" in row.summary
    assert row.metadata["steps"] == ["uv init", "uv add pytest", "uv run pytest"]


def test_pitfall_and_reference_summaries(tmp_path):
    db = _db(tmp_path)
    _record(
        db,
        "pitfall",
        symptom="docker compose 启动后数据库连接超时",
        root_cause="没有等待 MySQL 健康检查",
        fix="depends_on 加 service_healthy",
    )
    _record(
        db,
        "reference",
        pointer="docs/ops.md",
        what_for="上线检查清单",
    )
    pitfall = list_experiences(db, kind="pitfall")[0]
    reference = list_experiences(db, kind="reference")[0]
    assert "症状：docker compose" in pitfall.summary
    assert "修复：depends_on 加 service_healthy" in pitfall.summary
    assert "指针：docs/ops.md" in reference.summary
    assert reference.metadata["pointer"] == "docs/ops.md"


def test_kind_and_status_filters(tmp_path):
    db = _db(tmp_path)
    _record(db, "experience", situation="一次性任务", solution="重跑")
    _record(db, "pitfall", symptom="报错 A", fix="修 A")
    deprecated = _record(
        db, "skill", capability="旧流程", steps=["a"], status="deprecated"
    )
    assert [row.kind for row in list_experiences(db, kind="pitfall")] == ["pitfall"]
    assert [row.id for row in list_experiences(db, kind="skill", status="active")] == []
    assert [row.id for row in list_experiences(db, kind="skill")] == [deprecated.id]
    assert len(list_experiences(db)) == 3


def test_outcome_filter_ignores_non_outcome_kinds(tmp_path):
    db = _db(tmp_path)
    _record(db, "experience", situation="失败任务", outcome="failed", solution="修复")
    _record(db, "skill", capability="步骤", steps=["a"])
    rows = list_experiences(db, outcome="completed")
    assert all(row.kind in {"experience", "pitfall"} for row in rows)


def test_legacy_row_without_kind_defaults_to_experience(tmp_path):
    db = _db(tmp_path)
    episode = _record(db, "experience", situation="旧数据", solution="旧解法")
    with db.connect() as conn:
        conn.execute(
            "UPDATE episodes SET metadata = ? WHERE id = ?",
            (json.dumps({"title": "旧数据"}), episode.id),
        )
    rows = list_experiences(db, kind="experience")
    assert [row.id for row in rows] == [episode.id]
    assert rows[0].kind == "experience"
    assert rows[0].status == "active"


async def test_record_tool_reports_kind(tmp_path):
    db = _db(tmp_path)
    services = MemoryServices(workspace_id="default", database=db)
    tool = ExperienceRecordTool(services)
    result = await tool.execute(
        kind="pitfall",
        title="连接超时",
        symptom="数据库连接超时",
        fix="等待健康检查",
    )
    assert "已记录踩坑" in result
    assert list_experiences(db, kind="pitfall")[0].title == "连接超时"


async def test_memory_search_tool_passes_kind_filters():
    from nanobot.agent.tools.memory_search import MemorySearchTool

    captured: dict[str, object] = {}

    class _Engine:
        async def retrieve(self, **kwargs):
            captured.update(kwargs)
            return "命中"

    tool = MemorySearchTool(
        retrieval_engine_provider=lambda: _Engine(),
        workspace_ids_provider=lambda _session: ("default",),
    )
    out = await tool.execute(query="连接超时", kind="pitfall", status="active", outcome="failed")
    assert out == "命中"
    assert captured["kind"] == "pitfall"
    assert captured["status"] == "active"
    assert captured["outcome"] == "failed"


async def test_memory_search_tool_falls_back_without_engine(tmp_path):
    from nanobot.agent.tools.memory_search import MemorySearchTool

    db = _db(tmp_path)
    _record(db, "pitfall", symptom="数据库连接超时", fix="等待健康检查")
    services = MemoryServices(workspace_id="default", database=db)
    tool = MemorySearchTool(
        retrieval_engine_provider=lambda: None,
        workspace_ids_provider=lambda _session: ("default",),
        services_provider=lambda: services,
    )
    out = await tool.execute(query="连接超时", kind="pitfall")
    assert "[pitfall]" in out
    assert "修复：等待健康检查" in out


def test_repository_search_episodes_filters_kind(tmp_path):
    from nanobot.memory.repository import search_episodes

    db = _db(tmp_path)
    _record(db, "experience", situation="depends_on 配置", solution="重跑")
    _record(
        db,
        "pitfall",
        symptom="depends_on 没有等待健康检查",
        root_cause="compose 默认只等容器启动",
        fix="加 service_healthy",
    )
    with db.connect() as conn:
        rows = search_episodes(conn, entity="depends_on", kind="pitfall")
    assert len(rows) == 1
    assert json.loads(rows[0].metadata)["kind"] == "pitfall"
