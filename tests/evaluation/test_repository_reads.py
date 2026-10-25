"""GROUP-3 仓储读出层：列表查询、计数桶、终态口径与校准项推进。

这些测试锁的是 WU-05 服务层与 T10 路由层依赖的读出契约，其中两条是本批修正的
真实缺陷：

1. 契约 §4 的 ``terminal_count`` 要求「执行与评分都终结」。原实现只看
   execution_status，会把「Agent 已结束、Judge 仍在跑」的 attempt 算成完成，
   run 也会提前收成 completed。
2. 校准项此前没有任何领取/提交方法，作业状态永远停在 running。
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from nanobot.evaluation.repository import EvaluationRepository, StateConflict
from nanobot.evaluation.schemas import RunCreate

from .conftest import (
    make_case_payload,
    make_run_payload,
    make_suite_payload,
)


@pytest.fixture
def repository(evaluation_repository: EvaluationRepository) -> EvaluationRepository:
    return evaluation_repository


def _seed_run(
    repository: EvaluationRepository, *, case_count: int = 2
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    suite = repository.create_suite(make_suite_payload())
    cases = [
        repository.save_case(make_case_payload(suite_id=suite["id"], name=f"用例 {i}"))
        for i in range(case_count)
    ]
    request = RunCreate.model_validate(
        make_run_payload([case["id"] for case in cases], suite_id=suite["id"])
    )
    run = repository.create_run(
        scope="local-user",
        idempotency_key="key-read-1",
        request=request,
        agent={"provider": "controlled", "model": "eval-model"},
        judge=None,
        rules_version="rules-v1",
        grader_version="judge-v1",
    )
    return run, cases


def _finish(
    repository: EvaluationRepository,
    attempt_id: str,
    *,
    execution_status: str = "succeeded",
    grading_status: str = "completed",
    verdict: str = "passed",
) -> None:
    repository.finish_attempt(
        attempt_id,
        result={
            "execution_status": execution_status,
            "grading_status": grading_status,
            "verdict": verdict,
            "result": {},
        },
    )


# --------------------------------------------------------------------- 运行列表


def test_list_runs_returns_suite_name_and_both_counts(repository: EvaluationRepository) -> None:
    run, _ = _seed_run(repository, case_count=3)
    _finish(repository, run["attempts"][0]["id"])

    rows = repository.list_runs()
    assert len(rows) == 1
    assert rows[0]["id"] == run["id"]
    assert rows[0]["suite_name"] == "基础回归用例集"
    assert rows[0]["planned_count"] == 3
    # 只有 1 项执行与评分都终结。
    assert rows[0]["terminal_count"] == 1


def test_list_runs_filters_by_status_and_suite(repository: EvaluationRepository) -> None:
    run, _ = _seed_run(repository, case_count=1)
    assert [row["id"] for row in repository.list_runs(status="queued")] == [run["id"]]
    assert repository.list_runs(status="completed") == []
    assert repository.list_runs(suite_id="00000000-0000-4000-8000-000000000000") == []
    assert repository.list_runs(suite_id=run["suite_id"]) != []


def test_list_runs_excludes_soft_deleted_runs(repository: EvaluationRepository) -> None:
    run, _ = _seed_run(repository, case_count=1)
    for attempt in run["attempts"]:
        _finish(repository, attempt["id"])
    assert repository.get_run(run["id"])["status"] == "completed"
    repository.soft_delete_run(run["id"])

    assert repository.list_runs() == []


def test_list_runs_filters_by_parent_run(repository: EvaluationRepository) -> None:
    run, cases = _seed_run(repository, case_count=1)
    child = repository.create_run(
        scope="local-user",
        idempotency_key="key-read-child",
        request=RunCreate.model_validate(
            make_run_payload([cases[0]["id"]], suite_id=run["suite_id"])
        ),
        agent={"provider": "controlled", "model": "eval-model"},
        judge=None,
        rules_version="rules-v1",
        grader_version="judge-v1",
        parent_run_id=run["id"],
    )
    assert [row["id"] for row in repository.list_runs(parent_run_id=run["id"])] == [
        child["id"]
    ]


# ------------------------------------------------------------------- 终态口径


def test_agent_done_with_judge_pending_is_not_terminal(
    repository: EvaluationRepository,
) -> None:
    """契约 §4：Agent finished 而 Judge pending 不算整体完成。"""
    run, _ = _seed_run(repository, case_count=1)
    attempt_id = run["attempts"][0]["id"]
    repository.claim_attempt()
    repository.mark_attempt_grading_running(attempt_id)
    repository.mark_attempt_execution_done(attempt_id, execution_status="succeeded")

    rows = repository.list_runs()
    assert rows[0]["terminal_count"] == 0
    assert repository.get_run(run["id"])["status"] == "running"

    # 评分回来后才收口。
    _finish(repository, attempt_id)
    assert repository.list_runs()[0]["terminal_count"] == 1
    assert repository.get_run(run["id"])["status"] == "completed"


def test_judge_error_counts_as_grading_terminal(repository: EvaluationRepository) -> None:
    run, _ = _seed_run(repository, case_count=1)
    repository.claim_attempt()
    _finish(
        repository,
        run["attempts"][0]["id"],
        grading_status="judge_error",
        verdict="review_required",
    )
    assert repository.list_runs()[0]["terminal_count"] == 1


def test_queued_attempts_land_in_pending_bucket(
    repository: EvaluationRepository,
) -> None:
    run, _ = _seed_run(repository, case_count=2)
    counts = repository.run_counts(run["id"])
    assert counts == {
        "pending": 2,
        "passed": 0,
        "failed": 0,
        "review_required": 0,
        "not_evaluated": 0,
    }


def test_open_runs_and_open_attempts_cover_recovery_rows(
    repository: EvaluationRepository,
) -> None:
    run, _ = _seed_run(repository, case_count=2)
    repository.claim_attempt()
    assert [row["id"] for row in repository.open_runs()] == [run["id"]]
    open_attempts = repository.list_open_attempts()
    assert len(open_attempts) == 2
    assert {row["execution_status"] for row in open_attempts} == {"queued", "running"}


def test_mark_interrupted_finishes_queued_items_without_rerun(
    repository: EvaluationRepository,
) -> None:
    run, _ = _seed_run(repository, case_count=3)
    repository.claim_attempt()
    repository.mark_interrupted(run["id"])

    detail = repository.get_run(run["id"])
    assert detail["status"] == "interrupted"
    statuses = {item["execution_status"] for item in detail["attempts"]}
    assert statuses == {"interrupted"}
    assert all(item["verdict"] == "not_evaluated" for item in detail["attempts"])
    assert repository.list_runs()[0]["terminal_count"] == 3
    # 幂等：已终结的运行再标一次不改变结果。
    assert repository.mark_interrupted(run["id"])["status"] == "interrupted"


# ------------------------------------------------------------------- 尝试列表


def test_list_run_attempts_carries_case_name_and_filters(repository: EvaluationRepository) -> None:
    run, _ = _seed_run(repository, case_count=2)
    _finish(repository, run["attempts"][0]["id"], verdict="failed")
    rows = repository.list_run_attempts(run["id"])
    assert [row["position"] for row in rows] == [1, 2]
    assert rows[0]["case_name"] == "用例 0"
    assert rows[0]["case_revision"] >= 1

    failed = repository.list_run_attempts(run["id"], verdict="failed")
    assert len(failed) == 1
    assert repository.list_run_attempts(run["id"], execution_status="queued") != []
    assert repository.list_run_attempts(run["id"], execution_status="succeeded") != []


def test_list_run_attempts_only_exposes_frozen_snapshot_to_internal_callers(
    repository: EvaluationRepository,
) -> None:
    run, _ = _seed_run(repository, case_count=1)
    public = repository.list_run_attempts(run["id"])
    internal = repository.list_run_attempts(run["id"], include_snapshot=True)
    assert "case_snapshot" not in public[0]
    assert internal[0]["case_snapshot"]["name"] == "用例 0"


def test_list_run_attempts_rejects_unknown_run(repository: EvaluationRepository) -> None:
    from nanobot.evaluation.repository import RecordNotFound

    with pytest.raises(RecordNotFound):
        repository.list_run_attempts("00000000-0000-4000-8000-000000000000")


# --------------------------------------------------------------------- 产物行


def test_get_artifact_only_from_registered_row(repository: EvaluationRepository) -> None:
    run, _ = _seed_run(repository, case_count=1)
    attempt_id = run["attempts"][0]["id"]
    registered = repository.append_artifact(
        attempt_id=attempt_id,
        relative_path="output.txt",
        media_type="text/plain",
        size_bytes=6,
        sha256="a" * 64,
    )
    found = repository.get_artifact(registered["id"])
    assert found["attempt_id"] == attempt_id
    assert found["relative_path"] == "output.txt"

    from nanobot.evaluation.repository import RecordNotFound

    with pytest.raises(RecordNotFound):
        repository.get_artifact("00000000-0000-4000-8000-000000000000")


def test_list_artifacts_preserves_registration_order(repository: EvaluationRepository) -> None:
    run, _ = _seed_run(repository, case_count=1)
    attempt_id = run["attempts"][0]["id"]
    for name in ("a.txt", "b.txt"):
        repository.append_artifact(
            attempt_id=attempt_id,
            relative_path=name,
            media_type="text/plain",
            size_bytes=1,
            sha256="b" * 64,
        )
    rows = repository.list_artifacts_for_attempt(attempt_id)
    assert [row["relative_path"] for row in rows] == ["a.txt", "b.txt"]


# --------------------------------------------------------------------- 校准项


def _calibration(repository: EvaluationRepository, *, count: int = 3) -> dict[str, Any]:
    return repository.create_calibration(
        snapshot={"judge_fingerprint": "fp-1", "rubric_version": "general-v1"},
        sample_ids=[f"g{i}" for i in range(count)],
    )


def test_claim_calibration_item_is_unique_and_starts_job(
    repository: EvaluationRepository,
) -> None:
    job = _calibration(repository, count=2)
    first = repository.claim_calibration_item()
    second = repository.claim_calibration_item()
    assert first is not None and second is not None
    assert first["id"] != second["id"]
    assert {first["position"], second["position"]} == {1, 2}
    assert repository.get_calibration(job["id"])["status"] == "running"
    assert repository.claim_calibration_item() is None


def test_concurrent_claim_calibration_items_never_duplicates(repository: EvaluationRepository) -> None:
    _calibration(repository, count=4)
    claimed: list[dict[str, Any]] = []
    barrier = threading.Barrier(3)

    def worker() -> None:
        barrier.wait()
        while True:
            item = repository.claim_calibration_item()
            if item is None:
                return
            claimed.append(item)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(claimed) == 4
    assert len({item["id"] for item in claimed}) == 4


def test_calibration_writes_passed_only_when_all_items_succeed(
    repository: EvaluationRepository,
) -> None:
    job = _calibration(repository, count=2)
    for _ in range(2):
        item = repository.claim_calibration_item()
        repository.finish_calibration_item(
            item["id"], status="completed", result={"matched": True}
        )
    detail = repository.get_calibration(job["id"])
    assert detail["status"] == "completed"
    assert detail["result"]["agreement"] == 1.0
    assert detail["result"]["passed"] is True


def test_calibration_agreement_stays_null_on_item_failure(repository: EvaluationRepository) -> None:
    job = _calibration(repository, count=2)
    first = repository.claim_calibration_item()
    repository.finish_calibration_item(first["id"], status="completed",
                                      result={"matched": False})
    second = repository.claim_calibration_item()
    repository.finish_calibration_item(second["id"], status="failed",
                                       result={"error": "judge timeout"})

    detail = repository.get_calibration(job["id"])
    assert detail["status"] == "cancelled"
    # 未全部成功就不能给 agreement，否则「跑完了但一致率是零」会被误读。
    assert detail["result"]["agreement"] is None
    assert detail["result"]["passed"] is None


def test_finish_calibration_item_rejects_double_submit(repository: EvaluationRepository) -> None:
    _calibration(repository, count=1)
    item = repository.claim_calibration_item()
    repository.finish_calibration_item(item["id"], status="completed",
                                      result={"matched": True})
    with pytest.raises(StateConflict):
        repository.finish_calibration_item(item["id"], status="completed",
                                          result={"matched": True})


def test_cancelled_calibration_stops_claiming_items(repository: EvaluationRepository) -> None:
    job = _calibration(repository, count=3)
    repository.cancel_calibration(job["id"])
    assert repository.claim_calibration_item() is None


def test_restart_marks_residual_calibration_interrupted(repository: EvaluationRepository) -> None:
    job = _calibration(repository, count=2)
    repository.claim_calibration_item()
    assert [row["id"] for row in repository.open_calibrations()] == [job["id"]]
    repository.mark_calibration_interrupted(job["id"])

    detail = repository.get_calibration(job["id"])
    assert detail["status"] == "interrupted"
    assert repository.claim_calibration_item() is None


# ------------------------------------------------------------------- gold 版本


def test_gold_revision_advances_on_confirmation(repository: EvaluationRepository) -> None:
    repository.seed_gold_samples(
        [{"id": "g1", "task": "t", "rubric": {}, "evidence": {},
          "expected_verdict": "passed", "expected_scores": []}],
        gold_revision=1,
    )
    assert repository.gold_revision() >= 1
    before = repository.gold_revision()
    repository.review_gold_sample(
        "g1",
        {
            "base_gold_revision": before,
            "expected_verdict": "passed",
            "expected_scores": [{"dimension_id": "task_completion", "score": 3}],
            "comment": "人工确认",
        },
    )
    assert repository.gold_revision() == before + 1
