"""专项 Worker 进程装配与真实子进程闭环（plan §6 Task 4 / WU-04b）。

职责
----

本模块是 04a 状态机（:mod:`nanobot.evaluation.memory.runner`）与真实产品链路之间的
**唯一接线层**：Worker 子进程拿到 ``AgentWorkerBoot.memory_execution`` 后，在这里完成

1. **真实 observer**（:class:`ZhixuWorkerObserver`）：对接 trial 记忆库的真实行、
   派生文件与会话原文——``export_extracted_facts`` / ``export_source_supported_facts``
   来自 ``adapter.services.database`` 的真实记忆行，``export_state_observations``
   来自存储面 + 派生 ``MEMORY.md``，``seed_reference_state`` 用真实存储原语
   （``repository.add_memory``）写入检索轨参考状态。
2. **注入映射缝**：04a 的 ``injected_facts`` 恒为空（协议只带 ``injected_evidence``
   的 memory id）——本模块维护 id→事实的累积索引，run 结束后把每个 probe 的
   ``injected_facts`` 补齐再经 ``build_trial_execution`` 重组 trial。
3. **事件采集**（:class:`RecordingAdapter`）：包装真实 adapter，把每次真实产品调用
   记成契约 §4 闭集的 ``memory.event``（session_closed / extraction_* /
   injected / probe_answered / state_snapshot / deletion_checked …），
   由 Worker 以 ``memory.event`` 帧统一发出；不从预期 gold 伪造事件。
4. **trial 装配**（:func:`run_memory_worker_execution`）：trial 根在 attempt 目录下的
   ``mtrial/``，LLM 运行时取 ``loop.llm_runtime()``（真实产品 runtime，duck-typing
   ``provider.chat_with_retry``）。

边界：gold 永不出现在执行端——本模块只消费 ``MemoryExecutionInput`` 形状的载荷；
类别 ``tags`` 不在这里补（父进程评分端负责）；语义等价判定不在子进程做
（评分重用父进程 rules/Judge）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from nanobot.evaluation.memory.models import (
    ForgetRequest,
    ForgetResult,
    MemoryCandidate,
    MemoryExecutionInput,
    MemoryFact,
    MemoryProbe,
    MemoryReferenceState,
    MemoryScope,
    MemoryStateSnapshot,
    ProbeExecution,
    RecallResult,
    SettleResult,
)
from nanobot.evaluation.memory.runner import (
    MemoryScenarioRun,
    build_trial_execution,
    run_memory_scenario,
)
from nanobot.evaluation.memory.zhixu_adapter import ZhixuMemoryAdapter

if TYPE_CHECKING:  # pragma: no cover - 仅类型
    from nanobot.evaluation.runtime_factory import AttemptPaths, AttemptRuntime

__all__ = [
    "MEMORY_EVENT_KINDS",
    "MEMORY_TRIAL_DIRNAME",
    "MemoryWorkerOutcome",
    "MemoryWorkerSetupError",
    "RecordingAdapter",
    "ZhixuWorkerObserver",
    "memory_trial_paths",
    "run_memory_worker_execution",
]

#: trial 评估根在 attempt 目录下的目录名（刻意短：Windows 路径预算紧张）。
MEMORY_TRIAL_DIRNAME = "mtrial"

#: 契约 §4 的 ``memory.event`` kind 闭集。
MEMORY_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "session_closed",
        "extraction_completed",
        "extraction_skipped",
        "extraction_failed",
        "memory_changed",
        "index_ready",
        "recall_candidates",
        "reranked",
        "injected",
        "probe_answered",
        "state_snapshot",
        "deletion_checked",
    }
)

#: memory.event payload 里正文（回答/注入块）的截断上限；正文超限进受控 artifact
#: 是 WU-09 的映射工作，这里先保证帧远小于 IPC 3 MiB 上限。
_EVENT_BODY_LIMIT = 2000


class MemoryWorkerSetupError(ValueError):
    """专项装配失败：setup 级错误，不降级普通聊天。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


# =========================================================================== 路径


def memory_trial_paths(paths: "AttemptPaths", scope: Mapping[str, Any]) -> Path:
    """trial 评估根：``<attempt 根>/mtrial``。adapter 的工作区在其下按 scope 展开。"""
    return Path(paths.root) / MEMORY_TRIAL_DIRNAME


# =========================================================================== 真实 observer


def _longest_common_substring(left: str, right: str) -> str:
    """两个字符串的最长公共子串（确定性规则解析用；短串回退安全）。"""
    if not left or not right:
        return ""
    best = ""
    for start in range(len(left)):
        for end in range(start + len(best) + 1, len(left) + 1):
            piece = left[start:end]
            if piece in right:
                best = piece
    return best


class ZhixuWorkerObserver:
    """契约 observer 的真实实现：数据全部来自 trial 记忆库与派生文件。

    - ``parse_answer_facts``：确定性规则——对每个已知记忆行，取行正文与回答的
      最长公共子串（≥2 字符）作为断言值。这是「执行端按确定性规则解析」，不是
      语义 Judge；等价性核验仍由父进程评分端完成。
    - ``export_extracted_facts`` / ``export_source_supported_facts``：真实记忆行
      （抽取管线只消费源会话，行即源会话派生）。逐条支持性判定留给评分端。
    - ``export_state_observations``：``store`` 面按行存在性；``derived`` 面按
      ``MEMORY.md`` 是否包含行正文。
    - ``seed_reference_state``：检索轨播种——真实存储原语写入参考事实。
    - ``injected_fact_index``：memory id → 事实的累积映射（含已删除行），
      补 ``injected_facts`` 用。
    """

    def __init__(self, adapter: ZhixuMemoryAdapter) -> None:
        self._adapter = adapter
        self._scope: MemoryScope = adapter._scope  # noqa: SLF001 - 同包装配层
        #: memory id → 事实的累积索引：删除后仍可回填删除前注入的证据。
        self._fact_index: dict[str, MemoryFact] = {}

    # ---- 内部 ---------------------------------------------------------

    def _rows(self) -> list[Any]:
        return list(self._adapter.list_memory_rows())

    def _row_fact(self, row: Any, *, value: str | None = None) -> MemoryFact:
        predicate = str(getattr(row, "predicate", "") or "")
        subject = str(getattr(row, "subject", "") or "")
        return MemoryFact(
            key=predicate or subject or "memory",
            value=value if value is not None else str(getattr(row, "content", "")),
            subject=subject or None,
            scope=self._scope,
        )

    def _refresh_index(self, rows: Sequence[Any]) -> None:
        for row in rows:
            memory_id = str(getattr(row, "id", "") or "")
            if memory_id:
                self._fact_index[memory_id] = self._row_fact(row)

    # ---- observer 协议 --------------------------------------------------

    def parse_answer_facts(self, answer: str) -> list[MemoryFact] | None:
        rows = self._rows()
        self._refresh_index(rows)
        facts: list[MemoryFact] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            content = str(getattr(row, "content", "") or "")
            overlap = _longest_common_substring(content, answer)
            if len(overlap) < 2:
                continue
            fact = self._row_fact(row, value=overlap)
            marker = (fact.key, fact.value)
            if marker not in seen:
                seen.add(marker)
                facts.append(fact)
        return facts

    def export_extracted_facts(self) -> list[MemoryFact] | None:
        rows = self._rows()
        self._refresh_index(rows)
        return [self._row_fact(row) for row in rows]

    def export_source_supported_facts(self) -> list[MemoryFact] | None:
        rows = self._rows()
        self._refresh_index(rows)
        return [self._row_fact(row) for row in rows]

    def export_state_observations(self) -> list[Any] | None:
        from nanobot.evaluation.memory.grading import MemoryStateObservation

        rows = self._rows()
        self._refresh_index(rows)
        derived = self._adapter.workspace_path / "memory" / "MEMORY.md"
        derived_text = derived.read_text(encoding="utf-8") if derived.is_file() else ""
        observations = []
        for row in rows:
            key = str(getattr(row, "predicate", "") or getattr(row, "subject", "") or "memory")
            observations.append(
                MemoryStateObservation(fact_key=key, surface="store", present=True)
            )
            observations.append(
                MemoryStateObservation(
                    fact_key=key,
                    surface="derived",
                    present=bool(derived_text) and str(row.content) in derived_text,
                )
            )
        return observations

    def seed_reference_state(self, state: MemoryReferenceState) -> bool:
        from datetime import datetime, timezone

        from nanobot.memory.models import Memory as MemoryRow
        from nanobot.memory.repository import add_memory

        clock = getattr(self._adapter, "_clock", None)
        now = (
            clock.wall_now().isoformat()
            if clock is not None
            else datetime.now(timezone.utc).isoformat()
        )
        try:
            # ``MemoryDatabase.connect()`` 不接受 ``immediate``；写事务的串行化由
            # SQLite 自己的写锁保证。``Memory`` 行的 ``created_at`` 是必填字段，
            # 缺了同样会被下面的 except 吞掉——结果是「检索轨播种永远失败」，
            # 属于静默未生效配置而不是能力缺失。
            with self._adapter.services.database.connect() as conn:
                for fact in state.facts:
                    row = MemoryRow(
                        id=f"eval-seed-{uuid.uuid4().hex[:12]}",
                        content=fact.value,
                        created_at=now,
                        subject=fact.subject or "",
                        predicate=fact.key,
                        user_id=self._scope.subject_id,
                        workspace_id=self._adapter.services.workspace_id,
                        source="evaluation_seed",
                    )
                    add_memory(conn, row)
        except Exception:  # noqa: BLE001 - runner 会把播种失败记成账目
            return False
        return True

    # ---- 评分端接缝 ------------------------------------------------------

    def injected_fact_index(self) -> dict[str, MemoryFact]:
        """memory id → 事实的当前累积索引（04a 注入映射缝的唯一回填点）。"""
        return dict(self._fact_index)


# =========================================================================== 事件采集


@dataclass
class RecordingAdapter:
    """真实 adapter 的包装：每次真实产品调用记一条契约闭集事件。

    只转发不改语义；runner 的 duck-typing 协议由内层 adapter 满足。
    """

    inner: ZhixuMemoryAdapter
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def capabilities(self) -> Any:
        return self.inner.capabilities

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def _record(self, kind: str, **payload: Any) -> None:
        if kind not in MEMORY_EVENT_KINDS:
            raise ValueError(f"MEMORY_EVENT_KIND_UNKNOWN:{kind}")
        body = payload.get("body")
        if isinstance(body, str) and len(body) > _EVENT_BODY_LIMIT:
            payload["body"] = body[:_EVENT_BODY_LIMIT]
            payload["body_truncated"] = True
        self.events.append({"kind": kind, **payload})

    async def reset(self, candidate: MemoryCandidate) -> None:
        await self.inner.reset(candidate)

    async def ingest_session(self, event: Any) -> None:
        await self.inner.ingest_session(event)
        self._record("session_closed", session_id=event.id)

    async def settle(self, session_id: str, timeout_s: float) -> SettleResult:
        result = await self.inner.settle(session_id, timeout_s)
        kind = {
            "completed": "extraction_completed",
            "skipped_by_policy": "extraction_skipped",
            "failed": "extraction_failed",
            "timeout": "extraction_failed",
        }[result.status]
        self._record(
            kind,
            session_id=session_id,
            state_version=result.state_version,
            detail=";".join(result.failed_stages + result.reason_codes) or result.status,
        )
        if result.status == "completed" and result.index_version:
            self._record(
                "index_ready", session_id=session_id, detail=str(result.index_version)
            )
        return result

    async def snapshot(self, checkpoint_id: str) -> MemoryStateSnapshot:
        snapshot = await self.inner.snapshot(checkpoint_id)
        self._record(
            "state_snapshot",
            checkpoint_id=checkpoint_id,
            state_version=str(snapshot.version),
        )
        return snapshot

    async def restore(self, state: MemoryStateSnapshot) -> None:
        await self.inner.restore(state)

    async def restart(self, state: MemoryStateSnapshot) -> None:
        await self.inner.restart(state)

    async def recall(self, probe: MemoryProbe) -> RecallResult:
        result = await self.inner.recall(probe)
        self._record(
            "recall_candidates",
            probe_id=probe.id,
            state_version=result.state_version,
            detail=f"CANDIDATES={len(result.items)}",
        )
        return result

    async def answer(self, probe: MemoryProbe) -> ProbeExecution:
        execution = await self.inner.answer(probe)
        # runner 走 adapter.answer（检索在 answer 内部真实发生），召回候选数在这里
        # 如实留痕——不从预期 gold 伪造候选。
        self._record(
            "recall_candidates",
            probe_id=probe.id,
            detail=f"CANDIDATES={len(execution.recalled)}",
        )
        self._record(
            "injected",
            probe_id=probe.id,
            origin_refs=[str(item) for item in execution.injected_evidence],
        )
        self._record("probe_answered", probe_id=probe.id, body=execution.answer or "")
        return execution

    async def forget(self, request: ForgetRequest) -> ForgetResult:
        result = await self.inner.forget(request)
        self._record(
            "deletion_checked",
            detail=";".join(result.deleted_ids) or request.fact_key or "",
        )
        return result

    async def rebuild(self) -> SettleResult:
        result = await self.inner.rebuild()
        self._record("memory_changed", detail=result.status)
        return result

    async def close(self) -> None:
        await self.inner.close()


# =========================================================================== 装配与执行


@dataclass
class MemoryWorkerOutcome:
    """专项执行产物：trial（注入已回填）+ 事件 + 失败账目经 ``run`` 透出。"""

    run: MemoryScenarioRun
    events: list[dict[str, Any]] = field(default_factory=list)


def _build_trial_adapter(
    runtime: "AttemptRuntime",
    execution: MemoryExecutionInput,
) -> ZhixuMemoryAdapter:
    """在 attempt 目录下的 trial 根里装配真实 adapter。

    LLM 运行时是真实产品的 ``loop.llm_runtime()``（duck-typing
    ``provider.chat_with_retry``）；评测时钟不注入——真实执行超时走事件循环。
    """
    paths = runtime.paths
    trial_root = memory_trial_paths(paths, execution.scope.model_dump(mode="json"))
    trial_root.mkdir(parents=True, exist_ok=True)
    return ZhixuMemoryAdapter(
        trial_root=trial_root,
        scope=execution.scope,
        runtime=runtime.loop.llm_runtime(),
    )


def _fill_injected_facts(
    run: MemoryScenarioRun,
    execution: MemoryExecutionInput,
    index: Mapping[str, MemoryFact],
) -> MemoryScenarioRun:
    """把 ``injected_evidence`` 的 id 回填成事实，再经 build_trial_execution 重组。"""
    outcomes = []
    for outcome in run.trial.probes:
        injected = [
            index[str(item)]
            for item in outcome.execution.injected_evidence
            if str(item) in index
        ]
        outcomes.append(outcome.model_copy(update={"injected_facts": injected}))
    trial = build_trial_execution(
        execution,
        probes=outcomes,
        extracted_facts=run.trial.extracted_facts,
        source_supported_facts=run.trial.source_supported_facts,
        state_observations=run.trial.state_observations,
        forgotten_fact_keys=run.trial.forgotten_fact_keys,
        stage_usage=run.trial.stage_usage,
        trial_index=run.trial.trial_index,
    )
    return MemoryScenarioRun(
        trial=trial, failure_ledger=run.failure_ledger, coverage=run.coverage
    )


async def run_memory_worker_execution(
    runtime: "AttemptRuntime",
    execution_payload: Mapping[str, Any],
    *,
    emit_event: Callable[..., Any] | None = None,
) -> MemoryWorkerOutcome:
    """执行一个专项场景：boot 载荷 → 真实 adapter/observer → 状态机 → 注入回填。

    ``emit_event``（Worker 的 ``recorder.emit``）非空时，采集到的 ``memory.event``
    在执行过程中即时发出（seq 与落盘共用同一来源）；异常路径由 Worker 兜底。
    """
    try:
        execution = MemoryExecutionInput.model_validate(dict(execution_payload))
    except Exception as exc:
        raise MemoryWorkerSetupError(
            "MEMORY_EXECUTION_INVALID", str(exc)[:500]
        ) from exc

    adapter = _build_trial_adapter(runtime, execution)
    # 契约 §1/§2：进程内先应用该 trial 的**生效候选**。缺这一步时
    # no_persistent_memory 仍会读写持久记忆、候选记忆设置被静默忽略——两种都是
    # 「候选没生效」，而报告会把它们当成记忆能力差异。
    if execution.candidates:
        await adapter.reset(execution.candidates[-1])
    observer = ZhixuWorkerObserver(adapter)
    recording = RecordingAdapter(inner=adapter)

    def _emit(kind: str, payload: Mapping[str, Any]) -> None:
        if emit_event is None:
            return
        emit_event(
            "memory.event",
            payload={
                "kind": kind,
                "scenario_id": execution.scenario_id,
                "scope": execution.scope.model_dump(mode="json"),
                **payload,
            },
        )

    run = await run_memory_scenario(execution, recording, observer=observer)
    # 发出采集到的事件（顺序即真实发生顺序；取消路径的残留事件由 Worker 兜底丢弃）。
    for event in recording.events:
        _emit(str(event.get("kind")), {k: v for k, v in event.items() if k != "kind"})
    outcome = MemoryWorkerOutcome(
        run=_fill_injected_facts(run, execution, observer.injected_fact_index()),
        events=list(recording.events),
    )
    return outcome
