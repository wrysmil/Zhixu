"""new_chat 选团队 → 首条消息用暂存绑定 → 绑定进会话读数（plan Task 5 / 契约 §4）。

这里测的是**传输层真的通**：命令信封进 ``WebUICommandRouter.dispatch``，事件与入站消息
出 FakeTransport，而不是只调 helper。三条口径必须成立：

1. ``new_chat`` 带 ``team_id`` 时先做准入再暂存，失败就不建聊天、不 attach、不 hydrate。
2. 首条消息把暂存的绑定放进入站 metadata，Leader 看到的和落盘的是同一份，
   所以不存在「先普通回答、再变团队」的竞态。
3. 空聊天重连时暂存已失效要说清 ``TEAM_BINDING_EXPIRED``，不能静默按普通聊天回答。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nanobot.teams.binding import TeamBindingStore
from nanobot.teams.models import TeamBinding
from nanobot.teams.repository import TeamNotFound
from nanobot.teams.service import TeamArchived, TeamDisabled
from nanobot.webui.inbound_commands import WebUICommandRouter
from nanobot.webui.session_identity import webui_session_key
from nanobot.webui.session_projection import WebUISessionProjection
from nanobot.webui.workspaces import WebUIWorkspaceController

_BOUND = TeamBinding(team_id="content-team", team_revision=3, team_name="内容团队")
_BINDING_DICT = {"teamId": "content-team", "teamRevision": 3, "teamName": "内容团队"}


class _Service:
    """内存替身：只做准入检查，一个 SQLite 都不碰。"""

    def __init__(self, *, enabled: bool = True, archived: bool = False) -> None:
        self.enabled = enabled
        self.archived = archived
        self.prepared: list[tuple[str, int | None]] = []

    def prepare_new_run(self, team_id: str, *, revision: int | None = None) -> Any:
        self.prepared.append((team_id, revision))
        if not self.enabled:
            raise TeamDisabled("agents.teams.enabled is false")
        if team_id == "ghost":
            raise TeamNotFound(team_id)
        if self.archived:
            raise TeamArchived(team_id)
        return SimpleNamespace(definition=SimpleNamespace(id=team_id, revision=3, name="内容团队"))


class _Transport:
    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.events: list[dict[str, Any]] = []
        self.attached: list[str] = []
        self.hydrated: list[str] = []
        self.dispatched: list[dict[str, Any]] = []

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
        self.hydrated.append(chat_id)

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
    """``SessionMetadataReader`` 替身：只有测试里显式写过的会话才算存在。"""

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


def _make_router(
    tmp_path: Path,
    *,
    reader: _Reader,
    store: TeamBindingStore | None,
) -> tuple[WebUICommandRouter, _Transport, Any]:
    connection = object()
    transport = _Transport(connection)
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
            # 本文件的用例都不带附件：空进空出与真实实现一致。
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
        team_bindings=store,
    )
    return WebUICommandRouter(transport, gateway), transport, connection  # type: ignore[arg-type]


@pytest.fixture
def clock_now() -> list[float]:
    """可推进的时钟：过期路径不靠 sleep。"""
    return [1_000.0]


def _store_with(clock: list[float], *, ttl: float = 600.0) -> TeamBindingStore:
    def _now() -> float:
        return clock[0]

    return TeamBindingStore(_Service(), now=_now, ttl_seconds=ttl)


async def _new_chat(
    router: WebUICommandRouter,
    transport: _Transport,
    connection: Any,
    envelope: dict[str, Any],
) -> str:
    await router.dispatch(connection, "client-1", {"type": "new_chat", **envelope})
    attached = transport.of("attached")
    assert attached, transport.events
    return str(attached[-1]["chat_id"])


async def test_new_chat_stages_the_team_and_replies_the_binding(
    tmp_path: Path, clock_now: list[float]
) -> None:
    store = _store_with(clock_now)
    router, transport, connection = _make_router(tmp_path, reader=_Reader(), store=store)

    chat_id = await _new_chat(router, transport, connection, {"team_id": "content-team"})

    assert transport.of("attached")[-1]["team_binding"] == _BINDING_DICT
    assert transport.of("session_updated")[-1]["team_binding"] == _BINDING_DICT
    assert chat_id
    assert store.claim(chat_id) == _BOUND


async def test_new_chat_forwards_an_explicit_revision(
    tmp_path: Path, clock_now: list[float]
) -> None:
    """带上 team_revision 时按该修订准入；省略时由服务端解析最新修订（契约 §4）。"""
    service = _Service()
    store = TeamBindingStore(service, now=lambda: clock_now[0])
    router, transport, connection = _make_router(tmp_path, reader=_Reader(), store=store)

    chat_id = await _new_chat(
        router, transport, connection, {"team_id": "content-team", "team_revision": 3}
    )

    assert service.prepared == [("content-team", 3)]
    assert store.claim(chat_id) == _BOUND

    transport.events.clear()
    await _new_chat(router, transport, connection, {"team_id": "content-team"})
    assert service.prepared[-1] == ("content-team", None)


async def test_new_chat_without_a_team_is_a_plain_chat(
    tmp_path: Path, clock_now: list[float]
) -> None:
    store = _store_with(clock_now)
    router, transport, connection = _make_router(tmp_path, reader=_Reader(), store=store)

    chat_id = await _new_chat(router, transport, connection, {})

    assert transport.of("attached")[-1]["team_binding"] is None
    assert transport.of("session_updated")[-1]["team_binding"] is None
    assert store.claim(chat_id) is None


async def test_new_chat_rejects_admission_without_creating_a_chat(
    tmp_path: Path, clock_now: list[float]
) -> None:
    """团队不可用时不建聊天：没有 attached，也没有 attach/hydrate 副作用。"""
    store = _store_with(clock_now)
    router, transport, connection = _make_router(tmp_path, reader=_Reader(), store=store)

    await router.dispatch(connection, "client-1", {"type": "new_chat", "team_id": "ghost"})

    assert transport.of("error")[0]["detail"].startswith("TEAM_NOT_FOUND")
    assert transport.of("attached") == []
    assert transport.of("session_updated") == []
    assert transport.attached == []
    assert transport.hydrated == []


@pytest.mark.parametrize("team_id", ["", " ", 12, "x" * 200])
async def test_new_chat_rejects_malformed_team_id(
    tmp_path: Path, clock_now: list[float], team_id: Any
) -> None:
    store = _store_with(clock_now)
    router, transport, connection = _make_router(tmp_path, reader=_Reader(), store=store)

    await router.dispatch(connection, "client-1", {"type": "new_chat", "team_id": team_id})

    assert transport.of("error"), transport.events
    assert transport.attached == []


@pytest.mark.parametrize("revision", [0, -1, "3", 1.5, True])
async def test_new_chat_rejects_malformed_team_revision(
    tmp_path: Path, clock_now: list[float], revision: Any
) -> None:
    store = _store_with(clock_now)
    router, transport, connection = _make_router(tmp_path, reader=_Reader(), store=store)

    await router.dispatch(
        connection,
        "client-1",
        {"type": "new_chat", "team_id": "content-team", "team_revision": revision},
    )

    assert transport.of("error"), transport.events
    assert transport.attached == []


async def test_new_chat_reports_teams_disabled_instead_of_plain_chat(
    tmp_path: Path, clock_now: list[float]
) -> None:
    store = TeamBindingStore(_Service(enabled=False), now=lambda: clock_now[0])
    router, transport, connection = _make_router(tmp_path, reader=_Reader(), store=store)

    await router.dispatch(connection, "client-1", {"type": "new_chat", "team_id": "content-team"})

    assert transport.of("error")[0]["detail"].startswith("TEAMS_DISABLED")
    assert transport.attached == []


async def test_new_chat_reports_an_archived_team_as_not_found(
    tmp_path: Path, clock_now: list[float]
) -> None:
    """归档团队不再是一个可用团队（契约 §4：对外表现为 TEAM_NOT_FOUND）。"""
    store = TeamBindingStore(_Service(archived=True), now=lambda: clock_now[0])
    router, transport, connection = _make_router(tmp_path, reader=_Reader(), store=store)

    await router.dispatch(connection, "client-1", {"type": "new_chat", "team_id": "content-team"})

    assert transport.of("error")[0]["detail"].startswith("TEAM_NOT_FOUND")
    assert transport.hydrated == []


async def test_new_chat_without_a_team_stack_still_works(tmp_path: Path) -> None:
    router, transport, connection = _make_router(tmp_path, reader=_Reader(), store=None)

    chat_id = await _new_chat(router, transport, connection, {})

    assert transport.of("attached")[-1]["chat_id"] == chat_id


async def test_first_message_carries_the_staged_binding(
    tmp_path: Path, clock_now: list[float]
) -> None:
    """首条消息把可信绑定带进会话：Leader 与落盘读同一份，没有先普通回答的窗口。"""
    store = _store_with(clock_now)
    router, transport, connection = _make_router(tmp_path, reader=_Reader(), store=store)
    chat_id = await _new_chat(router, transport, connection, {"team_id": "content-team"})

    await router.dispatch(
        connection,
        "client-1",
        {"type": "message", "chat_id": chat_id, "content": "research this", "webui": True},
    )

    inbound = transport.dispatched[0]
    assert inbound["metadata"]["team_binding"] == _BINDING_DICT
    # 一次性取走：第二条消息不再携带，会话元数据才是后续回合的真值。
    await router.dispatch(
        connection,
        "client-1",
        {"type": "message", "chat_id": chat_id, "content": "and then this", "webui": True},
    )
    assert "team_binding" not in transport.dispatched[1]["metadata"]


async def test_only_a_webui_authenticated_connection_can_take_the_staging(
    tmp_path: Path, clock_now: list[float]
) -> None:
    """没做过 WebUI 握手的连接不能消费暂存：注入 ``team_binding`` 的通道一无所获。"""
    store = _store_with(clock_now)
    router, transport, _connection = _make_router(tmp_path, reader=_Reader(), store=store)
    chat_id = await _new_chat(router, transport, _connection, {"team_id": "content-team"})

    # 同一台网关上一条来自未登记连接的消息（例如别的通道转投进来的帧）。
    await router.dispatch(
        object(),
        "client-2",
        {"type": "message", "chat_id": chat_id, "content": "research this", "webui": True},
    )

    assert "team_binding" not in transport.dispatched[0]["metadata"]
    # 暂存原封不动：受信连接随后仍能正常取到绑定。
    await router.dispatch(
        _connection,
        "client-1",
        {"type": "message", "chat_id": chat_id, "content": "research this", "webui": True},
    )
    assert transport.dispatched[1]["metadata"]["team_binding"] == _BINDING_DICT


async def test_plain_chat_messages_carry_no_binding(tmp_path: Path, clock_now: list[float]) -> None:
    store = _store_with(clock_now)
    router, transport, connection = _make_router(tmp_path, reader=_Reader(), store=store)
    chat_id = await _new_chat(router, transport, connection, {})

    await router.dispatch(
        connection,
        "client-1",
        {"type": "message", "chat_id": chat_id, "content": "hello", "webui": True},
    )

    assert "team_binding" not in transport.dispatched[0]["metadata"]


async def test_expired_staging_fails_the_first_message_instead_of_plain_chat(
    tmp_path: Path, clock_now: list[float]
) -> None:
    """暂存过期后第一条消息明确报错并要求重选，不能静默按普通聊天回答（契约 §4）。"""
    store = _store_with(clock_now, ttl=30.0)
    router, transport, connection = _make_router(tmp_path, reader=_Reader(), store=store)
    chat_id = await _new_chat(router, transport, connection, {"team_id": "content-team"})

    clock_now[0] += 31.0
    await router.dispatch(
        connection,
        "client-1",
        {"type": "message", "chat_id": chat_id, "content": "research this", "webui": True},
    )

    error = transport.of("error")[-1]
    assert error["detail"].startswith("TEAM_BINDING_EXPIRED")
    assert error["chat_id"] == chat_id
    assert transport.dispatched == []


async def test_reconnecting_an_empty_expired_chat_reports_the_binding_expiry(
    tmp_path: Path, clock_now: list[float]
) -> None:
    """空聊天重连：会话里还没有绑定、暂存又失效了 → 报 TEAM_BINDING_EXPIRED 要求重选。"""
    store = _store_with(clock_now, ttl=30.0)
    router, transport, connection = _make_router(tmp_path, reader=_Reader(), store=store)
    chat_id = await _new_chat(router, transport, connection, {"team_id": "content-team"})
    transport.events.clear()

    clock_now[0] += 31.0
    await router.dispatch(connection, "client-1", {"type": "attach", "chat_id": chat_id})

    error = transport.of("error")[-1]
    assert error["detail"].startswith("TEAM_BINDING_EXPIRED")
    assert error["team_name"] == "内容团队"
    assert transport.of("attached") == []

    # 说过一次就不再重复说：第二次 attach 按普通空聊天处理。
    transport.events.clear()
    await router.dispatch(connection, "client-1", {"type": "attach", "chat_id": chat_id})
    assert transport.of("error") == []
    assert transport.of("attached")[-1]["team_binding"] is None


async def test_reconnecting_a_bound_chat_reports_the_persisted_binding(
    tmp_path: Path, clock_now: list[float]
) -> None:
    """已有会话的绑定来自 session 读数：重连不受暂存生命周期影响。"""
    reader = _Reader()
    reader.metadata[webui_session_key("chat-9")] = {"team_binding": _BINDING_DICT}
    store = _store_with(clock_now)
    router, transport, connection = _make_router(tmp_path, reader=reader, store=store)

    await router.dispatch(connection, "client-1", {"type": "attach", "chat_id": "chat-9"})

    assert transport.of("attached")[-1]["team_binding"] == _BINDING_DICT
