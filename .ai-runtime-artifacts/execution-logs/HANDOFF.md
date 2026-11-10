# HANDOFF — 知识检索评测台（未完成，可续）

> 最后更新：2026-11-10
> 状态：**实现进行中，前端页面尚未开始；后端 API 已可用。**

## 1. 一句话现状

后端「知识检索评测」全链路（检索 → 并池 → LLM 判分 → 指标 → 落库 → HTTP API）已实现并测试通过；**前端页面（`#/knowledge-eval`）与 i18n 未开始**，因此目前只能通过 HTTP API 使用，WebUI 里还没有入口。

## 2. 代码位置

| 项 | 值 |
| --- | --- |
| worktree | `/Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/zhixu/wt-knowledge-rag-eval` |
| 分支 | `harness/wt-knowledge-rag-eval` |
| 基线 | `feature/desktop-app` @ `c4cc9f40` |
| 主仓（产物） | `/Users/mima0000/Documents/学习-001/do-project/zhixu` |

**所有命令都要在 worktree 里跑**（代码解析到 worktree，不是主仓）。

## 3. 环境（重要，别重复踩坑）

worktree 内**没有** `.venv` 和 `node_modules`（都在 .gitignore 里），已做如下配置：

```bash
WT=/Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/zhixu/wt-knowledge-rag-eval
V=/Users/mima0000/Documents/学习-001/do-project/zhixu/.venv/bin/python   # 主仓解释器

# Python：在 worktree 里用主仓解释器，nanobot 会解析到 worktree 代码（已实测）
cd "$WT" && $V -m pytest tests/evaluation/ tests/knowledge/ -q

# 前端：本机无 bun，node_modules 已软链到主仓 → 用 npx
cd "$WT/webui" && npx vitest run src/tests/xxx.test.ts
cd "$WT/webui" && npx tsc -p tsconfig.build.json --noEmit
```

> 若 worktree 被删除重建，需重新软链：`ln -sfn <主仓>/webui/node_modules "$WT/webui/node_modules"`

## 4. 已完成（12 个提交 + 1 个待提交）

| Task | 内容 | 状态 |
| --- | --- | --- |
| 1 | `KnowledgeService.retriever_with(settings)` 只读构造器（产品侧唯一改动） | ✅ |
| 2 | 评测路由挂进共享 `_routes()`；`config` 贯通到评测服务 | ✅ |
| 3 | 数据模型 + 检索参数白名单（`models.py`） | ✅ |
| 4 | 指标计算 + `METRIC_SPECS` 公式注册表（`metrics.py`） | ✅ |
| 5 | 4 张表 + 仓储层（`repository.py`，schema 版本 5） | ✅ |
| 6 | LLM 相关性判定（`judge.py`，含提示注入守卫） | ✅ |
| 7 | 执行器（`runner.py`，并池/判分/打分/降级标注） | ✅ |
| 8 | HTTP 路由真实 handler + 错误码登记 | ✅（**未提交**，见 §6） |
| 9 | 前端 API 客户端（`knowledge-eval-api.ts`） | ✅ |
| 10 | 注册 `#/knowledge-eval` 路由与导航 | ❌ **未开始** |
| 11 | 页面组件（概览卡 + 公式区 + 逐题表 + 四分区） | ❌ **未开始** |
| 12 | 10 语言 i18n 文案 | ❌ **未开始** |
| 13 | 端到端验证 + 浏览器回归 + verification 产物 | ❌ **未开始** |

关键产物：
- `nanobot/evaluation/knowledge/{models,metrics,judge,repository,runner,routes}.py`
- `nanobot/knowledge/service.py`（+`retriever_with`）
- `webui/src/lib/knowledge-eval-api.ts` + 测试
- 设计与口径：`specs/2026-11-10-knowledge-rag-eval-spec.md`、`contracts/2026-11-10-contract-knowledge-rag-eval.md`、`plans/2026-11-10-knowledge-rag-eval-plan.md`

## 5. 下一步（按顺序）

1. **Task 10 路由注册**：改 4 个文件
   - `webui/src/components/workspace/contracts.ts`：`WorkspaceView` 加 `"knowledge-eval"`
   - `webui/src/workspace/routes.ts`：`parseWorkspaceHash` 加 `/knowledge-eval`；`workspaceRouteHash` 自动通用
   - `webui/src/components/workspace/WorkspaceNavigation.tsx`：加导航项（挂 `CAPABILITY_ITEMS`，紧随 knowledge）
   - `webui/src/App.tsx`：渲染分支加 `<KnowledgeEvalPage />`
2. **Task 11 页面组件**：`KnowledgeEvalPage.tsx` + `knowledge-eval/` 下 5 个子组件
   - 概览卡（5 指标 + **每卡下方公式行**，公式取自 `api.listMetrics()`）
   - **指标口径区**（首页必备，`KnowledgeEvalMetricGuide.tsx`，渲染 `GET /knowledge/metrics` 的公式表）
   - **逐题结果表**（`KnowledgeEvalCaseResults.tsx`，每行 = 查询 × 配置，5 个指标 + status）
   - 数据集 / 运行 / 对比分区
   - **概览（聚合）与逐题表必须同时可见**
3. **Task 12 i18n**：10 个 locale 补 `knowledge.eval.*`。**公式不许抄进 i18n**（只放展示名与表头）。
4. **Task 13 验证**：跑全量 + `bun`/npx 构建 + **浏览器回归**（建集 → 跑 `expect` 组 → 看指标 → 改参数再跑 → 看退化清单），写 `verifications/2026-11-10-knowledge-rag-eval-verification.md`。

## 6. 待提交的改动（Task 8）

`nanobot/evaluation/{errors,routes,services,server}.py` 与 `tests/evaluation/knowledge/test_routes.py` 有**未提交改动**（约 1174 行）。

来源：WU-08 子代理在汇报阶段因模型服务错误中断，代码已落盘但未提交。**Leader 已独立核实**：
- 模块可正常导入；
- `tests/evaluation/knowledge/` → **180 passed**；
- 错误码已登记（`errors.py` 中 `KNOWLEDGE_*` 共 16 处）；
- 13 条路由无 501 占位；
- `EvaluationService` 知识方法齐全（`knowledge_capabilities` / `knowledge_create_dataset` / `knowledge_run_report` / `knowledge_compare` 等）；
- report 直返「聚合 + 逐题」两段。

## 7. 关键口径（不要改错）

- **每次测评输出义务**：逐题必须带 `precision_at_k` / `recall_at_k` / `hit_at_k` / `mrr` / `ndcg_at_k` + `status`；聚合按 `label_source` 分组给全 7 个指标；**缺证据是 `null` + `not_assessable`，绝不补 0**。
- **`recall_at_k` 是池相对值**（分母 = 候选池中相关块数），不是全库绝对召回；首页必须常驻说明。
- **公式单点定义**：`metrics.py` 的 `METRIC_SPECS` → `GET /knowledge/metrics` → 前端渲染。**前端与 i18n 都不得内置公式串**。
- **`scored` 由权威结论派生**：池中无相关块时即使 hit/precision 有值，`scored` 也必须为 `False`（已修，有回归测试）。
- 聚合方式：precision/recall/mrr/nDCG 为 macro（逐题平均）；hit/pool_coverage/unscored_rate 为 micro（整体比值）。

## 8. 已知问题（非本次引入）

| 问题 | 说明 |
| --- | --- |
| 基线 lint 债 | `nanobot/memory/__init__.py` 3 处 F401；`tests/evaluation/{test_observability,test_observability_routes,test_timeout}.py` 3 处 I001。`feature/desktop-app` 上同样存在，**不是本次改动引入** |
| basedpyright 噪声 | aiohttp `web.RouteDef` 被报 unknown（未改动的 `memory/routes.py` 同样报） |
| `mrr` 指标 ID 重名 | 记忆评测与知识评测都有 `mrr`。两模块独立、运行不冲突；若将来统一指标目录需先去重 |
| 测试辅助方法 | `KnowledgeService` 无「建文档+提交块」公开方法，造语料需照抄 `tests/knowledge/test_golden_queries.py:48` 的 `_seed_corpus`（用 `KnowledgeRepository.insert_document`/`claim_document`/`commit_chunks`） |

## 9. 恢复指令（直接对 AI 说）

> 读 `.ai-runtime-artifacts/execution-logs/HANDOFF.md`，从 Task 10 继续实现知识检索评测台的前端页面。
> worktree 是 `/Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/zhixu/wt-knowledge-rag-eval`，
> 用主仓 `.venv` 的解释器跑 pytest，前端用 `npx vitest`（本机无 bun）。
