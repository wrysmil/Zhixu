"""协作批次运行器单测（plan Task 8 / 契约 §1/§3/§4/§9）。

覆盖用例：等待作者时结束批次并释放活句柄、确认后在同一协作内开新批次并采用新提纲
版本、旧交接版本 409、取消后代数闸门拦住晚到结果、15 分钟期限走同一 cancel/清理链、
重试新 attempt 且复用仍有效产物、重启收敛把在途批次标 interrupted、同文章第二个活动
批次 409、``agents.teams.enabled=false`` 时装配出的 service 开关为假、选区缺失 400、
状态跃迁的结构化日志字段（且不记录正文）。

全部依赖走注入：假 ``TeamPort`` / 假 ``ArtifactSink`` / 假时钟 / 假日志，仓储与文章库
用真实 SQLite（``tmp_path``），因此 CAS、活动批次唯一与收敛都是真实行为而非替身行为。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from nanobot.agent.skills import SkillsLoader
from nanobot.channels.websocket.runtime import WebSocketConfig
from nanobot.config.loader import save_config
from nanobot.config.schema import Config
from nanobot.webui.gateway_services import build_gateway_services
from nanobot.writing.collab.models import (
    Action,
    BatchStatus,
    CollaborationStatus,
    HandoffStatus,
)
from nanobot.writing.collab.recipe import RECIPES
from nanobot.writing.collab.repository import (
    ActiveBatchConflict,
    CollabRepository,
    CollabVersionConflict,
)
from nanobot.writing.collab.runner import (
    CollabRunner,
    CollabSelectionRequired,
    StartCollabRequest,
    frozen_preparer,
)
from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import snapshot_digest
from nanobot.writing.repository import ArticleRepository

# 配方表由 WU-06 拥有；本文件的假配方只提供运行器需要的 action → recipe 映射。
_RECIPES: dict[str, dict[str, Any]] = {
    action: {"action": action, "roles": ("writing-author",), "lead_role": "writing-editor"}
    for action in (
        "start_full", "ideate", "research", "outline", "draft",
        "rewrite_selection", "review_all", "reply",
    )
}


def _digest(article: dict[str, Any]) -> str:
    return snapshot_digest(article)


# ---- 假依赖 -----------------------------------------------------------------


@dataclass
class _FakeArtifact:
    id: str
    kind: str
    version: int
    summary: str = ""


@dataclass
class _FakeTeam:
    started: list[dict[str, Any]] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    #: 停 run 期间的回调：用来复现「``cancel_run`` 还没返回、批次行仍是 queued/running」
    #: 这段窗口里到达的晚到结果（生产里由团队域的事件投递触发）。
    on_cancel: Callable[[str], None] | None = None

    async def start_batch(self, **kwargs: Any) -> str:
        run_id = f"run-{len(self.started) + 1}"
        self.started.append({**kwargs, "run_id": run_id})
        return run_id

    async def cancel_run(self, run_id: str) -> None:
        self.cancelled.append(run_id)
        if self.on_cancel is not None:
            self.on_cancel(run_id)


@dataclass
class _FakeArtifacts:
    """假产物出口：按脚本返回产物；``None`` 表示诊断性输出（不生成可采纳卡）。"""

    scripted: list[_FakeArtifact | None] = field(default_factory=list)
    ingested: list[dict[str, Any]] = field(default_factory=list)

    def ingest(
        self,
        *,
        collaboration_id: str,
        batch_id: str,
        attempt: int,
        event: Any,
    ) -> _FakeArtifact | None:
        self.ingested.append(
            {
                "collaboration_id": collaboration_id,
                "batch_id": batch_id,
                "attempt": attempt,
                "event": event,
            }
        )
        return self.scripted.pop(0) if self.scripted else None


@dataclass
class _FakePreparer:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(
        self,
        *,
        recipe: Any,
        article_snapshot: Any,
        material_ids: Any,
        collaboration_id: str,
        workspace_key: str = "",
        article_id: str = "",
        batch_id: str = "",
    ) -> dict[str, Any]:
        snapshot = dict(article_snapshot)
        self.calls.append(
            {
                "recipe": recipe,
                "article_snapshot": snapshot,
                "material_ids": tuple(material_ids),
                "collaboration_id": collaboration_id,
                "workspace_key": workspace_key,
                "article_id": article_id,
                "batch_id": batch_id,
            }
        )
        return {
            "skills": (
                {
                    "name": "article-outline",
                    "path": "skills/article-outline.md",
                    "sha256": "a" * 8,
                    # 技能正文只进运行期上下文，**不许**进 batch.frozen（契约 §1）。
                    "body": "技能正文-机密",
                },
            ),
            "model_id": "model-x",
            "source_scope": {"article_id": snapshot.get("id", "")},
        }


@dataclass
class _FakePublisher:
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __call__(self, name: str, body: Any) -> None:
        self.events.append((name, dict(body)))


class _FakeClock:
    """假时钟：``sleep`` 记录期限并停在闸门上，测试显式放行来触发超时。"""

    def __init__(self) -> None:
        self.timeouts: list[float] = []
        self._gate = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        self.timeouts.append(seconds)
        await self._gate.wait()

    def fire(self) -> None:
        self._gate.set()


class _BoundLog:
    def __init__(self, recorder: _LogRecorder, fields: dict[str, Any]) -> None:
        self._recorder = recorder
        self._fields = fields

    def _emit(self, message: str) -> None:
        self._recorder.records.append({**self._fields, "message": message})

    info = _emit
    warning = _emit
    debug = _emit
    exception = _emit


class _LogRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def bind(self, **fields: Any) -> _BoundLog:
        return _BoundLog(self, fields)

    def _emit(self, message: str) -> None:
        self.records.append({"message": message})

    info = _emit
    warning = _emit
    debug = _emit
    exception = _emit


@dataclass(frozen=True)
class _Event:
    run_id: str
    type: str
    data: dict[str, Any] = field(default_factory=dict)


def _event(run_id: str, event_type: str = "task.finished", **data: Any) -> _Event:
    return _Event(run_id=run_id, type=event_type, data=dict(data))


# ---- 装置 -------------------------------------------------------------------


@dataclass
class _Harness:
    db: WritingDatabase
    repo: CollabRepository
    articles: ArticleRepository
    team: _FakeTeam
    artifacts: _FakeArtifacts
    preparer: _FakePreparer
    publisher: _FakePublisher
    clock: _FakeClock
    logs: _LogRecorder
    runner: CollabRunner

    def request(
        self,
        *,
        article: dict[str, Any],
        action: str = Action.START_FULL,
        request_id: str = "req-start-1",
        chat_id: str = "chat-1",
        instruction: str = "",
        selection: Any = None,
        material_ids: tuple[str, ...] = (),
    ) -> StartCollabRequest:
        return StartCollabRequest(
            client_request_id=request_id,
            article_id=str(article["id"]),
            chat_id=chat_id,
            action=action,
            base_revision=int(article["revision"]),
            snapshot_digest=_digest(article),
            instruction=instruction,
            selection=selection,
            material_ids=material_ids,
        )


def _store(tmp_path: Path) -> tuple[WritingDatabase, CollabRepository, ArticleRepository]:
    db = WritingDatabase(tmp_path / "ws")
    repo = CollabRepository(db)
    return db, repo, ArticleRepository(db)


def _create_article(
    articles: ArticleRepository, *, body: str = "正文甲", request_id: str = "req-article-1"
) -> dict[str, Any]:
    created = articles.create_article(request_id, {"title": "标题", "body": body})
    return dict(created["article"])


def _build_runner(
    db: WritingDatabase,
    repo: CollabRepository,
    articles: ArticleRepository,
    *,
    team: _FakeTeam | None = None,
    artifacts: _FakeArtifacts | None = None,
    preparer: _FakePreparer | None = None,
    publisher: _FakePublisher | None = None,
    clock: _FakeClock | None = None,
    logs: _LogRecorder | None = None,
    converge: bool = True,
    recipes: Any = None,
) -> CollabRunner:
    return CollabRunner(
        repository=repo,
        workspace_key=db.workspace_key,
        articles=articles,
        team=team if team is not None else _FakeTeam(),
        artifacts=artifacts if artifacts is not None else _FakeArtifacts(),
        recipes=_RECIPES if recipes is None else recipes,
        preparer=preparer if preparer is not None else _FakePreparer(),
        publisher=publisher if publisher is not None else _FakePublisher(),
        sleep=(clock or _FakeClock()).sleep,
        logger=logs if logs is not None else _LogRecorder(),
        converge=converge,
    )


def _harness(tmp_path: Path, *, converge: bool = True) -> _Harness:
    db, repo, articles = _store(tmp_path)
    team, artifacts, preparer = _FakeTeam(), _FakeArtifacts(), _FakePreparer()
    publisher, clock, logs = _FakePublisher(), _FakeClock(), _LogRecorder()
    runner = _build_runner(
        db, repo, articles, team=team, artifacts=artifacts, preparer=preparer,
        publisher=publisher, clock=clock, logs=logs, converge=converge,
    )
    return _Harness(
        db=db, repo=repo, articles=articles, team=team, artifacts=artifacts,
        preparer=preparer, publisher=publisher, clock=clock, logs=logs, runner=runner,
    )


async def _hand_off_outline(h: _Harness, handle: Any) -> Any:
    """模拟成员产出提纲 → 交接：返回落库的 pending 交接。"""
    run_id = h.team.started[0]["run_id"]
    h.artifacts.scripted.append(_FakeArtifact(id="art-outline-1", kind="outline", version=1))
    h.runner.on_team_event(_event(run_id))
    await h.runner.drain()
    return h.repo.list_handoffs(handle.collaboration_id)[0]


# ---- 用例 -------------------------------------------------------------------


async def test_waiting_author_ends_batch_without_live_handle(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    article = _create_article(h.articles)
    handle = await h.runner.start(h.request(article=article))
    run_id = h.team.started[0]["run_id"]
    assert h.runner.pending_deadlines() != ()

    handoff = await _hand_off_outline(h, handle)

    batch = h.repo.get_batch(handle.batch_id)
    collaboration = h.repo.get_collaboration(handle.collaboration_id)
    assert batch.status == BatchStatus.WAITING_AUTHOR
    assert batch.finished_at is not None
    assert collaboration.status == CollaborationStatus.WAITING_AUTHOR
    assert collaboration.active_batch_id is None
    assert handoff.kind == "outline_confirm"
    assert handoff.artifact_version == 1
    assert handoff.status == HandoffStatus.PENDING
    assert h.runner.live_batches() == ()
    assert h.runner.pending_deadlines() == ()
    assert h.team.cancelled == [run_id]
    assert [name for name, _ in h.publisher.events] == ["writing_artifact", "writing_handoff"]

    # 无存活模型任务 / 锁 / 未闭合事务：等待作者时立刻能开下一批次。
    reply = await h.runner.reply(
        collaboration_id=handle.collaboration_id,
        feedback="按这个提纲写",
        expect_version=collaboration.version,
    )
    assert reply.collaboration_id == handle.collaboration_id
    assert reply.attempt == 2
    assert h.repo.get_batch(handle.batch_id).status == BatchStatus.COMPLETED


async def test_confirm_creates_next_batch_in_same_collaboration(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    article = _create_article(h.articles)
    handle = await h.runner.start(h.request(article=article))
    handoff = await _hand_off_outline(h, handle)
    edited = [{"id": "o-1", "text": "改后的第一节", "order": 0}]

    result = await h.runner.confirm(
        collaboration_id=handle.collaboration_id,
        handoff_id=handoff.id,
        expected_handoff_version=handoff.artifact_version,
        outline=edited,
        article_revision=int(article["revision"]),
        article_digest=_digest(article),
    )

    assert result.collaboration_id == handle.collaboration_id
    assert result.handoff_status == HandoffStatus.CONFIRMED
    assert result.article_revision == int(article["revision"]) + 1
    assert result.batch_id != handle.batch_id

    next_batch = h.repo.get_batch(result.batch_id)
    assert next_batch.collaboration_id == handle.collaboration_id
    assert next_batch.attempt == 2
    assert next_batch.base_revision == result.article_revision
    assert next_batch.snapshot_digest == result.article_digest
    assert next_batch.status == BatchStatus.QUEUED

    saved = h.articles.get_article(str(article["id"]))["article"]
    assert [item["text"] for item in saved["outline"]] == ["改后的第一节"]
    assert h.repo.get_handoff(handoff.id).status == HandoffStatus.CONFIRMED
    assert h.repo.get_batch(handle.batch_id).status == BatchStatus.COMPLETED
    after = h.repo.get_collaboration(handle.collaboration_id)
    assert after.status == CollaborationStatus.RUNNING
    assert after.active_batch_id == result.batch_id
    # 新提纲版本：后续批次的冻结准备看到的是作者编辑后的提纲。
    assert h.preparer.calls[-1]["article_snapshot"]["outline"][0]["text"] == "改后的第一节"
    assert len(h.team.started) == 2


async def test_confirm_rejects_stale_handoff_version(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    article = _create_article(h.articles)
    handle = await h.runner.start(h.request(article=article))
    handoff = await _hand_off_outline(h, handle)
    stale = handoff.artifact_version + 7

    with pytest.raises(CollabVersionConflict) as excinfo:
        await h.runner.confirm(
            collaboration_id=handle.collaboration_id,
            handoff_id=handoff.id,
            expected_handoff_version=stale,
            outline=[{"id": "o-1", "text": "不该落盘的提纲", "order": 0}],
            article_revision=int(article["revision"]),
            article_digest=_digest(article),
        )

    assert str(excinfo.value) == f"handoff version conflict expected={stale} actual=1"
    assert excinfo.value.kind == "handoff"
    assert excinfo.value.status == 409
    # 交接、文章与批次都没有被动过。
    assert h.repo.get_handoff(handoff.id).status == HandoffStatus.PENDING
    assert h.articles.get_article(str(article["id"]))["article"]["revision"] == article["revision"]
    assert len(h.team.started) == 1


async def test_cancel_generation_blocks_late_results(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    article = _create_article(h.articles)
    handle = await h.runner.start(h.request(article=article))
    run_id = h.team.started[0]["run_id"]

    cancelled = await h.runner.cancel(
        collaboration_id=handle.collaboration_id, active_batch_id=handle.batch_id
    )

    assert cancelled.status == CollaborationStatus.CANCELLED
    assert cancelled.active_batch_id is None
    assert h.team.cancelled == [run_id]
    assert h.repo.get_batch(handle.batch_id).cancel_generation == 1
    assert h.runner.live_batches() == ()
    assert h.runner.pending_deadlines() == ()
    before = h.repo.list_artifacts(handle.collaboration_id)

    # 晚到的 task.finished：带旧代数、以及不带代数，都必须被闸门拦住。
    h.artifacts.scripted.append(_FakeArtifact(id="art-late", kind="outline", version=1))
    h.runner.on_team_event(_event(run_id, cancelGeneration=0))
    h.runner.on_team_event(_event(run_id))

    assert h.artifacts.ingested == []
    assert h.publisher.events == []
    assert h.repo.list_artifacts(handle.collaboration_id) == before
    assert h.repo.get_collaboration(handle.collaboration_id).status == (
        CollaborationStatus.CANCELLED
    )
    assert h.repo.get_batch(handle.batch_id).status == BatchStatus.CANCELLED
    assert [
        record for record in h.logs.records if record.get("event") == "writing_collab_event_dropped"
    ]


def _dropped_reasons(h: _Harness) -> list[str]:
    return [
        str(record.get("reason"))
        for record in h.logs.records
        if record.get("event") == "writing_collab_event_dropped"
    ]


async def test_cancel_window_drops_late_result_without_live_handle(tmp_path: Path) -> None:
    """M-3：``cancel_run`` 尚未返回时到达的结果只能靠「活句柄已释放」拦住。

    这是 ``if not ref.live`` 唯一不可替代的窗口：``_cancel_chain`` 先就地换代（旧 ref
    ``live=False``、``current_generation`` 已 +1）再 ``await cancel_run``，此刻批次行仍是
    ``queued``、协作仍是 ``running`` 且它是活动批次 —— 闸门的其余条件**全部通过**，
    只有活句柄标记能拦下这条晚到结果。变异探针：把 ``if not ref.live`` 改成 ``if False``
    时本用例必须变红（产物会落库、可采纳事件会外推）。
    """
    h = _harness(tmp_path)
    article = _create_article(h.articles)
    handle = await h.runner.start(h.request(article=article))
    run_id = h.team.started[0]["run_id"]

    h.artifacts.scripted.append(_FakeArtifact(id="art-window", kind="outline", version=1))
    # 事件带的是换代后的代数（团队域回显协作当前代数）：代数闸门不会拦它。
    late = _event(run_id, cancelGeneration=1)
    h.team.on_cancel = lambda _run_id: h.runner.on_team_event(late)

    await h.runner.cancel(
        collaboration_id=handle.collaboration_id, active_batch_id=handle.batch_id
    )
    await h.runner.drain()

    assert h.artifacts.ingested == []
    assert h.publisher.events == []
    assert _dropped_reasons(h) == ["batch handle has been released"]
    assert h.repo.list_artifacts(handle.collaboration_id) == {}
    assert h.repo.get_collaboration(handle.collaboration_id).status == (
        CollaborationStatus.CANCELLED
    )


async def test_cancel_then_reopen_drops_old_run_and_stale_generation(tmp_path: Path) -> None:
    """M-3：同一协作取消后重开新批次，旧 run 事件与旧代数事件都不得落产物。

    三个边界一次锁死：
    1. 旧 ``run_id`` 的事件随后到达（句柄已释放）→ 只落 warning 日志，不落产物、不推
       可采纳事件；
    2. 新 run 回显的是取消前的代数（批次行里那份 0 从不递增，内存代数才是水位）→ 代数
       闸门拒绝；
    3. 新 run 用当前代数时必须正常交付 —— 闸门不是一律拒绝。
    变异探针：把 ``cancel_generation`` 校验改成 ``if False`` 时本用例必须变红（第 2 步会
    落产物并外推 ``writing_artifact``/``writing_handoff``）。
    """
    h = _harness(tmp_path)
    article = _create_article(h.articles)
    handle = await h.runner.start(h.request(article=article))
    old_run = h.team.started[0]["run_id"]

    cancelled = await h.runner.cancel(
        collaboration_id=handle.collaboration_id, active_batch_id=handle.batch_id
    )
    assert cancelled.status == CollaborationStatus.CANCELLED

    # 同一协作重开新批次（内存丢弃代数 0 → 1）。
    reopened = await h.runner.reply(
        collaboration_id=handle.collaboration_id,
        feedback="按新方向重开",
        expect_version=cancelled.version,
    )
    new_run = h.team.started[-1]["run_id"]
    assert reopened.attempt == 2
    assert new_run != old_run

    # 1) 旧 run 的事件随后到达：句柄已释放，必须只落 warning。
    h.artifacts.scripted.append(_FakeArtifact(id="art-old-run", kind="outline", version=1))
    h.runner.on_team_event(_event(old_run))
    await h.runner.drain()

    assert h.artifacts.ingested == []
    assert h.publisher.events == []
    assert _dropped_reasons(h) == ["batch handle has been released"]

    # 2) 新 run 回显取消前的代数：换代闸门必须拒绝（否则旧世代结果会落进重开的批次）。
    h.runner.on_team_event(_event(new_run, cancelGeneration=0))
    await h.runner.drain()

    assert h.artifacts.ingested == []
    assert h.publisher.events == []
    assert _dropped_reasons(h) == [
        "batch handle has been released",
        "cancel generation mismatch",
    ]
    assert h.repo.list_artifacts(handle.collaboration_id) == {}

    # 3) 新 run 用当前代数（缺省即启动代数 1）：正常落产物并外推。
    h.artifacts.scripted.clear()
    h.artifacts.scripted.append(_FakeArtifact(id="art-new-run", kind="outline", version=1))
    h.runner.on_team_event(_event(new_run))
    await h.runner.drain()

    assert [entry["batch_id"] for entry in h.artifacts.ingested] == [reopened.id]
    assert [name for name, _ in h.publisher.events] == ["writing_artifact", "writing_handoff"]


async def test_batch_timeout_uses_same_cancel_chain(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    article = _create_article(h.articles)
    handle = await h.runner.start(h.request(article=article))
    run_id = h.team.started[0]["run_id"]

    # 期限由批次自持的期限任务保证（§9：单次自动批次 15 分钟），不是只填配置字段。
    assert h.clock.timeouts == [900.0]
    tasks = h.runner.pending_deadlines()
    assert len(tasks) == 1

    h.clock.fire()
    await asyncio.wait_for(tasks[0], timeout=1)

    assert h.repo.get_batch(handle.batch_id).status == BatchStatus.CANCELLED
    assert h.repo.get_collaboration(handle.collaboration_id).status == (
        CollaborationStatus.CANCELLED
    )
    assert h.team.cancelled == [run_id]
    assert h.runner.pending_deadlines() == ()
    assert h.runner.live_batches() == ()


async def test_retry_reuses_valid_artifacts(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    article = _create_article(h.articles)
    handle = await h.runner.start(h.request(article=article))
    run_id = h.team.started[0]["run_id"]
    artifact = h.repo.add_artifact(
        collaboration_id=handle.collaboration_id,
        batch_id=handle.batch_id,
        attempt=1,
        kind="brief",
        role_id="writing-editor",
        content={"summary": "要点"},
        source_ids=[],
        gaps=[],
    )
    h.artifacts.scripted.append(_FakeArtifact(id="art-brief-1", kind="brief", version=1))
    h.runner.on_team_event(_event(run_id))
    await h.runner.drain()
    h.repo.finish_batch(batch_id=handle.batch_id, status=BatchStatus.FAILED, error_message="boom")
    collaboration = h.repo.get_collaboration(handle.collaboration_id)
    original_frozen = h.repo.get_batch(handle.batch_id).frozen

    retried = await h.runner.retry(
        collaboration_id=handle.collaboration_id,
        batch_id=handle.batch_id,
        expect_version=collaboration.version,
    )

    assert retried.attempt == 2
    assert retried.id != handle.batch_id
    assert retried.collaboration_id == handle.collaboration_id
    assert retried.status == BatchStatus.QUEUED
    assert retried.frozen == original_frozen
    # 不重复采纳：仍然只有第一次尝试留下的那一条产物。
    assert set(h.repo.list_artifacts(handle.collaboration_id)) == {artifact.id}
    assert h.team.started[-1]["attempt"] == 2
    assert h.team.cancelled == [run_id]
    assert h.repo.get_collaboration(handle.collaboration_id).status == CollaborationStatus.RUNNING
    assert len(h.runner.live_batches()) == 1


def test_restart_marks_interrupted(tmp_path: Path) -> None:
    db, repo, articles = _store(tmp_path)
    article = _create_article(articles)
    collaboration = repo.create_collaboration(
        article_id=str(article["id"]),
        chat_id="chat-1",
        session_key="websocket:chat-1",
        goal={},
        request_id="req-collab-1",
    )
    batch = repo.create_batch(
        collaboration_id=collaboration.id,
        action=Action.START_FULL,
        base_revision=int(article["revision"]),
        snapshot_digest=_digest(article),
        instruction="",
        frozen={},
        expect_version=None,
        request_id="req-batch-1",
    )
    artifact = repo.add_artifact(
        collaboration_id=collaboration.id,
        batch_id=batch.id,
        attempt=1,
        kind="outline",
        role_id="writing-architect",
        content={"sections": ["第一节"]},
        source_ids=[],
        gaps=[],
    )
    team = _FakeTeam()

    runner = _build_runner(db, repo, articles, team=team)

    assert repo.get_batch(batch.id).status == BatchStatus.INTERRUPTED
    after = repo.get_collaboration(collaboration.id)
    assert after.status == CollaborationStatus.INTERRUPTED
    assert after.active_batch_id is None
    assert repo.get_artifact(artifact.id).content == {"sections": ["第一节"]}
    assert team.started == []
    assert runner.live_batches() == ()


async def test_second_active_batch_rejected(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    article = _create_article(h.articles)
    await h.runner.start(h.request(article=article, request_id="req-1"))

    with pytest.raises(ActiveBatchConflict) as excinfo:
        await h.runner.start(h.request(article=article, request_id="req-2"))

    assert str(excinfo.value) == (
        f"collaboration already has an active batch for article_id={article['id']}"
    )
    assert excinfo.value.status == 409
    assert len(h.team.started) == 1


async def test_rewrite_selection_requires_a_target(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    article = _create_article(h.articles)

    with pytest.raises(CollabSelectionRequired) as excinfo:
        await h.runner.start(
            h.request(article=article, action=Action.REWRITE_SELECTION, request_id="req-1")
        )

    assert str(excinfo.value) == "collaboration target selection is required"
    assert excinfo.value.status == 400
    assert h.team.started == []

    selection = {"start": 0, "end": 2, "text": "正文"}
    await h.runner.start(
        h.request(
            article=article, action=Action.REWRITE_SELECTION, request_id="req-2", selection=selection
        )
    )
    assert h.team.started[0]["selection"] == selection


async def test_batch_transition_logs_structured_fields_without_article_body(
    tmp_path: Path,
) -> None:
    h = _harness(tmp_path)
    secret = "机密正文-甲乙丙"
    article = _create_article(h.articles, body=secret)
    handle = await h.runner.start(h.request(article=article, instruction="改得更克制"))

    transitions = [
        record
        for record in h.logs.records
        if record.get("event") == "writing_collab_batch_transition"
    ]
    assert transitions
    fields = transitions[0]
    assert fields["collaboration_id"] == handle.collaboration_id
    assert fields["batch_id"] == handle.batch_id
    assert fields["attempt"] == 1
    assert fields["from_status"] == BatchStatus.QUEUED
    assert fields["to_status"] == CollaborationStatus.RUNNING
    assert fields["cancel_generation"] == 0

    dumped = json.dumps(h.logs.records, ensure_ascii=False)
    assert secret not in dumped
    assert "改得更克制" not in dumped

    # 契约 §1：batch.frozen 只留 name/path/sha256，技能正文既不进库也不进日志。
    frozen = h.repo.get_batch(handle.batch_id).frozen
    assert frozen["skills"] == [
        {"name": "article-outline", "path": "skills/article-outline.md", "sha256": "a" * 8}
    ]
    assert set(frozen) == {
        "skills", "model_id", "source_scope", "workspace_key", "article_id",
        "collaboration_id", "batch_id",
    }
    assert "技能正文-机密" not in json.dumps(frozen, ensure_ascii=False)
    # 运行期交给 Team 的冻结上下文才带正文（TeamPort 收到的是完整技能）。
    assert h.team.started[0]["frozen"]["skills"][0]["body"] == "技能正文-机密"


def test_teams_enabled_flag_respected(tmp_path: Path) -> None:
    disabled = _gateway(tmp_path / "ws-disabled", _config_file(tmp_path, enabled=False))
    enabled = _gateway(tmp_path / "ws-enabled", _config_file(tmp_path, enabled=True))

    assert disabled.team_service.enabled is False
    assert enabled.team_service.enabled is True


async def test_default_preparer_runs_real_prepare_frozen(tmp_path: Path) -> None:
    """回归（审查 blocker 1）：默认 ``frozen_preparer`` 必须传满四个身份参数。

    缺陷版本漏传 ``workspace_key/article_id/collaboration_id/batch_id``，真实
    ``prepare_frozen`` 直接 ``TypeError`` —— 默认冻结准备 100% 失败、``writing.start``
    永远起不来；且当时 runner 单测全部注入假准备器，所以无一条用例能发现。此处刻意
    不注入假准备器：走真实 ``prepare_frozen`` + 真实 ``SkillsLoader``。
    """
    h = _harness(tmp_path)
    h.runner = _build_runner(
        h.db, h.repo, h.articles, team=h.team, artifacts=h.artifacts,
        publisher=h.publisher, clock=h.clock, logs=h.logs,
        preparer=frozen_preparer(
            loader=SkillsLoader(tmp_path / "ws"), model_id="openai/gpt-test",
        ),
        recipes=RECIPES,
    )
    article = _create_article(h.articles)

    handle = await h.runner.start(h.request(article=article))

    batch = h.repo.get_batch(handle.batch_id)
    frozen = batch.frozen
    # 批次起跑后停在 queued（成员事件才推进批次行），协作必须已进 running。
    assert batch.status == BatchStatus.QUEUED
    assert (
        h.repo.get_collaboration(handle.collaboration_id).status
        == CollaborationStatus.RUNNING
    )
    assert frozen["workspace_key"] == h.db.workspace_key
    assert frozen["article_id"] == article["id"]
    assert frozen["collaboration_id"] == handle.collaboration_id
    # 技能正文进运行期上下文（TeamPort），落库只留身份与哈希。
    assert frozen["skills"], "真实加载器必须冻结到至少一个写作 Skill"
    assert all(set(skill) == {"name", "path", "sha256"} for skill in frozen["skills"])
    assert frozen["model_id"] == "openai/gpt-test"
    # 批次的 batch_id 在冻结之后才生成：落库那份留空，运行期那份必须补齐。
    assert frozen["batch_id"] == ""
    # 真实准备器返回 ``FrozenContext`` 数据类（假准备器返回 dict），身份由
    # ``_with_identity`` 在运行期补齐。
    runtime_frozen = h.team.started[0]["frozen"]
    assert runtime_frozen.batch_id == handle.batch_id
    assert runtime_frozen.workspace_key == h.db.workspace_key
    assert runtime_frozen.collaboration_id == handle.collaboration_id
    assert runtime_frozen.skills[0].body, "运行期冻结上下文必须带技能正文"


async def test_start_accepts_transport_payload_mapping(tmp_path: Path) -> None:
    """回归（冒烟）：payload 层送**普通 dict**，真执行栈必须按同一份字段名展开。

    ``nanobot/webui/writing_collab_api.py::_start_request`` 刻意不 import runner 的
    领域类型，组装出来的是 dict；缺陷版本 ``CollabRunner.start`` 只做属性访问
    （``request.action``），于是生产 ``writing.start`` 恒抛
    ``AttributeError: 'dict' object has no attribute 'action'``：异常未登记进
    ``translate_error``，被 handler 折成 500 ``writing collaboration request failed``，
    前端再映射成「写作搭档无法处理该请求，请重试。」。失败发生在建协作之前，
    库里不留任何一行 —— 与线上现象（三张协作表全空、state.db mtime 不动）一致。

    当时的盲区：runner 单测一律构造 ``StartCollabRequest``，webui 单测又用同时接受
    映射与对象的宽松假 runner，因此没有一条用例跨过这个接缝。
    """
    from nanobot.webui import writing_collab_api

    h = _harness(tmp_path)
    article = _create_article(h.articles)
    services = writing_collab_api.WritingCollabServices.for_workspace(
        "default", h.db, h.runner,
    )

    result = await writing_collab_api.start_collaboration(
        services,
        client_request_id="req-transport-1",
        article_id=str(article["id"]),
        chat_id="chat-1",
        action=Action.START_FULL,
        base_revision=int(article["revision"]),
        snapshot_digest=_digest(article),
    )

    assert result["collaboration_id"]
    assert result["batch_id"]
    assert result["status"] == CollaborationStatus.RUNNING
    assert h.team.started, "接缝通过后必须真的把批次交给 TeamPort"


def _config_file(tmp_path: Path, *, enabled: bool) -> Path:
    config = Config()
    config.agents.teams.enabled = enabled
    path = tmp_path / f"config-{'on' if enabled else 'off'}.json"
    save_config(config, path)
    return path


def _gateway(workspace: Path, config_path: Path) -> Any:
    return build_gateway_services(
        config=WebSocketConfig(),
        bus=MagicMock(),
        session_manager=None,
        static_dist_path=None,
        workspace_path=workspace,
        default_restrict_to_workspace=False,
        config_path=config_path,
        runtime_model_name=None,
        runtime_surface="browser",
        runtime_capabilities_overrides=None,
    )
