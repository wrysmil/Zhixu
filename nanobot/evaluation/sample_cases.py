"""把内置分层样本转换成可入库的用例。

样本文件（reliability-v2）带一层**判据元数据**：case_id、target、tier、
失败定义、可接受替代路径、所需环境、gold 状态。CaseWrite 是 extra=forbid 的
入库模型，两者不是一回事，直接丢进去会被拒。

这里做一次有损但可追溯的映射：

- 元数据进 tags 与描述，页面与报告里能看出这条用例属于哪一档、为什么算失败；
- environment.required 里的 fixture 依赖转成 dependencies（缺失依赖要明示）；
- gold 不入库——人工确认属于校准通道，样本里的 gold 一律 proposed。

映射只做「搬运」，不改判据：断言、里程碑、量表原样进入用例。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nanobot.evaluation.schemas import CaseWrite

#: 样本里允许进入用例模型的字段；其余一律当元数据处理。
CASE_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "tags",
    "turns",
    "fixtures",
    "memory_seed",
    "clock_iso",
    "tool_fixtures",
    "execution",
    "assertions",
    "rubric",
    "script",
    "artifact_paths",
    "evaluation",
)

#: 需要经过适配才能入库的内置样本。
ADAPTED_SAMPLE_IDS: frozenset[str] = frozenset({"reliability-v2"})


def _dependencies(raw: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """把 environment.required 拆成 (入库依赖, 运行期必需输入)。

    - 普通 fixture 依赖：用例入库时必须齐备（dependencies）；
    - 标注「故意缺失」的 fixture：入库合法，运行 setup 时必须明确报缺
      （required_inputs），否则会用空文件假装任务成功；
    - tool/skill/team/member 之类不是文件依赖，只进描述。
    """
    environment = raw.get("environment")
    if not isinstance(environment, Mapping):
        return [], []
    required = environment.get("required")
    if not isinstance(required, list):
        return [], []
    deps: list[str] = []
    inputs: list[str] = []
    for item in required:
        if not isinstance(item, str) or not item.startswith("fixture:"):
            continue
        value = item.split(":", 1)[1].strip()
        if "故意缺失" in value or "missing" in value.lower():
            inputs.append(value.split("（")[0].strip())
        else:
            deps.append(value)
    return deps, inputs


def _notes(raw: Mapping[str, Any]) -> list[str]:
    """把判据元数据拼成可读说明，附在用例描述后面。"""
    notes: list[str] = []
    failure = raw.get("failure_definition")
    if isinstance(failure, str) and failure.strip():
        notes.append(f"失败定义：{failure.strip()}")
    alternatives = raw.get("acceptable_alternative_paths")
    if isinstance(alternatives, list) and alternatives:
        joined = "、".join(str(item) for item in alternatives if isinstance(item, str))
        if joined:
            notes.append(f"可接受替代路径：{joined}")
    environment = raw.get("environment")
    if isinstance(environment, Mapping):
        required = environment.get("required")
        if isinstance(required, list) and required:
            joined = "、".join(str(item) for item in required)
            notes.append(f"所需环境：{joined}")
        identity = environment.get("identity_template")
        if isinstance(identity, str) and identity:
            notes.append(f"身份模板：{identity}")
    expected = raw.get("expected")
    if isinstance(expected, Mapping):
        reference = expected.get("reference_answer")
        if isinstance(reference, str) and reference:
            notes.append(f"参考输出：{reference}")
    return notes


def adapt_case(raw: Mapping[str, Any]) -> CaseWrite:
    """把一条样本用例映射成可入库的 CaseWrite。"""
    data: dict[str, Any] = {
        key: value for key, value in raw.items() if key in CASE_FIELDS
    }
    tags = [str(item) for item in (data.get("tags") or [])]
    for key in ("case_id", "target", "tier"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            tags.append(f"{key}:{value}")
    data["tags"] = list(dict.fromkeys(tags))
    notes = _notes(raw)
    if notes:
        base = str(data.get("description") or "").strip()
        data["description"] = "\n".join([base, *notes]) if base else "\n".join(notes)
    dependencies, required_inputs = _dependencies(raw)
    if dependencies:
        data["dependencies"] = dependencies
    if required_inputs:
        data["required_inputs"] = required_inputs
    # suite_id 在导入时被服务端重建，这里只是让模型校验通过。
    suite_id = raw.get("suite_id")
    data["suite_id"] = (
        suite_id if isinstance(suite_id, str) and suite_id
        else "00000000-0000-4000-8000-000000000000"
    )
    return CaseWrite.model_validate(data)


def adapt_sample_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """把内置样本文档转换成导入接口认识的形状。"""
    suite = payload.get("suite")
    cases = payload.get("cases")
    if not isinstance(suite, Mapping) or not isinstance(cases, list):
        raise ValueError("SAMPLE_PAYLOAD_INVALID")
    adapted = [adapt_case(item).model_dump(mode="json", by_alias=True) for item in cases]
    return {
        "schema_version": 1,
        "suite": dict(suite),
        "cases": adapted,
    }
