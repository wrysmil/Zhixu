"""`/feedback` 命令单元测试（契约 §3：jsonl 存储 + session metadata + 面板条目）。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.bus.events import InboundMessage
from nanobot.command.builtin import (
    builtin_command_palette,
    builtin_command_starts_agent_turn,
    register_builtin_commands,
)
from nanobot.command.router import CommandContext, CommandRouter
from nanobot.session.manager import SessionManager

_SESSION_KEY = "websocket:chat-1"


def _feedback_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "nanobot-data"
    data_dir.mkdir()
    monkeypatch.setattr("nanobot.config.paths.get_data_dir", lambda: data_dir)
    return data_dir


def _context(
    router: CommandRouter,
    manager: SessionManager,
    raw: str,
    session=None,
) -> CommandContext:
    msg = InboundMessage(
        channel="websocket",
        sender_id="user",
        chat_id="chat-1",
        content=raw,
    )
    return CommandContext(
        msg=msg,
        session=session,
        key=_SESSION_KEY,
        raw=raw,
        loop=SimpleNamespace(sessions=manager),
    )


def _router_and_manager(tmp_path: Path) -> tuple[CommandRouter, SessionManager]:
    router = CommandRouter()
    register_builtin_commands(router)
    manager = SessionManager(workspace=tmp_path / "ws")
    return router, manager


@pytest.mark.asyncio
async def test_feedback_records_jsonl_and_session_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = _feedback_data_dir(tmp_path, monkeypatch)
    router, manager = _router_and_manager(tmp_path)
    session = manager.get_or_create(_SESSION_KEY)
    manager.save(session)

    response = await router.dispatch(
        _context(router, manager, "/feedback 界面很流畅", session=session)
    )

    assert response is not None
    assert "已记录反馈，谢谢！" in response.content

    lines = (data_dir / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert set(entry) == {"ts", "session_key", "text"}
    assert isinstance(entry["ts"], float)
    assert entry["session_key"] == _SESSION_KEY
    assert entry["text"] == "界面很流畅"

    payload = manager.read_session_metadata(_SESSION_KEY)
    assert payload is not None
    metadata = payload.get("metadata", {})
    assert metadata.get("feedbacks") == [entry]


@pytest.mark.asyncio
async def test_feedback_appends_second_entry_without_overwriting_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = _feedback_data_dir(tmp_path, monkeypatch)
    router, manager = _router_and_manager(tmp_path)
    session = manager.get_or_create(_SESSION_KEY)
    manager.save(session)

    first = await router.dispatch(
        _context(router, manager, "/feedback 第一条", session=session)
    )
    second = await router.dispatch(
        _context(router, manager, "/feedback 第二条", session=session)
    )

    assert first is not None and second is not None
    lines = (data_dir / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    entries = [json.loads(line) for line in lines]
    assert [e["text"] for e in entries] == ["第一条", "第二条"]

    payload = manager.read_session_metadata(_SESSION_KEY)
    assert payload is not None
    assert payload.get("metadata", {}).get("feedbacks") == entries


@pytest.mark.asyncio
async def test_feedback_without_text_returns_usage_and_stores_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = _feedback_data_dir(tmp_path, monkeypatch)
    router, manager = _router_and_manager(tmp_path)
    session = manager.get_or_create(_SESSION_KEY)
    manager.save(session)

    response = await router.dispatch(_context(router, manager, "/feedback", session=session))

    assert response is not None
    assert "Usage: /feedback" in response.content
    assert not (data_dir / "feedback.jsonl").exists()
    payload = manager.read_session_metadata(_SESSION_KEY)
    assert payload is not None
    assert "feedbacks" not in payload.get("metadata", {})


def test_feedback_palette_entry_registered() -> None:
    items = [item for item in builtin_command_palette() if item["command"] == "/feedback"]
    assert len(items) == 1
    assert items[0]["description"] == "发送关于当前会话的反馈"
    assert items[0]["accepts_args"] is True


def test_feedback_is_side_channel_command() -> None:
    assert builtin_command_starts_agent_turn("/feedback 很好") is False


@pytest.mark.asyncio
async def test_feedback_accepts_exactly_4000_chars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = _feedback_data_dir(tmp_path, monkeypatch)
    router, manager = _router_and_manager(tmp_path)
    session = manager.get_or_create(_SESSION_KEY)
    manager.save(session)

    response = await router.dispatch(
        _context(router, manager, "/feedback " + "a" * 4000, session=session)
    )

    assert response is not None
    assert "已记录反馈，谢谢！" in response.content
    lines = (data_dir / "feedback.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["text"] == "a" * 4000


@pytest.mark.asyncio
async def test_feedback_rejects_over_4000_chars_without_storing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = _feedback_data_dir(tmp_path, monkeypatch)
    router, manager = _router_and_manager(tmp_path)
    session = manager.get_or_create(_SESSION_KEY)
    manager.save(session)

    response = await router.dispatch(
        _context(router, manager, "/feedback " + "a" * 4001, session=session)
    )

    assert response is not None
    assert "已记录反馈，谢谢！" not in response.content
    assert "4000" in response.content
    assert not (data_dir / "feedback.jsonl").exists()
    payload = manager.read_session_metadata(_SESSION_KEY)
    metadata = payload.get("metadata", {}) if payload else {}
    assert "feedbacks" not in metadata
