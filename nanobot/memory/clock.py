"""记忆评测可注入时钟（契约 §3「MemoryClock」）。

三条时间轴
----------
``wall_now()``
    **测试日期**：新鲜度 / TTL / 默认时间戳的比较基准。
``monotonic()``
    **策略间隔**：幂等窗口、去抖、冷却等「距上次多久」的判定。
``real_deadline()``
    **真实执行超时**：asyncio 真实 deadline。**不随模拟时钟跳跃** —— 虚拟日期
    跳一天不会把「真实超时 30 秒」变成「已经过了 30 秒」。

生产缺省（不注入）时三条轴全部走真实时间，行为与改造前逐字一致；注入时只有
``wall_now`` / ``monotonic`` 变成虚拟值。契约 §3 明确「禁止全局 monkeypatch」，
因此本模块只提供**显式注入**，不做任何全局替换。

冻结模式（``frozen_at`` 给出）下 ``advance(seconds)`` 是唯一的时间推进手段：
它同时推进 wall 与策略 monotonic，且不产生任何真实等待 —— 评测因此可以在
毫秒内「跳一天」而得到确定结果。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

__all__ = ["MemoryClock"]


def _loop_time() -> float:
    """事件循环的单调时钟；不在事件循环里时退回 ``time.monotonic()``。

    ``asyncio.wait_for`` 等真实超时以事件循环时钟为准，故 deadline 必须在
    **运行中的事件循环内**取，保证与 ``wait_for`` 同一时间轴。
    """
    try:
        return asyncio.get_running_loop().time()
    except RuntimeError:
        return time.monotonic()


class MemoryClock:
    """记忆处理的可注入时钟。

    Args:
        frozen_at: 冻结的墙上时间（测试日期）。必须带时区。``None`` → 生产真实时间。
        monotonic_at: 策略单调轴的起点（秒）。冻结模式下与 ``advance`` 的位移叠加；
            非冻结模式下作为 ``time.monotonic()`` 的原点偏移（便于按「策略间隔」
            口径记录证据）。

    Raises:
        ValueError: ``frozen_at`` 缺少时区信息。
    """

    def __init__(
        self,
        *,
        frozen_at: datetime | None = None,
        monotonic_at: float | None = None,
    ) -> None:
        if frozen_at is not None and frozen_at.tzinfo is None:
            raise ValueError("MemoryClock.frozen_at must be timezone-aware")
        self._frozen_at = frozen_at
        self._monotonic_at = float(monotonic_at) if monotonic_at is not None else 0.0
        # 冻结模式下累计的虚拟位移（秒）。
        self._offset = 0.0
        if frozen_at is None and monotonic_at is not None:
            # 生产模式：把单调轴平移到调用方给的原点，保持「距起点多久」的口径。
            self._monotonic_origin_shift = self._monotonic_at - time.monotonic()
        else:
            self._monotonic_origin_shift = 0.0

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    @property
    def frozen(self) -> bool:
        """是否处于冻结（虚拟时间）模式。"""
        return self._frozen_at is not None

    # ------------------------------------------------------------------
    # 三条时间轴
    # ------------------------------------------------------------------

    def wall_now(self) -> datetime:
        """当前墙上时间（测试日期基准），带时区。"""
        if self._frozen_at is None:
            return datetime.now(timezone.utc)
        return self._frozen_at + timedelta(seconds=self._offset)

    def monotonic(self) -> float:
        """策略单调时钟（秒）：幂等窗口 / 去抖 / 冷却的判定基准。"""
        if self._frozen_at is None:
            return time.monotonic() + self._monotonic_origin_shift
        return self._monotonic_at + self._offset

    def real_deadline(self, timeout_s: float = 0.0) -> float:
        """真实执行 deadline：事件循环单调时钟上的绝对时刻。

        ``timeout_s=0`` 时返回「真实现在」。**虚拟时钟的 ``advance`` 不影响该
        值** —— 真实超时口径与模拟日期严格分离（契约 §3）。
        """
        return _loop_time() + max(0.0, float(timeout_s))

    def iso_now(self) -> str:
        """当前墙上时间的 ISO-8601 字符串（默认时间戳口径）。"""
        return self.wall_now().isoformat()

    # ------------------------------------------------------------------
    # 推进（仅冻结模式）
    # ------------------------------------------------------------------

    def advance(self, seconds: float) -> None:
        """把虚拟时间向前推进 ``seconds`` 秒（同时推进 wall 与策略 monotonic）。

        Raises:
            RuntimeError: 时钟未冻结（生产模式不允许改写真实时间轴）。
            ValueError: ``seconds`` 为负（只允许向前）。
        """
        if self._frozen_at is None:
            raise RuntimeError("MemoryClock.advance requires a frozen clock")
        if seconds < 0:
            raise ValueError("MemoryClock.advance only moves forward")
        self._offset += float(seconds)
