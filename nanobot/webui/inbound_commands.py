"""Application orchestration for typed WebUI WebSocket commands."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from loguru import logger
from websockets.asyncio.server import ServerConnection

from nanobot.bus.events import INBOUND_META_USER_SHELL
from nanobot.command.builtin import USER_SHELL_COMMAND, builtin_command_starts_agent_turn
from nanobot.runtime_context import (
    RUNTIME_CONTEXT_INPUT_META,
    WEBUI_QUOTE_METADATA,
    RuntimeContextBlock,
    webui_quote_runtime_context,
)
from nanobot.security.workspace_access import (
    WORKSPACE_SCOPE_METADATA_KEY,
    WorkspaceScopeError,
)
from nanobot.session.model_selection import SESSION_REASONING_EFFORT_METADATA_KEY
from nanobot.session.webui_turns import (
    clear_websocket_turn_if_current,
    clear_websocket_turns,
    register_queued_websocket_turn_if_idle,
    websocket_turn_id,
    websocket_turn_wall_started_at,
)
from nanobot.teams.binding import SESSION_TEAM_BINDING_KEY, TeamBindingExpired
from nanobot.teams.models import TeamBinding
from nanobot.utils.helpers import safe_filename
from nanobot.webui.cli_apps_api import normalize_cli_app_mentions
from nanobot.webui.forking import handle_webui_fork_chat
from nanobot.webui.gateway_services import GatewayServices
from nanobot.webui.mcp_presets_api import normalize_mcp_preset_mentions
from nanobot.webui.metadata import WEBSOCKET_TURN_OWNER_METADATA_KEY
from nanobot.webui.outbound_wire import (
    encode_terminal_list_result,
    encode_terminal_started,
)
from nanobot.webui.pty_terminal import SHELL_NAME_RE
from nanobot.webui.session_access import (
    SessionMention,
    WebuiSessionAccess,
    session_mentions_runtime_context,
)
from nanobot.webui.session_identity import (
    WEBUI_SESSION_STORAGE_PREFIX,
    is_valid_webui_chat_id,
    webui_session_key,
)
from nanobot.webui.sidebar_state import write_webui_sidebar_state
from nanobot.webui.teams_routes import translate
from nanobot.webui.temporary_chats import TemporaryChatError
from nanobot.webui.transcription_ws import webui_transcription_event

_WEBUI_REQUEST_CACHE_TTL_S = 5 * 60.0
_WEBUI_REQUEST_CACHE_MAX = 256
_TERMINAL_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MAX_TERMINAL_WRITE_CHARS = 64_000
_MAX_TEAM_ID_CHARS = 128


def _team_selection(envelope: dict[str, Any]) -> tuple[str | None, int | None, str | None]:
    """读 ``new_chat`` 的团队选择，返回 ``(team_id, team_revision, 非法提示)``。

    没带 team_id 就是普通聊天（契约 §4）；省略 team_revision 时交服务端解析最新修订。
    这里只做形状检查——团队是否存在、能不能用由服务端准入检查决定，客户端说的不算。
    """
    raw_id = envelope.get("team_id")
    if raw_id is None:
        return None, None, None
    if not isinstance(raw_id, str):
        return None, None, "TEAM_INVALID team_id must be a string"
    team_id = raw_id.strip()
    if not team_id or len(team_id) > _MAX_TEAM_ID_CHARS:
        return None, None, "TEAM_INVALID team_id must be a non-empty id"
    raw_revision = envelope.get("team_revision")
    if raw_revision is None:
        return team_id, None, None
    if isinstance(raw_revision, bool) or not isinstance(raw_revision, int) or raw_revision < 1:
        return None, None, "TEAM_INVALID team_revision must be a positive integer"
    return team_id, raw_revision, None


@dataclass(frozen=True)
class WebUIRequestResult:
    result: Any = None
    status: int | None = None
    message: str | None = None


@dataclass
class WebUIRequestOperation:
    action: str
    payload_digest: bytes
    task: asyncio.Task[WebUIRequestResult]
    completed_at: float | None = None


class WebUICommandTransport(Protocol):
    """Typed transport capabilities consumed by WebUI command orchestration."""

    def is_allowed(self, sender_id: str) -> bool: ...

    def webui_subscribers(self, chat_id: str) -> tuple[ServerConnection, ...]: ...

    def webui_connection_chats(self, connection: ServerConnection) -> tuple[str, ...]: ...

    def webui_default_chat(self, connection: ServerConnection) -> str | None: ...

    def webui_attach(self, connection: ServerConnection, chat_id: str) -> None: ...

    def webui_detach(self, connection: ServerConnection, chat_id: str) -> None: ...

    def webui_clear_connection_default(self, connection: ServerConnection) -> None: ...

    def webui_clear_stream_buffers(self, chat_id: str) -> None: ...

    async def webui_hydrate(self, chat_id: str) -> None: ...

    async def webui_send_event(
        self,
        connection: ServerConnection,
        event: str,
        **fields: Any,
    ) -> None: ...

    async def webui_send_raw(
        self,
        connection: ServerConnection,
        raw: str,
        *,
        label: str = "",
    ) -> None: ...

    async def webui_dispatch_message(
        self,
        *,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None,
        metadata: dict[str, Any],
        is_dm: bool,
        session_key: str | None,
        require_existing_session: bool,
    ) -> None: ...

    async def send_session_updated(
        self,
        chat_id: str,
        *,
        scope: str | None = None,
    ) -> None: ...


class WebUICommandRouter:
    """Own WebUI command semantics while a transport host owns raw connections."""

    def __init__(self, transport: WebUICommandTransport, gateway: GatewayServices) -> None:
        self._transport = transport
        self.gateway = gateway
        self._http_router = gateway.http
        self._media = gateway.media
        self._ingress = gateway.ingress
        self._transcripts = gateway.transcripts
        self._workspaces = gateway.workspaces
        self._temporary_chats = gateway.temporary_chats
        self._session_projection = gateway.session_projection
        self._team_bindings = gateway.team_bindings
        self._webui_connections = gateway.endpoint.webui_connections
        self._session_access = (
            WebuiSessionAccess(gateway.session_manager)
            if gateway.session_manager is not None
            else None
        )
        self.request_tasks: dict[
            tuple[ServerConnection, str],
            asyncio.Task[None],
        ] = {}
        self.request_operations: dict[str, WebUIRequestOperation] = {}
        self.request_locks: dict[ServerConnection, asyncio.Lock] = {}
        self._terminal_bridge_instance: Any | None = None

    def workspace_controls_available(self, connection: ServerConnection) -> bool:
        return self._http_router.workspace_controls_available(connection)

    async def send_webui_protocol_error(
        self,
        connection: ServerConnection,
        detail: str,
    ) -> None:
        await self._transport.webui_send_event(connection, "error", detail=detail)

    async def attach_webui_fork(
        self,
        connection: ServerConnection,
        *,
        fork_id: str,
        fork_key: str,
    ) -> None:
        scope = self._workspaces.scope_for_session_key(fork_key)
        self._transport.webui_attach(connection, fork_id)
        await self._transport.webui_send_event(
            connection,
            "attached",
            chat_id=fork_id,
            **self._session_projection.attach_fields(fork_key),
        )
        await self._transport.webui_send_event(
            connection,
            "session_updated",
            chat_id=fork_id,
            scope="metadata",
            workspace_scope=scope.payload(),
        )
        await self._transport.webui_hydrate(fork_id)

    async def discard_owned_chat(
        self,
        connection: ServerConnection,
        chat_id: str,
    ) -> None:
        await self._temporary_chats.discard(connection, chat_id)
        self._transport.webui_detach(connection, chat_id)
        clear_websocket_turns(chat_id)
        self._transport.webui_clear_stream_buffers(chat_id)

    async def cleanup_connection(self, connection: ServerConnection) -> None:
        """Release command-owned state associated with one transport connection."""
        chat_ids = self._transport.webui_connection_chats(connection)
        for chat_id in chat_ids:
            if self._temporary_chats.owns(connection, chat_id):
                await self.discard_owned_chat(connection, chat_id)
            else:
                self._transport.webui_detach(connection, chat_id)
        for chat_id in self._temporary_chats.chat_ids_for_owner(connection):
            await self.discard_owned_chat(connection, chat_id)
        self._transport.webui_clear_connection_default(connection)
        self.gateway.endpoint.discard_connection(connection)
        self.discard_request_lock_if_idle(connection)

    async def broadcast_webui_event(self, event: str, **fields: Any) -> None:
        for connection in tuple(self._webui_connections):
            await self._transport.webui_send_event(connection, event, **fields)

    async def broadcast_user_message(
        self,
        origin: ServerConnection,
        chat_id: str,
        text: str,
        *,
        turn_id: str | None,
        starts_turn: bool,
        media_paths: list[str],
        media_names: list[str | None],
        cli_apps: list[dict[str, Any]],
        mcp_presets: list[dict[str, Any]],
        session_mentions: list[SessionMention],
    ) -> None:
        body: dict[str, Any] = {
            "event": "user_message",
            "chat_id": chat_id,
            "text": text,
            "starts_turn": starts_turn,
        }
        if turn_id is not None:
            body["turn_id"] = turn_id
        media = self._media.augment_transcript_user_media(media_paths)
        for attachment, name in zip(media, media_names, strict=False):
            if name:
                attachment["name"] = name
        if media:
            body["media_urls"] = media
        if cli_apps:
            body["cli_apps"] = cli_apps
        if mcp_presets:
            body["mcp_presets"] = mcp_presets
        if session_mentions:
            body["session_mentions"] = session_mentions
        active_turn_id = websocket_turn_id(chat_id)
        if active_turn_id is not None:
            body["active_turn_id"] = active_turn_id
        started_at = websocket_turn_wall_started_at(chat_id)
        if active_turn_id is not None and started_at is not None:
            body["started_at"] = started_at
        raw = json.dumps(body, ensure_ascii=False)
        for connection in self._transport.webui_subscribers(chat_id):
            if connection is not origin:
                await self._transport.webui_send_raw(connection, raw, label=" user_message ")

    async def workspace_scope_or_error(
        self,
        connection: ServerConnection,
        resolver: Callable[[], Any],
        *,
        chat_id: str | None = None,
        turn_id: str | None = None,
    ) -> Any | None:
        try:
            return resolver()
        except WorkspaceScopeError as exc:
            await self._transport.webui_send_event(
                connection,
                "error",
                detail="workspace_scope_rejected",
                reason=exc.message,
                **({"chat_id": chat_id} if chat_id else {}),
                **({"turn_id": turn_id} if turn_id else {}),
            )
            return None

    async def _stage_team_binding(
        self,
        connection: ServerConnection,
        chat_id: str,
        *,
        team_id: str,
        team_revision: int | None,
    ) -> TeamBinding | None:
        """服务端暂存新聊天的团队绑定；失败时回契约错误码，聊天不会建成。

        只暂存不落盘（契约 §4 的懒创建）：绑定要等首条消息写进会话元数据后才启动 Leader。
        """
        store = self._team_bindings
        if store is None:
            await self._transport.webui_send_event(
                connection,
                "error",
                detail="TEAMS_DISABLED team support is not wired",
            )
            return None
        try:
            return await asyncio.to_thread(
                store.stage,
                chat_id=chat_id,
                team_id=team_id,
                revision=team_revision,
            )
        except Exception as exc:  # 领域异常 → 契约 §4 错误码；内部细节只进日志
            status, message = translate(exc)
            logger.warning("team staging failed for a new chat: {} {}", status, message)
            await self._transport.webui_send_event(connection, "error", detail=message)
            return None

    async def _reject_expired_team_binding(
        self,
        connection: ServerConnection,
        chat_id: str,
    ) -> bool:
        """空聊天重连时暂存已失效 → 报 ``TEAM_BINDING_EXPIRED`` 要求重选（契约 §4）。

        只认「暂存过、但已过期」这一种状态：普通聊天从来没暂存过，已经绑定的会话在首条
        消息时就取走了暂存，两者都不该收到这个错误，也就都不该被它拦住。
        """
        store = self._team_bindings
        if store is None:
            return False
        expired = store.expired_binding(chat_id)
        if expired is None:
            return False
        await self._transport.webui_send_event(
            connection,
            "error",
            detail=f"TEAM_BINDING_EXPIRED chat_id={chat_id}",
            chat_id=chat_id,
            team_name=expired.team_name,
        )
        return True

    async def _claim_team_binding(self, chat_id: str) -> TeamBinding | None:
        """取走本聊天待生效的绑定；过期时抛出 ``TeamBindingExpired`` 由调用方拦住消息。"""
        store = self._team_bindings
        if store is None:
            return None
        return await asyncio.to_thread(store.claim, chat_id)

    async def dispatch(
        self,
        connection: ServerConnection,
        client_id: str,
        envelope: dict[str, Any],
    ) -> None:
        """Execute one typed WebUI command."""
        command_type = envelope.get("type")
        if command_type == "webui_request":
            await self.start_webui_request(connection, envelope)
            return
        if command_type == "new_chat":
            new_id = str(uuid.uuid4())
            team_id, team_revision, team_rejection = _team_selection(envelope)
            if team_rejection is not None:
                await self._transport.webui_send_event(
                    connection,
                    "error",
                    detail=team_rejection,
                )
                return
            scope = await self.workspace_scope_or_error(
                connection,
                lambda: self._workspaces.scope_for_new_chat(
                    envelope,
                    controls_available=self.workspace_controls_available(connection),
                ),
            )
            if scope is None:
                return
            self._workspaces.stage_scope(new_id, scope)
            binding: TeamBinding | None = None
            if team_id is not None:
                binding = await self._stage_team_binding(
                    connection,
                    new_id,
                    team_id=team_id,
                    team_revision=team_revision,
                )
                if binding is None:
                    # 准入不过就什么都不建：留下的草稿范围会被下一条普通消息误用。
                    self._workspaces.discard_draft_scope(webui_session_key(new_id))
                    return
            self._transport.webui_attach(connection, new_id)
            fields = self._session_projection.attach_fields(webui_session_key(new_id))
            fields["team_binding"] = binding.to_dict() if binding is not None else None
            await self._transport.webui_send_event(
                connection,
                "attached",
                chat_id=new_id,
                **fields,
            )
            await self._transport.webui_send_event(
                connection,
                "session_updated",
                chat_id=new_id,
                scope="metadata",
                workspace_scope=scope.payload(),
                team_binding=fields["team_binding"],
            )
            await self._transport.webui_hydrate(new_id)
            return
        if command_type == "new_temporary_chat":
            try:
                new_id = self._temporary_chats.create(
                    connection,
                    trusted_webui=connection in self._webui_connections,
                )
            except TemporaryChatError as exc:
                await self._transport.webui_send_event(connection, "error", detail=exc.detail)
                return
            self._transport.webui_attach(connection, new_id)
            await self._transport.webui_send_event(
                connection,
                "attached",
                chat_id=new_id,
                temporary=True,
            )
            return
        if command_type == "fork_chat":
            await handle_webui_fork_chat(self, connection, envelope)
            return
        if command_type == "discard_temporary_chat":
            chat_id = envelope.get("chat_id")
            if not is_valid_webui_chat_id(chat_id):
                await self._transport.webui_send_event(
                    connection,
                    "error",
                    detail="invalid temporary chat_id",
                )
                return
            try:
                await self.discard_owned_chat(connection, chat_id)
            except TemporaryChatError as exc:
                await self._transport.webui_send_event(
                    connection,
                    "error",
                    detail=exc.detail,
                    chat_id=chat_id,
                )
            return
        if command_type == "attach":
            chat_id = envelope.get("chat_id")
            if not is_valid_webui_chat_id(chat_id):
                await self._transport.webui_send_event(
                    connection,
                    "error",
                    detail="invalid chat_id",
                )
                return
            try:
                self._temporary_chats.validate_attach(chat_id)
            except TemporaryChatError as exc:
                await self._transport.webui_send_event(
                    connection,
                    "error",
                    detail=exc.detail,
                    chat_id=chat_id,
                )
                return
            if await self._reject_expired_team_binding(connection, chat_id):
                return
            self._transport.webui_attach(connection, chat_id)
            await self._transport.webui_send_event(
                connection,
                "attached",
                chat_id=chat_id,
                **self._session_projection.attach_fields(webui_session_key(chat_id)),
            )
            await self._transport.webui_hydrate(chat_id)
            return
        if command_type == "set_sidebar_state":
            if connection not in self._webui_connections:
                await self._transport.webui_send_event(connection, "error", detail="access_denied")
                return
            state = envelope.get("state")
            if not isinstance(state, dict):
                await self._transport.webui_send_event(
                    connection,
                    "error",
                    detail="invalid_sidebar_state",
                )
                return
            try:
                saved_state = await asyncio.to_thread(
                    write_webui_sidebar_state,
                    cast(dict[str, Any], state),
                )
            except (OSError, ValueError):
                await self._transport.webui_send_event(
                    connection,
                    "error",
                    detail="invalid_sidebar_state",
                )
                return
            await self.broadcast_webui_event("sidebar_state_updated", state=saved_state)
            return
        if command_type == "set_workspace_scope":
            chat_id = envelope.get("chat_id")
            if not is_valid_webui_chat_id(chat_id):
                await self._transport.webui_send_event(
                    connection,
                    "error",
                    detail="invalid chat_id",
                )
                return
            try:
                self._temporary_chats.validate_workspace_update(chat_id)
            except TemporaryChatError as exc:
                await self._transport.webui_send_event(
                    connection,
                    "error",
                    detail=exc.detail,
                    chat_id=chat_id,
                )
                return
            scope = await self.workspace_scope_or_error(
                connection,
                lambda: self._workspaces.scope_for_set_request(
                    envelope,
                    chat_id=chat_id,
                    chat_running=websocket_turn_wall_started_at(chat_id) is not None,
                    controls_available=self.workspace_controls_available(connection),
                ),
                chat_id=chat_id,
            )
            if scope is None:
                return
            self._workspaces.stage_scope(chat_id, scope)
            await self._transport.send_session_updated(chat_id, scope="metadata")
            await self._transport.webui_send_event(
                connection,
                "session_updated",
                chat_id=chat_id,
                scope="metadata",
                workspace_scope=scope.payload(),
            )
            return
        if command_type == "terminal_start":
            await self._handle_terminal_start(connection, client_id, envelope)
            return
        if command_type == "terminal_write":
            await self._handle_terminal_write(connection, client_id, envelope)
            return
        if command_type == "terminal_resize":
            await self._handle_terminal_resize(connection, client_id, envelope)
            return
        if command_type == "terminal_kill":
            await self._handle_terminal_kill(connection, client_id, envelope)
            return
        if command_type == "terminal_list":
            await self._handle_terminal_list(connection, client_id, envelope)
            return
        if command_type == "set_reasoning_effort":
            await self._handle_set_reasoning_effort(connection, envelope)
            return
        if command_type == "transcribe_audio":
            event, payload = await webui_transcription_event(
                envelope,
                config_path=self.gateway.settings.config.path,
            )
            await self._transport.webui_send_event(connection, event, **payload)
            return
        if command_type == "question_answer":
            await self._handle_question_answer(connection, envelope)
            return
        if command_type == "message":
            await self._dispatch_message(connection, client_id, envelope)
            return
        await self._transport.webui_send_event(
            connection,
            "error",
            detail=f"unknown type: {command_type!r}",
        )

    # -- terminal frames (contract 2026-10-11 §1) ----------------------------

    def _terminal_bridge(self) -> Any:
        if self._terminal_bridge_instance is None:
            from nanobot.agent.tools.exec_session import ExecSessionManager
            from nanobot.webui.pty_terminal import (
                DEFAULT_MAX_TERMINALS as _TERMINAL_MAX,
            )
            from nanobot.webui.pty_terminal import create_terminal_bridge
            from nanobot.webui.terminal_bridge import DEFAULT_TERMINAL_IDLE_TIMEOUT_S

            exec_config = None
            allow_local_service_access = True
            try:
                from nanobot.config.loader import load_config

                config = load_config(self.gateway.settings.config.path)
                exec_config = config.tools.exec
                allow_local_service_access = config.tools.webui_allow_local_service_access
            except Exception:
                logger.warning(
                    "terminal bridge could not read exec config; using defaults"
                )
            manager = ExecSessionManager(
                max_sessions=_TERMINAL_MAX,
                idle_timeout=DEFAULT_TERMINAL_IDLE_TIMEOUT_S,
            )
            self._terminal_bridge_instance = create_terminal_bridge(
                manager,
                exec_config=exec_config,
                webui_allow_local_service_access=allow_local_service_access,
            )
        return self._terminal_bridge_instance

    def _terminal_publisher(self, chat_id: str) -> Callable[[dict[str, Any]], Any]:
        transport = self._transport

        async def publish(payload: dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False)
            for connection in transport.webui_subscribers(chat_id):
                await transport.webui_send_raw(connection, raw, label=" terminal ")

        return publish

    async def _publish_terminal(
        self,
        chat_id: str,
        origin: ServerConnection,
        payload: dict[str, Any],
    ) -> None:
        """Fan a terminal event out to chat subscribers; the origin always receives it."""
        raw = json.dumps(payload, ensure_ascii=False)
        delivered: set[int] = set()
        for connection in self._transport.webui_subscribers(chat_id):
            delivered.add(id(connection))
            await self._transport.webui_send_raw(connection, raw, label=" terminal ")
        if id(origin) not in delivered:
            await self._transport.webui_send_raw(origin, raw, label=" terminal ")

    def _resolve_terminal_chat(
        self,
        connection: ServerConnection,
        raw_session_key: Any,
    ) -> str | None:
        if raw_session_key is not None:
            if not isinstance(raw_session_key, str):
                return None
            candidate = (
                raw_session_key[len(WEBUI_SESSION_STORAGE_PREFIX):]
                if raw_session_key.startswith(WEBUI_SESSION_STORAGE_PREFIX)
                else raw_session_key
            )
            if not is_valid_webui_chat_id(candidate):
                return None
            if candidate not in self._transport.webui_connection_chats(connection):
                return None
            return candidate
        default_chat = self._transport.webui_default_chat(connection)
        if isinstance(default_chat, str) and is_valid_webui_chat_id(default_chat):
            return default_chat
        chats = self._transport.webui_connection_chats(connection)
        if len(chats) == 1:
            return chats[0]
        return None

    def _validate_terminal_id(self, envelope: dict[str, Any]) -> str | None:
        terminal_id = envelope.get("terminal_id")
        if isinstance(terminal_id, str) and _TERMINAL_ID_RE.fullmatch(terminal_id):
            return terminal_id
        return None

    async def _terminal_access_error(
        self,
        connection: ServerConnection,
        detail: str,
        *,
        terminal_id: str | None = None,
    ) -> None:
        await self._transport.webui_send_event(
            connection,
            "error",
            detail=detail,
            **({"terminal_id": terminal_id} if terminal_id else {}),
        )

    async def _handle_terminal_start(
        self,
        connection: ServerConnection,
        client_id: str,
        envelope: dict[str, Any],
    ) -> None:
        if connection not in self._webui_connections or not self._transport.is_allowed(client_id):
            await self._terminal_access_error(connection, "access_denied")
            return
        terminal_id = self._validate_terminal_id(envelope)
        if terminal_id is None:
            await self._terminal_access_error(connection, "invalid_terminal_id")
            return
        chat_id = self._resolve_terminal_chat(connection, envelope.get("session_key"))
        if chat_id is None:
            await self._terminal_access_error(
                connection, "terminal_session_unavailable", terminal_id=terminal_id
            )
            return
        try:
            scope = self._workspaces.scope_for_session_key(webui_session_key(chat_id))
        except WorkspaceScopeError as exc:
            await self._transport.webui_send_event(
                connection,
                "error",
                detail="workspace_scope_rejected",
                reason=exc.message,
                terminal_id=terminal_id,
            )
            return
        raw_shell = envelope.get("shell")
        if raw_shell is not None and (
            not isinstance(raw_shell, str) or not SHELL_NAME_RE.fullmatch(raw_shell)
        ):
            payload = encode_terminal_started(
                terminal_id=terminal_id,
                shell="",
                cwd=str(scope.project_path),
                status="error",
                message="shell_unavailable",
            )
            await self._publish_terminal(chat_id, connection, payload)
            return
        bridge = self._terminal_bridge()
        session_key = webui_session_key(chat_id)
        result = await bridge.start(
            terminal_id=terminal_id,
            scope=scope,
            chat_id=chat_id,
            session_key=session_key,
            publisher=self._terminal_publisher(chat_id),
            shell=raw_shell if isinstance(raw_shell, str) else None,
        )
        payload = encode_terminal_started(
            terminal_id=terminal_id,
            shell=str(result.get("shell") or ""),
            cwd=str(result.get("cwd") or scope.project_path),
            status=result.get("status", "error"),
            message=result.get("message"),
            pty=bool(result.get("pty")),
        )
        await self._publish_terminal(chat_id, connection, payload)

    async def _handle_terminal_write(
        self,
        connection: ServerConnection,
        client_id: str,
        envelope: dict[str, Any],
    ) -> None:
        if connection not in self._webui_connections or not self._transport.is_allowed(client_id):
            await self._terminal_access_error(connection, "access_denied")
            return
        terminal_id = self._validate_terminal_id(envelope)
        if terminal_id is None:
            await self._terminal_access_error(connection, "invalid_terminal_id")
            return
        data = envelope.get("data")
        if (
            not isinstance(data, str)
            or not data
            or len(data) > _MAX_TERMINAL_WRITE_CHARS
        ):
            await self._terminal_access_error(
                connection, "invalid_terminal_data", terminal_id=terminal_id
            )
            return
        bridge = self._terminal_bridge_instance
        owner = bridge.owner_chat(terminal_id) if bridge is not None else None
        if owner is None:
            await self._terminal_access_error(
                connection, "unknown_terminal", terminal_id=terminal_id
            )
            return
        if owner not in self._transport.webui_connection_chats(connection):
            await self._terminal_access_error(
                connection, "terminal_not_attached", terminal_id=terminal_id
            )
            return
        error = await bridge.write(terminal_id=terminal_id, data=data)
        if error:
            await self._terminal_access_error(
                connection, error, terminal_id=terminal_id
            )

    async def _handle_terminal_resize(
        self,
        connection: ServerConnection,
        client_id: str,
        envelope: dict[str, Any],
    ) -> None:
        if connection not in self._webui_connections or not self._transport.is_allowed(client_id):
            await self._terminal_access_error(connection, "access_denied")
            return
        terminal_id = self._validate_terminal_id(envelope)
        if terminal_id is None:
            await self._terminal_access_error(connection, "invalid_terminal_id")
            return
        from nanobot.webui.pty_terminal import MAX_PTY_COLS, MAX_PTY_ROWS

        cols, rows = envelope.get("cols"), envelope.get("rows")
        if (
            not isinstance(cols, int) or isinstance(cols, bool) or not 1 <= cols <= MAX_PTY_COLS
            or not isinstance(rows, int) or isinstance(rows, bool) or not 1 <= rows <= MAX_PTY_ROWS
        ):
            await self._terminal_access_error(
                connection, "invalid_terminal_resize", terminal_id=terminal_id
            )
            return
        bridge = self._terminal_bridge_instance
        owner = bridge.owner_chat(terminal_id) if bridge is not None else None
        if owner is None:
            await self._terminal_access_error(
                connection, "unknown_terminal", terminal_id=terminal_id
            )
            return
        if owner not in self._transport.webui_connection_chats(connection):
            await self._terminal_access_error(
                connection, "terminal_not_attached", terminal_id=terminal_id
            )
            return
        error = await bridge.resize(terminal_id=terminal_id, cols=cols, rows=rows)
        if error:
            await self._terminal_access_error(connection, error, terminal_id=terminal_id)

    async def _handle_terminal_kill(
        self,
        connection: ServerConnection,
        client_id: str,
        envelope: dict[str, Any],
    ) -> None:
        if connection not in self._webui_connections or not self._transport.is_allowed(client_id):
            await self._terminal_access_error(connection, "access_denied")
            return
        terminal_id = self._validate_terminal_id(envelope)
        if terminal_id is None:
            await self._terminal_access_error(connection, "invalid_terminal_id")
            return
        bridge = self._terminal_bridge_instance
        owner = bridge.owner_chat(terminal_id) if bridge is not None else None
        if owner is None and bridge is not None:
            owner = bridge.exited_owner(terminal_id)  # 60s grace: kill is idempotent
        if owner is None:
            await self._terminal_access_error(
                connection, "unknown_terminal", terminal_id=terminal_id
            )
            return
        if owner not in self._transport.webui_connection_chats(connection):
            await self._terminal_access_error(
                connection, "terminal_not_attached", terminal_id=terminal_id
            )
            return
        error = await bridge.kill(terminal_id=terminal_id)
        if error:
            await self._terminal_access_error(
                connection, error, terminal_id=terminal_id
            )

    async def _handle_terminal_list(
        self,
        connection: ServerConnection,
        client_id: str,
        envelope: dict[str, Any],
    ) -> None:
        if connection not in self._webui_connections or not self._transport.is_allowed(client_id):
            await self._terminal_access_error(connection, "access_denied")
            return
        bridge = self._terminal_bridge()
        terminals = bridge.list_visible(set(self._transport.webui_connection_chats(connection)))
        payload = encode_terminal_list_result(terminals)
        await self._transport.webui_send_event(
            connection,
            "terminal_list_result",
            **{key: value for key, value in payload.items() if key != "event"},
        )

    async def _handle_question_answer(
        self,
        connection: ServerConnection,
        envelope: dict[str, Any],
    ) -> None:
        """Resolve a pending ask_question future with the user's answer."""
        from nanobot.agent.questions import resolve_answer

        question_id = envelope.get("question_id")
        answer = envelope.get("answer")
        if (
            not isinstance(question_id, str)
            or not question_id
            or not isinstance(answer, str)
            or not answer.strip()
            or len(answer) > 4000
        ):
            await self._transport.webui_send_event(
                connection,
                "error",
                detail="invalid_question_answer",
            )
            return
        if not resolve_answer(question_id, answer.strip()):
            await self._transport.webui_send_event(
                connection,
                "error",
                detail="unknown_question",
                chat_id=envelope.get("chat_id")
                if isinstance(envelope.get("chat_id"), str)
                else None,
            )

    async def _handle_set_reasoning_effort(
        self,
        connection: ServerConnection,
        envelope: dict[str, Any],
    ) -> None:
        chat_id = envelope.get("chat_id")
        raw_effort = envelope.get("reasoning_effort")
        sessions = self.gateway.session_manager
        if not is_valid_webui_chat_id(chat_id) or not isinstance(raw_effort, str) or sessions is None:
            await self._transport.webui_send_event(
                connection,
                "error",
                detail="invalid_reasoning_effort",
            )
            return
        effort = raw_effort.strip()
        session = sessions.get_or_create(webui_session_key(chat_id))
        if effort:
            try:
                allowed = self._reasoning_effort_options(session)
            except Exception:
                allowed = None
            if allowed is not None and effort not in allowed:
                await self._transport.webui_send_event(
                    connection,
                    "error",
                    detail="invalid_reasoning_effort",
                    chat_id=chat_id,
                )
                return
            session.metadata[SESSION_REASONING_EFFORT_METADATA_KEY] = effort
        else:
            session.metadata.pop(SESSION_REASONING_EFFORT_METADATA_KEY, None)
        sessions.save(session)
        await self._transport.send_session_updated(chat_id, scope="metadata")
        await self._transport.webui_send_event(
            connection,
            "session_updated",
            chat_id=chat_id,
            scope="metadata",
            reasoning_effort=effort,
        )

    def _reasoning_effort_options(self, session: Any) -> list[str]:
        from nanobot.config.loader import load_config
        from nanobot.session.model_selection import model_preset_from_metadata
        from nanobot.webui.settings_models import reasoning_effort_values_for

        config = load_config(self.gateway.settings.config.path)
        try:
            preset_name = model_preset_from_metadata(session.metadata)
        except ValueError:
            preset_name = None
        try:
            preset = config.resolve_preset(preset_name)
        except KeyError:
            preset = config.resolve_preset(None)
        return reasoning_effort_values_for(preset.provider, preset.model)

    async def _dispatch_message(
        self,
        connection: ServerConnection,
        client_id: str,
        envelope: dict[str, Any],
    ) -> None:
        chat_id = envelope.get("chat_id")
        content = envelope.get("content")
        if not is_valid_webui_chat_id(chat_id):
            await self._transport.webui_send_event(connection, "error", detail="invalid chat_id")
            return
        raw_turn_id = envelope.get("turn_id")
        turn_id = raw_turn_id if isinstance(raw_turn_id, str) and raw_turn_id else None
        rejection_fields = {
            "chat_id": chat_id,
            **({"turn_id": turn_id} if turn_id else {}),
        }
        if not self._transport.is_allowed(client_id):
            await self._transport.webui_send_event(
                connection,
                "error",
                detail="access_denied",
                **rejection_fields,
            )
            return
        if not isinstance(content, str):
            await self._transport.webui_send_event(
                connection,
                "error",
                detail="missing content",
                **rejection_fields,
            )
            return
        message_rejection = self._ingress.validate_text(content)
        if message_rejection is not None:
            await self._transport.webui_send_event(
                connection,
                "error",
                detail="message_rejected",
                reason=message_rejection,
                **rejection_fields,
            )
            return

        try:
            temporary_policy = self._temporary_chats.message_policy(
                connection,
                chat_id,
                content,
            )
        except TemporaryChatError as exc:
            await self._transport.webui_send_event(
                connection,
                "error",
                detail=exc.detail,
                **rejection_fields,
            )
            return

        raw_media = envelope.get("media")
        media_paths: list[str] = []
        media_names: list[str | None] = []
        if raw_media is not None:
            if not isinstance(raw_media, list):
                await self._transport.webui_send_event(
                    connection,
                    "error",
                    detail="attachment_rejected",
                    reason="malformed",
                    **rejection_fields,
                )
                return
            media_paths, reason = self._media.store_inbound_attachments(
                cast(list[Any], raw_media)
            )
            if reason is not None:
                await self._transport.webui_send_event(
                    connection,
                    "error",
                    detail="attachment_rejected",
                    reason=reason,
                    **rejection_fields,
                )
                return
            for item in cast(list[Any], raw_media):
                attachment = cast(dict[str, Any], item) if isinstance(item, dict) else {}
                name = attachment.get("name")
                media_names.append((safe_filename(name) or None) if isinstance(name, str) else None)
            if temporary_policy is not None:
                self._temporary_chats.register_media(connection, chat_id, media_paths)

        if not content.strip() and not media_paths:
            await self._transport.webui_send_event(
                connection,
                "error",
                detail="missing content",
                **rejection_fields,
            )
            return
        self._transport.webui_attach(connection, chat_id)
        if temporary_policy is None or temporary_policy.hydrate_transcript:
            await self._transport.webui_hydrate(chat_id)

        scope = await self.workspace_scope_or_error(
            connection,
            lambda: (
                temporary_policy.workspace_scope
                if temporary_policy is not None
                else self._workspaces.scope_for_message(
                    envelope,
                    chat_id=chat_id,
                    chat_running=websocket_turn_wall_started_at(chat_id) is not None,
                    controls_available=self.workspace_controls_available(connection),
                )
            ),
            chat_id=chat_id,
            turn_id=turn_id,
        )
        if scope is None:
            return

        if not self._transport.is_allowed(client_id):
            await self._transport.webui_send_event(
                connection,
                "error",
                detail="access_denied",
                **rejection_fields,
            )
            return

        metadata: dict[str, Any] = {
            "remote": getattr(connection, "remote_address", None)
        }
        if envelope.get("webui") is True:
            metadata["webui"] = True
            metadata.update(self._transcripts.client_turn_metadata(envelope.get("turn_id")))
        trusted_webui = metadata.get("webui") is True and connection in self._webui_connections
        is_user_shell = (
            trusted_webui
            and envelope.get("user_shell") is True
            and content.startswith("!")
        )
        if is_user_shell:
            metadata[INBOUND_META_USER_SHELL] = True
        dispatch_content = (
            f"{USER_SHELL_COMMAND} {content[1:].lstrip()}" if is_user_shell else content
        )
        cli_apps = normalize_cli_app_mentions(envelope.get("cli_apps"))
        if cli_apps:
            metadata["cli_apps"] = cli_apps
        mcp_presets = normalize_mcp_preset_mentions(
            envelope.get("mcp_presets"),
            config_path=self.gateway.settings.config.path,
        )
        if mcp_presets:
            metadata["mcp_presets"] = mcp_presets
        session_mentions: list[SessionMention] = []
        if trusted_webui and self._session_access is not None:
            session_mentions = await asyncio.to_thread(
                self._session_access.normalize_mentions,
                envelope.get("session_mentions"),
                exclude_session_key=webui_session_key(chat_id),
            )
            if session_mentions:
                metadata["session_mentions"] = session_mentions
        metadata[WORKSPACE_SCOPE_METADATA_KEY] = scope.metadata()
        # 团队绑定在发布这条消息之前取走：Leader 装配和会话落盘读同一份，「先普通回答、
        # 再变团队」就没有窗口（契约 §4）。取不到绑定的普通聊天照原样跑。
        # 只有完成过 WebUI 握手的连接才能取暂存：别的通道即便把 team_binding 塞进自己
        # 的 metadata 也没有服务端暂存可消费，注入面在这里就被关掉了。
        if trusted_webui:
            try:
                team_binding = await self._claim_team_binding(chat_id)
            except TeamBindingExpired as exc:
                await self._transport.webui_send_event(
                    connection,
                    "error",
                    detail=str(exc),
                    **rejection_fields,
                )
                return
            if team_binding is not None:
                metadata[SESSION_TEAM_BINDING_KEY] = team_binding.to_dict()
        is_webui = metadata.get("webui") is True
        queued_owner = None
        if is_webui and not is_user_shell and builtin_command_starts_agent_turn(content):
            queued_owner = register_queued_websocket_turn_if_idle(chat_id, turn_id)
            if queued_owner is not None:
                metadata[WEBSOCKET_TURN_OWNER_METADATA_KEY] = queued_owner

        accepted = False
        try:
            if is_webui and (
                temporary_policy is None or temporary_policy.persist_transcript
            ):
                self._transcripts.append_user_message(
                    chat_id,
                    content,
                    metadata=metadata,
                    media_paths=media_paths or None,
                    cli_apps=cli_apps or None,
                    mcp_presets=mcp_presets or None,
                    session_mentions=session_mentions or None,
                )
            if trusted_webui:
                context_blocks: list[RuntimeContextBlock] = []
                quote = webui_quote_runtime_context(
                    {WEBUI_QUOTE_METADATA: envelope.get("quoted_context")}
                )
                if quote is not None:
                    context_blocks.append(quote)
                session_context = session_mentions_runtime_context(session_mentions)
                if session_context is not None:
                    context_blocks.append(session_context)
                if context_blocks:
                    metadata[RUNTIME_CONTEXT_INPUT_META] = context_blocks
            await self._transport.webui_dispatch_message(
                sender_id=client_id,
                chat_id=chat_id,
                content=dispatch_content,
                media=media_paths or None,
                metadata=metadata,
                is_dm=False,
                session_key=(
                    temporary_policy.session_key if temporary_policy is not None else None
                ),
                require_existing_session=(
                    temporary_policy.require_existing_session
                    if temporary_policy is not None
                    else False
                ),
            )
            self._workspaces.persist_scope(chat_id, scope)
            accepted = True
        finally:
            if not accepted and queued_owner is not None:
                clear_websocket_turn_if_current(chat_id, queued_owner)

        if is_webui:
            await self.broadcast_user_message(
                connection,
                chat_id,
                content,
                turn_id=turn_id,
                starts_turn=queued_owner is not None,
                media_paths=media_paths,
                media_names=media_names,
                cli_apps=cli_apps,
                mcp_presets=mcp_presets,
                session_mentions=session_mentions,
            )
        if is_webui and turn_id:
            active_turn_id = websocket_turn_id(chat_id)
            started_at = websocket_turn_wall_started_at(chat_id)
            await self._transport.webui_send_event(
                connection,
                "message_accepted",
                chat_id=chat_id,
                turn_id=turn_id,
                starts_turn=queued_owner is not None,
                **(
                    {"active_turn_id": active_turn_id}
                    if active_turn_id is not None
                    else {}
                ),
                **(
                    {"started_at": started_at}
                    if active_turn_id is not None and started_at is not None
                    else {}
                ),
            )

    async def start_webui_request(
        self,
        connection: ServerConnection,
        envelope: dict[str, Any],
    ) -> None:
        request_id = envelope.get("request_id")
        if not isinstance(request_id, str) or re.fullmatch(
            r"[A-Za-z0-9._:-]{1,128}",
            request_id,
        ) is None:
            await self._transport.webui_send_event(
                connection,
                "error",
                detail="invalid webui request_id",
            )
            return
        if connection not in self._webui_connections:
            await self.send_webui_response(
                connection,
                request_id,
                status=403,
                message="access_denied",
            )
            return

        action = envelope.get("action")
        payload = envelope.get("payload")
        if not isinstance(action, str) or re.fullmatch(
            r"[a-z][a-z0-9_.]{0,127}",
            action,
        ) is None:
            await self.send_webui_response(
                connection,
                request_id,
                status=400,
                message="invalid WebUI mutation action",
            )
            return
        if not isinstance(payload, dict):
            await self.send_webui_response(
                connection,
                request_id,
                status=400,
                message="WebUI mutation payload must be an object",
            )
            return

        payload_digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).digest()
        self.prune_request_operations()
        operation = self.request_operations.get(request_id)
        is_replay = operation is not None
        if operation is not None and (
            operation.action != action or operation.payload_digest != payload_digest
        ):
            await self.send_webui_response(
                connection,
                request_id,
                status=409,
                message="request_id was already used for a different WebUI mutation",
            )
            return
        if operation is None:
            operation_task = asyncio.create_task(
                self.execute_webui_request(
                    connection,
                    action,
                    cast(dict[str, Any], payload),
                )
            )
            new_operation = WebUIRequestOperation(
                action=action,
                payload_digest=payload_digest,
                task=operation_task,
            )
            operation = new_operation
            self.request_operations[request_id] = new_operation

            def mark_complete(_task: asyncio.Task[WebUIRequestResult]) -> None:
                current = self.request_operations.get(request_id)
                if current is not new_operation:
                    return
                new_operation.completed_at = time.monotonic()
                self.prune_request_operations()

            operation_task.add_done_callback(mark_complete)

        key = (connection, request_id)
        if key in self.request_tasks:
            return
        delivery_task = asyncio.create_task(
            self.deliver_webui_request(
                connection,
                request_id,
                operation.task,
                sequence=is_replay,
            )
        )
        self.request_tasks[key] = delivery_task

    def prune_request_operations(self) -> None:
        now = time.monotonic()
        for request_id, operation in tuple(self.request_operations.items()):
            if (
                operation.completed_at is not None
                and now - operation.completed_at >= _WEBUI_REQUEST_CACHE_TTL_S
            ):
                self.request_operations.pop(request_id, None)

        completed = sorted(
            (
                (operation.completed_at, request_id)
                for request_id, operation in self.request_operations.items()
                if operation.completed_at is not None
            ),
            key=lambda item: item[0],
        )
        for _, request_id in completed[:-_WEBUI_REQUEST_CACHE_MAX]:
            self.request_operations.pop(request_id, None)

    def discard_request_lock_if_idle(self, connection: ServerConnection) -> None:
        if connection in self._webui_connections:
            return
        if any(task_connection is connection for task_connection, _ in self.request_tasks):
            return
        self.request_locks.pop(connection, None)

    async def deliver_webui_request(
        self,
        connection: ServerConnection,
        request_id: str,
        operation_task: asyncio.Task[WebUIRequestResult],
        *,
        sequence: bool = False,
    ) -> None:
        try:
            if sequence:
                lock = self.request_locks.setdefault(connection, asyncio.Lock())
                async with lock:
                    result = await asyncio.shield(operation_task)
                    await self.send_webui_response(
                        connection,
                        request_id,
                        result=result.result,
                        status=result.status,
                        message=result.message,
                    )
                return
            result = await asyncio.shield(operation_task)
            await self.send_webui_response(
                connection,
                request_id,
                result=result.result,
                status=result.status,
                message=result.message,
            )
        finally:
            self.request_tasks.pop((connection, request_id), None)
            self.discard_request_lock_if_idle(connection)

    async def execute_webui_request(
        self,
        connection: ServerConnection,
        action: str,
        payload: dict[str, Any],
    ) -> WebUIRequestResult:
        try:
            lock = self.request_locks.setdefault(connection, asyncio.Lock())
            async with lock:
                response = await self._http_router.dispatch_webui_mutation(
                    connection,
                    action,
                    payload,
                )
                status = response.status_code
                body = bytes(response.body).decode("utf-8", errors="replace").strip()
                if 200 <= status < 300:
                    try:
                        result = json.loads(body)
                    except json.JSONDecodeError:
                        return WebUIRequestResult(
                            status=502,
                            message="WebUI mutation returned an invalid response",
                        )
                    if action == "sidebar.update" and isinstance(result, dict):
                        await self.broadcast_webui_event(
                            "sidebar_state_updated",
                            state=result,
                        )
                    return WebUIRequestResult(result=result)
                return WebUIRequestResult(
                    status=status,
                    message=body or response.reason_phrase,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("WebUI mutation '{}' failed", action)
            return WebUIRequestResult(
                status=500,
                message="WebUI mutation failed",
            )

    async def send_webui_response(
        self,
        connection: ServerConnection,
        request_id: str,
        *,
        result: Any = None,
        status: int | None = None,
        message: str | None = None,
    ) -> None:
        if status is None:
            await self._transport.webui_send_event(
                connection,
                "webui_response",
                request_id=request_id,
                ok=True,
                result=result,
            )
            return
        await self._transport.webui_send_event(
            connection,
            "webui_response",
            request_id=request_id,
            ok=False,
            error={
                "status": status,
                "message": message or "WebUI mutation failed",
            },
        )

    async def close(self) -> None:
        """Cancel command work and release application-owned gateway state."""
        if self._terminal_bridge_instance is not None:
            await self._terminal_bridge_instance.aclose()
            self._terminal_bridge_instance = None
        delivery_tasks = tuple(self.request_tasks.values())
        operation_tasks = tuple(operation.task for operation in self.request_operations.values())
        for task in (*delivery_tasks, *operation_tasks):
            task.cancel()
        if delivery_tasks:
            await asyncio.gather(*delivery_tasks, return_exceptions=True)
        if operation_tasks:
            await asyncio.gather(*operation_tasks, return_exceptions=True)
        self.request_tasks.clear()
        self.request_locks.clear()
        self.request_operations.clear()
        self.gateway.tokens.clear()
        self.gateway.endpoint.clear()
        self._temporary_chats.close()
