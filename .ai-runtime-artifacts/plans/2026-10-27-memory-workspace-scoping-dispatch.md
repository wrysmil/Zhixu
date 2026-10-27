---
artifact: implementation-dispatch
route: orchestration:dispatcher-workflow
plan: .ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-plan.md
skills:
  - writing-plans
  - orchestration
skills_evidence:
  - .claude/skills/writing-plans/SKILL.md
  - .claude/skills/orchestration/SKILL.md
source:
  - core/orchestration/dispatcher-workflow.md
  - .ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-plan.md
created_at: 2026-10-27
---

# 记忆工作区隔离 — Harness 执行图

> 实施步骤以 **plan** 为准；本文件只描述并行 GROUP / WU 与派发。多轮审阅时优先改本文件，避免扰动 plan 内 Task 细步。

## 执行图

```markdown
GROUP-1:
  WU-01: Task 1-3 | 标题: 存储基座（工作区键推导 / episode 工作区列 / repository 可见集合过滤） | 文件: nanobot/memory/provenance.py, nanobot/memory/models.py, nanobot/memory/database.py, nanobot/memory/repository.py, tests/memory/test_workspace_scope.py, tests/memory/test_repository_workspace_filter.py, tests/memory/test_repository.py, tests/memory/test_database.py | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: <dispatcher 分配> | branch: n/a | wu_skills: auto
  WU-02: Task 4 | 标题: 提取写入按会话工作区打标 | 文件: nanobot/memory/extractor.py, nanobot/memory/provenance.py, tests/memory/test_extractor_workspace.py | 依赖: WU-01 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: <dispatcher 分配> | branch: n/a | wu_skills: auto
  WU-03: Task 5-6 | 标题: 检索链路可见集合（retrieval / agent / tool） | 文件: nanobot/memory/retrieval/**, nanobot/agent/context.py, nanobot/agent/loop.py, nanobot/agent/tools/memory_search.py, tests/memory/retrieval/**, tests/agent/test_memory_visibility.py, tests/memory/test_memory_search_tool.py | 依赖: WU-01 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: <dispatcher 分配> | branch: n/a | wu_skills: auto
  WU-04: Task 7-8 | 标题: 记忆面板与工作台归属口径 | 文件: nanobot/webui/memory_api.py, nanobot/webui/memory_presentation.py, nanobot/webui/session_workbench.py, tests/webui/** | 依赖: WU-01 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: <dispatcher 分配> | branch: n/a | wu_skills: auto
  WU-05: Task 9 | 标题: 前端筛选与标签 | 文件: webui/src/lib/types.ts, webui/src/components/settings/memory/**, webui/src/tests/** | 依赖: WU-04 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: <dispatcher 分配> | branch: n/a | wu_skills: auto
```

## 并行边界（冲突规避）

| 文件 | 归属 WU | 说明 |
| --- | --- | --- |
| `nanobot/memory/provenance.py` | WU-01（主）、WU-02（改 `extraction_provenance` 签名） | WU-02 必须在 WU-01 合入后开工；改动限于该函数签名与全局态分支 |
| `nanobot/memory/repository.py` | WU-01 独占 | WU-02/03/04 只调用，不修改 |
| `nanobot/webui/memory_api.py` | WU-04 独占 | 手动写入归属（`create_memory`）与面板读取同属 WU-04，避免同文件并行冲突 |
| `nanobot/agent/loop.py` | WU-03 独占 | WU-04 只读不改 |

## 禁止事项

- 不改 `nanobot/memory/lifecycle.py`（MEMORY.md 派生 `scope='user'` 恒空属独立缺陷，见 plan §6 R5）。
- 不做历史数据迁移：`workspace_id='default'` 即全局，无需回填。
- 不改 `nanobot/memory/database.py` 的 memories 表结构（`workspace_id` 列已存在）。
- **不改 `nanobot/memory/vector/store.py`**：向量侧不做库内 `where` 过滤（实测 +1~18ms/查询，见 plan §5.3），可见性统一在 SQLite 侧判定。
- 不改 `docs/` 下的记忆系统设计文档（本计划与 `docs/记忆系统/plan/*` 是历史方案，不回改）。
- 不引入新的第三方依赖。

## done criteria（每个 WU）

1. 该 WU 的新增单测先红后绿；所属目录既有测试全绿。
2. `uv run --no-sync ruff check nanobot/` 与 `uv run --no-sync basedpyright` 无新增告警。
3. 子 Agent 返回体含 `### Skills 使用` 段，列出实际加载的 skill。
4. 不 commit、不 push；提交与合流由 Leader 按 `harness-kit/project.git.md` 统一处理。

## 变更记录

| 轮次 | 日期 | 变更摘要 |
| --- | --- | --- |
| 1 | 2026-10-27 | 初稿 |

## Next

- 执行图确认 → 说「开始实现」或「并行执行」
- 只改 plan 任务、不改并行策略 → 仅改 `*-plan.md`
- 只改 WU 拆分 / 依赖 → 改本文件并告知 Leader 审阅
