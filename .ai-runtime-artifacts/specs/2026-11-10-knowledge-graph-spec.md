---
artifact: spec
route: superpowers:brainstorming
skills:
  - brainstorming
  - source-driven-development
skills_evidence:
  - harness-kit/.agents/skills/brainstorming/SKILL.md
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/stack/2026-11-10-knowledge-graph-stack.md
  - .ai-runtime-artifacts/specs/2026-11-09-knowledge-center-spec.md
  - nanobot/knowledge/database.py
  - nanobot/knowledge/ingestor.py
  - nanobot/knowledge/retriever.py
  - nanobot/knowledge/service.py
  - nanobot/config/schema.py
  - nanobot/webui/knowledge_routes.py
  - WeKnora/internal/types/graph.go
  - WeKnora/internal/application/service/extract.go
  - WeKnora/internal/application/repository/retriever/neo4j/repository.go
  - WeKnora/internal/application/repository/retriever/neo4j/graph_search.go
  - WeKnora/docs/LITE.md
created_at: 2026-11-10
status: draft
approved: false
---

# 知识图谱（可选 Neo4j 后端）方案 v1

## 1. 背景与目标

知识中心 v1 已交付「存储 + 混合检索 + RAG 工具 + 评测台」，并明确排除了知识图谱。
本轮要把图补上，且用户已确认四个产品决策：

| 决策点 | 结论 |
| --- | --- |
| 首期目标 | **检索增强 + 可视化并重**（一次交付闭环） |
| 默认后端 | **默认关闭 + 可选 Neo4j**（与 WeKnora 同款；桌面/Lite 无外部依赖） |
| 抽取时机 | **入库异步抽取**（复用队列/租约；历史文档可批量回填） |
| 评测联动 | **纳入**评测配置白名单，可对比「开图/不开图」指标 |

v1 要交付三件事：

1. **图谱构建**：文档入库后异步抽取实体/关系 → SQLite 真相源 + Neo4j 派生索引。
2. **图谱检索**：查询实体链接 → 1~2 跳扩展 → 作为第三路候选进入加权 RRF，失败显式降级。
3. **图谱可视化**：知识库详情新增「图谱」标签页：状态、实体列表/详情、力导向子图。

**不做（v1 排除）**：实体人工合并/别名管理、社区发现与 GraphRAG 全局摘要、图谱编辑、
跨库联合图、Neo4j 打包分发、嵌入式图库后端（保留 `backend` 枚举位）。

## 2. 调研结论

### 2.1 本项目现状（可复用资产）

| 现状 | 位置 | 对本方案的意义 |
| --- | --- | --- |
| SQLite 迁移链 `_MIGRATIONS` + `_schema_meta`，当前 `SCHEMA_VERSION=2` | `nanobot/knowledge/database.py:15,231-302` | 图相关表以 **v3 迁移**加入；不触碰 chunk 不可变触发器（`:135-153`） |
| 入库管线：抽取/分块 → 事务提交 → 事务外向量 → 问题生成（best-effort） | `nanobot/knowledge/ingestor.py:123-185` | 图谱抽取挂在**问题生成之后**的同类后处理位置（`:176-177`） |
| 队列 + 租约 + attempt 守卫 | `repository.py:660-716`、`ingestor.py` | 图谱抽取复用同一套「租约/重试/幂等」纪律，另建状态表 |
| 双通道召回 + 加权 RRF + 阈值/扩展 + 降级标签（关键词含块/问题两个子列表） | `retriever.py:782,695,173-378`、`fusion.py:96-170`、`rerank.py:34-43` | 图作为**第三路** `RouteSpec` 参与 `weighted_rrf_multi`，沿用 `degraded` 语义 |
| LLM 后处理范式（每块一次 OpenAI 兼容调用、失败只跳块） | `nanobot/knowledge/questions.py:109-180,263-265` | 抽取器直接复用其客户端/配置/失败策略 |
| 读接口 + WS mutation 动作名 | `nanobot/webui/knowledge_routes.py:21-36,72-82` | 图读接口命名 `knowledge-graph-*`，写动作 `knowledge.graph.*`；须同步更新 `READ_ACTIONS/WRITE_ACTIONS` 与 `READ_PATHS/WRITE_PATHS` |
| 评测参数白名单与预算 | `nanobot/evaluation/knowledge/models.py:36-55`、`services.py:288-301`、`runner.py:348-374` | 图参数加入白名单与同处校验；沿用并发/预算机制 |
| 中文分词：索引/查询都用 `tokenize_index/tokenize_query` 生成 `search_text` | `nanobot/knowledge/tokenizer.py:118,159`、`database.py:126,156-177` | 实体链接**不直接对中文名建 unicode61 FTS**（会让整段中文成为单 token）；v1 用归一化子串 + 查询分词匹配 |
| 前端页面三段式（文档 / 检索预览） | `webui/src/components/workspace/KnowledgePage.tsx` | 新增第三个 tab「图谱」，沿用卡片区 + 详情两级 IA |

### 2.2 从 WeKnora 取什么、不取什么

| WeKnora 做法 | 证据 | v1 采用 | 理由 / 裁剪 |
| --- | --- | --- | --- |
| Entity(title/type/description/chunkIds/度) + Relationship(strength 1-10) | `internal/types/graph.go:8-29` | 采用字段语义 | 概念简单、与评测/可视化兼容 |
| 按 chunk 异步抽取，`NEO4J_ENABLE≠true` 不入队 | `service/extract.go:102-127`、`knowledge_post_process.go:420-435` | 采用 | 与现有入库后处理同构 |
| declined：无 JSON 视为拒绝、stop 不重试、length 重试 | `chat_pipeline/extract_entity.go:151-210`、`extract_graph_declined_test.go:47-66` | 采用 | 避免把「模型说无实体」当失败 |
| Neo4j APOC `merge.node / merge.relationship`，精确 name 合并 | `neo4j/repository.go:76-103` | **不引入 APOC**，用原生 `MERGE` | 少一个插件依赖；v1 精确归一即可 |
| 无约束/索引；删除按命名空间整删 | `neo4j/repository.go` | 加唯一约束 + 索引；删除按 KB/文档同步 | 可维护性与性能 |
| 图检索只有子串 CONTAINS + 一跳，结果 append 去重、score=0 | `neo4j/graph_search.go:64-109`、`search_entity.go:237-256` | **改为**实体链接 + 1~2 跳 + **加权 RRF** | 否则评测不可比、也无法与向量/关键词公平融合 |
| 图 API 独立 `graph-search` | `handler/graph_search.go`、`router/routes_graph.go:12-18` | 改为**实体/子图读接口 + WS 写动作** | 前端可视化与重建需要管理面 |
| 唯一开关 `NEO4J_ENABLE`，Lite 默认 false | `docker-compose.yml:305-310`、`docs/LITE.md:12`、`.env.lite.example:46-48` | 采用 | 保证桌面/Lite 零外部依赖 |
| 可视化是 Wiki 链接图（SVG + Louvain） | `webui/.../wikiGraphLayout.ts` | 采用 SVG 自绘；v1 用 d3-force，不做社区检测 | 少依赖、够用 |

## 3. 方案对比与选择

| 方案 | 内容 | 优点 | 代价 | 估算 |
| --- | --- | --- | --- | --- |
| A 纯 SQLite 图 | 邻接表 + 递归 CTE，无外部服务 | 零运维、桌面友好 | 多跳/可视化查询要手写；生态弱 | 10–13 WU |
| **B 可选 Neo4j 派生索引** | SQLite 真相源；Neo4j 镜像图谱；关闭时零依赖 | Cypher/可视化生态；与 WeKnora 对齐；可降级 | 可选运维成本；双写同步 | **14–18 WU** |
| C 嵌入式 Kuzu | 嵌入式图库，无服务 | 桌面友好、Cypher 风格 | 生态/可视化弱；新依赖风险 | 12–15 WU |

**选择 B**：用户已明确接受引入 Neo4j，且目标包含可视化；Neo4j 的 Bloom/浏览器与成熟 Cypher
能省掉大量图查询与渲染工作。通过「默认关闭 + 派生索引 + 显式降级」把运维风险关进笼子。

## 4. 总体架构

```
入库：source → extract/chunk → commit_chunks → vector → questions
                                                └→ graph_extract (async worker, 新)
                                                      ├→ SQLite: entities/relations/mentions/state
                                                      └→ Neo4j: Entity/RELATED（可重建镜像）

检索：query ─┬→ vector route ─┐
             ├→ keyword route ─┼→ weighted RRF(k=60) → rerank → MMR → assemble
             └→ graph route ───┘        ↑
                 ├ 实体链接（归一化子串 + 查询分词）
                 └ Neo4j 1~2 跳 → chunk_ids → 候选

读端：WebUI 图谱 tab → HTTP 读接口（stats/entities/entity/subgraph）
写端：WebUI 重建/抽取 → WS mutation（knowledge.graph.*）
```

**不变量：**

1. SQLite 是唯一真相源；Neo4j 永远可由 SQLite 重建，删除/重建不丢数据。
2. 图能力关闭或后端不可用时，检索/入库行为与今天完全一致（只多一个显式降级标签）。
3. 图谱抽取失败不影响文档 `ready`，与问题生成同级 best-effort。
4. 所有图查询先做 KB/workspace 作用域校验，再访问后端。

## 5. 数据模型

### 5.1 SQLite schema v3（迁移 `("3", _V3_STATEMENTS)`）

```sql
CREATE TABLE graph_entities (
  id            TEXT PRIMARY KEY,              -- sha256(kb_id \0 name_norm)[:32]，重建稳定
  kb_id         TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  name_norm     TEXT NOT NULL,
  type          TEXT NOT NULL DEFAULT 'concept',
  description   TEXT NOT NULL DEFAULT '',
  mention_count INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  UNIQUE (kb_id, name_norm)
);
CREATE INDEX idx_graph_entities_kb ON graph_entities(kb_id);

CREATE TABLE graph_relations (
  id               TEXT PRIMARY KEY,           -- sha256(kb_id \0 src_norm \0 rel \0 tgt_norm)[:32]
  kb_id            TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
  source_entity_id TEXT NOT NULL REFERENCES graph_entities(id) ON DELETE CASCADE,
  target_entity_id TEXT NOT NULL REFERENCES graph_entities(id) ON DELETE CASCADE,
  relation         TEXT NOT NULL,
  description      TEXT NOT NULL DEFAULT '',
  weight           REAL NOT NULL DEFAULT 5.0,  -- 1..10，来自抽取 strength（缺省取中值 5）
  mention_count    INTEGER NOT NULL DEFAULT 0, -- 由 graph_relation_evidence 聚合重算
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  UNIQUE (kb_id, source_entity_id, target_entity_id, relation)
);
CREATE INDEX idx_graph_relations_source ON graph_relations(source_entity_id);
CREATE INDEX idx_graph_relations_target ON graph_relations(target_entity_id);

CREATE TABLE graph_mentions (
  entity_id  TEXT NOT NULL REFERENCES graph_entities(id) ON DELETE CASCADE,
  doc_id     TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_id   TEXT NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  PRIMARY KEY (entity_id, chunk_id)
);
CREATE INDEX idx_graph_mentions_doc ON graph_mentions(doc_id);

CREATE TABLE graph_relation_evidence (
  relation_id TEXT NOT NULL REFERENCES graph_relations(id) ON DELETE CASCADE,
  doc_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_id    TEXT NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (relation_id, chunk_id)
);
CREATE INDEX idx_graph_relation_evidence_doc ON graph_relation_evidence(doc_id);

CREATE TABLE graph_extraction_state (
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
);
CREATE INDEX idx_graph_state_status ON graph_extraction_state(status);
```

> **设计说明：v1 不建实体 FTS。** 现有知识 FTS 的中文可用性来自先算
> `search_text`（`tokenize_index`）再用 `unicode61`；直接对中文实体名建 FTS 会把整段中文当成
> 一个 token，`灰度发布` 无法命中 `灰度发布流程`。实体名很短、单库规模 ≤2k，
> v1 改为对 `graph_entities.name_norm` 做 `LIKE` 子串扫描 + 查询分词匹配；规模上来后
> 再按 `document_chunks_fts` 的 `search_text` 模式补实体 FTS（v2）。

迁移注意事项：

- `SCHEMA_VERSION` 升到 `"3"`；只新增表/索引，不改既有表结构，chunk 不可变触发器不动。
- 文档删除级联 `graph_mentions` / `graph_relation_evidence` / `graph_extraction_state`；**实体/关系不级联删**——
  由同步服务按剩余证据重算 `mention_count`，归零的实体与孤立关系再删。
- 迁移测试覆盖 v2 库升级、级联删除；不改动既有 `document_chunks_fts` 触发器。

### 5.2 Neo4j 派生 schema

```cypher
CREATE CONSTRAINT graph_entity_uid IF NOT EXISTS
FOR (e:Entity) REQUIRE e.uid IS UNIQUE;

CREATE INDEX graph_entity_kb IF NOT EXISTS
FOR (e:Entity) ON (e.kb_id);

MERGE (:GraphMeta {id:'schema'}) SET version = 1;

// 节点
(:Entity {
  uid: "<kb_id>:<entity_id>", kb_id, entity_id, name, name_norm,
  type, description, mention_count,
  chunk_ids: [<chunk_id>...]          // 证据块，<=200
})
// 关系
(:Entity)-[:RELATED {
  uid: "<kb_id>:<relation_id>", relation, weight, description, mention_count
}]->(:Entity)
```

- 只存检索与可视化所需字段；正文永远回 SQLite 读。
- 写入使用参数化 `MERGE`（无 APOC）；`chunk_ids` 由 SQLite `graph_mentions` 计算后整体替换，
  单个实体最多保留 200 个证据块（跨文档按最后提及时间截断，并在 `graph_extraction_state.truncated` 置位）。
- `GraphStore` 协议（WU-02 边界）：`health()->{status,reason}`、`ensure_schema()`、
  `upsert_subgraph(kb_id, entities, relations)`、`delete_kb(kb_id)`、`delete_entities(kb_id, entity_ids)`、
  `expand(kb_id, entity_ids, hops, limits)->{entity_ids, edges, chunk_ids}`。
  SQLite 侧仓储提供实体/关系/证据的读写与聚合，二者都只由 `GraphSyncService` 调用。
- `GraphSyncService.sync_entities(kb_id, entity_ids)` 负责镜像；`rebuild_kb(kb_id)` 先按 `kb_id` 删除再全量写。
- 后端不可用：连接失败/约束创建失败 → `status=degraded(reason=neo4j_unavailable|schema_failed)`，不影响入库与检索。

## 6. 图谱抽取（入库异步）

### 6.1 触发与状态机

- 入口：`Ingestor.ingest` 在问题生成之后调用 `GraphExtractor.enqueue(doc)`（`graph.enabled && extraction.enabled`）。
  配置开关本身就是「用户已知晓 chunk 会发给模型」的授权；手动全量抽取/重建另需显式 `confirm`（见 §9.2）。
- 独立 worker（`GraphService._graph_worker`），不阻塞文档队列；复用「租约 + attempt」模式：
  `pending → processing → ready | failed | skipped`，`processing` 超时 10 分钟回收。
- 逐块调用模型（默认并发 2）；单文档最多 200 块，超出按 `chunk_index` 顺序截断并记 `truncated=true`。
- 重抽幂等：按 `doc_id` 先删 `graph_mentions` 与 `graph_relation_evidence`，再写入本轮结果；
  `mention_count` 一律由证据表聚合重算（不累加），证据归零的实体/关系删除；旧 `attempt` 结果不得覆盖新状态。
  幂等指**不产生重复行/重复计数**；模型重跑输出本身允许有差异。
- 删除文档：级联删 `graph_mentions` / `graph_relation_evidence` / `graph_extraction_state` → 同步服务清理 Neo4j 与孤立实体/关系。
- 历史回填：开启图后由 `knowledge.graph.rebuild`（全库）或 CLI `nanobot knowledge graph rebuild --kb <id>` 触发；
  返回排队文档数与进度。

### 6.2 抽取协议（`prompt_version = "graph1"`）

输入：`heading_path + chunk_text`（截断 `max_context_chars=1200`）。
输出严格 JSON：

```json
{
  "entities": [{"name": "web-api", "type": "service", "description": "…"}],
  "relations": [{"source": "web-api", "target": "值班二号", "relation": "升级给", "description": "…", "strength": 8}]
}
```

守卫（对齐 WeKnora `extract_entity.go:151-210` 但更严格）：

| 情况 | 处理 |
| --- | --- |
| 无 `entities` 键 / 明确表示无实体 | `skipped`，不重试 |
| JSON 解析失败 | 用 `json_repair` 修复一次；仍失败记 `failed(parse)` |
| `finish_reason=length` / 输出截断 | 上下文减半重试一次 |
| HTTP 4xx（除 429） | `failed(http_4xx)`，不重试 |
| 429 / 5xx / 超时 | 指数退避重试 1 次，仍失败 `failed(...)` |
| 实体名超过 40 字 / 含代词 / 空白 | 丢弃该实体及其关系 |
| 关系端点不在实体表 | 丢弃该关系 |
| 数量超限 | 按出现顺序截断（实体 20 / 关系 30） |

抽取器与模型客户端沿用 `questions.py` 的 OpenAI 兼容配置形状，新增独立配置块，**不复用问题生成配置**。

### 6.3 归一与链接

- 归一函数 `normalize_entity_name()`：Unicode NFKC → 去首尾空白 → 连续空白折叠 → casefold；
  `name` 展示保留该实体**最早入库**的原文（按 `created_at`），归一值只用于合并/匹配。
- 同一 KB 内按 `name_norm` 精确合并（v1 不做别名/模糊合并，与 WeKnora 一致）；
  `type`/`description` 以最新一次非空抽取为准，`mention_count` = `graph_mentions` 计数；
  关系同理：`description`/`weight` 取最新非空，`mention_count` = `graph_relation_evidence` 计数。
- 查询链接（不依赖 FTS，见 §5.1 设计说明）：
  1. 整串精确：`normalize_entity_name(query)` 与 `name_norm` 相等；
  2. 子串：用 `tokenize_query(query)` 拆出的 token 做 `name_norm LIKE '%<token>%'`（中文 bigram / 英文词）；
  3. 排序：整串精确 > 命中 token 数 > `mention_count`，取前 `seed_limit`（5）；同值按 `id` 升序保证可复现。
- 单库实体 ≤2k 时该扫描是毫秒级；不引入实体 embedding（v1 省一次模型调用，后续可扩展）。

## 7. 检索融合

- 开关：`graph.enabled && expansion.enabled` 且后端 `ready` 才启用图路；否则不产生 `graph` 路由。
- 流程：`query → seeds(≤5) → Neo4j 1~2 跳(≤20 实体) → chunk_ids(≤50) → SQLite 取块 → 候选`。
- 候选块必须回 SQLite 复核「文档 ready + is_enabled + KB enabled + 作用域命中」后才进入融合，
  与现有读路径不变量一致（不允许图返回已删除/停用内容）。
- 融合：`weighted_rrf_multi` 已支持 N 路（`fusion.py:147-170`），图开启时传三个 `RouteSpec`：
  vector `0.7` / keyword `0.3` / graph `expansion.weight`（默认 `0.25`），`rrf_k=60` 不变。
- **归一化变化（重要）**：`maxRRF = Σw/(k+1)`，开启图后分母从 `1.0` 变为 `1.25`，
  同一命中的绝对融合分会按比例变小；**图关闭时仍走现有两路实现，数值完全不变**。
  因此验收与评测只比较排名/指标，不比较绝对融合分；UI 如需展示分数需标注「三路口径」。
- 图路分数只用于融合，不冒充相关性分；精排（若开启）照常对合并后的候选重排，
  但需在验收中确认启用图后精排阈值不会因融合分尺度变化而误判（见 §17 风险）。
- 证据：命中结构新增可选字段（每命中只保留**最短一条**路径，避免响应膨胀）

```ts
graph?: {
  seeds: string[];                       // 命中的种子实体名
  path: Array<{ from: string; relation: string; to: string }>;
  hop: 1 | 2;
  entityIds: string[];
}
```

- 降级/状态（沿用 `KnowledgeSearchResult.degraded`）：
  - `graph_unavailable`：Neo4j 连接/查询失败 → 返回其余两路结果，HTTP 仍 200；
  - `graph_truncated`：节点/块数触顶被截断；
  - 无种子不降级（正常空）。
- 工具输出（`knowledge_search`）：复用 `render_search_result`（`knowledge_common.py:215-275`），
  在 `<hit>` 上追加可选属性 `graph_path="A —关系→ B"`；**不改** `doc://` / `chunk://` 句柄格式与工具名。

## 8. 配置

`nanobot/config/schema.py` 新增（挂在 `KnowledgeConfig` 下）：

```python
class KnowledgeGraphExtractionConfig(Base):
    enabled: bool = True
    model: str = ""
    base_url: str = ""                       # OpenAI 兼容，含 /v1
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
    require_confirmation: bool = True        # 手动全量抽取/重建需显式 confirm（入库自动抽取由配置开关授权）

class KnowledgeGraphExpansionConfig(Base):
    enabled: bool = True
    hops: int = 1                            # 1..2
    seed_limit: int = 5
    max_entities: int = 20
    max_chunks: int = 50
    weight: float = 0.25                     # 三路 RRF 中 graph 的权重
    timeout_seconds: float = 0.8             # 单次检索图路硬超时

class KnowledgeGraphConfig(Base):
    enabled: bool = False                    # 默认关闭（与 WeKnora 同款）
    backend: Literal["neo4j"] = "neo4j"      # 预留 sqlite/kuzu
    uri: str = ""                            # bolt://localhost:7687
    username: str = "neo4j"
    password: str = Field(default="", repr=False)
    password_env: str = ""
    database: str = "neo4j"
    connect_timeout_seconds: float = 5.0
    extraction: KnowledgeGraphExtractionConfig = Field(default_factory=...)
    expansion: KnowledgeGraphExpansionConfig = Field(default_factory=...)
```

- `KnowledgeConfig.graph: KnowledgeGraphConfig`；camelCase 由现有 `Base` 统一处理。
- 装配：`KnowledgeService.local/build` 注入 `GraphStore`（None = 关闭）；Neo4j 驱动延迟导入（未装 extra 时报出可读错误）。
- 状态三态：`disabled`（未启用）/ `ready` / `degraded(reason)`；用于能力接口与 UI 徽标。

## 9. API 契约

### 9.1 读（HTTP GET，认证与作用域沿用 `knowledge-*`）

> 新动作须同时登记到 `knowledge_routes.py` 的 `READ_ACTIONS` / `READ_PATHS`（写动作同理），
> 否则 `dispatch` 判定为未知动作（`:21-36,72-82`）。

| 动作名 | 路径 | 返回 |
| --- | --- | --- |
| `knowledge-graph-stats` | `/api/settings/knowledge/graph/stats?kbId=` | `{enabled, backend, status, reason, entities, relations, docsReady, docsPending, docsFailed, docsSkipped, truncatedDocs, lastExtractedAt}` |
| `knowledge-graph-entities` | `…/graph/entities?kbId=&q=&type=&limit=50&offset=0` | `{entities:[{id,name,type,description,mentionCount}], total}`（`limit` 上限 100） |
| `knowledge-graph-entity` | `…/graph/entity?id=` | `{entity, mentions:[{docId,docTitle,chunkId,chunkIndex,heading}], neighbors:[{entity,relation,direction,weight}]}` |
| `knowledge-graph-subgraph` | `…/graph/subgraph?entityId=&hops=1&limit=50` | `{nodes:[{id,name,type,mentionCount}], edges:[{source,target,relation,weight}]}` |

### 9.2 写（WS mutation）

| 动作名 | payload | 返回 |
| --- | --- | --- |
| `knowledge.graph.extract` | `{kbId?, docId?, force?, confirm?}` | `{queued, docs}` |
| `knowledge.graph.rebuild` | `{kbId, purge?, confirm?}` | `{queued}` |

- `extract`：`docId` 优先；仅 `kbId` 时回填该库所有 `pending/failed` 文档；两者都缺 → 400。
- `rebuild`：按 KB 重新抽取全部文档。`purge=true` 时先清空该 KB 的派生数据
  （Neo4j 命名空间 + `graph_mentions` + `graph_relations` + `graph_entities` + `graph_extraction_state`），
  文档与分块不动；`purge=false` 只重抽并覆盖实体/关系（保留可复用实体 id）。
- `extraction.require_confirmation=true` 时，未带 `confirm=true` → `KNOWLEDGE_GRAPH_CONFIRM_REQUIRED`。

### 9.3 错误码

| 码 | HTTP/WS | 场景 |
| --- | --- | --- |
| `KNOWLEDGE_GRAPH_DISABLED` | 409 | 未启用图能力却调用图接口 |
| `KNOWLEDGE_GRAPH_BACKEND_UNAVAILABLE` | 503 | Neo4j 不可用（管理动作；检索侧走降级不报错） |
| `KNOWLEDGE_GRAPH_EXTRACTION_NOT_CONFIGURED` | 409 | 未配置抽取模型 |
| `KNOWLEDGE_GRAPH_CONFIRM_REQUIRED` | 409 | 隐私门禁未确认 |
| `KNOWLEDGE_GRAPH_ENTITY_NOT_FOUND` | 404 | 实体不存在或不在作用域 |
| `KNOWLEDGE_GRAPH_SCOPE` | 403 | KB/workspace 无权访问 |

## 10. WebUI

位置：`#/knowledge` 详情页新增第三个 tab「图谱」（文档 / 检索预览 / 图谱）。

1. **状态卡**：`已启用 / 未启用 / 后端不可用(原因)` + 实体数 / 关系数 / 抽取覆盖（ready/pending/failed/skipped）
   + 「构建图谱」（回填 pending/failed）与「重建」（重抽全库，可选 purge）按钮。
   构建/重建属手动全量动作，须弹隐私确认（chunk 正文会发往模型服务），对齐评测台 judge 门禁；
   `enabled=false` 时两个按钮禁用并显示配置提示（v1 不提供 UI 开关启用图能力）。
2. **实体列表**：搜索框 + 类型筛选；卡片列表（名称、类型、提及数），空态给出「在配置中启用图谱」引导。
3. **实体详情**（二级）：描述、邻居列表（关系名/方向/权重）、提及列表（点击打开现有分块预览弹窗）。
4. **子图可视化**：`d3-force` + SVG 自绘（图谱 tab 组件**动态 import**，`d3-force` 独立 chunk，
   图关闭时不进首屏包），默认选中的实体为中心，1 跳；上限 50 节点 / 100 边；
   支持拖拽/缩放/单击选中、双击扩展一跳；按 `type` 着色（最多 6 类 + 其他），边显示关系名（hover）。
   截断时显示「已截断，仅显示前 N 个节点」。
5. **降级呈现**：后端不可用时图 tab 显示原因与重试按钮；文档/检索功能不受影响。
6. **i18n**：新增 `knowledge.graph.*` 约 25 键，10 语言齐全；公式/术语不进语言文件以外的硬编码。

## 11. 评测联动

- `ALLOWED_RETRIEVAL_FIELDS` 增加：`graph_enabled`(bool)、`graph_hops`(1..2)、`graph_seed_limit`(1..10)、
  `graph_weight`(0..1)、`graph_max_chunks`(10..200)；范围校验放在现有白名单校验同处（`services.py:288-301`）。
- 评测执行器把覆写映射到 `RetrieverSettings`/图扩展开关；覆写只在**本次评测运行内**生效，
  不修改全局配置；`routes` 出现 `graph` 即证明确实走了图路。
- 报告/对比结构不变（7 指标 + `regressions`）；降级运行的 `pool.degraded` 会带 `graph_unavailable`，
  对比页沿用现有「降级运行不能直接对比」提示。

## 12. 安全与隐私

- Neo4j 凭据优先 `password_env`；`repr=False`；日志/错误消息脱敏；不写入 URL。
- 所有 Cypher 参数化；不存在用户自定义 Cypher；KB/workspace 作用域在服务层校验后才查询。
- 抽取会把 chunk 正文发给配置的模型服务：入库自动抽取由 `graph.enabled` 配置开关授权（配置模板/文档明确告知，
  保存配置时 WebUI 设置页给出提示）；手动全量「构建/重建」默认要求显式确认（`confirm=true`）；
  文案复用 judge 门禁措辞；远程服务提示与现有评测一致。
- 图内容来自模型：UI 以纯文本渲染（不注入 HTML），工具输出标注 untrusted；实体描述不参与系统提示。

## 13. 性能与容量

| 项 | 目标 / 上限 |
| --- | --- |
| 检索图路开销 | 本地 Neo4j 1 跳 p95 ≤ 150ms；硬超时 800ms（超时降级 `graph_unavailable`） |
| 候选规模 | seeds ≤5、实体 ≤20、块 ≤50、节点 ≤50、边 ≤100 |
| 抽取 | 每块 ≤1200 字上下文；并发 2；单文档 ≤200 块；失败不影响文档 ready |
| 存储 | 典型单库实体 ≤2k、关系 ≤10k；Neo4j 内存默认 ≤512MB |
| 回填 | 并发 2 时目标 ≥100 块/分钟；进度可查（state 汇总） |

## 14. 测试策略

| 层 | 内容 |
| --- | --- |
| 迁移/存储 | v2→v3 升级、级联删除、实体/关系 MERGE 唯一性、孤儿清理 |
| 抽取 | JSON 修复、无实体 declined、截断重试、4xx/5xx/超时、数量上限、名称过滤、幂等重抽 |
| 链接 | 归一化（全半角/大小写/空白）、中文整串/分词子串命中、种子排序与上限 |
| 融合 | 两路数值回归不变；三路加权 RRF 数值、图路截断、无种子、`graph_unavailable` 降级不抛错；中文实体名子串/分词匹配 |
| 后端 | `GraphStore` 协议 + FakeStore；Neo4j 适配器集成测试（`NEO4J_TEST_URI` 标记，默认 skip） |
| API | 读接口作用域/404/409；WS 动作 confirm 门禁；错误码 |
| 前端 | 实体列表/详情/子图渲染、禁用与降级态、隐私确认弹窗、10 语言 key 形状 |
| E2E | 本地 Neo4j（compose profile）→ 建库入库 → 抽取 → 实体页/子图截图 → 检索 routes 含 graph → 聊天引用 → 评测开/关图对比 |
| 回归 | 图关闭时全流程 16/16、评测 11/11 重跑通过（现有脚本） |

## 15. 实施拆分（WU）

| WU | 内容 | 依赖 | 估算 |
| --- | --- | --- | --- |
| WU-01 | SQLite schema v3 + graph repository + 迁移/级联测试 | — | 1.0 |
| WU-02 | `GraphStore` 协议 + Neo4j 适配器（约束/索引/MERGE/查询）+ 健康检查 + FakeStore | WU-01 | 1.5 |
| WU-03 | 抽取服务：prompt、JSON 守卫、declined、重试、并发/上限、幂等 | WU-01 | 1.5 |
| WU-04 | 入库钩子 + 抽取状态机/租约 + 回填/重建 + 删除同步 | WU-01/02/03 | 1.5 |
| WU-05 | 归一 + 实体链接（整串/分词子串）+ 种子排序 | WU-01 | 1.0 |
| WU-06 | 图检索路线 + 三路 RRF + 降级 + 证据字段 | WU-02/05 | 1.5 |
| WU-07 | 配置块 + 装配 + 能力/状态三态 + 可选 extra + CLI rebuild | WU-02 | 1.0 |
| WU-08 | HTTP 读接口 + WS mutation + 错误码 | WU-07 | 1.0 |
| WU-09 | 图谱 tab：状态卡、实体列表/详情、构建/重建+隐私确认 | WU-08 | 1.5 |
| WU-10 | 子图可视化（d3-force + SVG，交互/上限/降级） | WU-09 | 1.5 |
| WU-11 | Agent 工具证据行 + 提示词措辞 | WU-06 | 0.5 |
| WU-12 | 评测白名单/范围校验 + 开/关图对比验证 | WU-06 | 1.0 |
| WU-13 | 10 语言文案 | WU-09/10 | 0.5 |
| WU-14 | E2E + compose profile + 文档 + 验证报告 | 全部 | 1.5 |
| **合计** | | | **≈16.5 WU（14–18 区间）** |

里程碑：**M1 后端闭环**（WU-01~07，测试全离线 + Neo4j integration 可跳过）→
**M2 前端与工具**（WU-08~11、13）→ **M3 评测与交付**（WU-12、14）。

## 16. 验收标准

1. **零默认影响**：`graph.enabled=false`（默认）时，后端不新增默认依赖（`neo4j` 为可选 extra）；
   前端 `d3-force` 走图谱 tab 的独立 chunk；现有 16/16 全流程与 11/11 评测脚本重跑通过。
2. **构建闭环**：开启 Neo4j 与抽取模型后，入库示例文档 → SQLite 实体/关系/mentions 与 Neo4j 镜像可查；
   `knowledge-graph-stats` 返回 entities/relations/coverage。
3. **检索闭环**：查询命中种子实体 → `routes` 含 `graph`，命中带 `graph.path` 证据；聊天回答可引用；
   关闭图时 `routes` 不含 `graph`。
4. **可视化闭环**：图谱 tab 可浏览实体、实体详情、1 跳子图（≤50 节点）；截图存档。
5. **降级**：停掉 Neo4j → 检索仍返回 200，`degraded` 含 `graph_unavailable`；图谱 tab 显示后端不可用。
6. **幂等**：同一份抽取结果重复同步不产生重复实体/关系/证据；删文档后孤儿实体与关系被清理；
   重建后同一归一 key 不重复（模型重跑允许输出差异，但不得重复计数）。
7. **评测**：同一数据集跑「图关 / 图开」两次，报告与对比正常；未知图字段返回 `KNOWLEDGE_CONFIG_UNSUPPORTED`。
8. **安全**：凭据不出现在日志/响应；越权 KB 返回 403；手动全量抽取未带 `confirm=true` 被拒（`KNOWLEDGE_GRAPH_CONFIRM_REQUIRED`）。
9. **性能**：图路 p95 ≤150ms（本地 Neo4j）、超时 800ms 内降级；抽取不推迟文档 `ready`。

## 17. 风险与缓解

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 抽取质量差（关系幻觉/漏抽） | 图搜索噪声 | 严格 JSON + 名称校验 + 数量上限；v1 只做 1 跳；用评测开/关对比，不达标可关 |
| 抽取成本/延迟 | 费用与入库时长 | 默认关闭 + 每块上限 + 并发 2；失败不阻断；`graph.enabled=false` 完全不调用 |
| Neo4j 运维负担 | 用户装不起来 | 默认关闭、extra 独立、compose profile、清晰错误码与文档；降级不伤主链路 |
| 实体消歧不足 | 合并错误/召回低 | v1 精确归一并在文档中声明边界；别名/embedding 消歧列为 v2 |
| 双存储不一致 | 图与库不同步 | Neo4j 只读派生；`rebuild_kb` 可恢复；所有写路径经 `GraphSyncService` |
| 三路 RRF 改归一化 | 开启图后绝对分变小，可能影响精排复合分/阈值 | 关闭图时走原两路不变；验收包含「开图后精排阈值不误判」；对比只看排名与指标；UI 不展示绝对分 |
| 可视化性能 | 大图卡顿 | 服务端截断 50 节点/100 边；前端 canvas/SVG 限制与懒加载 |
| 隐私 | chunk 外发 | 默认确认门禁、凭据脱敏、文档明确说明 |

## 18. 明确不做（v1）

- 实体人工合并/别名管理/纠错 UI；社区发现（Louvain/Leiden）与 GraphRAG 全局摘要。
- 图谱编辑、人工建边、跨 KB 联合图、多租户独立 Neo4j 实例。
- 打包 Neo4j 进桌面；SQLite/Kuzu 图后端实现（仅保留配置枚举位）。
- 图嵌入/图神经网络；按实体 rerank 的专用模型。

## 19. 开放项（默认值已定）

| 问题 | 默认 |
| --- | --- |
| 是否在 `knowledge-stats` 里附带图计数？ | 不附带，保持既有形状；图计数走 `knowledge-graph-stats` |
| 实体类型词表？ | 使用模型自由输出 + 前端最多 6 类着色，其余归「其他」 |
| 2 跳是否默认开？ | 默认 1 跳；2 跳仅在评测/手动配置开启 |
| UI 是否提供「启用图谱」开关？ | v1 仅展示状态，启用/连接配置走 `config.json`（与 WeKnora 的环境变量姿态一致）；UI 开关列为 v2 |
| Neo4j 版本？ | 5.x Community（compose 固定小版本，如 `neo4j:5.26-community`），无需 APOC |
| 实体规模增长后如何链接？ | v1 LIKE 扫描（≤2k 实体）；超过阈值再补 `search_text` 实体 FTS（v2） |

## 20. Next

**（写入后须暂停，等用户明确继续 — 见 `harness-kit/core/routing.md` § 阶段门禁）**

- 确认方案无误 → 说「写计划」或「制定实施计划」
- 变更范围小、无需计划 → 说「直接实现」或「直接做」
- 需要调整方案（范围/默认值/接口） → 直接说修改意见

## 21. 2026-11-11 WeKnora 对齐修订（覆盖 §7 / §8 / §11）

用户要求按本地 WeKnora `docs/原理/03/04/05/08` 重新对齐后，以下口径取代原方案：

| 主题 | 原方案 | 对齐后 |
| --- | --- | --- |
| 图在融合中的位置 | 第三路加权 RRF（`graph_weight=0.25`） | **`Score=0`，不进 RRF**；图只补位，精排再兑现 |
| 精排候选截断 | 与文本候选一起按分截 | 带 `graph_evidence` 的候选**豁免** `max_candidates` |
| 复合分 base | 图命中沿用 0 | 纯图命中用 `model_score` 顶替 base；图+文本命中保留文本分 |
| 图扩展方向 | 有向 `(s)-[:RELATED*1..N]->(e)` | **无向** `(s)-[:RELATED*1..N]-(e)`；种子可为入边终点 |
| 种子 | 所有名称命中实体 | **无边实体不做种子**（WeKnora `EXISTS { (n)--() }`） |
| 图证据上限 | 50 chunk | 30 chunk（`expansion.max_chunks` 默认 30） |
| 查询侧实体 | 本地归一化子串 + 分词 | **先用抽取模型从改写后的 query 抽实体名**，失败/空/名称无命中均回退本地链接（`expansion.query_entity_extraction` 默认 true） |
| Prompt | 硬编码在 `extractor.py` | 统一到 `knowledge.graph.prompts`（`extraction` / `queryEntities` / `queryUser`），默认值在 `graph/prompts.py` |
| Thinking | 未控制，MiniMax 会先长篇推理 | `extraction.thinking` 默认 `disabled`，请求体带 `thinking: {"type":"disabled"}`；不支持该参数的供应商设 `auto` |
| Agent 工具 | 仅 `knowledge_search` 内含图路 | 新增独立 `knowledge_graph`（对齐 `query_knowledge_graph`） |
| 评测覆写 | 含 `graph_weight` | **删除 `graph_weight`**；旧配置会被拒绝 |

**刻意保留的差异（不是缺陷）：**

- 图开关仍是全局 `knowledge.graph`（WeKnora 是 KB 级 `IndexingStrategy.GraphEnabled + ExtractConfig`）。
- 检索侧已对齐 WeKnora 的「改写 query → LLM 抽实体」；本地归一化子串 + 分词保留为抽取失败/空结果的回退。
- Agent 图谱工具仍由模型在 `query` 里给实体名，不额外做一次查询 LLM 抽取。
- 实体为 KB 级合并（WeKnora 是文档级实例）；v1 不做实体消歧。
- 扩展默认 1 跳、可配 2 跳；WeKnora 对话主链固定 1 跳。
- 不引入 APOC；Neo4j 写入仍用原生 `MERGE`。

详见 `.ai-runtime-artifacts/research/2026-11-11-weknora-agent-search-graph-alignment.md` 与修订后的
`.ai-runtime-artifacts/contracts/2026-11-11-contract-knowledge-graph.md`。
