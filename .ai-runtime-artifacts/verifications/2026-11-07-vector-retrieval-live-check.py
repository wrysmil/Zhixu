"""离线核对：真实 embedding 下「纯词法」与「词法+向量 RRF」的召回差异。

不启动网关、不调用 LLM，只跑检索链路，用于给「向量默认开启」提供实测证据。
运行：.venv/bin/python .ai-runtime-artifacts/verifications/2026-11-07-vector-retrieval-live-check.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

from loguru import logger

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.experience import list_experiences, record_experience
from nanobot.memory.retrieval.engine import RetrievalEngine
from nanobot.memory.retrieval.store_adapter import MemoryStoreAdapter
from nanobot.memory.vector.indexer import (
    EpisodeVectorIndexer,
    set_active_episode_indexer,
)
from nanobot.memory.vector.settings import VectorSettings
from nanobot.memory.vector.store import VectorStore

ROOT = Path(__file__).resolve().parent / "2026-11-07-vector-retrieval-live"
WORKSPACE_ID = "default"

logger.remove()
logger.add(lambda m: print(f"[log] {m.strip()}", flush=True), level="WARNING")


def seed(db: MemoryDatabase) -> list:
    common = {"workspace_id": WORKSPACE_ID, "session_id": "s-vec-check"}
    episodes = [
        record_experience(
            db,
            **common,
            title="探针服务只监听 IPv6 回环导致客户端 connection refused",
            kind="pitfall",
            symptom="check.py 访问 127.0.0.1:8137 报 Connection refused，但服务进程存在",
            root_cause="app.py 默认绑定 ::1，只监听 IPv6 回环",
            fix="绑定改为 127.0.0.1:8137 或双栈监听，并按地址族选择 AF_INET/AF_INET6",
            verification="check.py 返回 200，RAVEN-42",
            tags=["8137", "IPv6", "connection refused"],
        ),
        record_experience(
            db,
            **common,
            title="Redis 容器内错连 localhost 而不是服务名",
            kind="pitfall",
            symptom="容器里的应用连 Redis 报连接被拒绝",
            root_cause="容器内 localhost 指向容器自身，且端口写成 6387",
            fix="连接串改为 redis-q7:6379",
            verification="容器内 ping 通，应用启动成功",
            tags=["redis", "docker", "6379"],
        ),
        record_experience(
            db,
            **common,
            title="系统 pip 装包与 uv 隔离环境不一致导致导入失败",
            kind="pitfall",
            symptom="测试文件已安装 httpx，但运行时报 ModuleNotFoundError",
            root_cause="依赖装在系统 Python，测试跑在 uv 虚拟环境",
            fix="在项目内执行 uv add --dev httpx，不要再用系统 pip",
            verification="uv run pytest 12 passed",
            tags=["httpx", "uv", "venv"],
        ),
        record_experience(
            db,
            **common,
            title="Q7 探针标准检查流程",
            kind="skill",
            capability="在新环境确认探针服务可用",
            prerequisites=["Python 3.11", "uv"],
            steps=[
                "确认服务进程与监听地址",
                "用 check.py 访问 /ready-q7",
                "比对返回的 RAVEN-42",
                "失败时区分客户端端口与服务端绑定地址",
            ],
        ),
        record_experience(
            db,
            **common,
            title="健康端点保留 /ready-q7 而不是改成 /health",
            kind="decision",
            context="新项目要统一健康端点路径",
            options=[{"name": "/health"}, {"name": "/ready-q7"}],
            choice="/ready-q7",
            rationale="旧探针代号 RAVEN-42 依赖该路径，改名会打断既有监控",
        ),
        record_experience(
            db,
            **common,
            title="Q7 探针排障手册",
            kind="reference",
            pointer="runbooks/q7-probe.md",
            what_for="记录探针端口、IPv6 绑定与 Redis 连接的排查顺序",
        ),
    ]
    return episodes


QUERIES = [
    ("精确关键词", "8137 connection refused 怎么修"),
    ("同义改写-无原词", "服务明明在跑，客户端却连不上，该查什么"),
    ("跨语言", "redis container cannot connect to localhost"),
    ("现象抽象", "包装好了还是导不进来"),
    ("文档位置", "探针排障手册放在哪个文件"),
    ("决策理由", "为什么健康检查不用 /health"),
]


def bullets(block: str) -> list[str]:
    return [
        line.strip()
        for line in block.splitlines()
        if line.strip().startswith("- ")
    ]


async def run_engine(engine: RetrievalEngine, query: str) -> dict:
    started = time.perf_counter()
    block, ids = await engine.retrieve_with_ids(
        query=query,
        recent_messages=[],
        workspace_ids=[WORKSPACE_ID],
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        "block": block,
        "bullets": bullets(block),
        "memory_ids": ids,
        "elapsed_ms": elapsed_ms,
    }


async def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)

    db = MemoryDatabase(ROOT, db_path=ROOT / "state.db")
    seed(db)

    settings = VectorSettings(enabled=True, index_path=str(ROOT / "chromadb"))
    store = VectorStore(settings, workspace=ROOT)
    deadline = time.monotonic() + 180
    while store.state in ("idle", "loading") and time.monotonic() < deadline:
        await asyncio.sleep(0.25)
    if store.state != "ready":
        raise SystemExit(f"vector store not ready: state={store.state} error={store.error}")

    indexer = EpisodeVectorIndexer(store, db)
    set_active_episode_indexer(indexer)
    sync = indexer.sync_from_sqlite()

    lexical_engine = RetrievalEngine(store=MemoryStoreAdapter(db), brain=None)
    lexical_rows = {}
    set_active_episode_indexer(None)
    for label, query in QUERIES:
        lexical_rows[label] = await run_engine(lexical_engine, query)

    set_active_episode_indexer(indexer)
    vector_engine = RetrievalEngine(
        store=MemoryStoreAdapter(db, vector_store=store), brain=None
    )
    vector_rows = {}
    for label, query in QUERIES:
        vector_rows[label] = await run_engine(vector_engine, query)

    report = {
        "workspace": WORKSPACE_ID,
        "episodes": len(list_experiences(db, workspace_ids=[WORKSPACE_ID])),
        "vector_sync": sync,
        "vector_state": store.state,
        "vector_model": store.model_name,
        "vector_dimensions": store.dimensions,
        "vector_episode_count": len(store.list_episode_ids()),
        "queries": [
            {
                "label": label,
                "query": query,
                "lexical": lexical_rows[label],
                "vector": vector_rows[label],
            }
            for label, query in QUERIES
        ],
    }
    (ROOT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"episodes={report['episodes']} vector_count={report['vector_episode_count']} "
          f"model={report['vector_model']} dim={report['vector_dimensions']}")
    for row in report["queries"]:
        print(f"\n=== {row['label']} | {row['query']} ===")
        for mode in ("lexical", "vector"):
            data = row[mode]
            print(f"-- {mode} ({data['elapsed_ms']}ms, {len(data['bullets'])} 条)")
            for line in data["bullets"]:
                print(f"   {line}")


if __name__ == "__main__":
    asyncio.run(main())
