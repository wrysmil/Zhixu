---
artifact: research-report
route: roles.web-investigator
skills: []
skills_evidence:
  - skipped: web-investigator (以 Agent/Explore 子代理完成联网检索，未找到独立 skill 文件)
source:
  - https://ir-measur.es/en/latest/measures.html
  - https://github.com/vectara/open-rag-eval
  - https://github.com/stanford-futuredata/ARES
  - https://github.com/BytePioneer-AI/RAGEval
  - https://github.com/pazyork/RagLab
  - https://github.com/firecrawl/rag-arena
  - https://github.com/amazon-science/RAGChecker
  - https://github.com/Arize-ai/phoenix
  - https://github.com/langfuse/langfuse
  - https://github.com/truera/trulens
  - https://github.com/YHPeter/Awesome-RAG-Evaluation
  - https://github.com/ragas-ai/ragas
  - https://github.com/confident-ai/deepeval
  - https://github.com/vishalmysore/ragevalinjava
  - .ai-runtime-artifacts/stack/2026-11-10-knowledge-rag-eval-stack.md
created_at: 2026-11-10
---

# 业界 RAG 检索质量测评做法调研

目的：为知序「知识检索评测台」设计提供外部依据，重点回答三个问题：**测什么指标**、**ground truth 怎么来**、**有没有可直接复用的现成平台**。

结论摘要：检索级测评的主流做法是「gold 相关文档集合 + IR 指标」；但在缺人工标注时，业界一致选择 **LLM-as-judge 打相关性标签**（UMBRELA / AutoNuggetizer / ARES），而不是 Ragas 那种「参考答案句子覆盖」——后者需要 reference 答案且是答案级而非 chunk 级。带 Web 标注 UI 的开源项目很少；RAGEval 与 RagLab 是仅有的两个可参考形态。

## 1. 检索级指标的标准口径

来源：<https://ir-measur.es/en/latest/measures.html>（ir_measures 官方文档）

| 指标 | 定义 | 分母 |
| --- | --- | --- |
| `Recall@k`（TREC 口径） | top-k 中命中的相关文档数 / qrels 中已知相关文档总数 | gold 集合大小（**不是** k） |
| `Hit@k` / `Success@k` | top-k 中是否存在任一相关文档 | —（0/1） |
| `Precision@k` | top-k 中相关文档数 / k | k |
| `MRR` | 首个相关结果排名的倒数 | —（无命中记 0） |
| `nDCG@k` | DCG / IDCG，支持分级相关性 | 理想排序 |
| `MAP` | 各相关位置 Precision 的平均 | gold 数量（同 Recall） |

**关键坑：** 很多外部数字把 `Hit@k` 叫作 `Recall@k`。本项目检索 spec §10 已定义的「Recall@5 = 前 5 条中命中的期望块数 / 期望块总数」用的是**覆盖比例**口径，与 TREC 的 `Recall@k` 一致，命名不冲突；引用外部数字时必须先确认分母。

**业界常用组合：** BEIR/MTEB 系用 `Recall@k + nDCG@k`；只看首个命中用 `Hit@k + MRR`；判全排序质量再加 `MAP`。

**不能直接搬 Ragas：** Ragas 的 `context_precision` 是 LLM 逐 chunk 判定相关性后按位次算 average precision，**必须有 reference 答案**；`context_recall` 是 reference 句子被 retrieved_contexts 覆盖的比例，分母是 reference 句子数——两者都是**答案句级**而非 chunk-id 级，与本项目「chunk 级 gold」的诉求不匹配。
来源：<https://github.com/ragas-ai/ragas>

## 2. Ground truth 的三种来源

来源：<https://github.com/vectara/open-rag-eval>、<https://github.com/stanford-futuredata/ARES>

### 2.1 人工标注 chunk id（最准，成本最高）

每条 query 标注 relevant chunk id 集合（binary 或 graded），粒度 = chunk 级，**必须与切分版本绑定**。

ARES 给出的校准规模参考：人工偏好验证集**至少 50 条、几百条更佳**，用于 PPI（Predictive Power Index）校准。
来源：<https://github.com/stanford-futuredata/ARES>

### 2.2 无 gold 的 LLM 判定（本项目首版方向）

| 方法 | 机制 | 成本 | 适用 |
| --- | --- | --- | --- |
| **UMBRELA** | 复现 Bing 相关性评估，0–3 级，GPT-4o，temperature=0，每段落一次调用后阈值化（≥2 视为相关） | ≈ 段落数 × 1 次调用 | 只需判断「检索到的段落是否相关」 |
| **AutoNuggetizer** | query+passage 迭代抽 1–12 词 nugget（最多 30 个 / 5 轮），分 vital/okay，再给答案打 support/partial/none | 批量 10 条一次调用，多阶段多轮，**最高** | 需要答案级支撑判定 |
| **ARES** | 合成数据 + 微调分类器 + 少量人标 PPI；GPT 做 few-shot 判分并训分类器 | 需训练管线 | 要兼顾准确率与成本 |

**本项目取 UMBRELA 路线**：只对「检索返回的 chunk」判 0–3 相关性，不做 nugget 抽取、不做答案判定。理由是首版只做检索级，且 `SearchHit.content` 已包含 chunk 正文，判定材料天然齐备。

### 2.3 日志反推的弱监督

- rag-arena：用**用户投票 + Elo** 反推检索器排名，无标注也能排出相对优劣。
  来源：<https://github.com/firecrawl/rag-arena>
- open-rag-eval 的 Citation metric：按「被引 passage 是否支撑该 statement」给 1/0.5/0，算 weighted P/R/F1。

## 3. 哪些项目真的带 Web UI

| 项目 | 形态 | 是否有标注 UI |
| --- | --- | --- |
| Ragas | 纯 Python 库 + CLI；experiments 只落本地 CSV，**无 web server** | — |
| DeepEval | pytest 风格库，仓库内无 web server；UI 在商业版 Confident AI | 仅商业版 |
| RAGChecker / ARES / qdrant-rag-eval / Rag-Eval-flow / Awesome-RAG-Evaluation | CLI / notebook / YAML | 无 |
| **RAGEval（灵鉴）** | 完整 Web：React + FastAPI + Postgres，docker-compose；含系统配置 / 性能测试 / 精度评测 / AI 生成问答 / 多系统对比 | **人工协作标注标注为「🔜 计划中」** |
| **RagLab** | 轻量 Web（FastAPI + 前端，`raglab serve` :8099）：数据集、Playground、相似度热力图、UMAP | 无 gold 标注 |
| open-rag-eval | CLI + **Flask REST API** + **Streamlit 查看器**（多 run 叠加对比） | 无 |
| rag-arena | Next.js 投票 + Elo 排行榜 | 投票式 |
| Phoenix / Langfuse / TruLens | 独立 Web 服务：Phoenix `phoenix serve`（datasets/experiments/span 标注 UI）；Langfuse 自托管（人工标注 + 数据集 + 实验对比）；TruLens 内置 Streamlit dashboard | 有，但绑定自家数据模型 |

来源：<https://github.com/BytePioneer-AI/RAGEval>、<https://github.com/pazyork/RagLab>、<https://github.com/vectara/open-rag-eval>、<https://github.com/Arize-ai/phoenix>、<https://github.com/langfuse/langfuse>、<https://github.com/truera/trulens>

**结论：主流 IR 评测项目（Ragas / DeepEval / ARES / RAGChecker）都没有标注 Web UI。**带 UI 的要么是通用观测平台（绑定自家 trace 数据模型），要么是完整评测系统（RAGEval，但其人工标注仍在计划中）。「检索级测评台 + 标注界面」这个组合在开源里没有成熟可直接套用的实现。

## 4. 自建「RAG 检索测评台」的三条路线

| 路线 | 形态 | 代表 | 代价 | 与本项目的契合 |
| --- | --- | --- | --- | --- |
| **(a) 纯 pytest/CLI + 静态报告** | ir_measures/pytrec_eval 算 P@k/Recall@k/nDCG/MAP，出 per-query CSV + 图表 | DeepEval pytest 风格、open-rag-eval `plot` | 1–2 天 | 已有 `tests/knowledge/test_golden_queries.py` 雏形，但**没有标注界面、没有交互对比** |
| **(b) 自建轻量 Web** | 数据集管理 → chunk 级标注 → 批量跑检索 → 指标表 + 两配置对比 | RAGEval（架构）、RagLab（轻量） | 1–2 周 | **推荐**：知序已有 `nanobot/evaluation/` 平台与 WebUI 评测页可复用 |
| **(c) 嵌入既有观测平台** | 把 query/retrieved chunk id/gold 写进 Phoenix/Langfuse trace | Phoenix / Langfuse / LangSmith | 集成 3–7 天 | 被平台数据模型绑定；LangSmith 为闭源 SaaS |

## 5. 常见反模式（评测不可信的来源）

1. **只测合成语料**：open-rag-eval `generate-queries`、Ragas testset 生成、RAGEval AI 生成问答都从同一 corpus 反推 query，天然偏向 embedding 近邻。须混真实日志 query 并留出 held-out 文档。
2. **把 score 当置信度**：UMBRELA 0–3 是相关性等级不是概率；只有 ARES 的 PPI 才给统计置信区间。
3. **LLM judge 自评偏差**：AutoNuggetizer 的 nugget 与打分歧于同一模型会系统性高估；须用交叉模型判分 + 人工 meta-eval（RAGChecker 提供 human preference meta-eval 数据集）。
4. **评测集泄漏 / gold 失效**：切分规则一改，旧 chunk id 全失效——标注应绑 chunk 文本哈希；合成 query 不能同时参与 index。
5. **口径混用**：外部数字里「Recall@k」可能指 Hit@k，比较前先确认分母。
6. **只看均值**：open-rag-eval 同时输出 per-query 分数与 consistency（mean/std/iqr/consistency-adjusted index），避免平均分掩盖长尾失败。
7. **名称混淆**：`BytePioneer-AI/RAGEval`（中文「灵鉴」工具）与 `OpenBMB/RAGEval`（评测数据集）是两个东西。
   来源：<https://github.com/YHPeter/Awesome-RAG-Evaluation>

## 6. 对本项目设计的直接约束

| 约束 | 依据 |
| --- | --- |
| 指标用 TREC 口径的 `Recall@k`（分母=gold 数量），并另出 `Hit@k`，两者不混称 | §1 |
| 标注绑 `content_hash` 而非仅 `chunk_id`；切分变更后 gold 标为失效并提示重标 | §5.4 |
| 判分材料是「数据不是指令」——chunk 正文可能含提示注入 | 本项目既有 `nanobot/evaluation/graders/judge.py` 的守卫设计 |
| 逐题分数与聚合同时呈现，聚合附一致性/长尾信息 | §5.6 |
| 判分失败/超时 → 该题 `not_assessable`，不补 0 | 本项目记忆评测既有口径（`nanobot/evaluation/memory/metrics.py`） |
| 首版不引入 Ragas / DeepEval / Phoenix 等外部依赖 | 本地自托管定位 + 依赖克制 |
