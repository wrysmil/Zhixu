"""LongMemEval 本地导入适配（契约 §1/§6，plan §8 Task 6 Step 4）。

这个模块只做**数据与协议适配**，不做评测执行，也不把公开基准的数据打进代码库：

- 只读**本地上传文件**：不接受远程 URL，不执行上传的评分脚本；
- 官方字段（question_id / 题型 / 时间 / answer / evidence_sessions / has_answer）
  全部保留在评估侧的私有对象里；
- **``has_answer`` 绝不出现在 Worker 可见的执行载荷里**——无答案题只通过 gold 的
  ``answerability``/``expected_absence`` 表达；
- 锁定 cleaned revision / hash 与原始 ID 清单；分层子集带 subset 标识；
- 官方二值 Judge 按题型建立**版本化内置适配**并记录协议 hash。

口径提醒：内置二值适配是本平台的**近似判定**，不是官方分数，也不冒充官方分数；
内部 F1 与官方指标分开报告。短答案题用「答案短语出现在回答里」的确定性近似，
结论可能与官方 Judge 不一致——对外只报本平台口径，官方分数以官方协议为准。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: 加载器版本：随来源元数据一起冻结。
LONGMEMEVAL_LOADER_VERSION = "longmemeval-loader/v1"
#: 内置二值 Judge 的协议版本（按题型分支的规则集）。
LONGMEMEVAL_JUDGE_PROTOCOL = "longmemeval-binary-judge/v1"
#: 内置判定指标 ID：与官方指标**分开**，不冒充官方分数。
LONGMEMEVAL_JUDGE_METRIC_ID = "longmemeval_binary_judge"

#: 公开数据里的时间都是 Asia/Shanghai 口径（spec §4.6）。
TIMEZONE = timezone(timedelta(hours=8))

#: 上传文件里可能出现的评分脚本字段：只记录、绝不执行。
_SCRIPT_FIELDS = ("evaluation_script", "judge_script", "scoring_script", "metric_script")

#: 按题型的内置适配规则。
#:
#: - ``answer-phrase/v1``：短答案题（单会话/多会话/时间推理）——模型通常用整句回答，
#:   归一化后**答案短语出现在回答里**即算命中（多值答案要求每段都出现）；
#: - ``contains-current/v1``：知识纠正题——回答里必须出现**当前**值；
#: - ``abstention-marker/v1``：信息不足题——命中拒答标记才算答对；
#: - ``exact-normalized/v1``：兜底——归一化后必须与标准答案完全一致。
JUDGE_ADAPTERS: dict[str, str] = {
    "single-session-user": "answer-phrase/v1",
    "single-session-assistant": "answer-phrase/v1",
    "single-session-preference": "answer-phrase/v1",
    "multi-session": "answer-phrase/v1",
    "knowledge-update": "contains-current/v1",
    "temporal-reasoning": "answer-phrase/v1",
    "abstention": "abstention-marker/v1",
}
#: 未知题型退回的默认适配（记录在诊断里，不假装知道官方口径）。
DEFAULT_JUDGE_ADAPTER = "exact-normalized/v1"

#: 无答案题的判定标记：命中即视为"正确承认信息不足"。
ABSTENTION_MARKERS: tuple[str, ...] = (
    "不知道",
    "没有提到",
    "未提及",
    "没有说过",
    "没有记录",
    "无法确定",
    "不能确定",
    "没有相关信息",
    "资料中没有",
    "信息不足",
    "not mentioned",
    "no information",
    "cannot determine",
    "unknown",
)

_PUNCTUATION = re.compile(r"[\s　,，。.、;；:：!！?？\"'“”‘’()（）\[\]【】<>《》\-_/\\]+")


class LongMemEvalFormatError(ValueError):
    """上传的 LongMemEval 文件不符合可解析的原格式。"""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


@dataclass(frozen=True)
class LongMemEvalSession:
    """一次原始会话（时间已解析成带时区的时间戳）。"""

    session_id: str
    at: datetime
    turns: tuple[tuple[str, str], ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "at": self.at.isoformat(),
            "turns": [{"role": role, "content": content} for role, content in self.turns],
        }


@dataclass(frozen=True)
class LongMemEvalItem:
    """一条原始题目（**评估侧私有**：含 has_answer 与标准答案）。"""

    question_id: str
    question_type: str
    question: str
    answer: str | None
    has_answer: bool
    evidence_session_ids: tuple[str, ...]
    sessions: tuple[LongMemEvalSession, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question_type": self.question_type,
            "question": self.question,
            "answer": self.answer,
            "has_answer": self.has_answer,
            "evidence_session_ids": list(self.evidence_session_ids),
            "sessions": [session.to_payload() for session in self.sessions],
        }


@dataclass(frozen=True)
class LongMemEvalVerdict:
    """内置二值判定结果（**不是**官方分数）。"""

    metric_id: str
    status: str  # correct | incorrect | not_assessable
    protocol_id: str
    protocol_hash: str
    official: bool = False
    reason: str = ""


@dataclass(frozen=True)
class LongMemEvalDataset:
    """一次本地导入的结果：场景、来源指纹与协议指纹。"""

    items: tuple[LongMemEvalItem, ...] = ()
    scenarios: tuple[dict[str, Any], ...] = ()
    #: 原始题目 ID 清单：整份上传文件的清单（子集只是视图，不裁剪清单）。
    original_ids: list[str] = field(default_factory=list)
    cleaned_revision: str = ""
    cleaned_sha256: str = ""
    uploaded_sha256: str = ""
    loader_version: str = LONGMEMEVAL_LOADER_VERSION
    judge_protocol: str = LONGMEMEVAL_JUDGE_PROTOCOL
    judge_protocol_hash: str = ""
    subset: str | None = None
    split: str = "development"
    diagnostics: tuple[dict[str, Any], ...] = ()
    #: 内置二值适配**不是**官方口径：报告里必须分开显示。
    official_score: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)

    def source_metadata(self) -> dict[str, Any]:
        """可直接交给集合导入器的来源元数据。"""
        return {
            "repo_url": "local://longmemeval-upload",
            "revision": self.cleaned_revision,
            "sha256": self.cleaned_sha256,
            "license": "unverified-see-uploader-terms",
            "original_ids": list(self.original_ids),
            "loader_version": self.loader_version,
            "grading_protocol": self.judge_protocol,
        }


# ---------------------------------------------------------------- 工具


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize(text: str) -> str:
    """归一化答案文本：NFKC + 去空白标点 + 小写。"""
    folded = unicodedata.normalize("NFKC", text or "").strip().casefold()
    return _PUNCTUATION.sub("", folded)


def _split_list(text: str) -> list[str]:
    parts = re.split(r"[,，;；]", text or "")
    return [_normalize(part) for part in parts if _normalize(part)]


def _safe_id(value: str, *, fallback: str) -> str:
    """把外部标识收敛成合法且稳定的短标识。"""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-")
    if not cleaned:
        cleaned = fallback
    return cleaned[:120]


def parse_timestamp(value: Any) -> datetime:
    """把公开数据里的多种时间写法解析成带时区的时间。

    公开数据常见 ``2023/05/20 (Sat) 02:21:38``、``2023-05-20 02:21:38`` 与 ISO 8601。
    解析不出来就报错——**不编造时间戳**。
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=TIMEZONE)
    text = str(value or "").strip()
    if not text:
        raise LongMemEvalFormatError("LONGMEMEVAL_TIMESTAMP_INVALID", "empty timestamp")
    cleaned = re.sub(r"\([^)]*\)", " ", text).strip()
    candidates = (
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S %Z",
    )
    for pattern in candidates:
        try:
            return datetime.strptime(cleaned, pattern).replace(tzinfo=TIMEZONE)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise LongMemEvalFormatError(
            "LONGMEMEVAL_TIMESTAMP_INVALID", f"unsupported timestamp: {text}"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=TIMEZONE)


# ---------------------------------------------------------------- 加载


def _first_present(raw: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in raw and raw[name] is not None:
            return raw[name]
    return None


def _extract_evidence_ids(raw: Mapping[str, Any]) -> tuple[str, ...]:
    """evidence_sessions 兼容三种写法：字符串列表 / 列表的列表 / 逗号分隔串。"""
    value = _first_present(
        raw, ("evidence_sessions", "evidence_session_ids", "answer_session_ids")
    )
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    collected: list[str] = []
    if isinstance(value, Sequence):
        for entry in value:
            if isinstance(entry, str):
                collected.append(entry)
            elif isinstance(entry, Sequence):
                collected.extend(str(item) for item in entry if str(item))
    return tuple(collected)


def _parse_sessions(raw: Mapping[str, Any]) -> tuple[LongMemEvalSession, ...]:
    value = _first_present(raw, ("haystack_sessions", "sessions", "haystack"))
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise LongMemEvalFormatError(
            "LONGMEMEVAL_SESSIONS_MISSING", "haystack_sessions is missing"
        )
    sessions: list[LongMemEvalSession] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            raise LongMemEvalFormatError(
                "LONGMEMEVAL_SESSION_INVALID", f"session #{index} is not an object"
            )
        session_id = str(entry.get("session_id") or entry.get("id") or f"sess-{index}")
        at = parse_timestamp(entry.get("date") or entry.get("at") or entry.get("timestamp"))
        turns: list[tuple[str, str]] = []
        raw_turns = entry.get("turns") or entry.get("messages") or []
        if not isinstance(raw_turns, Sequence):
            raise LongMemEvalFormatError(
                "LONGMEMEVAL_TURNS_INVALID", f"session {session_id} has invalid turns"
            )
        for turn in raw_turns:
            if not isinstance(turn, Mapping):
                continue
            role = str(turn.get("role") or "user").lower()
            content = str(turn.get("content") or turn.get("text") or "")
            if role not in ("user", "assistant"):
                # system/tool 角色不注入执行载荷（契约 §1）：丢弃而不是改写。
                continue
            if not content.strip():
                continue
            turns.append((role, content))
        sessions.append(LongMemEvalSession(session_id=session_id, at=at, turns=tuple(turns)))
    return tuple(sessions)


def _parse_item(raw: Mapping[str, Any], index: int) -> tuple[LongMemEvalItem, list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    question_id = str(raw.get("question_id") or raw.get("id") or "").strip()
    if not question_id:
        raise LongMemEvalFormatError(
            "LONGMEMEVAL_QUESTION_ID_MISSING", f"item #{index} has no question_id"
        )
    question_type = str(
        _first_present(raw, ("question_type", "type")) or "unknown"
    ).strip()
    question = str(raw.get("question") or raw.get("question_text") or "").strip()
    if not question:
        raise LongMemEvalFormatError(
            "LONGMEMEVAL_QUESTION_MISSING", f"item {question_id} has no question"
        )
    answer_value = _first_present(raw, ("answer", "answer_string"))
    answer = str(answer_value).strip() if isinstance(answer_value, str) else ""
    raw_has_answer = _first_present(raw, ("has_answer", "answerable"))
    if isinstance(raw_has_answer, bool):
        has_answer = raw_has_answer
    else:
        has_answer = bool(answer)
        diagnostics.append(
            {
                "code": "INFERRED_HAS_ANSWER",
                "question_id": question_id,
                "message": "has_answer is absent; inferred from the answer field",
            }
        )
    if not has_answer and answer:
        # 无答案题的标准答案不能进入任何场景或 gold：否则等于泄题。
        answer = ""
        diagnostics.append(
            {
                "code": "ANSWER_DROPPED_FOR_UNANSWERABLE",
                "question_id": question_id,
                "message": "the answer of an unanswerable item is dropped",
            }
        )
    for field_name in _SCRIPT_FIELDS:
        if field_name in raw:
            diagnostics.append(
                {
                    "code": "IGNORED_UPLOADED_SCRIPT",
                    "question_id": question_id,
                    "message": f"{field_name} is recorded but never executed",
                }
            )
    sessions = _parse_sessions(raw)
    evidence = _extract_evidence_ids(raw)
    return (
        LongMemEvalItem(
            question_id=question_id,
            question_type=question_type,
            question=question,
            answer=answer or None,
            has_answer=has_answer,
            evidence_session_ids=evidence,
            sessions=sessions,
        ),
        diagnostics,
    )


def _build_scenario(
    item: LongMemEvalItem, *, split: str, subset: str | None
) -> dict[str, Any]:
    """把一条原始题目变成 ``memory-scenario/v1`` 场景。

    ``has_answer`` **不进入**场景：它只体现在 gold 的 ``answerability`` 上。
    """
    scenario_id = f"lme-{_safe_id(item.question_id, fallback='item')}"
    events: list[dict[str, Any]] = []
    message_ids: dict[str, list[str]] = {}
    seen_sessions: set[str] = set()
    for index, session in enumerate(item.sessions):
        event_id = session.session_id
        if event_id in seen_sessions or len(event_id) > 120:
            event_id = f"sess-{index:03d}"
        seen_sessions.add(event_id)
        messages: list[dict[str, str]] = []
        ids: list[str] = []
        for turn_index, (role, content) in enumerate(session.turns):
            message_id = f"m-{index:03d}-{turn_index:03d}"
            ids.append(message_id)
            messages.append({"id": message_id, "role": role, "content": content})
        message_ids[event_id] = ids
        events.append(
            {
                "type": "session",
                "id": event_id,
                "at": session.at.isoformat(),
                "messages": messages,
            }
        )
        events.append({"type": "settle", "after_session": event_id})

    last_at = max((session.at for session in item.sessions), default=None)
    probe_at = (last_at + timedelta(days=1)) if last_at else datetime.now(TIMEZONE)

    evidence_ids: list[str] = []
    for session_id in item.evidence_session_ids:
        evidence_ids.extend(message_ids.get(session_id, []))
    if not evidence_ids:
        # 没有显式证据会话时，退回全部原始会话的消息：证据单元就是会话。
        evidence_ids = [mid for ids in message_ids.values() for mid in ids]

    tags = [
        f"longmemeval-{_safe_id(item.question_type, fallback='unknown')}"[:40],
        "external-benchmark",
    ]
    if subset:
        tags.append(f"subset:{_safe_id(subset, fallback='subset')}"[:40])

    if item.has_answer and item.answer:
        probe_gold: dict[str, Any] = {
            "required_facts": [
                {
                    "key": "answer",
                    "value": item.answer,
                    "evidence_message_ids": evidence_ids,
                }
            ],
            "forbidden_current_values": [],
            "answerability": "answerable",
            "expected_absence": False,
        }
    else:
        probe_gold = {
            "required_facts": [],
            "forbidden_current_values": [],
            "answerability": "unanswerable",
            "expected_absence": True,
        }

    return {
        "schema_version": "memory-scenario/v1",
        "scenario_id": scenario_id,
        "revision": 1,
        "split": split,
        "template_family": f"longmemeval/{_safe_id(item.question_type, fallback='unknown')}",
        "cluster_id": f"longmemeval/{_safe_id(item.question_id, fallback='item')}",
        "tags": tags,
        "scope": {
            "subject_id": f"synthetic-lme-{_safe_id(item.question_id, fallback='item')}",
            "workspace_id": "synthetic-lme-w01",
        },
        "events": events,
        "probes": [
            {
                "id": "q1",
                "question": item.question,
                "at": probe_at.isoformat(),
                "fresh_session": True,
            }
        ],
        "gold": {"annotation_scope": "targeted", "q1": probe_gold},
    }


def judge_protocol_hash() -> str:
    """内置二值 Judge 的协议指纹：规则集变了，hash 必须变。"""
    payload = {
        "protocol_id": LONGMEMEVAL_JUDGE_PROTOCOL,
        "metric_id": LONGMEMEVAL_JUDGE_METRIC_ID,
        "adapters": dict(sorted(JUDGE_ADAPTERS.items())),
        "default_adapter": DEFAULT_JUDGE_ADAPTER,
        "abstention_markers": list(ABSTENTION_MARKERS),
        "normalization": "NFKC + casefold + punctuation/whitespace strip",
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def load_longmemeval(
    path: str | Path,
    *,
    split: str = "development",
    subset: str | None = None,
) -> LongMemEvalDataset:
    """读取**本地上传**的 LongMemEval 原格式文件并转成场景。

    - 远程 URL 一律拒绝：只读本地上传文件；
    - 上传文件里的评分脚本只记录不执行；
    - ``has_answer`` 与标准答案留在评估侧私有对象里，不进入场景。
    """
    if isinstance(path, str) and path.lower().startswith(("http://", "https://")):
        raise LongMemEvalFormatError(
            "LONGMEMEVAL_REMOTE_SOURCE_REJECTED", "only a local upload file is accepted"
        )
    source = Path(path)
    if not source.is_file():
        raise LongMemEvalFormatError("LONGMEMEVAL_FILE_NOT_FOUND", source.name)

    raw_bytes = source.read_bytes()
    uploaded_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LongMemEvalFormatError(
            "LONGMEMEVAL_JSON_INVALID", "the uploaded file is not valid utf-8 json"
        ) from exc
    if isinstance(payload, Mapping):
        payload = _first_present(payload, ("items", "data", "questions")) or []
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise LongMemEvalFormatError(
            "LONGMEMEVAL_JSON_INVALID", "the uploaded file is not a list of items"
        )

    items: list[LongMemEvalItem] = []
    diagnostics: list[dict[str, Any]] = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, Mapping):
            raise LongMemEvalFormatError(
                "LONGMEMEVAL_ITEM_INVALID", f"item #{index} is not an object"
            )
        item, item_diagnostics = _parse_item(raw, index)
        items.append(item)
        diagnostics.extend(item_diagnostics)

    original_ids = [item.question_id for item in items]
    cleaned_payload = [item.to_payload() for item in items]
    cleaned_sha256 = hashlib.sha256(_canonical(cleaned_payload).encode("utf-8")).hexdigest()
    cleaned_revision = f"longmemeval-cleaned/{cleaned_sha256[:16]}"

    scenarios = tuple(
        _build_scenario(item, split=split, subset=subset) for item in items
    )
    return LongMemEvalDataset(
        items=tuple(items),
        scenarios=scenarios,
        original_ids=original_ids,
        cleaned_revision=cleaned_revision,
        cleaned_sha256=cleaned_sha256,
        uploaded_sha256=uploaded_sha256,
        judge_protocol_hash=judge_protocol_hash(),
        subset=subset,
        split=split,
        diagnostics=tuple(diagnostics),
    )


def select_stratified_subset(
    dataset: LongMemEvalDataset, *, per_type: int = 1
) -> LongMemEvalDataset:
    """按题型分层取一个固定子集（确定性：同题型内按 question_id 排序取前 N 个）。

    原始 ID 清单仍然是整份文件的：子集是**视图**，不是另一份数据。
    """
    if per_type < 1:
        raise ValueError("LONGMEMEVAL_SUBSET_SIZE_INVALID")
    buckets: dict[str, list[LongMemEvalItem]] = {}
    for item in dataset.items:
        buckets.setdefault(item.question_type, []).append(item)
    selected: list[LongMemEvalItem] = []
    for question_type in sorted(buckets):
        ordered = sorted(buckets[question_type], key=lambda item: item.question_id)
        selected.extend(ordered[:per_type])
    label = f"stratified-{per_type}-per-type"
    scenarios = tuple(
        _build_scenario(item, split=dataset.split, subset=label) for item in selected
    )
    return replace(dataset, items=tuple(selected), scenarios=scenarios, subset=label)


# ---------------------------------------------------------------- 二值 Judge


def _adapter_for(question_type: str) -> str:
    return JUDGE_ADAPTERS.get(question_type, DEFAULT_JUDGE_ADAPTER)


def judge_longmemeval_answer(item: LongMemEvalItem, prediction: str) -> LongMemEvalVerdict:
    """内置二值判定：按题型走版本化适配规则。

    这是**平台的近似判定**，``official`` 恒为 ``False``：报告里必须与官方分数分开。
    """
    protocol_hash = judge_protocol_hash()
    adapter = _adapter_for(item.question_type)
    text = (prediction or "").strip()

    if not item.has_answer:
        if not text:
            return LongMemEvalVerdict(
                metric_id=LONGMEMEVAL_JUDGE_METRIC_ID,
                status="not_assessable",
                protocol_id=LONGMEMEVAL_JUDGE_PROTOCOL,
                protocol_hash=protocol_hash,
                reason="empty prediction for an unanswerable item",
            )
        lowered = unicodedata.normalize("NFKC", text).casefold()
        if any(marker.casefold() in lowered for marker in ABSTENTION_MARKERS):
            return LongMemEvalVerdict(
                metric_id=LONGMEMEVAL_JUDGE_METRIC_ID,
                status="correct",
                protocol_id=LONGMEMEVAL_JUDGE_PROTOCOL,
                protocol_hash=protocol_hash,
                reason="abstention marker matched",
            )
        return LongMemEvalVerdict(
            metric_id=LONGMEMEVAL_JUDGE_METRIC_ID,
            status="incorrect",
            protocol_id=LONGMEMEVAL_JUDGE_PROTOCOL,
            protocol_hash=protocol_hash,
            reason="the model asserted an answer for an unanswerable item",
        )

    gold = item.answer or ""
    if not gold:
        return LongMemEvalVerdict(
            metric_id=LONGMEMEVAL_JUDGE_METRIC_ID,
            status="not_assessable",
            protocol_id=LONGMEMEVAL_JUDGE_PROTOCOL,
            protocol_hash=protocol_hash,
            reason="the item has_answer is true but no answer is present",
        )
    if not text:
        return LongMemEvalVerdict(
            metric_id=LONGMEMEVAL_JUDGE_METRIC_ID,
            status="incorrect",
            protocol_id=LONGMEMEVAL_JUDGE_PROTOCOL,
            protocol_hash=protocol_hash,
            reason="empty prediction for an answerable item",
        )

    if adapter == "contains-current/v1":
        ok = _normalize(gold) in _normalize(text)
        reason = "current value contained" if ok else "current value missing"
    elif adapter == "answer-phrase/v1":
        # 短答案题：模型多半答一整句，答案短语出现在回答里即算命中。
        parts = _split_list(gold)
        normalized_text = _normalize(text)
        ok = bool(parts) and all(part in normalized_text for part in parts)
        reason = "answer phrase contained" if ok else "answer phrase missing"
    elif adapter == "abstention-marker/v1":
        lowered = unicodedata.normalize("NFKC", text).casefold()
        ok = any(marker.casefold() in lowered for marker in ABSTENTION_MARKERS)
        reason = "abstention marker matched" if ok else "abstention marker missing"
    else:
        if _normalize(gold) == _normalize(text):
            ok = True
        else:
            gold_parts = _split_list(gold)
            ok = bool(gold_parts) and set(gold_parts) == set(_split_list(text))
        reason = "normalized match" if ok else "normalized mismatch"
    return LongMemEvalVerdict(
        metric_id=LONGMEMEVAL_JUDGE_METRIC_ID,
        status="correct" if ok else "incorrect",
        protocol_id=LONGMEMEVAL_JUDGE_PROTOCOL,
        protocol_hash=protocol_hash,
        reason=reason,
    )


__all__ = [
    "ABSTENTION_MARKERS",
    "DEFAULT_JUDGE_ADAPTER",
    "JUDGE_ADAPTERS",
    "LONGMEMEVAL_JUDGE_METRIC_ID",
    "LONGMEMEVAL_JUDGE_PROTOCOL",
    "LONGMEMEVAL_LOADER_VERSION",
    "LongMemEvalDataset",
    "LongMemEvalFormatError",
    "LongMemEvalItem",
    "LongMemEvalSession",
    "LongMemEvalVerdict",
    "judge_longmemeval_answer",
    "judge_protocol_hash",
    "load_longmemeval",
    "parse_timestamp",
    "select_stratified_subset",
]
