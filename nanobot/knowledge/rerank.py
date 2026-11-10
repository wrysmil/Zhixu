"""精排：cross-encoder 协议、阈值降级与复合分（WeKnora §7）。

口径来自 WeKnora《03 · 检索原理》§7：

- **接口**：`Rerank(query, documents) -> [(index, score)]`；默认实现走 cohere 兼容协议
  （`POST {base_url}/rerank`），BGE / Jina / 多数网关都是这个形状。
- **候选截断**：进模型前只取前 `max_candidates` 条（默认 200）。
- **passage 不是正文**：标题 + 面包屑 + 清洗正文，从尾部截断；空 passage 跳过不参与打分。
- **分批与并发**：按 `batch_size` 条数切批，且单批字符总量不超过 `max_request_chars`；
  最多 `max_concurrency` 批并发；合并后整体重排。
- **阈值降级**（三档）：`score >= threshold` → ok；`threshold > 0.3` 时用 `max(0.3, threshold×0.7)`
  → threshold_degraded；仍全拒时保留最高分 1 条（需 ≥ `fallback_min_score`，显式作用域无下限）
  → fallback_top1；否则 all_below_threshold（返回空）。
- **复合分**：`0.6×model_score + 0.3×base_score + 0.1×source_weight`，钳到 [0,1]。
- **失败语义**：无模型 → no_model；模型不可用（加载失败/配置缺失）→ model_unavailable；
  调用失败 → model_error；两者都**不报错**，用召回顺序继续。
- **score 量纲**：NIM 之类返回 logit，需要用 logistic 还原成概率再比阈值。
"""

from __future__ import annotations

import math
import os
import re
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx

#: 精排结果口径（与 WeKnora outcome 对齐）。
OUTCOME_OK = "ok"
OUTCOME_THRESHOLD_DEGRADED = "threshold_degraded"
OUTCOME_FALLBACK_TOP1 = "fallback_top1"
OUTCOME_ALL_BELOW_THRESHOLD = "all_below_threshold"
OUTCOME_NO_MODEL = "no_model"
OUTCOME_MODEL_UNAVAILABLE = "model_unavailable"
OUTCOME_MODEL_ERROR = "model_error"
OUTCOME_NO_CANDIDATES = "no_candidates"

_DEGRADED_THRESHOLD = 0.3


@dataclass(frozen=True, slots=True)
class RankResult:
    """一条候选的精排分；`index` 指回传入 `documents` 的下标。"""

    index: int
    relevance_score: float


@dataclass(frozen=True, slots=True)
class RerankSettings:
    """精排参数（config `knowledge.rerank`）。"""

    enabled: bool = False
    max_candidates: int = 200
    max_passage_chars: int = 1200
    max_request_chars: int = 8000
    threshold: float = 0.3
    fallback_min_score: float = 0.15
    model_weight: float = 0.6
    base_weight: float = 0.3
    source_weight: float = 0.1
    score_transform: str = "probability"


class RerankerError(RuntimeError):
    """精排调用失败（调用方按「增强项失败即降级」处理，不向用户报错）。"""


class RerankerUnavailableError(RerankerError):
    """精排模型不可用（加载失败、依赖缺失、配置不完整）——与调用失败区分开。"""


class Reranker(Protocol):
    """精排协议：实现方可以是 HTTP 服务、HTTP mock 或本地模型。"""

    @property
    def model_name(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    def rerank(self, query: str, documents: Sequence[str]) -> list[RankResult]: ...


def logistic(value: float) -> float:
    """logit → 概率；只换单位，不改顺序（WeKnora §7 NIM 归一化）。"""
    try:
        return 1.0 / (1.0 + math.exp(-value))
    except OverflowError:
        return 0.0


def _redact(text: str, secret: str | None = None) -> str:
    redacted = re.sub(r"(?i)(bearer\s+)\S+", r"\1***", text)
    redacted = re.sub(r"sk-[A-Za-z0-9_\-]{4,}", "sk-***", redacted)
    if secret:
        redacted = redacted.replace(secret, "***")
    return redacted


class HttpReranker:
    """cohere 兼容协议的精排客户端（同步；检索线程内调用）。"""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        api_key_env: str = "",
        timeout_seconds: float = 30.0,
        batch_size: int = 32,
        max_concurrency: int = 4,
        max_request_chars: int = 8000,
        score_transform: str = "probability",
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._timeout = timeout_seconds
        self._batch_size = max(1, batch_size)
        self._max_concurrency = max(1, max_concurrency)
        self._max_request_chars = max(1, max_request_chars)
        self._score_transform = score_transform
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._active_key: str | None = None

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def model_id(self) -> str:
        return f"{self._base_url}#{self._model}"

    def _resolve_key(self) -> str:
        if self._api_key_env:
            value = os.environ.get(self._api_key_env, "")
            if value:
                return value
        return self._api_key

    def _headers(self) -> dict[str, str]:
        key = self._resolve_key()
        self._active_key = key or None
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _post_batch(self, query: str, documents: Sequence[str], offset: int) -> list[RankResult]:
        payload = {
            "model": self._model,
            "query": query,
            "documents": list(documents),
            "top_n": len(documents),
        }
        try:
            response = self._client.post(
                f"{self._base_url}/rerank",
                json=payload,
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RerankerError(
                f"rerank request failed: {_redact(str(exc), self._active_key)}"
            ) from exc
        return self._parse(response.json(), offset)

    def _parse(self, payload: Any, offset: int) -> list[RankResult]:
        if not isinstance(payload, dict):
            raise RerankerError("rerank response is not an object")
        data = cast(dict[str, Any], payload)
        raw: Any = data.get("results")
        if raw is None:
            output = data.get("output")
            if isinstance(output, dict):
                raw = cast(dict[str, Any], output).get("results")
        if not isinstance(raw, list):
            raise RerankerError("rerank response has no results")
        results: list[RankResult] = []
        for item in cast(list[Any], raw):
            if not isinstance(item, dict):
                continue
            entry = cast(dict[str, Any], item)
            index = entry.get("index")
            score = entry.get("relevance_score", entry.get("score"))
            if not isinstance(index, int) or not isinstance(score, (int, float)):
                continue
            value = float(score)
            if self._score_transform == "logit":
                value = logistic(value)
            results.append(RankResult(index=offset + index, relevance_score=value))
        return results

    def _split_batches(
        self, query: str, documents: Sequence[str]
    ) -> list[tuple[int, list[str]]]:
        """按「条数上限 + 单批字符总量」切批。

        厂商除了限制单次 document 数，还限制单次请求的字符总量（MaxRequestChars）；
        只按条数切，长 passage 批次会把整个请求顶掉。query 本身也占字符预算。
        """
        budget = max(1, self._max_request_chars - len(query))
        batches: list[tuple[int, list[str]]] = []
        current: list[str] = []
        current_chars = 0
        offset = 0
        for document in documents:
            length = len(document)
            exceeds_chars = current and current_chars + length > budget
            exceeds_count = len(current) >= self._batch_size
            if exceeds_chars or exceeds_count:
                batches.append((offset, current))
                offset += len(current)
                current = []
                current_chars = 0
            current.append(document)
            current_chars += length
        if current:
            batches.append((offset, current))
        return batches

    def rerank(self, query: str, documents: Sequence[str]) -> list[RankResult]:
        if not documents:
            return []
        if not self._base_url or not self._model:
            raise RerankerUnavailableError("rerank base_url/model is not configured")
        batches = self._split_batches(query, documents)
        if len(batches) == 1:
            merged = self._post_batch(query, batches[0][1], batches[0][0])
        else:

            def send(batch: tuple[int, list[str]]) -> list[RankResult]:
                return self._post_batch(query, batch[1], batch[0])

            with ThreadPoolExecutor(max_workers=self._max_concurrency) as pool:
                merged = [result for batch in pool.map(send, batches) for result in batch]
        merged.sort(key=lambda item: (-item.relevance_score, item.index))
        return merged


class LocalCrossEncoderReranker:
    """本地 cross-encoder：sentence-transformers `CrossEncoder`，logit → 概率。

    模型按需加载（首次检索会有加载耗时），加载后缓存在进程内；
    打分在锁内串行，避免多线程共用同一模型实例。加载失败与调用失败分开报：
    加载失败是「模型不可用」，调用失败是「这一次没打上分」。
    """

    def __init__(
        self,
        model_name: str,
        *,
        device: str = "cpu",
        batch_size: int = 32,
        scorer: Callable[[str, Sequence[str]], Sequence[float]] | None = None,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._batch_size = max(1, batch_size)
        self._scorer = scorer
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def model_id(self) -> str:
        return f"local:{self._model_name}"

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name, device=self._device)
        except Exception as exc:  # noqa: BLE001 - 依赖缺失/模型下载失败都属「模型不可用」
            raise RerankerUnavailableError(
                f"rerank model unavailable: {type(exc).__name__}: {exc}"
            ) from exc
        return self._model

    def _score(self, query: str, documents: Sequence[str]) -> list[float]:
        if self._scorer is not None:
            return [float(value) for value in self._scorer(query, documents)]
        model = self._ensure_model()
        scores: list[float] = []
        # 分批打分：200 条长 passage 一次性进模型峰值内存很高，批量更稳。
        for start in range(0, len(documents), self._batch_size):
            batch = documents[start : start + self._batch_size]
            pairs = [(query, document) for document in batch]
            raw = model.predict(pairs)
            scores.extend(float(value) for value in list(raw))
        return scores

    def rerank(self, query: str, documents: Sequence[str]) -> list[RankResult]:
        if not documents:
            return []
        with self._lock:
            scores = self._score(query, documents)
        results = [
            RankResult(index=index, relevance_score=logistic(score))
            for index, score in enumerate(scores)
        ]
        results.sort(key=lambda item: (-item.relevance_score, item.index))
        return results


def apply_threshold(
    scores: dict[int, float],
    *,
    threshold: float,
    fallback_min_score: float,
    explicit_scope: bool = False,
) -> tuple[list[int], str]:
    """三档阈值降级；返回 `(保留的下标, outcome)`。"""
    if not scores:
        return [], OUTCOME_NO_CANDIDATES
    kept = [index for index, score in scores.items() if score >= threshold]
    if kept:
        return kept, OUTCOME_OK
    if threshold > _DEGRADED_THRESHOLD:
        degraded_threshold = max(_DEGRADED_THRESHOLD, threshold * 0.7)
        kept = [index for index, score in scores.items() if score >= degraded_threshold]
        if kept:
            return kept, OUTCOME_THRESHOLD_DEGRADED
    floor = float("-inf") if explicit_scope else fallback_min_score
    best = max(scores.items(), key=lambda item: (item[1], -item[0]))
    if best[1] >= floor:
        return [best[0]], OUTCOME_FALLBACK_TOP1
    return [], OUTCOME_ALL_BELOW_THRESHOLD


def composite_score(
    model_score: float,
    base_score: float,
    *,
    source_weight_value: float = 1.0,
    model_weight: float = 0.6,
    base_weight: float = 0.3,
    source_weight: float = 0.1,
) -> float:
    """复合分：`0.6×model + 0.3×base + 0.1×source`，钳到 [0,1]。

    `source_weight_value` 是来源可信度（普通知识库 1.0、外部网页 0.95），
    `source_weight` 是它在复合分里的系数。
    """
    value = (
        model_weight * model_score
        + base_weight * base_score
        + source_weight * source_weight_value
    )
    return max(0.0, min(1.0, value))


def build_reranker(config: Any) -> Reranker | None:
    """按 config 构造精排器；未启用或缺 base_url/model 时返回 None（no_model）。"""
    if not bool(getattr(config, "enabled", False)):
        return None
    model = str(getattr(config, "model", "") or "").strip()
    backend = str(getattr(config, "backend", "remote") or "remote").strip().lower()
    if backend == "local":
        if not model:
            return None
        return LocalCrossEncoderReranker(
            model,
            device=str(getattr(config, "device", "cpu") or "cpu"),
            batch_size=int(getattr(config, "batch_size", 32)),
        )
    base_url = str(getattr(config, "base_url", "") or "").strip()
    if not base_url or not model:
        return None
    return HttpReranker(
        base_url=base_url,
        model=model,
        api_key=str(getattr(config, "api_key", "") or ""),
        api_key_env=str(getattr(config, "api_key_env", "") or ""),
        timeout_seconds=float(getattr(config, "timeout_seconds", 30.0)),
        batch_size=int(getattr(config, "batch_size", 32)),
        max_concurrency=int(getattr(config, "max_concurrency", 4)),
        max_request_chars=int(getattr(config, "max_request_chars", 8000)),
        score_transform=str(getattr(config, "score_transform", "probability")),
    )


def rerank_settings(config: Any) -> RerankSettings:
    defaults = RerankSettings()
    return RerankSettings(
        enabled=bool(getattr(config, "enabled", defaults.enabled)),
        max_candidates=int(getattr(config, "max_candidates", defaults.max_candidates)),
        max_passage_chars=int(
            getattr(config, "max_passage_chars", defaults.max_passage_chars)
        ),
        max_request_chars=int(
            getattr(config, "max_request_chars", defaults.max_request_chars)
        ),
        threshold=float(getattr(config, "threshold", defaults.threshold)),
        fallback_min_score=float(
            getattr(config, "fallback_min_score", defaults.fallback_min_score)
        ),
        model_weight=float(getattr(config, "model_weight", defaults.model_weight)),
        base_weight=float(getattr(config, "base_weight", defaults.base_weight)),
        source_weight=float(getattr(config, "source_weight", defaults.source_weight)),
        score_transform=str(
            getattr(config, "score_transform", defaults.score_transform)
        ),
    )


__all__ = [
    "OUTCOME_ALL_BELOW_THRESHOLD",
    "OUTCOME_FALLBACK_TOP1",
    "OUTCOME_MODEL_ERROR",
    "OUTCOME_MODEL_UNAVAILABLE",
    "OUTCOME_NO_CANDIDATES",
    "OUTCOME_NO_MODEL",
    "OUTCOME_OK",
    "OUTCOME_THRESHOLD_DEGRADED",
    "HttpReranker",
    "LocalCrossEncoderReranker",
    "RankResult",
    "RerankerError",
    "RerankerUnavailableError",
    "RerankSettings",
    "Reranker",
    "apply_threshold",
    "build_reranker",
    "composite_score",
    "logistic",
    "rerank_settings",
]
