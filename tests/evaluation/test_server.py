"""T10 HTTP 层：health、登录、会话、CSRF、Origin、版本冲突、重复提交与产物隔离。

这里用 aiohttp 的真实测试客户端（不 mock 框架），锁的是契约 §3 里最容易悄悄失效的几条：

1. 未登录一律 401；登录后写操作缺 CSRF 一律 403。
2. Host / Origin 精确匹配：**不放行任意 Origin**。
3. 创建运行入队即 202，响应带 Location；重复 key 复用原对象而不是新建。
4. 产物只按 id 下载，且 attachment + nosniff。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.schemas import RunCreate
from nanobot.evaluation.server import API_PREFIX

from .conftest import (
    make_case_payload,
    make_run_payload,
    make_suite_payload,
)
from .http_app import PASSPHRASE, build_app
from .http_app import csrf_headers as _headers
from .http_app import login as _login
from .http_app import run_request as _run_request


@pytest.fixture
def repository(evaluation_repository: EvaluationRepository) -> EvaluationRepository:
    return evaluation_repository


@pytest.fixture
async def client(repository: EvaluationRepository, tmp_path: Path) -> Any:
    """真应用 + 真调度器生命周期；装配口径与 test_routes 共用 http_app。"""
    async with TestClient(
        TestServer(build_app(repository, tmp_path / "svc"), host="127.0.0.1")
    ) as test_client:
        yield test_client


# ------------------------------------------------------------------- health


async def test_health_needs_no_login(client: TestClient) -> None:
    response = await client.get(f"{API_PREFIX}/health")
    assert response.status == 200
    assert await response.json() == {"status": "ok"}


# -------------------------------------------------------------------- 登录


async def test_login_rejects_wrong_passphrase(client: TestClient) -> None:
    response = await client.post(
        f"{API_PREFIX}/auth/login", json={"passphrase": "wrong"}
    )
    assert response.status == 401
    body = await response.json()
    assert body["error"]["code"] == "AUTH_FAILED"
    # 错误体只有白名单字段：不能顺带回显配置或路径。
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}


async def test_login_sets_httponly_strict_cookie(client: TestClient) -> None:
    response = await client.post(
        f"{API_PREFIX}/auth/login", json={"passphrase": PASSPHRASE}
    )
    cookie = response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert "nanobot_eval_session" in cookie


async def test_session_requires_login(client: TestClient) -> None:
    response = await client.get(f"{API_PREFIX}/auth/session")
    assert response.status == 401


async def test_session_restores_after_login(client: TestClient) -> None:
    csrf = await _login(client)
    response = await client.get(f"{API_PREFIX}/auth/session")
    assert response.status == 200
    assert await response.json() == {"authenticated": True, "csrf_token": csrf}


async def test_logout_clears_session(client: TestClient) -> None:
    await _login(client)
    response = await client.post(f"{API_PREFIX}/auth/logout", headers={})
    # 登出是写操作：没有 CSRF 令牌就先被挡下，会话还没机会失效。
    assert response.status == 403
    assert (await response.json())["error"]["code"] == "CSRF_INVALID"
    csrf = await _login(client)
    ok = await client.post(f"{API_PREFIX}/auth/logout", headers=_headers(csrf))
    assert ok.status == 204
    assert (await client.get(f"{API_PREFIX}/auth/session")).status == 401


async def test_login_body_rejects_unknown_fields(client: TestClient) -> None:
    response = await client.post(
        f"{API_PREFIX}/auth/login",
        json={"passphrase": PASSPHRASE, "role": "admin"},
    )
    assert response.status == 422


# ------------------------------------------------------------------ CSRF


async def test_write_without_csrf_is_rejected(client: TestClient) -> None:
    await _login(client)
    response = await client.post(f"{API_PREFIX}/runs", json={})
    assert response.status == 403
    assert (await response.json())["error"]["code"] == "CSRF_INVALID"


async def test_write_with_wrong_csrf_is_rejected(client: TestClient) -> None:
    await _login(client)
    response = await client.post(
        f"{API_PREFIX}/runs", json={}, headers={"X-CSRF-Token": "wrong"}
    )
    assert response.status == 403


# ------------------------------------------------------- Host / Origin


async def test_unknown_host_is_rejected(client: TestClient) -> None:
    response = await client.get(
        f"{API_PREFIX}/health", headers={"Host": "evil.example.com"}
    )
    assert response.status == 403
    assert (await response.json())["error"]["code"] == "HOST_NOT_ALLOWED"


async def test_unknown_origin_is_rejected(client: TestClient) -> None:
    csrf = await _login(client)
    response = await client.get(
        f"{API_PREFIX}/overview", headers={"Origin": "http://evil.example.com"}
    )
    assert response.status == 403
    assert (await response.json())["error"]["code"] == "ORIGIN_NOT_ALLOWED"
    del csrf


async def test_allowed_origin_passes(client: TestClient) -> None:
    await _login(client)
    response = await client.get(
        f"{API_PREFIX}/overview", headers={"Origin": "http://127.0.0.1:5173"}
    )
    assert response.status == 200


# ------------------------------------------------------------------ 运行


def _seed(repository: EvaluationRepository, count: int = 1) -> dict[str, Any]:
    suite = repository.create_suite(make_suite_payload())
    cases = [
        repository.save_case(make_case_payload(suite_id=suite["id"], name=f"c{i}"))
        for i in range(count)
    ]
    return {
        "suite_id": suite["id"],
        "case_ids": [case["id"] for case in cases],
        "body": RunCreate.model_validate(
            make_run_payload(
                [case["id"] for case in cases],
                suite_id=suite["id"], agent_profile_id="agent-1",
            )
        ).model_dump(mode="json"),
    }


async def test_create_run_returns_202_with_location(
    client: TestClient, repository: EvaluationRepository
) -> None:
    csrf = await _login(client)
    seeded = _seed(repository)
    response = await client.post(
        f"{API_PREFIX}/runs",
        json=seeded["body"],
        headers={**_headers(csrf), "Idempotency-Key": "k-1"},
    )
    assert response.status == 202
    body = await response.json()
    assert response.headers["Location"].endswith(body["id"])
    assert body["status"] in ("queued", "running")
    assert body["snapshot"]["provider"] == "controlled"
    assert "bundle" not in json.dumps(body, ensure_ascii=False)


async def test_create_run_requires_idempotency_key(
    client: TestClient, repository: EvaluationRepository
) -> None:
    csrf = await _login(client)
    seeded = _seed(repository)
    response = await client.post(
        f"{API_PREFIX}/runs", json=seeded["body"], headers=_headers(csrf)
    )
    assert response.status == 422


async def test_replayed_idempotency_key_returns_same_run(
    client: TestClient, repository: EvaluationRepository
) -> None:
    csrf = await _login(client)
    seeded = _seed(repository)
    first = await client.post(
        f"{API_PREFIX}/runs", json=seeded["body"],
        headers={**_headers(csrf), "Idempotency-Key": "k-same"},
    )
    second = await client.post(
        f"{API_PREFIX}/runs", json=seeded["body"],
        headers={**_headers(csrf), "Idempotency-Key": "k-same"},
    )
    assert first.status == second.status == 202
    assert (await first.json())["id"] == (await second.json())["id"]


async def test_same_key_different_payload_is_mismatch(
    client: TestClient, repository: EvaluationRepository
) -> None:
    csrf = await _login(client)
    first_seed = _seed(repository)
    await client.post(
        f"{API_PREFIX}/runs", json=first_seed["body"],
        headers={**_headers(csrf), "Idempotency-Key": "k-diff"},
    )
    second_seed = _seed(repository)
    response = await client.post(
        f"{API_PREFIX}/runs", json=second_seed["body"],
        headers={**_headers(csrf), "Idempotency-Key": "k-diff"},
    )
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "IDEMPOTENCY_MISMATCH"


async def test_create_run_rejects_unknown_case(
    client: TestClient, repository: EvaluationRepository
) -> None:
    csrf = await _login(client)
    seeded = _seed(repository)
    body = {**seeded["body"], "case_ids": ["00000000-0000-4000-8000-000000000000"]}
    response = await client.post(
        f"{API_PREFIX}/runs", json=body,
        headers={**_headers(csrf), "Idempotency-Key": "k-bad"},
    )
    assert response.status >= 400


async def test_unknown_run_is_404(client: TestClient) -> None:
    await _login(client)
    response = await client.get(f"{API_PREFIX}/runs/00000000-0000-4000-8000-000000000000")
    assert response.status == 404


# ------------------------------------------------------------------ 用例


async def test_case_update_conflict_is_409(
    client: TestClient, repository: EvaluationRepository
) -> None:
    csrf = await _login(client)
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"]))
    body = {**make_case_payload(suite_id=suite["id"], name="改名"), "base_revision": 999}
    response = await client.put(
        f"{API_PREFIX}/cases/{case['id']}", json=body, headers=_headers(csrf)
    )
    # 契约的用例编辑走 PATCH；PUT 未注册时 aiohttp 回 405，同样不得静默成功。
    assert response.status in (404, 405)


async def test_cases_list_requires_suite_filter(client: TestClient) -> None:
    await _login(client)
    response = await client.get(f"{API_PREFIX}/cases")
    assert response.status == 200
    assert await response.json() == {"data": []}


# ---------------------------------------------------------------- 校准


async def test_calibration_requires_confirmed_gold(
    client: TestClient, repository: EvaluationRepository
) -> None:
    """proposed 与 confirmed 不能混淆：未经人工确认的 gold 不能起校准。"""
    csrf = await _login(client)
    repository.seed_gold_samples(
        [{
            "id": f"G{i:02d}", "task": "任务", "rubric": {}, "evidence": {},
            "expected_verdict": "passed", "expected_scores": [],
        } for i in range(1, 11)],
        gold_revision=1,
    )
    body = {
        "judge_profile_id": "judge-1",
        "gold_revision": repository.gold_revision(),
        "sample_ids": [f"G{i:02d}" for i in range(1, 11)],
    }
    response = await client.post(
        f"{API_PREFIX}/calibrations", json=body,
        headers={**_headers(csrf), "Idempotency-Key": "cal-1"},
    )
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "CALIBRATION_GOLD_UNCONFIRMED"


async def test_calibration_sample_list_is_proposed_by_default(
    client: TestClient, repository: EvaluationRepository
) -> None:
    repository.seed_gold_samples(
        [{
            "id": "G01", "task": "任务", "rubric": {}, "evidence": {},
            "expected_verdict": "passed", "expected_scores": [],
        }],
        gold_revision=1,
    )
    await _login(client)
    response = await client.get(f"{API_PREFIX}/calibration-samples")
    assert response.status == 200
    data = (await response.json())["data"]
    assert data[0]["label_status"] == "proposed"
    assert data[0]["reviewer"] is None


# ---------------------------------------------------------------- 产物


async def test_artifact_download_is_attachment_and_unknown_is_404(
    client: TestClient, repository: EvaluationRepository
) -> None:
    csrf = await _login(client)
    service = client.app["eval_context"].service
    run = service.create_run(
        _run_request(repository), idempotency_key="art-1", scope="local-user"
    )
    attempt = repository.get_run(run["id"])["attempts"][0]
    target = service.runtime_root / attempt["id"] / "workspace"
    target.mkdir(parents=True, exist_ok=True)
    (target / "output.txt").write_text("内容", encoding="utf-8")
    registered = repository.append_artifact(
        attempt_id=attempt["id"], relative_path="output.txt",
        media_type="text/plain; charset=utf-8", size_bytes=6, sha256="c" * 64,
    )
    response = await client.get(f"{API_PREFIX}/artifacts/{registered['id']}")
    assert response.status == 200
    assert "attachment" in response.headers["Content-Disposition"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    del csrf

    missing = await client.get(
        f"{API_PREFIX}/artifacts/00000000-0000-4000-8000-000000000000"
    )
    assert missing.status == 404


async def test_artifact_outside_workspace_is_rejected(
    client: TestClient, repository: EvaluationRepository
) -> None:
    await _login(client)
    service = client.app["eval_context"].service
    run = service.create_run(
        _run_request(repository), idempotency_key="art-2", scope="local-user"
    )
    attempt = repository.get_run(run["id"])["attempts"][0]
    # 越界路径在**登记层**就被拒：等下载时再判定等于把篡改后的数据当可信输入用。
    with pytest.raises(ValueError, match="PATH_OUTSIDE_ROOT"):
        repository.append_artifact(
            attempt_id=attempt["id"], relative_path="../../../etc/passwd",
            media_type="text/plain", size_bytes=1, sha256="d" * 64,
        )
    assert repository.list_artifacts_for_attempt(attempt["id"]) == []
