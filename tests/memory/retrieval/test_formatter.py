"""Tests for RetrievalFormatter (T-11)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.memory.retrieval.candidate import MemoryHit, RetrievalCandidate
from nanobot.memory.retrieval.formatter import RetrievalFormatter


def _memory(content: str, type_value: str = "fact") -> Any:
    """构造简化的 Memory：type 为带 ``.value`` 的对象（模拟 MemoryType Enum）。"""
    return SimpleNamespace(content=content, type=SimpleNamespace(value=type_value))


def _cand(
    content: str,
    *,
    score: float,
    type_value: str = "fact",
    memory_id: str = "m",
    channel: str = "semantic",
    raw: Any = None,
) -> RetrievalCandidate:
    if raw is None:
        raw = _memory(content, type_value=type_value)
    return RetrievalCandidate(
        hit=MemoryHit(
            kind="memory",
            record_id=memory_id,
            content=content,
            workspace_id="default",
        ),
        source_channel=channel,
        composite_score=score,
        raw=raw,
    )


# ---- 行为契约：format() 返回结构 ----


def test_format_empty_returns_empty():
    assert RetrievalFormatter().format([]) == []


def test_format_limit_zero_returns_empty():
    cands = [_cand("x", score=0.9)]
    assert RetrievalFormatter(limit=0).format(cands) == []


def test_format_returns_top_n_sorted_by_score_desc():
    cands = [
        _cand("low", score=0.3, memory_id="low"),
        _cand("highest", score=0.95, memory_id="hi"),
        _cand("mid", score=0.6, memory_id="mid"),
        _cand("higher", score=0.7, memory_id="higher"),
        _cand("top", score=0.85, memory_id="top"),
    ]
    out = RetrievalFormatter(limit=3).format(cands)
    assert [d["content"] for d in out] == ["highest", "top", "higher"]


def test_format_limit_larger_than_input_returns_all_sorted():
    cands = [_cand("a", score=0.5), _cand("b", score=0.7)]
    out = RetrievalFormatter(limit=10).format(cands)
    assert [d["content"] for d in out] == ["b", "a"]


def test_format_default_limit_is_10():
    cands = [_cand(f"item-{i:02d}", score=i * 0.05, memory_id=f"m-{i:02d}")
             for i in range(15)]
    out = RetrievalFormatter().format(cands)
    assert len(out) == 10
    # top-10 按 score 降序：item-14 (0.70), item-13 (0.65), ..., item-05 (0.25)
    expected = [f"item-{i:02d}" for i in range(14, 4, -1)]
    assert [d["content"] for d in out] == expected


# ---- 行为契约：reason 标签 ----


def test_format_reason_high_at_threshold():
    c = _cand("x", score=0.8)
    out = RetrievalFormatter().format([c])
    assert out[0]["reason"] == "高度相关"


def test_format_reason_high_above_threshold():
    c = _cand("x", score=0.95)
    out = RetrievalFormatter().format([c])
    assert out[0]["reason"] == "高度相关"


def test_format_reason_mid_at_lower_bound():
    c = _cand("x", score=0.5)
    out = RetrievalFormatter().format([c])
    assert out[0]["reason"] == "中等相关"


def test_format_reason_mid_below_high_boundary():
    """0.799 未取整 → 仍属 "中等相关"，不因四舍五入越界。"""
    c = _cand("x", score=0.799)
    out = RetrievalFormatter().format([c])
    assert out[0]["reason"] == "中等相关"


def test_format_reason_low_below_mid():
    c = _cand("x", score=0.499)
    out = RetrievalFormatter().format([c])
    assert out[0]["reason"] == "弱相关"


def test_format_reason_low_zero():
    c = _cand("x", score=0.0)
    out = RetrievalFormatter().format([c])
    assert out[0]["reason"] == "弱相关"


# ---- _score_label 静态方法 ----


def test_score_label_high_at_threshold():
    assert RetrievalFormatter._score_label(0.8) == "高度相关"


def test_score_label_high_above_threshold():
    assert RetrievalFormatter._score_label(0.95) == "高度相关"


def test_score_label_mid_at_lower_bound():
    assert RetrievalFormatter._score_label(0.5) == "中等相关"


def test_score_label_mid_below_high_boundary():
    assert RetrievalFormatter._score_label(0.799) == "中等相关"


def test_score_label_low_below_mid():
    assert RetrievalFormatter._score_label(0.499) == "弱相关"


def test_score_label_low_zero():
    assert RetrievalFormatter._score_label(0.0) == "弱相关"


# ---- 输出 dict 字段完整性 ----


def test_format_output_keys_complete():
    c = _cand("Python 爬虫脚本", score=0.9, type_value="preference")
    out = RetrievalFormatter().format([c])
    assert set(out[0].keys()) == {
        "content", "type", "score", "reason", "memory_id", "entry_kind", "entry_status",
    }


def test_format_output_content_type_reason_match():
    c = _cand("Python 爬虫脚本", score=0.9, type_value="preference")
    out = RetrievalFormatter().format([c])
    item = out[0]
    assert item["content"] == "Python 爬虫脚本"
    assert item["type"] == "preference"
    assert item["reason"] == "高度相关"


# ---- score 取整 ----


def test_format_score_rounded_to_two_decimals():
    c = _cand("x", score=0.87654)
    out = RetrievalFormatter().format([c])
    assert out[0]["score"] == pytest.approx(0.88, abs=1e-9)


def test_format_score_exact_two_decimals_preserved():
    c = _cand("x", score=0.85)
    out = RetrievalFormatter().format([c])
    assert out[0]["score"] == pytest.approx(0.85, abs=1e-9)


# ---- 重复 score 不去重 ----


def test_format_keeps_duplicate_scores_no_dedup():
    cands = [
        _cand("a", score=0.7, memory_id="a"),
        _cand("b", score=0.7, memory_id="b"),
    ]
    out = RetrievalFormatter().format(cands)
    assert len(out) == 2
    assert {d["content"] for d in out} == {"a", "b"}


# ---- 不抛异常 ----


def test_format_does_not_throw_on_missing_raw():
    c = RetrievalCandidate(
        hit=MemoryHit(
            kind="memory", record_id="x", content="content", workspace_id="default",
        ),
        source_channel="semantic", composite_score=0.5, raw=None,
    )
    out = RetrievalFormatter().format([c])
    assert out[0]["type"] == ""
    assert out[0]["content"] == "content"


def test_format_accepts_string_type_on_raw():
    """raw.type 为裸字符串时（某些测试 stub），不应抛 AttributeError。"""
    raw = SimpleNamespace(type="fact")
    c = _cand("x", score=0.5, raw=raw)
    out = RetrievalFormatter().format([c])
    assert out[0]["type"] == "fact"


def test_format_does_not_throw_on_raw_without_type():
    raw = SimpleNamespace(content="x")
    c = _cand("x", score=0.5, raw=raw)
    out = RetrievalFormatter().format([c])
    assert out[0]["type"] == ""


def test_format_uses_real_memory_type_value():
    """集成：与 MemoryType 枚举兼容。"""
    from nanobot.memory.models import Memory, MemoryType

    m = Memory(
        id="m-1", content="content",
        created_at="2025-01-01T00:00:00+00:00",
        type=MemoryType.PREFERENCE,
    )
    c = RetrievalCandidate(
        hit=MemoryHit(
            kind="memory", record_id="m-1", content="content", workspace_id="default",
        ),
        source_channel="semantic", composite_score=0.9, raw=m,
    )
    out = RetrievalFormatter().format([c])
    assert out[0]["type"] == "preference"


# ---- 构造函数属性 ----


def test_limit_property_reflects_constructor():
    f = RetrievalFormatter(limit=7)
    assert f.limit == 7


def test_default_limit_is_10():
    assert RetrievalFormatter().limit == 10


def test_constructor_rejects_negative_limit():
    with pytest.raises(ValueError, match="limit"):
        RetrievalFormatter(limit=-1)
