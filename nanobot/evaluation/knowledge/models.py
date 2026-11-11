"""知识检索评测的数据模型（契约 §3.1）。

三条硬约束在这里落地：

1. ``label_source`` **由服务端推导**：``expect_any`` 非空 → ``expect``，否则 →
   ``judge``。客户端传什么都不作数——否则同一运行里两种标注会混算，口径漂移。
2. ``KnowledgeRetrievalConfig`` 是**白名单**：只声明允许覆写的检索字段，
   未声明字段一律校验拒绝，**不静默忽略**（``tool_output_chars`` 属于工具层参数，
   不影响检索结果，故不开放）。
3. 指标缺证据是 ``None`` + ``not_assessable``，**绝不补 0**——所有指标字段的
   默认值都是 ``None``，没有任何证据时 ``scored`` 为 ``False``。

本模块刻意不 import ``nanobot.config.schema``：产品配置是**默认值来源**，
评测模型只声明「允许覆写哪些字段、取值范围是多少」，不复制一份产品默认值。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: 标注来源：``expect`` = 关键词确定性标注（零成本），``judge`` = LLM 判定。
LabelSource = Literal["expect", "judge"]
#: 检索模式，与产品 ``Retriever`` 的模式口径一致。
SearchMode = Literal["hybrid", "semantic", "keyword"]
#: 运行状态。
RunStatus = Literal["queued", "running", "completed", "cancelled", "interrupted", "failed"]
#: 单题状态：``scored`` / ``not_assessable``（缺证据）/ ``failed``。
CaseStatus = Literal["scored", "not_assessable", "failed"]

#: 检索参数白名单：只允许覆写这些字段（契约 §3.1）。
#:
#: 数值范围与产品 ``KnowledgeRetrievalConfig``（``nanobot/config/schema.py``）对齐；
#: ``tool_output_chars`` **不在**其中——它是工具层输出长度，不影响检索结果。
ALLOWED_RETRIEVAL_FIELDS = frozenset(
    {
        "vector_weight",
        "keyword_weight",
        "rrf_k",
        "pool_size",
        "keyword_pool_size",
        "default_limit",
        "max_limit",
        "mmr_lambda",
        "relevance_band",
        "adjacent_similarity_floor",
        "dedup_containment",
        "dedup_unique_ratio",
        "dedup_jaccard",
        "dedup_length_ratio",
        "mode",
        "limit",
        "graph_enabled",
        "graph_hops",
        "graph_seed_limit",
        "graph_weight",
        "graph_max_chunks",
    }
)


class _Base(BaseModel):
    """评测模型的共同基类：未知字段一律拒绝，不做静默透传。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class KnowledgeEvalCase(_Base):
    """一条查询用例。"""

    id: str = Field(default="", max_length=120)
    query: str = Field(min_length=1, max_length=500)
    expect_any: list[str] = Field(default_factory=list, max_length=8)
    kb_ids: list[str] = Field(default_factory=list, max_length=32)
    mode: SearchMode = "hybrid"
    limit: int = Field(default=8, ge=1, le=20)
    label_source: LabelSource = "judge"
    ordinal: int = Field(default=0, ge=0)

    @field_validator("query")
    @classmethod
    def _non_blank_query(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("query 不能为空")
        return text

    @field_validator("expect_any")
    @classmethod
    def _clean_expect(cls, value: list[str]) -> list[str]:
        """去掉首尾空白与全空白项——全空白等价于「没有 expect」。"""
        return [item.strip() for item in value if item and item.strip()]

    @field_validator("kb_ids")
    @classmethod
    def _clean_kb_ids(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]

    @model_validator(mode="after")
    def _derive_label_source(self) -> KnowledgeEvalCase:
        """标注来源由服务端推导，客户端传什么都不作数。"""
        self.label_source = "expect" if self.expect_any else "judge"
        return self


class KnowledgeEvalDatasetCreate(_Base):
    """建数据集。"""

    name: str = Field(min_length=1, max_length=120)
    notes: str = Field(default="", max_length=500)
    cases: list[KnowledgeEvalCase] = Field(default_factory=list, max_length=500)


class KnowledgeEvalDatasetPatch(_Base):
    """改数据集；``cases`` 是**整表替换**语义，不是增量。"""

    base_revision: str = Field(min_length=1, max_length=120)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=500)
    cases: list[KnowledgeEvalCase] | None = Field(default=None, max_length=500)

    @field_validator("base_revision", mode="before")
    @classmethod
    def _normalize_revision(cls, value: object) -> object:
        """版本号按字符串传输；仓储侧用整数时也能接住。"""
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value


class KnowledgeRetrievalConfig(_Base):
    """检索参数覆写白名单。

    缺省字段表示「用产品默认值」——这里只声明**允许覆写哪些字段**与取值范围，
    不复制产品默认值（默认值唯一真相源在 ``nanobot/config/schema.py``）。
    """

    vector_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    keyword_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    rrf_k: int | None = Field(default=None, ge=1, le=1000)
    pool_size: int | None = Field(default=None, ge=1, le=200)
    keyword_pool_size: int | None = Field(default=None, ge=1, le=300)
    default_limit: int | None = Field(default=None, ge=1, le=20)
    max_limit: int | None = Field(default=None, ge=1, le=20)
    mmr_lambda: float | None = Field(default=None, ge=0.0, le=1.0)
    relevance_band: float | None = Field(default=None, ge=0.0, le=1.0)
    adjacent_similarity_floor: float | None = Field(default=None, ge=0.0, le=1.0)
    dedup_containment: float | None = Field(default=None, ge=0.0, le=1.0)
    dedup_unique_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    dedup_jaccard: float | None = Field(default=None, ge=0.0, le=1.0)
    dedup_length_ratio: float | None = Field(default=None, ge=1.0, le=100.0)
    mode: SearchMode | None = None
    limit: int | None = Field(default=None, ge=1, le=20)
    graph_enabled: bool | None = None
    graph_hops: int | None = Field(default=None, ge=1, le=2)
    graph_seed_limit: int | None = Field(default=None, ge=1, le=10)
    graph_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    graph_max_chunks: int | None = Field(default=None, ge=10, le=200)


class KnowledgeEvalBudgets(_Base):
    """一次运行的预算上限。"""

    max_queries: int = Field(default=200, ge=1, le=500)
    max_judge_calls: int = Field(default=500, ge=1, le=5000)
    per_query_timeout_s: float = Field(default=60.0, ge=1.0, le=300.0)
    concurrency: int = Field(default=4, ge=1, le=8)


class KnowledgeEvalConfigEntry(_Base):
    """一次运行里被评的一组检索配置。"""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=120)
    overrides: KnowledgeRetrievalConfig = Field(default_factory=KnowledgeRetrievalConfig)


class KnowledgeEvalRunCreate(_Base):
    """发起一次运行；请求内不执行检索。"""

    dataset_id: str = Field(min_length=1, max_length=120)
    configs: list[KnowledgeEvalConfigEntry] = Field(min_length=1, max_length=3)
    judge_profile_id: str | None = Field(default=None, max_length=120)
    budgets: KnowledgeEvalBudgets = Field(default_factory=KnowledgeEvalBudgets)

    @model_validator(mode="after")
    def _unique_config_ids(self) -> KnowledgeEvalRunCreate:
        ids = [entry.id for entry in self.configs]
        if len(set(ids)) != len(ids):
            raise ValueError("configs 的 id 必须唯一")
        return self


class KnowledgeEvalHit(_Base):
    """一条检索命中。"""

    chunk_id: str
    rank: int = Field(ge=1)
    score: float
    content_hash: str = ""
    grade: int | None = Field(default=None, ge=0, le=3)
    reason: str = ""


class KnowledgeEvalCaseMetrics(_Base):
    """单题指标。

    缺证据的指标是 ``None``，**绝不补 0**——``0.0`` 与「算出来是 0」在
    聚合时不可区分，补 0 会把「没证据」伪装成「很差」。
    """

    hit_at_k: float | None = None
    precision_at_k: float | None = None
    mrr: float | None = None
    ndcg_at_k: float | None = None
    recall_at_k: float | None = None
    pool_coverage: float | None = None
    scored: bool = True
    reason: str = ""
    #: 逐指标状态：``scored`` / ``not_assessable``（契约 §3.2）。
    metric_status: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _derive_scored(self) -> KnowledgeEvalCaseMetrics:
        """``scored`` 由**权威结论**派生，不由「值是否全 None」猜测。

        三条不可评路径：

        1. ``reason`` 标了「池里没有相关块」→ 该题不可评，尽管 hit/precision
           等指标仍算出了数值——此时若按「有值即已评分」判定，会把
           ``not_assessable`` 伪装成 ``scored``，前端徽标会说反。
        2. ``metric_status`` 里任一指标为 ``not_assessable`` → 该指标不可评。
        3. 一个指标都没算出来 → 缺证据（不是 0 分）。
        """
        if self.reason == "POOL_HAS_NO_RELEVANT":
            self.scored = False
            return self
        if any(
            state != "scored" for state in self.metric_status.values()
        ):
            self.scored = False
            return self
        if self.scored and all(
            value is None
            for value in (
                self.hit_at_k,
                self.precision_at_k,
                self.mrr,
                self.ndcg_at_k,
                self.recall_at_k,
                self.pool_coverage,
            )
        ):
            self.scored = False
        return self


class KnowledgeEvalResult(_Base):
    """单题结果。"""

    run_id: str
    case_id: str
    status: CaseStatus
    label_source: LabelSource
    hits: list[KnowledgeEvalHit] = Field(default_factory=list)
    metrics: KnowledgeEvalCaseMetrics = Field(default_factory=KnowledgeEvalCaseMetrics)
    error: str | None = None


__all__ = [
    "ALLOWED_RETRIEVAL_FIELDS",
    "CaseStatus",
    "KnowledgeEvalBudgets",
    "KnowledgeEvalCase",
    "KnowledgeEvalCaseMetrics",
    "KnowledgeEvalConfigEntry",
    "KnowledgeEvalDatasetCreate",
    "KnowledgeEvalDatasetPatch",
    "KnowledgeEvalHit",
    "KnowledgeEvalResult",
    "KnowledgeEvalRunCreate",
    "KnowledgeRetrievalConfig",
    "LabelSource",
    "RunStatus",
    "SearchMode",
]
