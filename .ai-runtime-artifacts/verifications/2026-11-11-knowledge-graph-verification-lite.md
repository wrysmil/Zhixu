---
artifact: verification
route: verification-before-completion
skills:
  - verification-before-completion
created_at: 2026-11-11
status: passed
source:
  - .ai-runtime-artifacts/plans/2026-11-11-knowledge-graph-plan.md
  - .ai-runtime-artifacts/specs/2026-11-10-knowledge-graph-spec.md
  - .ai-runtime-artifacts/contracts/2026-11-11-contract-knowledge-graph.md
---

# 知识图谱实现验证记录（2026-11-11）

## 范围

已完成计划中 Task 0–13 的代码实现：

- SQLite schema v3（5 张图表）+ GraphRepository；
- GraphStore 协议、Neo4j 适配器、FakeGraphStore；
- 抽取器（JSON 守卫/declined/重试/上限）、归一与种子链接；
- GraphService（独立 worker 执行体、回填/重建、删除同步）与 Ingestor 钩子；
- 图检索第三路 + 三路 RRF + 降级 + 证据字段；
- 配置块、service 装配、CLI `knowledge graph rebuild`；
- 4 读 + 2 写 WebUI 动作与错误码；
- 图谱 tab（状态/实体列表/详情/确认门禁）+ d3-force 子图 + 10 语言；
- 评测 `graph_*` 白名单与工具 `graph_path` 证据行。

## 环境

- Python 3.13.13；pytest 8.3.4
- Node 24 / vitest 2.1.9
- `neo4j` 驱动未安装（可选 extra `graph`），真实 Neo4j 集成用例 skipped

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `python -m pytest tests/knowledge/test_graph_*.py tests/evaluation/knowledge/test_graph_overrides.py tests/tools/test_knowledge_tools.py tests/webui/test_knowledge_api.py -q` | **148 passed, 1 skipped** |
| `python -m pytest tests/evaluation/knowledge tests/tools/test_knowledge_tools.py tests/webui/test_knowledge_api.py -q` | **288 passed** |
| `python -m pytest tests/knowledge -q` | 482 passed, 1 skipped, **11 failed（基线）** |
| `python -m ruff check <本次改动文件>` | clean（仅 `nanobot/evaluation/knowledge/routes.py` 为既有 import 排序问题，未改动该文件） |
| `cd webui && npx tsc -p tsconfig.build.json --noEmit` | clean |
| `cd webui && npx vitest run knowledge-graph*.test.*` | **6 passed** |
| `cd webui && npx vitest run src/tests/knowledge-page.test.tsx` | **16 passed** |

### 基线失败（非本次引入）

- `tests/knowledge/storage/test_files.py` ×5：Windows chmod / symlink 语义；
- `tests/knowledge/test_service.py` ×2：Windows 文件 mode（0666 vs 0600）；
- `tests/knowledge/storage/test_extract_lines.py` ×1：文本规范化基线；
- `tests/knowledge/test_embedding.py`、`test_questions.py`、`test_rerank.py` ×3：本机 httpx 对测试 URL 的 `Invalid port: ':1]'`，相关文件未改动。

## 关键行为核对

| 不变量 | 证据 |
| --- | --- |
| 图关闭时两路数值不变 | `test_fusion.py` / `test_retriever.py` 既有用例全绿；`routes` 不含 graph |
| 图开启产出第三路与证据 | `test_graph_route.py::test_graph_route_adds_route_and_evidence` |
| 后端不可用只降级 | `test_graph_backend_failure_degrades_not_raises`、`test_graph_enabled_without_route_degrades` |
| 抽取失败不影响文档 ready | `test_graph_service.py::test_extractor_failure_does_not_fail_document` |
| 幂等重抽不重复计数 | `test_graph_repo.py::test_replace_document_graph_is_idempotent`、`test_graph_service.py::test_reprocess_is_idempotent` |
| 删文档级联 + 孤儿清理 | `test_graph_repo.py::test_delete_document_cascades_and_cleans_orphans` |
| 删文档后关系计数重算 | `test_graph_repo.py::test_cleanup_relations_removes_evidence_starved_relation` |
| 手动全量需确认 | `test_graph_api.py::test_extract_requires_confirm_for_kb_backfill` / `test_rebuild_requires_confirm` |
| 越权实体 404 | `test_graph_api.py::test_graph_entity_out_of_scope_returns_404` |
| 10 语言键集一致 | `knowledge-graph-i18n-parity.test.ts` |

## 真实环境闭环（2026-11-11）

环境：Docker `neo4j:5.26-community`（`bolt://localhost:7687`）+ `neo4j` Python 驱动 5.28.8 +
真实 MiniMax `MiniMax-M3` 抽取；隔离配置、DB 与端口（WebUI 8775 / gateway 18791），未改动用户配置。

### 1. Neo4j 集成

| 命令 | 结果 |
| --- | --- |
| `NEO4J_TEST_URI=... NEO4J_TEST_PASSWORD=... pytest tests/knowledge/test_graph_store.py -m neo4j -q` | **1 passed** |

用例校验了 1 跳只到 `e2`、2 跳到 `e3`，并校验 `chunk_ids` 随节点返回。

### 2. 真实抽取 + 镜像

脚本：[run_pipeline.py](file:///D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-11-11-graph-e2e/run_pipeline.py)

- 3 份文档均 `ready`；真实抽取分别得到 **10 实体/9 关系**、**5/4**、**2/1**；
- Neo4j 镜像 **14 节点 / 15 关系**；
- 检索 `routes = ("keyword", "graph")`，`degraded = ("vector_unavailable")`（本 E2E 主动关闭 embedding）；
- 跨块证据路径：`灰度发布流程 —负责→ 值班二号`（实体名，非 ID）。

### 3. 浏览器闭环

脚本：[verify_graph_ui.cjs](file:///D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-11-11-graph-e2e/verify_graph_ui.cjs)  
**7/7 PASS**：

1. 图谱 tab 显示真实实体；
2. 实体详情与一跳子图渲染（4 节点）；
3. 聊天请求发送并完成；
4. 回答包含“值班二号”；
5. 回答引用 `sample-rollout` / `Graph E2E` 来源；
6. 控制台无非预期 error；
7. 无非 workbench API 404。

截图：

- [graph-tab.png](file:///D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-11-11-graph-e2e/graph-tab.png)
- [graph-detail.png](file:///D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-11-11-graph-e2e/graph-detail.png)
- [chat-citation.png](file:///D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-11-11-graph-e2e/chat-citation.png)

### 4. 评测开/关图对比

脚本：[run_eval_compare.py](file:///D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-11-11-graph-e2e/run_eval_compare.py)  
数据集 4 题（expect 标注，无 judge 调用），两次运行均 `completed`：

| 指标 | 图关 | 图开 | delta |
| --- | --- | --- | --- |
| hit_at_k | 1.0 | 1.0 | 0 |
| precision_at_k | 1.0 | 1.0 | 0 |
| mrr | 1.0 | 1.0 | 0 |
| ndcg_at_k | 1.0 | 1.0 | 0 |
| recall_at_k | 1.0 | 1.0 | 0 |
| pool_coverage | 1.0 | 1.0 | 0 |
| **unscored_rate** | **0.25** | **0.0** | **-0.25** |

`regression_count = 0`。差异来自第 4 题：图关时关键词路命中的块不含期望词，记为不可评；
图开后图路带回含“回滚”的块并命中。直接核对：

```
graph_enabled=False routes=('keyword',)        hits=1 evidence=None
graph_enabled=True  routes=('keyword','graph') hits=1 evidence=(('值班二号','负责执行','回滚'),)
```

### 真实环境暴露并已修复的问题

1. `MERGE (:GraphMeta {...}) SET version = 1` 无变量名 → Cypher 语法错误；改为 `MERGE (meta:...) SET meta.version`。
2. Neo4j `execute_query()` 返回 `EagerResult`，直接迭代拿到的是 key 列表；改为取 `.records`。
3. 推理模型返回 `<think>`/Markdown 围栏导致 JSON 解析失败；抽取前剥离。
4. 本机 `NO_PROXY` 含非法 IPv6 项，`httpx.Client()` 初始化失败；抽取器增加直连回退。
5. 历史引用注入调用不存在的 `SessionManager.read`；改为兼容 `read()` / `read_session_snapshot()`。
6. 会话进行中未落盘时 workbench 轮询 404；增加「快照 → 进程缓存」回退。

### 5. 复杂文档图谱（连线验证）

脚本：[run_complex_pipeline.py](file:///D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-11-11-graph-e2e/run_complex_pipeline.py)

6 份互相引用的文档（发布流程 / 值班 / 监控 / 复盘 / 支付链路 / 支付值班），真实抽取结果：

| 项 | 结果 |
| --- | --- |
| 实体 / 关系 | **27 / 33** |
| 文档 | 6 份全部 ready |
| 值班二号子图 | 8 节点 / **7 边** |
| 支付服务子图 | 4 节点 / 3 边 |
| 灰度发布流程子图 | 2 节点 / 1 边 |

浏览器实测（[verify_complex_graph.cjs](file:///D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-11-11-graph-e2e/verify_complex_graph.cjs)）：

```
SUBGRAPH API nodes=8 edges=7
COMPLEX nodes=8 graphLines=7
```

截图：[graph-complex.png](file:///D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-11-11-graph-e2e/graph-complex.png)

**连线问题修复**：此前 `<line stroke="var(--border, ...)">` 作为 SVG 属性被浏览器解析为
`stroke: none`，所以节点有、线不显示。改为 `className="text-muted-foreground"` +
`stroke="currentColor"` + `strokeOpacity=0.55`，并增加基于节点包围盒的 viewBox 自适应。

### 6. 公版短篇小说《孔乙己》

来源：维基文库（公版）。正文 2,616 字、15 段，入 1 份文档、约 4 个块。  
脚本：[run_novel_pipeline.py](file:///D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-11-11-graph-e2e/run_novel_pipeline.py)

| 项 | 结果 |
| --- | --- |
| 实体 / 关系 | **28 / 35** |
| 文档 | ready |
| 中心实体「孔乙己」子图 | **15 节点 / 15 边** |
| 其他高连接实体 | 掌柜 4 节点/3 边；短衣帮 3/2；茴香豆 3/2 |

首轮 30s 超时导致 2 个块被跳过；把临时抽取超时调到 120s 后重跑，实体从 12 增至 28、关系从 11 增至 35。

浏览器实测：

```
NOVEL 1hop center=孔乙己 nodes=15 lines=18
NOVEL 2hop nodes=18 lines=31
```

**诱导子图修复**：此前只返回「中心 → 邻居」的路径边，邻居之间即使有关系也不画，视觉上像一只星形图。
`GraphStore.expand` 新增 `include_inner_edges`：子图查询会额外返回选中节点之间的边；契约/检索路径保持不变（默认关闭）。
UI 增加「1 跳 / 2 跳」切换，2 跳会把更远实体及它们之间的边一起带回。

截图：

- [novel-graph.png](file:///D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-11-11-graph-e2e/novel-graph.png)（1 跳，15 节点/18 边）
- [novel-graph-2hop.png](file:///D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-11-11-graph-e2e/novel-graph-2hop.png)（2 跳，18 节点/31 边）
- [novel-graph-page.png](file:///D:/studyspace/project/Zhixu/.ai-runtime-artifacts/verifications/2026-11-11-graph-e2e/novel-graph-page.png)

### 已知遗留（非图谱阻塞）

- WebUI 仍会偶发一次 `/api/sessions/<key>/workbench` 404：请求的会话键与 agent 活跃键不同源；
  页面有兜底，不影响图谱与聊天。属既有会话工作台问题。
- 评测中 `vector_unavailable` 是本次刻意关闭 embedding（隔离图路影响），不是后端故障；
  真实向量 + 图的三路融合需在有 embedding 模型的环境另行验证。
