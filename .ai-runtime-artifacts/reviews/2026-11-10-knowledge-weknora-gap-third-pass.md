---
artifact: review
route: document-review
type: knowledge-weknora-gap-third-pass
date: 2026-11-10
skills:
  - source-driven-development
source:
  - D:\studyspace\源码学习\WeKnora（commit dacb9c264）
  - .ai-runtime-artifacts/reviews/2026-11-10-knowledge-weknora-alignment.md
  - .ai-runtime-artifacts/verifications/2026-11-10-knowledge-weknora-alignment-round2-verification.md
scope: 知识域入库/检索全链路逐段对照，只列仍未对齐且值得做的差异
---

# 知识域 × WeKnora 第三轮缺口对照

> 前两轮已对齐的内容不再重复，本轮只回答「还剩什么值得改」。
> WeKnora 侧证据均取自本地源码 `D:\studyspace\源码学习\WeKnora`（比交接文档的 `cbdcebab` 更新）。

## 1. 已确认对齐（不要再动）

| 环节 | WeKnora | Zhixu | 状态 |
| --- | --- | --- | --- |
| 混合融合 | RRF，`bestRanks` 取跨列表最小名次，单路各自归一化 | `fusion.py` 同口径（`weighted_rrf_multi` / `single_*_scores_multi`） | 一致 |
| 多 KB embedding 分组 | 按模型 key 分组，同组只算一次 query embedding | `_EmbedderRegistry` + 按组扇出 | 一致（差异见 §4） |
| 精排 | 复合分 0.6/0.3/0.1，三档阈值降级 | `rerank.py` 同口径 | 一致 |
| MMR | λ=0.7，enriched passage 词面 Jaccard，TopK 内选择 | `fusion.mmr_rerank` 同口径 | 一致 |
| 上下文装配 | 父块回填 → 连续合并 → 短块扩邻 → 二次合并 → 终态去重 | `assemble.py` 同顺序 | 一致 |
| 问题生成 | 独立索引行、命中回填父块、失败不阻断 | `questions.py` + `chunk_questions` | 一致（差异见 §4） |
| 查询改写 / 意图门控 | rewrite + intent；仅 kb_search/clarification 检索 | 工具参数 `rewrite_query` / `intent` | 一致 |
| 超取池 | `max(topK*5, 50) × len(KBs)`，上限 500 | 同公式 | 一致 |

## 2. 仍存在、建议修改（按优先级）

### P0-1 召回侧绝对阈值（小改动，质量收益直接）

WeKnora 在进入融合前就按阈值裁掉弱候选：

- `internal/types/retrieval_config.go:14-37`：`vector_threshold` 默认 **0.15**、`keyword_threshold` 默认 **0.3**、`embedding_top_k` 默认 50；
- 阈值在每次 `SearchParams` 里下传，0 才表示「全保留」。

Zhixu 现状：`RetrieverSettings`（[retriever.py:89](file:///D:/studyspace/project/zhixu/nanobot/knowledge/retriever.py#L89)）只有精排阈值，向量/关键词召回**没有任何下限**。精排未启用时，弱相关块会一路进融合、MMR、装配并返回。

建议：
- 配置加 `retrieval.vector_threshold=0.15`、`retrieval.keyword_threshold=0.3`；
- 向量按 cosine 原值过滤；关键词按「该组内最高分」相对过滤（这是与 WeKnora 唯一的口径差异，因为 SQLite `-bm25()` 量纲不可比，绝对 0.3 等于不过滤）；
- 全部被阈值裁掉时返回 `reason="filtered"`（沿用现有四态），不要伪装成 `no_candidates`。

### P0-2 低召回时的查询扩展（本地、无 LLM、仅关键词路）

WeKnora：`chat_pipeline/search.go:140-147` 在结果数 < `EmbeddingTopK` 时触发；`query_expansion.go:125-186` 用本地规则生成 ≤5 个变体（去停用词 / 引号短语 / 最长分段 / 去疑问词），只走关键词路（`DisableVectorMatch=true`），关键词阈值 ×0.8，并发上限 16，结果并入候选后再去重。

Zhixu 完全没有这一层。对「短查询 + 口语化提问」召回偏少的情况有直接收益，且不依赖模型。

建议：`Retriever` 增加 `expand_on_low_recall=True`（默认开）、`expansion_limit=5`；变体只查 FTS，复用现有 `search_fts`，结果与主召回合并去重后再进融合。

### P0-3 代码块 / 表格 / LaTeX 保护（分块质量）

WeKnora：`infrastructure/chunker/fenced_code.go` 跟踪 ``` / ~~~ 围栏；`heuristic_splitter.go:45-46` 明确「落在 protected region（表格、围栏代码、LaTeX）内部的边界一律丢弃」。另外支持 `token_limit`、`languages`、`strategy=auto/heading/heuristic/recursive/legacy`。

Zhixu：`chunker._cut`（[chunker.py:86](file:///D:/studyspace/project/zhixu/nanobot/knowledge/chunker.py#L86)）只有「标题 → 段落空行 → 列表项 → 句末标点 → 硬切」，没有围栏/表格感知；技术文档里的代码块与表格会被从中间切断。

建议：分块前扫描 fenced code 与 markdown 表格区间，切点时跳过这些区间（与列表项切点同一层优先级）。不需要引入完整 strategy 体系。

## 3. 结构性差异（值得做，但要先定产品口径）

| 项 | WeKnora 做法 | Zhixu 现状 | 代价 |
| --- | --- | --- | --- |
| Chunk 启停/编辑 | `chunk_write.go` 支持改正文、启停；检索过滤 `is_enabled` | `document_chunks.is_enabled` 列存在且检索已尊重，但**没有任何写入入口**（WebUI 只能预览） | 中：API + WebUI + 校验（编辑要触发 reindex/向量重写） |
| 父子块落库 | `EnableParentChild`：父块 4096 / 子块 384，子块入索引，命中回父块 | 查询期按邻块动态合成父块（`assemble.py`），不落库 | 中高：schema + 入库 + 检索双写；收益主要是「父块边界稳定」 |
| FAQ 类型库 | `KnowledgeBaseTypeFAQ`，FAQ chunk、直接回答、`FAQPriorityEnabled` / `FAQScoreBoost`、迭代扩召回 | 无 FAQ 类型（原审查判为不适用） | 高：新库类型 + 分块 + 检索分支 + 直接回答 |
| 摘要 / 自动标签 / Wiki | 入库后处理异步生成摘要 chunk、标签、wiki 页，wiki 页召回 ×1.3 | 无 | 高：需要新 chunk 类型 + 后台任务 + 模型调用 |
| 表格元数据 | CSV/Excel 生成表格摘要与 metadata | 仅抽成纯文本 | 中 |
| 多模态 | 图片 OCR / caption / image vector，独立 chunk 类型 | 仅文本抽取 | 很高 |

## 4. 刻意保留的差异（不是缺陷）

1. **跨 embedding 模型多 KB**：WeKnora 直接拒绝（`knowledgebase_search_storegroup.go:220-252`），Zhixu 按库扇出、组内一次 embedding（这是第二轮用户明确要求）。RRF 只用名次，所以跨模型融合仍然可比。
2. **问题生成开关**：WeKnora 是 KB 级（`QuestionGenerationConfig`），Zhixu 是知识配置级全局开关（默认关）。要做成 KB 级需加列 + WebUI。
3. **任务队列**：WeKnora 用 asynq + Redis 做后处理/问题/图谱任务；Zhixu 用 SQLite 租约 + 单 worker，符合「后台能力随 gateway 启动、不引入外部服务」的项目约束。
4. **意图集合**：WeKnora 有 `web_search` 意图；Zhixu 的联网能力是独立工具，`intent` 只承担「是否检索知识库」。

## 5. 不建议做（明确出局）

- **实体/图谱检索**：WeKnora 依赖 Neo4j + `extract_entity` + graph chunks；个人 agent 引入图数据库与抽取成本不成比例。
- **多向量引擎**（pgvector/qdrant/milvus/doris/opensearch）：Zhixu 单机 Chroma 足够。
- **文件夹 / GitLab 数据源 / 团队多租户 / 数据分析插件**：产品形态不同。

## 5.5 落地状态（2026-11-10 晚）

| 项 | 状态 | 验证 |
| --- | --- | --- |
| P0-1 召回阈值 | **已实现**：`vector_threshold=0.15`、`keyword_threshold=0.3`（组内相对） | `verifications/2026-11-10-knowledge-recall-thresholds-and-protection-verification.md` |
| P0-3 分块保护 | **已实现**：围栏代码 / markdown 表格 / `$$`；围栏内伪标题不开节；超长区间回退硬切 | 同上（含真实 extractor+chunker 实机输出） |
| P0-2 查询扩展 | **已实现**：候选 < `pool_size` 时本地变体（≤5），只走关键词路、阈值×0.8 | 同上 |
| P1（chunk 启停、父子块、FAQ、摘要/标签） | 未做，待产品口径 | — |

## 6. 建议的下一步

如果只做一轮，建议按 **P0-1 → P0-3 → P0-2** 的顺序：

1. P0-1 改动集中在一个函数 + 两个配置字段，最容易验证（构造低分候选，断言被裁、`reason=filtered`）；
2. P0-3 只动 `chunker.py`，用围栏代码/表格样本做回归；
3. P0-2 涉及 `retriever.search` 的控制流，最后做。

三项都不改 schema、不新增外部依赖，适合当前分支直接做。**本轮已全部落地**；下一轮再从 §3 的结构性差异里按产品口径挑选。
