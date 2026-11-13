"""检索第三路：实体链接 → 1~2 跳扩展 → 回表复核 → 融合候选与证据。"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from loguru import logger

from nanobot.knowledge.fusion import SearchHit
from nanobot.knowledge.graph.linker import (
    find_seed_entities,
    find_seed_entities_by_names,
)
from nanobot.knowledge.graph.models import GraphEvidencePayload, GraphExpansion
from nanobot.knowledge.graph.repo import GraphRepository
from nanobot.knowledge.graph.store import GraphStore
from nanobot.knowledge.repository import KnowledgeRepository, StoredChunk
from nanobot.knowledge.trace import hit_rows

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
    # 种子既可能是边的起点，也可能是终点；遍历时双向可达，展示仍保留真实方向。
    adjacency: dict[str, list[tuple[str, tuple[str, str, str]]]] = {}
    for edge in expansion.edges:
        step = (
            names.get(edge.source_entity_id, edge.source_entity_id),
            edge.relation,
            names.get(edge.target_entity_id, edge.target_entity_id),
        )
        adjacency.setdefault(edge.source_entity_id, []).append(
            (edge.target_entity_id, step)
        )
        adjacency.setdefault(edge.target_entity_id, []).append(
            (edge.source_entity_id, step)
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
        for target, step in adjacency.get(current, ()):
            if target in seen:
                continue
            seen.add(target)
            queue.append((target, path + (step,)))
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
        query_entities: Callable[[str], Sequence[str]] | None = None,
    ) -> None:
        self._repo = repo
        self._knowledge = knowledge_repo
        self._store = store
        self._settings = settings
        self._query_entities = query_entities

    def search(
        self,
        query: str,
        visible_kbs: Sequence[Any],
        *,
        trace: Any | None = None,
    ) -> tuple[list[SearchHit], dict[str, GraphEvidencePayload], list[str], bool]:
        """返回 `(候选, 证据映射, 降级标签, 是否实际产出候选)`；失败只降级不抛。"""
        if self._store.health().status != "ready":
            if trace is not None:
                trace.add(
                    "graph",
                    status="degraded",
                    detail={"reason": "store_unavailable"},
                )
            return [], {}, ["graph_unavailable"], False

        # WeKnora 口径：图路开启时先用 LLM 从（改写后的）query 抽实体名；
        # 失败/空结果回退本地归一化子串 + 分词。
        extracted: tuple[str, ...] = ()
        if self._query_entities is not None:
            extraction_started = (
                time.perf_counter() if trace is not None else None
            )
            extraction_failed = False
            try:
                future = _EXPAND_POOL.submit(self._query_entities, query)
                extracted = tuple(
                    future.result(
                        timeout=float(
                            getattr(
                                self._settings,
                                "graph_query_extraction_timeout_seconds",
                                5.0,
                            )
                        )
                    )
                )
            except Exception as exc:  # noqa: BLE001 - 抽实体失败只回退本地链接
                logger.warning(
                    "knowledge graph query entity extraction failed: query={!r} error={!r}",
                    query,
                    exc,
                )
                extracted = ()
                extraction_failed = True
            if trace is not None:
                trace.add(
                    "rewrite",
                    status=(
                        "degraded"
                        if extraction_failed
                        else ("ok" if extracted else "skipped")
                    ),
                    metrics={"entities": len(extracted)},
                    detail={
                        "entities": list(extracted),
                        "fallback": extraction_failed or not extracted,
                    },
                    duration_ms=(
                        0
                        if extraction_started is None
                        else int((time.perf_counter() - extraction_started) * 1000)
                    ),
                )

        hits: list[SearchHit] = []
        evidence: dict[str, GraphEvidencePayload] = {}
        labels: list[str] = []
        produced = False
        seed_count = 0
        route_started = time.perf_counter() if trace is not None else None
        for kb in visible_kbs:
            try:
                seeds = (
                    find_seed_entities_by_names(
                        self._repo,
                        kb.id,
                        extracted,
                        limit=self._settings.graph_seed_limit,
                    )
                    if extracted
                    else []
                )
                if not seeds:
                    # LLM 可能抽出「发布负责人」这类泛化名，图里没有对应实体；
                    # 此时按 KB 回退本地链接，避免图路静默缺席。
                    seeds = find_seed_entities(
                        self._repo,
                        kb.id,
                        query,
                        limit=self._settings.graph_seed_limit,
                    )
                if not seeds:
                    continue
                seed_count += len(seeds)
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
        if trace is not None:
            trace.add(
                "graph",
                status=(
                    "ok"
                    if produced
                    else ("degraded" if "graph_unavailable" in labels else "skipped")
                ),
                metrics={
                    "kbs": len(visible_kbs),
                    "entities": len(extracted),
                    "seeds": seed_count,
                    "candidates": len(hits),
                },
                detail={
                    "labels": list(dict.fromkeys(labels)),
                    "top": hit_rows(hits),
                },
                duration_ms=(
                    0
                    if route_started is None
                    else int((time.perf_counter() - route_started) * 1000)
                ),
            )
        return hits, evidence, list(dict.fromkeys(labels)), produced
