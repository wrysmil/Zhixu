"""踩坑主动提示：检索与 hook 注入。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nanobot.agent.hook import AgentHookContext
from nanobot.agent.hooks.pitfall_recall import PitfallRecallHook
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.experience import record_experience
from nanobot.memory.pitfall import find_pitfall_hint


def _db(tmp_path) -> MemoryDatabase:
    db = MemoryDatabase(tmp_path, db_path=tmp_path / "state.db")
    record_experience(
        db,
        workspace_id="default",
        session_id="s1",
        title="容器启动连不上数据库",
        kind="pitfall",
        symptom="docker compose 启动后数据库连接超时",
        root_cause="没有等待 MySQL 健康检查",
        fix="depends_on 加 service_healthy",
    )
    return db


def test_find_pitfall_hint_returns_fix(tmp_path):
    hint = find_pitfall_hint(_db(tmp_path), "数据库连接超时", workspace_ids=("default",))
    assert hint is not None
    assert "service_healthy" in hint
    assert len(hint) <= 300


def test_find_pitfall_hint_ignores_unrelated_error(tmp_path):
    assert (
        find_pitfall_hint(_db(tmp_path), "测试断言失败 assert 1 == 2", workspace_ids=("default",))
        is None
    )


def _context(messages):
    return AgentHookContext(iteration=1, messages=messages)


@pytest.mark.asyncio
async def test_hook_appends_hint_to_matching_tool_message():
    hook = PitfallRecallHook(lambda text: "修复：等待健康检查" if "超时" in text else None)
    messages = [
        {"role": "tool", "tool_call_id": "c1", "content": "数据库连接超时"},
        {"role": "tool", "tool_call_id": "c2", "content": "ok"},
    ]
    context = _context(messages)
    await hook.on_execute_tool_error(
        context, SimpleNamespace(id="c1"), None, None, "数据库连接超时"
    )
    await hook.after_iteration(context)

    assert "经验库提示" in messages[0]["content"]
    assert "等待健康检查" in messages[0]["content"]
    assert messages[1]["content"] == "ok"


@pytest.mark.asyncio
async def test_hook_caps_hints_per_turn():
    hook = PitfallRecallHook(lambda _text: "提示")
    messages = [
        {"role": "tool", "tool_call_id": f"c{i}", "content": f"error {i}"}
        for i in range(3)
    ]
    context = _context(messages)
    for i in range(3):
        await hook.on_execute_tool_error(
            context, SimpleNamespace(id=f"c{i}"), None, None, f"error {i}"
        )
    await hook.after_iteration(context)

    hinted = [m for m in messages if "经验库提示" in m["content"]]
    assert len(hinted) == 2
