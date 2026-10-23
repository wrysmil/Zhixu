"""WebUI settings 域 handler：创作空间固定团队协作。

照 ``writing_routes.py`` / ``teams_routes.py`` 的形状：与传输无关的 handler，
``handle(action, request)`` 对着注入的 operations 分发，返回 ``SettingsRouteResult``。
路径 → action 的映射、WebSocket / HTTP 的门禁都在 ``settings_routes.py``。

读动作（``writing.collab.get`` / ``writing.artifact.get``）走普通认证 HTTP GET；
七个写动作只走已认证 WS 的 ``requestMutation`` 白名单（契约 §3）。其中三个控制动作
（``writing.start`` / ``writing.cancel`` / ``writing.retry``）要叫停/叫醒事件循环上的
在场句柄，所以另有 :meth:`WritingCollabSettingsHandler.handle_control` 一条 async
入口；sync 入口对它们**响亮地**回 500，而不是悄悄 ``to_thread``。

错误翻译只有一处：``writing_collab_api.translate_error``（契约 §4 的 message 原文）。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from loguru import logger

from nanobot.webui.settings_contracts import (
    SettingsRequest,
    SettingsRouteResult,
    WebUISettingsError,
    query_first,
)
from nanobot.webui.writing_collab_api import translate_error

# 与传输层 request_id 同正则（契约 §5）：形状校验在同步入口也要做一遍。
_CLIENT_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")

# 本域认识的动作全集；settings router 用它把 action 路由进来。
WRITING_COLLAB_ACTION_NAMES = frozenset({
    "writing.collab.get",
    "writing.artifact.get",
    "writing.start",
    "writing.reply",
    "writing.confirm",
    "writing.cancel",
    "writing.retry",
    "writing.apply",
    "writing.undo",
})

#: 读动作：认证 HTTP GET 即可（契约 §3）。
WRITING_COLLAB_READ_ACTION_NAMES = frozenset({
    "writing.collab.get",
    "writing.artifact.get",
})

#: 写动作：只走已认证 WS 的 ``requestMutation``。
WRITING_COLLAB_MUTATION_ACTION_NAMES = (
    WRITING_COLLAB_ACTION_NAMES - WRITING_COLLAB_READ_ACTION_NAMES
)

#: 控制动作：必须在事件循环上执行（叫停/叫醒在场批次句柄），不能包 ``to_thread``。
#: ``writing.reply`` 同样是协程（要 await runner.start），所以一并列入。
WRITING_COLLAB_CONTROL_ACTION_NAMES = frozenset({
    "writing.start",
    "writing.cancel",
    "writing.retry",
    "writing.reply",
})

#: HTTP 路径 → 动作（契约 §3）。这是路由三表的唯一真相源：
#: ``settings_routes._SYSTEM_ROUTES`` 直接展开它，``_WRITING_COLLAB_MUTATION_PATHS``
#: 与 ``ws_http._WEBUI_MUTATION_PATHS`` 由它按写动作名单派生。新增动作时**先**改
#: 这里，再同步三表。
WRITING_COLLAB_ROUTE_PATHS: dict[str, str] = {
    "/api/writing/collab": "writing.collab.get",
    "/api/writing/collab/artifact": "writing.artifact.get",
    "/api/writing/collab/start": "writing.start",
    "/api/writing/collab/reply": "writing.reply",
    "/api/writing/collab/confirm": "writing.confirm",
    "/api/writing/collab/cancel": "writing.cancel",
    "/api/writing/collab/retry": "writing.retry",
    "/api/writing/collab/apply": "writing.apply",
    "/api/writing/collab/undo": "writing.undo",
}

#: 读动作的路径集合（HTTP GET 放行）。
WRITING_COLLAB_READ_PATHS = frozenset(
    path
    for path, action in WRITING_COLLAB_ROUTE_PATHS.items()
    if action in WRITING_COLLAB_READ_ACTION_NAMES
)

#: 写动作的路径集合（HTTP 直连必须 405）。
WRITING_COLLAB_MUTATION_PATHS = frozenset(
    path
    for path, action in WRITING_COLLAB_ROUTE_PATHS.items()
    if action in WRITING_COLLAB_MUTATION_ACTION_NAMES
)

#: 写动作 → 路径（WS ``requestMutation`` 的 action 翻译表口径）。
WRITING_COLLAB_MUTATION_WS_ACTIONS: dict[str, str] = {
    action: path
    for path, action in sorted(WRITING_COLLAB_ROUTE_PATHS.items())
    if action in WRITING_COLLAB_MUTATION_ACTION_NAMES
}

#: ``writing.start`` 允许透传给 payload 层的可选业务字段；归属字段（teamId/agentId/
#: skillPath…）不在其中，因此永远不会影响服务端解析的团队与角色（契约 §3）。
_OPTIONAL_START_FIELDS = ("instruction", "material_ids", "selection")


@dataclass(frozen=True)
class WritingCollabSettingsOperations:
    """注入 handler 的传输无关入口（已绑定 workspace 依赖）。

    ``start`` / ``cancel`` / ``retry`` 是协程（控制动作必须跑在 loop 上）；
    其余是可在线程里跑的阻塞短事务。参数一律关键字传递，payload 层签名调整
    不会在这里静默错位。
    """

    get_collaboration: Callable[..., dict[str, Any]]
    get_artifact: Callable[..., dict[str, Any]]
    start: Callable[..., Awaitable[dict[str, Any]]]
    reply: Callable[..., Awaitable[dict[str, Any]]]
    confirm: Callable[..., dict[str, Any]]
    cancel: Callable[..., Awaitable[dict[str, Any]]]
    retry: Callable[..., Awaitable[dict[str, Any]]]
    apply: Callable[..., dict[str, Any]]
    undo: Callable[..., dict[str, Any]]


class WritingCollabSettingsHandler:
    """把单个协作域动作分发到注入的 operations 上。"""

    def __init__(self, operations: WritingCollabSettingsOperations) -> None:
        self._ops = operations

    def handle(self, action: str, request: SettingsRequest) -> SettingsRouteResult:
        if action not in WRITING_COLLAB_ACTION_NAMES:
            return SettingsRouteResult.failure(
                404, f"unknown writing collaboration action: {action}",
            )
        if action in WRITING_COLLAB_CONTROL_ACTION_NAMES:
            try:
                control_arguments(action, request)
            except WebUISettingsError as exc:
                return SettingsRouteResult.failure(exc.status, exc.message)
            # 控制动作包进 to_thread 就永远叫不醒在场句柄（契约 §3）。
            return SettingsRouteResult.failure(
                500, f"{action} must run on the event loop",
            )
        try:
            payload = dispatch(self._ops, action, request)
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        except Exception as exc:  # 领域异常统一翻译成契约 §4 的 message
            return self._failure(exc)
        return SettingsRouteResult.success(payload)

    async def handle_control(
        self, action: str, request: SettingsRequest
    ) -> SettingsRouteResult:
        """控制动作的异步入口：必须跑在 loop 上（``start``/``cancel``/``retry``）。"""
        if action not in WRITING_COLLAB_CONTROL_ACTION_NAMES:
            return SettingsRouteResult.failure(
                404, f"unknown writing collaboration control action: {action}",
            )
        try:
            payload = await dispatch_control(self._ops, action, request)
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        except Exception as exc:
            return self._failure(exc)
        return SettingsRouteResult.success(payload)

    @staticmethod
    def _failure(error: BaseException) -> SettingsRouteResult:
        try:
            status, message = translate_error(error)
        except BaseException:
            # 未登记的异常：固定文案，内部细节不出网（排查靠日志，不靠 error 字符串）。
            logger.opt(exception=error).error(
                "writing collaboration action failed: {}", type(error).__name__,
            )
            return SettingsRouteResult.failure(500, "writing collaboration request failed")
        return SettingsRouteResult.failure(status, message)


# ---- per-action dispatch ---------------------------------------------------


def dispatch(
    operations: WritingCollabSettingsOperations,
    action: str,
    request: SettingsRequest,
) -> dict[str, Any]:
    payload = request.payload or {}

    if action in WRITING_COLLAB_CONTROL_ACTION_NAMES:
        # 控制动作有协程入口（``dispatch_control``）；sync 分支只负责把「路由接错
        # 执行栈」这件事说清楚，而不是报一个 404 未知动作。
        raise WebUISettingsError(
            f"{action} must run on the event loop", status=500,
        )

    if action == "writing.collab.get":
        return operations.get_collaboration(
            article_id=_query_first(request, "article_id") or payload.get("article_id"),
        )

    if action == "writing.artifact.get":
        return operations.get_artifact(
            artifact_id=_query_first(request, "artifact_id") or payload.get("artifact_id"),
        )

    if action == "writing.reply":
        # reply 也是协程（要 await runner.start），只走 handle_control。
        raise WebUISettingsError(
            "writing.reply must run on the event loop", status=500,
        )

    if action == "writing.confirm":
        return operations.confirm(
            client_request_id=payload.get("client_request_id"),
            collaboration_id=payload.get("collaboration_id"),
            handoff_id=payload.get("handoff_id"),
            expected_handoff_version=payload.get("expected_handoff_version"),
            article_revision=payload.get("article_revision"),
            article_digest=payload.get("article_digest"),
            outline=payload.get("outline"),
        )

    if action == "writing.apply":
        return operations.apply(
            client_request_id=payload.get("client_request_id"),
            suggestion_id=_apply_suggestion_id(payload),
            article_id=payload.get("article_id"),
            base_revision=payload.get("base_revision"),
        )

    if action == "writing.undo":
        return operations.undo(
            client_request_id=payload.get("client_request_id"),
            undo_id=payload.get("undo_id"),
            base_revision=payload.get("base_revision"),
        )

    # 已知动作不会走到这里；留着作为将来新增动作时的兜底。
    raise WebUISettingsError(f"unsupported writing collaboration action: {action}")


async def dispatch_control(
    operations: WritingCollabSettingsOperations,
    action: str,
    request: SettingsRequest,
) -> dict[str, Any]:
    payload = request.payload or {}
    if action == "writing.start":
        extras = sanitized_extras(payload)
        return await operations.start(
            client_request_id=payload.get("client_request_id"),
            article_id=payload.get("article_id"),
            chat_id=payload.get("chat_id"),
            action=payload.get("action"),
            base_revision=payload.get("base_revision"),
            snapshot_digest=payload.get("snapshot_digest"),
            instruction=extras.get("instruction", ""),
            selection=extras.get("selection"),
            material_ids=extras.get("material_ids"),
        )

    if action == "writing.reply":
        return await operations.reply(
            client_request_id=payload.get("client_request_id"),
            collaboration_id=payload.get("collaboration_id"),
            feedback=payload.get("feedback"),
            expect_version=payload.get("expect_version"),
        )

    if action == "writing.cancel":
        return await operations.cancel(
            client_request_id=payload.get("client_request_id"),
            collaboration_id=payload.get("collaboration_id"),
            # 契约 §3 的取消入参是 active_batch_id（不是 batch_id）。
            active_batch_id=payload.get("active_batch_id"),
        )

    return await operations.retry(
        client_request_id=payload.get("client_request_id"),
        collaboration_id=payload.get("collaboration_id"),
        batch_id=payload.get("batch_id"),
        expect_version=payload.get("expect_version"),
    )


def control_arguments(action: str, request: SettingsRequest) -> dict[str, Any]:
    """控制动作的同步校验（两条入口共用，且必须早于「路由接错执行栈」的判断）。"""
    payload = request.payload or {}
    if not _valid_request_id(payload.get("client_request_id")):
        raise WebUISettingsError("client_request_id is required", status=400)
    if action == "writing.start":
        for field in ("article_id", "chat_id", "action", "snapshot_digest"):
            _require_present(payload.get(field), field)
        _require_present(payload.get("base_revision"), "base_revision")
        sanitized_extras(payload)
    elif action == "writing.cancel":
        _require_present(payload.get("collaboration_id"), "collaboration_id")
        _require_present(payload.get("active_batch_id"), "active_batch_id")
    else:
        _require_present(payload.get("collaboration_id"), "collaboration_id")
        _require_present(payload.get("batch_id"), "batch_id")
        _require_present(payload.get("expect_version"), "expect_version")
    return payload


def _valid_request_id(raw: Any) -> bool:
    """与 payload 层同一正则：形状不对的 request_id 是 400，不是幂等键。"""
    return isinstance(raw, str) and _CLIENT_REQUEST_ID_RE.fullmatch(raw) is not None


def sanitized_extras(payload: Mapping[str, Any]) -> dict[str, Any]:
    """只保留 ``writing.start`` 允许的业务字段，归属字段一律丢弃（契约 §3）。

    ``teamId`` / ``agentId`` / ``skillPath`` 之类既不报错也不进入 ``extras``——
    团队、角色、Skill、模型一律服务端解析（契约 §3）。
    """
    extras: dict[str, Any] = {}
    for key in _OPTIONAL_START_FIELDS:
        if key not in payload or payload[key] is None:
            continue
        extras[key] = payload[key]
    if "instruction" in extras and not isinstance(extras["instruction"], str):
        raise WebUISettingsError("instruction must be a string of characters", status=400)
    if "material_ids" in extras:
        raw = extras["material_ids"]
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise WebUISettingsError("material_ids must be a list of strings", status=400)
        if any(not isinstance(item, str) for item in raw):
            raise WebUISettingsError("material_ids must be a list of strings", status=400)
        extras["material_ids"] = list(raw)
    if "selection" in extras:
        extras["selection"] = _checked_selection(extras["selection"])
    return extras


def _checked_selection(raw: Any) -> dict[str, Any]:
    """选区只校验形状：``text`` 缺省由 payload 层补空串，路由层不凭空造字段。"""
    if not isinstance(raw, Mapping):
        raise WebUISettingsError("selection must be an object", status=400)
    data: dict[str, Any] = dict(raw)
    for key in ("start", "end"):
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise WebUISettingsError("selection must be an object", status=400)
    text = data.get("text")
    if text is not None and not isinstance(text, str):
        raise WebUISettingsError("selection must be an object", status=400)
    if text is None:
        data.pop("text", None)
    return data


def _apply_suggestion_id(payload: Mapping[str, Any]) -> Any:
    single = payload.get("suggestion_id")
    if single is not None and single != "":
        return single
    batch = payload.get("suggestion_ids")
    if isinstance(batch, Sequence) and not isinstance(batch, (str, bytes)) and batch:
        return list(batch)[0]
    return None


def _require_present(raw: Any, field: str) -> Any:
    if raw is None or raw == "":
        raise WebUISettingsError(f"{field} is required", status=400)
    return raw


def _query_first(request: SettingsRequest, key: str) -> Any:
    """query 取参；空串视作未提供，让 payload / 必填校验接手。"""
    raw = query_first(request.query, key)
    if isinstance(raw, str):
        return raw.strip() or None
    return raw


__all__ = [
    "WRITING_COLLAB_ACTION_NAMES",
    "WRITING_COLLAB_MUTATION_PATHS",
    "WRITING_COLLAB_MUTATION_WS_ACTIONS",
    "WRITING_COLLAB_READ_PATHS",
    "WRITING_COLLAB_ROUTE_PATHS",
    "WRITING_COLLAB_CONTROL_ACTION_NAMES",
    "WRITING_COLLAB_MUTATION_ACTION_NAMES",
    "WRITING_COLLAB_READ_ACTION_NAMES",
    "WritingCollabSettingsHandler",
    "WritingCollabSettingsOperations",
    "control_arguments",
    "dispatch",
    "dispatch_control",
    "sanitized_extras",
]
