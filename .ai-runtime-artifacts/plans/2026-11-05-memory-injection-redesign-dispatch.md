---
artifact: implementation-dispatch
route: orchestration:dispatcher-workflow
plan: .ai-runtime-artifacts/plans/2026-11-05-memory-injection-redesign-plan.md
skills:
  - writing-plans
  - orchestration
skills_evidence:
  - harness-kit/core/orchestration/dispatcher-workflow.md
source:
  - harness-kit/core/routing.md
  - harness-kit/core/orchestration/dispatcher-workflow.md
created_at: 2026-11-05
---

# 记忆注入改造 — Harness 执行图

> 实施步骤以 **plan** 为准；本文件只描述 WU 边界与执行方式。

## 执行方式（偏离说明）

本次采用 **Leader 单线程顺序执行**（不派 worker、不建 worktree）：8 个 WU 在 `nanobot/memory/files.py`、
`context.py`、`loop.py`、`lifecycle.py` 之间有数据流依赖（文件层 API → 注入 → 工具 → 提示词 → 抽取 → Dream → 生命周期 → 迁移），
并行派发会在同一 checkout 产生交叉改动与冲突。工作树隔离的收益低于串行执行的稳定性，故按顺序推进，每个 WU 单独跑定向测试后再进入下一个。

## 执行图

```markdown
GROUP-1（顺序，Leader 串行执行，每 WU 独立提交）:
  WU-01: 记忆文件层 + 配置开关 | 文件: nanobot/memory/files.py, nanobot/config/schema.py, tests/memory/test_memory_files.py | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: test-driven-development, verification-before-completion
  WU-02: 注入改造（索引块 / 退出核心事实） | 文件: nanobot/agent/context.py, tests/memory/test_memory_injection.py | 依赖: WU-01 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: test-driven-development, verification-before-completion
  WU-03: 主动写（白名单 + 撤下记忆库工具） | 文件: nanobot/agent/tools/filesystem.py, nanobot/agent/tools/memory_admin.py, nanobot/agent/loop.py, tests/memory/test_memory_file_tools.py | 依赖: WU-01 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: test-driven-development, verification-before-completion
  WU-04: 提示词重写 | 文件: nanobot/templates/AGENT.md, nanobot/templates/agent/identity.md, nanobot/templates/agent/dream.md, nanobot/templates/memory/MEMORY.md | 依赖: WU-03 | wu_type: docs | agent_role: implementer | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: verification-before-completion
  WU-05: 定时抽取 cron + 双出口 | 文件: nanobot/memory/extract_writer.py, nanobot/memory/extractor.py, nanobot/agent/hooks/memory_extraction.py, nanobot/agent/loop.py, nanobot/cli/gateway_runtime.py, tests/memory/test_scheduled_extraction.py | 依赖: WU-01, WU-03 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: test-driven-development, verification-before-completion
  WU-06: Dream 改造 | 文件: nanobot/agent/memory.py, tests/memory/test_dream_file_tools.py | 依赖: WU-01, WU-03 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: test-driven-development, verification-before-completion
  WU-07: USER.md 自动区切源 | 文件: nanobot/memory/lifecycle.py, tests/memory/test_lifecycle.py | 依赖: WU-01 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: test-driven-development, verification-before-completion
  WU-08: 迁移 + 回滚 + 文档 + 端到端验收 | 文件: nanobot/memory/files.py, docs/memory.md, tests/memory/test_memory_migration.py | 依赖: WU-01..WU-07 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: verification-before-completion
```

## 变更记录

| 轮次 | 日期 | 变更摘要 |
| --- | --- | --- |
| 1 | 2026-11-05 | 初稿 |

## Next

- 执行图确认 → 用户已授权（「写计划然后实现」），Leader 按 WU-01 → WU-08 顺序推进
- 只改 plan 任务 → 仅改 `2026-11-05-memory-injection-redesign-plan.md`
- 只改 WU 拆分 / 依赖 → 改本文件
