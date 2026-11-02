"""Phase 2 提示词常量测试。

验证 prompts.py 中的提示词常量符合规范。
"""

from nanobot.memory.prompts import (
    EPISODE_EXTRACTION_PROMPT,
    SEMANTIC_EXTRACTION_PROMPT,
)


class TestSemanticExtractionPrompt:
    """§4.1 语义记忆抽取提示词测试"""

    def test_contains_user_profile_keywords(self):
        """memories（用户画像）应包含 user_profile 相关关键字"""
        assert 'user_profile' in SEMANTIC_EXTRACTION_PROMPT or 'memories' in SEMANTIC_EXTRACTION_PROMPT

    def test_contains_task_experience_keywords(self):
        """experiences（任务经验）应包含 task_experience 相关关键字"""
        assert 'task_experience' in SEMANTIC_EXTRACTION_PROMPT or 'experiences' in SEMANTIC_EXTRACTION_PROMPT

    def test_contains_memory_types(self):
        """应包含 FACT/PREFERENCE/RULE/SKILL/ERROR 类型"""
        for mem_type in ['FACT', 'PREFERENCE', 'RULE', 'SKILL', 'ERROR']:
            assert mem_type in SEMANTIC_EXTRACTION_PROMPT

    def test_contains_experience_types(self):
        """experiences 应包含 EXPERIENCE/SKILL/ERROR 类型"""
        assert 'EXPERIENCE' in SEMANTIC_EXTRACTION_PROMPT

    def test_contains_priority_levels(self):
        """应包含 priority 层级（long_term/short_term）"""
        assert 'long_term' in SEMANTIC_EXTRACTION_PROMPT
        assert 'short_term' in SEMANTIC_EXTRACTION_PROMPT

    def test_contains_importance_field(self):
        """应包含 importance 字段（0.5-1.0 范围）"""
        assert 'importance' in SEMANTIC_EXTRACTION_PROMPT
        assert '0.5' in SEMANTIC_EXTRACTION_PROMPT or '0.5-1.0' in SEMANTIC_EXTRACTION_PROMPT

    def test_output_json_format(self):
        """输出格式必须是 JSON 结构"""
        assert '"memories"' in SEMANTIC_EXTRACTION_PROMPT
        assert '"experiences"' in SEMANTIC_EXTRACTION_PROMPT

    def test_requires_subject_first_content(self):
        """事实类记忆必须以主语开头（检索期会把动词开头判为操作指令）"""
        assert "主语开头" in SEMANTIC_EXTRACTION_PROMPT

    def test_filters_operator_instructions(self):
        """用户对助手的操作指令不是记忆，应被过滤"""
        assert "操作指令" in SEMANTIC_EXTRACTION_PROMPT

    def test_length_within_limit(self):
        """提示词长度应小于 4000 tokens（约 16000 字符）"""
        # rough estimate: 1 token ≈ 4 chars for English
        char_count = len(SEMANTIC_EXTRACTION_PROMPT)
        assert char_count < 16000, f"Prompt too long: {char_count} chars"


class TestEpisodeExtractionPrompt:
    """§4.2 情节记忆抽取提示词测试"""

    def test_contains_summary_field(self):
        """应包含 summary 字段"""
        assert 'summary' in EPISODE_EXTRACTION_PROMPT

    def test_contains_goal_field(self):
        """应包含 goal 字段"""
        assert 'goal' in EPISODE_EXTRACTION_PROMPT

    def test_contains_outcome_field(self):
        """应包含 outcome 字段"""
        assert 'outcome' in EPISODE_EXTRACTION_PROMPT

    def test_contains_outcome_variants(self):
        """outcome 应包含所有枚举值（包含 ongoing 特有值）"""
        assert 'completed' in EPISODE_EXTRACTION_PROMPT
        assert 'partial' in EPISODE_EXTRACTION_PROMPT
        assert 'failed' in EPISODE_EXTRACTION_PROMPT
        assert 'ongoing' in EPISODE_EXTRACTION_PROMPT  # nanobot 特有

    def test_contains_entities_field(self):
        """应包含 entities 字段"""
        assert 'entities' in EPISODE_EXTRACTION_PROMPT

    def test_contains_tools_used_field(self):
        """应包含 tools_used 字段"""
        assert 'tools_used' in EPISODE_EXTRACTION_PROMPT

    def test_output_json_format(self):
        """输出格式必须是 JSON 结构"""
        assert '"summary"' in EPISODE_EXTRACTION_PROMPT
        assert '"goal"' in EPISODE_EXTRACTION_PROMPT
        assert '"outcome"' in EPISODE_EXTRACTION_PROMPT
        assert '"entities"' in EPISODE_EXTRACTION_PROMPT
        assert '"tools_used"' in EPISODE_EXTRACTION_PROMPT

    def test_length_within_limit(self):
        """提示词长度应小于 4000 tokens（约 16000 字符）"""
        char_count = len(EPISODE_EXTRACTION_PROMPT)
        assert char_count < 16000, f"Prompt too long: {char_count} chars"


class TestPromptModule:
    """prompts.py 模块整体测试"""

    def test_all_prompts_are_strings(self):
        """所有提示词常量都是字符串类型"""
        assert isinstance(SEMANTIC_EXTRACTION_PROMPT, str)
        assert isinstance(EPISODE_EXTRACTION_PROMPT, str)

    def test_all_prompts_are_non_empty(self):
        """所有提示词常量都有内容"""
        assert len(SEMANTIC_EXTRACTION_PROMPT) > 100
        assert len(EPISODE_EXTRACTION_PROMPT) > 100

    def test_no_sensitive_data_in_prompts(self):
        """提示词不应包含敏感信息占位符（防止意外泄露）"""
        sensitive_patterns = ['{password}', '{token}', '{api_key}', '{secret}']
        all_prompts = SEMANTIC_EXTRACTION_PROMPT + EPISODE_EXTRACTION_PROMPT
        for pattern in sensitive_patterns:
            assert pattern not in all_prompts
