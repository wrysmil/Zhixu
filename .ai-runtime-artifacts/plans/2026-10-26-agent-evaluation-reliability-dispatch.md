---
artifact: implementation-dispatch
route: superpowers:writing-plans
plan: .ai-runtime-artifacts/plans/2026-10-26-agent-evaluation-reliability-plan.md
skills: [writing-plans, api-and-interface-design]
skills_evidence: [.claude/skills/writing-plans/SKILL.md, .claude/skills/api-and-interface-design/SKILL.md]
source:
  - .ai-runtime-artifacts/contracts/2026-10-26-contract-evaluation-reliability.md
created_at: 2026-10-26
status: approved
approved: true
---

# Agent评估可靠性执行图

这是待审执行图，本轮不派实施worker、不创建worktree。任务步骤、测试与验收以plan为准。

## 前置

- 用户单独说开始实现后，Leader加载orchestration并执行WORKTREE-INIT；保留当前gateway启动修复和全部其他未跟踪文件，不复制运行缓存为业务改动。
- worktree_path由工具创建后的真实结果回填，不猜路径；worker cwd强制该目录。
- 所有WU中文派发。wu_type=bugfix/feature/test/docs；agent_role=implementer/test-engineer/reviewer/security-auditor；每个prompt注明独占文件、禁止项、契约、done criteria、Skills与返回格式。
- 实施Skills：source-driven-development、test-driven-development、incremental-implementation、verification-before-completion；API任务追加api-and-interface-design，UI追加frontend-ui-engineering。Leader提交时另加载git-xywh与project.git.md。
- 实施worker禁止commit/push；共享文件只能由当前所属WU写，计划中的依赖不等于文件锁。

## GROUP / WU

| GROUP | WU / plan task | 标题 | 主要文件所有权 | 依赖/执行 |
| --- | --- | --- | --- | --- |
| A | WU-01 / T01 | 修复基线与并发 | evaluation services/scheduler/repository/runtime_factory及对应测试；必要session路径 | 无，串行 |
| A | WU-02 / T02 | 类型、快照、迁移 | schemas/database/repository/services/manifests及对应测试 | WU-01后 |
| B | WU-03 / T03 | 完整Trace与身份 | worker/trace/ipc/usage/observability及subagent最小扩展 | WU-02后 |
| B | WU-04 / T04 | 隔离团队执行 | team_runtime/runtime_factory/worker/services、teams栈、gateway适配与测试 | WU-03后 |
| B | WU-05 / T05 | 模式与Skill环境 | runtime_factory/worker/services及模式测试 | WU-04后 |
| C | WU-06 / T06 | 强规则与合格 | assessment/rules/verdict/services及测试 | WU-05后 |
| C | WU-07 / T07 | Judge校准闭环 | judge_materials/judge/calibration/verdict/worker/services及测试 | WU-06后，避免verdict/services冲突 |
| D | WU-08 / T08 | 统一指标 | reports/observability/report_view/services及测试 | WU-07后 |
| D | WU-09 / T09 | 独立重复 | repository/scheduler/services/reports及测试 | WU-08后 |
| D | WU-10 / T10 | 配对比较 | comparisons/server/services/repository/report_view及测试 | WU-09后 |
| E | WU-11 / T11 | 页面闭环 | webui评估组件、evaluation-api、locale、前端测试 | WU-10后，可与12/13并行 |
| E | WU-12 / T12 | 36任务与反例 | samples、datasets、后端样本/导入导出测试 | WU-10后；不写schemas/services/UI |
| E | WU-13 / T13 | 独立启动与CI | cli/eval_commands、cli/commands、server、eval.html/evaluation-entry、vite.config、workflow/README/打包 | WU-10后；不写WU11组件/locale/API文件 |
| F | WU-14 / T14 | 集成验证 | verifications、execution-log；Leader整合 | WU11–13全部返回 |
| F | R-CODE | 独立代码审查 | 只读业务；写reviews/code-review | WU14测试后 |
| F | R-SEC | 独立安全审查 | 只读业务；写reviews/security-review | 与R-CODE可并行 |

GROUP A–D串行是有意安排：共享services/repository/worker/schema很多，不能为了多Agent而同时改同一套判分口径。
GROUP E只在接口冻结、独占文件明确后并行；新增契约字段统一交Leader，不可让前后端各补一版。

## 每WU完成条件

1. 引用冻结契约，说明实际文件与偏差；先红测再绿测，相关命令退出码可查。
2. 不能以mock全绿替代完整SDK/真实团队受控端到端；真实模型测试单独标示。
3. 返回`### Skills 使用`、变更、测试证据、风险、wu_status；已知失败不填done。
4. Leader检查diff和证据后整合；必要修复以新WU返工，不越过共享文件锁。
5. 最终collective-test/code-review/security-review/execution-log齐全才可宣布本批交付。

## Next

- 执行图确认后说「开始实现」或「并行执行」。
- 调整范围/先后顺序时同步修改plan；只改文件所有权/并行策略时改本文件即可。
