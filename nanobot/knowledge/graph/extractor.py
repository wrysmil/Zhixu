"""入库异步图谱抽取：严格 JSON + 守卫 + 有界重试。

输出严格 JSON：
``{"entities":[{name,type,description}], "relations":[{source,target,relation,description,strength}]}``
模型说「没有实体」时返回 ``declined=True``，调用方记 ``skipped`` 且不重试。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx
import json_repair
from loguru import logger

from nanobot.knowledge.graph.normalize import normalize_entity_name
from nanobot.knowledge.graph.prompts import (
    DEFAULT_GRAPH_EXTRACTION_PROMPT,
    DEFAULT_QUERY_ENTITY_SYSTEM_PROMPT,
    DEFAULT_QUERY_ENTITY_USER_PROMPT,
)

PROMPT_VERSION = "graph1"


def _render_extraction_prompt(
    template: str, heading_path: str, content: str
) -> str:
    # 不用 str.format：Prompt 里含 JSON 花括号，format 会把它当占位符。
    return template.replace("<<heading>>", heading_path or "无").replace(
        "<<content>>", content
    )


def _render_query_user_prompt(template: str, query: str) -> str:
    return template.replace("<<query>>", query.strip())


_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)
_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

_PRONOUNS = frozenset(
    {
        "它",
        "他",
        "她",
        "它们",
        "他们",
        "她们",
        "这个",
        "那个",
        "这些",
        "那些",
        "其",
        "该",
        "this",
        "that",
        "these",
        "those",
        "it",
        "they",
        "he",
        "she",
    }
)


class GraphExtractionError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    #: (name, type, description)
    entities: tuple[tuple[str, str, str], ...]
    #: (source_name, target_name, relation, description, strength)
    relations: tuple[tuple[str, str, str, str, float], ...]
    declined: bool = False


class GraphExtractor(Protocol):
    @property
    def model_id(self) -> str: ...

    def extract(self, heading_path: str, content: str) -> ExtractionResult: ...


def _redact(text: str, secret: str | None = None) -> str:
    redacted = re.sub(r"(?i)(bearer\s+)\S+", r"\1***", text)
    redacted = re.sub(r"sk-[A-Za-z0-9_\-]{4,}", "sk-***", redacted)
    if secret:
        redacted = redacted.replace(secret, "***")
    return redacted


def clean_model_text(text: str) -> str:
    """剥掉推理模型的 `<think>` 与 Markdown 围栏，只留待解析正文。"""
    cleaned = _THINK_BLOCK.sub("", text or "")
    cleaned = _JSON_FENCE.sub("", cleaned.strip())
    return cleaned.strip()


def parse_extraction(
    text: str,
    *,
    max_entities: int = 20,
    max_relations: int = 30,
) -> ExtractionResult:
    try:
        payload = json_repair.loads(text)
    except Exception as exc:  # noqa: BLE001 - 模型输出不可信
        raise GraphExtractionError("parse") from exc
    if not isinstance(payload, dict):
        raise GraphExtractionError("parse")
    data = cast(dict[str, Any], payload)
    raw_entities = data.get("entities")
    if not isinstance(raw_entities, list) or not raw_entities:
        return ExtractionResult((), (), declined=True)

    entities: list[tuple[str, str, str]] = []
    kept_norms: set[str] = set()
    for item in raw_entities:
        if len(entities) >= max_entities:
            break
        if not isinstance(item, dict):
            continue
        entity = cast(dict[str, Any], item)
        name = str(entity.get("name") or "").strip()
        norm = normalize_entity_name(name)
        if not name or len(name) > 40 or not norm or norm in _PRONOUNS or norm in kept_norms:
            continue
        kept_norms.add(norm)
        entities.append(
            (
                name,
                str(entity.get("type") or "concept").strip()[:40] or "concept",
                str(entity.get("description") or "").strip()[:500],
            )
        )

    relations: list[tuple[str, str, str, str, float]] = []
    raw_relations = data.get("relations")
    if isinstance(raw_relations, list):
        for item in raw_relations:
            if len(relations) >= max_relations:
                break
            if not isinstance(item, dict):
                continue
            relation = cast(dict[str, Any], item)
            source = normalize_entity_name(str(relation.get("source") or ""))
            target = normalize_entity_name(str(relation.get("target") or ""))
            if source not in kept_norms or target not in kept_norms:
                continue
            name = str(relation.get("relation") or "").strip()
            if not name:
                continue
            try:
                strength = float(relation.get("strength") or 5.0)
            except (TypeError, ValueError):
                strength = 5.0
            relations.append(
                (
                    source,
                    target,
                    name[:40],
                    str(relation.get("description") or "").strip()[:500],
                    max(1.0, min(10.0, strength)),
                )
            )
    return ExtractionResult(tuple(entities), tuple(relations))


def parse_query_entities(
    text: str, *, max_entities: int = 8
) -> tuple[str, ...]:
    """查询侧实体名解析；兼容 WeKnora `[{"entity": ...}]` 与字符串/entities 列表。"""
    try:
        payload = json_repair.loads(text)
    except Exception as exc:  # noqa: BLE001 - 模型输出不可信
        raise GraphExtractionError("parse") from exc
    if isinstance(payload, dict):
        raw = payload.get("entities")
        if raw is None and any(key in payload for key in ("entity", "name", "title")):
            raw = [payload]
    elif isinstance(payload, list):
        raw = payload
    else:
        raise GraphExtractionError("parse")
    if not isinstance(raw, list):
        raise GraphExtractionError("parse")
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            entity = cast(dict[str, Any], item)
            name = (
                entity.get("entity")
                or entity.get("name")
                or entity.get("title")
                or entity.get("text")
                or ""
            )
        else:
            continue
        text_name = str(name).strip()
        norm = normalize_entity_name(text_name)
        if (
            not text_name
            or len(text_name) > 40
            or not norm
            or norm in _PRONOUNS
            or norm in seen
        ):
            continue
        seen.add(norm)
        names.append(text_name)
        if len(names) >= max(1, max_entities):
            break
    return tuple(names)


def _build_client(timeout: float) -> httpx.Client:
    """默认信任环境代理；本机代理变量非法时退回直连，避免入库整体失败。"""
    try:
        return httpx.Client(timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - 代理配置异常不应阻断抽取
        logger.warning("knowledge graph http client fallback to direct: {!r}", exc)
        return httpx.Client(timeout=timeout, trust_env=False)


class OpenAICompatGraphExtractor:
    """OpenAI 兼容 ``/chat/completions`` 同步客户端（入库线程内调用）。"""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        api_key_env: str = "",
        timeout_seconds: float = 30.0,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        max_context_chars: int = 1200,
        max_retries: int = 1,
        max_entities: int = 20,
        max_relations: int = 30,
        thinking: str = "disabled",
        extraction_prompt: str = DEFAULT_GRAPH_EXTRACTION_PROMPT,
        query_entity_system_prompt: str = DEFAULT_QUERY_ENTITY_SYSTEM_PROMPT,
        query_entity_user_prompt: str = DEFAULT_QUERY_ENTITY_USER_PROMPT,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._timeout = timeout_seconds
        self._max_tokens = max(1, max_tokens)
        self._temperature = temperature
        self._max_context = max(1, max_context_chars)
        self._retries = max(0, max_retries)
        self._max_entities = max(1, max_entities)
        self._max_relations = max(1, max_relations)
        self._thinking = (
            thinking if thinking in ("auto", "enabled", "disabled") else "disabled"
        )
        self._extraction_prompt = extraction_prompt
        self._query_entity_system_prompt = query_entity_system_prompt
        self._query_entity_user_prompt = query_entity_user_prompt
        self._client = client or _build_client(timeout_seconds)
        self._active_key: str | None = None

    @property
    def model_id(self) -> str:
        return f"{self._base_url}#{self._model}"

    def _headers(self) -> dict[str, str]:
        key = os.environ.get(self._api_key_env, "") if self._api_key_env else ""
        key = key or self._api_key
        self._active_key = key or None
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _request(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
    ) -> tuple[str, str]:
        """POST chat/completions（含 HTTP 重试），返回 `(content, finish_reason)`。"""
        for attempt in range(self._retries + 1):
            payload: dict[str, Any] = {
                "model": self._model,
                "messages": messages,
                "temperature": self._temperature,
                "max_tokens": (
                    self._max_tokens if max_tokens is None else max_tokens
                ),
            }
            if self._thinking != "auto":
                payload["thinking"] = {"type": self._thinking}
            try:
                response = self._client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                    timeout=self._timeout,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 429 or status >= 500:
                    if attempt < self._retries:
                        time.sleep(2**attempt)
                        continue
                    raise GraphExtractionError(
                        "http_retry_exhausted",
                        _redact(str(exc), self._active_key),
                    ) from exc
                raise GraphExtractionError(
                    "http_4xx", _redact(str(exc), self._active_key)
                ) from exc
            except httpx.TimeoutException as exc:
                if attempt < self._retries:
                    time.sleep(2**attempt)
                    continue
                raise GraphExtractionError("timeout") from exc

            choice = self._first_choice(response.json())
            return (
                self._message_content(choice),
                str(choice.get("finish_reason") or ""),
            )
        raise GraphExtractionError("retry_exhausted")

    def extract(self, heading_path: str, content: str) -> ExtractionResult:
        if not self._base_url or not self._model:
            raise GraphExtractionError("not_configured")
        context = content[: self._max_context]
        request_text = _render_extraction_prompt(
            self._extraction_prompt, heading_path, context
        )
        for attempt in range(self._retries + 1):
            text, finish = self._request(
                [{"role": "user", "content": request_text}]
            )
            if finish == "length" and attempt < self._retries:
                context = context[: max(1, len(context) // 2)]
                request_text = _render_extraction_prompt(
                    self._extraction_prompt, heading_path, context
                )
                logger.debug(
                    "knowledge graph extraction truncated; retry with {} chars",
                    len(context),
                )
                continue
            return parse_extraction(
                clean_model_text(text),
                max_entities=self._max_entities,
                max_relations=self._max_relations,
            )
        raise GraphExtractionError("retry_exhausted")

    def extract_query(
        self, query: str, *, max_entities: int = 8
    ) -> tuple[str, ...]:
        """查询侧实体抽取：WeKnora `extract_entity` 同款 system + few-shot。"""
        if not self._base_url or not self._model:
            raise GraphExtractionError("not_configured")
        text, _finish = self._request(
            [
                {
                    "role": "system",
                    "content": self._query_entity_system_prompt,
                },
                {
                    "role": "user",
                    "content": _render_query_user_prompt(
                        self._query_entity_user_prompt, query
                    ),
                },
            ],
            max_tokens=max(64, min(self._max_tokens, 512)),
        )
        return parse_query_entities(
            clean_model_text(text), max_entities=max_entities
        )

    @staticmethod
    def _first_choice(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise GraphExtractionError("parse")
        choices = cast(dict[str, Any], payload).get("choices")
        if not isinstance(choices, list) or not choices:
            raise GraphExtractionError("parse")
        first = choices[0]
        if not isinstance(first, dict):
            raise GraphExtractionError("parse")
        return cast(dict[str, Any], first)

    @staticmethod
    def _message_content(choice: dict[str, Any]) -> str:
        message = choice.get("message")
        if not isinstance(message, dict):
            raise GraphExtractionError("parse")
        content = cast(dict[str, Any], message).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = cast(dict[str, Any], item).get("text")
                    if isinstance(text, str):
                        parts.append(text)
            if parts:
                return "".join(parts)
        raise GraphExtractionError("empty_response")


def build_graph_extractor(
    config: Any, prompts: Any = None
) -> OpenAICompatGraphExtractor | None:
    """按配置构造抽取器；未启用或缺 base_url/model 时返回 None。"""
    if not bool(getattr(config, "enabled", False)):
        return None
    model = str(getattr(config, "model", "") or "").strip()
    base_url = str(getattr(config, "base_url", "") or "").strip()
    if not model or not base_url:
        return None
    return OpenAICompatGraphExtractor(
        base_url=base_url,
        model=model,
        api_key=str(getattr(config, "api_key", "") or ""),
        api_key_env=str(getattr(config, "api_key_env", "") or ""),
        timeout_seconds=float(getattr(config, "timeout_seconds", 30.0)),
        max_tokens=int(getattr(config, "max_tokens", 2048)),
        temperature=float(getattr(config, "temperature", 0.2)),
        max_context_chars=int(getattr(config, "max_context_chars", 1200)),
        max_retries=int(getattr(config, "max_retries", 1)),
        max_entities=int(getattr(config, "max_entities_per_chunk", 20)),
        max_relations=int(getattr(config, "max_relations_per_chunk", 30)),
        thinking=str(getattr(config, "thinking", "disabled") or "disabled"),
        extraction_prompt=str(
            getattr(prompts, "extraction", "") or DEFAULT_GRAPH_EXTRACTION_PROMPT
        ),
        query_entity_system_prompt=str(
            getattr(prompts, "query_entities", "")
            or DEFAULT_QUERY_ENTITY_SYSTEM_PROMPT
        ),
        query_entity_user_prompt=str(
            getattr(prompts, "query_user", "")
            or DEFAULT_QUERY_ENTITY_USER_PROMPT
        ),
    )
