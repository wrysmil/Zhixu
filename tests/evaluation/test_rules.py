"""确定性规则判分的红灯测试（WU-04 / T07 第 1–3 项）。

覆盖口径全部来自契约 §2（断言 kind 闭集、SchemaSubset 白名单、RFC 6901 Pointer）、
§4（``AssertionResult.passed`` 三态）与 §6.1（AttemptEvidence 消费侧字段与读取入口）。

**并行隔离说明**：``FakeAttemptEvidence`` 只是契约 §6.1 的结构化替身，字段名、读取入口与
``resolve_ref`` 语义逐字照抄该节；真实的 ``nanobot/evaluation/evidence.py`` 由 WU-03 拥有，
本文件不 import 也不修改它。屏障时由 Leader 用真实证据跑集成检查。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from nanobot.evaluation.graders.rules import evaluate_rules
from nanobot.evaluation.schemas import AssertionResult, TraceEvent

# --------------------------------------------------------------------- §6.1 替身


@dataclass(frozen=True)
class FakeToolCallEvidence:
    """契约 §6.1 ToolCallEvidence 的字段名照抄。"""

    name: str
    arguments: dict[str, Any]
    result: Any
    is_error: bool
    seq: int
    turn: int | None
    iteration: int | None


@dataclass(frozen=True)
class FakeEvidenceArtifact:
    """契约 §6.1 EvidenceArtifact 的字段名照抄；未登记产物 ``artifact_id=None``。"""

    relative_path: str
    media_type: str
    size_bytes: int
    sha256: str
    artifact_id: uuid.UUID | None


class _UnregisteredAccessError(RuntimeError):
    """替身内部使用：判分侧一旦对未登记产物发起读取就说明越界。"""


class FakeAttemptEvidence:
    """契约 §6.1 AttemptEvidence 的消费侧替身（frozen dataclass 的用等价结构）。

    公开成员与真实类逐字一致：``attempt_id`` / ``case_snapshot`` / ``final_answer`` /
    ``stop_reason`` / ``iterations`` / ``trace_events`` / ``tool_calls`` / ``artifacts`` /
    ``workspace`` / ``trace_complete`` / ``execution_status``，读取入口只有
    ``read_text`` / ``read_json`` / ``exists``，引用解析只有 ``resolve_ref``。
    """

    def __init__(
        self,
        *,
        attempt_id: str | None = None,
        case_snapshot: dict[str, Any] | None = None,
        final_answer: str | None = "已完成",
        stop_reason: str | None = "stop",
        iterations: int = 1,
        tool_calls: Sequence[FakeToolCallEvidence] = (),
        artifacts: Sequence[FakeEvidenceArtifact] = (),
        files: Mapping[str, Any] | None = None,
        trace_events: Sequence[TraceEvent] | None = None,
        trace_complete: bool = True,
        execution_status: str = "succeeded",
        workspace: Path | None = None,
        missing_files: Sequence[str] = (),
    ) -> None:
        self.attempt_id = attempt_id if attempt_id is not None else str(uuid.uuid4())
        self.case_snapshot = dict(case_snapshot or {})
        self.final_answer = final_answer
        self.stop_reason = stop_reason
        self.iterations = iterations
        self.tool_calls = tuple(tool_calls)
        self.artifacts = tuple(artifacts)
        self.workspace = workspace if workspace is not None else Path.cwd()
        self.trace_complete = trace_complete
        self.execution_status = execution_status
        self.read_calls: list[str] = []
        self._files: dict[str, Any] = dict(files or {})
        self._missing = set(missing_files)
        self._trace_events = tuple(
            trace_events
            if trace_events is not None
            else [_trace_event(self.attempt_id, call.seq) for call in self.tool_calls]
        )

    @property
    def trace_events(self) -> tuple[TraceEvent, ...]:  # noqa: D401 - §6.1 成员
        return self._trace_events

    def _artifact(self, relative_path: str) -> FakeEvidenceArtifact | None:
        for artifact in self.artifacts:
            if artifact.relative_path == relative_path:
                return artifact
        return None

    def _assert_registered(self, relative_path: str) -> None:
        artifact = self._artifact(relative_path)
        if artifact is None or artifact.artifact_id is None:
            raise _UnregisteredAccessError("ARTIFACT_NOT_REGISTERED")

    def read_text(self, relative_path: str) -> str:
        self._assert_registered(relative_path)
        self.read_calls.append(relative_path)
        if relative_path in self._missing:
            raise FileNotFoundError(relative_path)
        if relative_path.startswith("..") or Path(relative_path).is_absolute():
            raise ValueError("PATH_OUTSIDE_ROOT")
        payload = self._files.get(relative_path)
        if isinstance(payload, BaseException):
            raise payload
        if isinstance(payload, str):
            return payload
        return json.dumps(payload, ensure_ascii=False)

    def read_json(self, relative_path: str) -> Any:
        self._assert_registered(relative_path)
        self.read_calls.append(relative_path)
        if relative_path in self._missing:
            raise FileNotFoundError(relative_path)
        if relative_path.startswith("..") or Path(relative_path).is_absolute():
            raise ValueError("PATH_OUTSIDE_ROOT")
        payload = self._files.get(relative_path)
        if isinstance(payload, BaseException):
            raise payload
        if isinstance(payload, str):
            return json.loads(payload)
        return payload

    def exists(self, relative_path: str) -> bool:
        self._assert_registered(relative_path)
        self.read_calls.append(relative_path)
        if relative_path in self._missing:
            raise FileNotFoundError(relative_path)
        if relative_path.startswith("..") or Path(relative_path).is_absolute():
            raise ValueError("PATH_OUTSIDE_ROOT")
        return relative_path in self._files

    def resolve_ref(self, ref: str) -> Any | None:
        if ref.startswith("artifact:"):
            for artifact in self.artifacts:
                if artifact.artifact_id is not None and ref == f"artifact:{artifact.artifact_id}":
                    return artifact
            return None
        if ref.startswith(f"trace:{self.attempt_id}:"):
            seq = ref.rsplit(":", 1)[-1]
            for event in self.trace_events:
                if str(event.seq) == seq:
                    return event
            return None
        return None


def _trace_event(attempt_id: str, seq: int) -> TraceEvent:
    return TraceEvent(
        attempt_id=attempt_id,
        seq=seq,
        event_id=uuid.uuid4(),
        type="tool.completed",
        ts="2026-10-15T00:00:00Z",
        elapsed_ms=seq,
        payload={"name": "write_file"},
    )


def artifact_for(path: str, *, registered: bool = True) -> FakeEvidenceArtifact:
    return FakeEvidenceArtifact(
        relative_path=path,
        media_type="application/json",
        size_bytes=8,
        sha256="a" * 64,
        artifact_id=uuid.uuid4() if registered else None,
    )


def tool_call(
    name: str = "test_lookup",
    arguments: dict[str, Any] | None = None,
    *,
    seq: int = 1,
    is_error: bool = False,
) -> FakeToolCallEvidence:
    return FakeToolCallEvidence(
        name=name,
        arguments={"city": "北京"} if arguments is None else arguments,
        result={"temperature_c": 18},
        is_error=is_error,
        seq=seq,
        turn=0,
        iteration=0,
    )


def case_with(assertion: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """契约 §2 CaseRead 形状的最小快照（判分前已由仓储过一遍 CaseWrite.model_validate）。"""
    payload: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "revision": 1,
        "name": "规则用例",
        "description": "",
        "tags": [],
        "enabled": True,
        "suite_id": str(uuid.uuid4()),
        "turns": [{"content": "生成 summary.json"}],
        "fixtures": [],
        "memory_seed": [],
        "clock_iso": None,
        "tool_fixtures": [],
        "execution": {
            "tool_mode": "fixture",
            "allowed_tools": ["read_file", "write_file"],
            "max_iterations": 12,
            "timeout_s": 120,
        },
        "assertions": [assertion],
        "rubric": None,
        "artifact_paths": [],
        "created_at": "2026-10-15T00:00:00Z",
        "updated_at": "2026-10-15T00:00:00Z",
        "is_deleted": False,
    }
    payload.update(overrides)
    return payload


def only(evidence: FakeAttemptEvidence) -> AssertionResult:
    results = evaluate_rules(evidence.case_snapshot, evidence)
    assert len(results) == 1
    return results[0]


# --------------------------------------------------------------- B07 正反例（CSV→JSON）


def test_file_json_matches_total_42() -> None:
    assertion = {
        "id": "j1",
        "kind": "file_json",
        "required": True,
        "path": "summary.json",
        "schema": {"type": "object", "properties": {"total": {"type": "integer"}}, "required": ["total"]},
        "checks": [{"pointer": "/total", "equals": 42}],
    }
    artifact = artifact_for("summary.json")
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        artifacts=[artifact],
        files={"summary.json": {"total": 42}},
    )

    result = only(evidence)

    assert result.passed is True
    assert result.required is True
    assert result.evidence_refs == [f"artifact:{artifact.artifact_id}"]


def test_file_json_rejects_wrong_total() -> None:
    assertion = {
        "id": "j1",
        "kind": "file_json",
        "required": True,
        "path": "summary.json",
        "schema": {"type": "object", "properties": {"total": {"type": "integer"}}},
        "checks": [{"pointer": "/total", "equals": 42}],
    }
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        artifacts=[artifact_for("summary.json")],
        files={"summary.json": {"total": 41}},
    )

    assert only(evidence).passed is False


def test_file_json_integer_and_boolean_are_distinct() -> None:
    assertion = {
        "id": "j1",
        "kind": "file_json",
        "required": True,
        "path": "summary.json",
        "schema": {"type": "object", "properties": {"total": {"type": "integer"}}},
        "checks": [{"pointer": "/total", "equals": 1}],
    }
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        artifacts=[artifact_for("summary.json")],
        files={"summary.json": {"total": True}},
    )

    assert only(evidence).passed is False


def test_file_json_schema_type_violation_is_failure() -> None:
    assertion = {
        "id": "j1",
        "kind": "file_json",
        "required": True,
        "path": "summary.json",
        "schema": {
            "type": "object",
            "properties": {"total": {"type": "integer"}},
            "additionalProperties": False,
        },
        "checks": [],
    }
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        artifacts=[artifact_for("summary.json")],
        files={"summary.json": {"total": "42"}},
    )

    result = only(evidence)

    assert result.passed is False
    assert result.message


# ------------------------------------------------------------- JSON Pointer（RFC 6901）


def test_json_pointer_missing_path_is_assertion_failure() -> None:
    assertion = {
        "id": "j1",
        "kind": "file_json",
        "required": True,
        "path": "summary.json",
        "schema": {"type": "object"},
        "checks": [{"pointer": "/missing", "equals": 1}],
    }
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        artifacts=[artifact_for("summary.json")],
        files={"summary.json": {"total": 42}},
    )

    assert only(evidence).passed is False


def test_json_pointer_unescapes_tilde_sequences() -> None:
    assertion = {
        "id": "j1",
        "kind": "answer_json",
        "required": True,
        "schema": {"type": "object"},
        "checks": [
            {"pointer": "/a~1b", "equals": "斜杠键"},
            {"pointer": "/c~0d", "equals": "波浪号键"},
        ],
    }
    answer = json.dumps({"a/b": "斜杠键", "c~d": "波浪号键"}, ensure_ascii=False)
    evidence = FakeAttemptEvidence(case_snapshot=case_with(assertion), final_answer=answer)

    assert only(evidence).passed is True


def test_json_pointer_empty_string_targets_whole_document() -> None:
    assertion = {
        "id": "j1",
        "kind": "answer_json",
        "required": True,
        "schema": {"type": "array", "minItems": 1},
        "checks": [{"pointer": "", "equals": [1, 2]}],
    }
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(assertion), final_answer=json.dumps([1, 2])
    )

    assert only(evidence).passed is True


# ------------------------------------------------------------------- SchemaSubset 白名单


def test_answer_json_uses_contract_schema_keywords() -> None:
    assertion = {
        "id": "j1",
        "kind": "answer_json",
        "required": True,
        "schema": {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["ok"]}, "items": {"type": "array", "maxItems": 2}},
            "required": ["status"],
        },
        "checks": [],
    }
    ok = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        final_answer=json.dumps({"status": "ok", "items": [1, 2]}),
    )
    bad_enum = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        final_answer=json.dumps({"status": "failed", "items": [1, 2]}),
    )
    too_many = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        final_answer=json.dumps({"status": "ok", "items": [1, 2, 3]}),
    )
    missing_required = FakeAttemptEvidence(
        case_snapshot=case_with(assertion), final_answer=json.dumps({"items": []})
    )

    assert only(ok).passed is True
    assert only(bad_enum).passed is False
    assert only(too_many).passed is False
    assert only(missing_required).passed is False


def test_remote_ref_assertion_is_rejected_before_grading() -> None:
    """契约 §2：SchemaSubset 不接受 ``$ref``/远程引用/正则/代码，判分侧无执行通道。"""
    assertion = {
        "id": "j1",
        "kind": "file_json",
        "required": True,
        "path": "summary.json",
        "schema": {"type": "object", "$ref": "https://example.com/s.json"},
        "checks": [],
    }
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        artifacts=[artifact_for("summary.json")],
        files={"summary.json": {"total": 42}},
    )

    with pytest.raises(ValidationError):
        evaluate_rules(evidence.case_snapshot, evidence)


def test_unknown_assertion_kind_is_rejected() -> None:
    assertion = {"id": "x1", "kind": "run_python", "required": True, "code": "print(1)"}
    evidence = FakeAttemptEvidence(case_snapshot=case_with(assertion))

    with pytest.raises(ValidationError):
        evaluate_rules(evidence.case_snapshot, evidence)


def test_non_json_answer_fails_instead_of_erroring() -> None:
    assertion = {
        "id": "j1",
        "kind": "answer_json",
        "required": True,
        "schema": {"type": "object"},
        "checks": [],
    }
    evidence = FakeAttemptEvidence(case_snapshot=case_with(assertion), final_answer="任务已完成")

    result = only(evidence)

    assert result.passed is False
    assert "ANSWER_NOT_JSON" in result.message


# --------------------------------------------------------------------------- 回答断言


def test_answer_contains_honours_case_sensitivity() -> None:
    sensitive = {
        "id": "a1",
        "kind": "answer_contains",
        "required": True,
        "text": "Alpha",
        "case_sensitive": True,
    }
    insensitive = dict(sensitive, id="a2", case_sensitive=False)
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(sensitive, assertions=[sensitive, insensitive]),
        final_answer="结论是 alpha",
    )

    results = evaluate_rules(evidence.case_snapshot, evidence)

    assert [item.passed for item in results] == [False, True]


def test_answer_not_contains_passes_when_absent() -> None:
    assertion = {
        "id": "a2",
        "kind": "answer_not_contains",
        "required": True,
        "text": "网络",
        "case_sensitive": True,
    }
    evidence = FakeAttemptEvidence(case_snapshot=case_with(assertion), final_answer="只用本地文件")

    assert only(evidence).passed is True


def test_missing_final_answer_is_unreadable_evidence() -> None:
    """无法读取证据 → passed=None，既不算失败也不算通过（契约 §4）。"""
    assertion = {
        "id": "a1",
        "kind": "answer_contains",
        "required": True,
        "text": "alpha",
        "case_sensitive": True,
    }
    evidence = FakeAttemptEvidence(case_snapshot=case_with(assertion), final_answer=None)

    result = only(evidence)

    assert result.passed is None
    assert "ANSWER_UNAVAILABLE" in result.message


# --------------------------------------------------------------------------- 文件断言


def test_file_exists_and_contains() -> None:
    exists_assertion = {"id": "f1", "kind": "file_exists", "required": True, "path": "output.txt"}
    contains_assertion = {
        "id": "f2",
        "kind": "file_contains",
        "required": False,
        "path": "output.txt",
        "text": "hello",
    }
    artifact = artifact_for("output.txt")
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(exists_assertion, assertions=[exists_assertion, contains_assertion]),
        artifacts=[artifact],
        files={"output.txt": "hello world"},
    )

    results = evaluate_rules(evidence.case_snapshot, evidence)

    assert [item.passed for item in results] == [True, True]
    assert results[0].required is True and results[1].required is False
    assert results[0].evidence_refs == [f"artifact:{artifact.artifact_id}"]


def test_registered_artifact_with_absent_file_fails_existence() -> None:
    """登记过但工作区里已不存在的产物：判失败（缺失产物），不是判分错误。"""
    assertion = {"id": "f1", "kind": "file_exists", "required": True, "path": "output.txt"}
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        artifacts=[artifact_for("output.txt")],
        files={},
    )

    assert only(evidence).passed is False


def test_unregistered_artifact_fails_without_being_read() -> None:
    """契约 §6.1：``artifact_id=None`` 的条目只能判失败，不得读取内容。"""
    assertion = {
        "id": "j1",
        "kind": "file_json",
        "required": True,
        "path": "summary.json",
        "schema": {"type": "object"},
        "checks": [{"pointer": "/total", "equals": 42}],
    }
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        artifacts=[artifact_for("summary.json", registered=False)],
        files={"summary.json": {"total": 42}},
    )

    result = only(evidence)

    assert result.passed is False
    assert "ARTIFACT_NOT_REGISTERED" in result.message
    assert evidence.read_calls == []


def test_absent_artifact_entry_fails_without_being_read() -> None:
    assertion = {"id": "f1", "kind": "file_exists", "required": True, "path": "ghost.json"}
    evidence = FakeAttemptEvidence(case_snapshot=case_with(assertion), files={"ghost.json": "{}"})

    result = only(evidence)

    assert result.passed is False
    assert evidence.read_calls == []


def test_path_outside_root_is_unreadable_not_failure() -> None:
    assertion = {"id": "f1", "kind": "file_exists", "required": True, "path": "../outside.txt"}
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        artifacts=[artifact_for("../outside.txt")],
    )

    result = only(evidence)

    assert result.passed is None
    assert "PATH_OUTSIDE_ROOT" in result.message


def test_filesystem_error_is_unreadable_not_failure() -> None:
    assertion = {
        "id": "j1",
        "kind": "file_json",
        "required": True,
        "path": "summary.json",
        "schema": {"type": "object"},
        "checks": [],
    }
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        artifacts=[artifact_for("summary.json")],
        missing_files=["summary.json"],
    )

    result = only(evidence)

    assert result.passed is None
    assert "EVIDENCE_UNREADABLE" in result.message


def test_binary_artifact_content_is_a_failure() -> None:
    assertion = {
        "id": "j1",
        "kind": "file_json",
        "required": True,
        "path": "summary.json",
        "schema": {"type": "object"},
        "checks": [],
    }
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        artifacts=[artifact_for("summary.json")],
        files={"summary.json": json.JSONDecodeError("binary", "doc", 0)},
    )

    result = only(evidence)

    assert result.passed is False
    assert "FILE_NOT_JSON" in result.message


# --------------------------------------------------------------------------- 工具断言


def test_tool_called_matches_arguments_subset_and_bounds() -> None:
    assertion = {
        "id": "t1",
        "kind": "tool_called",
        "required": True,
        "name": "test_lookup",
        "arguments_subset": {"city": "北京"},
        "min_calls": 1,
        "max_calls": 2,
    }
    ok = FakeAttemptEvidence(
        case_snapshot=case_with(assertion), tool_calls=[tool_call(seq=1), tool_call(seq=2)]
    )
    too_many = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        tool_calls=[tool_call(seq=1), tool_call(seq=2), tool_call(seq=3)],
    )
    wrong_args = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        tool_calls=[tool_call("test_lookup", {"city": "上海"}, seq=1)],
    )
    never = FakeAttemptEvidence(case_snapshot=case_with(assertion), tool_calls=[])

    assert only(ok).passed is True
    assert only(too_many).passed is False
    assert only(wrong_args).passed is False
    assert only(never).passed is False


def test_tool_called_matches_nested_arguments_but_keeps_bool_strict() -> None:
    assertion = {
        "id": "t1",
        "kind": "tool_called",
        "required": True,
        "name": "write_file",
        "arguments_subset": {"payload": {"flag": 1}},
        "min_calls": 1,
        "max_calls": None,
    }
    nested = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        tool_calls=[tool_call("write_file", {"payload": {"flag": 1, "extra": True}, "path": "a"})],
    )
    bool_confusion = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        tool_calls=[tool_call("write_file", {"payload": {"flag": True}})],
    )

    assert only(nested).passed is True
    assert only(bool_confusion).passed is False


def test_tool_called_error_call_still_counts_as_invocation() -> None:
    """B15：受控失败调用同样证明「做了这次调用决策」，``is_error`` 由工具错误率另计。"""
    assertion = {
        "id": "t1",
        "kind": "tool_called",
        "required": True,
        "name": "test_unstable",
        "arguments_subset": {},
        "min_calls": 2,
        "max_calls": None,
    }
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        tool_calls=[tool_call("test_unstable", {}, seq=1, is_error=True), tool_call("test_unstable", {}, seq=2)],
    )

    assert only(evidence).passed is True


def test_tool_forbidden_detects_any_call() -> None:
    assertion = {"id": "t2", "kind": "tool_forbidden", "required": True, "names": ["web_search"]}
    clean = FakeAttemptEvidence(case_snapshot=case_with(assertion), tool_calls=[])
    violated = FakeAttemptEvidence(
        case_snapshot=case_with(assertion), tool_calls=[tool_call("web_search", {}, seq=1)]
    )

    assert only(clean).passed is True
    assert only(violated).passed is False


def test_iteration_and_tool_call_limits() -> None:
    iteration = {"id": "l1", "kind": "iteration_limit", "required": True, "maximum": 2}
    call_limit = {"id": "l2", "kind": "tool_call_limit", "required": True, "maximum": 1}
    within = FakeAttemptEvidence(
        case_snapshot=case_with(iteration, assertions=[iteration, call_limit]),
        iterations=2,
        tool_calls=[tool_call(seq=1)],
    )
    beyond = FakeAttemptEvidence(
        case_snapshot=case_with(iteration, assertions=[iteration, call_limit]),
        iterations=3,
        tool_calls=[tool_call(seq=1), tool_call("read_file", {}, seq=2)],
    )

    assert [item.passed for item in evaluate_rules(within.case_snapshot, within)] == [True, True]
    assert [item.passed for item in evaluate_rules(beyond.case_snapshot, beyond)] == [False, False]


def test_tool_trace_ref_unresolvable_is_incomplete_evidence() -> None:
    """契约 §6.1：resolve_ref 未命中即证据不完整，不得判通过。"""
    assertion = {
        "id": "t1",
        "kind": "tool_called",
        "required": True,
        "name": "test_lookup",
        "arguments_subset": {},
        "min_calls": 1,
        "max_calls": None,
    }
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(assertion),
        tool_calls=[tool_call(seq=99)],
        trace_events=[],
    )

    result = only(evidence)

    assert result.passed is None
    assert "EVIDENCE_REF_UNRESOLVED" in result.message


# ----------------------------------------------------------------------- 结果与顺序


def test_results_follow_case_assertion_order_and_ids() -> None:
    first = {"id": "a1", "kind": "answer_contains", "required": True, "text": "结论", "case_sensitive": True}
    second = {"id": "f1", "kind": "file_exists", "required": False, "path": "missing.txt"}
    evidence = FakeAttemptEvidence(
        case_snapshot=case_with(first, assertions=[first, second]),
        final_answer="结论如下",
        artifacts=[artifact_for("missing.txt")],
        files={},
    )

    results = evaluate_rules(evidence.case_snapshot, evidence)

    assert [item.id for item in results] == ["a1", "f1"]
    assert [item.passed for item in results] == [True, False]
