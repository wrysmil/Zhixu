---
artifact: implementation-plan
route: writing-plans + orchestration
skills: [writing-plans, systematic-debugging, test-driven-development, verification-before-completion]
skills_evidence:
  - /Users/mima0000/.agents/skills/writing-plans/SKILL.md
  - /Users/mima0000/.agents/skills/test-driven-development/SKILL.md
  - /Users/mima0000/.agents/skills/verification-before-completion/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-11-02-chat-activity-feedback-dispatch.md
source: [.ai-runtime-artifacts/specs/2026-11-02-chat-activity-feedback-spec.md, AGENTS.md]
created_at: 2026-11-02
status: completed
approved: true
approval_basis: 按用户本会话追加要求与自主执行指令推进本地可逆修改。
---

# 聊天执行反馈实施计划

**Goal:** 消除执行中的静默观感，使步骤目的可见并改善回复区位置。

**Architecture:** 复用现有 turn 生命周期、file_edit 事件和 ThreadMotionCoordinator；工作单元文件不重叠，不新增依赖。

**Tech Stack:** React 18.3.1、TypeScript 5.9.3、Vite 5.4.21、Python asyncio、Vitest 2.1.9、pytest。

## WU-01 运行标记与可读活动区（Leader）

1. 先补 shell/MessageBubble 运行状态失败测试，并确认 RED。
2. 最小实现标题 spinner、正文末尾状态，复用 footer 几何与本地化。
3. 先补展开活动区可读性测试，再调整活动视口与详情布局。
4. 跑相关聊天组件测试与触及文件 ESLint；记录 GREEN。

## WU-02 回复区上移（viewport_alignment）

1. 先补本地新回合、长输出、完成留白与用户上翻测试并观察 RED。
2. 调整 ThreadViewport 底部留白，复用现有滚动协调器。
3. 跑 viewport/motion/camera 测试与 ESLint。

## WU-03 文件参数生成可见性（tool_argument_visibility）

1. 先补普通工具参数流与 pending file_edit 生命周期测试并观察 RED。
2. runner 转发既有 provider event，由文件活动 hook 发 pending 并处理终态。
3. 跑 runner/hooks 相关 pytest、ruff 与类型检查。

## WU-04 工具动作标题（semantic_activity_titles）

1. 先补显式描述、命令目的回退、安全脱敏与状态变更测试并观察 RED。
2. 优先采用真实描述，必要时兼容可选 shell 描述参数。
3. 跑活动模型相关 Vitest/ESLint，涉及 Python 时补 pytest/ruff。

## 集成与交付

- 逐项整合，运行聊天相关测试、构建、触及文件 lint、Python 针对性验证。
- 真实浏览器检查桌面和窄屏，保存过程截图与 verdict。
- 独立审查各单元及事件数据安全；修复确认问题后落盘集体测试与执行日志。
- 当前工作区大量记忆/评测未提交改动属于其他工作，保留，不掺入本任务。
- 未经请求不提交或推送。

## Next

四个工作单元已集成并通过浏览器验收与全量单测；证据见 `.ai-runtime-artifacts/verifications/2026-11-02-chat-activity-feedback-verification-lite.md` 与集体测试。未经请求不提交。
