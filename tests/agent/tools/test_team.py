"""``team_dispatch`` 工具层：模型只说 batchId + tasks，运行与权限由宿主注入。

四条契约口径（contract §5）都在这层可测：

1. 工具**不自动装载**（``_plugin_discoverable = False``），普通聊天的 registry 里没有它，
   模型也无法靠参数里的 runId 指到别的运行上。
2. 参数对象是严格的：多一个 ``runId`` 当场报错，不静默忽略。
3. 整批结果结构化返回，Leader 只看到一次。
4. 调度层的校验失败变成 ``ToolResult.error`` 回到模型，而不是把整个回合打崩。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from nanobot.agent.tools.base import ToolResult
from nanobot.agent.tools.loader import ToolLoader
from nanobot.agent.tools.team import TeamDispatchTool
from nanobot.teams.models import TeamTaskInput, TeamTaskResult, TeamValidationError
from nanobot.teams.repository import DispatchKeyConflict


def is_error(result: object) -> bool:
    return isinstance(result, ToolResult) and result.is_error

TASK_DECLARE = {
    "key": "research",
    "memberId": "m-researcher",
    "title": "查阅资料",
    "instruction": "检索官方文档并给出来源",
    "acceptance": "至少两条来源",
    "dependsOn": [],
    "resourceKeys": ["file:notes.md"],
}


def _result(status: str = "succeeded", summary: str = "查到了两条来源") -> TeamTaskResult:
    return TeamTaskResult(
        task_id="task-1",
        attempt_id="att-1",
        member_id="m-researcher",
        agent_id="researcher",
        status=status,  # pyright: ignore[reportArgumentType] 测试只取用到的两个取值
        stop_reason="completed" if status == "succeeded" else "error",
        summary=summary,
        artifacts=[],
        error_code=None if status == "succeeded" else "member_failed",
    )


class FakeRun:
    """宿主注入的运行把手：工具只能用我们给它的那一个运行。"""

    def __init__(self, *, results: list[TeamTaskResult] | None = None, error: Exception | None = None) -> None:
        self.dispatched: list[tuple[str, list[TeamTaskInput]]] = []
        self.actions: list[str] = []
        self._results = results if results is not None else [_result()]
        self._error = error
        self.run_id = "run-1"

    async def dispatch(self, *, batch_id: str, tasks: Any) -> list[TeamTaskResult]:
        if self._error is not None:
            raise self._error
        self.dispatched.append((batch_id, list(tasks)))
        return list(self._results)

    async def set_leader_action(self, action: str) -> None:
        self.actions.append(action)


def _tool(run: FakeRun) -> TeamDispatchTool:
    return TeamDispatchTool(run)  # pyright: ignore[reportArgumentType] 替身只需同名方法


async def test_dispatch_returns_structured_results_once() -> None:
    run = FakeRun()
    raw = await _tool(run).execute(batchId="batch-1", tasks=[TASK_DECLARE])

    payload = json.loads(str(raw))
    assert payload == [
        {
            "taskId": "task-1",
            "attemptId": "att-1",
            "memberId": "m-researcher",
            "agentId": "researcher",
            "status": "succeeded",
            "stopReason": "completed",
            "summary": "查到了两条来源",
            "artifacts": [],
            "errorCode": None,
            "usage": None,
        }
    ]
    batch_id, tasks = run.dispatched[0]
    assert batch_id == "batch-1"
    assert tasks == [TeamTaskInput.from_dict(TASK_DECLARE)]


async def test_leader_action_waits_during_dispatch_then_reviews() -> None:
    run = FakeRun()
    await _tool(run).execute(batchId="batch-1", tasks=[TASK_DECLARE])
    assert run.actions == ["waiting", "reviewing"]


@pytest.mark.parametrize(
    ("error", "match"),
    [
        (TeamValidationError("unknown memberId 'outsider'"), "memberId"),
        (DispatchKeyConflict("DISPATCH_KEY_CONFLICT run_id=run-1"), "DISPATCH_KEY_CONFLICT"),
    ],
)
async def test_dispatch_rejections_come_back_as_tool_errors(error: Exception, match: str) -> None:
    run = FakeRun(error=error)
    raw = await _tool(run).execute(batchId="batch-1", tasks=[TASK_DECLARE])

    assert is_error(raw)
    assert match in str(raw)
    assert run.dispatched == []


async def test_task_declarations_are_validated_before_the_run_is_touched() -> None:
    run = FakeRun()
    raw = await _tool(run).execute(batchId="batch-1", tasks=[{"key": "research"}])

    assert is_error(raw)
    assert "memberId" in str(raw)
    assert run.dispatched == []


async def test_empty_batch_is_rejected_before_dispatch() -> None:
    run = FakeRun()
    raw = await _tool(run).execute(batchId="batch-1", tasks=[])
    assert is_error(raw)
    assert run.dispatched == []


def test_model_visible_schema_has_only_batch_and_tasks() -> None:
    params = TeamDispatchTool().parameters
    assert sorted(params["properties"]) == ["batchId", "tasks"]
    assert params["additionalProperties"] is False
    assert sorted(params["properties"]["tasks"]["items"]["required"]) == [
        "acceptance",
        "instruction",
        "key",
        "memberId",
        "title",
    ]


def test_run_and_session_are_not_model_supplied() -> None:
    """宿主注入的运行不能被参数覆盖：多写的键在校验阶段就挡掉。"""
    tool = _tool(FakeRun())
    errors = tool.validate_params(
        {"batchId": "batch-1", "tasks": [TASK_DECLARE], "runId": "run-other", "sessionKey": "cli:x"}
    )
    assert errors
    assert any("runId" in item for item in errors)


def test_tool_is_not_auto_loaded_into_normal_chats() -> None:
    """契约 §5：调度工具由 Team 服务注入；``spawn`` 那条自动发现路径必须跳过它。"""
    assert TeamDispatchTool._plugin_discoverable is False
    discovered = {cls.__name__ for cls in ToolLoader().discover()}
    assert "TeamDispatchTool" not in discovered


def test_dispatch_runs_alone() -> None:
    """一批未收口就再起一批会让轮次配额与锁顺序变成竞态，所以独占执行。"""
    tool = _tool(FakeRun())
    assert tool.exclusive is True
    assert tool.concurrency_safe is False
