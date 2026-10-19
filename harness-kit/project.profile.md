# Project Profile

本文件是当前项目画像，不是通用 Harness 模板。迁移到其他项目时，必须让 AI 通过 `harness-kit/init/project-profiler.prompt.md` 重新生成，并由人 review 推断项和待确认项。

## 项目身份

**知序 Zhixu v0.3.0（Agent-With-Memory 仓库）** — 超轻量、自托管的个人 AI agent 框架（Python），支持 WebUI / 终端 / 聊天应用多端运行，内置工具、长期记忆（Dream）、MCP 集成、模型路由、多 agent 委派、定时任务与 OpenAI 兼容 API。

本仓库是上游 HKUDS/nanobot 的个人分支（remote: `github.com/wrysmil/Agent-With-Memory`）。当前主线工作分支为 **feature/desktop-app**（与 `origin/feature/desktop-app` 同步，无未推送提交）；品牌改名（知序 Zhixu）等专项工作会在其之上开临时分支（如 `feature/zhixu-rename-phase1`）完成后合回。记忆系统二期扩展（WebUI 记忆管理 API 与前端原型、episode 相关与部分更新 repository CRUD、phase-2 抽取设计）属于 `feature/memory-system` 分支的历史工作，结论见 `docs/memory.md` 与 `.ai-runtime-artifacts/`。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 用户端 | React + TypeScript + Vite + Tailwind（`webui/`，Bun 1.3.x） |
| 终端 UI | TypeScript + Bun（`tui/`） |
| 共享前端包 | TypeScript（`packages/client-events/`） |
| 后端 / Agent 运行时 | Python ≥3.11 + asyncio（`nanobot/`）；`uv sync --all-extras --dev` |
| 数据库 / 持久化 | SQLite / JSON 文件（session、memory、config 均以文件持久化，原子写 + fsync） |
| 测试 | pytest（`asyncio_mode=auto`）、WebUI/TUI 用 `bun run test` |
| 静态检查 | `ruff check` + `basedpyright`（strict，匹配 CI） |
| 部署 | Dockerfile / docker-compose.yml / render.yaml（Render）；GitHub Actions（`.github/workflows/ci.yml` + `tui-release.yml`） |
| Harness | harness-kit 目录拷贝版（本文件所在目录），Claude Code 平台投影 |

## 主要目录

| 路径 | 职责 |
| --- | --- |
| `nanobot/` | 核心 Python 包：agent 循环、providers、channels、tools、memory、session、gateway、web、api |
| `webui/` | Web SPA（React + TS + Vite），经 WebSocket 多路复用协议连 gateway |
| `tui/` | 终端 UI（Bun + TS） |
| `packages/client-events/` | 前端共享事件包 |
| `tests/` | Python 测试（镜像 `nanobot/` 结构） |
| `docs/` | 项目文档（architecture、development、memory、deployment 等） |
| `scripts/` | 安装/CI 辅助脚本（`install_channel_dependencies.py` 等） |
| `images/` | README 封面图 |
| `harness-kit/` | Agent Harness 规范、适配器、脚本（勿当业务模块改） |
| `.ai-runtime-artifacts/` | spec / plan / verification / execution-log 等过程产物 |

## 禁区

- **勿读、勿提交**：`.env`、密钥、token、provider key、本机私有 MCP 配置。
- **勿在未过阶段门禁时**大规模改业务代码（Harness `routing.md`）；小改动除外。
- **勿删改** `harness-kit/` 内 `core/` 通用规则（项目差异写在 `project.*`）。
- **子 Agent 默认不** `git commit` / `push`（Leader + `git-xywh` 执行）。

## 交付口径

- 非琐碎需求：先 spec → 人确认 → plan → 人确认 → 实现 → **尾盘**：集体测试 → 集体审查（Leader 落盘）→ execution-log 完成。
- 验收标准：Python 改动须 `pytest` + `ruff check nanobot/` + `basedpyright` 通过（匹配 CI）；WebUI 改动须 `bun run test` + `bun run build` 通过；涉及 gateway/WS 的改动须确认 gateway 进程可启动。
- 文档与根 `README.md` 不一致时，以 **已批准 spec/plan** 与当前代码为准，并记待确认项。

## 推断项

- **Git 模型**：CI 只在 push/PR 到 `main` 时触发 → 推断 `main` 为受保护主干；未见 `develop`/`test` 主干，疑似**偏离** git-xywh 三主干模型（需人工确认）。
- **提交规范**：无 `.husky/`、`commitlint`、lint-staged 配置，但近期提交采用 Angular 风格（`feat(webui): ...`）→ 推断为**人工遵循 Angular 约定，无本地 pre-commit 门禁**。
- **包管理**：`uv.lock` 被 `.gitignore` 排除，但 CI 的 changes 检测显式引用 `uv.lock` → 推断本地存在 uv.lock 但未跟踪（与 CI 路径分类有潜在不一致，需确认）。
- **测试入口**：WebUI 存在 `webui/src/tests/`（新 memory API 测试），Python 测试在 `tests/`。

## 待确认项

- 是否采用 git-xywh 三主干（main/test/develop），还是沿用本仓库现有的 `main` + `feature/*` 简化模型。
- 远程保护分支与 AI push 权限：`origin/feature/desktop-app` 已存在且本地与其同步，是否允许 Leader 直接 push；`origin/feature/memory-system` 亦存在但已非当前主线。
- `uv.lock` 被 `.gitignore` 排除但 CI 引用 —— 是否有意为之，是否应恢复跟踪。
- harness-kit 升级方式：目录拷贝（当前）还是改 Git Submodule。
- MR/PR 审查人要求（CI 无 required-reviewer 配置，需人工约定）。
