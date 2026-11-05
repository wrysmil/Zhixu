import pytest

from nanobot.agent.hook import AgentHookContext, AgentRunHookContext
from nanobot.providers.base import ToolCallRequest
from nanobot.session.workbench_observations import TurnObservationRecorder


@pytest.mark.asyncio
async def test_tool_lifecycle_and_cancel(tmp_path):
    from nanobot.agent.hooks.workbench_activity import WorkbenchActivityHook
    recorder = TurnObservationRecorder('s', 't', None, tmp_path)
    hook = WorkbenchActivityHook(recorder)
    ctx = AgentHookContext(iteration=0, messages=[])
    call = ToolCallRequest(id='call', name='read_file', arguments={'secret': 'do not store'})
    await hook.before_execute_tool(ctx, call, None, call.arguments)
    await hook.after_execute_tool(ctx, call, None, call.arguments, 'secret result')
    await hook.before_execute_tool(ctx, call, None, call.arguments)
    pending = ToolCallRequest(id='pending', name='write_file', arguments={})
    await hook.before_execute_tool(ctx, pending, None, {})
    await hook.on_finally(AgentRunHookContext(messages=[], stop_reason='cancelled'))
    events = [e for e in recorder.snapshot()['events'] if e['kind'] == 'tool']
    assert {e['call_id']: e['status'] for e in events} == {'call': 'succeeded', 'pending': 'cancelled'}
    assert 'secret' not in str(events)


@pytest.mark.asyncio
async def test_hook_failure_does_not_record_error_text(tmp_path):
    from nanobot.agent.hooks.workbench_activity import WorkbenchActivityHook
    recorder = TurnObservationRecorder('s', 't', None, tmp_path)
    hook = WorkbenchActivityHook(recorder)
    context = AgentHookContext(iteration=0, messages=[])
    call = ToolCallRequest(id='bad', name='write_file', arguments={})
    await hook.before_run(AgentRunHookContext(messages=[]))
    await hook.before_execute_tool(context, call, None, {})
    await hook.on_execute_tool_error(context, call, None, {}, RuntimeError('private text'))
    await hook.on_finally(AgentRunHookContext(messages=[], stop_reason='error', error='private text'))
    events = recorder.snapshot()['events']
    assert next(e for e in events if e['kind'] == 'tool')['status'] == 'failed'
    assert next(e for e in events if e['kind'] == 'phase')['label'] == 'failed'
    assert 'private text' not in str(events)
