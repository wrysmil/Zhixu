"""WU-08：记忆比较报告（同一不可变快照 + 自包含 HTML）。

红线：

- API 视图与 HTML 来自同一份快照摘要，重复渲染逐字节稳定；
- 所有用户文本被转义，不出现 ``<script>``/事件属性/``javascript:``/外链；
- 改善、退化、未知三组逐题都列出，缺失场景单独列"被排除"；
- 成本拆写入/查询/Judge，未知显示"未知"而不是 0；
- 快照里混入的 gold/artifact 路径不会被渲染；
- 少于 20 个 cluster 时明确写"不输出区间"。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

from nanobot.evaluation.memory.report import (
    COMPARISON_SNAPSHOT_VERSION,
    MemoryComparisonSnapshot,
    build_comparison_snapshot,
    render_memory_comparison_report,
)

MALICIOUS_SCENARIO = "<script>alert(1)</script>"
MALICIOUS_CLUSTER = '"><img src=x onerror=alert(2)>'
MALICIOUS_REASON = "javascript:alert(3)"
MALICIOUS_CANDIDATE = "https://evil.example/candidate"


# --------------------------------------------------------------------------- 夹具


def _metric_entry(
    metric_id: str,
    unit: str,
    *,
    baseline: float | None,
    candidate: float | None,
    complete: bool = True,
    ratio: float | None = None,
) -> dict[str, object]:
    return {
        "baseline": baseline,
        "candidate": candidate,
        "delta": None if baseline is None or candidate is None else candidate - baseline,
        "ratio": ratio if complete else None,
        "unit": unit,
        "direction": "lower_is_better",
        "aggregation": "sum",
        "coverage": {
            "baseline": {"scored": 2 if baseline is not None else 0, "planned": 2},
            "candidate": {"scored": 1 if candidate is not None else 0, "planned": 2},
        },
        "complete": complete,
    }


def _comparison() -> dict[str, object]:
    return {
        "id": "comparison-1",
        "status": "incomplete",
        "measurement_status": "incomplete",
        "compatibility": "strict",
        "baseline_experiment_id": "exp-baseline",
        "candidate_experiment_id": "exp-candidate",
        "baseline_candidate_id": "cand-a",
        "candidate_candidate_id": MALICIOUS_CANDIDATE,
        "track": "end_to_end",
        "split": "development",
        "primary_metric": "qa_accuracy_scenario_macro",
        "repetitions": 2,
        "changed_fields": ["/candidates/0/memory_settings/recall_limit"],
        "applied_changes": ["/candidates/0/memory_settings/recall_limit"],
        "model_changes": [],
        "attribution": {"single_factor": True, "reasons": [], "model_changes": []},
        "paired": 2,
        "planned": 3,
        "excluded": [
            {
                "scenario_id": "s-excluded",
                "reason": "SCENE_NOT_FULLY_JUDGEABLE",
                "details": ["METRIC_NOT_SCORED:q1"],
                "artifact_path": "C:/private/attempts/s-excluded",
            }
        ],
        "coverage": {
            "scenes": {"paired": 2, "planned": 3},
            "trials": {
                "baseline": {"graded": 4, "planned": 6, "duplicates": 0, "ungraded": 0},
                "candidate": {"graded": 4, "planned": 6, "duplicates": 0, "ungraded": 0},
            },
            "probes": {
                "baseline": {"scored": 8, "planned": 8},
                "candidate": {"scored": 8, "planned": 8},
            },
        },
        "scenario_macro": {"baseline": 0.5, "candidate": 0.75, "delta": 0.25, "delta_pp": 25.0},
        "probe_micro": {"baseline": 0.5, "candidate": 0.625, "delta_pp": 12.5},
        "category_macro": {
            "overall": {"baseline": 0.5, "candidate": 0.75, "delta_pp": 25.0, "categories": 1},
            "categories": [
                {
                    "category": "fact",
                    "scenes": 2,
                    "baseline": 0.5,
                    "candidate": 0.75,
                    "delta_pp": 25.0,
                }
            ],
        },
        "primary": {
            "metric_id": "qa_accuracy_scenario_macro",
            "scope": "scenario",
            "baseline": 0.5,
            "candidate": 0.75,
            "delta": 0.25,
            "delta_pp": 25.0,
            "statistical_direction": "inconclusive",
            "subset_direction": "improved",
            "interval_pp": None,
        },
        "interval": None,
        "secondary": [
            {
                "metric_id": "grounded_memory_success",
                "unit": "ratio",
                "direction": "higher_is_better",
                "baseline": 0.5,
                "candidate": 0.75,
                "delta_pp": 25.0,
                "improvement_pp": 25.0,
                "interval_pp": [10.0, 40.0],
                "improvement_interval_pp": [10.0, 40.0],
                "statistical_direction": "improved",
                "pairs": 2,
                "missing": 1,
                "clusters": 2,
            },
            {
                "metric_id": "scope_leak_rate",
                "unit": "ratio",
                "direction": "lower_is_better",
                "baseline": 0.0,
                "candidate": None,
                "delta_pp": None,
                "improvement_pp": None,
                "interval_pp": None,
                "improvement_interval_pp": None,
                "statistical_direction": "inconclusive",
                "pairs": 1,
                "missing": 1,
                "clusters": None,
            },
        ],
        "cost": {
            "write": {
                "extraction_tokens": _metric_entry(
                    "extraction_tokens", "token", baseline=60.0, candidate=80.0, ratio=80 / 60
                ),
                "estimated_cost": _metric_entry(
                    "estimated_cost",
                    "currency",
                    baseline=None,
                    candidate=None,
                    complete=False,
                ),
            },
            "query": {
                "retrieve_latency_p95_ms": _metric_entry(
                    "retrieve_latency_p95_ms", "ms", baseline=120.0, candidate=180.0,
                    complete=False,
                ),
            },
            "judge": {
                "judge_tokens": _metric_entry(
                    "judge_tokens", "token", baseline=20.0, candidate=15.0, complete=False
                ),
            },
            "totals": {
                "model_requests": _metric_entry(
                    "model_requests", "count", baseline=40.0, candidate=40.0, ratio=1.0
                ),
                "estimated_cost": _metric_entry(
                    "estimated_cost", "currency", baseline=None, candidate=None, complete=False
                ),
            },
            "summary": {
                "cost_ratio": None,
                "p95_latency_ratio": None,
                "cost_unknown": True,
                "note": "未知成本保持 null，不填 0。",
            },
        },
        "gates": {
            "scope_leak": {
                "baseline_failed": False,
                "candidate_failed": False,
                "baseline_values": [0.0],
                "candidate_values": [0.0],
                "evidence": [],
            },
            "deletion_resurrection": {
                "baseline_failed": False,
                "candidate_failed": False,
                "baseline_values": [1.0],
                "candidate_values": [1.0],
                "evidence": [],
            },
            "blocked": False,
            "critical_violations": [],
        },
        "statistical_direction": "inconclusive",
        "subset_statistical_direction": "improved",
        "adoption_decision": {
            "decision": "review_required",
            "reasons": ["INTERVAL_UNAVAILABLE", MALICIOUS_REASON],
            "checks": {
                "minimum_effect_pp": None,
                "statistical_direction": "inconclusive",
                "delta_pp": 25.0,
                "interval_pp": None,
                "critical_violations": [],
                "cost_ratio": None,
                "p95_latency_ratio": None,
                "regressions": {
                    "grounded_memory_success": {
                        "tolerance_pp": 2.0,
                        "interval_pp": [10.0, 40.0],
                        "verdict": "within_tolerance",
                    }
                },
                "budgets": {
                    "cost_ratio": {"limit": 1.5, "actual": None, "verdict": "unknown"}
                },
            },
        },
        "uncalibrated_metrics": [],
        "official_protocol": {
            "separated": True,
            "metrics": [
                {
                    "metric_id": "longmemeval_binary_judge",
                    "official": False,
                    "note": "平台近似判定，非官方口径。",
                    "baseline": 0.5,
                    "candidate": 0.5,
                }
            ],
        },
        "scene_pairs": [
            {
                "scenario_id": "s000",
                "revision": 1,
                "cluster_id": "cluster-000",
                "category": "fact",
                "categories": ["fact"],
                "status": "improved",
                "baseline": 0.5,
                "candidate": 1.0,
                "delta": 0.5,
                "delta_pp": 50.0,
                "baseline_trials": 2,
                "candidate_trials": 2,
                "probe_changes": [
                    {"probe_id": "q1", "baseline": 1.0, "candidate": 1.0}
                ],
                "reasons": [],
            },
            {
                "scenario_id": "s001",
                "revision": 1,
                "cluster_id": MALICIOUS_CLUSTER,
                "category": MALICIOUS_SCENARIO,
                "categories": [MALICIOUS_SCENARIO],
                "status": "regressed",
                "baseline": 0.5,
                "candidate": 0.0,
                "delta": -0.5,
                "delta_pp": -50.0,
                "baseline_trials": 2,
                "candidate_trials": 2,
                "probe_changes": [],
                "reasons": [MALICIOUS_REASON],
            },
            {
                "scenario_id": "s002",
                "revision": 1,
                "cluster_id": "cluster-002",
                "category": "fact",
                "categories": ["fact"],
                "status": "incomparable",
                "baseline": None,
                "candidate": 1.0,
                "delta": None,
                "delta_pp": None,
                "baseline_trials": 1,
                "candidate_trials": 2,
                "probe_changes": [],
                "reasons": ["TRIAL_MISSING"],
            },
        ],
        "reasons": ["INCOMPLETE_PAIRING", "INTERVALS_SKIPPED:CLUSTERS_LT_20"],
        "required_metrics_missing": [],
        "bootstrap": {
            "version": "paired-cluster-percentile/v1",
            "seed": 42,
            "rounds": 2000,
            "confidence": 0.95,
            "min_clusters": 20,
        },
        "gold_path": "C:/private/gold.jsonl",
    }


# --------------------------------------------------------------------------- 结构


def test_report_is_self_contained_html_without_scripts_or_links() -> None:
    document = render_memory_comparison_report(_comparison())
    assert document.startswith("<!doctype html>")
    assert '<html lang="zh-CN">' in document
    assert document.rstrip().endswith("</html>")
    assert "<style>" in document
    assert "<script" not in document
    assert "javascript:" not in document
    assert "http://" not in document
    assert "https://" not in document
    assert "foreignObject" not in document
    assert "<link" not in document
    assert "<iframe" not in document
    assert "@import" not in document
    assert "url(" not in document
    # 任何事件属性（onclick=/onerror=...）都不允许出现。
    assert re.search(r"\son[a-zA-Z]+\s*=", document) is None


def test_untrusted_text_is_escaped_everywhere() -> None:
    document = render_memory_comparison_report(_comparison())
    assert MALICIOUS_SCENARIO not in document
    assert "<img" not in document
    assert "onerror=" not in document
    assert "&lt;script&gt;" in document
    assert "&quot;&gt;&lt;img" in document
    assert "javascript&#58;" in document
    # 明文 URL 不能出现（协议冒号被编码），只保留可读路径文本。
    assert "https&#58;//evil.example/candidate" in document


def test_report_lists_improved_regressed_and_unknown_groups() -> None:
    document = render_memory_comparison_report(_comparison())
    assert 'data-group="improved"' in document
    assert 'data-group="regressed"' in document
    assert 'data-group="incomparable"' in document
    assert "改善（1）" in document
    assert "退化（1）" in document
    assert "未知/不可比（1）" in document
    assert "被排除场景" in document
    assert "s-excluded" in document
    assert "TRIAL_MISSING" in document


# --------------------------------------------------------------------------- 快照


def test_api_and_html_share_the_same_immutable_snapshot() -> None:
    comparison = _comparison()
    snapshot = build_comparison_snapshot(comparison)
    assert isinstance(snapshot, MemoryComparisonSnapshot)
    api = snapshot.to_dict()
    assert api["snapshot_version"] == COMPARISON_SNAPSHOT_VERSION
    assert api["digest"] == snapshot.digest

    canonical = json.dumps(
        api["comparison"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == snapshot.digest

    # 同一快照三处渲染逐字节一致。
    assert render_memory_comparison_report(api) == render_memory_comparison_report(comparison)
    assert snapshot.to_html() == render_memory_comparison_report(snapshot)

    # 调用方改 API 视图不能污染快照本身。
    api["comparison"]["paired"] = 999
    assert snapshot.comparison["paired"] == 2
    assert '"paired":999' not in snapshot.to_html()


def test_result_object_with_to_dict_is_accepted() -> None:
    class FakeResult:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def to_dict(self) -> dict[str, object]:
            return copy.deepcopy(self._payload)

    document = render_memory_comparison_report(FakeResult(_comparison()))
    assert document.startswith("<!doctype html>")
    assert "配对场景" in document


def test_renderer_does_not_mutate_input_and_is_deterministic() -> None:
    comparison = _comparison()
    frozen = copy.deepcopy(comparison)
    first = render_memory_comparison_report(comparison)
    second = render_memory_comparison_report(comparison)
    assert first == second
    assert comparison == frozen


def test_snapshot_digest_is_stable_across_key_order() -> None:
    comparison = _comparison()
    reordered = dict(reversed(list(comparison.items())))
    assert build_comparison_snapshot(comparison).digest == build_comparison_snapshot(
        reordered
    ).digest


# --------------------------------------------------------------------------- 成本与未知


def _row(document: str, metric_id: str) -> str:
    start = document.index(f'data-metric="{metric_id}"')
    end = document.index("</tr>", start)
    return document[start:end]


def test_cost_is_split_into_write_query_and_judge() -> None:
    document = render_memory_comparison_report(_comparison())
    assert "<h3>写入</h3>" in document
    assert "<h3>查询</h3>" in document
    assert "<h3>Judge</h3>" in document
    write_row = _row(document, "extraction_tokens")
    assert "60 Token" in write_row
    assert "80 Token" in write_row


def test_unknown_cost_is_rendered_as_unknown_not_zero() -> None:
    document = render_memory_comparison_report(_comparison())
    judge_row = _row(document, "judge_tokens")
    assert "未知" in judge_row
    assert "<td>0 Token</td>" not in judge_row
    assert "0.00×" not in judge_row
    cost_row = _row(document, "estimated_cost")
    assert cost_row.count("未知") >= 2
    assert "费用比：未知" in document
    assert "p95 时延比：未知" in document
    # 未校准/未知分项不发明分数。
    scope_row = _row(document, "scope_leak_rate")
    assert "未知" in scope_row


def test_missing_interval_is_explicitly_stated() -> None:
    document = render_memory_comparison_report(_comparison())
    assert "不输出区间" in document
    assert "独立 cluster 少于 20" in document
    assert 'data-interval="primary"' in document


# --------------------------------------------------------------------------- 数据边界


def test_gold_and_artifact_paths_are_never_rendered() -> None:
    comparison = _comparison()
    document = render_memory_comparison_report(comparison)
    assert "C:/private" not in document
    assert "gold.jsonl" not in document
    assert "artifact_path" not in document
    assert "gold_path" not in document


def test_official_protocol_scores_are_separated() -> None:
    document = render_memory_comparison_report(_comparison())
    assert "官方协议单列" in document
    assert "longmemeval_binary_judge" in document
    assert "否（平台近似判定）" in document
    assert "官方分与平台分数不混算" in document


def test_statistics_note_pagination_does_not_change_denominators() -> None:
    document = render_memory_comparison_report(_comparison())
    assert "逐题列表分页不会重算或缩减分母" in document
    assert "配对场景" in document
    assert "2 / 3" in document


def test_zero_violation_note_is_present() -> None:
    document = render_memory_comparison_report(_comparison())
    assert "不等于安全保证" in document
    assert "不以「未显著」豁免" in document


def test_decision_and_direction_are_rendered_separately() -> None:
    document = render_memory_comparison_report(_comparison())
    assert "统计方向" in document
    assert "采用建议" in document
    assert "需复核" in document
    assert "统计方向只回答" in document


def test_empty_comparison_still_renders_valid_document() -> None:
    document = render_memory_comparison_report({})
    assert document.startswith("<!doctype html>")
    assert "未知" in document
    assert "<script" not in document
