"""分块：标题分段、贪心切分（段落 / 列表项 / 句末优先级）、重叠、碎片合并。"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, replace

from nanobot.knowledge.extractor import Line

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SENTENCE_END = "。！？；.!?;"
#: 列表项行首（`- ` / `* ` / `+ ` / `1. ` / `1) `）；参考 WeKnora legacy 的 `\n` 切点。
_LIST_ITEM_RE = re.compile(r"[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+\S")
_FRAGMENT_CHARS = 80
_MERGE_RATIO = 1.5


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """块草稿；偏移是归一化文本上的半开区间。"""

    content: str
    start_offset: int
    end_offset: int
    heading_path: str
    page: int | None
    locator: str

    @property
    def char_count(self) -> int:
        return self.end_offset - self.start_offset


def _build_text(lines: list[Line]) -> tuple[str, list[int]]:
    starts: list[int] = []
    pos = 0
    for line in lines:
        starts.append(pos)
        pos += len(line.text) + 1
    return "\n".join(line.text for line in lines), starts


def _sections(lines: list[Line], mode: str) -> list[tuple[int, int]]:
    bounds = [0]
    if mode == "blank_line":
        bounds.extend(
            index
            for index, line in enumerate(lines)
            if index > 0 and _HEADING_RE.match(line.text)
        )
    bounds.append(len(lines))
    return list(zip(bounds, bounds[1:]))


def _heading_paths(lines: list[Line], mode: str) -> list[str]:
    if mode != "blank_line":
        return ["" for _ in lines]
    stack: list[tuple[int, str]] = []
    paths: list[str] = []
    for line in lines:
        match = _HEADING_RE.match(line.text)
        if match:
            level = len(match.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, match.group(2)))
        paths.append(" > ".join(text for _, text in stack))
    return paths


def _list_break(text: str, start: int, limit: int) -> int:
    """窗口内最后一个「列表项行首」的换行位置；没有返回 -1。

    列表项之间通常只有单个换行（没有空行），段落切点找不到时退到这一级，
    避免把一条分点从中间硬切开（参考 WeKnora legacy 的 `\\n` 分隔符）。
    """
    index = text.rfind("\n", start, limit)
    while index > start:
        if _LIST_ITEM_RE.match(text, index + 1):
            return index
        index = text.rfind("\n", start, index)
    return -1


def _cut(text: str, start: int, limit: int, mode: str) -> int:
    if mode == "blank_line":
        index = text.rfind("\n\n", start, limit)
        if index > start:
            return index
    index = _list_break(text, start, limit)
    if index > start:
        return index + 1
    boundary = max((text.rfind(mark, start, limit) for mark in _SENTENCE_END), default=-1)
    if boundary >= start:
        return boundary + 1
    return limit


def _chunk_meta(
    lines: list[Line], starts: list[int], start: int, end: int
) -> tuple[int | None, str]:
    """块内第一个有页码的行 / 第一个非空行的 locator。"""
    index = max(bisect_right(starts, start) - 1, 0)
    page: int | None = None
    locator = ""
    while index < len(lines) and starts[index] < end:
        line = lines[index]
        if page is None and line.page is not None:
            page = line.page
        if not locator and line.text.strip():
            locator = line.locator
        index += 1
    return page, locator


def chunk_lines(
    lines: list[Line],
    *,
    chunk_size: int,
    chunk_overlap: int,
    paragraph_mode: str,
) -> list[ChunkDraft]:
    if chunk_size <= 0 or chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("require 0 <= chunk_overlap < chunk_size")
    if paragraph_mode not in {"blank_line", "line"}:
        raise ValueError(f"unknown paragraph_mode: {paragraph_mode}")
    if not lines:
        return []

    text, starts = _build_text(lines)
    if not text.strip():
        return []
    paths = _heading_paths(lines, paragraph_mode)
    total = len(text)
    drafts: list[ChunkDraft] = []

    for index0, index1 in _sections(lines, paragraph_mode):
        if index0 >= index1:
            continue
        section_start = starts[index0]
        # 下一个标题行的起始位置：本节的换行符归本节，避免丢字符。
        section_end = starts[index1] if index1 < len(lines) else total
        heading_path = paths[index0]
        pos = section_start
        while pos < section_end:
            limit = min(pos + chunk_size, section_end)
            cut = section_end if limit == section_end else _cut(text, pos, limit, paragraph_mode)
            if cut <= pos:
                cut = limit
            content = text[pos:cut]
            if content.strip():
                page, locator = _chunk_meta(lines, starts, pos, cut)
                drafts.append(
                    ChunkDraft(
                        content=content,
                        start_offset=pos,
                        end_offset=cut,
                        heading_path=heading_path,
                        page=page,
                        locator=locator,
                    )
                )
            if cut >= section_end:
                break
            pos = max(pos + 1, cut - chunk_overlap, section_start)

    return _merge_fragments(text, drafts, chunk_size)


def _merge_fragments(
    text: str, drafts: list[ChunkDraft], chunk_size: int
) -> list[ChunkDraft]:
    limit = int(chunk_size * _MERGE_RATIO)
    merged: list[ChunkDraft] = []
    index = 0
    while index < len(drafts):
        current = drafts[index]
        while (
            current.char_count < _FRAGMENT_CHARS
            and index + 1 < len(drafts)
            and drafts[index + 1].heading_path == current.heading_path
            and drafts[index + 1].end_offset - current.start_offset <= limit
        ):
            index += 1
            tail = drafts[index]
            current = replace(
                current,
                content=text[current.start_offset : tail.end_offset],
                end_offset=tail.end_offset,
                page=current.page if current.page is not None else tail.page,
                locator=current.locator or tail.locator,
            )
        merged.append(current)
        index += 1
    if (
        len(merged) >= 2
        and merged[-1].char_count < _FRAGMENT_CHARS
        and merged[-2].heading_path == merged[-1].heading_path
        and merged[-1].end_offset - merged[-2].start_offset <= limit
    ):
        tail = merged.pop()
        prev = merged[-1]
        merged[-1] = replace(
            prev,
            content=text[prev.start_offset : tail.end_offset],
            end_offset=tail.end_offset,
            page=prev.page if prev.page is not None else tail.page,
            locator=prev.locator or tail.locator,
        )
    return merged


_MAX_CHUNKS = 20_000
_SMALL_CHUNK_CHARS = 50
_SMALL_CHUNK_DIVISOR = 4
_OVERSIZED_RATIO = 2


class KnowledgeChunkError(ValueError):
    """分块校验失败；`code` 直接落库到 `documents.error_code`。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def validate_chunks(
    drafts: list[ChunkDraft], *, chunk_size: int, chunk_overlap: int, text: str
) -> None:
    """校验切分结果，不合格即抛 `KnowledgeChunkError`（纯函数、无副作用）。

    规则（阈值均为严格大于）：无文本 → `KNOWLEDGE_TEXT_EMPTY`；
    块数 > 20 000 → `KNOWLEDGE_CHUNK_LIMIT`；单块 > 2×chunk_size、
    ≥4 块时非末块中 <50 字的块超过 1/4、偏移/重叠不变量破坏
    → `KNOWLEDGE_CHUNK_INVALID`。
    """
    if not drafts:
        code = "KNOWLEDGE_TEXT_EMPTY" if not text.strip() else "KNOWLEDGE_CHUNK_INVALID"
        raise KnowledgeChunkError(code, "no chunks produced")
    if len(drafts) > _MAX_CHUNKS:
        raise KnowledgeChunkError("KNOWLEDGE_CHUNK_LIMIT", "chunk count > 20000")
    if len(drafts) == 1 and drafts[0].char_count > _OVERSIZED_RATIO * chunk_size:
        raise KnowledgeChunkError("KNOWLEDGE_CHUNK_INVALID", "single oversized chunk")
    if len(drafts) >= _SMALL_CHUNK_DIVISOR:
        # 最后一块常被剩余文本截短，豁免。
        small = sum(1 for draft in drafts[:-1] if draft.char_count < _SMALL_CHUNK_CHARS)
        if small > len(drafts) / _SMALL_CHUNK_DIVISOR:
            raise KnowledgeChunkError("KNOWLEDGE_CHUNK_INVALID", "too many small chunks")
    for index, draft in enumerate(drafts):
        if text[draft.start_offset : draft.end_offset] != draft.content:
            raise KnowledgeChunkError("KNOWLEDGE_CHUNK_INVALID", "offset mismatch")
        if draft.start_offset >= draft.end_offset:
            raise KnowledgeChunkError("KNOWLEDGE_CHUNK_INVALID", "empty span")
        if index:
            previous = drafts[index - 1]
            if not previous.start_offset < draft.start_offset <= previous.end_offset:
                raise KnowledgeChunkError("KNOWLEDGE_CHUNK_INVALID", "non-monotonic start")
            if previous.end_offset - draft.start_offset > chunk_overlap:
                raise KnowledgeChunkError("KNOWLEDGE_CHUNK_INVALID", "overlap too large")
