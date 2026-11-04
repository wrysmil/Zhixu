"""记忆实验组：分批计划、组预算、子 run 入队与恢复（契约 §2 / §5 / §7）。

分工与边界：

- **调度器**是唯一的 attempt 执行者与结果提交者；本模块只负责「创建/取消子 run 与
  聚合」，不另起执行信号量。
- **仓储**是唯一的事务边界；本模块不自己开事务、不自己拼 SQL。
- **HTTP 层**永远不在这里等模型：``create_experiment`` / ``resume`` / ``cancel`` 只做
  落库与状态推进。

三条容易写错的地方，这里用结构固定：

1. **分批只认固定公式**：``plan_batches`` 来自
   :mod:`nanobot.evaluation.memory.repository`（单一真相源），本模块只再导出；
   场景顺序永远是 ``scenario_id`` 升序，两个进程切出的批次必须一致。
2. **子 run 幂等**：计划里的 ``run_id`` 直接作为 ``runs.id``，幂等键是
   ``(experiment_id, candidate_id, batch_index)``。重启后只补**缺失**的子 run，
   已入队的一个都不重建（``Idempotency``/唯一键双保险）。
3. **预算未知不当 0**：请求前预留，结算失败/重试按全成本；某一维用量未知时宁可
   拒绝继续，也不把它当成「没花钱」。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nanobot.evaluation.memory import manifests as memory_manifests
from nanobot.evaluation.memory.models import (
    MemoryBudgets,
    MemoryCandidate,
    MemoryCapabilities,
    MemoryGradingPolicy,
    MemoryScenarioRef,
    MemoryTrack,
)
from nanobot.evaluation.memory.repository import plan_batches
from nanobot.evaluation.schemas import CaseRead, RunCreate, RunLimits
from nanobot.memory.clock import MemoryClock

if TYPE_CHECKING:  # pragma: no cover - 仅类型
    from nanobot.evaluation.memory.repository import MemoryRepository
    from nanobot.evaluation.repository import EvaluationRepository
    from nanobot.evaluation.scheduler import Scheduler

#: 契约 §2：默认单实验总尝试上限（核心 1080 必须由用户显式提高）。
DEFAULT_MAX_TRIALS = 200

#: 主要指标必须落在本轨道的判分阶段（跨轨道的主要指标没有可比口径）。
_PRIMARY_METRIC_STAGE: dict[str, tuple[str, ...]] = {
    "extraction": ("extraction",),
    "retrieval": ("retrieval", "injection"),
    "end_to_end": ("answer",),
}

#: 每个轨道判分时**必须**给出的指标（专项 case 的 ``memory_required_metrics``）。
REQUIRED_METRICS_BY_TRACK: dict[str, tuple[str, ...]] = {
    "extraction": ("extraction_recall",),
    "retrieval": ("evidence_recall_at_k",),
    "end_to_end": ("qa_accuracy", "grounded_memory_success"),
}

DEFAULT_PRIMARY_METRIC: dict[str, str] = {
    "extraction": "extraction_recall",
    "retrieval": "evidence_recall_at_k",
    "end_to_end": "qa_accuracy_scenario_macro",
}

#: 已停止的实验组：不再创建新批，也不再补建子 run。
_STOPPED_EXPERIMENT_STATUSES = frozenset(
    {"cancelling", "cancelled", "interrupted", "timed_out", "completed"}
)
_TERMINAL_EXPERIMENT_STATUSES = frozenset(
    {"cancelled", "interrupted", "timed_out", "completed"}
)


class MemoryBudgetExceeded(ValueError):  # noqa: N818 - 名称由错误码映射锁定
    """预算门禁拒绝（契约 §6 的 422 ``MEMORY_BUDGET_EXCEEDED``）。"""

    code = "MEMORY_BUDGET_EXCEEDED"

    def __init__(self, reason: str = "") -> None:
        super().__init__(f"{self.code}: {reason}" if reason else self.code)
        self.reason = reason


# =========================================================================== 分批


@dataclass(frozen=True)
class MemoryBatchPlan:
    """一个子 run 的计划（候选 × 批次）。"""

    candidate_id: str
    batch_index: int
    scenario_ids: tuple[str, ...]
    repetitions: int

    @property
    def planned_trials(self) -> int:
        return len(self.scenario_ids) * self.repetitions


def _candidate_ids(candidates: Sequence[Mapping[str, Any] | MemoryCandidate]) -> list[str]:
    return [
        item.id if isinstance(item, MemoryCandidate) else str(item["id"])
        for item in candidates
    ]


def build_batch_plan(
    candidates: Sequence[Mapping[str, Any] | MemoryCandidate],
    scenario_ids: Sequence[str],
    repetitions: int,
) -> list[MemoryBatchPlan]:
    """按契约公式切批：每个候选、每个批次一个子 run。"""
    if not 1 <= int(repetitions) <= 10:
        raise ValueError("MEMORY_REPETITIONS_INVALID")
    ordered = [str(item) for item in scenario_ids]
    batches = plan_batches(ordered, int(repetitions))
    return [
        MemoryBatchPlan(
            candidate_id=candidate_id,
            batch_index=batch_index,
            scenario_ids=tuple(batch),
            repetitions=int(repetitions),
        )
        for candidate_id in _candidate_ids(candidates)
        for batch_index, batch in enumerate(batches)
    ]


def trial_budget_of(plan: Sequence[MemoryBatchPlan]) -> int:
    return sum(item.planned_trials for item in plan)


def assert_group_trial_budget(
    plan: Sequence[MemoryBatchPlan], budgets: MemoryBudgets
) -> int:
    """**全组总量**预算检查（不能只看单批：每批 120 也可能整体 1080 超限）。"""
    total = trial_budget_of(plan)
    limit = budgets.max_trials if budgets.max_trials is not None else DEFAULT_MAX_TRIALS
    if total > limit:
        raise MemoryBudgetExceeded(f"TRIALS planned={total} limit={limit}")
    return total


def required_metrics_for_track(track: str) -> tuple[str, ...]:
    return REQUIRED_METRICS_BY_TRACK.get(track, REQUIRED_METRICS_BY_TRACK["end_to_end"])


def default_primary_metric(track: str) -> str:
    return DEFAULT_PRIMARY_METRIC.get(track, DEFAULT_PRIMARY_METRIC["end_to_end"])


#: ``schemas.RunLimits.timeout_s`` 的合法区间；预算模型允许到 86400，
#: 但冻结成 run 限制时必须落在这个区间内，否则创建实验直接 422。
_RUN_TIMEOUT_MIN_S = 5
_RUN_TIMEOUT_MAX_S = 900
_DEFAULT_TRIAL_TIMEOUT_S = 120


def _trial_timeout_s(value: Any) -> int:
    """把预算里的单次超时夹进 ``RunLimits`` 区间。"""
    if value is None or value == "":
        seconds = _DEFAULT_TRIAL_TIMEOUT_S
    else:
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            seconds = _DEFAULT_TRIAL_TIMEOUT_S
    return max(_RUN_TIMEOUT_MIN_S, min(seconds, _RUN_TIMEOUT_MAX_S))


def validate_primary_metric(track: str, primary_metric: str) -> None:
    """主要指标必须属于该轨道的判分阶段（跨轨道的主要指标没有可比口径）。"""
    from nanobot.evaluation.memory.metrics import METRIC_SPECS

    spec = METRIC_SPECS.get(primary_metric)
    stages = _PRIMARY_METRIC_STAGE.get(track)
    if spec is None or stages is None or spec.stage not in stages:
        raise ValueError(f"MEMORY_SCHEMA_INVALID: primary_metric={primary_metric}")


# =========================================================================== 预算


@dataclass(frozen=True)
class MemoryUnitPrice:
    """合成单价（测试注入；真实单价由服务端配置提供，绝不读 .env）。"""

    currency: str = "USD"
    input_per_million: float | None = None
    output_per_million: float | None = None
    request_price: float | None = None

    @property
    def complete(self) -> bool:
        return (
            self.input_per_million is not None
            and self.output_per_million is not None
        )


@dataclass(frozen=True)
class MemoryBudgetUsage:
    """一维用量；``None`` 表示**未知**（不是 0）。"""

    trials: int = 0
    model_requests: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_cost: float | None = None
    cost_reason: str | None = None


def estimate_cost(
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    model_requests: int | None,
    prices: MemoryUnitPrice | None,
) -> tuple[float | None, str | None]:
    """按单价估算金额；缺单价/缺用量一律返回 ``None`` + 原因（不当 0）。"""
    if prices is None or not prices.complete:
        return None, "UNIT_PRICE_REQUIRED"
    if input_tokens is None or output_tokens is None:
        return None, "TOKEN_USAGE_UNKNOWN"
    cost = (
        input_tokens / 1_000_000 * float(prices.input_per_million)
        + output_tokens / 1_000_000 * float(prices.output_per_million)
    )
    if model_requests is not None and prices.request_price is not None:
        cost += model_requests * prices.request_price
    return cost, None


@dataclass
class MemoryBudgetReservation:
    """一次预留：请求发出前占用，结算时才转成实际花费。"""

    key: str
    trials: int
    model_requests: int | None
    input_tokens: int | None
    output_tokens: int | None
    total_cost: float | None


class MemoryBudgetGuard:
    """实验组预算（进程内账本 + 从库回填的历史花费）。

    语义按契约 §2/§8.4：

    - **请求前预留**：并发跑的 trial 不会各自看到「还剩很多」然后一起超支；
    - **失败/重试全成本**：失败结算按预留金额记账，不退还；
    - **未知不当 0**：某一维没有可信用量时该维转成 ``None`` 并一直保持未知，
      ``remaining()`` 也不再给出该维的余额。
    """

    def __init__(
        self,
        budgets: MemoryBudgets,
        *,
        prices: MemoryUnitPrice | None = None,
        spent: MemoryBudgetUsage | None = None,
    ) -> None:
        self._budgets = budgets
        self._prices = prices
        initial = spent or MemoryBudgetUsage(
            trials=0,
            model_requests=0,
            input_tokens=0,
            output_tokens=0,
            total_cost=0.0,
        )
        self._trials = initial.trials
        self._requests = initial.model_requests
        self._input_tokens = initial.input_tokens
        self._output_tokens = initial.output_tokens
        # 还没有任何花费时金额是 0（确定值），不是「未知」；只有回填失败才留 None。
        self._cost = (
            initial.total_cost
            if initial.total_cost is not None or initial.cost_reason is not None
            else 0.0
        )
        self._cost_reason = initial.cost_reason
        self._reserved: dict[str, MemoryBudgetReservation] = {}

    # ------------------------------------------------------------------ 读数

    @property
    def budgets(self) -> MemoryBudgets:
        return self._budgets

    @property
    def spent(self) -> MemoryBudgetUsage:
        return MemoryBudgetUsage(
            trials=self._trials,
            model_requests=self._requests,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            total_cost=self._cost,
            cost_reason=self._cost_reason,
        )

    def remaining(self) -> dict[str, float | int | None]:
        """各维余额；未知维返回 ``None``（表示「无法确认还有多少」）。"""
        pending_requests = self._reserved_total("model_requests")
        pending_input = self._reserved_total("input_tokens")
        pending_output = self._reserved_total("output_tokens")
        pending_cost = self._reserved_total("total_cost")
        pending_trials = sum(item.trials for item in self._reserved.values())
        budgets = self._budgets

        def _left(limit: int | float | None, used: int | float | None, pending: int | float | None) -> float | None:
            if limit is None:
                return None
            if used is None or pending is None:
                return None
            return float(limit) - float(used) - float(pending)

        return {
            "max_trials": _left(
                budgets.max_trials if budgets.max_trials is not None else DEFAULT_MAX_TRIALS,
                self._trials,
                pending_trials,
            ),
            "max_model_requests": _left(
                budgets.max_model_requests, self._requests, pending_requests
            ),
            "max_input_tokens": _left(
                budgets.max_input_tokens, self._input_tokens, pending_input
            ),
            "max_output_tokens": _left(
                budgets.max_output_tokens, self._output_tokens, pending_output
            ),
            "max_total_cost": _left(budgets.max_total_cost, self._cost, pending_cost),
        }

    # ------------------------------------------------------------------ 预留

    def reserve(
        self,
        *,
        key: str,
        model_requests: int | None,
        input_tokens: int | None,
        output_tokens: int | None,
        trials: int = 1,
    ) -> MemoryBudgetReservation:
        """请求前预留；任一维会越界就拒绝（未知也拒绝，不当成 0）。"""
        if key in self._reserved:
            raise MemoryBudgetExceeded(f"RESERVATION_DUPLICATED key={key}")
        budgets = self._budgets
        remaining = self.remaining()
        if remaining["max_trials"] is not None and remaining["max_trials"] < trials:
            raise MemoryBudgetExceeded(
                f"TRIALS remaining={remaining['max_trials']} need={trials}"
            )
        self._check_dimension(
            "max_model_requests", budgets.max_model_requests, self._requests,
            model_requests, "MEMORY_REQUEST_ESTIMATE_REQUIRED",
        )
        self._check_dimension(
            "max_input_tokens", budgets.max_input_tokens, self._input_tokens,
            input_tokens, "MEMORY_TOKEN_ESTIMATE_REQUIRED",
        )
        self._check_dimension(
            "max_output_tokens", budgets.max_output_tokens, self._output_tokens,
            output_tokens, "MEMORY_TOKEN_ESTIMATE_REQUIRED",
        )
        cost: float | None = None
        if budgets.max_total_cost is not None:
            cost, reason = estimate_cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model_requests=model_requests,
                prices=self._prices,
            )
            if cost is None:
                raise MemoryBudgetExceeded(
                    f"CURRENCY_BUDGET_BLOCKED reason={reason}"
                )
            self._check_amount(
                "max_total_cost", budgets.max_total_cost, self._cost, cost,
                "UNIT_PRICE_REQUIRED",
            )
        reservation = MemoryBudgetReservation(
            key=key,
            trials=trials,
            model_requests=model_requests,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_cost=cost,
        )
        self._reserved[key] = reservation
        return reservation

    def settle(
        self,
        reservation: MemoryBudgetReservation,
        usage: MemoryBudgetUsage | None = None,
    ) -> MemoryBudgetUsage:
        """结算：实际用量优先，未知则按预留**全成本**记账（不退还、不当 0）。"""
        self._reserved.pop(reservation.key, None)
        actual = usage or MemoryBudgetUsage()
        self._trials += reservation.trials
        self._requests = _accumulate(
            self._requests,
            actual.model_requests if actual.model_requests is not None
            else reservation.model_requests,
        )
        self._input_tokens = _accumulate(
            self._input_tokens,
            actual.input_tokens if actual.input_tokens is not None
            else reservation.input_tokens,
        )
        self._output_tokens = _accumulate(
            self._output_tokens,
            actual.output_tokens if actual.output_tokens is not None
            else reservation.output_tokens,
        )
        if actual.total_cost is not None:
            self._cost = _accumulate(self._cost, actual.total_cost)
            self._cost_reason = None
            return self.spent
        if reservation.total_cost is not None and not self._cost_reason:
            self._cost = _accumulate(self._cost, reservation.total_cost)
            return self.spent
        if self._budgets.max_total_cost is not None and self._cost_reason is None:
            # 该结算金额算不出来：保持未知，不让「0 元」成为默认结论。
            self._cost = None
            self._cost_reason = "SETTLEMENT_COST_UNKNOWN"
        return self.spent

    def charge_failed(self, reservation: MemoryBudgetReservation) -> MemoryBudgetUsage:
        """失败/重试按预留全成本结算：物理请求可能已经发生，不能退款。"""
        return self.settle(reservation, None)

    def release(self, reservation: MemoryBudgetReservation) -> None:
        """从未发出任何请求的预留可以撤销（仅限确认没有物理请求的路径）。"""
        self._reserved.pop(reservation.key, None)

    # ------------------------------------------------------------------ 内部

    def _reserved_total(self, attribute: str) -> int | float | None:
        total: int | float = 0
        for item in self._reserved.values():
            value = getattr(item, attribute)
            if value is None:
                return None
            total += value
        return total

    def _check_dimension(
        self,
        name: str,
        limit: int | None,
        used: int | None,
        planned: int | None,
        unknown_reason: str,
    ) -> None:
        if limit is None:
            return
        if planned is None:
            raise MemoryBudgetExceeded(f"{unknown_reason} dimension={name}")
        self._check_amount(name, limit, used, planned, unknown_reason)

    def _check_amount(
        self,
        name: str,
        limit: int | float,
        used: int | float | None,
        planned: int | float,
        unknown_reason: str,
    ) -> None:
        pending = self._reserved_total(
            {"max_model_requests": "model_requests",
             "max_input_tokens": "input_tokens",
             "max_output_tokens": "output_tokens",
             "max_total_cost": "total_cost"}[name]
        )
        if used is None or pending is None:
            raise MemoryBudgetExceeded(f"{unknown_reason} dimension={name}")
        if used + pending + planned > limit:
            raise MemoryBudgetExceeded(
                f"BUDGET_EXCEEDED dimension={name} used={used} reserved={pending}"
                f" planned={planned} limit={limit}"
            )


def _accumulate(current: int | float | None, delta: int | float | None) -> int | float | None:
    if current is None or delta is None:
        return None
    return current + delta


# =========================================================================== 可评分性


def _attempt_metrics(attempt: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """摊平一次 attempt 的全部指标（trial 级 + 逐 probe 级）。"""
    result = attempt.get("result")
    if not isinstance(result, Mapping):
        return []
    memory = result.get("memory")
    if not isinstance(memory, Mapping) or memory.get("status") == "grading_error":
        return []
    graded = memory.get("metrics")
    if not isinstance(graded, Mapping):
        return []
    collected: list[Mapping[str, Any]] = []
    trial_metrics = graded.get("metrics")
    if isinstance(trial_metrics, list):
        collected.extend(item for item in trial_metrics if isinstance(item, Mapping))
    for probe in graded.get("probe_results") or []:
        if isinstance(probe, Mapping) and isinstance(probe.get("metrics"), list):
            collected.extend(
                item for item in probe["metrics"] if isinstance(item, Mapping)
            )
    return collected


def measurement_status_of(
    attempts: Sequence[Mapping[str, Any]], *, planned_trials: int
) -> str:
    """契约 §8.4：只有全部预定 trial 的必需指标都可评分才是 ``complete``。"""
    if planned_trials <= 0 or len(attempts) < planned_trials:
        return "incomplete"
    for attempt in attempts:
        if attempt.get("execution_status") != "succeeded":
            return "incomplete"
        if attempt.get("grading_status") not in ("completed", "judge_error"):
            return "incomplete"
        snapshot = attempt.get("case_snapshot") or {}
        required = [
            str(item)
            for item in (snapshot.get("memory_required_metrics") or [])
            if str(item)
        ]
        scored = {
            str(item.get("id"))
            for item in _attempt_metrics(attempt)
            if item.get("status") == "scored"
        }
        if not required or not set(required) <= scored:
            return "incomplete"
    return "complete"


# =========================================================================== 协调器


@dataclass
class _ExperimentContext:
    """一次协调调用需要的冻结上下文。"""

    suite_id: str
    track: MemoryTrack
    split: str
    repetitions: int
    model_mode: str
    candidates: list[MemoryCandidate]
    budgets: MemoryBudgets
    primary_metric: str
    policy: MemoryGradingPolicy
    judge_profile_id: str | None
    trial_estimate: dict[str, Any] = field(default_factory=dict)


class MemoryExperimentCoordinator:
    """实验组协调器：分批、冻结、入队、恢复、取消与聚合。

    它**不执行**任何 trial（那是调度器的职责），也不在 HTTP 请求里 await 模型。
    """

    def __init__(
        self,
        *,
        repository: "EvaluationRepository",
        memory_repository: "MemoryRepository",
        clock: MemoryClock | None = None,
        capabilities: MemoryCapabilities | None = None,
        prices: MemoryUnitPrice | None = None,
        source_state_provider: Callable[[], Mapping[str, Any]] | None = None,
        profile_resolver: Callable[[str], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self._repository = repository
        self._memory = memory_repository
        self._clock = clock or MemoryClock()
        self._capabilities = capabilities or MemoryCapabilities()
        self._prices = prices
        self._source_state_provider = (
            source_state_provider or memory_manifests.source_state_or_unknown
        )
        self._profile_resolver = profile_resolver

    # ------------------------------------------------------------------ 对外

    def experiment_status(self, experiment_id: str) -> str | None:
        """给调度器做组闸：实验已停止就不再领取它的子 run。"""
        try:
            return str(
                self._memory.get_experiment(experiment_id)["experiment"]["status"]
            )
        except Exception:  # noqa: BLE001 - 读不到不能让调度器停摆
            return None

    def create_experiment(
        self, request: Mapping[str, Any], *, enqueue: bool = True
    ) -> dict[str, Any]:
        """冻结计划并入队全部子 run；同一 ``plan_key`` 重放不重复创建。"""
        context = self._context_of(request)
        revision_id = str(request["dataset_revision_id"])
        dataset = self._memory.get_dataset_revision(revision_id)
        bindings = self._bindings(revision_id, context.split, request.get("scenario_ids"))
        if not bindings:
            raise ValueError("MEMORY_SCENARIOS_EMPTY")
        bindings = [self._ensure_case(context, revision_id, item) for item in bindings]
        scenario_ids = [str(item["scenario_id"]) for item in bindings]

        plan = build_batch_plan(context.candidates, scenario_ids, context.repetitions)
        assert_group_trial_budget(plan, context.budgets)
        validate_primary_metric(context.track, context.primary_metric)
        manifest = self._build_manifest(context, revision_id, dataset, bindings)

        payload = {
            "suite_id": str(request["suite_id"]),
            "dataset_revision_id": revision_id,
            "split": context.split,
            "track": context.track,
            "candidates": [item.model_dump(mode="json") for item in context.candidates],
            "repetitions": context.repetitions,
            "model_mode": context.model_mode,
            "judge_profile_id": context.judge_profile_id,
            "budgets": context.budgets.model_dump(mode="json"),
            "primary_metric": context.primary_metric,
            "policy": context.policy.model_dump(mode="json"),
            "scenario_ids": scenario_ids,
            "plan_key": request.get("plan_key"),
        }
        created = self._memory.create_experiment_plan(payload)
        experiment_id = str(created["experiment"]["id"])
        self._repository.attach_memory_manifest(experiment_id, manifest)
        if enqueue:
            self.enqueue(experiment_id)
        return self._memory.get_experiment(experiment_id)

    def enqueue(self, experiment_id: str) -> dict[str, Any]:
        """把计划里**尚未入队**的子 run 建成真实 run；重复调用不产生新 run。"""
        plan = self._memory.get_experiment(experiment_id)
        experiment = plan["experiment"]
        if str(experiment["status"]) in _STOPPED_EXPERIMENT_STATUSES:
            return plan
        self._repository.set_memory_experiment_status(experiment_id, "queued")
        for child in plan["child_runs"]:
            if self._repository.run_status(str(child["run_id"])) is not None:
                continue
            self._enqueue_child(experiment, child)
        return self.refresh(experiment_id)

    def resume(self, experiment_id: str) -> dict[str, Any]:
        """重启恢复：只补缺失的子 run，已入队/已取消的一律不动。"""
        plan = self._memory.get_experiment(experiment_id)
        experiment = plan["experiment"]
        status = str(experiment["status"])
        if status in _TERMINAL_EXPERIMENT_STATUSES:
            return self.refresh(experiment_id)
        if status == "cancelling":
            # 取消中的实验不补建任何子 run：先停住剩余批次再收口成 cancelled。
            # 重启后拿不到原取消原因（用户/超时），按契约终态取更保守的 cancelled。
            self._cancel_open_children(experiment_id)
            self._repository.set_memory_experiment_status(
                experiment_id, "cancelled", measurement_status="incomplete"
            )
            return self.refresh(experiment_id)
        return self.enqueue(experiment_id)

    def resume_all(self, *, suite_id: str | None = None) -> list[dict[str, Any]]:
        """服务启动时调用：把仍处于 queued/running 的实验补齐并聚合。"""
        resumed = []
        for row in self._repository.list_memory_experiments(
            suite_id=suite_id, statuses=("queued", "running", "cancelling")
        ):
            resumed.append(self.resume(str(row["id"])))
        return resumed

    async def cancel(
        self,
        experiment_id: str,
        *,
        scheduler: "Scheduler | None" = None,
        reason: str = "user",
    ) -> dict[str, Any]:
        """取消实验组：先停止新批，再回收在飞进程，最后才落终态。"""
        self._repository.set_memory_experiment_status(experiment_id, "cancelling")
        plan = self._memory.get_experiment(experiment_id)
        for child in plan["child_runs"]:
            run_id = str(child["run_id"])
            status = self._repository.run_status(run_id)
            if status is None or status in _TERMINAL_EXPERIMENT_STATUSES:
                continue
            if scheduler is not None:
                try:
                    await scheduler.cancel_run(run_id, reason=reason)
                except Exception:  # noqa: BLE001 - 已终态/正在收口都继续处理其余子 run
                    continue
            else:
                self._cancel_queued_run(run_id, reason=reason)
        final = "timed_out" if reason == "timeout" else "cancelled"
        self._repository.set_memory_experiment_status(
            experiment_id, final, measurement_status="incomplete"
        )
        return self.refresh(experiment_id)

    def refresh(self, experiment_id: str) -> dict[str, Any]:
        """聚合子 run 状态与可评分性：状态是投影，执行状态以 ``runs`` 为准。"""
        plan = self._memory.get_experiment(experiment_id)
        experiment = plan["experiment"]
        current = str(experiment["status"])
        attempts: list[Mapping[str, Any]] = []
        child_statuses: list[str] = []
        for child in plan["child_runs"]:
            run_id = str(child["run_id"])
            status = self._repository.run_status(run_id)
            if status is None:
                child_statuses.append("queued")
                continue
            child_statuses.append(status)
            self._repository.update_memory_child_run_status(
                experiment_id, run_id, status
            )
            attempts.extend(
                self._repository.list_run_attempts(run_id, include_snapshot=True)
            )
        measurement = measurement_status_of(
            attempts, planned_trials=int(experiment["planned_trials"])
        )
        status = self._derive_status(current, child_statuses)
        self._repository.set_memory_experiment_status(
            experiment_id, status, measurement_status=measurement
        )
        return self._memory.get_experiment(experiment_id)

    def on_run_settled(self, run_id: str) -> None:
        """调度器回调：子 run 终结后聚合所属实验（找不到宿主就静默）。"""
        experiment_id = self._repository.memory_experiment_of_run(run_id)
        if experiment_id is None:
            return
        self.refresh(experiment_id)

    def spent_usage(self, experiment_id: str) -> MemoryBudgetUsage:
        """重启后从库回填已发生的花费（预算账本不能因为重启就归零）。"""
        try:
            plan = self._memory.get_experiment(experiment_id)
        except Exception:  # noqa: BLE001 - 读不到就按未知处理
            return MemoryBudgetUsage()
        trials = 0
        requests: int | None = 0
        input_tokens: int | None = 0
        output_tokens: int | None = 0
        for child in plan["child_runs"]:
            try:
                attempts = self._repository.list_run_attempts(str(child["run_id"]))
            except Exception:  # noqa: BLE001 - 子 run 可能还没入队
                continue
            for attempt in attempts:
                if attempt.get("execution_status") in ("queued", "running"):
                    continue
                trials += 1
                result = attempt.get("result") or {}
                usage = (result.get("usage") or {}).get("agent") or {}
                requests = _accumulate(requests, _int_or_none(usage.get("physical_requests")))
                input_tokens = _accumulate(input_tokens, _int_or_none(usage.get("input_tokens")))
                output_tokens = _accumulate(output_tokens, _int_or_none(usage.get("output_tokens")))
        cost, cost_reason = estimate_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_requests=requests,
            prices=self._prices,
        )
        return MemoryBudgetUsage(
            trials=trials,
            model_requests=None if requests is None else int(requests),
            input_tokens=None if input_tokens is None else int(input_tokens),
            output_tokens=None if output_tokens is None else int(output_tokens),
            total_cost=cost,
            cost_reason=cost_reason,
        )

    # ------------------------------------------------------------------ 计划

    def _context_of(self, request: Mapping[str, Any]) -> _ExperimentContext:
        candidates = [
            item if isinstance(item, MemoryCandidate) else MemoryCandidate.model_validate(item)
            for item in request.get("candidates") or []
        ]
        if not 1 <= len(candidates) <= 3:
            raise ValueError("MEMORY_CANDIDATES_INVALID")
        if len({item.id for item in candidates}) != len(candidates):
            raise ValueError("MEMORY_CANDIDATES_DUPLICATED")
        repetitions = int(request.get("repetitions") or 1)
        if not 1 <= repetitions <= 10:
            raise ValueError("MEMORY_REPETITIONS_INVALID")
        track = str(request["track"])
        if track not in ("extraction", "retrieval", "end_to_end"):
            raise ValueError(f"MEMORY_TRACK_UNSUPPORTED: {track}")
        return _ExperimentContext(
            suite_id=str(request["suite_id"]),
            track=track,  # type: ignore[arg-type]
            split=str(request["split"]),
            repetitions=repetitions,
            model_mode=str(request.get("model_mode") or "real"),
            candidates=candidates,
            budgets=MemoryBudgets.model_validate(request.get("budgets") or {}),
            primary_metric=str(
                request.get("primary_metric") or default_primary_metric(track)
            ),
            policy=MemoryGradingPolicy.model_validate(request["policy"]),
            judge_profile_id=request.get("judge_profile_id"),
            trial_estimate=dict(request.get("trial_estimate") or {}),
        )

    def _bindings(
        self, revision_id: str, split: str, requested: Sequence[str] | None
    ) -> list[dict[str, Any]]:
        bindings = self._repository.memory_scenario_bindings(
            revision_id, split=split, scenario_ids=requested
        )
        if requested:
            found = {str(item["scenario_id"]) for item in bindings}
            missing = sorted({str(item) for item in requested} - found)
            if missing:
                raise LookupError(f"MEMORY_SCENARIO_NOT_FOUND {missing[0]}")
        return bindings

    def _ensure_case(
        self, context: _ExperimentContext, revision_id: str, binding: Mapping[str, Any]
    ) -> dict[str, Any]:
        """场景 → case 的映射：缺失就建，已绑定就沿用（revision 不再改）。"""
        case_id = binding.get("case_id")
        case_revision = binding.get("case_revision")
        if case_id and case_revision is not None:
            try:
                existing = self._repository.get_case(str(case_id))
            except Exception:  # noqa: BLE001 - 用例被删/缺失时补建一份新的
                existing = None
            if existing is not None and not existing.get("is_deleted"):
                # 场景行只记一份 ``case_id``，但 case 里还冻结着**轨道**与必需指标：
                # 复用别的轨道建的 case 会把抽取用例当成端到端跑（或反之），
                # 而且这种错配在报告里只表现为「分数不一样」。轨道不同就另建一份。
                if str(existing.get("memory_track") or "end_to_end") == context.track:
                    return dict(binding)
        saved = self._repository.save_case(
            self._memory_case_payload(context, revision_id, binding)
        )
        return {
            **dict(binding),
            "case_id": str(saved["id"]),
            "case_revision": int(saved["revision"]),
        }

    def _memory_case_payload(
        self, context: _ExperimentContext, revision_id: str, binding: Mapping[str, Any]
    ) -> dict[str, Any]:
        """专项 case：turns/assertions 为空，只存 ref 与必需的记忆指标判据。"""
        limits = self._run_limits(context)
        scenario_id = str(binding["scenario_id"])
        return {
            "name": f"记忆专项 {context.track} {scenario_id}",
            "description": "候选项冻结的实验子 run 用例；场景正文按 ref 解析。",
            "tags": ["memory", context.track],
            "enabled": True,
            "suite_id": context.suite_id,
            "base_revision": None,
            "turns": [],
            "fixtures": [],
            "memory_seed": [],
            "clock_iso": None,
            "tool_fixtures": [],
            "execution": {
                "tool_mode": "fixture",
                "allowed_tools": ["read_file", "write_file", "edit_file", "list_dir"],
                "max_iterations": limits.max_iterations,
                "timeout_s": limits.timeout_s,
            },
            "assertions": [],
            "rubric": None,
            "script": [],
            "artifact_paths": [],
            "memory_scenario_ref": MemoryScenarioRef(
                dataset_revision_id=revision_id,
                scenario_id=scenario_id,
                revision=int(binding["revision"]),
            ).model_dump(mode="json"),
            "memory_track": context.track,
            "memory_required_metrics": list(required_metrics_for_track(context.track)),
        }

    # ------------------------------------------------------------------ 冻结

    def _build_manifest(
        self,
        context: _ExperimentContext,
        revision_id: str,
        dataset: Mapping[str, Any],
        bindings: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        dataset_view = {**dict(dataset), "split": context.split}
        return memory_manifests.build_memory_manifest(
            dataset=dataset_view,
            scenarios=bindings,
            policy=context.policy,
            track=context.track,
            repetitions=context.repetitions,
            primary_metric=context.primary_metric,
            model_mode=context.model_mode,
            candidates=context.candidates,
            capabilities=self._capabilities,
            budgets=context.budgets,
            clock=self._clock,
            judge_profile_id=context.judge_profile_id,
            models=self._models(context),
            limits=self._run_limits(context).model_dump(mode="json"),
            source_state=self._source_state_provider(),
            trial_estimate=context.trial_estimate,
        )

    def _models(self, context: _ExperimentContext) -> dict[str, Any]:
        """冻结真实模型：解析不到就显式记 unresolved，不虚构一个模型名。"""
        agent_profiles: dict[str, Any] = {}
        for candidate in context.candidates:
            for profile_id in (
                candidate.agent_profile_id,
                candidate.extractor_profile_id,
            ):
                if not profile_id or profile_id in agent_profiles:
                    continue
                resolved = (
                    self._profile_resolver(profile_id)
                    if self._profile_resolver is not None
                    else None
                )
                agent_profiles[profile_id] = (
                    dict(resolved)
                    if resolved is not None
                    else {"id": profile_id, "resolved": False}
                )
        judge = None
        if context.judge_profile_id and self._profile_resolver is not None:
            resolved = self._profile_resolver(context.judge_profile_id)
            judge = dict(resolved) if resolved is not None else {
                "id": context.judge_profile_id, "resolved": False
            }
        return {"agent_profiles": agent_profiles, "judge_profile": judge}

    def _run_limits(self, context: _ExperimentContext) -> RunLimits:
        concurrency = context.budgets.concurrency or 1
        return RunLimits(
            concurrency=max(1, min(int(concurrency), 3)),
            max_iterations=12,
            timeout_s=_trial_timeout_s(context.budgets.per_trial_timeout_s),
        )

    # ------------------------------------------------------------------ 入队

    def _enqueue_child(
        self, experiment: Mapping[str, Any], child: Mapping[str, Any]
    ) -> None:
        experiment_id = str(experiment["id"])
        candidates = {
            str(item["id"]): item for item in experiment.get("candidates") or []
        }
        candidate = candidates.get(str(child["candidate_id"]))
        if candidate is None:
            raise ValueError(f"MEMORY_CANDIDATE_UNKNOWN {child['candidate_id']}")
        manifest = experiment.get("manifest") or {}
        by_scenario = {
            str(item["scenario_id"]): item
            for item in ((manifest.get("dataset") or {}).get("scenarios") or [])
        }
        frozen_cases = []
        case_ids: list[str] = []
        for scenario_id in child.get("scenario_ids") or []:
            binding = by_scenario.get(str(scenario_id))
            if binding is None:
                raise ValueError(f"MEMORY_SCENARIO_NOT_FOUND {scenario_id}")
            frozen_cases.append(self._frozen_case(binding))
            case_ids.append(str(binding["case_id"]))
        limits = RunLimits(
            concurrency=max(1, min(int((experiment.get("budgets") or {}).get("concurrency") or 1), 3)),
            max_iterations=12,
            timeout_s=_trial_timeout_s(
                (experiment.get("budgets") or {}).get("per_trial_timeout_s")
            ),
        )
        budgets = dict(experiment.get("budgets") or {})
        request = RunCreate.model_validate(
            {
                "suite_id": str(experiment["suite_id"]),
                "case_ids": case_ids,
                "agent_profile_id": str(candidate["agent_profile_id"]),
                "judge_profile_id": experiment.get("judge_profile_id"),
                "limits": limits.model_dump(mode="json"),
                "repetitions": int(experiment["repetitions"]),
                "model_mode": str(experiment.get("model_mode") or "real"),
                "run_timeout_s": (
                    int(budgets["group_timeout_s"])
                    if budgets.get("group_timeout_s")
                    else None
                ),
            }
        )
        self._repository.create_run(
            scope=experiment_id,
            idempotency_key=f"{child['candidate_id']}:{child['batch_index']}",
            request=request,
            agent=self._profile_payload(str(candidate["agent_profile_id"])),
            judge=(
                None
                if not experiment.get("judge_profile_id")
                else self._profile_payload(str(experiment["judge_profile_id"]))
            ),
            rules_version="rules-v1",
            grader_version="judge-v1",
            frozen_cases=frozen_cases,
            effective_limits={
                "max_iterations": limits.max_iterations,
                "timeout_s": limits.timeout_s,
            },
            run_id=str(child["run_id"]),
            snapshot_extra={
                "memory_experiment": {
                    "experiment_id": experiment_id,
                    "candidate_id": str(child["candidate_id"]),
                    # 契约 §1：执行载荷必须带**生效候选**。快照里冻结完整候选定义，
                    # 子进程才能把 no_persistent_memory / 记忆设置落到真实执行端。
                    "candidate": dict(candidate),
                    "batch_index": int(child["batch_index"]),
                    "concurrency": max(
                        1, min(int(budgets.get("concurrency") or 1), 3)
                    ),
                    "budgets": budgets,
                    "trial_estimate": dict(
                        (manifest.get("environment") or {}).get("trial_estimate") or {}
                    ),
                }
            },
        )

    def _frozen_case(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        case_id = str(binding["case_id"])
        revision = int(binding["case_revision"])
        definition = self._repository.get_case_revision(case_id, revision)
        read = CaseRead.model_validate(definition)
        return {
            "case_id": case_id,
            "case_revision": revision,
            "case_snapshot": read.model_dump(
                mode="json",
                exclude={"id", "revision", "created_at", "updated_at", "is_deleted"},
            ),
        }

    def _profile_payload(self, profile_id: str) -> dict[str, Any]:
        resolved = (
            self._profile_resolver(profile_id)
            if self._profile_resolver is not None
            else None
        )
        return dict(resolved) if resolved is not None else {"id": profile_id}

    # ------------------------------------------------------------------ 取消/聚合

    def _cancel_queued_run(self, run_id: str, *, reason: str) -> None:
        closed = "timed_out" if reason == "timeout" else "cancelled"
        try:
            self._repository.cancel_run(run_id, reason=reason)
            self._repository.finalize_cancel(run_id, status=closed)
        except Exception:  # noqa: BLE001 - 已终态/收口中都继续处理其余子 run
            return

    def _cancel_open_children(self, experiment_id: str) -> None:
        plan = self._memory.get_experiment(experiment_id)
        for child in plan["child_runs"]:
            run_id = str(child["run_id"])
            status = self._repository.run_status(run_id)
            if status is None or status in _TERMINAL_EXPERIMENT_STATUSES:
                continue
            self._cancel_queued_run(run_id, reason="user")

    def _derive_status(self, current: str, child_statuses: Sequence[str]) -> str:
        """状态投影：取消中的实验不会因为子 run 都停了就被改成 completed。"""
        if current in _TERMINAL_EXPERIMENT_STATUSES:
            return current
        if current == "cancelling":
            # 终态由 cancel() 在回收完成后显式落库，聚合阶段只保持 cancelling。
            return "cancelling"
        if not child_statuses:
            return "queued"
        if all(status == "completed" for status in child_statuses):
            return "completed"
        for terminal in ("timed_out", "interrupted", "cancelled"):
            if any(status == terminal for status in child_statuses):
                return terminal
        if any(status == "running" for status in child_statuses):
            return "running"
        return "queued"


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


__all__ = [
    "DEFAULT_MAX_TRIALS",
    "REQUIRED_METRICS_BY_TRACK",
    "MemoryBatchPlan",
    "MemoryBudgetExceeded",
    "MemoryBudgetGuard",
    "MemoryBudgetReservation",
    "MemoryBudgetUsage",
    "MemoryExperimentCoordinator",
    "MemoryUnitPrice",
    "assert_group_trial_budget",
    "build_batch_plan",
    "default_primary_metric",
    "estimate_cost",
    "measurement_status_of",
    "plan_batches",
    "required_metrics_for_track",
    "trial_budget_of",
    "validate_primary_metric",
]
