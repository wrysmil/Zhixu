"""会话结束 flush（WU-06b / 契约 §5）与 ``POST /api/sessions/{key}/end`` 行为测试。

覆盖：
- ``MemoryExtractor.flush_session``：解析最新 generation/through_seq、提交
  ``ExtractionRequest(flush=True)``、无新增消息返回 completed 空回执、失败返回
  结构化 ``error_code``、无持久快照返回 ``None``、消费键以会话归属为准；
- 端点行为矩阵：装配消费者 → 接受（后台调度，不阻塞模型）；未装配 → 503 错误
  信封；flush 失败 → 结构化错误在消费者回执上可见；
- 装配处的 MessageBus 订阅把同一 ``SessionEndEvent`` 路由到同一个消费者。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from aiohttp import web

from nanobot.api.endpoints.sessions import (
    SESSION_END_CONSUMER_KEY,
    SESSION_END_FLUSH_TASKS_KEY,
    handle_session_end,
)
from nanobot.api.server import create_app
from nanobot.bus.queue import MessageBus
from nanobot.memory.database import MemoryDatabase, get_extraction_batch
from nanobot.memory.extractor import MemoryExtractor
from nanobot.memory.provenance import memory_workspace_id
from nanobot.memory.repository import get_extraction_seq, list_memories
from nanobot.memory.session_end_event import (
    SessionEndEvent,
    SessionEndFlushConsumer,
    SessionEndReason,
)
from nanobot.session.manager import Session

try:
    from aiohttp.test_utils import TestClient, TestServer

    HAS_AIOHTTP = True
except ImportError:  # pragma: no cover - aiohttp 是运行期依赖
    HAS_AIOHTTP = False

pytest_plugins = ("pytest_asyncio",)


# ---------------------------------------------------------------------------
# Fakes（鸭子类型，不依赖真实 provider / 会话存储）
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 1024
    reasoning_effort = None


class _FakeProvider:
    """按 prompt 标记分流语义 / 情节两轨；值可以是字符串、dict 或异常。"""

    def __init__(self, *, semantic: Any = None, episode: Any = None) -> None:
        self.semantic = semantic
        self.episode = episode
        self.tracks: list[str] = []

    async def chat_with_retry(self, *, messages: list[dict[str, Any]], **_kwargs: Any) -> Any:
        body = " ".join(
            message.get("content", "")
            for message in messages
            if isinstance(message.get("content"), str)
        )
        is_semantic = "语义记忆抽取" in body
        self.tracks.append("semantic" if is_semantic else "episode")
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
    key: str = "websocket:session-end",
    *,
    generation: str = "gen-end-1",
    metadata: dict[str, Any] | None = None,
) -> Session:
    """构造等同已保存形状的会话：消息带 memory_seq，会话带 generation。"""
    payload = [
        {"role": "user", "content": "以后都用 uv 管理依赖"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "记一下：必须每次保存日志"},
        {"role": "assistant", "content": "收到"},
    ]
    numbered = []
    for index, message in enumerate(payload, start=1):
        entry = dict(message)
        entry["memory_seq"] = index
        numbered.append(entry)
    session = Session(key=key, messages=numbered, metadata=dict(metadata or {}))
    session.memory_generation = generation
    session.memory_next_seq = len(numbered) + 1
    return session


def _make_extractor(
    db: MemoryDatabase,
    provider: _FakeProvider,
    session: Session | None,
) -> MemoryExtractor:
    return MemoryExtractor(
        db,
        _FakeRuntime(provider),
        session_loader=lambda key: session if session is not None and key == session.key else None,
    )


def _last_seq(db: MemoryDatabase, session: Session) -> int:
    with db.connect() as conn:
        return get_extraction_seq(conn, session.key, session.memory_generation)


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


# ---------------------------------------------------------------------------
# flush_session
# ---------------------------------------------------------------------------


class TestFlushSession:
    async def test_flush_submits_request_for_latest_coordinates(self, db: MemoryDatabase):
        provider = _FakeProvider(semantic=SEMANTIC_ONE, episode=EPISODE_ONE)
        session = _coordinated_session()
        extractor = _make_extractor(db, provider, session)

        captured: list[Any] = []
        original = extractor.process_request

        async def _spy(request: Any, **kwargs: Any) -> Any:
            captured.append(request)
            return await original(request, **kwargs)

        extractor.process_request = _spy  # type: ignore[method-assign]

        receipt = await extractor.flush_session(
            session.key, workspace_id="default", reason="session_end"
        )

        assert receipt is not None
        assert receipt.status == "completed"
        assert receipt.through_seq == 4
        assert len(receipt.memory_ids) == 1
        assert len(receipt.episode_ids) == 1
        assert len(captured) == 1
        request = captured[0]
        assert request.flush is True
        assert request.generation == session.memory_generation
        assert request.through_seq == 4
        assert request.reason == "session_end"
        assert _last_seq(db, session) == 4
        with db.connect() as conn:
            row = get_extraction_batch(conn, receipt.batch_id)
        assert row is not None and row.reason == "session_end"

    async def test_flush_without_new_messages_returns_completed_empty(
        self, db: MemoryDatabase
    ):
        provider = _FakeProvider(semantic=SEMANTIC_ONE, episode=EPISODE_ONE)
        session = _coordinated_session()
        extractor = _make_extractor(db, provider, session)
        first = await extractor.flush_session(session.key, workspace_id="default")
        assert first is not None and first.status == "completed"

        calls = list(provider.tracks)
        second = await extractor.flush_session(session.key, workspace_id="default")

        assert second is not None
        assert second.status == "completed"
        assert second.through_seq == 4
        assert second.memory_ids == ()
        assert second.episode_ids == ()
        assert provider.tracks == calls, "无新增消息不得再调模型"

    async def test_flush_failure_surfaces_structured_error_code(self, db: MemoryDatabase):
        provider = _FakeProvider(
            semantic=RuntimeError("semantic down"),
            episode=RuntimeError("episode down"),
        )
        session = _coordinated_session()
        extractor = _make_extractor(db, provider, session)

        receipt = await extractor.flush_session(session.key, workspace_id="default")

        assert receipt is not None
        assert receipt.status == "failed"
        assert receipt.error_code == "SEMANTIC_TRACK_FAILED"
        assert _last_seq(db, session) == 0, "失败不得推进消费边界"

    async def test_flush_returns_none_when_session_never_persisted(self, db: MemoryDatabase):
        provider = _FakeProvider(semantic=SEMANTIC_ONE, episode=EPISODE_ONE)
        extractor = _make_extractor(db, provider, None)

        receipt = await extractor.flush_session("websocket:missing", workspace_id="default")

        assert receipt is None
        assert provider.tracks == []

    async def test_flush_uses_session_workspace_as_consumption_key(
        self, db: MemoryDatabase, tmp_path: Path
    ):
        project = tmp_path / "project-alpha"
        project.mkdir()
        session = _coordinated_session(
            metadata={"workspace_scope": {"project_path": str(project)}},
        )
        provider = _FakeProvider(semantic=SEMANTIC_ONE, episode=EPISODE_ONE)
        extractor = _make_extractor(db, provider, session)

        receipt = await extractor.flush_session(session.key, workspace_id="declared-elsewhere")

        assert receipt is not None and receipt.status == "completed"
        expected_workspace = memory_workspace_id(str(project))
        assert expected_workspace != "declared-elsewhere"
        with db.connect() as conn:
            row = get_extraction_batch(conn, receipt.batch_id)
            assert row is not None and row.workspace_id == expected_workspace
            assert {memory.workspace_id for memory in list_memories(conn)} == {
                expected_workspace
            }


# ---------------------------------------------------------------------------
# 端点行为矩阵
# ---------------------------------------------------------------------------


class _BlockingConsumer:
    """模拟慢模型：进入后等待释放，证明端点不等待 flush 完成。"""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.events: list[SessionEndEvent] = []

    async def __call__(self, event: SessionEndEvent) -> None:
        self.events.append(event)
        self.entered.set()
        await self.release.wait()


class _StubSessionManager:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages

    def get_cached(self, _key: str) -> None:
        return None

    def read_session_file(self, _key: str) -> dict[str, Any]:
        return {"messages": self._messages}


@pytest_asyncio.fixture
async def aiohttp_client():
    if not HAS_AIOHTTP:
        pytest.skip("aiohttp test utils unavailable")
    clients: list[TestClient] = []

    async def _make_client(app: web.Application) -> TestClient:
        client = TestClient(TestServer(app))
        await client.start_server()
        clients.append(client)
        return client

    try:
        yield _make_client
    finally:
        for client in clients:
            await client.close()


def _make_mock_agent() -> Any:
    agent = MagicMock()
    agent.process_direct = AsyncMock(return_value="unused")
    agent.aclose = AsyncMock()
    return agent


class TestSessionEndEndpoint:
    async def test_assembled_consumer_accepts_without_waiting_for_flush(self, aiohttp_client):
        consumer = _BlockingConsumer()
        messages = [{"role": "user", "content": "hello"}]
        app = create_app(
            _make_mock_agent(),
            session_manager=_StubSessionManager(messages),
            session_end_consumer=consumer,
        )
        client = await aiohttp_client(app)

        response = await client.post("/api/sessions/websocket%3Aabc/end", json={})
        payload = await response.json()

        assert response.status == 200
        assert payload["status"] == "ok"
        assert payload["session_key"] == "websocket:abc"
        assert payload["state"] == "accepted"

        await asyncio.wait_for(consumer.entered.wait(), timeout=2)
        assert len(consumer.events) == 1
        event = consumer.events[0]
        assert event.session_key == "websocket:abc"
        assert event.reason is SessionEndReason.USER_CLOSE
        assert event.transcript == messages

        consumer.release.set()
        for task in list(app.get(SESSION_END_FLUSH_TASKS_KEY) or ()):
            await task

    async def test_missing_consumer_returns_503_error_envelope(self, aiohttp_client):
        app = web.Application()
        app.router.add_post("/api/sessions/{session_key}/end", handle_session_end)
        client = await aiohttp_client(app)

        response = await client.post("/api/sessions/websocket%3Aabc/end", json={})
        payload = await response.json()

        assert response.status == 503
        assert payload["error"]["code"] == 503
        assert payload["error"]["type"] == "service_unavailable"
        assert payload["error"]["message"]
        assert SESSION_END_CONSUMER_KEY not in app
        assert not app.get(SESSION_END_FLUSH_TASKS_KEY)

    async def test_flush_failure_is_visible_on_consumer_receipt(
        self, aiohttp_client, db: MemoryDatabase
    ):
        session = _coordinated_session()
        provider = _FakeProvider(
            semantic=RuntimeError("semantic down"),
            episode=RuntimeError("episode down"),
        )
        extractor = _make_extractor(db, provider, session)
        consumer = SessionEndFlushConsumer(
            lambda _key: extractor, workspace_id="default"
        )
        app = create_app(_make_mock_agent(), session_end_consumer=consumer)
        client = await aiohttp_client(app)

        response = await client.post(
            f"/api/sessions/{session.key.replace(':', '%3A')}/end", json={}
        )
        assert response.status == 200
        tasks = list(app.get(SESSION_END_FLUSH_TASKS_KEY) or ())
        assert tasks, "接受后必须已调度后台 flush"
        await asyncio.gather(*tasks)

        assert consumer.last_receipt is not None
        assert consumer.last_receipt.status == "failed"
        assert consumer.last_receipt.error_code == "SEMANTIC_TRACK_FAILED"

    async def test_event_bus_subscription_routes_session_end_event(self):
        consumer = _BlockingConsumer()
        bus = MessageBus()
        create_app(_make_mock_agent(), event_bus=bus, session_end_consumer=consumer)

        task = bus.publish_nowait(
            SessionEndEvent(
                session_key="websocket:abc", reason=SessionEndReason.IDLE_TIMEOUT
            )
        )
        assert task is not None
        await asyncio.wait_for(consumer.entered.wait(), timeout=2)
        assert consumer.events[0].reason is SessionEndReason.IDLE_TIMEOUT

        consumer.release.set()
        await task

    async def test_unknown_reason_falls_back_to_user_close(self, aiohttp_client):
        consumer = _BlockingConsumer()
        app = create_app(_make_mock_agent(), session_end_consumer=consumer)
        client = await aiohttp_client(app)

        response = await client.post(
            "/api/sessions/websocket%3Aabc/end", json={"reason": "not-a-reason"}
        )
        assert response.status == 200
        await asyncio.wait_for(consumer.entered.wait(), timeout=2)
        assert consumer.events[0].reason is SessionEndReason.USER_CLOSE
        consumer.release.set()
