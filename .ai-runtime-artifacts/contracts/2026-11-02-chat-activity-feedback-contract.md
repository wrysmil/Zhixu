---
artifact: contract
route: api-and-interface-design
skills: [api-and-interface-design]
skills_evidence: [.claude/skills/api-and-interface-design/SKILL.md]
source: [.ai-runtime-artifacts/specs/2026-11-02-chat-activity-feedback-spec.md]
created_at: 2026-11-02
---

# 单元间契约

- 运行标记只依赖现有 isTurnStreaming/message.isStreaming；不新增定时假消息或状态轮播。
- 文件准备复用现有 file_edit start + pending 与稳定调用标识；不得流出 arguments_delta 文件内容，不改变 WebSocket 事件结构。
- 语义标题来自显式描述或可判定的命令类型；详情继续使用现有脱敏函数；未确定的动作保守回退。
- 回复区留白只在 ThreadViewport 内处理，保持 ThreadMessages、ThreadMotionCoordinator 外部接口。
- 活动区改动只调整布局与可见反馈，不更改工具成功/失败判定。

## Next

各 WU 按文件所有权实施，接口变更先上报 Leader。
