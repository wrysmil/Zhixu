"""协作仓储：单事务内完成幂等重放、活动批次唯一、版本 CAS、产物只追加与启动收敛。

契约 §4 的错误 message 由本模块抛出（异常带 ``status``），WebUI 层只做透传。
SQLite 事务一律在方法内部闭合：**不得**跨 LLM / 网络等待，事件循环里的调用由调用方
用 ``asyncio.to_thread`` 包裹。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from nanobot.webui.session_identity import (
    WEBUI_SESSION_STORAGE_PREFIX,
    is_valid_webui_chat_id,
)
from nanobot.writing.collab.database import ensure_collab_schema
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
    SUGGESTION_TARGET_KINDS,
    Artifact,
    Batch,
    Collaboration,
    CollaborationStatus,
    Handoff,
    InvalidEnumValueError,
    Suggestion,
    artifact_digest,
    normalize_enum,
    normalize_optional_enum,
)
from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import new_id, utc_now_iso

DEFAULT_SNAPSHOT_LIMIT = 50
MAX_SNAPSHOT_LIMIT = 200

_COLLAB_COLUMNS = (
    "id, workspace_key, article_id, chat_id, session_key, goal_json, status, stage,"
    " active_batch_id, cancel_generation, version, seq, last_handoff_id, created_at, updated_at"
)


class CollaborationNotFound(LookupError):  # noqa: N818  # message 由契约 §4 锁定
    """协作在本工作区从未存在。"""

    def __init__(self, collaboration_id: str) -> None:
        super().__init__(f"collaboration not found: {collaboration_id}")
        self.status = 404


class BatchNotFound(LookupError):  # noqa: N818
    def __init__(self, batch_id: str) -> None:
        super().__init__(f"batch not found: {batch_id}")
        self.status = 404


class ArtifactNotFound(LookupError):  # noqa: N818
    def __init__(self, artifact_id: str) -> None:
        super().__init__(f"artifact not found: {artifact_id}")
        self.status = 404


class HandoffNotFound(LookupError):  # noqa: N818
    def __init__(self, handoff_id: str) -> None:
        super().__init__(f"handoff not found: {handoff_id}")
        self.status = 404


class InvalidFieldError(ValueError):
    """字段不合法（如 chat_id 不在白名单）：message 含字段名与非法值。"""

    def __init__(self, field: str, value: object) -> None:
        super().__init__(f"unsupported collaboration field: {field}={value!r}")
        self.field = field
        self.value = value
        self.status = 400


class ActiveBatchConflict(ValueError):  # noqa: N818  # 名称由契约 §4 错误语义与测试锁定
    """契约 §4：该文章已有活动批次。"""

    def __init__(self, article_id: str) -> None:
        super().__init__(f"collaboration already has an active batch for article_id={article_id}")
        self.article_id = article_id
        self.status = 409


class CollabVersionConflict(ValueError):  # noqa: N818  # 名称由契约 §4 错误语义与测试锁定
    """契约 §4：协作版本过期；``kind="handoff"`` 时是交接版本过期。"""

    def __init__(self, *, expected: object, actual: object, kind: str = "collaboration") -> None:
        super().__init__(f"{kind} version conflict expected={expected} actual={actual}")
        self.expected = expected
        self.actual = actual
        self.kind = kind
        self.status = 409


class NoActiveBatch(ValueError):  # noqa: N818  # 名称由契约 §4 错误语义与测试锁定
    """契约 §4：无活动批次可取消。"""

    def __init__(self, collaboration_id: str) -> None:
        super().__init__(f"no active batch for collaboration_id={collaboration_id}")
        self.status = 409


class RequestReplayConflict(ValueError):  # noqa: N818  # 名称由契约 §4 错误语义与测试锁定
    """同一 ``client_request_id`` 被用于不同载荷：拒绝，不重复执行。"""

    def __init__(self) -> None:
        super().__init__("request_id was already used for a different WebUI mutation")
        self.status = 409


@dataclass(frozen=True, slots=True)
class CollabSnapshot:
    """``writing.collab.get`` 的读投影：列表不含产物全文。"""

    collaboration: Collaboration | None
    batches: list[Batch]
    artifacts: list[dict[str, Any]]
    handoffs: list[Handoff]
    suggestions: list[Suggestion]
    last_version: int
    last_seq: int

    @property
    def next_actions(self) -> list[str]:
        if self.collaboration is None or self.collaboration.status == CollaborationStatus.IDLE:
            return ["start_full", "ideate"]
        if self.collaboration.status in ACTIVE_BATCH_STATUSES:
            return []
        if self.collaboration.status == CollaborationStatus.WAITING_AUTHOR:
            return ["reply", "outline", "draft"]
        return ["start_full", "ideate", "reply"]

    @property
    def blocked_reason(self) -> str | None:
        status = self.collaboration.status if self.collaboration else None
        if status == CollaborationStatus.WAITING_AUTHOR:
            return "waiting_author"
        if status == CollaborationStatus.INTERRUPTED:
            return "interrupted"
        if status == CollaborationStatus.FAILED:
            return "failed"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "collaboration": self.collaboration.to_dict() if self.collaboration else None,
            "batches": [item.to_dict() for item in self.batches],
            "artifacts": list(self.artifacts),
            "handoffs": [item.to_dict() for item in self.handoffs],
            "suggestions": [item.to_dict() for item in self.suggestions],
            "last_version": self.last_version,
            "last_seq": self.last_seq,
            "next_actions": self.next_actions,
            "blocked_reason": self.blocked_reason,
        }


def _require_id(field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidFieldError(field, value)
    return value


def _require_int(field: str, value: object) -> int:
    if isinstance(value, bool):
        raise InvalidFieldError(field, value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value)
        except ValueError as error:
            raise InvalidFieldError(field, value) from error
    if isinstance(value, float):
        return int(value)
    raise InvalidFieldError(field, value)


def _optional_int(field: str, value: object) -> int | None:
    if value is None or value == "":
        return None
    return _require_int(field, value)


def _json_text(value: object, default: str) -> str:
    if value is None or value == "":
        return default
    return json.dumps(value, ensure_ascii=False, default=str)


def _payload_key(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _request_op(action: str, *identity: str, payload: Mapping[str, Any]) -> str:
    """幂等 ``op``：动作 + 身份段（协作/文章）+ 实际载荷指纹（契约 §2 / §10.4）。

    身份段是明文，便于人工核对「这一行属于哪个协作/文章」；指纹段保证同一动作下任何
    载荷差异（快照摘要、指令、goal）都算成异 ``op`` —— 同 ``request_id`` 撞上异 ``op``
    就是 409，绝不静默执行两次。
    """
    return f"{':'.join((action, *identity))}:{_payload_key(payload)}"


class CollabRepository:
    """一个工作区一个实例；所有写操作都在单事务内完成。"""

    def __init__(self, db: WritingDatabase) -> None:
        self._db = db
        self._workspace_key = db.workspace_key
        # 契约 §2 的双出口拆分：协作表由本包自管。幂等补建（主库 DDL + 协作 DDL，
        # 幂等键要复用 article_requests），保证任何入口拿到 repository 就能用，
        # 不依赖调用方记得先跑 ensure_schema / ensure_collab_schema。
        db.ensure_schema()
        ensure_collab_schema(db)

    # ---- 读取 --------------------------------------------------------------

    def get_collaboration(self, collaboration_id: str) -> Collaboration:
        with self._db.connect() as conn:
            return self._require_collaboration(conn, collaboration_id)

    def get_by_article(self, article_id: str) -> Collaboration | None:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {_COLLAB_COLUMNS} FROM writing_collaborations"
                " WHERE workspace_key = ? AND article_id = ?"
                " ORDER BY updated_at DESC, id LIMIT 1",
                (self._workspace_key, article_id),
            ).fetchone()
        return Collaboration.from_row(row) if row is not None else None

    def get_batch(self, batch_id: str) -> Batch:
        with self._db.connect() as conn:
            return self._require_batch(conn, batch_id)

    def list_batches(self, collaboration_id: str, *, limit: int = 200) -> list[Batch]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM writing_collab_batches"
                " WHERE workspace_key = ? AND collaboration_id = ?"
                " ORDER BY attempt ASC, created_at ASC, id ASC LIMIT ?",
                (self._workspace_key, collaboration_id, max(1, int(limit))),
            ).fetchall()
        return [Batch.from_row(row) for row in rows]

    def get_artifact(self, artifact_id: str) -> Artifact:
        """详情投影：含产物全文。"""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM writing_collab_artifacts WHERE workspace_key = ? AND id = ?",
                (self._workspace_key, artifact_id),
            ).fetchone()
        if row is None:
            raise ArtifactNotFound(artifact_id)
        return Artifact.from_row(row)

    def list_artifacts(self, collaboration_id: str, *, limit: int = 200) -> dict[str, Any]:
        """列表投影：不含产物全文（契约 §3）。"""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM writing_collab_artifacts"
                " WHERE workspace_key = ? AND collaboration_id = ?"
                " ORDER BY created_at ASC, id ASC LIMIT ?",
                (self._workspace_key, collaboration_id, max(1, int(limit))),
            ).fetchall()
        return {
            str(row["id"]): Artifact.from_row(row).to_listing_dict()
            for row in rows
        }

    def get_handoff(self, handoff_id: str) -> Handoff:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM writing_collab_handoffs WHERE workspace_key = ? AND id = ?",
                (self._workspace_key, handoff_id),
            ).fetchone()
        if row is None:
            raise HandoffNotFound(handoff_id)
        return Handoff.from_row(row)

    def list_handoffs(self, collaboration_id: str, *, limit: int = 200) -> list[Handoff]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM writing_collab_handoffs"
                " WHERE workspace_key = ? AND collaboration_id = ?"
                " ORDER BY created_at ASC, id ASC LIMIT ?",
                (self._workspace_key, collaboration_id, max(1, int(limit))),
            ).fetchall()
        return [Handoff.from_row(row) for row in rows]

    def list_suggestions(self, collaboration_id: str, *, limit: int = 200) -> list[Suggestion]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM writing_collab_suggestions"
                " WHERE workspace_key = ? AND collaboration_id = ?"
                " ORDER BY created_at ASC, id ASC LIMIT ?",
                (self._workspace_key, collaboration_id, max(1, int(limit))),
            ).fetchall()
        return [Suggestion.from_row(row) for row in rows]

    def snapshot(self, article_id: str, *, limit: int = DEFAULT_SNAPSHOT_LIMIT) -> CollabSnapshot:
        """按文章聚合读投影；产物只给列表投影（详情走 ``get_artifact``）。"""
        resolved = max(1, min(int(limit or DEFAULT_SNAPSHOT_LIMIT), MAX_SNAPSHOT_LIMIT))
        collaboration = self.get_by_article(article_id)
        if collaboration is None:
            return CollabSnapshot(
                collaboration=None, batches=[], artifacts=[], handoffs=[],
                suggestions=[], last_version=0, last_seq=0,
            )
        listings = self.list_artifacts(collaboration.id, limit=resolved)
        return CollabSnapshot(
            collaboration=collaboration,
            batches=self.list_batches(collaboration.id, limit=resolved),
            artifacts=[listings[key] for key in sorted(listings)],
            handoffs=self.list_handoffs(collaboration.id, limit=resolved),
            suggestions=self.list_suggestions(collaboration.id, limit=resolved),
            last_version=collaboration.version,
            last_seq=collaboration.seq,
        )

    # ---- 写入 --------------------------------------------------------------

    def create_collaboration(
        self,
        *,
        article_id: str,
        chat_id: str,
        session_key: str,
        goal: Mapping[str, Any] | None,
        request_id: str,
    ) -> Collaboration:
        """一篇文章一个协作；同 ``request_id`` 重放返回既有行。

        ``client_request_id`` 的真相源是 ``article_requests``（PK 工作区 + request_id）：
        先查重、命中即返回，**不**二次执行，也不新增行。
        """
        article_id = _require_id("article_id", article_id)
        request_id = _require_id("request_id", request_id)
        _validate_chat_id(chat_id)
        expected_session = f"{WEBUI_SESSION_STORAGE_PREFIX}{chat_id}"
        if session_key != expected_session:
            # 契约 §2：会话键是唯一构造口径，服务端不接受调用方自造
            raise InvalidFieldError("session_key", session_key)
        goal_payload = _normalize_goal(goal)

        op_key = _request_op(
            "writing.collab.create", article_id,
            payload={"chat_id": chat_id, "session_key": session_key, "goal": goal_payload},
        )
        with self._db.connect() as conn:
            replay = self._replay(conn, request_id, op_key)
            if replay is not None:
                return self._require_collaboration(
                    conn, str(replay.get("collaboration_id", "")),
                )
            existing_row = conn.execute(
                f"SELECT {_COLLAB_COLUMNS} FROM writing_collaborations"
                " WHERE workspace_key = ? AND article_id = ? LIMIT 1",
                (self._workspace_key, article_id),
            ).fetchone()
            if existing_row is not None:
                # 一篇文章一个协作：已有即返回（不改绑定，避免撞掉活动批次）
                current = Collaboration.from_row(existing_row)
                self._record_request(
                    conn, request_id, article_id, op_key,
                    {"collaboration_id": current.id, "replayed": True},
                )
                return current

            collaboration_id = new_id()
            now = utc_now_iso()
            conn.execute(
                "INSERT INTO writing_collaborations (id, workspace_key, article_id, chat_id,"
                " session_key, goal_json, status, stage, active_batch_id, cancel_generation,"
                " version, seq, last_handoff_id, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'idle', '', NULL, 0, 1, 0, NULL, ?, ?)",
                (
                    collaboration_id, self._workspace_key, article_id, chat_id, session_key,
                    json.dumps(goal_payload, ensure_ascii=False), now, now,
                ),
            )
            created = self._require_collaboration(conn, collaboration_id)
            self._record_request(
                conn, request_id, article_id, op_key,
                {"collaboration_id": collaboration_id, "replayed": False},
            )
            return created

    def create_batch(
        self,
        *,
        collaboration_id: str,
        action: str,
        base_revision: int,
        snapshot_digest: str,
        instruction: str = "",
        frozen: Mapping[str, Any] | None = None,
        expect_version: int | None = None,
        request_id: str,
    ) -> Batch:
        """新建批次；已有活动批次则 409（契约 §4）。"""
        request_id = _require_id("request_id", request_id)
        action = normalize_enum("action", action, ACTIONS)
        base_revision = _require_int("base_revision", base_revision)
        snapshot_digest = _require_id("snapshot_digest", snapshot_digest)
        expected = _optional_int("expect_version", expect_version)
        frozen_payload = dict(frozen or {})

        with self._db.connect() as conn:
            collaboration = self._require_collaboration(conn, collaboration_id)
            # 幂等键带协作与文章身份段 + 载荷指纹：同 request_id 换文章/换协作/换载荷
            # 都算异 op（409），绝不静默重放别人的批次。
            op_key = _request_op(
                "writing.collab.start", collaboration_id, collaboration.article_id,
                payload={
                    "action": action, "base_revision": base_revision,
                    "snapshot_digest": snapshot_digest, "instruction": instruction or "",
                    "frozen": frozen_payload,
                },
            )
            replay = self._replay(conn, request_id, op_key)
            if replay is not None:
                return self._require_batch(conn, str(replay.get("batch_id", "")))

            if expected is not None and expected != collaboration.version:
                raise CollabVersionConflict(expected=expected, actual=collaboration.version)

            active = conn.execute(
                "SELECT COUNT(*) AS n FROM writing_collab_batches"
                " WHERE workspace_key = ? AND collaboration_id = ?"
                f" AND status IN ({','.join('?' * len(ACTIVE_BATCH_STATUSES))})",
                (self._workspace_key, collaboration_id, *ACTIVE_BATCH_STATUSES),
            ).fetchone()
            if int(active["n"]) > 0:
                raise ActiveBatchConflict(collaboration.article_id)

            attempt_row = conn.execute(
                "SELECT COALESCE(MAX(attempt), 0) + 1 AS next FROM writing_collab_batches"
                " WHERE workspace_key = ? AND collaboration_id = ?",
                (self._workspace_key, collaboration_id),
            ).fetchone()
            attempt = int(attempt_row["next"])
            batch_id = new_id()
            now = utc_now_iso()
            conn.execute(
                "INSERT INTO writing_collab_batches (id, workspace_key, collaboration_id,"
                " attempt, team_run_id, base_revision, snapshot_digest, action, instruction,"
                " status, cancel_generation, frozen_json, error_message, created_at, finished_at)"
                " VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, 'queued', ?, ?, '', ?, NULL)",
                (
                    batch_id, self._workspace_key, collaboration_id, attempt, base_revision,
                    snapshot_digest, action, instruction or "",
                    collaboration.cancel_generation,
                    _json_text(frozen_payload, "{}"), now,
                ),
            )
            conn.execute(
                "UPDATE writing_collaborations SET active_batch_id = ?,"
                " status = CASE WHEN status IN ('queued', 'running', 'waiting_author')"
                " THEN status ELSE 'queued' END,"
                " version = version + 1, updated_at = ?"
                " WHERE workspace_key = ? AND id = ?",
                (batch_id, now, self._workspace_key, collaboration_id),
            )
            created = self._require_batch(conn, batch_id)
            self._record_request(
                conn, request_id, collaboration.article_id, op_key,
                {"collaboration_id": collaboration_id, "batch_id": batch_id},
            )
            return created

    def finish_batch(
        self,
        *,
        batch_id: str,
        status: str,
        error_message: str | None = None,
        cancel_generation: int | None = None,
    ) -> Batch:
        """收尾批次；协作随之推进（含 ``waiting_author`` 与释放 ``active_batch_id``）。"""
        status = normalize_enum("batch.status", status, BATCH_STATUSES)
        with self._db.connect() as conn:
            batch = self._require_batch(conn, batch_id)
            now = utc_now_iso()
            generation = (
                batch.cancel_generation
                if cancel_generation is None
                else _require_int("cancel_generation", cancel_generation)
            )
            conn.execute(
                "UPDATE writing_collab_batches SET status = ?, error_message = ?,"
                " cancel_generation = ?, finished_at = ? WHERE workspace_key = ? AND id = ?",
                (
                    status, error_message or "", generation, now,
                    self._workspace_key, batch_id,
                ),
            )
            conn.execute(
                "UPDATE writing_collaborations SET"
                " active_batch_id = CASE WHEN active_batch_id = ? THEN NULL"
                " ELSE active_batch_id END,"
                " status = CASE WHEN active_batch_id = ? THEN ? ELSE status END,"
                " version = version + 1, updated_at = ?"
                " WHERE workspace_key = ? AND id = ?",
                (batch_id, batch_id, status, now, self._workspace_key, batch.collaboration_id),
            )
            return self._require_batch(conn, batch_id)

    def set_collaboration_status(
        self,
        *,
        collaboration_id: str,
        status: str,
        stage: str | None = None,
        active_batch_id: object = ...,
        blocked_reason: str | None = None,
        expect_version: int | None,
    ) -> Collaboration:
        """协作状态 CAS；``stage=None`` 表示不改，``active_batch_id=...`` 表示不改。"""
        status = normalize_enum("collaboration.status", status, COLLAB_STATUSES)
        stage = normalize_optional_enum("collaboration.stage", stage, COLLAB_STAGES)
        expected = _optional_int("expect_version", expect_version)
        with self._db.connect() as conn:
            current = self._require_collaboration(conn, collaboration_id)
            if expected is not None and expected != current.version:
                raise CollabVersionConflict(expected=expected, actual=current.version)
            batch_id = (
                current.active_batch_id
                if active_batch_id is ...
                else cast("str | None", active_batch_id)
            )
            now = utc_now_iso()
            cursor = conn.execute(
                "UPDATE writing_collaborations SET status = ?,"
                " stage = COALESCE(?, stage), active_batch_id = ?,"
                " version = version + 1, updated_at = ?"
                " WHERE workspace_key = ? AND id = ? AND version = ?",
                (
                    status, stage, batch_id, now,
                    self._workspace_key, collaboration_id, current.version,
                ),
            )
            if cursor.rowcount != 1:
                latest = self._require_collaboration(conn, collaboration_id)
                raise CollabVersionConflict(expected=current.version, actual=latest.version)
            return self._require_collaboration(conn, collaboration_id)

    def add_artifact(
        self,
        *,
        collaboration_id: str,
        batch_id: str,
        attempt: int,
        kind: str,
        role_id: str,
        content: Mapping[str, Any],
        source_ids: Sequence[str],
        gaps: Sequence[Any],
    ) -> Artifact:
        """产物只追加：同 ``(collaboration_id, kind)`` 下 ``version`` 递增，旧版本保留。"""
        kind = normalize_enum("artifact.kind", kind, ARTIFACT_KINDS)
        role_id = normalize_enum("artifact.role_id", role_id, ARTIFACT_ROLE_IDS)
        attempt = _require_int("attempt", attempt)
        payload = dict(content or {})
        digest = artifact_digest(payload)
        with self._db.connect() as conn:
            self._require_collaboration(conn, collaboration_id)
            self._require_batch(conn, batch_id)
            version_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next FROM writing_collab_artifacts"
                " WHERE workspace_key = ? AND collaboration_id = ? AND kind = ?",
                (self._workspace_key, collaboration_id, kind),
            ).fetchone()
            version = int(version_row["next"])
            artifact_id = new_id()
            now = utc_now_iso()
            conn.execute(
                "INSERT INTO writing_collab_artifacts (id, workspace_key, collaboration_id,"
                " batch_id, attempt, kind, role_id, version, content_json, content_digest,"
                " source_ids, gaps, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)",
                (
                    artifact_id, self._workspace_key, collaboration_id, batch_id, attempt,
                    kind, role_id, version,
                    json.dumps(payload, ensure_ascii=False, default=str), digest,
                    json.dumps(list(source_ids or []), ensure_ascii=False, default=str),
                    json.dumps(list(gaps or []), ensure_ascii=False, default=str), now,
                ),
            )
            conn.execute(
                "UPDATE writing_collaborations SET version = version + 1, updated_at = ?"
                " WHERE workspace_key = ? AND id = ?",
                (now, self._workspace_key, collaboration_id),
            )
            return Artifact.from_row(
                conn.execute(
                    "SELECT * FROM writing_collab_artifacts WHERE workspace_key = ? AND id = ?",
                    (self._workspace_key, artifact_id),
                ).fetchone()
            )

    def add_handoff(
        self,
        *,
        collaboration_id: str,
        batch_id: str,
        artifact_id: str,
        kind: str,
        artifact_version: int,
    ) -> Handoff:
        kind = normalize_enum("handoff.kind", kind, HANDOFF_KINDS)
        artifact_version = _require_int("artifact_version", artifact_version)
        with self._db.connect() as conn:
            self._require_collaboration(conn, collaboration_id)
            self._require_batch(conn, batch_id)
            handoff_id = new_id()
            now = utc_now_iso()
            conn.execute(
                "INSERT INTO writing_collab_handoffs (id, workspace_key, collaboration_id,"
                " batch_id, artifact_id, kind, artifact_version, status, created_at, resolved_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)",
                (
                    handoff_id, self._workspace_key, collaboration_id, batch_id, artifact_id,
                    kind, artifact_version, now,
                ),
            )
            conn.execute(
                "UPDATE writing_collaborations SET last_handoff_id = ?, status ="
                " CASE WHEN status IN ('queued', 'running') THEN 'waiting_author' ELSE status END,"
                " version = version + 1, updated_at = ? WHERE workspace_key = ? AND id = ?",
                (handoff_id, now, self._workspace_key, collaboration_id),
            )
            return Handoff.from_row(
                conn.execute(
                    "SELECT * FROM writing_collab_handoffs WHERE workspace_key = ? AND id = ?",
                    (self._workspace_key, handoff_id),
                ).fetchone()
            )

    def resolve_handoff(
        self, *, handoff_id: str, expected_version: int, status: str,
    ) -> Handoff:
        """交接 CAS：版本号是 ``artifact_version`` 整数；不匹配抛
        ``handoff version conflict expected=<n> actual=<n>``（契约 §4）。"""
        status = normalize_enum("handoff.status", status, HANDOFF_STATUSES)
        expected = _require_int("expected_version", expected_version)
        with self._db.connect() as conn:
            current = self._require_handoff(conn, handoff_id)
            if expected != current.artifact_version:
                raise CollabVersionConflict(
                    expected=expected, actual=current.artifact_version, kind="handoff",
                )
            now = utc_now_iso()
            cursor = conn.execute(
                "UPDATE writing_collab_handoffs SET status = ?, resolved_at = ?"
                " WHERE workspace_key = ? AND id = ? AND artifact_version = ?",
                (status, now, self._workspace_key, handoff_id, expected),
            )
            if cursor.rowcount != 1:
                latest = self._require_handoff(conn, handoff_id)
                raise CollabVersionConflict(
                    expected=expected, actual=latest.artifact_version, kind="handoff",
                )
            return self._require_handoff(conn, handoff_id)

    def advance_seq(self, collaboration_id: str) -> int:
        """``seq`` 严格递增：单条 UPDATE 完成，天然串行，跨批次共享同一计数器。"""
        with self._db.connect() as conn:
            cursor = conn.execute(
                "UPDATE writing_collaborations SET seq = seq + 1,"
                " updated_at = ? WHERE workspace_key = ? AND id = ?",
                (utc_now_iso(), self._workspace_key, collaboration_id),
            )
            row = conn.execute(
                "SELECT seq FROM writing_collaborations WHERE workspace_key = ? AND id = ?",
                (self._workspace_key, collaboration_id),
            ).fetchone()
        if cursor.rowcount != 1 or row is None:
            raise CollaborationNotFound(collaboration_id)
        return int(row["seq"])

    def converge_interrupted(self, workspace_key: str) -> int:
        """进程启动收敛：``queued|running`` 批次标 ``interrupted``，已交付产物一律保留。"""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT id, collaboration_id FROM writing_collab_batches"
                " WHERE workspace_key = ? AND status IN (?, ?)",
                (workspace_key, *IN_FLIGHT_STATUSES),
            ).fetchall()
            if not rows:
                return 0
            now = utc_now_iso()
            conn.execute(
                "UPDATE writing_collab_batches SET status = 'interrupted', finished_at = ?"
                " WHERE workspace_key = ? AND status IN (?, ?)",
                (now, workspace_key, *IN_FLIGHT_STATUSES),
            )
            collaboration_ids = sorted(
                {str(row["collaboration_id"]) for row in rows}
            )
            for collaboration_id in collaboration_ids:
                conn.execute(
                    "UPDATE writing_collaborations SET status = 'interrupted',"
                    " active_batch_id = NULL, version = version + 1, updated_at = ?"
                    " WHERE workspace_key = ? AND id = ?"
                    " AND status IN ('queued', 'running')",
                    (now, workspace_key, collaboration_id),
                )
        return len(rows)

    # ---- 内部 --------------------------------------------------------------

    def _require_collaboration(
        self, conn: sqlite3.Connection, collaboration_id: str,
    ) -> Collaboration:
        row = conn.execute(
            f"SELECT {_COLLAB_COLUMNS} FROM writing_collaborations"
            " WHERE workspace_key = ? AND id = ?",
            (self._workspace_key, collaboration_id),
        ).fetchone()
        if row is None:
            raise CollaborationNotFound(collaboration_id)
        return Collaboration.from_row(row)

    def _require_batch(self, conn: sqlite3.Connection, batch_id: str) -> Batch:
        row = conn.execute(
            "SELECT * FROM writing_collab_batches WHERE workspace_key = ? AND id = ?",
            (self._workspace_key, batch_id),
        ).fetchone()
        if row is None:
            raise BatchNotFound(batch_id)
        return Batch.from_row(row)

    def _require_handoff(self, conn: sqlite3.Connection, handoff_id: str) -> Handoff:
        row = conn.execute(
            "SELECT * FROM writing_collab_handoffs WHERE workspace_key = ? AND id = ?",
            (self._workspace_key, handoff_id),
        ).fetchone()
        if row is None:
            raise HandoffNotFound(handoff_id)
        return Handoff.from_row(row)

    def _replay(
        self, conn: sqlite3.Connection, request_id: str, op_key: str,
    ) -> dict[str, Any] | None:
        """幂等重放：同 request_id 只在 ``op`` 一致时返回既有响应。"""
        row = conn.execute(
            "SELECT op, response FROM article_requests WHERE workspace_key = ? AND request_id = ?",
            (self._workspace_key, request_id),
        ).fetchone()
        if row is None:
            return None
        if row["op"] != op_key:
            raise RequestReplayConflict()
        try:
            stored = json.loads(row["response"])
        except ValueError as error:  # 库内响应损坏：直接暴露，不静默重放
            raise RequestReplayConflict() from error
        if not isinstance(stored, Mapping):
            raise RequestReplayConflict()
        return dict(cast(Mapping[str, Any], stored))

    def _record_request(
        self,
        conn: sqlite3.Connection,
        request_id: str,
        article_id: str,
        op_key: str,
        response: Mapping[str, Any],
    ) -> None:
        """写路径的幂等守卫：同 ``request_id`` 只允许同一个 ``op`` + 同一篇文章。

        :meth:`_replay` 是**读路径**的守卫；这里是**写路径**的守卫，因为
        ``INSERT OR REPLACE`` 本身会无条件覆盖主键行：任何绕过 ``_replay`` 的写入或
        与别的域（共用 ``article_requests`` 主键）并发/嵌套的写入，都会用一份异载荷
        静默顶掉既有行，让「同 id 异载荷 → 409」在写路径失效。守卫只在真重放
        （同 ``op`` 同文章）时放行替换，其余一律 :class:`RequestReplayConflict`。
        """
        row = conn.execute(
            "SELECT op, article_id FROM article_requests"
            " WHERE workspace_key = ? AND request_id = ?",
            (self._workspace_key, request_id),
        ).fetchone()
        if row is not None and (
            str(row["op"]) != op_key or str(row["article_id"]) != article_id
        ):
            raise RequestReplayConflict()
        conn.execute(
            "INSERT OR REPLACE INTO article_requests"
            " (request_id, workspace_key, article_id, op, response, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                request_id, self._workspace_key, article_id, op_key,
                json.dumps(dict(response), ensure_ascii=False), utc_now_iso(),
            ),
        )


def _validate_chat_id(chat_id: object) -> str:
    if not is_valid_webui_chat_id(chat_id):
        raise InvalidFieldError("chat_id", chat_id)
    return cast(str, chat_id)


def _normalize_goal(goal: Mapping[str, Any] | None) -> dict[str, Any]:
    """目标载荷：动作与目标类型同样按契约 §1 白名单校验。"""
    payload = dict(goal or {})
    if "action" in payload and payload["action"] is not None:
        payload["action"] = normalize_enum("action", payload["action"], ACTIONS)
    target = payload.get("target")
    if isinstance(target, Mapping):
        target_payload = dict(cast(Mapping[str, Any], target))
        if "kind" in target_payload and target_payload["kind"] is not None:
            target_payload["kind"] = normalize_enum(
                "target.kind", target_payload["kind"], SUGGESTION_TARGET_KINDS,
            )
        payload["target"] = target_payload
    if "suggestion_status" in payload and payload["suggestion_status"] is not None:
        payload["suggestion_status"] = normalize_enum(
            "suggestion.status", payload["suggestion_status"], SUGGESTION_STATUSES,
        )
    return payload


__all__ = [
    "ActiveBatchConflict",
    "ArtifactNotFound",
    "BatchNotFound",
    "CollabRepository",
    "CollabSnapshot",
    "CollabVersionConflict",
    "CollaborationNotFound",
    "HandoffNotFound",
    "InvalidEnumValueError",
    "InvalidFieldError",
    "NoActiveBatch",
    "RequestReplayConflict",
]
