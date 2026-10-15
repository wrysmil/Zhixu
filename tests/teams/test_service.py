"""TeamService 门面测试：引用校验、修订盖章、复制/归档与分页（plan Task 2）。

服务层是 gateway 注入口面，负责「保存前把引用校验干净」，仓储层只负责落盘原子性。
档案来源用内存替身，不读真实 AgentStore、不碰凭据。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from nanobot.agents.models import AgentSelection
from nanobot.teams.database import TeamDatabase
from nanobot.teams.models import TeamDefinition, TeamValidationError
from nanobot.teams.repository import TeamRepository, TeamRevisionConflict
from nanobot.teams.service import (
    ProfileRecord,
    TeamArchived,
    TeamDisabled,
    TeamProfileUnavailable,
    TeamReferenceError,
    TeamService,
)


def _selection(value: Any) -> AgentSelection:
    """teams 域的 CapabilitySelection → 档案域的 AgentSelection（形状完全一致）。"""
    return AgentSelection(mode=value.mode, entries=list(value.entries))


class FakeProfileDirectory:
    """按 id 提供档案约束、可见性与 subAgents 选择的内存替身。"""

    def __init__(self, records: dict[str, ProfileRecord]) -> None:
        self.records = records

    def get(self, agent_id: str) -> ProfileRecord | None:
        return self.records.get(agent_id)

    def change(self, agent_id: str, **fields: Any) -> None:
        self.records[agent_id] = replace(self.records[agent_id], **fields)

    def remove(self, agent_id: str) -> None:
        self.records.pop(agent_id, None)


@pytest.fixture
def profiles(fake_profiles: Any) -> FakeProfileDirectory:
    """把 snapshot fixture 里的档案换成服务可用的目录项。"""
    records: dict[str, ProfileRecord] = {}
    for agent_id, snapshot in fake_profiles.profiles.items():
        records[agent_id] = ProfileRecord(
            id=agent_id,
            name=snapshot.name,
            prompt=snapshot.prompt,
            model_id=snapshot.model_id,
            tools=_selection(snapshot.tools),
            skills=_selection(snapshot.skills),
            sub_agents=_selection(snapshot.sub_agents),
            hidden=False,
            updated_at=snapshot.updated_at,
        )
    return FakeProfileDirectory(records)


@pytest.fixture
def service(team_db: TeamDatabase, profiles: FakeProfileDirectory) -> TeamService:
    return TeamService(TeamRepository(team_db), profiles)  # type: ignore[arg-type]


def create_payload(team_payload: dict[str, Any]) -> dict[str, Any]:
    """创建路径不带服务端字段（契约 §1：revision/createdAt/updatedAt 归服务端）。"""
    payload = dict(team_payload)
    for key in ("revision", "createdAt", "updatedAt"):
        payload.pop(key, None)
    return payload


# ---------------------------------------------------------------- 引用校验


def test_unknown_leader_is_rejected_and_nothing_persisted(
    service: TeamService, team_payload: dict[str, Any]
) -> None:
    payload = {**create_payload(team_payload), "leaderAgentId": "ghost"}
    with pytest.raises(TeamProfileUnavailable, match="ghost"):
        service.save_team(payload, expected_revision=None, request_id="r1")
    assert service.list_teams(limit=20)["teams"] == []


def test_unknown_member_is_rejected_and_nothing_persisted(
    service: TeamService, team_payload: dict[str, Any]
) -> None:
    payload = create_payload(team_payload)
    payload["members"][1]["agentId"] = "ghost-writer"
    with pytest.raises(TeamProfileUnavailable, match="ghost-writer"):
        service.save_team(payload, expected_revision=None, request_id="r1")
    assert service.list_teams(limit=20)["teams"] == []


def test_member_excluded_by_leader_profile_is_rejected(
    service: TeamService, team_payload: dict[str, Any], profiles: FakeProfileDirectory
) -> None:
    """Leader 档案的 subAgents 是黑名单时，被排除的成员不能进团队（契约 §1）。"""
    profiles.change("leader", sub_agents=AgentSelection(mode="exclude", entries=["researcher"]))
    with pytest.raises(TeamReferenceError, match="researcher"):
        service.save_team(
            create_payload(team_payload), expected_revision=None, request_id="r1"
        )
    assert service.list_teams(limit=20)["teams"] == []


def test_member_not_in_leader_include_whitelist_is_rejected(
    service: TeamService, team_payload: dict[str, Any], profiles: FakeProfileDirectory
) -> None:
    profiles.change("leader", sub_agents=AgentSelection(mode="include", entries=["writer"]))
    with pytest.raises(TeamReferenceError, match="researcher"):
        service.save_team(
            create_payload(team_payload), expected_revision=None, request_id="r1"
        )


def test_hidden_profile_cannot_be_newly_selected(
    service: TeamService, team_payload: dict[str, Any], profiles: FakeProfileDirectory
) -> None:
    profiles.change("researcher", hidden=True)
    with pytest.raises(TeamProfileUnavailable, match="researcher"):
        service.save_team(
            create_payload(team_payload), expected_revision=None, request_id="r1"
        )


# ------------------------------------------------------------ 修订盖章与生命周期


def test_save_stamps_server_owned_fields(service: TeamService, team_payload: dict[str, Any]) -> None:
    result = service.save_team(create_payload(team_payload), expected_revision=None, request_id="r1")
    definition = TeamDefinition.from_dict(result["team"])
    assert definition.revision == 1
    assert definition.created_at.endswith(("Z", "+00:00"))
    assert definition.updated_at == definition.created_at


def test_save_rejects_client_forged_revision(service: TeamService, team_payload: dict[str, Any]) -> None:
    with pytest.raises(TeamValidationError, match="server-owned"):
        service.save_team(
            {**create_payload(team_payload), "revision": 99},
            expected_revision=None,
            request_id="r1",
        )


def test_edit_requires_matching_expected_revision(
    service: TeamService, team_payload: dict[str, Any]
) -> None:
    saved = service.save_team(create_payload(team_payload), expected_revision=None, request_id="r1")
    definition = TeamDefinition.from_dict(saved["team"])
    payload = {**definition.to_dict(), "name": "改名"}
    for key in ("revision", "createdAt", "updatedAt"):
        payload.pop(key, None)
    with pytest.raises(TeamRevisionConflict):
        service.save_team(payload, expected_revision=7, request_id="r2")
    second = service.save_team(payload, expected_revision=1, request_id="r3")
    assert TeamDefinition.from_dict(second["team"]).revision == 2


def test_archive_is_idempotent(service: TeamService, team_payload: dict[str, Any]) -> None:
    saved = service.save_team(create_payload(team_payload), expected_revision=None, request_id="r1")
    team_id = saved["team"]["id"]
    first = service.archive_team(team_id, expected_revision=1, request_id="r2")
    assert first["archived"] is True
    again = service.archive_team(team_id, expected_revision=2, request_id="r3")
    assert again["archived"] is True
    assert again["revision"] == 2


def test_archived_team_blocks_new_runs_but_keeps_history_readable(
    service: TeamService, team_payload: dict[str, Any]
) -> None:
    saved = service.save_team(create_payload(team_payload), expected_revision=None, request_id="r1")
    team_id = saved["team"]["id"]
    service.archive_team(team_id, expected_revision=1, request_id="r2")
    with pytest.raises(TeamArchived):
        service.prepare_new_run(team_id)
    assert service.get_team_detail(team_id)["team"]["archived"] is True


def test_clone_starts_a_fresh_revision_with_a_new_id(
    service: TeamService, team_payload: dict[str, Any]
) -> None:
    saved = service.save_team(create_payload(team_payload), expected_revision=None, request_id="r1")
    source_id = saved["team"]["id"]
    clone = service.clone_team(source_id, name="内容团队副本", request_id="r2")
    definition = TeamDefinition.from_dict(clone["team"])
    assert definition.id != source_id
    assert definition.revision == 1
    assert definition.name == "内容团队副本"
    assert [member.agent_id for member in definition.members] == ["researcher", "writer"]


# ------------------------------------------------------------------ 分页与引用


def test_list_teams_pagination_is_stable_and_bounded(
    service: TeamService, team_payload: dict[str, Any]
) -> None:
    for index in range(3):
        payload = {**create_payload(team_payload), "id": f"team-{index}", "name": f"团队{index}"}
        service.save_team(payload, expected_revision=None, request_id=f"r{index}")
    page_one = service.list_teams(limit=2)
    assert len(page_one["teams"]) == 2
    assert page_one["nextCursor"]
    page_two = service.list_teams(limit=2, cursor=page_one["nextCursor"])
    assert len(page_two["teams"]) == 1
    assert page_two["nextCursor"] is None
    ids = {item["id"] for item in page_one["teams"] + page_two["teams"]}
    assert ids == {"team-0", "team-1", "team-2"}


def test_limit_out_of_range_is_rejected(service: TeamService) -> None:
    with pytest.raises(TeamValidationError):
        service.list_teams(limit=0)
    with pytest.raises(TeamValidationError):
        service.list_teams(limit=101)


def test_deleting_a_referenced_agent_is_blocked_until_archived(
    service: TeamService, team_payload: dict[str, Any]
) -> None:
    """引用保护只作用于未归档团队；归档团队与历史快照不绑住删除（契约 §3）。"""
    saved = service.save_team(create_payload(team_payload), expected_revision=None, request_id="r1")
    team_id = saved["team"]["id"]
    assert service.teams_referencing_profile("researcher") == [team_id]
    service.archive_team(team_id, expected_revision=1, request_id="r2")
    assert service.teams_referencing_profile("researcher") == []


# -------------------------------------------------------------------- 开关


def test_disabled_teams_blocks_new_runs_but_keeps_reads(
    team_db: TeamDatabase, profiles: FakeProfileDirectory, team_payload: dict[str, Any]
) -> None:
    enabled = TeamService(TeamRepository(team_db), profiles)  # type: ignore[arg-type]
    saved = enabled.save_team(
        create_payload(team_payload), expected_revision=None, request_id="r1"
    )
    disabled = TeamService(TeamRepository(team_db), profiles, enabled=False)  # type: ignore[arg-type]
    with pytest.raises(TeamDisabled):
        disabled.prepare_new_run(saved["team"]["id"])
    assert disabled.list_teams(limit=20)["teams"] != []


# ------------------------------------------------------------------ 异步入口


async def test_async_entries_match_their_sync_counterparts(
    team_db: TeamDatabase, profiles: FakeProfileDirectory, team_payload: dict[str, Any]
) -> None:
    """gateway 用短 ``to_thread`` 入口，结果与同步口径一致（plan Task 2 Step 3）。"""
    service = TeamService(TeamRepository(team_db), profiles)  # type: ignore[arg-type]
    payload = create_payload(team_payload)
    saved = await service.a_save_team(payload, expected_revision=None, request_id="r1")
    direct = service.save_team(payload, expected_revision=None, request_id="r1")
    assert saved == direct
    listed = await service.a_list_teams(limit=20)
    assert listed == service.list_teams(limit=20)
    assert (await service.a_get_team_detail(payload["id"]))["team"]["id"] == payload["id"]
