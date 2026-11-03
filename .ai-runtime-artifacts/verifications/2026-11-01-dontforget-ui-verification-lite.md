---
artifact: verification-lite
route: Tier 1 Leader 直做
created_at: 2026-11-01
status: completed
skills_evidence:
  - /Users/mima0000/.agents/skills/verification-before-completion/SKILL.md
  - /Users/mima0000/.agents/skills/requesting-code-review/SKILL.md
---

# Dont Forget 桌面原型验证

本轮补充：按用户要求，将首页标题、说明、输入提示、快捷建议和最近对话标题改为记忆主题，并同步更新抽离计划的现状、正式 UI 任务和 Logo 接入任务。再次运行同一浏览器脚本：退出码 0，11 项交互、50 组布局通过，无 page error；已查看更新后的首页截图。计划自检确认文案与 HTML 一致、Task 5b/5c 顺序正确、资产保护与迁移未完成状态明确、代码块配对完整。本轮未开始正式应用迁移。

## 变更文件

`/Users/mima0000/Documents/学习-001/do-project/dontforget/design-demo/index.html`。

由展示型首页改为会话工作台：精简导航、最近会话、按需右栏、中性主题、系统字体。保留原有功能路由，补齐核心演示交互。

## 命令与结果

- `node` + `vm.Script` 对 HTML 内脚本做语法检查：通过。
- `node .ai-runtime-artifacts/verifications/2026-11-01-dontforget-ui/browser-check.cjs`：退出码 0。
- 输出：`PASS 11 interaction checks, 50 layouts, zero page errors`。
- 宽度：1440 / 1024 / 768 / 390 / 320px。
- 路由：new / chat / memory / identity / agents / skills / automations / workbench / settings / connect。
- 50 组检查未发现 document/main 横向溢出；开发工具标签有意使用容器内横向滚动。
- 手动查看生成截图：新版首页、对话及记忆侧栏、390px 窄屏；检查信息层级、边界、输入区和导航。

## 交互覆盖

1. 首屏精简与空消息禁用发送。
2. 建议填充及主题/记忆切换后草稿保留。
3. Enter 发送、Shift+Enter 换行、中文输入法防误发。
4. 历史和新会话独立保存演示内容。
5. 按需记忆栏开关。
6. 附件名称添加/移除；不读取或上传文件。
7. 会话与记忆搜索、空结果、Esc 与焦点恢复。
8. 记忆筛选、技能键盘开关、MCP 设置入口。
9. 浏览器后退到新对话后建立独立会话，最近列表更新。
10. 390px 助手详情默认关闭，可用关闭按钮或 Escape 关闭。
11. 320px 侧栏、会话导航、记忆栏。

## 独立审查与修正

只读 reviewer 发现三项问题，均已修正并新增浏览器检查：

- 助手配置栏窄屏无法关闭 → 独立显隐状态、关闭按钮、Escape。
- 历史返回新对话可能沿用旧 sessionId → 在路由渲染中恢复新会话状态。
- 新会话排在最末且首页不显示 → 稳定索引 + 独立活动顺序数组。

用户消息、会话标题、附件名及搜索结果均做 HTML 转义。原有创建、保存、连接和编译提示改为演示未接入说明。

## 证据与限制

- 原版备份和调研：`.ai-runtime-artifacts/research/2026-11-01-dontforget-ui/`。
- JSON、浏览器检查脚本和前后截图：`.ai-runtime-artifacts/verifications/2026-11-01-dontforget-ui/`。
- 这是静态交互原型，不含模型调用、真实记忆读写、文件上传或服务连接。刷新清空会话，只有主题存储于 localStorage。
- 未运行 zhixu Python/WebUI 构建和测试：该文件不属于这些构建入口；相关源码没有改动。
- 浏览器检查不等价于完整 WCAG 认证；未运行系统读屏器或 Electron 真机。
