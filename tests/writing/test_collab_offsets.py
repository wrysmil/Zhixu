"""协作定位口径（契约 §3.1）：归一化映射、范围解析与目标校验。"""

from __future__ import annotations

import pytest

from nanobot.writing.collab.offsets import (
    OffsetError,
    apply_outline_target,
    apply_target,
    normalize_with_map,
    resolve_range,
    validate_target,
)


def test_normalize_crlf_and_cr() -> None:
    body = "甲\r\n乙\r丙\n丁"
    normalized, mapping = normalize_with_map(body)

    assert normalized == "甲\n乙\n丙\n丁"
    assert len(mapping) == len(normalized) + 1
    assert mapping[-1] == len(body)
    for index, raw_index in enumerate(mapping[:-1]):
        char = normalized[index]
        assert body[raw_index] in ("\r", "\n") if char == "\n" else body[raw_index] == char
    # \r\n 占两个原始下标，归一化下标 2（乙）对应原始下标 3
    assert mapping[2] == 3

    first = {"kind": "replace_range", "start": 0, "end": 1, "expected_before": "甲"}
    assert resolve_range(normalized, mapping, first) == (0, 1)
    crlf = {"kind": "replace_range", "start": 1, "end": 2, "expected_before": "\n"}
    raw_start, raw_end = resolve_range(normalized, mapping, crlf)
    assert body[raw_start:raw_end] == "\r\n"


def test_map_handles_cjk_and_emoji() -> None:
    body = "😀开头\n重复段。重复段。\n尾"
    normalized, mapping = normalize_with_map(body)

    assert normalized == body
    assert mapping == list(range(len(body) + 1))
    assert normalized.index("开") == 1  # emoji 在 Python 里只占 1 个码点

    first = normalized.index("重复段。")
    second = normalized.index("重复段。", first + 1)
    target = {
        "kind": "replace_range", "start": second, "end": second + 4,
        "expected_before": "重复段。",
    }
    raw_start, raw_end = resolve_range(normalized, mapping, target)
    assert body[raw_start:raw_end] == "重复段。"
    assert apply_target(body, target, "改写段。") == "😀开头\n重复段。改写段。\n尾"

    normalized_crlf, mapping_crlf = normalize_with_map("😀\r\n尾")
    assert normalized_crlf == "😀\n尾"
    assert mapping_crlf == [0, 1, 3, 4]


def test_replace_range_requires_expected_before() -> None:
    normalized = "第一段。\n第二段。"
    target = {"kind": "replace_range", "start": 0, "end": 4, "expected_before": "第二段。"}

    with pytest.raises(OffsetError) as error:
        validate_target(normalized, target)
    assert (error.value.code, str(error.value)) == ("expected_before_mismatch", "expected_before mismatch")

    with pytest.raises(OffsetError, match="expected_before mismatch"):
        apply_target(normalized, target, "改写。")


def test_insert_at_requires_empty_range() -> None:
    validate_target("abcdef", {"kind": "insert_at", "start": 2, "end": 2})

    with pytest.raises(OffsetError) as error:
        validate_target("abcdef", {"kind": "insert_at", "start": 1, "end": 3})
    assert error.value.code == "insert_range_not_empty"

    assert apply_target("前后", {"kind": "insert_at", "start": 1, "end": 1}, "中") == "前中后"


def test_outline_item_target_requires_existing_id() -> None:
    outline = [{"id": "o-1", "text": "第一章", "order": 0}]
    validate_target("正文", {"kind": "outline_item", "outline_item_id": "o-1"}, outline=outline)

    with pytest.raises(OffsetError) as error:
        validate_target("正文", {"kind": "outline_item", "outline_item_id": "o-9"}, outline=outline)
    assert (error.value.code, str(error.value)) == ("outline_item_not_found", "outline item not found")


def test_out_of_range_and_unknown_kind_rejected() -> None:
    with pytest.raises(OffsetError) as out_of_range:
        validate_target("abc", {"kind": "replace_range", "start": 0, "end": 9, "expected_before": "abc"})
    assert out_of_range.value.code == "out_of_range"

    with pytest.raises(OffsetError) as unknown:
        validate_target("abc", {"kind": "rewrite_everything"})
    assert unknown.value.code == "unsupported_kind"


def test_outline_target_replaces_only_the_anchor_item() -> None:
    outline = [
        {"id": "o-1", "text": "旧标题", "order": 0},
        {"id": "o-2", "text": "保留", "order": 1},
    ]
    target = {"kind": "outline_item", "outline_item_id": "o-1"}
    updated = apply_outline_target(outline, target, ["新标题", "新第二节"])

    assert [item["text"] for item in updated] == ["新标题", "新第二节", "保留"]
    assert [item["order"] for item in updated] == [0, 1, 2]  # 只改锚点，其余顺序不变
    assert updated[0]["id"] == "o-1"  # 锚点项沿用原 id
    assert updated[2]["id"] == "o-2"
    assert "id" not in updated[1]  # 新增项不自造 id，交给 A 契约的服务端生成
