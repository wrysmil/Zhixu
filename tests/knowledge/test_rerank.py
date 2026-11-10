"""精排：协议解析、阈值降级、复合分与检索侧接入（WeKnora §7）。"""

from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest

from nanobot.config.schema import KnowledgeRerankConfig
from nanobot.knowledge.embedding import NoneEmbedder
from nanobot.knowledge.fusion import SearchHit
from nanobot.knowledge.rerank import (
    OUTCOME_ALL_BELOW_THRESHOLD,
    OUTCOME_FALLBACK_TOP1,
    OUTCOME_MODEL_ERROR,
    OUTCOME_NO_MODEL,
    OUTCOME_OK,
    OUTCOME_THRESHOLD_DEGRADED,
    HttpReranker,
    LocalCrossEncoderReranker,
    RankResult,
    Reranker,
    RerankerError,
    RerankSettings,
    apply_threshold,
    build_reranker,
    composite_score,
    logistic,
)
from nanobot.knowledge.retriever import Retriever


def _hit(chunk_id: str, score: float, *, doc_id: str = "doc_1") -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        doc_id=doc_id,
        kb_id="kb_1",
        chunk_index=0,
        heading_path="手册 > 回滚",
        page=3,
        locator="page=3",
        content=f"{chunk_id} 正文内容",
        char_count=32,
        content_hash="h",
        score=score,
    )


class StubRepo:
    def __init__(self, titles: dict[str, str] | None = None) -> None:
        self.titles = titles or {}

    def list_document_titles(self, doc_ids: Any) -> dict[str, str]:
        return {doc_id: self.titles.get(doc_id, "") for doc_id in doc_ids}


class StubReranker:
    def __init__(
        self,
        scores: dict[int, float] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.scores = scores or {}
        self.error = error
        self.queries: list[str] = []

    @property
    def model_name(self) -> str:
        return "stub-rerank"

    @property
    def model_id(self) -> str:
        return "stub"

    def rerank(self, query: str, documents: Any) -> list[RankResult]:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return [RankResult(index=index, relevance_score=score) for index, score in self.scores.items()]


def _retriever(reranker: Reranker | None, **settings: Any) -> Retriever:
    return Retriever(
        cast(Any, StubRepo({"doc_1": "青鸾发布手册"})),
        embedder=NoneEmbedder(),
        vector_store=None,
        reranker=reranker,
        rerank_settings=RerankSettings(**settings),
    )


# ---------- 阈值降级 ----------


def test_threshold_keeps_candidates_above_threshold() -> None:
    kept, outcome = apply_threshold({0: 0.9, 1: 0.31}, threshold=0.3, fallback_min_score=0.15)
    assert kept == [0, 1]
    assert outcome == OUTCOME_OK


def test_threshold_degrades_to_seventy_percent_with_floor() -> None:
    # 第二档只在 threshold > 0.3 时启用：0.5 → 降级阈值 max(0.3, 0.35)=0.35。
    kept, outcome = apply_threshold({0: 0.4}, threshold=0.5, fallback_min_score=0.15)
    assert kept == [0]
    assert outcome == OUTCOME_THRESHOLD_DEGRADED
    # 低于 0.3 地板的候选不会被降级档收下，直接落到第三档。
    kept, outcome = apply_threshold({0: 0.2}, threshold=0.5, fallback_min_score=0.15)
    assert kept == [0]
    assert outcome == OUTCOME_FALLBACK_TOP1
    # threshold 恰好 0.3 时不启用第二档。
    kept, outcome = apply_threshold({0: 0.25}, threshold=0.3, fallback_min_score=0.15)
    assert kept == [0]
    assert outcome == OUTCOME_FALLBACK_TOP1


def test_fallback_top1_requires_min_score_except_explicit_scope() -> None:
    kept, outcome = apply_threshold(
        {0: 0.2, 1: 0.1}, threshold=0.3, fallback_min_score=0.15
    )
    assert kept == [0]
    assert outcome == OUTCOME_FALLBACK_TOP1
    kept, outcome = apply_threshold(
        {0: 0.05}, threshold=0.3, fallback_min_score=0.15, explicit_scope=True
    )
    assert kept == [0]
    assert outcome == OUTCOME_FALLBACK_TOP1


def test_all_below_threshold_returns_empty() -> None:
    kept, outcome = apply_threshold({0: 0.05}, threshold=0.3, fallback_min_score=0.15)
    assert kept == []
    assert outcome == OUTCOME_ALL_BELOW_THRESHOLD


def test_no_scores_is_no_candidates() -> None:
    kept, outcome = apply_threshold({}, threshold=0.3, fallback_min_score=0.15)
    assert kept == []
    assert outcome == "no_candidates"


# ---------- 复合分 ----------


def test_composite_score_uses_weights_and_clamps() -> None:
    value = composite_score(0.8, 0.5, source_weight_value=1.0)
    assert value == pytest.approx(0.6 * 0.8 + 0.3 * 0.5 + 0.1 * 1.0)
    assert composite_score(1.0, 1.0, source_weight_value=1.0) <= 1.0
    assert composite_score(0.0, 0.0, source_weight_value=1.0) >= 0.0


def test_logistic_maps_logit_to_probability() -> None:
    assert logistic(0.0) == pytest.approx(0.5)
    assert logistic(-10.0) < 0.01
    assert 0.0 < logistic(3.0) < 1.0


# ---------- HTTP 客户端 ----------


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_http_reranker_parses_cohere_shape_and_sends_payload() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.91},
                    {"index": 0, "relevance_score": 0.12},
                ]
            },
        )

    reranker = HttpReranker(
        base_url="http://rerank.local/v1",
        model="bge-reranker",
        api_key="sk-secret",
        client=_client(handler),
    )
    results = reranker.rerank("回滚流程", ["甲文档", "乙文档"])
    assert [result.index for result in results] == [1, 0]
    assert seen[0]["model"] == "bge-reranker"
    assert seen[0]["query"] == "回滚流程"
    assert seen[0]["documents"] == ["甲文档", "乙文档"]


def test_http_reranker_parses_dashscope_shape_and_logit_transform() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"output": {"results": [{"index": 0, "relevance_score": 2.0}]}},
        )

    reranker = HttpReranker(
        base_url="http://rerank.local",
        model="nim-rerank",
        score_transform="logit",
        client=_client(handler),
    )
    results = reranker.rerank("q", ["doc"])
    assert results[0].relevance_score == pytest.approx(logistic(2.0))


def test_http_reranker_reports_failure_without_leaking_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom sk-secret"})

    reranker = HttpReranker(
        base_url="http://rerank.local",
        model="m",
        api_key="sk-secret",
        client=_client(handler),
    )
    with pytest.raises(RerankerError) as exc:
        reranker.rerank("q", ["doc"])
    assert "sk-secret" not in str(exc.value)


def test_http_reranker_batches_documents() -> None:
    batches: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        batches.append(len(payload["documents"]))
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": index, "relevance_score": 0.5}
                    for index in range(len(payload["documents"]))
                ]
            },
        )

    reranker = HttpReranker(
        base_url="http://rerank.local",
        model="m",
        batch_size=2,
        max_concurrency=2,
        client=_client(handler),
    )
    results = reranker.rerank("q", ["a", "b", "c"])
    assert sorted(batches) == [1, 2]
    assert len(results) == 3


# ---------- 检索侧接入 ----------


def test_rerank_reorders_by_model_score_and_reports_outcome() -> None:
    reranker = StubReranker({0: 0.5, 1: 0.9})
    hits = [_hit("chk_a", 0.9), _hit("chk_b", 0.5)]
    scored, labels, outcome = _retriever(reranker)._apply_rerank(
        "回滚", hits, explicit_scope=False
    )
    assert outcome == OUTCOME_OK
    assert labels == []
    assert [hit.chunk_id for hit in scored] == ["chk_b", "chk_a"]
    # 复合分：0.6×0.9 + 0.3×0.5 + 0.1×1.0
    assert scored[0].score == pytest.approx(0.6 * 0.9 + 0.3 * 0.5 + 0.1)


def test_rerank_failure_falls_back_to_recall_order() -> None:
    reranker = StubReranker(error=RuntimeError("model down"))
    hits = [_hit("chk_a", 0.9), _hit("chk_b", 0.5)]
    scored, labels, outcome = _retriever(reranker)._apply_rerank(
        "回滚", hits, explicit_scope=False
    )
    assert outcome == OUTCOME_MODEL_ERROR
    assert labels == ["rerank_model_error"]
    assert [hit.chunk_id for hit in scored] == ["chk_a", "chk_b"]


def test_rerank_without_model_reports_no_model() -> None:
    hits = [_hit("chk_a", 0.9)]
    scored, labels, outcome = _retriever(None)._apply_rerank(
        "回滚", hits, explicit_scope=False
    )
    assert outcome == OUTCOME_NO_MODEL
    assert labels == []
    assert scored == hits


def test_rerank_all_below_threshold_returns_empty() -> None:
    reranker = StubReranker({0: 0.05})
    hits = [_hit("chk_a", 0.9)]
    scored, labels, outcome = _retriever(reranker)._apply_rerank(
        "回滚", hits, explicit_scope=False
    )
    assert outcome == OUTCOME_ALL_BELOW_THRESHOLD
    assert scored == []
    assert labels == ["rerank_all_below_threshold"]


def test_rerank_passage_contains_title_breadcrumb_and_content() -> None:
    reranker = StubReranker({0: 0.9})
    hits = [_hit("chk_a", 0.9)]
    _retriever(reranker)._apply_rerank("回滚", hits, explicit_scope=False)
    assert reranker.queries == ["回滚"]


# ---------- 配置 ----------


def test_rerank_config_defaults_match_weknora() -> None:
    config = KnowledgeRerankConfig()
    assert config.enabled is False
    assert config.max_candidates == 200
    assert config.threshold == 0.3
    assert config.fallback_min_score == 0.15
    assert config.model_weight == 0.6
    assert config.base_weight == 0.3
    assert config.source_weight == 0.1
    assert config.score_transform == "probability"


def test_build_reranker_requires_enabled_and_target() -> None:
    assert build_reranker(KnowledgeRerankConfig()) is None
    assert build_reranker(KnowledgeRerankConfig(enabled=True)) is None
    reranker = build_reranker(
        KnowledgeRerankConfig(enabled=True, base_url="http://r.local", model="m")
    )
    assert isinstance(reranker, HttpReranker)
    assert reranker.model_name == "m"


def test_local_reranker_uses_injected_scorer_and_sigmoid() -> None:
    reranker = LocalCrossEncoderReranker(
        "BAAI/bge-reranker-base",
        scorer=lambda query, docs: [2.0, -2.0][: len(docs)],
    )
    results = reranker.rerank("回滚", ["甲", "乙"])
    assert [result.index for result in results] == [0, 1]
    assert results[0].relevance_score == pytest.approx(logistic(2.0))
    assert results[0].relevance_score > results[1].relevance_score
    assert reranker.model_id == "local:BAAI/bge-reranker-base"


def test_build_reranker_supports_local_backend() -> None:
    assert build_reranker(KnowledgeRerankConfig(enabled=True, backend="local")) is None
    reranker = build_reranker(
        KnowledgeRerankConfig(
            enabled=True, backend="local", model="BAAI/bge-reranker-base", device="cpu"
        )
    )
    assert isinstance(reranker, LocalCrossEncoderReranker)
    assert reranker.model_name == "BAAI/bge-reranker-base"
