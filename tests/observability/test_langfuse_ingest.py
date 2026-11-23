import pytest

from nanobot.observability import langfuse_ingest as ingest


class FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "data": [
                {"id": "queue-1", "name": "zhixu-review"},
                {"id": "queue-2", "name": "other"},
            ]
        }


class FakeClient:
    calls: list[dict[str, object]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def post(self, url: str, json: dict[str, object], auth: tuple[str, str]) -> FakeResponse:
        type(self).calls.append({"url": url, "json": json, "auth": auth})
        return FakeResponse()

    async def get(self, url: str, params: dict[str, object], auth: tuple[str, str]) -> FakeResponse:
        type(self).calls.append({"url": url, "params": params, "auth": auth})
        return FakeResponse()


@pytest.mark.asyncio
async def test_create_feedback_score(monkeypatch) -> None:
    FakeClient.calls = []
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://lf")
    monkeypatch.setattr(ingest.httpx, "AsyncClient", FakeClient)

    ok = await ingest.create_feedback_score(trace_id="d" * 32, helpful=True)

    assert ok is True
    assert FakeClient.calls == [
        {
            "url": "http://lf/api/public/scores",
            "json": {
                "id": "zhixu-feedback-" + "d" * 32,
                "traceId": "d" * 32,
                "name": "user_feedback",
                "value": 1,
                "dataType": "BOOLEAN",
            },
            "auth": ("pk", "sk"),
        }
    ]


@pytest.mark.asyncio
async def test_add_trace_to_queue(monkeypatch) -> None:
    FakeClient.calls = []
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://lf")
    monkeypatch.setattr(ingest.httpx, "AsyncClient", FakeClient)

    ok = await ingest.add_trace_to_queue(queue_id="queue-1", trace_id="e" * 32)

    assert ok is True
    assert FakeClient.calls[0]["url"] == "http://lf/api/public/annotation-queues/queue-1/items"
    assert FakeClient.calls[0]["json"] == {
        "objectId": "e" * 32,
        "objectType": "TRACE",
        "status": "PENDING",
    }


@pytest.mark.asyncio
async def test_create_dataset_item(monkeypatch) -> None:
    FakeClient.calls = []
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://lf")
    monkeypatch.setattr(ingest.httpx, "AsyncClient", FakeClient)

    ok = await ingest.create_dataset_item(
        dataset_name="zhixu-evals",
        trace_id="a" * 32,
        input_text="问题",
        expected_output="答案",
    )

    assert ok is True
    assert FakeClient.calls[0]["url"] == "http://lf/api/public/dataset-items"
    assert FakeClient.calls[0]["json"] == {
        "datasetName": "zhixu-evals",
        "input": "问题",
        "expectedOutput": "答案",
        "sourceTraceId": "a" * 32,
        "metadata": {},
    }


@pytest.mark.asyncio
async def test_resolve_queue_id(monkeypatch) -> None:
    FakeClient.calls = []
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://lf")
    monkeypatch.setattr(ingest.httpx, "AsyncClient", FakeClient)

    assert await ingest.resolve_queue_id("zhixu-review") == "queue-1"
    assert await ingest.resolve_queue_id("missing") is None


@pytest.mark.asyncio
async def test_create_feedback_score_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    assert await ingest.create_feedback_score(trace_id="d" * 32, helpful=False) is False
