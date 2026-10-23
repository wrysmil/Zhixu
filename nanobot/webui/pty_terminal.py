"""PTY-backed WebUI terminal bridge (contract v2 §4.1, POSIX main path).

Design notes:

- ``pty.openpty()`` + ``asyncio.create_subprocess_exec(shell, stdin/stdout/stderr
  =slave_fd, start_new_session=True)``: the shell sees a real terminal, echoes,
  and renders prompts; the bridge never echoes input itself.
- Output is captured on the loop thread via ``loop.add_reader`` on the master fd,
  decoded with an incremental UTF-8 decoder (multibyte chars may straddle reads),
  and handed to a per-terminal ordered feed task so ``seq`` numbering matches
  byte order.
- Exit is claimed exactly once (``finalized`` flag) by whichever of reader-EOF /
  ``process.wait`` / kill / idle expiry fires first — mirrors the pipe bridge.
- Restricted sessions never land here: the supervisor (Task 4) keeps them on the
  guarded pipe bridge (spec §4.4 conservative shape).
"""

from __future__ import annotations

import asyncio
import codecs
import os
import re
import shutil
import signal
import sys
import time
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.shell import ExecTool, ExecToolConfig
from nanobot.security.workspace_access import WorkspaceScope
from nanobot.webui.outbound_wire import (
    encode_terminal_exit,
    encode_terminal_output,
    encode_terminal_snapshot,
)
from nanobot.webui.terminal_bridge import (
    DEFAULT_MAX_TERMINALS,
    DEFAULT_TERMINAL_IDLE_TIMEOUT_S,
    EXITED_GRACE_S,
    MAX_TERMINAL_WRITE_CHARS,
    TerminalEventPublisher,
    split_output_chunks,
)
from nanobot.webui.terminal_scrollback import TerminalScrollback

_IS_WINDOWS = sys.platform == "win32"
try:
    import fcntl
    import pty
    import struct as _struct
    import termios

    PTY_SUPPORTED = not _IS_WINDOWS
except ImportError:  # pragma: no cover - Windows has no pty module
    fcntl = pty = termios = None  # type: ignore[assignment]
    _struct = None  # type: ignore[assignment]
    PTY_SUPPORTED = False

#: Shell short-name policy shared with the inbound layer (contract v2 §4.2).
SHELL_NAME_RE = re.compile(r"^[a-z]{2,12}$")
POSIX_SHELL_CHOICES = ("zsh", "bash", "fish")
WINDOWS_SHELL_CHOICES = ("cmd", "powershell", "pwsh")

MAX_PTY_READ_BYTES = 65536
DEFAULT_PTY_COLS = 80
DEFAULT_PTY_ROWS = 24
MAX_PTY_COLS = 500
MAX_PTY_ROWS = 200
DEFAULT_PTY_SWEEP_INTERVAL_S = 10.0
_TERMINAL_EXPIRED_NOTICE = "session expired"


def available_shells() -> dict[str, Any]:
    """Whitelisted shells installed on this host, plus the platform default."""
    choices = WINDOWS_SHELL_CHOICES if _IS_WINDOWS else POSIX_SHELL_CHOICES
    shells = [
        {"id": name, "label": name, "path": str(path)}
        for name in choices
        if (path := shutil.which(name))
    ]
    default: str | None = None
    env_shell = os.environ.get("SHELL") or ""
    stem = Path(env_shell).name.lower()
    if SHELL_NAME_RE.fullmatch(stem) and shutil.which(stem):
        default = stem
    if default is None and shells:
        default = str(shells[0]["id"])
    return {"shells": shells, "default": default}


def _set_pty_size(fd: int, rows: int, cols: int) -> None:
    winsize = _struct.pack("HHHH", max(rows, 1), max(cols, 1), 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


@dataclass
class _ExitedMeta:
    chat_id: str
    shell: str
    cwd: str
    deadline: float


@dataclass
class _PtyTerminal:
    terminal_id: str
    chat_id: str
    session_key: str
    shell: str
    cwd: str
    publisher: TerminalEventPublisher
    master_fd: int
    process: asyncio.subprocess.Process
    feed_queue: asyncio.Queue[str | None]
    decoder: codecs.IncrementalDecoder
    seq: int = 0
    last_activity: float = field(default_factory=time.monotonic)
    finalized: bool = False
    reader_added: bool = False
    feed_task: asyncio.Task[None] | None = None
    exit_task: asyncio.Task[None] | None = None
    scrollback: TerminalScrollback = field(default_factory=TerminalScrollback)


class PtyTerminalBridge:
    """One PTY per terminal_id; mirrors the pipe bridge public API plus resize."""

    def __init__(
        self,
        *,
        exec_config: ExecToolConfig | None = None,
        webui_allow_local_service_access: bool = True,
        max_terminals: int = DEFAULT_MAX_TERMINALS,
        idle_timeout_s: float = DEFAULT_TERMINAL_IDLE_TIMEOUT_S,
        sweep_interval_s: float = DEFAULT_PTY_SWEEP_INTERVAL_S,
    ) -> None:
        cfg = exec_config or ExecToolConfig()
        self._env_tool = ExecTool(
            timeout=cfg.timeout,
            deny_patterns=None,
            allow_patterns=list(cfg.allow_patterns),
            restrict_to_workspace=False,
            webui_allow_local_service_access=webui_allow_local_service_access,
            sandbox=cfg.sandbox,
            path_prepend=cfg.path_prepend,
            path_append=cfg.path_append,
        )
        self._max_terminals = max(1, int(max_terminals))
        self._idle_timeout_s = float(idle_timeout_s)
        self._sweep_interval_s = max(float(sweep_interval_s), 0.05)
        self._terminals: dict[str, _PtyTerminal] = {}
        self._exited: dict[str, _ExitedMeta] = {}
        self._closed = False
        self._sweeper: asyncio.Task[None] | None = None

    # -- public API ---------------------------------------------------------

    async def start(
        self,
        *,
        terminal_id: str,
        scope: WorkspaceScope,
        chat_id: str,
        session_key: str,
        publisher: TerminalEventPublisher,
        shell: str | None = None,
    ) -> dict[str, Any]:
        cwd = str(scope.project_path)
        base: dict[str, Any] = {
            "terminal_id": terminal_id,
            "shell": "",
            "cwd": cwd,
            "status": "error",
            "message": None,
            "pty": False,
        }
        if self._closed:
            return {**base, "message": "terminal bridge is closed"}
        if not PTY_SUPPORTED:
            return {**base, "message": "terminal_pty_unavailable"}
        if scope.restrict_to_workspace:
            # spec §4.4 (conservative): restricted stays on the guarded pipe bridge.
            return {**base, "message": "sandbox_required"}
        existing = self._terminals.get(terminal_id)
        if existing is not None and not existing.finalized:
            # Takeover (contract v2 §3.3): rebind fan-out to the requesting chat.
            existing.publisher = publisher
            existing.chat_id = chat_id
            existing.session_key = session_key
            existing.last_activity = time.monotonic()
            snapshot, truncated = existing.scrollback.snapshot()
            if snapshot:
                await self._publish(
                    existing,
                    encode_terminal_snapshot(
                        terminal_id=existing.terminal_id,
                        seq=existing.seq,
                        data=snapshot,
                        truncated=truncated,
                    ),
                )
            return {
                **base,
                "shell": existing.shell,
                "cwd": existing.cwd,
                "status": "already_running",
                "pty": True,
            }
        if len(self._terminals) >= self._max_terminals:
            return {
                **base,
                "status": "limit_reached",
                "message": f"terminal limit reached ({self._max_terminals})",
            }
        shell_path = self._resolve_shell(shell)
        if shell_path is None:
            return {**base, "message": "shell_unavailable"}
        master_fd = -1
        try:
            master_fd, slave_fd = pty.openpty()
            _set_pty_size(master_fd, DEFAULT_PTY_ROWS, DEFAULT_PTY_COLS)
            env = self._env_tool._build_env()  # pyright: ignore[reportPrivateUsage]
            env["TERM"] = "xterm-256color"
            process = await asyncio.create_subprocess_exec(
                shell_path,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
            os.close(slave_fd)
        except Exception:
            for fd in (master_fd,):
                if fd >= 0:
                    with suppress(OSError):
                        os.close(fd)
            logger.exception("webui pty terminal start failed")
            return {**base, "shell": shell_path, "message": "terminal_start_failed"}
        terminal = _PtyTerminal(
            terminal_id=terminal_id,
            chat_id=chat_id,
            session_key=session_key,
            shell=shell_path,
            cwd=cwd,
            publisher=publisher,
            master_fd=master_fd,
            process=process,
            feed_queue=asyncio.Queue(),
            decoder=codecs.getincrementaldecoder("utf-8")("replace"),
        )
        self._terminals[terminal_id] = terminal
        terminal.feed_task = asyncio.create_task(self._feed_loop(terminal))
        asyncio.get_running_loop().add_reader(master_fd, self._on_readable, terminal)
        terminal.reader_added = True
        terminal.exit_task = asyncio.create_task(self._watch_exit(terminal))
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._sweep_loop())
        return {**base, "shell": shell_path, "cwd": cwd, "status": "ok", "pty": True}

    async def write(self, *, terminal_id: str, data: str) -> str | None:
        terminal = self._terminals.get(terminal_id)
        if terminal is None or terminal.finalized:
            return "unknown_terminal"
        if len(data) > MAX_TERMINAL_WRITE_CHARS:
            return "invalid_terminal_data"
        terminal.last_activity = time.monotonic()
        payload = data.encode("utf-8")
        try:
            while payload:
                payload = payload[os.write(terminal.master_fd, payload):]
        except OSError:
            await self._finalize_exit(terminal, terminal.process.returncode)
        return None

    async def resize(self, *, terminal_id: str, cols: int, rows: int) -> str | None:
        terminal = self._terminals.get(terminal_id)
        if terminal is None or terminal.finalized:
            return "unknown_terminal"
        terminal.last_activity = time.monotonic()
        with suppress(OSError):
            _set_pty_size(terminal.master_fd, rows, cols)
            os.killpg(os.getpgid(terminal.process.pid), signal.SIGWINCH)
        return None

    async def kill(self, *, terminal_id: str) -> str | None:
        terminal = self._terminals.get(terminal_id)
        if terminal is not None:
            await self._finalize_exit(terminal, terminal.process.returncode)
            return None
        # T4: killing a grace-window exited terminal is an idempotent no-op.
        return None if self.exited_owner(terminal_id) is not None else "unknown_terminal"

    def owner_chat(self, terminal_id: str) -> str | None:
        terminal = self._terminals.get(terminal_id)
        if terminal is None or terminal.finalized:
            return None
        return terminal.chat_id

    def exited_owner(self, terminal_id: str) -> str | None:
        meta = self._exited.get(terminal_id)
        if meta is None:
            return None
        if meta.deadline < time.monotonic():
            self._exited.pop(terminal_id, None)
            return None
        return meta.chat_id

    def has_live(self, terminal_id: str) -> bool:
        terminal = self._terminals.get(terminal_id)
        return terminal is not None and not terminal.finalized

    def list_visible(self, chat_ids: Iterable[str]) -> list[dict[str, Any]]:
        wanted = set(chat_ids)
        entries: list[dict[str, Any]] = []
        for terminal in self._terminals.values():
            if terminal.chat_id not in wanted:
                continue
            exited = terminal.process.returncode is not None
            entries.append(
                {
                    "terminal_id": terminal.terminal_id,
                    "status": "exited" if exited else "running",
                    "shell": terminal.shell,
                    "cwd": terminal.cwd,
                }
            )
        now = time.monotonic()
        for terminal_id, meta in list(self._exited.items()):
            if meta.deadline < now:
                self._exited.pop(terminal_id, None)
            elif meta.chat_id in wanted:
                entries.append(
                    {
                        "terminal_id": terminal_id,
                        "status": "exited",
                        "shell": meta.shell,
                        "cwd": meta.cwd,
                    }
                )
        return entries

    async def aclose(self) -> None:
        self._closed = True
        if self._sweeper is not None:
            self._sweeper.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._sweeper
        for terminal in list(self._terminals.values()):
            await self._finalize_exit(terminal, terminal.process.returncode)
        self._exited.clear()

    # -- internals ----------------------------------------------------------

    def _resolve_shell(self, shell: str | None) -> str | None:
        from nanobot.webui.terminal_bridge import _resolve_shell_program

        if shell is not None:
            resolved = shutil.which(shell) if SHELL_NAME_RE.fullmatch(shell) else None
            return resolved
        return _resolve_shell_program()

    def _on_readable(self, terminal: _PtyTerminal) -> None:
        if terminal.finalized:
            return
        try:
            chunk = os.read(terminal.master_fd, MAX_PTY_READ_BYTES)
        except BlockingIOError:
            return
        except OSError:
            asyncio.get_running_loop().create_task(
                self._finalize_exit(terminal, terminal.process.returncode)
            )
            return
        if not chunk:
            asyncio.get_running_loop().create_task(
                self._finalize_exit(terminal, terminal.process.returncode)
            )
            return
        terminal.last_activity = time.monotonic()
        text = terminal.decoder.decode(chunk)
        if text:
            terminal.feed_queue.put_nowait(text)

    async def _feed_loop(self, terminal: _PtyTerminal) -> None:
        try:
            while True:
                text = await terminal.feed_queue.get()
                if text is None:
                    break
                for chunk in split_output_chunks(text):
                    terminal.scrollback.append(chunk)
                    terminal.seq += 1
                    await self._publish(
                        terminal,
                        encode_terminal_output(
                            terminal_id=terminal.terminal_id,
                            seq=terminal.seq,
                            stream="stdout",
                            data=chunk,
                        ),
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("webui pty terminal feed loop failed")

    async def _watch_exit(self, terminal: _PtyTerminal) -> None:
        try:
            code = await terminal.process.wait()
        except asyncio.CancelledError:
            return
        await self._finalize_exit(terminal, code)

    async def _sweep_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(self._sweep_interval_s)
            now = time.monotonic()
            for terminal_id, meta in list(self._exited.items()):
                if meta.deadline < now:
                    self._exited.pop(terminal_id, None)
            for terminal in list(self._terminals.values()):
                if now - terminal.last_activity >= self._idle_timeout_s:
                    await self._finalize_exit(terminal, None, expired=True)

    async def _finalize_exit(
        self,
        terminal: _PtyTerminal,
        exit_code: int | None,
        *,
        expired: bool = False,
    ) -> None:
        if terminal.finalized:
            return
        terminal.finalized = True
        self._terminals.pop(terminal.terminal_id, None)
        self._exited[terminal.terminal_id] = _ExitedMeta(
            chat_id=terminal.chat_id,
            shell=terminal.shell,
            cwd=terminal.cwd,
            deadline=time.monotonic() + EXITED_GRACE_S,
        )
        if terminal.reader_added:
            with suppress(Exception):
                asyncio.get_running_loop().remove_reader(terminal.master_fd)
            terminal.reader_added = False
        # Drain residual master bytes (reader is detached; plain loop is safe).
        # Switch the master to non-blocking first: the shell may still be alive
        # here (kill path), and a blocking read would freeze the whole loop.
        with suppress(OSError):
            flags = fcntl.fcntl(terminal.master_fd, fcntl.F_GETFL)
            fcntl.fcntl(terminal.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        while True:
            try:
                chunk = os.read(terminal.master_fd, MAX_PTY_READ_BYTES)
            except OSError:
                break
            if not chunk:
                break
            text = terminal.decoder.decode(chunk)
            if text:
                terminal.feed_queue.put_nowait(text)
        terminal.feed_queue.put_nowait(None)
        if terminal.feed_task is not None:
            with suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(terminal.feed_task, timeout=2.0)
        if terminal.exit_task is not None and terminal.exit_task is not asyncio.current_task():
            terminal.exit_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await terminal.exit_task
        if terminal.process.returncode is None:
            with suppress(ProcessLookupError, OSError):
                os.killpg(os.getpgid(terminal.process.pid), signal.SIGTERM)
            with suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(terminal.process.wait(), timeout=2.0)
            with suppress(ProcessLookupError, OSError):
                os.killpg(os.getpgid(terminal.process.pid), signal.SIGKILL)
        with suppress(OSError):
            os.close(terminal.master_fd)
        if expired:
            terminal.seq += 1
            await self._publish(
                terminal,
                encode_terminal_output(
                    terminal_id=terminal.terminal_id,
                    seq=terminal.seq,
                    stream="stderr",
                    data=_TERMINAL_EXPIRED_NOTICE + "\n",
                ),
            )
        await self._publish(
            terminal,
            encode_terminal_exit(terminal_id=terminal.terminal_id, exit_code=exit_code),
        )

    async def _publish(self, terminal: _PtyTerminal, payload: dict[str, Any]) -> None:
        try:
            await terminal.publisher(payload)
        except Exception:
            logger.warning("webui pty terminal publish failed for {}", terminal.terminal_id)


# -- Task 4: TerminalSupervisor facade ----------------------------------------


class TerminalSupervisor:
    """Route terminals between the PTY bridge and the guarded pipe bridge.

    contract v2 §4.1/§4.4: full-access POSIX sessions use PTY; restricted
    sessions and Windows stay on the pipe bridge (line console + per-command
    guard). The live-terminal cap is enforced across both children so the
    gateway-wide limit of 8 survives the split.
    """

    def __init__(self, pipe: Any, pty_bridge: Any | None, *, max_terminals: int) -> None:
        self._pipe = pipe
        self._pty = pty_bridge
        self._max_terminals = max(1, int(max_terminals))
        self._owner: dict[str, str] = {}

    def _live_count(self) -> int:
        return sum(
            len(bridge._terminals)  # pyright: ignore[reportPrivateUsage]
            for bridge in (self._pty, self._pipe)
            if bridge is not None
        )

    def _route(self, terminal_id: str) -> Any | None:
        name = self._owner.get(terminal_id)
        if name == "pty" and self._pty is not None:
            return self._pty
        if name == "pipe":
            return self._pipe
        return None

    async def start(
        self,
        *,
        terminal_id: str,
        scope: WorkspaceScope,
        chat_id: str,
        session_key: str,
        publisher: TerminalEventPublisher,
        shell: str | None = None,
    ) -> dict[str, Any]:
        # Takeover first: an id that already lives anywhere keeps its bridge.
        for name, bridge in (("pty", self._pty), ("pipe", self._pipe)):
            if bridge is not None and bridge.has_live(terminal_id):
                self._owner[terminal_id] = name
                result = await bridge.start(
                    terminal_id=terminal_id, scope=scope, chat_id=chat_id,
                    session_key=session_key, publisher=publisher, shell=shell,
                )
                return {**result, "pty": name == "pty"}
        target = (
            self._pty
            if (self._pty is not None and not scope.restrict_to_workspace)
            else self._pipe
        )
        if self._live_count() >= self._max_terminals:
            return {
                "terminal_id": terminal_id,
                "shell": "",
                "cwd": str(scope.project_path),
                "status": "limit_reached",
                "message": f"terminal limit reached ({self._max_terminals})",
                "pty": target is self._pty,
            }
        name = "pty" if target is self._pty else "pipe"
        result = await target.start(
            terminal_id=terminal_id, scope=scope, chat_id=chat_id,
            session_key=session_key, publisher=publisher, shell=shell,
        )
        if result.get("status") in {"ok", "already_running"}:
            self._owner[terminal_id] = name
        return {**result, "pty": name == "pty" and result.get("status") in {"ok", "already_running"}}

    async def _delegate(self, method: str, terminal_id: str, **kwargs: Any) -> str | None:
        bridge = self._route(terminal_id)
        if bridge is not None:
            error = await getattr(bridge, method)(terminal_id=terminal_id, **kwargs)
            if error != "unknown_terminal":
                return error
            # Routed bridge lost the terminal; drop the mapping and try the other.
            self._owner.pop(terminal_id, None)
        for name, candidate in (("pty", self._pty), ("pipe", self._pipe)):
            if candidate is None or candidate is bridge:
                continue
            error = await getattr(candidate, method)(terminal_id=terminal_id, **kwargs)
            if error is None:
                if method == "kill" and not candidate.has_live(terminal_id):
                    self._owner[terminal_id] = name  # grace-window kill landed
                return None
        return "unknown_terminal"

    async def write(self, *, terminal_id: str, data: str) -> str | None:
        return await self._delegate("write", terminal_id, data=data)

    async def kill(self, *, terminal_id: str) -> str | None:
        return await self._delegate("kill", terminal_id)

    async def resize(self, *, terminal_id: str, cols: int, rows: int) -> str | None:
        return await self._delegate("resize", terminal_id, cols=cols, rows=rows)

    def owner_chat(self, terminal_id: str) -> str | None:
        for bridge in (self._pty, self._pipe):
            if bridge is not None:
                owner = bridge.owner_chat(terminal_id)
                if owner is not None:
                    return owner
        return None

    def exited_owner(self, terminal_id: str) -> str | None:
        for bridge in (self._pty, self._pipe):
            if bridge is not None:
                owner = bridge.exited_owner(terminal_id)
                if owner is not None:
                    return owner
        return None

    def has_live(self, terminal_id: str) -> bool:
        return any(
            bridge is not None and bridge.has_live(terminal_id)
            for bridge in (self._pty, self._pipe)
        )

    def list_visible(self, chat_ids: Iterable[str]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for bridge in (self._pty, self._pipe):
            if bridge is not None:
                entries.extend(bridge.list_visible(chat_ids))
        return entries

    async def aclose(self) -> None:
        for bridge in (self._pty, self._pipe):
            if bridge is not None:
                await bridge.aclose()
        self._owner.clear()


def create_terminal_bridge(
    exec_manager: Any,
    *,
    exec_config: ExecToolConfig | None = None,
    webui_allow_local_service_access: bool = True,
    max_terminals: int = DEFAULT_MAX_TERMINALS,
    idle_timeout_s: float | None = None,
) -> TerminalSupervisor:
    """Factory used by the gateway command router (inbound_commands)."""
    from nanobot.webui.terminal_bridge import WebuiTerminalBridge

    pipe = WebuiTerminalBridge(
        exec_manager,
        exec_config=exec_config,
        webui_allow_local_service_access=webui_allow_local_service_access,
        max_terminals=max_terminals,
        idle_timeout_s=idle_timeout_s,
    )
    pty_bridge = None
    if PTY_SUPPORTED:
        pty_bridge = PtyTerminalBridge(
            exec_config=exec_config,
            webui_allow_local_service_access=webui_allow_local_service_access,
            max_terminals=max_terminals,
            idle_timeout_s=(
                DEFAULT_TERMINAL_IDLE_TIMEOUT_S if idle_timeout_s is None else idle_timeout_s
            ),
        )
    return TerminalSupervisor(pipe, pty_bridge, max_terminals=max_terminals)
