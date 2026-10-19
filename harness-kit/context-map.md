# Context Map

本文件由 Harness 初始化流程生成，用于帮助 AI 快速理解项目结构。迁移到新项目后，应通过 `harness-kit/init/project-profiler.prompt.md` 重新生成。

## 顶层结构

| 路径 | 类型 | 说明 |
| --- | --- | --- |
| `nanobot/` | Python | 核心包：agent 循环、providers、channels、tools、memory、session、gateway、web、api |
| `webui/` | Node/TS | Web SPA（React + Vite + Tailwind + Bun） |
| `tui/` | Node/TS | 终端 UI（Bun） |
| `packages/client-events/` | TS | 前端共享事件包 |
| `tests/` | Python | 测试树（镜像 `nanobot/` 结构） |
| `docs/` | 文档 | architecture / development / memory / deployment 等 |
| `scripts/` | 脚本 | `install_channel_dependencies.py`、发布脚本等 |
| `images/` | 资源 | README 封面 SVG |
| `harness-kit/` | 文档/配置 | Harness 规范、适配器、脚本（源仓库拷贝，勿当业务模块） |
| `.ai-runtime-artifacts/` | 产物 | AI 过程文档（spec/plan/log/review） |
| `.claude/`、`.agents/` | 工具 | Claude Code 平台投影：rules、agents、skills |

## 主要入口

| 入口 | 说明 |
| --- | --- |
| `nanobot/cli/commands.py` | CLI 入口（`nanobot` 命令） |
| `nanobot/nanobot.py` | Python SDK 入口 |
| `nanobot/gateway/` | gateway 服务（WebUI/WS 复用协议） |
| `nanobot/agent/loop.py`、`runner.py` | Agent 核心循环与 LLM 多轮对话 |
| `webui/src/` | SPA 源码；dev server 代理 /api、/webui、/auth、WS 到 gateway :8765 |
| `tui/` | 终端 UI 入口 |
| `AGENTS.md` | 工具中立顶层入口（Harness 覆盖层 + 项目指南） |
| `harness-kit/core/routing.md` | 路由表与阶段门禁 |

## 关键模块

| 模块 | 路径 | 说明 |
| --- | --- | --- |
| MessageBus | `nanobot/bus/queue.py` | 解耦 channels 与 agent core 的异步总线 |
| AgentLoop | `nanobot/agent/loop.py` | 消费 InboundMessage、构建上下文、协调 turn |
| AgentRunner | `nanobot/agent/runner.py` | LLM 多轮对话 + 工具执行 + 流式响应 |
| Memory | `nanobot/agent/memory.py`、`nanobot/memory/` | 会话历史持久化 + Dream 两阶段记忆巩固；正在扩展的 memory 子系统 |
| Session | `nanobot/session/` | 会话历史、上下文压缩、TTL 自动压缩、目标状态 |
| Providers | `nanobot/providers/` | Anthropic / OpenAI 兼容 / Responses API / Azure / Bedrock 等 |
| Channels | `nanobot/channels/` | Telegram / Discord / Slack / WeChat / WebSocket 等平台集成 |
| Tools | `nanobot/agent/tools/` | filesystem / shell / web / MCP / cron / subagent / long_task 等 |
| Gateway | `nanobot/gateway/` | WebSocket 多路复用协议服务 |
| WebUI | `webui/src/` | 页面、组件、API/WS 封装；memory 管理 API 正在接线 |
| API Server | `nanobot/api/server.py` | OpenAI 兼容 HTTP API |

## 待确认项

- `nanobot/memory/`（新 memory 子系统）与 `nanobot/agent/memory.py`（旧 Dream 实现）的职责边界与迁移状态。**已过期（2026-10-19 更正）**：该项原属 `feature/memory-system` 分支的当期工作，当前主线已切到 `feature/desktop-app`，结论待重新核对（参考 `docs/memory.md` 与 `.ai-runtime-artifacts/`）。
- WebUI 与 gateway 的 WS 协议扩展点（memory 操作是否复用现有 multiplex 帧类型）。
