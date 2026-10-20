---
artifact: research-report
route: source-driven-development
skills: [source-driven-development]
skills_evidence: [.claude/skills/source-driven-development/SKILL.md]
source: [webui/package.json, .ai-runtime-artifacts/stack/2026-10-20-cmd-terminal-stack.md]
created_at: 2026-10-20
status: verified
---

# 终端样式技术栈

复用同一 checkout、本日已核对的运行环境：React 18.3.1、TypeScript 5.9.3、Vite 5.4.21、xterm 6.0.0、Vitest 2.1.9。布局使用项目已有 Tailwind 3 和 CSS 语义变量。

没有新增依赖。本地 xterm 类型声明确认 fontSize、lineHeight 可用；终端主题继续由现有 updateAppearance 更新，不重建终端。
