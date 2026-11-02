---
artifact: research-report
route: source-driven-development
skills: [source-driven-development]
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
source:
  - pyproject.toml
  - webui/package.json
  - nanobot/evaluation/schemas.py
created_at: 2026-11-02
---

# 记忆评估方案：技术栈检测

本轮仅调研、设计与文档检查，不安装依赖、不运行模型、不修改业务代码。

| 项目 | 声明约束 | 本机读取到的版本 | 证据 |
| --- | --- | --- | --- |
| Python | >=3.11 | 3.13.3 | pyproject.toml；.venv/Scripts/python.exe |
| Pydantic | >=2.12.0,<3.0.0 | 2.14.1 | pyproject.toml；importlib.metadata |
| aiohttp | >=3.9.0,<4.0.0 | 3.14.5 | pyproject.toml api/dev extra；importlib.metadata |
| OpenAI SDK | >=2.8.0 | 3.31.0 | pyproject.toml；importlib.metadata |
| pytest | >=9.0.0,<10.0.0；dev group >=9.1.1 | 9.1.1 | pyproject.toml；importlib.metadata |
| React | ^18.3.1 | 18.3.1 | webui/package.json；webui/node_modules/react/package.json |
| Vite | ^5.4.11 | 5.4.21 | webui/package.json；webui/node_modules/vite/package.json |
| TypeScript | ^5.7.2 | 5.9.3 | webui/package.json；webui/node_modules/typescript/package.json |
| 持久化 | SQLite + 文件 | 本轮未采集 SQLite runtime 版本 | nanobot/evaluation/database.py、nanobot/memory/__init__.py |

声明约束与已安装版本分别记录，不能把 package.json 的范围当成精确安装版本。

当前分支：feature/desktop-app；HEAD：ce595d6e14857cea18903d4b2eae688c804c1320。只读 git diff --stat 无已跟踪文件差异；存在较多原有未跟踪产物，保持原状。

方案优先复用 nanobot/evaluation 的 SQLite、Worker、Trace、Judge、manifest、comparison 和现有 React 页面。外部基准使用数据导入适配，不直接把其历史依赖装进知序环境。是否直接集成 MemEval / DeepEval SDK，留待实现前按锁定版本验证；本轮不承诺其依赖与当前环境兼容。
