"""ask_question tool: pause the turn and ask the user a structured question."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

from nanobot.agent.questions import forget_question, register_question
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import ToolContext, current_request_context
from nanobot.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.bus.events import OutboundMessage
from nanobot.bus.outbound_events import QuestionRequestedEvent

# Channels whose clients can render an interactive question card.
_INTERACTIVE_CHANNELS = frozenset({"websocket"})
_ANSWER_TIMEOUT_SECONDS = 30 * 60
_MAX_ANSWER_CHARS = 4000


@tool_parameters(
    tool_parameters_schema(
        question=StringSchema(
            "The question to ask the user, phrased as a single clear sentence.",
            min_length=1,
            max_length=500,
        ),
        header=StringSchema(
            "Optional short label for the question (a few words). Display hint only.",
            max_length=60,
            nullable=True,
        ),
        options=ArraySchema(
            ObjectSchema(
                {
                    "label": StringSchema(
                        "Short option label the user clicks (a few words).",
                        min_length=1,
                        max_length=120,
                    ),
                    # Passed as a properties dict: a bare `description=` kwarg
                    # would bind to ObjectSchema's root description, not the field.
                    "description": StringSchema(
                        "Optional one-line explanation of the option.",
                        max_length=500,
                        nullable=True,
                    ),
                    "recommended": BooleanSchema(
                        description="Mark this option as the recommended default (at most one)."
                    ),
                },
                required=["label"],
            ),
            description="2-8 distinct answer options shown as a numbered list.",
            min_items=2,
            max_items=8,
        ),
        required=["question", "options"],
    )
)
class AskQuestionTool(Tool):
    """Ask the user a multiple-choice question and wait for the answer."""

    def __init__(
        self,
        publish_outbound: Callable[[OutboundMessage], Any] | None = None,
    ):
        self._publish_outbound = publish_outbound

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        publish = ctx.bus.publish_outbound if ctx.bus else None
        return cls(publish_outbound=publish)

    @property
    def name(self) -> str:
        return "ask_question"

    @property
    def description(self) -> str:
        return (
            "Ask the user a question with numbered options and BLOCK until they "
            "answer, then continue with their answer. Use when you need a "
            "decision, preference, or clarification before proceeding. Provide "
            "2-8 short, distinct options; optionally mark one as recommended. "
            "The user can also type a custom answer or decline to choose. "
            "Only available on interactive chat channels (WebUI/desktop); on "
            "other channels ask in plain text instead."
        )

    @property
    def exclusive(self) -> bool:
        return True

    @staticmethod
    def _normalize_options(raw: Any) -> list[dict[str, Any]] | str:
        if not isinstance(raw, list):
            return "options must be a list"
        options: list[dict[str, Any]] = []
        recommended_seen = False
        for item in raw:
            if not isinstance(item, dict):
                return "each option must be an object with a 'label'"
            label = str(item.get("label") or "").strip()
            if not label:
                return "every option needs a non-empty 'label'"
            option: dict[str, Any] = {"label": label[:120]}
            description = item.get("description")
            if isinstance(description, str) and description.strip():
                option["description"] = description.strip()[:500]
            recommended = bool(item.get("recommended")) and not recommended_seen
            recommended_seen = recommended_seen or recommended
            if recommended:
                option["recommended"] = True
            options.append(option)
        if len(options) < 2:
            return "provide at least 2 options"
        return options

    async def execute(
        self,
        question: str,
        options: Any,
        header: Any = None,
        **kwargs: Any,
    ) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
        rc = current_request_context()
        if rc is None or rc.channel not in _INTERACTIVE_CHANNELS:
            return ToolResult.error(
                "Error: ask_question needs an interactive client (WebUI/desktop); "
                "this turn came from a channel that cannot render a question card. "
                "Ask the question in plain text instead."
            )
        chat_id = (rc.chat_id or "").strip()
        if not chat_id:
            return ToolResult.error("Error: no chat_id in the current request context")
        if self._publish_outbound is None:
            return ToolResult.error("Error: message bus is not available")
        normalized = self._normalize_options(options)
        if isinstance(normalized, str):
            return ToolResult.error(f"Error: {normalized}")
        question_text = str(question).strip()
        if not question_text:
            return ToolResult.error("Error: question must not be empty")

        payload: dict[str, Any] = {
            "question_id": uuid.uuid4().hex,
            "question": question_text[:500],
            "options": normalized,
        }
        if isinstance(header, str) and header.strip():
            payload["header"] = header.strip()[:60]

        pending = register_question(
            channel=rc.channel,
            chat_id=chat_id,
            session_key=rc.session_key,
            payload=payload,
        )
        try:
            await self._publish_outbound(
                OutboundMessage(
                    channel=rc.channel,
                    chat_id=chat_id,
                    content="",
                    event=QuestionRequestedEvent(question=payload),
                    metadata=dict(rc.metadata or {}),
                )
            )
        except Exception as exc:  # delivery failed: do not block the turn
            forget_question(payload["question_id"])
            return ToolResult.error(f"Error: failed to deliver question: {exc}")

        try:
            answer = await asyncio.wait_for(pending.future, timeout=_ANSWER_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            forget_question(payload["question_id"])
            return ToolResult.error(
                "Error: the user did not answer within the time limit. Continue "
                "with your best judgment or ask again later in plain text."
            )
        except asyncio.CancelledError:
            forget_question(payload["question_id"])
            raise
        forget_question(payload["question_id"])
        return (
            f'The user answered: "{str(answer)[:_MAX_ANSWER_CHARS]}"\n'
            "Continue using this answer; do not re-ask the same question."
        )
