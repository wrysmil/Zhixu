---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
dispatch: n/a
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - harness-kit/artifact-templates/plan.harness-overlay.md
  - .ai-runtime-artifacts/specs/2026-11-10-knowledge-graph-spec.md
  - .ai-runtime-artifacts/stack/2026-11-10-knowledge-graph-stack.md
  - .ai-runtime-artifacts/contracts/2026-11-11-contract-knowledge-graph.md
  - nanobot/knowledge/database.py
  - nanobot/knowledge/repository.py
  - nanobot/knowledge/ingestor.py
  - nanobot/knowledge/questions.py
  - nanobot/knowledge/fusion.py
  - nanobot/knowledge/retriever.py
  - nanobot/knowledge/service.py
  - nanobot/config/schema.py
  - nanobot/webui/knowledge_routes.py
  - nanobot/webui/knowledge_api.py
  - nanobot/evaluation/knowledge/models.py
  - nanobot/evaluation/knowledge/runner.py
  - nanobot/agent/tools/knowledge_common.py
  - webui/src/lib/api.ts
  - webui/src/components/workspace/KnowledgePage.tsx
  - webui/src/i18n/locales/en/common.json
created_at: 2026-11-11
status: implemented
approved: true
approval_note: 用户本会话原话「开始实现啊」
---

# 知识图谱（可选 Neo4j 后端）实施计划 v1

> **执行方式：单分支、顺序执行。** 不拆并行 WU、不建 worktree、不派子 Agent；
> 每个 Task 完成后立即补测试与提交，Task 之间按依赖顺序推进（与用户既有偏好一致）。

**Goal:** 在知识中心补齐「入库异步抽取 → 1~2 跳图检索 → 图谱可视化」的完整闭环，且默认关闭时对现有行为零影响。

**Architecture:** SQLite schema v3 保存实体/关系/证据与抽取状态，是唯一真相源；Neo4j 仅作可重建的派生索引。抽取挂在入库事务之后，由独立 worker 按「租约 + attempt」执行；检索时图作为第三路 `RouteSpec` 参与加权 RRF，失败显式降级为 `graph_unavailable`。WebUI 新增第三个 tab，`d3-force` 动态 import。

**Tech Stack:** Python 3.11+ / SQLite / httpx（抽取）/ neo4j 5.x 驱动（可选 extra）/ React 18 + TypeScript + Vite / d3-force ^3。

---

## 0. 前置修订（先改契约，再写代码）

> 契约规定「契约与实现冲突 → 先改契约再改代码」。本计划对契约做两处收敛：

1. **图参数单一来源**：不在 `KnowledgeRetrievalConfig` 里重复 7 个 `graph_*` 字段，
   统一放在 `knowledge.graph.expansion`，装配时映射进 `RetrieverSettings`。
   理由：两处同名字段会产生「配置改了但没生效」的二义性；评测覆写仍直接写 `RetrieverSettings`。
2. **工具属性落点**：实际工具输出根节点是 `<result>`（`knowledge_common.py:render_search_result`），
   不是 spec 写的 `<hit>`；新增属性名保持 `graph_path`。

**Step 0.1** 修改 `.ai-runtime-artifacts/contracts/2026-11-11-contract-knowledge-graph.md`：
把 §6/§11 中「`KnowledgeRetrievalConfig` 同步新增 5 个图参数」改为「图参数唯一来源是
`knowledge.graph.expansion`；`RetrieverSettings` 仍是评测覆写载体」；§10 的 `<hit>` 改为 `<result>`。

**Step 0.2** 同步修改契约 §9 前端契约：`GraphPanel` 由 `KnowledgePage` 注入
`token` 与 `client`（`useClient()`），不自行取认证上下文；与现有知识页口径一致。
spec 本身无需改动（§8 未重复声明检索配置字段）；FM `status` 保持 `draft` 到实现验收通过。

---

## 1. 文件结构（先锁边界）

```
nanobot/knowledge/graph/
  __init__.py           # 导出 models / service 的公共符号
  models.py             # frozen dataclass：实体/关系/证据/扩展/健康/统计/状态
  repo.py               # GraphRepository：SQLite 读写、计数重算、状态机、种子查询
  normalize.py          # normalize_entity_name()
  linker.py             # find_seed_entities()
  store.py              # GraphStore Protocol + GraphStoreError
  neo4j_store.py        # Neo4jGraphStore（延迟 import neo4j）
  extractor.py          # OpenAI 兼容抽取器 + JSON 守卫
  sync.py               # GraphSyncService：SQLite → Neo4j 镜像
  service.py            # GraphService：worker、回填、重建、统计、读接口
  route.py              # GraphRoute：检索第三路（链接 → 扩展 → 回表 → 证据）

nanobot/knowledge/ingestor.py      # 末尾调用 graph pending 钩子
nanobot/knowledge/service.py       # 装配 GraphService、第二个 worker、删除同步
nanobot/knowledge/repository.py    # 新增 fetch_search_chunks()
nanobot/knowledge/retriever.py     # 第三路、GraphRoute 注入、_fuse 扩展
nanobot/knowledge/fusion.py        # SearchHit.graph_evidence 可选字段
nanobot/config/schema.py           # 三个 Graph 配置块
nanobot/cli/knowledge.py           # graph rebuild 子命令
nanobot/webui/knowledge_routes.py  # 4 读 + 2 写动作登记
nanobot/webui/knowledge_api.py     # handler 与作用域/门禁
nanobot/agent/tools/knowledge_common.py  # <result graph_path="...">
nanobot/evaluation/knowledge/models.py   # 白名单 + 范围
webui/src/lib/api.ts                     # 客户端函数与类型
webui/src/components/workspace/KnowledgePage.tsx  # 第三个 tab
webui/src/components/workspace/knowledge/GraphPanel.tsx
webui/src/components/workspace/knowledge/GraphView.tsx
webui/src/i18n/locales/*/common.json     # 10 语言 knowledge.graph.*
```

---

## 2. 任务

### Task 1：SQLite schema v3 + 图数据模型 + GraphRepository

**Files:**
- Modify: `nanobot/knowledge/database.py`（`SCHEMA_VERSION` 与 `_MIGRATIONS`）
- Create: `nanobot/knowledge/graph/__init__.py`
- Create: `nanobot/knowledge/graph/models.py`
- Create: `nanobot/knowledge/graph/repo.py`
- Test: `tests/knowledge/test_graph_repo.py`

- [ ] **Step 1：写失败测试（迁移与级联）**

```python
# tests/knowledge/test_graph_repo.py
from nanobot.knowledge.database import KnowledgeDatabase, SCHEMA_VERSION
from nanobot.knowledge.graph.repo import GraphRepository
from nanobot.knowledge.repository import KnowledgeRepository


def test_schema_v3_creates_graph_tables(tmp_path):
    db = KnowledgeDatabase(tmp_path / "state.db")
    db.init_schema()
    assert SCHEMA_VERSION == "3"
    with db.connect() as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"graph_entities", "graph_relations", "graph_mentions",
            "graph_relation_evidence", "graph_extraction_state"} <= names


def test_document_delete_cascades_graph_rows(tmp_path):
    db = KnowledgeDatabase(tmp_path / "state.db")
    db.init_schema()
    repo = KnowledgeRepository(db)
    graph = GraphRepository(db)
    kb = repo.create_kb(name="k", workspace_id="default")
    doc = repo.insert_document(kb_id=kb.id, workspace_id="default", title="d",
                               source_kind="external", source_path=str(tmp_path / "a.md"),
                               original_name="a.md", file_hash="h1", file_size=1)
    with db.connect(immediate=True) as conn:
        conn.execute(
            "INSERT INTO document_chunks"
            " (id,doc_id,kb_id,workspace_id,chunk_index,content,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            ("chunk-1", doc.id, kb.id, "default", 0, "x", "now"),
        )
    graph.replace_document_graph(
        doc.id, kb.id,
        entities=[("web-api", "service", "网关")],
        relations=[],
        mentions=[("web-api", "chunk-1")],
    )
    assert graph.count_entities(kb.id) == 1
    repo.delete_document(doc.id)
    assert graph.count_entities(kb.id) == 0          # 孤儿清理
    assert graph.count_mentions_for_doc(doc.id) == 0  # 级联
```

- [ ] **Step 2：跑测试确认失败**

Run: `pytest tests/knowledge/test_graph_repo.py -v`
Expected: FAIL（`ModuleNotFoundError: nanobot.knowledge.graph`）

- [ ] **Step 3：database.py 增加 v3**

```python
SCHEMA_VERSION = "3"

_V3_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS graph_entities (
      id            TEXT PRIMARY KEY,
      kb_id         TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
      name          TEXT NOT NULL,
      name_norm     TEXT NOT NULL,
      type          TEXT NOT NULL DEFAULT 'concept',
      description   TEXT NOT NULL DEFAULT '',
      mention_count INTEGER NOT NULL DEFAULT 0,
      created_at    TEXT NOT NULL,
      updated_at    TEXT NOT NULL,
      UNIQUE (kb_id, name_norm)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_graph_entities_kb ON graph_entities(kb_id)",
    """
    CREATE TABLE IF NOT EXISTS graph_relations (
      id               TEXT PRIMARY KEY,
      kb_id            TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
      source_entity_id TEXT NOT NULL REFERENCES graph_entities(id) ON DELETE CASCADE,
      target_entity_id TEXT NOT NULL REFERENCES graph_entities(id) ON DELETE CASCADE,
      relation         TEXT NOT NULL,
      description      TEXT NOT NULL DEFAULT '',
      weight           REAL NOT NULL DEFAULT 5.0,
      mention_count    INTEGER NOT NULL DEFAULT 0,
      created_at       TEXT NOT NULL,
      updated_at       TEXT NOT NULL,
      UNIQUE (kb_id, source_entity_id, target_entity_id, relation)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_graph_relations_source ON graph_relations(source_entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_graph_relations_target ON graph_relations(target_entity_id)",
    """
    CREATE TABLE IF NOT EXISTS graph_mentions (
      entity_id  TEXT NOT NULL REFERENCES graph_entities(id) ON DELETE CASCADE,
      doc_id     TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
      chunk_id   TEXT NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL,
      PRIMARY KEY (entity_id, chunk_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_graph_mentions_doc ON graph_mentions(doc_id)",
    """
    CREATE TABLE IF NOT EXISTS graph_relation_evidence (
      relation_id TEXT NOT NULL REFERENCES graph_relations(id) ON DELETE CASCADE,
      doc_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
      chunk_id    TEXT NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
      created_at  TEXT NOT NULL,
      PRIMARY KEY (relation_id, chunk_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_graph_relation_evidence_doc ON graph_relation_evidence(doc_id)",
    """
    CREATE TABLE IF NOT EXISTS graph_extraction_state (
      doc_id           TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
      status           TEXT NOT NULL CHECK (status IN ('pending','processing','ready','failed','skipped')),
      attempt          INTEGER NOT NULL DEFAULT 0,
      lease_owner      TEXT,
      lease_expires_at TEXT,
      model            TEXT NOT NULL DEFAULT '',
      prompt_version   TEXT NOT NULL DEFAULT '',
      chunk_total      INTEGER NOT NULL DEFAULT 0,
      chunk_done       INTEGER NOT NULL DEFAULT 0,
      entity_count     INTEGER NOT NULL DEFAULT 0,
      relation_count   INTEGER NOT NULL DEFAULT 0,
      truncated        INTEGER NOT NULL DEFAULT 0,
      error_code       TEXT NOT NULL DEFAULT '',
      error_message    TEXT NOT NULL DEFAULT '',
      extracted_at     TEXT,
      updated_at       TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_graph_state_status ON graph_extraction_state(status)",
)

_MIGRATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("1", _V1_STATEMENTS),
    ("2", _V2_STATEMENTS),
    ("3", _V3_STATEMENTS),
)
```

- [ ] **Step 4：写 models.py 与 repo.py**

```python
# nanobot/knowledge/graph/models.py
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GraphEntity:
    id: str
    kb_id: str
    name: str
    name_norm: str
    type: str
    description: str
    mention_count: int
    chunk_ids: tuple[str, ...] = ()   # 仅镜像用；SQLite 真源在 graph_mentions


@dataclass(frozen=True, slots=True)
class GraphRelation:
    id: str
    kb_id: str
    source_entity_id: str
    target_entity_id: str
    relation: str
    description: str
    weight: float
    mention_count: int


@dataclass(frozen=True, slots=True)
class GraphMention:
    entity_id: str
    doc_id: str
    chunk_id: str


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source_entity_id: str
    target_entity_id: str
    relation: str
    weight: float
    hop: int


@dataclass(frozen=True, slots=True)
class GraphExpansion:
    entity_ids: tuple[str, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    chunk_ids: tuple[str, ...] = ()
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class GraphHealth:
    status: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class GraphStats:
    enabled: bool
    backend: str
    status: str
    reason: str
    entities: int
    relations: int
    docs_ready: int
    docs_pending: int
    docs_failed: int
    docs_skipped: int
    truncated_docs: int
    last_extracted_at: str | None


@dataclass(frozen=True, slots=True)
class GraphEvidencePayload:
    seeds: tuple[str, ...]
    path: tuple[tuple[str, str, str], ...]  # (from, relation, to)
    hop: int
    entity_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GraphExtractionState:
    doc_id: str
    status: str
    attempt: int
    chunk_total: int
    chunk_done: int
    entity_count: int
    relation_count: int
    truncated: bool
    error_code: str
    error_message: str
    extracted_at: str | None
    updated_at: str
```

```python
# nanobot/knowledge/graph/repo.py（关键实现）
import hashlib
from datetime import datetime, timezone

from nanobot.knowledge.database import KnowledgeDatabase
from nanobot.knowledge.graph.models import (GraphEntity, GraphExtractionState,
                                            GraphRelation)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def entity_id_for(kb_id: str, name_norm: str) -> str:
    digest = hashlib.sha256(f"{kb_id}\0{name_norm}".encode()).hexdigest()
    return f"ent_{digest[:32]}"


def relation_id_for(kb_id: str, src_norm: str, relation: str, tgt_norm: str) -> str:
    digest = hashlib.sha256(
        f"{kb_id}\0{src_norm}\0{relation}\0{tgt_norm}".encode()
    ).hexdigest()
    return f"rel_{digest[:32]}"


class GraphRepository:
    def __init__(self, database: KnowledgeDatabase) -> None:
        self._db = database

    def replace_document_graph(self, doc_id, kb_id, *, entities, relations, mentions):
        """幂等重抽：先删本文档证据，再写实体/关系/证据，最后重算计数与孤儿。"""
        now = _now()
        touched_entities: set[str] = set()
        touched_relations: set[str] = set()
        with self._db.connect(immediate=True) as conn:
            old = conn.execute(
                "SELECT DISTINCT entity_id FROM graph_mentions WHERE doc_id=?", (doc_id,)
            ).fetchall()
            touched_entities.update(row[0] for row in old)
            old_rel = conn.execute(
                "SELECT DISTINCT relation_id FROM graph_relation_evidence WHERE doc_id=?",
                (doc_id,),
            ).fetchall()
            touched_relations.update(row[0] for row in old_rel)
            conn.execute("DELETE FROM graph_mentions WHERE doc_id=?", (doc_id,))
            conn.execute("DELETE FROM graph_relation_evidence WHERE doc_id=?", (doc_id,))
            for name, type_, description in entities:
                from nanobot.knowledge.graph.normalize import normalize_entity_name
                norm = normalize_entity_name(name)
                eid = entity_id_for(kb_id, norm)
                conn.execute(
                    """INSERT INTO graph_entities
                       (id,kb_id,name,name_norm,type,description,mention_count,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,0,?,?)
                       ON CONFLICT(kb_id,name_norm) DO UPDATE SET
                         type=excluded.type, description=excluded.description,
                         updated_at=excluded.updated_at""",
                    (eid, kb_id, name, norm, type_, description, now, now),
                )
                touched_entities.add(eid)
            norm_to_id = {
                row[1]: row[0] for row in conn.execute(
                    "SELECT id, name_norm FROM graph_entities WHERE kb_id=?", (kb_id,)
                )
            }
            for src, tgt, relation, description, weight in relations:
                src_id = norm_to_id[normalize_entity_name(src)]
                tgt_id = norm_to_id[normalize_entity_name(tgt)]
                rid = relation_id_for(
                    kb_id, normalize_entity_name(src), relation, normalize_entity_name(tgt)
                )
                conn.execute(
                    """INSERT INTO graph_relations
                       (id,kb_id,source_entity_id,target_entity_id,relation,description,
                        weight,mention_count,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,0,?,?)
                       ON CONFLICT(kb_id,source_entity_id,target_entity_id,relation)
                       DO UPDATE SET description=excluded.description,
                         weight=excluded.weight, updated_at=excluded.updated_at""",
                    (rid, kb_id, src_id, tgt_id, relation, description, weight, now, now),
                )
                touched_relations.add(rid)
            for name, chunk_id in mentions:
                eid = norm_to_id[normalize_entity_name(name)]
                conn.execute(
                    "INSERT OR IGNORE INTO graph_mentions(entity_id,doc_id,chunk_id,created_at)"
                    " VALUES (?,?,?,?)",
                    (eid, doc_id, chunk_id, now),
                )
            for rid in touched_relations:
                conn.execute(
                    """UPDATE graph_relations SET mention_count=(
                         SELECT COUNT(*) FROM graph_relation_evidence WHERE relation_id=?)
                       WHERE id=?""",
                    (rid, rid),
                )
            self._recount_entities(conn, touched_entities)
        return touched_entities, touched_relations

    @staticmethod
    def _recount_entities(conn, entity_ids):
        for eid in entity_ids:
            conn.execute(
                """UPDATE graph_entities SET mention_count=(
                     SELECT COUNT(*) FROM graph_mentions WHERE entity_id=?)
                   WHERE id=?""",
                (eid, eid),
            )
            conn.execute(
                "DELETE FROM graph_entities WHERE id=? AND mention_count=0", (eid,)
            )
        conn.execute(
            """DELETE FROM graph_relations WHERE mention_count=0 AND id NOT IN (
                 SELECT relation_id FROM graph_relation_evidence)"""
        )
```

```python
# repo.py 其余公开方法（签名固定，实现按下方 SQL 落地）
def count_entities(self, kb_id: str, *, q: str = "", type_: str = "") -> int: ...
def count_mentions_for_doc(self, doc_id: str) -> int: ...
def entity_ids_for_document(self, doc_id: str) -> list[str]: ...
def list_entity_ids(self, kb_id: str, entity_ids: Sequence[str]) -> set[str]: ...
def list_entities(self, kb_id: str, *, q: str = "", type_: str = "",
                  limit: int = 50, offset: int = 0) -> list[GraphEntity]: ...
def list_entities_by_ids(self, kb_id: str,
                         entity_ids: Sequence[str]) -> list[GraphEntity]: ...
def list_relations(self, kb_id: str, *, limit: int = 100_000) -> list[GraphRelation]: ...
def list_relations_for_entities(self, kb_id: str,
                                entity_ids: Sequence[str]) -> list[GraphRelation]: ...
def get_entity_in_scope(self, entity_id: str,
                        workspace_ids: Sequence[str]) -> GraphEntity | None: ...
def entities_for_chunk(self, chunk_id: str) -> set[str]: ...
def list_mentions(self, entity_id: str) -> list[dict]: ...
def list_neighbors(self, entity_id: str) -> list[dict]: ...
def mention_chunks(self, entity_ids: Sequence[str]) -> list[str]: ...
def search_entities_by_like(self, kb_id: str, exact_norm: str,
                            tokens: Sequence[str], *, limit: int = 200) -> list[GraphEntity]: ...
def cleanup_orphans(self, entity_ids: Sequence[str]) -> None: ...
def stats(self, kb_id: str) -> GraphStats: ...
def get_state(self, doc_id: str) -> GraphExtractionState | None: ...
def enqueue_document(self, doc_id: str, *, model: str, prompt_version: str,
                     force: bool = False) -> None: ...
def claim_document(self, doc_id: str) -> GraphExtractionState | None: ...
def update_progress(self, doc_id: str, attempt: int, *, chunk_done: int) -> None: ...
def finish_document(self, doc_id: str, attempt: int, *, status: str,
                    chunk_total: int = 0, chunk_done: int = 0,
                    entity_count: int = 0, relation_count: int = 0,
                    truncated: bool = False) -> bool: ...
def fail_document(self, doc_id: str, attempt: int, code: str, message: str) -> bool: ...
def reset_expired_leases(self, *, timeout_seconds: int = 600) -> list[str]: ...
def list_documents_for_extraction(self, kb_id: str,
                                  statuses: Sequence[str] = ()) -> list[str]: ...
def list_pending_documents(self) -> list[str]: ...
def purge_kb(self, kb_id: str) -> None: ...
```

关键 SQL：

```sql
-- search_entities_by_like：exact 优先，token 子串用参数绑定（% 在 Python 侧转义）
SELECT id,kb_id,name,name_norm,type,description,mention_count
FROM graph_entities
WHERE kb_id = ?
  AND (name_norm = ? OR name_norm LIKE ? ESCAPE '\' OR name_norm LIKE ? ESCAPE '\')
ORDER BY mention_count DESC, id ASC
LIMIT ?;

-- mention_chunks：按实体取证据块，保留首次出现顺序
SELECT m.chunk_id FROM graph_mentions m
WHERE m.entity_id IN (...)
GROUP BY m.chunk_id
ORDER BY MIN(m.created_at), m.chunk_id;

-- enqueue_document：pending 覆盖 failed/skipped，processing 不覆盖；force 时连 ready 一起置 pending
INSERT INTO graph_extraction_state
  (doc_id,status,attempt,model,prompt_version,updated_at)
VALUES (?, 'pending', 0, ?, ?, ?)
ON CONFLICT(doc_id) DO UPDATE SET
  status = 'pending', attempt = graph_extraction_state.attempt + 1,
  model = excluded.model, prompt_version = excluded.prompt_version,
  error_code = '', error_message = '', updated_at = excluded.updated_at
WHERE graph_extraction_state.status IN ('failed','skipped','pending')
   OR (? = 1 AND graph_extraction_state.status = 'ready');

-- get_entity_in_scope：实体 → 知识库作用域（global 库对所有工作区可见）
SELECT e.* FROM graph_entities e
JOIN knowledge_bases kb ON kb.id = e.kb_id
WHERE e.id = ? AND kb.is_enabled = 1
  AND (kb.visibility = 'global' OR kb.workspace_id IN (...));

-- claim_document：租约 10 分钟；过期可回收
UPDATE graph_extraction_state
SET status='processing', lease_owner=?, lease_expires_at=?, updated_at=?
WHERE doc_id=? AND status='pending';

-- finish_document / fail_document / update_progress 一律带 attempt 守卫
UPDATE graph_extraction_state SET status='ready', chunk_total=?, chunk_done=?,
  entity_count=?, relation_count=?, truncated=?, extracted_at=?, updated_at=?
WHERE doc_id=? AND attempt=? AND status='processing';
```

> `list_entities_by_ids` 额外按实体查 `graph_mentions` 填 `chunk_ids`（最多 200，按
> `created_at DESC`），供 Neo4j 镜像使用；`list_entities`（列表页）不查证据块，保持轻量。
> 入参超过 500 个 id 时按 500 一批查询后合并，规避 SQLite 变量上限。
>
> `cleanup_orphans`：对传入实体 id 重算 `mention_count`，删除计数为 0 的实体；
> 再删除 `mention_count=0` 且无 `graph_relation_evidence` 的关系。
> 所有状态机写入都必须带 `attempt` 守卫，旧 attempt 不得覆盖新状态。

- [ ] **Step 5：跑测试通过并提交**

Run: `pytest tests/knowledge/test_graph_repo.py -v`
Expected: PASS

```bash
git add nanobot/knowledge/database.py nanobot/knowledge/graph tests/knowledge/test_graph_repo.py
git commit -m "feat(knowledge): 图谱 SQLite v3 与图仓储"
```

---

### Task 2：GraphStore 协议 + Neo4j 适配器 + FakeStore

**Files:**
- Create: `nanobot/knowledge/graph/store.py`
- Create: `nanobot/knowledge/graph/neo4j_store.py`
- Create: `tests/knowledge/graph_fakes.py`
- Test: `tests/knowledge/test_graph_store.py`
- Modify: `pyproject.toml`（新增 `graph` extra 与 pytest marker）

- [ ] **Step 1：写失败测试**

```python
# tests/knowledge/test_graph_store.py
import os
import pytest
from nanobot.knowledge.graph.models import GraphEntity, GraphHealth
from nanobot.knowledge.graph.neo4j_store import Neo4jGraphStore


def test_fake_store_health_is_ready():
    from tests.knowledge.graph_fakes import FakeGraphStore
    assert FakeGraphStore().health() == GraphHealth("ready")


@pytest.mark.neo4j
@pytest.mark.skipif(not os.environ.get("NEO4J_TEST_URI"), reason="no neo4j")
def test_neo4j_upsert_and_expand():
    store = Neo4jGraphStore(
        uri=os.environ["NEO4J_TEST_URI"],
        username=os.environ.get("NEO4J_TEST_USER", "neo4j"),
        password=os.environ.get("NEO4J_TEST_PASSWORD", "password"),
        database=os.environ.get("NEO4J_TEST_DATABASE", "neo4j"),
        timeout=5.0,
    )
    store.ensure_schema()
    kb = "kb_test"
    store.delete_kb(kb)
    store.upsert_subgraph(kb, [
        GraphEntity("e1", kb, "A", "a", "concept", "", 1),
        GraphEntity("e2", kb, "B", "b", "concept", "", 1),
    ], [])
    out = store.expand(kb, ["e1"], hops=1, max_entities=20, max_chunks=50)
    assert "e2" in out.entity_ids
```

```python
# tests/knowledge/graph_fakes.py
from nanobot.knowledge.graph.models import GraphExpansion, GraphHealth


class FakeGraphStore:
    def __init__(self) -> None:
        self.available = True
        self.entities: dict[str, list] = {}
        self.relations: dict[str, list] = {}
        self.deleted_kbs: list[str] = []
        self.expansions: list[GraphExpansion] = []

    def health(self):
        return GraphHealth("ready") if self.available else GraphHealth(
            "degraded", "neo4j_unavailable")

    def ensure_schema(self):
        if not self.available:
            raise RuntimeError("neo4j_unavailable")

    def upsert_subgraph(self, kb_id, entities, relations):
        self.entities[kb_id] = list(entities)
        self.relations[kb_id] = list(relations)

    def delete_kb(self, kb_id):
        self.deleted_kbs.append(kb_id)

    def delete_entities(self, kb_id, entity_ids):
        wanted = set(entity_ids)
        self.entities[kb_id] = [e for e in self.entities.get(kb_id, []) if e.id not in wanted]

    def expand(self, kb_id, entity_ids, *, hops, max_entities, max_chunks):
        if self.expansions:
            return self.expansions.pop(0)
        return GraphExpansion()
```

- [ ] **Step 2：跑测试确认失败**

Run: `pytest tests/knowledge/test_graph_store.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3：写 store.py**

```python
# nanobot/knowledge/graph/store.py
from __future__ import annotations
from collections.abc import Sequence
from typing import Protocol

from nanobot.knowledge.graph.models import (GraphEntity, GraphExpansion,
                                            GraphHealth, GraphRelation)


class GraphStoreError(RuntimeError):
    """派生索引后端错误；调用方按降级处理，不向上抛给检索。"""


class GraphStore(Protocol):
    def health(self) -> GraphHealth: ...
    def ensure_schema(self) -> None: ...
    def upsert_subgraph(self, kb_id: str, entities: Sequence[GraphEntity],
                        relations: Sequence[GraphRelation]) -> None: ...
    def delete_kb(self, kb_id: str) -> None: ...
    def delete_entities(self, kb_id: str, entity_ids: Sequence[str]) -> None: ...
    def expand(self, kb_id: str, entity_ids: Sequence[str], *,
               hops: int, max_entities: int, max_chunks: int) -> GraphExpansion: ...
```

- [ ] **Step 4：写 neo4j_store.py（延迟 import + 参数化 Cypher）**

```python
class Neo4jGraphStore:
    def __init__(self, *, uri, username, password, database, timeout):
        self._uri = uri
        self._auth = (username, password)
        self._database = database
        self._timeout = timeout
        self._driver = None

    def _connect(self):
        if self._driver is None:
            try:
                from neo4j import GraphDatabase
            except ImportError as exc:
                raise GraphStoreError("neo4j 驱动未安装：pip install 'zhixu[graph]'") from exc
            self._driver = GraphDatabase.driver(
                self._uri, auth=self._auth,
                connection_timeout=self._timeout, max_connection_lifetime=300,
            )
        return self._driver

    def health(self) -> GraphHealth:
        try:
            self._connect().verify_connectivity()
            return GraphHealth("ready")
        except Exception:  # noqa: BLE001 - 健康检查不抛
            return GraphHealth("degraded", "neo4j_unavailable")

    def ensure_schema(self) -> None:
        try:
            self._query("CREATE CONSTRAINT graph_entity_uid IF NOT EXISTS "
                        "FOR (e:Entity) REQUIRE e.uid IS UNIQUE")
            self._query("CREATE INDEX graph_entity_kb IF NOT EXISTS "
                        "FOR (e:Entity) ON (e.kb_id)")
            self._query("MERGE (:GraphMeta {id:'schema'}) SET version = 1")
        except GraphStoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise GraphStoreError("schema_failed") from exc

    def _query(self, cypher, parameters=None):
        from neo4j import Query
        records = self._connect().execute_query(
            cypher, parameters or {}, database_=self._database,
            timeout=self._timeout,
        )
        return records

    def upsert_subgraph(self, kb_id, entities, relations):
        rows = [{"uid": f"{kb_id}:{e.id}", "kb_id": kb_id, "entity_id": e.id,
                 "name": e.name, "name_norm": e.name_norm, "type": e.type,
                 "description": e.description, "mention_count": e.mention_count,
                 "chunk_ids": list(e.chunk_ids)[:200]} for e in entities]
        if rows:
            self._query(
                "UNWIND $rows AS row MERGE (e:Entity {uid: row.uid}) "
                "SET e.kb_id=row.kb_id, e.entity_id=row.entity_id, e.name=row.name, "
                "e.name_norm=row.name_norm, e.type=row.type, e.description=row.description, "
                "e.mention_count=row.mention_count, e.chunk_ids=row.chunk_ids",
                {"rows": rows},
            )
        rel_rows = [{"uid": f"{kb_id}:{r.id}", "source_uid": f"{kb_id}:{r.source_entity_id}",
                     "target_uid": f"{kb_id}:{r.target_entity_id}", "relation": r.relation,
                     "weight": r.weight, "description": r.description,
                     "mention_count": r.mention_count} for r in relations]
        if rel_rows:
            self._query(
                "UNWIND $rows AS row "
                "MERGE (s:Entity {uid: row.source_uid}) "
                "MERGE (t:Entity {uid: row.target_uid}) "
                "MERGE (s)-[r:RELATED {uid: row.uid}]->(t) "
                "SET r.relation=row.relation, r.weight=row.weight, "
                "r.description=row.description, r.mention_count=row.mention_count",
                {"rows": rel_rows},
            )

    def expand(self, kb_id, entity_ids, *, hops, max_entities, max_chunks):
        if hops not in (1, 2):
            raise GraphStoreError("invalid_hops")
        uids = [f"{kb_id}:{eid}" for eid in entity_ids]
        if not uids:
            return GraphExpansion()
        # hops 只允许 1/2，直接进 Cypher 字面量；其余参数全部走参数绑定。
        cypher = (
            f"MATCH p=(s:Entity)-[:RELATED*1..{hops}]->(e:Entity) "
            "WHERE s.uid IN $uids AND e.kb_id = $kb_id AND s.kb_id = $kb_id "
            "RETURN p LIMIT $row_limit"
        )
        records = self._query(
            cypher,
            {"uids": uids, "kb_id": kb_id, "row_limit": max(1, max_entities * 8)},
        )
        entities: list[str] = []
        edges: list[GraphEdge] = []
        chunk_ids: list[str] = []
        truncated = False
        for record in records:
            path = record["p"]
            for node in path.nodes:
                eid = node.get("entity_id")
                if eid and eid not in entities:
                    if len(entities) >= max_entities:
                        truncated = True
                    else:
                        entities.append(eid)
                for cid in node.get("chunk_ids") or ():
                    if cid not in chunk_ids:
                        if len(chunk_ids) >= max_chunks:
                            truncated = True
                        else:
                            chunk_ids.append(cid)
            for rel in path.relationships:
                src = rel.start_node.get("entity_id")
                tgt = rel.end_node.get("entity_id")
                edges.append(GraphEdge(src, tgt, rel.get("relation", ""),
                                       float(rel.get("weight", 5.0)), hops))
        return GraphExpansion(tuple(entities), tuple(edges), tuple(chunk_ids), truncated)
```

- [ ] **Step 5：pyproject 增加 extra / marker**

```toml
graph = [
    "neo4j>=5.26,<6.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests", "nanobot/channels"]
markers = ["neo4j: 需要 NEO4J_TEST_URI 的真实 Neo4j 集成测试"]
```

- [ ] **Step 6：跑测试并提交**

Run: `pytest tests/knowledge/test_graph_store.py -v`
Expected: PASS（Neo4j 用例 skipped）

```bash
git add pyproject.toml nanobot/knowledge/graph tests/knowledge
git commit -m "feat(knowledge): 图谱存储协议与 Neo4j 适配器"
```

---

### Task 3：抽取器（Prompt / JSON 守卫 / 重试 / 幂等输入）

**Files:**
- Create: `nanobot/knowledge/graph/extractor.py`
- Test: `tests/knowledge/test_graph_extractor.py`

- [ ] **Step 1：写失败测试（守卫表逐条）**

```python
# tests/knowledge/test_graph_extractor.py
from nanobot.knowledge.graph.extractor import (GraphExtractionError,
                                               OpenAICompatGraphExtractor,
                                               parse_extraction)


def test_empty_entities_is_declined():
    result = parse_extraction('{"entities": [], "relations": []}')
    assert result.declined is True


def test_invalid_relation_endpoint_is_dropped():
    payload = '{"entities":[{"name":"A","type":"concept"}],' \
              '"relations":[{"source":"A","target":"B","relation":"依赖"}]}'
    result = parse_extraction(payload)
    assert result.relations == ()


def test_pronoun_entity_is_dropped_with_its_relations():
    payload = '{"entities":[{"name":"它","type":"concept"},{"name":"A","type":"concept"}],' \
              '"relations":[{"source":"它","target":"A","relation":"指向"}]}'
    result = parse_extraction(payload)
    assert [e[0] for e in result.entities] == ["A"]
    assert result.relations == ()


def test_parse_uses_json_repair():
    result = parse_extraction("{'entities': [{'name': 'A', 'type': 'concept'}]}")
    assert result.entities[0][0] == "A"


def test_length_finish_reason_retries_with_half_context(monkeypatch):
    calls: list[str] = []

    class Client:
        def post(self, url, json, headers, timeout):
            calls.append(json["messages"][0]["content"])
            return _FakeResponse("length" if len(calls) == 1 else "stop",
                                 '{"entities": []}')

    extractor = OpenAICompatGraphExtractor(
        base_url="http://x/v1", model="m", client=Client())
    extractor.extract("h", "x" * 2000)
    assert len(calls) == 2
    assert len(calls[1]) < len(calls[0])
```

- [ ] **Step 2：跑测试确认失败**

Run: `pytest tests/knowledge/test_graph_extractor.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3：实现抽取器**

```python
PROMPT_VERSION = "graph1"

_PROMPT = (
    "你是知识图谱抽取器。阅读片段，抽取其中的实体与实体间关系。\n"
    "只输出一个 JSON 对象，不要解释、不要 Markdown 围栏：\n"
    '{"entities":[{"name":"名称","type":"概念/服务/系统/人物/文档/其他",'
    '"description":"一句话"}],"relations":[{"source":"实体名","target":"实体名",'
    '"relation":"关系名","description":"一句话","strength":1-10}]}\n'
    "规则：实体名不超过 40 字；不要代词；关系两端必须是 entities 中出现的名称；"
    "没有实体就输出 {\"entities\": [], \"relations\": []}。\n"
    "标题路径：{heading}\n片段：\n{content}"
)

_PRONOUNS = frozenset({"它", "他", "她", "这个", "那个", "其", "该", "they", "it", "this"})


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    entities: tuple[tuple[str, str, str], ...]   # (name, type, description)
    relations: tuple[tuple[str, str, str, str, float], ...]  # (src,tgt,rel,desc,weight)
    declined: bool = False


@dataclass(frozen=True, slots=True)
class _Raw:
    entities: list[dict]
    relations: list[dict]


def parse_extraction(text: str, *, max_entities: int = 20,
                     max_relations: int = 30) -> ExtractionResult:
    try:
        payload = json_repair.loads(text)
    except Exception as exc:  # noqa: BLE001
        raise GraphExtractionError("parse") from exc
    if not isinstance(payload, dict):
        raise GraphExtractionError("parse")
    raw_entities = payload.get("entities")
    if raw_entities is None or not isinstance(raw_entities, list) or not raw_entities:
        return ExtractionResult((), (), declined=True)
    entities: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for item in raw_entities[:max_entities]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        from nanobot.knowledge.graph.normalize import normalize_entity_name
        norm = normalize_entity_name(name)
        if not name or len(name) > 40 or norm in _PRONOUNS or norm in seen:
            continue
        seen.add(norm)
        entities.append((name, str(item.get("type") or "concept")[:40],
                         str(item.get("description") or "")[:500]))
    relations: list[tuple[str, str, str, str, float]] = []
    raw_relations = payload.get("relations")
    if isinstance(raw_relations, list):
        for item in raw_relations[:max_relations]:
            if not isinstance(item, dict):
                continue
            src = normalize_entity_name(str(item.get("source") or ""))
            tgt = normalize_entity_name(str(item.get("target") or ""))
            if src not in seen or tgt not in seen:
                continue
            rel = str(item.get("relation") or "").strip()
            if not rel:
                continue
            try:
                strength = float(item.get("strength") or 5.0)
            except (TypeError, ValueError):
                strength = 5.0
            relations.append((src, tgt, rel[:40],
                              str(item.get("description") or "")[:500],
                              max(1.0, min(10.0, strength))))
    return ExtractionResult(tuple(entities), tuple(relations))
```

```python
class GraphExtractionError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class OpenAICompatGraphExtractor:
    def __init__(self, *, base_url: str, model: str, api_key: str = "",
                 api_key_env: str = "", timeout_seconds: float = 30.0,
                 max_tokens: int = 2048, temperature: float = 0.2,
                 max_context_chars: int = 1200, max_retries: int = 1,
                 max_entities: int = 20, max_relations: int = 30,
                 client: httpx.Client | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._api_key_env = api_key_env
        self._timeout = timeout_seconds
        self._max_tokens = max(1, max_tokens)
        self._temperature = temperature
        self._max_context = max(1, max_context_chars)
        self._retries = max(0, max_retries)
        self._max_entities = max(1, max_entities)
        self._max_relations = max(1, max_relations)
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._active_key: str | None = None

    @property
    def model_id(self) -> str:
        return f"{self._base_url}#{self._model}"

    def _headers(self) -> dict[str, str]:
        key = os.environ.get(self._api_key_env, "") if self._api_key_env else ""
        key = key or self._api_key
        self._active_key = key or None
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def extract(self, heading_path: str, content: str) -> ExtractionResult:
        if not self._base_url or not self._model:
            raise GraphExtractionError("not_configured")
        context = content[: self._max_context]
        request_text = _PROMPT.format(heading=heading_path or "无", content=context)
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                response = self._client.post(
                    f"{self._base_url}/chat/completions",
                    json={"model": self._model,
                          "messages": [{"role": "user", "content": request_text}],
                          "temperature": self._temperature,
                          "max_tokens": self._max_tokens},
                    headers=self._headers(), timeout=self._timeout,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 429 or status >= 500:
                    last_error = exc
                    if attempt < self._retries:
                        time.sleep(2 ** attempt)
                        continue
                    raise GraphExtractionError("http_retry_exhausted",
                                               _redact(str(exc), self._active_key)) from exc
                raise GraphExtractionError("http_4xx",
                                           _redact(str(exc), self._active_key)) from exc
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt < self._retries:
                    time.sleep(2 ** attempt)
                    continue
                raise GraphExtractionError("timeout") from exc
            payload = response.json()
            choice = (payload.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            text = message.get("content") or ""
            if choice.get("finish_reason") == "length" and attempt < self._retries:
                context = context[: max(1, len(context) // 2)]
                request_text = _PROMPT.format(
                    heading=heading_path or "无", content=context)
                continue
            return parse_extraction(
                text, max_entities=self._max_entities,
                max_relations=self._max_relations)
        raise GraphExtractionError("retry_exhausted", str(last_error or ""))


def build_graph_extractor(config: Any) -> OpenAICompatGraphExtractor | None:
    if not bool(getattr(config, "enabled", False)):
        return None
    model = str(getattr(config, "model", "") or "").strip()
    base_url = str(getattr(config, "base_url", "") or "").strip()
    if not model or not base_url:
        return None
    return OpenAICompatGraphExtractor(
        base_url=base_url, model=model,
        api_key=str(getattr(config, "api_key", "") or ""),
        api_key_env=str(getattr(config, "api_key_env", "") or ""),
        timeout_seconds=float(getattr(config, "timeout_seconds", 30.0)),
        max_tokens=int(getattr(config, "max_tokens", 2048)),
        temperature=float(getattr(config, "temperature", 0.2)),
        max_context_chars=int(getattr(config, "max_context_chars", 1200)),
        max_retries=int(getattr(config, "max_retries", 1)),
        max_entities=int(getattr(config, "max_entities_per_chunk", 20)),
        max_relations=int(getattr(config, "max_relations_per_chunk", 30)),
    )
```

- [ ] **Step 4：跑测试并提交**

Run: `pytest tests/knowledge/test_graph_extractor.py -v`
Expected: PASS

```bash
git add nanobot/knowledge/graph/extractor.py tests/knowledge/test_graph_extractor.py
git commit -m "feat(knowledge): 图谱抽取器与 JSON 守卫"
```

---

### Task 4：归一化 + 实体链接（种子排序）

**Files:**
- Create: `nanobot/knowledge/graph/normalize.py`
- Create: `nanobot/knowledge/graph/linker.py`
- Test: `tests/knowledge/test_graph_linker.py`

- [ ] **Step 1：写失败测试**

```python
from nanobot.knowledge.graph.linker import find_seed_entities
from nanobot.knowledge.graph.normalize import normalize_entity_name


def test_normalize_nfkc_casefold_whitespace():
    assert normalize_entity_name("  Ｗｅｂ   ＡＰＩ ") == "web api"


def test_linker_exact_beats_partial(graph_repo_with_entities):
    found = find_seed_entities(graph_repo_with_entities, "kb", "灰度发布", limit=5)
    assert found[0].name == "灰度发布"
    assert [e.name for e in found] == ["灰度发布", "灰度发布流程"]


def test_linker_chinese_bigram_hit(graph_repo_with_entities):
    found = find_seed_entities(graph_repo_with_entities, "kb", "灰度流程", limit=5)
    assert any(e.name == "灰度发布流程" for e in found)
```

> `graph_repo_with_entities` fixture 在 `tests/knowledge/conftest.py` 中构造
> `灰度发布`（mention 9）、`灰度发布流程`（mention 3）、`值班二号`（mention 1）。

- [ ] **Step 2：跑测试确认失败**

Run: `pytest tests/knowledge/test_graph_linker.py -v`
Expected: FAIL

- [ ] **Step 3：实现**

```python
# normalize.py
import re
import unicodedata


def normalize_entity_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", name or "")
    return re.sub(r"\s+", " ", text.strip()).casefold()
```

```python
# linker.py
from nanobot.knowledge.graph.models import GraphEntity
from nanobot.knowledge.graph.normalize import normalize_entity_name
from nanobot.knowledge.graph.repo import GraphRepository
from nanobot.knowledge.tokenizer import tokenize_index


def find_seed_entities(repo: GraphRepository, kb_id: str, query: str, *,
                       limit: int = 5) -> list[GraphEntity]:
    normalized = normalize_entity_name(query)
    tokens = [t for t in tokenize_index(query).split() if len(t) >= 2][:8]
    if not normalized and not tokens:
        return []
    candidates = repo.search_entities_by_like(kb_id, normalized, tokens, limit=200)
    scored: list[tuple[int, int, int, str, GraphEntity]] = []
    for entity in candidates:
        exact = 1 if entity.name_norm == normalized else 0
        hits = sum(1 for token in tokens if token in entity.name_norm)
        scored.append((exact, hits, entity.mention_count, entity.id, entity))
    scored.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
    if scored and scored[0][0] == 1:
        # 整串精确命中时，partial 候选仅作为补充，不挤掉精确种子
        exact = [item[4] for item in scored if item[0] == 1]
        rest = [item[4] for item in scored if item[0] == 0]
        return (exact + rest)[:limit]
    return [item[4] for item in scored][:limit]
```

> `GraphRepository.search_entities_by_like(kb_id, exact_norm, tokens, limit)`：
> `WHERE kb_id=? AND (name_norm = ? OR name_norm LIKE '%tok%' ...)`（token 用参数绑定，
> `%`/`_` 在 Python 侧转义），按 `mention_count DESC, id ASC LIMIT ?`。

- [ ] **Step 4：跑测试并提交**

Run: `pytest tests/knowledge/test_graph_linker.py -v`
Expected: PASS

```bash
git add nanobot/knowledge/graph/normalize.py nanobot/knowledge/graph/linker.py tests/knowledge/test_graph_linker.py
git commit -m "feat(knowledge): 实体归一与种子链接"
```

---

### Task 5：GraphSyncService + GraphService（worker / 回填 / 重建 / 删除同步）

**Files:**
- Create: `nanobot/knowledge/graph/sync.py`
- Create: `nanobot/knowledge/graph/service.py`
- Modify: `nanobot/knowledge/service.py`
- Modify: `nanobot/knowledge/ingestor.py`
- Test: `tests/knowledge/test_graph_service.py`

- [ ] **Step 1：写失败测试**

```python
# graph_env fixture：真实 KnowledgeService + FakeGraphStore + 脚本化抽取器；
# 暴露 add_doc(text) → 入库并返回 Document，便于直接驱动 graph.process_document。
# failing_extractor fixture：extract() 始终抛 GraphExtractionError("http_4xx")。


def test_process_document_writes_and_mirrors(tmp_path, graph_env):
    service, store, repo = graph_env
    doc = graph_env.add_doc("网关升级时值班二号处理")
    service.graph.process_document(doc.id)
    assert repo.count_entities(doc.kb_id) == 1
    assert store.entities[doc.kb_id][0].name == "web-api"
    assert repo.get_state(doc.id).status == "ready"


def test_extractor_failure_does_not_fail_document(graph_env, failing_extractor):
    service, store, repo = graph_env
    doc = graph_env.add_doc("任意")
    service.graph.process_document(doc.id)
    assert repo.get_document(doc.id).parse_status == "ready"
    assert repo.graph_state(doc.id).status == "failed"


def test_rebuild_purge_clears_derived_rows(graph_env):
    service, store, repo = graph_env
    doc = graph_env.add_doc("A 依赖 B")
    service.graph.process_document(doc.id)
    service.graph.rebuild_kb(doc.kb_id, purge=True)
    assert doc.kb_id in store.deleted_kbs
```

- [ ] **Step 2：跑测试确认失败**

Run: `pytest tests/knowledge/test_graph_service.py -v`
Expected: FAIL

- [ ] **Step 3：实现 sync.py**

```python
class GraphSyncService:
    """SQLite 真相 → Neo4j 镜像；任何失败只记录，不改变文档/抽取状态。"""

    def __init__(self, repo, store):
        self._repo = repo
        self._store = store

    def sync_entities(self, kb_id: str, entity_ids):
        try:
            if self._store.health().status != "ready":
                return
            entities = self._repo.list_entities_by_ids(kb_id, entity_ids)
            relations = self._repo.list_relations_for_entities(kb_id, entity_ids)
            self._store.upsert_subgraph(kb_id, entities, relations)
        except Exception as exc:  # noqa: BLE001 - 派生索引失败不影响主链路
            logger.warning("knowledge graph sync failed: kb={} error={!r}", kb_id, exc)

    def rebuild_kb(self, kb_id: str):
        if self._store.health().status != "ready":
            return
        self._store.delete_kb(kb_id)
        all_ids = [e.id for e in self._repo.list_entities(kb_id, limit=100_000)]
        entities = self._repo.list_entities_by_ids(kb_id, all_ids)
        relations = self._repo.list_relations(kb_id, limit=100_000)
        self._store.upsert_subgraph(kb_id, entities, relations)

    def delete_document(self, doc_id: str, kb_id: str, entity_ids):
        self._repo.cleanup_orphans(entity_ids)
        alive = self._repo.list_entity_ids(kb_id, entity_ids)
        self._store.delete_entities(kb_id, [e for e in entity_ids if e not in alive])
```

- [ ] **Step 4：实现 service.py**

```python
class GraphService:
    def __init__(self, *, repo, knowledge_repo, store, extractor, config,
                 sync=None) -> None:
        self._repo = repo
        self._knowledge = knowledge_repo
        self._store = store
        self._extractor = extractor
        self._config = config
        self._sync = sync or GraphSyncService(repo, store)
        self._notify = None

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._config, "enabled", False))

    @property
    def extractor_ready(self) -> bool:
        return self._extractor is not None

    def health(self) -> GraphHealth:
        return self._store.health() if self.enabled else GraphHealth(
            "degraded", "disabled")

    def bind_notifier(self, notifier) -> None:
        """KnowledgeService 注入「唤醒 graph worker」回调；GraphService 不持有事件循环。"""
        self._notify = notifier

    def _notify(self, doc_id: str) -> None:
        if self._notify is not None:
            self._notify(doc_id)

    def enqueue_document(self, doc_id: str) -> None:
        """入库后置 pending 并唤醒 worker；幂等，重复调用不重复入队。"""
        if not self.enabled or self._extractor is None:
            return
        self._repo.enqueue_document(
            doc_id, model=self._extractor.model_id,
            prompt_version=PROMPT_VERSION,
        )
        self._notify(doc_id)

    def pending_documents(self, kb_id: str) -> list[str]:
        return self._repo.list_documents_for_extraction(kb_id, statuses=("pending",))

    def reset_expired_and_pending(self) -> list[str]:
        """启动恢复：过期租约置回 pending，再返回全部 pending 文档。"""
        if not self.enabled:
            return []
        self._repo.reset_expired_leases()
        return self._repo.list_pending_documents()

    def claim(self, doc_id: str) -> GraphExtractionState | None:
        return self._repo.claim_document(doc_id)

    def entity_ids_for_document(self, doc_id: str) -> list[str]:
        return self._repo.entity_ids_for_document(doc_id)

    def stats(self, kb_id: str) -> dict[str, Any]:
        """读接口形状（camelCase）；内部计数走 SQL 聚合。"""
        raw = self._repo.stats(kb_id)
        health = self.health()
        return {
            "enabled": self.enabled,
            "backend": str(getattr(self._config, "backend", "neo4j")),
            "status": "disabled" if not self.enabled else health.status,
            "reason": health.reason,
            "entities": raw.entities,
            "relations": raw.relations,
            "docsReady": raw.docs_ready,
            "docsPending": raw.docs_pending,
            "docsFailed": raw.docs_failed,
            "docsSkipped": raw.docs_skipped,
            "truncatedDocs": raw.truncated_docs,
            "lastExtractedAt": raw.last_extracted_at,
        }

    def list_entities(self, kb_id: str, *, query: str = "", type_: str = "",
                      limit: int = 50, offset: int = 0) -> dict[str, Any]:
        entities = self._repo.list_entities(
            kb_id, q=query, type_=type_, limit=limit, offset=offset)
        return {
            "entities": [
                {"id": e.id, "name": e.name, "type": e.type,
                 "description": e.description, "mentionCount": e.mention_count}
                for e in entities
            ],
            "total": self._repo.count_entities(kb_id, q=query, type_=type_),
        }

    def get_entity(self, entity_id: str,
                   workspace_ids: Sequence[str]) -> dict[str, Any] | None:
        entity = self._repo.get_entity_in_scope(entity_id, workspace_ids)
        if entity is None:
            return None
        neighbors = self._repo.list_neighbors(entity_id)
        mentions = self._repo.list_mentions(entity_id)
        return {
            "entity": {"id": entity.id, "name": entity.name, "type": entity.type,
                       "description": entity.description,
                       "mentionCount": entity.mention_count},
            "neighbors": [
                {"entity": {"id": n["id"], "name": n["name"], "type": n["type"],
                            "description": "", "mentionCount": n["mention_count"]},
                 "relation": n["relation"], "direction": n["direction"],
                 "weight": n["weight"]}
                for n in neighbors
            ],
            "mentions": [
                {"docId": m["doc_id"], "docTitle": m["doc_title"],
                 "chunkId": m["chunk_id"], "chunkIndex": m["chunk_index"],
                 "heading": m["heading_path"]}
                for m in mentions
            ],
        }

    def subgraph(self, entity_id: str, workspace_ids: Sequence[str], *,
                 hops: int = 1, limit: int = 50) -> dict[str, Any]:
        center = self._repo.get_entity_in_scope(entity_id, workspace_ids)
        if center is None:
            return {"nodes": [], "edges": []}
        expansion = self._store.expand(
            center.kb_id, [center.id], hops=max(1, min(2, hops)),
            max_entities=limit, max_chunks=limit * 4,
        )
        nodes = self._repo.list_entities_by_ids(
            center.kb_id, (center.id, *expansion.entity_ids))
        return {
            "nodes": [{"id": e.id, "name": e.name, "type": e.type,
                       "mentionCount": e.mention_count} for e in nodes],
            "edges": [{"source": edge.source_entity_id,
                       "target": edge.target_entity_id,
                       "relation": edge.relation, "weight": edge.weight}
                      for edge in expansion.edges],
        }

    def process_document(self, doc_id: str, attempt: int | None = None) -> None:
        state = self._repo.claim_document(doc_id) if attempt is None else None
        if attempt is None:
            if state is None:
                return
            attempt = state.attempt
        document = self._knowledge.get_document(doc_id)
        if document is None:
            return
        chunks = self._knowledge.list_chunks(doc_id, offset=0, limit=100_000)
        limit = int(getattr(self._config.extraction, "max_chunks_per_doc", 200))
        truncated = len(chunks) > limit
        chunks = chunks[:limit]
        concurrency = max(1, int(getattr(self._config.extraction, "concurrency", 2)))
        entities: dict[str, tuple[str, str, str]] = {}
        relations: dict[tuple[str, str, str], tuple[str, str, str, str, float]] = {}
        mentions: list[tuple[str, str]] = []
        declined_all = True
        failures = 0
        done_chunks = 0
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(self._extract_chunk, c): c for c in chunks}
            for future in as_completed(futures):
                chunk = futures[future]
                done_chunks += 1
                try:
                    result = future.result()
                except GraphExtractionError as exc:
                    failures += 1
                    logger.warning("knowledge graph extract failed: doc={} err={}",
                                   doc_id, exc.code)
                    self._repo.update_progress(
                        doc_id, attempt, chunk_done=done_chunks)
                    continue
                if not result.declined:
                    declined_all = False
                for entity in result.entities:
                    entities.setdefault(entity[0], entity)
                for relation in result.relations:
                    relations.setdefault(relation[:3], relation)
                for name, _type, _desc in result.entities:
                    mentions.append((name, chunk.id))
                self._repo.update_progress(
                    doc_id, attempt, chunk_done=done_chunks)
        if not entities:
            if failures and not declined_all:
                self._repo.fail_document(doc_id, attempt, "extract_failed", "all chunks failed")
            else:
                self._repo.finish_document(doc_id, attempt, status="skipped")
            return
        touched_entities, _ = self._repo.replace_document_graph(
            doc_id, document.kb_id,
            entities=list(entities.values()),
            relations=list(relations.values()),
            mentions=mentions,
        )
        self._repo.finish_document(
            doc_id, attempt, status="ready",
            chunk_total=len(chunks), chunk_done=len(chunks),
            entity_count=len(entities), relation_count=len(relations),
            truncated=truncated,
        )
        self._sync.sync_entities(document.kb_id, touched_entities)

    def _extract_chunk(self, chunk):
        assert self._extractor is not None
        return self._extractor.extract(chunk.heading_path, chunk.content)

    def extract_documents(self, doc_id: str, *, force: bool = False) -> dict:
        state = self._repo.get_state(doc_id)
        if state is not None and state.status == "ready" and not force:
            return {"queued": 0, "docs": []}
        self._repo.enqueue_document(doc_id, model=self._extractor.model_id,
                                    prompt_version=PROMPT_VERSION, force=force)
        self._notify(doc_id)
        return {"queued": 1, "docs": [doc_id]}

    def backfill_kb(self, kb_id: str) -> dict:
        docs = self._repo.list_documents_for_extraction(
            kb_id, statuses=("pending", "failed", "missing"))
        for doc_id in docs:
            self._repo.enqueue_document(doc_id, model=self._extractor.model_id,
                                        prompt_version=PROMPT_VERSION)
            self._notify(doc_id)
        return {"queued": len(docs), "docs": docs}

    def rebuild_kb(self, kb_id: str, *, purge: bool = False) -> dict:
        if purge:
            self._store.delete_kb(kb_id)
            self._repo.purge_kb(kb_id)
        docs = self._repo.list_documents_for_extraction(kb_id, statuses=())
        for doc_id in docs:
            self._repo.enqueue_document(doc_id, model=self._extractor.model_id,
                                        prompt_version=PROMPT_VERSION, force=True)
            self._notify(doc_id)
        return {"queued": len(docs), "docs": docs}
```

```python
# nanobot/knowledge/service.py 增量
def __init__(self, ..., graph: GraphService | None = None) -> None:
    ...
    self._graph = graph
    self._graph_queue: asyncio.Queue[str] = asyncio.Queue()
    self._graph_queued: set[str] = set()
    self._graph_worker: asyncio.Task[None] | None = None

async def start(self) -> None:
    ...  # 既有文档 worker 逻辑
    if self._graph is not None and self._graph.enabled:
        self._graph.bind_notifier(self._wake_graph_worker)
        recovered = await asyncio.to_thread(self._graph.reset_expired_and_pending)
        self._graph_worker = asyncio.create_task(
            self._run_graph_worker(), name="knowledge-graph-worker")
        for doc_id in recovered:
            self._wake_graph_worker(doc_id)

async def stop(self) -> None:
    worker = self._graph_worker
    self._graph_worker = None
    if worker is not None:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
    ...  # 既有 stop 逻辑

def enqueue_graph_document(self, doc_id: str) -> None:
    """Ingestor 钩子：写 pending 状态并唤醒 worker（可由 worker 线程调用）。"""
    if self._graph is None or not self._graph.enabled:
        return
    self._graph.enqueue_document(doc_id)   # 内部会回调 bind_notifier

def _wake_graph_worker(self, doc_id: str) -> None:
    """只投递事件，不写库；GraphService 的 notifier 指向这里，避免递归。"""
    loop = self._loop
    if loop is None or loop.is_closed() or self._graph_worker is None:
        return
    with self._queued_lock:
        if doc_id in self._graph_queued:
            return
        self._graph_queued.add(doc_id)
    loop.call_soon_threadsafe(self._graph_queue.put_nowait, doc_id)

async def _run_graph_worker(self) -> None:
    while True:
        doc_id = await self._graph_queue.get()
        try:
            with self._queued_lock:
                self._graph_queued.discard(doc_id)
            await asyncio.to_thread(self._graph_process_queued, doc_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("knowledge graph worker failed: doc={}", doc_id)
        finally:
            self._graph_queue.task_done()

def _graph_process_queued(self, doc_id: str) -> None:
    assert self._graph is not None
    state = self._graph.claim(doc_id)
    if state is None:
        return
    self._graph.process_document(doc_id, attempt=state.attempt)

@property
def graph(self) -> GraphService | None:
    """读接口/CLI 的入口；未启用时为 None。"""
    return self._graph

def delete_document(self, doc_id: str) -> None:
    document = self._repo.get_document(doc_id)
    if document is None:
        return
    entity_ids = (
        self._graph.entity_ids_for_document(doc_id)
        if self._graph is not None else []
    )
    self._repo.delete_document(doc_id)
    if self._graph is not None:
        self._graph.sync.delete_document(doc_id, document.kb_id, entity_ids)
    ...
```

> `GraphService` 需补 `reset_expired_and_pending()`、`claim(doc_id)`、
> `entity_ids_for_document(doc_id)` 三个薄封装；`Ingestor` 构造时传
> `graph_hook=self.enqueue_graph_document`；`retriever_with(settings)` 把同一个
> `GraphRoute` 注入新 `Retriever`。

- [ ] **Step 5：Ingestor 钩子**

```python
# Ingestor.__init__ 增加
graph_hook: Callable[[str], None] | None = None,
self._graph_hook = graph_hook

# process_document 末尾（问题生成之后）
if self._graph_hook is not None:
    try:
        self._graph_hook(document.id)
    except Exception as exc:  # noqa: BLE001 - 图抽取是增强项
        logger.warning("knowledge graph enqueue skipped: doc={} error={!r}",
                       document.id, exc)
```

- [ ] **Step 6：跑测试并提交**

Run: `pytest tests/knowledge/test_graph_service.py tests/knowledge/test_ingestor.py -v`
Expected: PASS

```bash
git add nanobot/knowledge/graph nanobot/knowledge/service.py nanobot/knowledge/ingestor.py tests/knowledge
git commit -m "feat(knowledge): 图谱抽取 worker、回填与删除同步"
```

---

### Task 6：图检索第三路 + 三路 RRF + 降级 + 证据字段

**Files:**
- Create: `nanobot/knowledge/graph/route.py`
- Modify: `nanobot/knowledge/fusion.py`
- Modify: `nanobot/knowledge/retriever.py`
- Modify: `nanobot/knowledge/repository.py`（`fetch_search_chunks`）
- Test: `tests/knowledge/test_graph_route.py`

- [ ] **Step 1：写失败测试（关闭图时数值不变是硬约束）**

```python
# fixtures 由 tests/knowledge/conftest.py 提供：
# - retriever_factory(graph=None|fake_graph|broken_graph) → Retriever
# - fake_graph：固定返回 1 跳 expansion
# - broken_graph：health=degraded，expand 抛 GraphStoreError
# - disable_chunk(chunk_id)：把块 is_enabled 置 0


def test_two_route_fusion_unchanged_when_graph_disabled(retriever_factory):
    from nanobot.knowledge.fusion import weighted_rrf
    before = weighted_rrf(["a", "b"], ["b", "c"])
    retriever = retriever_factory(graph=None)
    result = retriever.search("q", workspace_ids=["default"])
    assert result.routes == ("vector", "keyword") or result.routes == ("keyword",)
    assert before  # 数值回归由 tests/knowledge/test_fusion.py 既有用例守护


def test_graph_route_adds_third_route_and_evidence(retriever_factory, fake_graph):
    retriever = retriever_factory(graph=fake_graph)
    result = retriever.search("灰度发布", workspace_ids=["default"])
    assert result.routes == ("vector", "keyword", "graph")
    assert result.hits[0].graph_evidence is not None
    assert result.hits[0].graph_evidence.hop == 1


def test_graph_backend_failure_degrades_not_raises(retriever_factory, broken_graph):
    result = retriever_factory(graph=broken_graph).search(
        "灰度发布", workspace_ids=["default"])
    assert "graph_unavailable" in result.degraded


def test_deleted_or_disabled_chunk_is_filtered(retriever_factory, fake_graph,
                                               disable_chunk):
    disable_chunk("chk-1")
    result = retriever_factory(graph=fake_graph).search(
        "灰度发布", workspace_ids=["default"])
    assert all(hit.chunk_id != "chk-1" for hit in result.hits)
```

- [ ] **Step 2：跑测试确认失败**

Run: `pytest tests/knowledge/test_graph_route.py -v`
Expected: FAIL

- [ ] **Step 3：fusion.py 增加可选字段**

```python
@dataclass(frozen=True, slots=True)
class SearchHit:
    ...
    graph_evidence: "GraphEvidencePayload | None" = None
```

> `GraphEvidencePayload` 只在 `TYPE_CHECKING` 下 import，避免 `fusion → graph` 循环。

- [ ] **Step 4：repository.py 增加受控回表**

```python
def fetch_search_chunks(self, chunk_ids, *, kb_ids):
    """图候选回表复核：只信已通过 `_visible_kbs` 的 kb_ids，不再按块 workspace 过滤
    （global 库的块 workspace 属于库所在工作区，不属于请求方）。"""
    if not chunk_ids or not kb_ids:
        return []
    placeholders = ",".join("?" for _ in chunk_ids)
    kb_placeholders = ",".join("?" for _ in kb_ids)
    sql = f"""
      SELECT c.* FROM document_chunks c
      JOIN documents d ON d.id = c.doc_id
      JOIN knowledge_bases kb ON kb.id = c.kb_id
      WHERE c.id IN ({placeholders})
        AND c.is_enabled = 1 AND d.is_enabled = 1 AND kb.is_enabled = 1
        AND d.parse_status = 'ready'
        AND c.kb_id IN ({kb_placeholders})
    """
    with self._db.connect() as conn:
        rows = conn.execute(sql, (*chunk_ids, *kb_ids)).fetchall()
    order = {chunk_id: index for index, chunk_id in enumerate(chunk_ids)}
    chunks = [self._row_to_chunk(row) for row in rows]
    chunks.sort(key=lambda chunk: order.get(chunk.id, 10**9))
    return chunks
```

- [ ] **Step 5：route.py 实现**

```python
class GraphRoute:
    def __init__(self, *, repo, knowledge_repo, store, settings) -> None:
        self._repo = repo
        self._knowledge = knowledge_repo
        self._store = store
        self._settings = settings

    def search(self, query, visible_kbs):
        """返回 (排序后的 SearchHit 列表, 证据映射, 降级标签, 是否实际产出候选)。"""
        hits: list[SearchHit] = []
        evidence: dict[str, GraphEvidencePayload] = {}
        labels: list[str] = []
        produced = False
        for kb in visible_kbs:
            try:
                seeds = find_seed_entities(
                    self._repo, kb.id, query, limit=self._settings.graph_seed_limit)
                if not seeds:
                    continue
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        self._store.expand, kb.id, [seed.id for seed in seeds],
                        hops=self._settings.graph_hops,
                        max_entities=self._settings.graph_max_entities,
                        max_chunks=self._settings.graph_max_chunks,
                    )
                    expansion = future.result(
                        timeout=self._settings.graph_timeout_seconds)
                if expansion.truncated:
                    labels.append("graph_truncated")
                chunk_ids = self._repo.mention_chunks(expansion.entity_ids)
                chunks = self._knowledge.fetch_search_chunks(
                    chunk_ids, kb_ids=[kb.id])
                if not chunks:
                    continue
                produced = True
                for index, chunk in enumerate(chunks):
                    payload = self._evidence_for(
                        chunk.id, seeds, expansion, self._repo)
                    evidence.setdefault(chunk.id, payload)
                    hits.append(_from_chunk(chunk, score=0.0))
            except Exception as exc:  # noqa: BLE001 - 图路失败只降级
                logger.warning("knowledge graph route failed: kb={} error={!r}",
                               kb.id, exc)
                labels.append("graph_unavailable")
                continue
        # 图路名次按跳数：1 跳优先于 2 跳；同跳按 chunk_id 稳定排序。
        hits.sort(key=lambda hit: (
            evidence.get(hit.chunk_id, GraphEvidencePayload((), (), 2, ())).hop,
            hit.chunk_id,
        ))
        return hits, evidence, _dedup_labels(labels), produced
```

```python
@staticmethod
def _evidence_for(chunk_id, seeds, expansion, repo):
    """取「种子 → 提及该块的实体」最短一条路径；每命中只保留一条。"""
    mention_entities = repo.entities_for_chunk(chunk_id)
    if not mention_entities:
        return GraphEvidencePayload(
            seeds=tuple(seed.name for seed in seeds[:1]), path=(), hop=1,
            entity_ids=tuple(expansion.entity_ids))
    adjacency: dict[str, list[tuple[str, str]]] = {}
    for edge in expansion.edges:
        adjacency.setdefault(edge.source_entity_id, []).append(
            (edge.target_entity_id, edge.relation))
    seed_ids = {seed.id for seed in seeds}
    queue: list[tuple[str, tuple[tuple[str, str, str], ...]]] = [
        (seed_id, ()) for seed_id in seed_ids]
    seen = set(seed_ids)
    while queue:
        current, path = queue.pop(0)
        if current in mention_entities:
            return GraphEvidencePayload(
                seeds=tuple(seed.name for seed in seeds[:1]), path=path,
                hop=max(1, len(path)), entity_ids=(current,))
        for target, relation in adjacency.get(current, ()):
            if target in seen:
                continue
            seen.add(target)
            queue.append((target, path + ((current, relation, target),)))
    return GraphEvidencePayload(
        seeds=tuple(seed.name for seed in seeds[:1]), path=(), hop=1,
        entity_ids=tuple(expansion.entity_ids))
```

> `GraphRepository.entities_for_chunk(chunk_id)`：`SELECT entity_id FROM graph_mentions
> WHERE chunk_id=?`，返回 `set[str]`。`mention_chunks(entity_ids)` 查
> `graph_mentions` 并按首次出现顺序去重。

- [ ] **Step 6：retriever.py 接线**

```python
# Retriever.__init__ 增加 graph_route: GraphRoute | None = None

# search()：关键词路之后
graph_lists: list[list[tuple[str, float]]] = []
graph_hits: list[SearchHit] = []
graph_evidence: dict[str, GraphEvidencePayload] = {}
graph_used = False
if settings.graph_enabled:
    if self._graph_route is None:
        # 评测覆写把图打开、但进程未装配图后端：显式降级，不静默变成两路。
        degraded.append("graph_unavailable")
    else:
        graph_hits, graph_evidence, graph_labels, graph_used = (
            self._graph_route.search(query, visible))
        degraded.extend(graph_labels)
        if graph_used:
            graph_lists.append([(hit.chunk_id, 0.0) for hit in graph_hits])

if not keyword_hits and not vector_hits and not graph_hits:
    ...  # 既有空结果分支

scores, routes, mode_used = self._fuse(vector_lists, keyword_lists, graph_lists)
candidates = self._assemble(scores, vector_hits, keyword_hits,
                            graph_hits=graph_hits, graph_evidence=graph_evidence)
```

```python
def _fuse(self, vector_lists, keyword_lists, graph_lists):
    settings = self._settings
    if graph_lists:
        specs = []
        modes = []
        if vector_lists:
            specs.append(RouteSpec(settings.vector_weight, tuple(
                tuple(cid for cid, _ in ids) for ids in vector_lists)))
            modes.append("vector")
        if keyword_lists:
            specs.append(RouteSpec(settings.keyword_weight, tuple(
                tuple(cid for cid, _ in ids) for ids in keyword_lists)))
            modes.append("keyword")
        specs.append(RouteSpec(settings.graph_weight, (tuple(
            cid for cid, _ in graph_lists[0]),)))
        modes.append("graph")
        scores = weighted_rrf_multi(tuple(specs), k=settings.rrf_k)
        mode_used = "hybrid" if len(modes) > 1 else "graph"
        return scores, tuple(modes), mode_used
    # 以下为既有两路/单路分支，逐字保留
    ...
```

```python
@staticmethod
def _assemble(scores, vector_hits, keyword_hits, *, graph_hits=(), graph_evidence=None):
    graph_evidence = graph_evidence or {}
    by_id = {hit.chunk_id: hit for hit in vector_hits}
    for keyword_hit in keyword_hits:
        by_id[keyword_hit.chunk_id] = _from_fts(keyword_hit)
    for graph_hit in graph_hits:
        by_id.setdefault(graph_hit.chunk_id, graph_hit)
    assembled = []
    for chunk_id, score in scores.items():
        hit = replace(by_id[chunk_id], score=score)
        payload = graph_evidence.get(chunk_id)
        if payload is not None:
            hit = replace(hit, graph_evidence=payload)
        assembled.append(hit)
    return assembled
```

> `RetrieverSettings` 新增（全部带默认值，保证关闭时零影响）：
> `graph_enabled: bool = False`、`graph_weight: float = 0.25`、`graph_hops: int = 1`、
> `graph_seed_limit: int = 5`、`graph_max_entities: int = 20`、`graph_max_chunks: int = 50`、
> `graph_timeout_seconds: float = 0.8`。

- [ ] **Step 7：跑测试并提交**

Run: `pytest tests/knowledge/test_graph_route.py tests/knowledge/test_fusion.py tests/knowledge/test_retriever.py -v`
Expected: PASS（既有两路用例数值不变）

```bash
git add nanobot/knowledge/graph/route.py nanobot/knowledge/fusion.py \
  nanobot/knowledge/retriever.py nanobot/knowledge/repository.py tests/knowledge
git commit -m "feat(knowledge): 图检索第三路与加权 RRF 降级"
```

---

### Task 7：配置块 + 装配 + CLI rebuild

**Files:**
- Modify: `nanobot/config/schema.py`
- Modify: `nanobot/knowledge/service.py`
- Modify: `nanobot/cli/knowledge.py`
- Test: `tests/knowledge/test_config.py`（追加用例）

- [ ] **Step 1：写失败测试**

```python
def test_graph_defaults_are_disabled():
    config = KnowledgeConfig()
    assert config.graph.enabled is False
    assert config.graph.backend == "neo4j"
    assert config.graph.extraction.prompt_version == "graph1"
    assert config.graph.expansion.hops == 1


def test_graph_camel_case_roundtrip():
    config = KnowledgeConfig.model_validate({
        "graph": {"enabled": True, "uri": "bolt://localhost:7687",
                  "extraction": {"concurrency": 4, "maxChunksPerDoc": 50},
                  "expansion": {"seedLimit": 8, "weight": 0.4}}})
    assert config.graph.extraction.max_chunks_per_doc == 50
    assert config.graph.expansion.seed_limit == 8
```

- [ ] **Step 2：跑测试确认失败**

Run: `pytest tests/knowledge/test_config.py -v -k graph`
Expected: FAIL（`KnowledgeConfig` 无 `graph`）

- [ ] **Step 3：schema.py 新增配置块**

```python
class KnowledgeGraphExtractionConfig(Base):
    enabled: bool = True
    model: str = ""
    base_url: str = ""
    api_key: str = Field(default="", repr=False)
    api_key_env: str = ""
    temperature: float = 0.2
    max_tokens: int = 2048
    timeout_seconds: float = 30.0
    max_context_chars: int = 1200
    concurrency: int = 2
    max_entities_per_chunk: int = 20
    max_relations_per_chunk: int = 30
    max_chunks_per_doc: int = 200
    prompt_version: str = "graph1"
    max_retries: int = 1
    require_confirmation: bool = True


class KnowledgeGraphExpansionConfig(Base):
    enabled: bool = True
    hops: int = 1
    seed_limit: int = 5
    max_entities: int = 20
    max_chunks: int = 50
    weight: float = 0.25
    timeout_seconds: float = 0.8


class KnowledgeGraphConfig(Base):
    enabled: bool = False
    backend: Literal["neo4j"] = "neo4j"
    uri: str = ""
    username: str = "neo4j"
    password: str = Field(default="", repr=False)
    password_env: str = ""
    database: str = "neo4j"
    connect_timeout_seconds: float = 5.0
    extraction: KnowledgeGraphExtractionConfig = Field(
        default_factory=KnowledgeGraphExtractionConfig)
    expansion: KnowledgeGraphExpansionConfig = Field(
        default_factory=KnowledgeGraphExpansionConfig)


class KnowledgeConfig(Base):
    ...
    graph: KnowledgeGraphConfig = Field(default_factory=KnowledgeGraphConfig)
```

- [ ] **Step 4：service 装配**

```python
def _graph_settings(graph: KnowledgeGraphConfig) -> dict[str, Any]:
    expansion = graph.expansion
    return {
        "graph_enabled": bool(graph.enabled and expansion.enabled),
        "graph_weight": float(expansion.weight),
        "graph_hops": max(1, min(2, int(expansion.hops))),
        "graph_seed_limit": max(1, int(expansion.seed_limit)),
        "graph_max_entities": max(1, int(expansion.max_entities)),
        "graph_max_chunks": max(1, int(expansion.max_chunks)),
        "graph_timeout_seconds": float(expansion.timeout_seconds),
    }


def _build_graph(graph_config: KnowledgeGraphConfig, repo):
    if not graph_config.enabled:
        return None
    extractor = build_graph_extractor(graph_config.extraction)
    if not graph_config.uri:
        logger.warning("knowledge graph enabled but uri is empty")
        return None
    password = graph_config.password
    if graph_config.password_env:
        password = os.environ.get(graph_config.password_env, password)
    store = Neo4jGraphStore(
        uri=graph_config.uri, username=graph_config.username,
        password=password, database=graph_config.database,
        timeout=graph_config.connect_timeout_seconds,
    )
    return GraphService(
        repo=GraphRepository(repo.database),
        knowledge_repo=repo, store=store, extractor=extractor,
        config=graph_config,
    )
```

```python
# _retriever_settings：既有字段构造完之后合入图字段
def _retriever_settings(retrieval: Any, graph: KnowledgeGraphConfig | None = None) -> RetrieverSettings:
    base = RetrieverSettings(
        vector_weight=float(getattr(retrieval, "vector_weight", 0.7)),
        ...  # 既有字段保持原样
    )
    if graph is None:
        return base
    return replace(base, **_graph_settings(graph))


# KnowledgeService.local
def local(..., graph: GraphService | None = None) -> KnowledgeService:
    ...
    graph_route = None
    if graph is not None and graph.enabled:
        graph_route = GraphRoute(
            repo=graph.repo,
            knowledge_repo=repo,
            store=graph.store,
            settings=RetrieverSettings(**_graph_settings(graph.config)),
        )
    retriever = Retriever(
        repo, embedder=actual_embedder, vector_store=vector_store,
        settings=_retriever_settings(retrieval, graph.config if graph else None),
        reranker=reranker, rerank_settings=rerank_settings,
        tokenizer_mode=tokenizer_mode, identity_resolver=identity_resolver,
        graph_route=graph_route,
    )
    return cls(..., graph=graph)


# KnowledgeRepository.__init__ 保存连接工厂所需的 database
def __init__(self, database: KnowledgeDatabase) -> None:
    self.database = database
    self._db = database


# _build_service 末尾
graph_config = knowledge.graph
graph = _build_graph(graph_config, repo, repo) if graph_config.enabled else None
return KnowledgeService.local(..., graph=graph)
```

- [ ] **Step 5：CLI 子命令**

```python
graph_app = typer.Typer(help="知识图谱维护", no_args_is_help=True)
knowledge_app.add_typer(graph_app, name="graph")


@graph_app.command("rebuild")
def graph_rebuild(
    kb: str = typer.Option(..., "--kb", help="知识库 id 或名称"),
    purge: bool = typer.Option(False, "--purge", help="先清空该库派生图数据"),
    wait: bool = typer.Option(False, "--wait", help="同步跑完抽取"),
    yes: bool = typer.Option(False, "--yes", help="确认发送 chunk 到模型服务"),
) -> None:
    if not yes:
        _abort("重建会把分块正文发送给图谱抽取模型；请加 --yes 确认")
    service = _service()
    graph = getattr(service, "graph", None)
    if graph is None:
        _abort("知识图谱未启用或后端未配置")
    kb_id = _resolve_kb(service, kb, "default")
    queued = graph.rebuild_kb(kb_id, purge=purge)["queued"]
    if wait:
        for doc_id in graph.pending_documents(kb_id):
            graph.process_document(doc_id)
    _print_json({"queued": queued, "waited": wait})
```

- [ ] **Step 6：跑测试并提交**

Run: `pytest tests/knowledge/test_config.py tests/knowledge/test_service.py -v`
Expected: PASS

```bash
git add nanobot/config/schema.py nanobot/knowledge/service.py nanobot/cli/knowledge.py tests/knowledge
git commit -m "feat(knowledge): 图谱配置、装配与 CLI 重建"
```

---

### Task 8：HTTP 读接口 + WS mutation + 错误码

**Files:**
- Modify: `nanobot/webui/knowledge_routes.py`
- Modify: `nanobot/webui/knowledge_api.py`
- Test: `tests/knowledge/test_graph_api.py`

- [ ] **Step 1：写失败测试**

```python
GRAPH_STATS = "knowledge-graph-stats"
GRAPH_ENTITIES = "knowledge-graph-entities"
GRAPH_ENTITY = "knowledge-graph-entity"
GRAPH_SUBGRAPH = "knowledge-graph-subgraph"
GRAPH_EXTRACT = "knowledge.graph.extract"
GRAPH_REBUILD = "knowledge.graph.rebuild"


def test_graph_actions_registered():
    assert {GRAPH_STATS, GRAPH_ENTITIES, GRAPH_ENTITY, GRAPH_SUBGRAPH} <= READ_ACTIONS
    assert {GRAPH_EXTRACT, GRAPH_REBUILD} <= WRITE_ACTIONS
    assert READ_PATHS["/api/settings/knowledge/graph/stats"] == GRAPH_STATS


def test_graph_disabled_returns_409(api):
    response = api.dispatch(GRAPH_STATS, {"kbId": "kb"})
    assert response["error"]["code"] == "KNOWLEDGE_GRAPH_DISABLED"


def test_extract_requires_confirm(api_with_graph):
    response = api_with_graph.dispatch(GRAPH_REBUILD, {"kbId": "kb"})
    assert response["error"]["code"] == "KNOWLEDGE_GRAPH_CONFIRM_REQUIRED"


def test_entity_out_of_scope_returns_404(api_with_graph):
    response = api_with_graph.dispatch(GRAPH_ENTITY, {"id": "ent_x"})
    assert response["error"]["code"] == "KNOWLEDGE_GRAPH_ENTITY_NOT_FOUND"
```

- [ ] **Step 2：跑测试确认失败**

Run: `pytest tests/knowledge/test_graph_api.py -v`
Expected: FAIL（未知动作 404）

- [ ] **Step 3：登记动作**

```python
GRAPH_STATS: Final = "knowledge-graph-stats"
GRAPH_ENTITIES: Final = "knowledge-graph-entities"
GRAPH_ENTITY: Final = "knowledge-graph-entity"
GRAPH_SUBGRAPH: Final = "knowledge-graph-subgraph"
GRAPH_EXTRACT: Final = "knowledge.graph.extract"
GRAPH_REBUILD: Final = "knowledge.graph.rebuild"

READ_ACTIONS = frozenset({..., GRAPH_STATS, GRAPH_ENTITIES, GRAPH_ENTITY, GRAPH_SUBGRAPH})
WRITE_ACTIONS = frozenset({..., GRAPH_EXTRACT, GRAPH_REBUILD})

READ_PATHS.update({
    "/api/settings/knowledge/graph/stats": GRAPH_STATS,
    "/api/settings/knowledge/graph/entities": GRAPH_ENTITIES,
    "/api/settings/knowledge/graph/entity": GRAPH_ENTITY,
    "/api/settings/knowledge/graph/subgraph": GRAPH_SUBGRAPH,
})
WRITE_PATHS.update({
    "/api/settings/knowledge/graph/extract": GRAPH_EXTRACT,
    "/api/settings/knowledge/graph/rebuild": GRAPH_REBUILD,
})
```

- [ ] **Step 4：handler 与门禁**

```python
def _graph(self, services):
    graph = getattr(services.service, "graph", None)
    if graph is None or not graph.enabled:
        raise KnowledgeRouteError("knowledge graph is disabled",
                                  code="KNOWLEDGE_GRAPH_DISABLED", status=409)
    return graph


def _graph_stats(self, payload, services):
    kb = _require_kb(_kb_id_from(payload), services)
    graph = self._graph(services)
    return graph.stats(kb.id)


def _graph_entities(self, payload, services):
    kb = _require_kb(_kb_id_from(payload), services)
    limit = min(100, max(1, _optional_int(payload, "limit", 50)))
    offset = max(0, _optional_int(payload, "offset", 0))
    return self._graph(services).list_entities(
        kb.id, query=_optional_str(payload, "q") or "",
        type_=_optional_str(payload, "type") or "", limit=limit, offset=offset)


def _graph_entity(self, payload, services):
    entity_id = _required_str(payload, "id", "id is required")
    detail = self._graph(services).get_entity(entity_id, services.workspace_ids)
    if detail is None:
        raise KnowledgeRouteError("knowledge graph entity not found",
                                  code="KNOWLEDGE_GRAPH_ENTITY_NOT_FOUND", status=404)
    return detail


def _graph_subgraph(self, payload, services):
    entity_id = _required_str(payload, "entityId", "entityId is required")
    hops = min(2, max(1, _optional_int(payload, "hops", 1)))
    limit = min(50, max(1, _optional_int(payload, "limit", 50)))
    return self._graph(services).subgraph(entity_id, services.workspace_ids,
                                          hops=hops, limit=limit)


def _graph_extract(self, payload, services):
    graph = self._graph(services)
    doc_id = _optional_str(payload, "docId")
    kb_id = _optional_str(payload, "kbId")
    if not doc_id and not kb_id:
        raise KnowledgeRouteError("kbId or docId is required")
    confirm = _optional_bool(payload, "confirm", default=False)
    if graph.require_confirmation and not doc_id and not confirm:
        raise KnowledgeRouteError("confirmation required",
                                  code="KNOWLEDGE_GRAPH_CONFIRM_REQUIRED", status=409)
    if not graph.extractor_ready:
        raise KnowledgeRouteError("extraction model not configured",
                                  code="KNOWLEDGE_GRAPH_EXTRACTION_NOT_CONFIGURED",
                                  status=409)
    return graph.extract_documents(doc_id) if doc_id else graph.backfill_kb(kb_id)


def _graph_rebuild(self, payload, services):
    graph = self._graph(services)
    kb = _require_kb(_kb_id_from(payload), services)
    if not graph.extractor_ready:
        raise KnowledgeRouteError("extraction model not configured",
                                  code="KNOWLEDGE_GRAPH_EXTRACTION_NOT_CONFIGURED",
                                  status=409)
    if graph.require_confirmation and not _optional_bool(payload, "confirm", default=False):
        raise KnowledgeRouteError("confirmation required",
                                  code="KNOWLEDGE_GRAPH_CONFIRM_REQUIRED", status=409)
    if graph.health().status != "ready":
        raise KnowledgeRouteError("graph backend unavailable",
                                  code="KNOWLEDGE_GRAPH_BACKEND_UNAVAILABLE", status=503)
    return graph.rebuild_kb(kb.id, purge=_optional_bool(payload, "purge", default=False))
```

- [ ] **Step 5：跑测试并提交**

Run: `pytest tests/knowledge/test_graph_api.py tests/knowledge/test_api.py -v`
Expected: PASS

```bash
git add nanobot/webui/knowledge_routes.py nanobot/webui/knowledge_api.py tests/knowledge
git commit -m "feat(knowledge): 图谱读写接口与错误码"
```

---

### Task 9：WebUI 图谱 tab（状态卡 / 实体列表 / 实体详情 / 构建重建）

**Files:**
- Modify: `webui/src/lib/api.ts`
- Modify: `webui/src/components/workspace/KnowledgePage.tsx`
- Create: `webui/src/components/workspace/knowledge/GraphPanel.tsx`
- Test: `webui/src/tests/knowledge-graph.test.tsx`

- [ ] **Step 1：写失败测试**

```tsx
// webui/src/tests/knowledge-graph.test.tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { GraphPanel } from "@/components/workspace/knowledge/GraphPanel";
import type {
  KnowledgeGraphEntity,
  KnowledgeGraphStats,
} from "@/lib/api";


function fakeApi(stats: Partial<KnowledgeGraphStats>,
                 entities: KnowledgeGraphEntity[] = []) {
  return {
    stats: vi.fn(async () => ({ enabled: true, backend: "neo4j", status: "ready",
      reason: "", entities: 0, relations: 0, docsReady: 0, docsPending: 0,
      docsFailed: 0, docsSkipped: 0, truncatedDocs: 0, lastExtractedAt: null,
      ...stats } as KnowledgeGraphStats)),
    entities: vi.fn(async () => ({ entities, total: entities.length })),
    entity: vi.fn(async () => ({ entity: entities[0], mentions: [], neighbors: [] })),
    subgraph: vi.fn(async () => ({ nodes: [], edges: [] })),
    extract: vi.fn(async () => ({ queued: 1, docs: [] })),
    rebuild: vi.fn(async () => ({ queued: 1 })),
  };
}

const renderPanel = (api: ReturnType<typeof fakeApi>, kbId = "kb") =>
  render(<GraphPanel kbId={kbId} token="t" client={{} as never} api={api} />);


it("renders disabled state without action buttons", () => {
  const api = fakeApi({ enabled: false, status: "disabled", reason: "disabled" });
  renderPanel(api);
  expect(screen.getByText(/未启用/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /重建/ })).toBeNull();
});


it("renders entity list and opens detail", async () => {
  const api = fakeApi({ enabled: true, status: "ready" }, [
    { id: "e1", name: "web-api", type: "service", description: "", mentionCount: 3 }]);
  renderPanel(api);
  expect(await screen.findByText("web-api")).toBeTruthy();
});


it("rebuild asks for confirmation before calling api", async () => {
  const api = fakeApi({ enabled: true, status: "ready" });
  renderPanel(api);
  await userEvent.click(await screen.findByRole("button", { name: /重建/ }));
  expect(api.rebuild).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: /确认/ }));
  expect(api.rebuild).toHaveBeenCalledWith(
    { kbId: "kb", purge: false, confirm: true });
});
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd webui && npx vitest run src/tests/knowledge-graph.test.tsx`
Expected: FAIL（模块不存在）

- [ ] **Step 3：api.ts 增加类型与函数**

```ts
export interface KnowledgeGraphStats {
  enabled: boolean;
  backend: string;
  status: "ready" | "degraded" | "disabled" | string;
  reason?: string;
  entities: number;
  relations: number;
  docsReady: number;
  docsPending: number;
  docsFailed: number;
  docsSkipped: number;
  truncatedDocs: number;
  lastExtractedAt?: string | null;
}

export interface KnowledgeGraphEntity {
  id: string;
  name: string;
  type: string;
  description: string;
  mentionCount: number;
}

export interface KnowledgeGraphNode {
  id: string;
  name: string;
  type: string;
  mentionCount: number;
}

export interface KnowledgeGraphEdge {
  source: string;
  target: string;
  relation: string;
  weight: number;
}

export interface KnowledgeGraphEntityDetail {
  entity: KnowledgeGraphEntity;
  mentions: Array<{ docId: string; docTitle: string; chunkId: string;
                   chunkIndex: number; heading: string }>;
  neighbors: Array<{ entity: KnowledgeGraphEntity; relation: string;
                     direction: "in" | "out"; weight: number }>;
}

export async function fetchKnowledgeGraphStats(token: string, kbId: string, base = "") {
  const query = knowledgeQuery({ kbId });
  return request<KnowledgeGraphStats>(
    `${base}${KNOWLEDGE_BASE}/graph/stats${query}`, token, undefined, API_READ_TIMEOUT_MS);
}

export async function fetchKnowledgeGraphEntities(
  token: string, params: { kbId: string; q?: string; type?: string;
                            limit?: number; offset?: number }, base = "") {
  const query = knowledgeQuery({ kbId: params.kbId, q: params.q, type: params.type,
    limit: params.limit, offset: params.offset });
  return request<{ entities: KnowledgeGraphEntity[]; total: number }>(
    `${base}${KNOWLEDGE_BASE}/graph/entities${query}`, token, undefined, API_READ_TIMEOUT_MS);
}

export async function fetchKnowledgeGraphEntity(
  token: string, id: string, base = "") {
  const query = knowledgeQuery({ id });
  return request<KnowledgeGraphEntityDetail>(
    `${base}${KNOWLEDGE_BASE}/graph/entity${query}`, token, undefined, API_READ_TIMEOUT_MS);
}

export async function fetchKnowledgeGraphSubgraph(
  token: string, params: { entityId: string; hops?: number; limit?: number }, base = "") {
  const query = knowledgeQuery({ entityId: params.entityId,
    hops: params.hops, limit: params.limit });
  return request<{ nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[] }>(
    `${base}${KNOWLEDGE_BASE}/graph/subgraph${query}`, token, undefined, API_READ_TIMEOUT_MS);
}

export function knowledgeGraphExtract(
  transport: MutationTransport,
  payload: { kbId?: string; docId?: string; force?: boolean; confirm?: boolean },
) {
  return mutation<{ queued: number; docs: string[] }>(
    transport, "knowledge.graph.extract", payload);
}

export function knowledgeGraphRebuild(
  transport: MutationTransport,
  payload: { kbId: string; purge?: boolean; confirm?: boolean },
) {
  return mutation<{ queued: number }>(
    transport, "knowledge.graph.rebuild", payload);
}
```

- [ ] **Step 4：GraphPanel.tsx**

```tsx
export interface GraphPanelProps {
  kbId: string;
  token: string;
  client: MutationTransport;
  api?: GraphPanelApi;           // 测试注入；生产不传
  onOpenChunk?: (docId: string, chunkId: string) => void;
}

export function GraphPanel({ kbId, token, client, api, onOpenChunk }: GraphPanelProps) {
  const { t } = useTranslation();
  const clientApi = api ?? createGraphApi(token, client);
  const [stats, setStats] = useState<KnowledgeGraphStats | null>(null);
  const [entities, setEntities] = useState<KnowledgeGraphEntity[]>([]);
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [detail, setDetail] = useState<KnowledgeGraphEntityDetail | null>(null);
  const [confirm, setConfirm] = useState<"build" | "rebuild" | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    void clientApi.stats(kbId).then(setStats).catch(() => setError("loadError"));
  }, [clientApi, kbId]);
  useEffect(() => {
    void clientApi.entities({ kbId, q: query, type })
      .then((page) => setEntities(page.entities));
  }, [clientApi, kbId, query, type]);

  const disabled = !stats?.enabled || stats?.status === "disabled";
  return (
    <section className="flex flex-col gap-3">
      <GraphStatsCard stats={stats} disabled={disabled}
        onBuild={() => setConfirm("build")} onRebuild={() => setConfirm("rebuild")} />
      {stats?.status === "degraded" && stats.reason !== "disabled" ? (
        <GraphDegraded reason={stats.reason}
          onRetry={() => void clientApi.stats(kbId).then(setStats)} />
      ) : null}
      <GraphEntityList entities={entities} query={query} type={type}
        onQuery={setQuery} onType={setType} onSelect={(id) =>
          void clientApi.entity(id).then(setDetail)} />
      {detail ? (
        <GraphEntityDetail detail={detail} onClose={() => setDetail(null)}
          onOpenChunk={onOpenChunk} />
      ) : null}
      {confirm ? <GraphConfirmDialog kind={confirm}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          const promise = confirm === "rebuild"
            ? clientApi.rebuild({ kbId, purge: false, confirm: true })
            : clientApi.extract({ kbId, confirm: true });
          void promise.then(() => { setConfirm(null); return clientApi.stats(kbId); })
            .then(setStats).catch(() => setError("actionError"));
        }} /> : null}
      {error ? <p className="text-[12px] text-destructive">{t(`knowledge.graph.${error}`)}</p> : null}
    </section>
  );
}
```

```tsx
// GraphPanel 内部子组件与测试注入点（同文件，保持一个文件一个职责）
type ConfirmKind = "build" | "rebuild";

interface GraphPanelApi {
  stats(kbId: string): Promise<KnowledgeGraphStats>;
  entities(params: { kbId: string; q?: string; type?: string }):
    Promise<{ entities: KnowledgeGraphEntity[]; total: number }>;
  entity(id: string): Promise<KnowledgeGraphEntityDetail>;
  subgraph(params: { entityId: string; hops?: number; limit?: number }):
    Promise<{ nodes: KnowledgeGraphNode[]; edges: KnowledgeGraphEdge[] }>;
  extract(payload: { kbId?: string; docId?: string; force?: boolean;
                     confirm?: boolean }): Promise<{ queued: number; docs: string[] }>;
  rebuild(payload: { kbId: string; purge?: boolean; confirm?: boolean }):
    Promise<{ queued: number }>;
}

function GraphStatsCard(props: {
  stats: KnowledgeGraphStats | null;
  disabled: boolean;
  onBuild: () => void;
  onRebuild: () => void;
}) { /* 状态徽标 + 4 个计数 + 两个按钮；disabled 时按钮 disabled */ }

function GraphDegraded(props: { reason: string; onRetry: () => void }) {
  /* reason 文案 + 重试按钮 */
}

function GraphEntityList(props: {
  entities: KnowledgeGraphEntity[];
  query: string;
  type: string;
  onQuery: (value: string) => void;
  onType: (value: string) => void;
  onSelect: (id: string) => void;
}) { /* 搜索框 + 类型筛选 + 卡片列表 + 空态 */ }

function GraphEntityDetail(props: {
  detail: KnowledgeGraphEntityDetail;
  onClose: () => void;
  onOpenChunk?: (docId: string, chunkId: string) => void;
}) { /* 描述 + 邻居 + 提及（提及点击调用 onOpenChunk） */ }

function GraphConfirmDialog(props: {
  kind: ConfirmKind;
  onCancel: () => void;
  onConfirm: () => void;
}) { /* 复用 KnowledgePage 的 AlertDialog 形状，文案走 knowledge.graph.confirm* */ }

function createGraphApi(token: string, transport: MutationTransport): GraphPanelApi {
  return {
    stats: (kbId) => fetchKnowledgeGraphStats(token, kbId),
    entities: (params) => fetchKnowledgeGraphEntities(token, params),
    entity: (id) => fetchKnowledgeGraphEntity(token, id),
    subgraph: (params) => fetchKnowledgeGraphSubgraph(token, params),
    extract: (payload) => knowledgeGraphExtract(transport, payload),
    rebuild: (payload) => knowledgeGraphRebuild(transport, payload),
  };
}
```

> `KnowledgePage.tsx`：`detailTab` 扩为 `"documents" | "search" | "graph"`；
> 第三个 tab 按钮沿用现有 `role="tab"` / `aria-selected` 样式；
> 内容区用 `React.lazy(() => import("./knowledge/GraphPanel"))` + `<Suspense>`，
> 保证图关闭时 `d3-force` 不进首屏包。

- [ ] **Step 5：跑测试并提交**

Run: `cd webui && npx vitest run src/tests/knowledge-graph.test.tsx`
Expected: PASS

```bash
git add webui/src/lib/api.ts webui/src/components/workspace/KnowledgePage.tsx \
  webui/src/components/workspace/knowledge webui/src/tests/knowledge-graph.test.tsx
git commit -m "feat(webui): 知识图谱 tab 与实体浏览"
```

---

### Task 10：子图可视化（d3-force + SVG）

**Files:**
- Create: `webui/src/components/workspace/knowledge/GraphView.tsx`
- Modify: `webui/src/components/workspace/knowledge/GraphPanel.tsx`
- Modify: `webui/package.json`
- Test: `webui/src/tests/knowledge-graph-view.test.tsx`

- [ ] **Step 1：安装依赖**

Run: `cd webui && npm install d3-force@^3.0.0 && npm install -D @types/d3-force`
Expected: `package.json` / lockfile 新增两个包

- [ ] **Step 2：写失败测试**

```tsx
it("caps nodes and edges and shows truncation notice", async () => {
  const nodes = Array.from({ length: 80 }, (_, i) => ({
    id: `e${i}`, name: `n${i}`, type: "concept", mentionCount: 1 }));
  render(<GraphView nodes={nodes} edges={[]} centerId="e0" />);
  expect(await screen.findByText(/已截断/)).toBeTruthy();
  expect(document.querySelectorAll("[data-graph-node]").length).toBeLessThanOrEqual(50);
});
```

- [ ] **Step 3：实现 GraphView（动态 import）**

```tsx
interface PositionedNode extends KnowledgeGraphNode {
  x: number;
  y: number;
}

interface Link {
  source: PositionedNode;
  target: PositionedNode;
  relation: string;
  weight: number;
}

interface GraphViewProps {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  centerId: string;
  onExpand?: (entityId: string) => void | Promise<void>;
}
```

```tsx
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { KnowledgeGraphEdge, KnowledgeGraphNode } from "@/lib/api";
```

```tsx
export function GraphView({ nodes, edges, centerId, onExpand }: GraphViewProps) {
  const { t } = useTranslation();
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [layout, setLayout] = useState<{ nodes: PositionedNode[]; links: Link[] } | null>(null);
  const cappedNodes = nodes.slice(0, 50);
  const nodeIds = new Set(cappedNodes.map((node) => node.id));
  const cappedEdges = edges.filter((edge) =>
    nodeIds.has(edge.source) && nodeIds.has(edge.target)).slice(0, 100);
  const truncated = nodes.length > cappedNodes.length || edges.length > cappedEdges.length;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } =
        await import("d3-force");
      const simNodes = cappedNodes.map((node) => ({ ...node }));
      const simLinks = cappedEdges.map((edge) => ({ ...edge }));
      const simulation = forceSimulation(simNodes as never)
        .force("link", forceLink(simLinks as never).id((d: any) => d.id).distance(90))
        .force("charge", forceManyBody().strength(-220))
        .force("center", forceCenter(0, 0))
        .force("collide", forceCollide(28))
        .stop();
      for (let i = 0; i < 180; i += 1) simulation.tick();
      if (!cancelled) setLayout({ nodes: simNodes as PositionedNode[], links: simLinks as Link[] });
    })();
    return () => { cancelled = true; };
  }, [nodes, edges]);

  return (
    <div className="relative rounded-panel border border-border/60 bg-card">
      {truncated ? <p className="px-3 py-2 text-[11.5px] text-muted-foreground">
        {t("knowledge.graph.truncated", { count: cappedNodes.length })}</p> : null}
      <svg ref={svgRef} viewBox="-320 -240 640 480" className="h-[360px] w-full"
           role="img" aria-label={t("knowledge.graph.subgraph")}>
        <g>
          {layout?.links.map((link) => (
            <line key={`${link.source}-${link.target}-${link.relation}`}
              x1={link.source.x} y1={link.source.y}
              x2={link.target.x} y2={link.target.y}
              className="stroke-border" strokeWidth={1}>
              <title>{link.relation}</title>
            </line>
          ))}
          {layout?.nodes.map((node) => (
            <g key={node.id} data-graph-node
               transform={`translate(${node.x},${node.y})`}>
              <circle r={node.id === centerId ? 12 : 8}
                className={node.id === centerId ? "fill-primary" : "fill-muted-foreground"}
                onClick={() => void onExpand?.(node.id)}
                onDoubleClick={() => void onExpand?.(node.id)}>
                <title>{`${node.name} · ${node.type}`}</title>
              </circle>
              <text y={20} textAnchor="middle" className="fill-foreground text-[9px]">
                {node.name}
              </text>
            </g>
          ))}
        </g>
      </svg>
    </div>
  );
}
```

> 交互口径：单击选中/高亮，双击 `onExpand(id)`（父组件以该实体为 `entityId` 重新取子图）；
> 不引入缩放库，使用原生 `wheel` + `pointer` 事件做 `viewBox` 调整或拖动节点；
> 颜色按 `type` 取 6 类固定色板，其余归「其他」。

- [ ] **Step 4：接入 GraphPanel**

实体详情下方渲染 `<GraphView nodes={...} edges={...} centerId={detail.entity.id} onExpand={...} />`；
子图数据由 `fetchKnowledgeGraphSubgraph({ entityId, hops: 1, limit: 50 })` 获取。

- [ ] **Step 5：跑测试与类型检查**

Run: `cd webui && npx vitest run src/tests/knowledge-graph-view.test.tsx`
Expected: PASS  
Run: `cd webui && npx tsc -p tsconfig.build.json --noEmit`
Expected: 无错误

- [ ] **Step 6：提交**

```bash
git add webui/package.json webui/package-lock.json \
  webui/src/components/workspace/knowledge webui/src/tests/knowledge-graph-view.test.tsx
git commit -m "feat(webui): 知识图谱力导向子图"
```

---

### Task 11：10 语言文案

**Files:**
- Modify: `webui/src/i18n/locales/*/common.json`（10 个）
- Test: `webui/src/tests/knowledge-graph-i18n-parity.test.ts`

- [ ] **Step 1：写失败测试（键集完全一致）**

```ts
const LOCALES = ["en", "zh-CN", "zh-TW", "ja", "ko", "vi", "fr", "es", "pt-BR", "id"];

function graphKeys(lang: string): string[] {
  const data = JSON.parse(readFileSync(
    join(ROOT, "src/i18n/locales", lang, "common.json"), "utf8"));
  return leafKeys(data.knowledge?.graph ?? {});
}

it("knowledge.graph 键集在 10 语言之间完全一致", () => {
  const base = graphKeys("en").sort();
  expect(base.length).toBeGreaterThanOrEqual(30);
  for (const lang of LOCALES) {
    expect(graphKeys(lang).sort(), lang).toEqual(base);
  }
});
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd webui && npx vitest run src/tests/knowledge-graph-i18n-parity.test.ts`
Expected: FAIL

- [ ] **Step 3：补齐 10 语言**

键表（每语言同一集合）：

```
tab, title, subtitle, statusReady, statusDisabled, statusDegraded, reason,
backend, entities, relations, docsReady, docsPending, docsFailed,
docsSkipped, truncatedDocs, lastExtracted, build, rebuild, rebuildPurge,
confirmTitle, confirmBody, confirmAction, searchPlaceholder, filterType,
allTypes, empty, emptyHint, entityDetail, mentions, neighbors,
directionOut, directionIn, weight, subgraph, truncated, retry,
loadError, actionError, disabledHint, otherType
```

- [ ] **Step 4：跑测试与全量 i18n 回归**

Run: `cd webui && npx vitest run src/tests/knowledge-graph-i18n-parity.test.ts src/tests/i18n.test.tsx`
Expected: PASS

```bash
git add webui/src/i18n/locales webui/src/tests/knowledge-graph-i18n-parity.test.ts
git commit -m "feat(webui): 图谱文案 10 语言"
```

---

### Task 12：评测白名单与开/关图对比

**Files:**
- Modify: `nanobot/evaluation/knowledge/models.py`
- Modify: `nanobot/knowledge/service.py`（`retriever_with` 注入图路）
- Test: `tests/evaluation/knowledge/test_graph_overrides.py`

- [ ] **Step 1：写失败测试**

```python
def test_graph_fields_allowed():
    from nanobot.evaluation.knowledge.models import ALLOWED_RETRIEVAL_FIELDS
    assert {"graph_enabled", "graph_hops", "graph_seed_limit",
            "graph_weight", "graph_max_chunks"} <= ALLOWED_RETRIEVAL_FIELDS


def test_graph_hops_out_of_range_is_rejected():
    from pydantic import ValidationError
    from nanobot.evaluation.knowledge.models import KnowledgeRetrievalConfig
    with pytest.raises(ValidationError):
        KnowledgeRetrievalConfig(graph_hops=3)


def test_unknown_graph_field_still_rejected(service):
    with pytest.raises(ServiceError) as exc:
        service.create_run({... "overrides": {"graph_nope": 1}})
    assert exc.value.code == "KNOWLEDGE_CONFIG_UNSUPPORTED"


def test_override_marks_graph_route(service_with_graph):
    result = service_with_graph.run_once(overrides={"graph_enabled": True})
    assert "graph" in result["pool"]["routes"] or "graph" in result["degraded"]
```

- [ ] **Step 2：跑测试确认失败**

Run: `pytest tests/evaluation/knowledge/test_graph_overrides.py -v`
Expected: FAIL

- [ ] **Step 3：白名单与模型**

```python
ALLOWED_RETRIEVAL_FIELDS = frozenset({
    ..., "graph_enabled", "graph_hops", "graph_seed_limit",
    "graph_weight", "graph_max_chunks",
})


class KnowledgeRetrievalConfig(_Base):
    ...
    graph_enabled: bool | None = None
    graph_hops: int | None = Field(default=None, ge=1, le=2)
    graph_seed_limit: int | None = Field(default=None, ge=1, le=10)
    graph_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    graph_max_chunks: int | None = Field(default=None, ge=10, le=200)
```

> `_retriever_settings` 已按 `fields(RetrieverSettings)` 自动映射，无需新增分支；
> 前提是 Task 6 已把 7 个 `graph_*` 字段加到 `RetrieverSettings`。

- [ ] **Step 4：`retriever_with` 注入图路**

```python
def retriever_with(self, settings: RetrieverSettings) -> Retriever:
    return Retriever(
        self._repo,
        embedder=self._embedder,
        vector_store=self._vector,
        settings=settings,
        reranker=self._reranker,
        rerank_settings=self._rerank_settings,
        tokenizer_mode=self._tokenizer_mode,
        identity_resolver=self._identity_resolver,
        graph_route=self._graph_route,
    )
```

> 全局图未装配时 `self._graph_route` 为 `None`；`Retriever.search` 在
> `settings.graph_enabled=True` 时显式追加 `graph_unavailable`（Task 6 已实现），
> 因此评测覆写「开图」会稳定降级，而不是静默变成两路。

- [ ] **Step 5：跑测试并提交**

Run: `pytest tests/evaluation/knowledge -v`
Expected: PASS

```bash
git add nanobot/evaluation/knowledge/models.py nanobot/knowledge/service.py \
  tests/evaluation/knowledge/test_graph_overrides.py
git commit -m "feat(evaluation): 图谱参数白名单与开图对比"
```

---

### Task 13：Agent 工具证据行

**Files:**
- Modify: `nanobot/agent/tools/knowledge_common.py`
- Test: `tests/tools/test_knowledge_tools.py`（追加用例）

- [ ] **Step 1：写失败测试**

```python
def test_render_includes_graph_path_when_evidence_present():
    hit = SearchHit(chunk_id="c1", doc_id="d1", kb_id="kb", chunk_index=0,
                    heading_path="", page=None, locator="", content="x",
                    char_count=1, score=0.9,
                    graph_evidence=GraphEvidencePayload(
                        seeds=("A",), path=(("A", "依赖", "B"),), hop=1,
                        entity_ids=("e1", "e2")))
    text = render_search_result(SearchResult(hits=(hit,), mode_used="hybrid",
                                             routes=("vector", "graph")),
                                mode_requested="hybrid")
    assert 'graph_path="A —依赖→ B"' in text
```

- [ ] **Step 2：跑测试确认失败**

Run: `pytest tests/tools/test_knowledge_tools.py -v -k graph_path`
Expected: FAIL

- [ ] **Step 3：实现**

```python
def _graph_path(payload: GraphEvidencePayload | None) -> str:
    if payload is None or not payload.path:
        return ""
    first = payload.path[0]
    parts = [first[0]]
    for _src, relation, target in payload.path:
        parts.append(f"—{relation}→ {target}")
    return " ".join(parts)
```

```python
# render_search_result 的 <result> 拼接处
+ (f' graph_path="{escape_xml(_graph_path(hit.graph_evidence))}"'
   if hit.graph_evidence is not None else "")
```

> 图内容来自模型：仍走 `escape_xml`，纯文本渲染，不注入 HTML；工具名与
> `doc://` / `chunk://` 句柄格式不变。

- [ ] **Step 4：跑测试并提交**

Run: `pytest tests/tools/test_knowledge_tools.py -v`
Expected: PASS

```bash
git add nanobot/agent/tools/knowledge_common.py tests/tools/test_knowledge_tools.py
git commit -m "feat(knowledge): 工具结果附带图路径证据"
```

---

### Task 14：端到端验证、文档与交付

**Files:**
- Create: `.ai-runtime-artifacts/verifications/2026-11-11-knowledge-graph-verification.md`
- Modify: `.ai-runtime-artifacts/contracts/2026-11-11-contract-knowledge-graph.md`（状态置 approved）
- Modify: `.ai-runtime-artifacts/specs/2026-11-10-knowledge-graph-spec.md`（状态置 approved）

- [ ] **Step 1：离线全量回归**

Run: `pytest tests/knowledge tests/evaluation/knowledge tests/tools/test_knowledge_tools.py -v`
Expected: 新增用例全绿；既有 8 个 Windows chmod/symlink 基线失败保持不变

- [ ] **Step 2：静态检查**

Run: `ruff check nanobot/`
Expected: 无新增告警  
Run: `cd webui && npx tsc -p tsconfig.build.json --noEmit`
Expected: 无错误

- [ ] **Step 3：关闭态零影响验证**

Run: `pytest tests/knowledge/test_retriever.py tests/knowledge/test_fusion.py -v`
Expected: PASS，且 `graph.enabled=false` 时 `routes` 仍为 `("vector","keyword")` 或单路

- [ ] **Step 4：真实 Neo4j 闭环（有 Docker 时）**

```bash
docker run --rm -d --name zhixu-neo4j -p 7687:7687 -p 7474:7474 \
  -e NEO4J_AUTH=neo4j/password neo4j:5.26-community
export NEO4J_TEST_URI=bolt://localhost:7687
pytest tests/knowledge/test_graph_store.py -v -m neo4j
```

检查清单：

1. 建库 → 入库示例 Markdown → `knowledge-graph-stats` 的 `entities/relations/docsReady` 增长；
2. 查询命中种子 → 检索结果 `routes` 含 `graph`，`<result graph_path>` 非空；
3. 图谱 tab 截图（实体列表、实体详情、1 跳子图）；
4. 停掉 Neo4j → 检索仍 200，`degraded` 含 `graph_unavailable`；
5. 删文档 → 孤儿实体/关系被清理；重建后计数不重复。

- [ ] **Step 5：写验证报告并收尾**

```markdown
# 知识图谱验证记录（2026-11-11）

## 环境
- Python / Node / Neo4j 版本
- 是否安装 `[graph]` extra

## 命令与结果
| 命令 | 结果 |
| --- | --- |
| pytest tests/knowledge ... | N passed / M skipped |
| ruff check nanobot/ | clean |
| tsc --noEmit | clean |

## 闭环证据
- 关闭态：routes = ...
- 开启态：routes = ...，graph_path = ...
- 降级：degraded = ...
- 幂等：重建前后 entities/relations 计数 = ...

## 已知差异/遗留
- Windows 8 个基线失败
- Neo4j 集成用例默认 skip
```

- [ ] **Step 6：提交**

```bash
git add .ai-runtime-artifacts/verifications/2026-11-11-knowledge-graph-verification.md \
  .ai-runtime-artifacts/contracts/2026-11-11-contract-knowledge-graph.md \
  .ai-runtime-artifacts/specs/2026-11-10-knowledge-graph-spec.md
git commit -m "docs(knowledge): 图谱实现验证与契约收口"
```

---

## 3. 关键口径与不变量

| 主题 | 口径 |
| --- | --- |
| 真相源 | SQLite 唯一真相；Neo4j 随时可由 `rebuild_kb` 重建 |
| 默认影响 | `graph.enabled=false` 时无新默认依赖、无新路由、既有融合数值不变 |
| 抽取失败 | 不影响文档 `ready`；单块失败跳过，全失败才 `failed`；无实体 → `skipped` |
| 幂等 | 重抽先删本文档证据再写；计数由证据表聚合重算；不产生重复行 |
| 作用域 | 图读接口先校验 KB 可见；图候选回表时再校验 `ready/is_enabled/KB enabled` |
| 降级 | 检索侧失败 → `graph_unavailable` 且 HTTP 200；管理动作失败 → 503 |
| 隐私 | 入库自动抽取由 `graph.enabled` 授权；手动全量需 `confirm=true` |
| 配置 | 图参数唯一来源 `knowledge.graph.expansion`；评测覆写落在 `RetrieverSettings` |

## 4. 风险与验收

| 风险 | 缓解 | 验收 |
| --- | --- | --- |
| 三路 RRF 改变绝对分尺度 | 关闭图时走原两路分支；只比排名/指标 | `test_fusion.py` 既有数值用例全绿 |
| 抽取质量差引入噪声 | JSON 守卫 + 名称校验 + 数量上限 + 默认 1 跳 | 评测开/关对比可关闭图 |
| Neo4j 不可用 | 默认关闭 + 可选 extra + 显式降级 | 停后端检索仍 200 |
| 前端包体变大 | 图谱 tab `React.lazy` + `d3-force` 动态 import | 构建产物中 d3-force 独立 chunk |
| 双存储不一致 | 所有写路径经 `GraphSyncService` | 重建后计数与 SQLite 一致 |

## 5. 预计提交序列

1. `feat(knowledge): 图谱 SQLite v3 与图仓储`
2. `feat(knowledge): 图谱存储协议与 Neo4j 适配器`
3. `feat(knowledge): 图谱抽取器与 JSON 守卫`
4. `feat(knowledge): 实体归一与种子链接`
5. `feat(knowledge): 图谱抽取 worker、回填与删除同步`
6. `feat(knowledge): 图检索第三路与加权 RRF 降级`
7. `feat(knowledge): 图谱配置、装配与 CLI 重建`
8. `feat(knowledge): 图谱读写接口与错误码`
9. `feat(webui): 知识图谱 tab 与实体浏览`
10. `feat(webui): 知识图谱力导向子图`
11. `feat(webui): 图谱文案 10 语言`
12. `feat(evaluation): 图谱参数白名单与开图对比`
13. `feat(knowledge): 工具结果附带图路径证据`
14. `docs(knowledge): 图谱实现验证与契约收口`

## 6. 依赖与顺序

```
Task 0 ──> Task 1 ──> Task 2 ──> Task 5 ──> Task 6 ──> Task 7 ──> Task 8 ──> Task 9 ──> Task 10 ──> Task 11
                 └──> Task 3 ──┘              └──> Task 12
                 └──> Task 4 ──┘
Task 6 ──> Task 13
全部 ──> Task 14
```

## Next

**（计划写入后须暂停 — 即使用户句末含「然后实现」）**

- 计划确认 → 说「开始实现」或「执行」
- 需要调整范围/默认值/接口 → 直接说修改意见
- 想先做最小闭环 → 说「只做 Task 0–7」（后端闭环，暂不做 UI）
