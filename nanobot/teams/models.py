"""Agent Team 领域类型：结构、边界校验与 camelCase 线格式。

真相源是 ``.ai-runtime-artifacts/contracts/2026-10-15-agent-team-contract.md``
§1（对象）、§5（执行结果字段）、§6（事件）；可执行形状锚点是
``tests/fixtures/teams/{definition,snapshot,event}.json``，Python 与 TS 侧共用同一组
文件。三条硬口径：

1. **拒绝式边界**：``from_dict`` 不接受未知枚举值、``bool`` 当 ``int``、越界 limits、
   缺字段、多余字段、非 UTC ISO 8601 时间戳；一律抛 :class:`TeamValidationError`，
   消息里带可定位的字段名。任何入口都不做静默转换、不截断、不补默认模型。
2. **往返等价**：``to_dict()`` 的字段集与 ``from_dict()`` 接受的字段集完全一致，
   所以 fixture 文件与 dataclass 不可能各自漂移。
3. **服务端所有权**：``revision/createdAt/updatedAt`` 由服务端生成；创建入口
   :meth:`TeamDefinition.from_create_payload` 明确拒绝客户端携带这些字段。

值校验统一放在 ``__post_init__``，因此直接构造与 ``from_dict`` 走的是同一套口径。
``CapabilitySelection`` 的 JSON 形状与 ``nanobot.agents.models.AgentSelection``
（即 ``webui/src/lib/agents/types.ts`` 的 ``AgentSelection``）逐字段一致；团队域独立
成类型是为了让边界只抛 ``TeamValidationError``，不把档案域的异常类型泄漏进来。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal, cast

TEAM_SCHEMA_VERSION = 1

TeamRunStatus = Literal[
    "running", "cancelling", "completed", "partial", "failed", "cancelled", "interrupted"
]
TaskStatus = Literal[
    "pending", "queued", "running", "cancelling", "succeeded",
    "failed", "cancelled", "blocked", "interrupted",
]
SelectionMode = Literal["all", "include", "exclude"]
LeaderAction = Literal["planning", "waiting", "reviewing", "summarizing", "idle"]
TeamEventType = Literal[
    "run.started", "leader.updated", "task.created", "task.queued", "task.started",
    "task.activity", "task.finished", "run.cancelling", "run.finished",
]

TEAM_RUN_STATUSES: tuple[str, ...] = (
    "running", "cancelling", "completed", "partial", "failed", "cancelled", "interrupted",
)
TASK_STATUSES: tuple[str, ...] = (
    "pending", "queued", "running", "cancelling", "succeeded",
    "failed", "cancelled", "blocked", "interrupted",
)
SELECTION_MODES: tuple[str, ...] = ("all", "include", "exclude")
LEADER_ACTIONS: tuple[str, ...] = ("planning", "waiting", "reviewing", "summarizing", "idle")
TEAM_EVENT_TYPES: tuple[str, ...] = (
    "run.started", "leader.updated", "task.created", "task.queued", "task.started",
    "task.activity", "task.finished", "run.cancelling", "run.finished",
)

# 非终态：不得带 finishedAt。终态的 finishedAt 是否必填只在运行快照上收紧（契约 §6
# 「运行完成要求所有已声明任务终态」），任务侧的 blocked 允许无时间戳。
NON_TERMINAL_TASK_STATUSES: tuple[str, ...] = ("pending", "queued", "running", "cancelling")
NON_TERMINAL_RUN_STATUSES: tuple[str, ...] = ("running", "cancelling")
_TASK_EVENT_TYPES: frozenset[str] = frozenset(
    {"task.created", "task.queued", "task.started", "task.activity", "task.finished"}
)

MIN_TEAM_MEMBERS = 1
MAX_TEAM_MEMBERS = 8
MAX_ID_CHARS = 128
MAX_TEAM_NAME_CHARS = 80
MAX_TEAM_INSTRUCTIONS_CHARS = 8000
MAX_MEMBER_ROLE_CHARS = 120
MAX_PROFILE_NAME_CHARS = 120  # 与 nanobot/agents/models.py 的 NAME_MAX_LENGTH 同值。
MAX_TASK_TITLE_CHARS = 120
MAX_TASK_INSTRUCTION_CHARS = 32_000
MAX_TASK_ACCEPTANCE_CHARS = 2000
MAX_TASK_SUMMARY_CHARS = 8000
MAX_TASK_RESULT_CHARS = 64_000
MAX_LAST_ACTION_CHARS = 240

SERVER_OWNED_DEFINITION_FIELDS: tuple[str, ...] = ("revision", "createdAt", "updatedAt")

_UNBOUNDED_INT = 2 ** 63 - 1
_UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


class TeamValidationError(Exception):
    """团队领域边界校验失败；message 带可定位的字段名，由上层映射为 TEAM_INVALID。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ------------------------------------------------------------------ 边界原语


def _kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _mapping(raw: object, name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TeamValidationError(f"team {name} must be a JSON object, got {_kind(raw)}")
    return cast("dict[str, Any]", raw)


def _reject_unknown_keys(data: Mapping[str, Any], allowed: Sequence[str], name: str) -> None:
    unknown = sorted(key for key in data if key not in allowed)
    if unknown:
        raise TeamValidationError(f"team {name} has unknown fields: {', '.join(unknown)}")


def _require(data: Mapping[str, Any], key: str, name: str) -> Any:
    if key not in data:
        raise TeamValidationError(f"team {name} is missing required field {key}")
    return data[key]


def _list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise TeamValidationError(f"team {name} must be an array, got {_kind(value)}")
    return cast("list[Any]", value)


def _instance(value: object, name: str, expected: type[Any]) -> None:
    """构造入口的类型护栏；``from_dict`` 之外直接构造时同样生效。"""
    if not isinstance(value, expected):
        raise TeamValidationError(
            f"team {name} must be a {expected.__name__}, got {_kind(value)}"
        )


def _text(value: object, name: str, *, min_chars: int, max_chars: int | None = None) -> str:
    if not isinstance(value, str):
        raise TeamValidationError(f"team {name} must be a string, got {_kind(value)}")
    if min_chars and len(value.strip()) < min_chars:
        raise TeamValidationError(f"team {name} must not be empty")
    if max_chars is not None and len(value) > max_chars:
        raise TeamValidationError(f"team {name} must not exceed {max_chars} characters")
    return value


def _optional_text(
    value: object, name: str, *, max_chars: int | None = MAX_ID_CHARS
) -> str | None:
    if value is None:
        return None
    return _text(value, name, min_chars=1, max_chars=max_chars)


def _integer(value: object, name: str, *, minimum: int, maximum: int = _UNBOUNDED_INT) -> int:
    # ``bool`` 是 ``int`` 的子类，但 True 绝不是「1 次并发」。
    bound = f"between {minimum} and {maximum}" if maximum != _UNBOUNDED_INT else f">= {minimum}"
    if isinstance(value, bool) or not isinstance(value, int):
        raise TeamValidationError(
            f"team {name} must be an integer {bound}, got {_kind(value)}"
        )
    if not minimum <= value <= maximum:
        raise TeamValidationError(f"team {name} must be an integer {bound}, got {value}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TeamValidationError(f"team {name} must be a boolean, got {_kind(value)}")
    return value


def _choice(value: object, name: str, choices: Sequence[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise TeamValidationError(
            f"team {name} must be one of {', '.join(choices)}, got {value!r}"
        )
    return value


def _timestamp(value: object, name: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not _UTC_TIMESTAMP_RE.match(value):
        raise TeamValidationError(
            f"team {name} must be a UTC ISO 8601 timestamp (offset +00:00 or Z), got {value!r}"
        )
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise TeamValidationError(
            f"team {name} must be a UTC ISO 8601 timestamp (offset +00:00 or Z), got {value!r}"
        ) from None
    return value


def _parse_timestamp(value: str, name: str) -> datetime:
    _timestamp(value, name)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _string_list(value: object, name: str, *, unique: bool = False) -> list[str]:
    items = _list(value, name)
    result: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise TeamValidationError(
                f"team {name} must contain non-empty strings, got {_kind(item)}"
            )
        result.append(item)
    if unique and len(set(result)) != len(result):
        raise TeamValidationError(f"team {name} must not contain duplicates")
    return result


def _artifact_list(value: object, name: str) -> list[str]:
    """只接受工作区相对引用；绝对路径、``..`` 越界与反斜杠一律拒绝（契约 §1）。"""
    refs = _string_list(value, name)
    for ref in refs:
        candidate = PurePosixPath(ref.replace("\\", "/"))
        if (
            _WINDOWS_DRIVE_RE.match(ref)
            or "\\" in ref
            or ref.startswith("~")
            or candidate.is_absolute()
            or ".." in candidate.parts
        ):
            raise TeamValidationError(
                f"team {name} must reference workspace-relative paths, got {ref!r}"
            )
    return refs


def check_full_result_length(text: object) -> None:
    """完整任务结果的落盘上限（契约 §1：summary 8000、完整结果 64000）。"""
    _text(text, "fullResult", min_chars=0, max_chars=MAX_TASK_RESULT_CHARS)


# ------------------------------------------------------------------ 领域结构


@dataclass(frozen=True)
class CapabilitySelection:
    """能力选择器；JSON 形状与档案域 ``AgentSelection`` 一致。"""

    mode: SelectionMode = "all"
    entries: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _choice(self.mode, "mode", SELECTION_MODES)
        _string_list(self.entries, "entries", unique=True)

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "entries": list(self.entries)}

    @classmethod
    def from_dict(cls, raw: object) -> CapabilitySelection:
        if raw is None:
            return cls()
        data = _mapping(raw, "selection")
        _reject_unknown_keys(data, ("mode", "entries"), "selection")
        return cls(
            mode=cast("SelectionMode", data.get("mode", "all")),
            entries=_list(data.get("entries", []), "entries"),
        )


@dataclass(frozen=True)
class TeamLimits:
    """契约 §3.2 的默认与上限；越界即拒绝，不夹紧。"""

    max_concurrency: int = 3
    max_tasks: int = 16
    max_dispatch_rounds: int = 4
    task_timeout_seconds: int = 600
    run_timeout_seconds: int = 1800

    def __post_init__(self) -> None:
        _integer(self.max_concurrency, "maxConcurrency", minimum=1, maximum=8)
        _integer(self.max_tasks, "maxTasks", minimum=1, maximum=64)
        _integer(self.max_dispatch_rounds, "maxDispatchRounds", minimum=1, maximum=8)
        _integer(self.task_timeout_seconds, "taskTimeoutSeconds", minimum=30, maximum=1800)
        _integer(self.run_timeout_seconds, "runTimeoutSeconds", minimum=60, maximum=7200)

    def to_dict(self) -> dict[str, Any]:
        return {
            "maxConcurrency": self.max_concurrency,
            "maxTasks": self.max_tasks,
            "maxDispatchRounds": self.max_dispatch_rounds,
            "taskTimeoutSeconds": self.task_timeout_seconds,
            "runTimeoutSeconds": self.run_timeout_seconds,
        }

    @classmethod
    def from_dict(cls, raw: object) -> TeamLimits:
        data = _mapping(raw, "limits")
        _reject_unknown_keys(
            data,
            (
                "maxConcurrency",
                "maxTasks",
                "maxDispatchRounds",
                "taskTimeoutSeconds",
                "runTimeoutSeconds",
            ),
            "limits",
        )
        defaults = cls()
        return cls(
            max_concurrency=data.get("maxConcurrency", defaults.max_concurrency),
            max_tasks=data.get("maxTasks", defaults.max_tasks),
            max_dispatch_rounds=data.get("maxDispatchRounds", defaults.max_dispatch_rounds),
            task_timeout_seconds=data.get("taskTimeoutSeconds", defaults.task_timeout_seconds),
            run_timeout_seconds=data.get("runTimeoutSeconds", defaults.run_timeout_seconds),
        )


@dataclass(frozen=True)
class TeamMember:
    """一个成员槽位；``memberId`` 在团队内稳定，不同于 Agent 档案 ID。"""

    member_id: str
    agent_id: str
    role: str

    def __post_init__(self) -> None:
        _text(self.member_id, "memberId", min_chars=1, max_chars=MAX_ID_CHARS)
        _text(self.agent_id, "agentId", min_chars=1, max_chars=MAX_ID_CHARS)
        _text(self.role, "role", min_chars=1, max_chars=MAX_MEMBER_ROLE_CHARS)

    def to_dict(self) -> dict[str, Any]:
        return {"memberId": self.member_id, "agentId": self.agent_id, "role": self.role}

    @classmethod
    def from_dict(cls, raw: object) -> TeamMember:
        data = _mapping(raw, "member")
        _reject_unknown_keys(data, ("memberId", "agentId", "role"), "member")
        return cls(
            member_id=_require(data, "memberId", "member"),
            agent_id=_require(data, "agentId", "member"),
            role=_require(data, "role", "member"),
        )


@dataclass(frozen=True)
class TeamDefinition:
    """团队当前修订。``revision`` 由服务端递增，客户端不得伪造。"""

    id: str
    revision: int
    name: str
    description: str
    instructions: str
    leader_agent_id: str
    members: list[TeamMember]
    limits: TeamLimits
    archived: bool
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        _text(self.id, "id", min_chars=1, max_chars=MAX_ID_CHARS)
        _integer(self.revision, "revision", minimum=1)
        _text(self.name, "name", min_chars=1, max_chars=MAX_TEAM_NAME_CHARS)
        _text(self.description, "description", min_chars=0)
        _text(self.instructions, "instructions", min_chars=0, max_chars=MAX_TEAM_INSTRUCTIONS_CHARS)
        _text(self.leader_agent_id, "leaderAgentId", min_chars=1, max_chars=MAX_ID_CHARS)
        _boolean(self.archived, "archived")
        _timestamp(self.created_at, "createdAt")
        _timestamp(self.updated_at, "updatedAt")
        _check_members(self.members, self.leader_agent_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "revision": self.revision,
            "name": self.name,
            "description": self.description,
            "instructions": self.instructions,
            "leaderAgentId": self.leader_agent_id,
            "members": [member.to_dict() for member in self.members],
            "limits": self.limits.to_dict(),
            "archived": self.archived,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: object) -> TeamDefinition:
        data = _mapping(raw, "definition")
        _reject_unknown_keys(
            data,
            (
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
            ),
            "definition",
        )
        members = _list(_require(data, "members", "definition"), "members")
        return cls(
            id=_require(data, "id", "definition"),
            revision=_require(data, "revision", "definition"),
            name=_require(data, "name", "definition"),
            description=data.get("description", ""),
            instructions=data.get("instructions", ""),
            leader_agent_id=_require(data, "leaderAgentId", "definition"),
            members=[TeamMember.from_dict(item) for item in members],
            limits=TeamLimits.from_dict(data["limits"]) if "limits" in data else TeamLimits(),
            archived=data.get("archived", False),
            created_at=_require(data, "createdAt", "definition"),
            updated_at=_require(data, "updatedAt", "definition"),
        )

    @classmethod
    def from_create_payload(cls, raw: object, *, now: str) -> TeamDefinition:
        """创建路径（``expectedRevision=null``）：服务端字段一律由本方法盖章。"""
        data = _mapping(raw, "create payload")
        forged = sorted(key for key in data if key in SERVER_OWNED_DEFINITION_FIELDS)
        if forged:
            raise TeamValidationError(
                "team create payload must not carry server-owned fields: "
                + ", ".join(forged)
            )
        payload = dict(data)
        payload["revision"] = 1
        payload["createdAt"] = now
        payload["updatedAt"] = now
        return cls.from_dict(payload)


def _check_members(members: object, leader_agent_id: str) -> None:
    if not isinstance(members, list):
        raise TeamValidationError(f"team members must be an array, got {_kind(members)}")
    items = cast("list[Any]", members)
    if not MIN_TEAM_MEMBERS <= len(items) <= MAX_TEAM_MEMBERS:
        raise TeamValidationError(
            "team members must contain "
            f"{MIN_TEAM_MEMBERS}..{MAX_TEAM_MEMBERS} entries, got {len(items)}"
        )
    member_ids: set[str] = set()
    agent_ids: set[str] = set()
    for index, member in enumerate(items):
        if not isinstance(member, TeamMember):
            raise TeamValidationError(f"team members[{index}] must be a TeamMember")
        if member.member_id in member_ids:
            raise TeamValidationError(f"duplicate team memberId: {member.member_id!r}")
        if member.agent_id in agent_ids:
            raise TeamValidationError(f"duplicate team member agentId: {member.agent_id!r}")
        if member.agent_id == leader_agent_id:
            raise TeamValidationError(
                f"team leaderAgentId must not also be a member: {leader_agent_id!r}"
            )
        member_ids.add(member.member_id)
        agent_ids.add(member.agent_id)


@dataclass(frozen=True)
class TeamBinding:
    """聊天绑定到一个不可变团队修订。"""

    team_id: str
    team_revision: int
    team_name: str

    def __post_init__(self) -> None:
        _text(self.team_id, "teamId", min_chars=1, max_chars=MAX_ID_CHARS)
        _integer(self.team_revision, "teamRevision", minimum=1)
        _text(self.team_name, "teamName", min_chars=1, max_chars=MAX_TEAM_NAME_CHARS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "teamId": self.team_id,
            "teamRevision": self.team_revision,
            "teamName": self.team_name,
        }

    @classmethod
    def from_dict(cls, raw: object) -> TeamBinding:
        data = _mapping(raw, "binding")
        _reject_unknown_keys(data, ("teamId", "teamRevision", "teamName"), "binding")
        return cls(
            team_id=_require(data, "teamId", "binding"),
            team_revision=_require(data, "teamRevision", "binding"),
            team_name=_require(data, "teamName", "binding"),
        )


@dataclass(frozen=True)
class AgentProfileSnapshot:
    """修订快照里的档案约束。绝不包含凭据、provider 配置或父聊天内容。"""

    id: str
    updated_at: str
    prompt: str
    name: str
    model_id: str | None = None
    tools: CapabilitySelection = field(default_factory=CapabilitySelection)
    skills: CapabilitySelection = field(default_factory=CapabilitySelection)
    sub_agents: CapabilitySelection = field(default_factory=CapabilitySelection)

    def __post_init__(self) -> None:
        _text(self.id, "id", min_chars=1, max_chars=MAX_ID_CHARS)
        _timestamp(self.updated_at, "updatedAt")
        _text(self.prompt, "prompt", min_chars=0)
        _text(self.name, "name", min_chars=1, max_chars=MAX_PROFILE_NAME_CHARS)
        _optional_text(self.model_id, "modelId")
        _check_selection(self.tools, "tools")
        _check_selection(self.skills, "skills")
        _check_selection(self.sub_agents, "subAgents")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "updatedAt": self.updated_at,
            "prompt": self.prompt,
            "modelId": self.model_id,
            "tools": self.tools.to_dict(),
            "skills": self.skills.to_dict(),
            "subAgents": self.sub_agents.to_dict(),
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, raw: object) -> AgentProfileSnapshot:
        data = _mapping(raw, "profile snapshot")
        _reject_unknown_keys(
            data,
            ("id", "updatedAt", "prompt", "modelId", "tools", "skills", "subAgents", "name"),
            "profile snapshot",
        )
        return cls(
            id=_require(data, "id", "profile snapshot"),
            updated_at=_require(data, "updatedAt", "profile snapshot"),
            prompt=_require(data, "prompt", "profile snapshot"),
            name=_require(data, "name", "profile snapshot"),
            model_id=data.get("modelId"),
            tools=CapabilitySelection.from_dict(_require(data, "tools", "profile snapshot")),
            skills=CapabilitySelection.from_dict(_require(data, "skills", "profile snapshot")),
            sub_agents=CapabilitySelection.from_dict(
                _require(data, "subAgents", "profile snapshot")
            ),
        )


def _check_selection(value: object, name: str) -> None:
    if not isinstance(value, CapabilitySelection):
        raise TeamValidationError(f"team {name} must be a CapabilitySelection")


@dataclass(frozen=True)
class TeamRevisionMember:
    """成员槽位与其冻结档案的配对。"""

    member: TeamMember
    profile: AgentProfileSnapshot

    def __post_init__(self) -> None:
        _instance(self.member, "member", TeamMember)
        _instance(self.profile, "profile", AgentProfileSnapshot)
        if self.profile.id != self.member.agent_id:
            raise TeamValidationError(
                f"team revision profile {self.profile.id!r} does not match "
                f"member agentId {self.member.agent_id!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"member": self.member.to_dict(), "profile": self.profile.to_dict()}

    @classmethod
    def from_dict(cls, raw: object) -> TeamRevisionMember:
        data = _mapping(raw, "revision member")
        _reject_unknown_keys(data, ("member", "profile"), "revision member")
        return cls(
            member=TeamMember.from_dict(_require(data, "member", "revision member")),
            profile=AgentProfileSnapshot.from_dict(_require(data, "profile", "revision member")),
        )


@dataclass(frozen=True)
class TeamRevisionSnapshot:
    """保存修订时冻结的完整执行上下文；``members`` 顺序必须与定义一致。"""

    definition: TeamDefinition
    leader: AgentProfileSnapshot
    members: list[TeamRevisionMember]

    def __post_init__(self) -> None:
        _instance(self.definition, "definition", TeamDefinition)
        _instance(self.leader, "leader", AgentProfileSnapshot)
        if self.leader.id != self.definition.leader_agent_id:
            raise TeamValidationError(
                f"team revision leader profile {self.leader.id!r} does not match "
                f"leaderAgentId {self.definition.leader_agent_id!r}"
            )
        _instance(self.members, "members", list)
        if len(self.members) != len(self.definition.members):
            raise TeamValidationError(
                f"team revision snapshot members must cover all {len(self.definition.members)} "
                "team members"
            )
        for index, entry in enumerate(self.members):
            _instance(entry, f"members[{index}]", TeamRevisionMember)
            expected = self.definition.members[index]
            if entry.member != expected:
                raise TeamValidationError(
                    f"team revision snapshot members[{index}] does not match the "
                    f"definition member order ({expected.member_id!r})"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "definition": self.definition.to_dict(),
            "leader": self.leader.to_dict(),
            "members": [entry.to_dict() for entry in self.members],
        }

    @classmethod
    def from_dict(cls, raw: object) -> TeamRevisionSnapshot:
        data = _mapping(raw, "revision snapshot")
        _reject_unknown_keys(data, ("definition", "leader", "members"), "revision snapshot")
        entries = _list(_require(data, "members", "revision snapshot"), "members")
        return cls(
            definition=TeamDefinition.from_dict(_require(data, "definition", "revision snapshot")),
            leader=AgentProfileSnapshot.from_dict(_require(data, "leader", "revision snapshot")),
            members=[TeamRevisionMember.from_dict(item) for item in entries],
        )


@dataclass(frozen=True)
class TeamTaskInput:
    """``team_dispatch`` 的单条任务声明（模型可见字段，宿主再注入可信上下文）。"""

    key: str
    member_id: str
    title: str
    instruction: str
    acceptance: str
    depends_on: list[str] = field(default_factory=list)
    resource_keys: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _text(self.key, "key", min_chars=1, max_chars=MAX_ID_CHARS)
        _text(self.member_id, "memberId", min_chars=1, max_chars=MAX_ID_CHARS)
        _text(self.title, "title", min_chars=1, max_chars=MAX_TASK_TITLE_CHARS)
        _text(self.instruction, "instruction", min_chars=1, max_chars=MAX_TASK_INSTRUCTION_CHARS)
        _text(self.acceptance, "acceptance", min_chars=1, max_chars=MAX_TASK_ACCEPTANCE_CHARS)
        _string_list(self.depends_on, "dependsOn", unique=True)
        _string_list(self.resource_keys, "resourceKeys")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "memberId": self.member_id,
            "title": self.title,
            "instruction": self.instruction,
            "acceptance": self.acceptance,
            "dependsOn": list(self.depends_on),
            "resourceKeys": list(self.resource_keys),
        }

    @classmethod
    def from_dict(cls, raw: object) -> TeamTaskInput:
        data = _mapping(raw, "task input")
        _reject_unknown_keys(
            data,
            ("key", "memberId", "title", "instruction", "acceptance", "dependsOn", "resourceKeys"),
            "task input",
        )
        return cls(
            key=_require(data, "key", "task input"),
            member_id=_require(data, "memberId", "task input"),
            title=_require(data, "title", "task input"),
            instruction=_require(data, "instruction", "task input"),
            acceptance=_require(data, "acceptance", "task input"),
            depends_on=_list(data.get("dependsOn", []), "dependsOn"),
            resource_keys=_list(data.get("resourceKeys", []), "resourceKeys"),
        )


@dataclass(frozen=True)
class TeamTaskUsage:
    """契约 §1 的两字段用量视图；缺失时整体为 ``None``，不按 0 计。"""

    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        _integer(self.input_tokens, "inputTokens", minimum=0)
        _integer(self.output_tokens, "outputTokens", minimum=0)

    def to_dict(self) -> dict[str, Any]:
        return {"inputTokens": self.input_tokens, "outputTokens": self.output_tokens}

    @classmethod
    def from_dict(cls, raw: object) -> TeamTaskUsage:
        data = _mapping(raw, "usage")
        _reject_unknown_keys(data, ("inputTokens", "outputTokens"), "usage")
        return cls(
            input_tokens=_require(data, "inputTokens", "usage"),
            output_tokens=_require(data, "outputTokens", "usage"),
        )


@dataclass(frozen=True)
class TeamTaskResult:
    """任务终态结果；``succeeded`` 不允许带 ``errorCode``（契约 §5 只做结构化判定）。"""

    task_id: str
    attempt_id: str
    member_id: str
    agent_id: str
    status: TaskStatus
    stop_reason: str
    summary: str
    artifacts: list[str]
    error_code: str | None = None
    usage: TeamTaskUsage | None = None

    def __post_init__(self) -> None:
        _text(self.task_id, "taskId", min_chars=1, max_chars=MAX_ID_CHARS)
        _text(self.attempt_id, "attemptId", min_chars=1, max_chars=MAX_ID_CHARS)
        _text(self.member_id, "memberId", min_chars=1, max_chars=MAX_ID_CHARS)
        _text(self.agent_id, "agentId", min_chars=1, max_chars=MAX_ID_CHARS)
        _choice(self.status, "status", TASK_STATUSES)
        _text(self.stop_reason, "stopReason", min_chars=1)
        _text(self.summary, "summary", min_chars=0, max_chars=MAX_TASK_SUMMARY_CHARS)
        _artifact_list(self.artifacts, "artifacts")
        _optional_text(self.error_code, "errorCode")
        if self.usage is not None:
            _instance(self.usage, "usage", TeamTaskUsage)
        if self.status == "succeeded" and self.error_code is not None:
            raise TeamValidationError(
                f"team succeeded task must not carry errorCode: {self.error_code!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id,
            "attemptId": self.attempt_id,
            "memberId": self.member_id,
            "agentId": self.agent_id,
            "status": self.status,
            "stopReason": self.stop_reason,
            "summary": self.summary,
            "artifacts": list(self.artifacts),
            "errorCode": self.error_code,
            "usage": self.usage.to_dict() if self.usage is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: object) -> TeamTaskResult:
        data = _mapping(raw, "task result")
        _reject_unknown_keys(
            data,
            (
                "taskId",
                "attemptId",
                "memberId",
                "agentId",
                "status",
                "stopReason",
                "summary",
                "artifacts",
                "errorCode",
                "usage",
            ),
            "task result",
        )
        usage = data.get("usage")
        return cls(
            task_id=_require(data, "taskId", "task result"),
            attempt_id=_require(data, "attemptId", "task result"),
            member_id=_require(data, "memberId", "task result"),
            agent_id=_require(data, "agentId", "task result"),
            status=cast("TaskStatus", _require(data, "status", "task result")),
            stop_reason=_require(data, "stopReason", "task result"),
            summary=_require(data, "summary", "task result"),
            artifacts=_list(data.get("artifacts", []), "artifacts"),
            error_code=data.get("errorCode"),
            usage=TeamTaskUsage.from_dict(usage) if usage is not None else None,
        )


@dataclass(frozen=True)
class TeamTaskSnapshot:
    """任务最新状态快照；事件 ``task.*`` 与运行快照共用这一形状。"""

    task_id: str
    member_id: str
    agent_id: str
    title: str
    acceptance: str
    depends_on: list[str]
    status: TaskStatus
    attempt_id: str | None = None
    last_action: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    result: TeamTaskResult | None = None

    def __post_init__(self) -> None:
        _text(self.task_id, "taskId", min_chars=1, max_chars=MAX_ID_CHARS)
        _text(self.member_id, "memberId", min_chars=1, max_chars=MAX_ID_CHARS)
        _text(self.agent_id, "agentId", min_chars=1, max_chars=MAX_ID_CHARS)
        _text(self.title, "title", min_chars=1, max_chars=MAX_TASK_TITLE_CHARS)
        _text(self.acceptance, "acceptance", min_chars=1, max_chars=MAX_TASK_ACCEPTANCE_CHARS)
        _string_list(self.depends_on, "dependsOn", unique=True)
        _choice(self.status, "status", TASK_STATUSES)
        _optional_text(self.attempt_id, "attemptId")
        _optional_text(self.last_action, "lastAction", max_chars=MAX_LAST_ACTION_CHARS)
        _timestamp(self.started_at, "startedAt", nullable=True)
        _timestamp(self.finished_at, "finishedAt", nullable=True)
        if self.status in NON_TERMINAL_TASK_STATUSES and self.finished_at is not None:
            raise TeamValidationError(
                f"team task status {self.status!r} must not carry finishedAt"
            )
        started = self.started_at
        finished = self.finished_at
        if started is not None and finished is not None:
            if _parse_timestamp(finished, "finishedAt") < _parse_timestamp(started, "startedAt"):
                raise TeamValidationError(
                    f"team finishedAt must not precede startedAt for task {self.task_id!r}"
                )
        if self.result is not None:
            _check_result(self.result, self.task_id, self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskId": self.task_id,
            "memberId": self.member_id,
            "agentId": self.agent_id,
            "title": self.title,
            "acceptance": self.acceptance,
            "dependsOn": list(self.depends_on),
            "status": self.status,
            "attemptId": self.attempt_id,
            "lastAction": self.last_action,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "result": self.result.to_dict() if self.result is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: object) -> TeamTaskSnapshot:
        data = _mapping(raw, "task snapshot")
        _reject_unknown_keys(
            data,
            (
                "taskId",
                "memberId",
                "agentId",
                "title",
                "acceptance",
                "dependsOn",
                "status",
                "attemptId",
                "lastAction",
                "startedAt",
                "finishedAt",
                "result",
            ),
            "task snapshot",
        )
        result = data.get("result")
        return cls(
            task_id=_require(data, "taskId", "task snapshot"),
            member_id=_require(data, "memberId", "task snapshot"),
            agent_id=_require(data, "agentId", "task snapshot"),
            title=_require(data, "title", "task snapshot"),
            acceptance=_require(data, "acceptance", "task snapshot"),
            depends_on=_list(_require(data, "dependsOn", "task snapshot"), "dependsOn"),
            status=cast("TaskStatus", _require(data, "status", "task snapshot")),
            attempt_id=data.get("attemptId"),
            last_action=data.get("lastAction"),
            started_at=data.get("startedAt"),
            finished_at=data.get("finishedAt"),
            result=TeamTaskResult.from_dict(result) if result is not None else None,
        )


def _check_result(result: object, task_id: str, status: str) -> None:
    if not isinstance(result, TeamTaskResult):
        raise TeamValidationError(f"team result for task {task_id!r} must be a TeamTaskResult")
    if result.task_id != task_id:
        raise TeamValidationError(
            f"team result taskId {result.task_id!r} does not match snapshot taskId {task_id!r}"
        )
    if result.status != status:
        raise TeamValidationError(
            f"team result status {result.status!r} does not match snapshot status {status!r}"
        )


@dataclass(frozen=True)
class TeamRunSnapshot:
    """一次团队运行；``lastSeq`` 与任务状态来自同一数据库读事务（契约 §6）。"""

    run_id: str
    chat_id: str
    turn_id: str
    binding: TeamBinding
    status: TeamRunStatus
    last_seq: int
    tasks: list[TeamTaskSnapshot]
    leader_action: LeaderAction
    started_at: str
    finished_at: str | None = None
    parent_run_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.run_id, "runId", min_chars=1, max_chars=MAX_ID_CHARS)
        _text(self.chat_id, "chatId", min_chars=1, max_chars=MAX_ID_CHARS)
        _text(self.turn_id, "turnId", min_chars=1, max_chars=MAX_ID_CHARS)
        _instance(self.binding, "binding", TeamBinding)
        _choice(self.status, "status", TEAM_RUN_STATUSES)
        _integer(self.last_seq, "lastSeq", minimum=0)
        _check_task_list(self.tasks)
        _choice(self.leader_action, "leaderAction", LEADER_ACTIONS)
        _timestamp(self.started_at, "startedAt")
        _timestamp(self.finished_at, "finishedAt", nullable=True)
        _optional_text(self.parent_run_id, "parentRunId")
        if self.status in NON_TERMINAL_RUN_STATUSES:
            if self.finished_at is not None:
                raise TeamValidationError(
                    f"team run status {self.status!r} must not carry finishedAt"
                )
        elif self.finished_at is None:
            raise TeamValidationError(f"team terminal run {self.status!r} requires finishedAt")
        finished = self.finished_at
        if finished is not None and _parse_timestamp(
            finished, "finishedAt"
        ) < _parse_timestamp(self.started_at, "startedAt"):
            raise TeamValidationError("team finishedAt must not precede startedAt")

    def to_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "chatId": self.chat_id,
            "turnId": self.turn_id,
            "binding": self.binding.to_dict(),
            "status": self.status,
            "lastSeq": self.last_seq,
            "tasks": [task.to_dict() for task in self.tasks],
            "leaderAction": self.leader_action,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "parentRunId": self.parent_run_id,
        }

    @classmethod
    def from_dict(cls, raw: object) -> TeamRunSnapshot:
        data = _mapping(raw, "run snapshot")
        _reject_unknown_keys(
            data,
            (
                "runId",
                "chatId",
                "turnId",
                "binding",
                "status",
                "lastSeq",
                "tasks",
                "leaderAction",
                "startedAt",
                "finishedAt",
                "parentRunId",
            ),
            "run snapshot",
        )
        tasks = _list(_require(data, "tasks", "run snapshot"), "tasks")
        return cls(
            run_id=_require(data, "runId", "run snapshot"),
            chat_id=_require(data, "chatId", "run snapshot"),
            turn_id=_require(data, "turnId", "run snapshot"),
            binding=TeamBinding.from_dict(_require(data, "binding", "run snapshot")),
            status=cast("TeamRunStatus", _require(data, "status", "run snapshot")),
            last_seq=_require(data, "lastSeq", "run snapshot"),
            tasks=[TeamTaskSnapshot.from_dict(item) for item in tasks],
            leader_action=cast(
                "LeaderAction", _require(data, "leaderAction", "run snapshot")
            ),
            started_at=_require(data, "startedAt", "run snapshot"),
            finished_at=data.get("finishedAt"),
            parent_run_id=data.get("parentRunId"),
        )


def _check_task_list(tasks: object) -> None:
    if not isinstance(tasks, list):
        raise TeamValidationError(f"team tasks must be an array, got {_kind(tasks)}")
    for index, task in enumerate(cast("list[Any]", tasks)):
        if not isinstance(task, TeamTaskSnapshot):
            raise TeamValidationError(f"team tasks[{index}] must be a TeamTaskSnapshot")


# ------------------------------------------------------------------ 事件边界


@dataclass(frozen=True)
class RunStartedData:
    binding: TeamBinding
    started_at: str

    def __post_init__(self) -> None:
        _instance(self.binding, "binding", TeamBinding)
        _timestamp(self.started_at, "startedAt")

    def to_dict(self) -> dict[str, Any]:
        return {"binding": self.binding.to_dict(), "startedAt": self.started_at}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], event_type: str) -> RunStartedData:
        _reject_unknown_keys(data, ("binding", "startedAt"), f"event {event_type}")
        return cls(
            binding=TeamBinding.from_dict(_require(data, "binding", f"event {event_type}")),
            started_at=_require(data, "startedAt", f"event {event_type}"),
        )


@dataclass(frozen=True)
class LeaderUpdatedData:
    leader_action: str

    def __post_init__(self) -> None:
        _choice(self.leader_action, "leaderAction", LEADER_ACTIONS)

    def to_dict(self) -> dict[str, Any]:
        return {"leaderAction": self.leader_action}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], event_type: str) -> LeaderUpdatedData:
        _reject_unknown_keys(data, ("leaderAction",), f"event {event_type}")
        return cls(leader_action=_require(data, "leaderAction", f"event {event_type}"))


@dataclass(frozen=True)
class TaskEventData:
    task: TeamTaskSnapshot

    def __post_init__(self) -> None:
        _instance(self.task, "task", TeamTaskSnapshot)

    def to_dict(self) -> dict[str, Any]:
        return {"task": self.task.to_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], event_type: str) -> TaskEventData:
        _reject_unknown_keys(data, ("task",), f"event {event_type}")
        return cls(task=TeamTaskSnapshot.from_dict(_require(data, "task", f"event {event_type}")))


@dataclass(frozen=True)
class RunCancellingData:
    status: str

    def __post_init__(self) -> None:
        _choice(self.status, "status", ("cancelling",))

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], event_type: str) -> RunCancellingData:
        _reject_unknown_keys(data, ("status",), f"event {event_type}")
        return cls(status=_require(data, "status", f"event {event_type}"))


@dataclass(frozen=True)
class RunFinishedData:
    run: TeamRunSnapshot

    def __post_init__(self) -> None:
        _instance(self.run, "run", TeamRunSnapshot)

    def to_dict(self) -> dict[str, Any]:
        return {"run": self.run.to_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], event_type: str) -> RunFinishedData:
        _reject_unknown_keys(data, ("run",), f"event {event_type}")
        return cls(run=TeamRunSnapshot.from_dict(_require(data, "run", f"event {event_type}")))


TeamEventData = (
    RunStartedData | LeaderUpdatedData | TaskEventData | RunCancellingData | RunFinishedData
)

_EVENT_DATA_TYPES: Mapping[str, type[Any]] = {
    "run.started": RunStartedData,
    "leader.updated": LeaderUpdatedData,
    "task.created": TaskEventData,
    "task.queued": TaskEventData,
    "task.started": TaskEventData,
    "task.activity": TaskEventData,
    "task.finished": TaskEventData,
    "run.cancelling": RunCancellingData,
    "run.finished": RunFinishedData,
}


@dataclass(frozen=True)
class TeamEvent:
    """契约 §6 的持久事件；``data`` 已按 ``type`` 解析成判别联合，不是原始字典。"""

    schema_version: int
    run_id: str
    chat_id: str
    turn_id: str
    seq: int
    type: TeamEventType
    occurred_at: str
    data: TeamEventData
    task_id: str | None = None
    attempt_id: str | None = None

    def __post_init__(self) -> None:
        _integer(self.schema_version, "schemaVersion", minimum=1, maximum=TEAM_SCHEMA_VERSION)
        _text(self.run_id, "runId", min_chars=1, max_chars=MAX_ID_CHARS)
        _text(self.chat_id, "chatId", min_chars=1, max_chars=MAX_ID_CHARS)
        _text(self.turn_id, "turnId", min_chars=1, max_chars=MAX_ID_CHARS)
        _integer(self.seq, "seq", minimum=0)
        _choice(self.type, "type", TEAM_EVENT_TYPES)
        _timestamp(self.occurred_at, "occurredAt")
        _optional_text(self.task_id, "taskId")
        _optional_text(self.attempt_id, "attemptId")
        self._check_data_ownership()

    def _check_data_ownership(self) -> None:
        if self.type in _TASK_EVENT_TYPES:
            if not isinstance(self.data, TaskEventData):
                raise TeamValidationError(
                    f"team event {self.type} requires a task snapshot, got {_kind(self.data)}"
                )
            if self.task_id is None:
                raise TeamValidationError(f"team event {self.type} requires a non-null taskId")
            if self.data.task.task_id != self.task_id:
                raise TeamValidationError(
                    f"team event {self.type} taskId {self.task_id!r} conflicts with "
                    f"snapshot taskId {self.data.task.task_id!r}"
                )
            if self.attempt_id is not None and self.data.task.attempt_id != self.attempt_id:
                raise TeamValidationError(
                    f"team event {self.type} attemptId {self.attempt_id!r} conflicts with "
                    f"snapshot attemptId {self.data.task.attempt_id!r}"
                )
            return
        _instance(self.data, "data", _EVENT_DATA_TYPES[self.type])
        if self.task_id is not None:
            raise TeamValidationError(f"team event {self.type} must not carry a taskId")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "runId": self.run_id,
            "chatId": self.chat_id,
            "turnId": self.turn_id,
            "seq": self.seq,
            "type": self.type,
            "taskId": self.task_id,
            "attemptId": self.attempt_id,
            "occurredAt": self.occurred_at,
            "data": self.data.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: object) -> TeamEvent:
        data = _mapping(raw, "event")
        _reject_unknown_keys(
            data,
            (
                "schemaVersion",
                "runId",
                "chatId",
                "turnId",
                "seq",
                "type",
                "taskId",
                "attemptId",
                "occurredAt",
                "data",
            ),
            "event",
        )
        event_type = _choice(_require(data, "type", "event"), "type", TEAM_EVENT_TYPES)
        payload = _mapping(_require(data, "data", "event"), f"event {event_type} data")
        return cls(
            schema_version=_require(data, "schemaVersion", "event"),
            run_id=_require(data, "runId", "event"),
            chat_id=_require(data, "chatId", "event"),
            turn_id=_require(data, "turnId", "event"),
            seq=_require(data, "seq", "event"),
            type=cast("TeamEventType", event_type),
            occurred_at=_require(data, "occurredAt", "event"),
            data=_EVENT_DATA_TYPES[event_type].from_dict(payload, event_type),
            task_id=data.get("taskId"),
            attempt_id=data.get("attemptId"),
        )
