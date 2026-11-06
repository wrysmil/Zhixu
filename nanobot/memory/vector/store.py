"""向量存储：ChromaDB client + collection + 模型推理 + 状态机。

设计依据（spec §4.3）：
- ``_ensure_initialized()`` **绝不阻塞调用方**：``loading`` 中直接返回 ``False``；
- 普通失败固定 300 s 冷却；``ImportError`` 指数退避 300→600→1200→…→3600 s 封顶；
- 每个公开方法 ``try/except`` 吞掉异常 → 返回 ``[]`` / ``False``，仅 log。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.memory.vector import model_hub
from nanobot.memory.vector.settings import VectorSettings

_COLLECTION_NAME = "memories"
_EPISODES_COLLECTION_NAME = "episodes"

_COOLDOWN_SECONDS = 300.0
_COOLDOWN_MAX_SECONDS = 3600.0


class VectorStore:
    """ChromaDB 向量索引的最小封装。"""

    def __init__(self, settings: VectorSettings, *, workspace: Path) -> None:
        self._settings = settings
        self._workspace = Path(workspace)
        self._index_path = (
            Path(settings.index_path) if settings.index_path
            else self._workspace / "memory" / "chromadb"
        )
        self._state = "idle"
        self._error: str | None = None
        self._cooldown_until = 0.0
        self._cooldown_seconds = _COOLDOWN_SECONDS
        self._model: Any = None
        self._collection: Any = None
        self._episodes_collection: Any = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

        if settings.enabled:
            self._start_background_load()

    @property
    def enabled(self) -> bool:
        if not self._settings.enabled:
            return False
        if self._state in ("idle", "failed") and self._cooldown_expired():
            self._start_background_load()
        return True

    @property
    def state(self) -> str:
        return self._state

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def model_name(self) -> str:
        return self._settings.model

    @property
    def dimensions(self) -> int:
        return self._settings.dimensions

    def _cooldown_expired(self) -> bool:
        return time.monotonic() >= self._cooldown_until

    def _start_background_load(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._state = "loading"
            self._thread = threading.Thread(
                target=self._load_worker, name="nanobot-vector-load", daemon=True
            )
            self._thread.start()

    def _load_worker(self) -> None:
        try:
            self._do_load()
            self._state = "ready"
            self._error = None
            self._cooldown_seconds = _COOLDOWN_SECONDS
            logger.info(
                "vector store ready: model={} dim={}", self._settings.model, self._settings.dimensions
            )
        except ImportError as exc:
            self._mark_failed(exc, is_import_error=True)
        except Exception as exc:
            self._mark_failed(exc, is_import_error=False)

    def _do_load(self) -> None:
        import os

        os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
        os.environ.setdefault("CHROMA_TELEMETRY", "False")

        model_hub.apply_source_env(self._settings.download_source)
        model_path = model_hub.ensure_model(
            self._settings.model,
            source=self._settings.download_source,
            local_dir=self._settings.local_model_dir,
        )

        import chromadb
        from chromadb.config import Settings as ChromaSettings
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(model_path, device=self._settings.device)

        actual = int(model.get_sentence_embedding_dimension() or 0)
        if actual != self._settings.dimensions:
            raise ValueError(
                f"embedding dimension mismatch: model={actual} "
                f"configured={self._settings.dimensions}"
            )

        self._index_path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(
            path=str(self._index_path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        collection = client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        episodes = client.get_or_create_collection(
            name=_EPISODES_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._activate(model, collection, episodes)

    def _activate(
        self, model: Any, collection: Any, episodes_collection: Any = None
    ) -> None:
        actual = int(model.get_sentence_embedding_dimension() or 0)
        if actual != self._settings.dimensions:
            self._mark_failed(
                ValueError(
                    f"embedding dimension mismatch: model={actual} "
                    f"configured={self._settings.dimensions}"
                ),
                is_import_error=False,
            )
            return
        self._model = model
        self._collection = collection
        self._episodes_collection = episodes_collection
        self._state = "ready"
        self._error = None

    def _mark_failed(self, exc: Exception, *, is_import_error: bool) -> None:
        self._state = "failed"
        self._error = f"{type(exc).__name__}: {exc}"
        if is_import_error:
            self._cooldown_seconds = min(self._cooldown_seconds * 2, _COOLDOWN_MAX_SECONDS)
        else:
            self._cooldown_seconds = _COOLDOWN_SECONDS
        self._cooldown_until = time.monotonic() + self._cooldown_seconds
        logger.warning(
            "vector store unavailable ({}), retry in {:.0f}s: {}",
            "import" if is_import_error else "runtime",
            self._cooldown_seconds,
            self._error,
        )

    def _initialize_now(self) -> bool:
        if self._state == "ready":
            return True
        if not self._settings.enabled:
            return False
        try:
            self._do_load()
            return True
        except Exception:
            return False

    def _ready(self) -> bool:
        # 必须读 self.enabled（而非 self._settings.enabled）：enabled 是**唯一**的
        # 重试触发点——冷却到期且状态为 idle/failed 时会重启后台加载（D2）。
        # 此前读 _settings.enabled，导致该属性全仓无调用方、重试机制实际失效，
        # 向量层一旦失败便永久停在 failed 直到进程重启（D1/D2 未实现）。
        return self.enabled and self._state == "ready"

    def upsert(self, memory_id: str, content: str, metadata: dict[str, Any]) -> bool:
        if not self._ready():
            return False
        try:
            vector = self._encode([content])[0]
            self._collection.upsert(
                ids=[memory_id],
                embeddings=[vector],
                documents=[content],
                metadatas=[_clean_metadata(metadata)],
            )
            return True
        except Exception as exc:
            logger.warning("vector upsert failed for {}: {}", memory_id, exc)
            return False

    def remove(self, memory_id: str) -> bool:
        if not self._ready():
            return False
        try:
            self._collection.delete(ids=[memory_id])
            return True
        except Exception as exc:
            logger.warning("vector delete failed for {}: {}", memory_id, exc)
            return False

    def upsert_episode(
        self, episode_id: str, content: str, metadata: dict[str, Any]
    ) -> bool:
        """写入/更新一条经验库条目的向量；集合与模型与 memories 共用。"""
        if not self._episodes_ready():
            return False
        try:
            vector = self._encode([content])[0]
            self._episodes_collection.upsert(
                ids=[episode_id],
                embeddings=[vector],
                documents=[content],
                metadatas=[_clean_metadata(metadata)],
            )
            return True
        except Exception as exc:
            logger.warning("episode vector upsert failed for {}: {}", episode_id, exc)
            return False

    def remove_episode(self, episode_id: str) -> bool:
        if not self._episodes_ready():
            return False
        try:
            self._episodes_collection.delete(ids=[episode_id])
            return True
        except Exception as exc:
            logger.warning("episode vector delete failed for {}: {}", episode_id, exc)
            return False

    def search_episodes(self, query: str, *, limit: int = 15) -> list[tuple[str, float]]:
        """返回 ``[(episode_id, score)]``，**score 越大越相关**。"""
        if not self._episodes_ready():
            return []
        try:
            vector = self._encode([query])[0]
            res = self._episodes_collection.query(
                query_embeddings=[vector], n_results=int(limit)
            )
            ids = (res.get("ids") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            return [
                (str(eid), _distance_to_score(dist))
                for eid, dist in zip(ids, dists, strict=False)
            ]
        except Exception as exc:
            logger.warning("episode vector search failed: {}", exc)
            return []

    def list_episode_ids(self) -> list[str]:
        if not self._episodes_ready():
            return []
        try:
            return [str(i) for i in self._episodes_collection.get().get("ids", [])]
        except Exception as exc:
            logger.warning("episode vector list_ids failed: {}", exc)
            return []

    def delete_episode_ids(self, ids: list[str]) -> int:
        if not ids or not self._episodes_ready():
            return 0
        try:
            self._episodes_collection.delete(ids=list(ids))
            return len(ids)
        except Exception as exc:
            logger.warning("episode vector bulk delete failed: {}", exc)
            return 0

    def episode_count(self) -> int:
        if not self._episodes_ready():
            return 0
        try:
            return int(self._episodes_collection.count())
        except Exception as exc:
            logger.warning("episode vector count failed: {}", exc)
            return 0

    def _episodes_ready(self) -> bool:
        return self._ready() and self._episodes_collection is not None

    def search(self, query: str, *, limit: int = 15) -> list[tuple[str, float]]:
        """返回 ``[(memory_id, score)]``，**score 越大越相关**。"""
        if not self._ready():
            return []
        try:
            vector = self._encode([query])[0]
            res = self._collection.query(query_embeddings=[vector], n_results=int(limit))
            ids = (res.get("ids") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            return [
                (str(mid), _distance_to_score(dist))
                for mid, dist in zip(ids, dists, strict=False)
            ]
        except Exception as exc:
            logger.warning("vector search failed: {}", exc)
            return []

    def count(self) -> int:
        if not self._ready():
            return 0
        try:
            return int(self._collection.count())
        except Exception as exc:
            logger.warning("vector count failed: {}", exc)
            return 0

    def list_ids(self) -> list[str]:
        if not self._ready():
            return []
        try:
            return [str(i) for i in self._collection.get().get("ids", [])]
        except Exception as exc:
            logger.warning("vector list_ids failed: {}", exc)
            return []

    def delete_ids(self, ids: list[str]) -> int:
        if not ids or not self._ready():
            return 0
        try:
            self._collection.delete(ids=list(ids))
            return len(ids)
        except Exception as exc:
            logger.warning("vector bulk delete failed: {}", exc)
            return 0

    def _encode(self, texts: list[str]):
        return self._model.encode(texts, normalize_embeddings=True).tolist()


def _distance_to_score(distance: float) -> float:
    """cosine distance ∈ [0,2] → score ∈ [0,1]，**越大越相关**。"""
    return max(0.0, min(1.0, 1.0 - float(distance)))


_METADATA_SCALARS = (str, int, float, bool)


def _clean_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v for k, v in metadata.items() if isinstance(v, _METADATA_SCALARS) and k != "tags"
    }
