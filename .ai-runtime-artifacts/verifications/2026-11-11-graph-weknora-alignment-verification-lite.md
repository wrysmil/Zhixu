---
artifact: verification-lite
route: leader-direct
skills:
  - verification-before-completion
skills_evidence:
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户原话：「对于 search 工具中，是否涉及到了图检索。等等那些，你都对齐下那个开源项目」「search_knowledge 这个底层是三路检索」「可以重点看看 docs 下所有相关的文档」
created_at: 2026-11-11
tier: 1
---

# WeKnora 图检索 / Agent 工具对齐 — 轻量验证（Tier 1）

## 范围

| 文件 | 改动 |
| --- | --- |
| `nanobot/knowledge/retriever.py` | 图命中 Score=0、不进 RRF；精排候选豁免；纯图命中 base 用 model_score |
| `nanobot/knowledge/graph/neo4j_store.py` | 扩展改无向；边去重并保留真实方向与最短 hop；诱导边 hop=1 |
| `nanobot/knowledge/graph/route.py` | 证据 BFS 双向遍历；查询侧先 LLM 抽实体，失败/空回退本地链接 |
| `nanobot/knowledge/graph/linker.py` | 新增 `find_seed_entities_by_names`（LLM 实体名 → 种子） |
| `nanobot/knowledge/graph/repo.py` | 无边实体不做种子；新增 `entities_for_chunks` |
| `nanobot/knowledge/graph/service.py` | 新增 `query_related` / `GraphQueryResult`（活证据校验、关系计数/截断）；新增 `extract_query_entities`（查询侧 LLM 抽实体，失败返回空） |
| `nanobot/knowledge/graph/extractor.py` | 新增 WeKnora `extract_entity` 同款专用 prompt（system + few-shot）、`extract_query` 与 `parse_query_entities`（兼容 `[{"entity":...}]` / `{"entities":[...]}` / 字符串列表）；prompt 从配置注入 |
| `nanobot/knowledge/graph/prompts.py`、`nanobot/config/schema.py` | 新增 `knowledge.graph.prompts`（`extraction` / `queryEntities` / `queryUser`）与默认值；校验 `<<content>>` / `<<query>>` 占位符；新增 `extraction.thinking`（默认 disabled） |
| `nanobot/config/schema.py`、`nanobot/knowledge/service.py` | 新增 `expansion.query_entity_extraction`（默认 true）与 5s 超时；装配 `GraphRoute` 时注入抽取回调 |
| `nanobot/agent/tools/knowledge_graph.py` | 新增独立图谱工具（对齐 `query_knowledge_graph`） |
| `nanobot/agent/tools/knowledge_search.py` | 描述改为三路，并指向 `knowledge_graph` |
| `nanobot/agent/loop.py` | 注入 `knowledge_graph_enabled`，图谱工具按配置注册 |
| `nanobot/config/schema.py`、`nanobot/knowledge/service.py`、`nanobot/evaluation/knowledge/models.py` | 删除 `graph_weight`；`max_chunks` 默认 30；新增 `retrieval.enable_rewrite`（默认 true） |
| `nanobot/agent/tools/knowledge_search.py`、`nanobot/agent/loop.py` | `enable_rewrite=false` 时忽略 `rewrite_query`/`intent`，直接用原 query（WeKnora `EnableRewrite` 门控） |
| 测试 | 更新 graph 路由/链接/服务/配置/评测覆写/工具测试，新增图谱工具测试 |

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `pytest tests/knowledge/test_graph_extractor.py tests/knowledge/test_graph_route.py tests/knowledge/test_graph_linker.py tests/knowledge/test_graph_service.py tests/knowledge/test_graph_config.py tests/knowledge/test_config.py tests/evaluation/knowledge/test_graph_overrides.py tests/tools/test_knowledge_graph_tool.py tests/tools/test_knowledge_tools.py -q` | **138 passed** |
| `pytest tests/knowledge tests/tools/test_knowledge_tools.py tests/evaluation/knowledge -q`（`NO_PROXY=` 清空） | **758 passed / 8 failed / 1 skipped**；8 个为既有 Windows chmod/symlink/CRLF 基线（`storage/test_extract_lines` 1、`storage/test_files` 5、`test_service` 2） |
| `NEO4J_TEST_URI=bolt://127.0.0.1:17687 NEO4J_TEST_PASSWORD=testpass pytest tests/knowledge/test_graph_store.py -q` | **3 passed**（临时 `neo4j:5.26-community` 容器，验证后已删除） |
| 真实 Neo4j 完整检索探针（`KnowledgeService` + `GraphSyncService` + 真实 `Neo4jGraphStore` + fake reranker） | `routes=('keyword','graph')`、`rerank=ok`、`candidates_in=2/kept=2`；精排收到 2 条 passage，纯图命中 `chk-2 score=0.910` 排第一，图+关键词 `chk-1 score=0.460` |
| `ruff check <changed py files>` | **All checks passed** |

> 解释器：`D:\workspace\environment\python3.13\python.exe`（仓库 `.venv` 缺 `jieba`）。
> 本机 `NO_PROXY` 含非法 IPv6 端口（`':1]'`），不清空会让 `test_embedding` / `test_questions` / `test_rerank` 的 httpx client 构造失败；清空后通过，与本次改动无关。

## 关键行为证据

- `test_graph_only_hit_gets_zero_score_and_text_hit_keeps_its_score`：纯图命中 `score == 0.0`，图+关键词命中保留关键词分。
- `test_graph_only_hit_is_exempt_from_rerank_candidate_cap`：`max_candidates=1` 时纯图命中仍进入精排并拿到复合分。
- `test_graph_hit_uses_model_score_as_rerank_base`：纯图命中复合分 `base` 用 `model_score`，图+文本命中保留文本分。
- `test_query_related_returns_relations_and_live_evidence`：只返回有活证据的实体/关系/块，计数正确。
- `test_neo4j_upsert_and_expand`：从入边终点 `e3` 一跳可扩展到 `e2`，且边方向保持 `e2 -> e3`。
- `tests/tools/test_knowledge_graph_tool.py`：实体/关系/证据渲染、种子标记、截断计数、后端不可用/空结果/部分失败区分。
- `test_search_ignores_rewrite_and_intent_when_rewrite_disabled`：`enable_rewrite=false` 时忽略改写/意图，直接用原 query；`test_loop_rewrite_flag_follows_config` 验证配置注入。
- `test_llm_extracted_entities_seed_graph_route`：查询侧抽实体被调用一次，抽出的 `值班二号` 能直接作为图种子。
- `test_query_entity_extraction_failure_falls_back_to_local_linker`：抽取失败不拖垮图路，回退本地链接仍产出 `graph` 路由。
- `test_unmatched_llm_entities_fall_back_to_local_linker`：LLM 抽出图里不存在的泛化名时，按 KB 回退本地链接，避免图路静默缺席。
- `test_extract_query_entities_returns_names` / `_failure_is_empty`：抽取成功返回实体名，失败返回空。
- `test_extract_query_uses_dedicated_prompt_and_parses_entities`：查询侧发送 WeKnora 同款 system + few-shot，并解析 `[{"entity":"值班二号"}]`；`test_extract_query_entities_uses_dedicated_query_prompt` 确认服务优先调用 `extract_query` 而非入库 chunk prompt。
- `test_custom_prompts_are_rendered` / `test_build_graph_extractor_passes_prompts`：自定义 `extraction` / `queryEntities` / `queryUser` 从配置注入并正确渲染占位符。
- `test_thinking_defaults_to_disabled_and_can_be_auto`：默认请求带 `thinking={"type":"disabled"}`，`auto` 时省略该字段。
- `test_graph_prompts_camel_case_roundtrip` / `test_graph_prompts_require_placeholders`：prompt 配置 camelCase 往返与占位符校验。
- `test_full_search_flow_sends_graph_only_hit_to_rerank`：完整 `search()` 流程（抽实体 → 关键词/图召回 → 融合 → 去重 → 精排 → MMR → 装配）；`max_candidates=1` 时纯图命中仍进入精排，拿到 `0.91` 复合分并排第一；精排实际收到 3 条 passage。
- 真实 Neo4j 全流程探针：图扩展来自真实 Cypher（无向），精排收到 `chk-1`、`chk-2` 两条 passage；`chk-2` 纯图命中 `0.910` 排第一，`chk-1` 带 `graph_path=灰度发布—执行人→值班二号`。

## 未验证项

- **查询侧抽实体的真实延迟/成本未实测**：新增一次 LLM 调用，已配 5s 超时与本地回退；未在真实大图上量 p95。
- **WebUI 图谱页未重新截图**：本次未改前端；上一轮截图仍对应现有 UI。
- **评测「开图/关图」未重跑**：图从 RRF 改为 0 分补位后，旧评测指标不再可比；需要真实 Neo4j + 抽取模型时重新跑基线。
- **未跑全量测试套件**：仅覆盖 knowledge / knowledge tools / evaluation knowledge 域。
- `tests/tools` 全量运行受本机 `NO_PROXY` 与进程树基线影响（25 failed，均为 web_search / shell 既有环境问题）；知识工具子集已通过。

## Next

- 任务完成；未执行 commit（用户未要求）。
- 若需要发布口径 → 先重跑真实评测开/关图对比，再更新验证报告中的指标。
