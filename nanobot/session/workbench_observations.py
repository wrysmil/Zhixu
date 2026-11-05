"""Bounded, session-local facts for the workbench.

Only metadata crosses this boundary; tool arguments and results are never stored.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from nanobot.memory.files import memory_dirs_for
from nanobot.memory.provenance import memory_workspace_id


class WorkbenchObservation(TypedDict):
    id: str
    sequence: int
    turn_id: str | None
    call_id: str | None
    at: str | None
    kind: str
    status: str
    label: str
    resource: dict[str, str] | None
    extent: str | None
    write_kind: str | None
    source: str
    coverage: str


_ID_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
_HASH_RE = re.compile(r'^[0-9a-f]{64}$')
_KINDS = {'phase', 'tool', 'memory_read', 'memory_write', 'index_maintenance', 'context_provided', 'skill_read'}
_STATUSES = {'running', 'succeeded', 'failed', 'cancelled'}
_TERMINAL = _STATUSES - {'running'}
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_LOCK = threading.Lock()


def _lock_for(key: str) -> threading.RLock:
    with _LOCKS_LOCK:
        return _LOCKS.setdefault(key, threading.RLock())


def encode_resource_id(kind: str, scope: str, name: str, project_id: str | None) -> str:
    """Encode a constrained logical identity; no physical path is exposed."""
    if kind not in {'note', 'index', 'profile', 'skill'} or scope not in {'workspace', 'global', 'plugin', 'builtin'}:
        raise ValueError('invalid resource identity')
    if kind == 'skill':
        if not _HASH_RE.fullmatch(name):
            raise ValueError('invalid skill identity')
    elif kind == 'note':
        if not _ID_RE.fullmatch(name):
            raise ValueError('invalid note identity')
    elif (kind, name) not in {('index', 'MEMORY.md'), ('profile', 'USER.md')}:
        raise ValueError('invalid resource identity')
    if kind != 'skill' and scope not in {'global', 'workspace'}:
        raise ValueError('invalid resource scope')
    if scope == 'workspace' and not project_id:
        raise ValueError('workspace identity required')
    if project_id is not None and len(project_id) > 128:
        raise ValueError('invalid project identity')
    raw = json.dumps([1, kind, scope, name, project_id], ensure_ascii=True, separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def decode_resource_id(identifier: str, project_id: str | None) -> dict[str, Any]:
    if not identifier or len(identifier) > 1024 or not re.fullmatch(r'[A-Za-z0-9_-]+', identifier):
        raise ValueError('invalid resource identity')
    try:
        data: Any = json.loads(base64.b64decode(identifier + '=' * (-len(identifier) % 4), altchars=b'-_', validate=True))
        if not isinstance(data, list) or len(cast(list[Any], data)) != 5 or data[0] != 1 or data[4] != project_id:
            raise ValueError('resource identity mismatch')
        _, kind, scope, name, owner = cast(list[Any], data)
        if not isinstance(kind, str) or not isinstance(scope, str) or not isinstance(name, str):
            raise ValueError('invalid resource identity')
        if encode_resource_id(kind, scope, name, owner) != identifier:
            raise ValueError('invalid resource identity')
        return {'kind': kind, 'scope': scope, 'name': name, 'project_id': owner}
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid resource identity') from exc


def _text(value: object, maximum: int, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError('invalid observation field')
    return value


def _resource(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(cast(Mapping[str, object], value)) != {'id', 'kind', 'scope', 'name', 'title'}:
        raise ValueError('invalid resource')
    mapping = cast(Mapping[str, object], value)
    result = {key: _text(mapping[key], 1024 if key == 'id' else 300, required=True) for key in mapping}
    if result['kind'] not in {'note', 'index', 'profile', 'skill'} or result['scope'] not in {'workspace', 'global', 'plugin', 'builtin'}:
        raise ValueError('invalid resource')
    return result  # type: ignore[return-value]


def _merge(existing: object, snapshot: dict[str, Any]) -> dict[str, Any]:
    old_data: dict[str, Any] = cast(dict[str, Any], existing) if isinstance(existing, dict) else {}
    raw_turns: Any = old_data.get('turns', [])
    turns: list[dict[str, Any]] = [deepcopy(cast(dict[str, Any], t)) for t in cast(list[Any], raw_turns)
                                    if isinstance(t, dict) and isinstance(cast(dict[str, Any], t).get('events'), list)] if isinstance(raw_turns, list) else []
    turn_id = snapshot['turn_id']
    old = next((t for t in turns if t.get('turn_id') == turn_id), None)
    if old:
        combined: dict[str, dict[str, Any]] = {cast(dict[str, Any], e)['id']: cast(dict[str, Any], e) for e in cast(list[Any], old['events']) if isinstance(e, dict) and isinstance(cast(dict[str, Any], e).get('id'), str)}
        seq = max((e.get('sequence', 0) for e in combined.values() if isinstance(e.get('sequence'), int)), default=0)
        for event in snapshot['events']:
            prior = combined.get(event['id'])
            if prior and prior.get('status') in _TERMINAL and event['status'] != prior.get('status'):
                continue
            if prior and {**prior, 'sequence': 0} == {**event, 'sequence': 0}:
                continue
            seq += 1
            combined[event['id']] = {**event, 'sequence': seq}
        events = sorted(combined.values(), key=lambda e: e.get('sequence', 0))
        snapshot = {**snapshot, 'events': events[-200:], 'truncated': bool(old.get('truncated') or snapshot['truncated'] or len(events) > 200)}
        turns.remove(old)
    turns.append(deepcopy(snapshot))
    return {'turns': turns[-20:], 'truncated': bool(old_data.get('truncated') or len(turns) > 20)}



class TurnObservationRecorder:
    def __init__(self, session_key: str, turn_id: str | None, project_path: Path | None, agent_workspace: Path):
        _text(session_key, 512, required=True)
        self.session_key = session_key
        self.turn_id = _text(turn_id, 256)
        self.project_path = Path(project_path).resolve() if project_path is not None else None
        self.agent_workspace = Path(agent_workspace).resolve()
        self.project_id = memory_workspace_id(str(self.project_path)) if self.project_path else None
        self._events: dict[str, WorkbenchObservation] = {}
        self._sequence = 0
        self._truncated = False
        self.limitations: list[str] = []
        self._lock = threading.RLock()

    def record(self, event: Mapping[str, object]) -> None:
        event_id = _text(event.get('id'), 256, required=True)
        assert event_id is not None
        turn_id = _text(event.get('turn_id', self.turn_id), 256)
        if turn_id != self.turn_id:
            raise ValueError('turn mismatch')
        kind, status = event.get('kind'), event.get('status')
        extent, write_kind, source = event.get('extent'), event.get('write_kind'), event.get('source', 'runtime')
        if (not isinstance(kind, str) or kind not in _KINDS or not isinstance(status, str) or status not in _STATUSES
                or extent not in (None, 'full', 'partial', 'unknown')
                or write_kind not in (None, 'created', 'updated', 'written')
                or source not in ('runtime', 'history', 'extraction')):
            raise ValueError('invalid observation enum')
        coverage = event.get('coverage', 'turn' if self.turn_id else 'session')
        if coverage not in ('turn', 'session', 'unrecorded') or (coverage == 'turn' and not self.turn_id):
            raise ValueError('invalid coverage')
        resource = _resource(event.get('resource'))
        if resource is not None:
            decoded = decode_resource_id(resource['id'], self.project_id)
            if (decoded['kind'] != resource['kind'] or decoded['scope'] != resource['scope']
                    or (resource['kind'] != 'skill' and decoded['name'] != resource['name'])):
                raise ValueError('resource mismatch')
        with self._lock:
            previous = self._events.get(event_id)
            if previous and previous['status'] in _TERMINAL and status != previous['status']:
                return
            projected = {'id': event_id, 'sequence': 0, 'turn_id': turn_id,
                         'call_id': _text(event.get('call_id'), 256), 'at': _text(event.get('at'), 64),
                         'kind': kind, 'status': status, 'label': _text(event.get('label'), 300, required=True),
                         'resource': resource, 'extent': extent, 'write_kind': write_kind, 'source': source, 'coverage': coverage}
            if previous and {**previous, 'sequence': 0} == projected:
                return
            self._sequence += 1
            projected['sequence'] = self._sequence
            self._events[event_id] = projected  # type: ignore[assignment,index]
            if len(self._events) > 200:
                oldest = min(self._events, key=lambda key: self._events[key]['sequence'])
                del self._events[oldest]
                self._truncated = True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy({'events': sorted(self._events.values(), key=lambda e: e['sequence']),
                             'truncated': self._truncated, 'turn_id': self.turn_id,
                             'coverage': 'turn' if self.turn_id else 'session',
                             'limitations': self.limitations})

    def persist(self, session: Any) -> None:
        if session.key != self.session_key:
            raise ValueError('session mismatch')
        with _lock_for(self.session_key):
            session.metadata['workbench_observations'] = _merge(session.metadata.get('workbench_observations'), self.snapshot())

    def persist_to_manager(self, manager: Any) -> bool:
        """Serialize background appends and update only the metadata key."""
        with _lock_for(self.session_key):
            try:
                metadata = manager.read_session_metadata(self.session_key)
                if metadata is None:
                    raise ValueError('session unavailable')
                updated = _merge(metadata['metadata'].get('workbench_observations'), self.snapshot())
                if not manager.update_session_metadata(self.session_key, {'workbench_observations': updated}):
                    raise ValueError('session unavailable')
                return True
            except Exception:
                if 'observation_persistence_failed' not in self.limitations:
                    self.limitations.append('observation_persistence_failed')
                return False

    def record_context_resources(self, resources: Sequence[Mapping[str, object]]) -> None:
        for item in resources:
            resource = item.get('resource')
            if not isinstance(resource, Mapping):
                continue
            resource = cast(Mapping[str, object], resource)
            resource_id = resource.get('id')
            if not isinstance(resource_id, str):
                continue
            digest = hashlib.sha256(resource_id.encode()).hexdigest()
            self.record({'id': f'context:{digest}', 'kind': 'context_provided', 'status': 'succeeded',
                         'label': 'context provided', 'resource': resource, 'extent': item.get('extent', 'unknown')})

    def record_file_access(self, *, path: Path, call_id: str, operation: Literal['read', 'write', 'edit'],
                           at: str | None, extent: Literal['full', 'partial', 'unknown'] = 'unknown',
                           write_kind: Literal['created', 'updated', 'written'] | None = None) -> None:
        if operation not in {'read', 'write', 'edit'} or not call_id:
            return
        target = Path(path).resolve()
        memory_roots = memory_dirs_for(self.project_path)
        resource: dict[str, str] | None = None
        for scope, root in zip(('global', 'workspace'), memory_roots):
            if scope == 'workspace' and not self.project_path:
                continue
            root = root.resolve()
            if target.parent != root or target.suffix != '.md':
                continue
            name = target.stem
            kind = 'index' if target.name == 'MEMORY.md' else 'note'
            if kind == 'note' and not _ID_RE.fullmatch(name):
                return
            identity_name = target.name if kind == 'index' else name
            resource = {'id': encode_resource_id(kind, scope, identity_name, self.project_id), 'kind': kind,
                        'scope': scope, 'name': identity_name, 'title': identity_name}
            break
        if resource is None and operation == 'read':
            from nanobot.agent.context import resolve_identity_dir
            from nanobot.agent.skills import BUILTIN_SKILLS_DIR

            profile_paths = {self.agent_workspace / 'USER.md', resolve_identity_dir(self.agent_workspace) / 'USER.md'}
            if target in {candidate.resolve() for candidate in profile_paths}:
                resource = {'id': encode_resource_id('profile', 'global', 'USER.md', self.project_id),
                            'kind': 'profile', 'scope': 'global', 'name': 'USER.md', 'title': 'USER.md'}
            roots = [('workspace', self.project_path / 'skills') for _ in [0] if self.project_path]
            roots += [('global', self.agent_workspace / 'skills'), ('builtin', BUILTIN_SKILLS_DIR)]
            for scope, root in roots if resource is None else []:
                root = root.resolve()
                if target.name == 'SKILL.md' and target.parent.parent == root:
                    digest = hashlib.sha256(str(target).encode()).hexdigest()
                    name = target.parent.name[:300]
                    resource = {'id': encode_resource_id('skill', scope, digest, self.project_id),
                                'kind': 'skill', 'scope': scope, 'name': name, 'title': name}
                    break
            if resource is None and target.name == 'SKILL.md':
                from nanobot.agent.plugins import enabled_agent_plugin_skill_dirs

                for plugin_root in enabled_agent_plugin_skill_dirs(self.agent_workspace, requested_path=target):
                    root = plugin_root.resolve()
                    if target.parent.parent == root:
                        digest = hashlib.sha256(str(target).encode()).hexdigest()
                        name = target.parent.name[:300]
                        resource = {'id': encode_resource_id('skill', 'plugin', digest, self.project_id),
                                    'kind': 'skill', 'scope': 'plugin', 'name': name, 'title': name}
                        break
        if resource is None:
            return
        kind = ('skill_read' if resource['kind'] == 'skill' else
                'memory_read' if operation == 'read' else
                'index_maintenance' if resource['kind'] == 'index' else 'memory_write')
        key = hashlib.sha256(f"{call_id}:{kind}:{resource['id']}".encode()).hexdigest()
        self.record({'id': f'file:{key}', 'call_id': call_id, 'at': at, 'kind': kind,
                     'status': 'succeeded', 'label': resource['name'], 'resource': resource,
                     'extent': extent if operation == 'read' else None,
                     'write_kind': write_kind if operation != 'read' else None})
