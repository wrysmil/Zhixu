"""查询链接：归一化子串 + 查询分词，产出 ≤seed_limit 个种子实体。

v1 不建实体 FTS：中文 unicode61 会把整段中文当单 token。
"""

from __future__ import annotations

from nanobot.knowledge.graph.models import GraphEntity
from nanobot.knowledge.graph.normalize import normalize_entity_name
from nanobot.knowledge.graph.repo import GraphRepository
from nanobot.knowledge.tokenizer import tokenize_index


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
