---
artifact: implementation-plan
route: writing-plans -> orchestration
skills: [writing-plans, orchestration, api-and-interface-design]
source: [2026-10-15-agent-workspace-spec.md]
created_at: 2026-10-15
approved: true
approval_basis: 用户要求优化页面并自主执行，补充范围都有
dispatch: 2026-10-15-agent-workspace-dispatch.md
status: completed
---
# Agent工作台实施计划

**Goal:** 统一优化新对话首页、聊天、记忆及共享页面。
**Architecture:** 保留生产控制器和路由，复用共享欢迎区与设置框架，预览沿用隔离入口。
**Tech Stack:** React 18.3.1、Vite 5.4.21、TypeScript 5.9.3、Tailwind 3、Vitest 2；npm为本机可用工具，bun不可用。

- [x] WU01：先验证现有thread/preview/navigation测试；实现共享欢迎区、文字导航、绿色主题和聊天样式，新增示例填草稿/导航行为测试；运行相关测试。
- [x] WU02：先验证memory/preview-settings测试；改善记忆说明、分类和预览本地搜索，保留真实CRUD；新交互测试；运行相关测试。
- [x] Leader：下载unDraw并改为绿系、本地资源，记录来源及许可；准备Iconify Lucide说明。
- [x] 整合：将worktree变更通过git diff/apply复制到当前工作区（不覆盖已有无关未跟踪文件），启动预览，桌面/窄屏/深色视觉复核。
- [x] 尾盘：全量npm test、lint、build；独立审查后修复；验证与执行日志落盘。

## 验证发现的有界修复
原始主checkout执行npm run lint确认已有7个错误（ArticleEditor未用参数、creative测试未用type/参数、i18n-dock-parity的any）。追加WU03只消除未用绑定和收窄测试类型，不改运行行为，不升级依赖。现有creative与i18n测试锁定行为，修后运行它们及lint。全量初跑1520通过/1失败：失败为WU01正在加入主题按钮的先失败测试，完成接线后重跑。

## Next
按契约并行实施并交付可查看的本地预览。
