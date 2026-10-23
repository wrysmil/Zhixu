---
artifact: verification
route: Tier 1 Leader 直做 -> verification-before-completion
skills: [verification-before-completion]
skills_evidence:
  - /Users/mima0000/.agents/skills/verification-before-completion/SKILL.md
source:
  - webui/src/components/thread/AgentActivityCluster.tsx
  - webui/src/components/thread/activity/ThinkingReasoningShell.tsx
  - webui/src/components/thread/SubagentExecutionActivity.tsx
  - .ai-runtime-artifacts/specs/2026-10-18-subagent-observability-spec.md
created_at: 2026-10-18
status: page-verified
tier: 1
---

# 子 Agent 折叠态入口：前端页面验证

## 结论

用户反馈「主聊天没有展现子 Agent 的样式/标识」。真机核对确认：入口本身存在，
但只渲染在可折叠的活动簇内部；回合结束后活动簇默认折叠，主聊天只剩 `Worked for 8s`，
因此看不到任何「子 Agent」标识。这违反 spec §3「若现有工具组折叠，在组标题保留
『2 个子任务运行中』的入口」。本轮把入口补到活动簇标题，折叠状态下同样可见可点。

## 改动

- `webui/src/components/thread/activity/ThinkingReasoningShell.tsx`：新增 `headerAccessory`，
  标题行改为「标题 + 折叠箭头 + 入口」，入口是折叠按钮的兄弟节点（不嵌套 button）。
- `webui/src/components/thread/AgentActivityCluster.tsx`：新增 `collectSubagentItems()`，
  与簇内 `ActivityTraceList` 使用同一份 trace/状态口径、按 `tool_call_id` 关联（不猜标题）；
  以 `headerAccessory` 渲染 `SubagentExecutionGroupEntry`。
- `webui/src/components/thread/SubagentExecutionActivity.tsx`：新增 `SubagentExecutionGroupEntry`，
  聚合显示「子 Agent ×N · 状态」，点击打开对应调用详情；`capability=false` 或解析不到调用时返回 `null`。
- i18n：新增 `thread.subagentExecution.groupEntry`，10 个语言包键集对齐。
- 测试：`webui/src/tests/subagent-execution-panel.test.tsx` 新增 3 条折叠态用例。

## 证据（本轮实跑）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 定向单测 | `vitest run src/tests/subagent-execution-panel.test.tsx` | 12/12 通过 |
| 定向单测 | `vitest run src/tests/i18n.test.tsx src/tests/right-surface.test.tsx` | 31/31 通过 |
| 定向单测 | `vitest run src/tests/thread-shell.test.tsx` | 74/74 通过 |
| 类型 | `tsc -p tsconfig.build.json --noEmit` | 退出 0 |
| Lint | `eslint --config webui/eslint.config.js webui/src --max-warnings 0` | 退出 0 |
| 构建 | `vite build`（输出 `nanobot/web/dist`） | 退出 0，仅既存 >500kB chunk 提示 |

真机页面（`http://127.0.0.1:8765`，真实会话 `websocket:1f6f72be-f972-4f1e-9418-dd438f2420c2`，
invocation `c8f59ca6-31b9-47e6-afffc-513ba57ed646`）：

- 折叠态标题显示 `Sub-agent ×1 · Done`，与 `Worked for 8s` 同一行、可点击。
- 点击后右侧打开详情，TASK / EXECUTION / SUB-AGENT RESULT 三段齐全。
- 键盘路径：入口 `Enter` 打开详情，`Escape` 关闭并把焦点交回入口
  （`document.activeElement` 的 `data-testid` = `subagent-execution-group-entry`）。
- 控制台 error/warning：0。

截图：`2026-10-18-subagent-frontend-test/04-subagent-group-entry.png`、
`2026-10-18-subagent-frontend-test/05-group-entry-opens-panel.png`。

## 未覆盖

- 按用户要求**未跑前端全量回归**（上一轮全量结论见本目录其他产物）。
- 多子 Agent 并发（N≥2）的聚合文案与「打开哪一个」只有单测覆盖，未做真机多任务。
- 360/768/1024/1440 视口矩阵、200% 缩放、屏幕阅读器未做。

## 追加：实时运行态实测（2026-10-18 17:10，Leader 用 browser-use 真机）

对象：运行中的 gateway `http://127.0.0.1:8765`（PID 32112，16:38 启动），serves `nanobot/web/dist`（17:01 构建，晚于最后源码改动 17:00）。

### 后端接线复核（只读）

- `loop.py:504-523` 已在生产路径把 `execution_ledger` + `execution_event_sink` 注入 `SubagentManager`，
  `_close_execution_ledger` 挂到关闭路径 → dispatch 跟踪「集成缺口 #1/#2/#3」已收口。
- bootstrap `runtime_capabilities` 返回 `subagentExecutionEventsV1: true`。
- 只读三路由用 `Authorization: Bearer <api_token>` 实测 200：
  list（2 条 succeeded，mode=background）、detail（含 `activities`/`result.summary`）、
  events（d9b25d5d 36 事件：1 created + 1 started + 8 phase_changed + 25 activity + 1 finished）。
- ledger 落盘 `~/.nanobot/workspace/runtime/agent-executions.db`。

### 浏览器交互（视口 531×593 → 抽屉路径）

- 折叠态活动簇标题行可见可点入口：`子 Agent · list_dir 计数工作区文件 · Done`、
  `资料研究 · 资料核对 · Done`（DOM `data-testid` = `subagent-execution-entry`）。
- 点击打开右侧详情抽屉：banner（Sub-agent execution / 标题 / Done / Reload / Close）+
  任务切换菜单 + TASK + EXECUTION（逐条工具活动，含 Failed/Done 状态）+ SUB-AGENT RESULT（完整结果）。

### 实时生命周期（新发一条 wait=false 后台派发，invocation `d3065d7e`）

- 主 Agent 立即回复「已派发，并且没有等待它跑完」+ invocation id，**未阻塞**。
- 入口状态经 WS 实时翻转：`子 Agent · 读取五个配置文件并概括 · Running` → `· Done`（两次快照间隔约 4s 捕获）。
- 子 Agent 跑完结果异步回传主聊天（「五个文件都已读到…共调用 read_file 五次，无失败」）。
- 详情面板 EXECUTION 实时显示 5 条 `read_file` 活动全部 `Done`，标题栏 `Done · 6 sec`。

### 唯一缺陷（非阻断）

- 抽屉打开时控制台 1 条 `aria-hidden` 警告：焦点仍留在主聊天 textarea，Radix 给 `#root` 设
  `aria-hidden` 后浏览器告警（应把焦点移入 dialog）。无 error。建议后续用 `inert` 或打开时聚焦抽屉。

### 结论

用户关切「子 Agent 阻塞式等待、前端看不到状态」→ 实测确认：`wait=false` 后台派发不阻塞主回合，
前端能实时查看子 Agent 状态（Running/Done）、工具活动与最终结果；`wait=true` 才会阻塞当前回合。

## 附带发现（待用户确认）

用户提供的截图含「和 Codex 一起工作」「用时 1m 37s」「已读取…技能」「…开始工作」、
右侧「完成 · 2」列表等文案，这些字符串在知序源码、`nanobot/web/dist` 构建产物、
git 历史中都查不到；知序真实页面是「侧栏 + Workbench」布局且标题行为 `Worked for 8s`。
据此判断该截图是 Codex 桌面端（`/Applications/ChatGPT.app`）界面，不是知序 WebUI。
若用户在测知序，需先确认打开的是哪个应用/哪份构建。
