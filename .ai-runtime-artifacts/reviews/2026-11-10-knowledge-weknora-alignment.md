---
artifact: review
type: weknora-alignment
date: 2026-11-10
scope: nanobot/knowledge + knowledge_search tool
source: 03 · 检索原理（WeKnora 文章）+ knowledge-center/retrieval spec
status: partial
---

# 知识检索与 WeKnora 对齐审查

> 目的：逐项对照 WeKnora《03 · 检索原理》，区分「对齐」「有意的 v1 裁剪」「我们自己发明的实现」，并记录本轮修正。

## 1. 结论摘要

- **对齐**：两段式召回 + 融合、三分支归一化、0.7/0.3 加权 RRF（k=60）、MMR λ=0.7、BM25 + 向量互补、embedding 身份分组、失败降级语义。
- **本轮修正**：去重阈值与判据（原 0.98/0.6/5.0 → WeKnora 的 3 / 子串 / 0.85 / 0.02）；MMR token 从纯正文改为「标题路径 + 正文」；**新增精排层**（§7 口径：候选截断 200、passage=标题+面包屑+正文、三档阈值降级、复合分 0.6/0.3/0.1、失败即降级），默认关闭。
- **有意的 v1 裁剪（不是 WeKnora 原样）**：无图谱路、无父子分块、无 bestRanks 多列表扇出、无查询扩展、无入库期问题生成。
- **仍属自定实现**：`relevance_band`、`adjacent_similarity_floor`、5% 同分规则、召回池公式、`routes/mode_used` 词表、中文 uni+bigram 分词器。这些在 spec 里有定义，但不是 WeKnora 文章的口径，需产品验证。

## 2. 对齐矩阵

| 文章章节 | WeKnora 口径 | 当前实现 | 判定 |
| --- | --- | --- | --- |
| §2.1 查询理解 | 一次 LLM 调用输出 `rewrite_query` / `intent` / `image_description` | **工具契约版已实现**：三个字段都是 `knowledge_search` 的参数——`rewrite_query`（字符串，检索直接用它）、`intent`（意图门控）、`image_description`（接收但忽略，纯文本 KB）；agent 模型填参数即完成查询理解 | 对齐（架构映射：agent 即查询理解） |
| §2.2 保留专名 | 改写必须保留实体、关键词、核心搜索词，禁止元指令 | schema + AGENT.md 明确要求保留专名/型号/错误码/条款号/时间，禁止元指令 | 对齐（提示词层） |
| §2.3 意图总开关 | greeting/chitchat/follow_up/summarize/image_only/doc_only 不检索 | 工具按 `intent` 直接不检索并回明原因；`kb_search`/`clarification`/留空/未知意图都检索 | 对齐 |
| §3.1 扇出 | 按 embedding 模型身份分组，同空间共用一次 query embedding | `_identity_groups` 保留分组循环；当前只有一个活动 embedder，单组；每库独立模型是 v2 | 部分对齐 |
| §3.2 超取 | `max(topK*5,50)*len(KBs)`，上限 500 | 向量 `min(max(limit*5,50),200)`；关键词 `min(max(limit*10,100),300)` | **自定公式（v1 单次合并查询，spec 已记录）** |
| §3.3 显式作用域 | 显式范围下阈值让位 | `kb_ids` 越权/不存在直接报错；没有 rerank 阈值概念 | 对齐（范围校验） |
| §4 三路召回 | 向量 + BM25 + 图谱 | 向量 + FTS5/BM25；**无图谱路** | 两路对齐，图谱 v2 |
| §4.1 向量 | cosine + HNSW/ANN | Chroma HNSW，cosine；`max(0,1-distance)` | 对齐 |
| §4.2 关键词 | 倒排 + Lindera 中文分词 + BM25 | SQLite FTS5 + 自研 CJK uni+bigram 分词 + `bm25()` | **自研分词（spec 已记录，和 Lindera 不同）** |
| §4.4 partial error | 单路失败返回其余结果 + error 标记 | `degraded` / `routes` / `reason`；embedding 失败可退关键词，纯向量失败报错 | 基本对齐（词表自定） |
| §5.1/5.2 归一化 | 只有向量保留 cosine；只有关键词各自除 max；两路加权 RRF / maxRRF | `single_vector_scores` / `single_keyword_scores` / `weighted_rrf` | 对齐 |
| §5.3 RRF | `w/(k+rank)`，0.7/0.3，k=60，映射 [0,1] | 同公式、同默认值 | 对齐 |
| §5.4 bestRanks | 多列表取最佳名次 | 当前只有一张向量列表 + 一张关键词列表，没有跨 KB/引擎多列表 | v1 不需要；v2 多列表再补 |
| §5.4 同分保留规则 | 文章未定义 | 分数差 >5% 取高分，否则取更长块，再取小 chunk_id | **自定（spec §5.4）** |
| §6 去重 ③ | 长度比 ≤3；短文本是长文本子串；或 token 覆盖 ≥0.85 且独有 ≤2% | **本轮已改为同一口径**；`dedup_jaccard` 字段保留但不再参与判定 | **已修正** |
| §7 精排 | cross-encoder，200 候选，三档阈值 + 复合分 | **已实现并接真模型**：`Reranker` 协议 + 两种后端（cohere 兼容 HTTP / 本地 sentence-transformers cross-encoder）、候选截断 200、passage=标题+面包屑+正文、三档阈值降级、复合分 0.6/0.3/0.1、失败退化为召回顺序；当前实例用 `BAAI/bge-reranker-base`（本地 CPU） | 对齐 |
| §8 MMR | λ=0.7，token 来自 enriched passage（标题/面包屑 + 正文） | λ=0.7；**本轮 token 已改为 heading_path + content**；同文档邻块相似度下限 0.25 | 部分对齐（邻块下限自定） |
| §9 上下文装配 | 子块→父块、相邻块合并、邻居扩展 | 不做父子块；`knowledge_read` 按 chunk/doc 句柄扩读 | **v1 裁剪**（spec 已记录） |
| §9.3 Agent 预算 | 输出预算 4/5，超预算按比例收缩，带句柄 | 单条 1000 字、总预算 6000 字；句柄 `chunk://`/`doc://`；未做 4/5 动态收缩 | 部分对齐 |
| §10 失败语义 | 单路/单 target/embedding 失败分别降级 | 向量失败退关键词；关键词失败且向量失败才抛；`degraded` 标记 | 基本对齐 |
| 查询扩展 | 变体 ≤5、并发 16 | 未做 | v2 |

## 3. 粗排 → 细排逐工序对照（除图谱外）

我们实际顺序（`nanobot/knowledge/retriever.py::Retriever.search`）：

```
作用域校验 → 分词 → 向量召回(池≤200) → 关键词召回(池≤300)
→ 融合(_fuse) → 去重(dedup_partial) → 精排(截断200→拼passage→打分→三档阈值→复合分)
→ MMR(relevance_band 过滤 + λ=0.7 贪心) → 直接返回
```

文章骨架：①融合 ②去重 ③截断200 ④精排 ⑤阈值+复合分+top_k ⑥MMR ⑦装配。
**前六道都有，第七道完全没有。**

| 工序 | 文章 | 我们 | 判定 |
| --- | --- | --- | --- |
| ① 融合在去重之前 | §6 强调顺序 | `retriever.py` 融合 → 去重 | 对齐 |
| ③ 截断 200 | `topByScore(200)` 按分取 | `candidates[:200]` | 对齐（无图谱豁免） |
| ④ 精排 query 同源 | §7.0 ③「送模型的是 RewriteQuery」 | 工具传 `effective_query`，召回与精排同一句 | 对齐 |
| ④ passage | 标题+面包屑+清洗正文+图注/OCR | title+heading+content，无清洗 | 部分对齐 |
| ⑤ 三档阈值 | 含显式作用域无下限 | `rerank.py::apply_threshold` | 对齐 |
| ⑤ 复合分 | 0.6/0.3/0.1 + clamp[0,1] | `rerank.py::composite_score` | 对齐 |
| ⑥ MMR | λ=0.7，token=enriched passage（含标题） | heading+content，缺标题 | 部分对齐 |
| ⑦ 上下文装配 | CHUNK_MERGE 七步 | 无 | **缺失** |

### 3.1 缺口清单

| # | 缺口 | 位置 | 严重度 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | 上下文装配整段缺失 | `retriever.py` / `chunker.py` | 高 | 文章 §9.1：子块→父块回填、相邻块合并、邻居扩展、注入历史引用、FAQ 补答、最终去重。我们 `chunker.py` 不产父子块（无 `parent` 字段），相邻块合并也没有，只靠 `knowledge_read` 让模型自己扩读 |
| 2 | 精排截断未告知模型 | `knowledge_common.py::render_search_result` | 高 | 文章 §7 要求 RecordRetrievalCut「N 进 M 出」写进 prompt，否则模型把截断误读为「库里只有这些」。我们只输出 `rerank="ok"` |
| 3 | 无独立 rerank_top_k（=10）预算 | `retriever.py` | 中 | 文章 §10.2 四个预算之一。我们 `limit` 直接当 MMR 选条上限（默认 8），且顺序不是「先截 10 再 MMR」 |
| 4 | 超取池口径 | `retriever.py::_keyword_pool/_vector_pool` | 中 | 文章 `max(topK*5,50)*len(KBs)`、上限 500；我们向量池上限 200、关键词 300，单库合并查询、无 per-KB 扇出 |
| 5 | `relevance_band=0.15` 是自定裁剪层 | `fusion.py::mmr_rerank` | 中 | 文章无此层。在 composite 尺度按「最高分−0.15」砍候选，会砍掉「排第二但同样相关」的合法结果 |
| 6 | `adjacent_similarity_floor=0.25` 是自定惩罚 | `fusion.py::_similarity` | 中 | 文章无此逻辑。它压制「相邻两块同时入选」，与文章 §9.1 的「邻块合并」方向相反，两条互相打架 |
| 7 | passage 无清洗正文、限长不自适应 | `retriever.py::_passage` | 中 | 文章 §7.3：标题+面包屑+**清洗正文**+图注/OCR；限长是 `min(单条上限, 请求上限−query)`，我们固定 1200 |
| 8 | MMR token 缺标题 | `fusion.py::mmr_rerank` | 中 | 文章 §8.2 token 来自 enriched passage（含标题）。标题相同的块本来更易冗余 |
| 9 | 分批只按条数切 | `rerank.py::HttpReranker.rerank` | 低 | 文章 SplitBatches 按 `MaxRequestChars`/`MaxDocuments` 切；32×1200 字符可能超厂商单请求上限 |
| 10 | outcome 命名混装 | `rerank.py` | 低 | 文章分 `model_unavailable` / `model_error`，我们加载失败也归 `model_error` |
| 11 | MMR 朴素 O(k·n²) | `fusion.py::mmr_rerank` | 低 | 文章用倒排表降到 O(k·共享边)；200 条内可接受 |
| 12 | 确定性排序键不同 | `fusion.py` | 低 | 文章 §9.2：Score→KnowledgeID→ChunkType→ChunkIndex→ID；我们是 `(-score, chunk_id)` |
| 13 | Agent 预算固定 6000/1000 | `knowledge_common.py` | 低 | 文章 §9.3 是 OutputBudget×4/5 按比例收缩条数；我们有 `<omitted>` 但不是比例收缩 |
| 14 | FAQ 加权缺失 | — | 低 | 文章 §7.5 FAQScoreBoost；我们无 FAQ chunk 类型，可忽略 |

第 5、6、7、8 条属于「我们自己发明的」——文章没有这些参数。处理口径：要么按文章删掉，
要么在 spec 里明确标为自定并补测；当前 spec 只标了去重阈值与召回池两处自定项，缺口登记不全。

## 4. 「自己乱写」清单

| 项 | 位置 | 严重度 | 处理 |
| --- | --- | --- | --- |
| 去重阈值 0.98/0.6/5.0 与 Jaccard 分支 | `fusion.py` / `RetrieverSettings` / config | 高 | **已按 WeKnora 改为 3 / 子串 / 0.85 / 0.02；Jaccard 字段废弃** |
| MMR 只用正文 token | `fusion.py` | 中 | **已改为 heading_path + content** |
| `relevance_band` 截断 | `fusion.py` / config | 中 | spec 自定；文章没有该层。**缺口 #5**：在 composite 尺度按「最高分−0.15」砍候选，会砍掉「排第二但同样相关」的合法结果。待决：按文章删掉，或保留并在 spec 标明自定 |
| `adjacent_similarity_floor=0.25` | `fusion.py` / config | 中 | spec 自定；文章 MMR 没有邻块下限。**缺口 #6**：与文章 §9.1 的「邻块合并」方向相反。待决同上 |
| 5% 同分规则 | `fusion.py` | 低 | spec 自定；文章未定义。保留，保证确定性 |
| 召回池公式 | `retriever.py` | 中 | spec 自定；与 WeKnora 的 `*len(KBs)`/500 不同。当前单次合并查询，暂不按 KB 扇出 |
| `routes` / `mode_used` 词表 | `fusion.py` / tool | 低 | 产品/UI 契约，自定；语义与文章 `outcome` 不同 |
| CJK uni+bigram 分词 | `tokenizer.py` | 中 | 自研，非 Lindera；spec 已记录，中文召回质量需评测台验证 |
| 无精排 / 图谱 / 父子块 / bestRanks / 查询扩展 | 全局 | 高 | 精排本轮已补；图谱/父子块/bestRanks/查询扩展仍是 v2 缺口，不应宣称对齐 WeKnora 全链路 |

## 5. 本轮落地

- `nanobot/knowledge/fusion.py`
  - 去重：长度比 3、子串判重、token 覆盖≥0.85 且独有≤2%；移除 Jaccard 分支。
  - MMR：token 使用 `heading_path + content`。
- `nanobot/knowledge/retriever.py` / `nanobot/config/schema.py`
  - 默认值同步；`dedup_jaccard` 标记为「兼容旧配置、不再参与判定」。
- `nanobot/knowledge/rerank.py`（新增）
  - `Reranker` 协议、`RankResult`、`HttpReranker`（cohere 兼容 + 分批并发 + logit 归一化 + 密钥脱敏）、
    `LocalCrossEncoderReranker`（sentence-transformers CrossEncoder，logit→sigmoid，模型懒加载 + 进程内缓存 + 打分加锁）、
    `apply_threshold`（三档降级）、`composite_score`（0.6/0.3/0.1）。
- `nanobot/knowledge/retriever.py`
  - 精排接入点：融合+去重 → 截断 200 → passage 构造 → 精排 → 阈值降级 → 复合分 → MMR。
  - 失败一律降级（model_error / no_model），不改变可用性；`SearchResult.rerank` 暴露 outcome。
- `nanobot/knowledge/service.py` / `nanobot/config/schema.py`
  - 新增 `knowledge.rerank` 配置（enabled 默认 false），服务层构造精排器并注入检索器。
- `nanobot/agent/tools/knowledge_search.py` / `knowledge_common.py`
  - `rewrite_query` 为字符串参数，检索优先使用，空则回退 `query`；工具内不调模型。
  - `intent` 为意图门控：`greeting|chitchat|follow_up|summarize|image_only|doc_only` 不检索并回明原因；
    `kb_search|clarification|留空|未知` 都检索（未知默认检索，宁可多检一次）。
  - `image_description` 接收但忽略（纯文本 KB）。
  - 工具结果头带 `rerank="<outcome>"`；精排全拒时区分于「库里没有」。
- `nanobot/cli/knowledge.py`
  - 检索预览头输出 `rerank=`。
- `AGENT.md`（模板 + 当前 identity 副本）
  - 增加「改写保留检索词」与「先判断意图再检索」。

## 6. 验证

| 命令 | 结果 |
| --- | --- |
| `pytest tests/knowledge/test_rerank.py` | 18 passed（协议解析、logit 归一化、分批、三档阈值、复合分、检索接入、失败降级、配置默认值） |
| `pytest tests/knowledge/test_fusion.py tests/knowledge/test_config.py` | 70 passed |
| `pytest tests/knowledge/test_retriever.py tests/knowledge/test_retriever_with.py tests/knowledge/test_golden_queries.py` | 42 passed |
| `pytest tests/tools/test_knowledge_tools.py` | 49 passed（含改写、意图门控、image_description 忽略） |
| `pytest tests/knowledge tests/tools/test_knowledge_tools.py tests/evaluation/knowledge` | 591 passed；8 个 Windows chmod/symlink/换行基线用例失败（与本次无关） |
| 实机（本地 mock 精排服务 + `nanobot knowledge search`） | `mode=hybrid routes=vector,keyword rerank=ok degraded=-`，命中顺序按精排分变化；配置已还原、测试库已删除 |
| 实机（真实模型 `BAAI/bge-reranker-base`） | 同一查询不精排时 top1 是「备份策略」（答非所问），开精排后 top1 是「生产回滚流程」；WebUI 会话工具结果 `rerank="ok"`，模型参数里 `rewrite_query`/`intent` 也已生效 |

## 7. 建议的下一步（按收益）

1. **补 ⑦ 上下文装配 + 精排截断告知模型**（缺口 #1、#2）：这是粗排→细排链路上唯一的结构性缺口，
   改动集中在 `retriever.py` 末端与 `chunker.py`（父子块），收益最大。
2. **多 KB 扇出 + bestRanks**：与每 KB embedding 一起做，让召回池按 `len(KBs)` 放大（缺口 #4）。
3. **图谱/查询扩展**：单独里程碑，不建议与上面两项混做。
4. **自定参数裁决**（缺口 #5、#6、#7、#8）：`relevance_band` 与 `adjacent_similarity_floor`
   要么按文章删掉，要么明确标注为自定口径并补测——目前两者方向与文章相反。
5. **查询理解的独立调用**：当前是「agent 填参数」形态，若要严格照文章的「一次 JSON 调用」，
   需要把查询理解从工具参数升级成独立步骤——先看当前形态的实际效果再决定。
