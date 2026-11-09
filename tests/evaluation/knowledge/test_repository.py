"""知识评测仓储往返：数据集版本、运行、结果与回收。

锁死的几条硬约束（改口径 = 改契约，必须先改契约）：

1. **数据集乐观锁**：``update_dataset`` 必须带 ``base_revision``；版本不符抛
   ``KNOWLEDGE_DATASET_REVISION_CONFLICT``，绝不静默覆盖别人的编辑。
2. **结果行主键是 ``(run_id, case_id)``**，``case_id`` 是 ``case_id::config_id``
   复合形式；重复写入是 upsert，不产生重复行。
3. **JSON 列保留中文原文**（``ensure_ascii=False``），缺证据的 ``None`` 原样保留。
4. **删数据集幂等**：删两次不报错；删完再读返回 ``None``。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from nanobot.evaluation.knowledge.repository import (
    KnowledgeEvalError,
    KnowledgeEvalRepository,
)


def _repo(tmp_path: Path) -> KnowledgeEvalRepository:
    repo = KnowledgeEvalRepository(tmp_path / "evaluation.db")
    repo.init_schema()
    return repo


def _dataset(repo: KnowledgeEvalRepository) -> dict[str, Any]:
    return repo.create_dataset(
        name="检索基线",
        notes="首版",
        cases=[{"id": "c1", "query": "回滚", "expect_any": ["回滚"]}],
    )


# --------------------------------------------------------------------------- 数据集


def test_dataset_round_trip(tmp_path: Path) -> None:
    """建 → 读回：name / notes / cases 原样（含中文）。"""
    repo = _repo(tmp_path)
    dataset = _dataset(repo)

    loaded = repo.get_dataset(dataset["id"])
    assert loaded is not None
    assert loaded["name"] == "检索基线"
    assert loaded["notes"] == "首版"
    assert loaded["revision"] == 1
    assert loaded["cases"][0]["id"] == "c1"
    assert loaded["cases"][0]["query"] == "回滚"
    # label_source 由服务端推导：expect_any 非空 → expect。
    assert loaded["cases"][0]["label_source"] == "expect"
    assert loaded["created_at"] == dataset["created_at"]
    assert loaded["updated_at"] == dataset["updated_at"]


def test_get_dataset_returns_none_for_unknown_id(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert repo.get_dataset("不存在") is None


def test_list_datasets_returns_all(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _dataset(repo)
    repo.create_dataset(name="第二个", cases=[{"id": "c1", "query": "部署"}])

    datasets = repo.list_datasets()
    assert len(datasets) == 2
    assert {item["name"] for item in datasets} == {"检索基线", "第二个"}


def test_update_dataset_bumps_revision(tmp_path: Path) -> None:
    """版本号自增，改名生效。"""
    repo = _repo(tmp_path)
    dataset = _dataset(repo)

    updated = repo.update_dataset(dataset["id"], base_revision=dataset["revision"], name="新名")
    assert updated["name"] == "新名"
    assert updated["revision"] == dataset["revision"] + 1

    loaded = repo.get_dataset(dataset["id"])
    assert loaded is not None
    assert loaded["name"] == "新名"
    assert loaded["revision"] == dataset["revision"] + 1


def test_update_dataset_with_stale_revision_conflicts(tmp_path: Path) -> None:
    """过期 base_revision 抛带码异常，且**不落库**。"""
    repo = _repo(tmp_path)
    dataset = _dataset(repo)
    repo.update_dataset(dataset["id"], base_revision=dataset["revision"], name="新名")

    with pytest.raises(KnowledgeEvalError) as excinfo:
        repo.update_dataset(dataset["id"], base_revision=dataset["revision"], name="再改")
    assert excinfo.value.code == "KNOWLEDGE_DATASET_REVISION_CONFLICT"

    loaded = repo.get_dataset(dataset["id"])
    assert loaded is not None
    assert loaded["name"] == "新名"


def test_update_dataset_with_unknown_id_raises(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(KnowledgeEvalError) as excinfo:
        repo.update_dataset("不存在", base_revision=1, name="新名")
    assert excinfo.value.code == "KNOWLEDGE_DATASET_NOT_FOUND"


def test_update_dataset_replaces_cases(tmp_path: Path) -> None:
    """cases 是整表替换语义，不是增量。"""
    repo = _repo(tmp_path)
    dataset = _dataset(repo)

    updated = repo.update_dataset(
        dataset["id"],
        base_revision=dataset["revision"],
        cases=[{"id": "c2", "query": "部署"}],
    )
    assert [case["id"] for case in updated["cases"]] == ["c2"]


def test_delete_dataset_is_idempotent(tmp_path: Path) -> None:
    """删两次不报错；删完再读返回 None。"""
    repo = _repo(tmp_path)
    dataset = _dataset(repo)

    repo.delete_dataset(dataset["id"])
    repo.delete_dataset(dataset["id"])
    assert repo.get_dataset(dataset["id"]) is None


def test_delete_dataset_removes_its_runs(tmp_path: Path) -> None:
    """连带删掉运行与结果，避免外键悬挂。"""
    repo = _repo(tmp_path)
    dataset = _dataset(repo)
    run = repo.create_run(dataset_id=dataset["id"], configs=[{"id": "base"}])
    repo.save_result(
        run_id=run["id"],
        case_id="c1::base",
        status="scored",
        label_source="expect",
        hits=[{"chunk_id": "chk_1", "rank": 1, "score": 0.9}],
        metrics={"hit_at_k": 1.0},
    )

    repo.delete_dataset(dataset["id"])
    assert repo.get_run(run["id"]) is None


# --------------------------------------------------------------------------- 运行与结果


def test_create_run_returns_queued(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    dataset = _dataset(repo)

    run = repo.create_run(
        dataset_id=dataset["id"],
        configs=[{"id": "base"}],
        judge_config={"judge_profile_id": "jp_1"},
        budgets={"max_queries": 10},
    )
    assert run["status"] == "queued"
    assert run["dataset_id"] == dataset["id"]
    assert run["configs"] == [{"id": "base"}]
    assert run["created_at"]

    loaded = repo.get_run(run["id"])
    assert loaded is not None
    assert loaded["status"] == "queued"
    assert loaded["budgets"] == {"max_queries": 10}


def test_create_run_with_unknown_dataset_raises(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(KnowledgeEvalError) as excinfo:
        repo.create_run(dataset_id="不存在", configs=[{"id": "base"}])
    assert excinfo.value.code == "KNOWLEDGE_DATASET_NOT_FOUND"


def test_list_runs_filters_by_dataset(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = _dataset(repo)
    second = repo.create_dataset(name="第二个", cases=[{"id": "c1", "query": "部署"}])
    repo.create_run(dataset_id=first["id"], configs=[{"id": "base"}])
    repo.create_run(dataset_id=second["id"], configs=[{"id": "base"}])

    assert len(repo.list_runs()) == 2
    assert len(repo.list_runs(dataset_id=first["id"])) == 1


def test_run_and_results_round_trip(tmp_path: Path) -> None:
    """建 run → 写结果 → 收尾 → 报告里 status / metrics / results 都对。"""
    repo = _repo(tmp_path)
    dataset = _dataset(repo)
    run = repo.create_run(dataset_id=dataset["id"], configs=[{"id": "base"}])

    repo.save_result(
        run_id=run["id"],
        case_id="c1::base",
        status="scored",
        label_source="expect",
        hits=[{"chunk_id": "chk_1", "rank": 1, "score": 0.9, "content_hash": "h1"}],
        metrics={"hit_at_k": 1.0, "recall_at_k": None},
    )
    repo.finish_run(run["id"], status="completed", metrics={"expect": {"scored_queries": 1}})

    report = repo.get_run_report(run["id"])
    assert report["status"] == "completed"
    assert report["metrics"] == {"expect": {"scored_queries": 1}}
    assert report["results"][0]["metrics"]["hit_at_k"] == 1.0
    assert report["results"][0]["metrics"]["recall_at_k"] is None


def test_run_report_result_fields(tmp_path: Path) -> None:
    """逐题段每项都带 case_id / status / label_source / hits / metrics。"""
    repo = _repo(tmp_path)
    dataset = _dataset(repo)
    run = repo.create_run(dataset_id=dataset["id"], configs=[{"id": "base"}])
    repo.save_result(
        run_id=run["id"],
        case_id="c1::base",
        status="not_assessable",
        label_source="judge",
        hits=[],
        metrics={"hit_at_k": None},
        error="POOL_HAS_NO_RELEVANT",
    )

    result = repo.get_run_report(run["id"])["results"][0]
    assert result["case_id"] == "c1::base"
    assert result["status"] == "not_assessable"
    assert result["label_source"] == "judge"
    assert result["hits"] == []
    assert result["metrics"] == {"hit_at_k": None}
    assert result["error"] == "POOL_HAS_NO_RELEVANT"


def test_save_result_is_upsert(tmp_path: Path) -> None:
    """同一 (run_id, case_id) 重复写入覆盖，不产生第二行。"""
    repo = _repo(tmp_path)
    dataset = _dataset(repo)
    run = repo.create_run(dataset_id=dataset["id"], configs=[{"id": "base"}])

    repo.save_result(
        run_id=run["id"],
        case_id="c1::base",
        status="scored",
        label_source="expect",
        hits=[],
        metrics={"hit_at_k": 0.0},
    )
    repo.save_result(
        run_id=run["id"],
        case_id="c1::base",
        status="scored",
        label_source="expect",
        hits=[],
        metrics={"hit_at_k": 1.0},
    )

    results = repo.get_run_report(run["id"])["results"]
    assert len(results) == 1
    assert results[0]["metrics"]["hit_at_k"] == 1.0


def test_finish_run_records_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    dataset = _dataset(repo)
    run = repo.create_run(dataset_id=dataset["id"], configs=[{"id": "base"}])

    repo.finish_run(run["id"], status="failed", metrics={}, error="BOOM")

    loaded = repo.get_run(run["id"])
    assert loaded is not None
    assert loaded["status"] == "failed"
    assert loaded["error"] == "BOOM"
    assert loaded["finished_at"]


def test_get_run_report_for_unknown_run_raises(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(KnowledgeEvalError) as excinfo:
        repo.get_run_report("不存在")
    assert excinfo.value.code == "KNOWLEDGE_RUN_NOT_FOUND"


# --------------------------------------------------------------------------- 残留回收


def test_mark_interrupted_runs_marks_running(tmp_path: Path) -> None:
    """服务重启后把残留 running 的运行置 interrupted。"""
    repo = _repo(tmp_path)
    dataset = _dataset(repo)
    run = repo.create_run(dataset_id=dataset["id"], configs=[{"id": "base"}])

    with sqlite3.connect(tmp_path / "evaluation.db") as conn:
        conn.execute(
            "UPDATE knowledge_eval_runs SET status = 'running' WHERE id = ?", (run["id"],)
        )
        conn.commit()

    assert repo.mark_interrupted_runs() == 1
    loaded = repo.get_run(run["id"])
    assert loaded is not None
    assert loaded["status"] == "interrupted"
    # 已经收尾的不再重复处理。
    assert repo.mark_interrupted_runs() == 0
