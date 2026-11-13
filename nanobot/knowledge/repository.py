"""SQLite 仓储：KB / 文档 / 块。所有 SQL 参数化。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from nanobot.knowledge.database import KnowledgeDatabase
from nanobot.knowledge.models import Document, KnowledgeBase, new_id, utc_now

_KB_FIELDS = (
    "id, name, description, workspace_id, visibility, chunk_size, chunk_overlap,"
    " embedding_backend, embedding_model, embedding_dimensions, is_enabled,"
    " created_at, updated_at"
)

_CHUNK_FIELDS = (
    "row_id, id, doc_id, kb_id, workspace_id, chunk_index, content, heading_path,"
    " page, locator, start_offset, end_offset, char_count, content_hash, is_enabled"
)

# Task 15 的 Ingestor 会把 index_version/vector_status 改成显式参数。
_DEFAULT_INDEX_VERSION = "norm1:tok1:chunk1"

_VECTOR_STATUSES = frozenset({"pending", "ok", "partial", "failed", "skipped"})

_INSERT_CHUNK_SQL = (
    "INSERT INTO document_chunks"
    " (id,doc_id,kb_id,workspace_id,chunk_index,content,heading_path,page,locator,"
    "start_offset,end_offset,char_count,content_hash,search_text,created_at)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)

_INSERT_QUESTION_SQL = (
    "INSERT INTO chunk_questions"
    " (id,chunk_id,doc_id,kb_id,workspace_id,question_index,question,search_text,created_at)"
    " VALUES (?,?,?,?,?,?,?,?,?)"
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


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    """待写入的块；doc_id/kb_id/workspace_id 由 commit_chunks 按文档填充。"""

    id: str
    doc_id: str
    kb_id: str
    workspace_id: str
    chunk_index: int
    content: str
    heading_path: str
    page: int | None
    locator: str
    start_offset: int
    end_offset: int
    char_count: int
    content_hash: str
    search_text: str


@dataclass(frozen=True, slots=True)
class QuestionRecord:
    """一条入库期生成的问题；`id = <chunk_id>-q<序号>`，与父块同集合索引。

    结构上满足向量层的 `ChunkMeta`（id/doc_id/kb_id/workspace_id/chunk_index），
    因此问题可以直接作为独立的向量行写入，命中后按 id 前缀回填父块。
    """

    id: str
    chunk_id: str
    doc_id: str
    kb_id: str
    workspace_id: str
    chunk_index: int
    question_index: int
    question: str
    search_text: str = ""


@dataclass(frozen=True, slots=True)
class StoredChunk:
    """document_chunks 的读模型。"""

    row_id: int
    id: str
    doc_id: str
    kb_id: str
    workspace_id: str
    chunk_index: int
    content: str
    heading_path: str
    page: int | None
    locator: str
    start_offset: int
    end_offset: int
    char_count: int
    content_hash: str
    is_enabled: bool


@dataclass(frozen=True, slots=True)
class FtsHit:
    """FTS 关键词召回的读模型；score = -bm25()，越大越相关。"""

    chunk_id: str
    doc_id: str
    kb_id: str
    chunk_index: int
    heading_path: str
    page: int | None
    locator: str
    content: str
    char_count: int
    content_hash: str
    score: float


@dataclass(frozen=True, slots=True)
class DocumentStats:
    """按 workspace 作用域的文档聚合（`stats` 用，避免整表拉行）。"""

    kbs: int
    documents: int
    chunks: int
    by_status: dict[str, int]
    vector_counts: dict[str, int]
    index_versions: dict[str, int]
    pending: int
    expired_leases: int
    stale_ready: int


# FTS 必须作为驱动表：CROSS JOIN 把「先召回、后过滤」从优化器的偶然选择
# 变成语法保证（普通 JOIN 在 SQLite 3.52 上实测 1.7 s vs 0.6 ms）。
# {kb_filter} / {scope} 只由本模块按占位符数量拼装，绝不插入用户输入。
_FTS_SQL_TEMPLATE = """
SELECT c.id            AS chunk_id,
       c.doc_id        AS doc_id,
       c.kb_id         AS kb_id,
       c.chunk_index   AS chunk_index,
       c.heading_path  AS heading_path,
       c.page          AS page,
       c.locator       AS locator,
       c.content       AS content,
       c.char_count    AS char_count,
       c.content_hash  AS content_hash,
       -bm25(document_chunks_fts) AS score
FROM document_chunks_fts
CROSS JOIN document_chunks c ON c.row_id = document_chunks_fts.rowid
JOIN documents d        ON d.id = c.doc_id
JOIN knowledge_bases kb ON kb.id = c.kb_id
WHERE document_chunks_fts MATCH ?
  AND c.is_enabled = 1
  AND d.is_enabled = 1
  AND d.parse_status = 'ready'
  AND kb.is_enabled = 1
  AND {kb_filter}{scope}
ORDER BY document_chunks_fts.rank
LIMIT ?
"""

_FTS_SCOPE_SINGLE = "(kb.visibility = 'global' OR kb.workspace_id = ?)"

#: 问题行命中的仍是父块：JOIN 回 document_chunks 后按父块去重/排序（WeKnora 口径）。
_QUESTION_FTS_SQL_TEMPLATE = """
SELECT c.id            AS chunk_id,
       c.doc_id        AS doc_id,
       c.kb_id         AS kb_id,
       c.chunk_index   AS chunk_index,
       c.heading_path  AS heading_path,
       c.page           AS page,
       c.locator       AS locator,
       c.content       AS content,
       c.char_count    AS char_count,
       c.content_hash  AS content_hash,
       -bm25(chunk_questions_fts) AS score
FROM chunk_questions_fts
JOIN chunk_questions q  ON q.row_id = chunk_questions_fts.rowid
JOIN document_chunks c  ON c.id = q.chunk_id
JOIN documents d        ON d.id = c.doc_id
JOIN knowledge_bases kb ON kb.id = c.kb_id
WHERE chunk_questions_fts MATCH ?
  AND c.is_enabled = 1
  AND d.is_enabled = 1
  AND d.parse_status = 'ready'
  AND kb.is_enabled = 1
  AND {kb_filter}{scope}
ORDER BY chunk_questions_fts.rank
LIMIT ?
"""


class _AttemptSupersededError(Exception):
    """commit_chunks 的 attempt/状态守卫失败，需要回滚整笔写入。"""


def _like_pattern(query: str) -> str:
    """块内容搜索：转义 LIKE 通配符，避免用户输入 `%`/`_` 扩大匹配面。"""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class KnowledgeRepository:
    # 单 workspace 作用域的完整查询；执行计划测试直接 EXPLAIN 它。
    FTS_SQL = _FTS_SQL_TEMPLATE.replace("{kb_filter}", "").replace(
        "{scope}", _FTS_SCOPE_SINGLE
    )

    def __init__(self, database: KnowledgeDatabase) -> None:
        self.database = database
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
        self._db.execute_write("DELETE FROM knowledge_bases WHERE id=?", (kb_id,))

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
        doc_id: str | None = None,
    ) -> Document:
        now = utc_now()
        doc_id = doc_id or new_id("doc")
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

    def list_document_titles(self, doc_ids: Iterable[str]) -> dict[str, str]:
        """批量取文档标题（精排 passage 的标题段，避免逐条 N+1 查询）。"""
        unique = [doc_id for doc_id in dict.fromkeys(doc_ids) if doc_id]
        if not unique:
            return {}
        placeholders = ",".join("?" * len(unique))
        with self._db.connect() as conn:
            rows = conn.execute(
                f"SELECT id, title FROM documents WHERE id IN ({placeholders})",
                unique,
            ).fetchall()
        return {str(row[0]): str(row[1] or "") for row in rows}

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

    def list_documents(
        self,
        *,
        kb_id: str | None = None,
        workspace_ids: list[str] | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[Document]:
        if workspace_ids is not None and not workspace_ids:
            return []
        clauses: list[str] = []
        params: list[object] = []
        if kb_id is not None:
            clauses.append("kb_id=?")
            params.append(kb_id)
        if workspace_ids is not None:
            placeholders = ",".join("?" for _ in workspace_ids)
            clauses.append(f"workspace_id IN ({placeholders})")
            params.extend(workspace_ids)
        if status is not None:
            clauses.append("parse_status=?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend((limit, offset))
        with self._db.connect() as conn:
            rows = conn.execute(
                f"SELECT {_DOC_FIELDS} FROM documents{where}"
                " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                tuple(params),
            ).fetchall()
        return [self._row_to_document(row) for row in rows]

    def list_pending_documents(self, *, limit: int = 100_000) -> list[str]:
        """启动恢复用：所有 `pending` 文档 id，按创建顺序（先到先处理）。"""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM documents WHERE parse_status='pending'"
                " ORDER BY created_at, rowid LIMIT ?",
                (limit,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def update_source(
        self,
        doc_id: str,
        *,
        file_hash: str | None = None,
        file_size: int | None = None,
        source_mtime_ns: int | None = None,
        source_path: str | None = None,
        original_name: str | None = None,
        mime: str | None = None,
    ) -> None:
        """复用已有行（同路径新版本 / 同哈希 failed 复用）：更新源指纹并回到 pending。"""
        assignments = ["parse_status='pending'", "updated_at=?"]
        params: list[object] = [utc_now()]
        updates = {
            "file_hash": file_hash,
            "file_size": file_size,
            "source_mtime_ns": source_mtime_ns,
            "source_path": source_path,
            "original_name": original_name,
            "mime": mime,
        }
        for column, value in updates.items():
            if value is not None:
                assignments.append(f"{column}=?")
                params.append(value)
        params.append(doc_id)
        with self._db.connect(immediate=True) as conn:
            conn.execute(
                f"UPDATE documents SET {', '.join(assignments)} WHERE id=?",
                tuple(params),
            )

    def list_vector_repair_candidates(self) -> list[tuple[str, str]]:
        """`ready` 但向量派生状态不完整的文档：`(doc_id, kb_id)`。"""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT id, kb_id FROM documents"
                " WHERE parse_status='ready' AND vector_status IN"
                " ('pending','partial','failed')"
                " ORDER BY created_at, rowid"
            ).fetchall()
        return [(str(row["id"]), str(row["kb_id"])) for row in rows]

    def vector_expectations(self) -> dict[str, list[str]]:
        """向量修复对账基准：`ready` 文档 → SQLite 现有 chunk id 列表。

        无块的 ready 文档映射为空列表（扫描据此清掉残留向量）；只读，不改任何行。
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT d.id AS doc_id, c.id AS chunk_id, q.id AS question_id"
                " FROM documents d"
                " LEFT JOIN document_chunks c ON c.doc_id = d.id"
                " LEFT JOIN chunk_questions q ON q.doc_id = d.id"
                " WHERE d.parse_status='ready'"
                " ORDER BY d.id, c.chunk_index, q.question_index"
            ).fetchall()
        expected: dict[str, list[str]] = {}
        seen: dict[str, set[str]] = {}
        for row in rows:
            doc_id = str(row["doc_id"])
            expected_ids = expected.setdefault(doc_id, [])
            seen_ids = seen.setdefault(doc_id, set())
            for key in ("chunk_id", "question_id"):
                value = row[key]
                # chunk × question 的笛卡尔积会重复父块 id，去重保持稳定顺序。
                if value is None:
                    continue
                text = str(value)
                if text not in seen_ids:
                    seen_ids.add(text)
                    expected_ids.append(text)
        return expected

    def stats_summary(
        self, *, workspace_ids: list[str], index_version: str
    ) -> DocumentStats:
        """一次 GROUP BY 得到统计所需的全部计数（空作用域只保底返回 KB 数）。"""
        kbs = len(self.list_kbs(workspace_ids=workspace_ids))
        if not workspace_ids:
            return DocumentStats(
                kbs=kbs,
                documents=0,
                chunks=0,
                by_status={},
                vector_counts={},
                index_versions={},
                pending=0,
                expired_leases=0,
                stale_ready=0,
            )
        placeholders = ",".join("?" for _ in workspace_ids)
        now = utc_now()
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT parse_status, vector_status, index_version, COUNT(*) AS doc_count,"
                " COALESCE(SUM(chunk_count), 0) AS chunk_sum,"
                " SUM(CASE WHEN parse_status='processing' AND lease_expires_at < ?"
                " THEN 1 ELSE 0 END) AS expired_count,"
                " SUM(CASE WHEN parse_status='ready' AND index_version != ?"
                " THEN 1 ELSE 0 END) AS stale_count"
                f" FROM documents WHERE workspace_id IN ({placeholders})"
                " GROUP BY parse_status, vector_status, index_version",
                (now, index_version, *workspace_ids),
            ).fetchall()
        by_status: dict[str, int] = {}
        vector_counts: dict[str, int] = {}
        index_versions: dict[str, int] = {}
        documents = chunks = pending = expired = stale = 0
        for row in rows:
            count = int(row["doc_count"])
            status = str(row["parse_status"])
            vector_status = str(row["vector_status"])
            version = str(row["index_version"])
            documents += count
            chunks += int(row["chunk_sum"])
            by_status[status] = by_status.get(status, 0) + count
            vector_counts[vector_status] = vector_counts.get(vector_status, 0) + count
            index_versions[version] = index_versions.get(version, 0) + count
            if status == "pending":
                pending += count
            expired += int(row["expired_count"] or 0)
            stale += int(row["stale_count"] or 0)
        return DocumentStats(
            kbs=kbs,
            documents=documents,
            chunks=chunks,
            by_status=by_status,
            vector_counts=vector_counts,
            index_versions=index_versions,
            pending=pending,
            expired_leases=expired,
            stale_ready=stale,
        )

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
        self._db.execute_write("DELETE FROM documents WHERE id=?", (doc_id,))

    # ---------- 块 ----------
    def commit_chunks(
        self,
        doc_id: str,
        *,
        attempt: int,
        chunks: list[ChunkRecord],
        index_version: str = _DEFAULT_INDEX_VERSION,
        vector_status: str | None = None,
    ) -> bool:
        """单事务重写该文档的全部块并置 ready；attempt/状态不匹配则整笔回滚。"""
        document = self.get_document(doc_id)
        if document is None:
            return False
        if vector_status is None:
            vector_status = "pending" if chunks else "skipped"
        now = utc_now()
        try:
            with self._db.connect(immediate=True) as conn:
                # 块不可变：重索引必须整体删除后重插，不能 INSERT OR REPLACE。
                conn.execute("DELETE FROM document_chunks WHERE doc_id=?", (doc_id,))
                conn.executemany(
                    _INSERT_CHUNK_SQL,
                    [
                        (
                            chunk.id,
                            doc_id,
                            document.kb_id,
                            document.workspace_id,
                            chunk.chunk_index,
                            chunk.content,
                            chunk.heading_path,
                            chunk.page,
                            chunk.locator,
                            chunk.start_offset,
                            chunk.end_offset,
                            chunk.char_count,
                            chunk.content_hash,
                            chunk.search_text,
                            now,
                        )
                        for chunk in chunks
                    ],
                )
                cursor = conn.execute(
                    "UPDATE documents SET parse_status='ready', chunk_count=?,"
                    " char_count=?, index_version=?, vector_status=?, error_code='',"
                    " error_message='', indexed_at=?, updated_at=?, lease_owner='',"
                    " lease_expires_at=''"
                    " WHERE id=? AND attempt=? AND parse_status='processing'",
                    (
                        len(chunks),
                        sum(chunk.char_count for chunk in chunks),
                        index_version,
                        vector_status,
                        now,
                        now,
                        doc_id,
                        attempt,
                    ),
                )
                if cursor.rowcount == 0:
                    raise _AttemptSupersededError
        except _AttemptSupersededError:
            return False
        return True

    def set_vector_status(
        self, doc_id: str, status: str, *, attempt: int | None = None
    ) -> bool:
        """回写向量派生状态；只作用于 `ready` 文档，可选 attempt 守卫防覆盖新结果。"""
        if status not in _VECTOR_STATUSES:
            raise ValueError(f"unknown vector status: {status}")
        clauses = ["id=?", "parse_status='ready'"]
        params: list[object] = [status, utc_now(), doc_id]
        if attempt is not None:
            clauses.append("attempt=?")
            params.append(attempt)
        with self._db.connect(immediate=True) as conn:
            cursor = conn.execute(
                f"UPDATE documents SET vector_status=?, updated_at=?"
                f" WHERE {' AND '.join(clauses)}",
                params,
            )
            return cursor.rowcount == 1

    def fetch_chunk(self, chunk_id: str) -> StoredChunk | None:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {_CHUNK_FIELDS} FROM document_chunks WHERE id=?", (chunk_id,)
            ).fetchone()
        return self._row_to_chunk(row) if row else None

    def list_chunks(
        self,
        doc_id: str,
        *,
        offset: int = 0,
        limit: int = 20,
        query: str = "",
    ) -> list[StoredChunk]:
        sql = f"SELECT {_CHUNK_FIELDS} FROM document_chunks WHERE doc_id=?"
        params: list[object] = [doc_id]
        if query:
            sql += " AND content LIKE ? ESCAPE '\\'"
            params.append(_like_pattern(query))
        sql += " ORDER BY chunk_index LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def count_chunks(self, doc_id: str, *, query: str = "") -> int:
        sql = "SELECT COUNT(*) FROM document_chunks WHERE doc_id=?"
        params: list[object] = [doc_id]
        if query:
            sql += " AND content LIKE ? ESCAPE '\\'"
            params.append(_like_pattern(query))
        with self._db.connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    def fetch_search_chunks(
        self, chunk_ids: Sequence[str], *, kb_ids: Sequence[str]
    ) -> list[StoredChunk]:
        """图候选回表复核：只信已通过 `_visible_kbs` 的 kb_ids。

        不按块 workspace 过滤——global 库的块 workspace 属于库所在工作区，
        不属于请求方。保持调用方传入的 chunk_id 顺序（即图路名次）。
        """
        if not chunk_ids or not kb_ids:
            return []
        fields = ",".join(f"c.{name.strip()}" for name in _CHUNK_FIELDS.split(","))
        chunk_placeholders = ",".join("?" for _ in chunk_ids)
        kb_placeholders = ",".join("?" for _ in kb_ids)
        sql = f"""
          SELECT {fields} FROM document_chunks c
          JOIN documents d ON d.id = c.doc_id
          JOIN knowledge_bases kb ON kb.id = c.kb_id
          WHERE c.id IN ({chunk_placeholders})
            AND c.is_enabled = 1 AND d.is_enabled = 1 AND kb.is_enabled = 1
            AND d.parse_status = 'ready'
            AND c.kb_id IN ({kb_placeholders})
        """
        with self._db.connect() as conn:
            rows = conn.execute(sql, (*chunk_ids, *kb_ids)).fetchall()
        order = {chunk_id: index for index, chunk_id in enumerate(chunk_ids)}
        chunks = [self._row_to_chunk(row) for row in rows]
        chunks.sort(key=lambda chunk: order.get(chunk.id, 10**9))
        return chunks

    def fetch_neighbors(
        self, chunk: StoredChunk, *, before: int = 1, after: int = 1
    ) -> list[StoredChunk]:
        with self._db.connect() as conn:
            rows = conn.execute(
                f"SELECT {_CHUNK_FIELDS} FROM document_chunks"
                " WHERE doc_id=? AND chunk_index BETWEEN ? AND ? AND is_enabled=1"
                " ORDER BY chunk_index",
                (chunk.doc_id, chunk.chunk_index - before, chunk.chunk_index + after),
            ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    # ---------- FTS 检索 ----------
    def search_fts(
        self,
        match_expr: str,
        *,
        workspace_ids: list[str],
        limit: int = 20,
        kb_ids: list[str] | None = None,
    ) -> list[FtsHit]:
        """关键词召回；match_expr 由 tokenizer.tokenize_query 构造并整串绑定。

        FTS5 对空表达式直接报语法错，因此空表达式在任何 SQL 之前拦下。
        """
        if not match_expr.strip():
            raise ValueError("empty FTS match expression")
        if not workspace_ids:
            return []
        if kb_ids is not None and not kb_ids:
            return []
        ws_placeholders = ",".join("?" for _ in workspace_ids)
        kb_filter = ""
        params: list[object] = [match_expr]
        if kb_ids is not None:
            kb_placeholders = ",".join("?" for _ in kb_ids)
            kb_filter = f"c.kb_id IN ({kb_placeholders}) AND "
            params.extend(kb_ids)
        params.extend(workspace_ids)
        params.append(limit)
        sql = _FTS_SQL_TEMPLATE.replace("{kb_filter}", kb_filter).replace(
            "{scope}",
            "(kb.visibility = 'global' OR kb.workspace_id IN"
            f" ({ws_placeholders}))",
        )
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            FtsHit(
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                kb_id=row["kb_id"],
                chunk_index=row["chunk_index"],
                heading_path=row["heading_path"],
                page=row["page"],
                locator=row["locator"],
                content=row["content"],
                char_count=row["char_count"],
                content_hash=row["content_hash"],
                score=float(row["score"]),
            )
            for row in rows
        ]

    def search_questions_fts(
        self,
        match_expr: str,
        *,
        workspace_ids: list[str],
        limit: int = 20,
        kb_ids: list[str] | None = None,
    ) -> list[FtsHit]:
        """问题索引的关键词召回；返回父块（`chunk_id` 是父块，不是问题行）。"""
        if not match_expr.strip():
            raise ValueError("empty FTS match expression")
        if not workspace_ids:
            return []
        if kb_ids is not None and not kb_ids:
            return []
        ws_placeholders = ",".join("?" for _ in workspace_ids)
        kb_filter = ""
        params: list[object] = [match_expr]
        if kb_ids is not None:
            kb_placeholders = ",".join("?" for _ in kb_ids)
            kb_filter = f"c.kb_id IN ({kb_placeholders}) AND "
            params.extend(kb_ids)
        params.extend(workspace_ids)
        params.append(limit)
        sql = _QUESTION_FTS_SQL_TEMPLATE.replace("{kb_filter}", kb_filter).replace(
            "{scope}",
            "(kb.visibility = 'global' OR kb.workspace_id IN"
            f" ({ws_placeholders}))",
        )
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        # 一条块可能有多条问题同时命中：按父块去重，保留最高分（同块同答案）。
        best: dict[str, FtsHit] = {}
        for row in rows:
            hit = FtsHit(
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                kb_id=row["kb_id"],
                chunk_index=row["chunk_index"],
                heading_path=row["heading_path"],
                page=row["page"],
                locator=row["locator"],
                content=row["content"],
                char_count=row["char_count"],
                content_hash=row["content_hash"],
                score=float(row["score"]),
            )
            current = best.get(hit.chunk_id)
            if current is None or hit.score > current.score:
                best[hit.chunk_id] = hit
        return sorted(best.values(), key=lambda hit: (-hit.score, hit.chunk_id))

    def replace_questions(
        self, doc_id: str, questions: Iterable[QuestionRecord]
    ) -> None:
        """原子替换一篇文档的全部生成问题；空列表等价于清空（重索引/关闭开关）。"""
        rows = list(questions)
        now = utc_now()
        with self._db.connect(immediate=True) as conn:
            conn.execute("DELETE FROM chunk_questions WHERE doc_id=?", (doc_id,))
            if rows:
                conn.executemany(
                    _INSERT_QUESTION_SQL,
                    [
                        (
                            item.id,
                            item.chunk_id,
                            doc_id,
                            item.kb_id,
                            item.workspace_id,
                            item.question_index,
                            item.question,
                            item.search_text,
                            now,
                        )
                        for item in rows
                    ],
                )

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> StoredChunk:
        return StoredChunk(
            row_id=row["row_id"],
            id=row["id"],
            doc_id=row["doc_id"],
            kb_id=row["kb_id"],
            workspace_id=row["workspace_id"],
            chunk_index=row["chunk_index"],
            content=row["content"],
            heading_path=row["heading_path"],
            page=row["page"],
            locator=row["locator"],
            start_offset=row["start_offset"],
            end_offset=row["end_offset"],
            char_count=row["char_count"],
            content_hash=row["content_hash"],
            is_enabled=bool(row["is_enabled"]),
        )

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
