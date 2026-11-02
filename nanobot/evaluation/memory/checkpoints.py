"""记忆状态检查点存储（plan §5 Task 3 Step 2）。

职责与硬约束
------------

- :func:`backup_sqlite`：**标准库 backup API** 的叶函数——绝不直接 copy 活动
  DB / WAL 文件。WAL 中尚未合并进主文件的数据也必须出现在备份里。
- :class:`MemoryCheckpointStore.capture`：在完成屏障之后保存 SQLite 状态并
  **原子**写 manifest（tmp + ``os.replace``）：状态文件 hash、scope 映射、
  origin_refs、store 版本、向量索引版本、后台任务终态、工作区身份 marker。
- :class:`MemoryCheckpointStore.restore`：恢复到调用方指定的**独立 probe 子
  目录**；校验 sqlite 与向量索引版本一致后才开放查询（返回恢复出的工作区
  路径，由调用方在其上重建查询服务）。

路径纪律：store 内部所有路径都限制在自己的根目录之内（``_resolve`` 强制）；
快照里不保存、也不回读任何 gold。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nanobot.evaluation.memory.models import (
    MemoryArtifactRef,
    MemoryCoverage,
    MemoryStateSnapshot,
)

__all__ = [
    "CheckpointError",
    "CheckpointManifest",
    "CheckpointRestoreResult",
    "MemoryCheckpointStore",
    "backup_sqlite",
    "hash_file",
]

_MANIFEST_NAME = "manifest.json"
_SQLITE_NAME = "state.db"
_VECTOR_DIR_NAME = "vector"
_IDENTITY_MARKER_RELPATH = Path(".nanobot") / "workspace-id"


def hash_file(path: Path) -> str:
    """文件的 sha256 hex 摘要（流式读取，不整文件载入内存）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_sqlite(source: Path, destination: Path) -> None:
    """SQLite backup API 叶函数（plan §5 给定的参考实现）。

    通过 ``sqlite3.Connection.backup`` 完成一致快照：读取方看到的是包含 WAL
    内容的完整逻辑状态，而不是主文件的字节副本。
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()


class CheckpointError(Exception):
    """快照/恢复失败。``str(exc)`` 携带机器可读的 reason code。"""


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckpointManifest:
    """检查点 manifest（契约 §3：原子保存、可校验）。

    字段全部是 JSON 标量/容器；``digest()`` 是对 canonical JSON 的 sha256，
    恢复时重算比对——manifest 被篡改即拒绝。
    """

    checkpoint_id: str
    created_at: str
    store_version: str
    sqlite_sha256: str
    sqlite_bytes: int
    vector_version: str = "absent"
    workspace_identity: str = ""
    scope_mapping: dict[str, list[str]] = field(default_factory=dict)
    origin_refs: dict[str, list[str]] = field(default_factory=dict)
    background_tasks: dict[str, Any] = field(default_factory=dict)

    # -- 序列化 ------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "created_at": self.created_at,
            "store_version": self.store_version,
            "sqlite_sha256": self.sqlite_sha256,
            "sqlite_bytes": self.sqlite_bytes,
            "vector_version": self.vector_version,
            "workspace_identity": self.workspace_identity,
            "scope_mapping": self.scope_mapping,
            "origin_refs": self.origin_refs,
            "background_tasks": self.background_tasks,
        }

    def digest(self) -> str:
        payload = json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def dump(self, path: Path) -> None:
        """原子写：tmp 文件 + fsync + ``os.replace``。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp-{uuid.uuid4().hex}")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()

    @classmethod
    def load(cls, path: Path) -> "CheckpointManifest":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CheckpointError("CHECKPOINT_MANIFEST_NOT_FOUND") from exc
        except json.JSONDecodeError as exc:
            raise CheckpointError("CHECKPOINT_MANIFEST_INVALID") from exc
        if not isinstance(raw, dict):
            raise CheckpointError("CHECKPOINT_MANIFEST_INVALID")
        return cls(
            checkpoint_id=str(raw.get("checkpoint_id", "")),
            created_at=str(raw.get("created_at", "")),
            store_version=str(raw.get("store_version", "")),
            sqlite_sha256=str(raw.get("sqlite_sha256", "")),
            sqlite_bytes=int(raw.get("sqlite_bytes", 0)),
            vector_version=str(raw.get("vector_version", "absent")),
            workspace_identity=str(raw.get("workspace_identity", "")),
            scope_mapping={
                str(k): [str(item) for item in v]
                for k, v in dict(raw.get("scope_mapping") or {}).items()
            },
            origin_refs={
                str(k): [str(item) for item in v]
                for k, v in dict(raw.get("origin_refs") or {}).items()
            },
            background_tasks=dict(raw.get("background_tasks") or {}),
        )


@dataclass(frozen=True)
class CheckpointRestoreResult:
    """恢复结果：独立 probe 子目录里的工作区路径 + 已校验的 manifest。"""

    workspace_path: Path
    manifest: CheckpointManifest


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


class MemoryCheckpointStore:
    """记忆状态检查点的保存与恢复（全部路径限制在 store 根内）。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    # -- 路径纪律 ----------------------------------------------------------

    def _resolve(self, *parts: str) -> Path:
        candidate = (self._root.joinpath(*parts)).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise CheckpointError("CHECKPOINT_PATH_OUTSIDE_ROOT")
        return candidate

    # -- capture -----------------------------------------------------------

    def capture(
        self,
        checkpoint_id: str,
        database: Any,
        *,
        workspace_path: Path | None = None,
        vector_store: Any | None = None,
        scope_mapping: dict[str, list[str]] | None = None,
        origin_refs: dict[str, list[str]] | None = None,
        background_tasks: dict[str, Any] | None = None,
    ) -> MemoryStateSnapshot:
        """在完成屏障之后保存状态并原子落 manifest。

        ``database.db_path`` 是活动的 SQLite 文件；这里走 backup API 产生一致
        快照，不触碰 WAL 文件本身。``vector_store`` 就绪时把向量索引目录一并
        落盘；未就绪记录为 ``absent``（绝不假装向量状态可用）。
        """
        if not checkpoint_id or "/" in checkpoint_id or "\\" in checkpoint_id or checkpoint_id in (".", ".."):
            raise CheckpointError("CHECKPOINT_ID_INVALID")
        checkpoint_dir = self._resolve("checkpoints", checkpoint_id)
        if checkpoint_dir.exists():
            raise CheckpointError("CHECKPOINT_ID_ALREADY_EXISTS")
        checkpoint_dir.mkdir(parents=True)

        db_path = Path(getattr(database, "db_path"))
        if not db_path.is_file():
            raise CheckpointError("SOURCE_DB_NOT_FOUND")
        backup_sqlite(db_path, checkpoint_dir / _SQLITE_NAME)

        # 向量索引：就绪时复制索引目录；否则记录 absent，不伪造。
        vector_version = "absent"
        if vector_store is not None and self._vector_is_ready(vector_store):
            vector_dir = Path(self._vector_index_dir(vector_store))
            if vector_dir.is_dir():
                shutil.copytree(vector_dir, checkpoint_dir / _VECTOR_DIR_NAME)
            vector_version = f"ready:{getattr(vector_store, 'model_name', 'unknown')}"

        manifest = CheckpointManifest(
            checkpoint_id=checkpoint_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            store_version=self._read_store_version(checkpoint_dir / _SQLITE_NAME),
            sqlite_sha256=hash_file(checkpoint_dir / _SQLITE_NAME),
            sqlite_bytes=(checkpoint_dir / _SQLITE_NAME).stat().st_size,
            vector_version=vector_version,
            workspace_identity=self._read_workspace_identity(workspace_path),
            scope_mapping={
                str(k): [str(item) for item in v]
                for k, v in dict(scope_mapping or {}).items()
            },
            origin_refs={
                str(k): [str(item) for item in v]
                for k, v in dict(origin_refs or {}).items()
            },
            background_tasks=dict(background_tasks or {}),
        )
        manifest.dump(checkpoint_dir / _MANIFEST_NAME)

        artifacts = [
            MemoryArtifactRef(
                id=f"{checkpoint_id}-sqlite",
                name=_SQLITE_NAME,
                media_type="application/sqlite",
                size_bytes=manifest.sqlite_bytes,
                sha256=manifest.sqlite_sha256,
            )
        ]
        return MemoryStateSnapshot(
            version=1,
            checkpoint_id=checkpoint_id,
            state_digest=manifest.digest(),
            artifact_refs=artifacts,
            origin_map=dict(manifest.origin_refs),
            storage_coverage=MemoryCoverage(scored=1, planned=1),
        )

    # -- restore -----------------------------------------------------------

    def restore(
        self,
        snapshot: MemoryStateSnapshot,
        destination: Path,
        *,
        expected_store_version: str | None = None,
        expected_vector_version: str | None = None,
    ) -> CheckpointRestoreResult:
        """恢复到独立 probe 子目录；校验通过后才返回可开放查询的工作区。

        校验项（任一不符即 :class:`CheckpointError`，不开放查询）：

        - manifest 存在且 ``state_digest`` 与快照一致；
        - 备份 SQLite 文件的 sha256 与 manifest 记录一致；
        - 备份库内的 schema 版本与 manifest 一致（防止半迁移库混入）；
        - 调用方期望的 store 版本 / 向量索引版本（如有）与 manifest 一致；
        - 目标目录不存在或为空（不覆盖既有 probe 状态）。
        """
        checkpoint_dir = self._resolve("checkpoints", snapshot.checkpoint_id)
        manifest_path = checkpoint_dir / _MANIFEST_NAME
        if not (checkpoint_dir / _SQLITE_NAME).is_file() or not manifest_path.is_file():
            raise CheckpointError("CHECKPOINT_NOT_FOUND")
        manifest = CheckpointManifest.load(manifest_path)
        if manifest.digest() != snapshot.state_digest:
            raise CheckpointError("STATE_DIGEST_MISMATCH")

        state_file = checkpoint_dir / _SQLITE_NAME
        if hash_file(state_file) != manifest.sqlite_sha256:
            raise CheckpointError("STATE_FILE_HASH_MISMATCH")
        actual_store_version = self._read_store_version(state_file)
        if actual_store_version != manifest.store_version:
            raise CheckpointError(
                f"STORE_VERSION_MISMATCH:manifest={manifest.store_version},"
                f"backup={actual_store_version}"
            )
        if expected_store_version is not None and manifest.store_version != expected_store_version:
            raise CheckpointError(
                f"STORE_VERSION_MISMATCH:expected={expected_store_version},"
                f"manifest={manifest.store_version}"
            )
        if expected_vector_version is not None and manifest.vector_version != expected_vector_version:
            raise CheckpointError(
                f"VECTOR_VERSION_MISMATCH:expected={expected_vector_version},"
                f"manifest={manifest.vector_version}"
            )

        destination = Path(destination).resolve()
        if destination.exists() and any(destination.iterdir()):
            raise CheckpointError("RESTORE_DESTINATION_NOT_EMPTY")
        (destination / "memory").mkdir(parents=True, exist_ok=True)
        shutil.copy2(state_file, destination / "memory" / _SQLITE_NAME)

        # 恢复向量索引目录（捕获时存在才复制）。落回 VectorStore 的默认索引
        # 目录 ``memory/chromadb``（自定义 index_path 的映射不在 v1 范围）。
        if manifest.vector_version != "absent" and (checkpoint_dir / _VECTOR_DIR_NAME).is_dir():
            shutil.copytree(
                checkpoint_dir / _VECTOR_DIR_NAME,
                destination / "memory" / "chromadb",
                dirs_exist_ok=True,
            )

        # 工作区身份 marker：快照状态的一部分，随恢复落盘。注意产品规则是
        # 「复制目录会换新键」（registry 复制检测）：probe 解析身份时会旋转出
        # 自己的新键，由适配层把恢复出的行重新绑定到 probe 身份（probe 接管）。
        if manifest.workspace_identity:
            marker = destination / _IDENTITY_MARKER_RELPATH
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(manifest.workspace_identity, encoding="utf-8")

        return CheckpointRestoreResult(workspace_path=destination, manifest=manifest)

    # -- 内部 --------------------------------------------------------------

    @staticmethod
    def _read_store_version(db_path: Path) -> str:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT value FROM _schema_meta WHERE key = 'version'"
            ).fetchone()
            return str(row[0]) if row is not None else ""
        finally:
            conn.close()

    @staticmethod
    def _read_workspace_identity(workspace_path: Path | None) -> str:
        """工作区身份：优先读 marker；缺失时按产品规则补建（identity 会写 marker）。"""
        if workspace_path is None:
            return ""
        marker = Path(workspace_path) / _IDENTITY_MARKER_RELPATH
        if marker.is_file():
            return marker.read_text(encoding="utf-8").strip()
        from nanobot.memory.provenance import memory_workspace_id

        return memory_workspace_id(str(workspace_path))

    @staticmethod
    def _vector_is_ready(vector_store: Any) -> bool:
        state = str(getattr(vector_store, "state", "ready"))
        return state == "ready"

    @staticmethod
    def _vector_index_dir(vector_store: Any) -> Path:
        """向量索引的落盘目录（VectorStore 私有属性，取不到时拒绝）。"""
        index_path = getattr(vector_store, "_index_path", None)
        if isinstance(index_path, Path):
            return index_path
        raise CheckpointError("VECTOR_INDEX_PATH_UNKNOWN")
