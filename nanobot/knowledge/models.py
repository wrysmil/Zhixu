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


@dataclass(frozen=True, slots=True)
class Document:
    id: str
    kb_id: str
    workspace_id: str
    title: str
    source_type: str
    source_kind: str
    source_path: str
    original_name: str
    mime: str
    file_hash: str
    file_size: int
    source_mtime_ns: int
    parse_status: str
    error_code: str
    error_message: str
    attempt: int
    lease_owner: str
    lease_expires_at: str
    index_version: str
    vector_status: str
    chunk_count: int
    char_count: int
    is_enabled: bool
    created_at: str
    updated_at: str
    indexed_at: str | None


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
