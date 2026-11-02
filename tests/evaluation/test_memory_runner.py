"""WU-04a：run_memory_scenario 状态机测试（fake adapter，duck-typing 协议签名）。

不起真实子进程（那是 WU-04b 的 test_memory_worker.py）；这里验证：
全事件顺序处理、checkpoint 密封屏障、probe 副本隔离、三轨语义、失败账目、
execution_fields 不含 gold，以及 runner 产物能直接喂给 WU-05 ``grade_memory_trial``。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import pytest

from nanobot.evaluation.memory.grading import (
    MemoryStateObservation,
    default_grading_policy,
    grade_memory_trial,
)
from nanobot.evaluation.memory.models import (
    ForgetRequest,
    ForgetResult,
    MemoryCandidate,
    MemoryCoverage,
    MemoryExecutionInput,
    MemoryFact,
    MemoryProbe,
    MemoryRecallItem,
    MemoryReferenceState,
    MemoryScenario,
    MemorySessionEvent,
    MemoryStateSnapshot,
    MemoryUsage,
    ProbeExecution,
    RecallResult,
    SettleResult,
)
from nanobot.evaluation.memory.runner import (
    NullExecutionObserver,
    ScenarioLedgerEntry,
    build_trial_execution,
    execution_fields,
    run_memory_scenario,
)
from tests.evaluation.memory_fixtures import make_memory_scenario

# --------------------------------------------------------------------------- fixtures


class FakeAdapter:
    """只实现协议签名，不实现产品语义；记录调用序列供断言。"""

    def __init__(self, *, settle_status: str = "completed") -> None:
        self.capabilities: Any = None
        self.calls: list[tuple[Any, ...]] = []
        self.settle_status = settle_status
        self.closed = False
        self.fail_answer = False
        self.cancel_answer = False
        self.forget_status = "completed"

    async def reset(self, candidate: MemoryCandidate) -> None:
        self.calls.append(("reset", candidate.id))

    async def ingest_session(self, event: MemorySessionEvent) -> None:
        self.calls.append(("ingest", event.id))

    async def settle(self, session_id: str, timeout_s: float) -> SettleResult:
        self.calls.append(("settle", session_id, timeout_s))
        return SettleResult(status=self.settle_status, state_version="sv1")

    async def snapshot(self, checkpoint_id: str) -> MemoryStateSnapshot:
        self.calls.append(("snapshot", checkpoint_id))
        return MemoryStateSnapshot(
            version=1, checkpoint_id=checkpoint_id, state_digest="d" * 64
        )

    async def restore(self, state: MemoryStateSnapshot) -> None:
        self.calls.append(("restore", state.checkpoint_id))

    async def restart(self, state: MemoryStateSnapshot) -> None:
        self.calls.append(("restart", state.checkpoint_id))

    async def recall(self, probe: MemoryProbe) -> RecallResult:
        self.calls.append(("recall", probe.id))
        return RecallResult(probe_id=probe.id, items=[])

    async def answer(self, probe: MemoryProbe) -> ProbeExecution:
        if self.cancel_answer:
            raise asyncio.CancelledError
        if self.fail_answer:
            raise RuntimeError("model service unavailable")
        self.calls.append(("answer", probe.id))
        return ProbeExecution(
            probe_id=probe.id,
            answer="我住在青岚城。",
            recalled=[MemoryRecallItem(rank=1, key="city", value="青岚城")],
            usage=MemoryUsage(
                input_tokens=10, output_tokens=5, total_tokens=15,
                physical_requests=1, source="reported",
            ),
            coverage=MemoryCoverage(scored=1, planned=1),
        )

    async def forget(self, request: ForgetRequest) -> ForgetResult:
        self.calls.append(("forget", request.mode, request.fact_key))
        return ForgetResult(status=self.forget_status, deleted_ids=[request.fact_key or ""])

    async def rebuild(self) -> SettleResult:
        self.calls.append(("rebuild",))
        return SettleResult(status="completed")

    async def close(self) -> None:
        self.closed = True
        self.calls.append(("close",))


class FakeObserver:
    """确定性 observer：能解析青岚城回答、导出固定评分材料。"""

    def __init__(
        self,
        *,
        parsed: bool = True,
        seed_ok: bool = True,
        extracted: list[MemoryFact] | None = None,
    ) -> None:
        self.parsed = parsed
        self.seed_ok = seed_ok
        self.seeded: MemoryReferenceState | None = None
        self._extracted = extracted

    def parse_answer_facts(self, answer: str) -> list[MemoryFact] | None:
        if not self.parsed:
            return None
        if "青岚城" in answer:
            return [MemoryFact(key="city", value="青岚城")]
        return []

    def export_extracted_facts(self) -> list[MemoryFact] | None:
        return list(self._extracted) if self._extracted is not None else None

    def export_source_supported_facts(self) -> list[MemoryFact] | None:
        return [MemoryFact(key="city", value="青岚城")]

    def export_state_observations(self) -> list[MemoryStateObservation] | None:
        return [MemoryStateObservation(fact_key="city", surface="store", present=True)]

    def seed_reference_state(self, state: MemoryReferenceState) -> bool:
        self.seeded = state
        return self.seed_ok


def _execution(
    scenario_payload: dict[str, Any], track: str
) -> MemoryExecutionInput:
    return MemoryExecutionInput.model_validate(
        {**execution_fields(scenario_payload), "track": track}
    )


def _scenario(**overrides: Any) -> dict[str, Any]:
    return make_memory_scenario(**overrides)


def _kinds(ledger: Sequence[ScenarioLedgerEntry]) -> list[str]:
    return [entry.kind for entry in ledger]


def _steps(adapter: FakeAdapter) -> list[str]:
    return [call[0] for call in adapter.calls]


# --------------------------------------------------------------------------- 载荷分离


def test_execution_payload_excludes_gold() -> None:
    """plan §6 Step 2 的验收例：execution 载荷不含 gold。"""
    payload = execution_fields(_scenario())
    assert "gold" not in payload
    assert "events" in payload and "probes" in payload
    assert set(payload) == {
        "schema_version", "scenario_id", "revision", "scope", "events", "probes",
    }


def test_execution_fields_keeps_reference_state_when_present() -> None:
    scenario = _scenario(
        reference_state={
            "version": "ref/v1",
            "facts": [{"key": "city", "value": "青岚城"}],
        }
    )
    payload = execution_fields(scenario)
    assert payload["reference_state"]["version"] == "ref/v1"


# --------------------------------------------------------------------------- 状态机：全事件


@pytest.mark.asyncio()
async def test_end_to_end_processes_all_event_types_in_order() -> None:
    """session→settle→checkpoint→probe(clone)→probe(live)→forget→probe→rebuild→restart。"""
    scenario = _scenario(
        events=[
            {
                "type": "session",
                "id": "s1",
                "at": "2026-11-01T09:00:00+08:00",
                "messages": [
                    {"id": "m1", "role": "user", "content": "我的城市是青岚城。"}
                ],
            },
            {"type": "settle", "after_session": "s1", "timeout_s": 30},
            {"type": "checkpoint", "id": "cp1"},
            {"type": "forget", "mode": "product_request", "fact_key": "city"},
            {"type": "rebuild"},
            {"type": "restart", "checkpoint_id": "cp1"},
        ],
        probes=[
            {"id": "q1", "question": "住在哪？", "at": "2026-11-02T09:00:00+08:00",
             "checkpoint_id": "cp1"},
            {"id": "q2", "question": "还知道吗？", "at": "2026-11-02T10:00:00+08:00"},
        ],
    )
    adapter = FakeAdapter()
    run = await run_memory_scenario(_execution(scenario, "end_to_end"), adapter)

    # 事件顺序：ingest → settle(带 timeout) → snapshot(checkpoint) → forget → rebuild
    assert adapter.calls[:4] == [
        ("ingest", "s1"), ("settle", "s1", 30), ("snapshot", "cp1"),
        ("forget", "product_request", "city"),
    ]
    assert _steps(adapter)[4:6] == ["rebuild", "restart"]
    # probe 阶段（事件之后的 5 个调用）：q1 前先拍 base 再恢复 cp1；q2 恢复 base 回实时状态。
    probe_calls = adapter.calls[6:-1]
    assert probe_calls == [
        ("snapshot", "probe-base"), ("restore", "cp1"), ("answer", "q1"),
        ("restore", "probe-base"), ("answer", "q2"),
    ]
    assert adapter.closed

    # 账目：q1 在 cp1（forget 前捕获的快照）上作答 → after_forget=False；q2 → True。
    q1, q2 = run.trial.probes
    assert q1.probe_id == "q1" and q1.after_forget is False
    assert q2.after_forget is True
    assert run.trial.forgotten_fact_keys == ["city"]
    assert run.trial.extracted_facts == []
    # 每个 probe 独立产出 answer/usage/coverage，绝不只保留最后回答。
    assert all(outcome.execution.answer == "我住在青岚城。" for outcome in run.trial.probes)
    assert all(outcome.execution.usage.total_tokens == 15 for outcome in run.trial.probes)
    assert run.trial.stage_usage["reader"].total_tokens == 30
    assert run.coverage == MemoryCoverage(scored=2, planned=2)
    # NullObserver 下抽取材料不可得，账目如实留痕（同一账目只记一次）。
    assert _kinds(run.failure_ledger) == ["extracted_facts_unavailable"]


@pytest.mark.asyncio()
async def test_probe_referencing_unknown_checkpoint_is_ledgered_not_dropped() -> None:
    scenario = _scenario(
        probes=[
            {"id": "q1", "question": "住在哪？", "at": "2026-11-02T09:00:00+08:00",
             "checkpoint_id": "cp-ghost"},
        ]
    )
    adapter = FakeAdapter()
    run = await run_memory_scenario(_execution(scenario, "end_to_end"), adapter)
    assert _kinds(run.failure_ledger) == [
        "extracted_facts_unavailable", "probe_checkpoint_unknown",
    ]
    outcome = run.trial.probes[0]
    assert outcome.execution.answer is None
    assert outcome.execution.coverage.reasons == ["PROBE_CHECKPOINT_UNAVAILABLE"]
    assert outcome.answer_facts_parsed is False
    # probe 没被剔除：planned=1，scored=0。
    assert run.coverage == MemoryCoverage(scored=0, planned=1)


@pytest.mark.asyncio()
async def test_settle_timeout_blocks_checkpoint_but_run_continues() -> None:
    scenario = _scenario(
        events=[
            {
                "type": "session", "id": "s1", "at": "2026-11-01T09:00:00+08:00",
                "messages": [{"id": "m1", "role": "user", "content": "记得青岚城。"}],
            },
            {"type": "settle", "after_session": "s1"},
            {"type": "checkpoint", "id": "cp1"},
        ],
    )
    adapter = FakeAdapter(settle_status="timeout")
    run = await run_memory_scenario(_execution(scenario, "end_to_end"), adapter)
    assert "settle_timeout" in _kinds(run.failure_ledger)
    assert "checkpoint_not_sealed" in _kinds(run.failure_ledger)
    # 不剔除：probe 照常应答，只是状态材料未密封。
    assert run.coverage == MemoryCoverage(scored=1, planned=1)


@pytest.mark.asyncio()
async def test_model_service_error_is_recorded_and_probe_stays() -> None:
    adapter = FakeAdapter()
    adapter.fail_answer = True
    run = await run_memory_scenario(_execution(_scenario(), "end_to_end"), adapter)
    assert _kinds(run.failure_ledger) == ["extracted_facts_unavailable", "probe_failed"]
    assert "model service unavailable" in run.failure_ledger[1].detail
    outcome = run.trial.probes[0]
    assert outcome.execution.answer is None
    assert outcome.answer_facts_parsed is False
    assert run.coverage == MemoryCoverage(scored=0, planned=1)


@pytest.mark.asyncio()
async def test_forget_unsupported_is_ledgered_and_not_marked_forgotten() -> None:
    scenario = _scenario(
        events=[
            {
                "type": "session", "id": "s1", "at": "2026-11-01T09:00:00+08:00",
                "messages": [{"id": "m1", "role": "user", "content": "青岚城。"}],
            },
            {"type": "settle", "after_session": "s1"},
            {"type": "forget", "mode": "product_request", "fact_key": "city"},
        ],
    )
    adapter = FakeAdapter()
    adapter.forget_status = "unsupported"
    run = await run_memory_scenario(_execution(scenario, "end_to_end"), adapter)
    assert "forget_unsupported" in _kinds(run.failure_ledger)
    assert run.trial.forgotten_fact_keys == []
    assert run.trial.probes[0].after_forget is False


@pytest.mark.asyncio()
async def test_cancellation_is_recorded_then_reraised_and_closes_adapter() -> None:
    adapter = FakeAdapter()
    adapter.cancel_answer = True
    with pytest.raises(asyncio.CancelledError):
        await run_memory_scenario(_execution(_scenario(), "end_to_end"), adapter)
    assert adapter.closed


# --------------------------------------------------------------------------- 三轨语义


@pytest.mark.asyncio()
async def test_extraction_track_exports_materials_and_skips_probes() -> None:
    scenario = _scenario(
        gold={
            "annotation_scope": "closed_world",
            "extraction_facts": [{"key": "city", "value": "青岚城"}],
        },
    )
    adapter = FakeAdapter()
    observer = FakeObserver(extracted=[MemoryFact(key="city", value="青岚城")])
    run = await run_memory_scenario(
        _execution(scenario, "extraction"), adapter, observer=observer
    )
    # 只导出 state 评分材料，不应答 probe。
    assert not [call for call in adapter.calls if call[0] == "answer"]
    assert run.trial.probes == []
    assert _kinds(run.failure_ledger) == ["probes_skipped_by_track"]
    assert run.trial.extracted_facts == [MemoryFact(key="city", value="青岚城")]
    assert run.trial.source_supported_facts is not None
    assert run.trial.state_observations is not None


@pytest.mark.asyncio()
async def test_extraction_track_freezes_extracted_facts_before_forget() -> None:
    scenario = _scenario(
        events=[
            {
                "type": "session", "id": "s1", "at": "2026-11-01T09:00:00+08:00",
                "messages": [{"id": "m1", "role": "user", "content": "青岚城。"}],
            },
            {"type": "settle", "after_session": "s1"},
            {"type": "forget", "mode": "admin_delete", "fact_key": "city"},
        ],
    )
    adapter = FakeAdapter()
    observer = FakeObserver(extracted=[MemoryFact(key="city", value="青岚城")])
    run = await run_memory_scenario(
        _execution(scenario, "extraction"), adapter, observer=observer
    )
    assert run.trial.extracted_facts == [MemoryFact(key="city", value="青岚城")]
    assert run.trial.forgotten_fact_keys == ["city"]


@pytest.mark.asyncio()
async def test_retrieval_track_seeds_reference_state_and_skips_history() -> None:
    reference = {
        "version": "ref/v1",
        "facts": [{"key": "city", "value": "青岚城"}],
    }
    scenario = _scenario(reference_state=reference)
    adapter = FakeAdapter()
    observer = FakeObserver()
    run = await run_memory_scenario(
        _execution(scenario, "retrieval"), adapter, observer=observer
    )
    # 禁用历史抽取：不 ingest、不 settle。
    assert not [call for call in adapter.calls if call[0] in ("ingest", "settle")]
    assert observer.seeded is not None and observer.seeded.version == "ref/v1"
    assert run.coverage == MemoryCoverage(scored=1, planned=1)
    # 跳过历史也有账目：不静默；抽取材料不可得同样留痕。
    assert _kinds(run.failure_ledger) == [
        "session_skipped_by_track", "settle_skipped_by_track",
        "extracted_facts_unavailable",
    ]


@pytest.mark.asyncio()
async def test_retrieval_track_without_seeder_is_ledgered() -> None:
    reference = {"version": "ref/v1", "facts": [{"key": "city", "value": "青岚城"}]}
    scenario = _scenario(reference_state=reference)
    adapter = FakeAdapter()
    run = await run_memory_scenario(
        _execution(scenario, "retrieval"), adapter, observer=NullExecutionObserver()
    )
    assert "reference_state_unseeded" in _kinds(run.failure_ledger)
    # probe 仍执行，材料缺失如实留痕，不伪装成功。
    assert run.coverage == MemoryCoverage(scored=1, planned=1)


# --------------------------------------------------------------------------- 失败账目：observer


@pytest.mark.asyncio()
async def test_observer_failure_is_ledgered_and_materials_stay_none() -> None:
    class BrokenObserver:
        def parse_answer_facts(self, answer: str) -> list[MemoryFact] | None:
            raise RuntimeError("observer broken")

        def export_extracted_facts(self) -> list[MemoryFact] | None:
            raise RuntimeError("observer broken")

        def export_source_supported_facts(self) -> list[MemoryFact] | None:
            raise RuntimeError("observer broken")

        def export_state_observations(self) -> list[MemoryStateObservation] | None:
            raise RuntimeError("observer broken")

        def seed_reference_state(self, state: MemoryReferenceState) -> bool:
            return False

    adapter = FakeAdapter()
    run = await run_memory_scenario(
        _execution(_scenario(), "end_to_end"), adapter, observer=BrokenObserver()
    )
    kinds = _kinds(run.failure_ledger)
    assert kinds.count("observer_error") == 4
    assert run.trial.source_supported_facts is None
    assert run.trial.state_observations is None
    assert run.trial.probes[0].answer_facts_parsed is False


@pytest.mark.asyncio()
async def test_unparsed_answer_yields_not_assessable_in_grading() -> None:
    adapter = FakeAdapter()
    run = await run_memory_scenario(
        _execution(_scenario(), "end_to_end"), adapter, observer=NullExecutionObserver()
    )
    assert run.trial.probes[0].answer_facts_parsed is False
    gold = MemoryScenario.model_validate(_scenario()).gold
    grade = grade_memory_trial(run.trial, gold, default_grading_policy())
    probe_grade = grade.probe_results[0]
    assert probe_grade.qa_correct is None
    assert probe_grade.qa_status == "not_assessable"
    assert "ANSWER_FACTS_UNPARSED" in probe_grade.reasons


# --------------------------------------------------------------------------- 与 WU-05 对接


@pytest.mark.asyncio()
async def test_runner_output_feeds_grade_memory_trial_end_to_end() -> None:
    """runner 产物 + 场景 gold 直接进 WU-05：qa_correct=1 的最小闭环。"""
    adapter = FakeAdapter()
    run = await run_memory_scenario(
        _execution(_scenario(), "end_to_end"), adapter, observer=FakeObserver()
    )
    scenario = MemoryScenario.model_validate(_scenario())
    grade = grade_memory_trial(run.trial, scenario.gold, default_grading_policy())
    assert grade.probe_results[0].qa_correct is True
    assert grade.scenario_id == "fact-001"


@pytest.mark.asyncio()
async def test_build_trial_execution_defaults_match_grading_model() -> None:
    execution = _execution(_scenario(), "end_to_end")
    trial = build_trial_execution(
        execution,
        probes=[],
        extracted_facts=[],
        source_supported_facts=None,
        state_observations=None,
        forgotten_fact_keys=[],
    )
    assert trial.scenario_id == "fact-001"
    assert trial.scenario_revision == 1
    assert trial.track == "end_to_end"
    assert trial.source_supported_facts is None
    assert trial.state_observations is None
    assert trial.trial_index == 0


# --------------------------------------------------------------------------- 顺序保持


@pytest.mark.asyncio()
async def test_multiple_probes_keep_individual_coverage_and_usage() -> None:
    """三个 probe 各自保留 coverage/usage/answer，而不是只剩最后一个回答。"""
    scenario = _scenario(
        probes=[
            {"id": "q1", "question": "住在哪？", "at": "2026-11-02T09:00:00+08:00"},
            {"id": "q2", "question": "重复问", "at": "2026-11-02T10:00:00+08:00"},
            {"id": "q3", "question": "再问一次", "at": "2026-11-02T11:00:00+08:00"},
        ]
    )
    adapter = FakeAdapter()
    run = await run_memory_scenario(_execution(scenario, "end_to_end"), adapter)
    assert [outcome.probe_id for outcome in run.trial.probes] == ["q1", "q2", "q3"]
    assert [call[0] for call in adapter.calls] == [
        "ingest", "settle", "answer", "answer", "answer", "close",
    ]
    assert run.coverage == MemoryCoverage(scored=3, planned=3)


# ---------------------------------------------------------------------------
