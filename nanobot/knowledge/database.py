"""SQLite 结构真源：连接口径、DDL 与版本迁移。"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = "1"


class KnowledgeSchemaTooNewError(RuntimeError):
    def __init__(self, current: str, supported: str) -> None:
        super().__init__(f"KNOWLEDGE_SCHEMA_TOO_NEW: db={current} code={supported}")


_V1_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS _schema_meta (
      key   TEXT PRIMARY KEY,
      value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_bases (
      id                   TEXT    PRIMARY KEY,
      name                 TEXT    NOT NULL,
      description          TEXT    NOT NULL DEFAULT '',
      workspace_id         TEXT    NOT NULL DEFAULT 'default',
      visibility           TEXT    NOT NULL DEFAULT 'workspace'
                           CHECK (visibility IN ('workspace','global')),
      chunk_size           INTEGER NOT NULL DEFAULT 800
                           CHECK (chunk_size BETWEEN 200 AND 4000),
      chunk_overlap        INTEGER NOT NULL DEFAULT 120
                           CHECK (chunk_overlap BETWEEN 0 AND 1000),
      embedding_backend    TEXT    NOT NULL DEFAULT 'none'
                           CHECK (embedding_backend IN ('local','remote','none')),
      embedding_model      TEXT    NOT NULL DEFAULT '',
      embedding_dimensions INTEGER NOT NULL DEFAULT 0
                           CHECK (embedding_dimensions >= 0),
      is_enabled           INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1)),
      created_at           TEXT    NOT NULL,
      updated_at           TEXT    NOT NULL,
      UNIQUE (workspace_id, name),
      CHECK (chunk_overlap < chunk_size)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_kb_scope ON knowledge_bases(workspace_id, visibility)",
    """
    CREATE TABLE IF NOT EXISTS documents (
      id                TEXT    PRIMARY KEY,
      kb_id             TEXT    NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
      workspace_id      TEXT    NOT NULL DEFAULT 'default',
      title             TEXT    NOT NULL,
      source_type       TEXT    NOT NULL DEFAULT 'file' CHECK (source_type IN ('file')),
      source_kind       TEXT    NOT NULL DEFAULT 'external'
                        CHECK (source_kind IN ('external','managed')),
      source_path       TEXT    NOT NULL,
      original_name     TEXT    NOT NULL DEFAULT '',
      mime              TEXT    NOT NULL DEFAULT '',
      file_hash         TEXT    NOT NULL DEFAULT '',
      file_size         INTEGER NOT NULL DEFAULT 0,
      source_mtime_ns   INTEGER NOT NULL DEFAULT 0,
      parse_status      TEXT    NOT NULL DEFAULT 'pending'
                        CHECK (parse_status IN ('pending','processing','ready','failed')),
      error_code        TEXT    NOT NULL DEFAULT '',
      error_message     TEXT    NOT NULL DEFAULT '',
      attempt           INTEGER NOT NULL DEFAULT 0,
      lease_owner       TEXT    NOT NULL DEFAULT '',
      lease_expires_at  TEXT    NOT NULL DEFAULT '',
      index_version     TEXT    NOT NULL DEFAULT '',
      vector_status     TEXT    NOT NULL DEFAULT 'pending'
                        CHECK (vector_status IN ('pending','ok','partial','failed','skipped')),
      chunk_count       INTEGER NOT NULL DEFAULT 0,
      char_count        INTEGER NOT NULL DEFAULT 0,
      is_enabled        INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1)),
      created_at        TEXT    NOT NULL,
      updated_at        TEXT    NOT NULL,
      indexed_at        TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_docs_kb_status ON documents(kb_id, parse_status)",
    "CREATE INDEX IF NOT EXISTS idx_docs_hash ON documents(kb_id, file_hash)",
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_docs_path"
        " ON documents(kb_id, source_path)"
    ),
    "CREATE INDEX IF NOT EXISTS idx_docs_lease ON documents(parse_status, lease_expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_docs_vector ON documents(parse_status, vector_status)",
    """
    CREATE TABLE IF NOT EXISTS document_chunks (
      row_id         INTEGER PRIMARY KEY,
      id             TEXT    NOT NULL UNIQUE,
      doc_id         TEXT    NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
      kb_id          TEXT    NOT NULL,
      workspace_id   TEXT    NOT NULL DEFAULT 'default',
      chunk_index    INTEGER NOT NULL,
      content        TEXT    NOT NULL,
      heading_path   TEXT    NOT NULL DEFAULT '',
      page           INTEGER,
      locator        TEXT    NOT NULL DEFAULT '',
      start_offset   INTEGER NOT NULL DEFAULT 0,
      end_offset     INTEGER NOT NULL DEFAULT 0,
      char_count     INTEGER NOT NULL DEFAULT 0,
      content_hash   TEXT    NOT NULL DEFAULT '',
      search_text    TEXT    NOT NULL DEFAULT '',
      is_enabled     INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1)),
      created_at     TEXT    NOT NULL,
      UNIQUE (doc_id, chunk_index),
      CHECK (end_offset >= start_offset)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chunks_kb ON document_chunks(kb_id, is_enabled)",
    """
    CREATE TRIGGER IF NOT EXISTS document_chunks_immutable
    BEFORE UPDATE OF content, search_text, heading_path, doc_id, chunk_index, kb_id, workspace_id
    ON document_chunks
    BEGIN
      SELECT RAISE(ABORT, 'CHUNK_IMMUTABLE');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS document_chunks_no_replace
    BEFORE INSERT ON document_chunks
    WHEN EXISTS (SELECT 1 FROM document_chunks WHERE id = new.id)
      OR EXISTS (
        SELECT 1 FROM document_chunks
        WHERE doc_id = new.doc_id AND chunk_index = new.chunk_index
      )
    BEGIN
      SELECT RAISE(ABORT, 'CHUNK_IMMUTABLE');
    END
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
      search_text,
      id            UNINDEXED,
      kb_id         UNINDEXED,
      workspace_id  UNINDEXED,
      content='document_chunks',
      content_rowid='row_id',
      detail=column,
      tokenize='unicode61 remove_diacritics 2'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS document_chunks_ai AFTER INSERT ON document_chunks BEGIN
      INSERT INTO document_chunks_fts(rowid, search_text, id, kb_id, workspace_id)
      VALUES (new.row_id, new.search_text, new.id, new.kb_id, new.workspace_id);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS document_chunks_ad AFTER DELETE ON document_chunks BEGIN
      INSERT INTO document_chunks_fts(
        document_chunks_fts, rowid, search_text, id, kb_id, workspace_id
      )
      VALUES ('delete', old.row_id, old.search_text, old.id, old.kb_id, old.workspace_id);
    END
    """,
)

_MIGRATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (("1", _V1_STATEMENTS),)


class KnowledgeDatabase:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    @contextmanager
    def connect(self, *, immediate: bool = False) -> Generator[sqlite3.Connection, None, None]:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, isolation_level=None, timeout=15)
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = FULL")
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("PRAGMA busy_timeout = 15000")
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                try:
                    yield conn
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
            finally:
                conn.close()

    def init_schema(self) -> None:
        with self.connect(immediate=True) as conn:
            row = conn.execute(
                "SELECT value FROM _schema_meta WHERE key='version'"
            ).fetchone() if self._has_meta(conn) else None
            current = str(row[0]) if row else None
            if current is not None and int(current) > int(SCHEMA_VERSION):
                raise KnowledgeSchemaTooNewError(current, SCHEMA_VERSION)
            for version, statements in _MIGRATIONS:
                if current is not None and int(version) <= int(current):
                    continue
                for statement in statements:
                    conn.execute(statement)
            conn.execute(
                "INSERT OR REPLACE INTO _schema_meta(key, value) VALUES ('version', ?)",
                (SCHEMA_VERSION,),
            )

    @staticmethod
    def _has_meta(conn: sqlite3.Connection) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_schema_meta'"
            ).fetchone()
            is not None
        )
