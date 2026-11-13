"""KnowledgeService 单测：单例、路径解析、去重、队列、统计与修复。

全部使用 tmp_path 与显式 DB 路径；不触碰 `~/.nanobot`，不发起网络请求。
"""

from __future__ import annotations

import asyncio
import sqlite3
import stat
from pathlib import Path

import pytest

import nanobot.config.paths as config_paths
import nanobot.knowledge.service as service_module
from nanobot.config.schema import KnowledgeConfig, KnowledgeEmbeddingConfig
from nanobot.knowledge.database import KnowledgeDatabase
from nanobot.knowledge.embedding import NoneEmbedder
from nanobot.knowledge.extractor import KnowledgeExtractionError, KnowledgeUnsupportedType
from nanobot.knowledge.files import is_within
from nanobot.knowledge.ingestor import Ingestor
from nanobot.knowledge.repository import KnowledgeRepository
from nanobot.knowledge.retriever import Retriever, RetrieverSettings
from nanobot.knowledge.service import (
    KnowledgeService,
    KnowledgeServiceError,
    get_knowledge_service,
    get_knowledge_service_for_test,
    reset_knowledge_service,
)
from nanobot.knowledge.tokenizer import tokenize_query
from nanobot.knowledge.vector import RepairAction, RepairReport

TEXT = "回滚步骤：先停止服务，再切换到上一个版本，最后验证健康检查。"


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_knowledge_service()
    yield
    reset_knowledge_service()


def _service(tmp_path: Path, **kwargs) -> KnowledgeService:
    return KnowledgeService.local(tmp_path / "state.db", **kwargs)


def _build_service(tmp_path: Path, **overrides):
    """手工装配服务：可注入假 ingestor / retriever / 向量层。"""
    path = tmp_path / "state.db"
    database = KnowledgeDatabase(path)
    database.init_schema()
    repo = KnowledgeRepository(database)
    embedder = overrides.pop("embedder", None) or NoneEmbedder()
    vector = overrides.pop("vector_store", None)
    ingestor = overrides.pop("ingestor", None) or Ingestor(
        repo, embedder=embedder, vector_store=vector
    )
    retriever = overrides.pop("retriever", None) or Retriever(
        repo, embedder=embedder, vector_store=vector
    )
    service = KnowledgeService(
        database=database,
        repo=repo,
        embedder=embedder,
        vector_store=vector,
        ingestor=ingestor,
        retriever=retriever,
        uploads_dir=path.parent / "uploads",
        **overrides,
    )
    return service, repo


async def _wait_until(predicate, *, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met before timeout")


class FakeEmbedder:
    def __init__(self, identity: tuple[str, str, int] = ("local", "fake", 2)) -> None:
        self.identity = identity

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


class FakeVector:
    available = True
    identity = ("local", "fake", 2)

    def __init__(self, report: RepairReport | None = None) -> None:
        self.report = report or RepairReport()
        self.scanned: dict[str, list[str]] | None = None
        self.deleted: list[str] = []
        self.upserted: list[int] = []

    def scan(self, expected):
        self.scanned = {key: list(value) for key, value in expected.items()}
        return self.report

    def delete_document(self, doc_id: str) -> None:
        self.deleted.append(doc_id)

    def delete_kb(self, kb_id: str) -> None:
        self.deleted.append(kb_id)

    def upsert(self, chunks, vectors) -> int:
        self.upserted.append(len(chunks))
        return len(chunks)


class RecordingIngestor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def process_document(self, doc_id: str, attempt: int | None = None) -> None:
        self.calls.append((doc_id, attempt))


class RecordingRetriever:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, object]]] = []

    def search(self, query: str, **kwargs):
        self.calls.append((query, dict(kwargs)))
        return self.result


# ---------- 单例与路径 ----------


def test_singleton_is_cached_by_path(tmp_path):
    path = tmp_path / "state.db"
    first = get_knowledge_service_for_test(path)
    second = get_knowledge_service_for_test(path)
    assert first is second
    reset_knowledge_service()
    assert get_knowledge_service_for_test(path) is not first


def test_singleton_is_keyed_by_resolved_path(tmp_path):
    path = tmp_path / "state.db"
    first = get_knowledge_service_for_test(path)
    assert get_knowledge_service_for_test(Path(str(tmp_path) + "/./state.db")) is first


def test_default_paths_stay_under_knowledge_dir(monkeypatch, tmp_path):
    knowledge_dir = tmp_path / "home" / ".nanobot" / "knowledge"
    monkeypatch.setattr(config_paths, "get_knowledge_dir", lambda: knowledge_dir)
    config = KnowledgeConfig(embedding=KnowledgeEmbeddingConfig(backend="none"))

    service = get_knowledge_service(config)

    assert (knowledge_dir / "state.db").is_file()
    assert (knowledge_dir / "uploads").is_dir()
    assert stat.S_IMODE(knowledge_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((knowledge_dir / "state.db").stat().st_mode) == 0o600
    assert get_knowledge_service(config) is service


def test_explicit_paths_and_vector_identity(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class FakeVectorStore:
        def __init__(self, path, *, backend, model, dimensions) -> None:
            captured["vector"] = (Path(path), backend, model, dimensions)
            self.available = False

    config = KnowledgeConfig(
        database_path=str(tmp_path / "data" / "state.db"),
        vector_path=str(tmp_path / "vec"),
        embedding=KnowledgeEmbeddingConfig(backend="auto"),
    )
    monkeypatch.setattr(service_module, "build_embedder", lambda **kwargs: FakeEmbedder())
    monkeypatch.setattr(service_module, "VectorStore", FakeVectorStore)

    service = get_knowledge_service(config)
    kb = service.create_kb(name="a", workspace_id="ws1")

    assert captured["vector"] == (
        tmp_path / "vec",
        "local",
        "fake",
        2,
    )
    assert (kb.embedding_backend, kb.embedding_model, kb.embedding_dimensions) == (
        "local",
        "fake",
        2,
    )
    assert (tmp_path / "data" / "uploads").is_dir()


def test_retrieval_settings_mapping():
    class Retrieval:
        vector_weight = 0.5
        keyword_weight = 0.5
        rrf_k = 7
        pool_size = 11
        keyword_pool_size = 22
        default_limit = 3
        max_limit = 9
        mmr_lambda = 0.1
        relevance_band = 0.2
        adjacent_similarity_floor = 0.3
        dedup_containment = 0.4
        dedup_unique_ratio = 0.05
        dedup_jaccard = 0.7
        dedup_length_ratio = 2.0
        tool_output_chars = 123

    settings = service_module._retriever_settings(Retrieval())

    assert isinstance(settings, RetrieverSettings)
    assert settings.vector_weight == 0.5
    assert settings.keyword_pool_size == 22
    assert settings.dedup_jaccard == 0.7
    # `tool_output_chars` 属于工具层预算，不进 RetrieverSettings
    assert not hasattr(settings, "tool_output_chars")


# ---------- 生命周期 ----------


async def test_start_and_stop_are_idempotent(tmp_path):
    service = _service(tmp_path)
    await service.start()
    worker = service._worker
    await service.start()
    assert service._worker is worker
    await service.stop()
    await service.stop()
    assert service._worker is None


async def test_start_recovers_pending_documents(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    path = tmp_path / "a.md"
    path.write_text(TEXT, encoding="utf-8")
    outcome = service.add_file(path, kb_id=kb.id, workspace_id="ws1")
    assert service.get_document(outcome.id).parse_status == "pending"

    await service.start()
    await _wait_until(
        lambda: service.get_document(outcome.id).parse_status == "ready"
    )
    await service.stop()

    assert service.list_chunks(outcome.id)


async def test_start_recovers_expired_leases(tmp_path):
    service, repo = _build_service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    path = tmp_path / "a.md"
    path.write_text(TEXT, encoding="utf-8")
    outcome = service.add_file(path, kb_id=kb.id, workspace_id="ws1")
    assert repo.claim_document(outcome.id, lease_owner="dead", lease_seconds=-1) == 1
    assert service.get_document(outcome.id).parse_status == "processing"

    await service.start()
    await _wait_until(
        lambda: service.get_document(outcome.id).parse_status == "ready"
    )
    await service.stop()


async def test_worker_processes_queue_in_thread(tmp_path):
    ingestor = RecordingIngestor()
    service, repo = _build_service(tmp_path, ingestor=ingestor)
    kb = service.create_kb(name="a", workspace_id="ws1")
    path = tmp_path / "a.md"
    path.write_text(TEXT, encoding="utf-8")
    outcome = service.add_file(path, kb_id=kb.id, workspace_id="ws1")

    await service.start()
    await _wait_until(lambda: bool(ingestor.calls))
    await service.stop()

    assert ingestor.calls == [(outcome.id, 1)]


async def test_worker_survives_ingestor_failure(tmp_path):
    class BrokenIngestor(RecordingIngestor):
        def process_document(self, doc_id, attempt=None):
            self.calls.append((doc_id, attempt))
            raise RuntimeError("boom")

    ingestor = BrokenIngestor()
    service, _ = _build_service(tmp_path, ingestor=ingestor)
    kb = service.create_kb(name="a", workspace_id="ws1")
    first = tmp_path / "a.md"
    first.write_text(TEXT, encoding="utf-8")
    second = tmp_path / "b.md"
    second.write_text(TEXT + "另一份", encoding="utf-8")
    a = service.add_file(first, kb_id=kb.id, workspace_id="ws1")
    b = service.add_file(second, kb_id=kb.id, workspace_id="ws1")

    await service.start()
    await _wait_until(lambda: len(ingestor.calls) == 2)
    assert service._worker is not None and not service._worker.done()
    await service.stop()

    assert {a.id, b.id} == {doc_id for doc_id, _ in ingestor.calls}


# ---------- add_file 去重 ----------


def test_add_file_same_path_updates_in_place(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    path = tmp_path / "a.txt"
    path.write_text("第一版", encoding="utf-8")
    first = service.add_file(path, kb_id=kb.id, workspace_id="ws1")
    path.write_text("第二版", encoding="utf-8")
    second = service.add_file(path, kb_id=kb.id, workspace_id="ws1")

    assert second.id == first.id
    assert second.duplicate is False
    assert second.file_hash != first.file_hash
    assert service.get_document(first.id).parse_status == "pending"


def test_add_file_dedups_same_hash_without_force(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    first_path = tmp_path / "a.txt"
    second_path = tmp_path / "b.txt"
    first_path.write_text("相同内容", encoding="utf-8")
    second_path.write_text("相同内容", encoding="utf-8")

    first = service.add_file(first_path, kb_id=kb.id, workspace_id="ws1")
    second = service.add_file(second_path, kb_id=kb.id, workspace_id="ws1")

    assert second.id == first.id
    assert second.duplicate is True


def test_add_file_force_creates_new_row(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    first_path = tmp_path / "a.txt"
    second_path = tmp_path / "b.txt"
    first_path.write_text("相同内容", encoding="utf-8")
    second_path.write_text("相同内容", encoding="utf-8")

    first = service.add_file(first_path, kb_id=kb.id, workspace_id="ws1")
    second = service.add_file(
        second_path, kb_id=kb.id, workspace_id="ws1", force=True
    )

    assert second.id != first.id
    assert second.duplicate is False
    assert len(service.list_documents(kb_id=kb.id, limit=10)) == 2


def test_add_file_reclaims_failed_row(tmp_path):
    service, repo = _build_service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    first_path = tmp_path / "a.txt"
    second_path = tmp_path / "b.txt"
    first_path.write_text("相同内容", encoding="utf-8")
    second_path.write_text("相同内容", encoding="utf-8")
    first = service.add_file(first_path, kb_id=kb.id, workspace_id="ws1")
    attempt = repo.claim_document(first.id, lease_owner="t")
    assert attempt is not None
    assert repo.fail_document(
        first.id, attempt=attempt, error_code="X", error_message="boom"
    )

    second = service.add_file(second_path, kb_id=kb.id, workspace_id="ws1")

    assert second.id == first.id
    document = service.get_document(first.id)
    assert document.parse_status == "pending"
    assert document.source_path == str(second_path.resolve())
    assert document.attempt == 1


def test_add_file_rejects_non_file_and_unknown_kb(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    with pytest.raises(KnowledgeExtractionError):
        service.add_file(tmp_path, kb_id=kb.id, workspace_id="ws1")
    path = tmp_path / "a.txt"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(KeyError):
        service.add_file(path, kb_id="kb_missing", workspace_id="ws1")


# ---------- 上传与托管副本 ----------


def test_upload_is_managed_under_uploads_dir(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")

    document = service.add_upload_bytes(
        "../../etc/notes.txt", TEXT.encode(), kb_id=kb.id,
        workspace_id="ws1", mime="text/plain",
    )

    managed = Path(document.source_path)
    assert document.source_kind == "managed"
    assert managed.name == "notes.txt"
    assert is_within(managed, tmp_path / "uploads")
    assert managed.parent.name == document.id
    assert managed.read_text(encoding="utf-8") == TEXT
    assert stat.S_IMODE(managed.stat().st_mode) == 0o600
    assert service.get_document(document.id).parse_status == "pending"


def test_preview_chunks_does_not_create_document_and_cleans_temp(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    body = ("第一段内容。" * 40 + "\n\n" + "第二段内容。" * 40).encode()

    preview = service.preview_chunks(
        kb_id=kb.id, name="notes.md", data=body, chunk_size=200, chunk_overlap=0
    )

    assert preview["chunkSize"] == 200
    assert preview["chunkOverlap"] == 0
    assert preview["total"] >= 2
    assert preview["avgChars"] > 0
    assert [chunk["index"] for chunk in preview["chunks"]] == list(
        range(len(preview["chunks"]))
    )
    assert all(chunk["content"] for chunk in preview["chunks"])
    assert service.list_documents(kb_id=kb.id) == []
    assert not list((tmp_path / "uploads").glob(".preview-*"))


def test_preview_chunks_reads_path_with_kb_defaults(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(
        name="a", workspace_id="ws1", chunk_size=200, chunk_overlap=20
    )
    source = tmp_path / "a.txt"
    source.write_text(TEXT * 20, encoding="utf-8")

    preview = service.preview_chunks(kb_id=kb.id, path=str(source))

    assert preview["chunkSize"] == 200
    assert preview["chunkOverlap"] == 20
    assert preview["total"] >= 2
    assert service.list_documents(kb_id=kb.id) == []


def test_preview_chunks_rejects_invalid_settings(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")

    with pytest.raises(KnowledgeServiceError):
        service.preview_chunks(
            kb_id=kb.id, name="a.txt", data=b"x",
            chunk_size=200, chunk_overlap=200,
        )
    with pytest.raises(KnowledgeServiceError):
        service.preview_chunks(
            kb_id=kb.id, name="a.txt", data=b"x", chunk_size=100,
        )
    with pytest.raises(KeyError):
        service.preview_chunks(kb_id="kb_missing", name="a.txt", data=b"x")


def test_preview_chunks_separator_mode_uses_custom_separator(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    body = (TEXT * 80).encode()

    smart = service.preview_chunks(
        kb_id=kb.id, name="a.txt", data=body, chunk_size=400, chunk_overlap=0
    )
    separator = service.preview_chunks(
        kb_id=kb.id,
        name="a.txt",
        data=body,
        chunk_mode="separator",
        separators=["。"],
        chunk_size=400,
        chunk_overlap=0,
    )

    assert separator["chunkMode"] == "separator"
    assert separator["separators"] == ["。"]
    assert separator["total"] != smart["total"]
    assert separator["source"]["text"].startswith("回滚步骤")
    assert separator["editable"] is True
    assert len(separator["chunks"]) == separator["total"]
    assert all(chunk["content"].endswith("。") for chunk in separator["chunks"])
    assert service.list_documents(kb_id=kb.id) == []


def test_preview_reports_all_chunks_with_offsets(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")

    preview = service.preview_chunks(
        kb_id=kb.id, name="a.txt", data=TEXT.encode(), chunk_size=200, chunk_overlap=0
    )

    text = preview["source"]["text"]
    for chunk in preview["chunks"]:
        assert text[chunk["start"] : chunk["end"]] == chunk["content"]


def test_custom_chunks_are_stored_and_used_on_ingest(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    custom = [
        {"content": "第一段：回滚前确认版本。", "heading_path": "回滚", "page": 1},
        {"content": "第二段：执行回滚并验证。", "heading_path": "回滚", "page": 2},
    ]

    doc = service.add_upload_bytes(
        "note.md",
        TEXT.encode(),
        kb_id=kb.id,
        workspace_id="ws1",
        mime="text/plain",
        custom_chunks=custom,
    )

    assert service.ingest_now(doc.id) is True
    chunks = service.list_chunks(doc.id)
    assert [chunk.content for chunk in chunks] == [item["content"] for item in custom]
    assert chunks[1].page == 2

    # 重索引复用自定义分块，不被重新切分覆盖。
    assert service.reindex_document(doc.id) is True
    assert service.ingest_now(doc.id) is True
    again = service.list_chunks(doc.id)
    assert [chunk.content for chunk in again] == [item["content"] for item in custom]


def test_upload_rejects_unsupported_suffix_and_mime(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    uploads = tmp_path / "uploads"

    with pytest.raises(KnowledgeUnsupportedType):
        service.add_upload_bytes(
            "a.exe", b"x", kb_id=kb.id, workspace_id="ws1",
            mime="application/x-msdownload",
        )
    with pytest.raises(KnowledgeUnsupportedType):
        service.add_upload_bytes(
            "a.txt", b"x", kb_id=kb.id, workspace_id="ws1", mime="image/png",
        )
    assert not any(uploads.iterdir())


def test_upload_cleans_copy_when_insert_fails(tmp_path, monkeypatch):
    service, repo = _build_service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    uploads = tmp_path / "uploads"

    def _boom(**kwargs):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(repo, "insert_document", _boom)
    with pytest.raises(sqlite3.IntegrityError):
        service.add_upload_bytes(
            "a.txt", b"x", kb_id=kb.id, workspace_id="ws1", mime="text/plain"
        )

    assert not any(uploads.iterdir())


def test_delete_managed_removes_copy_but_external_survives(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    external = tmp_path / "a.txt"
    external.write_text("x", encoding="utf-8")
    doc = service.add_file(external, kb_id=kb.id, workspace_id="ws1")
    service.delete_document(doc.id)
    assert external.exists()

    uploaded = service.add_upload_bytes(
        "b.txt", b"content", kb_id=kb.id, workspace_id="ws1", mime="text/plain"
    )
    managed_path = Path(uploaded.source_path)
    service.delete_document(uploaded.id)
    assert not managed_path.exists()
    assert not managed_path.parent.exists()


# ---------- reindex / 读取 / 检索 ----------


def test_reindex_kb_requeues_ready_documents(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    first = tmp_path / "a.txt"
    first.write_text(TEXT, encoding="utf-8")
    second = tmp_path / "b.txt"
    second.write_text(TEXT + "补充", encoding="utf-8")
    a = service.add_file(first, kb_id=kb.id, workspace_id="ws1")
    b = service.add_file(second, kb_id=kb.id, workspace_id="ws1")
    assert service.ingest_now(a.id)
    assert service.ingest_now(b.id)
    assert service.get_document(a.id).parse_status == "ready"

    queued = service.reindex_kb(kb.id)

    assert set(queued) == {a.id, b.id}
    assert service.get_document(a.id).parse_status == "pending"


def test_ingest_now_is_sync_and_idempotent(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    path = tmp_path / "a.txt"
    path.write_text(TEXT, encoding="utf-8")
    doc = service.add_file(path, kb_id=kb.id, workspace_id="ws1")

    assert service.ingest_now(doc.id) is True
    assert service.ingest_now(doc.id) is False
    document = service.get_document(doc.id)
    assert document.parse_status == "ready"
    assert document.chunk_count >= 1
    chunk = service.list_chunks(doc.id)[0]
    assert service.get_chunk(chunk.id).doc_id == doc.id


def test_search_passes_through_to_retriever(tmp_path):
    sentinel = object()
    retriever = RecordingRetriever(sentinel)
    service, _ = _build_service(tmp_path, retriever=retriever)

    result = service.search("回滚", workspace_ids=["ws1"], kb_ids=["kb_1"], limit=3)

    assert result is sentinel
    query, kwargs = retriever.calls[0]
    assert query == "回滚"
    assert kwargs["workspace_ids"] == ["ws1"]
    assert kwargs["kb_ids"] == ["kb_1"]
    assert kwargs["limit"] == 3


# ---------- 统计与修复 ----------


def test_stats_reports_counts_and_storage(tmp_path):
    service = _service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    external = tmp_path / "a.txt"
    external.write_text(TEXT, encoding="utf-8")
    service.add_file(external, kb_id=kb.id, workspace_id="ws1")
    service.add_upload_bytes(
        "b.txt", b"content", kb_id=kb.id, workspace_id="ws1", mime="text/plain"
    )

    stats = service.stats(workspace_ids=["ws1"])

    assert stats["kbs"] == 1
    assert stats["documents"] == 2
    assert stats["chunks"] == 0
    assert stats["by_status"] == {"pending": 2}
    assert stats["pending"] == 2
    assert stats["expired_leases"] == 0
    assert stats["vector"]["status"] == "disabled"
    assert stats["vector"]["ok_docs"] == 0
    assert stats["vector"]["pending_docs"] == 2
    assert stats["drift"]["index_version"] == {"": 2}
    assert stats["drift"]["stale_ready"] == 0
    assert stats["db_bytes"] > 0
    assert stats["uploads_bytes"] > 0


def test_stats_counts_expired_leases(tmp_path):
    service, repo = _build_service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    path = tmp_path / "a.txt"
    path.write_text(TEXT, encoding="utf-8")
    doc = service.add_file(path, kb_id=kb.id, workspace_id="ws1")
    assert repo.claim_document(doc.id, lease_owner="dead", lease_seconds=-1)

    stats = service.stats(workspace_ids=["ws1"])

    assert stats["expired_leases"] == 1
    assert stats["by_status"] == {"processing": 1}


def test_repair_scans_vectors_and_requeues_rewrite(tmp_path):
    vector = FakeVector()
    service, _ = _build_service(tmp_path, vector_store=vector, embedder=FakeEmbedder())
    kb = service.create_kb(name="a", workspace_id="ws1")
    path = tmp_path / "a.txt"
    path.write_text(TEXT, encoding="utf-8")
    doc = service.add_file(path, kb_id=kb.id, workspace_id="ws1")
    assert service.ingest_now(doc.id)
    assert service.get_document(doc.id).parse_status == "ready"
    vector.report = RepairReport(
        actions=(
            RepairAction(
                doc_id=doc.id, action="rewrite", expected_chunks=1, actual_chunks=0
            ),
            RepairAction(
                doc_id="doc_orphan", action="purge", expected_chunks=0, actual_chunks=2
            ),
        )
    )

    result = service.repair(vectors=True)

    assert vector.scanned is not None
    assert doc.id in vector.scanned
    assert result["vectors_scanned"] == 2
    assert result["vectors_rewrite"] == 1
    assert result["vectors_purge"] == 1
    assert result["rewritten_docs"] == [doc.id]
    assert result["purged_docs"] == ["doc_orphan"]
    assert "doc_orphan" in vector.deleted
    assert service.get_document(doc.id) is not None
    assert service.get_document(doc.id).parse_status == "pending"


def test_repair_rebuilds_fts(tmp_path):
    service, repo = _build_service(tmp_path)
    kb = service.create_kb(name="a", workspace_id="ws1")
    path = tmp_path / "a.txt"
    path.write_text(TEXT, encoding="utf-8")
    doc = service.add_file(path, kb_id=kb.id, workspace_id="ws1")
    assert service.ingest_now(doc.id)

    result = service.repair(fts=True)

    assert result["fts_rebuilt"] == 1
    assert repo.search_fts(tokenize_query("回滚"), workspace_ids=["ws1"])


def test_repair_purges_orphan_upload_dirs(tmp_path):
    service = _service(tmp_path)
    orphan = tmp_path / "uploads" / "doc_orphan"
    orphan.mkdir(parents=True)
    (orphan / "a.txt").write_text("x", encoding="utf-8")

    result = service.repair(purge=True)

    assert result["files_purged"] == 1
    assert not orphan.exists()


def test_delete_kb_removes_documents_and_vectors(tmp_path):
    vector = FakeVector()
    service, _ = _build_service(tmp_path, vector_store=vector)
    kb = service.create_kb(name="a", workspace_id="ws1")
    path = tmp_path / "a.txt"
    path.write_text(TEXT, encoding="utf-8")
    doc = service.add_file(path, kb_id=kb.id, workspace_id="ws1")

    service.delete_kb(kb.id)

    assert service.get_document(doc.id) is None
    assert service.get_kb(kb.id) is None
    assert kb.id in vector.deleted
    assert path.exists()
