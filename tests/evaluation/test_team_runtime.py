"""T04 隔离团队运行时。

底线：

- 团队必须走隔离 workspace：团队库、成员档案都落在 attempt workspace，
  不碰用户日常团队库；
- 冻结之后源团队被改也不影响本次运行；
- 全部轮次都要执行，不能只跑最后一轮；
- 缺 Trace 不能靠开关说成"证据完整"。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nanobot.evaluation.team_runtime import (
    IsolatedTeamRuntime,
    TeamRunRequest,
    TeamRuntimeError,
    build_frozen_team,
)

TIMESTAMP = "2026-01-01T00:00:00+00:00"


def _profile(agent_id: str, name: str) -> dict[str, Any]:
    return {
        "id": agent_id,
        "updatedAt": TIMESTAMP,
        "prompt": f"{name} 的提示词",
        "name": name,
        "modelId": None,
        "tools": {"mode": "all", "entries": []},
        "skills": {"mode": "include", "entries": []},
        "subAgents": {"mode": "all", "entries": []},
    }


def _team_snapshot(team_id: str = "creative") -> dict[str, Any]:
    return {
        "kind": "team",
        "id": team_id,
        "team": {
            "revision": 3,
            "definition": {
                "id": team_id,
                "revision": 3,
                "name": "创意组",
                "description": "",
                "instructions": "",
                "leaderAgentId": "leader",
                "members": [
                    {"memberId": "writer", "agentId": "writer", "role": "写作"},
                ],
                "limits": {},
                "archived": False,
                "createdAt": TIMESTAMP,
                "updatedAt": TIMESTAMP,
            },
            "leader": _profile("leader", "领队"),
            "members": [
                {
                    "member": {"memberId": "writer", "agentId": "writer", "role": "写作"},
                    "profile": _profile("writer", "写手"),
                }
            ],
        },
    }


def test_frozen_team_parses_revision_leader_and_members() -> None:
    team = build_frozen_team(_team_snapshot())
    assert team.team_id == "creative"
    assert team.revision == 3
    assert team.member_ids() == ("writer",)
    assert team.capability()["leader_agent_id"] == "leader"


def test_missing_leader_is_rejected() -> None:
    snapshot = _team_snapshot()
    snapshot["team"]["leader"] = {}
    with pytest.raises(TeamRuntimeError) as excinfo:
        build_frozen_team(snapshot)
    assert excinfo.value.code == "TEAM_LEADER_MISSING"


def test_invalid_revision_is_rejected() -> None:
    snapshot = _team_snapshot()
    snapshot["team"]["definition"]["revision"] = 0
    with pytest.raises(TeamRuntimeError) as excinfo:
        build_frozen_team(snapshot)
    assert excinfo.value.code == "TEAM_REVISION_INVALID"


def test_member_without_agent_is_rejected() -> None:
    snapshot = _team_snapshot()
    snapshot["team"]["members"][0]["member"] = {"memberId": "writer"}
    with pytest.raises(TeamRuntimeError) as excinfo:
        build_frozen_team(snapshot)
    assert excinfo.value.code == "TEAM_MEMBER_INVALID"


async def test_all_turns_are_executed_and_dispatched(tmp_path: Path) -> None:
    """两轮输入都要跑：只跑最后一轮就是团队丢上下文。"""
    seen: list[tuple[int, str]] = []

    async def turn_runner(*, turn_index: int, prompt: str, request: TeamRunRequest) -> str:
        seen.append((turn_index, prompt))
        return f"第{turn_index}轮结论"

    events: list[tuple[str, dict[str, Any]]] = []

    class Sink:
        def emit(self, event_type: str, *, payload: dict[str, Any] | None = None, **_: Any) -> None:
            events.append((event_type, dict(payload or {})))

    runtime = IsolatedTeamRuntime(
        request=TeamRunRequest(
            attempt_id="00000000-0000-4000-8000-000000000001",
            team=build_frozen_team(_team_snapshot()),
            turns=("口令是青松", "只回复刚才的口令"),
            workspace=tmp_path / "attempt",
        ),
        turn_runner=turn_runner,
        events=Sink(),
    )
    outcome = await runtime.execute()

    assert outcome.status == "succeeded"
    assert seen == [(1, "口令是青松"), (2, "只回复刚才的口令")]
    assert [item[0] for item in events] == ["team.dispatch", "team.dispatch"]
    assert events[0][1]["turn"] == 1
    assert events[0][1]["capability"]["revision"] == 3


async def test_team_is_materialized_inside_attempt_workspace(tmp_path: Path) -> None:
    """隔离的硬边界：团队库与成员档案只能落在 attempt workspace。"""
    workspace = tmp_path / "attempt"

    async def turn_runner(*, turn_index: int, prompt: str, request: TeamRunRequest) -> str:
        return "结论"

    runtime = IsolatedTeamRuntime(
        request=TeamRunRequest(
            attempt_id="00000000-0000-4000-8000-000000000001",
            team=build_frozen_team(_team_snapshot()),
            turns=("跑一下",),
            workspace=workspace,
        ),
        turn_runner=turn_runner,
    )
    outcome = await runtime.execute()
    assert outcome.status == "succeeded"

    assert (workspace / "teams" / "state.db").is_file()
    assert not (tmp_path / "teams" / "state.db").exists()


async def test_turn_failure_is_not_reported_as_success(tmp_path: Path) -> None:
    async def turn_runner(*, turn_index: int, prompt: str, request: TeamRunRequest) -> str:
        raise RuntimeError("成员执行炸了")

    runtime = IsolatedTeamRuntime(
        request=TeamRunRequest(
            attempt_id="00000000-0000-4000-8000-000000000001",
            team=build_frozen_team(_team_snapshot()),
            turns=("跑一下",),
            workspace=tmp_path / "attempt",
        ),
        turn_runner=turn_runner,
    )
    outcome = await runtime.execute()
    assert outcome.status == "failed"
    assert outcome.errors[0]["code"] == "TEAM_TURN_FAILED"
