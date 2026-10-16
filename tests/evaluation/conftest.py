"""评估域共享 fixture（WU-01 是唯一所有者）。

工厂函数只产出**原始 dict**，字段与取值全部来自契约 §2 / §4，因此 T01 的模型测试与
T02 的仓储测试都能共用同一份「契约合法」定义；provider 一律不落网络（``tool_mode``
固定 ``fixture``，白名单只含工作区文件工具与受控测试工具）。

后续 WU 需要新 fixture 时在自己的测试文件里定义局部 fixture，不并行修改本文件。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from typing import Any

import pytest

DEFAULT_TOOL_MODE = "fixture"
DEFAULT_ALLOWED_TOOLS = ["read_file", "write_file", "edit_file", "list_dir"]


def _uid() -> str:
    return str(uuid.uuid4())


def make_rubric_payload(**overrides: Any) -> dict[str, Any]:
    """契约 §2 Rubric：五档锚点齐全、维度 1–6、pass_score 0–4（默认 3）。"""
    payload: dict[str, Any] = {
        "version": "general-v1",
        "required": True,
        "dimensions": [
            {
                "id": "task_completion",
                "name": "任务完成",
                "anchors": [
                    "完全没有完成任务",
                    "部分完成但遗漏主要要求",
                    "基本完成存在明显缺口",
                    "完整完成且结果可用",
                    "完整完成且质量优秀",
                ],
                "pass_score": 3,
            },
            {
                "id": "correctness",
                "name": "正确性",
                "anchors": [
                    "结果全错",
                    "关键结论错误",
                    "存在一处非关键错误",
                    "结果正确",
                    "结果正确且解释充分",
                ],
                "pass_score": 3,
            },
        ],
    }
    payload.update(overrides)
    return payload


def make_case_payload(
    *, suite_id: str | None = None, **overrides: Any
) -> dict[str, Any]:
    """契约 §2 CaseWrite 的合法文本任务（单轮、fixture 文件、必过规则齐备）。"""
    payload: dict[str, Any] = {
        "name": "读取 input.txt 并复述 alpha 标识",
        "description": "读取 fixture 文件中的标识并原样复述，不使用网络。",
        "tags": ["basic", "file"],
        "enabled": True,
        # 显式传入的空串必须原样透出（校验非法 UUID 的用例依赖它），只有 None 才补随机值。
        "suite_id": suite_id if suite_id is not None else _uid(),
        "base_revision": None,
        "turns": [{"content": "读取 input.txt，把里面的标识原样告诉我。"}],
        "fixtures": [{"path": "input.txt", "content": "alpha\n", "encoding": "utf-8"}],
        "memory_seed": [],
        "clock_iso": None,
        "tool_fixtures": [],
        "execution": {
            "tool_mode": DEFAULT_TOOL_MODE,
            "allowed_tools": list(DEFAULT_ALLOWED_TOOLS),
            "max_iterations": 12,
            "timeout_s": 120,
        },
        "assertions": [
            {
                "id": "a1",
                "kind": "answer_contains",
                "required": True,
                "text": "alpha",
                "case_sensitive": True,
            }
        ],
        "rubric": None,
        "artifact_paths": ["output.txt"],
    }
    payload.update(overrides)
    return payload


def make_tool_fixture_case_payload(**overrides: Any) -> dict[str, Any]:
    """受控工具用例（test_lookup 固定结果，同样不触网）。"""
    payload = make_case_payload(
        name="查询北京温度并生成一句答复",
        description="调用 test_lookup 取得固定温度后回答。",
        assertions=[
            {
                "id": "t1",
                "kind": "tool_called",
                "required": True,
                "name": "test_lookup",
                "arguments_subset": {"city": "北京"},
                "min_calls": 1,
                "max_calls": None,
            }
        ],
        tool_fixtures=[
            {
                "tool_name": "test_lookup",
                "arguments_match": {"city": "北京"},
                "result": {"temperature_c": 18},
                "is_error": False,
            }
        ],
    )
    payload["execution"]["allowed_tools"] = [*DEFAULT_ALLOWED_TOOLS, "test_lookup"]
    payload.update(overrides)
    return payload


def make_suite_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "基础回归用例集",
        "description": "覆盖问答与文件读写的基础集合。",
        "tags": ["basic-v1"],
        "base_revision": None,
    }
    payload.update(overrides)
    return payload


def make_run_payload(
    case_ids: Sequence[str], *, suite_id: str | None = None, **overrides: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        # 显式传入的空串必须原样透出（校验非法 UUID 的用例依赖它），只有 None 才补随机值。
        "suite_id": suite_id if suite_id is not None else _uid(),
        "case_ids": list(case_ids),
        "agent_profile_id": "agent-profile",
        "judge_profile_id": None,
        "limits": {"concurrency": 1, "max_iterations": 12, "timeout_s": 120},
    }
    payload.update(overrides)
    return payload


def make_import_payload(case_count: int = 2) -> dict[str, Any]:
    """契约 §3 ``/suites/import`` 请求体（schema_version=1，忽略旧 id 并重建关联）。"""
    suite = make_suite_payload()
    cases: list[dict[str, Any]] = []
    for index in range(case_count):
        case = make_case_payload(name=f"用例 {index + 1:03d}")
        case["id"] = _uid()  # 旧 id：导入必须忽略并重新生成
        cases.append(case)
    return {"schema_version": 1, "suite": suite, "cases": cases}


def make_json_pointer_assertion(**overrides: Any) -> dict[str, Any]:
    """file_json 断言：SchemaSubset + RFC 6901 JSON Pointer 检查。"""
    payload: dict[str, Any] = {
        "id": "j1",
        "kind": "file_json",
        "required": True,
        "path": "summary.json",
        "schema": {
            "type": "object",
            "properties": {"total": {"type": "integer"}, "name": {"type": "string"}},
            "required": ["total"],
            "additionalProperties": False,
        },
        "checks": [{"pointer": "/total", "equals": 42}],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def case_payload_factory() -> Callable[..., dict[str, Any]]:
    return make_case_payload


@pytest.fixture
def tool_case_payload_factory() -> Callable[..., dict[str, Any]]:
    return make_tool_fixture_case_payload


@pytest.fixture
def suite_payload_factory() -> Callable[..., dict[str, Any]]:
    return make_suite_payload


@pytest.fixture
def run_payload_factory() -> Callable[..., dict[str, Any]]:
    return make_run_payload


@pytest.fixture
def rubric_factory() -> Callable[..., dict[str, Any]]:
    return make_rubric_payload


@pytest.fixture
def import_payload_factory() -> Callable[..., dict[str, Any]]:
    return make_import_payload


@pytest.fixture
def new_uid() -> Callable[[], str]:
    return _uid


@pytest.fixture
def evaluation_database(tmp_path: Any):
    """T02 仓储测试用：库文件只落 pytest ``tmp_path``，绝不触碰真实数据目录。

    延迟导入，保证 T01 阶段（database 模块尚未实现）不阻塞本文件收集。
    """
    from nanobot.evaluation.database import EvaluationDatabase

    database = EvaluationDatabase(tmp_path / "evaluation.db")
    database.init_schema()
    return database


@pytest.fixture
def evaluation_repository(evaluation_database):
    from nanobot.evaluation.repository import EvaluationRepository

    return EvaluationRepository(evaluation_database)
