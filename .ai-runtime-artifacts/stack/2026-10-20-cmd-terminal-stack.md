---
artifact: research-report
route: source-driven-development
skills: [source-driven-development]
skills_evidence: [.claude/skills/source-driven-development/SKILL.md]
source: [webui/package.json, desktop/package.json, nanobot/webui/pty_terminal.py]
created_at: 2026-10-20
status: verified
---

# CMD 缺陷调查技术栈

本机核对：Python 3.13.3、websockets 15.0.1、React 18.3.1、Vite 5.4.21、xterm 6.0.0、Vitest 2.1.9。

Windows 走项目现有 asyncio 管道终端，POSIX 才创建 PTY bridge。本轮新增现有接口的真实 Windows 回归测试并重启网关，不升级依赖、不引入框架 API、不修改认证机制。
