"""WU-02：可注入记忆时钟（契约 §3「MemoryClock 分别提供 wall_now / monotonic /
real_deadline」，且「真实超时不得随模拟时钟跳跃」）。

覆盖：

- 计划验收例：虚拟日期跳跃一天，真实执行 deadline 不变。
- 生产缺省：全部真实时间（不注入时行为与今天一致）。
- 冻结模式 ``advance`` 只影响 wall / 策略 monotonic。
- 白名单内时间读取改走注入时钟：TTL（store_adapter）、新鲜度（engine recency）、
  默认时间戳（extractor）、幂等窗口（orchestrator）、派生刷新（lifecycle）。

不在本文件覆盖的时钟依赖（仍走真实时间）见 WU-02 的 ``risks_and_limits``：
``repository.query_semantic`` 的 SQL 窗口截断（``repository.py`` 不在本 WU 白名单）。
"""

from __future__ import annotations

import inspect
import math
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.memory.clock import MemoryClock
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import MemoryExtractor
from nanobot.memory.lifecycle import MemoryLifecycle
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.orchestrator import SessionEndOrchestrator
from nanobot.memory.repository import add_memory
from nanobot.memory.retrieval.channels.attachments import search_attachments
from nanobot.memory.retrieval.channels.episodes import search_episodes
from nanobot.memory.retrieval.engine import RetrievalEngine, _build_recency_fn
from nanobot.memory.retrieval.reranker import _compute_recency
from nanobot.memory.retrieval.store_adapter import MemoryStoreAdapter, _is_live
from nanobot.memory.session_end_event import SessionEndEvent, SessionEndReason
from nanobot.webui.memory_services import MemoryServices

_FROZEN = datetime(2026, 11, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 三条时间轴的基本契约
# ---------------------------------------------------------------------------


def test_virtual_day_keeps_real_deadline():
    """计划验收例：日期跳一天，策略 monotonic 同步前进，真实 deadline 不变。"""
    clock = MemoryClock(frozen_at=_FROZEN, monotonic_at=0)
    deadline = clock.real_deadline()
    clock.advance(86400)
    assert clock.wall_now() == datetime(2026, 11, 2, tzinfo=timezone.utc)
    assert clock.monotonic() == 86400
    assert 0 <= clock.real_deadline() - deadline < 5


def test_production_clock_uses_real_time():
    """生产缺省：墙上时间与单调时间都是真值，不注入时行为与今天一致。"""
    clock = MemoryClock()
    before = datetime.now(timezone.utc)
    wall = clock.wall_now()
    after = datetime.now(timezone.utc)
    assert before <= wall <= after
    assert clock.monotonic() > 0.0


def test_advance_requires_frozen_clock():
    """非冻结时钟不允许 advance（避免把生产时间轴改写）。"""
    clock = MemoryClock()
    with pytest.raises(RuntimeError):
        clock.advance(1)


def test_advance_rejects_backwards_move():
    clock = MemoryClock(frozen_at=_FROZEN, monotonic_at=0)
    with pytest.raises(ValueError):
        clock.advance(-1)


def test_frozen_clock_is_deterministic():
    """两个同样起点的冻结时钟在同样 advance 后读数完全一致。"""
    a = MemoryClock(frozen_at=_FROZEN, monotonic_at=0)
    b = MemoryClock(frozen_at=_FROZEN, monotonic_at=0)
    a.advance(3600)
    b.advance(3600)
    assert a.wall_now() == b.wall_now() == _FROZEN + timedelta(hours=1)
    assert a.monotonic() == b.monotonic() == 3600.0


def test_frozen_at_requires_timezone():
    with pytest.raises(ValueError):
        MemoryClock(frozen_at=datetime(2026, 11, 1))


def test_real_deadline_ignores_virtual_advance():
    """虚拟时钟跳一天不得改变真实执行 deadline（真实超时口径独立）。"""
    clock = MemoryClock(frozen_at=_FROZEN, monotonic_at=0)
    first = clock.real_deadline(timeout_s=30)
    clock.advance(86400)
    second = clock.real_deadline(timeout_s=30)
    assert 0 <= second - first < 5


def test_real_deadline_carries_timeout():
    """real_deadline 带超时时返回「真实现在 + 超时」。"""
    clock = MemoryClock(frozen_at=_FROZEN, monotonic_at=0)
    base = clock.real_deadline()
    with_timeout = clock.real_deadline(timeout_s=12.5)
    assert with_timeout - base == pytest.approx(12.5, abs=1.0)


# ---------------------------------------------------------------------------
# 白名单内时间读取改走注入时钟
# ---------------------------------------------------------------------------


def test_clock_injection_surfaces_are_available():
    """白名单内的组件都接受**可选** clock 注入（未注入时行为不变）。"""
    for target in (
        MemoryExtractor.__init__,
        MemoryLifecycle.__init__,
        SessionEndOrchestrator.__init__,
        RetrievalEngine.__init__,
        MemoryStoreAdapter.__init__,
    ):
        assert "clock" in inspect.signature(target).parameters, target


def test_engine_recency_follows_injected_clock():
    """检索新鲜度以注入时钟为基准：虚拟日期跳跃后 recency 按天衰减。"""
    clock = MemoryClock(frozen_at=_FROZEN, monotonic_at=0)
    recency = _build_recency_fn(clock)
    assert recency(_FROZEN) == pytest.approx(1.0)
    clock.advance(86400 * 10)
    assert recency(_FROZEN) == pytest.approx(math.exp(-1.0))


def test_engine_recency_without_clock_uses_real_time():
    """未注入时钟时新鲜度仍按真实时间（行为与今天一致）。"""
    recency = _build_recency_fn()
    now = datetime.now(timezone.utc)
    assert recency(now) == pytest.approx(1.0, abs=1e-3)


def test_compute_recency_accepts_explicit_now():
    """``_compute_recency`` 支持显式 now（naive/aware 混用不炸）。"""
    aware = _FROZEN
    naive = _FROZEN.astimezone().replace(tzinfo=None)
    assert _compute_recency(aware, now=aware) == pytest.approx(1.0)
    assert _compute_recency(naive, now=aware) == pytest.approx(1.0)


def test_store_adapter_expiry_uses_injected_clock():
    """TTL（expires_at）以注入时钟判定过期。"""
    mem = SimpleNamespace(superseded_by=None, expires_at="2026-11-01T12:00:00+00:00")
    clock = MemoryClock(frozen_at=_FROZEN, monotonic_at=0)
    assert _is_live(mem, now=clock.wall_now()) is True
    clock.advance(86400)
    assert _is_live(mem, now=clock.wall_now()) is False


def test_store_adapter_uses_injected_clock():
    adapter = MemoryStoreAdapter(None, clock=MemoryClock(frozen_at=_FROZEN, monotonic_at=0))
    assert adapter._now() == _FROZEN


def test_episode_channel_default_timestamp_uses_injected_now():
    """情节通道的「默认时间戳」走注入时钟。"""
    captured: dict[str, Any] = {}

    def _compute(value: datetime) -> float:
        captured["value"] = value
        return 1.0

    store = SimpleNamespace(
        search_episodes=lambda **kwargs: [SimpleNamespace(id="e1", summary="摘要")]
    )
    search_episodes(
        store,
        query="main.py",
        keywords=None,
        limit=3,
        compute_recency=_compute,
        now=_FROZEN,
    )
    assert captured["value"] == _FROZEN


def test_attachment_channel_default_timestamp_uses_injected_now():
    """附件通道的「默认时间戳」走注入时钟。"""
    captured: dict[str, Any] = {}

    def _compute(value: datetime) -> float:
        captured["value"] = value
        return 1.0

    store = SimpleNamespace(
        search_attachments=lambda term, *, intent, limit: [
            {"id": "a1", "content": "图片", "importance_score": 0.5}
        ]
    )
    search_attachments(
        store,
        raw_query="找一张图片",
        keywords=[],
        intent="general",
        limit=3,
        compute_recency=_compute,
        now=_FROZEN,
    )
    assert captured["value"] == _FROZEN


def test_extractor_default_timestamp_uses_injected_clock():
    """抽取写入的默认时间戳走注入时钟。"""
    clock = MemoryClock(frozen_at=_FROZEN, monotonic_at=0)
    extractor = MemoryExtractor(database=None, runtime=None, clock=clock)
    assert extractor._now_iso() == _FROZEN.isoformat()


def test_extractor_without_clock_uses_real_time():
    extractor = MemoryExtractor(database=None, runtime=None)
    assert extractor._now_iso() != _FROZEN.isoformat()


@pytest.mark.asyncio
async def test_orchestrator_idempotency_window_uses_injected_clock():
    """幂等窗口（策略间隔）走注入时钟：窗口内跳过，窗口外重跑。"""

    class _Episode:
        id = "ep-1"
        summary = "sum"
        goal = "g"
        outcome = SimpleNamespace(value="completed")
        entities: list[Any] = []
        tools_used: list[Any] = []
        action_nodes: list[Any] = []

    class _Extractor:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def generate_episode(self, transcript, session_key, source="session_end"):
            self.calls.append("episode")
            return _Episode()

        async def extract_user_profile(self, transcript, episode_id, cited=None):
            self.calls.append("profile")
            return [], []

        async def extract_experience(self, transcript, episode_id):
            self.calls.append("experience")
            return []

    extractor = _Extractor()
    clock = MemoryClock(frozen_at=_FROZEN, monotonic_at=0)
    orchestrator = SessionEndOrchestrator(extractor=extractor, clock=clock)
    event = SessionEndEvent(
        session_key="k1",
        reason=SessionEndReason.USER_CLOSE,
        transcript=[
            {"role": "user", "content": "Hello there!"},
            {"role": "assistant", "content": "Hi! How can I help you?"},
            {"role": "user", "content": "Can you help with my code?"},
        ],
    )

    await orchestrator.run(event)
    clock.advance(10)  # 仍在 30s 幂等窗口内
    await orchestrator.run(event)
    assert extractor.calls.count("episode") == 1

    clock.advance(40)  # 超出幂等窗口
    await orchestrator.run(event)
    assert extractor.calls.count("episode") == 2


def test_lifecycle_refresh_timestamp_uses_injected_clock(tmp_path):
    """派生文件（MEMORY.md）刷新时间戳走注入时钟。"""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    database = MemoryDatabase(workspace)
    database.init_schema()
    services = MemoryServices(workspace_id="test-ws", database=database)

    MemoryLifecycle._instances.clear()
    MemoryLifecycle._last_refresh_at.clear()
    MemoryLifecycle._last_refresh_iso.clear()
    MemoryLifecycle._last_refresh_trigger.clear()

    now = _FROZEN.isoformat()
    memory = Memory(
        id=str(uuid.uuid4()),
        content="用户偏好 Python",
        created_at=now,
        updated_at=now,
        type=MemoryType.PREFERENCE,
        priority=MemoryPriority.LONG_TERM,
        source="manual",
        importance_score=0.9,
        tags=[],
        subject="",
        predicate="",
        scope="user",
        workspace_id="test-ws",
    )
    with services.database.connect() as conn:
        add_memory(conn, memory)

    clock = MemoryClock(frozen_at=_FROZEN, monotonic_at=0)
    lifecycle = MemoryLifecycle("test-ws", services, clock=clock)
    lifecycle.memory_dir.mkdir(parents=True, exist_ok=True)
    result = lifecycle.refresh_memory_md_sync("test-ws")

    assert result["status"] == "ok"
    assert MemoryLifecycle._last_refresh_iso["test-ws"] == "2026-11-01T00:00:00Z"
