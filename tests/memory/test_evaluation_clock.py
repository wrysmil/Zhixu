"""WU-02：可注入记忆时钟（契约 §3「MemoryClock 分别提供 wall_now / monotonic /
real_deadline」，且「真实超时不得随模拟时钟跳跃」）。

覆盖：

- 计划验收例：虚拟日期跳跃一天，真实执行 deadline 不变。
- 生产缺省：全部真实时间（不注入时行为与今天一致）。
- 冻结模式 ``advance`` 只影响 wall / 策略 monotonic。
- 仍保留注入面的时间读取：默认时间戳（extractor）、TTL 读取入口
  （store_adapter）、派生刷新（lifecycle）。

主线记忆重构（D01–D13）删除了 ``SessionEndOrchestrator``、附件通道与按新鲜度
重排的辅助函数，对应覆盖已随实现移除；会话结束改由
``MemoryExtractor.flush_session`` 承担。
"""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from nanobot.memory.clock import MemoryClock
from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import MemoryExtractor
from nanobot.memory.lifecycle import MemoryLifecycle
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.repository import add_memory
from nanobot.memory.retrieval.engine import RetrievalEngine
from nanobot.memory.retrieval.store_adapter import MemoryStoreAdapter
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
# 仍保留的时钟注入面
# ---------------------------------------------------------------------------


def test_clock_injection_surfaces_are_available():
    """仍在使用的组件都接受**可选** clock 注入（未注入时行为不变）。"""
    for target in (
        MemoryExtractor.__init__,
        MemoryLifecycle.__init__,
        RetrievalEngine.__init__,
        MemoryStoreAdapter.__init__,
    ):
        assert "clock" in inspect.signature(target).parameters, target


def test_store_adapter_uses_injected_clock():
    adapter = MemoryStoreAdapter(
        None, clock=MemoryClock(frozen_at=_FROZEN, monotonic_at=0)
    )
    assert adapter._now() == _FROZEN


def test_extractor_default_timestamp_uses_injected_clock():
    """抽取写入的默认时间戳走注入时钟。"""
    clock = MemoryClock(frozen_at=_FROZEN, monotonic_at=0)
    extractor = MemoryExtractor(database=None, runtime=None, clock=clock)
    assert extractor._now_iso() == _FROZEN.isoformat()


def test_extractor_without_clock_uses_real_time():
    extractor = MemoryExtractor(database=None, runtime=None)
    assert extractor._now_iso() != _FROZEN.isoformat()


def test_lifecycle_refresh_timestamp_uses_injected_clock(tmp_path):
    """派生文件（MEMORY.md）刷新时间戳走注入时钟。"""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    database = MemoryDatabase(workspace)
    database.init_schema()
    services = MemoryServices(workspace_id="test-ws", database=database)

    MemoryLifecycle._instances.clear()
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
