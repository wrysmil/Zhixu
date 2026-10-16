"""T06：Worker 子进程与进程树 owner。

这里必须真实起子进程：Windows 走项目已有的 Job Object（suspended spawn / assign_and_resume /
terminate），其他平台走进程组。孙进程回收要真跑，不许 skip 后声称已验证。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from nanobot.evaluation.ipc import MAX_FRAME_BYTES
from nanobot.evaluation.processes import (
    WorkerProcess,
    make_launch_identity,
    verify_launch_identity,
)

IS_WINDOWS = sys.platform == "win32"

# 子进程桩：先落 ready，再按模式继续。GRANDCHILD=1 时派一个孙进程并把它的 pid 写进文件。
STUB = textwrap.dedent(
    """
    import json, os, subprocess, sys, time
    attempt_id = sys.argv[1]
    mode = __MODE__
    pid_file = __PID_FILE__

    def emit(payload):
        sys.stdout.write(json.dumps(payload) + "\\n")
        sys.stdout.flush()

    emit({"type": "ready", "protocol_version": 1, "attempt_id": attempt_id})

    if pid_file:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time\\nwhile True: time.sleep(0.2)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        with open(pid_file, "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))

    usage = {"input_tokens": None, "output_tokens": None, "total_tokens": None,
             "source": "unavailable", "coverage": "unavailable",
             "physical_requests": 0, "iterations": 0, "missing_usage_calls": 0}
    result = {"type": "result", "attempt_id": attempt_id,
              "execution_status": "succeeded", "final_answer": "stub",
              "stop_reason": "end_turn", "usage": usage,
              "timing": {"queue_ms": 0, "agent_ms": 1, "grading_ms": None, "total_ms": 1},
              "errors": [], "trace_complete": True}

    if mode == "ok":
        emit(result)
        raise SystemExit(0)
    if mode == "silent":
        # 只发 ready 后挂住：父进程取消时不得把它当成 succeeded。
        while True:
            time.sleep(0.2)
    if mode == "exit_without_result":
        raise SystemExit(0)
    if mode == "oversized":
        sys.stdout.write("x" * (__SIZE__ + 16))
        sys.stdout.flush()
        raise SystemExit(0)
    raise SystemExit(3)
    """
).replace("__SIZE__", str(MAX_FRAME_BYTES))


def pid_alive(pid: int) -> bool:
    """跨平台的存活判定：Windows 查进程句柄退出码，POSIX 用 signal 0。"""
    if pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_until_dead(pid: int, *, timeout_s: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return False


def stub_command(attempt_id: str, *, mode: str, pid_file: str = "") -> list[str]:
    script = STUB.replace("__MODE__", json.dumps(mode)).replace(
        "__PID_FILE__", json.dumps(pid_file)
    )
    return [sys.executable, "-c", script, attempt_id]


def boot_frame(attempt_id: str) -> dict[str, Any]:
    return {
        "type": "boot",
        "role": "agent",
        "protocol_version": 1,
        "attempt_id": attempt_id,
        "runtime_root": "C:/eval/attempt/runtime",
        "workspace": "C:/eval/attempt/workspace",
        "profile_bundle": {"provider": "stub"},
        "case_snapshot": {},
    }


async def run_stub(
    *, mode: str, pid_file: str = "", attempt_id: str | None = None
) -> tuple[str, WorkerProcess]:
    attempt = attempt_id or str(uuid.uuid4())
    worker = WorkerProcess(
        attempt_id=attempt,
        command=stub_command(attempt, mode=mode, pid_file=pid_file),
    )
    await worker.start(boot_frame(attempt))
    return attempt, worker


async def test_stub_worker_delivers_result_and_exits_zero() -> None:
    attempt, worker = await run_stub(mode="ok")
    outcome = await worker.collect(timeout_s=15.0)
    exit_code = await worker.wait(timeout_s=15.0)
    assert outcome == "result"
    assert exit_code == 0
    assert worker.ipc.result is not None
    assert worker.ipc.can_seal_succeeded(exit_code=exit_code) is True
    assert worker.timing()["agent_ms"] >= 0


async def test_grandchild_is_recycled_with_the_job_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    attempt, worker = await run_stub(mode="silent", pid_file=str(pid_file))
    await worker.pump_until_idle(timeout_s=10.0)
    assert pid_file.exists(), "grandchild pid was never recorded"
    grandchild = int(pid_file.read_text(encoding="utf-8"))
    assert pid_alive(grandchild), "grandchild should be running before cancel"

    errors = await worker.cancel()
    assert worker.exit_code is not None
    assert grandchild in worker.descendant_pids or IS_WINDOWS
    assert wait_until_dead(grandchild), "孙进程必须随 owner 一起回收，不能靠进程名批量杀"
    assert any(code == "WORKER_CANCELLED" for code, _ in errors)


async def test_timeout_cancels_and_reports_execution_phase(tmp_path: Path) -> None:
    attempt, worker = await run_stub(mode="silent", pid_file=str(tmp_path / "g.pid"))
    await worker.pump_until_idle(timeout_s=10.0)
    grandchild = int((tmp_path / "g.pid").read_text(encoding="utf-8"))
    errors = await worker.run_with_timeout(timeout_s=0.5)
    assert worker.ipc.result is None
    assert worker.ipc.exit_reason_without_result() == "IPC_RESULT_MISSING"
    assert any(code == "WORKER_TIMED_OUT" for code, _ in errors)
    assert wait_until_dead(grandchild)


async def test_exit_without_result_is_not_success() -> None:
    attempt, worker = await run_stub(mode="exit_without_result")
    outcome = await worker.collect(timeout_s=15.0)
    exit_code = await worker.wait(timeout_s=15.0)
    assert exit_code == 0
    assert outcome == "eof"
    assert worker.ipc.can_seal_succeeded(exit_code=exit_code) is False


async def test_oversized_frame_fails_without_hanging() -> None:
    attempt, worker = await run_stub(mode="oversized")
    with pytest.raises(ValueError, match="IPC_FRAME_TOO_LARGE"):
        await worker.collect(timeout_s=15.0)
    await worker.cancel()


async def test_wrong_attempt_frames_are_rejected_and_worker_killed() -> None:
    attempt = str(uuid.uuid4())
    other = str(uuid.uuid4())
    worker = WorkerProcess(
        attempt_id=attempt, command=stub_command(other, mode="ok")
    )
    await worker.start(boot_frame(attempt))
    with pytest.raises(ValueError, match="IPC_ATTEMPT_MISMATCH"):
        await worker.collect(timeout_s=15.0)
    errors = await worker.cancel()
    assert worker.exit_code is not None
    assert any(code == "WORKER_ATTEMPT_MISMATCH" for code, _ in errors)


async def test_owner_close_is_idempotent() -> None:
    attempt, worker = await run_stub(mode="silent")
    await worker.pump_until_idle(timeout_s=10.0)
    first = await worker.cancel()
    second = await worker.cancel()
    assert isinstance(first, list) and isinstance(second, list)
    assert worker.process is not None


async def test_startup_failure_is_reported_not_swallowed() -> None:
    attempt = str(uuid.uuid4())
    worker = WorkerProcess(
        attempt_id=attempt,
        command=[sys.executable, "-c", "import sys; raise SystemExit(4)", attempt],
    )
    await worker.start(boot_frame(attempt))
    await worker.pump_until_idle(timeout_s=10.0)
    assert worker.exit_code == 4
    assert worker.ipc.result is None


def test_launch_identity_blocks_unverifiable_cleanup() -> None:
    identity = make_launch_identity(os.getpid())
    assert verify_launch_identity(identity) is True

    forged = dict(identity)
    forged["pid"] = os.getpid()
    forged["nonce"] = "not-the-recorded-nonce"
    assert verify_launch_identity(forged) is False

    # 身份不可验证时必须拒绝清理（交人工处置），而不是按进程名批量杀。
    with pytest.raises(ValueError, match="PROCESS_IDENTITY_UNVERIFIED"):
        verify_launch_identity({"pid": os.getpid()}, strict=True)


def test_identity_of_a_reused_pid_with_unknown_start_time_is_rejected() -> None:
    identity = make_launch_identity(os.getpid())
    identity.pop("started_at", None)
    with pytest.raises(ValueError, match="PROCESS_IDENTITY_UNVERIFIED"):
        verify_launch_identity(identity, strict=True)


def test_stub_command_helper_builds_valid_json_boot() -> None:
    payload = boot_frame(str(uuid.uuid4()))
    assert json.loads(json.dumps(payload))["protocol_version"] == 1
    assert asyncio.iscoroutinefunction(WorkerProcess.start)
