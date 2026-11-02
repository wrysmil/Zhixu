"""持久消息坐标（契约 §1）：generation / next_seq / memory_seq。

覆盖：首次保存分配、旧 session 补齐、重复加载不重编号、冲突必须报告、
fork 与清空换 generation、压缩不换 generation 且不降低 next_seq、
provider 请求剥离身份字段、metadata 原子更新不丢坐标。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from nanobot.session.manager import MemoryCoordinateError, SessionManager
from nanobot.session.summary import SUMMARY_CONTINUATION_TEXT


@pytest.fixture()
def session_roots(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "workspace", tmp_path / "sessions"


@pytest.fixture()
def manager(session_roots: tuple[Path, Path]) -> SessionManager:
    return _new_manager(session_roots)


def _new_manager(roots: tuple[Path, Path]) -> SessionManager:
    workspace, sessions_root = roots
    return SessionManager(workspace, sessions_root=sessions_root)


def _session_path(manager: SessionManager, key: str) -> Path:
    return manager._get_session_path(key)  # pyright: ignore[reportPrivateUsage]


def _records(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _metadata_record(records: list[dict[str, Any]]) -> dict[str, Any]:
    first = records[0]
    assert first.get("_type") == "metadata"
    return first


def _message_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if record.get("_type") is None]


def _write_legacy_session(
    manager: SessionManager,
    key: str,
    contents: list[str],
    *,
    roles: list[str] | None = None,
) -> Path:
    """写入升级前格式的 session 文件：metadata 记录无坐标，消息无 memory_seq。"""
    now = datetime.now().isoformat()
    records: list[dict[str, Any]] = [
        {
            "_type": "metadata",
            "key": key,
            "created_at": now,
            "updated_at": now,
            "metadata": {},
            "last_archived": 0,
            "last_consolidated": 0,
        }
    ]
    for index, content in enumerate(contents):
        role = roles[index] if roles is not None else ("user" if index % 2 == 0 else "assistant")
        records.append({"role": role, "content": content, "timestamp": now})
    path = _session_path(manager, key)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def _sequences(messages: list[dict[str, Any]]) -> list[int]:
    return [message["memory_seq"] for message in messages]


class TestFirstSave:
    def test_save_assigns_generation_and_sequences(self, manager: SessionManager) -> None:
        session = manager.get_or_create("test:coords")
        assert session.memory_generation == ""
        assert session.memory_next_seq == 0
        for index in range(3):
            session.add_message("user", f"m{index}")

        manager.save(session)

        assert len(session.memory_generation) == 32
        assert session.memory_next_seq == 4
        assert _sequences(session.messages) == [1, 2, 3]
        # 身份字段只做持久化/记忆消费，不进入用户可见的 session.metadata。
        assert "memory_generation" not in session.metadata
        assert "memory_next_seq" not in session.metadata

        metadata = _metadata_record(_records(_session_path(manager, "test:coords")))
        assert metadata["memory_generation"] == session.memory_generation
        assert metadata["memory_next_seq"] == 4
        assert _sequences(_message_records(_records(_session_path(manager, "test:coords")))) == [
            1,
            2,
            3,
        ]

    def test_messages_added_after_save_continue_the_sequence(
        self, manager: SessionManager
    ) -> None:
        session = manager.get_or_create("test:append")
        session.add_message("user", "first")
        manager.save(session)
        generation = session.memory_generation

        session.add_message("assistant", "second")
        manager.save(session)

        assert session.memory_generation == generation
        assert session.memory_next_seq == 3
        assert _sequences(session.messages) == [1, 2]

    def test_empty_session_gets_generation_on_first_save(
        self, manager: SessionManager
    ) -> None:
        session = manager.get_or_create("test:empty")
        manager.save(session)

        assert len(session.memory_generation) == 32
        assert session.memory_next_seq == 1
        metadata = _metadata_record(_records(_session_path(manager, "test:empty")))
        assert metadata["memory_next_seq"] == 1


class TestLegacyBackfill:
    def test_legacy_session_is_backfilled_in_original_order(
        self, manager: SessionManager
    ) -> None:
        key = "test:legacy"
        _write_legacy_session(manager, key, ["m0", "m1", "m2"])

        session = manager.get_or_create(key)
        assert session.memory_generation == ""
        assert session.memory_next_seq == 0
        assert [message["content"] for message in session.messages] == ["m0", "m1", "m2"]
        assert all("memory_seq" not in message for message in session.messages)

        manager.save(session)

        assert [message["content"] for message in session.messages] == ["m0", "m1", "m2"]
        assert [message["role"] for message in session.messages] == [
            "user",
            "assistant",
            "user",
        ]
        assert _sequences(session.messages) == [1, 2, 3]
        assert session.memory_next_seq == 4

        metadata = _metadata_record(_records(_session_path(manager, key)))
        assert metadata["memory_generation"] == session.memory_generation
        assert metadata["memory_next_seq"] == 4

    def test_reload_does_not_renumber(
        self, manager: SessionManager, session_roots: tuple[Path, Path]
    ) -> None:
        key = "test:stable"
        session = manager.get_or_create(key)
        session.add_message("user", "a")
        session.add_message("assistant", "b")
        manager.save(session)
        generation = session.memory_generation
        first_run = _sequences(session.messages)

        reloaded = _new_manager(session_roots).get_or_create(key)
        assert reloaded.memory_generation == generation
        assert _sequences(reloaded.messages) == first_run

        reloaded.add_message("user", "c")
        _new_manager(session_roots).save(reloaded)

        final = _new_manager(session_roots).get_or_create(key)
        assert final.memory_generation == generation
        assert _sequences(final.messages) == [*first_run, 3]
        assert final.memory_next_seq == 4

    def test_legacy_partial_tail_is_numbered_after_existing(
        self, manager: SessionManager
    ) -> None:
        key = "test:partial"
        path = _write_legacy_session(manager, key, ["m0", "m1"])
        records = _records(path)
        records[0]["memory_generation"] = "gen-1"
        records[0]["memory_next_seq"] = 3
        records[1]["memory_seq"] = 1
        records[2]["memory_seq"] = 2
        path.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )

        session = manager.get_or_create(key)
        session.add_message("user", "m2")
        manager.save(session)

        assert session.memory_generation == "gen-1"
        assert _sequences(session.messages) == [1, 2, 3]
        assert session.memory_next_seq == 4


class TestConflictsAreReported:
    def test_duplicate_sequence_is_reported(self, manager: SessionManager) -> None:
        key = "test:duplicate"
        session = manager.get_or_create(key)
        session.add_message("user", "a")
        session.add_message("assistant", "b")
        manager.save(session)
        path = _session_path(manager, key)
        before = path.read_bytes()

        session.messages[1]["memory_seq"] = 1
        with pytest.raises(MemoryCoordinateError) as excinfo:
            manager.save(session)

        assert "duplicate memory_seq 1" in str(excinfo.value)
        assert path.read_bytes() == before, "冲突时不得写出部分文件"

    def test_invalid_message_sequence_is_reported(self, manager: SessionManager) -> None:
        session = manager.get_or_create("test:invalid-seq")
        session.add_message("user", "a")
        manager.save(session)

        session.messages[0]["memory_seq"] = "1"
        with pytest.raises(MemoryCoordinateError, match="memory_seq is invalid"):
            manager.save(session)

    def test_zero_sequence_is_reported(self, manager: SessionManager) -> None:
        session = manager.get_or_create("test:zero-seq")
        session.add_message("user", "a")
        manager.save(session)

        session.messages[0]["memory_seq"] = 0
        with pytest.raises(MemoryCoordinateError, match="memory_seq is invalid"):
            manager.save(session)

    def test_next_seq_below_high_watermark_is_reported(
        self, manager: SessionManager
    ) -> None:
        session = manager.get_or_create("test:next-seq")
        session.add_message("user", "a")
        session.add_message("assistant", "b")
        manager.save(session)

        session.memory_next_seq = 2
        with pytest.raises(MemoryCoordinateError, match="does not exceed"):
            manager.save(session)

    def test_invalid_generation_is_reported(self, manager: SessionManager) -> None:
        session = manager.get_or_create("test:bad-generation")
        session.add_message("user", "a")
        manager.save(session)

        session.memory_generation = 5  # type: ignore[assignment]
        with pytest.raises(MemoryCoordinateError, match="memory_generation is invalid"):
            manager.save(session)

    def test_corrupt_coordinates_survive_load_and_are_reported_on_save(
        self, manager: SessionManager, session_roots: tuple[Path, Path]
    ) -> None:
        key = "test:corrupt"
        path = _write_legacy_session(manager, key, ["m0", "m1"])
        records = _records(path)
        records[0]["memory_generation"] = 7
        records[0]["memory_next_seq"] = "9"
        path.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )

        session = _new_manager(session_roots).get_or_create(key)
        # 加载不静默覆盖损坏值，保存时才报告。
        with pytest.raises(MemoryCoordinateError, match="memory_generation is invalid"):
            _new_manager(session_roots).save(session)


class TestGenerationLifecycle:
    def test_fork_creates_new_generation(self, manager: SessionManager) -> None:
        source = manager.get_or_create("test:source")
        for index in range(3):
            source.add_message("user", f"m{index}")
        manager.save(source)
        source_generation = source.memory_generation

        target = manager.fork_session_before_user_index("test:source", "test:target", 2)

        assert target is not None
        assert target.memory_generation != source_generation
        assert len(target.memory_generation) == 32
        assert [message["content"] for message in target.messages] == ["m0", "m1"]
        assert _sequences(target.messages) == [1, 2]
        assert target.memory_next_seq == 3

        target_metadata = _metadata_record(_records(_session_path(manager, "test:target")))
        assert target_metadata["memory_generation"] == target.memory_generation
        source_metadata = _metadata_record(_records(_session_path(manager, "test:source")))
        assert source_metadata["memory_generation"] == source_generation

    def test_clear_starts_new_generation(self, manager: SessionManager) -> None:
        session = manager.get_or_create("test:clear")
        session.add_message("user", "a")
        session.add_message("assistant", "b")
        manager.save(session)
        previous_generation = session.memory_generation

        session.clear()
        manager.save(session)

        assert session.memory_generation != previous_generation
        assert session.memory_next_seq == 1
        assert session.messages == []
        path = _session_path(manager, "test:clear")
        metadata = _metadata_record(_records(path))
        assert metadata["memory_generation"] == session.memory_generation
        assert metadata["memory_next_seq"] == 1
        assert _message_records(_records(path)) == []

    def test_compaction_keeps_generation_and_does_not_lower_next_seq(
        self, manager: SessionManager
    ) -> None:
        session = manager.get_or_create("test:compact")
        for index in range(3):
            session.add_message("user", f"m{index}")
        manager.save(session)
        generation = session.memory_generation
        assert session.memory_next_seq == 4

        # 模拟普通压缩：在最前面插入隐藏 summary 边界，并丢弃被替换的尾部消息。
        session.messages.insert(
            0,
            {
                "role": "user",
                "content": SUMMARY_CONTINUATION_TEXT,
                "_hidden": True,
                "timestamp": datetime.now().isoformat(),
            },
        )
        session.messages.pop()
        manager.save(session)

        assert session.memory_generation == generation
        assert session.memory_next_seq == 5
        assert session.messages[0]["memory_seq"] == 4
        assert _sequences(session.messages[1:]) == [1, 2]


class TestProviderProjection:
    def test_history_strips_memory_coordinates(self, manager: SessionManager) -> None:
        session = manager.get_or_create("test:history")
        session.add_message("user", "hello")
        session.add_message(
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "noop", "arguments": "{}"},
                }
            ],
        )
        manager.save(session)
        assert all("memory_seq" in message for message in session.messages)

        history = session.get_history(max_messages=0)

        assert [entry["role"] for entry in history] == ["user", "assistant"]
        assert history[0]["content"] == "hello"
        assert history[1]["tool_calls"][0]["id"] == "call-1"
        assert all("memory_seq" not in entry for entry in history)


class TestMetadataUpdate:
    def test_update_metadata_preserves_coordinates(
        self, manager: SessionManager, session_roots: tuple[Path, Path]
    ) -> None:
        key = "test:metadata"
        session = manager.get_or_create(key)
        session.add_message("user", "a")
        manager.save(session)
        generation = session.memory_generation
        next_seq = session.memory_next_seq

        assert manager.update_session_metadata(key, {"title": "标题"}) is True

        metadata = _metadata_record(_records(_session_path(manager, key)))
        assert metadata["metadata"]["title"] == "标题"
        assert metadata["memory_generation"] == generation
        assert metadata["memory_next_seq"] == next_seq

        reloaded = _new_manager(session_roots).get_or_create(key)
        assert reloaded.memory_generation == generation
        assert reloaded.memory_next_seq == next_seq
        assert _sequences(reloaded.messages) == [1]
