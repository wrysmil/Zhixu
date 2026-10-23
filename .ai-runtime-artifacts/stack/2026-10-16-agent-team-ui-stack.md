---
artifact: research-report
route: source-driven-development
skills:
  - source-driven-development
skills_evidence:
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/source-driven-development/SKILL.md
source:
  - webui/package.json
  - webui/node_modules
  - harness-kit/core/routing.md
created_at: 2026-10-16
status: detected
---

# Teams UI 技术栈核验

以下为当前已安装依赖的精确版本，不把 package.json 的版本范围当作实际版本。

| 依赖 | 精确版本 | 本次用途 |
| --- | --- | --- |
| react | 18.3.1 | 现有页面与配置状态 |
| react-dom | 18.3.1 | 渲染 |
| typescript | 5.9.3 | Team 与 Agent 类型约束 |
| vite | 5.4.21 | 构建、预览 |
| tailwindcss | 3.4.19 | 布局、主题、响应式 |
| @radix-ui/react-dialog | 1.1.15 | 现有弹窗交互基础 |
| lucide-react | 0.469.0 | 现有图标 |

Iconify 尚未安装；建议从已核验条目导出所需 SVG 并本地集成，无需为单个图标安装整个集合。
现有 TeamDefinition 不含徽记字段，稳定派生徽记可避免修改持久化协议；自选徽记需要前后端扩展。

本轮仅调研和生成候选插画，没有安装依赖或改动生产代码。

