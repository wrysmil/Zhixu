"""D07：逐条 fallback 收敛为按批次幂等重试。

覆盖：
1. 写入失败不再落 ``_memory_fallback`` 单条 JSON，而是返回结构化失败回执；
2. 失败批次保留在 ``memory_extraction_batches``，修复后按同一 batch_id 重试；
3. 升级前遗留的 fallback JSON 仍可导入（一次性兼容，不是新的重放路径）。

模型用 ``tests/memory/test_extractor.py`` 的可编程 Fake，数据库用真 SQLite。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import (
    Episode,
    EpisodeSource,
    Memory,
    MemoryType,
)
from nanobot.session.manager import Session
from tests.memory.test_extractor import (
    _FakeProvider,
    _make_extractor,
    _plain_session,
    _semantic_json,
)

_SEMANTIC_ONE = _semantic_json(
    memories=[
        {
            "content": "用户偏好使用 uv 管理依赖",
            "type": "PREFERENCE",
            "priority": "long_term",
            "importance": 0.8,
        }
    ]
)


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    """每个测试一个 fresh database,避免 state 污染。"""
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


# ---------------------------------------------------------------------------
# A 节：写入失败不再降级为单条 fallback
# ---------------------------------------------------------------------------


class TestFallbackRemoved:
    def test_extractor_has_no_per_item_fallback_path(self, db: MemoryDatabase):
        extractor = _make_extractor(db, _FakeProvider())
        assert not hasattr(extractor, "_safe_write_with_fallback"), (
            "D07：逐条 fallback 写入路径必须删除"
        )

    async def test_write_failure_surfaces_as_failed_batch(
        self,
        db: MemoryDatabase,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import nanobot.memory.writer as writer_module

        provider = _FakeProvider(
            semantic=_SEMANTIC_ONE, episode=json.dumps({"summary": "s"})
        )
        extractor = _make_extractor(db, provider)

        def _boom(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("simulated db failure")

        monkeypatch.setattr(writer_module, "add_memory", _boom)
        result = await extractor.extract_session(_plain_session())

        assert result.memory_ids == []
        assert result.failed_tracks, "写入失败必须明确报告，不能静默吞错"
        assert list(db.fallback_dir.glob("*.json")) == [], (
            "D07 之后写入失败不得再落单条 fallback 文件"
        )
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT status, error_code FROM memory_extraction_batches"
            ).fetchall()
        assert [(row[0], row[1]) for row in rows] == [("failed", "PERSISTENCE_FAILED")]

    async def test_failed_batch_retries_with_same_id(
        self,
        db: MemoryDatabase,
        monkeypatch: pytest.MonkeyPatch,
    ):
        import nanobot.memory.writer as writer_module

        provider = _FakeProvider(
            semantic=_SEMANTIC_ONE, episode=json.dumps({"summary": "s"})
        )
        extractor = _make_extractor(db, provider)
        original = writer_module.add_memory
        # 带持久坐标的会话：同一 session_key + generation 才能在失败后按批次重试。
        session = Session(
            key="websocket:fallback",
            messages=[{"role": "user", "content": "以后都用 uv", "memory_seq": 1}],
        )
        session.memory_generation = "gen-fallback"
        session.memory_next_seq = 2

        def _boom(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("simulated db failure")

        monkeypatch.setattr(writer_module, "add_memory", _boom)
        first = await extractor.extract_session(session)
        monkeypatch.setattr(writer_module, "add_memory", original)

        with db.connect() as conn:
            failed_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT batch_id FROM memory_extraction_batches"
                ).fetchall()
            ]
        second = await extractor.extract_session(session)

        assert first.memory_ids == []
        assert len(second.memory_ids) == 1, "修复后按原批次重试必须写入"
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT batch_id, status FROM memory_extraction_batches"
            ).fetchall()
        assert [row[0] for row in rows] == failed_ids, "重试不得创建新 batch_id"
        assert rows[0][1] == "completed"


# ---------------------------------------------------------------------------
# B 节：旧 fallback 数据可导入（一次性兼容）
# ---------------------------------------------------------------------------


def _episode_row_dict() -> dict[str, Any]:
    return Episode(
        id="fb-episode-1",
        session_id="s1",
        summary="fb ep",
        goal="",
        source=EpisodeSource.SESSION_END,
        started_at="2026-09-13T00:00:00+00:00",
        ended_at="2026-09-13T00:00:01+00:00",
    ).to_row()


class TestLegacyFallbackImport:
    def test_replay_drains_legacy_files(self, tmp_path: Path):
        """旧 fallback 文件被导入后删除，DB 中有对应行。"""
        db = MemoryDatabase(tmp_path)
        db.init_schema()

        for i, content in enumerate(["alpha", "beta"]):
            payload = {
                "kind": "memory",
                "item": Memory(
                    id=f"fb-mem-{i}",
                    content=content,
                    type=MemoryType.FACT,
                    created_at="2026-09-13T00:00:00+00:00",
                    workspace_id="default",
                ).to_row(),
                "attempt": f"2026-09-13T00:00:0{i}+00:00",
                "error": "simulated",
            }
            (db.fallback_dir / f"2026-09-13T00-00-0{i}_memory_{i:08x}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

        episode_payload = {
            "kind": "episode",
            "item": _episode_row_dict(),
            "attempt": "2026-09-13T00:00:02+00:00",
            "error": "simulated",
        }
        (db.fallback_dir / "2026-09-13T00-00-02_episode_99999999.json").write_text(
            json.dumps(episode_payload, ensure_ascii=False), encoding="utf-8"
        )

        imported = db.replay_fallback()
        assert imported == 3
        assert list(db.fallback_dir.glob("*.json")) == []

        with db.connect() as conn:
            from nanobot.memory.repository import get_episode, list_memories

            mems = list_memories(conn, workspace_id="default")
            episode = get_episode(conn, "fb-episode-1")
        assert {memory.content for memory in mems} == {"alpha", "beta"}
        assert episode is not None
        assert episode.summary == "fb ep"

    def test_import_keeps_invalid_files(self, tmp_path: Path):
        """导入失败的文件保留，成功的被清理。"""
        db = MemoryDatabase(tmp_path)
        db.init_schema()

        good_payload = {
            "kind": "memory",
            "item": Memory(
                id="good-id",
                content="good",
                type=MemoryType.FACT,
                created_at="2026-09-13T00:00:00+00:00",
                workspace_id="default",
            ).to_row(),
            "attempt": "2026-09-13T00:00:00+00:00",
            "error": "simulated",
        }
        bad_payload = {
            "kind": "memory",
            "item": {"id": "bad-id", "content": "bad"},  # 缺字段 → 反构失败
            "attempt": "2026-09-13T00:00:01+00:00",
            "error": "simulated",
        }
        (db.fallback_dir / "2026-09-13T00-00-00_memory_aaaaaaaa.json").write_text(
            json.dumps(good_payload, ensure_ascii=False), encoding="utf-8"
        )
        (db.fallback_dir / "2026-09-13T00-00-01_memory_bbbbbbbb.json").write_text(
            json.dumps(bad_payload, ensure_ascii=False), encoding="utf-8"
        )

        imported = db.replay_fallback()
        assert imported == 1

        remaining = sorted(path.name for path in db.fallback_dir.glob("*.json"))
        assert remaining == ["2026-09-13T00-00-01_memory_bbbbbbbb.json"]

        from nanobot.memory.repository import list_memories

        with db.connect() as conn:
            mems = list_memories(conn, workspace_id="default")
        assert [memory.id for memory in mems] == ["good-id"]


class TestDatabaseInitImportsLegacy:
    def test_database_init_imports_pending_file(self, tmp_path: Path):
        """``MemoryDatabase.__init__`` 仍会导入升级前遗留的 fallback 文件。"""
        db_setup = MemoryDatabase(tmp_path)
        db_setup.init_schema()

        payload = {
            "kind": "memory",
            "item": Memory(
                id="init-replay-db",
                content="replayed-on-db-init",
                type=MemoryType.FACT,
                created_at="2026-09-13T00:00:00+00:00",
                workspace_id="default",
            ).to_row(),
            "attempt": "2026-09-13T00:00:00+00:00",
            "error": "prior failure",
        }
        (db_setup.fallback_dir / "2026-09-13T00-00-00_memory_initdb01.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

        db2 = MemoryDatabase(tmp_path)

        assert list(db2.fallback_dir.glob("*.json")) == []
        from nanobot.memory.repository import list_memories

        with db2.connect() as conn:
            mems = list_memories(conn, workspace_id="default")
        assert any(memory.content == "replayed-on-db-init" for memory in mems)

    def test_database_init_import_failure_does_not_raise(self, tmp_path: Path):
        db_setup = MemoryDatabase(tmp_path)
        db_setup.init_schema()

        (db_setup.fallback_dir / "2026-09-13T00-00-00_memory_bad0001.json").write_text(
            json.dumps(
                {"kind": "memory", "attempt": "2026-09-13T00:00:00+00:00"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        db = MemoryDatabase(tmp_path)
        assert isinstance(db, MemoryDatabase)
        remaining = list(db.fallback_dir.glob("*.json"))
        assert len(remaining) == 1
