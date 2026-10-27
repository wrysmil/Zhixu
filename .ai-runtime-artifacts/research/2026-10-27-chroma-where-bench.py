"""Chroma 向量路的 where 过滤可用性与开销基线（一次性调研脚本）。

用显式 embedding 绕过模型下载，只测「加 workspace 过滤」本身的开销与正确性。

运行：.venv/Scripts/python.exe .ai-runtime-artifacts/research/2026-10-27-chroma-where-bench.py
"""
from __future__ import annotations

import random
import statistics
import time
from pathlib import Path

import chromadb

TMP = Path(__file__).parent / "_bench_tmp_chroma"


def build(rows: int, workspaces: int):
    TMP.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(TMP / f"chroma_{rows}_{workspaces}"))
    try:
        client.delete_collection("bench")
    except Exception:
        pass
    collection = client.create_collection("bench", metadata={"hnsw:space": "cosine"})
    rng = random.Random(7)
    ids, docs, metas, embeddings = [], [], [], []
    for i in range(rows):
        ws = "default" if i % workspaces == 0 else f"ws-{i % workspaces:02d}"
        ids.append(f"m-{i}")
        docs.append(f"项目{i} 创作灵感 长期记忆记录 {i}")
        metas.append({"workspace_id": ws, "type": "fact", "importance": 0.5})
        embeddings.append([rng.random() for _ in range(16)])
    for start in range(0, rows, 1000):
        collection.add(
            ids=ids[start:start + 1000],
            documents=docs[start:start + 1000],
            metadatas=metas[start:start + 1000],
            embeddings=embeddings[start:start + 1000],
        )
    return collection


def timeit(collection, where, query_vec, repeat=10):
    samples = []
    hits = 0
    # 预热：排除首次构建元数据索引的一次性成本
    collection.query(query_embeddings=[query_vec], n_results=90, where=where)
    for _ in range(repeat):
        start = time.perf_counter()
        res = collection.query(query_embeddings=[query_vec], n_results=90, where=where)
        samples.append((time.perf_counter() - start) * 1000)
        hits = len(res["ids"][0])
    return statistics.median(samples), hits


def main() -> None:
    rng = random.Random(11)
    query_vec = [rng.random() for _ in range(16)]
    print(f"{'rows':>7} {'ws':>4} {'no-where ms':>13} {'where ms':>10} {'delta':>8} {'hit(u/f)':>12}")
    for rows in (1_000, 10_000):
        for workspaces in (1, 10):
            collection = build(rows, workspaces)
            base, hits_u = timeit(collection, None, query_vec)
            filt, hits_f = timeit(
                collection,
                {"workspace_id": {"$in": ["default", "ws-01"]}},
                query_vec,
            )
            print(f"{rows:>7} {workspaces:>4} {base:>13.2f} {filt:>10.2f} {filt - base:>8.2f} {hits_u:>5}/{hits_f:<6}")
            # 正确性：过滤结果不得越界
            res = collection.query(
                query_embeddings=[query_vec], n_results=90,
                where={"workspace_id": {"$in": ["default", "ws-01"]}},
            )
            got = {m["workspace_id"] for m in res["metadatas"][0]}
            assert got <= {"default", "ws-01"}, got
            print(f"        过滤结果工作区集合: {sorted(got)}")


if __name__ == "__main__":
    main()
