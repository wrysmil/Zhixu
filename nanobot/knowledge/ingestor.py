"""单文档入库状态机：抽取 → 分块 → 校验 → embedding → 事务写块 → 向量。

契约见 `.ai-runtime-artifacts/specs/2026-11-09-knowledge-storage-spec.md` §10–§12：
- embedding 必须在写事务之外完成，事务里只做「删旧块 → 插新块 → attempt 守卫置 ready」；
- 向量在 SQLite 提交之后 best-effort 写入：成功 `ok`、部分 `partial`、失败 `failed`，
  文档始终 `ready`，关键词路可用；
- 失败路径（源缺失/抽取/分块/校验）不触碰旧块，只把文档置 `failed` + error_code；
- 文档在提交前被删除时静默退出，不回写状态、不记 `failed`。
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from loguru import logger

from nanobot.knowledge.chunker import (
    ChunkDraft,
    KnowledgeChunkError,
    chunk_lines,
    validate_chunks,
)
from nanobot.knowledge.embedding import Embedder, EmbeddingError, EmbeddingUnavailable
from nanobot.knowledge.extractor import (
    KnowledgeExtractionError,
    KnowledgeUnsupportedType,
    extract_lines,
)
from nanobot.knowledge.models import Document, KnowledgeBase
from nanobot.knowledge.repository import ChunkRecord, KnowledgeRepository
from nanobot.knowledge.tokenizer import tokenize_index
from nanobot.knowledge.vector import VectorStore, VectorStoreError

INDEX_VERSION = "norm1:tok1:chunk1"

SOURCE_MISSING = "KNOWLEDGE_SOURCE_MISSING"
UNSUPPORTED_TYPE = "KNOWLEDGE_UNSUPPORTED_TYPE"
EXTRACT_FAILED = "KNOWLEDGE_EXTRACT_FAILED"
EMBEDDING_FAILED = "KNOWLEDGE_EMBEDDING_FAILED"

SOURCE_MISSING_MESSAGE = "源文件不存在：请重新上传或恢复源文件"

DEFAULT_BATCH_SIZE = 64
DEFAULT_LEASE_SECONDS = 600
DEFAULT_LEASE_OWNER = "ingestor"

_BLANK_LINE_SUFFIXES = frozenset({".md", ".txt"})
_FOREIGN_KEY_FAILURE = "FOREIGN KEY constraint failed"


def chunk_id_for(doc_id: str, chunk_index: int) -> str:
    """块 id 只依赖 (doc_id, chunk_index)：重跑同文档得到同一组 id，upsert 幂等。"""
    digest = hashlib.sha256(f"{doc_id}:{chunk_index}".encode("utf-8")).hexdigest()
    return f"chk_{digest[:16]}"


def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class Ingestor:
    """单文档入库执行器；同步、线程内调用（服务层用 `asyncio.to_thread` 包裹）。"""

    def __init__(
        self,
        repo: KnowledgeRepository,
        *,
        embedder: Embedder,
        vector_store: VectorStore | None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        lease_owner: str = DEFAULT_LEASE_OWNER,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self._repo = repo
        self._embedder = embedder
        self._vector = vector_store
        self._batch_size = batch_size
        self._lease_owner = lease_owner
        self._lease_seconds = lease_seconds

    # ---------- 入口 ----------
    def process_document(self, doc_id: str, attempt: int | None = None) -> None:
        """处理一篇已（或待）认领的文档。

        `attempt` 由调用方传入时假定其已 `claim_document`；省略时本方法自行认领
        （`ready` 文档先转 `pending`，即 reindex 语义）。认领失败/文档已删除均静默返回。
        """
        document = self._repo.get_document(doc_id)
        if document is None:
            return
        if attempt is None:
            if document.parse_status == "ready":
                self._repo.mark_reindex_pending(doc_id)
            attempt = self._repo.claim_document(
                doc_id,
                lease_owner=self._lease_owner,
                lease_seconds=self._lease_seconds,
            )
            if attempt is None:
                return

        kb = self._repo.get_kb(document.kb_id)
        if kb is None:
            return
        source = self._source_path(document)
        if source is None:
            self._fail(doc_id, attempt, SOURCE_MISSING, SOURCE_MISSING_MESSAGE)
            return

        try:
            drafts = self._extract_and_chunk(source, kb)
        except KnowledgeUnsupportedType as exc:
            self._fail(doc_id, attempt, UNSUPPORTED_TYPE, str(exc))
            return
        except KnowledgeExtractionError as exc:
            self._fail(doc_id, attempt, EXTRACT_FAILED, str(exc))
            return
        except KnowledgeChunkError as exc:
            self._fail(doc_id, attempt, exc.code, str(exc))
            return

        records = self._records(document, drafts)
        vectors, vector_status = self._embed(document, kb, records)
        if not self._commit(doc_id, attempt, records, vector_status):
            return
        if vectors:
            vector_status = self._write_vectors(doc_id, records, vectors)
            self._set_vector_status(doc_id, attempt, vector_status)
        elif vector_status == "failed":
            # 新块已替换旧块但向量没算成：旧向量与新内容不再对应，best-effort 清掉。
            self._discard_vectors(doc_id)
        logger.debug(
            "knowledge ingest finished: doc={} attempt={} chunks={} vector={}",
            doc_id,
            attempt,
            len(records),
            vector_status,
        )

    # ---------- 读取与分块 ----------
    @staticmethod
    def _source_path(document: Document) -> Path | None:
        if not document.source_path:
            return None
        path = Path(document.source_path)
        return path if path.is_file() else None

    @staticmethod
    def _extract_and_chunk(source: Path, kb: KnowledgeBase) -> list[ChunkDraft]:
        lines = extract_lines(source)
        text = "\n".join(line.text for line in lines)
        mode = (
            "blank_line"
            if source.suffix.lower() in _BLANK_LINE_SUFFIXES
            else "line"
        )
        drafts = chunk_lines(
            lines,
            chunk_size=kb.chunk_size,
            chunk_overlap=kb.chunk_overlap,
            paragraph_mode=mode,
        )
        validate_chunks(
            drafts,
            chunk_size=kb.chunk_size,
            chunk_overlap=kb.chunk_overlap,
            text=text,
        )
        return drafts

    def _records(
        self, document: Document, drafts: list[ChunkDraft]
    ) -> list[ChunkRecord]:
        return [
            ChunkRecord(
                id=chunk_id_for(document.id, index),
                doc_id=document.id,
                kb_id=document.kb_id,
                workspace_id=document.workspace_id,
                chunk_index=index,
                content=draft.content,
                heading_path=draft.heading_path,
                page=draft.page,
                locator=draft.locator,
                start_offset=draft.start_offset,
                end_offset=draft.end_offset,
                char_count=draft.char_count,
                content_hash=content_digest(draft.content),
                search_text=tokenize_index(draft.content),
            )
            for index, draft in enumerate(drafts)
        ]

    # ---------- embedding（不持 DB 事务） ----------
    def _embed(
        self, document: Document, kb: KnowledgeBase, records: list[ChunkRecord]
    ) -> tuple[list[list[float]], str]:
        if not records:
            return [], "skipped"
        if self._vector is None or not self._vector.available:
            return [], "skipped"
        try:
            identity = self._embedder.identity
        except EmbeddingUnavailable:
            return [], "skipped"
        except EmbeddingError:
            return [], "failed"
        if identity[0] == "none":
            return [], "skipped"
        kb_identity = (
            kb.embedding_backend,
            kb.embedding_model,
            kb.embedding_dimensions,
        )
        if kb_identity != identity:
            # KB 记录的模型与当前 embedder 不一致：等 reindex/配置修复，不换模型。
            logger.warning(
                "knowledge embedding mismatch: doc={} kb={}", document.id, kb.id
            )
            return [], "skipped"
        texts = [self._embedding_text(document, record) for record in records]
        try:
            vectors = self._embedder.embed_documents(texts)
        except EmbeddingUnavailable:
            return [], "skipped"
        except EmbeddingError as exc:
            logger.warning(
                "knowledge {}: doc={} error={}", EMBEDDING_FAILED, document.id, exc
            )
            return [], "failed"
        if len(vectors) != len(texts) or any(
            len(vector) != identity[2] for vector in vectors
        ):
            logger.warning(
                "knowledge {}: doc={} expected_dims={}",
                EMBEDDING_FAILED,
                document.id,
                identity[2],
            )
            return [], "failed"
        return vectors, "pending"

    @staticmethod
    def _embedding_text(document: Document, record: ChunkRecord) -> str:
        parts = (document.title, record.heading_path, record.content)
        return "\n".join(part for part in parts if part)

    # ---------- SQLite 提交（单事务） ----------
    def _commit(
        self,
        doc_id: str,
        attempt: int,
        records: list[ChunkRecord],
        vector_status: str,
    ) -> bool:
        try:
            return self._repo.commit_chunks(
                doc_id,
                attempt=attempt,
                chunks=records,
                index_version=INDEX_VERSION,
                vector_status=vector_status,
            )
        except sqlite3.IntegrityError as exc:
            if _FOREIGN_KEY_FAILURE in str(exc):
                # 文档在加载与提交之间被删除：静默退出，不重建状态、不记 failed。
                logger.info("knowledge document deleted before commit: {}", doc_id)
                return False
            raise

    # ---------- 向量（提交后 best-effort） ----------
    def _write_vectors(
        self, doc_id: str, records: list[ChunkRecord], vectors: list[list[float]]
    ) -> str:
        vector = self._vector
        assert vector is not None
        try:
            vector.delete_document(doc_id)
        except VectorStoreError as exc:
            logger.warning("knowledge vector cleanup failed: doc={} error={}", doc_id, exc)
            return "failed"
        written = 0
        for start in range(0, len(records), self._batch_size):
            batch = records[start : start + self._batch_size]
            batch_vectors = vectors[start : start + self._batch_size]
            try:
                vector.upsert(batch, batch_vectors)
            except VectorStoreError as exc:
                logger.warning(
                    "knowledge vector batch failed: doc={} offset={} error={}",
                    doc_id,
                    start,
                    exc,
                )
                break
            written += len(batch)
        if written == len(records):
            return "ok"
        return "partial" if written else "failed"

    def _set_vector_status(self, doc_id: str, attempt: int, status: str) -> None:
        if not self._repo.set_vector_status(doc_id, status, attempt=attempt):
            logger.info(
                "knowledge vector status skipped (stale/deleted): doc={} status={}",
                doc_id,
                status,
            )

    def _discard_vectors(self, doc_id: str) -> None:
        if self._vector is None or not self._vector.available:
            return
        try:
            self._vector.delete_document(doc_id)
        except VectorStoreError as exc:
            logger.warning("knowledge stale vector cleanup failed: doc={} error={}", doc_id, exc)

    # ---------- 失败路径 ----------
    def _fail(self, doc_id: str, attempt: int, code: str, message: str) -> None:
        self._repo.fail_document(
            doc_id, attempt=attempt, error_code=code, error_message=message
        )
