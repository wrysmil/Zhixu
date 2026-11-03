"""WU-12 Step 1：记忆评测离线端到端（真实服务/DB/子进程 + 受控 provider）。

口径先说清楚（验收时不得混称）：

- **执行段**用的是真实 ``create_app`` + 真实调度器 + 真实 Worker 子进程 + 真实
  SQLite 记忆库；唯一被替换的是 LLM——``profile_bundle["script"]`` 下发的
  ``ControlledProvider`` 按脚本回放。因此这里证明的是「链路正确、账目正确、失败
  可定位」，**不能**据此声称任何真实模型质量或记忆算法提升。
- 本文件断言的分数全部来自合成场景与脚本化模型；``qa_accuracy < 1`` 恰恰用来
  证明没有把受控脚本包装成达标成绩。
- 四种受控故障（抽取漏项 / 召回漏项 / 注入预算截断 / 回答误用）只注入到
  **状态机接缝**（adapter 包装 + observer 的固定词表解析），不新增任何产品分支；
  评分、指标、比较与 HTML 报告全部走生产实现，并要求报告能分别定位到不同阶段。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from nanobot.evaluation.auth import AuthConfig
from nanobot.evaluation.controlled_provider import ControlledProvider, ScriptStep
from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.memory.comparisons import compare_memory_experiments
from nanobot.evaluation.memory.experiments import (
    MemoryBudgetExceeded,
    MemoryExperimentCoordinator,
    plan_batches,
)
from nanobot.evaluation.memory.grading import default_grading_policy, grade_memory_trial
from nanobot.evaluation.memory.models import (
    MemoryCapabilities,
    MemoryCapability,
    MemoryExecutionInput,
    MemoryFact,
    MemoryScenario,
)
from nanobot.evaluation.memory.report import render_memory_comparison_report
from nanobot.evaluation.memory.runner import execution_fields, run_memory_scenario
from nanobot.evaluation.memory.worker_bridge import ZhixuWorkerObserver
from nanobot.evaluation.memory.zhixu_adapter import ZhixuMemoryAdapter
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.scheduler import SchedulerConfig
from nanobot.evaluation.server import API_PREFIX, create_app
from nanobot.evaluation.services import EvaluationService, Profile
from nanobot.providers.base import GenerationSettings

from .http_app import PASSPHRASE, csrf_headers, login
from .memory_fixtures import make_memory_candidate, make_memory_dataset_payload
from .test_runtime_factory import profile_bundle

TERMINAL = ("completed", "cancelled", "interrupted", "timed_out", "failed")
AGENT_PROFILE_ID = "agent-1"

#: 受控 provider 的一轮抽取调用（semantic）+ 情节（episode）+ 探针回答。
SEMANTIC_JSON = json.dumps(
    {
        "memories": [
            {
                "content": "用户的虚构测试城市叫青岚城",
                "evidence": "我的虚构测试城市叫青岚城",
                "type": "fact",
                "importance": 0.9,
                "subject": "用户",
                "predicate": "city",
                "tags": ["test"],
            }
        ],
        "experiences": [],
    },
    ensure_ascii=False,
)
EPISODE_JSON = json.dumps(
    {"summary": "用户告知居住城市。", "goal": "记录城市", "importance": 0.7},
    ensure_ascii=False,
)
PROBE_ANSWER = "我住在青岚城。"


# --------------------------------------------------------------------------- 装配


def _capabilities() -> MemoryCapabilities:
    return MemoryCapabilities(
        **{
            name: MemoryCapability(status="supported")
            for name in MemoryCapabilities.model_fields
        }
    )


def _profiles(
    script: Sequence[Mapping[str, Any]] | None,
    *,
    config: Mapping[str, Any] | None = None,
) -> tuple[Profile, ...]:
    bundle = profile_bundle()
    if config is not None:
        bundle["config"] = dict(config)
    if script is not None:
        bundle["script"] = [dict(item) for item in script]
    return (
        Profile(
            id=AGENT_PROFILE_ID,
            label="受控执行器",
            provider="controlled",
            model="eval-model",
            kind="agent",
            bundle=bundle,
        ),
    )


def _script(*, with_probe: bool = True) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = [
        {"json_text": SEMANTIC_JSON},
        {"json_text": EPISODE_JSON},
    ]
    if with_probe:
        steps.append({"text": PROBE_ANSWER})
    return steps


def _root(tmp_path: Path) -> Path:
    """服务数据根与库文件必须同目录：专项仓储按 ``data_dir/evaluation.db`` 开库。"""
    root = tmp_path / "svc"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _build_app(
    root: Path,
    *,
    script: Sequence[Mapping[str, Any]] | None,
    capabilities: MemoryCapabilities | None = None,
    config: Mapping[str, Any] | None = None,
    poll_interval_s: float = 0.05,
) -> tuple[Any, EvaluationRepository, EvaluationService]:
    database = EvaluationDatabase(root / "evaluation.db")
    database.init_schema()
    repository = EvaluationRepository(database)
    profiles = _profiles(script, config=config)
    service = EvaluationService(
        repository=repository,
        data_dir=root,
        profiles=profiles,
        memory_capabilities=capabilities or _capabilities(),
    )
    app = create_app(
        repository=repository,
        service=service,
        auth_config=AuthConfig(
            passphrase=PASSPHRASE,
            allowed_hosts=frozenset({"127.0.0.1", "localhost"}),
            allowed_origins=frozenset({"http://127.0.0.1:5173"}),
        ),
        scheduler_config=SchedulerConfig(
            data_dir=root, concurrency=1, poll_interval_s=poll_interval_s
        ),
        profiles=profiles,
    )
    return app, repository, service


def _candidate(**overrides: Any) -> dict[str, Any]:
    return make_memory_candidate(
        agent_profile_id=AGENT_PROFILE_ID,
        extractor_profile_id=AGENT_PROFILE_ID,
        **overrides,
    )


def _policy() -> dict[str, Any]:
    return default_grading_policy().model_dump(mode="json")


def _experiment_body(
    revision_id: str,
    *,
    track: str,
    split: str,
    scenario_ids: Sequence[str] | None = None,
    candidates: Sequence[Mapping[str, Any]] | None = None,
    repetitions: int = 1,
    max_trials: int = 200,
) -> dict[str, Any]:
    primary = {
        "extraction": "extraction_recall",
        "retrieval": "evidence_recall_at_k",
        "end_to_end": "qa_accuracy_scenario_macro",
    }[track]
    return {
        "dataset_revision_id": revision_id,
        "scenario_ids": list(scenario_ids) if scenario_ids else None,
        "split": split,
        "track": track,
        "candidates": [dict(item) for item in (candidates or [_candidate()])],
        "repetitions": repetitions,
        "model_mode": "real",
        "budgets": {"max_trials": max_trials, "per_trial_timeout_s": 120,
                    "concurrency": 1},
        "primary_metric": primary,
        "policy": _policy(),
    }


async def _import_smoke(client: TestClient, csrf: str) -> str:
    response = await client.post(
        f"{API_PREFIX}/samples/import",
        json={"sample_id": "memory-smoke-v1"},
        headers=csrf_headers(csrf),
    )
    assert response.status == 201, await response.text()
    payload = await response.json()
    assert payload["scenario_count"] == 20
    return str(payload["memory_dataset_revision_id"])


async def _create_experiment(
    client: TestClient, csrf: str, body: Mapping[str, Any], key: str
) -> dict[str, Any]:
    response = await client.post(
        f"{API_PREFIX}/memory/experiments",
        json=dict(body),
        headers={**csrf_headers(csrf), "Idempotency-Key": key},
    )
    assert response.status == 202, await response.text()
    return dict(await response.json())


async def _wait_terminal(
    client: TestClient, experiment_id: str, *, timeout_s: float = 180.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = await client.get(f"{API_PREFIX}/memory/experiments/{experiment_id}")
        assert response.status == 200
        detail = dict(await response.json())
        if detail["status"] in TERMINAL:
            return detail
        await asyncio.sleep(0.25)
    raise AssertionError(f"experiment did not settle: {experiment_id}")


def _attempts(repository: EvaluationRepository, detail: Mapping[str, Any]) -> list[dict]:
    rows: list[dict] = []
    for child in detail["child_runs"]:
        rows.extend(
            repository.list_run_attempts(str(child["run_id"]), include_snapshot=True)
        )
    return rows


async def _attempt_events(
    client: TestClient, attempt_id: str, csrf: str
) -> list[dict[str, Any]]:
    response = await client.get(
        f"{API_PREFIX}/memory/attempts/{attempt_id}/events",
        headers=csrf_headers(csrf),
    )
    assert response.status == 200, await response.text()
    payload = await response.json()
    assert payload["sealed"] is True
    return [dict(item["payload"]) for item in payload["data"]]


# ====================================================================== 20 场景


async def test_smoke_20_scenarios_run_through_real_service_and_subprocess(
    tmp_path: Path,
) -> None:
    """内置冒烟 20 场景：真实应用 + 调度器 + Worker 子进程 + 真实记忆库。

    受控脚本只会写一条固定记忆，因此**多数场景不会答对**——本用例要求实验技术
    完成、覆盖完整、逐题可读，同时显式断言 ``qa_accuracy < 1``：受控脚本成绩
    不能被当成真实质量。
    """
    root = _root(tmp_path)
    app, repository, _service = _build_app(root, script=_script())
    async with TestClient(TestServer(app, host="127.0.0.1")) as client:
        csrf = await login(client)
        revision_id = await _import_smoke(client, csrf)

        created = await _create_experiment(
            client, csrf, _experiment_body(
                revision_id, track="end_to_end", split="development"
            ), key="smoke-20",
        )
        assert created["planned_trials"] == 20
        assert len(created["child_runs"]) == 1
        assert created["child_runs"][0]["scenario_ids"] and len(
            created["child_runs"][0]["scenario_ids"]
        ) == 20

        detail = await _wait_terminal(client, created["id"])
        assert detail["status"] == "completed"
        # 状态与「能不能评分」是两件事：这里 20 个 trial 全部技术完成且必需指标可评分。
        assert detail["measurement_status"] == "complete"
        assert detail["progress"]["planned_trials"] == 20
        assert detail["progress"]["attempts"] == 20
        assert detail["progress"]["terminal_attempts"] == 20
        assert detail["progress"]["child_status_counts"] == {"completed": 1}

        attempts = _attempts(repository, detail)
        assert len(attempts) == 20
        assert {item["execution_status"] for item in attempts} == {"succeeded"}
        assert {item["grading_status"] for item in attempts} == {"completed"}
        assert all((item.get("result") or {}).get("memory") for item in attempts)

        metrics = await (
            await client.get(f"{API_PREFIX}/memory/experiments/{created['id']}/metrics")
        ).json()
        rows = {item["id"]: item for item in metrics["metrics"]}
        qa = rows["qa_accuracy"]
        assert qa["status"] == "scored"
        assert qa["value"] is not None and qa["value"] < 1.0, "受控脚本不得被当成达标"
        assert qa["coverage"]["planned"] > 0
        assert qa["coverage"]["scored"] == qa["coverage"]["planned"]
        assert rows["grounded_memory_success"]["status"] == "scored"
        # 未知不当 0：没有真实单价/嵌入时这些指标保持 not_assessable。
        assert rows["estimated_cost"]["status"] == "not_assessable"
        assert rows["embedding_calls"]["status"] in ("not_assessable", "not_applicable")

        # 逐题回放：probe 摘要与 memory.event 投影都来自真实 Trace。
        attempt_id = str(attempts[0]["id"])
        probes = await (
            await client.get(
                f"{API_PREFIX}/memory/attempts/{attempt_id}/probes",
                headers=csrf_headers(csrf),
            )
        ).json()
        assert len(probes["data"]) == 1
        kinds = [item["kind"] for item in await _attempt_events(client, attempt_id, csrf)]
        assert "probe_answered" in kinds
        assert kinds.index("session_closed") < kinds.index("probe_answered")


# ====================================================================== 三轨


def _custom_scenario(**overrides: Any) -> dict[str, Any]:
    """单场景定制数据：closed_world 抽取目标 + 需要注入才能答对的问题。"""
    payload: dict[str, Any] = {
        "schema_version": "memory-scenario/v1",
        "scenario_id": "custom-fault-001",
        "revision": 1,
        "split": "development",
        "template_family": "tf-custom-fault-001",
        "cluster_id": "custom-fault-001",
        "tags": ["correction", "negative"],
        "scope": {
            "subject_id": "synthetic-fault-1",
            "workspace_id": "synthetic-fault-ws-1",
        },
        "events": [
            {
                "type": "session",
                "id": "s1",
                "at": "2026-11-01T09:00:00+08:00",
                "messages": [
                    {"id": "m1", "role": "user",
                     "content": "My car is now parked at Spring Isle Road 7."},
                    {"id": "m2", "role": "assistant", "content": "Noted."},
                    {"id": "m3", "role": "user",
                     "content": "It used to be parked at Old Dock, do not mix them up."},
                ],
            },
            {"type": "settle", "after_session": "s1"},
        ],
        "probes": [
            {
                "id": "q1",
                "question": "Where is my car parked now?",
                "at": "2026-11-02T09:00:00+08:00",
                "fresh_session": True,
            }
        ],
        "gold": {
            "annotation_scope": "closed_world",
            "extraction_facts": [
                {"key": "car_location", "value": ROW_CURRENT,
                 "evidence_message_ids": ["m1"]}
            ],
            "q1": {
                "required_facts": [
                    {"key": "car_location", "value": ROW_CURRENT,
                     "evidence_message_ids": ["m1"]}
                ],
                "forbidden_current_values": [
                    {"key": "car_location", "value": ROW_STALE,
                     "evidence_message_ids": ["m3"]}
                ],
                "answerability": "answerable",
            },
        },
        "reference_state": {
            "version": "reference/v1",
            "facts": [{"key": "car_location", "value": ROW_CURRENT}],
        },
    }
    payload.update(overrides)
    return payload


CURRENT = "Spring Isle Road 7"
STALE = "Old Dock"
ROW_CURRENT = f"car parked at {CURRENT}"
ROW_STALE = f"parked at {STALE}"
SEMANTIC_CAR = json.dumps(
    {
        "memories": [
            {
                "content": ROW_CURRENT,
                "evidence": "My car is now parked at Spring Isle Road 7.",
                "type": "fact",
                "importance": 0.9,
                "subject": "user",
                "predicate": "car_location",
                "tags": ["car"],
            }
        ],
        "experiences": [],
    },
    ensure_ascii=False,
)
SEMANTIC_EMPTY = json.dumps({"memories": [], "experiences": []}, ensure_ascii=False)
EPISODE_CAR = json.dumps(
    {"summary": "user told where the car is parked.", "goal": "record location",
     "importance": 0.6},
    ensure_ascii=False,
)


def _custom_revision(
    service: EvaluationService, *, scenarios: Sequence[Mapping[str, Any]] | None = None
) -> tuple[str, str]:
    suite = service.repository.create_suite(
        {"name": "记忆专项故障集合", "description": "WU-12 受控故障用例",
         "tags": ["memory"], "base_revision": None}
    )
    revision = service.memory_repository.import_revision(
        make_memory_dataset_payload(
            suite_id=str(suite["id"]),
            scenarios=[dict(item) for item in (scenarios or [_custom_scenario()])],
        )
    )["dataset_revision"]
    assert revision["status"] == "ready"
    return str(suite["id"]), str(revision["id"])


async def test_three_tracks_produce_track_specific_evidence(
    tmp_path: Path,
) -> None:
    """三轨各跑一个真实子进程：抽取轨不应答、检索轨不走历史、端到端两段都有。"""
    root = _root(tmp_path)
    app, repository, service = _build_app(root, script=_script())
    suite_id, revision_id = _custom_revision(service)
    async with TestClient(TestServer(app, host="127.0.0.1")) as client:
        csrf = await login(client)
        created = {}
        for track in ("extraction", "retrieval", "end_to_end"):
            created[track] = await _create_experiment(
                client,
                csrf,
                _experiment_body(
                    revision_id, track=track, split="development",
                    scenario_ids=["custom-fault-001"],
                ),
                key=f"track-{track}",
            )
        details = {
            track: await _wait_terminal(client, item["id"])
            for track, item in created.items()
        }
        assert all(detail["status"] == "completed" for detail in details.values())
        assert all(
            detail["progress"]["attempts"] == 1 for detail in details.values()
        )

        events: dict[str, list[str]] = {}
        probes: dict[str, int] = {}
        for track, detail in details.items():
            attempt = _attempts(repository, detail)[0]
            assert attempt["execution_status"] == "succeeded", attempt.get("result")
            attempt_id = str(attempt["id"])
            rows = [item["kind"] for item in await _attempt_events(
                client, attempt_id, csrf
            )]
            events[track] = rows
            probe_payload = await (
                await client.get(
                    f"{API_PREFIX}/memory/attempts/{attempt_id}/probes",
                    headers=csrf_headers(csrf),
                )
            ).json()
            probes[track] = len(probe_payload["data"])

        # 抽取轨：只导出评分材料，不应答 probe（skip 策略如实入账）。
        assert "session_closed" in events["extraction"]
        assert "probe_answered" not in events["extraction"]
        assert probes["extraction"] == 0
        # 检索轨：跳过历史抽取与 settle，用 reference_state 播种后应答。
        assert "session_closed" not in events["retrieval"]
        assert "probe_answered" in events["retrieval"]
        assert probes["retrieval"] == 1
        # 播种真的落进记忆库（否则这条轨只剩空召回，等于没测到检索）。
        assert "injected" in events["retrieval"]
        # 端到端：历史 → settle → 注入 → 应答。
        assert "session_closed" in events["end_to_end"]
        assert "injected" in events["end_to_end"]
        assert "probe_answered" in events["end_to_end"]
        assert probes["end_to_end"] == 1


# ====================================================================== 幂等/重启


async def test_duplicate_create_and_restart_do_not_spawn_extra_runs(
    tmp_path: Path,
) -> None:
    """重复提交与进程重启都只补**缺失**的计划，不重复创建子 run / 试验。"""
    root = _root(tmp_path)
    app, repository, service = _build_app(root, script=_script())
    _suite_id, revision_id = _custom_revision(service)
    body = _experiment_body(
        revision_id, track="end_to_end", split="development",
        scenario_ids=["custom-fault-001"],
    )
    async with TestClient(TestServer(app, host="127.0.0.1")) as client:
        csrf = await login(client)
        first = await _create_experiment(client, csrf, body, key="dup-key")
        replay = await _create_experiment(client, csrf, body, key="dup-key")
        assert replay["id"] == first["id"]
        assert len(replay["child_runs"]) == len(first["child_runs"]) == 1

        detail = await _wait_terminal(client, first["id"])
        attempts_before = len(_attempts(repository, detail))

        # 同 key 异体：拒绝，且不产生第二套计划。
        conflicting = dict(body)
        conflicting["repetitions"] = 2
        response = await client.post(
            f"{API_PREFIX}/memory/experiments",
            json=conflicting,
            headers={**csrf_headers(csrf), "Idempotency-Key": "dup-key"},
        )
        assert response.status == 422, await response.text()

    # 进程重启：新的服务实例在同一个 data_dir/DB 上恢复，不重建已完成计划。
    restarted = EvaluationService(
        repository=repository,
        data_dir=root,
        profiles=_profiles(_script()),
        memory_capabilities=_capabilities(),
    )
    resumed = restarted.memory_experiment_coordinator().resume_all()
    # 已完成实验不在恢复集合里：重启不会重放已结算的计划。
    assert [item["experiment"]["id"] for item in resumed] == []
    after = restarted.memory_experiment_read(first["id"])
    assert len(after["child_runs"]) == 1
    assert after["planned_trials"] == 1
    assert len(_attempts(repository, after)) == attempts_before
    assert after["status"] == "completed"


# ====================================================================== 取消


async def test_cancel_stops_remaining_batches_and_does_not_resurrect(
    tmp_path: Path,
) -> None:
    """核心 120 场景 3 批：取消后停止新批，重启也不会把剩余批复活。"""
    root = _root(tmp_path)
    app, repository, service = _build_app(root, script=_script())
    async with TestClient(TestServer(app, host="127.0.0.1")) as client:
        csrf = await login(client)
        response = await client.post(
            f"{API_PREFIX}/samples/import",
            json={"sample_id": "memory-core-v1"},
            headers=csrf_headers(csrf),
        )
        assert response.status == 201, await response.text()
        revision_id = str((await response.json())["memory_dataset_revision_id"])

        created = await _create_experiment(
            client, csrf,
            _experiment_body(
                revision_id, track="end_to_end", split="regression", max_trials=200
            ),
            key="cancel-120",
        )
        assert created["planned_trials"] == 120
        assert len(created["child_runs"]) == 3, "120 场景应按 40/批切 3 批"
        assert all(len(item["scenario_ids"]) == 40 for item in created["child_runs"])

        # 等到真有一条 attempt 在跑再取消（此前的批次要回收）。
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            detail = await (
                await client.get(f"{API_PREFIX}/memory/experiments/{created['id']}")
            ).json()
            if detail["progress"]["attempts"] > 0:
                break
            await asyncio.sleep(0.2)
        assert detail["progress"]["attempts"] > 0

        response = await client.post(
            f"{API_PREFIX}/memory/experiments/{created['id']}/cancel",
            json={},
            headers=csrf_headers(csrf),
        )
        assert response.status == 202, await response.text()
        cancelled = dict(await response.json())
        assert cancelled["status"] == "cancelled"
        assert cancelled["measurement_status"] == "incomplete"
        attempts_at_cancel = len(_attempts(repository, cancelled))

        # 冷却后确认没有新批被领取（组闸生效）。
        await asyncio.sleep(3.0)
        frozen = await (
            await client.get(f"{API_PREFIX}/memory/experiments/{created['id']}")
        ).json()
        assert frozen["status"] == "cancelled"
        assert len(_attempts(repository, frozen)) == attempts_at_cancel

        # 已终态再取消：200（幂等），不再是 202。
        response = await client.post(
            f"{API_PREFIX}/memory/experiments/{created['id']}/cancel",
            json={},
            headers=csrf_headers(csrf),
        )
        assert response.status == 200

    resumed = service.memory_experiment_coordinator().resume(created["id"])
    assert resumed["experiment"]["status"] == "cancelled"
    assert len(resumed["child_runs"]) == 3
    assert len(_attempts(repository, resumed)) == attempts_at_cancel


# ====================================================================== 120×3


async def test_core_120_by_3_batches_and_group_budget(tmp_path: Path) -> None:
    """120 场景 × 3 候选 × 3 重复 = 1080 trials / 9 批；默认预算必须拒绝。"""
    root = _root(tmp_path)
    _app, _repository, service = _build_app(root, script=_script())
    imported = service.import_sample("memory-core-v1")
    revision_id = str(imported["memory_dataset_revision_id"])
    revision = service.memory_repository.get_dataset_revision(revision_id)
    suite_id = str(revision["suite_id"])
    coordinator: MemoryExperimentCoordinator = service.memory_experiment_coordinator()
    scenario_ids = [
        str(item["scenario_id"])
        for item in service.memory_scenario_list(revision_id)
    ]
    assert len(scenario_ids) == 120
    assert [len(batch) for batch in plan_batches(scenario_ids, 3)] == [40, 40, 40]

    def request(max_trials: int | None) -> dict[str, Any]:
        candidates = [
            _candidate(id=f"cand-{index}", name=f"候选 {index}") for index in range(3)
        ]
        body = _experiment_body(
            revision_id,
            track="end_to_end",
            split="regression",
            candidates=candidates,
            repetitions=3,
            max_trials=max_trials or 200,
        )
        body["suite_id"] = suite_id
        if max_trials is None:
            body["budgets"] = {"per_trial_timeout_s": 120, "concurrency": 1}
        return body

    # 默认上限 200：1080 trials 必须在入队前被拒绝（不是跑一半才超支）。
    with pytest.raises(MemoryBudgetExceeded):
        coordinator.create_experiment(request(None), enqueue=False)

    created = coordinator.create_experiment(request(1080), enqueue=False)
    experiment = created["experiment"]
    assert experiment["planned_trials"] == 1080
    assert len(created["child_runs"]) == 9
    batches = {}
    for child in created["child_runs"]:
        batches.setdefault(str(child["candidate_id"]), []).append(
            (int(child["batch_index"]), len(child["scenario_ids"]))
        )
    assert set(batches) == {"cand-0", "cand-1", "cand-2"}
    for rows in batches.values():
        assert sorted(rows) == [(0, 40), (1, 40), (2, 40)]
    # 未入队：没有任何真实 run 被创建。
    assert service.memory_experiment_read(str(experiment["id"]))["progress"][
        "attempts"
    ] == 0


# ====================================================================== 四种故障


class _AnswerParserObserver(ZhixuWorkerObserver):
    """固定词表回答解析器（真实解析器的替身）。

    真实后端会把回答解析成原子事实；这里用「回答里出现哪个已知值」的确定性
    词表代替，用来把「回答误用」打到答案层，而不是靠 substring 当语义判定。
    """

    TABLE = ((CURRENT, "car_location", ROW_CURRENT), (STALE, "car_location", ROW_STALE))

    def parse_answer_facts(self, answer: str) -> list[MemoryFact] | None:
        return [
            MemoryFact(key=key, value=value)
            for needle, key, value in self.TABLE
            if needle in answer
        ]


class _FaultAdapter:
    """在状态机接缝注入受控故障；其余调用原样转发给真实 adapter。"""

    def __init__(self, inner: ZhixuMemoryAdapter, fault: str) -> None:
        self.inner = inner
        self.fault = fault

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def answer(self, probe: Any) -> Any:
        execution = await self.inner.answer(probe)
        if self.fault == "recall_omission":
            return execution.model_copy(update={"recalled": [], "injected_evidence": []})
        if self.fault == "injection_truncated":
            return execution.model_copy(update={"injected_evidence": []})
        return execution


def _fault_provider(fault: str, *, track: str) -> ControlledProvider:
    semantic = SEMANTIC_EMPTY if fault == "extraction_omission" else SEMANTIC_CAR
    answer = (
        f"My car is parked at {CURRENT}."
        if fault == "healthy"
        else f"My car is parked at {STALE}."
        if fault == "answer_misuse"
        else "I do not know."
    )
    texts = [semantic, EPISODE_CAR]
    if track != "extraction":
        texts.append(answer)
    steps = tuple(
        ScriptStep(json_text=part) if part.startswith("{") else ScriptStep(text=part)
        for part in texts
    )
    return ControlledProvider(steps=steps)


async def _run_fault_trial(root: Path, fault: str, *, track: str) -> Any:
    """真实 adapter + 真实判定：只在接缝处注入受控故障。

    返回 ``MemoryTrialGrade``（真实 ``grade_memory_trial`` 的产物），调用方再把它
    组装成服务层同形状的 attempt 行，喂给真实比较引擎与 HTML 报告。
    """
    scenario = MemoryScenario.model_validate(_custom_scenario())
    execution = MemoryExecutionInput.model_validate(
        {**execution_fields(_custom_scenario()), "track": track}
    )
    provider = _fault_provider(fault, track=track)
    runtime = SimpleNamespace(
        provider=provider,
        model=provider.model,
        generation=GenerationSettings(),
    )
    adapter = ZhixuMemoryAdapter(
        trial_root=root / "trials" / f"{track}-{fault}",
        scope=scenario.scope,
        runtime=runtime,
    )
    observer = _AnswerParserObserver(adapter)
    run = await run_memory_scenario(
        execution, _FaultAdapter(adapter, fault), observer=observer
    )
    index = observer.injected_fact_index()
    probes = []
    for outcome in run.trial.probes:
        injected = [
            index[str(item)]
            for item in outcome.execution.injected_evidence
            if str(item) in index
        ]
        probes.append(
            outcome.model_copy(
                update={
                    "injected_facts": injected,
                    "asserted_facts": observer.parse_answer_facts(
                        outcome.execution.answer or ""
                    ),
                    "answer_facts_parsed": outcome.execution.answer is not None,
                }
            )
        )
    trial = run.trial.model_copy(update={"tags": list(scenario.tags), "probes": probes})
    return grade_memory_trial(
        execution=trial, gold=scenario.gold, policy=default_grading_policy()
    )


def _attempt_row(
    scenario_id: str, revision: int, graded: Any, *, track: str, required: Sequence[str]
) -> dict[str, Any]:
    """服务层同形状的 attempt 行：``result.memory.metrics`` 是整份 trial 判定。"""
    return {
        "id": str(uuid.uuid4()),
        "execution_status": "succeeded",
        "grading_status": "completed",
        "case_snapshot": {
            "memory_scenario_ref": {
                "dataset_revision_id": str(uuid.uuid4()),
                "scenario_id": scenario_id,
                "revision": revision,
            },
            "memory_required_metrics": list(required),
            "memory_track": track,
        },
        "result": {
            "memory": {
                "scenario_id": scenario_id,
                "revision": revision,
                "track": track,
                "metrics": graded.model_dump(mode="json"),
            }
        },
    }


def _metric_row(html: str, metric_id: str) -> str:
    """取出报告里某个指标所在行（到下一个 ``<tr>`` 为止）。"""
    marker = f'data-metric="{metric_id}"'
    start = html.find(marker)
    assert start >= 0, f"report lost metric row: {metric_id}"
    end = html.find("<tr>", start)
    return html[start : end if end > 0 else len(html)]


def _create_plan(
    coordinator: MemoryExperimentCoordinator,
    *,
    suite_id: str,
    revision_id: str,
    track: str,
    candidate_id: str,
) -> dict[str, Any]:
    request = _experiment_body(
        revision_id, track=track, split="development",
        candidates=[_candidate(id=candidate_id, name=candidate_id)],
        scenario_ids=["custom-fault-001"],
        max_trials=10,
    )
    request["suite_id"] = suite_id
    return coordinator.create_experiment(request, enqueue=False)


async def test_four_controlled_faults_are_localized_in_comparison_report(
    tmp_path: Path,
) -> None:
    """四种受控故障分别落在抽取 / 召回 / 注入 / 回答四层，报告要能分辨。

    允许断言受控故障产生失败；这些数字来自合成场景 + 脚本化模型，**不能**据此
    声称真实模型质量。报告定位靠的是「哪一行的差值变了」，不是平均分高低。
    """
    root = _root(tmp_path)
    _app, repository, service = _build_app(root, script=None)
    suite_id, revision_id = _custom_revision(service)
    coordinator = service.memory_experiment_coordinator()

    plans = {
        ("extraction", "baseline"): _create_plan(
            coordinator, suite_id=suite_id, revision_id=revision_id,
            track="extraction", candidate_id="baseline",
        ),
        ("extraction", "fault"): _create_plan(
            coordinator, suite_id=suite_id, revision_id=revision_id,
            track="extraction", candidate_id="fault",
        ),
        ("end_to_end", "baseline"): _create_plan(
            coordinator, suite_id=suite_id, revision_id=revision_id,
            track="end_to_end", candidate_id="baseline",
        ),
    }
    for fault in ("recall_omission", "injection_truncated", "answer_misuse"):
        plans[("end_to_end", fault)] = _create_plan(
            coordinator, suite_id=suite_id, revision_id=revision_id,
            track="end_to_end", candidate_id=fault,
        )
    for key, plan in list(plans.items()):
        experiment_id = str(plan["experiment"]["id"])
        repository.set_memory_experiment_status(experiment_id, "completed")
        # 重新读回：比较引擎读的是库里的 manifest/状态，不是创建时的内存副本。
        plans[key] = service.memory_repository.get_experiment(experiment_id)

    required = {
        "extraction": ["extraction_recall"],
        "end_to_end": ["qa_accuracy", "grounded_memory_success"],
    }
    grades = {
        ("extraction", "baseline"): await _run_fault_trial(
            root, "healthy", track="extraction"
        ),
        ("extraction", "fault"): await _run_fault_trial(
            root, "extraction_omission", track="extraction"
        ),
        ("end_to_end", "baseline"): await _run_fault_trial(
            root, "healthy", track="end_to_end"
        ),
        ("end_to_end", "recall_omission"): await _run_fault_trial(
            root, "recall_omission", track="end_to_end"
        ),
        ("end_to_end", "injection_truncated"): await _run_fault_trial(
            root, "injection_truncated", track="end_to_end"
        ),
        ("end_to_end", "answer_misuse"): await _run_fault_trial(
            root, "answer_misuse", track="end_to_end"
        ),
    }
    attempts = {
        key: [
            _attempt_row(
                "custom-fault-001", 1, graded,
                track=key[0], required=required[key[0]],
            )
        ]
        for key, graded in grades.items()
    }

    def compare(track: str, candidate_key: str) -> str:
        result = compare_memory_experiments(
            baseline_experiment=plans[(track, "baseline")],
            candidate_experiment=plans[(track, candidate_key)],
            baseline_attempts=attempts[(track, "baseline")],
            candidate_attempts=attempts[(track, candidate_key)],
            baseline_candidate_id="baseline",
            candidate_candidate_id=candidate_key,
        )
        return render_memory_comparison_report(result.to_dict())

    # ---- 抽取漏项：抽取轨 recall/f1 掉到 0，precision 明确未知。
    html_extraction = compare("extraction", "fault")
    recall_row = _metric_row(html_extraction, "extraction_f1")
    assert "0.0%" in recall_row and "-100.0 个百分点" in recall_row
    precision_row = _metric_row(html_extraction, "extraction_precision")
    assert "未知" in precision_row
    assert "退化" in html_extraction
    # 抽取漏项不会把回答层指标算成 0：该轨不应答 probe，缺证保持未知。
    assert 'data-metric="qa_accuracy"' not in html_extraction

    # ---- 召回漏项：召回与注入同时掉到 0。
    html_recall = compare("end_to_end", "recall_omission")
    assert "-100.0 个百分点" in _metric_row(html_recall, "evidence_recall_at_k")
    assert "-100.0 个百分点" in _metric_row(html_recall, "injection_recall")

    # ---- 注入预算截断：召回到了，注入为 0（只有注入行掉）。
    html_injection = compare("end_to_end", "injection_truncated")
    assert "0.0 个百分点" in _metric_row(html_injection, "evidence_recall_at_k")
    assert "-100.0 个百分点" in _metric_row(html_injection, "injection_recall")

    # ---- 回答误用：召回与注入都正常，答案用了被禁止的旧值。
    html_answer = compare("end_to_end", "answer_misuse")
    assert "0.0 个百分点" in _metric_row(html_answer, "evidence_recall_at_k")
    assert "0.0 个百分点" in _metric_row(html_answer, "injection_recall")
    stale_row = _metric_row(html_answer, "stale_use_rate")
    assert "-100.0 个百分点" in stale_row, stale_row
    assert "FORBIDDEN_VALUE_ASSERTED" in json.dumps(
        [item.reasons for item in grades[("end_to_end", "answer_misuse")].probe_results],
        ensure_ascii=False,
    )

    # 四种故障的「哪一行掉」互不相同：报告不是只给一个平均分。
    signatures = {
        "extraction": "extraction_f1" in html_extraction,
        "recall": "evidence_recall_at_k" in html_recall,
        "injection": "injection_recall" in html_injection,
        "answer": "stale_use_rate" in html_answer,
    }
    assert all(signatures.values())
    # 报告继续声明降低口径：区间不输出、逐题不因分页缩小分母。
    assert "不输出区间" in html_answer or "独立 cluster" in html_answer


# ================================================================== 无记忆基线


async def test_no_persistent_memory_candidate_really_disables_persistence(
    tmp_path: Path,
) -> None:
    """``no_persistent_memory`` 必须在真实子进程里关闭持久读写（不是只写在报告里）。

    这条同时守住「生效候选」的接线：缺它时记忆照写照查，基线会被当成能力差异。
    """
    root = _root(tmp_path)
    app, repository, _service = _build_app(root, script=_script())
    async with TestClient(TestServer(app, host="127.0.0.1")) as client:
        csrf = await login(client)
        revision_id = await _import_smoke(client, csrf)
        candidate = _candidate(id="baseline", mode="no_persistent_memory")
        created = await _create_experiment(
            client, csrf,
            _experiment_body(
                revision_id, track="end_to_end", split="development",
                scenario_ids=["smoke-stable-fact-01"], candidates=[candidate],
            ),
            key="no-memory",
        )
        detail = await _wait_terminal(client, created["id"])
        assert detail["status"] == "completed"
        attempts = _attempts(repository, detail)
        assert len(attempts) == 1
        assert attempts[0]["execution_status"] == "succeeded"

        run = repository.get_run(str(detail["child_runs"][0]["run_id"]))
        frozen = run["snapshot"]["memory_experiment"]
        assert frozen["candidate"]["mode"] == "no_persistent_memory", (
            "子 run 快照必须冻结生效候选"
        )

        events = await _attempt_events(client, str(attempts[0]["id"]), csrf)
        kinds = [item["kind"] for item in events]
        assert "extraction_skipped" in kinds
        skipped = next(item for item in events if item["kind"] == "extraction_skipped")
        assert "memory_disabled" in str(skipped.get("detail"))
        injected = next(item for item in events if item["kind"] == "injected")
        assert list(injected.get("origin_refs") or []) == []

        memory = attempts[0]["result"]["memory"]["metrics"]
        rows = {item["id"]: item for item in memory["metrics"]}
        assert rows["qa_accuracy"]["status"] == "scored"
        assert rows["qa_accuracy"]["value"] == 0.0
        probe = memory["probe_results"][0]
        assert probe["grounded_memory_success"] is False


async def test_setup_failure_is_measured_not_hidden(tmp_path: Path) -> None:
    """装配失败（profile 缺模型）要成为可读的失败账目，而不是空成绩。"""
    root = _root(tmp_path)
    app, repository, service = _build_app(
        root, script=None, config={"agents": {"defaults": {"model": ""}}}
    )
    _suite_id, revision_id = _custom_revision(service)
    async with TestClient(TestServer(app, host="127.0.0.1")) as client:
        csrf = await login(client)
        created = await _create_experiment(
            client, csrf,
            _experiment_body(
                revision_id, track="end_to_end", split="development",
                scenario_ids=["custom-fault-001"],
            ),
            key="setup-failure",
        )
        detail = await _wait_terminal(client, created["id"])
        assert detail["status"] == "completed"
        # 技术完成不等于可评分：这里必须是 incomplete。
        assert detail["measurement_status"] == "incomplete"
        assert detail["coverage"]["scored"] == 0
        attempts = _attempts(repository, detail)
        assert len(attempts) == 1
        assert attempts[0]["execution_status"] == "setup_error"
        assert attempts[0]["grading_status"] == "not_applicable"
        errors = attempts[0]["result"]["errors"]
        assert errors and errors[0]["phase"] == "setup"

        metrics = await (
            await client.get(f"{API_PREFIX}/memory/experiments/{created['id']}/metrics")
        ).json()
        assert metrics["metrics"] == []
        assert "no_attempts" not in metrics["coverage"]["reasons"]

