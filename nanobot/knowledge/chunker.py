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

#: 受保护区间（WeKnora protected_spans）：围栏代码、markdown 表格、`$$` 块。
#: 切点落在区间内部会把代码/表格从中间切断，向量与倒排都跟着失真。
_FENCE_OPEN_RE = re.compile(r"^( {0,3})(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
_TABLE_DELIM_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_LATEX_FENCE = "$$"
#: 区间超过 1.5×chunk_size 时放弃保护——硬切至少还能保证块大小可控。
_PROTECTED_MAX_FACTOR = 1.5


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


def _inside(pos: int, spans: list[tuple[int, int]]) -> bool:
    """`pos` 是否严格落在某个受保护区间内部（等于区间边界是安全切点）。"""
    return any(start < pos < end for start, end in spans)


def _covering(pos: int, spans: list[tuple[int, int]]) -> tuple[int, int] | None:
    """覆盖 `pos` 的受保护区间；没有返回 `None`。"""
    for start, end in spans:
        if start < pos < end:
            return (start, end)
    return None


def _scan_fences(lines: list[Line], starts: list[int]) -> list[tuple[int, int]]:
    """围栏代码块（``` / ~~~）：从开围栏行首到闭围栏行尾；未闭合保护到文末。"""
    spans: list[tuple[int, int]] = []
    fence: tuple[str, int] | None = None
    fence_start = 0
    for index, line in enumerate(lines):
        text = line.text.rstrip()
        if fence is None:
            match = _FENCE_OPEN_RE.match(text)
            if match and not (
                match.group("marker")[0] == "`" and "`" in match.group("info")
            ):
                fence = (match.group("marker")[0], len(match.group("marker")))
                fence_start = starts[index]
            continue
        marker, length = fence
        indent = len(text) - len(text.lstrip(" "))
        stripped = text.strip()
        if (
            indent <= 3
            and stripped
            and stripped == marker * len(stripped)
            and len(stripped) >= length
        ):
            spans.append((fence_start, starts[index] + len(line.text)))
            fence = None
    if fence is not None:
        last = len(lines) - 1
        spans.append((fence_start, starts[last] + len(lines[last].text)))
    return spans


def _scan_tables(lines: list[Line], starts: list[int]) -> list[tuple[int, int]]:
    """markdown 表格：以分隔行（`|---|`）为锚，向上/向下吃掉连续含 `|` 的行。"""
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(lines):
        if not _TABLE_DELIM_RE.match(lines[index].text):
            index += 1
            continue
        top = index
        while top > 0 and "|" in lines[top - 1].text and lines[top - 1].text.strip():
            top -= 1
        bottom = index
        while (
            bottom + 1 < len(lines)
            and "|" in lines[bottom + 1].text
            and lines[bottom + 1].text.strip()
        ):
            bottom += 1
        spans.append((starts[top], starts[bottom] + len(lines[bottom].text)))
        index = bottom + 1
    return spans


def _scan_latex(lines: list[Line], starts: list[int]) -> list[tuple[int, int]]:
    """`$$` 独占一行的 LaTeX 块；未闭合保护到文末。"""
    spans: list[tuple[int, int]] = []
    open_start: int | None = None
    for index, line in enumerate(lines):
        if line.text.strip() != _LATEX_FENCE:
            continue
        if open_start is None:
            open_start = starts[index]
            continue
        spans.append((open_start, starts[index] + len(line.text)))
        open_start = None
    if open_start is not None:
        last = len(lines) - 1
        spans.append((open_start, starts[last] + len(lines[last].text)))
    return spans


def _protected_spans(
    lines: list[Line], starts: list[int], chunk_size: int
) -> list[tuple[int, int]]:
    """合并重叠区间，并丢掉长到保护不了（>1.5×chunk_size）的块。"""
    raw = [
        *_scan_fences(lines, starts),
        *_scan_tables(lines, starts),
        *_scan_latex(lines, starts),
    ]
    raw.sort()
    merged: list[tuple[int, int]] = []
    for start, end in raw:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    limit = int(chunk_size * _PROTECTED_MAX_FACTOR)
    return [(start, end) for start, end in merged if end - start <= limit]


def _heading_flags(
    lines: list[Line], starts: list[int], spans: list[tuple[int, int]]
) -> list[bool]:
    """围栏代码里的 `# 注释` 不是标题，不能开新小节。"""
    return [
        bool(_HEADING_RE.match(line.text)) and not _inside(starts[index], spans)
        for index, line in enumerate(lines)
    ]


def _sections(
    lines: list[Line], mode: str, headings: list[bool]
) -> list[tuple[int, int]]:
    bounds = [0]
    if mode == "blank_line":
        bounds.extend(
            index
            for index, is_heading in enumerate(headings)
            if index > 0 and is_heading
        )
    bounds.append(len(lines))
    return list(zip(bounds, bounds[1:]))


def _heading_paths(
    lines: list[Line], mode: str, headings: list[bool]
) -> list[str]:
    if mode != "blank_line":
        return ["" for _ in lines]
    stack: list[tuple[int, str]] = []
    paths: list[str] = []
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line.text) if headings[index] else None
        if match:
            level = len(match.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, match.group(2)))
        paths.append(" > ".join(text for _, text in stack))
    return paths


def _list_break(
    text: str, start: int, limit: int, protected: list[tuple[int, int]]
) -> int:
    """窗口内最后一个「列表项行首」的换行位置；没有返回 -1。

    列表项之间通常只有单个换行（没有空行），段落切点找不到时退到这一级，
    避免把一条分点从中间硬切开（参考 WeKnora legacy 的 `\\n` 分隔符）。
    """
    index = text.rfind("\n", start, limit)
    while index > start:
        if _LIST_ITEM_RE.match(text, index + 1) and not _inside(index + 1, protected):
            return index
        index = text.rfind("\n", start, index)
    return -1


def _cut(
    text: str,
    start: int,
    limit: int,
    mode: str,
    protected: list[tuple[int, int]],
) -> int:
    """在 `(start, limit]` 内选切点；所有候选都落在受保护区间时切到区间尾部。"""
    if mode == "blank_line":
        index = text.rfind("\n\n", start, limit)
        while index > start and _inside(index, protected):
            index = text.rfind("\n\n", start, index)
        if index > start:
            return index
    index = _list_break(text, start, limit, protected)
    if index > start:
        return index + 1
    boundary = max(
        (text.rfind(mark, start, limit) for mark in _SENTENCE_END), default=-1
    )
    while boundary >= start and _inside(boundary + 1, protected):
        boundary = max(
            (text.rfind(mark, start, boundary) for mark in _SENTENCE_END),
            default=-1,
        )
    if boundary >= start:
        return boundary + 1
    span = _covering(limit, protected)
    if span is not None:
        # 窗口尾部落在代码/表格里：宁可让这一块略超长，也不从中间切断。
        return span[1]
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
    protected = _protected_spans(lines, starts, chunk_size)
    headings = _heading_flags(lines, starts, protected)
    paths = _heading_paths(lines, paragraph_mode, headings)
    total = len(text)
    drafts: list[ChunkDraft] = []

    for index0, index1 in _sections(lines, paragraph_mode, headings):
        if index0 >= index1:
            continue
        section_start = starts[index0]
        # 下一个标题行的起始位置：本节的换行符归本节，避免丢字符。
        section_end = starts[index1] if index1 < len(lines) else total
        heading_path = paths[index0]
        pos = section_start
        while pos < section_end:
            limit = min(pos + chunk_size, section_end)
            cut = (
                section_end
                if limit == section_end
                else _cut(text, pos, limit, paragraph_mode, protected)
            )
            # 保护区间可能把切点推到本节之后；标题边界仍然优先。
            cut = min(cut, section_end)
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
            span = _covering(pos, protected)
            if span is not None:
                # 重叠不要把人带进代码/表格中间：直接跳到区间之后。
                pos = span[1]

    return _close_gaps(text, _merge_fragments(text, drafts, chunk_size))


def _close_gaps(text: str, drafts: list[ChunkDraft]) -> list[ChunkDraft]:
    """把相邻块之间的空白归给前一块，保证 `end_offset` 与下一块 `start_offset` 相接。

    章节边界最容易留洞：上一节的最后一块停在代码块/段落末尾，下一节从标题行起算，
    中间的换行没人接管。`validate_chunks` 要求 `下一块.start <= 上一块.end`，
    这些洞会被判成 non-monotonic start，导入页直接报错。
    """
    closed: list[ChunkDraft] = []
    for index, draft in enumerate(drafts):
        next_draft = drafts[index + 1] if index + 1 < len(drafts) else None
        if next_draft is not None and draft.end_offset < next_draft.start_offset:
            end = next_draft.start_offset
            draft = replace(
                draft,
                content=text[draft.start_offset : end],
                end_offset=end,
            )
        closed.append(draft)
    return closed


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


#: 自定义分隔符模式的默认分隔符（按优先级尝试，参考 Dify 通用分段）。
DEFAULT_SEPARATORS = ("\n\n", "\n", "。", "！", "？", "；", "，")
MAX_SEPARATORS = 12
MAX_SEPARATOR_CHARS = 16


def _split_by_separators(
    text: str,
    start: int,
    end: int,
    separators: tuple[str, ...],
    chunk_size: int,
) -> list[tuple[int, int]]:
    """按分隔符优先级递归切到 ≤ chunk_size 的片段；分隔符保留在前一段末尾。"""
    if end - start <= chunk_size:
        return [(start, end)]
    for index, separator in enumerate(separators):
        if not separator:
            continue
        parts: list[tuple[int, int]] = []
        cursor = start
        while cursor < end:
            hit = text.find(separator, cursor, end)
            if hit < 0:
                parts.append((cursor, end))
                break
            cut = hit + len(separator)
            parts.append((cursor, cut))
            cursor = cut
        if len(parts) > 1:
            out: list[tuple[int, int]] = []
            for part_start, part_end in parts:
                out.extend(
                    _split_by_separators(
                        text,
                        part_start,
                        part_end,
                        separators[index + 1 :],
                        chunk_size,
                    )
                )
            return out
    return [
        (pos, min(pos + chunk_size, end))
        for pos in range(start, end, chunk_size)
    ]


def chunk_by_separators(
    lines: list[Line],
    *,
    separators: tuple[str, ...],
    chunk_size: int,
    chunk_overlap: int,
) -> list[ChunkDraft]:
    """自定义分隔符分段：先按分隔符优先级切片，再按最大长度打包并回填重叠。

    与智能分段不同，这里不保护代码块/标题结构，按用户显式选择的边界切分。
    """
    if chunk_size <= 0 or chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("require 0 <= chunk_overlap < chunk_size")
    if not lines:
        return []
    cleaned = tuple(separator for separator in separators if separator)
    if not cleaned:
        raise ValueError("separators must not be empty")

    text, starts = _build_text(lines)
    if not text.strip():
        return []

    pieces = _split_by_separators(text, 0, len(text), cleaned, chunk_size)
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(pieces):
        start, end = pieces[index]
        cursor = index + 1
        while cursor < len(pieces) and pieces[cursor][1] - start <= chunk_size:
            end = pieces[cursor][1]
            cursor += 1
        spans.append((start, end))
        index = cursor

    drafts: list[ChunkDraft] = []
    previous_start = -1
    previous_end = -1
    for start, end in spans:
        if chunk_overlap > 0 and previous_end >= 0:
            start = max(start - chunk_overlap, previous_start + 1)

        # 段首空白不能裁过上一块终点；段尾也不裁——裁剪会让上一块的 end 变小，
        # 与下一块的 start 之间留下 1~2 个字符的空隙，`validate_chunks` 会报
        # non-monotonic start（重叠为 0 时尤甚）。分隔符本身的归属保持不变。
        while start < end and text[start].isspace():
            if drafts and start >= previous_end:
                break
            start += 1
        if start >= end:
            continue
        if drafts and start <= previous_start:
            start = previous_start + 1
        if start >= end:
            continue

        page, locator = _chunk_meta(lines, starts, start, end)
        drafts.append(
            ChunkDraft(
                content=text[start:end],
                start_offset=start,
                end_offset=end,
                heading_path="",
                page=page,
                locator=locator,
            )
        )
        previous_start = start
        previous_end = end
    return drafts


def validate_chunks(
    drafts: list[ChunkDraft],
    *,
    chunk_size: int,
    chunk_overlap: int,
    text: str,
    allow_small: bool = False,
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
    if not allow_small and len(drafts) >= _SMALL_CHUNK_DIVISOR:
        # 最后一块常被剩余文本截短，豁免。自定义分隔符模式由用户决定边界，跳过该启发式。
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
