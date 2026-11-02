"""Agent hook that observes file-editing tools and emits file-edit activity."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from nanobot.agent.hook import (
    AgentHook,
    AgentHookContext,
    AgentRunHookContext,
    AgentTurnHookContext,
)
from nanobot.bus.outbound_events import FileEditEvent
from nanobot.events import EventSink
from nanobot.providers.base import ToolCallRequest
from nanobot.utils.file_edit_events import (
    FileEditTracker,
    build_file_edit_end_event,
    build_file_edit_error_event,
    build_file_edit_start_event,
    is_file_edit_tool,
    prepare_file_edit_trackers,
)


class FileEditActivityHook(AgentHook):
    """Translate file-editing tool lifecycle events into WebUI progress events."""

    def __init__(
        self,
        *,
        events: EventSink,
        workspace: Path | None,
    ) -> None:
        super().__init__()
        self._publish = events.publish if events.accepts(FileEditEvent) else None
        self._workspace = workspace
        self._trackers_by_call: dict[str, list[FileEditTracker]] = {}
        self._provider_calls: dict[str, dict[str, str]] = {}
        self._pending_edits: dict[str, str] = {}

    async def before_iteration(self, context: AgentHookContext) -> None:
        self._trackers_by_call.clear()
        self._provider_calls.clear()

    async def on_provider_tool_event(
        self,
        context: AgentHookContext,
        event: dict[str, Any],
    ) -> None:
        if self._publish is None or event.get("kind") == "hosted_tool":
            return
        call_id = str(event.get("call_id") or "")
        item_id = event.get("item_id")
        if call_id and isinstance(item_id, str) and item_id:
            # Responses keeps its output-item id in ToolCallRequest.id. Match
            # that execution identity so the pending row upgrades in place.
            call_id = f"{call_id}|{item_id}"
        index = event.get("index")
        if index is None and not call_id:
            return
        key = f"index:{index}" if index is not None else f"call:{call_id}"
        state = self._provider_calls.setdefault(key, {})
        if call_id and state.get("call_id") not in {None, call_id}:
            state.clear()
        if call_id:
            state["call_id"] = call_id
        name = event.get("name")
        if isinstance(name, str) and name:
            previous = state.get("name", "")
            state["name"] = name if name.startswith(previous) else previous + name
        call_id = state.get("call_id", "")
        name = state.get("name", "")
        if call_id and is_file_edit_tool(name) and call_id not in self._pending_edits:
            self._pending_edits[call_id] = name
            # Never buffer or publish partial arguments: they can contain the
            # entire generated file. A stable id is enough to upgrade this row
            # when execution supplies the validated path.
            await self._emit([self._pending_event(call_id, name)])

    async def before_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
    ) -> None:
        if self._publish is None or not isinstance(params, dict):
            return
        typed_params = cast(dict[str, Any], params)
        trackers = prepare_file_edit_trackers(
            call_id=tool_call.id,
            tool_name=tool_call.name,
            tool=tool,
            workspace=self._workspace,
            params=typed_params,
        )
        if not trackers:
            return
        self._trackers_by_call[self._tool_call_key(tool_call)] = trackers
        self._pending_edits.pop(tool_call.id, None)
        await self._emit([
            build_file_edit_start_event(tracker, typed_params)
            for tracker in trackers
        ])

    async def after_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        result: Any,
    ) -> None:
        key = self._tool_call_key(tool_call)
        trackers = self._trackers_by_call.get(key, [])
        if trackers:
            await self._emit([build_file_edit_end_event(tracker) for tracker in trackers])
            self._trackers_by_call.pop(key, None)

    async def on_execute_tool_error(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
        error: Any,
    ) -> None:
        key = self._tool_call_key(tool_call)
        trackers = self._trackers_by_call.get(key, [])
        if trackers:
            await self._emit([
                build_file_edit_error_event(tracker, str(error)) for tracker in trackers
            ])
            self._trackers_by_call.pop(key, None)

    async def on_finally(self, context: AgentRunHookContext) -> None:
        await self._close_pending("File edit preparation was interrupted.")
        if context.stop_reason != "cancelled" or not self._trackers_by_call:
            return
        trackers = [
            tracker
            for trackers in self._trackers_by_call.values()
            for tracker in trackers
        ]
        self._trackers_by_call.clear()
        await self._emit([
            build_file_edit_error_event(
                tracker,
                "Task interrupted before this tool finished.",
            )
            for tracker in trackers
        ])

    async def after_iteration(self, context: AgentHookContext) -> None:
        await self._close_pending("File edit was not executed.")

    async def _close_pending(self, error: str) -> None:
        events = [
            self._pending_event(call_id, name, error=error)
            for call_id, name in self._pending_edits.items()
        ]
        self._pending_edits.clear()
        if events:
            await self._emit(events)

    @staticmethod
    def _pending_event(call_id: str, name: str, *, error: str | None = None) -> dict[str, Any]:
        event: dict[str, Any] = {
            "version": 1,
            "call_id": call_id,
            "tool": name,
            "path": "",
            "phase": "error" if error else "start",
            "status": "error" if error else "editing",
            "pending": True,
            "added": 0,
            "deleted": 0,
            "approximate": not bool(error),
        }
        if error:
            event["error"] = error
        return event

    async def _emit(self, events: list[dict[str, Any]]) -> None:
        if self._publish is not None:
            await self._publish(FileEditEvent(file_edit_events=events))

    @staticmethod
    def _tool_call_key(tool_call: ToolCallRequest) -> str:
        call_id = getattr(tool_call, "id", "") or ""
        return f"{call_id}|{tool_call.name}" if call_id else f"{id(tool_call)}|{tool_call.name}"


def create_file_edit_activity_hook(context: AgentTurnHookContext) -> AgentHook | None:
    """Create the default file-edit observer for one agent turn."""
    if not context.events.accepts(FileEditEvent):
        return None
    return FileEditActivityHook(
        events=context.events,
        workspace=context.workspace,
    )
