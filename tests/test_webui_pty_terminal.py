"""PTY WebUI 终端桥测试（契约 v2 §4.1；spec R2 探针先行）。"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

import pytest

from nanobot.security.workspace_access import default_workspace_scope
from nanobot.webui.pty_terminal import _TERMINAL_EXPIRED_NOTICE, PtyTerminalBridge

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="PTY bridge is POSIX-only in this batch"
)


class Collector:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any]) -> None:
        self.events.append(payload)

    def of(self, name: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event.get("event") == name]

    def text(self, name: str = "terminal_output") -> str:
        return "".join(str(e.get("data", "")) for e in self.of(name))


async def _wait_until(predicate, *, timeout: float = 5.0, message: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out waiting for: {message}")


@pytest.fixture
def scope(tmp_path):
    (tmp_path / "probe.txt").write_text("probe", encoding="utf-8")
    return default_workspace_scope(tmp_path, restrict_to_workspace=False)


async def test_spike_echo_lifecycle_kill(scope):  # R2 探针
    bridge = PtyTerminalBridge()
    collector = Collector()
    try:
        result = await bridge.start(
            terminal_id="term-spike", scope=scope, chat_id="chat-1",
            session_key="websocket:chat-1", publisher=collector,
        )
        assert result["status"] == "ok"
        assert result["pty"] is True
        assert result["shell"]  # 解析出的 shell 路径
        await bridge.write(terminal_id="term-spike", data="echo PTY_OK\n")
        await _wait_until(lambda: "PTY_OK" in collector.text(), message="echo output")
        await bridge.kill(terminal_id="term-spike")
        await _wait_until(lambda: len(collector.of("terminal_exit")) == 1, message="exit frame")
    finally:
        await bridge.aclose()


async def test_pty_stty_size_reflects_resize(scope):
    bridge = PtyTerminalBridge()
    collector = Collector()
    try:
        await bridge.start(
            terminal_id="term-size", scope=scope, chat_id="c",
            session_key="websocket:c", publisher=collector,
        )
        assert await bridge.resize(terminal_id="term-size", cols=120, rows=40) is None
        await bridge.write(terminal_id="term-size", data="stty size\n")
        await _wait_until(lambda: "40 120" in collector.text(), message="stty size")
    finally:
        await bridge.aclose()


async def test_pty_unknown_shell_rejected(scope):
    bridge = PtyTerminalBridge()
    result = await bridge.start(
        terminal_id="term-bad", scope=scope, chat_id="c",
        session_key="websocket:c", publisher=Collector(), shell="definitelynotashell",
    )
    assert result["status"] == "error"
    assert result["message"] == "shell_unavailable"
    await bridge.aclose()


async def test_pty_restricted_scope_refuses_pty(scope, tmp_path):
    from nanobot.security.workspace_access import default_workspace_scope

    restricted = default_workspace_scope(tmp_path, restrict_to_workspace=True)
    bridge = PtyTerminalBridge()
    result = await bridge.start(
        terminal_id="term-r", scope=restricted, chat_id="c",
        session_key="websocket:c", publisher=Collector(),
    )
    assert result["status"] == "error" and result["message"] == "sandbox_required"
    await bridge.aclose()


async def test_pty_takeover_rebinds_publisher(scope):
    bridge = PtyTerminalBridge()
    first, second = Collector(), Collector()
    try:
        await bridge.start(
            terminal_id="term-t", scope=scope, chat_id="chat-1",
            session_key="websocket:chat-1", publisher=first,
        )
        result = await bridge.start(
            terminal_id="term-t", scope=scope, chat_id="chat-2",
            session_key="websocket:chat-2", publisher=second,
        )
        assert result["status"] == "already_running" and result["pty"] is True
        assert bridge.owner_chat("term-t") == "chat-2"
        await bridge.write(terminal_id="term-t", data="echo TAKEOVER_OK\n")
        await _wait_until(lambda: "TAKEOVER_OK" in second.text(), message="takeover output")
        assert "TAKEOVER_OK" not in first.text()
    finally:
        await bridge.aclose()


async def test_pty_kill_idempotent_within_grace(scope):
    bridge = PtyTerminalBridge()
    try:
        await bridge.start(
            terminal_id="term-k", scope=scope, chat_id="c",
            session_key="websocket:c", publisher=Collector(),
        )
        assert await bridge.kill(terminal_id="term-k") is None
        assert bridge.exited_owner("term-k") == "c"
        # T4: second kill inside the 60s window is a silent no-op.
        assert await bridge.kill(terminal_id="term-k") is None
        assert await bridge.write(terminal_id="term-k", data="x") == "unknown_terminal"
    finally:
        await bridge.aclose()


async def test_pty_idle_expiry(tmp_path):
    from nanobot.security.workspace_access import default_workspace_scope

    scope = default_workspace_scope(tmp_path, restrict_to_workspace=False)
    bridge = PtyTerminalBridge(idle_timeout_s=0.3, sweep_interval_s=0.1)
    collector = Collector()
    try:
        await bridge.start(
            terminal_id="term-i", scope=scope, chat_id="c",
            session_key="websocket:c", publisher=collector,
        )
        await _wait_until(
            lambda: any(
                e.get("exit_code") is None and e.get("event") == "terminal_exit"
                for e in collector.events
            ),
            timeout=8.0,
            message="idle expiry exit",
        )
        assert _TERMINAL_EXPIRED_NOTICE in collector.text("terminal_output")
    finally:
        await bridge.aclose()


async def test_pty_limit_reached(scope):
    from nanobot.webui.pty_terminal import PtyTerminalBridge as Bridge

    bridge = Bridge(max_terminals=2)
    try:
        for index in range(2):
            result = await bridge.start(
                terminal_id=f"term-{index}", scope=scope, chat_id="c",
                session_key="websocket:c", publisher=Collector(),
            )
            assert result["status"] == "ok"
        result = await bridge.start(
            terminal_id="term-overflow", scope=scope, chat_id="c",
            session_key="websocket:c", publisher=Collector(),
        )
        assert result["status"] == "limit_reached"
    finally:
        await bridge.aclose()


# -- Task 4: TerminalSupervisor facade --------------------------------------


async def test_supervisor_routes_restricted_to_pipe(tmp_path):
    from nanobot.agent.tools.exec_session import ExecSessionManager
    from nanobot.security.workspace_access import default_workspace_scope
    from nanobot.webui.pty_terminal import create_terminal_bridge

    restricted = default_workspace_scope(tmp_path, restrict_to_workspace=True)
    manager = ExecSessionManager(max_sessions=8, idle_timeout=1800)
    bridge = create_terminal_bridge(manager)
    try:
        result = await bridge.start(
            terminal_id="term-x", scope=restricted, chat_id="c",
            session_key="websocket:c", publisher=Collector(),
        )
        assert result["pty"] is False  # restricted：管道守卫行式路径（spec §4.4 保守形态）
        assert bridge.owner_chat("term-x") == "c"
    finally:
        await bridge.aclose()


async def test_supervisor_prefers_pty_for_full_access(scope):
    from nanobot.agent.tools.exec_session import ExecSessionManager
    from nanobot.webui.pty_terminal import create_terminal_bridge

    manager = ExecSessionManager(max_sessions=8, idle_timeout=1800)
    bridge = create_terminal_bridge(manager)
    try:
        result = await bridge.start(
            terminal_id="term-p", scope=scope, chat_id="c",
            session_key="websocket:c", publisher=Collector(),
        )
        assert result["pty"] is True and result["status"] == "ok"
        assert await bridge.resize(terminal_id="term-p", cols=100, rows=30) is None
    finally:
        await bridge.aclose()


async def test_supervisor_shared_limit_of_eight(scope, tmp_path):
    from nanobot.agent.tools.exec_session import ExecSessionManager
    from nanobot.security.workspace_access import default_workspace_scope
    from nanobot.webui.pty_terminal import create_terminal_bridge

    restricted = default_workspace_scope(tmp_path, restrict_to_workspace=True)
    manager = ExecSessionManager(max_sessions=8, idle_timeout=1800)
    bridge = create_terminal_bridge(manager, max_terminals=2)
    try:
        full = await bridge.start(
            terminal_id="term-1", scope=scope, chat_id="c",
            session_key="websocket:c", publisher=Collector(),
        )
        pipe = await bridge.start(
            terminal_id="term-2", scope=restricted, chat_id="c",
            session_key="websocket:c", publisher=Collector(),
        )
        assert full["status"] == "ok" and pipe["status"] == "ok"
        overflow = await bridge.start(
            terminal_id="term-3", scope=scope, chat_id="c",
            session_key="websocket:c", publisher=Collector(),
        )
        assert overflow["status"] == "limit_reached"
    finally:
        await bridge.aclose()


async def test_supervisor_takeover_passthrough(scope):
    """同 id 再次 start：不新建、不计数，重绑 publisher 并透传 already_running。"""
    from nanobot.agent.tools.exec_session import ExecSessionManager
    from nanobot.webui.pty_terminal import create_terminal_bridge

    manager = ExecSessionManager(max_sessions=8, idle_timeout=1800)
    bridge = create_terminal_bridge(manager, max_terminals=2)
    first, second = Collector(), Collector()
    try:
        await bridge.start(
            terminal_id="term-t", scope=scope, chat_id="chat-1",
            session_key="websocket:chat-1", publisher=first,
        )
        result = await bridge.start(
            terminal_id="term-t", scope=scope, chat_id="chat-2",
            session_key="websocket:chat-2", publisher=second,
        )
        assert result["status"] == "already_running" and result["pty"] is True
        assert bridge.owner_chat("term-t") == "chat-2"
        # 接管不占用新增名额：仍可再开一个到上限。
        other = await bridge.start(
            terminal_id="term-t2", scope=scope, chat_id="chat-2",
            session_key="websocket:chat-2", publisher=second,
        )
        assert other["status"] == "ok"
        overflow = await bridge.start(
            terminal_id="term-t3", scope=scope, chat_id="chat-2",
            session_key="websocket:chat-2", publisher=second,
        )
        assert overflow["status"] == "limit_reached"
        # write/kill 路由透传到持有该 id 的桥。
        await bridge.write(terminal_id="term-t", data="echo SUP_OK\n")
        await _wait_until(lambda: "SUP_OK" in second.text(), message="supervisor routed write")
        assert await bridge.kill(terminal_id="term-t") is None
        assert bridge.exited_owner("term-t") == "chat-2"
    finally:
        await bridge.aclose()


async def test_supervisor_list_visible_merges_both_bridges(scope, tmp_path):
    from nanobot.agent.tools.exec_session import ExecSessionManager
    from nanobot.security.workspace_access import default_workspace_scope
    from nanobot.webui.pty_terminal import create_terminal_bridge

    restricted = default_workspace_scope(tmp_path, restrict_to_workspace=True)
    manager = ExecSessionManager(max_sessions=8, idle_timeout=1800)
    bridge = create_terminal_bridge(manager)
    try:
        await bridge.start(
            terminal_id="term-m1", scope=scope, chat_id="c",
            session_key="websocket:c", publisher=Collector(),
        )
        await bridge.start(
            terminal_id="term-m2", scope=restricted, chat_id="c",
            session_key="websocket:c", publisher=Collector(),
        )
        ids = {entry["terminal_id"] for entry in bridge.list_visible({"c"})}
        assert {"term-m1", "term-m2"} <= ids
    finally:
        await bridge.aclose()
