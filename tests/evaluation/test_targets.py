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

    service = EvaluationService(repository=repository, data_dir=tmp_path)
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
    assert snapshot["target"] == {"kind": "team", "id": "creative"}
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
