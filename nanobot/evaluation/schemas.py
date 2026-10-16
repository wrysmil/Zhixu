"""契约 §2/§4/§5 的输入与读出模型（唯一入参校验点）。

设计取舍：

- 全部 ``extra="forbid"``，且默认走 **strict** 标量：契约 §2 末尾要求整数与布尔严格区分，
  Pydantic 的 lax 模式会把 ``1`` 收成 ``True``、把 ``True`` 收成 ``1``，因此这里对每个
  标量字段显式收紧，避免"看起来合法但语义变了"的载荷落库。
- 集合类上限（用例数、条目数、字节合计）在字段级用 ``max_length``，跨字段合计（fixture
  2 MiB、tool_fixture 1 MiB、memory_seed 64000 字、SchemaSubset 节点数）在 model 级
  用计数器校验，错误码以字符串形式出现在 ``ValidationError`` 里，供 WU-05 映射到 422。
- 客户端可控的相对路径一律过 ``validate_relative_path``：这里只做语法层拦截，工作区尚未
  创建，真正的目录包含判定由 ``resolve_inside`` 在运行时完成。
"""

from __future__ import annotations

import json
import sys
import uuid
import warnings
from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from nanobot.evaluation.validation import validate_relative_path

if sys.version_info >= (3, 12):  # pragma: no cover - 解释器版本分支
    from typing import TypeAliasType
else:  # pragma: no cover - 3.11 走 typing_extensions（pydantic 的既有依赖）
    from typing_extensions import TypeAliasType

# ---- 复用标量 ------------------------------------------------------------


def _uuid_input(value: Any) -> Any:
    """pydantic 的 lax UUID 会把 ``""`` 收成 nil UUID，空串必须在上游就拒掉。"""
    if isinstance(value, str) and not value.strip():
        raise ValueError("INVALID_UUID")
    return value


Uuid = Annotated[uuid.UUID, BeforeValidator(_uuid_input)]
#: 非空短标识：服务端生成或客户端提交的 id、kind 名、维度 id 等。
Identifier = Annotated[StrictStr, Field(min_length=1, max_length=40)]
#: revision / seq / position 一类的正整数序号。
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
#: 契约 §1「耗时非负毫秒」；JSON 里是 number，但 position/time 不允许小数以外的东西。
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
Mib = 1024 * 1024


class JsonModel(BaseModel):
    """所有评估域模型的共同基类：拒绝未知字段，不做静默透传。"""

    model_config = ConfigDict(extra="forbid")


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("BLANK_FIELD")
    return value


# 严格 JSON 值：契约 §2 tool_fixtures.result / checks.equals / evidence 都是任意 JSON，
# 但不接受 bytes、set、tuple、NaN 等非 JSON 类型，也不把 1 收成 True。递归别名必须走
# TypeAliasType —— 直接写 `list["JsonValue"]` 会在 schema 生成期无限递归。
JsonValue = TypeAliasType(
    "JsonValue",
    Union[
        StrictStr,
        StrictBool,
        StrictInt,
        Annotated[StrictFloat, Field(allow_inf_nan=False)],
        None,
        Annotated[list["JsonValue"], Field(strict=True)],
        Annotated[dict[str, "JsonValue"], Field(strict=True)],
    ],
)
JsonObject = dict[str, JsonValue]


# =========================================================================== 量表


NonEmptyLine = Annotated[StrictStr, Field(min_length=1, max_length=500)]


class RubricDimension(JsonModel):
    """契约 §2：五档锚点齐备是硬约束，少一档 0–4 分口径就无法复核。"""

    id: Identifier
    name: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    anchors: Annotated[list[NonEmptyLine], Field(min_length=5, max_length=5)]
    pass_score: Annotated[int, Field(strict=True, ge=0, le=4)] = 3

    @model_validator(mode="after")
    def _anchors_not_blank(self) -> "RubricDimension":
        for anchor in self.anchors:
            _non_blank(anchor)
        return self


class Rubric(JsonModel):
    version: Annotated[StrictStr, Field(min_length=1, max_length=40)]
    required: StrictBool
    dimensions: Annotated[list[RubricDimension], Field(min_length=1, max_length=6)]


# =========================================================================== SchemaSubset

SCHEMA_MAX_DEPTH = 16
SCHEMA_MAX_NODES = 1000
SCHEMA_TYPES = Literal[
    "object", "array", "string", "number", "integer", "boolean", "null"
]


class SchemaSubset(JsonModel):
    """契约 §2 的声明式 JSON 结构子集。

    只支持校验所需的关键词；``$ref``、``pattern``、``$where`` 等一切可指向远程或执行代码
    的关键词都会被 ``extra="forbid"`` 拒掉，无需再逐个枚举。
    """

    type: SCHEMA_TYPES | None = None
    properties: dict[str, "SchemaSubset"] | None = None
    required: list[StrictStr] | None = None
    items: "SchemaSubset | None" = None
    enum: list[JsonValue] | None = None
    # 下面三个是 JSON Schema 规范的关键词原名，契约 §2 要求按原样收发，不能改成 snake_case。
    additionalProperties: StrictBool | None = None  # noqa: N815
    minItems: NonNegativeInt | None = None  # noqa: N815
    maxItems: NonNegativeInt | None = None  # noqa: N815

    @model_validator(mode="after")
    def _within_limits(self) -> "SchemaSubset":
        remaining = [SCHEMA_MAX_NODES]
        depth = self._measure(remaining)
        if depth > SCHEMA_MAX_DEPTH:
            raise ValueError("SCHEMA_TOO_DEEP")
        if remaining[0] < 0:
            raise ValueError("SCHEMA_TOO_MANY_NODES")
        return self

    def _measure(self, remaining: list[int]) -> int:
        """后序遍历求深度并累计节点数；超限立刻抛出，避免对恶意载荷做无界工作。"""
        remaining[0] -= 1
        if remaining[0] < 0:
            raise ValueError("SCHEMA_TOO_MANY_NODES")
        child_depth = 0
        for child in (self.properties or {}).values():
            child_depth = max(child_depth, child._measure(remaining))
        if self.items is not None:
            child_depth = max(child_depth, self.items._measure(remaining))
        return 1 + child_depth


# =========================================================================== 断言

#: RFC 6901：空串指向整个文档，缺 ``/`` 前缀不是合法 pointer。
JsonPointer = Annotated[StrictStr, Field(max_length=1024)]


class AssertionCheck(JsonModel):
    pointer: JsonPointer
    equals: JsonValue


class _AssertionBase(JsonModel):
    id: Identifier
    required: StrictBool


# 契约 §2 的 JSON 断言字段就叫 ``schema``，与 pydantic 已废弃的 ``BaseModel.schema``
# 同名；线上字段名不能改，这里只关掉这一条装饰性告警，其余告警照常抛出。
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message='Field name "schema"')

    class AnswerContainsAssertion(_AssertionBase):
        kind: Literal["answer_contains"]
        text: Annotated[StrictStr, Field(min_length=1, max_length=32000)]
        case_sensitive: StrictBool = True

    class AnswerNotContainsAssertion(_AssertionBase):
        kind: Literal["answer_not_contains"]
        text: Annotated[StrictStr, Field(min_length=1, max_length=32000)]
        case_sensitive: StrictBool = True

    class AnswerJsonAssertion(_AssertionBase):
        kind: Literal["answer_json"]
        schema: SchemaSubset  # pyright: ignore[reportIncompatibleMethodOverride] 契约字段名
        checks: list[AssertionCheck] = Field(default_factory=list)

    class FileJsonAssertion(_AssertionBase):
        kind: Literal["file_json"]
        path: StrictStr
        schema: SchemaSubset  # pyright: ignore[reportIncompatibleMethodOverride] 契约字段名
        checks: list[AssertionCheck] = Field(default_factory=list)


class FileExistsAssertion(_AssertionBase):
    kind: Literal["file_exists"]
    path: StrictStr


class FileContainsAssertion(_AssertionBase):
    kind: Literal["file_contains"]
    path: StrictStr
    text: Annotated[StrictStr, Field(max_length=32000)]


class ToolCalledAssertion(_AssertionBase):
    kind: Literal["tool_called"]
    name: Identifier
    arguments_subset: JsonObject = Field(default_factory=dict)
    min_calls: NonNegativeInt
    max_calls: NonNegativeInt | None = None

    @model_validator(mode="after")
    def _bounds_ordered(self) -> "ToolCalledAssertion":
        if self.max_calls is not None and self.max_calls < self.min_calls:
            raise ValueError("TOOL_CALL_BOUNDS_INVERTED")
        return self


class ToolForbiddenAssertion(_AssertionBase):
    kind: Literal["tool_forbidden"]
    names: Annotated[list[Identifier], Field(min_length=1, max_length=50)]


class IterationLimitAssertion(_AssertionBase):
    kind: Literal["iteration_limit"]
    maximum: PositiveInt


class ToolCallLimitAssertion(_AssertionBase):
    kind: Literal["tool_call_limit"]
    maximum: PositiveInt


Assertion = Annotated[
    Union[
        AnswerContainsAssertion,
        AnswerNotContainsAssertion,
        AnswerJsonAssertion,
        FileJsonAssertion,
        FileExistsAssertion,
        FileContainsAssertion,
        ToolCalledAssertion,
        ToolForbiddenAssertion,
        IterationLimitAssertion,
        ToolCallLimitAssertion,
    ],
    Field(discriminator="kind"),
]


def _assertion_path_check(assertion: object) -> None:
    """带 ``path`` 的断言变体统一走路径语法校验。"""
    path = getattr(assertion, "path", None)
    if isinstance(path, str):
        validate_relative_path(path)


# =========================================================================== 用例输入


class Turn(JsonModel):
    content: Annotated[StrictStr, Field(min_length=1, max_length=32000)]


class Fixture(JsonModel):
    path: StrictStr
    content: StrictStr
    encoding: Literal["utf-8"]


class MemorySeed(JsonModel):
    kind: Literal["semantic"]
    content: Annotated[StrictStr, Field(min_length=1)]


class ToolFixture(JsonModel):
    tool_name: Literal["test_lookup", "test_unstable"]
    arguments_match: JsonObject = Field(default_factory=dict)
    result: JsonValue
    #: 契约 §2 的受控工具错误开关；不声明即视为受控成功结果。
    is_error: StrictBool = False


class ExecutionConfig(JsonModel):
    tool_mode: Literal["fixture", "live"]
    allowed_tools: list[StrictStr]
    max_iterations: Annotated[int, Field(strict=True, ge=1, le=50)] = 12
    timeout_s: Annotated[int, Field(strict=True, ge=5, le=900)] = 120

    @model_validator(mode="after")
    def _tools_are_allowlisted(self) -> "ExecutionConfig":
        if not self.allowed_tools:
            raise ValueError("ALLOWED_TOOLS_EMPTY")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("ALLOWED_TOOLS_DUPLICATED")
        for name in self.allowed_tools:
            if name not in FIXTURE_TOOLS and not (
                self.tool_mode == "live" and name in LIVE_ONLY_TOOLS
            ):
                raise ValueError(f"TOOL_NOT_ALLOWED:{name}")
        return self


FIXTURE_TOOLS = frozenset(
    {
        "read_file", "write_file", "edit_file", "list_dir",
        "test_lookup", "test_unstable",
    }
)
#: 契约 §2：live 模式才可额外选择的只读网络工具，且保留项目网络保护。
LIVE_ONLY_TOOLS = frozenset({"web_search", "web_fetch"})


class _CaseFields(JsonModel):
    """CaseWrite 与 CaseRead 的共有字段；base_revision 只属于写入侧，故不在此处。"""

    name: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    description: Annotated[StrictStr, Field(max_length=4000)] = ""
    tags: Annotated[
        list[Annotated[StrictStr, Field(min_length=1, max_length=40)]],
        Field(max_length=20),
    ] = Field(default_factory=list)
    enabled: StrictBool = True
    suite_id: Uuid
    turns: Annotated[list[Turn], Field(min_length=1, max_length=20)]
    fixtures: Annotated[list[Fixture], Field(max_length=50)] = Field(default_factory=list)
    memory_seed: Annotated[
        list[MemorySeed], Field(max_length=20)
    ] = Field(default_factory=list)
    clock_iso: StrictStr | None = None
    tool_fixtures: Annotated[
        list[ToolFixture], Field(max_length=50)
    ] = Field(default_factory=list)
    execution: ExecutionConfig
    assertions: Annotated[list[Assertion], Field(min_length=1, max_length=50)]
    rubric: Rubric | None = None
    artifact_paths: Annotated[list[StrictStr], Field(max_length=50)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _limits_and_shape(self) -> "_CaseFields":
        _non_blank(self.name)
        for turn in self.turns:
            _non_blank(turn.content)
        if self.clock_iso is not None:
            _parse_iso8601(self.clock_iso)
        _check_fixtures(self.fixtures)
        _check_memory_seed(self.memory_seed)
        _check_tool_fixtures(self.tool_fixtures)
        for raw in self.artifact_paths:
            validate_relative_path(raw)
        for assertion in self.assertions:
            _assertion_path_check(assertion)
        if not self._has_required_rule():
            raise ValueError("REQUIRED_RULE_OR_RUBRIC")
        return self

    def _has_required_rule(self) -> bool:
        """契约 §4：没有必过规则也没有必需量表时，服务端给不出任何 verdict。"""
        if any(item.required for item in self.assertions):
            return True
        return self.rubric is not None and self.rubric.required


class CaseWrite(_CaseFields):
    base_revision: PositiveInt | None = None


class CaseRead(_CaseFields):
    """契约 §2：读模型去掉 base_revision，带上服务端生成的标识与软删标记。"""

    id: Uuid
    revision: PositiveInt
    created_at: datetime
    updated_at: datetime
    is_deleted: StrictBool = False


class SuiteWrite(JsonModel):
    name: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    description: Annotated[StrictStr, Field(max_length=4000)] = ""
    tags: Annotated[
        list[Annotated[StrictStr, Field(min_length=1, max_length=40)]],
        Field(max_length=20),
    ] = Field(default_factory=list)
    base_revision: PositiveInt | None = None

    @model_validator(mode="after")
    def _name_not_blank(self) -> "SuiteWrite":
        _non_blank(self.name)
        return self


class SuiteRead(JsonModel):
    id: Uuid
    revision: PositiveInt
    case_count: NonNegativeInt
    name: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    description: Annotated[StrictStr, Field(max_length=4000)] = ""
    tags: list[StrictStr] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    is_deleted: StrictBool = False


class RunLimits(JsonModel):
    """契约 §4：run 覆盖只能收紧用例限制，因此范围不比用例更宽。"""

    concurrency: Annotated[int, Field(strict=True, ge=1, le=3)] = 1
    max_iterations: Annotated[int, Field(strict=True, ge=1, le=50)] = 12
    timeout_s: Annotated[int, Field(strict=True, ge=5, le=900)] = 120


class RunCreate(JsonModel):
    suite_id: Uuid
    case_ids: Annotated[list[Uuid], Field(min_length=1, max_length=200)]
    agent_profile_id: Identifier
    judge_profile_id: Identifier | None = None
    limits: RunLimits = Field(default_factory=RunLimits)

    @model_validator(mode="after")
    def _case_ids_unique(self) -> "RunCreate":
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("DUPLICATE_CASE_IDS")
        return self


# =========================================================================== 运行产物

ExecutionStatus = Literal[
    "queued", "running", "succeeded", "failed", "timed_out", "cancelled",
    "interrupted", "setup_error",
]
GradingStatus = Literal[
    "pending", "running", "completed", "judge_error", "not_applicable",
]
Verdict = Literal["passed", "failed", "review_required", "not_evaluated"]
RunStatus = Literal[
    "queued", "running", "cancelling", "completed", "cancelled", "interrupted",
]


class AttemptError(JsonModel):
    """契约 §4：按发生顺序追加，不允许后发生的错误覆盖先发生的错误。"""

    phase: Literal["setup", "execution", "trace", "grading", "cleanup"]
    code: StrictStr
    message: StrictStr


class AssertionResult(JsonModel):
    id: Identifier
    passed: StrictBool | None
    required: StrictBool
    message: StrictStr = ""
    evidence_refs: list[StrictStr] = Field(default_factory=list)


class UsageAggregate(JsonModel):
    """契约 §4：未知量不补零，用 source/coverage 显式区分"实报"与"估算"。"""

    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    total_tokens: NonNegativeInt | None = None
    source: Literal["reported", "estimated", "mixed", "unavailable"]
    coverage: Literal["complete", "partial", "unavailable"]
    physical_requests: NonNegativeInt
    iterations: NonNegativeInt
    missing_usage_calls: NonNegativeInt


class AttemptTiming(JsonModel):
    queue_ms: NonNegativeInt | None = None
    agent_ms: NonNegativeInt | None = None
    grading_ms: NonNegativeInt | None = None
    total_ms: NonNegativeInt | None = None


class JudgeDimensionResult(JsonModel):
    id: Identifier
    score: Annotated[int, Field(strict=True, ge=0, le=4)]
    reason: StrictStr
    evidence_refs: list[StrictStr] = Field(default_factory=list)


class JudgeResult(JsonModel):
    """契约 §4/§5：``calibrated`` 为 false 时，评分只能进入 review_required。"""

    status: StrictStr
    model: StrictStr
    version: StrictStr
    dimensions: list[JudgeDimensionResult] = Field(default_factory=list)
    calibrated: StrictBool
    error: StrictStr | None = None


class ArtifactRef(JsonModel):
    id: Uuid
    name: StrictStr
    media_type: StrictStr
    size_bytes: NonNegativeInt
    sha256: Annotated[StrictStr, Field(min_length=64, max_length=64)]


class ManualReview(JsonModel):
    id: Uuid
    verdict: Literal["passed", "failed"]
    comment: StrictStr
    reviewer: StrictStr
    created_at: datetime


# =========================================================================== Trace 与 Worker IPC


class TraceEvent(JsonModel):
    """契约 §5：seq 由服务端单调分配；type 是闭集，拼错即拒。"""

    schema_version: Literal[1] = 1
    attempt_id: Uuid
    seq: PositiveInt
    event_id: Uuid
    type: Literal[
        "attempt.started", "turn.started", "iteration.started",
        "model.request_summary", "model.call_completed", "tool.started",
        "tool.completed", "tool.failed", "iteration.completed",
        "turn.completed", "attempt.completed", "attempt.failed",
        "trace.incomplete",
    ]
    turn: NonNegativeInt | None = None
    iteration: NonNegativeInt | None = None
    call_id: StrictStr | None = None
    ts: StrictStr
    elapsed_ms: NonNegativeInt
    payload: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ts_is_iso8601(self) -> "TraceEvent":
        _parse_iso8601(self.ts)
        return self


class _BootBase(JsonModel):
    type: Literal["boot"]
    protocol_version: Literal[1]
    attempt_id: Uuid
    runtime_root: StrictStr
    workspace: StrictStr
    #: 只存在于内存与私有管道，不写原始 boot（契约 §5）。
    profile_bundle: JsonObject

    @model_validator(mode="after")
    def _paths_are_usable(self) -> "_BootBase":
        # 空白路径会一路传到 resolve_inside 再炸在更远的地方，这里提前挡住。
        _non_blank(self.workspace)
        _non_blank(self.runtime_root)
        return self


class AgentWorkerBoot(_BootBase):
    role: Literal["agent"]
    case_snapshot: CaseWrite


class JudgeWorkerBoot(_BootBase):
    role: Literal["judge"]
    rubric: Rubric
    evidence: JsonObject


WorkerBoot = Annotated[
    Union[AgentWorkerBoot, JudgeWorkerBoot], Field(discriminator="role")
]


class _ResultBase(JsonModel):
    type: Literal["result"] = "result"
    attempt_id: Uuid
    execution_status: ExecutionStatus
    usage: UsageAggregate
    timing: AttemptTiming
    errors: list[AttemptError] = Field(default_factory=list)
    trace_complete: StrictBool


class AgentWorkerResult(_ResultBase):
    final_answer: StrictStr | None = None
    stop_reason: StrictStr | None = None


class JudgeWorkerResult(_ResultBase):
    """契约 §5：评分结果不能覆盖 Agent 的 execution_status，因此不携带 final_answer。"""

    final_answer: None = None
    stop_reason: None = None
    judge: JudgeResult | None = None


WorkerResult = Union[AgentWorkerResult, JudgeWorkerResult]


def _parse_iso8601(value: str) -> None:
    """RFC3339 解析：契约 §1 规定时间是 UTC RFC3339，宽松日期串不接受。"""
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("INVALID_TIMESTAMP") from exc


def _check_fixtures(fixtures: list[Fixture]) -> None:
    total = 0
    for fixture in fixtures:
        validate_relative_path(fixture.path)
        if "\x00" in fixture.content or _has_forbidden_control(fixture.content):
            raise ValueError("FIXTURE_NOT_TEXT")
        total += len(fixture.content.encode("utf-8"))
    if total > 2 * Mib:
        raise ValueError("FIXTURES_TOO_LARGE")


def _has_forbidden_control(content: str) -> bool:
    """只放行制表、换行、回车；其余 C0 控制字符都视为二进制内容。"""
    return any(
        ord(ch) < 0x20 and ch not in "\t\n\r" for ch in content
    )


def _check_memory_seed(seeds: list[MemorySeed]) -> None:
    if sum(len(seed.content) for seed in seeds) > 64000:
        raise ValueError("MEMORY_SEED_TOO_LARGE")


def _check_tool_fixtures(fixtures: list[ToolFixture]) -> None:
    total = 0
    for fixture in fixtures:
        total += len(_canonical_json(fixture.result).encode("utf-8"))
    if total > Mib:
        raise ValueError("TOOL_FIXTURES_TOO_LARGE")


def _canonical_json(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
