"""Turn-level Langfuse tracing. Fail-open: any error only disables tracing."""
from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

TRACE_NAME = "zhixu.turn"
_MAX_TEXT = 20000

ClientFactory = Callable[[], Any]
_client_factory: ClientFactory | None = None

_TURN_TRACES: dict[str, str] = {}
_TRACE_SESSIONS: dict[str, str] = {}
_TURN_CONTENTS: dict[str, tuple[str, str]] = {}
_TURN_TRACE_LIMIT = 2048


def remember_turn_content(trace_id: str | None, input_text: str, output_text: str) -> None:
    """Cache the turn's question/answer so dataset export works without re-reading traces."""
    if not trace_id:
        return
    if len(_TURN_CONTENTS) >= _TURN_TRACE_LIMIT:
        _TURN_CONTENTS.clear()
    _TURN_CONTENTS[trace_id] = (input_text, output_text)


def lookup_turn_content(trace_id: str | None) -> tuple[str, str] | None:
    if not trace_id:
        return None
    return _TURN_CONTENTS.get(trace_id)


def register_turn_trace(turn_id: str | None, trace_id: str | None) -> None:
    """Remember turn -> trace so transport layers can attach it without metadata plumbing."""
    if not turn_id or not trace_id:
        return
    if len(_TURN_TRACES) >= _TURN_TRACE_LIMIT:
        _TURN_TRACES.clear()
    _TURN_TRACES[turn_id] = trace_id


def lookup_turn_trace(turn_id: str | None) -> str | None:
    if not turn_id:
        return None
    return _TURN_TRACES.get(turn_id)


def register_trace_session(trace_id: str | None, session_key: str | None) -> None:
    if not trace_id or not session_key:
        return
    if len(_TRACE_SESSIONS) >= _TURN_TRACE_LIMIT:
        _TRACE_SESSIONS.clear()
    _TRACE_SESSIONS[trace_id] = session_key


def session_for_trace(trace_id: str | None) -> str | None:
    if not trace_id:
        return None
    return _TRACE_SESSIONS.get(trace_id)


def set_client_factory(factory: ClientFactory | None) -> None:
    global _client_factory
    _client_factory = factory


def tracing_enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def _get_client() -> Any | None:
    if not tracing_enabled():
        return None
    try:
        if _client_factory is not None:
            return _client_factory()
        from langfuse import get_client

        return get_client()
    except Exception:
        logger.exception("Langfuse init failed; turn tracing disabled")
        return None


def hashed_user_id(channel: str, sender_id: str) -> str:
    salt = os.environ.get("LANGFUSE_USER_SALT", "zhixu")
    return hashlib.sha256(f"{channel}:{sender_id}:{salt}".encode()).hexdigest()[:16]


@dataclass(slots=True)
class TurnTraceHandle:
    trace_id: str | None = None
    client: Any = None
    manager: Any = None
    span: Any = None
    closed: bool = field(default=False)


def start_turn_trace(
    *,
    session_key: str,
    channel: str,
    sender_id: str,
    turn_id: str | None,
    user_input: str | None,
) -> TurnTraceHandle:
    client = _get_client()
    if client is None:
        return TurnTraceHandle()
    manager = None
    try:
        manager = client.start_as_current_span(
            name=TRACE_NAME,
            input={"message": (user_input or "")[:_MAX_TEXT]},
            metadata={"channel": channel, "turn_id": turn_id},
        )
        span = manager.__enter__()
        client.update_current_trace(
            name=TRACE_NAME,
            session_id=session_key,
            user_id=hashed_user_id(channel, sender_id),
            tags=["zhixu", channel],
        )
        trace_id = client.get_current_trace_id()
        register_trace_session(trace_id, session_key)
        return TurnTraceHandle(
            trace_id=trace_id,
            client=client,
            manager=manager,
            span=span,
        )
    except Exception:
        logger.exception("Langfuse turn trace start failed")
        try:
            if manager is not None:
                manager.__exit__(None, None, None)
        except Exception:
            logger.exception("Langfuse turn trace rollback failed")
        return TurnTraceHandle()


def finish_turn_trace(
    handle: TurnTraceHandle,
    *,
    output: str | None,
    status: str,
    error: str | None = None,
) -> None:
    if handle.closed or handle.manager is None:
        return
    handle.closed = True
    try:
        if handle.span is not None:
            handle.span.update(
                output={"message": (output or "")[:_MAX_TEXT]},
                metadata={"status": status},
                level="ERROR" if status in {"failed", "cancelled"} else "DEFAULT",
                status_message=error,
            )
    except Exception:
        logger.exception("Langfuse turn span update failed")
    try:
        from nanobot.observability.langfuse_prompts import used_prompts

        prompts = used_prompts()
        if prompts and handle.client is not None:
            handle.client.update_current_trace(metadata={"prompts": prompts})
    except Exception:
        logger.exception("Langfuse prompt metadata update failed")
    try:
        handle.manager.__exit__(None, None, None)
    except Exception:
        logger.exception("Langfuse turn span close failed")
