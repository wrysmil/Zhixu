"""Bounded, read-only projection of real session workbench state."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from typing import Any, cast

from loguru import logger

from nanobot.agent.tools.mcp import _tool_prefix  # pyright: ignore[reportPrivateUsage]
from nanobot.agent.tools.update_plan import MAX_PLAN_STEPS, PLAN_KEY, PLAN_STATUSES
from nanobot.memory.provenance import memory_visibility_ids, session_memory_workspace_id
from nanobot.memory.scratchpad_writer import ScratchpadWriter
from nanobot.session.goal_state import goal_state_raw, parse_goal_state
from nanobot.session.manager import Session
from nanobot.webui.memory_routes import MemorySettingsOperations
from nanobot.webui.session_identity import webui_chat_id
from nanobot.webui.subagent_execution_routes import SubagentExecutionSettingsOperations

_MAX_CONTENT = 4000
_MAX_HISTORY = 500
_MAX_CAPABILITIES = 100


def _text(value: Any, limit: int = _MAX_CONTENT) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _rows(value: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], row) for row in cast(list[Any], value)[:limit]
            if isinstance(row, dict)]


def _mapping(value: Any) -> dict[str, Any] | None:
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def _task(row: dict[str, Any]) -> dict[str, str] | None:
    title = _text(row.get("title"), 300)
    step_id = _text(row.get("id"), 64)
    status = row.get("status")
    if not title or not step_id or status not in PLAN_STATUSES:
        return None
    return {"id": step_id, "title": title, "status": status, "source": "plan"}


def _observed_tools(session: Session) -> tuple[Counter[str], set[str]]:
    """Count calls; successful Skill file reads are evidence of actual usage."""
    calls: Counter[str] = Counter()
    read_requests: dict[str, str] = {}
    used_skills: set[str] = set()
    for message in session.messages[-_MAX_HISTORY:]:
        for call in _rows(message.get("tool_calls"), 50):
            function = _mapping(call.get("function"))
            if function is None:
                continue
            name = _text(function.get("name"), 150)
            if name.startswith("mcp_"):
                calls[name] += 1
            if name != "read_file":
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, str) and len(arguments) <= 4096:
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    continue
            arguments = _mapping(arguments)
            if arguments is None:
                continue
            path = _text(arguments.get("path"), 2000).replace("\\", "/").rstrip("/")
            if path.endswith("/SKILL.md"):
                read_requests[_text(call.get("id"), 150)] = path.split("/")[-2]
        if message.get("role") == "tool":
            call_id = _text(message.get("tool_call_id"), 150)
            content = message.get("content")
            if (call_id and call_id in read_requests and not message.get("is_error")
                    and isinstance(content, str) and content.strip()
                    and not content.lstrip().startswith(("Error:", "error:"))):
                used_skills.add(read_requests[call_id])
    return calls, used_skills


def session_workbench_payload(
    session: Session, *,
    memory_operations: MemorySettingsOperations | None = None,
    execution_operations: SubagentExecutionSettingsOperations | None = None,
    skills: list[dict[str, Any]] | None = None,
    always_skills: list[str] | None = None,
    mcp_status: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Use gateway-owned services; never create stores or invoke models here."""
    limitations: list[str] = []

    def unavailable(module: str) -> None:
        limitations.append(f"{module}_unavailable")
        logger.bind(event="workbench_source_unavailable", source=module,
                    session_key=session.key).warning("Workbench source is unavailable")

    raw_plan = _mapping(session.metadata.get(PLAN_KEY))
    plan = None
    tasks: list[dict[str, str]] = []
    if raw_plan is not None:
        tasks = [task for row in _rows(raw_plan.get("steps"), MAX_PLAN_STEPS)
                 if (task := _task(row)) is not None]
        plan = {"explanation": _text(raw_plan.get("explanation"), 1000),
                "steps": tasks, "updated_at": _text(raw_plan.get("updated_at"), 80) or None}
    raw_goal = parse_goal_state(goal_state_raw(session.metadata))
    goal = None
    if isinstance(raw_goal, dict) and _text(raw_goal.get("objective")):
        goal = {"objective": _text(raw_goal.get("objective")),
                "status": _text(raw_goal.get("status"), 40)}
        for key in ("ui_summary", "recap"):
            value = _text(raw_goal.get(key), 1000)
            if value:
                goal[key] = value
        if plan is None:
            status = {"active": "in_progress", "blocked": "blocked", "completed": "completed",
                      "cancelled": "cancelled"}.get(goal["status"], "pending")
            tasks = [{"id": "sustained-goal", "title": goal["objective"][:300],
                      "status": status, "source": "goal"}]

    agents: list[dict[str, str]] = []
    if execution_operations is None:
        limitations.append("subagent_executions_unavailable")
    else:
        try:
            page = execution_operations.list_executions(chat_id=webui_chat_id(session.key) or "", limit=20)
            for row in _rows(page.get("executions"), 20):
                invocation_id = _text(row.get("invocationId"), 80)
                if invocation_id:
                    agents.append({"id": invocation_id, "invocation_id": invocation_id,
                                   "name": _text(row.get("agentName"), 100),
                                   "status": _text(row.get("status"), 40),
                                   "detail": _text(row.get("lastAction") or row.get("title"), 300)})
        except Exception:
            unavailable("subagent_executions")

    memories: list[dict[str, Any]] = []
    if memory_operations is None:
        limitations.append("memory_unavailable")
    else:
        try:
            row = _mapping(memory_operations.fetch_scratchpad(
                user_id=ScratchpadWriter.user_id_for_key(session.key),
            ).get("scratchpad"))
            if row is not None:
                parts = [_text(row.get("content")), _text(row.get("current_focus"))]
                for key in ("active_projects", "open_questions", "next_steps"):
                    items = row.get(key)
                    if isinstance(items, list):
                        parts.extend(_text(item, 300) for item in cast(list[Any], items)[:10])
                content = "\n".join(dict.fromkeys(part for part in parts if part))[:_MAX_CONTENT]
                if content:
                    memories.append({"id": "working:" + session.key, "kind": "working",
                                     "title": _text(row.get("current_focus"), 100) or "工作记忆",
                                     "content": content, "source": "session_scratchpad",
                                     "updated_at": _text(row.get("updated_at"), 80) or None,
                                     "persisted": True,
                                     "workspace_id": "default", "is_global": True})
        except Exception:
            unavailable("working_memory")
        # 本会话产出的长期记忆：优先按 provenance.session_id 反查（抽取器直接写入，
        # 带工作区可见性过滤）；episode 的 linked_memory_ids 仅作兜底——那条链路
        # 历史上因模型不填 subject 而恒为空，且原有实现按 id 直取、不带可见性判定。
        seen: set[str] = set()

        def _append_long_term(row: dict[str, Any], source: str) -> None:
            content = _text(row.get("content"))
            memory_id = _text(row.get("id"), 100)
            if not content or not memory_id or memory_id in seen or len(memories) >= 20:
                return
            seen.add(memory_id)
            workspace_id = _text(row.get("workspace_id"), 64) or "default"
            memories.append({
                "id": memory_id,
                "kind": "long_term",
                "title": content[:80],
                "content": content,
                "source": source,
                "updated_at": _text(row.get("updated_at"), 80) or None,
                "persisted": True,
                "workspace_id": workspace_id,
                "is_global": workspace_id == "default",
            })

        resolver = getattr(memory_operations, "list_memories_by_session", None)
        if resolver is not None:
            try:
                # 可见集合按**会话自身作用域**算：网关把记忆服务绑在 "default"，
                # 而记忆行按会话的项目工作区打标，用 store 作用域会漏掉本会话的记忆。
                session_workspace = session_memory_workspace_id(session)
                session_rows = resolver(
                    session_id=session.key,
                    limit=20,
                    workspace_ids=memory_visibility_ids(session_workspace),
                ).get("items", [])
                for row in _rows(session_rows, 20):
                    _append_long_term(row, "session:" + session.key)
            except Exception:
                unavailable("session_memories")

        try:
            episodes = memory_operations.list_episodes(session_id=session.key, limit=20)
            for episode in _rows(episodes.get("items"), 20):
                linked = episode.get("linked_memory_ids")
                if not isinstance(linked, list):
                    continue
                for memory_id in cast(list[Any], linked)[:20]:
                    if len(memories) >= 20:
                        break
                    if not isinstance(memory_id, str) or memory_id in seen:
                        continue
                    try:
                        row = _mapping(memory_operations.fetch_memory(memory_id).get("memory"))
                    except Exception:
                        unavailable("linked_memory")
                        continue
                    if row is not None:
                        _append_long_term(row, "episode:" + _text(episode.get("id"), 100))
        except Exception:
            unavailable("session_memories")

    calls, used_skills = _observed_tools(session)
    projected_skills: list[dict[str, str]] = []
    always = set(always_skills or [])
    for row in _rows(skills or [], _MAX_CAPABILITIES):
        name = _text(row.get("name"), 100)
        if not name or not row.get("enabled") or not row.get("available"):
            continue
        status = "used" if name in used_skills else "always_loaded" if name in always else "available"
        projected_skills.append({"name": name, "status": status, "source": _text(row.get("source"), 40)})
    if skills is None:
        limitations.append("skills_unavailable")
    limitations.append("skill_usage_tracks_successful_file_reads_only")
    if len(session.messages) > _MAX_HISTORY:
        limitations.append("tool_usage_limited_to_recent_500_messages")

    statuses = dict(list((mcp_status or {}).items())[:_MAX_CAPABILITIES])
    counts: Counter[str] = Counter()
    names_by_length = sorted(statuses, key=len, reverse=True)
    for name, count in calls.items():
        server = next((key for key in names_by_length if name.startswith(_tool_prefix(key))), name)
        counts[server] += count
    mcp = [{"name": _text(name, 150),
            "status": statuses.get(name, "observed") if statuses.get(name, "observed") in
                {"connecting", "connected", "failed", "observed", "configured"} else "unknown",
            "calls": counts[name], "successes": None, "failures": None}
           for name in sorted(set(statuses) | set(counts))[:_MAX_CAPABILITIES]]
    if mcp_status is None:
        limitations.append("mcp_connection_state_unavailable")
    if calls:
        limitations.append("mcp_call_outcomes_not_tracked")
    return {"schema_version": 1, "session_key": session.key,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
            "goal": goal, "tasks": tasks, "plan": plan, "agents": agents,
            "memories": memories, "skills": projected_skills, "mcp": mcp,
            "limitations": list(dict.fromkeys(limitations))}
