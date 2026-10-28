"""Session workbench: persisted plans, scoped memory and safe read projection."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request

from nanobot.agent.tools.context import RequestContext, ToolContext, request_context
from nanobot.agent.tools.loader import ToolLoader
from nanobot.config.schema import ToolsConfig
from nanobot.session.manager import SessionManager


def plan_tool(manager):
    from nanobot.agent.tools.update_plan import UpdatePlanTool

    return UpdatePlanTool(manager)


@pytest.mark.asyncio
async def test_plan_persists_only_current_session_and_does_not_authorize_goal(tmp_path):
    manager = SessionManager(tmp_path)
    tool = plan_tool(manager)
    ctx = RequestContext(channel="websocket", chat_id="a", session_key="websocket:a")
    with request_context(ctx):
        result = await tool.execute(steps=[{"title": "检查布局", "status": "in_progress"}])
    assert not getattr(result, "is_error", False)
    persisted = SessionManager(tmp_path).read_session_snapshot("websocket:a")
    assert persisted is not None
    assert persisted.metadata["workbench_plan"]["steps"][0]["title"] == "检查布局"
    assert "goal_state" not in persisted.metadata
    assert manager.read_session_snapshot("websocket:b") is None
    previous_id = persisted.metadata["workbench_plan"]["steps"][0]["id"]
    with request_context(ctx):
        await tool.execute(steps=[{"title": "检查布局", "status": "completed"}])
    assert manager.get_or_create(ctx.session_key).metadata["workbench_plan"]["steps"][0]["id"] == previous_id


@pytest.mark.asyncio
async def test_plan_requires_context_validates_and_rolls_back_failed_save(tmp_path, monkeypatch):
    manager = SessionManager(tmp_path)
    tool = plan_tool(manager)
    assert getattr(await tool.execute(steps=[]), "is_error", False)
    session = manager.get_or_create("websocket:a")
    session.metadata["workbench_plan"] = {"steps": [], "explanation": "old"}
    manager.save(session)
    ctx = RequestContext(channel="websocket", chat_id="a", session_key=session.key)
    with request_context(ctx):
        assert getattr(await tool.execute(steps=[{"title": " ", "status": "completed"}]), "is_error", False)
        assert getattr(await tool.execute(steps=[{"title": "x", "status": "done"}]), "is_error", False)
        assert getattr(await tool.execute(steps=[{"title": "x", "status": "pending"}] * 51), "is_error", False)
        monkeypatch.setattr(manager, "save", MagicMock(side_effect=OSError("private path")))
        failed = await tool.execute(steps=[{"title": "new", "status": "pending"}])
    assert getattr(failed, "is_error", False)
    assert "private path" not in str(failed)
    assert session.metadata["workbench_plan"] == {"steps": [], "explanation": "old"}


def test_plan_auto_discovery_and_construction(tmp_path):
    manager = SessionManager(tmp_path)
    candidates = [cls for cls in ToolLoader().discover() if getattr(cls, "name", None) == "update_plan"]
    assert len(candidates) == 1
    context = ToolContext(config=ToolsConfig(), workspace=str(tmp_path), sessions=manager)
    assert candidates[0].enabled(context)
    assert candidates[0].create(context).name == "update_plan"


@pytest.mark.asyncio
async def test_plan_read_after_reload_does_not_write_or_clear(tmp_path, monkeypatch):
    manager = SessionManager(tmp_path)
    ctx = RequestContext(channel="websocket", chat_id="a", session_key="websocket:a")
    with request_context(ctx):
        await plan_tool(manager).execute(steps=[{"title": "压缩后继续", "status": "pending"}])
    reloaded = SessionManager(tmp_path)
    save = MagicMock(side_effect=AssertionError("read must not write"))
    monkeypatch.setattr(reloaded, "save", save)
    with request_context(ctx):
        result = await plan_tool(reloaded).execute()
    assert "压缩后继续" in str(result)
    assert "pending" in str(result)
    save.assert_not_called()


def test_workbench_projects_current_session_memory_and_capabilities(tmp_path):
    from nanobot.webui.session_workbench import session_workbench_payload

    manager = SessionManager(tmp_path)
    session = manager.get_or_create("websocket:a")
    session.metadata["workbench_plan"] = {
        "steps": [{"id": "s1", "title": "修复", "status": "completed"}],
        "explanation": "先修复后验收", "updated_at": "now",
    }
    session.messages = [{"role": "assistant", "tool_calls": [
        {"function": {"name": "mcp_file_server_read", "arguments": "{}"}},
        {"id": "read-skill", "function": {"name": "read_file", "arguments": json.dumps({"path": "skills/design/SKILL.md"})}},
    ]}, {"role": "tool", "tool_call_id": "read-skill", "content": "# Design skill"}]
    memory = SimpleNamespace(
        fetch_scratchpad=MagicMock(return_value={"scratchpad": {"current_focus": "修布局", "content": "工作上下文", "updated_at": "now"}}),
        list_episodes=MagicMock(return_value={"items": [{"id": "ep", "linked_memory_ids": ["m1"]}]}),
        fetch_memory=MagicMock(return_value={"memory": {"id": "m1", "content": "偏好简洁", "updated_at": "now"}}),
    )
    executions = SimpleNamespace(list_executions=MagicMock(return_value={"executions": [
        {"invocationId": "inv", "agentName": "设计", "status": "running", "lastAction": "检查布局"}
    ]}))
    result = session_workbench_payload(session, memory_operations=memory,
        execution_operations=executions,
        skills=[{"name": "design", "source": "workspace", "enabled": True, "available": True}],
        always_skills=[], mcp_status={"file_server": "connected"})
    assert result["tasks"][0]["source"] == "plan"
    assert result["plan"]["steps"] == result["tasks"]
    assert {m["kind"] for m in result["memories"]} == {"working", "long_term"}
    assert all(m["persisted"] for m in result["memories"])
    memory.fetch_scratchpad.assert_called_once_with(user_id="a")
    memory.list_episodes.assert_called_once_with(session_id=session.key, limit=20)
    memory.fetch_memory.assert_called_once_with("m1")
    executions.list_executions.assert_called_once_with(chat_id="a", limit=20)
    assert result["agents"][0]["invocation_id"] == "inv"
    assert result["skills"][0]["status"] == "used"
    assert result["mcp"] == [{"name": "file_server", "status": "connected", "calls": 1, "successes": None, "failures": None}]
    assert "arguments" not in json.dumps(result)


def test_workbench_unavailable_memory_is_explicit_and_bounded(tmp_path):
    from nanobot.webui.session_workbench import session_workbench_payload

    session = SessionManager(tmp_path).get_or_create("websocket:a")
    session.metadata["goal_state"] = {"objective": "目标", "status": "completed"}
    memory = SimpleNamespace(fetch_scratchpad=MagicMock(side_effect=RuntimeError("secret")),
        list_episodes=MagicMock(return_value={"items": []}))
    result = session_workbench_payload(session, memory_operations=memory)
    assert result["goal"]["status"] == "completed"
    assert result["tasks"][0]["status"] == "completed"
    assert result["limitations"]
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("workspace_id", ["default", "ws-a"])
def test_workbench_memory_items_carry_workspace(tmp_path, workspace_id):
    from functools import partial

    from nanobot.memory.models import Episode, Memory, ScratchpadEntry
    from nanobot.memory.repository import add_episode, add_memory, upsert_scratchpad
    from nanobot.webui import memory_api
    from nanobot.webui.memory_services import MemoryServices
    from nanobot.webui.session_workbench import session_workbench_payload

    services = MemoryServices.for_workspace("default", tmp_path)
    with services.database.connect() as conn:
        for key in ("a", "b"):
            add_memory(conn, Memory(id="memory-" + key, content="memory " + key,
                                   created_at="now", workspace_id=workspace_id))
            add_episode(conn, Episode(id="episode-" + key, session_id="websocket:" + key,
                summary="episode " + key, started_at="now", ended_at="now", linked_memory_ids=["memory-" + key]))
            upsert_scratchpad(conn, ScratchpadEntry(user_id=key, workspace_id="default",
                updated_at="now", current_focus="focus " + key))
    operations = SimpleNamespace(
        fetch_scratchpad=partial(memory_api.scratchpad_payload, services),
        list_episodes=partial(memory_api.list_episodes_payload, services),
        fetch_memory=partial(memory_api.fetch_memory_payload, services),
    )
    session = SessionManager(tmp_path).get_or_create("websocket:a")
    result = session_workbench_payload(session, memory_operations=operations)
    assert [row["content"] for row in result["memories"]] == ["focus a", "memory a"]
    assert "memory b" not in json.dumps(result)
    assert "focus b" not in json.dumps(result)
    assert result["memories"][0]["workspace_id"] == "default"
    assert result["memories"][0]["is_global"] is True
    assert result["memories"][1]["workspace_id"] == workspace_id
    assert result["memories"][1]["is_global"] is (workspace_id == "default")


def test_workbench_lists_memories_extracted_from_current_session(tmp_path):
    """抽取产出、但 episode 没有关联的记忆也必须出现在工作台。

    现实里 ``episode.linked_memory_ids`` 常为空（模型不填 subject），
    工作台必须按 ``provenance.session_id`` 反查，否则恒显示「No memories linked」。
    """
    from functools import partial

    from nanobot.memory.models import Memory
    from nanobot.memory.repository import add_memory
    from nanobot.webui import memory_api
    from nanobot.webui.memory_services import MemoryServices
    from nanobot.webui.session_workbench import session_workbench_payload

    services = MemoryServices.for_workspace("default", tmp_path)
    with services.database.connect() as conn:
        add_memory(conn, Memory(
            id="m-mine", content="本会话抽出的约定", created_at="now",
            workspace_id="default",
            metadata={"provenance": {"session_id": "websocket:a"}},
        ))
        add_memory(conn, Memory(
            id="m-other-session", content="别的会话的记忆", created_at="now",
            workspace_id="default",
            metadata={"provenance": {"session_id": "websocket:b"}},
        ))
        add_memory(conn, Memory(
            id="m-other-workspace", content="别的工作区的记忆", created_at="now",
            workspace_id="ws-x",
            metadata={"provenance": {"session_id": "websocket:a"}},
        ))
    operations = SimpleNamespace(
        fetch_scratchpad=partial(memory_api.scratchpad_payload, services),
        list_episodes=partial(memory_api.list_episodes_payload, services),
        fetch_memory=partial(memory_api.fetch_memory_payload, services),
        list_memories_by_session=partial(
            memory_api.list_memories_by_session_payload, services
        ),
    )
    session = SessionManager(tmp_path).get_or_create("websocket:a")

    result = session_workbench_payload(session, memory_operations=operations)

    long_term = [row for row in result["memories"] if row["kind"] == "long_term"]
    assert [row["content"] for row in long_term] == ["本会话抽出的约定"]
    assert {row["source"] for row in long_term} == {"session:websocket:a"}
    assert long_term[0]["workspace_id"] == "default"
    assert long_term[0]["is_global"] is True
    rendered = json.dumps(result, ensure_ascii=False)
    assert "别的会话的记忆" not in rendered
    assert "别的工作区的记忆" not in rendered


def test_projection_bounds_historical_records_and_content(tmp_path):
    from nanobot.webui.session_workbench import session_workbench_payload

    session = SessionManager(tmp_path).get_or_create("websocket:a")
    session.metadata["workbench_plan"] = {"steps": [
        {"id": str(i), "title": "x" * 1000, "status": "pending"} for i in range(100)
    ]}
    memory = SimpleNamespace(
        fetch_scratchpad=lambda **_: {"scratchpad": {"content": "x" * 10000}},
        list_episodes=lambda **_: {"items": [{"id": "ep", "linked_memory_ids": [str(i) for i in range(100)]}]},
        fetch_memory=lambda mid: {"memory": {"id": mid, "content": "y" * 10000}},
    )
    result = session_workbench_payload(session, memory_operations=memory)
    assert len(result["tasks"]) == 50
    assert all(len(row["title"]) <= 300 for row in result["tasks"])
    assert len(result["memories"]) == 20
    assert all(len(row["content"]) <= 4000 for row in result["memories"])


def test_mcp_calls_use_real_sanitized_server_prefix(tmp_path):
    from nanobot.agent.tools.mcp import _sanitize_mcp_tool_name
    from nanobot.webui.session_workbench import session_workbench_payload

    session = SessionManager(tmp_path).get_or_create("websocket:a")
    name = "file.server 中文"
    session.messages = [{"role": "assistant", "tool_calls": [
        {"function": {"name": _sanitize_mcp_tool_name(f"mcp_{name}_read")}}
    ]}]
    result = session_workbench_payload(session, mcp_status={name: "configured"})
    assert result["mcp"] == [{"name": name, "status": "configured", "calls": 1,
                              "successes": None, "failures": None}]


@pytest.mark.asyncio
async def test_workbench_get_auth_namespace_missing_and_real_persisted_plan(tmp_path):
    from nanobot.webui.ws_http import GatewayHTTPHandler

    manager = SessionManager(tmp_path)
    session = manager.get_or_create("websocket:a")
    manager.save(session)
    with request_context(RequestContext(channel="websocket", chat_id="a", session_key=session.key)):
        await plan_tool(manager).execute(steps=[{"title": "真实计划", "status": "pending"}])
    handler = GatewayHTTPHandler.__new__(GatewayHTTPHandler)
    handler.session_manager = manager
    handler.check_api_token = lambda request: request.headers.get("Authorization") == "Bearer tok"
    handler.memory_operations = None
    handler.subagent_execution_operations = None
    handler._mcp_runtime_status = None
    handler.skills_workspace_path = tmp_path
    handler.disabled_skills = set()
    handler.settings = SimpleNamespace(config=SimpleNamespace(load=lambda: SimpleNamespace(
        tools=SimpleNamespace(mcp_servers={})
    )))
    for key, token, status in [(session.key, "bad", 401), ("telegram:a", "tok", 404), ("websocket:missing", "tok", 404), (session.key, "tok", 200)]:
        encoded = quote(key, safe="")
        request = Request(f"/api/sessions/{encoded}/workbench", Headers([("Authorization", f"Bearer {token}")]))
        response = await handler._dispatch_session_routes(request, request.path)
        assert response.status_code == status
        if status == 200:
            assert json.loads(response.body)["tasks"][0]["title"] == "真实计划"
