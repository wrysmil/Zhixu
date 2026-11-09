"""runner 的纯函数口径与端到端编排。

纯函数（并池去重 / 四态映射 / 分组不混算）与编排（检索 → 并池 → 判定 → 打分
→ 落库）都在这里钉住。编排用假 repository / 假 knowledge_service / 假 provider，
不依赖真实检索设施与真实 LLM——「expect 组零 LLM 成本」「judge 组每查询一次
批量调用」这两条口径必须可离线复现。
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from typing import Any

import pytest

from nanobot.evaluation.knowledge.models import KnowledgeEvalCaseMetrics
from nanobot.evaluation.knowledge.repository import KnowledgeEvalRepository
from nanobot.evaluation.knowledge.runner import (
    KnowledgeEvalRunner,
    build_pool,
    map_search_reason,
    split_by_label_source,
)
from nanobot.knowledge.retriever import KnowledgeSearchError

# --------------------------------------------------------------------------- 纯函数


class _FakeHit:
    def __init__(self, chunk_id: str, content: str, score: float = 0.5) -> None:
        self.chunk_id = chunk_id
        self.content = content
        self.content_hash = f"hash-{chunk_id}"
        self.score = score


def test_build_pool_dedupes_by_chunk_id() -> None:
    pool = build_pool(
        {
            "base": [_FakeHit("c1", "回滚用 rollout undo")],
            "cand": [_FakeHit("c1", "回滚用 rollout undo"), _FakeHit("c2", "备份脚本")],
        }
    )
    assert {candidate.chunk_id for candidate in pool} == {"c1", "c2"}
    assert len(pool) == 2


def test_build_pool_keeps_first_content_for_duplicate_chunk() -> None:
    pool = build_pool(
        {
            "base": [_FakeHit("c1", "first")],
            "cand": [_FakeHit("c1", "second")],
        }
    )
    assert len(pool) == 1
    assert pool[0].content == "first"


def test_build_pool_skips_hits_without_chunk_id() -> None:
    pool = build_pool({"base": [_FakeHit("", "空 id"), _FakeHit("c1", "正文")]})
    assert [candidate.chunk_id for candidate in pool] == ["c1"]


def test_map_search_reason_no_candidates_is_scorable() -> None:
    """真的没搜到 → 记 0 分（是有效结果，不是配置问题）。"""
    assert map_search_reason("no_candidates", []) == "scored"


def test_map_search_reason_scope_problems_are_not_assessable() -> None:
    """no_scope / filtered / empty_query_tokens 是配置或查询问题，不记 0。"""
    assert map_search_reason("no_scope", []) == "not_assessable"
    assert map_search_reason("filtered", []) == "not_assessable"
    assert map_search_reason("empty_query_tokens", []) == "not_assessable"


def test_map_search_reason_none_is_scorable() -> None:
    assert map_search_reason(None, []) == "scored"


def test_split_by_label_source_keeps_groups_separate() -> None:
    class _Case:
        def __init__(self, case_id: str, label_source: str) -> None:
            self.id = case_id
            self.label_source = label_source

    groups = split_by_label_source(
        [_Case("a", "expect"), _Case("b", "judge"), _Case("c", "expect")]
    )
    assert [case.id for case in groups["expect"]] == ["a", "c"]
    assert [case.id for case in groups["judge"]] == ["b"]


def test_split_by_label_source_accepts_dict_cases() -> None:
    groups = split_by_label_source(
        [{"id": "a", "label_source": "expect"}, {"id": "b", "label_source": "judge"}]
    )
    assert [case["id"] for case in groups["expect"]] == ["a"]
    assert [case["id"] for case in groups["judge"]] == ["b"]


# --------------------------------------------------------------------------- 假设施


class _FakeResult:
    def __init__(
        self,
        hits: tuple[_FakeHit, ...] = (),
        *,
        reason: str | None = None,
        degraded: tuple[str, ...] = (),
    ) -> None:
        self.hits = hits
        self.reason = reason
        self.degraded = degraded


class _FakeRetriever:
    def __init__(self, service: _FakeService) -> None:
        self._service = service

    def search(
        self,
        query: str,
        *,
        workspace_ids: list[str],
        kb_ids: list[str] | None = None,
        mode: str = "hybrid",
        limit: int | None = None,
    ) -> _FakeResult:
        return self._service.handle(
            query,
            workspace_ids=workspace_ids,
            kb_ids=kb_ids,
            mode=mode,
            limit=limit,
        )


class _FakeService:
    """假知识服务：按 ``mode`` 返回预设检索结果或抛出预设异常。"""

    def __init__(
        self,
        results: dict[str, _FakeResult] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.results = results or {}
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def retriever_with(self, settings: Any) -> _FakeRetriever:
        return _FakeRetriever(self)

    def handle(
        self,
        query: str,
        *,
        workspace_ids: list[str],
        kb_ids: list[str] | None,
        mode: str,
        limit: int | None,
    ) -> _FakeResult:
        self.calls.append(
            {
                "query": query,
                "workspace_ids": list(workspace_ids),
                "kb_ids": list(kb_ids or []),
                "mode": mode,
                "limit": limit,
            }
        )
        if self.error is not None:
            raise self.error
        return self.results.get(mode, _FakeResult())


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls = None


class _FakeProvider:
    """假判定 provider：按候选块 id 输出严格 JSON 数组，并记录调用次数。"""

    def __init__(self, grades: dict[str, int] | None = None) -> None:
        self.grades = grades or {}
        self.calls: list[list[dict[str, Any]]] = []

    def get_default_model(self) -> str:
        return "fake-judge-model"

    async def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: Any = None,
        model: Any = None,
        **kwargs: Any,
    ) -> _FakeResponse:
        self.calls.append(list(messages))
        chunk_ids = re.findall(r"\[([^\[\]]+)\]", str(messages[-1]["content"]))
        payload = [
            {"chunk_id": chunk_id, "grade": int(self.grades.get(chunk_id, 0)), "reason": "假判定"}
            for chunk_id in chunk_ids
        ]
        return _FakeResponse(json.dumps(payload, ensure_ascii=False))


class _FakeRepository:
    """假仓储：记录落库与收尾调用，供编排断言。"""

    def __init__(self, run: dict[str, Any], dataset: dict[str, Any]) -> None:
        self.run = run
        self.dataset = dataset
        self.results: list[dict[str, Any]] = []
        self.finish_calls: list[dict[str, Any]] = []

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.run if self.run.get("id") == run_id else None

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        return self.dataset if self.dataset.get("id") == dataset_id else None

    def save_result(
        self,
        *,
        run_id: str,
        case_id: str,
        status: str,
        label_source: str,
        hits: Any = None,
        metrics: Any = None,
        error: str | None = None,
    ) -> None:
        self.results.append(
            {
                "run_id": run_id,
                "case_id": case_id,
                "status": status,
                "label_source": label_source,
                "hits": list(hits or []),
                "metrics": dict(metrics or {}),
                "error": error,
            }
        )

    def finish_run(
        self,
        run_id: str,
        status: str,
        metrics: Any = None,
        error: str | None = None,
    ) -> None:
        self.finish_calls.append(
            {
                "run_id": run_id,
                "status": status,
                "metrics": dict(metrics or {}),
                "error": error,
            }
        )
        self.run["status"] = status
        self.run["metrics"] = dict(metrics or {})

    def get_run_report(self, run_id: str) -> dict[str, Any]:
        return {"id": run_id, "results": list(self.results)}


# --------------------------------------------------------------------------- 编排


def _case(
    case_id: str,
    query: str,
    *,
    expect_any: list[str] | None = None,
    label_source: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "query": query,
        "expect_any": expect_any or [],
        "kb_ids": [],
        "mode": "hybrid",
        "limit": limit,
        "label_source": label_source or ("expect" if expect_any else "judge"),
        "ordinal": 0,
    }


def _repo(
    cases: list[dict[str, Any]],
    *,
    configs: list[dict[str, Any]] | None = None,
    budgets: dict[str, Any] | None = None,
) -> _FakeRepository:
    run = {
        "id": "run-1",
        "dataset_id": "ds-1",
        "status": "queued",
        "configs": configs or [{"id": "base", "name": "基线", "overrides": {"mode": "keyword", "limit": 5}}],
        "judge": {},
        "budgets": budgets or {},
        "metrics": {},
        "pool": {},
        "progress": {},
        "error": None,
    }
    dataset = {"id": "ds-1", "name": "集", "notes": "", "revision": 1, "cases": cases}
    return _FakeRepository(run, dataset)


async def test_expect_group_never_calls_llm_and_scores_precision_recall() -> None:
    """expect 组零 LLM 成本：关键词命中即 grade 3，否则 0。"""
    service = _FakeService(
        {
            "keyword": _FakeResult(
                (
                    _FakeHit("c1", "回滚用 rollout undo 步骤"),
                    _FakeHit("c2", "与问题无关的内容"),
                )
            )
        }
    )
    provider = _FakeProvider()
    repo = _repo([_case("q1", "如何回滚部署", expect_any=["回滚"])])

    runner = KnowledgeEvalRunner(
        repository=repo, knowledge_service=service, provider=provider
    )
    await runner.run("run-1")

    assert provider.calls == [], "expect 组不得调用 LLM"
    assert len(repo.results) == 1
    row = repo.results[0]
    assert row["case_id"] == "q1::base"
    assert row["status"] == "scored"
    assert row["label_source"] == "expect"
    assert row["metrics"]["precision_at_k"] == pytest.approx(1 / 5)
    assert row["metrics"]["recall_at_k"] == pytest.approx(1.0)

    groups = repo.finish_calls[-1]["metrics"]
    assert repo.finish_calls[-1]["status"] == "completed"
    assert groups["expect"]["metrics"]["precision_at_k"] == pytest.approx(1 / 5)
    assert "judge" not in groups


async def test_judge_group_calls_llm_once_per_query_with_all_candidates() -> None:
    """judge 组每查询一次批量调用（不是每块一次）。"""
    service = _FakeService(
        {
            "keyword": _FakeResult(
                (
                    _FakeHit("c1", "回滚用 rollout undo 步骤"),
                    _FakeHit("c2", "与问题无关的内容"),
                )
            )
        }
    )
    provider = _FakeProvider({"c1": 3, "c2": 0})
    repo = _repo([_case("q1", "如何回滚部署")])

    runner = KnowledgeEvalRunner(
        repository=repo, knowledge_service=service, provider=provider
    )
    await runner.run("run-1")

    assert len(provider.calls) == 1, "每查询一次批量调用"
    assert len(repo.results) == 1
    row = repo.results[0]
    assert row["label_source"] == "judge"
    assert row["status"] == "scored"
    assert row["metrics"]["precision_at_k"] == pytest.approx(1 / 5)
    assert row["metrics"]["recall_at_k"] == pytest.approx(1.0)
    assert row["hits"][0]["chunk_id"] == "c1"
    assert row["hits"][0]["grade"] == 3
    assert row["hits"][0]["reason"] == "假判定"


async def test_retrieval_exception_marks_case_failed_and_excluded_from_aggregate() -> None:
    """检索失败 ≠ 空结果：该题 failed，不参与聚合、不计 0。"""
    service = _FakeService(error=KnowledgeSearchError("all routes failed"))
    provider = _FakeProvider()
    repo = _repo([_case("q1", "如何回滚部署", expect_any=["回滚"])])

    runner = KnowledgeEvalRunner(
        repository=repo, knowledge_service=service, provider=provider
    )
    await runner.run("run-1")

    assert len(repo.results) == 1
    assert repo.results[0]["status"] == "failed"
    assert repo.results[0]["error"]
    # failed 不进聚合：没有可聚合的分组。
    assert repo.finish_calls[-1]["metrics"] == {}


async def test_no_scope_marks_case_not_assessable() -> None:
    """no_scope 是作用域问题，不是「没搜到」→ not_assessable，不补 0。"""
    service = _FakeService({"keyword": _FakeResult((), reason="no_scope")})
    provider = _FakeProvider()
    repo = _repo([_case("q1", "如何回滚部署", expect_any=["回滚"])])

    runner = KnowledgeEvalRunner(
        repository=repo, knowledge_service=service, provider=provider
    )
    await runner.run("run-1")

    assert len(repo.results) == 1
    row = repo.results[0]
    assert row["status"] == "not_assessable"
    assert row["error"] == "no_scope"
    assert row["metrics"]["precision_at_k"] is None


async def test_judge_failure_marks_case_not_assessable() -> None:
    """判定失败 → 该题 not_assessable，error 记原始原因。"""

    class _BrokenProvider:
        def get_default_model(self) -> str:
            return "broken"

        async def chat_with_retry(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("provider 挂了")

    service = _FakeService({"keyword": _FakeResult((_FakeHit("c1", "正文"),))})
    repo = _repo([_case("q1", "如何回滚部署")])

    runner = KnowledgeEvalRunner(
        repository=repo, knowledge_service=service, provider=_BrokenProvider()
    )
    await runner.run("run-1")

    assert len(repo.results) == 1
    row = repo.results[0]
    assert row["status"] == "not_assessable"
    assert "provider 挂了" in (row["error"] or "")


async def test_budget_exceeded_fails_run_without_partial_metrics() -> None:
    """判定调用预算耗尽 → 运行 failed，metrics 为空（不产出半截指标）。"""
    service = _FakeService({"keyword": _FakeResult((_FakeHit("c1", "正文"),))})
    provider = _FakeProvider({"c1": 3})
    repo = _repo(
        [_case("q1", "问题一"), _case("q2", "问题二")],
        budgets={"max_queries": 10, "max_judge_calls": 1, "concurrency": 1},
    )

    runner = KnowledgeEvalRunner(
        repository=repo, knowledge_service=service, provider=provider
    )
    await runner.run("run-1")

    assert len(provider.calls) == 1, "预算耗尽后不得再调用 LLM"
    last = repo.finish_calls[-1]
    assert last["status"] == "failed"
    assert last["metrics"] == {}
    assert (last["error"] or "").startswith("KNOWLEDGE_BUDGET_EXCEEDED")


async def test_query_budget_exceeded_fails_run_before_any_work() -> None:
    service = _FakeService({"keyword": _FakeResult((_FakeHit("c1", "正文"),))})
    provider = _FakeProvider({"c1": 3})
    repo = _repo(
        [_case("q1", "问题一"), _case("q2", "问题二")],
        budgets={"max_queries": 1, "max_judge_calls": 10, "concurrency": 1},
    )

    runner = KnowledgeEvalRunner(
        repository=repo, knowledge_service=service, provider=provider
    )
    await runner.run("run-1")

    assert repo.results == []
    assert provider.calls == []
    last = repo.finish_calls[-1]
    assert last["status"] == "failed"
    assert last["metrics"] == {}
    assert (last["error"] or "").startswith("KNOWLEDGE_BUDGET_EXCEEDED")


async def test_no_candidates_config_is_scored_zero_not_assessable() -> None:
    """no_candidates 是「真的没搜到」→ 照常打分记 0 分，不是不可评。"""
    service = _FakeService(
        {
            "keyword": _FakeResult((), reason="no_candidates"),
            "hybrid": _FakeResult((_FakeHit("c1", "回滚用 rollout undo"),)),
        }
    )
    provider = _FakeProvider()
    repo = _repo(
        [_case("q1", "如何回滚部署", expect_any=["回滚"])],
        configs=[
            {"id": "empty", "name": "没搜到", "overrides": {"mode": "keyword", "limit": 5}},
            {"id": "base", "name": "基线", "overrides": {"mode": "hybrid", "limit": 5}},
        ],
    )

    runner = KnowledgeEvalRunner(
        repository=repo, knowledge_service=service, provider=provider
    )
    await runner.run("run-1")

    by_case = {row["case_id"]: row for row in repo.results}
    empty_row = by_case["q1::empty"]
    base_row = by_case["q1::base"]
    assert empty_row["status"] == "scored"
    assert empty_row["metrics"]["precision_at_k"] == pytest.approx(0.0)
    assert empty_row["metrics"]["recall_at_k"] == pytest.approx(0.0)
    assert base_row["status"] == "scored"
    assert base_row["metrics"]["precision_at_k"] == pytest.approx(1 / 5)


async def test_empty_pool_is_not_assessable_because_pool_has_no_relevant() -> None:
    """整池无候选 → 池里没有相关块 → 不可评（缺证据，不补 0）。"""
    service = _FakeService({"keyword": _FakeResult((), reason="no_candidates")})
    provider = _FakeProvider()
    repo = _repo([_case("q1", "如何回滚部署", expect_any=["回滚"])])

    runner = KnowledgeEvalRunner(
        repository=repo, knowledge_service=service, provider=provider
    )
    await runner.run("run-1")

    row = repo.results[0]
    assert row["status"] == "not_assessable"
    assert row["metrics"]["reason"] == "POOL_HAS_NO_RELEVANT"
    # recall 无分母 → 缺证据是 None；precision 是算出来的 0（0/k），不是补的。
    assert row["metrics"]["recall_at_k"] is None
    assert row["metrics"]["precision_at_k"] == pytest.approx(0.0)


async def test_cancel_stops_processing_subsequent_queries() -> None:
    """取消是协作式：在查询边界停；已完成的逐题结果保留。"""
    service = _FakeService({"keyword": _FakeResult((_FakeHit("c1", "正文"),))})
    provider = _FakeProvider({"c1": 3})
    repo = _repo(
        [_case("q1", "问题一"), _case("q2", "问题二")],
        budgets={"concurrency": 1},
    )

    runner = KnowledgeEvalRunner(
        repository=repo, knowledge_service=service, provider=provider
    )
    await runner.cancel("run-1")
    await runner.run("run-1")

    assert repo.results == [], "取消后不应再处理查询"
    assert repo.finish_calls[-1]["status"] == "cancelled"


async def test_cancel_mid_run_keeps_finished_results_and_stops_the_rest() -> None:
    """运行中取消：第一题的结果保留，后续查询不再处理。"""
    started = threading.Event()
    release = threading.Event()

    class _BlockingService(_FakeService):
        def handle(self, query: str, **kwargs: Any) -> _FakeResult:
            result = super().handle(query, **kwargs)
            started.set()
            release.wait(5)
            return result

    service = _BlockingService({"keyword": _FakeResult((_FakeHit("c1", "正文"),))})
    provider = _FakeProvider({"c1": 3})
    repo = _repo(
        [_case("q1", "问题一"), _case("q2", "问题二")],
        budgets={"concurrency": 1},
    )

    runner = KnowledgeEvalRunner(
        repository=repo, knowledge_service=service, provider=provider
    )
    task = asyncio.create_task(runner.run("run-1"))
    await asyncio.to_thread(started.wait, 5)
    await runner.cancel("run-1")
    release.set()
    await task

    assert [row["case_id"] for row in repo.results] == ["q1::base"]
    assert repo.finish_calls[-1]["status"] == "cancelled"


async def test_saved_metrics_carry_reason_and_metric_status() -> None:
    """落库的 metrics 必须带 reason 与 metric_status（scored 由二者派生）。"""
    service = _FakeService(
        {"keyword": _FakeResult((_FakeHit("c1", "回滚用 rollout undo"),))}
    )
    provider = _FakeProvider()
    repo = _repo([_case("q1", "如何回滚部署", expect_any=["回滚"])])

    runner = KnowledgeEvalRunner(
        repository=repo, knowledge_service=service, provider=provider
    )
    await runner.run("run-1")

    metrics = repo.results[0]["metrics"]
    assert "reason" in metrics
    assert "metric_status" in metrics
    assert metrics["reason"] == ""
    assert metrics["metric_status"]["precision_at_k"] == "scored"
    derived = KnowledgeEvalCaseMetrics.model_validate(metrics)
    assert derived.scored is True


async def test_not_assessable_row_derives_scored_false() -> None:
    service = _FakeService({"keyword": _FakeResult((), reason="filtered")})
    provider = _FakeProvider()
    repo = _repo([_case("q1", "如何回滚部署", expect_any=["回滚"])])

    runner = KnowledgeEvalRunner(
        repository=repo, knowledge_service=service, provider=provider
    )
    await runner.run("run-1")

    metrics = repo.results[0]["metrics"]
    derived = KnowledgeEvalCaseMetrics.model_validate(metrics)
    assert derived.scored is False


async def test_mixed_label_sources_are_aggregated_separately() -> None:
    """expect 与 judge 同库同跑时，聚合**分组不混算**。"""
    service = _FakeService(
        {"keyword": _FakeResult((_FakeHit("c1", "回滚用 rollout undo"),))}
    )
    provider = _FakeProvider({"c1": 3})
    repo = _repo(
        [
            _case("q1", "如何回滚部署", expect_any=["回滚"]),
            _case("q2", "如何备份数据"),
        ]
    )

    runner = KnowledgeEvalRunner(
        repository=repo, knowledge_service=service, provider=provider
    )
    await runner.run("run-1")

    groups = repo.finish_calls[-1]["metrics"]
    assert set(groups) == {"expect", "judge"}
    assert groups["expect"]["total_queries"] == 1
    assert groups["judge"]["total_queries"] == 1
    assert groups["expect"]["metrics"]["precision_at_k"] == pytest.approx(1 / 5)
    assert groups["judge"]["metrics"]["precision_at_k"] == pytest.approx(1 / 5)


async def test_degraded_marker_is_persisted_on_run(tmp_path: Any) -> None:
    """降级必须落到 run 的字段上：降级运行与正常运行的指标不得直接对比。"""
    db_path = tmp_path / "eval.db"
    repository = KnowledgeEvalRepository(db_path)
    repository.init_schema()
    dataset = repository.create_dataset(
        "集",
        cases=[_case("q1", "如何回滚部署", expect_any=["回滚"])],
    )
    run = repository.create_run(
        dataset["id"],
        configs=[{"id": "base", "name": "基线", "overrides": {"mode": "keyword", "limit": 5}}],
        budgets={"concurrency": 1},
    )

    service = _FakeService(
        {
            "keyword": _FakeResult(
                (_FakeHit("c1", "回滚用 rollout undo"),),
                degraded=("vector_unavailable",),
            )
        }
    )
    runner = KnowledgeEvalRunner(
        repository=repository, knowledge_service=service, provider=_FakeProvider()
    )
    await runner.run(run["id"])

    stored = repository.get_run(run["id"])
    assert stored is not None
    assert "vector_unavailable" in stored["pool"]["degraded"]
    report = repository.get_run_report(run["id"])
    assert report["results"], "逐题结果必须落库"
    assert report["results"][0]["case_id"] == "q1::base"
    repository.close()
