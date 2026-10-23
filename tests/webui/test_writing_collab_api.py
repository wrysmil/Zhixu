"""协作 payload 层（``writing_collab_api``）单测。

契约 ``.ai-runtime-artifacts/contracts/2026-10-17-contract-writing-team-collab.md``
§3 / §4 / §10 是唯一口径：本文件逐条断言七个写动作的返回形状与 §4 的 message
原文（含 ``suggestion target is invalid: <code>``、``suggestion targets overlap``、
``undo entry not found``），并证明**客户端提供的团队/角色/Skill 一律不被采信**。

runner 在这里是注入的假件：WU-08 的 ``nanobot.writing.collab.runner`` 尚未落地，
payload 层只认 Protocol，测试替身足以锁定调用口径。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from nanobot.webui import writing_api
from nanobot.webui.settings_contracts import WebUISettingsError
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
from nanobot.webui.writing_services import WritingServices
from nanobot.writing.collab.models import ACTIVE_BATCH_STATUSES, Batch
from nanobot.writing.collab.repository import (
    ActiveBatchConflict,
    CollabRepository,
    CollabVersionConflict,
    HandoffNotFound,
    NoActiveBatch,
)
from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import new_id, utc_now_iso

CHAT_ID = "chat-collab-1"
SESSION_KEY = "websocket:chat-collab-1"
CLIENT_REQUEST_ID = "r-1"
# 客户端上送的快照摘要是不透明串（payload 层只要求非空；CAS 由 base_revision 承担）。
ARTICLE_DIGEST = "digest-1"


class FakeRunner:
    """执行栈替身：按**冻结接口**（``runner.py`` 的方法名/参数名）真落库。

    真 runner 负责建协作与批次，所以替身也走 ``CollabRepository``——只有这样，
    ``confirm_handoff`` 之类的仓储校验与返回体才有真行可查。这里刻意不复刻 runner
    的调度/团队部分，只保留「写库 + 返回领域结果」这一层。
    """

    def __init__(self, db: WritingDatabase) -> None:
        self.repository = CollabRepository(db)
        self.started: list[dict[str, Any]] = []
        self.replied: list[dict[str, Any]] = []
        self.confirmed: list[dict[str, Any]] = []
        self.cancelled: list[dict[str, Any]] = []
        self.retried: list[dict[str, Any]] = []

    async def start(self, request: Any) -> Any:
        # 冻结接口的 ``StartCollabRequest``。传输层按关键字传给真 runner；替身用
        # ``_fields()`` 同时吃 dataclass 与等价映射，断言口径完全一致。
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
            request_id=fields["client_request_id"],
        )
        current = self.repository.get_collaboration(collaboration.id)
        return _Handle(
            collaboration_id=current.id,
            batch_id=batch.id,
            status=batch.status,
            stage=current.stage,
            session_key=current.session_key,
            active_batch_id=current.active_batch_id,
        )

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

    async def confirm(
        self,
        *,
        collaboration_id: str,
        handoff_id: str,
        expected_handoff_version: int,
        outline: Any,
        article_revision: int,
        article_digest: str,
    ) -> Any:
        self.confirmed.append({
            "collaboration_id": collaboration_id,
            "handoff_id": handoff_id,
            "expected_handoff_version": expected_handoff_version,
            "outline": outline,
            "article_revision": article_revision,
            "article_digest": article_digest,
        })
        self.repository.get_collaboration(collaboration_id)  # 不存在即抛，属守卫
        handoff = self.repository.get_handoff(handoff_id)
        if handoff.collaboration_id != collaboration_id:
            raise HandoffNotFound(handoff_id)
        if expected_handoff_version != handoff.artifact_version:
            raise CollabVersionConflict(
                expected=expected_handoff_version,
                actual=handoff.artifact_version,
                kind="handoff",
            )
        resolved = self.repository.resolve_handoff(
            handoff_id=handoff_id,
            expected_version=expected_handoff_version,
            status="confirmed",
        )
        return _Confirmed(
            collaboration_id=collaboration_id,
            batch_id=resolved.batch_id,
            article_revision=article_revision,
            article_digest=article_digest,
            handoff_status=resolved.status,
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
        # 与真 runner 一致：先把丢弃代数落到批次行与协作行，再返回**协作**行
        # （`writing.cancel` 的响应字段就是从协作行取的）。
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
    return {
        "client_request_id": data["client_request_id"],
        "article_id": data["article_id"],
        "chat_id": data["chat_id"],
        "action": data["action"],
        "base_revision": data["base_revision"],
        "snapshot_digest": data["snapshot_digest"],
        "instruction": data.get("instruction") or "",
        "selection": data.get("selection"),
        "material_ids": tuple(data.get("material_ids") or ()),
    }


def _close_waiting_author(repository: CollabRepository, collaboration_id: str) -> None:
    """作者回复会让等待中的批次让位（真 runner 的 ``_close_waiting_author_batches``）。"""
    for batch in repository.list_batches(collaboration_id):
        if batch.status == "waiting_author":
            repository.finish_batch(batch_id=batch.id, status="completed")


@dataclass(frozen=True)
class _Handle:
    collaboration_id: str
    batch_id: str
    status: str
    stage: str
    session_key: str
    active_batch_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "collaboration_id": self.collaboration_id,
            "batch_id": self.batch_id,
            "status": self.status,
            "stage": self.stage,
            "session_key": self.session_key,
            "active_batch_id": self.active_batch_id,
        }


@dataclass(frozen=True)
class _Confirmed:
    collaboration_id: str
    batch_id: str
    article_revision: int
    article_digest: str
    handoff_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "collaboration_id": self.collaboration_id,
            "batch_id": self.batch_id,
            "article_revision": self.article_revision,
            "article_digest": self.article_digest,
            "handoff_status": self.handoff_status,
        }


@pytest.fixture
def db(tmp_path: Path) -> WritingDatabase:
    database = WritingDatabase(tmp_path)
    database.ensure_schema()
    return database


@pytest.fixture
def runner(db: WritingDatabase) -> FakeRunner:
    return FakeRunner(db)


@pytest.fixture
def services(db: WritingDatabase, runner: FakeRunner) -> WritingCollabServices:
    return WritingCollabServices.for_workspace("default", db, runner)


def _article(services: WritingCollabServices, title: str = "标题") -> dict[str, Any]:
    payload = writing_api.create_article(
        WritingServices(workspace_id="default", repository=services.article_repository),
        client_request_id="r-article-1",
        title=title,
        body="第一段\n第二段",
    )
    return dict(payload["article"])


async def _start(
    services: WritingCollabServices, article: dict[str, Any], **kwargs: Any,
) -> dict[str, Any]:
    return await start_collaboration(
        services,
        client_request_id=kwargs.pop("client_request_id", CLIENT_REQUEST_ID),
        article_id=kwargs.pop("article_id", article["id"]),
        chat_id=kwargs.pop("chat_id", CHAT_ID),
        action=kwargs.pop("action", "start_full"),
        base_revision=kwargs.pop("base_revision", article["revision"]),
        snapshot_digest=kwargs.pop("snapshot_digest", ARTICLE_DIGEST),
        **kwargs,
    )


def _insert_suggestion(
    db: WritingDatabase,
    *,
    article_id: str,
    collaboration_id: str,
    target: dict[str, Any],
    after: str = "替换后",
    status: str = "pending",
    base_revision: int = 1,
) -> str:
    suggestion_id = new_id()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO writing_collab_suggestions (id, workspace_key, collaboration_id,"
            " artifact_id, article_id, base_revision, snapshot_digest, target_json, before_text,"
            " after_text, summary, notes, source_material_ids, status, applied_revision,"
            " created_at, applied_at)"
            " VALUES (?, ?, ?, '', ?, ?, ?, ?, '', ?, '', '', '[]', ?, NULL, ?, NULL)",
            (
                suggestion_id, db.workspace_key, collaboration_id, article_id, base_revision,
                "digest-1", _json(target), after, status, utc_now_iso(),
            ),
        )
    return suggestion_id


def _insert_undo_entry(
    db: WritingDatabase, *, article_id: str, suggestion_id: str, before_body: str,
) -> str:
    undo_id = new_id()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO writing_collab_undo_entries (id, workspace_key, article_id,"
            " suggestion_id, before_body, before_revision, result_revision, result_digest,"
            " reverted, created_at, reverted_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL)",
            (
                undo_id, db.workspace_key, article_id, suggestion_id, before_body, 1, 2,
                "digest-after", utc_now_iso(),
            ),
        )
    return undo_id


def _json(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _extras(payload: dict[str, Any]) -> dict[str, Any]:
    """归属字段是**客户端多余的**输入：payload 层没有这些形参，多传即 TypeError。

    因此测试要把它们当「不该被消费的杂键」处理——真正的「服务端忽略」由路由层的
    ``sanitized_extras`` 证明（见 ``test_writing_collab_routes.py``）。
    """
    accepted = {"instruction", "selection", "material_ids"}
    return {key: value for key, value in payload.items() if key in accepted}


# ---- §3 writing.start -------------------------------------------------------


@pytest.mark.asyncio
async def test_start_delegates_to_the_runner_with_frozen_fields(
    services: WritingCollabServices, runner: FakeRunner,
) -> None:
    """契约 §3：传输层只组装 ``StartCollabRequest`` 并调用 ``runner.start(request)``。"""
    article = _article(services)
    payload = await _start(services, article, instruction="先做提纲")

    assert set(payload) == {
        "collaboration_id", "batch_id", "status", "stage", "session_key", "active_batch_id",
    }
    assert payload["session_key"] == SESSION_KEY
    assert payload["active_batch_id"] == payload["batch_id"]
    assert payload["status"] == "queued"
    assert payload["stage"] == ""
    assert len(runner.started) == 1
    call = runner.started[0]
    # 冻结接口的字段名逐字一致：漂移即真装配时的 TypeError。
    assert set(call) == {
        "client_request_id", "article_id", "chat_id", "action", "base_revision",
        "snapshot_digest", "instruction", "selection", "material_ids",
    }
    assert call["client_request_id"] == CLIENT_REQUEST_ID
    assert call["article_id"] == article["id"]
    assert call["chat_id"] == CHAT_ID
    assert call["action"] == "start_full"
    assert call["base_revision"] == article["revision"]
    assert call["snapshot_digest"] == ARTICLE_DIGEST
    assert call["instruction"] == "先做提纲"
    # start_full 不带选区：选区只属于 rewrite_selection（契约 §3 / §4）。
    assert call["selection"] is None
    assert call["material_ids"] == ()


@pytest.mark.asyncio
async def test_start_does_not_write_collaboration_or_batch_itself(
    services: WritingCollabServices,
) -> None:
    """建库与幂等都是 runner 的职责：没 runner 时 start 必须 503，且不落任何行。"""
    article = _article(services)
    services_without_runner = WritingCollabServices.for_workspace(
        "default", services.database, None,
    )
    with pytest.raises(WebUISettingsError) as excinfo:
        await start_collaboration(
            services_without_runner,
            client_request_id=CLIENT_REQUEST_ID,
            article_id=article["id"],
            chat_id=CHAT_ID,
            action="start_full",
            base_revision=article["revision"],
            snapshot_digest=ARTICLE_DIGEST,
        )
    assert (excinfo.value.status, excinfo.value.message) == (
        503, "writing collaboration service is not configured",
    )
    assert services.collab_repository.get_by_article(article["id"]) is None


@pytest.mark.asyncio
async def test_start_same_request_id_same_payload_replays(
    services: WritingCollabServices, runner: FakeRunner,
) -> None:
    """幂等由执行栈负责（裁决 1）：同 request_id + 同载荷 → 重放，不建第二行。

    传输层不再自建幂等，它只组装请求交给 runner。真 runner 的重放语义由
    `tests/writing/test_collab_runner.py` 锁定；这里锁定「传输层不破坏重放」。
    """
    article = _article(services)
    first = await _start(services, article)
    second = await _start(services, article)

    assert second == first
    assert len(runner.started) == 2
    assert len(services.collab_repository.list_batches(first["collaboration_id"])) == 1


@pytest.mark.asyncio
async def test_start_requires_chat_id(services: WritingCollabServices) -> None:
    article = _article(services)
    with pytest.raises(WebUISettingsError) as excinfo:
        await _start(services, article, chat_id="bad chat")
    assert excinfo.value.status == 400
    assert excinfo.value.message.startswith("unsupported collaboration field: chat_id=")


@pytest.mark.asyncio
async def test_start_requires_selection_for_rewrite(services: WritingCollabServices) -> None:
    """契约 §4：选区缺失或为空 → 400。"""
    article = _article(services)
    with pytest.raises(WebUISettingsError) as excinfo:
        await _start(services, article, action="rewrite_selection")
    assert (excinfo.value.status, excinfo.value.message) == (
        400, "collaboration target selection is required",
    )


@pytest.mark.asyncio
async def test_start_rejects_unknown_action(services: WritingCollabServices) -> None:
    article = _article(services)
    with pytest.raises(WebUISettingsError) as excinfo:
        await _start(services, article, action="launch_missiles")
    assert excinfo.value.status == 400
    assert excinfo.value.message.startswith("unsupported collaboration field: action=")


@pytest.mark.asyncio
async def test_start_rejects_selection_for_non_rewrite_action(
    services: WritingCollabServices,
) -> None:
    article = _article(services)
    with pytest.raises(WebUISettingsError) as excinfo:
        await _start(services, article, selection={"start": 0, "end": 1})
    assert (excinfo.value.status, excinfo.value.message) == (
        400, "collaboration target selection is not allowed for this action",
    )


@pytest.mark.asyncio
async def test_start_conflicts_when_an_active_batch_exists(
    services: WritingCollabServices,
) -> None:
    article = _article(services)
    created = await _start(services, article)
    with pytest.raises(WebUISettingsError) as excinfo:
        await _start(services, article, client_request_id="r-2")
    assert (excinfo.value.status, excinfo.value.message) == (
        409,
        f"collaboration already has an active batch for article_id={article['id']}",
    )
    assert created["batch_id"]


@pytest.mark.asyncio
async def test_start_ignores_client_supplied_team_agent_and_skill_path(
    services: WritingCollabServices, runner: FakeRunner,
) -> None:
    """契约 §3：不接受 teamId/agentId/skillPath，团队/角色/Skill 一律服务端解析。"""
    article = _article(services)
    payload = await _start(
        services,
        article,
        **_extras({
            "teamId": "evil-team",
            "agentId": "evil-agent",
            "skillPath": "/tmp/evil.md",
            "workspace_key": "other-workspace",
        }),
    )
    assert payload["collaboration_id"]
    request = runner.started[0]
    # 冻结接口的字段面是封闭的：payload 里的归属字段既不进请求，也不改服务端解析。
    assert set(request) == {
        "client_request_id", "article_id", "chat_id", "action", "base_revision",
        "snapshot_digest", "instruction", "selection", "material_ids",
    }
    assert set(request) & PROTECTED_PAYLOAD_FIELDS == set()
    assert request["action"] == "start_full"
    assert request["base_revision"] == article["revision"]
    assert request["snapshot_digest"] == ARTICLE_DIGEST
    assert "evil" not in _flatten(request)
    collaboration = services.collab_repository.get_by_article(article["id"])
    assert collaboration is not None
    # 固定团队：协作绑定的是服务端解析的 chat_id，而不是 payload 里的任何归属字段。
    assert collaboration.chat_id == CHAT_ID
    assert set(PROTECTED_PAYLOAD_FIELDS) == {
        "teamId", "team_id", "agentId", "agent_id", "skillPath", "skill_path",
    }


@pytest.mark.asyncio
async def test_start_with_extras_does_not_change_fixed_team_binding(
    services: WritingCollabServices,
) -> None:
    """带归属字段的两次调用：第二次必须命中同一协作（不因 payload 归属而换绑）。"""
    article = _article(services)
    first = await _start(services, article, **_extras({"teamId": "evil-team"}))
    services.collab_repository.finish_batch(batch_id=first["batch_id"], status="failed")
    second = await _start(
        services, article, client_request_id="r-2",
        **_extras({"teamId": "other-team"}),
    )
    assert second["collaboration_id"] == first["collaboration_id"]


def _flatten(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


# ---- §3 writing.reply / confirm --------------------------------------------


@pytest.mark.asyncio
async def test_reply_requires_collaboration_and_version(
    services: WritingCollabServices, runner: FakeRunner,
) -> None:
    article = _article(services)
    created = await _start(services, article)
    # 活动批次未收尾时 reply 会命中契约 §4 的「已有活动批次」（`waiting_author`
    # 也算活动批次）；先让首轮收尾成终态，作者反馈再开新批次。
    services.collab_repository.finish_batch(batch_id=created["batch_id"], status="failed")
    collaboration = services.collab_repository.get_collaboration(created["collaboration_id"])

    payload = await reply_collaboration(
        services,
        client_request_id="r-2",
        collaboration_id=created["collaboration_id"],
        feedback="请补一段案例",
        expect_version=collaboration.version,
    )

    assert payload == {
        "collaboration_id": created["collaboration_id"],
        "batch_id": payload["batch_id"],
        "status": "queued",
    }
    assert payload["batch_id"]
    assert [item["action"] for item in runner.started] == ["start_full"]
    assert runner.replied == [{
        "collaboration_id": created["collaboration_id"],
        "feedback": "请补一段案例",
        "expect_version": collaboration.version,
    }]


@pytest.mark.asyncio
async def test_reply_with_stale_version_is_409(services: WritingCollabServices) -> None:
    article = _article(services)
    created = await _start(services, article)
    with pytest.raises(WebUISettingsError) as excinfo:
        await reply_collaboration(
            services,
            client_request_id="r-2",
            collaboration_id=created["collaboration_id"],
            feedback="喂",
            expect_version=999,
        )
    assert excinfo.value.status == 409
    assert excinfo.value.message.startswith("collaboration version conflict expected=999 ")


@pytest.mark.asyncio
async def test_reply_to_missing_collaboration_is_404(services: WritingCollabServices) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        await reply_collaboration(
            services,
            client_request_id="r-2",
            collaboration_id="nope",
            feedback="喂",
            expect_version=1,
        )
    assert (excinfo.value.status, excinfo.value.message) == (404, "collaboration not found")


@pytest.mark.asyncio
async def test_confirm_handoff_uses_integer_artifact_version(
    services: WritingCollabServices,
) -> None:
    """实施期裁决 §10.3：handoff CAS 用整数 ``artifact_version``。"""
    article = _article(services)
    created = await _start(services, article)
    artifact = services.collab_repository.add_artifact(
        collaboration_id=created["collaboration_id"],
        batch_id=created["batch_id"],
        attempt=1,
        kind="outline",
        role_id="writing-architect",
        content={"items": ["一", "二"]},
        source_ids=[],
        gaps=[],
    )
    handoff = services.collab_repository.add_handoff(
        collaboration_id=created["collaboration_id"],
        batch_id=created["batch_id"],
        artifact_id=artifact.id,
        kind="outline_confirm",
        artifact_version=artifact.version,
    )

    payload = confirm_handoff(
        services,
        client_request_id="r-2",
        collaboration_id=created["collaboration_id"],
        handoff_id=handoff.id,
        expected_handoff_version=artifact.version,
        article_revision=article["revision"],
        article_digest=ARTICLE_DIGEST,
        outline=["一", "二"],
    )

    assert payload["handoff_status"] == "confirmed"
    assert payload["article_revision"] == article["revision"]
    assert payload["article_digest"] == ARTICLE_DIGEST
    assert payload["batch_id"] == created["batch_id"]
    resolved = services.collab_repository.get_handoff(handoff.id)
    assert resolved.status == "confirmed"
    assert resolved.artifact_version == artifact.version


@pytest.mark.asyncio
async def test_confirm_with_stale_handoff_version_is_409(
    services: WritingCollabServices,
) -> None:
    article = _article(services)
    created = await _start(services, article)
    artifact = services.collab_repository.add_artifact(
        collaboration_id=created["collaboration_id"],
        batch_id=created["batch_id"],
        attempt=1,
        kind="outline",
        role_id="writing-architect",
        content={"items": ["一"]},
        source_ids=[],
        gaps=[],
    )
    handoff = services.collab_repository.add_handoff(
        collaboration_id=created["collaboration_id"],
        batch_id=created["batch_id"],
        artifact_id=artifact.id,
        kind="outline_confirm",
        artifact_version=artifact.version,
    )

    with pytest.raises(WebUISettingsError) as excinfo:
        confirm_handoff(
            services,
            client_request_id="r-2",
            collaboration_id=created["collaboration_id"],
            handoff_id=handoff.id,
            expected_handoff_version=99,
            article_revision=article["revision"],
            article_digest=ARTICLE_DIGEST,
        )
    assert (excinfo.value.status, excinfo.value.message) == (
        409, f"handoff version conflict expected=99 actual={artifact.version}",
    )


@pytest.mark.asyncio
async def test_confirm_rejects_boolean_handoff_version(
    services: WritingCollabServices,
) -> None:
    """布尔是 int 的子类，必须被整数校验挡住，否则 CAS 会静默拿 True == 1。"""
    article = _article(services)
    created = await _start(services, article)
    with pytest.raises(WebUISettingsError) as excinfo:
        confirm_handoff(
            services,
            client_request_id="r-2",
            collaboration_id=created["collaboration_id"],
            handoff_id="h-1",
            expected_handoff_version=True,
            article_revision=article["revision"],
            article_digest=ARTICLE_DIGEST,
        )
    assert excinfo.value.status == 400


# ---- §3 writing.cancel / retry ---------------------------------------------


@pytest.mark.asyncio
async def test_cancel_delegates_to_runner_and_does_not_touch_runner_state(
    services: WritingCollabServices, runner: FakeRunner,
) -> None:
    article = _article(services)
    created = await _start(services, article)
    payload = await cancel_collaboration(
        services,
        client_request_id="r-2",
        collaboration_id=created["collaboration_id"],
        active_batch_id=created["batch_id"],
    )
    assert payload == {
        "collaboration_id": created["collaboration_id"],
        "status": "cancelled",
        "cancel_generation": 1,
    }
    assert runner.cancelled == [{
        "collaboration_id": created["collaboration_id"],
        "active_batch_id": created["batch_id"],
    }]


@pytest.mark.asyncio
async def test_cancel_without_active_batch_is_409(services: WritingCollabServices) -> None:
    """契约 §4：无活动批次可取消；批次已收尾后 active_batch_id 不再匹配。"""
    article = _article(services)
    created = await _start(services, article)
    services.collab_repository.finish_batch(batch_id=created["batch_id"], status="failed")
    with pytest.raises(WebUISettingsError) as excinfo:
        await cancel_collaboration(
            services,
            client_request_id="r-2",
            collaboration_id=created["collaboration_id"],
            active_batch_id=created["batch_id"],
        )
    assert (excinfo.value.status, excinfo.value.message) == (
        409, f"no active batch for collaboration_id={created['collaboration_id']}",
    )


@pytest.mark.asyncio
async def test_retry_passes_expect_version_to_repository(
    services: WritingCollabServices, runner: FakeRunner,
) -> None:
    article = _article(services)
    created = await _start(services, article)
    services.collab_repository.finish_batch(batch_id=created["batch_id"], status="failed")
    collaboration = services.collab_repository.get_collaboration(created["collaboration_id"])

    payload = await retry_collaboration(
        services,
        client_request_id="r-2",
        collaboration_id=created["collaboration_id"],
        batch_id=created["batch_id"],
        expect_version=collaboration.version,
    )

    # 重试 = 新一代 attempt 行（新 batch_id，attempt 由仓储递增到 2），不是复用原行。
    assert payload["collaboration_id"] == created["collaboration_id"]
    assert payload["batch_id"] != created["batch_id"]
    assert payload["attempt"] == 2
    assert payload["status"] == "queued"
    assert runner.retried[0]["expect_version"] == collaboration.version


@pytest.mark.asyncio
async def test_retry_with_stale_version_is_409(services: WritingCollabServices) -> None:
    """重试的版本 CAS 只在批次已收尾后才会被走到（活动批次先撞 §4 的 409）。

    这与真 runner 的顺序一致：`CollabRunner.retry` 先拒活动批次，再交给
    `create_batch(expect_version=...)` 结算协作版本。
    """
    article = _article(services)
    created = await _start(services, article)
    services.collab_repository.finish_batch(batch_id=created["batch_id"], status="failed")
    with pytest.raises(WebUISettingsError) as excinfo:
        await retry_collaboration(
            services,
            client_request_id="r-2",
            collaboration_id=created["collaboration_id"],
            batch_id=created["batch_id"],
            expect_version=999,
        )
    assert excinfo.value.status == 409
    assert excinfo.value.message.startswith("collaboration version conflict expected=999 ")


@pytest.mark.asyncio
async def test_retry_on_an_active_batch_is_409(services: WritingCollabServices) -> None:
    """契约 §4：活动批次不能重试（先于版本 CAS 判定）。"""
    article = _article(services)
    created = await _start(services, article)
    with pytest.raises(WebUISettingsError) as excinfo:
        await retry_collaboration(
            services,
            client_request_id="r-2",
            collaboration_id=created["collaboration_id"],
            batch_id=created["batch_id"],
            expect_version=services.collab_repository.get_collaboration(
                created["collaboration_id"],
            ).version,
        )
    assert excinfo.value.status == 409
    assert excinfo.value.message.startswith("collaboration already has an active batch")


# ---- §3 writing.apply / undo ------------------------------------------------


@pytest.mark.asyncio
async def test_apply_returns_revision_digest_and_undo(
    services: WritingCollabServices,
) -> None:
    article = _article(services)
    created = await _start(services, article)
    suggestion_id = _insert_suggestion(
        services.database,
        article_id=article["id"],
        collaboration_id=created["collaboration_id"],
        target={"kind": "replace_range", "start": 0, "end": 3, "expected_before": "第一段"},
        after="改写段",
    )

    payload = apply_suggestion(
        services,
        client_request_id="r-2",
        suggestion_id=suggestion_id,
        article_id=article["id"],
        base_revision=article["revision"],
    )

    assert set(payload) == {
        "suggestion_id", "article_id", "revision", "digest",
        "staled_suggestion_ids", "undo_id",
    }
    assert payload["suggestion_id"] == suggestion_id
    assert payload["revision"] == 2
    assert payload["undo_id"]
    assert payload["staled_suggestion_ids"] == []


@pytest.mark.asyncio
async def test_apply_offset_error_is_400_with_code(services: WritingCollabServices) -> None:
    """契约 §10.7：``OffsetError`` → 400 ``suggestion target is invalid: <code>``。"""
    article = _article(services)
    created = await _start(services, article)
    suggestion_id = _insert_suggestion(
        services.database,
        article_id=article["id"],
        collaboration_id=created["collaboration_id"],
        target={"kind": "replace_range", "start": 0, "end": 999, "expected_before": ""},
    )

    with pytest.raises(WebUISettingsError) as excinfo:
        apply_suggestion(
            services,
            client_request_id="r-2",
            suggestion_id=suggestion_id,
            article_id=article["id"],
            base_revision=article["revision"],
        )
    assert (excinfo.value.status, excinfo.value.message) == (
        400, "suggestion target is invalid: out_of_range",
    )


@pytest.mark.asyncio
async def test_apply_before_mismatch_code_is_400(services: WritingCollabServices) -> None:
    article = _article(services)
    created = await _start(services, article)
    suggestion_id = _insert_suggestion(
        services.database,
        article_id=article["id"],
        collaboration_id=created["collaboration_id"],
        target={"kind": "replace_range", "start": 0, "end": 3, "expected_before": "不是这段"},
    )
    with pytest.raises(WebUISettingsError) as excinfo:
        apply_suggestion(
            services,
            client_request_id="r-2",
            suggestion_id=suggestion_id,
            article_id=article["id"],
            base_revision=article["revision"],
        )
    assert (excinfo.value.status, excinfo.value.message) == (
        400, "suggestion target is invalid: expected_before_mismatch",
    )


@pytest.mark.asyncio
async def test_apply_non_pending_suggestion_is_409(services: WritingCollabServices) -> None:
    article = _article(services)
    created = await _start(services, article)
    suggestion_id = _insert_suggestion(
        services.database,
        article_id=article["id"],
        collaboration_id=created["collaboration_id"],
        target={"kind": "replace_range", "start": 0, "end": 3, "expected_before": "第一段"},
        status="applied",
    )
    with pytest.raises(WebUISettingsError) as excinfo:
        apply_suggestion(
            services,
            client_request_id="r-2",
            suggestion_id=suggestion_id,
            article_id=article["id"],
            base_revision=article["revision"],
        )
    assert (excinfo.value.status, excinfo.value.message) == (409, "suggestion is not pending")


@pytest.mark.asyncio
async def test_undo_restores_body_and_consumes_entry(services: WritingCollabServices) -> None:
    article = _article(services)
    created = await _start(services, article)
    suggestion_id = _insert_suggestion(
        services.database,
        article_id=article["id"],
        collaboration_id=created["collaboration_id"],
        target={"kind": "replace_range", "start": 0, "end": 3, "expected_before": "第一段"},
        after="改写段",
    )
    applied = apply_suggestion(
        services,
        client_request_id="r-2",
        suggestion_id=suggestion_id,
        article_id=article["id"],
        base_revision=article["revision"],
    )

    payload = undo(
        services,
        client_request_id="r-3",
        undo_id=applied["undo_id"],
        base_revision=applied["revision"],
    )

    assert payload["undo_id"] == applied["undo_id"]
    assert payload["article_id"] == article["id"]
    assert payload["revision"] == applied["revision"] + 1
    assert payload["suggestion_id"] == suggestion_id
    restored = writing_api.fetch_article(
        WritingServices(workspace_id="default", repository=services.article_repository),
        article["id"],
    )
    assert restored["article"]["body"] == "第一段\n第二段"


def test_undo_with_unknown_entry_is_404(services: WritingCollabServices) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        undo(services, client_request_id="r-2", undo_id="nope", base_revision=1)
    assert (excinfo.value.status, excinfo.value.message) == (404, "undo entry not found")


@pytest.mark.asyncio
async def test_undo_blocked_by_newer_edits_is_409(services: WritingCollabServices) -> None:
    article = _article(services)
    created = await _start(services, article)
    suggestion_id = _insert_suggestion(
        services.database,
        article_id=article["id"],
        collaboration_id=created["collaboration_id"],
        target={"kind": "replace_range", "start": 0, "end": 3, "expected_before": "第一段"},
    )
    applied = apply_suggestion(
        services,
        client_request_id="r-2",
        suggestion_id=suggestion_id,
        article_id=article["id"],
        base_revision=article["revision"],
    )
    writing_api.save_article(
        WritingServices(workspace_id="default", repository=services.article_repository),
        client_request_id="r-4",
        article_id=article["id"],
        base_revision=applied["revision"],
        snapshot={"title": article["title"], "body": "用户又改了一版"},
    )
    with pytest.raises(WebUISettingsError) as excinfo:
        undo(
            services,
            client_request_id="r-5",
            undo_id=applied["undo_id"],
            base_revision=applied["revision"],
        )
    assert (excinfo.value.status, excinfo.value.message) == (409, "undo blocked by newer edits")


@pytest.mark.asyncio
async def test_apply_overlapping_targets_is_409(services: WritingCollabServices) -> None:
    """契约 §4：批量补丁相互重叠 → 409 ``suggestion targets overlap``。"""
    from nanobot.webui.writing_collab_api import apply_batch_suggestions

    article = _article(services)
    created = await _start(services, article)
    target = {"kind": "replace_range", "start": 0, "end": 3, "expected_before": "第一段"}
    first = _insert_suggestion(
        services.database,
        article_id=article["id"],
        collaboration_id=created["collaboration_id"],
        target=dict(target),
    )
    second = _insert_suggestion(
        services.database,
        article_id=article["id"],
        collaboration_id=created["collaboration_id"],
        target=dict(target),
    )
    with pytest.raises(WebUISettingsError) as excinfo:
        apply_batch_suggestions(
            services,
            client_request_id="r-3",
            suggestion_ids=[first, second],
            article_id=article["id"],
            base_revision=article["revision"],
        )
    assert (excinfo.value.status, excinfo.value.message) == (
        409, "suggestion targets overlap",
    )


def test_translate_error_maps_repository_exceptions() -> None:
    from nanobot.webui.writing_collab_api import translate_error
    from nanobot.writing.collab.offsets import OffsetError
    from nanobot.writing.collab.repository import (
        ActiveBatchConflict,
        CollaborationNotFound,
        CollabVersionConflict,
        NoActiveBatch,
        RequestReplayConflict,
    )
    from nanobot.writing.models import ArticleLimitError

    assert translate_error(CollaborationNotFound("c-1")) == (404, "collaboration not found")
    assert translate_error(ActiveBatchConflict("a-1")) == (
        409, "collaboration already has an active batch for article_id=a-1",
    )
    assert translate_error(CollabVersionConflict(expected=1, actual=2)) == (
        409, "collaboration version conflict expected=1 actual=2",
    )
    assert translate_error(NoActiveBatch("c-1")) == (
        409, "no active batch for collaboration_id=c-1",
    )
    assert translate_error(RequestReplayConflict()) == (
        409, "request_id was already used for a different WebUI mutation",
    )
    assert translate_error(OffsetError("target out of range", code="out_of_range")) == (
        400, "suggestion target is invalid: out_of_range",
    )
    assert translate_error(ArticleLimitError("suggestion targets overlap", status=409)) == (
        409, "suggestion targets overlap",
    )
    assert translate_error(ArticleLimitError("undo entry not found", status=404)) == (
        404, "undo entry not found",
    )


# ---- 读动作 -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_collaboration_snapshot_projection(
    services: WritingCollabServices,
) -> None:
    article = _article(services)
    created = await _start(services, article)
    payload = fetch_collaboration(services, article_id=article["id"])

    assert set(payload) == {
        "collaboration", "batches", "artifacts", "handoffs", "suggestions",
        "last_version", "last_seq", "next_actions", "blocked_reason",
    }
    assert payload["collaboration"]["id"] == created["collaboration_id"]
    assert payload["last_version"] == payload["collaboration"]["version"]
    assert payload["last_seq"] == 0


def test_fetch_collaboration_for_unknown_article_is_empty_snapshot(
    services: WritingCollabServices,
) -> None:
    payload = fetch_collaboration(services, article_id="nope")
    assert payload["collaboration"] is None
    assert payload["batches"] == []
    assert payload["next_actions"] == ["start_full", "ideate"]


def test_fetch_collaboration_requires_article_id(services: WritingCollabServices) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        fetch_collaboration(services, article_id=None)
    assert (excinfo.value.status, excinfo.value.message) == (400, "article_id is required")


@pytest.mark.asyncio
async def test_fetch_artifact_returns_full_content(services: WritingCollabServices) -> None:
    article = _article(services)
    created = await _start(services, article)
    artifact = services.collab_repository.add_artifact(
        collaboration_id=created["collaboration_id"],
        batch_id=created["batch_id"],
        attempt=1,
        kind="outline",
        role_id="writing-architect",
        content={"items": ["一"]},
        source_ids=[],
        gaps=[],
    )
    payload = fetch_artifact(services, artifact_id=artifact.id)
    assert payload["id"] == artifact.id
    assert payload["content"] == {"items": ["一"]}


def test_fetch_artifact_unknown_is_404(services: WritingCollabServices) -> None:
    with pytest.raises(WebUISettingsError) as excinfo:
        fetch_artifact(services, artifact_id="nope")
    assert (excinfo.value.status, excinfo.value.message) == (404, "artifact not found")


@pytest.mark.asyncio
async def test_services_are_workspace_scoped(tmp_path: Path) -> None:
    """跨工作区不可见：右区拿到的是空投影，读不到左区的协作。"""
    left_db = WritingDatabase(tmp_path / "left")
    right_db = WritingDatabase(tmp_path / "right")
    left = WritingCollabServices.for_workspace("left", left_db, FakeRunner(left_db))
    right = WritingCollabServices.for_workspace("right", right_db, FakeRunner(right_db))
    left_article = _article(left)
    created = await _start(left, left_article)

    assert fetch_collaboration(right, article_id=left_article["id"])["collaboration"] is None
    with pytest.raises(WebUISettingsError) as excinfo:
        await reply_collaboration(
            right,
            client_request_id="r-9",
            collaboration_id=created["collaboration_id"],
            feedback="越区",
            expect_version=1,
        )
    assert (excinfo.value.status, excinfo.value.message) == (404, "collaboration not found")


# ---- 协议级必填 -------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["client_request_id", "article_id", "chat_id", "action", "base_revision", "snapshot_digest"],
)
@pytest.mark.asyncio
async def test_start_requires_every_documented_field(
    services: WritingCollabServices, field: str,
) -> None:
    article = _article(services)
    kwargs: dict[str, Any] = {
        "client_request_id": "r-1",
        "article_id": article["id"],
        "chat_id": CHAT_ID,
        "action": "start_full",
        "base_revision": article["revision"],
        "snapshot_digest": ARTICLE_DIGEST,
    }
    kwargs.pop(field)
    with pytest.raises(WebUISettingsError) as excinfo:
        await start_collaboration(services, **kwargs)
    assert excinfo.value.status == 400, field
    assert excinfo.value.message.endswith("is required")
