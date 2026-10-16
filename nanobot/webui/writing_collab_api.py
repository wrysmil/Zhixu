"""协作域 payload 层：契约 §3 的七个写动作 + 两个读动作 + §4 的错误翻译。

本模块是协作仓储异常、建议定位异常（``OffsetError``）与 WebUI 错误
（``WebUISettingsError``）之间的**唯一**翻译点——handler（``writing_collab_routes.py``）
不再二次包装。翻译表就是：

| 领域异常 | status | message |
| --- | --- | --- |
| ``CollaborationNotFound`` / ``ArtifactNotFound`` / ``HandoffNotFound`` | 404 | 异常原文（``collaboration not found`` …） |
| ``ActiveBatchConflict`` / ``CollabVersionConflict`` / ``NoActiveBatch`` / ``RequestReplayConflict`` | 409 | 异常原文（契约 §4 逐字） |
| ``ArticleLimitError`` | ``exc.status`` | ``exc.message``（``suggestion is not pending`` / ``undo entry not found`` / ``suggestion targets overlap`` …） |
| ``OffsetError`` | 400 | ``suggestion target is invalid: <code>``（实施期裁决 §10.7） |
| ``InvalidFieldError`` | 400 | 异常原文（``unsupported collaboration field: <field>=<value>``） |
| ``ArticleConflict`` / ``ArticleNotFound`` | 409 / 404 | 沿用 A 契约错误体 |

执行栈（``nanobot.writing.collab.runner.CollabRunner``，WU-08 并行实现）只以
:class:`CollabRunnerProtocol` 依赖注入：本模块**不 import runner**，因此 runner
尚未落地时整个 webui 仍可导入、测试可注入假 runner。三个控制动作
（``start`` / ``cancel`` / ``retry``）是协程，必须在事件循环上调用；其余动作是
阻塞 SQLite 短事务，由 ``writing_collab_routes`` 包装 ``asyncio.to_thread``。

客户端**不**能指定团队 / 角色 / Skill / 模型：这些字段一律服务端解析，
payload 里出现同类键值也不会进入 ``goal``（契约 §3）。
"""

from __future__ import annotations

import re
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, cast, runtime_checkable

from nanobot.webui.settings_contracts import WebUISettingsError
from nanobot.writing.collab import ACTIONS
from nanobot.writing.collab.apply import apply_batch
from nanobot.writing.collab.apply import apply_suggestion as _apply_suggestion
from nanobot.writing.collab.apply import undo as _undo
from nanobot.writing.collab.offsets import OffsetError
from nanobot.writing.collab.repository import (
    ActiveBatchConflict,
    ArtifactNotFound,
    BatchNotFound,
    CollaborationNotFound,
    CollabRepository,
    CollabVersionConflict,
    HandoffNotFound,
    InvalidFieldError,
    NoActiveBatch,
    RequestReplayConflict,
)
from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import ArticleLimitError
from nanobot.writing.repository import (
    ArticleConflict,
    ArticleNotFound,
    ArticleRepository,
)

# 与传输层 request_id 同正则（契约 §5）：业务级幂等靠 payload 内的显式 id。
_CLIENT_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
# 与 ``is_valid_webui_chat_id`` 同口径（契约 §2：chat_id ^[A-Za-z0-9_:-]{1,64}$）。
_CHAT_ID_RE = re.compile(r"^[A-Za-z0-9_:-]{1,64}$")
#: 客户端**不允许**影响服务端归属的字段（契约 §3）。
PROTECTED_PAYLOAD_FIELDS = frozenset({
    "teamId", "team_id", "agentId", "agent_id", "skillPath", "skill_path",
})
#: 需要非空选区的动作（契约 §4：选区缺失或为空 → 400）。
SELECTION_ACTIONS = frozenset({"rewrite_selection"})


@dataclass(frozen=True)
class WritingCollabServices:
    """一个工作区一套协作依赖：主库 + 协作仓储 + 执行栈（可为 ``None``）。

    ``runner`` 是执行栈（WU-08）；CLI/测试构造的 gateway 可以先只有仓储，
    此时控制动作回 503「协作层未装配」。读动作、``apply`` / ``undo`` 不依赖 runner。
    """

    workspace_id: str
    database: WritingDatabase
    article_repository: ArticleRepository
    collab_repository: CollabRepository
    runner: "CollabRunnerProtocol | None" = None

    @classmethod
    def for_workspace(
        cls,
        workspace_id: str,
        database: WritingDatabase,
        runner: "CollabRunnerProtocol | None" = None,
    ) -> "WritingCollabServices":
        return cls(
            workspace_id=workspace_id,
            database=database,
            article_repository=ArticleRepository(database),
            collab_repository=CollabRepository(database),
            runner=runner,
        )


@runtime_checkable
class CollabRunnerProtocol(Protocol):
    """执行栈契约 = WU-08 冻结接口（``nanobot/writing/collab/runner.py``）。

    传输层**不 import** runner（runner 未落地时 webui 仍要能导入），只按下面的
    形状鸭子调用；参数名与冻结接口逐字一致，形状不符会在真装配时立刻炸，而不是
    静默错位：

    - ``start(request)``：领域输入是 ``StartCollabRequest``，返回 ``BatchHandle``
      （**建库与幂等都在 runner 内**，传输层不再自己写 collaboration/batch）；
    - ``reply`` / ``confirm`` / ``cancel`` / ``retry``：都是协程，必须在事件循环上跑
      （要叫停/叫醒在场句柄，包 ``to_thread`` 就永远叫不醒，契约 §3 / §10）。

    参数类型写成 ``Any``：这些类型属于领域层，传输层不做结构化依赖（见模块 docstring）。
    """

    async def start(self, request: Any) -> Any: ...

    async def reply(
        self, *, collaboration_id: str, feedback: str, expect_version: int,
    ) -> Any: ...

    async def confirm(
        self,
        *,
        collaboration_id: str,
        handoff_id: str,
        expected_handoff_version: int,
        outline: Any,
        article_revision: int,
        article_digest: str,
    ) -> Any: ...

    async def cancel(self, *, collaboration_id: str, active_batch_id: str) -> Any: ...

    async def retry(
        self, *, collaboration_id: str, batch_id: str, expect_version: int,
    ) -> Any: ...


# ---- 错误翻译（契约 §4，唯一翻译点）-----------------------------------------


def translate_error(error: BaseException) -> tuple[int, str]:
    """把领域异常翻成契约 §4 的 ``(status, message)``。"""
    if isinstance(error, OffsetError):
        return 400, f"suggestion target is invalid: {error.code}"
    if isinstance(error, ArticleLimitError):
        return error.status, error.message
    for kind in (
        CollaborationNotFound,
        ArtifactNotFound,
        HandoffNotFound,
        BatchNotFound,
    ):
        if isinstance(error, kind):
            return 404, _collaboration_message(error)
    for kind in (
        ActiveBatchConflict,
        CollabVersionConflict,
        NoActiveBatch,
        RequestReplayConflict,
        ArticleConflict,
    ):
        if isinstance(error, kind):
            return 409, _collaboration_message(error)
    if isinstance(error, ArticleNotFound):
        return 404, "article not found"
    if isinstance(error, InvalidFieldError):
        return 400, str(error)
    raise error


@contextmanager
def _translated() -> Generator[None, None, None]:
    try:
        yield
    except (
        OffsetError,
        ArticleLimitError,
        CollaborationNotFound,
        ArtifactNotFound,
        HandoffNotFound,
        BatchNotFound,
        ActiveBatchConflict,
        CollabVersionConflict,
        NoActiveBatch,
        RequestReplayConflict,
        ArticleConflict,
        ArticleNotFound,
        InvalidFieldError,
    ) as exc:
        status, message = translate_error(exc)
        raise WebUISettingsError(message, status=status) from exc


def _collaboration_message(error: BaseException) -> str:
    """按契约 §4 归一：协作/产物/交接不存在时回固定文案，不带内部 id。"""
    if isinstance(error, CollaborationNotFound):
        return "collaboration not found"
    if isinstance(error, ArtifactNotFound):
        return "artifact not found"
    if isinstance(error, HandoffNotFound):
        return "handoff not found"
    return str(error)


# ---- 输入校验 ---------------------------------------------------------------


def _require_text(raw: Any, field: str, *, max_chars: int = 128) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise WebUISettingsError(f"{field} is required")
    value = raw.strip()
    if len(value) > max_chars:
        raise WebUISettingsError(f"{field} is required")
    return value


def _require_id(raw: Any, field: str) -> str:
    return _require_text(raw, field)


def _require_request_id(raw: Any) -> str:
    if not isinstance(raw, str) or not _CLIENT_REQUEST_ID_RE.fullmatch(raw):
        raise WebUISettingsError("client_request_id is required")
    return raw


def _require_int(raw: Any, field: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise WebUISettingsError(f"{field} must be an integer")
    return raw


def _require_revision(raw: Any, field: str = "base_revision") -> int:
    if raw is None or raw == "":
        # 契约 §4：缺字段是「<field> is required」，不是「必须是整数」。
        raise WebUISettingsError(f"{field} is required")
    value = _require_int(raw, field)
    if value < 1:
        raise WebUISettingsError(f"{field} is required")
    return value


def _require_chat_id(raw: Any) -> str:
    if raw is None or raw == "":
        raise WebUISettingsError("chat_id is required")
    if not isinstance(raw, str) or _CHAT_ID_RE.fullmatch(raw) is None:
        raise WebUISettingsError(f"unsupported collaboration field: chat_id={raw!r}")
    return raw


def _optional_text(raw: Any, field: str) -> str:
    if raw is None or raw == "":
        return ""
    if not isinstance(raw, str):
        raise WebUISettingsError(f"unsupported collaboration field: {field}")
    return raw


def _normalize_action(raw: Any) -> str:
    value = _require_id(raw, "action")
    if value not in ACTIONS:
        raise WebUISettingsError(f"unsupported collaboration field: action={value!r}")
    return value


def _selection(raw: Any) -> dict[str, Any] | None:
    if raw is None or raw == "":
        return None
    if not isinstance(raw, Mapping):
        raise WebUISettingsError("unsupported collaboration field: selection")
    data: Mapping[str, Any] = cast(Mapping[str, Any], raw)
    start = data.get("start")
    end = data.get("end")
    if isinstance(start, bool) or isinstance(end, bool):
        raise WebUISettingsError("unsupported collaboration field: selection")
    if not isinstance(start, int) or not isinstance(end, int):
        raise WebUISettingsError("unsupported collaboration field: selection")
    text = data.get("text", "")
    if text is None:
        text = ""
    if not isinstance(text, str):
        raise WebUISettingsError("unsupported collaboration field: selection")
    return {"kind": "replace_range", "start": start, "end": end, "expected_before": text}


def _material_ids(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise WebUISettingsError("unsupported collaboration field: material_ids")
    entries: Sequence[Any] = cast(Sequence[Any], raw)
    return [_require_id(item, "material_ids") for item in entries]


def _goal(
    *,
    action: str,
    instruction: str,
    selection: Mapping[str, Any] | None,
    material_ids: Sequence[str],
    base_revision: int,
    snapshot_digest: str,
) -> dict[str, Any]:
    """服务端组装 ``goal``：只放业务字段，任何归属字段都进不来（契约 §3）。"""
    goal: dict[str, Any] = {
        "action": action,
        "base_revision": base_revision,
        "snapshot_digest": snapshot_digest,
    }
    if instruction:
        goal["instruction"] = instruction
    if selection is not None:
        goal["target"] = dict(selection)
    if material_ids:
        goal["material_ids"] = list(material_ids)
    return goal


def _runner(services: WritingCollabServices) -> CollabRunnerProtocol:
    if services.runner is None:
        raise WebUISettingsError(
            "writing collaboration service is not configured", status=503,
        )
    return services.runner


# ---- 读动作（认证 HTTP GET）-------------------------------------------------


def fetch_collaboration(
    services: WritingCollabServices, *, article_id: Any,
) -> dict[str, Any]:
    """``writing.collab.get``：快照投影（产物列表**不含**全文，契约 §3）。"""
    checked = _require_id(article_id, "article_id")
    with _translated():
        return services.collab_repository.snapshot(checked).to_dict()


def fetch_artifact(services: WritingCollabServices, *, artifact_id: Any) -> dict[str, Any]:
    """``writing.artifact.get``：详情投影，含产物全文。"""
    checked = _require_id(artifact_id, "artifact_id")
    with _translated():
        return services.collab_repository.get_artifact(checked).to_dict()


# ---- 写动作（仅认证 WS mutation，契约 §3）-----------------------------------


async def start_collaboration(
    services: WritingCollabServices,
    *,
    client_request_id: Any = None,
    article_id: Any = None,
    chat_id: Any = None,
    action: Any = None,
    base_revision: Any = None,
    snapshot_digest: Any = None,
    instruction: Any = "",
    selection: Any = None,
    material_ids: Any = None,
) -> dict[str, Any]:
    """``writing.start``：组装领域输入 → 交给执行栈（契约 §3）。

    传输层只做 payload 校验与 ``StartCollabRequest`` 组装：**建协作、建批次、幂等
    与唤起团队都在 runner 内**（WU-08 冻结接口），所以这里不做任何仓储写。
    """
    request = _start_request(
        client_request_id=client_request_id,
        article_id=article_id,
        chat_id=chat_id,
        action=action,
        base_revision=base_revision,
        snapshot_digest=snapshot_digest,
        instruction=instruction,
        selection=selection,
        material_ids=material_ids,
    )
    runner = _runner(services)
    with _translated():
        handle = await runner.start(request)
        return _handle_payload(handle)


def _start_request(
    *,
    client_request_id: Any,
    article_id: Any,
    chat_id: Any,
    action: Any,
    base_revision: Any,
    snapshot_digest: Any,
    instruction: Any = "",
    selection: Any = None,
    material_ids: Any = None,
) -> dict[str, Any]:
    """校验并组装 ``StartCollabRequest`` 的字段（形状与字段名逐字对齐冻结接口）。

    返回普通 dict（不是领域 dataclass）：传输层不 import 领域类型，runner 侧按关键字
    参数展开即可；字段名一旦漂移，真装配时会立刻 ``TypeError``。
    """
    resolved_action = _normalize_action(action)
    resolved_selection = _selection(selection)
    if resolved_selection is None and resolved_action in SELECTION_ACTIONS:
        raise WebUISettingsError("collaboration target selection is required")
    if resolved_selection is not None and resolved_action not in SELECTION_ACTIONS:
        raise WebUISettingsError(
            "collaboration target selection is not allowed for this action",
        )
    return {
        "client_request_id": _require_request_id(client_request_id),
        "article_id": _require_id(article_id, "article_id"),
        "chat_id": _require_chat_id(chat_id),
        "action": resolved_action,
        "base_revision": _require_revision(base_revision),
        "snapshot_digest": _require_id(snapshot_digest, "snapshot_digest"),
        "instruction": _optional_text(instruction, "instruction"),
        "selection": resolved_selection,
        "material_ids": tuple(_material_ids(material_ids)),
    }


def _handle_payload(handle: Any) -> dict[str, Any]:
    """``BatchHandle.to_dict()``（或等价映射）→ 契约 §3 的 ``writing.start`` 响应。"""
    payload = _as_payload(handle)
    return {
        "collaboration_id": str(payload.get("collaboration_id", "")),
        "batch_id": str(payload.get("batch_id", "")),
        "status": str(payload.get("status", "")),
        "stage": str(payload.get("stage", "")),
        "session_key": str(payload.get("session_key", "")),
        "active_batch_id": payload.get("active_batch_id"),
    }


def _as_payload(value: Any) -> dict[str, Any]:
    """领域结果对象 → 普通 dict（``to_dict`` 优先，其次 ``asdict``，最后原样）。"""
    if isinstance(value, Mapping):
        return dict(cast(Mapping[str, Any], value))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        data = to_dict()
        if isinstance(data, Mapping):
            return dict(cast(Mapping[str, Any], data))
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value) and not isinstance(value, type):
        return dict(asdict(value))
    raise WebUISettingsError(
        "writing collaboration service returned an unsupported response", status=500,
    )


async def reply_collaboration(
    services: WritingCollabServices,
    *,
    client_request_id: Any,
    collaboration_id: Any,
    feedback: Any,
    expect_version: Any,
) -> dict[str, Any]:
    """``writing.reply``：把作者反馈交给执行栈（冻结接口 ``runner.reply``）。

    协作版本 CAS（``expect_version``）在 runner 内结算；重放会因版本已前进而 409，
    不会静默开第二个批次。
    """
    _require_request_id(client_request_id)
    checked_id = _require_id(collaboration_id, "collaboration_id")
    message = _require_text(feedback, "feedback", max_chars=8192)
    expected = _require_revision(expect_version, "expect_version")
    runner = _runner(services)
    with _translated():
        batch = await runner.reply(
            collaboration_id=checked_id, feedback=message, expect_version=expected,
        )
        payload = _as_payload(batch)
        return {
            "collaboration_id": checked_id,
            "batch_id": str(payload.get("id", "")),
            "status": str(payload.get("status", "")),
        }


def confirm_handoff(
    services: WritingCollabServices,
    *,
    client_request_id: Any,
    collaboration_id: Any,
    handoff_id: Any,
    expected_handoff_version: Any,
    article_revision: Any,
    article_digest: Any,
    outline: Any = None,
) -> dict[str, Any]:
    """``writing.confirm``：交接 CAS（整数 ``artifact_version``，裁决 §10.3）。

    两处绑定校验缺一不可：
    1. 交接必须属于 ``collaboration_id``（否则可以拿别人的 handoff 确认自己的协作）；
    2. 交接必须属于该协作当前的文章（跨文章同样拒绝）。
    不一致按契约 §4 的「协作不存在」语义回 404。
    """
    _require_request_id(client_request_id)
    checked_id = _require_id(collaboration_id, "collaboration_id")
    checked_handoff = _require_id(handoff_id, "handoff_id")
    expected = _require_int(expected_handoff_version, "expected_handoff_version")
    revision = _require_revision(article_revision, "article_revision")
    digest = _require_id(article_digest, "article_digest")
    if outline is not None and not isinstance(outline, Sequence):
        raise WebUISettingsError("unsupported collaboration field: outline")
    with _translated():
        collaboration = services.collab_repository.get_collaboration(checked_id)
        handoff = services.collab_repository.get_handoff(checked_handoff)
        if handoff.collaboration_id != collaboration.id:
            # 交叉确认（A 的 handoff + B 的协作 id）必须与「不存在」同语义。
            raise HandoffNotFound(checked_handoff)
        resolved = services.collab_repository.resolve_handoff(
            handoff_id=checked_handoff,
            expected_version=expected,
            status="confirmed",
        )
        return {
            "collaboration_id": collaboration.id,
            "batch_id": resolved.batch_id,
            "article_revision": revision,
            "article_digest": digest,
            "handoff_status": resolved.status,
        }


async def cancel_collaboration(
    services: WritingCollabServices,
    *,
    client_request_id: Any,
    collaboration_id: Any,
    active_batch_id: Any,
) -> dict[str, Any]:
    """``writing.cancel``：在事件循环上叫停执行栈（冻结接口 ``runner.cancel``）。

    「无活动批次」由 runner 判定（它同时核对批次归属、协作的 active 指针与代数），
    这里不重复一套判据——重复实现就是第二个真相源。
    """
    _require_request_id(client_request_id)
    checked_id = _require_id(collaboration_id, "collaboration_id")
    checked_batch = _require_id(active_batch_id, "active_batch_id")
    runner = _runner(services)
    with _translated():
        collaboration = await runner.cancel(
            collaboration_id=checked_id, active_batch_id=checked_batch,
        )
        payload = _as_payload(collaboration)
        return {
            "collaboration_id": str(payload.get("id", checked_id)),
            "status": str(payload.get("status", "cancelled")),
            "cancel_generation": int(payload.get("cancel_generation", 0) or 0),
        }


async def retry_collaboration(
    services: WritingCollabServices,
    *,
    client_request_id: Any,
    collaboration_id: Any,
    batch_id: Any,
    expect_version: Any,
) -> dict[str, Any]:
    """``writing.retry``：在事件循环上重开一次尝试（冻结接口 ``runner.retry``）。"""
    _require_request_id(client_request_id)
    checked_id = _require_id(collaboration_id, "collaboration_id")
    checked_batch = _require_id(batch_id, "batch_id")
    expected = _require_revision(expect_version, "expect_version")
    runner = _runner(services)
    with _translated():
        batch = await runner.retry(
            collaboration_id=checked_id, batch_id=checked_batch, expect_version=expected,
        )
        payload = _as_payload(batch)
        return {
            "collaboration_id": str(payload.get("collaboration_id", checked_id)),
            "batch_id": str(payload.get("id", "")),
            "attempt": int(payload.get("attempt", 0) or 0),
            "status": str(payload.get("status", "")),
        }


def apply_suggestion(
    services: WritingCollabServices,
    *,
    client_request_id: Any,
    suggestion_id: Any,
    article_id: Any,
    base_revision: Any,
) -> dict[str, Any]:
    """``writing.apply``：采纳单条建议（同事务写建议状态与撤销条目）。"""
    request_id = _require_request_id(client_request_id)
    checked_suggestion = _require_id(suggestion_id, "suggestion_id")
    checked_article = _require_id(article_id, "article_id")
    revision = _require_revision(base_revision)
    with _translated():
        return _apply_suggestion(
            services.database,
            checked_suggestion,
            article_id=checked_article,
            base_revision=revision,
            client_request_id=request_id,
        )


def apply_batch_suggestions(
    services: WritingCollabServices,
    *,
    client_request_id: Any,
    suggestion_ids: Any,
    article_id: Any,
    base_revision: Any,
) -> dict[str, Any]:
    """批量采纳（内部入口）：重叠补丁 → 409 ``suggestion targets overlap``。"""
    request_id = _require_request_id(client_request_id)
    ids = _material_ids(suggestion_ids)
    if not ids:
        raise WebUISettingsError("suggestion_id is required")
    checked_article = _require_id(article_id, "article_id")
    revision = _require_revision(base_revision)
    with _translated():
        return apply_batch(
            services.database,
            ids,
            article_id=checked_article,
            base_revision=revision,
            client_request_id=request_id,
        )


def undo(
    services: WritingCollabServices,
    *,
    client_request_id: Any,
    undo_id: Any,
    base_revision: Any,
) -> dict[str, Any]:
    """``writing.undo``：撤销一次采纳（版本号只前进，不回落）。"""
    request_id = _require_request_id(client_request_id)
    checked_undo = _require_id(undo_id, "undo_id")
    revision = _require_revision(base_revision)
    with _translated():
        return _undo(
            services.database,
            checked_undo,
            base_revision=revision,
            client_request_id=request_id,
        )


__all__ = [
    "PROTECTED_PAYLOAD_FIELDS",
    "SELECTION_ACTIONS",
    "CollabRunnerProtocol",
    "WritingCollabServices",
    "apply_batch_suggestions",
    "apply_suggestion",
    "cancel_collaboration",
    "confirm_handoff",
    "fetch_artifact",
    "fetch_collaboration",
    "reply_collaboration",
    "retry_collaboration",
    "start_collaboration",
    "translate_error",
    "undo",
]
