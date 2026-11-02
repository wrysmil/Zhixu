"""ProfileExtractor 单测（S3 Track 1：用户画像 + 经验）。

增量合并/冲突判定已收敛到 ``nanobot.memory.writer`` 的写入决策表（D08），
合并语义的测试见 ``test_writer.py`` 与 ``test_profile_merge_persistence.py``。

引用评分（``citation_scores``）已随 D11 删除：模型即便多吐这个字段也只被忽略。
"""
from __future__ import annotations

import pytest

from nanobot.memory.profile_extractor import ProfileExtractor


class _FakeProvider:
    def __init__(self, content: str = "", raise_exc: Exception | None = None) -> None:
        self._content = content
        self._raise = raise_exc

    async def chat_with_retry(self, *, model, messages, tools, **kwargs):
        if self._raise is not None:
            raise self._raise

        class _R:
            content = self._content

        return _R()


class _FakeRuntime:
    def __init__(self, content: str = "", raise_exc: Exception | None = None) -> None:
        self.provider = _FakeProvider(content, raise_exc)
        self.model = "test-model"
        self.generation = type("G", (), {"temperature": 0.0, "max_tokens": 1000, "reasoning_effort": None})()


@pytest.mark.asyncio
async def test_extract_profile_returns_items():
    runtime = _FakeRuntime(
        '{"memories":[{"content":"用户喜欢美咖啡","subject":"用户",'
        '"predicate":"喜欢","type":"PREFERENCE","importance":0.7,'
        '"priority":"long_term","tags":["coffee"],"is_update":false}]}'
    )
    ext = ProfileExtractor(runtime=runtime)
    result = await ext.extract(
        transcript=[{"role": "user", "content": "我喜欢美咖啡"}],
        episode_id="ep-1",
    )
    assert len(result.items) == 1
    assert result.items[0].content == "用户喜欢美咖啡"
    assert result.items[0].subject == "用户"
    assert result.items[0].type == "PREFERENCE"
    assert result.error is None


@pytest.mark.asyncio
async def test_extract_profile_ignores_model_citation_scores():
    """模型多吐 ``citation_scores`` 不算解析失败，但产出里不再有评分。"""
    runtime = _FakeRuntime(
        '{"memories":[{"content":"x","subject":"用户",'
        '"predicate":"喜欢","type":"PREFERENCE","importance":0.5,'
        '"priority":"long_term","tags":[],"is_update":false}],'
        '"citation_scores":[{"memory_id":"m1","useful":true}]}'
    )
    ext = ProfileExtractor(runtime=runtime)
    result = await ext.extract(
        transcript=[{"role": "user", "content": "x"}], episode_id="e"
    )
    assert len(result.items) == 1
    assert not hasattr(result, "scores")


@pytest.mark.asyncio
async def test_extract_profile_llm_failure_returns_empty():
    runtime = _FakeRuntime(raise_exc=RuntimeError("down"))
    ext = ProfileExtractor(runtime=runtime)
    result = await ext.extract(
        transcript=[{"role": "user", "content": "x"}], episode_id="e"
    )
    assert result.items == []
    assert result.error is not None


@pytest.mark.asyncio
async def test_extract_profile_parses_experiences_from_same_payload():
    """双轨同源：experiences 与 memories 来自同一次调用，不额外发 LLM。"""
    runtime = _FakeRuntime(
        '{"memories":[{"content":"用户使用 Windows","type":"FACT"}],'
        '"experiences":[{"content":"uv 可避免依赖漂移","type":"EXPERIENCE"}]}'
    )
    ext = ProfileExtractor(runtime=runtime)
    result = await ext.extract(transcript=[{"role": "user", "content": "x"}], episode_id="e")
    assert [i.content for i in result.items] == ["用户使用 Windows"]
    assert [e.content for e in result.experiences] == ["uv 可避免依赖漂移"]
    assert result.experiences[0].type == "EXPERIENCE"


@pytest.mark.asyncio
async def test_extract_profile_uses_supplied_prompt_messages():
    """传入 prompt_messages 时原样使用，不再自行拼 transcript。"""

    class _RecordingProvider:
        def __init__(self) -> None:
            self.messages: list[dict] | None = None

        async def chat_with_retry(self, *, model, messages, tools, **kwargs):
            self.messages = messages

            class _R:
                content = '{"memories":[]}'

            return _R()

    provider = _RecordingProvider()
    runtime = type("RT", (), {})()
    runtime.provider = provider
    runtime.model = "m"
    runtime.generation = type("G", (), {"temperature": 0.0, "max_tokens": 100, "reasoning_effort": None})()

    ext = ProfileExtractor(runtime=runtime)
    await ext.extract(
        transcript=[{"role": "user", "content": "不应出现"}],
        episode_id="e",
        prompt_messages=[{"role": "user", "content": "调用方拼好的 prompt"}],
    )

    assert provider.messages is not None
    assert provider.messages[0]["content"] == "调用方拼好的 prompt"
    assert "不应出现" not in provider.messages[0]["content"]
