"""TDD coverage for the WebUI terminal WS channel (contract 2026-10-11 §1).

Covers three layers:
- outbound_wire terminal event encoders (exact contract field sets)
- WebuiTerminalBridge behavior against the real ExecSessionManager
- WebUICommandRouter dispatch of the four terminal frames
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from nanobot.agent.tools.exec_session import ExecSessionManager
from nanobot.security.workspace_access import build_workspace_scope, default_workspace_scope
from nanobot.webui.inbound_commands import WebUICommandRouter
from nanobot.webui.outbound_wire import (
    encode_terminal_exit,
    encode_terminal_list_result,
    encode_terminal_output,
    encode_terminal_started,
)
from nanobot.webui.terminal_bridge import (
    MAX_OUTPUT_CHUNK_BYTES,
    WebuiTerminalBridge,
    split_output_chunks,
)
from nanobot.webui.workspaces import WebUIWorkspaceController

_SKIP_WINDOWS = pytest.mark.skipif(
    sys.platform == "win32",
    reason="terminal bridge spawns POSIX shells in these tests",
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class Collector:
    """Async terminal event sink used as the bridge publisher."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any]) -> None:
        self.events.append(payload)

    def of(self, name: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event.get("event") == name]

    def text(self, name: str) -> str:
        return "".join(
            str(event.get("data", "")) for event in self.of(name)
        )


async def _wait_until(
    predicate,
    *,
    timeout: float = 5.0,
    interval: float = 0.02,
    message: str = "condition",
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"timed out waiting for: {message}")


def _make_bridge(
    tmp_path: Path,
    **kwargs: Any,
) -> WebuiTerminalBridge:
    manager_kwargs = {
        key: kwargs.pop(key)
        for key in ("max_sessions", "idle_timeout")
        if key in kwargs
    }
    manager = ExecSessionManager(
        max_sessions=manager_kwargs.get("max_sessions", 8),
        idle_timeout=manager_kwargs.get("idle_timeout", 1800),
    )
    return WebuiTerminalBridge(manager, **kwargs)


# --------------------------------------------------------------------------
# outbound_wire encoders — exact contract §1.2 field sets
# --------------------------------------------------------------------------


def test_encode_terminal_started_exact_fields() -> None:
    payload = encode_terminal_started(
        terminal_id="t1",
        shell="/bin/bash",
        cwd="/tmp/ws",
        status="ok",
    )
    assert set(payload) == {
        "event",
        "type",
        "terminal_id",
        "shell",
        "cwd",
        "status",
        "pty",
    }
    assert payload["event"] == "terminal_started"
    assert payload["type"] == "terminal_started"
    assert payload["terminal_id"] == "t1"
    assert payload["shell"] == "/bin/bash"
    assert payload["cwd"] == "/tmp/ws"
    assert payload["status"] == "ok"
    assert payload["pty"] is False

    failed = encode_terminal_started(
        terminal_id="t1",
        shell="",
        cwd="/tmp/ws",
        status="error",
        message="terminal_start_failed",
    )
    assert failed["message"] == "terminal_start_failed"
    assert failed["status"] == "error"


def test_encode_terminal_output_exact_fields() -> None:
    payload = encode_terminal_output(
        terminal_id="t1",
        seq=12,
        stream="stderr",
        data="boom\n",
    )
    assert set(payload) == {
        "event",
        "type",
        "terminal_id",
        "seq",
        "stream",
        "data",
    }
    assert payload["event"] == "terminal_output"
    assert payload["type"] == "terminal_output"
    assert payload["seq"] == 12
    assert payload["stream"] == "stderr"
    assert payload["data"] == "boom\n"


def test_encode_terminal_exit_exact_fields() -> None:
    payload = encode_terminal_exit(terminal_id="t1", exit_code=None)
    assert set(payload) == {"event", "type", "terminal_id", "exit_code"}
    assert payload["event"] == "terminal_exit"
    assert payload["type"] == "terminal_exit"
    assert payload["exit_code"] is None
    coded = encode_terminal_exit(terminal_id="t1", exit_code=-1)
    assert coded["exit_code"] == -1


def test_encode_terminal_list_result_exact_fields() -> None:
    payload = encode_terminal_list_result(
        terminals=[
            {
                "terminal_id": "t1",
                "status": "running",
                "shell": "/bin/bash",
                "cwd": "/tmp/ws",
            },
        ],
    )
    assert set(payload) == {"event", "type", "terminals"}
    assert payload["event"] == "terminal_list_result"
    assert payload["type"] == "terminal_list_result"
    assert payload["terminals"][0]["status"] == "running"


# --------------------------------------------------------------------------
# split_output_chunks — ≤8KB UTF-8 budget, character-safe boundaries
# --------------------------------------------------------------------------


def test_split_output_chunks_respects_utf8_budget() -> None:
    text = "あ" * 5_000  # 3 bytes per char in UTF-8
    chunks = split_output_chunks(text)
    assert len(chunks) > 1
    assert all(len(chunk.encode("utf-8")) <= MAX_OUTPUT_CHUNK_BYTES for chunk in chunks)
    assert "".join(chunks) == text


def test_split_output_chunks_passes_short_text_through() -> None:
    assert split_output_chunks("hi\n") == ["hi\n"]
    assert split_output_chunks("") == []


# --------------------------------------------------------------------------
# Bridge — real ExecSessionManager, real shells
# --------------------------------------------------------------------------


@_SKIP_WINDOWS
async def test_bridge_start_reports_shell_and_cwd(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    collector = Collector()
    scope = build_workspace_scope(tmp_path, "full")
    try:
        result = await bridge.start(
            terminal_id="t1",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=collector,
        )
        assert result["status"] == "ok"
        assert result["terminal_id"] == "t1"
        assert result["shell"]
        assert Path(result["shell"]).exists()
        assert result["cwd"] == str(scope.project_path)
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_bridge_write_streams_stdout_with_increasing_seq(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    collector = Collector()
    scope = build_workspace_scope(tmp_path, "full")
    try:
        await bridge.start(
            terminal_id="t1",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=collector,
        )
        error = await bridge.write(terminal_id="t1", data="echo hi\n")
        assert error is None

        await _wait_until(
            lambda: "hi" in collector.text("terminal_output"),
            message="terminal_output containing 'hi'",
        )
        outputs = collector.of("terminal_output")
        seqs = [event["seq"] for event in outputs]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)
        assert all(event["stream"] in {"stdout", "stderr"} for event in outputs)
        assert all(event["terminal_id"] == "t1" for event in outputs)
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_bridge_pending_input_buffers_until_newline(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    collector = Collector()
    scope = build_workspace_scope(tmp_path, "full")
    try:
        await bridge.start(
            terminal_id="t1",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=collector,
        )
        await bridge.write(terminal_id="t1", data="echo pend")
        await asyncio.sleep(0.2)
        assert "pend" not in collector.text("terminal_output")
        await bridge.write(terminal_id="t1", data="ing\n")
        await _wait_until(
            lambda: "pending" in collector.text("terminal_output"),
            message="buffered line submission",
        )
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_bridge_kill_publishes_terminal_exit(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    collector = Collector()
    scope = build_workspace_scope(tmp_path, "full")
    try:
        await bridge.start(
            terminal_id="t1",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=collector,
        )
        await bridge.write(terminal_id="t1", data="sleep 30\n")
        error = await bridge.kill(terminal_id="t1")
        assert error is None
        await _wait_until(
            lambda: len(collector.of("terminal_exit")) == 1,
            message="terminal_exit after kill",
        )
        exit_event = collector.of("terminal_exit")[0]
        assert exit_event["terminal_id"] == "t1"
        assert bridge.owner_chat("t1") is None
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_bridge_duplicate_start_is_already_running(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    collector = Collector()
    scope = build_workspace_scope(tmp_path, "full")
    try:
        first = await bridge.start(
            terminal_id="t1",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=collector,
        )
        second = await bridge.start(
            terminal_id="t1",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=collector,
        )
        assert first["status"] == "ok"
        assert second["status"] == "already_running"
        assert second["shell"] == first["shell"]
        assert second["cwd"] == first["cwd"]
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_bridge_ninth_start_hits_limit_reached(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    collector = Collector()
    scope = build_workspace_scope(tmp_path, "full")
    statuses: list[str] = []
    try:
        for index in range(8):
            result = await bridge.start(
                terminal_id=f"t{index}",
                scope=scope,
                chat_id=f"chat{index}",
                session_key=f"websocket:chat{index}",
                publisher=collector,
            )
            statuses.append(result["status"])
        overflow = await bridge.start(
            terminal_id="t-blocked",
            scope=scope,
            chat_id="chat-blocked",
            session_key="websocket:chat-blocked",
            publisher=collector,
        )
        assert statuses == ["ok"] * 8
        assert overflow["status"] == "limit_reached"
        assert overflow["message"]
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_bridge_restricted_rejects_blocked_command(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    collector = Collector()
    scope = build_workspace_scope(tmp_path, "restricted")
    try:
        await bridge.start(
            terminal_id="t1",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=collector,
        )
        await bridge.write(terminal_id="t1", data="rm -rf /\n")
        await _wait_until(
            lambda: collector.of("terminal_exit"),
            message="terminal_exit after guard rejection",
        )
        stderr_text = "".join(
            event["data"]
            for event in collector.of("terminal_output")
            if event["stream"] == "stderr"
        )
        assert "blocked" in stderr_text
        exit_event = collector.of("terminal_exit")[0]
        assert exit_event["exit_code"] == -1
        assert bridge.owner_chat("t1") is None
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_bridge_idle_expiry_emits_notice_and_null_exit(tmp_path: Path) -> None:
    bridge = _make_bridge(
        tmp_path,
        idle_timeout_s=0.3,
        poll_interval_s=0.05,
        idle_timeout=3600,  # manager reaper must not race the bridge
    )
    collector = Collector()
    scope = build_workspace_scope(tmp_path, "full")
    try:
        await bridge.start(
            terminal_id="t1",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=collector,
        )
        await _wait_until(
            lambda: collector.of("terminal_exit"),
            timeout=8.0,
            message="idle terminal expiry",
        )
        assert "session expired" in collector.text("terminal_output")
        exit_event = collector.of("terminal_exit")[0]
        assert exit_event["exit_code"] is None
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_bridge_list_and_owner_scoping(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    collector = Collector()
    scope = build_workspace_scope(tmp_path, "full")
    try:
        await bridge.start(
            terminal_id="t1",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=collector,
        )
        await bridge.start(
            terminal_id="t2",
            scope=scope,
            chat_id="chat2",
            session_key="websocket:chat2",
            publisher=collector,
        )
        visible = bridge.list_visible({"chat1"})
        assert [entry["terminal_id"] for entry in visible] == ["t1"]
        assert visible[0]["status"] == "running"
        assert visible[0]["shell"]
        assert visible[0]["cwd"] == str(scope.project_path)
        assert bridge.owner_chat("t2") == "chat2"
        assert bridge.owner_chat("missing") is None
        assert bridge.list_visible(set()) == []
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_bridge_write_and_kill_unknown_terminal(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    try:
        assert await bridge.write(terminal_id="ghost", data="echo x\n") == "unknown_terminal"
        assert await bridge.kill(terminal_id="ghost") == "unknown_terminal"
    finally:
        await bridge.aclose()


# --------------------------------------------------------------------------
# Router — dispatch of the four inbound terminal frames
# --------------------------------------------------------------------------


class FakeTransport:
    """Minimal WebUICommandTransport implementation for terminal routing tests."""

    def __init__(self, connection: Any, chat_ids: tuple[str, ...]) -> None:
        self.connection = connection
        self.chat_ids = chat_ids
        self.events: list[dict[str, Any]] = []
        self._subscribers = (connection,)

    def is_allowed(self, sender_id: str) -> bool:
        return True

    def webui_subscribers(self, chat_id: str) -> tuple[Any, ...]:
        return self._subscribers if chat_id in self.chat_ids else ()

    def webui_connection_chats(self, connection: Any) -> tuple[str, ...]:
        return self.chat_ids if connection is self.connection else ()

    def webui_default_chat(self, connection: Any) -> str | None:
        return self.chat_ids[0] if connection is self.connection and self.chat_ids else None

    def webui_attach(self, connection: Any, chat_id: str) -> None:
        return None

    def webui_detach(self, connection: Any, chat_id: str) -> None:
        return None

    def webui_clear_connection_default(self, connection: Any) -> None:
        return None

    def webui_clear_stream_buffers(self, chat_id: str) -> None:
        return None

    async def webui_hydrate(self, chat_id: str) -> None:
        return None

    async def webui_send_event(self, connection: Any, event: str, **fields: Any) -> None:
        self.events.append({"event": event, **fields})

    async def webui_send_raw(self, connection: Any, raw: str, *, label: str = "") -> None:
        self.events.append(json.loads(raw))

    async def webui_dispatch_message(self, **kwargs: Any) -> None:
        return None

    async def send_session_updated(self, chat_id: str, *, scope: str | None = None) -> None:
        return None

    def of(self, name: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event.get("event") == name]

    def text(self, name: str) -> str:
        return "".join(str(event.get("data", "")) for event in self.of(name))


def _make_router(
    tmp_path: Path,
    transport: FakeTransport,
    connection: Any,
) -> WebUICommandRouter:
    workspaces = WebUIWorkspaceController(
        session_manager=None,
        default_workspace=tmp_path,
        default_restrict_to_workspace=True,
    )
    gateway = SimpleNamespace(
        http=MagicMock(),
        endpoint=SimpleNamespace(
            webui_connections=[connection],
            discard_connection=MagicMock(),
            clear=MagicMock(),
        ),
        settings=SimpleNamespace(
            config=SimpleNamespace(path=tmp_path / "config.json"),
        ),
        tokens=MagicMock(),
        media=MagicMock(),
        ingress=MagicMock(),
        transcripts=MagicMock(),
        workspaces=workspaces,
        temporary_chats=MagicMock(),
        session_projection=MagicMock(),
        session_manager=None,
    )
    return WebUICommandRouter(transport, gateway)  # type: ignore[arg-type]


@_SKIP_WINDOWS
async def test_router_terminal_frames_round_trip(tmp_path: Path) -> None:
    connection = object()
    transport = FakeTransport(connection, ("chat1",))
    router = _make_router(tmp_path, transport, connection)
    terminal_id = "5f0c1a9e-2b6f-4c3a-9a77-terminal"
    try:
        await router.dispatch(
            connection,
            "client-1",
            {"type": "terminal_start", "terminal_id": terminal_id},
        )
        await _wait_until(
            lambda: transport.of("terminal_started"),
            message="terminal_started fan-out",
        )
        started = transport.of("terminal_started")[0]
        assert started["type"] == "terminal_started"
        assert started["terminal_id"] == terminal_id
        assert started["status"] == "ok"
        assert started["cwd"] == str(tmp_path.resolve())

        await router.dispatch(
            connection,
            "client-1",
            {"type": "terminal_write", "terminal_id": terminal_id, "data": "echo hello\n"},
        )
        await _wait_until(
            lambda: "hello" in transport.text("terminal_output"),
            message="terminal_output round trip",
        )

        await router.dispatch(connection, "client-1", {"type": "terminal_list"})
        listings = transport.of("terminal_list_result")
        assert listings
        entries = listings[-1]["terminals"]
        assert [entry["terminal_id"] for entry in entries] == [terminal_id]
        assert entries[0]["status"] == "running"

        await router.dispatch(
            connection,
            "client-1",
            {"type": "terminal_kill", "terminal_id": terminal_id},
        )
        await _wait_until(
            lambda: transport.of("terminal_exit"),
            message="terminal_exit after kill frame",
        )
        exit_event = transport.of("terminal_exit")[0]
        assert exit_event["terminal_id"] == terminal_id
    finally:
        await router.close()


@_SKIP_WINDOWS
async def test_router_rejects_invalid_terminal_id(tmp_path: Path) -> None:
    connection = object()
    transport = FakeTransport(connection, ("chat1",))
    router = _make_router(tmp_path, transport, connection)
    try:
        await router.dispatch(
            connection,
            "client-1",
            {"type": "terminal_start", "terminal_id": "bad id/../x"},
        )
        errors = transport.of("error")
        assert errors and errors[0]["detail"] == "invalid_terminal_id"
    finally:
        await router.close()


@_SKIP_WINDOWS
async def test_router_rejects_unattached_session_key(tmp_path: Path) -> None:
    connection = object()
    transport = FakeTransport(connection, ("chat1",))
    router = _make_router(tmp_path, transport, connection)
    try:
        await router.dispatch(
            connection,
            "client-1",
            {
                "type": "terminal_start",
                "terminal_id": "t1",
                "session_key": "websocket:not-attached",
            },
        )
        errors = transport.of("error")
        assert errors and errors[0]["detail"] == "terminal_session_unavailable"
    finally:
        await router.close()


@_SKIP_WINDOWS
async def test_router_write_unknown_terminal_is_reported(tmp_path: Path) -> None:
    connection = object()
    transport = FakeTransport(connection, ("chat1",))
    router = _make_router(tmp_path, transport, connection)
    try:
        await router.dispatch(
            connection,
            "client-1",
            {"type": "terminal_write", "terminal_id": "ghost", "data": "ls\n"},
        )
        errors = transport.of("error")
        assert errors and errors[0]["detail"] == "unknown_terminal"
    finally:
        await router.close()


@_SKIP_WINDOWS
async def test_router_rejects_untrusted_connection(tmp_path: Path) -> None:
    connection = object()
    transport = FakeTransport(connection, ("chat1",))
    router = _make_router(tmp_path, transport, connection)
    stranger = object()
    try:
        await router.dispatch(
            stranger,
            "client-2",
            {"type": "terminal_start", "terminal_id": "t1"},
        )
        errors = transport.of("error")
        assert errors and errors[0]["detail"] == "access_denied"
    finally:
        await router.close()


# --------------------------------------------------------------------------
# FIX-3 security: restricted multi-line command assembly guard
# --------------------------------------------------------------------------

_VALID_TERMINAL_ID = "5f0c1a9e-2b6f-4c3a-9a77-lockedterm"


def test_analyze_restricted_segment_classifies_shapes() -> None:
    """Pure segment classifier: continuation vs guard-ready vs fail-closed reject."""
    from nanobot.webui.terminal_bridge import analyze_restricted_segment

    # Provably complete single/multi logical command -> run the whole-segment guard.
    assert analyze_restricted_segment("ls\n") == "ready"
    assert analyze_restricted_segment("echo a\nb\n") == "ready"
    assert analyze_restricted_segment("echo $(date)\n") == "ready"
    assert analyze_restricted_segment('echo "a\nb"\n') == "ready"
    # Unescaped trailing backslash -> interactive shell continues reading.
    assert analyze_restricted_segment("rm \\\n") == "continuation"
    assert analyze_restricted_segment("foo bar \\\n") == "continuation"
    # Unclosed quotes swallow following lines.
    assert analyze_restricted_segment('echo "hello\n') == "continuation"
    assert analyze_restricted_segment("echo 'hello\n") == "continuation"
    assert analyze_restricted_segment('echo "a\\\n') == "continuation"
    # Indeterminate / multi-line assembly shapes -> fail closed.
    assert analyze_restricted_segment("(foo\n") == "reject"
    assert analyze_restricted_segment("{ foo\n") == "reject"
    assert analyze_restricted_segment("cat <<EOF\n") == "reject"
    assert analyze_restricted_segment("echo `date\n") == "reject"
    assert analyze_restricted_segment("echo )\n") == "reject"


def _spy_raw_writes(bridge: WebuiTerminalBridge, *, call_orig: bool) -> list[str]:
    """Record every ``_write_raw`` submission; optionally still forward to the shell."""
    recorded: list[str] = []
    original = bridge._write_raw

    async def _spy(terminal: Any, chars: str) -> Any:
        recorded.append(chars)
        if call_orig:
            return await original(terminal, chars)
        return None

    bridge._write_raw = _spy  # type: ignore[method-assign]
    return recorded


@_SKIP_WINDOWS
async def test_bridge_restricted_blocks_line_continuation_split_deny(tmp_path: Path) -> None:
    """`rm \\` + `-rf …` must be guarded as one assembled command, not per line."""
    bridge = _make_bridge(tmp_path)
    collector = Collector()
    scope = build_workspace_scope(tmp_path, "restricted")
    recorded = _spy_raw_writes(bridge, call_orig=False)
    try:
        await bridge.start(
            terminal_id="t1",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=collector,
        )
        await bridge.write(terminal_id="t1", data="rm \\\n")
        await bridge.write(terminal_id="t1", data="-rf ./nope\n")
        await _wait_until(
            lambda: collector.of("terminal_exit"),
            message="blocked terminal_exit after continuation assembly",
        )
        # Neither fragment reached the shell as an executable line.
        assert not any("rm" in chunk or "-rf" in chunk for chunk in recorded)
        stderr_text = "".join(
            event["data"]
            for event in collector.of("terminal_output")
            if event["stream"] == "stderr"
        )
        assert "blocked" in stderr_text
        assert collector.of("terminal_exit")[0]["exit_code"] == -1
        assert bridge.owner_chat("t1") is None
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_bridge_restricted_buffers_unclosed_quote_until_closed(tmp_path: Path) -> None:
    """An unterminated quote is held at the bridge and guarded only once complete."""
    bridge = _make_bridge(tmp_path)
    collector = Collector()
    scope = build_workspace_scope(tmp_path, "restricted")
    recorded = _spy_raw_writes(bridge, call_orig=True)
    try:
        await bridge.start(
            terminal_id="t1",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=collector,
        )
        await bridge.write(terminal_id="t1", data='echo "alpha\n')
        await asyncio.sleep(0.2)
        assert recorded == []
        assert "alpha" not in collector.text("terminal_output")

        await bridge.write(terminal_id="t1", data='beta"\n')
        await _wait_until(
            lambda: "beta" in collector.text("terminal_output"),
            message="assembled quoted command output",
        )
        assert any("alpha" in chunk and "beta" in chunk for chunk in recorded)
        assert not collector.of("terminal_exit")  # safe command, not blocked
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_bridge_restricted_single_line_command_unaffected(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    collector = Collector()
    scope = build_workspace_scope(tmp_path, "restricted")
    try:
        await bridge.start(
            terminal_id="t1",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=collector,
        )
        await bridge.write(terminal_id="t1", data="echo ok\n")
        await _wait_until(
            lambda: "ok" in collector.text("terminal_output"),
            message="single-line restricted command output",
        )
        assert not collector.of("terminal_exit")
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_bridge_restricted_fail_closed_on_unbalanced_construct(tmp_path: Path) -> None:
    bridge = _make_bridge(tmp_path)
    collector = Collector()
    scope = build_workspace_scope(tmp_path, "restricted")
    recorded = _spy_raw_writes(bridge, call_orig=False)
    try:
        await bridge.start(
            terminal_id="t1",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=collector,
        )
        await bridge.write(terminal_id="t1", data="( echo hi\n")
        await _wait_until(
            lambda: collector.of("terminal_exit"),
            message="fail-closed exit on ambiguous construct",
        )
        assert recorded == []
        stderr_text = "".join(
            event["data"]
            for event in collector.of("terminal_output")
            if event["stream"] == "stderr"
        )
        assert "blocked" in stderr_text
        assert collector.of("terminal_exit")[0]["exit_code"] == -1
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_bridge_full_mode_does_not_buffer_multiline(tmp_path: Path) -> None:
    """Unrestricted (full) sessions keep the original immediate per-line submission."""
    bridge = _make_bridge(tmp_path)
    collector = Collector()
    scope = build_workspace_scope(tmp_path, "full")
    recorded = _spy_raw_writes(bridge, call_orig=False)
    try:
        await bridge.start(
            terminal_id="t1",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=collector,
        )
        terminal = bridge._terminals["t1"]  # pyright: ignore[reportPrivateUsage]
        await bridge.write(terminal_id="t1", data="echo \\\n")
        assert recorded == ["echo \\\n"]
        assert terminal.pending_command == ""
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_bridge_start_sandbox_wrap_failure_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sandbox wrap error must not launch an unsandboxed shell (contract §1.2)."""
    from nanobot.agent.tools.shell import ExecToolConfig

    def _boom(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("bwrap unavailable")

    monkeypatch.setattr("nanobot.webui.terminal_bridge.wrap_command", _boom)
    bridge = _make_bridge(tmp_path, exec_config=ExecToolConfig(sandbox="bwrap"))
    collector = Collector()
    scope = build_workspace_scope(tmp_path, "full")
    try:
        started = await bridge.start(
            terminal_id="t1",
            scope=scope,
            chat_id="chat1",
            session_key="websocket:chat1",
            publisher=collector,
        )
        assert started["status"] == "error"
        assert started.get("message")
        assert "t1" not in bridge._terminals  # no shell spawned or registered
    finally:
        await bridge.aclose()


# --------------------------------------------------------------------------
# Router — connection gate on write/kill/list (issue 3)
# --------------------------------------------------------------------------


@_SKIP_WINDOWS
async def test_router_write_rejects_untrusted_connection(tmp_path: Path) -> None:
    connection = object()
    transport = FakeTransport(connection, ("chat1",))
    router = _make_router(tmp_path, transport, connection)
    stranger = object()
    try:
        await router.dispatch(
            stranger,
            "client-2",
            {"type": "terminal_write", "terminal_id": _VALID_TERMINAL_ID, "data": "ls\n"},
        )
        errors = transport.of("error")
        assert errors and errors[0]["detail"] == "access_denied"
    finally:
        await router.close()


@_SKIP_WINDOWS
async def test_router_kill_rejects_untrusted_connection(tmp_path: Path) -> None:
    connection = object()
    transport = FakeTransport(connection, ("chat1",))
    router = _make_router(tmp_path, transport, connection)
    stranger = object()
    try:
        await router.dispatch(
            stranger,
            "client-2",
            {"type": "terminal_kill", "terminal_id": _VALID_TERMINAL_ID},
        )
        errors = transport.of("error")
        assert errors and errors[0]["detail"] == "access_denied"
    finally:
        await router.close()


@_SKIP_WINDOWS
async def test_router_list_rejects_untrusted_connection(tmp_path: Path) -> None:
    connection = object()
    transport = FakeTransport(connection, ("chat1",))
    router = _make_router(tmp_path, transport, connection)
    stranger = object()
    try:
        await router.dispatch(stranger, "client-2", {"type": "terminal_list"})
        errors = transport.of("error")
        assert errors and errors[0]["detail"] == "access_denied"
        assert not transport.of("terminal_list_result")
    finally:
        await router.close()


# --------------------------------------------------------------------------
# Task 3 — pipe bridge v2 contract alignment (pty flag / grace / takeover)
# --------------------------------------------------------------------------


@_SKIP_WINDOWS
async def test_pipe_bridge_started_result_carries_pty_false(tmp_path):
    bridge = _make_bridge(tmp_path)  # 本文件既有工厂：ExecSessionManager + WebuiTerminalBridge
    collector = Collector()
    scope = default_workspace_scope(tmp_path, restrict_to_workspace=False)
    try:
        result = await bridge.start(
            terminal_id="term-f",
            scope=scope,
            chat_id="c",
            session_key="websocket:c",
            publisher=collector,
        )
        assert result["pty"] is False and result["status"] == "ok"
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_pipe_bridge_kill_grace_idempotent_and_exited_owner(tmp_path):
    bridge = _make_bridge(tmp_path)
    scope = default_workspace_scope(tmp_path, restrict_to_workspace=False)
    try:
        await bridge.start(
            terminal_id="term-g",
            scope=scope,
            chat_id="c",
            session_key="websocket:c",
            publisher=Collector(),
        )
        await bridge.kill(terminal_id="term-g")
        assert bridge.exited_owner("term-g") == "c"
        assert await bridge.kill(terminal_id="term-g") is None  # T4 no-op
        assert bridge.owner_chat("term-g") is None
    finally:
        await bridge.aclose()


@_SKIP_WINDOWS
async def test_pipe_bridge_takeover_rebinds_publisher(tmp_path):
    bridge = _make_bridge(tmp_path)
    first, second = Collector(), Collector()
    scope = default_workspace_scope(tmp_path, restrict_to_workspace=False)
    try:
        await bridge.start(
            terminal_id="term-t",
            scope=scope,
            chat_id="c1",
            session_key="websocket:c1",
            publisher=first,
        )
        result = await bridge.start(
            terminal_id="term-t",
            scope=scope,
            chat_id="c2",
            session_key="websocket:c2",
            publisher=second,
        )
        assert result["status"] == "already_running"
        assert bridge.owner_chat("term-t") == "c2"
    finally:
        await bridge.aclose()


# --------------------------------------------------------------------------
# Task 5 — contract v2 router wiring (pty flag / shell gate / resize frame /
# kill grace) with a fake bridge injected via _terminal_bridge_instance
# --------------------------------------------------------------------------


class FakeBridge:
    """Records router-forwarded calls; exposes live + exited-grace ownership."""

    def __init__(self) -> None:
        self.starts: list[dict[str, Any]] = []
        self.resizes: list[tuple[str, int, int]] = []
        self.kills: list[str] = []
        self._live: dict[str, str] = {"term-1": "chat1"}
        self._grace: dict[str, str] = {"term-done": "chat1"}

    async def start(
        self,
        *,
        terminal_id: str,
        scope: Any,
        chat_id: str,
        session_key: str,
        publisher: Any,
        shell: str | None = None,
    ) -> dict[str, Any]:
        self.starts.append({"terminal_id": terminal_id, "shell": shell})
        return {
            "terminal_id": terminal_id, "shell": "/bin/zsh",
            "cwd": str(scope.project_path),
            "status": "ok", "message": None, "pty": True,
        }

    def owner_chat(self, terminal_id: str) -> str | None:
        return self._live.get(terminal_id)

    def exited_owner(self, terminal_id: str) -> str | None:
        return self._grace.get(terminal_id)

    async def resize(self, *, terminal_id: str, cols: int, rows: int) -> str | None:
        self.resizes.append((terminal_id, cols, rows))
        return None

    async def kill(self, *, terminal_id: str) -> str | None:
        self.kills.append(terminal_id)
        return None

    async def aclose(self) -> None:
        return None


def _make_fake_bridge_router(
    tmp_path: Path,
) -> tuple[WebUICommandRouter, FakeTransport, FakeBridge, Any]:
    connection = object()
    transport = FakeTransport(connection, ("chat1",))
    router = _make_router(tmp_path, transport, connection)
    bridge = FakeBridge()
    router._terminal_bridge_instance = bridge
    return router, transport, bridge, connection


def test_encode_terminal_started_always_carries_pty() -> None:
    payload = encode_terminal_started(
        terminal_id="t", shell="/bin/zsh", cwd="/x", status="ok", pty=True,
    )
    assert payload["pty"] is True
    payload = encode_terminal_started(terminal_id="t", shell="", cwd="", status="error")
    assert payload["pty"] is False


@_SKIP_WINDOWS
async def test_resize_frame_reaches_bridge(tmp_path: Path) -> None:
    router, transport, bridge, connection = _make_fake_bridge_router(tmp_path)
    try:
        await router.dispatch(
            connection,
            "client-1",
            {"type": "terminal_resize", "terminal_id": "term-1", "cols": 120, "rows": 40},
        )
        assert bridge.resizes == [("term-1", 120, 40)]
        assert not transport.of("error")
    finally:
        await router.close()


@_SKIP_WINDOWS
async def test_resize_rejects_out_of_bounds(tmp_path: Path) -> None:
    router, transport, bridge, connection = _make_fake_bridge_router(tmp_path)
    try:
        await router.dispatch(
            connection,
            "client-1",
            {"type": "terminal_resize", "terminal_id": "term-1", "cols": 9999, "rows": 40},
        )
        assert bridge.resizes == []
        errors = transport.of("error")
        assert errors and errors[0]["detail"] == "invalid_terminal_resize"
    finally:
        await router.close()


@_SKIP_WINDOWS
async def test_kill_on_grace_terminal_is_silent_noop(tmp_path: Path) -> None:
    router, transport, bridge, connection = _make_fake_bridge_router(tmp_path)
    try:
        await router.dispatch(
            connection,
            "client-1",
            {"type": "terminal_kill", "terminal_id": "term-done"},
        )
        assert bridge.kills == ["term-done"]
        assert not transport.of("error")
    finally:
        await router.close()


@_SKIP_WINDOWS
async def test_start_rejects_bad_shell_name_before_bridge(tmp_path: Path) -> None:
    router, transport, bridge, connection = _make_fake_bridge_router(tmp_path)
    try:
        await router.dispatch(
            connection,
            "client-1",
            {
                "type": "terminal_start",
                "terminal_id": "term-1",
                "session_key": "websocket:chat1",
                "shell": "rm -rf /",
            },
        )
        assert bridge.starts == []
        started = transport.of("terminal_started")
        assert started and started[-1]["status"] == "error"
        assert started[-1]["message"] == "shell_unavailable"
    finally:
        await router.close()
