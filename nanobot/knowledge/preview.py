"""知识文档原文预览：把已入库文档的源文件转成有界、可读的 JSON 结构。

上传与入库只保留 ``source_path``；WebUI 需要「看原文」但不能拿到路径，因此
由服务端按文档行读取源文件，返回 markdown / 纯文本 / PDF 分页 / DOCX 块结构。
所有输出都受字符与条目上限约束，错误信息不含文件系统路径。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MAX_PREVIEW_CHARS = 200_000
MAX_PREVIEW_FILE_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_DOCX_BLOCKS = 2_000
MAX_DOCX_TABLE_ROWS = 100
MAX_DOCX_TABLE_COLUMNS = 12

_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_TEXT_SUFFIXES = frozenset({".txt", ".text"})
_HEADING_STYLE = re.compile(r"^(?:heading|标题)\s*(\d)$", re.IGNORECASE)


class KnowledgePreviewError(Exception):
    """原文预览失败：稳定错误码 + 状态，消息不含文件系统路径。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "KNOWLEDGE_DOC_PREVIEW_FAILED",
        status: int = 400,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def build_document_preview(document: Any) -> dict[str, Any]:
    """按源文件类型生成原文预览；未知格式降级为纯文本或 unsupported。"""
    source = Path(str(getattr(document, "source_path", "") or ""))
    name = str(getattr(document, "original_name", "") or source.name)
    if not source.is_file():
        raise KnowledgePreviewError(
            "文档原文已不存在",
            code="KNOWLEDGE_DOC_SOURCE_MISSING",
            status=404,
        )
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise KnowledgePreviewError(
            "文档原文不可读",
            code="KNOWLEDGE_DOC_SOURCE_UNREADABLE",
            status=500,
        ) from exc

    suffix = source.suffix.lower()
    if suffix in _MARKDOWN_SUFFIXES:
        content, truncated = _read_text(source)
        return _payload(name, size, "markdown", content=content, truncated=truncated)
    if suffix in _TEXT_SUFFIXES:
        content, truncated = _read_text(source)
        return _payload(name, size, "text", content=content, truncated=truncated)
    if suffix == ".pdf":
        pages, total_pages, truncated = _pdf_pages(source)
        return _payload(
            name,
            size,
            "pdf",
            pages=pages,
            total_pages=total_pages,
            truncated=truncated,
        )
    if suffix == ".docx":
        blocks, truncated = _docx_blocks(source)
        return _payload(name, size, "docx", blocks=blocks, truncated=truncated)

    content, truncated = _fallback_text(source)
    if content is None:
        return _payload(name, size, "unsupported")
    return _payload(name, size, "text", content=content, truncated=truncated)


def _payload(
    name: str,
    size: int,
    kind: str,
    *,
    content: str = "",
    pages: list[dict[str, Any]] | None = None,
    total_pages: int = 0,
    blocks: list[dict[str, Any]] | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": name,
        "size": size,
        "truncated": truncated,
        "content": content,
        "pages": pages or [],
        "totalPages": total_pages,
        "blocks": blocks or [],
    }


def _read_text(path: Path) -> tuple[str, bool]:
    _require_reasonable_size(path)
    try:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1")
    except OSError as exc:
        raise KnowledgePreviewError(
            "文档原文不可读",
            code="KNOWLEDGE_DOC_SOURCE_UNREADABLE",
            status=500,
        ) from exc
    if len(text) <= MAX_PREVIEW_CHARS:
        return text, False
    return text[:MAX_PREVIEW_CHARS], True


def _pdf_pages(path: Path) -> tuple[list[dict[str, Any]], int, bool]:
    _require_reasonable_size(path)
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - pypdf is a bundled dependency
        raise KnowledgePreviewError(
            "当前环境缺少 PDF 解析组件",
            code="KNOWLEDGE_DOC_PREVIEW_UNAVAILABLE",
            status=503,
        ) from exc
    try:
        reader = PdfReader(str(path), strict=False)
        total_pages = len(reader.pages)
        pages: list[dict[str, Any]] = []
        consumed = 0
        truncated = total_pages > MAX_PDF_PAGES
        for index in range(min(total_pages, MAX_PDF_PAGES)):
            text = (reader.pages[index].extract_text() or "").strip()
            remaining = MAX_PREVIEW_CHARS - consumed
            if remaining <= 0:
                truncated = True
                break
            if len(text) > remaining:
                text = text[:remaining]
                truncated = True
            consumed += len(text)
            pages.append({"page": index + 1, "text": text})
    except Exception as exc:
        raise KnowledgePreviewError(
            "PDF 解析失败，原文无法预览",
            code="KNOWLEDGE_DOC_PREVIEW_FAILED",
            status=422,
        ) from exc
    return pages, total_pages, truncated


def _docx_blocks(path: Path) -> tuple[list[dict[str, Any]], bool]:
    _require_reasonable_size(path)
    try:
        from docx import Document as DocxDocument
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover - python-docx is bundled
        raise KnowledgePreviewError(
            "当前环境缺少 Word 解析组件",
            code="KNOWLEDGE_DOC_PREVIEW_UNAVAILABLE",
            status=503,
        ) from exc
    try:
        document = DocxDocument(str(path))
        blocks: list[dict[str, Any]] = []
        consumed = 0
        truncated = False
        for block in document.iter_inner_content():
            if len(blocks) >= MAX_DOCX_BLOCKS or consumed >= MAX_PREVIEW_CHARS:
                truncated = True
                break
            if isinstance(block, Paragraph):
                text = block.text.strip()
                if not text:
                    continue
                level = _heading_level(block)
                item: dict[str, Any] = (
                    {"type": "heading", "level": level, "text": text}
                    if level
                    else {"type": "paragraph", "text": text}
                )
                consumed += len(text)
            elif isinstance(block, Table):
                table_rows = list(block.rows)
                rows: list[list[str]] = []
                for row in table_rows[:MAX_DOCX_TABLE_ROWS]:
                    cells = [cell.text.strip() for cell in row.cells]
                    rows.append(cells[:MAX_DOCX_TABLE_COLUMNS])
                    consumed += sum(len(cell) for cell in rows[-1])
                if len(table_rows) > MAX_DOCX_TABLE_ROWS:
                    truncated = True
                item = {"type": "table", "rows": rows}
            else:  # pragma: no cover - 当前 python-docx 只产出段落与表格
                continue
            blocks.append(item)
    except KnowledgePreviewError:
        raise
    except Exception as exc:
        raise KnowledgePreviewError(
            "Word 文档解析失败，原文无法预览",
            code="KNOWLEDGE_DOC_PREVIEW_FAILED",
            status=422,
        ) from exc
    return blocks, truncated


def _heading_level(paragraph: Any) -> int:
    style = getattr(paragraph, "style", None)
    name = str(getattr(style, "name", "") or "")
    match = _HEADING_STYLE.match(name.strip())
    return int(match.group(1)) if match else 0


def _fallback_text(path: Path) -> tuple[str | None, bool]:
    """xlsx/pptx 等格式复用入库文本抽取，失败时返回 unsupported。"""
    from nanobot.utils.document import extract_text

    text = extract_text(path)
    if text is None:
        return None, False
    if len(text) <= MAX_PREVIEW_CHARS:
        return text, False
    return text[:MAX_PREVIEW_CHARS], True


def _require_reasonable_size(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise KnowledgePreviewError(
            "文档原文不可读",
            code="KNOWLEDGE_DOC_SOURCE_UNREADABLE",
            status=500,
        ) from exc
    if size > MAX_PREVIEW_FILE_BYTES:
        raise KnowledgePreviewError(
            "文档过大，暂不支持原文预览",
            code="KNOWLEDGE_DOC_PREVIEW_TOO_LARGE",
            status=413,
        )
