"""入库期问题生成（doc2query）单元测试：解析、id 反解、HTTP 协议与构造。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from nanobot.knowledge.questions import (
    OpenAICompatQuestionGenerator,
    QuestionGenerationError,
    build_question_generator,
    parent_chunk_id,
    parse_generated_questions,
    question_count_from,
    question_id,
)


def test_parse_strips_think_numbering_and_short_lines() -> None:
    raw = (
        "<think>先想一想</think>\n"
        "1. 部署失败后怎么回滚？\n"
        "2) 回滚要多久？\n"
        "短\n"
        "- 可以只回滚一个服务吗？\n"
        "3. 多出来的问题不该保留\n"
    )
    assert parse_generated_questions(raw, 3) == [
        "部署失败后怎么回滚？",
        "回滚要多久？",
        "可以只回滚一个服务吗？",
    ]


def test_parse_respects_limit_and_empty_input() -> None:
    assert parse_generated_questions("", 3) == []
    assert parse_generated_questions("一\n二\n三\n四", 2) == []
    text = "\n".join(f"第{index}个足够长的问题" for index in range(5))
    assert len(parse_generated_questions(text, 2)) == 2


def test_question_id_round_trips_to_parent() -> None:
    chunk = "chk_0123456789abcdef"
    assert question_id(chunk, 3) == f"{chunk}-q3"
    assert parent_chunk_id(f"{chunk}-q3") == chunk
    assert parent_chunk_id(chunk) is None
    assert parent_chunk_id("chk_other-q10") == "chk_other"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [ (99, 10), (0, 1), (-3, 1), (5, 5), ("bad", 3) ],
)
def test_question_count_clamped(raw: Any, expected: int) -> None:
    assert question_count_from(SimpleNamespace(question_count=raw)) == expected


def test_build_generator_requires_enabled_and_connection() -> None:
    disabled = SimpleNamespace(enabled=False, base_url="https://x/v1", model="m")
    missing_model = SimpleNamespace(enabled=True, base_url="https://x/v1", model="")
    missing_url = SimpleNamespace(enabled=True, base_url="", model="m")
    enabled = SimpleNamespace(
        enabled=True,
        base_url="https://x/v1",
        model="m",
        api_key="",
        api_key_env="",
        timeout_seconds=10.0,
        max_tokens=512,
        temperature=0.2,
        max_context_chars=800,
    )

    assert build_question_generator(disabled) is None
    assert build_question_generator(missing_model) is None
    assert build_question_generator(missing_url) is None
    assert isinstance(build_question_generator(enabled), OpenAICompatQuestionGenerator)


def test_openai_generator_posts_prompt_and_parses_reply() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "1. 怎么回滚？\n2. 回滚要多久？\n3. 可以只回滚一个服务吗？"
                        }
                    }
                ]
            },
        )

    generator = OpenAICompatQuestionGenerator(
        base_url="https://example.test/v1",
        model="q-model",
        api_key="sk-secret-value",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    questions = generator.generate(
        "部署失败后先冻结流量",
        prev_content="发布流程",
        next_content="回滚预案",
        doc_name="运维手册",
        question_count=3,
    )

    assert questions == [
        "怎么回滚？",
        "回滚要多久？",
        "可以只回滚一个服务吗？",
    ]
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer sk-secret-value"
    payload = captured["payload"]
    assert payload["model"] == "q-model"
    assert payload["temperature"] == 0.7
    assert payload["max_tokens"] == 2048
    prompt = payload["messages"][0]["content"]
    for fragment in ("部署失败后先冻结流量", "发布流程", "回滚预案", "运维手册"):
        assert fragment in prompt


def test_openai_generator_accepts_content_parts_and_reports_http_errors() -> None:
    def parts_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "回滚步骤有哪些？"},
                                {"type": "text", "text": "\n回滚失败怎么办？"},
                            ]
                        }
                    }
                ]
            },
        )

    generator = OpenAICompatQuestionGenerator(
        base_url="https://example.test/v1",
        model="q-model",
        client=httpx.Client(transport=httpx.MockTransport(parts_handler)),
    )
    assert generator.generate("正文") == ["回滚步骤有哪些？", "回滚失败怎么办？"]

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "down"})

    failing = OpenAICompatQuestionGenerator(
        base_url="https://example.test/v1",
        model="q-model",
        api_key="sk-secret-value",
        client=httpx.Client(transport=httpx.MockTransport(failing_handler)),
    )
    with pytest.raises(QuestionGenerationError) as excinfo:
        failing.generate("正文")
    assert "sk-secret-value" not in str(excinfo.value)
