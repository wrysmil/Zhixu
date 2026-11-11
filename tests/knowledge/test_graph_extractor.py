from __future__ import annotations

import httpx
import pytest

from nanobot.knowledge.graph.extractor import (
    GraphExtractionError,
    OpenAICompatGraphExtractor,
    clean_model_text,
    parse_extraction,
    parse_query_entities,
)


class _FakeResponse:
    def __init__(self, content: str, *, finish: str = "stop", status: int = 200) -> None:
        self.status_code = status
        self._payload = {
            "choices": [
                {"message": {"content": content}, "finish_reason": finish}
            ]
        }
        self._request = httpx.Request("POST", "http://x/v1/chat/completions")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err",
                request=self._request,
                response=httpx.Response(self.status_code, request=self._request),
            )

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[str] = []
        self.messages: list[list[dict[str, str]]] = []
        self.requests: list[dict] = []

    def post(self, url, json, headers, timeout):
        self.calls.append(json["messages"][0]["content"])
        self.messages.append(json["messages"])
        self.requests.append(json)
        return self.responses.pop(0)


def test_empty_entities_is_declined():
    result = parse_extraction('{"entities": [], "relations": []}')
    assert result.declined is True
    assert result.entities == ()


def test_missing_entities_key_is_declined():
    assert parse_extraction('{"foo": 1}').declined is True


def test_invalid_relation_endpoint_is_dropped():
    payload = (
        '{"entities":[{"name":"A","type":"concept"}],'
        '"relations":[{"source":"A","target":"B","relation":"依赖"}]}'
    )
    result = parse_extraction(payload)
    assert result.relations == ()


def test_pronoun_entity_is_dropped_with_its_relations():
    payload = (
        '{"entities":[{"name":"它","type":"concept"},'
        '{"name":"A","type":"concept"}],'
        '"relations":[{"source":"它","target":"A","relation":"指向"}]}'
    )
    result = parse_extraction(payload)
    assert [entity[0] for entity in result.entities] == ["A"]
    assert result.relations == ()


def test_clean_model_text_strips_think_and_fence():
    raw = '<think>内部推理</think>\n```json\n{"entities": []}\n```'
    assert clean_model_text(raw) == '{"entities": []}'


def test_parse_uses_json_repair():
    result = parse_extraction("{'entities': [{'name': 'A', 'type': 'concept'}]}")
    assert result.entities[0][0] == "A"


def test_entity_and_relation_caps():
    entities = ",".join(f'{{"name":"E{i}"}}' for i in range(25))
    relations = ",".join(
        f'{{"source":"E0","target":"E1","relation":"r{i}"}}' for i in range(35)
    )
    result = parse_extraction(f'{{"entities":[{entities}],"relations":[{relations}]}}')
    assert len(result.entities) == 20
    assert len(result.relations) == 30


def test_length_finish_reason_retries_with_half_context(monkeypatch):
    monkeypatch.setattr("nanobot.knowledge.graph.extractor.time.sleep", lambda _: None)
    client = _Client(
        [
            _FakeResponse("{}", finish="length"),
            _FakeResponse('{"entities": []}'),
        ]
    )
    extractor = OpenAICompatGraphExtractor(
        base_url="http://x/v1", model="m", max_context_chars=2000, client=client
    )
    result = extractor.extract("h", "x" * 2000)
    assert result.declined is True
    assert len(client.calls) == 2
    assert len(client.calls[1]) < len(client.calls[0])


def test_http_4xx_is_not_retried():
    client = _Client([_FakeResponse("{}", status=400)])
    extractor = OpenAICompatGraphExtractor(
        base_url="http://x/v1", model="m", client=client
    )
    with pytest.raises(GraphExtractionError) as exc:
        extractor.extract("h", "x")
    assert exc.value.code == "http_4xx"
    assert len(client.calls) == 1


def test_429_is_retried_once(monkeypatch):
    monkeypatch.setattr("nanobot.knowledge.graph.extractor.time.sleep", lambda _: None)
    client = _Client([
        _FakeResponse("{}", status=429),
        _FakeResponse('{"entities": []}'),
    ])
    extractor = OpenAICompatGraphExtractor(
        base_url="http://x/v1", model="m", client=client
    )
    assert extractor.extract("h", "x").declined is True
    assert len(client.calls) == 2


def test_missing_config_raises_not_configured():
    extractor = OpenAICompatGraphExtractor(
        base_url="", model="", client=_Client([])
    )
    with pytest.raises(GraphExtractionError) as exc:
        extractor.extract("h", "x")
    assert exc.value.code == "not_configured"


def test_parse_query_entities_accepts_weknora_entity_array():
    text = '[{"entity":"Romeo and Juliet"},{"entity":"William Shakespeare"}]'
    assert parse_query_entities(text) == ("Romeo and Juliet", "William Shakespeare")


def test_parse_query_entities_accepts_entities_key_and_strings():
    assert parse_query_entities('{"entities":["A","B"]}') == ("A", "B")


def test_parse_query_entities_drops_pronouns_and_duplicates():
    text = '[{"entity":"它"},{"entity":"A"},{"name":"A"}]'
    assert parse_query_entities(text) == ("A",)


def test_parse_query_entities_rejects_prose():
    with pytest.raises(GraphExtractionError):
        parse_query_entities("抱歉，这段内容没有可抽取的实体。")


def test_extract_query_uses_dedicated_prompt_and_parses_entities():
    client = _Client([_FakeResponse('[{"entity":"值班二号"}]')])
    extractor = OpenAICompatGraphExtractor(
        base_url="http://x/v1", model="m", client=client
    )
    assert extractor.extract_query("谁负责执行") == ("值班二号",)
    system = client.messages[0][0]["content"]
    user = client.messages[0][1]["content"]
    assert "Based on the user's question" in system
    assert '"entity": "Romeo and Juliet"' in system
    assert "# Question" in user
    assert "Q: 谁负责执行" in user


def test_custom_prompts_are_rendered():
    client = _Client(
        [
            _FakeResponse('{"entities": []}'),
            _FakeResponse('[{"entity":"A"}]'),
        ]
    )
    extractor = OpenAICompatGraphExtractor(
        base_url="http://x/v1",
        model="m",
        client=client,
        extraction_prompt="H=<<heading>> C=<<content>>",
        query_entity_system_prompt="SYS",
        query_entity_user_prompt="Q=<<query>>",
    )
    extractor.extract("h", "body")
    assert client.messages[0][0]["content"] == "H=h C=body"
    assert extractor.extract_query("q") == ("A",)
    assert client.messages[1][0]["content"] == "SYS"
    assert client.messages[1][1]["content"] == "Q=q"


def test_thinking_defaults_to_disabled_and_can_be_auto():
    client = _Client([_FakeResponse('{"entities": []}'), _FakeResponse('{"entities": []}')])
    extractor = OpenAICompatGraphExtractor(
        base_url="http://x/v1", model="m", client=client
    )
    extractor.extract("h", "x")
    assert client.requests[0]["thinking"] == {"type": "disabled"}

    auto = OpenAICompatGraphExtractor(
        base_url="http://x/v1", model="m", client=client, thinking="auto"
    )
    auto.extract("h", "x")
    assert "thinking" not in client.requests[1]


def test_build_graph_extractor_passes_prompts():
    from types import SimpleNamespace

    from nanobot.knowledge.graph.extractor import build_graph_extractor

    extractor = build_graph_extractor(
        SimpleNamespace(enabled=True, model="m", base_url="http://x/v1"),
        SimpleNamespace(
            extraction="E <<content>>",
            query_entities="S",
            query_user="Q <<query>>",
        ),
    )
    assert extractor is not None
    assert extractor._extraction_prompt == "E <<content>>"
    assert extractor._query_entity_system_prompt == "S"
    assert extractor._query_entity_user_prompt == "Q <<query>>"
