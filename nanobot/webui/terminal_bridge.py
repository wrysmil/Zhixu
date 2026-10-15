"""Bridge WebUI terminal frames onto the agent exec-session machinery.

Contract: 2026-10-11 home-start-panel contract §1 (terminal WS channel).
One :class:`WebuiTerminalBridge` owns the mapping ``terminal_id`` ->
``_ExecSession`` and emits the four outbound events through a per-chat
publisher callback supplied by the command router.

Design notes:

- The inner shell is spawned through ``ExecSessionManager`` as ``shell -c shell``
  so a line-oriented, PTY-free console reads commands from the piped stdin and
  never echoes input.
- Output is drained directly from the session's bounded buffers under the
  session lock so stdout/stderr stream labels survive; the merged
  ``manager.write`` poll result would collapse them into one text blob.
- Idle expiry is owned by the bridge (``terminal.last_activity``). The manager
  reaper is deliberately kept alive by touching ``session.last_access`` each
  tick, because expiry must emit the contract's ``session expired`` notice and
  a ``terminal_exit`` with ``exit_code: null``.
- Restricted access modes route every submitted line through
  ``ExecTool._guard_command`` bound to the terminal's workspace scope before it
  ever reaches stdin; a rejection emits ``stderr`` text plus an immediate
  ``terminal_exit`` with ``exit_code: -1`` and no new error protocol.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from collections.abc import Awaitable, Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.exec_session import MAX_OUTPUT_CHARS, ExecSessionManager
from nanobot.agent.tools.sandbox import wrap_command
from nanobot.agent.tools.shell import (
    ExecTool,
    ExecToolConfig,
    _reap_pid,  # pyright: ignore[reportPrivateUsage]
)
from nanobot.security.workspace_access import (
    WorkspaceScope,
    bind_workspace_scope,
    reset_workspace_scope,
)
from nanobot.webui.outbound_wire import (
    TerminalOutputStream,
    encode_terminal_exit,
    encode_terminal_output,
)

_IS_WINDOWS = sys.platform == "win32"

#: Maximum UTF-8 byte size of one ``terminal_output.data`` chunk (contract §1.2).
MAX_OUTPUT_CHUNK_BYTES = 8 * 1024
#: Concurrent terminals per bridge (contract §1.3: backend limit 8).
DEFAULT_MAX_TERMINALS = 8
#: Idle seconds before a terminal expires (contract §1.3: reuse 1800s).
DEFAULT_TERMINAL_IDLE_TIMEOUT_S = 1800
#: Output drain cadence for one terminal.
DEFAULT_POLL_INTERVAL_S = 0.1
#: Upper bound on one inbound ``terminal_write.data`` frame.
MAX_TERMINAL_WRITE_CHARS = 64_000
#: Seconds an exited terminal stays kill-idempotent / visible via exited_owner (T4).
EXITED_GRACE_S = 60.0

_TERMINAL_EXPIRED_NOTICE = "session expired"
_INPUT_OVERFLOW_NOTICE = "Error: terminal input buffer overflow; pending input discarded"
#: Fail-closed rejection when a restricted segment cannot be resolved to a single
#: complete, guardable command (unbalanced subshell/brace/bracket, open backtick
#: substitution, here-document, or stray closer).
_MULTILINE_GUARD_REJECT = (
    "Error: Command blocked by safety guard (unterminated or ambiguous multi-line command)"
)
#: Fail-closed rejection when a restricted multi-line buffer grows past the bound.
_PENDING_COMMAND_OVERFLOW = (
    "Error: Command blocked by safety guard (multi-line command buffer overflow)"
)

TerminalEventPublisher = Callable[[dict[str, Any]], Awaitable[None]]


#: Classification outcomes for one restricted-mode command segment.
_SEGMENT_CONTINUATION = "continuation"
_SEGMENT_READY = "ready"
_SEGMENT_REJECT = "reject"


def analyze_restricted_segment(text: str) -> str:
    """Classify an accumulated restricted-mode command segment.

    An interactive shell re-joins physical lines (unmatched quotes, trailing
    ``\\`` continuations, subshells, here-documents, backtick substitutions) into
    a single logical command, so a per-line deny check is bypassable by splitting
    a dangerous token across lines. The bridge must therefore never hand a line to
    the shell until the whole logical command can be guarded at once.

    Returns one of:

    - ``continuation``: provably still open (unmatched single/double quote or an
      unescaped trailing backslash) -- keep buffering; do not guard or submit yet.
    - ``reject``: a form whose completion cannot be determined (unbalanced
      parentheses/braces/brackets, a stray closer, an open backtick substitution,
      or a here-document) -- fail closed.
    - ``ready``: quotes and structural tokens are balanced with no dangling line,
      so the guard may run over the whole assembled segment.
    """
    in_single = False
    in_double = False
    in_backtick = False
    depth: dict[str, int] = {"(": 0, "{": 0, "[": 0}
    closers = {")": "(", "}": "{", "]": "["}
    trailing_continuation = False
    i = 0
    total = len(text)
    while i < total:
        char = text[i]
        if in_single:
            i += 1
            if char == "'":
                in_single = False
            continue
        if in_double or in_backtick:
            if char == "\\":
                i += 2
                continue
            i += 1
            if in_double and char == '"':
                in_double = False
            elif in_backtick and char == "`":
                in_backtick = False
            continue
        # Outside every quoting construct.
        if char == "\\":
            trailing_continuation = i + 1 < total and text[i + 1] == "\n"
            i += 2
            continue
        trailing_continuation = False
        if char == "'":
            in_single = True
        elif char == '"':
            in_double = True
        elif char == "`":
            in_backtick = True
        elif char in depth:
            depth[char] += 1
        elif char in closers:
            depth[closers[char]] -= 1
            if depth[closers[char]] < 0:
                return _SEGMENT_REJECT
        elif char == "<" and i + 1 < total and text[i + 1] == "<":
            return _SEGMENT_REJECT
        i += 1
    if in_single or in_double:
        return _SEGMENT_CONTINUATION
    if in_backtick:
        return _SEGMENT_REJECT
    if trailing_continuation:
        return _SEGMENT_CONTINUATION
    if any(count != 0 for count in depth.values()):
        return _SEGMENT_REJECT
    return _SEGMENT_READY


def join_line_continuations(text: str) -> str:
    """Remove shell ``backslash-newline`` continuations so a guard sees the joined
    logical command (e.g. ``rm \\`` + newline + ``-rf`` -> ``rm -rf``)."""
    return text.replace("\\\n", "")



def split_output_chunks(text: str, max_bytes: int = MAX_OUTPUT_CHUNK_BYTES) -> list[str]:
    """Split output text into ≤ ``max_bytes`` UTF-8 chunks on character boundaries."""
    if not text:
        return []
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    chunks: list[str] = []
    current = bytearray()
    for char in text:
        encoded = char.encode("utf-8")
        if len(current) + len(encoded) > max_bytes:
            chunks.append(current.decode("utf-8"))
            current = bytearray(encoded)
        else:
            current.extend(encoded)
    if current:
        chunks.append(current.decode("utf-8"))
    return chunks


def _resolve_shell_program() -> str:
    if _IS_WINDOWS:
        return os.environ.get("COMSPEC") or shutil.which("cmd") or "cmd.exe"
    candidates = (
        os.environ.get("SHELL"),
        shutil.which("bash"),
        "/bin/bash",
        shutil.which("sh"),
        "/bin/sh",
    )
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise RuntimeError("no usable shell found (tried $SHELL, bash, sh)")


@dataclass
class _ShellTerminal:
    """One WebUI-owned console running inside an exec session."""

    terminal_id: str
    chat_id: str
    session_key: str
    shell: str
    cwd: str
    exec_session_id: str
    session: Any  # _ExecSession instance (private class of exec_session)
    scope: WorkspaceScope
    restricted: bool
    publisher: TerminalEventPublisher
    seq: int = 0
    pending_input: str = ""
    #: Restricted-mode buffer for a logical command assembled across physical
    #: lines; the whole segment is guarded once before it ever reaches the shell.
    pending_command: str = ""
    last_activity: float = field(default_factory=time.monotonic)
    finalized: bool = False
    closing: str | None = None
    poll_task: asyncio.Task[None] | None = None


class WebuiTerminalBridge:
    """Own WebUI terminal state on top of a dedicated ``ExecSessionManager``."""

    def __init__(
        self,
        exec_manager: ExecSessionManager,
        *,
        exec_config: ExecToolConfig | None = None,
        webui_allow_local_service_access: bool = True,
        max_terminals: int = DEFAULT_MAX_TERMINALS,
        idle_timeout_s: float | None = None,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        self._manager = exec_manager
        cfg = exec_config or ExecToolConfig()
        self._max_terminals = max(1, int(max_terminals))
        self._guard_tool = ExecTool(
            timeout=cfg.timeout,
            deny_patterns=None,
            allow_patterns=list(cfg.allow_patterns),
            restrict_to_workspace=False,
            webui_allow_local_service_access=webui_allow_local_service_access,
            sandbox=cfg.sandbox,
            path_prepend=cfg.path_prepend,
            path_append=cfg.path_append,
            sandbox_ro_binds=list(cfg.sandbox_ro_binds),
            sandbox_rw_binds=list(cfg.sandbox_rw_binds),
            allowed_env_keys=list(cfg.allowed_env_keys),
            session_manager=exec_manager,
        )
        self._idle_timeout_s = float(
            idle_timeout_s
            if idle_timeout_s is not None
            else getattr(exec_manager, "idle_timeout", DEFAULT_TERMINAL_IDLE_TIMEOUT_S)
        )
        self._poll_interval_s = max(float(poll_interval_s), 0.01)
        self._terminals: dict[str, _ShellTerminal] = {}
        # id -> (chat_id, shell, cwd, deadline, session_key) grace metadata.
        self._exited: dict[str, tuple[str, str, str, float, str]] = {}
        self._closed = False

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
        """Spawn (or reuse) one terminal; returns the ``terminal_started`` fields."""
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
        existing = self._terminals.get(terminal_id)
        if existing is not None:
            # Takeover (contract v2 §3.3): rebind fan-out to the requesting chat.
            existing.publisher = publisher
            existing.chat_id = chat_id
            existing.session_key = session_key
            existing.last_activity = time.monotonic()
            return {
                **base,
                "shell": existing.shell,
                "cwd": existing.cwd,
                "status": "already_running",
                "pty": False,
            }
        if len(self._terminals) >= self._max_terminals:
            return {
                **base,
                "status": "limit_reached",
                "message": f"terminal limit reached ({self._max_terminals})",
            }
        if shell is not None:
            from nanobot.webui.pty_terminal import SHELL_NAME_RE

            resolved = shutil.which(shell) if SHELL_NAME_RE.fullmatch(shell) else None
            if resolved is None:
                return {**base, "message": "shell_unavailable"}
            shell_program = resolved
        else:
            try:
                shell_program = _resolve_shell_program()
            except RuntimeError as exc:
                return {**base, "message": str(exc)}

        env = self._guard_tool._build_env()  # pyright: ignore[reportPrivateUsage]
        command = shell_program
        if self._guard_tool.path_prepend or self._guard_tool.path_append:
            if _IS_WINDOWS:
                env["PATH"] = self._guard_tool._compose_path(  # pyright: ignore[reportPrivateUsage]
                    env.get("PATH", "")
                )
            else:
                command = self._guard_tool._wrap_path_export(  # pyright: ignore[reportPrivateUsage]
                    command, env
                )
        if self._guard_tool.sandbox and not _IS_WINDOWS:
            try:
                command = wrap_command(
                    self._guard_tool.sandbox,
                    command,
                    cwd,
                    cwd,
                    sandbox_ro_binds=[str(p) for p in self._guard_tool.sandbox_ro_binds],
                    sandbox_rw_binds=[str(p) for p in self._guard_tool.sandbox_rw_binds],
                )
            except Exception:
                # Fail closed: a terminal configured for a sandbox must never fall
                # back to an unsandboxed shell. Surface a stable error and do not
                # spawn the session (contract §1.2 / §1.3 failure semantics).
                logger.exception("terminal sandbox wrap failed; refusing to start unsandboxed")
                return {**base, "shell": shell_program, "message": "terminal_start_failed"}

        try:
            session_id, poll = await self._manager.start(
                command=command,
                cwd=cwd,
                env=env,
                timeout=None,
                shell_program=shell_program,
                login=False,
                yield_time_ms=0,
                max_output_chars=MAX_OUTPUT_CHARS,
                owner_session_key=session_key,
            )
        except Exception as exc:
            message = str(exc)
            if "maximum exec sessions" in message:
                return {
                    **base,
                    "shell": shell_program,
                    "status": "limit_reached",
                    "message": f"terminal limit reached ({self._max_terminals})",
                }
            logger.exception("webui terminal start failed")
            # Never leak internals; the stable token is enough for the client.
            return {**base, "shell": shell_program, "message": "terminal_start_failed"}

        session = self._manager._sessions.get(session_id)  # pyright: ignore[reportPrivateUsage]
        terminal = _ShellTerminal(
            terminal_id=terminal_id,
            chat_id=chat_id,
            session_key=session_key,
            shell=shell_program,
            cwd=cwd,
            exec_session_id=session_id,
            session=session,
            scope=scope,
            restricted=bool(scope.restrict_to_workspace),
            publisher=publisher,
            last_activity=time.monotonic(),
        )
        self._terminals[terminal_id] = terminal
        if poll.done or session is None:
            if poll.output:
                await self._emit(terminal, "stdout", poll.output)
            await self._finalize_exit(terminal, poll.exit_code)
        else:
            terminal.poll_task = asyncio.create_task(self._poll_loop(terminal))
        return {**base, "shell": shell_program, "cwd": cwd, "status": "ok"}

    async def write(self, *, terminal_id: str, data: str) -> str | None:
        """Queue inbound keystrokes; complete lines are submitted. No echo."""
        terminal = self._terminals.get(terminal_id)
        if terminal is None or terminal.finalized:
            return "unknown_terminal"
        terminal.last_activity = time.monotonic()
        buffered = terminal.pending_input + data
        lines = buffered.split("\n")
        terminal.pending_input = lines.pop()
        if len(terminal.pending_input) > MAX_TERMINAL_WRITE_CHARS:
            terminal.pending_input = ""
            await self._emit(terminal, "stderr", _INPUT_OVERFLOW_NOTICE + "\n")
        for line in lines:
            if terminal.finalized:
                break
            await self._submit_line(terminal, line.rstrip("\r"))
        if terminal.finalized:
            terminal.pending_input = ""
            terminal.pending_command = ""
        return None

    async def kill(self, *, terminal_id: str) -> str | None:
        """Terminate one terminal; emits residual output then ``terminal_exit``."""
        terminal = self._terminals.get(terminal_id)
        if terminal is None or terminal.finalized:
            # T4: killing a grace-window exited terminal is an idempotent no-op.
            return None if self.exited_owner(terminal_id) is not None else "unknown_terminal"
        if terminal.closing is None:
            terminal.closing = "kill"
        exit_code: int | None = None
        session = terminal.session
        if session is not None:
            if session.process.returncode is None:
                with suppress(Exception):
                    await session.kill()
            exit_code = session.process.returncode
        await self._finalize_exit(terminal, exit_code)
        return None

    def exited_owner(self, terminal_id: str) -> str | None:
        entry = self._exited.get(terminal_id)
        if entry is None:
            return None
        if entry[3] < time.monotonic():
            self._exited.pop(terminal_id, None)
            return None
        return entry[0]

    def has_live(self, terminal_id: str) -> bool:
        terminal = self._terminals.get(terminal_id)
        return terminal is not None and not terminal.finalized

    async def resize(self, *, terminal_id: str, cols: int, rows: int) -> str | None:
        # Pipe mode has no terminal geometry; accept the frame so the shared
        # contract stays uniform (contract v2: resize is meaningful only for pty).
        if terminal_id not in self._terminals:
            return "unknown_terminal"
        return None

    def owner_chat(self, terminal_id: str) -> str | None:
        terminal = self._terminals.get(terminal_id)
        if terminal is None or terminal.finalized:
            return None
        return terminal.chat_id

    def list_visible(self, chat_ids: Iterable[str]) -> list[dict[str, Any]]:
        wanted = set(chat_ids)
        entries: list[dict[str, Any]] = []
        for terminal in self._terminals.values():
            if terminal.chat_id not in wanted:
                continue
            exited = terminal.session is None or terminal.session.process.returncode is not None
            entries.append(
                {
                    "terminal_id": terminal.terminal_id,
                    "status": "exited" if exited else "running",
                    "shell": terminal.shell,
                    "cwd": terminal.cwd,
                    "session_key": terminal.session_key,
                }
            )
        now = time.monotonic()
        for terminal_id, entry in list(self._exited.items()):
            chat, shell_name, cwd, deadline, session_key = entry
            if deadline < now:
                self._exited.pop(terminal_id, None)
            elif chat in wanted:
                entries.append(
                    {
                        "terminal_id": terminal_id,
                        "status": "exited",
                        "shell": shell_name,
                        "cwd": cwd,
                        "session_key": session_key,
                    }
                )
        return entries

    async def aclose(self) -> None:
        """Stop all poll loops and terminate every live terminal (manager owned here)."""
        self._closed = True
        terminals = list(self._terminals.values())
        tasks = [
            terminal.poll_task
            for terminal in terminals
            if terminal.poll_task is not None and not terminal.poll_task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for terminal in terminals:
            self._terminals.pop(terminal.terminal_id, None)
            terminal.finalized = True
            session = terminal.session
            if session is not None:
                with suppress(Exception):
                    if session.process.returncode is None:
                        await session.kill()
            with suppress(Exception):
                self._manager._sessions.pop(  # pyright: ignore[reportPrivateUsage]
                    terminal.exec_session_id, None
                )
        with suppress(Exception):
            await self._manager.close_all()
        # Mirrors PtyTerminalBridge.aclose: no grace window survives close.
        self._exited.clear()

    # -- internals ----------------------------------------------------------

    async def _submit_line(self, terminal: _ShellTerminal, line: str) -> None:
        if not terminal.restricted:
            # Full access keeps the original immediate per-line submission.
            if not line.strip():
                await self._write_raw(terminal, "\n")
                return
            await self._write_raw(terminal, line + "\n")
            return
        # Restricted: assemble the whole logical command before guarding, so a deny
        # token split across physical lines cannot slip past a per-line check.
        terminal.pending_command += line + "\n"
        if len(terminal.pending_command) > MAX_TERMINAL_WRITE_CHARS:
            await self._reject_segment(terminal, _PENDING_COMMAND_OVERFLOW)
            return
        state = analyze_restricted_segment(terminal.pending_command)
        if state == _SEGMENT_CONTINUATION:
            return
        if state == _SEGMENT_REJECT:
            await self._reject_segment(terminal, _MULTILINE_GUARD_REJECT)
            return
        command = terminal.pending_command
        terminal.pending_command = ""
        blocked = self._guard_command_segment(terminal, command)
        if blocked is not None:
            await self._reject_segment(terminal, blocked)
            return
        await self._write_raw(terminal, command)

    async def _reject_segment(self, terminal: _ShellTerminal, message: str) -> None:
        """Emit one fail-closed rejection: stderr text + ``terminal_exit`` code -1."""
        terminal.pending_command = ""
        await self._emit(terminal, "stderr", f"{message}\n")
        if terminal.closing is None:
            terminal.closing = "blocked"
        await self._finalize_exit(terminal, -1)

    def _guard_command_segment(self, terminal: _ShellTerminal, command: str) -> str | None:
        # Guard the assembled text and its line-continuation-joined view so a deny
        # token broken across physical lines (e.g. ``rm \`` + newline + ``-rf``) is
        # still matched the way the interactive shell will execute the joined form.
        for variant in self._guard_variants(command):
            blocked = self._guard_line(terminal, variant)
            if blocked is not None:
                return blocked
        return None

    @staticmethod
    def _guard_variants(command: str) -> list[str]:
        joined = join_line_continuations(command)
        return [command] if joined == command else [command, joined]

    def _guard_line(self, terminal: _ShellTerminal, line: str) -> str | None:
        token = bind_workspace_scope(terminal.scope)
        try:
            return self._guard_tool._guard_command(  # pyright: ignore[reportPrivateUsage]
                line,
                terminal.cwd,
                restrict_to_workspace=True,
                workspace_root=terminal.cwd,
            )
        except Exception:
            logger.exception("webui terminal guard failed")
            return "Error: terminal command blocked by safety guard"
        finally:
            reset_workspace_scope(token)

    async def _write_raw(self, terminal: _ShellTerminal, chars: str) -> None:
        session = terminal.session
        if session is None or terminal.finalized:
            return
        with suppress(Exception):
            # The exit transition (and its output flush) is owned by the poll
            # loop; stdin errors here are surfaced by the process exiting.
            await session.write(chars)

    async def _poll_loop(self, terminal: _ShellTerminal) -> None:
        try:
            while not self._closed and not terminal.finalized:
                await asyncio.sleep(self._poll_interval_s)
                session = terminal.session
                if session is None or terminal.finalized:
                    return
                # Keep the manager reaper out of it; expiry is bridge-owned.
                session.last_access = time.monotonic()
                stdout, stderr = await self._drain(terminal)
                if stdout:
                    await self._emit(terminal, "stdout", stdout)
                if stderr:
                    await self._emit(terminal, "stderr", stderr)
                if stdout or stderr:
                    terminal.last_activity = time.monotonic()
                if terminal.finalized:
                    return
                if session.process.returncode is not None:
                    await self._finalize_exit(terminal, session.process.returncode)
                    return
                if time.monotonic() - terminal.last_activity >= self._idle_timeout_s:
                    if terminal.closing is None:
                        terminal.closing = "idle"
                    await self._finalize_exit(terminal, None, expired=True)
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("webui terminal poll loop failed")
            if not terminal.finalized:
                if terminal.closing is None:
                    terminal.closing = "error"
                with suppress(Exception):
                    await self._finalize_exit(terminal, None)

    async def _drain(self, terminal: _ShellTerminal) -> tuple[str, str]:
        session = terminal.session
        if session is None:
            return "", ""
        try:
            async with session._lock:  # pyright: ignore[reportPrivateUsage]
                stdout, _stdout_truncated = session._stdout.drain()  # pyright: ignore[reportPrivateUsage]
                stderr, _stderr_truncated = session._stderr.drain()  # pyright: ignore[reportPrivateUsage]
        except Exception:
            return "", ""
        return stdout, stderr

    async def _finalize_exit(
        self,
        terminal: _ShellTerminal,
        exit_code: int | None,
        *,
        expired: bool = False,
    ) -> None:
        if terminal.finalized:
            return
        # Claim synchronously (single-threaded loop) so kill/idle/blocked/exit
        # racing paths emit exactly one terminal_exit per terminal.
        terminal.finalized = True
        self._terminals.pop(terminal.terminal_id, None)
        now = time.monotonic()
        for stale in [key for key, (_, _, _, deadline, _) in self._exited.items() if deadline < now]:
            self._exited.pop(stale, None)
        self._exited[terminal.terminal_id] = (
            terminal.chat_id,
            terminal.shell,
            terminal.cwd,
            now + EXITED_GRACE_S,
            terminal.session_key,
        )
        task = terminal.poll_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        session = terminal.session
        if session is not None:
            if session.process.returncode is None:
                with suppress(Exception):
                    await session.kill()
            else:
                with suppress(Exception):
                    await asyncio.wait_for(
                        asyncio.gather(
                            session._stdout_task,  # pyright: ignore[reportPrivateUsage]
                            session._stderr_task,  # pyright: ignore[reportPrivateUsage]
                            return_exceptions=True,
                        ),
                        timeout=2.0,
                    )
                with suppress(Exception):
                    ExecTool._release_process_tree(  # pyright: ignore[reportPrivateUsage]
                        session.process
                    )
                with suppress(Exception):
                    _reap_pid(session.process.pid)
            with suppress(Exception):
                self._manager._sessions.pop(  # pyright: ignore[reportPrivateUsage]
                    terminal.exec_session_id, None
                )
            stdout, stderr = await self._drain(terminal)
            if stdout:
                await self._emit(terminal, "stdout", stdout)
            if stderr:
                await self._emit(terminal, "stderr", stderr)
        if expired:
            await self._emit(terminal, "stderr", _TERMINAL_EXPIRED_NOTICE + "\n")
        await self._publish(
            terminal,
            encode_terminal_exit(terminal_id=terminal.terminal_id, exit_code=exit_code),
        )

    async def _emit(
        self,
        terminal: _ShellTerminal,
        stream: TerminalOutputStream,
        text: str,
    ) -> None:
        for chunk in split_output_chunks(text):
            terminal.seq += 1
            await self._publish(
                terminal,
                encode_terminal_output(
                    terminal_id=terminal.terminal_id,
                    seq=terminal.seq,
                    stream=stream,
                    data=chunk,
                ),
            )

    async def _publish(self, terminal: _ShellTerminal, payload: dict[str, Any]) -> None:
        try:
            await terminal.publisher(payload)
        except Exception:
            logger.warning(
                "webui terminal event publish failed for {}", terminal.terminal_id
            )
