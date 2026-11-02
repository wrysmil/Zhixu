"""记忆专项的严格模型（契约 §1 / §3 / §4 / §8.1–8.3）。

本模块是 WU-01 冻结的**跨 WU 接口基线**：WU-02/03/04/05/06/07/08/09 直接复用这里
的类型与字段名，改字段必须先改契约。

三条硬约束在这里落地：

1. **未知字段一律拒绝**（``extra="forbid"``）：契约 §1 要求"未知字段、重复 ID、
   时间倒退、悬空证据"全部校验拒绝。
2. **标量走 strict**：``revision`` 这类序号不接受 ``True``（bool 伪装 int）。
3. **gold 与执行载荷分离**：``MemoryExecutionInput`` 由 ``build_execution_input``
   生成，只带 scope/events/probes/reference_state/track/候选，**不含 gold、来源许可
   与评分判据**。

本模块刻意不 import ``nanobot.evaluation.schemas``：那边要 import 本模块的
``memory_scenario_ref`` / ``memory_ref``，反向依赖会形成循环。
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

if sys.version_info >= (3, 12):  # pragma: no cover - 解释器版本分支
    from typing import TypeAliasType
else:  # pragma: no cover - 3.11 走 typing_extensions（pydantic 的既有依赖）
    from typing_extensions import TypeAliasType

# ---- 复用标量 ------------------------------------------------------------

#: 契约 §1：scenario_id / probe_id / message_id 一类的短标识（1..120 字符）。
ShortId = Annotated[StrictStr, Field(min_length=1, max_length=120)]
#: 契约 §1：revision / batch_index 一类的正整数序号（strict：拒绝 bool）。
PositiveInt = Annotated[StrictInt, Field(ge=1)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
#: 契约 §1：标签。
Tag = Annotated[StrictStr, Field(min_length=1, max_length=40)]
#: 契约 §1：UUID 字段（服务端生成）。
Uuid = uuid.UUID
#: 契约 §1：sha256 摘要。
Digest = Annotated[StrictStr, Field(min_length=64, max_length=64)]

#: 严格 JSON 值：与旧评估域口径一致，不接受 bytes/set/NaN，也不把 1 收成 True。
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


class JsonModel(BaseModel):
    """记忆专项模型的共同基类：拒绝未知字段，不做静默透传。"""

    model_config = ConfigDict(extra="forbid")


def _require_timezone(value: datetime) -> datetime:
    """契约 §1：场景时间必须带时区（naive 时间会让"跨天"判定失去依据）。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return value


# =========================================================================== 作用域


class MemoryScope(JsonModel):
    """契约 §1：scope 只映射到本 trial 新建的合成作用域，不接受真实目录。"""

    subject_id: ShortId
    workspace_id: ShortId


class MemoryCoverage(JsonModel):
    """契约 §8.4：证据覆盖度——已评分/计划与未知原因。"""

    scored: NonNegativeInt = 0
    planned: NonNegativeInt = 0
    reasons: list[ShortId] = Field(default_factory=list)

    @model_validator(mode="after")
    def _scored_le_planned(self) -> "MemoryCoverage":
        if self.scored > self.planned:
            raise ValueError("COVERAGE_SCORED_EXCEEDS_PLANNED")
        return self

    @property
    def complete(self) -> bool:
        return self.planned > 0 and self.scored == self.planned


class MemoryInterval(JsonModel):
    """契约 §8.5：区间（cluster 数不足时不输出区间，而不是给一个假的窄区间）。"""

    low: StrictFloat
    high: StrictFloat
    method: Annotated[StrictStr, Field(min_length=1, max_length=80)] = (
        "paired-cluster-percentile/v1"
    )


# =========================================================================== 事实


class MemoryFact(JsonModel):
    """契约 §1/§8.2：原子事实。

    ``evidence_message_ids`` 指向场景里的真实消息；悬空引用在
    :class:`MemoryScenario` 的跨字段校验里被拒。
    """

    key: ShortId
    value: Annotated[StrictStr, Field(min_length=1, max_length=4000)]
    subject: StrictStr | None = None
    scope: MemoryScope | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    evidence_message_ids: list[ShortId] = Field(default_factory=list)

    @field_validator("valid_from", "valid_to")
    @classmethod
    def _fact_times_are_aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_timezone(value)


class MemoryStateExpectation(JsonModel):
    """契约 §1/§8.3：状态层期望（存储/索引/派生/重建面是否应残留该事实）。"""

    fact_key: ShortId
    present: StrictBool
    surfaces: list[Literal["store", "index", "derived", "rebuild"]] = Field(
        default_factory=list
    )
    detail: StrictStr = ""


# =========================================================================== gold


class MemoryProbeGold(JsonModel):
    """契约 §1：逐 probe 的标准结果。"""

    required_facts: list[MemoryFact] = Field(default_factory=list)
    forbidden_current_values: list[MemoryFact] = Field(default_factory=list)
    answerability: Literal["answerable", "unanswerable"] = "answerable"
    expected_absence: StrictBool = False
    allowed_evidence_sets: list[list[MemoryFact]] = Field(default_factory=list)


#: gold 顶层保留键：其余键都是 probe id。
_GOLD_RESERVED_KEYS = ("annotation_scope", "extraction_facts", "state_expectations")


class MemoryGold(JsonModel):
    """契约 §1/§8.2：私有 gold。

    载荷形状是 ``{probe_id: {...}}``；``annotation_scope`` / ``extraction_facts`` /
    ``state_expectations`` 是顶层保留键。``annotation_scope`` **缺省 targeted**——
    旧记录不能被当成完整抽取标注。
    """

    annotation_scope: Literal["closed_world", "targeted"] = "targeted"
    extraction_facts: list[MemoryFact] = Field(default_factory=list)
    state_expectations: list[MemoryStateExpectation] = Field(default_factory=list)
    probes: dict[str, MemoryProbeGold] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _split_probe_entries(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        # 已经是内部形状（``probes`` + 保留键）就原样放行：仓储读回的 gold_json
        # 走的就是这条路。
        if "probes" in value and all(
            key in _GOLD_RESERVED_KEYS or key == "probes" for key in value
        ):
            return value
        reserved = {key: value[key] for key in _GOLD_RESERVED_KEYS if key in value}
        probes = {key: item for key, item in value.items() if key not in _GOLD_RESERVED_KEYS}
        if probes and not all(isinstance(item, dict) for item in probes.values()):
            raise ValueError("GOLD_PROBE_ENTRY_INVALID")
        return {**reserved, "probes": probes}

    @model_validator(mode="after")
    def _closed_world_needs_target_set(self) -> "MemoryGold":
        """契约 §8.2：closed_world 必须给出抽取目标全集，否则不能算全量 P/R/F1。"""
        if self.annotation_scope == "closed_world" and not self.extraction_facts:
            raise ValueError("CLOSED_WORLD_WITHOUT_FACTS")
        return self

    def all_facts(self) -> list[MemoryFact]:
        """本 gold 涉及的全部事实（供悬空证据校验遍历）。"""
        facts: list[MemoryFact] = list(self.extraction_facts)
        for probe_gold in self.probes.values():
            facts.extend(probe_gold.required_facts)
            facts.extend(probe_gold.forbidden_current_values)
            for evidence_set in probe_gold.allowed_evidence_sets:
                facts.extend(evidence_set)
        return facts


# =========================================================================== 事件


class MemoryMessage(JsonModel):
    """契约 §1：会话消息；system/tool 角色不允许用户注入。"""

    id: ShortId
    role: Literal["user", "assistant"]
    content: Annotated[StrictStr, Field(min_length=1, max_length=32000)]


class MemorySessionEvent(JsonModel):
    """契约 §1：一次真实会话边界（带时区的发生时间）。"""

    type: Literal["session"]
    id: ShortId
    at: datetime
    messages: Annotated[list[MemoryMessage], Field(min_length=1, max_length=100000)]

    @field_validator("at")
    @classmethod
    def _session_time_is_aware(cls, value: datetime) -> datetime:
        return _require_timezone(value)


class MemorySettleEvent(JsonModel):
    """契约 §1：等待记忆处理完成（不是删除会话触发抽取）。"""

    type: Literal["settle"]
    after_session: ShortId
    timeout_s: Annotated[StrictInt, Field(ge=1, le=900)] = 60


class MemoryCheckpointEvent(JsonModel):
    """契约 §1：产出一个可回放的检查点。"""

    type: Literal["checkpoint"]
    id: ShortId


class MemoryRestoreEvent(JsonModel):
    """契约 §1：从检查点恢复。"""

    type: Literal["restore"]
    checkpoint_id: ShortId


class MemoryRestartEvent(JsonModel):
    """契约 §1：关闭并重建 runtime（必须从持久化文件恢复）。"""

    type: Literal["restart"]
    checkpoint_id: ShortId


class MemoryForgetEvent(JsonModel):
    """契约 §1：删除事实；两种模式分开上报。"""

    type: Literal["forget"]
    mode: Literal["product_request", "admin_delete"]
    fact_key: ShortId
    target_id: ShortId | None = None


class MemoryRebuildEvent(JsonModel):
    """契约 §1：重建派生状态（后端不支持时上报 capability_missing）。"""

    type: Literal["rebuild"]


MemoryEvent = Annotated[
    Union[
        MemorySessionEvent,
        MemorySettleEvent,
        MemoryCheckpointEvent,
        MemoryRestoreEvent,
        MemoryRestartEvent,
        MemoryForgetEvent,
        MemoryRebuildEvent,
    ],
    Field(discriminator="type"),
]


# =========================================================================== 探针


class MemoryProbe(JsonModel):
    """契约 §1：探针；默认落在最后一个已 settle 的状态上。"""

    id: ShortId
    question: Annotated[StrictStr, Field(min_length=1, max_length=4000)]
    at: datetime
    fresh_session: StrictBool = True
    checkpoint_id: ShortId | None = None
    scope: MemoryScope | None = None

    @field_validator("at")
    @classmethod
    def _probe_time_is_aware(cls, value: datetime) -> datetime:
        return _require_timezone(value)


# =========================================================================== 场景


class MemoryReferenceFact(JsonModel):
    """契约 §1：检索轨道的标准状态事实（带来源）。"""

    key: ShortId
    value: Annotated[StrictStr, Field(min_length=1, max_length=4000)]
    subject: StrictStr | None = None
    scope: MemoryScope | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    origin_refs: list[ShortId] = Field(default_factory=list)


class MemoryReferenceState(JsonModel):
    """契约 §1：检索轨道的标准状态（事实/episode/画像/工作记忆及其版本）。"""

    version: Annotated[StrictStr, Field(min_length=1, max_length=80)]
    facts: list[MemoryReferenceFact] = Field(default_factory=list)
    episodes: list[ShortId] = Field(default_factory=list)
    profile: JsonObject = Field(default_factory=dict)
    working_memory: JsonObject = Field(default_factory=dict)


class MemoryScenario(JsonModel):
    """契约 §1：记忆专项场景（``memory-scenario/v1``）。"""

    schema_version: Literal["memory-scenario/v1"] = "memory-scenario/v1"
    scenario_id: ShortId
    revision: PositiveInt
    split: Literal["development", "calibration", "regression"]
    template_family: ShortId
    cluster_id: ShortId
    tags: list[Tag] = Field(default_factory=list, max_length=20)
    scope: MemoryScope
    events: list[MemoryEvent] = Field(default_factory=list)
    probes: list[MemoryProbe] = Field(default_factory=list)
    gold: MemoryGold = Field(default_factory=MemoryGold)
    reference_state: MemoryReferenceState | None = None

    @model_validator(mode="before")
    @classmethod
    def _cluster_defaults_to_scenario(cls, value: Any) -> Any:
        """契约 §1：cluster_id 缺省取 scenario_id（标识原始 conversation）。"""
        if (
            isinstance(value, dict)
            and "cluster_id" not in value
            and isinstance(value.get("scenario_id"), str)
        ):
            return {**value, "cluster_id": value["scenario_id"]}
        return value

    @model_validator(mode="after")
    def _validate_invariants(self) -> "MemoryScenario":
        self._check_unique_ids()
        self._check_settle_targets()
        self._check_time_order()
        self._check_gold_targets()
        return self

    def _check_unique_ids(self) -> None:
        probe_ids = [probe.id for probe in self.probes]
        if len(set(probe_ids)) != len(probe_ids):
            raise ValueError("DUPLICATE_PROBE_ID")
        message_ids: list[str] = []
        session_ids: list[str] = []
        for event in self.events:
            if not isinstance(event, MemorySessionEvent):
                continue
            session_ids.append(event.id)
            message_ids.extend(message.id for message in event.messages)
        if len(set(message_ids)) != len(message_ids):
            raise ValueError("DUPLICATE_MESSAGE_ID")
        if len(set(session_ids)) != len(session_ids):
            raise ValueError("DUPLICATE_SESSION_ID")

    def _check_settle_targets(self) -> None:
        """契约 §1：settle.after_session 必须引用先前的 session。"""
        seen: set[str] = set()
        for event in self.events:
            if isinstance(event, MemorySessionEvent):
                seen.add(event.id)
            elif isinstance(event, MemorySettleEvent):
                if event.after_session not in seen:
                    raise ValueError("SETTLE_WITHOUT_SESSION")

    def _check_time_order(self) -> None:
        """事件发生时间不得倒退。

        只看 session 事件的发生时间：有效时间（valid_from/valid_to）与事件顺序无关，
        契约 §1 明确不能用有效时间变化误判事件乱序。
        """
        previous: datetime | None = None
        for event in self.events:
            if not isinstance(event, MemorySessionEvent):
                continue
            if previous is not None and event.at < previous:
                raise ValueError("TIME_REVERSED")
            previous = event.at

    def _check_gold_targets(self) -> None:
        probe_ids = {probe.id for probe in self.probes}
        for probe_id in self.gold.probes:
            if probe_id not in probe_ids:
                raise ValueError("GOLD_WITHOUT_PROBE")
        known: set[str] = set()
        for event in self.events:
            if isinstance(event, MemorySessionEvent):
                known.update(message.id for message in event.messages)
        for fact in self.gold.all_facts():
            for message_id in fact.evidence_message_ids:
                if message_id not in known:
                    raise ValueError("DANGLING_EVIDENCE")


# =========================================================================== 候选与能力


class MemorySettings(JsonModel):
    """契约 §2：候选的记忆设置白名单（只放能落到真实配置的字段）。"""

    extraction_enabled: StrictBool = True
    extraction_strategy_version: StrictStr | None = None
    merge_strategy_version: StrictStr | None = None
    retrieval_channels: list[ShortId] = Field(default_factory=list)
    retrieval_weights: dict[str, StrictFloat] = Field(default_factory=dict)
    retrieval_threshold: StrictFloat | None = None
    recall_limit: Annotated[StrictInt, Field(ge=1, le=1000)] | None = None
    injection_budget_tokens: Annotated[StrictInt, Field(ge=0)] | None = None
    embedding_model: StrictStr | None = None
    index_version: StrictStr | None = None


class MemoryCandidate(JsonModel):
    """契约 §2：候选（不提供任意类名/模块/路径/shell 参数）。"""

    id: ShortId
    name: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    mode: Literal["zhixu", "no_persistent_memory"]
    agent_profile_id: ShortId
    extractor_profile_id: ShortId | None = None
    memory_settings: MemorySettings = Field(default_factory=MemorySettings)


class MemoryCapability(JsonModel):
    """契约 §3：单项能力声明（unsupported 与"试验失败"是两回事）。"""

    status: Literal["supported", "unsupported", "partial"] = "unsupported"
    detail: StrictStr = ""


class MemoryCapabilities(JsonModel):
    """契约 §3：逐项声明后端能力。"""

    extraction: MemoryCapability = Field(default_factory=MemoryCapability)
    semantic_recall: MemoryCapability = Field(default_factory=MemoryCapability)
    episodes: MemoryCapability = Field(default_factory=MemoryCapability)
    working_memory: MemoryCapability = Field(default_factory=MemoryCapability)
    profile: MemoryCapability = Field(default_factory=MemoryCapability)
    delete: MemoryCapability = Field(default_factory=MemoryCapability)
    rebuild: MemoryCapability = Field(default_factory=MemoryCapability)
    restart: MemoryCapability = Field(default_factory=MemoryCapability)
    clock_coverage: MemoryCapability = Field(default_factory=MemoryCapability)
    scope_coverage: MemoryCapability = Field(default_factory=MemoryCapability)


# =========================================================================== 执行载荷


class MemoryExecutionInput(JsonModel):
    """契约 §1：执行端载荷——**不含 gold、来源许可与评分判据**。"""

    schema_version: Literal["memory-scenario/v1"]
    scenario_id: ShortId
    revision: PositiveInt
    scope: MemoryScope
    events: list[MemoryEvent] = Field(default_factory=list)
    probes: list[MemoryProbe] = Field(default_factory=list)
    reference_state: MemoryReferenceState | None = None
    track: Literal["extraction", "retrieval", "end_to_end"]
    candidates: list[MemoryCandidate] = Field(default_factory=list)


def build_execution_input(
    scenario: MemoryScenario,
    *,
    track: str,
    candidates: "list[MemoryCandidate] | tuple[MemoryCandidate, ...]" = (),
) -> MemoryExecutionInput:
    """由场景构造执行载荷：去掉 gold、来源许可与评分判据。

    这是 gold 与执行端之间唯一的构造点：执行载荷里不可能出现 ``gold``、许可证或
    评分判据，因为它们根本不在入参里。
    """
    if track not in ("extraction", "retrieval", "end_to_end"):
        raise ValueError(f"MEMORY_TRACK_UNSUPPORTED:{track}")
    return MemoryExecutionInput(
        schema_version=scenario.schema_version,
        scenario_id=scenario.scenario_id,
        revision=scenario.revision,
        scope=scenario.scope,
        events=scenario.events,
        probes=scenario.probes,
        reference_state=scenario.reference_state,
        track=track,
        candidates=list(candidates),
    )


# =========================================================================== 运行产物


class MemoryArtifactRef(JsonModel):
    """契约 §3：受控产物引用（正文按 artifact 权限读取，不进执行载荷）。"""

    id: ShortId
    name: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    media_type: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    size_bytes: NonNegativeInt
    sha256: Digest


class MemoryStateSnapshot(JsonModel):
    """契约 §3：状态快照（复制 SQLite 走 backup API，不复制活动 WAL）。"""

    version: PositiveInt
    checkpoint_id: ShortId
    state_digest: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    artifact_refs: list[MemoryArtifactRef] = Field(default_factory=list)
    origin_map: dict[str, list[str]] = Field(default_factory=dict)
    storage_coverage: MemoryCoverage = Field(default_factory=MemoryCoverage)


class MemoryRecallItem(JsonModel):
    """契约 §4：一条召回结果（带排序、来源与作用域）。"""

    rank: PositiveInt
    key: ShortId
    value: Annotated[StrictStr, Field(min_length=1, max_length=4000)]
    subject: StrictStr | None = None
    scope: MemoryScope | None = None
    origin_refs: list[ShortId] = Field(default_factory=list)
    score: StrictFloat | None = None


class RecallResult(JsonModel):
    """契约 §3：一次召回的结果（空召回与"没有相关 gold"是两件事）。"""

    probe_id: ShortId
    items: list[MemoryRecallItem] = Field(default_factory=list)
    coverage: MemoryCoverage = Field(default_factory=MemoryCoverage)
    state_version: StrictStr | None = None
    index_version: StrictStr | None = None


class MemoryUsage(JsonModel):
    """契约 §4：分阶段 usage；未知量不补 0。"""

    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    total_tokens: NonNegativeInt | None = None
    physical_requests: NonNegativeInt = 0
    source: Literal["reported", "estimated", "mixed", "unavailable"] = "unavailable"


class MemoryTiming(JsonModel):
    """契约 §4：分阶段时延（毫秒）；未知量不补 0。"""

    write_ms: NonNegativeInt | None = None
    index_ms: NonNegativeInt | None = None
    retrieve_ms: NonNegativeInt | None = None
    inject_ms: NonNegativeInt | None = None
    answer_ms: NonNegativeInt | None = None
    total_ms: NonNegativeInt | None = None


class ProbeExecution(JsonModel):
    """契约 §4：逐 probe 的执行结果（全部 probe 都要留，不只留最后一个）。"""

    probe_id: ShortId
    answer: StrictStr | None = None
    recalled: list[MemoryRecallItem] = Field(default_factory=list)
    injected_evidence: list[ShortId] = Field(default_factory=list)
    context_refs: list[ShortId] = Field(default_factory=list)
    usage: MemoryUsage = Field(default_factory=MemoryUsage)
    timing: MemoryTiming = Field(default_factory=MemoryTiming)
    coverage: MemoryCoverage = Field(default_factory=MemoryCoverage)


class SettleResult(JsonModel):
    """契约 §3：等待记忆处理的结果。

    ``completed`` 只有在所有必需轨道都完成时才成立：异常被产品链路隔离时仍要收集
    失败轨道，不能因为函数返回就判 completed。
    """

    status: Literal["completed", "skipped_by_policy", "failed", "timeout"]
    completed_stages: list[ShortId] = Field(default_factory=list)
    failed_stages: list[ShortId] = Field(default_factory=list)
    reason_codes: list[ShortId] = Field(default_factory=list)
    state_version: StrictStr | None = None
    index_version: StrictStr | None = None


class ForgetRequest(JsonModel):
    """契约 §3：删除请求（按事实 key 或服务端解析出的 id）。"""

    mode: Literal["product_request", "admin_delete"]
    fact_key: ShortId | None = None
    target_id: ShortId | None = None
    scope: MemoryScope | None = None


class ForgetResult(JsonModel):
    """契约 §3：删除结果（不支持时必须显式 unsupported，不能推断成功）。"""

    status: Literal["completed", "skipped_by_policy", "failed", "unsupported"]
    deleted_ids: list[ShortId] = Field(default_factory=list)
    reason_codes: list[ShortId] = Field(default_factory=list)
    state_checked: StrictBool = False


# =========================================================================== 指标


MemoryMetricUnit = Literal["ratio", "count", "ms", "token", "bytes", "currency"]
MemoryMetricStatus = Literal["scored", "not_assessable", "not_applicable"]
MemoryMetricScope = Literal["probe", "scenario", "category", "dataset"]


class MemoryMetricResult(JsonModel):
    """契约 §4/§8.4：指标结果。

    **字段缺失不是 0**：``value`` / ``numerator`` / ``denominator`` 可以为 null，
    由 ``status`` 说明是"不可评"还是"不适用"。语义评分另带 grader 指纹与校准状态。
    """

    id: ShortId
    version: Literal[1] = 1
    grading_revision: PositiveInt
    review_digest: StrictStr | None = None
    unit: MemoryMetricUnit
    value: StrictFloat | None = None
    numerator: StrictFloat | None = None
    denominator: StrictFloat | None = None
    status: MemoryMetricStatus
    scope: MemoryMetricScope
    evidence_refs: list[Annotated[StrictStr, Field(min_length=1, max_length=200)]] = (
        Field(default_factory=list)
    )
    reasons: list[Annotated[StrictStr, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list
    )
    coverage: MemoryCoverage = Field(default_factory=MemoryCoverage)
    interval: MemoryInterval | None = None
    grader_fingerprint: StrictStr | None = None
    calibration_status: StrictStr | None = None

    @model_validator(mode="after")
    def _scored_needs_value(self) -> "MemoryMetricResult":
        if self.status == "scored" and self.value is None:
            raise ValueError("SCORED_WITHOUT_VALUE")
        return self


# =========================================================================== 集合版本


class MemoryDatasetSource(JsonModel):
    """契约 §1：来源与 gold 版本不可变。"""

    repo_url: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    revision: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    sha256: Digest
    license: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    original_ids: list[ShortId] = Field(default_factory=list)
    loader_version: Annotated[StrictStr, Field(min_length=1, max_length=80)]
    grading_protocol: Annotated[StrictStr, Field(min_length=1, max_length=80)]


class MemoryDatasetRevision(JsonModel):
    """契约 §1：数据集修订（ready 修订后再改会产生新 revision）。"""

    id: Uuid
    suite_id: Uuid
    dataset_version: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    status: Literal["staging", "ready", "invalid"]
    split_counts: dict[str, NonNegativeInt] = Field(default_factory=dict)
    source: MemoryDatasetSource
    definition_digest: Digest
    created_at: datetime | None = None
    updated_at: datetime | None = None


# =========================================================================== 策略


class MemoryCalibrationPolicy(JsonModel):
    """契约 §8.5：校准策略。"""

    min_samples: Annotated[StrictInt, Field(ge=1)] = 30
    min_per_class: Annotated[StrictInt, Field(ge=1)] = 5
    min_agreement: Annotated[StrictFloat, Field(ge=0, le=1)] = 0.8
    max_false_pass_rate: Annotated[StrictFloat, Field(ge=0, le=1)] = 0.05


class MemoryGradingPolicy(JsonModel):
    """契约 §8.5：判分策略——算法版本与采用阈值都进 grading_digest。"""

    fact_matching_version: Annotated[StrictStr, Field(min_length=1, max_length=80)]
    metric_aggregation_version: Annotated[StrictStr, Field(min_length=1, max_length=80)]
    bootstrap_version: Annotated[StrictStr, Field(min_length=1, max_length=80)]
    minimum_effect_pp: StrictFloat | None = None
    calibration: MemoryCalibrationPolicy = Field(default_factory=MemoryCalibrationPolicy)


class MemoryAdoptionPolicy(JsonModel):
    """契约 §8.5：采用判定策略（缺配置时只出诊断，不自动采用）。"""

    minimum_effect_pp: StrictFloat | None = None
    max_regression_pp_by_metric: dict[str, StrictFloat] = Field(default_factory=dict)
    max_cost_ratio: StrictFloat | None = None
    max_p95_latency_ratio: StrictFloat | None = None


# =========================================================================== 实验与比较


MemoryTrack = Literal["extraction", "retrieval", "end_to_end"]
MemoryExperimentStatus = Literal[
    "queued", "running", "cancelling", "completed", "cancelled", "interrupted",
    "timed_out",
]
MemoryMeasurementStatus = Literal["complete", "incomplete"]


class MemoryBudgets(JsonModel):
    """契约 §2：实验预算（未知单价不允许自动放行货币预算）。"""

    max_trials: Annotated[StrictInt, Field(ge=1, le=1000000)] | None = None
    max_model_requests: NonNegativeInt | None = None
    max_input_tokens: NonNegativeInt | None = None
    max_output_tokens: NonNegativeInt | None = None
    max_total_cost: StrictFloat | None = None
    per_trial_timeout_s: Annotated[StrictInt, Field(ge=1, le=86400)] | None = None
    group_timeout_s: Annotated[StrictInt, Field(ge=1, le=604800)] | None = None
    concurrency: Annotated[StrictInt, Field(ge=1, le=64)] | None = None


class MemoryExperimentRun(JsonModel):
    """契约 §7：实验的一个子 run（候选 × 批次）。"""

    id: Uuid
    experiment_id: Uuid
    candidate_id: ShortId
    batch_index: NonNegativeInt
    run_id: ShortId
    scenario_ids: list[ShortId] = Field(default_factory=list)
    status: Annotated[StrictStr, Field(min_length=1, max_length=40)] = "queued"
    created_at: datetime | None = None


class MemoryExperiment(JsonModel):
    """契约 §2/§5：实验（冻结内容与计划）。"""

    id: Uuid
    suite_id: Uuid
    dataset_revision_id: Uuid
    split: Literal["development", "calibration", "regression"]
    track: MemoryTrack
    candidates: list[MemoryCandidate] = Field(default_factory=list)
    repetitions: Annotated[StrictInt, Field(ge=1, le=10)] = 1
    model_mode: Literal["real", "scripted"] = "real"
    judge_profile_id: StrictStr | None = None
    budgets: MemoryBudgets = Field(default_factory=MemoryBudgets)
    primary_metric: ShortId
    policy: MemoryGradingPolicy
    manifest: JsonObject = Field(default_factory=dict)
    status: MemoryExperimentStatus = "queued"
    measurement_status: MemoryMeasurementStatus = "incomplete"
    planned_trials: NonNegativeInt = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    child_runs: list[MemoryExperimentRun] = Field(default_factory=list)


class MemoryComparison(JsonModel):
    """契约 §5/§7：版本配对比较（结果与引用快照一起冻结）。"""

    id: Uuid
    baseline_experiment_id: Uuid
    candidate_experiment_id: Uuid
    baseline_candidate_id: ShortId
    candidate_candidate_id: ShortId
    changed_fields: list[Annotated[StrictStr, Field(min_length=1, max_length=200)]] = (
        Field(default_factory=list)
    )
    policy: MemoryAdoptionPolicy = Field(default_factory=MemoryAdoptionPolicy)
    status: Literal["pending", "completed", "incomplete", "rejected"] = "pending"
    result: JsonObject = Field(default_factory=dict)
    snapshot: JsonObject = Field(default_factory=dict)
    created_at: datetime | None = None


# =========================================================================== 引用


class MemoryScenarioRef(JsonModel):
    """契约 §1：普通 case 指向专项场景的可选引用。"""

    dataset_revision_id: Uuid
    scenario_id: ShortId
    revision: PositiveInt


class MemoryReviewRef(JsonModel):
    """契约 §4：逐 probe 复核引用。"""

    scenario_id: ShortId
    probe_id: ShortId
    metric_id: ShortId
    grading_revision: PositiveInt
