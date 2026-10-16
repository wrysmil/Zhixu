"""T09 服务层：执行流水线、判分编排与读出装配。

这些用例跑的是**真实 Worker 子进程**（``python -m nanobot.evaluation.worker``），
受控 provider 按 fixture 返回，不触网。验证的是三件事：

1. 流水线顺序：执行 → 规则 → 按需 Judge → 合并 verdict，且只有一次提交。
2. 凭据不出内存：``profile_bundle`` 不落库、不进命令行、不进 Trace。
3. 读出装配：RunRead/AttemptRead/TracePage 的形状与契约一致。

「真实 provider 冒烟」与本文件的受控 provider 是两件事，这里不声称前者已验证。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.scheduler import Scheduler, SchedulerConfig
from nanobot.evaluation.schemas import (
    AttemptRead,
    GoldSamplePage,
    RunRead,
    RunSummary,
    TracePage,
)
from nanobot.evaluation.services import EvaluationService, Profile

from .conftest import (
    make_case_payload,
    make_rubric_payload,
    make_run_payload,
    make_suite_payload,
)

API_KEY = "sk-sentinel-eval-secret-0000"


@pytest.fixture
def repository(evaluation_repository: EvaluationRepository) -> EvaluationRepository:
    return evaluation_repository


@pytest.fixture
def service(repository: EvaluationRepository, tmp_path: Path) -> EvaluationService:
    return EvaluationService(
        repository=repository,
        data_dir=tmp_path / "service",
        profiles=(
            Profile(id="agent-1", label="受控执行器", provider="controlled",
                    model="eval-model", kind="agent",
                    bundle={"config": {"providers": {"controlled": {"api_key": API_KEY}}}}),
            Profile(id="judge-1", label="受控评分器", provider="controlled",
                    model="eval-model", kind="judge"),
        ),
    )


async def _drive(
    repository: EvaluationRepository, service: EvaluationService, run_id: str
) -> dict[str, Any]:
    """用真实调度器把运行跑到终态。"""
    scheduler = Scheduler(
        repository=repository,
        pipeline=service,
        config=SchedulerConfig(
            data_dir=service._data_dir,  # noqa: SLF001 - 测试与调度器共用同一 data_dir
            concurrency=1,
            poll_interval_s=0.01,
        ),
    )
    async with scheduler:
        import asyncio

        deadline = asyncio.get_running_loop().time() + 60
        while asyncio.get_running_loop().time() < deadline:
            status = repository.get_run(run_id)["status"]
            if status in ("completed", "cancelled", "interrupted"):
                return repository.get_run(run_id)
            await asyncio.sleep(0.05)
    raise AssertionError("run did not settle")


def _seed_run(
    repository: EvaluationRepository,
    service: EvaluationService,
    *,
    case_count: int = 1,
    judge: bool = False,
    key: str = "svc-1",
) -> dict[str, Any]:
    suite = repository.create_suite(make_suite_payload())
    payload = make_case_payload(suite_id=suite["id"], name="读取 input.txt")
    payload["script"] = [
        {"text": "", "tool_calls": [{"name": "read_file", "arguments": {"path": "input.txt"}}]},
        {"text": "标识是 alpha。"},
    ]
    if judge:
        payload["rubric"] = make_rubric_payload()
    case = repository.save_case(payload)
    return service.create_run(
        _run_request([case["id"]], suite["id"], judge=judge),
        idempotency_key=key,
        scope="local-user",
    )


def _run_request(case_ids: list[str], suite_id: str, *, judge: bool) -> Any:
    from nanobot.evaluation.schemas import RunCreate

    return RunCreate.model_validate(
        make_run_payload(
            case_ids, suite_id=suite_id, agent_profile_id="agent-1",
            judge_profile_id="judge-1" if judge else None,
        )
    )


# ----------------------------------------------------------------- 流水线


async def test_pipeline_grades_and_passes_a_good_case(
    repository: EvaluationRepository, service: EvaluationService
) -> None:
    run = _seed_run(repository, service)
    detail = await _drive(repository, service, run["id"])

    assert detail["status"] == "completed"
    attempt = detail["attempts"][0]
    assert attempt["execution_status"] == "succeeded"
    assert attempt["grading_status"] == "completed"
    assert attempt["verdict"] == "passed"
    result = attempt["result"]
    assert result["errors"] == []
    assert any(item["id"] == "a1" and item["passed"] for item in result["assertions"])
    assert result["trace_complete"] is True


async def test_failing_required_assertion_yields_failed_not_error(
    repository: EvaluationRepository, service: EvaluationService
) -> None:
    suite = repository.create_suite(make_suite_payload())
    payload = make_case_payload(
        suite_id=suite["id"], name="不可能满足",
        assertions=[{
            "id": "never", "kind": "answer_contains", "required": True,
            "text": "这段文字不可能出现在回答里", "case_sensitive": True,
        }],
    )
    payload["script"] = [{"text": "我的回答里没有那句禁词。"}]
    case = repository.save_case(payload)
    run = service.create_run(
        _run_request([case["id"]], suite["id"], judge=False),
        idempotency_key="svc-fail", scope="local-user",
    )
    detail = await _drive(repository, service, run["id"])
    attempt = detail["attempts"][0]
    assert attempt["verdict"] == "failed"
    assert attempt["grading_status"] == "completed"


async def test_required_rubric_without_judge_profile_is_rejected(
    repository: EvaluationRepository, service: EvaluationService
) -> None:
    """契约 §4：所选用例 rubric.required=true 时 judge_profile_id 必填。

    RunCreate 只看得到 id，看不到用例正文，所以这条判定只能在服务层做。
    """
    from nanobot.evaluation.services import ServiceError

    suite = repository.create_suite(make_suite_payload())
    payload = make_case_payload(suite_id=suite["id"], rubric=make_rubric_payload())
    payload["script"] = [{"text": "标识是 alpha。"}]
    case = repository.save_case(payload)
    request = _run_request([case["id"]], suite["id"], judge=False)
    with pytest.raises(ServiceError) as caught:
        service.create_run(request, idempotency_key="svc-nojudge", scope="local-user")
    assert caught.value.code == "JUDGE_PROFILE_REQUIRED"


async def test_secrets_never_reach_database_trace_or_command_line(
    repository: EvaluationRepository, service: EvaluationService
) -> None:
    """凭据只在内存：库、Trace 目录、Worker 命令行里都搜不到 sentinel。"""
    run = _seed_run(repository, service)
    detail = await _drive(repository, service, run["id"])
    assert detail["status"] == "completed"

    # 1) 数据库快照：逐表扫全文，不只查 runs.snapshot_json
    with repository._db.connect() as conn:  # noqa: SLF001 - 测试直接扫库
        for (table,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall():
            for row in conn.execute(f"SELECT * FROM {table}").fetchall():  # noqa: S608
                if API_KEY in repr(row):
                    raise AssertionError(f"API_KEY leaked into table {table}")

    # 2) Trace 与产物落盘
    for path in service.runtime_root.rglob("*"):
        if path.is_file():
            body = path.read_bytes()
            assert API_KEY.encode() not in body, f"API_KEY leaked into {path}"

    # 3) 读出的运行快照里没有 profile_bundle
    read = service.run_read(run["id"])
    assert "bundle" not in json.dumps(read["snapshot"], ensure_ascii=False)


async def test_run_read_and_attempt_read_match_contract_shapes(
    repository: EvaluationRepository, service: EvaluationService
) -> None:
    run = _seed_run(repository, service)
    await _drive(repository, service, run["id"])

    read = RunRead.model_validate(service.run_read(run["id"]))
    assert read.status == "completed"
    assert read.planned_count == 1
    assert read.terminal_count == 1
    assert read.counts.passed == 1
    assert read.metrics.planned == 1
    assert read.attempts_url.endswith("/attempts")

    attempt_id = repository.get_run(run["id"])["attempts"][0]["id"]
    attempt = AttemptRead.model_validate(service.attempt_read(attempt_id))
    assert attempt.verdict == "passed"
    assert attempt.trace_complete is True
    assert attempt.usage.agent.source in ("reported", "estimated", "mixed", "unavailable")


async def test_trace_page_is_strictly_increasing_and_paginates(
    repository: EvaluationRepository, service: EvaluationService
) -> None:
    run = _seed_run(repository, service)
    await _drive(repository, service, run["id"])
    attempt_id = repository.get_run(run["id"])["attempts"][0]["id"]

    first = TracePage.model_validate(
        service.trace_page(attempt_id, after_seq=0, limit=2)
    )
    assert [event.seq for event in first.data] == sorted(
        event.seq for event in first.data
    )
    second = TracePage.model_validate(
        service.trace_page(attempt_id, after_seq=first.next_after_seq, limit=200)
    )
    assert all(event.seq > first.next_after_seq for event in second.data)
    assert first.sealed is True


async def test_run_summary_and_overview_derive_from_persisted_state(
    repository: EvaluationRepository, service: EvaluationService
) -> None:
    run = _seed_run(repository, service)
    await _drive(repository, service, run["id"])

    summary = RunSummary.model_validate(service.run_summary(run["id"]))
    assert summary.suite_name == "基础回归用例集"
    assert summary.terminal_count == 1

    overview = service.overview()
    assert any(item["id"] == run["id"] for item in overview["recent_runs"])
    assert overview["status_counts"]["completed"] == 1
    assert overview["metrics"]["planned"] == 1


async def test_rerun_creates_a_child_run_from_frozen_snapshot(
    repository: EvaluationRepository, service: EvaluationService
) -> None:
    run = _seed_run(repository, service)
    detail = await _drive(repository, service, run["id"])
    attempt_id = detail["attempts"][0]["id"]

    child = service.rerun(run["id"], [attempt_id], idempotency_key="svc-rerun",
                          scope="local-user")
    assert child["parent_run_id"] == run["id"]
    assert child["snapshot"]["agent_profile"]["id"] == "agent-1"
    await _drive(repository, service, child["id"])
    assert repository.get_run(child["id"])["status"] == "completed"


async def test_export_supports_json_and_csv(
    repository: EvaluationRepository, service: EvaluationService
) -> None:
    run = _seed_run(repository, service)
    await _drive(repository, service, run["id"])

    body, media, filename = service.export_run(run["id"], fmt="json")
    assert media.startswith("application/json")
    assert filename.endswith(".json")
    assert json.loads(body)["run_id"] == run["id"]

    csv_body, csv_media, _ = service.export_run(run["id"], fmt="csv")
    assert csv_media.startswith("text/csv")
    assert "attempt_id" in csv_body


async def test_profiles_expose_public_fields_only(service: EvaluationService) -> None:
    listed = service.list_profiles()
    assert {item["id"] for item in listed} == {"agent-1", "judge-1"}
    assert all("bundle" not in item for item in listed)
    assert API_KEY not in json.dumps(listed, ensure_ascii=False)


async def test_gold_samples_default_to_proposed(
    repository: EvaluationRepository, service: EvaluationService
) -> None:
    repository.seed_gold_samples(
        [
            {
                "id": f"G{i:02d}", "task": f"任务 {i}",
                "rubric": make_rubric_payload(), "evidence": {},
                "expected_verdict": "passed" if i < 6 else "failed",
                "expected_scores": [
                    {"dimension_id": "task_completion", "score": 3},
                    {"dimension_id": "correctness", "score": 3},
                ],
            }
            for i in range(1, 11)
        ],
        gold_revision=1,
    )
    page = service.list_gold_samples()
    parsed = GoldSamplePage.model_validate(page)
    assert len(parsed.data) == 10
    # 内置标签一律 proposed：AI 不能替用户确认。
    assert all(item.label_status == "proposed" for item in parsed.data)
    assert all(item.reviewer is None for item in parsed.data)


async def test_confirming_a_gold_sample_bumps_revision(
    repository: EvaluationRepository, service: EvaluationService
) -> None:
    repository.seed_gold_samples(
        [{
            "id": "G01", "task": "任务", "rubric": make_rubric_payload(),
            "evidence": {}, "expected_verdict": "passed",
            "expected_scores": [{"dimension_id": "correctness", "score": 3}],
        }],
        gold_revision=1,
    )
    before = repository.gold_revision()
    updated = repository.review_gold_sample(
        "G01",
        {
            "base_gold_revision": before,
            "expected_verdict": "passed",
            "expected_scores": [{"dimension_id": "correctness", "score": 4}],
            "comment": "人工确认",
        },
    )
    assert updated["label_status"] == "confirmed"
    assert updated["reviewer"] == "local-user"
    assert repository.gold_revision() == before + 1
