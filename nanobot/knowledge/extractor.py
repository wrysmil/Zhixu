"""抽取行流；PDF 必须循环 continuation，md/txt 自行解码。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from nanobot.utils.document import open_document_line_source

_OFFICE_SUFFIXES = {".pdf", ".docx", ".xlsx", ".pptx"}
_TEXT_SUFFIXES = {".md", ".txt"}
_PAGES_RE = re.compile(r"pages='(\d+)-(\d+)'")
_PDF_PAGE_RE = re.compile(r"page=(\d+)")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_CHARDET_MIN_CONFIDENCE = 0.7
_CJK_FALLBACK_ENCODING = "gb18030"
_CJK_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))


class KnowledgeExtractionError(RuntimeError):
    """Raised when a document cannot be turned into searchable lines."""


class KnowledgeUnsupportedType(KnowledgeExtractionError):  # noqa: N818 - 名称由 plan Task 8 契约固定
    """Raised for document types the knowledge center cannot extract."""


@dataclass(frozen=True, slots=True)
class Line:
    """One normalized, searchable line of a document."""

    text: str
    locator: str
    page: int | None
    searchable: bool = True


def _normalize_text(text: str) -> str:
    text = text.lstrip("\ufeff")
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _MULTI_NEWLINE_RE.sub("\n\n", text).rstrip("\n")


def _has_cjk(text: str) -> bool:
    for char in text:
        code = ord(char)
        if any(start <= code <= end for start, end in _CJK_RANGES):
            return True
    return False


def _decode_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    import chardet

    guess = chardet.detect(raw)
    encoding = guess.get("encoding")
    confidence = guess.get("confidence") or 0.0
    if encoding and confidence >= _CHARDET_MIN_CONFIDENCE:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            pass
    # chardet 对很短的 GBK 样本给不出结论（encoding=None），确定性兜底
    # 到 GBK/GB2312 超集，避免纯中文短文件变成替换字符。
    try:
        fallback = raw.decode(_CJK_FALLBACK_ENCODING)
    except (LookupError, UnicodeDecodeError):
        fallback = ""
    if fallback and _has_cjk(fallback):
        return fallback
    return raw.decode("utf-8", errors="replace")


def _parse_continuation(value: str) -> str:
    match = _PAGES_RE.fullmatch(value)
    if match is None:
        raise KnowledgeExtractionError(f"invalid continuation: {value!r}")
    return f"{match.group(1)}-{match.group(2)}"


def _pdf_page(locator: str) -> int | None:
    match = _PDF_PAGE_RE.search(locator)
    return int(match.group(1)) if match is not None else None


def extract_lines(path: Path) -> list[Line]:
    """Extract normalized lines from ``path``; page numbers stay 1-based."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        text = _normalize_text(_decode_text(path))
        return [Line(line, "", None) for line in text.split("\n")]
    if suffix not in _OFFICE_SUFFIXES:
        raise KnowledgeUnsupportedType(f"unsupported document type: {suffix or path.name}")

    collected: list[Line] = []
    pages: str | None = None
    seen: set[str] = set()
    while True:
        source = open_document_line_source(path, pages=pages)
        if source is None:
            raise KnowledgeExtractionError(f"no line source for document type: {suffix}")
        for located in source.lines:
            if not located.searchable:
                continue
            page = _pdf_page(located.locator) if suffix == ".pdf" else None
            collected.append(Line(_normalize_text(located.text), located.locator, page))
        continuation = source.continuation
        if not continuation:
            break
        if continuation in seen:
            raise KnowledgeExtractionError(f"continuation loop detected: {continuation!r}")
        seen.add(continuation)
        pages = _parse_continuation(continuation)
    return collected
