"""T06：Worker 子进程与进程树 owner（spec §4、§9）。

评估用例跑在独立进程里，父进程必须能在超时、取消和协议失败时把**整棵进程树**收干净：

- Windows 复用 SDK 已有的 Job Object（``nanobot.agent.tools._windows_job``）：挂起创建 → 入 Job → 恢复，
  root 还没来得及派生子进程就已经被 owner 罩住；失败路径 ``terminate()`` 杀整棵树。
- 其他平台用 ``start_new_session=True`` 让子进程自成进程组，取消时 ``killpg``。
- 禁止按进程名批量杀：清理前用 :func:`verify_launch_identity` 核对 PID + 启动时刻 + 本次服务签发的
  nonce（spec §9「验证 PID、启动标识和所属运行」）。

管道协议与帧校验见 :mod:`nanobot.evaluation.ipc`；本模块只做启动、读帧循环与回收。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from nanobot.evaluation.ipc import MAX_FRAME_BYTES, IpcError, ParentIpc, encode_boot

_IS_WINDOWS = sys.platform == "win32"

WORKER_CANCELLED = "WORKER_CANCELLED"
WORKER_TIMED_OUT = "WORKER_TIMED_OUT"
WORKER_ATTEMPT_MISMATCH = "WORKER_ATTEMPT_MISMATCH"
WORKER_IPC_FAILED = "WORKER_IPC_FAILED"
WORKER_BOOT_FAILED = "WORKER_BOOT_FAILED"
PROCESS_IDENTITY_UNVERIFIED = "PROCESS_IDENTITY_UNVERIFIED"

#: 拿到 result 或关闭管道后等待进程退出的宽限。
EXIT_GRACE_S = 10.0
#: ``pump_until_idle`` 判定「暂无更多输出」的空闲窗口。
IDLE_WINDOW_S = 1.0
#: 启动时刻比对的容差，用于识破 PID 复用。
START_SLACK_MS = 1000

# 本次服务生命周期内签发的清理身份；进程回收后由 forget_launch_identity 清掉。
_LAUNCH_NONCES: dict[tuple[int, str], int] = {}


class WorkerLaunchError(RuntimeError):
    """Worker 没能启动或状态不允许该操作；``code`` 原样进入 ``errors[].code``。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# --------------------------------------------------------------------------- 启动身份


def _win_process_start_time(pid: int) -> int | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        creation = ctypes.c_ulonglong()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(ctypes.c_ulonglong()),
            ctypes.byref(ctypes.c_ulonglong()),
            ctypes.byref(ctypes.c_ulonglong()),
        )
        if not ok:
            return None
        # FILETIME：1601-01-01 起的 100ns，换算成 epoch 毫秒。
        return creation.value // 10_000 - 116_444_736_000
    finally:
        kernel32.CloseHandle(handle)


def _posix_process_start_time(pid: int) -> int | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        # comm 字段可能含空格与括号：从最后一个 ')' 之后再切。
        start_ticks = int(raw[raw.rfind(")") + 2 :].split()[19])
        boot: int | None = None
        with open("/proc/stat", "rb") as handle:
            for line in handle:
                if line.startswith(b"btime "):
                    boot = int(line.split()[1])
                    break
        if boot is None:
            return None
        return boot * 1000 + round(start_ticks * 1000 / os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError):
        return None


def process_start_time(pid: int) -> int | None:
    """进程启动时刻（epoch 毫秒）；平台查不到时返回 ``None`` 表示身份不可验证。"""
    if pid <= 0:
        return None
    return _win_process_start_time(pid) if _IS_WINDOWS else _posix_process_start_time(pid)


def make_launch_identity(pid: int) -> dict[str, Any]:
    """给本服务即将拥有的进程签发清理身份。"""
    started_at = process_start_time(pid)
    if started_at is None:
        started_at = int(time.time() * 1000)
    nonce = uuid.uuid4().hex
    _LAUNCH_NONCES[(pid, nonce)] = started_at
    return {"pid": pid, "nonce": nonce, "started_at": started_at}


def forget_launch_identity(identity: Mapping[str, Any] | None) -> None:
    if not identity:
        return
    pid, nonce = identity.get("pid"), identity.get("nonce")
    if isinstance(pid, int) and isinstance(nonce, str):
        _LAUNCH_NONCES.pop((pid, nonce), None)


def verify_launch_identity(identity: Mapping[str, Any], *, strict: bool = False) -> bool:
    """核对清理请求是否指向当初签发的那个进程。

    ``strict=True`` 用于「准备动手但身份查不了」的场合：此时必须拒绝自动清理、交人工处置，
    绝不允许退化成按进程名或命令行批量杀。
    """
    pid = identity.get("pid")
    nonce = identity.get("nonce")
    started_at = identity.get("started_at")
    if not isinstance(pid, int) or not isinstance(nonce, str) or not nonce:
        return _reject_identity("identity carries no verifiable pid/nonce", strict=strict)
    if not isinstance(started_at, int):
        return _reject_identity("identity carries no started_at", strict=strict)

    recorded = _LAUNCH_NONCES.get((pid, nonce))
    if recorded is None or abs(recorded - started_at) > START_SLACK_MS:
        return False
    current = process_start_time(pid)
    if current is None:
        # 平台查不到启动时间时，只认本次服务亲自签发、且从未写入任何持久化通道的身份。
        return True
    return abs(current - started_at) <= START_SLACK_MS


def _reject_identity(message: str, *, strict: bool) -> bool:
    if strict:
        raise ValueError(f"{PROCESS_IDENTITY_UNVERIFIED}: {message}")
    return False


# --------------------------------------------------------------------------- 子进程


class WorkerProcess:
    """一个 case_attempt 的 Worker 子进程：启动、读管道、超时/取消时回收整棵树。"""

    def __init__(
        self,
        *,
        attempt_id: str,
        command: Sequence[str],
        log_path: str | Path | None = None,
    ) -> None:
        if not attempt_id.strip():
            raise ValueError(f"{WORKER_ATTEMPT_MISMATCH}: attempt_id is required")
        if len(command) < 2:
            raise ValueError("WORKER_COMMAND_INVALID: command needs an executable and args")
        self._attempt_id = attempt_id
        self._command = [str(item) for item in command]
        self._log_path = Path(log_path) if log_path is not None else None
        self._ipc = ParentIpc(attempt_id=attempt_id)
        self._process: asyncio.subprocess.Process | None = None
        self._job: Any | None = None
        self._stderr_handle: Any | None = None
        self._identity: dict[str, Any] | None = None
        self._exit_code: int | None = None
        self._started_at: float | None = None
        self._result_at: float | None = None
        self._fatal: tuple[str, str] | None = None
        self._descendants: tuple[int, ...] = ()
        self._cancel_errors: list[tuple[str, str]] | None = None

    @property
    def attempt_id(self) -> str:
        return self._attempt_id

    @property
    def ipc(self) -> ParentIpc:
        return self._ipc

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self._process

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    @property
    def launch_identity(self) -> dict[str, Any] | None:
        """交给调度器做孤儿清理核对；nonce 不落数据库、不进命令行。"""
        return self._identity

    @property
    def descendant_pids(self) -> tuple[int, ...]:
        """POSIX 下取消前抓到的进程组成员；Windows 由 Job Object 覆盖整棵树，不逐个枚举。"""
        return self._descendants

    @property
    def fatal_reason(self) -> tuple[str, str] | None:
        return self._fatal

    def timing(self) -> dict[str, int | None]:
        """agent_ms 用父进程侧实测值；其余口径沿用 Worker 上报。"""
        reported: Mapping[str, Any] = {}
        if self._ipc.result is not None:
            reported = self._ipc.result.get("timing") or {}
        agent_ms = 0
        if self._started_at is not None:
            end = self._result_at if self._result_at is not None else time.monotonic()
            agent_ms = max(0, int((end - self._started_at) * 1000))
        return {
            "queue_ms": reported.get("queue_ms"),
            "agent_ms": agent_ms,
            "grading_ms": reported.get("grading_ms"),
            "total_ms": reported.get("total_ms"),
        }

    async def start(self, boot: Mapping[str, Any]) -> None:
        if self._process is not None:
            raise WorkerLaunchError("WORKER_ALREADY_STARTED", self._attempt_id)
        frame = encode_boot(boot)
        spawn_kwargs: dict[str, Any] = {}
        job: Any | None = None
        if _IS_WINDOWS:
            from nanobot.agent.tools._windows_job import WindowsJob

            job = WindowsJob.create()
            spawn_kwargs["creationflags"] = job.creation_flags
        else:
            spawn_kwargs["start_new_session"] = True

        stderr: Any = subprocess.DEVNULL
        if self._log_path is not None:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            self._stderr_handle = open(self._log_path, "ab")  # noqa: SIM115
            stderr = self._stderr_handle

        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr,
                limit=MAX_FRAME_BYTES + 1,
                **spawn_kwargs,
            )
        except OSError as exc:
            if job is not None:
                job.terminate()
            self._close_stderr()
            raise WorkerLaunchError("WORKER_SPAWN_FAILED", str(exc)) from exc
        assert process is not None

        # 挂起状态下先确立树归属再放行：root 派生的任何进程一出生就在 Job 里。
        if job is not None:
            try:
                job.assign_and_resume(process.pid)
            except Exception as exc:
                process.kill()
                await _silent_wait(process)
                self._exit_code = process.returncode
                job.terminate()
                self._close_stderr()
                raise WorkerLaunchError("WORKER_JOB_ASSIGN_FAILED", str(exc)) from exc
            self._job = job

        self._process = process
        self._started_at = time.monotonic()
        self._identity = make_launch_identity(process.pid)
        await self._write_boot(frame)

    async def _write_boot(self, frame: bytes) -> None:
        process = self._process
        assert process is not None and process.stdin is not None
        try:
            process.stdin.write(frame)
            await process.stdin.drain()
        except (OSError, asyncio.TimeoutError) as exc:
            self._note_fatal(WORKER_BOOT_FAILED, f"boot frame was not accepted: {exc}")
            return
        finally:
            process.stdin.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.stdin.wait_closed(), timeout=EXIT_GRACE_S)

    async def collect(self, *, timeout_s: float) -> str:
        """读到 result 帧返回 ``"result"``，stdout 关闭返回 ``"eof"``；超时抛 TimeoutError。"""
        try:
            return await asyncio.wait_for(self._read_until_result(), timeout=timeout_s)
        except asyncio.TimeoutError:
            self._note_fatal(WORKER_TIMED_OUT, f"worker exceeded {timeout_s}s")
            raise

    async def run_with_timeout(self, *, timeout_s: float) -> list[tuple[str, str]]:
        """一次完整执行：超时与协议失败都强制回收，返回错误清单而不抛出。"""
        try:
            await self.collect(timeout_s=timeout_s)
        except (asyncio.TimeoutError, ValueError):
            return await self.cancel()
        try:
            await self.wait(timeout_s=EXIT_GRACE_S)
        except asyncio.TimeoutError:
            self._note_fatal(WORKER_TIMED_OUT, "worker did not exit after result")
            return await self.cancel()
        return self._error_pairs()

    async def pump_until_idle(self, *, timeout_s: float) -> None:
        """把当前可得的输出读干净：一个空闲窗口无新行或 EOF 就返回，不等 result。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            try:
                line = await asyncio.wait_for(
                    self._readline(), timeout=min(remaining, IDLE_WINDOW_S)
                )
            except asyncio.TimeoutError:
                return
            if not line:
                await self._reap()
                return
            self._dispatch(line)
            if self._ipc.result is not None:
                return

    async def wait(self, *, timeout_s: float) -> int:
        code = await self._reap(timeout=timeout_s)
        if code is None:
            raise asyncio.TimeoutError(f"worker {self._attempt_id} did not exit")
        return code

    async def cancel(self) -> list[tuple[str, str]]:
        """终止整棵进程树；重复调用返回同一份错误清单。"""
        if self._cancel_errors is not None:
            return list(self._cancel_errors)
        process = self._process
        if process is not None:
            self._descendants = _posix_process_group(process.pid)
            self._terminate_tree(process)
            await _silent_wait(process)
            self._exit_code = process.returncode if process.returncode is not None else -1
        self._close_job(terminate=self._ipc.result is None)
        await self._close_pipes()
        forget_launch_identity(self._identity)
        if self._fatal is None:
            self._note_fatal(WORKER_CANCELLED, "worker was cancelled by the scheduler")
        errors = self._error_pairs()
        self._cancel_errors = errors
        return list(errors)

    # ------------------------------------------------------------------ 内部

    def _note_fatal(self, code: str, message: str) -> None:
        if self._fatal is None:
            self._fatal = (code, message)

    def _error_pairs(self) -> list[tuple[str, str]]:
        pairs = [(error.code, error.message) for error in self._ipc.result_errors]
        if self._fatal is not None:
            pairs.append(self._fatal)
        return pairs

    async def _read_until_result(self) -> str:
        while True:
            line = await self._readline()
            if not line:
                await self._reap()
                return "eof"
            self._dispatch(line)
            if self._ipc.result is not None:
                return "result"

    async def _readline(self) -> bytes:
        process = self._process
        if process is None or process.stdout is None:
            raise WorkerLaunchError("WORKER_NOT_STARTED", self._attempt_id)
        try:
            return await process.stdout.readline()
        except (asyncio.LimitOverrunError, ValueError) as exc:
            # 行缓冲上限 = MAX_FRAME_BYTES + 1：超限就是超长帧，不能当成一条正常事件吞下。
            self._note_fatal(WORKER_IPC_FAILED, f"IPC_FRAME_TOO_LARGE: {exc}")
            raise IpcError("IPC_FRAME_TOO_LARGE", str(exc)) from exc

    def _dispatch(self, line: bytes) -> None:
        try:
            frame = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._note_fatal(WORKER_IPC_FAILED, f"IPC_INVALID_JSON: {exc}")
            raise IpcError("IPC_INVALID_JSON", str(exc)) from exc
        if not isinstance(frame, dict):
            self._note_fatal(WORKER_IPC_FAILED, "IPC_INVALID_JSON: not an object")
            raise IpcError("IPC_INVALID_JSON", "frame is not an object")
        try:
            outcome = self._ipc.accept(frame)
        except IpcError as exc:
            self._note_fatal(_worker_code_for(exc.code), str(exc))
            if self._process is not None:
                self._terminate_tree(self._process)
            raise
        if outcome.frame_type == "result":
            self._result_at = time.monotonic()

    async def _reap(self, *, timeout: float = EXIT_GRACE_S) -> int | None:
        process = self._process
        if process is None:
            return None
        if self._exit_code is None:
            try:
                self._exit_code = await asyncio.wait_for(process.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                self._note_fatal(WORKER_TIMED_OUT, "worker did not exit on time")
                return None
        await self._close_pipes()
        self._close_job(terminate=not self._ipc.can_seal_succeeded(exit_code=self._exit_code))
        forget_launch_identity(self._identity)
        return self._exit_code

    def _terminate_tree(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        job = self._job
        if job is not None:
            job.terminate()
            return
        if not _IS_WINDOWS:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                return
            except (ProcessLookupError, PermissionError, OSError):
                pass
        with contextlib.suppress(ProcessLookupError):
            process.kill()

    def _close_job(self, *, terminate: bool) -> None:
        job = self._job
        self._job = None
        if job is None:
            return
        if terminate:
            job.terminate()
        else:
            job.release()

    async def _close_pipes(self) -> None:
        process = self._process
        if process is not None and process.stdin is not None:
            with contextlib.suppress(Exception):
                process.stdin.close()
        self._close_stderr()

    def _close_stderr(self) -> None:
        handle = self._stderr_handle
        self._stderr_handle = None
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()


async def _silent_wait(process: asyncio.subprocess.Process, *, timeout: float = EXIT_GRACE_S) -> None:
    with contextlib.suppress(Exception):
        await asyncio.wait_for(process.wait(), timeout=timeout)


def _posix_process_group(pid: int) -> tuple[int, ...]:
    if _IS_WINDOWS:
        return ()
    members: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return ()
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            raw = Path(f"/proc/{entry}/stat").read_text(encoding="utf-8", errors="replace")
            if int(raw[raw.rfind(")") + 2 :].split()[2]) == pid:  # ppid
                members.append(int(entry))
        except (OSError, ValueError, IndexError):
            continue
    return tuple(members)


def _worker_code_for(ipc_code: str) -> str:
    if ipc_code == "IPC_ATTEMPT_MISMATCH":
        return WORKER_ATTEMPT_MISMATCH
    return WORKER_IPC_FAILED
