"""评估域仓储：版本 CAS、快照冻结、幂等入队与原子领取。

契约 §6 的边界在本模块落地：**事务归 repository，连接归 database**。仓储方法要么
完整成功要么完整回滚，不允许把半个写入留给调用方判断。

错误语义一律用**错误码字符串**而不是文案，WU-05 的路由层直接映射到契约 §1 的
HTTP 状态码，不需要回来解析 exception 文案：

===========================  ==========================================
码                          契约状态码
===========================  ==========================================
REVISION_CONFLICT           409
CASE_NOT_FOUND /            409（运行输入不合法，不是资源不存在）
CASE_NOT_IN_SUITE
RUN_NOT_CANCELLABLE /       409
RUN_NOT_TERMINAL /
ATTEMPT_NOT_FINISHABLE
IDEMPOTENCY_MISMATCH        422
IDEMPOTENCY_IN_PROGRESS     409
PATH_OUTSIDE_ROOT           422
===========================  ==========================================
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, cast

from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.schemas import CaseRead, CaseWrite, RunCreate, SuiteRead, SuiteWrite
from nanobot.evaluation.validation import validate_relative_path

RUNS_ENDPOINT = "POST /runs"

#: run 终态：只有这三种允许软删（契约 §3 `DELETE /runs/{id}`）。
TERMINAL_RUN_STATUSES = frozenset({"completed", "cancelled", "interrupted"})
#: attempt 的非终态 execution_status（契约 §4）。
OPEN_EXECUTION_STATUSES = frozenset({"queued", "running"})
CANCELLABLE_RUN_STATUSES = frozenset({"queued", "running"})
#: 契约 §4：terminal_count 同时要求执行与评分都终结。单看 execution 会把
#: 「Agent 已结束、Judge 仍在跑」的 attempt 误算成完成，运行会提前显示 completed。
TERMINAL_EXECUTION_STATUSES = frozenset(
    {"succeeded", "failed", "timed_out", "cancelled", "interrupted", "setup_error"}
)
TERMINAL_GRADING_STATUSES = frozenset({"completed", "judge_error", "not_applicable"})

_TERMINAL_PREDICATE = (
    "execution_status NOT IN ('queued', 'running')"
    " AND grading_status IN ('completed', 'judge_error', 'not_applicable')"
)


class RevisionConflict(ValueError):  # noqa: N818 - 名称由错误码映射锁定
    """base_revision 与服务端当前版本不一致，或用例不属于目标用例集。"""


class StateConflict(ValueError):  # noqa: N818
    """资源当前状态不允许该变更。"""


class IdempotencyMismatch(ValueError):  # noqa: N818
    """同一 key 被用于不同请求体。"""


class IdempotencyInProgress(ValueError):  # noqa: N818
    """同一 key 的前一次入队尚未写回结果。"""


class RecordNotFound(LookupError):  # noqa: N818
    """请求的资源在本库中不存在。"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(raw: str | None, default: Any) -> Any:
    return default if raw is None else json.loads(raw)


#: 导出文件带服务端字段（id / revision / 时间戳 / 软删标记），契约 §3 要求导入时忽略
#: 并重建关联，因此这些键在校验前剔除；剩下的未知键仍由 extra="forbid" 拒绝。
_CASE_SERVER_KEYS = frozenset(
    {"id", "revision", "created_at", "updated_at", "is_deleted"}
)
_SUITE_SERVER_KEYS = frozenset(
    {"id", "revision", "case_count", "created_at", "updated_at", "is_deleted"}
)


def _importable(raw: Mapping[str, Any], server_keys: frozenset[str]) -> dict[str, Any]:
    stripped = {k: v for k, v in raw.items() if k not in server_keys}
    stripped["base_revision"] = None
    return stripped


def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def _group_by_attempt(rows: Sequence[sqlite3.Row]) -> dict[str, list[dict[str, Any]]]:
    """把 reviews / artifacts 行按 attempt_id 分桶，供一次查询服务整份运行读。"""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["attempt_id"], []).append(dict(row))
    return grouped

class EvaluationRepository:
    """评估域唯一写入口；一个服务实例持有一个。"""

    def __init__(self, db: EvaluationDatabase) -> None:
        self._db = db

    @staticmethod
    def request_digest(payload: Mapping[str, Any]) -> str:
        """规范化请求摘要：字段顺序无关，只有内容相同才算同一请求。"""
        return hashlib.sha256(_dump(payload).encode("utf-8")).hexdigest()

    # ----------------------------------------------------------------- 用例集

    def create_suite(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        write = SuiteWrite.model_validate(payload)
        if write.base_revision is not None:
            raise RevisionConflict("REVISION_CONFLICT: new suite must omit base_revision")
        suite_id = _new_id()
        now = _utc_now_iso()
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO suites (id, revision, name, description, tags_json,"
                " is_deleted, created_at, updated_at) VALUES (?, 1, ?, ?, ?, 0, ?, ?)",
                (suite_id, write.name, write.description, _dump(write.tags), now, now),
            )
        return self.get_suite(suite_id)

    def get_suite(self, suite_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT id, revision, name, description, tags_json, is_deleted,"
                " created_at, updated_at FROM suites WHERE id = ?",
                (suite_id,),
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"SUITE_NOT_FOUND {suite_id}")
            case_count = conn.execute(
                "SELECT COUNT(*) FROM cases WHERE suite_id = ? AND is_deleted = 0",
                (suite_id,),
            ).fetchone()[0]
        return self._suite_dict(row, case_count)

    def update_suite(
        self, suite_id: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        write = SuiteWrite.model_validate(payload)
        with self._db.connect() as conn:
            current = conn.execute(
                "SELECT revision FROM suites WHERE id = ?", (suite_id,)
            ).fetchone()
            if current is None:
                raise RecordNotFound(f"SUITE_NOT_FOUND {suite_id}")
            if write.base_revision != current["revision"]:
                raise RevisionConflict(
                    f"REVISION_CONFLICT current={current['revision']} suite={suite_id}"
                )
            revision = current["revision"] + 1
            conn.execute(
                "UPDATE suites SET revision = ?, name = ?, description = ?,"
                " tags_json = ?, updated_at = ? WHERE id = ? AND revision = ?",
                (revision, write.name, write.description, _dump(write.tags),
                 _utc_now_iso(), suite_id, current["revision"]),
            )
        return self.get_suite(suite_id)

    def delete_suite(self, suite_id: str, *, base_revision: int) -> None:
        with self._db.connect() as conn:
            current = conn.execute(
                "SELECT revision FROM suites WHERE id = ?", (suite_id,)
            ).fetchone()
            if current is None:
                raise RecordNotFound(f"SUITE_NOT_FOUND {suite_id}")
            if base_revision != current["revision"]:
                raise RevisionConflict(
                    f"REVISION_CONFLICT current={current['revision']} suite={suite_id}"
                )
            # 软删：历史运行引用的 revision 仍然可读。
            conn.execute(
                "UPDATE suites SET is_deleted = 1, updated_at = ?"
                " WHERE id = ? AND is_deleted = 0",
                (_utc_now_iso(), suite_id),
            )

    @staticmethod
    def _suite_dict(row: sqlite3.Row, case_count: int) -> dict[str, Any]:
        read = SuiteRead.model_validate({
            "id": row["id"], "revision": row["revision"], "case_count": case_count,
            "name": row["name"], "description": row["description"],
            "tags": _load(row["tags_json"], []),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "is_deleted": bool(row["is_deleted"]),
        })
        return read.model_dump(mode="json")

    # ----------------------------------------------------------------- 用例版本

    def save_case(
        self, payload: Mapping[str, Any], *, case_id: str | None = None
    ) -> dict[str, Any]:
        """新建或编辑用例；编辑走 base_revision 的 CAS，落库即冻结一份新 revision。"""
        write = CaseWrite.model_validate(payload)
        definition = write.model_dump(mode="json", exclude={"base_revision", "suite_id"})
        suite_id = str(write.suite_id)
        now = _utc_now_iso()
        with self._db.connect() as conn:
            if case_id is None:
                if write.base_revision is not None:
                    raise RevisionConflict(
                        "REVISION_CONFLICT: new case must omit base_revision"
                    )
                new_case_id = _new_id()
                conn.execute(
                    "INSERT INTO cases (id, suite_id, latest_revision, is_deleted,"
                    " created_at, updated_at) VALUES (?, ?, 1, 0, ?, ?)",
                    (new_case_id, suite_id, now, now),
                )
                revision = 1
            else:
                new_case_id = case_id
                current = conn.execute(
                    "SELECT latest_revision, suite_id FROM cases WHERE id = ?",
                    (case_id,),
                ).fetchone()
                if current is None:
                    raise RecordNotFound(f"CASE_NOT_FOUND {case_id}")
                if write.base_revision != current["latest_revision"]:
                    raise RevisionConflict(
                        f"REVISION_CONFLICT current={current['latest_revision']}"
                        f" case={case_id}"
                    )
                if suite_id != current["suite_id"]:
                    raise RevisionConflict(
                        f"REVISION_CONFLICT CASE_NOT_IN_SUITE case={case_id}"
                    )
                revision = current["latest_revision"] + 1
                conn.execute(
                    "UPDATE cases SET latest_revision = ?, updated_at = ?"
                    " WHERE id = ? AND latest_revision = ?",
                    (revision, now, case_id, current["latest_revision"]),
                )
            conn.execute(
                "INSERT INTO case_revisions (case_id, revision, definition_json,"
                " created_at) VALUES (?, ?, ?, ?)",
                (new_case_id, revision, _dump(definition), now),
            )
            conn.execute(
                "UPDATE suites SET updated_at = ? WHERE id = ?", (now, suite_id)
            )
        return self.get_case(new_case_id)

    def get_case(self, case_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT id, suite_id, latest_revision, is_deleted, created_at,"
                " updated_at FROM cases WHERE id = ?",
                (case_id,),
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"CASE_NOT_FOUND {case_id}")
            definition = self._revision_json(conn, case_id, row["latest_revision"])
        return self._case_dict(row, definition, row["latest_revision"])

    def get_case_revision(self, case_id: str, revision: int) -> dict[str, Any]:
        """读取任意历史 revision 正文：重跑与报告必须回到当初冻结的输入。"""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT id, suite_id, latest_revision, is_deleted, created_at,"
                " updated_at FROM cases WHERE id = ?",
                (case_id,),
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"CASE_NOT_FOUND {case_id}")
            definition = self._revision_json(conn, case_id, revision)
        return self._case_dict(row, definition, revision)

    def delete_case(self, case_id: str, *, base_revision: int) -> None:
        with self._db.connect() as conn:
            current = conn.execute(
                "SELECT latest_revision FROM cases WHERE id = ?", (case_id,)
            ).fetchone()
            if current is None:
                raise RecordNotFound(f"CASE_NOT_FOUND {case_id}")
            if base_revision != current["latest_revision"]:
                raise RevisionConflict(
                    f"REVISION_CONFLICT current={current['latest_revision']} case={case_id}"
                )
            conn.execute(
                "UPDATE cases SET is_deleted = 1 WHERE id = ? AND is_deleted = 0",
                (case_id,),
            )

    @staticmethod
    def _revision_json(
        conn: sqlite3.Connection, case_id: str, revision: int
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT definition_json FROM case_revisions"
            " WHERE case_id = ? AND revision = ?",
            (case_id, revision),
        ).fetchone()
        if row is None:
            raise RecordNotFound(f"CASE_REVISION_NOT_FOUND {case_id}@{revision}")
        payload: dict[str, Any] = json.loads(row["definition_json"])
        return payload

    @staticmethod
    def _case_dict(
        row: Mapping[str, Any], definition: dict[str, Any], revision: int
    ) -> dict[str, Any]:
        read = CaseRead.model_validate({
            **definition,
            "suite_id": row["suite_id"],
            "id": row["id"],
            "revision": revision,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "is_deleted": bool(row["is_deleted"]),
        })
        return read.model_dump(mode="json")

    # ----------------------------------------------------------------- 导入

    def import_suite(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """契约 §3：单事务、全部合法才提交；导入忽略旧 id 并重建关联。"""
        schema_version = payload.get("schema_version")
        if schema_version != 1:
            raise ValueError("VALIDATION_ERROR: schema_version must be 1")
        raw_cases = payload.get("cases")
        if not isinstance(raw_cases, list):
            raise ValueError("VALIDATION_ERROR: cases must be a list")
        # 下面两处 cast 只让类型检查看到具体类型；同一路径上的 isinstance 才是真正的
        # 运行时护栏，元素内容随后仍要过 CaseWrite 校验。
        entries = cast("list[Any]", raw_cases)
        if not 1 <= len(entries) <= 200:
            raise ValueError("VALIDATION_ERROR: cases must hold 1..200 entries")
        cases: list[Mapping[str, Any]] = [
            cast("Mapping[str, Any]", item)
            for item in entries
            if isinstance(item, dict)
        ]
        if len(cases) != len(entries):
            raise ValueError("VALIDATION_ERROR: every case entry must be an object")

        # 先在事务外把每一条都校验一遍：任一条非法时不留半套数据，也便于报出是哪一条。
        write = SuiteWrite.model_validate(_importable(payload["suite"], _SUITE_SERVER_KEYS))
        if write.base_revision is not None:
            raise RevisionConflict("REVISION_CONFLICT: imported suite must omit base_revision")
        writes = [
            CaseWrite.model_validate(_importable(raw, _CASE_SERVER_KEYS)) for raw in cases
        ]

        suite_id = _new_id()
        now = _utc_now_iso()
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO suites (id, revision, name, description, tags_json,"
                " is_deleted, created_at, updated_at) VALUES (?, 1, ?, ?, ?, 0, ?, ?)",
                (suite_id, write.name, write.description, _dump(write.tags), now, now),
            )
            case_ids: list[str] = []
            definitions: list[dict[str, Any]] = []
            for item in writes:
                case_id = _new_id()
                case_ids.append(case_id)
                definition = self._definition(item, suite_id)
                definitions.append(definition)
                conn.execute(
                    "INSERT INTO cases (id, suite_id, latest_revision, is_deleted,"
                    " created_at, updated_at) VALUES (?, ?, 1, 0, ?, ?)",
                    (case_id, suite_id, now, now),
                )
                conn.execute(
                    "INSERT INTO case_revisions (case_id, revision, definition_json,"
                    " created_at) VALUES (?, 1, ?, ?)",
                    (case_id, _dump(definition), now),
                )
            case_count = conn.execute(
                "SELECT COUNT(*) FROM cases WHERE suite_id = ? AND is_deleted = 0",
                (suite_id,),
            ).fetchone()[0]
            suite = self._suite_dict(
                conn.execute(
                    "SELECT id, revision, name, description, tags_json, is_deleted,"
                    " created_at, updated_at FROM suites WHERE id = ?",
                    (suite_id,),
                ).fetchone(),
                case_count,
            )
            # 读模型就地由已校验的 definition 拼出：逐条 get_case 会为 200 条导入
            # 再开 200 个连接，这里一次事务读完。
            cases = [
                self._case_dict(
                    {"id": case_id, "suite_id": suite_id, "latest_revision": 1,
                     "is_deleted": 0, "created_at": now, "updated_at": now},
                    definition,
                    1,
                )
                for case_id, definition in zip(case_ids, definitions, strict=True)
            ]
        return {"suite": suite, "case_count": len(cases), "cases": cases}

    def import_sample(
        self, sample_id: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        """契约 §3：同一 sample_id 重复导入返回原对象，不产生副本。"""
        with self._db.connect() as conn:
            existing = conn.execute(
                "SELECT suite_id FROM sample_imports WHERE sample_id = ?", (sample_id,)
            ).fetchone()
        if existing is not None:
            suite = self.get_suite(existing["suite_id"])
            cases = self.list_cases(existing["suite_id"])
            return {"suite": suite, "case_count": suite["case_count"], "cases": cases}
        result = self.import_suite(payload)
        with self._db.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sample_imports (sample_id, suite_id, created_at)"
                " VALUES (?, ?, ?)",
                (sample_id, result["suite"]["id"], _utc_now_iso()),
            )
        return result

    def list_cases(self, suite_id: str) -> list[dict[str, Any]]:
        """固定排序（契约 §1）：用例按 created_at, id 降序。"""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT id, suite_id, latest_revision, is_deleted, created_at,"
                " updated_at FROM cases WHERE suite_id = ? AND is_deleted = 0"
                " ORDER BY created_at DESC, id DESC",
                (suite_id,),
            ).fetchall()
            definitions = [
                self._revision_json(conn, row["id"], row["latest_revision"])
                for row in rows
            ]
        return [
            self._case_dict(row, definition, row["latest_revision"])
            for row, definition in zip(rows, definitions, strict=True)
        ]

    @staticmethod
    def _definition(write: CaseWrite, suite_id: str) -> dict[str, Any]:
        dumped = write.model_dump(mode="json", exclude={"base_revision", "suite_id"})
        dumped["suite_id"] = suite_id
        return dumped

    # ----------------------------------------------------------------- 运行

    def create_run(
        self,
        *,
        scope: str,
        idempotency_key: str,
        request: RunCreate,
        agent: Mapping[str, Any],
        judge: Mapping[str, Any] | None,
        rules_version: str,
        grader_version: str,
        parent_run_id: str | None = None,
    ) -> dict[str, Any]:
        """幂等入队：key、request_hash、run 与 attempts 在**同一事务**内落库。"""
        digest = self.request_digest(json.loads(request.model_dump_json()))
        with self._db.connect(immediate=True) as conn:
            replay = self._replay(conn, scope, idempotency_key, digest)
            if replay is not None:
                return replay

            run_id = _new_id()
            now = _utc_now_iso()
            snapshots = [
                self._freeze_case(conn, str(request.suite_id), case_id)
                for case_id in request.case_ids
            ]
            snapshot_json = _dump({
                "case_revisions": [item["case_revision"] for item in snapshots],
                "agent_profile": dict(agent),
                "judge_profile": dict(judge) if judge else None,
                "rules_version": rules_version,
                "grader_version": grader_version,
                "tool_mode": snapshots[0]["case_snapshot"]["execution"]["tool_mode"],
                "limits": request.limits.model_dump(mode="json"),
            })
            conn.execute(
                "INSERT INTO runs (id, suite_id, status, snapshot_json, request_hash,"
                " parent_run_id, is_deleted, created_at) VALUES (?, ?, 'queued', ?, ?,"
                " ?, 0, ?)",
                (run_id, str(request.suite_id), snapshot_json, digest,
                 parent_run_id, now),
            )
            for index, item in enumerate(snapshots, start=1):
                conn.execute(
                    "INSERT INTO attempts (id, run_id, position, case_id, case_revision,"
                    " case_snapshot_json, execution_status, grading_status, verdict)"
                    " VALUES (?, ?, ?, ?, ?, ?, 'queued', 'pending', 'not_evaluated')",
                    (_new_id(), run_id, index, item["case_id"], item["case_revision"],
                     _dump(item["case_snapshot"])),
                )
            response = self._run_dict(conn, run_id)
            conn.execute(
                "INSERT INTO idempotency (scope, endpoint, key, request_hash, run_id,"
                " response_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (scope, RUNS_ENDPOINT, idempotency_key, digest, run_id, _dump(response), now),
            )
        return response

    @staticmethod
    def _replay(
        conn: sqlite3.Connection, scope: str, key: str, digest: str
    ) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT request_hash, response_json FROM idempotency"
            " WHERE scope = ? AND endpoint = ? AND key = ?",
            (scope, RUNS_ENDPOINT, key),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != digest:
            raise IdempotencyMismatch(
                f"IDEMPOTENCY_MISMATCH scope={scope} key={key}"
            )
        if row["response_json"] is None:
            raise IdempotencyInProgress(
                f"IDEMPOTENCY_IN_PROGRESS scope={scope} key={key}"
            )
        payload: dict[str, Any] = json.loads(row["response_json"])
        return payload

    @staticmethod
    def _freeze_case(
        conn: sqlite3.Connection, suite_id: str, case_id: uuid.UUID
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT id, suite_id, latest_revision, is_deleted FROM cases WHERE id = ?",
            (str(case_id),),
        ).fetchone()
        if row is None or row["is_deleted"]:
            raise RevisionConflict(f"REVISION_CONFLICT CASE_NOT_FOUND {case_id}")
        if row["suite_id"] != suite_id:
            raise RevisionConflict(
                f"REVISION_CONFLICT CASE_NOT_IN_SUITE case={case_id} suite={suite_id}"
            )
        definition = EvaluationRepository._revision_json(
            conn, str(case_id), row["latest_revision"]
        )
        definition["suite_id"] = suite_id
        return {
            "case_id": row["id"],
            "case_revision": row["latest_revision"],
            # 冻结**完整正文**：只留 revision id 的话，用例被删改后历史运行将无法复现。
            "case_snapshot": CaseRead.model_validate({
                **definition,
                "id": row["id"],
                "revision": row["latest_revision"],
                "created_at": _utc_now_iso(),
                "updated_at": _utc_now_iso(),
                "is_deleted": False,
            }).model_dump(mode="json", exclude={"id", "revision", "created_at",
                                                "updated_at", "is_deleted"}),
        }

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            return self._run_dict(conn, run_id)

    def _run_dict(self, conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
        row = conn.execute(
            "SELECT id, suite_id, status, snapshot_json, parent_run_id, is_deleted,"
            " created_at, started_at, finished_at FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RecordNotFound(f"RUN_NOT_FOUND {run_id}")
        attempts = conn.execute(
            "SELECT id, run_id, position, case_id, case_revision, case_snapshot_json,"
            " execution_status, grading_status, verdict, result_json, started_at,"
            " finished_at FROM attempts WHERE run_id = ? ORDER BY position, id",
            (run_id,),
        ).fetchall()
        attempt_ids = [item["id"] for item in attempts]
        # 复核与产物各查一次再按 attempt_id 分桶：逐条查会让 200 用例的运行读放大到
        # 400 次查询。空 attempt 列表直接跳过 IN 查询。
        reviews = _group_by_attempt(
            self._rows_for_attempts(
                conn,
                "SELECT id, attempt_id, verdict, comment, reviewer, created_at"
                " FROM reviews WHERE attempt_id IN ({slots}) ORDER BY created_at, id",
                attempt_ids,
            )
        )
        artifacts = _group_by_attempt(
            self._rows_for_attempts(
                conn,
                "SELECT id, attempt_id, relative_path, media_type, size_bytes, sha256"
                " FROM artifacts WHERE attempt_id IN ({slots}) ORDER BY id",
                attempt_ids,
            )
        )
        return {
            "id": row["id"],
            "suite_id": row["suite_id"],
            "status": row["status"],
            "snapshot": _load(row["snapshot_json"], {}),
            "parent_run_id": row["parent_run_id"],
            "is_deleted": bool(row["is_deleted"]),
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "planned_count": len(attempts),
            "attempts": [
                self._attempt_dict(item, reviews.get(item["id"], []),
                                  artifacts.get(item["id"], []))
                for item in attempts
            ],
        }

    @staticmethod
    def _rows_for_attempts(
        conn: sqlite3.Connection, sql: str, attempt_ids: Sequence[str]
    ) -> list[sqlite3.Row]:
        if not attempt_ids:
            return []
        return conn.execute(
            sql.format(slots=_placeholders(len(attempt_ids))), list(attempt_ids)
        ).fetchall()

    @staticmethod
    def _attempt_dict(
        row: sqlite3.Row,
        reviews: list[dict[str, Any]],
        artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "position": row["position"],
            "case_id": row["case_id"],
            "case_revision": row["case_revision"],
            "case_snapshot": _load(row["case_snapshot_json"], {}),
            "execution_status": row["execution_status"],
            "grading_status": row["grading_status"],
            "verdict": row["verdict"],
            "result": _load(row["result_json"], None),
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "reviews": reviews,
            "artifacts": artifacts,
        }

    def get_attempt(self, attempt_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT run_id FROM attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"ATTEMPT_NOT_FOUND {attempt_id}")
            run = self._run_dict(conn, row["run_id"])
        return next(item for item in run["attempts"] if item["id"] == attempt_id)

    # ----------------------------------------------------------------- 只读查询

    def get_case_name(self, case_id: str) -> str | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT name FROM cases WHERE id = ?", (case_id,)
            ).fetchone()
        return None if row is None else str(row["name"])

    def get_suite_name(self, suite_id: str) -> str:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT name FROM suites WHERE id = ?", (suite_id,)
            ).fetchone()
        if row is None:
            raise RecordNotFound(f"SUITE_NOT_FOUND {suite_id}")
        return str(row["name"])

    def list_runs(
        self,
        *,
        status: str | None = None,
        suite_id: str | None = None,
        parent_run_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """运行列表；软删运行不出现在这里，但软删本身不释放幂等 key。"""
        where = ["r.is_deleted = 0"]
        params: dict[str, Any] = {"limit": limit}
        if status is not None:
            where.append("r.status = :status")
            params["status"] = status
        if suite_id is not None:
            where.append("r.suite_id = :suite_id")
            params["suite_id"] = suite_id
        if parent_run_id is not None:
            where.append("r.parent_run_id = :parent_run_id")
            params["parent_run_id"] = parent_run_id
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT r.id, r.suite_id, s.name AS suite_name, r.status,"
                " r.created_at, r.started_at, r.finished_at, r.parent_run_id,"
                " (SELECT COUNT(*) FROM attempts a WHERE a.run_id = r.id)"
                "   AS planned_count,"
                " (SELECT COUNT(*) FROM attempts a WHERE a.run_id = r.id"
                "   AND " + _TERMINAL_PREDICATE + ") AS terminal_count"
                " FROM runs r JOIN suites s ON s.id = r.suite_id"
                " WHERE " + " AND ".join(where) +
                " ORDER BY r.created_at DESC, r.id LIMIT :limit",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def run_counts(self, run_id: str) -> dict[str, int]:
        """按终态分桶；未终结的 attempt 只进 pending。

        不能直接 ``GROUP BY verdict``：未领取的项 verdict 初始就是 not_evaluated，
        直接分组会把它算成「已判为未评估」，让页面显示的分布比真实进度领先。
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT verdict, COUNT(*) AS n FROM attempts WHERE run_id = ?"
                " AND " + _TERMINAL_PREDICATE + " GROUP BY verdict",
                (run_id,),
            ).fetchall()
            pending = conn.execute(
                "SELECT COUNT(*) FROM attempts WHERE run_id = ?"
                " AND NOT (" + _TERMINAL_PREDICATE + ")",
                (run_id,),
            ).fetchone()[0]
        counts = {"pending": int(pending), "passed": 0, "failed": 0,
                  "review_required": 0, "not_evaluated": 0}
        for row in rows:
            verdict = str(row["verdict"])
            counts[verdict if verdict in counts else "not_evaluated"] += int(row["n"])
        return counts

    def list_run_attempts(
        self,
        run_id: str,
        *,
        verdict: str | None = None,
        execution_status: str | None = None,
        grading_status: str | None = None,
    ) -> list[dict[str, Any]]:
        where = ["a.run_id = :run_id"]
        params: dict[str, Any] = {"run_id": run_id}
        for column, value in (
            ("verdict", verdict),
            ("execution_status", execution_status),
            ("grading_status", grading_status),
        ):
            if value is not None:
                where.append(f"a.{column} = :{column}")
                params[column] = value
        with self._db.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM runs WHERE id = ?", (run_id,)
            ).fetchone() is None:
                raise RecordNotFound(f"RUN_NOT_FOUND {run_id}")
            rows = conn.execute(
                "SELECT a.id, a.run_id, a.position, a.case_id, a.case_revision,"
                " a.case_snapshot_json, a.execution_status, a.grading_status,"
                " a.verdict, a.started_at, a.finished_at, a.result_json"
                " FROM attempts a WHERE " + " AND ".join(where) +
                " ORDER BY a.position, a.id",
                params,
            ).fetchall()
        listed = []
        for row in rows:
            attempt = {**dict(row), "result": _load(row["result_json"], None)}
            # 用例名取自冻结快照：运行中途改名也不该让历史报告换名字。
            snapshot = _load(attempt.pop("case_snapshot_json"), {})
            attempt["case_name"] = str(snapshot.get("name") or "")
            listed.append(attempt)
        return listed

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        """按 artifact id 取登记行；下载只认这一行给出的 attempt 归属，不解析任意 path。"""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT id, attempt_id, relative_path, media_type, size_bytes, sha256"
                " FROM artifacts WHERE id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise RecordNotFound(f"ARTIFACT_NOT_FOUND {artifact_id}")
        return dict(row)

    def list_artifacts_for_attempt(self, attempt_id: str) -> list[dict[str, Any]]:
        # 按 rowid 而非 id 排序：artifact id 是 uuid，字典序与登记顺序无关。
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT id, attempt_id, relative_path, media_type, size_bytes, sha256"
                " FROM artifacts WHERE attempt_id = ? ORDER BY rowid",
                (attempt_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def gold_revision(self) -> int:
        """契约 §3.1：确认或修订标签都会推高该版本，使旧校准结果失效。"""
        with self._db.connect() as conn:
            row = conn.execute("SELECT revision FROM gold_state WHERE id = 1").fetchone()
        return 0 if row is None else int(row["revision"])

    def list_open_attempts(self) -> list[dict[str, Any]]:
        """重启恢复用：所有执行或评分仍在进行的 attempt，附带 PID 与启动身份。"""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT id, run_id, execution_status, grading_status, pid,"
                " process_nonce FROM attempts"
                " WHERE execution_status IN ('queued', 'running')"
                "    OR grading_status IN ('pending', 'running')"
                " ORDER BY run_id, position",
            ).fetchall()
        return [dict(row) for row in rows]

    def open_runs(self) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT id, status FROM runs WHERE is_deleted = 0"
                " AND status IN ('queued', 'running', 'cancelling')"
                " ORDER BY created_at",
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_interrupted(self, run_id: str) -> dict[str, Any]:
        """重启后收口残留运行：连同待执行项一并终结，不自动补跑。"""
        now = _utc_now_iso()
        with self._db.connect(immediate=True) as conn:
            row = conn.execute(
                "SELECT status FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"RUN_NOT_FOUND {run_id}")
            if row["status"] in TERMINAL_RUN_STATUSES:
                return self._run_dict(conn, run_id)
            # 待执行的项没有进程可回收，但也不能留在 queued 让运行永远不算结束。
            conn.execute(
                "UPDATE attempts SET execution_status = 'interrupted',"
                " grading_status = 'not_applicable', verdict = 'not_evaluated',"
                " finished_at = :now"
                " WHERE run_id = :run_id AND execution_status = 'queued'",
                {"now": now, "run_id": run_id},
            )
            conn.execute(
                "UPDATE attempts SET execution_status = 'interrupted',"
                " grading_status = 'not_applicable', verdict = 'not_evaluated',"
                " finished_at = :now WHERE run_id = :run_id"
                " AND execution_status = 'running'",
                {"now": now, "run_id": run_id},
            )
            conn.execute(
                "UPDATE runs SET status = 'interrupted', finished_at = :now"
                " WHERE id = :run_id",
                {"now": now, "run_id": run_id},
            )
        return self.get_run(run_id)

    # ----------------------------------------------------------------- 领取与终态

    def claim_attempt(self) -> dict[str, Any] | None:
        """plan §3 的原子领取：``BEGIN IMMEDIATE`` + 状态谓词 + ``RETURNING``。

        不能拆成「先查后改」：两个执行器会拿到同一行。没有可领取项时返回 ``None``。
        """
        now = _utc_now_iso()
        with self._db.connect(immediate=True) as conn:
            row = conn.execute(
                "UPDATE attempts SET execution_status = 'running', started_at = :now"
                " WHERE id = ("
                "   SELECT a.id FROM attempts a JOIN runs r ON r.id = a.run_id"
                "   WHERE a.execution_status = 'queued'"
                "     AND r.status IN ('queued', 'running')"
                "     AND r.is_deleted = 0"
                "   ORDER BY r.created_at, a.position LIMIT 1"
                " ) AND execution_status = 'queued'"
                " RETURNING id, run_id, position, case_id, case_revision,"
                " case_snapshot_json, execution_status, grading_status, verdict,"
                " started_at",
                {"now": now},
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE runs SET status = 'running',"
                " started_at = COALESCE(started_at, :now)"
                " WHERE id = :run_id AND status = 'queued'",
                {"now": now, "run_id": row["run_id"]},
            )
            claimed = dict(row)
            claimed["case_snapshot"] = _load(row["case_snapshot_json"], {})
            claimed.pop("case_snapshot_json")
        return claimed

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        """取消只对排队/运行中的 run 生效；已是 cancelling/cancelled 的原样返回。"""
        with self._db.connect(immediate=True) as conn:
            row = conn.execute(
                "SELECT status FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"RUN_NOT_FOUND {run_id}")
            # 契约 §3：已是 cancelling/cancelled 的原样返回，不重复触发回收。
            if row["status"] in ("cancelling", "cancelled"):
                return self._run_dict(conn, run_id)
            if row["status"] not in CANCELLABLE_RUN_STATUSES:
                raise StateConflict(f"RUN_NOT_CANCELLABLE status={row['status']}")
            # 只进 cancelling：进程树回收归 WU-05 的调度器，收干净后才 finalize_cancel。
            # 这里直接落 cancelled 会让页面显示已取消而进程仍在跑。
            conn.execute(
                "UPDATE attempts SET execution_status = 'cancelled', finished_at = :now"
                " WHERE run_id = :run_id AND execution_status = 'queued'",
                {"now": _utc_now_iso(), "run_id": run_id},
            )
            conn.execute(
                "UPDATE runs SET status = 'cancelling'"
                " WHERE id = :run_id AND status IN ('queued', 'running')",
                {"run_id": run_id},
            )
        return self.get_run(run_id)

    def finalize_cancel(self, run_id: str) -> dict[str, Any]:
        """调度器回收完进程树后收口：cancelling → cancelled。"""
        now = _utc_now_iso()
        with self._db.connect(immediate=True) as conn:
            row = conn.execute(
                "SELECT status FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"RUN_NOT_FOUND {run_id}")
            if row["status"] == "cancelled":
                return self._run_dict(conn, run_id)
            if row["status"] != "cancelling":
                raise StateConflict(f"RUN_NOT_CANCELLABLE status={row['status']}")
            conn.execute(
                "UPDATE attempts SET execution_status = 'cancelled', finished_at = :now"
                " WHERE run_id = :run_id AND execution_status IN ('queued', 'running')",
                {"now": now, "run_id": run_id},
            )
            conn.execute(
                "UPDATE runs SET status = 'cancelled', finished_at = :now"
                " WHERE id = :run_id AND status = 'cancelling'",
                {"now": now, "run_id": run_id},
            )
        return self.get_run(run_id)

    def finish_attempt(self, attempt_id: str, *, result: Mapping[str, Any]) -> None:
        """提交 attempt 终态；评分已终结的一律拒绝（防止重复提交覆盖先落的结果）。

        允许的两种进入方式：

        1. 一次性提交——执行刚结束、评分还没写库（``execution_status`` 仍 open）。
        2. Judge 窗口——执行阶段已由 :meth:`mark_attempt_execution_done` 结束，
           等评分回来后再补齐评分与 verdict。
        """
        execution_status = result["execution_status"]
        grading_status = result["grading_status"]
        verdict = result["verdict"]
        now = _utc_now_iso()
        with self._db.connect(immediate=True) as conn:
            row = conn.execute(
                "SELECT run_id, execution_status, grading_status FROM attempts"
                " WHERE id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"ATTEMPT_NOT_FOUND {attempt_id}")
            # 先看已落库的状态：重复提交必须先被拒，不能让本次 payload 覆盖先落的结果。
            if row["grading_status"] in TERMINAL_GRADING_STATUSES:
                raise StateConflict(
                    f"ATTEMPT_NOT_FINISHABLE status={row['grading_status']}"
                )
            if grading_status not in TERMINAL_GRADING_STATUSES:
                raise StateConflict(f"ATTEMPT_GRADING_NOT_FINISHED status={grading_status}")
            if execution_status not in TERMINAL_EXECUTION_STATUSES:
                raise StateConflict(
                    f"ATTEMPT_EXECUTION_NOT_FINISHED status={execution_status}"
                )
            conn.execute(
                "UPDATE attempts SET execution_status = ?, grading_status = ?,"
                " verdict = ?, result_json = ?, finished_at = ?"
                " WHERE id = ? AND grading_status NOT IN"
                " ('completed', 'judge_error', 'not_applicable')",
                (execution_status, grading_status, verdict,
                 _dump(dict(result.get("result", {}))), now, attempt_id),
            )
            self._settle_run_if_done(conn, row["run_id"], now)

    def mark_attempt_execution_done(
        self, attempt_id: str, *, execution_status: str
    ) -> None:
        """记录执行阶段结束但评分仍在进行。

        契约 §4 明确存在「Agent finished 而 Judge pending」这一状态：它既不算终态，
        也不能让 run 收口。没有这一步，评分窗口期在库里的表现会和「已排队」混同，
        重启恢复也无法区分「还没开始跑」与「跑完了在等 Judge」。
        """
        if execution_status not in TERMINAL_EXECUTION_STATUSES:
            raise StateConflict(f"ATTEMPT_EXECUTION_NOT_FINISHED status={execution_status}")
        now = _utc_now_iso()
        with self._db.connect(immediate=True) as conn:
            row = conn.execute(
                "SELECT execution_status FROM attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"ATTEMPT_NOT_FOUND {attempt_id}")
            if row["execution_status"] not in OPEN_EXECUTION_STATUSES:
                raise StateConflict(
                    f"ATTEMPT_NOT_FINISHABLE status={row['execution_status']}"
                )
            conn.execute(
                "UPDATE attempts SET execution_status = ?, finished_at = ?"
                " WHERE id = ?",
                (execution_status, now, attempt_id),
            )

    def set_attempt_process(
        self, attempt_id: str, *, pid: int, nonce: str
    ) -> None:
        """记录本次服务亲自启动的进程身份，供重启恢复核对。

        nonce 只存在这一列与进程内存，不进命令行、不进 API 响应；重启后核不上就
        只能走人工处置，这正是「禁止按进程名杀」要保住的后果。
        """
        with self._db.connect(immediate=True) as conn:
            if conn.execute(
                "SELECT 1 FROM attempts WHERE id = ?", (attempt_id,)
            ).fetchone() is None:
                raise RecordNotFound(f"ATTEMPT_NOT_FOUND {attempt_id}")
            conn.execute(
                "UPDATE attempts SET pid = ?, process_nonce = ? WHERE id = ?",
                (pid, nonce, attempt_id),
            )

    def mark_attempt_grading_running(self, attempt_id: str) -> None:
        """评分开始：让「等 Judge」与「还没排到评分」在库里可区分。"""
        with self._db.connect(immediate=True) as conn:
            row = conn.execute(
                "SELECT grading_status FROM attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"ATTEMPT_NOT_FOUND {attempt_id}")
            conn.execute(
                "UPDATE attempts SET grading_status = 'running' WHERE id = ?"
                " AND grading_status = 'pending'",
                (attempt_id,),
            )

    def _settle_run_if_done(
        self, conn: sqlite3.Connection, run_id: str, now: str
    ) -> None:
        """只有全部 attempt 的执行与评分都终结才把 run 收成 completed。

        契约 §4：Agent finished 而 Judge pending 不算整体完成。只看
        execution_status 会在评分窗口期提前收口，指标也会少算未判分的项。
        """
        pending = conn.execute(
            "SELECT COUNT(*) FROM attempts WHERE run_id = ?"
            " AND NOT (" + _TERMINAL_PREDICATE + ")",
            (run_id,),
        ).fetchone()[0]
        if pending:
            return
        conn.execute(
            "UPDATE runs SET status = 'completed', finished_at = :now"
            " WHERE id = :run_id AND status IN ('queued', 'running')",
            {"now": now, "run_id": run_id},
        )

    def soft_delete_run(self, run_id: str) -> None:
        """仅终态可软删；幂等记录保留，因此同一 key 重放仍命中已删运行。"""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT status FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"RUN_NOT_FOUND {run_id}")
            if row["status"] not in TERMINAL_RUN_STATUSES:
                raise StateConflict(f"RUN_NOT_TERMINAL status={row['status']}")
            conn.execute(
                "UPDATE runs SET is_deleted = 1 WHERE id = ? AND is_deleted = 0",
                (run_id,),
            )

    # ----------------------------------------------------------------- 复核与产物

    def append_review(
        self, attempt_id: str, verdict: str, comment: str, *, reviewer: str
    ) -> dict[str, Any]:
        """只追加，不回写自动 verdict（契约 §7.2）。"""
        if verdict not in ("passed", "failed"):
            raise ValueError(f"VALIDATION_ERROR: unsupported verdict {verdict}")
        if not reviewer.strip():
            raise ValueError("VALIDATION_ERROR: reviewer is required")
        review_id = _new_id()
        created_at = _utc_now_iso()
        with self._db.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM attempts WHERE id = ?", (attempt_id,)
            ).fetchone() is None:
                raise RecordNotFound(f"ATTEMPT_NOT_FOUND {attempt_id}")
            conn.execute(
                "INSERT INTO reviews (id, attempt_id, verdict, comment, reviewer,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (review_id, attempt_id, verdict, comment, reviewer, created_at),
            )
        return {
            "id": review_id, "attempt_id": attempt_id, "verdict": verdict,
            "comment": comment, "reviewer": reviewer, "created_at": created_at,
        }

    def list_reviews(self, attempt_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT id, attempt_id, verdict, comment, reviewer, created_at"
                " FROM reviews WHERE attempt_id = ? ORDER BY created_at, id",
                (attempt_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def append_artifact(
        self,
        attempt_id: str,
        *,
        relative_path: str,
        media_type: str,
        size_bytes: int,
        sha256: str,
    ) -> dict[str, Any]:
        """产物只登记受控相对路径；绝不接受客户端给的绝对路径。"""
        validate_relative_path(relative_path)
        artifact_id = _new_id()
        with self._db.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM attempts WHERE id = ?", (attempt_id,)
            ).fetchone() is None:
                raise RecordNotFound(f"ATTEMPT_NOT_FOUND {attempt_id}")
            conn.execute(
                "INSERT INTO artifacts (id, attempt_id, relative_path, media_type,"
                " size_bytes, sha256) VALUES (?, ?, ?, ?, ?, ?)",
                (artifact_id, attempt_id, relative_path, media_type, size_bytes, sha256),
            )
        return {
            "id": artifact_id, "attempt_id": attempt_id,
            "relative_path": relative_path, "media_type": media_type,
            "size_bytes": size_bytes, "sha256": sha256,
        }

    # ----------------------------------------------------------------- 校准（供 WU-05/WU-08 接线）

    def list_gold_samples(self) -> dict[str, Any]:
        with self._db.connect() as conn:
            state = conn.execute("SELECT revision FROM gold_state WHERE id = 1").fetchone()
            rows = conn.execute(
                "SELECT id, revision, definition_json, label_status, reviewer, comment,"
                " updated_at FROM gold_samples ORDER BY id"
            ).fetchall()
        return {
            "gold_revision": 0 if state is None else state["revision"],
            "data": [
                {**json.loads(row["definition_json"]), "id": row["id"],
                 "label_status": row["label_status"],
                 "reviewer": row["reviewer"], "comment": row["comment"],
                 "updated_at": row["updated_at"]}
                for row in rows
            ],
        }

    def review_gold_sample(
        self, sample_id: str, review: Mapping[str, Any]
    ) -> dict[str, Any]:
        """确认/修订人工标签并抬升全局 gold_revision，使旧校准失效。"""
        now = _utc_now_iso()
        with self._db.connect(immediate=True) as conn:
            state = conn.execute("SELECT revision FROM gold_state WHERE id = 1").fetchone()
            current = 0 if state is None else state["revision"]
            if review["base_gold_revision"] != current:
                raise RevisionConflict(
                    f"REVISION_CONFLICT gold={current} sample={sample_id}"
                )
            row = conn.execute(
                "SELECT definition_json FROM gold_samples WHERE id = ?", (sample_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"GOLD_SAMPLE_NOT_FOUND {sample_id}")
            definition = json.loads(row["definition_json"])
            definition["expected_verdict"] = review["expected_verdict"]
            definition["expected_scores"] = [
                dict(item) for item in review["expected_scores"]
            ]
            revision = current + 1
            conn.execute(
                "UPDATE gold_samples SET definition_json = ?, label_status = 'confirmed',"
                " reviewer = ?, comment = ?, updated_at = ? WHERE id = ?",
                (_dump(definition), "local-user", review["comment"], now, sample_id),
            )
            conn.execute(
                "INSERT INTO gold_state (id, revision) VALUES (1, ?)"
                " ON CONFLICT(id) DO UPDATE SET revision = excluded.revision",
                (revision,),
            )
        return {**definition, "id": sample_id, "label_status": "confirmed",
                "reviewer": "local-user", "updated_at": now}

    def create_calibration(
        self, *, snapshot: Mapping[str, Any], sample_ids: Sequence[str]
    ) -> dict[str, Any]:
        job_id = _new_id()
        now = _utc_now_iso()
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO calibration_jobs (id, status, snapshot_json, created_at)"
                " VALUES (?, 'queued', ?, ?)", (job_id, _dump(dict(snapshot)), now)
            )
            for index, _ in enumerate(sample_ids, start=1):
                conn.execute(
                    "INSERT INTO calibration_items (id, job_id, position, status)"
                    " VALUES (?, ?, ?, 'queued')", (_new_id(), job_id, index)
                )
        return self.get_calibration(job_id)

    def get_calibration(self, job_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT id, status, snapshot_json, result_json, created_at, started_at,"
                " finished_at FROM calibration_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"CALIBRATION_NOT_FOUND {job_id}")
            progress = conn.execute(
                "SELECT COUNT(*) AS total,"
                " SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed"
                " FROM calibration_items WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return {
            "id": row["id"], "status": row["status"],
            "snapshot": _load(row["snapshot_json"], {}),
            "result": _load(row["result_json"], None),
            "total": progress["total"],
            "completed": progress["completed"] or 0,
            "created_at": row["created_at"], "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    def cancel_calibration(self, job_id: str) -> dict[str, Any]:
        now = _utc_now_iso()
        with self._db.connect(immediate=True) as conn:
            row = conn.execute(
                "SELECT status FROM calibration_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"CALIBRATION_NOT_FOUND {job_id}")
            if row["status"] in TERMINAL_RUN_STATUSES | {"cancelled"}:
                return self._get_calibration_in(conn, job_id)
            conn.execute(
                "UPDATE calibration_items SET status = 'cancelled'"
                " WHERE job_id = ? AND status = 'queued'",
                (job_id,),
            )
            conn.execute(
                "UPDATE calibration_jobs SET status = 'cancelled', finished_at = ?"
                " WHERE id = ? AND status IN ('queued', 'running')",
                (now, job_id),
            )
        return self.get_calibration(job_id)

    def _get_calibration_in(
        self, conn: sqlite3.Connection, job_id: str
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT id, status, snapshot_json, result_json, created_at, started_at,"
            " finished_at FROM calibration_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return {
            "id": row["id"], "status": row["status"],
            "snapshot": _load(row["snapshot_json"], {}),
            "result": _load(row["result_json"], None),
            "created_at": row["created_at"], "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    def claim_calibration_item(self) -> dict[str, Any] | None:
        """校准项的原子领取；校准任务与普通运行共享同一并发预算与取消语义。"""
        now = _utc_now_iso()
        with self._db.connect(immediate=True) as conn:
            row = conn.execute(
                "UPDATE calibration_items SET status = 'running', pid = NULL"
                " WHERE id = ("
                "   SELECT i.id FROM calibration_items i"
                "   JOIN calibration_jobs j ON j.id = i.job_id"
                "   WHERE i.status = 'queued' AND j.status IN ('queued', 'running')"
                "   ORDER BY j.created_at, i.position LIMIT 1"
                " ) AND status = 'queued'"
                " RETURNING id, job_id, position",
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE calibration_jobs SET status = 'running',"
                " started_at = COALESCE(started_at, :now)"
                " WHERE id = :job_id AND status = 'queued'",
                {"now": now, "job_id": row["job_id"]},
            )
        claimed = dict(row)
        job = self.get_calibration(claimed["job_id"])
        claimed["snapshot"] = job["snapshot"]
        return claimed

    def finish_calibration_item(
        self, item_id: str, *, status: str, result: Mapping[str, Any] | None = None
    ) -> None:
        """提交校准项终态；``matched`` 为 bool，决定 agreement 的分子。"""
        if status not in ("completed", "failed", "cancelled"):
            raise StateConflict(f"CALIBRATION_ITEM_NOT_FINISHABLE status={status}")
        with self._db.connect(immediate=True) as conn:
            row = conn.execute(
                "SELECT job_id, status FROM calibration_items WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"CALIBRATION_ITEM_NOT_FOUND {item_id}")
            if row["status"] not in ("queued", "running"):
                raise StateConflict(
                    f"CALIBRATION_ITEM_NOT_FINISHABLE status={row['status']}"
                )
            conn.execute(
                "UPDATE calibration_items SET status = ?, result_json = ? WHERE id = ?",
                (status, _dump(dict(result or {})), item_id),
            )
            self._settle_calibration_if_done(conn, row["job_id"])

    def _settle_calibration_if_done(
        self, conn: sqlite3.Connection, job_id: str
    ) -> None:
        """全部校准项终结才收口作业；agreement 由 completed 项的 matched 汇总。"""
        pending = conn.execute(
            "SELECT COUNT(*) FROM calibration_items WHERE job_id = ?"
            " AND status IN ('queued', 'running')",
            (job_id,),
        ).fetchone()[0]
        if pending:
            return
        rows = conn.execute(
            "SELECT status, result_json FROM calibration_items"
            " WHERE job_id = ? AND status = 'completed'",
            (job_id,),
        ).fetchall()
        total = len(rows)
        matched = sum(
            1 for row in rows if _load(row["result_json"], {}).get("matched") is True
        )
        # 未全部成功就不写 passed/agreement：契约 §3.1 要求这种情况保持 null，
        # 用 0 冒充会让人以为「跑完了但一致率是零」。
        complete = len(
            conn.execute(
                "SELECT 1 FROM calibration_items WHERE job_id = ?"
                " AND status != 'completed'",
                (job_id,),
            ).fetchall()
        ) == 0
        agreement = (matched / total) if total and complete else None
        status = "completed" if complete else "cancelled"
        conn.execute(
            "UPDATE calibration_jobs SET status = ?, result_json = ?, finished_at = ?"
            " WHERE id = ?",
            (
                status,
                _dump({"total": total, "matched": matched, "agreement": agreement,
                       "passed": None if agreement is None else agreement >= 0.8}),
                _utc_now_iso(),
                job_id,
            ),
        )

    def open_calibrations(self) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT id, status FROM calibration_jobs"
                " WHERE status IN ('queued', 'running') ORDER BY created_at",
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_calibration_interrupted(self, job_id: str) -> dict[str, Any]:
        """重启恢复：残留校准作业连同待执行项一并终结，不自动补跑。"""
        with self._db.connect(immediate=True) as conn:
            row = conn.execute(
                "SELECT status FROM calibration_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"CALIBRATION_NOT_FOUND {job_id}")
            if row["status"] in TERMINAL_RUN_STATUSES | {"cancelled"}:
                return self._get_calibration_in(conn, job_id)
            conn.execute(
                "UPDATE calibration_items SET status = 'cancelled'"
                " WHERE job_id = ? AND status IN ('queued', 'running')",
                (job_id,),
            )
            conn.execute(
                "UPDATE calibration_jobs SET status = 'interrupted', finished_at = ?"
                " WHERE id = ?",
                (_utc_now_iso(), job_id),
            )
        return self.get_calibration(job_id)

    def record_calibration_result(
        self,
        *,
        judge_fingerprint: str,
        rubric_version: str,
        gold_revision: int,
        total: int,
        matched: int,
    ) -> dict[str, Any]:
        """契约 §3.1：agreement = matched / total；≥0.8 才算通过。"""
        agreement = None if total == 0 else matched / total
        result_id = _new_id()
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO calibration_results (id, judge_fingerprint, rubric_version,"
                " gold_revision, total, matched, agreement, passed, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result_id, judge_fingerprint, rubric_version, gold_revision, total,
                 matched, agreement, None if agreement is None else int(agreement >= 0.8),
                 _utc_now_iso()),
            )
        return {
            "id": result_id, "judge_fingerprint": judge_fingerprint,
            "rubric_version": rubric_version, "gold_revision": gold_revision,
            "total": total, "matched": matched, "agreement": agreement,
            "passed": None if agreement is None else agreement >= 0.8,
        }

    def find_calibration_result(
        self, *, judge_fingerprint: str, rubric_version: str, gold_revision: int
    ) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT id, judge_fingerprint, rubric_version, gold_revision, total,"
                " matched, agreement, passed FROM calibration_results"
                " WHERE judge_fingerprint = ? AND rubric_version = ?"
                " AND gold_revision = ?",
                (judge_fingerprint, rubric_version, gold_revision),
            ).fetchone()
        if row is None:
            return None
        return {**dict(row), "passed": None if row["passed"] is None else bool(row["passed"])}

    def seed_gold_samples(
        self, samples: Sequence[Mapping[str, Any]], *, gold_revision: int
    ) -> int:
        """首启写入内置 gold 建议标签；状态一律 proposed，AI 不能替用户确认。"""
        now = _utc_now_iso()
        with self._db.connect() as conn:
            for sample in samples:
                conn.execute(
                    "INSERT OR REPLACE INTO gold_samples (id, revision, definition_json,"
                    " label_status, reviewer, comment, updated_at)"
                    " VALUES (?, 1, ?, 'proposed', NULL, '', ?)",
                    (sample["id"], _dump(dict(sample)), now),
                )
            conn.execute(
                "INSERT INTO gold_state (id, revision) VALUES (1, ?)"
                " ON CONFLICT(id) DO UPDATE SET revision = excluded.revision",
                (gold_revision,),
            )
        return len(samples)
