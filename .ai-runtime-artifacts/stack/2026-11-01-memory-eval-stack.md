---
artifact: stack
route: source-driven-development
skills:
  - source-driven-development
skills_evidence:
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
source:
  - pyproject.toml
  - uv.lock
  - webui/package.json
  - webui/bun.lock
created_at: 2026-11-01
---

# 记忆评测设计的技术栈核对

项目 nanobot-ai 0.3.0；本次检查分支 feature/desktop-app，HEAD 为 ce595d6e14857cea18903d4b2eae688c804c1320。实际执行 Python 为 3.13.9，项目要求 Python ≥3.11。

| 依赖 | 声明范围 | 锁文件精确版本 |
| --- | --- | --- |
| pydantic | ≥2.12.0,<3.0.0 | 2.13.5 |
| openai | ≥2.8.0 | 3.11.0 |
| aiohttp | ≥3.9.0,<4.0.0（optional/dev） | 3.14.3 |
| pytest | ≥9.0.0,<10.0.0 | 9.1.1 |
| chromadb | ≥1.0,<2.0（optional） | 1.5.9 |
| React | ^18.3.1 | 18.3.1 |
| Vite | ^5.4.11 | 5.4.21 |
| TypeScript | ^5.7.2 | 5.9.3 |
| Tailwind CSS | ^3.4.17 | 3.4.19 |
| Vitest | ^2.1.8 | 2.1.9 |

精确版本来自锁文件，不能据此断言所有可选包已安装。本次只设计数据与操作流程，未增加依赖、未改变框架 API。
