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

## 发现的既有行为（未改动）

- **短且标题密集的文档会被分块校验拒绝**：428 字、4 个小节的 Markdown 会得到 4 个短块，触发既有规则 `KNOWLEDGE_CHUNK_INVALID: too many small chunks`（`chunk_size=800`、≥4 块、非末块中 <50 字的块超过 1/4）。这是 `nanobot/knowledge/chunker.py::validate_chunks` 的现有设计，需要单独决策是否放宽；本次用正常长度文档验证主链路。
