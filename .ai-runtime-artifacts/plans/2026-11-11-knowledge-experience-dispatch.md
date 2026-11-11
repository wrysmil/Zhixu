---
artifact: implementation-dispatch
route: superpowers:writing-plans
plan: .ai-runtime-artifacts/plans/2026-11-11-knowledge-experience-plan.md
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - C:/Users/Huangqh/.codex/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/api-and-interface-design/SKILL.md
source:
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/contracts/2026-11-11-contract-knowledge-experience.md
created_at: 2026-11-11
status: in-progress
approved: true
---

# 知识工作区体验改版：执行图

当前只描述执行安排，不派发worker。用户确认后按项目编排要求先隔离实现工作区，写调度跟踪；具体路径由当时实际分支与脏工作区检查决定，不能假定主checkout可安全覆盖。所有worker cwd为该worktree，worktree_path/branch在派发前由Leader写入跟踪记录。

## GROUP-0：上下文与基线

Leader执行Task0，复核已有知识图谱与评测代码，建立变更基线，固定本契约。无worker先行编码。

## GROUP-1：共享体验层

- WU-01｜标题：统一视觉与交互基础｜wu_type: ui｜agent_role: coder｜workspace_scope: wu｜依赖：GROUP-0｜文件：knowledge-shared/*、knowledge-experience.test.tsx｜done：契约导出一致、基础行为通过；冻结共享样式供下一组消费。

## GROUP-2：双页并行

- WU-02｜标题：知识库资料管理体验｜wu_type: ui｜agent_role: coder｜workspace_scope: wu｜依赖：WU-01｜文件：KnowledgePage.tsx、KnowledgeLibraryList/DocumentTable/DocumentSheet/ImportSheet.tsx、knowledge-page.test.tsx｜禁止：GraphPanel/GraphView、locales、lib/api.ts｜done：列表/上传/详情/检索/返回与异步守卫通过。
- WU-03｜标题：评测记录与报告体验｜wu_type: ui｜agent_role: coder｜workspace_scope: wu｜依赖：WU-01｜文件：KnowledgeEvalPage.tsx、knowledge-eval组件与三个新Sheet、knowledge-eval-page.test.tsx｜禁止：locales、lib/knowledge-eval-api.ts｜done：题集/新建/运行/取消/报告/对比通过，指标不变量保留。

WU-02/03不得自行修改共享层；问题交Leader安排WU-05处理。新增翻译键及文案在各自execution-log交接，统一由WU-05写locales。

## GROUP-3：图谱收口

- WU-04｜标题：图谱选择与证据反馈｜wu_type: ui｜agent_role: coder｜workspace_scope: wu｜依赖：WU-02｜文件：GraphPanel.tsx、GraphView.tsx、对应graph测试｜done：节点选择/展开/证据/错误、reduce检查通过。可在WU-03仍运行时推进，写集不相交。

## GROUP-4：文案、联调与验证

- WU-05｜标题：统一文案与动效联调｜wu_type: ui｜agent_role: coder｜workspace_scope: wu｜依赖：WU-02/03/04｜文件：locales/*/common.json、knowledge-shared/*、两页少量集成修正、knowledge-experience.test.tsx｜done：暗色/窄屏/键盘/reduce/快速切换一致，无遗留遮罩及重复请求。
- WU-06｜标题：整体验收与审查｜wu_type: e2e｜agent_role: test-engineer｜workspace_scope: wu｜依赖：WU-05｜写集：本轮verifications目录｜done：动态录屏、状态矩阵与测试证据。reviewer/security-auditor分别独立只读审查，Leader整合并落盘collective-test、code-review、security-review、execution-log。

## WU 上下文与返回格式

所有WU必读计划相关Task、本契约与原research；实现时按项目可用路径加载 source-driven-development、frontend-ui-engineering、test-driven-development 和 verification-before-completion，对技能缺失记录实际降级。不得凭本文件声称这些执行阶段技能已加载。

派发消息必含实际worktree_path、branch、允许文件、禁止项、WU技能与done criteria。返回使用中文并包含：完成行为、实际改动文件、验证命令与结果、截图/录屏路径、未解决项、文案键表、`### Skills 使用`。未通过行为/业务不变量不得仅凭漂亮截图完成WU。

## Next

- 确认后说「开始实现」或「并行执行」。
- 调整任务内容改plan；调整依赖/owner改本文件并同步契约。
