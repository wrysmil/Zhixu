from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

import nanobot.subagent_executions.repository as ledger
from nanobot.subagent_executions import ExecutionRepository


def create(repo, chat='chat'):
    return repo.create(chat_id=chat, parent_turn_id='turn', parent_tool_call_id='call')


def test_terminal_state_and_activity_cannot_be_reopened():
    repo = ExecutionRepository()
    item = create(repo)
    activity = repo.start_activity(item.invocation_id, 'read')
    final = repo.finish(item.invocation_id, 'cancelled')
    assert final.activities[0].status == 'cancelled'
    assert repo.finish_activity(item.invocation_id, activity.activity_id).status == 'cancelled'
    assert repo.set_phase(item.invocation_id, 'model').last_seq == final.last_seq
    assert repo.finish(item.invocation_id, 'succeeded').status == 'cancelled'
    with pytest.raises(ValueError):
        repo.start_activity(item.invocation_id, 'read')
    repo.close()


def test_concurrent_activity_updates_have_contiguous_event_sequences():
    repo = ExecutionRepository()
    item = create(repo)
    with ThreadPoolExecutor(max_workers=4) as pool:
        activities = list(pool.map(lambda _: repo.start_activity(item.invocation_id, 'read'), range(20)))
    assert len({activity.activity_id for activity in activities}) == 20
    assert [event.seq for event in repo.events('chat', item.invocation_id).items] == list(range(1, 22))
    first = repo.events('chat', item.invocation_id, through_seq=5, limit=2)
    assert [event.seq for event in first.items] == [1, 2]
    assert first.has_more
    repo.close()


def test_cursor_rejects_different_chat_and_parent_scope():
    repo = ExecutionRepository()
    for _ in range(3):
        item = create(repo)
        repo.finish(item.invocation_id, 'succeeded')
    cursor = repo.list('chat', limit=1).next_cursor
    with pytest.raises(ValueError):
        repo.list('other', cursor=cursor)
    with pytest.raises(ValueError):
        repo.list('chat', cursor=cursor, parent_turn_id='other')
    repo.close()


def test_retention_preserves_active_and_reports_expired_event_history(monkeypatch):
    repo = ExecutionRepository()
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(ledger, '_now', lambda: (now - timedelta(days=31)).isoformat())
    expired = create(repo)
    repo.finish(expired.invocation_id, 'succeeded')
    active = create(repo)
    monkeypatch.setattr(ledger, '_now', lambda: (now - timedelta(days=8)).isoformat())
    history = create(repo)
    repo.finish(history.invocation_id, 'failed')
    repo.prune(now=now)
    assert repo.detail('chat', expired.invocation_id) is None
    assert repo.detail('chat', active.invocation_id).status == 'queued'
    assert repo.detail('chat', history.invocation_id).status == 'failed'
    page = repo.events('chat', history.invocation_id)
    assert page.items == [] and page.reset_required
    repo.close()


def test_cross_thread_close_releases_writer_and_is_idempotent(tmp_path):
    repo = ExecutionRepository(tmp_path)
    create(repo)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(repo.close).result()
        pool.submit(repo.close).result()
    repo.close()
    reopened = ExecutionRepository(tmp_path)
    assert reopened.list('chat').items[0].status == 'interrupted'
    reopened.close()
