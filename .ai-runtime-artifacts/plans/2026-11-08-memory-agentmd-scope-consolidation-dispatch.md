---
artifact: implementation-dispatch
route: orchestration:dispatcher-workflow
plan: .ai-runtime-artifacts/plans/2026-11-08-memory-agentmd-scope-consolidation-plan.md
skills:
  - writing-plans
  - orchestration
skills_evidence:
  - ~/.agents/skills/writing-plans/SKILL.md
source:
  - .ai-runtime-artifacts/plans/2026-11-08-memory-agentmd-scope-consolidation-plan.md
  - nanobot/agent/context.py
  - nanobot/memory/decay.py
  - nanobot/cli/gateway_runtime.py
  - nanobot/agent/tools/memory_admin.py
created_at: 2026-11-08
status: implemented
approved: true
---

# 记忆作用域对齐 — Harness 执行图

> 实施步骤以 **plan** 为准；本文件只描述 WU 边界、文件归属与验收。改步骤改 plan，改派发改本文件。

## 执行图

```markdown
GROUP-1（三路并行，文件不相交）:
  WU-A:  Task 1 | 标题: AGENTS.md 全局 + 项目双层注入 | 文件: nanobot/agent/context.py, tests/agent/test_context_agents_md_scope.py | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | branch: feature/desktop-app | wu_skills: auto
  WU-B1: Task 2 | 标题: 每日盘账覆盖库内全部工作区 | 文件: nanobot/memory/decay.py, nanobot/cli/gateway_runtime.py, tests/memory/test_decay.py | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | branch: feature/desktop-app | wu_skills: auto
  WU-B2: Task 3 | 标题: consolidate_memory 支持去重/过期 | 文件: nanobot/agent/tools/memory_admin.py, tests/agent/test_memory_admin_tools.py | 依赖: 无（与 WU-B1 仅共享只读符号） | wu_type: feature | agent_role: coder | workspace_scope: wu | branch: feature/desktop-app | wu_skills: auto
```

## 并行边界（冲突规避）

| 文件 | 归属 WU | 说明 |
| --- | --- | --- |
| `nanobot/agent/context.py` | WU-A 独占 | 仅改 `_load_bootstrap_files` / `_render_bootstrap_file` |
| `nanobot/memory/decay.py` | WU-B1 独占 | 只新增 `run_consolidation_for_all_workspaces`，不动 `run_consolidation` 本体 |
| `nanobot/cli/gateway_runtime.py` | WU-B1 独占 | 仅 `memory_consolidate` job 体 |
| `nanobot/agent/tools/memory_admin.py` | WU-B2 独占 | WU-B2 **只读** `run_consolidation`，不得改 `decay.py` |
| `tests/memory/test_decay.py` | WU-B1 独占 | 追加用例 |
| `tests/agent/test_memory_admin_tools.py` | WU-B2 独占 | 追加用例；若缺 `_tool` 等既有辅助，只做增量 |
| `tests/agent/test_context_agents_md_scope.py` | WU-A 独占 | 新建 |

## 禁止事项

- 不改 `nanobot/memory/lifecycle.py`（MEMORY.md 导出仍按调用方 workspace_id；用户已明确暂不改该口径）。
- 不改表结构、不加迁移、不新增第三方依赖。
- 不改 `nanobot/agent/loop.py`（本轮只读 `current_workspace_scope` 与 `memory_workspace_id`）。
- 不改 `nanobot/webui/**`（面板触发链路沿用现有 API）。
- 不新增 per-project `MEMORY.md`，不回读 `MEMORY.md` 入库。
- worker 不 `git commit` / `git push`；提交由 Leader 按 plan 的分 Task 提交执行。

## done criteria（每个 WU）

1. plan 中该 Task 的新增用例先红后绿；相关既有测试全绿（命令见 plan 各 Task Step 4）。
2. `uv run --no-sync ruff check` 对改动文件无新增告警。
3. 失败隔离：WU-B1 单工作区异常不打断其余工作区；WU-B2 盘账异常不阻断投影刷新。
4. 汇报内容：改了哪些文件、测试输出摘要、发现但与计划不符之处。

## 合并后验收

```bash
pytest tests/agent/test_context_agents_md_scope.py tests/agent/test_memory_admin_tools.py tests/memory/test_decay.py tests/test_context_documents.py -v
ruff check nanobot/agent/context.py nanobot/memory/decay.py nanobot/cli/gateway_runtime.py nanobot/agent/tools/memory_admin.py
```
