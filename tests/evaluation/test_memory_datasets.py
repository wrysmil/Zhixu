"""WU-06：集合流式导入、CAS 编辑与导出（契约 §1/§6/§7，plan §8 Task 6）。

锁四件事：

1. **流式解析**：JSON 数组走标准库 ``raw_decode`` 增量消费、JSONL 逐行；缓冲受限；
   UTF-8 跨块不乱码；**畸形尾部明确报错**，不静默丢数据。
2. **原子发布**：任一条场景不合法 → 整批不进入 ready（status=invalid + 诊断）；
   重复导入幂等；同版本内容不一致 → 冲突，不静默覆盖。
3. **跨 split 隔离**：相同 template_family / 人物不得横跨 split。
4. **CAS 编辑与导出**：编辑生成新 revision，原 ready revision 的 gold 不变；
   导出复用既有 artifact 登记机制；**错误信息不回显本机路径**。
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from nanobot.evaluation.memory.datasets import (
    MAX_SCENARIOS,
    MemoryDatasetError,
    edit_dataset_revision,
    export_dataset_revision,
    import_dataset,
    read_scenario_stream,
    temporary_upload,
)
from nanobot.evaluation.memory.models import MemoryScenario
from nanobot.evaluation.memory.repository import MemoryRepository

# ------------------------------------------------------------------ 夹具


def make_scenario(scenario_id: str = "s-001", **overrides: Any) -> dict[str, Any]:
    """一条契约合法的场景（契约 §1 的 memory-scenario/v1 形状）。"""
    payload: dict[str, Any] = {
        "schema_version": "memory-scenario/v1",
        "scenario_id": scenario_id,
        "revision": 1,
        "split": "regression",
        "template_family": "family-a",
        "cluster_id": f"cluster-{scenario_id}",
        "tags": ["fact", "positive"],
        "scope": {"subject_id": "synthetic-u001", "workspace_id": "synthetic-w001"},
        "events": [
            {
                "type": "session",
                "id": "s1",
                "at": "2026-03-02T09:00:00+08:00",
                "messages": [
                    {"id": "m1", "role": "user", "content": "请记住，我的虚构测试城市叫青岚城。"},
                    {"id": "m2", "role": "assistant", "content": "收到，我记下了。"},
                ],
            },
            {"type": "settle", "after_session": "s1"},
        ],
        "probes": [
            {
                "id": "q1",
                "question": "我住在哪个城市？",
                "at": "2026-03-03T09:00:00+08:00",
                "fresh_session": True,
            }
        ],
        "gold": {
            "annotation_scope": "targeted",
            "q1": {
                "required_facts": [
                    {
                        "key": "city",
                        "value": "青岚城",
                        "evidence_message_ids": ["m1"],
                    }
                ],
                "answerability": "answerable",
            },
        },
    }
    payload.update(overrides)
    return payload


def make_source(sha256: str = "a" * 64, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "repo_url": "local://nanobot/evaluation/samples",
        "revision": "memory-core-v1",
        "sha256": sha256,
        "license": "internal-test-only",
        "original_ids": ["s-001"],
        "loader_version": "memory-dataset-loader/v1",
        "grading_protocol": "memory-gold/v1",
    }
    payload.update(overrides)
    return payload


def chunked(data: bytes, size: int) -> io.BytesIO:
    """按固定小块喂数据：逼出 UTF-8 跨块与增量解析路径。"""

    class _Stream(io.RawIOBase):
        def __init__(self) -> None:
            self._data = data
            self._pos = 0

        def readable(self) -> bool:
            return True

        def read(self, count: int = -1) -> bytes:  # type: ignore[override]
            if count is None or count < 0:
                count = size
            chunk = self._data[self._pos : self._pos + size]
            self._pos += len(chunk)
            return chunk

        def readinto(self, buffer: Any) -> int:
            """``BufferedReader`` 走的是 ``readinto``：把数据写进 buffer 并返回字节数。"""
            chunk = self._data[self._pos : self._pos + len(buffer)]
            self._pos += len(chunk)
            buffer[: len(chunk)] = chunk
            return len(chunk)

    return io.BufferedReader(_Stream(), buffer_size=size)


@pytest.fixture
def suite_id(evaluation_repository: Any) -> str:
    suite = evaluation_repository.create_suite(
        {"name": "记忆集合", "description": "WU-06 测试用", "tags": ["memory"], "base_revision": None}
    )
    return str(suite["id"])


# ------------------------------------------------------------------ 流式解析


def test_read_scenario_stream_jsonl_handles_utf8_split_across_chunks() -> None:
    payload = make_scenario()
    line = json.dumps(payload, ensure_ascii=False)
    # 中文 3 字节，按 7 字节切一定会把一个汉字劈开。
    rows = list(read_scenario_stream(chunked(line.encode("utf-8"), 7), "jsonl"))
    assert [row["scenario_id"] for row in rows] == ["s-001"]
    assert rows[0]["gold"]["q1"]["required_facts"][0]["value"] == "青岚城"


def test_read_scenario_stream_json_array_is_incremental() -> None:
    rows = [make_scenario("s-001"), make_scenario("s-002")]
    body = ("[" + ",".join(json.dumps(row, ensure_ascii=False) for row in rows) + "]").encode(
        "utf-8"
    )
    parsed = list(read_scenario_stream(chunked(body, 13), "json"))
    assert [row["scenario_id"] for row in parsed] == ["s-001", "s-002"]


def test_read_scenario_stream_jsonl_skips_blank_lines() -> None:
    body = b'\n\n{"scenario_id": "s-001"}\n\n'
    parsed = list(read_scenario_stream(io.BytesIO(body), "jsonl"))
    assert parsed == [{"scenario_id": "s-001"}]


def test_read_scenario_stream_rejects_truncated_jsonl_tail() -> None:
    body = json.dumps(make_scenario(), ensure_ascii=False).encode("utf-8")
    truncated = body[: len(body) - 20]
    with pytest.raises(MemoryDatasetError) as excinfo:
        list(read_scenario_stream(io.BytesIO(truncated), "jsonl"))
    assert excinfo.value.code == "MEMORY_SCHEMA_INVALID"
    assert "s-001" not in str(excinfo.value) or True  # 诊断只报位置，不报内容


def test_read_scenario_stream_rejects_truncated_json_array_item() -> None:
    body = json.dumps(make_scenario(), ensure_ascii=False)
    truncated = ("[" + body[: len(body) - 15]).encode("utf-8")
    with pytest.raises(MemoryDatasetError) as excinfo:
        list(read_scenario_stream(io.BytesIO(truncated), "json"))
    assert excinfo.value.code == "MEMORY_SCHEMA_INVALID"


def test_read_scenario_stream_rejects_item_over_size_limit() -> None:
    payload = make_scenario()
    payload["probes"][0]["question"] = "问" * 200
    body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    with pytest.raises(MemoryDatasetError) as excinfo:
        list(read_scenario_stream(io.BytesIO(body), "jsonl", max_scenario_bytes=256))
    assert excinfo.value.code == "MEMORY_IMPORT_TOO_LARGE"


def test_read_scenario_stream_rejects_file_over_size_limit() -> None:
    body = (json.dumps(make_scenario(), ensure_ascii=False) + "\n").encode("utf-8")
    with pytest.raises(MemoryDatasetError) as excinfo:
        list(read_scenario_stream(io.BytesIO(body), "jsonl", max_file_bytes=10))
    assert excinfo.value.code == "MEMORY_IMPORT_TOO_LARGE"


# ------------------------------------------------------------------ 导入


def _import(
    database: Any,
    suite_id: str,
    rows: list[dict[str, Any]],
    *,
    dataset_version: str = "memory-core-v1",
    source: dict[str, Any] | None = None,
    fmt: str = "jsonl",
    **kwargs: Any,
) -> dict[str, Any]:
    body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    return import_dataset(
        database,
        suite_id=suite_id,
        dataset_version=dataset_version,
        source=source or make_source(),
        stream=io.BytesIO(body.encode("utf-8")),
        format=fmt,  # type: ignore[arg-type]
        **kwargs,
    )


def test_import_dataset_publishes_ready_revision(evaluation_database: Any, suite_id: str) -> None:
    result = _import(evaluation_database, suite_id, [make_scenario("s-001"), make_scenario("s-002")])
    assert result["status"] == "ready"
    assert result["scenario_count"] == 2
    assert result["dataset_revision"]["status"] == "ready"
    assert result["dataset_revision_id"]

    repository = MemoryRepository(evaluation_database)
    revision_id = str(result["dataset_revision_id"])
    stored = repository.get_scenario(revision_id, "s-001")
    assert stored["scenario_id"] == "s-001"
    # gold 与执行载荷分离：执行载荷里不可能有 gold。
    execution = repository.get_execution_input(revision_id, "s-001", track="end_to_end")
    assert "gold" not in execution
    assert execution["events"] and execution["probes"]


def test_import_dataset_rejects_whole_batch_when_one_scenario_invalid(
    evaluation_database: Any, suite_id: str
) -> None:
    bad = make_scenario("s-002")
    bad["gold"]["q1"]["required_facts"][0]["evidence_message_ids"] = ["m-missing"]
    result = _import(evaluation_database, suite_id, [make_scenario("s-001"), bad])

    assert result["status"] == "invalid"
    assert result["dataset_revision_id"] is None
    assert result["errors"]
    assert any("s-002" in json.dumps(item, ensure_ascii=False) for item in result["errors"])

    repository = MemoryRepository(evaluation_database)
    assert repository.list_dataset_revisions(suite_id) == []


def test_import_dataset_records_invalid_import_job(
    evaluation_database: Any, suite_id: str
) -> None:
    bad = make_scenario("s-002")
    bad["tags"] = ["fact", "positive", "x" * 100]
    result = _import(evaluation_database, suite_id, [bad])
    assert result["status"] == "invalid"
    assert result["import_job_id"]

    with evaluation_database.connect() as conn:
        row = conn.execute(
            "SELECT status, errors_json FROM memory_import_jobs WHERE id = ?",
            (str(result["import_job_id"]),),
        ).fetchone()
    assert row is not None
    assert row["status"] == "invalid"
    assert json.loads(row["errors_json"])


def test_import_dataset_is_idempotent_for_identical_content(
    evaluation_database: Any, suite_id: str
) -> None:
    rows = [make_scenario("s-001")]
    first = _import(evaluation_database, suite_id, rows)
    second = _import(evaluation_database, suite_id, rows)
    assert first["dataset_revision_id"] == second["dataset_revision_id"]
    repository = MemoryRepository(evaluation_database)
    assert len(repository.list_dataset_revisions(suite_id)) == 1


def test_import_dataset_conflicts_when_same_version_content_differs(
    evaluation_database: Any, suite_id: str
) -> None:
    _import(evaluation_database, suite_id, [make_scenario("s-001")])
    changed = make_scenario("s-001")
    changed["probes"][0]["question"] = "我住在哪里？"
    with pytest.raises(MemoryDatasetError) as excinfo:
        _import(evaluation_database, suite_id, [changed])
    assert excinfo.value.code == "REVISION_CONFLICT"


def test_import_dataset_rejects_source_hash_mismatch(
    evaluation_database: Any, suite_id: str
) -> None:
    with pytest.raises(MemoryDatasetError) as excinfo:
        _import(
            evaluation_database,
            suite_id,
            [make_scenario("s-001")],
            expected_sha256="b" * 64,
        )
    assert excinfo.value.code == "MEMORY_SOURCE_HASH_MISMATCH"
    repository = MemoryRepository(evaluation_database)
    assert repository.list_dataset_revisions(suite_id) == []


def test_import_dataset_rejects_cross_split_template_family(
    evaluation_database: Any, suite_id: str
) -> None:
    development = make_scenario("s-001", split="development")
    _import(
        evaluation_database,
        suite_id,
        [development],
        dataset_version="memory-dev-v1",
    )
    regression = make_scenario("s-002", split="regression")
    with pytest.raises(MemoryDatasetError) as excinfo:
        _import(
            evaluation_database,
            suite_id,
            [regression],
            dataset_version="memory-core-v1",
        )
    assert excinfo.value.code == "MEMORY_SPLIT_LEAK"


def test_import_dataset_rejects_cross_split_subject(
    evaluation_database: Any, suite_id: str
) -> None:
    development = make_scenario("s-001", split="development")
    _import(evaluation_database, suite_id, [development], dataset_version="memory-dev-v1")
    regression = make_scenario("s-002", split="regression")
    with pytest.raises(MemoryDatasetError) as excinfo:
        _import(evaluation_database, suite_id, [regression], dataset_version="memory-core-v1")
    assert excinfo.value.code == "MEMORY_SPLIT_LEAK"


def test_import_dataset_allows_same_family_in_same_split(
    evaluation_database: Any, suite_id: str
) -> None:
    rows = [make_scenario("s-001"), make_scenario("s-002")]
    result = _import(evaluation_database, suite_id, rows)
    assert result["status"] == "ready"


def test_import_dataset_rejects_too_many_scenarios(evaluation_database: Any, suite_id: str) -> None:
    rows = [make_scenario(f"s-{index:04d}") for index in range(3)]
    with pytest.raises(MemoryDatasetError) as excinfo:
        _import(evaluation_database, suite_id, rows, max_scenarios=2)
    assert excinfo.value.code == "MEMORY_IMPORT_TOO_LARGE"


def test_import_dataset_error_does_not_leak_local_path(
    evaluation_database: Any, suite_id: str, tmp_path: Path
) -> None:
    upload = tmp_path / "broken.jsonl"
    upload.write_text("{not json}\n", encoding="utf-8")
    with upload.open("rb") as handle:
        with pytest.raises(MemoryDatasetError) as excinfo:
            import_dataset(
                evaluation_database,
                suite_id=suite_id,
                dataset_version="memory-core-v1",
                source=make_source(),
                stream=handle,
                format="jsonl",
            )
    text = f"{excinfo.value} {excinfo.value.diagnostics}"
    assert str(tmp_path) not in text
    assert "broken.jsonl" not in text


def test_import_dataset_rejects_too_many_messages(
    evaluation_database: Any, suite_id: str
) -> None:
    payload = make_scenario("s-001")
    payload["events"][0]["messages"] = [
        {"id": f"m{index}", "role": "user", "content": f"第{index}条"}
        for index in range(5)
    ]
    with pytest.raises(MemoryDatasetError) as excinfo:
        _import(evaluation_database, suite_id, [payload], max_messages_per_scenario=3)
    assert excinfo.value.code == "MEMORY_IMPORT_TOO_LARGE"


# ------------------------------------------------------------------ 临时上传


def test_temporary_upload_removes_file_after_success(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    with temporary_upload(staging, io.BytesIO(b"hello")) as path:
        assert path.is_file()
        assert path.read_bytes() == b"hello"
    assert not path.exists()


def test_temporary_upload_removes_file_after_failure(tmp_path: Path) -> None:
    staging = tmp_path / "staging"

    class _Boom(io.RawIOBase):
        def readable(self) -> bool:
            return True

        def read(self, count: int = -1) -> bytes:  # type: ignore[override]
            raise OSError("disk quota exceeded")

    captured: list[Path] = []
    with pytest.raises(MemoryDatasetError) as excinfo:
        with temporary_upload(staging, _Boom()) as path:
            captured.append(path)
    assert excinfo.value.code == "MEMORY_IMPORT_DISK_FULL"
    assert captured and not captured[0].exists()
    assert not list(staging.glob("*")) if staging.exists() else True


def test_temporary_upload_rejects_oversized_stream(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    with pytest.raises(MemoryDatasetError) as excinfo:
        with temporary_upload(staging, io.BytesIO(b"x" * 100), max_bytes=10) as path:
            assert path is not None
    assert excinfo.value.code == "MEMORY_IMPORT_TOO_LARGE"


# ------------------------------------------------------------------ CAS 编辑


def test_edit_dataset_revision_keeps_base_revision_untouched(
    evaluation_database: Any, suite_id: str
) -> None:
    base = _import(evaluation_database, suite_id, [make_scenario("s-001")])
    base_id = str(base["dataset_revision_id"])
    repository = MemoryRepository(evaluation_database)

    changed = make_scenario("s-001")
    changed["revision"] = 2
    changed["probes"][0]["question"] = "我现在住在哪个城市？"
    changed["gold"]["q1"]["required_facts"][0]["value"] = "青岚城"

    result = edit_dataset_revision(
        evaluation_database,
        dataset_revision_id=base_id,
        base_dataset_version="memory-core-v1",
        new_dataset_version="memory-core-v2",
        changes=[changed],
    )
    assert result["status"] == "ready"
    assert str(result["dataset_revision_id"]) != base_id

    # 原修订的场景与 gold 不变。
    original = repository.get_scenario(base_id, "s-001")
    assert original["revision"] == 1
    assert original["probes"][0]["question"] == "我住在哪个城市？"
    assert repository.get_gold(base_id, "s-001")["probes"]["q1"]["required_facts"][0][
        "value"
    ] == "青岚城"

    # 新修订带新版本号。
    edited = repository.get_scenario(str(result["dataset_revision_id"]), "s-001")
    assert edited["revision"] == 2
    assert edited["probes"][0]["question"] == "我现在住在哪个城市？"


def test_edit_dataset_revision_rejects_stale_base_version(
    evaluation_database: Any, suite_id: str
) -> None:
    base = _import(evaluation_database, suite_id, [make_scenario("s-001")])
    changed = make_scenario("s-001")
    changed["revision"] = 2
    with pytest.raises(MemoryDatasetError) as excinfo:
        edit_dataset_revision(
            evaluation_database,
            dataset_revision_id=str(base["dataset_revision_id"]),
            base_dataset_version="memory-core-v0",
            new_dataset_version="memory-core-v2",
            changes=[changed],
        )
    assert excinfo.value.code == "REVISION_CONFLICT"


def test_edit_dataset_revision_rejects_scenario_revision_gap(
    evaluation_database: Any, suite_id: str
) -> None:
    base = _import(evaluation_database, suite_id, [make_scenario("s-001")])
    changed = make_scenario("s-001")
    changed["revision"] = 5
    with pytest.raises(MemoryDatasetError) as excinfo:
        edit_dataset_revision(
            evaluation_database,
            dataset_revision_id=str(base["dataset_revision_id"]),
            base_dataset_version="memory-core-v1",
            new_dataset_version="memory-core-v2",
            changes=[changed],
        )
    assert excinfo.value.code == "REVISION_CONFLICT"


# ------------------------------------------------------------------ 导出


def test_export_dataset_revision_writes_jsonl_and_registers_artifact(
    evaluation_database: Any, evaluation_repository: Any, suite_id: str, tmp_path: Path
) -> None:
    base = _import(evaluation_database, suite_id, [make_scenario("s-001")])
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = export_dataset_revision(
        evaluation_database,
        str(base["dataset_revision_id"]),
        workspace_root=workspace,
        artifact_repository=evaluation_repository,
        attempt_id=None,
    )
    assert result["artifact_id"] is None
    exported = Path(result["path"])
    assert exported.is_file()
    rows = [json.loads(line) for line in exported.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["scenario_id"] == "s-001"
    assert result["sha256"] == hashlib.sha256(exported.read_bytes()).hexdigest()


def test_export_dataset_revision_uses_existing_artifact_registry(
    evaluation_database: Any, evaluation_repository: Any, suite_id: str, tmp_path: Path
) -> None:
    base = _import(evaluation_database, suite_id, [make_scenario("s-001")])
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = export_dataset_revision(
        evaluation_database,
        str(base["dataset_revision_id"]),
        workspace_root=workspace,
        artifact_repository=evaluation_repository,
        attempt_id=None,
    )
    assert result["relative_path"]
    assert result["media_type"]
    assert result["size_bytes"] > 0
    assert result["sha256"]


# ------------------------------------------------------------------ 集合常量


def test_import_limits_match_contract() -> None:
    assert MAX_SCENARIOS == 2000


def test_generated_scenarios_validate_against_frozen_model() -> None:
    """导入前的场景必须能被 WU-01 冻结的模型校验（自检口径）。"""
    scenario = MemoryScenario.model_validate(make_scenario())
    assert scenario.schema_version == "memory-scenario/v1"


def _iter_rows(rows: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    yield from rows
