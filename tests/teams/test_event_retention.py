"""事件保留与补拉协议：阈值剪枝、30 天保留期与 resetRequired（契约 §6/§7）。

生命周期事件（``run.*`` / ``task.finished`` 等）永远不参与清理，只有 ``task.activity``
这种中间过程会被剪掉；剪掉之后客户端只能靠 ``resetRequired`` 回快照。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from nanobot.teams.binding import TeamBinding
from nanobot.teams.database import StorageUnavailable
from nanobot.teams.models import TeamDefinition
from nanobot.teams.repository import TeamRepository

BINDING_REVISION = 1


def _binding(definition: TeamDefinition) -> TeamBinding:
    return TeamBinding(
        team_id=definition.id,
        team_revision=definition.revision,
        team_name=definition.name,
    )


def _run_with_task(repository: TeamRepository, definition: TeamDefinition) -> tuple[str, str]:
    binding = _binding(definition)
    run = repository.create_run(
        binding=binding,
        chat_id="chat-1",
        session_key="session-1",
        turn_id="turn-1",
        internal_turn_id="internal-1",
        snapshot=repository.run_binding_snapshot(binding),
    )
    task = repository.create_task(
        run_id=run.run_id,
        member=definition.members[0],
        title="跑腿",
        acceptance="给出结果",
        instruction="执行",
        status="running",
    )
    return run.run_id, task.task_id


def _append_activity(repository: TeamRepository, run_id: str, task_id: str, action: str) -> None:
    repository.record_activity(task_id, last_action=action)


def _types(repository: TeamRepository, run_id: str) -> list[tuple[int, str]]:
    with repository.db.connect(create=True) as conn:
        rows = conn.execute(
            "SELECT seq, type FROM team_events WHERE run_id = ? ORDER BY seq ASC", (run_id,)
        ).fetchall()
    return [(int(row["seq"]), str(row["type"])) for row in rows]


def _age_activity(repository: TeamRepository, run_id: str, days: int) -> None:
    """把某条活动事件的时间戳往回拨，模拟「早就发生过的中间过程」。"""
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    with repository.db.connect(create=True) as conn:
        conn.execute(
            "UPDATE team_events SET occurred_at = ? WHERE run_id = ? AND type = 'task.activity'",
            (stamp, run_id),
        )


def test_prune_keeps_latest_activity_and_never_drops_lifecycle(
    repository: TeamRepository, team_definition: TeamDefinition
) -> None:
    repository.hold_writer()
    run_id, task_id = _run_with_task(repository, team_definition)
    for index in range(10):
        _append_activity(repository, run_id, task_id, f"步骤 {index}")

    before = _types(repository, run_id)
    assert sum(1 for _, kind in before if kind == "task.activity") == 10

    pruned = repository.prune_activity_events(retain=3)
    assert pruned == [run_id]

    after = _types(repository, run_id)
    activities = [seq for seq, kind in after if kind == "task.activity"]
    assert len(activities) == 3
    # 留最新三条，丢最旧七条。
    assert activities == [seq for seq, kind in before if kind == "task.activity"][-3:]
    # run.started / task.created 之类的生命周期事件一条不少。
    assert all(kind != "task.activity" for _, kind in before if kind != "task.activity")
    assert [kind for _, kind in after if kind != "task.activity"] == [
        kind for _, kind in before if kind != "task.activity"
    ]


def test_prune_drops_activity_older_than_retention_window(
    repository: TeamRepository, team_definition: TeamDefinition
) -> None:
    repository.hold_writer()
    run_id, task_id = _run_with_task(repository, team_definition)
    _append_activity(repository, run_id, task_id, "旧活动")
    _age_activity(repository, run_id, days=31)

    assert repository.prune_activity_events() == [run_id]
    assert all(kind != "task.activity" for _, kind in _types(repository, run_id))


def test_prune_leaves_recent_activity_alone(
    repository: TeamRepository, team_definition: TeamDefinition
) -> None:
    repository.hold_writer()
    run_id, task_id = _run_with_task(repository, team_definition)
    _append_activity(repository, run_id, task_id, "刚发生")

    assert repository.prune_activity_events() == []
    assert any(kind == "task.activity" for _, kind in _types(repository, run_id))


def test_events_page_flags_reset_after_pruning(
    repository: TeamRepository, team_definition: TeamDefinition
) -> None:
    """增量被剪掉后 ``resetRequired`` 必须为真，否则客户端会安静地漏掉中间过程。"""
    repository.hold_writer()
    run_id, task_id = _run_with_task(repository, team_definition)
    for index in range(6):
        _append_activity(repository, run_id, task_id, f"步骤 {index}")

    fresh = repository.events_page(run_id, after_seq=0)
    assert fresh["resetRequired"] is False
    assert fresh["lastSeq"] == fresh["events"][-1]["seq"]

    repository.prune_activity_events(retain=2)
    pruned = repository.events_page(run_id, after_seq=0)
    assert pruned["resetRequired"] is True
    # 从已知 seq 之后连续补拉时不要求重置。
    tail = repository.events_page(run_id, after_seq=pruned["events"][-1]["seq"])
    assert tail["resetRequired"] is False


def test_events_page_rejects_out_of_range_limit(
    repository: TeamRepository, team_definition: TeamDefinition
) -> None:
    run_id, _ = _run_with_task(repository, team_definition)
    with pytest.raises(Exception, match="event limit"):
        repository.events_page(run_id, after_seq=0, limit=0)
    with pytest.raises(Exception, match="event limit"):
        repository.events_page(run_id, after_seq=0, limit=501)


def test_events_are_scoped_to_their_workspace(
    repository: TeamRepository, team_definition: TeamDefinition, tmp_path_factory
) -> None:
    """另一个 workspace 的库文件读不到这条 run。"""
    from nanobot.teams.database import TeamDatabase

    run_id, _ = _run_with_task(repository, team_definition)
    other = TeamRepository(TeamDatabase(tmp_path_factory.mktemp("other-workspace")))
    with pytest.raises(Exception):
        other.events_page(run_id, after_seq=0)


def test_startup_cleanup_needs_the_writer(repository: TeamRepository) -> None:
    with pytest.raises(StorageUnavailable):
        repository.prune_activity_events()


def test_sqlite_row_factory_is_row(repository: TeamRepository) -> None:
    """事件读取依赖 ``sqlite3.Row`` 索引访问；这里钉住它不被改掉。"""
    with repository.db.connect(create=True) as conn:
        conn.execute("SELECT 1 AS one")
        row = conn.execute("SELECT 1 AS one").fetchone()
        assert isinstance(row, sqlite3.Row)
        assert row["one"] == 1
