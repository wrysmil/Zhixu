"""团队存储层行为测试（plan Task 2）。

覆盖 plan Step 1 列举的六项验收，以及 Step 4 要求补的异常路径：事务中断回滚、
损坏 JSON、DB 锁超时、第二 writer、workspace 越界。锚点测试逐字来自 plan Task 2。

全部用 ``tmp_path`` 里的真实 SQLite 文件，不 mock 数据库；不涉及模型与网络。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from nanobot.teams.database import SCHEMA_VERSION, TeamDatabase
from nanobot.teams.models import TeamBinding, TeamDefinition, TeamValidationError
from nanobot.teams.repository import (
    DispatchKeyConflict,
    RequestReplayConflict,
    RunNotFound,
    StorageUnavailable,
    TeamNotFound,
    TeamRepository,
    TeamRevisionConflict,
)

# ------------------------------------------------------ 读路径不落盘（Task 2 Step 1）


def test_reading_an_empty_workspace_creates_no_directory(team_db_uninitialized: TeamDatabase) -> None:
    repo = TeamRepository(team_db_uninitialized)
    assert repo.list_teams(limit=20)["teams"] == []
    assert not team_db_uninitialized.db_path.parent.exists()
    assert not team_db_uninitialized.db_path.exists()


def test_get_team_on_missing_database_raises_without_creating_it(
    team_db_uninitialized: TeamDatabase,
) -> None:
    repo = TeamRepository(team_db_uninitialized)
    with pytest.raises(TeamNotFound):
        repo.get_team("content-team")
    assert not team_db_uninitialized.db_path.exists()


def test_database_path_is_not_client_controllable(team_workspace: Path) -> None:
    db = TeamDatabase(team_workspace)
    assert db.db_path == team_workspace / "teams" / "state.db"
    assert not db.exists()


# --------------------------------------------------------------- 保存与修订 CAS


def test_save_team_persists_definition_and_revision_atomically(
    team_db: TeamDatabase, team_definition: TeamDefinition, fake_profiles: Any
) -> None:
    repo = TeamRepository(team_db)
    snapshot = fake_profiles.revision_snapshot(team_definition)
    result = repo.save_team(
        definition=team_definition,
        snapshot=snapshot,
        expected_revision=None,
        request_id="req-create",
    )
    stored = TeamDefinition.from_dict(result["team"])
    assert stored == team_definition
    assert repo.get_team(stored.id).revision == 1
    loaded = repo.get_revision_snapshot(stored.id, revision=1)
    assert loaded.to_dict() == snapshot.to_dict()


def test_second_save_bumps_revision_and_appends_history(
    repository: TeamRepository, team_definition: TeamDefinition, fake_profiles: Any
) -> None:
    updated = TeamDefinition.from_dict(
        {**team_definition.to_dict(), "name": "内容团队改", "revision": 2}
    )
    snapshot = fake_profiles.revision_snapshot(updated)
    repository.save_team(
        definition=updated,
        snapshot=snapshot,
        expected_revision=1,
        request_id="req-v2",
    )
    assert repository.get_team(updated.id).revision == 2
    # 旧修订仍可读：team_revisions 只追加，不原地修改。
    old = repository.get_revision_snapshot(updated.id, revision=1)
    assert old.definition.name == team_definition.name
    new = repository.get_revision_snapshot(updated.id, revision=2)
    assert new.definition.name == "内容团队改"


def test_concurrent_expected_revision_only_one_wins(
    repository: TeamRepository, team_definition: TeamDefinition, fake_profiles: Any
) -> None:
    payload = team_definition.to_dict()
    first = TeamDefinition.from_dict({**payload, "name": "第一版", "revision": 2})
    second = TeamDefinition.from_dict({**payload, "name": "第二版", "revision": 2})
    repository.save_team(
        definition=first,
        snapshot=fake_profiles.revision_snapshot(first),
        expected_revision=1,
        request_id="req-a",
    )
    with pytest.raises(TeamRevisionConflict) as excinfo:
        repository.save_team(
            definition=second,
            snapshot=fake_profiles.revision_snapshot(second),
            expected_revision=1,
            request_id="req-b",
        )
    assert excinfo.value.current_revision == 2
    # 失败的一方不留任何半成品修订行。
    assert [item["revision"] for item in repository.list_revisions(first.id)] == [1, 2]
    assert repository.get_team(first.id).name == "第一版"


def test_revision_conflict_leaves_no_partially_written_revision(
    repository: TeamRepository, team_definition: TeamDefinition, fake_profiles: Any
) -> None:
    """CAS 失败必须整事务回滚：定义、修订快照、请求记录都不许留下新的。"""
    stale = TeamDefinition.from_dict({**team_definition.to_dict(), "name": "不该出现", "revision": 5})
    with pytest.raises(TeamRevisionConflict):
        repository.save_team(
            definition=stale,
            snapshot=fake_profiles.revision_snapshot(stale),
            expected_revision=4,
            request_id="req-stale",
        )
    assert repository.get_team(stale.id).name == team_definition.name
    assert [item["revision"] for item in repository.list_revisions(stale.id)] == [1]
    assert repository.get_request("req-stale") is None


def test_team_revisions_are_append_only(repository: TeamRepository, team_definition: Any) -> None:
    """修订表由数据库层拒绝原地修改与删除（契约 §2「只追加」）。"""
    with repository.db.connect(create=True) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE team_revisions SET snapshot = '{}' WHERE team_id = ? AND revision = 1",
                (team_definition.id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "DELETE FROM team_revisions WHERE team_id = ? AND revision = 1",
                (team_definition.id,),
            )


# --------------------------------------------------------------------- 幂等重放


def test_repeated_request_id_replays_the_same_response(
    repository: TeamRepository, team_definition: TeamDefinition, fake_profiles: Any
) -> None:
    """重连重放同一 requestId 返回同一结果，且不推进第二次修订（契约 §3）。"""
    updated = TeamDefinition.from_dict({**team_definition.to_dict(), "name": "重放名", "revision": 2})
    updated_snapshot = fake_profiles.revision_snapshot(updated)
    one = repository.save_team(
        definition=updated,
        snapshot=updated_snapshot,
        expected_revision=1,
        request_id="req-same",
    )
    two = repository.save_team(
        definition=updated,
        snapshot=updated_snapshot,
        expected_revision=1,
        request_id="req-same",
    )
    assert one == two
    assert repository.get_team(updated.id).revision == 2


def test_same_request_id_with_different_payload_is_rejected(
    repository: TeamRepository, team_definition: TeamDefinition, fake_profiles: Any
) -> None:
    updated = TeamDefinition.from_dict({**team_definition.to_dict(), "name": "甲", "revision": 2})
    other = TeamDefinition.from_dict({**team_definition.to_dict(), "name": "乙", "revision": 2})
    repository.save_team(
        definition=updated,
        snapshot=fake_profiles.revision_snapshot(updated),
        expected_revision=1,
        request_id="req-key",
    )
    with pytest.raises(RequestReplayConflict):
        repository.save_team(
            definition=other,
            snapshot=fake_profiles.revision_snapshot(other),
            expected_revision=2,
            request_id="req-key",
        )
    assert repository.get_team(updated.id).name == "甲"


# ------------------------------------------------------------ 运行 / 任务 / 事件


def test_events_and_state_are_atomic(repository: TeamRepository, running_task: Any) -> None:
    before = repository.get_run(running_task.run_id)
    repository.finish_task(running_task.task_id, status="succeeded", summary="done")
    after = repository.get_run(running_task.run_id)
    events = repository.list_events(after.run_id, after_seq=before.last_seq, limit=200)
    assert after.last_seq == before.last_seq + 1
    assert events[0].type == "task.finished"


def test_finish_task_writes_terminal_state_and_result_in_one_transaction(
    repository: TeamRepository, running_task: Any
) -> None:
    result = repository.finish_task(
        running_task.task_id, status="succeeded", summary="已给出来源"
    )
    assert result.status == "succeeded"
    task = repository.get_task(running_task.task_id)
    assert task.status == "succeeded"
    assert task.result is not None
    assert task.result.summary == "已给出来源"
    assert task.finished_at is not None
    events = repository.list_events(running_task.run_id, after_seq=0, limit=10)
    finished = [event for event in events if event.type == "task.finished"]
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert len(finished) == 1
    assert finished[0].data.task.to_dict() == task.to_dict()


def test_event_sequence_is_monotonic_per_run(repository: TeamRepository, running_task: Any) -> None:
    repository.append_run_event(running_task.run_id, type="task.activity", task_id=running_task.task_id)
    repository.finish_task(running_task.task_id, status="failed", summary="超时")
    events = repository.list_events(running_task.run_id, after_seq=0, limit=50)
    assert [event.seq for event in events] == sorted(event.seq for event in events)
    assert len({event.seq for event in events}) == len(events)


def test_events_for_one_run_do_not_leak_into_another(
    repository: TeamRepository,
    running_task: Any,
    team_definition: TeamDefinition,
) -> None:
    """seq 只在单个 run 内递增，跨运行没有全局序号（契约 §6）。"""
    binding = TeamBinding(
        team_id=team_definition.id,
        team_revision=team_definition.revision,
        team_name=team_definition.name,
    )
    other = repository.create_run(
        binding=binding,
        chat_id="chat-2",
        session_key="session-2",
        turn_id="turn-2",
        internal_turn_id="internal-turn-2",
        snapshot=repository.run_binding_snapshot(binding),
    )
    repository.finish_task(running_task.task_id, status="succeeded", summary="done")
    leaked = repository.list_events(other.run_id, after_seq=0, limit=50)
    assert [event.type for event in leaked] == ["run.started"]
    assert all(event.run_id == other.run_id for event in leaked)
    assert repository.get_run(other.run_id).tasks == []


def test_one_visible_turn_has_at_most_one_run(
    repository: TeamRepository, running_task: Any, team_definition: TeamDefinition
) -> None:
    binding = TeamBinding(
        team_id=team_definition.id,
        team_revision=team_definition.revision,
        team_name=team_definition.name,
    )
    snapshot = repository.run_binding_snapshot(binding)
    with pytest.raises(sqlite3.IntegrityError):
        repository.create_run(
            binding=binding,
            chat_id="chat-1",
            session_key="session-1",
            turn_id="turn-1",
            internal_turn_id="internal-turn-other",
            snapshot=snapshot,
        )


def test_task_ownership_is_immutable_after_create(
    repository: TeamRepository, running_task: Any
) -> None:
    with repository.db.connect(create=True) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE team_tasks SET run_id = 'other-run' WHERE task_id = ?",
                (running_task.task_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE team_tasks SET member_id = 'someone-else' WHERE task_id = ?",
                (running_task.task_id,),
            )


def test_run_not_found_is_typed(repository: TeamRepository) -> None:
    with pytest.raises(RunNotFound):
        repository.get_run("no-such-run")


# --------------------------------------------------------------------- 派单幂等


def test_dispatch_with_same_batch_id_and_payload_is_idempotent(
    repository: TeamRepository, running_task: Any
) -> None:
    payload = {"tasks": [{"key": "research", "memberId": running_task.snapshot.member_id}]}
    first = repository.record_dispatch(
        run_id=running_task.run_id, batch_id="batch-1", payload=payload, task_ids=["t-1"]
    )
    replay = repository.record_dispatch(
        run_id=running_task.run_id, batch_id="batch-1", payload=payload, task_ids=["t-1"]
    )
    assert replay.replayed
    assert replay.task_ids == first.task_ids


def test_dispatch_with_same_batch_id_and_different_payload_conflicts(
    repository: TeamRepository, running_task: Any
) -> None:
    repository.record_dispatch(
        run_id=running_task.run_id,
        batch_id="batch-1",
        payload={"tasks": [{"key": "a"}]},
        task_ids=["t-1"],
    )
    with pytest.raises(DispatchKeyConflict):
        repository.record_dispatch(
            run_id=running_task.run_id,
            batch_id="batch-1",
            payload={"tasks": [{"key": "b"}]},
            task_ids=["t-2"],
        )


# ------------------------------------------------------------------- 异常与隔离


def test_failed_transaction_rolls_back_every_table(
    team_db: TeamDatabase, team_definition: TeamDefinition, fake_profiles: Any
) -> None:
    """中途抛错时定义、修订、请求记录一起回滚，不留半个团队。"""
    repo = TeamRepository(team_db)
    snapshot = fake_profiles.revision_snapshot(team_definition)
    original = repo._write_revision

    def boom(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        raise RuntimeError("mid-transaction failure")

    repo._write_revision = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        repo.save_team(
            definition=team_definition,
            snapshot=snapshot,
            expected_revision=None,
            request_id="req-rollback",
        )
    with pytest.raises(TeamNotFound):
        repo.get_team(team_definition.id)
    assert repo.get_request("req-rollback") is None


def test_corrupted_payload_is_reported_not_silently_defaulted(
    repository: TeamRepository, team_definition: TeamDefinition
) -> None:
    with repository.db.connect(create=True) as conn:
        conn.execute(
            "UPDATE teams SET payload = ? WHERE team_id = ?",
            ("{not json", team_definition.id),
        )
    with pytest.raises(StorageUnavailable):
        repository.get_team(team_definition.id)


def test_second_writer_on_the_same_workspace_is_refused(team_db: TeamDatabase) -> None:
    """调度写者必须持有 workspace 独占锁；第二持锁者直接拒绝启动调度（契约 §6）。"""
    holder = TeamRepository(TeamDatabase(team_db.workspace))
    rival = TeamRepository(TeamDatabase(team_db.workspace))
    with holder.claim_writer():
        with pytest.raises(StorageUnavailable):
            with rival.claim_writer(timeout=0.0):
                pass


def test_busy_timeout_surfaces_as_storage_unavailable(
    team_db: TeamDatabase, team_definition: TeamDefinition, fake_profiles: Any
) -> None:
    """另一连接占住写锁时，超时后报可读的存储不可用，而不是挂死事件循环。"""
    blocker = sqlite3.connect(team_db.db_path)
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("INSERT INTO _schema_meta (key, value) VALUES ('held', '1')")
    team_db.busy_timeout_ms = 50
    try:
        repo = TeamRepository(team_db)
        with pytest.raises(StorageUnavailable):
            repo.save_team(
                definition=team_definition,
                snapshot=fake_profiles.revision_snapshot(team_definition),
                expected_revision=None,
                request_id="req-blocked",
            )
    finally:
        blocker.rollback()
        blocker.close()


def test_teams_from_another_workspace_are_invisible(
    team_workspace: Path, team_definition: TeamDefinition, fake_profiles: Any
) -> None:
    """workspace_key 由路径推导，跨工作空间既读不到也改不了别人的团队。"""
    repo_a = TeamRepository(TeamDatabase(team_workspace))
    repo_a.save_team(
        definition=team_definition,
        snapshot=fake_profiles.revision_snapshot(team_definition),
        expected_revision=None,
        request_id="req-a",
    )
    repo_b = TeamRepository(TeamDatabase(team_workspace.parent / "other-workspace"))
    assert repo_b.workspace_key != repo_a.workspace_key
    assert repo_b.list_teams(limit=20)["teams"] == []
    with pytest.raises(TeamNotFound):
        repo_b.get_team(team_definition.id)


def test_schema_version_is_recorded(team_db: TeamDatabase) -> None:
    with team_db.connect(create=True) as conn:
        row = conn.execute("SELECT value FROM _schema_meta WHERE key = 'version'").fetchone()
    assert row["value"] == SCHEMA_VERSION


def test_repository_validates_before_touching_any_table(
    team_db: TeamDatabase, team_definition: TeamDefinition, fake_profiles: Any
) -> None:
    """边界校验发生在落盘之前：definition 与冻结快照不一致时一张表都不写。"""
    repo = TeamRepository(team_db)
    mismatched = TeamDefinition.from_dict({**team_definition.to_dict(), "name": "另一个名字"})
    with pytest.raises(TeamValidationError):
        repo.save_team(
            definition=mismatched,
            snapshot=fake_profiles.revision_snapshot(team_definition),
            expected_revision=None,
            request_id="req-mismatch",
        )
    with pytest.raises(TeamNotFound):
        repo.get_team(mismatched.id)
    assert repo.get_request("req-mismatch") is None
    assert repo.list_teams(limit=20)["teams"] == []
