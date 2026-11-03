"""WU-04b：专项 Worker 闭环（真实子进程 × 受控 provider）+ 父进程评分接线。

子进程用例通过 ``python -m nanobot.evaluation.worker`` 真实拉起 Worker（Windows 走
Job Object 回收），受控 provider 经 profile bundle 的 ``script`` 下发：专项 case 的
``turns``/``script`` 恒为空（schema 禁止混写），probe 回答与抽取直连 provider，脚本
按调用次序确定性回放（semantic → episode → probe，全部真实产品链路）。覆盖：

- end_to_end：写历史 → settle → 新会话 query（注入来自真实检索，会话无旧对话）；
- gold 永不进入执行端（帧、Trace、磁盘文件里都没有 gold 哨兵）；
- 三轨分测（extraction / retrieval / end_to_end），skip 策略导致能力失败而非剔除；
- 模型服务错、settle timeout、取消、observer 损坏的失败账目保留（进程内验证）；
- ``seed_memory`` 对专项 case 绝不被调用；Windows 紧凑路径；
- 父进程评分端消费 ``memory_result``（补齐 tags 后喂 ``grade_memory_trial``）。
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from nanobot.config import loader
from nanobot.evaluation.controlled_provider import ControlledProvider, ScriptStep
from nanobot.evaluation.memory.grading import (
    MemoryProbeOutcome,
    MemoryStateObservation,
)
from nanobot.evaluation.memory.models import (
    MemoryCoverage,
    MemoryExecutionInput,
    MemoryFact,
    MemoryRecallItem,
    MemoryUsage,
    ProbeExecution,
)
from nanobot.evaluation.memory.runner import (
    MemoryScenarioRun,
    build_trial_execution,
    execution_fields,
)
from nanobot.evaluation.processes import WorkerProcess
from nanobot.evaluation.schemas import AgentWorkerBoot, CaseWrite
from nanobot.evaluation.worker import WORKER_CANCELLED, WorkerChannel, run_agent_worker
from tests.evaluation.conftest import make_suite_payload
from tests.evaluation.memory_fixtures import (
    make_memory_case_payload,
    make_memory_dataset_payload,
    make_memory_scenario,
)
from tests.evaluation.test_runtime_factory import profile_bundle

WORKER_TIMEOUT_S = 120.0

GOLD_SENTINEL = "GOLD-SENTINEL-3f7a9c"
CITY_FACT_CONTENT = "用户的虚构测试城市叫青岚城"

#: 抽取两路 LLM 调用按创建次序确定性消费脚本：semantic → episode → probe。
SEMANTIC_JSON = json.dumps(
    {
        "memories": [
            {
                "content": CITY_FACT_CONTENT,
                "evidence": "我的虚构测试城市叫青岚城",
                "type": "fact",
                "importance": 0.9,
                "subject": "用户",
                "predicate": "city",
                "tags": ["test"],
            }
        ],
        "experiences": [],
    },
    ensure_ascii=False,
)
EPISODE_JSON = json.dumps(
    {"summary": "用户告知居住城市。", "goal": "记录城市", "importance": 0.7},
    ensure_ascii=False,
)
PROBE_ANSWER = "我住在青岚城。"


def controlled_script(*, with_probes: bool = True) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = [
        {"json_text": SEMANTIC_JSON},
        {"json_text": EPISODE_JSON},
    ]
    if with_probes:
        steps.append({"text": PROBE_ANSWER})
    return steps


def bundle_with_script(script: list[dict[str, Any]] | None) -> dict[str, Any]:
    bundle = profile_bundle()
    if script is not None:
        bundle["script"] = script
    return bundle


def scenario_payload(**overrides: Any) -> dict[str, Any]:
    return make_memory_scenario(**overrides)


def memory_boot(
    root: Path,
    attempt_id: str,
    *,
    track: str = "end_to_end",
    scenario: dict[str, Any] | None = None,
    script: list[dict[str, Any]] | None = None,
) -> AgentWorkerBoot:
    scenario = scenario if scenario is not None else scenario_payload()
    if script is None:
        # 默认给完整三步脚本（semantic → episode → probe）；三轨各自按需覆写。
        script = controlled_script()
    return AgentWorkerBoot.model_validate(
        {
            "type": "boot",
            "role": "agent",
            "protocol_version": 1,
            "attempt_id": attempt_id,
            "runtime_root": str(root / "a1" / "runtime"),
            "workspace": str(root / "ws"),
            "profile_bundle": bundle_with_script(script),
            "case_snapshot": make_memory_case_payload(
                suite_id=str(uuid.uuid4()),
                dataset_revision_id=str(uuid.uuid4()),
                memory_track=track,
            ),
            "memory_execution": {**execution_fields(scenario), "track": track},
        }
    )


async def run_subprocess_worker(
    boot: AgentWorkerBoot, tmp_path: Path, *, timeout_s: float = WORKER_TIMEOUT_S
) -> tuple[dict[str, Any], list[Any]]:
    """拉起真实 Worker 子进程，收集 result 帧与全部 trace 帧。"""
    worker = WorkerProcess(
        attempt_id=str(boot.attempt_id),
        command=[
            sys.executable, "-m", "nanobot.evaluation.worker", str(boot.attempt_id),
        ],
        log_path=tmp_path / f"worker-{boot.attempt_id}.log",
    )
    await worker.start(boot.model_dump(mode="json"))
    try:
        errors = await worker.run_with_timeout(timeout_s=timeout_s)
    except BaseException:
        await worker.cancel()
        raise
    assert errors == [], f"worker reported errors: {errors}"
    result = worker.ipc.result
    assert result is not None, "worker produced no result frame"
    return result, list(worker.ipc.events)


def memory_events(events: list[Any]) -> list[dict[str, Any]]:
    return [dict(event.payload) for event in events if event.type == "memory.event"]


def scenario_run_of(result: dict[str, Any]) -> MemoryScenarioRun:
    payload = result.get("memory_result")
    assert isinstance(payload, dict) and payload, "memory_result missing from result"
    return MemoryScenarioRun.model_validate(payload)


# ================================================================== 真实子进程


async def test_subprocess_end_to_end_writes_history_settles_and_answers_fresh_session(
    tmp_path: Path,
) -> None:
    boot = memory_boot(tmp_path, str(uuid.uuid4()))
    result, events = await run_subprocess_worker(boot, tmp_path)

    assert result["execution_status"] == "succeeded"
    assert result["errors"] == []
    assert result["trace_complete"] is True
    run = scenario_run_of(result)
    assert run.failure_ledger == []
    assert run.coverage.scored == 1 and run.coverage.planned == 1

    # 新会话 query：回答来自真实 provider，注入块来自真实检索（不是旧对话回放）。
    probe = run.trial.probes[0]
    assert probe.execution.answer == PROBE_ANSWER
    assert probe.execution.injected_evidence, "probe must reference injected memory ids"
    assert probe.execution.recalled
    assert CITY_FACT_CONTENT in probe.execution.recalled[0].value
    # 评分材料来自真实记忆行（抽取管线只消费源会话）。
    assert [fact.value for fact in run.trial.extracted_facts] == [CITY_FACT_CONTENT]

    assert "memory.event" in [str(event.type) for event in events]
    memory_kinds = [str(item.get("kind")) for item in memory_events(events)]
    assert memory_kinds == [
        "session_closed",
        "extraction_completed",
        "recall_candidates",
        "injected",
        "probe_answered",
    ]
    injected = next(
        item for item in memory_events(events) if item["kind"] == "injected"
    )
    assert injected["probe_id"] == "q1"
    assert list(injected["origin_refs"]) == list(probe.execution.injected_evidence)

    # Windows 紧凑路径：trial 根固定在 attempt 目录下、目录名刻意压短。
    trial_root = tmp_path / "a1" / "mtrial"
    assert trial_root.is_dir()
    longest = max(len(str(item)) for item in trial_root.rglob("*"))
    assert longest < 260
    assert run.trial.scope.workspace_id == "synthetic-w1"


async def test_gold_never_reaches_worker_frames_or_disk(tmp_path: Path) -> None:
    scenario = scenario_payload(
        gold={
            "q1": {
                "required_facts": [
                    {"key": "city", "value": "青岚城", "evidence_message_ids": ["m1"]}
                ],
                "answerability": "answerable",
            },
            "extraction_facts": [
                {"key": "gold_sentinel", "value": GOLD_SENTINEL},
            ],
        }
    )
    boot = memory_boot(tmp_path, str(uuid.uuid4()), scenario=scenario)
    result, events = await run_subprocess_worker(boot, tmp_path)

    scenario_run_of(result)  # result 帧本身可解析
    serialized = json.dumps(
        {
            "result": result,
            "events": [event.model_dump(mode="json") for event in events],
        },
        ensure_ascii=False,
    )
    assert GOLD_SENTINEL not in serialized
    assert '"required_facts"' not in serialized
    assert '"answerability"' not in serialized

    # 磁盘上（Trace/产物/工作区/trial 库/日志）同样没有 gold 哨兵。
    sentinel = GOLD_SENTINEL.encode("utf-8")
    for item in tmp_path.rglob("*"):
        if item.is_file():
            assert sentinel not in item.read_bytes(), f"gold leaked to {item}"


# ================================================================== 真实子进程：三轨


async def test_subprocess_extraction_track_exports_materials_without_probes(
    tmp_path: Path,
) -> None:
    scenario = scenario_payload(
        gold={"extraction_facts": [{"key": "city", "value": "青岚城"}]},
    )
    boot = memory_boot(
        tmp_path,
        str(uuid.uuid4()),
        track="extraction",
        scenario=scenario,
        script=controlled_script(with_probes=False),
    )
    result, events = await run_subprocess_worker(boot, tmp_path)

    run = scenario_run_of(result)
    assert result["execution_status"] == "succeeded"
    assert run.trial.probes == []
    assert [fact.value for fact in run.trial.extracted_facts] == [CITY_FACT_CONTENT]
    assert run.trial.source_supported_facts is not None
    assert run.trial.state_observations is not None
    assert any(
        obs.surface == "store" and obs.present
        for obs in (run.trial.state_observations or [])
    )
    # skip 策略入账（extraction 轨不应答 probe），不静默剔除也不伪装。
    assert [entry.kind for entry in run.failure_ledger] == ["probes_skipped_by_track"]
    memory_kinds = [str(item.get("kind")) for item in memory_events(events)]
    assert "session_closed" in memory_kinds
    assert "extraction_completed" in memory_kinds
    assert "probe_answered" not in memory_kinds


async def test_subprocess_retrieval_track_seeds_reference_and_skips_history(
    tmp_path: Path,
) -> None:
    scenario = scenario_payload(
        reference_state={
            "version": "ref/v1",
            "facts": [{"key": "city", "value": "青岚城"}],
        }
    )
    boot = memory_boot(
        tmp_path,
        str(uuid.uuid4()),
        track="retrieval",
        scenario=scenario,
        script=[{"text": PROBE_ANSWER}],
    )
    result, events = await run_subprocess_worker(boot, tmp_path)

    run = scenario_run_of(result)
    assert result["execution_status"] == "succeeded"
    # 跳过历史有账目；probe 仍执行（skip 策略导致能力失败，不是剔除）。
    kinds = [entry.kind for entry in run.failure_ledger]
    assert "session_skipped_by_track" in kinds
    assert "settle_skipped_by_track" in kinds
    assert run.coverage.scored == 1 and run.coverage.planned == 1
    probe = run.trial.probes[0]
    assert probe.execution.answer == PROBE_ANSWER
    memory_kinds = [str(item.get("kind")) for item in memory_events(events)]
    assert "session_closed" not in memory_kinds
    assert "extraction_completed" not in memory_kinds
    assert "probe_answered" in memory_kinds


# ================================================================== 进程内：失败账目


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content
        self.usage = None


class ScriptedProvider:
    """按第 N 次调用回放；可注入首调延迟与异常（进程内失败账目用）。"""

    def __init__(self, responses: list[Any], *, first_call_delay: float = 0.0) -> None:
        self.responses = list(responses)
        self.first_call_delay = first_call_delay
        self.calls = 0
        self.generation = None

    def get_default_model(self) -> str:
        return "eval-model"

    def set_llm_call_observer(self, observer: Any) -> None:
        self._observer = observer

    async def chat_with_retry(self, *, messages: list[dict[str, Any]], **_: Any) -> Any:
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        if self.calls == 1 and self.first_call_delay:
            await asyncio.sleep(self.first_call_delay)
        response = self.responses[index]
        if isinstance(response, Exception):
            raise response
        return _FakeResponse(response)


async def _run_memory_inprocess(
    tmp_path: Path, boot: AgentWorkerBoot, provider: Any
) -> Any:
    from nanobot.evaluation.memory.worker_bridge import run_memory_worker_execution
    from nanobot.evaluation.runtime_factory import create_attempt

    runtime = await create_attempt(boot, provider=provider)
    try:
        return await run_memory_worker_execution(
            runtime, dict(boot.memory_execution or {})
        )
    finally:
        await runtime.aclose()


@pytest.fixture
def _restore_config_path():
    previous = loader._current_config_path  # pyright: ignore[reportPrivateUsage]
    yield
    loader.set_config_path(previous)  # pyright: ignore[reportArgumentType]


@pytest.mark.usefixtures("_restore_config_path")
async def test_model_service_error_is_kept_in_ledger_and_answer(tmp_path: Path) -> None:
    boot = memory_boot(tmp_path, str(uuid.uuid4()))
    provider = ScriptedProvider([RuntimeError("model service unavailable")])
    outcome = await _run_memory_inprocess(tmp_path, boot, provider)

    kinds = [entry.kind for entry in outcome.run.failure_ledger]
    assert "settle_failed" in kinds
    # probe 未被剔除，回答如实带错误前缀；失败账目随 memory_result 保留。
    probe = outcome.run.trial.probes[0]
    answer = probe.execution.answer
    assert answer is not None and answer.startswith("PROVIDER_ERROR")
    assert outcome.run.coverage.planned == 1


@pytest.mark.usefixtures("_restore_config_path")
async def test_settle_timeout_is_ledgered_and_run_continues(tmp_path: Path) -> None:
    scenario = scenario_payload(
        events=[
            {
                "type": "session",
                "id": "s1",
                "at": "2026-11-01T09:00:00+08:00",
                "messages": [
                    {"id": "m1", "role": "user",
                     "content": "请记住，我的虚构测试城市叫青岚城。"},
                    {"id": "m2", "role": "assistant", "content": "收到这条信息。"},
                    {"id": "m3", "role": "user", "content": "之后问居住地时请参考。"},
                ],
            },
            {"type": "settle", "after_session": "s1", "timeout_s": 1},
        ]
    )
    boot = memory_boot(tmp_path, str(uuid.uuid4()), scenario=scenario)
    provider = ScriptedProvider(
        [SEMANTIC_JSON, EPISODE_JSON, PROBE_ANSWER], first_call_delay=3.0
    )
    outcome = await _run_memory_inprocess(tmp_path, boot, provider)

    kinds = [entry.kind for entry in outcome.run.failure_ledger]
    assert "settle_timeout" in kinds
    assert outcome.run.coverage.scored == 1 and outcome.run.coverage.planned == 1
    assert outcome.run.trial.probes[0].execution.answer == PROBE_ANSWER


@pytest.mark.usefixtures("_restore_config_path")
async def test_observer_corruption_preserves_failure_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nanobot.evaluation.memory import worker_bridge

    class BrokenObserver(worker_bridge.ZhixuWorkerObserver):
        def parse_answer_facts(self, answer: str) -> list[MemoryFact] | None:
            raise RuntimeError("observer broken")

        def export_extracted_facts(self) -> list[MemoryFact] | None:
            raise RuntimeError("observer broken")

        def export_source_supported_facts(self) -> list[MemoryFact] | None:
            raise RuntimeError("observer broken")

        def export_state_observations(self) -> list[Any] | None:
            raise RuntimeError("observer broken")

    monkeypatch.setattr(worker_bridge, "ZhixuWorkerObserver", BrokenObserver)
    boot = memory_boot(tmp_path, str(uuid.uuid4()))
    provider = ControlledProvider(
        steps=tuple(ScriptStep(**item) for item in controlled_script())
    )
    outcome = await _run_memory_inprocess(tmp_path, boot, provider)

    kinds = [entry.kind for entry in outcome.run.failure_ledger]
    assert kinds.count("observer_error") == 4
    assert outcome.run.trial.extracted_facts == []
    assert outcome.run.trial.source_supported_facts is None
    probe = outcome.run.trial.probes[0]
    assert probe.execution.answer == PROBE_ANSWER
    assert probe.answer_facts_parsed is False


@pytest.mark.usefixtures("_restore_config_path")
async def test_memory_seed_is_never_invoked_and_paths_stay_compact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nanobot.evaluation.runtime_factory as runtime_factory

    calls: list[Any] = []
    original = runtime_factory.seed_memory

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime_factory, "seed_memory", spy)
    boot = memory_boot(tmp_path, str(uuid.uuid4()))
    provider = ControlledProvider(steps=(ScriptStep(text=PROBE_ANSWER),))
    outcome = await _run_memory_inprocess(tmp_path, boot, provider)

    assert calls == [], "seed_memory must not run for scenario cases"
    from nanobot.evaluation.runtime_factory import attempt_paths

    paths = attempt_paths(
        attempt_id=str(boot.attempt_id),
        runtime_root=str(boot.runtime_root),
        workspace=str(boot.workspace),
    )
    assert (paths.root / "mtrial").is_dir()
    assert len(str(paths.root / "mtrial" / "workspaces")) < 260
    assert outcome.run.trial.scope.workspace_id == "synthetic-w1"


@pytest.mark.usefixtures("_restore_config_path")
async def test_worker_cancel_lands_cancelled_state_with_error_code(
    tmp_path: Path,
) -> None:
    class HangingProvider(ScriptedProvider):
        async def chat_with_retry(self, *, messages: Any, **_: Any) -> Any:
            self.calls += 1
            await asyncio.sleep(30)

    attempt_id = str(uuid.uuid4())
    boot = memory_boot(tmp_path, attempt_id)
    frames: list[bytes] = []
    channel = WorkerChannel(attempt_id=attempt_id, sink=frames.append)
    task = asyncio.create_task(
        run_agent_worker(boot, channel=channel, provider=HangingProvider([]))
    )
    await asyncio.sleep(1.5)  # 进入执行段（settle 挂在 provider 上）
    task.cancel()
    result = await task

    assert result.execution_status == "cancelled"
    assert any(error.code == WORKER_CANCELLED for error in result.errors)
    assert result.memory_result is None


# ================================================================== 父进程评分接线


def _memory_result_dump() -> dict[str, Any]:
    """构造 worker 密封返回的 ``MemoryScenarioRun`` JSON 形状（tags 留空待回填）。"""
    execution = MemoryExecutionInput.model_validate(
        {**execution_fields(scenario_payload()), "track": "end_to_end"}
    )
    outcome = MemoryProbeOutcome(
        execution=ProbeExecution(
            probe_id="q1",
            answer=PROBE_ANSWER,
            recalled=[MemoryRecallItem(rank=1, key="city", value="青岚城")],
            usage=MemoryUsage(
                input_tokens=3, output_tokens=2, total_tokens=5,
                physical_requests=1, source="reported",
            ),
            coverage=MemoryCoverage(scored=1, planned=1),
        ),
        question="我住在哪个城市？",
        asked_at=execution.probes[0].at,
        scope=execution.scope,
        asserted_facts=[MemoryFact(key="city", value="青岚城")],
        answer_facts_parsed=True,
        injected_facts=[],
        after_forget=False,
        scope_observable=True,
    )
    trial = build_trial_execution(
        execution,
        probes=[outcome],
        extracted_facts=[MemoryFact(key="city", value="青岚城")],
        source_supported_facts=[MemoryFact(key="city", value="青岚城")],
        state_observations=[
            MemoryStateObservation(fact_key="city", surface="store", present=True)
        ],
        forgotten_fact_keys=[],
    )
    return MemoryScenarioRun(trial=trial).model_dump(mode="json")


@pytest.fixture
def grading_service(tmp_path: Path, evaluation_repository: Any) -> Any:
    from nanobot.evaluation.services import EvaluationService, Profile

    return EvaluationService(
        repository=evaluation_repository,
        data_dir=tmp_path,
        profiles=(
            Profile(
                id="agent-1", label="受控执行器", provider="controlled",
                model="eval-model", kind="agent",
                bundle={"config": {"providers": {"controlled": {"api_key": "sk-x"}}}},
            ),
        ),
    )


def _memory_case(service: Any, evaluation_repository: Any) -> CaseWrite:
    suite_id = str(
        evaluation_repository.create_suite(make_suite_payload())["id"]
    )
    revision = service.memory_repository.import_revision(
        make_memory_dataset_payload(suite_id=suite_id)
    )
    revision_id = str(revision["dataset_revision"]["id"])
    return CaseWrite.model_validate(
        make_memory_case_payload(suite_id=suite_id, dataset_revision_id=revision_id)
    )


def test_memory_execution_payload_excludes_gold_and_carries_track(
    grading_service: Any, evaluation_repository: Any
) -> None:
    case = _memory_case(grading_service, evaluation_repository)
    payload = grading_service._memory_execution_payload(case)  # pyright: ignore[reportPrivateUsage]

    assert "gold" not in payload
    assert payload["scenario_id"] == "fact-001"
    assert payload["track"] == "end_to_end"
    assert payload["events"] and payload["probes"]


def test_grade_memory_result_fills_tags_and_grades(
    grading_service: Any, evaluation_repository: Any
) -> None:
    case = _memory_case(grading_service, evaluation_repository)
    graded = grading_service._grade_memory_result(  # pyright: ignore[reportPrivateUsage]
        case, {"memory_result": _memory_result_dump()}
    )

    assert graded["scenario_id"] == "fact-001"
    assert graded["revision"] == 1
    assert graded["track"] == "end_to_end"
    metrics = graded["metrics"]
    # tags 由父进程按场景回填（runner 不知道类别）。
    assert metrics["categories"] == ["fact", "cross_session"]
    probe = metrics["probe_results"][0]
    assert probe["qa_correct"] is True
    assert probe["qa_status"] == "scored"


def test_grade_memory_result_rejects_missing_or_invalid_payload(
    grading_service: Any, evaluation_repository: Any
) -> None:
    from nanobot.evaluation.services import ServiceError

    case = _memory_case(grading_service, evaluation_repository)
    with pytest.raises(ServiceError, match="MEMORY_RESULT_MISSING"):
        grading_service._grade_memory_result(case, None)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ServiceError, match="MEMORY_RESULT_INVALID"):
        grading_service._grade_memory_result(  # pyright: ignore[reportPrivateUsage]
            case, {"memory_result": {"trial": {"scenario_id": "fact-001"}}}
        )
