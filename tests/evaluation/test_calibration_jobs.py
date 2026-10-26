"""校准作业的服务层与调度循环接线（D14 收口）。

这里跑的是**真实调度器 + 真实服务层**，只有 Judge 的 provider 换成受控回放：
领取、逐项提交、作业收口、门禁落表、取消与版本漂移全部走生产代码。

口径与 :mod:`tests.evaluation.test_end_to_end` 一致——本文件用测试代码批量
``confirmed`` 的 gold 是**测试数据**，不等于人工标注；离线受控 provider 也不等于
真实 provider 冒烟。

钉死的六件事：

1. 证据只认 gold 行里冻结的那份：不重跑 attempt，也不接受调用方递进来的证据。
2. 未确认标签 / gold 版本漂移 / 量表混用都在**评分之前**拦下，逐项落 failed，
   作业收口成未完成，``calibration_results`` 里绝不留结论行。
3. 一致率只由逐项 ``matched`` 汇总；「跑完了但一致率偏低」与「没跑完」是两种状态。
4. 取消抢先落终态后，在飞项不得把作业复活成 completed。
5. 作业结论按 (judge 指纹, 量表版本, gold 版本) 三元组绑定，未验证的新量表不复用。
6. HTTP 创建的作业能被调度器跑到底，读出的指纹非空。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from nanobot.evaluation.controlled_provider import ControlledProvider, ScriptStep
from nanobot.evaluation.evidence import AttemptEvidence
from nanobot.evaluation.graders.calibration import (
    judge_fingerprint,
    lookup_calibration,
    rubric_payload,
)
from nanobot.evaluation.graders.judge import evaluate_judge
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.scheduler import Scheduler, SchedulerConfig
from nanobot.evaluation.server import API_PREFIX
from nanobot.evaluation.services import EvaluationService

from .http_app import JUDGE_PROFILE_ID, build_app, csrf_headers, login
from .test_end_to_end import TEST_LABEL_COMMENT, confirm_all, gold_samples

GOLD_SAMPLE_IDS = [str(sample["id"]) for sample in gold_samples()]
RUBRIC = gold_samples()[0]["rubric"]
#: 服务层由 profile 折算出的指纹输入：provider/model 相同即同一把标尺。
SERVICE_PROFILE = {"provider": "controlled", "model": "eval-model", "bundle": {}}


def _service(
    repository: EvaluationRepository, tmp_path: Path, judge_runner: Any
) -> EvaluationService:
    return EvaluationService(
        repository=repository,
        data_dir=tmp_path / "service",
        judge_runner=judge_runner,
    )


def _create_job(
    repository: EvaluationRepository, *, gold_revision: int, sample_ids: list[str]
) -> dict[str, Any]:
    return repository.create_calibration(
        snapshot={
            "judge_profile_id": JUDGE_PROFILE_ID,
            "gold_revision": gold_revision,
            "sample_ids": list(sample_ids),
        },
        sample_ids=list(sample_ids),
    )


async def _drive(
    repository: EvaluationRepository, service: EvaluationService, job_id: str
) -> dict[str, Any]:
    """用真实调度器把作业跑到不再有在飞项为止。"""
    scheduler = Scheduler(
        repository=repository,
        pipeline=service,
        config=SchedulerConfig(
            data_dir=service.data_dir, concurrency=1, poll_interval_s=0.01
        ),
    )
    async with scheduler:
        deadline = asyncio.get_running_loop().time() + 60
        while asyncio.get_running_loop().time() < deadline:
            status = repository.get_calibration(job_id)["status"]
            if status not in ("queued", "running"):
                return repository.get_calibration(job_id)
            await asyncio.sleep(0.02)
    raise AssertionError("calibration job did not settle")


def _judge_runner(
    *,
    fixed: list[int] | None = None,
    gate: asyncio.Event | None = None,
    seen: list[Any] | None = None,
):
    """受控 Judge：按 gold 的 expected_scores 给分，或无视样本一律给 ``fixed``。

    ``gate`` 让评分停在等待点，用来造「取消时还有项在飞」；``seen`` 收回每次评分
    实际拿到的证据对象，用来证明证据来自 gold 冻结内容。
    """

    async def runner(
        *, attempt_id: str, rubric: Any, evidence: AttemptEvidence, profile: Any, paths: Any
    ) -> Any:
        if seen is not None:
            seen.append(evidence)
        if gate is not None:
            await gate.wait()
        by_attempt = {
            str(sample["evidence"]["attempt_id"]): [
                int(item["score"]) for item in sample["expected_scores"]
            ]
            for sample in gold_samples()
        }
        scores = (
            list(fixed)
            if fixed is not None
            else list(by_attempt[evidence.attempt_id])
        )
        payload = {
            "dimensions": [
                {
                    "id": str(item["id"]),
                    "score": score,
                    "reason": "离线测试夹具给出的评分",
                    "evidence_refs": [f"trace:{evidence.attempt_id}:1"],
                }
                for item, score in zip(rubric_payload(rubric)["dimensions"], scores, strict=True)
            ]
        }
        result = await evaluate_judge(
            rubric,
            evidence,
            ControlledProvider(steps=(ScriptStep(json_text=json.dumps(payload, ensure_ascii=False)),)),
            model=str(profile.get("model", "")),
        )
        return result.model_copy(update={"calibrated": False})

    return runner


# --------------------------------------------------------------------- 跑通与门禁


async def test_job_completes_and_records_a_passing_gate(
    evaluation_repository: EvaluationRepository, tmp_path: Path
) -> None:
    service = _service(evaluation_repository, tmp_path, _judge_runner())
    assert service.seed_builtin_gold_samples() == 10
    revision = confirm_all(evaluation_repository)
    job = _create_job(
        evaluation_repository, gold_revision=revision, sample_ids=GOLD_SAMPLE_IDS
    )

    detail = await _drive(evaluation_repository, service, job["id"])

    assert detail["status"] == "completed"
    assert detail["total"] == 10 and detail["completed"] == 10
    result = detail["result"]
    assert result["agreement"] == 1.0 and result["passed"] is True
    assert result["judge_fingerprint"] == judge_fingerprint(
        profile=SERVICE_PROFILE, rubric=RUBRIC
    )
    assert result["rubric_version"] == "general-v1"
    assert result["gold_revision"] == revision
    items = evaluation_repository.list_calibration_items(job["id"])
    assert [item["result"]["matched"] for item in items] == [True] * 10

    # 门禁查的是 calibration_results：作业结论必须真的并进那张表。
    found = evaluation_repository.find_calibration_result(
        judge_fingerprint=result["judge_fingerprint"],
        rubric_version="general-v1",
        gold_revision=revision,
    )
    assert found is not None and found["passed"] is True
    assert lookup_calibration(
        evaluation_repository,
        judge_fingerprint=result["judge_fingerprint"],
        rubric_version="general-v1",
        gold_revision=evaluation_repository.gold_revision(),
    )["passed"] is True


async def test_over_generous_judge_completes_but_fails_the_gate(
    evaluation_repository: EvaluationRepository, tmp_path: Path
) -> None:
    """一致率偏低是「跑完了但没达标」：状态 completed、passed=false，不是未完成。"""
    service = _service(evaluation_repository, tmp_path, _judge_runner(fixed=[4, 4]))
    service.seed_builtin_gold_samples()
    revision = confirm_all(evaluation_repository)
    job = _create_job(
        evaluation_repository, gold_revision=revision, sample_ids=GOLD_SAMPLE_IDS
    )

    detail = await _drive(evaluation_repository, service, job["id"])

    assert detail["status"] == "completed"
    result = detail["result"]
    assert result["matched"] == 5 and result["agreement"] == 0.5
    assert result["passed"] is False
    assert evaluation_repository.find_calibration_result(
        judge_fingerprint=result["judge_fingerprint"],
        rubric_version="general-v1",
        gold_revision=revision,
    )["passed"] is False


# --------------------------------------------------------------------- 前置不满足


async def test_unconfirmed_labels_write_no_gate_row(
    evaluation_repository: EvaluationRepository, tmp_path: Path
) -> None:
    """proposed 标签起校准：逐项 failed、作业未完成、门禁表里没有结论行。"""
    service = _service(evaluation_repository, tmp_path, _judge_runner())
    service.seed_builtin_gold_samples()
    job = _create_job(
        evaluation_repository,
        gold_revision=evaluation_repository.gold_revision(),
        sample_ids=GOLD_SAMPLE_IDS,
    )

    detail = await _drive(evaluation_repository, service, job["id"])

    assert detail["status"] == "cancelled"
    assert detail["completed"] == 0
    items = evaluation_repository.list_calibration_items(job["id"])
    assert {item["status"] for item in items} == {"failed"}
    assert all("GOLD_NOT_CONFIRMED" in item["result"]["error"] for item in items)
    assert "GOLD_NOT_CONFIRMED" in detail["result"]["error"]
    assert evaluation_repository.find_calibration_result(
        judge_fingerprint=judge_fingerprint(profile=SERVICE_PROFILE, rubric=RUBRIC),
        rubric_version="general-v1",
        gold_revision=job["snapshot"]["gold_revision"],
    ) is None


async def test_gold_revision_drift_stops_before_scoring(
    evaluation_repository: EvaluationRepository, tmp_path: Path
) -> None:
    """作业冻结的 gold 版本过期后不再评分：标签一旦变动，旧结论就不成立。"""
    service = _service(evaluation_repository, tmp_path, _judge_runner())
    service.seed_builtin_gold_samples()
    revision = confirm_all(evaluation_repository)
    job = _create_job(
        evaluation_repository, gold_revision=revision, sample_ids=GOLD_SAMPLE_IDS
    )
    evaluation_repository.review_gold_sample(
        GOLD_SAMPLE_IDS[0],
        {
            "base_gold_revision": evaluation_repository.gold_revision(),
            "expected_verdict": "failed",
            "expected_scores": [{"dimension_id": "task_completion", "score": 1}],
            "comment": TEST_LABEL_COMMENT,
        },
    )

    detail = await _drive(evaluation_repository, service, job["id"])

    assert detail["status"] == "cancelled"
    assert detail["completed"] == 0
    items = evaluation_repository.list_calibration_items(job["id"])
    assert all("GOLD_REVISION_STALE" in str(item["result"]["error"]) for item in items)
    assert evaluation_repository.find_calibration_result(
        judge_fingerprint=judge_fingerprint(profile=SERVICE_PROFILE, rubric=RUBRIC),
        rubric_version="general-v1",
        gold_revision=revision,
    ) is None


# --------------------------------------------------------------------- 取消


async def test_cancelling_a_job_leaves_agreement_null(
    evaluation_repository: EvaluationRepository, tmp_path: Path
) -> None:
    gate = asyncio.Event()
    service = _service(evaluation_repository, tmp_path, _judge_runner(gate=gate))
    service.seed_builtin_gold_samples()
    revision = confirm_all(evaluation_repository)
    job = _create_job(
        evaluation_repository, gold_revision=revision, sample_ids=GOLD_SAMPLE_IDS
    )
    scheduler = Scheduler(
        repository=evaluation_repository,
        pipeline=service,
        config=SchedulerConfig(
            data_dir=service.data_dir, concurrency=1, poll_interval_s=0.01
        ),
    )

    async with scheduler:
        deadline = asyncio.get_running_loop().time() + 10
        while not evaluation_repository.list_calibration_items(job["id"])[0]["result"]:
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("first item never claimed")
            if evaluation_repository.list_calibration_items(job["id"])[0]["status"] == "running":
                break
            await asyncio.sleep(0.02)
        evaluation_repository.cancel_calibration(job["id"])
        gate.set()

    detail = evaluation_repository.get_calibration(job["id"])
    assert detail["status"] == "cancelled"
    # 未完成就是没有一致率：0 会让页面显示「跑完了但一致率为零」。
    assert detail["result"] is None
    assert detail["completed"] == 0
    assert evaluation_repository.find_calibration_result(
        judge_fingerprint=judge_fingerprint(profile=SERVICE_PROFILE, rubric=RUBRIC),
        rubric_version="general-v1",
        gold_revision=revision,
    ) is None


# --------------------------------------------------------------------- 证据来源


async def test_item_evidence_is_the_frozen_gold_payload(
    evaluation_repository: EvaluationRepository, tmp_path: Path
) -> None:
    seen: list[AttemptEvidence] = []
    service = _service(
        evaluation_repository, tmp_path, _judge_runner(seen=seen)
    )
    service.seed_builtin_gold_samples()
    revision = confirm_all(evaluation_repository)
    job = _create_job(
        evaluation_repository, gold_revision=revision, sample_ids=GOLD_SAMPLE_IDS
    )

    await _drive(evaluation_repository, service, job["id"])

    frozen = {
        str(sample["evidence"]["attempt_id"]): sample["evidence"]["final_answer"]
        for sample in gold_samples()
    }
    assert {item.attempt_id for item in seen} == set(frozen)
    assert {item.attempt_id: item.final_answer for item in seen} == frozen
    # 每项独占一套 runtime/workspace：校准项之间不能共享 Trace 目录。
    roots = {str(Path(service.runtime_root) / "calibrations")}
    assert all(str(paths.workspace).startswith(next(iter(roots))) for paths in [
        service._calibration_paths(item["id"])[0]  # noqa: SLF001 - 断言布局本身
        for item in evaluation_repository.list_calibration_items(job["id"])
    ])


# --------------------------------------------------------------------- HTTP


async def test_http_created_job_runs_to_completed(
    evaluation_repository: EvaluationRepository, tmp_path: Path
) -> None:
    """页面确认标签 → 起校准 → 调度器跑完 → 读出指纹，全程走 HTTP 契约。"""
    app = build_app(
        evaluation_repository, tmp_path / "svc", judge_runner=_judge_runner()
    )
    async with TestClient(TestServer(app, host="127.0.0.1")) as client:
        csrf = await login(client)
        samples = await (
            await client.get(
                f"{API_PREFIX}/calibration-samples", headers=csrf_headers(csrf)
            )
        ).json()
        revision = int(samples["gold_revision"])
        for row in samples["data"]:
            revision += 1
            response = await client.patch(
                f"{API_PREFIX}/calibration-samples/{row['id']}",
                json={
                    "base_gold_revision": revision - 1,
                    "expected_verdict": row["expected_verdict"],
                    "expected_scores": row["expected_scores"],
                    "comment": TEST_LABEL_COMMENT,
                    "confirm": True,
                },
                headers=csrf_headers(csrf),
            )
            assert response.status == 200
            assert (await response.json())["label_status"] == "confirmed"

        rejected = await client.patch(
            f"{API_PREFIX}/calibration-samples/C01",
            json={
                "base_gold_revision": revision,
                "expected_verdict": "passed",
                "expected_scores": [{"dimension_id": "task_completion", "score": 4}],
                "comment": "未确认的修订",
                "confirm": False,
            },
            headers=csrf_headers(csrf),
        )
        assert rejected.status == 422

        created = await client.post(
            f"{API_PREFIX}/calibrations",
            json={
                "judge_profile_id": JUDGE_PROFILE_ID,
                "gold_revision": revision,
                "sample_ids": GOLD_SAMPLE_IDS,
            },
            headers={**csrf_headers(csrf), "Idempotency-Key": "calib-http-1"},
        )
        assert created.status == 202
        location = created.headers["Location"]
        body = await created.json()
        assert body["status"] in ("queued", "running")

        # build_app 里的调度器已在应用生命周期里启动，这里只需轮询到终态。
        for _ in range(500):
            response = await client.get(location, headers=csrf_headers(csrf))
            read = await response.json()
            if read["status"] not in ("queued", "running"):
                break
            await asyncio.sleep(0.02)

        assert read["status"] == "completed", read
        assert read["total"] == 10 and read["completed"] == 10
        assert read["agreement"] == 1.0
        assert read["passed"] is True
        assert len(read["judge_fingerprint"]) == 64
        assert read["error"] is None
