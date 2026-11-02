"""WU-03：MemoryCheckpointStore 的真实 SQLite/WAL 备份与恢复（plan §5 Task 3）。

覆盖（plan Step 1 对应点）：

- ``backup_sqlite`` 叶函数走 **backup API**，不是 raw copy：WAL 里尚未合并进
  主文件的数据也必须出现在备份里。
- ``capture`` 在完成屏障后保存，并**原子**写 manifest：状态文件 hash、scope
  映射、origin_refs、store 版本、后台任务终态。
- ``restore`` 落在**独立 probe 子目录**，校验 sqlite 与向量索引版本一致后才
  开放查询；摘要不符 / store 版本不符 / 向量版本不符一律拒绝。
- 同一 checkpoint 的两次 restore 互不影响（probe 状态隔离）。
- 所有 store 内部路径都限制在评估根之内。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nanobot.evaluation.memory.checkpoints import (
    CheckpointError,
    CheckpointManifest,
    MemoryCheckpointStore,
    backup_sqlite,
    hash_file,
)
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory
from nanobot.memory.repository import add_memory, get_memory

# ---------------------------------------------------------------------------
# 身份 registry 重定向到临时目录（不写真实 ~/.nanobot）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_identity_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from nanobot import workspace_identity

    registry_root = tmp_path / "registry"

    def runtime_subdir(name: str) -> Path:
        path = registry_root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(workspace_identity, "get_runtime_subdir", runtime_subdir)
    workspace_identity.reset_workspace_identity_cache()
    yield
    workspace_identity.reset_workspace_identity_cache()

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _seed_memory(database: MemoryDatabase, content: str, workspace_id: str = "default") -> str:
    """用产品自己的持久化原语写一条记忆（不加任何测试专用写路径）。"""
    from nanobot.memory.models import MemoryPriority, MemoryType

    memory = Memory(
        id=f"mem-{content[:8]}-{abs(hash(content)) % 100000:05d}",
        content=content,
        created_at="2026-11-01T09:00:00Z",
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        workspace_id=workspace_id,
    )
    with database.connect() as conn:
        add_memory(conn, memory)
    return memory.id


def _wal_database(tmp_path: Path) -> MemoryDatabase:
    """建库并切到 WAL，让最新写入停留在 -wal 文件里（未合并进主文件）。"""
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    raw = sqlite3.connect(database.db_path)
    try:
        raw.execute("PRAGMA journal_mode=WAL")
        raw.commit()
    finally:
        raw.close()
    return database


def _count_rows(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# backup_sqlite 叶函数
# ---------------------------------------------------------------------------


def test_backup_sqlite_recovers_wal_state(tmp_path: Path) -> None:
    """backup API 必须把 -wal 里的数据带进备份；raw copy 主文件做不到这一点。"""
    workspace = tmp_path / "ws"
    database = _wal_database(workspace)
    memory_id = _seed_memory(database, "备份必须包含WAL中的这条记忆")

    destination = tmp_path / "backup" / "state.db"
    backup_sqlite(database.db_path, destination)

    assert destination.is_file()
    assert _count_rows(destination) == 1
    restored = sqlite3.connect(destination)
    try:
        row = restored.execute("SELECT id FROM memories").fetchone()
        assert row[0] == memory_id
    finally:
        restored.close()


def test_backup_sqlite_creates_parent_dirs(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    database = _wal_database(workspace)
    destination = tmp_path / "deep" / "nested" / "state.db"
    backup_sqlite(database.db_path, destination)
    assert destination.is_file()


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


def test_capture_writes_manifest_with_state_hash_scope_and_version(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    database = _wal_database(workspace)
    _seed_memory(database, "捕获应记录状态hash")

    store = MemoryCheckpointStore(tmp_path / "trial" / "checkpoints")
    snapshot = store.capture(
        "cp-1",
        database,
        scope_mapping={"subject_id": ["synthetic-u1"]},
        origin_refs={"s1": ["m1", "m2"]},
        background_tasks={"extraction": "completed"},
        workspace_path=workspace,
    )

    checkpoint_dir = tmp_path / "trial" / "checkpoints" / "checkpoints" / "cp-1"
    assert (checkpoint_dir / "state.db").is_file()
    assert (checkpoint_dir / "manifest.json").is_file()

    manifest = CheckpointManifest.load(checkpoint_dir / "manifest.json")
    assert manifest.sqlite_sha256 == hash_file(checkpoint_dir / "state.db")
    assert manifest.store_version  # 来自 _schema_meta 的真实 store 版本
    assert manifest.scope_mapping == {"subject_id": ["synthetic-u1"]}
    assert manifest.origin_refs == {"s1": ["m1", "m2"]}
    assert manifest.background_tasks == {"extraction": "completed"}
    # 工作区身份 marker 是产品隔离键的载体，必须进快照
    assert manifest.workspace_identity != ""

    assert snapshot.checkpoint_id == "cp-1"
    assert snapshot.state_digest == manifest.digest()
    assert snapshot.version >= 1
    assert any(ref.name == "state.db" for ref in snapshot.artifact_refs)


def test_capture_digest_changes_when_state_changes(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    database = _wal_database(workspace)
    store = MemoryCheckpointStore(tmp_path / "checkpoints")
    first = store.capture("cp-a", database, workspace_path=workspace)
    _seed_memory(database, "捕获后新增一条")
    second = store.capture("cp-b", database, workspace_path=workspace)
    assert first.state_digest != second.state_digest


def test_capture_confines_paths_to_root(tmp_path: Path) -> None:
    store = MemoryCheckpointStore(tmp_path / "checkpoints")
    with pytest.raises(CheckpointError):
        store.capture("../escape", _wal_database(tmp_path / "ws"), workspace_path=tmp_path / "ws")


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


def test_restore_into_independent_probe_dir_and_queries_work(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    database = _wal_database(workspace)
    _seed_memory(database, "恢复后必须可查询的记忆")

    store = MemoryCheckpointStore(tmp_path / "checkpoints")
    snapshot = store.capture("cp-r", database, workspace_path=workspace)

    probe_dir = tmp_path / "probes" / "p1"
    restored = store.restore(snapshot, probe_dir)

    # 独立 probe 子目录里有完整工作区结构（memory/state.db + 身份 marker）
    assert (restored.workspace_path / "memory" / "state.db").is_file()
    with MemoryDatabase(restored.workspace_path).connect() as conn:
        assert get_memory(conn, _rows_first_id(probe_dir)) is not None


def _rows_first_id(probe_dir: Path) -> str:
    conn = sqlite3.connect(probe_dir / "memory" / "state.db")
    try:
        return str(conn.execute("SELECT id FROM memories LIMIT 1").fetchone()[0])
    finally:
        conn.close()


def test_restore_rejects_state_digest_mismatch(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    database = _wal_database(workspace)
    _seed_memory(database, "摘要校验")
    store = MemoryCheckpointStore(tmp_path / "checkpoints")
    snapshot = store.capture("cp-d", database, workspace_path=workspace)


    tampered = snapshot.model_copy(update={"state_digest": "0" * 64})
    with pytest.raises(CheckpointError) as excinfo:
        store.restore(tampered, tmp_path / "probe")
    assert "STATE_DIGEST" in str(excinfo.value)


def test_restore_rejects_store_version_mismatch(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    database = _wal_database(workspace)
    store = MemoryCheckpointStore(tmp_path / "checkpoints")
    snapshot = store.capture("cp-v", database, workspace_path=workspace)
    with pytest.raises(CheckpointError) as excinfo:
        store.restore(snapshot, tmp_path / "probe", expected_store_version="999-other")
    assert "STORE_VERSION" in str(excinfo.value)


def test_restore_rejects_vector_version_mismatch(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    database = _wal_database(workspace)
    store = MemoryCheckpointStore(tmp_path / "checkpoints")
    snapshot = store.capture("cp-w", database, workspace_path=workspace)
    with pytest.raises(CheckpointError) as excinfo:
        store.restore(snapshot, tmp_path / "probe", expected_vector_version="ready:bge")
    assert "VECTOR_VERSION" in str(excinfo.value)


def test_restore_rejects_non_empty_destination(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    database = _wal_database(workspace)
    store = MemoryCheckpointStore(tmp_path / "checkpoints")
    snapshot = store.capture("cp-e", database, workspace_path=workspace)
    destination = tmp_path / "probe"
    destination.mkdir(parents=True)
    (destination / "keep.txt").write_text("existing", encoding="utf-8")
    with pytest.raises(CheckpointError) as excinfo:
        store.restore(snapshot, destination)
    assert "DESTINATION" in str(excinfo.value)


def test_two_restores_from_same_checkpoint_are_isolated(tmp_path: Path) -> None:
    """两个 probe 从同一 checkpoint 恢复后互不影响（probe 状态隔离）。"""
    workspace = tmp_path / "ws"
    database = _wal_database(workspace)
    _seed_memory(database, "共享起点的记忆")
    store = MemoryCheckpointStore(tmp_path / "checkpoints")
    snapshot = store.capture("cp-i", database, workspace_path=workspace)

    probe_a = store.restore(snapshot, tmp_path / "probes" / "a")
    probe_b = store.restore(snapshot, tmp_path / "probes" / "b")

    # 在 probe A 的工作区里继续写入，probe B 不得看到
    db_a = MemoryDatabase(probe_a.workspace_path)
    db_a.init_schema()
    _seed_memory(db_a, "只属于probeA的新记忆", workspace_id="default")

    conn_b = sqlite3.connect(probe_b.workspace_path / "memory" / "state.db")
    try:
        count_b = int(conn_b.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
    finally:
        conn_b.close()
    assert count_b == 1

    # checkpoint 原件也不被 mutate
    original = sqlite3.connect(tmp_path / "checkpoints" / "checkpoints" / "cp-i" / "state.db")
    try:
        count_original = int(original.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
    finally:
        original.close()
    assert count_original == 1


def test_restore_writes_marker_and_copy_rotates_identity(tmp_path: Path) -> None:
    """产品规则「复制目录会换新键」：恢复出的 probe 得到自己的新身份。

    快照携带原 marker（状态的一部分），但 registry 的复制检测会让 probe 旋转
    出新隔离键；由适配层做 probe 接管（见 test_memory_adapter）。
    """
    from nanobot.workspace_identity import workspace_identity_id

    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    original_id = workspace_identity_id(workspace)

    database = _wal_database(workspace)
    store = MemoryCheckpointStore(tmp_path / "checkpoints")
    snapshot = store.capture("cp-id", database, workspace_path=workspace)

    restored = store.restore(snapshot, tmp_path / "probes" / "p")
    marker = restored.workspace_path / ".nanobot" / "workspace-id"
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8").strip() == original_id
    # 复制检测：probe 按产品规则解析出「新键」，不冒充原工作区。
    assert workspace_identity_id(restored.workspace_path) != original_id


def test_missing_checkpoint_is_rejected(tmp_path: Path) -> None:
    from nanobot.evaluation.memory.models import MemoryStateSnapshot

    store = MemoryCheckpointStore(tmp_path / "checkpoints")
    ghost = MemoryStateSnapshot(
        version=1,
        checkpoint_id="missing",
        state_digest=hash_file(_wal_database(tmp_path / "ws").db_path),
    )
    with pytest.raises(CheckpointError):
        store.restore(ghost, tmp_path / "probe")


def test_capture_and_restore_roundtrip_bytes_identical_rows(tmp_path: Path) -> None:
    """manifest.json 是原子落盘的合法 JSON；恢复出的库与捕获内容逐行一致。"""
    workspace = tmp_path / "ws"
    database = _wal_database(workspace)
    ids = [_seed_memory(database, f"roundtrip {index}") for index in range(3)]

    store = MemoryCheckpointStore(tmp_path / "checkpoints")
    snapshot = store.capture("cp-j", database, workspace_path=workspace)
    manifest_path = tmp_path / "checkpoints" / "checkpoints" / "cp-j" / "manifest.json"
    json.loads(manifest_path.read_text(encoding="utf-8"))  # 原子写产物必须是合法 JSON

    restored = store.restore(snapshot, tmp_path / "probes" / "p")
    conn = sqlite3.connect(restored.workspace_path / "memory" / "state.db")
    try:
        restored_ids = {
            row[0] for row in conn.execute("SELECT id FROM memories").fetchall()
        }
    finally:
        conn.close()
    assert restored_ids == set(ids)


def test_vector_index_directory_is_captured_when_ready(tmp_path: Path) -> None:
    """向量索引就绪时随快照落盘；恢复校验版本一致后才开放。"""
    workspace = tmp_path / "ws"
    database = _wal_database(workspace)
    store = MemoryCheckpointStore(tmp_path / "checkpoints")

    class _ReadyVector:
        state = "ready"
        model_name = "fake-bge"
        dimensions = 8

        def __init__(self) -> None:
            self.root = workspace / "memory" / "chromadb"
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "index.bin").write_bytes(b"v1")
            self._index_path = self.root  # 与真实 VectorStore 同名私有属性

    vector = _ReadyVector()
    snapshot = store.capture("cp-vec", database, vector_store=vector, workspace_path=workspace)
    manifest = CheckpointManifest.load(
        tmp_path / "checkpoints" / "checkpoints" / "cp-vec" / "manifest.json"
    )
    assert manifest.vector_version == "ready:fake-bge"
    assert (tmp_path / "checkpoints" / "checkpoints" / "cp-vec" / "vector" / "index.bin").exists()

    restored = store.restore(snapshot, tmp_path / "probes" / "v")
    assert (restored.workspace_path / "memory" / "chromadb" / "index.bin").exists()

    # 版本不一致拒绝
    with pytest.raises(CheckpointError) as excinfo:
        store.restore(snapshot, tmp_path / "probes" / "v2", expected_vector_version="ready:other")
    assert "VECTOR_VERSION" in str(excinfo.value)


def test_capture_without_vector_records_absent(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    database = _wal_database(workspace)
    store = MemoryCheckpointStore(tmp_path / "checkpoints")
    store.capture("cp-novec", database, workspace_path=workspace)
    manifest = CheckpointManifest.load(
        tmp_path / "checkpoints" / "checkpoints" / "cp-novec" / "manifest.json"
    )
    assert manifest.vector_version == "absent"
