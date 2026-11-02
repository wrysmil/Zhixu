"""会话结束 4 步编排器（S1）。"""
from __future__ import annotations

import time
from typing import Any

from loguru import logger

from nanobot.memory.clock import MemoryClock
from nanobot.memory.session_end_event import SessionEndEvent
from nanobot.memory.signals import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    MemoryOperationKey,
    MemoryOperationRegistry,
)

#: 会话结束编排在 registry 中的 op_id。
SESSION_END_OP_ID = "session_end"

#: 编排器的 4 条轨道（与 plan 的 Step 1 / 2 / 3 / 4 对应）。
_TRACKS = ("episode", "profile", "experience", "scratchpad", "link_relations")


class SessionEndOrchestrator:
    """会话结束编排器：4 步依赖链 + 失败隔离 + feature flag + 幂等保护。

    可选注入：

    - ``clock``：幂等窗口（策略间隔）走注入时钟，评测可确定地推进时间。
    - ``operation_registry``：把每一步的真实结果（完成 / 失败 / 跳过）登记到
      完成信号里。**不注入时行为与改造前完全一致**（仅 warning，不外泄）。
    """

    IDEMPOTENCY_WINDOW_SECONDS: float = 30.0

    def __init__(
        self,
        *,
        extractor: Any,
        scratchpad_writer: Any | None = None,
        enable_track2: bool = False,
        enable_scratchpad_reformat: bool = False,
        clock: MemoryClock | None = None,
        operation_registry: MemoryOperationRegistry | None = None,
        namespace: str = "default",
    ) -> None:
        self._extractor = extractor
        self._scratchpad_writer = scratchpad_writer
        self._enable_track2 = enable_track2
        self._enable_scratchpad_reformat = enable_scratchpad_reformat
        self._last_run: dict[tuple[str, str], float] = {}
        # 评测时钟（可选）：幂等窗口按注入时钟判定。
        self._clock = clock
        # 完成信号登记簿（可选）：缺省时只 warning，不对外登记。
        self._registry = operation_registry
        self._namespace = namespace

    def _monotonic(self) -> float:
        """策略单调时钟（幂等窗口口径）。"""
        if self._clock is not None:
            return self._clock.monotonic()
        return time.monotonic()

    async def run(self, event: SessionEndEvent) -> None:
        # 幂等保护
        key = (event.session_key, event.reason.value)
        now = self._monotonic()
        prev = self._last_run.get(key)
        if prev is not None and (now - prev) < self.IDEMPOTENCY_WINDOW_SECONDS:
            logger.info("[S1:SessionEnd] skipped (idempotent within 30s) key={}", key)
            self._report_skip(event, "idempotent")
            return
        self._last_run[key] = now

        # ------------------------------------------------------------------
        # L0: 空 transcript 直接跳过（无对话内容）
        # ------------------------------------------------------------------
        if not event.transcript:
            logger.debug("[S1] skip: empty transcript")
            self._report_skip(event, "empty_transcript")
            return

        # ------------------------------------------------------------------
        # L1: 消息数 < 3 的短会话跳过（无足够上下文）
        # ------------------------------------------------------------------
        if len(event.transcript) < 3:
            logger.debug("[S1] skip: transcript has {} messages (< 3)", len(event.transcript))
            self._report_skip(event, "short_transcript")
            return

        # ------------------------------------------------------------------
        # L2: 单用户 + 短内容 + 无工具调用 → 跳过（有工具调用时豁免）
        # ------------------------------------------------------------------
        # 检查是否只有单个用户消息且内容很短
        user_msgs = [t for t in event.transcript if t.get("role") == "user"]
        if len(user_msgs) == 1:
            first_content = (user_msgs[0].get("content") or "").strip()
            # 检查是否有工具调用（豁免 L2）
            has_tool_calls = any(t.get("tool_calls") for t in event.transcript)
            if len(first_content) < 10 and not has_tool_calls:
                logger.debug(
                    "[S1] skip: single short message ({} chars) without tool calls",
                    len(first_content),
                )
                self._report_skip(event, "short_single_message")
                return

        op_key = self._start_operation(event)

        # Step 1: Episode
        episode = None
        try:
            episode = await self._extractor.generate_episode(
                event.transcript, event.session_key
            )
            self._report_track(op_key, "episode", STATUS_COMPLETED)
        except Exception as exc:
            logger.warning("[S1:SessionEnd] episode step failed: {}", exc)
            self._report_track(op_key, "episode", STATUS_FAILED, error=str(exc))
        ep_id = getattr(episode, "id", None) if episode is not None else None
        ep_summary = getattr(episode, "summary", "") or "" if episode is not None else ""

        # Step 2a: 用户画像（默认 ON）
        try:
            await self._extractor.extract_user_profile(
                event.transcript, ep_id, cited=None
            )
            self._report_track(op_key, "profile", STATUS_COMPLETED)
        except Exception as exc:
            logger.warning("[S3:Track1] failed: {}", exc)
            self._report_track(op_key, "profile", STATUS_FAILED, error=str(exc))

        # Step 2b: 任务经验（feature flag）
        if self._enable_track2:
            try:
                await self._extractor.extract_experience(event.transcript, ep_id)
                self._report_track(op_key, "experience", STATUS_COMPLETED)
            except Exception as exc:
                logger.warning("[S3:Track2] failed: {}", exc)
                self._report_track(op_key, "experience", STATUS_FAILED, error=str(exc))

        # Step 3: Scratchpad 重构（feature flag，依赖 episode）
        if (
            self._enable_scratchpad_reformat
            and self._scratchpad_writer is not None
            and ep_summary
        ):
            try:
                await self._scratchpad_writer.format_with_llm(
                    None,
                    ep_summary,
                )
                self._report_track(op_key, "scratchpad", STATUS_COMPLETED)
            except Exception as exc:
                logger.warning("[S4:Scratchpad] failed: {}", exc)
                self._report_track(op_key, "scratchpad", STATUS_FAILED, error=str(exc))

        # Step 4: 关联回填（episode ↔ memories ↔ turns）—— spec §1.1 / §7.1
        await self._step_link_relations(ep_id, op_key)

    async def _step_link_relations(self, ep_id: Any, op_key: Any = None) -> None:
        """关联回填：失败重试 1 次，仍失败仅告警（spec §1.2 失败隔离）。"""
        linker = getattr(self._extractor, "link_relations", None)
        if linker is None or ep_id is None:
            self._report_track(op_key, "link_relations", STATUS_SKIPPED)
            return
        # 占位：调用方提供 link_relations(episode_id, memory_ids, turn_ids)
        # 当前 _build_orchestrator_extractor 适配器未实现，按 no-op 处理。
        # 生产应由 repository.update_episode + link_turns_to_episode 实现。
        try:
            result = linker(ep_id, [], [])
            if hasattr(result, "__await__"):
                await result
            self._report_track(op_key, "link_relations", STATUS_COMPLETED)
        except Exception as exc:
            logger.warning("[S1:SessionEnd] link_relations first attempt failed: {}", exc)
            # 重试 1 次
            try:
                result = linker(ep_id, [], [])
                if hasattr(result, "__await__"):
                    await result
                self._report_track(op_key, "link_relations", STATUS_COMPLETED)
            except Exception as exc2:
                logger.error(
                    "[S1:SessionEnd] link_relations retry failed: {}",
                    exc2,
                )
                self._report_track(op_key, "link_relations", STATUS_FAILED, error=str(exc2))

    # ------------------------------------------------------------------
    # 完成信号（可选注入）
    # ------------------------------------------------------------------

    def _start_operation(self, event: SessionEndEvent) -> MemoryOperationKey | None:
        if self._registry is None:
            return None
        key = MemoryOperationKey(
            namespace=self._namespace,
            session_key=event.session_key,
            op_id=SESSION_END_OP_ID,
        )
        self._registry.start(key, tracks=_TRACKS)
        return key

    def _report_track(
        self,
        op_key: MemoryOperationKey | None,
        track: str,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        if self._registry is None or op_key is None:
            return
        self._registry.track(op_key, track, status, error=error)

    def _report_skip(self, event: SessionEndEvent, reason: str) -> None:
        """策略跳过（L0/L1/L2/幂等）—— 必须显式记 skipped，不能当成功。"""
        if self._registry is None:
            return
        key = MemoryOperationKey(
            namespace=self._namespace,
            session_key=event.session_key,
            op_id=SESSION_END_OP_ID,
        )
        self._registry.start(key, tracks=_TRACKS)
        self._registry.finish(
            key,
            status=STATUS_SKIPPED,
            detail={"reason": reason},
        )
