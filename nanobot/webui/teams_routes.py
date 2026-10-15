"""WebUI settings 域 handler：Agent Team 管理。

照 ``agents_routes.py`` 的形状：与传输无关的 handler，``handle(action, request)``
对着注入进来的 operations 分发，返回 ``SettingsRouteResult``。路径 → action 的映射、
WebSocket / HTTP 的门禁都在 ``settings_routes.py``。

读动作（list / detail / revisions）走普通认证 HTTP GET；写动作（save / archive /
clone）改变磁盘状态，只走已认证 WS 的 ``requestMutation`` 白名单（契约 §3）。

领域异常在这儿翻译成契约 §4 的错误码前缀：传输层的信封仍是全站统一的
``{error: string}``，代码放在消息前缀里，本功能不单独改 error 结构。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from nanobot.teams.database import StorageUnavailable, WriterBusy
from nanobot.teams.leader import ModelPresetUnavailable
from nanobot.teams.models import TeamValidationError
from nanobot.teams.repository import (
    DispatchKeyConflict,
    RequestReplayConflict,
    RunNotFound,
    TeamNotFound,
    TeamRevisionConflict,
)
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

# 本域认识的动作全集；settings router 用它把 action 路由进来。
TEAMS_ACTION_NAMES = frozenset({
    "teams-list",
    "teams-detail",
    "teams-revisions",
    "teams-save",
    "teams-archive",
    "teams-clone",
})

_DEFAULT_LIST_LIMIT = 20


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


class TeamSettingsHandler:
    """把单个团队域动作分发到注入进来的 operations 上。"""

    def __init__(self, operations: TeamSettingsOperations) -> None:
        self._ops = operations

    def handle(self, action: str, request: SettingsRequest) -> SettingsRouteResult:
        if action not in TEAMS_ACTION_NAMES:
            return SettingsRouteResult.failure(404, f"unknown team action: {action}")
        try:
            payload = dispatch(self._ops, action, request)
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        except Exception as exc:  # 领域异常统一翻译成契约 §4 的错误码
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
    TeamRevisionConflict: (409, "TEAM_REVISION_CONFLICT"),
    TeamProfileUnavailable: (409, "TEAM_MEMBER_UNAVAILABLE"),
    RequestReplayConflict: (409, "TEAM_REVISION_CONFLICT"),
    DispatchKeyConflict: (409, "DISPATCH_KEY_CONFLICT"),
    TeamDisabled: (503, "TEAMS_DISABLED"),
    WriterBusy: (503, "STORAGE_UNAVAILABLE"),
    StorageUnavailable: (503, "STORAGE_UNAVAILABLE"),
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

    # 已知动作不会走到这里；留着作为将来新增动作时的兜底。
    raise WebUISettingsError(f"unsupported team action: {action}")


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
