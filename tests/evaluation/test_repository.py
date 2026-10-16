"""T02 仓储层：版本 CAS、运行快照冻结、幂等入队与原子领取。

这些测试锁的是契约里最容易悄悄失效的三件事：

1. 编辑用例不能改写已经冻结的历史运行正文（plan §3「运行创建时冻结完整正文」）。
2. 幂等 key 的重放 / 冲突 / 未完成三条分支，以及软删运行不释放 key。
3. 领取必须靠 ``BEGIN IMMEDIATE`` + 状态谓词，不能出现同一 attempt 被两个执行器领走。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.repository import (
    EvaluationRepository,
    IdempotencyInProgress,
    IdempotencyMismatch,
    RevisionConflict,
    StateConflict,
)
from nanobot.evaluation.schemas import RunCreate

from .conftest import (
    make_case_payload,
    make_import_payload,
    make_run_payload,
    make_suite_payload,
)


@pytest.fixture
def repository(evaluation_repository: EvaluationRepository) -> EvaluationRepository:
    return evaluation_repository


def _seed_suite(repository: EvaluationRepository, **overrides: Any) -> dict[str, Any]:
    return repository.create_suite(make_suite_payload(**overrides))


def _seed_case(repository: EvaluationRepository, suite_id: str, **overrides: Any) -> dict[str, Any]:
    return repository.save_case(make_case_payload(suite_id=suite_id, **overrides))


def _profiles() -> dict[str, Any]:
    return {
        "agent": {"id": "agent-profile", "provider": "openai", "model": "gpt-test"},
        "judge": None,
        "rules_version": "rules-v1",
        "grader_version": "judge-v1",
    }


# ----------------------------------------------------------------- 用例集


def test_create_suite_starts_at_revision_one(repository: EvaluationRepository) -> None:
    suite = _seed_suite(repository)
    assert suite["revision"] == 1
    assert suite["case_count"] == 0
    assert suite["is_deleted"] is False
    assert suite["tags"] == ["basic-v1"]


def test_create_suite_rejects_unknown_field(repository: EvaluationRepository) -> None:
    with pytest.raises(ValidationError):
        repository.create_suite({**make_suite_payload(), "colour": "red"})


def test_update_suite_rejects_stale_base_revision(repository: EvaluationRepository) -> None:
    suite = _seed_suite(repository)
    with pytest.raises(RevisionConflict, match="REVISION_CONFLICT"):
        repository.update_suite(suite["id"], {**make_suite_payload(), "base_revision": 9})
    updated = repository.update_suite(
        suite["id"], {**make_suite_payload(), "name": "改名", "base_revision": 1}
    )
    assert updated["revision"] == 2 and updated["name"] == "改名"


def test_soft_deleted_suite_is_hidden_but_row_survives(repository: EvaluationRepository) -> None:
    suite = _seed_suite(repository)
    repository.delete_suite(suite["id"], base_revision=1)
    assert repository.get_suite(suite["id"])["is_deleted"] is True


def test_delete_suite_rejects_stale_base_revision(repository: EvaluationRepository) -> None:
    suite = _seed_suite(repository)
    with pytest.raises(RevisionConflict, match="REVISION_CONFLICT"):
        repository.delete_suite(suite["id"], base_revision=5)


# ----------------------------------------------------------------- 用例版本


def test_save_case_writes_revision_one(repository: EvaluationRepository) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    assert case["revision"] == 1
    assert case["turns"][0]["content"] == "读取 input.txt，把里面的标识原样告诉我。"


def test_edit_case_appends_a_new_revision(repository: EvaluationRepository) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    edited = repository.save_case(
        make_case_payload(
            suite_id=suite["id"], name="改名后的用例", base_revision=case["revision"]
        ),
        case_id=case["id"],
    )
    assert edited["revision"] == 2
    assert repository.get_case_revision(case["id"], 1)["name"] == case["name"]
    assert repository.get_case_revision(case["id"], 2)["name"] == "改名后的用例"


def test_save_case_without_case_id_requires_null_base_revision(
    repository: EvaluationRepository,
) -> None:
    suite = _seed_suite(repository)
    with pytest.raises(RevisionConflict, match="REVISION_CONFLICT"):
        repository.save_case(make_case_payload(suite_id=suite["id"], base_revision=1))


def test_stale_base_revision_is_rejected(repository: EvaluationRepository) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    repository.save_case(
        make_case_payload(suite_id=suite["id"], base_revision=1), case_id=case["id"]
    )
    with pytest.raises(RevisionConflict, match="REVISION_CONFLICT"):
        repository.save_case(
            make_case_payload(suite_id=suite["id"], base_revision=1), case_id=case["id"]
        )


def test_case_revision_body_is_immutable_even_via_repository(
    repository: EvaluationRepository,
) -> None:
    """仓储层是唯一写入方；绕过它直接改库也必须被数据库自身拒绝。"""
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    with pytest.raises(sqlite3.IntegrityError):
        with repository._db.connect() as conn:  # noqa: SLF001 - 白盒验证数据库护栏
            conn.execute(
                "UPDATE case_revisions SET definition_json = '{}'"
                " WHERE case_id = ? AND revision = 1",
                (case["id"],),
            )


# ----------------------------------------------------------------- 运行快照冻结


def test_run_freezes_case_body_before_a_later_edit(
    repository: EvaluationRepository,
) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    run = repository.create_run(
        scope="user-1", idempotency_key="k-run-1", request=request, **_profiles()
    )
    repository.save_case(
        make_case_payload(suite_id=suite["id"], name="改过名的用例", base_revision=1),
        case_id=case["id"],
    )

    fetched = repository.get_run(run["id"])
    snapshot = fetched["attempts"][0]["case_snapshot"]
    assert snapshot["name"] == case["name"]
    assert fetched["attempts"][0]["case_revision"] == 1
    # 最新用例正文已经是 rev2，但当次运行判分仍以冻结正文为准。
    assert repository.get_case(case["id"])["name"] == "改过名的用例"


def test_run_snapshot_contains_the_whole_case_definition(
    repository: EvaluationRepository,
) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    run = repository.create_run(
        scope="user-1", idempotency_key="k-snap", request=request, **_profiles()
    )
    snapshot = run["attempts"][0]["case_snapshot"]
    assert snapshot["assertions"][0]["kind"] == "answer_contains"
    assert snapshot["execution"]["max_iterations"] == 12


def test_run_snapshot_does_not_carry_base_revision(
    repository: EvaluationRepository,
) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    run = repository.create_run(
        scope="user-1", idempotency_key="k-no-br", request=request, **_profiles()
    )
    assert "base_revision" not in run["attempts"][0]["case_snapshot"]


def test_run_rejects_case_from_another_suite(repository: EvaluationRepository) -> None:
    suite_a = _seed_suite(repository)
    suite_b = _seed_suite(repository, name="另一个集合")
    case = _seed_case(repository, suite_b["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite_a["id"])
    )
    with pytest.raises(RevisionConflict, match="CASE_NOT_IN_SUITE"):
        repository.create_run(
            scope="user-1", idempotency_key="k-mix", request=request, **_profiles()
        )


def test_run_rejects_deleted_case(repository: EvaluationRepository) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    repository.delete_case(case["id"], base_revision=1)
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    with pytest.raises(RevisionConflict, match="CASE_NOT_FOUND"):
        repository.create_run(
            scope="user-1", idempotency_key="k-del", request=request, **_profiles()
        )


def test_run_snapshot_records_profiles_without_secrets(
    repository: EvaluationRepository,
) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    run = repository.create_run(
        scope="user-1", idempotency_key="k-prof", request=request, **_profiles()
    )
    snapshot = run["snapshot"]
    assert snapshot["agent_profile"]["model"] == "gpt-test"
    assert "api_key" not in str(snapshot)


# ----------------------------------------------------------------- 导入事务


def test_import_creates_suite_with_all_cases(repository: EvaluationRepository) -> None:
    result = repository.import_suite(make_import_payload(3))
    assert result["case_count"] == 3
    assert result["suite"]["case_count"] == 3


def test_import_ignores_incoming_case_ids(repository: EvaluationRepository) -> None:
    payload = make_import_payload(1)
    stale_id = payload["cases"][0]["id"]
    result = repository.import_suite(payload)
    assert result["cases"][0]["id"] != stale_id
    assert result["cases"][0]["suite_id"] == result["suite"]["id"]


def test_import_rolls_back_when_a_later_case_is_invalid(
    repository: EvaluationRepository,
) -> None:
    """契约 §3：单事务、全部合法才提交。第 200 项非法必须整批回滚。"""
    payload = make_import_payload(200)
    payload["cases"][199]["name"] = ""
    with pytest.raises(Exception):  # noqa: B017 - 具体异常类型由 CaseWrite 决定
        repository.import_suite(payload)

    with repository._db.connect() as conn:  # noqa: SLF001 - 白盒确认没有残留
        assert conn.execute("SELECT COUNT(*) FROM suites").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM case_revisions").fetchone()[0] == 0


def test_import_accepts_two_hundred_valid_cases(repository: EvaluationRepository) -> None:
    result = repository.import_suite(make_import_payload(200))
    assert result["case_count"] == 200


def test_repeated_sample_import_returns_the_original_suite(
    repository: EvaluationRepository,
) -> None:
    payload = make_import_payload(2)
    first = repository.import_sample("basic-v1", payload)
    second = repository.import_sample("basic-v1", make_import_payload(2))
    assert second["suite"]["id"] == first["suite"]["id"]
    with repository._db.connect() as conn:  # noqa: SLF001
        assert conn.execute("SELECT COUNT(*) FROM suites").fetchone()[0] == 1


# ----------------------------------------------------------------- 幂等


def test_request_digest_ignores_key_order() -> None:
    left = EvaluationRepository.request_digest({"b": 1, "a": [1, 2]})
    right = EvaluationRepository.request_digest({"a": [1, 2], "b": 1})
    assert left == right


def test_same_key_same_payload_replays_original_run(
    repository: EvaluationRepository,
) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    first = repository.create_run(
        scope="user-1", idempotency_key="k-1", request=request, **_profiles()
    )
    second = repository.create_run(
        scope="user-1", idempotency_key="k-1", request=request, **_profiles()
    )
    assert second["id"] == first["id"]
    with repository._db.connect() as conn:  # noqa: SLF001
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_same_key_different_payload_is_a_mismatch(
    repository: EvaluationRepository,
) -> None:
    suite = _seed_suite(repository)
    first_case = _seed_case(repository, suite["id"])
    second_case = _seed_case(repository, suite["id"], name="另一个用例")
    request_a = RunCreate.model_validate(
        make_run_payload([first_case["id"]], suite_id=suite["id"])
    )
    request_b = RunCreate.model_validate(
        make_run_payload([second_case["id"]], suite_id=suite["id"])
    )
    repository.create_run(
        scope="user-1", idempotency_key="k-2", request=request_a, **_profiles()
    )
    with pytest.raises(IdempotencyMismatch, match="IDEMPOTENCY_MISMATCH"):
        repository.create_run(
            scope="user-1", idempotency_key="k-2", request=request_b, **_profiles()
        )


def test_incomplete_reservation_reports_in_progress(
    repository: EvaluationRepository,
) -> None:
    """预留了 key 却没有写回响应的记录必须报未完成，而不是静默新建第二个运行。"""
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    digest = EvaluationRepository.request_digest(_run_digest_payload(request))
    with repository._db.connect() as conn:  # noqa: SLF001
        conn.execute(
            "INSERT INTO idempotency (scope, endpoint, key, request_hash, response_json,"
            " created_at) VALUES ('user-1', 'POST /runs', 'k-3', ?, NULL, 't0')",
            (digest,),
        )
    with pytest.raises(IdempotencyInProgress, match="IDEMPOTENCY_IN_PROGRESS"):
        repository.create_run(
            scope="user-1", idempotency_key="k-3", request=request, **_profiles()
        )


def test_soft_deleting_a_run_does_not_release_the_key(
    repository: EvaluationRepository,
) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    run = repository.create_run(
        scope="user-1", idempotency_key="k-4", request=request, **_profiles()
    )
    repository.claim_attempt()
    repository.finish_attempt(
        run["attempts"][0]["id"],
        result={"execution_status": "succeeded", "grading_status": "completed",
                "verdict": "passed", "result": {}},
    )
    repository.soft_delete_run(run["id"])

    replay = repository.create_run(
        scope="user-1", idempotency_key="k-4", request=request, **_profiles()
    )
    assert replay["id"] == run["id"]


def test_different_scope_may_reuse_the_same_key(repository: EvaluationRepository) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    first = repository.create_run(
        scope="user-1", idempotency_key="k-5", request=request, **_profiles()
    )
    second = repository.create_run(
        scope="user-2", idempotency_key="k-5", request=request, **_profiles()
    )
    assert first["id"] != second["id"]


def _run_digest_payload(request: RunCreate) -> dict[str, Any]:
    return json.loads(request.model_dump_json())


# ----------------------------------------------------------------- 领取与终态


def test_claim_returns_queued_attempt_in_position_order(
    repository: EvaluationRepository,
) -> None:
    suite = _seed_suite(repository)
    cases = [_seed_case(repository, suite["id"], name=f"用例 {i}") for i in range(2)]
    request = RunCreate.model_validate(
        make_run_payload([case["id"] for case in cases], suite_id=suite["id"])
    )
    repository.create_run(
        scope="user-1", idempotency_key="k-6", request=request, **_profiles()
    )
    first = repository.claim_attempt()
    second = repository.claim_attempt()
    assert first is not None and second is not None
    assert (first["position"], second["position"]) == (1, 2)
    assert repository.claim_attempt() is None


def test_claim_marks_execution_running(repository: EvaluationRepository) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    run = repository.create_run(
        scope="user-1", idempotency_key="k-7", request=request, **_profiles()
    )
    claimed = repository.claim_attempt()
    assert claimed["execution_status"] == "running"
    assert repository.get_run(run["id"])["status"] == "running"


def test_concurrent_claim_never_hands_out_the_same_attempt(tmp_path: Path) -> None:
    """两个独立连接同时领取：同一 attempt 只能被一个执行器拿到。"""
    path = tmp_path / "evaluation.db"
    seed = EvaluationDatabase(path)
    seed.init_schema()
    repository = EvaluationRepository(seed)
    suite = repository.create_suite(make_suite_payload())
    cases = [repository.save_case(make_case_payload(suite_id=suite["id"], name=f"用例 {i}"))
             for i in range(4)]
    request = RunCreate.model_validate(
        make_run_payload([case["id"] for case in cases], suite_id=suite["id"])
    )
    repository.create_run(
        scope="user-1", idempotency_key="k-8", request=request, **_profiles()
    )

    competitor = EvaluationRepository(EvaluationDatabase(path))
    claimed: list[str | None] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def _drain(repo: EvaluationRepository) -> None:
        barrier.wait()
        taken: list[str] = []
        while True:
            attempt = repo.claim_attempt()
            if attempt is None:
                break
            taken.append(attempt["id"])
        with lock:
            claimed.append(",".join(taken) if taken else None)

    threads = [
        threading.Thread(target=_drain, args=(repository,)),
        threading.Thread(target=_drain, args=(competitor,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    taken_ids = [item for chunk in claimed if chunk for item in chunk.split(",")]
    assert len(taken_ids) == len(set(taken_ids)) == 4


def test_claim_skips_attempts_of_a_deleted_run(repository: EvaluationRepository) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    run = repository.create_run(
        scope="user-1", idempotency_key="k-9", request=request, **_profiles()
    )
    with repository._db.connect() as conn:  # noqa: SLF001
        conn.execute("UPDATE runs SET is_deleted = 1 WHERE id = ?", (run["id"],))
    assert repository.claim_attempt() is None


def test_finish_attempt_records_result_and_completes_run(
    repository: EvaluationRepository,
) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    run = repository.create_run(
        scope="user-1", idempotency_key="k-10", request=request, **_profiles()
    )
    claimed = repository.claim_attempt()
    repository.finish_attempt(
        claimed["id"],
        result={"execution_status": "succeeded", "grading_status": "completed",
                "verdict": "passed", "result": {"assertions": []}},
    )
    fetched = repository.get_run(run["id"])
    assert fetched["status"] == "completed"
    assert fetched["attempts"][0]["verdict"] == "passed"
    assert fetched["attempts"][0]["finished_at"] is not None


def test_finish_attempt_requires_a_claimable_state(
    repository: EvaluationRepository,
) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    run = repository.create_run(
        scope="user-1", idempotency_key="k-11", request=request, **_profiles()
    )
    claimed = repository.claim_attempt()
    repository.finish_attempt(
        claimed["id"],
        result={"execution_status": "succeeded", "grading_status": "completed",
                "verdict": "passed", "result": {}},
    )
    with pytest.raises(StateConflict, match="ATTEMPT_NOT_FINISHABLE"):
        repository.finish_attempt(
            claimed["id"],
            result={"execution_status": "failed", "grading_status": "pending",
                    "verdict": "failed", "result": {}},
        )
    assert repository.get_run(run["id"])["attempts"][0]["execution_status"] == "succeeded"


def test_cancel_run_is_rejected_for_terminal_runs(
    repository: EvaluationRepository,
) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    run = repository.create_run(
        scope="user-1", idempotency_key="k-12", request=request, **_profiles()
    )
    claimed = repository.claim_attempt()
    repository.finish_attempt(
        claimed["id"],
        result={"execution_status": "succeeded", "grading_status": "completed",
                "verdict": "passed", "result": {}},
    )
    with pytest.raises(StateConflict, match="RUN_NOT_CANCELLABLE"):
        repository.cancel_run(run["id"])


def test_cancel_queued_run_stops_further_claims(
    repository: EvaluationRepository,
) -> None:
    """契约 §4：取消先落 cancelling（等调度器回收进程树），领取随即停止。"""
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    run = repository.create_run(
        scope="user-1", idempotency_key="k-13", request=request, **_profiles()
    )
    cancelling = repository.cancel_run(run["id"])
    assert cancelling["status"] == "cancelling"
    assert repository.claim_attempt() is None

    finalized = repository.finalize_cancel(run["id"])
    assert finalized["status"] == "cancelled"
    assert finalized["finished_at"] is not None


def test_cancel_is_idempotent_while_cancelling(repository: EvaluationRepository) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    run = repository.create_run(
        scope="user-1", idempotency_key="k-13b", request=request, **_profiles()
    )
    repository.cancel_run(run["id"])
    assert repository.cancel_run(run["id"])["status"] == "cancelling"


def test_soft_delete_run_requires_a_terminal_state(
    repository: EvaluationRepository,
) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    run = repository.create_run(
        scope="user-1", idempotency_key="k-14", request=request, **_profiles()
    )
    with pytest.raises(StateConflict, match="RUN_NOT_TERMINAL"):
        repository.soft_delete_run(run["id"])


# ----------------------------------------------------------------- 人工复核与产物


def test_append_review_is_append_only(repository: EvaluationRepository) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    repository.create_run(
        scope="user-1", idempotency_key="k-15", request=request, **_profiles()
    )
    claimed = repository.claim_attempt()
    repository.append_review(claimed["id"], "failed", "产物缺失", reviewer="local-user")
    repository.append_review(claimed["id"], "passed", "复核后通过", reviewer="local-user")
    reviews = repository.list_reviews(claimed["id"])
    assert [item["verdict"] for item in reviews] == ["failed", "passed"]
    assert repository.get_attempt(claimed["id"])["verdict"] == "not_evaluated"


def test_append_review_rejects_unknown_verdict(repository: EvaluationRepository) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    repository.create_run(
        scope="user-1", idempotency_key="k-16", request=request, **_profiles()
    )
    claimed = repository.claim_attempt()
    with pytest.raises(ValueError, match="VALIDATION_ERROR"):
        repository.append_review(claimed["id"], "maybe", "x", reviewer="local-user")


def test_artifact_relative_path_cannot_escape(repository: EvaluationRepository) -> None:
    suite = _seed_suite(repository)
    case = _seed_case(repository, suite["id"])
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    repository.create_run(
        scope="user-1", idempotency_key="k-17", request=request, **_profiles()
    )
    claimed = repository.claim_attempt()
    artifact = repository.append_artifact(
        claimed["id"], relative_path="out/result.json",
        media_type="application/json", size_bytes=2, sha256="a" * 64,
    )
    assert artifact["relative_path"] == "out/result.json"
    with pytest.raises(ValueError, match="PATH_OUTSIDE_ROOT"):
        repository.append_artifact(
            claimed["id"], relative_path="../escape.json",
            media_type="application/json", size_bytes=2, sha256="b" * 64,
        )


# ----------------------------------------------------------------- 校准与 gold 样本


def _gold_sample(index: int) -> dict[str, Any]:
    return {
        "id": f"C{index:02d}",
        "task": f"任务 {index}",
        "rubric": {"version": "general-v1", "required": True, "dimensions": []},
        "evidence": {"answer": "x"},
        "expected_verdict": "passed",
        "expected_scores": [{"dimension_id": "task_completion", "score": 3}],
    }


def test_seeded_gold_samples_start_as_proposed(repository: EvaluationRepository) -> None:
    """契约 §3.1：内置标签默认 proposed，AI 不能替用户标成已确认。"""
    repository.seed_gold_samples([_gold_sample(i) for i in range(1, 4)], gold_revision=1)
    listing = repository.list_gold_samples()
    assert listing["gold_revision"] == 1
    assert {item["label_status"] for item in listing["data"]} == {"proposed"}
    assert all(item["reviewer"] is None for item in listing["data"])


def test_reviewing_a_gold_sample_confirms_and_bumps_revision(
    repository: EvaluationRepository,
) -> None:
    repository.seed_gold_samples([_gold_sample(1)], gold_revision=1)
    updated = repository.review_gold_sample("C01", {
        "base_gold_revision": 1, "expected_verdict": "failed",
        "expected_scores": [{"dimension_id": "task_completion", "score": 0}],
        "comment": "人工确认：不通过", "confirm": True,
    })
    assert updated["label_status"] == "confirmed"
    assert updated["reviewer"] == "local-user"
    assert updated["expected_verdict"] == "failed"
    assert repository.list_gold_samples()["gold_revision"] == 2


def test_reviewing_with_a_stale_gold_revision_conflicts(
    repository: EvaluationRepository,
) -> None:
    repository.seed_gold_samples([_gold_sample(1)], gold_revision=3)
    with pytest.raises(RevisionConflict, match="REVISION_CONFLICT"):
        repository.review_gold_sample("C01", {
            "base_gold_revision": 1, "expected_verdict": "passed",
            "expected_scores": [], "comment": "", "confirm": True,
        })


def test_calibration_result_passes_only_at_or_above_eighty_percent(
    repository: EvaluationRepository,
) -> None:
    """契约 §3.1：至少 8/10 匹配才算通过。"""
    passed = repository.record_calibration_result(
        judge_fingerprint="fp-a", rubric_version="general-v1",
        gold_revision=1, total=10, matched=8,
    )
    failed = repository.record_calibration_result(
        judge_fingerprint="fp-b", rubric_version="general-v1",
        gold_revision=1, total=10, matched=7,
    )
    assert passed["passed"] is True and passed["agreement"] == 0.8
    assert failed["passed"] is False


def test_calibration_agreement_is_null_for_an_empty_set(
    repository: EvaluationRepository,
) -> None:
    empty = repository.record_calibration_result(
        judge_fingerprint="fp-c", rubric_version="general-v1",
        gold_revision=1, total=0, matched=0,
    )
    assert empty["agreement"] is None and empty["passed"] is None


def test_calibration_result_is_scoped_by_fingerprint_rubric_and_gold(
    repository: EvaluationRepository,
) -> None:
    """契约 §3.1：新量表不能借用 general-v1 的校准结果。"""
    repository.record_calibration_result(
        judge_fingerprint="fp-a", rubric_version="general-v1",
        gold_revision=1, total=10, matched=10,
    )
    found = repository.find_calibration_result(
        judge_fingerprint="fp-a", rubric_version="general-v1", gold_revision=1
    )
    assert found is not None and found["passed"] is True
    assert repository.find_calibration_result(
        judge_fingerprint="fp-a", rubric_version="custom-v1", gold_revision=1
    ) is None
    assert repository.find_calibration_result(
        judge_fingerprint="fp-a", rubric_version="general-v1", gold_revision=2
    ) is None


def test_calibration_job_reports_progress_then_cancels(
    repository: EvaluationRepository,
) -> None:
    created = repository.create_calibration(
        snapshot={"judge_fingerprint": "fp-a"}, sample_ids=["C01", "C02", "C03"]
    )
    assert created["status"] == "queued"
    assert created["total"] == 3 and created["completed"] == 0
    cancelled = repository.cancel_calibration(created["id"])
    assert cancelled["status"] == "cancelled"
    assert repository.cancel_calibration(created["id"])["status"] == "cancelled"


def test_list_cases_keeps_disabled_cases_and_drops_soft_deleted_ones(
    repository: EvaluationRepository,
) -> None:
    """契约 §2：停用是 CaseWrite.enabled=false（仍是用例），软删才是 is_deleted。"""
    suite = _seed_suite(repository)
    for index in range(3):
        _seed_case(repository, suite["id"], name=f"用例 {index}")
    disabled = _seed_case(repository, suite["id"], name="停用用例", enabled=False)
    removed = _seed_case(repository, suite["id"], name="软删用例")
    repository.delete_case(removed["id"], base_revision=1)

    listed = repository.list_cases(suite["id"])
    # 契约 §1：固定排序 created_at, id 降序。
    assert [item["name"] for item in listed] == ["停用用例", "用例 2", "用例 1", "用例 0"]
    assert listed[0]["enabled"] is False
    assert listed[0]["id"] == disabled["id"]
