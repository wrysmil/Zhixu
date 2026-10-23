"""正文定位口径（契约 §3.1）：归一化映射、范围解析、目标校验与落文。

**单位是 Python 码点**（``len()``）：前端选区用 JS UTF-16，换算在前端完成
（契约 §6 的 ``utf16RangeToCodepoints``），本模块只认码点。

归一化只作用于协作定位：``\\r\\n`` / ``\\r`` → ``\\n``，映射表把归一化下标换回
原始下标，因此保存的正文保持用户原样，A 契约的 ``content_digest`` 口径不受影响。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

BODY_KINDS = ("replace_range", "insert_at")
TARGET_KINDS = (*BODY_KINDS, "outline_item")


class OffsetError(ValueError):
    """定位失败：越界、``expected_before`` 不符、提纲项不存在等。

    ``code`` 是稳定的机器可判定原因（``message`` 保持旧契约 §3.1 的人类可读文案），
    调用方据此把「该条建议判废」的原始回复原样保留、逐条记原因。
    """

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_with_map(body: str) -> tuple[str, list[int]]:
    """返回 ``(归一化正文, 归一化下标 → 原始下标)``。

    映射表长度为 ``len(归一化正文) + 1``，末位等于 ``len(原始正文)``，因此右边界
    也能直接切原始串。换行统一记到 ``\\r`` 的原始位置（``\\r\\n`` 占两位）。
    """
    parts: list[str] = []
    mapping: list[int] = []
    cursor = 0
    length = len(body)
    while cursor < length:
        char = body[cursor]
        if char == "\r":
            step = 2 if cursor + 1 < length and body[cursor + 1] == "\n" else 1
            mapping.append(cursor)
            parts.append("\n")
            cursor += step
            continue
        mapping.append(cursor)
        parts.append(char)
        cursor += 1
    mapping.append(length)
    return "".join(parts), mapping


def _offsets(normalized: str, target: Mapping[str, Any]) -> tuple[int, int]:
    """取并校验 ``0 <= start <= end <= len(normalized)``。"""
    start = target.get("start", 0)
    end = target.get("end", start)
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
        raise OffsetError("target offsets must be integers", code="invalid_offsets")
    if start < 0 or end < start or end > len(normalized):
        raise OffsetError("target out of range", code="out_of_range")
    return start, end


def validate_target(
    normalized: str,
    target: Mapping[str, Any],
    *,
    outline: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    """按契约 §3.1 判定目标是否可采纳；不满足即抛 :class:`OffsetError`。

    - 三种 kind 之外一律判废；
    - 正文目标要求 ``0 <= start <= end <= len(归一化正文)``；
    - ``replace_range`` 要求 ``expected_before`` 与归一化切片**全等**；
    - ``insert_at`` 要求 ``start == end`` 且 ``expected_before`` 为空；
    - ``outline_item`` 要求 ``outline_item_id`` 存在于快照提纲（坐标不参与定位）。
    """
    kind = target.get("kind")
    if kind not in TARGET_KINDS:
        raise OffsetError(f"unsupported target kind: {kind}", code="unsupported_kind")
    if kind == "outline_item":
        item_id = target.get("outline_item_id")
        if not any(str(item.get("id")) == str(item_id) for item in (outline or ())):
            raise OffsetError("outline item not found", code="outline_item_not_found")
        return
    start, end = _offsets(normalized, target)
    expected = str(target.get("expected_before") or "")
    if kind == "insert_at":
        if start != end:
            raise OffsetError("insert_at requires an empty range", code="insert_range_not_empty")
        if expected:
            raise OffsetError("expected_before mismatch", code="expected_before_mismatch")
        return
    if normalized[start:end] != expected:
        raise OffsetError("expected_before mismatch", code="expected_before_mismatch")


def resolve_range(
    normalized: str, mapping: Sequence[int], target: Mapping[str, Any],
) -> tuple[int, int]:
    """把归一化坐标换回**原始正文**下标，供调用方切原始串。"""
    if len(mapping) != len(normalized) + 1:
        raise OffsetError("offset map does not match the normalized body", code="invalid_offsets")
    start, end = _offsets(normalized, target)
    return int(mapping[start]), int(mapping[end])


def apply_target(body: str, target: Mapping[str, Any], after: str) -> str:
    """把建议正文落到原始正文（``replace_range`` / ``insert_at`` 共用）。"""
    kind = target.get("kind")
    if kind == "outline_item":
        raise OffsetError("outline targets must go through apply_outline_target", code="unsupported_kind")
    normalized, mapping = normalize_with_map(body)
    validate_target(normalized, target)
    raw_start, raw_end = resolve_range(normalized, mapping, target)
    return body[:raw_start] + after + body[raw_end:]


def apply_outline_target(
    outline: Sequence[Mapping[str, Any]],
    target: Mapping[str, Any],
    items: Sequence[str],
) -> list[dict[str, Any]]:
    """提纲建议：只替换被指向的那一项（可扩成多项），其余项与相对顺序不变。

    ``items`` 是按行分隔的提纲项列表（契约 §5.2）；锚点项沿用原 id，新增项不自造
    id（由 A 的 ``normalize_article_fields`` 生成服务端 uuid）。
    """
    if target.get("kind") != "outline_item":
        raise OffsetError("unsupported target kind for outline apply", code="unsupported_kind")
    validate_target("", target, outline=outline)
    lines = [str(item).strip() for item in items if str(item).strip()]
    if not lines:
        raise OffsetError("outline suggestion is empty", code="empty_outline")
    item_id = str(target.get("outline_item_id"))
    index = next(
        position for position, item in enumerate(outline) if str(item.get("id")) == item_id
    )
    anchor = outline[index].get("id")
    replacement: list[Mapping[str, Any]] = [{"id": anchor, "text": lines[0]}]
    replacement.extend({"text": text} for text in lines[1:])
    merged: list[Mapping[str, Any]] = [
        *outline[:index], *replacement, *outline[index + 1:],
    ]
    return [
        {**({"id": item["id"]} if item.get("id") else {}), "text": item.get("text", ""), "order": position}
        for position, item in enumerate(merged)
    ]
