---
artifact: stack
route: source-driven-development
skills: [source-driven-development]
skills_evidence: [.claude/skills/source-driven-development/SKILL.md]
source: [pyproject.toml, webui/package.json]
created_at: 2026-10-26
status: complete
---

# 评估可靠性计划技术栈核查

| 项目 | 声明 | 本机安装值 |
| --- | --- | --- |
| Zhixu | 0.3.0 | 当前源码 pyproject.toml |
| Python | >=3.11 | 3.13.3 |
| aiohttp | >=3.9.0,<4.0.0 | 3.14.5 |
| Pydantic | >=2.12.0,<3.0.0 | 2.14.1 |
| pytest | 项目开发依赖 | 9.1.1 |
| Ruff | 项目开发依赖 | 0.17.0 |
| React | ^18.3.1 | 18.3.1 |
| TypeScript | ^5.7.2 | 5.9.3 |
| Vite | ^5.4.11 | 5.4.21 |
| Tailwind | ^3.4.17 | 3.4.19 |
| Vitest | ^2.1.8 | 2.1.9 |

安装版本通过 importlib.metadata 与 node_modules/package.json 读取，未把声明范围当精确版本。
后端使用 asyncio + aiohttp + SQLite；Trace 使用 JSONL，已有 Worker IPC、规则、Judge、校准与 HTML 报告。
前端复用 webui 中的评估设置页。本机没有 bun，计划命令使用 npm/npx。
本计划不新增 LangChain、ADK、Inspect 或外部托管平台依赖；借鉴评估方法，不替换 Agent 框架。
