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
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from nanobot.knowledge.chunker import (
    DEFAULT_SEPARATORS,
    ChunkDraft,
    KnowledgeChunkError,
    chunk_by_separators,
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
from nanobot.knowledge.questions import (
    DEFAULT_QUESTION_COUNT,
    QuestionGenerationError,
    QuestionGenerator,
    question_id,
    question_search_text,
)
from nanobot.knowledge.repository import (
    ChunkRecord,
    KnowledgeRepository,
    QuestionRecord,
)
from nanobot.knowledge.tokenizer import tokenize_index
from nanobot.knowledge.vector import VectorStore, VectorStoreError

INDEX_VERSION = "norm1:tok2:chunk1"

#: 分词模式 → index_version 的 tok 段。分词行为一变，索引必须重建
#: （建索引与查询必须用同一个分词器，否则 key 对不上，永远零命中）。
_TOKENIZER_VERSION = {
    "jieba": "tok2",
    "unigram_bigram": "tok1",
    "bigram": "tok1",
}


def index_version_for(tokenizer_mode: str = "jieba") -> str:
    """按分词模式生成 index_version。"""
    token = _TOKENIZER_VERSION.get(tokenizer_mode, "tok2")
    return f"norm1:{token}:chunk1"

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


def _drafts_from_custom(chunks: list[dict[str, object]]) -> list[ChunkDraft]:
    """把导入前编辑过的分块转成草稿：内容即边界，偏移按内容自身计算。"""
    drafts: list[ChunkDraft] = []
    for chunk in chunks:
        content = str(chunk.get("content") or "").strip()
        if not content:
            continue
        page = chunk.get("page")
        drafts.append(
            ChunkDraft(
                content=content,
                start_offset=0,
                end_offset=len(content),
                heading_path=str(chunk.get("heading_path") or ""),
                page=page if isinstance(page, int) else None,
                locator=str(chunk.get("locator") or ""),
            )
        )
    if not drafts:
        raise KnowledgeChunkError("KNOWLEDGE_CHUNK_INVALID", "custom chunks are empty")
    return drafts


def extract_drafts(
    source: Path,
    *,
    chunk_size: int,
    chunk_overlap: int,
    chunk_mode: str = "smart",
    separators: tuple[str, ...] | None = None,
) -> tuple[list[ChunkDraft], str]:
    """抽取并按指定模式分块，返回 (草稿, 归一化全文)；预览与入库共用。"""
    lines = extract_lines(source)
    text = "\n".join(line.text for line in lines)
    if not text.strip():
        # 扫描件/图片版 PDF 没有文字层，给出可执行的提示，而不是裸的 no chunks produced。
        if source.suffix.lower() == ".pdf":
            raise KnowledgeChunkError(
                "KNOWLEDGE_TEXT_EMPTY",
                "PDF 中没有可提取的文字（多为扫描件或图片版），请先做 OCR，"
                "或改用可复制文字的 PDF",
            )
        raise KnowledgeChunkError(
            "KNOWLEDGE_TEXT_EMPTY",
            "文档中没有可提取的文字内容，请检查文件是否为空或只有图片",
        )
    if chunk_mode == "separator":
        drafts = chunk_by_separators(
            lines,
            separators=separators or DEFAULT_SEPARATORS,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        validate_chunks(
            drafts,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            text=text,
            allow_small=True,
        )
        return drafts, text
    if chunk_mode != "smart":
        raise ValueError(f"unknown chunk_mode: {chunk_mode}")
    mode = (
        "blank_line"
        if source.suffix.lower() in _BLANK_LINE_SUFFIXES
        else "line"
    )
    drafts = chunk_lines(
        lines,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        paragraph_mode=mode,
    )
    validate_chunks(
        drafts,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        text=text,
    )
    return drafts, text


def extract_and_chunk(
    source: Path, *, chunk_size: int, chunk_overlap: int
) -> list[ChunkDraft]:
    """按入库同一套规则抽取并分块；导入前预览复用，避免两处规则漂移。"""
    drafts, _ = extract_drafts(
        source, chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    return drafts


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
        tokenizer_mode: str = "jieba",
        identity_resolver: Callable[[KnowledgeBase], tuple[Embedder, VectorStore] | None]
        | None = None,
        question_generator: QuestionGenerator | None = None,
        question_count: int = DEFAULT_QUESTION_COUNT,
        graph_hook: Callable[[str], None] | None = None,
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
        self._tokenizer_mode = tokenizer_mode
        self._identity_resolver = identity_resolver
        self._question_generator = question_generator
        self._question_count = max(1, question_count)
        self._graph_hook = graph_hook

    def set_graph_hook(self, hook: Callable[[str], None] | None) -> None:
        """服务装配完成后注入图抽取钩子（避免构造期循环引用）。"""
        self._graph_hook = hook

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

        custom = self._repo.list_custom_chunks(doc_id)
        if custom:
            # 导入前编辑过的分块：原样重嵌入（重索引也复用），不再重新切分。
            drafts = _drafts_from_custom(custom)
        else:
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
        target = self._vector_target(kb)
        vectors, vector_status = self._embed(document, kb, records, target)
        if not self._commit(doc_id, attempt, records, vector_status):
            return
        if vectors and target is not None:
            vector_status = self._write_vectors(
                doc_id, records, vectors, target[1]
            )
            self._set_vector_status(doc_id, attempt, vector_status)
        elif vector_status == "failed" and target is not None:
            # 新块已替换旧块但向量没算成：旧向量与新内容不再对应，best-effort 清掉。
            self._discard_vectors(target[1], doc_id)
        if self._question_generator is not None:
            self._generate_questions(document, target, records)
        if self._graph_hook is not None:
            try:
                self._graph_hook(document.id)
            except Exception as exc:  # noqa: BLE001 - 图抽取是增强项
                logger.warning(
                    "knowledge graph enqueue skipped: doc={} error={!r}",
                    document.id,
                    exc,
                )
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
        return extract_and_chunk(
            source, chunk_size=kb.chunk_size, chunk_overlap=kb.chunk_overlap
        )

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
                search_text=tokenize_index(draft.content, mode=self._tokenizer_mode),
            )
            for index, draft in enumerate(drafts)
        ]

    # ---------- embedding（不持 DB 事务） ----------
    def _vector_target(self, kb: KnowledgeBase) -> tuple[Embedder, VectorStore] | None:
        """该库自己的 (embedder, 向量库)；未配置 resolver 时退回复用全局实例。"""
        if self._identity_resolver is not None:
            return self._identity_resolver(kb)
        if self._vector is None:
            return None
        return self._embedder, self._vector

    def _embed(
        self,
        document: Document,
        kb: KnowledgeBase,
        records: list[ChunkRecord],
        target: tuple[Embedder, VectorStore] | None,
    ) -> tuple[list[list[float]], str]:
        if not records or target is None:
            return [], "skipped"
        embedder, vector = target
        if not vector.available:
            return [], "skipped"
        try:
            identity = embedder.identity
        except EmbeddingUnavailable:
            return [], "skipped"
        except EmbeddingError:
            return [], "failed"
        if identity[0] == "none":
            return [], "skipped"
        if self._identity_resolver is None:
            kb_identity = (
                kb.embedding_backend,
                kb.embedding_model,
                kb.embedding_dimensions,
            )
            if kb_identity != identity:
                # 单 embedder 兼容路径：KB 记录的模型与当前 embedder 不一致，
                # 等 reindex/配置修复，不换模型。
                logger.warning(
                    "knowledge embedding mismatch: doc={} kb={}", document.id, kb.id
                )
                return [], "skipped"
        texts = [self._embedding_text(document, record) for record in records]
        try:
            vectors = embedder.embed_documents(texts)
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

    # ---------- 入库期问题生成（doc2query） ----------
    def _generate_questions(
        self,
        document: Document,
        target: tuple[Embedder, VectorStore] | None,
        records: list[ChunkRecord],
    ) -> None:
        """提交后生成问题并作为独立行索引；任何失败只降级，不改变文档状态。"""
        generator = self._question_generator
        if generator is None:
            return
        if not records:
            self._replace_questions(document.id, [], [], target)
            return
        questions: list[QuestionRecord] = []
        texts: list[str] = []
        for index, record in enumerate(records):
            prev_content = records[index - 1].content if index > 0 else ""
            next_content = (
                records[index + 1].content if index + 1 < len(records) else ""
            )
            try:
                generated = generator.generate(
                    record.content,
                    prev_content=prev_content,
                    next_content=next_content,
                    doc_name=document.title,
                    question_count=self._question_count,
                )
            except QuestionGenerationError as exc:
                logger.warning(
                    "knowledge question generation failed: doc={} chunk={} error={}",
                    document.id,
                    record.id,
                    exc,
                )
                continue
            except Exception as exc:  # noqa: BLE001 - 生成是增强项，逐块失败逐块跳过
                logger.warning(
                    "knowledge question generation error: doc={} chunk={} error={!r}",
                    document.id,
                    record.id,
                    exc,
                )
                continue
            for offset, question in enumerate(generated[: self._question_count]):
                questions.append(
                    QuestionRecord(
                        id=question_id(record.id, offset),
                        chunk_id=record.id,
                        doc_id=document.id,
                        kb_id=document.kb_id,
                        workspace_id=document.workspace_id,
                        chunk_index=record.chunk_index,
                        question_index=offset,
                        question=question,
                        search_text=question_search_text(question, document.title),
                    )
                )
                texts.append(
                    f"{document.title}\n{question}" if document.title else question
                )
        self._replace_questions(document.id, questions, texts, target)

    def _replace_questions(
        self,
        doc_id: str,
        questions: list[QuestionRecord],
        texts: list[str],
        target: tuple[Embedder, VectorStore] | None,
    ) -> None:
        try:
            self._repo.replace_questions(doc_id, questions)
        except Exception as exc:  # noqa: BLE001 - 问题只是召回增强
            logger.warning(
                "knowledge question rows skipped: doc={} error={!r}", doc_id, exc
            )
            return
        if not questions or target is None:
            return
        embedder, vector = target
        if not vector.available:
            return
        try:
            vectors = embedder.embed_documents(texts)
        except (EmbeddingUnavailable, EmbeddingError) as exc:
            logger.warning(
                "knowledge question vectors skipped: doc={} error={}", doc_id, exc
            )
            return
        if len(vectors) != len(questions):
            logger.warning(
                "knowledge question vectors skipped: doc={} expected={} got={}",
                doc_id,
                len(questions),
                len(vectors),
            )
            return
        for start in range(0, len(questions), self._batch_size):
            batch = questions[start : start + self._batch_size]
            batch_vectors = vectors[start : start + self._batch_size]
            try:
                vector.upsert(batch, batch_vectors)
            except VectorStoreError as exc:
                logger.warning(
                    "knowledge question vector batch failed: doc={} offset={} error={}",
                    doc_id,
                    start,
                    exc,
                )
                break

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
                index_version=index_version_for(self._tokenizer_mode),
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
        self,
        doc_id: str,
        records: list[ChunkRecord],
        vectors: list[list[float]],
        vector: VectorStore,
    ) -> str:
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

    def _discard_vectors(self, vector: VectorStore, doc_id: str) -> None:
        if not vector.available:
            return
        try:
            vector.delete_document(doc_id)
        except VectorStoreError as exc:
            logger.warning("knowledge stale vector cleanup failed: doc={} error={}", doc_id, exc)

    # ---------- 失败路径 ----------
    def _fail(self, doc_id: str, attempt: int, code: str, message: str) -> None:
        self._repo.fail_document(
            doc_id, attempt=attempt, error_code=code, error_message=message
        )
