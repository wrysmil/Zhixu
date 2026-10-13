---
artifact: research-report
route: source-driven-development
skills:
  - source-driven-development
skills_evidence:
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
source:
  - webui/package.json
  - desktop/package.json
  - webui/node_modules/*/package.json
  - .ai-runtime-artifacts/stack/2026-10-10-zhixu-workspace-stack.md
created_at: 2026-10-11
status: verified
---

# Mira 首页与双侧栏视觉优化：技术栈核对

当前 checkout 实际安装版本：React 18.3.1、Vite 5.4.21、TypeScript 5.9.3、Tailwind CSS 3.4.19、lucide-react 0.469.0、@xterm/xterm 6.0.0。桌面壳 `desktop/package.json` 声明 Electron ^33.2.0。

本阶段只制定视觉方案，不调用新框架 API，也不引入图标依赖。实施阶段如需使用新 API，应查对应版本官方文档。左侧图标优先从现有 `lucide-react` 选择；终端继续复用现有 xterm 实例及 PTY/管道双模式。

已读项目基线：Codex 对标方案、首页启动面板方案、v1.1 dock/PTY 方案；它们决定功能与安全边界。本次视觉优化不改这些数据契约。
