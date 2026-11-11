"""图后端的唯一接口：只有 GraphSyncService / GraphRoute 可以调用。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from nanobot.knowledge.graph.models import (
    GraphEntity,
    GraphExpansion,
    GraphHealth,
    GraphRelation,
)


class GraphStoreError(RuntimeError):
    """派生索引后端错误；检索侧按降级处理，不向上抛给调用方。"""


class GraphStore(Protocol):
    def health(self) -> GraphHealth: ...

    def ensure_schema(self) -> None: ...

    def upsert_subgraph(
        self,
        kb_id: str,
        entities: Sequence[GraphEntity],
        relations: Sequence[GraphRelation],
    ) -> None: ...

    def delete_kb(self, kb_id: str) -> None: ...

    def delete_entities(self, kb_id: str, entity_ids: Sequence[str]) -> None: ...

    def expand(
        self,
        kb_id: str,
        entity_ids: Sequence[str],
        *,
        hops: int,
        max_entities: int,
        max_chunks: int,
        include_inner_edges: bool = False,
    ) -> GraphExpansion: ...
