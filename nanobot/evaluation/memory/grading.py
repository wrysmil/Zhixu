"""记忆专项逐 probe 评分、事实匹配与聚合（契约 §4 / §8.2 / §8.3 / §8.4）。

匹配 v1（契约 §8.2）：

1. 原子事实拆分，保留 ``(subject,key,value,scope,valid_from,valid_to,origin_refs)``；
2. 版本化规范化（NFKC、空白统一、数字/日期格式统一）——**不**去掉否定词、比较符、
   时间限定或作用域（去掉它们会把「不含糖」变成「含糖」）；
3. 等价边：只允许**已校准**的 ``equivalent``；``unknown`` 的必需关系使该指标
   ``not_assessable``，不能当不相等；
4. 预测事实先去重（重复记冗余，不增加 TP），再做**最大基数二分图匹配**
   （确定性增广路，节点排序固定）——禁止按出现顺序贪心，一个事实不能重复充当
   多个 TP。

聚合（契约 §8.4）：probe → trial → scene 等权，
``s_i = (1/K)Σ_k[(1/Q_i)Σ_j q(i,k,j)]``，``S = (1/N)Σ_i s_i``；
严格场景通过率与重复稳定性是另外两个指标，三者不可互换。

未知不填 0：缺后端状态时只算行为，不生成伪状态分数。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import Field

from nanobot.evaluation.graders.calibration import (
    grading_digest,
    next_grading_revision,
    review_digest,
)
from nanobot.evaluation.graders.judge import EquivalenceJudgement, EquivalenceVerdict
from nanobot.evaluation.memory.metrics import (
    ABSTENTION_ACCURACY,
    CROSS_SESSION_ACCURACY,
    DUPLICATE_MEMORY_RATE,
    EVIDENCE_RECALL_AT_K,
    EXTRACTION_F1,
    EXTRACTION_PRECISION,
    EXTRACTION_RECALL,
    FACT_COVERAGE,
    FALSE_ABSTENTION_RATE,
    FORGET_BEHAVIOR_SUCCESS,
    FORGET_STATE_SUCCESS,
    GROUNDED_MEMORY_SUCCESS,
    INJECTION_RECALL,
    MRR,
    NEGATIVE_WRITE_SUCCESS,
    PREFERENCE_COMPLIANCE,
    QA_ACCURACY,
    QA_ACCURACY_CATEGORY_MACRO,
    QA_ACCURACY_PROBE_MICRO,
    QA_ACCURACY_SCENARIO_MACRO,
    REPEAT_STABILITY,
    RESTART_ACCURACY,
    RETRIEVAL_PRECISION_RETURNED_AT_K,
    SCOPE_LEAK_RATE,
    STALE_USE_RATE,
    STRICT_SCENARIO_PASS_RATE,
    UNSUPPORTED_MEMORY_RATE,
    UPDATE_ACCURACY,
    build_metric,
    coverage_of,
    extraction_prf,
    ratio_metric,
)
from nanobot.evaluation.memory.models import (
    JsonModel,
    MemoryFact,
    MemoryGold,
    MemoryGradingPolicy,
    MemoryMetricResult,
    MemoryMetricStatus,
    MemoryProbeGold,
    MemoryScope,
    MemoryUsage,
    NonNegativeInt,
    PositiveInt,
    ProbeExecution,
    ShortId,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)

#: 等价判定结论与评分层共用同一类型：``unknown`` 不是「不相等」。
EquivalenceDecision = EquivalenceJudgement

#: 契约 §8.2：原子事实匹配算法版本。
FACT_MATCHING_VERSION = "fact-matching/v1"
#: 契约 §8.4：指标聚合算法版本。
METRIC_AGGREGATION_VERSION = "metric-aggregation/v1"
#: 契约 §8.5：区间算法版本（此处只登记，不在本模块计算）。
BOOTSTRAP_VERSION = "paired-cluster-percentile/v1"

#: 场景标签 → 分类指标（契约 §8.3「定向更新、偏好、重启题以对应 probe 的 qa_correct 聚合」）。
CATEGORY_TAG_METRICS: dict[str, str] = {
    "preference": PREFERENCE_COMPLIANCE,
    "update": UPDATE_ACCURACY,
    "cross_session": CROSS_SESSION_ACCURACY,
    "restart": RESTART_ACCURACY,
}

#: 没有标签的场景归入的兜底类别。
DEFAULT_CATEGORY = "untagged"


def default_grading_policy() -> MemoryGradingPolicy:
    """本模块实现的判分策略版本（与契约 §8.2/§8.4 的口径一一对应）。"""
    return MemoryGradingPolicy(
        fact_matching_version=FACT_MATCHING_VERSION,
        metric_aggregation_version=METRIC_AGGREGATION_VERSION,
        bootstrap_version=BOOTSTRAP_VERSION,
    )


# =========================================================================== 规范化

_WHITESPACE_RE = re.compile(r"\s+")
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_TRAILING_ZERO_RE = re.compile(r"(?<=\d)\.0+(?=\D|$)")  # "3.0" → "3"
_CN_DATE_RE = re.compile(
    r"(\d{4})\s*年\s*([零〇一二三四五六七八九十\d]{1,3})\s*月\s*([零〇一二三四五六七八九十\d]{1,3})\s*日"
)
_CN_MONTH_RE = re.compile(r"(\d{4})\s*年\s*([零〇一二三四五六七八九十\d]{1,3})\s*月")
_SLASH_DATE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_CN_NUM_RUN_RE = re.compile(r"[零〇一二三四五六七八九十百千万两]+")

_CN_DIGITS: dict[str, int] = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS: dict[str, int] = {"十": 10, "百": 100, "千": 1000, "万": 10000}
#: 允许中文数字转换的后接量词；没有量词时不做转换（避免把「张三」变成「张3」）。
_CN_MEASURE_SUFFIXES = (
    "月",
    "日",
    "天",
    "年",
    "个",
    "次",
    "点",
    "杯",
    "斤",
    "公里",
    "米",
    "元",
    "块",
    "岁",
    "层",
    "步",
    "件",
    "张",
    "条",
    "瓶",
    "片",
    "支",
    "本",
    "台",
    "辆",
    "座",
    "间",
    "份",
    "倍",
)


def chinese_number_to_int(text: str) -> int | None:
    """中文数字 → 整数；不能确定时返回 ``None``（不猜）。"""
    if not text:
        return None
    if any(char not in _CN_DIGITS and char not in _CN_UNITS for char in text):
        return None
    total = 0
    section = 0
    digit: int | None = None
    for char in text:
        if char in _CN_DIGITS:
            digit = _CN_DIGITS[char]
            continue
        unit = _CN_UNITS[char]
        if unit == 10000:
            section = (section + (digit or 0)) * unit
            total += section
            section = 0
            digit = None
            continue
        if digit is None:
            if unit == 10:
                digit = 1
            else:
                return None
        section += digit * unit
        digit = None
    value = total + section + (digit or 0)
    return value


def _replace_cn_number(match: re.Match[str]) -> str:
    """把中文数字片段替换成阿拉伯数字；不能确定就原样保留。"""
    text = match.group(0)
    if len(text) > 1 and "两" in text:
        # 「两三」= 两三个，不是 23：不确定就不动。
        return text
    value = chinese_number_to_int(text)
    if value is None or value >= 10000:
        return text
    return str(value)


def _parse_number(text: str) -> int | None:
    """阿拉伯数字或中文数字 → 整数；不能确定就返回 ``None``。"""
    if text.isdigit():
        return int(text)
    return chinese_number_to_int(text)


def _replace_cn_date(match: re.Match[str]) -> str:
    year = match.group(1)
    month = _parse_number(match.group(2))
    day = _parse_number(match.group(3))
    if month is None or day is None:
        return match.group(0)
    return f"{year}-{month:02d}-{day:02d}"


def _replace_cn_month(match: re.Match[str]) -> str:
    year = match.group(1)
    month = _parse_number(match.group(2))
    if month is None:
        return match.group(0)
    return f"{year}-{month:02d}"


def _pad_iso_date(match: re.Match[str]) -> str:
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def normalize_text(text: str) -> str:
    """版本化规范化：NFKC → 空白统一 → 数字/日期格式统一。

    **不去掉**否定词、比较符、时间限定与作用域——去掉它们会把「不含糖」变成
    「含糖」、把「大于 10」变成「10」。中文数字只在能确定时转换。
    """
    body = unicodedata.normalize("NFKC", text)
    body = _CN_DATE_RE.sub(_replace_cn_date, body)
    body = _CN_MONTH_RE.sub(_replace_cn_month, body)
    body = _SLASH_DATE_RE.sub(_pad_iso_date, body)
    body = _ISO_DATE_RE.sub(_pad_iso_date, body)
    body = _WHITESPACE_RE.sub(" ", body).strip()
    body = _THOUSANDS_RE.sub("", body)
    body = _TRAILING_ZERO_RE.sub("", body)
    body = _CN_NUM_RUN_RE.sub(_replace_cn_number, body)
    return body


@dataclass(frozen=True)
class NormalizedFact:
    """规范化后的原子事实；``canonical`` 是去重与排序用的稳定键。"""

    subject: str
    key: str
    value: str
    scope: tuple[str, str] | None
    valid_from: datetime | None
    valid_to: datetime | None
    origin_refs: tuple[str, ...]
    canonical: str

    @property
    def scope_key(self) -> tuple[str, str] | None:
        return self.scope


def _scope_key(scope: MemoryScope | None) -> tuple[str, str] | None:
    if scope is None:
        return None
    return (scope.subject_id, scope.workspace_id)


def _normalize_atom(
    *,
    subject: str | None,
    key: str,
    value: str,
    scope: MemoryScope | None,
    valid_from: datetime | None,
    valid_to: datetime | None,
    origin_refs: Sequence[str],
) -> NormalizedFact:
    normalized_subject = normalize_text(subject or "")
    normalized_key = normalize_text(key)
    normalized_value = normalize_text(value)
    scope_tuple = _scope_key(scope)
    refs = tuple(origin_refs)
    canonical = "\x1f".join(
        (
            normalized_subject,
            normalized_key,
            normalized_value,
            "|".join(scope_tuple) if scope_tuple else "",
            valid_from.isoformat() if valid_from else "",
            valid_to.isoformat() if valid_to else "",
        )
    )
    return NormalizedFact(
        subject=normalized_subject,
        key=normalized_key,
        value=normalized_value,
        scope=scope_tuple,
        valid_from=valid_from,
        valid_to=valid_to,
        origin_refs=refs,
        canonical=canonical,
    )


def normalize_fact(fact: MemoryFact) -> NormalizedFact:
    """把 :class:`MemoryFact` 规范化。"""
    return _normalize_atom(
        subject=fact.subject,
        key=fact.key,
        value=fact.value,
        scope=fact.scope,
        valid_from=fact.valid_from,
        valid_to=fact.valid_to,
        origin_refs=fact.evidence_message_ids,
    )


def _intervals_compatible(left: NormalizedFact, right: NormalizedFact) -> bool:
    """有效区间是否一致（契约 §8.2 第 2 步：查询时有效区间要一致）。

    两侧都写了同一个边界却不相同 → 不一致（例如一条说「2026-01-01 起有效」，
    另一条说「2026-06-01 起有效」）；某侧没写（``None``）视为未指定，不阻塞比较。
    """
    if (
        left.valid_from is not None
        and right.valid_from is not None
        and left.valid_from != right.valid_from
    ):
        return False
    if (
        left.valid_to is not None
        and right.valid_to is not None
        and left.valid_to != right.valid_to
    ):
        return False
    return True


def _comparable(gold: NormalizedFact, predicted: NormalizedFact) -> bool:
    """主体/scope/有效区间一致才允许比较（契约 §8.2 第 2 步）。

    key **不再**作为准入门槛：数据集里的 `required_facts[].key` 是人工标注名
    （如 `pet_name`），产品侧 facts 的 key 来自记忆行的 predicate/subject，两套
    命名空间永远不可能逐字相等——把它当硬门槛会让所有正面场景在结构上恒为 0 分
    （2026-11-04 复现）。是否等价仍由 resolver 按 value 判定，且
    :func:`match_facts` 是一对一匹配，同值不同 key 不会重复计入召回。
    """
    if gold.subject and predicted.subject and gold.subject != predicted.subject:
        return False
    if gold.scope is not None and predicted.scope is not None and gold.scope != predicted.scope:
        return False
    return _intervals_compatible(gold, predicted)


# =========================================================================== 等价判定


class FactEquivalenceResolver(Protocol):
    """事实等价判定器。

    ``calibrated`` 为假时，本模块**不采信**它给出的 ``equivalent``：等价边降级成
    ``unknown``，对应指标变成 ``not_assessable``。确定性规则自身
    （:class:`DeterministicFactResolver`）是版本化可信规则，恒为已校准。
    """

    calibrated: bool

    def resolve(
        self,
        gold: MemoryFact,
        predicted: MemoryFact,
        *,
        at: datetime | None = None,
    ) -> EquivalenceDecision: ...


class DeterministicFactResolver:
    """确定性等价判定：规范化后逐字相等，或 gold 取值被完整包含在预测值里。

    包含判定是**单向**的（gold ⊆ predicted）：记忆行常是整句，标注原子是句中的
    取值；反向的截断（预测「青岚」 vs gold「青岚城」）不算命中。数字/日期/布尔
    一律只认逐字相等。判定不了"同义"就判不相等——这会低估召回，但不会把不同的
    事实当成同一个；需要语义等价时必须换成经过校准的评分器。
    """

    calibrated = True

    def resolve(
        self,
        gold: MemoryFact,
        predicted: MemoryFact,
        *,
        at: datetime | None = None,
    ) -> EquivalenceDecision:
        del at
        left = normalize_fact(gold)
        right = normalize_fact(predicted)
        if not _comparable(left, right):
            return EquivalenceDecision("unrelated", "SUBJECT_SCOPE_OR_TIME_MISMATCH")
        if left.value == right.value:
            return EquivalenceDecision("equivalent", "CANONICAL_VALUE_EQUAL")
        if _both_comparable_types(left.value, right.value):
            # 数字/日期/布尔只有逐字相等才算同一事实；不同 key 之间不做矛盾判定，
            # 免得「城市=5」和「年龄=6」被当成互相矛盾。
            if left.key == right.key:
                return EquivalenceDecision("contradictory", "SAME_KEY_DIFFERENT_NUMERIC_VALUE")
            return EquivalenceDecision("unrelated", "CANONICAL_VALUE_DIFFERENT")
        # 方向性包含：gold 是标注出来的取值原子（「阿橙」），产品侧 value 常是整句
        # （「用户养有一只名叫阿橙的橘猫」）。反过来（预测值更短）是截断，不算命中。
        if left.value and right.value and len(left.value) >= 2 and left.value in right.value:
            return EquivalenceDecision("equivalent", "GOLD_VALUE_CONTAINED_IN_PREDICTED")
        return EquivalenceDecision("unrelated", "CANONICAL_VALUE_DIFFERENT")


def _both_comparable_types(left: str, right: str) -> bool:
    """两个值是否都是可比较的确定类型（数字/日期/布尔）。"""
    def _kind(value: str) -> str | None:
        if value in {"true", "false", "yes", "no"}:
            return "bool"
        if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
            return "number"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}(?::\d{2})?)?", value):
            return "date"
        return None

    left_kind = _kind(left)
    return left_kind is not None and left_kind == _kind(right)


# =========================================================================== 匹配


@dataclass(frozen=True)
class FactMatch:
    gold_index: int
    predicted_index: int
    decision: EquivalenceDecision


@dataclass(frozen=True)
class MatchOutcome:
    """一次匹配的结果。"""

    pairs: tuple[FactMatch, ...] = ()
    unmatched_gold: tuple[int, ...] = ()
    unmatched_predictions: tuple[int, ...] = ()
    undetermined_gold: tuple[int, ...] = ()
    undetermined_predictions: tuple[int, ...] = ()

    @property
    def true_positives(self) -> int:
        return len(self.pairs)

    @property
    def undetermined(self) -> bool:
        return bool(self.undetermined_gold or self.undetermined_predictions)


def _sorted_atoms(atoms: Sequence[NormalizedFact]) -> list[int]:
    """固定节点排序：先按 canonical，再按原始下标。"""
    return sorted(range(len(atoms)), key=lambda index: (atoms[index].canonical, index))


def _max_cardinality_matching(
    adjacency: Sequence[Sequence[int]], gold_count: int
) -> dict[int, int]:
    """最大基数二分图匹配（确定性增广路）。

    返回 ``gold_index -> predicted_index``。节点顺序固定，因此同一输入永远得到
    同一个匹配——不做按出现顺序的贪心。
    """
    matched_gold: dict[int, int] = {}

    def _augment(predicted: int, visited: set[int]) -> bool:
        for gold in adjacency[predicted]:
            if gold in visited:
                continue
            visited.add(gold)
            holder = matched_gold.get(gold)
            if holder is None or _augment(holder, visited):
                matched_gold[gold] = predicted
                return True
        return False

    for predicted in range(len(adjacency)):
        _augment(predicted, set())
    del gold_count
    return matched_gold


def match_facts(
    gold_facts: Sequence[MemoryFact],
    predicted_facts: Sequence[MemoryFact],
    resolver: FactEquivalenceResolver,
    *,
    at: datetime | None = None,
) -> MatchOutcome:
    """在 gold 与预测事实之间做最大基数匹配。

    ``unknown`` 的必需关系会被记录（``undetermined_*``），由调用方决定把对应指标
    标成 ``not_assessable``。
    """
    gold_atoms = [normalize_fact(item) for item in gold_facts]
    predicted_atoms = [normalize_fact(item) for item in predicted_facts]
    gold_order = _sorted_atoms(gold_atoms)
    predicted_order = _sorted_atoms(predicted_atoms)

    adjacency: list[list[int]] = [[] for _ in predicted_order]
    undetermined_gold: set[int] = set()
    undetermined_predictions: set[int] = set()
    for predicted_position, predicted_index in enumerate(predicted_order):
        predicted_atom = predicted_atoms[predicted_index]
        for gold_position, gold_index in enumerate(gold_order):
            gold_atom = gold_atoms[gold_index]
            if not _comparable(gold_atom, predicted_atom):
                continue
            decision = resolver.resolve(
                gold_facts[gold_index], predicted_facts[predicted_index], at=at
            )
            verdict = decision.verdict
            if verdict == "equivalent" and not getattr(resolver, "calibrated", False):
                verdict = "unknown"
            if verdict == "equivalent":
                adjacency[predicted_position].append(gold_position)
            elif verdict == "unknown":
                undetermined_gold.add(gold_position)
                undetermined_predictions.add(predicted_position)

    matched = _max_cardinality_matching(adjacency, len(gold_order))
    pairs: list[FactMatch] = []
    for gold_position, predicted_position in sorted(matched.items()):
        pairs.append(
            FactMatch(
                gold_index=gold_order[gold_position],
                predicted_index=predicted_order[predicted_position],
                decision=EquivalenceDecision("equivalent", "MATCHED"),
            )
        )
    matched_gold = {item.gold_index for item in pairs}
    matched_predictions = {item.predicted_index for item in pairs}
    return MatchOutcome(
        pairs=tuple(pairs),
        unmatched_gold=tuple(
            index for index in range(len(gold_facts)) if index not in matched_gold
        ),
        unmatched_predictions=tuple(
            index for index in range(len(predicted_facts)) if index not in matched_predictions
        ),
        undetermined_gold=tuple(
            gold_order[position] for position in sorted(undetermined_gold)
        ),
        undetermined_predictions=tuple(
            predicted_order[position] for position in sorted(undetermined_predictions)
        ),
    )


def _dedupe_facts(facts: Sequence[MemoryFact]) -> tuple[list[MemoryFact], int]:
    """去重（保留首次出现），返回 ``(去重后, 冗余条数)``。"""
    seen: set[str] = set()
    unique: list[MemoryFact] = []
    duplicates = 0
    for fact in facts:
        canonical = normalize_fact(fact).canonical
        if canonical in seen:
            duplicates += 1
            continue
        seen.add(canonical)
        unique.append(fact)
    return unique, duplicates


# =========================================================================== 执行产物


class MemoryStateObservation(JsonModel):
    """状态层观测：某个事实在某个存储面是否存在。"""

    fact_key: ShortId
    surface: Literal["store", "index", "derived", "rebuild"]
    present: StrictBool


class MemoryProbeOutcome(JsonModel):
    """逐 probe 的执行产物 + 事实层观测。"""

    execution: ProbeExecution
    question: StrictStr = ""
    asked_at: datetime | None = None
    scope: MemoryScope | None = None
    #: 回答里断言的原子事实（由执行端按确定性规则解析或人工标注）。
    asserted_facts: list[MemoryFact] = Field(default_factory=list)
    #: 回答是否已经被解析成原子事实；否则二值判定只能是未知。
    answer_facts_parsed: StrictBool = False
    #: 实际注入模型上下文的事实。
    injected_facts: list[MemoryFact] = Field(default_factory=list)
    #: 该 probe 是否发生在删除事件之后。
    after_forget: StrictBool = False
    #: 该 probe 的作用域面是否可观察；不可观察时不参与 scope 泄漏分母。
    scope_observable: StrictBool = True

    @property
    def probe_id(self) -> str:
        return self.execution.probe_id


class MemoryTrialExecution(JsonModel):
    """一次 trial 的执行产物（评分端输入）。"""

    scenario_id: ShortId
    scenario_revision: PositiveInt = 1
    track: Literal["extraction", "retrieval", "end_to_end"]
    scope: MemoryScope
    #: 场景标签（类别）。
    tags: list[ShortId] = Field(default_factory=list)
    #: 逐 probe 结果。
    probes: list[MemoryProbeOutcome] = Field(default_factory=list)
    #: 抽取轨道写入记忆的原子事实。
    extracted_facts: list[MemoryFact] = Field(default_factory=list)
    #: 源会话支持的事实；``None`` 表示没有材料（未知，不是 0 条）。
    source_supported_facts: list[MemoryFact] | None = None
    #: 状态层观测；``None`` 表示后端不支持导出状态。
    state_observations: list[MemoryStateObservation] | None = None
    #: 被删除的事实 key。
    forgotten_fact_keys: list[ShortId] = Field(default_factory=list)
    #: 分阶段 usage：extraction / derivation / embedding / reader / judge。
    stage_usage: dict[str, MemoryUsage] = Field(default_factory=dict)
    #: 存储字节；未知为 None。
    storage_bytes: StrictInt | None = None
    #: 估算费用；单价未知为 None。
    estimated_cost: StrictFloat | None = None
    #: 重复次数下标（同一场景的第几次）。
    trial_index: NonNegativeInt = 0


class MemoryProbeGrade(JsonModel):
    """逐 probe 的判分结论。"""

    probe_id: ShortId
    category: ShortId = DEFAULT_CATEGORY
    qa_correct: StrictBool | None = None
    qa_status: MemoryMetricStatus = "not_assessable"
    fact_coverage: StrictFloat | None = None
    grounded_memory_success: StrictBool | None = None
    reasons: list[ShortId] = Field(default_factory=list)
    metrics: list[MemoryMetricResult] = Field(default_factory=list)


class MemoryTrialGrade(JsonModel):
    """一次 trial 的判分结论。"""

    scenario_id: ShortId
    track: str
    grading_revision: PositiveInt = 1
    review_digest: StrictStr | None = None
    previous_review_digest: StrictStr | None = None
    categories: list[ShortId] = Field(default_factory=list)
    probe_results: list[MemoryProbeGrade] = Field(default_factory=list)
    strict_pass: StrictBool | None = None
    #: 重复次数下标（同一场景的第几次）；聚合时用于固定 trial 顺序。
    trial_index: NonNegativeInt = 0
    metrics: list[MemoryMetricResult] = Field(default_factory=list)


# =========================================================================== 逐 probe 评分


def _scope_violation(
    facts: Sequence[MemoryFact], scope: MemoryScope | None
) -> bool:
    """事实是否越过了 probe 的作用域。"""
    if scope is None:
        return False
    expected = (scope.subject_id, scope.workspace_id)
    for fact in facts:
        fact_scope = _scope_key(fact.scope)
        if fact_scope is None:
            continue
        if fact_scope != expected:
            return True
    return False


def _recall_to_fact(item: Any) -> MemoryFact:
    """把召回结果转成可匹配的事实（召回结果没有有效区间）。"""
    return MemoryFact(
        key=item.key,
        value=item.value,
        subject=item.subject,
        scope=item.scope,
    )


def _required_units(probe_gold: MemoryProbeGold) -> list[MemoryFact]:
    """本题所需证据单元：优先 ``required_facts``，否则取第一组等价证据。"""
    if probe_gold.required_facts:
        return list(probe_gold.required_facts)
    for evidence_set in probe_gold.allowed_evidence_sets:
        if evidence_set:
            return list(evidence_set)
    return []


def _coverage(
    required: Sequence[MemoryFact],
    candidates: Sequence[MemoryFact],
    resolver: FactEquivalenceResolver,
    *,
    at: datetime | None,
) -> tuple[int, bool]:
    """返回 ``(已覆盖数, 是否存在未知关系)``。"""
    if not required:
        return 0, False
    outcome = match_facts(required, candidates, resolver, at=at)
    return outcome.true_positives, outcome.undetermined


def _grade_probe(
    outcome: MemoryProbeOutcome,
    probe_gold: MemoryProbeGold | None,
    execution: MemoryTrialExecution,
    resolver: FactEquivalenceResolver,
    *,
    recall_k: int,
    grading_revision: int,
) -> MemoryProbeGrade:
    """逐 probe 判分：``qa_correct`` 是 0/1，部分正确另记事实覆盖分。"""
    probe_id = outcome.probe_id
    reasons: list[str] = []
    metrics: list[MemoryMetricResult] = []
    scope = outcome.scope or execution.scope
    at = outcome.asked_at

    if probe_gold is None:
        reasons.append("GOLD_MISSING_FOR_PROBE")
        return MemoryProbeGrade(
            probe_id=probe_id,
            qa_correct=None,
            qa_status="not_assessable",
            reasons=reasons,
            metrics=metrics,
        )

    asserted = list(outcome.asserted_facts)
    recalled = [_recall_to_fact(item) for item in outcome.execution.recalled]
    injected = list(outcome.injected_facts)
    required = list(probe_gold.required_facts)
    units = _required_units(probe_gold)

    recall_leak = _scope_violation(recalled, scope)
    injection_leak = _scope_violation(injected, scope)
    answer_leak = _scope_violation(asserted, scope)
    scope_leak = recall_leak or injection_leak or answer_leak

    covered, coverage_unknown = _coverage(required, asserted, resolver, at=at)
    forbidden_hit = False
    forbidden_unknown = False
    for forbidden in probe_gold.forbidden_current_values:
        forbidden_outcome = match_facts([forbidden], asserted, resolver, at=at)
        if forbidden_outcome.true_positives:
            forbidden_hit = True
        elif forbidden_outcome.undetermined:
            forbidden_unknown = True

    # ---- 二值判定：先确定违规，再看未知
    qa_correct: bool | None
    if not outcome.answer_facts_parsed:
        qa_correct = None
        reasons.append("ANSWER_FACTS_UNPARSED")
    elif outcome.execution.answer is None or not outcome.execution.answer.strip():
        qa_correct = False
        reasons.append("NO_ANSWER")
    elif probe_gold.answerability == "unanswerable":
        if asserted:
            qa_correct = False
            reasons.append("FABRICATED_OR_LEAKED")
        else:
            qa_correct = True
    else:
        judgeable = bool(required or probe_gold.forbidden_current_values)
        if not judgeable:
            qa_correct = None
            reasons.append("NO_REQUIRED_FACTS")
        elif forbidden_hit:
            qa_correct = False
            reasons.append("FORBIDDEN_VALUE_ASSERTED")
        elif scope_leak:
            qa_correct = False
            reasons.append("SCOPE_VIOLATION")
        elif coverage_unknown or forbidden_unknown:
            qa_correct = None
            reasons.append("SEMANTIC_EQUIVALENCE_UNKNOWN")
        elif covered < len(required):
            qa_correct = False
            reasons.append("REQUIRED_FACTS_NOT_COVERED")
        else:
            qa_correct = True

    fact_coverage: float | None = None
    if required and not coverage_unknown:
        fact_coverage = covered / len(required)

    # ---- 记忆证据可用性
    available = [fact for fact in recalled + injected if not _scope_violation([fact], scope)]
    evidence_covered, evidence_unknown = _coverage(required, available, resolver, at=at)
    recall_observable = (
        outcome.execution.coverage.planned == 0
        or outcome.execution.coverage.scored >= outcome.execution.coverage.planned
    )
    if qa_correct is None:
        grounded: bool | None = None
        reasons.append("GROUNDED_UNKNOWN_CAUSE")
    elif not recall_observable:
        grounded = None
        reasons.append("RECALL_COVERAGE_INCOMPLETE")
    elif evidence_unknown:
        grounded = None
        reasons.append("SEMANTIC_EQUIVALENCE_UNKNOWN")
    else:
        grounded = qa_correct is True and evidence_covered >= len(required)
        if not grounded and qa_correct is True:
            reasons.append("REQUIRED_EVIDENCE_NOT_AVAILABLE")

    # ---- 逐 probe 指标
    qa_metric = ratio_metric(
        QA_ACCURACY,
        numerator=None if qa_correct is None else int(qa_correct),
        denominator=None if qa_correct is None else 1,
        scope="probe",
        grading_revision=grading_revision,
        reasons=reasons,
    )
    metrics.append(qa_metric)

    if fact_coverage is None:
        metrics.append(
            build_metric(
                FACT_COVERAGE,
                value=None,
                status="not_assessable" if required else "not_applicable",
                scope="probe",
                grading_revision=grading_revision,
                reasons=["COVERAGE_UNKNOWN"] if required else ["NO_REQUIRED_FACTS"],
            )
        )
    else:
        metrics.append(
            ratio_metric(
                FACT_COVERAGE,
                numerator=covered,
                denominator=len(required),
                scope="probe",
                grading_revision=grading_revision,
            )
        )

    metrics.append(
        build_metric(
            GROUNDED_MEMORY_SUCCESS,
            value=None if grounded is None else float(grounded),
            numerator=None if grounded is None else float(int(grounded)),
            denominator=None if grounded is None else 1.0,
            status="not_assessable" if grounded is None else "scored",
            scope="probe",
            grading_revision=grading_revision,
            reasons=["GROUNDED_NOT_ASSESSABLE"] if grounded is None else [],
        )
    )

    # ---- 检索与注入
    metrics.extend(
        _retrieval_metrics(
            units=units,
            recalled=recalled,
            injected=injected,
            resolver=resolver,
            recall_k=recall_k,
            at=at,
            grading_revision=grading_revision,
        )
    )

    # ---- 拒答与过期事实
    if probe_gold.answerability == "unanswerable":
        metrics.append(
            build_metric(
                ABSTENTION_ACCURACY,
                value=None if qa_correct is None else float(int(qa_correct)),
                numerator=None if qa_correct is None else float(int(qa_correct)),
                denominator=None if qa_correct is None else 1.0,
                status="not_assessable" if qa_correct is None else "scored",
                scope="probe",
                grading_revision=grading_revision,
            )
        )
    else:
        abstained = not asserted or outcome.execution.answer is None
        metrics.append(
            ratio_metric(
                FALSE_ABSTENTION_RATE,
                numerator=int(abstained),
                denominator=1,
                scope="probe",
                grading_revision=grading_revision,
            )
        )

    if probe_gold.forbidden_current_values:
        metrics.append(
            ratio_metric(
                STALE_USE_RATE,
                numerator=int(forbidden_hit),
                denominator=1,
                scope="probe",
                grading_revision=grading_revision,
            )
        )
    else:
        metrics.append(
            build_metric(
                STALE_USE_RATE,
                value=None,
                status="not_applicable",
                scope="probe",
                grading_revision=grading_revision,
                reasons=["NO_CURRENT_STATE_PROBE"],
            )
        )

    return MemoryProbeGrade(
        probe_id=probe_id,
        category=execution.tags[0] if execution.tags else DEFAULT_CATEGORY,
        qa_correct=qa_correct,
        qa_status="scored" if qa_correct is not None else "not_assessable",
        fact_coverage=fact_coverage,
        grounded_memory_success=grounded,
        reasons=reasons,
        metrics=metrics,
    )


def _retrieval_metrics(
    *,
    units: Sequence[MemoryFact],
    recalled: Sequence[MemoryFact],
    injected: Sequence[MemoryFact],
    resolver: FactEquivalenceResolver,
    recall_k: int,
    at: datetime | None,
    grading_revision: int,
) -> list[MemoryMetricResult]:
    """检索与注入指标（分母是实际返回条数，不是固定 k）。"""
    metrics: list[MemoryMetricResult] = []
    if not units:
        for metric_id in (
            EVIDENCE_RECALL_AT_K,
            RETRIEVAL_PRECISION_RETURNED_AT_K,
            MRR,
            INJECTION_RECALL,
        ):
            metrics.append(
                build_metric(
                    metric_id,
                    value=None,
                    status="not_applicable",
                    scope="probe",
                    grading_revision=grading_revision,
                    reasons=["NO_GOLD_EVIDENCE"],
                )
            )
        return metrics

    top = list(recalled)[: max(recall_k, 0)]
    # 一次匹配同时给出：覆盖了多少证据单元、哪些返回项相关、首个相关排名。
    # 重复事实由一对一匹配天然吸收（重复项不会多覆盖一个单元），
    # 因此不需要再单独去重——也避免对每条返回项重复调用判定器。
    matched = match_facts(units, top, resolver, at=at)
    covered = matched.true_positives
    coverage_unknown = matched.undetermined_gold
    matched_predictions = {item.predicted_index for item in matched.pairs}
    metrics.append(
        build_metric(
            EVIDENCE_RECALL_AT_K,
            value=None if coverage_unknown else covered / len(units),
            numerator=None if coverage_unknown else float(covered),
            denominator=None if coverage_unknown else float(len(units)),
            status="not_assessable" if coverage_unknown else "scored",
            scope="probe",
            grading_revision=grading_revision,
            reasons=["SEMANTIC_EQUIVALENCE_UNKNOWN"] if coverage_unknown else [],
        )
    )

    relevant = len(matched_predictions)
    first_rank: int | None = None
    for index in sorted(matched_predictions):
        first_rank = index + 1
        break
    undetermined_item = bool(matched.undetermined_predictions)
    if undetermined_item:
        metrics.append(
            build_metric(
                RETRIEVAL_PRECISION_RETURNED_AT_K,
                value=None,
                numerator=float(relevant),
                denominator=float(len(top)),
                status="not_assessable",
                scope="probe",
                grading_revision=grading_revision,
                reasons=["RELEVANCE_UNKNOWN"],
            )
        )
    else:
        metrics.append(
            ratio_metric(
                RETRIEVAL_PRECISION_RETURNED_AT_K,
                numerator=relevant,
                denominator=len(top),
                scope="probe",
                grading_revision=grading_revision,
                reasons=["NO_RESULTS_RETURNED"] if not top else [],
            )
        )

    metrics.append(
        ratio_metric(
            MRR,
            numerator=None if first_rank is None else 1,
            denominator=None if first_rank is None else first_rank,
            scope="probe",
            grading_revision=grading_revision,
            reasons=["NO_RELEVANT_RESULT"] if first_rank is None else [],
        )
    )

    injected_covered, injected_unknown = _coverage(units, injected, resolver, at=at)
    metrics.append(
        build_metric(
            INJECTION_RECALL,
            value=None if injected_unknown else injected_covered / len(units),
            numerator=None if injected_unknown else float(injected_covered),
            denominator=None if injected_unknown else float(len(units)),
            status="not_assessable" if injected_unknown else "scored",
            scope="probe",
            grading_revision=grading_revision,
            reasons=["SEMANTIC_EQUIVALENCE_UNKNOWN"] if injected_unknown else [],
        )
    )
    return metrics


# =========================================================================== trial 评分


def _extraction_metrics(
    execution: MemoryTrialExecution,
    gold: MemoryGold,
    resolver: FactEquivalenceResolver,
    *,
    grading_revision: int,
) -> list[MemoryMetricResult]:
    """抽取轨道指标（契约 §8.2 第 4–5 步）。"""
    metrics: list[MemoryMetricResult] = []
    targets = list(gold.extraction_facts)
    raw_predictions = list(execution.extracted_facts)
    predictions, duplicates = _dedupe_facts(raw_predictions)

    if execution.track != "extraction":
        for metric_id in (
            EXTRACTION_PRECISION,
            EXTRACTION_RECALL,
            EXTRACTION_F1,
            UNSUPPORTED_MEMORY_RATE,
            DUPLICATE_MEMORY_RATE,
            NEGATIVE_WRITE_SUCCESS,
        ):
            metrics.append(
                build_metric(
                    metric_id,
                    value=None,
                    status="not_applicable",
                    scope="scenario",
                    grading_revision=grading_revision,
                    reasons=["TRACK_NOT_EXTRACTION"],
                )
            )
        return metrics

    if not targets:
        for metric_id in (EXTRACTION_PRECISION, EXTRACTION_RECALL, EXTRACTION_F1):
            metrics.append(
                build_metric(
                    metric_id,
                    value=None,
                    status="not_applicable",
                    scope="scenario",
                    grading_revision=grading_revision,
                    reasons=["NO_LABELED_TARGET"],
                )
            )
    else:
        outcome = match_facts(targets, predictions, resolver)
        tp = outcome.true_positives
        fp = len(predictions) - tp
        fn = len(targets) - tp
        precision, recall, f1 = extraction_prf(tp, fp, fn)
        targeted = gold.annotation_scope == "targeted"
        if outcome.undetermined:
            for metric_id in (EXTRACTION_PRECISION, EXTRACTION_RECALL, EXTRACTION_F1):
                metrics.append(
                    build_metric(
                        metric_id,
                        value=None,
                        status="not_assessable",
                        scope="scenario",
                        grading_revision=grading_revision,
                        reasons=["SEMANTIC_EQUIVALENCE_UNKNOWN"],
                    )
                )
        else:
            metrics.append(
                build_metric(
                    EXTRACTION_PRECISION,
                    value=None if targeted else precision,
                    numerator=None if targeted else float(tp),
                    denominator=None if targeted else float(tp + fp),
                    status="not_assessable" if targeted else (
                        "scored" if precision is not None else "not_applicable"
                    ),
                    scope="scenario",
                    grading_revision=grading_revision,
                    reasons=["TARGETED_SCOPE_NO_FULL_PRECISION"] if targeted else [],
                )
            )
            metrics.append(
                ratio_metric(
                    EXTRACTION_RECALL,
                    numerator=tp,
                    denominator=tp + fn,
                    scope="scenario",
                    grading_revision=grading_revision,
                )
            )
            metrics.append(
                build_metric(
                    EXTRACTION_F1,
                    value=None if targeted else f1,
                    numerator=None if targeted else float(2 * tp),
                    denominator=None if targeted else float(2 * tp + fp + fn),
                    status="not_assessable" if targeted else (
                        "scored" if f1 is not None else "not_applicable"
                    ),
                    scope="scenario",
                    grading_revision=grading_revision,
                    reasons=["TARGETED_SCOPE_NO_FULL_F1"] if targeted else [],
                )
            )

    # 重复率：重复不增加 TP，单列。
    metrics.append(
        ratio_metric(
            DUPLICATE_MEMORY_RATE,
            numerator=duplicates,
            denominator=len(raw_predictions),
            scope="scenario",
            grading_revision=grading_revision,
            reasons=["NO_EXTRACTED_FACTS"] if not raw_predictions else [],
        )
    )

    # 无依据记忆率：需要源会话材料。
    if execution.source_supported_facts is None:
        metrics.append(
            build_metric(
                UNSUPPORTED_MEMORY_RATE,
                value=None,
                status="not_assessable",
                scope="scenario",
                grading_revision=grading_revision,
                reasons=["SOURCE_FACTS_UNAVAILABLE"],
            )
        )
    else:
        supported = list(execution.source_supported_facts)
        unsupported = 0
        for prediction in predictions:
            if not match_facts(supported, [prediction], resolver).true_positives:
                unsupported += 1
        metrics.append(
            ratio_metric(
                UNSUPPORTED_MEMORY_RATE,
                numerator=unsupported,
                denominator=len(predictions),
                scope="scenario",
                grading_revision=grading_revision,
                reasons=["NO_EXTRACTED_FACTS"] if not predictions else [],
            )
        )

    # 纯负例：没有事实被写出来才算成功；目标集未确认时不可评。
    if not targets:
        metrics.append(
            build_metric(
                NEGATIVE_WRITE_SUCCESS,
                value=None,
                status="not_assessable" if gold.annotation_scope == "targeted" else "not_applicable",
                scope="scenario",
                grading_revision=grading_revision,
                reasons=["TARGET_SET_NOT_CONFIRMED"],
            )
        )
    else:
        metrics.append(
            build_metric(
                NEGATIVE_WRITE_SUCCESS,
                value=None,
                status="not_applicable",
                scope="scenario",
                grading_revision=grading_revision,
                reasons=["SCENE_HAS_LABELED_TARGETS"],
            )
        )
    return metrics


def _scope_leak_metric(
    execution: MemoryTrialExecution, probe_grades: Sequence[MemoryProbeGrade]
) -> MemoryMetricResult:
    """scope 泄漏率：候选/注入/回答任一阶段越界的场景 / 可观察隔离场景。"""
    leaking = 0
    observable = 0
    for outcome, grade in zip(execution.probes, probe_grades):
        if not outcome.scope_observable:
            continue
        observable += 1
        if "SCOPE_VIOLATION" in grade.reasons:
            leaking += 1
    if observable == 0:
        return build_metric(
            SCOPE_LEAK_RATE,
            value=None,
            status="not_assessable",
            scope="scenario",
            reasons=["SCOPE_NOT_OBSERVABLE"],
        )
    return ratio_metric(
        SCOPE_LEAK_RATE,
        numerator=leaking,
        denominator=observable,
        scope="scenario",
    )


def _forget_metrics(
    execution: MemoryTrialExecution,
    gold: MemoryGold,
    probe_grades: Sequence[MemoryProbeGrade],
    *,
    grading_revision: int,
) -> list[MemoryMetricResult]:
    """遗忘行为与状态指标（契约 §8.3）。"""
    metrics: list[MemoryMetricResult] = []
    post_forget = [
        (outcome, grade)
        for outcome, grade in zip(execution.probes, probe_grades)
        if outcome.after_forget
    ]
    forgotten = set(execution.forgotten_fact_keys)
    if not post_forget:
        metrics.append(
            build_metric(
                FORGET_BEHAVIOR_SUCCESS,
                value=None,
                status="not_applicable",
                scope="scenario",
                grading_revision=grading_revision,
                reasons=["NO_POST_FORGET_PROBE"],
            )
        )
    elif not forgotten:
        metrics.append(
            build_metric(
                FORGET_BEHAVIOR_SUCCESS,
                value=None,
                status="not_assessable",
                scope="scenario",
                grading_revision=grading_revision,
                reasons=["FORGET_TARGET_UNKNOWN"],
            )
        )
    else:
        exposed = 0
        for outcome, _ in post_forget:
            for asserted in outcome.asserted_facts:
                if asserted.key in forgotten:
                    exposed += 1
                    break
        metrics.append(
            ratio_metric(
                FORGET_BEHAVIOR_SUCCESS,
                numerator=int(exposed == 0),
                denominator=1,
                scope="scenario",
                grading_revision=grading_revision,
            )
        )

    if not gold.state_expectations:
        metrics.append(
            build_metric(
                FORGET_STATE_SUCCESS,
                value=None,
                status="not_applicable",
                scope="scenario",
                grading_revision=grading_revision,
                reasons=["NO_STATE_EXPECTATION"],
            )
        )
    elif execution.state_observations is None:
        metrics.append(
            build_metric(
                FORGET_STATE_SUCCESS,
                value=None,
                status="not_assessable",
                scope="scenario",
                grading_revision=grading_revision,
                reasons=["STATE_BACKEND_UNSUPPORTED"],
            )
        )
    else:
        observations = {
            (item.fact_key, item.surface): item.present
            for item in execution.state_observations
        }
        missing = False
        failed = False
        for expectation in gold.state_expectations:
            surfaces = expectation.surfaces or ["store", "index", "derived", "rebuild"]
            for surface in surfaces:
                present = observations.get((expectation.fact_key, surface))
                if present is None:
                    missing = True
                    continue
                if present != expectation.present:
                    failed = True
        if missing:
            metrics.append(
                build_metric(
                    FORGET_STATE_SUCCESS,
                    value=None,
                    status="not_assessable",
                    scope="scenario",
                    grading_revision=grading_revision,
                    reasons=["STATE_OBSERVATION_MISSING"],
                )
            )
        else:
            metrics.append(
                ratio_metric(
                    FORGET_STATE_SUCCESS,
                    numerator=int(not failed),
                    denominator=1,
                    scope="scenario",
                    grading_revision=grading_revision,
                )
            )
    return metrics


def _usage_metrics(
    execution: MemoryTrialExecution, *, grading_revision: int
) -> list[MemoryMetricResult]:
    """分阶段 usage 与成本指标（未知保持 null）。"""
    from nanobot.evaluation.memory.metrics import (
        DERIVATION_TOKENS,
        EMBEDDING_CALLS,
        EMBEDDING_TOKENS,
        ESTIMATED_COST,
        EXTRACTION_TOKENS,
        JUDGE_TOKENS,
        MODEL_REQUESTS,
        READER_TOKENS,
        STORAGE_BYTES,
        total_metric,
    )

    stage_metric = {
        "extraction": EXTRACTION_TOKENS,
        "derivation": DERIVATION_TOKENS,
        "embedding": EMBEDDING_TOKENS,
        "reader": READER_TOKENS,
        "judge": JUDGE_TOKENS,
    }
    metrics: list[MemoryMetricResult] = []
    for stage, metric_id in stage_metric.items():
        usage = execution.stage_usage.get(stage)
        total = usage.total_tokens if usage is not None else None
        metrics.append(
            total_metric(
                metric_id,
                value=total,
                scope="dataset",
                grading_revision=grading_revision,
                reasons=["STAGE_USAGE_UNAVAILABLE"] if usage is None else [],
            )
        )
    embedding = execution.stage_usage.get("embedding")
    metrics.append(
        total_metric(
            EMBEDDING_CALLS,
            value=None if embedding is None else embedding.physical_requests,
            scope="dataset",
            grading_revision=grading_revision,
            reasons=["STAGE_USAGE_UNAVAILABLE"] if embedding is None else [],
        )
    )
    requests = sum(
        usage.physical_requests for usage in execution.stage_usage.values()
    )
    metrics.append(
        total_metric(
            MODEL_REQUESTS,
            value=requests if execution.stage_usage else None,
            scope="dataset",
            grading_revision=grading_revision,
            reasons=["STAGE_USAGE_UNAVAILABLE"] if not execution.stage_usage else [],
        )
    )
    metrics.append(
        total_metric(
            STORAGE_BYTES,
            value=execution.storage_bytes,
            scope="dataset",
            grading_revision=grading_revision,
            reasons=["STORAGE_BYTES_UNKNOWN"]
            if execution.storage_bytes is None
            else [],
        )
    )
    metrics.append(
        total_metric(
            ESTIMATED_COST,
            value=execution.estimated_cost,
            scope="dataset",
            grading_revision=grading_revision,
            reasons=["UNIT_PRICE_UNKNOWN"]
            if execution.estimated_cost is None
            else [],
        )
    )
    return metrics


def grade_memory_trial(
    execution: MemoryTrialExecution,
    gold: MemoryGold,
    policy: MemoryGradingPolicy,
    *,
    resolver: FactEquivalenceResolver | None = None,
    requested_revision: int | None = None,
    recall_k: int = 5,
    previous: MemoryTrialGrade | None = None,
    reviews: Sequence[Mapping[str, Any]] = (),
) -> MemoryTrialGrade:
    """按契约 §8 给一次 trial 评分。

    ``grading_revision`` 与 ``review_digest`` 随评分内容变化：重算（传入 ``previous``）
    会自动产生新的版本号与新的摘要，原分数不被改写——``previous`` 只被读取。
    ``reviews`` 是被引用的既有复核记录：它们进判分摘要，所以「同一批复核 + 同一份
    执行结果」永远得到同一个摘要，重算可复现。
    """
    effective_resolver = resolver or DeterministicFactResolver()
    grading_revision = (
        int(requested_revision)
        if requested_revision is not None
        else next_grading_revision(previous.grading_revision if previous else None)
    )
    version_ok = policy.fact_matching_version == FACT_MATCHING_VERSION
    reasons: list[str] = []
    probe_grades: list[MemoryProbeGrade] = []
    metrics: list[MemoryMetricResult] = []

    if not version_ok:
        reasons.append("FACT_MATCHING_VERSION_UNSUPPORTED")
        for probe in execution.probes:
            probe_grades.append(
                MemoryProbeGrade(
                    probe_id=probe.probe_id,
                    category=execution.tags[0] if execution.tags else DEFAULT_CATEGORY,
                    qa_correct=None,
                    qa_status="not_assessable",
                    reasons=list(reasons),
                    metrics=[],
                )
            )
        metrics.append(
            build_metric(
                QA_ACCURACY,
                value=None,
                status="not_assessable",
                scope="scenario",
                grading_revision=grading_revision,
                reasons=reasons,
            )
        )
    else:
        for probe in execution.probes:
            probe_grades.append(
                _grade_probe(
                    probe,
                    gold.probes.get(probe.probe_id),
                    execution,
                    effective_resolver,
                    recall_k=recall_k,
                    grading_revision=grading_revision,
                )
            )
        metrics.extend(
            _extraction_metrics(
                execution, gold, effective_resolver, grading_revision=grading_revision
            )
        )
        metrics.append(_scope_leak_metric(execution, probe_grades))
        metrics.extend(
            _forget_metrics(
                execution, gold, probe_grades, grading_revision=grading_revision
            )
        )
        metrics.extend(_usage_metrics(execution, grading_revision=grading_revision))
        for grade in probe_grades:
            metrics.extend(grade.metrics)

    strict_pass: bool | None = None
    if probe_grades and version_ok:
        strict_pass = all(grade.qa_correct is True for grade in probe_grades) and not any(
            "SCOPE_VIOLATION" in grade.reasons for grade in probe_grades
        )

    # 语义判定器的指纹与校准状态落在每个指标上（契约 §4）。
    fingerprint = getattr(effective_resolver, "fingerprint", None)
    calibration_status = getattr(effective_resolver, "calibration_status", None)
    if fingerprint or calibration_status:
        metrics = [
            item.model_copy(
                update={
                    "grader_fingerprint": fingerprint,
                    "calibration_status": calibration_status,
                }
            )
            for item in metrics
        ]
        for probe_grade in probe_grades:
            probe_grade.metrics = [
                item.model_copy(
                    update={
                        "grader_fingerprint": fingerprint,
                        "calibration_status": calibration_status,
                    }
                )
                for item in probe_grade.metrics
            ]

    digest = grading_digest(
        policy=policy,
        extra={
            "scenario_id": execution.scenario_id,
            "track": execution.track,
            "trial_index": execution.trial_index,
            "reviews": [
                {
                    "id": str(item.get("id") or ""),
                    "verdict": str(item.get("verdict") or ""),
                    "memory_ref": item.get("memory_ref"),
                }
                for item in reviews
            ],
        },
    )
    metric_digests = {
        item.id: hashlib.sha256(
            json.dumps(
                item.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        for item in metrics
    }
    review = review_digest(
        grading_revision=grading_revision,
        grading_digest_value=digest,
        metric_digests=metric_digests,
    )

    return MemoryTrialGrade(
        scenario_id=execution.scenario_id,
        track=execution.track,
        grading_revision=grading_revision,
        review_digest=review,
        previous_review_digest=previous.review_digest if previous else None,
        categories=list(execution.tags) or [DEFAULT_CATEGORY],
        probe_results=probe_grades,
        strict_pass=strict_pass,
        trial_index=execution.trial_index,
        metrics=metrics,
    )


# =========================================================================== 聚合


def aggregate_memory_scores(
    trials: Sequence[MemoryTrialGrade],
    *,
    policy: MemoryGradingPolicy | None = None,
    grading_revision: int = 1,
) -> list[MemoryMetricResult]:
    """probe → trial → scene 等权聚合（契约 §8.4）。

    ``s_i = (1/K)Σ_k[(1/Q_i)Σ_j q(i,k,j)]``，``S = (1/N)Σ_i s_i``；
    严格场景通过率与重复稳定性是另外两个指标。缺判定的场景被排除并计入覆盖度，
    不用 0 顶替。
    """
    effective_policy = policy or default_grading_policy()
    results: list[MemoryMetricResult] = []
    if effective_policy.metric_aggregation_version != METRIC_AGGREGATION_VERSION:
        for metric_id in (
            QA_ACCURACY_SCENARIO_MACRO,
            QA_ACCURACY_PROBE_MICRO,
            QA_ACCURACY_CATEGORY_MACRO,
            STRICT_SCENARIO_PASS_RATE,
            REPEAT_STABILITY,
        ):
            results.append(
                build_metric(
                    metric_id,
                    value=None,
                    status="not_assessable",
                    scope="dataset",
                    grading_revision=grading_revision,
                    reasons=["METRIC_AGGREGATION_VERSION_UNSUPPORTED"],
                )
            )
        return results

    by_scene: dict[str, list[MemoryTrialGrade]] = {}
    for trial in trials:
        by_scene.setdefault(trial.scenario_id, []).append(trial)

    scene_scores: dict[str, float] = {}
    reasons: list[str] = []
    planned_scenes = len(by_scene)
    scored_scenes = 0
    micro_numerator = 0.0
    micro_denominator = 0.0
    planned_probes = 0
    strict_pass = 0
    strict_eligible = 0
    stable = 0
    stable_eligible = 0

    for scene_id in sorted(by_scene):
        scene_trials = sorted(by_scene[scene_id], key=lambda item: item.trial_index)
        probe_counts = {len(item.probe_results) for item in scene_trials}
        if len(probe_counts) != 1:
            reasons.append(f"SCENE_PROBE_COUNT_MISMATCH:{scene_id}")
            continue
        probe_count = probe_counts.pop()
        if probe_count == 0:
            reasons.append(f"SCENE_WITHOUT_PROBE:{scene_id}")
            continue
        trial_scores: list[float] = []
        judgeable = True
        for trial in scene_trials:
            values = [grade.qa_correct for grade in trial.probe_results]
            if any(value is None for value in values):
                judgeable = False
                break
            trial_scores.append(sum(1.0 for value in values if value) / probe_count)
        if not judgeable:
            reasons.append(f"SCENE_NOT_FULLY_JUDGEABLE:{scene_id}")
            continue
        scene_scores[scene_id] = sum(trial_scores) / len(trial_scores)
        scored_scenes += 1
        planned_probes += probe_count * len(scene_trials)
        micro_numerator += sum(trial_scores) * probe_count
        micro_denominator += probe_count * len(scene_trials)
        for trial in scene_trials:
            if trial.strict_pass is None:
                continue
            strict_eligible += 1
            strict_pass += int(trial.strict_pass)
        strict_values = [trial.strict_pass for trial in scene_trials]
        if strict_values and all(value is not None for value in strict_values):
            stable_eligible += 1
            stable += int(all(strict_values))

    coverage = coverage_of(scored_scenes, planned_scenes)
    macro_reasons = list(reasons)
    if scored_scenes < planned_scenes:
        macro_reasons.append("INCOMPLETE_COVERAGE")
    results.append(
        build_metric(
            QA_ACCURACY_SCENARIO_MACRO,
            value=None
            if not scene_scores
            else sum(scene_scores.values()) / len(scene_scores),
            numerator=None if not scene_scores else float(scored_scenes),
            denominator=None if not scene_scores else float(planned_scenes),
            status="scored" if scene_scores else "not_assessable",
            scope="dataset",
            grading_revision=grading_revision,
            coverage=coverage,
            reasons=macro_reasons,
        )
    )
    micro_coverage = coverage_of(int(micro_denominator), planned_probes)
    results.append(
        build_metric(
            QA_ACCURACY_PROBE_MICRO,
            value=None
            if micro_denominator <= 0
            else micro_numerator / micro_denominator,
            numerator=micro_numerator if micro_denominator > 0 else None,
            denominator=micro_denominator if micro_denominator > 0 else None,
            status="scored" if micro_denominator > 0 else "not_assessable",
            scope="dataset",
            grading_revision=grading_revision,
            coverage=micro_coverage,
            reasons=["INCOMPLETE_COVERAGE"]
            if micro_denominator < planned_probes
            else [],
        )
    )

    # 类别宏平均：各类别内部按场景平均，再对类别平均。
    categories: dict[str, list[str]] = {}
    for trial in trials:
        for category in trial.categories or [DEFAULT_CATEGORY]:
            categories.setdefault(category, []).append(trial.scenario_id)
    category_values: list[float] = []
    for category in sorted(categories):
        scene_ids = [
            scene_id for scene_id in sorted(set(categories[category])) if scene_id in scene_scores
        ]
        if not scene_ids:
            results.append(
                build_metric(
                    QA_ACCURACY_CATEGORY_MACRO,
                    value=None,
                    status="not_assessable",
                    scope="category",
                    grading_revision=grading_revision,
                    coverage=coverage_of(0, len(set(categories[category]))),
                    reasons=["CATEGORY_NOT_JUDGEABLE"],
                    evidence_refs=[f"category:{category}"],
                )
            )
            continue
        value = sum(scene_scores[scene_id] for scene_id in scene_ids) / len(scene_ids)
        category_values.append(value)
        results.append(
            build_metric(
                QA_ACCURACY_CATEGORY_MACRO,
                value=value,
                numerator=float(len(scene_ids)),
                denominator=float(len(set(categories[category]))),
                status="scored",
                scope="category",
                grading_revision=grading_revision,
                coverage=coverage_of(len(scene_ids), len(set(categories[category]))),
                evidence_refs=[f"category:{category}"],
            )
        )
    results.append(
        build_metric(
            QA_ACCURACY_CATEGORY_MACRO,
            value=None
            if not category_values
            else sum(category_values) / len(category_values),
            numerator=None if not category_values else float(len(category_values)),
            denominator=None if not category_values else float(len(categories)),
            status="scored" if category_values else "not_assessable",
            scope="dataset",
            grading_revision=grading_revision,
            coverage=coverage_of(len(category_values), len(categories)),
            evidence_refs=["category:all"],
        )
    )

    results.append(
        ratio_metric(
            STRICT_SCENARIO_PASS_RATE,
            numerator=strict_pass,
            denominator=strict_eligible,
            scope="dataset",
            grading_revision=grading_revision,
            reasons=["NO_JUDGEABLE_TRIAL"] if strict_eligible == 0 else [],
        )
    )
    results.append(
        ratio_metric(
            REPEAT_STABILITY,
            numerator=stable,
            denominator=stable_eligible,
            scope="dataset",
            grading_revision=grading_revision,
            reasons=["NO_COMPLETE_SCENE"] if stable_eligible == 0 else [],
        )
    )

    # 分类指标（偏好 / 更新 / 跨会话 / 重启）：对应标签下的题正确率。
    for tag, metric_id in CATEGORY_TAG_METRICS.items():
        tagged = [trial for trial in trials if tag in (trial.categories or [])]
        values: list[float] = []
        for trial in tagged:
            for grade in trial.probe_results:
                if grade.qa_correct is not None:
                    values.append(1.0 if grade.qa_correct else 0.0)
        results.append(
            ratio_metric(
                metric_id,
                numerator=None if not values else sum(values),
                denominator=None if not values else len(values),
                scope="dataset",
                grading_revision=grading_revision,
                reasons=["NO_TAGGED_PROBE"] if not values else [],
            )
        )
    return results


__all__ = [
    "BOOTSTRAP_VERSION",
    "CATEGORY_TAG_METRICS",
    "DEFAULT_CATEGORY",
    "FACT_MATCHING_VERSION",
    "METRIC_AGGREGATION_VERSION",
    "DeterministicFactResolver",
    "EquivalenceDecision",
    "EquivalenceVerdict",
    "FactEquivalenceResolver",
    "MatchOutcome",
    "MemoryProbeGrade",
    "MemoryProbeOutcome",
    "MemoryStateObservation",
    "MemoryTrialExecution",
    "MemoryTrialGrade",
    "NormalizedFact",
    "aggregate_memory_scores",
    "chinese_number_to_int",
    "default_grading_policy",
    "grade_memory_trial",
    "match_facts",
    "normalize_fact",
    "normalize_text",
]
