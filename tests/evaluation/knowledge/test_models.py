"""知识评测数据模型：标注来源由服务端推导，检索参数只走白名单。

契约 §3.1 的三条硬约束在这一层落地，各由下面的测试逐条钉住：

1. ``label_source`` **由服务端推导**：``expect_any`` 非空 → ``expect``，否则 →
   ``judge``。客户端显式传值一律被覆写（不漂移口径）。
2. ``KnowledgeRetrievalConfig`` 是**白名单**：未声明字段一律校验拒绝，**不静默忽略**。
3. 缺证据是 ``None`` + ``not_assessable``，**绝不补 0**——模型默认值必须是 ``None``。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nanobot.evaluation.knowledge.models import (
    ALLOWED_RETRIEVAL_FIELDS,
    KnowledgeEvalBudgets,
    KnowledgeEvalCase,
    KnowledgeEvalCaseMetrics,
    KnowledgeEvalConfigEntry,
    KnowledgeEvalDatasetCreate,
    KnowledgeEvalDatasetPatch,
    KnowledgeEvalHit,
    KnowledgeEvalResult,
    KnowledgeEvalRunCreate,
    KnowledgeRetrievalConfig,
)

# ---- 约束 1：label_source 由服务端推导 --------------------------------


def test_label_source_is_derived_from_expect_any() -> None:
    with_expect = KnowledgeEvalCase(query="如何回滚", expect_any=["回滚"])
    assert with_expect.label_source == "expect"

    without = KnowledgeEvalCase(query="如何回滚")
    assert without.label_source == "judge"


def test_client_supplied_label_source_is_ignored() -> None:
    """客户端传什么都不作数——否则同一运行里两种标注会混算。"""
    case = KnowledgeEvalCase(query="如何回滚", expect_any=[], label_source="expect")
    assert case.label_source == "judge"

    judged = KnowledgeEvalCase(query="如何回滚", expect_any=["回滚"], label_source="judge")
    assert judged.label_source == "expect"


def test_blank_expect_entries_do_not_make_case_expect_labeled() -> None:
    """全是空白的 expect_any 等价于没有 expect_any。"""
    case = KnowledgeEvalCase(query="如何回滚", expect_any=["   ", ""])
    assert case.expect_any == []
    assert case.label_source == "judge"


def test_label_source_survives_dump_and_revalidate() -> None:
    """dump → 重新校验后仍由 expect_any 推导，不被序列化值带跑。"""
    case = KnowledgeEvalCase(query="如何回滚", expect_any=["回滚"])
    reloaded = KnowledgeEvalCase.model_validate(case.model_dump())
    assert reloaded.label_source == "expect"

    judged = KnowledgeEvalCase(query="如何回滚")
    assert KnowledgeEvalCase.model_validate(judged.model_dump()).label_source == "judge"


# ---- 用例本身的校验 ---------------------------------------------------


def test_case_rejects_blank_query() -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvalCase(query="   ")


def test_case_rejects_overlong_query() -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvalCase(query="q" * 501)


def test_case_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvalCase(query="如何回滚", label="gold")  # type: ignore[call-arg]


def test_case_rejects_invalid_mode() -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvalCase(query="如何回滚", mode="dense")  # type: ignore[arg-type]


def test_case_rejects_out_of_range_limit() -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvalCase(query="如何回滚", limit=21)


def test_case_rejects_too_many_expect_entries() -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvalCase(query="如何回滚", expect_any=[f"t{i}" for i in range(9)])


# ---- 约束 2：检索参数白名单 --------------------------------------------


def test_retrieval_config_rejects_unknown_field() -> None:
    """未声明字段一律拒绝，不静默忽略。"""
    with pytest.raises(ValidationError):
        KnowledgeRetrievalConfig(tool_output_chars=9999)


def test_retrieval_config_accepts_whitelisted_overrides() -> None:
    config = KnowledgeRetrievalConfig(vector_weight=0.5, rrf_k=30, mode="keyword", limit=5)
    assert config.vector_weight == 0.5
    assert config.rrf_k == 30
    assert config.mode == "keyword"
    assert config.limit == 5


def test_retrieval_config_whitelist_matches_declared_fields() -> None:
    """白名单常量与模型实际声明的字段必须一一对应。"""
    assert set(KnowledgeRetrievalConfig.model_fields) == set(ALLOWED_RETRIEVAL_FIELDS)
    assert "tool_output_chars" not in ALLOWED_RETRIEVAL_FIELDS


def test_retrieval_config_defaults_mean_use_product_defaults() -> None:
    """缺省字段留空——由产品默认值兜底，不在这里复制一份默认值。"""
    config = KnowledgeRetrievalConfig()
    assert config.vector_weight is None
    assert config.rrf_k is None
    assert config.mode is None
    assert config.limit is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rrf_k", 0),
        ("rrf_k", 1001),
        ("limit", 0),
        ("limit", 21),
        ("vector_weight", 1.5),
        ("vector_weight", -0.1),
        ("keyword_weight", 1.5),
        ("pool_size", 0),
        ("pool_size", 201),
        ("keyword_pool_size", 301),
        ("default_limit", 21),
        ("max_limit", 21),
        ("mmr_lambda", 1.2),
        ("relevance_band", -0.1),
        ("adjacent_similarity_floor", 1.2),
        ("dedup_containment", 1.2),
        ("dedup_unique_ratio", -0.1),
        ("dedup_jaccard", 1.2),
        ("dedup_length_ratio", 0.5),
        ("dedup_length_ratio", 101),
    ],
)
def test_retrieval_config_rejects_out_of_range_values(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        KnowledgeRetrievalConfig(**{field: value})


def test_retrieval_config_rejects_invalid_mode() -> None:
    with pytest.raises(ValidationError):
        KnowledgeRetrievalConfig(mode="dense")  # type: ignore[arg-type]


# ---- 数据集 -----------------------------------------------------------


def test_dataset_rejects_too_many_cases() -> None:
    cases = [{"query": f"q{i}"} for i in range(501)]
    with pytest.raises(ValidationError):
        KnowledgeEvalDatasetCreate(name="ds", cases=cases)


def test_dataset_accepts_cases_at_limit() -> None:
    cases = [{"query": f"q{i}"} for i in range(500)]
    dataset = KnowledgeEvalDatasetCreate(name="ds", cases=cases)
    assert len(dataset.cases) == 500
    assert dataset.cases[0].label_source == "judge"


def test_dataset_rejects_blank_name() -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvalDatasetCreate(name="")


def test_dataset_patch_requires_base_revision() -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvalDatasetPatch()  # type: ignore[call-arg]

    patch = KnowledgeEvalDatasetPatch(base_revision="3", name="新名")
    assert patch.base_revision == "3"
    assert patch.cases is None


# ---- 运行 -------------------------------------------------------------


def test_run_create_rejects_more_than_three_configs() -> None:
    configs = [{"id": f"c{i}"} for i in range(4)]
    with pytest.raises(ValidationError):
        KnowledgeEvalRunCreate(dataset_id="ds", configs=configs)  # type: ignore[arg-type]


def test_run_create_requires_at_least_one_config() -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvalRunCreate(dataset_id="ds", configs=[])  # type: ignore[arg-type]


def test_run_create_rejects_duplicate_config_ids() -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvalRunCreate(
            dataset_id="ds",
            configs=[{"id": "base"}, {"id": "base"}],  # type: ignore[list-item]
        )


def test_run_create_accepts_budgets_and_overrides() -> None:
    run = KnowledgeEvalRunCreate(
        dataset_id="ds",
        configs=[
            KnowledgeEvalConfigEntry(
                id="base",
                name="基线",
                overrides=KnowledgeRetrievalConfig(vector_weight=0.5),
            )
        ],
        budgets=KnowledgeEvalBudgets(max_queries=10, concurrency=2),
    )
    assert run.configs[0].overrides.vector_weight == 0.5
    assert run.budgets.max_queries == 10
    assert run.judge_profile_id is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_queries", 0),
        ("max_queries", 501),
        ("max_judge_calls", 0),
        ("max_judge_calls", 5001),
        ("per_query_timeout_s", 0),
        ("per_query_timeout_s", 301),
        ("concurrency", 0),
        ("concurrency", 9),
    ],
)
def test_budgets_reject_out_of_range_values(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvalBudgets(**{field: value})


# ---- 约束 3：缺证据是 None + not_assessable，绝不补 0 ------------------


def test_metrics_default_to_none_not_zero() -> None:
    metrics = KnowledgeEvalCaseMetrics()
    assert metrics.hit_at_k is None
    assert metrics.precision_at_k is None
    assert metrics.mrr is None
    assert metrics.ndcg_at_k is None
    assert metrics.recall_at_k is None
    assert metrics.pool_coverage is None
    # 没有任何证据 → 不能自称已评分。
    assert metrics.scored is False


def test_metrics_with_evidence_are_scored() -> None:
    metrics = KnowledgeEvalCaseMetrics(hit_at_k=0.0, precision_at_k=0.0)
    assert metrics.scored is True


def test_result_defaults_keep_missing_evidence_as_none() -> None:
    result = KnowledgeEvalResult(
        run_id="run-1",
        case_id="c1",
        status="not_assessable",
        label_source="judge",
    )
    assert result.metrics.recall_at_k is None
    assert result.metrics.scored is False
    assert result.hits == []
    assert result.error is None


def test_hit_keeps_optional_grade_absent() -> None:
    hit = KnowledgeEvalHit(chunk_id="chk_1", rank=1, score=0.9, content_hash="h1")
    assert hit.grade is None
    assert hit.reason == ""


def test_hit_rejects_out_of_range_grade() -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvalHit(chunk_id="chk_1", rank=1, score=0.9, content_hash="h1", grade=9)


def test_scored_is_false_when_pool_has_no_relevant() -> None:
    """池里没有相关块 → 该题不可评。

    这类题 hit/precision 等指标仍算出数值，若 ``scored`` 按「有值即已评分」
    判定，就会把 ``not_assessable`` 伪装成 ``scored``——前端「未评分」徽标
    会说反。``scored`` 必须由权威结论派生。
    """
    metrics = KnowledgeEvalCaseMetrics(
        hit_at_k=0.0,
        precision_at_k=0.0,
        mrr=0.0,
        ndcg_at_k=0.0,
        recall_at_k=None,
        pool_coverage=0.0,
        reason="POOL_HAS_NO_RELEVANT",
    )
    assert metrics.scored is False


def test_scored_is_false_when_any_metric_is_not_assessable() -> None:
    """逐指标状态里有 not_assessable → 该指标不可评。"""
    metrics = KnowledgeEvalCaseMetrics(
        hit_at_k=1.0,
        precision_at_k=0.5,
        mrr=1.0,
        ndcg_at_k=1.0,
        recall_at_k=None,
        pool_coverage=0.5,
        metric_status={"recall_at_k": "not_assessable", "precision_at_k": "scored"},
    )
    assert metrics.scored is False


def test_scored_is_true_for_fully_scored_case() -> None:
    metrics = KnowledgeEvalCaseMetrics(
        hit_at_k=1.0,
        precision_at_k=0.5,
        mrr=1.0,
        ndcg_at_k=1.0,
        recall_at_k=1.0,
        pool_coverage=0.5,
    )
    assert metrics.scored is True


def test_case_score_and_metrics_model_agree_on_scored() -> None:
    """metrics 计算与模型派生必须一致——这是 Task 7 runner 的前提。"""
    from nanobot.evaluation.knowledge.metrics import case_score

    scored = case_score(
        case_id="c1",
        label_source="judge",
        top_k=[("a", 3)],
        pool_grades={"a": 3},
        k=1,
    )
    assert scored.status == "scored"
    model = KnowledgeEvalCaseMetrics(**scored.metrics, reason=scored.reason)
    assert model.scored is True

    unscored = case_score(
        case_id="c2",
        label_source="judge",
        top_k=[("a", 0)],
        pool_grades={"a": 0},
        k=1,
    )
    assert unscored.status == "not_assessable"
    model = KnowledgeEvalCaseMetrics(**unscored.metrics, reason=unscored.reason)
    assert model.scored is False, "not_assessable 的题不得自称已评分"
