"""团队运行的公开事件：活动节流、边缘白名单投影与总线投递。

契约 §6：状态变更与事件**先同事务落盘**，再排入 MessageBus 的网络投影；广播失败不回滚
执行，也不在事务锁内 await。契约 §7：边缘只投影白名单字段——私有推理、完整工具参数与
原始 shell 输出一律不出进程。

``project_team_event`` 返回 ``None`` 表示「这条我看不懂」：未知 schema 版本或未知类型。
前端拿到 ``resetRequired``/没有事件时会回退去查询快照，普通聊天不受影响。
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from loguru import logger as default_logger

from nanobot.events import AgentEvent
from nanobot.teams.models import MAX_LAST_ACTION_CHARS, TeamEvent

# 契约 §6：task.activity 每任务每秒最多 4 条，单条 data 不超过 4KiB。
ACTIVITY_MIN_INTERVAL_SECONDS = 0.25
ACTIVITY_MAX_DATA_BYTES = 4_096
TEAM_EVENT_SCHEMA_VERSION = 1

# 每种事件的 data 只允许这些键（与 tests/fixtures/teams/event.json 同一口径）。
EVENT_DATA_FIELDS: dict[str, tuple[str, ...]] = {
    "run.started": ("binding", "startedAt"),
    "leader.updated": ("leaderAction",),
    "task.created": ("task",),
    "task.queued": ("task",),
    "task.started": ("task",),
    "task.activity": ("task",),
    "task.finished": ("task",),
    "run.cancelling": ("status",),
    "run.finished": ("run",),
}

TASK_WIRE_FIELDS: tuple[str, ...] = (
    "taskId", "memberId", "agentId", "title", "acceptance", "dependsOn", "status",
    "attemptId", "lastAction", "startedAt", "finishedAt", "result",
)
TASK_RESULT_WIRE_FIELDS: tuple[str, ...] = (
    "taskId", "attemptId", "memberId", "agentId", "status", "stopReason", "summary",
    "artifacts", "errorCode", "usage",
)
RUN_WIRE_FIELDS: tuple[str, ...] = (
    "runId", "chatId", "turnId", "binding", "status", "lastSeq", "tasks", "leaderAction",
    "startedAt", "finishedAt", "parentRunId",
)

# 已经投影出去的载荷形状由 ``TeamRunUpdateEvent`` 承担，这里只投递白名单后的 wire dict。
TeamEventPublisher = Callable[[Mapping[str, Any]], Awaitable[None]]
# 工厂按 (session_key, chat_id) 取一条运行的投递出口：总线与通道路由留在 gateway 侧。
TeamPublisherFactory = Callable[[str, str], TeamEventPublisher]


@dataclass(frozen=True)
class TeamRunUpdateEvent(AgentEvent):
    """投影后的运行更新；由 gateway 包成总线事件送到 WebUI 边缘。"""

    run_id: str
    seq: int
    payload: Mapping[str, Any]


def _pick(source: Any, fields: tuple[str, ...]) -> Any:
    if not isinstance(source, Mapping):
        return source
    raw = cast("Mapping[str, Any]", source)
    return {key: raw[key] for key in fields if key in raw}


def _project_task(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    task: dict[str, Any] = dict(_pick(value, TASK_WIRE_FIELDS))
    if "result" in task:
        task["result"] = _pick(task["result"], TASK_RESULT_WIRE_FIELDS)
    return task


def _project_run(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    run: dict[str, Any] = dict(_pick(value, RUN_WIRE_FIELDS))
    tasks = run.get("tasks")
    if isinstance(tasks, list):
        run["tasks"] = [_project_task(item) for item in cast("list[Any]", tasks)]
    return run


def project_run_snapshot(run: Any) -> dict[str, Any]:
    """运行快照的公开投影：hydrate 与查询路由都用它，字段口径和事件里的一致。"""
    projected = _project_run(run.to_dict() if hasattr(run, "to_dict") else run)
    return dict(cast("Mapping[str, Any]", projected)) if isinstance(projected, Mapping) else {}


def project_team_event(event: TeamEvent) -> dict[str, Any] | None:
    """把持久事件投影成前端线格式；看不懂的类型返回 ``None``（前端回退查快照）。"""
    allowed = EVENT_DATA_FIELDS.get(str(event.type))
    if event.schema_version != TEAM_EVENT_SCHEMA_VERSION or allowed is None:
        return None
    wire: dict[str, Any] = event.to_dict()
    source: Mapping[str, Any] = wire.get("data") or {}
    data: dict[str, Any] = {}
    for key in allowed:
        if key not in source:
            continue
        value: Any = source[key]
        if key == "task":
            value = _project_task(value)
        elif key == "run":
            value = _project_run(value)
        data[key] = value
    projected: dict[str, Any] = dict(wire)
    projected["data"] = data
    if str(event.type) != "task.activity":
        return projected
    return _fit_activity(projected)


def _fit_activity(projected: dict[str, Any]) -> dict[str, Any] | None:
    """活动事件超预算时先削掉可重建的字段；仍然超限就整个丢弃。

    终态与生命周期事件不会走到这里；丢掉一条中间活动不影响任务最新快照的正确性。
    """
    if len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) <= ACTIVITY_MAX_DATA_BYTES:
        return projected
    data = cast("Mapping[str, Any]", projected.get("data") or {})
    task = data.get("task")
    if not isinstance(task, dict):
        return None
    target = cast("dict[str, Any]", task)
    target.pop("result", None)
    action: Any = target.get("lastAction")
    if isinstance(action, str):
        target["lastAction"] = action[:MAX_LAST_ACTION_CHARS]
    if len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) <= ACTIVITY_MAX_DATA_BYTES:
        return projected
    return None


def render_activity_action(payload: Mapping[str, Any]) -> str:
    """子执行活动 → 公开的「最近动作」一句话；参数与输出一律不进摘要。"""
    name = str(payload.get("toolName") or "").strip()
    if not name:
        return ""
    status = str(payload.get("toolStatus") or "").strip()
    iteration = payload.get("iteration")
    parts = [name, status] if status else [name]
    if isinstance(iteration, int) and iteration > 0:
        parts.append(f"#{iteration}")
    return " ".join(parts)[:MAX_LAST_ACTION_CHARS]


class TeamRunEventSink:
    """一条运行的事件出口：按任务节流活动写库，投影后投递，广播失败只记日志。"""

    def __init__(
        self,
        publish: TeamEventPublisher,
        *,
        clock: Callable[[], float] = time.monotonic,
        logger: Any = default_logger,
    ) -> None:
        self._publish = publish
        self._clock = clock
        self._logger = logger
        self._last_activity: dict[str, float] = {}

    def allows_activity(self, task_id: str | None) -> bool:
        """每任务每秒 4 条的写库闸门；被挡住的中间活动不占事件序号。"""
        if task_id is None:
            return False
        now = self._clock()
        previous = self._last_activity.get(task_id)
        if previous is not None and now - previous < ACTIVITY_MIN_INTERVAL_SECONDS:
            return False
        self._last_activity[task_id] = now
        return True

    async def emit(self, events: Sequence[TeamEvent]) -> None:
        """按 seq 升序投递；任何一条失败都不向上抛（执行已经落盘，不能因为广播炸掉）。"""
        for event in events:
            payload = project_team_event(event)
            if payload is None:
                continue
            try:
                await self._publish(payload)
            except Exception as exc:  # noqa: BLE001 - 广播失败只降级为「需要补拉」
                self._logger.warning(
                    "team event projection failed run={} seq={}: {}", event.run_id, event.seq, exc
                )


def bus_publisher(
    publish_event: Callable[..., Awaitable[None]],
    session_key: str,
    chat_id: str,
) -> TeamEventPublisher:
    """把投影后的载荷包成总线事件；``publish_event`` 就是 ``MessageBus.publish_event``。"""

    async def _publish(payload: Mapping[str, Any]) -> None:
        channel = session_key.split(":", 1)[0] if ":" in session_key else "websocket"
        await publish_event(
            TeamRunUpdateEvent(
                run_id=str(payload.get("runId") or ""),
                seq=int(payload.get("seq") or 0),
                payload=payload,
            ),
            channel=channel,
            chat_id=chat_id,
        )

    return _publish
