---
route: superpowers:brainstorming
date: 2026-10-09
topic: desktop-mira
approved: true
approval_quote: "基于本分支新创建一个分支，参考hope Agent的 页面风格……你想想把，然后webui不能去除保留，然后桌面版也要保留。然后logo不要再用nanobot了……换一个新的名字"
---
# Mira 桌面版方案（spec）

## 需求
1. 基于 feature/memory-system 新分支（→ feature/desktop-app）。
2. 参考 Hope Agent 页面风格做本项目的桌面版；webui 保留，桌面版并存。
3. 弃用 nanobot 品牌：新名字 + 新 logo。

## 调研结论
- Hope Agent（本机已装于 %LOCALAPPDATA%/ai.hopeagent.desktop）= Tauri 壳 + React 前端；
  窗口：标准边框、1440×900、min 840×520、白底浅色（hsl(0 0% 100%)）、shadcn 色板。
- 本机无 Rust 工具链 → Tauri 不可行；**Electron 是最快的 webui→桌面版路径**。
- 现有 webui 已是 shadcn/tailwind 浅色体系，与 Hope 风格天然接近；页面布局复用 webui，
  桌面壳负责：拉起/复用 `nanobot gateway`（127.0.0.1:8765）、注入 bootstrapSecret、
  Mira 品牌窗口与图标。

## 品牌
- 新名字：**Mira**（Agent-With-Memory → "miracle/mirror/记忆"意象）。
- 新 logo：teal(#14b8a6)→indigo(#6366f1) 渐变圆角方块 + 白色记忆轨道环与星点。
  资产：webui/public/brand/mira_*（svg/png）、desktop/assets/icon.png。
- 范围：所有用户可见品牌字符串（i18n 10 语言、index.html、manifest、sw precache、
  Sidebar logo、桌面壳）；CLI 命令字面量 `nanobot gateway`、Python 包名、
  localStorage key、内部标识符保留（改包名属独立大重构，见 decision）。

## 验收
- webui 构建 + 测试通过（断言同步更新）。
- desktop：`npm run smoke` 通过；gateway 就绪后窗口加载 webui。
- 旧 nanobot 品牌图删除，页面/标题/图标全部呈现 Mira。

## Next
- 实现（本 goal 一次性授权，approved: true）→ 验证 → 提交 feature/desktop-app。
