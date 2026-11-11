"""在真实 Neo4j 后端上跑「图关 / 图开」两次知识检索评测并对比。"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from nanobot.config.loader import load_config
from nanobot.evaluation.database import EvaluationDatabase
from nanobot.evaluation.profiles import build_profiles_from_config
from nanobot.evaluation.repository import EvaluationRepository
from nanobot.evaluation.services import EvaluationService, default_profiles

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "eval-data"
KB_NAME = "Graph E2E"
CASES = [
    {"id": "c1", "query": "谁负责灰度发布流程的回滚", "expect_any": ["值班二号"], "limit": 1},
    {"id": "c2", "query": "灰度发布流程由哪个平台编排", "expect_any": ["发布平台"], "limit": 1},
    {"id": "c3", "query": "灰度发布先放多少流量", "expect_any": ["5%"], "limit": 1},
    {"id": "c4", "query": "值班二号负责什么", "expect_any": ["回滚"], "limit": 1},
]


def _service(config):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    database = EvaluationDatabase(DATA_DIR / "evaluation.db")
    database.init_schema()
    repository = EvaluationRepository(database)
    profiles = (*build_profiles_from_config(config), *default_profiles())
    return EvaluationService(
        repository=repository,
        data_dir=DATA_DIR,
        profiles=profiles,
    )


async def _wait(service, run_id: str, timeout: float = 300.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = service.knowledge_get_run(run_id)
        if str(run.get("status")) in {"completed", "failed", "cancelled"}:
            return run
        await asyncio.sleep(0.5)
    raise TimeoutError(run_id)


async def main(config_path: str) -> int:
    from nanobot.knowledge.service import get_knowledge_service, reset_knowledge_service

    config = load_config(Path(config_path))
    reset_knowledge_service()
    knowledge = get_knowledge_service(config.knowledge)
    kb = next(
        kb for kb in knowledge.list_kbs(workspace_ids=["default"]) if kb.name == KB_NAME
    )
    service = _service(config)

    for dataset in service.knowledge_list_datasets():
        if dataset.get("name") == "Graph E2E 评测":
            service.knowledge_delete_dataset(str(dataset["id"]))
    cases = [dict(case, kb_ids=[kb.id]) for case in CASES]
    dataset = service.knowledge_create_dataset(
        {"name": "Graph E2E 评测", "notes": "真实 Neo4j", "cases": cases}
    )
    print(json.dumps({"dataset": dataset["id"], "cases": len(cases)}, ensure_ascii=False))

    async def one(label: str, overrides: dict) -> str:
        run = service.knowledge_create_run(
            {
                "dataset_id": dataset["id"],
                "configs": [{"id": label, "name": label, "overrides": overrides}],
                "budgets": {
                    "max_queries": 20,
                    "max_judge_calls": 1,
                    "per_query_timeout_s": 60,
                    "concurrency": 1,
                },
            },
            config=config,
        )
        finished = await _wait(service, str(run["id"]))
        report = service.knowledge_run_report(str(run["id"]))
        metrics = (report.get("metrics") or {}).get("expect", {}).get("metrics", {})
        print(
            json.dumps(
                {
                    "run": label,
                    "id": run["id"],
                    "status": finished.get("status"),
                    "error": finished.get("error"),
                    "metrics": metrics,
                    "pool": report.get("pool"),
                    "routes": [
                        (result.get("hits") or [{}])[0]
                        if result.get("status") == "ok"
                        else {"status": result.get("status")}
                        for result in (report.get("results") or [])
                    ],
                },
                ensure_ascii=False,
            )
        )
        return str(run["id"])

    off_id = await one("graph_off", {"graph_enabled": False})
    on_id = await one("graph_on", {"graph_enabled": True, "graph_weight": 0.5})
    compare = service.knowledge_compare(off_id, on_id)
    print(json.dumps({"compare": compare}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(
        asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "config.json")))
    )
