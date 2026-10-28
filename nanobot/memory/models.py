"""三层记忆系统的 dataclass 数据模型。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MemoryType(str, Enum):
    FACT = 'fact'
    PREFERENCE = 'preference'
    SKILL = 'skill'
    ERROR = 'error'
    RULE = 'rule'
    EXPERIENCE = 'experience'


class MemoryPriority(str, Enum):
    SHORT_TERM = 'short_term'
    LONG_TERM = 'long_term'


class EpisodeOutcome(str, Enum):
    COMPLETED = 'completed'
    FAILED = 'failed'
    PARTIAL = 'partial'
    ONGOING = 'ongoing'


class EpisodeSource(str, Enum):
    MANUAL = 'manual'
    SESSION_END = 'session_end'
    CONTEXT_COMPRESS = 'context_compress'
    TOPIC_CHANGE = 'topic_change'
    DAILY_CONSOLIDATION = 'daily_consolidation'
    DELETION = 'deletion'
    IDLE = 'idle'


def _default_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Memory:
    id: str
    content: str
    created_at: str
    updated_at: str = field(default_factory=_default_now)
    type: MemoryType = MemoryType.FACT
    priority: MemoryPriority = MemoryPriority.LONG_TERM
    source: str = 'manual'
    importance_score: float = 0.5
    access_count: int = 0
    tags: list[str] = field(default_factory=list)
    subject: str = ''
    predicate: str = ''
    confidence: float = 0.5
    decay_rate: float = 0.1
    expires_at: str | None = None
    last_accessed_at: str | None = None
    superseded_by: str | None = None
    source_episode_id: str | None = None
    scope: str = 'global'
    scope_owner: str = ''
    agent_id: str = ''
    user_id: str = 'default'
    workspace_id: str = 'default'
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        """序列化为 SQLite 行 dict（JSON 字段已转字符串）。"""
        d = {
            'id': self.id,
            'content': self.content,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'type': self.type.value,
            'priority': self.priority.value,
            'source': self.source,
            'importance_score': self.importance_score,
            'access_count': self.access_count,
            'tags': json.dumps(self.tags, ensure_ascii=False),
            'subject': self.subject,
            'predicate': self.predicate,
            'confidence': self.confidence,
            'decay_rate': self.decay_rate,
            'expires_at': self.expires_at,
            'last_accessed_at': self.last_accessed_at,
            'superseded_by': self.superseded_by,
            'source_episode_id': self.source_episode_id,
            'scope': self.scope,
            'scope_owner': self.scope_owner,
            'agent_id': self.agent_id,
            'user_id': self.user_id,
            'workspace_id': self.workspace_id,
            'metadata': json.dumps(self.metadata, ensure_ascii=False),
        }
        return d

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Memory":
        """从 ``to_row()`` 序列化得到的 dict 反构实例（WU-3B fallback replay 用）。"""
        return cls(
            id=row['id'],
            content=row['content'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            type=MemoryType(row['type']) if not isinstance(row['type'], MemoryType) else row['type'],
            priority=MemoryPriority(row['priority']) if not isinstance(row['priority'], MemoryPriority) else row['priority'],
            source=row['source'],
            importance_score=row['importance_score'],
            access_count=row['access_count'],
            tags=json.loads(row['tags']) if isinstance(row['tags'], str) and row['tags'] else [],
            subject=row['subject'],
            predicate=row['predicate'],
            confidence=row['confidence'],
            decay_rate=row['decay_rate'],
            expires_at=row['expires_at'],
            last_accessed_at=row['last_accessed_at'],
            superseded_by=row['superseded_by'],
            source_episode_id=row['source_episode_id'],
            scope=row['scope'],
            scope_owner=row['scope_owner'],
            agent_id=row['agent_id'],
            user_id=row['user_id'],
            workspace_id=row['workspace_id'],
            metadata=json.loads(row['metadata']) if isinstance(row['metadata'], str) and row['metadata'] else {},
        )


@dataclass
class Episode:
    id: str
    session_id: str
    summary: str
    started_at: str
    ended_at: str
    goal: str = ''
    outcome: EpisodeOutcome = EpisodeOutcome.COMPLETED
    source: EpisodeSource = EpisodeSource.SESSION_END
    action_nodes: list[dict[str, Any]] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    #: 与本情节关联的记忆：本批次产出的记忆 ∪ 按 subject↔entities 语义命中的记忆。
    linked_memory_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    importance_score: float = 0.5
    access_count: int = 0
    compaction_checkpoint_id: str = ''
    workspace_snapshot_id: str = ''
    workspace_id: str = 'default'
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'session_id': self.session_id,
            'summary': self.summary,
            'goal': self.goal,
            'outcome': self.outcome.value,
            'source': self.source.value,
            'started_at': self.started_at,
            'ended_at': self.ended_at,
            'action_nodes': json.dumps(self.action_nodes, ensure_ascii=False),
            'entities': json.dumps(self.entities, ensure_ascii=False),
            'tools_used': json.dumps(self.tools_used, ensure_ascii=False),
            'linked_memory_ids': json.dumps(self.linked_memory_ids, ensure_ascii=False),
            'tags': json.dumps(self.tags, ensure_ascii=False),
            'importance_score': self.importance_score,
            'access_count': self.access_count,
            'compaction_checkpoint_id': self.compaction_checkpoint_id,
            'workspace_snapshot_id': self.workspace_snapshot_id,
            'workspace_id': self.workspace_id,
            'metadata': json.dumps(self.metadata, ensure_ascii=False),
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Episode":
        """从 ``to_row()`` 序列化得到的 dict 反构实例（WU-3B fallback replay 用）。"""
        def _load_json_list(value: Any) -> list[Any]:
            if isinstance(value, str):
                return json.loads(value) if value else []
            if isinstance(value, list):
                return value
            return []

        return cls(
            id=row['id'],
            session_id=row['session_id'],
            summary=row['summary'],
            goal=row['goal'],
            outcome=EpisodeOutcome(row['outcome']) if not isinstance(row['outcome'], EpisodeOutcome) else row['outcome'],
            source=EpisodeSource(row['source']) if not isinstance(row['source'], EpisodeSource) else row['source'],
            started_at=row['started_at'],
            ended_at=row['ended_at'],
            action_nodes=_load_json_list(row['action_nodes']),
            entities=_load_json_list(row['entities']),
            tools_used=_load_json_list(row['tools_used']),
            linked_memory_ids=_load_json_list(row['linked_memory_ids']),
            tags=_load_json_list(row['tags']),
            importance_score=row['importance_score'],
            access_count=row['access_count'],
            compaction_checkpoint_id=row['compaction_checkpoint_id'],
            workspace_snapshot_id=row['workspace_snapshot_id'],
            workspace_id=row.get('workspace_id') or 'default',
            metadata=(
                json.loads(row['metadata']) if isinstance(row.get('metadata'), str) and row['metadata']
                else dict(row['metadata']) if isinstance(row.get('metadata'), dict)
                else {}
            ),
        )


@dataclass
class ScratchpadEntry:
    user_id: str
    workspace_id: str
    updated_at: str
    content: str = ''
    active_projects: list[str] = field(default_factory=list)
    current_focus: str = ''
    open_questions: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        return {
            'user_id': self.user_id,
            'workspace_id': self.workspace_id,
            'content': self.content,
            'active_projects': json.dumps(self.active_projects, ensure_ascii=False),
            'current_focus': self.current_focus,
            'open_questions': json.dumps(self.open_questions, ensure_ascii=False),
            'next_steps': json.dumps(self.next_steps, ensure_ascii=False),
            'updated_at': self.updated_at,
        }


@dataclass
class ExtractionState:
    """``session_extraction_state`` 表的行数据模型（WU-A idle 增量提取游标）。

    - ``last_count``: 本会话上次增量抽取覆盖到的 ``session.messages`` 长度；
      下次 ``extract_incremental(session, last_count)`` 只扫增量。
    - ``last_source``: 上次抽取的来源标签（``session_end`` / ``deletion`` /
      ``context_compress`` / ``idle`` / ``topic_change``）；保留用于审计与
      未来策略判定。
    - ``last_extracted_at``: 上次成功抽取的 ISO-8601 UTC 时间戳。
    - ``updated_at``: 本行任意字段最近一次写入时间（UPSERT 总会刷新）。
    """

    session_key: str
    last_count: int
    last_source: str
    last_extracted_at: str
    updated_at: str
