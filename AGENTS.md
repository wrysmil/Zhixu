---
name: AGENTS
description: Agent Harness 入口（覆盖层）+ nanobot 项目开发指南
---

# AGENTS.md

项目背景：nanobot-ai（v0.3.0，Agent-With-Memory 仓库）是超轻量自托管的个人 AI agent 框架，Python ≥3.11 + asyncio 核心，配套 React/TypeScript WebUI 与终端 TUI。当前在 feature/memory-system 分支扩展记忆系统（WebUI 记忆管理 API + 前端 + phase-2 抽取）。

> 必读：`harness-kit/core/routing.md`（路由判定、阶段门禁、按判定加载）
> AI 入口顺序：1. 本文件 → 2. `harness-kit/core/routing.md` → 3. 平台适配器入口（`.claude/rules/`）

## 强制声明

每个任务首句必须以 `「Harness：<route>」` 开头；Tier 1+ 或 stage/route skill 次行追加 `Skills: <slug>@<path> loaded|skipped`。完整 route 表见 `harness-kit/core/routing.md`。

## 产物落盘（强制）

**所有 AI 过程产物必须写入 `.ai-runtime-artifacts/` 对应子目录，禁止写入其他位置。**

| 产物类型 | 目录 |
| --- | --- |
| spec / 方案 | `.ai-runtime-artifacts/specs/` |
| plan / 计划 | `.ai-runtime-artifacts/plans/` |
| dispatch / 调度 | `.ai-runtime-artifacts/plans/`（同 stem 的 `*-dispatch.md`） |
| verification / 验证 | `.ai-runtime-artifacts/verifications/` |
| collective-test / 集体测试 | `.ai-runtime-artifacts/verifications/*-collective-test.md` |
| review / 审查 | `.ai-runtime-artifacts/reviews/` |
| execution-log / 执行日志 | `.ai-runtime-artifacts/execution-logs/` |
| decision / 决策 | `.ai-runtime-artifacts/decisions/` |
| retro / 复盘 | `.ai-runtime-artifacts/retros/` |
| research / 调研 | `.ai-runtime-artifacts/research/` |

**禁止：** 把产物写到 `docs/`、项目根目录或其他任意位置；把 plan 写到平台私有目录（如 `~/.claude/plans/`）；用 `docs/superpowers/` 代替 `.ai-runtime-artifacts/`。

## 沟通语言

对用户回复、子 Agent 派发、产物摘要、验收口径全部使用**中文**（代码标识符、路径、命令、API 名保留英文）。细则见 `routing.md` § 沟通语言。

## 项目指南（nanobot 仓库原有内容，保留）

---

This file provides guidance to AI coding agents working with this repository.

## Project Overview

nanobot is a lightweight, open-source AI agent framework written in Python with a React/TypeScript WebUI. It centers around a small agent loop that receives messages from chat channels, invokes an LLM provider, executes tools, and manages session memory.

## Development Commands

```bash
# Python: run single test / lint
pytest tests/test_openai_api.py::test_function -v
ruff check nanobot/

# Strict type checking (matches CI)
uv sync --all-extras --dev
uv run --no-sync python -m scripts.install_channel_dependencies --all-channels
uv run --no-sync basedpyright

# WebUI: dev server (proxies API/WS to gateway :8765), build, test
# Build outputs to ../nanobot/web/dist (bundled into the Python wheel)
cd webui && bun run dev      # or NANOBOT_API_URL=... bun run dev
cd webui && bun run build
cd webui && bun run test

# Gateway
nanobot gateway
```

## High-Level Architecture

### Core Data Flow

Messages flow through an async `MessageBus` (`nanobot/bus/queue.py`) that decouples chat channels from the agent core:

1. **Channels** (`nanobot/channels/`) receive messages from external platforms and publish `InboundMessage` events to the bus.
2. **`AgentLoop`** (`nanobot/agent/loop.py`) consumes inbound messages, builds context, and coordinates the turn.
3. **`AgentRunner`** (`nanobot/agent/runner.py`) handles the actual LLM conversation loop: send messages to the provider, receive tool calls, execute tools, and stream responses.
4. Responses are published as `OutboundMessage` events back to the appropriate channel.

### Key Subsystems

- **Agent Loop** (`nanobot/agent/loop.py`, `runner.py`): The core processing engine. `AgentLoop` manages session keys, hooks, and context building. `AgentRunner` executes the multi-turn LLM conversation with tool execution.
- **LLM Providers** (`nanobot/providers/`): Provider implementations (Anthropic, OpenAI-compatible, OpenAI Responses API, Azure, Bedrock, GitHub Copilot, OpenAI Codex, etc.) built on a common base (`base.py`). Includes image generation (`image_generation.py`) and audio transcription (`transcription.py`). `factory.py` and `registry.py` handle instantiation and model discovery.
- **Channels** (`nanobot/channels/`): Platform integrations (Telegram, Discord, Slack, Feishu, Matrix, WhatsApp, QQ, WeChat, WeCom, DingTalk, Email, MoChat, MS Teams, WebSocket, Mattermost). `manager.py` discovers and coordinates them. Channels are self-contained packages auto-discovered via `pkgutil` scanning.
- **Tools** (`nanobot/agent/tools/`): Agent capabilities exposed to the LLM: filesystem (read/write/edit/list), shell execution (with sandbox backends), web search/fetch, MCP servers, cron, notebook editing, subagent spawning, long-running tasks / sustained goals (`long_task.py`), image generation, and self-modification. Tools are auto-discovered via `pkgutil` scan + entry-point plugins.
- **Memory** (`nanobot/agent/memory.py`): Session history persistence with Dream two-phase memory consolidation. Uses atomic writes with fsync for durability.
- **Session Management** (`nanobot/session/`): Per-session history, context compaction, TTL-based auto-compaction (`manager.py`), and sustained goal state tracking (`goal_state.py`).
- **Config** (`nanobot/config/schema.py`, `loader.py`): Pydantic-based configuration loaded from `~/.nanobot/config.json`. Supports camelCase aliases for JSON compatibility.
- **WebUI** (`webui/`): Vite-based React SPA that talks to the gateway over a WebSocket multiplex protocol. The dev server proxies `/api`, `/webui`, `/auth`, and WebSocket traffic to the gateway.
- **API Server** (`nanobot/api/server.py`): OpenAI-compatible HTTP API (`/v1/chat/completions`, `/v1/models`) for programmatic access.
- **Command Router** (`nanobot/command/`): Slash command routing and built-in command handlers.
- **Heartbeat** (`nanobot/templates/HEARTBEAT.md`): Periodic task list checked via `cron` jobs (legacy dedicated service removed).
- **Pairing** (`nanobot/pairing/`): DM sender approval store with persistent pairing codes per channel.
- **Skills** (`nanobot/skills/`): Built-in skill definitions (cron, github, image-generation, etc.) loaded into agent context.
- **Security** (`nanobot/security/`): PTH file guard and other security measures activated at CLI entry.

### Entry Points

- **CLI**: `nanobot/cli/commands.py`
- **Python SDK**: `nanobot/nanobot.py`

## Project-Specific Notes

- Architecture constraints: [`.agent/design.md`](.agent/design.md)
- Security boundaries: [`.agent/security.md`](.agent/security.md)
- Common gotchas: [`.agent/gotchas.md`](.agent/gotchas.md)

## WebUI 连接密码（Connect 页 Password 的来源）

**结论：** WebUI 登录页（`webui/src/App.tsx` 的 `AuthForm`，文案 `app.auth.label` = Password、`app.auth.submit` = Connect）要求输入的密码，就是配置文件 `~/.nanobot/config.json` 里

```
channels.websocket.tokenIssueSecret
```

的值；该项为空时回退到同级的 `channels.websocket.token`（取值逻辑见 `nanobot/cli/webui_support.py::webui_bootstrap_secret`）。它不是单独的用户账号密码。

- **生成**：首次启用 WebUI 时自动写入 `secrets.token_urlsafe(32)`（43 字符，见 `nanobot/cli/webui_support.py::_ensure_local_webui_channel`）；`nanobot onboard` Quick Start 会提示「Set a WebUI password」并写同一字段（`nanobot/cli/onboard.py`）。
- **用途/校验**：它是 bootstrap 与 token-issue 的共享密钥，HTTP 侧用 `Authorization: Bearer <secret>` 或 `X-Nanobot-Auth: <secret>` 校验（`nanobot/webui/ws_http.py::_handle_bootstrap`、`nanobot/webui/http_utils.py::issue_route_secret_matches`）。填错 → 前端提示 `app.auth.invalid`（Incorrect password）。
- **正常路径不会弹出此页**：`nanobot webui` 和桌面端 `desktop/main.cjs` 都把 URL 开成 `http://127.0.0.1:<port>/#/?bootstrapSecret=<secret>`，自动完成认证。只有裸开 `http://127.0.0.1:8765`（不带 `bootstrapSecret` fragment）、或 config 缺该字段时才需要手输。
- **查看/重置**：直接读或改 config 中该字段即可；若清空，下次启动会重新生成一个新密码。

## Contribution Flow

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for contribution flow and PR guidelines.

## Code Style

- Python 3.11+, asyncio throughout.
- Line length: 100.
- Linting: `ruff` with rules E, F, I, N, W (E501 ignored).
- pytest with `asyncio_mode = "auto"`.

## Common File Locations

- Config schema: `nanobot/config/schema.py`
- Provider base / new provider template: `nanobot/providers/base.py`
- Channel base / new channel template: `nanobot/channels/base.py`
- Tool registry: `nanobot/agent/tools/registry.py`
- WebUI dev proxy config: `webui/vite.config.ts`
- Tests mirror the `nanobot/` package structure.
