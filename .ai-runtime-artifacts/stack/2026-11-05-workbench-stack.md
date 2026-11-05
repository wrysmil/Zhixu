---
artifact: stack
route: brainstorming
skills: [source-driven-development, brainstorming]
skills_evidence:
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/source-driven-development/SKILL.md
source: [webui/package.json, pyproject.toml, webui/node_modules/react/package.json, webui/node_modules/vite/package.json, webui/node_modules/typescript/package.json]
created_at: 2026-11-05
---

# 工作台技术栈核对

| 项目 | 声明 | 本地安装核对 |
| --- | --- | --- |
| React | ^18.3.1 | 18.3.1 |
| Vite | ^5.4.11 | 5.4.21 |
| TypeScript | ^5.7.2 | 5.9.3 |
| Tailwind CSS | ^3.4.17 | 本轮未核对安装版本 |
| Python | >=3.11 | 本轮未核对解释器版本 |
| nanobot-ai | 0.3.0 | 项目声明版本 |

数据链路为 React 会话消息派生模型 + /api/sessions/:key/workbench HTTP 快照，并监听 WebSocket 事件触发刷新。当前仅核对已有设计依赖，尚未做框架实现决策。
