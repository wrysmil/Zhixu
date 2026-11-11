---
artifact: research
route: implementation
skills:
  - source-driven-development
source:
  - D:\studyspace\源码学习\WeKnora\docs\原理\03-检索原理.md
  - D:\studyspace\源码学习\WeKnora\docs\原理\04-知识图谱原理.md
  - D:\studyspace\源码学习\WeKnora\docs\原理\05-Agent检索与上下文装配原理.md
  - D:\studyspace\源码学习\WeKnora\docs\原理\08-图检索原理-图解版.md
  - D:\studyspace\源码学习\WeKnora\docs\仿写\仿写知识检索指南.md
  - D:\studyspace\源码学习\WeKnora\docs\仿写\仿写知识图谱指南.md
  - D:\studyspace\源码学习\WeKnora\internal\agent\tools\search_knowledge.go
  - D:\studyspace\源码学习\WeKnora\internal\agent\tools\query_knowledge_graph.go
  - D:\studyspace\源码学习\WeKnora\internal\application\service\chat_pipeline\search_entity.go
  - D:\studyspace\源码学习\WeKnora\internal\application\service\chat_pipeline\search_parallel.go
  - D:\studyspace\源码学习\WeKnora\internal\application\repository\retriever\neo4j\graph_search.go
created_at: 2026-11-11
---

# WeKnora Agent 检索 / 图检索对齐调研

## 1. 问题

用户问：WeKnora 的 `search_knowledge` 工具里是否涉及图检索？以及图路线、工具分工等应如何对齐。

## 2. 结论

**WeKnora 的 `search_knowledge` Agent 工具本身不调用图。** 图检索有两条独立入口：

| 入口 | 是否走图 | 输入 | 说明 |
| --- | --- | --- | --- |
| `search_knowledge`（Agent 工具） | **否** | query / mode / kb ids / limit | 只做向量 + 关键词；graph-only KB 被排除并提示改用图谱工具 |
| 对话主检索管线 | **是，第三路并行** | 改写后的 query → LLM 抽实体 | `PluginSearchParallel` 并发 `chunk_search` + `entity_search`，合并后统一 rerank |
| `query_knowledge_graph`（Agent 工具） | **是** | query（实体名）/ kb ids | Agent 主动探索关系，返回实体、关系、来源 chunk 句柄 |

因此「三路检索」在 WeKnora 是**对话主链**的概念；Agent 侧把文本检索和图谱探索拆成两个工具。

## 3. 图路的关键口径（docs 03/04/08）

1. **图只做一路召回 + 一层溯源**：`query → 实体 → 1 跳子图 → 节点上的 chunk 引用 → 回 chunks 表取正文`；图不独立回答。
2. **图命中 `Score=0`**，不是 1.0；不进 RRF 求和，避免给「有关系」伪造相似度。
3. **精排候选截断时图命中豁免**（否则 0 分必被删）；复合分里纯图命中用 `model_score` 顶替 `base_score`。
4. **查询用改写后的 query 抽实体**；四个前置判断：`NEO4J_ENABLE`、`NeedsRetrieval`、`SearchTargets` 非空、至少一个 KB 抽取开启。
5. **Cypher 设计**：`n.name CONTAINS 词项`、`EXISTS { (n)--() }`（无边实体不做种子）、精确名优先、稳定排序、种子/行数双重上限、每条边只从最早种子展开、无向匹配后还原真实方向。
6. **证据三步复核**：图里的 chunk 引用必须仍存活、启用、属于当前作用域；每个被引用的 chunk 都要重新校验。
7. **截断必须可见**：只给 30 条关系却不说「142 中显示 30 条」，模型会回答「X 和 Y 没有关系」。
8. **Agent 图谱工具**：查询词项最多 8 个、关系最多 30、证据候选 512、展示 10；输出实体 / 关系 / `chunk://` 句柄 + 截断说明；无 graph-enabled KB 时工具不注册。

## 4. 对齐前的 Zhixu 与差异

| 主题 | 对齐前 Zhixu | WeKnora | 本次处理 |
| --- | --- | --- | --- |
| 图在融合中的位置 | 第三路 `RouteSpec`，权重 0.25 进 RRF | `Score=0`，不进 RRF | 改为 0 分补位，删除 `graph_weight` |
| 精排候选截断 | 图命中与其他候选一起 `candidates[:max_candidates]` | 图命中豁免 | 带 `graph_evidence` 的候选全部豁免 |
| 复合分 base | 图命中 base=0 | 纯图命中用 `model_score` 顶替 | 纯图命中改为 model_score |
| 图扩展方向 | 有向 `(s)-[:RELATED*1..N]->(e)` | 无向 `(n)-[r]-(m)` + 方向还原 | 改为无向；证据路径保留真实方向 |
| 种子 | 名称命中即可 | 无边实体不做种子 | `search_entities_by_like` 加关系存在性过滤 |
| 图证据上限 | 50 chunk | 30 chunk | 默认改 30 |
| Agent 图谱工具 | 无（图只在 `knowledge_search` 内部） | 独立 `query_knowledge_graph` | 新增 `knowledge_graph` |
| 关系证据 | 只输出 `graph_path` | 关系只保留两端都有活证据的边 | 图谱工具按活证据过滤关系 |
| 截断提示 | 检索有 `graph_truncated` 标签 | 工具输出显式计数与警告 | 图谱工具输出 `relations/chunks` 计数与 warning |
| 评测覆写 | 含 `graph_weight` | 无此概念 | 删除；旧配置被拒绝 |
| 改写门控 | 无配置开关，模型传 `rewrite_query`/`intent` 就使用 | `conversation.enable_rewrite` 默认 true；关闭时 `RewriteQuery=Query` 并跳过内部调用 | 新增 `knowledge.retrieval.enable_rewrite` 默认 true；关闭时忽略 `rewrite_query`/`intent`，直接用原 query |
| 查询侧实体 | 本地归一化子串 + 分词 | 改写后 query 再调一次 LLM 抽实体名；prompt 见 `config/config.yaml` 的 `extract_entity`（步骤说明 + few-shot，输出 `[{"entity": ...}]`） | 新增 `GraphService.extract_query_entities` + `OpenAICompatGraphExtractor.extract_query` 专用 prompt；`GraphRoute` 先 LLM 抽实体，失败/空/名称无命中均回退本地链接；`expansion.query_entity_extraction` 默认 true |
| Prompt 管理 | 硬编码在 `extractor.py` | 模板在 `config/config.yaml` | 统一到 `knowledge.graph.prompts`（`extraction` / `queryEntities` / `queryUser`），默认值在 `graph/prompts.py` |
| Thinking | 未控制，MiniMax 先长篇推理再散文 | WeKnora 抽取显式 `thinking=false` | `extraction.thinking` 默认 `disabled`，请求带 `thinking: {"type":"disabled"}`；实测查询抽实体 1.4s 返回 JSON |

## 5. 刻意保留的差异

| 差异 | 保留原因 |
| --- | --- |
| 图开关是全局 `knowledge.graph`，不是 KB 级 | 现有配置/装配/评测均以全局开关为单一来源；改 KB 级会牵动 schema、API、UI 与抽取队列 |
| 本地实体链接保留为回退 | 已对齐 WeKnora 的查询侧 LLM 抽实体；本地链接只在模型未配置、超时、失败或抽到空实体时兜底 |
| 查询改写不新增内部 LLM 调用 | Agent 模式下主模型已在同一次工具调用里填 `rewrite_query`/`intent`；再加内部调用等于重复改写。`enable_rewrite=false` 时按 WeKnora 语义忽略这两个参数 |
| Agent 图谱工具不再抽实体 | `knowledge_graph` 由模型直接在 `query` 里给实体名，不额外烧一次查询 LLM |
| 实体按 KB 合并，不是文档级实例 | v1 无实体消歧；文档级会牺牲跨文档关系，KB 级符合当前产品口径 |
| 默认 1 跳、可配 2 跳 | 2 跳是保留的显式能力；WeKnora 主链固定 1 跳 |
| 不引入 APOC | 原生 `MERGE` 已满足幂等；少一个插件依赖 |
| 不做 200 候选的 rerank 池上限 | Zhixu 的 `max_candidates` 是独立配置项，沿用现有精排预算 |

## 6. 落地位置

- 融合/精排：`nanobot/knowledge/retriever.py`
- 配置/装配：`nanobot/config/schema.py`、`nanobot/knowledge/service.py`、`nanobot/evaluation/knowledge/models.py`
- 图存储/链接：`nanobot/knowledge/graph/{neo4j_store,repo,linker,route,service}.py`
- 查询侧 LLM 抽实体：`extractor.py` 专用 `extract_query` + `parse_query_entities`；prompt 从 `knowledge.graph.prompts` 读取；`GraphService.extract_query_entities` + `GraphRoute.search`（一次检索一次调用，5s 超时，本地回退）
- Agent 工具：`nanobot/agent/tools/knowledge_graph.py`、`knowledge_search.py`、`nanobot/agent/loop.py`
- 测试：`tests/knowledge/test_graph_*`、`tests/tools/test_knowledge_graph_tool.py`、`tests/evaluation/knowledge/test_graph_overrides.py`

## 7. 验证

- 定向单元测试 138 passed。
- 知识域回归：758 passed / 8 failed（均为既有 Windows chmod/symlink/CRLF 基线）；`NO_PROXY` 清空后 remote client 用例通过。
- 临时 Neo4j 5.26 容器：`test_graph_store.py` 3 passed，含「种子为入边终点仍可扩展」断言。
- 真实 Neo4j 完整检索探针：`routes=('keyword','graph')`、`rerank=ok`、`candidates_in=2/kept=2`；精排收到 2 条 passage，纯图命中 `chk-2 score=0.910` 排第一，图+关键词 `chk-1 score=0.460` 带 `graph_path`。
- 详细证据见 `.ai-runtime-artifacts/verifications/2026-11-11-graph-weknora-alignment-verification-lite.md`。
