"""``TeamPort`` 的生产实现：把一批写作动作变成真正的团队运行（plan Task 8 / 契约 §9）。

一条批次的生命周期
------------------

1. **取内置团队**：``BuiltinAssetRegistry.prepare("team", "writing-team", allocator=…)``
   幂等地把契约 §7 的固定创作团队落成一个真实 ``TeamDefinition``；用户已占用同名 id 时
   按 allocator 协议让名（``writing-team-builtin``…），绝不覆盖用户对象。
2. **发起前强制限额**（契约 §9）：团队限额＋本批次成员任务数、自动派工轮数一起断言，
   任何一条越界都在**创建运行之前**抛 :class:`TeamPortLimitError`。
3. **起跑**：``TeamScheduler.start_run`` 落运行 → 后台任务里 ``dispatch`` 成员任务 →
   收口 ``finalize``。``start_batch`` 只等待到「运行已落库」就返回 ``run_id``，
   否则``CollabRunner`` 在拿到 run_id 之前收到的事件会被它自己的闸门丢掉。
4. **事件回执**：端口把自己注册成调度器的事件观察者，把每条团队事件交给
   ``CollabRunner.on_team_event``（闸门在它之前），并对**格式不合规**的交付补一轮修正派工
   （契约 §9 的「最多 2 轮自动派工」）。

线程口径（为什么这里没有 ``asyncio.to_thread`` 包裹收口）
--------------------------------------------------------

端口自己的所有 I/O（播种/读文章/读全文/写会话绑定）都走 ``asyncio.to_thread``。但
``CollabRunner.on_team_event`` **不能**整体丢进线程：它在提纲交付与运行收口时会
``asyncio.create_task(...)`` 释放团队运行句柄（``nanobot/writing/collab/runner.py`` 的
``_release_handle``），而 ``asyncio.create_task`` 要求当前线程有运行中的事件循环——
在线程里会抛 ``RuntimeError: no running event loop``，结果是「批次已 waiting_author，
但活动运行没有停」。因此收口在**事件循环上逐条**执行（每条之间 ``await asyncio.sleep(0)``
让出循环），单条事件只做几个本地 SQLite 短事务、不跨 ``await``、不持锁等待。
``tests/writing/test_collab_team_port.py`` 用一条**约束探针**锁定了这个事实。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from loguru import logger as default_logger

from nanobot.agent.subagent import frozen_skills_context
from nanobot.teams.binding import SESSION_TEAM_BINDING_KEY
from nanobot.teams.models import (
    MAX_TASK_INSTRUCTION_CHARS,
    TeamBinding,
    TeamDefinition,
    TeamLimits,
    TeamTaskInput,
)
from nanobot.teams.repository import TeamNotFound, utc_now_iso
from nanobot.teams.scheduler import WRITING_UPSTREAM_BUDGET, TeamScheduler
from nanobot.teams.service import TeamService
from nanobot.teams.turn import (
    TeamLeaderTurns,
    bind_team_turns,
    build_team_turns,
    resolve_session_runtime,
)
from nanobot.writing.builtin import WRITING_TEAM_LOGICAL_ID
from nanobot.writing.collab.envelope import (
    ROLE_ARTIFACT_KINDS,
    envelope_output_contract,
    parse_envelope,
)
from nanobot.writing.collab.recipe import (
    ACTION_START_FULL,
    ARCHITECT_ROLE,
    AUTHOR_ROLE,
    BATCH_TIMEOUT_SECONDS,
    EDITOR_ROLE,
    MAX_AUTO_DISPATCH_ROUNDS,
    MAX_CONCURRENT_MEMBERS,
    MAX_MEMBER_TASKS_PER_BATCH,
    MEMBER_TIMEOUT_SECONDS,
    RESEARCHER_ROLE,
    REVIEWER_ROLE,
    TaskRecipe,
    recipe_for,
)

__all__ = [
    "COLLAB_LIMITS",
    "TEAM_MEMBER_ROLES",
    "TeamPortLimitError",
    "TeamWritingPort",
    "builtin_team_definition",
]

#: 契约 §9 的固定限额（唯一真相源是 ``recipe`` 的常量；这里只做投影与断言）。
COLLAB_LIMITS: dict[str, int] = {
    "max_concurrency": MAX_CONCURRENT_MEMBERS,
    "max_tasks": MAX_MEMBER_TASKS_PER_BATCH,
    "max_dispatch_rounds": MAX_AUTO_DISPATCH_ROUNDS,
    "task_timeout_seconds": MEMBER_TIMEOUT_SECONDS,
    "run_timeout_seconds": BATCH_TIMEOUT_SECONDS,
}

#: 固定创作团队的成员角色（主编不派成员任务：团队模型禁止 leader 兼任成员）。
TEAM_MEMBER_ROLES: tuple[str, ...] = (
    RESEARCHER_ROLE,
    ARCHITECT_ROLE,
    AUTHOR_ROLE,
    REVIEWER_ROLE,
)

#: 文章正文进指令的预算：长文按此摘录并显式标注，剩余上下文留给上游产物与交付格式。
MAX_ARTICLE_EXCERPT_CHARS = 12_000
#: 大纲进指令的条数上限（提纲类交接最多几十条，超出说明上下文已经很脏）。
MAX_OUTLINE_ITEMS = 40

#: 产物类型 → 给成员看的中文任务标题。
_KIND_TITLES: dict[str, str] = {
    "brief": "选题简报",
    "material_list": "资料清单",
    "outline": "结构提纲",
    "draft": "初稿",
    "rewrite": "片段改写",
    "review": "审校意见",
}


class TeamPortLimitError(RuntimeError):  # noqa: N818 - 领域限额违约（配置错误，非用户输入）
    """批次限额在发起前被违反：一条成员任务都不派、一个运行都不建。"""

    status = 409

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class _PlannedMember:
    """一条成员任务 + 它被要求交付的产物身份。"""

    task: TeamTaskInput
    role_id: str
    kind: str


def builtin_team_definition(team_id: str, *, now: str | None = None) -> TeamDefinition:
    """契约 §7 的固定创作团队（主编 ``writing-editor`` ＋ 四名成员）。"""
    moment = now or utc_now_iso()
    return TeamDefinition.from_dict(
        {
            "id": team_id,
            "revision": 1,
            "name": "创作团队",
            "description": "固定创作团队：研究 → 提纲 → 初稿 → 审校，成员与模型由服务端固定。",
            "instructions": (
                "按作者的目标推进：先查资料，再出提纲交作者确认，确认后写初稿，最后独立审校。"
                "交付必须带来源与疑点，不臆造证据。"
            ),
            "leaderAgentId": EDITOR_ROLE,
            "members": [
                {"memberId": role, "agentId": role, "role": role}
                for role in TEAM_MEMBER_ROLES
            ],
            "limits": TeamLimits(
                max_concurrency=COLLAB_LIMITS["max_concurrency"],
                max_tasks=COLLAB_LIMITS["max_tasks"],
                max_dispatch_rounds=COLLAB_LIMITS["max_dispatch_rounds"],
                task_timeout_seconds=COLLAB_LIMITS["task_timeout_seconds"],
                run_timeout_seconds=COLLAB_LIMITS["run_timeout_seconds"],
            ).to_dict(),
            "archived": False,
            "createdAt": moment,
            "updatedAt": moment,
        }
    )


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(cast("Mapping[str, Any]", value))
    return {}


def _frozen_skill_bodies(frozen: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """从冻结上下文里取 (name, body)：**只**用于运行期上下文，绝不落库。"""
    raw = frozen.get("skills")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    entries: list[tuple[str, str]] = []
    for item in cast("Sequence[Any]", raw):
        if not isinstance(item, Mapping):
            continue
        entry = cast("Mapping[str, Any]", item)
        name = entry.get("name")
        body = entry.get("body")
        if isinstance(name, str) and name and isinstance(body, str) and body.strip():
            entries.append((name, body))
    return tuple(entries)


class TeamWritingPort:
    """``CollabRunner`` 的 ``TeamPort`` 实现；一个 workspace 一个实例。"""

    def __init__(
        self,
        *,
        service: TeamService,
        workspace: Path | str,
        host: Any | None = None,
        turns: TeamLeaderTurns | None = None,
        agent: Any | None = None,
        articles: Any | None = None,
        registry: Any | None = None,
        session_store: Any | None = None,
        binding_sink: Callable[[str, TeamBinding], None] | None = None,
        runtime_provider: Callable[[], Any] | None = None,
        session_runtime_provider: Callable[[str], Any] | None = None,
        logger: Any = default_logger,
    ) -> None:
        """``turns`` 与 ``host`` 二选一：给了 ``turns`` 就复用既有团队执行栈。

        ``agent`` 是 ``AgentLoop``（``AgentLike``）：给了就调 ``bind_team_turns``，让写作
        会话里由主编推进的回合也走同一套团队栈；给 ``None``（CLI/测试）只装批次路径。

        ``runtime_provider`` 是**零参**的装配期默认执行配置（CLI/TUI/测试一直在用）；
        ``session_runtime_provider`` 是「按协作会话键解析 runtime」。两个都给时按会话优先，
        会话解析不出结果才回落零参那份 —— 否则侧栏切换的 preset 只是装饰。
        """
        if turns is None:
            if host is None:
                raise ValueError("team host is required to build the writing team stack")
            turns = build_team_turns(service=service, host=host, workspace=workspace)
        self._service = service
        self._workspace = Path(workspace)
        self._turns = turns
        self._scheduler: TeamScheduler = turns.scheduler
        self._articles = articles
        self._registry = registry
        self._session_store = session_store
        self._binding_sink = binding_sink
        self._runtime_provider = runtime_provider
        self._session_runtime_provider = session_runtime_provider
        self._logger = logger
        self._event_sink: Callable[[Any], None] | None = None
        self._pending: dict[str, asyncio.Task[Any]] = {}
        self._batch_runs: dict[str, str] = {}
        self._last_run_id: str | None = None
        self._last_ingest_thread: int | None = None
        if agent is not None:
            bind_team_turns(agent, turns)
        # 事件回执：与 WebUI 广播出口并行，互不影响。
        self._scheduler.observe(self.observe_team_events)

    # ---- 装配期自检 / 测试观测 ------------------------------------------------

    def set_event_sink(self, sink: Callable[[Any], None] | None) -> None:
        """注入收口出口（``CollabRunner.on_team_event``）；装配顺序允许后置。"""
        self._event_sink = sink

    def last_run_id(self) -> str | None:
        """最近一次真正起跑的运行 id（装配自检与测试用）。"""
        return self._last_run_id

    def pending_runs(self) -> tuple[str, ...]:
        """还没跑完的后台批次运行。"""
        return tuple(run_id for run_id, task in self._pending.items() if not task.done())

    def last_ingest_thread(self) -> int | None:
        """最后一次事件收口发生的线程 id（用于锁定「在哪个线程跑」的约定）。"""
        return self._last_ingest_thread

    async def drain(self) -> None:
        """等待后台批次跑完（测试/关停用；正常运行由调度器的事件驱动收口）。"""
        while self._pending:
            await asyncio.gather(*tuple(self._pending.values()), return_exceptions=True)

    # ---- 内置团队与限额 -------------------------------------------------------

    def resolve_team(self) -> TeamDefinition:
        """取（必要时播种）固定创作团队；用户占用同名 id 时让名，不覆盖。"""
        repository = self._service.repository
        if self._registry is not None:
            team_id = self._registry.prepare(
                "team", WRITING_TEAM_LOGICAL_ID, allocator=self._allocate_team
            )
            return repository.get_team(team_id)
        team_id = WRITING_TEAM_LOGICAL_ID
        try:
            definition = repository.get_team(team_id)
        except TeamNotFound:
            self._allocate_team(team_id)
            return repository.get_team(team_id)
        return definition

    def _allocate_team(self, candidate: str) -> str:
        """registry allocator：空串表示候选已被占用（用户对象一律原样保留）。"""
        repository = self._service.repository
        try:
            repository.get_team(candidate)
        except TeamNotFound:
            pass
        else:
            return ""
        definition = builtin_team_definition(candidate)
        # ``TeamService.save_team`` 会因内置保护拒绝写 ``writing-team``；内置资产的落地
        # 走仓储层（与 WU-10 的 seed 口径一致），引用校验仍复用 ``service.freeze``。
        snapshot = self._service.freeze(definition)
        repository.save_team(
            definition=definition,
            snapshot=snapshot,
            expected_revision=None,
            request_id=f"writing-builtin-team:{candidate}",
        )
        self._log(
            "info",
            event="writing_collab_team_seeded",
            team_id=candidate,
            revision=definition.revision,
        )
        return candidate

    def enforce_limits(
        self, *, definition: TeamDefinition, member_tasks: int, rounds: int
    ) -> None:
        """发起前的限额断言：团队限额不得宽于契约 §9，本批次也不得越界。"""
        limits = definition.limits
        caps: tuple[tuple[str, int, int], ...] = (
            ("maxTasks", limits.max_tasks, MAX_MEMBER_TASKS_PER_BATCH),
            ("maxDispatchRounds", limits.max_dispatch_rounds, MAX_AUTO_DISPATCH_ROUNDS),
            ("maxConcurrency", limits.max_concurrency, MAX_CONCURRENT_MEMBERS),
            ("taskTimeoutSeconds", limits.task_timeout_seconds, MEMBER_TIMEOUT_SECONDS),
            ("runTimeoutSeconds", limits.run_timeout_seconds, BATCH_TIMEOUT_SECONDS),
        )
        for name, actual, cap in caps:
            if actual > cap:
                raise TeamPortLimitError(
                    "writing team limits exceed the collaboration caps:"
                    f" {name}={actual} > {cap}"
                )
        if member_tasks > MAX_MEMBER_TASKS_PER_BATCH:
            raise TeamPortLimitError(
                "writing batch exceeds maxMemberTasksPerBatch="
                f"{MAX_MEMBER_TASKS_PER_BATCH}: {member_tasks}"
            )
        if rounds > MAX_AUTO_DISPATCH_ROUNDS:
            raise TeamPortLimitError(
                f"writing batch exceeds maxAutoDispatchRounds={MAX_AUTO_DISPATCH_ROUNDS}: {rounds}"
            )

    # ---- 成员任务计划 ---------------------------------------------------------

    def plan_members(
        self,
        *,
        action: str,
        definition: TeamDefinition,
        frozen: Mapping[str, Any],
        instruction: str = "",
        selection: Mapping[str, Any] | None = None,
        material_ids: Sequence[str] = (),
        article: Mapping[str, Any] | None = None,
    ) -> tuple[_PlannedMember, ...]:
        """按配方把动作拆成成员任务：角色→产物类型配套 + 交付格式说明进指令。"""
        recipe = recipe_for(action)
        pairs = _delivery_pairs(recipe)
        if not pairs:
            raise TeamPortLimitError(
                f"writing action {action} has no deliverable member role"
            )
        skill_names = [
            str(item.get("name"))
            for item in cast("Sequence[Any]", frozen.get("skills") or [])
            if isinstance(item, Mapping) and item.get("name")
        ]
        planned: list[_PlannedMember] = []
        previous_key: str | None = None
        for role_id, kind in pairs:
            member_id = self._member_for(role_id, definition=definition)
            body = self._base_instruction(
                role_id=role_id,
                kind=kind,
                action=action,
                definition=definition,
                instruction=instruction,
                selection=selection,
                material_ids=material_ids,
                article=article,
                skill_names=skill_names,
            )
            key = f"{kind}-{role_id}"
            depends_on = [previous_key] if action == ACTION_START_FULL and previous_key else []
            planned.append(
                _PlannedMember(
                    task=TeamTaskInput(
                        key=key,
                        member_id=member_id,
                        title=f"{_KIND_TITLES.get(kind, kind)}（{role_id}）",
                        instruction=body,
                        acceptance=(
                            # 点名校验：`ideate` 这类「只有主编产物」的动作会把成员槽位
                            # 让给别的成员，交付物要求里必须留着负责角色，收口时才知道
                            # 该按谁的身份登记产物（`TeamTaskSnapshot` 不暴露 instruction）。
                            f"由 `{role_id}` 交付一份可采纳的 {kind}；引用真实来源与素材，"
                            "把仍需作者确认的疑点写进 gaps。"
                        ),
                        depends_on=depends_on,
                        # 写作角色一律只读、各写各的产物：按角色分道，避免未声明写集
                        # 被并到同一把 workspace-write 锁上而静默串行化。
                        resource_keys=[f"writing-member:{role_id}"],
                    ),
                    role_id=role_id,
                    kind=kind,
                )
            )
            previous_key = key
        return tuple(planned)

    def _member_for(self, role_id: str, *, definition: TeamDefinition) -> str:
        """角色 → 团队成员槽位；配方里只有主编动作（ideate）时按序让位并记日志。"""
        for member in definition.members:
            if member.member_id == role_id or member.agent_id == role_id:
                return member.member_id
        fallback = definition.members[0].member_id
        self._log(
            "warning",
            event="writing_collab_member_substituted",
            role_id=role_id,
            member_id=fallback,
            reason="recipe declares a lead-only deliverable; member slot substituted",
        )
        return fallback

    def _base_instruction(
        self,
        *,
        role_id: str,
        kind: str,
        action: str,
        definition: TeamDefinition,
        instruction: str,
        selection: Mapping[str, Any] | None,
        material_ids: Sequence[str],
        article: Mapping[str, Any] | None,
        skill_names: Sequence[str],
    ) -> str:
        blocks: list[str] = [
            f"你是创作团队（{definition.name}）的 `{role_id}`；"
            f"本批次动作 `{action}`，只交付 `{kind}`。",
        ]
        blocks.append(
            "## 作者的要求\n\n"
            + (instruction.strip() or "（作者没有额外说明：按动作与文章上下文推进。）")
        )
        context = _article_context(article)
        if context:
            blocks.append(context)
        selection_block = _selection_block(selection)
        if selection_block:
            blocks.append(selection_block)
        if material_ids:
            blocks.append(
                "## 作者勾选的素材\n\n" + ", ".join(f"`{item}`" for item in material_ids)
            )
        if skill_names:
            blocks.append(
                "## 本批次冻结的技能（正文已在运行上下文里，只读）\n\n"
                + "\n".join(f"- `{name}`" for name in skill_names)
            )
        blocks.append(envelope_output_contract(role_id=role_id, kind=kind, action=action))
        body = "\n\n".join(block for block in blocks if block)
        if len(body) > MAX_TASK_INSTRUCTION_CHARS:
            raise TeamPortLimitError(
                "writing member instruction exceeds the team limit:"
                f" {len(body)} > {MAX_TASK_INSTRUCTION_CHARS}"
            )
        return body

    # ---- TeamPort 接口 --------------------------------------------------------

    async def start_batch(
        self,
        *,
        collaboration_id: str,
        batch_id: str,
        attempt: int,
        action: str,
        instruction: str,
        session_key: str,
        chat_id: str,
        frozen: Any,
        selection: Mapping[str, Any] | None,
        material_ids: Sequence[str],
    ) -> str:
        """落运行并返回 ``run_id``；成员任务在后台跑完后由事件收口。"""
        payload = _as_mapping(frozen)
        definition = await asyncio.to_thread(self.resolve_team)
        article = await self._article_snapshot(payload)
        planned = self.plan_members(
            action=action,
            definition=definition,
            frozen=payload,
            instruction=instruction,
            selection=selection,
            material_ids=material_ids,
            article=article,
        )
        # 契约 §9：限额在**发起前**强制——上面任何一条越界都不会留下运行。
        self.enforce_limits(
            definition=definition,
            member_tasks=len(planned),
            rounds=MAX_AUTO_DISPATCH_ROUNDS,
        )
        binding = TeamBinding(
            team_id=definition.id,
            team_revision=definition.revision,
            team_name=definition.name,
        )
        snapshot = await asyncio.to_thread(self._service.prepare_new_run, definition.id)
        # 按**这个协作会话**解析 runtime（侧栏切换的模型 preset 落在这里）；没有会话能力
        # 的装配（CLI/TUI/旧测试）回落零参 runtime_provider，行为与改动前一致。
        runtime, runtime_source = resolve_session_runtime(
            session_key,
            session_runtime_provider=self._session_runtime_provider,
            fallback=self._runtime_provider,
        )
        # 复用的冻结上下文（``writing.retry`` 不重新冻结技能/模型）会带着上一版的
        # ``model_id``。侧栏这时若已经切过模型，实际起跑的 runtime 会与冻结记录不一致 ——
        # 行为按 spec §4.4 只影响新批次（这里不动模型），但偏差必须留痕可查。
        frozen_model_id = str(payload.get("model_id") or "")
        runtime_model_id = str(getattr(runtime, "model", "") or "")
        if frozen_model_id and runtime_model_id and frozen_model_id != runtime_model_id:
            self._log(
                "warning",
                event="writing_collab_frozen_model_mismatch",
                collaboration_id=collaboration_id,
                batch_id=batch_id,
                attempt=attempt,
                frozen_model_id=frozen_model_id,
                runtime_model_id=runtime_model_id,
            )
        handle = await self._scheduler.start_run(
            binding=binding,
            chat_id=chat_id,
            session_key=session_key,
            # 同一会话 + 同一可见 turnId 只允许一个运行：批次内 attempt 进 turnId。
            turn_id=f"writing:{batch_id}:{attempt}",
            internal_turn_id=f"writing:{batch_id}:{attempt}",
            workspace=self._workspace,
            snapshot=snapshot,
            runtime=runtime,
            upstream_expander=self._expand_upstream,
            upstream_budget=WRITING_UPSTREAM_BUDGET,
        )
        self._last_run_id = handle.run_id
        self._batch_runs[batch_id] = handle.run_id
        await asyncio.to_thread(self._bind_session, session_key, binding)
        self._log(
            "info",
            event="writing_collab_batch_started",
            collaboration_id=collaboration_id,
            batch_id=batch_id,
            attempt=attempt,
            run_id=handle.run_id,
            action=action,
            member_tasks=len(planned),
            runtime_source=runtime_source,
        )
        task = asyncio.create_task(
            self._drive(
                run_id=handle.run_id,
                handle=handle,
                action=action,
                batch_id=batch_id,
                planned=planned,
                frozen_skills=_frozen_skill_bodies(payload),
            ),
            name=f"writing-collab-run-{batch_id}",
        )
        self._pending[handle.run_id] = task
        task.add_done_callback(lambda _done, rid=handle.run_id: self._pending.pop(rid, None))
        return handle.run_id

    async def cancel_run(self, run_id: str) -> Any:
        """停团队运行：在场句柄只落 ``cancelling``，终态由收口写（契约 §6）。"""
        snapshot = await self._scheduler.cancel_run(run_id)
        self._log(
            "info",
            event="writing_collab_run_cancelled",
            run_id=run_id,
            status=getattr(snapshot, "status", None),
        )
        return snapshot

    # ---- 事件收口 -------------------------------------------------------------

    async def observe_team_events(self, events: Sequence[Any]) -> None:
        """把团队事件交给 ``CollabRunner.on_team_event``（逐条、让出循环、绝不抛出）。

        为什么不在线程里跑：见模块说明——冻结的收口路径会在提纲交付与运行收口时
        ``asyncio.create_task`` 释放团队运行句柄，那要求当前线程有运行中的事件循环。
        每条事件之间 ``await asyncio.sleep(0)``，让事件循环在长批次里持续推进其它任务。
        """
        sink = self._event_sink
        if sink is None:
            return
        for event in events:
            await self._deliver(sink, event)
            await asyncio.sleep(0)

    async def _deliver(self, sink: Callable[[Any], None], event: Any) -> None:
        try:
            self._last_ingest_thread = _current_thread()
            sink(event)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - 收口失败不得打断团队执行
            self._log(
                "warning",
                event="writing_collab_event_delivery_failed",
                run_id=str(getattr(event, "run_id", "") or ""),
                event_type=str(getattr(event, "type", "") or ""),
                error_type=type(error).__name__,
                error=str(error),
            )

    # ---- 后台执行 -------------------------------------------------------------

    async def _drive(
        self,
        *,
        run_id: str,
        handle: Any,
        action: str,
        batch_id: str,
        planned: tuple[_PlannedMember, ...],
        frozen_skills: tuple[tuple[str, str], ...],
    ) -> None:
        try:
            with frozen_skills_context(frozen_skills):
                results = await handle.dispatch(
                    batch_id=batch_id, tasks=[item.task for item in planned]
                )
                await self._repair_round(
                    handle=handle,
                    action=action,
                    batch_id=batch_id,
                    planned=planned,
                    results=results,
                    frozen_skills=frozen_skills,
                )
        except asyncio.CancelledError:
            await self._finalize(handle, run_id, delivered=False)
            raise
        except Exception as error:  # noqa: BLE001 - 后台批次失败必须收口，不能留下 running
            self._log(
                "warning",
                event="writing_collab_batch_failed",
                run_id=run_id,
                batch_id=batch_id,
                error_type=type(error).__name__,
                error=str(error),
            )
        await self._finalize(handle, run_id, delivered=True)

    async def _finalize(self, handle: Any, run_id: str, *, delivered: bool) -> None:
        try:
            await handle.finalize(delivered=delivered)
        except Exception as error:  # noqa: BLE001
            self._log(
                "warning",
                event="writing_collab_finalize_failed",
                run_id=run_id,
                error_type=type(error).__name__,
                error=str(error),
            )
        finally:
            self._scheduler.forget(run_id)

    async def _repair_round(
        self,
        *,
        handle: Any,
        action: str,
        batch_id: str,
        planned: tuple[_PlannedMember, ...],
        results: Sequence[Any],
        frozen_skills: tuple[tuple[str, str], ...],
    ) -> None:
        """契约 §9：格式不合规的交付允许一次修正派工（第 2 轮即上限）。"""
        malformed = self._malformed(planned, results)
        if not malformed:
            return
        if MAX_AUTO_DISPATCH_ROUNDS < 2:
            return
        retry = tuple(
            _PlannedMember(
                task=_repair_task(item, action=action),
                role_id=item.role_id,
                kind=item.kind,
            )
            for item in malformed
        )
        self._log(
            "warning",
            event="writing_collab_envelope_repair_dispatched",
            batch_id=batch_id,
            members=[item.role_id for item in retry],
        )
        with frozen_skills_context(frozen_skills):
            # 修正轮必须换 batchId：``record_dispatch`` 按 (run, batchId, 载荷指纹) 幂等。
            await handle.dispatch(
                batch_id=f"{batch_id}:repair", tasks=[item.task for item in retry]
            )

    def _malformed(
        self, planned: Sequence[_PlannedMember], results: Sequence[Any]
    ) -> tuple[_PlannedMember, ...]:
        malformed: list[_PlannedMember] = []
        for item, result in zip(planned, results, strict=False):
            if str(getattr(result, "status", "")) != "succeeded":
                continue
            task_id = str(getattr(result, "task_id", "") or "")
            text = self._member_text(task_id, fallback=str(getattr(result, "summary", "") or ""))
            if parse_envelope(text) is None:
                malformed.append(item)
        return tuple(malformed)

    def _member_text(self, task_id: str, *, fallback: str) -> str:
        if not task_id:
            return fallback
        try:
            full = self._service.repository.get_task_full_result(task_id)
        except Exception:  # noqa: BLE001 - 读不到全文就按摘要判定
            return fallback
        return full or fallback

    async def _expand_upstream(self, task_id: str) -> str | None:
        """下游准备阶段取上游**完整产出**（长稿不被 8000 字符摘要截断）。"""
        try:
            return await asyncio.to_thread(
                self._service.repository.get_task_full_result, task_id
            )
        except Exception:  # noqa: BLE001 - 没有全文就回落 summary
            return None

    # ---- 会话绑定与工件 -------------------------------------------------------

    async def _article_snapshot(self, frozen: Mapping[str, Any]) -> dict[str, Any]:
        article_id = str(frozen.get("article_id") or "")
        if not article_id or self._articles is None:
            return {}
        getter = getattr(self._articles, "get_article", None)
        if not callable(getter):
            # 允许注入 ``article_id -> article`` 的轻量取值函数（装配层与测试）。
            snapshot = self._articles(article_id)
            return dict(snapshot or {}) if isinstance(snapshot, Mapping) else {}
        payload = await asyncio.to_thread(getter, article_id)
        article = payload.get("article") if isinstance(payload, Mapping) else None
        return dict(article) if isinstance(article, Mapping) else {}

    def _bind_session(self, session_key: str, binding: TeamBinding) -> None:
        """把绑定写进 ``session.metadata["team_binding"]``；写不进去只记日志。

        契约 §2：不用 TTL 暂存表，直接落会话元数据（会话文件由会话层负责保存）。
        """
        try:
            if self._binding_sink is not None:
                self._binding_sink(session_key, binding)
                return
            store = self._session_store
            if store is None:
                self._log(
                    "warning",
                    event="writing_collab_session_binding_skipped",
                    session_key=session_key,
                    reason="no session store configured",
                )
                return
            session = store.load(session_key)
            if session is None:
                self._log(
                    "warning",
                    event="writing_collab_session_binding_skipped",
                    session_key=session_key,
                    reason="session not found",
                )
                return
            session.metadata[SESSION_TEAM_BINDING_KEY] = binding.to_dict()
            save = getattr(store, "save", None)
            if callable(save):
                save(session)
        except Exception as error:  # noqa: BLE001 - 绑定写不进去不影响批次执行
            self._log(
                "warning",
                event="writing_collab_session_binding_failed",
                session_key=session_key,
                error_type=type(error).__name__,
                error=str(error),
            )

    def _log(self, level: str, **fields: Any) -> None:
        emit = getattr(self._logger.bind(**fields), level, None)
        if callable(emit):
            emit(str(fields.get("event") or "writing collaboration team port"))


# ---- 模块级工具 ---------------------------------------------------------------


def _delivery_pairs(recipe: TaskRecipe) -> tuple[tuple[str, str], ...]:
    """(角色, 该角色在本动作里要交付的产物类型)。

    取「角色配套 ∩ 动作配方」；配方里只有主编（``ideate``）时用主编角色，产物由被顶上的
    成员按主编身份交付——协作批次没有 Leader LLM 回合，主编只能以冻结技能正文的形式参与。
    """
    candidates = recipe.roles or (recipe.lead_role,)
    pairs: list[tuple[str, str]] = []
    for role_id in candidates:
        kinds = [
            kind
            for kind in ROLE_ARTIFACT_KINDS.get(role_id, ())
            if kind in recipe.artifact_kinds
        ]
        if kinds:
            pairs.append((role_id, kinds[0]))
    return tuple(pairs)


def _article_context(article: Mapping[str, Any] | None) -> str:
    if not isinstance(article, Mapping):
        return ""
    title = str(article.get("title") or "")
    body = str(article.get("body") or "")
    lines = ["## 文章上下文", ""]
    if title:
        lines.append(f"标题：{title}")
    if body:
        excerpt = body[:MAX_ARTICLE_EXCERPT_CHARS]
        suffix = "" if len(body) <= MAX_ARTICLE_EXCERPT_CHARS else "\n（正文过长，已按上下文预算摘录开头）"
        lines.append(f"正文：\n{excerpt}{suffix}")
    outline = article.get("outline")
    if isinstance(outline, Sequence) and not isinstance(outline, (str, bytes)):
        items: list[str] = []
        for index, item in enumerate(cast("Sequence[Any]", outline)):
            if index >= MAX_OUTLINE_ITEMS:
                items.append("…")
                break
            if isinstance(item, Mapping):
                text = str(item.get("text") or "")
            else:
                text = str(item or "")
            if text:
                items.append(f"{index + 1}. {text}")
        if items:
            lines.append("提纲：\n" + "\n".join(items))
    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


def _selection_block(selection: Mapping[str, Any] | None) -> str:
    if not isinstance(selection, Mapping):
        return ""
    text = str(selection.get("text") or "")
    if not text.strip():
        return ""
    start = selection.get("start")
    end = selection.get("end")
    header = f"## 本次选区（{start}-{end}，Python 码点）" if start is not None else "## 本次选区"
    return f"{header}\n\n{text}"


def _repair_task(item: _PlannedMember, *, action: str) -> TeamTaskInput:
    """修正轮任务：只重复交付格式，不改变角色/产物类型（配方仍由白名单把关）。"""
    instruction = (
        f"你上一次的交付无法解析成协作产物（动作 `{action}`，期望 `{item.kind}`，"
        f"角色 `{item.role_id}`）。\n\n"
        "请**只**重新输出一次交付：不要解释、不要复述上文、不要添加前后缀，"
        "整段回复就是一个 ```writing-envelope``` 代码块，里面的 JSON 必须能被解析。\n\n"
        + item.task.instruction
    )
    return TeamTaskInput(
        key=item.task.key,
        member_id=item.task.member_id,
        title=item.task.title,
        instruction=instruction[:MAX_TASK_INSTRUCTION_CHARS],
        acceptance=item.task.acceptance,
        resource_keys=list(item.task.resource_keys),
    )


def _current_thread() -> int:
    return threading.get_ident()
