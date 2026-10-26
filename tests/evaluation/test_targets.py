"""运行目标（TargetRef）与执行注入的契约测试。

WU-03 落 schemas 的 ``target`` / ``run_timeout_s``；WU-04 落 Agent/Skill 目标冻结、
worker 物化与 agent_id 注入；WU-05 落团队目标的进程内执行。四类目标共用本文件。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.schemas import RunCreate

from .conftest import make_case_payload

TIMESTAMP = "2026-01-01T00:00:00+00:00"


def _profile(agent_id: str, name: str) -> dict:
    return {
        "id": agent_id,
        "updatedAt": TIMESTAMP,
        "prompt": f"{name} 的提示词",
        "name": name,
        "modelId": None,
        "tools": {"mode": "all", "entries": []},
        "skills": {"mode": "include", "entries": []},
        "subAgents": {"mode": "all", "entries": []},
    }


def _team_snapshot(team_id: str) -> dict:
    """团队冻结快照替身：不碰日常团队库，也不依赖真实团队记录。"""
    return {
        "definition": {
            "id": team_id,
            "revision": 3,
            "name": "创意组",
            "description": "",
            "instructions": "",
            "leaderAgentId": "leader",
            "members": [{"memberId": "writer", "agentId": "writer", "role": "写作"}],
            "limits": {},
            "archived": False,
            "createdAt": TIMESTAMP,
            "updatedAt": TIMESTAMP,
        },
        "leader": _profile("leader", "领队"),
        "members": [
            {
                "member": {"memberId": "writer", "agentId": "writer", "role": "写作"},
                "profile": _profile("writer", "写手"),
            }
        ],
    }


class _StubRuntime:
    """固定结果的运行时替身：用来覆盖 partial / 非终态等分支。"""

    def __init__(self, outcome) -> None:
        self._outcome = outcome

    async def execute(self):
        return self._outcome


def _team_factory(*, status: str = "succeeded", answer: str = "团队结论 alpha", seen=None):
    """隔离团队运行时工厂替身：只回放结论，不触网、不碰日常团队库。"""
    from nanobot.evaluation.team_runtime import IsolatedTeamRuntime, TeamRunOutcome

    async def turn_runner(*, turn_index, prompt, request):
        if seen is not None:
            seen.append((turn_index, prompt))
        return answer

    def factory(*, request, events=None):
        if status != "succeeded":
            return _StubRuntime(
                TeamRunOutcome(status=status, final_answer=answer)
            )
        return IsolatedTeamRuntime(
            request=request, turn_runner=turn_runner, events=events
        )

    return factory

# --------------------------------------------------------------------- WU-03 schemas


def test_run_create_accepts_target_and_timeout() -> None:
    body = RunCreate.model_validate({
        "suite_id": "00000000-0000-4000-8000-000000000001",
        "case_ids": ["00000000-0000-4000-8000-000000000002"],
        "agent_profile_id": "model:fast",
        "target": {"kind": "agent", "id": "writer"},
        "run_timeout_s": 600,
    })
    assert body.target is not None and body.target.kind == "agent"
    assert body.target.id == "writer"
    assert body.run_timeout_s == 600


def test_run_create_allows_omitting_target_and_timeout() -> None:
    body = RunCreate.model_validate({
        "suite_id": "00000000-0000-4000-8000-000000000001",
        "case_ids": ["00000000-0000-4000-8000-000000000002"],
        "agent_profile_id": "controlled-agent",
    })
    assert body.target is None
    # None = 不限时；到点才由调度器批量停止（WU-03）。
    assert body.run_timeout_s is None


@pytest.mark.parametrize("value", [4, 86401, "600", 600.5, True])
def test_run_create_rejects_out_of_range_timeout(value: object) -> None:
    with pytest.raises(ValidationError):
        RunCreate.model_validate({
            "suite_id": "00000000-0000-4000-8000-000000000001",
            "case_ids": ["00000000-0000-4000-8000-000000000002"],
            "agent_profile_id": "controlled-agent",
            "run_timeout_s": value,
        })


def test_run_create_rejects_unknown_target_kind() -> None:
    with pytest.raises(ValidationError):
        RunCreate.model_validate({
            "suite_id": "00000000-0000-4000-8000-000000000001",
            "case_ids": ["00000000-0000-4000-8000-000000000002"],
            "agent_profile_id": "model:fast",
            "target": {"kind": "keyboard", "id": "x"},
        })


def test_run_create_rejects_blank_target_id() -> None:
    with pytest.raises(ValidationError):
        RunCreate.model_validate({
            "suite_id": "00000000-0000-4000-8000-000000000001",
            "case_ids": ["00000000-0000-4000-8000-000000000002"],
            "agent_profile_id": "model:fast",
            "target": {"kind": "agent", "id": ""},
        })


# --------------------------------------------------------------------- WU-03 快照冻结


@pytest.fixture
def repository(evaluation_repository: EvaluationRepository) -> EvaluationRepository:
    return evaluation_repository


def test_create_run_freezes_target_and_timeout(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    from nanobot.evaluation.services import EvaluationService
    from tests.evaluation.conftest import make_run_payload, make_suite_payload

    service = EvaluationService(
        repository=repository, data_dir=tmp_path,
        team_snapshot_provider=_team_snapshot,
    )
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"]))
    run = service.create_run(
        RunCreate.model_validate({
            **make_run_payload([case["id"]], suite_id=suite["id"],
                               agent_profile_id="controlled-agent"),
            "target": {"kind": "team", "id": "creative"},
            "run_timeout_s": 300,
        }),
        idempotency_key="target-1", scope="t",
    )
    snapshot = repository.get_run(run["id"])["snapshot"]
    # 团队目标在入队时冻结：快照里必须有 revision、Leader 与成员档案，
    # 之后源团队被改也不影响本次运行。
    target = snapshot["target"]
    assert target["kind"] == "team" and target["id"] == "creative"
    assert target["team"]["revision"] == 3
    assert target["team"]["leader"]["id"] == "leader"
    assert target["team"]["members"][0]["member"]["memberId"] == "writer"
    assert snapshot["run_timeout_s"] == 300


def test_create_run_without_target_keeps_snapshot_shape(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    from nanobot.evaluation.services import EvaluationService
    from tests.evaluation.conftest import make_run_payload, make_suite_payload

    service = EvaluationService(repository=repository, data_dir=tmp_path)
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"]))
    run = service.create_run(
        RunCreate.model_validate(
            make_run_payload([case["id"]], suite_id=suite["id"],
                             agent_profile_id="controlled-agent")
        ),
        idempotency_key="target-2", scope="t",
    )
    snapshot = repository.get_run(run["id"])["snapshot"]
    assert snapshot["target"] is None
    assert snapshot["run_timeout_s"] is None


# --------------------------------------------------------------------- WU-04 注入链路


def test_process_direct_accepts_extra_metadata() -> None:
    import inspect

    from nanobot.agent.loop import AgentLoop

    assert "extra_metadata" in inspect.signature(AgentLoop.process_direct).parameters


def test_run_metadata_reaches_process_direct_kwargs() -> None:
    from nanobot.sdk.runtime import build_process_direct_kwargs

    kwargs = build_process_direct_kwargs(
        session_key="s", channel="cli", chat_id="c", sender_id="u",
        media=None, ephemeral=False, metadata={"agent_id": "writer"},
    )
    assert kwargs["extra_metadata"] == {"agent_id": "writer"}


def test_live_mode_allows_sandbox_tools() -> None:
    from nanobot.evaluation.schemas import ExecutionConfig

    body = ExecutionConfig.model_validate({
        "tool_mode": "live",
        "allowed_tools": ["read_file", "write_file", "edit_file", "list_dir",
                          "grep", "find_files", "web_fetch"],
    })
    assert body.tool_mode == "live"


def test_live_mode_refuses_native_shell_tools() -> None:
    """live 模式同样拿不到原生 shell：越权工具在入库前就被拒绝。"""
    from pydantic import ValidationError

    from nanobot.evaluation.schemas import ExecutionConfig

    for name in ("exec", "exec_session", "apply_patch"):
        with pytest.raises(ValidationError) as excinfo:
            ExecutionConfig.model_validate({
                "tool_mode": "live",
                "allowed_tools": ["read_file", name],
            })
        assert "TOOL_NOT_ALLOWED" in str(excinfo.value)


def test_agent_boot_carries_frozen_target(repository: EvaluationRepository, tmp_path: Path) -> None:
    from nanobot.evaluation.schemas import AgentWorkerBoot

    boot = AgentWorkerBoot.model_validate({
        "type": "boot", "protocol_version": 1,
        "attempt_id": "00000000-0000-4000-8000-00000000000a",
        "runtime_root": str(tmp_path / "runtime"), "workspace": str(tmp_path / "ws"),
        "profile_bundle": {"config": {}}, "role": "agent",
        "case_snapshot": make_case_payload(
            suite_id="00000000-0000-4000-8000-000000000001"),
        "target": {"kind": "agent", "id": "writer"},
    })
    assert boot.target == {"kind": "agent", "id": "writer"}


def test_materialize_target_files_writes_profile_and_skills(tmp_path: Path) -> None:
    from nanobot.evaluation.runtime_factory import (
        _materialize_target_files,
        attempt_paths,
    )

    paths = attempt_paths(
        attempt_id="00000000-0000-4000-8000-00000000000b",
        runtime_root=tmp_path / "runtime", workspace=tmp_path / "ws",
    )
    paths.create()
    target = {
        "kind": "agent", "id": "writer",
        "agent": {
            "profile": {"id": "writer", "name": "写手"},
            "skills": {"skills/demo/SKILL.md": "# demo"},
        },
    }
    written, skills = _materialize_target_files(paths, target)
    assert "agents/profiles/writer.json" in written
    assert "skills/demo/SKILL.md" in written
    assert skills[0]["name"] == "demo"
    assert len(skills[0]["files"][0]["sha256"]) == 64
    assert (tmp_path / "ws" / "agents" / "profiles" / "writer.json").is_file()
    assert "# demo" in (tmp_path / "ws" / "skills" / "demo" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    with pytest.raises(ValueError):
        _materialize_target_files(paths, {
            "kind": "skill", "id": "x",
            "skill": {"name": "x", "files": {"../escape.txt": "pwn"}},
        })


def _seed_workspace(tmp_path: Path) -> Path:
    import json as jsonlib

    ws = tmp_path / "ws"
    profiles = ws / "agents" / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "writer.json").write_text(jsonlib.dumps({
        "id": "writer", "name": "写手", "prompt": "你是写手",
        "skills": {"mode": "include", "entries": ["demo"]},
    }, ensure_ascii=False), encoding="utf-8")
    skill = ws / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# demo skill", encoding="utf-8")
    return ws


def test_agent_target_freezes_profile_and_skill_files(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    from nanobot.evaluation.services import EvaluationService
    from tests.evaluation.conftest import make_run_payload, make_suite_payload

    service = EvaluationService(
        repository=repository, data_dir=tmp_path, workspace=_seed_workspace(tmp_path),
    )
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"]))
    run = service.create_run(
        RunCreate.model_validate({
            **make_run_payload([case["id"]], suite_id=suite["id"],
                               agent_profile_id="controlled-agent"),
            "target": {"kind": "agent", "id": "writer"},
        }),
        idempotency_key="freeze-1", scope="t",
    )
    target = repository.get_run(run["id"])["snapshot"]["target"]
    assert target["agent"]["profile"]["id"] == "writer"
    assert "skills/demo/SKILL.md" in target["agent"]["skills"]


def test_skill_target_freezes_files_and_missing_skill_fails(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    from nanobot.evaluation.services import EvaluationService, ServiceError
    from tests.evaluation.conftest import make_run_payload, make_suite_payload

    service = EvaluationService(
        repository=repository, data_dir=tmp_path, workspace=_seed_workspace(tmp_path),
    )
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"]))
    run = service.create_run(
        RunCreate.model_validate({
            **make_run_payload([case["id"]], suite_id=suite["id"],
                               agent_profile_id="controlled-agent"),
            "target": {"kind": "skill", "id": "demo"},
        }),
        idempotency_key="freeze-2", scope="t",
    )
    target = repository.get_run(run["id"])["snapshot"]["target"]
    assert "# demo skill" in target["skill"]["files"]["skills/demo/SKILL.md"]
    with pytest.raises(ServiceError) as excinfo:
        service.create_run(
            RunCreate.model_validate({
                **make_run_payload([case["id"]], suite_id=suite["id"],
                                   agent_profile_id="controlled-agent"),
                "target": {"kind": "skill", "id": "nope"},
            }),
            idempotency_key="freeze-3", scope="t",
        )
    assert excinfo.value.code == "SKILL_NOT_FOUND"


def test_agent_target_freezes_builtin_skill_files(
    repository: EvaluationRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """选了 builtin 技能的 Agent 档案也能入队：冻结来源必须与 SkillsLoader 同源。"""
    import json as jsonlib

    from nanobot.agent import skills as skills_mod
    from nanobot.evaluation.services import EvaluationService
    from tests.evaluation.conftest import make_run_payload, make_suite_payload

    builtin_root = tmp_path / "builtin-skills"
    pack = builtin_root / "pack"
    pack.mkdir(parents=True)
    (pack / "SKILL.md").write_text("# packed builtin", encoding="utf-8")
    monkeypatch.setattr(skills_mod, "BUILTIN_SKILLS_DIR", builtin_root)

    ws = _seed_workspace(tmp_path)
    profile = ws / "agents" / "profiles" / "writer.json"
    data = jsonlib.loads(profile.read_text(encoding="utf-8"))
    data["skills"] = {"mode": "include", "entries": ["demo", "pack"]}
    profile.write_text(jsonlib.dumps(data, ensure_ascii=False), encoding="utf-8")

    service = EvaluationService(
        repository=repository, data_dir=tmp_path, workspace=ws,
    )
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"]))
    run = service.create_run(
        RunCreate.model_validate({
            **make_run_payload([case["id"]], suite_id=suite["id"],
                               agent_profile_id="controlled-agent"),
            "target": {"kind": "agent", "id": "writer"},
        }),
        idempotency_key="freeze-builtin", scope="t",
    )
    skills = repository.get_run(run["id"])["snapshot"]["target"]["agent"]["skills"]
    assert skills["skills/pack/SKILL.md"] == "# packed builtin"
    assert "skills/demo/SKILL.md" in skills


# --------------------------------------------------------------------- WU-05 团队 runner


async def test_team_target_runs_through_injected_runner(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    from nanobot.evaluation.services import EvaluationService
    from tests.evaluation.conftest import make_run_payload, make_suite_payload

    calls: list[tuple[int, str]] = []

    service = EvaluationService(
        repository=repository, data_dir=tmp_path,
        team_runtime_factory=_team_factory(seen=calls),
        team_snapshot_provider=_team_snapshot,
    )
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"]))
    run = service.create_run(
        RunCreate.model_validate({
            **make_run_payload([case["id"]], suite_id=suite["id"],
                               agent_profile_id="controlled-agent"),
            "target": {"kind": "team", "id": "creative"},
        }),
        idempotency_key="team-1", scope="t",
    )
    detail = repository.get_run(run["id"])
    outcome = await service.execute(detail["attempts"][0], run=detail)
    assert outcome.execution_status == "succeeded"
    assert outcome.verdict == "passed"
    assert outcome.result["final_answer"] == "团队结论 alpha"
    # 全部轮次都要跑：只跑最后一轮就是团队丢上下文。
    assert calls == [(1, case["turns"][0]["content"])]


async def test_team_target_partial_run_is_submitted_as_succeeded(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    """团队 ``partial`` 是终结状态：执行按成功提交，停止原因如实保留。"""
    from nanobot.evaluation.services import EvaluationService
    from tests.evaluation.conftest import make_run_payload, make_suite_payload

    service = EvaluationService(
        repository=repository, data_dir=tmp_path,
        team_runtime_factory=_team_factory(status="partial"),
        team_snapshot_provider=_team_snapshot,
    )
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"]))
    run = service.create_run(
        RunCreate.model_validate({
            **make_run_payload([case["id"]], suite_id=suite["id"],
                               agent_profile_id="controlled-agent"),
            "target": {"kind": "team", "id": "creative"},
        }),
        idempotency_key="team-partial", scope="t",
    )
    detail = repository.get_run(run["id"])
    outcome = await service.execute(detail["attempts"][0], run=detail)
    assert outcome.execution_status == "succeeded"
    assert outcome.verdict == "passed"
    assert outcome.result["stop_reason"] == "partial"


async def test_team_target_non_terminal_status_fails_instead_of_conflicting(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    """runner 返回仍 running 时按执行失败收口，不能把非法状态交给 finish_attempt。"""
    from nanobot.evaluation.services import EvaluationService
    from tests.evaluation.conftest import make_run_payload, make_suite_payload

    service = EvaluationService(
        repository=repository, data_dir=tmp_path,
        team_runtime_factory=_team_factory(status="running", answer=None),
        team_snapshot_provider=_team_snapshot,
    )
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"]))
    run = service.create_run(
        RunCreate.model_validate({
            **make_run_payload([case["id"]], suite_id=suite["id"],
                               agent_profile_id="controlled-agent"),
            "target": {"kind": "team", "id": "creative"},
        }),
        idempotency_key="team-running", scope="t",
    )
    detail = repository.get_run(run["id"])
    outcome = await service.execute(detail["attempts"][0], run=detail)
    assert outcome.execution_status == "failed"
    assert outcome.verdict == "failed"
    assert outcome.result["errors"][0]["code"] == "TEAM_RUN_STATUS_UNEXPECTED"


async def test_team_target_without_runner_fails_setup(
    repository: EvaluationRepository, tmp_path: Path
) -> None:
    from nanobot.evaluation.services import EvaluationService
    from tests.evaluation.conftest import make_run_payload, make_suite_payload

    service = EvaluationService(
        repository=repository, data_dir=tmp_path,
        team_snapshot_provider=_team_snapshot,
    )
    suite = repository.create_suite(make_suite_payload())
    case = repository.save_case(make_case_payload(suite_id=suite["id"]))
    run = service.create_run(
        RunCreate.model_validate({
            **make_run_payload([case["id"]], suite_id=suite["id"],
                               agent_profile_id="controlled-agent"),
            "target": {"kind": "team", "id": "creative"},
        }),
        idempotency_key="team-2", scope="t",
    )
    detail = repository.get_run(run["id"])
    outcome = await service.execute(detail["attempts"][0], run=detail)
    assert outcome.execution_status == "setup_error"
    assert outcome.result["errors"][0]["code"] == "TEAM_RUNTIME_UNAVAILABLE"
