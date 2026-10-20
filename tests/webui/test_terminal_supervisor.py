import asyncio
import sys
from typing import Any

import pytest

from nanobot.agent.tools.exec_session import ExecSessionManager
from nanobot.security.workspace_access import default_workspace_scope
from nanobot.webui.pty_terminal import TerminalSupervisor, create_terminal_bridge


class FakePipeBridge:
    def __init__(self) -> None:
        self._terminals: dict[str, Any] = {}
        self.started: list[dict[str, Any]] = []

    def has_live(self, terminal_id: str) -> bool:
        return terminal_id in self._terminals

    async def start(self, **kwargs: Any) -> dict[str, Any]:
        self.started.append(kwargs)
        self._terminals[kwargs["terminal_id"]] = object()
        return {
            "terminal_id": kwargs["terminal_id"],
            "shell": "cmd",
            "cwd": "C:/workspace",
            "status": "ok",
            "pty": False,
        }


@pytest.mark.asyncio
async def test_supervisor_without_pty_routes_to_pipe(tmp_path) -> None:
    scope = default_workspace_scope(tmp_path, restrict_to_workspace=False)
    pipe = FakePipeBridge()
    supervisor = TerminalSupervisor(pipe, None, max_terminals=2)

    async def publish(_payload: dict[str, Any]) -> None:
        return None

    result = await supervisor.start(
        terminal_id="term-cmd",
        scope=scope,
        chat_id="chat-1",
        session_key="websocket:chat-1",
        publisher=publish,
        shell="cmd",
    )

    assert result["status"] == "ok"
    assert result["pty"] is False
    assert pipe.started[0]["shell"] == "cmd"


@pytest.mark.skipif(sys.platform != "win32", reason="requires the real Windows CMD")
@pytest.mark.asyncio
async def test_windows_cmd_starts_executes_and_replays_output(tmp_path) -> None:
    manager = ExecSessionManager()
    supervisor = create_terminal_bridge(manager)
    scope = default_workspace_scope(tmp_path, restrict_to_workspace=False)
    events: list[dict[str, Any]] = []

    async def publish(payload: dict[str, Any]) -> None:
        events.append(payload)

    async def start() -> dict[str, Any]:
        return await asyncio.wait_for(
            supervisor.start(
                terminal_id="term-real-cmd",
                scope=scope,
                chat_id="chat-1",
                session_key="websocket:chat-1",
                publisher=publish,
                shell="cmd",
            ),
            timeout=5,
        )

    try:
        result = await start()
        assert result["status"] == "ok"
        assert result["pty"] is False
        assert supervisor.has_live("term-real-cmd")
        assert await supervisor.write(
            terminal_id="term-real-cmd", data="echo ZHIXU_CMD_READY\n"
        ) is None

        async def wait_for_command() -> None:
            while True:
                output = "".join(
                    event.get("data", "")
                    for event in events
                    if event["event"] == "terminal_output"
                )
                if "ZHIXU_CMD_READY" in output.replace("\r", "").split("\n"):
                    return
                await asyncio.sleep(0.02)

        await asyncio.wait_for(wait_for_command(), timeout=5)
        assert (await start())["status"] == "already_running"
        assert any(
            event["event"] == "terminal_snapshot" and "ZHIXU_CMD_READY" in event["data"]
            for event in events
        )
        assert await supervisor.kill(terminal_id="term-real-cmd") is None
        assert not supervisor.has_live("term-real-cmd")
        assert any(event["event"] == "terminal_exit" for event in events)
    finally:
        await supervisor.aclose()
        await manager.close_all()


@pytest.mark.asyncio
async def test_supervisor_without_pty_still_enforces_cap(tmp_path) -> None:
    scope = default_workspace_scope(tmp_path, restrict_to_workspace=False)
    pipe = FakePipeBridge()
    pipe._terminals = {f"term-{index}": object() for index in range(2)}
    supervisor = TerminalSupervisor(pipe, None, max_terminals=2)

    async def publish(_payload: dict[str, Any]) -> None:
        return None

    result = await supervisor.start(
        terminal_id="term-overflow",
        scope=scope,
        chat_id="chat-1",
        session_key="websocket:chat-1",
        publisher=publish,
        shell="cmd",
    )

    assert result["status"] == "limit_reached"
    assert result["pty"] is False
