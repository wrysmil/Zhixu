"""Session management API endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from aiohttp import web
from loguru import logger

from nanobot.api.schemas import SessionEndRequest
from nanobot.memory.session_end_event import (
    SessionEndEvent,
    SessionEndReason,
    reason_value,
)

if TYPE_CHECKING:
    from nanobot.memory.models import BatchReceipt
    from nanobot.session.manager import SessionManager

#: 已装配的结束 flush 消费者（``SessionEndEvent -> BatchReceipt``）。它同时是
#: 「会话结束请求真的有人处理」的判据：没有消费者时端点必须返回 503，而不是
#: 像此前那样发布一个无人订阅的事件再回 ``ok``（契约 §5，假接受）。
SESSION_END_CONSUMER_KEY = "session_end_consumer"

#: 端点直接调度的后台 flush 任务集合（保强引用，避免未完成即被 GC）。
SESSION_END_FLUSH_TASKS_KEY = "session_end_flush_tasks"

SessionEndConsumer = Callable[[SessionEndEvent], Awaitable["BatchReceipt | None"]]


def _error_json(status: int, message: str, err_type: str = "invalid_request_error") -> web.Response:
    """与 ``nanobot.api.server`` 相同的错误信封（避免循环导入，只镜像格式）。"""
    return web.json_response(
        {"error": {"message": message, "type": err_type, "code": status}},
        status=status,
    )


def _app_tasks(app: web.Application) -> set[asyncio.Task[Any]]:
    existing = app.get(SESSION_END_FLUSH_TASKS_KEY)
    if isinstance(existing, set):
        return cast("set[asyncio.Task[Any]]", existing)
    tasks: set[asyncio.Task[Any]] = set()
    app[SESSION_END_FLUSH_TASKS_KEY] = tasks
    return tasks


async def _run_flush(consumer: SessionEndConsumer, event: SessionEndEvent) -> None:
    """后台执行 flush；结果与失败原因都落结构化日志（不静默丢弃）。"""
    reason = reason_value(event.reason)
    try:
        receipt = await consumer(event)
    except Exception as exc:  # noqa: BLE001 - 后台任务失败不能击穿端点
        logger.opt(exception=exc).warning(
            "session end flush raised: session={} reason={}",
            event.session_key,
            reason,
        )
        return
    if receipt is None:
        logger.info(
            "session end flush skipped: session={} reason={} has no persisted evidence",
            event.session_key,
            reason,
        )
        return
    level = "info" if receipt.status == "completed" else "warning"
    logger.log(
        level.upper(),
        "session end flush {}: session={} reason={} batch={} through={} error={}",
        receipt.status,
        event.session_key,
        reason,
        receipt.batch_id,
        receipt.through_seq,
        receipt.error_code or "-",
    )


def _schedule_flush(
    app: web.Application,
    consumer: SessionEndConsumer,
    event: SessionEndEvent,
) -> None:
    """在运行中的事件循环里调度 flush，立即返回（不等待模型）。"""
    task = asyncio.create_task(_run_flush(consumer, event))
    tasks = _app_tasks(app)
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def cancel_pending_session_end_flushes(app: web.Application) -> None:
    """应用关闭时取消尚未完成的 flush（清理由装配处注册）。"""
    tasks = app.get(SESSION_END_FLUSH_TASKS_KEY)
    if not tasks:
        return
    pending = [task for task in list(tasks) if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    tasks.clear()


async def handle_session_end(request: web.Request) -> web.Response:
    """POST /api/sessions/{session_key}/end — 请求对该会话做记忆 flush。

    成功只表示请求被接受（已调度后台 flush），不声称模型已完成：
    ``status=ok`` 保留旧字段，新增 ``state=accepted`` 表达接受语义。
    没有装配 flush 消费者时返回 503（现有错误信封），不再报假接受。
    """
    session_key = request.match_info.get("session_key", "")

    consumer: SessionEndConsumer | None = request.app.get(SESSION_END_CONSUMER_KEY)
    if consumer is None:
        logger.warning(
            "session end rejected: no memory flush consumer assembled for session={}",
            session_key,
        )
        return _error_json(
            503,
            "Memory extraction is not wired; session end was not accepted",
            err_type="service_unavailable",
        )

    # Parse request body
    try:
        raw_body: Any = await request.json()
    except Exception:
        raw_body = {}
    body: dict[str, Any] = (
        cast("dict[str, Any]", raw_body) if isinstance(raw_body, dict) else {}
    )

    schema = SessionEndRequest(**body)
    reason_str = schema.reason

    # Validate reason; fall back to USER_CLOSE for unknown values
    try:
        reason = SessionEndReason(reason_str)
    except ValueError:
        reason = SessionEndReason.USER_CLOSE

    # Get session_manager from app state
    session_manager: SessionManager | None = request.app.get("session_manager")

    transcript: list[dict[str, Any]] = []
    if session_manager is not None:
        # Try to retrieve transcript for the session
        session = session_manager.get_cached(session_key)
        if session is not None:
            transcript = session.messages
        else:
            # Attempt to load from storage
            stored = session_manager.read_session_file(session_key)
            if stored is not None:
                transcript = stored.get("messages", [])

    event = SessionEndEvent(
        session_key=session_key,
        reason=reason,
        transcript=transcript,
    )
    _schedule_flush(request.app, consumer, event)

    return web.json_response({
        "status": "ok",
        "session_key": session_key,
        "state": "accepted",
    })
