---
artifact: plan
type: weknora-alignment-impl
date: 2026-11-10
route: implementation
dispatch: n/a
approved: true
approval_note: 用户原话「全部都改。然后现在精排rerank模型不是引入了吗，然后继续做下去」
source: .ai-runtime-artifacts/reviews/2026-11-10-knowledge-weknora-alignment.md §3.1
---

# 知识检索 WeKnora 对齐补齐实施计划

> 范围：`.ai-runtime-artifacts/reviews/2026-11-10-knowledge-weknora-alignment.md` §3.1 的 14 项缺口（图谱除外）。
> 依据：WeKnora《03 · 检索原理》§7 精排、§8 MMR、§9 上下文装配、§10.2 四个预算。

## WU 拆分

| WU | 目标 | 主要文件 |
| --- | --- | --- |
| WU1 | 文本清洗与 passage 构造对齐 §7.3 | 新增 `nanobot/knowledge/text.py` |
| WU2 | 精排协议增强：请求字符上限分批、outcome 区分（model_unavailable / model_error） | `nanobot/knowledge/rerank.py` |
| WU3 | 精排进出条数透出到工具结果（§7 RecordRetrievalCut） | `nanobot/knowledge/fusion.py`、`retriever.py`、`knowledge_common.py` |
| WU4 | MMR 对齐 §8：enriched token、移除自定参数、倒排加速、k=1 特例 | `nanobot/knowledge/fusion.py` |
| WU5 | 上下文装配 §9.1 + 确定性排序 §9.2 | 新增 `nanobot/knowledge/assemble.py`、`retriever.py` |
| WU6 | 历史引用注入 §9.1 ③ | `nanobot/agent/tools/knowledge_search.py` |
| WU7 | 粗排预算对齐 §3.2 / §10.2：池公式 ×len(KBs)、rerank_top_k | `nanobot/knowledge/retriever.py`、`config/schema.py` |
| WU8 | Agent 输出预算 4/5 按比例收缩 §9.3 | `nanobot/agent/tools/knowledge_common.py` |
| WU9 | 实机验证 + 评测台量化 + 文档回写 | 验证产物、审查文档 |

## 关键口径决策

- **自定参数处理**：`relevance_band`、`adjacent_similarity_floor` 按文章删除判定逻辑；
  配置字段保留为 deprecated（不参与判定），避免旧配置加载失败，与既有 `dedup_jaccard` 处理一致。
- **父子块**：不引入父块表与 DB 迁移。父块回填改为**按需合成**：以命中块为中心，
  在同文档同 heading 下合并相邻块，上限 `parent_max_chars`。达成文章「子块召回准但上下文不足」
  的目标，同时不动存储层。
- **注入历史引用**：从会话历史的 `chunk://` 句柄中取回块，作为补充上下文注入（只读、作用域校验）。
- **排序键**：Score → kb_id → chunk_index → chunk_id（无 FAQ 类型，文章 ChunkType 位省略）。

## 验收口径

- `pytest tests/knowledge tests/tools/test_knowledge_tools.py` 全绿（8 个 Windows chmod/symlink 基线失败除外）。
- `ruff check nanobot/` 无新增告警。
- 实机：本地 cross-encoder（`BAAI/bge-reranker-base`）跑通，工具结果带精排进出统计与装配块。

## Next

- WU1 → WU9 顺序执行，每个 WU 完成后立即补测试。
