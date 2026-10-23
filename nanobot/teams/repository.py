"""团队仓储：单事务内的修订 CAS、事件 seq、幂等键与运行/任务落盘。

契约 §2 是表结构唯一真相源；§3 的 requestId 幂等、§6 的「状态改变与事件追加同一事务」
都落在本模块。与 ``nanobot/writing/repository.py`` 同范式：**CAS 用
``UPDATE ... WHERE revision = :expected`` 的 rowcount 判定**，不做「先读再写」。

四条硬口径：

1. **一次状态写入 = 一个事件**。``last_seq`` 只由 :meth:`_append_event` 递增，因此
   plan Task 2 的锚点断言 ``after.last_seq == before.last_seq + 1`` 成立，补拉方
   （WU-07/10）可以只靠 seq 判断有没有缺号。
2. **幂等判定先于 CAS**。同一 ``request_id`` 重放直接返回已存响应；同 key 不同载荷拒绝。
   顺序颠倒会让重连重放被自己的 CAS 判成版本冲突。
3. **一个写事务内只用同一个连接**。事务里再开连接会在自己持有的写锁上撞 BUSY，
   所以所有 ``*_snapshot`` 读取都提供 conn 版本，公开读入口才自开连接。
4. **读路径不建库**。库文件不存在时列表返回空、读取抛 :class:`TeamNotFound`，绝不在
   只读调用里创建 ``teams/`` 目录。

``team_revisions`` 只追加、``team_tasks`` 归属不可改，由 ``database.py`` 的触发器在数据
库层兜住；本模块不依赖调用方守规矩。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from nanobot.teams.database import (
    DatabaseAbsent,
    StorageUnavailable,
    TeamDatabase,
    WriterBusy,
)
from nanobot.teams.models import (
    MAX_TASK_INSTRUCTION_CHARS,
    SERVER_OWNED_DEFINITION_FIELDS,
    TEAM_EVENT_TYPES,
    LeaderUpdatedData,
    RunCancellingData,
    RunFinishedData,
    RunStartedData,
    TaskEventData,
    TeamBinding,
    TeamDefinition,
    TeamEvent,
    TeamEventType,
    TeamMember,
    TeamRevisionSnapshot,
    TeamRunSnapshot,
    TeamTaskResult,
    TeamTaskSnapshot,
    TeamTaskUsage,
    TeamValidationError,
)

DEFAULT_LIST_LIMIT = 20
MIN_LIST_LIMIT = 1
MAX_LIST_LIMIT = 100
DEFAULT_EVENT_LIMIT = 200
MAX_EVENT_LIMIT = 500
MAX_ACTIVITY_EVENTS_PER_RUN = 5000
ACTIVITY_RETENTION_DAYS = 30

TASK_TERMINAL_STATUSES: tuple[str, ...] = (
    "succeeded", "failed", "cancelled", "interrupted", "blocked",
)
#: ``cancelling`` 之后**不允许**再落地的「成功类」终态：取消是不可回退的（契约 §6）。
_LATE_SUCCESS_STATUSES: tuple[str, ...] = ("succeeded", "completed")
RUN_TERMINAL_STATUSES: tuple[str, ...] = (
    "completed", "partial", "failed", "cancelled", "interrupted",
)
TASK_OPEN_STATUSES: tuple[str, ...] = ("pending", "queued", "running", "cancelling")
# 重试的起跑集：只有这两种状态的任务会被重新执行（契约 §5）。
RETRY_SOURCE_STATUSES: tuple[str, ...] = ("failed", "interrupted")
# 只读前置引用的总预算：下游要看懂上游做完了什么，但不能把新任务的指令挤爆。
RETRY_REFERENCE_BUDGET = 8_000
# 任务状态 → 该状态落库时追加的事件类型（契约 §6 只有这 5 个 task.* 类型）。
TASK_EVENT_BY_STATUS: dict[str, str] = {
    "pending": "task.created",
    "queued": "task.queued",
    "running": "task.started",
    # 契约 §6：任务进入 cancelling 时复用 task.activity 携带更新后的快照，不新增事件类型。
    "cancelling": "task.activity",
    "succeeded": "task.finished",
    "failed": "task.finished",
    "cancelled": "task.finished",
    "interrupted": "task.finished",
    "blocked": "task.finished",
}


class TeamNotFound(LookupError):  # noqa: N818  # 对应契约 §3 的 TEAM_NOT_FOUND
    """团队在本工作区从未存在。"""


class RunNotFound(LookupError):  # noqa: N818  # 对应 RUN_NOT_FOUND
    """运行不存在，或不属于本工作区。"""


class TaskNotFound(LookupError):  # noqa: N818
    """任务不存在。"""


class TeamRevisionConflict(ValueError):  # noqa: N818  # 对应 TEAM_REVISION_CONFLICT
    """``expected_revision`` 与实际修订不一致：调用方应重新加载，而不是覆盖别人。"""

    def __init__(self, team_id: str, current_revision: int) -> None:
        super().__init__(
            f"TEAM_REVISION_CONFLICT team_id={team_id} current_revision={current_revision}"
        )
        self.team_id = team_id
        self.current_revision = current_revision


class RequestReplayConflict(ValueError):  # noqa: N818  # 对应 409
    """同一 requestId 被用于不同载荷：拒绝，不重复执行。"""


class DispatchKeyConflict(ValueError):  # noqa: N818  # 对应 DISPATCH_KEY_CONFLICT
    """同一 batchId 被用于不同任务集合。"""


class IllegalTaskTransition(ValueError):  # noqa: N818
    """终态不可回退，也不能重复终止（契约 §6）。"""


@dataclass(frozen=True)
class DispatchOutcome:
    """一次派单落盘结果；``replayed=True`` 表示重放，没有启动新任务。"""

    run_id: str
    batch_id: str
    task_ids: list[str]
    replayed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "batchId": self.batch_id,
            "taskIds": list(self.task_ids),
            "replayed": self.replayed,
        }


def payload_digest(payload: object) -> str:
    """幂等键的内容指纹：同一个 id 只允许对应同一份写入载荷。"""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


# 兼容仓储内部既有叫法。
_payload_key = payload_digest



def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _days_ago(moment: datetime, days: int) -> str:
    return (moment - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


class TeamRepository:
    """一个 workspace 一个实例；所有写操作都在单事务内完成。"""

    def __init__(self, db: TeamDatabase) -> None:
        self.db = db
        self.workspace_key = db.workspace_key

    @contextmanager
    def claim_writer(self, *, timeout: float = 0.0) -> Generator[None, None, None]:
        """持有本 workspace 的团队调度独占锁；抢不到就是存储不可用，不是「等一会儿」。

        只有持锁者可以改写遗留运行（契约 §6）。清理中断运行也走这里。
        """
        try:
            with self.db.claim_writer(timeout=timeout):
                yield None
        except WriterBusy as exc:
            raise StorageUnavailable(str(exc)) from exc

    def hold_writer(self) -> bool:
        """进程级占用调度权（gateway 启动时调一次）。"""
        return self.db.hold_writer()

    def release_writer(self) -> None:
        self.db.release_writer()

    @property
    def writer_held(self) -> bool:
        return self.db.writer_held

    # -------------------------------------------------------------- 团队与修订

    def save_team(
        self,
        *,
        definition: TeamDefinition,
        snapshot: TeamRevisionSnapshot,
        expected_revision: int | None,
        request_id: str,
    ) -> dict[str, Any]:
        """原子保存团队当前修订 + 追加历史修订 + 记录幂等键。

        创建（``expected_revision=None``）要求定义已盖章为 revision=1；更新走 CAS。
        """
        self._require_coherent(definition, snapshot)
        op = f"team.save:{definition.id}:{expected_revision}"
        payload = definition.to_dict()
        # 幂等指纹只取客户端可写部分：createdAt/updatedAt 每次盖章都不同，把它们算进去
        # 会让合法重放被判成「同 key 不同载荷」。
        fingerprint = {
            key: value for key, value in payload.items() if key not in SERVER_OWNED_DEFINITION_FIELDS
        }
        replay = self._replay(request_id, op, fingerprint)
        if replay is not None:
            return replay
        now = utc_now_iso()
        try:
            with self.db.connect(create=True) as conn:
                if expected_revision is None:
                    if definition.revision != 1:
                        raise TeamValidationError(
                            f"team create must start at revision 1, got {definition.revision}"
                        )
                    conn.execute(
                        "INSERT INTO teams (workspace_key, team_id, revision, archived, name,"
                        " payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            self.workspace_key, definition.id, definition.revision,
                            int(definition.archived), definition.name,
                            json.dumps(payload, ensure_ascii=False), now, now,
                        ),
                    )
                else:
                    current = self._read_team_row(conn, definition.id)
                    if current is None:
                        raise TeamNotFound(definition.id)
                    if int(current["revision"]) != int(expected_revision):
                        raise TeamRevisionConflict(definition.id, int(current["revision"]))
                    updated = conn.execute(
                        "UPDATE teams SET revision = ?, archived = ?, name = ?, payload = ?,"
                        " updated_at = ?"
                        " WHERE workspace_key = ? AND team_id = ? AND revision = ?",
                        (
                            definition.revision, int(definition.archived), definition.name,
                            json.dumps(payload, ensure_ascii=False), now,
                            self.workspace_key, definition.id, int(expected_revision),
                        ),
                    )
                    if updated.rowcount != 1:
                        raise TeamRevisionConflict(definition.id, int(current["revision"]))
                self._write_revision(conn, definition, snapshot, now)
                response = {"team": payload}
                self._record_request(conn, request_id, op, fingerprint, response)
        except sqlite3.IntegrityError as exc:
            # 只剩「创建撞上已存在团队」这一种真实竞争：INSERT 命中主键。
            if expected_revision is None:
                raise TeamRevisionConflict(definition.id, self._current_revision(definition.id)) from exc
            raise TeamValidationError(str(exc)) from exc
        return response

    def _write_revision(
        self,
        conn: sqlite3.Connection,
        definition: TeamDefinition,
        snapshot: TeamRevisionSnapshot,
        now: str,
    ) -> None:
        """追加一条不可变修订快照（与 teams 行同一事务）。"""
        conn.execute(
            "INSERT INTO team_revisions (workspace_key, team_id, revision, snapshot,"
            " created_at) VALUES (?, ?, ?, ?, ?)",
            (
                self.workspace_key, definition.id, definition.revision,
                json.dumps(snapshot.to_dict(), ensure_ascii=False), now,
            ),
        )

    def get_team(self, team_id: str) -> TeamDefinition:
        return self._definition_from_row(self._team_row(team_id))

    def list_teams(self, *, limit: int = DEFAULT_LIST_LIMIT, cursor: str | None = None) -> dict[str, Any]:
        resolved_limit = self._resolve_limit(limit)
        after = self._decode_cursor(cursor)
        sql = "SELECT payload, updated_at, team_id FROM teams WHERE workspace_key = ?"
        params: list[Any] = [self.workspace_key]
        if after is not None:
            sql += " AND (updated_at < ? OR (updated_at = ? AND team_id < ?))"
            params += [after[0], after[0], after[1]]
        sql += " ORDER BY updated_at DESC, team_id DESC LIMIT ?"
        params.append(resolved_limit + 1)
        rows = self._read(sql, params)
        page = rows[:resolved_limit]
        next_cursor = (
            self._encode_cursor(page[-1]["updated_at"], page[-1]["team_id"])
            if len(rows) > resolved_limit
            else None
        )
        return {"teams": [self._loads(row["payload"], "team definition") for row in page], "nextCursor": next_cursor}

    def set_archived(self, team_id: str, *, expected_revision: int, request_id: str) -> dict[str, Any]:
        """归档推进一个修订；已归档时原样返回，保证重复归档幂等（契约 §3）。"""
        op = f"team.archive:{team_id}"
        replay = self._replay(request_id, op, {"team_id": team_id})
        if replay is not None:
            return replay
        with self.db.connect(create=True) as conn:
            row = self._read_team_row(conn, team_id)
            if row is None:
                raise TeamNotFound(team_id)
            definition = self._definition_from_row(row)
            if definition.archived:
                response = {
                    "id": team_id, "archived": True, "revision": definition.revision
                }
                self._record_request(conn, request_id, op, {"team_id": team_id}, response)
                return response
            if definition.revision != int(expected_revision):
                raise TeamRevisionConflict(team_id, definition.revision)
            now = utc_now_iso()
            # 归档是一次真实的修订变更：成员引用没变，但当前修订必须前进，
            # 否则 team_revisions 会和已存在的那一版撞主键。
            archived = TeamDefinition.from_dict(
                {
                    **definition.to_dict(),
                    "archived": True,
                    "revision": definition.revision + 1,
                    "updatedAt": now,
                }
            )
            previous = self._snapshot_from_json(conn, definition.id, definition.revision)
            coherent = TeamRevisionSnapshot(
                definition=archived, leader=previous.leader, members=previous.members
            )
            updated = conn.execute(
                "UPDATE teams SET revision = ?, archived = 1, name = ?, payload = ?, updated_at = ?"
                " WHERE workspace_key = ? AND team_id = ? AND revision = ?",
                (
                    archived.revision, archived.name,
                    json.dumps(archived.to_dict(), ensure_ascii=False), now,
                    self.workspace_key, team_id, int(expected_revision),
                ),
            )
            if updated.rowcount != 1:
                latest = self._read_team_row(conn, team_id)
                raise TeamRevisionConflict(team_id, int(latest["revision"]) if latest else 0)
            self._write_revision(conn, archived, coherent, now)
            response = {"id": team_id, "archived": True, "revision": archived.revision}
            self._record_request(conn, request_id, op, {"team_id": team_id}, response)
        return response

    def clone_team(
        self,
        source: TeamDefinition,
        *,
        snapshot: TeamRevisionSnapshot,
        team_id: str,
        name: str,
        request_id: str,
    ) -> dict[str, Any]:
        """复制成 revision=1 的新团队；成员引用与 limits 原样带过去。"""
        now = utc_now_iso()
        definition = TeamDefinition.from_dict(
            {
                **source.to_dict(),
                "id": team_id,
                "name": name,
                "revision": 1,
                "archived": False,
                "createdAt": now,
                "updatedAt": now,
            }
        )
        cloned = TeamRevisionSnapshot(definition=definition, leader=snapshot.leader, members=snapshot.members)
        return self.save_team(
            definition=definition, snapshot=cloned, expected_revision=None, request_id=request_id
        )

    def get_revision_snapshot(self, team_id: str, *, revision: int | None = None) -> TeamRevisionSnapshot:
        target = revision if revision is not None else self.get_team(team_id).revision
        try:
            with self.db.connect() as conn:
                return self._snapshot_from_json(conn, team_id, int(target))
        except DatabaseAbsent:
            raise TeamNotFound(team_id) from None

    def list_revisions(self, team_id: str) -> list[dict[str, Any]]:
        rows = self._read(
            "SELECT revision, created_at FROM team_revisions"
            " WHERE workspace_key = ? AND team_id = ? ORDER BY revision ASC",
            [self.workspace_key, team_id],
        )
        return [{"revision": int(row["revision"]), "created_at": row["created_at"]} for row in rows]

    def teams_referencing_profile(self, agent_id: str) -> list[str]:
        """未归档团队的引用列表；归档团队与历史快照不绑住 Agent 删除（契约 §3）。"""
        rows = self._read(
            "SELECT payload FROM teams WHERE workspace_key = ? AND archived = 0",
            [self.workspace_key],
        )
        hits: list[str] = []
        for row in rows:
            definition = self._definition_from_row(row)
            if definition.leader_agent_id == agent_id or any(
                member.agent_id == agent_id for member in definition.members
            ):
                hits.append(definition.id)
        return hits

    # ------------------------------------------------------------------- 运行

    def run_binding_snapshot(self, binding: TeamBinding) -> TeamRevisionSnapshot:
        return self.get_revision_snapshot(binding.team_id, revision=binding.team_revision)

    def create_run(
        self,
        *,
        binding: TeamBinding,
        chat_id: str,
        session_key: str,
        turn_id: str,
        internal_turn_id: str,
        snapshot: TeamRevisionSnapshot,
        parent_run_id: str | None = None,
        started_at: str | None = None,
    ) -> TeamRunSnapshot:
        """建运行并落 ``run.started``；同 session_key + 可见 turn_id 只允许一个运行。"""
        now = started_at or utc_now_iso()
        with self.db.connect(create=True) as conn:
            run_id = self._insert_run(
                conn,
                binding=binding,
                chat_id=chat_id,
                session_key=session_key,
                turn_id=turn_id,
                internal_turn_id=internal_turn_id,
                snapshot=snapshot,
                parent_run_id=parent_run_id,
                now=now,
            )
            self._append_event(
                conn, run_id, "run.started", RunStartedData(binding=binding, started_at=now)
            )
        return self._run_snapshot(conn=None, run_id=run_id)

    def _insert_run(
        self,
        conn: sqlite3.Connection,
        *,
        binding: TeamBinding,
        chat_id: str,
        session_key: str,
        turn_id: str,
        internal_turn_id: str,
        snapshot: TeamRevisionSnapshot,
        parent_run_id: str | None,
        now: str,
        run_id: str | None = None,
    ) -> str:
        """在调用方的事务里插入运行行；``retry_run`` 要和任务复制同事务，所以拆出来。"""
        new_run_id = run_id or new_id("run")
        conn.execute(
            "INSERT INTO team_runs (run_id, workspace_key, session_key, chat_id, turn_id,"
            " internal_turn_id, team_id, team_revision, status, leader_action, last_seq,"
            " revision_snapshot, started_at, finished_at, parent_run_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', 'planning', 0, ?, ?, NULL, ?)",
            (
                new_run_id, self.workspace_key, session_key, chat_id, turn_id, internal_turn_id,
                binding.team_id, binding.team_revision,
                json.dumps(snapshot.to_dict(), ensure_ascii=False), now, parent_run_id,
            ),
        )
        return new_run_id

    def get_run(self, run_id: str) -> TeamRunSnapshot:
        return self._run_snapshot(conn=None, run_id=run_id)

    def run_context(self, run_id: str) -> dict[str, str]:
        """运行归属 ``(sessionKey, chatId)``：快照里不带会话键，控制动作却要按会话门禁。"""
        rows = self._read(
            "SELECT session_key, chat_id FROM team_runs WHERE run_id = ? AND workspace_key = ?",
            [run_id, self.workspace_key],
        )
        if not rows:
            raise RunNotFound(run_id)
        return {"sessionKey": str(rows[0]["session_key"]), "chatId": str(rows[0]["chat_id"])}

    def list_runs(
        self, chat_id: str, *, limit: int = DEFAULT_LIST_LIMIT, cursor: str | None = None
    ) -> dict[str, Any]:
        """某次聊天的运行清单，按开始时间倒序分页（契约 §5 的 ``nextCursor``）。"""
        resolved_limit = self._resolve_limit(limit)
        after = self._decode_cursor(cursor)
        sql = "SELECT run_id, started_at FROM team_runs WHERE workspace_key = ? AND chat_id = ?"
        params: list[Any] = [self.workspace_key, chat_id]
        if after is not None:
            sql += " AND (started_at < ? OR (started_at = ? AND run_id < ?))"
            params += [after[0], after[0], after[1]]
        sql += " ORDER BY started_at DESC, run_id DESC LIMIT ?"
        params.append(resolved_limit + 1)
        rows = self._read(sql, params)
        page = rows[:resolved_limit]
        return {
            "runs": [self._run_snapshot(conn=None, run_id=row["run_id"]) for row in page],
            "nextCursor": (
                self._encode_cursor(page[-1]["started_at"], page[-1]["run_id"])
                if len(rows) > resolved_limit
                else None
            ),
        }

    def set_leader_action(self, run_id: str, leader_action: str) -> TeamRunSnapshot:
        """Leader 阶段变化（planning/waiting/reviewing/summarizing/idle）单独成事件。"""
        with self.db.connect(create=True) as conn:
            if self._read_run_row(conn, run_id) is None:
                raise RunNotFound(run_id)
            conn.execute(
                "UPDATE team_runs SET leader_action = ? WHERE run_id = ?", (leader_action, run_id)
            )
            self._append_event(
                conn, run_id, "leader.updated", LeaderUpdatedData(leader_action=leader_action)
            )
        return self._run_snapshot(conn=None, run_id=run_id)

    def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        finished_at: str | None = None,
        emit_terminal_event: bool = True,
    ) -> TeamRunSnapshot:
        """运行状态转移；``cancelling`` 与终态各自追加对应事件。"""
        if status not in RUN_TERMINAL_STATUSES and status not in ("running", "cancelling"):
            raise TeamValidationError(f"unknown run status: {status!r}")
        terminal = status in RUN_TERMINAL_STATUSES
        now = finished_at or utc_now_iso()
        with self.db.connect(create=True) as conn:
            if self._read_run_row(conn, run_id) is None:
                raise RunNotFound(run_id)
            conn.execute(
                "UPDATE team_runs SET status = ?, finished_at = ? WHERE run_id = ?",
                (status, now if terminal else None, run_id),
            )
            if status == "cancelling":
                self._append_event(conn, run_id, "run.cancelling", RunCancellingData(status="cancelling"))
            elif terminal and emit_terminal_event:
                snapshot = self._run_snapshot(conn=conn, run_id=run_id)
                self._append_event(conn, run_id, "run.finished", RunFinishedData(run=snapshot))
        return self._run_snapshot(conn=None, run_id=run_id)

    def mark_interrupted_runs(self) -> list[str]:
        """启动清理：把本机遗留的未完成运行标为 interrupted（契约 §6）。

        只应由持有 writer 锁的服务调用；不重放任何模型或外部工具调用。
        """
        self._require_writer("mark_interrupted_runs")
        now = datetime.now(timezone.utc)
        stamp = now.isoformat().replace("+00:00", "Z")
        with self.db.connect(create=True) as conn:
            rows = conn.execute(
                "SELECT run_id FROM team_runs WHERE workspace_key = ?"
                " AND status IN ('running', 'cancelling')",
                (self.workspace_key,),
            ).fetchall()
            affected = [str(row["run_id"]) for row in rows]
            for run_id in affected:
                conn.execute(
                    "UPDATE team_tasks SET status = 'interrupted', finished_at = ?"
                    " WHERE run_id = ? AND status IN ('pending', 'queued', 'running', 'cancelling')",
                    (stamp, run_id),
                )
                conn.execute(
                    "UPDATE team_attempts SET finished_at = ?, outcome = 'interrupted'"
                    " WHERE run_id = ? AND finished_at IS NULL",
                    (stamp, run_id),
                )
                conn.execute(
                    "UPDATE team_runs SET status = 'interrupted', finished_at = ? WHERE run_id = ?",
                    (stamp, run_id),
                )
                self._append_event(
                    conn,
                    run_id,
                    "run.finished",
                    RunFinishedData(run=self._run_snapshot(conn=conn, run_id=run_id)),
                )
        return affected

    def retry_run(
        self,
        run_id: str,
        *,
        request_id: str,
        chat_id: str,
        session_key: str,
        turn_id: str,
        internal_turn_id: str,
    ) -> dict[str, Any]:
        """将失败/中断任务及其下游复制到新运行（契约 §5 ``teamRuns.retry``）。

        三条硬口径：

        1. **沿用原冻结修订** —— 重跑期间就算有人改了团队也不能换打法，
           所以快照直接取 ``team_runs.revision_snapshot``，不重新读定义。
        2. **成功任务不重跑** —— 它的结论作为只读前置引用折进下游指令。
        3. **requestId 重放给同一份回执** —— 重连重放不能再开一条运行。

        新运行与复制出来的任务在同一事务里落盘，所以下游调度器接手时看到的一定是
        完整计划，不存在「运行建了但任务丢了一半」。
        """
        op = f"team.run.retry:{run_id}"
        replay = self._replay(request_id, op, {"runId": run_id})
        if replay is not None:
            # 回执里标一下：调用方据此决定「不要再起一次执行」。
            return {**replay, "replayed": True}
        with self.db.connect(create=True) as conn:
            row = self._read_run_row(conn, run_id)
            if row is None:
                raise RunNotFound(run_id)
            if str(row["status"]) not in RUN_TERMINAL_STATUSES:
                raise TeamValidationError(
                    f"run {run_id} is {row['status']}; retry requires a terminal run"
                )
            tasks = conn.execute(
                "SELECT * FROM team_tasks WHERE run_id = ? ORDER BY rowid ASC", (run_id,)
            ).fetchall()
            selected = _retry_selection(tasks)
            if not selected:
                raise TeamValidationError(f"run {run_id} has no failed or interrupted task")
            revision = TeamRevisionSnapshot.from_dict(
                self._loads(row["revision_snapshot"], "team revision snapshot")
            )
            members = {item.member_id: item for item in revision.definition.members}
            for task_row in selected:
                member = members.get(str(task_row["member_id"]))
                if member is None or member.agent_id != task_row["agent_id"]:
                    # 先查后写：成员对不上时一张表都不碰，回执也不会留下半个新运行。
                    raise TeamValidationError(
                        f"revision {row['team_id']}@{row['team_revision']} no longer has member "
                        f"{task_row['member_id']!r}; retry refused"
                    )
            binding = TeamBinding(
                team_id=str(row["team_id"]),
                team_revision=int(row["team_revision"]),
                team_name=revision.definition.name,
            )
            now = utc_now_iso()
            new_run_id = self._insert_run(
                conn,
                binding=binding,
                chat_id=chat_id,
                session_key=session_key,
                turn_id=turn_id,
                internal_turn_id=internal_turn_id,
                snapshot=revision,
                parent_run_id=run_id,
                now=now,
            )
            self._append_event(
                conn, new_run_id, "run.started", RunStartedData(binding=binding, started_at=now)
            )
            id_map: dict[str, str] = {}
            by_id = {str(task_row["task_id"]): task_row for task_row in tasks}
            rerun_ids = {str(task_row["task_id"]) for task_row in selected}
            copied_ids: list[str] = []
            for task_row in selected:
                dependencies = [str(item) for item in json.loads(task_row["depends_on"] or "[]")]
                copied, _, _ = self._insert_task(
                    conn,
                    run_id=new_run_id,
                    member=members[str(task_row["member_id"])],
                    title=str(task_row["title"]),
                    acceptance=str(task_row["acceptance"]),
                    instruction=_retry_instruction(task_row, by_id, rerun_ids, dependencies),
                    status="pending",
                    depends_on=[id_map[item] for item in dependencies if item in id_map],
                    resource_keys=[str(x) for x in json.loads(task_row["resource_keys"] or "[]")],
                )
                id_map[str(task_row["task_id"])] = copied
                copied_ids.append(copied)
                self._append_event(
                    conn, new_run_id, "task.created",
                    TaskEventData(task=self._task_snapshot(conn, copied)), task_id=copied,
                )
            response: dict[str, Any] = {
                "runId": run_id,
                "newRunId": new_run_id,
                "turnId": turn_id,
                "taskIds": copied_ids,
            }
            self._record_request(conn, request_id, op, {"runId": run_id}, response)
        return response

    # ------------------------------------------------------------------- 任务

    def create_task(
        self,
        *,
        run_id: str,
        member: TeamMember,
        title: str,
        acceptance: str,
        instruction: str,
        status: str = "pending",
        depends_on: Sequence[str] = (),
        resource_keys: Sequence[str] = (),
        batch_id: str | None = None,
        last_action: str | None = None,
    ) -> TeamTaskSnapshot:
        """先建任务再执行（契约 §3.1「先创建任务再执行」），状态事件同事务。"""
        if status not in TASK_EVENT_BY_STATUS:
            raise TeamValidationError(f"unknown initial task status: {status!r}")
        with self.db.connect(create=True) as conn:
            if self._read_run_row(conn, run_id) is None:
                raise RunNotFound(run_id)
            task_id, attempt_id, _now = self._insert_task(
                conn,
                run_id=run_id,
                member=member,
                title=title,
                acceptance=acceptance,
                instruction=instruction,
                status=status,
                depends_on=depends_on,
                resource_keys=resource_keys,
                batch_id=batch_id,
                last_action=last_action,
            )
            snapshot = self._task_snapshot(conn, task_id)
            self._append_event(
                conn, run_id, TASK_EVENT_BY_STATUS[status], TaskEventData(task=snapshot),
                task_id=task_id, attempt_id=attempt_id,
            )
        return snapshot

    def _insert_task(
        self,
        conn: sqlite3.Connection,
        *,
        run_id: str,
        member: TeamMember,
        title: str,
        acceptance: str,
        instruction: str,
        status: str,
        depends_on: Sequence[str] = (),
        resource_keys: Sequence[str] = (),
        batch_id: str | None = None,
        last_action: str | None = None,
    ) -> tuple[str, str | None, str]:
        """在调用方的事务里插入任务（含 running 时的尝试行）。"""
        now = utc_now_iso()
        task_id = new_id("task")
        attempt_id = new_id("att") if status == "running" else None
        conn.execute(
            "INSERT INTO team_tasks (task_id, workspace_key, run_id, batch_id, member_id,"
            " agent_id, title, acceptance, instruction, depends_on, resource_keys, status,"
            " attempt_id, last_action, started_at, finished_at, result)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            (
                task_id, self.workspace_key, run_id, batch_id, member.member_id,
                member.agent_id, title, acceptance, instruction,
                json.dumps(list(depends_on)), json.dumps(list(resource_keys)), status,
                attempt_id, last_action, now if status == "running" else None,
            ),
        )
        if attempt_id is not None:
            conn.execute(
                "INSERT INTO team_attempts (attempt_id, workspace_key, task_id, run_id,"
                " outcome, stop_reason, error_code, usage, started_at, finished_at)"
                " VALUES (?, ?, ?, ?, '', '', NULL, NULL, ?, NULL)",
                (attempt_id, self.workspace_key, task_id, run_id, now),
            )
        return task_id, attempt_id, now

    def list_pending_tasks(self, run_id: str) -> list[dict[str, Any]]:
        """把已落盘的 ``pending`` 任务读成调度计划片段（重试接手用）。

        :class:`TeamTaskSnapshot` 故意不含 ``instruction`` / ``resourceKeys``（事件里
        不该带完整工具参数），所以这里直接读行，不绕快照。
        """
        self._require_run_scope(run_id)
        return [
            {
                "taskId": str(row["task_id"]),
                "memberId": str(row["member_id"]),
                "title": str(row["title"]),
                "acceptance": str(row["acceptance"]),
                "instruction": str(row["instruction"]),
                "dependsOn": [str(item) for item in json.loads(row["depends_on"] or "[]")],
                "resourceKeys": [str(item) for item in json.loads(row["resource_keys"] or "[]")],
            }
            for row in self._read(
                "SELECT * FROM team_tasks WHERE run_id = ? AND status = 'pending'"
                " ORDER BY rowid ASC",
                [run_id],
            )
        ]

    def get_task(self, task_id: str) -> TeamTaskSnapshot:
        try:
            with self.db.connect() as conn:
                return self._task_snapshot(conn, task_id)
        except DatabaseAbsent:
            raise TaskNotFound(task_id) from None

    def get_task_full_result(self, task_id: str) -> str | None:
        """读回成员**完整产出**（``result.fullResult``），供写作域取长稿全文。

        团队任务行的 ``result`` 列是 ``{"result": …, "fullResult": …}``：``result.summary``
        按契约封顶 8000 字符，超过的部分只在 ``fullResult`` 里。通用下游摘要协议仍只读
        ``summary``（``TeamTaskSnapshot`` 的线格式没有这个字段），因此这里单开一个
        只读入口而不是往领域快照上加字段。
        """
        rows = self._read(
            "SELECT result FROM team_tasks WHERE task_id = ? AND workspace_key = ?",
            [task_id, self.workspace_key],
        )
        if not rows:
            raise TaskNotFound(task_id)
        raw = rows[0]["result"]
        if not raw:
            return None
        stored = self._loads(str(raw), "task result payload")
        full = stored.get("fullResult")
        return str(full) if isinstance(full, str) and full else None

    def update_task_status(
        self, task_id: str, status: str, *, last_action: str | None = None
    ) -> TeamTaskSnapshot:
        """非终态之间的推进（queued/running/cancelling/blocked）。"""
        if status not in TASK_EVENT_BY_STATUS or status in TASK_TERMINAL_STATUSES:
            raise TeamValidationError(f"update_task_status needs an open status, got {status!r}")
        now = utc_now_iso()
        with self.db.connect(create=True) as conn:
            row = self._read_task_row(conn, task_id)
            if row is None:
                raise TaskNotFound(task_id)
            if row["status"] in TASK_TERMINAL_STATUSES:
                raise IllegalTaskTransition(f"task {task_id} already terminal: {row['status']}")
            run_id = str(row["run_id"])
            conn.execute(
                "UPDATE team_tasks SET status = ?, last_action = ?,"
                " started_at = COALESCE(started_at, ?) WHERE task_id = ?",
                (status, last_action, now if status == "running" else None, task_id),
            )
            snapshot = self._task_snapshot(conn, task_id)
            self._append_event(
                conn, run_id, TASK_EVENT_BY_STATUS[status], TaskEventData(task=snapshot),
                task_id=task_id, attempt_id=snapshot.attempt_id,
            )
        return snapshot

    def record_activity(self, task_id: str, *, last_action: str) -> TeamTaskSnapshot | None:
        """更新「最近公开动作」并追加一条 ``task.activity``；终态任务不再产生活动事件。

        每 run 的活动事件有保留上限（契约 §7）：超阈值后只更新快照、停止追加中间活动，
        生命周期与终态事件不受该阈值影响。返回 ``None`` 表示任务已终态，什么都没写。
        """
        with self.db.connect(create=True) as conn:
            row = self._read_task_row(conn, task_id)
            if row is None:
                raise TaskNotFound(task_id)
            if row["status"] in TASK_TERMINAL_STATUSES:
                return None
            run_id = str(row["run_id"])
            conn.execute(
                "UPDATE team_tasks SET last_action = ? WHERE task_id = ?",
                (last_action, task_id),
            )
            snapshot = self._task_snapshot(conn, task_id)
            if self._activity_count(conn, run_id) >= MAX_ACTIVITY_EVENTS_PER_RUN:
                return snapshot
            self._append_event(
                conn, run_id, "task.activity", TaskEventData(task=snapshot),
                task_id=task_id, attempt_id=snapshot.attempt_id,
            )
        return snapshot

    @staticmethod
    def _activity_count(conn: sqlite3.Connection, run_id: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM team_events WHERE run_id = ? AND type = 'task.activity'",
            (run_id,),
        ).fetchone()
        return int(row["total"]) if row is not None else 0

    def start_task(self, task_id: str, *, last_action: str | None = None) -> TeamTaskSnapshot:
        """排队 → 运行：同事务建 attempt 并落 ``task.started``。

        attemptId 必须在子执行开始**之前**就存在，它同时是共享 Runner 的 executionId
        （事件流、任务表与这次执行要对齐同一个 id），所以不能在 ``finish_task`` 里补。
        """
        now = utc_now_iso()
        with self.db.connect(create=True) as conn:
            row = self._read_task_row(conn, task_id)
            if row is None:
                raise TaskNotFound(task_id)
            if row["status"] in TASK_TERMINAL_STATUSES:
                raise IllegalTaskTransition(f"task {task_id} already terminal: {row['status']}")
            if row["status"] == "running" and row["attempt_id"]:
                return self._task_snapshot(conn, task_id)
            run_id = str(row["run_id"])
            attempt_id = str(row["attempt_id"]) if row["attempt_id"] else new_id("att")
            if not row["attempt_id"]:
                conn.execute(
                    "INSERT INTO team_attempts (attempt_id, workspace_key, task_id, run_id,"
                    " outcome, stop_reason, error_code, usage, started_at, finished_at)"
                    " VALUES (?, ?, ?, ?, '', '', NULL, NULL, ?, NULL)",
                    (attempt_id, self.workspace_key, task_id, run_id, now),
                )
            conn.execute(
                "UPDATE team_tasks SET status = 'running', attempt_id = ?, last_action = ?,"
                " started_at = COALESCE(started_at, ?) WHERE task_id = ?",
                (attempt_id, last_action, now, task_id),
            )
            snapshot = self._task_snapshot(conn, task_id)
            self._append_event(
                conn, run_id, "task.started", TaskEventData(task=snapshot),
                task_id=task_id, attempt_id=attempt_id,
            )
        return snapshot

    def finish_task(
        self,
        task_id: str,
        *,
        status: str,
        summary: str,
        artifacts: Sequence[str] = (),
        error_code: str | None = None,
        usage: TeamTaskUsage | None = None,
        stop_reason: str = "completed",
        full_result: str | None = None,
    ) -> TeamTaskResult:
        """写终态：任务状态 + 结构化结果 + attempt 收口 + ``task.finished`` 同一事务。"""
        if status not in TASK_TERMINAL_STATUSES:
            raise TeamValidationError(f"finish_task requires a terminal status, got {status!r}")
        now = utc_now_iso()
        with self.db.connect(create=True) as conn:
            row = self._read_task_row(conn, task_id)
            if row is None:
                raise TaskNotFound(task_id)
            if row["status"] in TASK_TERMINAL_STATUSES:
                raise IllegalTaskTransition(f"task {task_id} already terminal: {row['status']}")
            if row["status"] == "cancelling" and status in _LATE_SUCCESS_STATUSES:
                # 契约 §6：取消链已经落过 ``cancelling``，晚到的成功结果一律拒绝——
                # 否则「作者已停止的任务」会在 UI 上翻回成功，取消代数也拦不住它。
                raise IllegalTaskTransition(
                    f"task {task_id} is cancelling; late {status!r} result refused"
                )
            run_id = str(row["run_id"])
            attempt_id = str(row["attempt_id"]) if row["attempt_id"] else new_id("att")
            if row["attempt_id"] is None:
                conn.execute(
                    "INSERT INTO team_attempts (attempt_id, workspace_key, task_id, run_id,"
                    " outcome, stop_reason, error_code, usage, started_at, finished_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        attempt_id, self.workspace_key, task_id, run_id, status, stop_reason,
                        error_code, _usage_json(usage), now, now,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE team_attempts SET outcome = ?, stop_reason = ?, error_code = ?,"
                    " usage = ?, finished_at = ? WHERE attempt_id = ?",
                    (status, stop_reason, error_code, _usage_json(usage), now, attempt_id),
                )
            result = TeamTaskResult(
                task_id=task_id,
                attempt_id=attempt_id,
                member_id=str(row["member_id"]),
                agent_id=str(row["agent_id"]),
                status=status,  # pyright: ignore[reportArgumentType] 已用 TASK_TERMINAL_STATUSES 收紧
                stop_reason=stop_reason,
                summary=summary,
                artifacts=list(artifacts),
                error_code=error_code,
                usage=usage,
            )
            stored = {"result": result.to_dict(), "fullResult": full_result}
            conn.execute(
                "UPDATE team_tasks SET status = ?, attempt_id = ?, finished_at = ?, result = ?"
                " WHERE task_id = ?",
                (status, attempt_id, now, json.dumps(stored, ensure_ascii=False), task_id),
            )
            snapshot = self._task_snapshot(conn, task_id)
            self._append_event(
                conn, run_id, "task.finished", TaskEventData(task=snapshot),
                task_id=task_id, attempt_id=attempt_id,
            )
        return result

    # ------------------------------------------------------------------- 事件

    def append_run_event(
        self,
        run_id: str,
        *,
        type: TeamEventType | str,
        task_id: str | None = None,
        attempt_id: str | None = None,
        data: RunStartedData | LeaderUpdatedData | TaskEventData | RunCancellingData | RunFinishedData | None = None,
    ) -> TeamEvent:
        """公开追加入口；``task.*`` 未给 data 时用任务当前快照补齐。"""
        event_type = str(type)
        if event_type not in TEAM_EVENT_TYPES:
            raise TeamValidationError(f"unknown team event type: {event_type!r}")
        with self.db.connect(create=True) as conn:
            if self._read_run_row(conn, run_id) is None:
                raise RunNotFound(run_id)
            payload = data
            if event_type.startswith("task."):
                if payload is None:
                    if task_id is None:
                        raise TeamValidationError(f"event {event_type} requires task_id or data")
                    payload = TaskEventData(task=self._task_snapshot(conn, task_id))
                return self._append_event(
                    conn, run_id, event_type, payload, task_id=task_id, attempt_id=attempt_id
                )
            if payload is None:
                raise TeamValidationError(f"event {event_type} requires data")
            return self._append_event(conn, run_id, event_type, payload, task_id=task_id)

    def list_events(
        self, run_id: str, *, after_seq: int = 0, limit: int = DEFAULT_EVENT_LIMIT
    ) -> list[TeamEvent]:
        self._validate_event_limit(limit)
        self._require_run_scope(run_id)
        return self._events_after(run_id, after_seq=after_seq, limit=limit)

    def _events_after(
        self, run_id: str, *, after_seq: int, limit: int
    ) -> list[TeamEvent]:
        """裸读事件页；调用方必须已经确认 run 在本工作空间内。"""
        events: list[TeamEvent] = []
        for row in self._read(
            "SELECT event FROM team_events WHERE run_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
            [run_id, int(after_seq), int(limit)],
        ):
            events.append(TeamEvent.from_dict(self._loads(row["event"], "team event")))
        return events

    @staticmethod
    def _validate_event_limit(limit: int) -> None:
        if isinstance(limit, bool) or not 1 <= limit <= MAX_EVENT_LIMIT:
            raise TeamValidationError(f"event limit must be between 1 and {MAX_EVENT_LIMIT}")

    def events_page(
        self, run_id: str, *, after_seq: int = 0, limit: int = DEFAULT_EVENT_LIMIT
    ) -> dict[str, Any]:
        """带 lastSeq/hasMore/resetRequired 的补拉页（契约 §6 的客户端恢复协议）。"""
        self._validate_event_limit(limit)
        run = self.get_run(run_id)
        events = self._events_after(run_id, after_seq=after_seq, limit=limit)
        highest = events[-1].seq if events else int(after_seq)
        # 活动增量被剪掉后 seq 会断号：这时必须让客户端回快照，否则它会安静地漏掉中间
        # 过程。``afterSeq<0`` 是客户端自己声明的「我全丢了」。
        expected = max(int(after_seq), 0) + 1
        gap = int(after_seq) < 0
        for event in events:
            if event.seq != expected:
                gap = True
            expected = event.seq + 1
        return {
            "events": [event.to_dict() for event in events],
            "lastSeq": run.last_seq,
            "hasMore": run.last_seq > highest,
            "resetRequired": gap,
        }

    def prune_activity_events(
        self, *, retain: int = MAX_ACTIVITY_EVENTS_PER_RUN, older_than_days: int = ACTIVITY_RETENTION_DAYS
    ) -> list[str]:
        """剪掉超阈值/超期的中间活动，让客户端回快照；终态事件永不清理（契约 §7）。"""
        self._require_writer("prune_activity_events")
        cutoff = _days_ago(datetime.now(timezone.utc), older_than_days)
        # team_events 没有 workspace_key 列，清理范围必须回到 team_runs 上取，
        # 否则共用库文件时会删掉别的工作空间的活动事件。
        own_runs = "run_id IN (SELECT run_id FROM team_runs WHERE workspace_key = ?)"
        with self.db.connect(create=True) as conn:
            rows = conn.execute(
                f"SELECT run_id FROM team_events WHERE type = 'task.activity' AND {own_runs}"
                " GROUP BY run_id HAVING COUNT(*) > ?",
                (self.workspace_key, retain),
            ).fetchall()
            affected = {str(row["run_id"]) for row in rows}
            for run_id in sorted(affected):
                conn.execute(
                    "DELETE FROM team_events WHERE run_id = ? AND type = 'task.activity'"
                    " AND seq NOT IN (SELECT seq FROM team_events WHERE run_id = ?"
                    " AND type = 'task.activity' ORDER BY seq DESC LIMIT ?)",
                    (run_id, run_id, retain),
                )
            expired = conn.execute(
                f"SELECT DISTINCT run_id FROM team_events WHERE type = 'task.activity' AND {own_runs}"
                " AND occurred_at < ?",
                (self.workspace_key, cutoff),
            ).fetchall()
            affected.update(str(row["run_id"]) for row in expired)
            conn.execute(
                f"DELETE FROM team_events WHERE type = 'task.activity' AND {own_runs}"
                " AND occurred_at < ?",
                (self.workspace_key, cutoff),
            )
        return sorted(affected)

    def _append_event(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        type: str,
        data: Any,
        *,
        task_id: str | None = None,
        attempt_id: str | None = None,
    ) -> TeamEvent:
        """seq 分配与事件写入在同一连接事务内；``last_seq`` 是唯一递增来源。"""
        row = self._read_run_row(conn, run_id)
        if row is None:
            raise RunNotFound(run_id)
        seq = int(row["last_seq"]) + 1
        event = TeamEvent.from_dict(
            {
                "schemaVersion": 1,
                "runId": run_id,
                "chatId": row["chat_id"],
                "turnId": row["turn_id"],
                "seq": seq,
                "type": type,
                "taskId": task_id,
                "attemptId": attempt_id,
                "occurredAt": utc_now_iso(),
                "data": data.to_dict() if hasattr(data, "to_dict") else dict(data),
            }
        )
        conn.execute(
            "INSERT INTO team_events (run_id, seq, type, task_id, attempt_id, occurred_at, event)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id, seq, type, task_id, attempt_id, event.occurred_at,
                json.dumps(event.to_dict(), ensure_ascii=False),
            ),
        )
        conn.execute("UPDATE team_runs SET last_seq = ? WHERE run_id = ?", (seq, run_id))
        return event

    # ------------------------------------------------------------------- 派单

    def record_dispatch(
        self,
        *,
        run_id: str,
        batch_id: str,
        payload: Mapping[str, Any],
        task_ids: Sequence[str],
        result: Mapping[str, Any] | None = None,
    ) -> DispatchOutcome:
        """batchId 幂等：同 key 同载荷返回同一组任务，不同载荷冲突（契约 §2）。"""
        # 作用域守卫跑在幂等预检之前：否则 DispatchKeyConflict 会泄露「别的 workspace 里
        # 确实有这个 batch」。
        self._require_run_scope(run_id)
        digest = _payload_key(payload)
        rows = self._read(
            "SELECT payload_hash, task_ids FROM team_dispatches WHERE run_id = ? AND batch_id = ?",
            [run_id, batch_id],
        )
        if rows:
            if rows[0]["payload_hash"] != digest:
                raise DispatchKeyConflict(f"DISPATCH_KEY_CONFLICT run_id={run_id} batch_id={batch_id}")
            return DispatchOutcome(
                run_id=run_id, batch_id=batch_id, task_ids=json.loads(rows[0]["task_ids"]), replayed=True
            )
        with self.db.connect(create=True) as conn:
            if self._read_run_row(conn, run_id) is None:
                raise RunNotFound(run_id)
            conn.execute(
                "INSERT INTO team_dispatches (run_id, batch_id, payload_hash, task_ids, result,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_id, batch_id, digest, json.dumps(list(task_ids)),
                    json.dumps(dict(result or {}), ensure_ascii=False), utc_now_iso(),
                ),
            )
        return DispatchOutcome(run_id=run_id, batch_id=batch_id, task_ids=list(task_ids), replayed=False)

    def task_count(self, run_id: str) -> int:
        """这条运行已经落库多少任务（每次运行任务数预算按**运行**累计，不是按批）。"""
        rows = self._read(
            "SELECT COUNT(*) AS total FROM team_tasks WHERE run_id = ? AND run_id IN "
            "(SELECT run_id FROM team_runs WHERE workspace_key = ?)",
            [run_id, self.workspace_key],
        )
        return int(rows[0]["total"]) if rows else 0

    def get_dispatch(self, run_id: str, batch_id: str) -> dict[str, Any] | None:
        if not self._run_in_scope(run_id):
            return None
        rows = self._read(
            "SELECT payload_hash, task_ids, result FROM team_dispatches WHERE run_id = ? AND batch_id = ?",
            [run_id, batch_id],
        )
        if not rows:
            return None
        return {
            "payloadHash": rows[0]["payload_hash"],
            "taskIds": json.loads(rows[0]["task_ids"]),
            "result": json.loads(rows[0]["result"] or "{}"),
        }

    # --------------------------------------------------------------- 幂等键

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        rows = self._read(
            "SELECT op, payload_hash, response FROM team_requests"
            " WHERE workspace_key = ? AND request_id = ?",
            [self.workspace_key, request_id],
        )
        if not rows:
            return None
        return {
            "op": rows[0]["op"],
            "payloadHash": rows[0]["payload_hash"],
            "response": self._loads(rows[0]["response"], "idempotent response"),
        }

    def _replay(self, request_id: str, op: str, payload: object) -> dict[str, Any] | None:
        record = self.get_request(request_id)
        if record is None:
            return None
        if record["op"] != op or record["payloadHash"] != _payload_key(payload):
            raise RequestReplayConflict(f"request_id {request_id!r} was used for a different payload")
        return record["response"]

    def _record_request(
        self,
        conn: sqlite3.Connection,
        request_id: str,
        op: str,
        payload: object,
        response: Mapping[str, Any],
    ) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO team_requests (workspace_key, request_id, op, payload_hash,"
            " response, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                self.workspace_key, request_id, op, _payload_key(payload),
                json.dumps(dict(response), ensure_ascii=False), utc_now_iso(),
            ),
        )

    # ------------------------------------------------------------- 内部读取

    def _read(self, sql: str, params: Sequence[Any]) -> list[sqlite3.Row]:
        try:
            with self.db.connect() as conn:
                return list(conn.execute(sql, tuple(params)).fetchall())
        except DatabaseAbsent:
            return []

    def _read_team_row(self, conn: sqlite3.Connection, team_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM teams WHERE workspace_key = ? AND team_id = ?",
            (self.workspace_key, team_id),
        ).fetchone()

    def _read_run_row(self, conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM team_runs WHERE run_id = ? AND workspace_key = ?",
            (run_id, self.workspace_key),
        ).fetchone()

    def _read_task_row(self, conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM team_tasks WHERE task_id = ? AND workspace_key = ?",
            (task_id, self.workspace_key),
        ).fetchone()

    def _run_in_scope(self, run_id: str) -> bool:
        """run 是否属于本工作空间。

        ``team_events`` / ``team_dispatches`` 没有 ``workspace_key`` 列，直接按 runId 查
        就会跨工作空间命中；凡是读这两张表的入口都必须先过这道守卫。
        """
        return bool(
            self._read(
                "SELECT run_id FROM team_runs WHERE run_id = ? AND workspace_key = ?",
                [run_id, self.workspace_key],
            )
        )

    def _require_run_scope(self, run_id: str) -> None:
        if not self._run_in_scope(run_id):
            raise RunNotFound(run_id)

    def _require_writer(self, action: str) -> None:
        if not self.writer_held:
            raise StorageUnavailable(f"team writer is not held; {action} needs the scheduler writer")

    def _team_row(self, team_id: str) -> sqlite3.Row:
        rows = self._read(
            "SELECT * FROM teams WHERE workspace_key = ? AND team_id = ?",
            [self.workspace_key, team_id],
        )
        if not rows:
            raise TeamNotFound(team_id)
        return rows[0]

    def _run_snapshot(self, *, conn: sqlite3.Connection | None, run_id: str) -> TeamRunSnapshot:
        """构建运行快照：status/lastSeq 取列，tasks 取 team_tasks（快照不再是任务真值）。

        ``lastSeq`` 与 tasks 必须来自**同一个读事务**（契约 §6）：分开两次读会拿到
        「旧 seq + 新任务」这种自相矛盾的快照，前端一归并就会误判缺口。
        """
        if conn is None:
            try:
                with self.db.connect() as own:
                    return self._run_snapshot(conn=own, run_id=run_id)
            except DatabaseAbsent:
                # 从没建过团队库 = 没有这条运行，与查无此运行同一种对外回答。
                raise RunNotFound(run_id) from None
        found = self._read_run_row(conn, run_id)
        if found is None:
            raise RunNotFound(run_id)
        row = found
        task_rows = conn.execute(
            "SELECT * FROM team_tasks WHERE run_id = ? ORDER BY rowid ASC", (run_id,)
        ).fetchall()
        revision = self._loads(row["revision_snapshot"], "team revision snapshot")
        tasks = [self._task_snapshot_from_row(task) for task in task_rows]
        return TeamRunSnapshot.from_dict(
            {
                "runId": row["run_id"],
                "chatId": row["chat_id"],
                "turnId": row["turn_id"],
                "binding": {
                    "teamId": row["team_id"],
                    "teamRevision": int(row["team_revision"]),
                    "teamName": revision["definition"]["name"],
                },
                "status": row["status"],
                "lastSeq": int(row["last_seq"]),
                "tasks": [task.to_dict() for task in tasks],
                "leaderAction": row["leader_action"],
                "startedAt": row["started_at"],
                "finishedAt": row["finished_at"],
                "parentRunId": row["parent_run_id"],
            }
        )

    def _task_snapshot(self, conn: sqlite3.Connection, task_id: str) -> TeamTaskSnapshot:
        row = self._read_task_row(conn, task_id)
        if row is None:
            raise TaskNotFound(task_id)
        return self._task_snapshot_from_row(row)

    def _task_snapshot_from_row(self, row: sqlite3.Row) -> TeamTaskSnapshot:
        stored = self._loads(row["result"], "task result payload") if row["result"] else None
        return TeamTaskSnapshot.from_dict(
            {
                "taskId": row["task_id"],
                "memberId": row["member_id"],
                "agentId": row["agent_id"],
                "title": row["title"],
                "acceptance": row["acceptance"],
                "dependsOn": json.loads(row["depends_on"]),
                "status": row["status"],
                "attemptId": row["attempt_id"],
                "lastAction": row["last_action"],
                "startedAt": row["started_at"],
                "finishedAt": row["finished_at"],
                "result": stored["result"] if stored else None,
            }
        )

    def _snapshot_from_json(
        self, conn: sqlite3.Connection, team_id: str, revision: int
    ) -> TeamRevisionSnapshot:
        row = conn.execute(
            "SELECT snapshot FROM team_revisions WHERE workspace_key = ? AND team_id = ?"
            " AND revision = ?",
            (self.workspace_key, team_id, int(revision)),
        ).fetchone()
        if row is None:
            raise TeamNotFound(f"{team_id}@{revision}")
        return TeamRevisionSnapshot.from_dict(self._loads(row["snapshot"], "team revision snapshot"))

    def _definition_from_row(self, row: sqlite3.Row | Mapping[str, Any]) -> TeamDefinition:
        return TeamDefinition.from_dict(self._loads(row["payload"], "team definition"))

    def _loads(self, raw: str, name: str) -> dict[str, Any]:
        """落盘 JSON 损坏必须显式失败：静默默认会让半个团队凭空消失。"""
        try:
            decoded: object = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise StorageUnavailable(f"stored {name} is not valid JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise StorageUnavailable(f"stored {name} must be a JSON object")
        return cast("dict[str, Any]", decoded)

    def _require_coherent(self, definition: TeamDefinition, snapshot: TeamRevisionSnapshot) -> None:
        """校验先于写入：definition 与冻结快照不一致时一张表都不碰。"""
        if snapshot.definition.to_dict() != definition.to_dict():
            raise TeamValidationError(
                f"team revision snapshot does not match definition {definition.id!r}"
            )

    def _current_revision(self, team_id: str) -> int:
        try:
            return self.get_team(team_id).revision
        except TeamNotFound:
            return 0

    def _resolve_limit(self, limit: int) -> int:
        if isinstance(limit, bool) or not MIN_LIST_LIMIT <= limit <= MAX_LIST_LIMIT:
            raise TeamValidationError(
                f"team limit must be between {MIN_LIST_LIMIT} and {MAX_LIST_LIMIT}, got {limit}"
            )
        return limit

    @staticmethod
    def _decode_cursor(cursor: str | None) -> tuple[str, str] | None:
        if not cursor:
            return None
        parts = cursor.split("|", 1)
        if len(parts) != 2 or not all(parts):
            raise TeamValidationError(f"team cursor is not a valid page token: {cursor!r}")
        return parts[0], parts[1]

    @staticmethod
    def _encode_cursor(updated_at: str, team_id: str) -> str:
        return f"{updated_at}|{team_id}"


def _retry_selection(tasks: Sequence[sqlite3.Row]) -> list[sqlite3.Row]:
    """重跑集 = 失败/中断任务 ∪ 传递依赖它们的 blocked 下游；返回拓扑序。

    成功的兄弟不在这里——它不重跑，只在 :func:`_retry_instruction` 里变成只读引用。
    """
    by_id = {str(row["task_id"]): row for row in tasks}
    deps_of = {
        str(row["task_id"]): [str(item) for item in json.loads(row["depends_on"] or "[]")]
        for row in tasks
    }
    order_of = {str(row["task_id"]): index for index, row in enumerate(tasks)}
    selected = {
        rid for rid, row in by_id.items() if str(row["status"]) in RETRY_SOURCE_STATUSES
    }
    grew = True
    while grew:
        grew = False
        for rid, row in by_id.items():
            if rid in selected or str(row["status"]) != "blocked":
                continue
            if any(dep in selected for dep in deps_of[rid]):
                selected.add(rid)
                grew = True
    remaining = set(selected)
    ordered: list[sqlite3.Row] = []
    while remaining:
        ready = sorted(
            (rid for rid in remaining if not (set(deps_of[rid]) & remaining)),
            key=lambda rid: order_of[rid],
        )
        if not ready:
            raise TeamValidationError(
                f"team task dependencies form a cycle: {sorted(remaining)}"
            )
        remaining.difference_update(ready)
        ordered.extend(by_id[rid] for rid in ready)
    return ordered


def _retry_instruction(
    task_row: sqlite3.Row,
    by_id: Mapping[str, sqlite3.Row],
    rerun_ids: set[str],
    dependencies: Sequence[str],
) -> str:
    """把「不重跑的前置」折成只读引用拼进新指令（契约 §5 成功前置只读复用）。"""
    instruction = str(task_row["instruction"])
    references = [by_id[dep] for dep in dependencies if dep not in rerun_ids and dep in by_id]
    if not references:
        return instruction
    share = max(200, RETRY_REFERENCE_BUDGET // len(references))
    lines = ["", "—— 前置结果（只读引用，不要重做）——"]
    for reference in references:
        title = str(reference["title"])
        if str(reference["status"]) == "succeeded":
            lines.append(f"[{title}] {_stored_summary(reference)[:share]}")
        else:
            # 前置被取消/阻断时没有结论可给：写清楚，别下游假装拿到了东西。
            lines.append(f"[{title}] 未产出可用结论（状态 {reference['status']}）")
    room = MAX_TASK_INSTRUCTION_CHARS - len(instruction) - 1
    if room <= 0:
        return instruction
    return (instruction + "\n".join(lines))[:MAX_TASK_INSTRUCTION_CHARS]


def _stored_summary(row: sqlite3.Row) -> str:
    """从 ``team_tasks.result`` 里取结论摘要；落盘 JSON 坏了就当没有。"""
    try:
        stored = json.loads(row["result"] or "{}")
    except (TypeError, ValueError):
        return ""
    return _summary_of(stored)


def _summary_of(stored: object) -> str:
    if not isinstance(stored, Mapping):
        return ""
    result = cast("Mapping[str, Any]", stored).get("result")
    if not isinstance(result, Mapping):
        return ""
    summary = cast("Mapping[str, Any]", result).get("summary")
    return str(summary) if isinstance(summary, str) else ""


def _usage_json(usage: TeamTaskUsage | None) -> str | None:
    return json.dumps(usage.to_dict(), ensure_ascii=False) if usage is not None else None
