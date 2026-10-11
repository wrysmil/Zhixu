---
artifact: dispatch
route: writing-plans -> subagent-driven-development
batch: mira-home-sidebar-dock-visual
plan: .ai-runtime-artifacts/plans/2026-10-11-mira-home-sidebar-dock-visual-plan.md
spec: .ai-runtime-artifacts/specs/2026-10-11-mira-home-sidebar-dock-visual-spec.md
created_at: 2026-10-11
status: draft
approved: false
worktree: .harness-worktrees/zhixu/wt-mira-home-sidebar-dock-visual（用户确认后由 Leader 创建）
---

# 调度：mira-home-sidebar-dock-visual

## 拓扑

- Group A 视觉 token（leader直做，prereq）：
  - Task A.1 globals.css 新增 9 个 CSS 变量（亮暗双主题）
  - Task A.2 tailwind.config.js 暴露 width/height/fontSize/maxWidth 共 4 块
  - Task A.3 App.tsx SIDEBAR_WIDTH/RAIL_WIDTH/CHAT_WIDTH 同步 54/54/220
  - 三个 commit，串行。
- Group B 左轨道 + 首页（worker，依赖 A）：
  - Task B.1 WorkspaceNavigation rail `w-16` → `w-rail`
  - Task B.2 ThreadShell HeroGreeting `text-[34/48px]` → `text-hero`
  - Task B.3 ThreadComposer hero `max-w-[58rem]` → `max-w-heroComposer`
  - 三个 commit，串行。
- Group C 右 dock / 浏览器 / 终端（worker，依赖 A，与 B 并发）：
  - Task C.1 StartDock aside/header/启动器三处改写
  - Task C.2 BrowserSection 工具栏 36×36 + 地址框 13px
  - Task C.3 TerminalSurface xterm `flex-1` + `text-terminal`
  - 三个 commit，串行。
- Group D 尾盘（leader + reviewer + security-auditor）：
  - Task D.1 6 张截图 + 像素核查报告
  - Task D.2 collective-test + code-review + security-review 三份产物
  - Task D.3 合流 + execution-log + tracking 落盘

依赖：A → {B ∥ C} → D。B 与 C 文件零交集，并发安全。

## 审查门

- 每 Task worker 完成后 leader 抽验：diff 对照 plan Interfaces + `npm run lint` 退出码 + 关键 `npm run test` 子集结果。
- Group A commit 后才允许派发 B / C（共享 token 未就绪时 utility 不存在，编译失败）。
- Group D 启动前提：B 与 C 全部 commit；任一失败阻塞整批。
- 批次收尾：collective-test + code-review + security-review 全部 PASS 才允许合流。

## 文件冲突控制

1. Group A 独占：globals.css、tailwind.config.js、App.tsx（仅常量）。
2. Group B 独占：WorkspaceNavigation.tsx、ThreadShell.tsx、ThreadComposer.tsx。
3. Group C 独占：StartDock.tsx、BrowserSection.tsx、TerminalSurface.tsx。
4. i18n locale 文件：本批不修改任何 locale（规格不要求新增键；hero 标题用现有 `thread.empty.greetings.*` 随机键）。
5. 用户未提交文件（`docs/记忆系统/*.drawio.bkp`、`memory/`）：worker 不得触碰；leader 合流前处理。
6. 契约（PTY v2、browser-url 白名单、dock-store）：冻结，不改。

## Skill 派发要求（worker prompt 内列「本 WU Skills」，必 Load）

- 全部编码 worker：`source-driven-development`（`.ai-runtime-artifacts/specs/` + `stack/` 为真相源）、`verification-before-completion`、`incremental-implementation`；返回须含 `### Skills 使用`。
- B 全部 + C 全部：`frontend-ui-engineering`（CSS 变量 → utility → 组件三层落地）。
- C.2 BrowserSection + D.2 security-review：`security-and-hardening`（URL 白名单 + iframe sandbox 不动）。
- C.3 TerminalSurface + D.2 collective-test：`test-driven-development`（PTY 行为回归）。
- Leader：`orchestration`；提交时 `git-xywh` + `harness-kit/project.git.md`；尾盘按 routing § 批次收尾扇出。

## 共享文件约束

1. worker 不 commit/push（Leader 统一提交，按 plan 各 Task 末尾 commit message）；不改他人文件范围；不动 `.ai-runtime-artifacts/` 中非本 WU 产物。
2. `webui/src/globals.css` 与 `webui/tailwind.config.js` 仅 A 写；`App.tsx` 仅 A 改常量；其余组件仅对应组写。
3. 视觉规格 §验收口径 1–6 在 Task D.1 一次性核查（不分散到各 Task）。

## 工作树

- 主 checkout 保持现状（不动 `docs/记忆系统/*.drawio.bkp`、`memory/`、`feature/desktop-app` 当前未提交文件）。
- Leader 创建 worktree：`.harness-worktrees/zhixu/wt-mira-home-sidebar-dock-visual`，分支 `harness/wt-mira-home-sidebar-dock-visual`，base 当前 `feature/desktop-app` HEAD。
- 所有编码在 worktree 内完成；尾盘 D.3 处理合流回主 checkout。

## Next

- 用户确认后：Leader 创建 worktree → 执行 Group A → 并发派发 Group B / C → 收口 Group D。
- 也可指定裁剪：仅跑 Group A（仅 token 落地，便于将来再分批实施）；或 A + B（仅左栏 + 首页）；或 A + C（仅右 dock）。
- 取消：`$cancel`。
