"""SQLite 仓储：KB / 文档 / 块。所有 SQL 参数化。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from nanobot.knowledge.database import KnowledgeDatabase
from nanobot.knowledge.models import Document, KnowledgeBase, new_id, utc_now

_KB_FIELDS = (
    "id, name, description, workspace_id, visibility, chunk_size, chunk_overlap,"
    " embedding_backend, embedding_model, embedding_dimensions, is_enabled,"
    " created_at, updated_at"
)

_DOC_FIELDS = (
    "id, kb_id, workspace_id, title, source_type, source_kind, source_path,"
    " original_name, mime, file_hash, file_size, source_mtime_ns, parse_status,"
    " error_code, error_message, attempt, lease_owner, lease_expires_at,"
    " index_version, vector_status, chunk_count, char_count, is_enabled,"
    " created_at, updated_at, indexed_at"
)

_CLAIM_SQL = (
    "UPDATE documents"
    " SET parse_status='processing', attempt=attempt+1, lease_owner=?,"
    " lease_expires_at=?, error_code='', error_message='', updated_at=?"
    " WHERE id=?"
    " AND (parse_status IN ('pending','failed')"
    " OR (parse_status='processing' AND lease_expires_at < ?))"
    " RETURNING attempt"
)


_DUPLICATE_KB_NAME_MARKER = (
    "UNIQUE constraint failed: knowledge_bases.workspace_id, knowledge_bases.name"
)

_DUPLICATE_DOC_PATH_MARKER = (
    "UNIQUE constraint failed: documents.kb_id, documents.source_path"
)


def _is_duplicate_kb_name(exc: sqlite3.IntegrityError) -> bool:
    """仅当唯一约束（workspace_id, name）冲突时返回 True；CHECK 违规等返回 False。"""
    return _DUPLICATE_KB_NAME_MARKER in str(exc)


def _is_duplicate_doc_path(exc: sqlite3.IntegrityError) -> bool:
    """仅当唯一索引（kb_id, source_path）冲突时为 True；其他违规为 False。"""
    return _DUPLICATE_DOC_PATH_MARKER in str(exc)


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


class DuplicateKnowledgeBaseError(ValueError):
    pass


class DuplicateDocumentPathError(sqlite3.IntegrityError):
    """同一 KB 内 source_path 重复；仍可被 sqlite3.IntegrityError 捕获。"""


class KnowledgeRepository:
    def __init__(self, database: KnowledgeDatabase) -> None:
        self._db = database

    # ---------- KB ----------
    def create_kb(
        self,
        *,
        name: str,
        workspace_id: str = "default",
        description: str = "",
        visibility: str = "workspace",
        chunk_size: int = 800,
        chunk_overlap: int = 120,
        embedding_backend: str = "none",
        embedding_model: str = "",
        embedding_dimensions: int = 0,
    ) -> KnowledgeBase:
        now = utc_now()
        kb = KnowledgeBase(
            id=new_id("kb"),
            name=name,
            description=description,
            workspace_id=workspace_id,
            visibility=visibility,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            embedding_backend=embedding_backend,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            is_enabled=True,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._db.connect(immediate=True) as conn:
                conn.execute(
                    "INSERT INTO knowledge_bases (id,name,description,workspace_id,"
                    "visibility,chunk_size,chunk_overlap,embedding_backend,"
                    "embedding_model,embedding_dimensions,is_enabled,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        kb.id,
                        kb.name,
                        kb.description,
                        kb.workspace_id,
                        kb.visibility,
                        kb.chunk_size,
                        kb.chunk_overlap,
                        kb.embedding_backend,
                        kb.embedding_model,
                        kb.embedding_dimensions,
                        1,
                        kb.created_at,
                        kb.updated_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if _is_duplicate_kb_name(exc):
                raise DuplicateKnowledgeBaseError(name) from exc
            raise
        return kb

    def get_kb(self, kb_id: str) -> KnowledgeBase | None:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {_KB_FIELDS} FROM knowledge_bases WHERE id=?", (kb_id,)
            ).fetchone()
        return self._row_to_kb(row) if row else None

    def list_kbs(
        self, *, workspace_ids: list[str], include_global: bool = True
    ) -> list[KnowledgeBase]:
        if workspace_ids:
            placeholders = ",".join("?" for _ in workspace_ids)
            scope = f"workspace_id IN ({placeholders})"
            if include_global:
                scope = f"(visibility='global' OR {scope})"
        elif include_global:
            scope = "visibility='global'"
        else:
            return []
        with self._db.connect() as conn:
            query = f"SELECT {_KB_FIELDS} FROM knowledge_bases WHERE {scope}"
            rows = conn.execute(
                query + " ORDER BY created_at, rowid", tuple(workspace_ids)
            ).fetchall()
        return [self._row_to_kb(row) for row in rows]

    def update_kb(self, kb_id: str, **fields: object) -> KnowledgeBase:
        if "workspace_id" in fields:
            raise ValueError("KB workspace_id is immutable")
        allowed = {
            "name",
            "description",
            "is_enabled",
            "chunk_size",
            "chunk_overlap",
            "embedding_backend",
            "embedding_model",
            "embedding_dimensions",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown kb fields: {sorted(unknown)}")
        if not fields:
            kb = self.get_kb(kb_id)
            if kb is None:
                raise KeyError(kb_id)
            return kb
        assignments = ", ".join(f"{key}=?" for key in fields)
        values: list[object] = [int(v) if isinstance(v, bool) else v for v in fields.values()]
        try:
            with self._db.connect(immediate=True) as conn:
                row = conn.execute(
                    f"UPDATE knowledge_bases SET {assignments}, updated_at=? WHERE id=?"
                    f" RETURNING {_KB_FIELDS}",
                    (*values, utc_now(), kb_id),
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            if _is_duplicate_kb_name(exc):
                raise DuplicateKnowledgeBaseError(str(fields.get("name", kb_id))) from exc
            raise
        if row is None:
            raise KeyError(kb_id)
        return self._row_to_kb(row)

    def delete_kb(self, kb_id: str) -> None:
        with self._db.connect(immediate=True) as conn:
            conn.execute("DELETE FROM knowledge_bases WHERE id=?", (kb_id,))

    # ---------- 文档 ----------
    def insert_document(
        self,
        *,
        kb_id: str,
        workspace_id: str,
        title: str,
        source_kind: str,
        source_path: str,
        original_name: str = "",
        mime: str = "",
        file_hash: str = "",
        file_size: int = 0,
        source_mtime_ns: int = 0,
        source_type: str = "file",
    ) -> Document:
        now = utc_now()
        doc_id = new_id("doc")
        try:
            with self._db.connect(immediate=True) as conn:
                conn.execute(
                    "INSERT INTO documents (id,kb_id,workspace_id,title,source_type,"
                    "source_kind,source_path,original_name,mime,file_hash,file_size,"
                    "source_mtime_ns,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        doc_id,
                        kb_id,
                        workspace_id,
                        title,
                        source_type,
                        source_kind,
                        source_path,
                        original_name,
                        mime,
                        file_hash,
                        file_size,
                        source_mtime_ns,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            if _is_duplicate_doc_path(exc):
                raise DuplicateDocumentPathError(f"{kb_id}:{source_path}") from exc
            raise
        document = self.get_document(doc_id)
        assert document is not None
        return document

    def get_document(self, doc_id: str) -> Document | None:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {_DOC_FIELDS} FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
        return self._row_to_document(row) if row else None

    def find_by_path(self, kb_id: str, source_path: str) -> Document | None:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {_DOC_FIELDS} FROM documents WHERE kb_id=? AND source_path=?",
                (kb_id, source_path),
            ).fetchone()
        return self._row_to_document(row) if row else None

    def find_by_hash(
        self,
        kb_id: str,
        file_hash: str,
        *,
        statuses: tuple[str, ...] = ("ready", "processing", "pending"),
    ) -> Document | None:
        if not file_hash:
            return None
        placeholders = ",".join("?" for _ in statuses)
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {_DOC_FIELDS} FROM documents"
                f" WHERE kb_id=? AND file_hash=? AND parse_status IN ({placeholders})"
                " ORDER BY created_at DESC LIMIT 1",
                (kb_id, file_hash, *statuses),
            ).fetchone()
        return self._row_to_document(row) if row else None

    def claim_document(
        self,
        doc_id: str,
        *,
        lease_owner: str,
        lease_seconds: int = 600,
        now: str | None = None,
    ) -> int | None:
        """原子领取 pending/failed 或租约过期的 processing 行，返回新 attempt。"""
        now = now or utc_now()
        expires = (_iso(now) + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
        with self._db.connect(immediate=True) as conn:
            row = conn.execute(
                _CLAIM_SQL, (lease_owner, expires, now, doc_id, now)
            ).fetchone()
        return int(row[0]) if row else None

    def commit_document(
        self,
        doc_id: str,
        *,
        attempt: int,
        chunk_count: int,
        char_count: int,
        index_version: str,
        vector_status: str,
    ) -> bool:
        now = utc_now()
        with self._db.connect(immediate=True) as conn:
            cursor = conn.execute(
                "UPDATE documents SET parse_status='ready', chunk_count=?, char_count=?,"
                " index_version=?, vector_status=?, error_code='', error_message='',"
                " indexed_at=?, updated_at=?, lease_owner='', lease_expires_at=''"
                " WHERE id=? AND attempt=? AND parse_status='processing'",
                (chunk_count, char_count, index_version, vector_status, now, now,
                 doc_id, attempt),
            )
            return cursor.rowcount == 1

    def fail_document(
        self,
        doc_id: str,
        *,
        attempt: int,
        error_code: str,
        error_message: str,
    ) -> bool:
        with self._db.connect(immediate=True) as conn:
            cursor = conn.execute(
                "UPDATE documents SET parse_status='failed', error_code=?,"
                " error_message=?, lease_owner='', lease_expires_at='', updated_at=?"
                " WHERE id=? AND attempt=? AND parse_status='processing'",
                (error_code, error_message[:2000], utc_now(), doc_id, attempt),
            )
            return cursor.rowcount == 1

    def reset_expired_leases(self, *, now: str | None = None) -> list[str]:
        now = now or utc_now()
        with self._db.connect(immediate=True) as conn:
            rows = conn.execute(
                "SELECT id FROM documents"
                " WHERE parse_status='processing' AND lease_expires_at < ?",
                (now,),
            ).fetchall()
            ids = [str(row[0]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    "UPDATE documents SET parse_status='pending', lease_owner='',"
                    " lease_expires_at='', updated_at=?"
                    f" WHERE parse_status='processing' AND id IN ({placeholders})",
                    (now, *ids),
                )
        return ids

    def mark_reindex_pending(self, doc_id: str) -> bool:
        with self._db.connect(immediate=True) as conn:
            cursor = conn.execute(
                "UPDATE documents SET parse_status='pending', updated_at=?"
                " WHERE id=? AND parse_status IN ('ready','failed')",
                (utc_now(), doc_id),
            )
            return cursor.rowcount == 1

    def delete_document(self, doc_id: str) -> None:
        with self._db.connect(immediate=True) as conn:
            conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))

    @staticmethod
    def _row_to_document(row: sqlite3.Row) -> Document:
        return Document(
            id=row["id"],
            kb_id=row["kb_id"],
            workspace_id=row["workspace_id"],
            title=row["title"],
            source_type=row["source_type"],
            source_kind=row["source_kind"],
            source_path=row["source_path"],
            original_name=row["original_name"],
            mime=row["mime"],
            file_hash=row["file_hash"],
            file_size=row["file_size"],
            source_mtime_ns=row["source_mtime_ns"],
            parse_status=row["parse_status"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            attempt=row["attempt"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            index_version=row["index_version"],
            vector_status=row["vector_status"],
            chunk_count=row["chunk_count"],
            char_count=row["char_count"],
            is_enabled=bool(row["is_enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            indexed_at=row["indexed_at"],
        )

    @staticmethod
    def _row_to_kb(row: sqlite3.Row) -> KnowledgeBase:
        return KnowledgeBase(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            workspace_id=row["workspace_id"],
            visibility=row["visibility"],
            chunk_size=row["chunk_size"],
            chunk_overlap=row["chunk_overlap"],
            embedding_backend=row["embedding_backend"],
            embedding_model=row["embedding_model"],
            embedding_dimensions=row["embedding_dimensions"],
            is_enabled=bool(row["is_enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
