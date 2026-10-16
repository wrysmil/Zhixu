"""T06：物理调用 Token 账本（契约 §4/§5、spec §3/§7.3）。

口径要点：

- **实际 provider call 是主账本**。``round_usages`` 只做交叉检查，任何情况下都不改写主账本
  数字 —— 轮次上报会把重试/恢复的物理调用合并掉，这正是 spec §7.3 禁止「把只记录主循环的
  数值标成完整 Agent 总量」的原因。
- **未知量不补零**。没有 ``usage`` 的物理调用计入 ``missing_usage_calls`` 并把 coverage 压到
  ``partial``；整轮都没有用量时三个 Token 字段保持 ``None``、来源标 ``unavailable``。
- **agent 与 judge 分开记账**，屏障要求「两种 Token 账本不重复」。
- provider 侧的 usage observer 是 fail-open 的（异常被吞），所以账本自己保留 sticky 错误，
  由执行结束时 ``ensure_clean()`` 检查；异步队列满时同样走这条路径而不是丢证据。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from nanobot.evaluation.schemas import UsageAggregate
from nanobot.llm_usage.models import LLMCallRecord
from nanobot.providers.base import LLMUsage

UsageRole = Literal["agent", "judge"]
CallScope = Literal["main", "auxiliary", "unknown"]

ROLES: frozenset[str] = frozenset({"agent", "judge"})
SCOPES: frozenset[str] = frozenset({"main", "auxiliary", "unknown"})


@dataclass(frozen=True, slots=True)
class _Call:
    record: LLMCallRecord
    role: str
    scope: str


@dataclass(frozen=True, slots=True)
class ScopeBreakdown:
    """按 scope 分桶的物理调用数与 Token，供报告解释「主循环 vs 辅助调用」。"""

    scope: str
    calls: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True, slots=True)
class UsageCrossCheck:
    """``round_usages`` 与物理账本的差异报告；只报差异，不参与任何数值计算。"""

    role: str
    round_requests: int
    physical_requests: int
    round_total_tokens: int | None
    physical_total_tokens: int | None
    matched: bool
    differences: tuple[str, ...]


class ObserverCapableProvider(Protocol):
    """provider 上唯一的 observer 槽位（``LLMProvider.set_llm_call_observer``）。"""

    def set_llm_call_observer(self, observer: Callable[[LLMCallRecord], None] | None) -> None: ...


def check_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"USAGE_ROLE_NOT_ALLOWED: {role}")


def check_scope(scope: str) -> None:
    if scope not in SCOPES:
        raise ValueError(f"USAGE_SCOPE_NOT_ALLOWED: {scope}")


class UsageLedger:
    """按 role/scope 累积物理调用并派生契约 §4 的 ``UsageAggregate``。"""

    def __init__(self) -> None:
        self._calls: list[_Call] = []
        self._gaps: dict[str, list[str]] = {"agent": [], "judge": []}
        self.observer_errors: list[str] = []

    # ------------------------------------------------------------------ 写入

    def _append(self, call: _Call) -> None:
        """账本唯一的可变写入点；坏对象必须在这里失败，好让 observer 留下 sticky 痕迹。"""
        if not isinstance(call.record, LLMCallRecord):
            raise TypeError(f"USAGE_RECORD_INVALID: {type(call.record).__name__}")
        self._calls.append(call)

    def record(
        self, record: LLMCallRecord, *, role: str, scope: str = "main"
    ) -> None:
        check_role(role)
        check_scope(scope)
        self._append(_Call(record=record, role=role, scope=scope))

    def note_observer_error(self, detail: str) -> None:
        self.observer_errors.append(detail)

    def mark_coverage_gap(self, role: str, *, reason: str) -> None:
        """已知存在未被 observer 覆盖的调用（例如辅助 provider 没接上）时压低 coverage。"""
        check_role(role)
        self._gaps[role].append(reason)

    def ensure_clean(self) -> None:
        """结束检查：observer 抛出会被 provider 吞掉，因此错误必须在这里重新可见。"""
        if self.observer_errors:
            joined = "; ".join(self.observer_errors[:5])
            raise RuntimeError(f"USAGE_OBSERVER_FAILED: {joined}")

    def _bucket(self, role: str) -> list[_Call]:
        self._check_known_role(role)
        return [item for item in self._calls if item.role == role]

    @staticmethod
    def _check_known_role(role: str) -> None:
        if role not in ROLES:
            raise KeyError(f"USAGE_ROLE_UNKNOWN: {role}")

    # ------------------------------------------------------------------ 读出

    def calls(self, role: str) -> tuple[ScopeBreakdown, ...]:
        bucket = self._bucket(role)
        out: list[ScopeBreakdown] = []
        for scope in ("main", "auxiliary", "unknown"):
            rows = [item for item in bucket if item.scope == scope]
            if not rows:
                continue
            usages = [item.record.usage for item in rows if item.record.usage is not None]
            out.append(
                ScopeBreakdown(
                    scope=scope,
                    calls=len(rows),
                    input_tokens=_sum(usages, "input_tokens"),
                    output_tokens=_sum(usages, "output_tokens"),
                    total_tokens=_sum(usages, "total_tokens"),
                )
            )
        return tuple(out)

    def aggregate(self, role: str, *, iterations: int = 0) -> UsageAggregate:
        """物理调用汇总；``iterations`` 由 Runner 提供，账本不猜测轮数。"""
        bucket = self._bucket(role)
        usages = [item.record.usage for item in bucket if item.record.usage is not None]
        missing = len(bucket) - len(usages)
        if not bucket:
            return UsageAggregate(
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                source="unavailable",
                coverage="unavailable",
                physical_requests=0,
                iterations=iterations,
                missing_usage_calls=0,
            )
        unknown_scopes = sum(1 for item in bucket if item.scope == "unknown")
        coverage: str
        if not usages:
            coverage = "unavailable"
        elif missing == 0 and unknown_scopes == 0 and not self._gaps[role]:
            coverage = "complete"
        else:
            coverage = "partial"
        return UsageAggregate(
            input_tokens=_sum(usages, "input_tokens"),
            output_tokens=_sum(usages, "output_tokens"),
            total_tokens=_sum(usages, "total_tokens"),
            source=_source(usages),
            coverage=coverage,
            physical_requests=len(bucket),
            iterations=iterations,
            missing_usage_calls=missing,
        )

    def usage_summary(
        self, *, agent_iterations: int, judge_iterations: int
    ) -> dict[str, UsageAggregate]:
        return {
            "agent": self.aggregate("agent", iterations=agent_iterations),
            "judge": self.aggregate("judge", iterations=judge_iterations),
        }

    def cross_check_round_usages(
        self, role: str, round_usages: Sequence[LLMUsage]
    ) -> UsageCrossCheck:
        """轮次上报只用于发现「重试被合并藏起来」，差异不改写主账本。"""
        bucket = self._bucket(role)
        usages = [item.record.usage for item in bucket if item.record.usage is not None]
        physical_total = _sum(usages, "total_tokens")
        round_total = sum(row.total_tokens for row in round_usages) if round_usages else None
        differences: list[str] = []
        if len(round_usages) != len(bucket):
            differences.append(
                f"physical_requests={len(bucket)} round_requests={len(round_usages)}"
            )
        if round_total is not None and physical_total is not None:
            if round_total != physical_total:
                differences.append(
                    f"total_tokens round={round_total} physical={physical_total}"
                )
        elif round_total is not None or physical_total is not None:
            differences.append("total_tokens available on one side only")
        return UsageCrossCheck(
            role=role,
            round_requests=len(round_usages),
            physical_requests=len(bucket),
            round_total_tokens=round_total,
            physical_total_tokens=physical_total,
            matched=not differences,
            differences=tuple(differences),
        )


def _sum(usages: Iterable[LLMUsage], field: str) -> int | None:
    rows = list(usages)
    if not rows:
        return None
    return sum(getattr(row, field) for row in rows)


def _source(usages: list[LLMUsage]) -> Literal["reported", "estimated", "mixed", "unavailable"]:
    if not usages:
        return "unavailable"
    reported = any(row.reported_tokens > 0 for row in usages)
    estimated = any(row.estimated_tokens > 0 for row in usages)
    if reported and estimated:
        return "mixed"
    if estimated:
        return "estimated"
    return "reported"


def make_usage_observer(
    ledger: UsageLedger, *, role: str, scope: str
) -> Callable[[LLMCallRecord], None]:
    """把一个账本接到 provider 的 observer 槽位上；异常一律转成 sticky 记录。"""
    check_role(role)
    check_scope(scope)

    def observer(record: LLMCallRecord) -> None:
        try:
            ledger.record(record, role=role, scope=scope)
        except Exception as exc:  # noqa: BLE001 - provider 侧 fail-open，这里必须自己留痕
            ledger.note_observer_error(f"{role}/{scope}: {type(exc).__name__}: {exc}")

    return observer


def install_usage_observer(
    provider: ObserverCapableProvider,
    *,
    ledger: UsageLedger,
    role: str,
    scope: str,
    previous: Callable[[LLMCallRecord], None] | None = None,
) -> Callable[[LLMCallRecord], None]:
    """只改传入的 provider 实例，并显式串接既有 observer（槽位只有一个）。"""
    observer = make_usage_observer(ledger, role=role, scope=scope)

    def chained(record: LLMCallRecord) -> None:
        if previous is not None:
            try:
                previous(record)
            except Exception as exc:  # noqa: BLE001 - 旧观察者的故障同样要留痕
                ledger.note_observer_error(f"previous: {type(exc).__name__}: {exc}")
        observer(record)

    provider.set_llm_call_observer(chained)
    return chained
