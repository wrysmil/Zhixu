"""工作区过滤不得让 FTS5 查询计划退化成「索引驱动 + 逐行全扫 FTS5」。

背景（2026-10-27 性能审查 P1）：工作区可见集合过滤最初以普通 ``JOIN`` 拼进
FTS5 查询，SQLite 会改用 ``idx_memories_owner`` 作为驱动表，对命中集里的每一行
再全量扫一次 FTS5 虚表。实测 5 万行 / 单工作区：25ms → 4867ms。

修法是 ``CROSS JOIN``（SQLite 里 CROSS JOIN 锁死连接顺序），强制 FTS5 始终驱动，
工作区过滤降级为行级判定。本文件锁定该不变量。

断言对象是**生产路径实际执行的带参 SQL**（用连接代理记录 ``execute(sql, params)``）：
SQLite 的查询计划会因 ``LIMIT ?`` 与 ``LIMIT 45`` 字面量而不同，只看展开后的
语句会漏判。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.repository import search_memories, search_semantic_scored

_ROWS = 200
_WORKSPACES = 10


class _RecordingConnection:
    """记录 ``execute`` 的 SQL 与参数，其余行为转发给真实连接。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        self.statements.append((sql, tuple(params) if params else ()))
        return self._conn.execute(sql, params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def _seeded_db(tmp_path: Path) -> MemoryDatabase:
    db = MemoryDatabase(tmp_path)
    db.ensure_schema()
    with db.connect() as conn:
        conn.executemany(
            "INSERT INTO memories (id, content, type, priority, source, importance_score,"
            " access_count, tags, subject, predicate, confidence, decay_rate, expires_at,"
            " last_accessed_at, superseded_by, source_episode_id, scope, scope_owner,"
            " agent_id, user_id, workspace_id, metadata, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    f"m-{index}",
                    f"创作灵感 共享内容 {index}",
                    "fact",
                    "long_term",
                    "manual",
                    0.5,
                    0,
                    "[]",
                    "",
                    "",
                    0.5,
                    0.1,
                    None,
                    None,
                    None,
                    None,
                    "global",
                    "",
                    "default",
                    "default",
                    f"ws-{index % _WORKSPACES}",
                    "{}",
                    "2026-10-27T00:00:00+00:00",
                    "2026-10-27T00:00:00+00:00",
                )
                for index in range(_ROWS)
            ],
        )
        conn.commit()
    return db


def _fts_statement(recorder: _RecordingConnection) -> tuple[str, tuple]:
    matches = [
        (sql, params)
        for sql, params in recorder.statements
        if "MEMORIES_FTS" in sql.upper() and "SELECT" in sql.upper()
    ]
    assert matches, "未捕获到生产 FTS5 查询"
    return matches[0]


def _plan_of(conn: sqlite3.Connection, sql: str, params: tuple) -> list[str]:
    rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return [str(row["detail"]) for row in rows]


def _assert_fts_drives(plan: list[str]) -> None:
    assert any(
        "memories_fts" in detail.lower() and detail.startswith("SCAN") for detail in plan
    ), f"FTS5 虚表必须是驱动表，实际计划：{plan}"
    assert not any("idx_memories_owner" in detail for detail in plan), (
        f"工作区索引不得成为驱动表（否则退化成逐行全扫 FTS5），实际计划：{plan}"
    )


def test_semantic_scored_keeps_fts_as_driver_under_workspace_filter(tmp_path: Path) -> None:
    db = _seeded_db(tmp_path)
    with db.connect() as conn:
        recorder = _RecordingConnection(conn)
        search_semantic_scored(
            recorder, "创作灵感", limit=45, workspace_ids=("ws-0",)
        )
        sql, params = _fts_statement(recorder)
        _assert_fts_drives(_plan_of(conn, sql, params))


def test_search_memories_keeps_fts_as_driver_under_workspace_filter(tmp_path: Path) -> None:
    db = _seeded_db(tmp_path)
    with db.connect() as conn:
        recorder = _RecordingConnection(conn)
        search_memories(recorder, "创作灵感", limit=45, workspace_ids=("ws-0",))
        sql, params = _fts_statement(recorder)
        _assert_fts_drives(_plan_of(conn, sql, params))


def test_workspace_filter_still_excludes_other_workspaces(tmp_path: Path) -> None:
    """计划修正不得改变可见性语义：过滤后只剩本工作区的行。"""
    db = _seeded_db(tmp_path)
    with db.connect() as conn:
        rows = search_semantic_scored(conn, "创作灵感", limit=45, workspace_ids=("ws-0",))
        assert rows, "应召回 ws-0 的记忆"
        assert {memory.workspace_id for memory, _score in rows} == {"ws-0"}

        scoped = search_memories(conn, "创作灵感", limit=45, workspace_ids=("ws-0",))
        assert scoped
        assert {memory.workspace_id for memory in scoped} == {"ws-0"}
