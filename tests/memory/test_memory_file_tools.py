"""文件模式下的工具边界：记忆目录可写、其它目录不可写、记忆库写入工具撤下。"""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.filesystem import WriteFileTool
from nanobot.agent.tools.memory_admin import AddMemoryTool
from nanobot.config.schema import ToolsConfig, _resolve_tool_config_refs

_resolve_tool_config_refs()


def _ctx(tmp_path: Path, *, memory_mode: bool) -> ToolContext:
    return ToolContext(
        config=ToolsConfig(restrict_to_workspace=True),
        workspace=str(tmp_path / "ws"),
        attributes={"memory_file_mode": memory_mode},
    )


@pytest.mark.asyncio
async def test_write_file_allows_memory_dir(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    tool = WriteFileTool.create(_ctx(tmp_path, memory_mode=True))
    target = home / ".nanobot" / "memory" / "note.md"
    result = await tool.execute(path=str(target), content="记住：提交信息用中文")

    assert "Successfully wrote" in str(result)
    assert target.read_text(encoding="utf-8") == "记住：提交信息用中文"


@pytest.mark.asyncio
async def test_write_file_rejects_other_dirs(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    tool = WriteFileTool.create(_ctx(tmp_path, memory_mode=True))
    outside = tmp_path / "outside.txt"
    result = await tool.execute(path=str(outside), content="不该写")

    assert "Error" in str(result)
    assert not outside.exists()


def test_memory_admin_tools_disabled_in_file_mode(tmp_path: Path) -> None:
    assert AddMemoryTool.enabled(_ctx(tmp_path, memory_mode=True)) is False


@pytest.mark.asyncio
async def test_real_file_operations_record_only_success(monkeypatch, tmp_path):
    from nanobot.agent.tools.context import RequestContext, request_context
    from nanobot.agent.tools.execution import _CURRENT_TOOL_CALL_ID
    from nanobot.agent.tools.filesystem import EditFileTool, ReadFileTool
    from nanobot.session.workbench_observations import TurnObservationRecorder

    global_dir, project_dir = tmp_path / 'global', tmp_path / 'project-memory'
    global_dir.mkdir()
    project_dir.mkdir()
    monkeypatch.setattr('nanobot.session.workbench_observations.memory_dirs_for', lambda _: [global_dir, project_dir])
    monkeypatch.setattr('nanobot.session.workbench_observations.memory_workspace_id', lambda _: 'project-id')
    recorder = TurnObservationRecorder('s', 'turn', tmp_path / 'project', tmp_path / 'agent')
    context = RequestContext(channel='websocket', chat_id='s', session_key='s', turn_id='turn',
                             workspace=tmp_path / 'project', attributes={'workbench_observer': recorder})
    target = global_dir / 'test.md'
    token = _CURRENT_TOOL_CALL_ID.set('write-1')
    try:
        with request_context(context):
            write = WriteFileTool(workspace=tmp_path)
            read = ReadFileTool(workspace=tmp_path)
            edit = EditFileTool(workspace=tmp_path)
            await write.execute(path=str(target), content='one\ntwo\nthree')
            _CURRENT_TOOL_CALL_ID.set('read-1')
            await read.execute(path=str(target), offset=2, limit=1)
            _CURRENT_TOOL_CALL_ID.set('read-2')
            await read.execute(path=str(target), force=True)
            _CURRENT_TOOL_CALL_ID.set('edit-1')
            await edit.execute(path=str(target), old_text='one', new_text='changed')
            _CURRENT_TOOL_CALL_ID.set('fail')
            await edit.execute(path=str(target), old_text='missing', new_text='no')
            _CURRENT_TOOL_CALL_ID.set('index')
            await write.execute(path=str(global_dir / 'MEMORY.md'), content='index')
            _CURRENT_TOOL_CALL_ID.set('ordinary')
            await write.execute(path=str(tmp_path / 'plain.md'), content='normal')
    finally:
        _CURRENT_TOOL_CALL_ID.reset(token)
    events = recorder.snapshot()['events']
    assert [e['kind'] for e in events] == ['memory_write', 'memory_read', 'memory_read', 'memory_write', 'index_maintenance']
    assert events[0]['write_kind'] == 'created'
    assert events[1]['extent'] == 'partial' and events[2]['extent'] == 'full'
    assert events[3]['write_kind'] == 'updated'
    assert 'one\ntwo\nthree' not in str(events) and 'missing' not in str(events)


@pytest.mark.asyncio
async def test_project_memory_permission_tracks_request_workspace(monkeypatch, tmp_path):
    from nanobot.agent.tools.context import RequestContext, request_context
    global_dir = tmp_path / 'global-memory'
    first_dir = tmp_path / 'first-memory'
    second_dir = tmp_path / 'second-memory'
    for directory in (global_dir, first_dir, second_dir):
        directory.mkdir()
    agent = tmp_path / 'agent'
    agent.mkdir()
    project1, project2 = tmp_path / 'p1', tmp_path / 'p2'
    project1.mkdir()
    project2.mkdir()
    def dirs(project):
        return [global_dir, first_dir if project == project1 else second_dir]
    monkeypatch.setattr('nanobot.memory.files.memory_dirs_for', dirs)
    tool = WriteFileTool.create(ToolContext(config=ToolsConfig(restrict_to_workspace=True),
                                            workspace=str(agent), attributes={'memory_file_mode': True}))
    request = RequestContext(channel='websocket', chat_id='s', workspace=project1)
    with request_context(request):
        assert 'Successfully' in await tool.execute(path=str(first_dir / 'same.md'), content='a')
        assert 'Successfully' in await tool.execute(path=str(global_dir / 'same.md'), content='g')
        assert 'Error' in await tool.execute(path=str(second_dir / 'same.md'), content='b')
    assert not (second_dir / 'same.md').exists()
    with request_context(RequestContext(channel='websocket', chat_id='s', workspace=project2)):
        assert 'Successfully' in await tool.execute(path=str(second_dir / 'same.md'), content='b')
        assert 'Error' in await tool.execute(path=str(first_dir / 'same.md'), content='a')


@pytest.mark.asyncio
async def test_memory_symlink_escape_is_not_allowed(monkeypatch, tmp_path):
    from nanobot.agent.tools.context import RequestContext, request_context
    root, outside, project, agent = [tmp_path / name for name in ('memory', 'outside', 'project', 'agent')]
    for directory in (root, outside, project, agent):
        directory.mkdir()
    try:
        (root / 'escape').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('directory symlinks unavailable')
    monkeypatch.setattr('nanobot.memory.files.memory_dirs_for', lambda _: [root, root])
    tool = WriteFileTool.create(ToolContext(config=ToolsConfig(restrict_to_workspace=True), workspace=str(agent), attributes={'memory_file_mode': True}))
    with request_context(RequestContext(channel='websocket', chat_id='s', workspace=project)):
        result = await tool.execute(path=str(root / 'escape' / 'note.md'), content='blocked')
    assert 'Error' in result
    assert not (outside / 'note.md').exists()


@pytest.mark.asyncio
async def test_context_isolation_for_parallel_reads(monkeypatch, tmp_path):
    import asyncio

    from nanobot.agent.tools.context import RequestContext, request_context
    from nanobot.agent.tools.execution import _CURRENT_TOOL_CALL_ID
    from nanobot.agent.tools.filesystem import ReadFileTool
    from nanobot.session.workbench_observations import TurnObservationRecorder

    global_dir, project_dir = tmp_path / 'global', tmp_path / 'project-memory'
    global_dir.mkdir()
    project_dir.mkdir()
    (global_dir / 'same.md').write_text('global', encoding='utf-8')
    (project_dir / 'same.md').write_text('project', encoding='utf-8')
    monkeypatch.setattr('nanobot.session.workbench_observations.memory_dirs_for', lambda _: [global_dir, project_dir])
    monkeypatch.setattr('nanobot.session.workbench_observations.memory_workspace_id', lambda _: 'project-a')
    tool = ReadFileTool(workspace=tmp_path)
    first = TurnObservationRecorder('s1', 't1', tmp_path / 'project', tmp_path / 'agent')
    second = TurnObservationRecorder('s2', 't2', tmp_path / 'project', tmp_path / 'agent')
    async def read(recorder, path):
        ctx = RequestContext(channel='websocket', chat_id='s', session_key=recorder.session_key,
                             turn_id=recorder.turn_id, attributes={'workbench_observer': recorder})
        with request_context(ctx):
            token = _CURRENT_TOOL_CALL_ID.set('same-call')
            try:
                await asyncio.sleep(0)
                await tool.execute(path=str(path), force=True)
            finally:
                _CURRENT_TOOL_CALL_ID.reset(token)
    await asyncio.gather(read(first, global_dir / 'same.md'), read(second, project_dir / 'same.md'))
    assert first.snapshot()['events'][0]['resource']['scope'] == 'global'
    assert second.snapshot()['events'][0]['resource']['scope'] == 'workspace'
    assert first.snapshot()['events'][0]['resource']['id'] != second.snapshot()['events'][0]['resource']['id']


@pytest.mark.asyncio
async def test_mismatched_request_cannot_record_other_session(monkeypatch, tmp_path):
    from nanobot.agent.tools.context import RequestContext, request_context
    from nanobot.agent.tools.execution import _CURRENT_TOOL_CALL_ID
    from nanobot.session.workbench_observations import TurnObservationRecorder
    monkeypatch.setattr('nanobot.session.workbench_observations.memory_dirs_for', lambda _: [tmp_path, tmp_path])
    recorder = TurnObservationRecorder('owner', 'turn', None, tmp_path)
    token = _CURRENT_TOOL_CALL_ID.set('call')
    try:
        with request_context(RequestContext(channel='websocket', chat_id='x', session_key='other',
                                            turn_id='turn', attributes={'workbench_observer': recorder})):
            await WriteFileTool(workspace=tmp_path).execute(path=str(tmp_path / 'note.md'), content='test')
    finally:
        _CURRENT_TOOL_CALL_ID.reset(token)
    assert recorder.snapshot()['events'] == []
