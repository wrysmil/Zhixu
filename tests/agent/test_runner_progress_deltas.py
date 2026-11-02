"""Tests for runner progress hooks and provider event routing."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.runner_helpers import make_run_spec
from nanobot.agent.hook import AgentHookContext, CompositeHook
from nanobot.agent.hooks import FileEditActivityHook
from nanobot.agent.progress_hook import AgentProgressHook
from nanobot.agent.runner import AgentRunner
from nanobot.agent.tools.filesystem import EditFileTool, WriteFileTool
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import LLMResponse, ToolCallRequest
from nanobot.providers.openai_responses.parsing import (
    consume_sdk_stream,
    consume_sse_with_reasoning,
)
from nanobot.utils.progress_events import output_events

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_format", ["chat", "responses_sse", "responses_sdk"])
async def test_runner_prepares_file_edit_before_arguments_finish_without_exposing_content(tmp_path, provider_format):
    provider = MagicMock()
    edits: list[dict] = []
    calls = 0
    tool = WriteFileTool(workspace=tmp_path)
    expected_id = "write-1" if provider_format == "chat" else "write-1|fc1"

    async def progress_cb(_content, *, file_edit_events=None, **kwargs):
        edits.extend(file_edit_events or [])

    class Tools:
        def get_definitions(self):
            return [{"type": "function", "function": {"name": "write_file"}}]

        def prepare_call(self, name, params):
            return tool, params, None

    async def chat_stream_with_retry(*, on_tool_call_delta, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            return LLMResponse(content="done")
        if provider_format != "chat":
            async def response_events():
                yield {
                    "type": "response.output_item.added",
                    "item": {"type": "function_call", "call_id": "write-1", "id": "fc1", "name": "write_file", "arguments": ""},
                }
                assert len(edits) == 1 and edits[0]["call_id"] == expected_id
                assert not (tmp_path / "a.txt").exists()
                yield {"type": "response.function_call_arguments.delta", "call_id": "write-1", "delta": '{"path":"a.txt","content":"private content"}'}
                yield {
                    "type": "response.output_item.done",
                    "item": {"type": "function_call", "call_id": "write-1", "id": "fc1", "name": "write_file", "arguments": '{"path":"a.txt","content":"private content"}'},
                }
                yield {"type": "response.completed", "response": {"status": "completed"}}

            if provider_format == "responses_sdk":
                async def sdk_events():
                    async for event in response_events():
                        yield json.loads(json.dumps(event), object_hook=lambda data: SimpleNamespace(**data))

                parsed = await consume_sdk_stream(sdk_events(), on_tool_call_delta=on_tool_call_delta)
            else:
                class SseResponse:
                    async def aiter_lines(self):
                        async for event in response_events():
                            yield f"data: {json.dumps(event)}"
                            yield ""

                parsed = await consume_sse_with_reasoning(SseResponse(), on_tool_call_delta=on_tool_call_delta)
            return LLMResponse(content=parsed[0] or None, tool_calls=parsed[1], finish_reason=parsed[2])
        await on_tool_call_delta({"index": 0, "call_id": "write-1", "name": "write_file"})
        assert edits == [{
            "version": 1, "call_id": "write-1", "tool": "write_file", "path": "",
            "phase": "start", "status": "editing", "pending": True,
            "added": 0, "deleted": 0, "approximate": True,
        }]
        await on_tool_call_delta({"index": 0, "arguments_delta": '{"content":"private'})
        await on_tool_call_delta({"index": 0, "arguments_delta": ' content","path":"a.txt"}'})
        assert len(edits) == 1
        assert not (tmp_path / "a.txt").exists()
        return LLMResponse(content=None, tool_calls=[ToolCallRequest(
            id="write-1", name="write_file", arguments={"path": "a.txt", "content": "private content"},
        )])

    provider.chat_stream_with_retry = chat_stream_with_retry
    events = output_events(on_progress=progress_cb)
    result = await AgentRunner().run(make_run_spec(
        provider, initial_messages=[{"role": "user", "content": "write"}], tools=Tools(),
        model="test-model", max_iterations=2, max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        workspace=tmp_path, hook=CompositeHook([
            AgentProgressHook(events, streaming=True),
            FileEditActivityHook(events=events, workspace=tmp_path),
        ]),
    ))

    assert result.final_content == "done"
    assert [(edit["phase"], edit["path"]) for edit in edits] == [
        ("start", ""), ("start", "a.txt"), ("end", "a.txt"),
    ]
    assert all(edit["call_id"] == expected_id for edit in edits)
    assert (tmp_path / "a.txt").read_text() == "private content"


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["cancelled", "exception", "error", "omitted"])
async def test_runner_closes_file_edit_preparation_when_no_tool_executes(tmp_path, ending):
    edits: list[dict] = []
    provider = MagicMock()

    async def progress_cb(_content, *, file_edit_events=None, **kwargs):
        edits.extend(file_edit_events or [])

    async def chat_stream_with_retry(*, on_tool_call_delta, **kwargs):
        await on_tool_call_delta({"call_id": "unfinished", "name": "edit_file"})
        assert edits and edits[-1]["status"] == "editing"
        if ending == "cancelled":
            raise asyncio.CancelledError
        if ending == "exception":
            raise ValueError("provider failed")
        if ending == "error":
            return LLMResponse(content="provider failed", finish_reason="error")
        return LLMResponse(content="no edit needed")

    provider.chat_stream_with_retry = chat_stream_with_retry
    tools = MagicMock()
    tools.get_definitions.return_value = []
    events = output_events(on_progress=progress_cb)
    run = AgentRunner().run(make_run_spec(
        provider, initial_messages=[{"role": "user", "content": "edit"}], tools=tools,
        model="test-model", max_iterations=1, max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        hook=CompositeHook([
            AgentProgressHook(events, streaming=True),
            FileEditActivityHook(events=events, workspace=tmp_path),
        ]),
    ))
    if ending == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await run
    elif ending == "exception":
        with pytest.raises(ValueError, match="provider failed"):
            await run
    else:
        await run

    assert [(edit["phase"], edit["call_id"]) for edit in edits] == [
        ("start", "unfinished"), ("error", "unfinished"),
    ]


@pytest.mark.asyncio
async def test_file_edit_preparation_remembers_index_identity_and_resets_each_iteration(tmp_path):
    edits: list[dict] = []

    async def progress_cb(_content, *, file_edit_events=None, **kwargs):
        edits.extend(file_edit_events or [])

    hook = FileEditActivityHook(events=output_events(on_progress=progress_cb), workspace=tmp_path)
    context = AgentHookContext(iteration=0, messages=[])
    await hook.before_iteration(context)
    await hook.on_provider_tool_event(context, {"index": 0, "call_id": "a"})
    await hook.on_provider_tool_event(context, {"index": 0, "name": "apply_patch"})
    await hook.on_provider_tool_event(context, {"index": 0, "arguments_delta": "private"})
    await hook.on_provider_tool_event(context, {"index": 1, "call_id": "b", "name": "read_file"})
    assert [(edit["call_id"], edit["tool"]) for edit in edits] == [("a", "apply_patch")]
    # A provider recovery may reuse an index before the next runner iteration.
    await hook.on_provider_tool_event(context, {"index": 1, "call_id": "retry", "name": "write_file"})
    await hook.on_provider_tool_event(context, {"index": 1, "name": "write_file"})
    assert [(edit["call_id"], edit["tool"]) for edit in edits] == [
        ("a", "apply_patch"), ("retry", "write_file"),
    ]
    await hook.after_iteration(context)
    await hook.before_iteration(AgentHookContext(iteration=1, messages=[]))
    await hook.on_provider_tool_event(context, {"index": 0, "call_id": "c", "name": "read_file"})
    assert [(edit["call_id"], edit["phase"]) for edit in edits] == [
        ("a", "start"), ("retry", "start"), ("a", "error"), ("retry", "error"),
    ]


@pytest.mark.asyncio
async def test_runner_routes_hosted_tool_events_to_structured_progress():
    provider = MagicMock()

    async def chat_stream_with_retry(*, on_content_delta, on_tool_call_delta, **kwargs):
        await on_tool_call_delta({
            "call_id": "local-call",
            "name": "read_file",
            "arguments_delta": "",
        })
        await on_tool_call_delta({
            "kind": "hosted_tool",
            "phase": "start",
            "call_id": "x-search-1",
            "name": "x_search",
            "arguments": {"query": "nanobot oauth"},
            "result": None,
        })
        await on_tool_call_delta({
            "kind": "hosted_tool",
            "phase": "end",
            "call_id": "x-search-1",
            "name": "x_search",
            "arguments": {"query": "nanobot oauth"},
            "result": {"name": "x_semantic_search"},
        })
        await on_content_delta("done")
        return LLMResponse(content="done", tool_calls=[], usage=None)

    provider.chat_stream_with_retry = chat_stream_with_retry
    provider.chat_with_retry = AsyncMock()
    tools = MagicMock()
    tools.get_definitions.return_value = []
    progress_events: list[dict] = []
    progress_text: list[str] = []
    streamed_text: list[str] = []

    async def progress_cb(content, *, tool_events=None, **kwargs):
        progress_text.append(content)
        if tool_events:
            progress_events.extend(tool_events)

    async def stream_cb(content: str) -> None:
        streamed_text.append(content)

    hook = AgentProgressHook(output_events(on_progress=progress_cb, on_stream=stream_cb), streaming=True)
    result = await AgentRunner().run(make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "search X"}],
        tools=tools,
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        hook=hook,
    ))

    assert result.final_content == "done"
    assert result.tools_used == []
    assert result.tool_events == []
    assert progress_events == [
        {
            "version": 1,
            "phase": "start",
            "call_id": "x-search-1",
            "name": "x_search",
            "arguments": {"query": "nanobot oauth"},
            "result": None,
            "error": None,
            "files": [],
            "embeds": [],
        },
        {
            "version": 1,
            "phase": "end",
            "call_id": "x-search-1",
            "name": "x_search",
            "arguments": {"query": "nanobot oauth"},
            "result": {"name": "x_semantic_search"},
            "error": None,
            "files": [],
            "embeds": [],
        },
    ]
    assert progress_text == ['search X "nanobot oauth"', ""]
    assert streamed_text == ["done"]
    provider.chat_with_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_runner_fails_pending_hosted_tool_when_model_request_fails():
    provider = MagicMock()

    async def chat_stream_with_retry(*, on_tool_call_delta, **kwargs):
        await on_tool_call_delta({
            "kind": "hosted_tool",
            "phase": "start",
            "call_id": "x-search-failed",
            "name": "x_search",
            "arguments": {"query": "nanobot oauth"},
            "result": None,
        })
        return LLMResponse(
            content="hosted search backend failed",
            finish_reason="error",
        )

    provider.chat_stream_with_retry = chat_stream_with_retry
    provider.chat_with_retry = AsyncMock()
    tools = MagicMock()
    tools.get_definitions.return_value = []
    progress_events: list[dict] = []

    async def progress_cb(content, *, tool_events=None, **kwargs):
        if tool_events:
            progress_events.extend(tool_events)

    async def stream_cb(_content: str) -> None:
        pass

    hook = AgentProgressHook(output_events(on_progress=progress_cb, on_stream=stream_cb), streaming=True)
    result = await AgentRunner().run(make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "search X"}],
        tools=tools,
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        hook=hook,
    ))

    assert result.stop_reason == "error"
    assert [(event["phase"], event["call_id"]) for event in progress_events] == [
        ("start", "x-search-failed"),
        ("error", "x-search-failed"),
    ]
    assert progress_events[-1] == {
        "version": 1,
        "phase": "error",
        "call_id": "x-search-failed",
        "name": "x_search",
        "arguments": {"query": "nanobot oauth"},
        "result": None,
        "error": "hosted search backend failed",
        "files": [],
        "embeds": [],
    }
    provider.chat_with_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_runner_emits_write_file_diff_from_tool_execution_snapshots(tmp_path):
    provider = MagicMock()
    call_count = 0
    progress_events: list[dict] = []
    (tmp_path / "big.txt").write_text("old\n", encoding="utf-8")

    async def progress_cb(content, *, file_edit_events=None, **kwargs):
        if file_edit_events:
            progress_events.extend(file_edit_events)

    tool = WriteFileTool(workspace=tmp_path)

    class Tools:
        def get_definitions(self):
            return [{"type": "function", "function": {"name": "write_file"}}]

        def prepare_call(self, name, params):
            return tool, params, None

    async def chat_with_retry(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="call-write",
                        name="write_file",
                        arguments={"path": "big.txt", "content": "line\n" * 24},
                    )
                ],
                usage=None,
            )
        return LLMResponse(content="done", tool_calls=[], usage=None)

    provider.chat_with_retry = chat_with_retry
    tools = Tools()

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "write a large file"}],
        tools=tools,
        model="test-model",
        max_iterations=2,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        workspace=tmp_path,
        hook=FileEditActivityHook(events=output_events(on_progress=progress_cb), workspace=tmp_path),
    ))

    assert result.final_content == "done"
    assert progress_events[0]["phase"] == "start"
    assert progress_events[0]["added"] == 0
    assert progress_events[0]["deleted"] == 0
    assert any(
        not event["approximate"]
        and event["phase"] == "end"
        and event["added"] == 24
        and event["deleted"] == 1
        and event["diff"]["format"] == "unified"
        for event in progress_events
    )


@pytest.mark.asyncio
async def test_runner_emits_edit_file_diff_from_tool_execution_snapshots(tmp_path):
    provider = MagicMock()
    call_count = 0
    progress_events: list[dict] = []
    target = tmp_path / "notes.txt"
    target.write_text("old\nkeep\n", encoding="utf-8")

    async def progress_cb(content, *, file_edit_events=None, **kwargs):
        if file_edit_events:
            progress_events.extend(file_edit_events)

    tool = EditFileTool(workspace=tmp_path)

    class Tools:
        def get_definitions(self):
            return [{"type": "function", "function": {"name": "edit_file"}}]

        def prepare_call(self, name, params):
            return tool, params, None

    async def chat_with_retry(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="call-edit",
                        name="edit_file",
                        arguments={
                            "path": "notes.txt",
                            "old_text": "old\nkeep\n",
                            "new_text": "new\nkeep\nextra\n",
                        },
                    )
                ],
                usage=None,
            )
        return LLMResponse(content="done", tool_calls=[], usage=None)

    provider.chat_with_retry = chat_with_retry
    tools = Tools()

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "edit a file"}],
        tools=tools,
        model="test-model",
        max_iterations=2,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        workspace=tmp_path,
        hook=FileEditActivityHook(events=output_events(on_progress=progress_cb), workspace=tmp_path),
    ))

    assert result.final_content == "done"
    assert any(
        event["tool"] == "edit_file"
        and not event["approximate"]
        and event["phase"] == "end"
        and event["added"] == 2
        and event["deleted"] == 1
        and event["diff"]["format"] == "unified"
        for event in progress_events
    )


@pytest.mark.asyncio
async def test_runner_marks_file_edit_activity_failed_when_tool_errors(tmp_path):
    provider = MagicMock()
    call_count = 0
    progress_events: list[dict] = []

    async def progress_cb(content, *, file_edit_events=None, **kwargs):
        if file_edit_events:
            progress_events.extend(file_edit_events)

    tool = WriteFileTool(workspace=tmp_path)

    class Tools:
        def get_definitions(self):
            return [{"type": "function", "function": {"name": "write_file"}}]

        def prepare_call(self, name, params):
            return tool, params, None

    async def chat_with_retry(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id="call-write",
                        name="write_file",
                        arguments={"path": "aborted.txt"},
                    )
                ],
                usage=None,
            )
        return LLMResponse(content="done", tool_calls=[], usage=None)

    provider.chat_with_retry = chat_with_retry
    tools = Tools()

    runner = AgentRunner()
    result = await runner.run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "write a file"}],
        tools=tools,
        model="test-model",
        max_iterations=2,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        workspace=tmp_path,
        hook=FileEditActivityHook(events=output_events(on_progress=progress_cb), workspace=tmp_path),
    ))

    assert result.stop_reason == "completed"
    assert progress_events[-1]["path"] == "aborted.txt"
    assert progress_events[-1]["phase"] == "error"
    assert progress_events[-1]["status"] == "error"


@pytest.mark.asyncio
async def test_runner_marks_file_edit_activity_failed_when_cancelled(tmp_path):
    provider = MagicMock()
    progress_events: list[dict] = []
    executing = asyncio.Event()
    target = tmp_path / "cancelled.txt"
    target.write_text("old\n", encoding="utf-8")

    async def progress_cb(content, *, file_edit_events=None, **kwargs):
        if file_edit_events:
            progress_events.extend(file_edit_events)

    class SlowWriteTool(WriteFileTool):
        async def execute(self, path=None, content=None, **kwargs):
            executing.set()
            await asyncio.sleep(60)
            return "ok"

    tool = SlowWriteTool(workspace=tmp_path)

    class Tools:
        def get_definitions(self):
            return [{"type": "function", "function": {"name": "write_file"}}]

        def prepare_call(self, name, params):
            return tool, params, None

    async def chat_with_retry(**kwargs):
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCallRequest(
                    id="call-write",
                    name="write_file",
                    arguments={"path": "cancelled.txt", "content": "new\n"},
                )
            ],
            usage=None,
        )

    provider.chat_with_retry = chat_with_retry
    tools = Tools()

    runner = AgentRunner()
    task = asyncio.create_task(runner.run(make_run_spec(provider,
        initial_messages=[{"role": "user", "content": "write a file"}],
        tools=tools,
        model="test-model",
        max_iterations=2,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        workspace=tmp_path,
        hook=FileEditActivityHook(events=output_events(on_progress=progress_cb), workspace=tmp_path),
    )))
    await asyncio.wait_for(executing.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert [event["phase"] for event in progress_events] == ["start", "error"]
    assert progress_events[-1]["path"] == "cancelled.txt"
    assert progress_events[-1]["status"] == "error"
    assert progress_events[-1]["error"] == "Task interrupted before this tool finished."
