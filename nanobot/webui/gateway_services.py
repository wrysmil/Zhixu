"""Composition helpers for the embedded WebUI gateway."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast

from loguru import logger as default_logger

from nanobot.config.loader import get_config_path
from nanobot.config.loader import load_config as _load_config
from nanobot.memory.lifecycle import MemoryLifecycle
from nanobot.teams.binding import TeamBindingStore
from nanobot.teams.runs import TeamRunController
from nanobot.teams.service import TeamService
from nanobot.teams.turn import TeamLeaderTurns, build_team_turns, resolve_session_runtime
from nanobot.webui import (
    agents_api,
    identity_api,
    memory_api,
    teams_api,
    writing_api,
    writing_collab_api,
)
from nanobot.webui.agents_routes import AgentSettingsOperations
from nanobot.webui.gateway_endpoint import WebUIGatewayEndpoint
from nanobot.webui.gateway_tokens import GatewayTokenStore
from nanobot.webui.identity_routes import IdentitySettingsOperations
from nanobot.webui.ingress_policy import DEFAULT_WEBUI_INGRESS_POLICY, WebUIIngressPolicy
from nanobot.webui.media_gateway import WebUIMediaGateway
from nanobot.webui.memory_routes import MemorySettingsOperations
from nanobot.webui.memory_services import MemoryServices
from nanobot.webui.session_identity import webui_session_key
from nanobot.webui.session_projection import WebUISessionProjection
from nanobot.webui.settings_services import WebUISettingsServices
from nanobot.webui.subagent_execution_routes import (
    SubagentExecutionSettingsOperations,
    build_subagent_execution_operations,
)
from nanobot.webui.teams_routes import TeamSettingsOperations
from nanobot.webui.temporary_chats import WebUITemporaryChats
from nanobot.webui.transcript import WebUITranscriptRecorder
from nanobot.webui.workspaces import WebUIWorkspaceController
from nanobot.webui.writing_collab_api import WritingCollabServices
from nanobot.webui.writing_collab_routes import WritingCollabSettingsOperations
from nanobot.webui.writing_routes import WritingSettingsOperations
from nanobot.webui.writing_services import WritingServices
from nanobot.webui.ws_http import GatewayHTTPHandler
from nanobot.writing.database import WritingDatabase
from nanobot.writing.run_extension import WritingRunExtension, publish_writing_changed
from nanobot.writing.session_binding import SessionStoreLike, WritingSessionBinder
from nanobot.writing.team_seed import writing_team_seed_request_id, writing_team_template

if TYPE_CHECKING:
    from nanobot.bus.queue import MessageBus
    from nanobot.channels.websocket.runtime import WebSocketConfig
    from nanobot.config.schema import Config
    from nanobot.cron.service import CronService
    from nanobot.session.manager import SessionManager
    from nanobot.subagent_executions import ExecutionRepository
    from nanobot.triggers.local_store import LocalTriggerStore


@dataclass(frozen=True)
class GatewayServices:
    """Explicit dependencies shared by WebSocket transport and HTTP routes."""

    http: GatewayHTTPHandler
    endpoint: WebUIGatewayEndpoint
    settings: WebUISettingsServices
    tokens: GatewayTokenStore
    media: WebUIMediaGateway
    ingress: WebUIIngressPolicy
    transcripts: WebUITranscriptRecorder
    workspaces: WebUIWorkspaceController
    temporary_chats: WebUITemporaryChats
    session_projection: WebUISessionProjection
    session_manager: SessionManager | None
    cron_service: CronService | None
    local_trigger_store: LocalTriggerStore | None
    cron_pending_job_ids: Callable[[str], set[str]] | None
    local_trigger_pending_ids: Callable[[str], set[str]] | None
    team_service: TeamService | None = None
    team_operations: TeamSettingsOperations | None = None
    subagent_execution_repository: ExecutionRepository | None = None
    subagent_execution_operations: SubagentExecutionSettingsOperations | None = None
    team_bindings: TeamBindingStore | None = None
    team_turns: TeamLeaderTurns | None = None
    # 文章 ↔ 会话绑定（``bind_writing_session``）；未接团队栈时仍可绑定（只是没有运行）。
    writing_sessions: WritingSessionBinder | None = None
    writing_team_id: str | None = None


def build_memory_operations(
    *,
    workspace_id: str,
    workspace_path: Path,
) -> MemorySettingsOperations:
    """Wire the real ``memory_api`` actions to a workspace-scoped database."""
    services = MemoryServices.for_workspace(workspace_id, workspace_path)
    return MemorySettingsOperations(
        list_memories=partial(memory_api.list_memories_payload, services),
        search_memories=partial(memory_api.search_memories_payload, services),
        fetch_memory=partial(memory_api.fetch_memory_payload, services),
        list_episodes=partial(memory_api.list_episodes_payload, services),
        fetch_episode=partial(memory_api.fetch_episode_payload, services),
        fetch_scratchpad=partial(memory_api.scratchpad_payload, services),
        fetch_stats=partial(memory_api.stats_payload, services),
        create_memory=partial(memory_api.create_memory, services),
        update_memory=partial(memory_api.update_memory, services),
        delete_memory=partial(memory_api.delete_memory, services),
        update_episode=partial(memory_api.update_episode, services),
        delete_episode=partial(memory_api.delete_episode, services),
        save_scratchpad=partial(memory_api.save_scratchpad, services),
        # reindex/sync 不注入 indexer/vector_runtime：由 memory_api 内部 fallback
        # 到进程级单例（get_active_indexer / get_active_store），与写路径钩子同构，
        # 规避双 VectorStore 撞 ChromaDB 内部 sqlite 锁。
        reindex_vector=partial(memory_api.reindex_vector, services),
        sync_vector=partial(memory_api.sync_vector, services),
        refresh_memory_md=partial(memory_api.refresh_memory_md, services),  # 🆕 WU-4
        get_memory_md_content=partial(memory_api.get_memory_md_content, services),  # 🆕 WU-6
    )


def build_writing_operations(
    *,
    workspace_id: str,
    workspace_path: Path,
    database: WritingDatabase | None = None,
) -> WritingSettingsOperations:
    """Wire the real ``writing_api`` actions to a workspace-scoped article store.

    与 ``build_memory_operations`` 同签名同风格：一个 gateway 进程绑定一个
    ``workspace_path``，文章库落在 ``<workspace>/writing/state.db``。七个入口
    全部预绑定 services，handler 只传业务参数（契约 §4/§5 的字段）。
    """
    services = WritingServices.for_workspace(
        workspace_id, workspace_path, database=database
    )
    return WritingSettingsOperations(
        list_articles=partial(writing_api.list_articles, services),
        fetch_article=partial(writing_api.fetch_article, services),
        create_article=partial(writing_api.create_article, services),
        save_article=partial(writing_api.save_article, services),
        duplicate_article=partial(writing_api.duplicate_article, services),
        delete_article=partial(writing_api.delete_article, services),
        undo_change=partial(writing_api.undo_change, services),
    )


def build_writing_collab_operations(
    *,
    workspace_id: str,
    workspace_path: Path,
    runner: Any | None = None,
    database: WritingDatabase | None = None,
) -> WritingCollabSettingsOperations:
    """Wire the collaboration payload layer to a workspace-scoped writing store.

    与 ``build_writing_operations`` 同签名同风格：协作库就是写作库
    （``<workspace>/writing/state.db``，协作表由 ``CollabRepository`` 幂等补建）。

    ``runner`` 是执行栈（``nanobot.writing.collab.runner.CollabRunner``，WU-08）：
    它只以 Protocol 依赖注入，本模块**不 import** runner，因此 runner 未落地时
    装配仍然可用（读动作、``apply`` / ``undo`` 不需要执行栈；控制动作回 503
    ``writing collaboration service is not configured``）。
    """
    shared = database if database is not None else WritingDatabase(Path(workspace_path))
    services = WritingCollabServices.for_workspace(workspace_id, shared, runner)
    return WritingCollabSettingsOperations(
        get_collaboration=partial(writing_collab_api.fetch_collaboration, services),
        get_artifact=partial(writing_collab_api.fetch_artifact, services),
        start=partial(writing_collab_api.start_collaboration, services),
        reply=partial(writing_collab_api.reply_collaboration, services),
        confirm=partial(writing_collab_api.confirm_handoff, services),
        cancel=partial(writing_collab_api.cancel_collaboration, services),
        retry=partial(writing_collab_api.retry_collaboration, services),
        apply=partial(writing_collab_api.apply_suggestion, services),
        undo=partial(writing_collab_api.undo, services),
    )


def build_writing_collab_runner(
    *,
    workspace_path: Path,
    team_service: TeamService,
    team_turns: TeamLeaderTurns,
    bus: MessageBus,
    model_id: Callable[[], str | None] | None = None,
    session_manager: Any | None = None,
    channel_name: str = "websocket",
    database: WritingDatabase | None = None,
    logger: Any = default_logger,
) -> Any:
    """装配协作执行栈：``TeamWritingPort`` + envelope 出口 + ``CollabRunner``。

    只在这个 gateway 确实有团队执行栈时调用（``team_turns is not None``）：没有执行栈
    时 ``build_writing_collab_operations(runner=None)`` 保持契约 §4 的 503
    ``writing collaboration service is not configured``，读动作与 ``apply``/``undo``
    照常可用 —— 绝不假装能起跑。

    **导入纪律**：本函数内部的一切 import 都是延迟的。``nanobot.writing.builtin`` 与
    ``nanobot.writing.collab.team_port`` 都会在导入期拉进 ``nanobot.config.paths``，
    让 ``mock.patch("nanobot.agent.tools.mcp.connect_mcp_servers")`` 这类按名 patch
    不可达（已有一个 WU 踩过，见 ``nanobot/agents/store.py`` 的注释）。

    ``publish`` 把领域事件翻成契约 §5 的三类帧：帧的 ``chat_id`` 只能从协作行取
    （事件体里没有归属字段，客户端也不得影响归属），因此这里读一次协作行再把
    ``OutboundMessage`` 投进总线，交给既有的 WebUI 投影出口扇出。

    ``model_id`` 仍是「本批冻结的模型标识」的来源，只是**取值口径变了**：优先按协作会话
    （``websocket:<chat_id>``）解析出的 runtime，该会话没有 preset 或跑不起解析时才回落到
    这个零参回调（装配期的全局模型名）。批次 runtime 与冻结记录因此是同一份解析结果。
    """
    from nanobot.agent.skills import SkillsLoader
    from nanobot.bus.outbound_events import (
        WritingArtifactEvent,
        WritingCollabEvent,
        WritingHandoffEvent,
        outbound_message_for_event,
    )
    from nanobot.writing.builtin import BuiltinAssetRegistry
    from nanobot.writing.collab.envelope import EnvelopeArtifactSink
    from nanobot.writing.collab.repository import CollabRepository
    from nanobot.writing.collab.runner import CollabRunner, frozen_preparer
    from nanobot.writing.collab.team_port import TeamWritingPort
    from nanobot.writing.repository import ArticleRepository

    shared = database if database is not None else WritingDatabase(Path(workspace_path))
    repository = CollabRepository(shared)
    articles = ArticleRepository(shared)
    event_types: dict[str, Any] = {
        "writing_collab": WritingCollabEvent,
        "writing_artifact": WritingArtifactEvent,
        "writing_handoff": WritingHandoffEvent,
    }
    # 协作会话的模型 preset 是**唯一**的模型真相源（挂在会话 metadata 上）。装配层
    # 只负责把「按会话键解析」的能力递给批次端口与冻结准备，绝不新增任何模型字段。
    session_runtime_provider = getattr(team_turns, "session_runtime_provider", None)

    def _log_frozen_model_fallback(collaboration_id: str, error: Exception) -> None:
        logger.bind(
            event="writing_collab_frozen_session_model_failed",
            collaboration_id=collaboration_id,
            error_type=type(error).__name__,
        ).warning("frozen model resolution by collaboration session failed; using the default")

    def session_model_id(collaboration_id: str) -> str | None:
        """本批冻结的模型标识 = 该**协作会话**解析出的模型；取不到回 ``None``（回落全局）。

        会话键优先取协作行上的 ``session_key``（``CollabRunner`` 建行时写入、并且原样交给
        ``TeamWritingPort.start_batch``，所以冻结与实际起跑必然是同一个键）；老行缺该列时
        才按 ``<channel>:<chat_id>`` 拼。``chat_id`` 只能从协作行取（客户端不得影响归属）。
        会话 preset 失效时 ``runtime_for_session`` 自己回落默认（spec §4.4），这里只兜
        「会话层整个不可用」这类错误。
        """
        if session_runtime_provider is None or not collaboration_id:
            return None
        try:
            collaboration = repository.get_collaboration(collaboration_id)
        except Exception as error:  # noqa: BLE001 - 读不到协作行就回落全局模型名
            _log_frozen_model_fallback(collaboration_id, error)
            return None
        session_key = str(getattr(collaboration, "session_key", "") or "")
        if not session_key:
            chat_id = str(getattr(collaboration, "chat_id", "") or "")
            session_key = f"{channel_name}:{chat_id}" if chat_id else ""
        if not session_key:
            return None
        runtime, _source = resolve_session_runtime(
            session_key,
            session_runtime_provider=session_runtime_provider,
            fallback=None,
        )
        model = getattr(runtime, "model", None)
        if isinstance(model, str) and model.strip():
            return model.strip()
        return None

    def _log_publish_failure(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.bind(event="writing_collab_frame_publish_failed").warning(
                f"writing collaboration frame publish failed: {error}"
            )

    def publish(name: str, body: Mapping[str, Any]) -> None:
        event_type = event_types.get(name)
        collaboration_id = str(body.get("collaboration_id") or "")
        if event_type is None or not collaboration_id:
            return
        collaboration = repository.get_collaboration(collaboration_id)
        message = outbound_message_for_event(
            channel=channel_name,
            chat_id=collaboration.chat_id,
            event=event_type(body=dict(body)),
        )
        task = asyncio.get_running_loop().create_task(bus.publish_outbound(message))
        task.add_done_callback(_log_publish_failure)

    # 冻结准备端口：``model_id`` 语义不变（仍是「本批冻结的模型标识」），只是来源从全局
    # 改成协作会话解析结果。``frozen_preparer`` 的 ``model_id`` 是零参回调（``_prepare_frozen``
    # 调用时不给 session_key），所以这里包一层：先照旧冻结，再按协作会话改写到同一份
    # ``FrozenContext``（``FrozenContext`` 是 frozen dataclass，用 ``replace`` 只换一个字段）。
    base_preparer = frozen_preparer(
        loader=SkillsLoader(Path(workspace_path)),
        model_id=model_id,
    )

    def session_frozen_preparer(
        *,
        recipe: Any = None,
        article_snapshot: Mapping[str, Any] | None = None,
        material_ids: Sequence[str] = (),
        collaboration_id: str = "",
        workspace_key: str = "",
        article_id: str = "",
        batch_id: str = "",
    ) -> Any:
        frozen = base_preparer(
            recipe=recipe,
            article_snapshot=dict(article_snapshot or {}),
            material_ids=tuple(material_ids or ()),
            collaboration_id=collaboration_id,
            workspace_key=workspace_key,
            article_id=article_id,
            batch_id=batch_id,
        )
        resolved = session_model_id(collaboration_id)
        if resolved and resolved != str(getattr(frozen, "model_id", "") or ""):
            frozen = replace(frozen, model_id=resolved)
        return frozen

    port = TeamWritingPort(
        service=team_service,
        workspace=Path(workspace_path),
        turns=team_turns,
        articles=articles,
        registry=BuiltinAssetRegistry(shared),
        session_store=session_manager,
        runtime_provider=getattr(team_turns, "runtime_provider", None),
        session_runtime_provider=session_runtime_provider,
        logger=logger,
    )
    runner = CollabRunner(
        repository=repository,
        workspace_key=shared.workspace_key,
        articles=articles,
        team=port,
        artifacts=EnvelopeArtifactSink(
            repository=repository,
            teams=team_service.repository,
            articles=articles,
            workspace_key=shared.workspace_key,
            logger=logger,
        ),
        preparer=session_frozen_preparer,
        publisher=publish,
        logger=logger,
    )
    # 团队事件 → 收口：端口构造时已 subscribe 调度器，sink 允许后置绑定。
    port.set_event_sink(runner.on_team_event)
    return runner


def build_identity_operations(
    *,
    workspace_id: str,
    workspace_path: Path,
) -> IdentitySettingsOperations:
    """Wire the real ``identity_api`` actions to a workspace-scoped store.

    Every callable arrives pre-bound: the handler passes business arguments
    (name/content/mode) only. ``MEMORY.md`` writes and reloads go through the
    workspace's ``MemoryLifecycle`` singleton, matching the write-path hook and
    avoiding a second ``VectorStore``.
    """
    services = MemoryServices.for_workspace(workspace_id, workspace_path)
    lifecycle = MemoryLifecycle.for_workspace(workspace_id, services)
    workspace = Path(workspace_path)
    return IdentitySettingsOperations(
        list_files=partial(identity_api.identity_list_files, workspace),
        read_file=partial(identity_api.identity_read_file, workspace),
        write_file=partial(
            identity_api.identity_write_file, workspace, lifecycle=lifecycle
        ),
        reload=partial(
            identity_api.identity_reload,
            refresh_memory_md=partial(memory_api.refresh_memory_md, services),
        ),
        list_presets=identity_api.identity_list_presets,
        get_active_persona=partial(identity_api.identity_get_active_persona, workspace),
        set_active_persona=partial(identity_api.identity_set_active_persona, workspace),
    )


def build_agents_operations(
    *,
    workspace_path: Path,
    load_config: Callable[[], Config] | None = None,
    team_reference_lookup: Callable[[str], list[str]] | None = None,
) -> AgentSettingsOperations:
    """Wire the real ``agents_api`` actions to a workspace-scoped store.

    ``load_config`` 只被 ``agents_catalog`` 消费（工具与模型描述符要读
    ``Config.tools`` / 模型预设），未注入时回落到进程级配置路径。

    ``team_reference_lookup`` 给删除动作用：被未归档团队引用的档案要 409
    ``AGENT_IN_USE_BY_TEAM``（契约 §3）。没注入时删除行为与团队存在与否无关。
    """
    workspace = Path(workspace_path)
    return AgentSettingsOperations(
        list_profiles=partial(agents_api.agents_list, workspace),
        save_profile=partial(agents_api.agents_save, workspace),
        delete_profile=partial(
            agents_api.agents_delete, workspace, team_reference_lookup=team_reference_lookup
        ),
        reset_profile=partial(agents_api.agents_reset, workspace),
        set_visibility=partial(agents_api.agents_visibility, workspace),
        list_catalog=partial(
            agents_api.agents_catalog,
            workspace,
            load_config=load_config or _load_config,
        ),
    )


def seed_writing_team(
    team_service: TeamService,
    *,
    logger: Any = default_logger,
) -> str | None:
    """幂等播种内置创作团队；失败只留日志，不影响普通聊天与团队管理。

    返回内置团队的**物理 ID**。用户同名团队由 allocator 协议避让，绝不被覆盖。
    """
    try:
        team_id = team_service.ensure_builtin_team(
            writing_team_template(),
            request_id=writing_team_seed_request_id(),
        )
    except Exception as error:  # noqa: BLE001 - 播种失败降级为「无写作团队」
        logger.bind(event="writing_team_seed_failed").warning(
            f"writing team seed failed: {error}"
        )
        return None
    if team_id is None:
        logger.bind(event="writing_team_seed_skipped").warning(
            "writing team seed skipped: builtin asset registry is not wired"
        )
    return team_id


def build_team_stack(
    *,
    workspace_path: Path,
    enabled: bool,
) -> tuple[TeamService, TeamSettingsOperations, TeamBindingStore]:
    """装配团队服务、handler operations 与空聊天的绑定暂存。

    ``enabled`` 只影响领域判断（关掉 → 503 ``TEAMS_DISABLED``），路由与 WS 白名单
    照常登记，这样客户端拿到的是明确错误码而不是 404。
    """
    service = teams_api.build_service(Path(workspace_path), enabled=enabled)
    recovery = teams_api.claim_team_writer(service)
    if service.writer_owned:
        default_logger.info(
            f"team writer acquired for {workspace_path}:"
            f" interrupted={len(recovery['interruptedRuns'])} pruned={len(recovery['prunedRuns'])}"
        )
    else:
        default_logger.warning(
            f"team writer busy for {workspace_path}; team scheduling is disabled in this gateway"
        )
    return (
        service,
        teams_api.build_team_operations(service),
        TeamBindingStore(service, now=time.monotonic),
    )


def build_gateway_services(
    *,
    config: WebSocketConfig,
    bus: MessageBus,
    session_manager: SessionManager | None,
    static_dist_path: Path | None,
    workspace_path: Path,
    default_restrict_to_workspace: bool,
    config_path: Path | None = None,
    runtime_model_name: Callable[[], str | None] | None,
    refresh_runtime_config: Callable[[], None] | None = None,
    runtime_surface: str,
    runtime_capabilities_overrides: dict[str, Any] | None,
    disabled_skills: set[str] | None = None,
    cron_service: CronService | None = None,
    local_trigger_store: LocalTriggerStore | None = None,
    cron_pending_job_ids: Callable[[str], set[str]] | None = None,
    local_trigger_pending_ids: Callable[[str], set[str]] | None = None,
    channel_feature_action: Callable[..., Any] | None = None,
    channel_runtime_status: Callable[[], dict[str, Any]] | None = None,
    mcp_runtime_status: Callable[[], Mapping[str, str]] | None = None,
    mcp_reload: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    skill_state_action: Callable[[set[str]], None] | None = None,
    recovery_action: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    team_host: Any | None = None,
    logger: Any = default_logger,
) -> GatewayServices:
    settings = WebUISettingsServices.create(
        config_path or get_config_path(),
        rename_model_preset=(
            session_manager.rename_model_preset
            if session_manager is not None
            else None
        ),
        refresh_runtime_config=refresh_runtime_config,
    )
    tokens = GatewayTokenStore()
    ingress = DEFAULT_WEBUI_INGRESS_POLICY
    minimum_frame_bytes = ingress.minimum_full_policy_frame_bytes()
    if config.max_message_bytes < minimum_frame_bytes:
        logger.warning(
            "WebSocket maxMessageBytes={} is below the WebUI ingress policy capacity={}; "
            "policy-valid messages may still hit the transport frame guard",
            config.max_message_bytes,
            minimum_frame_bytes,
        )
    media = WebUIMediaGateway(
        workspace_path=workspace_path,
        logger=logger,
        attachment_limits=ingress.attachments,
    )
    transcripts = WebUITranscriptRecorder(log=logger)
    workspaces = WebUIWorkspaceController(
        session_manager=session_manager,
        default_workspace=workspace_path,
        default_restrict_to_workspace=default_restrict_to_workspace,
    )
    temporary_chats = WebUITemporaryChats(
        bus=bus,
        session_manager=session_manager,
        workspaces=workspaces,
        logger=logger,
    )
    session_projection = WebUISessionProjection(session_manager, log=logger)
    # 团队栈要在 agents operations 之前装配：删除档案要问「还有未归档团队引用它吗」。
    team_service, team_operations, team_bindings = build_team_stack(
        workspace_path=workspace_path,
        # 回滚开关必须读字段本身：``bool(pydantic 模型实例)`` 恒真，会让
        # ``agents.teams.enabled=false`` 完全失效（契约 §4 的 503 TEAMS_DISABLED）。
        enabled=bool(settings.config.load().agents.teams.enabled),
    )
    # 写作域与团队栈共享同一个 SQLite 入口（进程内一把写锁），并先播种内置创作团队：
    # 团队管理里看到的「创作团队」就是下面绑定会话用的同一个物理记录。
    writing_database = WritingDatabase(Path(workspace_path))
    writing_database.ensure_schema()
    writing_team_id = seed_writing_team(team_service, logger=logger)

    def resolve_writing_team() -> dict[str, Any]:
        if writing_team_id is None:
            raise RuntimeError("writing team is not seeded")
        definition = team_service.repository.get_team(writing_team_id)
        return {
            "team_id": definition.id,
            "team_revision": definition.revision,
            "team_name": definition.name,
        }

    writing_sessions = WritingSessionBinder(
        writing_database,
        # SessionManager 的 save() 收具体 Session，与结构协议参数类型窄一档；
        # 运行时鸭子类型成立，会话绑定只用到 get_or_create/save/metadata。
        cast(SessionStoreLike | None, session_manager),
        team_resolver=resolve_writing_team if writing_team_id is not None else None,
        session_key_for=webui_session_key,
    )
    # 写作扩展只认已播种的物理团队 ID；克隆团队因此拿不到写作工具（契约 §3）。
    writing_extension = (
        WritingRunExtension(
            db=writing_database,
            binder=writing_sessions,
            team_ids=lambda: {writing_team_id} if writing_team_id else set(),
            publish_change=lambda chat_id, body: publish_writing_changed(
                bus=bus,
                channel_name="websocket",
                chat_id=chat_id,
                body=body,
            ),
            logger_=logger,
        )
        if writing_team_id is not None
        else None
    )
    # 团队执行栈要有宿主（子执行器/工具目录/默认运行时）才能装配；CLI 或测试构造的
    # gateway 没有 host 时留 None，普通聊天路径完全不受影响。
    team_turns = (
        build_team_turns(
            service=team_service,
            host=team_host,
            workspace=workspace_path,
            run_extension=writing_extension,
        )
        if team_host is not None
        else None
    )
    if team_turns is not None:
        # 取消/重试要碰调度器里的活句柄，所以控制动作只能在执行栈装好之后接上；
        # 没装执行栈的 gateway（CLI/测试）保持 503「未接线」。
        team_operations = teams_api.with_team_controller(
            team_operations,
            TeamRunController(
                service=team_service,
                scheduler=team_turns.scheduler,
                runtime_provider=team_turns.runtime_provider or (lambda: None),
                session_runtime_provider=team_turns.session_runtime_provider,
                preset_resolver=team_turns.preset_resolver,
                workspace=workspace_path,
            ),
        )
    # 协作执行栈：与团队栈同生共死。没有执行栈时 runner=None → 控制动作 503（契约 §4），
    # 读动作与 apply/undo 仍然可用（它们本来就不需要执行栈）。
    collab_runner = (
        build_writing_collab_runner(
            workspace_path=workspace_path,
            team_service=team_service,
            team_turns=team_turns,
            bus=bus,
            model_id=runtime_model_name,
            session_manager=session_manager,
            database=writing_database,
        )
        if team_turns is not None
        else None
    )
    subagent_execution_repository = getattr(
        getattr(team_host, "subagents", None), "execution_ledger", None,
    )
    subagent_execution_operations = build_subagent_execution_operations(
        subagent_execution_repository,
    )

    http = GatewayHTTPHandler(
        config=config,
        session_manager=session_manager,
        static_dist_path=static_dist_path,
        runtime_model_name=runtime_model_name,
        runtime_surface=runtime_surface,
        runtime_capabilities_overrides=runtime_capabilities_overrides,
        bus=bus,
        tokens=tokens,
        media=media,
        ingress=ingress,
        workspaces=workspaces,
        settings=settings,
        skills_workspace_path=workspace_path,
        disabled_skills=disabled_skills,
        subagent_execution_operations=subagent_execution_operations,
        memory_operations=build_memory_operations(
            workspace_id="default",
            workspace_path=workspace_path,
        ),
        identity_operations=build_identity_operations(
            workspace_id="default",
            workspace_path=workspace_path,
        ),
        agents_operations=build_agents_operations(
            workspace_path=workspace_path,
            # 用 gateway 自己的配置路径，不要读进程级的默认配置路径。
            load_config=settings.config.load,
            team_reference_lookup=team_service.teams_referencing_profile,
        ),
        writing_operations=build_writing_operations(
            workspace_id="default",
            workspace_path=workspace_path,
            database=writing_database,
        ),
        writing_collab_operations=build_writing_collab_operations(
            workspace_id="default",
            workspace_path=workspace_path,
            runner=collab_runner,
            database=writing_database,
        ),
        team_operations=team_operations,
        team_bindings=team_bindings,
        cron_service=cron_service,
        local_trigger_store=local_trigger_store,
        cron_pending_job_ids=cron_pending_job_ids,
        local_trigger_pending_ids=local_trigger_pending_ids,
        channel_feature_action=channel_feature_action,
        channel_runtime_status=channel_runtime_status,
        mcp_runtime_status=mcp_runtime_status,
        mcp_reload=mcp_reload,
        skill_state_action=skill_state_action,
        recovery_action=recovery_action,
        log=logger,
    )
    http.settings_routes.configure_subagent_execution_operations(
        subagent_execution_operations,
    )
    endpoint = WebUIGatewayEndpoint(config=config, http=http, tokens=tokens)
    return GatewayServices(
        http=http,
        endpoint=endpoint,
        settings=settings,
        tokens=tokens,
        media=media,
        ingress=ingress,
        transcripts=transcripts,
        workspaces=workspaces,
        temporary_chats=temporary_chats,
        session_projection=session_projection,
        session_manager=session_manager,
        cron_service=cron_service,
        local_trigger_store=local_trigger_store,
        cron_pending_job_ids=cron_pending_job_ids,
        local_trigger_pending_ids=local_trigger_pending_ids,
        team_service=team_service,
        team_operations=team_operations,
        subagent_execution_repository=subagent_execution_repository,
        subagent_execution_operations=subagent_execution_operations,
        team_bindings=team_bindings,
        team_turns=team_turns,
        writing_sessions=writing_sessions,
        writing_team_id=writing_team_id,
    )
