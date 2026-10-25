"""观测读模型路由契约：鉴权、参数校验、返回形状与报告下载。

这些接口是页面与离线报告共同的取数入口：形状必须稳定，口径缺失必须显式降级，
不能让前端把「没采到」当成「零」。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.server import API_PREFIX

from .http_app import build_app
from .http_app import login as _login
from .http_app import run_request as _run_request


MISSING = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def repository(evaluation_repository: EvaluationRepository) -> EvaluationRepository:
    return evaluation_repository


@pytest.fixture
async def client(repository: EvaluationRepository, tmp_path: Path) -> Any:
    async with TestClient(
        TestServer(build_app(repository, tmp_path / "svc"), host="127.0.0.1")
    ) as test_client:
        yield test_client


async def _create_run(client: TestClient, repository: EvaluationRepository) -> tuple[str, str]:
    await _login(client)
    run = repository.create_run(
        scope="local-user",
        idempotency_key="obs-routes-1",
        request=_run_request(repository),
        agent={"id": "agent-1", "provider": "controlled", "model": "eval-model"},
        judge=None,
        rules_version="rules-v1",
        grader_version="grader-v1",
    )
    return str(run["id"]), str(run["attempts"][0]["id"])


@pytest.mark.parametrize(
    "path",
    [
        f"/attempts/{MISSING}/execution",
        f"/attempts/{MISSING}/tool-calls",
        f"/runs/{MISSING}/usage",
        f"/runs/{MISSING}/report",
    ],
)
async def test_observability_routes_require_login(client: TestClient, path: str) -> None:
    response = await client.get(f"{API_PREFIX}{path}")
    assert response.status == 401
    assert (await response.json())["error"]["code"] == "AUTH_REQUIRED"


async def test_execution_route_returns_nodes_and_coverage(
    client: TestClient, repository: EvaluationRepository
) -> None:
    await _login(client)
    run_id, attempt_id = await _create_run(client, repository)

    response = await client.get(f"{API_PREFIX}/attempts/{attempt_id}/execution")
    assert response.status == 200
    payload = await response.json()
    assert payload["data"]["nodes"] == []
    assert payload["data"]["edges"] == []
    assert payload["next_after_seq"] == 0
    assert payload["has_more"] is False
    assert payload["sealed"] is False
    assert payload["coverage"]["identity"] == "unavailable"
    assert payload["coverage"]["trace"] == "partial"


async def test_tool_calls_route_returns_empty_page_for_unstarted_attempt(
    client: TestClient, repository: EvaluationRepository
) -> None:
    await _login(client)
    _run_id, attempt_id = await _create_run(client, repository)

    response = await client.get(f"{API_PREFIX}/attempts/{attempt_id}/tool-calls")
    assert response.status == 200
    payload = await response.json()
    assert payload["data"] == []
    assert payload["total_count"] == 0
    assert payload["next_after_key"] is None


async def test_run_usage_route_returns_totals_for_the_whole_run(
    client: TestClient, repository: EvaluationRepository
) -> None:
    await _login(client)
    run_id, _attempt_id = await _create_run(client, repository)

    response = await client.get(f"{API_PREFIX}/runs/{run_id}/usage")
    assert response.status == 200
    payload = await response.json()
    assert payload["data"] == []
    # 没有采集到任何物理请求时是「未知」，不是零次请求。
    assert payload["totals"]["physical_requests"] == 0
    assert payload["totals"]["total_tokens"] is None
    assert payload["coverage"]["usage"] == "unavailable"


@pytest.mark.parametrize(
    "query",
    ["after_seq=-1", "limit=0", "limit=1001", "after_seq=abc"],
)
async def test_execution_query_parameters_are_validated(
    client: TestClient, repository: EvaluationRepository, query: str
) -> None:
    await _login(client)
    _run_id, attempt_id = await _create_run(client, repository)

    response = await client.get(f"{API_PREFIX}/attempts/{attempt_id}/execution?{query}")
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=201", "limit=abc", "status=weird"],
)
async def test_tool_calls_query_parameters_are_validated(
    client: TestClient, repository: EvaluationRepository, query: str
) -> None:
    await _login(client)
    _run_id, attempt_id = await _create_run(client, repository)

    response = await client.get(f"{API_PREFIX}/attempts/{attempt_id}/tool-calls?{query}")
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("query", ["group_by=unknown", "limit=0", "limit=201"])
async def test_run_usage_query_parameters_are_validated(
    client: TestClient, repository: EvaluationRepository, query: str
) -> None:
    await _login(client)
    run_id, _attempt_id = await _create_run(client, repository)

    response = await client.get(f"{API_PREFIX}/runs/{run_id}/usage?{query}")
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "VALIDATION_ERROR"


async def test_report_route_returns_a_self_contained_html_attachment(
    client: TestClient, repository: EvaluationRepository
) -> None:
    await _login(client)
    run_id, _attempt_id = await _create_run(client, repository)

    response = await client.get(f"{API_PREFIX}/runs/{run_id}/report")
    assert response.status == 200
    assert response.content_type == "text/html"
    disposition = response.headers["Content-Disposition"]
    assert disposition.startswith("attachment;")
    assert run_id in disposition
    body = await response.text()
    assert "<!doctype html" in body.lower()
    assert "<script" not in body.lower()


async def test_report_route_reports_missing_run_as_not_found(client: TestClient) -> None:
    await _login(client)
    response = await client.get(f"{API_PREFIX}/runs/{MISSING}/report")
    assert response.status == 404
    assert (await response.json())["error"]["code"] in {"RECORD_NOT_FOUND", "RUN_NOT_FOUND"}
