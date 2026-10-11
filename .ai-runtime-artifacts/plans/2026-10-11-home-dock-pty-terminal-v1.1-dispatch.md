---
artifact: dispatch
route: writing-plans -> subagent-driven-development
batch: home-dock-pty-terminal-v1.1
plan: .ai-runtime-artifacts/plans/2026-10-11-home-dock-pty-terminal-v1.1-plan.md
spec: .ai-runtime-artifacts/specs/2026-10-11-home-dock-pty-terminal-v1.1-spec.md
contract: .ai-runtime-artifacts/contracts/2026-10-11-contract-home-start-panel.md
created_at: 2026-10-11
---

# 调度：home-dock-pty-terminal v1.1

## 拓扑

- Track1 后端（串行）：Task1 PtyTerminalBridge → Task2 shells 端点 → Task3 管道桥 v2 → Task4 Supervisor 门面 → Task5 契约接线。worktree `.harness-worktrees/zhixu/wt-home-dock-pty-backend`（分支 harness/wt-home-dock-pty-backend）。
- Track2 前端（串行）：Task6 dock store+addon-fit → Task7 TerminalSurface → Task8 fetchTerminalShells → Task9 StartDock+首页 → Task10 SessionWorkbench → Task11 快捷键。worktree `.harness-worktrees/zhixu/wt-home-dock-pty-webui`（分支 harness/wt-home-dock-pty-webui）。
- 尾盘：backend ← webui 合流后 Task12 i18n → Task13 实机验收+收口（集成 worktree 或 backend worktree）。
- 两轨文件零交集；跨轨依赖仅契约形状（计划已冻结签名）。

## 审查门

- 每任务 worker 完成后编排者抽验（diff 对计划 Interfaces + 测试输出）；Track1 Task1 为 R2 风险点，spike 测试不过不许继续。
- 批次收尾：集体测试 + code-review + security-review（重点 R1：restricted 保持管道守卫=保守实现；PTY 仅 full-access；接管重绑同 gate 信任面）。
