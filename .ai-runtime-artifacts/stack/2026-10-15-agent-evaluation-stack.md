---
artifact: research-report
route: source-driven-development
skills:
  - source-driven-development
skills_evidence:
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/source-driven-development/SKILL.md
source:
  - pyproject.toml
  - webui/package.json
  - webui/bun.lock
  - webui/vite.config.ts
created_at: 2026-10-15
status: inspected
updated_at: 2026-10-15
---

# 自动化评估技术栈核对

| 组件 | 当前项目证据 | 评估网站采用方式 |
| --- | --- | --- |
| nanobot-ai | pyproject.toml：0.3.0 | 复用同仓库 Python SDK、AgentLoop、AgentRunner |
| Python | requires-python >=3.11 | 遵循 3.11 兼容基线；未探测已安装解释器版本 |
| aiohttp | api extra：>=3.9.0,<4.0.0 | 独立 HTTP 服务，启动时明确检查 api extra |
| Pydantic | >=2.12.0,<3.0.0 | 用例、请求和评分结果校验 |
| SQLite | Python 标准库 | 运行与用例状态持久化；不引入外部数据库 |
| React / React DOM | bun.lock：18.3.1 / 18.3.1 | 独立 evalui 前端，复用技术选型 |
| TypeScript | bun.lock：5.9.3 | 独立前端构建与类型检查 |
| Vite | bun.lock：5.4.21 | 独立开发入口和构建目录 |
| Tailwind CSS | bun.lock：3.4.19 | 信息密度适中的评估后台 |
| Vitest | bun.lock：2.1.9 | 前端必要行为测试 |
| pytest / pytest-asyncio | pyproject.toml：>=9.0.0,<10.0.0 / >=1.3.0,<2.0.0 | 后端测试沿用项目设施 |

前端版本来自现有 bun.lock，并不代表已安装。Python 依赖只声明范围，当前未发现可用的 uv.lock 或本地 .venv；不能把范围下限当成实际解析版本。新增应用在实施时生成自己的依赖锁，Python 安装结果在执行日志中记录，不需要本轮升级现有应用依赖。

## 实施环境实测（2026-10-15，GROUP-1）

| 项 | 实测结果 | 影响 |
| --- | --- | --- |
| 主 checkout `.venv` | 空壳（仅 `_virtualenv`，import pydantic 失败） | 不能直接跑 pytest，需先同步 |
| worktree `.venv` | `uv sync --all-extras --dev` 退出码 0；pydantic 2.14.1、pytest 9.1.1、aiohttp 3.14.5；解释器 CPython 3.13.13（uv 托管 3.13.3 基底） | WU-01 的 T01/T02 验证命令可执行；3.13 仍需保持 3.11 兼容基线 |
| Bun | 本机 PATH 与常见安装目录均未找到 | WU-02（evalui 的 `bun install --frozen-lockfile` / typecheck / vitest / build）无法验证，本批不派发，等用户装好 Bun |
| Node | v24.18.0 存在 | 不作为 evalui 的包管理器，避免生成第二套 lock |


## 已核对的官方资料

- aiohttp 3.9 应用生命周期与清理：https://docs.aiohttp.org/en/v3.9.5/web_advanced.html#cleanup-context
- Python 3.11 异步子进程：https://docs.python.org/3.11/library/asyncio-subprocess.html
- Python 3.11 SQLite：https://docs.python.org/3.11/library/sqlite3.html

设计采用应用生命周期管理调度器、显式回收子进程，以及 SQLite 事务持久化。Windows 子进程与进程树清理需要单独验证。此文档不声称已完成运行时验证。
