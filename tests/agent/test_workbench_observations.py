from nanobot.session.manager import SessionManager


def test_fork_does_not_inherit_workbench_observations(tmp_path):
    manager = SessionManager(tmp_path)
    source = manager.get_or_create('websocket:source')
    source.metadata['workbench_observations'] = {'turns': [{'turn_id': 'old'}]}
    source.add_message('user', 'hello')
    manager.save(source)

    fork = manager.fork_session_before_user_index('websocket:source', 'websocket:fork', 1)

    assert fork is not None
    assert 'workbench_observations' not in fork.metadata
    assert 'workbench_observations' not in manager.read_session_metadata('websocket:fork')['metadata']
    assert source.metadata['workbench_observations']['turns'][0]['turn_id'] == 'old'


def event(event_id, status='running', call_id=None):
    return {'id': event_id, 'turn_id': 'turn', 'call_id': call_id, 'at': None,
            'kind': 'tool', 'status': status, 'label': 'read_file', 'resource': None,
            'extent': None, 'write_kind': None, 'source': 'runtime', 'coverage': 'turn'}


def test_recorder_idempotent_bounded_and_isolated(tmp_path):
    from nanobot.session.workbench_observations import TurnObservationRecorder
    a = TurnObservationRecorder('s', 'turn', None, tmp_path)
    b = TurnObservationRecorder('s2', 'turn', None, tmp_path)
    a.record(event('one', call_id='c'))
    a.record(event('one', 'succeeded', 'c'))
    a.record(event('one', 'running', 'c'))
    assert a.snapshot()['events'][0]['status'] == 'succeeded'
    assert not b.snapshot()['events']
    for i in range(205):
        a.record(event(str(i)))
    snap = a.snapshot()
    assert len(snap['events']) == 200 and snap['truncated']
    assert [e['sequence'] for e in snap['events']] == sorted(e['sequence'] for e in snap['events'])
    snap['events'].clear()
    assert len(a.snapshot()['events']) == 200


def test_persist_limits_turns_and_background_merge(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from nanobot.session.workbench_observations import TurnObservationRecorder
    manager = SessionManager(tmp_path)
    session = manager.get_or_create('websocket:test')
    session.add_message('user', 'keep history')
    manager.save(session)
    for n in range(21):
        recorder = TurnObservationRecorder(session.key, str(n), None, tmp_path)
        recorder.record({**event(str(n)), 'turn_id': str(n)})
        recorder.persist(session)
    assert len(session.metadata['workbench_observations']['turns']) == 20
    manager.save(session)
    def append(n):
        rec = TurnObservationRecorder(session.key, f'background-{n}', None, tmp_path)
        rec.record({**event(f'bg-{n}'), 'turn_id': f'background-{n}', 'source': 'extraction'})
        assert rec.persist_to_manager(manager)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(append, range(2)))
    saved = manager.read_session_file(session.key)
    assert saved['messages'][0]['content'] == 'keep history'
    assert {f'background-{n}' for n in range(2)} <= {t['turn_id'] for t in saved['metadata']['workbench_observations']['turns']}


def test_resource_id_is_project_bound_and_validated():
    import pytest

    from nanobot.session.workbench_observations import decode_resource_id, encode_resource_id
    identifier = encode_resource_id('note', 'workspace', 'same-name', 'project-a')
    assert decode_resource_id(identifier, 'project-a')['name'] == 'same-name'
    with pytest.raises(ValueError):
        decode_resource_id(identifier, 'project-b')
    with pytest.raises(ValueError):
        encode_resource_id('note', 'workspace', '../secret', 'project-a')


def test_validation_manifest_and_persistence_failure(monkeypatch, tmp_path):
    import pytest

    from nanobot.session.workbench_observations import TurnObservationRecorder, encode_resource_id
    recorder = TurnObservationRecorder('s', None, None, tmp_path)
    resource = {'id': encode_resource_id('index', 'global', 'MEMORY.md', None),
                'kind': 'index', 'scope': 'global', 'name': 'MEMORY.md', 'title': 'MEMORY.md'}
    recorder.record_context_resources([{'resource': resource, 'extent': 'partial'}])
    snap = recorder.snapshot()
    assert snap['coverage'] == 'session'
    assert snap['events'][0]['extent'] == 'partial'
    with pytest.raises(ValueError):
        recorder.record({**event('bad'), 'turn_id': None})
    with pytest.raises(ValueError):
        recorder.record({'id': 'bad', 'kind': 'tool', 'status': 'succeeded', 'label': 'x',
                         'resource': {**resource, 'scope': 'workspace'}})
    class Unavailable:
        def read_session_metadata(self, key):
            return None
    assert recorder.persist_to_manager(Unavailable()) is False
    assert recorder.limitations == ['observation_persistence_failed']


def test_merge_idempotence_and_reload(tmp_path):
    from nanobot.session.workbench_observations import TurnObservationRecorder
    manager = SessionManager(tmp_path)
    session = manager.get_or_create('websocket:idempotent')
    session.add_message('user', 'hello')
    manager.save(session)
    rec = TurnObservationRecorder(session.key, 'turn', None, tmp_path)
    rec.record(event('same', 'succeeded'))
    assert rec.persist_to_manager(manager)
    first = manager.read_session_metadata(session.key)['metadata']['workbench_observations']
    assert rec.persist_to_manager(manager)
    second = manager.read_session_metadata(session.key)['metadata']['workbench_observations']
    assert first == second
    assert manager.read_session_snapshot(session.key).metadata['workbench_observations'] == first


def test_skill_and_profile_are_recorded_without_file_contents(monkeypatch, tmp_path):
    from nanobot.session.workbench_observations import TurnObservationRecorder, decode_resource_id
    monkeypatch.setattr('nanobot.session.workbench_observations.memory_dirs_for', lambda _: [tmp_path / 'mem', tmp_path / 'pmem'])
    monkeypatch.setattr('nanobot.session.workbench_observations.memory_workspace_id', lambda _: 'project')
    agent, project = tmp_path / 'agent', tmp_path / 'project'
    recorder = TurnObservationRecorder('s', 't', project, agent)
    skill = project / 'skills' / 'sample' / 'SKILL.md'
    profile = agent / 'USER.md'
    recorder.record_file_access(path=skill, call_id='skill', operation='read', at=None, extent='partial')
    recorder.record_file_access(path=profile, call_id='profile', operation='read', at=None, extent='full')
    events = recorder.snapshot()['events']
    assert [e['kind'] for e in events] == ['skill_read', 'memory_read']
    assert events[0]['resource']['scope'] == 'workspace'
    assert decode_resource_id(events[0]['resource']['id'], 'project')['kind'] == 'skill'
    assert events[1]['resource']['kind'] == 'profile'
    assert str(tmp_path) not in str(events)
