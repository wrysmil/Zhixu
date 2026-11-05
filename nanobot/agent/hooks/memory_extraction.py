"""MemoryExtractionHook：把三层记忆提取接入 AgentHook 生命周期。

设计依据：`docs/记忆系统/plan/阶段二设计_记忆提取.md`
§2（触发时机）、§6.5.3（T0 与 intent 集成）、§7.3（签名）、§9（错误处理与并发）、
§10 Task 7 / Task 12。

触发点
------
T0   ``after_run``
     取最后一条 user 消息 → ``classify_intent``；CHAT 不写，其余写
     ``scratchpad.current_focus``（同步、不调 LLM）。
T0'  ``on_error``
     异常退出时紧急保存 ``current_focus``（不调 LLM、不调 extractor）。
T1   ``after_run``
     ``after_run`` arm（或重置）本会话的 idle 定时器：sleep ``IDLE_THRESHOLD_SECONDS``
     后调 ``MemoryExtractor.run_idle_extraction``。定时器是 fire-and-forget，跨 run
     存活 —— 只有同一会话的**下一次** ``after_run`` 才会取消并替换它，因此
     ``on_finally`` 绝不可取消它（见该回调 docstring）。

（历史注：原 T5 ``before_iteration`` 话题切换检测已被移除——它每轮引入额外
LLM 调用，性能代价大于收益；记忆提取统一由 T1 idle 定时器与会话结束完整提取完成。）

统一提取入口（契约 §2）
-----------------------
所有触发（T1 idle 定时器、增量提取、明确的记忆写入）最终都经
``MemoryExtractor`` 的薄适配进入 ``process_request(ExtractionRequest)``：
适配层只把「会话 / 切片参数」转换成 ``(workspace_id, session_key, generation,
through_seq]`` 并翻译回执，**不各自推进游标**。进度只有一处真相源
（``session_extraction_state.last_seq``），由批次事务提交；本 hook 只负责
「何时提交请求」。

失败隔离
--------
四个回调的内部逻辑各自 ``try/except``，异常只 ``logger.warning`` / ``logger.exception``，
绝不上抛到 agent 主循环；``AgentHook._reraise`` 保持默认 ``False``。

装配
----
``AgentRunHookContext`` 不含 ``session_key``，因此优先通过
:func:`create_memory_extraction_hook_factory` 构造**按轮工厂**，由
``AgentTurnHookContext.session_key`` 解析出 per-session 的 extractor 与 runtime。
``MemoryExtractionHook(extractor, session_key, scratchpad_writer)`` 仍可直接实例化（单测）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.hook import (
    AgentHook,
    AgentRunHookContext,
    AgentTurnHookContext,
    AgentTurnHookFactory,
)
from nanobot.memory.intent import IntentType, classify_intent
from nanobot.memory.signals import (
    IDLE_EXTRACTION_OP_ID,
    STATUS_CANCELLED,
    STATUS_FAILED,
    MemoryOperationKey,
    MemoryOperationRegistry,
    classify_extraction_result,
)
from nanobot.session.labels import session_label_suffix
from nanobot.session.manager import Session

if TYPE_CHECKING:
    from nanobot.memory.extractor import MemoryExtractor
    from nanobot.memory.scratchpad_writer import ScratchpadWriter
    from nanobot.utils.llm_runtime import LLMRuntime

# WU-A: per-session idle 定时器强引用表。``MemoryExtractionHook._arm_idle_timer``
# 每次 after_run 都会取消旧任务并创建新任务,事件循环只持有任务的弱引用,
# 必须在此保强引用防止未完成即被 GC。键为 ``session_key``,值为当前正在等
# 待/执行的空闲增量提取任务。
_PENDING_IDLE_TIMERS: dict[str, asyncio.Task[Any]] = {}


def _cancel_pending_idle_timer(session_key: str) -> asyncio.Task[Any] | None:
    """取消并弹出指定会话的 idle 任务;返回旧任务以便调用方 await 取消完成。

    - 若该会话当前没有 pending 任务,返回 ``None``(不做任何事)。
    - 若任务已经完成,从 dict 中弹出即可,不再 cancel(避免 ``InvalidStateError``)。
    """
    task = _PENDING_IDLE_TIMERS.pop(session_key, None)
    if task is None or task.done():
        return None
    task.cancel()
    return task


# ---------------------------------------------------------------------------
# 消息处理工具（避免依赖 extractor 的私有函数）
# ---------------------------------------------------------------------------


def _content_text(content: Any) -> str:
    """把消息 content（str / content-block list / 其他）归一化为纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return str(content)


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    """返回消息列表中最后一条非空 user 文本；无则返回空串。"""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        text = _content_text(message.get("content")).strip()
        if text:
            return text
    return ""

# ---------------------------------------------------------------------------
# MemoryExtractionHook
# ---------------------------------------------------------------------------


class MemoryExtractionHook(AgentHook):
    """在 agent 生命周期触发三层记忆提取的 AgentHook（plan §7.3）。"""

    #: WU-A: idle 定时器阈值(秒)。每轮 after_run 重置定时器,空闲超过该时长则
    #: 触发 ``MemoryExtractor.run_idle_extraction``。测试可覆盖实例属性以缩短。
    IDLE_THRESHOLD_SECONDS: float = 120.0
    #: ``current_focus`` 截断长度（plan §6.2）。
    FOCUS_MAX_CHARS: int = 200

    def __init__(
        self,
        extractor: MemoryExtractor,
        session_key: str,
        scratchpad_writer: ScratchpadWriter,
        *,
        runtime: LLMRuntime | None = None,
        memory_enabled_provider: "Callable[[], bool] | None" = None,
        idle_seconds: float | None = None,
        label: str = "",
        workspace_scope: dict[str, str] | None = None,
        operation_registry: MemoryOperationRegistry | None = None,
        namespace: str = "default",
        memory_file_mode: bool = False,
    ) -> None:
        super().__init__()
        self._extractor = extractor
        self._session_key = session_key
        self._scratchpad_writer = scratchpad_writer
        # 会话摘要（WebUI 侧边栏那一行）：只用于日志，让「哪个会话」一眼可辨。
        # 由 ``create_memory_extraction_hook_factory`` 的 ``label_provider`` 注入；
        # 直接实例化（单测）时缺省为空，日志不打方括号段。
        self._session_label = label
        # 未显式注入时回落到 extractor 自带的 runtime（plan §7.3 无 runtime 参数）。
        self._runtime = runtime if runtime is not None else getattr(extractor, "runtime", None)
        # Hot-switchable user-facing memory toggle. ``None`` preserves legacy
        # behaviour (always-on when the loop registered extraction). When set,
        # every callback short-circuits to a no-op while the toggle is off so
        # that flipping it in the WebUI takes effect without restarting the
        # gateway.
        self._memory_enabled_provider = memory_enabled_provider
        # 会话归属工作区（记忆隔离口径）。idle / 增量提取会**重建** Session，
        # 若不把归属带过去，重建出来的 Session 没有 ``workspace_scope``，
        # 提取落库会退化成全局记忆（workspace_id='default'）→ 其他工作区
        # 也能召回本工作区的记忆（跨工作区泄漏）。
        self._workspace_scope: dict[str, str] | None = (
            dict(workspace_scope) if workspace_scope else None
        )
        # 完成信号登记簿（可选注入）。缺省时完全不与 registry 交互，生产行为不变；
        # 注入后 idle/增量抽取的真实结果可被评测等待。
        self._operation_registry = operation_registry
        self._namespace = namespace
        # 文件模式：空闲触发停用，抽取改由 cron ``memory_extract`` 定时驱动。
        # 「用户离开就烧钱、连续聊反而不记」是空闲触发的固有缺陷。
        self._memory_file_mode = memory_file_mode

        # WU-A: 允许通过构造参数覆盖 idle 阈值(供 AgentDefaults.memory_idle_seconds
        # 等配置源注入)。实例属性优先于类属性。
        if idle_seconds is not None:
            self.IDLE_THRESHOLD_SECONDS = float(idle_seconds)

        # WU-A: idle 定时器状态。每个 hook 实例只对应一个 session_key,因此无需
        # 维护 _pending_tasks 集合;模块级 _PENDING_IDLE_TIMERS 按 session_key
        # 维护,由 _arm_idle_timer 负责取消/替换。

    def _memory_disabled(self) -> bool:
        """Return ``True`` when the user-facing memory toggle is wired and off.

        When no provider is wired (legacy behaviour) this returns ``False`` so
        callbacks proceed unchanged.
        """
        provider = self._memory_enabled_provider
        if provider is None:
            return False
        try:
            return not provider()
        except Exception:
            logger.warning(
                "MemoryExtractionHook: memory_enabled_provider raised; treating as enabled"
            )
            return False

    def _label_suffix(self) -> str:
        """日志用会话摘要后缀（`` [摘要: …]``）；无标签时为空串。"""
        return session_label_suffix(self._session_label)

    # ------------------------------------------------------------------
    # AgentHook 回调
    # ------------------------------------------------------------------

    async def before_run(self, context: AgentRunHookContext) -> None:
        """本轮有新消息 → 把该会话的 idle 计时器**归零**（只取消，不装备）。

        语义（2026-09-15 spec §3）：触发时刻 = ``max(最后一条用户消息,
        最后一轮结束) + 阈值``。此前只有 ``after_run`` 会装备计时器，于是
        「用户打断上一轮」的那一轮不计入重置 —— 计时器量的是「距上一轮正常
        结束」，用户连发消息时仍会到点。

        这里**不能**顺手装备：``before_run`` 时刻本轮还没跑完，装备会让长轮次
        （工具调用几分钟）在**对话进行中**被抽。装备仍绑定在已定格的时间点上
        ——正常结束走 :meth:`after_run`，被打断/失败走 :meth:`on_finally`。
        """
        if self._memory_disabled():
            return
        try:
            _cancel_pending_idle_timer(self._session_key)
        except Exception:
            logger.exception(
                "MemoryExtractionHook.before_run reset failed for session {}",
                self._session_key,
            )

    async def after_run(self, context: AgentRunHookContext) -> None:
        """T0 即时同步 + T1 idle 定时器启动（plan §2.3 / WU-A §3）。"""
        if self._memory_disabled():
            return
        try:
            await self._apply_immediate_focus(context)
        except Exception:
            logger.exception(
                "MemoryExtractionHook.after_run T0 failed for session {}",
                self._session_key,
            )

        try:
            self._arm_idle_timer(context)
        except Exception:
            logger.exception(
                "MemoryExtractionHook.after_run T1 arm failed for session {}",
                self._session_key,
            )

    async def on_error(self, context: AgentRunHookContext) -> None:
        """T0' 紧急保存 ``current_focus``（不调 LLM、不调 extractor）。"""
        if self._memory_disabled():
            return
        try:
            message = _last_user_message(context.messages)
            if not message:
                return
            await self._scratchpad_writer.update_focus(
                message[: self.FOCUS_MAX_CHARS],
            )
        except Exception:
            logger.warning(
                "MemoryExtractionHook.on_error emergency focus save failed for session {}",
                self._session_key,
            )

    async def on_finally(self, context: AgentRunHookContext) -> None:
        """被打断/失败的轮次在此**装备** idle 定时器；正常路径不碰定时器。

        **不能取消 idle 定时器。** ``after_run`` 与 ``on_finally`` 属于同一次
        ``AgentRunner.run``：after_run 在 ``return`` 前调用，on_finally 在紧随其后的
        ``finally`` 块调用（runner.py:346 / 352），两者相隔微秒。若在此取消，刚 arm
        的定时器会在协程被事件循环首次调度之前就被 cancel —— 协程体一行都不会执行，
        ``except CancelledError`` 也不会跑到，表现为"日志停在 idle timer armed"。
        定时器生命周期由 ``_arm_idle_timer`` 自己管理：同一会话的下一次 after_run
        取消并替换旧的；进程退出时随事件循环销毁。

        **新增（2026-09-15 spec §3）：只对 ``cancelled`` / ``error`` 装备。**
        ``AgentRunner`` 在这两条出口上直接 ``raise``，**不会**调用 ``after_run``
        （runner.py 的 ``except`` 分支），若不在此补装备，会留下一个漏洞：用户说完
        最后一条、那一轮被打断后就再没人装备计时器，这段对话永远不会走 idle 抽取。
        正常结束（``stop`` / ``length`` / ``tool_calls``）由 ``after_run`` 负责，
        这里用 ``stop_reason`` 守卫，不会覆盖它刚装的那个。

        **不能派发 SessionEndEvent。** ``on_finally`` 是**每条消息**那次 run 的收尾，
        不是会话结束；在此派发会把每条消息都当成会话结束处理，与 idle 定时器的增量
        抽取重复消费同一份转录。会话结束的处理由真正的会话结束事件负责
        （``nanobot.memory.session_end_event`` → ``MemoryExtractor.flush_session``）。
        """
        if self._memory_disabled():
            return
        if context.stop_reason not in ("cancelled", "error"):
            return
        try:
            self._arm_idle_timer(context)
        except Exception:
            logger.exception(
                "MemoryExtractionHook.on_finally re-arm failed for session {}",
                self._session_key,
            )

    # ------------------------------------------------------------------
    # T0：即时同步焦点
    # ------------------------------------------------------------------

    async def _apply_immediate_focus(self, context: AgentRunHookContext) -> None:
        message = _last_user_message(context.messages)
        if not message:
            return
        if classify_intent(message) == IntentType.CHAT:
            logger.debug("T0 skip: chat intent for session {}", self._session_key)
            return
        await self._scratchpad_writer.update_focus(
            message[: self.FOCUS_MAX_CHARS],
        )

    # ------------------------------------------------------------------
    # 增量抽取辅助（供 ``_run_incremental_extraction`` / 未来增量路径复用）
    # ------------------------------------------------------------------

    def _compute_incremental_start_index(self, messages: list[dict[str, Any]]) -> int:
        """返回倒数第二条非空 user 消息之后的起始下标。

        增量抽取保留最新 user 消息及其后的 assistant / tool 消息，不重扫更早的
        历史。若找不到倒数第二条非空 user 消息（例如只有一条 user 消息），返回 0。
        """
        user_indices: list[int] = []
        for index, message in enumerate(messages):
            if message.get("role") != "user":
                continue
            text = _content_text(message.get("content")).strip()
            if not text:
                continue
            user_indices.append(index)

        if len(user_indices) < 2:
            return 0
        return user_indices[-2] + 1

    # ------------------------------------------------------------------
    # WU-A T1：idle 定时器增量提取
    # ------------------------------------------------------------------

    def _arm_idle_timer(self, context: AgentRunHookContext) -> None:
        """注册或重置当前会话的 idle 定时器。

        - 若该 session_key 已有 pending idle 任务(尚未完成),先取消;
          ``_wait_then_extract_idle`` 会捕获 ``CancelledError`` 并直接返回。
        - 新任务的等待时长 = ``self.IDLE_THRESHOLD_SECONDS``(可在 ``__init__``
          或运行时通过覆盖类/实例属性调整)。
        - 完成后通过 ``add_done_callback`` 把任务从 ``_PENDING_IDLE_TIMERS``
          弹出,防止 dict 无限增长。
        """
        if self._memory_file_mode:
            # 文件模式：抽取由 cron 定时驱动，空闲触发整体停用。
            return
        # 取消旧任务(若有)。
        _cancel_pending_idle_timer(self._session_key)
        # 启动新任务。
        task = asyncio.create_task(self._wait_then_extract_idle(context))
        _PENDING_IDLE_TIMERS[self._session_key] = task
        if self._operation_registry is not None:
            self._operation_registry.register_pending(
                self._namespace, self._session_key, task
            )

        def _drop(_t: asyncio.Task[Any]) -> None:
            # 仅当当前 dict 仍持有此任务时才 pop,避免覆盖更近一次的 arm。
            current = _PENDING_IDLE_TIMERS.get(self._session_key)
            if current is _t:
                _PENDING_IDLE_TIMERS.pop(self._session_key, None)

        task.add_done_callback(_drop)

    async def _wait_then_extract_idle(self, context: AgentRunHookContext) -> None:
        """sleep 阈值秒后调用 ``run_idle_extraction``;被 cancel 则直接返回。"""
        try:
            await asyncio.sleep(self.IDLE_THRESHOLD_SECONDS)
        except asyncio.CancelledError:
            # 同一会话的新一轮 after_run 触发了 _arm_idle_timer 重置,定时器被取消属正常路径。
            logger.debug(
                "idle timer cancelled by new message for session {}", self._session_key
            )
            return
        logger.info(
            "idle timer fired for session {}{} after {}s idle; starting incremental extraction "
            "({} messages)",
            self._session_key,
            self._label_suffix(),
            self.IDLE_THRESHOLD_SECONDS,
            len(context.messages),
        )
        await self._run_idle_extraction(context)

    def _session_metadata(self) -> dict[str, Any]:
        """重建 Session 时使用的元数据（携带归属工作区）。"""
        if not self._workspace_scope:
            return {}
        return {"workspace_scope": dict(self._workspace_scope)}

    async def _run_idle_extraction(self, context: AgentRunHookContext) -> None:
        """构造 Session 并调用 ``MemoryExtractor.run_idle_extraction``。

        重建的转录来自 provider 转录（身份字段已剥离），因此坐标解析与批次
        消费都在 extractor 侧完成：``run_idle_extraction`` 是**薄适配**，最终
        提交 ``ExtractionRequest`` 给 ``process_request``；hook 既不推进游标，
        也不把一次 run 当成会话结束（语义与 ``on_finally`` 注释一致）。

        失败隔离:任何异常仅 warning,绝不上抛。
        """
        session = Session(
            key=self._session_key,
            messages=[dict(message) for message in context.messages],
            metadata=self._session_metadata(),
        )
        op_key = self._start_extraction_operation()
        try:
            result = await self._extractor.run_idle_extraction(session)
        except asyncio.CancelledError:
            self._finish_extraction_operation(
                op_key, status=STATUS_CANCELLED, detail={"reason": "cancelled"}
            )
            raise
        except Exception as exc:
            logger.warning(
                "idle memory extraction failed for session {}: {}",
                self._session_key,
                exc,
            )
            self._finish_extraction_operation(
                op_key, status=STATUS_FAILED, error=str(exc)
            )
            return
        logger.info(
            "idle memory extraction finished for session {}{}: memories={} episodes={} "
            "skipped={} failed={}",
            self._session_key,
            self._label_suffix(),
            getattr(result, "memory_ids", None),
            getattr(result, "episode_ids", None),
            getattr(result, "skipped", None),
            getattr(result, "failed_tracks", None),
        )
        status, tracks, detail = classify_extraction_result(result)
        self._finish_extraction_operation(
            op_key, status=status, tracks=tracks, detail=detail
        )

    async def _run_incremental_extraction(
        self,
        messages: list[dict[str, Any]],
        last_extracted_index: int,
    ) -> None:
        """在后台执行增量抽取（薄适配 → ``process_request``），失败仅 warning。"""
        session = Session(
            key=self._session_key,
            messages=[dict(message) for message in messages],
            metadata=self._session_metadata(),
        )
        op_key = self._start_extraction_operation()
        try:
            result = await asyncio.wait_for(
                self._extractor.extract_incremental(session, last_extracted_index),
                timeout=30.0,
            )
        except asyncio.CancelledError:
            self._finish_extraction_operation(
                op_key, status=STATUS_CANCELLED, detail={"reason": "cancelled"}
            )
            raise
        except Exception as exc:
            logger.warning(
                "incremental memory extraction failed for session {}: {}",
                self._session_key,
                exc,
            )
            self._finish_extraction_operation(
                op_key, status=STATUS_FAILED, error=str(exc)
            )
            return
        logger.info(
            "incremental memory extraction finished for session {}",
            self._session_key,
        )
        status, tracks, detail = classify_extraction_result(result)
        self._finish_extraction_operation(
            op_key, status=status, tracks=tracks, detail=detail
        )

    # ------------------------------------------------------------------
    # 完成信号（可选注入）
    # ------------------------------------------------------------------

    def _start_extraction_operation(self) -> MemoryOperationKey | None:
        if self._operation_registry is None:
            return None
        key = MemoryOperationKey(
            namespace=self._namespace,
            session_key=self._session_key,
            op_id=IDLE_EXTRACTION_OP_ID,
        )
        self._operation_registry.start(key, tracks=("extraction",))
        return key

    def _finish_extraction_operation(
        self,
        op_key: MemoryOperationKey | None,
        *,
        status: str,
        tracks: dict[str, str] | None = None,
        detail: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if self._operation_registry is None or op_key is None:
            return
        self._operation_registry.finish(
            op_key,
            status=status,
            tracks=tracks,
            detail=detail,
            error=error,
        )


# ---------------------------------------------------------------------------
# 按轮工厂（plan §7.1：由 loop.py 注册）
# ---------------------------------------------------------------------------


def create_memory_extraction_hook_factory(
    *,
    extractor_provider: Callable[[str], MemoryExtractor],
    scratchpad_writer: ScratchpadWriter | None = None,
    scratchpad_writer_for_key: Callable[[str], ScratchpadWriter] | None = None,
    runtime_provider: Callable[[str], LLMRuntime | None] | None = None,
    memory_enabled_provider: Callable[[], bool] | None = None,
    label_provider: Callable[[str], str] | None = None,
    idle_seconds: float | None = None,
    workspace_scope_provider: Callable[[AgentTurnHookContext], dict[str, str] | None] | None = None,
    operation_registry: MemoryOperationRegistry | None = None,
    namespace: str = "default",
    memory_file_mode: bool = False,
) -> AgentTurnHookFactory:
    """返回 ``AgentTurnHookFactory``：从 ``AgentTurnHookContext`` 取 ``session_key``
    构造 :class:`MemoryExtractionHook`。

    Args:
        extractor_provider: ``session_key -> MemoryExtractor``，为每个会话解析提取器。
        scratchpad_writer: 共享的 scratchpad 写入器（向后兼容单实例场景，如单测）。
        scratchpad_writer_for_key: ``session_key -> ScratchpadWriter``，FIX-1 Sec-C-α
            推荐入口；优先使用本参数以实现 per-session 隔离。若两者同时给出，
            ``scratchpad_writer_for_key`` 胜出。
        runtime_provider: 可选的 ``session_key -> LLMRuntime | None``，注入
            ``MemoryExtractionHook._runtime``（原 T5 话题切换检测消费方已移除，
            当前仅作为实例持有的 LLM 运行时）；缺省时回落 ``extractor.runtime``。
        memory_enabled_provider: 用户侧记忆总开关，关闭时所有回调短路。
        label_provider: 可选的 ``session_key -> str``，返回日志用的会话摘要
            （WebUI 侧边栏那一行：标题优先、空则首条用户消息）。缺省时日志不带摘要。
            失败隔离：provider 抛错时按空标签处理。
        idle_seconds: WU-A 可选的 idle 阈值(秒),转发给 ``MemoryExtractionHook``。
            通常来自 ``AgentDefaults.memory_idle_seconds``。

    Returns:
        ``AgentTurnHookFactory``：``session_key`` 缺失或 provider 失败时返回 ``None``，
        由 ``build_agent_turn_hook`` 静默跳过。
    """
    writer_factory = scratchpad_writer_for_key
    if writer_factory is None:
        if scratchpad_writer is None:
            raise ValueError(
                "create_memory_extraction_hook_factory requires either "
                "scratchpad_writer or scratchpad_writer_for_key"
            )
        # 共享单实例：所有会话共用同一 writer（向后兼容单测 / 单用户场景）。
        writer_factory = lambda _key: scratchpad_writer  # noqa: E731

    def _factory(context: AgentTurnHookContext) -> AgentHook | None:
        session_key = context.session_key
        if not session_key:
            return None

        try:
            extractor = extractor_provider(session_key)
        except Exception:
            logger.exception(
                "memory extraction hook factory: extractor_provider failed for {}",
                session_key,
            )
            return None
        if extractor is None:
            return None

        try:
            writer = writer_factory(session_key)
        except Exception:
            logger.exception(
                "memory extraction hook factory: scratchpad_writer_for_key failed for {}",
                session_key,
            )
            return None

        runtime: LLMRuntime | None = None
        if runtime_provider is not None:
            try:
                runtime = runtime_provider(session_key)
            except Exception:
                logger.exception(
                    "memory extraction hook factory: runtime_provider failed for {}",
                    session_key,
                )

        label = ""
        if label_provider is not None:
            try:
                label = label_provider(session_key) or ""
            except Exception:
                logger.exception(
                    "memory extraction hook factory: label_provider failed for {}",
                    session_key,
                )

        workspace_scope: dict[str, str] | None = None
        if workspace_scope_provider is not None:
            try:
                workspace_scope = workspace_scope_provider(context)
            except Exception:
                logger.exception(
                    "memory extraction hook factory: workspace_scope_provider failed for {}",
                    session_key,
                )

        return MemoryExtractionHook(
            extractor,
            session_key,
            writer,
            runtime=runtime,
            memory_enabled_provider=memory_enabled_provider,
            idle_seconds=idle_seconds,
            label=label,
            workspace_scope=workspace_scope,
            operation_registry=operation_registry,
            namespace=namespace,
            memory_file_mode=memory_file_mode,
        )

    return _factory
