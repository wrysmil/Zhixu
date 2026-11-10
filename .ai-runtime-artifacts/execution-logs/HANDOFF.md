# HANDOFF — 知识检索评测台（实现完成，待合流）

> 最后更新：2026-11-10
> 状态：**Task 1–13 全部完成**；后端 API + 前端页面（`#/knowledge-eval`）+ 10 语言文案均已落地，
> 端到端在分支代码上真实跑通。**尚未合流回 `feature/desktop-app`**。

## 1. 一句话现状

「知识检索评测」全链路（数据集 → 检索 → 并池 → 标注/判定 → 指标 → 落库 → HTTP API → WebUI 页面）已实现、
已测试、已在浏览器里跑出真实指标。前端页面路由 `#/knowledge-eval` 可用，指标口径区与逐题表同屏可见。

## 2. 代码位置

| 项 | 值 |
| --- | --- |
| worktree | `D:/studyspace/project/.harness-worktrees/Zhixu/wt-knowledge-rag-eval` |
| 分支 | `harness/wt-knowledge-rag-eval`（基线 `feature/desktop-app` @ `c4cc9f40`） |
| 主仓 | `D:/studyspace/project/Zhixu`（`feature/desktop-app`） |

**所有命令都要在 worktree 里跑**（代码解析到 worktree，不是主仓）。

## 3. 环境（本机 Windows 的坑，别重复踩）

```bash
WT=D:/studyspace/project/.harness-worktrees/Zhixu/wt-knowledge-rag-eval
V=D:/studyspace/project/Zhixu/.venv/Scripts/python.exe

# Python：用主仓解释器 + `python -m`（cwd 插到 sys.path 最前 → 解析到 worktree 代码）
cd "$WT" && "$V" -m pytest tests/evaluation/knowledge -q

# 前端：node_modules 用 junction 挂主仓（worktree 内没有）
cd "$WT/webui" && cmd //c "mklink /J node_modules D:\studyspace\project\Zhixu\webui\node_modules"

# vitest：主仓 node_modules/.vite 被占用（EPERM）→ 用 worktree 内本地 config 把 cacheDir 指开
cd "$WT/webui" && npx vitest run --config vitest.local.config.ts src/tests/knowledge-eval-page.test.tsx

# 构建：esbuild 临时文件在默认 %TEMP% 被拒（Access is denied）→ TEMP 指到 node_modules/.tmp
cd "$WT/webui" && WINTMP=$(cd node_modules/.tmp && pwd -W) \
  && TMPDIR="$WINTMP" TEMP="$WINTMP" TMP="$WINTMP" npx vite build
```

> `vitest.local.config.ts` 是本轮新增的**本地未入库**辅助文件；`node_modules/.tmp` 目录同样不入库。

## 4. 已完成

| Task | 内容 | 状态 |
| --- | --- | --- |
| 1 | `KnowledgeService.retriever_with(settings)` 只读构造器（产品侧唯一改动） | ✅ |
| 2 | 评测路由挂进共享 `_routes()`；`config` 贯通到评测服务 | ✅ |
| 3 | 数据模型 + 检索参数白名单（`models.py`） | ✅ |
| 4 | 指标计算 + `METRIC_SPECS` 公式注册表（`metrics.py`） | ✅ |
| 5 | 4 张表 + 仓储层（`repository.py`，schema 版本 5） | ✅ |
| 6 | LLM 相关性判定（`judge.py`，含提示注入守卫） | ✅ |
| 7 | 执行器（`runner.py`，并池/判分/打分/降级标注） | ✅ |
| 8 | HTTP 路由真实 handler + 错误码登记 | ✅（上一轮，提交 `ad2be96a`） |
| 9 | 前端 API 客户端（`knowledge-eval-api.ts`） | ✅（上一轮，提交 `79ee089c`） |
| 10 | 注册 `#/knowledge-eval` 路由与导航 | ✅ `4fd279a` |
| 11 | 页面组件（概览卡 + 口径区 + 逐题表 + 数据集/运行/对比三分区） | ✅ |
| 12 | 10 语言 i18n 文案 | ✅ `1987b36` |
| 13 | 端到端验证 + 浏览器回归 + verification 产物 | ✅ 见 §6 |

关键产物：
- `nanobot/evaluation/knowledge/{models,metrics,judge,repository,runner,routes}.py`
- `nanobot/knowledge/service.py`（+`retriever_with`）
- `webui/src/lib/knowledge-eval-api.ts`、`webui/src/components/workspace/KnowledgeEvalPage.tsx` +
  `webui/src/components/workspace/knowledge-eval/*`（6 个子组件 + 指标工具）
- 设计与口径：`specs/2026-11-10-knowledge-rag-eval-spec.md`、`contracts/2026-11-10-contract-knowledge-rag-eval.md`、`plans/2026-11-10-knowledge-rag-eval-plan.md`

### 本轮修掉的阻断缺陷

1. **gateway 起不来**：`nanobot.evaluation.knowledge.__init__` 包初始化导入 `routes`，形成
   `services → knowledge → routes → memory.routes → server → services` 循环导入。pytest 因导入顺序不同
   **测不出来**。已改 PEP 562 惰性导出，并补 `tests/evaluation/knowledge/test_import_order.py`
   （子进程按 gateway 真实顺序导入）。
2. **页面默认选中最旧一次运行**：后端 `listRuns` 按 `created_at` 升序返回，前端取 `[0]` → 打开页面看到历史旧结果。
   已改为按 `createdAt` 降序并默认选最新。

## 5. 下一步（按顺序）

1. **合流**：`harness/wt-knowledge-rag-eval` → `feature/desktop-app`（需用户确认后执行）。
2. 合流后重启 gateway（Python 侧有改动）并重建 `nanobot/web/dist`。
3. 若要补「真实退化」演示，需要一份「关键词更像但不相关」的语料 + 一条语义能命中而关键词会跑偏的用例。

## 6. 验证产物

- 报告：`.ai-runtime-artifacts/verifications/2026-11-10-knowledge-rag-eval-verification.md`
- 截图：`.../verifications/knowledge-rag-eval/knowledge-eval-{overview,runs,compare}.png`
- 原始证据：`.../verifications/knowledge-rag-eval/e2e-report.json`

**未验证项（勿当已验证）**：judge 标注组的真实模型质量；`regressions` 非空时的浏览器渲染分支
（后端 HTTP 级测试已覆盖计算）；桌面端打包；10 语言文案的母语校对。

## 7. 关键口径（不要改错）

- **每次测评输出义务**：逐题必须带 `precision_at_k` / `recall_at_k` / `hit_at_k` / `mrr` / `ndcg_at_k` + `status`；聚合按 `label_source` 分组给全 7 个指标；**缺证据是 `null` + `not_assessable`，绝不补 0**。
- **`recall_at_k` 是池相对值**（分母 = 候选池中相关块数），不是全库绝对召回；首页必须常驻说明。
- **公式单点定义**：`metrics.py` 的 `METRIC_SPECS` → `GET /knowledge/metrics` → 前端渲染。**前端与 i18n 都不得内置公式串**（已 grep 校验语言文件无 `÷` / `log₂` / `2^grade`）。
- **`scored` 由权威结论派生**：池中无相关块时即使 hit/precision 有值，`scored` 也必须为 `False`（已修，有回归测试）。
- 聚合方式：precision/recall/mrr/nDCG 为 macro（逐题平均）；hit/pool_coverage/unscored_rate 为 micro（整体比值）。
- 前端归一化要认后端的两种形状：compare 的退化清单在 **`regressions`** 键（不是 `regressed*`）；运行降级原因在 **`pool.degraded`**。

## 8. 已知问题（非本次引入）

| 问题 | 说明 |
| --- | --- |
| 基线 lint 债 | `nanobot/memory/__init__.py` 3 处 F401；`tests/evaluation/{test_observability,test_observability_routes,test_timeout}.py` 3 处 I001。`feature/desktop-app` 上同样存在，**不是本次改动引入** |
| 平台相关测试失败 | `tests/knowledge/storage/test_files.py` 等断言 `chmod 0600/0700` 与 symlink 权限，Windows 下必失败；基线同样失败 |
| basedpyright 噪声 | aiohttp `web.RouteDef` 被报 unknown（未改动的 `memory/routes.py` 同样报） |
| `mrr` 指标 ID 重名 | 记忆评测与知识评测都有 `mrr`。两模块独立、运行不冲突；若将来统一指标目录需先去重 |
| 测试辅助方法 | `KnowledgeService` 无「建文档+提交块」公开方法，造语料需照抄 `tests/knowledge/test_golden_queries.py:48` 的 `_seed_corpus`（用 `KnowledgeRepository.insert_document`/`claim_document`/`commit_chunks`） |

## 9. 恢复指令（直接对 AI 说）

> 读 `.ai-runtime-artifacts/execution-logs/HANDOFF.md`，知识检索评测台已实现完成，待合流回 `feature/desktop-app`；
> worktree 在 `D:/studyspace/project/.harness-worktrees/Zhixu/wt-knowledge-rag-eval`，
> 用主仓 `.venv` 的 `python -m` 跑 pytest，前端用 `npx vitest --config vitest.local.config.ts`（本机无 bun）。
