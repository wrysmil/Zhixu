"""Pending ``ask_question`` requests awaiting a user answer.

The tool registers a question, pushes it to the client, and awaits the
future. The WebUI inbound command ``question_answer`` resolves it by
``question_id`` (a uuid4 token doubles as the capability to answer).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class PendingQuestion:
    question_id: str
    channel: str
    chat_id: str
    session_key: str | None
    payload: dict[str, Any]
    future: asyncio.Future[Any]


_PENDING: dict[str, PendingQuestion] = {}


def register_question(
    *,
    channel: str,
    chat_id: str,
    session_key: str | None,
    payload: dict[str, Any],
) -> PendingQuestion:
    """Track a question until it is answered, forgotten, or times out."""
    future = asyncio.get_running_loop().create_future()
    pending = PendingQuestion(
        question_id=str(payload["question_id"]),
        channel=channel,
        chat_id=chat_id,
        session_key=session_key,
        payload=payload,
        future=future,
    )
    _PENDING[pending.question_id] = pending
    return pending


def resolve_answer(question_id: str, answer: str) -> bool:
    """Deliver *answer* to a waiting ``ask_question`` call."""
    pending = _PENDING.pop(question_id, None)
    if pending is None or pending.future.done():
        return False
    pending.future.set_result(answer)
    return True


def forget_question(question_id: str) -> None:
    """Drop a pending question without resolving it (timeout/cancel path)."""
    pending = _PENDING.pop(question_id, None)
    if pending is not None and not pending.future.done():
        pending.future.cancel()


def pending_for_chat(chat_id: str) -> list[dict[str, Any]]:
    """Payload snapshots of unanswered questions for *chat_id* (re-hydration)."""
    return [pq.payload for pq in _PENDING.values() if pq.chat_id == chat_id]
