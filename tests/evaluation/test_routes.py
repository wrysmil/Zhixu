"""T10 路由契约：状态码、错误体形状与读接口不变量。

与 test_server.py 的分工：那份文件锁准入（登录/CSRF/Host/Origin/幂等），这里锁
「路由表本身的行为」——未匹配路径、错误方法、超大请求体、读接口返回形状，
以及服务层错误码能不能真的映射成契约状态码（退化成 500 就是缺陷）。

生命周期结果不在这里断言：应用一启动调度器就会接手 queued run，任何「它还在
排队」的断言都会和子进程抢时间；那种语义交给 test_scheduler.py 与 test_services.py。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from aiohttp.test_utils import TestClient, TestServer

from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.server import API_PREFIX

from .conftest import make_case_payload, make_suite_payload
from .http_app import (
    build_app,
)
from .http_app import (
    csrf_headers as _headers,
)
from .http_app import (
    login as _login,
)
from .http_app import (
    run_request as _run_request,
)


@pytest.fixture
def repository(evaluation_repository: EvaluationRepository) -> EvaluationRepository:
    return evaluation_repository


@pytest.fixture
async def client(repository: EvaluationRepository, tmp_path: Path) -> Any:
    async with TestClient(
        TestServer(build_app(repository, tmp_path / "svc"), host="127.0.0.1")
    ) as test_client:
        yield test_client


#: 造一个不存在的 uuid，专门验证「读不存在资源」的状态码。
MISSING = "00000000-0000-4000-8000-000000000000"

READ_PATHS = [
    "/suites",
    "/cases",
    "/profiles",
    "/runs",
    "/overview",
    "/calibration-samples",
    "/auth/session",
    f"/runs/{MISSING}",
    f"/runs/{MISSING}/attempts",
    f"/attempts/{MISSING}",
    f"/attempts/{MISSING}/trace",
    f"/runs/{MISSING}/export",
    f"/calibrations/{MISSING}",
    f"/artifacts/{MISSING}",
]


# ------------------------------------------------------------------ 未登录


@pytest.mark.parametrize("path", READ_PATHS)
async def test_every_read_route_requires_login(client: TestClient, path: str) -> None:
    response = await client.get(f"{API_PREFIX}{path}")
    assert response.status == 401
    assert (await response.json())["error"]["code"] == "AUTH_REQUIRED"


# ---------------------------------------------------------------- 路由与错误体


async def test_unknown_path_is_json_404(client: TestClient) -> None:
    await _login(client)
    response = await client.get(f"{API_PREFIX}/no-such-endpoint")
    assert response.status == 404
    body = await response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert set(body) == {"error"}


async def test_unknown_path_does_not_leak_routes_to_anonymous_callers(
    client: TestClient,
) -> None:
    """未登录时未知路径与未知资源都答 401：不让探测者用状态码枚举端点。"""
    response = await client.get(f"{API_PREFIX}/no-such-endpoint")
    assert response.status == 401


async def test_wrong_method_on_known_path_is_json_405(client: TestClient) -> None:
    csrf = await _login(client)
    response = await client.delete(
        f"{API_PREFIX}/runs", headers=_headers(csrf)
    )
    assert response.status == 405
    assert (await response.json())["error"]["code"] == "METHOD_NOT_ALLOWED"


async def test_oversized_body_is_json_413(client: TestClient) -> None:
    csrf = await _login(client)
    padding = "x" * (6 * 1024 * 1024)
    response = await client.post(
        f"{API_PREFIX}/suites",
        data=f'{{"name": "{padding}"}}',
        headers={**_headers(csrf), "Content-Type": "application/json"},
    )
    assert response.status == 413
    assert (await response.json())["error"]["code"] == "PAYLOAD_TOO_LARGE"


async def test_invalid_body_lists_field_names_without_internals(client: TestClient) -> None:
    csrf = await _login(client)
    response = await client.post(
        f"{API_PREFIX}/suites",
        json={"title": "缺 name 字段"},
        headers=_headers(csrf),
    )
    assert response.status == 422
    body = await response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    # 只回字段级 loc，绝不回 pydantic 的模型堆栈或 Python 类型名。
    assert "Traceback" not in body["error"]["message"]
    assert "model_validate" not in body["error"]["message"]


async def test_service_error_codes_map_to_contract_status(
    client: TestClient, repository: EvaluationRepository
) -> None:
    """服务层抛的带码异常必须落到登记状态码，不能被兜底分支变成 500。"""
    csrf = await _login(client)
    response = await client.post(
        f"{API_PREFIX}/runs",
        json={
            "suite_id": str(uuid4()),
            "case_ids": [str(uuid4())],
            "agent_profile_id": "no-such-profile",
        },
        headers={**_headers(csrf), "Idempotency-Key": "svc-err-1"},
    )
    assert response.status == 404
    assert (await response.json())["error"]["code"] == "PROFILE_NOT_FOUND"


# ------------------------------------------------------------------ 只读形状


async def test_profiles_expose_only_public_fields(client: TestClient) -> None:
    await _login(client)
    response = await client.get(f"{API_PREFIX}/profiles")
    assert response.status == 200
    payload = await response.json()
    assert payload["next_cursor"] is None
    kinds = {row["kind"] for row in payload["data"]}
    assert {"agent", "judge"} <= kinds
    for row in payload["data"]:
        assert set(row) <= {"id", "label", "provider", "model", "kind", "role"}
    raw = str(payload)
    # profile_bundle / 密钥永远不出现在可选模型列表里。
    for forbidden in ("bundle", "api_key", "passphrase", "credentials"):
        assert forbidden not in raw


async def test_overview_on_empty_database(client: TestClient) -> None:
    await _login(client)
    response = await client.get(f"{API_PREFIX}/overview")
    assert response.status == 200
    payload = await response.json()
    assert payload["recent_runs"] == []
    assert payload["status_counts"] == {}
    assert payload["metrics"] is None


async def test_queued_run_reads_are_internally_consistent(
    client: TestClient, repository: EvaluationRepository
) -> None:
    csrf = await _login(client)
    request = _run_request(repository)
    created = await client.post(
        f"{API_PREFIX}/runs",
        json=request.model_dump(mode="json"),
        headers={**_headers(csrf), "Idempotency-Key": "routes-read-1"},
    )
    assert created.status == 202
    run = await created.json()
    run_id = run["id"]
    assert created.headers["Location"] == f"{API_PREFIX}/runs/{run_id}"

    detail = await (await client.get(f"{API_PREFIX}/runs/{run_id}")).json()
    assert detail["id"] == run_id
    assert detail["planned_count"] == len(request.case_ids)
    # 契约 §4：terminal_count 只数执行与评分都终结的项，不可能超过计划数。
    assert 0 <= detail["terminal_count"] <= detail["planned_count"]

    attempts = await (await client.get(f"{API_PREFIX}/runs/{run_id}/attempts")).json()
    assert len(attempts["data"]) == detail["planned_count"]
    assert {item["run_id"] for item in attempts["data"]} == {run_id}

    attempt = attempts["data"][0]
    read = await (await client.get(f"{API_PREFIX}/attempts/{attempt['id']}")).json()
    assert read["case_name"] == attempt["case_name"]
    assert read["execution_status"] == attempt["execution_status"]


async def test_trace_page_of_unstarted_attempt_is_empty_and_unsealed(
    client: TestClient, repository: EvaluationRepository
) -> None:
    await _login(client)
    run = repository.create_run(
        scope="local-user",
        idempotency_key="routes-trace-1",
        request=_run_request(repository),
        agent={"id": "agent-1", "provider": "controlled", "model": "eval-model"},
        judge=None,
        rules_version="rules-v1",
        grader_version="grader-v1",
    )
    attempt_id = str(run["attempts"][0]["id"])
    response = await client.get(f"{API_PREFIX}/attempts/{attempt_id}/trace")
    assert response.status == 200
    payload = await response.json()
    assert payload["data"] == []
    assert payload["next_after_seq"] == 0
    assert payload["has_more"] is False
    # sealed=false 时前端不得把这份 Trace 当最终轨迹展示。
    assert payload["sealed"] is False


@pytest.mark.parametrize(
    "query",
    ["after_seq=-1", "limit=0", "limit=201", "after_seq=abc", "limit=1.5"],
)
async def test_trace_query_parameters_are_validated(
    client: TestClient, repository: EvaluationRepository, query: str
) -> None:
    await _login(client)
    run = repository.create_run(
        scope="local-user",
        idempotency_key=f"routes-trace-bad-{len(query)}",
        request=_run_request(repository),
        agent={"id": "agent-1", "provider": "controlled", "model": "eval-model"},
        judge=None,
        rules_version="rules-v1",
        grader_version="grader-v1",
    )
    attempt_id = str(run["attempts"][0]["id"])
    response = await client.get(f"{API_PREFIX}/attempts/{attempt_id}/trace?{query}")
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "VALIDATION_ERROR"


# -------------------------------------------------------------------- 导出


async def _create_run(client: TestClient, repository: EvaluationRepository) -> str:
    csrf = await _login(client)
    created = await client.post(
        f"{API_PREFIX}/runs",
        json=_run_request(repository).model_dump(mode="json"),
        headers={**_headers(csrf), "Idempotency-Key": "routes-export-1"},
    )
    assert created.status == 202
    return str((await created.json())["id"])


async def test_export_json_and_csv_are_attachments(
    client: TestClient, repository: EvaluationRepository
) -> None:
    run_id = await _create_run(client, repository)
    json_response = await client.get(f"{API_PREFIX}/runs/{run_id}/export?format=json")
    assert json_response.status == 200
    assert "attachment" in json_response.headers["Content-Disposition"]
    assert json_response.headers["X-Content-Type-Options"] == "nosniff"
    assert run_id in json_response.headers["Content-Disposition"]
    assert json_response.headers["Content-Type"].startswith("application/json")

    csv_response = await client.get(f"{API_PREFIX}/runs/{run_id}/export?format=csv")
    assert csv_response.status == 200
    assert csv_response.headers["Content-Type"].startswith("text/csv")
    # CSV 首列必须是表头，导出体不能是空串。
    assert (await csv_response.text()).strip()


async def test_export_rejects_unknown_format(
    client: TestClient, repository: EvaluationRepository
) -> None:
    run_id = await _create_run(client, repository)
    response = await client.get(f"{API_PREFIX}/runs/{run_id}/export?format=pdf")
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "VALIDATION_ERROR"


async def test_export_of_unknown_run_is_404(client: TestClient) -> None:
    await _login(client)
    response = await client.get(f"{API_PREFIX}/runs/{MISSING}/export")
    assert response.status == 404
    assert (await response.json())["error"]["code"] == "RECORD_NOT_FOUND"


# ------------------------------------------------------ 取消、重跑与复核边界


async def test_cancel_of_unknown_run_is_404(client: TestClient) -> None:
    csrf = await _login(client)
    response = await client.post(
        f"{API_PREFIX}/runs/{MISSING}/cancel", json={}, headers=_headers(csrf)
    )
    assert response.status == 404
    assert (await response.json())["error"]["code"] == "RECORD_NOT_FOUND"


async def test_rerun_requires_idempotency_key(
    client: TestClient, repository: EvaluationRepository
) -> None:
    csrf = await _login(client)
    run = _run_request(repository)
    created = await client.post(
        f"{API_PREFIX}/runs",
        json=run.model_dump(mode="json"),
        headers={**_headers(csrf), "Idempotency-Key": "routes-rerun-1"},
    )
    run_id = str((await created.json())["id"])
    response = await client.post(
        f"{API_PREFIX}/runs/{run_id}/rerun",
        json={"attempt_ids": [MISSING]},
        headers=_headers(csrf),
    )
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "VALIDATION_ERROR"


async def test_review_of_unknown_attempt_is_404(client: TestClient) -> None:
    csrf = await _login(client)
    response = await client.post(
        f"{API_PREFIX}/attempts/{MISSING}/reviews",
        json={"verdict": "passed", "comment": "人工复核"},
        headers=_headers(csrf),
    )
    assert response.status == 404
    assert (await response.json())["error"]["code"] in {"RECORD_NOT_FOUND", "ATTEMPT_NOT_FOUND"}


async def test_sample_import_rejects_unknown_sample_id(client: TestClient) -> None:
    csrf = await _login(client)
    response = await client.post(
        f"{API_PREFIX}/samples/import", json={"sample_id": "nope-v9"}, headers=_headers(csrf)
    )
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "VALIDATION_ERROR"


async def test_create_run_rejects_unknown_profile_without_side_effects(
    client: TestClient, repository: EvaluationRepository
) -> None:
    csrf = await _login(client)
    request = _run_request(repository).model_copy(
        update={"agent_profile_id": "no-such-profile"}
    )
    response = await client.post(
        f"{API_PREFIX}/runs",
        json=request.model_dump(mode="json"),
        headers={**_headers(csrf), "Idempotency-Key": "routes-bad-profile-1"},
    )
    assert response.status == 404
    assert (await response.json())["error"]["code"] == "PROFILE_NOT_FOUND"
    # 失败的入队不留 run 行：profile 校验必须在幂等事务之前。
    runs = await (await client.get(f"{API_PREFIX}/runs")).json()
    assert runs["data"] == []
    assert repository.list_runs(limit=10) == []


# ------------------------------------------------------------------ 用例判据编辑


async def test_case_detail_returns_full_criteria(
    client: TestClient, repository: EvaluationRepository
) -> None:
    """判据编辑页要拿到完整定义：里程碑、量表与断言都在。"""
    await _login(client)
    suite = repository.create_suite(make_suite_payload())
    payload = make_case_payload(suite_id=suite["id"])
    payload["evaluation"] = {
        "version": 2,
        "required_dimensions": ["task"],
        "milestones": [{"id": "m1", "weight": 1.0, "assertion_ids": ["a1"]}],
        "required_evidence": ["final_answer", "trace"],
    }
    case = repository.save_case(payload)

    response = await client.get(f"{API_PREFIX}/cases/{case['id']}")
    assert response.status == 200
    body = await response.json()
    assert body["revision"] == 1
    assert body["evaluation"]["milestones"][0]["assertion_ids"] == ["a1"]
    assert body["assertions"][0]["id"] == "a1"


async def test_case_criteria_update_creates_new_revision(
    client: TestClient, repository: EvaluationRepository
) -> None:
    """保存判据要落成新 revision，且旧 revision 仍可读。"""
    csrf = await _login(client)
    suite = repository.create_suite(make_suite_payload())
    payload = make_case_payload(suite_id=suite["id"])
    payload["evaluation"] = {
        "version": 2,
        "required_dimensions": ["task"],
        "milestones": [{"id": "m1", "weight": 1.0, "assertion_ids": ["a1"]}],
    }
    case = repository.save_case(payload)

    body = await (await client.get(f"{API_PREFIX}/cases/{case['id']}")).json()
    writable = {
        key: value
        for key, value in body.items()
        if key not in {"id", "revision", "created_at", "updated_at", "is_deleted"}
    }
    writable["base_revision"] = body["revision"]
    writable["evaluation"] = {
        "version": 2,
        "required_dimensions": ["task", "constraints"],
        "milestones": [
            {"id": "m1", "weight": 0.6, "assertion_ids": ["a1"]},
            {"id": "m2", "weight": 0.4, "assertion_ids": ["a1"]},
        ],
        "required_evidence": ["final_answer", "trace", "usage"],
    }
    response = await client.patch(
        f"{API_PREFIX}/cases/{case['id']}",
        json=writable,
        headers=_headers(csrf),
    )
    assert response.status == 200
    updated = await response.json()
    assert updated["revision"] == 2
    assert len(updated["evaluation"]["milestones"]) == 2
    # 旧 revision 仍可读：历史运行的冻结快照不受影响。
    old = repository.get_case_revision(case["id"], 1)
    assert len(old["evaluation"]["milestones"]) == 1


async def test_case_update_with_stale_revision_conflicts(
    client: TestClient, repository: EvaluationRepository
) -> None:
    csrf = await _login(client)
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"]))
    body = await (await client.get(f"{API_PREFIX}/cases/{case['id']}")).json()
    writable = {
        key: value
        for key, value in body.items()
        if key not in {"id", "revision", "created_at", "updated_at", "is_deleted"}
    }
    writable["base_revision"] = body["revision"]
    first = await client.patch(
        f"{API_PREFIX}/cases/{case['id']}",
        json=writable,
        headers=_headers(csrf),
    )
    assert first.status == 200
    stale = await client.patch(
        f"{API_PREFIX}/cases/{case['id']}",
        json=writable,
        headers=_headers(csrf),
    )
    assert stale.status == 409
    assert (await stale.json())["error"]["code"] == "REVISION_CONFLICT"
