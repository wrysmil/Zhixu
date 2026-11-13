"""轨迹上下文绑定。

Recorder 通过 ContextVar 随 asyncio 任务传播；工具并行分支与子运行各自
继承父上下文，不向共享 RequestContext 就地写父子 ID。每个绑定都在
``finally`` 中 reset，避免后台任务泄漏归属。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanobot.trajectory.recorder import TraceRecorder

_CURRENT_RECORDER: ContextVar["TraceRecorder | None"] = ContextVar(
    "zhixu_trajectory_recorder", default=None,
)
_CURRENT_LOGICAL_REQUEST: ContextVar[str | None] = ContextVar(
    "zhixu_trajectory_logical_request", default=None,
)


def current_trace_recorder() -> "TraceRecorder | None":
    return _CURRENT_RECORDER.get()


def bind_trace_recorder(recorder: "TraceRecorder | None") -> Token["TraceRecorder | None"]:
    return _CURRENT_RECORDER.set(recorder)


def reset_trace_recorder(token: Token["TraceRecorder | None"]) -> None:
    _CURRENT_RECORDER.reset(token)


def current_logical_request_id() -> str | None:
    return _CURRENT_LOGICAL_REQUEST.get()


def bind_logical_request(request_id: str | None) -> Token[str | None]:
    return _CURRENT_LOGICAL_REQUEST.set(request_id)


def reset_logical_request(token: Token[str | None]) -> None:
    _CURRENT_LOGICAL_REQUEST.reset(token)
