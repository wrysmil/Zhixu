---
artifact: dispatch-tracking
route: orchestration.dispatch
source:
  - .ai-runtime-artifacts/plans/2026-10-18-subagent-observability-plan.md
  - .ai-runtime-artifacts/plans/2026-10-18-subagent-observability-dispatch.md
created_at: 2026-10-18
updated_at: 2026-10-18
status: active
---

# 子 Agent 执行详情调度跟踪

## WORKTREE-INIT

- worktree: `/Users/mima0000/.codex/worktrees/subagent-observability/zhixu`（空壳，只遗留一份未跟踪测试草稿，未继续使用）
- 实际落盘位置：主工作区 `/Users/mima0000/Documents/学习-001/do-project/zhixu`
- branch: `feature/desktop-app` @ `9d1e869`
- 主工作区已有的 `memory/state.db` 与 `.ai-runtime-artifacts/` 未提交内容未纳入任何代码提交。

## 工作单元

| WU | 状态 | 负责人 | 依赖 | 证据 |
| --- | --- | --- | --- | --- |
| WU-01 ledger | GREEN | executor | 无 | `nanobot/subagent_executions/**`；`tests/subagent_executions/**` 3 文件 |
| WU-02 runtime | GREEN | executor | WU-01 | `subagent.py`/`spawn.py`/`execution.py`；`tests/webui/test_subagent_execution_runtime.py` |
| WU-03 gateway | 进行中 | executor(Huygens) | WU-01、02 | HTTP 只读三路由 + WS 帧 + 运行时注入 |
| WU-04 frontend state | GREEN | executor(Ptolemy) | wire 契约 | `webui/src/features/subagent-execution/**`；3 测试文件 14/14 |
| WU-05 UI | 进行中 | executor(Lovelace) | WU-04 | 聊天入口 + 右栏/抽屉 + 任务菜单 |
| 独立代码审查 | 进行中 | code-reviewer(Franklin) | WU-01/02/04 | 只读，覆盖安全/并发/生命周期/状态机 |
| 集体验收 | 等待 | Leader | WU-01..05 | E2E、真实浏览器、审查、Lore commit |

## 实测基线（Leader 亲自复核）

```text
.venv/bin/python -m pytest tests/subagent_executions tests/webui/test_subagent_execution_runtime.py -q
=> 29 passed in 1.17s
```

WU-04 自报：

```text
node node_modules/vitest/vitest.mjs run src/tests/subagent-execution-events.test.ts   src/tests/subagent-execution-reducer.test.ts src/tests/subagent-execution-flow.test.tsx
=> 3 files / 14 tests passed
node node_modules/vitest/vitest.mjs run src/tests/team-flow.test.tsx
=> 1 file / 9 tests passed
```

## 已发现的集成缺口（Leader 定位，交给 WU-03 收口）

1. `nanobot/agent/loop.py:445` 构造 `SubagentManager` 时未传 `execution_ledger` / `execution_event_sink`，
   即生产路径实际上不会写 ledger；WU-02 的单测是**显式注入**后才绿的，不能证明生产接线。
2. `SubagentManager` 需要 workspace 级 `ExecutionRepository`，并需要 gateway 提供 WS 事件 sink；
   该接线的所有权已划给 WU-03（gateway lane），写入边界已包含 `loop.py`、`gateway_services.py`、
   `channels/websocket/runtime.py`。
3. `ExecutionRepository.close()` 必须挂到 AgentLoop 关闭路径，否则 workspace writer lock 不会释放。

## 关键链路事实（供 WU-05 与验收使用）

- 详情快照 `parentToolCallId` 与 WebUI tool progress 事件的 `call_id` 同源
  （`nanobot/agent/tools/execution.py` 绑定 `tool_call.id`；`nanobot/utils/progress_events.py:95` 写入 `call_id`），
  因此「工具调用 → invocation」必须是 ID 精确关联，禁止按任务名/`started` 文本猜 ID。
- i18n 有 10 语言键集 parity 强校验（`webui/src/tests/i18n.test.tsx`），新增 UI 文案必须 10 语言齐备。

## 补充：折叠态入口缺口（2026-10-18 用户前端页面反馈）

用户反馈「主聊天没有展现子 Agent 的样式/标识」。真机核对后确认：入口只渲染在可折叠的活动簇内部，
回合结束后活动簇默认折叠，主聊天只剩 "Worked for 8s"，因此看不到「子 Agent」标识。
该行为违反 spec §3「若现有工具组折叠，在组标题保留『2 个子任务运行中』的入口」。

已按 spec 收口（Leader 直做，未派发）：

- `ThinkingReasoningShell` 增加 `headerAccessory`；`AgentActivityCluster` 增加
  `collectSubagentItems()`（复用簇内同一份 trace/状态口径，按 `tool_call_id` 关联）并在标题
  渲染 `SubagentExecutionGroupEntry`（「子 Agent ×N · 状态」，可点击）。
- 旧 gateway / 无关联历史调用时不渲染该入口，避免出现打不开的控件。
- 新增 i18n key `thread.subagentExecution.groupEntry`（10 语言齐备）。

验证见 `.ai-runtime-artifacts/verifications/2026-10-18-subagent-observability-frontend-page-test.md`。

附带发现：用户截图中的文案（「和 Codex 一起工作」「用时 1m 37s」「已读取…技能」「…开始工作」、
右侧「完成 · 2」）在知序源码、构建产物与 git 历史中均不存在，判断该截图是 Codex 桌面端
（ChatGPT.app）界面，不是知序 WebUI，需用户确认测试对象。
