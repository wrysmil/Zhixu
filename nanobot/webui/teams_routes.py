"""WebUI settings 域 handler：Agent Team 管理。

照 ``agents_routes.py`` 的形状：与传输无关的 handler，``handle(action, request)``
对着注入进来的 operations 分发，返回 ``SettingsRouteResult``。路径 → action 的映射、
WebSocket / HTTP 的门禁都在 ``settings_routes.py``。

读动作（团队 list / detail / revisions，运行 list / detail / events）走普通认证 HTTP
GET；写动作（save / archive / clone / cancel / retry）改变状态，只走已认证 WS 的
``requestMutation`` 白名单（契约 §3）。其中三个 run 控制动作要叫醒事件循环上的活句柄，
所以另有 :meth:`TeamSettingsHandler.handle_control` 一条 async 入口。

领域异常在这儿翻译成契约 §5 的错误码前缀：传输层的信封仍是全站统一的
``{error: string}``，代码放在消息前缀里，本功能不单独改 error 结构。
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Callable

from nanobot.teams.database import StorageUnavailable, WriterBusy
from nanobot.teams.leader import ModelPresetUnavailable
from nanobot.teams.models import TeamValidationError
from nanobot.teams.repository import (
    DispatchKeyConflict,
    RequestReplayConflict,
    RunNotFound,
    TaskNotFound,
    TeamNotFound,
    TeamRevisionConflict,
)
from nanobot.teams.runs import CrossSessionForbidden
from nanobot.teams.service import (
    TeamArchived,
    TeamDisabled,
    TeamProfileUnavailable,
    TeamReferenceError,
)
from nanobot.webui.settings_contracts import (
    SettingsRequest,
    SettingsRouteResult,
    WebUISettingsError,
)
from nanobot.writing.builtin import BUILTIN_RESOURCE_PROTECTED, BuiltinResourceProtected

# 本域认识的动作全集；settings router 用它把 action 路由进来。
TEAMS_ACTION_NAMES = frozenset({
    "teams-list",
    "teams-detail",
    "teams-revisions",
    "teams-save",
    "teams-archive",
    "teams-clone",
    "team-runs-list",
    "team-run-detail",
    "team-run-events",
    "team-run-cancel",
    "team-task-cancel",
    "team-run-retry",
})

# 控制动作要叫醒事件循环上的活句柄（``TeamRunHandle``），所以 handler 必须直接在 loop
# 上跑：settings router 给这一组名单开了一条不包 ``asyncio.to_thread`` 的分支。
TEAMS_CONTROL_ACTION_NAMES = frozenset({
    "team-run-cancel",
    "team-task-cancel",
    "team-run-retry",
})

_DEFAULT_LIST_LIMIT = 20
_DEFAULT_EVENT_LIMIT = 200


@dataclass(frozen=True)
class TeamSettingsOperations:
    """注入 handler 的传输无关入口，每个字段都已绑好 workspace 依赖。

    写成普通协议而不是继承某个基类，测试才能塞内存替身而不碰磁盘；参数一律用
    关键字传递，领域层的签名调整不会在这里静默错位。
    """

    list_teams: Callable[..., dict[str, Any]]
    get_detail: Callable[..., dict[str, Any]]
    list_revisions: Callable[..., dict[str, Any]]
    save_team: Callable[..., dict[str, Any]]
    archive_team: Callable[..., dict[str, Any]]
    clone_team: Callable[..., dict[str, Any]]
    list_runs: Callable[..., dict[str, Any]]
    run_detail: Callable[..., dict[str, Any]]
    run_events: Callable[..., dict[str, Any]]
    # 三个控制动作是协程：它们要在 loop 上叫醒活句柄。
    cancel_run: Callable[..., Awaitable[dict[str, Any]]]
    cancel_task: Callable[..., Awaitable[dict[str, Any]]]
    retry_run: Callable[..., Awaitable[dict[str, Any]]]


class TeamSettingsHandler:
    """把单个团队域动作分发到注入进来的 operations 上。"""

    def __init__(self, operations: TeamSettingsOperations) -> None:
        self._ops = operations

    def handle(self, action: str, request: SettingsRequest) -> SettingsRouteResult:
        if action not in TEAMS_ACTION_NAMES:
            return SettingsRouteResult.failure(404, f"unknown team action: {action}")
        if action in TEAMS_CONTROL_ACTION_NAMES:
            # 控制动作在别的线程里叫醒不了活句柄，也不会自动跑到事件循环上。
            try:
                control_arguments(action, request)
            except WebUISettingsError as exc:
                return SettingsRouteResult.failure(exc.status, exc.message)
            return SettingsRouteResult.failure(404, "team control action needs handle_control")
        try:
            payload = dispatch(self._ops, action, request)
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        except Exception as exc:  # 领域异常统一翻译成契约 §4 的错误码
            status, message = translate(exc)
            return SettingsRouteResult.failure(status, message)
        return SettingsRouteResult.success(payload)

    async def handle_control(
        self,
        action: str,
        request: SettingsRequest,
    ) -> SettingsRouteResult:
        """取消/重试的异步入口：必须跑在 loop 上（``runs.py`` 要碰活句柄）。"""
        if action not in TEAMS_CONTROL_ACTION_NAMES:
            return SettingsRouteResult.failure(404, f"unknown team control action: {action}")
        try:
            payload = await dispatch_control(self._ops, action, request)
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        except Exception as exc:
            status, message = translate(exc)
            return SettingsRouteResult.failure(status, message)
        return SettingsRouteResult.success(payload)


# ---- 领域异常 → 传输层状态码与错误码前缀 ------------------------------------

# (状态码, 错误码前缀)。TeamArchived 按契约 §4 对外表现为 TEAM_NOT_FOUND：
# 归档团队不是「存在但你不能看」，而是「不再是一个可用团队」。
_ERROR_CODES: dict[type[BaseException], tuple[int, str]] = {
    TeamValidationError: (400, "TEAM_INVALID"),
    TeamReferenceError: (400, "TEAM_INVALID"),
    ModelPresetUnavailable: (400, "MODEL_PRESET_UNAVAILABLE"),
    TeamNotFound: (404, "TEAM_NOT_FOUND"),
    TeamArchived: (404, "TEAM_NOT_FOUND"),
    RunNotFound: (404, "RUN_NOT_FOUND"),
    TaskNotFound: (404, "TASK_NOT_FOUND"),
    TeamRevisionConflict: (409, "TEAM_REVISION_CONFLICT"),
    TeamProfileUnavailable: (409, "TEAM_MEMBER_UNAVAILABLE"),
    RequestReplayConflict: (409, "TEAM_REVISION_CONFLICT"),
    DispatchKeyConflict: (409, "DISPATCH_KEY_CONFLICT"),
    CrossSessionForbidden: (403, "CROSS_SESSION_FORBIDDEN"),
    TeamDisabled: (503, "TEAMS_DISABLED"),
    WriterBusy: (503, "STORAGE_UNAVAILABLE"),
    StorageUnavailable: (503, "STORAGE_UNAVAILABLE"),
    # 创作空间内置团队受保护（契约 §4）：业务码与异常自带的 message 原文一致，
    # ``translate`` 的 ``startswith`` 去重逻辑据此不会拼两遍。
    BuiltinResourceProtected: (409, BUILTIN_RESOURCE_PROTECTED),
}


def translate(error: BaseException) -> tuple[int, str]:
    """把领域异常翻成 ``(状态码, "错误码 原文")``。

    未登记的异常类型只回一句固定文案：内部细节（SQL、路径、档案名）不该出现在
    客户端能读到的错误里，排查靠日志而不是 error 字符串。
    """
    for kind, (status, code) in _ERROR_CODES.items():
        if isinstance(error, kind):
            detail = str(error).strip()
            if not detail or detail.startswith(code):
                # 有的领域异常（TeamRevisionConflict）自己就带错误码，别拼两遍。
                return status, detail or code
            return status, f"{code} {detail}"
    return 500, "team request failed"


# ---- per-action dispatch ---------------------------------------------------


def dispatch(
    operations: TeamSettingsOperations,
    action: str,
    request: SettingsRequest,
) -> dict[str, Any]:
    payload = request.payload or {}

    if action == "teams-list":
        return operations.list_teams(
            limit=_int_query(request, "limit", _DEFAULT_LIST_LIMIT),
            cursor=_opt_query(request, "cursor"),
        )

    if action == "teams-detail":
        return operations.get_detail(
            team_id=_required_text(request, "id"),
            revision=_opt_int_query(request, "revision"),
        )

    if action == "teams-revisions":
        return operations.list_revisions(team_id=_required_text(request, "id"))

    if action == "teams-save":
        team = payload.get("team")
        if not isinstance(team, dict):
            raise WebUISettingsError("team must be an object", status=400)
        return operations.save_team(
            payload=team,
            expected_revision=_opt_int_payload(payload, "expectedRevision"),
            request_id=_required_request_id(payload),
        )

    if action == "teams-archive":
        return operations.archive_team(
            team_id=_required_text(request, "id", in_payload=True),
            expected_revision=_required_int_payload(payload, "expectedRevision"),
            request_id=_required_request_id(payload),
        )

    if action == "teams-clone":
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise WebUISettingsError("name is required", status=400)
        return operations.clone_team(
            team_id=_required_text(request, "id", in_payload=True),
            name=name.strip(),
            request_id=_required_request_id(payload),
        )

    if action == "team-runs-list":
        return operations.list_runs(
            chat_id=_required_text(request, "chatId"),
            limit=_int_query(request, "limit", _DEFAULT_LIST_LIMIT),
            cursor=_opt_query(request, "cursor"),
            caller_chats=request.caller_chats,
        )

    if action == "team-run-detail":
        return operations.run_detail(
            run_id=_required_text(request, "runId"),
            caller_chats=request.caller_chats,
        )

    if action == "team-run-events":
        return operations.run_events(
            run_id=_required_text(request, "runId"),
            after_seq=_int_query(request, "afterSeq", 0),
            limit=_int_query(request, "limit", _DEFAULT_EVENT_LIMIT),
            caller_chats=request.caller_chats,
        )

    # 已知动作不会走到这里；留着作为将来新增动作时的兜底。
    raise WebUISettingsError(f"unsupported team action: {action}")


async def dispatch_control(
    operations: TeamSettingsOperations,
    action: str,
    request: SettingsRequest,
) -> dict[str, Any]:
    """取消与重试（契约 §5 三条 run mutation）：参数一律来自 mutation payload。"""
    arguments = control_arguments(action, request)
    caller = request.caller_chats
    if action == "team-run-cancel":
        return await operations.cancel_run(run_id=arguments["run_id"], caller_chats=caller)

    if action == "team-task-cancel":
        return await operations.cancel_task(
            run_id=arguments["run_id"], task_id=arguments["task_id"], caller_chats=caller
        )

    return await operations.retry_run(
        run_id=arguments["run_id"], request_id=arguments["request_id"], caller_chats=caller
    )


def control_arguments(action: str, request: SettingsRequest) -> dict[str, Any]:
    """控制动作的入参校验；同步部分两条入口共用。

    缺 ``requestId`` 是 400 的调用方错误，与「这条路由是不是接了执行栈」无关，所以要在
    ``handle`` 的 404 分支之前就验掉。
    """
    payload = request.payload or {}
    arguments: dict[str, Any] = {"run_id": _required_text(request, "runId", in_payload=True)}
    if action == "team-task-cancel":
        arguments["task_id"] = _required_text(request, "taskId", in_payload=True)
    _required_request_id(payload)
    arguments["request_id"] = payload["requestId"].strip()
    return arguments


# ---- 参数取值 --------------------------------------------------------------


def _query(request: SettingsRequest, key: str) -> str | None:
    values = request.query.get(key)
    if values is None:
        return None
    # QueryParams 声明成 ``list[str]``，但这个 dict 是上游各处手工构造的：拿到裸字符串时
    # ``values[0]`` 会静默取出「第一个字符」，把 id 变成 i。所以运行时也要挡住。
    if isinstance(values, str):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise WebUISettingsError(f"{key} must be a list of values", status=400)
    if not values:
        return None
    return str(values[0]).strip() or None


def _int_query(request: SettingsRequest, key: str, default: int) -> int:
    raw = _query(request, key)
    if raw is None:
        return default
    return _parse_int(raw, key)


def _opt_int_query(request: SettingsRequest, key: str) -> int | None:
    raw = _query(request, key)
    if raw is None:
        return None
    return _parse_int(raw, key)


def _opt_query(request: SettingsRequest, key: str) -> str | None:
    return _query(request, key)


def _parse_int(raw: str, key: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise WebUISettingsError(f"{key} must be an integer", status=400) from None


def _required_text(
    request: SettingsRequest,
    key: str,
    *,
    in_payload: bool = False,
) -> str:
    raw: Any = (request.payload or {}).get(key) if in_payload else _query(request, key)
    if not isinstance(raw, str) or not raw.strip():
        raise WebUISettingsError(f"{key} is required", status=400)
    return raw.strip()


def _opt_int_payload(payload: dict[str, Any], key: str) -> int | None:
    raw = payload.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise WebUISettingsError(f"{key} must be an integer", status=400)
    return int(raw)


def _required_int_payload(payload: dict[str, Any], key: str) -> int:
    raw = _opt_int_payload(payload, key)
    if raw is None:
        raise WebUISettingsError(f"{key} is required", status=400)
    return raw


def _required_request_id(payload: dict[str, Any]) -> str:
    """requestId 是领域幂等键（契约 §2）：缺了就只能依赖「客户端别重试」的口头承诺。"""
    raw = payload.get("requestId")
    if not isinstance(raw, str) or not raw.strip():
        raise WebUISettingsError("requestId is required", status=400)
    return raw.strip()
