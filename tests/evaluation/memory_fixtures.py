"""记忆专项测试的共享原始数据（只返回原始 dict，不含任何 fixture 语义）。

与 ``conftest.py`` 的公共语义**不重叠**：本文件只服务记忆专项用例，产出的是
契约 §1/§7/§8 的原始载荷，供 ``test_memory_schemas.py`` 与
``test_memory_repository.py`` 各自校验。禁止在这里放 pytest fixture
（共享 fixture 会改公共语义），需要 fixture 的测试在自己的文件里定义。
"""

from __future__ import annotations

from typing import Any


def make_memory_scope(**overrides: Any) -> dict[str, Any]:
    """契约 §1：scope 只映射到本次 trial 新建的合成作用域。"""
    payload: dict[str, Any] = {
        "subject_id": "synthetic-u1",
        "workspace_id": "synthetic-w1",
    }
    payload.update(overrides)
    return payload


def make_memory_scenario(**overrides: Any) -> dict[str, Any]:
    """契约 §1 的最小场景（plan §3 给出的示意数据，逐字沿用）。

    字段：schema_version / scenario_id / revision / split / template_family /
    cluster_id / tags / scope / events / probes / gold。
    """
    payload: dict[str, Any] = {
        "schema_version": "memory-scenario/v1",
        "scenario_id": "fact-001",
        "revision": 1,
        "split": "regression",
        "template_family": "fact-city-a",
        "cluster_id": "conversation-a",
        "tags": ["fact", "cross_session"],
        "scope": make_memory_scope(),
        "events": [
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
            {"type": "settle", "after_session": "s1"},
        ],
        "probes": [
            {
                "id": "q1",
                "question": "我住在哪个城市？",
                "fresh_session": True,
                "at": "2026-11-02T09:00:00+08:00",
            }
        ],
        "gold": {
            "q1": {
                "required_facts": [
                    {"key": "city", "value": "青岚城", "evidence_message_ids": ["m1"]}
                ],
                "answerability": "answerable",
            }
        },
    }
    payload.update(overrides)
    return payload


def make_memory_dataset_source(**overrides: Any) -> dict[str, Any]:
    """契约 §1：来源与 gold 版本不可变。"""
    payload: dict[str, Any] = {
        "repo_url": "https://example.invalid/zhixu/memory-core-v1",
        "revision": "r1",
        "sha256": "a" * 64,
        "license": "CC-BY-4.0",
        "original_ids": ["core-001", "core-002"],
        "loader_version": "longmemeval-loader/1",
        "grading_protocol": "memory-grading/v1",
    }
    payload.update(overrides)
    return payload


def make_memory_dataset_payload(
    *, suite_id: str, scenarios: list[dict[str, Any]] | None = None, **overrides: Any
) -> dict[str, Any]:
    """一次数据集修订的导入载荷（契约 §1 的 ``MemoryDatasetRevision`` 形状）。"""
    payload: dict[str, Any] = {
        "suite_id": suite_id,
        "dataset_version": "memory-core-v1",
        "source": make_memory_dataset_source(),
        "scenarios": [make_memory_scenario()] if scenarios is None else scenarios,
    }
    payload.update(overrides)
    return payload


def make_memory_candidate(**overrides: Any) -> dict[str, Any]:
    """契约 §2 的候选输入：只允许能落到真实配置的字段。"""
    payload: dict[str, Any] = {
        "id": "cand-current",
        "name": "当前候选",
        "mode": "zhixu",
        "agent_profile_id": "agent-profile",
        "extractor_profile_id": "extractor-profile",
        "memory_settings": {
            "extraction_enabled": True,
            "extraction_strategy_version": "extraction/v1",
            "merge_strategy_version": "merge/v1",
            "retrieval_channels": ["episodes", "semantic"],
            "retrieval_weights": {"episodes": 0.4, "semantic": 0.6},
            "retrieval_threshold": 0.3,
            "recall_limit": 10,
            "injection_budget_tokens": 2000,
            "embedding_model": "bge-small-zh",
            "index_version": "index/v1",
        },
    }
    payload.update(overrides)
    return payload


def make_memory_case_payload(
    *, suite_id: str, dataset_revision_id: str, **overrides: Any
) -> dict[str, Any]:
    """契约 §1 的专项 case：turns/assertions 为空，靠 memory_scenario_ref 指路。"""
    payload: dict[str, Any] = {
        "name": "记忆专项：跨会话事实召回",
        "description": "专项 case 只保存 ref，执行载荷在评估服务侧解析。",
        "tags": ["memory"],
        "enabled": True,
        "suite_id": suite_id,
        "base_revision": None,
        "turns": [],
        "fixtures": [],
        "memory_seed": [],
        "clock_iso": None,
        "tool_fixtures": [],
        "execution": {
            "tool_mode": "fixture",
            "allowed_tools": ["read_file", "write_file", "edit_file", "list_dir"],
            "max_iterations": 12,
            "timeout_s": 120,
        },
        "assertions": [],
        "rubric": None,
        "script": [],
        "artifact_paths": [],
        "memory_scenario_ref": {
            "dataset_revision_id": dataset_revision_id,
            "scenario_id": "fact-001",
            "revision": 1,
        },
        "memory_required_metrics": ["qa_accuracy", "grounded_memory_success"],
    }
    payload.update(overrides)
    return payload


def make_memory_review_ref(**overrides: Any) -> dict[str, Any]:
    """契约 §4：逐 probe 复核引用。"""
    payload: dict[str, Any] = {
        "scenario_id": "fact-001",
        "probe_id": "q1",
        "metric_id": "qa_accuracy",
        "grading_revision": 1,
    }
    payload.update(overrides)
    return payload
