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
