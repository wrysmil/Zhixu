"""协作域接线检查（契约 §3 三表 + 事件链）。

``test_writing_collab_routes.py`` 证明 handler 本身正确，本文件证明它*外面*的
四层对得上：

1. ``settings_routes.py::_SYSTEM_ROUTES`` 九条路径 → action；
2. ``_SETTINGS_MUTATION_PATHS`` 七个写路径（含 ``_WRITING_COLLAB_MUTATION_PATHS``）；
3. ``ws_http.py::_WEBUI_MUTATION_PATHS`` 七个 WS action → HTTP 路径；
4. 事件链：``WritingEvent`` → 投影 → 帧（``{"event": <event_name>, "chat_id": ...}``）。

这四层漏一条都不会在 import 期报错：路径漏登记 404，写路径漏进白名单则未认证
的普通 GET 就能改协作状态，WS action 漏登记则前端提交时 404，投影漏分支则事件
静默丢弃。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from websockets.datastructures import Headers

from nanobot.bus.events import OutboundMessage
from nanobot.bus.outbound_events import (
    WritingArtifactEvent,
    WritingCollabEvent,
    WritingHandoffEvent,
)
from nanobot.webui.settings_contracts import WebUISettingsError
from nanobot.webui import (
    gateway_services,
    outbound_projection,
    settings_routes,
    writing_collab_routes,
)
from nanobot.webui.gateway_services import (
    build_gateway_services,
    build_writing_collab_operations,
    build_writing_operations,
)
from nanobot.webui.http_utils import http_json_response
from nanobot.webui.outbound_projection import WebUIOutboundProjector
from nanobot.webui.settings_routes import (
    _SETTINGS_MUTATION_PATHS,
    _SYSTEM_ROUTES,
    _WRITING_COLLAB_MUTATION_PATHS,
    WebUISettingsRouter,
    _null_writing_collab_operations,
)
from nanobot.webui.settings_services import WebUISettingsServices
from nanobot.webui.writing_collab_routes import (
    WRITING_COLLAB_ACTION_NAMES,
    WRITING_COLLAB_READ_ACTION_NAMES,
    WritingCollabSettingsHandler,
    WritingCollabSettingsOperations,
)
from nanobot.webui.ws_http import _WEBUI_MUTATION_PATHS, GatewayHTTPHandler

# 契约 §3：读走认证 HTTP GET，写走已认证 WS mutation，九个动作名逐字固定。
ARTICLE_DIGEST = "digest-1"

EXPECTED_READ_ROUTES = {
    "/api/writing/collab": "writing.collab.get",
    "/api/writing/collab/artifact": "writing.artifact.get",
}

EXPECTED_WRITE_ROUTES = {
    "/api/writing/collab/start": "writing.start",
    "/api/writing/collab/reply": "writing.reply",
    "/api/writing/collab/confirm": "writing.confirm",
    "/api/writing/collab/cancel": "writing.cancel",
    "/api/writing/collab/retry": "writing.retry",
    "/api/writing/collab/apply": "writing.apply",
    "/api/writing/collab/undo": "writing.undo",
}

EXPECTED_SYSTEM_ROUTES = {**EXPECTED_READ_ROUTES, **EXPECTED_WRITE_ROUTES}
EXPECTED_WRITE_PATHS = frozenset(EXPECTED_WRITE_ROUTES)
EXPECTED_WS_ACTIONS = {action: path for path, action in EXPECTED_WRITE_ROUTES.items()}

MATERIAL_EVENTS = (
    (
        WritingCollabEvent(body={
            "collaboration_id": "c-1", "status": "queued", "stage": "",
            "active_batch_id": "b-1", "version": 2, "seq": 3,
        }),
        "writing_collab",
    ),
    (
        WritingArtifactEvent(body={
            "collaboration_id": "c-1", "artifact_id": "a-1", "kind": "outline",
            "version": 1, "summary": "提纲",
        }),
        "writing_artifact",
    ),
    (
        WritingHandoffEvent(body={
            "collaboration_id": "c-1", "handoff_id": "h-1", "kind": "outline_confirm",
            "artifact_id": "a-1", "artifact_version": 1, "status": "pending",
        }),
        "writing_handoff",
    ),
)


# ---- R1: _SYSTEM_ROUTES（读路径 + 写路径落地）------------------------------


@pytest.mark.parametrize("path,action", sorted(EXPECTED_SYSTEM_ROUTES.items()))
def test_all_collab_actions_are_registered(path: str, action: str) -> None:
    assert _SYSTEM_ROUTES.get(path) == action
    assert WebUISettingsRouter._route(path) == ("system", action)


def test_system_routes_have_exactly_the_nine_collab_entries() -> None:
    assert {path for path in _SYSTEM_ROUTES if "/api/writing/collab" in path} == set(
        EXPECTED_SYSTEM_ROUTES
    )
    collab_paths = {
        path for path in _SYSTEM_ROUTES if "/api/writing/collab" in path
    }
    assert len(collab_paths) == 9


def test_registered_actions_all_belong_to_the_domain() -> None:
    assert set(EXPECTED_SYSTEM_ROUTES.values()) == WRITING_COLLAB_ACTION_NAMES


def test_collab_paths_do_not_shadow_article_paths() -> None:
    """``/api/writing/collab`` 不能吃掉 ``/api/writing/articles`` 的登记。"""
    article_paths = {path for path in _SYSTEM_ROUTES if path.startswith("/api/writing/articles")}
    assert len(article_paths) == 6
    assert not (article_paths & set(EXPECTED_SYSTEM_ROUTES))


def test_reads_are_not_mutations() -> None:
    for path in EXPECTED_READ_ROUTES:
        assert path not in _SETTINGS_MUTATION_PATHS
        assert not WebUISettingsRouter.is_mutation_path(path)


# ---- R2: _SETTINGS_MUTATION_PATHS（WS-only 门禁）---------------------------


@pytest.mark.parametrize("path", sorted(EXPECTED_WRITE_PATHS))
def test_collab_write_paths_require_authenticated_websocket(path: str) -> None:
    assert path in _SETTINGS_MUTATION_PATHS
    assert path in _WRITING_COLLAB_MUTATION_PATHS
    assert WebUISettingsRouter.is_mutation_path(path)


def test_collab_mutation_set_matches_registered_writes() -> None:
    assert _WRITING_COLLAB_MUTATION_PATHS == EXPECTED_WRITE_PATHS


def test_collab_mutations_do_not_leak_into_other_domains() -> None:
    assert not (
        _WRITING_COLLAB_MUTATION_PATHS
        & (
            settings_routes._MEMORY_MUTATION_PATHS
            | settings_routes._IDENTITY_MUTATION_PATHS
            | settings_routes._AGENTS_MUTATION_PATHS
            | settings_routes._WRITING_MUTATION_PATHS
            | settings_routes._TEAMS_MUTATION_PATHS
        )
    )


# ---- R3: _WEBUI_MUTATION_PATHS（WS action → HTTP 路径）---------------------


@pytest.mark.parametrize("action,path", sorted(EXPECTED_WS_ACTIONS.items()))
def test_collab_ws_action_maps_to_http_path(action: str, path: str) -> None:
    assert _WEBUI_MUTATION_PATHS.get(action) == path
    assert GatewayHTTPHandler._webui_mutation_path(action, {}) == path


def test_ws_action_names_match_the_frontend_contract() -> None:
    assert {a for a in _WEBUI_MUTATION_PATHS if a.startswith("writing.")} == set(
        EXPECTED_WS_ACTIONS
    )


def test_ws_mutation_actions_are_exactly_the_seven_writes() -> None:
    """handler 侧名单与传输侧翻译表必须同一口径。"""
    assert writing_collab_routes.WRITING_COLLAB_MUTATION_ACTION_NAMES == frozenset(
        EXPECTED_WS_ACTIONS
    )
    assert (
        WRITING_COLLAB_ACTION_NAMES
        - writing_collab_routes.WRITING_COLLAB_MUTATION_ACTION_NAMES
        == WRITING_COLLAB_READ_ACTION_NAMES
    )


def test_ws_mapped_paths_are_known_mutations() -> None:
    for path in EXPECTED_WS_ACTIONS.values():
        assert WebUISettingsRouter.is_mutation_path(path)


def test_unregistered_collab_ws_action_is_404() -> None:
    from websockets.http11 import Response

    result = GatewayHTTPHandler._webui_mutation_path("writing.frobnicate", {})
    assert isinstance(result, Response)
    assert result.status_code == 404


# ---- R4: gateway 装配 -------------------------------------------------------


def test_build_writing_collab_operations_is_fully_bound(tmp_path: Path) -> None:
    from dataclasses import fields

    ops = build_writing_collab_operations(workspace_id="default", workspace_path=tmp_path)
    for field in fields(WritingCollabSettingsOperations):
        assert callable(getattr(ops, field.name))
    assert asyncio.iscoroutinefunction(ops.start)
    assert asyncio.iscoroutinefunction(ops.cancel)
    assert asyncio.iscoroutinefunction(ops.retry)


def test_build_writing_collab_operations_without_runner_is_available(tmp_path: Path) -> None:
    """runner 缺失时（WU-08 未合入）装配仍可用：读动作与 apply/undo 照常工作。"""
    ops = build_writing_collab_operations(workspace_id="default", workspace_path=tmp_path)
    created = ops.get_collaboration(article_id="missing")
    assert created["collaboration"] is None


def test_gateway_services_wires_collab_operations() -> None:
    import inspect

    source = inspect.getsource(build_gateway_services)
    assert "writing_collab_operations=build_writing_collab_operations(" in source
    assert "writing_collab_operations=self.writing_collab_operations" in inspect.getsource(
        GatewayHTTPHandler
    )


def test_gateway_services_keeps_the_team_enabled_flag_untouched() -> None:
    """WU-08 正在改这一行：本 WU 只追加协作装配。

    这里不断言表达式内容（WU-08 会把它改成 ``.agents.teams.enabled``），只断言
    ``build_team_stack`` 仍然拿到一个 ``enabled=...`` 实参——一旦本 WU 的装配把
    团队开关吃掉，这条会立刻红。
    """
    import inspect
    import re

    source = inspect.getsource(build_gateway_services)
    call = re.search(r"build_team_stack\((.*?)\n    \)", source, re.S)
    assert call is not None
    assert "enabled=" in call.group(1)


# ---- R5: null 兜底 ----------------------------------------------------------


def test_null_collab_operations_are_503() -> None:
    handler = WritingCollabSettingsHandler(_null_writing_collab_operations())
    result = handler.handle("writing.collab.get", SimpleNamespace(
        query={"article_id": ["a-1"]}, payload=None,
    ))
    assert (result.status, result.error) == (
        503, "writing collaboration service is not configured",
    )
    assert result.payload is None

    for action, payload in (
        ("writing.apply", {
            "client_request_id": "r-1", "suggestion_id": "s-1",
            "article_id": "a-1", "base_revision": 1,
        }),
        ("writing.undo", {
            "client_request_id": "r-1", "undo_id": "u-1", "base_revision": 1,
        }),
        ("writing.artifact.get", {"artifact_id": "art-1"}),
    ):
        result = handler.handle(action, SimpleNamespace(query={}, payload=payload))
        assert (result.status, result.error) == (
            503, "writing collaboration service is not configured",
        ), action


# ---- R6: router 端到端 ------------------------------------------------------

WS_BASE_ARGS: dict[str, Any] = {
    "bus": SimpleNamespace(),
    "logger": SimpleNamespace(exception=lambda *_args: None),
    "check_api_token": lambda _request: True,
    "parse_query": lambda path: parse_qs(urlsplit(path).query),
    "json_response": http_json_response,
    "error_response": lambda status, message: http_json_response(
        {"error": message}, status=status
    ),
    "runtime_surface": "browser",
    "runtime_capabilities": {},
}


class FakeRunner:
    """执行栈替身：路由接线测试只关心「调用发生了没有」与请求字段。"""

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.cancelled: list[dict[str, Any]] = []
        self.retried: list[dict[str, Any]] = []

    async def start(self, request: Any) -> dict[str, Any]:
        self.started.append(_fields(request))
        return {
            "collaboration_id": "c-1",
            "batch_id": "b-1",
            "status": "queued",
            "stage": "",
            "session_key": f"websocket:{_fields(request)['chat_id']}",
            "active_batch_id": "b-1",
        }

    async def reply(
        self, *, collaboration_id: str, feedback: str, expect_version: int,
    ) -> dict[str, Any]:
        return {"id": "b-2", "status": "queued"}

    async def cancel(self, *, collaboration_id: str, active_batch_id: str) -> dict[str, Any]:
        self.cancelled.append({
            "collaboration_id": collaboration_id,
            "active_batch_id": active_batch_id,
        })
        return {"id": collaboration_id, "status": "cancelled", "cancel_generation": 1}

    async def retry(
        self, *, collaboration_id: str, batch_id: str, expect_version: int,
    ) -> dict[str, Any]:
        self.retried.append({
            "collaboration_id": collaboration_id,
            "batch_id": batch_id,
            "expect_version": expect_version,
        })
        return {"id": batch_id, "status": "queued", "attempt": 2}


def _fields(request: Any) -> dict[str, Any]:
    """展开 ``StartCollabRequest``（dataclass 或等价映射），字段名逐字对齐冻结接口。"""
    names = (
        "client_request_id", "article_id", "chat_id", "action", "base_revision",
        "snapshot_digest", "instruction", "selection", "material_ids",
    )
    if isinstance(request, Mapping):
        data: Mapping[str, Any] = request
    else:
        data = {name: getattr(request, name) for name in names}
    return {name: data.get(name) for name in names}


def _collab_operations(
    tmp_path: Path, *, runner: Any | None = None, workspace_id: str = "default",
) -> WritingCollabSettingsOperations:
    return build_writing_collab_operations(
        workspace_id=workspace_id, workspace_path=tmp_path, runner=runner,
    )


def _router(
    config_path: Path,
    *,
    writing_operations: Any = None,
    collab_operations: WritingCollabSettingsOperations | None = None,
    check_api_token: Any = None,
) -> WebUISettingsRouter:
    args = dict(WS_BASE_ARGS)
    if check_api_token is not None:
        args["check_api_token"] = check_api_token
    return WebUISettingsRouter(
        settings=WebUISettingsServices.create(config_path),
        writing_operations=writing_operations,
        writing_collab_operations=collab_operations,
        **args,
    )


def _get(path: str) -> SimpleNamespace:
    return SimpleNamespace(path=path, headers=Headers())


def _mutation_request(path: str, payload: dict[str, Any]) -> SimpleNamespace:
    request = _get(path)
    request._nanobot_webui_mutation_request = True
    request._nanobot_webui_mutation_payload = payload
    request._nanobot_trusted_proxy_authenticated = True
    return request


def _article(tmp_path: Path) -> dict[str, Any]:
    from nanobot.webui import writing_api
    from nanobot.webui.writing_services import WritingServices

    services = WritingServices.for_workspace("default", tmp_path)
    return dict(writing_api.create_article(
        services, client_request_id="r-article-1", title="标题", body="第一段\n第二段",
    )["article"])


@pytest.mark.asyncio
async def test_read_actions_over_authenticated_http(tmp_path: Path) -> None:
    """契约 §3：两个读动作走认证 HTTP GET；跨工作区不可见。"""
    article = _article(tmp_path)
    router = _router(
        tmp_path / "config.json", collab_operations=_collab_operations(tmp_path),
    )
    response = await router.dispatch(
        None,
        _get(f"/api/writing/collab?article_id={article['id']}"),
        "/api/writing/collab",
    )
    assert response is not None and response.status_code == 200
    body = json.loads(response.body)
    assert body["collaboration"] is None
    assert body["next_actions"] == ["start_full", "ideate"]

    empty = await router.dispatch(
        None, _get("/api/writing/collab/artifact?artifact_id=missing"),
        "/api/writing/collab/artifact",
    )
    assert empty is not None and empty.status_code == 404
    assert json.loads(empty.body)["error"] == "artifact not found"

    other = _router(
        tmp_path / "other-config.json",
        collab_operations=_collab_operations(tmp_path / "other", workspace_id="other"),
    )
    cross = await other.dispatch(
        None,
        _get(f"/api/writing/collab?article_id={article['id']}"),
        "/api/writing/collab",
    )
    assert cross is not None and cross.status_code == 200
    assert json.loads(cross.body)["collaboration"] is None


@pytest.mark.asyncio
async def test_read_actions_without_token_are_401(tmp_path: Path) -> None:
    router = _router(
        tmp_path / "config.json",
        collab_operations=_collab_operations(tmp_path),
        check_api_token=lambda _request: False,
    )
    for path in sorted(EXPECTED_READ_ROUTES):
        response = await router.dispatch(None, _get(path), path)
        assert response is not None
        assert response.status_code == 401, path
        assert json.loads(response.body)["error"] == "Unauthorized"


@pytest.mark.asyncio
async def test_collab_write_paths_direct_http_is_405(tmp_path: Path) -> None:
    router = _router(
        tmp_path / "config.json", collab_operations=_collab_operations(tmp_path),
    )
    for path in sorted(EXPECTED_WRITE_PATHS):
        response = await router.dispatch(None, _get(path), path)
        assert response is not None, path
        assert response.status_code == 405, path
        assert json.loads(response.body)["error"] == (
            "WebUI mutations require an authenticated WebSocket"
        )


@pytest.mark.asyncio
async def test_control_action_without_websocket_is_405(tmp_path: Path) -> None:
    router = _router(
        tmp_path / "config.json", collab_operations=_collab_operations(tmp_path),
    )
    path = "/api/writing/collab/start"
    response = await router.dispatch(None, _get(path), path)
    assert response is not None and response.status_code == 405


@pytest.mark.asyncio
async def test_control_actions_run_on_the_event_loop(tmp_path: Path) -> None:
    """契约 §3：``writing.start`` 在 loop 上直接调用 runner（不是 to_thread）。"""
    article = _article(tmp_path)
    runner = FakeRunner()
    router = _router(
        tmp_path / "config.json",
        collab_operations=_collab_operations(tmp_path, runner=runner),
    )
    payload = {
        "client_request_id": "r-1", "article_id": article["id"], "chat_id": "chat-1",
        "action": "start_full", "base_revision": article["revision"],
        "snapshot_digest": ARTICLE_DIGEST,
    }
    path = "/api/writing/collab/start"
    response = await router.dispatch(None, _mutation_request(path, payload), path)

    assert response is not None and response.status_code == 200, response
    assert len(runner.started) == 1
    assert runner.started[0]["chat_id"] == "chat-1"


@pytest.mark.asyncio
async def test_router_dispatches_apply_over_websocket(tmp_path: Path) -> None:
    router = _router(
        tmp_path / "config.json", collab_operations=_collab_operations(tmp_path),
    )
    path = "/api/writing/collab/apply"
    response = await router.dispatch(
        None,
        _mutation_request(path, {
            "client_request_id": "r-1", "suggestion_id": "s-1",
            "article_id": "a-1", "base_revision": 1,
        }),
        path,
    )
    assert response is not None
    assert response.status_code == 409
    assert json.loads(response.body)["error"] == "suggestion is not pending"


@pytest.mark.asyncio
async def test_router_without_collab_operations_is_503(tmp_path: Path) -> None:
    router = _router(tmp_path / "config.json", collab_operations=None)
    path = "/api/writing/collab"
    response = await router.dispatch(
        None, _get(f"{path}?article_id=a-1"), path,
    )
    assert response is not None
    assert response.status_code == 503
    assert json.loads(response.body) == {
        "error": "writing collaboration service is not configured",
    }


@pytest.mark.asyncio
async def test_article_paths_still_work_next_to_collab(tmp_path: Path) -> None:
    """回归：新增协作分支不得抢走既有写作域动作。"""
    router = _router(
        tmp_path / "config.json",
        writing_operations=build_writing_operations(
            workspace_id="default", workspace_path=tmp_path,
        ),
        collab_operations=_collab_operations(tmp_path),
    )
    response = await router.dispatch(
        None, _get("/api/writing/articles?limit=10"), "/api/writing/articles",
    )
    assert response is not None and response.status_code == 200
    assert json.loads(response.body) == {"articles": [], "total": 0, "limit": 10, "offset": 0}


# ---- R7: 事件链（WritingEvent → 投影 → 帧）---------------------------------


class _RecordingTransport:
    """投影层只认 Protocol：这里记录每一次可观察的 wire 调用。"""

    def __init__(self) -> None:
        self.writing: list[tuple[str, str, dict[str, Any]]] = []
        self.payloads: list[Any] = []

    def webui_subscribers(self, chat_id: str) -> tuple[Any, ...]:
        return (SimpleNamespace(chat_id=chat_id),)

    async def send_writing_event(
        self, chat_id: str, name: str, body: dict[str, Any],
    ) -> None:
        self.writing.append((chat_id, name, dict(body)))

    async def send_payload(self, chat_id: str, payload: Any, **_kwargs: Any) -> None:
        self.payloads.append((chat_id, payload))

    def __getattr__(self, _name: str) -> Any:
        async def _noop(*_args: Any, **_kwargs: Any) -> None:
            return None

        return _noop


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event,frame_name", MATERIAL_EVENTS, ids=["collab", "artifact", "handoff"],
)
async def test_events_projected_to_frames(event: Any, frame_name: str) -> None:
    transport = _RecordingTransport()
    projector = WebUIOutboundProjector(transport, SimpleNamespace())

    await projector.send(OutboundMessage(
        channel="websocket", chat_id="chat-1", content="", event=event,
    ))

    assert transport.writing == [("chat-1", frame_name, dict(event.body))]
    assert transport.payloads == [], "协作事件不得回落到普通 payload 投影"


def test_writing_event_is_one_isinstance_branch() -> None:
    """契约 §5：``outbound_projection`` 里只有一个 ``isinstance(event, WritingEvent)``。"""
    import inspect

    source = inspect.getsource(outbound_projection)
    assert source.count("isinstance(event, WritingEvent)") == 1


def test_writing_event_frames_are_transient_for_offline_chats() -> None:
    """没有订阅者时只应有 debug 级日志，不落 transcript、不告警。"""
    import inspect

    source = inspect.getsource(outbound_projection)
    quiet_block = source.split("quiet_events = (", 1)[1].split(")", 1)[0]
    assert "WritingEvent" in quiet_block


def test_runtime_has_a_single_writing_event_writer() -> None:
    """契约 §5：``runtime.py`` 只增一个 ``send_writing_event``。"""
    import inspect

    from nanobot.channels.websocket.runtime import WebSocketChannel

    source = inspect.getsource(WebSocketChannel)
    assert source.count("async def send_writing_event(") == 1
    assert '{"event": name, "chat_id": chat_id, **body}' in inspect.getsource(
        WebSocketChannel.send_writing_event
    )


@pytest.mark.asyncio
async def test_runtime_writing_event_fan_out_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """帧形状逐字锁定：``{"event": <name>, "chat_id": <chat>, ...body}``。"""
    from nanobot.channels.websocket.runtime import WebSocketChannel

    channel = WebSocketChannel.__new__(WebSocketChannel)
    channel._subs = {"chat-1": {object()}}  # type: ignore[dict-item]
    channel._connection_outbound = {}

    frames: list[str] = []

    async def _capture(_connection: Any, raw: str, **_kwargs: Any) -> None:
        frames.append(raw)

    monkeypatch.setattr(channel, "_safe_send_to", _capture)
    await channel.send_writing_event(
        "chat-1",
        "writing_collab",
        {"collaboration_id": "c-1", "status": "queued", "stage": "", "version": 2, "seq": 3},
    )

    assert frames == [json.dumps({
        "event": "writing_collab", "chat_id": "chat-1", "collaboration_id": "c-1",
        "status": "queued", "stage": "", "version": 2, "seq": 3,
    }, ensure_ascii=False)]


@pytest.mark.asyncio
async def test_runtime_writing_event_without_subscribers_is_a_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nanobot.channels.websocket.runtime import WebSocketChannel

    channel = WebSocketChannel.__new__(WebSocketChannel)
    channel._subs = {}
    channel._connection_outbound = {}

    async def _fail(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("没有订阅者时不应发送")

    monkeypatch.setattr(channel, "_safe_send_to", _fail)
    await channel.send_writing_event("chat-1", "writing_handoff", {"handoff_id": "h-1"})


# ---- 装配：真 runner 只有存在团队执行栈时才接上 ---------------------------------


class _FakeScheduler:
    """只要 ``observe`` 的调度器替身：装配期只 subscribe，不起跑。"""

    def __init__(self) -> None:
        self.observers: list[Any] = []

    def observe(self, observer: Any) -> None:
        self.observers.append(observer)


def test_collab_runner_assembly_binds_ports_and_event_sink(tmp_path: Path) -> None:
    """有团队执行栈时必须装配**真**执行栈：端口、envelope 出口、收口 sink 全部接上。

    没有这一步，控制动作在生产恒回 503（两份尾盘审查独立确认过的验收阻断项）。
    """
    from nanobot.writing.collab.envelope import EnvelopeArtifactSink
    from nanobot.writing.collab.team_port import TeamWritingPort

    scheduler = _FakeScheduler()
    turns = SimpleNamespace(scheduler=scheduler, runtime_provider=None)
    service = SimpleNamespace(repository=SimpleNamespace())

    runner = gateway_services.build_writing_collab_runner(
        workspace_path=tmp_path,
        team_service=service,
        team_turns=turns,
        bus=SimpleNamespace(publish_outbound=None),
        session_manager=None,
    )

    port = runner._team  # noqa: SLF001 - 装配断言只能看私有槽位
    assert isinstance(port, TeamWritingPort)
    assert isinstance(runner._artifacts, EnvelopeArtifactSink)  # noqa: SLF001
    assert runner._preparer is not None  # noqa: SLF001
    # 调度器事件 → 端口桥接；端口收口出口 → runner.on_team_event。
    assert scheduler.observers == [port.observe_team_events]
    assert port._event_sink == runner.on_team_event  # noqa: SLF001


@pytest.mark.asyncio
async def test_collab_operations_without_runner_stay_503(tmp_path: Path) -> None:
    """没有执行栈时不许假装能起跑：控制动作保持契约 §4 的 503。"""
    operations = _collab_operations(tmp_path)
    # operations 层直接抛领域 503；翻译成 HTTP/WS 结果体是 handler 的职责
    # （路由层已有 test_null_collab_operations_are_503 / ..._is_503 覆盖）。
    with pytest.raises(WebUISettingsError) as excinfo:
        await operations.start(
            client_request_id="r-1", article_id="a-1", chat_id="chat-1",
            action="start_full", base_revision=1, snapshot_digest="d-1",
        )
    assert (excinfo.value.status, excinfo.value.message) == (
        503, "writing collaboration service is not configured",
    )
