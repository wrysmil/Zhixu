"""默认评估入口与真实记忆 adapter 的能力接线回归；不调用外部模型。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.memory.models import MemoryCapabilities, MemoryScope
from nanobot.evaluation.memory.zhixu_adapter import ZhixuMemoryAdapter
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.runtime_factory import attempt_paths, build_attempt_config
from nanobot.evaluation.scheduler import Scheduler
from nanobot.evaluation.schemas import CaseWrite
from nanobot.evaluation.server import API_PREFIX, create_eval_app
from nanobot.evaluation.services import EvaluationService, Profile

from .http_app import PASSPHRASE, csrf_headers, default_profiles, login


def _service(root: Path, **kwargs: Any) -> EvaluationService:
    database = EvaluationDatabase(root / "evaluation.db")
    database.init_schema()
    return EvaluationService(
        repository=EvaluationRepository(database), data_dir=root,
        profiles=default_profiles(), workspace=root / "workspace", **kwargs,
    )


async def test_default_capabilities_match_actual_worker_adapter(tmp_path: Path) -> None:
    service = _service(tmp_path / "service")
    adapter = ZhixuMemoryAdapter(
        trial_root=tmp_path / "trial",
        scope=MemoryScope(subject_id="test-user", workspace_id="test-workspace"),
        runtime=object(),
    )
    try:
        view = service.memory_capabilities_view()
        assert view["capabilities"] == adapter.capabilities.model_dump(mode="json")
        assert all(track["supported"] for track in view["tracks"])
        assert view["capabilities"]["semantic_recall"]["status"] == "partial"
        assert view["capabilities"]["clock_coverage"]["status"] == "partial"
        # 展示、准入、冻结清单必须使用同一份能力声明。
        assert service.memory_experiment_coordinator()._capabilities == adapter.capabilities
    finally:
        await adapter.close()


def test_explicitly_unsupported_backend_is_not_enabled(tmp_path: Path) -> None:
    missing = MemoryCapabilities()
    service = _service(tmp_path, memory_capabilities=missing)
    assert not any(track["supported"] for track in service.memory_capabilities_view()["tracks"])
    assert service.memory_experiment_coordinator()._capabilities == missing


async def test_default_app_can_queue_builtin_smoke_experiment(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    async def do_not_dispatch(_scheduler: Scheduler) -> None:
        """保留真实服务生命周期，只禁止本接线测试启动外部模型子进程。"""

    monkeypatch.setattr(Scheduler, "_dispatch_once", do_not_dispatch)
    selected = Profile(
        id="model:chosen", label="被测模型", provider="openai", model="selected-model",
        bundle={"config": {"agents": {"defaults": {"model": "selected-model"}}}},
    )
    app = create_eval_app(
        data_dir=tmp_path, passphrase=PASSPHRASE, profiles=(*default_profiles(), selected),
    )
    async with TestClient(TestServer(app, host="127.0.0.1")) as client:
        csrf = await login(client)
        response = await client.get(f"{API_PREFIX}/memory/capabilities")
        assert response.status == 200
        view = await response.json()
        assert all(track["supported"] for track in view["tracks"])

        response = await client.post(
            f"{API_PREFIX}/samples/import", json={"sample_id": "memory-smoke-v1"},
            headers=csrf_headers(csrf),
        )
        assert response.status == 201, await response.text()
        revision = (await response.json())["memory_dataset_revision_id"]
        response = await client.post(
            f"{API_PREFIX}/memory/experiments",
            headers={**csrf_headers(csrf), "Idempotency-Key": "default-smoke-1"},
            json={
                "dataset_revision_id": revision,
                "split": "development", "track": "end_to_end",
                "candidates": [{
                    "id": "current", "name": "当前模型", "mode": "zhixu",
                    "agent_profile_id": selected.id, "memory_settings": {},
                }],
                "repetitions": 1, "model_mode": "real",
                "primary_metric": "qa_accuracy_scenario_macro",
                "budgets": {"max_trials": 20, "concurrency": 1},
                "policy": {
                    "fact_matching_version": "fact-matching/v1",
                    "metric_aggregation_version": "metric-aggregation/v1",
                    "bootstrap_version": "paired-cluster-percentile/v1",
                },
            },
        )
        assert response.status == 202, await response.text()
        experiment = await response.json()
        assert experiment["planned_trials"] == 20
        assert experiment["status"] == "queued"
        assert len(experiment["child_runs"]) == 1
        context = app["eval_context"]
        run = context.repository.get_run(experiment["child_runs"][0]["run_id"])
        assert len(run["attempts"]) == 20
        assert run["snapshot"]["agent_profile"]["id"] == selected.id
        assert run["snapshot"]["memory_experiment"]["candidate"]["mode"] == "zhixu"
        attempt = run["attempts"][0]
        paths = attempt_paths(
            attempt_id=attempt["id"], runtime_root=str(tmp_path / "runtime"),
            workspace=str(tmp_path / "workspace"),
        )
        boot = context.service._agent_boot(
            attempt["id"], CaseWrite.model_validate(attempt["case_snapshot"]), run, paths,
        )
        assert boot.profile_bundle == selected.bundle
        config = build_attempt_config(profile_bundle=boot.profile_bundle, paths=paths)
        assert config.resolve_preset().model == selected.model
        assert boot.memory_execution["track"] == "end_to_end"
        assert boot.memory_execution["candidates"][0]["agent_profile_id"] == selected.id
        assert "gold" not in boot.memory_execution
