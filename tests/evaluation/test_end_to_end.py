"""T12 离线端到端：20 条基础用例与 10 条校准样本走真实链路。

三段口径，**互不混称**：

- **执行段**：真实 ``create_attempt`` + 真实 ``AgentLoop``/``Nanobot``，只有 LLM 被换成受控
  provider（用例 ``script`` 回放）。工具、Trace、产物密封、判分全部是生产代码路径，
  没有一处 monkeypatch。
- **校准段**：同样只换 Judge 的 provider，量表折算、一致率、门禁判定走真实
  ``run_calibration`` / ``record_calibration_result``。
- **标签段**：本文件用测试代码批量 ``confirmed`` 的 gold **是测试数据**。真正的用户确认只能在
  页面里做，样本出厂状态永远是 ``proposed``。

真实 provider 的冒烟是另一件事（见 plan「离线测试使用受控 provider，与真实 provider 冒烟分开
表述」），本文件不覆盖。
"""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from nanobot.config import loader
from nanobot.evaluation.controlled_provider import ControlledProvider, ScriptStep
from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.evidence import AttemptEvidence, EvidenceArtifact, build_attempt_evidence
from nanobot.evaluation.graders.calibration import (
    CalibrationError,
    judge_fingerprint,
    lookup_calibration,
    rubric_payload,
    run_calibration,
)
from nanobot.evaluation.graders.judge import evaluate_judge
from nanobot.evaluation.graders.rules import evaluate_rules
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.runtime_factory import attempt_paths
from nanobot.evaluation.schemas import AgentWorkerBoot, CaseWrite
from nanobot.evaluation.server import API_PREFIX
from nanobot.evaluation.services import EvaluationService
from nanobot.evaluation.trace import read_trace
from nanobot.evaluation.validation import resolve_inside
from nanobot.evaluation.worker import (
    ARTIFACT_INDEX_NAME,
    WorkerChannel,
    evidence_from_payload,
    run_agent_worker,
)

from .http_app import build_app, csrf_headers, login

#: 凭据只在内存：既不进数据库快照，也不进 Trace/导出。
INLINE_API_KEY = "sk-e2e-secret-4242"

#: 离线测试自行确认的 gold 标签必须自报身份，不能冒充人工标注。
TEST_LABEL_COMMENT = "离线测试夹具：由测试代码批量确认，非人工标注"


# --------------------------------------------------------------------------- 样本


def sample_path(sample_id: str) -> Path:
    return Path(__file__).resolve().parents[2] / "nanobot" / "evaluation" / "samples" / f"{sample_id}.json"


def load_sample(sample_id: str) -> dict[str, Any]:
    return json.loads(sample_path(sample_id).read_text(encoding="utf-8"))


def basic_cases() -> dict[str, dict[str, Any]]:
    return {str(case["id"]): case for case in load_sample("basic-v1")["cases"]}


def gold_samples() -> list[dict[str, Any]]:
    return [dict(item) for item in load_sample("calibration-v1")["samples"]]


BASIC_CASE_IDS = sorted(basic_cases())
RUBRIC = {
    "version": "general-v1",
    "required": True,
    "dimensions": [
        {
            "id": "task_completion",
            "name": "任务完成度",
            "anchors": [
                "0：完全没有执行任务，或回答与任务无关。",
                "1：只做了少量相关工作，任务主体未完成。",
                "2：完成了任务的一部分，但缺少关键步骤或关键输出。",
                "3：完整完成任务的必要步骤，输出满足任务要求。",
                "4：完整且超出预期地完成任务，输出干净利落无冗余。",
            ],
            "pass_score": 3,
        },
        {
            "id": "correctness",
            "name": "正确性",
            "anchors": [
                "0：结果错误且具有误导性，或凭空编造内容。",
                "1：结果存在明显错误，只有少量正确信息。",
                "2：结果大体相关但包含错误或与证据不符之处。",
                "3：结果正确，与任务给定的证据一致，无虚构。",
                "4：结果完全正确，细节精确且可验证。",
            ],
            "pass_score": 3,
        },
    ],
}
JUDGE_PROFILE = {"provider": "controlled", "model": "eval-model", "generation": None}


# --------------------------------------------------------------------------- 执行段


@pytest.fixture(autouse=True)
def _restore_config_path():
    """``create_attempt`` 会把全局配置路径改到 attempt 目录；测试之间必须还原。"""
    previous = loader._current_config_path  # pyright: ignore[reportPrivateUsage]
    yield
    loader.set_config_path(previous)  # pyright: ignore[reportArgumentType]


def write_payload(case: Mapping[str, Any]) -> dict[str, Any]:
    """样本里的 ``id`` 是样本自身的编号，不是用例字段：导入时同样由服务端重新分配。"""
    return {key: value for key, value in case.items() if key != "id"}


def _boot(case: Mapping[str, Any], root: Path) -> AgentWorkerBoot:
    return AgentWorkerBoot(
        type="boot",
        protocol_version=1,
        attempt_id=uuid.uuid4(),
        runtime_root=str(root / "runtime"),
        workspace=str(root / "workspace"),
        profile_bundle={
            "config": {"agents": {"defaults": {"model": "eval-model", "provider": "openai"}}},
            "generation": {},
        },
        role="agent",
        case_snapshot=write_payload(case),
    )


def _registered_artifacts(root: Path, workspace: Path) -> tuple[EvidenceArtifact, ...]:
    """按 ``services._artifacts`` 的同一口径登记产物索引条目。

    差异只有一处：这里直接发 ``artifact_id``，不落 ``artifacts`` 表——本文件验证的是判分链路，
    登记入库由服务层的 ``append_artifact`` 负责。``kind == "workspace"``、``resolve_inside``
    目录包含判定、工作区包含判定三道口径与生产代码一致。
    """
    index_path = root / "artifacts" / ARTIFACT_INDEX_NAME
    if not index_path.is_file():
        return ()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entries = index.get("artifacts")
    if not isinstance(entries, list):
        return ()
    resolved = workspace.resolve()
    found: list[EvidenceArtifact] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.get("kind") != "workspace":
            continue
        raw = entry.get("relative_path")
        if not isinstance(raw, str) or not raw:
            continue
        target = resolve_inside(root, raw)
        if not target.resolve().is_relative_to(resolved) or not target.is_file():
            continue
        found.append(
            EvidenceArtifact(
                relative_path=target.resolve().relative_to(resolved).as_posix(),
                media_type=str(entry.get("media_type", "application/octet-stream")),
                size_bytes=int(entry.get("size_bytes", 0)),
                sha256=str(entry.get("sha256", "")),
                artifact_id=uuid.uuid4(),
            )
        )
    return tuple(found)


async def _execute(case: Mapping[str, Any], root: Path) -> tuple[Any, AttemptEvidence]:
    """真跑一次 attempt，返回 worker 结果与服务层同形状的证据。"""
    boot = _boot(case, root)
    written: list[bytes] = []
    channel = WorkerChannel(attempt_id=str(boot.attempt_id), sink=written.append)
    result = await run_agent_worker(boot, channel=channel)

    paths = attempt_paths(
        attempt_id=str(boot.attempt_id),
        runtime_root=boot.runtime_root,
        workspace=boot.workspace,
    )
    trace = read_trace(paths.trace_dir)
    evidence = build_attempt_evidence(
        attempt_id=str(boot.attempt_id),
        case_snapshot=CaseWrite.model_validate(write_payload(case)).model_dump(mode="json"),
        workspace=paths.workspace,
        trace_events=trace.events,
        artifacts=_registered_artifacts(paths.root, paths.workspace),
        final_answer=result.final_answer,
        stop_reason=result.stop_reason,
        iterations=result.usage.iterations,
        trace_complete=trace.trace_complete,
        execution_status=result.execution_status,
    )
    return result, evidence


def _rule_map(case: Mapping[str, Any], evidence: AttemptEvidence) -> dict[str, Any]:
    return {
        item.id: item
        for item in evaluate_rules(CaseWrite.model_validate(write_payload(case)), evidence)
    }


@pytest.mark.parametrize("case_id", BASIC_CASE_IDS)
async def test_basic_case_passes_offline(case_id: str, tmp_path: Path) -> None:
    case = copy.deepcopy(basic_cases()[case_id])
    result, evidence = await _execute(case, tmp_path)

    assert result.execution_status == "succeeded", [e.model_dump() for e in result.errors]
    assert result.errors == []
    assert result.trace_complete is True
    assert result.final_answer
    rules = _rule_map(case, evidence)
    failed = {
        item_id: item.message for item_id, item in rules.items() if item.passed is not True
    }
    assert failed == {}, f"{case_id} 断言未通过：{failed}"


def _flip_answer(text: str) -> Callable[[dict[str, Any]], None]:
    def apply(case: dict[str, Any]) -> None:
        case["script"][-1]["text"] = text

    return apply


def _flip_json(payload: Mapping[str, Any]) -> Callable[[dict[str, Any]], None]:
    def apply(case: dict[str, Any]) -> None:
        for step in case["script"]:
            if step.get("json_text") is not None:
                step["json_text"] = json.dumps(payload, ensure_ascii=False)
                return
        raise AssertionError("用例没有 json_text 步骤")

    return apply


def _flip_write(content: str) -> Callable[[dict[str, Any]], None]:
    def apply(case: dict[str, Any]) -> None:
        for step in case["script"]:
            for call in step.get("tool_calls") or []:
                if call["name"] == "write_file":
                    call["arguments"]["content"] = content
                    return
        raise AssertionError("用例没有 write_file 调用")

    return apply


def _flip_assertion(maximum: int) -> Callable[[dict[str, Any]], None]:
    def apply(case: dict[str, Any]) -> None:
        for item in case["assertions"]:
            if item["kind"] in ("iteration_limit", "tool_call_limit"):
                item["maximum"] = maximum
                return
        raise AssertionError("用例没有上限类断言")

    return apply


def _prepend_tool_call(name: str, arguments: Mapping[str, Any]) -> Callable[[dict[str, Any]], None]:
    def apply(case: dict[str, Any]) -> None:
        case["script"].insert(
            0, {"tool_calls": [{"name": name, "arguments": dict(arguments)}]}
        )

    return apply


#: 关键值翻转必须让**指定那条**断言失败，且执行本身仍然 succeeded ——
#: 断言失败与执行失败是两回事，混起来会掩盖判分链路的问题。
FLIPS: list[tuple[str, str, Callable[[dict[str, Any]], None]]] = [
    ("B01", "answer-is-5", _flip_answer("8")),
    ("B02", "answer-count-3", _flip_json({"status": "ok", "count": 4})),
    ("B04", "output-content", _flip_write("goodbye")),
    ("B07", "total-json", _flip_write(json.dumps({"total": 41}))),
    ("B09", "call-budget", _flip_assertion(2)),
    ("B12", "no-write-tools", _prepend_tool_call("read_file", {"path": "anything.txt"})),
    ("B13", "answer-18", _flip_answer("北京当前温度是 25°C。")),
    ("B20", "iteration-cap", _flip_assertion(1)),
]


@pytest.mark.parametrize(("case_id", "assertion_id", "mutate"), FLIPS, ids=[f[0] for f in FLIPS])
async def test_flipped_case_fails_expected_assertion(
    case_id: str, assertion_id: str, mutate: Callable[[dict[str, Any]], None], tmp_path: Path
) -> None:
    case = copy.deepcopy(basic_cases()[case_id])
    mutate(case)

    result, evidence = await _execute(case, tmp_path)

    assert result.execution_status == "succeeded", [e.model_dump() for e in result.errors]
    rules = _rule_map(case, evidence)
    assert assertion_id in rules, f"{case_id} 没有断言 {assertion_id}"
    assert rules[assertion_id].passed is False, rules[assertion_id].message
    assert any(rule.passed is not True for rule in rules.values())


# --------------------------------------------------------------------------- 标签段


def build_repository(tmp_path: Path) -> EvaluationRepository:
    database = EvaluationDatabase(tmp_path / "evaluation.db")
    database.init_schema()
    return EvaluationRepository(database)


def build_service(tmp_path: Path) -> EvaluationService:
    return EvaluationService(repository=build_repository(tmp_path), data_dir=tmp_path / "runs")


def confirm_all(repository: EvaluationRepository) -> int:
    """把 10 条 gold 全部确认（测试数据，不是人工标注）。"""
    gold = repository.list_gold_samples()
    revision = int(gold["gold_revision"])
    for row in gold["data"]:
        repository.review_gold_sample(
            str(row["id"]),
            {
                "base_gold_revision": revision,
                "expected_verdict": row["expected_verdict"],
                "expected_scores": row["expected_scores"],
                "comment": TEST_LABEL_COMMENT,
            },
        )
        revision += 1
    return revision


def test_gold_seed_starts_as_proposed_and_never_resets(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    assert service.seed_builtin_gold_samples() == 10
    rows = service.list_gold_samples()["data"]
    assert [row["id"] for row in rows] == [sample["id"] for sample in gold_samples()]
    assert {row["label_status"] for row in rows} == {"proposed"}
    assert {row["reviewer"] for row in rows} == {None}

    # 幂等：第二次不再写入。
    assert service.seed_builtin_gold_samples() == 0

    repository = service.repository
    confirm_all(repository)
    rows = repository.list_gold_samples()["data"]
    assert {row["label_status"] for row in rows} == {"confirmed"}
    assert {row["reviewer"] for row in rows} == {"local-user"}

    # 已有数据时绝不把人工确认打回建议状态。
    assert service.seed_builtin_gold_samples() == 0
    rows = repository.list_gold_samples()["data"]
    assert {row["label_status"] for row in rows} == {"confirmed"}


async def test_http_first_boot_exposes_only_proposed_samples(
    evaluation_repository: EvaluationRepository, tmp_path: Path
) -> None:
    async with TestClient(
        TestServer(build_app(evaluation_repository, tmp_path / "svc"), host="127.0.0.1")
    ) as client:
        token = await login(client)
        response = await client.get(
            f"{API_PREFIX}/calibration-samples", headers=csrf_headers(token)
        )
        assert response.status == 200
        payload = await response.json()

    rows = payload["data"]
    assert len(rows) == 10
    assert {row["label_status"] for row in rows} == {"proposed"}
    assert {row["reviewer"] for row in rows} == {None}
    assert "workspace" not in rows[0]


# --------------------------------------------------------------------------- 校准段


def _judge_for(
    scores_by_attempt: Mapping[str, Sequence[int]] | None = None,
    *,
    fixed: Sequence[int] | None = None,
):
    """构造 Judge：走真实 ``evaluate_judge``，只把 provider 换成回放脚本。

    ``scores_by_attempt`` 按证据里的 attempt_id 给出每维度分数；``fixed`` 则无视样本一律给同一
    组分数——用来造一个「什么都打 4 分」的坏 Judge。
    """

    async def judge(rubric: Any, evidence: AttemptEvidence) -> Any:
        dimension_ids = [str(item["id"]) for item in rubric_payload(rubric)["dimensions"]]
        if fixed is not None:
            scores = list(fixed)
        else:
            assert scores_by_attempt is not None
            scores = [int(item) for item in scores_by_attempt[evidence.attempt_id]]
        payload = {
            "dimensions": [
                {
                    "id": dimension_id,
                    "score": score,
                    "reason": "离线测试夹具给出的评分",
                    "evidence_refs": [f"trace:{evidence.attempt_id}:1"],
                }
                for dimension_id, score in zip(dimension_ids, scores, strict=True)
            ]
        }
        return await evaluate_judge(
            rubric,
            evidence,
            ControlledProvider(steps=(ScriptStep(json_text=json.dumps(payload, ensure_ascii=False)),)),
        )

    return judge


async def test_calibration_passes_when_judge_matches_confirmed_gold(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    assert service.seed_builtin_gold_samples() == 10
    repository = service.repository
    revision = confirm_all(repository)

    samples = gold_samples()
    scores = {
        str(sample["evidence"]["attempt_id"]): [
            int(item["score"]) for item in sample["expected_scores"]
        ]
        for sample in samples
    }
    result = await run_calibration(
        repository,
        profile=JUDGE_PROFILE,
        rubric=RUBRIC,
        judge=_judge_for(scores),
        evidence_for=lambda row: evidence_from_payload(row["evidence"], workspace=tmp_path),
        gold_revision=revision,
    )

    assert result["status"] == "completed"
    assert result["total"] == 10
    assert result["matched"] == 10
    assert result["agreement"] == 1.0
    assert result["passed"] is True
    assert all(item["matched"] for item in result["items"])
    assert {item["judge_status"] for item in result["items"]} == {"completed"}

    found = lookup_calibration(
        repository,
        judge_fingerprint=judge_fingerprint(profile=JUDGE_PROFILE, rubric=RUBRIC),
        rubric_version="general-v1",
        gold_revision=repository.gold_revision(),
    )
    assert found is not None
    assert found["passed"] is True


async def test_calibration_fails_for_over_generous_judge(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    assert service.seed_builtin_gold_samples() == 10
    repository = service.repository
    revision = confirm_all(repository)

    result = await run_calibration(
        repository,
        profile=JUDGE_PROFILE,
        rubric=RUBRIC,
        judge=_judge_for(fixed=[4, 4]),
        evidence_for=lambda row: evidence_from_payload(row["evidence"], workspace=tmp_path),
        gold_revision=revision,
    )

    assert result["matched"] == 5
    assert result["agreement"] == 0.5
    assert result["passed"] is False


async def test_calibration_refuses_unconfirmed_gold(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    assert service.seed_builtin_gold_samples() == 10
    repository = service.repository

    with pytest.raises(CalibrationError) as excinfo:
        await run_calibration(
            repository,
            profile=JUDGE_PROFILE,
            rubric=RUBRIC,
            judge=_judge_for(fixed=[4, 4]),
            evidence_for=lambda row: evidence_from_payload(row["evidence"], workspace=tmp_path),
        )

    assert "GOLD_NOT_CONFIRMED" in str(excinfo.value)
    assert repository.find_calibration_result(
        judge_fingerprint=judge_fingerprint(profile=JUDGE_PROFILE, rubric=RUBRIC),
        rubric_version="general-v1",
        gold_revision=repository.gold_revision(),
    ) is None
