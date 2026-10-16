"""T09 调度器：原子领取、并发预算、取消与重启恢复。

调度器是**唯一的结果提交者**（plan T09）：attempt 的终态只能由这里写，HTTP handler
入队后立刻返回 202，不参与执行也不参与提交。

三条容易写错的并发规则，这里用结构保证而不是靠注释提醒：

1. **总闸只有一个**。Agent Worker 与 Judge Worker 共用同一个信号量
   （:attr:`Scheduler._slots`）。切换阶段时先释放 Agent 槽位再申请 Judge 槽位；
   反过来做会在 concurrency=1 的运行上自锁——那一个槽位正被自己攥着。
2. **取消先回收后落状态**。``cancel_run`` 只把运行推进到 cancelling，等 owner 确认
   进程树收干净才 finalize 成 cancelled。反过来做会让页面显示「已取消」而进程还在跑。
3. **孤儿清理只认身份**。重启时读回旧 PID 必须核对启动身份；核不了就记人工处置提示，
   绝不按进程名批量杀（契约 §9 与 dispatch 安全约束）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from nanobot.evaluation.processes import verify_launch_identity
from nanobot.evaluation.repository import EvaluationRepository, StateConflict

#: 服务级并发总上限（契约 §4）：跨所有 run 生效，Agent 与 Judge 共用。
SERVICE_CONCURRENCY_LIMIT = 3

#: 单次调度循环的空闲等待；只在没有可领取项时生效。
DEFAULT_POLL_INTERVAL_S = 0.05

#: 取消时等待 owner 回收进程树的上限；超时按人工处置记录，不再无限等。
CANCEL_CLEANUP_TIMEOUT_S = 30.0


class SchedulerAlreadyRunningError(RuntimeError):  # noqa: N818 - 名称由错误码映射锁定
    """第二个服务实例试图在同一个 data_dir 上启动。"""


@dataclass(frozen=True)
class SchedulerConfig:
    data_dir: Path
    concurrency: int = SERVICE_CONCURRENCY_LIMIT
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S

    def __post_init__(self) -> None:
        if not 1 <= self.concurrency <= SERVICE_CONCURRENCY_LIMIT:
            raise ValueError(f"SCHEDULER_CONCURRENCY_INVALID: {self.concurrency}")

    @property
    def lock_path(self) -> Path:
        return Path(self.data_dir) / "service.lock"

    @property
    def runtime_root(self) -> Path:
        return Path(self.data_dir) / "runs"


@dataclass
class RecoveryNotice:
    """需要人工处置的残留：身份核不了，或回收超时不放弃。"""

    run_id: str | None
    attempt_id: str | None
    pid: int | None
    message: str


@dataclass
class AttemptOutcome:
    """一次 attempt 的判分结论，由 services 组装，调度器只负责提交。"""

    execution_status: str
    grading_status: str
    verdict: str
    result: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def passed(cls) -> "AttemptOutcome":
        return cls(
            execution_status="succeeded",
            grading_status="completed",
            verdict="passed",
            result={},
        )

    @classmethod
    def failed(cls, *, code: str, message: str, phase: str = "execution") -> "AttemptOutcome":
        return cls(
            execution_status="failed",
            grading_status="completed",
            verdict="failed",
            result={"errors": [{"phase": phase, "code": code, "message": message}]},
        )


@dataclass
class CalibrationItemOutcome:
    """一个校准项的结论；``status`` 决定作业能否汇总出 agreement。"""

    status: str
    result: dict[str, Any] = field(default_factory=dict)


class AttemptPipeline(Protocol):
    """执行一次 attempt 并给出结论；真实实现见 :mod:`nanobot.evaluation.services`。"""

    async def execute(
        self, attempt: Mapping[str, Any], *, run: Mapping[str, Any]
    ) -> AttemptOutcome: ...

    async def calibrate(
        self, claimed: Mapping[str, Any]
    ) -> CalibrationItemOutcome: ...

    def settle_calibration(self, job_id: str) -> None: ...


def kill_verified_process(pid: int) -> None:
    """只对**已核实身份**的进程动手；调用方必须先通过 verify_launch_identity。"""
    if pid <= 0 or pid == os.getpid():
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        if os.name == "nt":
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGKILL)


def _file_lock(path: Path) -> "Any":
    """服务独占锁：整个生命周期持有，第二个实例拿不到就立刻退出。"""
    from filelock import FileLock

    return FileLock(str(path), timeout=0)


class Scheduler:
    """受管理队列的唯一执行者与唯一结果提交者。"""

    def __init__(
        self,
        *,
        repository: EvaluationRepository,
        pipeline: AttemptPipeline,
        config: SchedulerConfig,
    ) -> None:
        self._repository = repository
        self._pipeline = pipeline
        self._config = config
        # Agent 与 Judge 共用的进程信号量：切换阶段必须先释放再申请。
        self._slots = asyncio.Semaphore(config.concurrency)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_requests: set[str] = set()
        self._dispatcher: asyncio.Task[None] | None = None
        self._lock: Any | None = None
        self._lock_depth = 0
        self._notices: list[RecoveryNotice] = []
        self._started = False

    # ------------------------------------------------------------------ 属性

    @property
    def recovery_notices(self) -> tuple[RecoveryNotice, ...]:
        return tuple(self._notices)

    @property
    def active_task_count(self) -> int:
        return len([task for task in self._tasks.values() if not task.done()])

    @property
    def config(self) -> SchedulerConfig:
        return self._config

    # -------------------------------------------------------------- 生命周期

    async def start(self) -> None:
        if self._started:
            return
        self._config.data_dir.mkdir(parents=True, exist_ok=True)
        lock = _file_lock(self._config.lock_path)
        try:
            lock.acquire()
        except Exception as exc:  # filelock.Timeout 及其子类
            raise SchedulerAlreadyRunningError(
                f"EVAL_SERVICE_ALREADY_RUNNING: {self._config.lock_path} ({exc})"
            ) from exc
        self._lock = lock
        self._lock_depth += 1
        self._started = True
        try:
            self._recover()
            self._dispatcher = asyncio.create_task(
                self._dispatch_loop(), name="eval-dispatcher"
            )
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        dispatcher, self._dispatcher = self._dispatcher, None
        if dispatcher is not None:
            dispatcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await dispatcher
        # 逆序回收：先等在飞的 attempt 收尾，再放开服务锁。
        pending = list(self._tasks.values())
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        self._release_lock()

    async def __aenter__(self) -> "Scheduler":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.stop()

    def _release_lock(self) -> None:
        lock, self._lock = self._lock, None
        self._lock_depth = max(0, self._lock_depth - 1)
        if lock is not None:
            with contextlib.suppress(Exception):
                lock.release()

    # ------------------------------------------------------------------ 取消

    async def cancel_run(self, run_id: str) -> dict[str, Any]:
        """排队/运行中 → cancelling；取消在飞任务，等 owner 回收完再落 cancelled。"""
        self._repository.cancel_run(run_id)
        self._cancel_requests.add(run_id)
        await self._finalize_cancelling(run_id)
        return self._repository.get_run(run_id)

    async def _finalize_cancelling(self, run_id: str) -> None:
        self._abort_tasks_for(run_id)
        try:
            async with asyncio.timeout(CANCEL_CLEANUP_TIMEOUT_S):
                while self._any_task_for(run_id):
                    await asyncio.sleep(0.01)
        except TimeoutError:
            self._notices.append(
                RecoveryNotice(
                    run_id=run_id,
                    attempt_id=None,
                    pid=None,
                    message="取消后进程回收超时，已转人工处置",
                )
            )
        # 收干净（或超时放弃）之后才落终态；取消标记必须留到任务真正退出之后，
        # 否则被唤醒的执行器会看不到「已取消」，把通过结论提交上去。
        self._repository.finalize_cancel(run_id)
        self._cancel_requests.discard(run_id)

    def _abort_tasks_for(self, run_id: str) -> None:
        prefix = f"attempt:{run_id}:"
        for name, task in list(self._tasks.items()):
            if name.startswith(prefix) and not task.done():
                task.cancel()

    def _any_task_for(self, run_id: str) -> bool:
        return any(
            not task.done() and task.get_name().startswith(f"attempt:{run_id}:")
            for task in self._tasks.values()
        )

    # ------------------------------------------------------------------ 派发

    async def _dispatch_loop(self) -> None:
        while True:
            try:
                await self._dispatch_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # 派发本身出错不能让整个服务停摆：下一轮重试。
                pass
            await asyncio.sleep(self._config.poll_interval_s)

    async def _dispatch_once(self) -> None:
        self._reap_done()
        if self._cancel_requests:
            for run_id in tuple(self._cancel_requests):
                if not self._any_task_for(run_id):
                    self._repository.finalize_cancel(run_id)
                    self._cancel_requests.discard(run_id)
        dispatched_any = False
        for row in self._repository.open_runs():
            run_id = str(row["id"])
            if run_id in self._cancel_requests:
                continue
            if self._inflight_for(run_id) >= self._run_limit(run_id):
                continue
            claimed = self._repository.claim_attempt()
            if claimed is None:
                break
            # 领取是全队 FIFO，回来的行未必属于这一轮正在看的 run：任务名与取消判定
            # 都必须跟 attempt 自己的 run_id，否则取消一个 run 会放过它名下在飞的项。
            run_id = str(claimed["run_id"])
            dispatched_any = True
            task = asyncio.create_task(
                self._run_attempt(claimed, run_id),
                name=f"attempt:{run_id}:{claimed['id']}",
            )
            self._tasks[task.get_name()] = task
        if not dispatched_any:
            await self._dispatch_calibration()

    async def _dispatch_calibration(self) -> None:
        """领取并派发一个校准项；与 attempt 共用同一个服务级信号量（契约 §3.1/§4）。

        排在 attempt 之后：运行是用户等着看结果的主链路，校准是后台门禁。空表时
        ``claim_calibration_item`` 返回 None，不会写任何状态。
        """
        claimed = self._repository.claim_calibration_item()
        if claimed is None:
            return
        task = asyncio.create_task(
            self._run_calibration_item(claimed),
            name=f"calibration:{claimed['job_id']}:{claimed['id']}",
        )
        self._tasks[task.get_name()] = task

    async def _run_calibration_item(self, claimed: Mapping[str, Any]) -> None:
        item_id = str(claimed["id"])
        job_id = str(claimed["job_id"])
        try:
            async with self._slots:
                if self._calibration_stopped(job_id):
                    raise asyncio.CancelledError
                outcome = await self._pipeline.calibrate(claimed)
            # 取消之后不能再把一致率提交上去：作业已由 cancel_calibration 落终态。
            if self._calibration_stopped(job_id):
                raise asyncio.CancelledError
            self._finish_calibration_item(
                item_id, status=outcome.status, result=outcome.result
            )
        except asyncio.CancelledError:
            self._finish_calibration_item(item_id, status="cancelled", result={})
            raise
        except Exception as exc:
            # 前置不满足（未确认 / 量表不匹配 / gold 版本变了）与执行异常都只让**这一项**
            # 落 failed；作业随后收口成未完成，绝不伪装成「跑完了但一致率是零」。
            self._finish_calibration_item(item_id, status="failed", result={"error": str(exc)})
        self._pipeline.settle_calibration(job_id)

    def _finish_calibration_item(
        self, item_id: str, *, status: str, result: Mapping[str, Any]
    ) -> None:
        """已终结（多为取消抢先）的项静默跳过：重复提交不该掀翻派发任务。"""
        with contextlib.suppress(StateConflict):
            self._repository.finish_calibration_item(item_id, status=status, result=result)

    def _calibration_stopped(self, job_id: str) -> bool:
        try:
            status = self._repository.get_calibration(job_id)["status"]
        except Exception:
            return True
        return status in ("cancelled", "interrupted")

    def _reap_done(self) -> None:
        for name, task in list(self._tasks.items()):
            if task.done():
                self._tasks.pop(name, None)

    def _inflight_for(self, run_id: str) -> int:
        prefix = f"attempt:{run_id}:"
        return sum(
            1 for name, task in self._tasks.items()
            if name.startswith(prefix) and not task.done()
        )

    def _run_limit(self, run_id: str) -> int:
        """单个 run 的并发上限来自冻结快照；缺省 1，绝不因为读不到就放大。"""
        try:
            snapshot = self._repository.get_run(run_id).get("snapshot") or {}
        except Exception:
            return 1
        limits = snapshot.get("limits") if isinstance(snapshot, Mapping) else None
        value = limits.get("concurrency") if isinstance(limits, Mapping) else None
        return value if isinstance(value, int) and not isinstance(value, bool) else 1

    async def _run_attempt(self, claimed: Mapping[str, Any], run_id: str) -> None:
        attempt_id = str(claimed["id"])
        try:
            # Agent 阶段占一个进程槽位，Judge 阶段在 execute 内部另行申请。
            async with self._slots:
                if self._cancelled(run_id):
                    raise asyncio.CancelledError
                run = self._repository.get_run(run_id)
                outcome = await self._pipeline.execute(claimed, run=run)
            if self._cancelled(run_id):
                # 取消已经发起：不再提交通过结论，只落 cancelled。
                raise asyncio.CancelledError
            self._repository.finish_attempt(attempt_id, result=_as_result(outcome))
        except asyncio.CancelledError:
            self._settle_cancelled(attempt_id)
            raise
        except Exception as exc:
            self._repository.finish_attempt(
                attempt_id,
                result=_as_result(
                    AttemptOutcome.failed(
                        code="SCHEDULER_STAGE_FAILED", message=str(exc)
                    )
                ),
            )

    def _cancelled(self, run_id: str) -> bool:
        """以持久状态为准。

        只看内存里的取消集合会漏：取消收口后集合会被清掉，此时仍在收尾的执行器
        会以为「没人取消过我」，把通过结论提交上去。
        """
        if run_id in self._cancel_requests:
            return True
        try:
            status = self._repository.get_run(run_id)["status"]
        except Exception:
            return True
        return status in ("cancelling", "cancelled")

    def _settle_cancelled(self, attempt_id: str) -> None:
        with contextlib.suppress(StateConflict, Exception):
            self._repository.finish_attempt(
                attempt_id,
                result=_as_result(
                    AttemptOutcome(
                        execution_status="cancelled",
                        grading_status="not_applicable",
                        verdict="not_evaluated",
                        result={},
                    )
                ),
            )

    # -------------------------------------------------------------- 重启恢复

    def _recover(self) -> None:
        """残留运行一律标 interrupted：不自动补跑，也不猜测未完成项的结果。

        只动 ``running``/``cancelling``：``queued`` 说明上次进程根本没开始执行它，
        那是正常待办而不是残留，把它标成 interrupted 会让用户白排一次队。
        """
        for attempt in self._repository.list_open_attempts():
            pid = attempt.get("pid")
            nonce = attempt.get("process_nonce")
            if isinstance(pid, int) and isinstance(nonce, str) and nonce:
                self._recover_process(attempt, pid, nonce)
        for row in self._repository.open_runs():
            if row["status"] in ("running", "cancelling"):
                self._repository.mark_interrupted(str(row["id"]))
        for job in self._repository.open_calibrations():
            # 与 attempt 同一口径：queued 的作业是正常待办，不是上次进程留下的残留。
            # 领取会把作业推到 running，所以 running 才是「有项真的在飞」。
            if job["status"] == "running":
                self._repository.mark_calibration_interrupted(str(job["id"]))

    def _recover_process(
        self, attempt: Mapping[str, Any], pid: int, nonce: str
    ) -> None:
        """核对启动身份后才动手；核不了就记人工处置，禁止按进程名杀。"""
        identity = {"pid": pid, "nonce": nonce, "started_at": None}
        verified = False
        with contextlib.suppress(ValueError):
            verified = verify_launch_identity(identity)
        if verified:
            kill_verified_process(pid)
            return
        self._notices.append(
            RecoveryNotice(
                run_id=str(attempt["run_id"]),
                attempt_id=str(attempt["id"]),
                pid=pid,
                message=(
                    f"PID {pid} 的启动身份无法核实，未自动清理，"
                    f"请人工确认后处置（attempt={attempt['id']}）"
                ),
            )
        )


def _as_result(outcome: AttemptOutcome) -> dict[str, Any]:
    return {
        "execution_status": outcome.execution_status,
        "grading_status": outcome.grading_status,
        "verdict": outcome.verdict,
        "result": json.loads(json.dumps(outcome.result, ensure_ascii=False, default=str)),
    }


def run_limits_from_snapshot(snapshot: Mapping[str, Any]) -> Sequence[int]:
    """给 services 用：冻结快照里的 (max_iterations, timeout_s)。"""
    limits = snapshot.get("limits") if isinstance(snapshot, Mapping) else None
    if not isinstance(limits, Mapping):
        return (12, 120)
    iterations = limits.get("max_iterations")
    timeout = limits.get("timeout_s")
    return (
        iterations if isinstance(iterations, int) else 12,
        timeout if isinstance(timeout, int) else 120,
    )
