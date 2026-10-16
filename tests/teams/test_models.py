"""Agent Team 领域类型的边界校验与线格式往返测试（契约 §1/§5/§6）。

口径只有一条：**拒绝，不宽容转换**。未知枚举、把 ``bool`` 当 ``int``、越界 limits、
缺字段、字段类型错、重复成员、Leader 兼成员、非 UTC 时间戳、创建路径伪造
``revision/createdAt/updatedAt`` 都必须抛 :class:`TeamValidationError`，且消息里带上
可定位的字段名。fixture 与 dataclass ``to_dict()`` 必须逐字段一致——它是 WU-02/03/08
并行开工的共同锚点。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from nanobot.config.loader import load_config
from nanobot.config.paths import get_teams_dir
from nanobot.config.schema import Config
from nanobot.teams.models import (
    MAX_LAST_ACTION_CHARS,
    MAX_MEMBER_ROLE_CHARS,
    MAX_TASK_ACCEPTANCE_CHARS,
    MAX_TASK_INSTRUCTION_CHARS,
    MAX_TASK_RESULT_CHARS,
    MAX_TASK_SUMMARY_CHARS,
    MAX_TASK_TITLE_CHARS,
    MAX_TEAM_INSTRUCTIONS_CHARS,
    MAX_TEAM_MEMBERS,
    MAX_TEAM_NAME_CHARS,
    NON_TERMINAL_RUN_STATUSES,
    TASK_STATUSES,
    TEAM_EVENT_TYPES,
    TEAM_RUN_STATUSES,
    AgentProfileSnapshot,
    CapabilitySelection,
    LeaderUpdatedData,
    RunCancellingData,
    RunFinishedData,
    RunStartedData,
    TaskEventData,
    TeamBinding,
    TeamDefinition,
    TeamEvent,
    TeamLimits,
    TeamMember,
    TeamRevisionMember,
    TeamRevisionSnapshot,
    TeamRunSnapshot,
    TeamTaskInput,
    TeamTaskResult,
    TeamTaskSnapshot,
    TeamTaskUsage,
    TeamValidationError,
    check_full_result_length,
)

STAMP = "2026-10-15T08:00:00+00:00"
LATER_STAMP = "2026-10-16T09:30:00+00:00"


def _task_payload(run_snapshot_payload: dict[str, Any]) -> dict[str, Any]:
    return run_snapshot_payload["tasks"][0]


# ---------------------------------------------------------------- TeamDefinition


def test_definition_fixture_round_trips(team_payload: dict[str, Any]) -> None:
    definition = TeamDefinition.from_dict(team_payload)
    assert definition.to_dict() == team_payload
    assert TeamDefinition.from_dict(definition.to_dict()) == definition


def test_definition_to_dict_emits_contract_fields(team_definition: TeamDefinition) -> None:
    assert set(team_definition.to_dict()) == {
        "id",
        "revision",
        "name",
        "description",
        "instructions",
        "leaderAgentId",
        "members",
        "limits",
        "archived",
        "createdAt",
        "updatedAt",
    }
    assert team_definition.revision == 1
    assert team_definition.leader_agent_id == "leader"
    assert [member.agent_id for member in team_definition.members] == ["researcher", "writer"]


def test_definition_defaults_limits_when_section_absent(team_payload: dict[str, Any]) -> None:
    del team_payload["limits"]
    definition = TeamDefinition.from_dict(team_payload)
    assert definition.limits == TeamLimits()
    assert definition.limits.max_concurrency == 3
    assert definition.limits.max_tasks == 16
    assert definition.limits.max_dispatch_rounds == 4
    assert definition.limits.task_timeout_seconds == 600
    assert definition.limits.run_timeout_seconds == 1800


def test_duplicate_agent_is_rejected(team_payload: dict[str, Any]) -> None:
    team_payload["members"][1]["agentId"] = team_payload["members"][0]["agentId"]
    with pytest.raises(TeamValidationError, match="duplicate"):
        TeamDefinition.from_dict(team_payload)


def test_duplicate_member_id_is_rejected(team_payload: dict[str, Any]) -> None:
    team_payload["members"][1]["memberId"] = team_payload["members"][0]["memberId"]
    with pytest.raises(TeamValidationError, match="duplicate"):
        TeamDefinition.from_dict(team_payload)


def test_leader_agent_cannot_also_be_a_member(team_payload: dict[str, Any]) -> None:
    team_payload["members"][0]["agentId"] = team_payload["leaderAgentId"]
    with pytest.raises(TeamValidationError, match="leader"):
        TeamDefinition.from_dict(team_payload)


def test_leader_agent_id_must_not_be_empty(team_payload: dict[str, Any]) -> None:
    team_payload["leaderAgentId"] = "  "
    with pytest.raises(TeamValidationError, match="leaderAgentId"):
        TeamDefinition.from_dict(team_payload)


@pytest.mark.parametrize("name", ["", "   ", "x" * (MAX_TEAM_NAME_CHARS + 1)])
def test_definition_rejects_invalid_name(team_payload: dict[str, Any], name: str) -> None:
    team_payload["name"] = name
    with pytest.raises(TeamValidationError, match="name"):
        TeamDefinition.from_dict(team_payload)


def test_definition_accepts_name_at_max_length(team_payload: dict[str, Any]) -> None:
    team_payload["name"] = "x" * MAX_TEAM_NAME_CHARS
    assert TeamDefinition.from_dict(team_payload).name == "x" * MAX_TEAM_NAME_CHARS


def test_definition_rejects_oversized_instructions(team_payload: dict[str, Any]) -> None:
    team_payload["instructions"] = "x" * (MAX_TEAM_INSTRUCTIONS_CHARS + 1)
    with pytest.raises(TeamValidationError, match="instructions"):
        TeamDefinition.from_dict(team_payload)


def test_definition_accepts_instructions_at_max_length(team_payload: dict[str, Any]) -> None:
    team_payload["instructions"] = "x" * MAX_TEAM_INSTRUCTIONS_CHARS
    assert TeamDefinition.from_dict(team_payload).instructions == "x" * MAX_TEAM_INSTRUCTIONS_CHARS


def test_definition_rejects_empty_members(team_payload: dict[str, Any]) -> None:
    team_payload["members"] = []
    with pytest.raises(TeamValidationError, match="members"):
        TeamDefinition.from_dict(team_payload)


def test_definition_rejects_members_above_max(team_payload: dict[str, Any]) -> None:
    team_payload["members"] = [
        {
            "memberId": f"m-{index}",
            "agentId": f"agent-{index}",
            "role": f"职责 {index}",
        }
        for index in range(MAX_TEAM_MEMBERS + 1)
    ]
    with pytest.raises(TeamValidationError, match="members"):
        TeamDefinition.from_dict(team_payload)


@pytest.mark.parametrize("count", [1, MAX_TEAM_MEMBERS])
def test_definition_accepts_member_count_at_boundaries(
    team_payload: dict[str, Any], count: int
) -> None:
    team_payload["members"] = [
        {"memberId": f"m-{index}", "agentId": f"agent-{index}", "role": "执行"}
        for index in range(count)
    ]
    assert len(TeamDefinition.from_dict(team_payload).members) == count


@pytest.mark.parametrize(
    "key",
    ["id", "revision", "name", "leaderAgentId", "members", "createdAt", "updatedAt"],
)
def test_definition_rejects_missing_required_field(team_payload: dict[str, Any], key: str) -> None:
    del team_payload[key]
    with pytest.raises(TeamValidationError, match=key):
        TeamDefinition.from_dict(team_payload)


def test_definition_rejects_unknown_field(team_payload: dict[str, Any]) -> None:
    team_payload["maxConcurrency"] = 8
    with pytest.raises(TeamValidationError, match="maxConcurrency"):
        TeamDefinition.from_dict(team_payload)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("id", 42),
        ("name", ["内容团队"]),
        ("description", None),
        ("instructions", 8000),
        ("leaderAgentId", True),
        ("members", {"memberId": "m-1"}),
        ("archived", "false"),
        ("archived", 0),
        ("revision", "1"),
        ("revision", 1.0),
        ("createdAt", 1760000000),
    ],
)
def test_definition_rejects_wrong_field_type(
    team_payload: dict[str, Any], key: str, value: Any
) -> None:
    team_payload[key] = value
    with pytest.raises(TeamValidationError, match=key):
        TeamDefinition.from_dict(team_payload)


@pytest.mark.parametrize("revision", [0, -1, True, False])
def test_definition_rejects_revision_below_one(
    team_payload: dict[str, Any], revision: Any
) -> None:
    team_payload["revision"] = revision
    with pytest.raises(TeamValidationError, match="revision"):
        TeamDefinition.from_dict(team_payload)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-15T08:00:00",
        "2026-10-15T08:00:00+08:00",
        "2026-10-15 08:00:00+00:00",
        "2026-10-15",
        "yesterday",
    ],
)
def test_definition_rejects_non_utc_timestamp(team_payload: dict[str, Any], timestamp: str) -> None:
    team_payload["updatedAt"] = timestamp
    with pytest.raises(TeamValidationError, match="updatedAt"):
        TeamDefinition.from_dict(team_payload)


def test_definition_accepts_z_suffix_utc_timestamp(team_payload: dict[str, Any]) -> None:
    team_payload["updatedAt"] = "2026-10-16T09:30:00Z"
    assert TeamDefinition.from_dict(team_payload).updated_at == "2026-10-16T09:30:00Z"


def test_member_rejects_oversized_role(team_payload: dict[str, Any]) -> None:
    team_payload["members"][0]["role"] = "x" * (MAX_MEMBER_ROLE_CHARS + 1)
    with pytest.raises(TeamValidationError, match="role"):
        TeamDefinition.from_dict(team_payload)


def test_member_rejects_missing_key(team_payload: dict[str, Any]) -> None:
    del team_payload["members"][0]["agentId"]
    with pytest.raises(TeamValidationError, match="agentId"):
        TeamDefinition.from_dict(team_payload)


def test_member_rejects_unknown_field(team_payload: dict[str, Any]) -> None:
    team_payload["members"][0]["agent_id"] = "researcher"
    with pytest.raises(TeamValidationError, match="agent_id"):
        TeamDefinition.from_dict(team_payload)


def test_member_round_trips() -> None:
    member = TeamMember(member_id="m-1", agent_id="researcher", role="查阅资料")
    assert TeamMember.from_dict(member.to_dict()) == member
    assert member.to_dict() == {"memberId": "m-1", "agentId": "researcher", "role": "查阅资料"}


# ----------------------------------------------------------- 创建路径所有权字段


def _create_payload(team_payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("revision", "createdAt", "updatedAt"):
        team_payload.pop(key, None)
    return team_payload


def test_create_payload_stamps_server_fields(team_payload: dict[str, Any]) -> None:
    definition = TeamDefinition.from_create_payload(
        _create_payload(team_payload), now=STAMP
    )
    assert definition.revision == 1
    assert definition.created_at == STAMP
    assert definition.updated_at == STAMP
    assert definition.archived is False


@pytest.mark.parametrize("forged", ["revision", "createdAt", "updatedAt"])
def test_create_payload_rejects_forged_server_fields(
    team_payload: dict[str, Any], forged: str
) -> None:
    payload = _create_payload(team_payload)
    payload[forged] = 9 if forged == "revision" else LATER_STAMP
    with pytest.raises(TeamValidationError, match=forged):
        TeamDefinition.from_create_payload(payload, now=STAMP)


# -------------------------------------------------------------------- TeamLimits


def test_limits_round_trips_at_boundaries() -> None:
    limits = TeamLimits(
        max_concurrency=8,
        max_tasks=64,
        max_dispatch_rounds=8,
        task_timeout_seconds=1800,
        run_timeout_seconds=7200,
    )
    assert limits.to_dict() == {
        "maxConcurrency": 8,
        "maxTasks": 64,
        "maxDispatchRounds": 8,
        "taskTimeoutSeconds": 1800,
        "runTimeoutSeconds": 7200,
    }
    assert TeamLimits.from_dict(limits.to_dict()) == limits


def test_limits_accepts_minimum_values() -> None:
    limits = TeamLimits(
        max_concurrency=1,
        max_tasks=1,
        max_dispatch_rounds=1,
        task_timeout_seconds=30,
        run_timeout_seconds=60,
    )
    assert TeamLimits.from_dict(limits.to_dict()) == limits


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("maxConcurrency", 0),
        ("maxConcurrency", 9),
        ("maxTasks", 0),
        ("maxTasks", 65),
        ("maxDispatchRounds", 0),
        ("maxDispatchRounds", 9),
        ("taskTimeoutSeconds", 29),
        ("taskTimeoutSeconds", 1801),
        ("runTimeoutSeconds", 59),
        ("runTimeoutSeconds", 7201),
    ],
)
def test_limits_reject_out_of_range(key: str, value: int) -> None:
    with pytest.raises(TeamValidationError, match=key):
        TeamLimits.from_dict({"maxConcurrency": 3, "maxTasks": 16, key: value})


@pytest.mark.parametrize(
    "key",
    [
        "maxConcurrency",
        "maxTasks",
        "maxDispatchRounds",
        "taskTimeoutSeconds",
        "runTimeoutSeconds",
    ],
)
def test_limits_reject_bool_as_int(key: str) -> None:
    with pytest.raises(TeamValidationError, match=key):
        TeamLimits.from_dict({key: True})


@pytest.mark.parametrize("value", [3.5, "3", None, [3]])
def test_limits_reject_non_integer(value: Any) -> None:
    with pytest.raises(TeamValidationError, match="maxConcurrency"):
        TeamLimits.from_dict({"maxConcurrency": value})


def test_limits_rejects_unknown_key() -> None:
    with pytest.raises(TeamValidationError, match="maxParallel"):
        TeamLimits.from_dict({"maxParallel": 2})


# ------------------------------------------------------------------- TeamBinding


def test_binding_round_trips() -> None:
    binding = TeamBinding(team_id="content-team", team_revision=2, team_name="内容团队")
    assert binding.to_dict() == {"teamId": "content-team", "teamRevision": 2, "teamName": "内容团队"}
    assert TeamBinding.from_dict(binding.to_dict()) == binding


@pytest.mark.parametrize("revision", [0, -1, True, "2", None])
def test_binding_rejects_invalid_revision(revision: Any) -> None:
    with pytest.raises(TeamValidationError, match="teamRevision"):
        TeamBinding.from_dict({"teamId": "content-team", "teamRevision": revision, "teamName": "n"})


# ------------------------------------------------- AgentProfileSnapshot 与选择器


def test_profile_snapshot_fixture_round_trips(revision_snapshot_payload: dict[str, Any]) -> None:
    leader = revision_snapshot_payload["leader"]
    profile = AgentProfileSnapshot.from_dict(leader)
    assert profile.to_dict() == leader
    assert AgentProfileSnapshot.from_dict(profile.to_dict()) == profile


def test_profile_snapshot_model_id_is_a_configured_preset(
    revision_snapshot_payload: dict[str, Any]
) -> None:
    leader = AgentProfileSnapshot.from_dict(revision_snapshot_payload["leader"])
    member = AgentProfileSnapshot.from_dict(revision_snapshot_payload["members"][0]["profile"])
    assert leader.model_id == "opus-high"
    assert member.model_id is None


@pytest.mark.parametrize("model_id", ["", "  ", 3, ["opus-high"]])
def test_profile_snapshot_rejects_invalid_model_id(
    revision_snapshot_payload: dict[str, Any], model_id: Any
) -> None:
    leader = dict(revision_snapshot_payload["leader"])
    leader["modelId"] = model_id
    with pytest.raises(TeamValidationError, match="modelId"):
        AgentProfileSnapshot.from_dict(leader)


@pytest.mark.parametrize("mode", ["", "ALL", "only", None, 3])
def test_selection_rejects_unknown_mode(mode: Any) -> None:
    with pytest.raises(TeamValidationError, match="mode"):
        CapabilitySelection.from_dict({"mode": mode, "entries": []})


def test_selection_rejects_duplicate_entries() -> None:
    with pytest.raises(TeamValidationError, match="duplicate"):
        CapabilitySelection.from_dict({"mode": "include", "entries": ["a", "a"]})


def test_selection_rejects_empty_entry() -> None:
    with pytest.raises(TeamValidationError, match="entries"):
        CapabilitySelection.from_dict({"mode": "include", "entries": [""]})


def test_selection_rejects_non_string_entries() -> None:
    with pytest.raises(TeamValidationError, match="entries"):
        CapabilitySelection.from_dict({"mode": "all", "entries": [1]})


def test_selection_defaults_to_all() -> None:
    assert CapabilitySelection.from_dict(None) == CapabilitySelection(mode="all", entries=[])


def test_profile_snapshot_rejects_unknown_field(revision_snapshot_payload: dict[str, Any]) -> None:
    leader = dict(revision_snapshot_payload["leader"])
    leader["apiKeys"] = {}
    with pytest.raises(TeamValidationError, match="apiKeys"):
        AgentProfileSnapshot.from_dict(leader)


# ----------------------------------------------------------- TeamRevisionSnapshot


def test_revision_snapshot_fixture_matches_to_dict(
    revision_snapshot_payload: dict[str, Any], team_payload: dict[str, Any]
) -> None:
    snapshot = TeamRevisionSnapshot.from_dict(revision_snapshot_payload)
    assert snapshot.to_dict() == revision_snapshot_payload
    assert snapshot.definition.to_dict() == team_payload
    assert TeamRevisionSnapshot.from_dict(snapshot.to_dict()) == snapshot


def test_revision_snapshot_built_by_fake_provider(
    team_definition: TeamDefinition,
    fake_profiles: Any,
    revision_snapshot_payload: dict[str, Any],
) -> None:
    assert fake_profiles.revision_snapshot(team_definition).to_dict() == revision_snapshot_payload


def test_revision_snapshot_rejects_leader_profile_mismatch(
    revision_snapshot_payload: dict[str, Any]
) -> None:
    revision_snapshot_payload["leader"]["id"] = "other-leader"
    with pytest.raises(TeamValidationError, match="leader"):
        TeamRevisionSnapshot.from_dict(revision_snapshot_payload)


def test_revision_snapshot_rejects_member_profile_mismatch(
    revision_snapshot_payload: dict[str, Any]
) -> None:
    revision_snapshot_payload["members"][1]["profile"]["id"] = "ghost"
    with pytest.raises(TeamValidationError, match="agentId"):
        TeamRevisionSnapshot.from_dict(revision_snapshot_payload)


def test_revision_snapshot_rejects_member_order_mismatch(
    revision_snapshot_payload: dict[str, Any]
) -> None:
    revision_snapshot_payload["members"].reverse()
    with pytest.raises(TeamValidationError, match="members"):
        TeamRevisionSnapshot.from_dict(revision_snapshot_payload)


def test_revision_member_round_trips(team_definition: TeamDefinition) -> None:
    member = team_definition.members[0]
    profile = AgentProfileSnapshot(
        id=member.agent_id,
        updated_at=STAMP,
        prompt="p",
        name="研究员",
        model_id=None,
        tools=CapabilitySelection(mode="all", entries=[]),
        skills=CapabilitySelection(mode="all", entries=[]),
        sub_agents=CapabilitySelection(mode="all", entries=[]),
    )
    entry = TeamRevisionMember(member=member, profile=profile)
    assert TeamRevisionMember.from_dict(entry.to_dict()) == entry


def test_revision_member_rejects_raw_dict_profile(team_definition: TeamDefinition) -> None:
    with pytest.raises(TeamValidationError, match="profile"):
        TeamRevisionMember(member=team_definition.members[0], profile={"id": "researcher"})


def test_run_snapshot_rejects_raw_dict_binding(run_snapshot_payload: dict[str, Any]) -> None:
    parsed = TeamRunSnapshot.from_dict(run_snapshot_payload)
    with pytest.raises(TeamValidationError, match="binding"):
        TeamRunSnapshot(
            run_id=parsed.run_id,
            chat_id=parsed.chat_id,
            turn_id=parsed.turn_id,
            binding=run_snapshot_payload["binding"],
            status=parsed.status,
            last_seq=parsed.last_seq,
            tasks=parsed.tasks,
            leader_action=parsed.leader_action,
            started_at=parsed.started_at,
            finished_at=parsed.finished_at,
        )


def test_event_rejects_raw_dict_data(event_payloads: list[dict[str, Any]]) -> None:
    raw = next(item for item in event_payloads if item["type"] == "task.started")
    event = TeamEvent.from_dict(raw)
    with pytest.raises(TeamValidationError, match="data"):
        TeamEvent(
            schema_version=event.schema_version,
            run_id=event.run_id,
            chat_id=event.chat_id,
            turn_id=event.turn_id,
            seq=event.seq,
            type="run.finished",
            occurred_at=event.occurred_at,
            data=raw["data"],
        )


# ------------------------------------------------------------------ 任务输入/结果


def test_task_input_round_trips() -> None:
    payload = {
        "key": "research",
        "memberId": "m-researcher",
        "title": "查阅资料",
        "instruction": "检索项目内文档并给出来源。",
        "acceptance": "列出至少 3 条可点击来源。",
        "dependsOn": [],
        "resourceKeys": ["file:docs/research.md"],
    }
    assert TeamTaskInput.from_dict(payload).to_dict() == payload


def test_task_input_defaults_optional_lists() -> None:
    task = TeamTaskInput.from_dict(
        {
            "key": "research",
            "memberId": "m-researcher",
            "title": "查阅资料",
            "instruction": "检索文档",
            "acceptance": "给出来源",
        }
    )
    assert task.depends_on == []
    assert task.resource_keys == []


@pytest.mark.parametrize("key", ["key", "memberId", "title", "instruction", "acceptance"])
def test_task_input_rejects_missing_field(key: str) -> None:
    payload = {
        "key": "research",
        "memberId": "m-researcher",
        "title": "查阅资料",
        "instruction": "检索文档",
        "acceptance": "给出来源",
    }
    del payload[key]
    with pytest.raises(TeamValidationError, match=key):
        TeamTaskInput.from_dict(payload)


def test_task_input_accepts_fields_at_max_length() -> None:
    payload = {
        "key": "research",
        "memberId": "m-researcher",
        "title": "t",
        "instruction": "i",
        "acceptance": "a",
    }
    payload["title"] = "x" * MAX_TASK_TITLE_CHARS
    assert TeamTaskInput.from_dict(payload).title == "x" * MAX_TASK_TITLE_CHARS
    payload["title"] = "t"
    payload["instruction"] = "x" * MAX_TASK_INSTRUCTION_CHARS
    assert TeamTaskInput.from_dict(payload).instruction == "x" * MAX_TASK_INSTRUCTION_CHARS
    payload["instruction"] = "i"
    payload["acceptance"] = "x" * MAX_TASK_ACCEPTANCE_CHARS
    assert TeamTaskInput.from_dict(payload).acceptance == "x" * MAX_TASK_ACCEPTANCE_CHARS


def test_task_input_rejects_oversized_instruction() -> None:
    payload = {
        "key": "research",
        "memberId": "m-researcher",
        "title": "查阅资料",
        "instruction": "x" * (MAX_TASK_INSTRUCTION_CHARS + 1),
        "acceptance": "给出来源",
    }
    with pytest.raises(TeamValidationError, match="instruction"):
        TeamTaskInput.from_dict(payload)


@pytest.mark.parametrize("key", ["title", "instruction", "acceptance"])
def test_task_input_rejects_empty_text(key: str) -> None:
    payload = {
        "key": "research",
        "memberId": "m-researcher",
        "title": "查阅资料",
        "instruction": "检索文档",
        "acceptance": "给出来源",
    }
    payload[key] = "   "
    with pytest.raises(TeamValidationError, match=key):
        TeamTaskInput.from_dict(payload)


def test_task_input_rejects_non_string_dependency() -> None:
    payload = {
        "key": "write",
        "memberId": "m-writer",
        "title": "成稿",
        "instruction": "写初稿",
        "acceptance": "交付正文",
        "dependsOn": ["research", 2],
    }
    with pytest.raises(TeamValidationError, match="dependsOn"):
        TeamTaskInput.from_dict(payload)


def test_task_input_rejects_duplicate_dependency() -> None:
    payload = {
        "key": "write",
        "memberId": "m-writer",
        "title": "成稿",
        "instruction": "写初稿",
        "acceptance": "交付正文",
        "dependsOn": ["research", "research"],
    }
    with pytest.raises(TeamValidationError, match="dependsOn"):
        TeamTaskInput.from_dict(payload)


def test_task_usage_round_trips() -> None:
    usage = TeamTaskUsage(input_tokens=1200, output_tokens=300)
    assert usage.to_dict() == {"inputTokens": 1200, "outputTokens": 300}
    assert TeamTaskUsage.from_dict(usage.to_dict()) == usage


@pytest.mark.parametrize("value", [True, -1, 1.5, "10"])
def test_task_usage_rejects_non_integer_tokens(value: Any) -> None:
    with pytest.raises(TeamValidationError, match="inputTokens"):
        TeamTaskUsage.from_dict({"inputTokens": value, "outputTokens": 1})


def test_task_result_round_trips(run_snapshot_payload: dict[str, Any]) -> None:
    result = _task_payload(run_snapshot_payload)["result"]
    parsed = TeamTaskResult.from_dict(result)
    assert parsed.status == "succeeded"
    assert parsed.usage is not None and parsed.usage.input_tokens == 1200
    assert parsed.to_dict() == result


@pytest.mark.parametrize("status", list(TASK_STATUSES) + ["done", "SUCCESS"])
def test_task_result_accepts_only_contract_statuses(status: str) -> None:
    payload = _result_payload()
    payload["status"] = status
    if status in TASK_STATUSES:
        assert TeamTaskResult.from_dict(payload).status == status
        return
    with pytest.raises(TeamValidationError, match="status"):
        TeamTaskResult.from_dict(payload)


def _result_payload() -> dict[str, Any]:
    return {
        "taskId": "task-0001",
        "attemptId": "attempt-0001",
        "memberId": "m-researcher",
        "agentId": "researcher",
        "status": "succeeded",
        "stopReason": "final_answer",
        "summary": "已整理 5 条来源。",
        "artifacts": ["docs/research/sources.md"],
        "errorCode": None,
        "usage": {"inputTokens": 1200, "outputTokens": 300},
    }


def test_task_result_rejects_oversized_summary() -> None:
    payload = _result_payload()
    payload["summary"] = "x" * (MAX_TASK_SUMMARY_CHARS + 1)
    with pytest.raises(TeamValidationError, match="summary"):
        TeamTaskResult.from_dict(payload)


def test_task_result_rejects_empty_stop_reason() -> None:
    payload = _result_payload()
    payload["stopReason"] = ""
    with pytest.raises(TeamValidationError, match="stopReason"):
        TeamTaskResult.from_dict(payload)


def test_task_result_rejects_error_code_on_success() -> None:
    payload = _result_payload()
    payload["errorCode"] = "MODEL_PRESET_UNAVAILABLE"
    with pytest.raises(TeamValidationError, match="errorCode"):
        TeamTaskResult.from_dict(payload)


@pytest.mark.parametrize(
    "ref", ["/etc/passwd", "../outside.md", "docs/../../x", "~/id_rsa", "C:\\secrets\\a.md"]
)
def test_task_result_rejects_unsafe_artifact_reference(ref: str) -> None:
    payload = _result_payload()
    payload["artifacts"] = [ref]
    with pytest.raises(TeamValidationError, match="artifacts"):
        TeamTaskResult.from_dict(payload)


def test_task_result_accepts_relative_artifact_reference() -> None:
    payload = _result_payload()
    payload["artifacts"] = ["docs/draft.md", "webui/src/App.tsx"]
    assert TeamTaskResult.from_dict(payload).artifacts == ["docs/draft.md", "webui/src/App.tsx"]


def test_task_snapshot_round_trips(run_snapshot_payload: dict[str, Any]) -> None:
    raw = _task_payload(run_snapshot_payload)
    snapshot = TeamTaskSnapshot.from_dict(raw)
    assert snapshot.depends_on == []
    assert snapshot.to_dict() == raw
    assert TeamTaskSnapshot.from_dict(snapshot.to_dict()) == snapshot


def test_task_snapshot_accepts_null_optionals() -> None:
    payload = {
        "taskId": "task-0002",
        "memberId": "m-writer",
        "agentId": "writer",
        "title": "成稿",
        "acceptance": "交付正文",
        "dependsOn": ["task-0001"],
        "status": "blocked",
    }
    snapshot = TeamTaskSnapshot.from_dict(payload)
    assert snapshot.attempt_id is None
    assert snapshot.last_action is None
    assert snapshot.started_at is None
    assert snapshot.finished_at is None
    assert snapshot.result is None


def test_task_snapshot_rejects_unknown_status() -> None:
    payload = {
        "taskId": "task-0002",
        "memberId": "m-writer",
        "agentId": "writer",
        "title": "成稿",
        "acceptance": "交付正文",
        "dependsOn": [],
        "status": "waiting",
    }
    with pytest.raises(TeamValidationError, match="status"):
        TeamTaskSnapshot.from_dict(payload)


def test_task_snapshot_rejects_finished_at_on_non_terminal_status() -> None:
    payload = {
        "taskId": "task-0002",
        "memberId": "m-writer",
        "agentId": "writer",
        "title": "成稿",
        "acceptance": "交付正文",
        "dependsOn": [],
        "status": "running",
        "finishedAt": STAMP,
    }
    with pytest.raises(TeamValidationError, match="finishedAt"):
        TeamTaskSnapshot.from_dict(payload)


def test_task_snapshot_rejects_oversized_last_action() -> None:
    payload = {
        "taskId": "task-0002",
        "memberId": "m-writer",
        "agentId": "writer",
        "title": "成稿",
        "acceptance": "交付正文",
        "dependsOn": [],
        "status": "running",
        "lastAction": "x" * (MAX_LAST_ACTION_CHARS + 1),
    }
    with pytest.raises(TeamValidationError, match="lastAction"):
        TeamTaskSnapshot.from_dict(payload)


# ------------------------------------------------------------------ 运行快照


def test_run_snapshot_fixture_round_trips(run_snapshot_payload: dict[str, Any]) -> None:
    snapshot = TeamRunSnapshot.from_dict(run_snapshot_payload)
    assert snapshot.status == "completed"
    assert snapshot.last_seq == run_snapshot_payload["lastSeq"]
    assert snapshot.parent_run_id is None
    assert snapshot.to_dict() == run_snapshot_payload
    assert TeamRunSnapshot.from_dict(snapshot.to_dict()) == snapshot


@pytest.mark.parametrize("status", [*TEAM_RUN_STATUSES, "done", "RUNNING"])
def test_run_snapshot_accepts_only_contract_statuses(status: str) -> None:
    payload = _run_payload()
    payload["status"] = status
    if status not in NON_TERMINAL_RUN_STATUSES:
        payload["finishedAt"] = LATER_STAMP
    if status in TEAM_RUN_STATUSES:
        assert TeamRunSnapshot.from_dict(payload).status == status
        return
    with pytest.raises(TeamValidationError, match="status"):
        TeamRunSnapshot.from_dict(payload)


def test_run_snapshot_requires_finished_at_for_terminal_status() -> None:
    payload = _run_payload()
    payload["status"] = "completed"
    with pytest.raises(TeamValidationError, match="finishedAt"):
        TeamRunSnapshot.from_dict(payload)


def _run_payload() -> dict[str, Any]:
    return {
        "runId": "run-0001",
        "chatId": "chat-0001",
        "turnId": "turn-0001",
        "binding": {"teamId": "content-team", "teamRevision": 1, "teamName": "内容团队"},
        "status": "running",
        "lastSeq": 0,
        "tasks": [],
        "leaderAction": "planning",
        "startedAt": STAMP,
    }


@pytest.mark.parametrize("action", ["thinking", "", "Planning", 3, None])
def test_run_snapshot_rejects_unknown_leader_action(action: Any) -> None:
    payload = _run_payload()
    payload["leaderAction"] = action
    with pytest.raises(TeamValidationError, match="leaderAction"):
        TeamRunSnapshot.from_dict(payload)


@pytest.mark.parametrize("last_seq", [-1, True, "0", 0.5, None])
def test_run_snapshot_rejects_invalid_last_seq(last_seq: Any) -> None:
    payload = _run_payload()
    payload["lastSeq"] = last_seq
    with pytest.raises(TeamValidationError, match="lastSeq"):
        TeamRunSnapshot.from_dict(payload)


def test_run_snapshot_rejects_finished_at_on_running_status() -> None:
    payload = _run_payload()
    payload["finishedAt"] = STAMP
    with pytest.raises(TeamValidationError, match="finishedAt"):
        TeamRunSnapshot.from_dict(payload)


def test_run_snapshot_rejects_empty_optional_references() -> None:
    payload = _run_payload()
    payload["parentRunId"] = ""
    with pytest.raises(TeamValidationError, match="parentRunId"):
        TeamRunSnapshot.from_dict(payload)


def test_run_snapshot_rejects_finished_before_started() -> None:
    payload = _run_payload()
    payload["status"] = "completed"
    payload["startedAt"] = LATER_STAMP
    payload["finishedAt"] = STAMP
    with pytest.raises(TeamValidationError, match="finishedAt"):
        TeamRunSnapshot.from_dict(payload)


# ---------------------------------------------------------------------- 事件


def test_event_types_cover_the_contract_union() -> None:
    assert TEAM_EVENT_TYPES == (
        "run.started",
        "leader.updated",
        "task.created",
        "task.queued",
        "task.started",
        "task.activity",
        "task.finished",
        "run.cancelling",
        "run.finished",
    )


@pytest.mark.parametrize("event_type", TEAM_EVENT_TYPES)
def test_event_fixture_anchors_every_contract_type(event_payloads: list[dict[str, Any]], event_type: str) -> None:
    raw = next((item for item in event_payloads if item["type"] == event_type), None)
    assert raw is not None, f"event.json 缺少 type={event_type} 的样例"
    event = TeamEvent.from_dict(raw)
    assert event.schema_version == 1
    assert event.to_dict() == raw
    assert TeamEvent.from_dict(event.to_dict()) == event


def test_task_events_project_to_typed_snapshots(event_payloads: list[dict[str, Any]]) -> None:
    for raw in event_payloads:
        if not raw["type"].startswith("task."):
            continue
        event = TeamEvent.from_dict(raw)
        assert isinstance(event.data, TaskEventData)
        assert isinstance(event.data.task, TeamTaskSnapshot)
        assert event.task_id == event.data.task.task_id


def test_run_and_leader_events_project_to_typed_data(event_payloads: list[dict[str, Any]]) -> None:
    by_type = {item["type"]: item for item in event_payloads}
    started = TeamEvent.from_dict(by_type["run.started"])
    assert isinstance(started.data, RunStartedData)
    assert isinstance(started.data.binding, TeamBinding)
    leader = TeamEvent.from_dict(by_type["leader.updated"])
    assert isinstance(leader.data, LeaderUpdatedData)
    assert leader.data.leader_action == "waiting"
    cancelling = TeamEvent.from_dict(by_type["run.cancelling"])
    assert isinstance(cancelling.data, RunCancellingData)
    assert cancelling.data.status == "cancelling"
    finished = TeamEvent.from_dict(by_type["run.finished"])
    assert isinstance(finished.data, RunFinishedData)
    assert isinstance(finished.data.run, TeamRunSnapshot)


def test_event_rejects_unknown_type(event_payloads: list[dict[str, Any]]) -> None:
    raw = dict(event_payloads[0])
    raw["type"] = "task.paused"
    with pytest.raises(TeamValidationError, match="type"):
        TeamEvent.from_dict(raw)


@pytest.mark.parametrize("version", [2, 0, "1", True, None])
def test_event_rejects_unknown_schema_version(event_payloads: list[dict[str, Any]], version: Any) -> None:
    raw = dict(event_payloads[0])
    raw["schemaVersion"] = version
    with pytest.raises(TeamValidationError, match="schemaVersion"):
        TeamEvent.from_dict(raw)


def test_event_rejects_missing_schema_version(event_payloads: list[dict[str, Any]]) -> None:
    raw = dict(event_payloads[0])
    del raw["schemaVersion"]
    with pytest.raises(TeamValidationError, match="schemaVersion"):
        TeamEvent.from_dict(raw)


def test_event_rejects_data_not_matching_type(event_payloads: list[dict[str, Any]]) -> None:
    raw = next(item for item in event_payloads if item["type"] == "task.started")
    raw["data"] = {"status": "cancelling"}
    with pytest.raises(TeamValidationError, match="task.started"):
        TeamEvent.from_dict(raw)


def test_event_rejects_unknown_data_key_for_started_event(event_payloads: list[dict[str, Any]]) -> None:
    raw = next(item for item in event_payloads if item["type"] == "run.started")
    raw["data"] = dict(raw["data"], credentials="sk-secret")
    with pytest.raises(TeamValidationError, match="credentials"):
        TeamEvent.from_dict(raw)


def test_event_rejects_run_cancelling_with_other_status(event_payloads: list[dict[str, Any]]) -> None:
    raw = next(item for item in event_payloads if item["type"] == "run.cancelling")
    raw["data"] = {"status": "cancelled"}
    with pytest.raises(TeamValidationError, match="status"):
        TeamEvent.from_dict(raw)


def test_event_rejects_unknown_leader_action(event_payloads: list[dict[str, Any]]) -> None:
    raw = next(item for item in event_payloads if item["type"] == "leader.updated")
    raw["data"] = {"leaderAction": "thinking"}
    with pytest.raises(TeamValidationError, match="leaderAction"):
        TeamEvent.from_dict(raw)


@pytest.mark.parametrize("event_type", ["task.created", "task.queued", "task.started", "task.activity", "task.finished"])
def test_task_events_require_task_id(event_payloads: list[dict[str, Any]], event_type: str) -> None:
    raw = next((item for item in event_payloads if item["type"] == event_type), None)
    assert raw is not None
    raw["taskId"] = None
    with pytest.raises(TeamValidationError, match="taskId"):
        TeamEvent.from_dict(raw)


def test_task_events_reject_task_id_conflicting_with_snapshot(
    event_payloads: list[dict[str, Any]]
) -> None:
    raw = next(item for item in event_payloads if item["type"] == "task.started")
    raw["taskId"] = "task-9999"
    with pytest.raises(TeamValidationError, match="taskId"):
        TeamEvent.from_dict(raw)


def test_attempt_id_conflicting_with_snapshot_is_rejected(event_payloads: list[dict[str, Any]]) -> None:
    raw = next(item for item in event_payloads if item["type"] == "task.finished")
    raw["attemptId"] = "attempt-other"
    with pytest.raises(TeamValidationError, match="attemptId"):
        TeamEvent.from_dict(raw)


@pytest.mark.parametrize("seq", [-1, True, "1", None])
def test_event_rejects_invalid_seq(seq: Any) -> None:
    raw = _event_payload()
    raw["seq"] = seq
    with pytest.raises(TeamValidationError, match="seq"):
        TeamEvent.from_dict(raw)


def _event_payload() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "runId": "run-0001",
        "chatId": "chat-0001",
        "turnId": "turn-0001",
        "seq": 1,
        "type": "leader.updated",
        "taskId": None,
        "attemptId": None,
        "occurredAt": STAMP,
        "data": {"leaderAction": "planning"},
    }


def test_event_rejects_non_utc_occurred_at() -> None:
    raw = _event_payload()
    raw["occurredAt"] = "2026-10-15T08:00:00+08:00"
    with pytest.raises(TeamValidationError, match="occurredAt"):
        TeamEvent.from_dict(raw)


def test_run_level_events_reject_task_id() -> None:
    raw = _event_payload()
    raw["taskId"] = "task-0001"
    with pytest.raises(TeamValidationError, match="taskId"):
        TeamEvent.from_dict(raw)


def test_event_rejects_unknown_field() -> None:
    raw = _event_payload()
    raw["workspaceKey"] = "other"
    with pytest.raises(TeamValidationError, match="workspaceKey"):
        TeamEvent.from_dict(raw)


# ------------------------------------------------------------ 路径与配置接入


def test_teams_dir_is_scoped_to_the_workspace(tmp_path: Any) -> None:
    assert get_teams_dir(tmp_path / "proj") == tmp_path / "proj" / "teams"


def test_teams_dir_accepts_str_workspace(tmp_path: Any) -> None:
    assert get_teams_dir(str(tmp_path / "proj")) == tmp_path / "proj" / "teams"


def test_teams_dir_creates_no_directory(tmp_path: Any) -> None:
    workspace = tmp_path / "readonly-project"
    path = get_teams_dir(workspace)
    assert path == workspace / "teams"
    assert not workspace.exists()
    assert not path.exists()


def test_teams_dir_falls_back_to_default_workspace(tmp_path: Any) -> None:
    path = get_teams_dir()
    assert path.parent == tmp_path / ".nanobot" / "workspace"
    assert path.name == "teams"
    assert not path.exists()


def test_teams_dir_expands_user_path() -> None:
    # 用 parts 断言：Windows 上 expanduser 后是反斜杠，按 "/" 结尾判断只在类 Unix 通过。
    path = get_teams_dir("~/proj")
    assert path.parts[-2:] == ("proj", "teams")
    assert "~" not in str(path)


def test_config_loads_without_teams_section_and_stays_enabled(tmp_path: Any) -> None:
    config = load_config(tmp_path / "instance" / "config.json")
    assert config.agents.teams.enabled is True
    assert config.agents.defaults.model


def test_config_reads_disabled_teams_flag(tmp_path: Any) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{"agents": {"teams": {"enabled": false}}}', encoding="utf-8")
    config = load_config(config_path)
    assert config.agents.teams.enabled is False


def test_config_teams_section_defaults_when_empty(tmp_path: Any) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text('{"agents": {}}', encoding="utf-8")
    assert load_config(config_path).agents.teams.enabled is True


def test_config_serializes_teams_section_in_camel_case() -> None:
    dumped = Config().model_dump(by_alias=True)
    assert dumped["agents"]["teams"] == {"enabled": True}


def test_config_rejects_non_boolean_teams_enabled() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate({"agents": {"teams": {"enabled": "sometimes"}}})


def test_full_result_cap_is_enforced_for_storage() -> None:
    assert MAX_TASK_SUMMARY_CHARS < MAX_TASK_RESULT_CHARS
    check_full_result_length("x" * MAX_TASK_RESULT_CHARS)
    with pytest.raises(TeamValidationError, match="fullResult"):
        check_full_result_length("x" * (MAX_TASK_RESULT_CHARS + 1))
