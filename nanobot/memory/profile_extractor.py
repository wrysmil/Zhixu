"""用户画像提取。

S3 Track 1: 复用 SEMANTIC_EXTRACTION_PROMPT（取 memories / experiences 字段）。

画像合并/冲突判定已收敛到 :mod:`nanobot.memory.writer` 的写入决策表（D08），
本模块只负责调用模型、解析候选。

引用评分（``citation_scores`` / ``useful``）已随 D11 删除：它的唯一消费者是
``access_count`` 写回，而排序不再读 ``access_count``（spec §6.3），
继续让模型逐条评判 usefulness 只会白花 token 并给出无人消费的产出。
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, cast

from loguru import logger

from nanobot.memory.llm_error import response_error
from nanobot.memory.prompts import SEMANTIC_EXTRACTION_PROMPT


@dataclass
class ProfileItem:
    content: str
    subject: str
    predicate: str
    type: str
    importance: float
    #: LLM 未给出 priority 时保持 ``None``，让下游 ``_resolve_priority`` 的兜底
    #: 推断（含时间关键词 → short_term）仍能生效。切勿默认成 "long_term"。
    priority: str | None
    tags: list[str]
    is_update: bool = False
    evidence: str = ""


@dataclass
class ProfileExtractionResult:
    """Track 1 单次调用的产出。

    ``experiences`` 与 ``items`` 同源：``SEMANTIC_EXTRACTION_PROMPT`` 是双轨输出，
    一次调用同时返回 ``memories`` 与 ``experiences``，拆开解析可免第二次 LLM 往返。

    ``error`` 非空表示这一路失败（调用异常 / JSON 不可解析），供调用方标记
    ``failed_tracks``。无内容（空响应或 "NONE"）不算失败，返回全空且 ``error=None``。
    """

    items: list[ProfileItem] = field(default_factory=list)
    experiences: list[ProfileItem] = field(default_factory=list)
    error: str | None = None
    rejected_memories: int = 0
    rejected_experiences: int = 0


class ProfileExtractor:
    """S3 Track 1：用户画像 + 经验。"""

    def __init__(self, runtime: Any | None = None, model: str = "") -> None:
        self._runtime = runtime
        self._model = model

    async def extract(
        self,
        transcript: list[dict[str, Any]],
        episode_id: str,
        *,
        prompt_messages: list[dict[str, Any]] | None = None,
        timeout: float | None = None,
    ) -> ProfileExtractionResult:
        """抽取画像（+ 经验）。

        Args:
            transcript: 本批对话转录；始终用于验证画像的用户原文证据。
            episode_id: 关联的情节 id（当前仅透传，不参与 prompt）。
            prompt_messages: 调用方已拼好的 prompt。传入时**原样使用**，不再自行拼接
                —— 让调用方复用带阶段1 系统提取结果的 prompt，避免两套拼装逻辑分叉。
            timeout: 单次 LLM 调用超时（秒）。``None`` 表示不额外设限。调用方应传入
                与 ``_call_track`` 一致的 ``LLM_TIMEOUT``，避免悬挂调用拖死整条流水线。

        Returns:
            :class:`ProfileExtractionResult`。失败隔离：任何异常仅 warning，
            ``error`` 记录原因，绝不上抛。
        """
        if self._runtime is None:
            return ProfileExtractionResult()
        try:
            if prompt_messages is not None:
                messages = prompt_messages
            else:
                conv_text = _format_conv_lines(transcript)
                # SEMANTIC_EXTRACTION_PROMPT 为指令式，无 {conversation} 占位符
                prompt = f"{SEMANTIC_EXTRACTION_PROMPT}\n\n### 对话转录\n{conv_text}"
                messages = [{"role": "user", "content": prompt}]

            call = self._runtime.provider.chat_with_retry(
                model=self._model or getattr(self._runtime, "model", ""),
                messages=messages,
                tools=[],
                temperature=getattr(self._runtime.generation, "temperature", 0.0),
                max_tokens=getattr(self._runtime.generation, "max_tokens", 1000),
                reasoning_effort=getattr(self._runtime.generation, "reasoning_effort", None),
            )
            resp = await asyncio.wait_for(call, timeout=timeout) if timeout else await call
            # provider 把 HTTP 错误包装成 content 返回（不抛异常），先识别。
            error = response_error(resp)
            if error is not None:
                logger.warning("ProfileExtractor.extract LLM call failed: {}", error)
                return ProfileExtractionResult(error=f"call_failed: {error}")
            text = (getattr(resp, "content", "") or "").strip()
            if not text or text.upper() == "NONE":
                return ProfileExtractionResult()
            data = _extract_json_obj(text)
            if not isinstance(data, dict):
                # 兼容旧式「纯 JSON 数组」输出；两者都解析不出才算解析失败，
                # 需与 ``_call_track`` 的 ``invalid_json`` 语义保持一致。
                legacy = _parse_legacy_array(text)
                if legacy:
                    grounded = _grounded_items(legacy, transcript)
                    return ProfileExtractionResult(
                        items=grounded,
                        rejected_memories=len(legacy) - len(grounded),
                    )
                return ProfileExtractionResult(error="invalid_json")
            parsed_memories = _parse_items(data.get("memories"))
            parsed_experiences = [
                item for item in _parse_items(data.get("experiences"))
                if item.type in {"EXPERIENCE", "SKILL", "ERROR"}
            ]
            grounded_memories = _grounded_items(parsed_memories, transcript)
            grounded_experiences = _grounded_experiences(parsed_experiences, transcript)
            return ProfileExtractionResult(
                items=grounded_memories,
                experiences=grounded_experiences,
                rejected_memories=len(parsed_memories) - len(grounded_memories),
                rejected_experiences=len(parsed_experiences) - len(grounded_experiences),
            )
        except Exception as exc:
            logger.warning("ProfileExtractor.extract failed: {}", exc)
            return ProfileExtractionResult(error=f"call_failed: {exc}")


def _parse_items(raw_items: Any) -> list[ProfileItem]:
    if not isinstance(raw_items, list):
        return []
    return [_item_from_dict(i) for i in raw_items if isinstance(i, dict)]


def _item_from_dict(raw: dict[str, Any]) -> ProfileItem:
    raw_priority = raw.get("priority")
    return ProfileItem(
        content=str(raw.get("content", "")),
        subject=str(raw.get("subject", "")),
        predicate=str(raw.get("predicate", "")),
        type=str(raw.get("type", "FACT")).upper(),
        importance=float(raw.get("importance", 0.5)),
        priority=str(raw_priority).lower() if raw_priority else None,
        tags=list(raw.get("tags") or []),
        is_update=bool(raw.get("is_update", False)),
        evidence=raw.get("evidence", "") if isinstance(raw.get("evidence"), str) else "",
    )


_RECALL_REQUEST = re.compile(
    r"^(?:(?:请|帮我|麻烦|你|您|能否|能不能|是否|还|可以)[，,\s]*)*"
    r"(?:还记得|记不记得|回忆|回顾|记忆里|之前说过什么|记得(?=.*(?:吗|么|哪|谁|是否)))"
    r"|^(?:do you (?:still )?remember|can you (?:recall|remember))\b",
    re.IGNORECASE,
)

_TASK_OR_QUERY_CLAUSE = re.compile(
    r"(?:你能|您能|能否|能不能|可以|请你|请帮我|麻烦|帮我)"
    r".{0,80}(?:找回|找到|回忆|说明|核对|检查|分析|列出|总结|查|搜索|处理|提供|确认)"
    r"|(?:我们|我|用户)(?:以前|之前|上次|曾经)"
    r".{0,30}(?:处理过|做过|遇到过|讨论过|聊过).{0,20}(?:类似|这个问题|这件事|同类|相关)"
    r"|(?:并)?说明这次需要先核对哪些证据",
    re.IGNORECASE,
)

_DIRECT_TASK_CLAUSE = re.compile(
    r"^(?:请|请你|帮我|麻烦你?)(?:检查|核对|分析|列出|总结|处理|提供|确认|查找|搜索|使用|打开|执行|运行|生成|写|创建|修改|删除|修复|排查)",
    re.IGNORECASE,
)

_QUESTION_CLAUSE = re.compile(
    r"^(?:能|可以|是否|要不要|能否|能不能).*(?:吗|么|？|\?)$",
    re.IGNORECASE,
)


def _is_non_assertion_clause(clause: str) -> bool:
    text = clause.strip()
    return bool(
        _RECALL_REQUEST.search(text)
        or _TASK_OR_QUERY_CLAUSE.search(text)
        or _DIRECT_TASK_CLAUSE.search(text)
        or _QUESTION_CLAUSE.search(text)
    )


def _assertion_sources(content: str) -> list[str]:
    """Return user/tool text with recall/task/question clauses removed.

    Keeping the punctuated remainder (instead of only individual clauses)
    lets an independent fact that spans a comma remain contiguous for exact
    evidence matching, while still preventing a recall request from becoming
    a profile fact.
    """
    clauses = re.split(r"[。！？!?；;\n，,]", content)
    if not any(_is_non_assertion_clause(clause) for clause in clauses):
        return [content]
    safe = content
    for clause in clauses:
        if clause.strip() and _is_non_assertion_clause(clause):
            safe = safe.replace(clause, " ")
    return [safe, *[clause for clause in clauses if clause.strip() and not _is_non_assertion_clause(clause)]]


def _grounded_items(items: list[ProfileItem], transcript: list[dict[str, Any]]) -> list[ProfileItem]:
    """仅接收本批用户原文证据；检索与助手复述不能成为新的画像来源。

    对明确的回忆提问按分句排除，避免从问题里的“猫”反推名字、住所等旧画像。
    保留同一句请求中独立陈述的新事实，不按问号过滤全部用户消息。
    此处验证来源边界，不尝试用字符串规则判定任意语义蕴含。
    """
    sources: list[str] = []
    for message in transcript:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            if not isinstance(content, list):
                continue
            texts: list[str] = []
            for raw_part in cast(list[Any], content):
                if not isinstance(raw_part, dict):
                    continue
                part = cast(dict[str, Any], raw_part)
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
            content = "\n".join(texts)
        sources.extend(_assertion_sources(content))
    accepted = [item for item in items if item.evidence.strip()
                and any(item.evidence.strip() in source for source in sources)]
    if len(accepted) != len(items):
        logger.info("profile candidates rejected without current user evidence: {}", len(items) - len(accepted))
    return accepted


def _grounded_experiences(
    items: list[ProfileItem], transcript: list[dict[str, Any]]
) -> list[ProfileItem]:
    """Experiences need evidence from user text or a tool result.

    Assistant prose, plans and generic advice are not proof that a procedure
    worked. A user task request ("请检查日志") is not evidence of a reusable
    experience either; the evidence must be the statement/result that supports
    the claimed lesson.
    """
    sources: list[str] = []
    for message in transcript:
        role = message.get("role")
        if role not in {"user", "tool"}:
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        sources.extend(_assertion_sources(content))
    accepted = [
        item for item in items
        if item.evidence.strip() and any(item.evidence.strip() in source for source in sources)
    ]
    if len(accepted) != len(items):
        logger.info("experience candidates rejected without verified evidence: {}", len(items) - len(accepted))
    return accepted


def _format_conv_lines(transcript: list[dict]) -> str:
    lines: list[str] = []
    for t in transcript[-30:]:
        content = (t.get("content", "") or "")[:1500]
        if content:
            role = "用户" if t.get("role") == "user" else "助手"
            lines.append(f"[{role}]: {content}")
    return "\n".join(lines)


def _extract_json_obj(text: str) -> Any | None:
    """从尾部锚定提取 JSON 对象（处理 prompt 模板示例的干扰花括号）。"""
    import json as _json

    # 从尾部尝试 parse，每个 } 位置都试一次
    for end_idx in range(len(text) - 1, -1, -1):
        if text[end_idx] != "}":
            continue
        # 找最近的 { 起点
        depth = 0
        start_idx = -1
        for i in range(end_idx, -1, -1):
            if text[i] == "}":
                depth += 1
            elif text[i] == "{":
                depth -= 1
                if depth == 0:
                    start_idx = i
                    break
        if start_idx < 0:
            continue
        candidate = text[start_idx:end_idx + 1]
        try:
            return _json.loads(candidate)
        except Exception:
            continue
    return None


def _parse_legacy_array(text: str) -> list[ProfileItem]:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    return [_item_from_dict(i) for i in arr if isinstance(i, dict)]
