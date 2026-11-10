# DISPATCH-TRACK 知识检索评测台

> 计划：`.ai-runtime-artifacts/plans/2026-11-10-knowledge-rag-eval-plan.md`
> 执行图：`.ai-runtime-artifacts/plans/2026-11-10-knowledge-rag-eval-dispatch.md`
> 契约：`.ai-runtime-artifacts/contracts/2026-11-10-contract-knowledge-rag-eval.md`
> worktree：`/Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/zhixu/wt-knowledge-rag-eval`
> branch：`harness/wt-knowledge-rag-eval`（base `c4cc9f40`）

## 环境事实（Leader 实测，供 Worker 直接使用）

- **Python**：worktree 内无 `.venv`（在 .gitignore）。用主仓解释器在 worktree 目录运行即可，`nanobot` 会解析到 **worktree 的代码**（已验证）：
  ```bash
  cd "$WT" && /Users/mima0000/Documents/学习-001/do-project/zhixu/.venv/bin/python -m pytest tests/... -q
  ```
  实测 `tests/knowledge/` 349 passed。
- **前端**：本机**无 bun**，用 npx 调主仓 node_modules（已软链进 worktree）：
  ```bash
  cd "$WT/webui" && npx vitest run src/tests/xxx.test.ts
  ```
  实测 `i18n.test.tsx` 27 passed。
- **提交**：worktree 内 `git commit`，分支 `harness/wt-knowledge-rag-eval`；**不要 push**。

## 执行图（来自 dispatch）

```text
GROUP-1（并行，文件互不重叠）:
  WU-01 Task 1  retriever_with 只读构造器     nanobot/knowledge/service.py + tests/knowledge/test_retriever_with.py
  WU-02 Task 2  评测服务接线 + 路由骨架        nanobot/evaluation/{knowledge/routes.py,knowledge/__init__.py,server.py} + nanobot/cli/{gateway_runtime.py,eval_commands.py} + tests/evaluation/knowledge/test_routes.py
  WU-03 Task 3  数据模型 + 检索参数白名单       nanobot/evaluation/knowledge/models.py + tests/evaluation/knowledge/test_models.py
  WU-04 Task 4  指标计算 + METRIC_SPECS 公式    nanobot/evaluation/knowledge/metrics.py + tests/evaluation/knowledge/test_metrics.py

GROUP-2: WU-05 存储层（依赖 WU-03）→ WU-06 判定（无依赖）→ WU-07 执行器（依赖 03/04/05/06）
GROUP-3: WU-08 HTTP 路由（依赖 02/05/07）→ WU-09 前端客户端（无依赖）
GROUP-4: WU-10 路由注册（依赖 09）→ WU-11 页面组件（依赖 09/10）
GROUP-5: WU-12 i18n（依赖 11）→ WU-13 端到端验证（依赖 08/12）
```

## 追踪条目（append-only）

[2026-11-10 01:33] WORKTREE-INIT | Leader | Status: completed
Detail: 创建 harness/wt-knowledge-rag-eval（base c4cc9f40）；产物先入库再开 worktree，确保契约/计划随代码可见。
Sub-agents: 0
Context: ~40%
Output: .ai-runtime-artifacts/execution-logs/tracking/DISPATCH-TRACK-2026-11-10-knowledge-rag-eval.md
Error: none
Next: 派发 GROUP-1 四个并行 WU

[2026-11-10 01:34] ENV-PROBE | Leader | Status: completed
Detail: worktree 无 .venv/node_modules；已验证主仓解释器+node_modules 可在 worktree 内跑测试且代码解析到 worktree。
Sub-agents: 0
Context: ~42%
Output: none
Error: none
Next: 派发 GROUP-1

[2026-11-10 01:35] DISPATCH-GROUP-1 | Leader | Status: started
Detail: 并行派发 WU-01/02/03/04，四个 WU 文件互不重叠。
Sub-agents: 4
Context: ~45%
Output: none
Error: none
Next: 等待四 WU 返回并整合

[2026-11-10 01:48] DISPATCH-GROUP-1 | Leader | Status: completed
Detail: 四个 WU 全部返回 completed；Leader 核实提交后发现 scored 派生缺陷并修复。
Sub-agents: 0
Context: ~55%
Output: nanobot/evaluation/knowledge/{models,metrics,routes}.py + nanobot/knowledge/service.py
Error: 修复：case_score 池无相关块时 status=not_assessable，但 KnowledgeEvalCaseMetrics.scored 按「有值即 True」误判为 True（前端「未评分」徽标会说反）。已改为由 reason/metric_status 派生，补 4 条回归测试（回退验证：修复前 2 failed，恢复后 60 passed）。
Next: 派发 GROUP-2（WU-05 存储层 / WU-06 判定 / WU-07 执行器）

[2026-11-10 01:52] VERIFY-GROUP-1 | Leader | Status: completed
Detail: 实测 tests/evaluation/ + tests/knowledge/ 全量 1807 passed；ruff All checks passed；basedpyright 0 errors。
Sub-agents: 0
Context: ~57%
Output: none
Error: none
Next: 派发 GROUP-2

[2026-11-10 02:05] DISPATCH-GROUP-2 | Leader | Status: completed
Detail: WU-05 存储层 + WU-06 判定并行完成；Leader 补 migration-5 测试并修正命名。
Sub-agents: 0
Context: ~60%
Output: nanobot/evaluation/knowledge/{repository,judge}.py
Error: 修复：schema 升到 5 但无 migration-5 测试，既有测试名 migration_four 实为「升级到当前版本」名实不符 → 已补版本 5 建表/幂等测试并语义化命名。
Next: 派发 WU-07 执行器

[2026-11-10 02:08] VERIFY-GROUP-2 | Leader | Status: completed
Detail: 实测 tests/evaluation/ + tests/knowledge/ 1849 passed；ruff 通过。交接风险已记录：错误码 KNOWLEDGE_* 定义在 repository.py 但未登记到 errors.py，路由层会退化成 500——WU-08 必须登记。
Sub-agents: 0
Context: ~62%
Output: none
Error: none
Next: 派发 WU-07

[2026-11-10 02:22] DISPATCH-WU-07 | Leader | Status: completed
Detail: 执行器完成；Leader 核实发现 runner 走「同库直写」兜底绕过仓储层。
Sub-agents: 0
Context: ~65%
Output: nanobot/evaluation/knowledge/runner.py
Error: 修复：runner 在仓储缺写方法时直写数据库，绕过仓储事务与「可写列」白名单 → 已给仓储补公开 update_run_fields（白名单键 status/pool/progress）并删除 runner 直写通道，补 3 条仓储测试。
Next: 派发 GROUP-3（WU-08 路由 / WU-09 前端客户端）

[2026-11-10 02:26] VERIFY-WU-07 | Leader | Status: completed
Detail: 实测 tests/evaluation/ + tests/knowledge/ 1875 passed；本次改动范围 ruff All checks passed。
Sub-agents: 0
Context: ~67%
Output: none
Error: 基线既有 lint（nanobot/memory/__init__.py 3 处 F401、tests/evaluation 3 处 I001）在 feature/desktop-app 上同样存在，非本次引入，已核实。
Next: 派发 GROUP-3
