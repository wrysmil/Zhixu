"""Phase 2 记忆提取器：从 session.messages 提取 memories / episodes / scratchpad。

设计依据：`docs/记忆系统/plan/阶段二设计_记忆提取.md` §3.1（提取流水线）、
§4（提示词）、§5.2-5.4（防污染）、§7.4（类签名）、§8.1-8.5（数据契约）。

流水线四阶段：
    阶段1 ``_system_extract``   纯结构化解析（ActionNode / 规则信号 / scratchpad 快照）
    阶段2 ``_llm_extract``      并发两路 LLM（语义 + 情节），失败隔离
    阶段3 ``_apply_filters``    候选合法性（任务产物 / AI 自答 / 合法 type / 批内精确去重）
    阶段4 ``_write_extraction`` 调用 :mod:`nanobot.memory.writer` 按写入决策表落库，
                                episodes 与 derived 关联在同一调用方事务内完成

写入决策（幂等 / 纠正 / 冲突 / 多值 / 遗忘）只在 ``writer.apply_memory_changes``
一处判断；本模块不再持有相似度丢弃或否定词冲突终判（D08）。
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import math
import re
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast
from uuid import uuid4

from loguru import logger

from nanobot.memory.clock import MemoryClock
from nanobot.memory.database import (
    ExtractionBatchRow,
    MemoryDatabase,
    find_extraction_batch_by_bounds,
    get_extraction_batch,
    insert_extraction_batch,
    list_incomplete_extraction_batches,
    update_extraction_batch,
)
from nanobot.memory.filters import (
    compute_content_hash,
    is_ai_self_talk,
    is_task_artifact,
)
from nanobot.memory.llm_error import response_error
from nanobot.memory.models import (
    BatchReceipt,
    Episode,
    EpisodeOutcome,
    EpisodeSource,
    ExtractionRequest,
    Memory,
    MemoryPriority,
    MemoryType,
    ScratchpadEntry,
)
from nanobot.memory.profile_extractor import (
    ProfileExtractionResult,
    ProfileExtractor,
    ProfileItem,
)
from nanobot.memory.prompts import (
    EPISODE_EXTRACTION_PROMPT,
    SEMANTIC_EXTRACTION_PROMPT,
)
from nanobot.memory.provenance import extraction_provenance, session_memory_workspace_id
from nanobot.memory.repository import (
    add_episode,
    get_extraction_seq,
    get_scratchpad,
    reset_extraction_state,
    set_extraction_seq,
    update_memory_source_episode,
)
from nanobot.memory.vector.indexer import index_memory_best_effort
from nanobot.memory.writer import MemoryChange, apply_memory_changes
from nanobot.session.labels import session_label, session_label_suffix

if TYPE_CHECKING:
    from nanobot.session.manager import Session
    from nanobot.utils.llm_runtime import LLMRuntime


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 规则信号关键词（Quick Facts 正则，plan §3.1 阶段1 / §10 Task 9）
_RULE_SIGNAL_RE = re.compile(r"必须|每次|总是|一律|务必|不要|禁止|记住|以后都|永远")

# Quick Facts 候选（规则信号）类型：统一落为 RULE
_QUICK_FACT_TYPE = MemoryType.RULE.value

# 句子切分（用于抽取命中的规则片段）
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?\n；;]+")

# priority 兜底的时间关键词（plan §8.2）
_SHORT_TERM_KEYWORDS = ("今天", "这周", "本月", "暂时", "先")

# 合法枚举值
_VALID_TYPES = frozenset(t.value for t in MemoryType)
_VALID_PRIORITIES = frozenset(p.value for p in MemoryPriority)

# FIX-3 Sec-M-1: ActionNode 输入/输出截断阈值（防凭据外发注入攻击）
ACTION_NODE_INPUT_MAX_CHARS = 512
ACTION_NODE_OUTPUT_MAX_CHARS = 2048

# FIX-3 Sec-M-1: 凭据/密钥/令牌脱敏模式；命中即替换为占位符，
# 然后再按上面的 OUTPUT_MAX_CHARS 截断。
_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # ``key=value`` / ``key: value`` / ``"key": "value"``（JSON 风格）均匹配
    (re.compile(
        r"(?i)(?P<k>api[_-]?key|secret|password|token)\s*[\"']?\s*[=:]\s*[\"']?\s*\S+"
    ), "<redacted>"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"), "Bearer <redacted>"),
    (re.compile(
        r"(?i)(aws[_-]?access[_-]?key[_-]?id|aws[_-]?secret)\s*[\"']?\s*[=:]\s*[\"']?\s*\S+"
    ), "<redacted>"),
]

# WU-1 head+tail 双向保留（plan §3 WU-1 / 参考 OpenAkita smart_truncate）：
# OpenAkita 用 65% head，我们调成 40% head：开篇任务定义 + 结尾最新进展，
# 对 LLM 抽取语义/情节记忆的覆盖率更高。head_ratio + tail_ratio 之和 < 1.0
# （给 marker 留 ~15% 空间）。
_TRANSCRIPT_HEAD_RATIO: float = 0.4
_TRANSCRIPT_TAIL_RATIO: float = 0.45
_TRANSCRIPT_TRUNCATE_MARKER = "\n...[中段已截断,完整对话已被压缩或超长]...\n"

# FIX-4 Sec-M-2: 数值与字符串字段值域校验（防 LLM 输出 NaN/Inf / 巨型字符串）
CONTENT_MAX_CHARS = 8192
TAGS_MAX_ITEMS = 32
SUBJECT_MAX_CHARS = 256
PREDICATE_MAX_CHARS = 256
DEFAULT_IMPORTANCE = 0.7

# source 字符串 → EpisodeSource 映射（FIX-2 Sec-C-β：``deletion`` 现在有专属枚举，
# 不再折叠到 SESSION_END；保留 provenance 用于审计 / 调优）
_SOURCE_MAP: dict[str, EpisodeSource] = {
    "manual": EpisodeSource.MANUAL,
    "session_end": EpisodeSource.SESSION_END,
    "context_compress": EpisodeSource.CONTEXT_COMPRESS,
    "topic_change": EpisodeSource.TOPIC_CHANGE,
    "deletion": EpisodeSource.DELETION,
    "daily_consolidation": EpisodeSource.DAILY_CONSOLIDATION,
    "idle": EpisodeSource.IDLE,
}

# ---------------------------------------------------------------------------
# 批次协议（契约 §2）
# ---------------------------------------------------------------------------

#: batch_id 摘要取前 32 个十六进制字符（128 bit，碰撞概率可忽略，日志可读）。
_BATCH_ID_DIGEST_CHARS = 32

#: 批次 claim 的租约时长（秒）。模型两路并发受 ``LLM_TIMEOUT`` 约束，120s 足够；
#: 过期 owner 的提交会被 ``require_owner`` 条件拒绝。
BATCH_LEASE_SECONDS = 120.0

#: 从未持久化的会话（例如单测里直接构造的 ``Session``）使用临时 generation：
#: 编号规则与保存路径一致，但不会写盘，也不会与持久 generation（32 位 hex）碰撞。
_EPHEMERAL_GENERATION_PREFIX = "unsaved-"

# 结构化错误码（BatchReceipt.error_code）。按「语义」而非自由文本记录，
# 调用方据此判断重试价值；模型错误文本只进 error_detail / 日志。
ERROR_SESSION_UNAVAILABLE = "SESSION_UNAVAILABLE"
ERROR_GENERATION_MISMATCH = "GENERATION_MISMATCH"
ERROR_COORDINATE_INVALID = "MEMORY_COORDINATE_INVALID"
ERROR_EVIDENCE_MISSING = "EVIDENCE_MISSING"
ERROR_BATCH_PAYLOAD_MISMATCH = "BATCH_PAYLOAD_MISMATCH"
ERROR_BATCH_CLAIMED = "BATCH_CLAIMED"
ERROR_BATCH_CLAIM_LOST = "BATCH_CLAIM_LOST"
ERROR_SEMANTIC_TRACK_FAILED = "SEMANTIC_TRACK_FAILED"
ERROR_EPISODE_TRACK_FAILED = "EPISODE_TRACK_FAILED"
ERROR_PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
ERROR_UNEXPECTED = "UNEXPECTED_ERROR"

class _BatchError(RuntimeError):
    """批次协议中的结构化失败（携带错误码，供回执直接使用）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


#: 进程内消费键串行锁。值为 ``(事件循环 id, asyncio.Lock)``：同一 session_key
#: 的提取可能由不同的 extractor 实例（每轮工厂）发起，锁必须跨实例共享；
#: 跨事件循环（测试里的多个 ``asyncio.run``）则重建，避免复用已绑定旧循环的锁。
_CONSUMPTION_LOCKS: dict[tuple[str, str, str], tuple[int, asyncio.Lock]] = {}


def _consumption_lock(key: tuple[str, str, str]) -> asyncio.Lock:
    """取消费键的进程内串行锁（同一事件循环内复用）。"""
    loop_id = id(asyncio.get_running_loop())
    entry = _CONSUMPTION_LOCKS.get(key)
    if entry is None or entry[0] != loop_id:
        lock = asyncio.Lock()
        _CONSUMPTION_LOCKS[key] = (loop_id, lock)
        return lock
    return entry[1]


#: 默认会话快照读取器缓存（按 memory 库工作区）：``SessionManager`` 构造会做
#: 目录迁移与文件锁，不能每次提取都重建；同一进程内按工作区复用一次。
_SESSION_LOADERS: dict[str, Callable[[str], "Session | None"] | None] = {}


def _positive_seq(message: dict[str, Any]) -> int | None:
    """读取消息的 ``memory_seq``；缺失或非法返回 ``None``。"""
    raw = message.get("memory_seq")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None
    return raw


def _stable_batch_id(
    workspace_id: str,
    session_key: str,
    generation: str,
    start_seq: int,
    end_seq: int,
) -> str:
    """从消费键 + 固定起止边界计算稳定 batch_id（重试不产生新 id）。"""
    raw = "\x1f".join(
        (
            workspace_id,
            session_key,
            generation,
            str(int(start_seq)),
            str(int(end_seq)),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_BATCH_ID_DIGEST_CHARS]


def _evidence_digest(messages: Sequence[tuple[int, dict[str, Any]]]) -> str:
    """批次证据摘要：同一 ID 必须对应同一份原始消息切片。

    只覆盖身份与载荷字段（seq / role / content / tool 调用 / 工具结果坐标），
    不含「本轮追加的 assistant 回复」之类的外围字段，避免同一切片因无关字段
    变化而被判成另一个载荷。
    """
    digest = hashlib.sha256()
    for seq, message in messages:
        canonical = json.dumps(
            {
                "seq": seq,
                "role": message.get("role"),
                "content": message.get("content"),
                "tool_calls": message.get("tool_calls"),
                "tool_call_id": message.get("tool_call_id"),
                "name": message.get("name"),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        digest.update(canonical.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 数据类（plan §7.4 / §8.4）
# ---------------------------------------------------------------------------


@dataclass
class ActionNode:
    """系统提取的单个工具调用节点。"""

    # FIX-3 Sec-M-1: 输入/输出大小上限（脱敏后截断）
    INPUT_MAX_CHARS: ClassVar[int] = ACTION_NODE_INPUT_MAX_CHARS
    OUTPUT_MAX_CHARS: ClassVar[int] = ACTION_NODE_OUTPUT_MAX_CHARS

    tool: str
    input: str = ""
    output: str = ""
    success: bool = True
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "input": self.input,
            "output": self.output,
            "success": self.success,
            "duration_ms": self.duration_ms,
        }


@dataclass
class SystemExtractionResult:
    """阶段1 系统提取结果（无需 LLM）。"""

    action_nodes: list[ActionNode] = field(default_factory=list)
    rule_signals: list[str] = field(default_factory=list)
    scratchpad_snapshot: ScratchpadEntry | None = None


@dataclass
class LLMMemoryItem:
    """语义 LLM 输出的单条记忆候选（memories 与 experiences 合并后同构）。"""

    content: str
    type: str
    priority: str | None = None
    importance: float = 0.7
    subject: str = ""
    predicate: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class LLMEpisodeItem:
    """情节 LLM 输出的 Episode 候选。"""

    summary: str = ""
    goal: str = ""
    outcome: str | None = None
    entities: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    importance: float = 0.7


@dataclass
class LLMExtractionResult:
    """阶段2 LLM 提取结果。

    ``action_nodes`` 承载阶段1 的系统提取结果，用于按 §7.4 的
    ``_apply_filters(llm_result)`` 签名把 ActionNode 传递到阶段3/4。

    引用评分（``citation_scores`` / ``cited_memory_ids``）已随 D11 删除：排序不再
    消费 ``access_count``，usefulness 写回没有下游消费者（spec §6.3）。
    """

    memories: list[LLMMemoryItem] = field(default_factory=list)
    episode: LLMEpisodeItem | None = None
    failed_tracks: list[str] = field(default_factory=list)
    action_nodes: list[ActionNode] = field(default_factory=list)
    #: 批次协议（契约 §2.6）：本次实际尝试的轨道，以及各轨失败原因。
    #: ``semantic_payload`` / ``episode_payload`` 是可直接落 checkpoint 的
    #: 序列化输出；``episode_payload=None`` 且该轨无失败表示「有效空输出」。
    attempted_tracks: list[str] = field(default_factory=list)
    track_errors: dict[str, str] = field(default_factory=dict)
    semantic_payload: dict[str, Any] | None = None
    episode_payload: dict[str, Any] | None = None


@dataclass
class FilteredExtractionResult:
    """阶段3 过滤后的结果。"""

    memories: list[LLMMemoryItem] = field(default_factory=list)
    episode: LLMEpisodeItem | None = None
    action_nodes: list[ActionNode] = field(default_factory=list)
    #: 各丢弃原因的条数（``skipped`` 只有总数，排障时看不出丢在哪一步）。
    skipped_reasons: dict[str, int] = field(default_factory=dict)


@dataclass
class PersistenceResult:
    """阶段4 写入结果。"""

    memory_ids: list[str] = field(default_factory=list)
    episode_ids: list[str] = field(default_factory=list)
    #: 本次真正新增的行数；幂等命中的既有 id 会计入 ``memory_ids`` 但不计入这里。
    created_count: int = 0


@dataclass
class ExtractionResult:
    """``extract_session`` 的对外返回。"""

    memory_ids: list[str] = field(default_factory=list)
    episode_ids: list[str] = field(default_factory=list)
    skipped: int = 0
    failed_tracks: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 纯函数工具
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _content_text(content: Any) -> str:
    """把消息 content（str / content-block list / 其他）归一化为纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return str(content)


def _looks_like_error(output: str) -> bool:
    low = output.lower()
    return "error" in low or "traceback" in low


def _resolve_action_success(output: str, has_result: bool) -> bool:
    """判定 ActionNode 是否执行成功。

    Args:
        output: 工具返回的 output 文本（已归一化）。
        has_result: 是否在 tool 消息中找到了对应 ``tool_call_id`` 的结果。

    Returns:
        True 表示成功,False 表示失败。

    孤儿语义：当 assistant 发出了 tool_call 但没有对应的 tool 结果消息
    时,视为执行失败（``has_result=False``）,避免把"调用了但没收到结果"
    的工具调用当作"成功调用"写入 episode。
    """
    if not has_result:
        return False
    return not _looks_like_error(output)


def _extract_tool_call(call: Any) -> tuple[str, str, str]:
    """探测 tool_call 结构，返回 ``(tool_name, arguments_str, call_id)``。"""
    if not isinstance(call, dict):
        return "", "", ""
    func = call.get("function")
    func_map: dict[str, Any] = func if isinstance(func, dict) else {}
    name = call.get("name") or func_map.get("name") or ""
    args = call.get("arguments")
    if args is None:
        args = func_map.get("arguments")
    if isinstance(args, str):
        args_str = args
    elif args is None:
        args_str = ""
    else:
        args_str = json.dumps(args, ensure_ascii=False)
    call_id = call.get("id") or ""
    return str(name), args_str, str(call_id)


def _redact(text: str) -> str:
    """FIX-3 Sec-M-1：用占位符替换常见凭据 / 密钥 / 令牌模式。"""
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _truncate(text: str, limit: int) -> str:
    """FIX-3 Sec-M-1：按 ``limit`` 截断字符串并附加 truncated 标记。"""
    if len(text) <= limit:
        return text
    extra = len(text) - limit
    return text[:limit] + f"...[truncated {extra} chars]"


def _collect_action_nodes(messages: list[dict[str, Any]]) -> list[ActionNode]:
    """扫描 assistant 消息中的 tool_calls，结合 tool 结果消息生成 ActionNode。

    FIX-3 Sec-M-1：构造前对 input/output 先脱敏，再按 INPUT_MAX_CHARS /
    OUTPUT_MAX_CHARS 截断，防止凭据随 action_nodes 持久化进入 episode。
    """
    results: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        tid = msg.get("tool_call_id")
        if tid:
            results[str(tid)] = _content_text(msg.get("content"))

    nodes: list[ActionNode] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        calls = msg.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            name, args, call_id = _extract_tool_call(call)
            if not name:
                continue
            has_result = call_id in results
            output = results.get(call_id, "")
            nodes.append(
                ActionNode(
                    tool=name,
                    input=_truncate(_redact(args), ActionNode.INPUT_MAX_CHARS),
                    output=_truncate(_redact(output), ActionNode.OUTPUT_MAX_CHARS),
                    success=_resolve_action_success(output, has_result),
                )
            )
    return nodes


def _collect_rule_signals(messages: list[dict[str, Any]]) -> list[str]:
    """扫描 user 消息，收集命中规则关键词的句子片段。"""
    signals: list[str] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        text = _content_text(msg.get("content")).strip()
        if not text:
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(text):
            fragment = sentence.strip()
            if fragment and _RULE_SIGNAL_RE.search(fragment):
                signals.append(fragment)
    return signals


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """容忍 ```json 围栏与前后解释文字地解析首个 JSON 对象；失败返回 None。"""
    cleaned = _strip_code_fence(text)
    for candidate in _json_candidates(cleaned):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    first_newline = cleaned.find("\n")
    if first_newline != -1:
        cleaned = cleaned[first_newline + 1:]
    if cleaned.rstrip().endswith("```"):
        cleaned = cleaned.rstrip()[:-3]
    return cleaned.strip()


def _json_candidates(text: str) -> list[str]:
    candidates = [text]
    balanced = _first_balanced_object(text)
    if balanced is not None and balanced != text:
        candidates.append(balanced)
    return candidates


def _first_balanced_object(text: str) -> str | None:
    """返回首个花括号配平的 ``{...}`` 子串。

    逐个字符扫描并跳过字符串字面量，因此不会像贪婪正则那样把「JSON 后面
    的解释文字」或「第二个对象」一并吞进来。
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _coerce_memory_item(raw: Any) -> LLMMemoryItem | None:
    if not isinstance(raw, dict):
        return None
    # FIX-4 Sec-M-2：content 长度上限 + 截断标记（防 LLM 输出巨型字符串）
    raw_content = str(raw.get("content") or "").strip()
    if not raw_content:
        return None
    if len(raw_content) > CONTENT_MAX_CHARS:
        content = raw_content[:CONTENT_MAX_CHARS] + "...[truncated]"
    else:
        content = raw_content

    priority_raw = raw.get("priority")
    priority = str(priority_raw).strip() if priority_raw is not None else None

    # FIX-4 Sec-M-2：importance 必须为有限数并位于 [0,1]；否则回落默认值
    importance_raw = raw.get("importance", DEFAULT_IMPORTANCE)
    try:
        importance_value = float(importance_raw)
    except (TypeError, ValueError):
        importance_value = DEFAULT_IMPORTANCE
    if not math.isfinite(importance_value) or not (0.0 <= importance_value <= 1.0):
        importance_value = DEFAULT_IMPORTANCE
    importance = min(max(importance_value, 0.0), 1.0)

    tags_raw = raw.get("tags")
    if isinstance(tags_raw, list):
        tags_all = [str(t) for t in tags_raw]
    else:
        tags_all = []
    # FIX-4 Sec-M-2：tags 条数上限（防 LLM 输出巨型标签列表）
    tags = tags_all[:TAGS_MAX_ITEMS]

    # FIX-4 Sec-M-2：subject / predicate 长度上限
    subject_raw = str(raw.get("subject") or "").strip()
    predicate_raw = str(raw.get("predicate") or "").strip()
    subject = subject_raw[:SUBJECT_MAX_CHARS]
    predicate = predicate_raw[:PREDICATE_MAX_CHARS]

    return LLMMemoryItem(
        content=content,
        type=str(raw.get("type") or "").strip(),
        priority=priority or None,
        importance=importance,
        subject=subject,
        predicate=predicate,
        tags=tags,
    )


def _memory_item_to_dict(item: LLMMemoryItem) -> dict[str, Any]:
    """把过滤前候选序列化进批次 checkpoint。"""
    return {
        "content": item.content,
        "type": item.type,
        "priority": item.priority,
        "importance": item.importance,
        "subject": item.subject,
        "predicate": item.predicate,
        "tags": list(item.tags),
    }


def _memory_item_from_dict(raw: Any) -> LLMMemoryItem | None:
    """从批次 checkpoint 的序列化 dict 还原 ``LLMMemoryItem``（过滤前候选）。"""
    if not isinstance(raw, dict):
        return None
    return _coerce_memory_item(raw)


def _episode_item_to_dict(item: LLMEpisodeItem) -> dict[str, Any]:
    """把情节候选序列化进批次 checkpoint（与 ``_coerce_episode_item`` 对称）。"""
    return {
        "summary": item.summary,
        "goal": item.goal,
        "outcome": item.outcome,
        "entities": list(item.entities),
        "tools_used": list(item.tools_used),
        "importance": item.importance,
    }


async def _skipped_track() -> None:
    """批次只重跑部分轨道时占位，保持 ``asyncio.gather`` 的两槽位形状。"""
    return None


def _coerce_episode_item(raw: dict[str, Any]) -> LLMEpisodeItem:
    outcome_raw = raw.get("outcome")
    entities_raw = raw.get("entities")
    tools_raw = raw.get("tools_used")
    try:
        importance = float(raw.get("importance", 0.7))
    except (TypeError, ValueError):
        importance = 0.7
    return LLMEpisodeItem(
        summary=str(raw.get("summary") or "").strip(),
        goal=str(raw.get("goal") or "").strip(),
        outcome=str(outcome_raw).strip() if outcome_raw is not None else None,
        entities=[str(e) for e in entities_raw] if isinstance(entities_raw, list) else [],
        tools_used=[str(t) for t in tools_raw] if isinstance(tools_raw, list) else [],
        importance=importance,
    )


def _profile_item_to_llm(item: ProfileItem) -> LLMMemoryItem:
    """S3 Track 1 的 ``ProfileItem`` 适配成流水线内部的 ``LLMMemoryItem``。

    适配层刻意保持 ``_apply_filters`` / ``_persist`` 的输入类型不变，
    避免改动阶段3/4。``type`` 沿用大写原样透传——下游统一 ``.lower()``。
    """
    return LLMMemoryItem(
        content=item.content,
        type=item.type,
        priority=item.priority,
        importance=item.importance,
        subject=item.subject,
        predicate=item.predicate,
        tags=list(item.tags),
    )


def _normalize_outcome(raw: str | None) -> EpisodeOutcome:
    """归一化 episode outcome（plan §8.4.1）。非法值降级 completed 并 warning。"""
    if raw is None:
        return EpisodeOutcome.COMPLETED
    mapping = {"success": "completed"}
    value = mapping.get(raw, raw)
    try:
        return EpisodeOutcome(value)
    except ValueError:
        logger.warning("invalid episode outcome {!r}, fallback to completed", raw)
        return EpisodeOutcome.COMPLETED


def _resolve_priority(item: LLMMemoryItem) -> MemoryPriority:
    """按 §8.2 解析 priority；缺失或（兜底）非法时按规则推断。

    注意：非法值在阶段3 ``_apply_filters`` 已被跳过，此处仅做最终兜底。
    """
    raw = (item.priority or "").strip().lower()
    if raw in _VALID_PRIORITIES:
        return MemoryPriority(raw)
    if raw:
        logger.warning("invalid memory priority {!r}, applying fallback rules", item.priority)
    if item.type.strip().lower() == MemoryType.ERROR.value:
        return MemoryPriority.LONG_TERM
    if any(keyword in item.content for keyword in _SHORT_TERM_KEYWORDS):
        return MemoryPriority.SHORT_TERM
    return MemoryPriority.LONG_TERM


def _render_scratchpad(snapshot: ScratchpadEntry | None) -> str:
    """把 scratchpad 快照渲染为 JSON 文本，供 LLM 调用输入（§3.1）。"""
    if snapshot is None:
        return "{}"
    return json.dumps(
        {
            "current_focus": snapshot.current_focus,
            "active_projects": snapshot.active_projects,
            "open_questions": snapshot.open_questions,
            "next_steps": snapshot.next_steps,
        },
        ensure_ascii=False,
    )


def _render_transcript(
    messages: list[dict[str, Any]],
    max_chars: int = 8000,
    *,
    head_ratio: float = _TRANSCRIPT_HEAD_RATIO,
    tail_ratio: float = _TRANSCRIPT_TAIL_RATIO,
) -> str:
    """渲染 user/assistant 文本转录，超长时 head + tail 双向保留。

    短对话（``<= max_chars``）原样返回，不做任何截断。
    长对话保留头部 ``head_ratio`` + 尾部 ``tail_ratio``，中间插入
    ``_TRANSCRIPT_TRUNCATE_MARKER``，让 LLM 既能看到开篇任务定义，
    又能看到结尾最新进展，覆盖率显著高于纯 tail 截断。

    参考 OpenAkita ``smart_truncate``（truncate.py:60-82），但更简单、无 sidecar
    文件机制（本路径无 LLM 工具调用回读能力）。

    :param head_ratio: 头部比例，默认 0.4。会被裁剪到 ``[0.0, 1.0]``。
    :param tail_ratio: 尾部比例，默认 0.45。会被裁剪到 ``[0.0, 1.0]``。
        若 ``head_ratio + tail_ratio > 1.0``，自动按比例缩放，确保不溢出。
    """
    head_ratio = max(0.0, min(1.0, float(head_ratio)))
    tail_ratio = max(0.0, min(1.0, float(tail_ratio)))
    if head_ratio + tail_ratio > 1.0:
        scale = 1.0 / (head_ratio + tail_ratio)
        head_ratio *= scale
        tail_ratio *= scale

    lines: list[str] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        text = _content_text(msg.get("content")).strip()
        if not text:
            continue
        lines.append(f"[{role}] {text}")
    joined = "\n".join(lines)
    if len(joined) <= max_chars:
        return joined
    head_chars = int(max_chars * head_ratio)
    tail_chars = int(max_chars * tail_ratio)
    return joined[:head_chars] + _TRANSCRIPT_TRUNCATE_MARKER + joined[-tail_chars:]


# ---------------------------------------------------------------------------
# MemoryExtractor
# ---------------------------------------------------------------------------


class MemoryExtractor:
    """从 ``session.messages`` 提取三层记忆的核心类（plan §7.4）。"""

    #: 单路 LLM 调用的超时（秒）；测试可覆盖实例属性以缩短超时。
    LLM_TIMEOUT: float = 30.0

    #: idle 增量抽取的切片条数下限：连「user 提问 + assistant 回复」都不足时无内容可抽。
    #:
    #: 取 2 而非 3：idle 抽的是增量切片，一轮实质对话（user + assistant 共 2 条）本身
    #: 就是完整单元，不像「以整个会话为抽取单元」时那样需要 3 条才成形态。区分
    #: 「琐碎」与「实质」的职责交给 :data:`IDLE_MIN_SINGLE_USER_CHARS` —— 一句「你好」
    #: 仍会被它拦下。
    IDLE_MIN_NEW_MESSAGES: int = 2

    #: 新增切片只有一条 user 消息、内容短于该字符数且无工具调用 → 不调 LLM。
    #: 切片内有工具调用则豁免。
    IDLE_MIN_SINGLE_USER_CHARS: int = 10

    def __init__(
        self,
        database: MemoryDatabase,
        runtime: LLMRuntime,
        workspace_id: str = "default",
        user_id: str = "default",
        *,
        session_loader: Callable[[str], Session | None] | None = None,
        clock: MemoryClock | None = None,
    ) -> None:
        self.database = database
        self.runtime = runtime
        self.workspace_id = workspace_id
        self.user_id = user_id
        # 评测可注入的时钟；缺省时全部走真实时间，生产行为不变。
        self._clock = clock
        # S3 Track 1：画像 + 引用评分 + 经验。SEMANTIC_EXTRACTION_PROMPT 是双轨输出，
        # 一次调用同时产出 memories / experiences，交给 ProfileExtractor 统一解析。
        self.profile_extractor = ProfileExtractor(runtime=runtime)
        # 批次协议需要按 session_key 读取**持久**消息坐标（memory_generation /
        # memory_seq）。hook 只能拿到剥掉身份字段的 provider 转录，因此由调用方
        # 注入会话快照读取器；缺省时按 memory 库所在工作区惰性构造
        # ``SessionManager.read_session_snapshot``（同进程默认数据目录）。
        self._session_loader = session_loader
        self._default_session_loader: Callable[[str], Session | None] | None = None
        self._default_session_loader_ready = False

    def _now_iso(self) -> str:
        """当前时间戳；注入 clock 时使用虚拟墙上时间（评测确定性）。"""
        if self._clock is not None:
            return self._clock.iso_now()
        return _now_iso()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def extract_session(
        self,
        session: Session,
        *,
        source: str = "session_end",
    ) -> ExtractionResult:
        """完整提取入口（薄适配）：把会话转换成 :class:`ExtractionRequest`。

        进度、幂等与事务全部由 :meth:`process_request` 负责；本方法只负责
        「会话 → 请求坐标」与「回执 → 旧返回类型」。保留原签名供删除路径、
        手工调用与测试使用。
        """
        started = time.perf_counter()
        result = await self._submit_session_request(session, source=source)
        logger.info(
            "memory extraction elapsed: session={} source={} elapsed_ms={:.0f}",
            session.key,
            source,
            (time.perf_counter() - started) * 1000,
        )
        return result

    async def flush_session(
        self,
        session_key: str,
        *,
        workspace_id: str,
        reason: str = "session_end",
    ) -> BatchReceipt | None:
        """会话结束 flush（契约 §5）：立刻消费到当前持久上界。

        - ``generation`` / ``through_seq`` 一律从带坐标的持久快照解析，与 idle /
          增量 / 删除路径共用同一个消费键与 ``last_seq`` 真相源，不另建进度链。
        - ``workspace_id`` 是调用方声明的记忆隔离键。与快照归属不一致时以快照
          为准并告警：同一会话出现两条消费键会让相同消息被抽两次。
        - 提交 ``ExtractionRequest(flush=True)`` 并走 :meth:`process_request`：
          无新增消息时返回 ``completed`` 空回执；模型 / 持久化失败返回带
          ``error_code`` 的失败回执，不伪装成空成功。
        - 会话从未持久化（读不到快照）时没有可消费证据，返回 ``None``。
        """
        loaded = self.load_session(session_key)
        if loaded is None or not loaded.memory_generation:
            logger.info(
                "session end flush skipped: session={} has no persisted memory snapshot",
                session_key,
            )
            return None
        resolved_workspace = self._row_workspace_id(loaded)
        if workspace_id and workspace_id != resolved_workspace:
            logger.warning(
                "session end flush workspace mismatch: session={} declared={} session={}",
                session_key,
                workspace_id,
                resolved_workspace,
            )
        through = self._through_seq(loaded)
        last_seq = self._last_consumed_seq(loaded, loaded.memory_generation)
        if through <= last_seq:
            # 没有新增消息：flush 本身没有消费任何内容，返回 completed 空回执。
            # 刻意不复用「重复上界返回既有批次」的 replay：上一批的 memory_ids
            # 属于更早的触发，作为本次结束 flush 的产出回报会误导调用方。
            logger.info(
                "session end flush noop: session={} through={} <= last_seq={}",
                session_key,
                through,
                last_seq,
            )
            return BatchReceipt(
                batch_id=_stable_batch_id(
                    resolved_workspace,
                    session_key,
                    loaded.memory_generation,
                    last_seq,
                    through,
                ),
                status="completed",
                through_seq=through,
            )
        request = ExtractionRequest(
            workspace_id=resolved_workspace,
            session_key=session_key,
            generation=loaded.memory_generation,
            through_seq=through,
            reason=reason,
            flush=True,
        )
        started = time.perf_counter()
        receipt = await self.process_request(request, session=loaded)
        logger.info(
            "session end flush finished: session={} reason={} status={} through={} "
            "error={} elapsed_ms={:.0f}",
            session_key,
            reason,
            receipt.status,
            receipt.through_seq,
            receipt.error_code or "-",
            (time.perf_counter() - started) * 1000,
        )
        return receipt

    # ------------------------------------------------------------------
    # 批次协议主入口（契约 §2）
    # ------------------------------------------------------------------

    async def process_request(
        self,
        request: ExtractionRequest,
        *,
        session: Session | None = None,
        start_hint: int | None = None,
    ) -> BatchReceipt:
        """按消费键顺序消费 ``(last_seq, through_seq]``，返回结构化回执。

        契约 §2 的批次协议主体：

        - ``(workspace_id, session_key, generation)`` 是消费键；批次边界固定为
          ``(start_seq, end_seq]``，batch_id 由二者做稳定摘要。
        - 同一消费键下先续跑未完成批次，再处理新上界；重复请求相同上界返回
          既有结果，不新建批次。
        - 两轨各自 checkpoint，失败只重试失败轨；两轨就绪后事实/情节写入与
          ``last_seq`` 在同一事务提交。
        - 失败不静默：返回 ``pending / partial / failed / completed`` 回执，
          ``error_code`` 是结构化错误码。

        Args:
            request: 消费边界与来源。
            session: 可选的坐标承载会话。缺省时按 ``session_key`` 读取持久快照
                （hook 只能拿到剥掉身份字段的 provider 转录）。
            start_hint: 旧接口（``extract_incremental``）的起始下标提示。只在
                「尚无任何已提交进度」时生效，用于迁移期把旧索引换算成 seq 起点；
                它不推进游标，也不覆盖已记录的 ``last_seq``。
        """
        key = (request.workspace_id, request.session_key, request.generation)
        lock = _consumption_lock(key)
        async with lock:
            try:
                return await self._process_request_locked(request, session, start_hint)
            except _BatchError as exc:
                logger.warning(
                    "memory batch rejected: session={} generation={} through={} code={} detail={}",
                    request.session_key,
                    request.generation,
                    request.through_seq,
                    exc.code,
                    exc.detail,
                )
                return self._error_receipt(request, 0, exc.code, exc.detail)
            except Exception as exc:  # noqa: BLE001 - 批次失败必须返回结构化回执
                logger.opt(exception=exc).warning(
                    "memory batch failed: session={} generation={} through={}",
                    request.session_key,
                    request.generation,
                    request.through_seq,
                )
                return self._error_receipt(request, 0, ERROR_UNEXPECTED, str(exc))

    # -- process_request 内部步骤 ---------------------------------------------

    async def _process_request_locked(
        self,
        request: ExtractionRequest,
        session: Session | None,
        start_hint: int | None,
    ) -> BatchReceipt:
        through = int(request.through_seq)
        if through < 0:
            raise _BatchError(ERROR_EVIDENCE_MISSING, f"through_seq must be >= 0: {through}")
        resolved = self._resolve_request_session(request, session)
        if not request.generation:
            # 调用方省略代时以持久会话为准；消费键、批次行与状态行必须同源。
            request = dataclasses.replace(request, generation=resolved.memory_generation)
        start = self._last_consumed_seq(resolved, request.generation)
        if start_hint is not None and start == 0:
            start = self._seq_start_from_index(resolved, start_hint, through)
        if through <= start:
            logger.info(
                "memory batch replay: session={} generation={} through={} <= last_seq={}",
                request.session_key,
                request.generation,
                through,
                start,
            )
            return self._replay_receipt(request, start, through)

        current = start
        last_receipt: BatchReceipt | None = None
        while True:
            pending = self._oldest_incomplete_batch(request, current)
            if pending is None:
                break
            receipt = await self._execute_batch(request, pending, resolved)
            if receipt.status != "completed":
                return receipt
            last_receipt = receipt
            current = receipt.through_seq
            if current >= through:
                return receipt

        if current < through:
            batch = self._ensure_batch(request, resolved, current, through)
            receipt = await self._execute_batch(request, batch, resolved)
            if receipt.status != "completed":
                return receipt
            last_receipt = receipt
            current = receipt.through_seq

        return last_receipt if last_receipt is not None else self._replay_receipt(
            request, start, through
        )

    def _resolve_request_session(
        self,
        request: ExtractionRequest,
        session: Session | None,
    ) -> Session:
        if session is not None:
            resolved = self._resolve_session(session)
            if request.generation and resolved.memory_generation != request.generation:
                raise _BatchError(
                    ERROR_GENERATION_MISMATCH,
                    f"request generation={request.generation!r} "
                    f"session generation={resolved.memory_generation!r}",
                )
            return resolved
        loaded = self.load_session(request.session_key)
        if loaded is None:
            raise _BatchError(
                ERROR_SESSION_UNAVAILABLE,
                f"no persisted session snapshot for {request.session_key!r}",
            )
        if not loaded.memory_generation:
            raise _BatchError(
                ERROR_SESSION_UNAVAILABLE,
                f"session {request.session_key!r} has no durable memory generation",
            )
        if request.generation and request.generation != loaded.memory_generation:
            raise _BatchError(
                ERROR_GENERATION_MISMATCH,
                f"request generation={request.generation!r} "
                f"session generation={loaded.memory_generation!r}",
            )
        return loaded

    def _resolve_session(self, session: Session) -> Session:
        """把调用方会话解析成「带持久坐标」的会话。

        - 已带 generation：直接使用（删除路径传入的就是持久快照）。
        - 没有 generation：按 key 读取持久快照（hook 重建的转录没有身份字段）；
          快照也不存在（从未保存）时用保存路径同一规则就地编号，不写盘。
        """
        if session.memory_generation:
            return session
        loaded = self.load_session(session.key)
        if loaded is not None and loaded.memory_generation:
            # 重建的 Session 携带调用方最新的归属工作区，比磁盘上的旧值更可信。
            scope = session.metadata.get("workspace_scope")
            if isinstance(scope, dict) and scope:
                loaded.metadata["workspace_scope"] = dict(scope)
            return loaded
        try:
            from nanobot.session.manager import _assign_memory_coordinates

            _assign_memory_coordinates(session)
        except Exception as exc:  # noqa: BLE001 - 坐标非法必须报告，不能静默重编号
            raise _BatchError(ERROR_COORDINATE_INVALID, str(exc)) from exc
        if not session.memory_generation:
            session.memory_generation = (
                _EPHEMERAL_GENERATION_PREFIX + uuid4().hex
            )
        return session

    def load_session(self, session_key: str) -> Session | None:
        """读取持久会话快照（含坐标）；读取失败只告警并返回 ``None``。"""
        loader = self._session_loader
        if loader is None:
            if not self._default_session_loader_ready:
                self._default_session_loader = self._build_default_session_loader()
                self._default_session_loader_ready = True
            loader = self._default_session_loader
        if loader is None:
            return None
        try:
            return loader(session_key)
        except Exception as exc:  # noqa: BLE001 - 快照读取失败按不可用处理
            logger.warning(
                "memory batch: session snapshot load failed for {}: {}",
                session_key,
                exc,
            )
            return None

    def _build_default_session_loader(self) -> Callable[[str], Session | None] | None:
        workspace = getattr(self.database, "workspace", None)
        if workspace is None:
            return None
        cache_key = str(Path(workspace))
        if cache_key in _SESSION_LOADERS:
            return _SESSION_LOADERS[cache_key]
        loader: Callable[[str], Session | None] | None = None
        try:
            from nanobot.session.manager import SessionManager

            manager = SessionManager(Path(workspace))
            loader = manager.read_session_snapshot
        except Exception as exc:  # noqa: BLE001 - 无会话存储时退化为不可用
            logger.warning(
                "memory batch: session store unavailable under {}: {}", workspace, exc
            )
        _SESSION_LOADERS[cache_key] = loader
        return loader

    def _last_consumed_seq(self, session: Session, generation: str) -> int:
        with self.database.connect() as conn:
            return get_extraction_seq(conn, session.key, generation)

    def _through_seq(self, session: Session) -> int:
        highest = max(
            (seq for seq in (_positive_seq(m) for m in session.messages) if seq is not None),
            default=0,
        )
        if session.memory_generation and session.memory_next_seq > 0:
            return max(highest, session.memory_next_seq - 1)
        return highest

    def _request_for_session(
        self,
        session: Session,
        reason: str,
        *,
        through_seq: int | None = None,
    ) -> ExtractionRequest:
        return ExtractionRequest(
            workspace_id=self._row_workspace_id(session),
            session_key=session.key,
            generation=session.memory_generation,
            through_seq=self._through_seq(session) if through_seq is None else int(through_seq),
            reason=reason,
        )

    def _seq_start_from_index(
        self,
        session: Session,
        start_index: int,
        through: int,
    ) -> int:
        """把旧接口的消息下标换算成 seq 起点（仅迁移期使用）。"""
        index = max(0, int(start_index))
        if index >= len(session.messages):
            return through
        seq = _positive_seq(session.messages[index])
        return max(0, (seq - 1) if seq is not None else 0)

    def _slice_evidence(
        self,
        session: Session,
        start_seq: int,
        end_seq: int,
    ) -> list[tuple[int, dict[str, Any]]]:
        pairs = [
            (seq, message)
            for message in session.messages
            if (seq := _positive_seq(message)) is not None and start_seq < seq <= end_seq
        ]
        pairs.sort(key=lambda item: item[0])
        return pairs

    def _evidence_session(
        self,
        session: Session,
        evidence: Sequence[tuple[int, dict[str, Any]]],
    ) -> Session:
        """把本批证据包成 ``Session``，供 prompt / 系统提取只看到该切片。"""
        from nanobot.session.manager import Session as _Session

        return _Session(
            key=session.key,
            messages=[dict(message) for _, message in evidence],
            created_at=session.created_at,
            metadata=dict(session.metadata),
        )

    def _oldest_incomplete_batch(
        self,
        request: ExtractionRequest,
        after_seq: int,
    ) -> ExtractionBatchRow | None:
        with self.database.connect() as conn:
            rows = list_incomplete_extraction_batches(
                conn,
                workspace_id=request.workspace_id,
                session_key=request.session_key,
                generation=request.generation,
                after_seq=after_seq,
            )
        return rows[0] if rows else None

    def _ensure_batch(
        self,
        request: ExtractionRequest,
        session: Session,
        start_seq: int,
        end_seq: int,
    ) -> ExtractionBatchRow:
        evidence = self._slice_evidence(session, start_seq, end_seq)
        if not evidence:
            raise _BatchError(
                ERROR_EVIDENCE_MISSING,
                f"no messages with memory_seq in ({start_seq}, {end_seq}]",
            )
        digest = _evidence_digest(evidence)
        batch_id = _stable_batch_id(
            request.workspace_id,
            request.session_key,
            request.generation,
            start_seq,
            end_seq,
        )
        now = self._now_iso()
        fresh = ExtractionBatchRow(
            batch_id=batch_id,
            workspace_id=request.workspace_id,
            session_key=request.session_key,
            generation=request.generation,
            reason=request.reason,
            start_seq=int(start_seq),
            end_seq=int(end_seq),
            evidence_digest=digest,
            status="pending",
            semantic_output="",
            episode_output="",
            semantic_checkpoint_at="",
            episode_checkpoint_at="",
            claim_owner="",
            claimed_at="",
            lease_expires_at="",
            error_code="",
            error_detail="",
            created_at=now,
            updated_at=now,
        )
        with self.database.connect() as conn:
            existing = get_extraction_batch(conn, batch_id)
            if existing is not None:
                return self._require_matching_payload(existing, digest)
            if insert_extraction_batch(conn, fresh):
                return fresh
            # 唯一约束冲突：并发方已插入同一批次，按既有行继续（仍要校验载荷）。
            existing = get_extraction_batch(conn, batch_id)
        if existing is None:
            raise _BatchError(
                ERROR_BATCH_CLAIM_LOST,
                f"batch row vanished after insert conflict: {batch_id}",
            )
        return self._require_matching_payload(existing, digest)

    def _require_matching_payload(
        self,
        row: ExtractionBatchRow,
        digest: str,
    ) -> ExtractionBatchRow:
        if row.evidence_digest == digest:
            return row
        if row.status != "completed":
            self._record_batch_failure(
                row.batch_id,
                ERROR_BATCH_PAYLOAD_MISMATCH,
                f"stored digest={row.evidence_digest} incoming digest={digest}",
            )
        raise _BatchError(
            ERROR_BATCH_PAYLOAD_MISMATCH,
            f"batch {row.batch_id} evidence digest changed",
        )

    def _replay_receipt(
        self,
        request: ExtractionRequest,
        start_seq: int,
        through: int,
    ) -> BatchReceipt:
        """重复请求相同上界：返回已落库结果，不新建批次。"""
        with self.database.connect() as conn:
            row = find_extraction_batch_by_bounds(
                conn,
                workspace_id=request.workspace_id,
                session_key=request.session_key,
                generation=request.generation,
                end_seq=through,
            )
        if row is None:
            return BatchReceipt(
                batch_id=_stable_batch_id(
                    request.workspace_id,
                    request.session_key,
                    request.generation,
                    start_seq,
                    through,
                ),
                status="completed",
                through_seq=through,
            )
        return self._receipt_from_row(row)

    def _receipt_from_row(self, row: ExtractionBatchRow) -> BatchReceipt:
        memory_ids: tuple[str, ...] = ()
        episode_ids: tuple[str, ...] = ()
        try:
            stored = json.loads(row.semantic_output or "{}")
        except (json.JSONDecodeError, ValueError):
            stored = {}
        committed = stored.get("committed") if isinstance(stored, dict) else None
        if isinstance(committed, dict):
            memory_ids = tuple(str(item) for item in committed.get("memory_ids") or ())
            episode_ids = tuple(str(item) for item in committed.get("episode_ids") or ())
        status = row.status if row.status in ("pending", "partial", "failed", "completed") else "failed"
        return BatchReceipt(
            batch_id=row.batch_id,
            status=cast(Any, status),
            through_seq=row.end_seq,
            memory_ids=memory_ids,
            episode_ids=episode_ids,
            error_code=row.error_code or None,
        )

    def _error_receipt(
        self,
        request: ExtractionRequest,
        start_seq: int,
        code: str,
        detail: str,
    ) -> BatchReceipt:
        return BatchReceipt(
            batch_id=_stable_batch_id(
                request.workspace_id,
                request.session_key,
                request.generation,
                start_seq,
                request.through_seq,
            ),
            status="failed",
            through_seq=int(request.through_seq),
            error_code=code,
        )

    # -- 单批执行 ------------------------------------------------------------

    async def _execute_batch(
        self,
        request: ExtractionRequest,
        row: ExtractionBatchRow,
        session: Session,
    ) -> BatchReceipt:
        evidence = self._slice_evidence(session, row.start_seq, row.end_seq)
        if not evidence:
            self._record_batch_failure(
                row.batch_id,
                ERROR_EVIDENCE_MISSING,
                f"no messages in ({row.start_seq}, {row.end_seq}]",
            )
            return BatchReceipt(
                batch_id=row.batch_id,
                status="failed",
                through_seq=row.end_seq,
                error_code=ERROR_EVIDENCE_MISSING,
            )
        digest = _evidence_digest(evidence)
        if digest != row.evidence_digest:
            if row.status != "completed":
                self._record_batch_failure(
                    row.batch_id,
                    ERROR_BATCH_PAYLOAD_MISMATCH,
                    f"stored digest={row.evidence_digest} incoming digest={digest}",
                )
            return BatchReceipt(
                batch_id=row.batch_id,
                status="failed",
                through_seq=row.end_seq,
                error_code=ERROR_BATCH_PAYLOAD_MISMATCH,
            )

        owner = uuid4().hex
        claim = self._claim_batch(row.batch_id, owner)
        if claim is None:
            current = self._get_batch(row.batch_id)
            if current is not None and current.status == "completed":
                return self._receipt_from_row(current)
            return BatchReceipt(
                batch_id=row.batch_id,
                status="pending",
                through_seq=row.end_seq,
                error_code=ERROR_BATCH_CLAIMED,
            )
        if claim.status == "completed":
            # 另一执行方已提交（例如跨进程并发）：直接复用既有结果。
            return self._receipt_from_row(claim)

        tracks = [
            track
            for track, checkpoint in (
                ("semantic", claim.semantic_checkpoint_at),
                ("episode", claim.episode_checkpoint_at),
            )
            if not checkpoint
        ]
        if tracks:
            # 模型只看到本批证据：切片会话与原会话同 key / 元数据，但 messages
            # 仅含 (start_seq, end_seq] 的原始消息（契约 §2.1「会话内顺序消费」）。
            evidence_session = self._evidence_session(session, evidence)
            system = self._system_extract(evidence_session)
            llm_result = await self._llm_extract(
                evidence_session,
                system,
                tracks=tuple(tracks),
            )
            receipt = self._checkpoint_tracks(claim, owner, tracks, llm_result)
            if receipt is not None:
                return receipt

        return await self._finalize_batch(
            request,
            self._get_batch(row.batch_id) or claim,
            owner,
            session,
        )

    def _claim_batch(self, batch_id: str, owner: str) -> ExtractionBatchRow | None:
        """认领批次（带 owner + lease）；被别的活 owner 持有时返回 ``None``。"""
        now = datetime.now(timezone.utc)
        deadline = (now + timedelta(seconds=BATCH_LEASE_SECONDS)).isoformat()
        with self.database.connect() as conn:
            row = get_extraction_batch(conn, batch_id)
            if row is None:
                return None
            if row.status == "completed":
                return row
            holding_lease = (
                row.claim_owner
                and row.claim_owner != owner
                and row.lease_expires_at
                and row.lease_expires_at > now.isoformat()
            )
            if holding_lease:
                logger.info(
                    "memory batch {} is claimed by {} until {}; leaving it to the owner",
                    batch_id,
                    row.claim_owner,
                    row.lease_expires_at,
                )
                return None
            claimed = update_extraction_batch(
                conn,
                batch_id,
                updates={
                    "claim_owner": owner,
                    "claimed_at": now.isoformat(),
                    "lease_expires_at": deadline,
                    "updated_at": now.isoformat(),
                },
            )
            if not claimed:
                return None
            row = get_extraction_batch(conn, batch_id)
        return row

    def _checkpoint_tracks(
        self,
        row: ExtractionBatchRow,
        owner: str,
        tracks: Sequence[str],
        llm_result: LLMExtractionResult,
    ) -> BatchReceipt | None:
        """把成功的轨道写 checkpoint；有失败轨时返回非完成回执。"""
        now = self._now_iso()
        updates: dict[str, Any] = {
            "updated_at": now,
            "lease_expires_at": self._lease_deadline(),
        }
        if "semantic" in tracks and "semantic" not in llm_result.failed_tracks:
            semantic_payload = llm_result.semantic_payload
            if semantic_payload is None:
                # 兼容测试/扩展点直接构造 LLMExtractionResult 的情况：没有显式
                # payload 时用候选本身序列化，checkpoint 语义不变。
                semantic_payload = {
                    "memories": [_memory_item_to_dict(item) for item in llm_result.memories],
                }
            updates["semantic_output"] = json.dumps(
                semantic_payload, ensure_ascii=False, default=str
            )
            updates["semantic_checkpoint_at"] = now
        if "episode" in tracks and "episode" not in llm_result.failed_tracks:
            episode_payload = llm_result.episode_payload
            if episode_payload is None and llm_result.episode is not None:
                episode_payload = _episode_item_to_dict(llm_result.episode)
            updates["episode_output"] = json.dumps(
                episode_payload or {}, ensure_ascii=False, default=str
            )
            updates["episode_checkpoint_at"] = now

        failed = [track for track in tracks if track in llm_result.failed_tracks]
        if not failed:
            self._update_batch(row.batch_id, updates, owner=owner)
            return None

        ready = [
            track
            for track, checkpoint in (
                ("semantic", updates.get("semantic_checkpoint_at") or row.semantic_checkpoint_at),
                ("episode", updates.get("episode_checkpoint_at") or row.episode_checkpoint_at),
            )
            if checkpoint
        ]
        status = "partial" if ready else "failed"
        first_failed = failed[0]
        code = (
            ERROR_SEMANTIC_TRACK_FAILED
            if first_failed == "semantic"
            else ERROR_EPISODE_TRACK_FAILED
        )
        detail = "; ".join(
            f"{track}: {llm_result.track_errors.get(track, 'unknown')}" for track in failed
        )
        updates.update(
            {
                "status": status,
                "error_code": code,
                "error_detail": detail,
                # 立即释放 claim：同一进程的下一次请求（或重试）可以只重跑失败轨。
                "claim_owner": "",
                "lease_expires_at": "",
            }
        )
        if not self._update_batch(row.batch_id, updates, owner=owner):
            logger.warning("memory batch {} lost its claim before checkpoint", row.batch_id)
            return BatchReceipt(
                batch_id=row.batch_id,
                status="failed",
                through_seq=row.end_seq,
                error_code=ERROR_BATCH_CLAIM_LOST,
            )
        logger.warning(
            "memory batch {} partially checkpointed: ready={} failed={} detail={}",
            row.batch_id,
            ready,
            failed,
            detail,
        )
        return BatchReceipt(
            batch_id=row.batch_id,
            status=cast(Any, status),
            through_seq=row.end_seq,
            error_code=code,
        )

    async def _finalize_batch(
        self,
        request: ExtractionRequest,
        row: ExtractionBatchRow,
        owner: str,
        session: Session,
    ) -> BatchReceipt:
        """两轨就绪：事实/情节写入 + last_seq + 批次完成，同一事务提交。"""
        llm_result = self._restore_checkpointed_result(row)
        # 系统提取是确定性输入，不随 checkpoint 持久化；还原后补回，
        # 否则「有效空情节 + 工具动作」的重试批次会丢掉 action_nodes。
        evidence = self._slice_evidence(session, row.start_seq, row.end_seq)
        if evidence:
            system = self._system_extract(self._evidence_session(session, evidence))
            llm_result = dataclasses.replace(
                llm_result, action_nodes=list(system.action_nodes)
            )
        filtered = self._apply_filters(llm_result)
        skipped = len(llm_result.memories) - len(filtered.memories)
        now = self._now_iso()
        try:
            with self.database.connect() as conn:
                persisted, indexed = self._write_extraction(
                    conn,
                    filtered,
                    session=session,
                    source=row.reason or request.reason or "extraction",
                    now=now,
                    evidence_key=row.batch_id,
                )
                stored = json.loads(row.semantic_output or "{}")
                if not isinstance(stored, dict):
                    stored = {}
                stored["committed"] = {
                    "memory_ids": list(persisted.memory_ids),
                    "episode_ids": list(persisted.episode_ids),
                    "skipped": skipped,
                }
                completed = update_extraction_batch(
                    conn,
                    row.batch_id,
                    updates={
                        "status": "completed",
                        "semantic_output": json.dumps(stored, ensure_ascii=False, default=str),
                        "semantic_checkpoint_at": row.semantic_checkpoint_at or now,
                        "episode_checkpoint_at": row.episode_checkpoint_at or now,
                        "error_code": "",
                        "error_detail": "",
                        "claim_owner": "",
                        "lease_expires_at": "",
                        "updated_at": now,
                    },
                    require_owner=owner,
                )
                if not completed:
                    raise _BatchError(
                        ERROR_BATCH_CLAIM_LOST,
                        f"batch {row.batch_id} claim expired before commit",
                    )
                consumed = sum(
                    1
                    for message in session.messages
                    if (seq := _positive_seq(message)) is not None
                    and seq <= row.end_seq
                )
                set_extraction_seq(
                    conn,
                    row.session_key,
                    generation=row.generation,
                    last_seq=row.end_seq,
                    source=row.reason or request.reason or "extraction",
                    extracted_at=now,
                    # 旧 ``last_count`` 只作兼容镜像：记录已消费条数，不再驱动进度。
                    last_count=consumed,
                )
        except _BatchError as exc:
            # 认领已失效说明另一个执行方可能正在提交：不能把它的批次标成失败。
            if exc.code != ERROR_BATCH_CLAIM_LOST:
                self._record_batch_failure(row.batch_id, exc.code, exc.detail)
            return BatchReceipt(
                batch_id=row.batch_id,
                status="failed",
                through_seq=row.end_seq,
                error_code=exc.code,
            )
        except Exception as exc:  # noqa: BLE001 - 事务已回滚，报告为持久化失败
            logger.opt(exception=exc).warning(
                "memory batch {} persistence failed", row.batch_id
            )
            self._record_batch_failure(row.batch_id, ERROR_PERSISTENCE_FAILED, str(exc))
            return BatchReceipt(
                batch_id=row.batch_id,
                status="failed",
                through_seq=row.end_seq,
                error_code=ERROR_PERSISTENCE_FAILED,
            )

        # 向量刷新在数据库提交之后：失败只记 dirty（index hook 自身绝不抛）。
        for memory in indexed:
            index_memory_best_effort(memory)
        logger.info(
            "memory batch completed: batch={} session={} range=({}, {}] memories={} episodes={} skipped={}",
            row.batch_id,
            row.session_key,
            row.start_seq,
            row.end_seq,
            len(persisted.memory_ids),
            len(persisted.episode_ids),
            skipped,
        )
        return BatchReceipt(
            batch_id=row.batch_id,
            status="completed",
            through_seq=row.end_seq,
            memory_ids=tuple(persisted.memory_ids),
            episode_ids=tuple(persisted.episode_ids),
        )

    def _restore_checkpointed_result(
        self, row: ExtractionBatchRow,
    ) -> LLMExtractionResult:
        """从两轨 checkpoint 还原过滤前的候选（不再调模型）。"""
        try:
            semantic = json.loads(row.semantic_output or "{}")
        except (json.JSONDecodeError, ValueError):
            semantic = {}
        if not isinstance(semantic, dict):
            semantic = {}
        memories = [
            item
            for raw in semantic.get("memories") or ()
            if (item := _memory_item_from_dict(raw)) is not None
        ]
        episode_payload: dict[str, Any] | None = None
        if row.episode_checkpoint_at:
            try:
                parsed = json.loads(row.episode_output or "{}")
            except (json.JSONDecodeError, ValueError):
                parsed = {}
            if isinstance(parsed, dict):
                episode_payload = parsed
        # 空 dict 表示「有效空输出」：与旧的 (None, None) 一致，不产生空 episode 行。
        episode = _coerce_episode_item(episode_payload) if episode_payload else None
        return LLMExtractionResult(
            memories=memories,
            episode=episode,
        )

    def _get_batch(self, batch_id: str) -> ExtractionBatchRow | None:
        with self.database.connect() as conn:
            return get_extraction_batch(conn, batch_id)

    def _update_batch(
        self,
        batch_id: str,
        updates: dict[str, Any],
        *,
        owner: str,
    ) -> bool:
        with self.database.connect() as conn:
            return update_extraction_batch(
                conn, batch_id, updates=updates, require_owner=owner
            )

    def _record_batch_failure(self, batch_id: str, code: str, detail: str) -> None:
        """把失败写回批次行；写回失败只告警（原始回执已经带 error_code）。"""
        now = self._now_iso()
        try:
            with self.database.connect() as conn:
                update_extraction_batch(
                    conn,
                    batch_id,
                    updates={
                        "status": "failed",
                        "error_code": code,
                        "error_detail": detail[:2000],
                        "claim_owner": "",
                        "lease_expires_at": "",
                        "updated_at": now,
                    },
                )
        except Exception as exc:  # noqa: BLE001 - 记录失败不能掩盖原始结果
            logger.warning("memory batch failure record skipped for {}: {}", batch_id, exc)

    @staticmethod
    def _lease_deadline() -> str:
        return (
            datetime.now(timezone.utc) + timedelta(seconds=BATCH_LEASE_SECONDS)
        ).isoformat()

    async def _submit_session_request(
        self,
        session: Session,
        *,
        source: str,
        start_hint: int | None = None,
    ) -> ExtractionResult:
        """会话 → 请求 → 回执 → 旧返回类型（薄适配共用）。"""
        try:
            resolved = self._resolve_session(session)
        except _BatchError as exc:
            logger.warning(
                "memory extraction rejected for session {}: {} ({})",
                session.key,
                exc.code,
                exc.detail,
            )
            return ExtractionResult(failed_tracks=["batch"])
        request = self._request_for_session(resolved, source)
        receipt = await self.process_request(
            request,
            session=resolved,
            start_hint=start_hint,
        )
        return self._to_extraction_result(receipt)

    def _to_extraction_result(self, receipt: BatchReceipt) -> ExtractionResult:
        """回执 → ``ExtractionResult``：失败以 failed_tracks 明确报告。"""
        if receipt.status == "completed":
            return ExtractionResult(
                memory_ids=list(receipt.memory_ids),
                episode_ids=list(receipt.episode_ids),
                skipped=self._committed_skipped(receipt.batch_id),
            )
        failed_tracks = self._failed_tracks(receipt.batch_id)
        if not failed_tracks:
            failed_tracks = ["batch"]
        return ExtractionResult(failed_tracks=failed_tracks)

    def _committed_skipped(self, batch_id: str) -> int:
        row = self._get_batch(batch_id)
        if row is None:
            return 0
        try:
            stored = json.loads(row.semantic_output or "{}")
        except (json.JSONDecodeError, ValueError):
            return 0
        committed = stored.get("committed") if isinstance(stored, dict) else None
        if not isinstance(committed, dict):
            return 0
        try:
            return int(committed.get("skipped") or 0)
        except (TypeError, ValueError):
            return 0

    def _failed_tracks(self, batch_id: str) -> list[str]:
        row = self._get_batch(batch_id)
        if row is None:
            return []
        return [
            track
            for track, checkpoint in (
                ("semantic", row.semantic_checkpoint_at),
                ("episode", row.episode_checkpoint_at),
            )
            if not checkpoint
        ]

    # ------------------------------------------------------------------
    # Quick Facts：压缩后规则信号提取（无需 LLM；plan §10 Task 9）
    # ------------------------------------------------------------------

    def extract_quick_facts(self, session: Session) -> int:
        """从 ``session.messages`` 用正则抽取「规则信号」类记忆（不调 LLM）。

        只扫描 user 消息，命中的句子落为 ``RULE``，priority 走 §8.2 兜底
        （``_resolve_priority``：含时间关键词 → short_term，否则 long_term）。
        去重与落库复用阶段3/4 的 ``_apply_filters`` + ``_persist``，不另写 INSERT。

        Returns:
            实际写入的 memory 条数；无命中或任何异常时返回 0（异常仅 warning）。
        """
        try:
            fragments = _collect_rule_signals(session.messages) if session.messages else []
            if not fragments:
                return 0
            candidates = [
                LLMMemoryItem(content=fragment, type=_QUICK_FACT_TYPE)
                for fragment in fragments
            ]
            row_workspace_id = self._row_workspace_id(session)
            filtered = self._apply_filters(LLMExtractionResult(memories=candidates))
            if not filtered.memories:
                return 0
            logger.debug(
                "quick facts candidates: session={} workspace_id={} kept={}",
                session.key,
                row_workspace_id,
                len(filtered.memories),
            )
            # 只填 memories（无 episode / action_nodes），故 _persist 仅写 memories，
            # source 仅用于（可能存在的）episode 映射，此处取压缩触发语义。
            persisted = self._persist(filtered, session, source="context_compress")
            return persisted.created_count
        except Exception as exc:  # noqa: BLE001 - 快速事实失败绝不能上抛
            logger.opt(exception=exc).warning("quick facts extraction failed: {}", exc)
            return 0

    async def extract_incremental(
        self,
        session: Session,
        last_extracted_index: int,
        *,
        source: str = "topic_change",
    ) -> ExtractionResult:
        """增量抽取薄适配：按批次协议消费 ``(last_seq, through_seq]``。

        流水线与 :meth:`extract_session` 完全一致（语义轨 + 情节轨都会跑），
        差别只在来源标签。

        ``last_extracted_index`` 只为兼容旧签名保留：进度由批次协议的
        ``(generation, last_seq)`` 维护，适配层不得各自推进游标。

        失败隔离：进程异常仅 warning 并返回空 :class:`ExtractionResult`；
        模型/解析失败通过 ``failed_tracks`` 明确报告，不伪装成空成功。
        """
        if not session.messages:
            return ExtractionResult()
        return await self._submit_session_request(
            session,
            source=source,
            start_hint=max(0, int(last_extracted_index)),
        )

    def _has_enough_idle_signal(self, messages: list[dict[str, Any]]) -> bool:
        """idle 抽取的信号门槛：只拦「明显不值得抽」的切片。

        - 条数：不足 :data:`IDLE_MIN_NEW_MESSAGES` 条 → 无内容可抽（例如只有一条
          尚未得到回复的 user 消息）。
        - 内容：切片只有一条 user 消息、其文本短于 :data:`IDLE_MIN_SINGLE_USER_CHARS`
          且切片内无工具调用 → 无实质内容（例如「你好」）。有工具调用则豁免。

        用 :func:`_content_text` 归一化 content：真实转录的 content 可能是
        content-block 列表，直接 ``.strip()`` 会抛 ``AttributeError``。
        """
        if len(messages) < self.IDLE_MIN_NEW_MESSAGES:
            return False
        user_msgs = [m for m in messages if m.get("role") == "user"]
        if len(user_msgs) == 1:
            content = _content_text(user_msgs[0].get("content")).strip()
            has_tool_calls = any(m.get("tool_calls") for m in messages)
            if len(content) < self.IDLE_MIN_SINGLE_USER_CHARS and not has_tool_calls:
                return False
        return True

    async def run_idle_extraction(
        self,
        session: Session,
    ) -> ExtractionResult:
        """Idle 定时器触发的薄适配：按 ``(generation, last_seq)`` 提交提取请求。

        行为契约：
        - ``through_seq <= last_seq``（无新增消息或已覆盖）：return 空结果，
          不调 LLM、不动进度。
        - 新增切片未过信号门槛（``_has_enough_idle_signal``）：return 空结果，
          **不动进度** —— 被跳过的消息留在待消费窗口内，用户继续发言时会随
          下一段切片一并抽取。
        - 通过门槛后委托 :meth:`process_request`：两轨各自 checkpoint，两轨就绪
          才在同一事务写入事实/情节并推进 ``last_seq``。本方法不再单独推进任何
          游标。
        - 任何异常仅 warning，返回空 :class:`ExtractionResult`（不静默吞 LLM 失败：
          失败会以 ``failed_tracks`` 报告）。

        Args:
            session: 当前会话；其 ``messages`` 是当前转录（hook 传的是重建转录，
                持久坐标由 :meth:`load_session` 补齐）。

        Returns:
            :class:`ExtractionResult`。无新增消息或失败时是空结果。
        """
        session_key = session.key
        try:
            resolved = self._resolve_session(session)
            generation = resolved.memory_generation
            through = self._through_seq(resolved)
            last_seq = self._last_consumed_seq(resolved, generation)
            logger.info(
                "idle extraction start for session {}{}: through_seq={} last_seq={}",
                session_key,
                session_label_suffix(session_label(session.messages, session.metadata)),
                through,
                last_seq,
            )
            if through <= last_seq:
                logger.info(
                    "idle extraction skipped for session {}: no new messages ({} <= {})",
                    session_key,
                    through,
                    last_seq,
                )
                return ExtractionResult()

            # 信号门槛：琐碎切片不调 LLM，否则一句「你好」也会触发两路 LLM 提取。
            # 刻意**不推进 last_seq** —— 被跳过的消息留在待消费窗口内，用户继续
            # 发言时会随下一段切片一并抽取。
            new_slice = [
                message
                for seq, message in self._slice_evidence(resolved, last_seq, through)
            ]
            if not self._has_enough_idle_signal(new_slice):
                logger.info(
                    "idle extraction skipped for session {}: {} new message(s) below "
                    "signal threshold (min {} messages; single user turn needs "
                    ">= {} chars or a tool call)",
                    session_key,
                    len(new_slice),
                    self.IDLE_MIN_NEW_MESSAGES,
                    self.IDLE_MIN_SINGLE_USER_CHARS,
                )
                return ExtractionResult()

            return await self._submit_session_request(resolved, source="idle")
        except Exception as exc:  # noqa: BLE001 - 整个路径绝不上抛
            logger.opt(exception=exc).warning(
                "run_idle_extraction failed for session {}: {}", session_key, exc
            )
            return ExtractionResult()

    def reset_session_extraction_state(self, session_key: str) -> None:
        """删除指定会话的 idle 增量游标（admin/测试用，失败仅 warning）。"""
        try:
            with self.database.connect() as conn:
                reset_extraction_state(conn, session_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "reset_session_extraction_state failed for {}: {}", session_key, exc
            )

    # ------------------------------------------------------------------
    # 阶段1：系统提取（无 LLM）
    # ------------------------------------------------------------------

    def _system_extract(self, session: Session) -> SystemExtractionResult:
        if not session.messages:
            return SystemExtractionResult()
        # 工具
        action_nodes = _collect_action_nodes(session.messages)
        # 对话内容
        rule_signals = _collect_rule_signals(session.messages)
        with self.database.connect() as conn:
            # 快照
            snapshot = get_scratchpad(conn, self.user_id, self.workspace_id)
        return SystemExtractionResult(
            action_nodes=action_nodes,
            rule_signals=rule_signals,
            scratchpad_snapshot=snapshot,
        )

    # ------------------------------------------------------------------
    # 阶段2：LLM 提取（并发 + 失败隔离）
    # ------------------------------------------------------------------

    async def _llm_extract(
        self,
        session: Session,
        system: SystemExtractionResult,
        *,
        tracks: Sequence[str] = ("semantic", "episode"),
    ) -> LLMExtractionResult:
        """并发跑指定轨道并解析结果；``tracks`` 让批次只重试失败轨。"""
        run_semantic = "semantic" in tracks
        run_episode = "episode" in tracks
        semantic_messages = (
            self._build_prompt_messages(SEMANTIC_EXTRACTION_PROMPT, session, system)
            if run_semantic
            else []
        )
        episode_messages = (
            self._build_prompt_messages(EPISODE_EXTRACTION_PROMPT, session, system)
            if run_episode
            else []
        )

        # semantic 路交给 ProfileExtractor（S3 Track 1）：复用上面同一份 prompt
        # （含阶段1 系统提取结果），一次调用同时拿到 memories / experiences。
        raw = await asyncio.gather(
            (
                self.profile_extractor.extract(
                    transcript=session.messages,
                    episode_id="",
                    prompt_messages=semantic_messages,
                    timeout=self.LLM_TIMEOUT,
                )
                if run_semantic
                else _skipped_track()
            ),
            self._call_track("episode", episode_messages) if run_episode else _skipped_track(),
            return_exceptions=True,
        )

        failed_tracks: list[str] = []
        track_errors: dict[str, str] = {}
        profile: ProfileExtractionResult | None = None
        episode_payload: dict[str, Any] | None = None

        profile_raw, episode_raw = raw[0], raw[1]

        if run_semantic:
            if isinstance(profile_raw, BaseException):
                failed_tracks.append("semantic")
                track_errors["semantic"] = f"raised: {profile_raw}"
                logger.warning("memory extraction track semantic raised: {}", profile_raw)
            else:
                profile = cast(ProfileExtractionResult | None, profile_raw)
                if profile is not None and profile.error is not None:
                    failed_tracks.append("semantic")
                    track_errors["semantic"] = profile.error
                    profile = None

        if run_episode:
            if isinstance(episode_raw, BaseException):
                failed_tracks.append("episode")
                track_errors["episode"] = f"raised: {episode_raw}"
                logger.warning("memory extraction track episode raised: {}", episode_raw)
            else:
                payload_raw, error = cast(
                    "tuple[dict[str, Any] | None, str | None]", episode_raw
                )
                if error is not None:
                    failed_tracks.append("episode")
                    track_errors["episode"] = error
                episode_payload = payload_raw

        memories: list[LLMMemoryItem] = []
        if profile is not None:
            memories.extend(_profile_item_to_llm(item) for item in profile.items)
            memories.extend(_profile_item_to_llm(item) for item in profile.experiences)

        episode = _coerce_episode_item(episode_payload) if episode_payload is not None else None
        semantic_payload = (
            {"memories": [_memory_item_to_dict(item) for item in memories]}
            if run_semantic and "semantic" not in failed_tracks
            else None
        )

        return LLMExtractionResult(
            memories=memories,
            episode=episode,
            failed_tracks=failed_tracks,
            action_nodes=list(system.action_nodes),
            attempted_tracks=[track for track in tracks],
            track_errors=track_errors,
            semantic_payload=semantic_payload,
            # 有效空输出（模型明确没有内容）落 ``{}``，仍算成功 checkpoint。
            episode_payload=episode_payload or {} if run_episode and "episode" not in failed_tracks else None,
        )

    async def _call_track(
        self,
        track: str,
        messages: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str | None]:
        """调用单路 LLM 并解析 JSON。

        Returns:
            ``(payload, error)``：成功为 (dict, None)，无内容为 (None, None)，
            失败为 (None, <原因>)。
        """
        try:
            response = await asyncio.wait_for(
                self.runtime.provider.chat_with_retry(
                    model=self.runtime.model,
                    messages=messages,
                    tools=[],
                    temperature=self.runtime.generation.temperature,
                    max_tokens=self.runtime.generation.max_tokens,
                    reasoning_effort=self.runtime.generation.reasoning_effort,
                ),
                timeout=self.LLM_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 - 失败隔离：任何异常都不上抛
            logger.warning("memory extraction LLM call failed ({}): {}", track, exc)
            return None, f"call_failed: {exc}"

        # provider 把 HTTP 错误（401/403/429/5xx）包装成 content 返回，不抛异常。
        # 必须先识别，否则错误文本会被当成「模型回了段没法解析的文字」。
        reason = response_error(response)
        if reason is not None:
            logger.warning("memory extraction LLM call failed ({}): {}", track, reason)
            return None, f"call_failed: {reason}"

        content = getattr(response, "content", None)
        if not content or not content.strip():
            return None, None
        if content.strip().upper() == "NONE":
            return None, None
        payload = _parse_json_object(content)
        if payload is None:
            logger.warning(
                "memory extraction LLM returned unparseable JSON ({}; len={}; head={!r})",
                track,
                len(content),
                content[:400],
            )
            return None, "invalid_json"
        return payload, None

    def _build_prompt_messages(
        self,
        prompt: str,
        session: Session,
        system: SystemExtractionResult,
        *,
        transcript_max_chars: int = 8000,
        transcript_head_ratio: float = _TRANSCRIPT_HEAD_RATIO,
    ) -> list[dict[str, Any]]:
        transcript = _render_transcript(
            session.messages,
            max_chars=transcript_max_chars,
            head_ratio=transcript_head_ratio,
        )
        action_summary = json.dumps(
            [node.to_dict() for node in system.action_nodes], ensure_ascii=False
        )
        rules = json.dumps(system.rule_signals, ensure_ascii=False)
        scratchpad_text = _render_scratchpad(system.scratchpad_snapshot)
        body = (
            f"{prompt}\n\n"
            f"## 对话内容\n\n{transcript}\n\n"
            f"## 阶段1 系统提取结果（参考用，可忽略）\n\n"
            f"- 工具调用摘要: {action_summary}\n"
            f"- 规则信号命中: {rules}\n"
            f"- 当前 scratchpad 快照: {scratchpad_text}"
        )
        return [{"role": "user", "content": body}]

    # ------------------------------------------------------------------
    # 阶段3：防污染过滤
    # ------------------------------------------------------------------

    @staticmethod
    def _row_workspace_id(session: Session) -> str:
        """会话工作区对应的记忆隔离键；无工作区时归全局。"""
        return session_memory_workspace_id(session)

    def _apply_filters(self, llm_result: LLMExtractionResult) -> FilteredExtractionResult:
        """阶段3：只保留候选合法性判断，不再做写入决策（D08）。

        保留：任务产物/AI 自答过滤、合法 type/priority、批内精确去重。
        去掉：与既有记忆的精确哈希丢弃、字符相似度 >0.8 丢弃——重复、纠正、
        冲突、多值全部交给 :func:`apply_memory_changes` 的决策表。
        """
        kept: list[LLMMemoryItem] = []
        seen_hashes: set[str] = set()
        skipped_reasons: dict[str, int] = {}

        def _skip(reason: str) -> None:
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1

        for item in llm_result.memories:
            content = item.content
            if is_task_artifact(content) or is_ai_self_talk(content):
                _skip("task_artifact_or_ai_self_talk")
                continue

            type_key = item.type.strip().lower()
            if type_key not in _VALID_TYPES:
                logger.warning("skipping memory with invalid type {!r}", item.type)
                _skip("invalid_type")
                continue

            priority_raw = (item.priority or "").strip().lower()
            if priority_raw and priority_raw not in _VALID_PRIORITIES:
                logger.warning("skipping memory with invalid priority {!r}", item.priority)
                _skip("invalid_priority")
                continue

            content_hash = compute_content_hash(content, item.subject, item.predicate)
            if content_hash in seen_hashes:
                _skip("duplicate_in_batch")
                continue

            seen_hashes.add(content_hash)
            kept.append(item)

        if skipped_reasons:
            logger.info("memory extraction skipped breakdown: {}", skipped_reasons)

        return FilteredExtractionResult(
            memories=kept,
            episode=llm_result.episode,
            action_nodes=list(llm_result.action_nodes),
            skipped_reasons=skipped_reasons,
        )

    # ------------------------------------------------------------------
    # 阶段4：持久化
    # ------------------------------------------------------------------

    def _persist(
        self,
        filtered: FilteredExtractionResult,
        session: Session,
        source: str,
    ) -> PersistenceResult:
        """单事务写入事实/情节（旧调用点保留；失败直接抛出，不再落 fallback）。

        批次协议路径（:meth:`_finalize_batch`）复用 :meth:`_write_extraction`，
        把 ``last_seq`` 与批次完成状态并入同一事务。
        """
        now = self._now_iso()
        with self.database.connect() as conn:
            persisted, created = self._write_extraction(
                conn,
                filtered,
                session=session,
                source=source,
                now=now,
                evidence_key=f"legacy:{session.key}:{now}",
            )
        for memory in created:
            index_memory_best_effort(memory)
        return persisted

    def _write_extraction(
        self,
        conn: sqlite3.Connection,
        filtered: FilteredExtractionResult,
        *,
        session: Session,
        source: str,
        now: str,
        evidence_key: str = "",
    ) -> tuple[PersistenceResult, list[Memory]]:
        """在调用方事务内写入本批事实与情节，返回结果与待建索引的新记忆。

        事实/记忆的写入决策全部委托 :func:`apply_memory_changes`（D08：写入判定
        只有一个位置）。本方法只负责构造变更、写 episode、回填来源关联。

        不 commit、不调 LLM、不刷新向量/MD：事务由调用方持有（契约 §2.6）。
        任何写入异常直接上抛，由调用方回滚并返回结构化失败回执；
        D07 之后不再有「单条落 fallback」的降级路径。
        """
        row_workspace_id = self._row_workspace_id(session)
        provenance = extraction_provenance(session, source, workspace_id=row_workspace_id)
        provenance["store_workspace_id"] = self.workspace_id
        saved_episode_ids: list[str] = []

        # 归属判定结论必须落在日志里：记忆隔离按「写进哪个 workspace_id」生效，
        # 归属算错时面板归属、可见集合、召回都会跟着错，而此前这条结论只在
        # 数据库里——排查必须回查 DB。project_path 一并打出，便于把 id 对回目录。
        scope_raw = session.metadata.get("workspace_scope")
        scope = cast(dict[str, Any], scope_raw) if isinstance(scope_raw, dict) else None
        project_path = scope.get("project_path") if scope is not None else None
        logger.info(
            "memory attribution resolved: session={} workspace_id={} project_path={} source={}",
            session.key,
            row_workspace_id,
            project_path or "<none/global>",
            source,
        )

        # 阶段 4a：事实/记忆经统一写入决策表落库（幂等、纠正、冲突、多值）。
        changes = [
            MemoryChange(
                memory=Memory(
                    id=str(uuid4()),
                    content=item.content,
                    type=MemoryType(item.type.strip().lower()),
                    priority=_resolve_priority(item),
                    source="extraction",
                    importance_score=item.importance,
                    tags=list(item.tags),
                    subject=item.subject,
                    predicate=item.predicate,
                    workspace_id=row_workspace_id,
                    user_id=self.user_id,
                    created_at=now,
                    updated_at=now,
                    metadata={"provenance": provenance},
                )
            )
            for item in filtered.memories
        ]
        written = apply_memory_changes(conn, changes, evidence_key=evidence_key)
        saved_memory_ids = list(written.memory_ids)
        created_memories = list(written.created)
        memory_by_subject: dict[str, str] = {}
        for item, outcome in zip(filtered.memories, written.outcomes, strict=False):
            if item.subject and outcome.memory_id:
                memory_by_subject.setdefault(item.subject, outcome.memory_id)

        # 阶段 4b：反向关联
        episode = filtered.episode
        linked_ids: list[str] = []
        semantic_ids: list[str] = []
        if episode is not None:
            # 语义关联：episode.entities 命中本批次记忆的 subject
            for entity in episode.entities:
                memory_id = memory_by_subject.get(entity)
                if memory_id is not None and memory_id not in semantic_ids:
                    semantic_ids.append(memory_id)
            # 兜底：本批次产出的记忆一律挂到本情节上。此前只靠
            # entities ↔ subject 的字符串相等，而抽取 prompt 从不要求 subject
            # → subject 常为空 → 关联整条断裂，工作台与 Episodes 页签恒为
            # 「0 条关联记忆」，记忆的 source_episode_id 也一直是空的。
            linked_ids.extend(saved_memory_ids)
            for memory_id in semantic_ids:
                if memory_id not in linked_ids:
                    linked_ids.append(memory_id)
            logger.info(
                "memory episode linkage: session={} semantic={} batch={} total={}",
                session.key,
                len(semantic_ids),
                len(saved_memory_ids),
                len(linked_ids),
            )

        # 阶段 4c：写入 episode（有效空输出也写，仅 action_nodes）
        if episode is not None or filtered.action_nodes:
            episode_id = str(uuid4())
            record = Episode(
                id=episode_id,
                session_id=session.key,
                workspace_id=row_workspace_id,
                summary=episode.summary if episode is not None else "",
                goal=episode.goal if episode is not None else "",
                outcome=_normalize_outcome(episode.outcome if episode is not None else None),
                source=self._map_source(source),
                action_nodes=[node.to_dict() for node in filtered.action_nodes],
                entities=list(episode.entities) if episode is not None else [],
                tools_used=list(episode.tools_used) if episode is not None else [],
                linked_memory_ids=linked_ids,
                importance_score=episode.importance if episode is not None else 0.7,
                started_at=_iso(session.created_at),
                ended_at=now,
                metadata={"provenance": provenance},
            )
            add_episode(conn, record)
            saved_episode_ids.append(episode_id)
            # FIX-6 Rev-M-4：反向回填 source_episode_id（同一事务）。
            # 这些记忆都来自本批（新建或合并命中），写入失败即整批回滚。
            for memory_id in linked_ids:
                update_memory_source_episode(conn, memory_id, episode_id)

        logger.info(
            "memory extraction persisted: workspace_id={} memories={} ids={} episodes={}",
            row_workspace_id,
            len(saved_memory_ids),
            saved_memory_ids,
            len(saved_episode_ids),
        )
        return (
            PersistenceResult(
                memory_ids=saved_memory_ids,
                episode_ids=saved_episode_ids,
                created_count=len(created_memories),
            ),
            created_memories,
        )

    @staticmethod
    def _map_source(source: str) -> EpisodeSource:
        mapped = _SOURCE_MAP.get(source)
        if mapped is None:
            logger.warning("unknown extraction source {!r}, fallback to session_end", source)
            return EpisodeSource.SESSION_END
        return mapped
