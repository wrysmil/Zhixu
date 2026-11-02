"""把内置分层样本转换成可入库的用例。

样本文件（reliability-v2）带一层**判据元数据**：case_id、target、tier、
失败定义、可接受替代路径、所需环境、gold 状态。CaseWrite 是 extra=forbid 的
入库模型，两者不是一回事，直接丢进去会被拒。

这里做一次有损但可追溯的映射：

- 元数据进 tags 与描述，页面与报告里能看出这条用例属于哪一档、为什么算失败；
- environment.required 里的 fixture 依赖转成 dependencies（缺失依赖要明示）；
- gold 不入库——人工确认属于校准通道，样本里的 gold 一律 proposed。

映射只做「搬运」，不改判据：断言、里程碑、量表原样进入用例。

内存专项的三个内置集合（``memory-*-v1``）走的是**另一条路**：它们是记忆场景，
不是普通用例，因此单独注册、单独入库——内部仍然调用同一个版本化导入器
（:func:`nanobot.evaluation.memory.datasets.import_dataset`），并返回
``suite_id`` 与 ``memory_dataset_revision_id``。旧 sample_id 与 xlsx 导入路径不受影响。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.memory.datasets import import_dataset
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

#: 内置记忆集合（JSONL 场景文件，与旧样本的 .json 用例文件不是一回事）。
MEMORY_SAMPLE_IDS: frozenset[str] = frozenset(
    {"memory-smoke-v1", "memory-core-v1", "memory-calibration-v1"}
)

#: 每个内置记忆集合的登记信息：名称、split、来源与许可声明。
MEMORY_SAMPLE_METADATA: dict[str, dict[str, Any]] = {
    "memory-smoke-v1": {
        "name": "中文记忆冒烟集",
        "description": "20 条 development 场景，用于开发期快速发现明显问题。",
        "tags": ["memory", "smoke", "development"],
        "dataset_version": "memory-smoke-v1",
        "split": "development",
    },
    "memory-core-v1": {
        "name": "中文记忆核心回归集",
        "description": "120 条 regression 场景，10 类各 12 条，发布候选重复 3 次。",
        "tags": ["memory", "core", "regression"],
        "dataset_version": "memory-core-v1",
        "split": "regression",
    },
    "memory-calibration-v1": {
        "name": "中文记忆校准集",
        "description": "独立模板与人物的校准场景，gold 默认 proposed（未经人工确认）。",
        "tags": ["memory", "calibration", "calibration"],
        "dataset_version": "memory-calibration-v1",
        "split": "calibration",
    },
}

#: 内置记忆集合统一的来源声明：随包发布、无外部仓库、许可待确认。
MEMORY_SAMPLE_SOURCE: dict[str, Any] = {
    "repo_url": "local://nanobot/evaluation/samples",
    "license": "internal-test-only",
    "loader_version": "memory-dataset-loader/v1",
    "grading_protocol": "memory-gold/v1",
}


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


# ---------------------------------------------------------------- 记忆专项内置集合


def _samples_dir() -> Path:
    return Path(__file__).parent / "samples"


def memory_sample_path(sample_id: str) -> Path:
    """内置记忆集合的随包文件路径（只读）。"""
    if sample_id not in MEMORY_SAMPLE_IDS:
        raise ValueError(f"SAMPLE_NOT_FOUND {sample_id}")
    return _samples_dir() / f"{sample_id}.jsonl"


def memory_sample_scenarios(sample_id: str) -> list[dict[str, Any]]:
    """读出内置记忆集合的原始场景行（未做映射，保持文件原样）。"""
    path = memory_sample_path(sample_id)
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def memory_sample_source(sample_id: str) -> dict[str, Any]:
    """内置记忆集合的来源元数据（sha256 取随包文件的真实摘要）。"""
    metadata = MEMORY_SAMPLE_METADATA[sample_id]
    path = memory_sample_path(sample_id)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "repo_url": MEMORY_SAMPLE_SOURCE["repo_url"],
        "revision": metadata["dataset_version"],
        "sha256": digest,
        "license": MEMORY_SAMPLE_SOURCE["license"],
        "original_ids": [str(row["scenario_id"]) for row in memory_sample_scenarios(sample_id)],
        "loader_version": MEMORY_SAMPLE_SOURCE["loader_version"],
        "grading_protocol": MEMORY_SAMPLE_SOURCE["grading_protocol"],
    }


def _memory_suite_tag(sample_id: str) -> str:
    return f"memory-sample:{sample_id}"


def import_memory_sample(
    database: EvaluationDatabase, sample_id: str
) -> dict[str, Any]:
    """把内置记忆集合导入为版本化集合修订。

    走的是**同一个版本化导入器**（:func:`import_dataset`）：先落 staging、再原子发布
    ready；重复导入返回同一修订（幂等）。返回 ``suite_id`` 与
    ``memory_dataset_revision_id``。
    """
    from nanobot.evaluation.repository import EvaluationRepository

    if sample_id not in MEMORY_SAMPLE_IDS:
        raise ValueError(f"SAMPLE_NOT_FOUND {sample_id}")
    metadata = MEMORY_SAMPLE_METADATA[sample_id]
    repository = EvaluationRepository(database)

    tag = _memory_suite_tag(sample_id)
    suite_id = ""
    for suite in repository.list_suites():
        if tag in (suite.get("tags") or []):
            suite_id = str(suite["id"])
            break
    if not suite_id:
        suite = repository.create_suite(
            {
                "name": str(metadata["name"]),
                "description": str(metadata["description"]),
                "tags": [tag, *[str(item) for item in metadata["tags"]]],
                "base_revision": None,
            }
        )
        suite_id = str(suite["id"])

    path = memory_sample_path(sample_id)
    with path.open("rb") as handle:
        result = import_dataset(
            database,
            suite_id=suite_id,
            dataset_version=str(metadata["dataset_version"]),
            source=memory_sample_source(sample_id),
            stream=handle,
            format="jsonl",
        )
    return {
        "suite_id": suite_id,
        "memory_dataset_revision_id": result["dataset_revision_id"],
        "dataset_revision_id": result["dataset_revision_id"],
        "dataset_version": str(metadata["dataset_version"]),
        "status": result["status"],
        "scenario_count": int(result["scenario_count"]),
        "split": str(metadata["split"]),
    }
