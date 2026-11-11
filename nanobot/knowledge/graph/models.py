"""图谱域读模型：全部 frozen dataclass，与仓储/检索层同风格。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GraphEntity:
    id: str
    kb_id: str
    name: str
    name_norm: str
    type: str
    description: str
    mention_count: int
    #: 仅 Neo4j 镜像使用；SQLite 真源是 graph_mentions。
    chunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphRelation:
    id: str
    kb_id: str
    source_entity_id: str
    target_entity_id: str
    relation: str
    description: str
    weight: float
    mention_count: int


@dataclass(frozen=True, slots=True)
class GraphMention:
    entity_id: str
    doc_id: str
    chunk_id: str


@dataclass(frozen=True, slots=True)
class GraphEvidence:
    relation_id: str
    doc_id: str
    chunk_id: str


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source_entity_id: str
    target_entity_id: str
    relation: str
    weight: float
    hop: int


@dataclass(frozen=True, slots=True)
class GraphExpansion:
    entity_ids: tuple[str, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    chunk_ids: tuple[str, ...] = ()
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class GraphHealth:
    status: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class GraphStats:
    enabled: bool
    backend: str
    status: str
    reason: str
    entities: int
    relations: int
    docs_ready: int
    docs_pending: int
    docs_failed: int
    docs_skipped: int
    truncated_docs: int
    last_extracted_at: str | None


@dataclass(frozen=True, slots=True)
class GraphEvidencePayload:
    seeds: tuple[str, ...]
    path: tuple[tuple[str, str, str], ...]
    hop: int
    entity_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GraphExtractionState:
    doc_id: str
    status: str
    attempt: int
    chunk_total: int
    chunk_done: int
    entity_count: int
    relation_count: int
    truncated: bool
    error_code: str
    error_message: str
    extracted_at: str | None
    updated_at: str
