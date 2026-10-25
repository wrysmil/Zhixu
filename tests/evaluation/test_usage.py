"""T06：物理调用 Token 账本（契约 §4/§5、spec §3）。

口径来自契约硬约束：实际 provider call 是主账本，``round_usages`` 只做交叉检查、禁止再相加；
未知量不补零；辅助调用缺失 → ``coverage=partial``；agent 与 judge 分开记账（屏障要求"两种
Token 账本不重复"）。observer 是 fail-open 的，所以写账本失败必须留 sticky 标志而不是抛出。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

from nanobot.evaluation.schemas import UsageAggregate
from nanobot.evaluation.usage import (
    UsageLedger,
    install_usage_observer,
    make_usage_observer,
)
from nanobot.llm_usage.models import LLMCallRecord
from nanobot.providers.base import LLMUsage


def call_record(
    *,
    input_tokens: int | None = 100,
    output_tokens: int | None = 20,
    total_tokens: int | None = None,
    estimated: int = 0,
    model: str = "test-model",
) -> LLMCallRecord:
    """构造一次物理调用记录；``LLMUsage`` 要求 reported + estimated == total。"""
    usage: LLMUsage | None
    if input_tokens is None and output_tokens is None:
        usage = None
    else:
        visible_in = input_tokens or 0
        visible_out = output_tokens or 0
        total = (
            total_tokens
            if total_tokens is not None
            else visible_in + visible_out
        )
        usage = LLMUsage(
            input_tokens=visible_in,
            output_tokens=visible_out,
            total_tokens=total,
            reported_tokens=total - estimated,
            estimated_tokens=estimated,
        )
    return LLMCallRecord(
        started_at_ms=0,
        duration_ms=5,
        provider="openai",
        model=model,
        source="user",
        stream=False,
        finish_reason="stop",
        usage=usage,
    )


def test_physical_calls_are_the_primary_ledger() -> None:
    ledger = UsageLedger()
    for _ in range(3):
        ledger.record(call_record(), role="agent", scope="main")

    aggregate = ledger.aggregate("agent", iterations=2)
    assert aggregate.physical_requests == 3
    assert aggregate.input_tokens == 300
    assert aggregate.output_tokens == 60
    assert aggregate.total_tokens == 360
    assert aggregate.iterations == 2
    assert aggregate.source == "reported"
    assert aggregate.coverage == "complete"
    assert aggregate.missing_usage_calls == 0


def test_missing_usage_is_not_zero_filled() -> None:
    ledger = UsageLedger()
    ledger.record(call_record(), role="agent", scope="main")
    ledger.record(call_record(input_tokens=None, output_tokens=None), role="agent", scope="main")

    aggregate = ledger.aggregate("agent", iterations=2)
    assert aggregate.physical_requests == 2
    assert aggregate.missing_usage_calls == 1
    assert aggregate.coverage == "partial"
    # 缺一条不补零，也不把已有实报抹掉。
    assert aggregate.input_tokens == 100
    assert aggregate.source == "reported"


def test_total_unknown_usage_stays_none() -> None:
    ledger = UsageLedger()
    ledger.record(call_record(input_tokens=None, output_tokens=None), role="judge", scope="main")

    aggregate = ledger.aggregate("judge", iterations=1)
    assert aggregate.input_tokens is None
    assert aggregate.output_tokens is None
    assert aggregate.total_tokens is None
    assert aggregate.source == "unavailable"
    assert aggregate.coverage == "unavailable"
    assert aggregate.physical_requests == 1


def test_estimated_and_reported_mix_is_marked_mixed() -> None:
    ledger = UsageLedger()
    ledger.record(call_record(), role="agent", scope="main")
    ledger.record(call_record(estimated=120), role="agent", scope="main")

    aggregate = ledger.aggregate("agent", iterations=1)
    assert aggregate.source == "mixed"
    assert aggregate.coverage == "complete"


def test_agent_and_judge_ledgers_do_not_double_count() -> None:
    ledger = UsageLedger()
    ledger.record(call_record(input_tokens=100, output_tokens=10), role="agent", scope="main")
    ledger.record(call_record(input_tokens=700, output_tokens=5), role="judge", scope="main")

    agent = ledger.aggregate("agent", iterations=1)
    judge = ledger.aggregate("judge", iterations=1)
    assert agent.input_tokens == 100
    assert judge.input_tokens == 700
    assert agent.physical_requests == judge.physical_requests == 1
    usage = ledger.usage_summary(agent_iterations=1, judge_iterations=1)
    assert usage["agent"].input_tokens == 100
    assert usage["judge"].input_tokens == 700


def test_stable_model_call_id_deduplicates_within_role_only() -> None:
    ledger = UsageLedger()
    record = call_record(input_tokens=100, output_tokens=10)

    ledger.record(record, role="agent", scope="main", model_call_id="model-call-1")
    ledger.record(record, role="agent", scope="main", model_call_id="model-call-1")
    ledger.record(record, role="judge", scope="main", model_call_id="model-call-1")

    assert ledger.aggregate("agent", iterations=1).physical_requests == 1
    assert ledger.aggregate("judge", iterations=1).physical_requests == 1
    assert [call.model_call_id for call in ledger.physical_calls("agent")] == [
        "model-call-1"
    ]


def test_physical_call_keeps_optional_execution_identity_without_guessing() -> None:
    ledger = UsageLedger()
    ledger.record(
        call_record(),
        role="agent",
        scope="auxiliary",
        model_call_id="model-call-child",
        agent_id="researcher",
        agent_name="Researcher",
        execution_id="exec-child",
        parent_execution_id="exec-root",
    )
    ledger.record(
        call_record(),
        role="agent",
        scope="main",
        model_call_id="model-call-legacy",
    )

    child, legacy = ledger.physical_calls("agent")
    assert child.agent_id == "researcher"
    assert child.agent_name == "Researcher"
    assert child.execution_id == "exec-child"
    assert child.parent_execution_id == "exec-root"
    assert legacy.agent_id is None
    assert legacy.execution_id is None
    assert legacy.parent_execution_id is None


def test_auxiliary_scope_is_tracked_separately() -> None:
    ledger = UsageLedger()
    ledger.record(call_record(input_tokens=100, output_tokens=10), role="agent", scope="main")
    ledger.record(call_record(input_tokens=7, output_tokens=3), role="agent", scope="auxiliary")

    aggregate = ledger.aggregate("agent", iterations=2)
    assert aggregate.physical_requests == 2
    assert aggregate.input_tokens == 107
    by_scope = {item.scope: item.calls for item in ledger.calls("agent")}
    assert by_scope == {"main": 1, "auxiliary": 1}


def test_observer_records_are_fail_open_with_sticky_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = UsageLedger()
    observer = make_usage_observer(ledger, role="agent", scope="main")
    observer(call_record())
    assert ledger.observer_errors == []
    assert ledger.aggregate("agent", iterations=1).physical_requests == 1

    def explode(record: LLMCallRecord) -> None:
        raise RuntimeError("boom")

    # 账本内部写入点故障：provider 侧 fail-open 吞掉异常，所以必须留 sticky 痕迹给结束检查。
    monkeypatch.setattr(ledger, "_append", explode)
    observer(call_record())
    assert len(ledger.observer_errors) == 1
    assert "boom" in ledger.observer_errors[0]
    assert ledger.aggregate("agent", iterations=1).physical_requests == 1


def test_round_usages_are_cross_checked_only() -> None:
    ledger = UsageLedger()
    ledger.record(call_record(input_tokens=100, output_tokens=20), role="agent", scope="main")
    ledger.record(call_record(input_tokens=100, output_tokens=20), role="agent", scope="main")

    aggregate = ledger.aggregate("agent", iterations=1)
    round_usage = LLMUsage(input_tokens=100, output_tokens=20, total_tokens=120, reported_tokens=120)
    report = ledger.cross_check_round_usages("agent", [round_usage, round_usage, round_usage])

    # 轮次用量比物理调用多：只报差异，绝不改动主账本数字。
    assert aggregate.input_tokens == 200
    assert report.round_requests == 3
    assert report.physical_requests == 2
    assert report.matched is False


def test_aggregate_shape_matches_contract() -> None:
    ledger = UsageLedger()
    ledger.record(call_record(), role="agent", scope="unknown")
    aggregate = ledger.aggregate("agent", iterations=0)
    assert isinstance(aggregate, UsageAggregate)
    assert aggregate.physical_requests == 1
    with pytest.raises(KeyError):
        ledger.aggregate("grader")


def test_unattributed_scope_cannot_claim_complete_coverage() -> None:
    # spec §7.3：禁止把只记录主循环的数值标成完整 Agent 总量。未归因的调用必须压低 coverage。
    ledger = UsageLedger()
    ledger.record(call_record(), role="agent", scope="main")
    ledger.record(call_record(), role="agent", scope="unknown")

    aggregate = ledger.aggregate("agent", iterations=1)
    assert aggregate.coverage == "partial"
    assert aggregate.missing_usage_calls == 0
    assert aggregate.input_tokens == 200


def test_missing_auxiliary_coverage_is_marked_partial() -> None:
    # 辅助 provider 没装上 observer（factory 接点缺失）时，账本仍是实报完整，
    # 但 coverage 必须降到 partial，报告不能显示成完整。
    ledger = UsageLedger()
    ledger.record(call_record(), role="agent", scope="main")
    assert ledger.aggregate("agent", iterations=1).coverage == "complete"

    ledger.mark_coverage_gap("agent", reason="auxiliary provider has no observer")
    aggregate = ledger.aggregate("agent", iterations=1)
    assert aggregate.coverage == "partial"
    assert aggregate.source == "reported"


def test_role_without_calls_is_unavailable_not_zero_filled() -> None:
    ledger = UsageLedger()
    ledger.record(call_record(), role="agent", scope="main")

    judge = ledger.aggregate("judge", iterations=0)
    assert judge.input_tokens is None
    assert judge.total_tokens is None
    assert judge.physical_requests == 0
    assert judge.source == "unavailable"
    assert judge.coverage == "unavailable"


def test_estimated_only_calls_report_estimated_source() -> None:
    ledger = UsageLedger()
    ledger.record(call_record(estimated=120), role="agent", scope="main")

    aggregate = ledger.aggregate("agent", iterations=1)
    assert aggregate.source == "estimated"
    assert aggregate.coverage == "complete"
    assert aggregate.total_tokens == 120


def test_sticky_observer_error_is_surfaced_at_the_end_of_a_run() -> None:
    # observer 在 provider 侧被吞掉，所以结束检查必须由账本自己给出可见错误。
    ledger = UsageLedger()
    ledger.ensure_clean()

    observer = make_usage_observer(ledger, role="agent", scope="main")

    class Broken:
        pass

    observer(Broken())  # type: ignore[arg-type]
    assert len(ledger.observer_errors) == 1
    with pytest.raises(RuntimeError) as excinfo:
        ledger.ensure_clean()
    assert "USAGE_OBSERVER_FAILED" in str(excinfo.value)


def test_observer_factory_rejects_roles_outside_the_contract() -> None:
    ledger = UsageLedger()
    with pytest.raises(ValueError):
        make_usage_observer(ledger, role="grader", scope="main")
    with pytest.raises(ValueError):
        make_usage_observer(ledger, role="agent", scope="background")
    with pytest.raises(ValueError):
        ledger.record(call_record(), role="agent", scope="subagent")


def test_matching_round_usages_confirm_the_physical_ledger() -> None:
    ledger = UsageLedger()
    ledger.record(call_record(input_tokens=100, output_tokens=20), role="agent", scope="main")
    ledger.record(call_record(input_tokens=100, output_tokens=20), role="agent", scope="main")

    round_usages = [
        LLMUsage(input_tokens=100, output_tokens=20, total_tokens=120, reported_tokens=120),
        LLMUsage(input_tokens=100, output_tokens=20, total_tokens=120, reported_tokens=120),
    ]
    report = ledger.cross_check_round_usages("agent", round_usages)
    assert report.matched is True
    assert report.round_requests == 2
    assert report.physical_requests == 2
    assert report.differences == ()
    assert ledger.aggregate("agent", iterations=2).total_tokens == 240


def test_round_usages_missing_a_physical_call_is_reported_as_mismatch() -> None:
    ledger = UsageLedger()
    ledger.record(call_record(input_tokens=100, output_tokens=20), role="agent", scope="main")
    ledger.record(call_record(input_tokens=50, output_tokens=10), role="agent", scope="main")

    # 轮次用量把两次物理请求合并成一轮上报：Token 合计碰巧相同，但请求数不同，
    # 交叉检查必须仍然报不一致（重试/恢复轮就是这么被藏起来的）。
    round_usages = [LLMUsage(input_tokens=150, output_tokens=30, total_tokens=180, reported_tokens=180)]
    report = ledger.cross_check_round_usages("agent", round_usages)
    assert report.matched is False
    assert report.round_requests == 1
    assert report.physical_requests == 2
    assert report.round_total_tokens == 180
    assert report.physical_total_tokens == 180
    assert any("physical_requests" in item for item in report.differences)
    # 交叉检查只报差异，绝不改写主账本。
    assert ledger.aggregate("agent", iterations=1).total_tokens == 180


def test_cross_check_rejects_unknown_role() -> None:
    ledger = UsageLedger()
    with pytest.raises(KeyError):
        ledger.cross_check_round_usages("grader", [])


class StubProvider:
    """只提供 ``set_llm_call_observer`` 接点，不碰真实 provider。"""

    def __init__(self) -> None:
        self.installed: object | None = None

    def set_llm_call_observer(self, observer: object | None) -> None:
        self.installed = observer


def test_install_usage_observer_only_touches_the_provider_it_is_given() -> None:
    ledger = UsageLedger()
    provider = StubProvider()
    untouched = StubProvider()

    installed = install_usage_observer(provider, ledger=ledger, role="agent", scope="main")
    assert provider.installed is not None
    assert untouched.installed is None

    cast("Callable[[LLMCallRecord], None]", installed)(call_record())
    assert ledger.aggregate("agent", iterations=1).physical_requests == 1


def test_install_usage_observer_chains_a_previous_observer() -> None:
    # provider 只有一个 observer 槽位：旧观察者必须显式交回并串接，否则装账本会丢掉既有记录。
    ledger = UsageLedger()
    provider = StubProvider()
    seen: list[str] = []

    def previous(record: LLMCallRecord) -> None:
        seen.append(record.model)
        raise RuntimeError("store down")

    installed = install_usage_observer(
        provider,
        ledger=ledger,
        role="judge",
        scope="auxiliary",
        previous=previous,
    )
    cast("Callable[[LLMCallRecord], None]", installed)(call_record(model="judge-model"))

    assert seen == ["judge-model"]
    assert ledger.aggregate("judge", iterations=0).physical_requests == 1
    assert any("store down" in error for error in ledger.observer_errors)
