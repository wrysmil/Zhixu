"""SQLite 仓储：KB / 文档 / 块。所有 SQL 参数化。"""

from __future__ import annotations

import sqlite3

from nanobot.knowledge.database import KnowledgeDatabase
from nanobot.knowledge.models import KnowledgeBase, new_id, utc_now

_KB_FIELDS = (
    "id, name, description, workspace_id, visibility, chunk_size, chunk_overlap,"
    " embedding_backend, embedding_model, embedding_dimensions, is_enabled,"
    " created_at, updated_at"
)


_DUPLICATE_KB_NAME_MARKER = (
    "UNIQUE constraint failed: knowledge_bases.workspace_id, knowledge_bases.name"
)


def _is_duplicate_kb_name(exc: sqlite3.IntegrityError) -> bool:
    """仅当唯一约束（workspace_id, name）冲突时返回 True；CHECK 违规等返回 False。"""
    return _DUPLICATE_KB_NAME_MARKER in str(exc)


class DuplicateKnowledgeBaseError(ValueError):
    pass


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
