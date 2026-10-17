"""团队调度：把一批 ``team_dispatch`` 声明变成有依赖顺序、有配额、可取消的子执行。

三件事只在这一层做（契约 §5、§7）：

1. **整批校验**：成员、依赖与 DAG 有任何一条不成立就整批报错，一张任务都不写 ——
   不能出现「团队跑了一半」。
2. **配额**：团队并发、同成员一次、资源锁（未声明写集一律共用 ``workspace-write``）。
   共享的 ``SubagentManager._run_slots`` 仍在外层兜住全局上限，所以这里不复制配额。
3. **收口**：排队、执行、超时、取消与终态全部写回 SQLite，由 ``run.finished``
   给 UI 唯一一个终态。

这里没有第二条 LLM 循环：成员执行一律走 :class:`TeamMemberExecutor` → 共享 Runner。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from loguru import logger as default_logger

from nanobot.agents.models import AgentProfile, AgentSelection
from nanobot.agents.runtime import ResolvedAgent, render_profile_prompt, resolve_selection
from nanobot.security.workspace_access import WorkspaceScope
from nanobot.teams.events import TeamPublisherFactory, TeamRunEventSink, render_activity_action
from nanobot.teams.execution import NO_EVENTS, TeamExecutionRequest
from nanobot.teams.models import (
    MAX_TASK_RESULT_CHARS,
    MAX_TASK_SUMMARY_CHARS,
    AgentProfileSnapshot,
    TeamBinding,
    TeamRevisionMember,
    TeamRevisionSnapshot,
    TeamRunSnapshot,
    TeamTaskInput,
    TeamTaskResult,
    TeamTaskSnapshot,
    TeamTaskUsage,
    TeamValidationError,
)
from nanobot.teams.repository import (
    MAX_EVENT_LIMIT,
    RUN_TERMINAL_STATUSES,
    TASK_TERMINAL_STATUSES,
    DispatchKeyConflict,
    TeamRepository,
    payload_digest,
)

# 契约 §7：没有声明写集的任务共用这一把锁 —— 宁慢不冲突。
UNDECLARED_WRITE_RESOURCE = "workspace-write"

# 上游结果拼进下游 instruction 的总预算：下游要看懂依赖，但不能吃掉整个上下文窗口。
UPSTREAM_CONTEXT_BUDGET = 8000

#: 下游准备阶段的「全文展开」出口：给定上游 taskId，返回该任务的完整产出。
#: 只有写作协作批次注入它（长稿不得被 8000 字符摘要截断）；不注入即保持通用摘要语义。
UpstreamExpander = Callable[[str], Awaitable[str | None]]

#: 写作批次的下游全文预算：仍受 ``MAX_TASK_INSTRUCTION_CHARS=32000`` 约束。
WRITING_UPSTREAM_BUDGET = 24_000

#: 事件观察者：按 seq 批量收到已落盘的 ``TeamEvent``（协作域用它桥接 CollabRunner）。
#: 与总线发布并行，互不影响：出口缺失不改变任务怎么跑。
EventObserver = Callable[[Sequence[Any]], Awaitable[None]]

#: 按 ``(handle, memberId)`` 给出可信注入工具（写作会话）；返回空序列即无扩展。
ExtraToolsProvider = Callable[["TeamRunHandle", str], Sequence[Any]]

# 子执行结论 → 任务终态。``interrupted`` 只能由恢复路径写，调度器这里拿不到。
_OUTCOME_TO_STATUS = {"succeeded": "succeeded", "failed": "failed", "cancelled": "cancelled"}


class MemberExecutor(Protocol):
    """调度器需要的子执行面；:class:`TeamMemberExecutor` 与测试替身都满足它。"""

    async def execute_member(
        self, request: TeamExecutionRequest, *, events: Any = NO_EVENTS
    ) -> Any: ...


class MemberCatalog(Protocol):
    """把冻结档案快照变成运行时约束；**不**按 agentId 重新读盘。"""

    def resolve(self, profile: AgentProfileSnapshot, *, workspace: Path) -> ResolvedAgent: ...


class SnapshotMemberCatalog:
    """用运行快照里的档案内容装配 :class:`ResolvedAgent`。

    工具白名单要和「子执行真实存在的工具」求交：档案里写了一个已被关掉的工具时，
    给它一个不存在的名字等于撒谎（与 ``AgentProfileRuntime.resolve`` 同一口径）。
    """

    def __init__(
        self,
        *,
        known_tools: Sequence[str] | Callable[[], Sequence[str]],
        known_skills: Sequence[str] | Callable[[], Sequence[str]] = (),
    ) -> None:
        # 允许传可调用对象：gateway 装配之后 MCP 还会 reload，技能也会在设置里开关，
        # 抓一次死名单会让成员拿不到后来才出现的工具（求交变成过期目录）。
        self._known_tools = known_tools
        self._known_skills = known_skills

    def _tools(self) -> list[str]:
        value = self._known_tools
        return list(value()) if callable(value) else list(value)

    def _skills(self) -> list[str]:
        value = self._known_skills
        return list(value()) if callable(value) else list(value)

    def resolve(self, profile: AgentProfileSnapshot, *, workspace: Path) -> ResolvedAgent:
        known_skills = self._skills()
        tool_names = frozenset(
            resolve_selection(
                AgentSelection(mode=profile.tools.mode, entries=list(profile.tools.entries)),
                self._tools(),
            )
        )
        enabled_skills = resolve_selection(
            AgentSelection(mode=profile.skills.mode, entries=list(profile.skills.entries)),
            known_skills,
        )
        return ResolvedAgent(
            id=profile.id,
            name=profile.name,
            description="",
            model_id=profile.model_id,
            prompt=render_profile_prompt(
                AgentProfile(id=profile.id, name=profile.name, prompt=profile.prompt),
                workspace=workspace,
                enabled_skills=sorted(enabled_skills),
                enabled_tools=sorted(tool_names),
            ),
            tool_names=tool_names,
            skill_exclude=frozenset(set(known_skills) - enabled_skills),
        )


@dataclass(frozen=True)
class _PlannedTask:
    """一条已落库的任务声明。批次短 key 只活在内存里，持久的是 taskId。"""

    key: str
    task_id: str
    title: str
    member: TeamRevisionMember
    instruction: str
    acceptance: str
    depends_on: tuple[str, ...]
    resources: tuple[str, ...]


def _lock_names(resource_keys: Sequence[str]) -> tuple[str, ...]:
    """固定顺序取锁，否则两个互相争资源的孩子会把彼此锁死。"""
    declared = sorted({str(item) for item in resource_keys if str(item).strip()})
    return tuple(declared or [UNDECLARED_WRITE_RESOURCE])


class TeamRunHandle:
    """一次团队运行的活状态：限额、锁、在场的子任务与取消口径都在这里。"""

    def __init__(
        self,
        *,
        repository: TeamRepository,
        executor: MemberExecutor,
        members: MemberCatalog,
        runtime: Any,
        run: TeamRunSnapshot,
        revision: TeamRevisionSnapshot,
        session_key: str,
        workspace: Path,
        workspace_scope: WorkspaceScope | None,
        origin_message_id: str | None,
        clock: Callable[[], float],
        task_timeout_seconds: float | None,
        events: TeamRunEventSink | None = None,
        upstream_expander: UpstreamExpander | None = None,
        upstream_budget: int = UPSTREAM_CONTEXT_BUDGET,
        observers: list[EventObserver] | None = None,
        extra_tools_provider: ExtraToolsProvider | None = None,
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._members = members
        self._runtime = runtime
        self._revision = revision
        self._limits = revision.definition.limits
        self._session_key = session_key
        self._events = events
        self._workspace = workspace
        self._workspace_scope = workspace_scope
        self._origin_message_id = origin_message_id
        self._clock = clock
        self._task_timeout_override = task_timeout_seconds
        self._upstream_expander = upstream_expander
        self._upstream_budget = int(upstream_budget)
        self._extra_tools_provider = extra_tools_provider
        # 与调度器共享同一个列表对象：运行起跑后再 ``observe()`` 也立刻生效。
        self._observers: list[EventObserver] = observers if observers is not None else []
        self.run_id = run.run_id
        self.chat_id = run.chat_id
        self.turn_id = run.turn_id
        self._started_monotonic = clock()
        self._slots = {item.member.member_id: item for item in revision.members}
        self._semaphore = asyncio.Semaphore(self._limits.max_concurrency)
        self._member_locks = {member_id: asyncio.Lock() for member_id in self._slots}
        self._resource_locks: dict[str, asyncio.Lock] = {}
        self._children: dict[str, asyncio.Task[TeamTaskResult]] = {}
        self._batches: dict[str, asyncio.Future[list[TeamTaskResult]]] = {}
        self._done_events: dict[str, asyncio.Event] = {}
        self._done_status: dict[str, str] = {}
        self._results: dict[str, TeamTaskResult] = {}
        self._planned: dict[str, _PlannedTask] = {}
        self._cancel_requested = asyncio.Event()
        self._cancelled_task_ids: set[str] = set()
        self._rounds = 0
        self._closed = False
        # 事件出口按 seq 增量读库再投递：序号只有一个真值来源（team_runs.last_seq），
        # 调度器不自造序号，才能保证「落盘的」和「广播的」是同一批。从 0 开始，
        # 首投一定会带出 run.started；重复投递由前端按 seq 去重（契约 §6 至少一次）。
        self._published_seq = 0
        self._publish_lock = asyncio.Lock()

    # ------------------------------------------------------------------ 读取

    @property
    def session_key(self) -> str:
        return self._session_key

    @property
    def revision(self) -> TeamRevisionSnapshot:
        return self._revision

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def accepts_writes(self) -> bool:
        """晚到的工具写入是否还能落库：取消或收口之后一律拒绝。

        写作工具在每次调用前读这个属性。模型可能在上一次工具返回后继续生成，
        停止请求到达时后台模型循环未必立刻结束；没有这道闸，取消后仍会覆盖正文。
        """
        return not self._closed and not self._cancel_requested.is_set()

    def run(self) -> TeamRunSnapshot:
        return self._repository.get_run(self.run_id)

    # ------------------------------------------------------------------ 派单

    async def dispatch(
        self, *, batch_id: str, tasks: Sequence[TeamTaskInput]
    ) -> list[TeamTaskResult]:
        """跑完整批任务，返回与声明同序的结构化结果（契约 §5）。"""
        if self._closed:
            raise TeamValidationError(f"team run {self.run_id} is finished; dispatch refused")
        if self._cancel_requested.is_set():
            raise TeamValidationError(f"team run {self.run_id} is cancelling; dispatch refused")
        inflight = self._batches.get(batch_id)
        if inflight is not None:
            return list(await inflight)
        declared = list(tasks)
        # 任务数预算按**运行**累计（契约 §5 的预算表：每次运行 16，最高 64，含返工）：
        # 只看单批的话，Leader 换几个 batchId 就能把一次运行撑到 4 倍。
        already = await self._call(self._repository.task_count, self.run_id)
        ordered = self._validate(declared, already=already)
        payload = [item.to_dict() for item in declared]
        recorded = await self._call(self._repository.get_dispatch, self.run_id, batch_id)
        if recorded is not None:
            if recorded["payloadHash"] != payload_digest(payload):
                raise DispatchKeyConflict(
                    f"DISPATCH_KEY_CONFLICT run_id={self.run_id} batch_id={batch_id}"
                )
            return self._replay(list(recorded["taskIds"]))
        if self._rounds + 1 > self._limits.max_dispatch_rounds:
            raise TeamValidationError(
                f"maxDispatchRounds={self._limits.max_dispatch_rounds} exhausted for run "
                f"{self.run_id}"
            )
        self._rounds += 1
        planned = await self._persist_batch(batch_id, declared, ordered)
        children = {
            item.task_id: asyncio.create_task(
                self._run_child(item), name=f"team-task-{self.run_id}-{item.key}"
            )
            for item in planned
        }
        for task_id in children:
            self._done_events[task_id] = asyncio.Event()
        self._children.update(children)
        batch = asyncio.gather(*children.values())
        self._batches[batch_id] = batch
        try:
            results = list(await batch)
        except asyncio.CancelledError:
            # 外层（回合）取消：先取消每个孩子并 await 清理，再把取消继续往上抛。
            for child in children.values():
                child.cancel()
            await asyncio.gather(*children.values(), return_exceptions=True)
            raise
        finally:
            self._batches.pop(batch_id, None)
            for task_id in children:
                self._children.pop(task_id, None)
        if self._cancel_requested.is_set():
            await self._settle_cancelled()
        return results

    async def run_pending(self) -> list[TeamTaskResult]:
        """执行已经落盘的 ``pending`` 任务（重试复制出来的计划），不重新建任务。

        和 :meth:`dispatch` 走同一批孩子/锁/取消口径，差别只在声明来自数据库而不是模型：
        ``key`` 用持久 taskId，批次幂等由调用方的 requestId 负责。
        """
        if self._closed:
            raise TeamValidationError(f"team run {self.run_id} is finished; dispatch refused")
        if self._cancel_requested.is_set():
            raise TeamValidationError(f"team run {self.run_id} is cancelling; dispatch refused")
        rows = await self._call(self._repository.list_pending_tasks, self.run_id)
        if not rows:
            return []
        planned: list[_PlannedTask] = []
        for row in rows:
            slot = self._slots.get(str(row["memberId"]))
            if slot is None:
                raise TeamValidationError(
                    f"unknown memberId {row['memberId']!r} is not a team member of "
                    f"{self._revision.definition.id!r}"
                )
            item = _PlannedTask(
                key=str(row["taskId"]),
                task_id=str(row["taskId"]),
                title=str(row["title"]),
                member=slot,
                instruction=str(row["instruction"]),
                acceptance=str(row["acceptance"]),
                depends_on=tuple(str(dep) for dep in row["dependsOn"]),
                resources=_lock_names(list(row["resourceKeys"])),
            )
            self._planned[item.task_id] = item
            planned.append(item)
        self._rounds += 1
        children = {
            item.task_id: asyncio.create_task(
                self._run_child(item), name=f"team-task-{self.run_id}-{item.key}"
            )
            for item in planned
        }
        for task_id in children:
            self._done_events[task_id] = asyncio.Event()
        self._children.update(children)
        try:
            results = list(await asyncio.gather(*children.values()))
        finally:
            for task_id in children:
                self._children.pop(task_id, None)
        if self._cancel_requested.is_set():
            await self._settle_cancelled()
        return results

    def _validate(self, declared: Sequence[TeamTaskInput], *, already: int = 0) -> list[TeamTaskInput]:
        """整批前置校验：任何一条不成立都不写库、不起任务；返回可执行的拓扑序。"""
        if not declared:
            raise TeamValidationError("team dispatch requires at least one task in tasks")
        if already + len(declared) > self._limits.max_tasks:
            raise TeamValidationError(
                f"maxTasks={self._limits.max_tasks} exceeded for this run: "
                f"{already} already dispatched + {len(declared)} declared"
            )
        keys = [item.key for item in declared]
        if len(set(keys)) != len(keys):
            raise TeamValidationError("team dispatch declares a duplicate task key")
        for item in declared:
            if item.member_id not in self._slots:
                raise TeamValidationError(
                    f"unknown memberId {item.member_id!r} is not a team member of "
                    f"{self._revision.definition.id!r}"
                )
        key_set = set(keys)
        for item in declared:
            unknown = [dep for dep in item.depends_on if dep not in key_set]
            if unknown or item.key in item.depends_on:
                raise TeamValidationError(
                    f"dependsOn of {item.key!r} must reference other keys in this batch; "
                    f"got {list(item.depends_on)}"
                )
        return self._toposort(declared)

    @staticmethod
    def _toposort(declared: Sequence[TeamTaskInput]) -> list[TeamTaskInput]:
        """Kahn 拓扑排序：排不完的那部分就是环；同层保持声明顺序。

        建任务时必须先有依赖的 taskId，所以 Leader 把下游写在前面（``draft`` 先于
        ``research``）也要能跑通——落库走拓扑序，返回走声明序。
        """
        pending = list(declared)
        resolved: set[str] = set()
        order: list[TeamTaskInput] = []
        while pending:
            ready = [item for item in pending if set(item.depends_on) <= resolved]
            if not ready:
                raise TeamValidationError(
                    "dependsOn forms a cycle and cannot be scheduled: "
                    f"{sorted(item.key for item in pending)}"
                )
            for item in ready:
                pending.remove(item)
            resolved.update(item.key for item in ready)
            order.extend(ready)
        return order

    async def _persist_batch(
        self,
        batch_id: str,
        declared: Sequence[TeamTaskInput],
        order: Sequence[TeamTaskInput],
    ) -> list[_PlannedTask]:
        """先建任务再执行；``dependsOn`` 在这里换成持久 taskId。"""
        planned_by_key: dict[str, _PlannedTask] = {}
        ids_by_key: dict[str, str] = {}
        for item in order:
            slot = self._slots[item.member_id]
            snapshot = await self._call(
                self._repository.create_task,
                run_id=self.run_id,
                member=slot.member,
                title=item.title,
                acceptance=item.acceptance,
                instruction=item.instruction,
                status="pending",
                depends_on=[ids_by_key[dep] for dep in item.depends_on],
                resource_keys=list(item.resource_keys),
                batch_id=batch_id,
            )
            ids_by_key[item.key] = snapshot.task_id
            planned_by_key[item.key] = _PlannedTask(
                key=item.key,
                task_id=snapshot.task_id,
                title=item.title,
                member=slot,
                instruction=item.instruction,
                acceptance=item.acceptance,
                depends_on=tuple(ids_by_key[dep] for dep in item.depends_on),
                resources=_lock_names(item.resource_keys),
            )
        planned = [planned_by_key[item.key] for item in declared]
        for item in planned:
            self._planned[item.task_id] = item
        # batchId 落库放在任务之后：record_dispatch 需要真实 taskId 才能给出幂等回执。
        await self._call(
            self._repository.record_dispatch,
            run_id=self.run_id,
            batch_id=batch_id,
            payload=[item.to_dict() for item in declared],
            task_ids=[item.task_id for item in planned],
        )
        # 整批只排空一次：一次写一条事件，逐条投递会把 N 次读放大成 N 次事务。
        await self._publish_new()
        return planned

    # ------------------------------------------------------------------ 执行

    async def _run_child(self, planned: _PlannedTask) -> TeamTaskResult:
        try:
            return await self._execute(planned)
        except asyncio.CancelledError:
            result = await self._cancel_open_task(planned)
            if self._cancel_requested.is_set() or planned.task_id in self._cancelled_task_ids:
                return result
            raise
        finally:
            self._done_events[planned.task_id].set()

    async def _execute(self, planned: _PlannedTask) -> TeamTaskResult:
        for dep_id in planned.depends_on:
            await self._done_events[dep_id].wait()
            if self._done_status.get(dep_id) != "succeeded":
                return await self._block(planned)
        if self._cancel_requested.is_set():
            return await self._cancel_open_task(planned)
        if self._past_run_deadline():
            return await self._cancel_open_task(planned, stop_reason="run_timeout")
        async with AsyncExitStack() as stack:
            # 成员锁必须排在 queued 落库之前：落库走 to_thread，一次线程跳的耗时会把
            # 同成员任务的起跑顺序打乱（谁先拿到锁变成随机）。先锁再写，顺序只由
            # 创建顺序决定，maxConcurrency=1 与同成员串行的语义才稳定。
            await stack.enter_async_context(self._member_locks[planned.member.member.member_id])
            await self._mutate(self._repository.update_task_status, planned.task_id, "queued")
            for resource in planned.resources:
                await stack.enter_async_context(self._lock_for_resource(resource))
            await stack.enter_async_context(self._semaphore)
            # 排队等待期间可能已经收到停止请求或越过总超时：这里必须再看一眼，
            # 否则队列里的任务能绕过 runTimeoutSeconds。
            if planned.task_id in self._cancelled_task_ids or self._cancel_requested.is_set():
                return await self._cancel_open_task(planned)
            if self._past_run_deadline():
                return await self._cancel_open_task(planned, stop_reason="run_timeout")
            return await self._invoke(planned)

    async def _instruction_for(self, planned: _PlannedTask) -> str:
        """把已成功的前置结果拼进本任务指令（契约 §5：dependsOn 不只是排序标签）。

        默认只拼 ``result.summary``（8000 字符预算内截断）；注入了 ``upstream_expander``
        的写作批次改为展开**完整产出**——否则审校拿到的是被摘要截断的初稿。
        """
        blocks: list[str] = []
        budget = self._upstream_budget
        for dep_id in planned.depends_on:
            result = self._results.get(dep_id)
            if result is None or result.status != "succeeded":
                continue
            title = self._planned[dep_id].title if dep_id in self._planned else dep_id
            detail = result.summary
            if self._upstream_expander is not None:
                expanded = await self._upstream_expander(dep_id)
                if expanded:
                    detail = expanded
            lines = [f"### {title}", detail]
            if result.artifacts:
                lines.append("产出：" + ", ".join(result.artifacts))
            block = "\n".join(lines)
            if len(block) > budget:
                block = block[:budget] + "\n（上游结果过长，已按预算截断）"
            budget -= len(block)
            blocks.append(block)
            if budget <= 0:
                break
        if not blocks:
            return planned.instruction
        return planned.instruction + "\n\n## 上游成员已完成的结果\n\n" + "\n\n".join(blocks)

    async def _invoke(self, planned: _PlannedTask) -> TeamTaskResult:
        started = await self._mutate(self._repository.start_task, planned.task_id)
        extra_tools: tuple[Any, ...] = ()
        if self._extra_tools_provider is not None:
            try:
                extra_tools = tuple(
                    self._extra_tools_provider(self, planned.member.member.member_id)
                )
            except Exception as error:  # noqa: BLE001 - 扩展失败退回不带写工具执行
                default_logger.bind(
                    event="team_extra_tools_failed",
                    run_id=self.run_id,
                    member_id=planned.member.member.member_id,
                ).warning(f"team extra tools provider failed: {error}")
        request = TeamExecutionRequest(
            run_id=self.run_id,
            task_id=planned.task_id,
            attempt_id=started.attempt_id or planned.task_id,
            member_id=planned.member.member.member_id,
            agent_id=planned.member.member.agent_id,
            instruction=await self._instruction_for(planned),
            acceptance=planned.acceptance,
            agent=self._members.resolve(planned.member.profile, workspace=self._workspace),
            runtime=self._runtime,
            workspace=self._workspace,
            workspace_scope=self._workspace_scope,
            origin_message_id=self._origin_message_id,
            session_key=self._session_key,
            extra_tools=extra_tools,
        )
        try:
            executed = await asyncio.wait_for(
                self._executor.execute_member(
                    request,
                    # 没有事件出口时保持旧行为：执行本身不因为投影缺失而改变。
                    events=self._activity_sink(planned) if self._events is not None else NO_EVENTS,
                ),
                timeout=self._task_timeout(),
            )
        except TimeoutError:
            return await self._finish(
                planned,
                status="failed",
                summary=f"成员执行超过 taskTimeoutSeconds={self._limits.task_timeout_seconds}，已停止。",
                stop_reason="timeout",
                error_code="timeout",
            )
        return await self._finish_from_execution(planned, executed)

    async def _finish_from_execution(self, planned: _PlannedTask, executed: Any) -> TeamTaskResult:
        outcome = str(getattr(executed, "outcome", "failed"))
        status = _OUTCOME_TO_STATUS.get(outcome, "interrupted")
        content = str(getattr(executed, "final_content", "") or "")[:MAX_TASK_RESULT_CHARS]
        usage = getattr(executed, "usage", None)
        task_usage = (
            TeamTaskUsage(
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            )
            if usage is not None
            else None
        )
        stop_reason = str(getattr(executed, "stop_reason", "") or outcome)
        error_code = getattr(executed, "error_code", None)
        return await self._finish(
            planned,
            status=status,
            summary=content[:MAX_TASK_SUMMARY_CHARS],
            stop_reason=stop_reason,
            error_code=None if status == "succeeded" else (error_code or "member_failed"),
            usage=task_usage,
            full_result=content if len(content) > MAX_TASK_SUMMARY_CHARS else None,
        )

    async def _finish(
        self,
        planned: _PlannedTask,
        *,
        status: str,
        summary: str,
        stop_reason: str,
        error_code: str | None = None,
        usage: TeamTaskUsage | None = None,
        full_result: str | None = None,
    ) -> TeamTaskResult:
        result = await self._mutate(
            self._repository.finish_task,
            planned.task_id,
            status=status,
            summary=summary,
            error_code=error_code,
            usage=usage,
            stop_reason=stop_reason,
            full_result=full_result,
        )
        self._done_status[planned.task_id] = status
        self._results[planned.task_id] = result
        return result

    async def _block(self, planned: _PlannedTask) -> TeamTaskResult:
        upstream = ", ".join(self._done_status.get(dep, "unknown") for dep in planned.depends_on)
        return await self._finish(
            planned,
            status="blocked",
            summary=f"前置任务未成功（{upstream}），本任务已阻断。",
            stop_reason="upstream_unavailable",
            error_code="upstream_unavailable",
        )

    async def _cancel_open_task(
        self, planned: _PlannedTask, *, stop_reason: str = "cancelled"
    ) -> TeamTaskResult:
        snapshot = await self._call(self._repository.get_task, planned.task_id)
        if snapshot.status in TASK_TERMINAL_STATUSES:
            self._done_status[planned.task_id] = snapshot.status
            return self._result_of(snapshot)
        if snapshot.status == "running":
            # 先落 cancelling 再落 cancelled：UI 要能区分「已请求停止」与「真的停了」。
            await self._call(self._repository.update_task_status, planned.task_id, "cancelling")
        summary = (
            "用户停止了本次团队运行。"
            if stop_reason == "cancelled"
            else "本次团队运行超过 runTimeoutSeconds，未再启动新任务。"
        )
        return await self._finish(
            planned,
            status="cancelled",
            summary=summary,
            stop_reason=stop_reason,
            error_code=stop_reason,
        )

    def _result_of(self, snapshot: TeamTaskSnapshot) -> TeamTaskResult:
        if snapshot.result is None:
            raise TeamValidationError(f"team task {snapshot.task_id} has no persisted result")
        self._results.setdefault(snapshot.task_id, snapshot.result)
        return snapshot.result

    def _replay(self, task_ids: Sequence[str]) -> list[TeamTaskResult]:
        return [self._result_of(self._repository.get_task(task_id)) for task_id in task_ids]

    # ------------------------------------------------------------------ 取消

    async def cancel(self) -> TeamRunSnapshot:
        """停止整个运行：先落 ``cancelling``，再取消并 await 在场的子任务（契约 §6）。"""
        self._cancel_requested.set()
        run = self.run()
        if run.status in RUN_TERMINAL_STATUSES:
            return run
        snapshot = await self._mutate(
            self._repository.update_run_status, self.run_id, "cancelling"
        )
        for child in list(self._children.values()):
            child.cancel()
        return snapshot

    async def cancel_task(self, task_id: str) -> TeamTaskSnapshot:
        """只停一条任务：成功的兄弟不受影响，其依赖转为 blocked。"""
        snapshot = self._repository.get_task(task_id)
        if snapshot.status in TASK_TERMINAL_STATUSES:
            return snapshot
        planned = self._planned.get(task_id) or _PlannedTask(
            key=task_id,
            task_id=task_id,
            title=snapshot.title,
            member=self._slots[snapshot.member_id],
            instruction="",
            acceptance=snapshot.acceptance,
            depends_on=tuple(snapshot.depends_on),
            resources=(),
        )
        self._cancelled_task_ids.add(task_id)
        child = self._children.get(task_id)
        if child is None:
            await self._cancel_open_task(planned)
        else:
            child.cancel()
            await asyncio.gather(child, return_exceptions=True)
        return self._repository.get_task(task_id)

    async def _settle_cancelled(self) -> TeamRunSnapshot:
        run = self.run()
        if run.status in RUN_TERMINAL_STATUSES:
            return run
        return await self._mutate(self._repository.update_run_status, self.run_id, "cancelled")

    # ------------------------------------------------------------------ 收口

    async def set_leader_action(self, action: str) -> TeamRunSnapshot:
        return await self._mutate(self._repository.set_leader_action, self.run_id, action)

    async def finalize(self, *, delivered: bool) -> TeamRunSnapshot:
        """Leader 回合结束时唯一允许的收口入口；重复调用返回既有终态。"""
        if self._closed:
            return self.run()
        self._closed = True
        run = self.run()
        if run.status in RUN_TERMINAL_STATUSES:
            return run
        status = self._terminal_status(run, delivered=delivered)
        await self._call(self._repository.set_leader_action, self.run_id, "idle")
        final = await self._call(self._repository.update_run_status, self.run_id, status)
        # 收口的两条写一起排空：run.finished 是 UI 唯一允许的终态，必须投出去。
        await self._publish_new()
        return final

    def _terminal_status(self, run: TeamRunSnapshot, *, delivered: bool) -> str:
        """终态判定（契约 §6）：取消 > Leader 未交付 > 全成功 > 混合 partial > 无结果 failed。"""
        if self._cancel_requested.is_set():
            return "cancelled"
        if not delivered:
            return "failed"
        statuses = [task.status for task in run.tasks]
        if not statuses or all(status == "succeeded" for status in statuses):
            return "completed"
        if any(status == "succeeded" for status in statuses):
            return "partial"
        return "failed"

    # ------------------------------------------------------------------ 内部

    def _lock_for_resource(self, resource: str) -> asyncio.Lock:
        lock = self._resource_locks.get(resource)
        if lock is None:
            lock = asyncio.Lock()
            self._resource_locks[resource] = lock
        return lock

    def _past_run_deadline(self) -> bool:
        return (self._clock() - self._started_monotonic) >= self._limits.run_timeout_seconds

    def _task_timeout(self) -> float:
        if self._task_timeout_override is not None:
            return float(self._task_timeout_override)
        return float(self._limits.task_timeout_seconds)

    async def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """SQLite 的短事务写在 ``to_thread`` 里跑，不占事件循环（契约 §2）。"""
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def _mutate(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """会追加事件的写：落盘成功后立刻把新事件投递出去（先持久化，再广播）。"""
        result = await self._call(fn, *args, **kwargs)
        await self._publish_new()
        return result

    async def _publish_new(self) -> None:
        """按 seq 增量读事件再投递；无出口或读不到就静默跳过。

        并发孩子可能同时写完各自的事务，所以这里串行化：一次读到的区间整体投递，
        避免两个任务的事件交错，也避免同一个事件被投两次。一次写最多追加一条事件，
        但续接运行首投要从 seq 0 回放，所以按页排空，不留没广播过的历史事件。

        观察者（协作域）拿到的是**未经投影的原事件**：写作域要读 ``task.result`` 与
        ``taskId``，而线格式白名单会把这些裁掉。观察者异常只记日志——执行已经落盘。
        """
        if self._events is None and not self._observers:
            return
        async with self._publish_lock:
            while True:
                events = await self._call(
                    self._repository.list_events,
                    self.run_id,
                    after_seq=self._published_seq,
                    limit=MAX_EVENT_LIMIT,
                )
                if not events:
                    return
                self._published_seq = max(int(item.seq) for item in events)
                if self._events is not None:
                    await self._events.emit(events)
                for observer in self._observers:
                    try:
                        await observer(events)
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:  # noqa: BLE001 - 出口故障不能改变领域状态
                        default_logger.bind(
                            event="team_event_observer_failed", run_id=self.run_id
                        ).warning(f"team event observer failed: {error}")
                if len(events) < MAX_EVENT_LIMIT:
                    return

    def _activity_sink(self, planned: _PlannedTask) -> TeamActivitySink:
        return TeamActivitySink(self, planned.task_id)

    async def record_activity(self, task_id: str, *, action: str) -> None:
        """把一条公开活动摘要落成 ``task.activity``；被节流挡掉就什么都不写。"""
        if self._events is None or not action or not self._events.allows_activity(task_id):
            return
        await self._mutate(self._repository.record_activity, task_id, last_action=action)


class TeamActivitySink:
    """成员执行期间的活动出口：只搬运白名单摘要，不碰参数与输出（契约 §7）。"""

    def __init__(self, handle: TeamRunHandle, task_id: str) -> None:
        self._handle = handle
        self._task_id = task_id

    async def task_activity(self, **payload: Any) -> None:
        await self._handle.record_activity(
            self._task_id, action=render_activity_action(payload)
        )


class TeamScheduler:
    """一个 workspace 一个调度器：持有活动运行，并把停止请求路由到对应运行。"""

    def __init__(
        self,
        *,
        repository: TeamRepository,
        executor: MemberExecutor,
        members: MemberCatalog,
        runtime: Any,
        workspace: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        task_timeout_seconds: float | None = None,
        publisher: TeamPublisherFactory | None = None,
    ) -> None:
        """``task_timeout_seconds`` 是测试/运维的降级开关：正常装配不传，用团队限额。"""
        self._repository = repository
        self._executor = executor
        self._members = members
        self._runtime = runtime
        self._workspace = Path(workspace) if workspace is not None else Path.cwd()
        self._clock = clock
        self._task_timeout_seconds = task_timeout_seconds
        self._publisher = publisher
        self._observers: list[EventObserver] = []
        self._handles: dict[str, TeamRunHandle] = {}

    @property
    def repository(self) -> TeamRepository:
        return self._repository

    def observe(self, observer: EventObserver) -> None:
        """追加一个事件观察者（同一次运行的事件投递顺序：总线出口 → 观察者）。

        协作域用它在**不夺取** WebUI 广播出口的前提下接住团队事件；观察者抛错只记日志，
        不能让一次广播失败改变已经落盘的任务状态。
        """
        if observer not in self._observers:
            self._observers.append(observer)

    def active_runs(self) -> list[TeamRunHandle]:
        return list(self._handles.values())

    def get(self, run_id: str) -> TeamRunHandle | None:
        return self._handles.get(run_id)

    def forget(self, run_id: str) -> None:
        """运行收口后由调用方摘掉，避免活动表里留着已终态的运行。"""
        self._handles.pop(run_id, None)

    def open_run(self, *, session_key: str, turn_id: str) -> TeamRunHandle | None:
        """同一会话的同一可见轮次只允许一个运行（``team_runs`` 上有唯一索引）。

        内部续接会带着同一个 ``webui_turn_id`` 再来一回合；这一回合必须复用运行，
        否则派了三轮活却开出两条运行，事件与配额都对不上账。
        """
        for handle in self._handles.values():
            if (
                handle.session_key == session_key
                and handle.turn_id == turn_id
                and not handle.is_closed
            ):
                return handle
        return None

    async def start_run(
        self,
        *,
        binding: TeamBinding,
        chat_id: str,
        session_key: str,
        turn_id: str,
        internal_turn_id: str,
        workspace: Path | None = None,
        workspace_scope: WorkspaceScope | None = None,
        origin_message_id: str | None = None,
        parent_run_id: str | None = None,
        snapshot: TeamRevisionSnapshot | None = None,
        runtime: Any = None,
        upstream_expander: UpstreamExpander | None = None,
        upstream_budget: int | None = None,
        extra_tools_provider: ExtraToolsProvider | None = None,
    ) -> TeamRunHandle:
        """``runtime`` 让调用方按回合注入当前默认执行配置；不传则用装配时的调度器默认。

        用户在设置里换默认模型后，``ModelRuntimeResolver.runtime`` 是新对象，装配时抓
        的那一份会过期，所以运行必须由调用方给当回合的 runtime。

        ``upstream_expander`` / ``upstream_budget`` 是**写作批次**的下游全文出口：只有
        显式传入的调用方（协作 TeamPort）才展开完整产物；主编回合与重试不传，摘要语义不变。
        """
        # 准入检查（开关/归档/档案可用）已经冻结过快照时直接沿用，不再读第二次盘。
        revision = snapshot or await asyncio.to_thread(
            self._repository.run_binding_snapshot, binding
        )
        run = await asyncio.to_thread(
            self._repository.create_run,
            binding=binding,
            chat_id=chat_id,
            session_key=session_key,
            turn_id=turn_id,
            internal_turn_id=internal_turn_id,
            snapshot=revision,
            parent_run_id=parent_run_id,
        )
        handle = self._handle_for(
            run,
            revision,
            session_key=session_key,
            workspace=Path(workspace) if workspace is not None else self._workspace,
            workspace_scope=workspace_scope,
            origin_message_id=origin_message_id,
            runtime=runtime if runtime is not None else self._runtime,
            upstream_expander=upstream_expander,
            upstream_budget=upstream_budget,
            extra_tools_provider=extra_tools_provider,
        )
        self._handles[run.run_id] = handle
        return handle

    async def adopt_run(
        self,
        run_id: str,
        *,
        session_key: str,
        workspace: Path | None = None,
        workspace_scope: WorkspaceScope | None = None,
        origin_message_id: str | None = None,
        runtime: Any = None,
    ) -> TeamRunHandle:
        """接手一条已经落盘的运行：重试复制出来的新 run 由这里进场执行。

        修订按 ``binding`` 回 ``team_revisions`` 取——那张表只追加，所以拿到的就是当时
        冻结的同一份（契约 §5「新尝试仍使用原固定修订」）。
        """
        run = await asyncio.to_thread(self._repository.get_run, run_id)
        existing = self.open_run(session_key=session_key, turn_id=run.turn_id)
        if existing is not None:
            return existing
        if run.status in RUN_TERMINAL_STATUSES:
            raise TeamValidationError(f"team run {run_id} is already {run.status}")
        revision = await asyncio.to_thread(self._repository.run_binding_snapshot, run.binding)
        handle = self._handle_for(
            run,
            revision,
            session_key=session_key,
            workspace=Path(workspace) if workspace is not None else self._workspace,
            workspace_scope=workspace_scope,
            origin_message_id=origin_message_id,
            runtime=runtime if runtime is not None else self._runtime,
        )
        self._handles[run.run_id] = handle
        return handle

    def _handle_for(
        self,
        run: TeamRunSnapshot,
        revision: TeamRevisionSnapshot,
        *,
        session_key: str,
        workspace: Path,
        workspace_scope: WorkspaceScope | None,
        origin_message_id: str | None,
        runtime: Any,
        upstream_expander: UpstreamExpander | None = None,
        upstream_budget: int | None = None,
        extra_tools_provider: ExtraToolsProvider | None = None,
    ) -> TeamRunHandle:
        return TeamRunHandle(
            repository=self._repository,
            executor=self._executor,
            members=self._members,
            runtime=runtime,
            run=run,
            revision=revision,
            session_key=session_key,
            workspace=workspace,
            workspace_scope=workspace_scope,
            origin_message_id=origin_message_id,
            clock=self._clock,
            task_timeout_seconds=self._task_timeout_seconds,
            events=(
                TeamRunEventSink(self._publisher(session_key, run.chat_id), clock=self._clock)
                if self._publisher is not None
                else None
            ),
            upstream_expander=upstream_expander,
            upstream_budget=(
                UPSTREAM_CONTEXT_BUDGET if upstream_budget is None else int(upstream_budget)
            ),
            observers=self._observers,
            extra_tools_provider=extra_tools_provider,
        )

    async def cancel_run(self, run_id: str) -> TeamRunSnapshot | None:
        handle = self._handles.get(run_id)
        if handle is None:
            return None
        return await handle.cancel()

    async def cancel_session(self, session_key: str) -> int:
        """停止整个聊天时一并停掉该会话的团队运行（契约 §6）。"""
        handles = [
            handle for handle in self._handles.values() if handle.session_key == session_key
        ]
        for handle in handles:
            await handle.cancel()
        return len(handles)
