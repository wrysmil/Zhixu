"""向量派生层：按 (backend, model, dimensions) 指纹分 Chroma collection。

契约见 `.ai-runtime-artifacts/specs/2026-11-09-knowledge-storage-spec.md` §8：
- collection 名 `kc_<sha1(backend:model)[:10]>_<dimensions>`，cosine 距离；
- 只存 id（= chunk id）+ 向量 + 最小元数据，正文只留在 SQLite；
- 检索只按可见 kb_id 过滤，绝不过滤 workspace_id（global 库跨工作区可见）；
- 向量是可丢可重建的派生状态：写入失败向上抛，由调用方落 `vector_status`；
  修复扫描只读，永不改动 SQLite。
"""

from __future__ import annotations

import hashlib
import importlib
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from loguru import logger

#: 单次 Chroma 写入的批大小（storage spec §8.2）。
BATCH_SIZE = 64


class ChunkMeta(Protocol):
    """向量层需要的最小块元数据；`StoredChunk` 与 `ChunkRecord` 都满足。"""

    @property
    def id(self) -> str: ...

    @property
    def doc_id(self) -> str: ...

    @property
    def kb_id(self) -> str: ...

    @property
    def workspace_id(self) -> str: ...

    @property
    def chunk_index(self) -> int: ...


class VectorStoreError(RuntimeError):
    """向量层失败；调用方据此把文档标为 `vector_status='failed'`。"""


class VectorUnavailableError(VectorStoreError):
    """chromadb 未安装或客户端不可用；检索退化为关键词单路。"""


class VectorDimensionError(VectorStoreError, ValueError):
    """向量维度与 collection 身份不符：拒绝写入，不污染其他 collection。"""


def collection_name(backend: str, model: str, dimensions: int) -> str:
    """collection 指纹：同一 (backend, model) 不同维度必须落在不同 collection。"""
    digest = hashlib.sha1(f"{backend}:{model}".encode()).hexdigest()[:10]
    return f"kc_{digest}_{dimensions}"


@dataclass(frozen=True, slots=True)
class VectorRecord:
    """一篇文档的一个块的向量写入单元：块身份 + 元数据 + 向量。"""

    chunk_id: str
    vector: tuple[float, ...]
    doc_id: str
    kb_id: str
    workspace_id: str
    chunk_index: int

    @classmethod
    def from_chunk(cls, chunk: ChunkMeta, vector: Sequence[float]) -> VectorRecord:
        return cls(
            chunk_id=chunk.id,
            vector=tuple(float(value) for value in vector),
            doc_id=chunk.doc_id,
            kb_id=chunk.kb_id,
            workspace_id=chunk.workspace_id,
            chunk_index=chunk.chunk_index,
        )


RepairKind = Literal["rewrite", "purge"]


@dataclass(frozen=True, slots=True)
class RepairAction:
    """修复扫描给出的一条动作；只在调用方显式执行时才写回。"""

    doc_id: str
    action: RepairKind
    expected_chunks: int
    actual_chunks: int
    missing_chunk_ids: tuple[str, ...] = ()
    orphan_chunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RepairReport:
    actions: tuple[RepairAction, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.actions

    @property
    def rewrite_doc_ids(self) -> tuple[str, ...]:
        return tuple(item.doc_id for item in self.actions if item.action == "rewrite")

    @property
    def purge_doc_ids(self) -> tuple[str, ...]:
        return tuple(item.doc_id for item in self.actions if item.action == "purge")


def _merge_where(
    kb_ids: Sequence[str], where: Mapping[str, Any] | None
) -> dict[str, Any]:
    """可见 kb_ids 与调用方附加条件取交集；绝不引入 workspace_id。"""
    kb_filter: dict[str, Any] = {"kb_id": {"$in": [str(kb_id) for kb_id in kb_ids]}}
    if not where:
        return kb_filter
    return {"$and": [kb_filter, dict(where)]}


def _build_report(
    expected: Mapping[str, Sequence[str]], actual: Mapping[str, set[str]]
) -> RepairReport:
    actions: list[RepairAction] = []
    for doc_id in sorted(expected):
        wanted = {str(chunk_id) for chunk_id in expected[doc_id]}
        present = set(actual.get(doc_id, set()))
        if wanted == present:
            continue
        if not wanted:
            actions.append(
                RepairAction(
                    doc_id=doc_id,
                    action="purge",
                    expected_chunks=0,
                    actual_chunks=len(present),
                    orphan_chunk_ids=tuple(sorted(present)),
                )
            )
            continue
        actions.append(
            RepairAction(
                doc_id=doc_id,
                action="rewrite",
                expected_chunks=len(wanted),
                actual_chunks=len(present),
                missing_chunk_ids=tuple(sorted(wanted - present)),
                orphan_chunk_ids=tuple(sorted(present - wanted)),
            )
        )
    for doc_id in sorted(set(actual) - set(expected)):
        present = actual[doc_id]
        actions.append(
            RepairAction(
                doc_id=doc_id,
                action="purge",
                expected_chunks=0,
                actual_chunks=len(present),
                orphan_chunk_ids=tuple(sorted(present)),
            )
        )
    actions.sort(key=lambda item: item.doc_id)
    return RepairReport(actions=tuple(actions))


class VectorStore:
    """单个 collection 的读写门面；collection 在首次写入时才创建。"""

    def __init__(
        self, path: Path | str, *, backend: str, model: str, dimensions: int
    ) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self._path = Path(path)
        self._backend = backend
        self._model = model
        self._dimensions = dimensions
        self._name = collection_name(backend, model, dimensions)
        self._lock = threading.RLock()
        self._client: Any = None
        self._cached: Any = None
        self._error: str | None = None
        self._available = self._load()

    def _load(self) -> bool:
        try:
            module = importlib.import_module("chromadb")
            factory = getattr(module, "PersistentClient")
            self._client = factory(path=str(self._path))
        except Exception as exc:
            self._error = f"{type(exc).__name__}: {exc}"
            logger.warning("knowledge vector unavailable: {}", exc)
            return False
        return True

    # ---------- 状态 ----------
    @property
    def available(self) -> bool:
        return self._available

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def name(self) -> str:
        return self._name

    @property
    def identity(self) -> tuple[str, str, int]:
        return (self._backend, self._model, self._dimensions)

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def exists(self) -> bool:
        if not self._available:
            return False
        return self._collection(create=False) is not None

    def count(self) -> int:
        collection = self._collection(create=False)
        if collection is None:
            return 0
        return int(cast(int, collection.count()))

    def _require(self) -> Any:
        if not self._available or self._client is None:
            raise VectorUnavailableError(self._error or "vector store unavailable")
        return self._client

    def _has_collection(self, client: Any) -> bool:
        try:
            names = client.list_collections()
        except Exception as exc:
            raise VectorStoreError(f"chroma list_collections failed: {exc}") from exc
        for item in names:
            name = item if isinstance(item, str) else str(getattr(item, "name", ""))
            if name == self._name:
                return True
        return False

    def _collection(self, *, create: bool) -> Any:
        """读路径 `create=False` 时不存在就返回 None，不顺手创建空 collection。"""
        client = self._require()
        with self._lock:
            if self._cached is not None:
                return self._cached
            if not create and not self._has_collection(client):
                return None
            try:
                self._cached = client.get_or_create_collection(
                    name=self._name, metadata={"hnsw:space": "cosine"}
                )
            except Exception as exc:
                raise VectorStoreError(
                    f"chroma collection {self._name} unavailable: {exc}"
                ) from exc
            return self._cached

    # ---------- 写入 ----------
    def _validate_vector(self, vector: Sequence[float]) -> list[float]:
        values = [float(value) for value in vector]
        if len(values) != self._dimensions:
            raise VectorDimensionError(
                f"vector dimension {len(values)} does not match collection"
                f" {self._name} dimension {self._dimensions}"
            )
        return values

    @staticmethod
    def _metadata(chunk: ChunkMeta) -> dict[str, Any]:
        return {
            "chunk_id": chunk.id,
            "doc_id": chunk.doc_id,
            "kb_id": chunk.kb_id,
            "workspace_id": chunk.workspace_id,
            "chunk_index": chunk.chunk_index,
        }

    def _write(
        self,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        metadatas: Sequence[Mapping[str, Any]],
    ) -> int:
        if not ids:
            return 0
        for start in range(0, len(ids), BATCH_SIZE):
            end = start + BATCH_SIZE
            self._write_batch(ids[start:end], vectors[start:end], metadatas[start:end])
        return len(ids)

    def _write_batch(
        self,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        metadatas: Sequence[Mapping[str, Any]],
    ) -> None:
        collection = self._collection(create=True)
        try:
            collection.upsert(
                ids=list(ids),
                embeddings=[list(vector) for vector in vectors],
                metadatas=[dict(metadata) for metadata in metadatas],
            )
        except Exception as exc:
            raise VectorStoreError(f"chroma upsert failed: {exc}") from exc

    def upsert(
        self, chunks: Sequence[ChunkMeta], vectors: Sequence[Sequence[float]]
    ) -> int:
        """按 chunk id 幂等写入；空输入不创建 collection。"""
        if len(chunks) != len(vectors):
            raise ValueError(
                f"chunks/vectors length mismatch: {len(chunks)} != {len(vectors)}"
            )
        if not chunks:
            return 0
        ids = [chunk.id for chunk in chunks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate chunk ids in one upsert call")
        values = [self._validate_vector(vector) for vector in vectors]
        metadatas = [self._metadata(chunk) for chunk in chunks]
        return self._write(ids, values, metadatas)

    def upsert_document(self, doc_id: str, records: Sequence[VectorRecord]) -> int:
        """整篇重写：先按 doc_id 删旧向量，再分批写入，保证幂等且不残留旧块。"""
        ids = [record.chunk_id for record in records]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate chunk ids for document {doc_id}")
        values: list[list[float]] = []
        metadatas: list[dict[str, Any]] = []
        for record in records:
            if record.doc_id != doc_id:
                raise ValueError(
                    f"record {record.chunk_id} belongs to {record.doc_id}, not {doc_id}"
                )
            values.append(self._validate_vector(record.vector))
            metadatas.append(
                {
                    "chunk_id": record.chunk_id,
                    "doc_id": record.doc_id,
                    "kb_id": record.kb_id,
                    "workspace_id": record.workspace_id,
                    "chunk_index": record.chunk_index,
                }
            )
        self.delete_document(doc_id)
        return self._write(ids, values, metadatas)

    def delete_document(self, doc_id: str) -> None:
        collection = self._collection(create=False)
        if collection is None:
            return
        try:
            collection.delete(where={"doc_id": doc_id})
        except Exception as exc:
            raise VectorStoreError(f"chroma delete document failed: {exc}") from exc

    def delete_kb(self, kb_id: str) -> None:
        collection = self._collection(create=False)
        if collection is None:
            return
        try:
            collection.delete(where={"kb_id": kb_id})
        except Exception as exc:
            raise VectorStoreError(f"chroma delete kb failed: {exc}") from exc

    # ---------- 读取 ----------
    def query(
        self,
        vector: Sequence[float],
        *,
        kb_ids: Sequence[str],
        limit: int = 10,
        where: Mapping[str, Any] | None = None,
    ) -> list[tuple[str, float]]:
        """按已解析的可见 kb_ids 检索；返回 (chunk_id, 余弦相似度) 降序。"""
        if not kb_ids or limit <= 0:
            return []
        values = self._validate_vector(vector)
        collection = self._collection(create=False)
        if collection is None:
            return []
        try:
            result = collection.query(
                query_embeddings=[values],
                n_results=int(limit),
                where=_merge_where(kb_ids, where),
                include=["distances", "metadatas"],
            )
        except Exception as exc:
            raise VectorStoreError(f"chroma query failed: {exc}") from exc
        payload = cast(dict[str, Any], result)
        ids = cast(list[list[str]], payload.get("ids") or [])
        distances = cast(list[list[float]] | None, payload.get("distances"))
        if not ids or not ids[0] or not distances or not distances[0]:
            return []
        return [
            (str(chunk_id), max(0.0, 1.0 - float(distance)))
            for chunk_id, distance in zip(ids[0], distances[0], strict=False)
        ]

    def query_kbs(
        self, kb_ids: Sequence[str], vector: Sequence[float], limit: int = 10
    ) -> list[tuple[str, float]]:
        """位置参数入口，等价于 `query(vector, kb_ids=kb_ids, limit=limit)`。"""
        return self.query(vector, kb_ids=kb_ids, limit=limit)

    # ---------- 修复 ----------
    def scan(self, expected: Mapping[str, Sequence[str]]) -> RepairReport:
        """只读对账：expected 是 doc_id → SQLite 现有 chunk id 集合。

        返回 rewrite（缺块/多块/块集合不一致）与 purge（SQLite 已无该文档或已无块）
        两类动作；不写 Chroma、不碰 SQLite。
        """
        collection = self._collection(create=False)
        actual: dict[str, set[str]] = {}
        if collection is not None:
            try:
                payload = cast(dict[str, Any], collection.get(include=["metadatas"]))
            except Exception as exc:
                raise VectorStoreError(f"chroma get failed: {exc}") from exc
            ids = cast(list[str], payload.get("ids") or [])
            metadatas = cast(list[Any], payload.get("metadatas") or [])
            for chunk_id, metadata in zip(ids, metadatas, strict=False):
                doc_id = "" if metadata is None else str(metadata.get("doc_id") or "")
                if not doc_id:
                    logger.warning(
                        "knowledge vector chunk without doc_id: {}", chunk_id
                    )
                    continue
                actual.setdefault(doc_id, set()).add(str(chunk_id))
        return _build_report(expected, actual)
