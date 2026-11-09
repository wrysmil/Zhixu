---
artifact: implementation-dispatch
route: orchestration:dispatcher-workflow
plan: .ai-runtime-artifacts/plans/2026-11-10-knowledge-rag-eval-plan.md
skills:
  - writing-plans
  - orchestration
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
source:
  - harness-kit/core/orchestration/dispatcher-workflow.md
  - harness-kit/core/routing.md
created_at: 2026-11-10
---

# 知识检索评测台 — Harness 执行图

> 实施步骤以 **plan** 为准；本文件只描述并行 GROUP / WU 与派发。多轮审阅时优先改本文件，避免扰动 plan 内 Task 细步。

## 执行图

```markdown
GROUP-1:
  WU-01: Task 1 检索参数覆盖构造器 | 标题: 为知识服务新增 retriever_with 只读构造器 | 文件: nanobot/knowledge/service.py, tests/knowledge/test_retriever_with.py | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: auto
  WU-02: Task 2 评测服务接线 | 标题: 挂载知识评测路由并把 config 贯通到评测服务 | 文件: nanobot/evaluation/knowledge/routes.py, nanobot/evaluation/knowledge/__init__.py, nanobot/evaluation/server.py, nanobot/cli/gateway_runtime.py, nanobot/cli/eval_commands.py, tests/evaluation/knowledge/test_routes.py | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: auto
  WU-03: Task 3 数据模型 | 标题: 新增知识评测数据模型与检索参数白名单 | 文件: nanobot/evaluation/knowledge/models.py, tests/evaluation/knowledge/test_models.py | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: auto
  WU-04: Task 4 指标计算 | 标题: 新增知识检索指标计算与分组聚合 | 文件: nanobot/evaluation/knowledge/metrics.py, tests/evaluation/knowledge/test_metrics.py | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: auto

GROUP-2:
  WU-05: Task 5 存储层 | 标题: 新增知识评测数据表与仓储层 | 文件: nanobot/evaluation/database.py, nanobot/evaluation/knowledge/repository.py, tests/evaluation/knowledge/test_repository.py | 依赖: WU-03 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: auto
  WU-06: Task 6 LLM 判定 | 标题: 新增知识检索 LLM 相关性判定 | 文件: nanobot/evaluation/knowledge/judge.py, tests/evaluation/knowledge/test_judge.py | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: auto
  WU-07: Task 7 执行器 | 标题: 新增知识评测执行器（并池/判定/打分编排） | 文件: nanobot/evaluation/knowledge/runner.py, tests/evaluation/knowledge/test_runner.py | 依赖: WU-03, WU-04, WU-05, WU-06 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: auto

GROUP-3:
  WU-08: Task 8 HTTP 路由 | 标题: 接入知识评测 HTTP 路由与错误码 | 文件: nanobot/evaluation/errors.py, nanobot/evaluation/knowledge/routes.py, nanobot/evaluation/services.py, nanobot/evaluation/server.py, tests/evaluation/knowledge/test_routes.py | 依赖: WU-02, WU-05, WU-07 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: auto
  WU-09: Task 9 前端客户端 | 标题: 新增知识评测 API 客户端与类型映射 | 文件: webui/src/lib/knowledge-eval-api.ts, webui/src/tests/knowledge-eval-api.test.ts | 依赖: 无 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: auto

GROUP-4:
  WU-10: Task 10 路由注册 | 标题: 注册 #/knowledge-eval 路由与导航项 | 文件: webui/src/components/workspace/contracts.ts, webui/src/workspace/routes.ts, webui/src/components/workspace/WorkspaceNavigation.tsx, webui/src/App.tsx, webui/src/tests/knowledge-eval-page.test.tsx | 依赖: WU-09 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: auto
  WU-11: Task 11 页面组件 | 标题: 新增知识检索评测台页面（概览卡 + 四分区） | 文件: webui/src/components/workspace/KnowledgeEvalPage.tsx, webui/src/components/workspace/knowledge-eval/*.tsx, webui/src/tests/knowledge-eval-page.test.tsx | 依赖: WU-09, WU-10 | wu_type: feature | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: auto

GROUP-5:
  WU-12: Task 12 i18n | 标题: 补齐知识评测台 10 语言文案 | 文件: webui/src/i18n/locales/*/common.json | 依赖: WU-11 | wu_type: chore | agent_role: coder | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: auto
  WU-13: Task 13 端到端验证 | 标题: 知识检索评测台端到端验证与证据落盘 | 文件: .ai-runtime-artifacts/verifications/2026-11-10-knowledge-rag-eval-verification.md | 依赖: WU-08, WU-12 | wu_type: test | agent_role: test-engineer | workspace_scope: wu | worktree_path: n/a | branch: feature/desktop-app | wu_skills: auto
```

## 文件冲突说明（Leader 必须处理）

| 文件 | 并行写它的 WU | 冲突性质 | 处置 |
| --- | --- | --- | --- |
| `nanobot/evaluation/knowledge/routes.py` | WU-02（骨架）→ WU-08（真实 handler） | **同文件串行**，但 WU-08 依赖 WU-02 | 串行执行，无冲突 |
| `nanobot/evaluation/server.py` | WU-02（挂路由 + config）→ WU-08（知识服务解析） | 同文件串行 | 串行执行，无冲突 |
| `tests/evaluation/knowledge/test_routes.py` | WU-02（挂载断言）→ WU-08（端点断言） | 同文件串行 | 串行执行，无冲突 |
| `nanobot/evaluation/database.py` | 仅 WU-05 | 单写 | 无冲突 |
| `webui/src/tests/knowledge-eval-page.test.tsx` | WU-10（路由断言）→ WU-11（渲染断言） | 同文件串行 | 串行执行，无冲突 |

**结论：真正可并行的只有 GROUP-1 内部的四个 WU**（文件互不重叠）。GROUP-2/3/4/5 内部存在同文件串行链，实际按 GROUP 顺序推进即可。

## 串行化建议（避免并行带来的合并成本）

本任务的并行收益有限——多数 WU 之间有依赖或共享文件。**推荐串行执行 GROUP-1 → GROUP-2 → GROUP-3 → GROUP-4 → GROUP-5**，在 GROUP-1 内部可并行 4 个 WU（模型/指标/接线/构造器彼此独立）。若用户要求「开始实现」但未说「并行执行」，按 `routing.md` 走 Tier 2 编排的串行路径。

## 关键约束（每个 worker 必读）

1. **不改既有公开契约**：不动 `knowledge_search` 工具名、不动 `/api/settings/knowledge/*`、不动既有评测端点。新增一律是纯增量。
2. **不改产品检索行为**：`retriever_with` 是新增只读构造器；产品默认 `search()` 路径不变。
3. **缺证据不补 0**：所有指标在证据不足时是 `null` + `not_assessable`。
4. **标注来源分组不混算**：`expect` 与 `judge` 的聚合分开出。
5. **判分材料是数据不是指令**：沿用 `graders/judge.py` 的守卫句式。
6. **i18n 10 语言同步**：`i18n.test.tsx` 会因漏一个 locale 而失败。
7. **不引入新依赖**：不得添加 Ragas / DeepEval / Phoenix / Streamlit 等。

## 变更记录（可选）

| 轮次 | 日期 | 变更摘要 |
| --- | --- | --- |
| 1 | 2026-11-10 | 初稿 |

## Next

- 执行图确认 → 说「开始实现」或「并行执行」
- 只改 plan 任务、不改并行策略 → 仅改 `*-plan.md`
- 只改 WU 拆分 / 依赖 → 改本文件并告知 Leader 审阅
