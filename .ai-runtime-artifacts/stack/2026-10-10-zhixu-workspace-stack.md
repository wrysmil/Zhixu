---
artifact: research-report
route: source-driven-development
skills:
  - source-driven-development
skills_evidence:
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.12/skills/source-driven-development/SKILL.md
source:
  - webui/package.json
  - webui/node_modules 下对应依赖的 package.json
  - desktop/package.json
  - pyproject.toml
  - harness-kit/project.profile.md
created_at: 2026-10-10
status: verified
---

# 知序 UI 设计：技术栈核对

## 前端

声明范围不等于实际安装版本，以下分别记录。实际版本来自当前 checkout 的 `webui/node_modules`，不声称是远程环境版本。

| 组件 | 依赖声明 | 当前安装版本 |
| --- | --- | --- |
| React / React DOM | ^18.3.1 | 18.3.1 / 18.3.1 |
| Vite | ^5.4.11 | 5.4.21 |
| TypeScript | ^5.7.2 | 5.9.3 |
| Tailwind CSS | ^3.4.17 | 3.4.19 |
| lucide-react | ^0.469.0 | 0.469.0 |
| Radix Dialog | ^1.1.4 | 1.1.15 |
| Vitest | ^2.1.8 | 2.1.9 |

## 其他层

- Python 包：`nanobot-ai` 0.3.0，Python 要求 >=3.11；异步 Agent 与 gateway。
- 桌面壳：Electron，声明 ^33.2.0；electron-builder 声明 ^25.1.8。本轮未核对桌面依赖的实际安装版本。
- 当前分支：`feature/desktop-app`，来自本轮只读 Git 查询。
- WebUI 现有样式：中性灰阶、蓝色焦点、小圆角；系统字体栈已经包含中文无衬线回退。

## 设计影响

1. 本阶段仅产出需求和页面设计，不安装依赖、不修改现有组件。
2. 推荐下一阶段用独立 HTML/CSS/JavaScript 演示原型验证布局；产物放 `.ai-runtime-artifacts/research/zhixu-prototype/`，无需接入 gateway 或凭据。
3. 未来集成沿用当前 React 18、Tailwind 3 和 Radix，不以“升级框架”作为 UI 重设计前提。
4. 本轮没有写框架 API 实现；官方 API 文档验证留在涉及具体框架用法的实施阶段，不把记忆中的 API 行为作为本轮证据。

## 证据

读取 `webui/package.json`、安装依赖中的 `package.json`、`desktop/package.json` 与 `pyproject.toml`，实际安装版本查询成功。未执行应用构建，不能据此声称现有应用构建通过。
