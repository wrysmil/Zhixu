import pytest

from nanobot.knowledge import extractor
from nanobot.utils.document import DocumentLineSource, LocatedDocumentLine


def _source(lines, continuation=None):
    return DocumentLineSource(iter(lines), continuation=continuation)


def test_text_normalization_and_lines(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("ＡＢＣ１２３\r\n第二行\r\n\r\n\r\n第三行", encoding="utf-8")
    lines = extractor.extract_lines(path)
    assert [line.text for line in lines] == ["ABC123", "第二行", "", "第三行"]


def test_gbk_falls_back_to_chardet(tmp_path):
    # 回归：这段 16 字节 GBK 样本在 chardet 5.x 下 confidence 为 0（encoding=None），
    # 仅按「chardet ≥0.7 否则 errors='replace'」会把中文解成乱码，因此实现需要
    # 在 chardet 之后追加确定性的 CJK 兜底（gb18030，GBK/GB2312 超集）。
    path = tmp_path / "a.txt"
    path.write_bytes("部署失败怎么回滚".encode("gbk"))
    lines = extractor.extract_lines(path)
    assert lines[0].text == "部署失败怎么回滚"


def test_longer_gbk_is_detected_by_chardet(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes("部署失败怎么回滚\n请给出回滚步骤\n".encode("gbk"))
    lines = extractor.extract_lines(path)
    assert [line.text for line in lines] == ["部署失败怎么回滚", "请给出回滚步骤"]


def test_utf8_bom_is_stripped(tmp_path):
    path = tmp_path / "bom.txt"
    path.write_bytes("\ufeff第一行\n第二行\n".encode("utf-8"))
    lines = extractor.extract_lines(path)
    assert [line.text for line in lines] == ["第一行", "第二行"]


def test_invalid_bytes_do_not_crash(tmp_path):
    path = tmp_path / "bad.txt"
    path.write_bytes(b"\x80\x81\x82\x83")
    lines = extractor.extract_lines(path)
    assert lines
    assert all(isinstance(line.text, str) for line in lines)


def test_pdf_continuation_is_looped(monkeypatch, tmp_path):
    calls: list[str | None] = []

    def fake(path, *, pages=None):
        calls.append(pages)
        if pages is None:
            return _source(
                [
                    LocatedDocumentLine("第一页", 1, "page=1,line=1"),
                    LocatedDocumentLine("--- Page 1 ---", 2, "", searchable=False),
                ],
                continuation="pages='101-200'",
            )
        return _source([LocatedDocumentLine("后来的内容", 3, "page=101,line=1")])

    monkeypatch.setattr(extractor, "open_document_line_source", fake)
    lines = extractor.extract_lines(tmp_path / "a.pdf")
    assert [line.text for line in lines] == ["第一页", "后来的内容"]
    assert lines[1].page == 101
    assert calls == [None, "101-200"]


def test_pdf_single_line_split_across_pages_is_joined_once(monkeypatch, tmp_path):
    # 跨页逻辑行由解析器切成两段时，续页只出现一次，页码保持 1-based。
    def fake(path, *, pages=None):
        if pages is None:
            return _source(
                [LocatedDocumentLine("上半句", 1, "page=1,line=1")],
                continuation="pages='2-2'",
            )
        return _source([LocatedDocumentLine("下半句", 1, "page=2,line=1")])

    monkeypatch.setattr(extractor, "open_document_line_source", fake)
    lines = extractor.extract_lines(tmp_path / "a.pdf")
    assert [line.text for line in lines] == ["上半句", "下半句"]
    assert [line.page for line in lines] == [1, 2]


def test_empty_pdf_page_terminates(monkeypatch, tmp_path):
    def fake(path, *, pages=None):
        if pages is None:
            return _source([], continuation="pages='2-3'")
        return _source([])

    monkeypatch.setattr(extractor, "open_document_line_source", fake)
    assert extractor.extract_lines(tmp_path / "a.pdf") == []


def test_repeated_continuation_terminates(monkeypatch, tmp_path):
    def fake(path, *, pages=None):
        return _source([LocatedDocumentLine("x", 1, "page=1")], continuation="pages='1-200'")

    monkeypatch.setattr(extractor, "open_document_line_source", fake)
    with pytest.raises(extractor.KnowledgeExtractionError, match="continuation"):
        extractor.extract_lines(tmp_path / "a.pdf")


def test_invalid_continuation_fails_loudly(monkeypatch, tmp_path):
    def fake(path, *, pages=None):
        return _source([LocatedDocumentLine("x", 1, "page=1")], continuation="bad")

    monkeypatch.setattr(extractor, "open_document_line_source", fake)
    with pytest.raises(extractor.KnowledgeExtractionError, match="continuation"):
        extractor.extract_lines(tmp_path / "a.pdf")


def test_unsupported_suffix(tmp_path):
    path = tmp_path / "a.doc"
    path.write_bytes(b"x")
    with pytest.raises(extractor.KnowledgeUnsupportedType):
        extractor.extract_lines(path)


def test_unsupported_type_is_extraction_error(tmp_path):
    path = tmp_path / "a.doc"
    path.write_bytes(b"x")
    with pytest.raises(extractor.KnowledgeExtractionError):
        extractor.extract_lines(path)


def test_rows_are_deterministic(tmp_path):
    path = tmp_path / "a.md"
    path.write_text("# 标题\n\n正文\n", encoding="utf-8")
    assert extractor.extract_lines(path) == extractor.extract_lines(path)
