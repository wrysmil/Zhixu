"""查询链接：归一化子串 + 查询分词，产出 ≤seed_limit 个种子实体。

v1 不建实体 FTS：中文 unicode61 会把整段中文当单 token。
"""

from __future__ import annotations

from collections.abc import Sequence

from nanobot.knowledge.graph.models import GraphEntity
from nanobot.knowledge.graph.normalize import normalize_entity_name
from nanobot.knowledge.graph.repo import GraphRepository
from nanobot.knowledge.tokenizer import tokenize_index


def find_seed_entities_by_names(
    repo: GraphRepository,
    kb_id: str,
    names: Sequence[str],
    *,
    limit: int = 5,
) -> list[GraphEntity]:
    """LLM 抽出的实体名 → 种子；精确名优先，其次名称包含。"""
    normalized = [
        normalize_entity_name(name) for name in names if str(name).strip()
    ]
    normalized = [name for name in normalized if name]
    if not normalized:
        return []
    candidates = repo.search_entities_by_like(
        kb_id, "", normalized, limit=max(50, limit * 20)
    )
    scored: list[tuple[int, int, int, str, GraphEntity]] = []
    for entity in candidates:
        exact = sum(1 for name in normalized if entity.name_norm == name)
        hits = sum(1 for name in normalized if name in entity.name_norm)
        scored.append((exact, hits, entity.mention_count, entity.id, entity))
    scored.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
    return [item[4] for item in scored[: max(1, limit)]]


def find_seed_entities(
    repo: GraphRepository,
    kb_id: str,
    query: str,
    *,
    limit: int = 5,
) -> list[GraphEntity]:
    normalized = normalize_entity_name(query)
    tokens = [token for token in tokenize_index(query).split() if len(token) >= 2][:8]
    if not normalized and not tokens:
        return []
    candidates = repo.search_entities_by_like(
        kb_id, normalized, tokens, limit=max(50, limit * 20)
    )
    scored: list[tuple[int, int, int, str, GraphEntity]] = []
    for entity in candidates:
        exact = 1 if normalized and entity.name_norm == normalized else 0
        hits = sum(1 for token in tokens if token in entity.name_norm)
        scored.append((exact, hits, entity.mention_count, entity.id, entity))
    scored.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
    if not scored:
        return []
    exact = [item[4] for item in scored if item[0] == 1]
    rest = [item[4] for item in scored if item[0] == 0]
    return (exact + rest)[: max(1, limit)]
