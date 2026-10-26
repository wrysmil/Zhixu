"""配对比较（契约 §6）。

严格比较的前提：两次运行的 dataset/environment/grading 摘要一致，
repetitions、model_mode、工具模式一致。任一必需摘要未知 → 元数据缺失；
摘要不一致 → 不兼容。**candidate 的代码可以不同**——那正是被测变量。

配对口径：先按 case 聚合 trial，再按 case 配对比较成功率；
不拿两份均值相减就宣称有效，也不把重复 trial 当独立任务扩大样本。

区间估计：有效配对 case 达到 20 个才启用 case 级 bootstrap（seed=42，2000 次）；
不足 20 个只描述差值，不输出显著性结论——这是本项目的显示策略，不是统计学保证。
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

BOOTSTRAP_SEED = 42
BOOTSTRAP_ROUNDS = 2000
MIN_PAIRS_FOR_INTERVAL = 20

IMPROVED = "improved"
REGRESSED = "regressed"
UNCHANGED = "unchanged"
INCOMPARABLE = "incomparable"

#: 严格比较要求一致的摘要字段。
REQUIRED_DIGESTS = ("dataset_digest", "environment_digest", "grading_digest")


class ComparisonError(RuntimeError):
    """比较失败；code 与契约 §6 的错误码一致。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class TrialSummary:
    """一个 case 在一次运行里的多次试验汇总。"""

    case_id: str
    case_revision: int
    trials: int
    successes: int
    failures: int
    review_required: int
    not_evaluated: int

    @property
    def success_rate(self) -> float | None:
        """成功率分母只算已判定的试验；未判定的不当失败。"""
        judged = self.successes + self.failures
        if judged <= 0:
            return None
        return self.successes / judged

    @property
    def status(self) -> str:
        if self.successes > 0 and self.failures > 0:
            return "mixed"
        if self.successes > 0:
            return "passed"
        if self.failures > 0:
            return "failed"
        return "unknown"


@dataclass(frozen=True)
class Pair:
    """一个 case 的配对结果。"""

    case_id: str
    case_revision: int
    baseline: TrialSummary
    candidate: TrialSummary
    status: str
    reason: str = ""

    @property
    def delta_success_rate(self) -> float | None:
        return _delta(self.baseline.success_rate, self.candidate.success_rate)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_revision": self.case_revision,
            "status": self.status,
            "reason": self.reason,
            "baseline": _summary_dict(self.baseline),
            "candidate": _summary_dict(self.candidate),
            "delta_success_rate": self.delta_success_rate,
        }


@dataclass
class ComparisonResult:
    """一次比较的读模型。"""

    comparison_id: str
    baseline_run_id: str
    candidate_run_id: str
    mode: str
    compatibility: str
    pairs: list[Pair] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    intervals: dict[str, Any] | None = None
    excluded: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.comparison_id,
            "baseline_run_id": self.baseline_run_id,
            "candidate_run_id": self.candidate_run_id,
            "mode": self.mode,
            "compatibility": self.compatibility,
            "pairs": [pair.to_dict() for pair in self.pairs],
            "metrics": dict(self.metrics),
            "intervals": self.intervals,
            "excluded": list(self.excluded),
            "reasons": list(self.reasons),
        }


def _summary_dict(summary: TrialSummary) -> dict[str, Any]:
    return {
        "case_id": summary.case_id,
        "case_revision": summary.case_revision,
        "trials": summary.trials,
        "successes": summary.successes,
        "failures": summary.failures,
        "review_required": summary.review_required,
        "not_evaluated": summary.not_evaluated,
        "success_rate": summary.success_rate,
        "status": summary.status,
    }


def _delta(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None:
        return None
    return candidate - baseline


def summarize_trials(attempts: Sequence[Mapping[str, Any]]) -> dict[str, TrialSummary]:
    """按 case 聚合 trial；未判定的试验单独计数，不当失败。"""
    buckets: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        case_id = str(attempt.get("case_id") or "")
        if not case_id:
            continue
        bucket = buckets.setdefault(
            case_id,
            {
                "case_revision": int(attempt.get("case_revision") or 0),
                "trials": 0,
                "successes": 0,
                "failures": 0,
                "review_required": 0,
                "not_evaluated": 0,
            },
        )
        bucket["trials"] += 1
        verdict = str(attempt.get("verdict") or "not_evaluated")
        if verdict == "passed":
            bucket["successes"] += 1
        elif verdict == "failed":
            bucket["failures"] += 1
        elif verdict == "review_required":
            bucket["review_required"] += 1
        else:
            bucket["not_evaluated"] += 1
    return {
        case_id: TrialSummary(
            case_id=case_id,
            case_revision=int(data["case_revision"]),
            trials=int(data["trials"]),
            successes=int(data["successes"]),
            failures=int(data["failures"]),
            review_required=int(data["review_required"]),
            not_evaluated=int(data["not_evaluated"]),
        )
        for case_id, data in buckets.items()
    }


def check_compatibility(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> tuple[bool, list[str]]:
    """严格比较的前置检查；返回 (是否可比, 原因列表)。"""
    reasons: list[str] = []
    baseline_manifest = baseline.get("manifest") or {}
    candidate_manifest = candidate.get("manifest") or {}
    if not baseline_manifest or not candidate_manifest:
        return False, ["COMPARISON_METADATA_MISSING"]
    for digest in REQUIRED_DIGESTS:
        left = baseline_manifest.get(digest)
        right = candidate_manifest.get(digest)
        if not left or not right:
            reasons.append(f"COMPARISON_METADATA_MISSING:{digest}")
            continue
        if left != right:
            reasons.append(f"COMPARISON_INCOMPATIBLE:{digest}")
    for name in ("repetitions", "model_mode"):
        left = baseline.get(name)
        right = candidate.get(name)
        if left is None or right is None:
            reasons.append(f"COMPARISON_METADATA_MISSING:{name}")
        elif left != right:
            reasons.append(f"COMPARISON_INCOMPATIBLE:{name}")
    if sorted(baseline.get("tool_modes") or []) != sorted(candidate.get("tool_modes") or []):
        reasons.append("COMPARISON_INCOMPATIBLE:tool_modes")
    return (not reasons), reasons


def pair_cases(
    baseline: Mapping[str, TrialSummary], candidate: Mapping[str, TrialSummary]
) -> tuple[list[Pair], list[dict[str, Any]]]:
    """按 case 配对；只在两边都出现的 case 上比较。"""
    pairs: list[Pair] = []
    excluded: list[dict[str, Any]] = []
    for case_id in sorted(set(baseline) | set(candidate)):
        left = baseline.get(case_id)
        right = candidate.get(case_id)
        if left is None or right is None:
            excluded.append(
                {
                    "case_id": case_id,
                    "reason": "CASE_MISSING_IN_BASELINE"
                    if right is not None
                    else "CASE_MISSING_IN_CANDIDATE",
                }
            )
            continue
        if left.case_revision != right.case_revision:
            excluded.append(
                {
                    "case_id": case_id,
                    "reason": "CASE_REVISION_MISMATCH",
                    "baseline_revision": left.case_revision,
                    "candidate_revision": right.case_revision,
                }
            )
            continue
        base_rate = left.success_rate
        candidate_rate = right.success_rate
        if base_rate is None or candidate_rate is None:
            status, reason = INCOMPARABLE, "NO_JUDGED_TRIALS"
        elif candidate_rate > base_rate:
            status, reason = IMPROVED, ""
        elif candidate_rate < base_rate:
            status, reason = REGRESSED, ""
        else:
            status, reason = UNCHANGED, ""
        pairs.append(
            Pair(
                case_id=case_id,
                case_revision=left.case_revision,
                baseline=left,
                candidate=right,
                status=status,
                reason=reason,
            )
        )
    return pairs, excluded


def bootstrap_interval(
    deltas: Sequence[float], *, seed: int = BOOTSTRAP_SEED, rounds: int = BOOTSTRAP_ROUNDS
) -> dict[str, Any] | None:
    """case 级配对 bootstrap；样本不足时返回 None（不输出显著性结论）。"""
    values = [float(item) for item in deltas]
    if len(values) < MIN_PAIRS_FOR_INTERVAL:
        return None
    rng = random.Random(seed)
    means: list[float] = []
    size = len(values)
    for _ in range(rounds):
        sample = [values[rng.randrange(size)] for _ in range(size)]
        means.append(sum(sample) / size)
    means.sort()
    low = means[int(0.025 * rounds)]
    high = means[min(rounds - 1, int(0.975 * rounds))]
    observed = sum(values) / size
    return {
        "mean_delta": observed,
        "ci_low": low,
        "ci_high": high,
        "rounds": rounds,
        "seed": seed,
        "pairs": size,
    }


def build_comparison(
    *,
    comparison_id: str,
    baseline_run: Mapping[str, Any],
    candidate_run: Mapping[str, Any],
    baseline_attempts: Sequence[Mapping[str, Any]],
    candidate_attempts: Sequence[Mapping[str, Any]],
    mode: str = "strict",
) -> ComparisonResult:
    """构建一次比较的完整读模型。"""
    compatible, reasons = check_compatibility(
        {
            "manifest": baseline_run.get("snapshot", {}).get("manifest"),
            "repetitions": baseline_run.get("snapshot", {}).get("repetitions"),
            "model_mode": baseline_run.get("snapshot", {}).get("model_mode"),
            "tool_modes": baseline_run.get("snapshot", {}).get("tool_modes"),
        },
        {
            "manifest": candidate_run.get("snapshot", {}).get("manifest"),
            "repetitions": candidate_run.get("snapshot", {}).get("repetitions"),
            "model_mode": candidate_run.get("snapshot", {}).get("model_mode"),
            "tool_modes": candidate_run.get("snapshot", {}).get("tool_modes"),
        },
    )
    baseline_trials = summarize_trials(baseline_attempts)
    candidate_trials = summarize_trials(candidate_attempts)
    pairs, excluded = pair_cases(baseline_trials, candidate_trials)
    if not compatible:
        return ComparisonResult(
            comparison_id=comparison_id,
            baseline_run_id=str(baseline_run.get("id") or ""),
            candidate_run_id=str(candidate_run.get("id") or ""),
            mode=mode,
            compatibility="incompatible",
            pairs=[],
            metrics={},
            intervals=None,
            excluded=excluded,
            reasons=reasons,
        )

    deltas = [
        pair.candidate.success_rate - pair.baseline.success_rate
        for pair in pairs
        if pair.baseline.success_rate is not None
        and pair.candidate.success_rate is not None
    ]
    metrics = {
        "compared_cases": len(pairs),
        "improved_cases": sum(1 for pair in pairs if pair.status == IMPROVED),
        "regressed_cases": sum(1 for pair in pairs if pair.status == REGRESSED),
        "unchanged_cases": sum(1 for pair in pairs if pair.status == UNCHANGED),
        "incomparable_cases": sum(1 for pair in pairs if pair.status == INCOMPARABLE),
        "task_success_rate_delta": (
            sum(deltas) / len(deltas) if deltas else None
        ),
        "baseline_success_rate": _mean(
            [pair.baseline.success_rate for pair in pairs]
        ),
        "candidate_success_rate": _mean(
            [pair.candidate.success_rate for pair in pairs]
        ),
    }
    intervals = bootstrap_interval(deltas)
    reasons = []
    if intervals is None:
        reasons.append(
            f"INTERVALS_SKIPPED:PAIRS_LT_{MIN_PAIRS_FOR_INTERVAL}"
        )
    return ComparisonResult(
        comparison_id=comparison_id,
        baseline_run_id=str(baseline_run.get("id") or ""),
        candidate_run_id=str(candidate_run.get("id") or ""),
        mode=mode,
        compatibility="strict",
        pairs=pairs,
        metrics=metrics,
        intervals=intervals,
        excluded=excluded,
        reasons=reasons,
    )


def _mean(values: Sequence[float | None]) -> float | None:
    known = [float(item) for item in values if item is not None]
    if not known:
        return None
    return sum(known) / len(known)
