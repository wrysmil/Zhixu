"""``bind_writing_session`` 与发送帧 ``writing`` 校验（契约 §1.2 / §2 / §6）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.webui.inbound_commands import WebUICommandRouter
from nanobot.webui.session_projection import WebUISessionProjection
from nanobot.webui.workspaces import WebUIWorkspaceController
from nanobot.writing.database import WritingDatabase
from nanobot.writing.repository import ArticleRepository
from nanobot.writing.session_binding import WritingSessionBinder


class _Reader:
    def __init__(self) -> None:
        self.metadata: dict[str, dict[str, Any]] = {}

    def read_session_metadata(self, key: str) -> dict[str, Any] | None:
        if key not in self.metadata:
            return None
        return {"metadata": self.metadata[key]}

    def get_or_create(self, key: str) -> Any:
        return SimpleNamespace(key=key, metadata=self.metadata.setdefault(key, {}))

    def save(self, session: Any, *, fsync: bool = False) -> None:
        self.metadata[session.key] = dict(session.metadata)


class _Transport:
    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.events: list[dict[str, Any]] = []
        self.attached: list[str] = []
        self.hydrated: list[str] = []

    def webui_attach(self, connection: Any, chat_id: str) -> None:
        self.attached.append(chat_id)

    async def webui_hydrate(self, chat_id: str) -> None:
        self.hydrated.append(chat_id)

    async def webui_send_event(self, connection: Any, event: str, **fields: Any) -> None:
        self.events.append({"event": event, **fields})

    def of(self, name: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event.get("event") == name]


class _TeamService:
    def __init__(self, member_ids: list[str]) -> None:
        self.member_ids = member_ids
        self.calls: list[tuple[str, int | None]] = []

    def get_team_detail(self, team_id: str, *, revision: int | None = None) -> Any:
        self.calls.append((team_id, revision))
        return {
            "snapshot": {
                "leader": {"id": "writing-editor"},
                "members": [
                    {"member": {"memberId": member_id}} for member_id in self.member_ids
                ],
            },
        }


class _Harness:
    def __init__(self, tmp_path) -> None:
        self.db = WritingDatabase(tmp_path)
        self.db.ensure_schema()
        self.article = ArticleRepository(self.db).create_article(
            "create-1", {"title": "标题", "body": "正文", "status": "draft"},
        )["article"]
        self.reader = _Reader()
        self.binder = WritingSessionBinder(
            self.db,
            self.reader,
            team_resolver=lambda: {
                "team_id": "team-1", "team_revision": 3, "team_name": "创作团队",
            },
            session_key_for=lambda chat_id: f"websocket:{chat_id}",
        )
        self.connection = object()
        self.transport = _Transport(self.connection)
        workspaces = WebUIWorkspaceController(
            session_manager=self.reader,
            default_workspace=tmp_path,
            default_restrict_to_workspace=True,
        )
        self.team_service = _TeamService(["writing-researcher", "writing-author"])
        gateway = SimpleNamespace(
            endpoint=SimpleNamespace(webui_connections=[self.connection]),
            writing_sessions=self.binder,
            team_service=self.team_service,
            workspaces=workspaces,
            session_projection=WebUISessionProjection(self.reader),
            session_manager=self.reader,
            http=SimpleNamespace(workspace_controls_available=lambda _connection: True),
            media=SimpleNamespace(
                store_inbound_attachments=lambda raw: ([], None),
                augment_transcript_user_media=lambda paths: [],
            ),
            ingress=SimpleNamespace(validate_text=lambda text: None),
            transcripts=SimpleNamespace(
                client_turn_metadata=lambda raw: {},
                append_user_message=lambda *args, **kwargs: None,
            ),
            temporary_chats=SimpleNamespace(
                message_policy=lambda connection, chat_id, content: None,
                validate_attach=lambda chat_id: None,
            ),
            team_bindings=None,
            settings=SimpleNamespace(config=SimpleNamespace(path=tmp_path / "config.json")),
        )
        self.router = WebUICommandRouter(self.transport, gateway)  # type: ignore[arg-type]

    def bind(self) -> dict[str, Any]:
        import asyncio

        asyncio.run(self.router.dispatch(
            self.connection, "client-1",  # type: ignore[arg-type]
            {"type": "bind_writing_session", "article_id": self.article["id"]},
        ))
        events = self.transport.of("writing_session")
        assert events, self.transport.events
        return events[-1]


@pytest.fixture
def harness(tmp_path) -> _Harness:
    return _Harness(tmp_path)


def test_bind_returns_server_session_and_hydrates(harness: _Harness) -> None:
    bound = harness.bind()
    assert bound["article_id"] == harness.article["id"]
    assert bound["chat_id"]
    assert bound["session_key"] == f"websocket:{bound['chat_id']}"
    assert bound["team_id"] == "team-1"
    assert bound["team_revision"] == 3
    assert bound["revision"] == 1
    assert bound["digest"] == harness.article["digest"]
    assert harness.transport.attached == [bound["chat_id"]]
    assert harness.transport.hydrated == [bound["chat_id"]]
    attached = harness.transport.of("attached")[-1]
    assert attached["team_binding"]["teamId"] == "team-1"
    session = harness.reader.metadata[bound["session_key"]]
    assert session["writing_article"]["articleId"] == harness.article["id"]


def test_bind_is_idempotent(harness: _Harness) -> None:
    first = harness.bind()
    second = harness.bind()
    assert first["chat_id"] == second["chat_id"]


def test_bind_rejects_unknown_article(harness: _Harness) -> None:
    import asyncio

    asyncio.run(harness.router.dispatch(
        harness.connection, "client-1",  # type: ignore[arg-type]
        {"type": "bind_writing_session", "article_id": "missing"},
    ))
    assert harness.transport.of("error")[-1]["detail"] == "article not found"


def test_bind_requires_webui_connection(harness: _Harness) -> None:
    import asyncio

    harness.router._webui_connections.clear()  # noqa: SLF001 - 断言协议边界
    asyncio.run(harness.router.dispatch(
        harness.connection, "client-1",  # type: ignore[arg-type]
        {"type": "bind_writing_session", "article_id": harness.article["id"]},
    ))
    assert harness.transport.of("error")[-1]["detail"] == "access_denied"


def _context(article: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    context = {
        "article_id": article["id"],
        "revision": article["revision"],
        "digest": article["digest"],
    }
    context.update(overrides)
    return context


def _validate(harness: _Harness, chat_id: str, context: Any, *, trusted: bool = True):
    import asyncio

    return asyncio.run(
        harness.router._validate_writing_context(chat_id, context, trusted=trusted)  # noqa: SLF001
    )


def test_valid_context_is_accepted(harness: _Harness) -> None:
    bound = harness.bind()
    context, error = _validate(harness, bound["chat_id"], _context(
        harness.article,
        member_id="writing-author",
        task="rewrite",
        selection={"text": "正文", "start": 0, "end": 2},
    ))
    assert error is None
    assert context == {
        "article_id": harness.article["id"],
        "revision": 1,
        "digest": harness.article["digest"],
        "member_id": "writing-author",
        "task": "rewrite",
        "selection": {"text": "正文", "start": 0, "end": 2},
    }


def test_untrusted_or_wrong_chat_is_unbound(harness: _Harness) -> None:
    bound = harness.bind()
    assert _validate(harness, bound["chat_id"], _context(harness.article), trusted=False) == (
        None, "writing_article_unbound",
    )
    assert _validate(harness, "other-chat", _context(harness.article)) == (
        None, "writing_article_unbound",
    )


def test_stale_revision_is_a_conflict(harness: _Harness) -> None:
    bound = harness.bind()
    stale = _context(harness.article, revision=0)
    assert _validate(harness, bound["chat_id"], stale)[1] == "invalid_writing_context"
    stale = _context(harness.article, revision=9)
    _context_value, error = _validate(harness, bound["chat_id"], stale)
    assert error == f"article_conflict current_revision=1 article_id={harness.article['id']}"


def test_unknown_member_is_denied(harness: _Harness) -> None:
    bound = harness.bind()
    _context_value, error = _validate(
        harness, bound["chat_id"], _context(harness.article, member_id="stranger"),
    )
    assert error == "writing_permission_denied"

    accepted, error = _validate(
        harness, bound["chat_id"], _context(harness.article, member_id="writing-author"),
    )
    assert error is None and accepted["member_id"] == "writing-author"


def test_task_and_selection_shape_are_validated(harness: _Harness) -> None:
    bound = harness.bind()
    assert _validate(harness, bound["chat_id"], _context(harness.article, task="delete"))[1] == (
        "invalid_writing_context"
    )
    assert _validate(harness, bound["chat_id"], _context(
        harness.article, selection={"text": "正文", "start": 2, "end": 2},
    ))[1] == "invalid_writing_context"
    assert _validate(harness, bound["chat_id"], _context(
        harness.article, selection={"text": "正文", "start": "0", "end": 2},
    ))[1] == "invalid_writing_context"
