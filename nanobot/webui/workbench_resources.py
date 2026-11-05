"""Read-only, project-bound workbench memory resources."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from nanobot.session.workbench_observations import decode_resource_id, encode_resource_id


class StaleCursorError(ValueError):
    """The collection changed since its previous page."""


def _safe_file(root: Path, name: str) -> Path:
    base = root.resolve(strict=True)
    candidate = (root / name).resolve(strict=True)
    if candidate.parent != base or not candidate.is_file():
        raise ValueError('resource unavailable')
    return candidate


def _encode_cursor(value: list[Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).decode().rstrip('=')


def list_notes(root: Path | None, scope: str, project_id: str | None, *, limit: int = 50,
               cursor: str | None = None) -> dict[str, Any]:
    if scope not in {'global', 'workspace'} or not 1 <= limit <= 50:
        raise ValueError('invalid parameters')
    paths: list[tuple[str, Path, Any]] = []
    if root is not None and root.is_dir():
        for path in root.iterdir():
            if path.suffix != '.md' or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', path.stem):
                continue
            try:
                safe = _safe_file(root, path.name)
                paths.append((path.stem, safe, safe.stat()))
            except (OSError, ValueError):
                continue
    paths.sort(key=lambda row: row[0])
    revision = hashlib.sha256(json.dumps([(n, s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
                                          for n, _, s in paths]).encode()).hexdigest()
    last = ''
    if cursor is not None:
        if len(cursor) > 2048 or not re.fullmatch(r'[A-Za-z0-9_-]+', cursor):
            raise ValueError('invalid cursor')
        try:
            data = json.loads(base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
        except (ValueError, UnicodeError) as exc:
            raise ValueError('invalid cursor') from exc
        if not isinstance(data, list) or len(data) != 6 or data[:4] != [1, 'memories', scope, project_id] or not all(isinstance(x, str) for x in data[4:]):
            raise ValueError('invalid cursor')
        if data[4] != revision:
            raise StaleCursorError('collection changed')
        last = data[5]
    remaining = [row for row in paths if row[0] > last]
    selected = remaining[:limit]
    items = []
    for name, path, stat in selected:
        with path.open('rb') as source:
            prefix = source.read(8192).decode('utf-8', errors='replace')
        meta: Any = {}
        if prefix.startswith('---\n') and '\n---' in prefix[4:]:
            try:
                meta = yaml.safe_load(prefix[4:].split('\n---', 1)[0]) or {}
            except yaml.YAMLError:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        description = meta.get('description')
        description = description[:500] if isinstance(description, str) else ''
        metadata = meta.get('metadata')
        typ = metadata.get('type') if isinstance(metadata, dict) else None
        items.append({'id': encode_resource_id('note', scope, name, project_id), 'scope': scope,
                      'name': name, 'title': description[:300] or name, 'description': description,
                      'memory_type': typ[:100] if isinstance(typ, str) else 'project',
                      'updated_at': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                      'preview_available': True})
    next_cursor = _encode_cursor([1, 'memories', scope, project_id, revision, selected[-1][0]]) if len(remaining) > limit else None
    return {'items': items, 'total': len(paths), 'next_cursor': next_cursor, 'revision': revision}


def preview_note(identifier: str, root: Path, project_id: str | None) -> dict[str, Any]:
    identity = decode_resource_id(identifier, project_id)
    if identity['kind'] not in {'note', 'index'}:
        raise ValueError('invalid resource')
    name = identity['name']
    path = _safe_file(root, f'{name}.md' if identity['kind'] == 'note' else name)
    with path.open('rb') as source:
        data = source.read(16385)
    truncated = len(data) > 16384
    text = data[:16384].decode('utf-8', errors='ignore')
    return {'resource': {'id': identifier, 'kind': identity['kind'], 'scope': identity['scope'],
                         'name': name, 'title': name}, 'content': text,
            'updated_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            'truncated': truncated}

