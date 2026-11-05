"""Dream 运行的审计与统计（P1）。

Dream 原来靠 ``GitStore`` 的 diff 交代自己改了什么：改了 SOUL.md、生成了
MEMORY.md.draft。P1 之后 Dream 不再碰任何文件，产出全部进 ``memories`` 表
——git 工作树可能一个字都不变，diff 从此回答不了「昨晚它动了什么」。

本模块补回这个能力，并且**计数不来自模型自述**：每次 Dream 运行开始时登记
一行 ``memory_dream_runs``，运行期间由记忆工具把真实写入决策（added /
updated / deleted / skipped）累加进来，结束时落状态。模型只能报数，不能造数。

为什么用模块级活跃运行表而不是逐层透传：Dream 的执行入口有两处
（``cli/gateway_runtime.py`` 的 cron 回调和 ``command/builtin.py`` 的
``/dream``），工具实例由 ``MemoryStore.build_dream_tools()`` 创建，
再往上传一层就要改三处签名。Dream 每进程串行、频率是小时级，一个按
workspace 维度的活跃表足够，且对主 Agent 的写入天然无副作用
（``active_run`` 查不到就什么都不记）。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from loguru import logger

#: 运行状态：running → completed / failed / no_input。
#: 与 ``BatchReceipt.status`` 一样只取固定几个值，前端不必做存在性判断。
_RUN_STATUSES = frozenset({"running", "completed", "failed", "no_input"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DreamRunStats:
    """一次 Dream 运行期间的写入计数（真实决策，非模型自述）。"""

    entries_read: int = 0
    added: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "entries_read": self.entries_read,
            "added": self.added,
            "updated": self.updated,
            "deleted": self.deleted,
            "skipped": self.skipped,
        }


@dataclass
class DreamRunAudit:
    """一次 Dream 运行的审计句柄：持库写计数，结束时可查询。"""

    workspace_id: str
    database: Any
    run_id: str = field(default_factory=lambda: uuid4().hex)
    trigger: str = ""
    model: str = ""
    started_at: str = field(default_factory=_now_iso)
    stats: DreamRunStats = field(default_factory=DreamRunStats)

    # -- 计数入口（由记忆工具调用） -------------------------------------

    def record_added(self, count: int = 1) -> None:
        self.stats.added += max(0, int(count))

    def record_updated(self, count: int = 1) -> None:
        self.stats.updated += max(0, int(count))

    def record_deleted(self, count: int = 1) -> None:
        self.stats.deleted += max(0, int(count))

    def record_skipped(self, count: int = 1) -> None:
        self.stats.skipped += max(0, int(count))

    # -- 持久化 ---------------------------------------------------------

    def finish(self, status: str, *, error: str = "") -> None:
        """收尾：把最终计数与状态写回 ``memory_dream_runs``。

        写失败只记日志——审计是附属能力，不能让它把一次成功的整理判成失败。
        """
        if status not in _RUN_STATUSES:
            logger.warning("dream audit: unknown status {!r}, recording as failed", status)
            status = "failed"
        try:
            with self.database.connect() as conn:
                conn.execute(
                    "UPDATE memory_dream_runs SET status = ?, added = ?, updated = ?, "
                    "deleted = ?, skipped = ?, entries_read = ?, error = ?, finished_at = ? "
                    "WHERE run_id = ?",
                    (
                        status,
                        self.stats.added,
                        self.stats.updated,
                        self.stats.deleted,
                        self.stats.skipped,
                        self.stats.entries_read,
                        error[:2000],
                        _now_iso(),
                        self.run_id,
                    ),
                )
        except Exception as exc:  # noqa: BLE001 - 审计失败不影响整理结果
            logger.warning("dream audit finish failed: {}: {!r}", type(exc).__name__, exc)

    def summary(self) -> str:
        """给人看的一行摘要（/dream 输出与 WebUI 共用）。"""
        stats = self.stats
        if stats.added == 0 and stats.updated == 0 and stats.deleted == 0:
            return "没有记忆变化"
        parts: list[str] = []
        if stats.added:
            parts.append(f"新增 {stats.added} 条")
        if stats.updated:
            parts.append(f"更新 {stats.updated} 条")
        if stats.deleted:
            parts.append(f"删除 {stats.deleted} 条")
        if stats.skipped:
            parts.append(f"跳过 {stats.skipped} 条")
        return "、".join(parts)


__all__ = [
    "DreamRunAudit",
    "DreamRunStats",
    "active_run",
    "begin_run",
    "end_run",
    "list_recent_runs",
    "reset_active_runs",
]


# ---------------------------------------------------------------------------
# 活跃运行表
# ---------------------------------------------------------------------------

_ACTIVE_RUNS: dict[str, DreamRunAudit] = {}


def begin_run(
    database: Any,
    workspace_id: str,
    *,
    trigger: str,
    model: str = "",
) -> DreamRunAudit:
    """登记一次 Dream 运行，并把它设为该 workspace 的活跃运行。"""
    audit = DreamRunAudit(
        workspace_id=workspace_id, database=database, trigger=trigger, model=model
    )
    try:
        with database.connect() as conn:
            conn.execute(
                "INSERT INTO memory_dream_runs "
                "(run_id, workspace_id, trigger, status, model, started_at) "
                "VALUES (?, ?, ?, 'running', ?, ?)",
                (audit.run_id, workspace_id, trigger, model, audit.started_at),
            )
    except Exception as exc:  # noqa: BLE001 - 登记失败不阻断整理
        logger.warning("dream audit begin failed: {}: {!r}", type(exc).__name__, exc)
    _ACTIVE_RUNS[workspace_id] = audit
    return audit


def end_run(workspace_id: str, *, status: str, error: str = "") -> DreamRunAudit | None:
    """结束该 workspace 的活跃运行并落库；没有活跃运行时返回 None。"""
    audit = _ACTIVE_RUNS.pop(workspace_id, None)
    if audit is None:
        return None
    audit.finish(status, error=error)
    return audit


def active_run(workspace_id: str) -> DreamRunAudit | None:
    """当前活跃的 Dream 运行；主 Agent 的写入查不到，天然不计数。"""
    return _ACTIVE_RUNS.get(workspace_id)


def reset_active_runs() -> None:
    """测试专用：清空活跃运行表，避免用例之间互相污染。"""
    _ACTIVE_RUNS.clear()


def list_recent_runs(
    database: Any,
    workspace_id: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """最近的 Dream 运行记录（新的在前），供 /dream-log 与 WebUI 展示。"""
    try:
        with database.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM memory_dream_runs WHERE workspace_id = ? "
                "ORDER BY started_at DESC LIMIT ?",
                (workspace_id, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception as exc:  # noqa: BLE001 - 查询失败不是致命错误
        logger.warning("dream audit list failed: {}: {!r}", type(exc).__name__, exc)
        return []
