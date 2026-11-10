"""知识评测 HTTP 层（契约 §2 / §3.2，WU-08）。

这份文件锁的是**边界契约**：

- 挂载：知识路由必须出现在 ``server._routes()`` 这份共享清单里，不能另起一套
  前缀相同但不受准入保护的路由表；
- 形状：路径统一在 ``/api/eval/v1/knowledge`` 之下，复数名词、无动词后缀；
- 真实行为：13 条路由不再是占位——入队即 202、检索参数白名单、乐观锁冲突、
  资源 404、报告的「聚合段 + 逐题段」两段缺一不可。

报告用例的数据**直接经仓储落库**：知识域跑一次真实检索需要索引与向量后端，
HTTP 层要验的是「投影形状」，不是检索质量（那是 runner 与 metrics 的职责）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from nanobot.evaluation.knowledge.metrics import METRIC_IDS, aggregate_metrics, case_score
from nanobot.evaluation.knowledge.routes import (
    KNOWLEDGE_API_PREFIX,
    knowledge_route_defs,
)
from nanobot.evaluation.server import API_PREFIX, _routes

PREFIX = "/api/eval/v1"
PASSPHRASE = "correct-horse-battery-staple"

#: 契约 §2 的知识路由矩阵（方法 + 路径）。Task 8 换真 handler 时这份清单不能变。
EXPECTED_ROUTES: set[tuple[str, str]] = {
    ("GET", "/knowledge/capabilities"),
    ("GET", "/knowledge/metrics"),
    ("GET", "/knowledge/datasets"),
    ("POST", "/knowledge/datasets"),
    ("GET", "/knowledge/datasets/{id}"),
    ("PATCH", "/knowledge/datasets/{id}"),
    ("DELETE", "/knowledge/datasets/{id}"),
    ("POST", "/knowledge/runs"),
    ("GET", "/knowledge/runs"),
    ("GET", "/knowledge/runs/{id}"),
    ("GET", "/knowledge/runs/{id}/report"),
    ("POST", "/knowledge/runs/{id}/cancel"),
    ("GET", "/knowledge/compare"),
}

#: 动词后缀黑名单：资源路由用复数名词，动作语义交给 HTTP 方法。
VERB_SUFFIXES = ("/create", "/delete", "/update", "/list", "/fetch", "/remove")


# --------------------------------------------------------------------------- 挂载


def test_knowledge_routes_are_mounted_in_shared_route_table() -> None:
    """知识路由必须在共享清单里——guard/CSRF/限流/405 兜底都从这份清单推导。"""
    shared = {(route.method, route.path) for route in _routes()}
    knowledge = {(route.method, route.path) for route in knowledge_route_defs(PREFIX)}

    assert knowledge, "知识路由清单不能为空"
    missing = knowledge - shared
    assert not missing, f"未挂进共享清单：{sorted(missing)}"


def test_knowledge_route_paths_match_contract() -> None:
    """契约 §2 的路由矩阵：方法与路径一一对应，不多不少。"""
    actual = {(route.method, route.path) for route in knowledge_route_defs(PREFIX)}
    expected = {(method, f"{PREFIX}{path}") for method, path in EXPECTED_ROUTES}
    assert actual == expected


def test_knowledge_routes_use_plural_nouns_without_verbs() -> None:
    for route in knowledge_route_defs(PREFIX):
        assert route.path.startswith(f"{PREFIX}/knowledge"), route.path
        for suffix in VERB_SUFFIXES:
            assert not route.path.endswith(suffix), route.path


def test_knowledge_default_prefix_matches_shared_api_prefix() -> None:
    """默认前缀必须与评测平台既有 API 前缀一致，否则会落到别的路由树。"""
    assert KNOWLEDGE_API_PREFIX == API_PREFIX == PREFIX


def test_knowledge_routes_do_not_shadow_existing_routes() -> None:
    """同一 (method, path) 只能挂一次；重复会让 aiohttp 静默选第一条。"""
    knowledge = {(route.method, route.path) for route in knowledge_route_defs(PREFIX)}
    shared = [(route.method, route.path) for route in _routes()]
    duplicated = {key for key in shared if key in knowledge and shared.count(key) > 1}
    assert not duplicated, f"路由重复挂载：{sorted(duplicated)}"


def test_knowledge_handlers_are_real_implementations() -> None:
    """占位 handler 必须已被真实实现取代（否则前端会把「没实现」当成「跑完了」）。"""
    for route in knowledge_route_defs(PREFIX):
        handler = route.handler
        assert callable(handler), route.path
        assert getattr(handler, "__name__", "") != "_placeholder", route.path
        assert getattr(handler, "__module__", "") == (
            "nanobot.evaluation.knowledge.routes"
        ), route.path


# --------------------------------------------------------------------------- 装配


@pytest.fixture
def app(tmp_path: Path) -> Any:
    """真应用（起调度器生命周期）；装配写在本文件里，不改共享 conftest。"""
    from nanobot.evaluation.auth import AuthConfig
    from nanobot.evaluation.database import EvaluationDatabase
    from nanobot.evaluation.repository import EvaluationRepository
    from nanobot.evaluation.scheduler import SchedulerConfig
    from nanobot.evaluation.server import create_app
    from nanobot.evaluation.services import EvaluationService

    root = tmp_path / "evaluation"
    root.mkdir(parents=True, exist_ok=True)
    database = EvaluationDatabase(root / "evaluation.db")
    database.init_schema()
    repository = EvaluationRepository(database)
    service = EvaluationService(repository=repository, data_dir=root, profiles=())
    return create_app(
        repository=repository,
        service=service,
        auth_config=AuthConfig(
            passphrase=PASSPHRASE,
            allowed_hosts=frozenset({"127.0.0.1", "localhost"}),
            allowed_origins=frozenset(),
        ),
        scheduler_config=SchedulerConfig(
            data_dir=root, concurrency=1, poll_interval_s=0.05
        ),
        profiles=(),
    )


@pytest.fixture
async def client(app: Any) -> Any:
    async with TestClient(TestServer(app, host="127.0.0.1")) as test_client:
        yield test_client


async def _login(client: TestClient) -> str:
    response = await client.post(
        f"{PREFIX}/auth/login", json={"passphrase": PASSPHRASE}
    )
    assert response.status == 200
    return str((await response.json())["csrf_token"])


def _service(client: TestClient) -> Any:
    """取服务实例（测试要直接经仓储落库造数据）。"""
    return client.app["eval_context"].service


async def _create_dataset(
    client: TestClient, csrf: str, *, cases: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    response = await client.post(
        f"{PREFIX}/knowledge/datasets",
        json={
            "name": "检索基线",
            "notes": "WU-08 测试集",
            "cases": cases
            if cases is not None
            else [
                {"id": "q1", "query": "如何回滚部署", "expect_any": ["回滚"]},
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status == 201, await response.text()
    return dict(await response.json())


def _seed_run(
    service: Any,
    *,
    dataset_id: str,
    case_id: str = "q1",
    config_id: str = "base",
    variant: str = "base",
) -> str:
    """经仓储造一次**已完成**的运行（含逐题结果与聚合指标）。

    ``variant="base"`` 全召回；``variant="partial"`` 池里多一块相关块但没进 top-k
    ——召回率因此掉下来（池相对召回的语义），用来验证退化清单。
    """
    repository = service.knowledge_repository
    run = repository.create_run(
        dataset_id=dataset_id,
        configs=[{"id": config_id, "name": "基线", "overrides": {}}],
        judge_config={
            "profile_id": "",
            "model": "",
            "provider_source": "unavailable",
            "available": False,
        },
        budgets={
            "max_queries": 200,
            "max_judge_calls": 500,
            "per_query_timeout_s": 60.0,
            "concurrency": 4,
        },
    )
    composite = f"{case_id}::{config_id}"
    if variant == "base":
        score = case_score(
            case_id=composite,
            label_source="expect",
            top_k=[("chunk-1", 3)],
            pool_grades={"chunk-1": 3},
            k=8,
        )
        hits = [
            {
                "chunk_id": "chunk-1",
                "rank": 1,
                "score": 0.9,
                "content_hash": "hash-1",
                "grade": 3,
                "reason": "命中关键词",
            }
        ]
    else:
        score = case_score(
            case_id=composite,
            label_source="expect",
            top_k=[("chunk-2", 2)],
            pool_grades={"chunk-2": 2, "chunk-3": 3},
            k=8,
        )
        hits = [
            {
                "chunk_id": "chunk-2",
                "rank": 1,
                "score": 0.5,
                "content_hash": "hash-2",
                "grade": 2,
                "reason": "命中关键词",
            }
        ]
    repository.save_result(
        run_id=str(run["id"]),
        case_id=composite,
        status=score.status,
        label_source="expect",
        hits=hits,
        metrics={
            **score.metrics,
            "reason": score.reason,
            "metric_status": score.metric_status,
        },
        error=score.reason or None,
    )
    repository.finish_run(
        str(run["id"]), status="completed", metrics=aggregate_metrics([score])
    )
    return str(run["id"])


# --------------------------------------------------------------------------- 准入与兜底


async def test_knowledge_read_route_requires_login(client: TestClient) -> None:
    """知识路由必须继承共享清单的准入：未登录不给任何内容。"""
    response = await client.get(f"{PREFIX}/knowledge/capabilities")
    assert response.status == 401
    assert (await response.json())["error"]["code"] == "AUTH_REQUIRED"


async def test_knowledge_route_inherits_405_fallback(client: TestClient) -> None:
    """未匹配方法走共享清单推出的 405 兜底（说明路由真的进了那份清单）。"""
    csrf = await _login(client)
    response = await client.put(
        f"{PREFIX}/knowledge/datasets",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status == 405
    assert (await response.json())["error"]["code"] == "METHOD_NOT_ALLOWED"


async def test_knowledge_error_envelope_shape(client: TestClient) -> None:
    """错误信封与既有评测错误形状一致：``{"error": {"code", "message"}}``。"""
    await _login(client)
    response = await client.get(f"{PREFIX}/knowledge/datasets/missing-dataset")
    assert response.status == 404
    body = await response.json()
    assert body["error"]["code"] == "KNOWLEDGE_DATASET_NOT_FOUND"
    assert body["error"]["message"]


# --------------------------------------------------------------------------- 能力与指标口径


async def test_capabilities_without_config_is_disabled(client: TestClient) -> None:
    """没有 config → 知识评测不可用，但**要能渲染出原因**（200 + enabled=false）。"""
    await _login(client)
    response = await client.get(f"{PREFIX}/knowledge/capabilities")
    assert response.status == 200
    body = await response.json()
    assert body["enabled"] is False
    assert body["reason"] == "NO_CONFIG"
    assert body["kbs"] == []


async def test_metrics_endpoint_returns_all_formulas(client: TestClient) -> None:
    """指标口径由后端单点下发：7 个指标，每个都要有非空公式串。"""
    await _login(client)
    response = await client.get(f"{PREFIX}/knowledge/metrics")
    assert response.status == 200
    body = await response.json()
    metrics = body["metrics"]
    assert len(metrics) == 7
    assert {item["metricId"] for item in metrics} == set(METRIC_IDS)
    for item in metrics:
        assert item["formula"].strip(), item["metricId"]
        assert item["label"].strip(), item["metricId"]
        assert item["direction"] in {"higher_is_better", "lower_is_better"}
        assert item["description"].strip(), item["metricId"]


# --------------------------------------------------------------------------- 数据集


async def test_dataset_crud_round_trip(client: TestClient) -> None:
    csrf = await _login(client)
    dataset = await _create_dataset(client, csrf)
    assert dataset["id"]
    assert dataset["revision"] == 1

    listed = await client.get(f"{PREFIX}/knowledge/datasets")
    assert listed.status == 200
    assert [item["id"] for item in (await listed.json())["data"]] == [dataset["id"]]

    detail = await client.get(f"{PREFIX}/knowledge/datasets/{dataset['id']}")
    assert detail.status == 200
    assert (await detail.json())["name"] == "检索基线"

    patched = await client.patch(
        f"{PREFIX}/knowledge/datasets/{dataset['id']}",
        json={"base_revision": "1", "notes": "改过的备注"},
        headers={"X-CSRF-Token": csrf},
    )
    assert patched.status == 200
    assert (await patched.json())["revision"] == 2

    deleted = await client.delete(
        f"{PREFIX}/knowledge/datasets/{dataset['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status == 200
    # 幂等：再删一次仍然成功。
    again = await client.delete(
        f"{PREFIX}/knowledge/datasets/{dataset['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert again.status == 200


async def test_patch_with_stale_base_revision_conflicts(client: TestClient) -> None:
    """乐观锁：``base_revision`` 过期 → 409，不静默覆盖别人的编辑。"""
    csrf = await _login(client)
    dataset = await _create_dataset(client, csrf)
    first = await client.patch(
        f"{PREFIX}/knowledge/datasets/{dataset['id']}",
        json={"base_revision": "1", "notes": "第一版"},
        headers={"X-CSRF-Token": csrf},
    )
    assert first.status == 200
    stale = await client.patch(
        f"{PREFIX}/knowledge/datasets/{dataset['id']}",
        json={"base_revision": "1", "notes": "第二版"},
        headers={"X-CSRF-Token": csrf},
    )
    assert stale.status == 409
    assert (await stale.json())["error"]["code"] == (
        "KNOWLEDGE_DATASET_REVISION_CONFLICT"
    )


# --------------------------------------------------------------------------- 运行


async def test_create_run_returns_202_without_executing(client: TestClient) -> None:
    """入队即 202：请求内不检索，响应体给出 run id 与 queued 状态。"""
    csrf = await _login(client)
    dataset = await _create_dataset(client, csrf)
    response = await client.post(
        f"{PREFIX}/knowledge/runs",
        json={
            "dataset_id": dataset["id"],
            "configs": [
                {"id": "base", "name": "基线", "overrides": {"mode": "keyword", "limit": 5}}
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status == 202
    body = await response.json()
    assert body["id"]
    assert body["status"] == "queued"
    assert response.headers["Location"].endswith(f"/knowledge/runs/{body['id']}")


async def test_unknown_retrieval_field_is_rejected(client: TestClient) -> None:
    """检索参数白名单外的字段 → 422 KNOWLEDGE_CONFIG_UNSUPPORTED（不静默忽略）。"""
    csrf = await _login(client)
    dataset = await _create_dataset(client, csrf)
    response = await client.post(
        f"{PREFIX}/knowledge/runs",
        json={
            "dataset_id": dataset["id"],
            "configs": [{"id": "base", "overrides": {"tool_output_chars": 1}}],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "KNOWLEDGE_CONFIG_UNSUPPORTED"


async def test_missing_dataset_returns_404(client: TestClient) -> None:
    csrf = await _login(client)
    response = await client.post(
        f"{PREFIX}/knowledge/runs",
        json={"dataset_id": "nope", "configs": [{"id": "base"}]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status == 404
    assert (await response.json())["error"]["code"] == "KNOWLEDGE_DATASET_NOT_FOUND"


async def test_missing_run_returns_404(client: TestClient) -> None:
    await _login(client)
    response = await client.get(f"{PREFIX}/knowledge/runs/missing-run")
    assert response.status == 404
    assert (await response.json())["error"]["code"] == "KNOWLEDGE_RUN_NOT_FOUND"


async def test_run_list_can_filter_by_dataset(client: TestClient) -> None:
    csrf = await _login(client)
    dataset = await _create_dataset(client, csrf)
    _seed_run(_service(client), dataset_id=dataset["id"])
    listed = await client.get(f"{PREFIX}/knowledge/runs?dataset_id={dataset['id']}")
    assert listed.status == 200
    data = (await listed.json())["data"]
    assert len(data) == 1
    assert data[0]["dataset_id"] == dataset["id"]
    assert data[0]["status"] == "completed"


async def test_cancel_terminal_run_returns_200(client: TestClient) -> None:
    """终态运行重复取消 → 200（不是 202）。"""
    csrf = await _login(client)
    dataset = await _create_dataset(client, csrf)
    run_id = _seed_run(_service(client), dataset_id=dataset["id"])
    response = await client.post(
        f"{PREFIX}/knowledge/runs/{run_id}/cancel",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status == 200


# --------------------------------------------------------------------------- 报告与对比


async def test_report_returns_both_aggregate_and_per_case(client: TestClient) -> None:
    """每次测评必须同时给出聚合与逐题——只有聚合视为缺失。"""
    csrf = await _login(client)
    dataset = await _create_dataset(client, csrf)
    run_id = _seed_run(_service(client), dataset_id=dataset["id"])
    response = await client.get(f"{PREFIX}/knowledge/runs/{run_id}/report")
    assert response.status == 200
    body = await response.json()
    assert body["metrics"], "缺少聚合段"
    assert body["results"], "缺少逐题段"
    group = body["metrics"]["expect"]
    assert set(group["metrics"]) == set(METRIC_IDS)
    for item in body["results"]:
        assert item["status"]
        for key in ("precision_at_k", "recall_at_k", "hit_at_k", "mrr", "ndcg_at_k"):
            assert key in item["metrics"], f"逐题缺少 {key}"


async def test_compare_returns_metric_deltas_and_regressions(client: TestClient) -> None:
    """对比：逐指标差 + 退化查询清单（候选变差的用例必须列出来）。"""
    csrf = await _login(client)
    dataset = await _create_dataset(client, csrf)
    service = _service(client)
    base_id = _seed_run(service, dataset_id=dataset["id"], variant="base")
    candidate_id = _seed_run(service, dataset_id=dataset["id"], variant="partial")
    response = await client.get(
        f"{PREFIX}/knowledge/compare?base={base_id}&candidate={candidate_id}"
    )
    assert response.status == 200
    body = await response.json()
    assert body["base"]["id"] == base_id
    assert body["candidate"]["id"] == candidate_id
    recall_row = next(
        row
        for row in body["metrics"]
        if row["metricId"] == "recall_at_k" and row["label_source"] == "expect"
    )
    assert recall_row["base"] == 1.0
    assert recall_row["candidate"] == 0.5
    assert recall_row["delta"] == -0.5
    regressions = body["regressions"]
    assert regressions, "缺少退化查询清单"
    assert any(row["metricId"] == "recall_at_k" for row in regressions)
    assert {row["case_id"] for row in regressions} == {"q1"}


async def test_compare_requires_both_run_ids(client: TestClient) -> None:
    await _login(client)
    response = await client.get(f"{PREFIX}/knowledge/compare?base=only-one")
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "VALIDATION_ERROR"


# --------------------------------------------------------------------------- config 贯通


def test_service_context_accepts_optional_config() -> None:
    """``config`` 可选且默认 None：既有调用方行为不变。"""
    import inspect

    from nanobot.evaluation.server import ServiceContext

    signature = inspect.signature(ServiceContext.__init__)
    assert "config" in signature.parameters
    assert signature.parameters["config"].default is None


def test_create_app_and_create_eval_app_accept_optional_config() -> None:
    """两个装配入口都要能透传 config，且默认 None。"""
    import inspect

    from nanobot.evaluation.server import create_app, create_eval_app

    for factory in (create_app, create_eval_app):
        parameter = inspect.signature(factory).parameters["config"]
        assert parameter.default is None, factory.__name__


def test_create_eval_app_threads_config_into_service_context(tmp_path: Any) -> None:
    """``create_eval_app(config=...)`` 之后 config 挂在 ServiceContext 上。"""
    from nanobot.evaluation.server import create_eval_app

    sentinel = object()
    app = create_eval_app(
        data_dir=tmp_path / "evaluation",
        passphrase=PASSPHRASE,
        config=sentinel,
    )
    context = app["eval_context"]
    assert context.config is sentinel


def test_create_eval_app_defaults_config_to_none(tmp_path: Any) -> None:
    """默认不传 config 时为 None——既有调用方不受影响。"""
    from nanobot.evaluation.server import create_eval_app

    app = create_eval_app(
        data_dir=tmp_path / "evaluation",
        passphrase=PASSPHRASE,
    )
    context = app["eval_context"]
    assert context.config is None


# --------------------------------------------------------------------------- 错误码登记


def test_knowledge_error_codes_are_registered() -> None:
    """知识专项的错误码必须在白名单里——否则 404/409 会退化成 500。"""
    from nanobot.evaluation.errors import ERROR_STATUS, code_from_exception, status_for
    from nanobot.evaluation.knowledge.repository import KnowledgeEvalError

    expected = {
        "KNOWLEDGE_DATASET_NOT_FOUND": 404,
        "KNOWLEDGE_DATASET_REVISION_CONFLICT": 409,
        "KNOWLEDGE_DATASET_NOT_EMPTY": 422,
        "KNOWLEDGE_RUN_NOT_FOUND": 404,
        "KNOWLEDGE_CONFIG_UNSUPPORTED": 422,
        "KNOWLEDGE_CAPABILITY_REQUIRED": 422,
        "KNOWLEDGE_CONFIG_UNAVAILABLE": 503,
        "KNOWLEDGE_BUDGET_EXCEEDED": 422,
    }
    for code, status in expected.items():
        assert ERROR_STATUS.get(code) == status, code
        assert status_for(code) == status, code
    for code in ("KNOWLEDGE_DATASET_NOT_FOUND", "KNOWLEDGE_DATASET_REVISION_CONFLICT",
                 "KNOWLEDGE_RUN_NOT_FOUND"):
        mapped = code_from_exception(KnowledgeEvalError(code, "x"))
        assert mapped == code
        assert status_for(mapped) != 500, code
