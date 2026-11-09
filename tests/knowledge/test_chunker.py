import pytest

from nanobot.knowledge.chunker import (
    ChunkDraft,
    KnowledgeChunkError,
    chunk_lines,
    validate_chunks,
)
from nanobot.knowledge.extractor import Line


def lines(*texts, page=None):
    return [Line(text, f"page={page}" if page else "", page) for text in texts]


def test_heading_path_stack_without_cross_section_merge():
    drafts = chunk_lines(
        lines("# 第一章", "正文甲", "## 1.1 节", "正文乙"),
        chunk_size=800,
        chunk_overlap=120,
        paragraph_mode="blank_line",
    )
    assert [d.heading_path for d in drafts] == ["第一章", "第一章 > 1.1 节"]


def test_paragraphs_are_aggregated_until_budget():
    para = "字" * 300
    draft_list = chunk_lines(
        lines(para, "", para, "", para),
        chunk_size=800,
        chunk_overlap=0,
        paragraph_mode="blank_line",
    )
    assert len(draft_list) == 2  # 300 + 1 + 300 = 601；第三个放不下
    assert draft_list[0].content.endswith("字")


def test_long_paragraph_cuts_at_sentence_boundary():
    sentence = "这是一个句子。" * 100  # 700 字
    drafts = chunk_lines(
        lines(sentence), chunk_size=300, chunk_overlap=0, paragraph_mode="blank_line"
    )
    assert all(draft.char_count <= 300 for draft in drafts)
    assert all(draft.content.endswith("。") for draft in drafts)


def test_overlap_is_applied_within_section_only():
    drafts = chunk_lines(
        lines("# 一", "甲" * 400, "# 二", "乙" * 400),
        chunk_size=300,
        chunk_overlap=50,
        paragraph_mode="blank_line",
    )
    first_section = [draft for draft in drafts if "甲" in draft.content]
    assert first_section
    assert all("乙" not in draft.content for draft in first_section)
    assert drafts[-1].content.startswith("乙")


def test_offsets_match_content_slices():
    drafts = chunk_lines(
        lines("第一段。", "", "第二段。", "", "第三段。"),
        chunk_size=10,
        chunk_overlap=2,
        paragraph_mode="blank_line",
    )
    text = "\n".join(line.text for line in lines("第一段。", "", "第二段。", "", "第三段。"))
    for draft in drafts:
        assert text[draft.start_offset : draft.end_offset] == draft.content


def test_deterministic_output():
    args = dict(chunk_size=120, chunk_overlap=20, paragraph_mode="blank_line")
    items = lines("甲" * 200, "", "乙" * 200)
    assert chunk_lines(items, **args) == chunk_lines(items, **args)


def test_invalid_overlap_rejected():
    with pytest.raises(ValueError):
        chunk_lines(lines("x"), chunk_size=100, chunk_overlap=100, paragraph_mode="line")


# --- Task 9 质量回归：终止性、标题栈、偏移不变量、页/定位 ---


def test_overlap_not_smaller_than_chunk_size_terminates():
    with pytest.raises(ValueError):
        chunk_lines(lines("abc"), chunk_size=4, chunk_overlap=4, paragraph_mode="blank_line")
    with pytest.raises(ValueError):
        chunk_lines(lines("abc"), chunk_size=2, chunk_overlap=5, paragraph_mode="line")
    with pytest.raises(ValueError):
        chunk_lines(lines("abc"), chunk_size=0, chunk_overlap=0, paragraph_mode="line")


def test_heading_level_jump_and_repeated_heading():
    drafts = chunk_lines(
        lines("# A", "甲" * 400, "### C", "乙" * 400, "# A", "丙" * 400),
        chunk_size=800,
        chunk_overlap=0,
        paragraph_mode="blank_line",
    )
    assert [draft.heading_path for draft in drafts] == ["A", "A > C", "A"]


def test_heading_lines_are_never_dropped():
    headings = ["# 第一章", "## 1.1", "### 1.1.1", "# 第二章"]
    items: list[Line] = []
    for heading in headings:
        items.append(Line(heading, "", None))
        items.append(Line("正文" * 200, "", None))
    drafts = chunk_lines(items, chunk_size=300, chunk_overlap=50, paragraph_mode="blank_line")
    joined = "".join(draft.content for draft in drafts)
    for heading in headings:
        assert heading in joined


def test_chunks_cover_all_visible_text_without_gaps_or_drift():
    items = [
        Line("# 标题", "", None),
        Line("正文一。", "", None),
        Line("正文二。", "", None),
        Line("", "", None),
        Line("# 另一个", "", None),
        Line("尾巴。", "", None),
    ]
    text = "\n".join(line.text for line in items)
    drafts = chunk_lines(items, chunk_size=12, chunk_overlap=3, paragraph_mode="blank_line")
    assert drafts[0].start_offset == 0
    assert drafts[-1].end_offset == len(text)
    for draft in drafts:
        assert text[draft.start_offset : draft.end_offset] == draft.content
        assert draft.char_count == draft.end_offset - draft.start_offset
    for previous, current in zip(drafts, drafts[1:]):
        assert current.start_offset <= previous.end_offset  # 无空洞
        assert previous.end_offset - current.start_offset <= 3  # 重叠不超预算
    covered = set()
    for draft in drafts:
        covered.update(range(draft.start_offset, draft.end_offset))
    missing = [i for i, char in enumerate(text) if not char.isspace() and i not in covered]
    assert missing == []


def test_page_and_locator_follow_each_chunk_first_line():
    drafts = chunk_lines(
        [Line("甲" * 5, "loc-1", 1), Line("乙" * 5, "loc-2", 2)],
        chunk_size=6,
        chunk_overlap=0,
        paragraph_mode="blank_line",
    )
    assert [(draft.page, draft.locator) for draft in drafts] == [(1, "loc-1"), (2, "loc-2")]


def test_mixed_cjk_ascii_cuts_on_sentence_end():
    sentence = "Hello 世界。" * 30
    drafts = chunk_lines(
        lines(sentence), chunk_size=100, chunk_overlap=0, paragraph_mode="blank_line"
    )
    assert len(drafts) >= 2
    assert all(draft.char_count <= 100 for draft in drafts)
    assert all(draft.content.endswith("。") for draft in drafts)


def test_fragment_merges_with_following_same_heading_chunk():
    drafts = chunk_lines(
        lines("短句。", "字" * 200),
        chunk_size=100,
        chunk_overlap=0,
        paragraph_mode="blank_line",
    )
    assert len(drafts) == 2
    assert drafts[0].char_count == 103
    assert drafts[0].content == "短句。\n" + "字" * 99


def test_empty_or_whitespace_lines_produce_no_chunks():
    assert chunk_lines([], chunk_size=100, chunk_overlap=0, paragraph_mode="blank_line") == []
    assert (
        chunk_lines(lines("", "   "), chunk_size=100, chunk_overlap=0, paragraph_mode="line")
        == []
    )
    assert (
        chunk_lines(lines("", "# 标题", "正文"), chunk_size=2, chunk_overlap=0,
                    paragraph_mode="blank_line")
        != []
    )


def test_unknown_paragraph_mode_rejected():
    with pytest.raises(ValueError):
        chunk_lines(lines("x"), chunk_size=100, chunk_overlap=0, paragraph_mode="word")


# --- Task 10 校验器：空文本、超长单块、切太碎、块数上限、偏移不变量 ---


def test_empty_text_is_text_empty():
    with pytest.raises(KnowledgeChunkError) as exc:
        validate_chunks([], chunk_size=800, chunk_overlap=120, text="   \n")
    assert exc.value.code == "KNOWLEDGE_TEXT_EMPTY"


def test_single_oversized_chunk_is_invalid():
    draft = ChunkDraft("x" * 2000, 0, 2000, "", None, "")
    with pytest.raises(KnowledgeChunkError) as exc:
        validate_chunks([draft], chunk_size=800, chunk_overlap=0, text=draft.content)
    assert exc.value.code == "KNOWLEDGE_CHUNK_INVALID"


def test_too_many_fragments_is_invalid():
    text = "\n".join("a" for _ in range(8))
    drafts = [ChunkDraft("a", i, i + 1, "", None, "") for i in range(8)]
    with pytest.raises(KnowledgeChunkError) as exc:
        validate_chunks(drafts, chunk_size=800, chunk_overlap=0, text=text)
    assert exc.value.code == "KNOWLEDGE_CHUNK_INVALID"


def test_chunk_limit_is_enforced():
    drafts = [ChunkDraft("a", i, i + 1, "", None, "") for i in range(20001)]
    with pytest.raises(KnowledgeChunkError) as exc:
        validate_chunks(drafts, chunk_size=800, chunk_overlap=0, text="a" * 20001)
    assert exc.value.code == "KNOWLEDGE_CHUNK_LIMIT"


def test_valid_chunks_pass():
    drafts = [
        ChunkDraft("abc", 0, 3, "", None, ""),
        ChunkDraft("cde", 2, 5, "", None, ""),
    ]
    validate_chunks(drafts, chunk_size=800, chunk_overlap=2, text="abcde")


# --- Task 10 边界回归：阈值必须是严格大于，偏移不变量逐块可查 ---


def test_boundary_single_chunk_at_exactly_twice_chunk_size_passes():
    draft = ChunkDraft("x" * 1600, 0, 1600, "", None, "")
    validate_chunks([draft], chunk_size=800, chunk_overlap=0, text=draft.content)


def test_boundary_exactly_one_quarter_small_chunks_passes():
    drafts = [
        ChunkDraft("short", 0, 5, "", None, ""),
        ChunkDraft("y" * 100, 5, 105, "", None, ""),
        ChunkDraft("z" * 100, 105, 205, "", None, ""),
        ChunkDraft("w" * 100, 205, 305, "", None, ""),
    ]
    validate_chunks(
        drafts, chunk_size=800, chunk_overlap=0, text="short" + "y" * 100 + "z" * 100 + "w" * 100
    )


def test_boundary_more_than_one_quarter_small_chunks_fails():
    drafts = [
        ChunkDraft("short", 0, 5, "", None, ""),
        ChunkDraft("tiny", 5, 9, "", None, ""),
        ChunkDraft("z" * 100, 9, 109, "", None, ""),
        ChunkDraft("w" * 100, 109, 209, "", None, ""),
    ]
    with pytest.raises(KnowledgeChunkError) as exc:
        validate_chunks(
            drafts, chunk_size=800, chunk_overlap=0, text="short" + "tiny" + "z" * 200
        )
    assert exc.value.code == "KNOWLEDGE_CHUNK_INVALID"


def test_boundary_exactly_20000_chunks_passes():
    drafts = [
        ChunkDraft("c" * 50, i * 50, i * 50 + 50, "", None, "") for i in range(20_000)
    ]
    validate_chunks(drafts, chunk_size=100, chunk_overlap=0, text="c" * 1_000_000)


def test_offset_mismatch_and_gap_and_overlap_are_rejected():
    mismatch = [ChunkDraft("XXX", 0, 3, "", None, "")]
    with pytest.raises(KnowledgeChunkError) as exc:
        validate_chunks(mismatch, chunk_size=800, chunk_overlap=0, text="abcdef")
    assert exc.value.code == "KNOWLEDGE_CHUNK_INVALID"

    empty_span = [ChunkDraft("", 2, 2, "", None, "")]
    with pytest.raises(KnowledgeChunkError) as exc:
        validate_chunks(empty_span, chunk_size=800, chunk_overlap=0, text="abcdef")
    assert exc.value.code == "KNOWLEDGE_CHUNK_INVALID"

    gap = [ChunkDraft("ab", 0, 2, "", None, ""), ChunkDraft("ef", 4, 6, "", None, "")]
    with pytest.raises(KnowledgeChunkError):
        validate_chunks(gap, chunk_size=800, chunk_overlap=0, text="abcdef")

    overlap = [ChunkDraft("ab", 0, 2, "", None, ""), ChunkDraft("bcd", 1, 4, "", None, "")]
    with pytest.raises(KnowledgeChunkError) as exc:
        validate_chunks(overlap, chunk_size=800, chunk_overlap=0, text="abcdef")
    assert exc.value.code == "KNOWLEDGE_CHUNK_INVALID"


def test_non_empty_text_without_chunks_is_invalid():
    with pytest.raises(KnowledgeChunkError) as exc:
        validate_chunks([], chunk_size=800, chunk_overlap=0, text="有效文本")
    assert exc.value.code == "KNOWLEDGE_CHUNK_INVALID"


def test_chunker_output_always_passes_validator():
    items = [
        Line("# 标题", "", None),
        Line("正文。" * 700, "", None),
        Line("尾巴。", "", None),
    ]
    text = "\n".join(line.text for line in items)
    args = dict(chunk_size=300, chunk_overlap=50, paragraph_mode="blank_line")
    validate_chunks(
        chunk_lines(items, **args), chunk_size=300, chunk_overlap=50, text=text
    )
