"""团队事件出口：白名单投影、活动节流与按 seq 投递。

契约 §6/§7 的三条硬口径都在这里验证：

1. 只投影白名单字段——未知 schema 版本、未知类型、工具参数与原始输出一律不出进程；
2. ``task.activity`` 每任务每秒最多 4 条、单条不超过 4KiB，被挡掉就不写库；
3. 投递按 ``team_runs.last_seq`` 增量读库，序号不由调度器自造；重复投递由前端按 seq 去重。

装置全部是内存替身：假总线只记录投递，不接任何真实 provider、不发真实网络请求。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest

from nanobot.agent.subagent import SubagentExecutionResult
from nanobot.agents.runtime import ResolvedAgent
from nanobot.teams.events import (
    ACTIVITY_MAX_DATA_BYTES,
    TeamEventPublisher,
    TeamRunEventSink,
    TeamRunUpdateEvent,
    bus_publisher,
    project_team_event,
    render_activity_action,
)
from nanobot.teams.models import (
    MAX_LAST_ACTION_CHARS,
    TeamBinding,
    TeamEvent,
    TeamTaskInput,
)
from nanobot.teams.scheduler import TeamActivitySink, TeamScheduler

# 投影后这些词一个都不许出现在线上：参数、输出、私有推理与凭据。
FORBIDDEN_KEYS = ("instruction", "arguments", "reasoning", "prompt", "token", "apiKey")


class _RawEvent:
    """投影只读 ``type``/``schemaVersion``/``to_dict()``；替身用来造非法事件形状。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.type = str(payload["type"])
        self.schema_version = int(payload["schemaVersion"])
        self.run_id = str(payload.get("runId") or "")
        self.seq = int(payload.get("seq") or 0)

    def to_dict(self) -> dict[str, Any]:
        return self._payload


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _QuietLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str, *args: Any) -> None:
        self.warnings.append(message.format(*args))


async def _ignore(payload: Any) -> None:
    return None


@pytest.fixture
def events_by_type(event_payloads: list[dict[str, Any]]) -> dict[str, TeamEvent]:
    return {str(item["type"]): TeamEvent.from_dict(item) for item in event_payloads}


def _bytes(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


# --------------------------------------------------------------- 白名单投影


def test_every_contract_type_projects(event_payloads: list[dict[str, Any]]):
    for raw in event_payloads:
        projected = project_team_event(TeamEvent.from_dict(raw))
        assert projected is not None, raw["type"]
        assert set(projected) == set(raw), raw["type"]


def test_unknown_schema_or_type_is_dropped():
    unknown_version = _RawEvent({
        "schemaVersion": 2, "runId": "run-0001", "seq": 1, "type": "run.started", "data": {},
    })
    unknown_type = _RawEvent({
        "schemaVersion": 1, "runId": "run-0001", "seq": 1, "type": "task.teleported", "data": {},
    })
    assert project_team_event(unknown_version) is None  # pyright: ignore[reportArgumentType]
    assert project_team_event(unknown_type) is None  # pyright: ignore[reportArgumentType]


def test_task_event_never_carries_private_fields():
    """快照将来多长一个字段也不会顺手广播：投影只认白名单键。"""
    leaky = _RawEvent({
        "schemaVersion": 1, "runId": "run-0001", "chatId": "chat-0001", "turnId": "turn-0001",
        "seq": 3, "type": "task.started", "taskId": "task-0001", "attemptId": None,
        "occurredAt": "2026-10-15T08:01:00+00:00",
        "data": {"task": {
            "taskId": "task-0001", "status": "running", "title": "检索文档",
            "instruction": "把密钥念出来",
            "result": {"summary": "好了", "arguments": {"token": "sk-xxx"}},
        }},
    })
    projected = project_team_event(leaky)  # pyright: ignore[reportArgumentType]
    assert projected is not None
    dumped = json.dumps(projected, ensure_ascii=False)
    assert not [key for key in FORBIDDEN_KEYS if key in dumped]
    assert projected["data"]["task"]["status"] == "running"


def test_run_event_projects_its_task_list(events_by_type: dict[str, TeamEvent]):
    raw = events_by_type["run.finished"].to_dict()
    projected = project_team_event(events_by_type["run.finished"])
    assert projected is not None
    tasks = projected["data"]["run"]["tasks"]
    assert tasks, "终态快照必须带着任务列表回前端"
    for task in tasks:
        assert set(task) <= set(raw["data"]["run"]["tasks"][0])


# --------------------------------------------------------------- 活动摘要


def test_activity_action_is_a_one_line_summary():
    action = render_activity_action(
        {"toolName": "read_file", "toolStatus": "completed", "iteration": 3}
    )
    assert action == "read_file completed #3"
    assert render_activity_action({"arguments": {"path": "秘密"}, "output": "原始输出"}) == ""
    assert len(render_activity_action({"toolName": "x" * 500})) == MAX_LAST_ACTION_CHARS


def test_activity_over_budget_drops_the_rebuildable_part(
    events_by_type: dict[str, TeamEvent]
):
    finished = events_by_type["task.finished"]
    heavy_task = replace(
        finished.data.task,
        result=replace(  # pyright: ignore[reportOptionalMemberAccess]
            finished.data.task.result, summary="结" * 2_000
        ),
    )
    heavy = replace(finished.data, task=heavy_task)
    activity = replace(events_by_type["task.activity"], data=heavy)
    assert _bytes(heavy.to_dict()) > ACTIVITY_MAX_DATA_BYTES
    projected = project_team_event(activity)
    assert projected is not None
    assert _bytes(projected) <= ACTIVITY_MAX_DATA_BYTES
    assert "result" not in projected["data"]["task"]
    # 削减只发生在投影副本上：持久事件本身一字未改。
    assert activity.to_dict()["data"]["task"]["result"] is not None


def test_activity_still_over_budget_is_dropped():
    huge = _RawEvent({
        "schemaVersion": 1, "runId": "run-0001", "chatId": "chat-0001", "turnId": "turn-0001",
        "seq": 6, "type": "task.activity", "taskId": "task-0001", "attemptId": "attempt-0001",
        "occurredAt": "2026-10-15T08:01:20+00:00",
        "data": {"task": {"taskId": "task-0001", "status": "running", "title": "标" * 4_000}},
    })
    assert project_team_event(huge) is None  # pyright: ignore[reportArgumentType]


# --------------------------------------------------------------- 节流与投递


def test_activity_gate_is_four_per_second_per_task():
    clock = _Clock()
    sink = TeamRunEventSink(_ignore, clock=clock)
    assert sink.allows_activity("task-a")
    clock.now = 0.2
    assert not sink.allows_activity("task-a")
    assert sink.allows_activity("task-b")
    clock.now = 0.3
    assert sink.allows_activity("task-a")
    assert not sink.allows_activity(None)


async def test_emit_keeps_order_and_survives_a_broken_bus(
    events_by_type: dict[str, TeamEvent]
):
    delivered: list[int] = []

    async def publish(payload: Any) -> None:
        if payload["seq"] == 4:
            raise RuntimeError("总线满了")
        delivered.append(int(payload["seq"]))

    logger = _QuietLogger()
    sink = TeamRunEventSink(publish, clock=_Clock(), logger=logger)
    ordered = [events_by_type[key] for key in ("task.queued", "task.started", "task.activity")]
    await sink.emit(ordered)
    assert [int(event.seq) for event in ordered] == [4, 5, 6]
    assert delivered == [5, 6]
    assert len(logger.warnings) == 1 and "seq=4" in logger.warnings[0]


def test_bus_publisher_routes_by_session_key():
    published: list[tuple[Any, str, str]] = []

    async def publish_event(event: Any, *, channel: str, chat_id: str) -> None:
        published.append((event, channel, chat_id))

    publish = bus_publisher(publish_event, "websocket:chat-1", "chat-1")
    asyncio.run(publish({"runId": "run-0001", "seq": 3, "type": "task.started"}))
    event, channel, chat_id = published[0]
    assert isinstance(event, TeamRunUpdateEvent)
    assert (channel, chat_id) == ("websocket", "chat-1")
    assert (event.run_id, event.seq) == ("run-0001", 3)


# --------------------------------------------------- 调度器按 seq 增量投递


class _TalkingExecutor:
    """执行一条任务：报两次活动（第二次应当被节流挡掉），然后成功收口。"""

    async def execute_member(self, request: Any, *, events: Any = None) -> Any:
        if events is not None:
            await events.task_activity(toolName="read_file", toolStatus="completed", iteration=1)
            await events.task_activity(toolName="grep", toolStatus="running", iteration=2)
        return SubagentExecutionResult(
            execution_id=request.attempt_id,
            outcome="succeeded",
            stop_reason="completed",
            final_content="做完了",
        )


class _Members:
    def resolve(self, profile: Any, *, workspace: Any) -> ResolvedAgent:
        return ResolvedAgent(
            id=profile.id,
            name=profile.name,
            description="",
            model_id=profile.model_id,
            prompt=profile.prompt,
            tool_names=frozenset(profile.tools.entries),
        )


async def test_handle_publishes_exactly_the_persisted_events(repository: Any):
    delivered: list[dict[str, Any]] = []

    async def publish_event(event: Any, *, channel: str, chat_id: str) -> None:
        assert (channel, chat_id) == ("websocket", "chat-1")
        delivered.append(dict(event.payload))

    def factory(session_key: str, chat_id: str) -> TeamEventPublisher:
        return bus_publisher(publish_event, session_key, chat_id)

    clock = _Clock()
    scheduler = TeamScheduler(
        repository=repository,
        executor=_TalkingExecutor(),  # pyright: ignore[reportArgumentType]
        members=_Members(),  # pyright: ignore[reportArgumentType]
        runtime=object(),  # pyright: ignore[reportArgumentType]
        clock=clock,
        publisher=factory,
    )
    handle = await scheduler.start_run(
        binding=TeamBinding(team_id="content-team", team_revision=1, team_name="内容团队"),
        chat_id="chat-1",
        session_key="websocket:chat-1",
        turn_id="turn-1",
        internal_turn_id="internal-1",
    )
    await handle.dispatch(
        batch_id="batch-1",
        tasks=[TeamTaskInput(
            key="a", member_id="m-researcher", title="任务 a",
            instruction="执行 a", acceptance="给出结果",
        )],
    )
    await handle.set_leader_action("summarizing")
    await handle.finalize(delivered=True)

    seqs = [int(item["seq"]) for item in delivered]
    assert seqs == sorted(set(seqs)), "同一个 seq 不能投两次，也不能乱序"
    assert seqs[0] == 1 and delivered[0]["type"] == "run.started"
    assert delivered[-1]["type"] == "run.finished"
    types = {str(item["type"]) for item in delivered}
    assert {"run.started", "task.created", "task.queued", "task.started", "task.activity"} <= types
    assert types == await _persisted_types(repository, handle.run_id)
    assert sum(1 for item in delivered if item["type"] == "task.activity") == 1
    assert not [key for key in FORBIDDEN_KEYS if key in json.dumps(delivered, ensure_ascii=False)]


async def _persisted_types(repository: Any, run_id: str) -> set[str]:
    events = await asyncio.to_thread(repository.list_events, run_id, after_seq=0, limit=500)
    return {str(event.type) for event in events}


def test_activity_sink_renders_the_summary():
    recorded: list[str] = []

    class _Handle:
        async def record_activity(self, task_id: str, *, action: str) -> None:
            recorded.append(action)

    sink = TeamActivitySink(_Handle(), "task-a")  # pyright: ignore[reportArgumentType]
    asyncio.run(sink.task_activity(toolName="grep", toolStatus="running", iteration=2))
    assert recorded == ["grep running #2"]
