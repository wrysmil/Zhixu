---
artifact: verification-lite
route: brainstorming
skills:
  - brainstorming
  - frontend-design
  - source-driven-development
  - verification-before-completion
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/brainstorming/SKILL.md
  - C:/Users/Huangqh/.codex/skills/frontend-design/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - 用户要求启动项目，检查聊天右侧工作台，并先给 Demo
created_at: 2026-10-21
tier: 1
---

# 会话右侧工作台 — 调查与 Demo 验证

## 范围

仅新增 `.ai-runtime-artifacts/` 下的研究原型、截图、检查会话脚本及记录。未修改生产源码，未提交或推送。

## 验证与结果

| 检查 | 结果 |
| --- | --- |
| 启动项目网关，打开真实 React 前端 | 成功显示检查环境的会话及真实 SessionWorkbench |
| 实际桌面工作台、工具坞切换 | 实际查看并截图；工具坞覆盖同一右侧区域 |
| 实际窄窗口 | 确认工作台隐藏，工具开关选中后没有可见面板 |
| 正确侧栏 Demo 的进展/记忆/能力切换 | 通过浏览器界面操作验证 |
| 工作记忆详情 | 显示来源及尚未长期保存的状态 |
| Skill 与 MCP 区分 | 显示 Skill 已加载、MCP 已调用及已连接未调用的不同状态 |
| 模拟完成运行任务 | 2/4 变为 3/4，运行计数归零，当前任务计划完成，子 Agent 展示同步变化 |
| 重置、主题及侧栏宽度 | 通过界面检查；最终恢复默认浅色布局并保留 Demo 标签 |
| 截图 | 保存 session-progress.png、session-memory.png、session-tools.png 等 |
| git diff --check | 通过；git diff 无生产文件改动 |

早期 `2026-10-21-workbench-demo-check.cjs` 检查的是误向的首页草稿，不能作为本次 `session.html` 的交互验证证据。本次正确侧栏验证来自浏览器实际操作。

## 未验证项

- 真实模型执行、真实 MCP 连接及工具调用、真实 Skill 生命周期、长期记忆写入。
- 正式业务数据接入、完整移动端回归及生产构建；本轮仅调查和静态原型。

## Next

交付当前项目调查结论与可点击侧栏 Demo，等待布局反馈；不把静态演示描述为功能接入完成。
