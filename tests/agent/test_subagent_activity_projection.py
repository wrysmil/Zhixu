"""子 Agent 工具结束时把参数/输出投影写进执行账本。"""

from pathlib import Path
from typing import Any

import pytest

from nanobot.agent.subagent import _ExecutionObservation, _SubagentHook
from nanobot.subagent_executions import ExecutionRepository


def _observation(tmp_path: Path) -> tuple[ExecutionRepository, str, _ExecutionObservation]:
    repo = ExecutionRepository(tmp_path)
    created = repo.create(
        chat_id="chat",
        parent_turn_id="turn",
        parent_tool_call_id="call",
        name="助手",
        title="任务",
    )
    repo.start(created.invocation_id)
    return repo, created.invocation_id, _ExecutionObservation(
        repo, created.invocation_id, "chat", None,
    )


class _Call:
    def __init__(self, name: str, arguments: dict[str, Any], call_id: str = "call-1") -> None:
        self.name = name
        self.arguments = arguments
        self.id = call_id


@pytest.mark.asyncio
async def test_successful_tool_writes_projected_detail_and_output(tmp_path: Path):
    repo, invocation_id, observation = _observation(tmp_path)
    hook = _SubagentHook("task", observation=observation)
    call = _Call("read_file", {"path": "src/app.py"})
    await hook.before_execute_tool(None, call, None, None)
    await hook.after_execute_tool(None, call, None, None, "l1\nl2\nl3")

    stored = repo.detail("chat", invocation_id).activities[0]
    assert (stored.status, stored.detail, stored.output) == ("succeeded", "path=src/app.py", "lines=3")
    repo.close()


@pytest.mark.asyncio
async def test_failed_tool_keeps_only_the_error_line(tmp_path: Path):
    repo, invocation_id, observation = _observation(tmp_path)
    hook = _SubagentHook("task", observation=observation)
    call = _Call("read_file", {"path": "missing.py"})
    await hook.before_execute_tool(None, call, None, None)
    await hook.on_execute_tool_error(
        None, call, None, None, "Error: File not found: missing.py\nstack tail",
    )

    stored = repo.detail("chat", invocation_id).activities[0]
    assert stored.status == "failed"
    assert stored.output == "error=Error: File not found: missing.py"
    repo.close()


@pytest.mark.asyncio
async def test_unlisted_tool_records_no_detail_or_output(tmp_path: Path):
    repo, invocation_id, observation = _observation(tmp_path)
    hook = _SubagentHook("task", observation=observation)
    call = _Call("spawn", {"task": "private task", "token": "sk-abcdef123456"})
    await hook.before_execute_tool(None, call, None, None)
    await hook.after_execute_tool(None, call, None, None, "done")

    stored = repo.detail("chat", invocation_id).activities[0]
    assert stored.detail is None and stored.output is None
    repo.close()


@pytest.mark.asyncio
async def test_credentials_in_arguments_never_reach_the_ledger(tmp_path: Path):
    repo, invocation_id, observation = _observation(tmp_path)
    hook = _SubagentHook("task", observation=observation)
    call = _Call("read_file", {"path": "/Users/mima/.ssh/id_rsa"})
    await hook.before_execute_tool(None, call, None, None)
    await hook.after_execute_tool(None, call, None, None, "PRIVATE KEY sk-live-abcdef123456")

    stored = repo.detail("chat", invocation_id).activities[0]
    assert stored.detail == "path=~/.ssh/id_rsa"
    # read_file 成功只给行数，正文不进账本。
    assert stored.output == "lines=1"
    repo.close()
