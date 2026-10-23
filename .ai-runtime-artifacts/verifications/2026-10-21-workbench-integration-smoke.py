"""仅对 artifacts 检查环境进行真实存储与 HTTP 联调，不调用模型。"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from nanobot.config.loader import load_config, set_config_path
from nanobot.session.manager import SessionManager

ROOT = Path(__file__).resolve().parent / "nanobot-ui-check"
CONFIG_PATH = ROOT / "config.json"
KEY = "websocket:workbench-inspection-20261021"
EMPTY_KEY = "websocket:workbench-empty-20261021"


async def seed() -> None:
    from nanobot.agent.tools.context import RequestContext, request_context
    from nanobot.agent.tools.update_plan import UpdatePlanTool
    from nanobot.memory.database import MemoryDatabase
    from nanobot.memory.models import Episode, Memory, ScratchpadEntry
    from nanobot.memory.repository import add_episode, add_memory, get_episode, get_memory, upsert_scratchpad
    from nanobot.subagent_executions import ExecutionRepository

    config = load_config(CONFIG_PATH)
    workspace = config.workspace_path.resolve()
    assert workspace.is_relative_to(ROOT.resolve()), "只允许使用 artifacts 内的检查工作区"
    manager = SessionManager(workspace, sessions_root=ROOT / "sessions")
    assert manager.read_session_snapshot(KEY) is not None
    with request_context(RequestContext(channel="websocket", chat_id=KEY.split(":", 1)[1], session_key=KEY)):
        result = await UpdatePlanTool(manager).execute(
            explanation="联调样例：通过真实工具持久化，未调用模型。",
            steps=[
                {"title": "核对工作台数据源（联调样例）", "status": "completed"},
                {"title": "修复预览与输入框布局（联调样例）", "status": "completed"},
                {"title": "接入会话状态（联调样例）", "status": "in_progress"},
                {"title": "检查刷新与会话隔离（联调样例）", "status": "pending"},
            ],
        )
    assert not getattr(result, "is_error", False), result
    now = datetime.now(timezone.utc).isoformat()
    db = MemoryDatabase(workspace)
    db.ensure_schema()
    with db.connect() as conn:
        episode_id = "workbench-verification-episode-20261021"
        memory_id = "workbench-verification-memory-20261021"
        if get_episode(conn, episode_id) is None:
            add_episode(conn, Episode(id=episode_id, session_id=KEY,
                summary="工作台联调样例，验证会话记忆关联。", started_at=now, ended_at=now,
                linked_memory_ids=[memory_id]))
        if get_memory(conn, memory_id) is None:
            add_memory(conn, Memory(id=memory_id, content="联调样例：右侧工作台区分任务进展、工作记忆和长期记录。",
                created_at=now, source="workbench_verification", source_episode_id=episode_id))
        upsert_scratchpad(conn, ScratchpadEntry(user_id=KEY.split(":", 1)[1], workspace_id="default",
            updated_at=now, current_focus="工作台前后端联调（样例）",
            content="这是隔离检查环境中的持久化工作记忆，不是用户正式记忆。",
            next_steps=["检查三个页签及会话切换。", "检查文件预览拉宽。"] ))
    ledger = ExecutionRepository(workspace)
    try:
        if not any(item.title == "工作台接口检查（联调样例）" for item in ledger.list(KEY.split(":", 1)[1], limit=20).items):
            execution = ledger.create(chat_id=KEY.split(":", 1)[1], parent_turn_id="workbench-verification",
                parent_tool_call_id="workbench-verification-call", name="检查任务（样例）",
                title="工作台接口检查（联调样例）", instruction_summary="本地写入的已结束检查记录，未启动模型。")
            ledger.start(execution.invocation_id)
            ledger.finish(execution.invocation_id, "succeeded", result={"summary": "本地检查记录已结束，未调用模型。"})
    finally:
        ledger.close()
    print("联调样例已写入隔离检查环境：真实计划工具、SQLite记忆、已结束执行记录。")


def read_json(path: str, token: str | None = None) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with urlopen(Request("http://127.0.0.1:8765" + path, headers=headers), timeout=20) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {"error": body}
        return error.code, payload


def verify() -> None:
    websocket = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["channels"]["websocket"]
    secret = websocket.get("tokenIssueSecret") or websocket.get("token")
    status, bootstrap = read_json("/webui/bootstrap", secret)
    assert status == 200
    token = bootstrap["api_token"]
    path = f"/api/sessions/{quote(KEY, safe='')}/workbench"
    assert read_json(path)[0] == 401
    assert read_json("/api/sessions/telegram%3Aa/workbench", token)[0] == 404
    assert read_json("/api/sessions/websocket%3Amissing-workbench-check/workbench", token)[0] == 404
    started = time.monotonic()
    status, data = read_json(path, token)
    assert status == 200, data
    assert data["session_key"] == KEY and data["schema_version"] == 1
    assert len(data["tasks"]) == 4 and data["tasks"][2]["status"] == "in_progress", data
    assert {item["kind"] for item in data["memories"]} == {"working", "long_term"}, data
    assert all(item["persisted"] for item in data["memories"])
    assert any(agent["status"] == "succeeded" for agent in data["agents"]), data
    elapsed = round((time.monotonic() - started) * 1000)
    other_status, other = read_json(f"/api/sessions/{quote(EMPTY_KEY, safe='')}/workbench", token)
    assert other_status == 200 and other["tasks"] == [] and other["memories"] == [], other
    assert other["agents"] == []
    print(json.dumps({"http_auth": "PASS", "namespace_and_missing": "PASS", "persisted_plan": "PASS",
        "scoped_memories": "PASS", "execution_ledger": "PASS", "session_isolation": "PASS",
        "elapsed_ms": elapsed, "tasks": len(data["tasks"]), "memories": len(data["memories"]),
        "skills": len(data["skills"]), "agents": len(data["agents"]), "limitations": data["limitations"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed", "verify"])
    args = parser.parse_args()
    set_config_path(CONFIG_PATH)
    if args.action == "seed":
        asyncio.run(seed())
    else:
        verify()
