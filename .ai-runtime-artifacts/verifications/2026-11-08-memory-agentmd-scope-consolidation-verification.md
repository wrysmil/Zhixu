---
artifact: verification
route: orchestration:verification
plan: .ai-runtime-artifacts/plans/2026-11-08-memory-agentmd-scope-consolidation-plan.md
dispatch: .ai-runtime-artifacts/plans/2026-11-08-memory-agentmd-scope-consolidation-dispatch.md
skills:
  - verification-before-completion
source:
  - AGENTS.md
  - harness-kit/core/routing.md
created_at: 2026-11-08
verdict: pass
---

# 记忆作用域对齐 — 验证记录

范围：WU-A（AGENTS.md 双层注入）、WU-B1（全工作区盘账）、WU-B2（`consolidate_memory` 去重/过期）。

## 1. TDD 红灯证据

| 用例 | 命令 | 红灯表现 |
| --- | --- | --- |
| 项目 AGENTS.md 未注入 | `pytest tests/agent/test_context_agents_md_scope.py -v` | `test_project_agents_md_is_injected_alongside_global` FAIL：prompt 中只有 `## AGENTS.md (project)`，无全局 marker |
| 多工作区函数不存在 | `pytest tests/memory/test_decay.py -k all_workspaces -v` | `AttributeError: module 'nanobot.memory.decay' has no attribute 'run_consolidation_for_all_workspaces'` |
| 工具缺少盘账/作用域解析 | `pytest tests/agent/test_memory_admin_tools.py -k consolidate -v` | 3 failed：无「归档」文案；`memory_admin.current_workspace_scope` 不存在；`_consolidation_workspace_ids` 不存在 |

## 2. 绿灯结果

```text
pytest tests/agent/test_context_agents_md_scope.py tests/agent/test_memory_admin_tools.py \
       tests/memory/test_decay.py tests/test_context_documents.py tests/agent/test_context_builder.py -q
→ 127 passed

pytest tests/memory tests/agent tests/test_context_documents.py tests/test_tool_contextvars.py -q
→ 3 failed, 2776 passed

pytest tests/agent/test_consolidator.py -q
→ 55 passed
```

## 3. 广域回归中的 3 条失败（基线既有，非本次引入）

在 `git worktree add /tmp/zhixu-head-check HEAD`（99f6689e，未含本次改动）上用同一 `.venv` 复跑：

```text
FAILED tests/agent/test_session_atomic.py::TestAtomicSave::test_provider_state_does_not_consume_list_preview_budget
FAILED tests/agent/test_turn_agent_profile.py::test_mentioned_agent_applies_prompt_tools_and_model
FAILED tests/agent/test_mcp_reconnect_crash.py::test_mcp_reconnect_during_shutdown_does_not_crash
→ 3 failed, 1 passed

# 「1 passed」= test_consolidator.py::...unified_session_workspace
# 该用例在基线上绿、在本次改动后转红，因为它断言「项目作用域下全局 AGENTS.md 不出现」，
# 与新的双层注入口径冲突；已按新语义改为断言两份都在。
```

临时 worktree 已用 `git worktree remove --force` 清理，未触碰主 checkout 的未提交改动。

## 4. 语义同步修改的既有用例

| 文件 | 旧断言 | 新断言 |
| --- | --- | --- |
| `tests/agent/test_context_builder.py::test_selected_project_supplies_only_agents_file` → `test_selected_project_extends_global_agents_file` | 全局 AGENTS.md 不出现 | `## AGENTS.md` 与 `## AGENTS.md (project)` 同时出现；SOUL/USER 仍只取全局 |
| `tests/agent/test_context_builder.py::test_selected_project_without_agents_does_not_fall_back` → `test_selected_project_without_agents_keeps_global_agents_file` | 项目无 AGENTS 时也不回退全局 | 回退注入全局 AGENTS.md，且不产生项目标题 |
| `tests/agent/test_consolidator.py::test_reuses_real_prefix_for_unified_session_workspace` | `GLOBAL_WORKSPACE_MARKER not in system` | `GLOBAL_WORKSPACE_MARKER in system` |

## 5. 静态检查

```text
ruff check <4 个生产文件 + 5 个测试文件>
→ All checks passed!

basedpyright nanobot/agent/context.py nanobot/memory/decay.py \
             nanobot/cli/gateway_runtime.py nanobot/agent/tools/memory_admin.py
→ 14 errors
```

14 条全部落在本次未改动的行（`memory_admin.py:253/254/408/409/556/557/628/629` 为既有 `@tool_parameters`/ClassVar 模式；`gateway_runtime.py:20/394/395`；`decay.py:277/478/484`），本次新增的行无告警。

## 6. 未覆盖项

- 未起 gateway 做人工端到端验证（计划 §验证口径 的人工四步），因此「模型复述两份 AGENTS.md」「面板触发后审计表出现全局 + 项目两条 run」仅由单测覆盖，未做真实运行观察。
- 面板/前端未改动。
