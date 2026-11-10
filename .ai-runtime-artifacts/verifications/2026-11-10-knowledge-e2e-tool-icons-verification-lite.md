---
artifact: verification-lite
route: Tier 1 Leader 直做
task: 知识库上传→入库→会话检索回答 + 知识工具文案/图标
date: 2026-11-10
status: passed
---

# 知识库端到端与工具图标验证

## 范围

1. 通过 WebUI 走完「新建知识库 → 上传文档 → 入库就绪」。
2. 在会话里让 AI 调用 `knowledge_search` / `knowledge_read` 并回答。
3. 修复：左侧「知识库 / 检索评测台」导航点击无反应。
4. 修复：工具活动行在中文界面显示英文（如 `Knowledge search`）。
5. 更换知识工具与其它工具的图标，并放大活动行徽标。

## 变更

| 文件 | 说明 |
| --- | --- |
| `webui/src/components/Sidebar.tsx` | `switch` 补 `knowledge` / `knowledge-eval` 分支，新增两个回调 props |
| `webui/src/App.tsx` | 新增 `onOpenKnowledge` / `onOpenKnowledgeEval` 并传入 Sidebar |
| `webui/src/components/thread/activity/generic-tool-model.ts` | 新增 `knowledge` family；中文界面下所有活动行文案与 aside 中文化；`knowledge_read` 读取 `handle` |
| `webui/src/components/thread/activity/GenericToolRun.tsx` | 按工具名/家族映射清晰图标：知识检索 `LibraryBig`、扩读 `BookOpenCheck`、内容搜索 `TextSearch`、查找文件 `FolderSearch`、目录 `FolderTree`、读取 `FileText`、记忆 `Brain`、子 Agent `Bot`、图片 `Image`、消息 `MessageSquare`、设置 `Settings2`、定时 `CalendarClock`、长任务 `Target`、终端 `Terminal`、截图 `Camera`、兜底 `Wrench` |
| `webui/src/components/thread/activity/ActivityStep.tsx` | 活动行徽标 14px→16px，图标 10px→12px |
| `webui/src/tests/generic-tool-model.test.ts` | 新增知识检索/扩读文案、中文文案、分组回归（36 passed） |

## 证据

### 端到端

- 知识库：新建全局库 → 上传 `kb-doc.md`（约 1.2 KB）→ 文档状态「就绪」。
- 会话问题：`请先调用 knowledge_search 工具检索知识库，再只依据检索结果回答：知序的回滚流程分几步？负责人是谁？`
- 实际工具行：
  - `检索知识库 · 已检索知识库 · 2 次知识调用`
  - `扩读知识 · 已扩读原文 chunk:/…dacc`
- 回答包含「三步」「冻结写入」「墨白」，并标注来源《青鸾发布手册》。
- 截图：`verifications/knowledge-e2e/02-knowledge-ready.png`、`06-tool-rows-expanded.png`
- 原始结果：`verifications/knowledge-e2e/summary.json`、`ui-summary.json`

### 导航修复

Playwright 实测左侧点击：

- `知识库` → `#/knowledge`（修复前无跳转）
- `检索评测台` → `#/knowledge-eval`（修复前无跳转）

### 构建与测试

| 命令 | 结果 |
| --- | --- |
| `cd webui && npm run build`（`tsc -p tsconfig.build.json && vite build`） | 通过 |
| `vitest run src/tests/generic-tool-model.test.ts` | 36 passed |
| `vitest run src/tests/workspace-navigation.test.tsx` | 21 passed |
| `vitest run src/tests/knowledge-page.test.tsx` | 14 passed |
| `vitest run src/tests/agent-activity-cluster.test.tsx` | 57 passed |
| `vitest run src/tests/thread-messages.test.tsx src/tests/preview-thread.test.tsx` | 48 passed |

### 清理

测试知识库、文档、分块、测试会话均已删除；`state.db` 计数 `knowledge_bases=0 / documents=0 / document_chunks=0`，`knowledge/uploads` 为空。

## 自主调用知识工具的提示词修复（第二轮）

初测发现：不点名工具时模型会先选 `spawn` / `grep`。原因是系统提示词只教了「缺内容用 grep/read_file」，从未提过 `knowledge_search`；工具选择顺序里也没有知识库。已修复：

| 位置 | 改动 |
| --- | --- |
| `nanobot/templates/AGENT.md` | 第一铁律新增「内部资料 → 先 knowledge_search」；第二铁律改为「内部资料先查知识库」；新增「知识库优先（Knowledge First）」小节；工具选择顺序把知识检索排到第 1 |
| `workspace/identity/AGENT.md` | 同步以上改动（当前运行实例实际注入的副本） |
| `nanobot/agent/tools/knowledge_search.py` | 工具描述新增「涉及项目/产品/团队/上传文档时优先调用，不要先用 grep/read_file/spawn」 |
| `tests/tools/test_knowledge_tools.py` | 断言工具描述包含优先调用约束 |

回归（**不点名工具**，问题「知序的回滚流程分几步？负责人是谁？」）连跑两次：

| 轮次 | 首个工具 | 回答关键事实 |
| --- | --- | --- |
| 第 1 次 | `检索知识库`（2 次调用） | 通过 |
| 第 2 次 | `检索知识库`（2 次调用） | 通过 |

模型推理中明确引用了新规则「Per AGENT.md, knowledge-first」以及「不要在还没检索知识库前就派子 Agent」。

证据：`verifications/knowledge-e2e/prompt-first-tool.png`、`prompt-summary.json`、`prompt-body.txt`。

## 工作台「能力 → 知识库」联动（最小闭环）

| 层 | 改动 |
| --- | --- |
| 后端 | `nanobot/webui/session_workbench.py` 新增 `_knowledge_activity`：从最近 500 条消息读取 `knowledge_search` / `knowledge_read`，解析检索结果 `<result>` 的 `doc / kb / title / chunk / heading / page / score`，输出 `calls / reads / empty / failed / queries / sources` |
| 前端 | `session-workbench-api.ts` 增加 `knowledge` 字段与形状校验；`workbench-context.tsx` 新增 `WorkbenchKnowledge`，在「能力」页展示本会话检索次数、最近查询、命中来源与「打开知识库」入口；点击入口跳转 `#/knowledge` |
| 透传 | `ThreadShell` / `App` 透传 `onOpenKnowledge` |

验证：真实会话（1 次检索 + 1 次扩读 + 4 个命中片段）在「能力」页显示：

```
本会话检索 1 次 · 扩读 1 次 · 命中 4 个片段
最近查询：知序 生产回滚流程 步骤 负责人
来源：kb-doc — 工作台联动测试 · 青鸾发布手册 > 生产回滚流程
```

「打开知识库」点击后 URL 变为 `#/knowledge`。证据：`verifications/knowledge-e2e/workbench-knowledge.png`、`workbench-summary.json`。

> 工作台在空闲时按 30s 轮询；回合刚结束时如需立即看到最新检索，点工作台右上角刷新按钮（实机验证时快照曾在回合结束前取到旧值）。

测试：`pytest tests/webui/test_session_workbench.py` 14 passed；`vitest run src/tests/session-workbench-live.test.tsx src/tests/session-workbench.test.tsx src/tests/workbench-memories.test.tsx` 14 passed；`npm run build` 通过。

## knowledge_search 查询改写（v2）

按检索原理文章口径：`rewrite_query` 是查询理解模型输出的**字符串字段**，检索直接用它；工具内部不再调用模型。

| 层 | 改动 |
| --- | --- |
| 工具 schema | `knowledge_search` 新增可选 `rewrite_query: string`；要求补全指代但保留专名、型号、错误码、条款号、时间，禁止元指令；为空则用 `query` |
| 检索 | 优先用 `rewrite_query`；改写查询无命中时回退原查询重搜；工具结果带 `<notice kind="query_rewrite">` |
| AGENT.md | 知识库优先第 6 条：调用前生成 `rewrite_query`，保留检索词，不要把具体实体泛化 |
| 单测 | `tests/tools/test_knowledge_tools.py` 46 passed（含字符串改写、空值回退、无命中回退） |

实机工具参数与结果：

```
{"query": "它分几步？", "rewrite_query": "知序 生产回滚流程 分几步 青鸾", "limit": 6}
检索查询使用改写「知序 生产回滚流程 分几步 青鸾」
```

证据：`verifications/knowledge-e2e/rewrite-v2-notice.txt`、`rewrite-v2-chat.png`、`rewrite-v2-summary.json`。

> 文章里的查询理解还包含 `intent` 与 `image_description`；本轮只按用户要求实现 `rewrite_query`，未引入意图门控与图片描述。

## WeKnora 口径对齐修正（2026-11-10）

逐项审查见 [reviews/2026-11-10-knowledge-weknora-alignment.md](../reviews/2026-11-10-knowledge-weknora-alignment.md)。本轮修正：

- 去重从自定的 `0.98 包含率 / 0.6 Jaccard / 长度比 5` 收敛为 WeKnora 口径：长度比 ≤3、短文本子串、token 覆盖 ≥0.85 且独有 ≤2%；`dedup_jaccard` 保留为兼容字段但不再参与判定
- MMR token 改为 `heading_path + content`（enriched passage），不再是纯正文
- `knowledge_search.rewrite_query` 改为字符串参数，工具内不再调用模型
- `AGENT.md` 增加「改写保留检索词」与「先判断意图再检索」

验证：`tests/knowledge/test_fusion.py + test_config.py` 70 passed；`test_retriever.py + test_retriever_with.py + test_golden_queries.py` 42 passed；`test_knowledge_tools.py` 46 passed。`tests/knowledge` 全量 344 passed、8 个 Windows chmod/symlink 基线用例失败（与本次无关）。

## 查询理解（WeKnora §2）

按文章口径把「查询理解」的三个输出落成 `knowledge_search` 的参数契约：

- `rewrite_query`（字符串）：检索直接用它；要求补全指代但保留专名/型号/错误码/条款号/时间，禁止元指令；为空回退 `query`
- `intent`（意图门控）：`greeting|chitchat|follow_up|summarize|image_only|doc_only` 不检索并回明原因；`kb_search|clarification|留空|未知` 都检索（未知默认检索，宁可多检一次）
- `image_description`：接收但忽略（纯文本知识库）

`AGENT.md` 第 7 条同步要求把判定结果写进 `intent`。

测试：`tests/tools/test_knowledge_tools.py` 49 passed（新增意图门控、空/未知意图仍检索、image_description 忽略）。

## 知识精排（WeKnora §7）

- 新增 `nanobot/knowledge/rerank.py`：`Reranker` 协议、cohere 兼容 HTTP 客户端（分批 ≤32、并发 ≤4、logit→logistic、密钥脱敏）、`apply_threshold` 三档降级、`composite_score`（0.6×model + 0.3×base + 0.1×source）
- 检索侧：融合+去重 → 截断 200 → passage（标题+面包屑+正文）→ 精排 → 阈值降级 → 复合分 → MMR；失败一律退化为召回顺序（no_model / model_error）
- 配置：`knowledge.rerank`（`enabled` 默认 false；`baseUrl` / `model` / `threshold` / `fallbackMinScore` / 权重 / `scoreTransform`）
- 工具结果头带 `rerank="<outcome>"`；精排全拒与「库里没有」区分开

实机：本地 mock 精排服务 + `nanobot knowledge search` 输出 `mode=hybrid routes=vector,keyword rerank=ok degraded=-`；配置已还原、测试库已删除。

**接真实模型**（`BAAI/bge-reranker-base`，本地 sentence-transformers，CPU）：

- 同一查询「回滚流程怎么执行」：不精排 top1 是「备份策略」（答非所问），开精排后 top1 是「生产回滚流程」
- WebUI 会话：工具参数带 `rewrite_query` / `intent="kb_search"`，结果头 `rerank="ok"`，hit1 = 生产回滚流程（0.837）
- 实例配置已启用 `knowledge.rerank`（backend=local）；模型缓存在 HF 缓存（约 1.1GB），gateway 常驻只加载一次，CLI 每次调用重新加载（首次约 15s）
- 证据：`verifications/knowledge-e2e/rerank-local-model.txt`、`rerank-chat.png`、`rerank-summary.json`

测试：`tests/knowledge/test_rerank.py` 18 passed；`tests/knowledge + tests/tools/test_knowledge_tools.py + tests/evaluation/knowledge` 591 passed（8 个 Windows chmod/symlink/换行基线用例失败，与本次无关）。

## 文档分块预览

文档行动作区新增「查看分块」：复用既有 `GET /api/settings/knowledge/doc?id=...&chunkLimit=20`，弹窗展示块序号、标题路径、页码、字符数、`chunk://` 句柄与正文；超过 20 块时页脚提示「共 N 块 · 显示前 20 块」。失败展示错误且不关闭弹窗，点击遮罩或「关闭」退出。

实机验证：上传 4 块文档后，弹窗显示 4 个块、正文与统计 `共 4 块`，关闭正常。证据：`verifications/knowledge-e2e/chunk-preview.png`、`chunk-summary.json`。

测试：`vitest run src/tests/knowledge-page.test.tsx` 16 passed（含预览成功与加载失败两条）；`npm run build` 通过；10 语言补齐 `viewChunks / chunksSummary / chunksShowing / chunksEmpty / chunksLoadError` 文案。

## 发现的既有行为（未改动）

- **短且标题密集的文档会被分块校验拒绝**：428 字、4 个小节的 Markdown 会得到 4 个短块，触发既有规则 `KNOWLEDGE_CHUNK_INVALID: too many small chunks`（`chunk_size=800`、≥4 块、非末块中 <50 字的块超过 1/4）。这是 `nanobot/knowledge/chunker.py::validate_chunks` 的现有设计，需要单独决策是否放宽；本次用正常长度文档验证主链路。
