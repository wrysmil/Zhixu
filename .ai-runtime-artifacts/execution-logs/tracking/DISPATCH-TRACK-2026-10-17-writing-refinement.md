---
artifact: dispatch-track
route: web-investigator -> superpowers:brainstorming
skills: [brainstorming, source-driven-development, find-skills]
skills_evidence:
  - /Users/mima0000/.agents/skills/brainstorming/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - /Users/mima0000/.agents/skills/find-skills/SKILL.md
source:
  - 用户要求细化固定创作团队方案、查找并下载可复用 Skill/提示词、实际测试前端
  - .ai-runtime-artifacts/specs/2026-10-17-writing-team-sidebar-spec.md
created_at: 2026-10-17
status: complete
---

# 创作团队方案细化：只读并行调查

本批是调研、现状验证和设计细化，不实施固定团队，不修改业务代码，不激活写作运行时，不建实现 worktree。

| 子任务 | 独占范围 | 约束 | 完成条件 |
| --- | --- | --- | --- |
| Skill 调研 | 网络、SkillHub 和上游许可；仅消息返回 | 不下载到全局 skill 目录、不执行第三方脚本、不改文件 | 提供可下载文件 URL、来源、许可、内容适配判断 |
| Team 接入核对 | 当前 Team/Agent/写作传输代码，只读 | 不改业务、不另起执行框架 | 返回具体接入点、状态/续接/事件约束及行号 |
| 前端基线测试 | 现有 Vitest、lint、build；仅新验证报告 | 不修业务代码，不与 Leader 共用浏览器 | 命令结果、失败分类和明确未验证范围 |
| Leader | Skill 静态资产归档、浏览器体验、既有 spec 更新 | 所有过程资产留在 `.ai-runtime-artifacts/` | 可审阅的修订方案、下载清单与前端测试证据 |

用户要求去掉作者展示；清理宣传、可选署名与社交链接，许可要求的版权/NOTICE 另行保留，不执行未知脚本或宣称已得到额外授权。

## 完成记录

- Skill 调研：实际核对 SkillHub 与上游，筛选五类候选；Leader 下载两份纯 Markdown 核心 Skill、许可和版本证据，共六个源文件，附 SHA-256 清单，整理三份非激活提示词草案。
- Team 核对：确认 factory 缺失、会话绑定入口、跨批交接、retry 限制、Skill/模型冻结缺口、长稿摘要截断及取消/超时边界；已整合到 spec v2 §13。
- 前端自动化：1636 通过、1 失败、2 跳过；创作 126 项与 Team 专门 88 项通过；lint/build 通过。唯一共享 i18n 失败独立复现，未改源码。
- Leader 浏览器：实际验证预览编辑/素材/提纲/分屏，二次复现预览新建重复记录并追到双重插入数据流；四张截图保存在验证目录。
- 交付：既有 spec 升至 v2，下载包 README、自动化报告、浏览器报告与本跟踪记录落盘。业务源码/测试/依赖未改，未创建实施计划，未提交或推送。
