"""SQLite CRUD 操作：memories / episodes / scratchpad。

Public API surface (all take a sqlite3.Connection as first arg):
    memories:
        add_memory, get_memory, list_memories, search_memories,
        update_memory, delete_memory
    episodes:
        add_episode, get_episode, list_episodes, list_episodes_by_session,
        update_episode, delete_episode
    scratchpad:
        upsert_scratchpad, get_scratchpad
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import Any, Literal

from loguru import logger

from nanobot.memory.models import (
    Episode,
    EpisodeOutcome,
    EpisodeSource,
    ExtractionState,
    Memory,
    MemoryPriority,
    MemoryType,
    ScratchpadEntry,
)

# ---------- memories ----------

_INSERT_MEMORY_SQL = """
    INSERT INTO memories (
        id, content, type, priority, source,
        importance_score, access_count, tags, subject, predicate,
        confidence, decay_rate, expires_at, last_accessed_at,
        superseded_by, source_episode_id,
        scope, scope_owner, agent_id, user_id, workspace_id, metadata,
        created_at, updated_at
    ) VALUES (
        :id, :content, :type, :priority, :source,
        :importance_score, :access_count, :tags, :subject, :predicate,
        :confidence, :decay_rate, :expires_at, :last_accessed_at,
        :superseded_by, :source_episode_id,
        :scope, :scope_owner, :agent_id, :user_id, :workspace_id, :metadata,
        :created_at, :updated_at
    )
"""


def add_memory(conn: sqlite3.Connection, memory: Memory) -> None:
    """插入一条 memory 行。"""
    conn.execute(_INSERT_MEMORY_SQL, memory.to_row())


_SELECT_MEMORY_COLUMNS = """
    id, content, type, priority, source,
    importance_score, access_count, tags, subject, predicate,
    confidence, decay_rate, expires_at, last_accessed_at,
    superseded_by, source_episode_id,
    scope, scope_owner, agent_id, user_id, workspace_id, metadata,
    created_at, updated_at
"""


def _row_to_memory(row: sqlite3.Row) -> Memory:
    return Memory(
        id=row['id'],
        content=row['content'],
        created_at=row['created_at'],
        updated_at=row['updated_at'],
        type=MemoryType(row['type']),
        priority=MemoryPriority(row['priority']),
        source=row['source'],
        importance_score=row['importance_score'],
        access_count=row['access_count'],
        tags=json.loads(row['tags']) if row['tags'] else [],
        subject=row['subject'],
        predicate=row['predicate'],
        confidence=row['confidence'],
        decay_rate=row['decay_rate'],
        expires_at=row['expires_at'],
        last_accessed_at=row['last_accessed_at'],
        superseded_by=row['superseded_by'],
        source_episode_id=row['source_episode_id'],
        scope=row['scope'],
        scope_owner=row['scope_owner'],
        agent_id=row['agent_id'],
        user_id=row['user_id'],
        workspace_id=row['workspace_id'],
        metadata=json.loads(row['metadata']) if row['metadata'] else {},
    )


def get_memory(conn: sqlite3.Connection, memory_id: str) -> Memory | None:
    flat = " ".join(_SELECT_MEMORY_COLUMNS.split())
    row = conn.execute(
        f"SELECT {flat} FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()
    return _row_to_memory(row) if row else None


def _workspace_ids_condition(
    column: str, workspace_ids: Sequence[str] | None
) -> tuple[str, list[Any]]:
    """可见集合 → (SQL 条件, 参数)。

    ``None`` 表示不过滤（管理面 / 全量读）；空元组表示"什么都看不到"，必须返回
    恒假条件而不是退化成不过滤——否则会泄漏其他工作区的记忆。
    """
    if workspace_ids is None:
        return "", []
    ids = [str(item) for item in workspace_ids if str(item)]
    if not ids:
        return "0 = 1", []
    placeholders = ", ".join("?" for _ in ids)
    return f"{column} IN ({placeholders})", ids


def list_memories(
    conn: sqlite3.Connection,
    *,
    type: MemoryType | None = None,
    workspace_id: str | None = None,
    workspace_ids: Sequence[str] | None = None,
    order_by: Literal["created", "importance"] = "created",
    limit: int | None = None,
    scope: str | None = None,
    min_importance: float | None = None,
) -> list[Memory]:
    clauses: list[str] = []
    params: list[Any] = []
    if type is not None:
        clauses.append("type = ?")
        params.append(type.value)
    if workspace_id is not None:
        clauses.append("workspace_id = ?")
        params.append(workspace_id)
    condition, condition_params = _workspace_ids_condition("workspace_id", workspace_ids)
    if condition:
        clauses.append(condition)
        params.extend(condition_params)
    if scope is not None:
        clauses.append("scope = ?")
        params.append(scope)
    if min_importance is not None:
        clauses.append("importance_score >= ?")
        params.append(min_importance)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order_sql = {
        "created": "created_at DESC",
        "importance": "importance_score DESC",
    }[order_by]
    limit_sql = f" LIMIT {int(limit)}" if limit is not None else ""
    flat = " ".join(_SELECT_MEMORY_COLUMNS.split())
    sql = (
        f"SELECT {flat} FROM memories "
        f"{where} ORDER BY {order_sql}{limit_sql}"
    )
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_memory(r) for r in rows]


def delete_memory(conn: sqlite3.Connection, memory_id: str) -> None:
    conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))


def update_memory_source_episode(
    conn: sqlite3.Connection,
    memory_id: str,
    episode_id: str,
) -> None:
    """反向回填：将 ``Memory.source_episode_id`` 设置为本次 episode 的 id。

    FIX-6 Rev-M-4：阶段 4a 先写 memory 再写 episode，episode 才知道 linked_memory_ids；
    反向回填在 episode 写入成功后逐条把 source_episode_id 写回对应 memory，
    让任一 memory 行都能反查来源 episode。无行被更新（如 memory_id 不存在）时
    ``rowcount == 0``，调用方负责记录 / 忽略。
    """
    cur = conn.execute(
        "UPDATE memories SET source_episode_id = ? WHERE id = ?",
        (episode_id, memory_id),
    )
    if cur.rowcount == 0:
        raise KeyError(f"memory not found: {memory_id}")


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def update_memory(
    conn: sqlite3.Connection,
    memory_id: str,
    *,
    content: str | None = None,
    type: MemoryType | None = None,
    priority: MemoryPriority | None = None,
    importance_score: float | None = None,
    tags: list[str] | None = None,
    subject: str | None = None,
    predicate: str | None = None,
    confidence: float | None = None,
    decay_rate: float | None = None,
    expires_at: str | None = None,
    metadata: dict[str, Any] | None = None,
    workspace_id: str | None = None,
) -> None:
    """Partially update a memory row by id.

    FTS5 triggers keep ``memories_fts`` in sync. Raises ``KeyError`` if the
    row does not exist.
    """
    sets: list[str] = []
    params: list[Any] = []
    now = _now_iso()
    field_map: dict[str, Any] = {
        "content": content,
        "type": type.value if isinstance(type, MemoryType) else type,
        "priority": priority.value if isinstance(priority, MemoryPriority) else priority,
        "importance_score": importance_score,
        "tags": json.dumps(tags, ensure_ascii=False) if tags is not None else None,
        "subject": subject,
        "predicate": predicate,
        "confidence": confidence,
        "decay_rate": decay_rate,
        "expires_at": expires_at,
        "metadata": json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
    }
    for column, value in field_map.items():
        if value is None:
            continue
        sets.append(f"{column} = ?")
        params.append(value)
    if workspace_id is not None:
        sets.append("workspace_id = ?")
        params.append(workspace_id)
    sets.append("updated_at = ?")
    params.append(now)
    params.append(memory_id)
    cur = conn.execute(
        f"UPDATE memories SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    if cur.rowcount == 0:
        raise KeyError(f"memory not found: {memory_id}")


def find_memory_by_subject_predicate(
    conn: sqlite3.Connection,
    subject: str,
    predicate: str,
    *,
    workspace_id: str | None = None,
) -> Memory | None:
    """按 ``subject`` + ``predicate`` 精确查找一条记忆（S3 画像增量合并用）。

    两者任一为空直接返回 ``None``：空 subject/predicate 不具备合并语义，
    必须退化为新建，否则会把互不相关的记忆误判为「同一条」。
    """
    if not subject or not predicate:
        return None
    clauses = ["subject = ?", "predicate = ?"]
    params: list[Any] = [subject, predicate]
    if workspace_id is not None:
        clauses.append("workspace_id = ?")
        params.append(workspace_id)
    flat = " ".join(_SELECT_MEMORY_COLUMNS.split())
    sql = f"SELECT {flat} FROM memories WHERE {' AND '.join(clauses)} LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    return _row_to_memory(row) if row is not None else None


def bump_access_count(
    conn: sqlite3.Connection,
    memory_id: str,
    delta: int = 1,
) -> None:
    """自增一条记忆的 ``access_count``（WU-B 引用评分闭环）。

    由 idle 提取把 LLM 判定 ``useful=true`` 的 ``citation_scores`` 落回
    ``access_count``，reranker 的 ``access_frequency_score``（``log1p(count)/5``）
    随之抬升，让“被证明有用”的记忆在后续检索里排更前。

    刻意**不**动 ``updated_at``：那是 recency 的信号，把“最近有用”画上等号
    会让被证明过一次的记忆同时吃 recency + access 双加成，形成正反馈循环。

    行不存在时静默忽略（记忆可能已在提取间隙被删除），不抛异常。
    """
    conn.execute(
        "UPDATE memories SET access_count = access_count + ? WHERE id = ?",
        (max(0, int(delta)), memory_id),
    )


# ---------- episodes ----------

_INSERT_EPISODE_SQL = """
    INSERT INTO episodes (
        id, session_id, summary, goal, outcome, source,
        started_at, ended_at,
        action_nodes, entities, tools_used, linked_memory_ids, tags,
        importance_score, access_count,
        compaction_checkpoint_id, workspace_snapshot_id, workspace_id, metadata
    ) VALUES (
        :id, :session_id, :summary, :goal, :outcome, :source,
        :started_at, :ended_at,
        :action_nodes, :entities, :tools_used, :linked_memory_ids, :tags,
        :importance_score, :access_count,
        :compaction_checkpoint_id, :workspace_snapshot_id, :workspace_id, :metadata
    )
"""


def add_episode(conn: sqlite3.Connection, episode: Episode) -> None:
    conn.execute(_INSERT_EPISODE_SQL, episode.to_row())


_SELECT_EPISODE_COLUMNS = """
    id, session_id, summary, goal, outcome, source,
    started_at, ended_at,
    action_nodes, entities, tools_used, linked_memory_ids, tags,
    importance_score, access_count,
    compaction_checkpoint_id, workspace_snapshot_id, workspace_id, metadata
"""


def _row_to_episode(row: sqlite3.Row) -> Episode:
    return Episode(
        id=row['id'],
        session_id=row['session_id'],
        summary=row['summary'],
        started_at=row['started_at'],
        ended_at=row['ended_at'],
        goal=row['goal'],
        outcome=EpisodeOutcome(row['outcome']),
        source=EpisodeSource(row['source']),
        action_nodes=json.loads(row['action_nodes']) if row['action_nodes'] else [],
        entities=json.loads(row['entities']) if row['entities'] else [],
        tools_used=json.loads(row['tools_used']) if row['tools_used'] else [],
        linked_memory_ids=json.loads(row['linked_memory_ids']) if row['linked_memory_ids'] else [],
        tags=json.loads(row['tags']) if row['tags'] else [],
        importance_score=row['importance_score'],
        access_count=row['access_count'],
        compaction_checkpoint_id=row['compaction_checkpoint_id'],
        workspace_snapshot_id=row['workspace_snapshot_id'],
        workspace_id=row['workspace_id'],
        metadata=json.loads(row['metadata']) if row['metadata'] else {},
    )


def get_episode(conn: sqlite3.Connection, episode_id: str) -> Episode | None:
    flat = " ".join(_SELECT_EPISODE_COLUMNS.split())
    row = conn.execute(
        f"SELECT {flat} FROM episodes WHERE id = ?",
        (episode_id,),
    ).fetchone()
    return _row_to_episode(row) if row else None


def list_episodes_by_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    limit: int | None = None,
) -> list[Episode]:
    flat = " ".join(_SELECT_EPISODE_COLUMNS.split())
    limit_sql = f" LIMIT {int(limit)}" if limit is not None else ""
    sql = (
        f"SELECT {flat} FROM episodes "
        f"WHERE session_id = ? "
        f"ORDER BY started_at DESC{limit_sql}"
    )
    rows = conn.execute(sql, (session_id,)).fetchall()
    return [_row_to_episode(r) for r in rows]


def list_episodes(
    conn: sqlite3.Connection,
    *,
    session_id: str | None = None,
    workspace_ids: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[Episode]:
    """List episodes, most recent first. Optionally filter by session_id."""
    flat = " ".join(_SELECT_EPISODE_COLUMNS.split())
    clauses: list[str] = []
    params: list[Any] = []
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    condition, condition_params = _workspace_ids_condition("workspace_id", workspace_ids)
    if condition:
        clauses.append(condition)
        params.extend(condition_params)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    limit_sql = f" LIMIT {int(limit)}" if limit is not None else ""
    sql = (
        f"SELECT {flat} FROM episodes {where} "
        f"ORDER BY started_at DESC{limit_sql}"
    )
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_episode(r) for r in rows]


def update_episode(
    conn: sqlite3.Connection,
    episode_id: str,
    *,
    summary: str | None = None,
    goal: str | None = None,
    outcome: EpisodeOutcome | None = None,
    tags: list[str] | None = None,
    importance_score: float | None = None,
) -> None:
    """Partially update an episode row by id. Raises KeyError if missing."""
    sets: list[str] = []
    params: list[Any] = []
    field_map: dict[str, Any] = {
        "summary": summary,
        "goal": goal,
        "outcome": outcome.value if isinstance(outcome, EpisodeOutcome) else outcome,
        "tags": json.dumps(tags, ensure_ascii=False) if tags is not None else None,
        "importance_score": importance_score,
    }
    for column, value in field_map.items():
        if value is None:
            continue
        sets.append(f"{column} = ?")
        params.append(value)
    if not sets:
        return
    params.append(episode_id)
    cur = conn.execute(
        f"UPDATE episodes SET {', '.join(sets)} WHERE id = ?",
        params,
    )
    if cur.rowcount == 0:
        raise KeyError(f"episode not found: {episode_id}")


def delete_episode(conn: sqlite3.Connection, episode_id: str) -> None:
    cur = conn.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))
    if cur.rowcount == 0:
        raise KeyError(f"episode not found: {episode_id}")


# ---------- scratchpad ----------

_UPSERT_SCRATCHPAD_SQL = """
    INSERT INTO scratchpad (
        user_id, workspace_id, content,
        active_projects, current_focus, open_questions, next_steps,
        updated_at
    ) VALUES (
        :user_id, :workspace_id, :content,
        :active_projects, :current_focus, :open_questions, :next_steps,
        :updated_at
    )
    ON CONFLICT (user_id, workspace_id) DO UPDATE SET
        content = excluded.content,
        active_projects = excluded.active_projects,
        current_focus = excluded.current_focus,
        open_questions = excluded.open_questions,
        next_steps = excluded.next_steps,
        updated_at = excluded.updated_at
"""


def upsert_scratchpad(conn: sqlite3.Connection, entry: ScratchpadEntry) -> None:
    conn.execute(_UPSERT_SCRATCHPAD_SQL, entry.to_row())


def get_scratchpad(
    conn: sqlite3.Connection,
    user_id: str,
    workspace_id: str,
) -> ScratchpadEntry | None:
    row = conn.execute(
        "SELECT * FROM scratchpad WHERE user_id = ? AND workspace_id = ?",
        (user_id, workspace_id),
    ).fetchone()
    if row is None:
        return None
    return ScratchpadEntry(
        user_id=row['user_id'],
        workspace_id=row['workspace_id'],
        updated_at=row['updated_at'],
        content=row['content'],
        active_projects=json.loads(row['active_projects']) if row['active_projects'] else [],
        current_focus=row['current_focus'],
        open_questions=json.loads(row['open_questions']) if row['open_questions'] else [],
        next_steps=json.loads(row['next_steps']) if row['next_steps'] else [],
    )


# ---------- search ----------

def search_memories(
    conn: sqlite3.Connection,
    query: str,
    *,
    type: MemoryType | None = None,
    workspace_id: str | None = None,
    workspace_ids: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[Memory]:
    """FTS5 全文检索 memories。

    检索字段: content / subject / predicate / tags。
    排序: BM25 rank（FTS5 内置）；相同 rank 时按 importance_score 降序。

    ``workspace_ids`` 是可见集合（全局 + 当前工作区）；``None`` 表示不过滤，
    空集合表示什么都看不到。
    """
    clauses = ["memories_fts MATCH ?"]
    params: list[Any] = [query]
    if type is not None:
        clauses.append("m.type = ?")
        params.append(type.value)
    if workspace_id is not None:
        clauses.append("m.workspace_id = ?")
        params.append(workspace_id)
    condition, condition_params = _workspace_ids_condition("m.workspace_id", workspace_ids)
    if condition:
        clauses.append(condition)
        params.extend(condition_params)
    where = " AND ".join(clauses)
    limit_sql = f" LIMIT {int(limit)}" if limit is not None else ""
    # Prefix all memories columns with m. to avoid ambiguity with memories_fts.content
    _SELECT_MEMORY_COLUMNS_PREFIXED = ", ".join(
        f"m.{col.strip()}" for col in _SELECT_MEMORY_COLUMNS.split(",")
    )
    # ``CROSS JOIN`` 强制 FTS5 驱动（SQLite 里的 CROSS JOIN 会锁死连接顺序）：
    # 普通 JOIN 下，只要 ``m.workspace_id IN (...)`` 可用索引，SQLite 就会改成
    # 用 ``idx_memories_owner`` 驱动，对命中集里的每一行再全量扫一次 FTS5 虚表
    # —— 实测 5 万行 / 1 个工作区下 25ms → 4867ms。过滤必须发生在 FTS5 召回
    # 之后（FTS5 驱动 + 行级判定），而不是换驱动表。
    sql = (
        f"SELECT {_SELECT_MEMORY_COLUMNS_PREFIXED} "
        f"FROM memories_fts CROSS JOIN memories m ON m.rowid = memories_fts.rowid "
        f"WHERE {where} "
        f"ORDER BY memories_fts.rank, m.importance_score DESC{limit_sql}"
    )
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        # FTS5 语法错误（query 含引号 / 连字符 / 未闭合短语等）不应让整条召回
        # 失败——与 search_backend.Fts5SearchBackend._try_fts5 同策略。
        #
        # 安全审查 M-1（2026-09-15）：原实现无条件吞掉**所有** OperationalError，
        # 会把「表缺失 / database is locked / disk I/O error / readonly / no such
        # column」这类**真实故障**也静默降级成全表 LIKE —— 正是 ``episodes.updated_at``
        # 列名错误得以长期隐藏的机制。现按消息特征区分：查询语法类属预期输入
        # （debug），其余类型升级为 warning（仍继续走 LIKE 回退，不改变召回契约）。
        # 注意 ``no such column`` **不**算语法类：它是 schema 缺陷，必须告警。
        #
        # 安全审查 M-2：不落 query 原文（PII at rest），只记长度。
        if _is_fts_syntax_error(exc):
            logger.debug(
                "FTS5 MATCH syntax error (query length {}): {}", len(query), exc
            )
        else:
            logger.warning(
                "FTS5 MATCH failed, falling back to LIKE (query length {}): {}",
                len(query),
                exc,
            )
        rows = []
    if rows:
        return [_row_to_memory(r) for r in rows]
    # 中文子串回退（RCA 2026-09-15 根因 5）：memories_fts 用
    # tokenize='unicode61'，连续 CJK 被切成一个整 token，因此「创作」永远匹配
    # 不到 content 里的「用户热爱创作，…」。无此回退时中文查询恒空。
    return _search_memories_like(
        conn,
        query,
        type=type,
        workspace_id=workspace_id,
        workspace_ids=workspace_ids,
        limit=limit,
    )


def _escape_like(value: str) -> str:
    """转义 LIKE 元字符（``%`` ``_`` ``\\``），避免 query 语义被通配符劫持。

    不转义时 ``search_memories(conn, "_")`` 会命中**整张表**（模式 ``%_%``
    等价于「任意非空串」），把子串回退退化成全表返回。反斜杠必须最先替换，
    否则会把后续插入的转义符二次转义。
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# FTS5 查询**语法**类错误的特征子串（小写匹配）。命中 → 视为预期输入（debug）；
# 未命中 → 视为库级故障（warning）。``no such column`` 刻意**不**在此列：它是
# schema 缺陷而非查询语法问题，必须告警（``episodes.updated_at`` 之鉴）。
_FTS_SYNTAX_MARKERS = (
    "fts5: syntax error",
    "unterminated string",
    "unknown special query",
    "phrase queries are not supported",
    "no such cursor",
)


def _is_fts_syntax_error(exc: sqlite3.OperationalError) -> bool:
    """区分「FTS5 查询语法问题」（预期输入）与「库级故障」（需告警）。"""
    message = str(exc).lower()
    return any(marker in message for marker in _FTS_SYNTAX_MARKERS)


def _search_memories_like(
    conn: sqlite3.Connection,
    query: str,
    *,
    type: MemoryType | None,
    workspace_id: str | None,
    workspace_ids: Sequence[str] | None = None,
    limit: int | None,
) -> list[Memory]:
    """FTS5 零结果 / 语法错误时的 ``LIKE %query%`` 子串回退。

    为什么必需：``memories_fts`` 使用 ``tokenize='unicode61 remove_diacritics 2'``
    （``database.py`` §memories_fts），对连续 CJK 不做分词，整串是一个 token。
    因此任何**中文子串**查询（「创作」「记忆」）都命中不了 FTS5。
    排序与 ``search_backend.Fts5SearchBackend._fallback_like`` 一致
    （``ORDER BY importance_score DESC``）。

    已知不完整（非回归，2026-09-15 审查记录）：调用方 ``search_memories`` 只在
    FTS **零命中**时才走这里，因此「query 是 A 行的整 token、同时又是 B 行 content
    的子串」时 B 行不会入选（删除无关的 A 行反而会让 B 行出现）。彻底修需要把
    FTS 与 LIKE 两段 SELECT 用 ``UNION`` 合并并定义去重/排序语义，属独立改动。

    与 ``_fallback_like`` 的唯一差异：本函数转义 LIKE 元字符（见 ``_escape_like``），
    且额外支持 ``type`` / ``workspace_id`` 过滤。
    """
    flat = " ".join(_SELECT_MEMORY_COLUMNS.split())
    clauses = ["content LIKE ? ESCAPE '\\'"]
    params: list[Any] = [f"%{_escape_like(query)}%"]
    if type is not None:
        clauses.append("type = ?")
        params.append(type.value)
    if workspace_id is not None:
        clauses.append("workspace_id = ?")
        params.append(workspace_id)
    condition, condition_params = _workspace_ids_condition("workspace_id", workspace_ids)
    if condition:
        clauses.append(condition)
        params.extend(condition_params)
    where = " AND ".join(clauses)
    limit_sql = f" LIMIT {int(limit)}" if limit is not None else ""
    sql = (
        f"SELECT {flat} FROM memories WHERE {where} "
        f"ORDER BY importance_score DESC{limit_sql}"
    )
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_memory(r) for r in rows]


# ---------- session_extraction_state ----------
# WU-A：idle 增量提取的 per-session 游标。表 schema 见 database.py
# ``session_extraction_state``；本节函数全部以 ``conn`` 为第一参数，与既有
# repository 约定一致（无 conn 即不写）。

_INSERT_EXTRACTION_STATE_SQL = """
    INSERT INTO session_extraction_state (
        session_key, last_count, last_source, last_extracted_at, updated_at
    ) VALUES (
        :session_key, :last_count, :last_source, :last_extracted_at, :updated_at
    )
    ON CONFLICT (session_key) DO UPDATE SET
        last_count = excluded.last_count,
        last_source = excluded.last_source,
        last_extracted_at = excluded.last_extracted_at,
        updated_at = excluded.updated_at
"""


def _row_to_extraction_state(row: sqlite3.Row) -> ExtractionState:
    return ExtractionState(
        session_key=row["session_key"],
        last_count=row["last_count"],
        last_source=row["last_source"],
        last_extracted_at=row["last_extracted_at"],
        updated_at=row["updated_at"],
    )


def get_extraction_state(
    conn: sqlite3.Connection,
    session_key: str,
) -> ExtractionState | None:
    """返回该 ``session_key`` 的最新提取游标；不存在返回 ``None``。"""
    row = conn.execute(
        "SELECT session_key, last_count, last_source, last_extracted_at, updated_at "
        "FROM session_extraction_state WHERE session_key = ?",
        (session_key,),
    ).fetchone()
    return _row_to_extraction_state(row) if row else None


def upsert_extraction_state(
    conn: sqlite3.Connection,
    session_key: str,
    last_count: int,
    source: str,
    extracted_at: str,
) -> None:
    """INSERT OR REPLACE（实际走 ON CONFLICT DO UPDATE）写入或更新游标。

    Args:
        session_key: 会话 key（PK）。
        last_count: 本次成功抽取覆盖到的 ``session.messages`` 长度。
        source: 本次抽取的来源标签（例如 ``idle`` / ``deletion``）。
        extracted_at: ISO-8601 UTC 时间戳（同时写 ``last_extracted_at`` 与
            ``updated_at``）。
    """
    conn.execute(
        _INSERT_EXTRACTION_STATE_SQL,
        {
            "session_key": session_key,
            "last_count": int(last_count),
            "last_source": source,
            "last_extracted_at": extracted_at,
            "updated_at": extracted_at,
        },
    )


def reset_extraction_state(conn: sqlite3.Connection, session_key: str) -> None:
    """删除该会话的游标行。无行被删除是合法状态，不报错。"""
    conn.execute(
        "DELETE FROM session_extraction_state WHERE session_key = ?",
        (session_key,),
    )


# ---------- T-12 retrieval adapters ----------
# 注：本段由 Phase 3 GROUP-D T-12 追加；既有代码完全未改。通道函数
# （channels/{semantic,episodes,recent,attachments}.py）期望 store 提供：
#     search_semantic_scored(query, limit) -> list[(Memory, float)]
#     search_episodes(entity, limit)       -> list[_EpisodeRow]
#     query_semantic(*, min_importance, since_days, limit) -> list[_MemoryRow]
#     search_attachments(term, *, intent, limit) -> list[_AttachmentRow]
# 真实实现在此；测试可用 stub 替代（见 tests/memory/retrieval/test_engine.py）。

from dataclasses import dataclass  # T-12: row dataclasses（追加于 T-12）  # noqa: E402


def _search_terms(query: str, keywords: list[str] | None) -> list[str]:
    """构造 FTS5/LIKE 逐词查询词表。

    terms = [query] + keywords（去重、去空、保留 query 首位）。
    query 恒在首位：即便关键词拆解质量差，原始查询仍有一条独立通路，
    保证「新机制不会让任何现有查询变差」。
    """
    seen: set[str] = set()
    result: list[str] = []
    for tok in [query] + (keywords or []):
        tok = tok.strip()
        if tok and tok not in seen:
            seen.add(tok)
            result.append(tok)
    return result


def search_semantic_scored(
    conn: sqlite3.Connection,
    query: str,
    *,
    keywords: list[str] | None = None,
    limit: int = 30,
    workspace_ids: Sequence[str] | None = None,
) -> list[tuple[Memory, float]]:
    """语义召回：FTS5 真实 ``bm25()`` 分（页内归一化）。

    修正记录（2026-09-16）：原实现用 ``_pseudo_bm25_score(float(idx))``，即
    **枚举序号**而非 bm25 —— 首名恒 1.0、次名恒 0.5、第三名 0.333，形成阶梯。
    这在纯 FTS5 场景无害，但向量并集（spec §4.6 的 ``max()`` 融合）会因此
    让 FTS5 首名永远压过向量分（bge 中文短句实测 0.5~0.9），表现为「向量接上了
    但排序毫无变化」。

    归一化刻意用**页内 min-max** 而非 ``1/(1+rank)`` 一类绝对值压缩：FTS5 的
    ``bm25()`` 取值是负的且无界（更相关 = 更负），min-max 只依赖**次序**，
    对符号约定免疫，且保证首名 = 1.0、末名 = 0.0，与向量的 [0,1] 同量纲。
    """
    # 构造逐词查询词表
    terms = _search_terms(query, keywords)

    # FTS5 逐词 OR 短语查询：每个 term 用双引号包裹以中性化特殊字符
    match_expr = " OR ".join(f'"{t}"' for t in terms)

    flat = ", ".join(f"m.{c.strip()}" for c in _SELECT_MEMORY_COLUMNS.split(","))
    condition, condition_params = _workspace_ids_condition("m.workspace_id", workspace_ids)
    where = "memories_fts MATCH ?"
    if condition:
        where = f"{where} AND {condition}"
    # 同 ``search_memories``：``CROSS JOIN`` 强制 FTS5 驱动，避免工作区过滤
    # 把计划换成「索引驱动 + 逐行全扫 FTS5」而放大千倍延迟。
    sql = (
        f"SELECT {flat}, bm25(memories_fts) AS _rank "
        f"FROM memories_fts CROSS JOIN memories m ON m.rowid = memories_fts.rowid "
        f"WHERE {where} "
        f"ORDER BY _rank LIMIT ?"
    )
    try:
        rows = conn.execute(sql, [match_expr, *condition_params, int(limit)]).fetchall()
    except sqlite3.OperationalError as exc:
        if _is_fts_syntax_error(exc):
            logger.debug("FTS5 MATCH syntax error in semantic scored: {}", exc)
        else:
            logger.warning("FTS5 MATCH failed in semantic scored: {}", exc)
        rows = []

    if rows:
        ranks = [float(r["_rank"]) for r in rows]
        lo, hi = min(ranks), max(ranks)
        span = hi - lo
        out: list[tuple[Memory, float]] = []
        for row, rank in zip(rows, ranks, strict=True):
            # rank 越小越相关（FTS5 惯例），故 (hi - rank) / span 使首名 = 1.0
            score = 1.0 if span <= 0.0 else (hi - rank) / span
            out.append((_row_to_memory(row), score))
        return out

    # 逐词 LIKE：每词单独 LIKE content，再按命中词数 + importance_score 排序
    # terms 至少含 query，故恒有至少一个 LIKE 条件
    hit_exprs, like_params = [], []
    for tok in terms:
        escaped = _escape_like(tok)
        like_params.append(f"%{escaped}%")
        hit_exprs.append("content LIKE ? ESCAPE '\\'")
    flat = " ".join(_SELECT_MEMORY_COLUMNS.split())
    _hits_clause = " + ".join(f"CASE WHEN {e} THEN 1 ELSE 0 END" for e in hit_exprs)
    like_where = f"({' OR '.join(hit_exprs)})"
    like_condition, like_condition_params = _workspace_ids_condition(
        "workspace_id", workspace_ids
    )
    if like_condition:
        like_where = f"{like_where} AND {like_condition}"
    sql = (
        f"SELECT {flat}, ({_hits_clause}) AS _hits "
        f"FROM memories WHERE {like_where} "
        f"ORDER BY _hits DESC, importance_score DESC LIMIT {int(limit)}"
    )
    rows = conn.execute(
        sql, [*like_params, *like_params, *like_condition_params]
    ).fetchall()
    return [(_row_to_memory(r), _pseudo_bm25_score(float(idx))) for idx, r in enumerate(rows)]


def search_episodes(
    conn: sqlite3.Connection,
    *,
    entity: str,
    limit: int = 5,
    workspace_ids: Sequence[str] | None = None,
) -> list["_EpisodeRow"]:
    """按实体名 LIKE 模糊匹配 episodes 表。

    真实 FTS5 关联属于未来增强（不在本 plan 范围）；最小实现用 LIKE 兜底。

    列名修正（2026-09-15，与 RCA 根因 1 同族）：原实现选 ``updated_at``，但
    ``episodes`` 表自 schema v1 起就没有该列（只有 ``started_at`` / ``ended_at``），
    因此**每次调用**都抛 ``no such column: updated_at``。之所以从未暴露：episodes
    通道仅在 query 含路径/扩展名实体时才走到这里（``channels/episodes.py:34``），
    普通 query 直接返回 ``[]``，计数器显示的「OK n=0」从未真正执行 SQL。
    改用 ``ended_at`` 作为时间戳，``_EpisodeRow.updated_at`` 字段名保持不变
    （那是通道 ``compute_recency`` 依赖的契约）。

    安全审查 M-4（2026-09-15）：本函数同样要转义 LIKE 元字符。``entity`` 由
    ``channels/episodes.py`` 的正则抽取，``_`` 属 ``\\w`` 可通过，未转义时
    entity ``a_c.py`` 会命中 ``abc.py`` / ``aXc.py``（过召回，非注入）。
    """
    pattern = f"%{_escape_like(entity)}%"
    # OR 分支必须先加括号：否则追加的 AND 条件会被 OR 吃掉（可见性过滤失效）。
    where = "(summary LIKE ? ESCAPE '\\' OR session_id LIKE ? ESCAPE '\\')"
    condition, condition_params = _workspace_ids_condition("workspace_id", workspace_ids)
    if condition:
        where = f"{where} AND {condition}"
    cur = conn.execute(
        f"SELECT id, summary, ended_at FROM episodes WHERE {where} LIMIT ?",
        [pattern, pattern, *condition_params, limit],
    )
    return [
        _EpisodeRow(id=row[0], summary=row[1], updated_at=row[2])
        for row in cur.fetchall()
    ]


def query_semantic(
    conn: sqlite3.Connection,
    *,
    min_importance: float,
    since_days: int,
    limit: int,
    workspace_ids: Sequence[str] | None = None,
) -> list["_MemoryRow"]:
    """近期高重要性记忆：``importance >= min_importance`` 且 ``updated_at`` 在窗口内。"""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    # 时间比较**不能**直接比 TEXT：生产写入点（``extractor.py:1459``、
    # ``webui/memory_api.py:268``、``update_memory``）都走 ``isoformat()``，即
    # ``'T'`` 分隔；而把 ``datetime`` 对象直接绑进 SQLite 时（sqlite3 自 3.12 起
    # 已弃用该隐式适配器）落库为**空格分隔**的 'YYYY-MM-DD HH:MM:SS.ffffff+00:00'。
    # 两种格式混存时字符串比较会失准：日期相同时按第 10 位判大小，
    # ``' '(0x20) < 'T'(0x54)`` → 空格分隔的**当日**记录被整批判为更早而排除。
    # ``datetime()`` 把两侧都归一后再比，对两种格式与 ``+00:00`` 偏移都成立，
    # 使查询不再耦合于存储端的文本格式（实测 sqlite 3.52；EXPLAIN QUERY PLAN 与
    # 旧写法一致，仍走 idx_memories_importance）。
    condition, condition_params = _workspace_ids_condition("workspace_id", workspace_ids)
    where = "importance_score >= ? AND datetime(updated_at) >= datetime(?)"
    if condition:
        where = f"{where} AND {condition}"
    cur = conn.execute(
        f"SELECT id, content, importance_score, updated_at, access_count "
        f"FROM memories "
        f"WHERE {where} "
        f"ORDER BY importance_score DESC LIMIT ?",
        (min_importance, cutoff.isoformat(), *condition_params, limit),
    )
    return [
        _MemoryRow(
            id=row[0],
            content=row[1],
            importance_score=row[2],
            updated_at=row[3],
            access_count=row[4],
        )
        for row in cur.fetchall()
    ]


def search_attachments(
    conn: sqlite3.Connection,
    *,
    term: str,
    intent: str,
    limit: int = 5,
) -> list["_AttachmentRow"]:
    """附件搜索：LIKE 匹配（最小实现）。

    注：``attachments`` 表当前未在 schema v1 内，此实现为预留接口；调用方须
    保证 schema 已扩展（或用 stub store）。真实 term 匹配语义属于未来增强。
    """
    cur = conn.execute(
        "SELECT id, file_path, created_at FROM attachments "
        "WHERE file_path LIKE ? LIMIT ?",
        (f"%{term}%", limit),
    )
    return [
        _AttachmentRow(id=row[0], content=row[1], updated_at=row[2])
        for row in cur.fetchall()
    ]


def _pseudo_bm25_score(rank: float) -> float:
    """``bm25_rank_to_score`` 内联版：避免在既有文件中新增顶层 import。"""
    return 1.0 / (1.0 + max(0.0, rank))


def known_workspace_ids(conn: sqlite3.Connection) -> set[str]:
    """库中已出现的工作区键（memories + episodes）。

    面板手动新增/改归属时用它校验目标工作区是否已登记。表缺失（未初始化 schema
    的库）时跳过该表，不抛异常。
    """
    ids: set[str] = set()
    for table in ("memories", "episodes"):
        try:
            rows = conn.execute(f"SELECT DISTINCT workspace_id FROM {table}").fetchall()
        except sqlite3.OperationalError:
            continue
        ids.update(str(row[0]) for row in rows if row[0])
    return ids


@dataclass
class _EpisodeRow:
    """``search_episodes`` 返回的轻量 row：与 ``channels/episodes`` 期望属性对齐。"""

    id: str
    summary: str
    updated_at: str


@dataclass
class _MemoryRow:
    """``query_semantic`` 返回的轻量 row：与 ``channels/recent`` 期望属性对齐。"""

    id: str
    content: str
    importance_score: float
    updated_at: str
    access_count: int


@dataclass
class _AttachmentRow:
    """``search_attachments`` 返回的轻量 row：与 ``channels/attachments`` 期望属性对齐。"""

    id: str
    content: str = ""
    updated_at: str = ""
    importance_score: float = 0.5


# ---------- vector_sync_state ----------
# Q2 选新表（database.py § vector_sync_state）。单行表，id 恒为 1。


@dataclass
class VectorSyncState:
    """``vector_sync_state`` 单行表映射。"""

    cursor: str
    indexed: int
    deleted: int
    last_error: str
    updated_at: str


def get_vector_sync_state(conn: sqlite3.Connection) -> VectorSyncState | None:
    """读取同步游标；表空返回 ``None``。"""
    row = conn.execute(
        "SELECT cursor, indexed, deleted, last_error, updated_at "
        "FROM vector_sync_state WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return VectorSyncState(
        cursor=row["cursor"],
        indexed=row["indexed"],
        deleted=row["deleted"],
        last_error=row["last_error"],
        updated_at=row["updated_at"],
    )


def upsert_vector_sync_state(
    conn: sqlite3.Connection,
    *,
    cursor: str,
    indexed: int,
    deleted: int,
    last_error: str,
) -> None:
    """写入单行同步游标（id 恒为 1）。"""
    conn.execute(
        "INSERT INTO vector_sync_state (id, cursor, indexed, deleted, last_error, updated_at) "
        "VALUES (1, :cursor, :indexed, :deleted, :last_error, :updated_at) "
        "ON CONFLICT (id) DO UPDATE SET "
        "cursor = excluded.cursor, indexed = excluded.indexed, "
        "deleted = excluded.deleted, last_error = excluded.last_error, "
        "updated_at = excluded.updated_at",
        {
            "cursor": cursor,
            "indexed": int(indexed),
            "deleted": int(deleted),
            "last_error": last_error,
            "updated_at": _now_iso(),
        },
    )
