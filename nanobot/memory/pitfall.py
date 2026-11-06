"""踩坑主动提示：按错误文本召回 active pitfall，生成一行参考提示。

只读、绝不抛出；向量不可用时退化为 LIKE。提示会进入下一轮上下文，
因此必须短、标注“仅供参考”，且不能覆盖工具原始报错。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from loguru import logger

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.experience import list_experiences
from nanobot.memory.repository import get_episodes_by_ids

_HINT_MAX_CHARS = 300
_VECTOR_MIN_SCORE = 0.55


def _row_hint(metadata: dict[str, Any], summary: str) -> str:
    title = str(metadata.get("title") or "").strip()
    symptom = str(metadata.get("symptom") or "").strip()
    fix = str(metadata.get("fix") or "").strip()
    root_cause = str(metadata.get("root_cause") or "").strip()
    if not (title or symptom or fix):
        first_lines = [line.strip() for line in summary.splitlines() if line.strip()]
        return first_lines[0][: _HINT_MAX_CHARS] if first_lines else ""
    parts = []
    if title:
        parts.append(title)
    if symptom and symptom != title:
        parts.append(f"症状：{symptom}")
    if root_cause:
        parts.append(f"根因：{root_cause}")
    if fix:
        parts.append(f"修复：{fix}")
    text = "；".join(parts)
    return text[:_HINT_MAX_CHARS]


def _metadata_of(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def find_pitfall_hint(
    db: MemoryDatabase,
    error_text: str,
    *,
    workspace_ids: Sequence[str] | None = None,
) -> str | None:
    """按错误文本找一条最相关的 active pitfall；没有可信命中返回 None。"""
    query = (error_text or "").strip()
    if not query:
        return None

    # 首选向量：同义报错也能命中；低分不提示，避免误报。
    try:
        from nanobot.memory.vector.indexer import get_active_episode_indexer

        indexer = get_active_episode_indexer()
        if indexer is not None:
            hits = indexer.search(query[:500], limit=5)
            confident = [
                str(eid) for eid, score in hits if score >= _VECTOR_MIN_SCORE
            ]
            if confident:
                with db.connect() as conn:
                    rows = get_episodes_by_ids(
                        conn,
                        confident,
                        workspace_ids=workspace_ids,
                        kind="pitfall",
                        status="active",
                    )
                by_id = {str(row.id): row for row in rows}
                for episode_id in confident:
                    row = by_id.get(episode_id)
                    if row is None:
                        continue
                    hint = _row_hint(_metadata_of(row.metadata), row.summary)
                    if hint:
                        return hint
    except Exception as exc:  # noqa: BLE001 - 提示是增强能力，失败只降级
        logger.warning("pitfall vector recall failed, degrading to LIKE: {}", exc)

    rows = list_experiences(
        db,
        workspace_ids=workspace_ids,
        query=query[:120],
        kind="pitfall",
        status="active",
        limit=1,
    )
    if not rows:
        return None
    return _row_hint(rows[0].metadata, rows[0].summary) or None
