"""每日盘账（``memory_consolidate``）：规则级去重 + 衰减 + 归档（P2）。

与 Dream 的分工：Dream（每 2h）做增量抽取；本模块（每天 1 次）扫全库，只做
规则级运算——零 LLM 调用，带时限与 checkpoint，超时下次续跑。Dream 的
``interval_h`` 降频会放大单次增量导致上下文截断，所以两件事永远分开跑。

衰减公式::

    effective = importance_score - decay_rate * days_since_last_access

- ``days_since_last_access`` 按 ``last_accessed_at`` 算，无值回退 ``created_at``；
- ``effective`` 低于 :data:`DECAY_ARCHIVE_THRESHOLD` → **归档**（不删除）：
  ``expires_at`` 拨到当前时刻，``current_memory_sql`` 随之判它无效——检索、
  注入、投影全部自然消失；行与归档原因仍在，清掉 ``expires_at`` 即可还原；
- ``PREFERENCE`` / ``RULE`` 不衰减（用户偏好与规则没有「过期」概念）；
- ``importance_score >= 0.9`` 豁免（永久化的门槛要高）；
- 检索命中刷新 ``last_accessed_at``（见 ``repository.touch_memories``），
  「常用的东西自动变重要」由这条热路径完成。

去重两档：内容哈希完全一致的重复行确定性合并（保留重要性最高的一条，其余
``superseded_by`` 指向它）；向量索引可用时再做一轮语义近重复合并，同一事实
的不同措辞也能收拢。两档都是幂等操作，中断后重跑不会产生二次副作用。

审计：每次运行写一行 ``memory_consolidate_runs``（处理/合并/衰减/归档计数），
页面与「上次运行结果」都读它——记忆凭空消失必须有账可查。

Public API:
    run_consolidation                       执行一次全库盘账（dry_run 支持预演）
    run_consolidation_for_all_workspaces    按工作区逐个盘账（每日 job 用）
    latest_interrupted_run                  读取上次中断的 checkpoint
    list_recent_runs                        最近运行记录（WebUI 用）
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from loguru import logger

from nanobot.memory.context import is_manual_note_record
from nanobot.memory.models import Memory, MemoryType
from nanobot.memory.provenance import GLOBAL_MEMORY_WORKSPACE_ID
from nanobot.memory.repository import current_memory_sql, known_workspace_ids

#: effective 低于该值 → 归档。
DECAY_ARCHIVE_THRESHOLD = 0.15

#: 重要性达到该值的条目豁免衰减（永久化门槛要高）。
DECAY_EXEMPT_MIN_IMPORTANCE = 0.9

#: 不参与衰减的类型：用户偏好与规则没有「过期」概念。
DECAY_EXEMPT_TYPES: frozenset[MemoryType] = frozenset(
    {MemoryType.PREFERENCE, MemoryType.RULE}
)

#: 每批扫描条数（keyset 分页）。
_SCAN_BATCH = 200

#: 语义近重复的相似度门槛（VectorStore.search 的 score 越高越相似）。
_SEMANTIC_DUP_SCORE = 0.92

#: dry_run 预览里最多列出多少条明细。
_PREVIEW_LIMIT = 50

_RUN_STATUSES = frozenset({"running", "completed", "interrupted", "failed"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _monotonic() -> float:
    """单调时钟的单一入口；测试注入时间推进时只替换这一处。"""
    return time.monotonic()


def days_since_last_access(memory: Memory, *, now: datetime) -> float:
    """距上次访问的天数；无 ``last_accessed_at`` 时按 ``created_at`` 算。"""
    raw = memory.last_accessed_at or memory.created_at
    try:
        then = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0.0
    if then.tzinfo is None:
        from datetime import timezone as _tz

        then = then.replace(tzinfo=_tz.utc)
    return max(0.0, (now - then).total_seconds() / 86400)


def effective_importance(memory: Memory, *, now: datetime) -> float:
    """衰减后的有效重要性。"""
    return memory.importance_score - memory.decay_rate * days_since_last_access(
        memory, now=now
    )


def is_decay_exempt(memory: Memory) -> bool:
    """偏好 / 规则 / 高重要性条目不衰减。"""
    if memory.type in DECAY_EXEMPT_TYPES:
        return True
    return memory.importance_score >= DECAY_EXEMPT_MIN_IMPORTANCE


def is_manual_note_id(memory_id: str) -> bool:
    return is_manual_note_record(memory_id)


@dataclass
class ConsolidationStats:
    """一次盘账的计数。"""

    processed: int = 0
    merged: int = 0
    decayed: int = 0
    archived: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "processed": self.processed,
            "merged": self.merged,
            "decayed": self.decayed,
            "archived": self.archived,
        }


@dataclass
class ConsolidationCheckpoint:
    """decay 阶段的续跑指针（keyset 分页坐标）。

    dedup 阶段是幂等全表操作，不设 checkpoint；中断只可能发生在 decay
    阶段，恢复时从 ``(last_created_at, last_id)`` 之后继续。
    """

    last_created_at: str = ""
    last_id: str = ""

    def as_json(self) -> str:
        return json.dumps(
            {"last_created_at": self.last_created_at, "last_id": self.last_id},
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str | None) -> "ConsolidationCheckpoint":
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
            return cls(
                last_created_at=str(data.get("last_created_at") or ""),
                last_id=str(data.get("last_id") or ""),
            )
        except (ValueError, TypeError):
            return cls()


@dataclass
class ConsolidationReport:
    """一次盘账的完整结果（审计行 + 给页面的摘要共用）。"""

    run_id: str
    status: str = "completed"
    dry_run: bool = False
    stats: ConsolidationStats = field(default_factory=ConsolidationStats)
    checkpoint: ConsolidationCheckpoint = field(default_factory=ConsolidationCheckpoint)
    error: str = ""
    #: dry_run / 审计明细：将归档条目摘要（封顶 _PREVIEW_LIMIT）。
    archived_preview: list[dict[str, Any]] = field(default_factory=list)
    #: dry_run 明细：将合并的重复组（封顶 _PREVIEW_LIMIT）。
    merged_preview: list[dict[str, Any]] = field(default_factory=list)
    #: 语义近重复合并这一步是否真的跑了：``ran`` /
    #: ``skipped:vector_unavailable`` / ``failed:<ExcName>``。向量后端未就绪时
    #: 合并数恒为 0，没有这个状态就分不清「没有重复」和「去重没跑」。
    semantic_dedup: str = ""

    def summary(self) -> str:
        stats = self.stats
        prefix = "预演（未落库）：" if self.dry_run else ""
        if (
            stats.processed == 0
            and stats.merged == 0
            and stats.decayed == 0
            and stats.archived == 0
        ):
            return prefix + "没有需要处理的记忆"
        parts = [f"处理 {stats.processed} 条"]
        if stats.merged:
            parts.append(f"合并 {stats.merged} 条")
        if stats.decayed:
            parts.append(f"衰减 {stats.decayed} 条")
        if stats.archived:
            parts.append(f"归档 {stats.archived} 条")
        return prefix + "、".join(parts) + "。"


# ---------------------------------------------------------------------------
# 归档 / 合并的 SQL 动作
# ---------------------------------------------------------------------------


def _archive_row(conn: sqlite3.Connection, memory: Memory, *, now: str) -> None:
    """归档：拨 ``expires_at`` 到当前时刻，并在 metadata 里留归档原因。

    ``current_memory_sql`` 用 ``datetime(expires_at) >= datetime('now')`` 判有效，
    拨到 now 之后该行立即从召回/注入/投影消失，但数据本身不删除。
    """
    metadata = dict(memory.metadata)
    metadata["decay_archived"] = {
        "at": now,
        "importance_score": memory.importance_score,
        "decay_rate": memory.decay_rate,
        "days_since_last_access": round(
            days_since_last_access(memory, now=_parse_now(now)), 2
        ),
    }
    # 只拨过期时间：归档是生命周期事件，内容没有变，改 ``updated_at`` 会让
    # 「按更新时间排序」把归档条目顶到最前，看起来像刚被编辑过。
    conn.execute(
        "UPDATE memories SET expires_at = ?, metadata = ? WHERE id = ?",
        (now, json.dumps(metadata, ensure_ascii=False), memory.id),
    )


def _supersede_row(
    conn: sqlite3.Connection, memory_id: str, *, canonical_id: str, now: str
) -> None:
    conn.execute(
        "UPDATE memories SET superseded_by = ?, updated_at = ? WHERE id = ?",
        (canonical_id, now, memory_id),
    )


def _parse_now(now_iso: str) -> datetime:
    dt = datetime.fromisoformat(now_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# 阶段一：规则级去重
# ---------------------------------------------------------------------------


def _dedup_pass(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    now: str,
    dry_run: bool,
    stats: ConsolidationStats,
    report: ConsolidationReport,
) -> None:
    """内容完全一致的重复行合并到一条 canonical（幂等，重跑无副作用）。"""
    rows = conn.execute(
        f"SELECT {flat_columns()} FROM memories "
        "WHERE workspace_id = ? AND " + current_memory_sql("memories") +
        " AND (superseded_by IS NULL OR superseded_by = '') "
        "ORDER BY created_at ASC, id ASC",
        (workspace_id,),
    ).fetchall()
    memories = [memory_from_row(row) for row in rows]

    groups: dict[str, list[Memory]] = {}
    for memory in memories:
        if is_manual_note_id(memory.id):
            continue
        key = (memory.type.value, (memory.content or "").strip())
        groups.setdefault(key, []).append(memory)

    for members in groups.values():
        if len(members) < 2:
            continue
        # canonical = 重要性最高；平局取最早创建（稳定、可复算）。
        members.sort(
            key=lambda m: (-m.importance_score, m.created_at, m.id)
        )
        canonical, duplicates = members[0], members[1:]
        if not dry_run:
            for duplicate in duplicates:
                _supersede_row(
                    conn, duplicate.id, canonical_id=canonical.id, now=now
                )
        stats.merged += len(duplicates)
        if len(report.merged_preview) < _PREVIEW_LIMIT:
            report.merged_preview.append(
                {
                    "canonical": canonical.id,
                    "content": canonical.content[:120],
                    "duplicate_ids": [m.id for m in duplicates],
                }
            )


def _semantic_dedup_pass(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    now: str,
    dry_run: bool,
    stats: ConsolidationStats,
    report: ConsolidationReport,
) -> str:
    """向量索引可用时的语义近重复合并（同一事实的不同措辞）。

    best-effort：索引未就绪 / 任何异常都跳过，绝不阻断盘账。只在哈希
    去重没动的行里找——完全相同的行上一阶段已经收拢。

    返回这一步的执行状态，写进审计行（见 ``ConsolidationReport.semantic_dedup``）。
    """
    try:
        from nanobot.memory.vector.indexer import get_active_store

        store = get_active_store()
        if store is None or not store.enabled() or not store._ready():  # noqa: SLF001
            return "skipped:vector_unavailable"
    except Exception as exc:  # noqa: BLE001
        return f"failed:{type(exc).__name__}"

    rows = conn.execute(
        f"SELECT {flat_columns()} FROM memories "
        "WHERE workspace_id = ? AND " + current_memory_sql("memories") +
        " AND (superseded_by IS NULL OR superseded_by = '') "
        "ORDER BY created_at ASC, id ASC",
        (workspace_id,),
    ).fetchall()
    memories = [
        memory_from_row(row)
        for row in rows
        if not is_manual_note_id(row["id"])
    ]
    by_id = {m.id: m for m in memories}
    already_merged: set[str] = set()

    for memory in memories:
        if memory.id in already_merged:
            continue
        try:
            matches = store.search(memory.content, limit=4)
        except Exception:  # noqa: BLE001 - 单条嵌入失败不放弃整轮
            continue
        for match_id, score in matches:
            if match_id == memory.id or match_id not in by_id:
                continue
            if match_id in already_merged:
                continue
            if score < _SEMANTIC_DUP_SCORE:
                continue
            other = by_id[match_id]
            if (other.type.value, other.content.strip()) == (
                memory.type.value,
                memory.content.strip(),
            ):
                continue  # 哈希组已处理
            # canonical 取重要性高的那条；平局取更早创建（与哈希去重同规则）。
            canonical, duplicate = (
                (memory, other) if _is_canonical(memory, other) else (other, memory)
            )
            already_merged.add(duplicate.id)
            if not dry_run:
                _supersede_row(conn, duplicate.id, canonical_id=canonical.id, now=now)
            stats.merged += 1
            if len(report.merged_preview) < _PREVIEW_LIMIT:
                report.merged_preview.append(
                    {
                        "canonical": canonical.id,
                        "content": canonical.content[:120],
                        "duplicate_ids": [duplicate.id],
                        "similarity": round(score, 4),
                    }
                )
    return "ran"


def _is_canonical(a: Memory, b: Memory) -> bool:
    """a 是否应成为 canonical：重要性高者优先；平局取更早创建。"""
    if a.importance_score != b.importance_score:
        return a.importance_score > b.importance_score
    return a.created_at <= b.created_at


# ---------------------------------------------------------------------------
# 阶段二：衰减 + 归档（带 checkpoint 续跑）
# ---------------------------------------------------------------------------


def _decay_pass(
    conn: sqlite3.Connection,
    *,
    workspace_id: str,
    now: str,
    now_dt: datetime,
    archive_threshold: float,
    dry_run: bool,
    stats: ConsolidationStats,
    report: ConsolidationReport,
    checkpoint: ConsolidationCheckpoint,
    deadline: float | None,
) -> None:
    """扫全库算衰减，低于阈值的归档。keyset 分页，超时从 checkpoint 续跑。"""
    while True:
        if deadline is not None and _monotonic() >= deadline:
            report.status = "interrupted"
            return

        clauses = [
            "workspace_id = ?",
            current_memory_sql("memories"),
            "(superseded_by IS NULL OR superseded_by = '')",
        ]
        params: list[Any] = [workspace_id]
        if checkpoint.last_created_at:
            clauses.append("(created_at > ? OR (created_at = ? AND id > ?))")
            params.extend(
                [checkpoint.last_created_at, checkpoint.last_created_at, checkpoint.last_id]
            )
        rows = conn.execute(
            f"SELECT {flat_columns()} FROM memories WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at ASC, id ASC LIMIT ?",
            [*params, _SCAN_BATCH],
        ).fetchall()
        if not rows:
            return

        for row in rows:
            memory = memory_from_row(row)
            checkpoint.last_created_at, checkpoint.last_id = (
                memory.created_at,
                memory.id,
            )
            if is_manual_note_id(memory.id):
                continue
            stats.processed += 1
            if is_decay_exempt(memory):
                continue
            effective = effective_importance(memory, now=now_dt)
            if effective >= memory.importance_score:
                continue  # 还没开始衰减（刚被访问/创建）
            stats.decayed += 1
            if effective < archive_threshold:
                stats.archived += 1
                if dry_run:
                    if len(report.archived_preview) < _PREVIEW_LIMIT:
                        report.archived_preview.append(_preview_entry(memory, effective))
                    continue
                _archive_row(conn, memory, now=now)
                if len(report.archived_preview) < _PREVIEW_LIMIT:
                    report.archived_preview.append(_preview_entry(memory, effective))

        if len(rows) < _SCAN_BATCH:
            return


def _preview_entry(memory: Memory, effective: float) -> dict[str, Any]:
    return {
        "id": memory.id,
        "content": memory.content[:120],
        "importance_score": memory.importance_score,
        "effective": round(effective, 4),
        "type": memory.type.value,
    }


# ---------------------------------------------------------------------------
# 编排入口
# ---------------------------------------------------------------------------


def flat_columns() -> str:
    from nanobot.memory.repository import _SELECT_MEMORY_COLUMNS  # noqa: SLF001

    return " ".join(_SELECT_MEMORY_COLUMNS.split())


def memory_from_row(row: sqlite3.Row) -> Memory:
    from nanobot.memory.repository import _row_to_memory  # noqa: SLF001

    return _row_to_memory(row)


def run_consolidation(
    database: Any,
    *,
    workspace_id: str,
    dry_run: bool = False,
    time_budget_seconds: int = 1500,
    archive_threshold: float = DECAY_ARCHIVE_THRESHOLD,
    trigger: str = "cron",
    resume: bool = True,
) -> ConsolidationReport:
    """执行一次全库盘账。

    Args:
        database: :class:`~nanobot.memory.database.MemoryDatabase`。
        workspace_id: 工作区。
        dry_run: True 时只算不写，报告里给出将归档 / 将合并的明细。
        time_budget_seconds: 时间预算；超时写 checkpoint 中断，下次续跑。
        archive_threshold: 归档阈值（配置可调）。
        trigger: 审计行来源（cron / manual）。
        resume: True 时从上次 interrupted 的 checkpoint 继续。
    """
    run_id = uuid4().hex
    report = ConsolidationReport(run_id=run_id, dry_run=dry_run)
    stats = report.stats
    deadline = (
        _monotonic() + max(30, int(time_budget_seconds))
        if time_budget_seconds > 0
        else None
    )
    started_at = _now_iso()
    now = _now_iso()
    now_dt = _parse_now(now)

    checkpoint = ConsolidationCheckpoint()
    if resume:
        previous = latest_interrupted_run(database, workspace_id)
        if previous is not None:
            checkpoint = ConsolidationCheckpoint.from_json(previous.get("checkpoint"))
            report.checkpoint = checkpoint
            logger.info(
                "memory_consolidate: resuming from checkpoint of run {}",
                previous.get("run_id"),
            )

    try:
        with database.connect() as conn:
            _run_body(
                conn, database=database, workspace_id=workspace_id,
                report=report, stats=stats, checkpoint=checkpoint,
                dry_run=dry_run, archive_threshold=archive_threshold,
                trigger=trigger, run_id=run_id, started_at=started_at,
                now=now, now_dt=now_dt, deadline=deadline,
            )
    except Exception as exc:  # noqa: BLE001
        report.status = "failed"
        report.error = str(exc)
        logger.exception("memory_consolidate run failed")
    return report


def run_consolidation_for_all_workspaces(
    database: Any,
    *,
    workspace_ids: Iterable[str] | None = None,
    include_global: bool = True,
    dry_run: bool = False,
    time_budget_seconds: int = 1500,
    archive_threshold: float = DECAY_ARCHIVE_THRESHOLD,
    trigger: str = "cron",
    resume: bool = True,
) -> list[tuple[str, ConsolidationReport]]:
    """按工作区逐个盘账，返回 ``(workspace_id, report)`` 列表。

    不传 ``workspace_ids`` 时取库内已出现过的全部工作区键，并按需补上全局
    ``"default"``。总时间预算在本次涉及的工作区之间均分（每个不低于 30 秒，
    与 ``run_consolidation`` 的下限一致），避免工作区一多就整体超时。
    """
    if workspace_ids is None:
        with database.connect() as conn:
            resolved = set(known_workspace_ids(conn))
    else:
        resolved = {ws for ws in workspace_ids if ws}
    if include_global:
        resolved.add(GLOBAL_MEMORY_WORKSPACE_ID)

    ordered = sorted(resolved)
    if not ordered:
        return []
    per_workspace_budget = (
        max(30, int(time_budget_seconds) // len(ordered))
        if time_budget_seconds > 0
        else 0
    )

    results: list[tuple[str, ConsolidationReport]] = []
    for workspace_id in ordered:
        report = run_consolidation(
            database,
            workspace_id=workspace_id,
            dry_run=dry_run,
            time_budget_seconds=per_workspace_budget,
            archive_threshold=archive_threshold,
            trigger=trigger,
            resume=resume,
        )
        results.append((workspace_id, report))
    return results


def _run_body(
    conn: sqlite3.Connection,
    *,
    database: Any,
    workspace_id: str,
    report: ConsolidationReport,
    stats: ConsolidationStats,
    checkpoint: ConsolidationCheckpoint,
    dry_run: bool,
    archive_threshold: float,
    trigger: str,
    run_id: str,
    started_at: str,
    now: str,
    now_dt: datetime,
    deadline: float | None,
) -> None:
    _begin_run_row(
        conn, run_id=run_id, workspace_id=workspace_id, trigger=trigger,
        dry_run=dry_run, started_at=started_at,
    )
    _dedup_pass(
        conn, workspace_id=workspace_id, now=now, dry_run=dry_run,
        stats=stats, report=report,
    )
    report.semantic_dedup = _semantic_dedup_pass(
        conn, workspace_id=workspace_id, now=now, dry_run=dry_run,
        stats=stats, report=report,
    )
    _decay_pass(
        conn, workspace_id=workspace_id, now=now, now_dt=now_dt,
        archive_threshold=archive_threshold, dry_run=dry_run,
        stats=stats, report=report, checkpoint=checkpoint, deadline=deadline,
    )
    if dry_run:
        # 预演的全部数据写入就地回滚；审计行是 _begin 阶段单独提交的，
        # 回滚不影响它，随后仍可更新出 dry_run 的统计。
        conn.rollback()
    report.checkpoint = (
        ConsolidationCheckpoint() if report.status != "interrupted" else checkpoint
    )
    _finish_run_row(
        conn,
        run_id=run_id,
        status=report.status,
        stats=stats,
        checkpoint=report.checkpoint,
        error=report.error,
        semantic_dedup=report.semantic_dedup,
    )


# ---------------------------------------------------------------------------
# 审计行（memory_consolidate_runs）
# ---------------------------------------------------------------------------


def _begin_run_row(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    workspace_id: str,
    trigger: str,
    dry_run: bool,
    started_at: str,
) -> None:
    try:
        conn.execute(
            "INSERT INTO memory_consolidate_runs "
            "(run_id, workspace_id, trigger, status, dry_run, started_at) "
            "VALUES (?, ?, ?, 'running', ?, ?)",
            (run_id, workspace_id, trigger, 1 if dry_run else 0, started_at),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - 审计失败不阻断盘账
        logger.warning("memory_consolidate audit begin failed: {!r}", exc)


def _finish_run_row(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    stats: ConsolidationStats,
    checkpoint: ConsolidationCheckpoint,
    error: str,
    semantic_dedup: str = "",
) -> None:
    if status not in _RUN_STATUSES:
        status = "failed"
    try:
        conn.execute(
            "UPDATE memory_consolidate_runs SET status = ?, processed = ?, merged = ?, "
            "decayed = ?, archived = ?, checkpoint = ?, error = ?, semantic_dedup = ?, "
            "finished_at = ? WHERE run_id = ?",
            (
                status,
                stats.processed,
                stats.merged,
                stats.decayed,
                stats.archived,
                checkpoint.as_json() if status == "interrupted" else "",
                error[:2000],
                semantic_dedup[:120],
                _now_iso(),
                run_id,
            ),
        )
        # 数据写入在 dry_run 时已于调用方回滚；这里只剩审计行的更新，统一提交。
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_consolidate audit finish failed: {!r}", exc)


def latest_interrupted_run(database: Any, workspace_id: str) -> dict[str, Any] | None:
    """该 workspace 最近一次**尚未被完成运行覆盖**的 interrupted 运行。

    只取最近一次 completed 之后的中断行：续跑完成后再下一轮不能再回到旧
    checkpoint，否则每次都会从同一个半路位置重扫。
    """
    try:
        with database.connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM memory_consolidate_runs WHERE workspace_id = ? "
                "AND status = 'interrupted' AND started_at > COALESCE(("
                "  SELECT MAX(started_at) FROM memory_consolidate_runs "
                "  WHERE workspace_id = ? AND status = 'completed'"
                "), '') ORDER BY started_at DESC LIMIT 1",
                (workspace_id, workspace_id),
            ).fetchone()
        return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_consolidate latest_interrupted_run failed: {!r}", exc)
        return None


def list_recent_runs(
    database: Any,
    workspace_id: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """最近的盘账运行记录（新的在前），供自动化页展示。"""
    try:
        with database.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM memory_consolidate_runs WHERE workspace_id = ? "
                "ORDER BY started_at DESC LIMIT ?",
                (workspace_id, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_consolidate list_recent_runs failed: {!r}", exc)
        return []


__all__ = [
    "ConsolidationCheckpoint",
    "ConsolidationReport",
    "ConsolidationStats",
    "DECAY_ARCHIVE_THRESHOLD",
    "DECAY_EXEMPT_MIN_IMPORTANCE",
    "DECAY_EXEMPT_TYPES",
    "days_since_last_access",
    "effective_importance",
    "is_decay_exempt",
    "latest_interrupted_run",
    "list_recent_runs",
    "run_consolidation",
]
