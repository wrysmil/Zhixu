---
artifact: spec
route: interview-me
skills:
  - interview-me
skills_evidence:
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/interview-me/SKILL.md
source:
  - 用户本轮请求及外观设置参考图
  - AGENTS.md
  - harness-kit/core/routing.md
  - webui/src/hooks/useTheme.ts
  - webui/src/components/settings/overview/OverviewSettings.tsx
  - webui/src/globals.css
  - webui/index.html
created_at: 2026-10-19
status: confirmed
confidence: 98
confirmed: true
approved: true
---

# 外观自定义：已确认需求

用户希望为知序增加参考图所示的主题颜色、字体等自定义能力。用户于本会话选择「全局外观：预设主题 + 自定义颜色 + 字体（推荐）」，已确认第一版功能范围。本文件的批准仅指需求范围确认，不代表后续设计方案或实施计划已批准。

## 已核实的项目基础

- 现有设置导航包含 appearance，已有 AppearanceSettings 组件。
- useTheme 支持 light/dark；没有可持续跟随系统变化的 system 模式。
- 全局样式使用 CSS 语义变量，包含背景、前景、强调色、卡片、侧栏、边框等。
- 中文字体在 globals.css 中单独指定；仅修改 Tailwind 字体配置不足以完成全局自定义。
- index.html 在 React 初始化之前恢复明暗模式；扩展配色时需同时考虑启动阶段的外观一致性。
- 本轮未修改业务代码，也未运行测试。

## 已确认范围与方案约束

- 目标：在「设置 → 外观」中调整配色与字体，让用户获得适合自己的阅读和使用体验。
- 使用者：知序 WebUI 与桌面端界面的用户。
- 第一版：全局预设主题、自定义强调色/背景色/文字色、字体选择、浅色/深色/跟随系统。
- 体验预期：修改即时可见，本地保存，刷新及相同应用来源下重新启动后保留，并可恢复默认。
- 字体边界建议：界面/正文与代码字体分开管理，中文字体具有回退顺序。
- 暂不纳入推荐第一版：跨设备同步、任意 CSS 注入、在线字体下载、完整主题导入导出。

## 确认记录

结构化问题：第一版采用全局外观、仅聊天区域，还是完整主题编辑器？

用户选择原文：「全局外观：预设主题 + 自定义颜色 + 字体（推荐）」。

## Next

- 进入 brainstorming，完成技术栈记录及外观方案设计。
- 设计产物：2026-10-19-appearance-customization-spec.md，须独立确认后进入下一阶段。
