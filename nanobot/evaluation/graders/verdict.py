"""verdict 合并（契约 §4 状态分离 + plan T07 优先级）。

本模块只做确定性合并，不读证据、不调模型：输入是已经算好的 execution_status、必过规则
结论列表、Judge 归一状态与 Trace 完整性，输出契约 §4 的四值 verdict。

优先级（plan T07 钉死，不得改动）：

1. ``failed`` / ``timed_out`` → ``failed``（已开始执行且失败，任何评分都不能翻案）。
2. 其他非 ``succeeded`` 状态 → ``not_evaluated``（取消、启动失败、服务中断没有可判结果）。
3. 必过规则出现 ``False`` 或 ``judge_state == "failed"`` → ``failed``。
4. Trace 不完整、必过规则出现 ``None``（读不到证据）、Judge 缺失/异常/未校准
   → ``review_required``。
5. 其余 → ``passed``。

``passed=None`` 与 ``False`` 的区分是这套合并的全部意义：采集故障不能算成 Agent 失败，
也不能算成通过。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

JUDGE_STATES = frozenset(
    {"disabled", "passed", "failed", "missing", "error", "uncalibrated"}
)
EXECUTION_STATUSES = frozenset(
    {
        "queued",
        "running",
        "succeeded",
        "failed",
        "timed_out",
        "cancelled",
        "interrupted",
        "setup_error",
    }
)
#: Judge 无法给出可用结论、必须人工复核的状态。
_REVIEW_JUDGE_STATES = frozenset({"missing", "error", "uncalibrated"})


def derive_verdict(
    execution_status: str,
    required_rules: Sequence[bool | None],
    judge_state: str,
    trace_complete: bool,
) -> str:
    """按 plan T07 的优先级合并出 verdict；入参非法立刻抛出，避免静默给出错误结论。"""
    if execution_status not in EXECUTION_STATUSES:
        raise ValueError(f"EXECUTION_STATUS_UNSUPPORTED:{execution_status}")
    if judge_state not in JUDGE_STATES:
        raise ValueError(f"JUDGE_STATE_UNSUPPORTED:{judge_state}")
    rules = list(required_rules)
    if execution_status in {"failed", "timed_out"}:
        return "failed"
    if execution_status != "succeeded":
        return "not_evaluated"
    if False in rules or judge_state == "failed":
        return "failed"
    if not trace_complete or None in rules or judge_state in _REVIEW_JUDGE_STATES:
        return "review_required"
    return "passed"


def judge_state_of(
    rubric: Any,
    judge: Any,
    calibration: Mapping[str, Any] | None,
) -> str:
    """把 ``JudgeResult`` + 量表 + 校准结论归一成 ``derive_verdict`` 需要的状态。

    归一规则（契约 §4）：

    - 没有量表 → ``disabled``。
    - 必需量表：无结果 ``missing``；状态异常或维度不全 ``error``；未通过校准 ``uncalibrated``；
      否则按各维度 ``pass_score`` 给 ``passed`` / ``failed``。
    - 可选量表：``error`` 归一为 ``disabled``（原始异常仍留在 ``judge.error`` 里供展示），
      评分本身不改变有效规则结论；但**未校准**保持 ``uncalibrated``，因为开放式任务不能拿
      未校准分数门禁为通过（spec 验收 10）。
    """
    if rubric is None:
        return "disabled"
    required = bool(_field(rubric, "required", False))
    if judge is None:
        return "missing" if required else "disabled"
    if judge.status != "completed" or judge.error is not None:
        return "error" if required else "disabled"
    scores = _scores_by_dimension(judge)
    thresholds = _thresholds_by_dimension(rubric)
    if not thresholds or not set(thresholds) <= set(scores):
        return "error" if required else "disabled"
    if not _is_calibrated(judge, calibration):
        return "uncalibrated"
    passed = all(scores[key] >= thresholds[key] for key in thresholds)
    if passed:
        return "passed"
    return "failed" if required else "disabled"


def _field(container: Any, key: str, default: Any) -> Any:
    """量表既可能是契约 §2 的 JSON 对象，也可能是已校验的 ``Rubric`` 模型。"""
    if isinstance(container, Mapping):
        return container.get(key, default)
    return getattr(container, key, default)


def _scores_by_dimension(judge: Any) -> dict[str, int]:
    return {dimension.id: dimension.score for dimension in judge.dimensions}


def _thresholds_by_dimension(rubric: Any) -> dict[str, int]:
    thresholds: dict[str, int] = {}
    for dimension in _field(rubric, "dimensions", ()) or ():
        dimension_id = _field(dimension, "id", None)
        if isinstance(dimension_id, str):
            thresholds[dimension_id] = int(_field(dimension, "pass_score", 3))
    return thresholds


def _is_calibrated(judge: Any, calibration: Mapping[str, Any] | None) -> bool:
    """只有当前指纹+量表+gold 版本上「已达标」的校准记录才放行（``passed is True``）。"""
    if getattr(judge, "calibrated", False):
        return True
    if calibration is None:
        return False
    return calibration.get("passed") is True
