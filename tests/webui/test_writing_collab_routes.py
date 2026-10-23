"""协作 handler（``writing_collab_routes``）单测。

口径来自契约 ``.ai-runtime-artifacts/contracts/2026-10-17-contract-writing-team-collab.md``
§3 / §4：七个写动作全部点号命名；控制动作（``writing.start`` / ``writing.cancel`` /
``writing.retry``）必须在事件循环上执行，sync 入口必须响亮地回 500 而不是偷偷
``to_thread``；``client_request_id`` 与其余必填字段在本层做协议级校验。

runner 是注入的假件：本层不认识 ``nanobot.writing.collab.runner``，只认 Protocol。
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from nanobot.webui import writing_api
from nanobot.webui.gateway_services import build_writing_collab_operations
from nanobot.webui.settings_contracts import SettingsRequest, WebUISettingsError
from nanobot.webui.writing_collab_api import (
    PROTECTED_PAYLOAD_FIELDS,
    WritingCollabServices,
    apply_suggestion,
    cancel_collaboration,
    confirm_handoff,
    fetch_artifact,
    fetch_collaboration,
    reply_collaboration,
    retry_collaboration,
    start_collaboration,
    undo,
)
from nanobot.webui.writing_collab_routes import (
    WRITING_COLLAB_ACTION_NAMES,
    WRITING_COLLAB_CONTROL_ACTION_NAMES,
    WRITING_COLLAB_MUTATION_ACTION_NAMES,
    WRITING_COLLAB_READ_ACTION_NAMES,
    WritingCollabSettingsHandler,
    WritingCollabSettingsOperations,
    sanitized_extras,
)
from nanobot.webui.writing_services import WritingServices
from nanobot.writing.collab.models import ACTIVE_BATCH_STATUSES, Batch
from nanobot.writing.collab.repository import (
    ActiveBatchConflict,
    CollabRepository,
    CollabVersionConflict,
    NoActiveBatch,
)
from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import new_id, utc_now_iso

CHAT_ID = "chat-collab-1"
SESSION_KEY = "websocket:chat-collab-1"
ARTICLE_DIGEST = "digest-1"
BASE_PAYLOAD = {
    "client_request_id": "r-1",
    "article_id": "article-1",
    "chat_id": CHAT_ID,
    "action": "start_full",
    "base_revision": 1,
    "snapshot_digest": "digest-1",
}


class FakeRunner:
    """协作执行栈替身：按**冻结接口**（``runner.py`` 的方法名/参数名）真落库。

    裁决 1 之后，建协作与建批次是执行栈的职责（传输层不再自建），所以替身也必须走
    ``CollabRepository`` —— 路由层要断言的正是「协作/批次真的存在」。
    """

    def __init__(self, db: WritingDatabase) -> None:
        self.repository = CollabRepository(db)
        self.started: list[dict[str, Any]] = []
        self.replied: list[dict[str, Any]] = []
        self.cancelled: list[dict[str, Any]] = []
        self.retried: list[dict[str, Any]] = []

    async def start(self, request: Any) -> dict[str, Any]:
        fields = _fields(request)
        self.started.append(fields)
        collaboration = self.repository.create_collaboration(
            article_id=fields["article_id"],
            chat_id=fields["chat_id"],
            session_key=f"websocket:{fields['chat_id']}",
            goal={"action": fields["action"]},
            request_id=f"{fields['client_request_id']}:collaboration",
        )
        batch = self.repository.create_batch(
            collaboration_id=collaboration.id,
            action=fields["action"],
            base_revision=fields["base_revision"],
            snapshot_digest=fields["snapshot_digest"],
            instruction=fields["instruction"],
            request_id=f"{fields['client_request_id']}:batch",
        )
        current = self.repository.get_collaboration(collaboration.id)
        return {
            "collaboration_id": current.id,
            "batch_id": batch.id,
            "status": batch.status,
            "stage": current.stage,
            "session_key": current.session_key,
            "active_batch_id": current.active_batch_id,
        }

    async def reply(
        self, *, collaboration_id: str, feedback: str, expect_version: int,
    ) -> Batch:
        self.replied.append({
            "collaboration_id": collaboration_id,
            "feedback": feedback,
            "expect_version": expect_version,
        })
        collaboration = self.repository.get_collaboration(collaboration_id)
        if expect_version != collaboration.version:
            raise CollabVersionConflict(
                expected=expect_version, actual=collaboration.version,
            )
        _close_waiting_author(self.repository, collaboration_id)
        return self.repository.create_batch(
            collaboration_id=collaboration_id,
            action="reply",
            base_revision=collaboration.version,
            snapshot_digest=collaboration.id,
            instruction=feedback,
            request_id=f"{collaboration_id}:reply:{expect_version}",
        )

    async def cancel(self, *, collaboration_id: str, active_batch_id: str) -> Any:
        self.cancelled.append({
            "collaboration_id": collaboration_id, "active_batch_id": active_batch_id,
        })
        collaboration = self.repository.get_collaboration(collaboration_id)
        batch = self.repository.get_batch(active_batch_id)
        if batch.collaboration_id != collaboration_id:
            raise NoActiveBatch(collaboration_id)
        if (
            collaboration.active_batch_id != active_batch_id
            or batch.status not in ACTIVE_BATCH_STATUSES
        ):
            raise NoActiveBatch(collaboration_id)
        self.repository.finish_batch(
            batch_id=active_batch_id,
            status="cancelled",
            cancel_generation=collaboration.cancel_generation + 1,
        )
        return self.repository.get_collaboration(collaboration_id)

    async def retry(
        self, *, collaboration_id: str, batch_id: str, expect_version: int,
    ) -> Batch:
        self.retried.append({
            "collaboration_id": collaboration_id,
            "batch_id": batch_id,
            "expect_version": expect_version,
        })
        batch = self.repository.get_batch(batch_id)
        if batch.collaboration_id != collaboration_id:
            raise ActiveBatchConflict(collaboration_id)
        if batch.status in ACTIVE_BATCH_STATUSES:
            raise ActiveBatchConflict(collaboration_id)
        return self.repository.create_batch(
            collaboration_id=collaboration_id,
            action=batch.action,
            base_revision=batch.base_revision,
            snapshot_digest=batch.snapshot_digest,
            instruction=batch.instruction,
            expect_version=expect_version,
            request_id=f"{batch_id}:retry:{expect_version}",
        )


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


def _close_waiting_author(repository: CollabRepository, collaboration_id: str) -> None:
    """作者回复会让等待中的批次让位（真 runner 的 ``_close_waiting_author_batches``）。"""
    for existing in repository.list_batches(collaboration_id):
        if existing.status == "waiting_author":
            repository.finish_batch(batch_id=existing.id, status="completed")


@pytest.fixture
def db(tmp_path: Path) -> WritingDatabase:
    database = WritingDatabase(tmp_path)
    database.ensure_schema()
    return database


@pytest.fixture
def services(db: WritingDatabase) -> WritingCollabServices:
    return WritingCollabServices.for_workspace("default", db, FakeRunner(db))


@pytest.fixture
def operations(services: WritingCollabServices) -> WritingCollabSettingsOperations:
    return WritingCollabSettingsOperations(
        get_collaboration=lambda **kwargs: fetch_collaboration(services, **kwargs),
        get_artifact=lambda **kwargs: fetch_artifact(services, **kwargs),
        start=lambda **kwargs: start_collaboration(services, **kwargs),
        reply=lambda **kwargs: reply_collaboration(services, **kwargs),
        confirm=lambda **kwargs: confirm_handoff(services, **kwargs),
        cancel=lambda **kwargs: cancel_collaboration(services, **kwargs),
        retry=lambda **kwargs: retry_collaboration(services, **kwargs),
        apply=lambda **kwargs: apply_suggestion(services, **kwargs),
        undo=lambda **kwargs: undo(services, **kwargs),
    )


@pytest.fixture
def handler(operations: WritingCollabSettingsOperations) -> WritingCollabSettingsHandler:
    return WritingCollabSettingsHandler(operations)


def _request(payload: dict[str, Any] | None = None, **query: str) -> SettingsRequest:
    return SettingsRequest(
        query={key: [value] for key, value in query.items()},
        payload=payload,
    )


def _article(services: WritingCollabServices) -> dict[str, Any]:
    payload = writing_api.create_article(
        WritingServices(workspace_id="default", repository=services.article_repository),
        client_request_id="r-article-1",
        title="标题",
        body="第一段\n第二段",
    )
    return dict(payload["article"])


def _start_payload(article: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    return dict(
        BASE_PAYLOAD,
        article_id=article["id"],
        base_revision=article["revision"],
        snapshot_digest=ARTICLE_DIGEST,
        **overrides,
    )


async def _started(
    handler: WritingCollabSettingsHandler, article: dict[str, Any], **overrides: Any,
) -> dict[str, Any]:
    result = await handler.handle_control(
        "writing.start", _request(_start_payload(article, **overrides)),
    )
    assert result.status == 200, result.error
    return dict(result.payload or {})


# ---- 动作名单 ---------------------------------------------------------------


def test_action_names_are_dotted_and_exactly_nine() -> None:
    assert WRITING_COLLAB_ACTION_NAMES == frozenset({
        "writing.collab.get", "writing.artifact.get",
        "writing.start", "writing.reply", "writing.confirm",
        "writing.cancel", "writing.retry", "writing.apply", "writing.undo",
    })
    assert all("-" not in action for action in WRITING_COLLAB_ACTION_NAMES)


def test_control_actions_are_the_event_loop_ones() -> None:
    """控制动作＝必须在 loop 上跑的四个：start / reply / cancel / retry。"""
    assert WRITING_COLLAB_CONTROL_ACTION_NAMES == frozenset({
        "writing.start", "writing.reply", "writing.cancel", "writing.retry",
    })
    assert WRITING_COLLAB_READ_ACTION_NAMES == frozenset({
        "writing.collab.get", "writing.artifact.get",
    })
    assert WRITING_COLLAB_MUTATION_ACTION_NAMES == (
        WRITING_COLLAB_ACTION_NAMES - WRITING_COLLAB_READ_ACTION_NAMES
    )


def _is_coroutine(callable_object: Any) -> bool:
    """``partial``/``lambda`` 双重包装下也能看出底层是不是协程函数。"""
    target: Any = callable_object
    while isinstance(target, functools.partial):
        target = target.func
    if inspect.iscoroutinefunction(target):
        return True
    # 测试替身惯用 ``lambda **kw: start_collaboration(...)``：lambda 本身不是协程
    # 函数，但它返回一个协程对象。断言真实调用结果才是可靠判据。
    try:
        probe = target()
    except TypeError:
        return False
    if inspect.iscoroutine(probe):
        probe.close()
        return True
    return False


def test_control_operations_are_coroutines_others_are_sync() -> None:
    """契约 §3：控制动作走协程（loop），其余是阻塞 SQLite 走 ``to_thread``。

    直接对着生产装配 ``build_writing_collab_operations`` 断言，而不是对着测试
    替身——替身换成 lambda 就会把这层口径测没了。
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        operations = build_writing_collab_operations(
            workspace_id="default", workspace_path=Path(tmp),
        )
        for name in ("start", "cancel", "retry"):
            assert inspect.iscoroutinefunction(getattr(operations, name)), name
        for name in ("reply",):
            assert inspect.iscoroutinefunction(getattr(operations, name)), name
        for name in (
            "get_collaboration", "get_artifact", "confirm", "apply", "undo",
        ):
            assert not inspect.iscoroutinefunction(getattr(operations, name)), name


def test_routes_delegate_to_the_payload_layer() -> None:
    """九个入口都必须落在 ``writing_collab_api`` 的同名函数上，且各调用一次。

    生产装配在 ``gateway_services.build_writing_collab_operations``：路由层不得自己
    拼装 payload，只能转发。
    """
    source = inspect.getsource(build_writing_collab_operations)
    payload = inspect.getsource(
        __import__("nanobot.webui.writing_collab_api", fromlist=["x"]),
    )
    for name in (
        "start_collaboration", "reply_collaboration", "confirm_handoff",
        "cancel_collaboration", "retry_collaboration", "apply_suggestion", "undo",
        "fetch_collaboration", "fetch_artifact",
    ):
        assert f"writing_collab_api.{name}" in source, name
        assert f"def {name}(" in payload, name
    assert source.count("partial(writing_collab_api.") == 9


# ---- 控制动作的执行栈 -------------------------------------------------------


def test_control_action_on_sync_entry_is_500(handler: WritingCollabSettingsHandler) -> None:
    """契约 §4：控制动作走 sync 入口 → 500 ``<action> must run on the event loop``。"""
    payload = dict(
        BASE_PAYLOAD, collaboration_id="c-1", batch_id="b-1", expect_version=1,
    )
    result = handler.handle("writing.cancel", _request({
        "client_request_id": "r-1", "collaboration_id": "c-1", "active_batch_id": "b-1",
    }))
    assert result.status == 500
    assert result.error == "writing.cancel must run on the event loop"
    assert result.payload is None

    for action in ("writing.start", "writing.retry"):
        sync_result = handler.handle(action, _request(payload))
        assert sync_result.status == 500, action
        assert sync_result.error == f"{action} must run on the event loop"


def test_control_action_on_sync_entry_still_validates_payload(
    handler: WritingCollabSettingsHandler,
) -> None:
    """缺 client_request_id 是 400 的调用方错误，先于「路由接错执行栈」的 500。"""
    result = handler.handle("writing.cancel", _request({
        "collaboration_id": "c-1", "active_batch_id": "b-1",
    }))
    assert (result.status, result.error) == (400, "client_request_id is required")


def test_unknown_action_is_404(handler: WritingCollabSettingsHandler) -> None:
    result = handler.handle("writing.frobnicate", _request({}))
    assert (result.status, result.error) == (
        404, "unknown writing collaboration action: writing.frobnicate",
    )


@pytest.mark.asyncio
async def test_handle_control_rejects_non_control_action(
    handler: WritingCollabSettingsHandler,
) -> None:
    result = await handler.handle_control("writing.apply", _request({}))
    assert (result.status, result.error) == (
        404, "unknown writing collaboration control action: writing.apply",
    )


# ---- 协议级必填 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "payload", "expected"),
    [
        pytest.param(
            "writing.reply", {"collaboration_id": "c-1", "feedback": "喂", "expect_version": 1},
            "client_request_id is required", id="reply-no-request-id",
        ),
        pytest.param(
            "writing.collab.get", {}, "article_id is required", id="get-no-article",
        ),
        pytest.param(
            "writing.artifact.get", {}, "artifact_id is required", id="artifact-no-id",
        ),
        pytest.param(
            "writing.reply",
            {"client_request_id": "r-1", "feedback": "喂", "expect_version": 1},
            "collaboration_id is required", id="reply-no-collaboration",
        ),
        pytest.param(
            "writing.confirm",
            {"client_request_id": "r-1", "handoff_id": "h-1",
             "expected_handoff_version": 1, "article_revision": 1, "article_digest": "d"},
            "collaboration_id is required", id="confirm-no-collaboration",
        ),
        pytest.param(
            "writing.cancel",
            {"client_request_id": "r-1", "collaboration_id": "c-1"},
            "active_batch_id is required", id="cancel-no-batch",
        ),
        pytest.param(
            "writing.retry",
            {"client_request_id": "r-1", "collaboration_id": "c-1", "batch_id": "b-1"},
            "expect_version is required", id="retry-no-version",
        ),
        pytest.param(
            "writing.apply",
            {"client_request_id": "r-1", "suggestion_id": "s-1", "article_id": "a-1"},
            "base_revision is required", id="apply-no-revision",
        ),
        pytest.param(
            "writing.undo",
            {"client_request_id": "r-1", "undo_id": "u-1"},
            "base_revision is required", id="undo-no-revision",
        ),
    ],
)
def test_required_fields_are_400(
    handler: WritingCollabSettingsHandler,
    action: str,
    payload: dict[str, Any],
    expected: str,
) -> None:
    result = handler.handle(action, _request(payload))
    assert (result.status, result.error) == (400, expected)


def test_confirm_requires_integer_handoff_version(
    handler: WritingCollabSettingsHandler,
) -> None:
    result = handler.handle("writing.confirm", _request({
        "client_request_id": "r-1", "collaboration_id": "c-1", "handoff_id": "h-1",
        "expected_handoff_version": "three", "article_revision": 1, "article_digest": "d",
    }))
    assert (result.status, result.error) == (
        400, "expected_handoff_version must be an integer",
    )


def test_confirm_requires_handoff_id_and_article_revision(
    handler: WritingCollabSettingsHandler,
) -> None:
    result = handler.handle("writing.confirm", _request({
        "client_request_id": "r-1", "collaboration_id": "c-1",
        "expected_handoff_version": 1, "article_revision": 1, "article_digest": "d",
    }))
    assert (result.status, result.error) == (400, "handoff_id is required")

    result = handler.handle("writing.confirm", _request({
        "client_request_id": "r-1", "collaboration_id": "c-1", "handoff_id": "h-1",
        "expected_handoff_version": 1, "article_digest": "d",
    }))
    assert (result.status, result.error) == (400, "article_revision is required")


def test_start_rejects_client_supplied_team_or_agent(
    handler: WritingCollabSettingsHandler, services: WritingCollabServices,
) -> None:
    """契约 §3：payload 里的 teamId/agentId/skillPath 既不报错也不影响固定团队。"""
    article = _article(services)
    payload = _start_payload(
        article, teamId="evil-team", agentId="evil-agent", skillPath="/tmp/evil.md",
    )
    assert sanitized_extras(payload) == {}

    result = asyncio.run(handler.handle_control("writing.start", _request(payload)))
    assert result.status == 200, result.error
    collaboration = services.collab_repository.get_by_article(article["id"])
    assert collaboration is not None
    assert collaboration.chat_id == CHAT_ID
    assert set(PROTECTED_PAYLOAD_FIELDS) == {
        "teamId", "team_id", "agentId", "agent_id", "skillPath", "skill_path",
    }


def test_sanitized_extras_keeps_only_business_fields() -> None:
    assert sanitized_extras({
        "teamId": "t", "agent_id": "a", "skill_path": "s",
        "instruction": "写细一点", "material_ids": ["m-1"],
        "selection": {"start": 0, "end": 2},
        "unknown_field": "x",
    }) == {
        "instruction": "写细一点",
        "material_ids": ["m-1"],
        "selection": {"start": 0, "end": 2},
    }


def test_sanitized_extras_rejects_bad_shapes() -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        sanitized_extras({"material_ids": "m-1"})
    assert (excinfo.value.status, excinfo.value.message) == (
        400, "material_ids must be a list of strings",
    )
    with pytest.raises(WebUISettingsError) as excinfo:
        sanitized_extras({"selection": {"start": 0}})
    assert (excinfo.value.status, excinfo.value.message) == (
        400, "selection must be an object",
    )


def test_apply_requires_suggestion_id(handler: WritingCollabSettingsHandler) -> None:
    result = handler.handle("writing.apply", _request({
        "client_request_id": "r-1", "article_id": "a-1", "base_revision": 1,
    }))
    assert (result.status, result.error) == (400, "suggestion_id is required")


# ---- 七个写动作的正常路径 ---------------------------------------------------


@pytest.mark.asyncio
async def test_handler_dispatches_every_write_action(
    handler: WritingCollabSettingsHandler, services: WritingCollabServices,
) -> None:
    article = _article(services)
    started = await _started(handler, article)
    collaboration_id = started["collaboration_id"]
    batch_id = started["batch_id"]
    assert started["session_key"] == SESSION_KEY

    # 交接属于首轮：先挂到已收尾的首批上（waiting_author 仍算活动批次，reply 之前
    # 必须先让首轮成终态——契约 §3 的 reply 语义）。
    artifact = services.collab_repository.add_artifact(
        collaboration_id=collaboration_id, batch_id=batch_id, attempt=1, kind="outline",
        role_id="writing-architect", content={"items": ["一"]}, source_ids=[], gaps=[],
    )
    handoff = services.collab_repository.add_handoff(
        collaboration_id=collaboration_id, batch_id=batch_id, artifact_id=artifact.id,
        kind="outline_confirm", artifact_version=artifact.version,
    )
    services.collab_repository.finish_batch(batch_id=batch_id, status="failed")

    collaboration = services.collab_repository.get_collaboration(collaboration_id)
    replied = await handler.handle_control("writing.reply", _request({
        "client_request_id": "r-2", "collaboration_id": collaboration_id,
        "feedback": "再补一段", "expect_version": collaboration.version,
    }))
    assert replied.status == 200, replied.error
    replied_batch = replied.payload["batch_id"]  # type: ignore[index]

    confirmed = handler.handle("writing.confirm", _request({
        "client_request_id": "r-3", "collaboration_id": collaboration_id,
        "handoff_id": handoff.id, "expected_handoff_version": artifact.version,
        "article_revision": article["revision"], "article_digest": ARTICLE_DIGEST,
    }))
    assert confirmed.status == 200, confirmed.error
    assert confirmed.payload["handoff_status"] == "confirmed"  # type: ignore[index]

    cancelled = await handler.handle_control("writing.cancel", _request({
        "client_request_id": "r-4", "collaboration_id": collaboration_id,
        "active_batch_id": replied_batch,
    }))
    assert cancelled.status == 200, cancelled.error

    retried = await handler.handle_control("writing.retry", _request({
        "client_request_id": "r-5", "collaboration_id": collaboration_id,
        "batch_id": replied_batch,
        "expect_version": services.collab_repository.get_collaboration(
            collaboration_id,
        ).version,
    }))
    assert retried.status == 200, retried.error
    # attempt 由仓储按协作递增：start=1 → reply=2 → retry=3（重试是新 attempt 行）。
    assert retried.payload["attempt"] == 3  # type: ignore[index]

    suggestion_id = _suggestion(
        services, article_id=article["id"], collaboration_id=collaboration_id,
    )
    applied = handler.handle("writing.apply", _request({
        "client_request_id": "r-6", "suggestion_id": suggestion_id,
        "article_id": article["id"], "base_revision": article["revision"],
    }))
    assert applied.status == 200, applied.error

    undone = handler.handle("writing.undo", _request({
        "client_request_id": "r-7", "undo_id": applied.payload["undo_id"],  # type: ignore[index]
        "base_revision": applied.payload["revision"],  # type: ignore[index]
    }))
    assert undone.status == 200, undone.error
    assert undone.payload["suggestion_id"] == suggestion_id  # type: ignore[index]


@pytest.mark.asyncio
async def test_read_actions_prefer_query_then_payload(
    handler: WritingCollabSettingsHandler, services: WritingCollabServices,
) -> None:
    article = _article(services)
    in_query = handler.handle(
        "writing.collab.get", _request({}, article_id=article["id"]),
    )
    assert in_query.status == 200 and in_query.payload["collaboration"] is None  # type: ignore[index]

    in_payload = handler.handle(
        "writing.collab.get", _request({"article_id": article["id"]}),
    )
    assert in_payload.status == 200

    started = await _started(handler, article)
    artifact = services.collab_repository.add_artifact(
        collaboration_id=started["collaboration_id"], batch_id=started["batch_id"],
        attempt=1, kind="outline", role_id="writing-architect", content={"items": ["一"]},
        source_ids=[], gaps=[],
    )
    artifact_payload = handler.handle(
        "writing.artifact.get", _request({"artifact_id": artifact.id}),
    )
    assert artifact_payload.status == 200
    assert artifact_payload.payload["id"] == artifact.id  # type: ignore[index]
    assert artifact_payload.payload["content"] == {"items": ["一"]}  # type: ignore[index]

    snapshot = handler.handle(
        "writing.collab.get", _request({"article_id": article["id"]}),
    )
    assert snapshot.status == 200
    assert snapshot.payload["collaboration"]["id"] == started["collaboration_id"]  # type: ignore[index]


# ---- 未装配（null operations）-----------------------------------------------


@pytest.mark.asyncio
async def test_null_operations_are_503() -> None:
    from nanobot.webui.settings_routes import _null_writing_collab_operations

    handler = WritingCollabSettingsHandler(_null_writing_collab_operations())
    # 四个控制动作走 handle_control：未装配 → 503（契约 §4），而不是 500 或 AttributeError。
    for action, extra in (
        ("writing.start", {}),
        ("writing.reply", {"collaboration_id": "c-1", "feedback": "f", "expect_version": 1}),
        ("writing.cancel", {"collaboration_id": "c-1", "active_batch_id": "b-1"}),
        ("writing.retry", {"collaboration_id": "c-1", "batch_id": "b-1", "expect_version": 1}),
    ):
        payload = dict(_start_payload({"id": "a", "revision": 1}), **extra)
        result = await handler.handle_control(action, _request(payload))
        assert (result.status, result.error) == (
            503, "writing collaboration service is not configured",
        ), action

    # 其余动作走 sync 入口，同样是 503。
    for action, payload in (
        ("writing.apply", {
            "client_request_id": "r-1", "suggestion_id": "s-1",
            "article_id": "a-1", "base_revision": 1,
        }),
        ("writing.undo", {
            "client_request_id": "r-1", "undo_id": "u-1", "base_revision": 1,
        }),
        ("writing.collab.get", {"article_id": "a-1"}),
        ("writing.artifact.get", {"artifact_id": "art-1"}),
    ):
        result = handler.handle(action, _request(payload))
        assert (result.status, result.error) == (
            503, "writing collaboration service is not configured",
        ), action


def _suggestion(
    services: WritingCollabServices, *, article_id: str, collaboration_id: str,
) -> str:
    suggestion_id = new_id()
    with services.database.connect() as conn:
        conn.execute(
            "INSERT INTO writing_collab_suggestions (id, workspace_key, collaboration_id,"
            " artifact_id, article_id, base_revision, snapshot_digest, target_json, before_text,"
            " after_text, summary, notes, source_material_ids, status, applied_revision,"
            " created_at, applied_at)"
            " VALUES (?, ?, ?, '', ?, 1, 'digest-1', ?, '', '改写段', '', '', '[]',"
            " 'pending', NULL, ?, NULL)",
            (
                suggestion_id, services.database.workspace_key, collaboration_id, article_id,
                json.dumps(
                    {"kind": "replace_range", "start": 0, "end": 3, "expected_before": "第一段"},
                    ensure_ascii=False,
                ),
                utc_now_iso(),
            ),
        )
    return suggestion_id
