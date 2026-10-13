"""Behaviour tests for the /plan builtin command."""

from types import SimpleNamespace

from nanobot.command.builtin import cmd_plan


def _ctx(args: str, *, session=object(), is_user_turn: bool = True):
    msg = SimpleNamespace(
        channel="websocket",
        chat_id="chat-1",
        content=f"/plan {args}".strip(),
        metadata={},
    )
    return SimpleNamespace(
        msg=msg,
        session=session,
        key="websocket:chat-1",
        raw=f"/plan {args}".strip(),
        args=args,
        is_user_turn=is_user_turn,
        turn_scopes=[],
    )


async def test_plan_without_args_returns_usage() -> None:
    reply = await cmd_plan(_ctx(""))
    assert reply is not None
    assert "Usage: /plan" in reply.content


async def test_plan_blocked_while_task_running() -> None:
    reply = await cmd_plan(_ctx("refactor auth", session=None))
    assert reply is not None
    assert "/stop" in reply.content


async def test_plan_rejected_for_non_user_turns() -> None:
    reply = await cmd_plan(_ctx("refactor auth", is_user_turn=False))
    assert reply is not None
    assert "user `/plan" in reply.content


async def test_plan_rewrites_turn_into_plan_instruction() -> None:
    ctx = _ctx("migrate the database")
    result = await cmd_plan(ctx)
    assert result is None
    assert ctx.msg.metadata["plan_requested"] is True
    assert ctx.msg.metadata["original_command"] == "/plan"
    assert ctx.msg.metadata["original_content"] == "/plan migrate the database"
    assert "[Plan mode]" in ctx.msg.content
    assert "migrate the database" in ctx.msg.content
