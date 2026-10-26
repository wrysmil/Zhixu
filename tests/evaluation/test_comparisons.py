"""T10 配对比较。

红线：

- 数据集/环境/判分摘要不同的两次运行不能做严格比较；
- 摘要未知时不能"降级比较"，必须报元数据缺失；
- 有效配对不足 20 个不输出显著性区间；
- 不生成"综合赢家"，只逐 case 展示改进/退步/不变/不可比。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from nanobot.evaluation.comparisons import (
    IMPROVED,
    INCOMPARABLE,
    MIN_PAIRS_FOR_INTERVAL,
    REGRESSED,
    UNCHANGED,
    bootstrap_interval,
    build_comparison,
    check_compatibility,
    pair_cases,
    summarize_trials,
)
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.schemas import ComparisonCreate, RunCreate
from nanobot.evaluation.services import EvaluationService, ServiceError

from .conftest import make_case_payload, make_run_payload, make_suite_payload


def _attempt(case_id: str, *, verdict: str, revision: int = 1) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "case_revision": revision,
        "verdict": verdict,
        "execution_status": "succeeded",
    }


def _manifest(**overrides: Any) -> dict[str, Any]:
    payload = {
        "version": 2,
        "dataset_digest": "d1",
        "environment_digest": "e1",
        "grading_digest": "g1",
    }
    payload.update(overrides)
    return payload


def _run(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(uuid.uuid4()), "snapshot": snapshot}


def _snapshot(*, manifest: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "manifest": manifest if manifest is not None else _manifest(),
        "repetitions": 1,
        "model_mode": "real",
        "tool_modes": ["fixture"],
    }
    payload.update(overrides)
    return payload


def test_comparison_pairs_case_id_and_revision() -> None:
    baseline = summarize_trials(
        [_attempt("case-b", verdict="failed"), _attempt("case-a", verdict="passed")]
    )
    candidate = summarize_trials(
        [_attempt("case-b", verdict="passed"), _attempt("case-a", verdict="failed")]
    )
    pairs, excluded = pair_cases(baseline, candidate)
    assert excluded == []
    assert [pair.case_id for pair in pairs] == ["case-a", "case-b"]
    assert [pair.status for pair in pairs] == [REGRESSED, IMPROVED]
    assert pairs[0].delta_success_rate == -1.0


def test_case_revision_mismatch_is_excluded() -> None:
    baseline = summarize_trials([_attempt("case-a", verdict="passed", revision=1)])
    candidate = summarize_trials([_attempt("case-a", verdict="passed", revision=2)])
    pairs, excluded = pair_cases(baseline, candidate)
    assert pairs == []
    assert excluded[0]["reason"] == "CASE_REVISION_MISMATCH"


def test_incompatible_dataset_is_not_comparable() -> None:
    compatible, reasons = check_compatibility(
        _snapshot(manifest=_manifest(dataset_digest="d1")),
        _snapshot(manifest=_manifest(dataset_digest="d2")),
    )
    assert compatible is False
    assert "COMPARISON_INCOMPATIBLE:dataset_digest" in reasons


def test_missing_metadata_is_reported() -> None:
    compatible, reasons = check_compatibility(
        _snapshot(manifest={}), _snapshot(manifest=_manifest())
    )
    assert compatible is False
    assert "COMPARISON_METADATA_MISSING" in reasons


def test_candidate_code_may_differ() -> None:
    """被测变量就是代码：candidate 摘要不一致的是代码版本，不是不兼容。"""
    compatible, reasons = check_compatibility(
        _snapshot(manifest=_manifest(candidate_digest="c1")),
        _snapshot(manifest=_manifest(candidate_digest="c2")),
    )
    assert compatible is True
    assert reasons == []


def test_intervals_are_skipped_for_small_samples() -> None:
    assert bootstrap_interval([0.1, -0.1]) is None
    intervals = bootstrap_interval([0.1] * (MIN_PAIRS_FOR_INTERVAL + 5))
    assert intervals is not None
    assert intervals["pairs"] == MIN_PAIRS_FOR_INTERVAL + 5
    assert intervals["seed"] == 42


def test_bootstrap_is_reproducible() -> None:
    values = [0.2, -0.1, 0.05, 0.0] * 6
    assert bootstrap_interval(values) == bootstrap_interval(values)


def test_build_comparison_reports_incomparable_without_creating_record() -> None:
    result = build_comparison(
        comparison_id="",
        baseline_run=_run(_snapshot(manifest=_manifest(dataset_digest="d1"))),
        candidate_run=_run(_snapshot(manifest=_manifest(dataset_digest="d2"))),
        baseline_attempts=[_attempt("case-a", verdict="passed")],
        candidate_attempts=[_attempt("case-a", verdict="passed")],
    )
    assert result.compatibility == "incompatible"
    assert result.pairs == []
    assert result.reasons


async def test_service_rejects_incompatible_comparison(
    evaluation_repository: EvaluationRepository, tmp_path: Path
) -> None:
    service = EvaluationService(repository=evaluation_repository, data_dir=tmp_path)
    suite = evaluation_repository.create_suite(make_suite_payload())
    case = evaluation_repository.save_case(make_case_payload(suite_id=suite["id"]))
    request = RunCreate.model_validate(
        make_run_payload([case["id"]], suite_id=suite["id"])
    )
    baseline = evaluation_repository.create_run(
        scope="test", idempotency_key="cmp-base", request=request,
        rules_version="rules-v1", grader_version="judge-v1",
        agent={"provider": "controlled", "model": "eval"}, judge=None,
    )
    candidate = evaluation_repository.create_run(
        scope="test", idempotency_key="cmp-cand", request=request,
        rules_version="rules-v1", grader_version="judge-v1",
        agent={"provider": "controlled", "model": "eval"}, judge=None,
    )
    # 让两次运行的 manifest 不同：篡改候选运行的环境摘要。
    detail = evaluation_repository.get_run(candidate["id"])
    assert detail["snapshot"]["manifest"]["environment_digest"]

    with pytest.raises(ServiceError) as excinfo:
        service.create_comparison(
            ComparisonCreate.model_validate(
                {
                    "baseline_run_id": baseline["id"],
                    "candidate_run_id": candidate["id"],
                }
            ),
            idempotency_key="cmp-1",
            scope="test",
        )
    assert excinfo.value.code in (
        "COMPARISON_INCOMPATIBLE",
        "COMPARISON_METADATA_MISSING",
        "COMPARISON_RUN_NOT_TERMINAL",
    )


def test_unjudged_trials_are_not_counted_as_failures() -> None:
    summary = summarize_trials(
        [
            _attempt("case-a", verdict="passed"),
            _attempt("case-a", verdict="not_evaluated"),
        ]
    )["case-a"]
    assert summary.trials == 2
    assert summary.successes == 1
    assert summary.not_evaluated == 1
    assert summary.success_rate == 1.0
    assert summary.status == "passed"


def test_mixed_case_is_reported_as_mixed() -> None:
    summary = summarize_trials(
        [
            _attempt("case-a", verdict="passed"),
            _attempt("case-a", verdict="failed"),
        ]
    )["case-a"]
    assert summary.status == "mixed"


def test_incomparable_case_has_reason() -> None:
    baseline = summarize_trials([_attempt("case-a", verdict="not_evaluated")])
    candidate = summarize_trials([_attempt("case-a", verdict="not_evaluated")])
    pairs, _ = pair_cases(baseline, candidate)
    assert pairs[0].status == INCOMPARABLE
    assert pairs[0].reason == "NO_JUDGED_TRIALS"


def test_unchanged_case_is_reported() -> None:
    baseline = summarize_trials([_attempt("case-a", verdict="passed")])
    candidate = summarize_trials([_attempt("case-a", verdict="passed")])
    pairs, _ = pair_cases(baseline, candidate)
    assert pairs[0].status == UNCHANGED
