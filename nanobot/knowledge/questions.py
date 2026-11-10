"""入库期问题生成（doc2query），口径对齐 WeKnora。

来源：Tencent/WeKnora `internal/application/service/knowledge_process.go`：

- 每个 text chunk 生成 N 条「用户可能问的问题」（默认 3，范围 1..10）；
- 输入 = 块正文 + 前后邻居块 + 文档名；
- temperature 0.7、max_tokens 2048；先剥 `<think>`，再逐行剥编号，长度 > 5 才保留；
- 问题写成独立索引行（`id = <chunk_id>-q<序号>`），命中时回填父块正文；
- 生成/索引失败不影响文档入库——问题只是召回增强，不是入库前提。
"""

from __future__ import annotations

import os
import re
from typing import Any, Protocol, cast

import httpx

from nanobot.knowledge.tokenizer import tokenize_index

DEFAULT_QUESTION_COUNT = 3
MAX_QUESTION_COUNT = 10

_MAX_TOKENS = 2048
_TEMPERATURE = 0.7

_DEFAULT_PROMPT = (
    "你是知识库检索增强助手。请阅读下面的文档片段，生成 {question_count} 个"
    "用户可能提出的问题。\n"
    "要求：每行一个问题，不要编号；问题必须能由片段内容回答；"
    "角度不要重复；使用 {language}。\n"
    "文档名称：{doc_name}\n"
    "{context}"
    "片段：\n{content}"
)

_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)
_QUESTION_SUFFIX = re.compile(r"^(?P<parent>.+)-q(?P<index>\d+)$")
_NUMBER_PREFIX = "0123456789.-*) "


class QuestionGenerationError(RuntimeError):
    """生成问题失败；入库方按「增强项失败」处理，不改变文档状态。"""


class QuestionGenerator(Protocol):
    @property
    def model_id(self) -> str: ...

    def generate(
        self,
        content: str,
        *,
        prev_content: str = "",
        next_content: str = "",
        doc_name: str = "",
        question_count: int = DEFAULT_QUESTION_COUNT,
    ) -> list[str]: ...


def question_id(chunk_id: str, index: int) -> str:
    """问题行的检索 id；父块 id 是它的前缀。"""
    return f"{chunk_id}-q{index}"


def parent_chunk_id(source_id: str) -> str | None:
    """从问题行 id 还原父块 id；不是问题行返回 `None`。"""
    match = _QUESTION_SUFFIX.match(source_id)
    return match.group("parent") if match else None


def parse_generated_questions(text: str, limit: int = DEFAULT_QUESTION_COUNT) -> list[str]:
    """剥 `<think>` → 逐行剥编号 → 长度 > 5 才留 → 最多 `limit` 条。"""
    if limit <= 0:
        return []
    cleaned = _THINK_BLOCK.sub("", text)
    questions: list[str] = []
    for line in cleaned.splitlines():
        line = line.strip()
        if not line:
            continue
        line = line.lstrip(_NUMBER_PREFIX).strip()
        # WeKnora 用 Go 的字节长度（len > 5）；中文约等于「至少 2 个字」。
        if len(line.encode("utf-8")) > 5:
            questions.append(line)
            if len(questions) >= limit:
                break
    return questions


def question_count_from(config: Any) -> int:
    """配置里的条数钳到 1..10；缺省/非法值回落 3（WeKnora 口径）。"""
    try:
        raw = int(getattr(config, "question_count", DEFAULT_QUESTION_COUNT))
    except (TypeError, ValueError):
        raw = DEFAULT_QUESTION_COUNT
    return max(1, min(raw, MAX_QUESTION_COUNT))


def _redact(text: str, secret: str | None = None) -> str:
    redacted = re.sub(r"(?i)(bearer\s+)\S+", r"\1***", text)
    redacted = re.sub(r"sk-[A-Za-z0-9_\-]{4,}", "sk-***", redacted)
    if secret:
        redacted = redacted.replace(secret, "***")
    return redacted


class OpenAICompatQuestionGenerator:
    """OpenAI 兼容 `/chat/completions` 同步客户端（入库线程内调用）。"""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        api_key_env: str = "",
        timeout_seconds: float = 30.0,
        max_tokens: int = _MAX_TOKENS,
        temperature: float = _TEMPERATURE,
        max_context_chars: int = 1200,
        prompt: str = _DEFAULT_PROMPT,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._timeout = timeout_seconds
        self._max_tokens = max(1, max_tokens)
        self._temperature = temperature
        self._max_context_chars = max(1, max_context_chars)
        self._prompt = prompt
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._active_key: str | None = None

    @property
    def model_id(self) -> str:
        return f"{self._base_url}#{self._model}"

    def _resolve_key(self) -> str:
        if self._api_key_env:
            value = os.environ.get(self._api_key_env, "")
            if value:
                return value
        return self._api_key

    def _headers(self) -> dict[str, str]:
        key = self._resolve_key()
        self._active_key = key or None
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _build_prompt(
        self,
        content: str,
        prev_content: str,
        next_content: str,
        doc_name: str,
        question_count: int,
    ) -> str:
        context = ""
        preceding = prev_content[: self._max_context_chars].strip()
        following = next_content[: self._max_context_chars].strip()
        if preceding or following:
            parts: list[str] = []
            if preceding:
                parts.append(
                    f"<preceding_content>\n{preceding}\n</preceding_content>"
                )
            if following:
                parts.append(
                    f"<following_content>\n{following}\n</following_content>"
                )
            context = "<surrounding_context>\n" + "\n".join(parts)
            context += "\n</surrounding_context>\n"
        return self._prompt.format(
            question_count=question_count,
            content=content,
            context=context,
            doc_name=doc_name or "未命名文档",
            language="中文",
        )

    def generate(
        self,
        content: str,
        *,
        prev_content: str = "",
        next_content: str = "",
        doc_name: str = "",
        question_count: int = DEFAULT_QUESTION_COUNT,
    ) -> list[str]:
        if not content.strip() or question_count <= 0:
            return []
        if not self._base_url or not self._model:
            raise QuestionGenerationError(
                "question generation base_url/model is not configured"
            )
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": self._build_prompt(
                        content, prev_content, next_content, doc_name, question_count
                    ),
                }
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        try:
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise QuestionGenerationError(
                "question generation request failed: "
                + _redact(str(exc), self._active_key)
            ) from exc
        return parse_generated_questions(
            self._message_content(response.json()), question_count
        )

    @staticmethod
    def _message_content(payload: Any) -> str:
        if not isinstance(payload, dict):
            raise QuestionGenerationError("question generation response is not an object")
        data = cast(dict[str, Any], payload)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise QuestionGenerationError("question generation response has no choices")
        first: Any = cast(list[Any], choices)[0]
        if not isinstance(first, dict):
            raise QuestionGenerationError("question generation choice is not an object")
        message = cast(dict[str, Any], first).get("message")
        if not isinstance(message, dict):
            raise QuestionGenerationError("question generation choice has no message")
        content = cast(dict[str, Any], message).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in cast(list[Any], content):
                if not isinstance(item, dict):
                    continue
                text = cast(dict[str, Any], item).get("text")
                if isinstance(text, str):
                    parts.append(text)
            if parts:
                return "".join(parts)
        raise QuestionGenerationError("question generation message content is empty")


def build_question_generator(config: Any) -> QuestionGenerator | None:
    """按配置构造生成器；未启用或缺 base_url/model 时返回 None（不生成）。"""
    if not bool(getattr(config, "enabled", False)):
        return None
    model = str(getattr(config, "model", "") or "").strip()
    base_url = str(getattr(config, "base_url", "") or "").strip()
    if not model or not base_url:
        return None
    return OpenAICompatQuestionGenerator(
        base_url=base_url,
        model=model,
        api_key=str(getattr(config, "api_key", "") or ""),
        api_key_env=str(getattr(config, "api_key_env", "") or ""),
        timeout_seconds=float(getattr(config, "timeout_seconds", 30.0)),
        max_tokens=int(getattr(config, "max_tokens", _MAX_TOKENS)),
        temperature=float(getattr(config, "temperature", _TEMPERATURE)),
        max_context_chars=int(getattr(config, "max_context_chars", 1200)),
    )


def question_search_text(question: str, title: str = "") -> str:
    """问题行的倒排文本：标题 + 问题，与建索引/查询同一分词器。"""
    return tokenize_index(f"{title}\n{question}" if title else question)


__all__ = [
    "DEFAULT_QUESTION_COUNT",
    "MAX_QUESTION_COUNT",
    "OpenAICompatQuestionGenerator",
    "QuestionGenerationError",
    "QuestionGenerator",
    "build_question_generator",
    "parent_chunk_id",
    "parse_generated_questions",
    "question_count_from",
    "question_id",
    "question_search_text",
]
