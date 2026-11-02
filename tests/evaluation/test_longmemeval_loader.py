"""WU-06：LongMemEval 本地导入适配（契约 §1/§6，plan §8 Task 6 Step 4）。

锁五件事：

1. **只读本地上传文件**：不接受远程 URL，不执行上传的评分脚本。
2. **保留官方字段**：question_id / 题型 / 时间 / answer / evidence_sessions / has_answer
   全部留在评估侧私有对象里。
3. **has_answer 绝不出现在 Worker 可见载荷**：执行载荷与场景 JSON 里都没有它，
   无答案题只以 gold 的 answerability/expected_absence 表达。
4. **可追溯**：锁定 cleaned revision / hash 与原始 ID 清单；分层子集带 subset 标识。
5. **官方二值 Judge 的版本化内置适配**：按题型给出协议与协议 hash；报告口径明确
   不是官方分数，内部 F1 不冒充官方分。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.evaluation.memory.longmemeval import (
    LONGMEMEVAL_JUDGE_PROTOCOL,
    LONGMEMEVAL_LOADER_VERSION,
    LongMemEvalDataset,
    judge_longmemeval_answer,
    load_longmemeval,
    select_stratified_subset,
)
from nanobot.evaluation.memory.models import MemoryScenario, build_execution_input

# ------------------------------------------------------------------ 夹具


def _session(session_id: str, date: str, turns: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "date": date,
        "turns": [{"role": role, "content": content} for role, content in turns],
    }


@pytest.fixture
def lme_file(tmp_path: Path) -> Path:
    """自建微型 fixture：形状贴近公开原格式，不含任何公开数据正文。"""
    payload = [
        {
            "question_id": "lme-0001",
            "question_type": "single-session-user",
            "question": "我最喜欢的饮品是什么？",
            "answer": "乌龙茶",
            "has_answer": True,
            "answer_session_ids": ["sess-a"],
            "haystack_sessions": [
                _session(
                    "sess-a",
                    "2023/05/20 (Sat) 02:21:38",
                    [
                        ("user", "我最近只喝乌龙茶，别的一律不喝。"),
                        ("assistant", "记下了，你只喝乌龙茶。"),
                    ],
                ),
                _session(
                    "sess-b",
                    "2023/05/21 (Sun) 10:00:00",
                    [
                        ("user", "今天天气不错。"),
                        ("assistant", "是的。"),
                    ],
                ),
            ],
        },
        {
            "question_id": "lme-0002",
            "type": "multi-session",
            "question": "我上次提到的项目叫什么？",
            "answer": "",
            "has_answer": False,
            "evidence_sessions": [["sess-c"]],
            "haystack_sessions": [
                _session(
                    "sess-c",
                    "2023/06/01 (Thu) 09:00:00",
                    [
                        ("user", "我们先把上线时间定下来。"),
                        ("assistant", "好的。"),
                    ],
                ),
            ],
        },
        {
            "question_id": "lme-0003",
            "question_type": "knowledge-update",
            "question": "我现在住在哪里？",
            "answer": "云栖镇",
            "has_answer": True,
            "answer_session_ids": ["sess-d"],
            "haystack_sessions": [
                _session(
                    "sess-d",
                    "2023-07-02T08:30:00",
                    [
                        ("user", "我以前住在白鹭湾，现在搬到云栖镇了。"),
                        ("assistant", "好的，以云栖镇为准。"),
                    ],
                ),
            ],
        },
    ]
    path = tmp_path / "longmemeval-mini.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ------------------------------------------------------------------ 加载


def test_load_longmemeval_reads_local_file_and_keeps_official_fields(
    lme_file: Path,
) -> None:
    dataset = load_longmemeval(lme_file)
    assert isinstance(dataset, LongMemEvalDataset)
    assert dataset.loader_version == LONGMEMEVAL_LOADER_VERSION
    assert dataset.original_ids == ["lme-0001", "lme-0002", "lme-0003"]
    assert dataset.cleaned_revision.startswith("longmemeval-cleaned/")
    assert len(dataset.cleaned_sha256) == 64
    assert len(dataset.uploaded_sha256) == 64

    item = dataset.items[0]
    assert item.question_id == "lme-0001"
    assert item.question_type == "single-session-user"
    assert item.answer == "乌龙茶"
    assert item.has_answer is True
    assert item.evidence_session_ids == ("sess-a",)
    # 时间已解析成带时区的时间戳（datetime），不是原始字符串。
    assert item.sessions[0].at.isoformat().startswith("2023-05-20")


def test_load_longmemeval_rejects_remote_url() -> None:
    with pytest.raises(ValueError) as excinfo:
        load_longmemeval("https://example.com/longmemeval.json")  # type: ignore[arg-type]
    assert "REMOTE" in str(excinfo.value).upper()


def test_load_longmemeval_never_executes_uploaded_script(tmp_path: Path) -> None:
    sentinel = tmp_path / "executed.txt"
    payload = [
        {
            "question_id": "lme-0001",
            "question_type": "single-session-user",
            "question": "我最喜欢什么？",
            "answer": "风筝",
            "has_answer": True,
            "answer_session_ids": ["sess-a"],
            "evaluation_script": f"open({str(sentinel)!r}, 'w').write('x')",
            "haystack_sessions": [
                _session("sess-a", "2023/05/20 (Sat) 02:21:38", [("user", "我喜欢风筝。")])
            ],
        }
    ]
    path = tmp_path / "with-script.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    dataset = load_longmemeval(path)
    assert not sentinel.exists()
    assert any("SCRIPT" in item["code"] for item in dataset.diagnostics)


def test_load_longmemeval_ignores_answer_of_unanswerable_item(tmp_path: Path) -> None:
    payload = [
        {
            "question_id": "lme-9001",
            "question_type": "single-session-user",
            "question": "我的车停在几号位？",
            "answer": "B12",
            "has_answer": False,
            "haystack_sessions": [
                _session("sess-a", "2023/05/20 (Sat) 02:21:38", [("user", "今天堵车。")])
            ],
        }
    ]
    path = tmp_path / "leaky.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    dataset = load_longmemeval(path)
    scenario = dataset.scenarios[0]
    assert "B12" not in json.dumps(scenario, ensure_ascii=False)
    gold = scenario["gold"]["q1"]
    assert gold["answerability"] == "unanswerable"
    assert gold["expected_absence"] is True
    assert gold["required_facts"] == []


# ------------------------------------------------------------------ gold 隔离


def test_has_answer_never_reaches_worker_payload(lme_file: Path) -> None:
    dataset = load_longmemeval(lme_file)
    for raw in dataset.scenarios:
        scenario = MemoryScenario.model_validate(raw)
        execution = build_execution_input(scenario, track="end_to_end")
        payload = execution.model_dump(mode="json")
        assert "has_answer" not in json.dumps(payload, ensure_ascii=False)
        assert "has_answer" not in json.dumps(raw, ensure_ascii=False)
        assert "gold" not in payload


def test_unanswerable_item_gold_is_private_and_marked(lme_file: Path) -> None:
    dataset = load_longmemeval(lme_file)
    unanswerable = dataset.scenarios[1]
    assert unanswerable["gold"]["q1"]["answerability"] == "unanswerable"
    assert unanswerable["gold"]["q1"]["expected_absence"] is True
    assert unanswerable["gold"]["q1"]["required_facts"] == []
    # 场景本体（执行端可见部分）不含答案文本
    assert "answer" not in json.dumps(
        {key: value for key, value in unanswerable.items() if key != "gold"},
        ensure_ascii=False,
    )


def test_evidence_sessions_become_gold_evidence(lme_file: Path) -> None:
    dataset = load_longmemeval(lme_file)
    scenario = MemoryScenario.model_validate(dataset.scenarios[0])
    fact = scenario.gold.probes["q1"].required_facts[0]
    assert fact.value == "乌龙茶"
    assert fact.evidence_message_ids
    known = {
        message.id
        for event in scenario.events
        if getattr(event, "type", None) == "session"
        for message in event.messages
    }
    assert set(fact.evidence_message_ids) <= known


# ------------------------------------------------------------------ 子集与可追溯


def test_stratified_subset_keeps_full_original_id_list(lme_file: Path) -> None:
    dataset = load_longmemeval(lme_file)
    subset = select_stratified_subset(dataset, per_type=1)
    assert subset.subset is not None
    assert subset.subset.startswith("stratified-")
    # 每题型取 per_type=1 个：fixture 里有 3 个不同题型（single-session-user /
    # multi-session / knowledge-update）。
    assert len(subset.items) == 3
    # 原始 ID 清单仍然是整份文件的，不是子集的
    assert subset.original_ids == ["lme-0001", "lme-0002", "lme-0003"]
    for raw in subset.scenarios:
        assert any(tag.startswith("subset:") for tag in raw["tags"])


def test_dataset_source_metadata_is_import_ready(lme_file: Path) -> None:
    dataset = load_longmemeval(lme_file)
    source = dataset.source_metadata()
    assert source["sha256"] == dataset.cleaned_sha256
    assert source["original_ids"] == dataset.original_ids
    assert source["loader_version"] == LONGMEMEVAL_LOADER_VERSION
    assert source["grading_protocol"] == LONGMEMEVAL_JUDGE_PROTOCOL


# ------------------------------------------------------------------ 二值 Judge


def test_binary_judge_is_versioned_and_records_protocol_hash(lme_file: Path) -> None:
    dataset = load_longmemeval(lme_file)
    assert dataset.judge_protocol == LONGMEMEVAL_JUDGE_PROTOCOL
    assert len(dataset.judge_protocol_hash) == 64

    verdict = judge_longmemeval_answer(dataset.items[0], "你最喜欢乌龙茶。")
    assert verdict.status == "correct"
    assert verdict.protocol_id == LONGMEMEVAL_JUDGE_PROTOCOL
    assert verdict.protocol_hash == dataset.judge_protocol_hash


def test_binary_judge_marks_unanswerable_item_correct_only_on_abstention(
    lme_file: Path,
) -> None:
    dataset = load_longmemeval(lme_file)
    item = dataset.items[1]
    assert judge_longmemeval_answer(item, "你没有提到过这个项目。").status == "correct"
    assert judge_longmemeval_answer(item, "项目叫星桥计划。").status == "incorrect"


def test_binary_judge_protocol_is_not_official_score(lme_file: Path) -> None:
    dataset = load_longmemeval(lme_file)
    assert dataset.official_score is False
    verdict = judge_longmemeval_answer(dataset.items[0], "乌龙茶")
    assert verdict.metric_id == "longmemeval_binary_judge"
    assert verdict.official is False
