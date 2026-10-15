---
artifact: execution-log
route: orchestration
date: 2026-10-15
status: in-progress
source:
  - .ai-runtime-artifacts/specs/2026-10-11-home-dock-pty-terminal-v1.1-spec.md
  - .ai-runtime-artifacts/plans/2026-10-11-home-dock-pty-terminal-v1.1-plan.md
  - 用户本轮截图及修复要求
skills: [systematic-debugging, source-driven-development, orchestration, verification-before-completion]
---

# 工作区侧栏修复

本次是既有功能的缺陷修复。用户要求修复越界、Windows 终端永久启动中、文件点击预览，并将默认工作区信息与展开后的侧栏工具分离。当前截图要求优先于旧方案中“工具嵌入工作台”的规定。

## 上下文与约束

- 已扫描既有 specs/plans/contracts，读取项目画像及 context-map。
- 当前 React 18.3.1 系列、Vite 5.4、TypeScript 5.7、xterm 6、FitAddon 0.11；锁文件中用户已有变更予以保留。
- 主 checkout 原有 desktop/package-lock.json、webui/package-lock.json 与 agent-evaluation 等产物不纳入本次改动。
- WORKTREE-INIT: D:/studyspace/project/Zhixu/.harness-worktrees/workspace-panel-fixes，base=fccafd7，detached HEAD。
- 不提交、不推送；完成后整合本次明确文件到用户工作区。

## 执行图

- WU-01 debugger：终端启动状态、前后端生命周期与 Windows 执行链；独占终端 store/client、TerminalSurface、StartDock 终端行为及相关 Python 文件/测试。
- WU-02 coder：工作区和工具面板分离、文件树与预览并列、输入框及面板宽度约束；独占 SessionWorkbench、ThreadShell、PaneWorkbench、App、DockFileTab、FilePreviewPanel 与对应测试。StartDock 如需公共布局修改先与 Leader 协调。
- Leader：跟踪、浏览器验收、集体验证、独立审查及整合。

## 验收

1. 窄窗口及打开工具后，页面、输入框与侧栏均在可见边界内。
2. Windows cmd 启动有明确运行/失败反馈，可以执行命令；断线/失败不永久停在启动中。
3. 点击文件能显示内容预览，保留文件树导航。
4. 工作区信息默认显示，工具在点击展开后出现，与摘要不共用上下堆叠区域。
5. 目标测试、前端构建与 lint、桌面 smoke、浏览器实际布局检查。

## 进展

- 已完成：只读根因调查与隔离工作区创建。
- 进行中：WU-01 / WU-02。
