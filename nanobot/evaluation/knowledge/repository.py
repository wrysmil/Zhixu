"""知识检索评测的持久化层（契约 §3）。

与既有评估域一致：**事务归仓储，连接归 database**。知识评测的表与旧评估域的表
**同库**（同一个 ``evaluation.db``），schema 版本 ``"5"`` 追加建表——结构只增不改，
旧库按 ``_schema_meta.version`` 推进即可，不会另开库文件。

四条硬约束：

1. **数据集乐观锁**：``update_dataset`` 必须带 ``base_revision``，与库里的
   ``revision`` 不一致时抛 ``KNOWLEDGE_DATASET_REVISION_CONFLICT``——绝不
   「最后写入者赢」地静默覆盖别人的编辑。
2. **结果行主键是 ``(run_id, case_id)``**，其中 ``case_id`` 是
   ``case_id::config_id`` 复合形式：同一条查询在多个检索配置下各存一行，
   重复写入是 upsert（重跑幂等，不产生重复行）。
3. **JSON 列一律** ``json.dumps(..., ensure_ascii=False)`` 存、``json.loads`` 读：
   保留中文原文；缺证据的 ``None`` 原样保留，绝不补 0。
4. **删数据集幂等**：连带删掉它名下的运行与结果，不留外键悬挂。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.knowledge.models import (
    KnowledgeEvalCase,
    KnowledgeEvalCaseMetrics,
    KnowledgeEvalConfigEntry,
    KnowledgeEvalHit,
)

#: 数据集不存在。
CODE_DATASET_NOT_FOUND = "KNOWLEDGE_DATASET_NOT_FOUND"
#: 数据集版本不符（乐观锁冲突）。
CODE_REVISION_CONFLICT = "KNOWLEDGE_DATASET_REVISION_CONFLICT"
#: 运行不存在。
CODE_RUN_NOT_FOUND = "KNOWLEDGE_RUN_NOT_FOUND"


class KnowledgeEvalError(RuntimeError):
    """带错误码的知识评测异常。

    错误码与 ``nanobot/evaluation/errors.py`` 的 ``ERROR_STATUS`` 对齐，
    HTTP 层据此映射状态码——仓储只负责「说清是哪一类错」，不回显内部细节。
    """

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _dump(value: Any) -> str:
    """JSON 列落库：保留中文原文（``ensure_ascii=False``）。"""
    return json.dumps(value, ensure_ascii=False)


def _load(raw: str | None, default: Any) -> Any:
    if raw is None:
        return default
    return json.loads(raw)


def _model_payload(value: Any) -> Any:
    """把 pydantic 模型摊成可 JSON 化的普通结构；其余原样返回。"""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return value


def _as_revision(value: int | str) -> int | None:
    """版本号按字符串传输，仓储侧统一按整数比较；解析不出视为冲突。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _case_payload(case: Mapping[str, Any] | KnowledgeEvalCase) -> dict[str, Any]:
    """用例落库前先过模型：**label_source 由服务端推导**，客户端传的不作数。"""
    model = (
        case
        if isinstance(case, KnowledgeEvalCase)
        else KnowledgeEvalCase.model_validate(dict(case))
    )
    if not model.id:
        # 调用方没给 id 就补一个：不补会让所有用例共用空串，runner 存结果时主键撞车。
        model = model.model_copy(update={"id": _new_id()})
    return model.model_dump(mode="json")


class KnowledgeEvalRepository:
    """知识评测的唯一写入口；一个服务实例持有一个。"""

    def __init__(self, db: EvaluationDatabase | Path | str) -> None:
        if isinstance(db, EvaluationDatabase):
            self._db = db
            self._owns_db = False
        else:
            self._db = EvaluationDatabase(Path(db))
            self._owns_db = True

    # ----------------------------------------------------------------- schema

    def init_schema(self) -> None:
        """把库推进到当前代码版本（含版本 ``"5"`` 的知识评测表）。"""
        self._db.init_schema()

    def close(self) -> None:
        """关闭自建的数据库连接；复用外部 ``EvaluationDatabase`` 时不动它。"""
        if self._owns_db:
            self._db.close()

    # ----------------------------------------------------------------- 数据集

    def create_dataset(
        self,
        name: str,
        notes: str = "",
        cases: Sequence[Mapping[str, Any] | KnowledgeEvalCase] | None = None,
    ) -> dict[str, Any]:
        """建数据集；``cases`` 逐条过模型，``label_source`` 服务端推导。"""
        dataset_id = _new_id()
        now = _utc_now_iso()
        payloads = [_case_payload(case) for case in (cases or ())]
        with self._db.connect(immediate=True) as conn:
            conn.execute(
                "INSERT INTO knowledge_eval_datasets (id, name, notes, revision,"
                " cases_json, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?, ?)",
                (dataset_id, name, notes, _dump(payloads), now, now),
            )
        return {
            "id": dataset_id,
            "name": name,
            "notes": notes,
            "revision": 1,
            "cases": payloads,
            "created_at": now,
            "updated_at": now,
        }

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        """读数据集；不存在返回 ``None``，由调用方决定怎么报（404 或 404 之外）。"""
        with self._db.connect() as conn:
            row = self._dataset_row(conn, dataset_id)
        return None if row is None else self._dataset_dict(row)

    def list_datasets(self) -> list[dict[str, Any]]:
        """列出全部数据集（按创建时间、再按 id 稳定排序）。"""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT id, name, notes, revision, cases_json, created_at, updated_at"
                " FROM knowledge_eval_datasets ORDER BY created_at, id"
            ).fetchall()
            return [self._dataset_dict(row) for row in rows]

    def update_dataset(
        self,
        dataset_id: str,
        base_revision: int | str,
        name: str | None = None,
        notes: str | None = None,
        cases: Sequence[Mapping[str, Any] | KnowledgeEvalCase] | None = None,
    ) -> dict[str, Any]:
        """改数据集；``base_revision`` 与库内版本不符时抛乐观锁冲突。

        ``cases`` 是**整表替换**语义，不是增量；成功一次 ``revision`` 自增。
        """
        now = _utc_now_iso()
        with self._db.connect(immediate=True) as conn:
            row = self._dataset_row(conn, dataset_id)
            if row is None:
                raise KnowledgeEvalError(
                    CODE_DATASET_NOT_FOUND, f"知识评测数据集不存在：{dataset_id}"
                )
            current = int(row["revision"])
            requested = _as_revision(base_revision)
            if requested is None or requested != current:
                raise KnowledgeEvalError(
                    CODE_REVISION_CONFLICT,
                    f"数据集版本已更新（当前 {current}，请求 {base_revision}）",
                )
            next_revision = current + 1
            next_name = name if name is not None else str(row["name"])
            next_notes = notes if notes is not None else str(row["notes"])
            next_cases = (
                [_case_payload(case) for case in cases]
                if cases is not None
                else _load(row["cases_json"], [])
            )
            conn.execute(
                "UPDATE knowledge_eval_datasets SET name = ?, notes = ?, revision = ?,"
                " cases_json = ?, updated_at = ? WHERE id = ?",
                (
                    next_name,
                    next_notes,
                    next_revision,
                    _dump(next_cases),
                    now,
                    str(dataset_id),
                ),
            )
        return {
            "id": str(dataset_id),
            "name": next_name,
            "notes": next_notes,
            "revision": next_revision,
            "cases": next_cases,
            "created_at": str(row["created_at"]),
            "updated_at": now,
        }

    def delete_dataset(self, dataset_id: str) -> None:
        """删数据集（幂等）；连带删掉它名下的运行与结果。"""
        with self._db.connect(immediate=True) as conn:
            conn.execute(
                "DELETE FROM knowledge_eval_results WHERE run_id IN"
                " (SELECT id FROM knowledge_eval_runs WHERE dataset_id = ?)",
                (str(dataset_id),),
            )
            conn.execute(
                "DELETE FROM knowledge_eval_runs WHERE dataset_id = ?", (str(dataset_id),)
            )
            conn.execute(
                "DELETE FROM knowledge_eval_datasets WHERE id = ?", (str(dataset_id),)
            )

    # ----------------------------------------------------------------- 运行

    def create_run(
        self,
        dataset_id: str,
        configs: Sequence[Mapping[str, Any] | KnowledgeEvalConfigEntry],
        judge_config: Mapping[str, Any] | None = None,
        budgets: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """建一次运行（只登记，不执行检索）；数据集不存在直接报错。"""
        with self._db.connect(immediate=True) as conn:
            if self._dataset_row(conn, dataset_id) is None:
                raise KnowledgeEvalError(
                    CODE_DATASET_NOT_FOUND, f"知识评测数据集不存在：{dataset_id}"
                )
            run_id = _new_id()
            now = _utc_now_iso()
            config_payloads = [_model_payload(item) for item in configs]
            judge_payload = dict(judge_config or {})
            budget_payload = dict(budgets or {})
            conn.execute(
                "INSERT INTO knowledge_eval_runs (id, dataset_id, status, configs_json,"
                " judge_json, budgets_json, metrics_json, pool_json, progress_json,"
                " error, created_at, finished_at)"
                " VALUES (?, ?, 'queued', ?, ?, ?, '{}', '{}', '{}', NULL, ?, NULL)",
                (
                    run_id,
                    str(dataset_id),
                    _dump(config_payloads),
                    _dump(judge_payload),
                    _dump(budget_payload),
                    now,
                ),
            )
        return {
            "id": run_id,
            "dataset_id": str(dataset_id),
            "status": "queued",
            "configs": config_payloads,
            "judge": judge_payload,
            "budgets": budget_payload,
            "metrics": {},
            "pool": {},
            "progress": {},
            "error": None,
            "created_at": now,
            "finished_at": None,
        }

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """读一次运行；不存在返回 ``None``。"""
        with self._db.connect() as conn:
            row = self._run_row(conn, run_id)
        return None if row is None else self._run_dict(row)

    def list_runs(self, dataset_id: str | None = None) -> list[dict[str, Any]]:
        """列出运行；给了 ``dataset_id`` 就只列该数据集的。"""
        with self._db.connect() as conn:
            if dataset_id is None:
                rows = conn.execute(
                    "SELECT id, dataset_id, status, configs_json, judge_json,"
                    " budgets_json, metrics_json, pool_json, progress_json, error,"
                    " created_at, finished_at FROM knowledge_eval_runs"
                    " ORDER BY created_at, id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, dataset_id, status, configs_json, judge_json,"
                    " budgets_json, metrics_json, pool_json, progress_json, error,"
                    " created_at, finished_at FROM knowledge_eval_runs"
                    " WHERE dataset_id = ? ORDER BY created_at, id",
                    (str(dataset_id),),
                ).fetchall()
            return [self._run_dict(row) for row in rows]

    def finish_run(
        self,
        run_id: str,
        status: str,
        metrics: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """收尾一次运行：写终态、聚合指标与结束时间。"""
        now = _utc_now_iso()
        with self._db.connect(immediate=True) as conn:
            if self._run_row(conn, run_id) is None:
                raise KnowledgeEvalError(
                    CODE_RUN_NOT_FOUND, f"知识评测运行不存在：{run_id}"
                )
            conn.execute(
                "UPDATE knowledge_eval_runs SET status = ?, metrics_json = ?,"
                " error = ?, finished_at = ? WHERE id = ?",
                (status, _dump(dict(metrics or {})), error, now, str(run_id)),
            )

    def update_run_fields(self, run_id: str, **fields: Any) -> None:
        """更新运行级字段：``status`` / ``pool`` / ``progress``。

        runner 需要在运行途中写入降级标记与进度——这些是**非终态**写入，
        与 ``finish_run``（写终态与聚合指标）职责不同，故单独提供。

        只接受白名单键，其余一律拒绝（不接受任意列名）。
        """
        allowed = {"status", "pool", "progress"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported run fields: {sorted(unknown)}")
        if not fields:
            return

        assignments: list[str] = []
        values: list[Any] = []
        if "status" in fields:
            assignments.append("status = ?")
            values.append(str(fields["status"]))
        for column, key in (("pool_json", "pool"), ("progress_json", "progress")):
            if key in fields:
                assignments.append(f"{column} = ?")
                values.append(_dump(fields[key]))
        if not assignments:
            return
        values.append(str(run_id))
        with self._db.connect(immediate=True) as conn:
            if self._run_row(conn, run_id) is None:
                raise KnowledgeEvalError(
                    CODE_RUN_NOT_FOUND, f"知识评测运行不存在：{run_id}"
                )
            conn.execute(
                f"UPDATE knowledge_eval_runs SET {', '.join(assignments)} WHERE id = ?",
                values,
            )

    def mark_interrupted_runs(self) -> int:
        """服务重启时把残留 ``running`` 的运行置 ``interrupted``，返回处理条数。"""
        now = _utc_now_iso()
        with self._db.connect(immediate=True) as conn:
            cursor = conn.execute(
                "UPDATE knowledge_eval_runs SET status = 'interrupted', finished_at = ?"
                " WHERE status = 'running'",
                (now,),
            )
            return int(cursor.rowcount)

    # ----------------------------------------------------------------- 结果

    def save_result(
        self,
        run_id: str,
        case_id: str,
        status: str,
        label_source: str,
        hits: Sequence[Mapping[str, Any] | KnowledgeEvalHit] | None = None,
        metrics: Mapping[str, Any] | KnowledgeEvalCaseMetrics | None = None,
        error: str | None = None,
    ) -> None:
        """写一条逐题结果（upsert）。

        ``case_id`` 是 ``case_id::config_id`` 复合形式——同一条查询在多个检索配置下
        各存一行；重复写入同一行是覆盖，不产生重复行。
        """
        hits_payload = [_model_payload(item) for item in (hits or ())]
        metrics_payload: Any = _model_payload(metrics) if metrics is not None else {}
        with self._db.connect(immediate=True) as conn:
            if self._run_row(conn, run_id) is None:
                raise KnowledgeEvalError(
                    CODE_RUN_NOT_FOUND, f"知识评测运行不存在：{run_id}"
                )
            conn.execute(
                "INSERT INTO knowledge_eval_results (run_id, case_id, status,"
                " label_source, hits_json, metrics_json, error) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(run_id, case_id) DO UPDATE SET"
                " status = excluded.status, label_source = excluded.label_source,"
                " hits_json = excluded.hits_json, metrics_json = excluded.metrics_json,"
                " error = excluded.error",
                (
                    str(run_id),
                    str(case_id),
                    status,
                    label_source,
                    _dump(hits_payload),
                    _dump(metrics_payload),
                    error,
                ),
            )

    def get_run_report(self, run_id: str) -> dict[str, Any]:
        """运行详情 + 逐题结果列表（报告页的两段缺一不可）。"""
        with self._db.connect() as conn:
            row = self._run_row(conn, run_id)
            if row is None:
                raise KnowledgeEvalError(
                    CODE_RUN_NOT_FOUND, f"知识评测运行不存在：{run_id}"
                )
            result_rows = conn.execute(
                "SELECT case_id, status, label_source, hits_json, metrics_json, error"
                " FROM knowledge_eval_results WHERE run_id = ? ORDER BY case_id",
                (str(run_id),),
            ).fetchall()
            report = self._run_dict(row)
            report["results"] = [
                {
                    "run_id": str(run_id),
                    "case_id": str(item["case_id"]),
                    "status": str(item["status"]),
                    "label_source": str(item["label_source"]),
                    "hits": _load(item["hits_json"], []),
                    "metrics": _load(item["metrics_json"], {}),
                    "error": item["error"],
                }
                for item in result_rows
            ]
        return report

    # ----------------------------------------------------------------- 内部

    @staticmethod
    def _dataset_row(conn: sqlite3.Connection, dataset_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT id, name, notes, revision, cases_json, created_at, updated_at"
            " FROM knowledge_eval_datasets WHERE id = ?",
            (str(dataset_id),),
        ).fetchone()

    @staticmethod
    def _run_row(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT id, dataset_id, status, configs_json, judge_json, budgets_json,"
            " metrics_json, pool_json, progress_json, error, created_at, finished_at"
            " FROM knowledge_eval_runs WHERE id = ?",
            (str(run_id),),
        ).fetchone()

    @staticmethod
    def _dataset_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "notes": str(row["notes"]),
            "revision": int(row["revision"]),
            "cases": _load(row["cases_json"], []),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _run_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "dataset_id": str(row["dataset_id"]),
            "status": str(row["status"]),
            "configs": _load(row["configs_json"], []),
            "judge": _load(row["judge_json"], {}),
            "budgets": _load(row["budgets_json"], {}),
            "metrics": _load(row["metrics_json"], {}),
            "pool": _load(row["pool_json"], {}),
            "progress": _load(row["progress_json"], {}),
            "error": row["error"],
            "created_at": str(row["created_at"]),
            "finished_at": row["finished_at"],
        }


__all__ = [
    "CODE_DATASET_NOT_FOUND",
    "CODE_REVISION_CONFLICT",
    "CODE_RUN_NOT_FOUND",
    "KnowledgeEvalError",
    "KnowledgeEvalRepository",
]
