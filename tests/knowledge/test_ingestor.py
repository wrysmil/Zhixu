"""Ingestor 状态机测试：真实临时 SQLite + 假 embedder / 向量层。"""

from __future__ import annotations

import hashlib

import pytest

import nanobot.knowledge.ingestor as ingestor_module
from nanobot.knowledge.chunker import ChunkDraft
from nanobot.knowledge.database import KnowledgeDatabase
from nanobot.knowledge.embedding import EmbeddingError, EmbeddingUnavailable
from nanobot.knowledge.extractor import KnowledgeExtractionError
from nanobot.knowledge.ingestor import Ingestor, chunk_id_for
from nanobot.knowledge.repository import KnowledgeRepository
from nanobot.knowledge.tokenizer import tokenize_index, tokenize_query
from nanobot.knowledge.vector import VectorStoreError

CONTENT = "部署失败怎么回滚？" * 60


class FakeEmbedder:
    def __init__(
        self,
        *,
        dims: int = 2,
        fail: bool = False,
        unavailable: bool = False,
        identity: tuple[str, str, int] | None = None,
        one_dim: bool = False,
        on_embed=None,
    ) -> None:
        self.identity = identity if identity is not None else ("local", "m", dims)
        self.fail = fail
        self.unavailable = unavailable
        self.one_dim = one_dim
        self.on_embed = on_embed
        self.calls = 0
        self.texts: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.texts.append(list(texts))
        if self.on_embed is not None:
            self.on_embed()
        if self.unavailable:
            raise EmbeddingUnavailable("backend down")
        if self.fail:
            raise EmbeddingError("boom")
        if self.one_dim:
            return [[1.0] for _ in texts]
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


class FakeVector:
    def __init__(
        self, *, fail: bool = False, fail_at: int | None = None, available: bool = True
    ) -> None:
        self.available = available
        self.fail = fail
        self.fail_at = fail_at
        self.calls = 0
        self.upserts: list[list[str]] = []
        self.deletes: list[str] = []

    def delete_document(self, doc_id: str) -> None:
        if self.fail:
            raise VectorStoreError("vector down")
        self.deletes.append(doc_id)

    def upsert(self, chunks, vectors) -> None:
        self.calls += 1
        if self.fail or (self.fail_at is not None and self.calls >= self.fail_at):
            raise VectorStoreError("vector down")
        assert len(chunks) == len(vectors)
        self.upserts.append([chunk.id for chunk in chunks])


@pytest.fixture
def db(tmp_path):
    database = KnowledgeDatabase(tmp_path / "state.db")
    database.init_schema()
    return database


@pytest.fixture
def repo(db):
    return KnowledgeRepository(db)


def _prepare(
    tmp_path,
    repo,
    content: str,
    *,
    name: str = "a.txt",
    chunk_size: int = 800,
    chunk_overlap: int = 120,
    model: str = "m",
    claim: bool = True,
):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    kb = repo.create_kb(
        name=f"kb-{name}",
        workspace_id="ws1",
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        embedding_backend="local",
        embedding_model=model,
        embedding_dimensions=2,
    )
    doc = repo.insert_document(
        kb_id=kb.id,
        workspace_id="ws1",
        title="a",
        source_kind="external",
        source_path=str(path),
        file_hash="h1",
    )
    attempt = (
        repo.claim_document(doc.id, lease_owner="w1") if claim else None
    )
    return kb, doc, attempt


def _ingestor(repo, *, embedder=None, vector=None, **kwargs) -> Ingestor:
    return Ingestor(
        repo,
        embedder=embedder or FakeEmbedder(),
        vector_store=vector if vector is not None else FakeVector(),
        **kwargs,
    )


def _chunks(repo, doc_id):
    return repo.list_chunks(doc_id, limit=1000)


def test_process_document_ready_path(tmp_path, repo):
    kb, doc, attempt = _prepare(tmp_path, repo, CONTENT)
    vector = FakeVector()
    _ingestor(repo, vector=vector).process_document(doc.id, attempt)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "ready"
    assert final.vector_status == "ok"
    assert final.error_code == ""
    assert final.index_version == "norm1:tok1:chunk1"
    assert final.indexed_at
    chunks = _chunks(repo, doc.id)
    assert final.chunk_count == len(chunks) >= 1
    assert final.char_count == sum(chunk.char_count for chunk in chunks)
    assert vector.deletes == [doc.id]
    assert vector.upserts == [[chunk.id for chunk in chunks]]
    hits = repo.search_fts(
        tokenize_query("回滚"), workspace_ids=["ws1"], kb_ids=[kb.id]
    )
    assert hits and all(hit.doc_id == doc.id for hit in hits)


def test_chunk_ids_and_hashes_are_deterministic(tmp_path, repo):
    _kb, doc, attempt = _prepare(tmp_path, repo, CONTENT)
    ingestor = _ingestor(repo)
    ingestor.process_document(doc.id, attempt)
    first = [(chunk.id, chunk.content_hash) for chunk in _chunks(repo, doc.id)]

    assert repo.mark_reindex_pending(doc.id)
    attempt2 = repo.claim_document(doc.id, lease_owner="w2")
    assert attempt2 is not None
    ingestor.process_document(doc.id, attempt2)
    second = [(chunk.id, chunk.content_hash) for chunk in _chunks(repo, doc.id)]

    assert first == second
    for chunk in _chunks(repo, doc.id):
        assert chunk.id == chunk_id_for(doc.id, chunk.chunk_index)
        assert chunk.id.startswith("chk_") and len(chunk.id) == 20
        expected = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
        assert chunk.content_hash == expected


def test_search_text_is_tokenized(tmp_path, repo):
    kb, doc, attempt = _prepare(tmp_path, repo, "部署失败怎么回滚？" * 10)
    _ingestor(repo).process_document(doc.id, attempt)

    assert tokenize_index("部署失败") == "部 署 失 败 部署 署失 失败"
    hits = repo.search_fts(
        tokenize_query("部署失败"), workspace_ids=["ws1"], kb_ids=[kb.id]
    )
    assert hits
    assert hits[0].chunk_id == _chunks(repo, doc.id)[0].id


def test_embedding_failure_keeps_document_ready(tmp_path, repo):
    kb, doc, attempt = _prepare(tmp_path, repo, CONTENT)
    vector = FakeVector()
    _ingestor(repo, embedder=FakeEmbedder(fail=True), vector=vector).process_document(
        doc.id, attempt
    )

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "ready"
    assert final.vector_status == "failed"
    assert _chunks(repo, doc.id)
    assert vector.upserts == []
    assert vector.deletes == [doc.id]
    hits = repo.search_fts(
        tokenize_query("回滚"), workspace_ids=["ws1"], kb_ids=[kb.id]
    )
    assert hits


def test_embedding_unavailable_marks_skipped(tmp_path, repo):
    _kb, doc, attempt = _prepare(tmp_path, repo, CONTENT)
    vector = FakeVector()
    _ingestor(
        repo, embedder=FakeEmbedder(unavailable=True), vector=vector
    ).process_document(doc.id, attempt)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "ready"
    assert final.vector_status == "skipped"
    assert _chunks(repo, doc.id)
    assert vector.upserts == []
    assert vector.deletes == []


def test_none_embedder_marks_skipped(tmp_path, repo):
    _kb, doc, attempt = _prepare(tmp_path, repo, CONTENT)
    embedder = FakeEmbedder(identity=("none", "", 0))
    vector = FakeVector()
    _ingestor(repo, embedder=embedder, vector=vector).process_document(doc.id, attempt)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "ready"
    assert final.vector_status == "skipped"
    assert embedder.calls == 0
    assert vector.upserts == []


def test_vector_identity_mismatch_marks_skipped(tmp_path, repo):
    _kb, doc, attempt = _prepare(tmp_path, repo, CONTENT, model="other")
    embedder = FakeEmbedder()
    vector = FakeVector()
    _ingestor(repo, embedder=embedder, vector=vector).process_document(doc.id, attempt)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.vector_status == "skipped"
    assert embedder.calls == 0
    assert vector.upserts == []


def test_vector_unavailable_marks_skipped(tmp_path, repo):
    _kb, doc, attempt = _prepare(tmp_path, repo, CONTENT)
    embedder = FakeEmbedder()
    _ingestor(repo, embedder=embedder, vector=FakeVector(available=False)).process_document(
        doc.id, attempt
    )

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.vector_status == "skipped"
    assert embedder.calls == 0


def test_wrong_vector_dimension_marks_failed(tmp_path, repo):
    _kb, doc, attempt = _prepare(tmp_path, repo, CONTENT)
    vector = FakeVector()
    _ingestor(repo, embedder=FakeEmbedder(one_dim=True), vector=vector).process_document(
        doc.id, attempt
    )

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "ready"
    assert final.vector_status == "failed"
    assert vector.upserts == []


def test_vector_failure_keeps_document_ready(tmp_path, repo):
    _kb, doc, attempt = _prepare(tmp_path, repo, CONTENT)
    vector = FakeVector(fail=True)
    _ingestor(repo, vector=vector).process_document(doc.id, attempt)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "ready"
    assert final.vector_status == "failed"
    assert final.error_code == ""
    assert _chunks(repo, doc.id)
    assert vector.upserts == []


def test_vector_partial_failure_marks_partial(tmp_path, repo):
    _kb, doc, attempt = _prepare(
        tmp_path, repo, "部署失败怎么回滚？" * 40, chunk_size=200, chunk_overlap=50
    )
    vector = FakeVector(fail_at=2)
    _ingestor(repo, vector=vector, batch_size=1).process_document(doc.id, attempt)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "ready"
    assert final.vector_status == "partial"
    assert len(vector.upserts) == 1


def test_text_empty_marks_failed(tmp_path, repo):
    _kb, doc, attempt = _prepare(tmp_path, repo, "   \n\n  ")
    _ingestor(repo).process_document(doc.id, attempt)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "failed"
    assert final.error_code == "KNOWLEDGE_TEXT_EMPTY"
    assert _chunks(repo, doc.id) == []


def test_empty_chunks_commit_skipped(tmp_path, repo, monkeypatch):
    _kb, doc, attempt = _prepare(tmp_path, repo, CONTENT)
    monkeypatch.setattr(ingestor_module, "chunk_lines", lambda lines, **kwargs: [])
    monkeypatch.setattr(
        ingestor_module, "validate_chunks", lambda drafts, **kwargs: None
    )
    vector = FakeVector()
    _ingestor(repo, vector=vector).process_document(doc.id, attempt)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "ready"
    assert final.vector_status == "skipped"
    assert final.chunk_count == 0
    assert final.char_count == 0
    assert vector.upserts == []


def test_missing_source_marks_failed(tmp_path, repo):
    kb = repo.create_kb(
        name="a",
        workspace_id="ws1",
        embedding_backend="local",
        embedding_model="m",
        embedding_dimensions=2,
    )
    doc = repo.insert_document(
        kb_id=kb.id,
        workspace_id="ws1",
        title="a",
        source_kind="external",
        source_path=str(tmp_path / "missing.txt"),
        file_hash="h1",
    )
    attempt = repo.claim_document(doc.id, lease_owner="w1")
    _ingestor(repo).process_document(doc.id, attempt)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "failed"
    assert final.error_code == "KNOWLEDGE_SOURCE_MISSING"
    assert "重新上传" in final.error_message


def test_unsupported_type_marks_failed(tmp_path, repo):
    _kb, doc, attempt = _prepare(tmp_path, repo, "binary", name="a.xyz")
    _ingestor(repo).process_document(doc.id, attempt)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "failed"
    assert final.error_code == "KNOWLEDGE_UNSUPPORTED_TYPE"


def test_validator_error_marks_failed_and_keeps_old_chunks(
    tmp_path, repo, monkeypatch
):
    _kb, doc, attempt = _prepare(
        tmp_path, repo, "x" * 500, chunk_size=200, chunk_overlap=50
    )
    ingestor = _ingestor(repo)
    ingestor.process_document(doc.id, attempt)
    old_count = len(_chunks(repo, doc.id))
    assert old_count >= 1

    assert repo.mark_reindex_pending(doc.id)
    attempt2 = repo.claim_document(doc.id, lease_owner="w2")
    assert attempt2 is not None
    oversized = [ChunkDraft("x" * 500, 0, 500, "", None, "")]
    monkeypatch.setattr(
        ingestor_module, "chunk_lines", lambda lines, **kwargs: oversized
    )
    ingestor.process_document(doc.id, attempt2)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "failed"
    assert final.error_code == "KNOWLEDGE_CHUNK_INVALID"
    assert len(_chunks(repo, doc.id)) == old_count


def test_extract_failure_marks_failed_and_keeps_old_chunks(
    tmp_path, repo, monkeypatch
):
    _kb, doc, attempt = _prepare(tmp_path, repo, "第一版内容。" * 50)
    ingestor = _ingestor(repo)
    ingestor.process_document(doc.id, attempt)
    old_count = len(_chunks(repo, doc.id))

    assert repo.mark_reindex_pending(doc.id)
    attempt2 = repo.claim_document(doc.id, lease_owner="w2")
    assert attempt2 is not None

    def boom(path):
        raise KnowledgeExtractionError("bad")

    monkeypatch.setattr(ingestor_module, "extract_lines", boom)
    ingestor.process_document(doc.id, attempt2)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "failed"
    assert final.error_code == "KNOWLEDGE_EXTRACT_FAILED"
    assert len(_chunks(repo, doc.id)) == old_count


def test_superseded_attempt_does_not_write(tmp_path, repo):
    _kb, doc, attempt = _prepare(tmp_path, repo, "内容。" * 100)
    assert attempt is not None
    repo.reset_expired_leases(now="9999-01-01T00:00:00+00:00")
    new_attempt = repo.claim_document(doc.id, lease_owner="w2")
    assert new_attempt == attempt + 1

    vector = FakeVector()
    _ingestor(repo, vector=vector).process_document(doc.id, attempt)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "processing"
    assert final.attempt == new_attempt
    assert _chunks(repo, doc.id) == []
    assert vector.upserts == []
    assert vector.deletes == []


def test_vector_status_does_not_overwrite_newer_attempt(tmp_path, repo, monkeypatch):
    _kb, doc, attempt = _prepare(tmp_path, repo, CONTENT)
    ingestor = _ingestor(repo)
    assert attempt is not None

    def newer_attempt(doc_id, records, vectors):
        assert repo.mark_reindex_pending(doc_id)
        assert repo.claim_document(doc_id, lease_owner="w2") == attempt + 1
        return "ok"

    monkeypatch.setattr(ingestor, "_write_vectors", newer_attempt)
    ingestor.process_document(doc.id, attempt)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "processing"
    assert final.attempt == attempt + 1
    assert final.vector_status == "pending"


def test_deleted_document_is_silent(tmp_path, repo):
    _kb, doc, attempt = _prepare(tmp_path, repo, CONTENT)
    repo.delete_document(doc.id)

    _ingestor(repo).process_document(doc.id, attempt)

    assert repo.get_document(doc.id) is None
    assert _chunks(repo, doc.id) == []


def test_deletion_between_load_and_commit_is_silent(tmp_path, repo, monkeypatch):
    _kb, doc, attempt = _prepare(tmp_path, repo, CONTENT)
    original = ingestor_module.chunk_lines

    def deleting_chunker(lines, **kwargs):
        drafts = original(lines, **kwargs)
        repo.delete_document(doc.id)
        return drafts

    monkeypatch.setattr(ingestor_module, "chunk_lines", deleting_chunker)
    _ingestor(repo).process_document(doc.id, attempt)

    assert repo.get_document(doc.id) is None
    assert _chunks(repo, doc.id) == []


def test_reindex_preserves_old_chunks_until_commit(tmp_path, repo, monkeypatch):
    _kb, doc, attempt = _prepare(tmp_path, repo, "第一版内容。" * 50)
    ingestor = _ingestor(repo)
    ingestor.process_document(doc.id, attempt)
    old = [(chunk.id, chunk.content) for chunk in _chunks(repo, doc.id)]
    assert old

    path = tmp_path / "a.txt"
    path.write_text("第二版内容。" * 50, encoding="utf-8")
    assert repo.mark_reindex_pending(doc.id)
    attempt2 = repo.claim_document(doc.id, lease_owner="w2")
    assert attempt2 is not None

    observed: dict[str, list[tuple[str, str]]] = {}
    original = ingestor_module.chunk_lines

    def observing_chunker(lines, **kwargs):
        observed["during"] = [
            (chunk.id, chunk.content) for chunk in _chunks(repo, doc.id)
        ]
        return original(lines, **kwargs)

    monkeypatch.setattr(ingestor_module, "chunk_lines", observing_chunker)
    ingestor.process_document(doc.id, attempt2)

    assert observed["during"] == old
    after = [(chunk.id, chunk.content) for chunk in _chunks(repo, doc.id)]
    assert [chunk_id for chunk_id, _ in after] == [chunk_id for chunk_id, _ in old]
    assert after != old
    assert "第二版" in after[0][1]


def test_reindex_missing_source_marks_failed(tmp_path, repo):
    _kb, doc, attempt = _prepare(tmp_path, repo, CONTENT)
    ingestor = _ingestor(repo)
    ingestor.process_document(doc.id, attempt)
    old_count = len(_chunks(repo, doc.id))

    (tmp_path / "a.txt").unlink()
    ingestor.process_document(doc.id)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "failed"
    assert final.error_code == "KNOWLEDGE_SOURCE_MISSING"
    assert len(_chunks(repo, doc.id)) == old_count


def test_process_document_claims_when_attempt_omitted(tmp_path, repo):
    _kb, doc, _ = _prepare(tmp_path, repo, CONTENT, claim=False)
    vector = FakeVector()
    _ingestor(repo, vector=vector).process_document(doc.id)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.attempt == 1
    assert final.parse_status == "ready"
    assert final.vector_status == "ok"
    assert _chunks(repo, doc.id)


def test_active_lease_is_not_reprocessed(tmp_path, repo):
    _kb, doc, attempt = _prepare(tmp_path, repo, CONTENT)
    _ingestor(repo).process_document(doc.id)

    final = repo.get_document(doc.id)
    assert final is not None
    assert final.parse_status == "processing"
    assert final.attempt == attempt
    assert _chunks(repo, doc.id) == []


def test_embedding_runs_outside_write_transaction(tmp_path, repo):
    _kb, doc, attempt = _prepare(tmp_path, repo, CONTENT)

    def probe() -> None:
        repo.create_kb(name="probe", workspace_id="ws2")

    _ingestor(repo, embedder=FakeEmbedder(on_embed=probe)).process_document(
        doc.id, attempt
    )

    assert [kb.name for kb in repo.list_kbs(workspace_ids=["ws2"])] == ["probe"]
