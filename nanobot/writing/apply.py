"""文章字段级写入：CAS、严格幂等、变更记录与可撤销。

与旧 ``collab/apply.py`` 的差别：这里不依赖建议/协作表，直接服务团队写作工具与
编辑器撤销。所有写路径共用一个事务，变更记录与文章版本同生共死；同一
``client_request_id`` 只能对应同一份载荷，且**不会**用 ``INSERT OR REPLACE`` 顶掉
既有的幂等记录。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import (
    ArticleLimitError,
    ArticleRecord,
    new_id,
    normalize_article_fields,
    snapshot_digest,
    utc_now_iso,
)
from nanobot.writing.repository import (
    ArticleConflict,
    ArticleNotFound,
    ArticleRequestReplay,
)

APPLY_ACTION = "writing.apply"
UNDO_ACTION = "writing.undo_change"

_ARTICLE_COLUMNS = (
    "id, workspace_key, title, writing_type, template_id, status, summary, brief, body,"
    " outline, materials, tags, revision, content_digest, created_at, updated_at"
)

_KINDS = frozenset({"body", "material", "outline"})


class WritingApplyError(ArticleLimitError):
    """写入被业务规则拒绝；message/status 直接回给工具或 WebUI。"""


@dataclass(frozen=True)
class WritingActor:
    """谁在写：成员、运行与来源消息都由服务端从可信上下文带入。"""

    member_id: str
    run_id: str = ""
    turn_id: str = ""
    message_id: str = ""


def apply_change(
    db: WritingDatabase,
    *,
    article_id: str,
    base_revision: int,
    base_digest: str,
    change: Mapping[str, Any],
    actor: WritingActor,
    request_fingerprint: str,
    client_request_id: str,
) -> dict[str, Any]:
    """单事务写入文章字段，返回提交后的 revision/digest 与 change_id。"""
    kind = str(change.get("kind") or "")
    if kind not in _KINDS:
        raise WritingApplyError("unsupported writing change kind")
    op_key = _request_key(APPLY_ACTION, {"article_id": article_id, "change": dict(change)})
    with db.connect() as conn:
        replay = _replay(conn, db.workspace_key, client_request_id, op_key)
        if replay is not None:
            return replay

        record, digest_now = _load_record(conn, db.workspace_key, article_id)
        effective_revision = int(base_revision)
        if record.revision != effective_revision or digest_now != str(base_digest):
            if not _same_actor_covers_current(
                conn,
                db.workspace_key,
                article_id,
                revision=record.revision,
                digest=digest_now,
                actor=actor,
            ):
                raise ArticleConflict(article_id, record.revision)
            # 同一 run 的同一成员：当前版本正是它上一次写入的结果。
            # 允许在这条自己的基线上继续，避免「第一次写完，后续写入必冲突」。
            effective_revision = record.revision

        snapshot = _apply_kind(record.fields, kind, change)
        fields = normalize_article_fields(snapshot)
        next_digest = snapshot_digest(fields)
        before_field, after_field = _field_payload(record.fields, fields, kind)
        if before_field == after_field:
            response = {
                "article_id": article_id,
                "revision": record.revision,
                "digest": digest_now,
                "change_id": "",
                "kind": kind,
                "no_change": True,
            }
            _record_response(
                conn, db.workspace_key, article_id, client_request_id, op_key, response,
            )
            return response

        now = utc_now_iso()
        updated = conn.execute(
            "UPDATE articles SET title = ?, writing_type = ?, template_id = ?,"
            " status = ?, summary = ?, brief = ?, body = ?, outline = ?,"
            " materials = ?, tags = ?, revision = revision + 1, content_digest = ?,"
            " updated_at = ? WHERE workspace_key = ? AND id = ? AND revision = ?",
            (
                fields["title"], fields["writing_type"], fields["template_id"],
                fields["status"], fields["summary"],
                json.dumps(fields["brief"], ensure_ascii=False), fields["body"],
                json.dumps(fields["outline"], ensure_ascii=False),
                json.dumps(fields["materials"], ensure_ascii=False),
                json.dumps(fields["tags"], ensure_ascii=False),
                next_digest, now, db.workspace_key, article_id, effective_revision,
            ),
        )
        if updated.rowcount != 1:
            latest, _digest = _load_record(conn, db.workspace_key, article_id)
            raise ArticleConflict(article_id, latest.revision)

        change_id = new_id()
        conn.execute(
            "INSERT INTO writing_article_changes (id, workspace_key, article_id, kind,"
            " before_json, after_json, actor_member, source_run_id, source_turn_id,"
            " source_message_id, request_fingerprint, result_revision, result_digest,"
            " reverted, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                change_id, db.workspace_key, article_id, kind,
                json.dumps(before_field, ensure_ascii=False),
                json.dumps(after_field, ensure_ascii=False),
                actor.member_id, actor.run_id, actor.turn_id, actor.message_id,
                request_fingerprint, effective_revision + 1, next_digest, now,
            ),
        )
        response = {
            "article_id": article_id,
            "revision": effective_revision + 1,
            "digest": next_digest,
            "change_id": change_id,
            "kind": kind,
            "no_change": False,
        }
        _record_response(
            conn, db.workspace_key, article_id, client_request_id, op_key, response,
        )
        return response


def undo_change(
    db: WritingDatabase,
    *,
    change_id: str,
    base_revision: int,
    client_request_id: str,
) -> dict[str, Any]:
    """把一次团队写入的字段还原；只有当前版本仍是该次写入的结果时才允许。"""
    op_key = _request_key(UNDO_ACTION, {"change_id": change_id, "base_revision": int(base_revision)})
    with db.connect() as conn:
        replay = _replay(conn, db.workspace_key, client_request_id, op_key)
        if replay is not None:
            return replay

        row = conn.execute(
            "SELECT id, article_id, kind, before_json, result_revision, result_digest, reverted"
            " FROM writing_article_changes WHERE workspace_key = ? AND id = ?",
            (db.workspace_key, change_id),
        ).fetchone()
        if row is None:
            raise WritingApplyError("writing change not found", status=404)
        if int(row["reverted"]) != 0:
            raise WritingApplyError("writing change already reverted", status=409)

        article_id = str(row["article_id"])
        record, digest_now = _load_record(conn, db.workspace_key, article_id)
        if (
            record.revision != int(base_revision)
            or record.revision != int(row["result_revision"])
            or digest_now != str(row["result_digest"])
        ):
            raise WritingApplyError("undo blocked by newer edits", status=409)

        snapshot = dict(record.fields)
        before = json.loads(str(row["before_json"]))
        for key, value in cast(dict[str, Any], before).items():
            snapshot[key] = value
        fields = normalize_article_fields(snapshot)
        next_digest = snapshot_digest(fields)
        now = utc_now_iso()
        updated = conn.execute(
            "UPDATE articles SET title = ?, writing_type = ?, template_id = ?,"
            " status = ?, summary = ?, brief = ?, body = ?, outline = ?,"
            " materials = ?, tags = ?, revision = revision + 1, content_digest = ?,"
            " updated_at = ? WHERE workspace_key = ? AND id = ? AND revision = ?",
            (
                fields["title"], fields["writing_type"], fields["template_id"],
                fields["status"], fields["summary"],
                json.dumps(fields["brief"], ensure_ascii=False), fields["body"],
                json.dumps(fields["outline"], ensure_ascii=False),
                json.dumps(fields["materials"], ensure_ascii=False),
                json.dumps(fields["tags"], ensure_ascii=False),
                next_digest, now, db.workspace_key, article_id, int(base_revision),
            ),
        )
        if updated.rowcount != 1:
            latest, _digest = _load_record(conn, db.workspace_key, article_id)
            raise ArticleConflict(article_id, latest.revision)
        conn.execute(
            "UPDATE writing_article_changes SET reverted = 1, reverted_at = ?"
            " WHERE workspace_key = ? AND id = ?",
            (now, db.workspace_key, change_id),
        )
        response = {
            "article_id": article_id,
            "change_id": change_id,
            "revision": int(base_revision) + 1,
            "digest": next_digest,
            "no_change": False,
        }
        _record_response(
            conn, db.workspace_key, article_id, client_request_id, op_key, response,
        )
        return response


def get_change(db: WritingDatabase, change_id: str) -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM writing_article_changes WHERE workspace_key = ? AND id = ?",
            (db.workspace_key, change_id),
        ).fetchone()
    if row is None:
        raise WritingApplyError("writing change not found", status=404)
    return {
        "change_id": str(row["id"]),
        "article_id": str(row["article_id"]),
        "kind": str(row["kind"]),
        "before": json.loads(str(row["before_json"])),
        "after": json.loads(str(row["after_json"])),
        "actor": str(row["actor_member"]),
        "result_revision": int(row["result_revision"]),
        "result_digest": str(row["result_digest"]),
        "reverted": bool(row["reverted"]),
        "created_at": str(row["created_at"]),
    }


def latest_change(db: WritingDatabase, article_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id FROM writing_article_changes WHERE workspace_key = ? AND article_id = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (db.workspace_key, article_id),
        ).fetchone()
    return None if row is None else get_change(db, str(row["id"]))


# ---- 内部 ------------------------------------------------------------------


def _apply_kind(fields: Mapping[str, Any], kind: str, change: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = dict(fields)
    if kind == "body":
        snapshot["body"] = _apply_body(str(snapshot.get("body") or ""), change)
        return snapshot
    if kind == "material":
        material = change.get("material")
        if not isinstance(material, Mapping):
            raise WritingApplyError("material is required")
        materials = list(snapshot.get("materials") or [])
        if len(materials) >= 100:
            raise WritingApplyError("article materials limit exceeded")
        materials.append(dict(cast(Mapping[str, Any], material)))
        snapshot["materials"] = materials
        return snapshot
    items = change.get("items")
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise WritingApplyError("outline items are required")
    mode = str(change.get("mode") or "replace")
    outline = list(snapshot.get("outline") or [])
    raw_items = cast(Sequence[Any], items)
    prepared: list[dict[str, Any]] = [
        dict(cast(Mapping[str, Any], item))
        if isinstance(item, Mapping)
        else {"text": item}
        for item in raw_items
    ]
    if mode == "replace":
        snapshot["outline"] = prepared
    elif mode == "append":
        snapshot["outline"] = [*outline, *prepared]
    else:
        raise WritingApplyError("unsupported outline mode")
    return snapshot


def _apply_body(body: str, change: Mapping[str, Any]) -> str:
    content = change.get("content")
    if not isinstance(content, str) or not content.strip():
        raise WritingApplyError("article body content is required")
    mode = str(change.get("mode") or "replace_range")
    if mode == "append":
        return f"{body}\n\n{content}" if body.strip() else content
    if mode == "replace_body":
        return content
    if mode != "replace_range":
        raise WritingApplyError("unsupported article body mode")
    start = change.get("start")
    end = change.get("end")
    if isinstance(start, bool) or not isinstance(start, int):
        raise WritingApplyError("selection start is required")
    if isinstance(end, bool) or not isinstance(end, int):
        raise WritingApplyError("selection end is required")
    if start < 0 or end < start or end > len(body):
        raise WritingApplyError("selection range is out of bounds")
    return f"{body[:start]}{content}{body[end:]}"


def _field_payload(
    before: Mapping[str, Any], after: Mapping[str, Any], kind: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    key = "body" if kind == "body" else ("materials" if kind == "material" else "outline")
    return {key: before.get(key, "" if key == "body" else [])}, {key: after.get(key)}


def _same_actor_covers_current(
    conn: sqlite3.Connection,
    workspace_key: str,
    article_id: str,
    *,
    revision: int,
    digest: str,
    actor: WritingActor,
) -> bool:
    """当前版本是否恰好由 ``actor`` 在同一 run 里的上一次写入产生。

    只认「最后一次变更」：中间只要有作者或其他成员写过，就不成立。这样同一成员
    连续写不会被自己的上一版拦住，而外部并发仍然按原规则冲突。
    """
    if not actor.run_id:
        return False
    row = conn.execute(
        "SELECT actor_member, source_run_id, result_revision, result_digest, reverted"
        " FROM writing_article_changes WHERE workspace_key = ? AND article_id = ?"
        " ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (workspace_key, article_id),
    ).fetchone()
    if row is None:
        return False
    return (
        str(row["actor_member"]) == actor.member_id
        and str(row["source_run_id"]) == actor.run_id
        and int(row["result_revision"]) == revision
        and str(row["result_digest"]) == digest
        and int(row["reverted"]) == 0
    )


def _load_record(
    conn: sqlite3.Connection, workspace_key: str, article_id: str,
) -> tuple[ArticleRecord, str]:
    row = conn.execute(
        f"SELECT {_ARTICLE_COLUMNS} FROM articles WHERE workspace_key = ? AND id = ?",
        (workspace_key, article_id),
    ).fetchone()
    if row is None:
        raise ArticleNotFound(article_id)
    return ArticleRecord.from_row(row), str(row["content_digest"])


def _request_key(action: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return f"{action}:{hashlib.sha256(encoded).hexdigest()[:16]}"


def _replay(
    conn: sqlite3.Connection, workspace_key: str, client_request_id: str, op_key: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT op, response FROM article_requests"
        " WHERE workspace_key = ? AND request_id = ?",
        (workspace_key, client_request_id),
    ).fetchone()
    if row is None:
        return None
    if str(row["op"]) != op_key:
        raise ArticleRequestReplay(
            "request_id was already used for a different writing mutation"
        )
    return cast(dict[str, Any], json.loads(str(row["response"])))


def _record_response(
    conn: sqlite3.Connection,
    workspace_key: str,
    article_id: str,
    client_request_id: str,
    op_key: str,
    response: Mapping[str, Any],
) -> None:
    """先核对再插入：不允许用 REPLACE 顶掉别的动作在同一 request_id 上的记录。"""
    row = conn.execute(
        "SELECT op FROM article_requests WHERE workspace_key = ? AND request_id = ?",
        (workspace_key, client_request_id),
    ).fetchone()
    if row is not None:
        if str(row["op"]) != op_key:
            raise ArticleRequestReplay(
                "request_id was already used for a different writing mutation"
            )
        return
    conn.execute(
        "INSERT INTO article_requests"
        " (request_id, workspace_key, article_id, op, response, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            client_request_id, workspace_key, article_id, op_key,
            json.dumps(dict(response), ensure_ascii=False), utc_now_iso(),
        ),
    )
