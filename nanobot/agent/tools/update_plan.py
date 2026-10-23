"""Maintain a normal conversation plan without granting sustained-goal permission."""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, ClassVar, cast
from uuid import uuid4

from loguru import logger

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import ToolContext, current_request_context
from nanobot.agent.tools.schema import (
    ArraySchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.session.manager import SessionManager

PLAN_KEY = "workbench_plan"
PLAN_STATUSES = ("pending", "in_progress", "completed", "blocked", "cancelled")
MAX_PLAN_STEPS = 50
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _stored_steps(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], row) for row in cast(list[Any], value)[:MAX_PLAN_STEPS]
            if isinstance(row, dict)]


@tool_parameters(tool_parameters_schema(
    explanation=StringSchema("Short explanation of this plan or its update.", max_length=1000),
    steps=ArraySchema(ObjectSchema(
        id=StringSchema("Existing step ID, if known.", max_length=64),
        title=StringSchema("Concrete step to perform.", min_length=1, max_length=300),
        status=StringSchema("Current observed step status.", enum=PLAN_STATUSES),
        required=["title", "status"], additional_properties=False,
    ), description="Full replacement list; keep completed steps to show progress.", max_items=50),
))
class UpdatePlanTool(Tool):
    name: ClassVar[str] = "update_plan"
    description: ClassVar[str] = (
        "Maintain a structured plan for the current conversation. Use for multi-step work, "
        "then update step statuses as work progresses. Omit steps to read the persisted plan "
        "after context compaction. Supply the complete list to update; steps=[] clears it. "
        "This records ordinary progress only; it does not create a sustained goal, schedule "
        "background work, or authorize continued execution."
    )

    def __init__(self, sessions: SessionManager) -> None:
        self._sessions = sessions

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.sessions is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.sessions is None:
            raise ValueError("update_plan requires session storage")
        return cls(ctx.sessions)

    async def execute(
        self, steps: list[dict[str, Any]] | None = None, explanation: str = "", **kwargs: Any,
    ) -> str:
        request = current_request_context()
        if request is None or not request.session_key:
            return ToolResult.error("Error: update_plan requires the current request session.")
        if kwargs:
            return ToolResult.error("Error: update_plan accepts only steps and explanation.")
        if steps is None:
            session = self._sessions.get_cached(request.session_key)
            session = session or self._sessions.read_session_snapshot(request.session_key)
            plan = session.metadata.get(PLAN_KEY) if session is not None else None
            if not isinstance(plan, dict):
                return "No plan recorded for the current conversation."
            plan = cast(dict[str, Any], plan)
            safe_steps = [
                {key: str(row.get(key, ""))[:300] for key in ("id", "title", "status")}
                for row in _stored_steps(plan.get("steps"))
            ]
            return json.dumps({
                "explanation": str(plan.get("explanation", ""))[:1000], "steps": safe_steps,
            }, ensure_ascii=False)
        errors = self.validate_params({"steps": steps, "explanation": explanation})
        if errors:
            return ToolResult.error("Error: invalid plan: " + "; ".join(errors))
        if any(not step["title"].strip() for step in steps):
            return ToolResult.error("Error: plan step titles must not be blank.")
        supplied_ids = [step["id"] for step in steps if "id" in step]
        if any(not _ID_RE.fullmatch(value) for value in supplied_ids) or len(set(supplied_ids)) != len(supplied_ids):
            return ToolResult.error("Error: plan step IDs must be unique safe identifiers.")
        session = self._sessions.get_or_create(request.session_key)
        previous = deepcopy(session.metadata.get(PLAN_KEY))
        had_plan = PLAN_KEY in session.metadata
        old_plan = cast(dict[str, Any], previous) if isinstance(previous, dict) else {}
        ids_by_title: dict[str, list[str]] = {}
        for step in _stored_steps(old_plan.get("steps")):
            if isinstance(step.get("title"), str) and isinstance(step.get("id"), str):
                ids_by_title.setdefault(step["title"], []).append(step["id"])
        used_ids = set(supplied_ids)
        normalized: list[dict[str, str]] = []
        for step in steps:
            title = step["title"].strip()
            step_id = step.get("id")
            if step_id is None:
                matches = ids_by_title.get(title, [])
                step_id = next((value for value in matches if value not in used_ids and _ID_RE.fullmatch(value)), None)
                step_id = step_id or uuid4().hex
            used_ids.add(step_id)
            normalized.append({"id": step_id, "title": title, "status": step["status"]})
        session.metadata[PLAN_KEY] = {
            "explanation": explanation.strip(), "steps": normalized,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._sessions.save(session)
        except Exception:
            if had_plan:
                session.metadata[PLAN_KEY] = previous
            else:
                session.metadata.pop(PLAN_KEY, None)
            logger.bind(event="workbench_plan_save_failed", session_key=request.session_key).warning(
                "Conversation plan could not be persisted"
            )
            return ToolResult.error("Error: the plan could not be saved; the previous plan was restored.")
        completed = sum(step["status"] == "completed" for step in normalized)
        return f"Plan updated. {completed}/{len(normalized)} steps completed."
