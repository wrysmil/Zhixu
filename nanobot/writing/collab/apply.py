"""建议采纳与撤销：单事务内的 CAS 保存、建议跃迁与撤销保护（契约 §5.3 / §5.4）。

事务边界由本模块掌握——**文章保存、采纳记录、建议状态、撤销条目同事务提交**
（spec §13 写入动作：`writing.apply` / `writing.undo` 要求同事务）。

为什么把连接借给 ``ArticleRepository``：``WritingDatabase.connect()`` 持有非重入
``threading.Lock``，且每次调用都新开连接并自行 commit/close；而契约要求采纳事务
与建议状态共用一个事务，所以在 ``db.connect()`` 块内直接调用 ``save_snapshot``
必然自锁。:class:`_SharedConnection` 把**同一个**已开启连接转交给仓储，
``connect()`` 只 yield、不新开、不提交、不关闭，因此这里既复用了
``ArticleRepository.save_snapshot`` 的 CAS 与 ``article_requests`` 幂等语义（未改
动 repository 一行），又保住了单事务。

位置单位与校验口径见 :mod:`nanobot.writing.collab.offsets`（Python 码点）。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, cast

from nanobot.writing.collab.offsets import (
    apply_outline_target,
    apply_target,
    normalize_with_map,
    resolve_range,
    validate_target,
)
from nanobot.writing.collab.repository import RequestReplayConflict
from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import ArticleLimitError, ArticleRecord, new_id, utc_now_iso
from nanobot.writing.repository import (
    ArticleConflict,
    ArticleNotFound,
    ArticleRepository,
)

APPLY_ACTION = "writing.apply"
UNDO_ACTION = "writing.undo"

_ARTICLE_COLUMNS = (
    "id, workspace_key, title, writing_type, template_id, status, summary, brief, body,"
    " outline, materials, tags, revision, content_digest, created_at, updated_at"
)
_SUGGESTION_COLUMNS = (
    "id, workspace_key, collaboration_id, artifact_id, article_id, base_revision,"
    " snapshot_digest, target_json, before_text, after_text, summary, notes,"
    " source_material_ids, status, applied_revision, created_at, applied_at"
)
_UNDO_COLUMNS = (
    "id, workspace_key, article_id, suggestion_id, before_body, before_revision,"
    " result_revision, result_digest, reverted, created_at, reverted_at"
)


class _SharedConnection:
    """把已开启的事务连接借给 ``ArticleRepository``（见模块 docstring）。"""

    def __init__(self, db: WritingDatabase, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.workspace_key = db.workspace_key

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        yield self._conn


def apply_suggestion(
    db: WritingDatabase,
    suggestion_id: str,
    *,
    article_id: str,
    base_revision: int,
    client_request_id: str,
) -> dict[str, Any]:
    """契约 §5.3：采纳单条建议，返回 ``writing.apply`` 的响应体。"""
    applied = apply_batch(
        db, [suggestion_id], article_id=article_id, base_revision=base_revision,
        client_request_id=client_request_id,
    )
    return {
        "suggestion_id": suggestion_id,
        "article_id": applied["article_id"],
        "revision": applied["revision"],
        "digest": applied["digest"],
        "staled_suggestion_ids": applied["staled_suggestion_ids"],
        "undo_id": applied["undo_id"],
    }


def apply_batch(
    db: WritingDatabase,
    suggestion_ids: Iterable[str],
    *,
    article_id: str,
    base_revision: int,
    client_request_id: str,
) -> dict[str, Any]:
    """一次提交多个**不重叠**补丁：revision 只前进一次，只写一条撤销条目。

    任一条不满足契约 §5.3 的第 1/2 步即整批失败（事务回滚，不落任何变更）；
    重叠（同一坐标或交叉）补丁按 409 拒绝，避免静默丢改写。
    """
    ordered = list(dict.fromkeys(str(item) for item in suggestion_ids))
    if not ordered:
        raise ArticleLimitError("suggestion is not pending", status=409)
    op_key = _request_key(
        APPLY_ACTION,
        {"suggestion_ids": ordered, "article_id": article_id, "base_revision": int(base_revision)},
    )
    with db.connect() as conn:
        replay = _replay_response(conn, db.workspace_key, client_request_id, op_key)
        if replay is not None:
            return replay

        rows = _suggestion_rows(conn, db.workspace_key, article_id, ordered)
        for row in rows:
            if str(row["status"]) != "pending" or int(row["base_revision"]) != int(base_revision):
                raise ArticleLimitError("suggestion is not pending", status=409)

        record, _digest = _load_article(conn, db.workspace_key, article_id)
        if record.revision != int(base_revision):
            raise ArticleConflict(article_id, record.revision)

        saved = _repository(db, conn).save_snapshot(
            article_id, int(base_revision), _apply_patches(record.fields, rows),
            client_request_id=_snapshot_request_id(client_request_id),
        )
        applied_revision = int(saved["revision"])
        now = utc_now_iso()
        placeholders = ", ".join("?" * len(ordered))
        conn.execute(
            f"UPDATE writing_collab_suggestions SET status = 'applied',"
            f" applied_revision = ?, applied_at = ? WHERE workspace_key = ?"
            f" AND id IN ({placeholders})",
            (applied_revision, now, db.workspace_key, *ordered),
        )
        staled = [
            str(row["id"]) for row in conn.execute(
                "SELECT id FROM writing_collab_suggestions WHERE workspace_key = ?"
                f" AND article_id = ? AND base_revision = ? AND status = 'pending'"
                f" AND id NOT IN ({placeholders}) ORDER BY created_at, id",
                (db.workspace_key, article_id, int(base_revision), *ordered),
            ).fetchall()
        ]
        if staled:
            staled_placeholders = ", ".join("?" * len(staled))
            conn.execute(
                "UPDATE writing_collab_suggestions SET status = 'stale'"
                f" WHERE workspace_key = ? AND id IN ({staled_placeholders})",
                (db.workspace_key, *staled),
            )
        undo_id = new_id()
        conn.execute(
            "INSERT INTO writing_collab_undo_entries (id, workspace_key, article_id,"
            " suggestion_id, before_body, before_revision, result_revision, result_digest,"
            " reverted, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                undo_id, db.workspace_key, article_id, ordered[0],
                _record_body(record), record.revision, applied_revision,
                str(saved["digest"]), now,
            ),
        )
        response: dict[str, Any] = {
            "suggestion_ids": ordered,
            "suggestion_id": ordered[0],
            "article_id": article_id,
            "revision": applied_revision,
            "digest": str(saved["digest"]),
            "staled_suggestion_ids": staled,
            "undo_id": undo_id,
        }
        _record_response(conn, db.workspace_key, article_id, client_request_id, op_key, response)
        return response


def undo(
    db: WritingDatabase,
    undo_id: str,
    *,
    base_revision: int,
    client_request_id: str,
) -> dict[str, Any]:
    """契约 §5.4：撤销采纳，产生 ``result_revision + 1``（版本号只前进）。"""
    op_key = _request_key(
        UNDO_ACTION, {"undo_id": undo_id, "base_revision": int(base_revision)},
    )
    with db.connect() as conn:
        replay = _replay_response(conn, db.workspace_key, client_request_id, op_key)
        if replay is not None:
            return replay

        entry = conn.execute(
            f"SELECT {_UNDO_COLUMNS} FROM writing_collab_undo_entries"
            " WHERE workspace_key = ? AND id = ?",
            (db.workspace_key, undo_id),
        ).fetchone()
        if entry is None:
            raise ArticleLimitError("undo entry not found", status=404)
        if int(entry["reverted"]) != 0:
            raise ArticleLimitError("undo already applied", status=409)

        article_id = str(entry["article_id"])
        record, digest_now = _load_article(conn, db.workspace_key, article_id)
        if record.revision != int(base_revision) or digest_now != str(entry["result_digest"]):
            raise ArticleLimitError("undo blocked by newer edits", status=409)

        snapshot = dict(record.fields)
        snapshot["body"] = str(entry["before_body"])
        saved = _repository(db, conn).save_snapshot(
            article_id, record.revision, snapshot,
            client_request_id=_snapshot_request_id(client_request_id),
        )
        now = utc_now_iso()
        conn.execute(
            "UPDATE writing_collab_undo_entries SET reverted = 1, reverted_at = ?"
            " WHERE workspace_key = ? AND id = ?",
            (now, db.workspace_key, undo_id),
        )
        # 同一批采纳（同一次提交）的建议一并回到 reverted；单条采纳时同版本其余建议
        # 早已是 stale，因此这里只会命中当次被采纳的那一条。
        conn.execute(
            "UPDATE writing_collab_suggestions SET status = 'reverted'"
            " WHERE workspace_key = ? AND article_id = ? AND status = 'applied'"
            " AND applied_revision = ?",
            (db.workspace_key, article_id, int(entry["result_revision"])),
        )
        response: dict[str, Any] = {
            "undo_id": undo_id,
            "article_id": article_id,
            "revision": int(saved["revision"]),
            "digest": str(saved["digest"]),
            "suggestion_id": str(entry["suggestion_id"]),
        }
        _record_response(conn, db.workspace_key, article_id, client_request_id, op_key, response)
        return response


def _repository(db: WritingDatabase, conn: sqlite3.Connection) -> ArticleRepository:
    """把当前事务连接包装成仓储可用的库（不新开连接、不自行提交）。"""
    return ArticleRepository(cast(WritingDatabase, _SharedConnection(db, conn)))


def _apply_patches(
    fields: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """按映射把若干条建议落到快照的 body / outline（其余字段原样保留）。"""
    snapshot = dict(fields)
    body_patches: list[tuple[Mapping[str, Any], str]] = []
    outline_patches: list[tuple[Mapping[str, Any], str]] = []
    for row in rows:
        target = json.loads(str(row["target_json"]) or "{}")
        after = str(row["after_text"])
        if target.get("kind") == "outline_item":
            outline_patches.append((target, after))
        else:
            body_patches.append((target, after))
    snapshot["body"] = _compose_body(str(snapshot.get("body") or ""), body_patches)
    outline = list(snapshot.get("outline") or [])
    for target, after in outline_patches:
        outline = apply_outline_target(outline, target, after.split("\n"))
    snapshot["outline"] = outline
    return snapshot


def _compose_body(body: str, patches: Sequence[tuple[Mapping[str, Any], str]]) -> str:
    """多个不重叠补丁一次合成：先按原始下标判定重叠，再从后往前落文。"""
    if not patches:
        return body
    normalized, mapping = normalize_with_map(body)
    ordered: list[tuple[int, int, Mapping[str, Any], str]] = []
    for target, after in patches:
        validate_target(normalized, target)
        raw_start, raw_end = resolve_range(normalized, mapping, target)
        ordered.append((raw_start, raw_end, target, after))
    ordered.sort(key=lambda item: (item[0], item[1]))
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] == previous[0] or current[0] < previous[1]:
            raise ArticleLimitError("suggestion targets overlap", status=409)
    composed = body
    for _start, _end, target, after in reversed(ordered):
        composed = apply_target(composed, target, after)
    return composed


def _load_article(
    conn: sqlite3.Connection, workspace_key: str, article_id: str,
) -> tuple[ArticleRecord, str]:
    """读一行的字段与 ``content_digest``；删除语义与 A 契约 §6 一致。"""
    row = conn.execute(
        f"SELECT {_ARTICLE_COLUMNS} FROM articles WHERE workspace_key = ? AND id = ?",
        (workspace_key, article_id),
    ).fetchone()
    if row is None:
        if conn.execute(
            "SELECT 1 FROM article_requests WHERE workspace_key = ? AND article_id = ?"
            " AND op LIKE 'article.delete%' LIMIT 1",
            (workspace_key, article_id),
        ).fetchone():
            raise ArticleLimitError("article deleted", status=409)
        raise ArticleNotFound(article_id)
    return ArticleRecord.from_row(row), str(row["content_digest"])


def _record_body(record: ArticleRecord) -> str:
    return str(record.fields.get("body") or "")


def _suggestion_rows(
    conn: sqlite3.Connection, workspace_key: str, article_id: str, ids: Sequence[str],
) -> list[Any]:
    placeholders = ", ".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT {_SUGGESTION_COLUMNS} FROM writing_collab_suggestions"
        f" WHERE workspace_key = ? AND article_id = ? AND id IN ({placeholders})",
        (workspace_key, article_id, *ids),
    ).fetchall()
    by_id = {str(row["id"]): row for row in rows}
    if any(item not in by_id for item in ids):
        raise ArticleLimitError("suggestion is not pending", status=409)
    return [by_id[item] for item in ids]


def _request_key(action: str, payload: Mapping[str, Any]) -> str:
    """幂等键：动作名 + 载荷指纹（同一 request_id 只允许对应同一份写入载荷）。"""
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return f"{action}:{hashlib.sha256(encoded).hexdigest()[:16]}"


def _replay_response(
    conn: sqlite3.Connection, workspace_key: str, client_request_id: str, op_key: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT op, response FROM article_requests WHERE workspace_key = ? AND request_id = ?",
        (workspace_key, client_request_id),
    ).fetchone()
    if row is None:
        return None
    if str(row["op"]) != op_key:
        raise RequestReplayConflict()
    return cast(dict[str, Any], json.loads(str(row["response"])))


def _record_response(
    conn: sqlite3.Connection,
    workspace_key: str,
    article_id: str,
    client_request_id: str,
    op_key: str,
    response: Mapping[str, Any],
) -> None:
    """写路径的幂等守卫：同 ``request_id`` 只允许同一个 ``op`` 覆盖。

    ``_replay_response`` 管读路径；``INSERT OR REPLACE`` 本身会无条件覆盖主键行，
    所以写之前必须按主键核对 ``op`` —— 否则本动作会顶掉别条流（例如
    ``article.save:…``）在同一 ``request_id`` 上留下的幂等记录。
    """
    row = conn.execute(
        "SELECT op FROM article_requests WHERE workspace_key = ? AND request_id = ?",
        (workspace_key, client_request_id),
    ).fetchone()
    if row is not None and str(row["op"]) != op_key:
        raise RequestReplayConflict()
    conn.execute(
        "INSERT OR REPLACE INTO article_requests"
        " (request_id, workspace_key, article_id, op, response, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            client_request_id, workspace_key, article_id, op_key,
            json.dumps(dict(response), ensure_ascii=False), utc_now_iso(),
        ),
    )


def _snapshot_request_id(client_request_id: str) -> str:
    """派生给同事务 ``save_snapshot`` 用的 request_id。

    采纳/撤销会在同一个事务里两次写 ``article_requests``：``save_snapshot`` 记
    ``article.save:…``，本动作记 ``writing.apply:…``/``writing.undo:…``。两条记录的主键
    都是 ``(workspace_key, request_id)``，用同一个 id 就必然一行顶掉另一行。这里按
    ``#`` 派生（客户端 request_id 的正则是 ``[A-Za-z0-9._:-]{1,128}``，**不含** ``#``，
    所以构造上撞不上客户端身份），两条记录都留下；外层动作的重放判定仍然只看本动作
    在原始 id 上的那一行，重放语义不变。
    """
    return f"{client_request_id}#snapshot"
