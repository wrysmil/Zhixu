"""图谱服务：异步抽取 worker 的执行体、回填/重建、统计与读接口。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from loguru import logger

from nanobot.knowledge.graph.extractor import (
    PROMPT_VERSION,
    GraphExtractionError,
    GraphExtractor,
)
from nanobot.knowledge.graph.linker import find_seed_entities
from nanobot.knowledge.graph.models import (
    GraphEntity,
    GraphExtractionState,
    GraphHealth,
    GraphRelation,
)
from nanobot.knowledge.graph.repo import GraphRepository
from nanobot.knowledge.graph.store import GraphStore, GraphStoreError
from nanobot.knowledge.graph.sync import GraphSyncService

_MAX_CHUNKS_LIST = 100_000
_VISUALIZE_POOL = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="knowledge-graph-view"
)


@dataclass(frozen=True, slots=True)
class GraphQueryResult:
    """一次 Agent 图谱查询的结果；计数用于把「被截断」显式告诉模型。"""

    seeds: tuple[GraphEntity, ...] = ()
    entities: tuple[GraphEntity, ...] = ()
    relations: tuple[GraphRelation, ...] = ()
    chunks: tuple[Any, ...] = ()
    relations_total: int = 0
    chunks_total: int = 0
    truncated: bool = False


class GraphService:
    def __init__(
        self,
        *,
        repo: GraphRepository,
        knowledge_repo: Any,
        store: GraphStore,
        extractor: GraphExtractor | None,
        config: Any,
        sync: GraphSyncService | None = None,
    ) -> None:
        self.repo = repo
        self.store = store
        self.config = config
        self._knowledge = knowledge_repo
        self._extractor = extractor
        self._sync = sync or GraphSyncService(repo, store)
        self._notify: Callable[[str], None] | None = None
        self._schema_attempted = False

    # ---------- 状态 ----------
    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", False))

    @property
    def extractor_ready(self) -> bool:
        return self._extractor is not None

    @property
    def require_confirmation(self) -> bool:
        extraction = getattr(self.config, "extraction", None)
        return bool(getattr(extraction, "require_confirmation", True))

    def bind_notifier(self, notifier: Callable[[str], None]) -> None:
        """KnowledgeService 注入「唤醒 graph worker」回调；本类不持有事件循环。"""
        self._notify = notifier

    def _notify_doc(self, doc_id: str) -> None:
        if self._notify is not None:
            self._notify(doc_id)

    def health(self) -> GraphHealth:
        if not self.enabled:
            return GraphHealth("degraded", "disabled")
        health = self.store.health()
        if health.status != "ready":
            return health
        if not self._schema_attempted:
            try:
                self.store.ensure_schema()
                self._schema_attempted = True
            except GraphStoreError as exc:
                logger.warning("knowledge graph schema failed: {}", exc)
                return GraphHealth("degraded", "schema_failed")
            except Exception as exc:  # noqa: BLE001
                logger.warning("knowledge graph schema failed: {!r}", exc)
                return GraphHealth("degraded", "schema_failed")
        return GraphHealth("ready")

    # ---------- 入队 / 状态恢复 ----------
    def enqueue_document(self, doc_id: str, *, force: bool = True) -> None:
        if not self.enabled or self._extractor is None:
            return
        self.repo.enqueue_document(
            doc_id,
            model=self._extractor.model_id,
            prompt_version=PROMPT_VERSION,
            force=force,
        )
        self._notify_doc(doc_id)

    def reset_expired_and_pending(self) -> list[str]:
        if not self.enabled:
            return []
        self.repo.reset_expired_leases()
        return self.repo.list_pending_documents()

    def claim(self, doc_id: str) -> GraphExtractionState | None:
        return self.repo.claim_document(doc_id)

    def entity_ids_for_document(self, doc_id: str) -> list[str]:
        return self.repo.entity_ids_for_document(doc_id)

    def extract_query_entities(self, query: str) -> tuple[str, ...]:
        """查询侧抽实体：复用图谱抽取模型，只取实体名；失败/无实体返回空。

        调用方（GraphRoute）会用本地链接兜底，因此这里不抛错。
        """
        extraction = getattr(self.config, "extraction", None)
        if (
            not self.enabled
            or self._extractor is None
            or not query.strip()
            or not bool(getattr(extraction, "enabled", True))
        ):
            return ()
        try:
            extract_query = getattr(self._extractor, "extract_query", None)
            if callable(extract_query):
                raw_names = tuple(extract_query(query))
            else:
                result = self._extractor.extract("query", query)
                raw_names = tuple(entity[0] for entity in result.entities)
        except Exception as exc:  # noqa: BLE001 - 图路增强失败不阻断检索
            logger.warning(
                "knowledge graph query entity extraction failed: {!r}", exc
            )
            return ()
        names: list[str] = []
        for name in raw_names:
            text = str(name).strip()
            if not text or text in names:
                continue
            names.append(text)
            if len(names) >= 8:
                break
        return tuple(names)

    def query_related(
        self,
        kb_id: str,
        query: str,
        *,
        hops: int = 1,
        seed_limit: int = 5,
        max_entities: int = 20,
        max_chunks: int = 30,
        display_chunks: int | None = None,
        max_relations: int = 30,
        timeout_seconds: float = 0.8,
    ) -> GraphQueryResult:
        """实体名 → 无向子图 → 仍存活的证据块。

        `query` 直接放实体名或关系问题；种子只取有边的实体（WeKnora 口径）。
        `max_chunks` 是校验/扩展预算，`display_chunks` 是展示上限
        （WeKnora 工具同样是「多校验、少展示」）。图是派生索引，实体/关系
        只有在仍有存活 chunk 证据时才返回。
        """
        seeds = find_seed_entities(
            self.repo, kb_id, query, limit=max(1, seed_limit)
        )
        if not seeds:
            return GraphQueryResult()
        future = _VISUALIZE_POOL.submit(
            self.store.expand,
            kb_id,
            [seed.id for seed in seeds],
            hops=max(1, min(2, hops)),
            max_entities=max(1, max_entities),
            max_chunks=max(1, max_chunks),
            include_inner_edges=True,
        )
        expansion = future.result(timeout=max(0.1, timeout_seconds))
        entity_ids = tuple(
            dict.fromkeys([seed.id for seed in seeds] + list(expansion.entity_ids))
        )
        selected = self.repo.list_entities_by_ids(kb_id, entity_ids)
        chunk_ids = self.repo.mention_chunks(entity_ids)
        chunks = list(
            self._knowledge.fetch_search_chunks(chunk_ids, kb_ids=[kb_id])
        )
        chunks_total = len(chunks)
        backed = self.repo.entities_for_chunks([chunk.id for chunk in chunks])
        order = {entity_id: index for index, entity_id in enumerate(entity_ids)}
        entities = tuple(
            sorted(
                (entity for entity in selected if entity.id in backed),
                key=lambda entity: (order.get(entity.id, 10**9), entity.id),
            )
        )
        if not entities:
            return GraphQueryResult(
                seeds=tuple(seeds),
                chunks_total=chunks_total,
                truncated=expansion.truncated,
            )
        backed_ids = {entity.id for entity in entities}
        all_relations = [
            relation
            for relation in self.repo.list_relations_for_entities(kb_id, entity_ids)
            if relation.source_entity_id in backed_ids
            and relation.target_entity_id in backed_ids
        ]
        all_relations.sort(key=lambda relation: (-relation.weight, relation.id))
        relations = tuple(all_relations[: max(1, max_relations)])
        shown_limit = max(1, display_chunks if display_chunks is not None else max_chunks)
        shown_chunks = tuple(chunks[:shown_limit])
        return GraphQueryResult(
            seeds=tuple(seeds),
            entities=entities,
            relations=relations,
            chunks=shown_chunks,
            relations_total=len(all_relations),
            chunks_total=chunks_total,
            truncated=(
                expansion.truncated
                or len(all_relations) > len(relations)
                or chunks_total > len(shown_chunks)
            ),
        )

    # ---------- 执行 ----------
    def process_document(self, doc_id: str, attempt: int | None = None) -> None:
        if self._extractor is None:
            if attempt is not None:
                self.repo.fail_document(
                    doc_id, attempt, "not_configured", "graph extractor is not configured"
                )
            return
        if attempt is None:
            state = self.repo.claim_document(doc_id)
            if state is None:
                return
            attempt = state.attempt
        document = self._knowledge.get_document(doc_id)
        if document is None:
            return
        chunks = self._knowledge.list_chunks(
            doc_id, offset=0, limit=_MAX_CHUNKS_LIST
        )
        extraction = getattr(self.config, "extraction", None)
        max_chunks = max(1, int(getattr(extraction, "max_chunks_per_doc", 200)))
        truncated = len(chunks) > max_chunks
        chunks = chunks[:max_chunks]
        if not chunks:
            self.repo.finish_document(doc_id, attempt, status="skipped")
            return

        concurrency = max(1, int(getattr(extraction, "concurrency", 2)))
        entities: dict[str, tuple[str, str, str]] = {}
        relations: dict[tuple[str, str, str], tuple[str, str, str, str, float]] = {}
        mentions: list[tuple[str, str]] = []
        failures = 0
        done_chunks = 0
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {
                pool.submit(self._extract_chunk, chunk): chunk for chunk in chunks
            }
            for future in as_completed(futures):
                chunk = futures[future]
                done_chunks += 1
                try:
                    result = future.result()
                except GraphExtractionError as exc:
                    failures += 1
                    logger.warning(
                        "knowledge graph extract failed: doc={} chunk={} code={}",
                        doc_id,
                        chunk.id,
                        exc.code,
                    )
                    self.repo.update_progress(
                        doc_id, attempt, chunk_done=done_chunks
                    )
                    continue
                except Exception as exc:  # noqa: BLE001 - 单块失败只跳过
                    failures += 1
                    logger.warning(
                        "knowledge graph extract error: doc={} chunk={} error={!r}",
                        doc_id,
                        chunk.id,
                        exc,
                    )
                    self.repo.update_progress(
                        doc_id, attempt, chunk_done=done_chunks
                    )
                    continue
                for entity in result.entities:
                    entities.setdefault(entity[0], entity)
                for relation in result.relations:
                    relations.setdefault(relation[:3], relation)
                for name, _type, _description in result.entities:
                    mentions.append((name, chunk.id))
                self.repo.update_progress(doc_id, attempt, chunk_done=done_chunks)

        if not entities:
            if failures == len(chunks):
                self.repo.fail_document(
                    doc_id, attempt, "extract_failed", "all chunks failed"
                )
            else:
                self.repo.finish_document(
                    doc_id,
                    attempt,
                    status="skipped",
                    chunk_total=len(chunks),
                    chunk_done=done_chunks,
                )
            return

        touched_entities, _touched_relations = self.repo.replace_document_graph(
            doc_id,
            document.kb_id,
            entities=list(entities.values()),
            relations=list(relations.values()),
            mentions=mentions,
        )
        self.repo.finish_document(
            doc_id,
            attempt,
            status="ready",
            chunk_total=len(chunks),
            chunk_done=done_chunks,
            entity_count=len(entities),
            relation_count=len(relations),
            truncated=truncated,
        )
        self._sync.sync_entities(document.kb_id, list(touched_entities))

    def _extract_chunk(self, chunk: Any):
        assert self._extractor is not None
        return self._extractor.extract(chunk.heading_path, chunk.content)

    # ---------- 手动动作 ----------
    def extract_documents(self, doc_id: str, *, force: bool = False) -> dict[str, Any]:
        state = self.repo.get_state(doc_id)
        if state is not None and state.status == "ready" and not force:
            return {"queued": 0, "docs": []}
        self.enqueue_document(doc_id, force=force)
        return {"queued": 1, "docs": [doc_id]}

    def backfill_kb(self, kb_id: str) -> dict[str, Any]:
        docs = self.repo.list_documents_for_extraction(
            kb_id, statuses=("pending", "failed")
        )
        for doc_id in docs:
            self.enqueue_document(doc_id, force=False)
        return {"queued": len(docs), "docs": docs}

    def rebuild_kb(self, kb_id: str, *, purge: bool = False) -> dict[str, Any]:
        self.repo.reset_expired_leases()
        if purge:
            try:
                if self.store.health().status == "ready":
                    self.store.delete_kb(kb_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "knowledge graph purge failed: kb={} error={!r}", kb_id, exc
                )
            self.repo.purge_kb(kb_id)
        docs = self.repo.list_documents_for_extraction(kb_id)
        for doc_id in docs:
            self.enqueue_document(doc_id, force=True)
        return {"queued": len(docs), "docs": docs}

    def pending_documents(self, kb_id: str) -> list[str]:
        return self.repo.list_documents_for_extraction(
            kb_id, statuses=("pending",)
        )

    def delete_document(
        self, doc_id: str, kb_id: str, entity_ids: Sequence[str]
    ) -> None:
        self._sync.delete_document(doc_id, kb_id, entity_ids)

    def delete_kb_namespace(self, kb_id: str) -> None:
        """知识库删除后清理 Neo4j 命名空间（SQLite 行由外键级联）。"""
        try:
            if self.store.health().status == "ready":
                self.store.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "knowledge graph delete_kb failed: kb={} error={!r}", kb_id, exc
            )

    # ---------- 读接口（camelCase，直接给 WebUI） ----------
    def stats(self, kb_id: str) -> dict[str, Any]:
        raw = self.repo.stats(kb_id)
        health = self.health()
        return {
            "enabled": self.enabled,
            "backend": str(getattr(self.config, "backend", "neo4j")),
            "status": "disabled" if not self.enabled else health.status,
            "reason": health.reason,
            "entities": raw.entities,
            "relations": raw.relations,
            "docsReady": raw.docs_ready,
            "docsPending": raw.docs_pending,
            "docsFailed": raw.docs_failed,
            "docsSkipped": raw.docs_skipped,
            "truncatedDocs": raw.truncated_docs,
            "lastExtractedAt": raw.last_extracted_at,
        }

    def list_entities(
        self,
        kb_id: str,
        *,
        query: str = "",
        type_: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        entities = self.repo.list_entities(
            kb_id, q=query, type_=type_, limit=limit, offset=offset
        )
        return {
            "entities": [
                {
                    "id": entity.id,
                    "name": entity.name,
                    "type": entity.type,
                    "description": entity.description,
                    "mentionCount": entity.mention_count,
                }
                for entity in entities
            ],
            "total": self.repo.count_entities(kb_id, q=query, type_=type_),
        }

    def get_entity(
        self, entity_id: str, workspace_ids: Sequence[str]
    ) -> dict[str, Any] | None:
        entity = self.repo.get_entity_in_scope(entity_id, workspace_ids)
        if entity is None:
            return None
        neighbors = self.repo.list_neighbors(entity_id)
        mentions = self.repo.list_mentions(entity_id)
        return {
            "entity": {
                "id": entity.id,
                "name": entity.name,
                "type": entity.type,
                "description": entity.description,
                "mentionCount": entity.mention_count,
            },
            "neighbors": [
                {
                    "entity": {
                        "id": item["id"],
                        "name": item["name"],
                        "type": item["type"],
                        "description": item["description"],
                        "mentionCount": int(item["mention_count"]),
                    },
                    "relation": item["relation"],
                    "direction": item["direction"],
                    "weight": float(item["weight"]),
                }
                for item in neighbors
            ],
            "mentions": [
                {
                    "docId": item["doc_id"],
                    "docTitle": item["doc_title"],
                    "chunkId": item["chunk_id"],
                    "chunkIndex": int(item["chunk_index"] or 0),
                    "heading": item["heading_path"] or "",
                }
                for item in mentions
            ],
        }

    def subgraph(
        self,
        entity_id: str,
        workspace_ids: Sequence[str],
        *,
        hops: int = 1,
        limit: int = 50,
    ) -> dict[str, Any]:
        center = self.repo.get_entity_in_scope(entity_id, workspace_ids)
        if center is None:
            return {"nodes": [], "edges": []}
        expansion = getattr(self.config, "expansion", None)
        timeout = float(getattr(expansion, "timeout_seconds", 0.8))
        try:
            # 可视化允许比检索稍慢；这里仍给硬上限，避免页面上挂死。
            future = _VISUALIZE_POOL.submit(
                self.store.expand,
                center.kb_id,
                [center.id],
                hops=max(1, min(2, hops)),
                max_entities=limit,
                max_chunks=limit * 4,
                include_inner_edges=True,
            )
            result = future.result(timeout=max(timeout, 3.0))
        except Exception as exc:  # noqa: BLE001 - 可视化失败只给空图
            logger.warning(
                "knowledge graph subgraph failed: kb={} error={!r}",
                center.kb_id,
                exc,
            )
            return {"nodes": [], "edges": []}
        entity_ids = (center.id, *result.entity_ids)
        entities = self.repo.list_entities_by_ids(center.kb_id, entity_ids)
        return {
            "nodes": [
                {
                    "id": entity.id,
                    "name": entity.name,
                    "type": entity.type,
                    "mentionCount": entity.mention_count,
                }
                for entity in entities
            ],
            "edges": [
                {
                    "source": edge.source_entity_id,
                    "target": edge.target_entity_id,
                    "relation": edge.relation,
                    "weight": edge.weight,
                }
                for edge in result.edges
            ],
        }

