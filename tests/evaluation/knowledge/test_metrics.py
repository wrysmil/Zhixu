"""知识检索指标口径：手算期望值；缺证据是 not_assessable，绝不补 0。

锁死的几条硬约束（改口径 = 改契约，必须先改契约）：

1. ``recall_at_k`` 的分母是**候选池中** grade≥2 的块数，不是全库相关块数；
2. 池里没有相关块 → ``recall_at_k`` 为 ``None`` 且该题 ``not_assessable``，
   其它指标（hit/precision/mrr/ndcg）仍照常给值；
3. 聚合按 ``label_source`` 分组，不跨组混算；
4. 缺证据是 ``None``，绝不补 0。
"""

from __future__ import annotations

import math

import pytest

from nanobot.evaluation.knowledge.metrics import (
    METRIC_IDS,
    METRIC_SPECS,
    UNSCORED_RATE,
    KnowledgeCaseScore,
    aggregate_metrics,
    case_score,
    pool_coverage,
)


def _hits(grades: list[int]) -> list[tuple[str, int]]:
    return [(f"chk_{i}", grade) for i, grade in enumerate(grades)]


# --------------------------------------------------------------------------- 逐题


def test_case_score_hit_and_precision() -> None:
    """手算：top-k 3 块里 2 块相关（grade 3 与 2），首位即相关。"""
    score = case_score(
        case_id="c1",
        label_source="judge",
        top_k=[("chk_0", 3), ("chk_1", 0), ("chk_2", 2)],
        pool_grades={"chk_0": 3, "chk_1": 0, "chk_2": 2},
        k=3,
    )
    assert score.metrics["hit_at_k"] == 1.0
    assert score.metrics["precision_at_k"] == 2 / 3
    assert score.metrics["mrr"] == 1.0
    assert score.metrics["recall_at_k"] == 1.0
    assert score.metrics["pool_coverage"] == 2 / 3
    assert score.status == "scored"
    assert score.reason == ""


def test_case_score_mrr_uses_first_relevant_rank() -> None:
    """MRR 取**首个**相关块的倒数名次，不是多个相关块的平均。"""
    score = case_score(
        case_id="c1",
        label_source="judge",
        top_k=[("chk_0", 0), ("chk_1", 2)],
        pool_grades={"chk_0": 0, "chk_1": 2},
        k=2,
    )
    assert score.metrics["mrr"] == 0.5
    assert score.metrics["hit_at_k"] == 1.0
    assert score.metrics["precision_at_k"] == 1 / 2


def test_case_score_mrr_is_first_relevant_not_average() -> None:
    """两个相关块在第 1、3 名 → MRR = 1/1，而不是 (1/1 + 1/3)/2。"""
    score = case_score(
        case_id="c1",
        label_source="judge",
        top_k=[("chk_0", 2), ("chk_1", 0), ("chk_2", 3)],
        pool_grades={"chk_0": 2, "chk_1": 0, "chk_2": 3},
        k=3,
    )
    assert score.metrics["mrr"] == 1.0
    assert score.metrics["precision_at_k"] == 2 / 3


def test_case_score_is_not_assessable_when_pool_has_no_relevant() -> None:
    """池里没有相关块 → recall 无意义，但 hit/precision 照常给值。"""
    score = case_score(
        case_id="c1",
        label_source="judge",
        top_k=[("chk_0", 0)],
        pool_grades={"chk_0": 0},
        k=1,
    )
    assert score.status == "not_assessable"
    assert score.metrics["recall_at_k"] is None
    assert score.metrics["hit_at_k"] == 0.0
    assert score.metrics["precision_at_k"] == 0.0
    assert score.reason == "POOL_HAS_NO_RELEVANT"


def test_case_score_keeps_hit_precision_when_pool_has_no_relevant() -> None:
    """池里没有相关块时，top-k 的相关性判定仍有效（其它指标照常给值）。"""
    score = case_score(
        case_id="c1",
        label_source="judge",
        top_k=[("chk_0", 3), ("chk_1", 0)],
        pool_grades={"chk_0": 0, "chk_1": 0},
        k=2,
    )
    assert score.status == "not_assessable"
    assert score.metrics["recall_at_k"] is None
    assert score.metrics["hit_at_k"] == 1.0
    assert score.metrics["precision_at_k"] == 1 / 2
    assert score.metrics["mrr"] == 1.0


def test_recall_denominator_is_pool_relative() -> None:
    """recall 分母是候选池中 grade≥2 的块数：池里 2 块相关，只捞到 1 块 → 0.5。"""
    score = case_score(
        case_id="c1",
        label_source="judge",
        top_k=[("chk_0", 3)],
        pool_grades={"chk_0": 3, "chk_1": 2, "chk_2": 0},
        k=1,
    )
    assert score.metrics["recall_at_k"] == 1 / 2
    assert score.metrics["precision_at_k"] == 1.0
    assert score.metrics["pool_coverage"] == 2 / 3


def test_ndcg_uses_exponential_gain() -> None:
    """DCG = Σ (2^grade − 1) ÷ log₂(名次+1)：grade 3 → gain 7。"""
    score = case_score(
        case_id="c1",
        label_source="judge",
        top_k=[("chk_0", 3), ("chk_1", 0)],
        pool_grades={"chk_0": 3, "chk_1": 0},
        k=2,
    )
    # DCG = 7/log2(2) + 0/log2(3) = 7；IDCG（池内降序）= 7 → 1.0
    assert score.metrics["ndcg_at_k"] == 1.0


def test_ndcg_penalises_bad_ranking() -> None:
    """把 grade 3 放到第 2 位 → DCG = 7/log2(3)，IDCG = 7。"""
    score = case_score(
        case_id="c1",
        label_source="judge",
        top_k=[("chk_0", 0), ("chk_1", 3)],
        pool_grades={"chk_0": 0, "chk_1": 3},
        k=2,
    )
    expected = (7 / math.log2(3)) / 7
    assert score.metrics["ndcg_at_k"] == pytest.approx(expected, rel=1e-12)
    assert score.metrics["ndcg_at_k"] < 1.0


def test_ndcg_is_zero_when_idcg_is_zero() -> None:
    """IDCG = 0（池里全是 grade 0）→ ndcg = 0.0，不补 None。"""
    score = case_score(
        case_id="c1",
        label_source="judge",
        top_k=[("chk_0", 0)],
        pool_grades={"chk_0": 0},
        k=1,
    )
    assert score.metrics["ndcg_at_k"] == 0.0


def test_pool_coverage_is_relevant_over_pool_size() -> None:
    """手算：池 4 块候选里 2 块 grade≥2 → 0.5。"""
    assert pool_coverage(pool_grades={"a": 3, "b": 0, "c": 2}, pool_size=4) == 0.5
    assert pool_coverage(pool_grades={"a": 3, "b": 0, "c": 2}, pool_size=3) == pytest.approx(
        2 / 3
    )


def test_pool_coverage_is_none_for_empty_pool() -> None:
    """空池 → None（缺证据不是 0）。"""
    assert pool_coverage(pool_grades={}, pool_size=0) is None


def test_case_score_truncates_ranking_to_k() -> None:
    """top-k 只取前 k 个：precision 不会超过 1，recall 分子也只看前 k。"""
    score = case_score(
        case_id="c1",
        label_source="judge",
        top_k=[("chk_0", 3), ("chk_1", 2), ("chk_2", 3)],
        pool_grades={"chk_0": 3, "chk_1": 2, "chk_2": 3},
        k=2,
    )
    assert score.metrics["precision_at_k"] == 1.0
    assert score.metrics["recall_at_k"] == 2 / 3
    assert score.metrics["mrr"] == 1.0


def test_case_score_rejects_non_positive_k() -> None:
    with pytest.raises(ValueError):
        case_score(
            case_id="c1",
            label_source="judge",
            top_k=[],
            pool_grades={},
            k=0,
        )


# --------------------------------------------------------------------------- 聚合


def _expect_scores() -> list[KnowledgeCaseScore]:
    return [
        # 可评：1/1 命中，池 1/1 相关
        case_score(
            case_id="e1",
            label_source="expect",
            top_k=[("a", 3)],
            pool_grades={"a": 3},
            k=1,
        ),
        # 可评：0 命中，池 1/2 相关
        case_score(
            case_id="e2",
            label_source="expect",
            top_k=[("b", 0)],
            pool_grades={"b": 0, "c": 2},
            k=1,
        ),
        # 不可评：池里没有相关块
        case_score(
            case_id="e3",
            label_source="expect",
            top_k=[("d", 0)],
            pool_grades={"d": 0},
            k=1,
        ),
    ]


def test_aggregate_separates_label_sources() -> None:
    """不同 label_source 不混算，各组独立统计。"""
    scores = [
        *(_expect_scores()),
        case_score(
            case_id="j1",
            label_source="judge",
            top_k=[("z", 0)],
            pool_grades={"z": 0},
            k=1,
        ),
    ]
    groups = aggregate_metrics(scores)
    assert set(groups) == {"expect", "judge"}

    expect = groups["expect"]
    assert expect["total_queries"] == 3
    assert expect["scored_queries"] == 2
    assert expect["unscored_rate"] == pytest.approx(1 / 3)

    judge = groups["judge"]
    assert judge["total_queries"] == 1
    assert judge["scored_queries"] == 0
    assert judge["unscored_rate"] == 1.0
    # 没有可评查询 → 其它指标是 None，不是 0
    assert judge["metrics"]["hit_at_k"] is None
    assert judge["metrics"]["precision_at_k"] is None
    assert judge["metrics"]["mrr"] is None
    assert judge["metrics"]["ndcg_at_k"] is None
    assert judge["metrics"]["recall_at_k"] is None
    assert judge["metrics"]["pool_coverage"] is None
    assert judge["metrics"]["unscored_rate"] == 1.0


def test_aggregate_macro_metrics_average_per_case_values() -> None:
    """macro：precision/recall/mrr/ndcg 逐题值再平均。"""
    expect = aggregate_metrics(_expect_scores())["expect"]
    metrics = expect["metrics"]
    # 逐题 precision：1.0 与 0.0 → 0.5
    assert metrics["precision_at_k"] == pytest.approx(0.5)
    # 逐题 recall：1.0 与 0.0 → 0.5
    assert metrics["recall_at_k"] == pytest.approx(0.5)
    # 逐题 mrr：1.0 与 0.0 → 0.5
    assert metrics["mrr"] == pytest.approx(0.5)
    # 逐题 ndcg：1.0 与 0.0 → 0.5
    assert metrics["ndcg_at_k"] == pytest.approx(0.5)


def test_aggregate_micro_metrics_are_ratios() -> None:
    """micro：hit_at_k / pool_coverage 是整体比值，不是逐题值的平均。"""
    expect = aggregate_metrics(_expect_scores())["expect"]
    metrics = expect["metrics"]
    # hit：命中查询数 1 ÷ 可评查询数 2
    assert metrics["hit_at_k"] == pytest.approx(0.5)
    # pool_coverage：池中相关块 1+1=2 ÷ 池候选 1+2=3（macro 会算成 0.75）
    assert metrics["pool_coverage"] == pytest.approx(2 / 3)
    assert metrics["pool_coverage"] != pytest.approx(0.75)
    # unscored_rate：not_assessable 1 ÷ 总查询 3
    assert metrics["unscored_rate"] == pytest.approx(1 / 3)


def test_aggregate_ignores_not_assessable_cases_in_metric_averages() -> None:
    """不可评题不参与其它指标的平均（只进 unscored_rate）。"""
    scored_only = aggregate_metrics(_expect_scores()[:2])["expect"]
    with_na = aggregate_metrics(_expect_scores())["expect"]
    assert scored_only["metrics"]["precision_at_k"] == pytest.approx(
        with_na["metrics"]["precision_at_k"]
    )
    assert scored_only["metrics"]["hit_at_k"] == pytest.approx(
        with_na["metrics"]["hit_at_k"]
    )
    assert scored_only["unscored_rate"] == 0.0
    assert with_na["unscored_rate"] == pytest.approx(1 / 3)


def test_aggregate_reports_all_metric_ids() -> None:
    groups = aggregate_metrics(_expect_scores())
    assert set(groups["expect"]["metrics"]) == set(METRIC_IDS)


def test_aggregate_marks_uncomputable_metrics_not_assessable() -> None:
    """不可算的指标：value=null + status=not_assessable，不补 0、不省略。"""
    groups = aggregate_metrics(
        [
            case_score(
                case_id="j1",
                label_source="judge",
                top_k=[("z", 0)],
                pool_grades={"z": 0},
                k=1,
            )
        ]
    )
    judge = groups["judge"]
    assert set(judge["metric_status"]) == set(METRIC_IDS)
    for metric_id, value in judge["metrics"].items():
        if metric_id == UNSCORED_RATE:
            assert judge["metric_status"][metric_id] == "scored"
            continue
        assert value is None, metric_id
        assert judge["metric_status"][metric_id] == "not_assessable", metric_id


def test_aggregate_marks_computed_metrics_scored() -> None:
    groups = aggregate_metrics(_expect_scores())
    status = groups["expect"]["metric_status"]
    for metric_id in ("precision_at_k", "recall_at_k", "mrr", "ndcg_at_k"):
        assert status[metric_id] == "scored", metric_id
        assert groups["expect"]["metrics"][metric_id] is not None, metric_id


def test_case_score_accepts_explicit_pool_size() -> None:
    """池里有未打标候选时，pool_size 可显式传入（分母是池候选总数）。"""
    score = case_score(
        case_id="c1",
        label_source="judge",
        top_k=[("a", 3)],
        pool_grades={"a": 3},
        k=1,
        pool_size=4,
    )
    assert score.metrics["pool_coverage"] == 0.25
    assert score.pool_size == 4
    assert score.pool_relevant == 1


def test_aggregate_on_empty_input() -> None:
    assert aggregate_metrics([]) == {}


# --------------------------------------------------------------------------- 口径注册表


def test_metric_ids_are_exactly_the_contract_set() -> None:
    assert set(METRIC_IDS) == {
        "hit_at_k",
        "precision_at_k",
        "mrr",
        "ndcg_at_k",
        "recall_at_k",
        "pool_coverage",
        "unscored_rate",
    }


def test_metric_specs_cover_every_metric_id_with_formula() -> None:
    """每个指标都必须有公式——首页公式区直接渲染它。"""
    assert set(METRIC_SPECS) == set(METRIC_IDS)
    for metric_id, spec in METRIC_SPECS.items():
        assert spec.formula.strip(), metric_id
        assert spec.label.strip(), metric_id
        assert spec.numerator.strip(), metric_id
        assert spec.denominator.strip(), metric_id
        assert spec.description.strip(), metric_id
        assert spec.direction in ("higher_is_better", "lower_is_better"), metric_id
        assert spec.id == metric_id


def test_metric_specs_formula_matches_contract_verbatim() -> None:
    """公式串是契约的一部分：逐字锁死，不许用 ASCII 替换 ÷ ≥ Σ ₂ − —。"""
    expected = {
        "precision_at_k": "相关块数(top-k) ÷ k",
        "recall_at_k": "相关块数(top-k) ÷ 相关块数(pool)",
        "hit_at_k": "命中查询数 ÷ 可评查询数",
        "mrr": "平均( 1 ÷ 首个相关块名次 )",
        "ndcg_at_k": "DCG ÷ IDCG，其中 DCG = Σ (2^grade − 1) ÷ log₂(名次+1)",
        "pool_coverage": "相关块数(pool) ÷ 池候选总数",
        "unscored_rate": "not_assessable 查询数 ÷ 总查询数",
    }
    for metric_id, formula in expected.items():
        assert METRIC_SPECS[metric_id].formula == formula, metric_id


def test_metric_specs_numerator_denominator_match_contract() -> None:
    expected = {
        "precision_at_k": ("top-k 中 grade≥2 的块数", "k"),
        "recall_at_k": ("top-k 中 grade≥2 的块数", "候选池中 grade≥2 的块数"),
        "hit_at_k": ("top-k 含 ≥1 相关块的查询数", "全部可评查询数"),
        "mrr": ("—", "—"),
        "ndcg_at_k": ("—", "—"),
        "pool_coverage": ("池中 grade≥2 的块数", "池候选总数"),
        "unscored_rate": ("not_assessable 查询数", "全部查询数"),
    }
    for metric_id, (numerator, denominator) in expected.items():
        spec = METRIC_SPECS[metric_id]
        assert spec.numerator == numerator, metric_id
        assert spec.denominator == denominator, metric_id


def test_metric_specs_direction_matches_contract() -> None:
    expected = {
        "precision_at_k": "higher_is_better",
        "recall_at_k": "higher_is_better",
        "hit_at_k": "higher_is_better",
        "mrr": "higher_is_better",
        "ndcg_at_k": "higher_is_better",
        "pool_coverage": "higher_is_better",
        "unscored_rate": "lower_is_better",
    }
    for metric_id, direction in expected.items():
        assert METRIC_SPECS[metric_id].direction == direction, metric_id
