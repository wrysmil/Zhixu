---
artifact: implementation-dispatch
route: superpowers:writing-plans
plan: .ai-runtime-artifacts/plans/2026-10-25-evaluation-observability-plan.md
skills: [writing-plans, api-and-interface-design]
skills_evidence:
  - /Users/mima0000/.agents/skills/writing-plans/SKILL.md
  - .claude/skills/api-and-interface-design/SKILL.md
source:
  - .ai-runtime-artifacts/plans/2026-10-25-evaluation-observability-plan.md
  - .ai-runtime-artifacts/contracts/2026-10-25-contract-evaluation-observability.md
created_at: 2026-10-25
status: active
approved: true
approval_evidence: "用户在 2026-10-25 单独要求：集成到项目中，后端也得考虑；并明确要求直接在当前分支修改。"
---

# 评估可观测性 — 待执行调度图

计划已获批准并进入执行。用户明确要求直接在当前分支修改，因此本轮跳过 WORKTREE-INIT；通过互斥文件所有权、共享文件串行交接和 Leader 最终整合避免覆盖当前分支上的其他在途改动。

| GROUP | WU | 责任 | 依赖 | 编排方式 |
| --- | --- | --- | --- | --- |
| 1 | WU-01 | 聚合、进度、来源、历史最新口径 | 无 | executor 串行；锁定 repository/reports/services |
| 2 | WU-02 | 真实身份、执行/父子关系、请求 ID、observer | WU-01 | executor 串行；涉及核心生命周期，先读再测试 |
| 3 | WU-03 | 图/工具/Token 统一读模型与 API | WU-02 | executor 串行；锁定 schemas/server/services/evidence |
| 4 | WU-04 | 前端画布、明细表、Token、国际化 | WU-03 契约固定 | 与 WU-05 并行，仅写 webui 文件 |
| 4 | WU-05 | HTML、SVG、打印、report 路由 | WU-03 契约固定 | 与 WU-04 并行，仅写后端报告相关文件 |
| 5 | WU-06 | 集成、回归、浏览器验证与尾盘 | WU-04/05 | Leader 整合，reviewer 与安全审查只读并行 |

所有执行派发使用中文，列明目标、拥有文件、禁止范围和 done criteria。必须告知执行 Agent：不是唯一在代码库工作的 Agent，不撤销他人改动；共享文件由 Leader 串行交接。原执行 Agent 不承担自己的独立审查。

每个 GROUP 记录 execution-log、定向验证、剩余缺口；末组完成后再执行集体测试和集体审查，不将“子 Agent 返回”当作交付完成。当前没有因本计划启动新子 Agent 或真实评估。

## Next

- 审阅计划与 Demo；修改意见直接反馈。
- 说“开始实现”或“执行”后按上述顺序进入实施。
