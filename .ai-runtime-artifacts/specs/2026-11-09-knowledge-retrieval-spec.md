---
artifact: spec
route: superpowers:brainstorming
scope: retrieval-only
skills:
  - brainstorming
  - source-driven-development
skills_evidence:
  - harness-kit/.agents/skills/brainstorming/SKILL.md
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
source:
  - .ai-runtime-artifacts/specs/2026-11-09-knowledge-center-spec.md
  - .ai-runtime-artifacts/specs/2026-11-09-knowledge-storage-spec.md
  - .ai-runtime-artifacts/plans/2026-11-09-knowledge-center-plan.md
  - .ai-runtime-artifacts/stack/2026-11-09-knowledge-center-stack.md
  - WeKnora/docs/原理/00-总览-端到端运行原理.md
  - WeKnora/docs/原理/01-存储与索引原理.md
  - WeKnora/docs/原理/02-任务与状态机原理.md
  - WeKnora/docs/原理/03-检索原理.md
  - WeKnora/docs/原理/04-知识图谱原理.md
  - WeKnora/docs/原理/05-Agent检索与上下文装配原理.md
  - WeKnora/docs/原理/06-检索后处理-粗排与精排.md
  - WeKnora/docs/原理/07-案例-一次混合检索的完整数据流.md
  - WeKnora/docs/原理/08-上下文拼装与问题生成原理.md
  - nanobot/knowledge/tokenizer.py
  - nanobot/knowledge/embedding.py
  - nanobot/knowledge/models.py
  - nanobot/memory/repository.py
  - .ai-runtime-artifacts/verifications/2026-11-09-knowledge-retrieval-fusion/verification.md
created_at: 2026-11-09
status: draft
approved: false
parent: 2026-11-09-knowledge-center-spec.md
---

# 知序知识中心 · 检索层方案（v1）

> 本文只覆盖**检索**：召回、融合、排序、去冗余、降级语义、装配与工具输出契约。
> 存储（DDL / FTS / Chroma / 状态机）见 [`2026-11-09-knowledge-storage-spec.md`](2026-11-09-knowledge-storage-spec.md)；
> 分块算法、入库流水线、CLI/WebUI 入口见父方案 [`2026-11-09-knowledge-center-spec.md`](2026-11-09-knowledge-center-spec.md)。
> **本文与父方案 §8 冲突时以本文为准**（父方案 §8 已加指针并同步修正项）。

## 0. 决定摘要

| # | 决定 | 依据 |
| --- | --- | --- |
| R1 | 融合用**加权 RRF（名次融合）**，不用分数融合 | bm25 无上界且随语料漂移，余弦相似度分布集中；两者不可比（§5.1） |
| R2 | 归一化走 WeKnora `06 §2.2` 的**三条互斥路径**：单路用该路原生分，双路才用加权 RRF / maxRRF | 与 WeKnora 对齐；保住 cosine 的绝对信息（§5.2，**撤回上一版的「有效权重归一化」**） |
| R3 | 去重三级：ID 合并 → 内容签名 → **部分包含（包含率口径）** | WeKnora `03 §6` / `06 §2.4` 的三级结构（§5.4，**撤回上一版的「Jaccard 阈值降到 0.6」**） |
| R4 | 增加**同文档邻块冗余抑制**：MMR 相似度对 `同 doc 且 |Δchunk_index| ≤ 1` 抬到下限 **0.25** | 重叠窗口产生的冗余是字面相似度测不出来的（§2.1、§5.5） |
| R5 | MMR 前加**相关性带**（`rel ≥ rel_top − 0.15`），λ 保持 0.7 | MMR 的多样性惩罚量纲可能压过相关性差异（§2.2、§5.6） |
| R6 | **MMR 决定选哪些，展示顺序按融合分降序**（分数单调） | LLM 看到的 score 必须单调可解释；MMR 只解决集合冗余（§5.7） |
| R7 | 候选池**不对称**：关键词池 `min(max(limit×10, keyword_pool_size),300)`，向量池 `min(max(limit×5, pool_size),200)` | FTS 单次 0.6 ms（存储方案 E15），向量侧含 embedding 计算与 Chroma 查询（§4.3） |
| R8 | 工具输出的 `score` 明确标注为**当前模式下的相对排序分**，非置信度、未精排 | RRF 分数只反映名次，不反映绝对相关性（§7.3） |
| R9 | 每次结果标注命中路由 `routes="vector,keyword"`，让模型知道证据来自哪一路 | 失败/空/被过滤三态之外，还需「从哪一路来的」以决定是否改写 query（§7.3） |
| R10 | 排序全部带确定性 tie-break（分数降序 → chunk_id 升序） | 重跑、回归测试、缓存都要可复现（§5.3） |
| R11 | 关键词路保留 `bm25` 原始分仅用于**展示与诊断**，不进融合 | 融合只用名次是 RRF 的定义，不能混用两种口径（§5.1） |
| R12 | 质量验收用 **golden queries + Recall@5 / MRR@10 / exact-hit** 三指标 | 融合参数可调，必须有可回归的度量口径（§10） |
| R13 | `embedding_backend='none'` 的 KB **不算** `embedding_mismatch`，单独记 `vector_disabled:<kb>` | 该库本就没有向量，属配置事实而非索引漂移；误报降级会污染工具输出（§4.2） |
| R14 | 单路检索时**不用 RRF**：向量路保留 cosine、关键词路除以自身最大值 | WeKnora `06 §2.2`；RRF 只在两路共识时才有意义（§5.2） |

## 1. 范围与边界

**本文负责**

- 检索管线的逐步精确口径（作用域 → 分词 → 召回 → 融合 → 去重 → 抑制 → 排序 → 装配）。
- 两路召回的候选生成规则与边界条件。
- 三条互斥归一化路径（单路原生分 / 双路加权 RRF）与降级状态的关系。
- 去重、邻块冗余抑制、MMR 的参数与量纲。
- 降级 / 失败 / 空结果 / 被过滤四态语义。
- 装配与工具输出契约（`knowledge_search` / `knowledge_read`）。
- 融合与排序的测试矩阵与质量评估口径。

**本文不负责**

- 存储结构、FTS 表与 Chroma collection（存储方案）。
- 分块算法与入库状态机（父方案 §7）。
- Embedder 的协议与远端实现细节（父方案 §10.1；本文只依赖 `identity` 三元组）。
- CLI / WebUI 路由与页面（父方案 §12 / §13）。
- 精排（cross-encoder）与查询改写：v1 只留 seam（§13）。

### 1.1 与 WeKnora 原理文档的对应关系

WeKnora 原理共 9 篇（`00`–`08`）。本文的对照如下：

| WeKnora 原理 | 对应章节 | v1 状态 |
| --- | --- | --- |
| `00 §2` 五类存储 / 真源与派生 | 存储方案 §3 | 对齐 |
| `00 §6` 七条系统级不变量 | 存储方案 §3；本文 §3 四态语义 | 对齐 |
| `00 §7` 失败模型（坏了降级成什么） | 本文 §6 | 对齐 |
| `01 §2` 结构真源与不变量 | 存储方案 §6 | 对齐 |
| `01 §5` 分块降级链 + 校验器 | 父方案 §7.1 | 对齐（无 docreader） |
| `01 §5.4` 父子分块 | — | **裁剪**：不做父块，改用 `knowledge_read` 扩读 |
| `01 §6` embedding 是配置不是依赖 | 父方案 §10.1 | 对齐 |
| `01 §7` 索引可插拔 / 幂等 upsert | 存储方案 §7 §8 | 对齐 |
| `02 §7` 文档状态机（含 `finalizing`） | 父方案 §7；存储方案 §10 | 对齐（v1 无富化扇出 → 无 `finalizing`） |
| `02 §8` 幂等三道防线 + attempt/epoch | 父方案 §7；存储方案 §10 | 对齐 |
| `03 §5` 分数代数与加权 RRF | 本文 §5.1 §5.2 | 对齐（**单路走原生分**，见 §5.2） |
| `03 §5.4` `bestRanks`（跨列表取最佳名次） | — | **v1 不需要**：每路只有一张列表；多 KB / 多引擎分组时再引入（§13） |
| `03 §6` 去重三级 | 本文 §5.4 | 对齐（阈值按分词粒度重标定，见 §5.4） |
| `03 §7` cross-encoder 精排 | — | **裁剪**：v1 不做，留 `Reranker` seam |
| `03 §8` MMR | 本文 §5.6 | 对齐 + 加相关性带 |
| `03 §9` 上下文装配（CHUNK_MERGE / FILTER_TOP_K） | 本文 §7.1 | **简化**：只做面包屑拼接与短块补邻，不做 passage 级合并 |
| `05 §4.2` 幻觉句柄校验 | 本文 §7.4；父方案 §9.2 | 对齐 |
| `05 §4.5` 失败 ≠ 空结果 | 本文 §3 §6 | 对齐 |
| `05 §5` 输出契约（XML + 句柄） | 本文 §7.3 | 对齐 |
| `05 §6` 上下文经济学（三道预算） | 本文 §7.1 §8；父方案 §9 | 对齐 |
| `06 §2` 粗排后处理四步 | 本文 §3 §5 | 对齐（无「截断到 200」余量概念，v1 无精排） |
| `06 §3` 精排五步 | — | **裁剪**：阈值三档降级、复合分、passage 拼装均属精排 |
| `06 §3.6` 装配会改变名次 | 本文 §5.7 | **有意不同**：v1 装配不改序（见 §5.7） |
| `07` 一次混合检索的完整数据流 | 本文 §5.5；[verification](../../verifications/2026-11-09-knowledge-retrieval-fusion/verification.md) | 用作口径标定与回归参照 |
| `08 §2` 两段拼装（prompt 组装层） | — | **裁剪**：属 prompt 组装，不在检索层 |
| `08 §5` 问题生成（doc2query） | 父方案 §18.1 | 规划在 v2 |
| `04` 知识图谱 | — | **裁剪**：v1 不做（父方案 §1） |

## 2. 本轮证据（实测）

**复跑命令**（证据留档：[`verifications/2026-11-09-knowledge-retrieval-fusion/verification.md`](../../verifications/2026-11-09-knowledge-retrieval-fusion/verification.md)）：

```bash
.venv/bin/python .ai-runtime-artifacts/verifications/2026-11-09-knowledge-retrieval-fusion/probe_fusion.py
```

探针使用仓库已实现的 `nanobot/knowledge/tokenizer.py`（`unigram+bigram`），文本为高熵中文串（模拟真实 prose 的 token 多样性）。

### 2.1 E1：字面相似度必须看包含率，不能只看 Jaccard

以高熵中文文本（800 字块，`chunk_size=800`、`chunk_overlap=120`）实测：

| 关系 | token 数 | Jaccard | 包含率 `|A∩B|/|A|`（A=短文本） |
| --- | --- | --- | --- |
| 相邻块（共享 120 字 overlap） | 1578 / 1584 | 0.091 | 0.167 |
| 共享一半内容 | 1578 / — | 0.336 | 0.504 |
| 完全无关 | 1578 / 1586 | 0.009 | 0.018 |
| **真包含（摘要 vs 正文）** | 1578 / — | **0.252** | **1.000** |

结论：

1. **纯 Jaccard 阈值抓不到「真包含」**：摘要 vs 正文的 Jaccard 只有 0.252，无论阈值取 0.6 还是 0.85 都漏判，
   而这正是 WeKnora `03 §6` 级别 ③ 要抓的主要重复（父块 vs 子块、正文 vs 摘要）。
2. **包含率口径对分词粒度不敏感**：只有真包含才接近 1.0（0.167 / 0.504 / 0.018 / 1.000），
   天然满足「短文本独有 token ≤2%」的语义（WeKnora `06 §2.4`）。
3. **overlap 邻块不属于去重范畴**：它的包含率只有 0.167，去重不该管它；邻块冗余由 R4 的邻块规则处理。
4. `unigram+bigram` 分词**不会**虚高相似度（无关块 0.009），所以 0.6 的 Jaccard 下限不会被噪声触发。
5. **邻块相似度下限取 0.25**：需高于邻块实测值 0.091（否则检不出），又需低于「共享一半内容」的 0.336
   （否则会把真正不同的块压到同一档）。0.25 落在 (0.091, 0.336) 区间内。

### 2.2 E2：MMR 的多样性惩罚可能压过相关性

父方案公式 `score = λ·rel − (1−λ)·max_sim`：

| 场景 | 计算 | 结果 |
| --- | --- | --- |
| A：rel 0.98，与已选项 sim 0.90 | `0.7×0.98 − 0.3×0.90` | 0.416 |
| B：rel 0.95，与已选项 sim 0.00 | `0.7×0.95 − 0.3×0` | 0.665 |

0.03 的相关性差距被 0.90 的相似度完全吞掉。但结合 E1：真实不同块之间的 Jaccard 通常 ≤ 0.35，惩罚项最大只有 `0.3×0.35 ≈ 0.105`，与 RRF 归一化后的相邻名次差（≈0.015）同量级——**惩罚项在真实语料上是「够用但不越权」的**。

结论：不必重写 MMR 公式，但**必须加相关性带**（R5），否则一旦某两条内容真的高度相似（复制粘贴的章节、重复的产品说明），MMR 会为了多样性丢掉次优但相关的块。

邻块下限取 0.25 时惩罚项上限为 `0.3×0.25 = 0.075`（探针输出）；若取 0.35 则为 0.105，会把邻块的压制力度拉到「共享一半内容」的档位，过强。

### 2.3 E3：单路天花板不是缺陷，是刻意设计

固定分母 `(w_v + w_k)/(k+1)` 下：

| 命中情况 | 归一化分 | 含义 |
| --- | --- | --- |
| 两路都 rank 1 | 1.000 | 双路共识满分 |
| 仅向量路 rank 1 | 0.700 | 缺关键词那一路的 0.3 |
| 仅关键词路 rank 1 | 0.300 | 缺向量那一路的 0.7 |

WeKnora `03 §5.5` 对此有明确解释：关键词路的作用是「**补向量没召回的**」；
一旦某个 chunk 也被向量召回，关键词那一路就只贡献加分、不再决定名次；
并给出算术结论——向量路要掉到第 82 名（`61×0.7/0.3 − 60`）才低于 0.3。

因此**不改这个代数**，改为对齐 WeKnora `06 §2.2` 的三条互斥归一化路径：
单路时根本不用 RRF，改用该路原生分（cosine / BM25 相对分），双路才走 RRF（§5.2）。
代价是三条分支的分数量纲不同，由 `mode_used` / `routes` 标注承担（不再试图统一量纲）。

### 2.4 E4：与 WeKnora 原理的逐条对照

对照表见 §1.1。三条实质差距与本轮的处置：

| 差距 | WeKnora | 本方案（对照前） | 处置 |
| --- | --- | --- | --- |
| 归一化 | `06 §2.2` 三条互斥路径 | 自创「有效权重归一化」 | **改为照抄三条路径**（§5.2） |
| 去重判据 | `03 §6` 三级 + 「重合 ≥0.85 且独有 token ≤2%」 | 单条件 Jaccard ≥0.6 | **改为三级 + 包含率口径**（§5.4，E1 证明纯 Jaccard 抓不到真包含） |
| 原理覆盖 | 9 篇（00–08） | 只读了 5 篇（漏 06/07/08） | 补读并逐条登记裁剪理由（§1.1） |

## 3. 检索管线（精确口径）

```
query
 │
 ├─ ① 作用域解析：visible_kb = {kb | kb.is_enabled=1 AND (kb.visibility='global' OR kb.workspace_id ∈ scopes)}
 │     └─ 显式 kb 参数：必须在 visible_kb 内，否则直接报错（不静默降级）
 ├─ ② 查询分词：tokenize_query(query) → FTS MATCH 表达式（与索引同一函数）
 ├─ ③ 召回（两路并发，互不阻塞）
 │     ├─ 关键词：FTS5 MATCH → bm25 排序 → top keyword_pool
 │     └─ 向量：按 embedding identity 分组 → 每组一次 query embedding → Chroma top vector_pool
 ├─ ④ 融合与归一化：两路都有 → 加权 RRF / maxRRF；单路 → 该路原生分（cosine / BM25 相对分）
 ├─ ⑤ 去重三级：content_hash 相同 → 部分包含（包含率 ≥0.98 或 高重合且独有 ≤2%）
 ├─ ⑥ 冗余抑制：同 doc 邻块抬相似度下限 → 相关性带过滤 → MMR 选择
 ├─ ⑦ 排序：按融合分降序（tie-break: chunk_id 升序）→ 截断到 limit
 └─ ⑧ 装配：句柄、面包屑、预算截断（仅工具渲染层）
```

> **两路执行顺序（AL-R10）**：v1 **顺序**执行，先向量后关键词（`nanobot/knowledge/retriever.py:17-18`）。
> 理由：向量路失败时语义模式可以直接退回关键词单路，省掉一次注定无用的 FTS 查询；
> v1 单用户单次检索，FTS 实测 0.6 ms，顺序执行的收益（可早退）大于并发的收益。

**四态语义（不可混淆）**

| 状态 | 判定 | `reason` / 异常 | 模型必须看到 |
| --- | --- | --- | --- |
| 失败 | 参与的路由全部抛错 | 抛 `KnowledgeSearchError` | 「检索失败，不代表没有答案」 |
| 空结果 | 路由成功但无候选 | `reason="no_candidates"` | 「没有检索到相关内容」 |
| 被过滤 | 有候选但被作用域/状态过滤掉 | `reason="filtered"` | 「有候选但不可见或已停用」 |
| 无查询词 | 分词后 token 为空 | `reason="empty_query_tokens"` | 「该查询没有可检索的词，请改写」 |

「失败」必须是异常而不是空结果（父方案 §8 的不变式，本文沿用并强化测试覆盖）。

## 4. 召回

### 4.1 关键词路

- MATCH 表达式由 `tokenize_query()` 生成：token 白名单（字母/数字/CJK）、逐个加引号、显式 `OR`、上限 64 token（父方案 §8 / 存储方案 §7.2）。
- **绝不发送空 MATCH**：token 为空时跳过本路并记 `empty_query_tokens`。
- SQL 用 `CROSS JOIN` 强制 FTS 驱动（存储方案 §7.3，实测 0.6 ms vs 1694–1806 ms）。
- 结果携带 `-bm25()` 原始分（`keyword_score`），**只用于展示与诊断**，不参与融合（R11）。
  > **实现现状（AL-R7）**：`SearchHit` 没有承载原始分，`_assemble` 直接用融合分覆盖（`retriever.py:396-401`）。
  > 修订口径：原始分**只在 repository 层可见**（`FtsHit.score`），不进 `SearchResult`、不分发给工具层。
  > 诊断需求由 `stats` 与 golden queries 报告承担，不在单次检索结果里携带第二个分数。
- 过滤条件在 FTS 召回之后生效：`c.is_enabled=1 AND d.is_enabled=1 AND d.parse_status='ready' AND kb.is_enabled=1`。

### 4.2 向量路

- 查询必须带 `where={"kb_id": {"$in": visible_kb_ids}}`（存储方案 §8.3），`kb_ids` 来自同一份 SQL 解析结果。
- 命中 id 必须回表 `document_chunks`，过滤已停用/已删除的孤儿向量后才进入融合。
  > **实现现状（AL-R1，高优先级）**：回表只查 `chunk.is_enabled`，**未 join `documents.parse_status='ready' AND documents.is_enabled=1'`**
  > （`retriever.py:362-364`），而 FTS 路在 SQL 里做了 join（`repository.py:173-181`）。
  > 后果：失败文档（`parse_status='failed'`）残留的旧块会**只从向量路漏出**——FTS 路查不到、向量路能查到，
  > 回表拿到的还是旧正文，与文档当前状态不一致。这违反父方案 §3「读路径复核启用状态」不变量与存储 spec §13 第 3 条。
  > **必须改代码**：向量回表与 FTS 路使用同一套可见性过滤（块启用 + 文档 ready + 文档启用）。
- 余弦距离 → 分数 `max(0, 1 − distance)`，仅用于展示与诊断。
- **embedding identity 分组**：按 KB 的 `embedding_backend` 先分流，再比对三元组：
  1. `embedding_backend == 'none'` 的 KB **本来就没有向量**（父方案 §10 的 `none` 后端语义），不参与向量路，
     记 `vector_disabled:<kb_id>`（R13），**不得**记成 `embedding_mismatch`；
  2. 其余 KB 的 `(backend, model, dimensions)` 与当前 embedder 一致的进向量路；
  3. 不一致的 KB 记 `embedding_mismatch:<kb_id>` 并排除（需 reindex）。
- **部分一致时按 KB 子集查询**：向量路的 `where={"kb_id": {"$in": ...}}` 只传一致的 KB 子集，其余 KB 仍走关键词路。
  （v1 只有一个活动 embedder，因此「部分一致」只发生在上表的 1/3 两类混合时。）
  **这条修正了原口径**：先前草稿在「部分一致」时整体跳过向量路，会让本可向量检索的库白白降级。

### 4.3 候选池

| 池 | 公式 | 硬上限 | 理由 |
| --- | --- | --- | --- |
| 关键词 | `min(max(limit × 10, keyword_pool_size), 300)` | 300 | FTS 单次 0.6 ms（存储方案 E15），扩大关键词池几乎免费 |
| 向量 | `min(max(limit × 5, pool_size), 200)` | 200 | 含 query embedding 计算与 Chroma 查询，成本高 |

- `keyword_pool_size` 默认 **100**，`pool_size` 默认 **50**（父方案 §10 配置字段）。
- 默认 `limit=8` 时：关键词池 = `min(max(80,100),300)` = 100；向量池 = `min(max(40,50),200)` = 50。
- **候选池是 RRF 的输入边界**：池外的文档名次未知，不参与融合。关键词池取 10× 是为了不让「只在关键词路排中后段」的精确命中（错误码、型号串）被池截断。
- 池大小随 `limit` 变化，但**不随 mode 变化**：`mode=keyword` 仍用同一关键词池公式。

## 5. 融合与排序（本文重点）

### 5.1 方案对比：为什么是 RRF

| 方案 | 做法 | 优点 | 缺点 | 结论 |
| --- | --- | --- | --- | --- |
| **A 加权 RRF（仅双路时）** | 只用名次：`Σ w_r/(k+rank_r)` | 免标定；跨路由可比；天然抗单路分数漂移 | 丢弃分数幅度；两路名次差 1 的差异被压到 ≈0.016 | **采用**（WeKnora `03 §5.3` / `06 §2.3` 同款） |
| A' 单路原生分 | 只有一路时用该路原生分（cosine / BM25 相对分） | 保住 cosine 的绝对信息；与 WeKnora 一致 | 三条分支量纲不同，跨模式不可比 | **采用**（WeKnora `06 §2.2`，§5.2） |
| B 归一化分数融合 | bm25 与余弦相似度各自归一化后加权求和 | 保留分数幅度 | bm25 无上界且随语料漂移；余弦分布集中；min-max 对单条异常值敏感；跨查询不可比 | 否决 |
| C 学习型融合（LambdaMART/LTR） | 用标注数据训一个排序器 | 质量上限最高 | 需要标注集 + 训练管线；v1 无 | 否决（v2+） |
| D 两阶段（RRF 取 top-N → 精排） | RRF 召回后 cross-encoder 重排 | top-1 精度最好 | 需要 rerank 服务；v1 无 | 否决（留 seam） |

RRF 的固有代价必须写进工具契约：**融合分只反映名次，不反映绝对相关性**。因此工具输出里 `score` 不允许被当作置信度（R8）。

### 5.2 归一化：三条互斥路径（对齐 WeKnora `06 §2.2`）

**不是「先都归一化再加权」，而是按当前有哪些路分三个分支**，每个分支各自保证输出落在 `[0,1]`：

| 分支 | 触发条件 | 分数口径 | 依据 |
| --- | --- | --- | --- |
| A 只有向量 | 关键词路无结果 | **保留原始 cosine**（`1 − distance`），按 chunk_id 去重后按分排序 | cosine 本来就在 [0,1]，再缩放会破坏绝对相似度语义 |
| B 只有关键词 | 向量路无结果 | **每张列表除以自身最大值**（仅当 max > 1），再去重排序 | BM25 无上界，不同引擎/索引不可比，各自的 max 才是合理参照 |
| C 两路都有 | 常规情况 | **加权 RRF / maxRRF** | RRF 原始值只有约 0.016，会被任何单路分数压过 |

分支 C 的代数（`k=60`，`w_v=0.7`，`w_k=0.3`）：

```
RRF(c)   = w_v/(k+rank_v) + w_k/(k+rank_k)      # 只在该路命中时才加该项
maxRRF   = (w_v + w_k) / (k + 1) = 1/61
fused(c) = RRF(c) / maxRRF                       ∈ [0,1]
```

| 命中情况 | fused | WeKnora `03 §5.5` 算例 |
| --- | --- | --- |
| 两路都 rank 1 | 1.0000 | c_7a3 = 0.9905（向量 1 + 关键词 3） |
| 向量 rank 1 + 关键词 rank 2 | 0.9952 | — |
| 两路都 rank 2 | 0.9839 | — |
| 仅向量 rank 1 | 0.7000 | c_2b1 = 0.6887 |
| 仅关键词 rank 1 | 0.3000 | c_55e = 0.3000 |
| 仅向量 rank 20 | 0.5338 | — |

**单路天花板是刻意设计，不是缺陷。** WeKnora `03 §5.5` 明确指出：关键词路的作用是「补向量没召回的」，
一旦某个 chunk 也被向量召回，关键词那一路就只贡献加分、不再决定名次；并给出算术结论——
向量路要掉到第 82 名才低于 0.3。
**因此不采用「按参与路由重新归一化」的做法**（那会把单路 top1 拉到 1.0，抹掉「双路共识才有高分」的语义，
且与 WeKnora 行为不一致）。

**代价与补偿**：三条分支的分数**量纲不同**（cosine 绝对分 / BM25 相对分 / RRF 归一化分），跨模式不可直接比较。补偿手段：

1. 工具输出必须带 `mode_used` 与 `routes`（§7.3），说明分数是在哪种模式下产生的；
2. `score` 文案统一表述为「**当前模式下的相对排序分**」，禁止解释为置信度或绝对相关度；
3. 跨模式的分数比较（如「降级前后分数是否变高」）**不在产品语义内**，不做任何承诺。

**权重取值口径**

- 默认 `w_v=0.7 / w_k=0.3`：语义为主、精确串为辅（父方案 §2 已定）。WeKnora 明确权重「不是在给分数调幅，
  而是改变两路各占多少话语权」。
- `mode=keyword` → 分支 B；`mode=semantic` → 分支 A。
- `mode=semantic` 但向量不可用 → 退回分支 B，并记 `degraded=["vector_unavailable","mode_downgraded"]`（父方案 §8）。
- v1 不提供 per-query 权重覆盖（避免模型随意调参导致不可复现）。

**RRF 的 k**

`k=60` 与 WeKnora、记忆域口径一致。`k` 越大，名次 1 与名次 10 的差距越小（更平）；`k` 越小越强调头部。v1 固定 60。

### 5.3 排序的确定性

所有排序必须写全序：

1. 主键：`fused` 降序（浮点比较用 `round(fused, 9)` 后比较，避免尾数抖动）；
2. 次键：`chunk_id` 升序。

- 融合前的 `score(c)` 用 `dict` 累加，遍历顺序不影响结果（求和顺序固定：先 vector 后 keyword）。
- 去重与 MMR 都必须保持输入顺序稳定（Python `list` 保序 + 稳定排序即可）。
- **同一 query + 同一库状态 → 同一结果序列**，这是回归测试与缓存的前提（R10）。

### 5.4 去重：三级（对齐 WeKnora `03 §6` / `06 §2.4`）

WeKnora 的去重是**三级，各抓一种重复**；我们沿用同样的分级与判据结构，但把阈值按本仓分词粒度重标定。

| 级 | 判据 | 删除谁 | 抓什么 | 本方案实现 |
| --- | --- | --- | --- | --- |
| ① ID | `chunk_id` 相同 | 后出现的 | 多路召回同一 chunk | **融合内按 id 合并**（RRF 求和天然合并，保留两路名次），不算删除 |
| ② 内容签名 | 归一化后内容指纹相同 | 后出现的 | 跨 KB 的重复文档、复制粘贴 | `content_hash` 相同 → 保留分高者 |
| ③ 部分包含 | 长度比 ≤5 **且**（短文本 token 集合 ⊆ 长文本 token 集合 **或**（token 重合率 ≥ T **且** 短文本独有 token 占比 ≤2%）） | 分数低的 | 父块 vs 子块、网页正文 vs 摘要 | 见下 |

**级别 ③ 的阈值必须按分词粒度重标定。** WeKnora 的「token 重合 ≥ 0.85」建立在词级分词（Lindera）上；
本仓用 `unigram+bigram`，token 更细，同等文本重合算出来的数值偏低，**照抄 0.85 会完全失效**。
因此把判据拆成**对粒度不敏感的包含率**与**粒度相关的重合率**两部分：

```
length_ratio = max(len)/min(len)                       # token 集合大小
containment  = |A∩B| / |A|        # A = 短文本的 token 集合
unique_ratio = 1 - containment     # 短文本独有 token 占比
判为重复 ⟺ length_ratio ≤ 5 且 (
      containment ≥ 0.98            # 短文本几乎完全被包含（独有 ≤2%）
   或 (Jaccard ≥ 0.6 且 unique_ratio ≤ 0.02)   # 高重合且短文本几乎无独有内容
)
```

**实测标定（E1，探针输出）**

| 关系 | Jaccard | containment | 是否判重复 | 期望 |
| --- | --- | --- | --- | --- |
| 相邻块（共享 120 字 overlap） | 0.091 | 0.167 | 否 | ✅ 邻块冗余交给邻块规则（§5.5），不由去重处理 |
| 共享一半内容 | 0.336 | 0.504 | 否 | ✅ 两块各有独有结论，不该删 |
| 无关块 | 0.009 | 0.018 | 否 | ✅ |
| **真包含（摘要 vs 正文）** | **0.252** | **1.000** | **是** | ✅ 纯 Jaccard 阈值（0.6 / 0.85）都抓不到，只有包含率能抓 |

**长度比必须同步放宽到 5。** 集合包含时 `J = 1/length_ratio`，E1 的真包含样例 J≈0.252
对应长度比≈4；若沿用 3.0，包含率分支会被长度比永久挡住，与本节结论自相矛盾。

**这条实测推翻了「把 Jaccard 阈值从 0.85 降到 0.6」的改法**：真包含的 Jaccard 只有 0.252，
任何纯 Jaccard 阈值要么抓不到真包含、要么误杀共享一半的两块；只有包含率口径同时满足两者。
WeKnora `06 §2.4` 的原话也印证这一点——「第 ③ 级的两个约束，缺一个就会误删」。

**保留方向**：分数差 > 5% 取高分；否则取更长块（更长块信息更全）。纯分数优先会在「短块分高、长块分低但内容更全」时丢信息。

**口径澄清（实现对齐 AL-R4 / AL-R5）**

- **长度比按 token 集合大小计算**，不是字符数：`|longer| / |shorter|`，`longer`/`shorter` 指 token 集合基数。
  上限取 **5.0**（不是 WeKnora 的 3）——`unigram+bigram` 使 token 数约为字符数的 2 倍，
  同样的「长短块比例」在 token 口径下数值更大，放宽到 5.0 才等价于 WeKnora 的 3。
- **5% 同分容差的基数是「高分者」**：`higher.score − lower.score > 0.05 × higher.score` 时取高分，
  否则取更长块，再同则取 `chunk_id` 较小者。全 0 分时容差退化为 0，直接走长度与 id 比较（确定性不受影响）。

**位置约束（WeKnora `03 §6` 的硬约束，必须遵守）**

1. **去重必须在归一化之后**：级别 ③ 比的是分数，跨 KB 比较分数要求量纲已统一；
2. **融合前不得跨列表按 id 合并**：那会让 RRF 少加一份权重，双路共识机制直接失效——
   融合内部按 id 归并（保留两路名次、每个 chunk 产出一条）是**合并**而非**去重**；
3. **不得把「同一个 `ParentChunkID`」当重复**：v1 无父块，但同文档邻块也不能按父块逻辑删（见 §5.5）。

### 5.5 同文档邻块冗余抑制（新增）

E1 表明：入库时的 `chunk_overlap=120` 会让相邻两块共享 15% 正文，但 Jaccard 只有 0.09，**去重抓不到，MMR 也不会压制**——最终 top-8 可能返回两段几乎重复的正文。

规则：

1. **邻块判定**：同 `doc_id` 且 `|chunk_index_a − chunk_index_b| ≤ 1`。
2. **相似度下限**：计算 MMR 相似度时，若两块互为邻块，取 `sim = max(sim, adjacent_similarity_floor)`，默认 `adjacent_similarity_floor = 0.25`。
   - 取值依据（E1）：邻块实测 0.091，「共享一半内容」0.336，无关块 0.009。0.25 落在 (0.091, 0.336) 区间内——
     高于邻块实测值才检得出，又不把「共享一半」压到同一档；对应 MMR 惩罚上限 `0.3×0.25 = 0.075`。
3. **不做硬删除**：邻块可能正是模型需要的连续上下文（跨块的步骤说明）。压制而不是删除，由模型用 `knowledge_read` 扩读补齐。
4. **不作用于 L1/L2 去重**：邻块不进硬去重，只在 MMR 的相似度里抬下限。
5. **可选的入库侧缓解（v2）**：embedding 只取块的主体（去掉 overlap 部分的尾部），向量天然不重复。v1 不做——会破坏「块内容 = 切片原文」的不变量。

### 5.6 MMR：参数、量纲与相关性带（修正）

```
mmr(c) = λ · rel(c) − (1 − λ) · max_sim(c, selected)
rel(c) = fused(c)
λ = 0.7（默认）
```

- **相似度**：`Jaccard(shingles(content))`，`shingles = set(tokenize_index(text).split())`；空集记 0。
  与去重同一口径，但叠加 §5.5 的邻块下限。
- **相关性带（R5）**：进入 MMR 的候选必须满足 `fused(c) ≥ fused_max − relevance_band`，`relevance_band = 0.15`（默认）。
  - 作用：MMR 只能在「相关性相近」的候选之间做多样性取舍，不会为了避开重复而跨档选入明显更不相关的块（E2）。
  - 与 `fused` 的压缩分布相容：top-10 的 `fused` 多落在 0.87–1.00，band 0.15 大致覆盖 top-10~top-30。
  - **尺度前提（AL-R3）**：0.15 是按**分支 C（RRF 归一化分）**的压缩分布标定的。
    分支 A（cosine）与分支 B（BM25 相对分）的分数尺度不同，直接套用同一 band 没有标定依据。
    处置：band **只在分支 C 生效**；分支 A/B 不做相关性带过滤（MMR 的多样性项在单路下本就弱）。
    若后续要在单路分支加带，需按各分支的分数分布单独标定。
- **λ 的口径**：0.7 表示「相关性主导、多样性纠偏」。若 golden queries 显示同主题重复块仍挤占结果，λ 降到 0.6；若显示多样性压过相关性，λ 提到 0.8。**v1 先跑 golden queries 再定稿**（R12）。
- **候选集**：MMR 的候选 = 融合 + 去重后的全部条目（上限 = 关键词池 + 向量池去重后的规模，≤ 500）。MMR 是 O(n²) 词面 Jaccard，500 条 × 20 次选择 ≈ 5000 次比较，每次比较两个 token 集合——v1 用「缓存每个候选的 token 集合」避免重复分词。

### 5.7 最终顺序与展示分（修正）

- **MMR 只决定「选哪些」**，不决定展示顺序。
- 展示顺序 = `fused` 降序（tie-break `chunk_id` 升序），因此工具输出里 `score` 序列**单调不增**（R6）。
- 展示分 = `fused`（∈ [0,1]，保留 3 位小数），**不代表置信度**，输出中标注 `rerank="none"`（R8）。
- 被 MMR 落选的块不进入结果；`limit` 之外的高分块不进入结果。工具输出用 `<omitted count="N" reason="budget"/>` 标注省略数量。

### 5.8 参数表

| 参数 | 默认 | 位置 | 调参影响 |
| --- | --- | --- | --- |
| `vector_weight` | 0.7 | `knowledge.retrieval` | ↑ 更偏语义；↓ 更偏精确串（只影响双路分支） |
| `keyword_weight` | 0.3 | 同上 | 与上者互补，二者不必和为 1 |
| `rrf_k` | 60 | 同上 | ↑ 更平（名次差被压小）；↓ 更强调头部 |
| `pool_size` | 50 | 同上 | 向量路召回**下限**（实际池 = `min(max(limit×5, pool_size), 200)`）；↑ 提升召回上限，成本线性上升 |
| `keyword_pool_size` | 100 | 同上 | 关键词路召回**下限**（实际池 = `min(max(limit×10, keyword_pool_size), 300)`） |
| `mmr_lambda` | 0.7 | 同上 | ↑ 更重相关性；↓ 更重多样性 |
| `relevance_band` | 0.15 | 同上 | ↑ 放宽 MMR 的候选范围 |
| `adjacent_similarity_floor` | 0.25 | 同上 | ↑ 邻块压制更强 |
| `dedup_containment` | 0.98 | 同上 | 短文本包含率下限（WeKnora「独有 token ≤2%」的等价口径，§5.4） |
| `dedup_unique_ratio` | 0.02 | 同上 | 短文本独有 token 占比上限 |
| `dedup_jaccard` | 0.6 | 同上 | 高重合分支的 Jaccard 下限（仅在 `unique_ratio` 同时满足时生效） |
| `dedup_length_ratio` | 5.0 | 同上 | ↑ 更激进去重；5.0 覆盖 E1 真包含样例（长度比≈4） |

配置节沿用父方案 §10 的 `KnowledgeRetrievalConfig`，本节字段为新增；池的硬上限 300 / 200 为常量，不进配置。

## 6. 降级与失败语义

| 情况 | 行为 | `mode_used` | `degraded` | `reason` |
| --- | --- | --- | --- | --- |
| 向量后端不可用（未装 / 无 key） | 只走关键词 | `keyword` | `["vector_unavailable"]` | — |
| `mode=semantic` 但向量不可用 | 降级关键词 | `keyword` | `["vector_unavailable","mode_downgraded"]` | — |
| 查询 token 为空 | 跳关键词路 | `keyword` | `["empty_query_tokens"]` | `empty_query_tokens`（无其他路时） |
| 远程 embedding 调用失败 | 关键词单路 | `keyword` | `["embedding_error:<Type>"]` | — |
| KB 的 `embedding_backend='none'` | 该库不走向量路（本就无向量） | 不影响其他库 | `["vector_disabled:<kb_id>"]` | — |
| 部分 KB embedding 身份不一致 | **只对一致的 KB 子集走向量路**，其余 KB 退出 | `hybrid` | `["embedding_mismatch:<kb_id>"]` | — |
| 全部 KB 身份不一致 | 无向量路 | `keyword` | `["embedding_mismatch:<kb_id>"]` | — |
| 关键词路抛错、向量路正常 | 向量结果 + 标注 | `semantic`/`hybrid` | `["keyword_error"]` | — |
| 向量路抛错、关键词路正常 | 关键词结果 + 标注 | `keyword` | `["vector_unavailable"]` | — |
| **参与的全部路由都抛错** | **抛异常**，不返回空结果 | — | — | — |
| 两路都无候选 | 空结果 | `keyword` | — | `no_candidates` |
| 有候选但被作用域/状态过滤 | 空结果 | `keyword` | — | `filtered` |
| 无可见 KB | 空结果 | `keyword` | — | `no_scope` |

规则：

- **`mode_used` = 实际跑通的路，不是「哪路有结果」（AL-R2）**：请求 `hybrid` 且两路都执行成功 → `hybrid`（即使关键词路 0 命中）；
  只有向量路可执行 → `semantic`；退回关键词 → `keyword`。
  `routes` 才是「哪路产出了候选」。两者分开，模型才能区分「这条路没命中」与「这条路没跑」。
- **降级永远伴随可见标注**：`degraded` 非空时，工具文案必须说出「哪一路不可用、降级到了什么」。
- **失败 ≠ 空结果**：本次**参与的路由全部失败**即抛 `KnowledgeSearchError`——包括 `mode=keyword` 时关键词路抛错
  （此时没有第二条路可退，绝不能返回空结果冒充「没搜到」）。
- **分数量纲随分支变化**：三条归一化路径的分数不可跨模式比较；由 `mode_used` 与 `routes` 标注承担，
  不对「降级前后分数是否变高」做任何承诺（§5.2）。
- **`vector_disabled` 与 `embedding_mismatch` 必须区分**：前者是「该库配置为关键词模式」，后者是「该库索引过期、需 reindex」，
  两者给模型的提示完全不同（R13）。

## 7. 装配与工具输出契约

### 7.1 装配

- 每条结果携带句柄：`chunk://<chunk_id>`；文档级 `doc://<doc_id>` 由 `knowledge_read` 使用。
- **正文渲染**：`title` + `heading_path`（面包屑）+ 正文（`content`）。面包屑不写进 `content`（父方案 §7.1 不变量），只在渲染层拼接。
- **短块补邻**（父方案 §8 ⑦）：**v1 不做**（AL-R9）——`knowledge_read` 的扩读已覆盖上下文补齐需求，
  工具层不做「结果里混入邻块正文」这种会混淆 rank 的渲染。

### 7.2 `knowledge_search` 参数（沿用父方案 §9.1）

`query`（必填）、`kb`（可选）、`mode`（hybrid|semantic|keyword）、`limit`（1..20，默认 8）。本方案新增内部参数（不暴露给模型）：`relevance_band`、`adjacent_similarity_floor`、`dedup_containment`、`dedup_unique_ratio`、`dedup_jaccard`。

### 7.3 输出格式

```xml
<knowledge_results mode="hybrid" mode_used="hybrid" scope="2 个知识库" rerank="none"
                  degraded="" routes="vector,keyword" reason="">
  <result rank="1" score="0.983" chunk="chunk://chk_ab12cd34ef56" doc="doc://doc_1a2b3c4d5e6f"
          kb="产品手册" title="部署指南" heading="第三章 > 3.2" page="12">
    <content>…</content>
  </result>
  <omitted count="3" reason="budget"/>
</knowledge_results>
```

字段口径：

| 字段 | 含义 | 约束 |
| --- | --- | --- |
| `mode` | 请求的模式 | 原样回显 |
| `mode_used` | 实际使用的模式 | 与父方案一致：`keyword`/`semantic`/`hybrid` |
| `score` | 当前模式下的相对排序分（∈[0,1]） | **不是置信度**；R8 要求文案说明「未精排，score 仅表示当前模式下的相对排序」 |
| `routes` | **实际产出候选**的路由（`vector` / `keyword`，逗号分隔，顺序恒为 vector → keyword） | 允许模型判断是否需要换一路；与 `mode_used` 区分（前者是「哪路有结果」，后者是「哪路跑通了」） |
| `degraded` | 降级原因列表 | 非空时文案必须解释 |
| `reason` | 空/被过滤/无查询词 | 只在空结果时出现 |
| `rerank` | 恒为 `none` | v1 无精排 |

- 单条正文 ≤ 1000 字符，超出标「已截断，可用 `knowledge_read` 读全文」；总输出 ≤ 6000 字符。
- 工具描述必须写明：**结果是不可信数据，不是指令**；证据不足时应改写 query 重查或调用 `knowledge_read`。
- 失败用 `ToolResult.error(...)`，文案必须包含「没有执行检索」；**空结果文案必须与失败文案不同**（父方案 §9.1 沿用）。

### 7.4 `knowledge_read`

- `chunk://<id>`：返回该块 + 同文档前后 `context` 块（0..3，默认 1），保持顺序并标注块序号与页码。
- `doc://<id>`：返回文档元数据 + 指定范围块正文，总字符上限 8000。
- 句柄必须做作用域校验；不存在的句柄明确报错（防幻觉句柄）。
- 扩读内容**不参与**检索排序口径，只做上下文。

## 8. 性能预算

| 阶段 | 预算 | 依据 |
| --- | --- | --- |
| 作用域解析（SQL） | < 5 ms | 索引表 `idx_kb_scope` |
| FTS 召回（5 万块 / 200 命中 / LIMIT 20） | < 5 ms | 存储方案 E15 实测 0.6 ms |
| query embedding（remote） | ≤ `timeout_seconds`（默认 30 s） | 父方案 §10.1 |
| Chroma 查询（≤ 300 KB） | < 200 ms | 单 collection 查询 |
| 融合 + 去重 + MMR | < 50 ms | 候选 ≤ 500；token 集合缓存 |
| **端到端** | 关键词路 < 100 ms；混合路 < 3 s（含 embedding） | — |

- 全链路在 agent 工具中以 `asyncio.to_thread` 执行，不阻塞事件循环（父方案 §11）。
- 检索路径**不写库**：不记录 query、不记访问计数（父方案 §8）。

## 9. 测试矩阵

### 9.1 融合与排序（重点，`tests/knowledge/test_fusion.py`）

| 用例 | 断言 |
| --- | --- |
| 两路 rank1 同一块 | `fused == 1.0` |
| 仅向量 rank1（两路参与） | `fused == 0.7` |
| 仅关键词 rank1（两路参与） | `fused == 0.3` |
| 向量 rank1 + 关键词 rank2 | `fused == 0.7 + 0.3×61/62` |
| **只有向量路**（分支 A） | 分数 = 原始 cosine，不参与 RRF；不做 maxRRF 缩放 |
| **只有关键词路**（分支 B） | 分数 = bm25 / 自身最大值；max ≤ 1 时不除 |
| 权重同比例缩放（如 0.7/0.3 → 1.4/0.6） | 融合分与排序完全不变（maxRRF 与分子同比例缩放） |
| 权重比例改变（如 0.7/0.3 → 0.6/0.4） | 仅「只被一路命中」的项之间相对位置变化；双路命中项仍为 1.0 |
| `k` 改变 | rank1 恒为 1.0，rank2 随 k 单调下降 |
| 空输入 | 返回 `{}` |
| 排序确定性 | 相同输入两次调用结果序列完全一致 |
| 同分 tie-break | `chunk_id` 升序 |

### 9.2 去重（`tests/knowledge/test_fusion.py`）

| 用例 | 断言 |
| --- | --- |
| `content_hash` 相同（级别 ②） | 只保留一条，保留高分者 |
| **真包含**（短文本 token 集合 ⊆ 长文本；Jaccard 仅 0.25） | 判为重复（E1：纯 Jaccard 阈值抓不到，必须靠包含率） |
| 包含率 0.98 且长度比 ≤ 5 | 判为重复 |
| 包含率 0.98 但长度比 > 5 | 不判为重复 |
| Jaccard ≥ 0.6 且短文本独有 token ≤2% | 判为重复 |
| Jaccard ≥ 0.6 但独有 token 占比高（共享一半内容） | **不判为重复**（WeKnora `06 §2.4`：缺一个约束就会误删） |
| 分数差 < 5% | 保留更长块 |
| **重叠邻块（J=0.091、包含率 0.167）** | **不被去重删除**（E1 边界，防回归） |

### 9.3 邻块冗余抑制与 MMR（`tests/knowledge/test_fusion.py`）

| 用例 | 断言 |
| --- | --- |
| 同 doc 相邻块 | MMR 相似度被抬到 ≥ 0.25，二者不同时进 top-2 |
| 同 doc 相隔 ≥ 2 的块 | 不抬下限 |
| 跨 doc 块 | 不抬下限 |
| 「共享一半内容」的两块（不同 doc） | 相似度保持实测值（约 0.34），不被邻块规则影响 |
| 相关性带 | `fused` 低于 `fused_max − 0.15` 的候选不进 MMR |
| λ=0.7 时高分近重复块被压制 | 次优块入选 |
| λ 调高 | 排序更贴 `fused` |
| 展示顺序 | 结果 `fused` 单调不增 |

### 9.4 召回与降级（`tests/knowledge/test_retriever.py`）

| 用例 | 断言 |
| --- | --- |
| 混合两路命中 | 结果集合 = 两路并集；`routes` 正确 |
| 关键词池 300 / 向量池 200 | 传给下层的 limit 正确 |
| 向量不可用 | `mode_used="keyword"`，`degraded` 含 `vector_unavailable` |
| `mode=semantic` 降级 | `degraded` 含 `mode_downgraded` |
| **部分 KB 身份不一致** | 不一致的 KB 排除，其余 KB 仍走向量路；`degraded` 含 `embedding_mismatch:<kb>` |
| **`embedding_backend='none'` 的 KB** | 不走向量路；`degraded` 含 `vector_disabled:<kb>`，**不含** `embedding_mismatch` |
| `mode=keyword` 且关键词路抛错 | 抛 `KnowledgeSearchError`（没有第二条路可退） |
| token 为空 | 不发空 MATCH；`reason="empty_query_tokens"` |
| 两路都失败 | 抛 `KnowledgeSearchError`（不是空结果） |
| 越权 kb | 报错，不静默降级 |
| 孤儿向量（块已停用） | 被回表过滤，不进结果 |

### 9.5 工具契约（`tests/tools/test_knowledge_tools.py`）

| 用例 | 断言 |
| --- | --- |
| 输出 `score` 单调不增 | 成立 |
| `score` 标注非置信度 | 文案含「未精排 / 相对排序」 |
| `degraded` 非空 | 文案解释降级 |
| 空结果 vs 失败 | 文案不同 |
| 幻觉句柄 | 明确报错 |

## 10. 质量评估：golden queries

融合与排序参数可调，必须有可回归的度量。

**评估集**：`tests/knowledge/golden_queries.json`

```json
{
  "cases": [
    {
      "query": "部署失败怎么回滚",
      "kb": "产品手册",
      "expect_chunk_contains": ["回滚"],
      "expect_min_relevant": 1
    },
    {
      "query": "E-1024 错误码含义",
      "kb": "产品手册",
      "expect_chunk_contains": ["E-1024"],
      "note": "精确串用例，考察关键词路"
    }
  ]
}
```

**指标**

| 指标 | 定义 | v1 门槛 |
| --- | --- | --- |
| Recall@5 | 前 5 条中命中的期望块数 / 期望块总数 | ≥ 0.9 |
| MRR@10 | 首个期望块的倒数名次平均 | ≥ 0.7 |
| exact-hit | 期望串在前 10 条中出现 | ≥ 0.95 |
| fail-to-empty | 失败被当成空结果的次数 | = 0 |

用例字段口径：`expect_chunk_contains` 是期望命中的字符串（命中即算该用例通过）；
`expect_min_relevant` 是宽松判据——前 `limit` 条中至少要有 N 条包含任一期望串（用于「多块都算对」的用例，
与严格判据 `expect_chunk_contains` 配合）。

**执行**：`tests/knowledge/test_golden_queries.py`（实施计划 Task 21 已规划该文件），跑在真实本地嵌入或 mock embedder 上；mock 模式只校验「排序管线不回归」，不作为语义质量指标。

**调参闭环**：指标不达标 → 只允许调整 §5.8 的参数 → 重跑 → 记录到 `.ai-runtime-artifacts/verifications/`。参数改动必须连带更新本表门槛或说明为何门槛不变。

## 11. 对父方案与实施计划的修正清单

| # | 位置 | 现状 | 修正 |
| --- | --- | --- | --- |
| C1 | 父方案 §8「分数代数」 | 只有 RRF 一条路径，分母固定 `(w_v+w_k)/(k+1)` | 改为 WeKnora `06 §2.2` 三条互斥路径（§5.2） |
| C2 | 父方案 §8「去重与 MMR 口径」 | 阈值 0.85 单条件 | 改三级去重 + 包含率口径（§5.4） |
| C3 | 父方案 §8 | 无邻块规则 | 增加同 doc 邻块相似度下限 0.25（§5.5） |
| C4 | 父方案 §8「去重与 MMR 口径」 | MMR 无相关性带 | 增加 `relevance_band=0.15`（§5.6） |
| C5 | 父方案 §8 | 池 = `max(limit*5,30)`，统一 | 拆为不对称双池（公式 + 下限参数，§4.3） |
| C6 | 父方案 §9.1 输出 | 只有 `mode`/`rerank`/`degraded` | 增加 `mode_used`/`routes`/`reason`，`score` 标注相对排序分（§7.3） |
| C7 | 父方案 §8 语义路 | 身份不一致整体跳过向量路 | 部分一致时按 KB 子集查询（§4.2） |
| C8 | 计划 Task 13 `weighted_rrf` | 只有 RRF，无分支判断 | 拆三条互斥路径；单路断言删除（改为分支 A/B 断言） |
| C9 | 计划 Task 13 `dedup_partial` | 默认 `threshold=0.85` | 改包含率口径：`dedup_containment=0.98` / `dedup_unique_ratio=0.02` / `dedup_jaccard=0.6` |
| C10 | 计划 Task 13 `mmr_rerank` | 无 band / 无邻块下限 | 新增 `relevance_band`、`adjacent_similarity_floor`（默认 0.25） |
| C11 | 计划 Task 14 `Retriever.search` | 单池 `pool` | 拆 `keyword_pool` / `vector_pool`；`SearchResult` 增加 `routes` |
| C12 | 计划 Task 21 golden queries | 有文件，无指标门槛 | 补 §10 三指标与门槛 |
| C13 | 计划 Task 14 身份比对 | 任何不一致都记 `embedding_mismatch` | `embedding_backend='none'` 的 KB 记 `vector_disabled`，不算 mismatch（R13） |
| C14 | 父方案 §8 分词函数名 | 写 `cjk_tokens(query)` | 实际实现为 `tokenize_query(query)`（`nanobot/knowledge/tokenizer.py`） |
| C15 | 父方案 §10 配置 | `pool_size` 语义未定义 | 明确为向量池下限；新增 `keyword_pool_size`；其余新参数同 §5.8 |
| C16 | 方案 `source` 清单 | 只列 WeKnora 00/01/02/03/05 | 补 06/07/08，并在 §1.1 登记逐条对照与裁剪理由 |

> C8–C12 属实施计划变更，需在「写计划」阶段统一回写；本 spec 落盘后不直接改动计划文件（计划属 writing-plans 阶段产物）。

## 12. 风险与待确认

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| RRF 归一化后分数集中（top-10 多在 0.87–1.00） | 分数无法区分「很好」与「还行」 | 工具文案明确 score 是相对排序分；质量评估用 golden queries 而非分数阈值 |
| **三条归一化分支量纲不同**（cosine / BM25 相对分 / RRF 分） | 跨模式分数不可比 | 由 `mode_used`/`routes` 标注承担；不做跨模式分数承诺（§5.2） |
| 包含率判据在 `unigram+bigram` 下未用真实语料标定 | 阈值 0.98 可能偏严或偏松 | 探针已用高熵合成语料标定；真实语料标定留给 golden queries（§10） |
| MMR 参数（λ、band）缺乏客观标定 | 调参靠直觉 | 先跑 §10 评估集再定稿；参数全部可配置 |
| 邻块下限 0.25 落在实测区间内 | 过大压制正文的连续上下文 | 不硬删除，只压制；`knowledge_read` 可扩读补齐 |
| 无精排 | top-1 精度低于 WeKnora | `Reranker` seam 预留；工具引导模型改写/精读 |
| 向量库与 SQLite 短暂不同步 | 少数块暂时搜不到 | 读路径回表过滤 + `vector_status` 修复扫描（存储方案 §8.4） |

**待确认（实现前可再定）**

1. `relevance_band` 默认 0.15 是否偏宽（0.10 / 0.20 待评估集定）。
2. `adjacent_similarity_floor` 默认 0.25（实测区间 (0.091, 0.336) 内）是否合适。
3. 关键词池 300 是否需要按库规模动态调整（v1 固定）。
4. `dedup_containment=0.98` 与 `dedup_unique_ratio=0.02` 是否需要在真实语料上再标定（v1 先用探针值）。

## 13. 非目标与 v2 路线

- v2：cross-encoder / LLM 精排（接 `Reranker` seam）、查询改写、doc2query 第三路召回（父方案 §18.1）、每 KB 独立 embedding 模型。
- v2：embedding 只覆盖块主体（去 overlap），从源头降低邻块冗余（§5.5 第 5 条）。
- v2：学习型融合（C 方案）——需要标注数据。
- v2：`relevance_band` 与 λ 的自动调参。
- v2：**`bestRanks`**（WeKnora `03 §5.4`）——多 KB / 多引擎分组检索时，一个 chunk 出现在多张列表里，名次取「任意一张列表里的最佳名次」；v1 每路只有一张列表，不需要（§1.1）。
- v2：**统一分数量纲**——目前三条归一化分支的分数不可跨模式比较；若有产品需求要比较「降级前后分数」，需另设口径（§5.2）。

## 14. 实现对齐修订（2026-11-10）

> 本节登记**代码实现与本方案不一致**的地方。依据：`nanobot/knowledge/{fusion,retriever}.py`
> 与 `nanobot/agent/tools/knowledge_*.py` 的实读审计（2026-11-10，代码基线 commit `1532b9b8`）。
> 编号 `AL-R*` 供实施计划引用；处置列 `改代码` / `改方案`。

### 14.1 高优先级

| # | 位置 | 实现现状 | 修订口径 | 处置 |
| --- | --- | --- | --- | --- |
| AL-R1 | `retriever.py:362-364` | 向量回表只查 `chunk.is_enabled`，未过滤 `documents.parse_status='ready' / is_enabled=1` | 两路可见性过滤必须一致（§4.2、存储 spec §13 第 3 条）；失败文档的残留旧块不得从向量路漏出 | 改代码 |

### 14.2 中优先级

| # | 位置 | 实现现状 | 修订口径 | 处置 |
| --- | --- | --- | --- | --- |
| AL-R2 | `retriever.py:369-387` | `mode_used` 由「哪一路产出候选」决定：hybrid 请求但关键词路 0 命中时报 `semantic`，且**不记任何降级标记** | `mode_used` 反映**实际跑通的路**而非「哪路有候选」：请求 hybrid 且两路都执行成功 → `hybrid`；只有向量路可用 → `semantic`；退回关键词 → `keyword` + `degraded` 标注。`routes` 仍标注「实际产出候选的路」 | 改代码 |
| AL-R3 | `fusion.py:289-290` | 相关性带 `0.15`（按分支 C 标定）无条件套用到分支 A（cosine）/B（BM25 相对分） | band **只在分支 C 生效**；单路分支不做相关性带过滤（§5.6） | 改代码 |
| AL-R8 | `retriever.py:404-412` | `filtered` 判据只依赖向量路 `raw_count`；关键词路的「被状态过滤」发生在 SQL 内，外部不可见 | 明确近似口径：**只有向量路能区分 `filtered`**；关键词路的过滤只能表现为 `no_candidates`。若要精确区分，需在 FTS 查询里额外统计「命中但被过滤」的条数（v2） | 改方案（已改） |
| AL-R11 | `retriever.py:186-189,356` | 向量路用泛 `except Exception` 把编程错误也降级为 `vector_unavailable`；用 `assert` 做类型收窄（`-O` 下失效） | 只捕获 `EmbeddingUnavailable` / `EmbeddingError` / `VectorStoreError`；其余异常向上抛（属代码缺陷，不该伪装成降级）；`assert` 换成显式 `if ... is None: raise` | 改代码 |

### 14.3 低优先级（口径澄清）

| # | 位置 | 实现现状 | 修订口径 | 处置 |
| --- | --- | --- | --- | --- |
| AL-R4 | `fusion.py:208-215` | `dedup_length_ratio` 默认 **5.0**，且长度比按 token 集合大小算 | 采纳实现：长度比 = token 集合基数之比，上限 5.0（`unigram+bigram` 使 token 数约为字符数 2 倍，5.0 才等价 WeKnora 的 3） | 改方案（已改，§5.4） |
| AL-R5 | `fusion.py:162-170` | 5% 同分容差以「高分者」为基数 | 写回方案：容差基数 = 高分者（§5.4） | 改方案（已改） |
| AL-R6 | `fusion.py:66`、`knowledge_common.py:171` | `routes` 取值 `("vector","keyword")`，输出为 `vector,keyword` | 统一为可读形式 `vector,keyword`（不用 `k,v` 缩写），工具层直接拼接 | 改方案（已改，§7.3） |
| AL-R7 | `retriever.py:396-401` | `bm25` 原始分不进 `SearchHit` | 原始分只在 repository 层可见，不进 `SearchResult`（§4.1） | 改方案（已改） |
| AL-R9 | `repository.py:774` / 工具层 | 短块补邻（§7.1）有 `fetch_neighbors` 但工具层未调用 | v1 不做短块补邻；`knowledge_read` 的扩读已覆盖上下文补齐需求 | 改方案（已改，§7.1） |
| AL-R10 | `retriever.py:17-18` | 两路顺序执行（先向量后关键词） | 采纳实现并写回：顺序执行，理由是向量失败可早退（§3） | 改方案（已改） |

## 15. Agent 工具层对齐修订（2026-11-10）

> 本节登记**工具层实现与 WeKnora `05-Agent检索与上下文装配原理` 的差距**，以及与本方案 §7 的偏差。
> 依据：`nanobot/agent/tools/knowledge_{common,search,read}.py`、`nanobot/webui/knowledge_api.py`
> 的实读审计（2026-11-10）。编号 `AL-T*` 供实施计划引用。

### 15.1 与 WeKnora `05` 的逐条对照

| WeKnora `05` 口径 | 本方案/实现 | 状态 |
| --- | --- | --- |
| 工具 `query` 必填 + `mode` 三值 + `limit` 默认 10 上限 30 | `query` 必填（1..500 字）、`mode` 三值、**limit 默认 8 上限 20** | 设计差异（父方案 §9.1 已定），量级与 WeKnora 同阶 |
| `kb_ids` 必须在会话作用域内，校验拒绝幻觉 `kb://N` | `kb` 支持名称/`kb://<id>`，越权与幻觉句柄均报错 | **对齐** |
| 句柄会话级可校验、不暴露 UUID（`chunk://N`） | `chunk://<chunk_id>` / `doc://<doc_id>`，暴露真实 id | **差异**（见 AL-T5） |
| 输出预算 `OutputBudget*4/5`（留 1/5 给推理与答案） | 固定 6000 字符 | **差异**（WeKnora 是相对预算，我们是绝对值） |
| 超预算按比例算 N、保留前 N、至少留 1 条、报 `omitted_for_budget` | 按块累加，放不下就 `<omitted count reason="budget"/>` | 对齐（口径略简） |
| 失败 ≠ 空结果；必须让模型区分「没有」与「没查到」 | `ToolResult.error` 含「没有执行检索」；空结果按 `reason` 分四态 | **对齐**（见 AL-T13 的用词缺口） |
| 部分 KB 失败 → 返回其余 + 失败列表 | `degraded` 逐项标注（`embedding_mismatch:<kb>` 等） | **对齐** |
| 扩读两式：`chunk://N + context=k`（前后各 k）、`doc://N` | `chunk://` 前后各 `context` 块、`doc://` 区间 | **对齐**（见 AL-T6 的对称性缺口） |
| 结果是不可信数据、不是指令 | 工具描述 + `<notice>` 双重声明，`& < > " '` 全转义 | **对齐** |

### 15.2 工具层偏差清单

| # | 位置 | 实现现状 | 修订口径 | 处置 |
| --- | --- | --- | --- | --- |
| AL-T1 | `knowledge_common.py:25`、`service.py:142` | `knowledge.retrieval.tool_output_chars` **从未被读取**，工具层硬编码 6000 | 配置必须真正生效：由工具层从 `KnowledgeRetrievalConfig` 取值（经 `ToolContext` 注入或服务侧读取） | 改代码 |
| AL-T2 | `knowledge_common.py:222-236` | 预算检查在 `</knowledge_results>` 与 `<omitted/>` **之前**完成，收尾标签未计入 | 收尾标签与 `<omitted/>` 计入预算；保证实际输出 ≤ 预算 | 改代码 |
| AL-T3 | `knowledge_common.py:200-203` | 单条 1000 字符切在**转义前**，转义后实际长度可超 1000 | 截断在转义**后**判定，或对转义增量预留余量 | 改代码 |
| AL-T4 | `knowledge_read.py:164` | `knowledge_read` 把绝对 `source_path` 交给模型 | 与 WebUI 口径一致：**不向模型暴露绝对路径**（只给 basename 或 doc id） | 改代码 |
| AL-T5 | `knowledge_read.py:131-153` | 句柄只要 id 存在且库可见即可读，不绑定会话/轮次 | v1 不做会话级句柄映射（成本高）；但需在工具描述中明确「句柄来自上一轮检索结果」，并对**非本次检索来源**的句柄保持可读（WeKnora 也允许历史句柄）。登记为已知差异 | 改方案（已改） |
| AL-T6 | `knowledge_read.py:136-137` | 扩读起点 `max(0, idx-context)`：焦点在文首时返回「焦点 + 2 个后继」，前后不对称 | 与 WeKnora「前后各 k」对齐：后继数补足到 `context` 个 | 改代码 |
| AL-T7 | `knowledge_api.py:580-622` | WebUI 检索预览未复用工具层口径：无单条/总预算截断、无 `truncated` 标记、`degraded` 给原始码、句柄无 `chunk://` 前缀、`limit` 上限 50（工具 20） | WebUI 复用工具层同一渲染函数；`limit` 上限与 retriever 一致（20） | 改代码 |
| AL-T8 | `knowledge_api.py:390-447` | `KnowledgeSearchError` 继承 `RuntimeError`，无翻译分支 → 落到 500「知识服务暂不可用」 | 补分支：检索失败返回明确错误（区别于空结果），文案含「没有执行检索」 | 改代码 |
| AL-T9 | `knowledge_search.py:58` | `mode` 无 enum：schema 允许任意字符串、运行期才报错；WebUI 则直接 400 | schema 用 enum 收敛（`hybrid`/`semantic`/`keyword`），两端一致 | 改代码 |
| AL-T10 | `knowledge_search.py:163-174` | 每条 hit 单独 `get_document` 取标题（N+1，limit=20 时 20 次查询） | 一次批量取标题（`get_documents(ids)` 或复用已有列表） | 改代码 |
| AL-T11 | `tests/tools/test_knowledge_tools.py:163` | 测试仍构造 `routes=("k","v")`，实现输出 `vector,keyword` | 测试改为 `("vector","keyword")`，与 §7.3 一致 | 改测试 |
| AL-T12 | `knowledge_api.py:768-776` | WebUI 禁用态用 `degraded=["disabled"]` / `reason="knowledge_disabled"`，不在降级词表 | 补进 `_DEGRADED_NOTES` 词表 | 改代码 |
| AL-T13 | `knowledge_common.py:67-72` | `no_kb_message()` 不含「没有执行检索」字样 | 与 §7.3 的字面校验口径统一 | 改代码 |

### 15.3 与 WeKnora 检索链路的结构性差异（登记，非缺陷）

| 差异 | WeKnora | 本方案 | 判断 |
| --- | --- | --- | --- |
| 候选池闸门 | 三路各 50 → 归并 ~500 → 去重 ~380 → **截断 200** → 精排 → 阈值 12 → MMR ~6 | 召回池（关键词 100 / 向量 50）→ 融合 → 去重 → MMR | 我们没有「融合后截断 200」这道闸：v1 无精排，池即 RRF 的输入边界；WeKnora 的 200 是为精排留余量，无精排时无意义 |
| 池的 KB 数因子 | `pool = max(topK*5, 50) * len(KBs)` ≤ 500 | `min(max(limit*10, keyword_pool_size), 300)` / `min(max(limit*5, pool_size), 200)` | v1 一次检索面向可见库集合、不按 KB 扇出（WeKnora 按模型分组扇出到多个 target）。多 KB 扇出与 `bestRanks` 一并进 v2 |
| `bestRanks` | 跨多张列表取最佳名次 | 无 | v1 每路一张列表，不需要（§13 已登记） |
| 查询改写 / 精排 / 图谱路 | 有 | 无 | 有意裁剪（父方案 §1、§18） |

## Next

**（spec 已写入，须暂停等用户确认 — 见 `harness-kit/core/routing.md` § 阶段门禁）**

- 确认方案 → 说「写计划」或「制定实施计划」
- 只要修正父方案与实施计划 → 说「同步父方案」
- 觉得范围可再收窄/调整 → 直接给修改意见
- 范围小、无需计划 → 说「直接实现」（仍建议先出 WU 计划）
