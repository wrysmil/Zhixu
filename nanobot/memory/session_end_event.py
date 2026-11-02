"""会话结束事件数据结构（契约 §5：结束请求接统一 flush）。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

from nanobot.events import AgentEvent

if TYPE_CHECKING:
    from nanobot.memory.extractor import MemoryExtractor
    from nanobot.memory.models import BatchReceipt


class SessionEndReason(str, Enum):
    """会话结束原因。"""

    USER_CLOSE = "user_close"
    IDLE_TIMEOUT = "idle_timeout"
    PROCESS_SHUTDOWN = "process_shutdown"
    CHANNEL_DISCONNECT = "channel_disconnect"


@dataclass
class SessionEndEvent(AgentEvent):
    """会话结束事件载荷。"""

    session_key: str
    reason: SessionEndReason
    transcript: list[dict[str, Any]] = field(default_factory=list)
    emitted_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        # 防御性浅拷贝，避免外部修改影响事件快照
        # 兼容调用方显式传 None（旧编排器与渠道侧的既有用法）。
        raw = cast("list[dict[str, Any]] | None", self.transcript)
        self.transcript = [dict(message) for message in raw] if raw else []


def reason_value(reason: SessionEndReason | str) -> str:
    """取审计用的来源值；容忍调用方直接传字符串。"""
    if isinstance(reason, SessionEndReason):
        return reason.value
    return str(reason)


class SessionEndFlushConsumer:
    """把 :class:`SessionEndEvent` 转成 ``MemoryExtractor.flush_session`` 的消费者。

    这是「会话结束事件 → 统一批次请求」的唯一转换点（契约 §5：结束请求表示
    请求被接受，不声称模型已完成）。它不新增事件系统：事件的传递仍走既有
    ``MessageBus`` 订阅或端点直接调度，本类只保存最近一次回执，供结构化状态
    观测与测试断言（失败时能拿到 ``error_code``，而不是静默丢弃）。
    """

    def __init__(
        self,
        extractor_provider: Callable[[str], "MemoryExtractor"],
        *,
        workspace_id: str = "default",
    ) -> None:
        self._extractor_provider = extractor_provider
        self._workspace_id = workspace_id
        self.last_receipt: "BatchReceipt | None" = None

    async def __call__(self, event: SessionEndEvent) -> "BatchReceipt | None":
        extractor = self._extractor_provider(event.session_key)
        receipt = await extractor.flush_session(
            event.session_key,
            workspace_id=self._workspace_id,
            reason=reason_value(event.reason),
        )
        self.last_receipt = receipt
        return receipt
