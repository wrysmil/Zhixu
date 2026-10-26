"""T02 运行清单与 v2 存储契约。

覆盖四件事：

1. 目标摘要随被测对象变化、与环境无关（技能文件改一个字，target 变、environment 不变）。
2. 摘要里没有凭据与绝对路径（否则换台机器同一次运行算出不同摘要，比较失去意义）。
3. v2 类型边界（repetitions / model_mode / 用例判据）按契约拒绝非法输入。
4. v1 库可迁移到 v2，旧 attempt 回填 trial_index=0 且历史数据不被改写。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from nanobot.evaluation.database import _MIGRATIONS, SCHEMA_VERSION, EvaluationDatabase
from nanobot.evaluation.manifests import build_manifest
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.schemas import CaseWrite, RunCreate

from .conftest import make_case_payload, make_run_payload, make_suite_payload


def _case_entry(
    *, case_payload: dict[str, Any] | None = None, case_id: str = "case-1"
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "case_revision": 1,
        "case_snapshot": case_payload or make_case_payload(),
    }


def _skill_target(skill_text: str) -> dict[str, Any]:
    return {
        "kind": "skill",
        "id": "demo",
        "skill": {"name": "demo", "files": {"skills/demo/SKILL.md": skill_text}},
    }


def _build(
    *,
    skill_text: str = "版本一",
    case_payload: dict[str, Any] | None = None,
    agent_profile: dict[str, Any] | None = None,
    model_mode: str = "real",
) -> dict[str, Any]:
    return build_manifest(
        case_snapshots=[_case_entry(case_payload=case_payload)],
        model_mode=model_mode,
        tool_modes=["fixture"],
        limits={"concurrency": 1, "max_iterations": 12, "timeout_s": 120},
        target_snapshot=_skill_target(skill_text),
        agent_profile=agent_profile or {"provider": "controlled", "model": "eval"},
        judge_profile=None,
        rules_version="rules-v1",
        grader_version="judge-v1",
    )


# --------------------------------------------------------------- 目标与环境摘要


def test_manifest_changes_when_skill_changes() -> None:
    """被测技能变一个字符，target/candidate 摘要必须变；环境摘要必须不变。"""
    before = _build(skill_text="版本一")
    after = _build(skill_text="版本二")
    assert before["target_digest"] != after["target_digest"]
    assert before["candidate_digest"] != after["candidate_digest"]
    assert before["environment_digest"] == after["environment_digest"]
    assert before["grading_digest"] == after["grading_digest"]


def test_manifest_dataset_digest_changes_with_case_definition() -> None:
    before = _build(case_payload=make_case_payload())
    after = _build(case_payload=make_case_payload(description="换了描述"))
    assert before["dataset_digest"] != after["dataset_digest"]
    assert before["environment_digest"] == after["environment_digest"]


def test_manifest_excludes_credentials_and_absolute_paths() -> None:
    """凭据与本机绝对路径都不能进摘要，否则同一次运行换台机器就对不上。"""
    with_secret = _build(
        agent_profile={"provider": "p", "model": "m", "api_key": "sk-SECRET"},
        skill_text="读取 C:/Users/me/private/notes.md",
    )
    without_secret = _build(
        agent_profile={"provider": "p", "model": "m"},
        skill_text="读取 D:/other/machine/notes.md",
    )
    assert "sk-SECRET" not in json.dumps(with_secret, ensure_ascii=False)
    assert with_secret["target_digest"] == without_secret["target_digest"]
    assert with_secret["candidate_digest"] == without_secret["candidate_digest"]


def test_manifest_reports_unknown_source_state_explicitly() -> None:
    manifest = _build()
    assert manifest["version"] == 2
    assert manifest["model_mode"] == "real"
    assert manifest["tool_modes"] == ["fixture"]
    # 拿不到代码版本时必须是 null + 原因，不允许假装已知。
    assert manifest["source_revision"] is None or isinstance(manifest["source_revision"], str)
    if manifest["source_revision"] is None:
        assert manifest["source_revision_reason"]


# --------------------------------------------------------------- v2 类型边界


def test_run_create_rejects_bool_repetitions() -> None:
    case_id = str(uuid.uuid4())
    with pytest.raises(ValidationError):
        RunCreate.model_validate(make_run_payload([case_id], repetitions=True))


def test_run_create_rejects_out_of_range_repetitions() -> None:
    case_id = str(uuid.uuid4())
    for value in (0, 11):
        with pytest.raises(ValidationError):
            RunCreate.model_validate(make_run_payload([case_id], repetitions=value))


def test_run_create_rejects_total_attempts_over_budget() -> None:
    case_ids = [str(uuid.uuid4()) for _ in range(101)]
    with pytest.raises(ValidationError, match="ATTEMPT_BUDGET_EXCEEDED"):
        RunCreate.model_validate(make_run_payload(case_ids, repetitions=2))


def test_case_evaluation_requires_task_criteria() -> None:
    """要求 task 维度时，必须有可验证里程碑或必需量表，否则只能算 smoke。"""
    payload = make_case_payload()
    payload["evaluation"] = {
        "version": 2,
        "required_dimensions": ["task"],
        "milestones": [],
    }
    with pytest.raises(ValidationError, match="TASK_CRITERIA_MISSING"):
        CaseWrite.model_validate(payload)


def test_case_evaluation_accepts_milestone_or_judge() -> None:
    payload = make_case_payload()
    payload["evaluation"] = {
        "version": 2,
        "required_dimensions": ["task", "constraints"],
        "milestones": [{"id": "m1", "weight": 1.0, "assertion_ids": ["a1"]}],
        "required_evidence": ["final_answer"],
    }
    case = CaseWrite.model_validate(payload)
    assert case.evaluation is not None
    assert case.evaluation.milestones[0].assertion_ids == ["a1"]


# --------------------------------------------------------------- 存储与迁移


def _v1_database(path: Path) -> EvaluationDatabase:
    """只落 v1 DDL 的库，用来验证追加迁移不依赖"全新建库"。"""
    database = EvaluationDatabase(path)
    with database.connect() as conn:
        for statement in _MIGRATIONS[0][1]:
            conn.execute(statement)
        conn.execute(
            "INSERT OR REPLACE INTO _schema_meta (key, value) VALUES ('version', '1')"
        )
    return database


def test_v1_database_migrates_to_v2_and_backfills_trials(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    database = _v1_database(path)
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO suites (id, revision, name, description, tags_json,"
            " is_deleted, created_at, updated_at)"
            " VALUES ('suite-1', 1, '旧集', '', '[]', 0, 'now', 'now')"
        )
        conn.execute(
            "INSERT INTO runs (id, suite_id, status, snapshot_json, request_hash,"
            " is_deleted, created_at) VALUES"
            " ('run-1', 'suite-1', 'completed', '{}', 'hash', 0, 'now')"
        )
        conn.execute(
            "INSERT INTO attempts (id, run_id, position, case_id, case_revision,"
            " case_snapshot_json, execution_status, grading_status, verdict)"
            " VALUES ('att-1', 'run-1', 1, 'case-1', 1, '{}', 'succeeded',"
            " 'completed', 'passed')"
        )

    database.init_schema()

    assert database.schema_version() == int(SCHEMA_VERSION) == 2
    with database.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(attempts)")}
        row = conn.execute(
            "SELECT trial_index, source_attempt_id FROM attempts WHERE id = 'att-1'"
        ).fetchone()
    assert {"trial_index", "source_attempt_id"} <= columns
    assert row["trial_index"] == 0
    assert row["source_attempt_id"] is None


def test_repetitions_create_independent_attempts(
    evaluation_repository: EvaluationRepository,
) -> None:
    suite = evaluation_repository.create_suite(make_suite_payload())
    cases = [
        evaluation_repository.save_case(
            make_case_payload(suite_id=suite["id"], name=f"case-{index}")
        )
        for index in range(2)
    ]
    request = RunCreate.model_validate(
        make_run_payload(
            [case["id"] for case in cases],
            suite_id=suite["id"],
            repetitions=3,
        )
    )
    run = evaluation_repository.create_run(
        scope="test",
        idempotency_key="manifest-repetitions",
        request=request,
        rules_version="rules-v1",
        grader_version="judge-v1",
        agent={"provider": "controlled", "model": "eval"},
        judge=None,
    )
    attempts = run["attempts"]
    assert len(attempts) == 6
    assert len({item["id"] for item in attempts}) == 6
    assert sorted(item["trial_index"] for item in attempts) == [0, 0, 1, 1, 2, 2]
    assert run["snapshot"]["repetitions"] == 3
    assert run["snapshot"]["manifest"]["version"] == 2
    assert run["snapshot"]["manifest"]["dataset_case_count"] == 2


def test_rerun_attempt_records_source_attempt(
    evaluation_repository: EvaluationRepository,
) -> None:
    suite = evaluation_repository.create_suite(make_suite_payload())
    case = evaluation_repository.save_case(make_case_payload(suite_id=suite["id"]))
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    first = evaluation_repository.create_run(
        scope="test",
        idempotency_key="source-run",
        request=request,
        rules_version="rules-v1",
        grader_version="judge-v1",
        agent={"provider": "controlled", "model": "eval"},
        judge=None,
    )
    source_attempt = first["attempts"][0]["id"]
    rerun = evaluation_repository.create_run(
        scope="test",
        idempotency_key="rerun-run",
        request=request,
        rules_version="rules-v1",
        grader_version="judge-v1",
        agent={"provider": "controlled", "model": "eval"},
        judge=None,
        frozen_cases=[
            {
                "case_id": case["id"],
                "case_revision": 1,
                "case_snapshot": first["attempts"][0]["case_snapshot"],
                "source_attempt_id": source_attempt,
            }
        ],
    )
    assert rerun["attempts"][0]["source_attempt_id"] == source_attempt
