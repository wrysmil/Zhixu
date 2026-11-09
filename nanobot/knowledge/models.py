"""知识库行模型与检索 DTO。"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class KnowledgeBase:
    id: str
    name: str
    description: str
    workspace_id: str
    visibility: str
    chunk_size: int
    chunk_overlap: int
    embedding_backend: str
    embedding_model: str
    embedding_dimensions: int
    is_enabled: bool
    created_at: str
    updated_at: str


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
