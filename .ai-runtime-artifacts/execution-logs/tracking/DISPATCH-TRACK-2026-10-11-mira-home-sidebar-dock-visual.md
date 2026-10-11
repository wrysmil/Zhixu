---
artifact: dispatch-track
route: orchestration
batch: mira-home-sidebar-dock-visual
plan: .ai-runtime-artifacts/plans/2026-10-11-mira-home-sidebar-dock-visual-plan.md
dispatch: .ai-runtime-artifacts/plans/2026-10-11-mira-home-sidebar-dock-visual-dispatch.md
created_at: 2026-10-11
updated_at: 2026-10-11
status: pending-dispatch
---

# DISPATCH-TRACK · mira-home-sidebar-dock-visual

## 批次状态

- 启动：`pending-dispatch`
- 用户批准：`可以的`（2026-10-11）
- Worktree：未创建（用户确认后由 Leader 执行）

## WU 时间线

| 阶段 | 触发条件 | 状态 | 起 |
| --- | --- | --- | --- |
| A.1 globals.css token | 用户确认 | pending | — |
| A.2 tailwind extend | A.1 commit | pending | — |
| A.3 App.tsx 常量 | A.2 commit | pending | — |
| B/C 并发派发 | A.3 commit | pending | — |
| D.1 截图 | B+C 全 commit | pending | — |
| D.2 review | D.1 通过 | pending | — |
| D.3 合流 | D.2 通过 | pending | — |

## 通信约定

- worker 返回格式：`### Skills 使用` + commit hash + diff 摘要 + 测试输出
- leader 整合：本文件 append-only 更新
- 阻塞事件：本文件同步记录（commit 失败 / 测试 fail / 文件冲突）

