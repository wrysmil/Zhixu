---
artifact: research-report
route: source-driven-development
skills: [source-driven-development]
skills_evidence: [.claude/skills/source-driven-development/SKILL.md]
source: [pyproject.toml, webui/package.json, webui/node_modules, .venv, harness-kit/project.profile.md]
created_at: 2026-10-17
status: verified
---

# 创作空间固定团队技术栈核对

基线：分支 `feature/desktop-app`，HEAD `161cbf7`。项目画像中的 memory 分支描述已过时，本次以 Git 与当前源码为准。

| 项目 | 声明 | 本次实际读取的版本 |
| --- | --- | --- |
| nanobot-ai | 0.3.0 | 0.3.0 |
| Python | >=3.11 | `.venv/bin/python` 3.13.9；系统 `python3` 为 3.9.6 |
| Pydantic | >=2.12,<3 | 2.13.5 |
| aiohttp | >=3.9,<4 | 3.14.3 |
| MCP Python SDK | >=1.26,<2 | 1.30.0 |
| pytest / pytest-asyncio | dev 依赖 | 9.1.1 / 1.4.0 |
| Ruff / BasedPyright | dev 依赖 | 0.16.6 / 1.40.0 |
| React / react-dom | ^18.3.1 | 18.3.1 / 18.3.1 |
| TypeScript | ^5.7.2 | 5.9.3 |
| Vite / Vitest | ^5.4.11 / ^2.1.8 | 5.4.21 / 2.1.9 |
| Tailwind CSS | ^3.4.17 | 3.4.19 |
| diff | ^9.0.0 | 9.0.0 |
| Node.js | 本机运行时 | 24.13.0 |

前端精确版本来自 `webui/node_modules/<package>/package.json`；Python 版本来自项目解释器及 `importlib.metadata`。本机未发现 Bun，后续执行可使用既有 Node/npm 与本地 Vitest，不安装替代依赖。

## 已有架构

- 文章库：`nanobot/writing/{database,models,repository}.py`，SQLite schema v1，文章快照、revision、digest、请求去重已存在。
- 传输：HTTP 读、现有认证 WebSocket mutation 写；沿用 `writing-api.ts` 模式。
- 多 Agent：`nanobot/teams/` 已有任务依赖、并发/轮次/超时限制、冻结档案与事件；成员通过 `SubagentManager.execute_frozen` 进入共享 Runner。
- 管理：Agent 有出厂目录与删除保护；Skill 有 builtin 来源与删除保护；MCP 预设尚无创作空间专属的不可删除策略。
- UI：保留 React/Tailwind、Markdown 编辑与预览、现有 IME 和保存保护。文章侧栏直接使用固定内置团队，显示任务进度与建议，不提供工作流设计器或团队选择器。

## 约束

本轮不安装、更新依赖，不修改 Loop/Runner，不新增模型调用循环，不引入 Dify、LangGraph、CrewAI 或数据库服务。竞品作为设计证据，不作为依赖选择。
