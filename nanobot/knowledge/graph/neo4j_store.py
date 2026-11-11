"""Neo4j 派生索引适配器：延迟 import 驱动，全部 Cypher 参数化。

节点 uid = ``<kb_id>:<entity_id>``，关系 uid = ``<kb_id>:<relation_id>``。
不引入 APOC；只使用原生 MERGE。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from loguru import logger

from nanobot.knowledge.graph.models import (
    GraphEdge,
    GraphEntity,
    GraphExpansion,
    GraphHealth,
    GraphRelation,
)
from nanobot.knowledge.graph.store import GraphStoreError


class Neo4jGraphStore:
    def __init__(
        self,
        *,
        uri: str,
        username: str,
        password: str,
        database: str,
        timeout: float = 5.0,
    ) -> None:
        self._uri = uri
        self._auth = (username, password)
        self._database = database
        self._timeout = timeout
        self._driver: Any = None
        self._health: GraphHealth | None = None
        self._health_at = 0.0

    def _connect(self) -> Any:
        if self._driver is None:
            try:
                from neo4j import GraphDatabase
            except ImportError as exc:  # pragma: no cover - 依赖缺失路径
                raise GraphStoreError(
                    "neo4j 驱动未安装：请安装可选依赖 'zhixu[graph]'"
                ) from exc
            self._driver = GraphDatabase.driver(
                self._uri,
                auth=self._auth,
                connection_timeout=self._timeout,
                max_connection_lifetime=300,
            )
        return self._driver

    def _query(
        self, cypher: str, parameters: dict[str, Any] | None = None
    ) -> Any:
        result = self._connect().execute_query(
            cypher,
            parameters or {},
            database_=self._database,
            timeout=self._timeout,
        )
        # EagerResult 自身迭代的是 key 列表；真正需要的是 .records。
        return result.records

    def health(self) -> GraphHealth:
        """30 秒缓存：检索路径每查询都会读健康态，不能每次都打网络。"""
        now = time.monotonic()
        if self._health is not None and now - self._health_at < 30.0:
            return self._health
        try:
            self._connect().verify_connectivity()
            self._health = GraphHealth("ready")
        except Exception:  # noqa: BLE001 - 健康检查永不抛
            self._health = GraphHealth("degraded", "neo4j_unavailable")
        self._health_at = now
        return self._health

    def ensure_schema(self) -> None:
        try:
            self._query(
                "CREATE CONSTRAINT graph_entity_uid IF NOT EXISTS "
                "FOR (e:Entity) REQUIRE e.uid IS UNIQUE"
            )
            self._query(
                "CREATE INDEX graph_entity_kb IF NOT EXISTS "
                "FOR (e:Entity) ON (e.kb_id)"
            )
            self._query(
                "MERGE (meta:GraphMeta {id:'schema'}) SET meta.version = 1"
            )
        except GraphStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise GraphStoreError("schema_failed") from exc

    def upsert_subgraph(
        self,
        kb_id: str,
        entities: Sequence[GraphEntity],
        relations: Sequence[GraphRelation],
    ) -> None:
        rows = [
            {
                "uid": f"{kb_id}:{entity.id}",
                "kb_id": kb_id,
                "entity_id": entity.id,
                "name": entity.name,
                "name_norm": entity.name_norm,
                "type": entity.type,
                "description": entity.description,
                "mention_count": entity.mention_count,
                "chunk_ids": list(entity.chunk_ids)[:200],
            }
            for entity in entities
        ]
        if rows:
            self._query(
                "UNWIND $rows AS row "
                "MERGE (e:Entity {uid: row.uid}) "
                "SET e.kb_id=row.kb_id, e.entity_id=row.entity_id, "
                "e.name=row.name, e.name_norm=row.name_norm, e.type=row.type, "
                "e.description=row.description, e.mention_count=row.mention_count, "
                "e.chunk_ids=row.chunk_ids",
                {"rows": rows},
            )
        relation_rows = [
            {
                "uid": f"{kb_id}:{relation.id}",
                "source_uid": f"{kb_id}:{relation.source_entity_id}",
                "target_uid": f"{kb_id}:{relation.target_entity_id}",
                "relation": relation.relation,
                "weight": relation.weight,
                "description": relation.description,
                "mention_count": relation.mention_count,
            }
            for relation in relations
        ]
        if relation_rows:
            self._query(
                "UNWIND $rows AS row "
                "MERGE (s:Entity {uid: row.source_uid}) "
                "MERGE (t:Entity {uid: row.target_uid}) "
                "MERGE (s)-[r:RELATED {uid: row.uid}]->(t) "
                "SET r.relation=row.relation, r.weight=row.weight, "
                "r.description=row.description, r.mention_count=row.mention_count",
                {"rows": relation_rows},
            )

    def delete_kb(self, kb_id: str) -> None:
        self._query(
            "MATCH (e:Entity {kb_id: $kb_id}) DETACH DELETE e", {"kb_id": kb_id}
        )

    def delete_entities(self, kb_id: str, entity_ids: Sequence[str]) -> None:
        uids = [f"{kb_id}:{entity_id}" for entity_id in entity_ids]
        if not uids:
            return
        self._query(
            "UNWIND $uids AS uid MATCH (e:Entity {uid: uid}) DETACH DELETE e",
            {"uids": uids},
        )

    def expand(
        self,
        kb_id: str,
        entity_ids: Sequence[str],
        *,
        hops: int,
        max_entities: int,
        max_chunks: int,
        include_inner_edges: bool = False,
    ) -> GraphExpansion:
        if hops not in (1, 2):
            raise GraphStoreError("invalid_hops")
        uids = [f"{kb_id}:{entity_id}" for entity_id in entity_ids]
        if not uids:
            return GraphExpansion()
        # hops 已校验为 1/2 的整数，可安全进 Cypher；其余全部参数绑定。
        # 无向扩展：种子既可能是边的起点，也可能是终点。WeKnora 的种子查询
        # 同样用 `(n)-[r]-(m)`，否则「A 被 B 依赖」这类入边永远不会被召回。
        cypher = (
            f"MATCH p=(s:Entity)-[:RELATED*1..{hops}]-(e:Entity) "
            "WHERE s.uid IN $uids AND s.kb_id = $kb_id AND e.kb_id = $kb_id "
            "RETURN p LIMIT $row_limit"
        )
        try:
            records = self._query(
                cypher,
                {
                    "uids": uids,
                    "kb_id": kb_id,
                    "row_limit": max(1, max_entities * 8),
                },
            )
        except GraphStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("knowledge graph expand failed: kb={} error={!r}", kb_id, exc)
            raise GraphStoreError("neo4j_unavailable") from exc

        entities: list[str] = []
        edges_by_key: dict[tuple[str, str, str], GraphEdge] = {}
        chunk_ids: list[str] = []
        truncated = False
        for record in records:
            path = record["p"]
            path_hop = len(path.relationships)
            for node in path.nodes:
                entity_id = node.get("entity_id")
                if entity_id and entity_id not in entities:
                    if len(entities) >= max_entities:
                        truncated = True
                    else:
                        entities.append(entity_id)
                for chunk_id in node.get("chunk_ids") or ():
                    if chunk_id not in chunk_ids:
                        if len(chunk_ids) >= max_chunks:
                            truncated = True
                        else:
                            chunk_ids.append(chunk_id)
            for relationship in path.relationships:
                source = relationship.start_node.get("entity_id")
                target = relationship.end_node.get("entity_id")
                if not source or not target:
                    continue
                relation = str(relationship.get("relation") or "")
                key = (source, target, relation)
                existing = edges_by_key.get(key)
                if existing is not None and existing.hop <= path_hop:
                    continue
                edges_by_key[key] = GraphEdge(
                    source_entity_id=source,
                    target_entity_id=target,
                    relation=relation,
                    weight=float(relationship.get("weight") or 5.0),
                    hop=path_hop,
                )
        edges = list(edges_by_key.values())
        if include_inner_edges and entities:
            seen = set(edges_by_key)
            limit = max(1, max_entities * 8)
            try:
                rows = self._query(
                    "MATCH (a:Entity)-[r:RELATED]->(b:Entity) "
                    "WHERE a.kb_id=$kb_id AND b.kb_id=$kb_id "
                    "AND a.entity_id IN $ids AND b.entity_id IN $ids "
                    "RETURN a.entity_id AS source, b.entity_id AS target, "
                    "r.relation AS relation, r.weight AS weight "
                    "LIMIT $limit",
                    {"kb_id": kb_id, "ids": entities, "limit": limit},
                )
            except Exception as exc:  # noqa: BLE001 - 补边失败不影响主结果
                logger.warning(
                    "knowledge graph inner edges failed: kb={} error={!r}",
                    kb_id,
                    exc,
                )
                rows = []
            for row in rows:
                key = (row["source"], row["target"], row["relation"])
                if key in seen:
                    continue
                seen.add(key)
                edges.append(
                    GraphEdge(
                        source_entity_id=row["source"],
                        target_entity_id=row["target"],
                        relation=str(row["relation"] or ""),
                        weight=float(row["weight"] or 5.0),
                        hop=1,
                    )
                )
        return GraphExpansion(
            entity_ids=tuple(entities),
            edges=tuple(edges),
            chunk_ids=tuple(chunk_ids),
            truncated=truncated,
        )
