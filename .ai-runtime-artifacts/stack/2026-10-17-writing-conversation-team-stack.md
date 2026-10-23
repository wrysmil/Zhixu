---
artifact: research-report
route: source-driven-development
skills: [source-driven-development]
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
source:
  - pyproject.toml
  - webui/package.json
  - webui/node_modules
  - .venv
  - harness-kit/project.profile.md
created_at: 2026-10-17
status: verified
---

# 创作空间对话团队改版：技术栈检测

本轮为调研与方案设计，不安装依赖、不修改业务代码、不调用真实模型。

## 当前基线

- 工作目录：`/Users/mima0000/Documents/学习-001/do-project/zhixu`。
- 分支：`feature/desktop-app`；读取时 HEAD：`3d10ef4`。
- `project.profile.md` 中的 `feature/memory-system` 为历史画像；本轮以当前源码和 Git 为准。
- 工作区已有未提交业务改动，本轮保留；交付仅新增本主题过程文档。

## 版本

| 项目 | 依赖声明 | 本机实际读取 |
| --- | --- | --- |
| nanobot-ai | 0.3.0 | 0.3.0 |
| Python | >=3.11 | 项目 `.venv`：3.13.9 |
| Pydantic | >=2.12.0,<3.0.0 | 2.13.5 |
| aiohttp | >=3.9.0,<4.0.0 | 3.14.3 |
| MCP Python SDK | >=1.26.0,<2.0.0 | 1.30.0 |
| pytest | >=9.0.0,<10.0.0 | 9.1.1 |
| React / react-dom | ^18.3.1 / ^18.3.1 | 18.3.1 / 18.3.1 |
| TypeScript | ^5.7.2 | 5.9.3 |
| Vite | ^5.4.11 | 5.4.21 |
| Vitest | ^2.1.8 | 2.1.9 |
| Tailwind CSS | ^3.4.17 | 3.4.19 |
| diff | ^9.0.0 | 9.0.0 |

实际 Python 包版本来自项目解释器的 `importlib.metadata`；前端版本来自各包 `package.json`，没有把声明范围当作精确版本。

## 影响设计的既有能力

- Markdown 编辑器为原生 `textarea`，已有编辑、预览、分屏、IME、保存与恢复处理。
- 通用聊天有 `ThreadComposer`、命令菜单、提及菜单、消息流和团队会话绑定。
- `@` 菜单目前主要支持会话、CLI 应用、MCP，不能直接视为已支持团队成员。
- `TeamLeaderTurns`、`TeamDispatchTool`、`TeamMemberExecutor` 已接入共享 Agent 执行路径。
- 文章、素材、提纲由 SQLite 文章仓储持久化；已有 revision、digest、请求记录及建议采纳事务。
- 有内置资产登记和团队播种方法；生产初始化接线、管理页来源投影仍需完成。

## 设计约束

沿用现有 React、Python asyncio、SQLite 与 WebSocket 架构。无需引入新的多 Agent 框架、工作流引擎或富文本编辑器。工具作用域与团队对话接线需要局部扩展现有宿主接口，不另建模型调用循环。
