---
artifact: verification
route: 功能实现 -> @ 候选全量展示 + 默认主 Agent 醒目标识
skills: [brainstorming, test-driven-development, verification-before-completion]
source:
  - webui/src/components/thread/ThreadComposer.tsx
  - webui/src/components/settings/agents/AgentsView.tsx
  - webui/src/components/settings/agents/AgentRow.tsx
  - webui/src/components/settings/agents/AgentCard.tsx
  - webui/src/components/settings/agents/AgentTreeView.tsx
  - webui/src/components/settings/agents/AgentEditorDialog.tsx
  - webui/src/components/settings/agents/shared.tsx
  - webui/src/lib/agents/types.ts
created_at: 2026-10-18
status: verified
tier: 1
---

# @ 候选全量展示 + 「指挥官」默认主 Agent 醒目标识

承接 `2026-10-18-agent-mention-verification.md`。本轮只处理用户反馈的两点：
@ 面板没有展示全部 Agent、默认主 Agent（指挥官）没有被明确标识。

## 结论

1. `@` 面板不再截断 Agent：Agent 组按可见数量全量入列，会话/CLI/MCP 保留原预留额度。
2. 指挥官在 @ 面板、Agent 管理（列表/卡片/树）、编辑器标题与预览栏均以
   「默认主 Agent」徽章 + 置顶 + 琥珀高亮三重方式标识。
3. 顺带修掉一个既有缺陷：树视图在没有任何显式子 Agent 配置时整页显示「没有可显示的 Agent」。

## 改动要点

### @ 候选预算（`ThreadComposer.tsx`）

原逻辑总预算 8，Agent 只预留 3，因此 10 个可见 Agent 被截断为 3 个。
现改为 Agent 组按 `agentCandidates.length` 全量占用，session/cli/mcp 仍按 3/1/1 预留，
剩余名额依次回补给 session→cli→mcp。Agent 数 ≤8 时行为与旧逻辑一致。

### 默认主 Agent 标识

- `lib/agents/types.ts`：新增 `isDefaultMainAgent(agent)`，唯一判据为
  `agent.id === DEFAULT_MAIN_AGENT_ID`（后端 `nanobot/agents/catalog.py` 同名常量）。
- `settings/agents/shared.tsx`：新增 `DefaultMainAgentBadge`（Star 图标 + warn 色徽章）。
- 徽章文案 i18n key：`settings.agents.basics.defaultMainAgent`、
  `thread.composer.mentions.defaultAgentBadge`，10 个语言全部补齐（纯新增 5 行/文件）。
- `AgentsView.tsx`：`filtered` 在筛选后以默认档案优先排序，列表/卡片/树三种视图共用同一顺序。
- `AgentRow.tsx` / `AgentCard.tsx` / `AgentTreeView.tsx`：默认档案整行/整卡加琥珀底色与 ring，
  名称前部插入徽章。
- `AgentEditorDialog.tsx`：非新建态下标题旁显示徽章；右侧 `AgentRailSummary` 复用行/卡组件，随之显示。

### 树视图层级判定（既有缺陷）

`AgentTreeView` 用 `resolveSelection(agent.subAgents, allIds)` 判定父子关系，
而 `mode: "all"` 会解析为「全部 id」。由于所有预设档案的 subAgents 默认都是 `all`，
每个 Agent 都被判为别人的子节点 → `roots` 为空 → 整页「没有可显示的 Agent」。
现改为只有显式 `include` 才建立层级（`all` / `exclude` 表达的是能力范围，不是组织结构）。

## 证据（本轮实跑）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| TypeScript | `webui/node_modules/.bin/tsc -p tsconfig.build.json` | 通过 |
| 前端全量 | `webui/node_modules/.bin/vitest run` | 1778 passed / 2 skipped（141 文件） |
| 前端构建 | `webui/node_modules/.bin/vite build` | 通过 |
| 后端聚焦 | `uv run --no-sync pytest tests/webui/test_agent_selection.py tests/agent/test_turn_agent_profile.py tests/webui/test_agents_wiring.py tests/webui/test_agents_runtime.py` | 66 passed |

新增/涉及用例：

- `thread-composer.test.tsx` > `lists every visible agent in the mention palette`
  （10 个可见 Agent 全列出、commander 首位且 accessible name 含 default agent）
- `agents-view-integration.test.tsx` > `pins the default agent first and marks it`
- `agents-view-integration.test.tsx` > `shows every agent in the tree view when no sub-agent hierarchy is configured`
  （修复前该用例失败：`Unable to find an element with the text: 指挥官`）

## 浏览器端到端（http://127.0.0.1:8765，本轮实测截图）

1. 首页输入 `@`：Mentions 面板 Agent 组列出全部 10 个 Agent，「指挥官 @commander」首位且默认选中，
   徽章为实心琥珀「默认主 Agent」+ Star；其余 Agent 为浅色「智能体」徽章；
   会话/CLI/MCP 组在其后，面板内层 `overflow-y-auto` 可滚动到全部条目。
2. `#/agents` 列表视图：指挥官排第一，行内「默认主 Agent」徽章在「系统预设」之前，整行琥珀底色。
3. 卡片视图：指挥官卡片排首位，边框与底色为琥珀高亮，徽章在徽章行首位。
4. 树视图：修复后 10 个 Agent 全部列出，指挥官首行带徽章与高亮（修复前为「没有可显示的 Agent」）。
5. 编辑器：标题「编辑 · 指挥官」旁显示「默认主 Agent」徽章，右侧配置摘要栏同样显示。
6. 语言切换：英文态显示 `Default agent`，中文态显示 `默认主 Agent`，其余语言走各自新补 key。

## 复验注意

`nanobot/web/dist` 由 `vite build` 覆盖，gateway 直接读取该目录；
浏览器仅改 hash（`#/agents`）不会重新拉取文档，需 `location.reload()` 才会加载新构建产物。
本轮首次复验即因未刷新看到旧行为。
