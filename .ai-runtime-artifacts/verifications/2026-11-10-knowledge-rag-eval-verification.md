---
artifact: verification
route: superpowers:verification-before-completion
skills:
  - verification-before-completion
skills_evidence:
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/plans/2026-11-10-knowledge-rag-eval-plan.md
  - .ai-runtime-artifacts/contracts/2026-11-10-contract-knowledge-rag-eval.md
created_at: 2026-11-10
status: completed
---

# 知识检索评测台 验证报告（Task 13）

> 分支：`harness/wt-knowledge-rag-eval`（基线 `feature/desktop-app` @ `c4cc9f40`）
> worktree：`D:/studyspace/project/.harness-worktrees/Zhixu/wt-knowledge-rag-eval`
> 本轮补完 Task 10–13（前端路由、页面组件、10 语言文案、端到端验证）。

## 1. 结论

- Task 10/11/12 全部完成并有测试证据：前端新增 1 个页面 + 6 个子组件 + 1 个测试文件（6 用例），10 语言文案补齐。
- 端到端在**分支代码**上真实跑通：建数据集 → 发起运行 → 聚合段 + 逐题段 → 第二次运行 → 对比，浏览器页面（`#/knowledge-eval`）渲染出真实指标、后端下发的公式、逐题表与降级徽标。
- **过程中发现并修复 2 个阻断性缺陷**（见 §4）：分支代码原本**无法启动 gateway**（循环导入），以及页面默认选中**最旧**一次运行。
- 未验证项见 §6（明确列出，未打勾）。

## 2. 命令证据

| # | 命令 | 结果 |
| --- | --- | --- |
| 1 | `python -m pytest tests/evaluation tests/knowledge -q`（worktree 内，主仓 venv 解释器） | `8 failed, 1880 passed` — 8 个失败全部是 **Windows 平台语义**（`chmod 0600/0700` 断言、`symlink` 需要特权），在 `feature/desktop-app` 基线上**同样失败**（基线复现 `7 failed, 44 passed`），非本次引入 |
| 2 | `python -m pytest tests/evaluation/knowledge -q` | `182 passed`（含本轮新增的 2 条导入顺序回归测试） |
| 3 | `python -m ruff check --no-cache nanobot/` | `Found 4 errors` — 全在 `nanobot/memory/__init__.py` 的存量 F401，基线同样存在 |
| 4 | `npx tsc -p tsconfig.build.json --noEmit` | 无输出（通过） |
| 5 | `npx vitest run src/tests/knowledge-eval-page.test.tsx src/tests/knowledge-eval-api.test.ts` | `19 passed` |
| 6 | `npx vitest run src/tests/i18n.test.tsx src/tests/workspace-routes.test.ts` | `34 passed`（10 语言 key 形状一致、插值一致） |
| 7 | `npx vitest run`（全量） | `2 failed | 162 passed | 2 skipped (166)` — 失败文件 `appearance-boot.test.ts`（3 例）在 `feature/desktop-app` 基线**同样失败**；`preview-thread.test.tsx` / `thread-shell.test.tsx` 为并行跑多文件时的时序抖动，复跑即过 |
| 8 | `npx vite build` | `✓ built in 11.90s`（注：`TEMP/TMP/TMPDIR` 必须指向 `webui/node_modules/.tmp`，否则 esbuild 临时文件被拒，报 `Access is denied`——本机既有环境问题，主仓同样复现） |
| 9 | `grep -rn "log₂\|2^grade\|÷" webui/src/i18n/locales/` | 无输出（公式串没有抄进语言文件） |

## 3. 端到端证据（分支代码 + 真实检索）

环境：worktree 网关跑在 `8765`(WebUI) / `8790`(评测) / `18791`(HTTP)，隔离实例配置
`.tmp-eval-instance/config.json`；知识库 `kb_9788851f8bbc`（2 篇文档）。

### 3.1 API 全链路

| 步骤 | 结果 |
| --- | --- |
| `POST /api/eval/v1/auth/login` | 200，取到 `csrf_token` |
| `GET /knowledge/capabilities` | 200，`enabled=true`，可见知识库含 `kb_9788851f8bbc` |
| `GET /knowledge/metrics` | 200，7 个指标 ID 齐：`hit_at_k / mrr / ndcg_at_k / pool_coverage / precision_at_k / recall_at_k / unscored_rate` |
| `POST /knowledge/datasets` | 201，得到 `dataset_id` |
| `POST /knowledge/runs`（`mode=keyword, limit=5`） | 202（入队，请求内不检索） |
| `GET /knowledge/runs/{id}/report` | `completed`；**聚合段** `expect`：`hit_at_k=1, precision_at_k=0.2, mrr=1, ndcg_at_k=1, recall_at_k=1, pool_coverage=1, unscored_rate=0`；**逐题段** 2 条，均 `scored` 且带 5 个指标 |
| 第二次运行（`mode=hybrid, rrf_k=60`） | 202 → `completed`，指标同上 |
| `GET /knowledge/compare?base=&candidate=` | 200，`metrics` 7 行（按 `label_source` 分组、带 `base/candidate/delta` 与 `direction`），`regressions` 列表结构完整 |

### 3.2 浏览器回归（msedge headless，1440×950）

截图：`.ai-runtime-artifacts/verifications/knowledge-rag-eval/knowledge-eval-{overview,runs,compare}.png`

1. 打开 `http://127.0.0.1:8765/#/knowledge-eval?bootstrapSecret=…` → 页面加载，左侧导航出现「检索评测台」入口并高亮。
2. 概览卡区：**5 张指标卡**（准确率@K / 召回率@K / 命中率@K / MRR / NDCG@K），每张卡下方**常驻一行公式**（内容与 `GET /knowledge/metrics` 一致）。
3. 降级徽标：跑 `vector_weight=1 / keyword_weight=0` 的配置后，页面顶部出现「**降级运行 · vector_unavailable**」与「降级运行的指标不能与正常运行直接对比」。
4. 指标口径区：7 行公式表（指标 / 计算公式 / 分子 / 分母 / 方向）+ 「召回率@k 是候选池相对值」的诚实边界说明。
5. 逐题结果表：每行 = 查询 × 配置，显示**查询原文**、5 个指标与状态（`已评分` / `不可评`）；未评的题显示「未评分」而不是 0。
6. 分区切换：数据集 / 运行 / 对比三个分区可切换并各自渲染。
7. 控制台：仅一条 `401 Unauthorized`（`/auth/session` 的未登录探测，请求随后自动登录成功），无 JS 报错。

## 4. 过程中发现并修复的缺陷

| # | 缺陷 | 影响 | 修复 | 证据 |
| --- | --- | --- | --- | --- |
| 1 | `nanobot/evaluation/knowledge/__init__.py` 在包初始化时导入 `routes` → `memory.routes` → `server` → `services`（`services` 又在模块级导入本包 `metrics`）形成**循环导入** | **gateway 根本无法启动**（`ImportError: cannot import name 'EvaluationService'`）；pytest 因导入顺序不同而**测不出来** | `__init__` 改为 PEP 562 惰性导出（`__getattr__`） | 新增 `tests/evaluation/knowledge/test_import_order.py`（2 例，子进程按 gateway 顺序导入）→ `182 passed` |
| 2 | 页面默认选中 `listRuns()` 的**第一条**（后端按创建时间**升序**返回）→ 默认看到最旧的一次运行 | 用户打开页面看到的是历史旧结果，误以为「没数据」 | 页面按 `createdAt` **降序**排列并默认选最新 | `knowledge-eval-page.test.tsx` 仍全绿；E2E 概览卡显示的已是最新一次运行 |

附带修掉两处前端与后端形状不一致（`fix` 一并落在同一提交里）：

- `compare` 响应退化清单的键是 `regressions`（客户端只认 `regressed*`）→ 客户端补齐该键并解析 `case_id/config_id/metricId/base/candidate/delta`。
- 降级原因落在运行的 `pool.degraded` → 客户端 `KnowledgeEvalRun` 补 `degraded` 字段，页面才能显示降级徽标。

## 5. 提交

| 提交 | 内容 |
| --- | --- |
| `4fd279a` | feat(webui): 注册 #/knowledge-eval 路由与导航项 |
| `1987b36` | feat(webui): 补齐知识评测台 10 语言文案 |
| （Task 11） | feat(webui): 新增知识检索评测台页面（概览卡 + 口径区 + 逐题表 + 三分区） |
| （修复） | fix(evaluation): 修复知识评测包循环导入导致 gateway 无法启动 |
| （修复） | fix(webui): 默认选中最新运行并对齐对比/降级响应形状 |

## 6. 未验证项（明确列出）

1. **LLM 判定组（`label_source=judge`）的真实质量**：本机无 judge provider（只有 `controlled` 受控档案），
   E2E 只覆盖了 `expect` 关键词标注组。判定链路的单测（`test_judge.py`）覆盖守卫与失败收敛，
   但**没有真实模型跑过一次 judge 组**。
2. **退化查询清单（`regressions` 非空）的浏览器呈现**：本轮语料下两次配置都没有变差，
   浏览器里该区显示「这些指标上没有查询变差」。退化清单的**计算与接口**由后端 HTTP 级测试
   `tests/evaluation/knowledge/test_routes.py::test_compare_returns_metric_deltas_and_regressions`
   覆盖（断言 `regressions` 非空且含 `recall_at_k`），**前端渲染分支未被真实数据验证**。
3. **`knob` 语义未验证**：`recall_at_k` 的「池相对」分母口径在真实多配置运行下是否符合预期，
   本轮只验证了单配置下 `recall=1` 的基本情况。
4. **10 语言逐条人工校对**：i18n 单测只保证 key 形状与插值一致，
   「文案是否地道」需要母语者复核（本轮已保证非 en locale 不是直接复制英文）。
5. **桌面端（Electron）打包**：未验证；本轮只验证了 Web 浏览器。

## 7. 复现方式

```bash
WT=D:/studyspace/project/.harness-worktrees/Zhixu/wt-knowledge-rag-eval
V=D:/studyspace/project/Zhixu/.venv/Scripts/python.exe

# 后端
cd "$WT" && "$V" -m pytest tests/evaluation/knowledge -q

# 前端（worktree 内 node_modules 是 junction；.vite 缓存被主仓占用 → 用本地 config）
cd "$WT/webui" && npx vitest run --config vitest.local.config.ts src/tests/knowledge-eval-page.test.tsx
cd "$WT/webui" && WINTMP=$(cd node_modules/.tmp && pwd -W) \
  && TMPDIR="$WINTMP" TEMP="$WINTMP" TMP="$WINTMP" npx vite build
```

E2E 脚本（未入库，属一次性验证脚本）：`.tmp-eval-instance/e2e-eval.cjs`，
输出 `e2e-out/e2e-report.json` 与三张截图。

## 8. 合流后回归（2026-11-10 晚，`feature/desktop-app` 上）

分支已合入 `feature/desktop-app`（merge 提交 `0a99ddf4`）。合流后在**用户实例**
（`8765` WebUI / `8790` 评测，真实知识库 `kb_c27ec95f0bb1`「E2E 临时库」）用 msedge 走了一遍完整用户路径：

| 步骤 | 结果 |
| --- | --- |
| 打开 `#/knowledge-eval` | 页面加载 ~2.1s，左侧「检索评测台」高亮，口径区 7 行公式均来自后端 |
| UI 建数据集（表单填名称 / 查询 / 期望关键词） | 成功，列表出现新数据集 |
| 运行分区选数据集 → 发起运行 | 202 入队，页面自动轮询 |
| 等运行结束 | 无人工刷新，概览卡与逐题表自动出数：准确率 0.125 / 召回率 1.000 / 命中率 1.000 / MRR 1.000 / nDCG 1.000，状态「已评分」，逐题行显示查询原文 |
| 切到对比分区 | 正常渲染（两次运行都没变差，退化清单为空态） |
| 控制台 | 只有一条 `401 Unauthorized`（`/auth/session` 未登录探测，随后自动登录成功），无 JS 报错 |

证据：`verifications/knowledge-rag-eval/merged-{01-page,02-dataset,04-report,05-compare}.png`、
`merged-summary.json`。

### 合流后又修掉 1 个缺陷

| 缺陷 | 影响 | 修复 |
| --- | --- | --- |
| 轮询只刷新**运行列表**，选中运行的**报告**不会重取 | 发起运行后页面一直停在「还没有逐题结果」，必须手动刷新才看得到结果（后端其实早已 `completed`） | 把选中运行的状态纳入报告 effect 依赖，`queued/running` 翻成终态时重新拉一次（`d28f0ab`） |

修复后同一条浏览器路径复跑通过（上面的表格即为修复后的结果）。

## 9. 布局调整：列表 / 详情两级（用户反馈）

**反馈**：一屏把概览卡、口径表、逐题表、三个分区全铺开太长；参照知识库页（列表 → 详情）拆成两级。

**改动**（`8ccad73`）：

- **列表页**：只保留 `数据集 / 运行 / 对比` 三个分区；运行行改成「查看报告」按钮。
- **详情页**：面包屑（← 检索评测台 / 运行 id）+ 运行头部卡（状态、配置数、完成时间、刷新）
  + 概览卡（5 指标 + 公式行 + 降级徽标）+ 逐题表 + **折叠的**指标口径区（`<details>`）。
- 发起运行后自动进详情；从详情返回落回原分区（与知识库页一致）。
- 新增文案键 `eval.action.viewReport`、`eval.detail.title`（10 语言）。

**验证**：

| 项 | 结果 |
| --- | --- |
| `npx vitest run src/tests/knowledge-eval-page.test.tsx` | 7 passed（新增「列表页不含详情内容」「详情可作为第二页打开并返回」两条断言） |
| `npx vitest run`（page + api + i18n） | 47 passed |
| `npx tsc -p tsconfig.build.json --noEmit` / `npx vite build` | 通过 |
| 浏览器（msedge）| 列表页无概览/逐题/口径 → 建集 → 发起运行自动进详情 → 出指标（准确率 0.125 / 召回率 1 / 命中率 1 / MRR 1 / nDCG 1，已评分）→ 展开口径表 → 返回落回运行分区 → 切对比分区正常；控制台仅 401 探测 |

证据：`verifications/knowledge-rag-eval/split-{01-list,03-detail,04-guide}.png`、`split-summary.json`。

> 该轮运行显示「降级运行 · vector_unavailable」：网关进程内向量后端当时不可用（本机 hf-mirror 连不上时的
> 既有现象），检索退化为关键词通道，指标仍是有效的——降级徽标正是为这种情况存在的。


