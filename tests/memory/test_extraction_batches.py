"""批次协议（契约 §2）行为测试：``MemoryExtractor.process_request``。

覆盖：
- 批次幂等：同一上界重复请求返回既有结果，不新建批次、不再调模型；
- 续跑：已完成的批次不重复消费，新上界在与上一批相邻的区间内处理；
- 单轨失败：另一轨 checkpoint 保留，重试只重跑失败轨；
- 事务回滚：事实/情节/last_seq 同事务，任一步失败整批回滚且可重试；
- 空成功：有效空输出（空数组 / ``NONE``）算完成并推进 last_seq；
- 错误文本不算空成功：provider 错误响应返回 failed，last_seq 不动；
- 载荷漂移：同一 batch_id 的证据摘要变化报 ``BATCH_PAYLOAD_MISMATCH``；
- 代不匹配 / 会话不可用：返回结构化错误码。

模型用可编程 Fake（鸭子类型），数据库用真 SQLite(tmp_path)。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.memory.database import (
    MemoryDatabase,
    get_extraction_batch,
    list_incomplete_extraction_batches,
)
from nanobot.memory.extractor import (
    MemoryExtractor,
    _evidence_digest,
    _stable_batch_id,
)
from nanobot.memory.models import ExtractionRequest
from nanobot.memory.repository import get_extraction_seq, list_memories
from nanobot.session.manager import Session

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, content: str | None, *, finish_reason: str = "stop") -> None:
        self.content = content
        self.finish_reason = finish_reason


class _ErrorResponse:
    """复刻 provider 的 HTTP 错误响应（错误文本包在 content 里）。"""

    def __init__(self, body: str = "Error: {'type': 'permission_error'}") -> None:
        self.content = body
        self.finish_reason = "error"
        self.error_status_code = 403
        self.error_kind = "authentication"
        self.error_type = "permission_error"
        self.error_code = None


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 1024
    reasoning_effort = None


class _FakeProvider:
    """按 prompt 标记分流 semantic / episode；支持抛错与错误响应。"""

    def __init__(
        self,
        *,
        semantic: Any = None,
        episode: Any = None,
        error_response: bool = False,
    ) -> None:
        self.semantic = semantic
        self.episode = episode
        self.error_response = error_response
        self.tracks: list[str] = []

    async def chat_with_retry(
        self, *, messages: list[dict[str, Any]], **_kwargs: Any
    ) -> Any:
        body = " ".join(
            m.get("content", "") for m in messages if isinstance(m.get("content"), str)
        )
        is_semantic = "语义记忆抽取" in body
        self.tracks.append("semantic" if is_semantic else "episode")
        if self.error_response:
            return _ErrorResponse()
        value = self.semantic if is_semantic else self.episode
        if isinstance(value, BaseException):
            raise value
        return _FakeResponse(value)


class _FakeRuntime:
    def __init__(self, provider: _FakeProvider) -> None:
        self.provider = provider
        self.model = "fake-model"
        self.generation = _FakeGeneration()
        self.context_window_tokens = 8192


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

SEMANTIC_ONE = json.dumps(
    {
        "memories": [
            {
                "content": "用户偏好使用 uv 管理依赖",
                "evidence": "以后都用 uv 管理依赖",
                "type": "PREFERENCE",
                "priority": "long_term",
                "subject": "用户",
                "predicate": "偏好依赖管理",
                "importance": 0.8,
            }
        ],
        "experiences": [],
    },
    ensure_ascii=False,
)
EPISODE_ONE = json.dumps(
    {
        "summary": "用户配置了 uv 依赖管理",
        "goal": "配置 uv",
        "outcome": "completed",
        "entities": ["uv"],
        "tools_used": ["bash"],
    },
    ensure_ascii=False,
)


def _coordinated_session(
    key: str = "websocket:batch",
    *,
    generation: str = "gen-1",
    messages: list[dict[str, Any]] | None = None,
) -> Session:
    """构造带持久坐标的会话（等同 ``JsonlSessionStore`` 保存后的形状）。"""
    payload = messages or [
        {"role": "user", "content": "以后都用 uv 管理依赖"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "记一下：必须每次保存日志"},
        {"role": "assistant", "content": "收到"},
    ]
    numbered = []
    for index, message in enumerate(payload, start=1):
        entry = dict(message)
        entry.setdefault("memory_seq", index)
        numbered.append(entry)
    session = Session(key=key, messages=numbered)
    session.memory_generation = generation
    session.memory_next_seq = len(numbered) + 1
    return session


def _request(
    session: Session,
    through_seq: int,
    *,
    reason: str = "idle",
    generation: str | None = None,
) -> ExtractionRequest:
    return ExtractionRequest(
        workspace_id="default",
        session_key=session.key,
        generation=generation if generation is not None else session.memory_generation,
        through_seq=through_seq,
        reason=reason,
    )


def _make_extractor(db: MemoryDatabase, provider: _FakeProvider) -> MemoryExtractor:
    return MemoryExtractor(
        db,
        _FakeRuntime(provider),
        session_loader=lambda _key: None,
    )


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


def _batch_rows(conn: Any) -> list[tuple[str, int, int, str, str]]:
    return [
        (row[0], row[1], row[2], row[3], row[4])
        for row in conn.execute(
            "SELECT batch_id, start_seq, end_seq, status, error_code "
            "FROM memory_extraction_batches ORDER BY start_seq"
        ).fetchall()
    ]


def _memory_contents(db: MemoryDatabase) -> list[str]:
    with db.connect() as conn:
        return [memory.content for memory in list_memories(conn)]


def _last_seq(db: MemoryDatabase, session: Session) -> int:
    with db.connect() as conn:
        return get_extraction_seq(conn, session.key, session.memory_generation)


# ---------------------------------------------------------------------------
# batch_id / 证据摘要
# ---------------------------------------------------------------------------


class TestStableIdentity:
    def test_batch_id_is_stable_for_same_key_and_bounds(self):
        first = _stable_batch_id("ws", "s", "gen", 0, 4)
        assert first == _stable_batch_id("ws", "s", "gen", 0, 4)
        assert first != _stable_batch_id("ws", "s", "gen", 0, 5)
        assert first != _stable_batch_id("ws", "s", "other", 0, 4)
        assert len(first) == 32

    def test_evidence_digest_changes_with_content(self):
        message = {"role": "user", "content": "a", "memory_seq": 1}
        other = {"role": "user", "content": "b", "memory_seq": 1}
        assert _evidence_digest([(1, message)]) == _evidence_digest([(1, dict(message))])
        assert _evidence_digest([(1, message)]) != _evidence_digest([(1, other)])


# ---------------------------------------------------------------------------
# 幂等 / 续跑
# ---------------------------------------------------------------------------


class TestIdempotencyAndContinuation:
    async def test_repeated_request_returns_existing_result(self, db: MemoryDatabase):
        provider = _FakeProvider(semantic=SEMANTIC_ONE, episode=EPISODE_ONE)
        extractor = _make_extractor(db, provider)
        session = _coordinated_session()
        request = _request(session, 2)

        first = await extractor.process_request(request, session=session)
        assert first.status == "completed"
        assert len(first.memory_ids) == 1
        assert len(first.episode_ids) == 1
        assert provider.tracks == ["semantic", "episode"]

        calls_before = list(provider.tracks)
        second = await extractor.process_request(request, session=session)

        assert second.status == "completed"
        assert second.batch_id == first.batch_id
        assert second.memory_ids == first.memory_ids
        assert second.episode_ids == first.episode_ids
        assert provider.tracks == calls_before, "重复请求不得再次调用模型"

        with db.connect() as conn:
            rows = _batch_rows(conn)
        assert len(rows) == 1, "重复请求不得新建批次"

    async def test_new_upper_bound_consumes_only_next_range(self, db: MemoryDatabase):
        provider = _FakeProvider(semantic=SEMANTIC_ONE, episode=EPISODE_ONE)
        extractor = _make_extractor(db, provider)
        session = _coordinated_session()

        first = await extractor.process_request(_request(session, 2), session=session)
        second = await extractor.process_request(_request(session, 4), session=session)

        assert first.status == second.status == "completed"
        assert second.through_seq == 4
        with db.connect() as conn:
            rows = _batch_rows(conn)
        assert [(start, end) for _, start, end, _, _ in rows] == [(0, 2), (2, 4)]
        assert _last_seq(db, session) == 4
        # 两段抽到同一 subject+predicate → 合并为一行（不重复入库）。
        assert len(_memory_contents(db)) == 1

    async def test_resumes_incomplete_batch_before_new_upper_bound(
        self, db: MemoryDatabase
    ):
        provider = _FakeProvider(semantic=RuntimeError("llm down"), episode=EPISODE_ONE)
        extractor = _make_extractor(db, provider)
        session = _coordinated_session()

        partial = await extractor.process_request(_request(session, 2), session=session)
        assert partial.status == "partial"
        assert partial.error_code == "SEMANTIC_TRACK_FAILED"
        assert _last_seq(db, session) == 0

        provider.semantic = SEMANTIC_ONE
        resumed = await extractor.process_request(_request(session, 4), session=session)

        assert resumed.status == "completed"
        assert resumed.through_seq == 4
        # 第一段只重跑 semantic（episode 复用已 checkpoint 的输出），
        # 第二段（2, 4] 两轨各跑一次。
        assert provider.tracks == [
            "semantic",
            "episode",
            "semantic",
            "semantic",
            "episode",
        ]
        with db.connect() as conn:
            rows = _batch_rows(conn)
        assert [(start, end) for _, start, end, _, _ in rows] == [(0, 2), (2, 4)]
        assert _last_seq(db, session) == 4

    async def test_no_new_messages_returns_completed_without_llm(
        self, db: MemoryDatabase
    ):
        provider = _FakeProvider(semantic=SEMANTIC_ONE, episode=EPISODE_ONE)
        extractor = _make_extractor(db, provider)
        session = _coordinated_session()
        await extractor.process_request(_request(session, 2), session=session)

        calls = list(provider.tracks)
        receipt = await extractor.process_request(_request(session, 1), session=session)

        assert receipt.status == "completed"
        assert provider.tracks == calls


# ---------------------------------------------------------------------------
# 失败语义
# ---------------------------------------------------------------------------


class TestFailureSemantics:
    async def test_single_track_failure_does_not_commit_facts(
        self, db: MemoryDatabase
    ):
        provider = _FakeProvider(semantic=RuntimeError("semantic boom"), episode=EPISODE_ONE)
        extractor = _make_extractor(db, provider)
        session = _coordinated_session()

        receipt = await extractor.process_request(_request(session, 2), session=session)

        assert receipt.status == "partial"
        assert receipt.error_code == "SEMANTIC_TRACK_FAILED"
        assert receipt.memory_ids == ()
        assert receipt.episode_ids == ()
        assert _memory_contents(db) == []
        assert _last_seq(db, session) == 0

        with db.connect() as conn:
            row = get_extraction_batch(conn, receipt.batch_id)
            assert row is not None
            assert row.status == "partial"
            assert row.semantic_checkpoint_at == ""
            assert row.episode_checkpoint_at != ""

    async def test_both_tracks_failed_returns_failed(self, db: MemoryDatabase):
        provider = _FakeProvider(
            semantic=RuntimeError("a"), episode=RuntimeError("b")
        )
        extractor = _make_extractor(db, provider)
        session = _coordinated_session()

        receipt = await extractor.process_request(_request(session, 2), session=session)

        assert receipt.status == "failed"
        assert receipt.error_code == "SEMANTIC_TRACK_FAILED"
        assert _last_seq(db, session) == 0
        with db.connect() as conn:
            row = get_extraction_batch(conn, receipt.batch_id)
            assert row is not None
            assert "episode" in row.error_detail

    async def test_model_error_text_is_not_empty_success(self, db: MemoryDatabase):
        provider = _FakeProvider(error_response=True)
        extractor = _make_extractor(db, provider)
        session = _coordinated_session()

        receipt = await extractor.process_request(_request(session, 2), session=session)

        assert receipt.status == "failed"
        assert receipt.error_code is not None
        assert _last_seq(db, session) == 0, "错误响应不得推进进度"

    async def test_valid_empty_output_completes(self, db: MemoryDatabase):
        provider = _FakeProvider(
            semantic=json.dumps({"memories": [], "experiences": []}),
            episode="NONE",
        )
        extractor = _make_extractor(db, provider)
        session = _coordinated_session()

        receipt = await extractor.process_request(_request(session, 2), session=session)

        assert receipt.status == "completed"
        assert receipt.memory_ids == ()
        assert receipt.episode_ids == ()
        assert _memory_contents(db) == []
        assert _last_seq(db, session) == 2

    async def test_persist_failure_rolls_back_and_retry_succeeds(
        self, db: MemoryDatabase, monkeypatch: pytest.MonkeyPatch
    ):
        import nanobot.memory.extractor as extractor_module

        provider = _FakeProvider(semantic=SEMANTIC_ONE, episode=EPISODE_ONE)
        extractor = _make_extractor(db, provider)
        session = _coordinated_session()

        def _boom(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("episode insert failed")

        monkeypatch.setattr(extractor_module, "add_episode", _boom)
        failed = await extractor.process_request(_request(session, 2), session=session)

        assert failed.status == "failed"
        assert failed.error_code == "PERSISTENCE_FAILED"
        assert _memory_contents(db) == [], "同一事务内的事实写入必须回滚"
        assert _last_seq(db, session) == 0
        with db.connect() as conn:
            row = get_extraction_batch(conn, failed.batch_id)
            assert row is not None and row.status == "failed"
            pending = list_incomplete_extraction_batches(
                conn,
                workspace_id="default",
                session_key=session.key,
                generation=session.memory_generation,
                after_seq=0,
            )
        assert [item.batch_id for item in pending] == [failed.batch_id]

        monkeypatch.undo()
        retried = await extractor.process_request(_request(session, 2), session=session)

        assert retried.status == "completed"
        assert retried.batch_id == failed.batch_id, "重试必须复用同一批次"
        assert len(_memory_contents(db)) == 1
        assert _last_seq(db, session) == 2

    async def test_evidence_change_reports_payload_mismatch(
        self, db: MemoryDatabase
    ):
        provider = _FakeProvider(semantic=RuntimeError("boom"), episode=EPISODE_ONE)
        extractor = _make_extractor(db, provider)
        session = _coordinated_session()

        first = await extractor.process_request(_request(session, 2), session=session)
        assert first.status == "partial"

        # 同一 seq 的内容被改写：同一 batch_id 对应了另一份证据。
        session.messages[0]["content"] = "以后都用 poetry 管理依赖"
        provider.semantic = SEMANTIC_ONE
        second = await extractor.process_request(_request(session, 2), session=session)

        assert second.status == "failed"
        assert second.error_code == "BATCH_PAYLOAD_MISMATCH"
        assert second.batch_id == first.batch_id
        assert _last_seq(db, session) == 0

    async def test_generation_mismatch_is_reported(self, db: MemoryDatabase):
        provider = _FakeProvider(semantic=SEMANTIC_ONE, episode=EPISODE_ONE)
        extractor = _make_extractor(db, provider)
        session = _coordinated_session()
        request = _request(session, 2, generation="another-generation")

        receipt = await extractor.process_request(request, session=session)

        assert receipt.status == "failed"
        assert receipt.error_code == "GENERATION_MISMATCH"
        assert provider.tracks == []

    async def test_missing_session_snapshot_is_reported(self, db: MemoryDatabase):
        provider = _FakeProvider(semantic=SEMANTIC_ONE, episode=EPISODE_ONE)
        extractor = _make_extractor(db, provider)
        request = ExtractionRequest(
            workspace_id="default",
            session_key="websocket:missing",
            generation="gen-1",
            through_seq=2,
            reason="idle",
        )

        receipt = await extractor.process_request(request)

        assert receipt.status == "failed"
        assert receipt.error_code == "SESSION_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 空批次 / 无证据
# ---------------------------------------------------------------------------


class TestEvidenceBoundaries:
    async def test_missing_evidence_for_range_is_failed(self, db: MemoryDatabase):
        provider = _FakeProvider(semantic=SEMANTIC_ONE, episode=EPISODE_ONE)
        extractor = _make_extractor(db, provider)
        session = _coordinated_session()
        await extractor.process_request(_request(session, 4), session=session)

        # (4, 6] 没有任何持久消息 → 不能拿旧证据凑数。
        receipt = await extractor.process_request(_request(session, 6), session=session)

        assert receipt.status == "failed"
        assert receipt.error_code == "EVIDENCE_MISSING"
        assert _last_seq(db, session) == 4
