import hashlib
import sqlite3

import pytest

import nanobot.knowledge.repository as repository_module
from nanobot.knowledge.repository import ChunkRecord, DuplicateKnowledgeBaseError
from nanobot.knowledge.tokenizer import tokenize_index, tokenize_query


def test_create_and_get_kb(repo):
    kb = repo.create_kb(name="产品手册", workspace_id="ws1")
    assert kb.id.startswith("kb_")
    assert repo.get_kb(kb.id).name == "产品手册"


def test_kb_name_unique_per_workspace(repo):
    repo.create_kb(name="手册", workspace_id="ws1")
    with pytest.raises(DuplicateKnowledgeBaseError):
        repo.create_kb(name="手册", workspace_id="ws1")
    assert repo.create_kb(name="手册", workspace_id="ws2").workspace_id == "ws2"


def test_list_kbs_respects_scope_and_global(repo):
    repo.create_kb(name="a", workspace_id="ws1")
    repo.create_kb(name="b", workspace_id="ws2")
    repo.create_kb(name="g", workspace_id="ws9", visibility="global")
    names = {kb.name for kb in repo.list_kbs(workspace_ids=["ws1"])}
    assert names == {"a", "g"}


def test_list_kbs_without_global_scope(repo):
    repo.create_kb(name="a", workspace_id="ws1")
    repo.create_kb(name="g", workspace_id="ws9", visibility="global")
    names = {kb.name for kb in repo.list_kbs(workspace_ids=["ws1"], include_global=False)}
    assert names == {"a"}


def test_list_kbs_empty_scope_is_safe(repo):
    repo.create_kb(name="a", workspace_id="ws1")
    repo.create_kb(name="g", workspace_id="ws9", visibility="global")
    assert {kb.name for kb in repo.list_kbs(workspace_ids=[])} == {"g"}
    assert repo.list_kbs(workspace_ids=[], include_global=False) == []


def test_update_kb_and_immutable_workspace(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    updated = repo.update_kb(kb.id, description="新描述", is_enabled=False)
    assert updated.description == "新描述"
    assert updated.is_enabled is False
    with pytest.raises(ValueError):
        repo.update_kb(kb.id, workspace_id="ws2")


def test_delete_kb_cascades(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    repo.delete_kb(kb.id)
    assert repo.get_kb(kb.id) is None


def test_create_kb_validation_error_is_not_duplicate(repo):
    with pytest.raises(sqlite3.IntegrityError) as bad_size:
        repo.create_kb(name="bad-size", chunk_size=100)
    assert not isinstance(bad_size.value, DuplicateKnowledgeBaseError)

    with pytest.raises(sqlite3.IntegrityError) as bad_visibility:
        repo.create_kb(name="bad-visibility", visibility="bogus")
    assert not isinstance(bad_visibility.value, DuplicateKnowledgeBaseError)


def test_update_kb_duplicate_name_maps_to_duplicate_error(repo):
    repo.create_kb(name="a", workspace_id="ws1")
    other = repo.create_kb(name="b", workspace_id="ws1")
    with pytest.raises(DuplicateKnowledgeBaseError) as excinfo:
        repo.update_kb(other.id, name="a")
    assert "a" in str(excinfo.value)


def test_update_kb_reads_in_same_transaction(repo, monkeypatch):
    kb = repo.create_kb(name="a", workspace_id="ws1")

    def _boom(_kb_id: str):
        raise AssertionError("update_kb must not re-read via get_kb")

    monkeypatch.setattr(repo, "get_kb", _boom)
    updated = repo.update_kb(kb.id, description="同事务")
    assert updated.id == kb.id
    assert updated.name == "a"
    assert updated.description == "同事务"


def test_list_kbs_orders_same_second_by_insertion(repo, monkeypatch):
    monkeypatch.setattr(repository_module, "utc_now", lambda: "2026-01-01T00:00:00+00:00")
    ids = iter(["kb_zz", "kb_mm", "kb_aa"])
    monkeypatch.setattr(repository_module, "new_id", lambda prefix: next(ids))

    created = [repo.create_kb(name=f"kb-{i}", workspace_id="ws1") for i in range(3)]
    listed = repo.list_kbs(workspace_ids=["ws1"])
    assert [kb.id for kb in listed] == [kb.id for kb in created]


def _insert_doc(repo, kb, *, path="/tmp/a.md", file_hash="h1"):
    return repo.insert_document(
        kb_id=kb.id,
        workspace_id=kb.workspace_id,
        title="a",
        source_kind="external",
        source_path=path,
        original_name="a.md",
        mime="text/markdown",
        file_hash=file_hash,
        file_size=10,
        source_mtime_ns=1,
    )


def test_insert_document_defaults(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    assert doc.parse_status == "pending"
    assert doc.attempt == 0
    assert doc.vector_status == "pending"
    assert doc.id.startswith("doc_")
    assert doc.workspace_id == "ws1"
    assert doc.index_version == ""
    assert doc.indexed_at is None


def test_duplicate_path_in_same_kb_is_rejected(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    _insert_doc(repo, kb, path="/tmp/x.md", file_hash="h1")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_doc(repo, kb, path="/tmp/x.md", file_hash="h2")


def test_duplicate_path_raises_distinct_error(repo):
    # 唯一约束冲突必须映射为专门的异常，且仍兼容 sqlite3.IntegrityError
    from nanobot.knowledge.repository import DuplicateDocumentPathError

    kb = repo.create_kb(name="a", workspace_id="ws1")
    _insert_doc(repo, kb, path="/tmp/x.md")
    with pytest.raises(DuplicateDocumentPathError) as excinfo:
        _insert_doc(repo, kb, path="/tmp/x.md", file_hash="h2")
    assert isinstance(excinfo.value, sqlite3.IntegrityError)
    assert "/tmp/x.md" in str(excinfo.value)


def test_other_integrity_errors_are_not_masked(repo):
    from nanobot.knowledge.repository import DuplicateDocumentPathError

    kb = repo.create_kb(name="a", workspace_id="ws1")
    with pytest.raises(sqlite3.IntegrityError) as excinfo:
        repo.insert_document(
            kb_id=kb.id,
            workspace_id=kb.workspace_id,
            title="a",
            source_kind="bogus",
            source_path="/tmp/y.md",
        )
    assert not isinstance(excinfo.value, DuplicateDocumentPathError)


def test_find_by_path_and_hash(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb, path="/tmp/x.md", file_hash="h1")
    assert repo.find_by_path(kb.id, "/tmp/x.md").id == doc.id
    assert repo.find_by_hash(kb.id, "h1").id == doc.id
    assert repo.find_by_hash(kb.id, "missing") is None


def test_claim_increments_attempt_and_sets_processing(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    assert repo.claim_document(doc.id, lease_owner="w1", now="2026-01-01T00:00:00+00:00") == 1
    claimed = repo.get_document(doc.id)
    assert claimed.parse_status == "processing"
    assert claimed.attempt == 1
    assert claimed.lease_owner == "w1"
    assert claimed.lease_expires_at == "2026-01-01T00:10:00+00:00"
    # 租约未过期，第二次 claim 失败
    assert repo.claim_document(doc.id, lease_owner="w2", now="2026-01-01T00:00:01+00:00") is None


def test_claim_ready_document_is_rejected(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    assert repo.commit_document(
        doc.id, attempt=1, chunk_count=1, char_count=5,
        index_version="norm1:tok1:chunk1", vector_status="skipped",
    )
    assert repo.claim_document(doc.id, lease_owner="w2") is None


def test_reindex_moves_ready_to_pending(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    repo.commit_document(doc.id, attempt=1, chunk_count=0, char_count=0,
                         index_version="v", vector_status="skipped")
    assert repo.mark_reindex_pending(doc.id) is True
    assert repo.get_document(doc.id).parse_status == "pending"


def test_commit_requires_matching_attempt(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    assert repo.commit_document(doc.id, attempt=99, chunk_count=1, char_count=1,
                                index_version="v", vector_status="skipped") is False
    assert repo.get_document(doc.id).parse_status == "processing"


def test_stale_attempt_cannot_overwrite_newer(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    assert repo.claim_document(doc.id, lease_owner="w1",
                               now="2026-01-01T00:00:00+00:00") == 1
    assert repo.claim_document(doc.id, lease_owner="w2",
                               now="2026-01-02T00:00:00+00:00") == 2
    assert repo.commit_document(doc.id, attempt=1, chunk_count=1, char_count=1,
                                index_version="v", vector_status="skipped") is False
    assert repo.commit_document(doc.id, attempt=2, chunk_count=1, char_count=1,
                                index_version="v", vector_status="skipped") is True
    current = repo.get_document(doc.id)
    assert current.parse_status == "ready"
    assert current.attempt == 2


def test_fail_then_retry(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    assert repo.fail_document(doc.id, attempt=1, error_code="KNOWLEDGE_EXTRACT_FAILED",
                              error_message="bad pdf") is True
    failed = repo.get_document(doc.id)
    assert failed.parse_status == "failed"
    assert failed.error_code == "KNOWLEDGE_EXTRACT_FAILED"
    assert failed.lease_owner == ""
    assert repo.claim_document(doc.id, lease_owner="w2") == 2


def test_fail_requires_processing_status(repo):
    # 已 ready 的行不能被迟到的 worker 覆盖为 failed
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    assert repo.commit_document(doc.id, attempt=1, chunk_count=1, char_count=5,
                                index_version="v", vector_status="skipped")
    assert repo.fail_document(doc.id, attempt=1, error_code="X",
                              error_message="late") is False
    assert repo.get_document(doc.id).parse_status == "ready"


def test_reset_expired_leases(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1", now="2026-01-01T00:00:00+00:00")
    assert repo.reset_expired_leases(now="2026-01-01T00:05:00+00:00") == []
    assert repo.reset_expired_leases(now="2026-01-01T01:00:00+00:00") == [doc.id]
    assert repo.get_document(doc.id).parse_status == "pending"


def test_reset_expired_leases_keeps_live_lease(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1", now="2026-01-01T00:00:00+00:00")
    assert repo.reset_expired_leases(now="2026-01-01T00:09:59+00:00") == []
    live = repo.get_document(doc.id)
    assert live.parse_status == "processing"
    assert live.lease_owner == "w1"
    assert repo.claim_document(doc.id, lease_owner="w2",
                               now="2026-01-02T00:00:00+00:00") == 2


def _chunk(index: int, text: str = "内容") -> ChunkRecord:
    return ChunkRecord(
        id=f"chk_{index:032x}",
        doc_id="",  # commit_chunks 填充
        kb_id="",
        workspace_id="",
        chunk_index=index,
        content=text,
        heading_path="",
        page=None,
        locator="",
        start_offset=index * 10,
        end_offset=index * 10 + len(text),
        char_count=len(text),
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        search_text=tokenize_index(text),
    )


def _fts_count(db, query: str) -> int:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT count(*) FROM document_chunks_fts"
            " WHERE document_chunks_fts MATCH ?",
            (tokenize_query(query),),
        ).fetchone()
    return int(row[0])


def test_commit_chunks_writes_rows_and_fts(repo, db):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    assert repo.commit_chunks(
        doc.id, attempt=1, chunks=[_chunk(0, "用户热爱创作"), _chunk(1, "部署失败回滚")]
    ) is True
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM document_chunks").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM document_chunks_fts").fetchone()[0] == 2
    stored = repo.fetch_chunk("chk_" + "0" * 31 + "0")
    assert stored is not None
    assert stored.doc_id == doc.id
    assert stored.kb_id == kb.id
    assert stored.workspace_id == "ws1"
    assert stored.content == "用户热爱创作"
    assert stored.is_enabled is True


def test_commit_chunks_updates_document_summary(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    assert repo.commit_chunks(
        doc.id, attempt=1, chunks=[_chunk(0, "用户热爱创作"), _chunk(1, "部署失败")]
    ) is True
    stored = repo.get_document(doc.id)
    assert stored.parse_status == "ready"
    assert stored.chunk_count == 2
    assert stored.char_count == len("用户热爱创作") + len("部署失败")
    assert stored.index_version == "norm1:tok1:chunk1"
    assert stored.vector_status == "pending"
    assert stored.lease_owner == ""
    assert stored.lease_expires_at == ""
    assert stored.error_code == ""
    assert stored.indexed_at is not None


def test_commit_chunks_empty_marks_ready_and_skipped(repo, db):
    # 空块列表也必须落到 ready + vector_status='skipped'，不能留在 processing
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    assert repo.commit_chunks(doc.id, attempt=1, chunks=[]) is True
    stored = repo.get_document(doc.id)
    assert stored.parse_status == "ready"
    assert stored.vector_status == "skipped"
    assert stored.chunk_count == 0
    assert stored.char_count == 0
    assert stored.lease_owner == ""
    assert repo.list_chunks(doc.id) == []
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM document_chunks_fts").fetchone()[0] == 0


def test_commit_chunks_unknown_document_is_noop(repo):
    assert repo.commit_chunks("doc_missing", attempt=1, chunks=[_chunk(0)]) is False


def test_commit_chunks_replaces_old_rows(repo, db):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    repo.commit_chunks(doc.id, attempt=1, chunks=[_chunk(0), _chunk(1)])
    repo.mark_reindex_pending(doc.id)
    repo.claim_document(doc.id, lease_owner="w2")
    assert repo.commit_chunks(doc.id, attempt=2, chunks=[_chunk(0)]) is True
    assert len(repo.list_chunks(doc.id)) == 1
    assert repo.get_document(doc.id).chunk_count == 1


def test_commit_chunks_rolls_back_when_attempt_superseded(repo, db):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    repo.commit_chunks(doc.id, attempt=1, chunks=[_chunk(0)])
    repo.mark_reindex_pending(doc.id)
    repo.claim_document(doc.id, lease_owner="w2")
    assert repo.commit_chunks(doc.id, attempt=1, chunks=[_chunk(0), _chunk(1)]) is False
    remaining = repo.list_chunks(doc.id)
    assert [c.id for c in remaining] == ["chk_" + "0" * 32]
    assert repo.get_document(doc.id).parse_status == "processing"


def test_commit_chunks_rejects_stale_worker_after_ready(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    assert repo.commit_chunks(doc.id, attempt=1, chunks=[_chunk(0)]) is True
    # 已 ready 后同一 attempt 不能再次写入，也不能删除既有块
    assert repo.commit_chunks(doc.id, attempt=1, chunks=[_chunk(0), _chunk(1)]) is False
    assert len(repo.list_chunks(doc.id)) == 1


def test_chunk_content_is_immutable(repo, db):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    repo.commit_chunks(doc.id, attempt=1, chunks=[_chunk(0)])
    with db.connect(immediate=True) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="CHUNK_IMMUTABLE"):
            conn.execute("UPDATE document_chunks SET content='x'")
        conn.execute("UPDATE document_chunks SET is_enabled=0")
    assert repo.fetch_chunk("chk_" + "0" * 32).is_enabled is False


def test_delete_document_cascades_chunks_and_fts(repo, db):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    repo.commit_chunks(doc.id, attempt=1, chunks=[_chunk(0)])
    repo.delete_document(doc.id)
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM document_chunks").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM document_chunks_fts").fetchone()[0] == 0
    assert _fts_count(db, "内容") == 0


def test_chunk_fts_tracks_reindex_and_delete(repo, db):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    repo.commit_chunks(
        doc.id, attempt=1, chunks=[_chunk(0, "用户热爱创作"), _chunk(1, "部署失败回滚")]
    )
    assert _fts_count(db, "热爱") == 1
    assert _fts_count(db, "回滚") == 1
    repo.mark_reindex_pending(doc.id)
    repo.claim_document(doc.id, lease_owner="w2")
    repo.commit_chunks(doc.id, attempt=2, chunks=[_chunk(0, "用户热爱创作")])
    assert _fts_count(db, "热爱") == 1
    assert _fts_count(db, "回滚") == 0
    repo.delete_document(doc.id)
    assert _fts_count(db, "热爱") == 0


def test_fetch_neighbors_returns_context(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    repo.commit_chunks(doc.id, attempt=1, chunks=[_chunk(0), _chunk(1), _chunk(2)])
    chunk = repo.list_chunks(doc.id)[1]
    neighbors = repo.fetch_neighbors(chunk, before=1, after=1)
    assert [c.chunk_index for c in neighbors] == [0, 1, 2]


def test_fetch_neighbors_skips_disabled(repo, db):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    repo.commit_chunks(doc.id, attempt=1, chunks=[_chunk(0), _chunk(1), _chunk(2)])
    with db.connect(immediate=True) as conn:
        conn.execute("UPDATE document_chunks SET is_enabled=0 WHERE chunk_index=1")
    middle = repo.list_chunks(doc.id)[1]
    neighbors = repo.fetch_neighbors(middle, before=1, after=1)
    assert [c.chunk_index for c in neighbors] == [0, 2]


def test_list_chunks_paginates_in_index_order(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    repo.commit_chunks(doc.id, attempt=1, chunks=[_chunk(i) for i in range(3)])
    page = repo.list_chunks(doc.id, offset=1, limit=1)
    assert [c.chunk_index for c in page] == [1]
