"""运行级控制：取消、单任务取消与重试（契约 §5 的三条 run mutation）。

为什么不放进 ``TeamService``：service 的方法都是同步的、跑在 ``asyncio.to_thread`` 里，
而这三条动作都要碰事件循环——取消要叫醒在场的 :class:`TeamRunHandle`，重试要在落盘后
把新运行接回调度器。所以本模块是**唯一**必须在 loop 上调用的团队入口，路由层要给它
一条不包 ``to_thread`` 的分支。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.teams.leader import ModelPresetUnavailable
from nanobot.teams.models import TeamRunSnapshot
from nanobot.teams.repository import RUN_TERMINAL_STATUSES, TeamRepository
from nanobot.teams.scheduler import TeamScheduler
from nanobot.teams.service import CrossSessionForbidden, TeamService

__all__ = ["CrossSessionForbidden", "TeamRunController"]


@dataclass
class TeamRunController:
    """按运行路由的控制入口：在场句柄优先，落库兜底。"""

    service: TeamService
    scheduler: TeamScheduler
    # 重试要在落盘后自己接手执行，所以拿的是装配期的 runtime；换默认模型不影响重试。
    runtime_provider: Callable[[], Any] = field(default=lambda: None)
    preset_resolver: Callable[[str], Any] | None = None
    workspace: Path | None = None
    _background: set[asyncio.Task[Any]] = field(default_factory=set)

    @property
    def repository(self) -> TeamRepository:
        return self.service.repository

    async def cancel_run(
        self, run_id: str, *, caller_chats: Sequence[str] = ()
    ) -> dict[str, Any]:
        """请求取消：在场运行只写 ``cancelling``，终态由收口写（契约 §5）。"""
        await self._require_session(run_id, caller_chats)
        handle = self.scheduler.get(run_id)
        if handle is not None:
            run = await handle.cancel()
            return {"runId": run_id, "status": run.status}
        run = await asyncio.to_thread(self.repository.get_run, run_id)
        if run.status in RUN_TERMINAL_STATUSES:
            # 已经是终态就照实返回，不把「已经停过」伪装成「正在停」。
            return {"runId": run_id, "status": run.status}
        final = await asyncio.to_thread(self.repository.update_run_status, run_id, "cancelled")
        return {"runId": run_id, "status": final.status}

    async def cancel_task(
        self, run_id: str, task_id: str, *, caller_chats: Sequence[str] = ()
    ) -> dict[str, Any]:
        await self._require_session(run_id, caller_chats)
        handle = self.scheduler.get(run_id)
        if handle is None:
            # 没有活句柄就没有在跑的孩子，直接落终态；跨会话被 run 归属检查挡住。
            run = await asyncio.to_thread(self.repository.get_run, run_id)
            self._require_task(run, task_id)
            task = await asyncio.to_thread(self.repository.update_task_status, task_id, "cancelled")
            return {"taskId": task_id, "status": task.status}
        run = await asyncio.to_thread(handle.run)
        self._require_task(run, task_id)
        task = await handle.cancel_task(task_id)
        return {"taskId": task_id, "status": task.status}

    async def retry_run(
        self, run_id: str, *, request_id: str, caller_chats: Sequence[str] = ()
    ) -> dict[str, Any]:
        """重开一条只含失败/中断子图的运行，并把它接回调度器。

        准入检查沿用 :meth:`TeamService.prepare_new_run`（开关/归档/档案可用），修订固定
        用原运行那一版；preset 已不存在时在落盘前就拒绝，免得建出一条注定全失败的新运行。
        """
        origin = await asyncio.to_thread(self.repository.get_run, run_id)
        await self._require_session(run_id, caller_chats)
        revision = await asyncio.to_thread(
            self.service.prepare_new_run,
            origin.binding.team_id,
            revision=origin.binding.team_revision,
        )
        self._require_presets(revision)
        context = await asyncio.to_thread(self.repository.run_context, run_id)
        turn_id = f"retry:{uuid.uuid4().hex[:16]}"
        response = await asyncio.to_thread(
            self.repository.retry_run,
            run_id,
            request_id=request_id,
            chat_id=context["chatId"],
            session_key=context["sessionKey"],
            turn_id=turn_id,
            internal_turn_id=turn_id,
        )
        if not response.get("replayed"):
            self._start(response["newRunId"], context["sessionKey"])
        return {
            "newRunId": response["newRunId"],
            "turnId": response["turnId"],
            "taskIds": response["taskIds"],
        }

    def _start(self, run_id: str, session_key: str) -> None:
        """后台接手：建句柄 → 跑复制出来的 pending 任务 → 收口 → 摘掉句柄。"""
        task = asyncio.create_task(
            self._drive(run_id, session_key), name=f"team-retry-{run_id}"
        )
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _drive(self, run_id: str, session_key: str) -> None:
        try:
            handle = await self.scheduler.adopt_run(
                run_id, session_key=session_key, runtime=self.runtime_provider()
            )
            await handle.run_pending()
            await handle.finalize(delivered=True)
        except Exception as error:  # 重试是后台任务，异常不能悄悄吞掉
            logger.warning(f"team retry run {run_id} failed: {error}")
        finally:
            self.scheduler.forget(run_id)

    def _require_presets(self, revision: Any) -> None:
        if self.preset_resolver is None:
            return
        model_ids = [revision.leader.model_id] + [item.profile.model_id for item in revision.members]
        for model_id in model_ids:
            if not model_id:
                continue
            try:
                self.preset_resolver(str(model_id))
            except (KeyError, ValueError) as error:
                raise ModelPresetUnavailable(f"MODEL_PRESET_UNAVAILABLE {error}") from error

    @staticmethod
    def _require_task(run: TeamRunSnapshot, task_id: str) -> None:
        if task_id not in {item.task_id for item in run.tasks}:
            raise CrossSessionForbidden(f"task {task_id} does not belong to run {run.run_id}")

    async def _require_session(self, run_id: str, caller_chats: Sequence[str]) -> None:
        """这条运行属于调用方挂着的某个聊天吗（契约 §3/§5 的会话访问检查）。

        ``caller_chats`` 是服务端从连接推导出来的，payload 里自称的 chatId 覆盖不了它。
        推导不出归属（空集合）按拒绝处理：控制动作只走已认证 WS，正常不会到这里。
        """
        context = await asyncio.to_thread(self.repository.run_context, run_id)
        owner = str(context.get("chatId") or "")
        if owner and owner in caller_chats:
            return
        raise CrossSessionForbidden(
            f"run {run_id} does not belong to the calling session"
        )
