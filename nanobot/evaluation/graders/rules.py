"""确定性规则判分（契约 §2 断言口径 + §6.1 证据消费口径）。

设计取舍：

- **只读契约 §6.1 定义的证据成员**：``attempt_id`` / ``final_answer`` / ``iterations`` /
  ``tool_calls`` / ``artifacts`` 三个集合，加上 ``read_text`` / ``read_json`` / ``exists``
  三个读取入口与 ``resolve_ref``。判分侧不 ``open()``、不解析路径、不碰 workspace，也不
  import WU-03 的 ``evidence.py``——本模块按 ``Protocol`` 做结构化消费，字段名逐字照抄契约。
- **三态结论**：``passed`` 为 ``True/False/None``。读不到证据（路径越界、文件不可读、
  ``final_answer`` 缺失）一律 ``None``；``None`` 既不是失败也不是通过，由 ``verdict`` 归入
  ``review_required``，避免「采集故障」被伪装成「Agent 做错了」。
- **不执行任何用例内容**：断言只有契约 §2 的 kind 闭集与 SchemaSubset 白名单关键词；
  ``$ref``/正则/代码在模型层（``schemas.py``，``extra="forbid"``）就被拒掉，本模块没有
  动态 import、``eval``、正则或网络出口。递归深度受 SchemaSubset 的 16 层上限约束。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import TypeAdapter

from nanobot.evaluation.schemas import Assertion, AssertionResult, JsonValue, SchemaSubset

#: 未登记产物（契约 §6.1 ``artifact_id=None``）只能判失败，不允许读取内容。
ARTIFACT_NOT_REGISTERED = "ARTIFACT_NOT_REGISTERED"
#: 证据存在但读不出来（越界、IO 错误）。
EVIDENCE_UNREADABLE = "EVIDENCE_UNREADABLE"
#: 断言结论成立但引用无法解析（不得当作通过）。
EVIDENCE_REF_UNRESOLVED = "EVIDENCE_REF_UNRESOLVED"
#: 工具断言的引用前缀来源。
PATH_OUTSIDE_ROOT = "PATH_OUTSIDE_ROOT"

#: 工具类断言 kind：报告侧据此区分「工具用例正确率」分母。
TOOL_ASSERTION_KINDS = frozenset({"tool_called", "tool_forbidden", "tool_call_limit"})

_MISSING = object()


class ToolCallEvidenceView(Protocol):
    """契约 §6.1 ``ToolCallEvidence`` 的消费侧视图（字段名逐字一致）。

    成员一律声明为只读 ``@property``：判分侧只需要读，而 ``AttemptEvidence`` 交出的正是只读
    属性。写成可变属性的 Protocol 会反过来要求实现方可写，把只读边界判成不匹配。
    """

    @property
    def name(self) -> str: ...

    @property
    def arguments(self) -> dict[str, Any]: ...

    @property
    def result(self) -> JsonValue | None: ...

    @property
    def is_error(self) -> bool: ...

    @property
    def seq(self) -> int: ...

    @property
    def turn(self) -> int | None: ...

    @property
    def iteration(self) -> int | None: ...


class EvidenceArtifactView(Protocol):
    """契约 §6.1 ``EvidenceArtifact`` 的消费侧视图。"""

    @property
    def relative_path(self) -> str: ...

    @property
    def media_type(self) -> str: ...

    @property
    def size_bytes(self) -> int: ...

    @property
    def sha256(self) -> str: ...

    @property
    def artifact_id(self) -> Any: ...  # uuid.UUID | None


class AttemptEvidenceView(Protocol):
    """契约 §6.1 ``AttemptEvidence`` 的消费侧视图：判分可见范围的唯一定义。"""

    @property
    def attempt_id(self) -> str: ...

    @property
    def case_snapshot(self) -> dict[str, Any]: ...

    @property
    def final_answer(self) -> str | None: ...

    @property
    def stop_reason(self) -> str | None: ...

    @property
    def iterations(self) -> int: ...

    @property
    def trace_events(self) -> tuple[Any, ...]: ...

    @property
    def tool_calls(self) -> tuple[ToolCallEvidenceView, ...]: ...

    @property
    def artifacts(self) -> tuple[EvidenceArtifactView, ...]: ...

    @property
    def trace_complete(self) -> bool: ...

    @property
    def execution_status(self) -> str: ...

    def read_text(self, relative_path: str) -> str: ...

    def read_json(self, relative_path: str) -> JsonValue: ...

    def exists(self, relative_path: str) -> bool: ...

    def resolve_ref(self, ref: str) -> Any | None: ...


_ASSERTION_LIST = TypeAdapter(list[Assertion])


@dataclass(frozen=True)
class _Outcome:
    """单条断言的判分结论（三态 + 面向复核的短消息 + 证据引用）。"""

    passed: bool | None
    message: str = ""
    refs: tuple[str, ...] = ()


def evaluate_rules(
    case: Mapping[str, Any] | Any,
    evidence: AttemptEvidenceView,
) -> list[AssertionResult]:
    """按用例声明顺序产出 ``AssertionResult`` 列表；不访问外网、不执行用例代码。"""
    results: list[AssertionResult] = []
    for assertion in _assertions_of(case):
        outcome = _grade(assertion, evidence)
        results.append(
            AssertionResult(
                id=assertion.id,
                passed=outcome.passed,
                required=assertion.required,
                message=outcome.message,
                evidence_refs=list(outcome.refs),
            )
        )
    return results


def assertions_of(case: Mapping[str, Any] | Any) -> list[Any]:
    """公开入口：把契约 §2 的 ``assertions`` 解析为类型化断言模型（供报告侧分类工具断言）。"""
    return _assertions_of(case)


def _assertions_of(case: Mapping[str, Any] | Any) -> list[Any]:
    raw = case.get("assertions") if isinstance(case, Mapping) else getattr(case, "assertions", None)
    if raw is None:
        raise ValueError("ASSERTIONS_UNAVAILABLE")
    return list(_ASSERTION_LIST.validate_python(raw))


def _grade(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    kind = assertion.kind
    if kind in ("answer_contains", "answer_not_contains"):
        outcome = _grade_answer_text(assertion, evidence)
    elif kind == "answer_json":
        outcome = _grade_answer_json(assertion, evidence)
    elif kind == "file_json":
        outcome = _grade_file_json(assertion, evidence)
    elif kind == "file_exists":
        outcome = _grade_file_exists(assertion, evidence)
    elif kind == "file_contains":
        outcome = _grade_file_contains(assertion, evidence)
    elif kind == "tool_called":
        outcome = _grade_tool_called(assertion, evidence)
    elif kind == "tool_forbidden":
        outcome = _grade_tool_forbidden(assertion, evidence)
    elif kind == "iteration_limit":
        outcome = _grade_iteration_limit(assertion, evidence)
    elif kind == "tool_call_limit":
        outcome = _grade_tool_call_limit(assertion, evidence)
    elif kind == "answer_exact":
        outcome = _grade_answer_exact(assertion, evidence)
    elif kind == "answer_length":
        outcome = _grade_answer_length(assertion, evidence)
    elif kind == "answer_json_block":
        outcome = _grade_answer_json_block(assertion, evidence)
    elif kind == "tool_trajectory":
        outcome = _grade_tool_trajectory(assertion, evidence)
    elif kind == "team_member_executed":
        outcome = _grade_team_member_executed(assertion, evidence)
    elif kind == "usage_limit":
        outcome = _grade_usage_limit(assertion, evidence)
    elif kind == "duration_limit":
        outcome = _grade_duration_limit(assertion, evidence)
    else:  # pragma: no cover - 判别联合已在模型层拒绝未知 kind
        return _Outcome(None, f"ASSERTION_KIND_UNSUPPORTED:{kind}")
    return _downgrade_unresolved_ref(outcome, evidence)


def _downgrade_unresolved_ref(outcome: _Outcome, evidence: AttemptEvidenceView) -> _Outcome:
    """结论为「通过」但引用解析不到时降级为 ``None``：证据不完整不得当作通过。"""
    if outcome.passed is not True:
        return outcome
    for ref in outcome.refs:
        if evidence.resolve_ref(ref) is None:
            message = outcome.message or EVIDENCE_REF_UNRESOLVED
            combined = message if EVIDENCE_REF_UNRESOLVED in message else (
                f"{message} {EVIDENCE_REF_UNRESOLVED}"
            )
            return _Outcome(None, combined.strip(), outcome.refs)
    return outcome


# --------------------------------------------------------------------------- 回答断言


def _grade_answer_text(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    answer = evidence.final_answer
    if answer is None:
        return _Outcome(None, "ANSWER_UNAVAILABLE")
    needle, haystack = _case_aligned(assertion.text, answer, assertion.case_sensitive)
    contains = needle in haystack
    if assertion.kind == "answer_contains":
        return _Outcome(contains, "" if contains else f"ANSWER_MISSING_TEXT:{assertion.text}")
    return _Outcome(not contains, "" if not contains else f"ANSWER_FORBIDDEN_TEXT:{assertion.text}")


def _case_aligned(needle: str, haystack: str, case_sensitive: bool) -> tuple[str, str]:
    if case_sensitive:
        return needle, haystack
    return needle.casefold(), haystack.casefold()


def _grade_answer_json(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    answer = evidence.final_answer
    if answer is None:
        return _Outcome(None, "ANSWER_UNAVAILABLE")
    try:
        document = json.loads(answer)
    except ValueError:
        return _Outcome(False, "ANSWER_NOT_JSON:回答不是合法 JSON")
    return _grade_json_document(assertion, document, source="answer")


# --------------------------------------------------------------------------- 文件断言


@dataclass(frozen=True)
class _ArtifactRead:
    status: str  # "ok" | "missing_entry" | "not_registered" | "unreadable" | "invalid_content"
    value: Any = None
    message: str = ""
    ref: str = ""


def _artifact_for(evidence: AttemptEvidenceView, path: str) -> EvidenceArtifactView | None:
    for artifact in evidence.artifacts:
        if artifact.relative_path == path:
            return artifact
    return None


def _read_artifact(
    evidence: AttemptEvidenceView,
    path: str,
    mode: str,  # "exists" | "text" | "json"
) -> _ArtifactRead:
    """文件类断言的唯一读取通道：先校验登记状态，再走 §6.1 的三个入口。"""
    artifact = _artifact_for(evidence, path)
    if artifact is None:
        return _ArtifactRead("missing_entry", message=f"{ARTIFACT_NOT_REGISTERED}:产物未登记 {path}")
    if artifact.artifact_id is None:
        return _ArtifactRead(
            "not_registered", message=f"{ARTIFACT_NOT_REGISTERED}:未登记产物不可读取 {path}"
        )
    ref = f"artifact:{artifact.artifact_id}"
    try:
        if mode == "exists":
            return _ArtifactRead("ok", value=evidence.exists(path), ref=ref)
        if mode == "text":
            return _ArtifactRead("ok", value=evidence.read_text(path), ref=ref)
        return _ArtifactRead("ok", value=evidence.read_json(path), ref=ref)
    except ValueError as exc:
        if PATH_OUTSIDE_ROOT in str(exc):
            return _ArtifactRead("unreadable", message=f"{PATH_OUTSIDE_ROOT}:{exc}", ref=ref)
        label = "FILE_NOT_JSON" if mode == "json" else "FILE_NOT_TEXT"
        return _ArtifactRead(
            "invalid_content", value=exc, message=f"{label}:内容不可解析为文本/JSON（{exc}）", ref=ref
        )
    except OSError as exc:
        return _ArtifactRead("unreadable", message=f"{EVIDENCE_UNREADABLE}:{exc}", ref=ref)


def _grade_file_exists(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    read = _read_artifact(evidence, assertion.path, "exists")
    if read.status in ("missing_entry", "not_registered"):
        return _Outcome(False, read.message)
    if read.status in ("unreadable", "invalid_content"):
        return _Outcome(None, read.message, (read.ref,))
    return _Outcome(bool(read.value), "" if read.value else f"FILE_ABSENT:{assertion.path}", (read.ref,))


def _grade_file_contains(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    read = _read_artifact(evidence, assertion.path, "text")
    if read.status in ("missing_entry", "not_registered"):
        return _Outcome(False, read.message)
    if read.status in ("unreadable", "invalid_content"):
        return _Outcome(None, read.message, (read.ref,))
    needle, haystack = _case_aligned(assertion.text, str(read.value), True)
    contains = needle in haystack
    return _Outcome(
        contains,
        "" if contains else f"FILE_MISSING_TEXT:{assertion.text}",
        (read.ref,),
    )


def _grade_file_json(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    read = _read_artifact(evidence, assertion.path, "json")
    if read.status in ("missing_entry", "not_registered"):
        return _Outcome(False, read.message)
    if read.status == "unreadable":
        return _Outcome(None, read.message, (read.ref,))
    if read.status == "invalid_content":
        return _Outcome(False, read.message, (read.ref,))
    outcome = _grade_json_document(assertion, read.value, source=assertion.path)
    return _Outcome(outcome.passed, outcome.message, (read.ref,) if read.ref else ())


# ------------------------------------------------------------------ JSON 结构与指针


def _grade_json_document(assertion: Any, document: Any, *, source: str) -> _Outcome:
    schema_error = _check_schema(assertion.schema, document, source)
    if schema_error is not None:
        return _Outcome(False, schema_error)
    for check in assertion.checks:
        found = _resolve_pointer(document, check.pointer)
        if found is _MISSING:
            return _Outcome(False, f"POINTER_MISSING:{check.pointer}")
        if not _json_equals(found, check.equals):
            return _Outcome(
                False,
                f"POINTER_MISMATCH:{check.pointer} 期望 {json.dumps(check.equals, ensure_ascii=False)}"
                f" 实际 {json.dumps(found, ensure_ascii=False)}",
            )
    return _Outcome(True)


def _resolve_pointer(document: Any, pointer: str) -> Any:
    """RFC 6901：空指针指向整份文档，``~1`` → ``/``、``~0`` → ``~``；缺失路径返回哨兵。"""
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        return _MISSING
    current = document
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit():
                return _MISSING
            index = int(token)
            if index >= len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def _type_matches(declared: str | None, value: Any) -> bool:
    if declared is None:
        return True
    if declared == "null":
        return value is None
    if declared == "boolean":
        return isinstance(value, bool)
    if declared == "integer":
        # 契约 §2：整数与布尔严格区分，bool 是 int 的子类必须显式排除。
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared == "string":
        return isinstance(value, str)
    if declared == "array":
        return isinstance(value, list)
    if declared == "object":
        return isinstance(value, dict)
    return False


def _check_schema(node: SchemaSubset, value: Any, where: str) -> str | None:
    """SchemaSubset 白名单校验；递归深度由模型层的 16 层上限保证，不做无界工作。"""
    if not _type_matches(node.type, value):
        return f"SCHEMA_TYPE:{where} 期望 {node.type} 实际 {_json_type_name(value)}"
    if node.enum is not None and not any(_json_equals(value, option) for option in node.enum):
        return f"SCHEMA_ENUM:{where} 取值不在允许列表内"
    if isinstance(value, dict):
        return _check_object(node, value, where)
    if isinstance(value, list):
        return _check_array(node, value, where)
    return None


def _check_object(node: SchemaSubset, value: dict[str, Any], where: str) -> str | None:
    for key in node.required or ():
        if key not in value:
            return f"SCHEMA_REQUIRED:{where} 缺少必填字段 {key}"
    properties = node.properties or {}
    for key, child in properties.items():
        if key not in value:
            continue
        error = _check_schema(child, value[key], f"{where}.{key}")
        if error is not None:
            return error
    if node.additionalProperties is False:
        extras = sorted(set(value) - set(properties))
        if extras:
            return f"SCHEMA_ADDITIONAL_PROPERTIES:{where} 含未声明字段 {', '.join(extras)}"
    return None


def _check_array(node: SchemaSubset, value: list[Any], where: str) -> str | None:
    if node.minItems is not None and len(value) < node.minItems:
        return f"SCHEMA_MIN_ITEMS:{where} 元素少于 {node.minItems}"
    if node.maxItems is not None and len(value) > node.maxItems:
        return f"SCHEMA_MAX_ITEMS:{where} 元素多于 {node.maxItems}"
    if node.items is None:
        return None
    for index, element in enumerate(value):
        error = _check_schema(node.items, element, f"{where}[{index}]")
        if error is not None:
            return error
    return None


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _json_equals(left: Any, right: Any) -> bool:
    """严格比较：布尔与整数互不相等，数组/对象递归比较。"""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            return False
        return all(_json_equals(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return False
        return all(_json_equals(a, b) for a, b in zip(left, right, strict=True))
    if left is None or right is None:
        return left is None and right is None
    return False


# --------------------------------------------------------------------------- 工具断言


def _matching_tool_calls(assertion: Any, evidence: AttemptEvidenceView) -> list[Any]:
    matched = []
    for call in evidence.tool_calls:
        if call.name != assertion.name:
            continue
        if _arguments_match(call.arguments, assertion.arguments_subset):
            matched.append(call)
    return matched


def _arguments_match(actual: Any, subset: Mapping[str, Any]) -> bool:
    """``arguments_subset`` 语义：声明的键必须存在；嵌套字典继续按子集匹配。"""
    if not isinstance(actual, Mapping):
        return False
    for key, expected in subset.items():
        if key not in actual:
            return False
        found = actual[key]
        if isinstance(expected, Mapping) and isinstance(found, Mapping):
            if not _arguments_match(found, expected):
                return False
        elif not _json_equals(found, expected):
            return False
    return True


def _grade_tool_called(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    matched = _matching_tool_calls(assertion, evidence)
    count = len(matched)
    refs = tuple(f"trace:{evidence.attempt_id}:{call.seq}" for call in matched)
    if count < assertion.min_calls:
        return _Outcome(False, f"TOOL_MIN_CALLS:{assertion.name} 调用 {count} 次 < {assertion.min_calls}", refs)
    if assertion.max_calls is not None and count > assertion.max_calls:
        return _Outcome(False, f"TOOL_MAX_CALLS:{assertion.name} 调用 {count} 次 > {assertion.max_calls}", refs)
    return _Outcome(True, "", refs)


def _grade_tool_forbidden(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    forbidden = set(assertion.names)
    violated = [call for call in evidence.tool_calls if call.name in forbidden]
    refs = tuple(f"trace:{evidence.attempt_id}:{call.seq}" for call in violated)
    if violated:
        names = ", ".join(sorted({call.name for call in violated}))
        return _Outcome(False, f"TOOL_FORBIDDEN_USED:{names}", refs)
    return _Outcome(True)


def _grade_iteration_limit(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    iterations = evidence.iterations
    return _Outcome(
        iterations <= assertion.maximum,
        "" if iterations <= assertion.maximum else f"ITERATION_LIMIT_EXCEEDED:{iterations} > {assertion.maximum}",
    )


def _grade_tool_call_limit(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    count = len(evidence.tool_calls)
    return _Outcome(
        count <= assertion.maximum,
        "" if count <= assertion.maximum else f"TOOL_CALL_LIMIT_EXCEEDED:{count} > {assertion.maximum}",
    )

# ------------------------------------------------------------------ v2 声明式规则


def _grade_answer_exact(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    """精确匹配：去掉首尾空白后整段比较，命中关键词不算通过。"""
    answer = evidence.final_answer
    if answer is None:
        return _Outcome(None, "ANSWER_UNAVAILABLE")
    actual = answer.strip()
    expected = assertion.text.strip()
    if not assertion.case_sensitive:
        actual = actual.casefold()
        expected = expected.casefold()
    if actual == expected:
        return _Outcome(True, "")
    return _Outcome(
        False,
        f"ANSWER_NOT_EXACT:expected={expected[:80]!r} actual={actual[:80]!r}",
    )


def _grade_answer_length(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    """长度按 Unicode 码点计；超限是硬约束失败，不是"风格问题"。"""
    answer = evidence.final_answer
    if answer is None:
        return _Outcome(None, "ANSWER_UNAVAILABLE")
    length = len(answer)
    if length > assertion.max_chars:
        return _Outcome(
            False, f"ANSWER_TOO_LONG:{length}>{assertion.max_chars}"
        )
    if length < assertion.min_chars:
        return _Outcome(
            False, f"ANSWER_TOO_SHORT:{length}<{assertion.min_chars}"
        )
    return _Outcome(True, "")


def _grade_answer_json_block(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    """答案里必须恰好一个 JSON 围栏块；多个或零个都判失败。"""
    answer = evidence.final_answer
    if answer is None:
        return _Outcome(None, "ANSWER_UNAVAILABLE")
    blocks = _json_blocks(answer)
    if not blocks:
        return _Outcome(False, "ANSWER_JSON_BLOCK_MISSING")
    if len(blocks) > 1:
        return _Outcome(False, f"ANSWER_JSON_BLOCK_AMBIGUOUS:{len(blocks)}")
    try:
        document = json.loads(blocks[0])
    except (ValueError, TypeError):
        return _Outcome(False, "ANSWER_JSON_BLOCK_INVALID")
    return _grade_json_document(
        assertion, document, source="ANSWER_JSON_BLOCK"
    )


def _json_blocks(text: str) -> list[str]:
    """抓取围栏里的 JSON；没有围栏时退回整段（便于历史用例继续工作）。"""
    blocks: list[str] = []
    marker = "```"
    index = text.find(marker)
    while index >= 0:
        start = index + len(marker)
        end = text.find(marker, start)
        if end < 0:
            break
        body = text[start:end].strip()
        if body.startswith("json"):
            body = body[4:].strip()
        if body:
            blocks.append(body)
        index = text.find(marker, end + len(marker))
    return blocks


def _grade_tool_trajectory(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    """轨迹匹配：按 call_id 一对一，一次调用不能满足多步。"""
    calls = list(evidence.tool_calls)
    if not calls:
        return _Outcome(None, "TOOL_TRAJECTORY_UNAVAILABLE")
    steps = list(assertion.steps)
    used: set[int] = set()
    matched: list[int] = []
    if assertion.mode == "in_order":
        cursor = 0
        for step in steps:
            found = None
            for index in range(cursor, len(calls)):
                if index in used:
                    continue
                if _call_matches_step(calls[index], step):
                    found = index
                    break
            if found is None:
                return _Outcome(
                    False, f"TOOL_TRAJECTORY_MISSING_STEP:{step.name}"
                )
            used.add(found)
            matched.append(found)
            cursor = found + 1
        return _Outcome(True, "")
    if assertion.mode == "any_order":
        for step in steps:
            found = None
            for index, call in enumerate(calls):
                if index in used:
                    continue
                if _call_matches_step(call, step):
                    found = index
                    break
            if found is None:
                return _Outcome(
                    False, f"TOOL_TRAJECTORY_MISSING_STEP:{step.name}"
                )
            used.add(found)
        return _Outcome(True, "")
    # exact：调用序列与步骤序列一一对应
    if len(calls) != len(steps):
        return _Outcome(
            False, f"TOOL_TRAJECTORY_LENGTH_MISMATCH:{len(calls)}!={len(steps)}"
        )
    for call, step in zip(calls, steps, strict=True):
        if not _call_matches_step(call, step):
            return _Outcome(False, f"TOOL_TRAJECTORY_STEP_MISMATCH:{step.name}")
    return _Outcome(True, "")


def _call_matches_step(call: Any, step: Any) -> bool:
    if str(call.name) != str(step.name):
        return False
    subset = dict(step.arguments_subset or {})
    return _arguments_match(call.arguments, subset)


def _grade_team_member_executed(
    assertion: Any, evidence: AttemptEvidenceView
) -> _Outcome:
    """成员真实执行：读 member 终态事件，不采信最终文本里的自报。"""
    events = list(evidence.trace_events)
    if not events:
        return _Outcome(None, "TEAM_MEMBER_EVENTS_UNAVAILABLE")
    completed: set[str] = set()
    for event in events:
        if str(getattr(event, "type", "")) != "member.completed":
            continue
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            continue
        member_id = payload.get("member_id")
        status = str(payload.get("status") or "")
        if isinstance(member_id, str) and member_id and status == "succeeded":
            completed.add(member_id)
    missing = [
        member_id for member_id in assertion.member_ids if member_id not in completed
    ]
    if missing:
        return _Outcome(
            False, "TEAM_MEMBER_NOT_EXECUTED:" + ",".join(sorted(missing))
        )
    return _Outcome(True, "")


def _grade_usage_limit(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    """Token 预算：缺 usage 时判不可评，绝不补 0 当成"没超"。"""
    usage = _usage_of(evidence)
    if usage is None:
        return _Outcome(None, "USAGE_UNAVAILABLE")
    total = usage.get("total_tokens")
    output = usage.get("output_tokens")
    checks = (
        (assertion.max_total_tokens, total, "TOTAL_TOKENS"),
        (assertion.max_output_tokens, output, "OUTPUT_TOKENS"),
    )
    for limit, actual, label in checks:
        if limit is None:
            continue
        if actual is None:
            return _Outcome(None, f"USAGE_{label}_UNAVAILABLE")
        if int(actual) > int(limit):
            return _Outcome(False, f"USAGE_LIMIT_EXCEEDED:{label}:{actual}>{limit}")
    if assertion.max_tool_calls is not None:
        calls = len(list(evidence.tool_calls))
        if calls > int(assertion.max_tool_calls):
            return _Outcome(
                False,
                f"USAGE_LIMIT_EXCEEDED:TOOL_CALLS:{calls}>{assertion.max_tool_calls}",
            )
    return _Outcome(True, "")


def _usage_of(evidence: AttemptEvidenceView) -> Mapping[str, Any] | None:
    getter = getattr(evidence, "usage", None)
    if isinstance(getter, Mapping):
        return getter
    return None


def _grade_duration_limit(assertion: Any, evidence: AttemptEvidenceView) -> _Outcome:
    """耗时预算：缺计时时判不可评。"""
    getter = getattr(evidence, "duration_ms", None)
    if getter is None:
        return _Outcome(None, "DURATION_UNAVAILABLE")
    actual = int(getter)
    if actual > int(assertion.max_duration_ms):
        return _Outcome(
            False, f"DURATION_LIMIT_EXCEEDED:{actual}>{assertion.max_duration_ms}"
        )
    return _Outcome(True, "")
