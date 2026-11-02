"""WU-06：内置记忆题库的内容质量（契约 §1/§6，plan §8 Task 6 Step 3）。

题库的价值全在内容：复制粘贴充数的集合比没有集合更糟——它会让回归分数看起来稳定。
因此这里不只数条数，还锁：

1. **形状**：core 120 条、10 类各 12 条、ID 唯一；smoke 20 条且是另一个集合；
   calibration 独立模板/人物、正反例齐全、gold 默认 proposed。
2. **可校验**：每条都能被 WU-01 冻结的 ``MemoryScenario`` 校验通过——
   悬空证据、重复 ID、时间倒退在这一步就会暴露。
3. **不重复**：cluster_id 唯一、模板族跨 split 不重叠、历史文本不重复。
4. **覆盖面**：偏好区分"只偏好不禁止"与"明确禁止"；隔离含允许共享的正例；
   有历史时点、删除后重建、短会话、无 gold 负例。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

import nanobot.evaluation
from nanobot.evaluation.memory.models import MemoryScenario
from nanobot.evaluation.sample_cases import (
    ADAPTED_SAMPLE_IDS,
    MEMORY_SAMPLE_IDS,
    import_memory_sample,
    memory_sample_scenarios,
    memory_sample_source,
)

SAMPLES_DIR = Path(nanobot.evaluation.__file__).parent / "samples"

#: spec §5 的十类（英文标识 → 中文类名）。
CATEGORIES: dict[str, str] = {
    "stable-fact": "稳定事实",
    "preference": "偏好约束",
    "paraphrase": "同义问法",
    "temporal": "时间有效性",
    "correction": "知识纠正",
    "multi-session": "多会话综合",
    "abstention": "信息不足拒答",
    "distractor": "无关信息干扰",
    "forget": "明确遗忘",
    "isolation": "工作区/身份隔离",
}


def load_rows(name: str) -> list[dict[str, Any]]:
    path = SAMPLES_DIR / f"{name}.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture(scope="module")
def core() -> list[dict[str, Any]]:
    return load_rows("memory-core-v1")


@pytest.fixture(scope="module")
def smoke() -> list[dict[str, Any]]:
    return load_rows("memory-smoke-v1")


@pytest.fixture(scope="module")
def calibration() -> list[dict[str, Any]]:
    return load_rows("memory-calibration-v1")


def history_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for event in row["events"]:
        if event.get("type") != "session":
            continue
        for message in event["messages"]:
            parts.append(message["content"])
    return "|".join(parts)


# ------------------------------------------------------------------ 形状


def test_core_covers_ten_categories() -> None:
    path = Path("nanobot/evaluation/samples/memory-core-v1.jsonl")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    counts = Counter(row["tags"][0] for row in rows)
    assert len(rows) == 120
    assert len(counts) == 10 and set(counts.values()) == {12}
    assert len({row["scenario_id"] for row in rows}) == 120


def test_core_categories_match_spec(core: list[dict[str, Any]]) -> None:
    counts = Counter(row["tags"][0] for row in core)
    assert set(counts) == set(CATEGORIES)
    assert set(counts.values()) == {12}


def test_smoke_is_a_separate_development_set(smoke: list[dict[str, Any]]) -> None:
    assert len(smoke) == 20
    assert {row["split"] for row in smoke} == {"development"}
    assert len({row["scenario_id"] for row in smoke}) == 20
    counts = Counter(row["tags"][0] for row in smoke)
    assert set(counts) == set(CATEGORIES)


def test_core_is_regression_and_calibration_is_calibration(
    core: list[dict[str, Any]], calibration: list[dict[str, Any]]
) -> None:
    assert {row["split"] for row in core} == {"regression"}
    assert {row["split"] for row in calibration} == {"calibration"}


# ------------------------------------------------------------------ 可校验


@pytest.mark.parametrize("name", ["memory-core-v1", "memory-smoke-v1", "memory-calibration-v1"])
def test_every_builtin_row_validates(name: str) -> None:
    for row in load_rows(name):
        scenario = MemoryScenario.model_validate(row)
        assert scenario.scenario_id


def test_evidence_points_at_real_messages(core: list[dict[str, Any]]) -> None:
    for row in core:
        known = {
            message["id"]
            for event in row["events"]
            if event.get("type") == "session"
            for message in event["messages"]
        }
        for probe_id, gold in row["gold"].items():
            if probe_id in ("annotation_scope", "extraction_facts", "state_expectations"):
                continue
            for fact in gold.get("required_facts", []) + gold.get("forbidden_current_values", []):
                assert fact["evidence_message_ids"], f"{row['scenario_id']} 证据为空"
                for message_id in fact["evidence_message_ids"]:
                    assert message_id in known, f"{row['scenario_id']} 悬空证据 {message_id}"


def test_gold_keys_match_probe_ids(core: list[dict[str, Any]]) -> None:
    for row in core:
        probe_ids = {probe["id"] for probe in row["probes"]}
        gold_ids = {
            key
            for key in row["gold"]
            if key not in ("annotation_scope", "extraction_facts", "state_expectations")
        }
        assert gold_ids == probe_ids


# ------------------------------------------------------------------ 不重复


def test_cluster_ids_are_unique(core: list[dict[str, Any]], smoke: list[dict[str, Any]]) -> None:
    clusters = [row["cluster_id"] for row in core + smoke]
    assert len(set(clusters)) == len(clusters)


def test_history_text_is_not_duplicated(core: list[dict[str, Any]]) -> None:
    texts = [history_text(row) for row in core]
    assert len(set(texts)) == len(texts)


def test_template_family_never_crosses_splits(
    core: list[dict[str, Any]], smoke: list[dict[str, Any]], calibration: list[dict[str, Any]]
) -> None:
    families: dict[str, set[str]] = {}
    for row in core + smoke + calibration:
        families.setdefault(row["template_family"], set()).add(row["split"])
    leaking = {name: splits for name, splits in families.items() if len(splits) > 1}
    assert leaking == {}


def test_subject_namespaces_never_cross_splits(
    core: list[dict[str, Any]], smoke: list[dict[str, Any]], calibration: list[dict[str, Any]]
) -> None:
    subjects: dict[str, set[str]] = {}
    for row in core + smoke + calibration:
        subjects.setdefault(row["scope"]["subject_id"], set()).add(row["split"])
    leaking = {name: splits for name, splits in subjects.items() if len(splits) > 1}
    assert leaking == {}


def test_builtin_sets_use_synthetic_identifiers(
    core: list[dict[str, Any]], smoke: list[dict[str, Any]], calibration: list[dict[str, Any]]
) -> None:
    for row in core + smoke + calibration:
        assert row["scope"]["subject_id"].startswith("synthetic-")
        assert row["scope"]["workspace_id"].startswith("synthetic-")


# ------------------------------------------------------------------ 覆盖面


def test_every_category_has_positive_and_negative(core: list[dict[str, Any]]) -> None:
    by_category: dict[str, set[str]] = {}
    for row in core:
        polarity = "negative" if "negative" in row["tags"] else "positive"
        by_category.setdefault(row["tags"][0], set()).add(polarity)
    for category, polarities in by_category.items():
        assert polarities == {"positive", "negative"}, category


def test_preference_distinguishes_soft_preference_and_prohibition(
    core: list[dict[str, Any]],
) -> None:
    preferences = [row for row in core if row["tags"][0] == "preference"]
    soft = [
        row
        for row in preferences
        if not any(
            gold.get("forbidden_current_values")
            for key, gold in row["gold"].items()
            if key not in ("annotation_scope", "extraction_facts", "state_expectations")
        )
    ]
    prohibited = [
        row
        for row in preferences
        if any(
            gold.get("forbidden_current_values")
            for key, gold in row["gold"].items()
            if key not in ("annotation_scope", "extraction_facts", "state_expectations")
        )
    ]
    assert len(soft) >= 3
    assert len(prohibited) >= 3


def test_isolation_category_contains_allowed_shared_positive(core: list[dict[str, Any]]) -> None:
    isolation = [row for row in core if row["tags"][0] == "isolation"]
    shared = [row for row in isolation if "shared-allowed" in row["tags"]]
    assert shared, "隔离类必须有允许共享的正例"
    for row in shared:
        gold = row["gold"][row["probes"][0]["id"]]
        assert gold["required_facts"]


def test_temporal_category_records_valid_intervals(core: list[dict[str, Any]]) -> None:
    temporal = [row for row in core if row["tags"][0] == "temporal"]
    with_interval = [
        row
        for row in temporal
        if any(
            fact.get("valid_from") or fact.get("valid_to")
            for key, gold in row["gold"].items()
            if key not in ("annotation_scope", "extraction_facts", "state_expectations")
            for fact in gold.get("required_facts", [])
        )
    ]
    assert len(with_interval) >= 6


def test_forget_category_covers_delete_and_rebuild(core: list[dict[str, Any]]) -> None:
    forget = [row for row in core if row["tags"][0] == "forget"]
    with_forget = [
        row for row in forget if any(event.get("type") == "forget" for event in row["events"])
    ]
    with_rebuild = [
        row for row in forget if any(event.get("type") == "rebuild" for event in row["events"])
    ]
    assert len(with_forget) >= 6
    assert len(with_rebuild) >= 3


def test_abstention_category_has_goldless_negative(core: list[dict[str, Any]]) -> None:
    abstention = [row for row in core if row["tags"][0] == "abstention"]
    goldless = [
        row
        for row in abstention
        if any(
            gold.get("answerability") == "unanswerable" and not gold.get("required_facts")
            for key, gold in row["gold"].items()
            if key not in ("annotation_scope", "extraction_facts", "state_expectations")
        )
    ]
    assert len(goldless) >= 6


def test_short_session_scenarios_exist(core: list[dict[str, Any]]) -> None:
    short = [row for row in core if "short-session" in row["tags"]]
    assert len(short) >= 2
    for row in short:
        first = next(event for event in row["events"] if event.get("type") == "session")
        assert len(first["messages"]) <= 2


def test_calibration_is_independent_and_proposed(
    calibration: list[dict[str, Any]], core: list[dict[str, Any]]
) -> None:
    assert len(calibration) >= 20
    core_families = {row["template_family"] for row in core}
    core_subjects = {row["scope"]["subject_id"] for row in core}
    for row in calibration:
        assert row["template_family"] not in core_families
        assert row["scope"]["subject_id"] not in core_subjects
        assert "gold:proposed" in row["tags"]
        assert "gold:confirmed" not in row["tags"]
    counts = Counter(row["tags"][0] for row in calibration)
    assert set(counts) == set(CATEGORIES)
    for category in counts:
        rows = [row for row in calibration if row["tags"][0] == category]
        polarities = {
            "negative" if "negative" in row["tags"] else "positive" for row in rows
        }
        assert polarities == {"positive", "negative"}, category


def test_core_scenarios_do_not_claim_confirmed_gold(core: list[dict[str, Any]]) -> None:
    for row in core:
        assert "gold:confirmed" not in row["tags"]


# ------------------------------------------------------------------ 注册与导入


def test_builtin_memory_samples_are_registered() -> None:
    assert MEMORY_SAMPLE_IDS == frozenset(
        {"memory-smoke-v1", "memory-core-v1", "memory-calibration-v1"}
    )
    # 旧 sample_id 的适配语义不变。
    assert ADAPTED_SAMPLE_IDS == frozenset({"reliability-v2"})


def test_memory_sample_source_declares_provenance() -> None:
    source = memory_sample_source("memory-core-v1")
    assert len(source["sha256"]) == 64
    assert source["loader_version"]
    assert source["grading_protocol"]
    assert source["license"]


def test_import_memory_sample_registers_versioned_revision(evaluation_database: Any) -> None:
    result = import_memory_sample(evaluation_database, "memory-smoke-v1")
    assert result["suite_id"]
    assert result["memory_dataset_revision_id"]
    assert result["scenario_count"] == 20
    assert result["status"] == "ready"


def test_import_memory_sample_is_idempotent(evaluation_database: Any) -> None:
    first = import_memory_sample(evaluation_database, "memory-core-v1")
    second = import_memory_sample(evaluation_database, "memory-core-v1")
    assert first["suite_id"] == second["suite_id"]
    assert first["memory_dataset_revision_id"] == second["memory_dataset_revision_id"]
    assert first["scenario_count"] == 120


def test_memory_sample_scenarios_helper_returns_raw_rows() -> None:
    rows = memory_sample_scenarios("memory-smoke-v1")
    assert len(rows) == 20
    assert rows[0]["schema_version"] == "memory-scenario/v1"
