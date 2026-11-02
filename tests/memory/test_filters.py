"""Tests for Phase 2 anti-pollution filters.

Covers: is_task_artifact / is_ai_self_talk / compute_content_hash.

相似度丢弃与 FilterResult 已随 D08 删除：去重/纠正/冲突统一由
``nanobot.memory.writer`` 的写入决策表判断（见 ``test_writer.py``）。
"""

from __future__ import annotations

from nanobot.memory.filters import (
    compute_content_hash,
    is_ai_self_talk,
    is_task_artifact,
)


class TestIsTaskArtifact:
    """Task artifact detection tests (plan §5.2 / §10 Task 1 Step 1)."""

    def test_chinese_generate_report_is_artifact(self):
        """帮我生成一份报告 -> True"""
        assert is_task_artifact("帮我生成一份报告") is True

    def test_chinese_search_is_artifact(self):
        """帮我搜索 Python 教程 -> True"""
        assert is_task_artifact("帮我搜索 Python 教程") is True

    def test_chinese_please帮我_is_artifact(self):
        """请帮我写代码 -> True"""
        assert is_task_artifact("请帮我写代码") is True

    def test_chinese_帮我做_is_artifact(self):
        """帮我做数据分析 -> True"""
        assert is_task_artifact("帮我做数据分析") is True

    def test_chinese_帮我查找_is_artifact(self):
        """帮我查找相关信息 -> True"""
        assert is_task_artifact("帮我查找相关信息") is True

    def test_chinese_normal_preference_is_not_artifact(self):
        """用户喜欢简洁风格 -> False"""
        assert is_task_artifact("用户喜欢简洁风格") is False

    def test_chinese_fact_is_not_artifact(self):
        """用户已经完成了报告 -> False"""
        assert is_task_artifact("用户已经完成了报告") is False

    def test_chinese_user_preference_is_not_artifact(self):
        """用户偏好深色主题 -> False"""
        assert is_task_artifact("用户偏好深色主题") is False

    def test_english_task_request_is_artifact(self):
        """search X for me -> True"""
        assert is_task_artifact("search the web for me") is True

    def test_english_please_generate_is_artifact(self):
        """please generate a report -> True"""
        assert is_task_artifact("please generate a report") is True

    def test_english_can_you_is_artifact(self):
        """can you find the file -> True"""
        assert is_task_artifact("can you find the file") is True

    def test_english_normal_preference_is_not_artifact(self):
        """user prefers dark mode -> False"""
        assert is_task_artifact("user prefers dark mode") is False

    def test_empty_string_is_not_artifact(self):
        assert is_task_artifact("") is False

    def test_whitespace_only_is_not_artifact(self):
        assert is_task_artifact("   ") is False

    def test_neutral_text_is_not_artifact(self):
        """普通陈述性句子不应被识别为任务产物"""
        assert is_task_artifact("Python 是一门编程语言") is False
        assert is_task_artifact("今天天气很好") is False


class TestIsAiSelfTalk:
    """AI self-talk detection tests (plan §5.2 / §10 Task 1 Step 1)."""

    def test_chinese_我建议_is_self_talk(self):
        """我建议使用 uv -> True"""
        assert is_ai_self_talk("我建议使用 uv") is True

    def test_chinese_作为AI_is_self_talk(self):
        """作为AI，我认为... -> True"""
        assert is_ai_self_talk("作为AI，我认为这个问题很重要") is True

    def test_chinese_作为语言模型_is_self_talk(self):
        """作为语言模型，我来解释 -> True"""
        assert is_ai_self_talk("作为语言模型，我来解释这个概念") is True

    def test_chinese_根据我的分析_is_self_talk(self):
        """根据我的分析，这个方案可行 -> True"""
        assert is_ai_self_talk("根据我的分析，这个方案可行") is True

    def test_chinese_以下是_is_self_talk(self):
        """以下是我的建议 -> True"""
        assert is_ai_self_talk("以下是我的建议") is True

    def test_chinese_根据搜索结果_is_self_talk(self):
        """根据搜索结果，最新版本是... -> True"""
        assert is_ai_self_talk("根据搜索结果，最新版本是 3.12") is True

    def test_chinese_user_preference_is_not_self_talk(self):
        """用户偏好 uv -> False"""
        assert is_ai_self_talk("用户偏好 uv") is False

    def test_chinese_user_fact_is_not_self_talk(self):
        """用户使用 Python 开发 -> False"""
        assert is_ai_self_talk("用户使用 Python 开发") is False

    def test_chinese_user_prefers_is_not_self_talk(self):
        """用户喜欢使用 Docker -> False"""
        assert is_ai_self_talk("用户喜欢使用 Docker") is False

    def test_english_as_an_ai_is_self_talk(self):
        """As an AI, I recommend... -> True"""
        assert is_ai_self_talk("As an AI, I recommend using poetry") is True

    def test_english_i_suggest_is_self_talk(self):
        """I suggest we use pytest -> True"""
        assert is_ai_self_talk("I suggest we use pytest") is True

    def test_english_based_on_my_analysis_is_self_talk(self):
        """Based on my analysis, the best approach is... -> True"""
        assert is_ai_self_talk("Based on my analysis, the best approach is modular design") is True

    def test_english_heres_my_suggestion_is_self_talk(self):
        """Here's my suggestion: use virtual environments -> True"""
        assert is_ai_self_talk("Here's my suggestion: use virtual environments") is True

    def test_english_user_preference_is_not_self_talk(self):
        """User prefers dark mode -> False"""
        assert is_ai_self_talk("User prefers dark mode") is False

    def test_empty_string_is_not_self_talk(self):
        assert is_ai_self_talk("") is False

    def test_whitespace_only_is_not_self_talk(self):
        assert is_ai_self_talk("   ") is False


class TestComputeContentHash:
    """Content hash tests (deterministic / collision-resistant)."""

    def test_same_input_produces_same_hash(self):
        """同一输入两次 -> 相同 hash"""
        h1 = compute_content_hash("用户喜欢 Python", "user", "likes")
        h2 = compute_content_hash("用户喜欢 Python", "user", "likes")
        assert h1 == h2

    def test_different_content_produces_different_hash(self):
        """不同内容 -> 不同 hash"""
        h1 = compute_content_hash("内容A", "subject", "predicate")
        h2 = compute_content_hash("内容B", "subject", "predicate")
        assert h1 != h2

    def test_different_subject_produces_different_hash(self):
        """不同主语 -> 不同 hash（即使是相同内容）"""
        h1 = compute_content_hash("喜欢", "user_a", "likes")
        h2 = compute_content_hash("喜欢", "user_b", "likes")
        assert h1 != h2

    def test_different_predicate_produces_different_hash(self):
        """不同谓词 -> 不同 hash"""
        h1 = compute_content_hash("内容", "user", "likes")
        h2 = compute_content_hash("内容", "user", "dislikes")
        assert h1 != h2

    def test_hash_is_hex_string(self):
        """hash 输出为十六进制字符串"""
        h = compute_content_hash("test", "s", "p")
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_has_expected_length(self):
        """SHA1 hex = 40 characters"""
        h = compute_content_hash("test", "s", "p")
        assert len(h) == 40

    def test_whitespace_is_normalized(self):
        """多余空白被规范化，不会影响 hash"""
        h1 = compute_content_hash("内容", "s", "p")
        h2 = compute_content_hash("  内容  ", " s ", " p ")
        assert h1 == h2

    def test_empty_inputs_produce_consistent_hash(self):
        """空字符串作为输入也能产生确定 hash"""
        h1 = compute_content_hash("", "", "")
        h2 = compute_content_hash("", "", "")
        assert h1 == h2
        assert len(h1) == 40
