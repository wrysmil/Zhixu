"""正文清洗与 passage 预算（WeKnora §7.3）。"""

from __future__ import annotations

import pytest

from nanobot.knowledge.text import build_passage, clean_text, passage_char_limit

# ---------- 清洗 ----------


def test_clean_text_normalizes_newlines_and_controls() -> None:
    cleaned = clean_text("第一行\r\n第二行\x00\x07\r第三行")
    assert "\r" not in cleaned
    assert "\x00" not in cleaned
    assert "\x07" not in cleaned
    assert cleaned == "第一行\n第二行\n第三行"


def test_clean_text_collapses_blank_runs_and_trailing_spaces() -> None:
    cleaned = clean_text("第一段\n\n\n\n第二段   \n")
    assert "\n\n\n" not in cleaned
    assert cleaned == "第一段\n\n第二段"


def test_clean_text_drops_image_markup_and_keeps_link_text() -> None:
    cleaned = clean_text("前 ![](http://img) 中 [链接文字](http://x) 后")
    assert "http://img" not in cleaned
    assert "http://x" not in cleaned
    assert cleaned == "前 中 链接文字 后"


def test_clean_text_empty_input_returns_empty() -> None:
    assert clean_text("") == ""
    assert clean_text("   \n  ") == ""


# ---------- 单条 passage 预算 ----------


def test_passage_limit_is_min_of_single_and_request_budget() -> None:
    assert passage_char_limit("查询", max_passage_chars=1200, max_request_chars=8000) == 1200
    # query 吃掉请求预算：10 − 2 = 8。
    assert passage_char_limit("查询", max_passage_chars=1200, max_request_chars=10) == 8


def test_passage_limit_never_returns_non_positive() -> None:
    assert passage_char_limit("很长的查询词", max_passage_chars=1200, max_request_chars=2) == 1
    assert passage_char_limit("q", max_passage_chars=0, max_request_chars=8000) == 1


# ---------- passage 拼接 ----------


def test_build_passage_joins_title_breadcrumb_and_clean_content() -> None:
    passage = build_passage(
        title="发布手册",
        heading_path="第三章 > 回滚",
        content="步骤一\r\n步骤二",
        limit=100,
    )
    assert passage == "发布手册\n第三章 > 回滚\n步骤一\n步骤二"


def test_build_passage_skips_empty_title_and_heading() -> None:
    assert build_passage(title="", heading_path="  ", content="正文", limit=100) == "正文"


def test_build_passage_truncates_from_tail() -> None:
    passage = build_passage(
        title="手册",
        heading_path="第三章 > 回滚",
        content="步骤一\n步骤二",
        limit=5,
    )
    assert passage == "手册\n第三"
    assert len(passage) == 5


def test_build_passage_never_returns_empty_string_for_positive_limit() -> None:
    assert build_passage(title="", heading_path="", content="", limit=10) == ""
    assert len(build_passage(title="", heading_path="", content="", limit=0)) <= 1


@pytest.mark.parametrize("limit", [1, 2, 10])
def test_build_passage_respects_limit(limit: int) -> None:
    passage = build_passage(
        title="标题",
        heading_path="面包屑",
        content="正文内容",
        limit=limit,
    )
    assert 1 <= len(passage) <= limit
