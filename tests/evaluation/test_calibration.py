"""Judge 量表校准的红灯测试（WU-04 / T08 第 5–7 项）。

口径来自 spec §11 验收 10、契约 §3.1/§4 与 plan T08：

- ``judge_fingerprint`` 只由 provider/model/generation + prompt_version + 量表版本**与正文**
  决定；换任一项都让旧校准失效，新量表不能借用 ``general-v1`` 的结论。
- 校准只用**人工已确认**（``confirmed``）的 gold 样本，至少 10 个、正反例各至少 4 个；
  内置标签默认 ``proposed``，AI 不能替用户确认。
- ``agreement = matched / total``，``>= 0.8`` 是 ``passed=true`` 的唯一路径；未达标时开放式
  任务只显示原始分数并进入 ``review_required``，不做门禁。
- 存储一律走 WU-01 的仓储方法（``seed_gold_samples`` / ``list_gold_samples`` /
  ``review_gold_sample`` / ``record_calibration_result`` / ``find_calibration_result``），
  判分侧不建第二套表，也绝不回写 gold 标签。

证据替身 ``CalEvidenceStub`` 的字段与入口逐字照抄契约 §6.1，不 import WU-03 的 ``evidence.py``。
"""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from nanobot.evaluation.graders.calibration import (
    AGREEMENT_TARGET,
    GOLD_MIN_PER_POLARITY,
    GOLD_MIN_SAMPLES,
    CalibrationError,
    calibration_snapshot,
    judge_fingerprint,
    lookup_calibration,
    run_calibration,
    select_gold_samples,
    verdict_from_scores,
)
from nanobot.evaluation.graders.judge import PROMPT_VERSION
from nanobot.evaluation.graders.verdict import derive_verdict, judge_state_of
from nanobot.evaluation.schemas import JudgeDimensionResult, JudgeResult
from tests.evaluation.conftest import make_rubric_payload

RUBRIC = make_rubric_payload()
DIMENSIONS = ("task_completion", "correctness")
PROFILE: dict[str, Any] = {
    "provider": "deepseek",
    "model": "judge-model",
    "generation": {"temperature": 0, "max_tokens": 512},
}


# ------------------------------------------------------------------ §6.1 证据替身


@dataclass(frozen=True)
class CalArtifactStub:
    relative_path: str
    media_type: str
    size_bytes: int
    sha256: str
    artifact_id: uuid.UUID | None


class CalEvidenceStub:
    """契约 §6.1 ``AttemptEvidence`` 的校准侧消费替身。"""

    def __init__(self, *, attempt_id: str, final_answer: str | None = "已完成") -> None:
        self.attempt_id = attempt_id
        self.case_snapshot: dict[str, Any] = {"assertions": [], "rubric": RUBRIC}
        self.final_answer = final_answer
        self.stop_reason: str | None = "stop"
        self.iterations: int = 2
        self.trace_events: tuple[Any, ...] = ()
        self.tool_calls: tuple[Any, ...] = ()
        self.artifacts: tuple[CalArtifactStub, ...] = ()
        self.workspace: Any = None
        self.trace_complete: bool = True
        self.execution_status: str = "succeeded"

    def read_text(self, relative_path: str) -> str:
        raise AssertionError("校准不读产物正文")

    def read_json(self, relative_path: str) -> Any:
        raise AssertionError("校准不读产物正文")

    def exists(self, relative_path: str) -> bool:
        return False

    def resolve_ref(self, ref: str) -> Any | None:
        return None


# ------------------------------------------------------------------------ gold 构造


def make_scores(verdict: str) -> list[dict[str, Any]]:
    score = 3 if verdict == "passed" else 1
    return [{"dimension_id": key, "score": score} for key in DIMENSIONS]


def gold_sample(index: int, *, verdict: str = "passed", **overrides: Any) -> dict[str, Any]:
    """契约 §3.1 GoldSample 形状；标签状态由仓储决定（写入即 ``proposed``）。"""
    payload: dict[str, Any] = {
        "id": f"gold-{index:02d}",
        "task": f"校准样本 {index:02d}",
        "rubric": copy.deepcopy(RUBRIC),
        "evidence": {"final_answer": f"answer {index}"},
        "expected_verdict": verdict,
        "expected_scores": make_scores(verdict),
    }
    payload.update(overrides)
    return payload


def balanced_samples(count: int = 10, *, passed: int = 6) -> list[dict[str, Any]]:
    return [
        gold_sample(index + 1, verdict="passed" if index < passed else "failed")
        for index in range(count)
    ]


def seed_confirmed(repository: Any, samples: Sequence[dict[str, Any]], *, gold_revision: int = 1) -> int:
    """写入内置建议标签后逐条「人工确认」，返回新的 gold_revision。"""
    repository.seed_gold_samples(list(samples), gold_revision=gold_revision)
    for sample in samples:
        repository.review_gold_sample(
            sample["id"],
            {
                "base_gold_revision": repository.list_gold_samples()["gold_revision"],
                "expected_verdict": sample["expected_verdict"],
                "expected_scores": sample["expected_scores"],
                "comment": "人工确认",
                "confirm": True,
            },
        )
    return repository.list_gold_samples()["gold_revision"]


def with_evidence(samples: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """给每条 gold 配一个 §6.1 视图；``attempt_id`` 用样本 id，假 Judge 据此取分。

    注意：仓储里存的 gold 仍是契约 §3.1 的 JSON ``evidence``；带对象的样本只作为「服务把
    gold 证据装配成 §6.1 视图」的替身传给 ``run_calibration``。
    """
    return [{**sample, "evidence": CalEvidenceStub(attempt_id=sample["id"])} for sample in samples]


def confirmed_with_evidence(
    repository: Any, *, count: int = 10, passed: int = 6
) -> tuple[list[dict[str, Any]], int]:
    """先落库并逐条人工确认（JSON gold），再返回装配好证据对象的同批样本与 revision。"""
    plain = balanced_samples(count, passed=passed)
    revision = seed_confirmed(repository, plain)
    return with_evidence(plain), revision


def agreeing_judge(samples: Sequence[dict[str, Any]], *, disagree: Sequence[str] = ()) -> Any:
    """假 Judge：默认与人工标签一致，``disagree`` 列出的样本给反向分数。"""
    expected = {sample["id"]: sample["expected_verdict"] for sample in samples}

    async def _judge(rubric: Any, evidence: Any) -> JudgeResult:
        del rubric
        verdict = expected[evidence.attempt_id]
        score = (1 if verdict == "passed" else 3) if evidence.attempt_id in disagree else (
            3 if verdict == "passed" else 1
        )
        return JudgeResult(
            status="completed",
            model="judge-model",
            version="judge-v1",
            dimensions=[
                JudgeDimensionResult(id=key, score=score, reason="校准", evidence_refs=["x"])
                for key in DIMENSIONS
            ],
            calibrated=False,
            error=None,
        )

    return _judge


# ------------------------------------------------------------------- judge_fingerprint


def test_fingerprint_is_stable_canonical_hex() -> None:
    first = judge_fingerprint(profile=PROFILE, rubric=RUBRIC)
    second = judge_fingerprint(profile=dict(PROFILE), rubric=json.loads(json.dumps(RUBRIC)))

    assert first == second
    assert len(first) == 64
    assert all(char in "0123456789abcdef" for char in first)


def test_fingerprint_ignores_key_order() -> None:
    reordered = {
        "generation": {"max_tokens": 512, "temperature": 0},
        "model": "judge-model",
        "provider": "deepseek",
    }

    assert judge_fingerprint(profile=reordered, rubric=RUBRIC) == judge_fingerprint(
        profile=PROFILE, rubric=RUBRIC
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"provider": "moonshot"},
        {"model": "judge-model-2"},
        {"generation": {"temperature": 1, "max_tokens": 512}},
        {"generation": None},
    ],
)
def test_profile_change_invalidates_fingerprint(patch: dict[str, Any]) -> None:
    base = judge_fingerprint(profile=PROFILE, rubric=RUBRIC)
    changed = dict(PROFILE)
    changed.update(patch)

    assert judge_fingerprint(profile=changed, rubric=RUBRIC) != base


def test_prompt_version_is_part_of_fingerprint() -> None:
    base = judge_fingerprint(profile=PROFILE, rubric=RUBRIC)

    assert judge_fingerprint(profile=PROFILE, rubric=RUBRIC, prompt_version=PROMPT_VERSION) == base
    assert judge_fingerprint(profile=PROFILE, rubric=RUBRIC, prompt_version="judge-prompt-v2") != base


def test_rubric_version_and_body_are_part_of_fingerprint() -> None:
    base = judge_fingerprint(profile=PROFILE, rubric=RUBRIC)
    version_bump = json.loads(json.dumps(RUBRIC))
    version_bump["version"] = "general-v2"
    threshold_change = json.loads(json.dumps(RUBRIC))
    threshold_change["dimensions"][0]["pass_score"] = 4
    extra_dimension = json.loads(json.dumps(RUBRIC))
    extra_dimension["dimensions"] = list(extra_dimension["dimensions"]) + [
        {
            "id": "tool_use",
            "name": "工具合理性",
            "anchors": ["a0", "a1", "a2", "a3", "a4"],
            "pass_score": 3,
        }
    ]

    assert judge_fingerprint(profile=PROFILE, rubric=version_bump) != base
    assert judge_fingerprint(profile=PROFILE, rubric=threshold_change) != base
    assert judge_fingerprint(profile=PROFILE, rubric=extra_dimension) != base


def test_fingerprint_reads_only_profile_fields() -> None:
    """密钥之类的额外字段既不参与指纹、也不出现在指纹里（导出/快照都不能泄密）。"""
    base = judge_fingerprint(profile=PROFILE, rubric=RUBRIC)
    with_secret = {**PROFILE, "api_key": "***"}

    assert judge_fingerprint(profile=with_secret, rubric=RUBRIC) == base
    assert "sk-" not in base


def test_incomplete_profile_is_rejected() -> None:
    with pytest.raises(CalibrationError, match="JUDGE_PROFILE_INCOMPLETE"):
        judge_fingerprint(profile={"provider": "deepseek", "model": ""}, rubric=RUBRIC)


# ------------------------------------------------------------------------ gold 门禁


def test_gate_accepts_ten_confirmed_balanced_samples(evaluation_repository: Any) -> None:
    samples = balanced_samples(10, passed=6)
    seed_confirmed(evaluation_repository, samples)

    selected = select_gold_samples(evaluation_repository.list_gold_samples(), rubric=RUBRIC)

    assert len(selected) == GOLD_MIN_SAMPLES
    assert [item["id"] for item in selected] == [sample["id"] for sample in samples]


def test_gate_rejects_too_few_samples(evaluation_repository: Any) -> None:
    seed_confirmed(evaluation_repository, balanced_samples(9))

    with pytest.raises(CalibrationError, match="GOLD_SAMPLES_INSUFFICIENT"):
        select_gold_samples(evaluation_repository.list_gold_samples(), rubric=RUBRIC)


def test_gate_rejects_polarity_below_four(evaluation_repository: Any) -> None:
    """9 正 1 反虽然够 10 条，仍然证明不了评分器可靠。"""
    seed_confirmed(evaluation_repository, balanced_samples(10, passed=9))

    with pytest.raises(CalibrationError, match="GOLD_POLARITY_IMBALANCE"):
        select_gold_samples(evaluation_repository.list_gold_samples(), rubric=RUBRIC)


def test_gate_ignores_unconfirmed_proposed_labels(evaluation_repository: Any) -> None:
    """内置标签默认 proposed：AI 不能替用户确认，未确认样本不进校准。"""
    evaluation_repository.seed_gold_samples(balanced_samples(10), gold_revision=1)

    with pytest.raises(CalibrationError, match="GOLD_NOT_CONFIRMED"):
        select_gold_samples(evaluation_repository.list_gold_samples(), rubric=RUBRIC)


def test_gate_rejects_duplicate_sample_ids(evaluation_repository: Any) -> None:
    seed_confirmed(evaluation_repository, balanced_samples(10))
    stored = evaluation_repository.list_gold_samples()
    duplicated = {**stored, "data": [*stored["data"], dict(stored["data"][0])]}

    with pytest.raises(CalibrationError, match="GOLD_SAMPLE_DUPLICATE"):
        select_gold_samples(duplicated, rubric=RUBRIC)


def test_gate_only_counts_samples_using_the_same_rubric(evaluation_repository: Any) -> None:
    """新量表没有匹配 gold 时显示未校准，不能借用 general-v1 的样本。"""
    seed_confirmed(evaluation_repository, balanced_samples(10))
    other_rubric = json.loads(json.dumps(RUBRIC))
    other_rubric["version"] = "custom-v1"

    with pytest.raises(CalibrationError, match="GOLD_RUBRIC_MISMATCH"):
        select_gold_samples(evaluation_repository.list_gold_samples(), rubric=other_rubric)


# ------------------------------------------------------------------------ agreement


async def test_agreement_at_eighty_percent_is_the_only_passing_path(
    evaluation_repository: Any,
) -> None:
    samples, revision = confirmed_with_evidence(evaluation_repository)

    result = await run_calibration(
        evaluation_repository,
        profile=PROFILE,
        rubric=RUBRIC,
        judge=agreeing_judge(samples, disagree=["gold-01", "gold-02"]),
        evidence_for=lambda sample: sample["evidence"],
        samples=samples,
        gold_revision=revision,
    )

    assert (GOLD_MIN_SAMPLES, GOLD_MIN_PER_POLARITY, AGREEMENT_TARGET) == (10, 4, 0.8)
    assert result["total"] == 10 and result["matched"] == 8
    assert result["agreement"] == 0.8
    assert result["passed"] is True


async def test_agreement_below_target_marks_not_passed(evaluation_repository: Any) -> None:
    samples, revision = confirmed_with_evidence(evaluation_repository)

    result = await run_calibration(
        evaluation_repository,
        profile=PROFILE,
        rubric=RUBRIC,
        judge=agreeing_judge(samples, disagree=["gold-01", "gold-02", "gold-03", "gold-04"]),
        evidence_for=lambda sample: sample["evidence"],
        samples=samples,
        gold_revision=revision,
    )

    assert result["matched"] == 6
    assert result["agreement"] == pytest.approx(0.6)
    assert result["passed"] is False


async def test_judge_error_counts_as_unmatched_but_keeps_total(evaluation_repository: Any) -> None:
    samples, revision = confirmed_with_evidence(evaluation_repository)

    async def failing_judge(rubric: Any, evidence: Any) -> JudgeResult:
        del rubric, evidence
        return JudgeResult(
            status="judge_error",
            model="judge-model",
            version="judge-v1",
            dimensions=[],
            calibrated=False,
            error="JUDGE_TIMEOUT: 评分预算耗尽",
        )

    result = await run_calibration(
        evaluation_repository,
        profile=PROFILE,
        rubric=RUBRIC,
        judge=failing_judge,
        evidence_for=lambda sample: sample["evidence"],
        samples=samples,
        gold_revision=revision,
    )

    assert result["total"] == 10
    assert result["matched"] == 0
    assert result["passed"] is False
    assert {item["judge_status"] for item in result["items"]} == {"judge_error"}
    assert all(item["matched"] is False for item in result["items"])


async def test_calibration_result_is_persisted_through_repository(
    evaluation_repository: Any,
) -> None:
    samples, revision = confirmed_with_evidence(evaluation_repository)

    result = await run_calibration(
        evaluation_repository,
        profile=PROFILE,
        rubric=RUBRIC,
        judge=agreeing_judge(samples),
        evidence_for=lambda sample: sample["evidence"],
        samples=samples,
        gold_revision=revision,
    )

    found = lookup_calibration(
        evaluation_repository,
        judge_fingerprint=result["judge_fingerprint"],
        rubric_version="general-v1",
        gold_revision=revision,
    )

    assert found is not None
    assert found["agreement"] == 1.0 and found["passed"] is True
    assert result["judge_fingerprint"] == judge_fingerprint(profile=PROFILE, rubric=RUBRIC)


async def test_changed_profile_finds_no_calibration(evaluation_repository: Any) -> None:
    samples, revision = confirmed_with_evidence(evaluation_repository)
    await run_calibration(
        evaluation_repository,
        profile=PROFILE,
        rubric=RUBRIC,
        judge=agreeing_judge(samples),
        evidence_for=lambda sample: sample["evidence"],
        samples=samples,
        gold_revision=revision,
    )

    other = {**PROFILE, "model": "judge-model-2"}
    after = lookup_calibration(
        evaluation_repository,
        judge_fingerprint=judge_fingerprint(profile=other, rubric=RUBRIC),
        rubric_version="general-v1",
        gold_revision=revision,
    )

    assert after is None


async def test_confirmed_label_change_invalidates_previous_calibration(
    evaluation_repository: Any,
) -> None:
    """gold_revision 抬升后，旧指纹在新版本上没有结论 → 重新校准前保持未校准。"""
    samples, revision = confirmed_with_evidence(evaluation_repository)
    result = await run_calibration(
        evaluation_repository,
        profile=PROFILE,
        rubric=RUBRIC,
        judge=agreeing_judge(samples),
        evidence_for=lambda sample: sample["evidence"],
        samples=samples,
        gold_revision=revision,
    )
    evaluation_repository.review_gold_sample(
        "gold-01",
        {
            "base_gold_revision": revision,
            "expected_verdict": "failed",
            "expected_scores": make_scores("failed"),
            "comment": "边界结果改判",
            "confirm": True,
        },
    )
    bumped = evaluation_repository.list_gold_samples()["gold_revision"]

    assert bumped == revision + 1
    assert (
        lookup_calibration(
            evaluation_repository,
            judge_fingerprint=result["judge_fingerprint"],
            rubric_version="general-v1",
            gold_revision=bumped,
        )
        is None
    )


async def test_new_rubric_cannot_reuse_general_v1_result(evaluation_repository: Any) -> None:
    samples, revision = confirmed_with_evidence(evaluation_repository)
    result = await run_calibration(
        evaluation_repository,
        profile=PROFILE,
        rubric=RUBRIC,
        judge=agreeing_judge(samples),
        evidence_for=lambda sample: sample["evidence"],
        samples=samples,
        gold_revision=revision,
    )
    custom = json.loads(json.dumps(RUBRIC))
    custom["version"] = "custom-v1"

    reuse = lookup_calibration(
        evaluation_repository,
        judge_fingerprint=judge_fingerprint(profile=PROFILE, rubric=custom),
        rubric_version="custom-v1",
        gold_revision=revision,
    )

    assert reuse is None
    assert result["rubric_version"] == "general-v1"


# ----------------------------------------------------------- 未校准 → review_required


async def test_low_agreement_shows_raw_scores_but_review_required(
    evaluation_repository: Any,
) -> None:
    """校准未达标：只显示原始分数，开放式任务进 review_required，不做门禁。"""
    samples, revision = confirmed_with_evidence(evaluation_repository)
    await run_calibration(
        evaluation_repository,
        profile=PROFILE,
        rubric=RUBRIC,
        judge=agreeing_judge(samples, disagree=["gold-01", "gold-02", "gold-03", "gold-04"]),
        evidence_for=lambda sample: sample["evidence"],
        samples=samples,
        gold_revision=revision,
    )
    calibration = lookup_calibration(
        evaluation_repository,
        judge_fingerprint=judge_fingerprint(profile=PROFILE, rubric=RUBRIC),
        rubric_version="general-v1",
        gold_revision=revision,
    )
    judge = JudgeResult(
        status="completed",
        model="judge-model",
        version="judge-v1",
        dimensions=[
            JudgeDimensionResult(id=key, score=3, reason="结果可用", evidence_refs=["x"])
            for key in DIMENSIONS
        ],
        calibrated=False,
        error=None,
    )

    assert calibration is not None and calibration["passed"] is False
    state = judge_state_of(RUBRIC, judge, calibration)
    assert state == "uncalibrated"
    assert derive_verdict("succeeded", [True], state, True) == "review_required"
    # 原始分数照常展示，不因为未校准而被清零。
    assert [item.score for item in judge.dimensions] == [3, 3]


async def test_calibrated_run_gates_on_scores(evaluation_repository: Any) -> None:
    samples, revision = confirmed_with_evidence(evaluation_repository)
    result = await run_calibration(
        evaluation_repository,
        profile=PROFILE,
        rubric=RUBRIC,
        judge=agreeing_judge(samples),
        evidence_for=lambda sample: sample["evidence"],
        samples=samples,
        gold_revision=revision,
    )
    calibration = lookup_calibration(
        evaluation_repository,
        judge_fingerprint=result["judge_fingerprint"],
        rubric_version="general-v1",
        gold_revision=revision,
    )
    low = JudgeResult(
        status="completed",
        model="judge-model",
        version="judge-v1",
        dimensions=[
            JudgeDimensionResult(id=key, score=1, reason="缺口明显", evidence_refs=["x"])
            for key in DIMENSIONS
        ],
        calibrated=True,
        error=None,
    )

    assert judge_state_of(RUBRIC, low, calibration) == "failed"
    assert derive_verdict("succeeded", [True], "failed", True) == "failed"


# ------------------------------------------------------------------------ 其它边界


def test_verdict_from_scores_uses_pass_thresholds() -> None:
    passed = [
        JudgeDimensionResult(id=key, score=3, reason="ok", evidence_refs=["x"]) for key in DIMENSIONS
    ]
    failed = [
        JudgeDimensionResult(id="task_completion", score=4, reason="ok", evidence_refs=["x"]),
        JudgeDimensionResult(id="correctness", score=2, reason="ok", evidence_refs=["x"]),
    ]
    strict = json.loads(json.dumps(RUBRIC))
    strict["dimensions"][0]["pass_score"] = 4

    assert verdict_from_scores(RUBRIC, passed) == "passed"
    assert verdict_from_scores(RUBRIC, failed) == "failed"
    assert verdict_from_scores(strict, passed) == "failed"
    assert verdict_from_scores(RUBRIC, passed[:1]) is None


def test_snapshot_has_no_secrets() -> None:
    snapshot = calibration_snapshot(
        profile=PROFILE, rubric=RUBRIC, gold_revision=3, sample_count=10
    )

    assert snapshot["judge_fingerprint"] == judge_fingerprint(profile=PROFILE, rubric=RUBRIC)
    assert snapshot["rubric_version"] == "general-v1"
    assert snapshot["gold_revision"] == 3
    assert snapshot["sample_count"] == 10
    assert "api_key" not in json.dumps({**snapshot, "profile": PROFILE}, ensure_ascii=False)


async def test_run_calibration_refuses_unconfirmed_selection(evaluation_repository: Any) -> None:
    """未确认标签直接启动校准：拒绝，且不写任何 calibration_results。"""
    plain = balanced_samples(10)
    evaluation_repository.seed_gold_samples(plain, gold_revision=1)
    samples = with_evidence(plain)

    with pytest.raises(CalibrationError, match="GOLD_NOT_CONFIRMED"):
        await run_calibration(
            evaluation_repository,
            profile=PROFILE,
            rubric=RUBRIC,
            judge=agreeing_judge(samples),
            evidence_for=lambda sample: sample["evidence"],
            samples=samples,
            gold_revision=1,
        )

    assert (
        lookup_calibration(
            evaluation_repository,
            judge_fingerprint=judge_fingerprint(profile=PROFILE, rubric=RUBRIC),
            rubric_version="general-v1",
            gold_revision=1,
        )
        is None
    )


async def test_run_calibration_requires_evidence_for_every_sample(evaluation_repository: Any) -> None:
    samples, revision = confirmed_with_evidence(evaluation_repository)
    missing = [{**sample, "evidence": None} for sample in samples if sample["id"] == "gold-03"]

    with pytest.raises(CalibrationError, match="GOLD_SAMPLE_EVIDENCE_MISSING"):
        await run_calibration(
            evaluation_repository,
            profile=PROFILE,
            rubric=RUBRIC,
            judge=agreeing_judge(samples),
            evidence_for=lambda sample: sample["evidence"],
            samples=[*(s for s in samples if s["id"] != "gold-03"), *missing],
            gold_revision=revision,
        )


async def test_cancelled_calibration_records_nothing(evaluation_repository: Any) -> None:
    """契约 §3.1：取消/未完成的 ``passed`` 必须是 null —— 半截结论不能污染门禁。"""
    samples, revision = confirmed_with_evidence(evaluation_repository)
    seen = {"n": 0}

    def cancel_after_three() -> bool:
        seen["n"] += 1
        return seen["n"] > 3

    result = await run_calibration(
        evaluation_repository,
        profile=PROFILE,
        rubric=RUBRIC,
        judge=agreeing_judge(samples),
        evidence_for=lambda sample: sample["evidence"],
        samples=samples,
        gold_revision=revision,
        should_cancel=cancel_after_three,
    )

    assert result["status"] == "cancelled"
    assert result["passed"] is None and result["agreement"] is None
    assert result["completed"] < result["total"]
    assert (
        lookup_calibration(
            evaluation_repository,
            judge_fingerprint=judge_fingerprint(profile=PROFILE, rubric=RUBRIC),
            rubric_version="general-v1",
            gold_revision=revision,
        )
        is None
    )


async def test_stale_gold_revision_is_rejected(evaluation_repository: Any) -> None:
    samples, _ = confirmed_with_evidence(evaluation_repository)

    with pytest.raises(CalibrationError, match="GOLD_REVISION_STALE"):
        await run_calibration(
            evaluation_repository,
            profile=PROFILE,
            rubric=RUBRIC,
            judge=agreeing_judge(samples),
            evidence_for=lambda sample: sample["evidence"],
            samples=samples,
            gold_revision=99,
        )


async def test_calibration_never_rewrites_gold_labels(evaluation_repository: Any) -> None:
    """校准只读 gold；复核结论是另一条追加记录，不能顺手改人工标签。"""
    samples, revision = confirmed_with_evidence(evaluation_repository)
    before = evaluation_repository.list_gold_samples()["data"]

    await run_calibration(
        evaluation_repository,
        profile=PROFILE,
        rubric=RUBRIC,
        judge=agreeing_judge(samples, disagree=["gold-01"]),
        evidence_for=lambda sample: sample["evidence"],
        samples=samples,
        gold_revision=revision,
    )

    after = evaluation_repository.list_gold_samples()["data"]
    assert [item["label_status"] for item in after] == ["confirmed"] * 10
    assert [item["expected_verdict"] for item in after] == [
        item["expected_verdict"] for item in before
    ]
    assert [item["expected_scores"] for item in after] == [
        item["expected_scores"] for item in before
    ]
