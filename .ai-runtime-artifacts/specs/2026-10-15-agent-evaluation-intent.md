---
artifact: spec
route: interview-me
skills:
  - interview-me
skills_evidence:
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.13/skills/interview-me/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - 本会话用户请求、截图及三项选择
created_at: 2026-10-15
status: confirmed
confirmed: true
confidence: 95
---

# Agent 自动化评估：已确认需求

- 目标：基于本项目批量执行测试任务，收集执行 Trace，自动评分并输出可追溯报告。
- 使用者：本项目开发者，首先用于开发期间的 Agent 任务回归。
- 评估对象：整个 Agent，覆盖任务完成、工具调用、回答质量、耗时和 Token。
- 产品形式：单独启动、单独访问的评估网站，拥有自己的后台页面。
- 第一版能力：用例管理、发起和取消批量评估、结果报告、单用例 Trace 详情。
- 判分原则：确定性结果优先规则断言；开放任务使用评分模型辅助判断。
- 约束：复用项目 Agent 能力；测试工作区隔离；保留原始执行证据。
- 范围外：第一版不建设 CI 门禁、定时评估、多租户、分布式调度及公网托管。

## 确认证据

用户依次选择“整个 Agent：任务完成、工具调用、回答质量、耗时与 Token”“单独启动、单独访问的评估网站”，并在包含上述第一版功能的范围复述后回复“可以的”。其后回复“继续”，授权推进当前设计工作。

## Next

进入方案设计。需求确认不代表实现方案已批准；方案见同目录 `2026-10-15-agent-evaluation-spec.md`。
