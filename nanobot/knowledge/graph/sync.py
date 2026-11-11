"""SQLite 真相 → Neo4j 镜像；所有写路径都经过这里。"""

from __future__ import annotations

from collections.abc import Sequence

from loguru import logger

from nanobot.knowledge.graph.repo import GraphRepository
from nanobot.knowledge.graph.store import GraphStore, GraphStoreError


class GraphSyncService:
    def __init__(self, repo: GraphRepository, store: GraphStore) -> None:
        self._repo = repo
        self._store = store

    def sync_entities(self, kb_id: str, entity_ids: Sequence[str]) -> None:
        try:
            if self._store.health().status != "ready":
                return
            entities = self._repo.list_entities_by_ids(kb_id, entity_ids)
            relations = self._repo.list_relations_for_entities(kb_id, entity_ids)
            self._store.upsert_subgraph(kb_id, entities, relations)
        except Exception as exc:  # noqa: BLE001 - 派生索引失败不影响主链路
            logger.warning(
                "knowledge graph sync failed: kb={} error={!r}", kb_id, exc
            )

    def rebuild_kb(self, kb_id: str) -> None:
        try:
            if self._store.health().status != "ready":
                return
            self._store.delete_kb(kb_id)
            all_ids = [
                entity.id for entity in self._repo.list_entities(kb_id, limit=100_000)
            ]
            entities = self._repo.list_entities_by_ids(kb_id, all_ids)
            relations = self._repo.list_relations(kb_id, limit=100_000)
            self._store.upsert_subgraph(kb_id, entities, relations)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "knowledge graph rebuild failed: kb={} error={!r}", kb_id, exc
            )

    def delete_document(
        self, doc_id: str, kb_id: str, entity_ids: Sequence[str]
    ) -> None:
        self._repo.cleanup_orphans(entity_ids)
        self._repo.cleanup_relations(kb_id)
        try:
            if not entity_ids or self._store.health().status != "ready":
                return
            alive = self._repo.list_entity_ids(kb_id, entity_ids)
            removed = [entity_id for entity_id in entity_ids if entity_id not in alive]
            if removed:
                self._store.delete_entities(kb_id, removed)
        except GraphStoreError as exc:
            logger.warning(
                "knowledge graph delete sync failed: kb={} error={!r}", kb_id, exc
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "knowledge graph delete sync failed: kb={} error={!r}", kb_id, exc
            )
