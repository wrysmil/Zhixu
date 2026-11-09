"""知识评测执行器（契约 §3.3 / §7）。

流程：逐查询按各配置检索 → 并池去重 → 分标注来源判定 → 逐题打分 → 分组聚合 → 落库。

六条硬约束（与 ``metrics.py`` / ``judge.py`` 对齐，改动前先读契约）：

1. **检索四态 → 评测可评性**（:func:`map_search_reason`）：``no_candidates``
   是「真的没搜到」→ 照常打分（该配置记 0 分）；``no_scope`` / ``filtered`` /
   ``empty_query_tokens`` 是配置或查询问题 → ``not_assessable``。
2. **检索失败 ≠ 空结果**：检索抛异常 → 该题 ``failed``，**不进聚合、不计 0**。
   ``failed`` / ``not_assessable`` / 「记 0 分」三者必须区分。
3. **标注来源分组，不混算**（:func:`split_by_label_source` 与
   ``aggregate_metrics``）：``expect`` 组走关键词确定性判定（零 LLM 成本），
   ``judge`` 组每查询一次批量 LLM 调用。
4. **预算触顶 → 运行置 ``failed``、不产出半截指标**：查询数超 ``max_queries``
   或判定调用数超 ``max_judge_calls`` 时收口，metrics 传空。
5. **取消是协作式**：在查询边界检查取消位；已完成的逐题结果保留。
6. **降级必须落库**：任一配置的 ``degraded`` 非空时把降级原因写进运行的
   ``pool`` 字段——**降级运行与正常运行的指标不得直接对比**，徽标依赖它。

判分材料是**数据不是指令**——守卫在 ``judge.py`` 里实现，本模块不重复，
只负责把候选块交给它。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any, cast

from nanobot.evaluation.knowledge.judge import (
    JudgeCandidate,
    JudgeProviderView,
    judge_relevance,
)
from nanobot.evaluation.knowledge.metrics import (
    METRIC_IDS,
    UNSCORED_RATE,
    KnowledgeCaseScore,
    aggregate_metrics,
    case_score,
)
from nanobot.knowledge.retriever import RetrieverSettings

logger = logging.getLogger(__name__)

#: 检索四态里「配置或查询问题」的三种——它们不可评，**不补 0**。
REASON_NOT_ASSESSABLE: frozenset[str] = frozenset(
    {"no_scope", "filtered", "empty_query_tokens"}
)

#: 用例未显式给出工作区时的兜底作用域。
DEFAULT_WORKSPACE_IDS: tuple[str, ...] = ("default",)

#: 预算耗尽时写进 ``error`` 的错误码前缀。
CODE_BUDGET_EXCEEDED = "KNOWLEDGE_BUDGET_EXCEEDED"

#: ``RetrieverSettings`` 的字段名——检索参数白名单到检索器参数的映射范围。
_RETRIEVER_SETTING_FIELDS: tuple[str, ...] = tuple(
    item.name for item in fields(RetrieverSettings)
)

#: 逐题指标 id——``unscored_rate`` 是聚合级指标，不进单题载荷。
_CASE_METRIC_IDS: tuple[str, ...] = tuple(
    metric_id for metric_id in METRIC_IDS if metric_id != UNSCORED_RATE
)


# --------------------------------------------------------------------------- 纯函数


def build_pool(hits_by_config: Mapping[str, Sequence[Any]]) -> list[JudgeCandidate]:
    """把各配置的候选按 ``chunk_id`` 去重合并成池。

    重复 ``chunk_id`` **保留首次出现的正文**——同一块在多个配置下召回时正文
    必然一致，保留首条可避免后到配置覆盖它。
    """
    pool: dict[str, JudgeCandidate] = {}
    for hits in hits_by_config.values():
        if not hits:
            continue
        for hit in hits:
            chunk_id = _text(hit, "chunk_id")
            if not chunk_id or chunk_id in pool:
                continue
            pool[chunk_id] = JudgeCandidate(
                chunk_id=chunk_id,
                content=_text(hit, "content"),
            )
    return list(pool.values())


def map_search_reason(reason: str | None, degraded: Sequence[str]) -> str:
    """检索四态 → 评测可评性。

    - ``no_candidates`` → ``scored``：真的没搜到，是有效结果，记 0 分。
    - ``no_scope`` / ``filtered`` / ``empty_query_tokens`` → ``not_assessable``：
      配置或查询问题，不记 0。
    - ``None``（有命中）→ ``scored``。

    ``degraded`` 不改变可评性——降级只影响「运行是否可比」，不影响单题证据；
    它由运行级降级标记单独承载。
    """
    if reason in REASON_NOT_ASSESSABLE:
        return "not_assessable"
    return "scored"


def split_by_label_source(cases: Sequence[Any]) -> dict[str, list[Any]]:
    """按标注来源分组——聚合不跨组混算。"""
    groups: dict[str, list[Any]] = {}
    for case in cases:
        source = str(_field(case, "label_source", "judge") or "judge")
        groups.setdefault(source, []).append(case)
    return groups


# --------------------------------------------------------------------------- 内部工具


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """同时支持对象属性与字典取值；``None`` 一律回落到默认值。"""
    if isinstance(obj, Mapping):
        mapping = cast("Mapping[str, Any]", obj)
        value: Any = mapping.get(name, default)
    else:
        value = getattr(obj, name, default)
    return default if value is None else value


def _text(obj: Any, name: str, default: str = "") -> str:
    """读字符串字段（字典或对象属性）；类型不符时回落默认值。"""
    value = _field(obj, name, default)
    return value if isinstance(value, str) else default


def _float(obj: Any, name: str, default: float = 0.0) -> float:
    """读数值字段（字典或对象属性）；类型不符时回落默认值。"""
    value = _field(obj, name, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _overrides_dict(overrides: Any) -> dict[str, Any]:
    """检索参数覆写 → 普通字典（模型与字典都吃）。"""
    if isinstance(overrides, Mapping):
        mapping = cast("Mapping[str, Any]", overrides)
        return {str(key): value for key, value in mapping.items()}
    dump = getattr(overrides, "model_dump", None)
    if callable(dump):
        dumped = dump(mode="json")
        if isinstance(dumped, Mapping):
            mapping = cast("Mapping[str, Any]", dumped)
            return {str(key): value for key, value in mapping.items()}
    return {}


def _retriever_settings(overrides: Mapping[str, Any]) -> RetrieverSettings:
    """检索参数白名单 → ``RetrieverSettings``；未声明的字段用产品默认值。"""
    defaults = RetrieverSettings()
    kwargs: dict[str, Any] = {}
    for name in _RETRIEVER_SETTING_FIELDS:
        value = overrides.get(name)
        if value is None:
            continue
        kwargs[name] = type(getattr(defaults, name))(value)
    return RetrieverSettings(**kwargs)


def _workspace_ids(case: Any) -> list[str]:
    """用例的工作区作用域；未声明时用默认工作区。"""
    ids = _str_list(case, "workspace_ids")
    return ids or list(DEFAULT_WORKSPACE_IDS)


def _str_list(obj: Any, name: str) -> list[str]:
    """读对象上的字符串列表字段；缺字段或类型不符时给空列表。"""
    raw = _field(obj, name, None)
    if not isinstance(raw, (list, tuple)):
        return []
    items = cast("Sequence[Any]", raw)
    return [str(item) for item in items]


def _empty_metrics(reason: str) -> dict[str, Any]:
    """不可评题目的指标载荷：值一律 ``None``，**绝不补 0**。"""
    return {
        **{metric_id: None for metric_id in _CASE_METRIC_IDS},
        "reason": reason,
        "metric_status": {metric_id: "not_assessable" for metric_id in _CASE_METRIC_IDS},
    }


def _not_assessable_score(
    *, case_id: str, label_source: str, reason: str
) -> KnowledgeCaseScore:
    """构造一个「不可评」的得分对象，供聚合统计未评率。"""
    return KnowledgeCaseScore(
        case_id=case_id,
        label_source=label_source,
        status="not_assessable",
        metrics={metric_id: None for metric_id in _CASE_METRIC_IDS},
        reason=reason,
        metric_status={metric_id: "not_assessable" for metric_id in _CASE_METRIC_IDS},
        pool_relevant=0,
        pool_size=0,
    )


def _update_run_fields(repository: Any, run_id: str, **fields: Any) -> bool:
    """把运行级字段（``status`` / ``pool`` / ``progress``）写回仓储。

    仓储层当前只暴露 ``finish_run``（写终态与指标），没有写 ``pool_json`` /
    ``progress_json`` 的公开方法。这里**优先**调用仓储未来可能提供的
    ``update_run_fields``；否则退回「同库直写」的兜底通道。SQL 的表名与列名
    全部来自本模块常量，不接受外部输入。
    """
    updater = getattr(repository, "update_run_fields", None)
    if callable(updater):
        updater(run_id, **fields)
        return True
    database = getattr(repository, "_db", None)
    if database is None:
        return False
    assignments: list[str] = []
    values: list[Any] = []
    if "status" in fields:
        assignments.append("status = ?")
        values.append(str(fields["status"]))
    for column, key in (("pool_json", "pool"), ("progress_json", "progress")):
        if key in fields:
            assignments.append(f"{column} = ?")
            values.append(json.dumps(fields[key], ensure_ascii=False))
    if not assignments:
        return False
    values.append(str(run_id))
    with database.connect(immediate=True) as conn:
        conn.execute(
            f"UPDATE knowledge_eval_runs SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
    return True


# --------------------------------------------------------------------------- 运行状态


@dataclass
class _CaseOutcome:
    """一条逐题结果（``case_id::config_id`` 复合形式）的内存形态。"""

    case_id: str
    config_id: str
    label_source: str
    status: str
    score: KnowledgeCaseScore


@dataclass
class _RunState:
    """一次运行的可变状态；取消位与预算位都在这里。"""

    run_id: str
    cancelled: bool = False
    budget_exceeded: bool = False
    judge_calls: int = 0
    outcomes: list[_CaseOutcome] = field(default_factory=list)
    degraded: dict[str, list[str]] = field(default_factory=dict)
    completed: int = 0
    not_assessable: int = 0
    failed: int = 0

    @property
    def degraded_labels(self) -> list[str]:
        """全部降级原因（去重且保持首次出现顺序）。"""
        labels: list[str] = []
        for items in self.degraded.values():
            for item in items:
                if item not in labels:
                    labels.append(item)
        return labels


# --------------------------------------------------------------------------- 执行器


class KnowledgeEvalRunner:
    """知识评测的执行器：取候选 → 并池 → 判分 → 打分 → 落库。

    依赖全部注入，便于离线测试（假 repository / 假知识服务 / 假 provider）。
    """

    def __init__(
        self,
        *,
        repository: Any,
        knowledge_service: Any,
        provider: JudgeProviderView | None,
        model: str | None = None,
    ) -> None:
        self._repository = repository
        self._service = knowledge_service
        self._provider = provider
        self._model = model
        self._states: dict[str, _RunState] = {}

    # ----------------------------------------------------------------- 对外

    async def cancel(self, run_id: str) -> dict[str, Any]:
        """置取消位；runner 在查询边界检查。已完成的结果保留。"""
        state = self._state(run_id)
        state.cancelled = True
        return {"id": run_id, "cancel_requested": True}

    async def run(self, run_id: str) -> None:
        """执行一次运行；任何异常都收敛成 ``failed``，不外泄异常细节。"""
        try:
            await self._execute(run_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 失败原因只留在 error 字段
            self._finish_failed(run_id, f"{type(exc).__name__}: {exc}")

    # ----------------------------------------------------------------- 内部

    def _state(self, run_id: str) -> _RunState:
        state = self._states.get(run_id)
        if state is None:
            state = _RunState(run_id=run_id)
            self._states[run_id] = state
        return state

    def _finish_failed(self, run_id: str, error: str) -> None:
        self._repository.finish_run(run_id, status="failed", metrics={}, error=error)

    def _fail_budget(self, state: _RunState, run_id: str, detail: str) -> None:
        """预算触顶 → 运行置 ``failed``、不产出半截指标，并停止后续处理。"""
        state.budget_exceeded = True
        logger.warning(
            "knowledge_eval_budget_exceeded run=%s detail=%s", run_id, detail
        )
        self._repository.finish_run(
            run_id,
            status="failed",
            metrics={},
            error=f"{CODE_BUDGET_EXCEEDED}: {detail}",
        )

    async def _execute(self, run_id: str) -> None:
        state = self._state(run_id)
        run = self._repository.get_run(run_id)
        if not run:
            self._finish_failed(run_id, f"KNOWLEDGE_RUN_NOT_FOUND: {run_id}")
            return
        dataset = self._repository.get_dataset(str(run.get("dataset_id") or ""))
        if not dataset:
            self._finish_failed(
                run_id, f"KNOWLEDGE_DATASET_NOT_FOUND: {run.get('dataset_id')}"
            )
            return

        cases = list(dataset.get("cases") or [])
        configs = list(run.get("configs") or [])
        budgets = dict(run.get("budgets") or {})
        max_queries = int(budgets.get("max_queries") or 200)
        max_judge_calls = int(budgets.get("max_judge_calls") or 500)
        timeout_s = float(budgets.get("per_query_timeout_s") or 60.0)
        concurrency = max(1, int(budgets.get("concurrency") or 4))

        if len(cases) > max_queries:
            self._fail_budget(
                state, run_id, f"查询数 {len(cases)} 超过 max_queries={max_queries}"
            )
            return

        _update_run_fields(
            self._repository,
            run_id,
            status="running",
            progress={"total_queries": len(cases), "completed": 0},
        )
        logger.info(
            "knowledge_eval_run_started run=%s queries=%d configs=%d concurrency=%d",
            run_id,
            len(cases),
            len(configs),
            concurrency,
        )

        semaphore = asyncio.Semaphore(concurrency)

        async def handle(case: Any) -> None:
            async with semaphore:
                if state.cancelled or state.budget_exceeded:
                    return
                await self._handle_case(
                    state,
                    run_id,
                    case,
                    configs,
                    timeout_s=timeout_s,
                    max_judge_calls=max_judge_calls,
                )

        outcomes = await asyncio.gather(
            *(handle(case) for case in cases), return_exceptions=True
        )

        if state.budget_exceeded:
            # 预算触顶：运行已置 failed 且不产出半截指标——不再聚合。
            return

        errors = [item for item in outcomes if isinstance(item, BaseException)]
        if errors:
            first = errors[0]
            logger.error(
                "knowledge_eval_run_error run=%s error=%s", run_id, first
            )
            self._finish_failed(
                run_id, f"RUNNER_ERROR: {type(first).__name__}: {first}"
            )
            return

        groups = self._aggregate(state)
        pool_payload = {
            "degraded": state.degraded_labels,
            "degraded_detail": {key: list(value) for key, value in state.degraded.items()},
            "by_config": self._aggregate_by_config(state),
        }
        progress_payload = {
            "total_queries": len(cases),
            "completed": state.completed,
            "not_assessable": state.not_assessable,
            "failed": state.failed,
            "judge_calls": state.judge_calls,
            "cancelled": state.cancelled,
        }
        _update_run_fields(
            self._repository, run_id, pool=pool_payload, progress=progress_payload
        )
        logger.info(
            "knowledge_eval_run_finished run=%s completed=%d not_assessable=%d"
            " failed=%d judge_calls=%d degraded=%d cancelled=%s",
            run_id,
            state.completed,
            state.not_assessable,
            state.failed,
            state.judge_calls,
            len(state.degraded_labels),
            state.cancelled,
        )
        if state.cancelled:
            self._repository.finish_run(
                run_id, status="cancelled", metrics=groups, error="CANCELLED"
            )
            return
        self._repository.finish_run(run_id, status="completed", metrics=groups)

    async def _handle_case(
        self,
        state: _RunState,
        run_id: str,
        case: Any,
        configs: Sequence[Any],
        *,
        timeout_s: float,
        max_judge_calls: int,
    ) -> None:
        """处理一条查询：检索 → 并池 → 判定 → 逐配置打分 → 落库。"""
        case_id = _text(case, "id")
        query = _text(case, "query")
        label_source = _text(case, "label_source", "judge") or "judge"
        kb_ids = _str_list(case, "kb_ids")
        workspace_ids = _workspace_ids(case)
        expect_any = _str_list(case, "expect_any")

        hits_by_config: dict[str, list[Any]] = {}
        limits: dict[str, int] = {}
        retrieval_error: str | None = None
        scope_reason: str | None = None

        for config in configs:
            config_id = _text(config, "id")
            overrides = _overrides_dict(_field(config, "overrides", {}))
            mode = _text(overrides, "mode") or _text(case, "mode", "hybrid") or "hybrid"
            limit = int(overrides.get("limit") or _field(case, "limit", 8) or 8)
            limits[config_id] = limit
            settings = _retriever_settings(overrides)
            retriever = self._service.retriever_with(settings)
            try:
                # Retriever 是线程内同步 API，必须 to_thread 包裹。
                result = await asyncio.wait_for(
                    asyncio.to_thread(
                        retriever.search,
                        query,
                        workspace_ids=list(workspace_ids),
                        kb_ids=(list(kb_ids) or None),
                        mode=mode,
                        limit=limit,
                    ),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                retrieval_error = f"RETRIEVAL_TIMEOUT: 查询超过 {timeout_s:g}s 未返回"
                break
            except Exception as exc:  # noqa: BLE001 - 检索失败 ≠ 空结果
                retrieval_error = f"RETRIEVAL_FAILED: {type(exc).__name__}: {exc}"
                break

            labels = [str(item) for item in (getattr(result, "degraded", ()) or ())]
            if labels:
                logger.warning(
                    "knowledge_eval_retrieval_degraded run=%s config=%s degraded=%s",
                    run_id,
                    config_id,
                    ",".join(labels),
                )
                state.degraded.setdefault(config_id, [])
                for label in labels:
                    if label not in state.degraded[config_id]:
                        state.degraded[config_id].append(label)
            raw_hits = getattr(result, "hits", ())
            hits_by_config[config_id] = list(raw_hits) if raw_hits else []
            reason = getattr(result, "reason", None)
            if map_search_reason(reason, labels) == "not_assessable":
                scope_reason = str(reason)
                break

        if retrieval_error is not None:
            # 检索失败：该题 failed，不参与聚合、不计 0。
            logger.warning(
                "knowledge_eval_case_failed run=%s case=%s error=%s",
                run_id,
                case_id,
                retrieval_error,
            )
            state.failed += 1
            self._save_case_failure(
                run_id,
                case_id=case_id,
                label_source=label_source,
                configs=configs,
                error=retrieval_error,
            )
            return

        if scope_reason is not None:
            # 作用域 / 查询问题：不可评，不补 0。
            state.not_assessable += 1
            for config in configs:
                config_id = _text(config, "id")
                self._repository.save_result(
                    run_id=run_id,
                    case_id=f"{case_id}::{config_id}",
                    status="not_assessable",
                    label_source=label_source,
                    hits=[],
                    metrics=_empty_metrics(scope_reason),
                    error=scope_reason,
                )
                state.outcomes.append(
                    _CaseOutcome(
                        case_id=f"{case_id}::{config_id}",
                        config_id=config_id,
                        label_source=label_source,
                        status="not_assessable",
                        score=_not_assessable_score(
                            case_id=f"{case_id}::{config_id}",
                            label_source=label_source,
                            reason=scope_reason,
                        ),
                    )
                )
            return

        pool = build_pool(hits_by_config)
        grades: dict[str, int] = {}
        reasons: dict[str, str] = {}

        if label_source == "expect":
            # 关键词确定性判定：正文小写包含任一 expect_any → grade 3，否则 0。
            for candidate in pool:
                body = candidate.content.lower()
                matched = next(
                    (term for term in expect_any if term.lower() in body), ""
                )
                grades[candidate.chunk_id] = 3 if matched else 0
                reasons[candidate.chunk_id] = f"expect_any 命中：{matched}" if matched else ""
        else:
            if pool:
                if state.judge_calls >= max_judge_calls:
                    self._fail_budget(
                        state,
                        run_id,
                        f"判定调用数超过 max_judge_calls={max_judge_calls}",
                    )
                    return
                state.judge_calls += 1
                provider = self._provider
                if provider is None:
                    raise RuntimeError("JUDGE_PROVIDER_MISSING: 未配置判定 provider")
                judged = await judge_relevance(
                    query,
                    pool,
                    provider,
                    model=self._model,
                    timeout_s=timeout_s,
                )
                if judged.status != "completed":
                    state.not_assessable += 1
                    error = judged.error or "JUDGE_FAILED"
                    for config in configs:
                        config_id = _text(config, "id")
                        self._repository.save_result(
                            run_id=run_id,
                            case_id=f"{case_id}::{config_id}",
                            status="not_assessable",
                            label_source=label_source,
                            hits=[],
                            metrics=_empty_metrics("JUDGE_NOT_ASSESSABLE"),
                            error=error,
                        )
                        state.outcomes.append(
                            _CaseOutcome(
                                case_id=f"{case_id}::{config_id}",
                                config_id=config_id,
                                label_source=label_source,
                                status="not_assessable",
                                score=_not_assessable_score(
                                    case_id=f"{case_id}::{config_id}",
                                    label_source=label_source,
                                    reason="JUDGE_NOT_ASSESSABLE",
                                ),
                            )
                        )
                    return
                for key, value in judged.labels.items():
                    if key.endswith("_reason"):
                        continue
                    grades[str(key)] = int(value)
                for key, value in judged.labels.items():
                    if key.endswith("_reason"):
                        reasons[str(key)[: -len("_reason")]] = str(value)

        state.completed += 1
        for config in configs:
            config_id = _text(config, "id")
            hits = hits_by_config.get(config_id, [])
            top_k = [
                (_text(hit, "chunk_id"), int(grades.get(_text(hit, "chunk_id"), 0)))
                for hit in hits
            ]
            limit = limits.get(config_id, int(_field(case, "limit", 8) or 8))
            composite = f"{case_id}::{config_id}"
            score = case_score(
                case_id=composite,
                label_source=label_source,
                top_k=top_k,
                pool_grades=grades,
                k=limit,
            )
            hits_payload: list[dict[str, Any]] = []
            for index, (chunk_id, grade) in enumerate(top_k):
                hit = hits[index]
                hits_payload.append(
                    {
                        "chunk_id": chunk_id,
                        "rank": index + 1,
                        "score": _float(hit, "score"),
                        "content_hash": _text(hit, "content_hash"),
                        "grade": grade,
                        "reason": reasons.get(chunk_id, ""),
                    }
                )
            self._repository.save_result(
                run_id=run_id,
                case_id=composite,
                status=score.status,
                label_source=label_source,
                hits=hits_payload,
                metrics={
                    **score.metrics,
                    "reason": score.reason,
                    "metric_status": score.metric_status,
                },
                error=score.reason or None,
            )
            state.outcomes.append(
                _CaseOutcome(
                    case_id=composite,
                    config_id=config_id,
                    label_source=label_source,
                    status=score.status,
                    score=score,
                )
            )

    def _save_case_failure(
        self,
        run_id: str,
        *,
        case_id: str,
        label_source: str,
        configs: Sequence[Any],
        error: str,
    ) -> None:
        """检索失败 → 该题所有配置都记 ``failed``（不进聚合）。"""
        for config in configs:
            config_id = _text(config, "id")
            composite = f"{case_id}::{config_id}"
            self._repository.save_result(
                run_id=run_id,
                case_id=composite,
                status="failed",
                label_source=label_source,
                hits=[],
                metrics=_empty_metrics("RETRIEVAL_FAILED"),
                error=error,
            )

    def _aggregate(self, state: _RunState) -> dict[str, Any]:
        """按标注来源分组聚合；``failed`` 的题不参与聚合。"""
        scores = [
            outcome.score for outcome in state.outcomes if outcome.status != "failed"
        ]
        return aggregate_metrics(scores)

    def _aggregate_by_config(self, state: _RunState) -> dict[str, Any]:
        """按配置再分一层的聚合——供「配置间对比」使用。"""
        by_config: dict[str, list[KnowledgeCaseScore]] = {}
        for outcome in state.outcomes:
            if outcome.status == "failed":
                continue
            by_config.setdefault(outcome.config_id, []).append(outcome.score)
        return {
            config_id: aggregate_metrics(scores) for config_id, scores in by_config.items()
        }


__all__ = [
    "CODE_BUDGET_EXCEEDED",
    "DEFAULT_WORKSPACE_IDS",
    "KnowledgeEvalRunner",
    "REASON_NOT_ASSESSABLE",
    "build_pool",
    "map_search_reason",
    "split_by_label_source",
]
