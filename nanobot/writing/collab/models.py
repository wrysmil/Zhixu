"""协作域字段语义：枚举常量、行投影与内容摘要。

契约 §1 的枚举取值在这里是**唯一出口**：所有写入路径都先过
``normalize_enum``，非法取值一律抛 ``InvalidEnumValueError``（message 必带字段名与非法值），
既不静默降级也不落库。本模块不生成 id / 不写时间戳（那是 repository 的职责）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar, cast

T = TypeVar("T")

COLLAB_SCHEMA_VERSION = "2"

# 协作状态（契约 §1）
COLLAB_STATUSES = (
    "idle", "queued", "running", "waiting_author",
    "completed", "failed", "cancelled", "interrupted",
)
# 紧凑显示用的快照枚举："" 表示尚未起步
COLLAB_STAGES = ("", "brief", "research", "outline", "draft", "review", "rewrite")
BATCH_STATUSES = (
    "queued", "running", "waiting_author",
    "completed", "failed", "cancelled", "interrupted",
)
ARTIFACT_KINDS = ("brief", "material_list", "outline", "draft", "rewrite", "review")
ARTIFACT_ROLE_IDS = (
    "writing-editor", "writing-researcher", "writing-architect",
    "writing-author", "writing-reviewer",
)
ARTIFACT_STATUSES = ("ready", "superseded", "discarded")
HANDOFF_KINDS = ("outline_confirm", "materials_request", "final_accept")
HANDOFF_STATUSES = ("pending", "confirmed", "superseded")
SUGGESTION_STATUSES = ("pending", "applied", "rejected", "stale", "reverted")
SUGGESTION_TARGET_KINDS = ("replace_range", "insert_at", "outline_item")
ACTIONS = (
    "start_full", "ideate", "research", "outline",
    "draft", "rewrite_selection", "review_all", "reply",
)

# 契约 §4：已有这些状态的批次时，该文章不得再开新批次
ACTIVE_BATCH_STATUSES = ("queued", "running", "waiting_author")
IN_FLIGHT_STATUSES = ("queued", "running")


class CollaborationStatus:
    """``collaboration.status`` 取值（字符串常量，可直接入库/比较）。"""

    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_AUTHOR = "waiting_author"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class Stage:
    """``collaboration.stage`` 取值；``EMPTY`` 是契约里的空串。"""

    EMPTY = ""
    BRIEF = "brief"
    RESEARCH = "research"
    OUTLINE = "outline"
    DRAFT = "draft"
    REVIEW = "review"
    REWRITE = "rewrite"


class BatchStatus:
    """``batch.status`` 取值。"""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_AUTHOR = "waiting_author"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ArtifactKind:
    """``artifact.kind`` 取值。"""

    BRIEF = "brief"
    MATERIAL_LIST = "material_list"
    OUTLINE = "outline"
    DRAFT = "draft"
    REWRITE = "rewrite"
    REVIEW = "review"


class HandoffKind:
    """``handoff.kind`` 取值。"""

    OUTLINE_CONFIRM = "outline_confirm"
    MATERIALS_REQUEST = "materials_request"
    FINAL_ACCEPT = "final_accept"


class HandoffStatus:
    """``handoff.status`` 取值。"""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"


class SuggestionStatus:
    """``suggestion.status`` 取值。"""

    PENDING = "pending"
    APPLIED = "applied"
    REJECTED = "rejected"
    STALE = "stale"
    REVERTED = "reverted"


class Action:
    """批次 ``action`` 取值（契约 §1）。"""

    START_FULL = "start_full"
    IDEATE = "ideate"
    RESEARCH = "research"
    OUTLINE = "outline"
    DRAFT = "draft"
    REWRITE_SELECTION = "rewrite_selection"
    REVIEW_ALL = "review_all"
    REPLY = "reply"


class InvalidEnumValueError(ValueError):
    """枚举取值不在白名单：message 含字段名与非法值，便于契约级断言。"""

    def __init__(self, field: str, value: object) -> None:
        super().__init__(f"unsupported {field}: {value!r}")
        self.field = field
        self.value = value


def normalize_enum(field: str, value: object, allowed: Sequence[str]) -> str:
    """白名单校验：非法值（含非字符串）一律拒绝，不做兜底降级。"""
    if not isinstance(value, str) or value not in allowed:
        raise InvalidEnumValueError(field, value)
    return value


def normalize_optional_enum(field: str, value: object, allowed: Sequence[str]) -> str | None:
    if value is None:
        return None
    return normalize_enum(field, value, allowed)


def artifact_digest(content: Mapping[str, Any]) -> str:
    """产物内容摘要：键序无关、同内容同值（契约 §1 ``content_digest``）。"""
    encoded = json.dumps(
        dict(content), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _optional(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int:
    return int(value) if isinstance(value, (int, float, str)) else 0


def _json_obj(raw: str, default: Mapping[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "null")
    except ValueError:
        parsed = None
    if not isinstance(parsed, Mapping):
        return dict(default)
    return dict(cast(Mapping[str, Any], parsed))


def _json_list(raw: str) -> list[Any]:
    try:
        parsed = json.loads(raw or "null")
    except ValueError:
        parsed = None
    if not isinstance(parsed, list):
        return []
    return list(cast(Sequence[Any], parsed))


def _text_list(raw: str) -> list[str]:
    return [item for item in _json_list(raw) if isinstance(item, str)]


@dataclass(frozen=True, slots=True)
class Collaboration:
    """一行 ``writing_collaborations``。"""

    id: str
    workspace_key: str
    article_id: str
    chat_id: str
    session_key: str
    goal: dict[str, Any]
    status: str
    stage: str
    active_batch_id: str | None
    cancel_generation: int
    version: int
    seq: int
    last_handoff_id: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Collaboration:
        return cls(
            id=_text(row["id"]),
            workspace_key=_text(row["workspace_key"]),
            article_id=_text(row["article_id"]),
            chat_id=_text(row["chat_id"]),
            session_key=_text(row["session_key"]),
            goal=_json_obj(_text(row["goal_json"]), {}),
            status=_text(row["status"]),
            stage=_text(row["stage"]),
            active_batch_id=_optional(row["active_batch_id"]),
            cancel_generation=_int(row["cancel_generation"]),
            version=_int(row["version"]),
            seq=_int(row["seq"]),
            last_handoff_id=_optional(row["last_handoff_id"]),
            created_at=_text(row["created_at"]),
            updated_at=_text(row["updated_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspace_key": self.workspace_key,
            "article_id": self.article_id,
            "chat_id": self.chat_id,
            "session_key": self.session_key,
            "goal": self.goal,
            "status": self.status,
            "stage": self.stage,
            "active_batch_id": self.active_batch_id,
            "cancel_generation": self.cancel_generation,
            "version": self.version,
            "seq": self.seq,
            "last_handoff_id": self.last_handoff_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class Batch:
    """一行 ``writing_collab_batches``。"""

    id: str
    workspace_key: str
    collaboration_id: str
    attempt: int
    team_run_id: str
    base_revision: int
    snapshot_digest: str
    action: str
    instruction: str
    status: str
    cancel_generation: int
    frozen: dict[str, Any]
    error_message: str
    created_at: str
    finished_at: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Batch:
        return cls(
            id=_text(row["id"]),
            workspace_key=_text(row["workspace_key"]),
            collaboration_id=_text(row["collaboration_id"]),
            attempt=_int(row["attempt"]),
            team_run_id=_text(row["team_run_id"]),
            base_revision=_int(row["base_revision"]),
            snapshot_digest=_text(row["snapshot_digest"]),
            action=_text(row["action"]),
            instruction=_text(row["instruction"]),
            status=_text(row["status"]),
            cancel_generation=_int(row["cancel_generation"]),
            frozen=_json_obj(_text(row["frozen_json"]), {}),
            error_message=_text(row["error_message"]),
            created_at=_text(row["created_at"]),
            finished_at=_optional(row["finished_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspace_key": self.workspace_key,
            "collaboration_id": self.collaboration_id,
            "attempt": self.attempt,
            "team_run_id": self.team_run_id,
            "base_revision": self.base_revision,
            "snapshot_digest": self.snapshot_digest,
            "action": self.action,
            "instruction": self.instruction,
            "status": self.status,
            "cancel_generation": self.cancel_generation,
            "frozen": self.frozen,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


@dataclass(frozen=True, slots=True)
class Artifact:
    """一行 ``writing_collab_artifacts``；``content`` 只在详情投影里出现。"""

    id: str
    workspace_key: str
    collaboration_id: str
    batch_id: str
    attempt: int
    kind: str
    role_id: str
    version: int
    content: dict[str, Any]
    content_digest: str
    source_ids: list[str]
    gaps: list[Any]
    status: str
    created_at: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Artifact:
        return cls(
            id=_text(row["id"]),
            workspace_key=_text(row["workspace_key"]),
            collaboration_id=_text(row["collaboration_id"]),
            batch_id=_text(row["batch_id"]),
            attempt=_int(row["attempt"]),
            kind=_text(row["kind"]),
            role_id=_text(row["role_id"]),
            version=_int(row["version"]),
            content=_json_obj(_text(row["content_json"]), {}),
            content_digest=_text(row["content_digest"]),
            source_ids=_text_list(_text(row["source_ids"])),
            gaps=_json_list(_text(row["gaps"])),
            status=_text(row["status"]),
            created_at=_text(row["created_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        """详情投影：含产物全文（列表投影见 ``to_listing_dict``）。"""
        return {
            "id": self.id,
            "workspace_key": self.workspace_key,
            "collaboration_id": self.collaboration_id,
            "batch_id": self.batch_id,
            "attempt": self.attempt,
            "kind": self.kind,
            "role_id": self.role_id,
            "version": self.version,
            "content": self.content,
            "content_digest": self.content_digest,
            "source_ids": self.source_ids,
            "gaps": self.gaps,
            "status": self.status,
            "created_at": self.created_at,
        }

    def to_listing_dict(self) -> dict[str, Any]:
        """列表投影：**不含** ``content``，避免一次拉回全部产物正文。"""
        return {
            "id": self.id,
            "collaboration_id": self.collaboration_id,
            "batch_id": self.batch_id,
            "attempt": self.attempt,
            "kind": self.kind,
            "role_id": self.role_id,
            "version": self.version,
            "content_digest": self.content_digest,
            "source_ids": self.source_ids,
            "gaps": self.gaps,
            "status": self.status,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class Handoff:
    """一行 ``writing_collab_handoffs``；``status`` 自身充当交接版本（CAS 口径）。"""

    id: str
    workspace_key: str
    collaboration_id: str
    batch_id: str
    artifact_id: str
    kind: str
    artifact_version: int
    status: str
    created_at: str
    resolved_at: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Handoff:
        return cls(
            id=_text(row["id"]),
            workspace_key=_text(row["workspace_key"]),
            collaboration_id=_text(row["collaboration_id"]),
            batch_id=_text(row["batch_id"]),
            artifact_id=_text(row["artifact_id"]),
            kind=_text(row["kind"]),
            artifact_version=_int(row["artifact_version"]),
            status=_text(row["status"]),
            created_at=_text(row["created_at"]),
            resolved_at=_optional(row["resolved_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "collaboration_id": self.collaboration_id,
            "batch_id": self.batch_id,
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "artifact_version": self.artifact_version,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }


@dataclass(frozen=True, slots=True)
class Suggestion:
    """一行 ``writing_collab_suggestions``（采纳/撤销由 WU-05 使用）。"""

    id: str
    workspace_key: str
    collaboration_id: str
    artifact_id: str
    article_id: str
    base_revision: int
    snapshot_digest: str
    target: dict[str, Any]
    before: str
    after: str
    summary: str
    notes: str
    source_material_ids: list[str]
    status: str
    created_at: str
    applied_revision: int | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Suggestion:
        return cls(
            id=_text(row["id"]),
            workspace_key=_text(row["workspace_key"]),
            collaboration_id=_text(row["collaboration_id"]),
            artifact_id=_text(row["artifact_id"]),
            article_id=_text(row["article_id"]),
            base_revision=_int(row["base_revision"]),
            snapshot_digest=_text(row["snapshot_digest"]),
            target=_json_obj(_text(row["target_json"]), {}),
            before=_text(row["before_text"]),
            after=_text(row["after_text"]),
            summary=_text(row["summary"]),
            notes=_text(row["notes"]),
            source_material_ids=_text_list(_text(row["source_material_ids"])),
            status=_text(row["status"]),
            created_at=_text(row["created_at"]),
            applied_revision=(
                int(row["applied_revision"])
                if isinstance(row["applied_revision"], (int, float, str))
                and row["applied_revision"] not in (None, "")
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "collaboration_id": self.collaboration_id,
            "artifact_id": self.artifact_id,
            "article_id": self.article_id,
            "base_revision": self.base_revision,
            "snapshot_digest": self.snapshot_digest,
            "target": self.target,
            "before": self.before,
            "after": self.after,
            "summary": self.summary,
            "notes": self.notes,
            "source_material_ids": self.source_material_ids,
            "status": self.status,
            "created_at": self.created_at,
            "applied_revision": self.applied_revision,
        }
