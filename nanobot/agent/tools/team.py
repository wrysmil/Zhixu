"""``team_dispatch``：团队 Leader 唯一的派活入口（契约 §5）。

这个工具**不由 loader 自动发现**：它只在 Team Leader 回合被宿主挂进 registry，普通聊天
连它的名字都看不到。模型能传的只有 ``batchId`` 与任务声明；runId、sessionKey、成员白名单、
工作区权限一律来自宿主注入的运行把手——否则「谁能派活」就变成模型自己说了算。
"""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Protocol

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.schema import (
    ArraySchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.teams.database import StorageUnavailable
from nanobot.teams.models import (
    MAX_ID_CHARS,
    TeamTaskInput,
    TeamTaskResult,
    TeamValidationError,
)
from nanobot.teams.repository import DispatchKeyConflict, IllegalTaskTransition

_REQUIRED_TASK_FIELDS = ("key", "memberId", "title", "instruction", "acceptance")


class TeamRunTarget(Protocol):
    """工具需要的运行把手；:class:`nanobot.teams.scheduler.TeamRunHandle` 满足它。"""

    run_id: str

    async def dispatch(
        self, *, batch_id: str, tasks: Sequence[TeamTaskInput]
    ) -> list[TeamTaskResult]: ...

    async def set_leader_action(self, action: str) -> Any: ...


@tool_parameters(
    tool_parameters_schema(
        batchId=StringSchema(
            "本批次幂等键：同一批次重发用同一个 batchId，换一批任务必须换一个新值。"
        ),
        tasks=ArraySchema(
            ObjectSchema(
                key=StringSchema("批次内短标识，供同批任务的 dependsOn 引用。"),
                memberId=StringSchema(
                    "团队成员槽位 id，必须来自本次运行冻结的团队名册（不是档案 id）。"
                ),
                title=StringSchema("一句话说清这件事要产出什么。"),
                instruction=StringSchema(
                    "交给成员的完整指令；跨批次的上游结论要由你亲手写进这里。"
                ),
                acceptance=StringSchema("怎样算完成的可验收口径。"),
                dependsOn=ArraySchema(
                    StringSchema(""),
                    description="同批次内必须先成功的任务 key；跨批次不要写在这里。",
                ),
                resourceKeys=ArraySchema(
                    StringSchema(""),
                    description=(
                        "写集（如 file:notes.md、shell）。互相冲突的声明会串行；"
                        "不声明则与所有未声明写集的任务串行。"
                    ),
                ),
                required=list(_REQUIRED_TASK_FIELDS),
            ),
            description="本批任务声明；dependsOn 只能引用同一批里的 key。",
            min_items=1,
        ),
        required=["batchId", "tasks"],
    )
)
class TeamDispatchTool(Tool):
    """把一批任务交给团队调度器，等它们全部进入终态后返回结构化结果。"""

    # 契约 §5：调度工具由 Team 服务注入，不能进普通聊天的自动发现。
    _plugin_discoverable = False

    def __init__(self, run: TeamRunTarget | None = None) -> None:
        self._run = run

    @property
    def name(self) -> str:
        return "team_dispatch"

    @property
    def description(self) -> str:
        return (
            "Dispatch a batch of tasks to the team members of this run and wait until every "
            "task reaches a terminal state, then return their structured results. Members are "
            "addressed by memberId from the frozen team roster; unknown members, dependsOn "
            "outside this batch and dependency cycles reject the whole batch without starting "
            "anything. Cross-batch results are not carried automatically: read the returned "
            "summaries and write what the next batch needs into its instructions."
        )

    @property
    def exclusive(self) -> bool:
        """一批未收口就再起一批，会让轮次配额与资源锁顺序变成竞态。"""
        return True

    async def execute(
        self,
        # 契约 §5：模型可见字段用 camelCase，与 TeamTaskInput 线格式同一口径。
        batchId: str,  # noqa: N803
        tasks: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ToolResult | str:
        """整批派发到收口；领域拒绝当场回给模型，不打崩本回合。"""
        if self._run is None:
            return ToolResult.error("Error: team_dispatch has no team run bound to this turn")
        if not batchId.strip():
            return ToolResult.error("Error: team_dispatch requires a non-empty batchId")
        # 幂等键要落库并参与 payload 比对，不能让模型拿一个几十 KB 的串灌进来。
        if len(batchId) > MAX_ID_CHARS:
            return ToolResult.error(
                f"Error: team_dispatch requires batchId of at most {MAX_ID_CHARS} characters"
            )
        if not isinstance(tasks, list) or not tasks:
            return ToolResult.error(
                "Error: team_dispatch requires at least one task in tasks"
            )
        try:
            declared = [TeamTaskInput.from_dict(item) for item in tasks]
        except TeamValidationError as error:
            return ToolResult.error(f"Error: invalid team_dispatch tasks: {error}")

        await self._run.set_leader_action("waiting")
        try:
            results = await self._run.dispatch(batch_id=batchId.strip(), tasks=declared)
        except (TeamValidationError, DispatchKeyConflict) as error:
            # 派发被拒时 Leader 还在改方案，不是在看结果。
            await self._run.set_leader_action("planning")
            return ToolResult.error(f"Error: team_dispatch rejected: {error}")
        except (IllegalTaskTransition, StorageUnavailable) as error:
            # 存储忙或状态转移非法不是「方案错了」，是这一批没能落地：照样回一句可读的话，
            # 不把异常穿透成打崩整个 Leader 回合的 traceback（契约 §5）。
            await self._run.set_leader_action("planning")
            return ToolResult.error(f"Error: team_dispatch could not be persisted: {error}")
        await self._run.set_leader_action("reviewing")
        return json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2)
