# DISPATCH-TRACK：home-dock-pty-terminal v1.1（append-only）

plan: .ai-runtime-artifacts/plans/2026-10-11-home-dock-pty-terminal-v1.1-plan.md
dispatch: .ai-runtime-artifacts/plans/2026-10-11-home-dock-pty-terminal-v1.1-dispatch.md

## INIT-2026-10-11

- 双 worktree 创建于 feature/desktop-app@83ce1ca：
  - wt-home-dock-pty-backend（harness/wt-home-dock-pty-backend）
  - wt-home-dock-pty-webui（harness/wt-home-dock-pty-webui）
- spec 计划期修正三处：§3.3 接管改 sessionStorage+terminal_start 重绑（首页默认 chat 每连接随机 uuid，list 不可见旧终端）；§4.2 增 already_running=接管语义与 shells 端点鉴权收口（check_api_token）；§4.4 本批取 R1 保守形态（restricted 保持管道守卫，PTY 仅 full-access）。
- 派发：Task1（backend worker）∥ Task6（webui worker），后台并行。
