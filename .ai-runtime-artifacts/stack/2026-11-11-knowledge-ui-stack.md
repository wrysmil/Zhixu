---
artifact: stack-detection
route: roles.web-investigator
skills: [source-driven-development]
skills_evidence: [.claude/skills/source-driven-development/SKILL.md]
source: [webui/package.json, webui/node_modules]
created_at: 2026-11-11
---

# 知识库双页视觉调研：当前技术栈

本轮只调研和制作独立布局示意，未安装组件、修改依赖或业务代码。以下精确版本读取自本机 `webui/node_modules/<包>/package.json`，不是把声明范围视为精确版本。

| 依赖 | 本机版本 |
| --- | --- |
| React / React DOM | 18.3.1 |
| TypeScript | 5.9.3 |
| Tailwind CSS | 3.4.19 |
| Vite | 5.4.21 |
| lucide-react | 0.469.0 |
| @radix-ui/react-dialog | 1.1.15 |
| @radix-ui/react-dropdown-menu | 2.1.16 |
| d3-force | 3.0.1 |

现有 UI 目录已有 Button、Input、Combobox、Dialog、Sheet、Popover、Dropdown Menu、Tooltip、Segmented Control 等。尚无项目声明的 TanStack Table 或 Recharts。引入新组件时优先复用现有 Radix + Tailwind 模式；shadcn 官方当前文档包含 Base UI 与 Radix 两条入口，应核对 Radix 对应实现及 React 18 兼容性，不直接套用 latest 或整体升级。
