---
artifact: research-report
route: source-driven-development
skills: [source-driven-development]
skills_evidence: [.claude/skills/source-driven-development/SKILL.md]
source: [pyproject.toml, webui/package.json, webui/bun.lock, uv.lock, harness-kit/project.profile.md]
created_at: 2026-10-15
status: verified
---

# Agent Team 技术栈核对

基线为 `feature/desktop-app`、提交 `690638e`。画像仍提到旧 memory 分支，当前事实以源码和 Git 查询为准。

| 层 | 声明 | 本地锁文件版本 |
| --- | --- | --- |
| Python | >=3.11 | 项目 `.venv/bin/python` 为 3.13.9；系统 `python3` 为 3.9.6，不用于项目验证 |
| nanobot-ai | 0.3.0 | 0.3.0 |
| Pydantic | >=2.12,<3 | 2.13.5 |
| aiohttp | >=3.9,<4，api/dev extra | 3.14.3 |
| pytest / pytest-asyncio | dev 依赖 | 9.1.1 / 1.4.0 |
| basedpyright / ruff | dev 依赖 | 1.40.0 / 0.16.6 |
| React | ^18.3.1 | 18.3.1 |
| TypeScript | ^5.7.2 | 5.9.3 |
| Vite | ^5.4.11 | 5.4.21 |
| Vitest | ^2.1.8 | 2.1.9 |
| Tailwind CSS | ^3.4.17 | 3.4.19 |
| 持久化 | Python 标准库 sqlite3、既有 session 文件 | 不新增数据库服务 |

版本来自声明/锁文件，不代表本轮执行过构建或应用测试。`uv.lock` 为本地证据，不要求将它新增到 Git。前端另有 package-lock.json，实施时沿仓库当前包管理约定安装，禁止在本功能内顺便更新依赖。

设计约束：沿用 asyncio、typed AgentEvent/EventSink、HTTP 读与认证 WebSocket mutation 写、React 状态；不引入 LangGraph、CrewAI、Redis 或新的状态管理库。核心 Loop/Runner 只添加每回合的适配点，团队领域逻辑放在 `nanobot/teams/`。
