"""Pure payload conversion + CRUD action functions for the WebUI memory domain.

This module owns the JSON wire format for ``Memory`` / ``Episode`` /
``ScratchpadEntry`` dataclasses and the pure CRUD entry points consumed by
``memory_routes.py``. It deliberately does NOT depend on HTTP/WebSocket
machinery; transport lives elsewhere.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, cast

from loguru import logger

from nanobot.memory.models import (
    Episode,
    EpisodeOutcome,
    Memory,
    MemoryPriority,
    MemoryType,
    ScratchpadEntry,
)
from nanobot.memory.repository import (
    add_memory,
    get_episode,
    get_memory,
    get_scratchpad,
    known_workspace_ids,
    list_episodes,
    list_memories,
    upsert_scratchpad,
)
from nanobot.memory.repository import (
    delete_episode as _repo_delete_episode,
)
from nanobot.memory.repository import (
    delete_memory as _repo_delete_memory,
)
from nanobot.memory.repository import (
    update_episode as _repo_update_episode,
)
from nanobot.memory.repository import (
    update_memory as _repo_update_memory,
)
from nanobot.memory.vector.indexer import (
    get_active_indexer,
    get_active_store,
    index_memory_best_effort,
    remove_memory_best_effort,
)
from nanobot.webui.memory_presentation import (
    PresentationContext,
    episode_in_store,
    filter_result,
    workspace_options,
)
from nanobot.webui.memory_services import MemoryServices
from nanobot.webui.settings_contracts import WebUISettingsError

_DEFAULT_USER_ID = "default"
_VALID_MEMORY_TYPES = {t.value for t in MemoryType}
_VALID_MEMORY_PRIORITIES = {p.value for p in MemoryPriority}
_VALID_OUTCOMES = {o.value for o in EpisodeOutcome}


class WebUIMemoryError(WebUISettingsError):
    """User-facing memory validation failure with an explicit HTTP status.

    Subclass of ``WebUISettingsError`` so the settings router's single error
    path catches memory-domain failures alongside the rest of the settings API.
    """


# ---- payload converters -----------------------------------------------------


def memory_payload(m: Memory, context: PresentationContext | None = None) -> dict[str, Any]:
    """Serialise a ``Memory`` to the JSON shape the WebUI expects."""
    return {
        "id": m.id,
        "content": m.content,
        "type": m.type.value,
        "priority": m.priority.value,
        "source": m.source,
        "importance_score": m.importance_score,
        "access_count": m.access_count,
        "tags": list(m.tags),
        "subject": m.subject,
        "predicate": m.predicate,
        "confidence": m.confidence,
        "decay_rate": m.decay_rate,
        "expires_at": m.expires_at,
        "last_accessed_at": m.last_accessed_at,
        "superseded_by": m.superseded_by,
        "source_episode_id": m.source_episode_id,
        "scope": m.scope,
        "workspace_id": m.workspace_id,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
        "metadata": dict(m.metadata),
        **(context or PresentationContext(None, [])).fields(m),
    }


def episode_payload(ep: Episode, context: PresentationContext | None = None) -> dict[str, Any]:
    return {
        "id": ep.id,
        "workspace_id": getattr(ep, "workspace_id", "default"),
        "session_id": ep.session_id,
        "summary": ep.summary,
        "goal": ep.goal,
        "outcome": ep.outcome.value,
        "source": ep.source.value,
        "started_at": ep.started_at,
        "ended_at": ep.ended_at,
        "action_nodes": list(ep.action_nodes),
        "entities": list(ep.entities),
        "tools_used": list(ep.tools_used),
        "linked_memory_ids": list(ep.linked_memory_ids),
        "tags": list(ep.tags),
        "importance_score": ep.importance_score,
        "access_count": ep.access_count,
        "metadata": dict(getattr(ep, "metadata", {})),
        **(context or PresentationContext(None, [])).fields(ep),
    }


def scratchpad_payload_from_entry(entry: ScratchpadEntry) -> dict[str, Any]:
    return {
        "user_id": entry.user_id,
        "workspace_id": entry.workspace_id,
        "updated_at": entry.updated_at,
        "content": entry.content,
        "active_projects": list(entry.active_projects),
        "current_focus": entry.current_focus,
        "open_questions": list(entry.open_questions),
        "next_steps": list(entry.next_steps),
    }


# ---- query param parsing ----------------------------------------------------


def _parse_optional_enum(raw: str | None, choices: set[str], field: str) -> str | None:
    if raw is None:
        return None
    if raw not in choices:
        raise WebUIMemoryError(f"invalid {field}: {raw!r}")
    return raw


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _time_order(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Historical writer timestamps without an offset were stored in UTC.
        return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        return float("-inf")


# ---- read actions -----------------------------------------------------------


def list_memories_payload(
    services: MemoryServices,
    *,
    type: str | None = None,
    order: str = "created",
    limit: int | None = None,
    priority: str | None = None,
    origin: str | None = None,
    workspace: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    type_value = _parse_optional_enum(type, _VALID_MEMORY_TYPES, "type")
    priority_value = _parse_optional_enum(priority, _VALID_MEMORY_PRIORITIES, "priority")
    if order not in {"created", "updated", "importance"}:
        raise WebUIMemoryError("order must be 'created', 'updated' or 'importance'")
    rows, episodes, context = _read_presentation(services)
    payloads = [memory_payload(row, context) for row in rows]
    options = workspace_options(services, [*payloads, *[episode_payload(ep, context) for ep in episodes]])
    needle = (q or "").strip().casefold()
    filtered = [
        item for item in payloads
        if (not type_value or item["type"] == type_value)
        and (not priority_value or item["priority"] == priority_value)
        and (not needle or needle in " ".join([
            item["content"], item["subject"], item["predicate"], *item["tags"],
        ]).casefold())
    ]
    field = {"created": "created_at", "updated": "updated_at", "importance": "importance_score"}[order]
    filtered.sort(key=lambda item: (
        float(item[field]) if order == "importance" else _time_order(item[field]),
        _time_order(item["created_at"]), item["id"],
    ), reverse=True)
    return _filtered_result(filtered, options, origin=origin, workspace=workspace, limit=limit)


def search_memories_payload(
    services: MemoryServices,
    query: str,
    *,
    type: str | None = None,
    limit: int | None = None,
    priority: str | None = None,
    origin: str | None = None,
    workspace: str | None = None,
    order: str = "created",
) -> dict[str, Any]:
    if not query.strip():
        raise WebUIMemoryError("query must not be empty")
    return {
        **list_memories_payload(
            services, type=type, order=order, limit=limit, priority=priority,
            origin=origin, workspace=workspace, q=query,
        ),
        "query": query,
    }


def _read_presentation(
    services: MemoryServices,
) -> tuple[list[Memory], list[Episode], PresentationContext]:
    with services.database.connect() as conn:
        rows = list_memories(conn)
        episodes = [ep for ep in list_episodes(conn) if episode_in_store(ep, services)]
    return rows, episodes, PresentationContext(services, episodes)


def _filtered_result(
    items: list[dict[str, Any]], options: list[dict[str, str]], *,
    origin: str | None, workspace: str | None, limit: int | None,
) -> dict[str, Any]:
    try:
        return filter_result(items, options=options, origin=origin, workspace=workspace, limit=limit)
    except WebUISettingsError as exc:
        raise WebUIMemoryError(exc.message, status=exc.status) from exc


def fetch_memory_payload(services: MemoryServices, memory_id: str) -> dict[str, Any]:
    with services.database.connect() as conn:
        row = get_memory(conn, memory_id)
    if row is None:
        raise WebUIMemoryError("memory not found", status=404)
    _, _, context = _read_presentation(services)
    return {"memory": memory_payload(row, context)}


def list_episodes_payload(
    services: MemoryServices,
    *,
    session_id: str | None = None,
    limit: int | None = None,
    origin: str | None = None,
    workspace: str | None = None,
    q: str | None = None,
    order: str = "created",
) -> dict[str, Any]:
    if order not in {"created", "importance"}:
        raise WebUIMemoryError("order must be 'created' or 'importance'")
    memories, rows, context = _read_presentation(services)
    payloads = [episode_payload(row, context) for row in rows]
    options = workspace_options(services, [*payloads, *[memory_payload(m, context) for m in memories]])
    needle = (q or "").strip().casefold()
    filtered = [
        item for item in payloads
        if (not session_id or item["session_id"] == session_id)
        and (not needle or needle in " ".join([
            item["summary"], item["goal"], *item["tags"], *item["entities"], *item["tools_used"],
        ]).casefold())
    ]
    filtered.sort(key=lambda item: (
        float(item["importance_score"]) if order == "importance" else _time_order(item["started_at"]),
        _time_order(item["started_at"]), item["id"],
    ), reverse=True)
    return _filtered_result(filtered, options, origin=origin, workspace=workspace, limit=limit)


def fetch_episode_payload(services: MemoryServices, episode_id: str) -> dict[str, Any]:
    with services.database.connect() as conn:
        row = get_episode(conn, episode_id)
    if row is None or not episode_in_store(row, services):
        raise WebUIMemoryError("episode not found", status=404)
    return {"episode": episode_payload(row, PresentationContext(services, [row]))}


def scratchpad_payload(
    services: MemoryServices,
    *,
    user_id: str = _DEFAULT_USER_ID,
) -> dict[str, Any]:
    with services.database.connect() as conn:
        row = get_scratchpad(conn, user_id, services.workspace_id)
    if row is None:
        return {"scratchpad": None}
    return {"scratchpad": scratchpad_payload_from_entry(row)}


# ---- MEMORY.md derived-state helpers ----------------------------------------


def _get_last_refresh_at(workspace_id: str) -> str | None:
    """从 MemoryLifecycle._last_refresh_iso 读取上次刷新时间（ISO8601 UTC）。"""
    from nanobot.memory.lifecycle import MemoryLifecycle

    return MemoryLifecycle._last_refresh_iso.get(workspace_id)


def _get_last_refresh_trigger(workspace_id: str) -> str | None:
    from nanobot.memory.lifecycle import MemoryLifecycle

    return MemoryLifecycle._last_refresh_trigger.get(workspace_id)


def _draft_exists(workspace_id: str) -> bool:
    """检查 MEMORY.md.draft 是否存在。"""
    from nanobot.memory.lifecycle import MemoryLifecycle

    inst = MemoryLifecycle._instances.get(workspace_id)
    if inst is None:
        return False
    return inst.draft_file.exists()


def _draft_age_seconds(workspace_id: str) -> int | None:
    """draft 文件距今秒数，不存在返回 None。"""
    from nanobot.memory.lifecycle import MemoryLifecycle

    inst = MemoryLifecycle._instances.get(workspace_id)
    if inst is None or not inst.draft_file.exists():
        return None
    try:
        return int(time.time() - inst.draft_file.stat().st_mtime)
    except OSError:
        return None


def _current_memory_md_chars(workspace_id: str) -> int:
    """MEMORY.md 当前字符数，不存在返回 0。"""
    from nanobot.memory.lifecycle import MemoryLifecycle

    inst = MemoryLifecycle._instances.get(workspace_id)
    if inst is None or not inst.memory_file.exists():
        return 0
    try:
        return inst.memory_file.stat().st_size
    except OSError:
        return 0


def _read_memory_md_content(workspace_id: str) -> str | None:
    """读取 MEMORY.md 文件内容，不存在返回 None。"""
    from nanobot.memory.lifecycle import MemoryLifecycle

    inst = MemoryLifecycle._instances.get(workspace_id)
    if inst is None or not inst.memory_file.exists():
        return None
    try:
        return inst.memory_file.read_text(encoding="utf-8")
    except OSError:
        return None


def _read_draft_content(workspace_id: str) -> str | None:
    """读取 MEMORY.md.draft 文件内容，不存在返回 None。"""
    from nanobot.memory.lifecycle import MemoryLifecycle

    inst = MemoryLifecycle._instances.get(workspace_id)
    if inst is None or not inst.draft_file.exists():
        return None
    try:
        return inst.draft_file.read_text(encoding="utf-8")
    except OSError:
        return None


def get_memory_md_content(services: MemoryServices) -> dict[str, Any]:
    """读取 MEMORY.md 和 draft 内容，用于前端 Modal 展示。"""
    workspace_id = services.workspace_id
    memory_md = _read_memory_md_content(workspace_id)
    draft = _read_draft_content(workspace_id)
    return {
        "memory_md": memory_md,
        "draft": draft,
        "memory_md_exists": memory_md is not None,
        "draft_exists": draft is not None,
    }


def stats_payload(
    services: MemoryServices,
    *,
    vector_runtime: Any = None,
) -> dict[str, Any]:
    """Aggregate counts by memory type + 向量层状态（spec §5.4）。

    向量状态必须可见：openakita 把它完全藏在 UI 之外，降级时用户零感知
    （调研文档 §8.2 的反面教材）。``vector_runtime`` 是 ``VectorStore`` 或
    ``None``；未显式传参时 fallback 到进程级 ``get_active_store()``（与 WU-08
    写路径钩子同构，规避逐层透传）。传 ``None`` 时新字段仍全部存在，只是显示
    为不可用——**字段集恒定**，避免前端做存在性判断。
    """
    if vector_runtime is None:
        vector_runtime = get_active_store()

    counters: dict[str, int] = {t.value: 0 for t in MemoryType}
    total = 0
    with services.database.connect() as conn:
        rows = list_memories(conn)
    for r in rows:
        counters[r.type.value] = counters.get(r.type.value, 0) + 1
        total += 1

    if vector_runtime is None:
        return {
            "total": total,
            "by_type": counters,
            "search_backend": "fts5",
            "vector_available": False,
            "vector_state": "disabled",
            "vector_count": 0,
            "vector_model": "",
            "vector_dimensions": 0,
            "vector_error": None,
            "memory_md": {
                "last_refresh_at": _get_last_refresh_at(services.workspace_id),
                "last_refresh_trigger": _get_last_refresh_trigger(services.workspace_id),
                "draft_exists": _draft_exists(services.workspace_id),
                "draft_age_seconds": _draft_age_seconds(services.workspace_id),
                "current_chars": _current_memory_md_chars(services.workspace_id),
                "max_chars": 1500,
            },
        }

    # 兼容测试替身（提供 vector_state / search_backend）与生产 VectorStore
    # （只有 state、无 search_backend）：优先读专有字段，缺失时回退。
    state = str(
        getattr(vector_runtime, "vector_state", None)
        or getattr(vector_runtime, "state", "")
        or "unknown"
    )
    return {
        "total": total,
        "by_type": counters,
        "search_backend": str(getattr(vector_runtime, "search_backend", None) or "chromadb"),
        "vector_available": state == "ready",
        "vector_state": state,
        "vector_count": int(getattr(vector_runtime, "count", lambda: 0)()),
        "vector_model": str(getattr(vector_runtime, "model_name", "")),
        "vector_dimensions": int(getattr(vector_runtime, "dimensions", 0)),
        "vector_error": getattr(vector_runtime, "error", None),
        "memory_md": {
            "last_refresh_at": _get_last_refresh_at(services.workspace_id),
            "last_refresh_trigger": _get_last_refresh_trigger(services.workspace_id),
            "draft_exists": _draft_exists(services.workspace_id),
            "draft_age_seconds": _draft_age_seconds(services.workspace_id),
            "current_chars": _current_memory_md_chars(services.workspace_id),
            "max_chars": 1500,
        },
    }


def _vector_not_ready_reason(vector_runtime: Any) -> str:
    """向量 store 未就绪时返回原因串；就绪或测试替身（无 state 属性）返回 ``""``。"""
    state = getattr(vector_runtime, "state", None) or getattr(
        vector_runtime, "vector_state", None
    )
    if state is None or str(state) == "ready":
        return ""
    return f"vector not ready: {state}"


def reindex_vector(
    services: MemoryServices,
    *,
    indexer: Any = None,
    vector_runtime: Any = None,
) -> dict[str, Any]:
    """全量重建向量索引（spec §5.4）。

    ``sync_from_sqlite`` 本身就是双向修复（补 missing + 删 stale），因此「重建」
    与「同步」在实现上是同一操作；差别只在**语义契约**：``reindex`` 承诺
    「调用后索引与 SQLite 一致」，``sync`` 承诺「增量对账」。保留两个端点是为了
    让运维意图显式，且 openakita **没有**任何手动重建入口（调研文档 §9 末）。

    未显式传 ``indexer`` / ``vector_runtime`` 时 fallback 到进程级单例
    （``get_active_indexer`` / ``get_active_store``）。向量未启用时两者皆
    ``None``，端点返回明确的不可用信号而非 500。
    """
    if indexer is None:
        indexer = get_active_indexer()
    if vector_runtime is None:
        vector_runtime = get_active_store()
    if indexer is None:
        return {"available": False, "indexed": 0, "deleted": 0, "error": "vector disabled"}
    not_ready = _vector_not_ready_reason(vector_runtime)
    if not_ready:
        # store 未就绪时 list_ids 返 []、upsert 静默返 False → 会报「0 改动」的
        # **假成功**；用户无法区分「已一致」与「向量挂了」。显式报不可用。
        return {"available": False, "indexed": 0, "deleted": 0, "error": not_ready}
    result = indexer.sync_from_sqlite()
    return {
        "available": True,
        "indexed": int(result.get("indexed", 0)),
        "deleted": int(result.get("deleted", 0)),
        "error": result.get("error", ""),
        "vector_count": int(getattr(vector_runtime, "count", lambda: 0)()),
    }


def sync_vector(
    services: MemoryServices,
    *,
    indexer: Any = None,
    vector_runtime: Any = None,
) -> dict[str, Any]:
    """增量对账（补 missing + 删 stale），不重建。

    未显式传 ``indexer`` 时 fallback 到 ``get_active_indexer()``；未启用向量时
    返回明确的不可用信号而非 500。
    """
    if indexer is None:
        indexer = get_active_indexer()
    if vector_runtime is None:
        vector_runtime = get_active_store()
    if indexer is None:
        return {"available": False, "indexed": 0, "deleted": 0, "error": "vector disabled"}
    not_ready = _vector_not_ready_reason(vector_runtime)
    if not_ready:
        return {"available": False, "indexed": 0, "deleted": 0, "error": not_ready}
    result = indexer.sync_from_sqlite()
    return {
        "available": True,
        "indexed": int(result.get("indexed", 0)),
        "deleted": int(result.get("deleted", 0)),
        "error": result.get("error", ""),
    }


# ---- write action hooks ------------------------------------------------------


def _refresh_memory_md_after_mutation(
    workspace_id: str, services: "MemoryServices"
) -> None:
    """在 SQLite 写操作后触发 MEMORY.md 派生（同步调用）。

    派生失败只记日志，不抛给 mutation 调用方。
    60s 去抖在 MemoryLifecycle 层面处理。
    """
    try:
        from nanobot.memory.lifecycle import MemoryLifecycle

        lifecycle = MemoryLifecycle.for_workspace(workspace_id, services)
        lifecycle.refresh_memory_md_sync(workspace_id)
    except Exception as e:
        _logger = logging.getLogger(__name__)
        _logger.warning("[MemoryLifecycle] refresh_memory_md failed after mutation: %s", e)


# ---- write actions -----------------------------------------------------------


def create_memory(
    services: MemoryServices,
    *,
    content: str,
    type: str = "fact",
    priority: str = "long_term",
    importance_score: float = 0.5,
    tags: list[str] | None = None,
    subject: str = "",
    predicate: str = "",
    metadata: dict[str, Any] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    if not (isinstance(content, str) and content.strip()):
        raise WebUIMemoryError("content must be a non-empty string")
    if type not in _VALID_MEMORY_TYPES:
        raise WebUIMemoryError(f"invalid type: {type!r}")
    if priority not in _VALID_MEMORY_PRIORITIES:
        raise WebUIMemoryError(f"invalid priority: {priority!r}")
    if not (0.0 <= float(importance_score) <= 1.0):
        raise WebUIMemoryError("importance_score must be in [0, 1]")
    memories, episodes, context = _read_presentation(services)
    options = workspace_options(services, [
        *[memory_payload(item, context) for item in memories],
        *[episode_payload(item, context) for item in episodes],
    ])
    with services.database.connect() as conn:
        known = known_workspace_ids(conn)
    if workspace in (None, "", "unassigned", "default"):
        row_workspace_id = "default"
        selected_workspaces: list[dict[str, str]] = []
    elif workspace in known:
        row_workspace_id = workspace
        selected_workspaces = [option for option in options if option["id"] == workspace]
    else:
        raise WebUIMemoryError("invalid workspace")
    saved_metadata = dict(metadata or {})
    saved_metadata["provenance"] = {
        "generation_method": "manual", "trigger_mode": "manual", "trigger_reason": "manual",
        "workspaces": selected_workspaces, "store_workspace_id": services.workspace_id,
    }
    memory_id = str(uuid.uuid4())
    now = _now_iso()
    memory = Memory(
        id=memory_id,
        content=content.strip(),
        type=MemoryType(type),
        priority=MemoryPriority(priority),
        importance_score=float(importance_score),
        tags=list(tags or []),
        subject=subject,
        predicate=predicate,
        metadata=saved_metadata,
        source="manual",
        workspace_id=row_workspace_id,
        created_at=now,
        updated_at=now,
    )
    try:
        with services.database.connect() as conn:
            add_memory(conn, memory)
    except Exception:
        logger.exception("create_memory failed: id={}", memory_id)
        raise WebUIMemoryError("failed to persist memory", status=500)
    # 向量挂钩：SQLite 落库成功（try 块之后）才 best-effort 索引
    index_memory_best_effort(memory)
    # 🆕 WU-3: 触发 MEMORY.md 派生
    _refresh_memory_md_after_mutation(services.workspace_id, services)
    return {"memory": memory_payload(memory)}


def fetch_and_index(services: MemoryServices, memory_id: str) -> Memory:
    """回读权威行并 best-effort 同步向量索引。

    更新走 repository 后必须回读，因为 ``_repo_update_memory`` 只吃 kwargs、
    不返回新行；同时保证索引里存的是**落库后**的内容。
    """
    with services.database.connect() as conn:
        row = get_memory(conn, memory_id)
    if row is None:
        raise WebUIMemoryError("memory not found", status=404)
    index_memory_best_effort(row)
    return row


def update_memory(
    services: MemoryServices,
    memory_id: str,
    *,
    content: str | None = None,
    type: str | None = None,
    priority: str | None = None,
    importance_score: float | None = None,
    tags: list[str] | None = None,
    subject: str | None = None,
    predicate: str | None = None,
    metadata: dict[str, Any] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if content is not None:
        if not content.strip():
            raise WebUIMemoryError("content must be a non-empty string")
        kwargs["content"] = content.strip()
    if type is not None:
        if type not in _VALID_MEMORY_TYPES:
            raise WebUIMemoryError(f"invalid type: {type!r}")
        kwargs["type"] = MemoryType(type)
    if priority is not None:
        if priority not in _VALID_MEMORY_PRIORITIES:
            raise WebUIMemoryError(f"invalid priority: {priority!r}")
        kwargs["priority"] = MemoryPriority(priority)
    if importance_score is not None:
        if not (0.0 <= float(importance_score) <= 1.0):
            raise WebUIMemoryError("importance_score must be in [0, 1]")
        kwargs["importance_score"] = float(importance_score)
    if tags is not None:
        kwargs["tags"] = list(tags)
    if subject is not None:
        kwargs["subject"] = subject
    if predicate is not None:
        kwargs["predicate"] = predicate
    options: list[dict[str, str]] = []
    if workspace not in (None, "", "unassigned", "default"):
        memories, episodes, context = _read_presentation(services)
        options = workspace_options(services, [
            *[memory_payload(item, context) for item in memories],
            *[episode_payload(item, context) for item in episodes],
        ])
    try:
        with services.database.connect() as conn:
            existing = get_memory(conn, memory_id)
            if existing is None:
                raise WebUIMemoryError("memory not found", status=404)
            if metadata is not None:
                merged_metadata = {**existing.metadata, **metadata}
                # 普通元数据编辑不能覆盖原始生成来源。
                if "provenance" in existing.metadata:
                    merged_metadata["provenance"] = existing.metadata["provenance"]
                else:
                    merged_metadata.pop("provenance", None)
                kwargs["metadata"] = merged_metadata
            if workspace is not None:
                known = known_workspace_ids(conn)
                if workspace in ("", "unassigned", "default"):
                    new_workspace_id = "default"
                    new_workspaces: list[dict[str, str]] = []
                elif workspace in known:
                    new_workspace_id = workspace
                    new_workspaces = [option for option in options if option["id"] == workspace]
                else:
                    raise WebUIMemoryError("invalid workspace")
                merged_metadata = dict(kwargs.get("metadata", existing.metadata))
                existing_provenance = existing.metadata.get("provenance")
                provenance = (
                    dict(cast(dict[str, Any], existing_provenance))
                    if isinstance(existing_provenance, dict) else {}
                )
                provenance["workspaces"] = new_workspaces
                merged_metadata["provenance"] = provenance
                kwargs["workspace_id"] = new_workspace_id
                kwargs["metadata"] = merged_metadata
            _repo_update_memory(conn, memory_id, **kwargs)
    except KeyError:
        raise WebUIMemoryError("memory not found", status=404)
    updated = fetch_and_index(services, memory_id)
    # 🆕 WU-3: 触发 MEMORY.md 派生
    _refresh_memory_md_after_mutation(services.workspace_id, services)
    _, _, context = _read_presentation(services)
    return {"memory": memory_payload(updated, context)}


def delete_memory(services: MemoryServices, memory_id: str) -> dict[str, Any]:
    with services.database.connect() as conn:
        existing = get_memory(conn, memory_id)
        if existing is None:
            raise WebUIMemoryError("memory not found", status=404)
        _repo_delete_memory(conn, memory_id)
    # 向量挂钩：SQLite 删除成功后 best-effort 移除索引
    remove_memory_best_effort(memory_id)
    # 🆕 WU-3: 触发 MEMORY.md 派生
    _refresh_memory_md_after_mutation(services.workspace_id, services)
    return {"ok": True}


def update_episode(
    services: MemoryServices,
    episode_id: str,
    *,
    summary: str | None = None,
    goal: str | None = None,
    tags: list[str] | None = None,
    importance_score: float | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if summary is not None:
        kwargs["summary"] = summary
    if goal is not None:
        kwargs["goal"] = goal
    if tags is not None:
        kwargs["tags"] = list(tags)
    if importance_score is not None:
        kwargs["importance_score"] = float(importance_score)
    try:
        with services.database.connect() as conn:
            existing = get_episode(conn, episode_id)
            if existing is None or not episode_in_store(existing, services):
                raise WebUIMemoryError("episode not found", status=404)
            _repo_update_episode(conn, episode_id, **kwargs)
    except KeyError:
        raise WebUIMemoryError("episode not found", status=404)
    return fetch_episode_payload(services, episode_id)


def delete_episode(services: MemoryServices, episode_id: str) -> dict[str, Any]:
    try:
        with services.database.connect() as conn:
            existing = get_episode(conn, episode_id)
            if existing is None or not episode_in_store(existing, services):
                raise WebUIMemoryError("episode not found", status=404)
            _repo_delete_episode(conn, episode_id)
    except KeyError:
        raise WebUIMemoryError("episode not found", status=404)
    return {"ok": True}


def refresh_memory_md(services: "MemoryServices") -> dict[str, Any]:
    """手动触发 MEMORY.md 从 SQLite 重建。

    供 WebUI mutation 调用。
    """
    from nanobot.memory.lifecycle import MemoryLifecycle

    lifecycle = MemoryLifecycle.for_workspace(services.workspace_id, services)
    result = lifecycle.refresh_memory_md_sync(services.workspace_id)
    return result


def save_scratchpad(
    services: MemoryServices,
    *,
    user_id: str = _DEFAULT_USER_ID,
    content: str = "",
    active_projects: list[str] | None = None,
    current_focus: str = "",
    open_questions: list[str] | None = None,
    next_steps: list[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(content, str):
        raise WebUIMemoryError("content must be a string")
    entry = ScratchpadEntry(
        user_id=user_id,
        workspace_id=services.workspace_id,
        updated_at=_now_iso(),
        content=content,
        active_projects=list(active_projects or []),
        current_focus=current_focus,
        open_questions=list(open_questions or []),
        next_steps=list(next_steps or []),
    )
    with services.database.connect() as conn:
        upsert_scratchpad(conn, entry)
    return {"scratchpad": scratchpad_payload_from_entry(entry)}
