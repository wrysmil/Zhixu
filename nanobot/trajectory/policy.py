"""轨迹正文的脱敏、截断与预算策略。

规范化发生在持久化与网络边界之前：进入 store 的正文已经是脱敏后的文本，
读取接口不再接触原始凭据。自由文本无法保证识别全部秘密，设置页需明确
本地轨迹会包含用户文件与模型输出。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

from nanobot.trajectory.models import BodyRow, ContentRef, unavailable_ref

PREVIEW_CHARS = 240
DEFAULT_BODY_LIMIT_BYTES = 8 * 1024 * 1024
DEFAULT_RUN_BODY_BUDGET_BYTES = 100 * 1024 * 1024

_SENSITIVE_KEYS = frozenset({
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "access_token",
    "refresh_token",
    "id_token",
    "bootstrap_secret",
    "bootstrapsecret",
    "private_key",
    "x-zhixu-auth",
    "x-nanobot-auth",
})

_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password"
               r"|secret)\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{8,}['\"]?"),
)

_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:token|api_key|apikey|access_token|key|secret|password)=)[^&\s#]+"
)

REDACTED = "[REDACTED]"


def redact_text(text: str) -> tuple[str, bool]:
    """Replace known credential shapes, keeping a stable marker."""
    if not text:
        return text, False
    redacted = False
    result = text
    for pattern in _SECRET_PATTERNS:
        replaced = pattern.sub(REDACTED, result)
        if replaced != result:
            redacted = True
            result = replaced
    replaced = _QUERY_SECRET.sub(lambda match: match.group(1) + REDACTED, result)
    if replaced != result:
        redacted = True
        result = replaced
    return result, redacted


def _key_is_sensitive(key: object) -> bool:
    return isinstance(key, str) and key.casefold() in _SENSITIVE_KEYS


def normalize_value(value: Any, *, depth: int = 0) -> tuple[Any, bool]:
    """Deep-copy a JSON-ish value with credentials and inline media removed."""
    if depth > 32:
        return "[depth-limit]", False
    if isinstance(value, dict):
        redacted = False
        cleaned: dict[str, Any] = {}
        mapping_items = cast(dict[object, object], value)
        for raw_key, raw_value in mapping_items.items():
            key = str(raw_key)
            if _key_is_sensitive(key):
                cleaned[key] = REDACTED
                redacted = True
                continue
            child, child_redacted = normalize_value(raw_value, depth=depth + 1)
            cleaned[key] = child
            redacted = redacted or child_redacted
        return cleaned, redacted
    if isinstance(value, (list, tuple)):
        items: list[Any] = []
        redacted = False
        for raw_item in cast(list[object] | tuple[object, ...], value):
            child, child_redacted = normalize_value(raw_item, depth=depth + 1)
            items.append(child)
            redacted = redacted or child_redacted
        return items, redacted
    if isinstance(value, str):
        if _looks_like_data_url(value):
            return "[inline media omitted]", False
        return redact_text(value)
    return value, False


def _looks_like_data_url(text: str) -> bool:
    head = text[:64].lower()
    return head.startswith("data:") and ";base64," in head


def preview_text(text: str) -> str:
    flat = " ".join(text.split())
    incoming = text.rstrip()
    if len(flat) > PREVIEW_CHARS:
        flat = flat[:PREVIEW_CHARS] + "…"
    if incoming.endswith("\n") and len(flat) < PREVIEW_CHARS:
        flat += " "
    return flat


def truncate_bytes(text: str, limit_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return text, False
    cut = encoded[: max(0, limit_bytes)]
    # 保证 UTF-8 字符边界。
    while cut:
        try:
            return cut.decode("utf-8"), True
        except UnicodeDecodeError:
            cut = cut[:-1]
    return "", True


@dataclass(slots=True)
class ContentPolicy:
    content_mode: str = "standard"
    body_limit_bytes: int = DEFAULT_BODY_LIMIT_BYTES
    run_body_budget_bytes: int = DEFAULT_RUN_BODY_BUDGET_BYTES
    store_bodies: bool = True

    def make_body(self, text: object, content_type: str = "text/plain") -> tuple[ContentRef, BodyRow | None]:
        if isinstance(text, (dict, list)):
            normalized, redacted_flag = normalize_value(text)
            raw = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
            content_type = "application/json"
        elif text is None:
            return unavailable_ref("not_recorded", content_type), None
        else:
            raw = text if isinstance(text, str) else str(text)
            raw, redacted_flag = redact_text(raw)

        original_bytes = len(raw.encode("utf-8"))
        stored, truncated = truncate_bytes(raw, max(0, self.body_limit_bytes))
        if not self.store_bodies:
            # metadata 模式只保留身份、状态与计时，不保留正文或预览。
            return (
                ContentRef(
                    body_id=None,
                    preview="",
                    content_type=content_type,
                    stored_bytes=0,
                    original_bytes=original_bytes,
                    redacted=redacted_flag,
                    truncated=truncated,
                    unavailable_reason="not_recorded",
                ),
                None,
            )
        body_id = uuid4().hex
        return (
            ContentRef(
                body_id=body_id,
                preview=preview_text(stored),
                content_type=content_type,
                stored_bytes=len(stored.encode("utf-8")),
                original_bytes=original_bytes,
                redacted=redacted_flag,
                truncated=truncated,
            ),
            BodyRow(
                body_id=body_id,
                content_type=content_type,
                text=stored,
                stored_bytes=len(stored.encode("utf-8")),
                original_bytes=original_bytes,
                redacted=redacted_flag,
                truncated=truncated,
                preview=preview_text(stored),
            ),
        )
