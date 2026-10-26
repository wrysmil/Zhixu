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
    warnings.filterwarnings("ignore", message='.*shadows an attribute in parent.*')

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

    class AnswerJsonBlockAssertion(_AssertionBase):
        """答案里恰好一个 JSON 围栏块，并按 schema 校验。"""

        kind: Literal["answer_json_block"]
        schema: SchemaSubset  # pyright: ignore[reportIncompatibleMethodOverride]
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


class AnswerExactAssertion(_AssertionBase):
    """答案精确匹配；去掉首尾空白后再比，case_sensitive 决定是否区分大小写。"""

    kind: Literal["answer_exact"]
    text: Annotated[StrictStr, Field(max_length=32000)]
    case_sensitive: StrictBool = True


class AnswerLengthAssertion(_AssertionBase):
    """答案长度上限；长度按 Unicode 码点计，含标点与空白。"""

    kind: Literal["answer_length"]
    max_chars: PositiveInt
    min_chars: NonNegativeInt = 0


class TrajectoryStep(JsonModel):
    """轨迹的一步：工具名 + 参数子集。"""

    name: Identifier
    arguments_subset: JsonObject = Field(default_factory=dict)


class ToolTrajectoryAssertion(_AssertionBase):
    """工具轨迹匹配；按 call_id 一对一，不让一次调用满足多步。"""

    kind: Literal["tool_trajectory"]
    steps: Annotated[list[TrajectoryStep], Field(min_length=1, max_length=20)]
    mode: Literal["exact", "in_order", "any_order"] = "in_order"


class TeamMemberExecutedAssertion(_AssertionBase):
    """成员真实执行：读 member 终态事件，不靠最终文本自报。"""

    kind: Literal["team_member_executed"]
    member_ids: Annotated[list[Identifier], Field(min_length=1, max_length=20)]


class UsageLimitAssertion(_AssertionBase):
    """Token 预算；缺 usage 时判不可评，不补 0。"""

    kind: Literal["usage_limit"]
    max_total_tokens: NonNegativeInt | None = None
    max_output_tokens: NonNegativeInt | None = None
    max_tool_calls: NonNegativeInt | None = None


class DurationLimitAssertion(_AssertionBase):
    """耗时预算（毫秒）；缺计时时判不可评。"""

    kind: Literal["duration_limit"]
    max_duration_ms: NonNegativeInt


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
        AnswerExactAssertion,
        AnswerLengthAssertion,
        AnswerJsonBlockAssertion,
        ToolTrajectoryAssertion,
        TeamMemberExecutedAssertion,
        UsageLimitAssertion,
        DurationLimitAssertion,
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


class ScriptToolCall(JsonModel):
    name: Identifier
    arguments: JsonObject = Field(default_factory=dict)


class ScriptStep(JsonModel):
    """离线脚本的一步：文本回复与/或工具调用。

    只在 ``execution.tool_mode == "fixture"`` 时被受控 provider 消费；live 模式下
    字段可以留着但不会生效。这是为了让判分链路能离线端到端跑通（spec §验证：
    离线用受控 provider，与真实 provider 冒烟分开表述）。
    """

    text: StrictStr = ""
    json_text: StrictStr | None = None
    tool_calls: Annotated[list[ScriptToolCall], Field(max_length=10)] = Field(
        default_factory=list
    )


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
#: live 模式下允许选择的真实工具（全部仍在 attempt workspace 沙箱内执行：
#: restrict_to_workspace + 空白 overlay，exec 仅在显式选择时打开）。
LIVE_ONLY_TOOLS = frozenset({
    "web_search", "web_fetch", "read_file", "write_file", "edit_file",
    "list_dir", "grep", "find_files",
})
#: live 模式同样不允许的越权工具：原生 shell 与补丁执行。
#: 它们不在受控白名单里；放进 LIVE_ONLY_TOOLS 就等于允许用例在入队时申请原生
#: shell，而运行期沙箱只能事后报错。要求「入队即拒绝」，所以这里直接不提供。
FORBIDDEN_LIVE_TOOLS = frozenset({"exec", "exec_session", "apply_patch"})


class EvaluationBudgets(JsonModel):
    """契约 §3：效率维度预算。缺项表示该维度不按预算判定。"""

    max_input_tokens: NonNegativeInt | None = None
    max_output_tokens: NonNegativeInt | None = None
    max_total_tokens: NonNegativeInt | None = None
    max_duration_ms: NonNegativeInt | None = None
    max_tool_calls: NonNegativeInt | None = None
    max_iterations: PositiveInt | None = None


class Milestone(JsonModel):
    """契约 §3：任务里程碑。引用真实 assertion id，权重必须为正。"""

    id: Identifier
    weight: Annotated[float, Field(gt=0, le=1)] = 1.0
    assertion_ids: Annotated[
        list[Identifier], Field(min_length=1, max_length=20)
    ] = Field(default_factory=list)

    @model_validator(mode="after")
    def _weight_positive(self) -> "Milestone":
        if not self.assertion_ids:
            raise ValueError("MILESTONE_WITHOUT_ASSERTION")
        return self


class CaseEvaluation(JsonModel):
    """契约 §3：用例级任务判据。

    没有它时任务维度只能算 smoke（只能显示规则通过率），不允许输出综合质量结论。
    """

    version: Literal[2] = 2
    required_dimensions: list[
        Literal["task", "constraints", "trajectory", "efficiency", "safety"]
    ] = Field(default_factory=list)
    milestones: list[Milestone] = Field(default_factory=list)
    required_evidence: list[
        Literal["final_answer", "turns", "artifacts", "trace", "usage", "delegation"]
    ] = Field(default_factory=list)
    reference_answer: StrictStr | None = None
    judge_artifact_paths: list[StrictStr] = Field(default_factory=list)
    budgets: EvaluationBudgets = Field(default_factory=EvaluationBudgets)

    @model_validator(mode="after")
    def _milestones_need_weight(self) -> "CaseEvaluation":
        if any(item.weight <= 0 for item in self.milestones):
            raise ValueError("MILESTONE_WEIGHT_INVALID")
        return self


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
    #: 离线脚本（仅 tool_mode=fixture 生效）；live 模式忽略。
    script: Annotated[list[ScriptStep], Field(max_length=20)] = Field(
        default_factory=list
    )
    artifact_paths: Annotated[list[StrictStr], Field(max_length=50)] = Field(
        default_factory=list
    )
    #: 契约 §3：任务判据（里程碑、必需证据、预算）。缺省表示沿用旧口径。
    evaluation: CaseEvaluation | None = None
    #: 契约 §3：本用例必须存在的输入素材（相对路径）。
    #: 必须由 fixtures 提供——缺依赖要明示，不能用空文件假装任务成功。
    dependencies: Annotated[list[StrictStr], Field(max_length=50)] = Field(
        default_factory=list
    )
    #: 契约 §3：运行时必须存在、但**故意不随用例提供**的输入（相对路径）。
    #: 用于「缺输入」边界用例：入库合法，执行 setup 时必须明确报缺，而不是
    #: 用空文件假装任务成功。
    required_inputs: Annotated[list[StrictStr], Field(max_length=50)] = Field(
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
        for raw in self.required_inputs:
            validate_relative_path(raw)
        self._check_dependencies()
        for assertion in self.assertions:
            _assertion_path_check(assertion)
        if not self._has_required_rule():
            raise ValueError("REQUIRED_RULE_OR_RUBRIC")
        if self.evaluation is not None and "task" in self.evaluation.required_dimensions:
            has_milestone = bool(self.evaluation.milestones)
            has_judge = self.rubric is not None and self.rubric.required
            if not has_milestone and not has_judge:
                raise ValueError("TASK_CRITERIA_MISSING")
        return self

    def _check_dependencies(self) -> None:
        """声明了输入素材就必须真的有：缺依赖在入库时明示，不留到运行期。

        素材只能由 fixtures 提供；Skill 目标冻结的技能文件属于被测对象自带的
        依赖，由目标快照负责，不在这里校验。
        """
        provided = {item.path for item in self.fixtures}
        for raw in self.dependencies:
            validate_relative_path(raw)
            if raw not in provided:
                raise ValueError(f"CASE_DEPENDENCY_MISSING:{raw}")

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


class TargetRef(JsonModel):
    """运行目标：Agent 档案 / 团队 / Skill / Excel 数据集（spec §目标体系）。

    只在入队时给出引用；真正的冻结（Agent 档案与 Skill 文件落快照）由服务层在
    ``create_run`` 时完成，快照冻结后运行与源文件的后续改动无关。
    """

    kind: Literal["agent", "team", "skill", "dataset"]
    id: Annotated[StrictStr, Field(min_length=1, max_length=120)]


class RunCreate(JsonModel):
    suite_id: Uuid
    case_ids: Annotated[list[Uuid], Field(min_length=1, max_length=200)]
    agent_profile_id: Identifier
    judge_profile_id: Identifier | None = None
    limits: RunLimits = Field(default_factory=RunLimits)
    target: TargetRef | None = None
    #: 整个 run 的总时限（秒）；None = 不限时。到点由调度器批量停止（WU-03），
    #: 与用例级 ``execution.timeout_s``（单 attempt）相互独立。
    run_timeout_s: Annotated[int | None, Field(strict=True, ge=5, le=86400)] = None
    repetitions: Annotated[int, Field(strict=True, ge=1, le=10)] = 1
    model_mode: Literal["real", "scripted"] = "real"

    @model_validator(mode="after")
    def _case_ids_unique(self) -> "RunCreate":
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("DUPLICATE_CASE_IDS")
        if len(self.case_ids) * self.repetitions > 200:
            raise ValueError("ATTEMPT_BUDGET_EXCEEDED")
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
    "timed_out",
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


# ====================================================================== 读出模型
# 契约 §3/§4 的响应形状。仓储内部仍返回 dict，读出模型只在这一层做类型化序列化：
# 边界一次收口，handler 不再自己拼字段。


class RunSummary(JsonModel):
    id: Uuid
    suite_id: Uuid
    suite_name: StrictStr
    status: RunStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    parent_run_id: Uuid | None = None
    planned_count: NonNegativeInt
    # 契约 §4：只数已经执行且评分终结的项；Agent finished 而 Judge pending 不算。
    terminal_count: NonNegativeInt


class RunSnapshot(JsonModel):
    """契约 §4：冻结内容只含可公开信息，秘密不进快照。"""

    suite_id: Uuid
    suite_revision: PositiveInt
    case_revisions: dict[str, PositiveInt]
    agent_profile_id: Identifier
    judge_profile_id: Identifier | None = None
    provider: StrictStr
    model: StrictStr
    generation: JsonObject = Field(default_factory=dict)
    limits: RunLimits
    rules_version: StrictStr
    grader_version: StrictStr
    tool_mode: StrictStr
    #: 契约 §1：v2 运行清单。旧记录没有该字段时按 legacy 读，不回填猜测值。
    manifest: JsonObject = Field(default_factory=dict)
    repetitions: Annotated[int, Field(strict=True, ge=1, le=10)] = 1
    model_mode: Literal["real", "scripted"] = "real"
    tool_modes: list[StrictStr] = Field(default_factory=list)


class RunCounts(JsonModel):
    """契约 §4 的 verdict 计数；未终结的项不计入任何一个终态桶。"""

    pending: NonNegativeInt
    passed: NonNegativeInt
    failed: NonNegativeInt
    review_required: NonNegativeInt
    not_evaluated: NonNegativeInt


class QualityEntry(JsonModel):
    dimension_id: Identifier
    mean: float | None = None
    valid_count: NonNegativeInt
    eligible_count: NonNegativeInt


class DurationStats(JsonModel):
    median_ms: float | None = None
    p95_ms: float | None = None
    count: NonNegativeInt


class UsageLedgerView(JsonModel):
    agent: UsageAggregate
    judge: UsageAggregate


class RunMetrics(JsonModel):
    planned: NonNegativeInt
    passed: NonNegativeInt
    failed: NonNegativeInt
    review_required: NonNegativeInt
    not_evaluated: NonNegativeInt
    overall_pass_rate: float | None = None
    evaluated_pass_rate: float | None = None
    evaluation_coverage: float | None = None
    tool_case_pass_rate: float | None = None
    tool_eligible_cases: NonNegativeInt
    tool_valid_cases: NonNegativeInt
    quality: list[QualityEntry] = Field(default_factory=list)
    agent_duration: DurationStats
    usage: UsageLedgerView
    #: v2 指标（契约 §5）：任务完成度、合格策略与重复可靠性。
    task_outcomes: JsonObject = Field(default_factory=dict)
    qualification: JsonObject = Field(default_factory=dict)
    reliability: JsonObject = Field(default_factory=dict)


class RunRead(RunSummary):
    """契约 §4：run 详情 = 摘要 + 冻结快照 + 计数 + 指标 + attempts 链接。"""

    snapshot: RunSnapshot
    counts: RunCounts
    metrics: RunMetrics
    attempts_url: StrictStr
    #: 契约 §1：评估能力等级。smoke = 只能显示规则通过率，不能当综合质量门禁。
    evaluation_capability: Literal["full", "smoke", "legacy"] = "legacy"
    #: 运行级证据覆盖度汇总。
    coverage: JsonObject = Field(default_factory=dict)


class EvaluationCoverage(JsonModel):
    """契约 §2：证据覆盖度。

    缺证必须显式为 missing/partial 并给出原因，不允许补 0 假装已知。
    """

    trace: Literal["complete", "partial", "missing"] = "missing"
    identity: Literal["complete", "partial", "missing"] = "missing"
    usage: Literal["complete", "partial", "missing"] = "missing"
    artifacts: Literal["complete", "partial", "missing", "not_applicable"] = "missing"
    #: 用例要求的证据是否齐备。
    required_evidence: Literal["complete", "partial"] = "complete"
    reasons: list[StrictStr] = Field(default_factory=list)


class AttemptRead(JsonModel):
    """契约 §4 的尝试详情；judge 为 null 表示未评分，calibrated=false 只能进复核。"""

    id: Uuid
    run_id: Uuid
    position: PositiveInt
    case_id: Uuid
    case_revision: PositiveInt
    case_name: StrictStr
    execution_status: ExecutionStatus
    grading_status: GradingStatus
    verdict: Verdict
    started_at: datetime | None = None
    finished_at: datetime | None = None
    final_answer: StrictStr | None = None
    stop_reason: StrictStr | None = None
    errors: list[AttemptError] = Field(default_factory=list)
    assertions: list[AssertionResult] = Field(default_factory=list)
    judge: JudgeResult | None = None
    usage: UsageLedgerView
    timing: AttemptTiming
    trace_complete: StrictBool
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    reviews: list[ManualReview] = Field(default_factory=list)
    trial_index: Annotated[int, Field(strict=True, ge=0)] = 0
    source_attempt_id: Uuid | None = None
    coverage: EvaluationCoverage | None = None
    #: 稳定证据引用（如最终回答 final:<attempt_id>:<seq>）。
    evidence_refs: list[StrictStr] = Field(default_factory=list)


class TracePage(JsonModel):
    """契约 §3：``after_seq`` 严格递增，``sealed`` 为 false 时前端不得视为最终 Trace。"""

    data: list[TraceEvent]
    next_after_seq: NonNegativeInt
    has_more: StrictBool
    sealed: StrictBool


class GoldScore(JsonModel):
    dimension_id: Identifier
    score: Annotated[int, Field(strict=True, ge=0, le=4)]


class GoldSample(JsonModel):
    """契约 §3.1：``label_status`` 只有用户确认才写 confirmed，AI 建议一律 proposed。"""

    id: Identifier
    task: StrictStr
    rubric: Rubric
    evidence: JsonObject
    expected_verdict: Literal["passed", "failed"]
    expected_scores: list[GoldScore] = Field(default_factory=list)
    label_status: Literal["proposed", "confirmed"]
    reviewer: StrictStr | None = None
    comment: StrictStr = ""
    updated_at: datetime


class GoldSamplePage(JsonModel):
    gold_revision: PositiveInt
    data: list[GoldSample]


class GoldReview(JsonModel):
    """契约 §3.1：reviewer 由服务端本地身份填充，客户端不可指定。

    ``confirm`` 是字面量 true：契约只定义「确认」这一种写入。传 false 若被当成普通布尔，
    服务端照样会把标签落 confirmed，等于给「AI/客户端替用户确认」留了后门。
    """

    base_gold_revision: PositiveInt
    expected_verdict: Literal["passed", "failed"]
    expected_scores: list[GoldScore]
    comment: StrictStr = ""
    confirm: Literal[True]


class CalibrationCreate(JsonModel):
    judge_profile_id: Identifier
    gold_revision: PositiveInt
    sample_ids: Annotated[list[Identifier], Field(min_length=10, max_length=10)]

    @model_validator(mode="after")
    def _sample_ids_unique(self) -> "CalibrationCreate":
        if len(set(self.sample_ids)) != len(self.sample_ids):
            raise ValueError("DUPLICATE_SAMPLE_IDS")
        return self


class CalibrationRead(JsonModel):
    """契约 §3.1：未完成/失败/取消时 agreement 与 passed 均为 null，不填 0 冒充结果。"""

    id: Uuid
    status: RunStatus
    judge_fingerprint: StrictStr
    gold_revision: PositiveInt
    total: NonNegativeInt
    completed: NonNegativeInt
    matched: NonNegativeInt
    agreement: float | None = None
    passed: StrictBool | None = None
    created_at: datetime
    finished_at: datetime | None = None
    error: StrictStr | None = None


class ProfileRead(JsonModel):
    """契约 §3：只回传可选模型的公开信息，绝不回传原配置或密钥。"""

    id: Identifier
    label: StrictStr
    provider: StrictStr
    model: StrictStr
    kind: Literal["agent", "judge", "both"]
    role: StrictStr | None = None


class Overview(JsonModel):
    """契约 §3：状态计数与指标都派生自持久状态，浏览器不持有调度状态。"""

    recent_runs: list[RunSummary]
    metrics: RunMetrics | None = None
    status_counts: dict[RunStatus, NonNegativeInt]


class SampleImportRequest(JsonModel):
    #: 内置样本 id：basic-v1 是基础回归集，reliability-v2 是 36 条分层样本。
    sample_id: Literal["basic-v1", "reliability-v2"]


class ImportSuiteRequest(JsonModel):
    """契约 §3：导入忽略旧 id 并重建关联，全部合法才提交。"""

    schema_version: Literal[1] = 1
    suite: SuiteWrite
    cases: Annotated[list[CaseWrite], Field(min_length=1, max_length=200)]


class ComparisonCreate(JsonModel):
    """创建一次严格比较（契约 §6）。"""

    baseline_run_id: Uuid
    candidate_run_id: Uuid
    mode: Literal["strict"] = "strict"


class ComparisonRead(JsonModel):
    """比较的读模型；统计覆盖整份比较快照，不是当前页。"""

    id: Uuid
    baseline_run_id: Uuid
    candidate_run_id: Uuid
    mode: StrictStr
    compatibility: Literal["strict", "incompatible"]
    pairs: list[JsonObject] = Field(default_factory=list)
    metrics: JsonObject = Field(default_factory=dict)
    intervals: JsonObject | None = None
    excluded: list[JsonObject] = Field(default_factory=list)
    reasons: list[StrictStr] = Field(default_factory=list)


class RerunCreate(JsonModel):
    """契约 §3：只能重跑同运行的终态尝试，引用原冻结快照。"""

    attempt_ids: Annotated[list[Uuid], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def _attempt_ids_unique(self) -> "RerunCreate":
        if len(set(self.attempt_ids)) != len(self.attempt_ids):
            raise ValueError("DUPLICATE_ATTEMPT_IDS")
        return self


class ManualReviewCreate(JsonModel):
    verdict: Literal["passed", "failed"]
    comment: StrictStr = ""


class EmptyBody(JsonModel):
    """取消类路由的请求体：契约要求 `{}`，不接受额外字段。"""


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
        # 契约 §2：执行身份与被测对象分开记录。
        "agent.started", "agent.completed", "agent.failed",
        "team.dispatch", "member.started", "member.completed",
        "tool.denied",
        "trace.incomplete",
    ]
    turn: NonNegativeInt | None = None
    iteration: NonNegativeInt | None = None
    call_id: StrictStr | None = None
    #: 被测对象（Agent/Skill/团队），与执行身份 agent_id 区分。
    target_kind: StrictStr | None = None
    target_id: StrictStr | None = None
    #: Skill 目标单独记录技能 id，不冒充用户已有 Agent。
    skill_id: StrictStr | None = None
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
    #: 运行目标冻结载荷（Agent 档案 / Skill 文件）；judge boot 不消费。
    target: JsonObject | None = None

    @model_validator(mode="after")
    def _paths_are_usable(self) -> "_BootBase":
        # 空白路径会一路传到 resolve_inside 再炸在更远的地方，这里提前挡住。
        _non_blank(self.workspace)
        _non_blank(self.runtime_root)
        return self


class AgentWorkerBoot(_BootBase):
    role: Literal["agent"]
    case_snapshot: CaseWrite
    #: run 级限额（服务端已与用例取更严值）。为 None 时只用用例级限额。
    limits: RunLimits | None = None


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
    #: 稳定证据引用（如最终回答 final:<attempt_id>:<seq>），供判分侧引用。
    evidence_refs: list[StrictStr] = Field(default_factory=list)


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
