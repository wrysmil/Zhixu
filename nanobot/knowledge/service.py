"""进程级 KnowledgeService：路径解析、去重、单 worker 队列、统计与修复。

契约见 `.ai-runtime-artifacts/specs/2026-11-09-knowledge-center-spec.md` §11 / §14 与
`.ai-runtime-artifacts/specs/2026-11-09-knowledge-storage-spec.md` §9–§13：

- 路径：`database_path or get_knowledge_dir()/"state.db"`，向量目录默认
  `<db 目录>/chromadb`，上传副本落 `<db 目录>/uploads/<doc_id>/<safe-name>`；
- 单例按解析后的 DB 路径缓存（Chroma 每 client 一份 HNSW 内存副本）；
- 队列：单 worker、串行处理；SQLite / embedding 都在线程里跑，不阻塞事件循环；
- 启动恢复：过期租约回收 + `pending` 文档重新入队；停止时确定性地取消并清空队列；
- 删除：先删行、再删向量、最后删托管副本；`external` 源文件永不删除。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import threading
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.schema import KnowledgeConfig, KnowledgeEmbeddingConfig
from nanobot.knowledge.database import KnowledgeDatabase
from nanobot.knowledge.embedding import (
    Embedder,
    EmbeddingError,
    EmbeddingUnavailable,
    NoneEmbedder,
    build_embedder,
)
from nanobot.knowledge.extractor import KnowledgeExtractionError, KnowledgeUnsupportedType
from nanobot.knowledge.files import (
    hash_file,
    is_within,
    safe_filename,
    write_bytes_atomic,
)
from nanobot.knowledge.fusion import SearchHit, SearchResult
from nanobot.knowledge.graph.extractor import build_graph_extractor
from nanobot.knowledge.graph.neo4j_store import Neo4jGraphStore
from nanobot.knowledge.graph.repo import GraphRepository
from nanobot.knowledge.graph.route import GraphRoute
from nanobot.knowledge.graph.service import GraphService
from nanobot.knowledge.ingestor import INDEX_VERSION, Ingestor
from nanobot.knowledge.models import Document, KnowledgeBase, new_id
from nanobot.knowledge.questions import (
    DEFAULT_QUESTION_COUNT,
    QuestionGenerator,
    build_question_generator,
    question_count_from,
)
from nanobot.knowledge.repository import (
    DocumentStats,
    DuplicateDocumentPathError,
    KnowledgeRepository,
)
from nanobot.knowledge.rerank import (
    Reranker,
    RerankSettings,
    build_reranker,
)
from nanobot.knowledge.rerank import (
    rerank_settings as rerank_settings_for,
)
from nanobot.knowledge.retriever import Retriever, RetrieverSettings
from nanobot.knowledge.vector import RepairAction, VectorStore, VectorStoreError

#: 按库解析 (embedder, 向量库)；`None` 表示该库只走关键词路。
IdentityResolver = Callable[[KnowledgeBase], tuple[Embedder, VectorStore] | None]

_WORKER_LEASE_OWNER = "gateway"
_CLI_LEASE_OWNER = "cli"

_DEFAULT_CHUNK_SIZE = 800
_DEFAULT_CHUNK_OVERLAP = 120
_MAX_LIST_LIMIT = 1_000_000

# 上传白名单：后缀与 MIME 双校验（storage spec §9.1，与 WebUI 上传同口径）。
_UPLOAD_SUFFIXES = frozenset({".pdf", ".docx", ".xlsx", ".pptx", ".md", ".txt"})
_UPLOAD_MIMES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/markdown",
        "text/plain",
    }
)


class KnowledgeServiceError(RuntimeError):
    """服务层参数/状态错误（区别于入库期的抽取/分块错误）。"""


@dataclass(frozen=True, slots=True)
class AddOutcome:
    """`add_file` 结果：既有行被复用（去重/新版本）时 `duplicate=True`。"""

    document: Document
    duplicate: bool = False

    @property
    def id(self) -> str:
        return self.document.id

    @property
    def file_hash(self) -> str:
        return self.document.file_hash


def _ensure_private_dir(path: Path) -> Path:
    """逐级创建目录，新建目录权限 0700；已存在的目录不改权限。"""
    missing: list[Path] = []
    cursor = Path(path)
    while not cursor.exists():
        missing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    for directory in reversed(missing):
        with suppress(FileExistsError):
            os.mkdir(directory, 0o700)
    return Path(path)


def _chmod_private_file(path: Path) -> None:
    with suppress(OSError):
        os.chmod(path, 0o600)


def _harden_sqlite_files(path: Path) -> None:
    """DB 主文件与 WAL/SHM 附属文件统一 0600（目录已由调用方置 0700）。"""
    _chmod_private_file(path)
    for suffix in ("-wal", "-shm"):
        _chmod_private_file(path.with_name(path.name + suffix))


def _directory_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            with suppress(OSError):
                total += item.stat().st_size
    return total


def _safe_identity(embedder: Embedder) -> tuple[str, str, int]:
    try:
        return embedder.identity
    except (EmbeddingUnavailable, EmbeddingError) as exc:
        logger.warning("knowledge embedding identity unavailable: {}", exc)
        return ("none", "", 0)


class _EmbedderRegistry:
    """按 `(backend, model, dimensions)` 缓存 embedder 与向量库（检索 spec §3.1）。

    KB 行里记录的是建库时解析出的具体身份；不同库可以指向不同 embedding 模型。
    同一身份只装配一次，检索与入库都从这里取「这个库自己的」embedder/collection；
    全局配置命中的身份直接复用进程级实例，不重复加载模型。
    """

    def __init__(
        self,
        embedding: KnowledgeEmbeddingConfig,
        vector_path: Path,
        *,
        default: tuple[Embedder, VectorStore] | None = None,
    ) -> None:
        self._config = embedding
        self._vector_path = Path(vector_path)
        self._default = default
        self._cache: dict[tuple[str, str, int], tuple[Embedder, VectorStore]] = {}
        self._lock = threading.Lock()

    def resolve(self, kb: KnowledgeBase) -> tuple[Embedder, VectorStore] | None:
        """返回该库的 (embedder, 向量库)；`none` 或维度未定 → `None`（只走关键词）。"""
        identity = (
            kb.embedding_backend,
            kb.embedding_model,
            kb.embedding_dimensions,
        )
        if identity[0] == "none" or identity[2] <= 0:
            return None
        with self._lock:
            cached = self._cache.get(identity)
        if cached is not None:
            return cached
        if self._default is not None and self._default[1].identity == identity:
            resolved = self._default
        else:
            backend, model, dimensions = identity
            resolved = (
                self._build_embedder(backend, model, dimensions),
                VectorStore(
                    self._vector_path,
                    backend=backend,
                    model=model,
                    dimensions=dimensions,
                ),
            )
        with self._lock:
            return self._cache.setdefault(identity, resolved)

    def _build_embedder(self, backend: str, model: str, dimensions: int) -> Embedder:
        cfg = self._config
        return build_embedder(
            backend=backend,
            model=model,
            dimensions=dimensions,
            device=cfg.device,
            download_source=cfg.download_source,
            local_model_dir=cfg.local_model_dir,
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            api_key_env=cfg.api_key_env,
            batch_size=cfg.batch_size,
            timeout_seconds=cfg.timeout_seconds,
        )


def _graph_settings(graph: Any) -> dict[str, Any]:
    """`KnowledgeGraphConfig` → `RetrieverSettings` 的图字段（唯一配置来源）。"""
    expansion = graph.expansion
    return {
        "graph_enabled": bool(graph.enabled and expansion.enabled),
        "graph_hops": max(1, min(2, int(expansion.hops))),
        "graph_seed_limit": max(1, int(expansion.seed_limit)),
        "graph_max_entities": max(1, int(expansion.max_entities)),
        "graph_max_chunks": max(1, int(expansion.max_chunks)),
        "graph_timeout_seconds": float(expansion.timeout_seconds),
        "graph_query_extraction_timeout_seconds": float(
            getattr(expansion, "query_extraction_timeout_seconds", 5.0)
        ),
    }


def _retriever_settings(retrieval: Any, graph: Any = None) -> RetrieverSettings:
    base = _base_retriever_settings(retrieval)
    if graph is None:
        return base
    return replace(base, **_graph_settings(graph))


def _base_retriever_settings(retrieval: Any) -> RetrieverSettings:
    """`KnowledgeRetrievalConfig` → `RetrieverSettings`；`tool_output_chars` 留在工具层。"""
    if retrieval is None:
        return RetrieverSettings()
    defaults = RetrieverSettings()
    return RetrieverSettings(
        vector_weight=float(getattr(retrieval, "vector_weight", defaults.vector_weight)),
        keyword_weight=float(
            getattr(retrieval, "keyword_weight", defaults.keyword_weight)
        ),
        rrf_k=int(getattr(retrieval, "rrf_k", defaults.rrf_k)),
        pool_size=int(getattr(retrieval, "pool_size", defaults.pool_size)),
        keyword_pool_size=int(
            getattr(retrieval, "keyword_pool_size", defaults.keyword_pool_size)
        ),
        default_limit=int(getattr(retrieval, "default_limit", defaults.default_limit)),
        max_limit=int(getattr(retrieval, "max_limit", defaults.max_limit)),
        mmr_lambda=float(getattr(retrieval, "mmr_lambda", defaults.mmr_lambda)),
        rerank_top_k=int(getattr(retrieval, "rerank_top_k", defaults.rerank_top_k)),
        parent_max_chars=int(
            getattr(retrieval, "parent_max_chars", defaults.parent_max_chars)
        ),
        parent_min_chars=int(
            getattr(retrieval, "parent_min_chars", defaults.parent_min_chars)
        ),
        short_chunk_chars=int(
            getattr(retrieval, "short_chunk_chars", defaults.short_chunk_chars)
        ),
        dedup_containment=float(
            getattr(retrieval, "dedup_containment", defaults.dedup_containment)
        ),
        dedup_unique_ratio=float(
            getattr(retrieval, "dedup_unique_ratio", defaults.dedup_unique_ratio)
        ),
        dedup_jaccard=float(
            getattr(retrieval, "dedup_jaccard", defaults.dedup_jaccard)
        ),
        dedup_length_ratio=float(
            getattr(retrieval, "dedup_length_ratio", defaults.dedup_length_ratio)
        ),
        vector_threshold=float(
            getattr(retrieval, "vector_threshold", defaults.vector_threshold)
        ),
        keyword_threshold=float(
            getattr(retrieval, "keyword_threshold", defaults.keyword_threshold)
        ),
        expand_on_low_recall=bool(
            getattr(retrieval, "expand_on_low_recall", defaults.expand_on_low_recall)
        ),
        expansion_limit=int(
            getattr(retrieval, "expansion_limit", defaults.expansion_limit)
        ),
    )


class KnowledgeService:
    """知识域门面：进程内唯一，同步 API；异步生命周期只负责 worker。"""

    def __init__(
        self,
        *,
        database: KnowledgeDatabase,
        repo: KnowledgeRepository,
        embedder: Embedder,
        vector_store: VectorStore | None,
        ingestor: Ingestor,
        retriever: Retriever,
        uploads_dir: Path,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP,
        reranker: Reranker | None = None,
        rerank_settings: RerankSettings | None = None,
        tokenizer_mode: str = "jieba",
        identity_resolver: IdentityResolver | None = None,
        graph: GraphService | None = None,
    ) -> None:
        self._db = database
        self._repo = repo
        self._embedder = embedder
        self._vector = vector_store
        self._ingestor = ingestor
        self._retriever = retriever
        self._uploads = Path(uploads_dir)
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._reranker = reranker
        self._rerank_settings = rerank_settings or RerankSettings()
        self._tokenizer_mode = tokenizer_mode
        self._identity_resolver = identity_resolver
        self._identity: tuple[str, str, int] | None = None
        self._graph = graph
        self._graph_route: GraphRoute | None = None
        _ensure_private_dir(self._uploads)

        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._queued: set[str] = set()
        self._queued_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker: asyncio.Task[None] | None = None
        self._graph_queue: asyncio.Queue[str] = asyncio.Queue()
        self._graph_queued: set[str] = set()
        self._graph_worker: asyncio.Task[None] | None = None

    # ---------- 构造 ----------
    @classmethod
    def local(
        cls,
        database_path: str | Path,
        *,
        embedder: Embedder | None = None,
        vector_store: VectorStore | None = None,
        retrieval: Any = None,
        chunking: Any = None,
        uploads_dir: str | Path | None = None,
        rerank: Any = None,
        identity_resolver: IdentityResolver | None = None,
        question_generator: QuestionGenerator | None = None,
        question_count: int = DEFAULT_QUESTION_COUNT,
        graph: GraphService | None = None,
        graph_factory: Callable[[KnowledgeRepository], GraphService | None]
        | None = None,
    ) -> KnowledgeService:
        """显式路径装配（测试 / CLI 单进程）；默认无 embedding、无向量层。

        `identity_resolver` 非空时，检索与入库都按 KB 自己的 embedding 身份取
        embedder/向量库——这是「每个库配自己的模型」真正生效的入口。
        """
        path = Path(database_path).expanduser().resolve()
        _ensure_private_dir(path.parent)
        database = KnowledgeDatabase(path)
        database.init_schema()
        _harden_sqlite_files(path)
        repo = KnowledgeRepository(database)
        if graph is None and graph_factory is not None:
            graph = graph_factory(repo)
        actual_embedder = embedder or NoneEmbedder()
        uploads = (
            Path(uploads_dir).expanduser().resolve()
            if uploads_dir is not None
            else path.parent / "uploads"
        )
        reranker = build_reranker(rerank)
        rerank_settings = rerank_settings_for(rerank)
        tokenizer_mode = str(
            getattr(chunking, "tokenizer_mode", "jieba") or "jieba"
        )
        ingestor = Ingestor(
            repo,
            embedder=actual_embedder,
            vector_store=vector_store,
            tokenizer_mode=tokenizer_mode,
            identity_resolver=identity_resolver,
            question_generator=question_generator,
            question_count=question_count,
        )
        graph_route = (
            GraphRoute(
                repo=graph.repo,
                knowledge_repo=repo,
                store=graph.store,
                settings=RetrieverSettings(**_graph_settings(graph.config)),
                query_entities=(
                    graph.extract_query_entities
                    if bool(
                        getattr(
                            getattr(graph.config, "expansion", None),
                            "query_entity_extraction",
                            True,
                        )
                    )
                    else None
                ),
            )
            if graph is not None and graph.enabled
            else None
        )
        service = cls(
            database=database,
            repo=repo,
            embedder=actual_embedder,
            vector_store=vector_store,
            ingestor=ingestor,
            retriever=Retriever(
                repo,
                embedder=actual_embedder,
                vector_store=vector_store,
                settings=_retriever_settings(
                    retrieval, graph.config if graph is not None else None
                ),
                reranker=reranker,
                rerank_settings=rerank_settings,
                tokenizer_mode=tokenizer_mode,
                identity_resolver=identity_resolver,
                graph_route=graph_route,
            ),
            uploads_dir=uploads,
            chunk_size=int(getattr(chunking, "chunk_size", _DEFAULT_CHUNK_SIZE)),
            chunk_overlap=int(
                getattr(chunking, "chunk_overlap", _DEFAULT_CHUNK_OVERLAP)
            ),
            reranker=reranker,
            rerank_settings=rerank_settings,
            tokenizer_mode=tokenizer_mode,
            graph=graph,
        )
        service._graph_route = graph_route
        if graph is not None:
            graph.bind_notifier(service._wake_graph_worker)
            ingestor.set_graph_hook(service.enqueue_graph_document)
        return service

    # ---------- 运行期参数 ----------
    def apply_retrieval_settings(self, retrieval: Any, rerank: Any) -> None:
        """热应用检索/精排参数，只重建检索器。

        已入库的块、向量与 FTS 不受影响；分块与 embedding 参数不在本方法范围内，
        改动它们仍需要重新入库才能生效。图谱路由保持原实例。
        """
        reranker = build_reranker(rerank)
        rerank_settings = rerank_settings_for(rerank)
        graph_config = self._graph.config if self._graph is not None else None
        self._retriever = Retriever(
            self._repo,
            embedder=self._embedder,
            vector_store=self._vector,
            settings=_retriever_settings(retrieval, graph_config),
            reranker=reranker,
            rerank_settings=rerank_settings,
            tokenizer_mode=self._tokenizer_mode,
            identity_resolver=self._identity_resolver,
            graph_route=self._graph_route,
        )
        self._reranker = reranker
        self._rerank_settings = rerank_settings

    # ---------- KB ----------
    def create_kb(
        self,
        *,
        name: str,
        workspace_id: str = "default",
        description: str = "",
        visibility: str = "workspace",
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        embedding_backend: str | None = None,
        embedding_model: str | None = None,
        embedding_dimensions: int | None = None,
    ) -> KnowledgeBase:
        """建库前先把 `auto` 解析成具体后端，并把 (backend, model, dims) 落库。"""
        backend, model, dimensions = self._embedding_identity()
        if backend != "none":
            embedding_backend = embedding_backend or backend
            embedding_model = embedding_model or model
            if embedding_dimensions is None:
                embedding_dimensions = dimensions
        return self._repo.create_kb(
            name=name,
            workspace_id=workspace_id,
            description=description,
            visibility=visibility,
            chunk_size=chunk_size or self._chunk_size,
            chunk_overlap=(
                self._chunk_overlap if chunk_overlap is None else chunk_overlap
            ),
            embedding_backend=embedding_backend or "none",
            embedding_model=embedding_model or "",
            embedding_dimensions=embedding_dimensions or 0,
        )

    def get_kb(self, kb_id: str) -> KnowledgeBase | None:
        return self._repo.get_kb(kb_id)

    def list_kbs(
        self, *, workspace_ids: list[str], include_global: bool = True
    ) -> list[KnowledgeBase]:
        return self._repo.list_kbs(
            workspace_ids=workspace_ids, include_global=include_global
        )

    def update_kb(self, kb_id: str, **fields: Any) -> KnowledgeBase:
        return self._repo.update_kb(kb_id, **fields)

    def delete_kb(self, kb_id: str) -> None:
        for document in self._repo.list_documents(kb_id=kb_id, limit=_MAX_LIST_LIMIT):
            self.delete_document(document.id)
        self._delete_kb_vectors(kb_id)
        self._repo.delete_kb(kb_id)
        if self._graph is not None:
            self._graph.delete_kb_namespace(kb_id)

    # ---------- 文档 ----------
    def add_file(
        self,
        path: str | Path,
        *,
        kb_id: str,
        workspace_id: str,
        title: str | None = None,
        force: bool = False,
    ) -> AddOutcome:
        """登记外部文件：同路径=新版本、同哈希=去重、failed 行复用。"""
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise KnowledgeExtractionError(f"不是普通文件：{resolved}")
        if self._repo.get_kb(kb_id) is None:
            raise KeyError(kb_id)
        digest, size = hash_file(resolved)
        mtime = resolved.stat().st_mtime_ns

        existing = self._repo.find_by_path(kb_id, str(resolved))
        if existing is not None:
            return self._reuse_row(
                existing.id, digest=digest, size=size, mtime=mtime
            )

        if not force:
            duplicate = self._repo.find_by_hash(kb_id, digest)
            if duplicate is not None:
                return AddOutcome(duplicate, duplicate=True)

        failed = self._repo.find_by_hash(kb_id, digest, statuses=("failed",))
        if failed is not None:
            return self._reuse_row(
                failed.id,
                digest=digest,
                size=size,
                mtime=mtime,
                source_path=str(resolved),
                original_name=resolved.name,
            )

        try:
            document = self._repo.insert_document(
                kb_id=kb_id,
                workspace_id=workspace_id,
                title=title or resolved.stem,
                source_kind="external",
                source_path=str(resolved),
                original_name=resolved.name,
                file_hash=digest,
                file_size=size,
                source_mtime_ns=mtime,
            )
        except DuplicateDocumentPathError:
            # 并发 add 同一路径：唯一索引兜底后回到「同路径新版本」语义。
            again = self._repo.find_by_path(kb_id, str(resolved))
            if again is None:
                raise
            return self._reuse_row(again.id, digest=digest, size=size, mtime=mtime)
        self.enqueue_document(document.id)
        return AddOutcome(document)

    def add_upload_bytes(
        self,
        name: str,
        data: bytes,
        *,
        kb_id: str,
        workspace_id: str,
        mime: str = "",
        title: str | None = None,
    ) -> Document:
        """WebUI 上传：服务端生成托管路径，客户端文件名只取安全 basename。"""
        safe = safe_filename(name)
        suffix = Path(safe).suffix.lower()
        if suffix not in _UPLOAD_SUFFIXES:
            raise KnowledgeUnsupportedType(f"不支持的上传类型：{suffix or safe}")
        normalized_mime = mime.lower()
        if normalized_mime and normalized_mime not in _UPLOAD_MIMES:
            raise KnowledgeUnsupportedType(f"不支持的 MIME：{mime}")
        if self._repo.get_kb(kb_id) is None:
            raise KeyError(kb_id)

        doc_id = new_id("doc")
        dest = self._uploads / doc_id / safe
        if not is_within(dest, self._uploads):
            raise KnowledgeServiceError("上传路径越出知识目录")
        digest, size = write_bytes_atomic(dest, data)
        try:
            document = self._repo.insert_document(
                doc_id=doc_id,
                kb_id=kb_id,
                workspace_id=workspace_id,
                title=title or Path(safe).stem,
                source_kind="managed",
                source_path=str(dest),
                original_name=safe,
                mime=normalized_mime,
                file_hash=digest,
                file_size=size,
            )
        except Exception:
            self._discard_upload(dest)
            raise
        self.enqueue_document(document.id)
        return document

    def delete_document(self, doc_id: str) -> None:
        document = self._repo.get_document(doc_id)
        if document is None:
            return
        graph = self._graph
        entity_ids = (
            graph.entity_ids_for_document(doc_id) if graph is not None else []
        )
        self._repo.delete_document(doc_id)
        self._delete_document_vectors(doc_id)
        if graph is not None:
            graph.delete_document(doc_id, document.kb_id, entity_ids)
        if document.source_kind == "managed":
            self._remove_managed_copy(document.source_path)

    def reindex_document(self, doc_id: str) -> bool:
        if not self._repo.mark_reindex_pending(doc_id):
            return False
        self.enqueue_document(doc_id)
        return True

    def reindex_kb(self, kb_id: str) -> list[str]:
        queued: list[str] = []
        for document in self._repo.list_documents(kb_id=kb_id, limit=_MAX_LIST_LIMIT):
            if self.reindex_document(document.id):
                queued.append(document.id)
        return queued

    def list_documents(
        self,
        *,
        kb_id: str | None = None,
        workspace_ids: list[str] | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[Document]:
        return self._repo.list_documents(
            kb_id=kb_id,
            workspace_ids=workspace_ids,
            status=status,
            offset=offset,
            limit=limit,
        )

    def get_document(self, doc_id: str) -> Document | None:
        return self._repo.get_document(doc_id)

    def get_chunk(self, chunk_id: str):
        return self._repo.fetch_chunk(chunk_id)

    def list_chunks(
        self,
        doc_id: str,
        *,
        offset: int = 0,
        limit: int = 20,
        query: str = "",
    ):
        return self._repo.list_chunks(
            doc_id, offset=offset, limit=limit, query=query
        )

    def count_chunks(self, doc_id: str, *, query: str = "") -> int:
        return self._repo.count_chunks(doc_id, query=query)

    # ---------- 入库执行 ----------
    def process_document(self, doc_id: str, attempt: int | None = None) -> None:
        """同步入口（CLI/worker）：`attempt` 已认领时直接处理，否则自行认领。"""
        self._ingestor.process_document(doc_id, attempt)

    def ingest_now(self, doc_id: str) -> bool:
        """`--wait` 语义：认领后同步跑完，不经过队列；认领失败返回 False。"""
        attempt = self._repo.claim_document(doc_id, lease_owner=_CLI_LEASE_OWNER)
        if attempt is None:
            return False
        self._ingestor.process_document(doc_id, attempt)
        return True

    def enqueue_document(self, doc_id: str) -> None:
        """线程安全入队；没有运行中的 worker 时只依赖 DB 的 `pending` 状态。"""
        loop = self._loop
        if loop is None or loop.is_closed() or self._worker is None:
            return
        with self._queued_lock:
            if doc_id in self._queued:
                return
            self._queued.add(doc_id)
        try:
            loop.call_soon_threadsafe(self._queue.put_nowait, doc_id)
        except RuntimeError as exc:
            # 与 stop() 竞争：事件循环刚关闭。文档仍是 pending，下次 start() 会恢复。
            with self._queued_lock:
                self._queued.discard(doc_id)
            logger.debug("knowledge enqueue skipped: doc={} error={}", doc_id, exc)

    # ---------- 图抽取（独立 worker，不阻塞文档队列） ----------
    @property
    def graph(self) -> GraphService | None:
        return self._graph

    def attach_graph(self, graph: GraphService | None) -> None:
        """测试/延迟装配：注入图服务并同步 Ingestor 钩子。"""
        self._graph = graph
        if graph is not None:
            graph.bind_notifier(self._wake_graph_worker)
        self._ingestor.set_graph_hook(
            self.enqueue_graph_document if graph is not None else None
        )

    def enqueue_graph_document(self, doc_id: str) -> None:
        """Ingestor 钩子：写 pending 状态并唤醒 worker（可由 worker 线程调用）。"""
        graph = self._graph
        if graph is None or not graph.enabled:
            return
        graph.enqueue_document(doc_id, force=True)

    def _wake_graph_worker(self, doc_id: str) -> None:
        """只投递事件、不写库；作为 GraphService 的 notifier，避免递归入队。"""
        loop = self._loop
        if loop is None or loop.is_closed() or self._graph_worker is None:
            return
        with self._queued_lock:
            if doc_id in self._graph_queued:
                return
            self._graph_queued.add(doc_id)
        try:
            loop.call_soon_threadsafe(self._graph_queue.put_nowait, doc_id)
        except RuntimeError as exc:
            with self._queued_lock:
                self._graph_queued.discard(doc_id)
            logger.debug("knowledge graph enqueue skipped: doc={} error={}", doc_id, exc)

    async def _run_graph_worker(self) -> None:
        while True:
            doc_id = await self._graph_queue.get()
            try:
                with self._queued_lock:
                    self._graph_queued.discard(doc_id)
                await asyncio.to_thread(self._graph_process_queued, doc_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("knowledge graph worker failed: doc={}", doc_id)
            finally:
                self._graph_queue.task_done()

    def _graph_process_queued(self, doc_id: str) -> None:
        graph = self._graph
        if graph is None:
            return
        state = graph.claim(doc_id)
        if state is None:
            return
        graph.process_document(doc_id, attempt=state.attempt)

    async def start(self) -> None:
        """幂等启动：恢复过期租约与 pending 文档，然后拉起单 worker。"""
        if self._worker is not None and not self._worker.done():
            return
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        recovered = await asyncio.to_thread(self._recover_pending)
        self._worker = asyncio.create_task(self._run_worker(), name="knowledge-worker")
        for doc_id in recovered:
            self.enqueue_document(doc_id)
        graph = self._graph
        if graph is not None and graph.enabled:
            self._graph_queue = asyncio.Queue()
            graph.bind_notifier(self._wake_graph_worker)
            graph_docs = await asyncio.to_thread(graph.reset_expired_and_pending)
            self._graph_worker = asyncio.create_task(
                self._run_graph_worker(), name="knowledge-graph-worker"
            )
            for doc_id in graph_docs:
                self._wake_graph_worker(doc_id)

    async def stop(self) -> None:
        """幂等停止：取消并等待 worker，再清空内存队列（DB 状态保持不变）。"""
        worker = self._worker
        self._worker = None
        self._loop = None
        graph_worker = self._graph_worker
        self._graph_worker = None
        if graph_worker is not None:
            graph_worker.cancel()
            with suppress(asyncio.CancelledError):
                await graph_worker
        if worker is not None:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        self._drain_queue()
        with self._queued_lock:
            self._graph_queued.clear()

    # ---------- 检索 ----------
    def retriever_with(self, settings: RetrieverSettings) -> Retriever:
        """用同一份 repo/embedder/vector 构造指定参数的检索器（评测用）。

        评测要在「同一套融合 / 去重 / MMR 实现」上跑不同参数，因此这里只替换
        ``RetrieverSettings``，其余依赖与产品实例共享——不新建 embedding、
        不新建向量库，也不改变产品默认行为。
        """
        return Retriever(
            self._repo,
            embedder=self._embedder,
            vector_store=self._vector,
            settings=settings,
            reranker=self._reranker,
            rerank_settings=self._rerank_settings,
            tokenizer_mode=self._tokenizer_mode,
            identity_resolver=self._identity_resolver,
            graph_route=self._graph_route,
        )

    def search(
        self,
        query: str,
        *,
        workspace_ids: list[str],
        kb_ids: list[str] | None = None,
        mode: str = "hybrid",
        limit: int | None = None,
        extra_context: Sequence[SearchHit] = (),
    ) -> SearchResult:
        """检索；`extra_context` 是调用方注入的补充上下文（§9.1 ③ 历史引用）。"""
        return self._retriever.search(
            query,
            workspace_ids=workspace_ids,
            kb_ids=kb_ids,
            mode=mode,
            limit=limit,
            extra_context=extra_context,
        )

    # ---------- 统计 ----------
    def stats(self, *, workspace_ids: list[str]) -> dict[str, Any]:
        summary = self._repo.stats_summary(
            workspace_ids=workspace_ids, index_version=INDEX_VERSION
        )
        vector = self._vector_info(summary)
        return {
            "kbs": summary.kbs,
            "documents": summary.documents,
            "chunks": summary.chunks,
            "by_status": summary.by_status,
            "vector": vector,
            "pending": summary.pending,
            "expired_leases": summary.expired_leases,
            "drift": {
                "index_version": summary.index_versions,
                "stale_ready": summary.stale_ready,
            },
            "db_bytes": self._db.path.stat().st_size if self._db.path.exists() else 0,
            "uploads_bytes": _directory_size(self._uploads),
        }

    # ---------- 修复 ----------
    def repair(
        self, *, fts: bool = False, vectors: bool = False, purge: bool = False
    ) -> dict[str, Any]:
        """幂等非破坏修复：不删 SQLite 行；rewrite 重新入队，purge 只清派生数据。"""
        result: dict[str, Any] = {
            "fts_rebuilt": 0,
            "vectors_scanned": 0,
            "vectors_rewrite": 0,
            "vectors_purge": 0,
            "vector_errors": 0,
            "files_purged": 0,
            "rewritten_docs": [],
            "purged_docs": [],
        }
        if fts:
            self._rebuild_fts()
            result["fts_rebuilt"] = 1
        if vectors:
            self._repair_vectors(result)
        if purge:
            result["files_purged"] = self._purge_orphan_uploads()
        return result

    # ---------- worker 内部 ----------
    def _recover_pending(self) -> list[str]:
        recovered: list[str] = []
        seen: set[str] = set()
        expired = self._repo.reset_expired_leases()
        pending = self._repo.list_pending_documents()
        for doc_id in (*expired, *pending):
            if doc_id not in seen:
                seen.add(doc_id)
                recovered.append(doc_id)
        if expired:
            logger.info("knowledge recovered expired leases: {}", len(expired))
        return recovered

    async def _run_worker(self) -> None:
        queue = self._queue
        while True:
            doc_id = await queue.get()
            try:
                with self._queued_lock:
                    self._queued.discard(doc_id)
                await asyncio.to_thread(self._process_queued, doc_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # 文档状态已由 ingestor 落库；worker 只记录并继续下一条。
                logger.exception("knowledge worker failed: doc={}", doc_id)
            finally:
                queue.task_done()

    def _process_queued(self, doc_id: str) -> None:
        attempt = self._repo.claim_document(doc_id, lease_owner=_WORKER_LEASE_OWNER)
        if attempt is None:
            logger.debug("knowledge skipped (already claimed): doc={}", doc_id)
            return
        self._ingestor.process_document(doc_id, attempt)

    def _drain_queue(self) -> None:
        with self._queued_lock:
            self._queued.clear()
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self._queue.task_done()

    # ---------- 去重/清理内部 ----------
    def _reuse_row(
        self,
        doc_id: str,
        *,
        digest: str,
        size: int,
        mtime: int,
        source_path: str | None = None,
        original_name: str | None = None,
    ) -> AddOutcome:
        self._repo.update_source(
            doc_id,
            file_hash=digest,
            file_size=size,
            source_mtime_ns=mtime,
            source_path=source_path,
            original_name=original_name,
        )
        self.enqueue_document(doc_id)
        updated = self._repo.get_document(doc_id)
        if updated is None:
            raise KnowledgeServiceError(f"文档在更新后消失：{doc_id}")
        return AddOutcome(updated)

    def _embedding_identity(self) -> tuple[str, str, int]:
        if self._identity is None:
            self._identity = _safe_identity(self._embedder)
        return self._identity

    def _delete_document_vectors(self, doc_id: str) -> None:
        if self._vector is None or not self._vector.available:
            return
        try:
            self._vector.delete_document(doc_id)
        except VectorStoreError as exc:
            logger.warning("knowledge vector delete failed: doc={} error={}", doc_id, exc)

    def _delete_kb_vectors(self, kb_id: str) -> None:
        if self._vector is None or not self._vector.available:
            return
        try:
            self._vector.delete_kb(kb_id)
        except VectorStoreError as exc:
            logger.warning("knowledge vector delete failed: kb={} error={}", kb_id, exc)

    def _remove_managed_copy(self, source_path: str) -> None:
        path = Path(source_path)
        if not source_path or not is_within(path, self._uploads):
            logger.warning("knowledge managed path outside uploads, skip: {}", source_path)
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("knowledge managed unlink failed: path={} error={}", path, exc)
            return
        with suppress(OSError):
            path.parent.rmdir()

    def _discard_upload(self, dest: Path) -> None:
        dest.unlink(missing_ok=True)
        with suppress(OSError):
            dest.parent.rmdir()

    def _vector_info(self, summary: DocumentStats) -> dict[str, Any]:
        if self._vector is not None:
            backend, model, dimensions = self._vector.identity
            available = self._vector.available
        else:
            backend, model, dimensions = self._embedding_identity()
            available = False
        enabled = self._vector is not None and backend != "none"
        return {
            "status": "ready" if enabled and available else "disabled",
            "backend": backend,
            "model": model,
            "dimensions": dimensions,
            "ok_docs": summary.vector_counts.get("ok", 0),
            "failed_docs": summary.vector_counts.get("failed", 0),
            "pending_docs": summary.vector_counts.get("pending", 0)
            + summary.vector_counts.get("partial", 0),
        }

    def _rebuild_fts(self) -> None:
        with self._db.connect(immediate=True) as conn:
            conn.execute(
                "INSERT INTO document_chunks_fts(document_chunks_fts) VALUES('rebuild')"
            )
            conn.execute(
                "INSERT INTO document_chunks_fts(document_chunks_fts) VALUES('optimize')"
            )

    def _repair_vectors(self, result: dict[str, Any]) -> None:
        vector = self._vector
        if vector is None or not vector.available:
            logger.info("knowledge vector repair skipped: vector store unavailable")
            return
        report = vector.scan(self._repo.vector_expectations())
        result["vectors_scanned"] = len(report.actions)
        rewritten: list[str] = []
        purged: list[str] = []
        for action in report.actions:
            if action.action == "rewrite":
                if self._requeue_rewrite(action):
                    rewritten.append(action.doc_id)
                continue
            if self._purge_vectors(action, result):
                purged.append(action.doc_id)
        result["vectors_rewrite"] = len(rewritten)
        result["vectors_purge"] = len(purged)
        result["rewritten_docs"] = rewritten
        result["purged_docs"] = purged

    def _requeue_rewrite(self, action: RepairAction) -> bool:
        if self._repo.mark_reindex_pending(action.doc_id):
            self.enqueue_document(action.doc_id)
            return True
        document = self._repo.get_document(action.doc_id)
        if document is not None and document.parse_status == "pending":
            self.enqueue_document(action.doc_id)
            return True
        logger.warning(
            "knowledge vector rewrite skipped: doc={} status={}",
            action.doc_id,
            document.parse_status if document is not None else "missing",
        )
        return False

    def _purge_vectors(self, action: RepairAction, result: dict[str, Any]) -> bool:
        vector = self._vector
        assert vector is not None
        try:
            vector.delete_document(action.doc_id)
        except VectorStoreError as exc:
            result["vector_errors"] = int(result["vector_errors"]) + 1
            logger.warning(
                "knowledge vector purge failed: doc={} error={}", action.doc_id, exc
            )
            return False
        return True

    def _purge_orphan_uploads(self) -> int:
        if not self._uploads.is_dir():
            return 0
        known = {
            document.id
            for document in self._repo.list_documents(limit=_MAX_LIST_LIMIT)
        }
        purged = 0
        for child in sorted(self._uploads.iterdir()):
            if not child.is_dir() or child.name in known:
                continue
            if not is_within(child, self._uploads):
                continue
            shutil.rmtree(child, ignore_errors=True)
            purged += 1
        return purged


_INSTANCES: dict[str, KnowledgeService] = {}
_INSTANCES_LOCK = threading.Lock()


def _resolve_database_path(knowledge: KnowledgeConfig) -> Path:
    raw = knowledge.database_path.strip()
    if raw:
        return Path(raw).expanduser().resolve()
    from nanobot.config.paths import get_knowledge_dir

    return (get_knowledge_dir() / "state.db").resolve()


def get_knowledge_service(knowledge: KnowledgeConfig) -> KnowledgeService:
    """进程级单例（入参是 `Config.knowledge`，按解析后的 DB 路径缓存）。"""
    database_path = _resolve_database_path(knowledge)
    key = str(database_path)
    cached = _INSTANCES.get(key)
    if cached is not None:
        return cached
    with _INSTANCES_LOCK:
        cached = _INSTANCES.get(key)
        if cached is not None:
            return cached
        instance = _build_service(knowledge, database_path)
        _INSTANCES[key] = instance
        return instance


def _build_graph(graph_config: Any, repo: KnowledgeRepository) -> GraphService | None:
    if not getattr(graph_config, "enabled", False):
        return None
    extractor = build_graph_extractor(
        graph_config.extraction, getattr(graph_config, "prompts", None)
    )
    if not extractor:
        logger.warning(
            "knowledge graph enabled but extraction model is not configured"
        )
    password = str(getattr(graph_config, "password", "") or "")
    password_env = str(getattr(graph_config, "password_env", "") or "")
    if password_env:
        password = os.environ.get(password_env, "") or password
    store = Neo4jGraphStore(
        uri=str(getattr(graph_config, "uri", "") or ""),
        username=str(getattr(graph_config, "username", "neo4j") or "neo4j"),
        password=password,
        database=str(getattr(graph_config, "database", "neo4j") or "neo4j"),
        timeout=float(getattr(graph_config, "connect_timeout_seconds", 5.0)),
    )
    return GraphService(
        repo=GraphRepository(repo.database),
        knowledge_repo=repo,
        store=store,
        extractor=extractor,
        config=graph_config,
    )


def _build_service(knowledge: KnowledgeConfig, database_path: Path) -> KnowledgeService:
    embedding = knowledge.embedding
    embedder = build_embedder(
        backend=embedding.backend,
        model=embedding.model,
        dimensions=embedding.dimensions,
        device=embedding.device,
        download_source=embedding.download_source,
        local_model_dir=embedding.local_model_dir,
        base_url=embedding.base_url,
        api_key=embedding.api_key,
        api_key_env=embedding.api_key_env,
        batch_size=embedding.batch_size,
        timeout_seconds=embedding.timeout_seconds,
    )
    vector_path = (
        Path(knowledge.vector_path).expanduser().resolve()
        if knowledge.vector_path.strip()
        else database_path.parent / "chromadb"
    )
    _ensure_private_dir(vector_path)
    vector_store: VectorStore | None = None
    backend, model, dimensions = _safe_identity(embedder)
    if backend != "none":
        if dimensions <= 0:
            # remote 自适应维度在首次请求前未知：无法确定 collection 指纹。
            logger.warning(
                "knowledge vector disabled: unresolved embedding dimensions"
            )
        else:
            vector_store = VectorStore(
                vector_path, backend=backend, model=model, dimensions=dimensions
            )
    registry = _EmbedderRegistry(
        embedding,
        vector_path,
        default=(embedder, vector_store) if vector_store is not None else None,
    )
    # `backend=none` 是全局关闭开关：此时不按 KB 身份重建 embedder，保持关键词单路。
    identity_resolver = registry.resolve if backend != "none" else None
    questions = knowledge.question_generation
    return KnowledgeService.local(
        database_path,
        embedder=embedder,
        vector_store=vector_store,
        retrieval=knowledge.retrieval,
        chunking=knowledge.chunking,
        rerank=knowledge.rerank,
        identity_resolver=identity_resolver,
        question_generator=build_question_generator(questions),
        question_count=question_count_from(questions),
        graph_factory=lambda graph_repo: _build_graph(knowledge.graph, graph_repo),
    )


def get_knowledge_service_for_test(database_path: str | Path) -> KnowledgeService:
    """测试辅助：按显式路径缓存，不读取配置。"""
    key = str(Path(database_path).expanduser().resolve())
    with _INSTANCES_LOCK:
        cached = _INSTANCES.get(key)
        if cached is None:
            cached = KnowledgeService.local(Path(database_path))
            _INSTANCES[key] = cached
        return cached


def reset_knowledge_service() -> None:
    """清空单例缓存（测试用）；不关闭已停止的 worker。"""
    with _INSTANCES_LOCK:
        _INSTANCES.clear()
