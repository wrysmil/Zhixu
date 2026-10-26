"""T06 五维度判分与新声明式规则。

红灯用例来自真实翻车场景：

- 团队回复 322 字却要求"只回一句话"：关键词齐也不能通过；
- 声称写了文件但没有真实产物：不能算完成；
- 缺 Trace / 缺 usage：不可评分（待复核），不是低分，也不是通过；
- 里程碑部分通过：任务结果是 partial，不能说成完成，也不能说成彻底失败。
"""

from __future__ import annotations

import uuid
from typing import Any

from nanobot.evaluation.assessment import (
    COMPLETED,
    DISQUALIFIED,
    PARTIAL,
    REVIEW_REQUIRED,
    UNKNOWN,
    assess,
)
from nanobot.evaluation.graders.rules import evaluate_rules
from nanobot.evaluation.schemas import CaseWrite, TraceEvent

from .test_rules import FakeAttemptEvidence, FakeToolCallEvidence


def _case(**overrides: Any) -> CaseWrite:
    payload: dict[str, Any] = {
        "name": "一句话结论",
        "suite_id": str(uuid.uuid4()),
        "turns": [{"content": "只回一句话"}],
        "fixtures": [],
        "memory_seed": [],
        "clock_iso": None,
        "tool_fixtures": [],
        "execution": {
            "tool_mode": "fixture",
            "allowed_tools": ["read_file"],
            "max_iterations": 4,
            "timeout_s": 60,
        },
        "assertions": [
            {
                "id": "a1",
                "kind": "answer_contains",
                "required": True,
                "text": "金黄",
                "case_sensitive": True,
            }
        ],
        "rubric": None,
        "artifact_paths": [],
    }
    payload.update(overrides)
    return CaseWrite.model_validate(payload)


def _rules(case: CaseWrite, evidence: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "passed": item.passed,
            "required": item.required,
            "message": item.message,
            "evidence_refs": list(item.evidence_refs),
        }
        for item in evaluate_rules(case, evidence)
    ]


def test_keyword_cannot_override_length_failure() -> None:
    """关键词齐但超长：硬约束失败优先，综合判定不合格。"""
    case = _case(
        assertions=[
            {
                "id": "a1",
                "kind": "answer_contains",
                "required": True,
                "text": "金黄",
                "case_sensitive": True,
            },
            {
                "id": "a2",
                "kind": "answer_length",
                "required": True,
                "max_chars": 40,
            },
        ],
        evaluation={
            "version": 2,
            "required_dimensions": ["task", "constraints"],
            "milestones": [{"id": "m1", "weight": 1.0, "assertion_ids": ["a1"]}],
        },
    )
    evidence = FakeAttemptEvidence(final_answer="金黄" + "说明" * 160)
    rules = _rules(case, evidence)

    result = assess(
        evaluation=case.evaluation.model_dump(mode="json"),
        assertions=list(case.assertions),
        rule_results=rules,
        coverage={"trace": "complete", "identity": "complete", "usage": "complete"},
        judge_state="disabled",
        execution_status="succeeded",
    )
    assert result.dimensions["constraints"].status == "failed"
    assert result.qualification == DISQUALIFIED
    assert result.verdict == "failed"


def test_missing_trace_makes_trajectory_unassessable() -> None:
    """缺 Trace 的维度不可评：待复核，不是"失败"。"""
    case = _case(
        assertions=[
            {
                "id": "t1",
                "kind": "tool_trajectory",
                "required": True,
                "steps": [{"name": "read_file", "arguments_subset": {"path": "a.txt"}}],
                "mode": "in_order",
            }
        ],
        evaluation={
            "version": 2,
            "required_dimensions": ["trajectory"],
        },
    )
    evidence = FakeAttemptEvidence(
        tool_calls=[
            FakeToolCallEvidence(
                name="read_file",
                arguments={"path": "a.txt"},
                result="ok",
                is_error=False,
                seq=1,
                turn=1,
                iteration=1,
            )
        ]
    )
    rules = _rules(case, evidence)
    result = assess(
        evaluation=case.evaluation.model_dump(mode="json"),
        assertions=list(case.assertions),
        rule_results=rules,
        coverage={"trace": "missing", "identity": "complete", "usage": "complete"},
        judge_state="disabled",
        execution_status="succeeded",
    )
    assert result.dimensions["trajectory"].status == "not_assessable"
    assert result.qualification == REVIEW_REQUIRED
    assert result.verdict == "review_required"


def test_unknown_milestone_is_unknown_not_zero() -> None:
    """断言读不到证据时，任务结果是 unknown，不是 0 分也不是 partial。"""
    case = _case(
        assertions=[
            {
                "id": "a1",
                "kind": "answer_contains",
                "required": True,
                "text": "金黄",
                "case_sensitive": True,
            }
        ],
        evaluation={
            "version": 2,
            "required_dimensions": ["task"],
            "milestones": [{"id": "m1", "weight": 1.0, "assertion_ids": ["a1"]}],
        },
    )
    # 回答读不到：结论未知，不是"没做到"。
    evidence = FakeAttemptEvidence(final_answer=None)
    rules = _rules(case, evidence)
    result = assess(
        evaluation=case.evaluation.model_dump(mode="json"),
        assertions=list(case.assertions),
        rule_results=rules,
        coverage={"trace": "complete", "identity": "complete", "usage": "complete"},
        judge_state="disabled",
        execution_status="succeeded",
    )
    assert result.task_outcome == UNKNOWN
    assert result.qualification == REVIEW_REQUIRED


def test_partial_milestones_report_partial_outcome() -> None:
    """里程碑只完成一半：任务是 partial，综合判定不合格。"""
    case = _case(
        assertions=[
            {
                "id": "a1",
                "kind": "answer_contains",
                "required": True,
                "text": "金黄",
                "case_sensitive": True,
            },
            {
                "id": "f1",
                "kind": "file_exists",
                "required": True,
                "path": "output.txt",
            },
        ],
        evaluation={
            "version": 2,
            "required_dimensions": ["task"],
            "milestones": [
                {"id": "m1", "weight": 1.0, "assertion_ids": ["a1"]},
                {"id": "m2", "weight": 1.0, "assertion_ids": ["f1"]},
            ],
        },
    )
    evidence = FakeAttemptEvidence(final_answer="金黄", files={})
    rules = _rules(case, evidence)
    result = assess(
        evaluation=case.evaluation.model_dump(mode="json"),
        assertions=list(case.assertions),
        rule_results=rules,
        coverage={"trace": "complete", "identity": "complete", "usage": "complete"},
        judge_state="disabled",
        execution_status="succeeded",
    )
    assert result.task_outcome == PARTIAL
    assert result.qualification == DISQUALIFIED


def test_completed_task_with_usage_budget_breach_is_disqualified() -> None:
    """任务完成但效率超预算：completed + disqualified，不互相抵消。"""
    case = _case(
        assertions=[
            {
                "id": "a1",
                "kind": "answer_contains",
                "required": True,
                "text": "金黄",
                "case_sensitive": True,
            },
            {
                "id": "u1",
                "kind": "usage_limit",
                "required": True,
                "max_total_tokens": 100,
            },
        ],
        evaluation={
            "version": 2,
            "required_dimensions": ["task", "efficiency"],
            "milestones": [{"id": "m1", "weight": 1.0, "assertion_ids": ["a1"]}],
        },
    )
    evidence = FakeAttemptEvidence(final_answer="金黄")
    evidence.usage = {"total_tokens": 999, "output_tokens": 10}
    rules = _rules(case, evidence)
    result = assess(
        evaluation=case.evaluation.model_dump(mode="json"),
        assertions=list(case.assertions),
        rule_results=rules,
        coverage={"trace": "complete", "identity": "complete", "usage": "complete"},
        judge_state="disabled",
        execution_status="succeeded",
    )
    assert result.task_outcome == COMPLETED
    assert result.dimensions["efficiency"].status == "failed"
    assert result.qualification == DISQUALIFIED


def test_not_executed_stays_not_evaluated() -> None:
    """基础设施失败不是 Agent 能力失败：结果 not_evaluated。"""
    result = assess(
        evaluation={"version": 2, "required_dimensions": ["task"]},
        assertions=[],
        rule_results=[],
        coverage={"trace": "missing", "identity": "missing", "usage": "missing"},
        judge_state="disabled",
        execution_status="failed",
    )
    assert result.qualification == "not_evaluated"
    assert result.task_outcome == UNKNOWN


def test_tool_trajectory_requires_one_call_per_step() -> None:
    """一次调用不能满足两步：轨迹必须按 call_id 一对一。"""
    case = _case(
        assertions=[
            {
                "id": "t1",
                "kind": "tool_trajectory",
                "required": True,
                "steps": [
                    {"name": "read_file", "arguments_subset": {"path": "a.txt"}},
                    {"name": "read_file", "arguments_subset": {"path": "b.txt"}},
                ],
                "mode": "in_order",
            }
        ]
    )
    evidence = FakeAttemptEvidence(
        tool_calls=[
            FakeToolCallEvidence(
                name="read_file",
                arguments={"path": "a.txt"},
                result="ok",
                is_error=False,
                seq=1,
                turn=1,
                iteration=1,
            )
        ]
    )
    results = evaluate_rules(case, evidence)
    assert results[0].passed is False


def test_answer_exact_rejects_extra_text() -> None:
    case = _case(
        assertions=[
            {
                "id": "a1",
                "kind": "answer_exact",
                "required": True,
                "text": "金黄",
                "case_sensitive": True,
            }
        ]
    )
    results = evaluate_rules(case, FakeAttemptEvidence(final_answer="金黄的天色"))
    assert results[0].passed is False
    ok = evaluate_rules(case, FakeAttemptEvidence(final_answer="  金黄  "))
    assert ok[0].passed is True


def test_team_member_executed_requires_real_member_events() -> None:
    """只有自报成员名不算委派：必须读到 member.completed 事件。"""
    case = _case(
        assertions=[
            {
                "id": "m1",
                "kind": "team_member_executed",
                "required": True,
                "member_ids": ["writer"],
            }
        ]
    )
    claimed = FakeAttemptEvidence(
        final_answer="已让 writer 完成",
        trace_events=[
            TraceEvent(
                attempt_id=uuid.UUID(str(uuid.uuid4())),
                seq=1,
                event_id=uuid.uuid4(),
                type="agent.completed",
                ts="2026-10-26T00:00:00+00:00",
                elapsed_ms=0,
                payload={},
            )
        ],
    )
    assert evaluate_rules(case, claimed)[0].passed is False
