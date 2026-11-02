"""统一写入决策表（spec §5.4，契约 §2.2 / §2.9）。

“一条语义候选怎么落库”只有这一处判断：规则候选、模型候选与手工写入都调用
:func:`apply_memory_changes`。函数只使用调用方传入的连接：

- 不 ``commit``：事务由调用方持有（契约 §2.9）；
- 不调 LLM，不刷新向量索引或 MEMORY.md：派生结果在提交后由调用方重建；
- 不吞写入异常：失败由调用方回滚并返回结构化失败。

决策表（spec §5.4，按顺序命中）：

1. 显式要求记住/纠正：优先处理，不受短文本门槛拦截；仍校验来源与作用域。
2. 完全相同内容与属性：幂等，不新增行；追加支持证据。
3. 同主体同属性且明确纠正、目标唯一：更新当前值，保存前后内容。
4. 同主体同属性但条件/时间/主体不清：不自动覆盖，保留带 ``uncertain`` 标记的
   可辨识冲突候选。
5. 多值属性或不同条件：并存，各自成行（默认分支）。
6. 助手建议、计划、未验证任务结果：不提升为用户当前事实，交给情节承载。
7. 明确撤销/遗忘：失效当前候选与替代链，登记证据抑制；同一旧证据键再次写入
   被拒绝，不复活已遗忘事实。
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Literal, Sequence, cast
from uuid import uuid4

from loguru import logger

from nanobot.memory.models import Memory, MemoryType
from nanobot.memory.repository import (
    add_memory,
    find_memories_by_content,
    find_memory_by_evidence_key,
    get_memory,
    is_evidence_suppressed,
    list_memories_by_subject_predicate,
    register_evidence_suppression,
    update_memory,
)

#: 非显式候选的最小内容长度（显式记忆不受该门槛拦截，spec §5.4 第 1 条）。
MIN_AUTOMATIC_CONTENT_CHARS = 2

#: 合法记忆类型（与 models.MemoryType 保持同源，供写入前运行期校验）。
_VALID_TYPES = frozenset(item.value for item in MemoryType)

#: 单条记忆保留的证据键上限：只用于幂等与遗忘抑制，不需要无限历史。
MAX_EVIDENCE_KEYS = 8

#: ``superseded_by`` 替代链的最大跟随深度（防脏数据形成环）。
_SUPERSEDED_CHAIN_LIMIT = 10

ChangeAction = Literal["add", "update", "upsert_by_evidence", "forget"]
ChangeKind = Literal[
    "added",
    "updated",
    "unchanged",
    "conflict",
    "not_promoted",
    "forgotten",
    "suppressed",
    "skipped",
]


@dataclass(frozen=True)
class MemoryChange:
    """一条待写入变更：沿用 :class:`Memory` 模型，加显式目标与操作类型。

    这些字段只属于写入内部的决策输入，不进入 HTTP/WS 必填协议：

    - ``target_id``：显式目标（手工编辑、纠正、遗忘指向已有 record ID）。
    - ``explicit``：用户显式要求记住/纠正，跳过短文本门槛。
    - ``correction``：显式纠正（同一属性换值），允许更新当前值。
    - ``conditions_clear``：条件/时间/主体可辨识；不清晰时不得自动覆盖。
    - ``assistant_generated``：助手建议、计划或未验证任务结果，不提升为事实。
    - ``evidence_key``：本条变更的幂等/抑制键；缺省时由批次键 + 内容派生。
    """

    memory: Memory
    action: ChangeAction = "add"
    target_id: str | None = None
    explicit: bool = False
    correction: bool = False
    conditions_clear: bool = True
    assistant_generated: bool = False
    evidence_key: str = ""


@dataclass(frozen=True)
class ChangeOutcome:
    """单条变更的决策结果（供调用方统计与日志）。"""

    index: int
    kind: ChangeKind
    memory_id: str | None = None
    conflict_with: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class WriterResult:
    """一批变更的写入结果（不含 commit，承诺由调用方完成）。"""

    outcomes: tuple[ChangeOutcome, ...] = ()
    memory_ids: tuple[str, ...] = ()
    created: tuple[Memory, ...] = ()

    @property
    def skipped(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.kind == "skipped")


def apply_memory_changes(
    conn: sqlite3.Connection,
    changes: Sequence[MemoryChange],
    *,
    evidence_key: str = "",
) -> WriterResult:
    """在调用方事务内按决策表写入一批变更。

    Args:
        conn: 调用方持有的 SQLite 连接；本函数不 commit。
        changes: 待写入的 :class:`MemoryChange` 序列，顺序即决策顺序。
        evidence_key: 批次级证据键（如 batch_id）；单条变更可自带更精确的键。

    Returns:
        :class:`WriterResult`：逐条决策结果、本批涉及的 memory id（含幂等命中的
        既有 id）与本次新建的 :class:`Memory`（供提交后建索引）。
    """
    outcomes: list[ChangeOutcome] = []
    memory_ids: list[str] = []
    created: list[Memory] = []
    for index, change in enumerate(changes):
        key = _resolve_evidence_key(change, evidence_key, index)
        outcome, new_memory = _apply_change(conn, change, key, index)
        outcomes.append(outcome)
        if outcome.memory_id:
            memory_ids.append(outcome.memory_id)
        if new_memory is not None:
            created.append(new_memory)

    if any(outcome.kind in {"conflict", "skipped", "suppressed"} for outcome in outcomes):
        logger.info(
            "memory write decisions: added={} updated={} unchanged={} conflict={} "
            "not_promoted={} forgotten={} suppressed={} skipped={}",
            _count(outcomes, "added"),
            _count(outcomes, "updated"),
            _count(outcomes, "unchanged"),
            _count(outcomes, "conflict"),
            _count(outcomes, "not_promoted"),
            _count(outcomes, "forgotten"),
            _count(outcomes, "suppressed"),
            _count(outcomes, "skipped"),
        )
    return WriterResult(
        outcomes=tuple(outcomes),
        memory_ids=tuple(memory_ids),
        created=tuple(created),
    )


# ---------------------------------------------------------------------------
# 单条决策
# ---------------------------------------------------------------------------


def _apply_change(
    conn: sqlite3.Connection,
    change: MemoryChange,
    key: str,
    index: int,
) -> tuple[ChangeOutcome, Memory | None]:
    memory = change.memory
    if change.action == "forget":
        return _forget(conn, change, key, index)

    if key and is_evidence_suppressed(
        conn, workspace_id=memory.workspace_id, evidence_key=key
    ):
        return _outcome(index, "suppressed", reason="evidence_key_suppressed"), None

    # 规则 6：助手建议/计划/未验证结果不提升为用户当前事实。
    if change.assistant_generated and not change.explicit:
        return _outcome(index, "not_promoted", reason="assistant_suggestion"), None

    reason = _validate(change)
    if reason:
        return _outcome(index, "skipped", reason=reason), None

    exact = _find_exact_duplicate(conn, memory)
    if exact is not None:
        # 规则 2：完全相同内容+属性幂等，只追加支持证据。
        _record_evidence(conn, exact, key)
        return _outcome(index, "unchanged", memory_id=exact.id), None

    if change.action == "upsert_by_evidence" and key:
        previous = find_memory_by_evidence_key(
            conn, key, workspace_id=memory.workspace_id
        )
        if previous is not None and _is_active(previous):
            _update_value(conn, previous, memory, key)
            return _outcome(index, "updated", memory_id=previous.id), None

    if change.action == "update":
        return _apply_explicit_update(conn, change, key, index)

    candidates = _active_same_attribute(conn, memory)
    if not candidates:
        # 规则 5：没有同属性当前值（多值/新属性）→ 并存新行。
        record = _insert(conn, memory, key)
        return _outcome(index, "added", memory_id=record.id), record

    if change.correction and change.conditions_clear and len(candidates) == 1:
        # 规则 3：明确纠正且目标唯一 → 更新当前值，保存前后内容。
        target = candidates[0]
        _update_value(conn, target, memory, key)
        return _outcome(index, "updated", memory_id=target.id), None

    # 规则 4：条件/时间/主体不清 → 不覆盖，保留可辨识冲突候选。
    record = _insert_conflict(conn, memory, candidates, key)
    return (
        _outcome(
            index,
            "conflict",
            memory_id=record.id,
            conflict_with=tuple(candidate.id for candidate in candidates),
            reason="same_attribute_without_explicit_correction",
        ),
        record,
    )


def _apply_explicit_update(
    conn: sqlite3.Connection,
    change: MemoryChange,
    key: str,
    index: int,
) -> tuple[ChangeOutcome, Memory | None]:
    memory = change.memory
    target_id = change.target_id
    if target_id is None:
        candidates = _active_same_attribute(conn, memory)
        if len(candidates) != 1:
            return _outcome(index, "skipped", reason="update_target_ambiguous"), None
        target_id = candidates[0].id
    target = get_memory(conn, target_id)
    if target is None or target.workspace_id != memory.workspace_id:
        return _outcome(index, "skipped", reason="target_scope_mismatch"), None
    _update_value(conn, target, memory, key)
    return _outcome(index, "updated", memory_id=target.id), None


def _forget(
    conn: sqlite3.Connection,
    change: MemoryChange,
    key: str,
    index: int,
) -> tuple[ChangeOutcome, Memory | None]:
    """规则 7：失效当前候选与替代链，并登记证据抑制，阻止旧证据复活。

    只做数据库事实的有效性变更与抑制登记；向量/MEMORY.md 等派生结果由调用方
    在提交后按 dirty 重建（契约 §2.9 的写入边界）。
    """
    scope = change.memory.workspace_id
    target = get_memory(conn, change.target_id or "")
    if target is None or target.workspace_id != scope:
        return _outcome(index, "skipped", reason="forget_target_scope_mismatch"), None

    now = _now_iso()
    chain = _with_superseded_chain(conn, target)
    for record in chain:
        metadata = dict(record.metadata)
        metadata["forgotten_at"] = now
        update_memory(conn, record.id, expires_at=now, metadata=metadata)
        # 旧证据键与新证据键都登记：重试同一批次、旧 fallback 与草稿恢复都必须
        # 先查这张表，才能不复活被遗忘的事实。
        for old_key in _evidence_keys(record):
            _suppress(conn, scope, old_key, record.id, record.content, now)
    if key:
        _suppress(conn, scope, key, target.id, target.content, now)
    logger.info(
        "memory forgotten: memory_id={} workspace_id={} chain={}",
        target.id,
        scope,
        len(chain),
    )
    return _outcome(index, "forgotten", memory_id=target.id), None


# ---------------------------------------------------------------------------
# 判定辅助
# ---------------------------------------------------------------------------


def _validate(change: MemoryChange) -> str:
    """保留内容长度、合法 type、来源边界与作用域校验（D08 保留项）。"""
    memory = change.memory
    if not memory.workspace_id:
        return "missing_scope"
    if not memory.source:
        return "missing_source"
    type_value = getattr(memory.type, "value", None)
    if not isinstance(type_value, str) or type_value not in _VALID_TYPES:
        return "invalid_type"
    content = memory.content.strip()
    if not content:
        return "empty_content"
    if not change.explicit and len(content) < MIN_AUTOMATIC_CONTENT_CHARS:
        return "content_too_short"
    return ""


def _active_same_attribute(
    conn: sqlite3.Connection, memory: Memory
) -> list[Memory]:
    """同主体同属性的当前候选；subject/predicate 缺失时无合并语义。"""
    if not memory.subject or not memory.predicate:
        return []
    rows = list_memories_by_subject_predicate(
        conn, memory.subject, memory.predicate, workspace_id=memory.workspace_id
    )
    return [row for row in rows if _is_active(row)]


def _find_exact_duplicate(
    conn: sqlite3.Connection, memory: Memory
) -> Memory | None:
    rows = find_memories_by_content(
        conn, memory.content.strip(), workspace_id=memory.workspace_id
    )
    for row in rows:
        if not _is_active(row):
            continue
        if _same_attributes(row, memory):
            return row
    return None


def _same_attributes(existing: Memory, incoming: Memory) -> bool:
    return (
        existing.content.strip() == incoming.content.strip()
        and existing.type == incoming.type
        and existing.priority == incoming.priority
        and existing.subject == incoming.subject
        and existing.predicate == incoming.predicate
    )


def _is_active(memory: Memory) -> bool:
    if memory.superseded_by:
        return False
    if not memory.expires_at:
        return True
    try:
        return memory.expires_at > _now_iso()
    except TypeError:  # 脏数据不可比较时按有效处理，交给读取层兜底
        return True


def _with_superseded_chain(
    conn: sqlite3.Connection, memory: Memory
) -> list[Memory]:
    chain: list[Memory] = [memory]
    seen = {memory.id}
    current = memory
    for _ in range(_SUPERSEDED_CHAIN_LIMIT):
        next_id = current.superseded_by
        if not next_id or next_id in seen:
            break
        seen.add(next_id)
        following = get_memory(conn, next_id)
        if following is None:
            break
        chain.append(following)
        current = following
    return chain


# ---------------------------------------------------------------------------
# 写入原语
# ---------------------------------------------------------------------------


def _insert(conn: sqlite3.Connection, memory: Memory, key: str) -> Memory:
    record = replace(
        memory,
        id=memory.id or str(uuid4()),
        created_at=memory.created_at or _now_iso(),
        updated_at=_now_iso(),
        metadata=_with_evidence(memory.metadata, key),
    )
    add_memory(conn, record)
    return record


def _insert_conflict(
    conn: sqlite3.Connection,
    memory: Memory,
    candidates: Sequence[Memory],
    key: str,
) -> Memory:
    metadata = _with_evidence(memory.metadata, key)
    metadata["conflicts_with"] = [candidate.id for candidate in candidates]
    metadata["uncertain"] = True
    metadata["uncertain_reason"] = "same_attribute_without_explicit_correction"
    return _insert(conn, replace(memory, metadata=metadata), "")


def _update_value(
    conn: sqlite3.Connection,
    target: Memory,
    incoming: Memory,
    key: str,
) -> None:
    """更新当前值：保存前后内容，不另立“第二条事实链”。"""
    metadata = _with_evidence(target.metadata, key)
    metadata["previous_content"] = target.content
    update_memory(
        conn,
        target.id,
        content=incoming.content,
        type=incoming.type,
        priority=incoming.priority,
        importance_score=incoming.importance_score,
        tags=list(incoming.tags),
        subject=incoming.subject or target.subject,
        predicate=incoming.predicate or target.predicate,
        metadata=metadata,
    )


def _record_evidence(conn: sqlite3.Connection, memory: Memory, key: str) -> None:
    if not key or key in _evidence_keys(memory):
        return
    update_memory(conn, memory.id, metadata=_with_evidence(memory.metadata, key))


def _suppress(
    conn: sqlite3.Connection,
    workspace_id: str,
    evidence_key: str,
    memory_id: str,
    content: str,
    forgotten_at: str,
) -> None:
    register_evidence_suppression(
        conn,
        workspace_id=workspace_id,
        evidence_key=evidence_key,
        memory_id=memory_id,
        summary=_content_fingerprint(content),
        forgotten_at=forgotten_at,
    )


# ---------------------------------------------------------------------------
# 证据键与元数据
# ---------------------------------------------------------------------------


def _resolve_evidence_key(change: MemoryChange, batch_key: str, index: int) -> str:
    if change.evidence_key:
        return change.evidence_key
    if not batch_key:
        return ""
    # upsert-by-evidence / forget 的键由调用方决定（如 batch_id + 规则证据位置），
    # 不能再按内容派生，否则同一证据键换内容就找不到原行、遗忘也抑制不住。
    if change.action in {"upsert_by_evidence", "forget"}:
        return batch_key
    memory = change.memory
    payload = f"{memory.subject}|{memory.predicate}|{memory.content}|{index}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"{batch_key}:{digest}"


def _evidence_keys(memory: Memory) -> list[str]:
    raw = memory.metadata.get("evidence_keys")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in cast("list[object]", raw) if item]


def _with_evidence(metadata: dict[str, Any], key: str) -> dict[str, Any]:
    merged: dict[str, Any] = dict(metadata)
    if not key:
        return merged
    raw = merged.get("evidence_keys")
    keys = (
        [str(item) for item in cast("list[object]", raw) if item]
        if isinstance(raw, list)
        else []
    )
    if key not in keys:
        keys.append(key)
    merged["evidence_keys"] = keys[-MAX_EVIDENCE_KEYS:]
    return merged


def _content_fingerprint(content: str) -> str:
    """遗忘记录只留指纹，不保存被遗忘正文（契约 §3）。"""
    if not content:
        return ""
    return hashlib.sha1(content.strip().encode("utf-8")).hexdigest()[:12]


def _outcome(
    index: int,
    kind: ChangeKind,
    *,
    memory_id: str | None = None,
    conflict_with: tuple[str, ...] = (),
    reason: str = "",
) -> ChangeOutcome:
    return ChangeOutcome(
        index=index,
        kind=kind,
        memory_id=memory_id,
        conflict_with=conflict_with,
        reason=reason,
    )


def _count(outcomes: Sequence[ChangeOutcome], kind: ChangeKind) -> int:
    return sum(1 for outcome in outcomes if outcome.kind == kind)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
