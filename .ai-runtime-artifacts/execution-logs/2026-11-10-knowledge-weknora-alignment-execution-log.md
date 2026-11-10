---
artifact: execution-log
batch: knowledge-weknora-alignment
date: 2026-11-10
branch: feature/desktop-app
base_ref: 024c9a78
route: implementation-handoff
status: completed
---

# 执行日志 — 知识检索 WeKnora 对齐补齐

## 1. 入口状态

接手 HANDOFF（WU1–WU8 代码已写、16 个测试待改口径、实机未做）。实际基线：
`pytest tests/knowledge tests/tools/test_knowledge_tools.py` → 395 passed / 26 failed。

- 其中 18 个为本次口径变更（比 HANDOFF 多 2 个：`test_embedding::test_missing_remote_key_is_unavailable`、
  `test_rerank::test_build_reranker_requires_enabled_and_target`，经复测是本机
  `NO_PROXY=[::1]` 被 httpx 当作 URL 解析导致的环境失败，与代码无关；`env -u NO_PROXY` 后通过）。
- 8 个为 Windows chmod/symlink/CRLF 基线失败。

## 2. WU 状态

| WU | 结果 | 关键文件 |
| --- | --- | --- |
| WU1 | 完成 | `nanobot/knowledge/text.py`、`tests/knowledge/test_text.py` |
| WU2 | 完成 | `rerank.py`（字符分批、`RerankerUnavailableError`）、`test_rerank.py` |
| WU3 | 完成（语义修正） | `retriever.py`、`knowledge_common.py`、`test_retriever.py`、`test_knowledge_tools.py` |
| WU4 | 完成 | `fusion.py`、`test_fusion.py` |
| WU5 | 完成 | `assemble.py`、`test_assemble.py` |
| WU6 | 完成 | `knowledge_search.py`、`knowledge_common.py`、工具用例 |
| WU7 | 完成 | `retriever.py`、`schema.py`、`test_retriever.py` |
| WU8 | 完成 | `knowledge_common.py`、工具用例 |
| WU9 | 完成 | `verifications/2026-11-10-knowledge-weknora-alignment-v2-verification.md` |
| WU10 | 完成（新增） | `chunker.py`、`test_chunker.py`、spec §7.1 |

追加（用户要求，不在原 14 项缺口内）：**修 `knowledge rm` 的 OperationalError**。
`database.py` 增加 `temp_store=MEMORY` + `execute_write` 瞬时错误有界重试；
`repository.delete_document` / `delete_kb` 改走该入口；补 4 个用例（重试 ×2、非瞬时不重试、temp_store）。
根因是 Windows 上级联删除 + FTS 触发器产生的临时文件被瞬时占用（CANTOPEN），非 SQL 错误。

## 3. 本轮决策与偏差

| 决策 | 原因 |
| --- | --- |
| `candidates_kept` = 最终返回条数（装配后），`candidates_in` = 截断前候选总数 | 实机 `in=13 kept=13` 但只返回 9 条，原口径下 `rerank_cut` notice 永不出现；改为真实「N 进 M 出」 |
| 新增列表项切点（`-`/`*`/`+`/`1.`/`1)`） | 用户要求「分点也要，参考原项目」；WeKnora 三级链的 legacy 级用 `\n` 分隔符，我们缺这一级 |
| 删除 `_similarity` / `_to_hit` | basedpyright `reportUnusedFunction` |
| 不引入新配置项 | 分点切点只在超预算时生效，无需开关；避免配置面膨胀 |
| 删除路径加瞬时重试而不是直接抛错 | Windows 上 CANTOPEN 是文件暂时被占用，事务已回滚，重试安全；非瞬时错误不重试 |

## 4. 验证

- `pytest tests/knowledge tests/tools/test_knowledge_tools.py` → **457 passed / 8 failed（Windows 基线）**
- `ruff check`（改动路径）→ 通过
- `basedpyright`（改动模块）→ 0 errors
- 实机（本地 `BAAI/bge-reranker-base`）：`rerank=ok`；父块回填 `chars=1221 blocks="2"`；
  `rerank_cut` 13→10；历史块 rank5 `score=0.000`；budget `omitted count="2"`；分点块尾落在完整分点。

## 5. 产物

- `verifications/2026-11-10-knowledge-weknora-alignment-v2-verification.md`
- `verifications/knowledge-e2e/wu9-v2-evidence.md`
- `verifications/knowledge-e2e/wu9-v2/`（可复现输入）
- 审查文档追加 §8（缺口闭环状态）

## Next

- 未提交、未 push。建议拆 3 个 commit：kernel（knowledge/*）、tool（agent/tools + config）、test/docs。

---

# 第二轮（HANDOFF-2）：扇出 / 分词 / 精排实机 / doc2query

## 6. WU10–WU14 状态

| WU | 结果 | 关键文件 |
| --- | --- | --- |
| WU10 确定性排序键 | 完成 | `fusion.py::hit_sort_key`（Score → kb_id → chunk_index → chunk_id） |
| WU11 精排 remote 实机 | 完成 | `verifications/knowledge-e2e/rerank_service.py`、`rerank_remote_probe.py` |
| WU12 多 KB 扇出 + bestRanks | **本轮收尾完成** | `fusion.py`、`retriever.py`、`service.py`、`ingestor.py`、`test_fusion.py`、`test_retriever.py` |
| WU13 中文分词器 | 完成（本轮复验） | `tokenizer.py`、`ingestor.py`、`retriever.py`、`schema.py` |
| WU14 doc2query | **本轮实现完成** | `questions.py`（新）、`database.py` v2、`repository.py`、`ingestor.py`、`retriever.py`、`schema.py`、`test_questions.py` |

## 7. 本轮决策

| 决策 | 原因 |
| --- | --- |
| `_EmbedderRegistry` 同时接入 Retriever 与 Ingestor | 只接检索层会出现「新库入库时仍用全局 embedder → 被 mismatch 守卫跳过」，per-KB 模型只有两侧都解析才真正可用 |
| 向量/关键词空列表不进入融合 | 组查询后 `vector_lists=[[]]` 曾让纯关键词结果误报 `mode_used=hybrid` |
| 关键词路「组内失败」不改全局裁决 | 每个 embedding 组独立查询；单组 vector/embedding 故障只丢该组，不拖垮其他组 |
| doc2query 默认关闭、每块 3 条、1..10 | 与本地 WeKnora 源码一致：KB 级显式开关（编辑器默认 false）、`QuestionCount` 缺省 3 |
| 问题用独立表 + 独立 FTS + 同一向量集合 | 对齐 WeKnora「问题写成独立检索行，`source_id=chunk_id-qN`，命中回填父块」；不污染 `document_chunks` 的不可变约束 |
| 问题生成在入库 worker 线程内同步执行 | `Ingestor` 是同步纯函数，问题生成放提交后 best-effort；失败只丢问题，文档仍 `ready` |
| 问题 id 前缀反解父块（`<chunk>-qN`） | 块 id 固定 `chk_<hex>`，不会误判；命中问题行直接返回父块正文 |
| 生成通道用知识域独立 OpenAI 兼容配置 | 知识服务只拿到 `KnowledgeConfig`；复用主 Agent provider 需把整份 `Config` 串进 4 个构造点，改动面大于收益 |

## 8. 验证（第二轮）

- `pytest tests/knowledge tests/tools/test_knowledge_tools.py` → **485 passed / 8 failed**
  （8 个均为 Windows 基线：chmod/symlink/CRLF；WU12 的 2 个口径用例已修好）。
- `ruff check nanobot/knowledge nanobot/config/schema.py tests/knowledge tests/tools/test_knowledge_tools.py` → 通过。
- `basedpyright`（本轮改动模块）→ **0 errors**（`rerank.py` 在接手时已无报错）。
- 实机：`verifications/knowledge-e2e/doc2query_probe.py` → 4 chunks / 10 questions；
  关键词与语义均命中父块，`rerank=ok`（本地 `BAAI/bge-reranker-base`），
  证据 `doc2query-probe.json`。
- 解释器说明：`.venv` 未安装 `jieba`，测试使用 `D:\workspace\environment\python3.13\python.exe`。

## 9. 遗留

1. 每 KB 独立 embedding 已可用，但 WebUI/CLI 还没有「改 KB embedding 模型」的入口。
2. doc2query 目前是全局开关（知识配置级），未做 KB 级开关；按 WeKnora 口径后续可加列。
3. 问题生成是入库串行调用，长文档会明显变慢；需要的话再加并发/上限。
4. 旧文档索引版本仍是 `tok1`（分词器升级前的），需 reindex 才能享受 jieba 命中提升。
