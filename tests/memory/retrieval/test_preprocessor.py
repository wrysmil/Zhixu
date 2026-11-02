"""T-02: MemoryQueryPreprocessor — Gate + anti-injection tests."""
from __future__ import annotations

import inspect

import pytest

from nanobot.memory.retrieval.preprocessor import MemoryQueryPreprocessor


class TestGate:
    """Gate conditions: empty / control_only.

    D10（spec §6.2）：长度闸已删除——短查询不全局禁止，``memory_search``
    是模型明确发起的调用，不能按「值不值得检索」的长度规则拒绝。
    """

    @pytest.mark.parametrize("text", ["", "   ", None])
    def test_should_skip_empty(self, text):
        skip, reason = MemoryQueryPreprocessor.should_skip_retrieval(text)
        assert skip is True
        assert reason == "empty"

    @pytest.mark.parametrize("text", ["好", "嗯", "ok", "yes", "stop", "继续", "可以", "收到"])
    def test_should_skip_control_only(self, text):
        skip, reason = MemoryQueryPreprocessor.should_skip_retrieval(text)
        assert skip is True
        assert reason == "control_only"

    @pytest.mark.parametrize(
        "text",
        [
            "hi",            # ≤3 字英文
            "嗯嗯",          # ≤3 字非控制词
            "知序",          # 中文短子串查询
            "健身",
            "那个项目",      # 不自足短查询（补全交给 decomposer）
            "明天天气?",     # 提示词字符
            "查一下我的记忆",  # 记忆锚点短句
            "明天天气怎么样",  # 首轮无历史的正常短消息
            "你好，请问明天的天气如何呢",
            "我之前写的那个 Python 爬虫脚本还能用吗",
        ],
    )
    def test_short_or_plain_query_is_not_rejected(self, text):
        skip, reason = MemoryQueryPreprocessor.should_skip_retrieval(text)
        assert skip is False, f"{text!r} 被误杀：{reason}"
        assert reason == ""

    def test_recent_messages_parameter_is_gone(self):
        """``recent_messages`` 死参数已删除（D13）：不再出现在预处理签名里。"""
        for method in (
            MemoryQueryPreprocessor.should_skip_retrieval,
            MemoryQueryPreprocessor.prepare,
        ):
            assert "recent_messages" not in inspect.signature(method).parameters


class TestAntiInjection:
    """Injection block removal."""

    def test_clean_query_removes_related_memory_section(self):
        raw = (
            "用户问题: 你好\n"
            "## 相关记忆\n过去我们聊过 Python\n##\n"
            "用户问题: 真正的问题"
        )
        cleaned = MemoryQueryPreprocessor.clean_query(raw)
        assert "相关记忆" not in cleaned
        assert "真正的问题" in cleaned

    def test_clean_query_removes_vault_context(self):
        raw = "<vault-context>block me</vault-context>\n真正的问题"
        cleaned = MemoryQueryPreprocessor.clean_query(raw)
        assert "<vault-context>" not in cleaned
        assert "真正的问题" in cleaned

    def test_clean_query_removes_memory_tag(self):
        raw = "<memory>injected content</memory>\n真正的问题"
        cleaned = MemoryQueryPreprocessor.clean_query(raw)
        assert "<memory>" not in cleaned
        assert "真正的问题" in cleaned

    def test_clean_query_removes_long_term_memory_tag(self):
        raw = "<long-term-memory>injected content</long-term-memory>\n真正的问题"
        cleaned = MemoryQueryPreprocessor.clean_query(raw)
        assert "<long-term-memory>" not in cleaned
        assert "真正的问题" in cleaned

    def test_clean_query_removes_core_memory_section(self):
        raw = "## 核心记忆\n过去我们聊过\n## 长期记忆\n更多信息\n真正的问题"
        cleaned = MemoryQueryPreprocessor.clean_query(raw)
        assert "核心记忆" not in cleaned
        assert "长期记忆" not in cleaned
        assert "真正的问题" in cleaned

    def test_clean_query_removes_sections_with_no_trailing_content(self):
        """When a section is the last thing, it gets fully stripped."""
        raw = "## 核心记忆\n过去我们聊过\n## 长期记忆\n更多信息"
        cleaned = MemoryQueryPreprocessor.clean_query(raw)
        assert "核心记忆" not in cleaned
        assert "长期记忆" not in cleaned
        # content before sections is preserved
        assert "过去我们聊过" in cleaned
        assert "更多信息" in cleaned


class TestPrepare:
    """Combined prepare() method."""

    def test_prepare_returns_cleaned_and_skip_flag(self):
        prepared = MemoryQueryPreprocessor.prepare(
            "## 相关记忆\n之前聊过一些话题\ninject",
        )
        # "之前聊过一些话题\ninject" is not a control word
        assert prepared.skip is False
        assert "相关记忆" not in prepared.cleaned_query

    def test_prepare_control_word_is_skipped(self):
        """A pure control word with no injection blocks triggers skip."""
        prepared = MemoryQueryPreprocessor.prepare("好")
        assert prepared.skip is True
        assert prepared.reason == "control_only"
        assert prepared.cleaned_query == "好"

    def test_prepare_short_query_passes(self):
        """短查询（含中文子串）不再被长度规则拒绝。"""
        prepared = MemoryQueryPreprocessor.prepare("知序")
        assert prepared.skip is False
        assert prepared.reason == ""
        assert prepared.cleaned_query == "知序"

    def test_prepare_pass_case(self):
        prepared = MemoryQueryPreprocessor.prepare(
            "我之前写的那个 Python 爬虫脚本还能用吗",
        )
        assert prepared.skip is False
        assert prepared.reason == ""
        assert "Python" in prepared.cleaned_query

    def test_prepare_none_query(self):
        """None input should be treated as empty."""
        prepared = MemoryQueryPreprocessor.prepare(None)
        assert prepared.skip is True
        assert prepared.reason == "empty"
