"""记忆召回加工作区过滤的性能基线（一次性调研脚本）。

目的：在真实 schema 上量化「FTS5 召回 + workspace_id 过滤」相对「纯 FTS5 召回」的
额外开销，评估是否会影响首轮对话延迟。

运行：.venv/Scripts/python.exe .ai-runtime-artifacts/research/2026-10-27-memory-recall-bench.py
"""
from __future__ import annotations

import sqlite3
import statistics
import time
from pathlib import Path

TMP = Path(__file__).parent / "_bench_tmp"

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY, content TEXT NOT NULL, type TEXT NOT NULL DEFAULT 'fact',
        priority TEXT NOT NULL DEFAULT 'long_term', source TEXT NOT NULL DEFAULT 'manual',
        importance_score REAL NOT NULL DEFAULT 0.5, access_count INTEGER NOT NULL DEFAULT 0,
        tags TEXT NOT NULL DEFAULT '[]', subject TEXT NOT NULL DEFAULT '', predicate TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 0.5, decay_rate REAL NOT NULL DEFAULT 0.1, expires_at TEXT,
        last_accessed_at TEXT, superseded_by TEXT, source_episode_id TEXT,
        scope TEXT NOT NULL DEFAULT 'global', scope_owner TEXT NOT NULL DEFAULT '',
        agent_id TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL DEFAULT 'default',
        workspace_id TEXT NOT NULL DEFAULT 'default', metadata TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_memories_owner ON memories(workspace_id, user_id, scope, scope_owner)",
    """CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        content, subject, predicate, tags, content='memories', content_rowid='rowid',
        tokenize='unicode61 remove_diacritics 2')""",
    """CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, content, subject, predicate, tags)
        VALUES (new.rowid, new.content, new.subject, new.predicate, new.tags); END""",
]

SELECT_ALL = ", ".join(f"m.{c.strip()}" for c in """
    id, content, type, priority, source, importance_score, access_count, tags, subject, predicate,
    confidence, decay_rate, expires_at, last_accessed_at, superseded_by, source_episode_id,
    scope, scope_owner, agent_id, user_id, workspace_id, metadata, created_at, updated_at
""".split(","))

UNFILTERED_SQL = (
    f"SELECT {SELECT_ALL} FROM memories m JOIN memories_fts f ON f.rowid = m.rowid "
    "WHERE memories_fts MATCH ? ORDER BY rank, m.importance_score DESC LIMIT 90"
)
FILTERED_SQL = (
    f"SELECT {SELECT_ALL} FROM memories m JOIN memories_fts f ON f.rowid = m.rowid "
    "WHERE memories_fts MATCH ? AND m.workspace_id IN (?, ?) "
    "ORDER BY rank, m.importance_score DESC LIMIT 90"
)


def build(rows: int, workspaces: int) -> Path:
    TMP.mkdir(parents=True, exist_ok=True)
    db_path = TMP / f"bench_{rows}_{workspaces}.db"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    for stmt in SCHEMA:
        conn.execute(stmt)
    now = "2026-10-27T00:00:00+00:00"
    payload = []
    for i in range(rows):
        ws = "default" if i % workspaces == 0 else f"ws-{i % workspaces:02d}"
        payload.append((
            f"m-{i}", f"项目{i} 创作灵感 长期记忆记录 {i}", "fact", "long_term", "extraction",
            0.5, 0, "[]", "", "", 0.5, 0.1, None, None, None, None,
            "global", "", "", "default", ws, "{}", now, now,
        ))
    conn.executemany(
        "INSERT INTO memories VALUES (" + ",".join("?" * 24) + ")", payload,
    )
    conn.commit()
    conn.close()
    return db_path


def timeit(conn: sqlite3.Connection, sql: str, params: tuple, repeat: int = 20) -> tuple[float, int]:
    samples: list[float] = []
    hits = 0
    for _ in range(repeat):
        start = time.perf_counter()
        rows = conn.execute(sql, params).fetchall()
        samples.append((time.perf_counter() - start) * 1000)
        hits = len(rows)
    return statistics.median(samples), hits


def main() -> None:
    print(f"{'rows':>7} {'ws':>4} {'unfiltered ms':>14} {'filtered ms':>12} {'delta':>8} {'hit(u/f)':>12}")
    for rows in (1_000, 10_000, 50_000):
        for workspaces in (1, 10):
            db_path = build(rows, workspaces)
            conn = sqlite3.connect(db_path)
            base, hits_u = timeit(conn, UNFILTERED_SQL, ("创作灵感",))
            filt, hits_f = timeit(conn, FILTERED_SQL, ("创作灵感", "default", "ws-01"))
            delta = filt - base
            print(f"{rows:>7} {workspaces:>4} {base:>14.2f} {filt:>12.2f} {delta:>8.2f} {hits_u:>5}/{hits_f:<6}")
            conn.close()
    conn = sqlite3.connect(TMP / "bench_50000_10.db")
    print("\nEXPLAIN (unfiltered):")
    for row in conn.execute("EXPLAIN QUERY PLAN " + UNFILTERED_SQL, ("创作灵感",)):
        print("  ", row[-1])
    print("EXPLAIN (filtered):")
    for row in conn.execute("EXPLAIN QUERY PLAN " + FILTERED_SQL, ("创作灵感", "default", "ws-01")):
        print("  ", row[-1])
    conn.close()


if __name__ == "__main__":
    main()
