"""协作持久化层单测：契约 §1/§2/§4 的对象、DDL 与错误语义。

覆盖用例：一篇文章一个协作、chat_id 白名单、request_id 重放、活动批次唯一、
版本 CAS、seq 严格递增、产物只追加与摘要稳定、列表投影不含正文、启动收敛、
跨工作区隔离、枚举校验、交接版本 CAS。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from nanobot.writing.collab.database import COLLAB_SCHEMA_STATEMENTS, ensure_collab_schema
from nanobot.writing.collab.models import (
    ACTIONS,
    ACTIVE_BATCH_STATUSES,
    ARTIFACT_KINDS,
    Action,
    ArtifactKind,
    BatchStatus,
    CollaborationStatus,
    InvalidEnumValueError,
    Stage,
)
from nanobot.writing.collab.repository import (
    ActiveBatchConflict,
    ArtifactNotFound,
    CollaborationNotFound,
    CollabRepository,
    CollabVersionConflict,
    InvalidFieldError,
    RequestReplayConflict,
)
from nanobot.writing.database import WritingDatabase

DRAFT = {"body": "第一版", "title": "标题"}


def _chat_ids() -> Iterator[str]:
    counter = 0
    while True:
        counter += 1
        yield f"chat-{counter}"


@dataclass
class _Store:
    db: WritingDatabase
    repo: CollabRepository
    workspace: Path
    workspace_key: str
    next_chat: Iterator[str] = field(default_factory=_chat_ids)

    def collab(
        self,
        *,
        article_id: str = "a-1",
        chat_id: str | None = None,
        request_id: str | None = None,
        goal: dict[str, Any] | None = None,
    ) -> Any:
        chat = chat_id or next(self.next_chat)
        return self.repo.create_collaboration(
            article_id=article_id,
            chat_id=chat,
            session_key=f"websocket:{chat}",
            goal=goal or {},
            request_id=request_id or f"req-collab-{article_id}-{chat}",
        )

    def batch(
        self,
        collaboration_id: str,
        *,
        request_id: str,
        action: str = "start_full",
        expect_version: int | None = None,
        base_revision: int = 1,
    ) -> Any:
        return self.repo.create_batch(
            collaboration_id=collaboration_id,
            action=action,
            base_revision=base_revision,
            snapshot_digest="d-1",
            instruction="写一篇稿子",
            frozen={"workspace_key": self.workspace_key},
            expect_version=expect_version,
            request_id=request_id,
        )


@pytest.fixture
def store(tmp_path: Path) -> _Store:
    return _store(tmp_path, "ws-1")


def _store(tmp_path: Path, name: str) -> _Store:
    workspace = tmp_path / name
    db = WritingDatabase(workspace)
    db.ensure_schema()
    ensure_collab_schema(db)
    return _Store(
        db=db, repo=CollabRepository(db), workspace=workspace, workspace_key=db.workspace_key,
    )


def _tables(db: WritingDatabase) -> set[str]:
    with db.connect() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row["name"] for row in rows}


def _count(db: WritingDatabase, table: str) -> int:
    with db.connect() as conn:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"])


def test_create_collaboration_binds_article_session(store: _Store) -> None:
    collab = store.collab(article_id="a-1", chat_id="chat-1")
    assert collab.article_id == "a-1"
    assert collab.chat_id == "chat-1"
    assert collab.session_key == "websocket:chat-1"
    assert len(collab.session_key) <= 74
    assert collab.status == CollaborationStatus.IDLE
    assert collab.stage == Stage.EMPTY
    assert collab.version == 1

    replay = store.repo.create_collaboration(
        article_id="a-1",
        chat_id="chat-1",
        session_key="websocket:chat-1",
        goal={},
        request_id="req-collab-a-1-chat-1",
    )
    assert replay.id == collab.id
    assert _count(store.db, "writing_collaborations") == 1

    assert store.repo.get_by_article("a-1").id == collab.id
    assert store.repo.get_by_article("missing") is None
    assert store.repo.get_collaboration(collab.id).id == collab.id
    with pytest.raises(CollaborationNotFound):
        store.repo.get_collaboration("nope")

    with pytest.raises(RequestReplayConflict):
        store.collab(
            article_id="a-1", chat_id="chat-1", request_id="req-collab-a-1-chat-1",
            goal={"action": "ideate"},
        )

    other = store.collab(article_id="a-2", chat_id="chat-2", request_id="req-2")
    assert other.id != collab.id
    assert store.repo.get_by_article("a-2").chat_id == "chat-2"


def test_invalid_chat_id_rejected(store: _Store) -> None:
    for index, bad in enumerate(("", "chat 1", "聊天", "a" * 65, "chat/1", "chat#1")):
        with pytest.raises(InvalidFieldError) as error:
            store.repo.create_collaboration(
                article_id="a-1",
                chat_id=bad,
                session_key=f"websocket:{bad}",
                goal={},
                request_id=f"req-bad-{index}",
            )
        assert "chat_id" in str(error.value)
        assert bad in str(error.value)

    for index, session_key in enumerate(
        ("chat-1", "telegram:chat-1", "websocket:", f"websocket:{'a' * 65}")
    ):
        with pytest.raises(InvalidFieldError) as error:
            store.repo.create_collaboration(
                article_id="a-1",
                chat_id="chat-1",
                session_key=session_key,
                goal={},
                request_id=f"req-session-{index}",
            )
        assert "session_key" in str(error.value)

    assert store.repo.get_by_article("a-1") is None
    assert _count(store.db, "writing_collaborations") == 0


def test_same_request_id_replays_without_second_batch(store: _Store) -> None:
    collab = store.collab()
    first = store.batch(collab.id, request_id="req-1")
    assert first.attempt == 1
    assert first.status == BatchStatus.QUEUED

    replay = store.repo.create_batch(
        collaboration_id=collab.id,
        action="start_full",
        base_revision=1,
        snapshot_digest="d-1",
        instruction="写一篇稿子",
        frozen={"workspace_key": store.workspace_key},
        expect_version=None,
        request_id="req-1",
    )
    assert replay.id == first.id
    assert replay.attempt == first.attempt
    assert _count(store.db, "writing_collab_batches") == 1

    finished = store.repo.finish_batch(batch_id=first.id, status=BatchStatus.COMPLETED)
    assert finished.status == BatchStatus.COMPLETED
    assert store.repo.get_batch(first.id).status == BatchStatus.COMPLETED
    assert store.repo.get_batch(first.id).finished_at

    with pytest.raises(RequestReplayConflict):
        store.batch(collab.id, request_id="req-1", action="outline")


def test_active_batch_uniqueness(store: _Store) -> None:
    collab = store.collab()
    first = store.batch(collab.id, request_id="req-1")
    assert store.repo.get_collaboration(collab.id).active_batch_id == first.id

    with pytest.raises(ActiveBatchConflict) as error:
        store.batch(collab.id, request_id="req-2")
    assert error.value.status == 409
    assert str(error.value) == "collaboration already has an active batch for article_id=a-1"

    store.repo.finish_batch(batch_id=first.id, status=BatchStatus.COMPLETED)
    second = store.batch(collab.id, request_id="req-2")
    assert second.id != first.id
    assert second.attempt == 2
    assert _count(store.db, "writing_collab_batches") == 2

    store.repo.finish_batch(batch_id=second.id, status=BatchStatus.COMPLETED)
    third = store.batch(collab.id, request_id="req-3")
    assert third.attempt == 3
    store.repo.finish_batch(batch_id=third.id, status=BatchStatus.WAITING_AUTHOR)
    assert store.repo.get_batch(third.id).status == BatchStatus.WAITING_AUTHOR
    assert store.repo.get_collaboration(collab.id).status == CollaborationStatus.WAITING_AUTHOR

    with pytest.raises(ActiveBatchConflict):
        store.batch(collab.id, request_id="req-4")


def test_version_conflict(store: _Store) -> None:
    collab = store.collab()
    with pytest.raises(CollabVersionConflict) as error:
        store.repo.set_collaboration_status(
            collaboration_id=collab.id,
            status=CollaborationStatus.RUNNING,
            expect_version=99,
        )
    assert error.value.status == 409
    assert "expected=99" in str(error.value)
    assert f"actual={collab.version}" in str(error.value)

    with pytest.raises(CollabVersionConflict) as error:
        store.batch(collab.id, request_id="req-1", expect_version=99)
    assert "expected=99" in str(error.value)
    assert _count(store.db, "writing_collab_batches") == 0

    updated = store.repo.set_collaboration_status(
        collaboration_id=collab.id,
        status=CollaborationStatus.RUNNING,
        expect_version=collab.version,
    )
    assert updated.status == CollaborationStatus.RUNNING
    assert updated.version == collab.version + 1

    with pytest.raises(CollabVersionConflict):
        store.repo.set_collaboration_status(
            collaboration_id=collab.id, status=CollaborationStatus.CANCELLED, expect_version=1,
        )


def test_advance_seq_monotonic(store: _Store) -> None:
    collab = store.collab()
    assert collab.seq == 0
    assert store.repo.get_collaboration(collab.id).seq == 0

    first = store.repo.advance_seq(collab.id)
    second = store.repo.advance_seq(collab.id)
    assert first == 1
    assert second == 2

    store.batch(collab.id, request_id="req-1")
    third = store.repo.advance_seq(collab.id)
    assert third == 3
    assert store.repo.get_collaboration(collab.id).seq == 3

    with pytest.raises(CollaborationNotFound):
        store.repo.advance_seq("missing")


def test_artifact_append_only(store: _Store) -> None:
    collab = store.collab()
    batch = store.batch(collab.id, request_id="req-1")

    first = store.repo.add_artifact(
        collaboration_id=collab.id,
        batch_id=batch.id,
        attempt=batch.attempt,
        kind=ArtifactKind.DRAFT,
        role_id="writing-author",
        content=dict(DRAFT),
        source_ids=["m-1"],
        gaps=["缺少数据"],
    )
    assert first.version == 1
    assert first.content_digest

    second = store.repo.add_artifact(
        collaboration_id=collab.id,
        batch_id=batch.id,
        attempt=batch.attempt,
        kind="draft",
        role_id="writing-author",
        content={"body": "第二版", "title": "标题"},
        source_ids=[],
        gaps=[],
    )
    assert second.version == 2
    assert second.id != first.id
    stored_first = store.repo.get_artifact(first.id)
    assert stored_first.content["body"] == "第一版"
    assert stored_first.version == 1
    assert set(store.repo.list_artifacts(collab.id)) == {first.id, second.id}
    assert _count(store.db, "writing_collab_artifacts") == 2

    other = store.collab(article_id="a-2", chat_id="chat-2", request_id="req-2")
    store.batch(other.id, request_id="req-3")
    other_batch = store.repo.list_batches(other.id)[0]
    same_content = store.repo.add_artifact(
        collaboration_id=other.id,
        batch_id=other_batch.id,
        attempt=1,
        kind=ArtifactKind.DRAFT,
        role_id="writing-author",
        content=dict(DRAFT),
        source_ids=[],
        gaps=[],
    )
    assert same_content.content_digest == first.content_digest
    assert same_content.version == 1

    different = store.repo.add_artifact(
        collaboration_id=other.id,
        batch_id=other_batch.id,
        attempt=1,
        kind=ArtifactKind.DRAFT,
        role_id="writing-author",
        content={"body": "另一版", "title": "标题"},
        source_ids=[],
        gaps=[],
    )
    assert different.content_digest != first.content_digest
    assert different.version == 2

    with pytest.raises(ArtifactNotFound):
        store.repo.get_artifact("nope")


def test_snapshot_projection_excludes_full_body(store: _Store) -> None:
    collab = store.collab()
    batch = store.batch(collab.id, request_id="req-1")
    secret = "只有详情接口才返回的正文" * 5
    artifact = store.repo.add_artifact(
        collaboration_id=collab.id,
        batch_id=batch.id,
        attempt=1,
        kind=ArtifactKind.OUTLINE,
        role_id="writing-architect",
        content={"body": secret, "items": [1, 2, 3]},
        source_ids=[],
        gaps=[],
    )

    snap = store.repo.snapshot("a-1")
    assert snap.collaboration is not None
    assert snap.collaboration.id == collab.id
    live = store.repo.get_collaboration(collab.id)
    assert snap.last_version == live.version
    assert snap.last_seq == live.seq
    assert [item["id"] for item in snap.artifacts] == [artifact.id]
    listing = snap.artifacts[0]
    assert listing["kind"] == ArtifactKind.OUTLINE
    assert listing["version"] == 1
    assert "content" not in listing
    assert "content_json" not in listing
    assert secret not in json.dumps(snap.to_dict(), ensure_ascii=False)

    listing_blob = store.repo.list_artifacts(collab.id)[artifact.id]
    assert "content_json" not in listing_blob
    assert secret not in json.dumps(listing_blob, ensure_ascii=False)

    detail = store.repo.get_artifact(artifact.id)
    assert detail.content["body"] == secret
    assert secret in json.dumps(detail.to_dict(), ensure_ascii=False)

    assert store.repo.snapshot("missing").collaboration is None


def test_converge_interrupted_marks_inflight(store: _Store) -> None:
    collab = store.collab()
    batch = store.batch(collab.id, request_id="req-1")
    artifact = store.repo.add_artifact(
        collaboration_id=collab.id,
        batch_id=batch.id,
        attempt=1,
        kind=ArtifactKind.BRIEF,
        role_id="writing-editor",
        content={"body": "已交付的产物"},
        source_ids=[],
        gaps=[],
    )
    store.repo.finish_batch(batch_id=batch.id, status=BatchStatus.COMPLETED)

    inflight = store.batch(collab.id, request_id="req-2")
    assert store.repo.get_collaboration(collab.id).status == CollaborationStatus.QUEUED

    assert store.repo.converge_interrupted("f" * 16) == 0
    assert store.repo.get_batch(inflight.id).status == BatchStatus.QUEUED

    marked = store.repo.converge_interrupted(store.workspace_key)
    assert marked == 1
    assert store.repo.get_batch(inflight.id).status == BatchStatus.INTERRUPTED
    assert store.repo.get_batch(batch.id).status == BatchStatus.COMPLETED
    assert store.repo.get_collaboration(collab.id).status == CollaborationStatus.INTERRUPTED
    assert store.repo.get_artifact(artifact.id).content["body"] == "已交付的产物"

    assert store.repo.converge_interrupted(store.workspace_key) == 0
    assert store.repo.get_batch(inflight.id).status == BatchStatus.INTERRUPTED


def test_cross_workspace_isolation(tmp_path: Path) -> None:
    left = _store(tmp_path, "ws-left")
    right = _store(tmp_path, "ws-right")
    assert left.workspace_key != right.workspace_key

    collab = left.collab(article_id="a-1")
    batch = left.batch(collab.id, request_id="req-1")
    artifact = left.repo.add_artifact(
        collaboration_id=collab.id,
        batch_id=batch.id,
        attempt=1,
        kind=ArtifactKind.DRAFT,
        role_id="writing-author",
        content={"body": "只属于左工作区"},
        source_ids=[],
        gaps=[],
    )

    assert right.repo.get_by_article("a-1") is None
    with pytest.raises(CollaborationNotFound):
        right.repo.get_collaboration(collab.id)
    with pytest.raises(ArtifactNotFound):
        right.repo.get_artifact(artifact.id)
    assert right.repo.list_artifacts(collab.id) == {}
    assert right.repo.converge_interrupted(right.workspace_key) == 0
    assert _count(left.db, "writing_collaborations") == 1
    assert _count(right.db, "writing_collaborations") == 0
    assert left.repo.get_artifact(artifact.id).content["body"] == "只属于左工作区"


def test_enum_validation(store: _Store) -> None:
    with pytest.raises(InvalidEnumValueError) as error:
        store.repo.create_collaboration(
            article_id="a-1", chat_id="chat-1", session_key="websocket:chat-1",
            goal={"action": "bogus"}, request_id="req-enum-goal",
        )
    assert "action" in str(error.value)
    assert "bogus" in str(error.value)

    collab = store.collab(chat_id="chat-2", request_id="req-enum-collab")

    with pytest.raises(InvalidEnumValueError) as error:
        store.repo.set_collaboration_status(
            collaboration_id=collab.id, status="bogus", expect_version=None,
        )
    assert "status" in str(error.value)
    assert "bogus" in str(error.value)

    with pytest.raises(InvalidEnumValueError) as error:
        store.repo.set_collaboration_status(
            collaboration_id=collab.id, status=CollaborationStatus.RUNNING, stage="bogus",
            expect_version=None,
        )
    assert "stage" in str(error.value)
    assert store.repo.get_collaboration(collab.id).status == CollaborationStatus.IDLE

    with pytest.raises(InvalidEnumValueError) as error:
        store.batch(collab.id, request_id="req-1", action="bogus")
    assert "action" in str(error.value)
    assert "bogus" in str(error.value)

    batch = store.batch(collab.id, request_id="req-2")
    with pytest.raises(InvalidEnumValueError) as error:
        store.repo.add_artifact(
            collaboration_id=collab.id, batch_id=batch.id, attempt=1, kind="bogus",
            role_id="writing-author", content={}, source_ids=[], gaps=[],
        )
    assert "kind" in str(error.value)

    with pytest.raises(InvalidEnumValueError) as error:
        store.repo.add_artifact(
            collaboration_id=collab.id, batch_id=batch.id, attempt=1, kind="draft",
            role_id="bogus", content={}, source_ids=[], gaps=[],
        )
    assert "role_id" in str(error.value)

    with pytest.raises(InvalidEnumValueError) as error:
        store.repo.finish_batch(batch_id=batch.id, status="bogus")
    assert "status" in str(error.value)


def test_handoff_version_cas(store: _Store) -> None:
    collab = store.collab()
    batch = store.batch(collab.id, request_id="req-1")
    artifact = store.repo.add_artifact(
        collaboration_id=collab.id,
        batch_id=batch.id,
        attempt=1,
        kind=ArtifactKind.OUTLINE,
        role_id="writing-architect",
        content={"body": "提纲"},
        source_ids=[],
        gaps=[],
    )
    handoff = store.repo.add_handoff(
        collaboration_id=collab.id,
        batch_id=batch.id,
        artifact_id=artifact.id,
        kind="outline_confirm",
        artifact_version=artifact.version,
    )
    assert handoff.status == "pending"
    assert handoff.artifact_version == artifact.version
    assert store.repo.get_collaboration(collab.id).last_handoff_id == handoff.id
    assert store.repo.list_handoffs(collab.id)[0].id == handoff.id

    with pytest.raises(CollabVersionConflict) as error:
        store.repo.resolve_handoff(
            handoff_id=handoff.id, expected_version="confirmed", status="confirmed",
        )
    assert str(error.value) == "handoff version conflict expected=confirmed actual=pending"
    assert store.repo.get_handoff(handoff.id).status == "pending"

    resolved = store.repo.resolve_handoff(
        handoff_id=handoff.id, expected_version=handoff.status, status="confirmed",
    )
    assert resolved.status == "confirmed"
    assert resolved.resolved_at

    with pytest.raises(InvalidEnumValueError):
        store.repo.add_handoff(
            collaboration_id=collab.id, batch_id=batch.id, artifact_id=artifact.id,
            kind="bogus", artifact_version=1,
        )


def test_schema_statements_are_collab_only(tmp_path: Path) -> None:
    db = WritingDatabase(tmp_path / "ws")
    db.ensure_schema()
    ensure_collab_schema(db)
    ensure_collab_schema(db)
    for statement in COLLAB_SCHEMA_STATEMENTS:
        assert statement.lstrip().startswith(("CREATE TABLE", "CREATE INDEX"))
        assert "DROP" not in statement.upper()
        assert "ALTER" not in statement.upper()
    # 内置资产表属 writing/database.py（WU-03），协作层 DDL 不得越界建它
    assert "writing_builtin_assets" not in " ".join(COLLAB_SCHEMA_STATEMENTS)
    assert {
        "writing_collaborations",
        "writing_collab_batches",
        "writing_collab_artifacts",
        "writing_collab_handoffs",
        "writing_collab_suggestions",
        "writing_collab_undo_entries",
    } <= _tables(db)


def test_models_enums_are_frozen() -> None:
    assert ACTIVE_BATCH_STATUSES == ("queued", "running", "waiting_author")
    assert ACTIONS == (
        "start_full", "ideate", "research", "outline", "draft",
        "rewrite_selection", "review_all", "reply",
    )
    assert tuple(ARTIFACT_KINDS) == (
        "brief", "material_list", "outline", "draft", "rewrite", "review",
    )
    assert Action.START_FULL == "start_full"
    assert BatchStatus.WAITING_AUTHOR == "waiting_author"
