"""WebUI 消息信封里的 ``agent``：只对受信连接生效，且必须真实存在。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.webui.inbound_commands import WebUICommandRouter, normalize_agent_selection
from nanobot.webui.session_projection import WebUISessionProjection
from nanobot.webui.workspaces import WebUIWorkspaceController


class _Transport:
    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.events: list[dict[str, Any]] = []
        self.dispatched: list[dict[str, Any]] = []
        self.attached: list[str] = []

    def is_allowed(self, client_id: str) -> bool:
        return bool(client_id)

    def webui_subscribers(self, chat_id: str) -> list[Any]:
        return []

    def webui_attach(self, connection: Any, chat_id: str) -> None:
        self.attached.append(chat_id)

    def webui_detach(self, connection: Any, chat_id: str) -> None:
        return None

    def webui_clear_connection_default(self, connection: Any) -> None:
        return None

    def webui_clear_stream_buffers(self, chat_id: str) -> None:
        return None

    async def webui_hydrate(self, chat_id: str) -> None:
        return None

    async def webui_send_event(self, connection: Any, event: str, **fields: Any) -> None:
        self.events.append({"event": event, **fields})

    async def webui_send_raw(self, connection: Any, raw: str, *, label: str = "") -> None:
        self.events.append({"raw": raw})

    async def webui_dispatch_message(self, **kwargs: Any) -> None:
        self.dispatched.append(kwargs)

    async def send_session_updated(self, chat_id: str, *, scope: str | None = None) -> None:
        return None

    def of(self, name: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event.get("event") == name]


class _Reader:
    def __init__(self) -> None:
        self.metadata: dict[str, dict[str, Any]] = {}

    def read_session_metadata(self, key: str) -> dict[str, Any] | None:
        if key not in self.metadata:
            return None
        return {"metadata": self.metadata[key]}

    def get_or_create(self, key: str) -> Any:
        return SimpleNamespace(key=key, metadata=self.metadata.setdefault(key, {}))

    def save(self, session: Any) -> None:
        self.metadata[session.key] = dict(session.metadata)


def _make_router(tmp_path: Path) -> tuple[WebUICommandRouter, _Transport, Any]:
    connection = object()
    transport = _Transport(connection)
    reader = _Reader()
    workspaces = WebUIWorkspaceController(
        session_manager=reader,
        default_workspace=tmp_path,
        default_restrict_to_workspace=True,
    )
    gateway = SimpleNamespace(
        http=SimpleNamespace(workspace_controls_available=lambda connection: True),
        endpoint=SimpleNamespace(webui_connections=[connection]),
        settings=SimpleNamespace(config=SimpleNamespace(path=tmp_path / "config.json")),
        media=SimpleNamespace(
            store_inbound_attachments=lambda raw: ([], None),
            augment_transcript_user_media=lambda paths: [],
        ),
        ingress=SimpleNamespace(validate_text=lambda text: None),
        transcripts=SimpleNamespace(
            client_turn_metadata=lambda raw: {},
            append_user_message=lambda *args, **kwargs: None,
        ),
        workspaces=workspaces,
        temporary_chats=SimpleNamespace(
            message_policy=lambda connection, chat_id, content: None,
            validate_attach=lambda chat_id: None,
        ),
        session_projection=WebUISessionProjection(reader),
        session_manager=reader,
        team_bindings=None,
    )
    return WebUICommandRouter(transport, gateway), transport, connection  # type: ignore[arg-type]


async def _new_chat(router: WebUICommandRouter, transport: _Transport, connection: Any) -> str:
    await router.dispatch(connection, "client-1", {"type": "new_chat"})
    attached = transport.of("attached")
    assert attached
    return str(attached[-1]["chat_id"])


def test_normalize_agent_selection_accepts_factory_profiles(tmp_path: Path) -> None:
    assert normalize_agent_selection("commander", tmp_path) == ("commander", None)


@pytest.mark.parametrize("raw", [123, "", "   ", "../../etc/passwd", "Upper", "a/b"])
def test_normalize_agent_selection_rejects_invalid_ids(tmp_path: Path, raw: object) -> None:
    assert normalize_agent_selection(raw, tmp_path) == (None, "invalid_agent")


def test_normalize_agent_selection_rejects_unknown_profiles(tmp_path: Path) -> None:
    assert normalize_agent_selection("ghost", tmp_path) == (None, "agent_not_found")


@pytest.mark.asyncio
async def test_trusted_message_carries_the_selected_agent(tmp_path: Path) -> None:
    router, transport, connection = _make_router(tmp_path)
    chat_id = await _new_chat(router, transport, connection)

    await router.dispatch(
        connection,
        "client-1",
        {
            "type": "message",
            "chat_id": chat_id,
            "content": "@code-reviewer 看看这段",
            "agent": "code-reviewer",
            "webui": True,
        },
    )

    assert transport.of("error") == []
    assert transport.dispatched[0]["metadata"]["agent_id"] == "code-reviewer"


@pytest.mark.asyncio
async def test_unknown_agent_fails_before_dispatch(tmp_path: Path) -> None:
    router, transport, connection = _make_router(tmp_path)
    chat_id = await _new_chat(router, transport, connection)

    await router.dispatch(
        connection,
        "client-1",
        {
            "type": "message",
            "chat_id": chat_id,
            "content": "@ghost 在吗",
            "agent": "ghost",
            "webui": True,
        },
    )

    assert transport.dispatched == []
    assert transport.of("error")[-1]["detail"] == "agent_not_found"


@pytest.mark.asyncio
async def test_invalid_agent_fails_before_dispatch(tmp_path: Path) -> None:
    router, transport, connection = _make_router(tmp_path)
    chat_id = await _new_chat(router, transport, connection)

    await router.dispatch(
        connection,
        "client-1",
        {
            "type": "message",
            "chat_id": chat_id,
            "content": "hello",
            "agent": "../../etc/passwd",
            "webui": True,
        },
    )

    assert transport.dispatched == []
    assert transport.of("error")[-1]["detail"] == "invalid_agent"


@pytest.mark.asyncio
async def test_untrusted_connection_cannot_select_an_agent(tmp_path: Path) -> None:
    router, transport, connection = _make_router(tmp_path)
    chat_id = await _new_chat(router, transport, connection)

    await router.dispatch(
        object(),
        "client-2",
        {
            "type": "message",
            "chat_id": chat_id,
            "content": "@code-reviewer 看看这段",
            "agent": "code-reviewer",
            "webui": True,
        },
    )

    assert transport.dispatched[0]["metadata"].get("agent_id") is None


@pytest.mark.asyncio
async def test_message_without_agent_leaves_metadata_untouched(tmp_path: Path) -> None:
    router, transport, connection = _make_router(tmp_path)
    chat_id = await _new_chat(router, transport, connection)

    await router.dispatch(
        connection,
        "client-1",
        {"type": "message", "chat_id": chat_id, "content": "hello", "webui": True},
    )

    assert "agent_id" not in transport.dispatched[0]["metadata"]
