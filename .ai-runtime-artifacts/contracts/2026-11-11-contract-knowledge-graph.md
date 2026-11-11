---
artifact: spec
route: writing-plans + api-and-interface-design
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
  - harness-kit/.agents/skills/api-and-interface-design/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - harness-kit/core/artifacts.md
  - .ai-runtime-artifacts/specs/2026-11-10-knowledge-graph-spec.md
  - .ai-runtime-artifacts/stack/2026-11-10-knowledge-graph-stack.md
  - nanobot/knowledge/database.py
  - nanobot/knowledge/repository.py
  - nanobot/knowledge/fusion.py
  - nanobot/knowledge/retriever.py
  - nanobot/knowledge/questions.py
  - nanobot/knowledge/ingestor.py
  - nanobot/knowledge/service.py
  - nanobot/webui/knowledge_routes.py
  - nanobot/webui/knowledge_api.py
  - nanobot/webui/settings_routes.py
  - nanobot/webui/ws_http.py
  - nanobot/evaluation/knowledge/models.py
  - nanobot/evaluation/knowledge/runner.py
  - nanobot/config/schema.py
  - nanobot/config_base.py
  - webui/src/lib/api.ts
  - webui/src/components/workspace/KnowledgePage.tsx
created_at: 2026-11-11
status: proposed
approved: false
---

# 知识图谱（可选 Neo4j 后端）增量契约 v1

本契约随实施计划待审；**仅定义增量**，不表示接口已实现。

来源 spec：`.ai-runtime-artifacts/specs/2026-11-10-knowledge-graph-spec.md`（v1，draft）。

## 0. 契约总则

1. **纯增量**：不改任何既有端点、动作名、工具名、既有 `SearchResult` 字段语义。图谱能力**默认关闭**（`knowledge.graph.enabled=false`），关闭时既有行为逐字节不变。
2. **SQLite 是唯一真相源**；Neo4j 只读派生，任何时候可由 SQLite 重建（`rebuild_kb`）。
3. **边界校验**：作用域（KB/workspace）在服务层校验后才访问后端；外部模型返回的 JSON 一律当作不可信数据解析 + 校验。
4. **降级优先于报错**：图后端不可用 → 检索仍 200，`degraded` 含 `graph_unavailable`；只有图管理动作才 503。
5. **新增优先**：既有 dataclass / Pydantic 模型只加**带默认值的可选字段**，不改既有字段类型。

## 1. SQLite schema v3（`nanobot/knowledge/database.py`）

`SCHEMA_VERSION` 从 `"2"` 升到 `"3"`；新增 `_V3_STATEMENTS` 并注册进 `_MIGRATIONS`（`("3", _V3_STATEMENTS)`）。**不改**既有表结构，**不动** `document_chunks` 不可变触发器。

新增 5 张表（DDL 见 spec §5.1，逐字照抄 spec 的 `CREATE TABLE` / `CREATE INDEX` 语句）：

| 表 | 主键 | 唯一约束 | 外键级联 |
| --- | --- | --- | --- |
| `graph_entities` | `id` | `UNIQUE (kb_id, name_norm)` | `kb_id → knowledge_bases(id)` CASCADE |
| `graph_relations` | `id` | `UNIQUE (kb_id, source_entity_id, target_entity_id, relation)` | `kb_id` CASCADE；两端实体 CASCADE |
| `graph_mentions` | `(entity_id, chunk_id)` | — | entity/doc/chunk 均 CASCADE |
| `graph_relation_evidence` | `(relation_id, chunk_id)` | — | relation/doc/chunk 均 CASCADE |
| `graph_extraction_state` | `doc_id` | — | `doc_id → documents(id)` CASCADE |

**级联语义（契约级，spec §5.1）**：删文档 → `graph_mentions` / `graph_relation_evidence` / `graph_extraction_state` 级联删除；**实体与关系不级联删**，由同步服务按剩余证据重算 `mention_count`，归零者再删除（孤儿清理）。

**id 生成（稳定、可重建）**：

```python
entity_id  = "ent_" + sha256(f"{kb_id}\0{name_norm}".encode()).hexdigest()[:32]
relation_id = "rel_" + sha256(f"{kb_id}\0{src_norm}\0{relation}\0{tgt_norm}".encode()).hexdigest()[:32]
```

## 2. 图域数据模型（`nanobot/knowledge/graph/models.py`）

全部为 `@dataclass(frozen=True, slots=True)`（与 `nanobot/knowledge/repository.py` 的 `StoredChunk` / `FtsHit` 同风格）：

```python
GraphEntity(id, kb_id, name, name_norm, type, description, mention_count)
GraphRelation(id, kb_id, source_entity_id, target_entity_id, relation, description, weight, mention_count)
GraphMention(entity_id, doc_id, chunk_id)
GraphEvidence(relation_id, doc_id, chunk_id)
GraphEdge(source_entity_id, target_entity_id, relation, weight, hop)
GraphExpansion(entity_ids, edges, chunk_ids, truncated)
GraphHealth(status, reason)            # status ∈ {"ready", "degraded"}
GraphStats(enabled, backend, status, reason, entities, relations,
           docs_ready, docs_pending, docs_failed, docs_skipped,
           truncated_docs, last_extracted_at)
GraphExtractionState(doc_id, status, attempt, lease_owner, lease_expires_at,
                     model, prompt_version, chunk_total, chunk_done,
                     entity_count, relation_count, truncated,
                     error_code, error_message, extracted_at, updated_at)
```

**状态机**：`graph_extraction_state.status ∈ {pending, processing, ready, failed, skipped}`（与 DDL 的 CHECK 一致）。`processing` 租约超时 10 分钟回收（`lease_expires_at`）。

## 3. `GraphStore` 协议（`nanobot/knowledge/graph/store.py`）

后端可替换的唯一接口；**只有 `GraphSyncService` 与 `GraphRoute` 可以调用它**。

```python
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

**语义约定**：

- `health()` 不抛异常；连接失败返回 `GraphHealth(status="degraded", reason="neo4j_unavailable")`。
- `ensure_schema()` 创建唯一约束 `graph_entity_uid`（`e.uid IS UNIQUE`）、索引 `graph_entity_kb`（`e.kb_id`）、`GraphMeta{version=1}`；失败抛 `GraphStoreError`。
- `upsert_subgraph()` 只写检索/可视化所需字段；节点 `uid = f"{kb_id}:{entity_id}"`，关系 `uid = f"{kb_id}:{relation_id}"`；`chunk_ids` 由 SQLite `graph_mentions` 计算后整体替换，单实体最多 200 个证据块。
- `expand()` 返回 1~2 跳可达的 `entity_ids` / `edges` / `chunk_ids`；触顶时 `truncated=True`。
- 任何 Cypher **必须参数化**；不允许用户自定义 Cypher。

## 4. 抽取协议（`nanobot/knowledge/graph/extractor.py`）

`prompt_version = "graph1"`；输入 `heading_path + chunk_text`（截断 `max_context_chars=1200`）；输出严格 JSON：

```json
{"entities": [{"name": "...", "type": "...", "description": "..."}],
 "relations": [{"source": "...", "target": "...", "relation": "...",
                "description": "...", "strength": 8}]}
```

**守卫表（逐条可测，spec §6.2）**：

| 情况 | 结果 |
| --- | --- |
| 无 `entities` 键 / 空数组 / 明确表示无实体 | `declined=True`（不重试，状态 `skipped`） |
| JSON 解析失败 | `json_repair.loads` 修复一次（`json-repair` 已是核心依赖，`pyproject.toml:45`）；仍失败 → `failed(parse)` |
| `finish_reason == "length"` | 上下文减半重试一次 |
| HTTP 4xx（除 429） | `failed(http_4xx)`，不重试 |
| 429 / 5xx / 超时 | 指数退避重试 1 次 → 仍失败 `failed(...)` |
| 实体名 > 40 字 / 含代词 / 空白 | 丢弃该实体**及其关系** |
| 关系端点不在实体集合 | 丢弃该关系 |
| 数量超限 | 按出现顺序截断（实体 20 / 关系 30） |

**幂等**：重抽先删本轮 `graph_mentions` / `graph_relation_evidence`，再写入；`mention_count` **一律由证据表聚合重算**（不累加）；证据归零的实体/关系删除。幂等定义 = 不产生重复行/重复计数（模型重跑输出允许有差异）。

## 5. 归一与链接（`nanobot/knowledge/graph/normalize.py` / `linker.py`）

```python
normalize_entity_name(name) -> str   # NFKC → 去首尾空白 → 连续空白折叠 → casefold
```

**种子链接顺序**（`find_seed_entities`）：

1. 整串精确：`normalize_entity_name(query) == name_norm`；
2. 子串：`tokenize_query(query)` 的 token 对 `name_norm LIKE '%token%'`；
3. 排序：整串精确 > 命中 token 数 > `mention_count`，同值按 `id` 升序；取前 `seed_limit`（默认 5）。

**v1 不建实体 FTS**（中文 unicode61 会把整段中文当单 token）；实体规模 ≤2k 时 LIKE 扫描为毫秒级。

## 6. 检索融合（`nanobot/knowledge/fusion.py` / `retriever.py`）

- `RetrieverSettings` 新增（**全部带默认值**）：`graph_enabled: bool = False`、`graph_weight: float = 0.25`、`graph_hops: int = 1`、`graph_seed_limit: int = 5`、`graph_max_entities: int = 20`、`graph_max_chunks: int = 50`、`graph_timeout_seconds: float = 0.8`。
- `SearchHit` 新增可选字段 `graph_evidence: GraphEvidencePayload | None = None`（默认 `None`，既有构造点零改动）。
- `SearchResult.routes` 扩展：启用图且图路**实际产出候选**时含 `"graph"`；关闭图时**逐字不变**（仍是 `("vector","keyword")`）。
- 融合：图开启时传三个 `RouteSpec`（vector 0.7 / keyword 0.3 / graph `graph_weight`），`rrf_k=60` 不变。**图关闭时走现有两路实现，数值完全不变**（`maxRRF` 分母变化仅在开图时发生）。
- 候选块**必须回 SQLite 复核**「文档 ready + chunk is_enabled + KB enabled + 作用域命中」后才进融合。
- 降级标签：`graph_unavailable`（后端连接/查询失败）、`graph_truncated`（节点/块数触顶）。无种子**不降级**（正常空）。
- 证据结构（每命中只保留**最短一条**路径）：

```ts
graph?: { seeds: string[]; path: Array<{from: string; relation: string; to: string}>; hop: 1 | 2; entityIds: string[] }
```

## 7. HTTP 读接口（`nanobot/webui/knowledge_routes.py`）

**读动作名与路径**（新增 4 个；须同时登记进 `READ_ACTIONS` 与 `READ_PATHS`）：

| 动作名 | 路径 | 查询参数 | 返回 |
| --- | --- | --- | --- |
| `knowledge-graph-stats` | `/api/settings/knowledge/graph/stats` | `kbId` | `GraphStats` 形状 |
| `knowledge-graph-entities` | `/api/settings/knowledge/graph/entities` | `kbId, q, type, limit(≤100), offset` | `{entities:[...], total}` |
| `knowledge-graph-entity` | `/api/settings/knowledge/graph/entity` | `id` | `{entity, mentions:[...], neighbors:[...]}` |
| `knowledge-graph-subgraph` | `/api/settings/knowledge/graph/subgraph` | `entityId, hops(1..2), limit(≤50)` | `{nodes:[...], edges:[...]}` |

**写动作名**（新增 2 个；须同时登记进 `WRITE_ACTIONS` 与 `WRITE_PATHS`）：

| 动作名 | payload | 返回 |
| --- | --- | --- |
| `knowledge.graph.extract` | `{kbId?, docId?, force?, confirm?}` | `{queued, docs}` |
| `knowledge.graph.rebuild` | `{kbId, purge?, confirm?}` | `{queued}` |

**注册链路（无需改其他文件）**：`settings_routes.py:165-166` 已把 `knowledge_domain.READ_PATHS/WRITE_PATHS` 合并进 `_SYSTEM_ROUTES`；`settings_routes.py:282` 与 `ws_http.py:247-248` 均从 `WRITE_PATHS` 反向派生。新增动作**只要改 `knowledge_routes.py` 即可生效**。

**错误码**（`KnowledgeRouteError(code=..., status=...)`）：

| 错误码 | 状态 | 场景 |
| --- | --- | --- |
| `KNOWLEDGE_GRAPH_DISABLED` | 409 | 未启用图能力却调用图接口 |
| `KNOWLEDGE_GRAPH_BACKEND_UNAVAILABLE` | 503 | Neo4j 不可用（**仅管理动作**；检索侧走降级不报错） |
| `KNOWLEDGE_GRAPH_EXTRACTION_NOT_CONFIGURED` | 409 | 未配置抽取模型 |
| `KNOWLEDGE_GRAPH_CONFIRM_REQUIRED` | 409 | 隐私门禁未确认 |
| `KNOWLEDGE_GRAPH_ENTITY_NOT_FOUND` | 404 | 实体不存在或不在作用域 |
| `KNOWLEDGE_GRAPH_SCOPE` | 403 | KB/workspace 无权访问 |

**确认门禁**：`extraction.require_confirmation=true` 时，手动全量 `extract`（仅 kbId）/ `rebuild` 未带 `confirm=true` → `KNOWLEDGE_GRAPH_CONFIRM_REQUIRED`。入库自动抽取由 `graph.enabled` 配置开关授权，不走门禁。

## 8. 评测联动（`nanobot/evaluation/knowledge/models.py`）

`ALLOWED_RETRIEVAL_FIELDS` 新增 5 个键：`graph_enabled`(bool)、`graph_hops`(1..2)、`graph_seed_limit`(1..10)、`graph_weight`(0..1)、`graph_max_chunks`(10..200)；范围校验放在 `nanobot/evaluation/services.py:278-301` 的既有白名单校验**同处**。未知图字段仍返回 `KNOWLEDGE_CONFIG_UNSUPPORTED`。

覆写只在**本次评测运行内**生效（映射到 `RetrieverSettings`），不改全局配置；`routes` 出现 `graph` 即证明确实走了图路。

## 9. 前端契约（`webui/src/lib/api.ts` + `KnowledgePage.tsx`）

- 详情页第三个 tab：`detailTab` 联合类型扩为 `"documents" | "search" | "graph"`；tab 按钮沿用既有 `role="tablist"` / `role="tab"` / `aria-selected` 模式。
- 新增客户端函数（camelCase payload，与既有 settings 客户端口径一致）：`fetchKnowledgeGraphStats` / `fetchKnowledgeGraphEntities` / `fetchKnowledgeGraphEntity` / `fetchKnowledgeGraphSubgraph` / `knowledgeGraphExtract` / `knowledgeGraphRebuild`。
- `d3-force` **动态 import**（图谱 tab 独立 chunk），图关闭时不进首屏包。
- i18n：新增 `knowledge.graph.*` 键，**10 语言键集合必须完全一致**（`webui/src/tests/evaluation-i18n-parity.test.ts` 的同款断言规则）。

## 10. 工具输出（`nanobot/agent/tools/knowledge_common.py`）

`render_search_result` 在 `<result>` 上追加可选属性 `graph_path="A —关系→ B"`；**不改** `doc://` / `chunk://` 句柄格式、**不改** 工具名 `knowledge_search`。图内容来自模型 → 以纯文本渲染、不注入 HTML。

## 11. 配置契约（`nanobot/config/schema.py`）

`Base`（`nanobot/config_base.py:12-15`）已提供 `alias_generator=to_camel` + `populate_by_name=True`，camelCase 兼容**无需额外代码**。新增三个配置块（字段见 spec §8）挂到 `KnowledgeConfig.graph: KnowledgeGraphConfig`；`KnowledgeRetrievalConfig` 同步新增 5 个图参数（与 §6 的 `RetrieverSettings` 字段同名，便于评测映射）。

**凭据**：`api_key` / `password` 一律 `Field(default="", repr=False)`；优先读 `*_env` 环境变量；日志/错误消息脱敏（复用 `questions.py:101-106` 的 `_redact` 口径）。

## 12. 不变量清单（验收逐条对应）

1. `graph.enabled=false`（默认）→ 无新默认依赖（`neo4j` 走**可选 extra** `graph`）、既有 16/16 全流程与 11/11 评测脚本重跑通过。
2. Neo4j 停掉 → 检索仍 200、`degraded` 含 `graph_unavailable`；图管理动作 503。
3. 同一份抽取结果重复同步 → 无重复实体/关系/证据、无重复计数。
4. 删文档 → 级联删证据 → 孤儿实体/关系被清理。
5. 越权 KB → 403；未确认的手动全量抽取 → `KNOWLEDGE_GRAPH_CONFIRM_REQUIRED`。
6. 图路 p95 ≤150ms（本地 Neo4j），硬超时 800ms 内降级；抽取不推迟文档 `ready`。

## Next

- 契约确认 → 回到 plan 继续；契约与实现冲突 → **先改契约再改代码**
- 契约要改 → 直接说修改意见
