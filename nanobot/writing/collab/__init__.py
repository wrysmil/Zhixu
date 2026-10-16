"""创作空间固定团队的协作持久化层（协作 / 批次 / 产物 / 交接 / 建议）。

本包是契约 §1/§2/§4 的唯一落库出口：

- :mod:`~nanobot.writing.collab.database` —— 协作域 DDL 与幂等补建；
- :mod:`~nanobot.writing.collab.models` —— 领域对象与枚举白名单；
- :mod:`~nanobot.writing.collab.repository` —— 单事务内的 CAS、幂等与启动收敛。

接口形状的唯一真相源是
``.ai-runtime-artifacts/contracts/2026-10-17-contract-writing-team-collab.md``；
正文定位与建议采纳见本包的兄弟模块 ``offsets`` / ``apply``。
"""

from __future__ import annotations

from nanobot.writing.collab.database import COLLAB_SCHEMA_STATEMENTS, ensure_collab_schema
from nanobot.writing.collab.models import (
    ACTIONS,
    ACTIVE_BATCH_STATUSES,
    ARTIFACT_KINDS,
    ARTIFACT_ROLE_IDS,
    BATCH_STATUSES,
    COLLAB_STAGES,
    COLLAB_STATUSES,
    HANDOFF_KINDS,
    HANDOFF_STATUSES,
    IN_FLIGHT_STATUSES,
    SUGGESTION_STATUSES,
    Action,
    Artifact,
    ArtifactKind,
    Batch,
    BatchStatus,
    Collaboration,
    CollaborationStatus,
    Handoff,
    HandoffKind,
    HandoffStatus,
    InvalidEnumValueError,
    Stage,
    Suggestion,
    SuggestionStatus,
    artifact_digest,
    normalize_enum,
)
from nanobot.writing.collab.repository import (
    ActiveBatchConflict,
    ArtifactNotFound,
    BatchNotFound,
    CollaborationNotFound,
    CollabRepository,
    CollabSnapshot,
    CollabVersionConflict,
    HandoffNotFound,
    InvalidFieldError,
    NoActiveBatch,
    RequestReplayConflict,
)

__all__ = [
    "ACTIONS",
    "ACTIVE_BATCH_STATUSES",
    "ARTIFACT_KINDS",
    "ARTIFACT_ROLE_IDS",
    "BATCH_STATUSES",
    "COLLAB_SCHEMA_STATEMENTS",
    "COLLAB_STAGES",
    "COLLAB_STATUSES",
    "HANDOFF_KINDS",
    "HANDOFF_STATUSES",
    "IN_FLIGHT_STATUSES",
    "SUGGESTION_STATUSES",
    "Action",
    "ActiveBatchConflict",
    "Artifact",
    "ArtifactKind",
    "ArtifactNotFound",
    "Batch",
    "BatchNotFound",
    "BatchStatus",
    "CollabRepository",
    "CollabSnapshot",
    "CollabVersionConflict",
    "Collaboration",
    "CollaborationNotFound",
    "CollaborationStatus",
    "Handoff",
    "HandoffKind",
    "HandoffNotFound",
    "HandoffStatus",
    "InvalidEnumValueError",
    "InvalidFieldError",
    "NoActiveBatch",
    "RequestReplayConflict",
    "Stage",
    "Suggestion",
    "SuggestionStatus",
    "artifact_digest",
    "ensure_collab_schema",
    "normalize_enum",
]
