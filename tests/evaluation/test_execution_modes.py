"""T05 执行模式与环境依赖：限额真的生效、工具模式不混、缺依赖不假装成功。

本文件验证的是**运行时口径**，不是模型能力：

- 限额：run 级覆盖只能收紧用例限制，收敛结果必须落到 Worker 真正使用的配置上；
- 工具模式：live 模式不得使用 exec/exec_session/apply_patch，冲突在用例入库前就被拒绝；
- 环境依赖：用例声明的输入素材缺失时报错，不能用空文件假装任务成功；
- Skill：物化进 attempt workspace 的技能必须留下清单与内容哈希。

真实模型能力不在本文件结论范围内。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.schemas import CaseWrite, RunCreate
from nanobot.evaluation.services import EvaluationService, Profile

from .conftest import (
    make_case_payload,
    make_run_payload,
    make_suite_payload,
)


@pytest.fixture(autouse=True)
def _restore_global_config_path() -> Any:
    """create_attempt 会切换全局配置路径上下文；用完必须还原，别影响后续用例。"""
    from nanobot.config import loader

    previous = loader.get_config_path()
    yield
    loader.set_config_path(previous)


@pytest.fixture
def service(
    evaluation_repository: EvaluationRepository, tmp_path: Path
) -> EvaluationService:
    return EvaluationService(
        repository=evaluation_repository,
        data_dir=tmp_path / "service",
        profiles=(
            Profile(
                id="agent-1",
                label="受控执行器",
                provider="controlled",
                model="eval-model",
                kind="agent",
                bundle={"config": {"providers": {"controlled": {"api_key": "sk-x"}}}},
            ),
        ),
    )


def _seed_case(
    repository: EvaluationRepository,
    *,
    max_iterations: int = 12,
    timeout_s: int = 120,
) -> tuple[dict[str, Any], dict[str, Any]]:
    suite = repository.create_suite(make_suite_payload())
    payload = make_case_payload(suite_id=suite["id"], name="限额收敛用例")
    payload["execution"]["max_iterations"] = max_iterations
    payload["execution"]["timeout_s"] = timeout_s
    case = repository.save_case(payload)
    return suite, case


def _run_request(
    case_ids: list[str], suite_id: str, limits: dict[str, Any]
) -> RunCreate:
    return RunCreate.model_validate(
        make_run_payload(
            case_ids,
            suite_id=suite_id,
            agent_profile_id="agent-1",
            limits=limits,
        )
    )


# ----------------------------------------------------------------- 工具模式


@pytest.mark.parametrize("tool_name", ["exec", "exec_session", "apply_patch"])
def test_live_mode_rejects_forbidden_tools(tool_name: str) -> None:
    """live 模式不得选用越权工具：拒绝发生在用例入库前，不是运行期才炸。"""
    payload = make_case_payload()
    payload["execution"]["tool_mode"] = "live"
    payload["execution"]["allowed_tools"] = ["read_file", tool_name]
    with pytest.raises(ValidationError) as excinfo:
        CaseWrite.model_validate(payload)
    assert "TOOL_NOT_ALLOWED" in str(excinfo.value)


def test_live_mode_allows_controlled_readonly_tools() -> None:
    payload = make_case_payload()
    payload["execution"]["tool_mode"] = "live"
    payload["execution"]["allowed_tools"] = ["read_file", "list_dir", "web_fetch"]
    case = CaseWrite.model_validate(payload)
    assert case.execution.tool_mode == "live"


# ----------------------------------------------------------------- 环境依赖


def test_case_dependencies_must_be_provided_by_fixtures() -> None:
    payload = make_case_payload(dependencies=["brief.md"])
    with pytest.raises(ValidationError) as excinfo:
        CaseWrite.model_validate(payload)
    assert "CASE_DEPENDENCY_MISSING" in str(excinfo.value)


def test_case_dependencies_satisfied_by_fixture_are_kept() -> None:
    payload = make_case_payload(dependencies=["input.txt"])
    case = CaseWrite.model_validate(payload)
    assert case.dependencies == ["input.txt"]


# ----------------------------------------------------------------- 限额收敛


def test_run_limits_tighten_case_limits(
    evaluation_repository: EvaluationRepository, service: EvaluationService
) -> None:
    suite, case = _seed_case(evaluation_repository, max_iterations=12, timeout_s=120)
    run = service.create_run(
        _run_request(
            [case["id"]],
            suite["id"],
            {"concurrency": 1, "max_iterations": 4, "timeout_s": 30},
        ),
        idempotency_key="limits-tighten",
        scope="local-user",
    )
    attempts = evaluation_repository.list_run_attempts(run["id"], include_snapshot=True)
    execution = attempts[0]["case_snapshot"]["execution"]
    assert execution["max_iterations"] == 4
    assert execution["timeout_s"] == 30


def test_run_limits_cannot_widen_case_limits(
    evaluation_repository: EvaluationRepository, service: EvaluationService
) -> None:
    suite, case = _seed_case(evaluation_repository, max_iterations=6, timeout_s=60)
    run = service.create_run(
        _run_request(
            [case["id"]],
            suite["id"],
            {"concurrency": 1, "max_iterations": 20, "timeout_s": 300},
        ),
        idempotency_key="limits-not-widen",
        scope="local-user",
    )
    attempts = evaluation_repository.list_run_attempts(run["id"], include_snapshot=True)
    execution = attempts[0]["case_snapshot"]["execution"]
    assert execution["max_iterations"] == 6
    assert execution["timeout_s"] == 60


def test_unset_run_limits_keep_case_limits(
    evaluation_repository: EvaluationRepository, service: EvaluationService
) -> None:
    suite, case = _seed_case(evaluation_repository, max_iterations=9, timeout_s=90)
    run = service.create_run(
        _run_request([case["id"]], suite["id"], {"concurrency": 1}),
        idempotency_key="limits-default",
        scope="local-user",
    )
    attempts = evaluation_repository.list_run_attempts(run["id"], include_snapshot=True)
    execution = attempts[0]["case_snapshot"]["execution"]
    assert execution["max_iterations"] == 9
    assert execution["timeout_s"] == 90


# ----------------------------------------------------------------- 生效限额与技能


def _fake_provider() -> Any:
    from unittest.mock import MagicMock

    from nanobot.providers.base import GenerationSettings

    provider = MagicMock()
    provider.get_default_model.return_value = "eval-model"
    provider.generation = GenerationSettings()
    return provider


def _boot_payload(tmp_path: Path, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "boot",
        "role": "agent",
        "protocol_version": 1,
        "attempt_id": "00000000-0000-4000-8000-0000000000aa",
        "runtime_root": str(tmp_path / "runtime"),
        "workspace": str(tmp_path / "workspace"),
        "profile_bundle": {
            "config": {
                "agents": {"defaults": {"model": "eval-model", "provider": "openai"}},
            }
        },
        "case_snapshot": make_case_payload(),
    }
    payload.update(extra)
    return payload


def _team_snapshot(team_id: str = "creative") -> dict[str, Any]:
    timestamp = "2026-01-01T00:00:00+00:00"

    def profile(agent_id: str, name: str) -> dict[str, Any]:
        return {
            "id": agent_id,
            "updatedAt": timestamp,
            "prompt": f"{name} 的提示词",
            "name": name,
            "modelId": None,
            "tools": {"mode": "all", "entries": []},
            "skills": {"mode": "include", "entries": []},
            "subAgents": {"mode": "all", "entries": []},
        }

    return {
        "kind": "team",
        "id": team_id,
        "team": {
            "revision": 1,
            "definition": {
                "id": team_id,
                "revision": 1,
                "name": "创意组",
                "description": "",
                "instructions": "",
                "leaderAgentId": "leader",
                "members": [{"memberId": "writer", "agentId": "writer", "role": "写作"}],
                "limits": {},
                "archived": False,
                "createdAt": timestamp,
                "updatedAt": timestamp,
            },
            "leader": profile("leader", "领队"),
            "members": [
                {
                    "member": {"memberId": "writer", "agentId": "writer", "role": "写作"},
                    "profile": profile("writer", "写手"),
                }
            ],
        },
    }


async def test_worker_applies_tighter_run_iteration_limit(tmp_path: Path) -> None:
    """run 级限额更严时，Worker 真正使用的迭代上限必须跟着收紧。"""
    from nanobot.evaluation.runtime_factory import create_attempt
    from nanobot.evaluation.schemas import AgentWorkerBoot

    boot = AgentWorkerBoot.model_validate(
        _boot_payload(
            tmp_path,
            limits={"concurrency": 1, "max_iterations": 2, "timeout_s": 30},
        )
    )
    runtime = await create_attempt(boot, provider=_fake_provider())
    try:
        # pyright: ignore[reportPrivateUsage]
        assert runtime.bot._loop.max_iterations == 2
        assert runtime.capability["effective_limits"]["max_iterations"] == 2
    finally:
        await runtime.aclose()


async def test_skill_materialization_records_content_digest(tmp_path: Path) -> None:
    """技能必须真的物化到 attempt workspace，并留下内容哈希作为证据。"""
    from nanobot.evaluation.runtime_factory import create_attempt
    from nanobot.evaluation.schemas import AgentWorkerBoot

    content = "---\nname: demo\ndescription: 演示技能\n---\n正文\n"
    boot = AgentWorkerBoot.model_validate(
        _boot_payload(
            tmp_path,
            target={
                "kind": "skill",
                "id": "demo",
                "skill": {"name": "demo", "files": {"skills/demo/SKILL.md": content}},
            },
        )
    )
    runtime = await create_attempt(boot, provider=_fake_provider())
    try:
        skills = runtime.capability["skills"]
        assert skills["requested"] == ["demo"]
        materialized = skills["materialized"]
        assert materialized[0]["name"] == "demo"
        assert len(materialized[0]["files"][0]["sha256"]) == 64
        assert (tmp_path / "workspace" / "skills" / "demo" / "SKILL.md").is_file()
    finally:
        await runtime.aclose()


async def test_team_turn_stops_at_timeout(tmp_path: Path) -> None:
    """回合超时要真的停住：不能等回合自然结束再报成功。"""
    from nanobot.evaluation.team_runtime import (
        IsolatedTeamRuntime,
        TeamRunRequest,
        build_frozen_team,
    )

    async def turn_runner(*, turn_index: int, prompt: str, request: TeamRunRequest) -> str:
        await asyncio.sleep(5)
        return "不该出现的结论"

    runtime = IsolatedTeamRuntime(
        request=TeamRunRequest(
            attempt_id="00000000-0000-4000-8000-0000000000bb",
            team=build_frozen_team(_team_snapshot()),
            turns=("跑一下",),
            workspace=tmp_path / "attempt",
            timeout_s=1,
        ),
        turn_runner=turn_runner,
    )
    outcome = await runtime.execute()
    assert outcome.status == "timed_out"
    assert outcome.errors[0]["code"] == "TEAM_TURN_TIMEOUT"


async def test_missing_required_input_fails_at_setup(tmp_path: Path) -> None:
    """故意缺输入的用例必须在 setup 阶段报缺，不能用空文件假装任务成功。"""
    from nanobot.evaluation.runtime_factory import create_attempt
    from nanobot.evaluation.schemas import AgentWorkerBoot

    case_payload = make_case_payload(required_inputs=["data/missing.csv"])
    boot = AgentWorkerBoot.model_validate(_boot_payload(tmp_path, case_snapshot=case_payload))
    with pytest.raises(ValueError) as excinfo:
        await create_attempt(boot, provider=_fake_provider())
    assert "CASE_INPUT_MISSING" in str(excinfo.value)
