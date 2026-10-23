---
artifact: implementation-dispatch
route: orchestration:dispatcher-workflow
plan: .ai-runtime-artifacts/specs/2026-10-17-writing-partner-model-selection-spec.md
skills:
  - orchestration
skills_evidence:
  - harness-kit/core/orchestration/dispatcher-workflow.md
source:
  - .ai-runtime-artifacts/specs/2026-10-17-writing-partner-model-selection-spec.md（用户已确认；用户原话「直接实现」= 跳过 plan 阶段，WU 拆分取自 spec §7）
  - .ai-runtime-artifacts/contracts/2026-10-17-contract-writing-team-collab.md（§3：客户端不得指定模型）
created_at: 2026-10-17
---

# 创作搭档侧栏切换会话模型 — Harness 执行图

> 实施步骤以 **spec** 为准（用户说「直接实现」，plan 阶段由用户显式跳过，见 FM source）；本文件只描述 GROUP / WU 与派发。
> 平台偏差（已记录，不是漏做）：
> 1. 本会话文件沙箱为 `workspace-write`，canonical 的 `<repo-parent>/.harness-worktrees/` 在沙箱外 → worktree 落到可写临时区 `/tmp/harness-worktrees/zhixu/`。
> 2. 本平台子代理**没有** `subagent_type` / role 机制（无 `.claude/agents/<role>.md` 自动加载），故 coder 的检查表与返回格式**内联**进派发 prompt。
> 3. 本机**没有 bun**，webui 测试用 `./node_modules/.bin/vitest run`（node v24.13.0）+ `npx tsc`/`vite build` 等价替代；`bun run test` 无法执行。

## 执行图

```markdown
GROUP-1（并行）:
  WU-01: 批次 runtime 按协作会话解析，frozen.model_id 取同一份解析结果 | 标题: 按会话解析写作批次模型 | 文件: nanobot/writing/collab/team_port.py, nanobot/teams/turn.py, nanobot/teams/runs.py, nanobot/webui/gateway_services.py, tests/teams/test_team_session_runtime.py(新) | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: /tmp/harness-worktrees/zhixu/wt-writing-partner-model-selection | branch: harness/wt-writing-partner-model-selection | wu_skills: source-driven-development, test-driven-development, observability-and-instrumentation

  WU-02: 侧栏模型 chip + /model 会话切换 + i18n + 组件测试 | 标题: 创作搭档侧栏模型选择 | 文件: webui/src/components/creative/WritingPartnerPanel.tsx, webui/src/components/creative/CreativeWorkspace.tsx, webui/src/components/creative/collab-labels.ts, webui/src/i18n/locales/zh-CN/common.json, webui/src/tests/creative-partner-model.test.tsx(新) | 依赖: 无 | wu_type: ui | agent_role: coder | workspace_scope: wu | worktree_path: /tmp/harness-worktrees/zhixu/wt-writing-partner-model-selection | branch: harness/wt-writing-partner-model-selection | wu_skills: source-driven-development, test-driven-development, frontend-ui-engineering

GROUP-2（尾盘，Leader 手动执行）:
  集体测试 → verification-before-completion → verifications/*-collective-test.md
  并行审查 → reviewer + security-auditor → reviews/*-code-review.md、reviews/*-security-review.md
  集成 → Leader 把 worktree 内变更落回主 checkout（保留作者 WIP）
  执行日志 → execution-logs/2026-10-17-writing-partner-model-selection-execution-log.md
```

## 接口边界（两 WU 之间）

只有一个接触面：**协作会话的模型 preset**（既有机制，无新增端点/字段）。

- WU-02 只负责发既有系统命令 `/model <preset>`（`chatId = <article_id>:partner`）并显示会话当前 preset；
- WU-01 只负责让批次 runtime 与 `frozen.model_id` 按该会话解析；
- 双方**都不得**给 `writing.start` 增加模型字段（契约 §3）。

## 变更记录

| 轮次 | 日期 | 变更摘要 |
| --- | --- | --- |
| 1 | 2026-10-17 | 初稿：GROUP-1 两个并行 WU |

## Next

- 执行图确认 → 说「开始实现」或「并行执行」
- 只改 WU 拆分 / 依赖 → 改本文件并告知 Leader 审阅
