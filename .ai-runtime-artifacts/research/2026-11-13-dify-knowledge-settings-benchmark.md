# Dify 知识库参数配置调研（前端可控参数对标）

- 日期：2026-11-13
- 目的：为知序知识中心"哪些检索/切块参数应该在前端可调、怎么组织"提供外部对标
- 方法：Dify 官方文档（docs.dify.ai）+ 官方 WebUI 中英文 i18n 文案（`langgenius/dify` main 分支 `web/i18n/locales/zh-Hans/`）+ 后端源码（`api/core/rag/`、`api/fields/dataset_fields.py`）
- 结论可信度：i18n 与源码为直接证据；官方文档页面正文经 WebFetch 摘要，细节以 i18n/源码为准

## 1. 结论摘要

1. Dify 把知识库参数拆成**三处入口**：建库向导（切块/索引）→ 库设置页（切块模式、索引方式、检索设置）→ 应用内知识库绑定（检索模式、权重、Top K、Score 阈值、Rerank 模型、元数据过滤）。前两处是"库级"，第三处是"应用级"，同一份库可以被不同应用用不同参数召回。
2. **权重与 Rerank 是互斥的两种精排模式**：`reranking_mode` 只有 `weighted_score` 与 `reranking_model` 两个取值（`api/core/rag/rerank/rerank_type.py`）。
3. `weighted_score` **不是 RRF**：它对候选重算「jieba 分词 + TF-IDF 余弦」与「embedding 余弦」，再做加权求和
   `score = vector_weight × cos_embed + keyword_weight × cos_tfidf`，最后对融合分过 `score_threshold`
   （`api/core/rag/rerank/weight_rerank.py`）。
4. 参数有**单向不可逆约束**：索引方式只能「经济 → 高质量」升级，不能降级（i18n：`无法从高质量降级为经济`）。
5. 参数变更会**显式提示代价**：换 Embedding 模型提示"将重新索引所有文档，需要时间并消耗 Token"。
6. 配置旁边**永远跟着验证入口**：召回测试（可保存「参考问题」和「问题案例」回流到质量页），以及建库时的「预览块」+「预估块数」。

## 2. 配置入口分层

| 层 | 位置 | 管什么 | 变更代价 |
|---|---|---|---|
| L-A 建库向导 | 创建知识库 → 文本分段与清洗 | 分段模式、分段标识符、最大长度、重叠、预处理规则、索引方式、Embedding 模型、Q&A 模式 | 决定首次入库结果 |
| L-B 库设置 | 知识库设置页 | 分段模式展示、索引方式、Embedding 模型、检索方法（语义/全文/混合）、Rerank 模型、可见权限 | 换 Embedding → 全量重索引 |
| L-C 应用内绑定 | 应用编排里的知识库节点 | 检索模式、权重设置、Rerank、Top K、Score 阈值、元数据过滤 | 即时生效，不改数据 |

## 3. 切块参数（L-A / L-B）

证据：`web/i18n/locales/zh-Hans/dataset-creation.json`、`dataset-settings.json`

| 参数（Dify 文案） | 说明 | 备注 |
|---|---|---|
| 分段模式：通用 / 父子分段 | 通用=检索块与召回块相同；父子=子块检索、父块作上下文 | 另有 Q&A 模式，仅高质量索引支持 |
| 分段最大长度 | 单块字符上限 | 有校验：不能大于上限 |
| 分段重叠长度 | 相邻块重叠字符数 | 提示"建议设为最大分段长度的 10%-25%" |
| 分段标识符 | `\n\n,\n` 逗号连接；可自定义特殊符号（如 `***`） | 超出最大长度时按行切 |
| 父子模式的父/子分隔符 | 父块用 `\n\n`，子块用 `\n` | 父子各有独立分隔符 |
| 全文 / 段落（父块模式） | 整篇作父块；超 10000 token 自动截断 | — |
| 文本预处理规则 | ① 替换连续空格/换行/制表符 ② 去除停用词 ③ 删除 URL 与邮箱 | 三个独立开关 |
| 索引方式：高质量 / 经济 | 高质量=Embedding 向量；经济=每块固定关键词数（10 个） | 只能升不能降 |
| Embedding 模型 | 库级选择，含 Vision 标签提示 | 变更 → 全量重索引 + 消耗 Token |
| 预览块 / 预估块数 | 入库前预览切块结果 | UI 直接给 `{{count}} 预估块` |

## 4. 检索参数（L-C）

证据：`app-debug.json`（`datasetConfig.*`）、`knowledge-settings.json`、官方文档、`dataset_retrieval.py` 默认值。

| 参数 | Dify 文案/字段 | 取值与默认 | 说明 |
|---|---|---|---|
| 检索模式 | `search_method`：语义检索 / 全文检索 / 混合检索 | 默认 `semantic_search` | 全文检索=倒排索引 |
| 精排方式 | `reranking_mode`：权重设置 / Rerank 模型 | 默认 `reranking_enable=False` | 二选一 |
| 权重设置 | `weights.vector_setting.vector_weight` / `keyword_setting.keyword_weight` | 0–1，UI 为语义/关键词双滑块；范围提示 `0.00–1.00` | UI 联动互补；后端实体只校验类型，不强制和为 1 |
| Rerank 模型 | `reranking_model.{provider,name}` | 未配置时提示"未配置 Rerank 模型" | 外部 API 有额外费用 |
| Top K | `top_k` | 源码默认 4（`default_retrieval_model`），文档写默认 3；最小 1 | 提示"系统会根据模型上下文窗口动态调整分段数量" |
| Score 阈值 | `score_threshold_enabled` + `score_threshold` | 默认关闭；文档默认 0.5 | 作用于**融合后的分**，不是单路原始分 |
| 元数据过滤 | `doc_metadata` 字段 | 类型：字符串/数字/时间；命名限小写字母数字下划线 | 仅 Chatflow/Workflow 手动模式支持，AND/OR 组合 |

## 5. 参数旁必须有的"验证入口"（最值得借鉴）

1. **召回测试**（`dataset-hit-testing.json`）：输入真实问题 → 看命中分段（`{{num}} 个召回段落`）→ 记录列表带**来源过滤**（检索测试 / 工作流 / Service API / Agent / MCP）与耗时。
2. **从测试反哺评测集**（`knowledge-retrieval.json`）：召回结果可一键「保存为参考问题」或「保存为问题案例」，并跳转"质量"页 —— 把**调参现场**直接变成**评测资产**。
3. **建库期预览**：切块参数旁边是「预览块」+「预估块数」，避免"入完库才发现切坏了"。
4. **变更代价提示**：换 Embedding/改索引方式都会弹"将重新索引/不可降级"，把破坏性写清楚。

## 6. 与知序现状对照

| Dify 能力 | 知序现状 | 差距/建议 |
|---|---|---|
| 建库向导切块参数 + 预览块 | 后端有 `KnowledgeChunkingConfig`（chunk_size/overlap/tokenizer_mode/paragraph_mode），WebUI 无切块配置 | 补库级切块面板 + 预览（后端已有分块器，可直接复用） |
| 索引方式高低档 | 无（只有 embedding backend: local/remote/none） | 不需要照搬"经济模式"，但可暴露 embedding 模型 + 变更警告 |
| 应用级检索参数 | `knowledge_search` 工具已支持 mode/limit/rewrite；WebUI 检索预览支持 mode/limit | 参数面板缺失：权重、阈值、MMR、精排开关目前只能改 config |
| 权重设置 | `vector_weight=0.7` / `keyword_weight=0.3`（RRF 加权，按名次） | 可直接暴露滑块，但**文案要写清是"排名融合权重"**，不是分数加权 |
| Rerank 开关与模型 | `KnowledgeRerankConfig` 完整（含阈值三档降级、复合分权重） | 前端只暴露"开关 + 模型选择 + 阈值"，批量/字符预算属高级 |
| Top K / Score 阈值 | `default_limit=8` / `max_limit=20`；`vector_threshold=0.15`、`keyword_threshold=0.3` 分路阈值 | 注意：知序是**分路阈值**，不是单一融合阈值，UI 不能照抄单个 Score 阈值 |
| 元数据过滤 | 有 heading_path/page/locator，但无用户自定义元数据与过滤 DSL | 二期再做；当前可先做"按文档/章节范围检索" |
| 召回测试 | 有评测台（数据集/运行/对比）+ 检索预览 | 缺"从检索预览一键存为评测用例"的闭环 |
| 分页/章节溯源 | `SearchHit` 有 page/locator/heading_path，前端有引用卡 | 已优于 Dify 基础形态，保持 |

## 7. 可借鉴 vs 不照搬

**借鉴**
- 三处入口分层（建库 / 库设置 / 应用绑定），与知序"会话级 / 库级 / 系统级"三层对应良好。
- 预设优先、高级折叠：常用参数给滑块/预设，`rrf_k`、`pool_size`、批量预算等收进"高级"。
- 参数旁边放验证：预览块、召回测试、存为评测用例。
- 破坏性变更显式提示（重索引、不可降级、消耗 Token）。
- 冲突提示：`serverConflict`（设置已在别处修改，需重新加载）——知序多端（桌面/WebUI/CLI）更需要。

**不照搬**
- Dify 的 `weighted_score` 是"重算两路分数再加权"，知序是"两路名次加权 RRF + 归一化"。RRF 对量纲更稳（BM25 无界、cosine 有界），**不要为了对齐 Dify 改算法**；UI 上只要把文案从"权重"改成"排名融合权重"并给出单路上限（仅向量 rank1=0.7）的解释即可。
- Dify 单一 `Score 阈值`；知序是向量/关键词分路阈值 + 精排三档降级。UI 应保留分路阈值，并把 `outcome`（ok/threshold_degraded/fallback_top1/…）作为诊断标签展示。
- Dify"经济模式"（固定关键词）在知序没有对应实现，不新增。

## 8. 对下一步 spec 的输入

1. 先做 **L1 会话级**：检索模式、limit、知识库范围 + 诊断信息（`mode_used` / `routes` / `candidates_in/kept` / `degraded` / `rerank outcome`）。
2. 再做 **L2 库级**：切块参数 + 预览块 + 预估块数 + embedding 变更警告。
3. **L3 系统级**只读展示 + 高级编辑：融合权重、阈值、MMR λ、精排批量参数。
4. 每次检索/调参可"存为评测用例"，参数快照写入评测 run 配置，指标可复现。

## 9. 证据来源

- 官方文档：<https://docs.dify.ai/en/cloud/use-dify/knowledge/integrate-knowledge-within-application>
- 官方文档：<https://docs.dify.ai/en/use-dify/knowledge/create-knowledge/chunking-and-cleaning-text>
- 官方文档：<https://docs.dify.ai/en/use-dify/knowledge/create-knowledge/setting-indexing-methods>
- 官方 i18n（zh-Hans）：`web/i18n/locales/zh-Hans/dataset-creation.json`、`dataset-settings.json`、`dataset.json`、`dataset-hit-testing.json`、`knowledge-settings.json`、`knowledge-retrieval.json`、`app-debug.json`
- 官方源码：`api/core/rag/retrieval/dataset_retrieval.py`（`default_retrieval_model`）、`api/core/rag/rerank/rerank_type.py`、`api/core/rag/rerank/weight_rerank.py`、`api/fields/dataset_fields.py`

## 10. 局限

- 官方文档正文经工具摘要，个别默认值（Top K 3 vs 源码 4）以源码为准并已标注分歧。
- 未实机操作 Dify 控制台，滑块步长、预设按钮文案、"质量页"具体布局未验证。
- 未调研 RAGFlow / FastGPT 的同类面板，本次只做 Dify 单点对标。
