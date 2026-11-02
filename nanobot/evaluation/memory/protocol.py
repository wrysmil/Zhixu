"""后端 adapter 协议（契约 §3，逐字沿用契约里的签名）。

这里的 ``...`` 是**类型签名**，不是实现内容。实现是 async，所有跨模块结果都是严格
Pydantic 模型——不用无结构 dict 当模块边界，否则 gold 与执行载荷的隔离会在某一层
悄悄失效。

实现方（WU-03 的知序 adapter）必须遵守三条语义约束：

- ``settle`` 的 ``completed`` 只在所有必需轨道都完成时成立；被产品链路隔离的异常
  要落到 ``failed_stages`` / ``reason_codes``，不能因为函数返回就判完成。
- ``snapshot`` / ``restore`` / ``restart`` 必须从持久化文件恢复，复制 SQLite 走
  backup API，不复制活动 WAL 文件，也不保留旧 Python 对象伪装重启。
- ``forget`` / ``rebuild`` 不支持时必须显式返回 unsupported / capability_missing，
  不用测试专用强删逻辑替产品补能力。
"""

from __future__ import annotations

from typing import Protocol

from nanobot.evaluation.memory.models import (
    ForgetRequest,
    ForgetResult,
    MemoryCandidate,
    MemoryCapabilities,
    MemoryExecutionInput,
    MemoryProbe,
    MemorySessionEvent,
    MemoryStateSnapshot,
    ProbeExecution,
    RecallResult,
    SettleResult,
)


class MemoryAdapter(Protocol):
    capabilities: MemoryCapabilities

    async def reset(self, candidate: MemoryCandidate) -> None: ...

    async def ingest_session(self, event: MemorySessionEvent) -> None: ...

    async def settle(self, session_id: str, timeout_s: float) -> SettleResult: ...

    async def snapshot(self, checkpoint_id: str) -> MemoryStateSnapshot: ...

    async def restore(self, state: MemoryStateSnapshot) -> None: ...

    async def restart(self, state: MemoryStateSnapshot) -> None: ...

    async def recall(self, probe: MemoryProbe) -> RecallResult: ...

    async def answer(self, probe: MemoryProbe) -> ProbeExecution: ...

    async def forget(self, request: ForgetRequest) -> ForgetResult: ...

    async def rebuild(self) -> SettleResult: ...

    async def close(self) -> None: ...


__all__ = ["MemoryAdapter", "MemoryExecutionInput"]
