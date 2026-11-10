import sqlite3

import pytest

from nanobot.knowledge.database import KnowledgeDatabase, KnowledgeSchemaTooNewError


@pytest.fixture
def db(tmp_path):
    database = KnowledgeDatabase(tmp_path / "knowledge" / "state.db")
    database.init_schema()
    return database


def test_init_schema_is_idempotent(db):
    db.init_schema()
    with db.connect() as conn:
        version = conn.execute(
            "SELECT value FROM _schema_meta WHERE key='version'"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        triggers = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
    assert version == "2"
    assert {
        "knowledge_bases",
        "documents",
        "document_chunks",
        "document_chunks_fts",
        "chunk_questions",
        "chunk_questions_fts",
    } <= tables
    assert {
        "document_chunks_immutable",
        "document_chunks_no_replace",
        "document_chunks_ai",
        "document_chunks_ad",
        "chunk_questions_ai",
        "chunk_questions_ad",
    } <= triggers


def test_refuses_newer_schema(db):
    with db.connect(immediate=True) as conn:
        conn.execute("UPDATE _schema_meta SET value='99' WHERE key='version'")
    with pytest.raises(KnowledgeSchemaTooNewError):
        db.init_schema()


def test_connect_rolls_back_on_exception(db):
    with pytest.raises(RuntimeError):
        with db.connect(immediate=True) as conn:
            conn.execute(
                "INSERT INTO knowledge_bases (id,name,created_at,updated_at)"
                " VALUES ('kb_x','x','t','t')"
            )
            raise RuntimeError("boom")
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM knowledge_bases").fetchone()[0] == 0


def test_insert_or_replace_cannot_bypass_chunk_immutability(db):
    with db.connect(immediate=True) as conn:
        conn.execute(
            "INSERT INTO knowledge_bases (id,name,created_at,updated_at)"
            " VALUES ('kb','n','t','t')"
        )
        conn.execute(
            "INSERT INTO documents (id,kb_id,title,source_path,created_at,updated_at)"
            " VALUES ('doc','kb','t','/t.md','t','t')"
        )
        conn.execute(
            "INSERT INTO document_chunks"
            " (id,doc_id,kb_id,chunk_index,content,search_text,created_at)"
            " VALUES ('chk','doc','kb',0,'原始正文','原始正文','t')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect(immediate=True) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO document_chunks"
                " (id,doc_id,kb_id,chunk_index,content,search_text,created_at)"
                " VALUES ('chk','doc','kb',0,'被篡改','被篡改','t')"
            )
    with db.connect() as conn:
        content = conn.execute(
            "SELECT content FROM document_chunks WHERE id='chk'"
        ).fetchone()[0]
        hits = conn.execute(
            "SELECT count(*) FROM document_chunks_fts"
            " WHERE document_chunks_fts MATCH '原始正文'"
        ).fetchone()[0]
    assert content == "原始正文"
    assert hits == 1


def test_wal_and_foreign_keys_are_on(db):
    with db.connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_parent_directory_is_created(tmp_path):
    path = tmp_path / "a" / "b" / "state.db"
    KnowledgeDatabase(path).init_schema()
    assert path.exists()
