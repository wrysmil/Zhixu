"""Embedding 后端：local / remote / none。

契约见 `.ai-runtime-artifacts/specs/2026-11-09-knowledge-center-spec.md` §10.1：
- `local`：sentence-transformers 惰性加载，依赖缺失/加载失败映射为 `EmbeddingUnavailable`；
- `remote`：OpenAI 兼容 `POST {base_url}/embeddings`，按 `index` 排序 + 客户端 L2 归一化；
- `none`：不产出向量，调用方降级为关键词单路。
"""

from __future__ import annotations

import importlib
import math
import os
import re
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol, cast

import httpx


class EmbeddingUnavailable(RuntimeError):  # noqa: N818 - 名称由 spec §10.1 契约固定
    """后端不可用：未配置 key、依赖缺失、4xx 拒绝。调用方降级为关键词检索。"""


class EmbeddingError(RuntimeError):
    """请求/响应失败：5xx 与超时重试耗尽、数量或维度不匹配、编码失败。"""


class Embedder(Protocol):
    @property
    def identity(self) -> tuple[str, str, int]: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class NoneEmbedder:
    """不产出向量：任何调用都抛 `EmbeddingUnavailable`，调用方据此走关键词单路。"""

    identity: tuple[str, str, int] = ("none", "", 0)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingUnavailable("embedding backend is none")

    def embed_query(self, text: str) -> list[float]:
        raise EmbeddingUnavailable("embedding backend is none")


def _redact(text: str, secret: str | None = None) -> str:
    """抹掉错误信息里可能出现的凭据（Bearer token / sk- key / 原始 key 字面量）。"""
    redacted = re.sub(r"(?i)(bearer\s+)\S+", r"\1***", text)
    redacted = re.sub(r"sk-[A-Za-z0-9_\-]{4,}", "sk-***", redacted)
    if secret:
        redacted = redacted.replace(secret, "***")
    return redacted


def _validated_vector(values: list[float], *, context: str = "embedding vector") -> list[float]:
    """拒绝空 / 非有限 / 零范数向量，其余做 L2 归一化（幂等）。"""
    if not values:
        raise EmbeddingError(f"{context}: empty vector")
    if not all(math.isfinite(value) for value in values):
        raise EmbeddingError(f"{context}: non-finite value")
    norm = math.hypot(*values)
    if not math.isfinite(norm) or norm == 0.0:
        raise EmbeddingError(f"{context}: zero norm")
    return [value / norm for value in values]


class LocalEmbedder:
    def __init__(
        self,
        *,
        backend: str,
        model: str,
        dimensions: int,
        batch_size: int,
        device: str,
        local_model_dir: str,
        download_source: str,
        model_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._backend = backend
        self._model_name = model
        self._dimensions_hint = dimensions
        self._batch_size = max(1, batch_size)
        self._device = device
        self._local_model_dir = local_model_dir
        self._download_source = download_source
        self._model_factory = model_factory
        self._lock = threading.Lock()
        self._model: Any = None
        self._identity: tuple[str, str, int] | None = None

    @staticmethod
    def _apply_mirror_endpoint() -> None:
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    def _load_huggingface(self) -> Any:
        # spec §10.1：模型标识用 model，local_model_dir 只是缓存目录
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(
            self._model_name,
            device=self._device,
            cache_folder=self._local_model_dir or None,
        )

    def _load_modelscope(self) -> Any:
        module = importlib.import_module("modelscope")
        snapshot_download = cast(Callable[..., str], getattr(module, "snapshot_download"))
        path = snapshot_download(self._model_name, cache_dir=self._local_model_dir or None)
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(path, device=self._device)

    def _build_model(self) -> Any:
        """按 `download_source` 加载模型；`auto` 有界（最多两次），不循环。"""
        if self._model_factory is not None:
            return self._model_factory()
        if self._download_source == "modelscope":
            return self._load_modelscope()
        if self._download_source == "auto":
            try:
                return self._load_huggingface()
            except Exception:  # noqa: BLE001 - auto：默认 HF 失败后走镜像重试一次
                self._apply_mirror_endpoint()
                return self._load_huggingface()
        if self._download_source == "hf-mirror":
            self._apply_mirror_endpoint()
        return self._load_huggingface()

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:  # 并发 ingest 不应重复加载/下载模型
            if self._model is not None:
                return self._model
            try:
                model = self._build_model()
                raw_dimension = model.get_sentence_embedding_dimension()
                dimension = None if raw_dimension is None else int(raw_dimension)
            except EmbeddingUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 - 依赖缺失/下载失败统一降级为不可用
                raise EmbeddingUnavailable(_redact(str(exc))) from exc
            if dimension is None:
                raise EmbeddingUnavailable("local model did not report an embedding dimension")
            if self._dimensions_hint and self._dimensions_hint != dimension:
                raise EmbeddingError(f"dimension mismatch: {self._dimensions_hint} != {dimension}")
            self._model = model
            self._identity = ("local", self._model_name, self._dimensions_hint or dimension)
            return model

    @property
    def identity(self) -> tuple[str, str, int]:
        # 有显式 dimensions 时不触发模型加载：建 KB 不应下载/加载几百 MB 模型
        if self._identity is None:
            if self._dimensions_hint > 0:
                self._identity = ("local", self._model_name, self._dimensions_hint)
            else:
                self._ensure_model()
        assert self._identity is not None
        return self._identity

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        try:
            vectors = model.encode(
                list(texts),
                batch_size=self._batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
            rows = [[float(value) for value in row] for row in vectors]
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(_redact(str(exc))) from exc
        if len(rows) != len(texts):
            raise EmbeddingError(
                f"embedding count mismatch: expected {len(texts)}, got {len(rows)}"
            )
        expected = self.identity[2]
        if any(len(row) != expected for row in rows):
            raise EmbeddingError(f"dimension mismatch: expected {expected} per vector")
        return [_validated_vector(row, context="local embedding row") for row in rows]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class RemoteEmbedder:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        dimensions: int,
        api_key: str,
        api_key_env: str,
        batch_size: int,
        timeout_seconds: float,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        retries: int = 3,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimensions = dimensions
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._batch_size = max(1, batch_size)
        self._timeout = timeout_seconds
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._sleep = sleep
        self._retries = retries
        self._active_key: str | None = None

    def _redact(self, text: str) -> str:
        """错误信息统一脱敏：Bearer/sk- 前缀以及当前解析到的原始 key 字面量。"""
        return _redact(text, self._active_key)

    def _resolve_key(self) -> str:
        if self._api_key_env:
            value = os.environ.get(self._api_key_env, "")
            if value:
                return value
        if self._api_key:
            return self._api_key
        raise EmbeddingUnavailable("remote embedding key is not configured")

    @property
    def identity(self) -> tuple[str, str, int]:
        if not self._base_url:
            raise EmbeddingUnavailable("base_url is empty")
        return ("remote", self._model, self._dimensions)

    def _parse(self, response: httpx.Response, expected_count: int) -> list[list[float]]:
        try:
            payload = cast(dict[str, Any], response.json())
            raw_data = payload["data"]
            if not isinstance(raw_data, list):
                raise TypeError("data must be a list")
            data = cast(list[Any], raw_data)
            indexed: dict[int, list[float]] = {}
            for raw_item in data:
                item = cast(dict[str, Any], raw_item)
                index = int(item["index"])
                if index in indexed:
                    raise ValueError(f"duplicate index {index}")
                embedding = cast(list[Any], item["embedding"])
                indexed[index] = [float(value) for value in embedding]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(self._redact(f"invalid embedding response: {exc}")) from exc
        # 只接受恰好覆盖 0..n-1 的索引集合（顺序任意），缺项/重复/越界都是协议错误
        if set(indexed) != set(range(expected_count)):
            raise EmbeddingError(
                f"embedding index mismatch: expected 0..{expected_count - 1}, "
                f"got {sorted(indexed)}"
            )
        vectors = [
            _validated_vector(indexed[index], context="remote embedding row")
            for index in range(expected_count)
        ]
        if self._dimensions <= 0 and vectors:
            self._dimensions = len(vectors[0])  # 自适应维度：KB 未记录时采用首个响应长度
        if any(len(vector) != self._dimensions for vector in vectors):
            raise EmbeddingError(f"dimension mismatch: expected {self._dimensions} per vector")
        return vectors

    def _request(self, texts: list[str]) -> list[list[float]]:
        key = self._resolve_key()
        self._active_key = key
        headers = {"Authorization": f"Bearer {key}"}
        payload = {"model": self._model, "input": texts, "encoding_format": "float"}
        url = f"{self._base_url}/embeddings"
        last: EmbeddingError | None = None
        for attempt in range(self._retries + 1):
            try:
                response = self._client.post(
                    url, headers=headers, json=payload, timeout=self._timeout
                )
            except (httpx.UnsupportedProtocol, httpx.InvalidURL) as exc:
                # 协议/URL 错误是确定性的：重试与退避都没有意义
                raise EmbeddingUnavailable(
                    self._redact(f"invalid embedding endpoint: {exc}")
                ) from exc
            except httpx.HTTPError as exc:
                last = EmbeddingError(self._redact(f"remote embedding request failed: {exc}"))
            else:
                if response.status_code == 429 or response.status_code >= 500:
                    last = EmbeddingError(f"remote embedding failed: HTTP {response.status_code}")
                elif response.status_code >= 400:
                    # 其他 4xx 是确定性拒绝：不重试，直接判定后端不可用
                    raise EmbeddingUnavailable(
                        f"remote embedding rejected: HTTP {response.status_code}"
                    )
                else:
                    return self._parse(response, len(texts))
            if attempt >= self._retries:
                break
            self._sleep(2**attempt)
        raise EmbeddingError(str(last) if last else "remote embedding failed")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), self._batch_size):
            vectors.extend(self._request(texts[offset : offset + self._batch_size]))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._request([text])[0]


def build_embedder(
    *,
    backend: str,
    model: str,
    dimensions: int,
    device: str,
    download_source: str,
    local_model_dir: str,
    base_url: str,
    api_key: str,
    api_key_env: str,
    batch_size: int,
    timeout_seconds: float,
    local_available: bool | None = None,
) -> Embedder:
    """把配置里的 `backend`（含 `auto`）解析成 `local | remote | none` 的实例。

    `auto` 只存在于配置层，解析结果（连同 model/dimensions）需要落库为 KB 指纹。
    """

    def remote_available() -> bool:
        if not base_url:
            return False
        if api_key_env and os.environ.get(api_key_env):
            return True
        return bool(api_key)

    if local_available is None:
        import importlib.util

        local_available = (
            importlib.util.find_spec("sentence_transformers") is not None
            and importlib.util.find_spec("chromadb") is not None
        )

    resolved = backend
    if backend == "auto":
        resolved = "remote" if remote_available() else ("local" if local_available else "none")
    if resolved == "remote":
        return RemoteEmbedder(
            base_url=base_url,
            model=model,
            dimensions=dimensions,
            api_key=api_key,
            api_key_env=api_key_env,
            batch_size=batch_size,
            timeout_seconds=timeout_seconds,
        )
    if resolved == "local":
        if not local_available:
            raise EmbeddingUnavailable("local embedding dependencies missing")
        return LocalEmbedder(
            backend="local",
            model=model,
            dimensions=dimensions,
            batch_size=batch_size,
            device=device,
            local_model_dir=local_model_dir,
            download_source=download_source,
        )
    return NoneEmbedder()
