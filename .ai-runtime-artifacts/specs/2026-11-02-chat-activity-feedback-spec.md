---
artifact: spec
route: systematic-debugging + brainstorming
skills: [systematic-debugging, brainstorming, source-driven-development]
skills_evidence:
  - /Users/mima0000/.agents/skills/systematic-debugging/SKILL.md
  - /Users/mima0000/.agents/skills/brainstorming/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
source: [用户本会话六组聊天活动截图, AGENTS.md, harness-kit/core/routing.md]
created_at: 2026-11-02
status: in-progress
approved: true
approval_basis: 用户要求修复并追加具体交互要求；按 AGENTS 自主执行指令继续有界本地修复。
---

# 聊天执行反馈与活动区可读性

## 目标

模型仍在运行、正文暂停或准备写文件时，用户可立即识别当前任务尚未结束；每条工具记录应优先说明动作目的；新回合的回复区靠近聊天视口顶部，活动区不再挤在低对比度的小框中。

## 证据

- ThinkingReasoningShell 只有淡色文字 pulse，工具完成后没有明确动态标记。
- MessageBubble 有正文后，未结束的回合 footer 仅预留空间或显示时间，没有运行反馈。
- runner._provider_tool_event 丢弃普通函数工具流；FileEditActivityHook 到 before_execute_tool 才开始，因此大型写文件参数的生成过程不可见。
- ThreadViewport 默认跟随 maxScrollTop，短回复没有留白，无法将回复起点上移。
- ActivityStep 是单行省略标题，ReasoningRow 默认以原始推理预览为标题；外层活动视口最高 180px，展开详情仍嵌套小滚动框。

## 设计取舍

仅加强 spinner 改动最小，但不能解释文件生成和每一步目的；只增加详细日志会增加噪声。采用已有事件与组件的小范围补足：明确动态状态、提前文件准备事件、真实动作摘要、更宽松的活动布局与回合留白。

## 约束与验收

1. 活动区无详情、展开、收起时均显示运行指示；结束即消失；尊重 reduced-motion。
2. 当前正文暂停但 turn 未结束时，正文末尾仍显示本地化运行状态；不声称模型正在思考或任务必定健康。
3. 文件编辑工具名已知但参数未完整时，复用既有 pending file_edit；不输出文件正文/参数片段；执行开始复用同调用标识，失败取消收口。
4. 本地新回合预留回复高度，短内容靠上；用户上翻后不抢滚动；历史恢复不强制上移；完成不突然回落。
5. 记录标题优先采用显式任务描述，无描述才用可确定的动作，原始命令留详情。不生成虚构进度或百分比。
6. 扩大活动可见高度，正文和当前步骤可读；收起行为、错误态、链接和文件预览保持有效。

## Spec 自检

范围限于聊天活动与事件可见性；不涉及执行器功能、模型调用授权、记忆与评测。测试覆盖运行/完成、流暂停、文件准备、用户滚动与历史恢复。

## Next

按本会话自主执行授权继续实施、验证并审查，不自动提交或推送。
