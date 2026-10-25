---
artifact: stack
route: source-driven-development
skills: [source-driven-development]
skills_evidence: [.claude/skills/source-driven-development/SKILL.md]
source:
  - webui/package.json
  - webui/node_modules/react/package.json
  - webui/node_modules/vite/package.json
  - webui/node_modules/typescript/package.json
  - webui/node_modules/tailwindcss/package.json
  - webui/node_modules/vitest/package.json
  - pyproject.toml
created_at: 2026-10-25
---

# 评估可观测性与报告技术栈

| 层 | 声明版本 | 本机安装版本 / 约束 |
| --- | --- | --- |
| React | ^18.3.1 | 18.3.1 |
| Vite | ^5.4.11 | 5.4.21 |
| TypeScript | ^5.7.2 | 5.9.3 |
| Tailwind CSS | ^3.4.17 | 3.4.19 |
| Vitest | ^2.1.8 | 2.1.9 |
| Python | >=3.11 | 项目约束；本机 `.venv/bin/python` 指向 Python 3.13 |
| aiohttp | >=3.9.0,<4.0.0 | 仅核对声明，未将声明范围当作精确安装版本 |
| pydantic | >=2.12.0,<3.0.0 | 仅核对声明，未将声明范围当作精确安装版本 |

演示使用原生 HTML/CSS/SVG/JavaScript，不安装依赖，不连接评估 API，不执行模拟工具参数中的命令。正式接入复用 React、现有 Trace、UsageLedger 和分页约定；当前没有直接声明 React Flow / XYFlow 依赖，不在本次计划中新增图形库。Streamdown 的 Mermaid 间接代码不作为业务画布的现成接口。

项目上下文依据：既有评估 spec、2026-10-23 WebUI plan、评估接口契约、`project.profile.md`、`context-map.md`。本轮只交付过程产物，不改动 `nanobot/` 或 `webui/src/`。
