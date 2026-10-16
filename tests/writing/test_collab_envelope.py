"""WU-07 / plan Task 7：成员产物 envelope 解析、校验与完整长稿落库。

覆盖三件事：

1. envelope 的**解析/校验**口径（角色→产物类型白名单、身份字段由服务端补齐、
   来源 id 必须真实存在、超限固定 message、坏输出只作诊断）；
2. **完整长稿落库**：正文逐字进 ``content_json``，不受 Team 侧 8000 字符摘要上限影响，
   列表投影不含正文、详情投影才含；
3. 支撑完整落库的两个 Team 侧最小改动：``TeamRepository.get_task_full_result``（读回
   ``fullResult``）与 ``finish_task`` 的 ``cancelling`` 晚到成功守卫。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from nanobot.teams.database import TeamDatabase
from nanobot.teams.models import (
    AgentProfileSnapshot,
    TaskEventData,
    TeamBinding,
    TeamDefinition,
    TeamEvent,
    TeamLimits,
    TeamMember,
    TeamRevisionMember,
    TeamRevisionSnapshot,
    TeamTaskResult,
    TeamTaskSnapshot,
)
from nanobot.teams.repository import IllegalTaskTransition, TeamRepository
from nanobot.webui.session_identity import WEBUI_SESSION_STORAGE_PREFIX
from nanobot.writing.collab.envelope import (
    ENVELOPE_TOO_LARGE_MESSAGE,
    EnvelopeArtifactSink,
    EnvelopeError,
    parse_envelope,
    validate_envelope,
)
from nanobot.writing.collab.repository import CollabRepository
from nanobot.writing.database import WritingDatabase
from nanobot.writing.repository import ArticleRepository

_NOW = "2026-10-17T00:00:00Z"


# ---- Team 侧装置 -------------------------------------------------------------


def _profile(agent_id: str) -> AgentProfileSnapshot:
    return AgentProfileSnapshot.from_dict(
        {
            "id": agent_id,
            "updatedAt": _NOW,
            "prompt": "",
            "name": agent_id,
            "modelId": None,
            "tools": {"mode": "all", "entries": []},
            "skills": {"mode": "all", "entries": []},
            "subAgents": {"mode": "all", "entries": []},
        }
    )


_MEMBERS = (
    "writing-researcher",
    "writing-architect",
    "writing-author",
    "writing-reviewer",
)


def _definition() -> TeamDefinition:
    return TeamDefinition(
        id="writing-team",
        revision=1,
        name="创作团队",
        description="",
        instructions="",
        leader_agent_id="writing-editor",
        members=[
            TeamMember(member_id=role, agent_id=role, role=role) for role in _MEMBERS
        ],
        limits=TeamLimits(),
        archived=False,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _snapshot(definition: TeamDefinition) -> TeamRevisionSnapshot:
    return TeamRevisionSnapshot(
        definition=definition,
        leader=_profile(definition.leader_agent_id),
        members=[
            TeamRevisionMember(member=member, profile=_profile(member.agent_id))
            for member in definition.members
        ],
    )


@dataclass
class _TeamStore:
    repository: TeamRepository
    definition: TeamDefinition
    snapshot: TeamRevisionSnapshot
    run_id: str

    def finish_long_task(self, *, summary: str, full: str | None, member_id: str) -> str:
        """落一条「摘要被截断、全文另存」的任务，返回 task_id。"""
        member = next(item for item in self.definition.members if item.member_id == member_id)
        task = self.repository.create_task(
            run_id=self.run_id,
            member=member,
            title="交付",
            acceptance="交付",
            instruction="写",
            status="running",
        )
        self.repository.finish_task(
            task.task_id,
            status="succeeded",
            summary=summary,
            full_result=full,
        )
        return task.task_id


def _team_store(tmp_path: Path) -> _TeamStore:
    db = TeamDatabase(tmp_path / "team-ws")
    db.init_schema()
    repository = TeamRepository(db)
    definition = _definition()
    snapshot = _snapshot(definition)
    repository.save_team(
        definition=definition,
        snapshot=snapshot,
        expected_revision=None,
        request_id="seed-team",
    )
    run = repository.create_run(
        binding=TeamBinding(
            team_id=definition.id,
            team_revision=definition.revision,
            team_name=definition.name,
        ),
        chat_id="chat-1",
        session_key=f"{WEBUI_SESSION_STORAGE_PREFIX}chat-1",
        turn_id="turn-1",
        internal_turn_id="turn-1",
        snapshot=snapshot,
    )
    return _TeamStore(
        repository=repository, definition=definition, snapshot=snapshot, run_id=run.run_id
    )


# ---- 写作侧装置 ---------------------------------------------------------------


@dataclass
class _WritingStore:
    database: WritingDatabase
    repository: CollabRepository
    articles: ArticleRepository
    article_id: str
    collaboration_id: str
    batch_id: str

    def add_material(self, material_id: str) -> None:
        payload = self.articles.get_article(self.article_id)["article"]
        materials = list(payload.get("materials") or [])
        materials.append({"id": material_id, "title": material_id, "content": "素材"})
        self.articles.save_snapshot(
            self.article_id,
            int(payload["revision"]),
            {**payload, "materials": materials},
            client_request_id=f"material-{material_id}",
        )


def _writing_store(tmp_path: Path, *, action: str = "outline") -> _WritingStore:
    database = WritingDatabase(tmp_path / "ws")
    repository = CollabRepository(database)
    articles = ArticleRepository(database)
    article = articles.create_article("req-article-1", {"title": "标题", "body": "正文"})
    article_id = str(article["article"]["id"])
    collaboration = repository.create_collaboration(
        article_id=article_id,
        chat_id="chat-1",
        session_key=f"{WEBUI_SESSION_STORAGE_PREFIX}chat-1",
        goal={"action": action},
        request_id="req-collab-1",
    )
    batch = repository.create_batch(
        collaboration_id=collaboration.id,
        action=action,
        base_revision=int(article["article"]["revision"]),
        snapshot_digest="digest-1",
        request_id="req-batch-1",
    )
    return _WritingStore(
        database=database,
        repository=repository,
        articles=articles,
        article_id=article_id,
        collaboration_id=collaboration.id,
        batch_id=batch.id,
    )


def _event(
    *,
    task_id: str,
    member_id: str = "writing-architect",
    status: str = "succeeded",
    summary: str = "ok",
    run_id: str = "run-placeholder",
) -> TeamEvent:
    snapshot = TeamTaskSnapshot(
        task_id=task_id,
        member_id=member_id,
        agent_id=member_id,
        title="交付",
        acceptance="交付",
        depends_on=[],
        status=status,  # pyright: ignore[reportArgumentType] 测试只喂契约白名单取值
        attempt_id="att-1",
        result=TeamTaskResult(
            task_id=task_id,
            attempt_id="att-1",
            member_id=member_id,
            agent_id=member_id,
            status=status,  # pyright: ignore[reportArgumentType]
            stop_reason="completed" if status == "succeeded" else "member_failed",
            summary=summary,
            artifacts=[],
            error_code=None if status == "succeeded" else "member_failed",
        ),
    )
    return TeamEvent(
        schema_version=1,
        run_id=run_id,
        chat_id="chat-1",
        turn_id="turn-1",
        seq=1,
        type="task.finished",
        occurred_at=_NOW,
        data=TaskEventData(task=snapshot),
        task_id=task_id,
    )


@dataclass
class _SinkHarness:
    writing: _WritingStore
    teams: _TeamStore
    sink: EnvelopeArtifactSink
    logs: "list[dict[str, Any]]"

    def ingest(
        self,
        *,
        summary: str,
        full: str | None,
        member_id: str = "writing-architect",
    ) -> Any:
        task_id = self.teams.finish_long_task(
            summary=summary, full=full, member_id=member_id
        )
        return self.sink.ingest(
            collaboration_id=self.writing.collaboration_id,
            batch_id=self.writing.batch_id,
            attempt=1,
            event=_event(task_id=task_id, member_id=member_id, summary=summary),
        )


class _Recorder:
    """结构化日志替身：只留 ``bind`` 字段与 message。"""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def bind(self, **fields: Any) -> "_Recorder":
        bound = _Recorder()
        bound.records = self.records
        bound._fields = fields  # noqa: SLF001 - 测试替身内部记账
        return bound

    _fields: dict[str, Any] = {}

    def _emit(self, message: str) -> None:
        self.records.append({**self._fields, "message": message})

    info = _emit
    warning = _emit
    debug = _emit
    exception = _emit


def _sink_harness(tmp_path: Path, *, action: str = "outline") -> _SinkHarness:
    writing = _writing_store(tmp_path, action=action)
    teams = _team_store(tmp_path)
    logs: list[dict[str, Any]] = []
    recorder = _Recorder()
    recorder.records = logs
    sink = EnvelopeArtifactSink(
        repository=writing.repository,
        teams=teams.repository,
        articles=writing.articles,
        workspace_key=writing.database.workspace_key,
        logger=recorder,
    )
    return _SinkHarness(writing=writing, teams=teams, sink=sink, logs=logs)


# ---- envelope 文本 -----------------------------------------------------------


def _wire(payload: Mapping[str, Any], *, fenced: bool = True) -> str:
    body = json.dumps(payload, ensure_ascii=False)
    if not fenced:
        # 成员偶尔漏掉代码块，整段就是一个裸 JSON：这种仍然认。
        return body
    return f"先说明思路。\n\n```writing-envelope\n{body}\n```\n\n收尾。"


def _outline_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": "outline",
        "roleId": "writing-architect",
        "summary": "三段式提纲",
        "sections": [
            {"id": "s1", "title": "引子", "text": "第一段正文"},
            {"id": "s2", "title": "论证", "text": "第二段正文"},
        ],
        "sourceIds": [],
        "gaps": [],
    }
    payload.update(overrides)
    return payload


# ---- 解析与校验 --------------------------------------------------------------


def test_envelope_accepts_allowed_kinds_per_role() -> None:
    good = parse_envelope(_wire(_outline_payload()))
    assert good is not None
    assert validate_envelope(good, action="outline") is good

    wrong_role = parse_envelope(
        _wire(_outline_payload(roleId="writing-author"))
    )
    assert wrong_role is not None
    with pytest.raises(EnvelopeError) as excinfo:
        validate_envelope(wrong_role, action="outline")
    assert "writing-author" in str(excinfo.value)

    wrong_kind = parse_envelope(_wire(_outline_payload(kind="draft")))
    assert wrong_kind is not None
    with pytest.raises(EnvelopeError):
        validate_envelope(wrong_kind, action="outline")

    # 动作白名单之外的产物类型（配方 artifact_kinds）同样拒绝。
    review_for_outline = parse_envelope(
        _wire(_outline_payload(kind="review", roleId="writing-reviewer"))
    )
    assert review_for_outline is not None
    with pytest.raises(EnvelopeError):
        validate_envelope(review_for_outline, action="outline")


def test_envelope_rejects_identity_fields() -> None:
    """客户端自称的身份字段一律丢弃，由服务端补齐（契约 §1）。"""
    payload = _outline_payload(
        id="attacker-artifact-id",
        artifactId="attacker-artifact-id",
        articleId="other-article",
        runId="other-run",
        version=99,
        revision=42,
        digest="deadbeef",
        contentDigest="deadbeef",
    )
    envelope = parse_envelope(_wire(payload))
    assert envelope is not None
    assert envelope.kind == "outline"
    dumped = json.dumps(envelope.content(), ensure_ascii=False)
    for forbidden in ("attacker-artifact-id", "other-article", "other-run", "deadbeef"):
        assert forbidden not in dumped
    assert "99" not in envelope.content().get("version", "")


def test_envelope_rejects_unknown_source_ids() -> None:
    envelope = parse_envelope(_wire(_outline_payload(sourceIds=["mat-1"])))
    assert envelope is not None
    with pytest.raises(EnvelopeError) as excinfo:
        validate_envelope(envelope, action="outline", known_source_ids=set())
    assert "mat-1" in str(excinfo.value)

    assert validate_envelope(
        envelope, action="outline", known_source_ids={"mat-1"}
    ) is envelope


def test_envelope_length_limits_use_fixed_message() -> None:
    oversized = _outline_payload(
        sections=[{"id": "s1", "text": "字" * 40_000}]
    )
    envelope = parse_envelope(_wire(oversized))
    assert envelope is not None
    with pytest.raises(EnvelopeError) as excinfo:
        validate_envelope(envelope, action="outline")
    assert str(excinfo.value) == ENVELOPE_TOO_LARGE_MESSAGE
    # 不静默裁切：超限输入没有被改小后放行。
    assert len(envelope.body_text()) == 40_000


def test_malformed_output_is_not_an_envelope() -> None:
    assert parse_envelope("成员只说了人话，没有 JSON。") is None
    assert parse_envelope("```writing-envelope\n{不是 JSON}\n```") is None
    assert parse_envelope(_wire({"kind": "outline"})) is None
    assert parse_envelope(_wire(_outline_payload(kind="poem"))) is None
    assert parse_envelope(_wire(_outline_payload(roleId="writing-poet"))) is None
    # 未围栏的裸 JSON 也认（成员偶尔漏掉代码块）；带散文前缀又没围栏的则要求修正。
    bare = parse_envelope(_wire(_outline_payload(), fenced=False))
    assert bare is not None and bare.kind == "outline"
    assert parse_envelope("说明文字\n" + json.dumps(_outline_payload(), ensure_ascii=False)) is None


# ---- 落库：完整正文 ----------------------------------------------------------


def test_artifact_persists_full_content_not_summary(tmp_path: Path) -> None:
    harness = _sink_harness(tmp_path)
    long_body = "长" * 12_000
    wire = _wire(_outline_payload(sections=[{"id": "s1", "title": "全文", "text": long_body}]))
    task_id = harness.teams.finish_long_task(
        summary=wire[:8000], full=wire, member_id="writing-architect"
    )

    artifact = harness.sink.ingest(
        collaboration_id=harness.writing.collaboration_id,
        batch_id=harness.writing.batch_id,
        attempt=1,
        event=_event(task_id=task_id, summary=wire[:8000]),
    )

    assert artifact is not None
    assert artifact.kind == "outline"
    assert artifact.role_id == "writing-architect"
    # 返回给 Team 侧的只有摘要：正文绝不回灌事件负载。
    assert len(artifact.summary) <= 2_000
    assert long_body not in artifact.summary

    stored = harness.writing.repository.get_artifact(artifact.id)
    assert stored.content["body"] == long_body
    assert len(stored.content["body"]) == 12_000
    assert stored.content_digest == artifact.content_digest

    # 列表投影不含正文，详情投影才含（契约 §3）。
    listing = harness.writing.repository.list_artifacts(harness.writing.collaboration_id)
    assert "content" not in listing[artifact.id]
    assert "content" in harness.writing.repository.get_artifact(artifact.id).to_dict()


def test_unknown_envelope_is_diagnostic_only(tmp_path: Path) -> None:
    harness = _sink_harness(tmp_path)
    task_id = harness.teams.finish_long_task(
        summary="我写完了但忘了格式", full="我写完了但忘了格式", member_id="writing-architect"
    )
    result = harness.sink.ingest(
        collaboration_id=harness.writing.collaboration_id,
        batch_id=harness.writing.batch_id,
        attempt=1,
        event=_event(task_id=task_id, summary="我写完了但忘了格式"),
    )
    assert result is None
    assert harness.writing.repository.list_artifacts(harness.writing.collaboration_id) == {}
    assert any(
        record.get("event") == "writing_collab_envelope_unparsed"
        for record in harness.logs
    )


def test_ingest_never_raises_on_unknown_event_shape(tmp_path: Path) -> None:
    harness = _sink_harness(tmp_path)

    @dataclass
    class _Weird:
        run_id: str = "run-1"
        type: str = "task.finished"
        data: Any = None

    assert (
        harness.sink.ingest(
            collaboration_id="missing",
            batch_id="missing",
            attempt=1,
            event=_Weird(),
        )
        is None
    )
    assert (
        harness.sink.ingest(
            collaboration_id="missing",
            batch_id="missing",
            attempt=1,
            event={"runId": "run-1", "type": "task.finished"},
        )
        is None
    )
    assert any(
        record.get("event") == "writing_collab_envelope_ingest_failed"
        or record.get("event") == "writing_collab_envelope_no_task"
        for record in harness.logs
    )


def test_failed_task_result_is_not_adopted(tmp_path: Path) -> None:
    harness = _sink_harness(tmp_path)
    task_id = harness.teams.finish_long_task(
        summary="成员崩了", full="成员崩了", member_id="writing-architect"
    )
    result = harness.sink.ingest(
        collaboration_id=harness.writing.collaboration_id,
        batch_id=harness.writing.batch_id,
        attempt=1,
        event=_event(task_id=task_id, status="failed", summary="成员崩了"),
    )
    assert result is None


def test_unknown_source_ids_do_not_create_artifact(tmp_path: Path) -> None:
    harness = _sink_harness(tmp_path)
    wire = _wire(_outline_payload(sourceIds=["mat-not-exist"]))
    task_id = harness.teams.finish_long_task(
        summary=wire[:200], full=wire, member_id="writing-architect"
    )
    assert (
        harness.sink.ingest(
            collaboration_id=harness.writing.collaboration_id,
            batch_id=harness.writing.batch_id,
            attempt=1,
            event=_event(task_id=task_id),
        )
        is None
    )
    assert harness.writing.repository.list_artifacts(harness.writing.collaboration_id) == {}


def test_known_article_material_is_accepted_as_source(tmp_path: Path) -> None:
    harness = _sink_harness(tmp_path)
    harness.writing.add_material("mat-1")
    wire = _wire(_outline_payload(sourceIds=["mat-1"]))
    task_id = harness.teams.finish_long_task(
        summary=wire[:200], full=wire, member_id="writing-architect"
    )
    artifact = harness.sink.ingest(
        collaboration_id=harness.writing.collaboration_id,
        batch_id=harness.writing.batch_id,
        attempt=1,
        event=_event(task_id=task_id),
    )
    assert artifact is not None
    assert artifact.source_ids == ["mat-1"]


# ---- Team 侧最小改动 ---------------------------------------------------------


def test_full_result_readable_for_writing_adapter(tmp_path: Path) -> None:
    teams = _team_store(tmp_path)
    full = "全文" * 5_000
    task_id = teams.finish_long_task(summary=full[:8_000], full=full, member_id="writing-architect")

    assert teams.repository.get_task_full_result(task_id) == full
    snapshot = teams.repository.get_task(task_id)
    assert snapshot.result is not None
    # 通用摘要语义不变：摘要是被截断的那一份，只有写作适配才去取全文。
    assert snapshot.result.summary == full[:8_000]
    assert len(snapshot.result.summary) == 8_000

    # 短结果不额外存全文：读回 None，调用方回落到 summary。
    short_id = teams.finish_long_task(summary="短", full=None, member_id="writing-author")
    assert teams.repository.get_task_full_result(short_id) is None


def test_finish_task_rejects_late_success_after_cancelling(tmp_path: Path) -> None:
    teams = _team_store(tmp_path)
    member = teams.definition.members[0]
    task = teams.repository.create_task(
        run_id=teams.run_id,
        member=member,
        title="交付",
        acceptance="交付",
        instruction="写",
        status="running",
    )
    teams.repository.update_task_status(task.task_id, "cancelling")

    with pytest.raises(IllegalTaskTransition):
        teams.repository.finish_task(
            task.task_id, status="succeeded", summary="晚到的成功"
        )

    snapshot = teams.repository.get_task(task.task_id)
    assert snapshot.status == "cancelling"
    assert snapshot.result is None

    # 取消链仍然能正常收口。
    teams.repository.finish_task(task.task_id, status="cancelled", summary="已停止")
    assert teams.repository.get_task(task.task_id).status == "cancelled"
