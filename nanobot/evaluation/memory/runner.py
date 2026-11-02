"""专项场景状态机 runner（plan §6 Task 4 / WU-04a，Step 2 + Step 3）。

职责与硬约束
------------

- :func:`execution_fields`：plan §6 Step 2 的参考实现（逐字沿用）——**唯一**从原始
  场景 dict 构造执行载荷的入口，键集固定，gold / 许可 / 评分判据不可能混入。
- :func:`run_memory_scenario`：把 :class:`~nanobot.evaluation.memory.models.MemoryExecutionInput`
  里的事件序列按契约 §3 顺序喂给 adapter（session → settle → checkpoint → restore →
  restart → forget → rebuild），再执行 probes，产出 :class:`~nanobot.evaluation.memory.grading.MemoryTrialExecution`
  供 WU-05 ``grade_memory_trial`` 评分。

状态机语义（本模块的确定性约定，WU-04b 依赖）：

- **两阶段时间线**：先按列表顺序处理全部 events，再按列表顺序执行 probes。
  probe 的逻辑时点由 ``checkpoint_id`` 表达——带 ``checkpoint_id`` 的 probe 在
  恢复出的快照副本上作答（clone 隔离），不带的使用相位 1 结束后的实时状态。
  因此：发生过成功的 forget 之后，所有非 checkpoint probe 都记 ``after_forget=True``；
  checkpoint 快照在 forget 之前捕获的 probe 记 ``False``。
- **密封屏障**：只有在至少一次 ``settle`` 完成（或 retrieval 轨 reference seed 成功）
  之后才允许 snapshot 注册；probe 引用未注册的 checkpoint 会被记账
  （``checkpoint_unknown``）并产出 not_assessable 的 probe 结果，不剔除、不伪装成功。
- **probe 副本隔离**：第一次 restore 前先对实时状态拍 base 快照；不带
  ``checkpoint_id`` 的 probe 在进入前恢复 base，避免被前一个 checkpoint probe 污染。
- **三轨语义**：``extraction`` 只导出评分材料（extracted_facts / state_observations /
  source_supported_facts），不应答 probe；``retrieval`` 跳过历史 session/settle，
  用 ``reference_state`` 播种且禁用历史抽取；``end_to_end`` 全量真实查询。
  每个 probe 独立产出 answer / coverage / usage，绝不只保留最后回答。
- **失败账目**：adapter 异常、settle timeout / failed、forget unsupported、observer
  损坏、取消（CancelledError 记账后原样上抛）都进入 :class:`ScenarioLedgerEntry`
  闭集，不静默吞掉。
- observer 钩子（:class:`MemoryExecutionObserver`）是 WU-04b 的接线缝：adapter
  协议本身不暴露事实/状态导出，真实实现由 WU-04b 提供，本 runner 只约定签名。
"""

from __future__ import annotations

import asyncio
from typing import Protocol, Sequence

from pydantic import StrictStr

from nanobot.evaluation.memory.grading import (
    MemoryProbeOutcome,
    MemoryStateObservation,
    MemoryTrialExecution,
)
from nanobot.evaluation.memory.models import (
    ForgetRequest,
    JsonModel,
    MemoryCheckpointEvent,
    MemoryCoverage,
    MemoryExecutionInput,
    MemoryFact,
    MemoryForgetEvent,
    MemoryProbe,
    MemoryRebuildEvent,
    MemoryReferenceState,
    MemoryRestartEvent,
    MemoryRestoreEvent,
    MemoryScope,
    MemorySessionEvent,
    MemorySettleEvent,
    MemoryStateSnapshot,
    MemoryUsage,
    ProbeExecution,
    SettleResult,
    ShortId,
)
from nanobot.evaluation.memory.protocol import MemoryAdapter

__all__ = [
    "MemoryExecutionObserver",
    "MemoryScenarioRun",
    "NullExecutionObserver",
    "ScenarioLedgerEntry",
    "build_trial_execution",
    "execution_fields",
    "run_memory_scenario",
]

#: plan §6 Step 2 的固定键集：execution 载荷只允许这些键，gold 永远不在其中。
_EXECUTION_KEYS = (
    "schema_version",
    "scenario_id",
    "revision",
    "scope",
    "events",
    "probes",
    "reference_state",
)

#: probe 副本隔离用的 base 快照 id（adapter 每 trial 一个实例，id 固定不冲突）。
_PROBE_BASE_CHECKPOINT_ID = "probe-base"

#: 账目 kind 闭集（契约 §3/§4：失败与跳过都如实留痕，不用布尔开关）。
LEDGER_KINDS = (
    "adapter_error",
    "settle_timeout",
    "settle_failed",
    "settle_skipped_by_policy",
    "checkpoint_not_sealed",
    "checkpoint_unknown",
    "forget_unsupported",
    "forget_failed",
    "forget_skipped_by_policy",
    "rebuild_timeout",
    "rebuild_failed",
    "rebuild_skipped_by_policy",
    "probe_failed",
    "probe_checkpoint_unknown",
    "probe_base_snapshot_failed",
    "probe_state_unsealed",
    "reference_state_unseeded",
    "observer_error",
    "extracted_facts_unavailable",
    "session_skipped_by_track",
    "settle_skipped_by_track",
    "probes_skipped_by_track",
    "cancelled",
)


# =========================================================================== 载荷分离


def execution_fields(scenario: dict) -> dict:
    """plan §6 Step 2 的参考实现：execution 载荷不含 gold 的唯一构造点。"""
    return {key: scenario[key] for key in _EXECUTION_KEYS if key in scenario}


# =========================================================================== 观察钩子


class MemoryExecutionObserver(Protocol):
    """WU-04b 的接线缝：把产品内部状态映射成评分材料。

    全部方法同步、可失败：返回 ``None`` / ``False`` 表示「材料不可得」（账目留痕），
    抛异常会被 runner 记 ``observer_error`` 并降级为不可得，不会伪装成空结果。
    """

    def parse_answer_facts(self, answer: str) -> list[MemoryFact] | None:
        """把回答解析成原子事实；无法解析时返回 ``None``（probe 记未解析）。"""
        ...

    def export_extracted_facts(self) -> list[MemoryFact] | None:
        """导出后端实际写入记忆的原子事实；不支持时 ``None``。"""
        ...

    def export_source_supported_facts(self) -> list[MemoryFact] | None:
        """导出源会话支持的事实全集；缺材料时 ``None``（无依据率未知）。"""
        ...

    def export_state_observations(self) -> list[MemoryStateObservation] | None:
        """导出状态层逐面观测；后端不支持状态导出时 ``None``。"""
        ...

    def seed_reference_state(self, state: MemoryReferenceState) -> bool:
        """retrieval 轨播种参考状态；成功返回 ``True``。"""
        ...


class NullExecutionObserver:
    """缺省 observer：所有材料不可得（二值判定全部落到 not_assessable）。"""

    def parse_answer_facts(self, answer: str) -> list[MemoryFact] | None:
        return None

    def export_extracted_facts(self) -> list[MemoryFact] | None:
        return None

    def export_source_supported_facts(self) -> list[MemoryFact] | None:
        return None

    def export_state_observations(self) -> list[MemoryStateObservation] | None:
        return None

    def seed_reference_state(self, state: MemoryReferenceState) -> bool:
        return False


# =========================================================================== 产物模型


class ScenarioLedgerEntry(JsonModel):
    """失败/跳过账目的一条记录（kind 取 LEDGER_KINDS 闭集）。"""

    kind: ShortId
    stage: ShortId
    detail: StrictStr = ""
    session_id: ShortId | None = None
    checkpoint_id: ShortId | None = None
    probe_id: ShortId | None = None


class MemoryScenarioRun(JsonModel):
    """一次场景执行的完整产物：评分输入 + 失败账目 + 执行覆盖。"""

    trial: MemoryTrialExecution
    failure_ledger: list[ScenarioLedgerEntry] = []
    #: probe 执行覆盖：scored = 拿到非空回答的 probe 数，planned = 实际应答数。
    coverage: MemoryCoverage = MemoryCoverage()


def build_trial_execution(
    execution: MemoryExecutionInput,
    *,
    probes: Sequence[MemoryProbeOutcome],
    extracted_facts: Sequence[MemoryFact],
    source_supported_facts: Sequence[MemoryFact] | None,
    state_observations: Sequence[MemoryStateObservation] | None,
    forgotten_fact_keys: Sequence[str],
    stage_usage: dict[str, MemoryUsage] | None = None,
    trial_index: int = 0,
) -> MemoryTrialExecution:
    """把状态机产物组装成 WU-05 ``grade_memory_trial`` 的输入。"""
    return MemoryTrialExecution(
        scenario_id=execution.scenario_id,
        scenario_revision=execution.revision,
        track=execution.track,
        scope=execution.scope,
        #: execution_fields 键集不含 tags——类别标注由父进程评分端补齐（WU-04b）。
        tags=[],
        probes=list(probes),
        extracted_facts=list(extracted_facts),
        source_supported_facts=(
            list(source_supported_facts) if source_supported_facts is not None else None
        ),
        state_observations=(
            list(state_observations) if state_observations is not None else None
        ),
        forgotten_fact_keys=list(forgotten_fact_keys),
        stage_usage=dict(stage_usage or {}),
        trial_index=trial_index,
    )


# =========================================================================== 状态机


def _detail(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text[:500]


class _ScenarioMachine:
    """单场景顺序状态机（非线程安全，一次一个 trial）。"""

    def __init__(
        self,
        execution: MemoryExecutionInput,
        adapter: MemoryAdapter,
        observer: MemoryExecutionObserver,
        *,
        trial_index: int,
    ) -> None:
        self.execution = execution
        self.adapter = adapter
        self.observer = observer
        self.trial_index = trial_index
        self.ledger: list[ScenarioLedgerEntry] = []
        self.snapshots: dict[str, MemoryStateSnapshot] = {}
        self.capture_index: dict[str, int] = {}
        self.forgotten_fact_keys: list[str] = []
        self.forgot_index: int | None = None
        self.sealed = False
        self.probe_outcomes: list[MemoryProbeOutcome] = []
        self.probe_usages: list[MemoryUsage] = []
        #: extraction 事实在 forget 前冻结；None 表示尚未导出。
        self.extracted_facts: list[MemoryFact] | None = None
        self.source_supported_facts: list[MemoryFact] | None = None
        self.state_observations: list[MemoryStateObservation] | None = None
        #: probe 副本隔离状态。
        self._base_snapshot: MemoryStateSnapshot | None = None
        self._active_checkpoint: str | None = None

    # ---- 账目 ------------------------------------------------------------

    def record(
        self,
        kind: str,
        stage: str,
        *,
        detail: str = "",
        session_id: str | None = None,
        checkpoint_id: str | None = None,
        probe_id: str | None = None,
    ) -> None:
        if kind not in LEDGER_KINDS:
            raise ValueError(f"LEDGER_KIND_UNKNOWN:{kind}")
        entry = ScenarioLedgerEntry(
            kind=kind,
            stage=stage,
            detail=detail,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            probe_id=probe_id,
        )
        #: 同一条账目只记一次：settle 点与收尾各导出一次材料是重试语义，
        #: 但重复的相同记录只会淹没真正的失败。
        for existing in self.ledger:
            if existing == entry:
                return
        self.ledger.append(entry)

    # ---- 事件处理 ---------------------------------------------------------

    async def run(self) -> MemoryScenarioRun:
        try:
            if self.execution.track == "retrieval":
                await self._seed_reference_state()
            await self._run_events()
            self._export_grading_materials()
            await self._run_probes()
        except asyncio.CancelledError:
            self.record("cancelled", "scenario", detail="CANCELLED")
            raise
        finally:
            try:
                await self.adapter.close()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - 防御：close 损坏也记账
                self.record("adapter_error", "close", detail=_detail(exc))
        return self._result()

    async def _seed_reference_state(self) -> None:
        """retrieval 轨：用 reference_state 播种，禁用历史抽取。"""
        reference = self.execution.reference_state
        if reference is None:
            self.record(
                "reference_state_unseeded", "seed_reference",
                detail="NO_REFERENCE_STATE",
            )
            return
        try:
            seeded = self.observer.seed_reference_state(reference)
        except Exception as exc:
            self.record("observer_error", "seed_reference", detail=_detail(exc))
            return
        if seeded:
            self.sealed = True
        else:
            self.record(
                "reference_state_unseeded", "seed_reference",
                detail="OBSERVER_DECLINED",
            )

    async def _run_events(self) -> None:
        for index, event in enumerate(self.execution.events):
            kind = event.type
            if kind == "session":
                await self._on_session(event, index)
            elif kind == "settle":
                await self._on_settle(event, index)
            elif kind == "checkpoint":
                await self._on_checkpoint(event, index)
            elif kind == "restore":
                await self._on_restore(event, index)
            elif kind == "restart":
                await self._on_restart(event, index)
            elif kind == "forget":
                await self._on_forget(event, index)
            elif kind == "rebuild":
                await self._on_rebuild(event, index)
            else:  # pragma: no cover - MemoryEvent 闭集已被 pydantic 校验
                raise ValueError(f"MEMORY_EVENT_UNKNOWN:{kind}")

    async def _on_session(self, event: MemorySessionEvent, index: int) -> None:
        if self.execution.track == "retrieval":
            self.record(
                "session_skipped_by_track", "session",
                detail="RETRIEVAL_TRACK", session_id=event.id,
            )
            return
        try:
            await self.adapter.ingest_session(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.record(
                "adapter_error", "session", detail=_detail(exc), session_id=event.id,
            )

    async def _on_settle(self, event: MemorySettleEvent, index: int) -> None:
        if self.execution.track == "retrieval":
            self.record(
                "settle_skipped_by_track", "settle",
                detail="RETRIEVAL_TRACK", session_id=event.after_session,
            )
            return
        try:
            result = await self.adapter.settle(event.after_session, event.timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.record(
                "adapter_error", "settle", detail=_detail(exc),
                session_id=event.after_session,
            )
            return
        self._account_settle(result, stage="settle", index=index,
                             session_id=event.after_session)

    def _account_settle(
        self, result: SettleResult, *, stage: str, index: int,
        session_id: str | None = None,
    ) -> None:
        if result.status == "completed":
            self.sealed = True
            self._refresh_extracted_facts()
            return
        kind = {
            "timeout": "settle_timeout",
            "failed": "settle_failed",
            "skipped_by_policy": "settle_skipped_by_policy",
        }[result.status]
        self.record(
            kind, stage,
            detail=";".join(result.failed_stages + result.reason_codes) or result.status,
            session_id=session_id,
        )

    async def _on_checkpoint(self, event: MemoryCheckpointEvent, index: int) -> None:
        if not self.sealed:
            self.record(
                "checkpoint_not_sealed", "checkpoint",
                detail="SETTLE_NOT_COMPLETED", checkpoint_id=event.id,
            )
            return
        try:
            snapshot = await self.adapter.snapshot(event.id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.record(
                "adapter_error", "checkpoint", detail=_detail(exc),
                checkpoint_id=event.id,
            )
            return
        self.snapshots[event.id] = snapshot
        self.capture_index[event.id] = index

    def _lookup_snapshot(self, checkpoint_id: str, stage: str) -> MemoryStateSnapshot | None:
        snapshot = self.snapshots.get(checkpoint_id)
        if snapshot is None:
            self.record(
                "checkpoint_unknown", stage,
                detail="CHECKPOINT_NOT_REGISTERED", checkpoint_id=checkpoint_id,
            )
        return snapshot

    async def _on_restore(self, event: MemoryRestoreEvent, index: int) -> None:
        snapshot = self._lookup_snapshot(event.checkpoint_id, "restore")
        if snapshot is None:
            return
        try:
            await self.adapter.restore(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.record(
                "adapter_error", "restore", detail=_detail(exc),
                checkpoint_id=event.checkpoint_id,
            )

    async def _on_restart(self, event: MemoryRestartEvent, index: int) -> None:
        snapshot = self._lookup_snapshot(event.checkpoint_id, "restart")
        if snapshot is None:
            return
        try:
            await self.adapter.restart(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.record(
                "adapter_error", "restart", detail=_detail(exc),
                checkpoint_id=event.checkpoint_id,
            )

    async def _on_forget(self, event: MemoryForgetEvent, index: int) -> None:
        request = ForgetRequest(
            mode=event.mode,
            fact_key=event.fact_key,
            target_id=event.target_id,
            scope=self.execution.scope,
        )
        try:
            result = await self.adapter.forget(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.record(
                "adapter_error", "forget", detail=_detail(exc),
            )
            return
        if result.status == "completed":
            self.forgotten_fact_keys.append(event.fact_key)
            self.forgot_index = index
        elif result.status == "unsupported":
            self.record("forget_unsupported", "forget", detail="CAPABILITY_MISSING")
        elif result.status == "failed":
            self.record(
                "forget_failed", "forget",
                detail=";".join(result.reason_codes) or result.status,
            )
        else:
            self.record("forget_skipped_by_policy", "forget", detail=result.status)

    async def _on_rebuild(self, event: MemoryRebuildEvent, index: int) -> None:
        try:
            result = await self.adapter.rebuild()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.record("adapter_error", "rebuild", detail=_detail(exc))
            return
        if result.status == "completed":
            self.sealed = True
            if self.forgot_index is None:
                self._refresh_extracted_facts()
            return
        kind = {
            "timeout": "rebuild_timeout",
            "failed": "rebuild_failed",
            "skipped_by_policy": "rebuild_skipped_by_policy",
        }[result.status]
        self.record(
            kind, "rebuild",
            detail=";".join(result.failed_stages + result.reason_codes) or result.status,
        )

    # ---- 评分材料导出 ------------------------------------------------------

    def _refresh_extracted_facts(self) -> None:
        """settle/rebuild 完成后导出抽取事实；forget 之后不再刷新（冻结在删除前）。"""
        if self.forgot_index is not None:
            return
        try:
            facts = self.observer.export_extracted_facts()
        except Exception as exc:
            self.record("observer_error", "export_extracted_facts", detail=_detail(exc))
            return
        if facts is None:
            self.record(
                "extracted_facts_unavailable", "export_extracted_facts",
                detail="OBSERVER_RETURNED_NONE",
            )
            return
        self.extracted_facts = list(facts)

    def _export_grading_materials(self) -> None:
        """统一导出 source_supported / state 观测；extracted 已在 settle 点冻结。"""
        self._refresh_extracted_facts()
        try:
            self.source_supported_facts = self.observer.export_source_supported_facts()
        except Exception as exc:
            self.record("observer_error", "export_source_supported_facts",
                        detail=_detail(exc))
        try:
            self.state_observations = self.observer.export_state_observations()
        except Exception as exc:
            self.record("observer_error", "export_state_observations", detail=_detail(exc))

    # ---- probes ------------------------------------------------------------

    def _probe_after_forget(self, probe: MemoryProbe) -> bool:
        """probe 是否处于删除后状态（见模块 docstring 的时间线约定）。"""
        if self.forgot_index is None:
            return False
        if probe.checkpoint_id is None:
            return True
        captured_at = self.capture_index.get(probe.checkpoint_id)
        return captured_at is not None and captured_at > self.forgot_index

    async def _ensure_base_snapshot(self) -> MemoryStateSnapshot | None:
        if self._base_snapshot is not None:
            return self._base_snapshot
        if not self.sealed:
            self.record(
                "probe_state_unsealed", "probe",
                detail="BASE_SNAPSHOT_BEFORE_SEAL",
            )
            return None
        try:
            self._base_snapshot = await self.adapter.snapshot(_PROBE_BASE_CHECKPOINT_ID)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.record("probe_base_snapshot_failed", "probe", detail=_detail(exc))
            return None
        return self._base_snapshot

    def _failed_probe_execution(self, probe: MemoryProbe, reason: str) -> ProbeExecution:
        return ProbeExecution(
            probe_id=probe.id,
            answer=None,
            coverage=MemoryCoverage(scored=0, planned=1, reasons=[reason]),
        )

    async def _run_probes(self) -> None:
        probes = self.execution.probes
        if self.execution.track == "extraction":
            if probes:
                self.record(
                    "probes_skipped_by_track", "probe",
                    detail=f"EXTRACTION_TRACK_COUNT={len(probes)}",
                )
            return
        for probe in probes:
            await self._run_probe(probe)

    async def _run_probe(self, probe: MemoryProbe) -> None:
        scope = probe.scope or self.execution.scope
        after_forget = self._probe_after_forget(probe)

        if probe.checkpoint_id is not None:
            snapshot = self.snapshots.get(probe.checkpoint_id)
            if snapshot is None:
                self.record(
                    "probe_checkpoint_unknown", "probe",
                    detail="CHECKPOINT_NOT_REGISTERED", probe_id=probe.id,
                    checkpoint_id=probe.checkpoint_id,
                )
                self.probe_outcomes.append(
                    self._outcome(
                        probe, self._failed_probe_execution(probe, "PROBE_CHECKPOINT_UNAVAILABLE"),
                        scope=scope, after_forget=after_forget,
                    )
                )
                return
            if self._base_snapshot is None:
                await self._ensure_base_snapshot()
            if not await self._restore_for_probe(snapshot, probe):
                return
            self._active_checkpoint = probe.checkpoint_id
        elif self._active_checkpoint is not None:
            base = await self._ensure_base_snapshot()
            if base is not None and not await self._restore_for_probe(base, probe):
                return
            self._active_checkpoint = None

        try:
            execution = await self.adapter.answer(probe)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.record("probe_failed", "probe", detail=_detail(exc), probe_id=probe.id)
            failed = self._failed_probe_execution(probe, "PROBE_EXECUTION_FAILED")
            self.probe_outcomes.append(
                self._outcome(probe, failed, scope=scope, after_forget=after_forget)
            )
            return

        self.probe_usages.append(execution.usage)
        self.probe_outcomes.append(
            self._outcome(probe, execution, scope=scope, after_forget=after_forget)
        )

    async def _restore_for_probe(
        self, snapshot: MemoryStateSnapshot, probe: MemoryProbe
    ) -> bool:
        try:
            await self.adapter.restore(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.record(
                "adapter_error", "probe", detail=_detail(exc), probe_id=probe.id,
                checkpoint_id=snapshot.checkpoint_id,
            )
            self.probe_outcomes.append(
                self._outcome(
                    probe,
                    self._failed_probe_execution(probe, "PROBE_RESTORE_FAILED"),
                    scope=probe.scope or self.execution.scope,
                    after_forget=self._probe_after_forget(probe),
                )
            )
            return False
        return True

    def _outcome(
        self,
        probe: MemoryProbe,
        execution: ProbeExecution,
        *,
        scope: MemoryScope,
        after_forget: bool,
    ) -> MemoryProbeOutcome:
        asserted: list[MemoryFact] | None = None
        if execution.answer is not None:
            try:
                asserted = self.observer.parse_answer_facts(execution.answer)
            except Exception as exc:
                self.record("observer_error", "parse_answer_facts",
                            detail=_detail(exc), probe_id=probe.id)
                asserted = None
        return MemoryProbeOutcome(
            execution=execution,
            question=probe.question,
            asked_at=probe.at,
            scope=scope,
            asserted_facts=list(asserted) if asserted else [],
            answer_facts_parsed=asserted is not None,
            injected_facts=[],
            after_forget=after_forget,
            scope_observable=True,
        )

    # ---- 结果组装 -----------------------------------------------------------

    def _result(self) -> MemoryScenarioRun:
        trial = build_trial_execution(
            self.execution,
            probes=self.probe_outcomes,
            extracted_facts=self.extracted_facts or [],
            source_supported_facts=self.source_supported_facts,
            state_observations=self.state_observations,
            forgotten_fact_keys=self.forgotten_fact_keys,
            stage_usage=self._stage_usage(),
            trial_index=self.trial_index,
        )
        scored = sum(1 for item in self.probe_outcomes if item.execution.answer)
        return MemoryScenarioRun(
            trial=trial,
            failure_ledger=self.ledger,
            coverage=MemoryCoverage(scored=scored, planned=len(self.probe_outcomes)),
        )

    def _stage_usage(self) -> dict[str, MemoryUsage]:
        usages = [usage for usage in self.probe_usages if usage is not None]
        if not usages:
            return {}
        sources = {usage.source for usage in usages}
        source = sources.pop() if len(sources) == 1 else "mixed"

        def _sum(field: str) -> int | None:
            values = [getattr(usage, field) for usage in usages]
            if any(value is None for value in values):
                return None
            return sum(value for value in values if value is not None)

        return {
            "reader": MemoryUsage(
                input_tokens=_sum("input_tokens"),
                output_tokens=_sum("output_tokens"),
                total_tokens=_sum("total_tokens"),
                physical_requests=sum(usage.physical_requests for usage in usages),
                source=source,  # type: ignore[arg-type]
            )
        }


async def run_memory_scenario(
    execution: MemoryExecutionInput,
    adapter: MemoryAdapter,
    *,
    observer: MemoryExecutionObserver | None = None,
    trial_index: int = 0,
) -> MemoryScenarioRun:
    """执行一个记忆专项场景并产出评分输入 + 失败账目。

    adapter 只依赖 :class:`MemoryAdapter` 协议签名（duck-typing）；observer 缺省为
    :class:`NullExecutionObserver`（全部材料不可得）。取消（``CancelledError``）在
    账目留痕后原样上抛，由调用方决定试验终态。
    """
    machine = _ScenarioMachine(
        execution,
        adapter,
        observer or NullExecutionObserver(),
        trial_index=trial_index,
    )
    return await machine.run()
