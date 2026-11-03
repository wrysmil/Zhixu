"""WU-09：记忆专项 HTTP 路由矩阵（契约 §6）。

这份文件锁的是**边界行为**，不是 happy path：

- 准入：未登录、坏 CSRF、坏 Origin、未知路径/方法；
- 限额与错误码：413（正文与上传两种）、404、422（无能力/不可落地设置/预算/游标）、
  409（处理中、版本过期、未就绪）、同 key 异体 422；
- 读面：分页稳定性、游标绑定筛选、gold 只在授权管理面出现、未知内部异常不回显路径。

调度器在本文件里故意把轮询间隔拉到 1 小时：这些用例只验证「入队即 202」与状态投影，
真跑 trial 属于 WU-12 的集成验收；否则用例会与子进程抢时间而偶发失败。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from nanobot.evaluation.auth import AuthConfig
from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.errors import status_for
from nanobot.evaluation.memory.models import MemoryCapabilities
from nanobot.evaluation.memory.repository import MemoryRepository
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.scheduler import SchedulerConfig
from nanobot.evaluation.server import API_PREFIX, create_app
from nanobot.evaluation.services import EvaluationService

from .conftest import make_suite_payload
from .http_app import PASSPHRASE, default_profiles
from .http_app import csrf_headers as _headers
from .http_app import login as _login
from .memory_fixtures import (
    make_memory_candidate,
    make_memory_dataset_payload,
    make_memory_dataset_source,
    make_memory_scenario,
)

MISSING = "00000000-0000-4000-8000-000000000000"


# --------------------------------------------------------------------------- 装配


def _supported_capabilities(**overrides: Any) -> MemoryCapabilities:
    """全能力可用；个别用例把它覆盖成缺能力来验证 422。"""
    from nanobot.evaluation.memory.models import MemoryCapability

    fields = {name: MemoryCapability(status="supported") for name in MemoryCapabilities.model_fields}
    fields.update(overrides)
    return MemoryCapabilities(**fields)


def _data_root(tmp_path: Path) -> Path:
    """服务数据根与库文件必须同目录：专项仓储按 ``data_dir/evaluation.db`` 惰性开库。"""
    return tmp_path / "svc"


def build_memory_app(
    tmp_path: Path, *, capabilities: MemoryCapabilities | None = None
) -> Any:
    root = _data_root(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    database = EvaluationDatabase(root / "evaluation.db")
    database.init_schema()
    repository = EvaluationRepository(database)
    profiles = default_profiles()
    service = EvaluationService(
        repository=repository,
        data_dir=root,
        profiles=profiles,
        memory_capabilities=capabilities,
    )
    return create_app(
        repository=repository,
        service=service,
        auth_config=AuthConfig(
            passphrase=PASSPHRASE,
            allowed_hosts=frozenset({"127.0.0.1", "localhost"}),
            allowed_origins=frozenset({"http://127.0.0.1:5173"}),
        ),
        scheduler_config=SchedulerConfig(
            data_dir=root, concurrency=1, poll_interval_s=3600
        ),
        profiles=profiles,
    )


@pytest.fixture
def database(tmp_path: Path) -> EvaluationDatabase:
    root = _data_root(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    db = EvaluationDatabase(root / "evaluation.db")
    db.init_schema()
    return db


@pytest.fixture
def repository(database: EvaluationDatabase) -> EvaluationRepository:
    return EvaluationRepository(database)


@pytest.fixture
def app(tmp_path: Path) -> Any:
    return build_memory_app(tmp_path, capabilities=_supported_capabilities())


@pytest.fixture
async def client(app: Any) -> Any:
    async with TestClient(TestServer(app, host="127.0.0.1")) as test_client:
        yield test_client


# --------------------------------------------------------------------------- 数据


def _prepare_dataset(
    repository: EvaluationRepository,
    database: EvaluationDatabase,
    *,
    version: str = "memory-core-v1",
    split: str = "regression",
    scenario_ids: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """造一个 ready 修订（真走版本化导入器，不手写 memory_scenarios）。"""
    suite = repository.create_suite(make_suite_payload(name=f"记忆集合 {version}"))
    ids = scenario_ids or ["fact-001"]
    scenarios = [
        make_memory_scenario(
            scenario_id=scenario_id,
            split=split,
            template_family=f"family-{scenario_id}",
            cluster_id=f"cluster-{scenario_id}",
            scope={"subject_id": f"subject-{scenario_id}", "workspace_id": "synthetic-w1"},
        )
        for scenario_id in ids
    ]
    payload = make_memory_dataset_payload(
        suite_id=suite["id"], scenarios=scenarios, dataset_version=version
    )
    result = MemoryRepository(database).import_revision(payload)
    return suite, result["dataset_revision"], ids


def _experiment_payload(dataset_revision_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "dataset_revision_id": dataset_revision_id,
        "split": "regression",
        "track": "end_to_end",
        "candidates": [make_memory_candidate()],
        "repetitions": 1,
        "model_mode": "real",
        "budgets": {"max_trials": 10, "concurrency": 1, "per_trial_timeout_s": 60},
        "primary_metric": "qa_accuracy_scenario_macro",
        "policy": {
            "fact_matching_version": "fact-matching/v1",
            "metric_aggregation_version": "metric-aggregation/v1",
            "bootstrap_version": "paired-cluster-percentile/v1",
        },
    }
    payload.update(overrides)
    return payload


def _import_metadata(content: bytes, **overrides: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "dataset_version": "uploaded-v1",
        "format": "jsonl",
        "source": make_memory_dataset_source(
            revision="uploaded-v1", sha256=hashlib.sha256(content).hexdigest()
        ),
    }
    metadata.update(overrides)
    return metadata


def _jsonl(scenarios: list[dict[str, Any]]) -> bytes:
    return "\n".join(
        json.dumps(item, ensure_ascii=False) for item in scenarios
    ).encode("utf-8") + b"\n"


def _form(metadata: dict[str, Any], content: bytes, filename: str = "core.jsonl") -> FormData:
    form = FormData()
    form.add_field("metadata", json.dumps(metadata, ensure_ascii=False), content_type="application/json")
    form.add_field("file", content, filename=filename, content_type="application/x-ndjson")
    return form


def _insert_memory_attempt(
    database: EvaluationDatabase,
    repository: EvaluationRepository,
    *,
    memory: dict[str, Any] | None,
) -> str:
    """直接插一条已终结 attempt：逐 probe 接口只读 result，不需要真跑 worker。"""
    suite = repository.create_suite(make_suite_payload(name="假 attempt 宿主"))
    run_id, attempt_id = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    result = {} if memory is None else {"memory": memory}
    with database.connect(immediate=True) as conn:
        conn.execute(
            "INSERT INTO runs (id, suite_id, status, snapshot_json, request_hash,"
            " parent_run_id, is_deleted, created_at, started_at, finished_at)"
            " VALUES (?, ?, 'completed', '{}', 'x', NULL, 0, ?, ?, ?)",
            (run_id, str(suite["id"]), now, now, now),
        )
        conn.execute(
            "INSERT INTO attempts (id, run_id, position, case_id, case_revision,"
            " case_snapshot_json, execution_status, grading_status, verdict,"
            " result_json, pid, process_nonce, started_at, finished_at)"
            " VALUES (?, ?, 1, ?, 1, ?, 'succeeded', 'completed', 'passed', ?, NULL,"
            " NULL, ?, ?)",
            (
                attempt_id,
                run_id,
                str(uuid.uuid4()),
                json.dumps({"name": "记忆专项"},
                           ensure_ascii=False),
                json.dumps(result, ensure_ascii=False),
                now,
                now,
            ),
        )
    return attempt_id


def _memory_result(*, grading_revision: int = 1, probe_id: str = "q1") -> dict[str, Any]:
    metric = {
        "id": "qa_accuracy",
        "version": 1,
        "grading_revision": grading_revision,
        "unit": "ratio",
        "value": 1.0,
        "numerator": 1,
        "denominator": 1,
        "status": "scored",
        "scope": "probe",
    }
    return {
        "scenario_id": "fact-001",
        "revision": 1,
        "track": "end_to_end",
        "metrics": {
            "scenario_id": "fact-001",
            "track": "end_to_end",
            "grading_revision": grading_revision,
            "trial_index": 0,
            "probe_results": [
                {
                    "probe_id": probe_id,
                    "qa_correct": True,
                    "qa_status": "scored",
                    "metrics": [metric],
                }
            ],
            "metrics": [metric],
        },
    }


# ------------------------------------------------------------------ 错误码映射


def test_memory_error_statuses() -> None:
    """契约 §6 的最小可执行验收：码到状态码必须逐项对上。"""
    assert status_for("MEMORY_DATASET_NOT_FOUND") == 404
    assert status_for("MEMORY_DATASET_NOT_READY") == 409
    assert status_for("MEMORY_BUDGET_EXCEEDED") == 422
    assert status_for("MEMORY_IMPORT_TOO_LARGE") == 413
    assert status_for("MEMORY_GRADING_REVISION_CONFLICT") == 409
    assert status_for("MEMORY_CAPABILITY_REQUIRED") == 422


# ------------------------------------------------------------------ 准入


READ_PATHS = [
    "/memory/datasets",
    "/memory/capabilities",
    "/memory/experiments",
    f"/memory/experiments/{MISSING}",
    f"/memory/experiments/{MISSING}/metrics",
    f"/memory/imports/{MISSING}",
    f"/memory/datasets/{MISSING}/scenarios",
    f"/memory/datasets/{MISSING}/scenarios/fact-001",
    f"/memory/attempts/{MISSING}/probes",
    f"/memory/attempts/{MISSING}/events",
    f"/memory/comparisons/{MISSING}",
    f"/memory/comparisons/{MISSING}/report",
    f"/artifacts/{MISSING}",
]


@pytest.mark.parametrize("path", READ_PATHS)
async def test_memory_read_routes_require_login(client: TestClient, path: str) -> None:
    """gold 与 artifact 也一样：读接口先过准入，未登录不给任何内容。"""
    response = await client.get(f"{API_PREFIX}{path}")
    assert response.status == 401
    assert (await response.json())["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize(
    "path",
    [
        "/memory/datasets/import",
        "/memory/experiments",
        "/memory/comparisons",
        f"/memory/experiments/{MISSING}/cancel",
        f"/memory/datasets/{MISSING}/revisions",
    ],
)
async def test_memory_write_routes_require_login(
    client: TestClient, path: str
) -> None:
    response = await client.post(f"{API_PREFIX}{path}", json={})
    assert response.status == 401


async def test_bad_csrf_on_memory_write_is_403(client: TestClient) -> None:
    await _login(client)
    response = await client.post(
        f"{API_PREFIX}/memory/experiments",
        json={},
        headers={"X-CSRF-Token": "forged", "Idempotency-Key": "csrf-1"},
    )
    assert response.status == 403
    assert (await response.json())["error"]["code"] == "CSRF_INVALID"


async def test_bad_origin_is_rejected_before_reading_gold(client: TestClient) -> None:
    response = await client.get(
        f"{API_PREFIX}/memory/datasets/{MISSING}/scenarios/fact-001",
        headers={"Origin": "http://evil.example"},
    )
    assert response.status == 403
    assert (await response.json())["error"]["code"] == "ORIGIN_NOT_ALLOWED"


async def test_unknown_memory_path_and_method_are_json_errors(
    client: TestClient,
) -> None:
    csrf = await _login(client)
    missing = await client.get(f"{API_PREFIX}/memory/no-such-endpoint")
    assert missing.status == 404
    assert (await missing.json())["error"]["code"] == "NOT_FOUND"

    wrong = await client.delete(
        f"{API_PREFIX}/memory/datasets", headers=_headers(csrf)
    )
    assert wrong.status == 405
    assert (await wrong.json())["error"]["code"] == "METHOD_NOT_ALLOWED"


# ------------------------------------------------------------------ 限额与分页参数


async def test_oversized_json_body_is_413(client: TestClient) -> None:
    csrf = await _login(client)
    padding = "x" * (6 * 1024 * 1024)
    response = await client.post(
        f"{API_PREFIX}/memory/experiments",
        data=json.dumps({"dataset_revision_id": padding}),
        headers={
            **_headers(csrf),
            "Idempotency-Key": "too-large-1",
            "Content-Type": "application/json",
        },
    )
    assert response.status == 413
    assert (await response.json())["error"]["code"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "limit=abc", "limit=1.5"])
async def test_memory_list_limit_is_validated(client: TestClient, query: str) -> None:
    await _login(client)
    response = await client.get(f"{API_PREFIX}/memory/datasets?{query}")
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------ 导入


async def test_import_requires_multipart(client: TestClient) -> None:
    csrf = await _login(client)
    response = await client.post(
        f"{API_PREFIX}/memory/datasets/import",
        json={"metadata": {}},
        headers={**_headers(csrf), "Idempotency-Key": "import-ctype-1"},
    )
    assert response.status == 415
    assert (await response.json())["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


async def test_import_rejects_hash_mismatch(
    client: TestClient, tmp_path: Path
) -> None:
    csrf = await _login(client)
    content = _jsonl([make_memory_scenario()])
    metadata = _import_metadata(content, source=make_memory_dataset_source(sha256="b" * 64))
    response = await client.post(
        f"{API_PREFIX}/memory/datasets/import",
        data=_form(metadata, content),
        headers={**_headers(csrf), "Idempotency-Key": "import-hash-1"},
    )
    assert response.status == 422
    body = await response.json()
    assert body["error"]["code"] == "MEMORY_SOURCE_HASH_MISMATCH"
    # 失败后临时文件必须清理干净。
    staging = tmp_path / "svc" / "memory-imports"
    assert not list(staging.glob("*.upload"))


async def test_import_over_upload_limit_is_413(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nanobot.evaluation.memory import routes as memory_routes

    monkeypatch.setattr(memory_routes, "MAX_UPLOAD_BYTES", 1024)
    csrf = await _login(client)
    content = _jsonl([make_memory_scenario()]) * 40
    response = await client.post(
        f"{API_PREFIX}/memory/datasets/import",
        data=_form(_import_metadata(content), content),
        headers={**_headers(csrf), "Idempotency-Key": "import-size-1"},
    )
    assert response.status == 413
    assert (await response.json())["error"]["code"] == "MEMORY_IMPORT_TOO_LARGE"
    staging = tmp_path / "svc" / "memory-imports"
    assert not list(staging.glob("*.upload"))


async def test_import_publishes_revision_and_polling_reads_status(
    client: TestClient,
) -> None:
    csrf = await _login(client)
    content = _jsonl([make_memory_scenario()])
    response = await client.post(
        f"{API_PREFIX}/memory/datasets/import",
        data=_form(_import_metadata(content), content),
        headers={**_headers(csrf), "Idempotency-Key": "import-ok-1"},
    )
    assert response.status == 202
    created = await response.json()
    assert created["status"] == "ready"
    assert created["dataset_revision_id"]
    assert response.headers["Location"] == (
        f"{API_PREFIX}/memory/imports/{created['import_id']}"
    )

    status = await client.get(
        f"{API_PREFIX}/memory/imports/{created['import_id']}"
    )
    assert status.status == 200
    detail = await status.json()
    assert detail["status"] == "ready"
    assert detail["counts"]["observed"] == 1
    assert detail["dataset_revision_id"] == created["dataset_revision_id"]

    listing = await (await client.get(f"{API_PREFIX}/memory/datasets")).json()
    assert any(
        item["id"] == created["dataset_revision_id"] for item in listing["data"]
    )


async def test_import_of_malformed_jsonl_is_422(client: TestClient) -> None:
    csrf = await _login(client)
    content = b'{"scenario_id": "x", \n'
    response = await client.post(
        f"{API_PREFIX}/memory/datasets/import",
        data=_form(_import_metadata(content), content),
        headers={**_headers(csrf), "Idempotency-Key": "import-bad-1"},
    )
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "MEMORY_SCHEMA_INVALID"


async def test_import_missing_file_field_is_422(client: TestClient) -> None:
    csrf = await _login(client)
    form = FormData()
    form.add_field("metadata", json.dumps(_import_metadata(b"x")), content_type="application/json")
    response = await client.post(
        f"{API_PREFIX}/memory/datasets/import",
        data=form,
        headers={**_headers(csrf), "Idempotency-Key": "import-nofile-1"},
    )
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "MEMORY_SCHEMA_INVALID"


async def test_import_is_idempotent_by_key(client: TestClient) -> None:
    """同一 key + 同一请求体：重放返回同一修订，绝不重复发布。"""
    csrf = await _login(client)
    content = _jsonl([make_memory_scenario()])
    metadata = _import_metadata(content)
    headers = {**_headers(csrf), "Idempotency-Key": "import-idem-1"}

    first = await client.post(
        f"{API_PREFIX}/memory/datasets/import",
        data=_form(metadata, content),
        headers=headers,
    )
    assert first.status == 202
    created = await first.json()

    replay = await client.post(
        f"{API_PREFIX}/memory/datasets/import",
        data=_form(metadata, content),
        headers=headers,
    )
    assert replay.status == 202
    assert await replay.json() == created

    listing = await (await client.get(f"{API_PREFIX}/memory/datasets")).json()
    assert len([item for item in listing["data"] if item["id"] == created["dataset_revision_id"]]) == 1


async def test_streaming_upload_exceeds_json_body_limit_without_raising_it(
    client: TestClient,
) -> None:
    """6MiB 上传成功，但全站 JSON 正文上限仍是 5MiB（流式 multipart 不受牵连）。"""
    from nanobot.evaluation import server as server_module

    assert server_module.MAX_BODY_BYTES == 5 * 1024 * 1024
    csrf = await _login(client)
    content = b"\n" * (6 * 1024 * 1024) + _jsonl([make_memory_scenario()])
    response = await client.post(
        f"{API_PREFIX}/memory/datasets/import",
        data=_form(_import_metadata(content), content),
        headers={**_headers(csrf), "Idempotency-Key": "import-stream-1"},
    )
    assert response.status == 202
    assert (await response.json())["scenario_count"] == 1


async def test_unknown_import_is_404(client: TestClient) -> None:
    await _login(client)
    response = await client.get(f"{API_PREFIX}/memory/imports/{MISSING}")
    assert response.status == 404
    assert (await response.json())["error"]["code"] == "MEMORY_IMPORT_NOT_FOUND"


async def test_builtin_memory_sample_import_returns_revision(client: TestClient) -> None:
    """契约 §6：内置集合走同一版本化导入器，返回 suite_id 与 memory_dataset_revision_id。"""
    csrf = await _login(client)
    first = await client.post(
        f"{API_PREFIX}/samples/import",
        json={"sample_id": "memory-smoke-v1"},
        headers=_headers(csrf),
    )
    assert first.status == 201
    body = await first.json()
    assert body["scenario_count"] == 20
    assert body["memory_dataset_revision_id"]
    assert body["suite_id"]

    replay = await (
        await client.post(
            f"{API_PREFIX}/samples/import",
            json={"sample_id": "memory-smoke-v1"},
            headers=_headers(csrf),
        )
    ).json()
    assert replay["memory_dataset_revision_id"] == body["memory_dataset_revision_id"]


# ------------------------------------------------------------------ 集合读面


async def test_dataset_pages_are_stable_and_split_filtered(
    client: TestClient,
    repository: EvaluationRepository,
    database: EvaluationDatabase,
) -> None:
    await _login(client)
    _prepare_dataset(repository, database, version="core-a", split="regression")
    _prepare_dataset(repository, database, version="smoke-a", split="development")

    regression = await (
        await client.get(f"{API_PREFIX}/memory/datasets?split=regression&limit=1")
    ).json()
    assert [item["split_counts"] for item in regression["data"]] == [{"regression": 1}]

    first = await (
        await client.get(f"{API_PREFIX}/memory/datasets?limit=1")
    ).json()
    assert first["next_after_key"]
    second = await (
        await client.get(
            f"{API_PREFIX}/memory/datasets?limit=1&after_key={first['next_after_key']}"
        )
    ).json()
    assert second["data"][0]["id"] != first["data"][0]["id"]
    assert second["next_after_key"] is None

    # 换筛选条件复用同一游标 = 明示拒绝（否则会把两套排序混在一起）。
    mismatch = await client.get(
        f"{API_PREFIX}/memory/datasets?split=regression&limit=1"
        f"&after_key={first['next_after_key']}"
    )
    assert mismatch.status == 422
    assert (await mismatch.json())["error"]["code"] == "VALIDATION_ERROR"

    garbage = await client.get(f"{API_PREFIX}/memory/datasets?after_key=not-a-cursor")
    assert garbage.status == 422


async def test_scenario_list_hides_gold_but_detail_exposes_it(
    client: TestClient,
    repository: EvaluationRepository,
    database: EvaluationDatabase,
) -> None:
    await _login(client)
    _suite, revision, scenario_ids = _prepare_dataset(repository, database)
    revision_id = str(revision["id"])

    listing = await (
        await client.get(f"{API_PREFIX}/memory/datasets/{revision_id}/scenarios")
    ).json()
    assert listing["data"][0]["scenario_id"] == scenario_ids[0]
    assert "gold" not in listing["data"][0]
    assert "gold" not in json.dumps(listing)

    detail = await (
        await client.get(
            f"{API_PREFIX}/memory/datasets/{revision_id}/scenarios/{scenario_ids[0]}"
        )
    ).json()
    assert detail["revision"] == 1
    assert set(detail) == {"execution", "gold", "revision"}
    assert "gold" not in detail["execution"]
    assert detail["gold"]["probes"]

    missing = await client.get(
        f"{API_PREFIX}/memory/datasets/{revision_id}/scenarios/no-such-scenario"
    )
    assert missing.status == 404
    assert (await missing.json())["error"]["code"] == "MEMORY_SCENARIO_NOT_FOUND"

    unknown_dataset = await client.get(
        f"{API_PREFIX}/memory/datasets/{MISSING}/scenarios"
    )
    assert unknown_dataset.status == 404
    assert (await unknown_dataset.json())["error"]["code"] == "MEMORY_DATASET_NOT_FOUND"


async def test_revision_edit_is_cas_and_keeps_base_untouched(
    client: TestClient,
    repository: EvaluationRepository,
    database: EvaluationDatabase,
) -> None:
    csrf = await _login(client)
    _suite, revision, scenario_ids = _prepare_dataset(repository, database)
    revision_id = str(revision["id"])
    change = make_memory_scenario(
        scenario_id=scenario_ids[0],
        revision=2,
        tags=["fact", "revised"],
    )
    created = await client.post(
        f"{API_PREFIX}/memory/datasets/{revision_id}/revisions",
        json={"base_revision": revision["dataset_version"], "changes": [change]},
        headers=_headers(csrf),
    )
    assert created.status == 201
    new_revision = await created.json()
    assert new_revision["id"] != revision_id
    assert new_revision["dataset_version"].startswith(revision["dataset_version"])

    base = await (
        await client.get(
            f"{API_PREFIX}/memory/datasets/{revision_id}/scenarios/{scenario_ids[0]}"
        )
    ).json()
    assert base["revision"] == 1

    stale = await client.post(
        f"{API_PREFIX}/memory/datasets/{revision_id}/revisions",
        json={"base_revision": "stale-version", "changes": [change]},
        headers=_headers(csrf),
    )
    assert stale.status == 409
    assert (await stale.json())["error"]["code"] == "REVISION_CONFLICT"


# ------------------------------------------------------------------ 能力与实验


async def test_capabilities_report_missing_items(tmp_path: Path) -> None:
    app = build_memory_app(tmp_path, capabilities=MemoryCapabilities())
    async with TestClient(TestServer(app, host="127.0.0.1")) as client:
        await _login(client)
        body = await (await client.get(f"{API_PREFIX}/memory/capabilities")).json()
    assert {item["id"] for item in body["tracks"]} == {
        "extraction",
        "retrieval",
        "end_to_end",
    }
    assert all(item["supported"] is False for item in body["tracks"])
    assert "extraction" in body["missing"]
    assert "retrieval_channels" in body["settings"]


@pytest.mark.parametrize("path", ["/memory/experiments"])
async def test_create_experiment_without_capabilities_is_422(
    tmp_path: Path, path: str
) -> None:
    # 显式缺能力后端仍应拒绝；未注入时现在使用真实默认 adapter 的能力。
    app = build_memory_app(tmp_path, capabilities=MemoryCapabilities())
    database = EvaluationDatabase(_data_root(tmp_path) / "evaluation.db")
    repository = EvaluationRepository(database)
    _suite, revision, _ids = _prepare_dataset(repository, database)
    async with TestClient(TestServer(app, host="127.0.0.1")) as client:
        csrf = await _login(client)
        response = await client.post(
            f"{API_PREFIX}{path}",
            json=_experiment_payload(str(revision["id"])),
            headers={**_headers(csrf), "Idempotency-Key": "cap-1"},
        )
        status, body = response.status, await response.json()
    assert status == 422
    assert body["error"]["code"] == "MEMORY_CAPABILITY_REQUIRED"


async def test_unmapped_memory_setting_is_422(
    client: TestClient,
    repository: EvaluationRepository,
    database: EvaluationDatabase,
) -> None:
    csrf = await _login(client)
    _suite, revision, _ids = _prepare_dataset(repository, database)
    candidate = make_memory_candidate(
        memory_settings={
            "extraction_enabled": True,
            "retrieval_channels": ["vector_v2"],
        }
    )
    response = await client.post(
        f"{API_PREFIX}/memory/experiments",
        json=_experiment_payload(str(revision["id"]), candidates=[candidate]),
        headers={**_headers(csrf), "Idempotency-Key": "setting-1"},
    )
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "UNSUPPORTED_MEMORY_SETTING"


async def test_create_experiment_requires_idempotency_key(
    client: TestClient,
    repository: EvaluationRepository,
    database: EvaluationDatabase,
) -> None:
    csrf = await _login(client)
    _suite, revision, _ids = _prepare_dataset(repository, database)
    response = await client.post(
        f"{API_PREFIX}/memory/experiments",
        json=_experiment_payload(str(revision["id"])),
        headers=_headers(csrf),
    )
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "VALIDATION_ERROR"


async def test_experiment_creation_is_idempotent_and_body_bound(
    client: TestClient,
    repository: EvaluationRepository,
    database: EvaluationDatabase,
) -> None:
    csrf = await _login(client)
    _suite, revision, _ids = _prepare_dataset(repository, database)
    payload = _experiment_payload(str(revision["id"]))
    headers = {**_headers(csrf), "Idempotency-Key": "exp-idem-1"}

    first = await client.post(f"{API_PREFIX}/memory/experiments", json=payload, headers=headers)
    assert first.status == 202
    created = await first.json()
    assert created["planned_trials"] == 1
    assert len(created["child_runs"]) == 1

    replay = await client.post(f"{API_PREFIX}/memory/experiments", json=payload, headers=headers)
    assert replay.status == 202
    assert (await replay.json())["id"] == created["id"]

    changed = await client.post(
        f"{API_PREFIX}/memory/experiments",
        json={**payload, "repetitions": 2},
        headers=headers,
    )
    assert changed.status == 422
    assert (await changed.json())["error"]["code"] == "IDEMPOTENCY_MISMATCH"

    # 计划只应落一条实验与一条子 run（重放不重复建批）。
    experiments = await (await client.get(f"{API_PREFIX}/memory/experiments")).json()
    assert len(experiments["data"]) == 1


async def test_in_flight_idempotency_key_is_409(
    client: TestClient,
    repository: EvaluationRepository,
    database: EvaluationDatabase,
) -> None:
    csrf = await _login(client)
    _suite, revision, _ids = _prepare_dataset(repository, database)
    payload = _experiment_payload(str(revision["id"]))
    # 幂等摘要按**服务层实际消费的**严格模型算（默认值补齐后），不是原始 dict。
    from nanobot.evaluation.schemas import MemoryExperimentCreate

    digest = repository.request_digest(
        MemoryExperimentCreate.model_validate(payload).model_dump(mode="json")
    )
    with database.connect(immediate=True) as conn:
        conn.execute(
            "INSERT INTO idempotency (scope, endpoint, key, request_hash, run_id,"
            " response_json, created_at) VALUES ('local-user', 'memory/experiments',"
            " 'exp-inflight-1', ?, NULL, NULL, '2026-11-02T00:00:00+00:00')",
            (digest,),
        )
    response = await client.post(
        f"{API_PREFIX}/memory/experiments",
        json=payload,
        headers={**_headers(csrf), "Idempotency-Key": "exp-inflight-1"},
    )
    assert response.status == 409
    assert (await response.json())["error"]["code"] == "IDEMPOTENCY_IN_PROGRESS"


async def test_experiment_budget_gate_is_422(
    client: TestClient,
    repository: EvaluationRepository,
    database: EvaluationDatabase,
) -> None:
    csrf = await _login(client)
    _suite, revision, _ids = _prepare_dataset(
        repository, database, scenario_ids=["fact-001", "fact-002"]
    )
    response = await client.post(
        f"{API_PREFIX}/memory/experiments",
        json=_experiment_payload(
            str(revision["id"]), budgets={"max_trials": 1, "concurrency": 1}
        ),
        headers={**_headers(csrf), "Idempotency-Key": "budget-1"},
    )
    assert response.status == 422
    assert (await response.json())["error"]["code"] == "MEMORY_BUDGET_EXCEEDED"


async def test_experiment_on_staging_revision_is_409(
    client: TestClient,
    repository: EvaluationRepository,
    database: EvaluationDatabase,
) -> None:
    csrf = await _login(client)
    suite = repository.create_suite(make_suite_payload(name="staging 宿主"))
    revision_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with database.connect(immediate=True) as conn:
        conn.execute(
            "INSERT INTO memory_dataset_revisions (id, suite_id, dataset_version,"
            " status, split_counts_json, source_json, definition_digest, created_at,"
            " updated_at) VALUES (?, ?, 'staging-v1', 'staging', '{}', ?, ?, ?, ?)",
            (revision_id, str(suite["id"]), json.dumps(make_memory_dataset_source()),
             "c" * 64, now, now),
        )
    response = await client.post(
        f"{API_PREFIX}/memory/experiments",
        json=_experiment_payload(revision_id),
        headers={**_headers(csrf), "Idempotency-Key": "staging-1"},
    )
    assert response.status == 409
    assert (await response.json())["error"]["code"] == "MEMORY_DATASET_NOT_READY"


async def test_experiment_read_cancel_and_metrics(
    client: TestClient,
    repository: EvaluationRepository,
    database: EvaluationDatabase,
) -> None:
    csrf = await _login(client)
    _suite, revision, _ids = _prepare_dataset(repository, database)
    created = await (
        await client.post(
            f"{API_PREFIX}/memory/experiments",
            json=_experiment_payload(str(revision["id"])),
            headers={**_headers(csrf), "Idempotency-Key": "exp-life-1"},
        )
    ).json()
    experiment_id = created["id"]

    detail = await (await client.get(f"{API_PREFIX}/memory/experiments/{experiment_id}")).json()
    assert detail["status"] == "queued"
    assert detail["planned_trials"] == 1
    assert detail["progress"]["child_runs"] == 1
    assert detail["measurement_status"] == "incomplete"
    assert detail["budget"]["max_trials"] == 10
    assert detail["frozen_manifest"]

    metrics = await (
        await client.get(f"{API_PREFIX}/memory/experiments/{experiment_id}/metrics")
    ).json()
    assert metrics["metrics_version"] == 1
    assert metrics["metrics"] == []
    assert metrics["coverage"]["planned"] == 1

    cancelled = await client.post(
        f"{API_PREFIX}/memory/experiments/{experiment_id}/cancel",
        json={},
        headers=_headers(csrf),
    )
    assert cancelled.status == 202
    assert (await cancelled.json())["status"] == "cancelled"

    again = await client.post(
        f"{API_PREFIX}/memory/experiments/{experiment_id}/cancel",
        json={},
        headers=_headers(csrf),
    )
    assert again.status == 200
    assert (await again.json())["status"] == "cancelled"

    missing = await client.get(f"{API_PREFIX}/memory/experiments/{MISSING}")
    assert missing.status == 404
    assert (await missing.json())["error"]["code"] == "MEMORY_EXPERIMENT_NOT_FOUND"


# ------------------------------------------------------------------ attempt 读面


async def test_attempt_probes_and_events(
    client: TestClient,
    repository: EvaluationRepository,
    database: EvaluationDatabase,
) -> None:
    await _login(client)
    attempt_id = _insert_memory_attempt(
        database, repository, memory=_memory_result()
    )
    probes = await (
        await client.get(f"{API_PREFIX}/memory/attempts/{attempt_id}/probes")
    ).json()
    assert probes["data"][0]["probe_id"] == "q1"
    assert probes["data"][0]["qa_correct"] is True
    assert "gold" not in json.dumps(probes)

    events = await (
        await client.get(f"{API_PREFIX}/memory/attempts/{attempt_id}/events")
    ).json()
    assert events["data"] == []
    assert events["sealed"] is False

    bad_limit = await client.get(
        f"{API_PREFIX}/memory/attempts/{attempt_id}/events?limit=0"
    )
    assert bad_limit.status == 422

    plain = _insert_memory_attempt(database, repository, memory=None)
    no_result = await client.get(f"{API_PREFIX}/memory/attempts/{plain}/probes")
    assert no_result.status == 422
    assert (await no_result.json())["error"]["code"] == "MEMORY_RESULT_MISSING"

    unknown = await client.get(f"{API_PREFIX}/memory/attempts/{MISSING}/probes")
    assert unknown.status == 404


async def test_review_memory_ref_is_attributed_and_version_checked(
    client: TestClient,
    repository: EvaluationRepository,
    database: EvaluationDatabase,
) -> None:
    csrf = await _login(client)
    attempt_id = _insert_memory_attempt(database, repository, memory=_memory_result())
    reference = {
        "scenario_id": "fact-001",
        "probe_id": "q1",
        "metric_id": "qa_accuracy",
        "grading_revision": 1,
    }
    accepted = await client.post(
        f"{API_PREFIX}/attempts/{attempt_id}/reviews",
        json={"verdict": "passed", "memory_ref": reference},
        headers=_headers(csrf),
    )
    assert accepted.status == 201
    assert (await accepted.json())["memory_ref"] == reference

    stale = await client.post(
        f"{API_PREFIX}/attempts/{attempt_id}/reviews",
        json={"verdict": "passed", "memory_ref": {**reference, "grading_revision": 2}},
        headers=_headers(csrf),
    )
    assert stale.status == 409
    assert (await stale.json())["error"]["code"] == "MEMORY_GRADING_REVISION_CONFLICT"

    wrong_probe = await client.post(
        f"{API_PREFIX}/attempts/{attempt_id}/reviews",
        json={"verdict": "passed", "memory_ref": {**reference, "probe_id": "q9"}},
        headers=_headers(csrf),
    )
    assert wrong_probe.status == 422
    assert (await wrong_probe.json())["error"]["code"] == "MEMORY_EVIDENCE_INVALID"

    legacy = await client.post(
        f"{API_PREFIX}/attempts/{attempt_id}/reviews",
        json={"verdict": "failed", "comment": "旧形状"},
        headers=_headers(csrf),
    )
    assert legacy.status == 201
    legacy_body = await legacy.json()
    assert legacy_body["memory_ref"] is None
    assert set(legacy_body) >= {
        "id",
        "attempt_id",
        "verdict",
        "comment",
        "reviewer",
        "created_at",
    }

    plain = _insert_memory_attempt(database, repository, memory=None)
    no_ref = await client.post(
        f"{API_PREFIX}/attempts/{plain}/reviews",
        json={"verdict": "passed", "memory_ref": reference},
        headers=_headers(csrf),
    )
    assert no_ref.status == 422
    assert (await no_ref.json())["error"]["code"] == "MEMORY_SCENARIO_REF_REQUIRED"


# ------------------------------------------------------------------ 比较


async def test_comparison_routes_are_guarded_and_gated(
    client: TestClient,
    repository: EvaluationRepository,
    database: EvaluationDatabase,
) -> None:
    csrf = await _login(client)
    payload = {
        "baseline_experiment_id": str(uuid.uuid4()),
        "candidate_experiment_id": str(uuid.uuid4()),
        "baseline_candidate_id": "cand-baseline",
        "candidate_candidate_id": "cand-candidate",
        "changed_fields": ["/candidates/0/memory_settings"],
    }
    no_key = await client.post(
        f"{API_PREFIX}/memory/comparisons", json=payload, headers=_headers(csrf)
    )
    assert no_key.status == 422
    assert (await no_key.json())["error"]["code"] == "VALIDATION_ERROR"

    unknown = await client.post(
        f"{API_PREFIX}/memory/comparisons",
        json=payload,
        headers={**_headers(csrf), "Idempotency-Key": "cmp-1"},
    )
    assert unknown.status == 404
    assert (await unknown.json())["error"]["code"] == "MEMORY_EXPERIMENT_NOT_FOUND"

    missing = await client.get(f"{API_PREFIX}/memory/comparisons/{MISSING}")
    assert missing.status == 404
    assert (await missing.json())["error"]["code"] == "MEMORY_COMPARISON_NOT_FOUND"

    report = await client.get(f"{API_PREFIX}/memory/comparisons/{MISSING}/report")
    assert report.status == 404


# ------------------------------------------------------------------ 未知内部错误


async def test_internal_error_never_leaks_paths(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _login(client)
    service = client.server.app["eval_context"].service

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("C:\\Users\\secret\\evaluation.db 读取失败")

    monkeypatch.setattr(service, "memory_dataset_list", boom)
    response = await client.get(f"{API_PREFIX}/memory/datasets")
    assert response.status == 500
    body = await response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in json.dumps(body)
    assert "evaluation.db" not in json.dumps(body)
