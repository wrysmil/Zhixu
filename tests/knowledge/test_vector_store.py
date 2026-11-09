"""向量派生层测试：使用真实 Chroma（PersistentClient + tmp_path），不 mock。"""

from __future__ import annotations

import hashlib

import pytest

from nanobot.knowledge.vector import (
    BATCH_SIZE,
    VectorDimensionError,
    VectorRecord,
    VectorStore,
    VectorUnavailableError,
    collection_name,
)

chromadb = pytest.importorskip("chromadb")


def _stored_chunk_type():
    """StoredChunk 当前在 repository，Task 5 会迁到 models；两处都兼容。"""
    try:
        from nanobot.knowledge.models import StoredChunk
    except ImportError:  # pragma: no cover - 迁移期兼容
        from nanobot.knowledge.repository import StoredChunk

    return StoredChunk


_STORED_CHUNK = _stored_chunk_type()


def _chunk(
    chunk_id: str,
    index: int,
    *,
    doc_id: str = "doc_1",
    kb_id: str = "kb_1",
    workspace_id: str = "ws1",
    text: str = "内容",
):
    return _STORED_CHUNK(
        row_id=index + 1,
        id=chunk_id,
        doc_id=doc_id,
        kb_id=kb_id,
        workspace_id=workspace_id,
        chunk_index=index,
        content=text,
        heading_path="",
        page=None,
        locator="",
        start_offset=0,
        end_offset=len(text),
        char_count=len(text),
        content_hash="h",
        is_enabled=True,
    )


def _record(chunk, vector):
    return VectorRecord.from_chunk(chunk, vector)


def _store(tmp_path, *, dimensions=2, backend="local", model="m"):
    store = VectorStore(
        tmp_path / "chroma", backend=backend, model=model, dimensions=dimensions
    )
    if not store.available:
        pytest.skip("chromadb unavailable")
    return store


def test_collection_name_is_stable_and_dimension_scoped():
    digest = hashlib.sha1(b"local:m").hexdigest()[:10]
    assert collection_name("local", "m", 512) == f"kc_{digest}_512"
    assert collection_name("local", "m", 512) == collection_name("local", "m", 512)
    assert collection_name("local", "m", 512) != collection_name("local", "m", 1024)
    assert collection_name("local", "m", 512) != collection_name("other", "m", 512)


def test_upsert_query_and_delete(tmp_path):
    store = _store(tmp_path)
    chunks = [_chunk("chk_1", 0), _chunk("chk_2", 1)]
    store.upsert(chunks, [[1.0, 0.0], [0.0, 1.0]])
    hits = store.query([1.0, 0.0], kb_ids=["kb_1"], limit=1)
    assert hits[0][0] == "chk_1"
    assert hits[0][1] == pytest.approx(1.0, abs=1e-5)
    store.delete_document("doc_1")
    assert store.query([1.0, 0.0], kb_ids=["kb_1"], limit=5) == []


def test_query_filters_by_kb_ids(tmp_path):
    store = _store(tmp_path)
    first = _chunk("chk_1", 0)
    other = _chunk("chk_2", 0, doc_id="doc_2", kb_id="kb_2", workspace_id="ws2")
    store.upsert([first, other], [[1.0, 0.0], [1.0, 0.0]])
    assert [hit[0] for hit in store.query([1.0, 0.0], kb_ids=["kb_1"], limit=5)] == ["chk_1"]
    assert [hit[0] for hit in store.query([1.0, 0.0], kb_ids=["kb_2"], limit=5)] == ["chk_2"]


def test_query_never_filters_workspace_id(tmp_path):
    """global KB 的 workspace_id 是属主工作区，按当前工作区过滤会静默漏库。"""
    store = _store(tmp_path)
    own = _chunk("chk_own", 0, kb_id="kb_global", workspace_id="ws_owner")
    other = _chunk("chk_other", 1, kb_id="kb_global", workspace_id="ws_other")
    store.upsert([own, other], [[1.0, 0.0], [1.0, 0.0]])
    hits = store.query([1.0, 0.0], kb_ids=["kb_global"], limit=5)
    assert {hit[0] for hit in hits} == {"chk_own", "chk_other"}


def test_query_merges_extra_where(tmp_path):
    store = _store(tmp_path)
    first = _chunk("chk_1", 0)
    second = _chunk("chk_2", 0, doc_id="doc_2")
    store.upsert([first, second], [[1.0, 0.0], [1.0, 0.0]])
    hits = store.query(
        [1.0, 0.0], kb_ids=["kb_1"], limit=5, where={"doc_id": {"$eq": "doc_2"}}
    )
    assert [hit[0] for hit in hits] == ["chk_2"]


def test_dimension_mismatch_is_isolated(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(VectorDimensionError):
        store.upsert([_chunk("chk_1", 0)], [[1.0, 0.0, 0.0]])
    # 回归：拒绝写入后不得留下空 collection，也不得污染其他维度。
    assert store.exists is False
    assert store.count() == 0
    assert _store(tmp_path, dimensions=3).identity == ("local", "m", 3)


def test_query_rejects_dimension_mismatch(tmp_path):
    store = _store(tmp_path)
    store.upsert([_chunk("chk_1", 0)], [[1.0, 0.0]])
    with pytest.raises(VectorDimensionError):
        store.query([1.0, 0.0, 0.0], kb_ids=["kb_1"], limit=1)


def test_upsert_rejects_mismatched_argument_lengths(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.upsert([_chunk("chk_1", 0), _chunk("chk_2", 1)], [[1.0, 0.0]])


def test_no_empty_collection_is_created_eagerly(tmp_path):
    store = _store(tmp_path)
    assert store.exists is False
    store.upsert([], [])
    assert store.count() == 0
    assert store.exists is False
    assert store.query([1.0, 0.0], kb_ids=[]) == []
    assert store.query([1.0, 0.0], kb_ids=["kb_1"], limit=5) == []
    assert store.available is True
    assert store.exists is False


def test_reupsert_is_idempotent_and_prunes_removed_chunks(tmp_path):
    store = _store(tmp_path)
    chunks = [_chunk("chk_1", 0), _chunk("chk_2", 1)]
    store.upsert(chunks, [[1.0, 0.0], [0.0, 1.0]])
    store.upsert(chunks, [[1.0, 0.0], [0.0, 1.0]])
    assert store.count() == 2

    records = [
        _record(_chunk("chk_1", 0), (1.0, 0.0)),
        _record(_chunk("chk_2", 1), (0.0, 1.0)),
        _record(_chunk("chk_3", 2), (0.5, 0.5)),
    ]
    store.upsert_document("doc_1", records)
    assert store.count() == 3
    store.upsert_document("doc_1", records)
    assert store.count() == 3

    store.upsert_document("doc_1", records[:2])
    assert store.count() == 2
    ids = {hit[0] for hit in store.query([1.0, 0.0], kb_ids=["kb_1"], limit=10)}
    assert ids == {"chk_1", "chk_2"}


def test_upsert_document_with_no_records_drops_vectors(tmp_path):
    store = _store(tmp_path)
    store.upsert_document("doc_1", [_record(_chunk("chk_1", 0), (1.0, 0.0))])
    assert store.count() == 1
    store.upsert_document("doc_1", [])
    assert store.count() == 0


def test_upsert_document_rejects_foreign_doc_id(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.upsert_document(
            "doc_1", [_record(_chunk("chk_1", 0, doc_id="doc_2"), (1.0, 0.0))]
        )


def test_delete_document_and_delete_kb_are_idempotent(tmp_path):
    store = _store(tmp_path)
    store.upsert(
        [
            _chunk("chk_1", 0),
            _chunk("chk_2", 1),
            _chunk("chk_3", 0, doc_id="doc_2", kb_id="kb_2"),
        ],
        [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]],
    )
    store.delete_document("doc_1")
    store.delete_document("doc_1")
    assert store.count() == 1
    store.delete_kb("kb_2")
    store.delete_kb("kb_2")
    assert store.count() == 0
    # 删除不存在 collection 时不得顺手创建。
    store.delete_document("doc_unknown")
    store.delete_kb("kb_unknown")


class _RecordingStore(VectorStore):
    def __init__(self, path, **kwargs):
        super().__init__(path, **kwargs)
        self.batches: list[int] = []

    def _write_batch(self, ids, vectors, metadatas):
        self.batches.append(len(ids))
        super()._write_batch(ids, vectors, metadatas)


def test_upsert_splits_large_input_into_batches(tmp_path):
    store = _RecordingStore(
        tmp_path / "chroma", backend="local", model="m", dimensions=2
    )
    if not store.available:
        pytest.skip("chromadb unavailable")
    total = BATCH_SIZE * 2 + 3
    chunks = [_chunk(f"chk_{index}", index) for index in range(total)]
    vectors = [[1.0, 0.0] for _ in range(total)]
    store.upsert(chunks, vectors)
    assert store.count() == total
    assert store.batches == [BATCH_SIZE, BATCH_SIZE, 3]


def test_scan_reports_missing_orphan_and_stale_vectors(tmp_path):
    store = _store(tmp_path)
    store.upsert(
        [
            _chunk("a1", 0, doc_id="doc_a"),
            _chunk("a2", 1, doc_id="doc_a"),
            _chunk("b1", 0, doc_id="doc_b"),
            _chunk("o1", 0, doc_id="doc_orphan"),
        ],
        [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0], [1.0, 0.0]],
    )
    expected = {
        "doc_a": ["a1", "a3"],
        "doc_b": ["b1"],
        "doc_missing": ["m1"],
    }
    report = store.scan(expected)
    assert report.rewrite_doc_ids == ("doc_a", "doc_missing")
    assert report.purge_doc_ids == ("doc_orphan",)
    by_doc = {action.doc_id: action for action in report.actions}
    assert by_doc["doc_a"].missing_chunk_ids == ("a3",)
    assert by_doc["doc_a"].orphan_chunk_ids == ("a2",)
    assert (by_doc["doc_a"].expected_chunks, by_doc["doc_a"].actual_chunks) == (2, 2)
    assert by_doc["doc_missing"].action == "rewrite"
    assert by_doc["doc_missing"].actual_chunks == 0
    assert by_doc["doc_orphan"].action == "purge"
    assert by_doc["doc_orphan"].orphan_chunk_ids == ("o1",)
    assert "doc_b" not in by_doc
    # 只读：扫描不写库、不删块。
    assert store.count() == 4
    assert store.scan(expected) == report


def test_scan_reports_doc_whose_chunks_all_removed(tmp_path):
    store = _store(tmp_path)
    store.upsert([_chunk("chk_1", 0)], [[1.0, 0.0]])
    report = store.scan({"doc_1": []})
    assert report.purge_doc_ids == ("doc_1",)
    assert report.actions[0].orphan_chunk_ids == ("chk_1",)
    assert store.count() == 1


def test_scan_on_absent_collection_is_empty(tmp_path):
    store = _store(tmp_path)
    empty = store.scan({})
    assert empty.actions == ()
    assert empty.rewrite_doc_ids == ()
    assert empty.purge_doc_ids == ()
    missing = store.scan({"doc_1": ["chk_1"]})
    assert missing.rewrite_doc_ids == ("doc_1",)
    # 只读扫描不得顺手创建空 collection。
    assert store.exists is False


def test_unavailable_store_reports_and_raises(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    store = VectorStore(blocked, backend="local", model="m", dimensions=2)
    if store.available:  # pragma: no cover - 环境差异保护
        pytest.skip("chromadb accepted a file path")
    assert store.error
    with pytest.raises(VectorUnavailableError):
        store.upsert([_chunk("chk_1", 0)], [[1.0, 0.0]])
    with pytest.raises(VectorUnavailableError):
        store.query([1.0, 0.0], kb_ids=["kb_1"], limit=1)
