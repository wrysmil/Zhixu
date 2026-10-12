"""写作域字段语义、上限与内容摘要。

上限取值与错误文案的唯一真相源是契约 §5.3 / §6，本模块只做校验与规范化，
不生成 id / 时间戳（那是 repository 的职责）。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

ARTICLE_STATUSES = ("idea", "draft", "review", "done")
MATERIAL_TYPES = ("note", "text", "code", "link")

MAX_TITLE_CHARS = 200
MAX_SUMMARY_CHARS = 500
MAX_BODY_CHARS = 200_000
MAX_TAGS = 20
MAX_TAG_CHARS = 50
MAX_OUTLINE_ITEMS = 200
MAX_OUTLINE_TEXT_CHARS = 200
MAX_MATERIALS = 100
MAX_MATERIAL_CONTENT_CHARS = 20_000
MAX_URL_CHARS = 2_000
MAX_SNAPSHOT_BYTES = 1024 * 1024

_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_URL_PREFIXES = ("http://", "https://")


class ArticleLimitError(ValueError):
    """字段校验失败；message 与 status 直接按契约 §6 原样透传给 WebUI。"""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def new_id() -> str:
    return uuid.uuid4().hex


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, field: str, limit: int, *, required: bool = False) -> str:
    if value is None:
        if required:
            raise ArticleLimitError(f"unsupported article field: {field}")
        return ""
    if not isinstance(value, str):
        raise ArticleLimitError(f"unsupported article field: {field}")
    if len(value) > limit:
        raise ArticleLimitError(
            f"article {field} is too long", status=413 if field == "body" else 400,
        )
    return value


def _tags(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise ArticleLimitError("unsupported article field: tags")
    entries: Sequence[Any] = cast(Sequence[Any], value)
    if len(entries) > MAX_TAGS:
        raise ArticleLimitError("article tags limit exceeded")
    return [
        item.strip() for item in entries
        if isinstance(item, str) and item.strip() and len(item.strip()) <= MAX_TAG_CHARS
    ]


def _brief(value: Any) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping):
        raise ArticleLimitError("unsupported article field: brief")
    data: Mapping[str, Any] = cast(Mapping[str, Any], value)
    out: dict[str, str] = {}
    for key in ("goal", "audience", "style"):
        raw = data.get(key)
        if raw is None or not isinstance(raw, str) or not raw.strip():
            continue
        out[key] = raw.strip()[:MAX_SUMMARY_CHARS]
    return out


def _outline(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise ArticleLimitError("unsupported article field: outline")
    entries: Sequence[Any] = cast(Sequence[Any], value)
    if len(entries) > MAX_OUTLINE_ITEMS:
        raise ArticleLimitError("article outline limit exceeded")
    items: list[dict[str, Any]] = []
    for order, raw in enumerate(entries):
        if isinstance(raw, Mapping):
            item: Mapping[str, Any] = cast(Mapping[str, Any], raw)
            text: Any = item.get("text", "")
            item_id: Any = item.get("id")
        else:
            text, item_id = raw, None
        if not isinstance(text, str):
            raise ArticleLimitError("unsupported article field: outline")
        text = text.strip()
        if not text:
            continue
        if len(text) > MAX_OUTLINE_TEXT_CHARS:
            raise ArticleLimitError("article outline limit exceeded")
        if item_id is not None and (
            not isinstance(item_id, str) or not _ID_RE.fullmatch(item_id)
        ):
            raise ArticleLimitError("unsupported article field: outline.id")
        items.append({"id": item_id or new_id(), "text": text, "order": order})
    return items


def _materials(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise ArticleLimitError("unsupported article field: materials")
    entries: Sequence[Any] = cast(Sequence[Any], value)
    if len(entries) > MAX_MATERIALS:
        raise ArticleLimitError("article materials limit exceeded")
    out: list[dict[str, Any]] = []
    for order, raw in enumerate(entries):
        if not isinstance(raw, Mapping):
            raise ArticleLimitError("unsupported article field: materials")
        item: Mapping[str, Any] = cast(Mapping[str, Any], raw)
        kind = item.get("type", "note")
        if kind not in MATERIAL_TYPES:
            raise ArticleLimitError("unsupported article material type")
        title = _text(item.get("title"), "materials.title", MAX_TITLE_CHARS)
        content = _text(item.get("content"), "materials.content", MAX_MATERIAL_CONTENT_CHARS)
        url = _text(item.get("url"), "materials.url", MAX_URL_CHARS)
        if kind == "link" and url and not url.lower().startswith(_URL_PREFIXES):
            raise ArticleLimitError("article material url must be http(s)")
        material_id = item.get("id")
        if material_id is not None and (
            not isinstance(material_id, str) or not _ID_RE.fullmatch(material_id)
        ):
            raise ArticleLimitError("unsupported article field: materials.id")
        created_at = item.get("created_at")
        updated_at = item.get("updated_at")
        out.append({
            "id": material_id or new_id(),
            "type": kind,
            "title": title,
            "content": content,
            "url": url if kind == "link" else "",
            "source": _text(item.get("source"), "materials.source", MAX_URL_CHARS),
            "note": _text(item.get("note"), "materials.note", MAX_MATERIAL_CONTENT_CHARS),
            "language": _text(item.get("language"), "materials.language", 50),
            "included": bool(item.get("included", True)),
            "order": order,
            "created_at": created_at if isinstance(created_at, str) and created_at else utc_now_iso(),
            "updated_at": updated_at if isinstance(updated_at, str) and updated_at else utc_now_iso(),
        })
    return out


def normalize_article_fields(raw: Mapping[str, Any]) -> dict[str, Any]:
    """创建/保存共用：产出可入库字段（不含 id / revision / 时间戳）。"""
    title = _text(raw.get("title"), "title", MAX_TITLE_CHARS, required=True).strip()
    if not title:
        raise ArticleLimitError("article title is required")
    status = raw.get("status", "idea")
    if status not in ARTICLE_STATUSES:
        raise ArticleLimitError("unsupported article status")
    return {
        "title": title,
        "writing_type": _text(raw.get("writing_type"), "writing_type", 50).strip(),
        "template_id": _text(raw.get("template_id"), "template_id", 50).strip(),
        "status": status,
        "summary": _text(raw.get("summary"), "summary", MAX_SUMMARY_CHARS).strip(),
        "brief": _brief(raw.get("brief")),
        "body": _text(raw.get("body"), "body", MAX_BODY_CHARS),
        "outline": _outline(raw.get("outline")),
        "materials": _materials(raw.get("materials")),
        "tags": _tags(raw.get("tags")),
    }


_SNAPSHOT_KEYS = (
    "status", "writing_type", "template_id", "title", "summary",
    "brief", "tags", "outline", "materials", "body",
)


def normalize_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    """摘要口径：只留契约列出的持久化内容，按固定键序排列。"""
    return {key: payload[key] for key in _SNAPSHOT_KEYS if key in payload}


def snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    """契约 §5.3 定义的 content_digest。"""
    encoded = json.dumps(
        normalize_snapshot(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise ArticleLimitError("article snapshot is too large", status=413)
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ArticleRecord:
    """一行 articles 记录。"""

    id: str
    workspace_key: str
    revision: int
    created_at: str
    updated_at: str
    fields: dict[str, Any]

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> ArticleRecord:
        fields: dict[str, Any] = {
            "title": row["title"],
            "writing_type": row["writing_type"],
            "template_id": row["template_id"],
            "status": row["status"],
            "summary": row["summary"],
            "body": row["body"],
        }
        for key in ("brief", "outline", "materials", "tags"):
            fields[key] = json.loads(row[key] or "null")
        return cls(
            id=row["id"],
            workspace_key=row["workspace_key"],
            revision=int(row["revision"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            fields=fields,
        )

    @classmethod
    def from_summary_row(cls, row: Mapping[str, Any]) -> ArticleRecord:
        """列表投影行：只携带 summary 列，不含正文/提纲/素材/brief。"""
        return cls(
            id=row["id"],
            workspace_key=row["workspace_key"],
            revision=int(row["revision"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            fields={
                "title": row["title"],
                "writing_type": row["writing_type"],
                "status": row["status"],
                "summary": row["summary"],
                "tags": json.loads(row["tags"] or "[]"),
            },
        )

    def to_article_dict(self) -> dict[str, Any]:
        """完整文章投影（契约 §4 article-get）。"""
        return {
            "id": self.id,
            "workspace_key": self.workspace_key,
            **self.fields,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_summary_dict(self) -> dict[str, Any]:
        """列表投影：不含 body / outline / materials / brief（契约 §4）。"""
        return {
            "id": self.id,
            "title": self.fields["title"],
            "summary": self.fields["summary"],
            "status": self.fields["status"],
            "writing_type": self.fields["writing_type"],
            "tags": self.fields["tags"],
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
