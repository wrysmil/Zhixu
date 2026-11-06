"""经验库：把“这次发生过什么”写进 SQLite episodes，按需检索。

与长期记忆（MEMORY.md）严格分离：
- 经验只进 ``episodes``（``source=experience``），不写记忆文件；
- 长期规则只进 MEMORY.md，不写经验库；
- 两者之间只允许经用户确认后的“经验升级为规则”，不做内容镜像。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Episode, EpisodeOutcome, EpisodeSource
from nanobot.memory.repository import add_episode

EXPERIENCE_SOURCE = EpisodeSource.EXPERIENCE.value


@dataclass(frozen=True)
class ExperienceRow:
    """经验列表/检索的轻量返回行。"""

    id: str
    title: str
    summary: str
    outcome: str
    workspace_id: str
    ended_at: str
    root_cause: str = ""
    solution: str = ""


def record_experience(
    db: MemoryDatabase,
    *,
    workspace_id: str,
    session_id: str,
    title: str,
    situation: str,
    action: str,
    outcome: str,
    root_cause: str = "",
    solution: str = "",
    verification: str = "",
    tags: Sequence[str] = (),
) -> Episode:
    """写一条经验/失败记录；不写长期记忆。"""
    title = title.strip()
    situation = situation.strip()
    if not title:
        raise ValueError("title 不能为空")
    if not situation:
        raise ValueError("situation 不能为空")
    now = datetime.now(timezone.utc).isoformat()
    summary = f"{situation}\n根因：{root_cause.strip()}\n解法：{solution.strip()}"
    episode = Episode(
        id=uuid4().hex,
        session_id=session_id,
        summary=summary,
        goal=situation,
        outcome=EpisodeOutcome(outcome),
        source=EpisodeSource.EXPERIENCE,
        started_at=now,
        ended_at=now,
        entities=list(dict.fromkeys([*tags, title])),
        tools_used=[],
        tags=["experience", outcome, *tags],
        importance_score=0.6,
        workspace_id=workspace_id,
        metadata={
            "title": title,
            "action": action.strip(),
            "root_cause": root_cause.strip(),
            "solution": solution.strip(),
            "verification": verification.strip(),
        },
    )
    db.ensure_schema()
    with db.connect() as conn:
        add_episode(conn, episode)
    return episode


def list_experiences(
    db: MemoryDatabase,
    *,
    workspace_ids: Sequence[str] | None = None,
    query: str = "",
    outcome: str | None = None,
    limit: int = 20,
) -> list[ExperienceRow]:
    """列出/检索经验；``query`` 为空时按时间倒序列出最近记录。"""
    db.ensure_schema()
    conditions = ["source = ?"]
    params: list[Any] = [EXPERIENCE_SOURCE]
    workspace_ids = tuple(workspace_ids or ())
    if workspace_ids:
        placeholders = ",".join("?" for _ in workspace_ids)
        conditions.append(f"workspace_id IN ({placeholders})")
        params.extend(workspace_ids)
    if outcome:
        conditions.append("outcome = ?")
        params.append(outcome)
    query = query.strip()
    if query:
        like = f"%{query}%"
        conditions.append(
            "(summary LIKE ? OR goal LIKE ? OR entities LIKE ? OR tools_used LIKE ?)"
        )
        params.extend([like, like, like, like])
    params.append(max(1, int(limit)))
    sql = (
        "SELECT id, summary, outcome, ended_at, workspace_id, metadata "
        "FROM episodes "
        f"WHERE {' AND '.join(conditions)} "
        "ORDER BY datetime(ended_at) DESC, id DESC LIMIT ?"
    )
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    results: list[ExperienceRow] = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        results.append(
            ExperienceRow(
                id=str(row["id"]),
                title=str(metadata.get("title") or ""),
                summary=str(row["summary"] or ""),
                outcome=str(row["outcome"] or ""),
                workspace_id=str(row["workspace_id"] or ""),
                ended_at=str(row["ended_at"] or ""),
                root_cause=str(metadata.get("root_cause") or ""),
                solution=str(metadata.get("solution") or ""),
            )
        )
    return results


__all__ = ["ExperienceRow", "record_experience", "list_experiences"]
