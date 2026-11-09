import json

import httpx
import pytest

from nanobot.knowledge.embedding import (
    EmbeddingError,
    EmbeddingUnavailable,
    LocalEmbedder,
    RemoteEmbedder,
    build_embedder,
)


class FakeModel:
    def __init__(self, dims=3):
        self.dims = dims
        self.calls = []

    def get_sentence_embedding_dimension(self):
        return self.dims

    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        return [[1.0, 0.0, 0.0] for _ in texts]


def test_local_embedder_uses_normalized_batches():
    model = FakeModel()
    embedder = LocalEmbedder(
        backend="local",
        model="m",
        dimensions=3,
        batch_size=2,
        device="cpu",
        local_model_dir="",
        download_source="auto",
        model_factory=lambda: model,
    )
    vectors = embedder.embed_documents(["a", "b"])
    assert vectors == [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    assert model.calls[0][1]["normalize_embeddings"] is True
    assert model.calls[0][1]["batch_size"] == 2


def test_remote_embedder_sorts_and_normalizes():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read()
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 2.0]},
                    {"index": 0, "embedding": [3.0, 4.0]},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    embedder = RemoteEmbedder(
        base_url="https://example.test/v1",
        model="m",
        dimensions=2,
        api_key="secret",
        api_key_env="",
        batch_size=8,
        timeout_seconds=5,
        client=client,
        sleep=lambda _: None,
    )
    vectors = embedder.embed_documents(["a", "b"])
    assert vectors[0] == pytest.approx([0.6, 0.8])
    assert vectors[1] == pytest.approx([0.0, 1.0])
    assert seen["url"] == "https://example.test/v1/embeddings"
    assert seen["auth"] == "Bearer secret"


def test_remote_retries_then_succeeds():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(500)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    embedder = RemoteEmbedder(
        base_url="https://example.test/v1",
        model="m",
        dimensions=2,
        api_key="k",
        api_key_env="",
        batch_size=8,
        timeout_seconds=5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    assert embedder.embed_query("q") == pytest.approx([1.0, 0.0])
    assert attempts["n"] == 2


def test_remote_4xx_is_not_retried():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(400, json={"error": "bad"})

    embedder = RemoteEmbedder(
        base_url="https://example.test/v1",
        model="m",
        dimensions=2,
        api_key="k",
        api_key_env="",
        batch_size=8,
        timeout_seconds=5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed_query("q")
    assert attempts["n"] == 1


def test_remote_dimension_mismatch_raises():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}]})

    embedder = RemoteEmbedder(
        base_url="https://example.test/v1",
        model="m",
        dimensions=2,
        api_key="k",
        api_key_env="",
        batch_size=8,
        timeout_seconds=5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    with pytest.raises(EmbeddingError, match="dimension"):
        embedder.embed_query("q")
    # 维度是确定性错误：不应重试（重试会把 1s/2s/4s 退避浪费在必然失败上）
    assert attempts["n"] == 1


def test_missing_remote_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("MY_KEY", raising=False)
    embedder = RemoteEmbedder(
        base_url="https://example.test/v1",
        model="m",
        dimensions=2,
        api_key="",
        api_key_env="MY_KEY",
        batch_size=8,
        timeout_seconds=5,
    )
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed_query("q")


def test_auto_without_remote_key_falls_back_to_none(monkeypatch):
    monkeypatch.delenv("MY_KEY", raising=False)
    embedder = build_embedder(
        backend="auto",
        model="m",
        dimensions=0,
        device="cpu",
        download_source="auto",
        local_model_dir="",
        base_url="",
        api_key="",
        api_key_env="MY_KEY",
        batch_size=8,
        timeout_seconds=5,
        local_available=False,
    )
    assert embedder.identity[0] == "none"


def test_local_load_failure_is_typed_error():
    def factory():
        raise ImportError("No module named 'sentence_transformers'")

    embedder = LocalEmbedder(
        backend="local",
        model="m",
        dimensions=3,
        batch_size=2,
        device="cpu",
        local_model_dir="",
        download_source="auto",
        model_factory=factory,
    )
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed_query("q")


def test_remote_batches_preserve_order_count_and_timeout():
    bodies: list[list[str]] = []
    timeouts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.read())["input"])
        timeouts.append(request.extensions.get("timeout") or {})
        size = len(bodies[-1])
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [float(len(bodies)), float(index)]}
                    for index in range(size)
                ]
            },
        )

    embedder = RemoteEmbedder(
        base_url="https://example.test/v1/",
        model="m",
        dimensions=2,
        api_key="k",
        api_key_env="",
        batch_size=2,
        timeout_seconds=7,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    vectors = embedder.embed_documents(["a", "b", "c"])
    assert bodies == [["a", "b"], ["c"]]
    assert len(vectors) == 3
    assert all(len(vector) == 2 for vector in vectors)
    assert vectors[1] == pytest.approx([2**-0.5, 2**-0.5])
    assert vectors[2] == pytest.approx([1.0, 0.0])
    assert timeouts[0].get("read") == 7
    # 空输入不发请求，也不抛错
    assert embedder.embed_documents([]) == []
    assert len(bodies) == 2


def test_remote_error_message_redacts_key():
    def handler(request):
        raise httpx.ConnectError("boom while sending Bearer sk-live-123456")

    embedder = RemoteEmbedder(
        base_url="https://example.test/v1",
        model="m",
        dimensions=2,
        api_key="sk-live-123456",
        api_key_env="",
        batch_size=8,
        timeout_seconds=5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    with pytest.raises(EmbeddingError) as info:
        embedder.embed_query("q")
    message = str(info.value)
    assert "sk-live-123456" not in message
    assert "***" in message


# --- Task 11 复核修复：local 模型加载 / download_source ---


def _fake_st_module(monkeypatch, factory):
    """注册一个假的 `sentence_transformers` 模块，返回记录调用参数的桩。"""
    import sys
    import types

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return module


def _working_model(dims=3):
    class FakeST:
        def __init__(self, model, **kwargs):
            self.model = model
            self.kwargs = kwargs

        def get_sentence_embedding_dimension(self):
            return dims

        def encode(self, texts, **kwargs):
            return [[1.0, 0.0, 0.0] for _ in texts]

    return FakeST


def test_local_model_dir_is_passed_as_cache_folder(monkeypatch):
    recorded = {}

    class RecordingST:
        def __init__(self, model, **kwargs):
            recorded["model"] = model
            recorded["kwargs"] = kwargs

        def get_sentence_embedding_dimension(self):
            return 3

        def encode(self, texts, **kwargs):
            return [[1.0, 0.0, 0.0] for _ in texts]

    _fake_st_module(monkeypatch, RecordingST)
    embedder = LocalEmbedder(
        backend="local",
        model="BAAI/bge-small-zh",
        dimensions=3,
        batch_size=2,
        device="cpu",
        local_model_dir="/models/cache",
        download_source="huggingface",
    )
    assert embedder.embed_documents(["a"]) == [[1.0, 0.0, 0.0]]
    # 模型名作为模型标识；local_model_dir 只作为缓存目录
    assert recorded["model"] == "BAAI/bge-small-zh"
    assert recorded["kwargs"]["cache_folder"] == "/models/cache"
    assert recorded["kwargs"]["device"] == "cpu"


def test_local_model_dir_empty_passes_none_cache_folder(monkeypatch):
    recorded = {}

    class RecordingST:
        def __init__(self, model, **kwargs):
            recorded["model"] = model
            recorded["kwargs"] = kwargs

        def get_sentence_embedding_dimension(self):
            return 3

        def encode(self, texts, **kwargs):
            return [[1.0, 0.0, 0.0] for _ in texts]

    _fake_st_module(monkeypatch, RecordingST)
    embedder = LocalEmbedder(
        backend="local",
        model="m",
        dimensions=3,
        batch_size=2,
        device="cpu",
        local_model_dir="",
        download_source="huggingface",
    )
    embedder.embed_query("q")
    assert recorded["model"] == "m"
    assert recorded["kwargs"]["cache_folder"] is None


def test_modelscope_downloads_then_loads_local_path(monkeypatch):
    import sys
    import types

    calls = {}

    def snapshot_download(model, cache_dir=None):
        calls["model"] = model
        calls["cache_dir"] = cache_dir
        return "/modelscope/snapshot/bge"

    ms_module = types.ModuleType("modelscope")
    ms_module.snapshot_download = snapshot_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "modelscope", ms_module)

    class RecordingST:
        def __init__(self, model, **kwargs):
            calls["loaded"] = model
            calls["kwargs"] = kwargs

        def get_sentence_embedding_dimension(self):
            return 3

        def encode(self, texts, **kwargs):
            return [[1.0, 0.0, 0.0] for _ in texts]

    _fake_st_module(monkeypatch, RecordingST)
    embedder = LocalEmbedder(
        backend="local",
        model="BAAI/bge-small-zh",
        dimensions=3,
        batch_size=2,
        device="cpu",
        local_model_dir="/models/cache",
        download_source="modelscope",
    )
    assert embedder.embed_query("q") == pytest.approx([1.0, 0.0, 0.0])
    assert calls["model"] == "BAAI/bge-small-zh"
    assert calls["cache_dir"] == "/models/cache"
    assert calls["loaded"] == "/modelscope/snapshot/bge"
    assert "cache_folder" not in calls["kwargs"]


def test_modelscope_missing_package_is_unavailable(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "modelscope", None)
    embedder = LocalEmbedder(
        backend="local",
        model="m",
        dimensions=3,
        batch_size=2,
        device="cpu",
        local_model_dir="",
        download_source="modelscope",
    )
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed_query("q")


def test_auto_retries_once_with_hf_mirror(monkeypatch):
    import os

    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    attempts: list[str | None] = []

    class FlakyST:
        def __init__(self, model, **kwargs):
            attempts.append(os.environ.get("HF_ENDPOINT"))
            if len(attempts) == 1:
                raise OSError("connection reset")

        def get_sentence_embedding_dimension(self):
            return 3

        def encode(self, texts, **kwargs):
            return [[1.0, 0.0, 0.0] for _ in texts]

    _fake_st_module(monkeypatch, FlakyST)
    embedder = LocalEmbedder(
        backend="local",
        model="m",
        dimensions=3,
        batch_size=2,
        device="cpu",
        local_model_dir="",
        download_source="auto",
    )
    assert embedder.embed_query("q") == pytest.approx([1.0, 0.0, 0.0])
    assert len(attempts) == 2
    assert attempts[0] is None
    assert os.environ.get("HF_ENDPOINT") == "https://hf-mirror.com"


def test_auto_gives_up_after_two_failures(monkeypatch):
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    attempts = {"n": 0}

    class BrokenST:
        def __init__(self, model, **kwargs):
            attempts["n"] += 1
            raise OSError("still down")

    _fake_st_module(monkeypatch, BrokenST)
    embedder = LocalEmbedder(
        backend="local",
        model="m",
        dimensions=3,
        batch_size=2,
        device="cpu",
        local_model_dir="",
        download_source="auto",
    )
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed_query("q")
    assert attempts["n"] == 2


def test_local_missing_dimension_is_unavailable(monkeypatch):
    class NoDimST:
        def __init__(self, model, **kwargs):
            pass

        def get_sentence_embedding_dimension(self):
            return None

    _fake_st_module(monkeypatch, NoDimST)
    embedder = LocalEmbedder(
        backend="local",
        model="m",
        dimensions=0,
        batch_size=2,
        device="cpu",
        local_model_dir="",
        download_source="huggingface",
    )
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed_query("q")


# --- Task 11 复核修复：响应索引与向量有效性 ---


def _remote_with_embeddings(embeddings):
    def handler(request):
        payload = {
            "data": [
                {"index": index, "embedding": vector}
                for index, vector in enumerate(embeddings)
            ]
        }
        # json= 走 httpx 的严格编码器，会拒绝 NaN/Infinity；原始 content 才能覆盖这两种非法响应
        return httpx.Response(200, content=json.dumps(payload))

    return RemoteEmbedder(
        base_url="https://example.test/v1",
        model="m",
        dimensions=2,
        api_key="k",
        api_key_env="",
        batch_size=8,
        timeout_seconds=5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )


@pytest.mark.parametrize("indices", [[0, 0], [7, 9]])
def test_remote_rejects_duplicate_or_out_of_range_indices(indices):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [1.0, 0.0]} for index in indices
                ]
            },
        )

    embedder = RemoteEmbedder(
        base_url="https://example.test/v1",
        model="m",
        dimensions=2,
        api_key="k",
        api_key_env="",
        batch_size=8,
        timeout_seconds=5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    with pytest.raises(EmbeddingError):
        embedder.embed_documents(["a", "b"])


@pytest.mark.parametrize(
    "vector",
    [[float("nan"), 1.0], [float("inf"), 1.0], [0.0, 0.0], []],
)
def test_remote_rejects_invalid_vectors(vector):
    embedder = _remote_with_embeddings([vector])
    with pytest.raises(EmbeddingError):
        embedder.embed_query("q")


def test_local_rejects_invalid_vectors():
    class BadModel(FakeModel):
        def __init__(self, vector):
            super().__init__(dims=len(vector))
            self.vector = vector

        def encode(self, texts, **kwargs):
            return [list(self.vector) for _ in texts]

    for vector in ([float("nan"), 1.0], [0.0, 0.0], []):
        embedder = LocalEmbedder(
            backend="local",
            model="m",
            dimensions=0,
            batch_size=2,
            device="cpu",
            local_model_dir="",
            download_source="auto",
            model_factory=lambda vector=vector: BadModel(vector),
        )
        with pytest.raises(EmbeddingError):
            embedder.embed_query("q")


# --- Task 11 复核修复：重试分类 ---


def test_remote_unsupported_protocol_is_not_retried():
    attempts = {"n": 0}
    sleeps: list[float] = []

    def handler(request):
        attempts["n"] += 1
        raise httpx.UnsupportedProtocol("unsupported protocol")

    embedder = RemoteEmbedder(
        base_url="https://example.test/v1",
        model="m",
        dimensions=2,
        api_key="k",
        api_key_env="",
        batch_size=8,
        timeout_seconds=5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed_query("q")
    assert attempts["n"] == 1
    assert sleeps == []


def test_remote_5xx_exhausts_four_attempts_with_backoff():
    attempts = {"n": 0}
    sleeps: list[float] = []

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(500)

    embedder = RemoteEmbedder(
        base_url="https://example.test/v1",
        model="m",
        dimensions=2,
        api_key="k",
        api_key_env="",
        batch_size=8,
        timeout_seconds=5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )
    with pytest.raises(EmbeddingError):
        embedder.embed_query("q")
    assert attempts["n"] == 4
    assert sleeps == [1, 2, 4]


def test_remote_400_is_unavailable_without_retry():
    attempts = {"n": 0}
    sleeps: list[float] = []

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(400, json={"error": "bad"})

    embedder = RemoteEmbedder(
        base_url="https://example.test/v1",
        model="m",
        dimensions=2,
        api_key="k",
        api_key_env="",
        batch_size=8,
        timeout_seconds=5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )
    with pytest.raises(EmbeddingUnavailable):
        embedder.embed_query("q")
    assert attempts["n"] == 1
    assert sleeps == []


def test_remote_error_redacts_raw_key_literal():
    raw_key = "tok-abcdef-123456"

    def handler(request):
        raise httpx.ConnectError(f"boom with key {raw_key} in message")

    embedder = RemoteEmbedder(
        base_url="https://example.test/v1",
        model="m",
        dimensions=2,
        api_key=raw_key,
        api_key_env="",
        batch_size=8,
        timeout_seconds=5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    with pytest.raises(EmbeddingError) as info:
        embedder.embed_query("q")
    message = str(info.value)
    assert raw_key not in message
    assert "***" in message


def test_remote_invalid_response_redacts_raw_key():
    raw_key = "tok-abcdef-123456"

    def handler(request):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [raw_key]}]})

    embedder = RemoteEmbedder(
        base_url="https://example.test/v1",
        model="m",
        dimensions=2,
        api_key=raw_key,
        api_key_env="",
        batch_size=8,
        timeout_seconds=5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    with pytest.raises(EmbeddingError) as info:
        embedder.embed_query("q")
    assert raw_key not in str(info.value)


# --- Task 11 复核修复：显式 local 回退语义 ---


def test_build_embedder_explicit_local_without_deps_raises():
    with pytest.raises(EmbeddingUnavailable):
        build_embedder(
            backend="local",
            model="m",
            dimensions=0,
            device="cpu",
            download_source="auto",
            local_model_dir="",
            base_url="",
            api_key="",
            api_key_env="",
            batch_size=8,
            timeout_seconds=5,
            local_available=False,
        )


def test_build_embedder_auto_still_falls_back_to_none(monkeypatch):
    monkeypatch.delenv("MY_KEY", raising=False)
    embedder = build_embedder(
        backend="auto",
        model="m",
        dimensions=0,
        device="cpu",
        download_source="auto",
        local_model_dir="",
        base_url="",
        api_key="",
        api_key_env="MY_KEY",
        batch_size=8,
        timeout_seconds=5,
        local_available=False,
    )
    from nanobot.knowledge.embedding import NoneEmbedder

    assert isinstance(embedder, NoneEmbedder)
