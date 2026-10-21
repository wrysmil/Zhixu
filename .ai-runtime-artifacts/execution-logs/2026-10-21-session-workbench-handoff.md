---
artifact: handoff
route: orchestration:dispatcher-workflow
skills:
  - orchestration
source:
  - .ai-runtime-artifacts/execution-logs/2026-10-21-session-workbench-execution-log.md
  - .ai-runtime-artifacts/contracts/2026-10-21-contract-session-workbench.md
created_at: 2026-10-21
platform: qoder
---

# HANDOFF — 会话右侧工作台接入

> 会话级交接。实现已整合，验证被用户中断后直接提交；下一个 Agent 从「验证状态」继续。

## 中断点

- 最后完成：WU-01（布局）、WU-02（后端）、WU-03（侧栏）代码已整合到主 checkout。
- 当前状态：代码完成、**未跑**定向测试与构建；交接后直接 commit + push。
- 下一步：按下方命令补验证；通过后更新 execution-log 状态并收口 review。

## 已做决策

- 工作台是**只读投影**：`session_workbench_payload()` 只读 `session.metadata`、gateway 注入的 services 与会话消息，不创建 store、不调用模型。
- 数据源不可用时降级为 `limitations`，单个来源失败不拖垮整个接口（skills / mcp 各自 try）。
- 前端每 15s 超时；有运行中任务时 10s 轮询，空闲 30s；页面隐藏或 WS 未连接时停轮询。
- 前端对响应做 schema 校验，格式不符直接报「工作台返回的数据格式不正确」。
- 拖拽统一收敛到 `ResizeHandle`：工具坞、工作台、文件预览、文件树共用，支持键盘方向键。
- 文件预览拖宽时关闭 width 过渡，并把横向滚动归零，避免拖宽后内容停在旧偏移。
- 窄屏（<768px 或 shell <680px）不再塞侧栏，工作台改为弹层入口。
- 运行时目录**不纳入版本控制**：`teams/`、`writing/`（SQLite）、`desktop/.app-profile/`、`nanobot-ui-check/`、各类 `*vite-cache/`。

## 待完成 WU

| WU | 状态 | 文件 | 阻塞 |
| --- | --- | --- | --- |
| WU-01 布局 | completed | ThreadShell / PaneWorkbench / FilePreviewPanel / Dock* | — |
| WU-02 后端 | completed | nanobot/webui/session_workbench.py / ws_http.py / gateway_services.py | — |
| WU-03 侧栏 | completed | webui/src/components/workbench/* | — |
| 尾盘 | in_progress | 验证 + review | 需跑测试与构建 |

## Git 沙箱

- 本会话：主 checkout，未新建 worktree。
- 分支：`feature/desktop-app`
- base：`3395681`（`feat(webui): 字体对齐 Qoder，默认浅色并收紧字号`）
- 上一轮隔离目录（如仍存在）：`C:/Users/Huangqh/.codex/worktrees/session-workbench/Zhixu`

## 交付摘要

### 后端

- `nanobot/webui/session_workbench.py`：把会话投影为
  `goal / tasks / plan / agents / memories / skills / mcp / limitations`；
  Skills 使用情况由 `read_file` 成功读取 `SKILL.md` 推断，MCP 调用从消息 `tool_calls` 统计。
- `nanobot/agent/tools/update_plan.py`：新增 `update_plan` 工具，计划写入
  `session.metadata["workbench_plan"]`，最多 50 步，状态限定
  `pending / in_progress / completed / blocked / cancelled`。
- `nanobot/webui/ws_http.py`：`GET /api/sessions/{key}/workbench`，鉴权、key 解码、
  session 归属校验；读写走 `asyncio.to_thread`。
- `nanobot/webui/gateway_services.py`：注入 `subagent_execution_operations`。

### 前端

- `session-workbench-api.ts`：契约类型 + 运行时校验。
- `use-session-workbench.ts`：轮询、可见性、WS 事件刷新、请求合并与 abort。
- `SessionWorkbench.tsx`：进展 / 记忆 / 能力三视图，键盘左右切换。
- `workbench-progress.tsx`：任务、计划、子 Agent。
- `workbench-context.tsx`：工作记忆 / 长期记忆、Skills 与 MCP 状态、limitations。
- `ResizableRail.tsx` + `ResizeHandle.tsx`：右锚定拖拽列，含 ARIA separator 与键盘操作。
- `ThreadShell.tsx`：窄屏弹层入口；把主列右边界上报给 composer，避免展开侧栏时输入框横移。
- `PaneWorkbench.tsx`：画布背景透明，渐变收口到外壳；按 pane 记录 composer 布局。
- 文件预览 / 工具坞 / 文件树统一使用新拖拽手柄。

## 关键文件

- contract：`.ai-runtime-artifacts/contracts/2026-10-21-contract-session-workbench.md`
- execution-log：`.ai-runtime-artifacts/execution-logs/2026-10-21-session-workbench-execution-log.md`
- track：`.ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-10-21-session-workbench.md`
- 审计：`.ai-runtime-artifacts/research/2026-10-21-session-workbench-audit.md`
- Demo：`.ai-runtime-artifacts/research/2026-10-21-workbench-demo/`
- 验证记录（Demo 阶段）：`.ai-runtime-artifacts/verifications/2026-10-21-session-workbench-verification-lite.md`

## 验证状态

已跑：

- `git diff --check` 通过。
- 上一轮 Demo 阶段做过浏览器交互检查；**不代表**本次接入已验证。

未跑（用户中断，下个 Agent 补）：

```bash
cd webui
./node_modules/.bin/vitest run \
  tests/session-workbench-live.test.tsx tests/pane-workbench.test.tsx \
  tests/start-dock.test.tsx tests/file-preview-panel.test.tsx \
  tests/dock-file-tab.test.tsx tests/thread-shell.test.tsx

cd ..
uv run --no-sync python -m pytest tests/webui/test_session_workbench.py -q

cd webui && npm run build
```

## 给下一个 Agent 的指令

```text
从 HANDOFF 恢复「会话右侧工作台接入」：先跑上方三条验证命令，修复失败项；
再在真实 GUI 里检查工作台三视图、工具坞拖宽、文件预览拖宽、窄屏弹层；
通过后更新 execution-log 为 completed，并补 review 产物。
不要提交 teams/、writing/、desktop/.app-profile/、*vite-cache/、nanobot-ui-check/。
```
