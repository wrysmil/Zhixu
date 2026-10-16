"""Judge 量表校准（spec §11 验收 10 + 契约 §3.1 + plan T08 第 5–7 项）。

要解决的问题：**未经验收的模型评分不能当门禁**。开放任务可以只显示原始分数并进入
``review_required``，但把「未校准」当成「通过」是评估系统最危险的失败模式。

口径（全部来自契约，模块不自定义阈值语义）：

- ``judge_fingerprint`` 只由 ``provider`` / ``model`` / ``generation`` + ``prompt_version`` +
  量表版本**与正文**做 canonical JSON 后取 sha256。换服务商、换模型、改采样参数、改提示词或
  改量表任一项，指纹都变，旧校准在新指纹下查不到结论 → 未校准。指纹只读这三个 profile 字段，
  ``api_key`` 等秘密既不参与哈希也不会出现在导出里。
- gold 样本必须是人工 ``confirmed`` 的标签，数量 ≥ 10、正反例各 ≥ 4；``proposed``（内置/AI 建议）
  一律不算，AI 不能替用户确认。
- ``agreement = matched / total``，``passed`` 由仓储按 ``>= 0.8`` 判定；判分侧不重复实现第二套
  阈值，也不自建表——读写全部走 WU-01 的 ``list_gold_samples`` / ``record_calibration_result`` /
  ``find_calibration_result``。
- 校准只读不写 gold：本模块没有任何“回写标签”的入口，人工复核是服务层的追加记录。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from nanobot.evaluation.graders.judge import PROMPT_VERSION
from nanobot.evaluation.graders.rules import AttemptEvidenceView

#: 契约 §3.1：至少 10 个已确认样本。
GOLD_MIN_SAMPLES = 10
#: 契约 §3.1：正反例各至少 4 个。
GOLD_MIN_PER_POLARITY = 4
#: 契约 §3.1：一致率达标线（由仓储据此判定 ``passed``）。
AGREEMENT_TARGET = 0.8
#: spec §7.1 默认通过阈值。
DEFAULT_PASS_SCORE = 3

GoldRow = Mapping[str, Any]
JudgeCallable = Callable[[Any, AttemptEvidenceView], Awaitable[Any]]

_POLARITIES = ("passed", "failed")


class CalibrationError(ValueError):
    """校准前置条件不满足：消息带稳定错误码，服务层直接映射成 422/409。"""


def judge_fingerprint(
    *,
    profile: Mapping[str, Any],
    rubric: Mapping[str, Any] | Any,
    prompt_version: str = PROMPT_VERSION,
) -> str:
    """provider/model/generation + prompt_version + 量表正文 的 sha256 指纹。"""
    provider = _required_text(profile, "provider")
    model = _required_text(profile, "model")
    payload: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "generation": _generation_of(profile),
        "prompt_version": prompt_version,
        "rubric": _canonical(rubric_payload(rubric)),
    }
    return hashlib.sha256(_dump(payload).encode("utf-8")).hexdigest()


def rubric_payload(rubric: Mapping[str, Any] | Any) -> dict[str, Any]:
    """量表既可能是 JSON 对象也可能是 ``Rubric`` 模型；统一成可比对的 dict。"""
    dump = getattr(rubric, "model_dump", None)
    if callable(dump):
        body = dump(mode="json")
    elif isinstance(rubric, Mapping):
        body = dict(rubric)
    else:
        raise CalibrationError(f"RUBRIC_UNREADABLE: {type(rubric).__name__}")
    if not isinstance(body, dict):  # pragma: no cover - 防御 model_dump 返回意外形状
        raise CalibrationError("RUBRIC_UNREADABLE: 量表不是可序列化对象")
    return body


def rubric_version(rubric: Mapping[str, Any] | Any) -> str:
    version = rubric_payload(rubric).get("version")
    if not isinstance(version, str) or not version.strip():
        raise CalibrationError("RUBRIC_VERSION_REQUIRED: 量表缺少 version")
    return version


def select_gold_samples(
    gold: Mapping[str, Any],
    *,
    rubric: Mapping[str, Any] | Any,
) -> list[dict[str, Any]]:
    """从 ``list_gold_samples()`` 结果里挑出可用于校准的样本，不满足门禁即抛出。"""
    rows = [dict(row) for row in (gold.get("data") or ())]
    ids = [str(row.get("id", "")) for row in rows]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise CalibrationError(f"GOLD_SAMPLE_DUPLICATE: {duplicates}")

    unconfirmed = sorted(item for item, row in zip(ids, rows) if row.get("label_status") != "confirmed")
    if unconfirmed:
        raise CalibrationError(
            f"GOLD_NOT_CONFIRMED: {len(unconfirmed)} 条标签仍是建议状态，需用户确认后才能校准"
        )

    target = _dump(_canonical(rubric_payload(rubric)))
    matching = [row for row in rows if _dump(_canonical(rubric_payload(row.get("rubric") or {}))) == target]
    if not matching:
        raise CalibrationError(
            f"GOLD_RUBRIC_MISMATCH: 量表 {rubric_version(rubric)} 没有匹配的人工样本，保持未校准"
        )
    if len(matching) < GOLD_MIN_SAMPLES:
        raise CalibrationError(
            f"GOLD_SAMPLES_INSUFFICIENT: 量表 {rubric_version(rubric)} 只有 {len(matching)} 条"
            f" 匹配样本，少于 {GOLD_MIN_SAMPLES}"
        )

    invalid = sorted(
        str(row.get("id"))
        for row in matching
        if row.get("expected_verdict") not in _POLARITIES
    )
    if invalid:
        raise CalibrationError(f"GOLD_LABEL_INVALID: {invalid} 的 expected_verdict 非法")

    counts = {polarity: sum(1 for row in matching if row.get("expected_verdict") == polarity) for polarity in _POLARITIES}
    if min(counts.values()) < GOLD_MIN_PER_POLARITY:
        raise CalibrationError(
            f"GOLD_POLARITY_IMBALANCE: 通过 {counts['passed']} / 失败 {counts['failed']}，"
            f"正反例各需至少 {GOLD_MIN_PER_POLARITY} 条"
        )
    return matching


def verdict_from_scores(
    rubric: Mapping[str, Any] | Any,
    dimensions: Sequence[Any],
) -> str | None:
    """按各维度 ``pass_score`` 把评分折算成 gold 可比对的 passed/failed；维度不全是 ``None``。"""
    thresholds: dict[str, int] = {}
    for dimension in rubric_payload(rubric).get("dimensions") or ():
        body = dimension if isinstance(dimension, Mapping) else rubric_payload(dimension)
        dimension_id = body.get("id")
        if isinstance(dimension_id, str):
            thresholds[dimension_id] = _pass_score(body.get("pass_score"))
    if not thresholds:
        return None
    scores: dict[str, int] = {}
    for item in dimensions:
        dimension_id = getattr(item, "id", None)
        score = getattr(item, "score", None)
        if isinstance(dimension_id, str) and isinstance(score, int) and not isinstance(score, bool):
            scores[dimension_id] = score
    # 维度缺分＝无法折算：交给上层标记未评估，而不是拿 None 去比大小。
    if not set(thresholds) <= set(scores):
        return None
    return "passed" if all(scores[key] >= thresholds[key] for key in thresholds) else "failed"


async def run_calibration(
    repository: Any,
    *,
    profile: Mapping[str, Any],
    rubric: Mapping[str, Any] | Any,
    judge: JudgeCallable,
    evidence_for: Callable[[GoldRow], AttemptEvidenceView | None],
    samples: Sequence[Mapping[str, Any]] | None = None,
    gold_revision: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """跑一遍 gold 样本，按契约 §3.1 记一条 ``calibration_results``。

    标签与量表**一律取仓储里的 gold 行**（调用方传入的 ``samples`` 只提供证据对象，不参与判定），
    避免调用方用自己的期望值抬高一致率。取消时不写结论：契约规定「失败/取消/未完成」的
    ``passed`` 为 ``null``，留下半成品结论会污染门禁。
    """
    fingerprint = judge_fingerprint(profile=profile, rubric=rubric)
    gold = repository.list_gold_samples()
    revision = int(gold.get("gold_revision", 0))
    if gold_revision is not None and gold_revision != revision:
        raise CalibrationError(f"GOLD_REVISION_STALE: 请求 {gold_revision}，当前 {revision}")
    selected = select_gold_samples(gold, rubric=rubric)
    sources = _index_sources(samples, selected)

    items: list[dict[str, Any]] = []
    matched = 0
    for row in selected:
        if should_cancel is not None and should_cancel():
            return {
                "status": "cancelled",
                "judge_fingerprint": fingerprint,
                "rubric_version": rubric_version(rubric),
                "gold_revision": revision,
                "total": len(selected),
                "completed": len(items),
                "matched": matched,
                "agreement": None,
                "passed": None,
                "items": items,
            }
        evidence = evidence_for(sources[str(row["id"])])
        if evidence is None:
            raise CalibrationError(f"GOLD_SAMPLE_EVIDENCE_MISSING: {row['id']}")
        result = await judge(row.get("rubric") or rubric, evidence)
        actual = verdict_from_scores(row.get("rubric") or rubric, getattr(result, "dimensions", ()))
        hit = (
            getattr(result, "status", None) == "completed"
            and actual is not None
            and actual == row["expected_verdict"]
        )
        matched += int(hit)
        items.append(
            {
                "id": row["id"],
                "expected_verdict": row["expected_verdict"],
                "actual_verdict": actual,
                "judge_status": getattr(result, "status", None),
                "matched": hit,
                "error": getattr(result, "error", None),
            }
        )

    recorded = repository.record_calibration_result(
        judge_fingerprint=fingerprint,
        rubric_version=rubric_version(rubric),
        gold_revision=revision,
        total=len(selected),
        matched=matched,
    )
    return {"status": "completed", **dict(recorded), "completed": len(selected), "items": items}


def lookup_calibration(
    repository: Any,
    *,
    judge_fingerprint: str,
    rubric_version: str,
    gold_revision: int,
) -> dict[str, Any] | None:
    """当前指纹 + 量表 + gold 版本上的校准结论；查不到就是「未校准」。"""
    return repository.find_calibration_result(
        judge_fingerprint=judge_fingerprint,
        rubric_version=rubric_version,
        gold_revision=gold_revision,
    )


def calibration_snapshot(
    *,
    profile: Mapping[str, Any],
    rubric: Mapping[str, Any] | Any,
    gold_revision: int,
    sample_count: int,
    prompt_version: str = PROMPT_VERSION,
) -> dict[str, Any]:
    """服务快照（契约 §3.1「快照 gold、rubric、judge_fingerprint」）；不含任何密钥。"""
    generation = _generation_of(profile)
    return {
        "judge_fingerprint": judge_fingerprint(
            profile=profile, rubric=rubric, prompt_version=prompt_version
        ),
        "provider": str(profile.get("provider", "")),
        "model": str(profile.get("model", "")),
        "generation": generation,
        "prompt_version": prompt_version,
        "rubric_version": rubric_version(rubric),
        "rubric": _canonical(rubric_payload(rubric)),
        "gold_revision": gold_revision,
        "sample_count": sample_count,
        "agreement_target": AGREEMENT_TARGET,
    }


# --------------------------------------------------------------------------- 小工具


def _index_sources(
    samples: Sequence[Mapping[str, Any]] | None,
    selected: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """调用方样本只按 id 匹配仓储选中的 gold 行。"""
    by_id = {str(row.get("id", "")): dict(row) for row in (samples or selected)}
    missing = sorted({str(row["id"]) for row in selected} - set(by_id))
    if missing:
        raise CalibrationError(f"GOLD_SAMPLE_EVIDENCE_MISSING: {missing}")
    return {key: by_id[key] for key in by_id}


def _required_text(profile: Mapping[str, Any], key: str) -> str:
    value = profile.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CalibrationError(f"JUDGE_PROFILE_INCOMPLETE: 缺少 {key}")
    return value


def _generation_of(profile: Mapping[str, Any]) -> Any:
    """只接受映射或 ``null``：缺失记 ``null``（与「显式空配置」区分），不补默认值。"""
    generation = profile.get("generation", None)
    if generation is None:
        return None
    if isinstance(generation, Mapping):
        return {str(key): _scalar(value) for key, value in generation.items()}
    raise CalibrationError("JUDGE_PROFILE_GENERATION_INVALID: generation 必须是对象")


def _scalar(value: Any) -> Any:
    if isinstance(value, bool) or isinstance(value, (int, float, str)) or value is None:
        return value
    return str(value)


def _canonical(body: Mapping[str, Any]) -> Any:
    """量表正文的规范形状：维度按声明顺序保留（顺序是量表语义的一部分），键排序由 dump 负责。"""
    payload = dict(body)
    dimensions = payload.get("dimensions")
    if isinstance(dimensions, Sequence) and not isinstance(dimensions, (str, bytes)):
        payload["dimensions"] = [dict(item) for item in dimensions]
    return payload


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _pass_score(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4:
        return DEFAULT_PASS_SCORE
    return value
