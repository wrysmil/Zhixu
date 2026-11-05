from pathlib import Path

import pytest

from nanobot.webui.workbench_resources import list_notes, preview_note, StaleCursorError


def test_pagination_and_project_binding(tmp_path):
    root = tmp_path / 'memory'
    root.mkdir()
    for name in ('a', 'b'):
        (root / f'{name}.md').write_text('---\ndescription: hello\n---\n正文', encoding='utf-8')
    first = list_notes(root, 'workspace', 'project-a', limit=1)
    assert first['total'] == 2 and first['items'][0]['name'] == 'a'
    assert list_notes(root, 'workspace', 'project-a', limit=1, cursor=first['next_cursor'])['items'][0]['name'] == 'b'
    with pytest.raises(ValueError):
        preview_note(first['items'][0]['id'], root, 'project-b')
    (root / 'c.md').write_text('new', encoding='utf-8')
    with pytest.raises(StaleCursorError):
        list_notes(root, 'workspace', 'project-a', limit=1, cursor=first['next_cursor'])


def test_preview_is_bounded_and_symlinks_cannot_escape(tmp_path):
    root = tmp_path / 'memory'
    root.mkdir()
    (root / 'a.md').write_text('好' * 6000, encoding='utf-8')
    item = list_notes(root, 'global', None)['items'][0]
    preview = preview_note(item['id'], root, None)
    assert preview['truncated'] and len(preview['content'].encode()) <= 16384
    assert not preview['content'].endswith('\ufffd')
    outside = tmp_path / 'outside.md'
    outside.write_text('secret', encoding='utf-8')
    try:
        (root / 'evil.md').symlink_to(outside)
    except OSError:
        return
    assert [row['name'] for row in list_notes(root, 'global', None)['items']] == ['a']

