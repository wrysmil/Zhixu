"""团队事件在 WebUI 边缘的投影：``team_event`` 帧、``team_snapshot`` 补发与订阅门。

契约 §6 要求前端只从这两帧拿团队状态：运行中的增量走 ``team_event``，重连先收
``team_snapshot`` 再按 seq 补拉。这里只验证**帧的形状与投递条件**，事件本身怎么产生
见 ``tests/teams/test_events.py``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.bus.events import OutboundMessage
from nanobot.teams.events import TeamRunUpdateEvent, project_run_snapshot, project_team_event
from nanobot.teams.models import TeamEvent
from nanobot.webui.outbound_projection import WebUIOutboundProjector
from nanobot.webui.outbound_wire import project_notification

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "teams" / "event.json"


def _fixture_event(event_type: str) -> TeamEvent:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return TeamEvent.from_dict(next(item for item in raw if item["type"] == event_type))


class _FakeTransport:
    def __init__(self, subscribers: int = 1) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.persistence: list[str] = []
        self.subscribers = [object()] * subscribers

    def webui_subscribers(self, chat_id: str) -> tuple[Any, ...]:
        return tuple(self.subscribers if chat_id == "chat-0001" else ())

    async def send_payload(
        self,
        chat_id: str,
        payload: dict[str, Any],
        *,
        persistence: str,
        metadata: dict[str, Any] | None = None,
        turn_owner: str | None = None,
    ) -> None:
        self.payloads.append(dict(payload))
        self.persistence.append(persistence)


class _FakeSessionProjection:
    def hydration_events(self, session_key: str, chat_id: str) -> list[dict[str, Any]]:
        return []


def _projector(
    transport: _FakeTransport, team_runs: list[dict[str, Any]] | None = None
) -> WebUIOutboundProjector:
    async def hydrate(chat_id: str) -> list[dict[str, Any]]:
        return list(team_runs or [])

    return WebUIOutboundProjector(  # pyright: ignore[reportArgumentType]
        transport, _FakeSessionProjection(),  # pyright: ignore[reportArgumentType]
        team_hydration=hydrate,
    )


def test_team_event_frame_matches_the_contract():
    event = _fixture_event("task.started")
    payload = project_team_event(event)
    assert payload is not None
    projection = project_notification(
        "chat-0001", TeamRunUpdateEvent(run_id=event.run_id, seq=event.seq, payload=payload)
    )
    assert projection is not None
    assert projection.persistence == "transient"
    assert projection.deliver_offline is False
    frame = projection.payload
    assert frame["event"] == "team_event" and frame["type"] == "team_event"
    assert frame["chat_id"] == "chat-0001"
    # turn_id 来自事件自己的 turnId：一条运行的事件属于开始它的那一轮。
    assert frame["turn_id"] == "turn-0001"
    assert frame["data"]["seq"] == 5 and frame["data"]["type"] == "task.started"


async def test_projector_delivers_team_events_to_subscribers_only():
    event = _fixture_event("run.finished")
    payload = project_team_event(event)
    assert payload is not None
    update = TeamRunUpdateEvent(run_id=event.run_id, seq=event.seq, payload=payload)

    transport = _FakeTransport(subscribers=0)
    await _projector(transport).send(
        OutboundMessage(channel="websocket", chat_id="chat-0001", content="", event=update)
    )
    assert transport.payloads == []

    connected = _FakeTransport()
    await _projector(connected).send(
        OutboundMessage(channel="websocket", chat_id="chat-0001", content="", event=update)
    )
    assert [item["event"] for item in connected.payloads] == ["team_event"]
    assert connected.persistence == ["transient"]


async def test_hydrate_replays_open_run_snapshots():
    run = _fixture_event("run.finished").to_dict()["data"]["run"]
    projector = _projector(_FakeTransport(), team_runs=[project_run_snapshot(run)])
    await projector.hydrate("chat-0001")
    frame = projector._transport.payloads[0]  # pyright: ignore[reportUnknownMemberType]
    assert frame["event"] == "team_snapshot" and frame["type"] == "team_snapshot"
    assert frame["turn_id"] == "turn-0001"
    assert frame["data"]["runId"] == "run-0001"
    assert frame["data"]["lastSeq"] == run["lastSeq"]


def test_snapshot_projection_strips_unlisted_run_fields():
    run = _fixture_event("run.finished").to_dict()["data"]["run"]
    leaky = {**run, "secret": "本机路径与凭据"}
    assert "secret" not in project_run_snapshot(leaky)
    assert "instruction" not in json.dumps(project_run_snapshot(leaky), ensure_ascii=False)
