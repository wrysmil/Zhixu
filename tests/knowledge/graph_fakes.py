from __future__ import annotations

from collections.abc import Sequence

from nanobot.knowledge.graph.models import (
    GraphEntity,
    GraphExpansion,
    GraphHealth,
    GraphRelation,
)


class FakeGraphStore:
    def __init__(self) -> None:
        self.available = True
        self.entities: dict[str, list[GraphEntity]] = {}
        self.relations: dict[str, list[GraphRelation]] = {}
        self.deleted_kbs: list[str] = []
        self.deleted_entities: list[tuple[str, tuple[str, ...]]] = []
        self.expansions: list[GraphExpansion] = []
        self.schema_ready = False

    def health(self) -> GraphHealth:
        return (
            GraphHealth("ready")
            if self.available
            else GraphHealth("degraded", "neo4j_unavailable")
        )

    def ensure_schema(self) -> None:
        if not self.available:
            raise RuntimeError("neo4j_unavailable")
        self.schema_ready = True

    def upsert_subgraph(
        self,
        kb_id: str,
        entities: Sequence[GraphEntity],
        relations: Sequence[GraphRelation],
    ) -> None:
        if not self.available:
            raise RuntimeError("neo4j_unavailable")
        self.entities[kb_id] = list(entities)
        self.relations[kb_id] = list(relations)

    def delete_kb(self, kb_id: str) -> None:
        self.deleted_kbs.append(kb_id)
        self.entities.pop(kb_id, None)
        self.relations.pop(kb_id, None)

    def delete_entities(self, kb_id: str, entity_ids: Sequence[str]) -> None:
        wanted = set(entity_ids)
        self.deleted_entities.append((kb_id, tuple(entity_ids)))
        self.entities[kb_id] = [
            entity for entity in self.entities.get(kb_id, []) if entity.id not in wanted
        ]

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
        if not self.available:
            raise RuntimeError("neo4j_unavailable")
        if self.expansions:
            return self.expansions.pop(0)
        return GraphExpansion()
