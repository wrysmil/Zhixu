"""记忆专项逐 probe 评分、事实匹配与聚合（契约 §4 / §8.2 / §8.3 / §8.4）。

红线：

- 匹配 v1：版本化规范化 → 等价边 → **最大基数二分图匹配**（不是贪心）；
- 只用已校准的 ``equivalent`` 建边，``unknown`` 的必需关系 → ``not_assessable``；
- ``closed_world`` 才出全量 P/R/F1；``targeted`` 的额外合法事实不计 FP；
- ``qa_correct`` 是 0/1，部分正确另记事实覆盖分，不四舍五入成成功；
- ``grounded_memory_success`` 需要 probe 实际可用的合法记忆证据；
- 聚合 probe → trial → scene 等权；严格场景通过率与重复稳定性是另外两个指标；
- 未知不填 0：缺后端状态时只算行为，不生成伪状态分数。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from nanobot.evaluation.memory.grading import (
    FACT_MATCHING_VERSION,
    DeterministicFactResolver,
    EquivalenceDecision,
    MemoryProbeGrade,
    MemoryProbeOutcome,
    MemoryStateObservation,
    MemoryTrialExecution,
    MemoryTrialGrade,
    aggregate_memory_scores,
    default_grading_policy,
    grade_memory_trial,
    normalize_text,
)
from nanobot.evaluation.memory.judge_materials import (
    MEMORY_JUDGE_GUARD_PHRASES,
    build_ab_materials,
    build_memory_probe_materials,
)
from nanobot.evaluation.memory.models import (
    MemoryCoverage,
    MemoryFact,
    MemoryGold,
    MemoryProbeGold,
    MemoryRecallItem,
    MemoryScope,
    MemoryTiming,
    MemoryUsage,
    ProbeExecution,
)

SCOPE = MemoryScope(subject_id="synthetic-u1", workspace_id="synthetic-w1")
OTHER_SCOPE = MemoryScope(subject_id="synthetic-u1", workspace_id="synthetic-w2")
ASKED_AT = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- 辅助


def _probe(
    probe_id: str,
    *,
    answer: str | None = "好的",
    asserted: list[MemoryFact] | None = None,
    parsed: bool = True,
    recalled: list[MemoryRecallItem] | None = None,
    injected: list[MemoryFact] | None = None,
    after_forget: bool = False,
    scope: MemoryScope | None = SCOPE,
    question: str = "我住在哪个城市？",
    coverage_scored: int = 1,
    coverage_planned: int = 1,
) -> MemoryProbeOutcome:
    return MemoryProbeOutcome(
        execution=ProbeExecution(
            probe_id=probe_id,
            answer=answer,
            recalled=list(recalled or []),
            usage=MemoryUsage(),
            timing=MemoryTiming(),
            coverage=MemoryCoverage(scored=coverage_scored, planned=coverage_planned),
        ),
        question=question,
        asked_at=ASKED_AT,
        scope=scope,
        asserted_facts=list(asserted or []),
        answer_facts_parsed=parsed,
        injected_facts=list(injected or []),
        after_forget=after_forget,
    )


def _gold(
    probes: dict[str, dict[str, Any]],
    *,
    annotation_scope: str | None = None,
    extraction_facts: list[MemoryFact] | None = None,
    state_expectations: list[Any] | None = None,
) -> MemoryGold:
    facts = list(extraction_facts or [])
    # 契约 §8.2：closed_world 必须带抽取目标全集，否则模型直接拒绝。
    scope = annotation_scope or ("closed_world" if facts else "targeted")
    payload: dict[str, Any] = {
        "annotation_scope": scope,
        "extraction_facts": facts,
        "state_expectations": list(state_expectations or []),
        "probes": {
            probe_id: MemoryProbeGold.model_validate(body) for probe_id, body in probes.items()
        },
    }
    return MemoryGold.model_validate(payload)


def _metric(results: list[Any], metric_id: str) -> Any:
    for item in results:
        if item.id == metric_id:
            return item
    raise AssertionError(f"metric {metric_id} not found in {[item.id for item in results]}")


def _grade(
    execution: MemoryTrialExecution,
    gold: MemoryGold,
    **kwargs: Any,
) -> MemoryTrialGrade:
    return grade_memory_trial(
        execution, gold, default_grading_policy(), **kwargs
    )


# --------------------------------------------------------------------------- 规范化


def test_normalization_keeps_negation_and_comparison() -> None:
    """规范化不得去掉否定词、比较符与时间限定。"""
    assert normalize_text("不含糖") != normalize_text("含糖")
    assert normalize_text("大于 10") != normalize_text("10")
    assert normalize_text("2026 年 11 月 1 日之后") != normalize_text("2026 年 11 月 1 日之前")
    assert normalize_text("禁止使用") != normalize_text("使用")


def test_normalization_unifies_width_space_numbers_and_dates() -> None:
    assert normalize_text("Ａ　Ｂ") == "A B"
    assert normalize_text("  多   空格  ") == "多 空格"
    assert normalize_text("1,000") == "1000"
    assert normalize_text("3.0") == "3"
    assert normalize_text("2026年11月1日") == "2026-11-01"
    assert normalize_text("2026/11/1") == "2026-11-01"
    assert normalize_text("2026-11-01") == "2026-11-01"


# --------------------------------------------------------------------------- 抽取匹配


def test_closed_world_extraction_prf_hand_computed() -> None:
    """审查文档 §5.1 的手算例子：应记 10 条、输出 12 条、匹配 8 条。"""
    gold_facts = [MemoryFact(key="k", value=f"v{index}") for index in range(10)]
    predictions = [MemoryFact(key="k", value=f"v{index}") for index in range(8)]
    predictions += [MemoryFact(key="k", value=f"x{index}") for index in range(4)]
    gold = _gold({}, annotation_scope="closed_world", extraction_facts=gold_facts)
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="extraction",
        scope=SCOPE,
        extracted_facts=predictions,
        source_supported_facts=predictions[:8],
    )
    grade = _grade(execution, gold)
    assert _metric(grade.metrics, "extraction_precision").value == pytest.approx(8 / 12)
    assert _metric(grade.metrics, "extraction_recall").value == pytest.approx(8 / 10)
    assert _metric(grade.metrics, "extraction_f1").value == pytest.approx(16 / 22)


def test_targeted_scope_does_not_count_extra_facts_as_false_positives() -> None:
    """targeted：额外合法事实不计 FP，全量 precision/F1 不输出。"""
    gold = _gold(
        {},
        annotation_scope="targeted",
        extraction_facts=[MemoryFact(key="k", value="a"), MemoryFact(key="k", value="b")],
    )
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="extraction",
        scope=SCOPE,
        extracted_facts=[
            MemoryFact(key="k", value="a"),
            MemoryFact(key="k", value="b"),
            MemoryFact(key="k", value="c"),
        ],
        source_supported_facts=[
            MemoryFact(key="k", value="a"),
            MemoryFact(key="k", value="b"),
            MemoryFact(key="k", value="c"),
        ],
    )
    grade = _grade(execution, gold)
    assert _metric(grade.metrics, "extraction_recall").value == pytest.approx(1.0)
    assert _metric(grade.metrics, "extraction_precision").status == "not_assessable"
    assert _metric(grade.metrics, "extraction_f1").status == "not_assessable"


def test_duplicate_facts_are_redundant_not_extra_true_positives() -> None:
    """重复事实先去重：重复率单列，不增加 TP。"""
    gold = _gold(
        {},
        annotation_scope="closed_world",
        extraction_facts=[MemoryFact(key="k", value="a"), MemoryFact(key="k", value="b")],
    )
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="extraction",
        scope=SCOPE,
        extracted_facts=[
            MemoryFact(key="k", value="a"),
            MemoryFact(key="k", value="a"),
            MemoryFact(key="k", value="b"),
        ],
        source_supported_facts=[MemoryFact(key="k", value="a"), MemoryFact(key="k", value="b")],
    )
    grade = _grade(execution, gold)
    assert _metric(grade.metrics, "extraction_precision").value == pytest.approx(1.0)
    assert _metric(grade.metrics, "extraction_recall").value == pytest.approx(1.0)
    assert _metric(grade.metrics, "extraction_f1").value == pytest.approx(1.0)
    assert _metric(grade.metrics, "duplicate_memory_rate").value == pytest.approx(1 / 3)


def test_maximum_matching_beats_greedy_order() -> None:
    """审查文档 §5.1：P1 可对应 G1/G2、P2 只能对应 G1 → 必须匹配 2 条，不是 1 条。"""

    class _StubResolver:
        calibrated = True

        def resolve(self, gold, predicted, *, at=None):
            table = {
                ("A", "X"): "equivalent",
                ("B", "X"): "equivalent",
                ("A", "Y"): "equivalent",
            }
            verdict = table.get((gold.value, predicted.value), "unrelated")
            return EquivalenceDecision(verdict, "stub")

    gold = _gold(
        {},
        annotation_scope="closed_world",
        extraction_facts=[MemoryFact(key="k", value="A"), MemoryFact(key="k", value="B")],
    )
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="extraction",
        scope=SCOPE,
        extracted_facts=[MemoryFact(key="k", value="X"), MemoryFact(key="k", value="Y")],
        source_supported_facts=[MemoryFact(key="k", value="X"), MemoryFact(key="k", value="Y")],
    )
    grade = _grade(execution, gold, resolver=_StubResolver())
    assert _metric(grade.metrics, "extraction_recall").value == pytest.approx(1.0)
    assert _metric(grade.metrics, "extraction_precision").value == pytest.approx(1.0)


def test_unknown_equivalence_makes_the_metric_not_assessable() -> None:
    """unknown 的必需关系 → not_assessable，不能当不相等。"""

    class _UnknownResolver:
        calibrated = True

        def resolve(self, gold, predicted, *, at=None):
            return EquivalenceDecision("unknown", "cannot decide")

    gold = _gold(
        {}, annotation_scope="closed_world", extraction_facts=[MemoryFact(key="k", value="青岚城")]
    )
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="extraction",
        scope=SCOPE,
        extracted_facts=[MemoryFact(key="k", value="青岚")],
        source_supported_facts=[MemoryFact(key="k", value="青岚")],
    )
    grade = _grade(execution, gold, resolver=_UnknownResolver())
    assert _metric(grade.metrics, "extraction_recall").status == "not_assessable"
    assert _metric(grade.metrics, "extraction_recall").value is None
    assert "SEMANTIC_EQUIVALENCE_UNKNOWN" in _metric(
        grade.metrics, "extraction_recall"
    ).reasons


def test_uncalibrated_equivalent_does_not_create_an_edge() -> None:
    """未校准的 equivalent 不许建边：降级成 unknown → not_assessable。"""

    class _UncalibratedResolver:
        calibrated = False

        def resolve(self, gold, predicted, *, at=None):
            return EquivalenceDecision("equivalent", "not calibrated")

    gold = _gold(
        {}, annotation_scope="closed_world", extraction_facts=[MemoryFact(key="k", value="青岚城")]
    )
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="extraction",
        scope=SCOPE,
        extracted_facts=[MemoryFact(key="k", value="青岚城")],
        source_supported_facts=[MemoryFact(key="k", value="青岚城")],
    )
    grade = _grade(execution, gold, resolver=_UncalibratedResolver())
    assert _metric(grade.metrics, "extraction_recall").status == "not_assessable"
    assert _metric(grade.metrics, "extraction_recall").value is None


def test_deterministic_resolver_is_trusted_and_versioned() -> None:
    """确定性规则自身即版本化的可信规则：规范化后逐字相等才算等价。"""
    resolver = DeterministicFactResolver()
    assert resolver.calibrated is True
    decision = resolver.resolve(
        MemoryFact(key="k", value="青岚城"), MemoryFact(key="k", value=" 青岚城 ")
    )
    assert decision.verdict == "equivalent"
    other = resolver.resolve(
        MemoryFact(key="k", value="青岚城"), MemoryFact(key="k", value="青岚市")
    )
    assert other.verdict != "equivalent"


def test_scope_and_time_mismatch_is_not_equivalent() -> None:
    """作用域/时态不符不建立等价边。"""
    resolver = DeterministicFactResolver()
    scoped = resolver.resolve(
        MemoryFact(key="k", value="v", scope=SCOPE),
        MemoryFact(key="k", value="v", scope=OTHER_SCOPE),
    )
    assert scoped.verdict == "unrelated"
    timed = resolver.resolve(
        MemoryFact(key="k", value="v", valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        MemoryFact(key="k", value="v", valid_from=datetime(2026, 6, 1, tzinfo=timezone.utc)),
    )
    assert timed.verdict == "unrelated"


def test_unsupported_memory_rate_separates_hallucination_from_extra_legal_fact() -> None:
    """FP 分两种来源：源会话支持但不在目标集 ≠ 源会话无依据。"""
    gold = _gold(
        {},
        annotation_scope="closed_world",
        extraction_facts=[MemoryFact(key="k", value="a")],
    )
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="extraction",
        scope=SCOPE,
        extracted_facts=[
            MemoryFact(key="k", value="a"),
            MemoryFact(key="k", value="b"),
            MemoryFact(key="k", value="hallucinated"),
        ],
        source_supported_facts=[MemoryFact(key="k", value="a"), MemoryFact(key="k", value="b")],
    )
    grade = _grade(execution, gold)
    # 3 条新增事实里 1 条无源会话依据 → 1/3。
    assert _metric(grade.metrics, "unsupported_memory_rate").value == pytest.approx(1 / 3)


def test_unsupported_memory_rate_is_not_assessable_without_source_facts() -> None:
    """缺源会话材料 → 未知，不是 0。"""
    gold = _gold(
        {}, annotation_scope="closed_world", extraction_facts=[MemoryFact(key="k", value="a")]
    )
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="extraction",
        scope=SCOPE,
        extracted_facts=[MemoryFact(key="k", value="a")],
        source_supported_facts=None,
    )
    grade = _grade(execution, gold)
    metric = _metric(grade.metrics, "unsupported_memory_rate")
    assert metric.status == "not_assessable"
    assert metric.value is None


# --------------------------------------------------------------------------- 单题评分


def test_qa_correct_requires_every_required_fact() -> None:
    """部分正确记 0 + 事实覆盖分，不四舍五入成成功。"""
    gold = _gold(
        {
            "q1": {
                "required_facts": [
                    {"key": "city", "value": "青岚城"},
                    {"key": "drink", "value": "拿铁"},
                ]
            }
        }
    )
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[
            _probe("q1", asserted=[MemoryFact(key="city", value="青岚城")]),
        ],
    )
    grade = _grade(execution, gold)
    probe = grade.probe_results[0]
    assert probe.qa_correct is False
    assert probe.fact_coverage == pytest.approx(0.5)
    assert _metric(grade.metrics, "qa_accuracy").value == pytest.approx(0.0)


def test_qa_correct_is_one_when_all_required_facts_are_covered() -> None:
    gold = _gold({"q1": {"required_facts": [{"key": "city", "value": "青岚城"}]}})
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[_probe("q1", asserted=[MemoryFact(key="city", value="青岚城")])],
    )
    grade = _grade(execution, gold)
    assert grade.probe_results[0].qa_correct is True
    assert _metric(grade.metrics, "qa_accuracy").value == pytest.approx(1.0)


def test_annotation_key_does_not_gate_value_matching() -> None:
    """回归 2026-11-04：标注 key（pet_name）与产品 key（predicate）永远不同名，

    旧规则把 key 相等当准入门槛，导致所有正面场景在结构上恒为 0 分。
    """
    resolver = DeterministicFactResolver()
    equal = resolver.resolve(
        MemoryFact(key="pet_name", value="阿橙"),
        MemoryFact(key="养了一只橘猫", value="阿橙"),
    )
    assert equal.verdict == "equivalent"
    contained = resolver.resolve(
        MemoryFact(key="pet_name", value="阿橙"),
        MemoryFact(key="养了一只橘猫", value="用户养有一只名叫阿橙的橘猫"),
    )
    assert contained.verdict == "equivalent"
    assert contained.reason == "GOLD_VALUE_CONTAINED_IN_PREDICTED"
    truncated = resolver.resolve(
        MemoryFact(key="city", value="青岚城"), MemoryFact(key="city", value="青岚")
    )
    assert truncated.verdict != "equivalent"


def test_qa_and_evidence_recall_score_with_mismatched_keys() -> None:
    gold = _gold({"q1": {"required_facts": [{"key": "pet_name", "value": "阿橙"}]}})
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[
            _probe(
                "q1",
                answer="你养的猫叫阿橙。",
                asserted=[MemoryFact(key="养了一只橘猫", value="阿橙")],
                recalled=[
                    MemoryRecallItem(
                        rank=1, key="养了一只橘猫", value="用户养有一只名叫阿橙的橘猫",
                        origin_refs=["m1"],
                    )
                ],
            )
        ],
    )
    grade = _grade(execution, gold)
    assert grade.probe_results[0].qa_correct is True
    assert _metric(grade.metrics, "qa_accuracy").value == pytest.approx(1.0)
    assert _metric(grade.metrics, "evidence_recall_at_k").value == pytest.approx(1.0)


def test_numeric_values_stay_strict_across_keys() -> None:
    """数字/日期只认逐字相等；不同 key 之间不做矛盾判定。"""
    resolver = DeterministicFactResolver()
    assert (
        resolver.resolve(
            MemoryFact(key="age", value="25"), MemoryFact(key="age", value="125")
        ).verdict
        != "equivalent"
    )
    decision = resolver.resolve(
        MemoryFact(key="age", value="25"), MemoryFact(key="room", value="30")
    )
    assert decision.verdict == "unrelated"
    assert decision.reason == "CANONICAL_VALUE_DIFFERENT"


def test_forbidden_current_value_makes_qa_correct_zero() -> None:
    """被断言为当前的禁用值 → 0。"""
    gold = _gold(
        {
            "q1": {
                "required_facts": [{"key": "city", "value": "青岚城"}],
                "forbidden_current_values": [{"key": "drink", "value": "拿铁"}],
            }
        }
    )
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[
            _probe(
                "q1",
                asserted=[
                    MemoryFact(key="city", value="青岚城"),
                    MemoryFact(key="drink", value="拿铁"),
                ],
            )
        ],
    )
    grade = _grade(execution, gold)
    assert grade.probe_results[0].qa_correct is False
    assert "FORBIDDEN_VALUE_ASSERTED" in grade.probe_results[0].reasons


def test_unanswerable_probe_correct_abstention_scores_one() -> None:
    """无答案题：正确承认信息不足记 1。"""
    gold = _gold({"q1": {"answerability": "unanswerable", "expected_absence": True}})
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[_probe("q1", answer="我不知道。", asserted=[])],
    )
    grade = _grade(execution, gold)
    assert grade.probe_results[0].qa_correct is True
    assert _metric(grade.metrics, "abstention_accuracy").value == pytest.approx(1.0)


def test_unanswerable_probe_with_fabrication_scores_zero() -> None:
    """无答案题编造 → 0。"""
    gold = _gold({"q1": {"answerability": "unanswerable"}})
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[_probe("q1", asserted=[MemoryFact(key="birthday", value="1990-01-01")])],
    )
    grade = _grade(execution, gold)
    assert grade.probe_results[0].qa_correct is False
    assert _metric(grade.metrics, "abstention_accuracy").value == pytest.approx(0.0)


def test_answerable_probe_abstention_scores_zero_and_counts_false_abstention() -> None:
    """有答案却拒答 → 0，并计入误拒答率。"""
    gold = _gold({"q1": {"required_facts": [{"key": "city", "value": "青岚城"}]}})
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[_probe("q1", answer="我不知道。", asserted=[])],
    )
    grade = _grade(execution, gold)
    assert grade.probe_results[0].qa_correct is False
    assert _metric(grade.metrics, "false_abstention_rate").value == pytest.approx(1.0)


def test_qa_correct_is_not_assessable_when_answer_facts_are_not_parsed() -> None:
    """回答没有被解析成原子事实 → 未知，不是 0。"""
    gold = _gold({"q1": {"required_facts": [{"key": "city", "value": "青岚城"}]}})
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[_probe("q1", asserted=[], parsed=False)],
    )
    grade = _grade(execution, gold)
    assert grade.probe_results[0].qa_correct is None
    metric = _metric(grade.metrics, "qa_accuracy")
    assert metric.status == "not_assessable"
    assert metric.value is None


def test_grounded_memory_success_requires_available_evidence() -> None:
    """答对但无记忆证据 → 只证明答对，不能归因为记忆使用。"""
    gold = _gold({"q1": {"required_facts": [{"key": "city", "value": "青岚城"}]}})
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[_probe("q1", asserted=[MemoryFact(key="city", value="青岚城")])],
    )
    grade = _grade(execution, gold)
    assert grade.probe_results[0].qa_correct is True
    assert grade.probe_results[0].grounded_memory_success is False
    metric = _metric(grade.metrics, "grounded_memory_success")
    assert metric.status == "scored"
    assert metric.value == pytest.approx(0.0)


def test_grounded_memory_success_is_one_when_evidence_is_available() -> None:
    gold = _gold({"q1": {"required_facts": [{"key": "city", "value": "青岚城"}]}})
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[
            _probe(
                "q1",
                asserted=[MemoryFact(key="city", value="青岚城")],
                recalled=[
                    MemoryRecallItem(
                        rank=1, key="city", value="青岚城", scope=SCOPE
                    )
                ],
            )
        ],
    )
    grade = _grade(execution, gold)
    assert grade.probe_results[0].grounded_memory_success is True
    assert _metric(grade.metrics, "grounded_memory_success").value == pytest.approx(1.0)


def test_evidence_recall_and_mrr_use_returned_items() -> None:
    """Recall@k 按实际返回条数算；MRR 取首条相关结果排名倒数。"""
    gold = _gold(
        {
            "q1": {
                "required_facts": [
                    {"key": "city", "value": "青岚城"},
                    {"key": "drink", "value": "拿铁"},
                ]
            }
        }
    )
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="retrieval",
        scope=SCOPE,
        probes=[
            _probe(
                "q1",
                recalled=[
                    MemoryRecallItem(rank=1, key="other", value="无关", scope=SCOPE),
                    MemoryRecallItem(rank=2, key="city", value="青岚城", scope=SCOPE),
                ],
            )
        ],
    )
    grade = _grade(execution, gold)
    assert _metric(grade.metrics, "evidence_recall_at_k").value == pytest.approx(0.5)
    assert _metric(grade.metrics, "mrr").value == pytest.approx(0.5)
    # 返回 2 条、命中 1 条 → 实际返回精度 1/2。
    assert _metric(grade.metrics, "retrieval_precision_returned_at_k").value == pytest.approx(0.5)


def test_injection_recall_is_recomputed_from_injected_context() -> None:
    """注入覆盖按最终模型上下文重算，不沿用召回值。"""
    gold = _gold(
        {
            "q1": {
                "required_facts": [
                    {"key": "city", "value": "青岚城"},
                    {"key": "drink", "value": "拿铁"},
                ]
            }
        }
    )
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[
            _probe(
                "q1",
                recalled=[
                    MemoryRecallItem(rank=1, key="city", value="青岚城", scope=SCOPE),
                    MemoryRecallItem(rank=2, key="drink", value="拿铁", scope=SCOPE),
                ],
                injected=[MemoryFact(key="city", value="青岚城")],
            )
        ],
    )
    grade = _grade(execution, gold)
    assert _metric(grade.metrics, "evidence_recall_at_k").value == pytest.approx(1.0)
    assert _metric(grade.metrics, "injection_recall").value == pytest.approx(0.5)


def test_scope_leak_is_detected_and_reported_at_scenario_level() -> None:
    """召回阶段出现越界事实 → scope 泄漏。"""
    gold = _gold({"q1": {"required_facts": [{"key": "city", "value": "青岚城"}]}})
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="retrieval",
        scope=SCOPE,
        probes=[
            _probe(
                "q1",
                recalled=[
                    MemoryRecallItem(
                        rank=1, key="city", value="青岚城", scope=OTHER_SCOPE
                    )
                ],
            )
        ],
    )
    grade = _grade(execution, gold)
    metric = _metric(grade.metrics, "scope_leak_rate")
    assert metric.status == "scored"
    assert metric.value == pytest.approx(1.0)
    assert grade.probe_results[0].qa_correct is False


def test_stale_use_rate_counts_violating_probes_only() -> None:
    """过期事实误用率 = 违规题 / 可判当前状态题。"""
    gold = _gold(
        {
            "q1": {
                "required_facts": [{"key": "drink", "value": "无糖美式"}],
                "forbidden_current_values": [{"key": "drink", "value": "加糖拿铁"}],
            },
            "q2": {"required_facts": [{"key": "city", "value": "青岚城"}]},
        }
    )
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[
            _probe(
                "q1",
                asserted=[
                    MemoryFact(key="drink", value="无糖美式"),
                    MemoryFact(key="drink", value="加糖拿铁"),
                ],
            ),
            _probe("q2", asserted=[MemoryFact(key="city", value="青岚城")]),
        ],
    )
    grade = _grade(execution, gold)
    metric = _metric(grade.metrics, "stale_use_rate")
    assert metric.value == pytest.approx(1.0)
    assert metric.denominator == pytest.approx(1.0)


# --------------------------------------------------------------------------- 遗忘


def test_forget_behavior_success_requires_no_exposure_after_delete() -> None:
    """删除后仍有 probe 暴露目标 → 行为遗忘失败。"""
    gold = _gold(
        {
            "q1": {
                "required_facts": [{"key": "city", "value": "青岚城"}],
                "answerability": "unanswerable",
            }
        }
    )
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        forgotten_fact_keys=["city"],
        probes=[
            _probe("q1", after_forget=True, asserted=[MemoryFact(key="city", value="青岚城")])
        ],
    )
    grade = _grade(execution, gold)
    metric = _metric(grade.metrics, "forget_behavior_success")
    assert metric.status == "scored"
    assert metric.value == pytest.approx(0.0)


def test_forget_state_success_is_not_assessable_without_state_backend() -> None:
    """缺后端状态 → 只算行为，不生成伪状态分数。"""
    gold = _gold(
        {"q1": {"answerability": "unanswerable"}},
        state_expectations=[
            {"fact_key": "city", "present": False, "surfaces": ["store", "index"]}
        ],
    )
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        forgotten_fact_keys=["city"],
        probes=[_probe("q1", after_forget=True, asserted=[])],
        state_observations=None,
    )
    grade = _grade(execution, gold)
    metric = _metric(grade.metrics, "forget_state_success")
    assert metric.status == "not_assessable"
    assert metric.value is None
    # 行为分照样给。
    assert _metric(grade.metrics, "forget_behavior_success").status == "scored"


def test_forget_state_success_is_scored_with_state_observations() -> None:
    gold = _gold(
        {"q1": {"answerability": "unanswerable"}},
        state_expectations=[
            {"fact_key": "city", "present": False, "surfaces": ["store", "index"]}
        ],
    )
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        forgotten_fact_keys=["city"],
        probes=[_probe("q1", after_forget=True, asserted=[])],
        state_observations=[
            MemoryStateObservation(fact_key="city", surface="store", present=False),
            MemoryStateObservation(fact_key="city", surface="index", present=False),
        ],
    )
    grade = _grade(execution, gold)
    assert _metric(grade.metrics, "forget_state_success").value == pytest.approx(1.0)


# --------------------------------------------------------------------------- 分阶段 usage


def test_stage_usage_is_counted_separately_and_unknown_cost_stays_null() -> None:
    gold = _gold({"q1": {"required_facts": [{"key": "city", "value": "青岚城"}]}})
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[_probe("q1", asserted=[MemoryFact(key="city", value="青岚城")])],
        stage_usage={
            "extraction": MemoryUsage(total_tokens=100, physical_requests=1),
            "reader": MemoryUsage(total_tokens=200, physical_requests=1),
        },
        estimated_cost=None,
    )
    grade = _grade(execution, gold)
    assert _metric(grade.metrics, "extraction_tokens").value == pytest.approx(100.0)
    assert _metric(grade.metrics, "reader_tokens").value == pytest.approx(200.0)
    assert _metric(grade.metrics, "judge_tokens").status == "not_assessable"
    cost = _metric(grade.metrics, "estimated_cost")
    assert cost.value is None
    assert cost.status == "not_assessable"


# --------------------------------------------------------------------------- 聚合


def _trial(
    scenario_id: str,
    q_values: list[float | None],
    *,
    trial_index: int = 0,
    tags: tuple[str, ...] = ("fact",),
    strict: bool | None = None,
) -> MemoryTrialGrade:
    probes = [
        MemoryProbeGrade(
            probe_id=f"q{index}",
            category=tags[0],
            qa_correct=None if value is None else bool(value),
            qa_status="scored" if value is not None else "not_assessable",
            fact_coverage=value,
            grounded_memory_success=None if value is None else bool(value),
        )
        for index, value in enumerate(q_values, start=1)
    ]
    if strict is None:
        strict = all(value == 1 for value in q_values)
    return MemoryTrialGrade(
        scenario_id=scenario_id,
        track="end_to_end",
        grading_revision=1,
        categories=list(tags),
        probe_results=probes,
        strict_pass=strict,
        metrics=[],
        trial_index=trial_index,
    )


def test_scenario_macro_matches_hand_computed_values() -> None:
    """审查文档 §6：A 场景 2 题全对（1.0）、B 场景 4 题对 1 题（0.25）。"""
    trials = [
        _trial("scene-a", [1.0, 1.0]),
        _trial("scene-b", [1.0, 0.0, 0.0, 0.0]),
    ]
    results = aggregate_memory_scores(trials, policy=default_grading_policy())
    macro = _metric(results, "qa_accuracy_scenario_macro")
    micro = _metric(results, "qa_accuracy_probe_micro")
    assert macro.value == pytest.approx(0.625)  # (1.0 + 0.25) / 2
    assert micro.value == pytest.approx(0.5)  # 3 / 6
    assert macro.scope == "dataset"
    assert micro.scope == "dataset"


def test_repetitions_are_weighted_equally() -> None:
    """K 次重复先在场景内平均，再对场景等权。"""
    trials = [
        _trial("scene-a", [1.0, 1.0], trial_index=0),
        _trial("scene-a", [1.0, 0.0], trial_index=1),
        _trial("scene-b", [1.0, 0.0, 0.0, 0.0], trial_index=0),
        _trial("scene-b", [0.0, 0.0, 0.0, 0.0], trial_index=1),
    ]
    results = aggregate_memory_scores(trials, policy=default_grading_policy())
    macro = _metric(results, "qa_accuracy_scenario_macro")
    # scene-a = (1.0 + 0.5)/2 = 0.75；scene-b = (0.25 + 0.0)/2 = 0.125。
    assert macro.value == pytest.approx((0.75 + 0.125) / 2)


def test_strict_pass_rate_and_repeat_stability_are_separate_metrics() -> None:
    """严格场景通过率与重复稳定性不可互换。"""
    trials = [
        _trial("scene-a", [1.0, 1.0], trial_index=0, strict=True),
        _trial("scene-a", [1.0, 1.0], trial_index=1, strict=True),
        _trial("scene-b", [1.0], trial_index=0, strict=True),
        _trial("scene-b", [0.0], trial_index=1, strict=False),
    ]
    results = aggregate_memory_scores(trials, policy=default_grading_policy())
    strict = _metric(results, "strict_scenario_pass_rate")
    stability = _metric(results, "repeat_stability")
    assert strict.value == pytest.approx(3 / 4)
    assert stability.value == pytest.approx(1 / 2)


def test_unknown_probe_makes_the_scene_incomplete_but_keeps_other_scenes() -> None:
    """未知不填 0：不可判的场景被排除，覆盖度如实下降。"""
    trials = [
        _trial("scene-a", [1.0, 1.0]),
        _trial("scene-b", [1.0, None]),
    ]
    results = aggregate_memory_scores(trials, policy=default_grading_policy())
    macro = _metric(results, "qa_accuracy_scenario_macro")
    assert macro.value == pytest.approx(1.0)
    assert macro.coverage.scored == 1
    assert macro.coverage.planned == 2
    assert macro.coverage.complete is False
    assert "INCOMPLETE_COVERAGE" in macro.reasons


def test_macro_is_not_assessable_when_nothing_is_judgeable() -> None:
    trials = [_trial("scene-a", [None])]
    results = aggregate_memory_scores(trials, policy=default_grading_policy())
    macro = _metric(results, "qa_accuracy_scenario_macro")
    assert macro.status == "not_assessable"
    assert macro.value is None


# --------------------------------------------------------------------------- 复核版本


def test_regrade_produces_new_grading_revision_and_review_digest() -> None:
    """重算产生新的 grading_revision 与 review_digest，原分数保留不动。"""
    gold = _gold({"q1": {"required_facts": [{"key": "city", "value": "青岚城"}]}})
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[_probe("q1", asserted=[MemoryFact(key="city", value="青岚城")])],
    )
    first = _grade(execution, gold)
    assert first.grading_revision == 1
    assert first.review_digest
    second = _grade(execution, gold, previous=first)
    assert second.grading_revision == 2
    assert second.review_digest != first.review_digest
    # 原记录不被改写。
    assert first.grading_revision == 1
    assert first.review_digest != second.review_digest


def test_matching_version_mismatch_is_not_scored() -> None:
    """策略声明的匹配版本与实现不一致 → 不可评分，不冒充同口径。"""
    gold = _gold({"q1": {"required_facts": [{"key": "city", "value": "青岚城"}]}})
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[_probe("q1", asserted=[MemoryFact(key="city", value="青岚城")])],
    )
    policy = default_grading_policy().model_copy(
        update={"fact_matching_version": "fact-matching/v999"}
    )
    grade = grade_memory_trial(execution, gold, policy)
    metric = _metric(grade.metrics, "qa_accuracy")
    assert metric.status == "not_assessable"
    assert "FACT_MATCHING_VERSION_UNSUPPORTED" in metric.reasons


def test_fact_matching_version_is_the_declared_one() -> None:
    assert FACT_MATCHING_VERSION == "fact-matching/v1"


def test_calibrated_resolver_fingerprint_is_recorded_on_metrics() -> None:
    """语义判定器的指纹与校准状态要落在指标上（契约 §4）。"""

    class _CalibratedResolver:
        calibrated = True
        fingerprint = "fp-abc"
        calibration_status = "passed"

        def resolve(self, gold, predicted, *, at=None):
            return EquivalenceDecision("equivalent", "calibrated")

    gold = _gold({"q1": {"required_facts": [{"key": "city", "value": "青岚城"}]}})
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[_probe("q1", asserted=[MemoryFact(key="city", value="青岚城")])],
    )
    grade = _grade(execution, gold, resolver=_CalibratedResolver())
    metric = _metric(grade.metrics, "qa_accuracy")
    assert metric.grader_fingerprint == "fp-abc"
    assert metric.calibration_status == "passed"


def test_review_digest_covers_existing_reviews() -> None:
    """重算的摘要要覆盖被引用的既有复核记录，且同输入同摘要。"""
    gold = _gold({"q1": {"required_facts": [{"key": "city", "value": "青岚城"}]}})
    execution = MemoryTrialExecution(
        scenario_id="s-1",
        track="end_to_end",
        scope=SCOPE,
        probes=[_probe("q1", asserted=[MemoryFact(key="city", value="青岚城")])],
    )
    first = _grade(execution, gold)
    reviews = [
        {
            "id": "r1",
            "verdict": "failed",
            "memory_ref": {
                "scenario_id": "s-1",
                "probe_id": "q1",
                "metric_id": "qa_accuracy",
                "grading_revision": 1,
            },
        }
    ]
    second = _grade(execution, gold, previous=first, reviews=reviews)
    assert second.grading_revision == 2
    assert second.review_digest != first.review_digest
    assert second.previous_review_digest == first.review_digest
    # 同输入同摘要：重算可复现。
    again = _grade(execution, gold, previous=first, reviews=reviews)
    assert again.review_digest == second.review_digest


# --------------------------------------------------------------------------- Judge 材料


def test_judge_materials_expose_question_scope_reference_and_available_evidence() -> None:
    gold = _gold({"q1": {"required_facts": [{"key": "city", "value": "青岚城"}]}})
    outcome = _probe(
        "q1",
        question="我住在哪个城市？",
        recalled=[MemoryRecallItem(rank=1, key="city", value="青岚城", scope=SCOPE)],
    )
    materials = build_memory_probe_materials(
        outcome=outcome, probe_gold=gold.probes["q1"], semantic_calibrated=False
    )
    body = materials.render()
    assert "我住在哪个城市？" in body
    assert "青岚城" in body
    assert "synthetic-w1" in body
    assert materials.not_assessable is False
    for phrase in MEMORY_JUDGE_GUARD_PHRASES:
        assert phrase in body


def test_judge_materials_mark_truncation_as_not_assessable() -> None:
    gold = _gold({"q1": {"required_facts": [{"key": "city", "value": "青岚城"}]}})
    outcome = _probe("q1", recalled=[MemoryRecallItem(rank=1, key="city", value="青岚城")])
    materials = build_memory_probe_materials(
        outcome=outcome, probe_gold=gold.probes["q1"], semantic_calibrated=False, budget_bytes=120
    )
    assert materials.not_assessable is True
    assert materials.truncations
    assert any(item.startswith("MATERIAL_TRUNCATED") for item in materials.missing)


def test_judge_materials_need_calibration_for_semantic_equivalence() -> None:
    gold = _gold({"q1": {"required_facts": [{"key": "city", "value": "青岚城"}]}})
    outcome = _probe("q1")
    materials = build_memory_probe_materials(
        outcome=outcome,
        probe_gold=gold.probes["q1"],
        semantic_calibrated=False,
        requires_semantic_equivalence=True,
    )
    assert materials.not_assessable is True
    assert any("SEMANTIC_EQUIVALENCE_NOT_CALIBRATED" in item for item in materials.missing)


def test_ab_materials_are_anonymous_and_order_is_fixed() -> None:
    gold = _gold({"q1": {"required_facts": [{"key": "city", "value": "青岚城"}]}})
    outcome = _probe("q1")
    left = build_memory_probe_materials(
        outcome=outcome, probe_gold=gold.probes["q1"], semantic_calibrated=False
    )
    right = build_memory_probe_materials(
        outcome=outcome, probe_gold=gold.probes["q1"], semantic_calibrated=False
    )
    first = build_ab_materials(
        scenario_id="s-1", probe_id="q1", candidates={"cand-a": left, "cand-b": right}
    )
    second = build_ab_materials(
        scenario_id="s-1", probe_id="q1", candidates={"cand-a": left, "cand-b": right}
    )
    assert first.order == second.order
    body = first.render()
    assert "cand-a" not in body
    assert "cand-b" not in body
    assert "候选 A" in body and "候选 B" in body
