"""记忆专项持久化（契约 §1 / §2 / §5 / §7）。

与旧评估域一样：**事务归仓储，连接归 database**。每个公开方法只开一个连接、在一个
事务里完成全部写入，绝不嵌套 ``connect()``——``EvaluationDatabase.connect`` 已经是
RLock + 显式事务，嵌套会自锁。

三条硬约束：

1. **gold 与执行载荷分列存储**：``memory_scenarios.execution_json`` 里没有 gold，
   ``gold_json`` 单独一列；读执行载荷时走 :func:`build_execution_input`，构造点唯一。
2. **导入幂等 + 原子发布**：同一 ``dataset_version`` 重复导入返回原修订（摘要不同
   才报冲突）；``expected_count`` 与 ``observed_count`` 校验通过后**在同一事务内**
   把修订与导入作业一起发布为 ready。
3. **比较读快照**：``memory_comparisons`` 同时存结果与引用快照，历史报告不读当前
   可变 case 重算。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.memory.models import (
    MemoryBudgets,
    MemoryCandidate,
    MemoryComparison,
    MemoryDatasetRevision,
    MemoryDatasetSource,
    MemoryExecutionInput,
    MemoryExperiment,
    MemoryExperimentRun,
    MemoryGold,
    MemoryGradingPolicy,
    MemoryScenario,
    build_execution_input,
)

#: 契约 §2：单子 run 的尝试上限（每个子 run 仍 <=200）。
MAX_RUN_ATTEMPTS = 200
#: 契约 §2：单个子 run 的最大场景数。
MAX_BATCH_SIZE = 40

_MEMORY_REVISION_STATUSES = ("staging", "ready", "invalid")
_IMPORT_STATUSES = ("staging", "running", "ready", "invalid", "cancelled")


class MemoryRecordNotFound(LookupError):  # noqa: N818 - 名称由错误码映射锁定
    """请求的记忆资源在本库中不存在。"""


class MemoryStateConflict(ValueError):  # noqa: N818
    """资源当前状态不允许该变更。"""


class MemoryRevisionConflict(ValueError):  # noqa: N818
    """同一 dataset_version 的内容摘要不一致。"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(raw: str | None, default: Any) -> Any:
    return default if raw is None else json.loads(raw)


def _digest(payload: Any) -> str:
    return hashlib.sha256(_dump(payload).encode("utf-8")).hexdigest()


def plan_batches(scenario_ids: Sequence[str], repetitions: int) -> list[list[str]]:
    """契约 §2 的分批公式：``min(40, floor(200 / repetitions))``。

    批次大小与重复次数共同保证「每个子 run 的尝试数 <= 200」。
    """
    if not 1 <= repetitions <= 10:
        raise ValueError("MEMORY_REPETITIONS_INVALID")
    size = min(MAX_BATCH_SIZE, MAX_RUN_ATTEMPTS // repetitions)
    return [
        list(scenario_ids[start : start + size])
        for start in range(0, len(scenario_ids), size)
    ]


class MemoryRepository:
    """记忆专项的唯一写入口；一个服务实例持有一个。"""

    def __init__(self, db: EvaluationDatabase) -> None:
        self._db = db

    # ----------------------------------------------------------------- 集合版本

    def import_revision(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """导入一个数据集修订：staging → 写入 → 校验 → 同一事务内发布 ready。

        重复导入同一 ``dataset_version`` 且内容摘要一致时返回原修订（幂等）；摘要
        不一致时抛 :class:`MemoryRevisionConflict`——静默覆盖会让历史实验失去可复现
        的输入。
        """
        suite_id = str(uuid.UUID(str(payload["suite_id"])))
        dataset_version = str(payload["dataset_version"])
        source = MemoryDatasetSource.model_validate(payload["source"])
        raw_scenarios = payload.get("scenarios")
        if not isinstance(raw_scenarios, list) or not raw_scenarios:
            raise ValueError("MEMORY_SCENARIOS_EMPTY")
        scenarios = [
            MemoryScenario.model_validate(item) for item in raw_scenarios
        ]
        self._check_split_isolation(scenarios)
        digest = _digest(
            {
                "dataset_version": dataset_version,
                "source": source.model_dump(mode="json"),
                "scenarios": [
                    scenario.model_dump(mode="json")
                    for scenario in sorted(scenarios, key=lambda item: item.scenario_id)
                ],
            }
        )
        split_counts: dict[str, int] = {}
        for scenario in scenarios:
            split_counts[scenario.split] = split_counts.get(scenario.split, 0) + 1

        now = _utc_now_iso()
        with self._db.connect(immediate=True) as conn:
            existing = conn.execute(
                "SELECT id, definition_digest FROM memory_dataset_revisions"
                " WHERE suite_id = ? AND dataset_version = ?",
                (suite_id, dataset_version),
            ).fetchone()
            if existing is not None:
                if existing["definition_digest"] != digest:
                    raise MemoryRevisionConflict(
                        "REVISION_CONFLICT dataset_version="
                        f"{dataset_version} suite={suite_id}"
                    )
                revision = self._revision_dict(conn, str(existing["id"]))
                return {
                    "dataset_revision": revision,
                    "scenario_count": len(scenarios),
                    "import_job_id": None,
                }
            self._require_suite(conn, suite_id)
            job_id = _new_id()
            revision_id = _new_id()
            conn.execute(
                "INSERT INTO memory_import_jobs (id, suite_id, status, expected_count,"
                " observed_count, dataset_revision_id, source_json, errors_json,"
                " created_at, updated_at) VALUES (?, ?, 'staging', ?, NULL, NULL, ?, '[]', ?, ?)",
                (
                    job_id,
                    suite_id,
                    len(scenarios),
                    _dump(source.model_dump(mode="json")),
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO memory_dataset_revisions (id, suite_id, dataset_version,"
                " status, split_counts_json, source_json, definition_digest, created_at,"
                " updated_at) VALUES (?, ?, ?, 'staging', ?, ?, ?, ?, ?)",
                (
                    revision_id,
                    suite_id,
                    dataset_version,
                    _dump(split_counts),
                    _dump(source.model_dump(mode="json")),
                    digest,
                    now,
                    now,
                ),
            )
            for scenario in scenarios:
                self._insert_scenario(conn, revision_id, scenario, now)
            observed = conn.execute(
                "SELECT COUNT(*) FROM memory_scenarios WHERE dataset_revision_id = ?",
                (revision_id,),
            ).fetchone()[0]
            if int(observed) != len(scenarios):
                # 计数对不上就不发布：宁可留在 staging，也不让半个集合变成 ready。
                conn.execute(
                    "UPDATE memory_import_jobs SET status = 'invalid', observed_count = ?,"
                    " updated_at = ? WHERE id = ?",
                    (int(observed), now, job_id),
                )
                raise MemoryStateConflict(
                    f"MEMORY_IMPORT_COUNT_MISMATCH expected={len(scenarios)}"
                    f" observed={observed}"
                )
            conn.execute(
                "UPDATE memory_import_jobs SET status = 'ready', observed_count = ?,"
                " dataset_revision_id = ?, updated_at = ? WHERE id = ?",
                (int(observed), revision_id, now, job_id),
            )
            conn.execute(
                "UPDATE memory_dataset_revisions SET status = 'ready', updated_at = ?"
                " WHERE id = ?",
                (now, revision_id),
            )
            revision = self._revision_dict(conn, revision_id)
        return {
            "dataset_revision": revision,
            "scenario_count": len(scenarios),
            "import_job_id": job_id,
        }

    def save_scenario(
        self, dataset_revision_id: str, scenario_id: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        """保存一个场景的新修订（编辑产生新 revision，旧修订与 gold 保持不变）。"""
        scenario = MemoryScenario.model_validate(payload)
        if scenario.scenario_id != scenario_id:
            raise ValueError("MEMORY_SCENARIO_ID_MISMATCH")
        now = _utc_now_iso()
        with self._db.connect(immediate=True) as conn:
            self._require_revision(conn, dataset_revision_id)
            self._insert_scenario(conn, dataset_revision_id, scenario, now, replace=True)
            row = conn.execute(
                "SELECT scenario_id, revision FROM memory_scenarios"
                " WHERE dataset_revision_id = ? AND scenario_id = ? AND revision = ?",
                (dataset_revision_id, scenario_id, scenario.revision),
            ).fetchone()
        if row is None:
            raise MemoryRecordNotFound(
                f"MEMORY_SCENARIO_NOT_FOUND {scenario_id}@{scenario.revision}"
            )
        return {
            "dataset_revision_id": dataset_revision_id,
            "scenario_id": row["scenario_id"],
            "revision": row["revision"],
        }

    def get_scenario(
        self, dataset_revision_id: str, scenario_id: str, *, revision: int | None = None
    ) -> dict[str, Any]:
        """读取场景（管理面读出：含 gold）。"""
        with self._db.connect() as conn:
            row = self._scenario_row(conn, dataset_revision_id, scenario_id, revision)
        execution = _load(row["execution_json"], {})
        gold = _load(row["gold_json"], {})
        return MemoryScenario.model_validate({**execution, "gold": gold}).model_dump(
            mode="json"
        )

    def get_execution_input(
        self,
        dataset_revision_id: str,
        scenario_id: str,
        *,
        revision: int | None = None,
        track: str,
        candidates: Sequence[Mapping[str, Any] | MemoryCandidate] = (),
    ) -> dict[str, Any]:
        """读取执行载荷：**剥离 gold、来源许可与评分判据**。

        构造点是 :func:`build_execution_input`——执行载荷里不可能出现 gold，因为它
        根本不在入参里。
        """
        parsed = [
            item if isinstance(item, MemoryCandidate) else MemoryCandidate.model_validate(item)
            for item in candidates
        ]
        with self._db.connect() as conn:
            row = self._scenario_row(conn, dataset_revision_id, scenario_id, revision)
        execution = _load(row["execution_json"], {})
        scenario = MemoryScenario.model_validate(
            {**execution, "gold": _load(row["gold_json"], {})}
        )
        payload: MemoryExecutionInput = build_execution_input(
            scenario, track=track, candidates=parsed
        )
        return payload.model_dump(mode="json")

    def get_gold(
        self, dataset_revision_id: str, scenario_id: str, *, revision: int | None = None
    ) -> dict[str, Any]:
        """读取私有 gold（评分端专用）。"""
        with self._db.connect() as conn:
            row = self._scenario_row(conn, dataset_revision_id, scenario_id, revision)
        gold = MemoryGold.model_validate(_load(row["gold_json"], {}))
        return gold.model_dump(mode="json")

    def list_dataset_revisions(self, suite_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM memory_dataset_revisions WHERE suite_id = ?"
                " ORDER BY created_at, id",
                (str(suite_id),),
            ).fetchall()
            return [self._revision_dict(conn, str(row["id"])) for row in rows]

    def get_dataset_revision(self, dataset_revision_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            return self._revision_dict(conn, dataset_revision_id)

    # ----------------------------------------------------------------- 实验

    def create_experiment_plan(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """冻结实验计划：实验行与全部子 run 在**同一事务**内落库。

        ``plan_key`` 给出时按幂等键处理：同一 ``(suite_id, plan_key)`` 重复提交返回
        原计划，不重复创建子 run。
        """
        suite_id = str(uuid.UUID(str(payload["suite_id"])))
        dataset_revision_id = str(uuid.UUID(str(payload["dataset_revision_id"])))
        candidates = [
            MemoryCandidate.model_validate(item) for item in payload.get("candidates") or []
        ]
        if not candidates:
            raise ValueError("MEMORY_CANDIDATES_EMPTY")
        repetitions = int(payload.get("repetitions") or 1)
        if not 1 <= repetitions <= 10:
            raise ValueError("MEMORY_REPETITIONS_INVALID")
        policy = MemoryGradingPolicy.model_validate(payload["policy"])
        budgets = MemoryBudgets.model_validate(payload.get("budgets") or {})
        track = str(payload["track"])
        split = str(payload["split"])
        primary_metric = str(payload["primary_metric"])
        plan_key = payload.get("plan_key")
        now = _utc_now_iso()
        experiment_id = _new_id()
        with self._db.connect(immediate=True) as conn:
            self._require_revision(conn, dataset_revision_id, require_ready=True)
            if plan_key is not None:
                replay = conn.execute(
                    "SELECT id FROM memory_experiments WHERE suite_id = ? AND plan_key = ?",
                    (suite_id, str(plan_key)),
                ).fetchone()
                if replay is not None:
                    experiment = self._experiment_dict(conn, str(replay["id"]))
                    return {
                        "experiment": experiment,
                        "child_runs": experiment["child_runs"],
                    }
            scenario_ids = self._resolve_scenario_ids(
                conn, dataset_revision_id, split, payload.get("scenario_ids")
            )
            if not scenario_ids:
                raise ValueError("MEMORY_SCENARIOS_EMPTY")
            scenario_batches = plan_batches(scenario_ids, repetitions)
            planned_trials = len(scenario_ids) * repetitions * len(candidates)
            conn.execute(
                "INSERT INTO memory_experiments (id, suite_id, dataset_revision_id, split,"
                " track, candidates_json, repetitions, model_mode, judge_profile_id,"
                " budgets_json, primary_metric, policy_json, manifest_json, status,"
                " measurement_status, planned_trials, plan_key, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 'incomplete',"
                " ?, ?, ?, ?)",
                (
                    experiment_id,
                    suite_id,
                    dataset_revision_id,
                    split,
                    track,
                    _dump([item.model_dump(mode="json") for item in candidates]),
                    repetitions,
                    str(payload.get("model_mode") or "real"),
                    payload.get("judge_profile_id"),
                    _dump(budgets.model_dump(mode="json")),
                    primary_metric,
                    _dump(policy.model_dump(mode="json")),
                    _dump(
                        {
                            "dataset_revision_id": dataset_revision_id,
                            "scenario_ids": scenario_ids,
                            "batch_size": len(scenario_batches[0]) if scenario_batches else 0,
                        }
                    ),
                    planned_trials,
                    None if plan_key is None else str(plan_key),
                    now,
                    now,
                ),
            )
            for candidate in candidates:
                for batch_index, batch in enumerate(scenario_batches):
                    conn.execute(
                        "INSERT INTO memory_experiment_runs (id, experiment_id, candidate_id,"
                        " batch_index, run_id, scenario_ids_json, status, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)",
                        (
                            _new_id(),
                            experiment_id,
                            candidate.id,
                            batch_index,
                            _new_id(),
                            _dump(batch),
                            now,
                        ),
                    )
            experiment = self._experiment_dict(conn, experiment_id)
        return {"experiment": experiment, "child_runs": experiment["child_runs"]}

    def get_experiment(self, experiment_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            experiment = self._experiment_dict(conn, experiment_id)
        return {"experiment": experiment, "child_runs": experiment["child_runs"]}

    # ----------------------------------------------------------------- 比较

    def save_comparison(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """写入一次配对比较：结果与引用快照一起冻结。

        之后即使场景被修订，历史比较也不会被重算——它读的是自己那份快照。
        """
        model = MemoryComparison.model_validate(
            {
                **dict(payload),
                "id": payload.get("id") or _new_id(),
                "status": payload.get("status") or "completed",
                "created_at": payload.get("created_at") or _utc_now_iso(),
            }
        )
        now = _utc_now_iso()
        with self._db.connect(immediate=True) as conn:
            conn.execute(
                "INSERT INTO memory_comparisons (id, baseline_experiment_id,"
                " candidate_experiment_id, baseline_candidate_id, candidate_candidate_id,"
                " changed_fields_json, policy_json, status, result_json, snapshot_json,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(model.id),
                    str(model.baseline_experiment_id),
                    str(model.candidate_experiment_id),
                    model.baseline_candidate_id,
                    model.candidate_candidate_id,
                    _dump(model.changed_fields),
                    _dump(model.policy.model_dump(mode="json")),
                    model.status,
                    _dump(model.result),
                    _dump(model.snapshot),
                    now,
                ),
            )
            stored = self._comparison_dict(conn, str(model.id))
        return stored

    def get_comparison(self, comparison_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            return self._comparison_dict(conn, comparison_id)

    # ----------------------------------------------------------------- 内部

    @staticmethod
    def _check_split_isolation(scenarios: Sequence[MemoryScenario]) -> None:
        """契约 §1：同一 template_family 不得跨 split 复用（否则调参集会串题）。"""
        families: dict[str, str] = {}
        for scenario in scenarios:
            seen = families.setdefault(scenario.template_family, scenario.split)
            if seen != scenario.split:
                raise ValueError(
                    f"TEMPLATE_FAMILY_CROSS_SPLIT family={scenario.template_family}"
                    f" splits={seen},{scenario.split}"
                )

    @staticmethod
    def _require_suite(conn: sqlite3.Connection, suite_id: str) -> None:
        if conn.execute("SELECT 1 FROM suites WHERE id = ?", (suite_id,)).fetchone() is None:
            raise MemoryRecordNotFound(f"SUITE_NOT_FOUND {suite_id}")

    @staticmethod
    def _require_revision(
        conn: sqlite3.Connection, dataset_revision_id: str, *, require_ready: bool = False
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT id, suite_id, dataset_version, status, split_counts_json, source_json,"
            " definition_digest, created_at, updated_at FROM memory_dataset_revisions"
            " WHERE id = ?",
            (dataset_revision_id,),
        ).fetchone()
        if row is None:
            raise MemoryRecordNotFound(
                f"MEMORY_DATASET_NOT_FOUND {dataset_revision_id}"
            )
        if require_ready and row["status"] != "ready":
            raise MemoryStateConflict(
                f"MEMORY_DATASET_NOT_READY status={row['status']}"
            )
        return row

    @staticmethod
    def _insert_scenario(
        conn: sqlite3.Connection,
        dataset_revision_id: str,
        scenario: MemoryScenario,
        now: str,
        *,
        replace: bool = False,
    ) -> None:
        payload = scenario.model_dump(mode="json")
        gold_payload = payload.pop("gold")
        if replace:
            conn.execute(
                "DELETE FROM memory_scenarios WHERE dataset_revision_id = ?"
                " AND scenario_id = ? AND revision = ?",
                (dataset_revision_id, scenario.scenario_id, scenario.revision),
            )
        conn.execute(
            "INSERT INTO memory_scenarios (dataset_revision_id, scenario_id, revision,"
            " case_id, case_revision, split, template_family, cluster_id, execution_json,"
            " gold_json, created_at) VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)",
            (
                dataset_revision_id,
                scenario.scenario_id,
                scenario.revision,
                scenario.split,
                scenario.template_family,
                scenario.cluster_id,
                _dump(payload),
                _dump(gold_payload),
                now,
            ),
        )

    @staticmethod
    def _scenario_row(
        conn: sqlite3.Connection,
        dataset_revision_id: str,
        scenario_id: str,
        revision: int | None,
    ) -> sqlite3.Row:
        if revision is None:
            row = conn.execute(
                "SELECT execution_json, gold_json FROM memory_scenarios"
                " WHERE dataset_revision_id = ? AND scenario_id = ?"
                " ORDER BY revision DESC LIMIT 1",
                (dataset_revision_id, scenario_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT execution_json, gold_json FROM memory_scenarios"
                " WHERE dataset_revision_id = ? AND scenario_id = ? AND revision = ?",
                (dataset_revision_id, scenario_id, revision),
            ).fetchone()
        if row is None:
            raise MemoryRecordNotFound(
                f"MEMORY_SCENARIO_NOT_FOUND {scenario_id}"
            )
        return row

    @staticmethod
    def _resolve_scenario_ids(
        conn: sqlite3.Connection,
        dataset_revision_id: str,
        split: str,
        requested: Sequence[str] | None,
    ) -> list[str]:
        if requested:
            return [str(item) for item in requested]
        rows = conn.execute(
            "SELECT DISTINCT scenario_id FROM memory_scenarios"
            " WHERE dataset_revision_id = ? AND split = ? ORDER BY scenario_id",
            (dataset_revision_id, split),
        ).fetchall()
        return [str(row["scenario_id"]) for row in rows]

    @staticmethod
    def _revision_dict(conn: sqlite3.Connection, dataset_revision_id: str) -> dict[str, Any]:
        row = MemoryRepository._require_revision(conn, dataset_revision_id)
        revision = MemoryDatasetRevision.model_validate(
            {
                "id": str(row["id"]),
                "suite_id": str(row["suite_id"]),
                "dataset_version": row["dataset_version"],
                "status": row["status"],
                "split_counts": _load(row["split_counts_json"], {}),
                "source": _load(row["source_json"], {}),
                "definition_digest": row["definition_digest"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        return revision.model_dump(mode="json")

    @staticmethod
    def _experiment_dict(conn: sqlite3.Connection, experiment_id: str) -> dict[str, Any]:
        row = conn.execute(
            "SELECT id, suite_id, dataset_revision_id, split, track, candidates_json,"
            " repetitions, model_mode, judge_profile_id, budgets_json, primary_metric,"
            " policy_json, manifest_json, status, measurement_status, planned_trials,"
            " created_at, updated_at FROM memory_experiments WHERE id = ?",
            (experiment_id,),
        ).fetchone()
        if row is None:
            raise MemoryRecordNotFound(f"MEMORY_EXPERIMENT_NOT_FOUND {experiment_id}")
        run_rows = conn.execute(
            "SELECT id, experiment_id, candidate_id, batch_index, run_id, scenario_ids_json,"
            " status, created_at FROM memory_experiment_runs WHERE experiment_id = ?"
            " ORDER BY candidate_id, batch_index, id",
            (experiment_id,),
        ).fetchall()
        child_runs = [
            MemoryExperimentRun.model_validate(
                {
                    "id": str(item["id"]),
                    "experiment_id": str(item["experiment_id"]),
                    "candidate_id": item["candidate_id"],
                    "batch_index": item["batch_index"],
                    "run_id": item["run_id"],
                    "scenario_ids": _load(item["scenario_ids_json"], []),
                    "status": item["status"],
                    "created_at": item["created_at"],
                }
            ).model_dump(mode="json")
            for item in run_rows
        ]
        experiment = MemoryExperiment.model_validate(
            {
                "id": str(row["id"]),
                "suite_id": str(row["suite_id"]),
                "dataset_revision_id": str(row["dataset_revision_id"]),
                "split": row["split"],
                "track": row["track"],
                "candidates": _load(row["candidates_json"], []),
                "repetitions": row["repetitions"],
                "model_mode": row["model_mode"],
                "judge_profile_id": row["judge_profile_id"],
                "budgets": _load(row["budgets_json"], {}),
                "primary_metric": row["primary_metric"],
                "policy": _load(row["policy_json"], {}),
                "manifest": _load(row["manifest_json"], {}),
                "status": row["status"],
                "measurement_status": row["measurement_status"],
                "planned_trials": row["planned_trials"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "child_runs": child_runs,
            }
        )
        return experiment.model_dump(mode="json")

    @staticmethod
    def _comparison_dict(conn: sqlite3.Connection, comparison_id: str) -> dict[str, Any]:
        row = conn.execute(
            "SELECT id, baseline_experiment_id, candidate_experiment_id,"
            " baseline_candidate_id, candidate_candidate_id, changed_fields_json,"
            " policy_json, status, result_json, snapshot_json, created_at"
            " FROM memory_comparisons WHERE id = ?",
            (comparison_id,),
        ).fetchone()
        if row is None:
            raise MemoryRecordNotFound(f"MEMORY_COMPARISON_NOT_FOUND {comparison_id}")
        comparison = MemoryComparison.model_validate(
            {
                "id": str(row["id"]),
                "baseline_experiment_id": str(row["baseline_experiment_id"]),
                "candidate_experiment_id": str(row["candidate_experiment_id"]),
                "baseline_candidate_id": row["baseline_candidate_id"],
                "candidate_candidate_id": row["candidate_candidate_id"],
                "changed_fields": _load(row["changed_fields_json"], []),
                "policy": _load(row["policy_json"], {}),
                "status": row["status"],
                "result": _load(row["result_json"], {}),
                "snapshot": _load(row["snapshot_json"], {}),
                "created_at": row["created_at"],
            }
        )
        return comparison.model_dump(mode="json")


__all__ = [
    "MemoryRecordNotFound",
    "MemoryRepository",
    "MemoryRevisionConflict",
    "MemoryStateConflict",
    "plan_batches",
]
