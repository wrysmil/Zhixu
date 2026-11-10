---
artifact: verification
route: implementation-handoff
type: knowledge-weknora-alignment-round2
date: 2026-11-10
skills:
  - verification-before-completion
source:
  - .ai-runtime-artifacts/execution-logs/2026-11-10-knowledge-weknora-alignment-handoff-2.md
  - .ai-runtime-artifacts/reviews/2026-11-10-knowledge-weknora-alignment.md
  - D:\studyspace\源码学习\WeKnora（internal/application/service/knowledge_process.go、internal/types/faq.go）
evidence:
  - .ai-runtime-artifacts/verifications/knowledge-e2e/doc2query-probe.json
  - .ai-runtime-artifacts/verifications/knowledge-e2e/rerank-remote-probe.json
base_ref: 024c9a78
---

# 知识检索 WeKnora 对齐（第二轮 WU10–WU14）— 验证

## 范围

| WU | 内容 | 状态 |
| --- | --- | --- |
| WU10 | 确定性排序键补 kb_id 与块序号 | 完成（交接前已做） |
| WU11 | 精排 remote 后端实机（本地 cohere 兼容服务） | 完成（交接前已做） |
| WU12 | bestRanks + 多 KB 扇出 + 每 KB 独立 embedding | **本轮收尾完成** |
| WU13 | 中文分词器（jieba 词典优先） | 完成（交接前已做，本轮复验） |
| WU14 | doc2query 入库期问题生成 | **本轮实现完成** |

## WU12 收尾内容

- `service.py::_EmbedderRegistry`：按 `(backend, model, dimensions)` 缓存 embedder 与 `VectorStore`；
  全局身份命中时复用进程级实例，其余按 KB 身份独立装配。经
  `KnowledgeService.local(identity_resolver=...)` 同时接到 `Retriever` 与 `Ingestor`
  ——只接检索层会让新库入库时仍被 mismatch 守卫跳过向量。
- `retriever.py`：组内空结果不进融合（修复纯关键词结果误报 `hybrid`）；
  单组向量/embedding 失败只丢该组，不拖垮其他组；resolver 返回的 store 不可用标
  `vector_unavailable:<kb>`。
- 关键词路按组查询：每个可见库恰好落进一个组；`embedding_mismatch` /
  `vector_disabled` 的库仍能被关键词路搜到。
- 测试：`test_partial_identity_mismatch_queries_consistent_subset` 改为「覆盖且仅覆盖一次」并
  钉住过期库命中；新增 `test_identity_resolver_fans_out_per_kb`、
  `test_resolver_none_keeps_kb_in_keyword_route`、`best_ranks` / 多列表融合 3 个用例、
  `test_identity_resolver_embeds_with_kb_embedder`。

## WU14 实现内容（对齐本地 WeKnora 源码）

| 环节 | Zhixu 实现 |
| --- | --- |
| 开关 | `knowledge.question_generation.enabled`，**默认关闭**（WeKnora KB 编辑器默认 false） |
| 条数 | `question_count` 默认 **3**，钳到 1..10（WeKnora `QuestionCount` 缺省 3） |
| 通道 | 独立 OpenAI 兼容 `/chat/completions` 配置（`base_url/model/api_key_env`）；temperature 0.7、max_tokens 2048 |
| 输入 | 块正文 + 前后邻居块（各截断 `max_context_chars=1200`）+ 文档名 |
| 解析 | 剥 `<think>` → 逐行剥编号 → 字节长度 > 5 → 最多 N 条 |
| 存储 | 新表 `chunk_questions`（schema v2）+ 独立 FTS；`id = <chunk_id>-qN` |
| 向量 | 问题作为独立向量行写入同一 collection（`QuestionRecord` 满足 `ChunkMeta`） |
| 检索 | 关键词/向量命中问题行都按 id 前缀回填父块；同一父块去重取最高分 |
| 失败语义 | 生成/写行/写向量失败都只降级，文档仍 `ready`；重索引时问题随块级联删除 |
| 修复 | `vector_expectations` 计入问题行，`repair --vectors` 不会把问题向量当孤儿清掉 |

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `pytest tests/knowledge tests/tools/test_knowledge_tools.py` | **485 passed / 8 failed**；8 个为已知 Windows chmod/symlink/CRLF 基线（`storage/test_extract_lines` 1、`storage/test_files` 5、`test_service` 2） |
| `ruff check nanobot/knowledge nanobot/config/schema.py tests/knowledge tests/tools/test_knowledge_tools.py` | All checks passed |
| `basedpyright nanobot/knowledge/{questions,ingestor,retriever,service,repository,database,fusion,rerank}.py nanobot/config/schema.py` | **0 errors, 0 warnings** |
| `python .ai-runtime-artifacts/verifications/knowledge-e2e/doc2query_probe.py` | OK（见下） |

> 解释器：`.venv` 缺 `jieba`，测试与探针使用 `D:\workspace\environment\python3.13\python.exe`。

## 实机证据（doc2query-probe.json）

```json
{
  "elapsed_s": 8.97,
  "chunks": 4,
  "questions": 10,
  "question_fts_hits": 2,
  "keyword": {"mode": "hybrid", "routes": ["vector", "keyword"],
              "top_chunk": "chk_bce58cc70131df90",
              "top_content": "## 回滚\n\n线上版本出问题时的回滚流程:先冻结流量,…"},
  "semantic": {"mode": "semantic", "routes": ["vector"], "rerank": "ok",
               "candidates_in": 4, "candidates_kept": 3,
               "top_chunk": "chk_bce58cc70131df90", "top_score": 0.8115},
  "document": {"status": "ready", "vector_status": "ok",
               "index_version": "norm1:tok2:chunk1"}
}
```

链路：真实 HTTP（内置 OpenAI 兼容 mock）→ 真实 SQLite/Chroma → 本地
`BAAI/bge-small-zh-v1.5` embedding → 本地 `BAAI/bge-reranker-base` 精排。
语义查询「线上版本出问题了怎么回滚」命中问题向量后回填父块，`rerank=ok`。

## 已知偏差 / 遗留

1. doc2query 目前是知识配置级全局开关，未做 KB 级开关（WeKnora 是 KB 级）；后续加列即可。
2. 问题生成为入库串行调用，长文档显著变慢；默认关闭，开启后建议配合队列观察耗时。
3. `Repair` 的 `vector_expectations` 已含问题行，但 `stats` 未单独统计问题条数（暂无 UI 需求）。
4. 旧文档需要 reindex（`index_version=norm1:tok1` → `tok2`）才能使用 jieba 分词与 doc2query。
