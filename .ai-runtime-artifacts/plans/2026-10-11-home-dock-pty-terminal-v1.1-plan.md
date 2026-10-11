---
artifact: plan
route: writing-plans
batch: home-dock-pty-terminal-v1.1
skills:
  - writing-plans
created_at: 2026-10-11
spec: .ai-runtime-artifacts/specs/2026-10-11-home-dock-pty-terminal-v1.1-spec.md
contract: .ai-runtime-artifacts/contracts/2026-10-11-contract-home-start-panel.md
---

# 首页标签式 dock + PTY 真终端（v1.1）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把首页/会话右栏从「卡片内联展开」升级为参考版（deepseek-harness）的标签式 dock，终端升级为 PTY 真终端（xterm 直通、shell 下拉、resize），并落地 T3/T4/B1/B2/D1 走查修复。

**Architecture:** 后端新增 `pty_terminal.py`（POSIX PTY 桥 + available_shells + TerminalSupervisor 门面：restricted/Windows 路由回 v1 管道桥），WS 契约 v2 增量（`terminal_start.shell`、`terminal_resize`、`terminal_started.pty`、接管重绑、kill 60s 幂等宽限、`GET /webui/terminal/shells`）。前端新增 `dock-store.ts` + `StartDock.tsx`（单一组件两宿主：首页 + SessionWorkbench），`TerminalSection` 重构为单终端 `TerminalSurface`（pty 交互 / 行式降级双模式）。

**Tech Stack:** Python 3.11 asyncio + `pty`/`fcntl`/`termios`（POSIX）；webui React 19 + TS + vitest；`@xterm/xterm@6` + 新增 `@xterm/addon-fit`。

**Spec:** `.ai-runtime-artifacts/specs/2026-10-11-home-dock-pty-terminal-v1.1-spec.md`（§3.3/§4.2/§4.4 为计划期修正版，以 spec 现文本为准）
**Contract:** `.ai-runtime-artifacts/contracts/2026-10-11-contract-home-start-panel.md`（Task 5 追加 v2 附录）

## Global Constraints

- 全程中文注释/文档，代码标识符英文；产物只落 `.ai-runtime-artifacts/`。
- 不修改任何 v1 既有帧/端点签名；v2 全部增量（旧客户端对 `pty`/`shell`/resize 不可见即安全忽略）。
- 上限不变：并发终端 8、单 write 帧 ≤64000 字符、idle 1800s、输出单帧 ≤8KB；resize 上限 cols≤500、rows≤200。
- `shell` 白名单正则 `^[a-z]{2,12}$`；候选 POSIX=`zsh|bash|fish`，Windows=`cmd|powershell|pwsh`。
- restricted access_mode 会话本批**保持管道行式守卫路径**（spec §4.4 修正：保守形态），PTY 仅 full-access。
- 命令：后端 `uv run --no-sync pytest tests/<file> -v`；前端 `cd webui && npm run test -- <pattern>`；构建 `cd webui && npm run build`（本机无 bun）；`ruff check nanobot/webui/` 目标文件 0 新增告警。
- 基线豁免照旧（memory「Known baseline failures」：`test_serve_*` 既有失败不算回归）。
- 每任务一个 commit；只 commit 不 push。

## 批次启动（编排者执行，非子任务）

- 双 worktree（两轨文件零交集，独立提交避免 git 锁冲突）：
  - `.harness-worktrees/zhixu/wt-home-dock-pty-backend`，分支 `harness/wt-home-dock-pty-backend`（Track1，Task 1→5）
  - `.harness-worktrees/zhixu/wt-home-dock-pty-webui`，分支 `harness/wt-home-dock-pty-webui`（Track2，Task 6→11）
  均基于 `feature/desktop-app`；尾盘 Task 12/13 在 backend 分支合入 webui 分支后的集成 worktree 执行。
- 跟踪文件：`.ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-11-home-dock-pty-v1.1.md`（append-only，主仓写）。
- Task 7 依赖的契约字段按本计划签名直接写（不必等 Task 5 合入）。

## File Structure

| 动作 | 文件 | 职责 |
| --- | --- | --- |
| Create | `nanobot/webui/pty_terminal.py` | PtyTerminalBridge、available_shells、TerminalSupervisor 门面、create_terminal_bridge |
| Modify | `nanobot/webui/terminal_bridge.py` | 常量 EXITED_GRACE_S（T1）、shell 参数/pty 标志/接管/宽限/resize（T3） |
| Modify | `nanobot/webui/outbound_wire.py` | terminal_started 增 `pty` 字段 |
| Modify | `nanobot/webui/inbound_commands.py` | shell 校验、terminal_resize 帧、kill 宽限、factory 换门面 |
| Modify | `nanobot/webui/ws_http.py` | GET /webui/terminal/shells |
| Create | `tests/test_webui_pty_terminal.py` | PTY 生命周期/接管/宽限/门面 |
| Create | `tests/test_webui_terminal_shells.py` | shells 端点 |
| Modify | `tests/test_webui_terminal.py` | v2 帧/宽限回归 |
| Create | `webui/src/lib/dock-store.ts` | dock 标签表 + sessionStorage 持久化 + 接管清单 |
| Create | `webui/src/components/dock/StartDock.tsx` | 标签头/+ 菜单/shell 子菜单/×/启动器空态/接管条 |
| Create | `webui/src/components/dock/DockFileTab.tsx` | 文件标签（树+预览，home/session 双锚点） |
| Create | `webui/src/components/workbench/TerminalSurface.tsx` | 单终端面板（pty 交互 / 行式降级） |
| Modify | `webui/src/lib/nanobot-client.ts` | terminalStart(shell)、terminalResize |
| Modify | `webui/src/lib/types.ts` | TerminalEvent.started 增 `pty?`；出站联合增 terminal_resize |
| Modify | `webui/src/hooks/useNanobotStream.ts` | TerminalRecord/TerminalLine 增 `pty` 字段 |
| Modify | `webui/src/lib/api.ts` | fetchTerminalShells |
| Modify | `webui/src/components/workbench/BrowserSection.tsx` | `fullHeight` prop（B1） |
| Modify | `webui/src/App.tsx` | 首页挂 StartDock、⌘P/⌘T 直达 dock（B2） |
| Modify | `webui/src/components/workbench/SessionWorkbench.tsx` | 三分区替换为 StartDock |
| Delete | `webui/src/components/home/HomeStartPanel.tsx`、`webui/src/components/workbench/TerminalSection.tsx` | 被 dock/TerminalSurface 取代 |
| Modify | `webui/src/i18n/locales/*/common.json` | dock.* 键 ×10 语言 |

---

# Track 1 — 后端（WU-A → WU-B）

### Task 1: PtyTerminalBridge 核心（R2 探针先行）

**Files:**
- Create: `nanobot/webui/pty_terminal.py`
- Create: `tests/test_webui_pty_terminal.py`
- Modify: `nanobot/webui/terminal_bridge.py`（仅加一个常量）

**Interfaces:**
- Consumes: `terminal_bridge.py` 现有 `DEFAULT_MAX_TERMINALS`、`DEFAULT_TERMINAL_IDLE_TIMEOUT_S`、`MAX_TERMINAL_WRITE_CHARS`、`TerminalEventPublisher`、`split_output_chunks`、`_resolve_shell_program`；`nanobot.security.workspace_access.WorkspaceScope`；`nanobot.agent.tools.shell.ExecTool/ExecToolConfig`。
- Produces: `PtyTerminalBridge`（`start(terminal_id, scope, chat_id, session_key, publisher, shell=None)->dict`、`write(terminal_id, data)->str|None`、`kill(terminal_id)->str|None`、`resize(terminal_id, cols, rows)->str|None`、`owner_chat(id)->str|None`、`exited_owner(id)->str|None`、`has_live(id)->bool`、`list_visible(chat_ids)->list[dict]`、`aclose()`；start 返回 dict 含 `"pty": True`）；`available_shells()->{"shells":[{"id","label","path"}],"default":str|None}`；`SHELL_NAME_RE`；`MAX_PTY_COLS/MAX_PTY_ROWS=500/200`。后续 Task 4 门面与 Task 5 inbound 依赖这些名字。

- [ ] **Step 1（探针 spike，R2）: 写最小生命周期测试并让它通过，验证 pty×asyncio 机制**

```python
# tests/test_webui_pty_terminal.py
"""PTY WebUI 终端桥测试（契约 v2 §4.1；spec R2 探针先行）。"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

import pytest

from nanobot.security.workspace_access import default_workspace_scope
from nanobot.webui.pty_terminal import PtyTerminalBridge

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
```

Run: `uv run --no-sync pytest tests/test_webui_pty_terminal.py -v` → 此时 FAIL（模块不存在），随后 Step 2 实现。

- [ ] **Step 2: 给 terminal_bridge.py 加共享常量**

在 `MAX_TERMINAL_WRITE_CHARS = 64_000` 下一行加：

```python
#: Seconds an exited terminal stays kill-idempotent / visible via exited_owner (T4).
EXITED_GRACE_S = 60.0
```

- [ ] **Step 3: 实现 `nanobot/webui/pty_terminal.py`（桥 + available_shells；门面在 Task 4 追加同一文件）**

```python
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
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.shell import ExecTool, ExecToolConfig
from nanobot.security.workspace_access import WorkspaceScope
from nanobot.webui.outbound_wire import encode_terminal_exit, encode_terminal_output
from nanobot.webui.terminal_bridge import (
    DEFAULT_MAX_TERMINALS,
    DEFAULT_TERMINAL_IDLE_TIMEOUT_S,
    EXITED_GRACE_S,
    MAX_TERMINAL_WRITE_CHARS,
    TerminalEventPublisher,
    split_output_chunks,
)

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
```

- [ ] **Step 4: 跑 Step 1 测试通过**

Run: `uv run --no-sync pytest tests/test_webui_pty_terminal.py -v` → PASS。若 `echo PTY_OK` 超时，先查 `add_reader` 回调是否被 `create_subprocess_exec` 的 child watcher 干扰（asyncio 在 macOS 用 `ThreadedChildWatcher`，不应冲突）；这是 R2 探针要暴露的机制风险，解决前不进 Step 5。

- [ ] **Step 5: 补齐生命周期测试集（同文件追加，全部实现完再跑）**

```python
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
```

（`_TERMINAL_EXPIRED_NOTICE` 测试文件顶部补 import：`from nanobot.webui.pty_terminal import PtyTerminalBridge, _TERMINAL_EXPIRED_NOTICE`）

- [ ] **Step 6: 全绿 + lint + 提交**

Run: `uv run --no-sync pytest tests/test_webui_pty_terminal.py tests/test_webui_terminal.py -v`（后者=管道桥回归，必须不受影响）；`uv run --no-sync ruff check nanobot/webui/pty_terminal.py nanobot/webui/terminal_bridge.py`。

```bash
git add nanobot/webui/pty_terminal.py nanobot/webui/terminal_bridge.py tests/test_webui_pty_terminal.py
git commit -m "feat(webui): PTY 终端桥（契约 v2 §4.1，接管/宽限/idle 回收/resize）"
```

---

### Task 2: GET /webui/terminal/shells 端点

**Files:**
- Modify: `nanobot/webui/ws_http.py`（`_dispatch_misc_routes`，`/api/workspace/files` 分支旁，约 :1270）
- Test: `tests/test_webui_terminal_shells.py`（新建；模式照抄 `tests/test_webui_workspace_files.py` 的 `object.__new__(GatewayHTTPHandler)` + `_request` 工具）

**Interfaces:**
- Consumes: `pty_terminal.available_shells()`（Task 1）。
- Produces: `GET /webui/terminal/shells` → 200 `{"shells":[{"id","label","path"}],"default":str|null}`；401 未带 api token。Task 8 前端 `fetchTerminalShells` 依赖此形状。

- [ ] **Step 1: 写失败测试**

```python
"""GET /webui/terminal/shells 单元测试（契约 v2：shell 白名单目录）。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from websockets.datastructures import Headers

from nanobot.webui.ws_http import GatewayHTTPHandler


def _handler(allowed: bool) -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.config = SimpleNamespace()
    handler.check_api_token = lambda request: allowed
    return handler


def _request(path: str) -> SimpleNamespace:
    return SimpleNamespace(path=path, headers=Headers())


@pytest.mark.asyncio
async def test_shells_route_lists_whitelisted_installed_shells(monkeypatch):
    monkeypatch.setattr(
        "nanobot.webui.pty_terminal.shutil.which",
        lambda name: {"/bin/zsh": "/bin/zsh"}.get(name),
    )
    monkeypatch.setattr("nanobot.webui.pty_terminal.os.environ.get", lambda key, **k: None)
    handler = _handler(True)

    response = await handler._dispatch_misc_routes(
        None, _request("/webui/terminal/shells"), "/webui/terminal/shells"
    )
    assert response is not None and response.status_code == 200
    payload = json.loads(response.body)
    assert [entry["id"] for entry in payload["shells"]] == ["zsh"]
    assert payload["default"] == "zsh"


@pytest.mark.asyncio
async def test_shells_route_requires_api_token(monkeypatch):
    handler = _handler(False)
    response = await handler._dispatch_misc_routes(
        None, _request("/webui/terminal/shells"), "/webui/terminal/shells"
    )
    assert response is not None
    assert response.status_code == 401
```

（若 `which` 是函数内直接 import，改 monkeypatch 目标为 `shutil.which`；以测试先红后绿为准调整，不改生产代码结构。）

Run: `uv run --no-sync pytest tests/test_webui_terminal_shells.py -v` → FAIL（路由不存在，response None）。

- [ ] **Step 2: 实现路由**

`_dispatch_misc_routes` 中，紧跟 `/api/workspace/files` 分支之后加（同 `check_api_token` 鉴权，同 `_http_json_response`）：

```python
        if got == "/webui/terminal/shells":
            return self._handle_terminal_shells(request)
```

在 `_handle_workspace_files` 方法旁新增：

```python
    def _handle_terminal_shells(self, request: WsRequest) -> Response:
        """Whitelisted shells installed on this host (contract v2 §4.2 shell 下拉)."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from nanobot.webui.pty_terminal import available_shells

        return _http_json_response(available_shells())
```

- [ ] **Step 3: 测试转绿 + 回归 + 提交**

`uv run --no-sync pytest tests/test_webui_terminal_shells.py tests/test_webui_workspace_files.py -v`；ruff 目标文件。

```bash
git add nanobot/webui/ws_http.py tests/test_webui_terminal_shells.py
git commit -m "feat(webui): GET /webui/terminal/shells（api token gate）"
```

---

### Task 3: 管道桥升级（pty 标志 / shell 参数 / 接管 / 60s 宽限 / resize 容忍）

**Files:**
- Modify: `nanobot/webui/terminal_bridge.py`
- Test: `tests/test_webui_terminal.py`（追加，不重写既有）

**Interfaces:**
- Consumes: `EXITED_GRACE_S`（Task 1 已加）。
- Produces: `WebuiTerminalBridge` 公开面与 PtyTerminalBridge 对齐：`start(..., shell: str|None=None)` 返回含 `"pty": False`；同 id 存活终端 = 接管重绑（`already_running`）；`kill` 宽限期幂等；`exited_owner(id)`、`has_live(id)`、`resize(terminal_id, cols, rows)->str|None`（no-op，live 返回 None，未知返回 unknown_terminal）。Task 4 门面依赖。

- [ ] **Step 1: 追加失败测试（tests/test_webui_terminal.py 尾部，复用该文件既有 Collector/manager fixture 风格——先读该文件的 `_make_bridge` 类辅助再落笔）**

```python
@_SKIP_WINDOWS
async def test_pipe_bridge_started_result_carries_pty_false(tmp_path):
    bridge = _make_bridge(tmp_path)  # 用本文件既有工厂：ExecSessionManager + WebuiTerminalBridge
    collector = Collector()
    scope = default_workspace_scope(tmp_path, restrict_to_workspace=False)
    try:
        result = await bridge.start(
            terminal_id="term-f", scope=scope, chat_id="c",
            session_key="websocket:c", publisher=collector,
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
            terminal_id="term-g", scope=scope, chat_id="c",
            session_key="websocket:c", publisher=Collector(),
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
            terminal_id="term-t", scope=scope, chat_id="c1",
            session_key="websocket:c1", publisher=first,
        )
        result = await bridge.start(
            terminal_id="term-t", scope=scope, chat_id="c2",
            session_key="websocket:c2", publisher=second,
        )
        assert result["status"] == "already_running"
        assert bridge.owner_chat("term-t") == "c2"
    finally:
        await bridge.aclose()
```

Run: `uv run --no-sync pytest tests/test_webui_terminal.py -k "pty_false or grace or takeover" -v` → FAIL。

- [ ] **Step 2: 实现 terminal_bridge.py 修改**

1. `__init__` 末尾加：`self._exited: dict[str, tuple[str, str, str, float]] = {}  # id -> (chat_id, shell, cwd, deadline)`（用简单 tuple，注释即够）。
2. `start` 签名加 `shell: str | None = None`；`base` 各返回 dict 均含 `"pty": False`；shell 解析替换 `shell = _resolve_shell_program()` 行：

```python
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
```

（后续引用 `shell` 局部变量的地方统一改成 `shell_program`。）
3. existing 分支接管重绑：

```python
        existing = self._terminals.get(terminal_id)
        if existing is not None:
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
```

4. `_finalize_exit` 在 `self._terminals.pop(...)` 后记录宽限元数据（并在插入前惰性清过期项）：

```python
        now = time.monotonic()
        for stale in [key for key, (_, _, _, deadline) in self._exited.items() if deadline < now]:
            self._exited.pop(stale, None)
        self._exited[terminal.terminal_id] = (
            terminal.chat_id, terminal.shell, terminal.cwd, now + EXITED_GRACE_S,
        )
```

5. 新方法（放在 `kill` 旁）：

```python
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
```

6. `kill` 现有开头（unknown 判定）改为宽限容忍：

```python
        terminal = self._terminals.get(terminal_id)
        if terminal is None or terminal.finalized:
            return None if self.exited_owner(terminal_id) is not None else "unknown_terminal"
```

7. `list_visible` 尾部合并 `_exited` 宽限条目（`status:"exited"`）。

- [ ] **Step 3: 全绿 + 提交**

`uv run --no-sync pytest tests/test_webui_terminal.py -v`；ruff。

```bash
git add nanobot/webui/terminal_bridge.py tests/test_webui_terminal.py
git commit -m "feat(webui): 管道桥 v2——pty 标志/shell 参数/接管重绑/kill 60s 幂等宽限（T4）"
```

---

### Task 4: TerminalSupervisor 门面 + 工厂接线

**Files:**
- Modify: `nanobot/webui/pty_terminal.py`（文件尾追加门面与工厂）
- Modify: `nanobot/webui/inbound_commands.py`（仅 `_terminal_bridge()` 工厂体，:504-534）
- Test: `tests/test_webui_pty_terminal.py`（追加）

**Interfaces:**
- Produces: `TerminalSupervisor`（start/write/kill/resize/owner_chat/exited_owner/has_live/list_visible/aclose；签名合并 pipe+pty 全集）与 `create_terminal_bridge(exec_manager, *, exec_config=None, webui_allow_local_service_access=True, max_terminals=DEFAULT_MAX_TERMINALS, idle_timeout_s=None)`；路由规则：`scope.restrict_to_workspace` 或非 POSIX → pipe，否则 pty；全局 live ≤ `max_terminals`。
- Consumes: Task 3 pipe 新签名、Task 1 PtyTerminalBridge。

- [ ] **Step 1: 追加失败测试**

```python
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
```

- [ ] **Step 2: 实现门面（pty_terminal.py 尾部）**

```python
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
        return len(self._pty._terminals) + len(self._pipe._terminals)  # pyright: ignore[reportPrivateUsage]

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
        target = self._pty if (self._pty is not None and not scope.restrict_to_workspace) else self._pipe
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
            if error == "unknown_terminal":
                self._owner.pop(terminal_id, None)
                # fall through to the other bridge before reporting unknown
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
```

（pty_terminal.py 头部 import 补 `Iterable`、`WorkspaceScope` 已有、`ExecToolConfig` 已有、`DEFAULT_MAX_TERMINALS` 已有。）

- [ ] **Step 3: 接线 inbound_commands.py `_terminal_bridge()`（:504-534 整体替换工厂体）**

```python
    def _terminal_bridge(self) -> Any:
        if self._terminal_bridge_instance is None:
            from nanobot.agent.tools.exec_session import ExecSessionManager
            from nanobot.webui.pty_terminal import (
                DEFAULT_MAX_TERMINALS as _TERMINAL_MAX,
            )
            from nanobot.webui.pty_terminal import create_terminal_bridge
            from nanobot.webui.terminal_bridge import DEFAULT_TERMINAL_IDLE_TIMEOUT_S

            exec_config = None
            allow_local_service_access = True
            try:
                from nanobot.config.loader import load_config

                config = load_config(self.gateway.settings.config.path)
                exec_config = config.tools.exec
                allow_local_service_access = config.tools.webui_allow_local_service_access
            except Exception:
                logger.warning(
                    "terminal bridge could not read exec config; using defaults"
                )
            manager = ExecSessionManager(
                max_sessions=_TERMINAL_MAX,
                idle_timeout=DEFAULT_TERMINAL_IDLE_TIMEOUT_S,
            )
            self._terminal_bridge_instance = create_terminal_bridge(
                manager,
                exec_config=exec_config,
                webui_allow_local_service_access=allow_local_service_access,
            )
        return self._terminal_bridge_instance
```

（`DEFAULT_MAX_TERMINALS` 从 `terminal_bridge` 导入的既有行随之调整；`Iterable`/`WorkspaceScope` 若 pty_terminal 未 import 需补。）

- [ ] **Step 4: 全绿 + 提交**

`uv run --no-sync pytest tests/test_webui_pty_terminal.py tests/test_webui_terminal.py -v`；ruff 两文件。

```bash
git add nanobot/webui/pty_terminal.py nanobot/webui/inbound_commands.py tests/test_webui_pty_terminal.py
git commit -m "feat(webui): TerminalSupervisor 门面——PTY/管道双桥路由 + 全局上限 8"
```

---

### Task 5: 契约 v2 接线（outbound pty 字段 / shell 校验 / terminal_resize 帧 / kill 宽限 / 契约文档）

**Files:**
- Modify: `nanobot/webui/outbound_wire.py`（TerminalStartedWirePayload + encode_terminal_started）
- Modify: `nanobot/webui/inbound_commands.py`（dispatch 链、`_handle_terminal_start`、`_handle_terminal_kill`、新增 `_handle_terminal_resize`）
- Modify: `.ai-runtime-artifacts/contracts/2026-10-11-contract-home-start-panel.md`（尾部追加 v2 附录）
- Test: `tests/test_webui_terminal.py`（追加；先读该文件既有 router 测试段，沿用其 router/transport fake 工厂）

**Interfaces:**
- Consumes: Task 3/4 桥的 `exited_owner`/`resize`/`start(shell=)`；`SHELL_NAME_RE`（pty_terminal）。
- Produces: wire `terminal_started` 恒含 `"pty": bool`；入站帧 `terminal_resize {terminal_id, cols, rows}`；错误 detail `invalid_terminal_resize`；shell 非法 → `terminal_started status=error message=shell_unavailable`。

- [ ] **Step 1: 失败测试**（router 工厂沿用 test_webui_terminal.py 既有 `_make_router(...)` 段；用 FakeBridge 注入 `_terminal_bridge_instance`）

```python
class FakeBridge:
    def __init__(self) -> None:
        self.starts: list[dict[str, Any]] = []
        self.resizes: list[tuple[str, int, int]] = []
        self.kills: list[str] = []
        self._grace: dict[str, str] = {"term-done": "chat-1"}

    async def start(self, *, terminal_id, scope, chat_id, session_key, publisher, shell=None):
        self.starts.append({"terminal_id": terminal_id, "shell": shell})
        return {
            "terminal_id": terminal_id, "shell": "/bin/zsh", "cwd": str(scope.project_path),
            "status": "ok", "message": None, "pty": True,
        }

    def owner_chat(self, terminal_id): return None
    def exited_owner(self, terminal_id): return self._grace.get(terminal_id)
    async def resize(self, *, terminal_id, cols, rows):
        self.resizes.append((terminal_id, cols, rows)); return None
    async def kill(self, *, terminal_id):
        self.kills.append(terminal_id); return None


@pytest.mark.asyncio
async def test_resize_frame_reaches_bridge(...):
    # 用本文件既有 router fixture 建 router，塞入 FakeBridge；connection 在
    # _webui_connections、chat-1 已 attach（同既有 terminal 帧测试的建法）。
    await router.dispatch(connection, client_id, {
        "type": "terminal_resize", "terminal_id": "term-1", "cols": 120, "rows": 40,
    })
    assert bridge.resizes == [("term-1", 120, 40)]


@pytest.mark.asyncio
async def test_resize_rejects_out_of_bounds(...):
    await router.dispatch(connection, client_id, {
        "type": "terminal_resize", "terminal_id": "term-1", "cols": 9999, "rows": 40,
    })
    assert sent_errors contains detail=="invalid_terminal_resize"   # 按 fixture 断言风格写


@pytest.mark.asyncio
async def test_kill_on_grace_terminal_is_silent_noop(...):
    await router.dispatch(connection, client_id, {"type": "terminal_kill", "terminal_id": "term-done"})
    assert bridge.kills == ["term-done"] and no error frame sent


@pytest.mark.asyncio
async def test_start_rejects_bad_shell_name_before_bridge(...):
    await router.dispatch(connection, client_id, {
        "type": "terminal_start", "terminal_id": "term-1", "session_key": "websocket:chat-1",
        "shell": "rm -rf /",
    })
    assert bridge.starts == []
    assert last sent terminal_started status=="error" message=="shell_unavailable"


def test_encode_terminal_started_always_carries_pty():
    payload = encode_terminal_started(
        terminal_id="t", shell="/bin/zsh", cwd="/x", status="ok", pty=True,
    )
    assert payload["pty"] is True
    payload = encode_terminal_started(terminal_id="t", shell="", cwd="", status="error")
    assert payload["pty"] is False
```

（`...` 处 = 复用文件内既有 router/connection fixture 参数组；每个 `pytest.mark.asyncio` 测试的 fixture 名以现有 test_webui_terminal.py 中「WebUICommandRouter dispatch」段实际存在的辅助为准，实现者先读后写，断言语义不变。）

- [ ] **Step 2: outbound_wire.py**

```python
class TerminalStartedWirePayload(TypedDict):
    ...
    status: TerminalWireStatus
    message: NotRequired[str]
    pty: bool
```

`encode_terminal_started` 增加关键字参数 `pty: bool = False`，payload 中 `"pty": bool(pty)`。（其余四个 encoder 不动。）

- [ ] **Step 3: inbound_commands.py**

1. dispatch 链（:474-479 附近）在 `terminal_kill` 分支前插入：

```python
        if command_type == "terminal_resize":
            await self._handle_terminal_resize(connection, client_id, envelope)
            return
```

2. `_handle_terminal_start`：scope 解析成功后、`bridge.start` 前加 shell 校验；`bridge.start(...)` 传 `shell=`；encode 传 `pty=`：

```python
        raw_shell = envelope.get("shell")
        if raw_shell is not None and (
            not isinstance(raw_shell, str) or not SHELL_NAME_RE.fullmatch(raw_shell)
        ):
            payload = encode_terminal_started(
                terminal_id=terminal_id,
                shell="",
                cwd=str(scope.project_path),
                status="error",
                message="shell_unavailable",
            )
            await self._publish_terminal(chat_id, connection, payload)
            return
        ...
        result = await bridge.start(
            terminal_id=terminal_id, scope=scope, chat_id=chat_id,
            session_key=session_key, publisher=self._terminal_publisher(chat_id),
            shell=raw_shell if isinstance(raw_shell, str) else None,
        )
        payload = encode_terminal_started(
            terminal_id=terminal_id,
            shell=str(result.get("shell") or ""),
            cwd=str(result.get("cwd") or scope.project_path),
            status=result.get("status", "error"),
            message=result.get("message"),
            pty=bool(result.get("pty")),
        )
```

（文件顶部从 `nanobot.webui.pty_terminal` import `SHELL_NAME_RE`——pty_terminal 平台安全可顶层导入；或沿用现有 terminal 段的就近 import 风格。）

3. 新增 handler（放 `_handle_terminal_kill` 前）：

```python
    async def _handle_terminal_resize(
        self,
        connection: ServerConnection,
        client_id: str,
        envelope: dict[str, Any],
    ) -> None:
        if connection not in self._webui_connections or not self._transport.is_allowed(client_id):
            await self._terminal_access_error(connection, "access_denied")
            return
        terminal_id = self._validate_terminal_id(envelope)
        if terminal_id is None:
            await self._terminal_access_error(connection, "invalid_terminal_id")
            return
        from nanobot.webui.pty_terminal import MAX_PTY_COLS, MAX_PTY_ROWS

        cols, rows = envelope.get("cols"), envelope.get("rows")
        if (
            not isinstance(cols, int) or isinstance(cols, bool) or not 1 <= cols <= MAX_PTY_COLS
            or not isinstance(rows, int) or isinstance(rows, bool) or not 1 <= rows <= MAX_PTY_ROWS
        ):
            await self._terminal_access_error(
                connection, "invalid_terminal_resize", terminal_id=terminal_id
            )
            return
        bridge = self._terminal_bridge_instance
        owner = bridge.owner_chat(terminal_id) if bridge is not None else None
        if owner is None:
            await self._terminal_access_error(
                connection, "unknown_terminal", terminal_id=terminal_id
            )
            return
        if owner not in self._transport.webui_connection_chats(connection):
            await self._terminal_access_error(
                connection, "terminal_not_attached", terminal_id=terminal_id
            )
            return
        error = await bridge.resize(terminal_id=terminal_id, cols=cols, rows=rows)
        if error:
            await self._terminal_access_error(connection, error, terminal_id=terminal_id)
```

4. `_handle_terminal_kill` owner 判定加宽限（T4）：

```python
        bridge = self._terminal_bridge_instance
        owner = bridge.owner_chat(terminal_id) if bridge is not None else None
        if owner is None and bridge is not None:
            owner = bridge.exited_owner(terminal_id)  # 60s grace: kill is idempotent
        if owner is None:
            ...（既有 unknown_terminal 错误帧不变）
```

5. 常量收口：`inbound_commands.py` 内既有的 cols/rows 魔法数不出现；`MAX_PTY_*` 只在 pty_terminal 定义（单一真相源）。

- [ ] **Step 4: 契约文档 v2 附录**（`contracts/2026-10-11-contract-home-start-panel.md` 末尾追加，不改 §1 原文）

```markdown
## 附录 A：契约 v2（2026-10-11 home-dock-pty v1.1 增量）

- 入站 `terminal_start` 增可选 `shell`（`^[a-z]{2,12}$`，POSIX=zsh|bash|fish，Windows=cmd|powershell|pwsh）；非法/本机缺失 → `terminal_started status=error message=shell_unavailable`。同 id 命中存活终端 = 接管：publisher/chat 归属重绑请求方，`status=already_running`。
- 入站新帧 `terminal_resize {terminal_id, cols≤500, rows≤200}`（过同款 gate；越界 detail=`invalid_terminal_resize`）。管道桥视为 no-op。
- 出站 `terminal_started` 恒含 `pty: bool`；`pty:false` 沿用行式帧语义（write 含 \n 提交、不回显），`pty:true` 直通不回显（回显由 PTY 负责），`terminal_output.stream` 恒 `stdout`（expiry 通知除外，仍 stderr）。
- `terminal_kill` 对 60s 内已退出终端 = 幂等 no-op（不发错误帧；T4）。
- 新端点 `GET /webui/terminal/shells`（`check_api_token`）→ `{shells:[{id,label,path}], default}`。
- 安全：restricted 会话本批保持管道守卫路径；PTY 仅 full-access（spec §4.4）。
```

- [ ] **Step 5: 全绿 + 提交**

`uv run --no-sync pytest tests/test_webui_terminal.py tests/test_webui_pty_terminal.py -v`；`ruff check nanobot/webui/`；确认 `test_webui_workspace_files.py` 与 `nanobot/channels/websocket/tests/test_websocket_http_routes.py`（bootstrap 邻近路由表如有清单需同步）不回归。

```bash
git add nanobot/webui/outbound_wire.py nanobot/webui/inbound_commands.py tests/test_webui_terminal.py .ai-runtime-artifacts/contracts/2026-10-11-contract-home-start-panel.md
git commit -m "feat(webui): WS 契约 v2——pty 字段/shell 白名单/terminal_resize/kill 宽限 + 契约附录"
```

---

# Track 2 — 前端（WU-D → WU-E）

### Task 6: dock store + @xterm/addon-fit 安装

**Files:**
- Modify: `webui/package.json`（`npm install @xterm/addon-fit`）
- Create: `webui/src/lib/dock-store.ts`
- Test: `webui/src/tests/dock-store.test.ts`

**Interfaces:**
- Produces（供 Task 7/9/10/11 使用，名字精确）：`DockTabKind = "file"|"browser"|"terminal"`；`DockTab {id,kind,title}`；`HOME_DOCK_SCOPE = "websocket:home"`；`MAX_DOCK_TERMINAL_TABS = 8`；单例 `dockStore`：`getSnapshot(scope): {tabs: DockTab[]; activeId: string|null}`、`subscribe(listener): () => void`、`openFile(scope): string`、`openBrowser(scope): string`、`openTerminal(scope, terminalId, shell: string|null): string|null`（越限返回 null；已存在=聚焦）、`focusTab(scope, tabId)`、`closeTab(scope, tabId)`、`setTerminalTitle(scope, terminalId, shellPath)`（title=`>. ${basename}`）、`persistedTerminals(scope): {id,shell}[]`、`forgetPersisted(scope, terminalId)`；hook `useDockTabs(scope): {tabs, activeId}`。file 标签 id=`"file"`、browser id=`"browser"`（天然单例+聚焦）。

- [ ] **Step 1: 安装依赖**

Run: `cd webui && npm install @xterm/addon-fit` → package.json 出现 `"@xterm/addon-fit"`；`npm run build` 冒烟不破坏。

- [ ] **Step 2: 失败测试**

```ts
import { beforeEach, describe, expect, it } from "vitest";
import {
  HOME_DOCK_SCOPE, MAX_DOCK_TERMINAL_TABS, dockStore, useDockTabs,
} from "@/lib/dock-store";

describe("dock store", () => {
  beforeEach(() => {
    sessionStorage.clear();
    dockStore.resetForTests();
  });

  it("file/browser tabs are singletons that focus instead of duplicating", () => {
    const first = dockStore.openFile(HOME_DOCK_SCOPE);
    const again = dockStore.openFile(HOME_DOCK_SCOPE);
    expect(again).toBe(first);
    expect(dockStore.getSnapshot(HOME_DOCK_SCOPE).tabs.filter((t) => t.kind === "file")).toHaveLength(1);
    dockStore.openTerminal(HOME_DOCK_SCOPE, "term-1", "/bin/zsh");
    dockStore.focusTab(HOME_DOCK_SCOPE, "term-1");
    dockStore.openFile(HOME_DOCK_SCOPE);
    expect(dockStore.getSnapshot(HOME_DOCK_SCOPE).activeId).toBe("file");
  });

  it("terminal tabs are titled >. <shell> and capped at 8", () => {
    for (let i = 1; i <= MAX_DOCK_TERMINAL_TABS; i += 1) {
      expect(dockStore.openTerminal(HOME_DOCK_SCOPE, `term-${i}`, "/bin/zsh")).toBe(`term-${i}`);
    }
    expect(dockStore.openTerminal(HOME_DOCK_SCOPE, "term-overflow", null)).toBeNull();
    dockStore.setTerminalTitle(HOME_DOCK_SCOPE, "term-1", "/bin/zsh");
    const tab = dockStore.getSnapshot(HOME_DOCK_SCOPE).tabs.find((t) => t.id === "term-1");
    expect(tab?.title).toBe(">. zsh");
  });

  it("persists terminal ids for the refresh takeover banner and forgets on close", () => {
    dockStore.openTerminal(HOME_DOCK_SCOPE, "term-a", "/bin/bash");
    expect(dockStore.persistedTerminals(HOME_DOCK_SCOPE)).toEqual([{ id: "term-a", shell: "/bin/bash" }]);
    dockStore.closeTab(HOME_DOCK_SCOPE, "term-a");
    expect(dockStore.persistedTerminals(HOME_DOCK_SCOPE)).toEqual([]);
  });

  it("scopes are isolated between home and a session", () => {
    dockStore.openFile(HOME_DOCK_SCOPE);
    dockStore.openBrowser("websocket:chat-9");
    expect(dockStore.getSnapshot(HOME_DOCK_SCOPE).tabs.map((t) => t.kind)).toEqual(["file"]);
    expect(dockStore.getSnapshot("websocket:chat-9").tabs.map((t) => t.kind)).toEqual(["browser"]);
  });
});
```

（store 额外暴露 `resetForTests()`，仅供测试。）

Run: `cd webui && npm run test -- dock-store` → FAIL。

- [ ] **Step 3: 实现 `webui/src/lib/dock-store.ts`**

```ts
import { useSyncExternalStore } from "react";

export type DockTabKind = "file" | "browser" | "terminal";

export interface DockTab {
  id: string;
  kind: DockTabKind;
  title: string;
}

export interface DockScopeSnapshot {
  tabs: DockTab[];
  activeId: string | null;
}

export const HOME_DOCK_SCOPE = "websocket:home";
export const MAX_DOCK_TERMINAL_TABS = 8;
const PERSIST_PREFIX = "nanobot.dock.terminals.";

export interface PersistedTerminal {
  id: string;
  shell: string;
}

export function dockTerminalTitle(shell: string | null): string {
  const name = shell ? shell.split(/[\\/]/).pop() : "";
  return `>. ${name || "terminal"}`;
}

function loadPersisted(scope: string): PersistedTerminal[] {
  try {
    const raw = sessionStorage.getItem(PERSIST_PREFIX + scope);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is PersistedTerminal =>
        !!item && typeof item === "object" &&
        typeof (item as { id?: unknown }).id === "string" &&
        typeof (item as { shell?: unknown }).shell === "string",
    );
  } catch {
    return [];
  }
}

function savePersisted(scope: string, entries: PersistedTerminal[]): void {
  try {
    sessionStorage.setItem(PERSIST_PREFIX + scope, JSON.stringify(entries));
  } catch {
    /* private mode: takeover list lives only for this page lifetime */
  }
}

const EMPTY_SNAPSHOT: DockScopeSnapshot = { tabs: [], activeId: null };

type Listener = () => void;

class DockStore {
  private snapshots = new Map<string, DockScopeSnapshot>();
  private listeners = new Set<Listener>();

  getSnapshot(scope: string): DockScopeSnapshot {
    return this.snapshots.get(scope) ?? EMPTY_SNAPSHOT;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  openFile(scope: string): string {
    return this.openSingleton(scope, "file", "file");
  }

  openBrowser(scope: string): string {
    return this.openSingleton(scope, "browser", "browser");
  }

  openTerminal(scope: string, terminalId: string, shell: string | null): string | null {
    const current = this.getSnapshot(scope);
    const existing = current.tabs.find((tab) => tab.id === terminalId);
    if (existing) {
      this.setSnapshot(scope, { tabs: current.tabs, activeId: terminalId });
      return terminalId;
    }
    const liveCount = current.tabs.filter((tab) => tab.kind === "terminal").length;
    if (liveCount >= MAX_DOCK_TERMINAL_TABS) return null;
    const tabs = [...current.tabs, { id: terminalId, kind: "terminal" as const, title: dockTerminalTitle(shell) }];
    this.setSnapshot(scope, { tabs, activeId: terminalId });
    this.writePersisted(scope, tabs);
    return terminalId;
  }

  focusTab(scope: string, tabId: string): void {
    const current = this.getSnapshot(scope);
    if (!current.tabs.some((tab) => tab.id === tabId)) return;
    this.setSnapshot(scope, { tabs: current.tabs, activeId: tabId });
  }

  closeTab(scope: string, tabId: string): void {
    const current = this.getSnapshot(scope);
    const tabs = current.tabs.filter((tab) => tab.id !== tabId);
    const activeId = current.activeId === tabId ? tabs[tabs.length - 1]?.id ?? null : current.activeId;
    this.setSnapshot(scope, { tabs, activeId });
    this.writePersisted(scope, tabs);
  }

  setTerminalTitle(scope: string, terminalId: string, shellPath: string): void {
    const current = this.getSnapshot(scope);
    if (!current.tabs.some((tab) => tab.id === terminalId)) return;
    const tabs = current.tabs.map((tab) =>
      tab.id === terminalId ? { ...tab, title: dockTerminalTitle(shellPath) } : tab,
    );
    this.setSnapshot(scope, { tabs, activeId: current.activeId });
    this.writePersisted(scope, tabs);
  }

  persistedTerminals(scope: string): PersistedTerminal[] {
    const known = new Set(this.getSnapshot(scope).tabs.map((tab) => tab.id));
    return loadPersisted(scope).filter((entry) => !known.has(entry.id));
  }

  forgetPersisted(scope: string, terminalId: string): void {
    savePersisted(scope, loadPersisted(scope).filter((entry) => entry.id !== terminalId));
  }

  resetForTests(): void {
    this.snapshots.clear();
  }

  private openSingleton(scope: string, kind: "file" | "browser", id: string): string {
    const current = this.getSnapshot(scope);
    if (!current.tabs.some((tab) => tab.id === id)) {
      const title = kind === "file" ? "Files" : "Browser";
      this.setSnapshot(scope, { tabs: [...current.tabs, { id, kind, title }], activeId: id });
    } else {
      this.setSnapshot(scope, { tabs: current.tabs, activeId: id });
    }
    return id;
  }

  private writePersisted(scope: string, tabs: DockTab[]): void {
    savePersisted(
      scope,
      tabs
        .filter((tab) => tab.kind === "terminal")
        .map((tab) => ({ id: tab.id, shell: tab.title })),
    );
  }

  private setSnapshot(scope: string, next: DockScopeSnapshot): void {
    this.snapshots.set(scope, next);
    for (const listener of [...this.listeners]) listener();
  }
}

export const dockStore = new DockStore();

export function useDockTabs(scope: string): DockScopeSnapshot {
  return useSyncExternalStore(
    (listener) => dockStore.subscribe(listener),
    () => dockStore.getSnapshot(scope),
    () => dockStore.getSnapshot(scope),
  );
}
```

注意：`writePersisted` 存的是 `tab.title`（`>. zsh` 形态）还是原始 shell 路径——接管条标题需要可解析形式。实现改为：persisted entry 的 shell 用 `setTerminalTitle`/`openTerminal` 收到的原始 `shell` 路径（在类内维护 `terminalShells: Map<string,string>` scope 无关即可）；测试第三例相应把 `dockStore.openTerminal(..., "/bin/bash")` 后断言 `{ id: "term-a", shell: "/bin/bash" }`。落地时以「persist 原始路径」为准实现。

- [ ] **Step 4: 测试绿 + 提交**

`cd webui && npm run test -- dock-store`。

```bash
git add webui/package.json webui/package-lock.json webui/src/lib/dock-store.ts webui/src/tests/dock-store.test.ts
git commit -m "feat(webui): dock store（标签表/sessionStorage 接管清单）+ @xterm/addon-fit"
```

---

### Task 7: TerminalSurface（PTY 交互 + 行式降级），删 TerminalSection

**Files:**
- Create: `webui/src/components/workbench/TerminalSurface.tsx`
- Delete: `webui/src/components/workbench/TerminalSection.tsx`
- Modify: `webui/src/lib/nanobot-client.ts`、`webui/src/lib/types.ts`、`webui/src/hooks/useNanobotStream.ts`
- Test: Create `webui/src/tests/terminal-surface.test.tsx`（迁移 terminal-section.test.tsx 的 store 用例，新增 pty 直通/resize 用例；删除旧测试文件）

**Interfaces:**
- Consumes: `useTerminalStore`、client（本任务扩签名）。
- Produces: `TerminalSurface(props: { terminalId: string; sessionKey?: string|null; createTerminalInstance?: (tabId:string)=>TerminalEmulator; generateTerminalId?: never })`，具名导出；扩展 `TerminalEmulator`（含 `onData?/onResize?/fit?/size?`）；`client.terminalStart(id, sessionKey?, shell?)`、`client.terminalResize(id, cols, rows)`；`TerminalRecord.pty: boolean`；types.ts started 事件 `pty?: boolean`、出站联合 `terminal_resize`。

- [ ] **Step 1: 类型与 client 扩参**

`webui/src/lib/types.ts`：`terminal_started` 分支加 `pty?: boolean;`；出站 union 加 `{ type: "terminal_resize"; terminal_id: string; cols: number; rows: number }`。

`webui/src/lib/nanobot-client.ts`（:1081-1110 段替换）：

```ts
  terminalStart(terminalId: string, sessionKey?: string | null, shell?: string | null): void {
    if (!TERMINAL_ID_PATTERN.test(terminalId)) return;
    this.queueSend({
      type: "terminal_start",
      terminal_id: terminalId,
      ...(sessionKey ? { session_key: sessionKey } : {}),
      ...(shell ? { shell } : {}),
    });
  }

  terminalWrite(terminalId: string, data: string): void { /* 既有不变 */ }

  terminalResize(terminalId: string, cols: number, rows: number): void {
    if (!TERMINAL_ID_PATTERN.test(terminalId)) return;
    if (!Number.isInteger(cols) || cols < 1 || cols > 500) return;
    if (!Number.isInteger(rows) || rows < 1 || rows > 200) return;
    this.queueSend({ type: "terminal_resize", terminal_id: terminalId, cols, rows });
  }

  terminalKill(terminalId: string): void { /* 既有不变 */ }
  terminalList(): void { /* 既有不变 */ }
```

`webui/src/hooks/useNanobotStream.ts`：`TerminalRecord` 加 `pty: boolean;`（`createEmptyRecord` 初始 `pty: false`）；`applyEvent` 的 `terminal_started` 分支 upsert 中加 `pty: event.pty === true`。

- [ ] **Step 2: 迁移/新增失败测试（要点断言，实现细节自拟）**

```tsx
// terminal-surface.test.tsx（FakeEmulator 记录 onData 订阅与 write；FakeClient 记录帧）
it("interactive mode (pty:true): raw onData frames go to terminalWrite, no echo, no input box", () => {
  // store 注入 started {pty:true,status:"running"} → 渲染后：
  // fake emulator 的 onData 回调发 "ls\r" → client.terminalWrite 被调且无 appendEcho
  // 不渲染 [data-testid=terminal-input]
});
it("interactive mode fits and sends terminalResize on container resize", () => {
  // fake emulator 报 size {cols:120,rows:40} 时 client.terminalResize 收到 (id,120,40)
});
it("line mode (pty absent/false): input box kept, submit sends line+\\n and echoes", () => {
  // 既有 v1 行为回归（迁移自 terminal-section.test.tsx）
});
it("failure tokens: shell_unavailable maps to a stable label", () => {
  // started {status:"error", message:"shell_unavailable"} → 文案
});
```

（迁移原则：v1 的 store/frames/多 tab 断言里属于 TerminalSection 容器职责的（tab strip、+ 按钮）不迁——容器交给 dock；单终端渲染/输入/失败文案断言全部迁到 surface 文件。旧 `webui/src/tests/terminal-section.test.tsx` 删除，`home-start-panel.test.tsx` 中 terminalSlot 相关断言随 Task 9 一并重写。）

Run: `npm run test -- terminal-surface` → FAIL（组件不存在）。

- [ ] **Step 3: 实现 TerminalSurface.tsx**

```tsx
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

import { useTerminalStore, type TerminalRecord } from "@/hooks/useNanobotStream";
import { useClient } from "@/providers/ClientProvider";
import { failureLabel, MAX_OUTPUT_ROWS_HINT } from "@/components/workbench/terminal-shared";
```

（为避免和待删文件互相 import，先建 `webui/src/components/workbench/terminal-shared.ts`：把 TerminalSection.tsx 里的 `failureLabel`（加 `shell_unavailable`→`t("startPanel.shellUnavailable")` 与 `terminal_start_failed` 兜底两个分支）与 `defaultGenerateTerminalId`、`CLEAR_SCREEN` 原样抽出为具名导出；`MAX_OUTPUT_ROWS_HINT` 不存在则删掉该 import 项——以抽出实际内容为准，别造新抽象。）

主体：

```tsx
export interface TerminalEmulator {
  open(element: HTMLElement): void;
  write(data: string): void;
  dispose(): void;
  onData?(callback: (data: string) => void): { dispose(): void } | void;
  onResize?(callback: (size: { cols: number; rows: number }) => void): { dispose(): void } | void;
  fit?(): void;
  size?: { cols: number; rows: number };
}

interface TerminalSurfaceProps {
  terminalId: string;
  sessionKey?: string | null;
  createTerminalInstance?: (tabId: string) => TerminalEmulator;
}

function createInteractiveTerminal(): TerminalEmulator {
  const terminal = new Terminal({
    disableStdin: false,
    convertEol: false,
    scrollback: 5000,
    fontSize: 12,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    cursorBlink: true,
    theme: { background: "transparent" },
  });
  const fitAddon = new FitAddon();
  terminal.loadAddon(fitAddon);
  return {
    open: (element) => {
      terminal.open(element);
      fitAddon.fit();
    },
    write: (data) => terminal.write(data),
    dispose: () => terminal.dispose(),
    onData: (callback) => terminal.onData(callback),
    onResize: (callback) => terminal.onResize(callback),
    fit: () => fitAddon.fit(),
    get size() {
      return { cols: terminal.cols, rows: terminal.rows };
    },
  };
}

function createLineTerminal(): TerminalEmulator {
  const terminal = new Terminal({
    disableStdin: true,
    convertEol: true,
    scrollback: 5000,
    fontSize: 12,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    cursorBlink: false,
    theme: { background: "transparent" },
  });
  return { open: (el) => terminal.open(el), write: (d) => terminal.write(d), dispose: () => terminal.dispose() };
}

export function TerminalSurface({ terminalId, sessionKey = null, createTerminalInstance }: TerminalSurfaceProps) {
  const { t } = useTranslation();
  const { client } = useClient();
  const { store, terminals } = useTerminalStore();
  const record = terminals.byId[terminalId] ?? null;
  // …输出回放（迁移 TerminalOutput：lines→write、trim→CLEAR_SCREEN 重绘、scrollToBottom）
  // …交互模式：record?.pty 为 true 时挂 onData/onResize/ResizeObserver(fit+sendResize，容器尺寸变化 120ms debounce 可省)
  // …行式模式：迁移 TerminalInput + ActiveTerminal 状态行（failed/exited/starting）
  // 返回 <div data-testid={`terminal-surface-${terminalId}`} className="flex min-h-0 flex-1 flex-col gap-1.5">…
}
```

（完整实现＝把 v1 `TerminalSection.tsx` 内 `TerminalOutput`、`ActiveTerminal`、`TerminalInput` 三个内聚块按上列 prop 形状移植进本文件并去 tab-strip/＋/close 容器逻辑（Task 9 的 dock 承担）。关键新行为：）

```tsx
  const interactive = record?.pty === true && record.status === "running";
  useEffect(() => {
    const emulator = emulatorRef.current;
    const container = containerRef.current;
    if (!interactive || !emulator || !container) return;
    const unsubData = emulator.onData?.((raw) => client.terminalWrite(terminalId, raw));
    const sendSize = () => {
      emulator.fit?.();
      const size = emulator.size;
      if (size) client.terminalResize(terminalId, size.cols, size.rows);
    };
    const unsubResize = emulator.onResize?.((size) =>
      client.terminalResize(terminalId, size.cols, size.rows),
    );
    const observer = new ResizeObserver(sendSize);
    observer.observe(container);
    sendSize();
    return () => {
      if (unsubData && "dispose" in unsubData) unsubData.dispose();
      if (unsubResize && "dispose" in unsubResize) unsubResize.dispose();
      observer.disconnect();
    };
  }, [interactive, terminalId, client]);
```

默认 emulator：`createTerminalInstance ?? (record?.pty ? createInteractiveTerminal : createLineTerminal)` —— emulator 在 `record.status !== "starting"` 后才创建（mode 已知）；starting 期间渲染「Starting terminal…」（v1 复用）。PTY 模式不写 echo；行式 `onLine = (line) => { client.terminalWrite(id, line + "\n"); store.appendEcho(id, line); }`。

- [ ] **Step 4: 删除 TerminalSection + 引用清理**

`git rm webui/src/components/workbench/TerminalSection.tsx webui/src/tests/terminal-section.test.tsx`；Task 9/10 之前 App/SessionWorkbench 仍引用它——本步先把两处引用改为临时内联占位（渲染 null），Task 9/10 各自正式接管；不允许编译不过：`npm run build` 必须绿，占位方式=两处调用点暂改为 `<TerminalSurface terminalId={…} sessionKey={…}/>` 不渲染容器版（即先隐藏入口），只保 build 绿 + 类型绿。

- [ ] **Step 5: 绿 + 提交**

`cd webui && npm run test -- terminal-surface && npm run build`。

```bash
git add -A webui/src
git commit -m "feat(webui): TerminalSurface——xterm PTY 直通交互 + resize + 行式降级保留"
```

---

### Task 8: fetchTerminalShells（前端 API 层）

**Files:**
- Modify: `webui/src/lib/api.ts`

**Interfaces:**
- Consumes: Task 2 端点。Produces: `fetchTerminalShells(token: string): Promise<TerminalShellsPayload>`，`TerminalShellsPayload { shells: {id,label,path}[]; default: string|null }`（Task 9 下拉用）。

- [ ] **Step 1: 实现（跟随文件内 fetchWorkspaceFiles 的 request helper 与 ApiError 语义，同 Authorization Bearer 模式，:255 同风格）**

```ts
export interface TerminalShellEntry {
  id: string;
  label: string;
  path: string;
}

export interface TerminalShellsPayload {
  shells: TerminalShellEntry[];
  default: string | null;
}

export async function fetchTerminalShells(token: string): Promise<TerminalShellsPayload> {
  // 走与本文件其它 GET 相同的授权头组装（api token Bearer）。
  return requestJson<TerminalShellsPayload>("/webui/terminal/shells", token);
}
```

（`requestJson` 名称以 api.ts 既有内部 helper 实际名替换——先读 :90-130，不新造第二套 fetch 包装。）

- [ ] **Step 2: `npm run build` 绿 + 提交**

```bash
git add webui/src/lib/api.ts
git commit -m "feat(webui): fetchTerminalShells API 客户端"
```

---

### Task 9: StartDock 组件 + 首页宿主替换（含接管条、shell 下拉、启动器空态）

**Files:**
- Create: `webui/src/components/dock/StartDock.tsx`
- Create: `webui/src/components/dock/DockFileTab.tsx`
- Modify: `webui/src/components/workbench/BrowserSection.tsx`（`fullHeight?: boolean` prop，根容器 `h-full min-h-0`、iframe 容器 `flex-1 min-h-0`、iframe `h-full w-full`；:82 根 div 与 :163/:181 区域）
- Modify: `webui/src/App.tsx`（:2896-2911 HomeStartPanel 块 → StartDock；删 startPanelFocus/state 相关、LazyTerminalSection 改 Lazy StartDock 内引）
- Delete: `webui/src/components/home/HomeStartPanel.tsx`、`webui/src/tests/home-start-panel.test.tsx`
- Test: Create `webui/src/tests/start-dock.test.tsx`

**Interfaces:**
- Consumes: dockStore（T6）、TerminalSurface（T7）、fetchTerminalShells（T8）、WorkspaceFileTree、`client.terminalStart/Write/Kill/List`、useTerminalStore。
- Produces: `StartDock(props: { scope: string; sessionKey: string | null; getToken: () => string; onOpenFilePreview?: (path: string) => void })`；data-testid 清单（验收依赖）：`start-dock`、`dock-tab-<tabId>`、`dock-close-<tabId>`、`dock-plus-button`、`dock-plus-files`、`dock-plus-browser`、`dock-plus-terminal`、`dock-shell-<id>`、`dock-launcher`、`dock-card-files|dock-card-terminal|dock-card-browser`、`dock-shell-select`、`dock-takeover-<terminalId>`、`terminal-surface-<id>`（T7）。

- [ ] **Step 1: 写失败测试（关键场景）**

```tsx
// start-dock.test.tsx：FakeClient 记录帧；预置 dockStore/dock scope=HOME_DOCK_SCOPE
it("empty dock shows the three launcher cards", () => { /* 渲染后 dock-launcher 存在、三卡片 testid */ });
it("clicking a launcher card opens its tab and drops the launcher", () => {
  fire.click("dock-card-browser");
  expect(screen by testid "dock-tab-browser"); expect("dock-launcher") absent;
});
it("terminal card + shell select sends terminal_start with the chosen shell and titled tab", () => {
  // fetchTerminalShells mock [{id:"zsh"},{id:"bash"}] default zsh
  fireEvent.change by testid dock-shell-select value "bash"
  fire.click("dock-card-terminal")
  expect client.frames terminal_start shell=="bash"; tab title ">. bash"
});
it("plus menu: terminal submenu opens shells; files/browser are focus-or-open", () => { /* … */ });
it("closing a terminal tab sends terminal_kill and removes the tab", () => { /* dock-close-term-1 → kill 帧 */ });
it("takeover banner from persisted terminals re-opens by original id", () => {
  sessionStorage.setItem("nanobot.dock.terminals.websocket:home", JSON.stringify([{id:"term-old",shell:"/bin/zsh"}]));
  render; expect("dock-takeover-term-old"); click → terminal_start {terminal_id:"term-old"}（无 shell）且 banner 消失
});
```

Run: `npm run test -- start-dock` → FAIL。

- [ ] **Step 2: 实现 DockFileTab.tsx**

把 HomeStartPanel.tsx 的 `WorkspaceFilePreview`（:104-186，`max-h-64` 改 `flex-1 min-h-0 overflow-auto` 容器化）与 files 状态机（:214-299 probe/noWorkspace/error/retry + fetchDirectory root 缓存）**移植**为标签组件；`sessionKey` 非空时树用 session 锚点（`<WorkspaceFileTree sessionKey={sessionKey} getToken=… onOpenFilePreview=…/>`，预览走 `fetchFilePreview(token, sessionKey, path)` scope 参数空串），空时走 home 锚点（`fetchWorkspaceFiles(token, path)` + `HOME_PREVIEW_SESSION_KEY="websocket:home"` + scope `"default"`，与现实现逐一对应）。根容器 `className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto px-3 py-2 scrollbar-thin"`。props：`{ scope, sessionKey, getToken, onOpenFilePreview? }`（onOpenFilePreview 传入则预览上抛宿主，SessionWorkbench 保留外置预览面板行为）。

- [ ] **Step 3: 实现 StartDock.tsx**

结构（完整实现按此骨架，样式沿用 v1 aside/tokens）：

```tsx
export function StartDock({ scope, sessionKey, getToken, onOpenFilePreview }: StartDockProps) {
  const { t } = useTranslation();
  const { client } = useClient();
  const snapshot = useDockTabs(scope);
  const { terminals } = useTerminalStore();
  const [collapsed, setCollapsed] = useState(false);
  const [plusOpen, setPlusOpen] = useState(false);
  const [shells, setShells] = useState<TerminalShellsPayload | null>(null);
  const [shellChoice, setShellChoice] = useState<string | null>(null);
  const takeovers = dockStore.persistedTerminals(scope); // mount 时快照即可，store 变更会重渲

  useEffect(() => {
    fetchTerminalShells(getToken()).then(setShells).catch(() => setShells(null));
  }, [getToken]);

  useEffect(() => { client.terminalList(); }, [client]);

  // 标题跟随：活动终端 started 回报 shell 后刷新 tab 标题（spec §3.1）
  const activeTerminal = snapshot.tabs.find((tab) => tab.id === snapshot.activeId && tab.kind === "terminal");
  const activeRecord = activeTerminal ? terminals.byId[activeTerminal.id] ?? null : null;
  useEffect(() => {
    if (activeTerminal && activeRecord?.shell) {
      dockStore.setTerminalTitle(scope, activeTerminal.id, activeRecord.shell);
    }
  }, [scope, activeTerminal?.id, activeRecord?.shell]);

  const openTerminalTab = (shell: string | null) => {
    const id = `term-${crypto.randomUUID()}`;
    if (!dockStore.openTerminal(scope, id, shell)) return;
    useTerminalStoreInstance.beginCreate(id, sessionKey);   // T7 store
    client.terminalStart(id, sessionKey, shell ?? null);
  };

  const takeOver = (entry: PersistedTerminal) => {
    dockStore.openTerminal(scope, entry.id, entry.shell);
    useTerminalStoreInstance.beginCreate(entry.id, sessionKey);
    dockStore.forgetPersisted(scope, entry.id);
    client.terminalStart(entry.id, sessionKey, null);       // 同 id = 接管重绑（契约 v2）
  };

  const closeTab = (tab: DockTab) => {
    if (tab.kind === "terminal") { client.terminalKill(tab.id); useTerminalStoreInstance.remove(tab.id); }
    dockStore.closeTab(scope, tab.id);
  };

  // collapsed：沿用 v1 aside 折叠 rail（PanelRightOpen 按钮，data-testid="start-dock"）
  // 展开：aside w-[22rem]→ 本批放宽：className 含 "w-[26rem] xl:w-[30rem]"；header 行=标签
  // header: snapshot.tabs.map → <button role="tab" data-testid={`dock-tab-${tab.id}`}> {tab.title}
  //         + <button data-testid={`dock-close-${tab.id}`}><X/></button>
  //         + <button data-testid="dock-plus-button" onClick={()=>setPlusOpen(v=>!v)}><Plus/></button>
  // plus 菜单: dock-plus-files / dock-plus-browser / dock-plus-terminal(展开 shell 子层: shells?.shells.map→
  //            dock-shell-<id>，点击 openTerminalTab(id)；无 shells 数据时 openTerminalTab(null) 走平台默认)
  // 接管条: takeovers.map(entry => <button data-testid={`dock-takeover-${entry.id}`}>
  //         {t("dock.takeoverRunning")}: {dockTerminalTitle(entry.shell)} — {t("dock.takeoverAction")}</button>)
  //         旁注 t("dock.takeoverHint")
  // body（tabs.length===0 → dock-launcher 三卡片：CARD_SPECS 从 HomeStartPanel 原文搬入本文件；
  //        终端卡片内嵌 <select data-testid="dock-shell-select">（shells 数据，default 打勾序前）；
  //        卡片点击: files→openFile、browser→openBrowser、terminal→openTerminalTab(shellChoice ?? shells?.default ?? null)）
  // 否则渲染活动标签内容: file→<DockFileTab …/>; browser→<BrowserSection fullHeight/>;
  //        terminal→<Suspense><TerminalSurface terminalId={activeId} sessionKey={sessionKey}/></Suspense>
}
```

（i18n 用 `t("dock.*", { defaultValue: … })`，键 Task 12 统一落 10 语言；组件文案文件内零硬编码中文。）

- [ ] **Step 4: App.tsx 首页替换**

删 `HomeStartPanel` import 与挂载块（:2896-2911），改挂：

```tsx
{view === "chat" && activeKey === null && !mobileWorkbench ? (
  <Suspense fallback={null}>
    <StartDock
      scope={HOME_DOCK_SCOPE}
      sessionKey={null}
      getToken={getToken}
    />
  </Suspense>
) : null}
```

同时删除 `startPanelFocus/openStartPanelSection` state（Task 11 重写快捷键；此刻先移除避免引用已删导出，App 编译绿）。`git rm webui/src/components/home/HomeStartPanel.tsx webui/src/tests/home-start-panel.test.tsx`。

- [ ] **Step 5: 绿 + 提交**

`npm run test -- start-dock && npm run build`；`git add -A webui/src`；
`git commit -m "feat(webui): StartDock 标签式右栏——启动器空态/+菜单/shell 下拉/接管条，首页换宿主"`

---

### Task 10: SessionWorkbench 接入同一 StartDock（D1/B1 收口）

**Files:**
- Modify: `webui/src/components/workbench/SessionWorkbench.tsx`（:194-230 三个 Section 删除；挂载点改嵌入版 StartDock）
- Modify: `webui/src/components/dock/StartDock.tsx`（新增 `variant?: "rail" | "embedded"`：embedded 不渲染自身 aside/折叠按钮，直接 header+body `h-full`）
- Test: `webui/src/tests/session-workbench.test.tsx`（若已有则追加；否则新建）

**Interfaces:** Consumes Task 9 StartDock；`sessionKey`（既有 prop）即 dock scope（`websocket:<chat_id>` 形态，与 v1 TerminalSection 传参一致）。

- [ ] **Step 1: 失败测试**

```tsx
it("session dock replaces the three inline sections with one tabbed dock", () => {
  // render SessionWorkbench with sessionKey="websocket:chat-7"
  // 断言: [data-testid=start-dock] 存在；原 workbench.fileTree/terminal/browser Section 标题不存在
  // 点 dock-card-terminal → client 帧 terminal_start 携带 session_key=="websocket:chat-7"
});
```

- [ ] **Step 2: 实现**：SessionWorkbench 中删 `WorkspaceFileTree`/`LazyTerminalSection`/`BrowserSection` 三个 Section 与相关 import、`onOpenFilePreview` 直传给 StartDock（保留外置预览回调语义）；原位置渲染：

```tsx
<StartDock
  variant="embedded"
  scope={sessionKey ?? HOME_DOCK_SCOPE}
  sessionKey={sessionKey ?? null}
  getToken={getToken ?? (() => "")}
  onOpenFilePreview={onOpenFilePreview}
/>
```

（SessionWorkbench 若无 getToken prop 直传，沿用其内部既有 getToken 来源；embedded 下 body `min-h-0 flex-1`。B1 验收点：BrowserSection `fullHeight` 生效、iframe 不再 340px。）

- [ ] **Step 3: 绿 + 提交**

`npm run test -- session-workbench start-dock && npm run build`；
`git commit -m "feat(webui): SessionWorkbench 并入同一 StartDock（内嵌变体），删三个内联分区"`

---

### Task 11: ⌘P / ⌘T 全场景直达（B2）

**Files:**
- Modify: `webui/src/App.tsx`（:1933-1944 shortcut 段重写；:1004 残留 state 清理）
- Create: `webui/src/lib/dock-shortcuts.ts`
- Test: `webui/src/tests/dock-shortcuts.test.ts`

**Interfaces:** Produces: `resolveDockShortcut(event): "file" | "browser" | null`（meta||ctrl，非 alt/shift；p→file，t→browser；无 isHome 限定）。

- [ ] **Step 1: 失败测试**

```ts
it("opens/focuses the dock tab for the active scope from anywhere", () => {
  expect(resolveDockShortcut(key("p", { meta: true }))).toBe("file");
  expect(resolveDockShortcut(key("t", { meta: true }))).toBe("browser");
  expect(resolveDockShortcut(key("t", { meta: true, shift: true }))).toBeNull();
});
```

- [ ] **Step 2: App 接线**：handler 段替换为

```tsx
const dockSection = resolveDockShortcut(event);
if (dockSection !== null) {
  event.preventDefault();
  const scope = activeKey ?? HOME_DOCK_SCOPE;
  if (dockSection === "file") dockStore.openFile(scope);
  else dockStore.openBrowser(scope);
  return;
}
```

（App 已 import HOME_DOCK_SCOPE/dockStore；xterm 聚焦时 ⌘T 走本 handler——浏览器新标签语义让位于 dock，符合 spec B2。若 `activeKey` 不是 `websocket:<id>` 形态，取其 session key 字段——以 App 内既有 `webui_session_key` 前端对应物为准，实现时读 App 确认。）

- [ ] **Step 3: 绿 + 提交**：`npm run test -- dock-shortcuts && npm run build`；
`git commit -m "feat(webui): ⌘P/⌘T 全场景直达 dock 标签（B2）"`

---

### Task 12: i18n 全量落键 + 前端回归收口

**Files:**
- Modify: `webui/src/i18n/locales/{en,zh-CN,zh-TW,ja,ko,vi,fr,es,pt-BR,id}/common.json`

**Interfaces:** 汇总 Task 7-11 用到的全部 `t()` 键（以代码中 defaultValue 为英文源，`grep -rn "t(\"dock\.\|dock\.\|shellUnavailable" webui/src/components webui/src/lib` 生成清单核对）。

- [ ] **Step 1: en/zh-CN 基准键表**（`dock` 命名空间并入现有结构）

| 键 | en | zh-CN |
| --- | --- | --- |
| dock.tabFile | Files | 文件 |
| dock.tabBrowser | Browser | 浏览器 |
| dock.plusAria | New tab | 新建标签 |
| dock.newTerminal | New terminal | 新建终端 |
| dock.shellChoices | Choose shell | 选择 shell |
| dock.takeoverRunning | Running terminal | 已运行终端 |
| dock.takeoverAction | Take over | 点击接管 |
| dock.takeoverHint | Press Enter to redraw | 回车重绘画面 |
| dock.closeTab | Close tab | 关闭标签 |
| startPanel.shellUnavailable | This shell is not installed on the host. | 本机未安装该 shell。 |

（其余组件里出现过但表中没有的 `dock.*`/`startPanel.*` 新键：补进此表再落文件，规则=有 defaultValue 必有键。）

- [ ] **Step 2: 其余 8 语言同 schema 翻译落文件**（zh-TW/日/韩/越/法/西/葡(巴西)/印尼；术语对齐既有 locale 里 terminal/browser 的译法——先 grep 该文件既有键再译）。

- [ ] **Step 3: 全量回归 + 提交**

```bash
cd webui && npm run test && npm run build
uv run --no-sync pytest tests/test_webui_terminal.py tests/test_webui_pty_terminal.py tests/test_webui_terminal_shells.py tests/test_webui_workspace_files.py -v
uv run --no-sync ruff check nanobot/webui/
```

`git add webui/src/i18n && git commit -m "i18n(webui): dock 命名空间 10 语言全量键"`

---

### Task 13: 实机验收 + 产物收口（F）

**Files:**
- Create: `.ai-runtime-artifacts/verifications/2026-10-11-home-dock-pty-v1.1-browser-acceptance.md`（+ assets/）
- Modify: `.ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-11-home-dock-pty-v1.1.md`（CLOSEOUT 段）

- [ ] **Step 1: 构建并重启**：`cd webui && npm run build`；重启 gateway（memory「Project startup」三步；沿用现 pid 52927 实例则 kill 后 `nanobot gateway --background`）；headless Chrome（localhost:9333，1440×900，profile /tmp/zhixu-cdp-profile，`--disable-gpu`）已在/重建，CDP 用 `http://localhost:9333`。
- [ ] **Step 2: CDP 验收清单**（/tmp/zhixu-cdp/flow.mjs 模式，帧断言走 `Network.webSocketFrameSent/Received`）：
  1. 首页空态三卡片 → 点文件卡片：dock 出 `>. 文件` 标签、树全高；
  2. 点终端卡片（shell 下拉选 zsh）→ `terminal_started {pty:true, shell:*/zsh}`、xterm 有提示符、`CDP Input.dispatchKeyEvent` 直输 `ls`+回车（PTY 直通：用 `Input.insertText`+`key rawKeyDown code=Enter key=Enter`，或 `terminal_write` 帧注入验证 xterm→frame 链路）；
  3. `+` 菜单再开一个 bash 终端 → 两终端标签并存、标题正确；
  4. resize：改窗口尺寸 → 出站 `terminal_resize` 帧、`stty size` 校验；
  5. Ctrl-C（写 `\x03`）中断 `sleep 30`；
  6. 浏览器标签全高（iframe 高度 ≥600px，B1）；
  7. × 关闭终端标签 → `terminal_kill`；退出 shell 后再发 kill → 无错误帧（T4）；
  8. ⌘P/⌘T 在首页与会话页各触发一次（B2）；
  9. 刷新页面 → 接管条出现 → 点击接管 → `already_running {pty:true}` → 回车重绘、`ls` 输出到达新连接（T3）；
  10. 会话页 dock：三旧 Section 不再存在、文件预览/标签并存（D1）；
  11. restricted 会话开终端：`pty:false` 行式 UI（输入框存在）——守卫路径未回退；
  12. v1 回归：feedback /「+」菜单指令组 / 文件预览抽测各 1 条。
- [ ] **Step 3: 截图与帧证据落 verifications/ + assets/；集体测试（三轨道全量命令一次跑完：pytest 目标集 + webui 全量 + ruff，基线豁免注明）记入同文件。**
- [ ] **Step 4: 安全/代码复审要点写入 TRACK**：R1（restricted=管道守卫路径为保守实现，PTY 仅 full-access——请求复审确认）；接管重绑的同 gate 信任面；resize 上限。审查通过后再做 worktree 合并回 feature/desktop-app（合并/推送需用户确认，默认只合并本地、不 push）。

---

## Self-Review（写完后自查结论）

- **Spec 覆盖**：G1→Task 6/9/10；G2→Task 1/2/3/4/5/7/8；G3：T1(T1+T7)、T2(T2+T9)、T3(T9 接管条+T1/T5 重绑)、T4(T3 宽限+T5 handler)、B1(T9 BrowserSection fullHeight)、B2(T11)、D1(T10)；G4→T1/T3/T4 restricted 全程守卫路径 + Task 13 复审要点。§6 测试标准：各任务 Step 1 测试先行 + Task 13 清单 12 项 ≥ spec 要求的 10 项。
- **占位符**：Task 5 router 测试的 fixture 名、Task 7/8/10 的「跟随既有 helper 实际名」处均给出了读取源文件定位（行号/文件），属可执行的查证指令而非 TBD。
- **类型一致性**：`exited_owner/has_live/resize(shell=…, cols/rows=…)` 三个方法名在 Task 1/3/4/5 全链一致；`available_shells()` 返回形状 Task 2 端点=Task 8 客户端=Task 9 下拉一致；dock testid 与 Task 13 验收选择器一致（`dock-*`/`terminal-surface-*`）。
