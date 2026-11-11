"""检索第三路：实体链接 → 1~2 跳扩展 → 回表复核 → 融合候选与证据。"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loguru import logger

from nanobot.knowledge.fusion import SearchHit
from nanobot.knowledge.graph.linker import find_seed_entities
from nanobot.knowledge.graph.models import GraphEvidencePayload, GraphExpansion
from nanobot.knowledge.graph.repo import GraphRepository
from nanobot.knowledge.graph.store import GraphStore
from nanobot.knowledge.repository import KnowledgeRepository, StoredChunk

#: 共享执行器：检索路径的硬超时必须真正生效；用 with ThreadPoolExecutor 会在退出时
#: wait 到后端查询结束，反而绕过 800ms 上限。
_EXPAND_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="knowledge-graph")


def _hit_from_chunk(chunk: StoredChunk) -> SearchHit:
    return SearchHit(
        chunk_id=chunk.id,
        doc_id=chunk.doc_id,
        kb_id=chunk.kb_id,
        chunk_index=chunk.chunk_index,
        heading_path=chunk.heading_path,
        page=chunk.page,
        locator=chunk.locator,
        content=chunk.content,
        char_count=chunk.char_count,
        content_hash=chunk.content_hash,
    )


def _evidence_for(
    chunk_id: str,
    seeds: Sequence[Any],
    expansion: GraphExpansion,
    repo: GraphRepository,
    names: dict[str, str],
) -> GraphEvidencePayload:
    mention_entities = repo.entities_for_chunk(chunk_id)
    seed_names = tuple(seed.name for seed in seeds[:1])
    adjacency: dict[str, list[tuple[str, str]]] = {}
    for edge in expansion.edges:
        adjacency.setdefault(edge.source_entity_id, []).append(
            (edge.target_entity_id, edge.relation)
        )
    queue: list[tuple[str, tuple[tuple[str, str, str], ...]]] = [
        (seed.id, ()) for seed in seeds
    ]
    seen = {seed.id for seed in seeds}
    fallback: str | None = None
    while queue:
        current, path = queue.pop(0)
        if current in mention_entities:
            if path:
                return GraphEvidencePayload(
                    seeds=seed_names,
                    path=path,
                    hop=len(path),
                    entity_ids=(current,),
                )
            # 同块种子只作兜底：继续找「种子 → 另一实体 → 该块」的非空路径。
            if fallback is None:
                fallback = current
        for target, relation in adjacency.get(current, ()):
            if target in seen:
                continue
            seen.add(target)
            queue.append(
                (
                    target,
                    path
                    + (
                        (
                            names.get(current, current),
                            relation,
                            names.get(target, target),
                        ),
                    ),
                )
            )
    return GraphEvidencePayload(
        seeds=seed_names,
        path=(),
        hop=1,
        entity_ids=(fallback,) if fallback else expansion.entity_ids,
    )


class GraphRoute:
    def __init__(
        self,
        *,
        repo: GraphRepository,
        knowledge_repo: KnowledgeRepository,
        store: GraphStore,
        settings: Any,
    ) -> None:
        self._repo = repo
        self._knowledge = knowledge_repo
        self._store = store
        self._settings = settings

    def search(
        self, query: str, visible_kbs: Sequence[Any]
    ) -> tuple[list[SearchHit], dict[str, GraphEvidencePayload], list[str], bool]:
        """返回 `(候选, 证据映射, 降级标签, 是否实际产出候选)`；失败只降级不抛。"""
        if self._store.health().status != "ready":
            return [], {}, ["graph_unavailable"], False

        hits: list[SearchHit] = []
        evidence: dict[str, GraphEvidencePayload] = {}
        labels: list[str] = []
        produced = False
        for kb in visible_kbs:
            try:
                seeds = find_seed_entities(
                    self._repo,
                    kb.id,
                    query,
                    limit=self._settings.graph_seed_limit,
                )
                if not seeds:
                    continue
                future = _EXPAND_POOL.submit(
                    self._store.expand,
                    kb.id,
                    [seed.id for seed in seeds],
                    hops=self._settings.graph_hops,
                    max_entities=self._settings.graph_max_entities,
                    max_chunks=self._settings.graph_max_chunks,
                )
                expansion = future.result(
                    timeout=self._settings.graph_timeout_seconds
                )
                if expansion.truncated:
                    labels.append("graph_truncated")
                entity_ids = tuple(
                    dict.fromkeys(
                        [seed.id for seed in seeds] + list(expansion.entity_ids)
                    )
                )
                chunk_ids = self._repo.mention_chunks(entity_ids)
                if not chunk_ids:
                    continue
                chunks = self._knowledge.fetch_search_chunks(
                    chunk_ids, kb_ids=[kb.id]
                )
                if not chunks:
                    continue
                named = self._repo.list_entities_by_ids(kb.id, entity_ids)
                names = {entity.id: entity.name for entity in named}
                names.update({seed.id: seed.name for seed in seeds})
                produced = True
                for chunk in chunks:
                    evidence.setdefault(
                        chunk.id,
                        _evidence_for(
                            chunk.id, seeds, expansion, self._repo, names
                        ),
                    )
                    hits.append(_hit_from_chunk(chunk))
            except Exception as exc:  # noqa: BLE001 - 图路失败只降级
                logger.warning(
                    "knowledge graph route failed: kb={} error={!r}", kb.id, exc
                )
                labels.append("graph_unavailable")
                continue

        hits.sort(
            key=lambda hit: (
                evidence.get(
                    hit.chunk_id, GraphEvidencePayload((), (), 2, ())
                ).hop,
                hit.chunk_id,
            )
        )
        return hits, evidence, list(dict.fromkeys(labels)), produced
