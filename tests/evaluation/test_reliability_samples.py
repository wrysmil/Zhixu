"""T12：36 条分层用例与评分器反例集的结构自洽与判分验证。

本文件只做三件事：

1. 结构自洽：36 条用例，Agent / Skill / 团队各 12 条，每目标内部 4 正常 + 4 边界 +
   2 恢复 + 2 安全；gold 一律 proposed；标签、环境依赖、失败定义齐备。
2. 判据可用：每条用例都能通过 CaseWrite 校验，并且每个必需维度都有属于自己的判据，
   否则该用例永远只能待复核，等于没有用例。
3. 反例真能被抓：用真实规则判分（graders/rules.py）与五维度判定（assessment.py）
   跑每条 grader 反例，断言它们被判为不合格或待复核，而不是被关键词放过。

本文件不 mock 判分逻辑，也不修改 nanobot/evaluation 下的任何模块。
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

import nanobot.evaluation
from nanobot.evaluation.assessment import DISQUALIFIED, QUALIFIED, REVIEW_REQUIRED, assess
from nanobot.evaluation.coverage import compute_coverage
from nanobot.evaluation.graders.rules import evaluate_rules
from nanobot.evaluation.schemas import FIXTURE_TOOLS, CaseWrite, TraceEvent

from .test_rules import FakeAttemptEvidence, FakeToolCallEvidence, artifact_for

SAMPLES_DIR = Path(nanobot.evaluation.__file__).parent / "samples"

TARGETS = ("agent", "skill", "team")
TIER_MIX = {"normal": 4, "edge": 4, "recovery": 2, "safety": 2}
SET_TAGS = ("capability", "regression", "holdout")
ENV_PREFIXES = (
    "fixture:",
    "tool:",
    "tool_fixture:",
    "team:",
    "member:",
    "skill:",
    "snapshot:",
    "identity_template:",
)
REQUIRED_EVIDENCE_KINDS = {
    "final_answer",
    "turns",
    "artifacts",
    "trace",
    "usage",
    "delegation",
}
DIMENSIONS = ("task", "constraints", "trajectory", "efficiency", "safety")
# 与 nanobot/evaluation/assessment.py 的断言维度映射保持一致。
PROCESS_KINDS = {"tool_trajectory", "team_member_executed"}
EFFICIENCY_KINDS = {"usage_limit", "duration_limit"}
SAFETY_KINDS = {"tool_forbidden"}
#: 用例自带的元数据字段：导入时会被剥掉，其余字段必须能被 CaseWrite 接受。
METADATA_KEYS = {
    "case_id",
    "target",
    "tier",
    "title",
    "task_input",
    "environment",
    "expected",
    "tool_criteria",
    "failure_definition",
    "acceptable_alternative_paths",
    "required_evidence",
    "gold",
    "mutation",
}


def _load(name: str) -> dict[str, Any]:
    return json.loads((SAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _strip(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key not in METADATA_KEYS}


def _kind_dimension(kind: str) -> str:
    if kind in PROCESS_KINDS:
        return "trajectory"
    if kind in EFFICIENCY_KINDS:
        return "efficiency"
    if kind in SAFETY_KINDS:
        return "safety"
    return "constraints"


@pytest.fixture(scope="module")
def reliability() -> dict[str, Any]:
    return _load("reliability-v2")


@pytest.fixture(scope="module")
def rubrics() -> dict[str, Any]:
    return _load("rubrics-v2")


@pytest.fixture(scope="module")
def counterexamples() -> dict[str, Any]:
    return _load("grader-counterexamples-v2")


def _cases_by_target(reliability: dict[str, Any], target: str) -> list[dict[str, Any]]:
    return [item for item in reliability["cases"] if item["target"] == target]


# ------------------------------------------------------------------ 结构自洽


def test_thirty_six_cases_split_evenly_across_targets(
    reliability: dict[str, Any],
) -> None:
    cases = reliability["cases"]
    assert len(cases) == 36
    assert Counter(item["target"] for item in cases) == {
        "agent": 12,
        "skill": 12,
        "team": 12,
    }
    ids = [str(item["case_id"]) for item in cases]
    assert len(set(ids)) == 36
    assert all(item_id.strip() for item_id in ids)


def test_each_target_has_the_declared_tier_mix(reliability: dict[str, Any]) -> None:
    for target in TARGETS:
        mix = Counter(item["tier"] for item in _cases_by_target(reliability, target))
        assert mix == TIER_MIX, f"{target} 的档位分布不符：{dict(mix)}"


def test_cases_are_valid_case_writes(reliability: dict[str, Any]) -> None:
    for raw in reliability["cases"]:
        case = CaseWrite.model_validate(_strip(raw))
        assert case.name.startswith(str(raw["case_id"]))
        assert case.execution.tool_mode == "fixture"
        assert case.assertions
        assert case.evaluation is not None
        assert case.evaluation.milestones, raw["case_id"]


def test_gold_labels_are_all_proposed(reliability: dict[str, Any]) -> None:
    for raw in reliability["cases"]:
        gold = raw["gold"]
        assert gold["status"] == "proposed", raw["case_id"]
        assert gold.get("reviewer") is None, raw["case_id"]
        assert gold.get("note", "").strip(), raw["case_id"]


def test_tags_mark_set_membership_and_stay_legal(
    reliability: dict[str, Any],
) -> None:
    for raw in reliability["cases"]:
        tags = list(raw["tags"])
        assert "reliability-v2" in tags
        assert raw["target"] in tags
        assert raw["tier"] in tags
        assert sum(1 for tag in tags if tag in SET_TAGS) == 1, raw["case_id"]
        assert all(1 <= len(tag) <= 40 for tag in tags), raw["case_id"]
    for target in TARGETS:
        holdout = [
            item
            for item in _cases_by_target(reliability, target)
            if "holdout" in item["tags"]
        ]
        assert len(holdout) >= 2, f"{target} 缺少保留集样本"
    assert any("capability" in item["tags"] for item in reliability["cases"])
    assert any("regression" in item["tags"] for item in reliability["cases"])


def test_environment_declares_dependencies_instead_of_faking_success(
    reliability: dict[str, Any],
) -> None:
    for raw in reliability["cases"]:
        environment = raw["environment"]
        required = list(environment["required"])
        assert required, f"{raw['case_id']} 必须声明环境依赖"
        for entry in required:
            assert entry.startswith(ENV_PREFIXES), (raw["case_id"], entry)
        assert environment["network"] is False, raw["case_id"]
        assert raw["failure_definition"].strip(), raw["case_id"]
        assert raw["acceptable_alternative_paths"], raw["case_id"]
        expected = raw["expected"]
        assert expected["reference_answer"].strip() or expected["artifacts"]
        assert raw["task_input"].strip()
        joined = " ".join(required)
        for fixture in _strip(raw).get("fixtures", []):
            assert fixture["path"] in joined, (raw["case_id"], fixture["path"])


def test_required_evidence_matches_case_evaluation(
    reliability: dict[str, Any],
) -> None:
    for raw in reliability["cases"]:
        case = CaseWrite.model_validate(_strip(raw))
        assert case.evaluation is not None
        required = list(raw["required_evidence"])
        assert required == list(case.evaluation.required_evidence), raw["case_id"]
        assert set(required) <= REQUIRED_EVIDENCE_KINDS, raw["case_id"]


def test_every_required_dimension_owns_at_least_one_criterion(
    reliability: dict[str, Any],
) -> None:
    """每个必需维度都要有非里程碑判据，否则该用例永远只能待复核。"""
    for raw in reliability["cases"]:
        case = CaseWrite.model_validate(_strip(raw))
        assert case.evaluation is not None
        milestone_ids = {
            aid
            for milestone in case.evaluation.milestones
            for aid in milestone.assertion_ids
        }
        usable = [
            assertion
            for assertion in case.assertions
            if assertion.required
            and assertion.id not in milestone_ids
            and _kind_dimension(assertion.kind) in case.evaluation.required_dimensions
        ]
        for dimension in case.evaluation.required_dimensions:
            if dimension == "task":
                # task 维度由里程碑判定，里程碑断言本身不进维度桶。
                assert case.evaluation.milestones
                continue
            assert any(
                _kind_dimension(assertion.kind) == dimension for assertion in usable
            ), f"{raw['case_id']} 的 {dimension} 维度没有判据"


def test_milestones_only_reference_existing_assertions(
    reliability: dict[str, Any],
) -> None:
    for raw in reliability["cases"]:
        case = CaseWrite.model_validate(_strip(raw))
        assert case.evaluation is not None
        ids = [assertion.id for assertion in case.assertions]
        assert len(set(ids)) == len(ids), raw["case_id"]
        for milestone in case.evaluation.milestones:
            assert milestone.assertion_ids, raw["case_id"]
            for aid in milestone.assertion_ids:
                assert aid in ids, (raw["case_id"], aid)
            assert milestone.weight > 0


def test_file_criteria_are_backed_by_declared_artifacts(
    reliability: dict[str, Any],
) -> None:
    """文件类判据必须有产物登记，否则判分永远读不到内容。"""
    for raw in reliability["cases"]:
        case = CaseWrite.model_validate(_strip(raw))
        declared = set(case.artifact_paths)
        for assertion in case.assertions:
            path = getattr(assertion, "path", None)
            if path is None:
                continue
            assert path in declared, (raw["case_id"], path)


def test_tool_criteria_match_the_machine_assertions(
    reliability: dict[str, Any],
) -> None:
    """元数据里的工具与成员判据必须能被断言兑现，不能只是文档。"""
    for raw in reliability["cases"]:
        case = CaseWrite.model_validate(_strip(raw))
        criteria = raw["tool_criteria"]
        used_tools: set[str] = set()
        forbidden_tools: set[str] = set()
        member_ids: set[str] = set()
        for assertion in case.assertions:
            if assertion.kind == "tool_called":
                used_tools.add(assertion.name)
            elif assertion.kind == "tool_forbidden":
                forbidden_tools.update(assertion.names)
            elif assertion.kind == "team_member_executed":
                member_ids.update(assertion.member_ids)
            elif assertion.kind == "tool_trajectory":
                used_tools.update(step.name for step in assertion.steps)
        assert set(criteria["required_tools"]) <= used_tools, raw["case_id"]
        assert set(criteria["forbidden_tools"]) <= forbidden_tools, raw["case_id"]
        assert set(criteria["member_ids"]) <= member_ids, raw["case_id"]


def test_fixture_baseline_contains_no_real_network_sample(
    reliability: dict[str, Any],
) -> None:
    for raw in reliability["cases"]:
        case = CaseWrite.model_validate(_strip(raw))
        assert case.execution.tool_mode == "fixture", raw["case_id"]
        assert set(case.execution.allowed_tools) <= FIXTURE_TOOLS, raw["case_id"]
        assert "web_search" not in case.execution.allowed_tools
        assert "web_fetch" not in case.execution.allowed_tools


# ------------------------------------------------------------------ 量表文件


def test_rubrics_cover_the_five_dimensions_with_five_anchors(
    rubrics: dict[str, Any],
) -> None:
    dimensions = rubrics["dimensions"]
    assert {item["id"] for item in dimensions} == set(DIMENSIONS)
    for dimension in dimensions:
        bands = dimension["bands"]
        assert [band["score"] for band in bands] == [0, 1, 2, 3, 4]
        assert all(band["criteria"].strip() for band in bands)
        assert dimension["pass_score"] == 3
        assert dimension["evidence_requirements"]
        assert set(dimension["evidence_requirements"]) <= REQUIRED_EVIDENCE_KINDS
        assert dimension["grading_notes"]


def test_sample_files_share_the_same_rubric(
    reliability: dict[str, Any],
    rubrics: dict[str, Any],
    counterexamples: dict[str, Any],
) -> None:
    assert reliability["rubric_ref"] == rubrics["rubric_id"]
    assert counterexamples["rubric_ref"] == rubrics["rubric_id"]


# ------------------------------------------------------------------ 反例判分


def _build_evidence(wrong: dict[str, Any], attempt_id: str) -> FakeAttemptEvidence:
    calls = [
        FakeToolCallEvidence(
            name=item["name"],
            arguments=item.get("arguments", {}),
            result=item.get("result"),
            is_error=bool(item.get("is_error", False)),
            seq=int(item.get("seq", index + 1)),
            turn=item.get("turn"),
            iteration=item.get("iteration"),
        )
        for index, item in enumerate(wrong.get("tool_calls", []))
    ]
    artifacts = [
        artifact_for(item["relative_path"], registered=bool(item.get("registered", True)))
        for item in wrong.get("artifacts", [])
    ]
    events = [
        TraceEvent(
            attempt_id=attempt_id,
            seq=int(item["seq"]),
            event_id=uuid.uuid4(),
            type=item["type"],
            ts="2026-10-26T00:00:00+00:00",
            elapsed_ms=int(item.get("elapsed_ms", 0)),
            payload=item.get("payload", {}),
        )
        for item in wrong.get("trace_events", [])
    ]
    evidence = FakeAttemptEvidence(
        attempt_id=attempt_id,
        final_answer=wrong.get("final_answer"),
        tool_calls=calls,
        artifacts=artifacts,
        files=wrong.get("files", {}),
        trace_events=events,
        trace_complete=bool(wrong.get("trace_complete", True)),
        execution_status=wrong.get("execution_status", "succeeded"),
    )
    if wrong.get("usage") is not None:
        evidence.usage = dict(wrong["usage"])
    if wrong.get("duration_ms") is not None:
        evidence.duration_ms = int(wrong["duration_ms"])
    return evidence


def _grade(
    case_payload: dict[str, Any], wrong: dict[str, Any]
) -> tuple[CaseWrite, list[Any], dict[str, Any], Any]:
    case = CaseWrite.model_validate(case_payload)
    evidence = _build_evidence(wrong, str(uuid.uuid4()))
    results = list(evaluate_rules(case, evidence))
    rules = [
        {
            "id": item.id,
            "passed": item.passed,
            "required": item.required,
            "message": item.message,
            "evidence_refs": list(item.evidence_refs),
        }
        for item in results
    ]
    coverage = compute_coverage(
        trace_events=evidence.trace_events,
        trace_sealed=bool(wrong.get("trace_sealed", True)),
        trace_complete=bool(wrong.get("trace_complete", True)),
        usage=wrong.get("usage"),
        declared_artifacts=case.artifact_paths,
        registered_artifacts=[
            {"relative_path": item["relative_path"]}
            for item in wrong.get("artifacts", [])
        ],
        required_evidence=(
            case.evaluation.required_evidence if case.evaluation is not None else []
        ),
        final_answer=evidence.final_answer,
    )
    assessment = assess(
        evaluation=(
            case.evaluation.model_dump(mode="json")
            if case.evaluation is not None
            else None
        ),
        assertions=list(case.assertions),
        rule_results=rules,
        coverage=coverage,
        judge_state="disabled",
        execution_status=evidence.execution_status,
    )
    return case, results, coverage, assessment


def _all_reasons(results: list[Any], assessment: Any) -> list[str]:
    reasons = [item.message for item in results if item.passed is not True]
    for dimension in assessment.dimensions.values():
        reasons.extend(dimension.reason_codes)
    return reasons


def test_every_known_false_positive_is_caught(
    counterexamples: dict[str, Any],
) -> None:
    items = counterexamples["counterexamples"]
    assert len(items) >= 10
    for item in items:
        _case, results, _coverage, assessment = _grade(
            item["case"], item["wrong_output"]
        )
        expected = item["expected"]
        assert assessment.qualification != QUALIFIED, item["counterexample_id"]
        assert assessment.qualification == expected["qualification"], (
            item["counterexample_id"],
            assessment.qualification,
            [r.message for r in results],
        )
        assert assessment.task_outcome == expected["task_outcome"], (
            item["counterexample_id"],
            assessment.task_outcome,
        )
        failed = {item.id for item in results if item.passed is False}
        assert failed == set(expected["failed_assertion_ids"]), item["counterexample_id"]
        reasons = _all_reasons(results, assessment)
        for code in expected["reason_codes"]:
            assert any(code in message for message in reasons), (
                item["counterexample_id"],
                code,
                reasons,
            )


def test_counterexample_expectations_never_allow_qualified(
    counterexamples: dict[str, Any],
) -> None:
    for item in counterexamples["counterexamples"]:
        expected = item["expected"]
        assert expected["qualification"] in (DISQUALIFIED, REVIEW_REQUIRED), (
            item["counterexample_id"]
        )
        assert expected["reason_codes"], item["counterexample_id"]
        assert expected["note"].strip(), item["counterexample_id"]
        assert item["scenario"].strip(), item["counterexample_id"]
        assert item["target"] in TARGETS


def test_counterexamples_are_not_a_single_uniform_outcome(
    counterexamples: dict[str, Any],
) -> None:
    """反例集不能只测一种失败：不合格与待复核都要覆盖，且失败点要分散。"""
    qualifications = Counter(
        item["expected"]["qualification"] for item in counterexamples["counterexamples"]
    )
    assert qualifications[DISQUALIFIED] >= 8
    assert qualifications[REVIEW_REQUIRED] >= 2
    failed_sets = {
        tuple(sorted(item["expected"]["failed_assertion_ids"]))
        for item in counterexamples["counterexamples"]
    }
    assert len(failed_sets) >= 8


def test_required_false_positives_are_all_covered(
    counterexamples: dict[str, Any],
) -> None:
    """计划里点名的假阳性必须逐条有反例，不能只测容易的那几个。"""
    scenarios = " ".join(
        item["scenario"] for item in counterexamples["counterexamples"]
    )
    for keyword in ("一句话", "产物", "关键词", "Trace", "usage", "成员"):
        assert keyword in scenarios, keyword


def test_grader_still_accepts_correct_outputs(
    counterexamples: dict[str, Any],
) -> None:
    """反向对照：判分器不是把一切都判失败，正确输出必须能合格。"""
    index = {item["counterexample_id"]: item for item in counterexamples["counterexamples"]}
    positives: dict[str, dict[str, Any]] = {
        "GE-02": {
            "final_answer": "已写入 report.md。",
            "tool_calls": [],
            "trace_events": [],
            "files": {"report.md": "数据齐全"},
            "artifacts": [{"relative_path": "report.md", "registered": True}],
            "usage": {"source": "reported", "coverage": "complete", "total_tokens": 80},
            "duration_ms": 300,
        },
        "GE-03": {
            "final_answer": '{"total": 3}',
            "tool_calls": [],
            "trace_events": [],
            "files": {},
            "artifacts": [],
            "usage": {"source": "reported", "coverage": "complete", "total_tokens": 60},
            "duration_ms": 200,
        },
        "GE-07": {
            "final_answer": "researcher 已完成。",
            "tool_calls": [],
            "trace_events": [
                {
                    "type": "agent.started",
                    "seq": 1,
                    "payload": {
                        "execution_id": "exec-pos",
                        "target_kind": "team",
                        "target_id": "frozen-team-v2",
                    },
                },
                {
                    "type": "member.completed",
                    "seq": 2,
                    "payload": {"member_id": "researcher", "status": "succeeded"},
                },
            ],
            "files": {},
            "artifacts": [],
            "usage": {"source": "reported", "coverage": "complete", "total_tokens": 100},
            "duration_ms": 500,
        },
    }
    for counterexample_id, correct in positives.items():
        _case, results, _coverage, assessment = _grade(
            index[counterexample_id]["case"], correct
        )
        assert assessment.qualification == QUALIFIED, (
            counterexample_id,
            [r.message for r in results],
        )


# ------------------------------------------------------------------ 交叉引用


def test_case_mutations_point_at_known_counterexamples(
    reliability: dict[str, Any],
    counterexamples: dict[str, Any],
) -> None:
    known = {item["counterexample_id"]: item for item in counterexamples["counterexamples"]}
    linked = 0
    for raw in reliability["cases"]:
        mutation = raw.get("mutation")
        if not mutation:
            continue
        linked += 1
        assert mutation["counterexample_id"] in known, raw["case_id"]
        assert mutation["expectation"] in (DISQUALIFIED, REVIEW_REQUIRED)
        assert known[mutation["counterexample_id"]]["expected"]["qualification"] != (
            QUALIFIED
        )
    assert linked >= 8, "已知假阳性的交叉引用太少，反例集与用例集没有连起来"
