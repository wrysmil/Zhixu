"""文章快照仓储：单事务内的版本 CAS、内容摘要短路与请求幂等。

契约 §5.1 / §5.2 是行为唯一真相源；错误 message 只在 webui 层拼装，
本模块抛类型化异常（ArticleNotFound / ArticleConflict / 复用
ArticleLimitError），便于 repository 单测直接断言语义。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import (
    ARTICLE_STATUSES,
    ArticleLimitError,
    ArticleRecord,
    new_id,
    normalize_article_fields,
    snapshot_digest,
    utc_now_iso,
)

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200

_READ_COLUMNS = (
    "id, workspace_key, title, writing_type, template_id, status, summary, brief,"
    " body, outline, materials, tags, revision, created_at, updated_at"
)
_SUMMARY_COLUMNS = (
    "id, workspace_key, title, summary, status, writing_type, tags, revision,"
    " created_at, updated_at"
)


class ArticleNotFound(LookupError):  # noqa: N818  # 名称由 plan A1.3 测试与 webui 层错误映射锁定
    """文章在本工作区从未存在。"""


class ArticleConflict(ValueError):  # noqa: N818  # 同上；message 才是契约口径
    """base_revision 与服务端当前版本不一致（契约 §6）。"""

    def __init__(self, article_id: str, current_revision: int) -> None:
        super().__init__(
            f"article_conflict current_revision={current_revision} article_id={article_id}"
        )
        self.article_id = article_id
        self.current_revision = current_revision


class ArticleRequestReplay(ValueError):  # noqa: N818  # 同上
    """同一 client_request_id 被用于不同内容：拒绝，不重复执行。"""


def _payload_key(payload: Mapping[str, Any]) -> str:
    """幂等键的内容指纹：同一 request_id 只允许对应同一份写入载荷。"""
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


class ArticleRepository:
    """一个工作区一个实例；所有写操作都在单事务内完成。"""

    def __init__(self, db: WritingDatabase) -> None:
        self._db = db
        self._workspace_key = db.workspace_key

    # ---- 读取 --------------------------------------------------------------

    def get_article(self, article_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {_READ_COLUMNS} FROM articles"
                " WHERE workspace_key = ? AND id = ?",
                (self._workspace_key, article_id),
            ).fetchone()
        if row is None:
            raise ArticleNotFound(article_id)
        return {"article": ArticleRecord.from_row(row).to_article_dict()}

    def list_articles(
        self,
        *,
        query: str | None = None,
        status: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        if status not in (None, "", "all"):
            if status not in ARTICLE_STATUSES:
                raise ArticleLimitError("unsupported article status")
        resolved_limit = max(1, min(int(limit or DEFAULT_LIST_LIMIT), MAX_LIST_LIMIT))
        resolved_offset = max(0, int(offset or 0))
        clauses = ["workspace_key = ?"]
        params: list[Any] = [self._workspace_key]
        if status not in (None, "", "all"):
            clauses.append("status = ?")
            params.append(status)
        if query and query.strip():
            clauses.append("(title LIKE ? OR summary LIKE ?)")
            needle = f"%{query.strip()}%"
            params.extend([needle, needle])
        where = " AND ".join(clauses)
        with self._db.connect() as conn:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM articles WHERE {where}", params,
                ).fetchone()["n"]
            )
            rows = conn.execute(
                f"SELECT {_SUMMARY_COLUMNS} FROM articles WHERE {where}"
                " ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                [*params, resolved_limit, resolved_offset],
            ).fetchall()
        return {
            "articles": [
                ArticleRecord.from_summary_row(row).to_summary_dict() for row in rows
            ],
            "total": total,
            "limit": resolved_limit,
            "offset": resolved_offset,
        }

    # ---- 写入 --------------------------------------------------------------

    def create_article(
        self,
        client_request_id: str,
        payload: Mapping[str, Any],
        *,
        template_outlines: Mapping[str, Sequence[str]] | None = None,
    ) -> dict[str, Any]:
        op_key = f"article.create:{_payload_key(payload)}"
        replay = self._replay_response(client_request_id, op_key)
        if replay is not None:
            return replay
        fields = normalize_article_fields(payload)
        if not fields["outline"] and fields["template_id"] and template_outlines:
            seeds = template_outlines.get(fields["template_id"]) or []
            fields["outline"] = [
                {"id": new_id(), "text": text, "order": order}
                for order, text in enumerate(seeds)
            ]
        article_id = new_id()
        now = utc_now_iso()
        digest = snapshot_digest(fields)
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO articles (id, workspace_key, title, writing_type, template_id,"
                " status, summary, brief, body, outline, materials, tags, revision,"
                " content_digest, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (
                    article_id, self._workspace_key, fields["title"],
                    fields["writing_type"], fields["template_id"], fields["status"],
                    fields["summary"], json.dumps(fields["brief"], ensure_ascii=False),
                    fields["body"], json.dumps(fields["outline"], ensure_ascii=False),
                    json.dumps(fields["materials"], ensure_ascii=False),
                    json.dumps(fields["tags"], ensure_ascii=False),
                    digest, now, now,
                ),
            )
            article = self._read_one(conn, article_id)
            response = {"article": article.to_article_dict()}
            self._record_request(conn, client_request_id, article_id, op_key, response)
        return response

    def save_snapshot(
        self,
        article_id: str,
        base_revision: int,
        snapshot: Mapping[str, Any],
        *,
        client_request_id: str,
    ) -> dict[str, Any]:
        op_key = f"article.save:{article_id}:{_payload_key(snapshot)}"
        replay = self._replay_response(client_request_id, op_key)
        if replay is not None:
            return replay
        fields = normalize_article_fields(snapshot)
        with self._db.connect() as conn:
            current = self._read_revision_row(conn, article_id)
            digest_now = snapshot_digest(fields)
            if digest_now == current["content_digest"]:
                return self._finish(
                    conn, client_request_id, article_id, op_key,
                    {
                        "article_id": article_id,
                        "revision": int(current["revision"]),
                        "updated_at": current["updated_at"],
                        "digest": current["content_digest"],
                        "no_change": True,
                    },
                )
            if int(current["revision"]) != int(base_revision):
                raise ArticleConflict(article_id, int(current["revision"]))
            now = utc_now_iso()
            updated = conn.execute(
                "UPDATE articles SET title = ?, writing_type = ?, template_id = ?,"
                " status = ?, summary = ?, brief = ?, body = ?, outline = ?,"
                " materials = ?, tags = ?, revision = revision + 1, content_digest = ?,"
                " updated_at = ?"
                " WHERE workspace_key = ? AND id = ? AND revision = ?",
                (
                    fields["title"], fields["writing_type"], fields["template_id"],
                    fields["status"], fields["summary"],
                    json.dumps(fields["brief"], ensure_ascii=False), fields["body"],
                    json.dumps(fields["outline"], ensure_ascii=False),
                    json.dumps(fields["materials"], ensure_ascii=False),
                    json.dumps(fields["tags"], ensure_ascii=False),
                    digest_now, now, self._workspace_key, article_id, int(base_revision),
                ),
            )
            if updated.rowcount != 1:
                latest = self._read_revision_row(conn, article_id)
                raise ArticleConflict(article_id, int(latest["revision"]))
            return self._finish(
                conn, client_request_id, article_id, op_key,
                {
                    "article_id": article_id, "revision": int(base_revision) + 1,
                    "updated_at": now, "digest": digest_now, "no_change": False,
                },
            )

    def duplicate_article(
        self, article_id: str, base_revision: int, *, client_request_id: str,
    ) -> dict[str, Any]:
        payload_key = _payload_key({"article_id": article_id, "base_revision": base_revision})
        op_key = f"article.duplicate:{article_id}:{payload_key}"
        replay = self._replay_response(client_request_id, op_key)
        if replay is not None:
            return replay
        with self._db.connect() as conn:
            record = ArticleRecord.from_row(self._read_row(conn, article_id))
            if record.revision != int(base_revision):
                raise ArticleConflict(article_id, record.revision)
            now = utc_now_iso()
            copy_id = new_id()
            fields = dict(record.fields)
            fields["title"] = f"{fields['title']}（副本）"[:200]
            fields["outline"] = [
                {"id": new_id(), "text": item["text"], "order": item["order"]}
                for item in fields["outline"]
            ]
            fields["materials"] = [
                {**material, "id": new_id(), "created_at": now, "updated_at": now}
                for material in fields["materials"]
            ]
            digest = snapshot_digest(fields)
            conn.execute(
                "INSERT INTO articles (id, workspace_key, title, writing_type, template_id,"
                " status, summary, brief, body, outline, materials, tags, revision,"
                " content_digest, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (
                    copy_id, self._workspace_key, fields["title"],
                    fields["writing_type"], fields["template_id"], fields["status"],
                    fields["summary"], json.dumps(fields["brief"], ensure_ascii=False),
                    fields["body"], json.dumps(fields["outline"], ensure_ascii=False),
                    json.dumps(fields["materials"], ensure_ascii=False),
                    json.dumps(fields["tags"], ensure_ascii=False),
                    digest, now, now,
                ),
            )
            article = self._read_one(conn, copy_id)
            return self._finish(
                conn, client_request_id, copy_id, op_key,
                {"article": article.to_article_dict()},
            )

    def delete_article(
        self, article_id: str, base_revision: int, *, client_request_id: str,
    ) -> dict[str, Any]:
        payload_key = _payload_key({"article_id": article_id, "base_revision": base_revision})
        op_key = f"article.delete:{article_id}:{payload_key}"
        replay = self._replay_response(client_request_id, op_key)
        if replay is not None:
            return replay
        with self._db.connect() as conn:
            record = ArticleRecord.from_row(self._read_row(conn, article_id))
            if record.revision != int(base_revision):
                raise ArticleConflict(article_id, record.revision)
            conn.execute(
                "DELETE FROM articles WHERE workspace_key = ? AND id = ?",
                (self._workspace_key, article_id),
            )
            response = {"deleted_article_id": article_id, "revision": record.revision}
            conn.execute(
                "INSERT INTO article_requests"
                " (request_id, workspace_key, article_id, op, response, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    client_request_id, self._workspace_key, article_id, op_key,
                    json.dumps(response, ensure_ascii=False), utc_now_iso(),
                ),
            )
            return response

    # ---- 内部 --------------------------------------------------------------

    def _replay_response(
        self, client_request_id: str, op_key: str,
    ) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT op, response FROM article_requests"
                " WHERE workspace_key = ? AND request_id = ?",
                (self._workspace_key, client_request_id),
            ).fetchone()
            if row is None:
                return None
            if row["op"] != op_key:
                raise ArticleRequestReplay(
                    "request_id was already used for a different article mutation"
                )
            stored = json.loads(row["response"])
            if op_key.startswith("article.create") or op_key.startswith("article.duplicate"):
                current = conn.execute(
                    "SELECT revision FROM articles WHERE workspace_key = ? AND id = ?",
                    (self._workspace_key, stored.get("article", {}).get("id", "")),
                ).fetchone()
                if current is None:
                    raise ArticleLimitError("article deleted", status=409)
            return stored

    def _read_row(self, conn: sqlite3.Connection, article_id: str) -> Any:
        row = conn.execute(
            f"SELECT {_READ_COLUMNS} FROM articles WHERE workspace_key = ? AND id = ?",
            (self._workspace_key, article_id),
        ).fetchone()
        if row is None:
            raise ArticleNotFound(article_id)
        return row

    def _read_one(self, conn: sqlite3.Connection, article_id: str) -> ArticleRecord:
        return ArticleRecord.from_row(self._read_row(conn, article_id))

    def _read_revision_row(
        self, conn: sqlite3.Connection, article_id: str,
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT revision, content_digest, updated_at FROM articles"
            " WHERE workspace_key = ? AND id = ?",
            (self._workspace_key, article_id),
        ).fetchone()
        if row is None:
            if conn.execute(
                "SELECT 1 FROM article_requests"
                " WHERE workspace_key = ? AND article_id = ? AND op LIKE 'article.delete%'"
                " LIMIT 1",
                (self._workspace_key, article_id),
            ).fetchone():
                raise ArticleLimitError("article deleted", status=409)
            raise ArticleNotFound(article_id)
        return dict(row)

    def _finish(
        self,
        conn: sqlite3.Connection,
        client_request_id: str,
        article_id: str,
        op_key: str,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        self._record_request(conn, client_request_id, article_id, op_key, response)
        return response

    def _record_request(
        self,
        conn: sqlite3.Connection,
        client_request_id: str,
        article_id: str,
        op_key: str,
        response: dict[str, Any],
    ) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO article_requests"
            " (request_id, workspace_key, article_id, op, response, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                client_request_id, self._workspace_key, article_id, op_key,
                json.dumps(response, ensure_ascii=False), utc_now_iso(),
            ),
        )
