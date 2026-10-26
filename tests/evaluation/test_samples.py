"""T12：随包样本文件的结构与自洽性（契约 §3 / §3.1 + plan T12）。

样本是判分链路的输入源，坏数据的代价比坏代码高：用例导入后会被真实执行，
gold 标签会被真实校准。这里锁四件事：

1. 形态：``basic-v1`` 严格 20 条 B01–B20、schema_version=1；``calibration-v1``
   严格 10 条 C01–C10，且**文件里不允许出现 label_status**（确认只能在用户手里发生，
   样本不能预置 confirmed）。
2. 自洽：脚本只调用白名单内工具；文件类断言的路径必须登记为产物，产物来源只能是
   fixture 或脚本自己写的文件——否则判分永远拿不到内容，用例看着能跑实际恒失败。
3. 口径：B11/B16 与 10 条 gold 共用同一份 ``general-v1`` 量表（规范化 JSON 完全一致），
   两个维度的五档锚点齐全、pass_score=3。
4. gold 标签与分数自洽：``expected_verdict`` 必须等于「所有维度 ≥ pass_score」的折算结果，
   成功/失败各至少 4 条，且存在恰好落在阈值上的边界样本。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import nanobot.evaluation
from nanobot.evaluation.evidence import tool_calls_from_events
from nanobot.evaluation.fixtures import CONTROLLED_TOOL_NAMES
from nanobot.evaluation.schemas import (
    FIXTURE_TOOLS,
    CaseWrite,
    GoldSample,
    Rubric,
)
from nanobot.evaluation.worker import evidence_from_payload

SAMPLES_DIR = Path(nanobot.evaluation.__file__).parent / "samples"

CASE_IDS = [f"B{i:02d}" for i in range(1, 21)]
GOLD_IDS = [f"C{i:02d}" for i in range(1, 11)]

ASSERTION_KINDS = {
    "answer_contains", "answer_not_contains", "answer_json", "file_json",
    "file_exists", "file_contains", "tool_called", "tool_forbidden",
    "iteration_limit", "tool_call_limit",
}

GENERAL_V1_VERSION = "general-v1"
RUBRIC_DIMENSIONS = {"task_completion", "correctness"}
PASS_SCORE = 3


def _load(name: str) -> dict[str, Any]:
    return json.loads((SAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _strip_case_identity(raw: Mapping[str, Any]) -> dict[str, Any]:
    """导入口径：仓储先剥掉服务端字段再校验（id 只是样本内的稳定标签）。"""
    return {key: value for key, value in raw.items() if key != "id"}


@pytest.fixture(scope="module")
def basic() -> dict[str, CaseWrite]:
    payload = _load("basic-v1")
    return {
        str(raw["id"]): CaseWrite.model_validate(_strip_case_identity(raw))
        for raw in payload["cases"]
    }


@pytest.fixture(scope="module")
def calibration() -> dict[str, Any]:
    return _load("calibration-v1")


# ------------------------------------------------------------------ basic-v1


def test_basic_v1_shape_and_stable_ids() -> None:
    payload = _load("basic-v1")
    assert payload["schema_version"] == 1
    assert isinstance(payload["suite"]["name"], str) and payload["suite"]["name"].strip()
    cases = payload["cases"]
    assert [str(item["id"]) for item in cases] == CASE_IDS
    assert len({str(item["id"]) for item in cases}) == 20


def test_basic_cases_are_valid_case_writes(basic: dict[str, CaseWrite]) -> None:
    suite_ids = {str(case.suite_id) for case in basic.values()}
    assert len(suite_ids) == 1
    for case_id, case in basic.items():
        assert case.name.startswith(case_id)
        assert case.execution.tool_mode == "fixture"  # 离线样本不带 live 语义
        assert case.script, f"{case_id} 必须带离线脚本，否则回放不出任何执行"
        # 每条用例都要有 verdict 可依的门：必过断言或必需量表（schema 已保证，这里显式钉住）
        assert any(item.required for item in case.assertions) or (
            case.rubric is not None and case.rubric.required
        )


def test_basic_cases_cover_every_assertion_kind(basic: dict[str, CaseWrite]) -> None:
    kinds = {item.kind for case in basic.values() for item in case.assertions}
    assert kinds == ASSERTION_KINDS


def test_basic_tools_are_supported_and_self_consistent(basic: dict[str, CaseWrite]) -> None:
    for case_id, case in basic.items():
        assert set(case.execution.allowed_tools) <= FIXTURE_TOOLS
        for step in case.script:
            for call in step.tool_calls:
                assert call.name in case.execution.allowed_tools, (
                    f"{case_id} 脚本调用了未声明工具 {call.name}"
                )
        for fixture in case.tool_fixtures:
            assert fixture.tool_name in CONTROLLED_TOOL_NAMES


def test_basic_file_assertions_resolve_to_declared_artifacts(basic: dict[str, CaseWrite]) -> None:
    """文件类断言的路径必须 ∈ artifact_paths，且产物来源只有 fixture 与脚本写入两种。"""
    for case_id, case in basic.items():
        declared = list(case.artifact_paths)
        assert len(set(declared)) == len(declared)
        fixture_paths = {fixture.path for fixture in case.fixtures}
        written: set[str] = set()
        for step in case.script:
            for call in step.tool_calls:
                if call.name in ("write_file", "edit_file"):
                    path = call.arguments.get("path")
                    assert isinstance(path, str), f"{case_id} 写库调用缺少 path"
                    written.add(path)
        for assertion in case.assertions:
            path = getattr(assertion, "path", None)
            if path is None:
                continue
            assert path in declared, f"{case_id} 断言 {assertion.id} 的 {path} 未登记为产物"
        for path in declared:
            assert path in fixture_paths | written, (
                f"{case_id} 声明了没人写的产物 {path}"
            )
        for path in fixture_paths | written:
            # 写进工作区的文件如果还是要被判分，就必须登记；纯输入文件可以不登记。
            if path in declared:
                continue


def test_basic_no_absolute_or_traversing_paths(basic: dict[str, CaseWrite]) -> None:
    """样本随包发布，任何平台相关的绝对路径都不允许出现。"""
    payload = _load("basic-v1")
    raw_text = json.dumps(payload, ensure_ascii=False)
    assert "\\\\" not in raw_text.replace("\\\"", "")  # JSON 里不该有反斜线路径
    for case_id, case in basic.items():
        paths = list(case.artifact_paths)
        paths += [fixture.path for fixture in case.fixtures]
        paths += [
            item.path for item in case.assertions if getattr(item, "path", None) is not None
        ]
        for path in paths:
            assert not path.startswith("/"), f"{case_id} 绝对路径 {path}"
            assert ".." not in path.split("/"), f"{case_id} 越界路径 {path}"


def test_basic_plan_table_anchors(basic: dict[str, CaseWrite]) -> None:
    """plan T12 表格里的关键形状：逐条钉住，防止样本被改走样。"""
    def kinds(case_id: str) -> set[str]:
        return {item.kind for item in basic[case_id].assertions}

    assert kinds("B09") >= {"tool_call_limit"}
    assert kinds("B20") >= {"iteration_limit", "file_json"}
    assert basic["B20"].execution.max_iterations == 2
    assert kinds("B12") >= {"tool_forbidden", "answer_json"}
    assert kinds("B02") >= {"answer_json"}
    assert kinds("B04") >= {"file_exists", "file_contains"}
    assert basic["B17"].turns and len(basic["B17"].turns) == 2
    assert basic["B18"].memory_seed
    for case_id in ("B13", "B14", "B15"):
        assert basic[case_id].tool_fixtures, f"{case_id} 必须带受控工具 fixture"
    # B11/B16 的门是必需量表，其余用例至少一条必过断言
    for case_id in ("B11", "B16"):
        rubric = basic[case_id].rubric
        assert rubric is not None and rubric.required
        assert rubric.version == GENERAL_V1_VERSION
    assert all(
        any(item.required for item in basic[case_id].assertions)
        for case_id in CASE_IDS
        if case_id not in ("B11", "B16")
    )


# --------------------------------------------------------------- calibration-v1


def test_calibration_v1_shape_and_no_pre_confirmed_labels(
    calibration: dict[str, Any],
) -> None:
    assert calibration["schema_version"] == 1
    samples = calibration["samples"]
    assert [str(item["id"]) for item in samples] == GOLD_IDS
    # 投放即 confirmed 等于伪造人工标注：样本文件不允许携带标签状态列。
    for sample in samples:
        assert "label_status" not in sample
        assert "reviewer" not in sample
    assert int(calibration["gold_revision"]) >= 1


def test_calibration_samples_read_as_gold_samples(calibration: dict[str, Any]) -> None:
    for sample in calibration["samples"]:
        page_row = {
            **sample,
            "label_status": "proposed",
            "reviewer": None,
            "comment": "",
            "updated_at": "2026-10-15T00:00:00+00:00",
        }
        parsed = GoldSample.model_validate(page_row)
        assert parsed.label_status == "proposed"
        assert parsed.task.strip()


def test_calibration_expected_verdict_matches_scores(calibration: dict[str, Any]) -> None:
    passed = failed = 0
    boundary_passed = boundary_failed = False
    for sample in calibration["samples"]:
        rubric = Rubric.model_validate(sample["rubric"])
        thresholds = {dimension.id: dimension.pass_score for dimension in rubric.dimensions}
        scores = {item["dimension_id"]: item["score"] for item in sample["expected_scores"]}
        assert set(scores) == set(thresholds), f"{sample['id']} 分数维度与量表不一致"
        verdict = "passed" if all(scores[key] >= thresholds[key] for key in thresholds) else "failed"
        assert verdict == sample["expected_verdict"], f"{sample['id']} 标签与分数不自洽"
        if verdict == "passed":
            passed += 1
            boundary_passed = boundary_passed or any(
                scores[key] == thresholds[key] for key in thresholds
            )
        else:
            failed += 1
            boundary_failed = boundary_failed or any(
                scores[key] == thresholds[key] for key in thresholds
            )
    assert passed >= 4 and failed >= 4
    # 边界锚点：阈值恰好卡在 pass_score 的正反例都要有，≥ 语义才有人复核。
    assert boundary_passed and boundary_failed


def test_calibration_shares_general_v1_rubric_with_b11_b16(
    basic: dict[str, CaseWrite], calibration: dict[str, Any]
) -> None:
    reference_raw = _canon(calibration["samples"][0]["rubric"])
    reference_model = _canon(
        Rubric.model_validate(calibration["samples"][0]["rubric"]).model_dump(mode="json")
    )
    # 样本文件的作者口径与模型规范化口径一致：否则 select_gold_samples 的
    # 「规范化 JSON 完全一致」会因缺省值差异拒掉同源量表。
    assert reference_raw == reference_model
    for sample in calibration["samples"][1:]:
        assert _canon(sample["rubric"]) == reference_raw
    for case_id in ("B11", "B16"):
        rubric = basic[case_id].rubric
        assert rubric is not None
        assert _canon(rubric.model_dump(mode="json")) == reference_model
    rubric = Rubric.model_validate(calibration["samples"][0]["rubric"])
    assert rubric.version == GENERAL_V1_VERSION
    assert {dimension.id for dimension in rubric.dimensions} == RUBRIC_DIMENSIONS
    assert all(len(dimension.anchors) == 5 for dimension in rubric.dimensions)
    assert all(dimension.pass_score == PASS_SCORE for dimension in rubric.dimensions)


def test_calibration_evidence_parses_and_matches_its_trace(
    calibration: dict[str, Any], tmp_path: Path
) -> None:
    seen_attempts: set[str] = set()
    for sample in calibration["samples"]:
        payload = sample["evidence"]
        assert payload["attempt_id"] not in seen_attempts
        seen_attempts.add(payload["attempt_id"])
        evidence = evidence_from_payload(payload, workspace=tmp_path)
        derived = tool_calls_from_events(evidence.trace_events)
        authored = payload.get("tool_calls") or []
        assert [
            (call.name, call.arguments, call.result, call.is_error, call.seq)
            for call in derived
        ] == [
            (item["name"], item["arguments"], item["result"], item["is_error"], item["seq"])
            for item in authored
        ], f"{sample['id']} 的 tool_calls 与其 Trace 事件不自洽"


# ----------------------------------------------------------------- 样本导入链路


def test_reliability_v2_imports_through_service(
    evaluation_repository: Any, tmp_path: Path
) -> None:
    """36 条分层样本必须能真的入库，不能只是躺在 JSON 里。"""
    from nanobot.evaluation.services import EvaluationService, Profile

    service = EvaluationService(
        repository=evaluation_repository,
        data_dir=tmp_path / "service",
        profiles=(
            Profile(id="agent-1", label="受控执行器", provider="controlled",
                    model="eval-model", kind="agent",
                    bundle={"config": {"providers": {"controlled": {"api_key": "sk-x"}}}}),
        ),
    )
    result = service.import_sample("reliability-v2")
    assert result["case_count"] == 36
    assert len(result["cases"]) == 36
    targets = {
        tag
        for case in result["cases"]
        for tag in case["tags"]
        if tag.startswith("target:")
    }
    assert targets == {"target:agent", "target:skill", "target:team"}
    # gold 不入库：人工确认走校准通道，样本里的 gold 一律 proposed。
    assert all("gold" not in case for case in result["cases"])


def test_reliability_v2_import_is_idempotent(
    evaluation_repository: Any, tmp_path: Path
) -> None:
    from nanobot.evaluation.services import EvaluationService, Profile

    service = EvaluationService(
        repository=evaluation_repository,
        data_dir=tmp_path / "service",
        profiles=(
            Profile(id="agent-1", label="受控执行器", provider="controlled",
                    model="eval-model", kind="agent",
                    bundle={"config": {"providers": {"controlled": {"api_key": "sk-x"}}}}),
        ),
    )
    first = service.import_sample("reliability-v2")
    second = service.import_sample("reliability-v2")
    assert first["suite"]["id"] == second["suite"]["id"]
    assert second["case_count"] == 36
