---
artifact: spec
route: source-driven-development -> brainstorming
skills:
  - source-driven-development
  - brainstorming
skills_evidence:
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
  - harness-kit/.agents/skills/brainstorming/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - harness-kit/core/artifacts.md
  - harness-kit/project.profile.md
  - harness-kit/context-map.md
  - .ai-runtime-artifacts/stack/2026-11-10-knowledge-rag-eval-stack.md
  - .ai-runtime-artifacts/research/2026-11-10-rag-evaluation-landscape-research.md
  - .ai-runtime-artifacts/specs/2026-11-09-knowledge-retrieval-spec.md
  - .ai-runtime-artifacts/specs/2026-11-02-memory-evaluation-platform-spec.md
  - .ai-runtime-artifacts/plans/2026-11-10-knowledge-center-alignment-plan.md
  - 用户本轮确认：首版只做检索级、ground truth 用 LLM 自动判相关、页面挂独立路由 #/knowledge-eval
created_at: 2026-11-10
status: draft
approved: false
---

# 知序知识检索评测台：设计

## 1. 要解决的问题

开发者今天调整 `vector_weight` / `rrf_k` / `mmr_lambda` / `dedup_*`，明天改 chunk 策略或 embedding 配置，**只能靠 golden queries 的一个命中率数字（≥0.8）判断有没有回归**——它不告诉他「召回率从 0.72 掉到 0.65」「哪 3 条查询变差了」「混合检索到底比纯关键词好在哪」。

本项目已有的检索 spec §10 已经把指标口径钉死（Recall@5 ≥ 0.9 / MRR@10 ≥ 0.7 / exact-hit ≥ 0.95），并写明「质量验收用 golden queries + 三指标，融合参数可调，必须有可回归的度量口径」——**但这口径只存在于文档，运行时没有任何东西在算它**。spec §5.8 的参数表有 14 个可调项，没有一个能对比。

首版交付：**一个可交互的检索质量测评台**，能对同一个查询集跑不同检索配置、用 LLM 自动判定 chunk 相关性、给出逐题与聚合指标、并把两次运行的差异摊开。

首版用户：知序开发者，本地单用户评估。

**首版明确不做**：生成端质量（faithfulness / 答案正确率）——用户已确认只做检索级；不引入 Ragas / DeepEval / Phoenix 等外部依赖；不做人工 chunk 标注界面；不做 cross-encoder / LLM 精排。

## 2. 方案比较与选择

| 方案 | 收益 | 代价 | 结论 |
| --- | --- | --- | --- |
| **A. 扩展现有 `nanobot/evaluation/`，新增 `knowledge/` 子包** | 复用评测平台已有的 RouteDef 挂载、Host/Origin guard、CSRF、限流、405 兜底、SQLite 存储、任务编排、报告与对比；与记忆评测同构，开发者心智一致 | 需给评测服务开一条「拿到 config / provider / 知识服务」的缝；独立 `nanobot eval serve` 进程与 gateway 并存时 ChromaDB 双客户端（同路径 SQLite）需说明 | **推荐** |
| B. 在 gateway 侧挂 `/api/settings/knowledge/eval/*` | 与知识库同进程同实例，检索零偏差；provider 现成 | 评测的数据集/运行/结果存储要从零建；任务编排与报告要重造；与记忆评测两套心智 | 不选 |
| C. 只扩 pytest / CLI | 最便宜（1–2 天） | 没有标注、没有交互对比、不满足「测试页面」诉求 | 不选 |

**推荐 A**。核心理由：记忆评测 `nanobot/evaluation/memory/` 已验证「专项子包 + 自有表 + 独立路由组」的形态可行（12 个模块、约 10k 行含前端），本设计是它的检索侧镜像，而非新范式。

**与 B 的关键差异说明**：A 方案下检索执行发生在评测服务进程内，但用的是**与产品完全同源的**知识服务——同一 DB 路径、同一 embedding 配置（因此同一 embedding 身份与同一 Chroma collection）、同一 `Retriever` 融合实现。**不是**重新实现一遍检索。

## 3. 与业界做法的关系

调研结论见 `.ai-runtime-artifacts/research/2026-11-10-rag-evaluation-landscape-research.md`，本设计的取舍：

| 业界做法 | 本设计 | 理由 |
| --- | --- | --- |
| TREC `Recall@k`（分母=gold 数量）与 `Hit@k` 分立 | 两者都出，命名不混 | 外部数字口径混乱，必须显式区分（研究 §1） |
| UMBRELA：0–3 级相关性判定、temperature=0 | 采用，0–3 级，判「chunk 是否回答该 query」 | 检索级只需段落级判定，无需 nugget 抽取（研究 §2.2） |
| TREC pooling：多路候选合并成池再判 | 采用：pool = 各被评配置候选的并集 | 这是「Recall 有意义」的前提，也是跨配置可比的基础（研究 §2.1） |
| 标注绑切分版本 | gold 绑 `content_hash`，切分变更后标失效 | 切分规则一改旧 chunk id 全失效（研究 §5.4） |
| 判分材料是数据不是指令 | 沿用 `graders/judge.py` 的守卫句式 | chunk 正文是用户文档，可能含提示注入 |
| 只看均值掩盖长尾 | 逐题 + 聚合双出，聚合附覆盖度与未评率 | 研究 §5.6 |
| Ragas 的 context precision/recall | **不采用** | 答案句级、需 reference 答案，与 chunk 级 gold 不匹配（研究 §1） |

## 4. 组件与边界

### 4.1 后端 `nanobot/evaluation/knowledge/`

| 模块 | 职责 | 不做 |
| --- | --- | --- |
| `models.py` | Pydantic 模型：数据集、用例、检索配置覆盖、运行、逐题结果、标签、指标结果 | 不含业务逻辑 |
| `metrics.py` | 指标定义与计算：hit@k / precision@k / mrr / ndcg@k / recall@k / pool_coverage；`not_assessable` 语义 | 不做语义判定 |
| `judge.py` | LLM 相关性判定：批量、守卫、指纹、解析校验 | 不检索、不算指标 |
| `runner.py` | 异步批量执行：取候选 → 并池 → 判分 → 打分 → 落库；进度与取消 | 不管 HTTP |
| `repository.py` | `evaluation.db` 内的新表读写 | 不含判分逻辑 |
| `routes.py` | `knowledge_route_defs()` → `web.RouteDef` 列表 | 不 import 传输层 |

### 4.2 知识检索侧的新增缝隙（唯一一处产品代码改动）

`KnowledgeService` 当前把 `_repo` / `_embedder` / `_vector` / `_retriever` 设为私有，且 `search()` 用固定配置。为支持「同一实现、不同参数」评测，在 `nanobot/knowledge/service.py` 增加一个**只读构造器**：

```python
def retriever_with(self, settings: RetrieverSettings) -> Retriever:
    """用同一份 repo/embedder/vector 构造指定参数的检索器（评测用）。"""
```

它复用同一批依赖，只替换 `RetrieverSettings`，因此**评测跑的融合/去重/MMR 与产品逐行同源**。

### 4.3 评测服务的接线缝

`create_eval_app` / `create_app` 增加**可选** `config` 参数：

- 嵌入式（桌面端 / `nanobot gateway`）：`gateway_runtime._start_embedded_evaluation` 已有 `config` → 传入 → 评测进程内 `get_knowledge_service(config.knowledge)` 命中**进程级单例**（与 gateway 的知识服务是同一个实例）。
- 独立 `nanobot eval serve`：`eval_commands.serve_eval` 已 `_load_config` → 同样传入；此时是新进程、新实例，但 DB 路径与 embedding 配置同源。
- 无 config（如离线 fixture 模式）→ 知识评测能力报 `unavailable`，不报错。

provider 解析：`make_provider(config, preset_name=...)`，与产品 provider 解析路径完全一致（含 auto / fallback）。评测侧新增 `knowledge/judge.py` 复用 `graders/judge.py` 的守卫与解析校验模式，但量表改为相关性 0–3。

### 4.4 前端

- `webui/src/components/workspace/KnowledgeEvalPage.tsx` + 子组件（概览卡 / 数据集 / 运行 / 对比 四个分区）。
- `webui/src/lib/knowledge-eval-api.ts`：复用 `evaluationTransport`（端口 8790，与记忆评测同源，跨源已验证可行）。
- 路由：`workspace/routes.ts` 解析 `/knowledge-eval`；`workspace/contracts.ts` 增 `WorkspaceView` 成员；`WorkspaceNavigation` 增导航项；`App.tsx` 增渲染分支。
- i18n：新增文案需在 **10 个 locale** 全部补齐——`webui/src/tests/i18n.test.tsx` 断言各 locale 与 en 的 key 形状完全一致（missing/extra 均为空），漏一个语言即测试失败。

## 5. 数据模型

### 5.1 表（`evaluation.db`，命名对齐既有 `memory_*` 表）

| 表 | 关键列 |
| --- | --- |
| `knowledge_eval_datasets` | `id` / `name` / `notes` / `created_at` |
| `knowledge_eval_cases` | `id` / `dataset_id` / `query` / `expect_any`(json) / `kb_ids`(json) / `mode` / `limit` / `label_source` / `ordinal` |
| `knowledge_eval_runs` | `id` / `dataset_id` / `status` / `retrieval_config`(json) / `judge_config`(json) / `metrics`(json) / `pool`(json) / `created_at` / `finished_at` |
| `knowledge_eval_results` | `run_id` / `case_id` / `status` / `hits`(json) / `labels`(json) / `metrics`(json) / `error` |

### 5.2 用例的两种标注来源（禁止混算）

| `label_source` | 含义 | 判定方式 | 成本 |
| --- | --- | --- | --- |
| `expect` | 查询带 `expect_any` 关键词 | 确定性：命中任一关键词即相关 | 0，可离线 |
| `judge` | 查询无 `expect_any` | LLM 判 0–3 相关性（≥2 视为相关） | 每查询 1 次批量调用 |

**规则**：一次运行的指标**按 `label_source` 分组出**，不做混合聚合（对齐记忆评测「报告明确标注路径，禁止混算」）。`expect` 组同时充当**免成本的确定性锚点**，也是既有 `tests/knowledge/fixtures/golden_queries.json` 的迁移目标形态。

### 5.3 gold 与切分版本绑定

- 标签存 `content_hash`（块正文哈希）而非仅 `chunk_id`。
- 判分时若某候选块的 `content_hash` 与入库时不一致 → 该标签标 `stale`，该题转 `not_assessable`，并在报告里提示「切分已变更，需重标」。

## 6. 指标口径（逐条钉死）

`k` = 被评配置的 `limit`；`pool` = 本次参与比较的全部配置的候选并集；`grade ≥ 2` 视为「相关块」。

### 6.1 指标定义与计算公式

下表的「计算公式」是**契约级口径**：后端 `metrics.py` 用它算，首页用它展示，报告用它标注。公式串由后端单点定义并经 `GET /knowledge/metrics` 下发，前端只渲染——**不在 10 个语言文件里各抄一份**，避免公式与实现漂移。

| 指标 ID | 展示名 | 计算公式 | 分子 | 分母 | 方向 |
| --- | --- | --- | --- | --- | --- |
| `precision_at_k` | 准确率 | `相关块数(top-k) ÷ k` | top-k 中 grade≥2 的块数 | k | 越高越好 |
| `recall_at_k` | 召回率 | `相关块数(top-k) ÷ 相关块数(pool)` | top-k 中 grade≥2 的块数 | 候选池中 grade≥2 的块数 | 越高越好 |
| `hit_at_k` | 命中率 | `命中查询数 ÷ 可评查询数` | top-k 含 ≥1 相关块的查询数 | 全部可评查询数 | 越高越好 |
| `mrr` | 首个命中倒数 | `平均( 1 ÷ 首个相关块名次 )` | — | — | 越高越好 |
| `ndcg_at_k` | 归一化折损累计增益 | `DCG ÷ IDCG`，其中 `DCG = Σ (2^grade − 1) ÷ log₂(名次+1)` | — | — | 越高越好 |
| `pool_coverage` | 候选池相关占比 | `相关块数(pool) ÷ 池候选总数` | 池中 grade≥2 的块数 | 池候选总数 | 越高越好 |
| `unscored_rate` | 未评率 | `not_assessable 查询数 ÷ 总查询数` | `not_assessable` 查询数 | 全部查询数 | 越低越好 |

**聚合规则**：`precision_at_k` / `recall_at_k` / `mrr` / `ndcg_at_k` 均为**逐题值再平均**（macro）；`hit_at_k` / `pool_coverage` / `unscored_rate` 为**整体比值**（micro）。

**每次测评的输出义务（不可省略）**：

1. **逐题**：每条查询在每个被评配置下都必须带 `precision_at_k` / `recall_at_k` / `hit_at_k` / `mrr` / `ndcg_at_k` 五个值 + `status`（`scored` / `not_assessable` / `failed`）。
2. **聚合**：按 `label_source` 分组给出上表全部 7 个指标；任一指标不可算时值为 `null` + `status=not_assessable`，**不补 0、不省略该指标**。
3. **公式随行**：报告与首页都能看到每个指标的计算公式（首页为主入口，报告内可展开）。

**`recall_at_k` 的诚实边界（必须在 UI 文案与报告里写明）**：分母是「本次候选池中被判定为相关的块数」，不是「全库中相关块总数」。当被评配置把相关块整个漏掉、而其他配置找到了时，它会被计入 pool 分母——这正是 pooling 的设计意图（跨配置可比），但它**不等于**绝对召回率。绝对召回率需要人工 chunk 标注，属 v2。

**`not_assessable` 的四种来源**（一律不补 0）：

1. 判分失败 / 超时 / 输出非法；
2. 查询无效——检索四态中的 `no_scope` / `filtered` / `empty_query_tokens`（配置或查询问题，不是「没搜到」）；
3. gold 失效（`content_hash` 不匹配）；
4. 该查询未进 pool（配置候选为空）。

**检索四态映射**（对齐检索 spec §3）：`no_candidates` = 真的没搜到 → 记 0 分；`no_scope` / `filtered` / `empty_query_tokens` → `not_assessable`。`KnowledgeSearchError`（全部路由失败）→ 该题 `failed`，不参与聚合、不计 0。

## 7. 执行流程

```
用户：选 KB → 建查询集 → 选被评配置（≥1，可含基线+候选）
  ↓
POST /knowledge/runs → 202（入队，不在请求里跑）
  ↓
runner（评测服务进程内 asyncio 任务）
  1. 逐查询：按各配置检索 → 各取 top-`limit` 候选
  2. 并池：所有配置候选按 chunk_id 去重合并 → 记录每块的 content_hash
  3. 判分：`expect` 组走关键词；`judge` 组每查询一次批量 LLM 调用
     （输入 = query + 各候选块截断正文，输出 = 严格 JSON 数组）
  4. 打分：按配置逐题算指标，再聚合
  5. 落库：runs.metrics + results
  ↓
GET /knowledge/runs/{id}/report → 指标 + 逐题明细 + 覆盖度
GET /knowledge/compare?a=..&b=.. → 逐指标差 + 退化查询清单
```

**并发**：判定是唯一 I/O，按查询并发，上限 4；检索是本地同步调用，`asyncio.to_thread` 包裹（对齐 `Retriever` 线程内同步的约定）。

**取消**：`POST /knowledge/runs/{id}/cancel` → 置取消位，runner 在查询边界检查。

## 8. 错误处理

| 场景 | 处理 |
| --- | --- |
| 判分超时 / provider 异常 / 输出非法 | 该查询 `not_assessable`，记录 `error`；运行继续；报告附 `unscored_rate` |
| 判分响应含 `tool_calls` | 直接拒收该次判定（同 `graders/judge.py`），不执行任何工具 |
| 判分材料含注入文本 | 守卫句式 + 只取结构化字段，材料只当数据 |
| 检索全部路由失败 | 该题 `failed`（不是空结果），不参与聚合 |
| 检索降级（`embedding_mismatch` / `vector_unavailable` 等） | 运行标 `degraded` 并记录 `degraded` 明细；**降级运行与正常运行的指标不得直接对比**，报告显式标注 |
| embedding 身份与索引不一致 | 沿用产品既有降级语义，不在评测侧另造口径 |
| 数据集为空 / 配置非法 | 请求期 4xx + 稳定错误码，不入队 |

## 9. 前端页面结构

按「概览卡 + 明细分区」组织（概览在上、明细在下）：

1. **概览卡区**：数据集名 / 库作用域 / 最近一次运行的主指标（precision@k、recall@k、hit@k、mrr、ndcg@k）+ 未评率徽标 + 降级徽标。**每个指标卡带公式**——卡下方常驻一行公式串（如 `相关块数(top-k) ÷ k`），点击可展开完整口径说明。
2. **指标口径区（首页必备）**：一张「计算公式」表，列出全部 7 个指标的展示名、公式、分子、分母、方向，以及「recall 是池相对值」的诚实边界说明。**公式文本由后端 `GET /knowledge/metrics` 下发**，前端只渲染——不在语言文件里抄公式。
3. **数据集分区**：查询列表、每条查询的 `expect_any`、标注来源、批量编辑。
4. **运行分区**：被评配置列表（基线 + 候选，可覆写 `vector_weight`/`keyword_weight`/`rrf_k`/`pool_size`/`keyword_pool_size`/`mmr_lambda`/`relevance_band`/`dedup_*`/`mode`/`limit`）、发起运行、实时进度。
5. **逐题结果区**：每次测评的**逐题**指标表——每行一条查询，列出该题在每个配置下的 precision@k / recall@k / hit@k / mrr / ndcg@k 与 `status`；`not_assessable` 显示「未评分」而非 0。
6. **对比分区**：两次运行的逐指标差、**逐查询退化清单**（哪些查询在候选配置下变差）、逐题命中明细（返回块、分数、判定等级、理由）。

**每次测评的呈现义务**：一次运行完成后，**概览卡（聚合）+ 逐题结果表（逐题）必须同时可见**，不允许只有聚合数字。聚合值旁标注「N 条查询参与计算 / M 条未评」，让用户知道分母。

**隐私提示**：使用 `judge` 标注来源时，chunk 正文会发给配置的模型服务（可能是远程）。页面在开启 `judge` 运行前必须显式提示这一点。

## 10. 测试

### 10.1 Python（pytest）

| 测试 | 要点 |
| --- | --- |
| `metrics` 单测 | 手算期望值的 hit@k / precision@k / mrr / ndcg@k / recall@k / pool_coverage；`not_assessable` 不进分母 |
| `judge` 单测 | 合法 JSON、缺字段、越界分数、非 JSON、含 `tool_calls`、注入文本被当数据、批量子集、指纹随提示词版本变化 |
| `runner` 单测 | 假检索器 + 假判定器：并池去重、`expect`/`judge` 分组不混算、四态映射、`failed` 不计 0、降级标注 |
| `repository` 往返 | 建集 → 写运行 → 读回；`content_hash` 不匹配标 stale |
| `retriever_with` | 同 repo/embedder/vector、不同 settings 产不同排序；不改动产品默认行为 |

### 10.2 WebUI（vitest）

- api client 请求形状与错误映射；页面分区渲染与空态。

### 10.3 浏览器 E2E（人工 + 脚本）

建集 → 跑 `expect` 组（离线可跑）→ 出指标 → 加候选配置 → 对比出退化清单。**「测试」在本项目指功能浏览器回归，不只是 pytest 绿。**

## 11. 交付顺序（建议）

1. 后端骨架：`models` + `repository` + `metrics`（可离线单测）
2. `judge` + `runner` + `routes`（用 `expect` 组端到端跑通，无 LLM 依赖）
3. 前端页面四分区 + 对比
4. 接入 `judge` 组（真实 provider）+ 降级标注与指纹展示
5. 浏览器 E2E + 文档（知识库页面入口、README 片段）

## 12. 风险与待确认

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| LLM 判定存在系统性偏差（自评/宽松） | 指标整体偏高 | 记录判定模型 + 提示词指纹；支持换模型重判并出**一致率**；`expect` 组作确定性锚点；v2 接人工覆写 |
| 判分成本随查询数×候选数增长 | 成本上升 | 每查询 1 次批量调用（非每块 1 次）；并发上限 4；`expect` 组零成本 |
| 候选池只覆盖被评配置的召回 | `recall_at_k` 是池相对值 | 报告显式写明；对比固定同池，跨运行不直比 |
| 独立 `nanobot eval serve` 与 gateway 并存时 ChromaDB 同路径双客户端 | SQLite 锁等待 | 评测只读；Chroma 已设 `busy_timeout`；文档建议优先用桌面端/网关内嵌模式 |
| 切分策略变更使全部 gold 失效 | 指标无法对比 | gold 绑 `content_hash`；失效即 `not_assessable` 并提示重标 |
| 跨模式分数不可比（检索 spec §5.2） | 误读 | 报告只呈现跨模式**集合质量**（hit/recall/mrr），不呈现跨模式 score 对比；score 继续标注为相对排序分 |
| i18n 10 语言同步 | 漏一个即测试红 | 文案一次性补齐 10 个 locale；交付前跑 `bun run test` |

**待确认（实现前可再定）**

1. `ndcg_at_k` 的 gain 形式：提案 `2^grade − 1`（标准 DCG），备选线性 gain。
2. 判分相关性等级阈值：提案 `grade ≥ 2` 视为相关（对齐 UMBRELA 的 ≥2 二值化）。
3. 每查询候选上限与正文截断长度：提案候选 ≤ 20、正文每块截断 600 字符。
4. `expect` 组是否也允许人工写「不相关」标记（负例）——v1 不做，v2 再议。

## 13. 非目标

- 生成端质量（faithfulness、答案正确率、拒答正确率）——v2
- 人工 chunk 级标注界面——v2（数据模型已预留 `label_source` 与 `stale` 语义）
- cross-encoder / LLM 精排、查询改写、doc2query 第三路召回
- 多知识库 / 多 embedding 模型的分组对比矩阵——v2
- 导出到 Phoenix / Langfuse 等外部平台

## 14. 文档落点

- 知识中心页面增加「评测台」入口（`#/knowledge-eval`）。
- `docs/` 下补一节：检索评测口径（与本文 §6 一致），避免文档与实现漂移。
