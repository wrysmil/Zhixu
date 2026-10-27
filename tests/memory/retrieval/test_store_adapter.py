"""store adapter 单测 + 中文检索端到端验收（RCA 2026-09-15）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from loguru import logger

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Episode, Memory, MemoryType
from nanobot.memory.repository import add_episode, add_memory, search_memories
from nanobot.memory.retrieval.engine import RetrievalEngine
from nanobot.memory.retrieval.store_adapter import MemoryStoreAdapter

_CHINESE_MEMORY = "用户热爱创作，希望AI主动提供创作灵感"
_SECOND_MEMORY = "用户希望定期收到AI主动推送的灵感内容"


def _now_iso() -> str:
    """生产写入格式：``isoformat()``，即 ``'T'`` 分隔（见 repository._now_iso）。

    刻意**不**把 ``datetime`` 对象直接交给 sqlite3——那会走 sqlite3 自 3.12 起
    已弃用的隐式适配器（落库为空格分隔格式，并触发 DeprecationWarning），
    与全部生产写入点（``extractor.py:1459``、``webui/memory_api.py:268``、
    ``repository.update_memory``）不一致。
    """
    return datetime.now(timezone.utc).isoformat()


def _seed(db: MemoryDatabase) -> None:
    """写入 2 条中文记忆（importance 0.9 / 0.8，updated_at=now，生产格式）。"""
    now = _now_iso()
    with db.connect() as conn:
        add_memory(
            conn,
            Memory(
                id="m1",
                content=_CHINESE_MEMORY,
                type=MemoryType.PREFERENCE,
                importance_score=0.9,
                created_at=now,
                updated_at=now,
            ),
        )
        add_memory(
            conn,
            Memory(
                id="m2",
                content=_SECOND_MEMORY,
                type=MemoryType.PREFERENCE,
                importance_score=0.8,
                created_at=now,
                updated_at=now,
            ),
        )


@pytest.fixture()
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.ensure_schema()
    _seed(database)
    return database


# ---------- FTS5 中文子串回退（根因 5）----------


def test_search_memories_matches_chinese_substring(db: MemoryDatabase):
    """unicode61 不分词，中文子串必须靠 LIKE 回退命中。"""
    with db.connect() as conn:
        hits = search_memories(conn, "创作", limit=10)
    assert [m.id for m in hits] == ["m1"]


def test_search_memories_still_matches_exact_token(db: MemoryDatabase):
    """FTS5 能命中的整串 token 走原路径，结果不变。"""
    with db.connect() as conn:
        hits = search_memories(conn, "用户热爱创作", limit=10)
    assert "m1" in [m.id for m in hits]


def test_search_memories_tolerates_fts_syntax_error(db: MemoryDatabase):
    """FTS5 语法错误（未闭合引号）不应抛出，且回退后仍能命中子串。

    断言必须**非空**才有价值：先插一条 content 含 ``"创作`` 的记忆，让 LIKE 模式
    ``%"创作%`` 真正能命中，从而同时验证「不抛异常」与「异常后被 LIKE 回退救回」。
    实测该 query 在 FTS5 上确实报错（``OperationalError: unterminated string``），
    在修复前会直接冒泡给调用方。
    """
    quote_memory = '用户说"创作灵感"来自晨跑'
    with db.connect() as conn:
        add_memory(
            conn,
            Memory(
                id="m9",
                content=quote_memory,
                type=MemoryType.PREFERENCE,
                importance_score=0.7,
                created_at=_now_iso(),
                updated_at=_now_iso(),
            ),
        )
    with db.connect() as conn:
        hits = search_memories(conn, '"创作', limit=10)
    assert [m.id for m in hits] == ["m9"], "FTS5 语法错误后未由 LIKE 回退救回"
    assert hits[0].content == quote_memory


# ---------- adapter 契约（根因 1）----------


def test_adapter_exposes_channel_contract(db: MemoryDatabase):
    """通道调用的 4 个方法必须存在且可调用（此前全是 AttributeError）。"""
    adapter = MemoryStoreAdapter(db)
    scored = adapter.search_semantic_scored("创作", limit=15)
    assert scored and scored[0][0].id == "m1"

    recent = adapter.query_semantic(min_importance=0.6, since_days=3, limit=5)
    assert {m.id for m in recent} == {"m1", "m2"}

    assert adapter.search_episodes(entity="不存在", limit=3) == []
    # attachments 表未进 schema v1：必须返回 []，不得抛 OperationalError
    assert adapter.search_attachments("图片", intent="search_file", limit=5) == []


def test_search_episodes_returns_seeded_episode(db: MemoryDatabase):
    """episodes 通道契约的**正向**覆盖（上面那条只断言空结果）。

    此前 ``repository.search_episodes`` 选的是 ``episodes.updated_at``，而该列在
    schema v1 里根本不存在（只有 ``started_at`` / ``ended_at``）→ 每次调用都抛
    ``no such column: updated_at``。长期不可见的原因：episodes 通道只在 query 含
    路径/扩展名实体时才被调用（``channels/episodes.py:34``），普通 query 直接返回
    ``[]``，这条 SQL 从未被执行到。
    """
    started = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    ended = datetime(2026, 9, 1, 11, 0, 0, tzinfo=timezone.utc)
    with db.connect() as conn:
        add_episode(
            conn,
            Episode(
                id="e1",
                session_id="s1",
                summary="调试 main.py 脚本",
                started_at=started.isoformat(),
                ended_at=ended.isoformat(),
            ),
        )
    rows = MemoryStoreAdapter(db).search_episodes(entity="main.py", limit=3)
    assert [r.id for r in rows] == ["e1"]
    assert rows[0].summary == "调试 main.py 脚本"
    # _EpisodeRow.updated_at 承载 ``ended_at``（字段名是通道 compute_recency 的契约）
    assert rows[0].updated_at == ended.isoformat()


def test_query_semantic_includes_boundary_day(db: MemoryDatabase):
    """TEXT 时间比较必须对存储格式不敏感（空格分隔 vs ``'T'`` 分隔）。

    探针取 ``cutoff + 1s`` 并以**空格分隔**格式写入：它落在 3 天窗口之内，且与
    cutoff **同日**——这正是字符串比较会失准的组合（日期相同时按第 10 位判大小，
    ``' '(0x20) < 'T'(0x54)`` → 被判为更早而排除）。``datetime()`` 归一后必须保留。

    实测该对值：``'2026-09-15 14:15:31.123456+00:00' >= '2026-09-15T14:15:30.999999+00:00'``
    → 字符串比较 ``0``，``datetime()`` 比较 ``1``。

    下方两条前置断言把「探针确实落在这个敏感组合上」写成显式契约：若某次运行
    恰好跨了午夜（cutoff ≈ 23:59:59），测试会**响亮失败**而不是悄悄失去判别力。

    注：UPDATE 必须在**独立**的 ``db.connect()`` 块内提交后再调 adapter——
    ``MemoryDatabase.connect`` 用非重入 ``threading.Lock``，同线程嵌套
    ``connect()`` 会死锁（实测 database.py:183 挂起），而 database.py 不在
    本 WU 允许修改的文件内。
    """
    adapter = MemoryStoreAdapter(db)
    since_days = 3
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=since_days)
    probe = cutoff + timedelta(seconds=1)
    assert probe.date() == cutoff.date(), (
        f"探针与 cutoff 不同日 → 字符串比较的 ' ' vs 'T' 差异不会触发：{probe} vs {cutoff}"
    )
    assert probe > cutoff, f"探针必须落在 {since_days} 天窗口内：{probe} <= {cutoff}"
    with db.connect() as conn:
        conn.execute(
            "UPDATE memories SET updated_at = ? WHERE id = 'm1'",
            (probe.strftime("%Y-%m-%d %H:%M:%S.%f+00:00"),),
        )
    got = adapter.query_semantic(
        min_importance=0.6, since_days=since_days, limit=5
    )
    assert "m1" in {m.id for m in got}, "边界当天记录被整批排除"


# ---------- 端到端验收（RCA §Next 要求的失败测试）----------


@pytest.mark.asyncio
async def test_retrieve_with_ids_returns_chinese_memories(db: MemoryDatabase):
    """向临时库写入记忆后，中文 query 必须召回非空注入块。

    覆盖范围（2026-09-15 代码审查 C-2 更正）：本用例**只**守住**根因 1**
    （store 适配层）。经变异实验证实，把 ``repository._search_memories_like``
    整个禁掉后本用例**仍然通过** —— 因为 ``recent`` 通道
    （``query_semantic``：importance ≥ 0.6 且 3 天窗口）**与 query 文本无关**，
    单靠它就足以产出非空块。故「中文 query」在本用例中是**无关变量**，
    原 docstring 声称的「覆盖根因 5」不成立。

    根因 5（CJK 子串 LIKE 回退）的判别性覆盖见
    ``test_semantic_channel_alone_recovers_chinese_substring``（下方）与
    ``tests/memory/test_search.py::TestChineseSubstringFallback``。

    ``recent_messages`` 给非空以隔离门禁（首轮行为由
    ``tests/memory/retrieval/test_preprocessor.py`` 覆盖）。
    """
    engine = RetrievalEngine(store=MemoryStoreAdapter(db), brain=None)
    block, ids = await engine.retrieve_with_ids(
        query="我正在规划创作选题",
        recent_messages=[{"role": "user", "content": "在吗"}, {"role": "assistant", "content": "在"}],
    )
    assert block.startswith("## 相关记忆（自动检索）")
    assert _CHINESE_MEMORY in block
    assert "m1" in ids


@pytest.mark.asyncio
async def test_semantic_channel_alone_recovers_chinese_substring(tmp_path: Path):
    """根因 5 的**判别性**测试：只有语义通道能救回这条记忆。

    构造手法（2026-09-15 代码审查 C-2 要求）：把记忆的 ``updated_at`` 设为
    ``now - 30 天``，使其**超出 ``recent`` 通道的 3 天窗口**；episodes 通道需要
    query 含路径/扩展名实体（本 query 无）；attachments 通道被媒体词闸门挡住。
    → 四路里**只有 semantic** 可能产出候选。

    因此：把 ``_search_memories_like`` 禁掉 → 块必为空（变异实验成立）；
    保留它 → 块非空。这正是原验收测试缺失的判别力。
    """
    database = MemoryDatabase(tmp_path)
    database.ensure_schema()
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with database.connect() as conn:
        add_memory(
            conn,
            Memory(
                id="old1",
                content=_CHINESE_MEMORY,
                type=MemoryType.PREFERENCE,
                importance_score=0.9,
                created_at=old,
                updated_at=old,
            ),
        )

    adapter = MemoryStoreAdapter(database)
    # 前置断言：窗外交付给 recent 通道看不见（否则本用例失去判别力）
    assert adapter.query_semantic(min_importance=0.6, since_days=3, limit=5) == []
    # semantic 通道凭 LIKE 子串命中。用 4 字的「创作灵感」而非 2 字的「创作」
    # ——后者会被 ``too_short`` 门禁（≤3 字且无锚点）拦掉，令本用例失败于无关原因。
    assert [m.id for m, _ in adapter.search_semantic_scored("创作灵感", limit=15)] == ["old1"]

    engine = RetrievalEngine(store=adapter, brain=None)
    block, ids = await engine.retrieve_with_ids(
        query="创作灵感",
        recent_messages=[{"role": "user", "content": "在吗"}],
    )
    assert "old1" in ids, "语义通道未召回窗外的中文记忆——根因 5 回退失效"
    assert _CHINESE_MEMORY in block


# ---------- 通道异常可观测性（根因 1 的「为何多年不可见」）----------


class _BrokenSemanticStore:
    """四路通道契约齐全，但 semantic 抛异常——复刻根因 1 的装配级故障。"""

    def search_semantic_scored(self, query, *, keywords=None, limit=None):
        raise AttributeError(
            "'MemoryDatabase' object has no attribute 'search_semantic_scored'"
        )

    def search_episodes(self, *, entity: str, limit: int = 5):
        return []

    def query_semantic(self, *, min_importance: float, since_days: int, limit: int):
        return []

    def search_attachments(self, term: str, *, intent: str, limit: int = 5):
        return []


@pytest.mark.asyncio
async def test_engine_channel_failure_is_logged_not_silent():
    """通道异常必须留下 ``warning``，而不是被静默 ``continue`` 吞掉。

    这是根因 1「四路召回全废却表现为『记忆是空的』」的**可观测性**修复：
    异常仍被隔离（不阻断其余通道、不抛给调用方），但通道名 + 异常类型 + 消息
    必须进日志，否则同类装配故障永不可见。

    放在本文件而非 ``tests/memory/retrieval/test_engine.py``：后者不在本 WU
    允许修改的文件清单内（WU 边界见 dispatch）。
    """
    messages: list[str] = []
    sink_id = logger.add(
        lambda message: messages.append(str(message)),
        level="WARNING",
        format="{message}",
    )
    try:
        engine = RetrievalEngine(store=_BrokenSemanticStore(), brain=None)
        block, ids = await engine.retrieve_with_ids(
            query="我正在规划创作选题",
            recent_messages=[{"role": "user", "content": "在吗"}],
        )
    finally:
        logger.remove(sink_id)

    # 异常仍被隔离：无候选 → 空块，不向调用方抛出
    assert block == ""
    assert ids == []
    assert any(
        "retrieval channel semantic failed" in m and "AttributeError" in m
        for m in messages
    ), f"通道异常未被记录：{messages}"


@pytest.mark.parametrize("vector_only", [False, True])
@pytest.mark.parametrize("workspace_ids", [None, (), ("default", "ws-a")])
def test_search_semantic_scored_filters_by_workspace_ids(tmp_path, vector_only, workspace_ids):
    """FTS5 和向量独有候选均按 SQLite 权威归属过滤，向量不传 where。"""
    database = MemoryDatabase(tmp_path)
    database.ensure_schema()
    with database.connect() as conn:
        for index, workspace in enumerate(("default", "ws-a", "ws-b")):
            add_memory(conn, Memory(
                id=f"m-{index}",
                content="创作灵感 共享",
                type=MemoryType.FACT,
                created_at=_now_iso(),
                updated_at=_now_iso(),
                workspace_id=workspace,
            ))

    class _Vectors:
        def search(self, query, *, limit):
            return [(f"m-{index}", 0.9) for index in range(3)]

    adapter = MemoryStoreAdapter(database, vector_store=_Vectors() if vector_only else None)
    hits = adapter.search_semantic_scored(
        "向量独有查询" if vector_only else "创作灵感",
        limit=10,
        workspace_ids=workspace_ids,
    )
    expected = {"default", "ws-a", "ws-b"} if workspace_ids is None else set(workspace_ids)
    assert {mem.workspace_id for mem, _ in hits} == expected


def test_episode_and_recent_queries_filter_by_workspace_ids(tmp_path):
    """情节和近期通道复用 repository 的可见集合过滤。"""
    database = MemoryDatabase(tmp_path)
    database.ensure_schema()
    with database.connect() as conn:
        for index, workspace in enumerate(("default", "ws-a", "ws-b")):
            add_memory(conn, Memory(
                id=f"m-{index}", content="创作灵感", type=MemoryType.FACT,
                created_at=_now_iso(), updated_at=_now_iso(),
                importance_score=0.9, workspace_id=workspace,
            ))
            add_episode(conn, Episode(
                id=f"e-{index}", session_id="s1", summary="创作灵感",
                started_at=_now_iso(), ended_at=_now_iso(), workspace_id=workspace,
            ))
    adapter = MemoryStoreAdapter(database)
    assert {row.id for row in adapter.search_episodes(
        entity="创作灵感", workspace_ids=("default", "ws-a")
    )} == {"e-0", "e-1"}
    assert {row.id for row in adapter.query_semantic(
        min_importance=0.6, since_days=3, limit=10, workspace_ids=("default", "ws-a")
    )} == {"m-0", "m-1"}
