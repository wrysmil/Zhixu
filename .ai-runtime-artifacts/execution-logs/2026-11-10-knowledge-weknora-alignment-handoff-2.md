---
artifact: handoff
route: implementation-handoff
batch: knowledge-weknora-alignment-round2
date: 2026-11-10
worktree: （无：主 checkout，与另一 Agent 并行作业）
branch: feature/desktop-app
base_ref: 024c9a78
status: 半成品交接（WU10/WU11/WU13 已完成并验证；WU12 代码写完待收尾；WU14 只调研未实现）
skills:
  - source-driven-development
  - verification-before-completion
source:
  - .ai-runtime-artifacts/reviews/2026-11-10-knowledge-weknora-alignment.md
  - .ai-runtime-artifacts/plans/2026-11-10-knowledge-weknora-alignment-impl-plan.md
  - .ai-runtime-artifacts/execution-logs/2026-11-10-knowledge-weknora-alignment-handoff.md
created_at: 2026-11-10
platform: qoder-desktop
---

# HANDOFF-2 — WeKnora 对齐补齐（第二轮：扇出 / 分词 / 精排实机 / doc2query 调研）

## 中断点

- 最后完成：**WU13 中文分词器对齐**（已实机验证）；**WU12 代码写完、剩 2 个测试待收尾**
- 当前 GROUP：第二轮增量（WU10–WU14）
- 下一步：**收尾 WU12 的 2 个失败用例 → 实现 WU14 doc2query → 修 rerank.py 的类型错误 → 提交**

> 本文件是 `2026-11-10-knowledge-weknora-alignment-handoff.md` 的续篇。第一轮（WU1–WU9 的
> 口径修复与测试补齐）**已由另一 Agent 完成**（见该文件），本轮不重复。

## 0. 一句话状态

用户追加了 5 项要求并全部开工：

| 项 | 状态 |
| --- | --- |
| bestRanks + 多 KB 扇出 + 每 KB 独立 embedding | **代码写完，2 个测试待收尾**（WU12） |
| 中文分词器（替 Lindera） | **已完成并验证**（WU13） |
| 确定性排序键补 chunk_index | **已完成**（WU10） |
| 精排 remote 后端实机 | **已完成并验证**（WU11） |
| doc2query 入库期问题生成 | **只完成调研，未实现**（WU14） |

当前测试：`pytest tests/knowledge tests/tools/test_knowledge_tools.py` → **457 passed / 10 failed**
（8 个是 Windows 基线：chmod/symlink/CRLF；2 个是 WU12 的期望待更新）。

## 1. 重要：工作区有两个 Agent 同时在改

**另一个 Agent 正在同一个 checkout 上作业**，已改动（不是我改的，别覆盖）：

| 文件 | 对方在做什么 |
| --- | --- |
| `nanobot/knowledge/chunker.py` | 列表项切点（`- ` / `1. ` 行首作为分块边界，参考 WeKnora legacy 的 `\n` 切点） |
| `nanobot/knowledge/database.py` | Windows 上 SQLite `CANTOPEN` 的瞬时重试 + 临时表放内存 |
| `nanobot/knowledge/repository.py` | `delete_kb` / `delete_document` 改用 `execute_write` |
| `tests/knowledge/storage/test_database.py`、`test_repository.py`、`test_chunker.py` | 上述两项的测试 |

我这一轮改的：`fusion.py`、`rerank.py`、`retriever.py`、`service.py`、`ingestor.py`、
`tokenizer.py`、`config/schema.py`、`agent/tools/knowledge_{common,search}.py`、
`pyproject.toml`、`tests/knowledge/{storage/test_tokenizer,test_config,test_ingestor}.py`。

**两个新文件还没 `git add`**：`nanobot/knowledge/assemble.py`、`nanobot/knowledge/text.py`
（上一轮产物，本轮仍未提交）。

> 冲突风险：`repository.py` 两边都碰（我改 `list_document_titles` 的参数类型，对方改删除路径），
> 目前是干净叠加。**继续动手前先 `git status` 看一眼对方又改了什么。**

## 2. 本轮已完成（可直接用，别重做）

### WU10 确定性排序键（§9.2）

`nanobot/knowledge/fusion.py::hit_sort_key`：

```python
def hit_sort_key(hit) -> tuple[float, str, int, str]:
    return (-hit.score, hit.kb_id, hit.chunk_index, hit.chunk_id)
```

已替换 fusion 的 4 处排序与 retriever 的精排排序。**排序键从 `(-score, chunk_id)` 变成
Score → kb_id → chunk_index → chunk_id**（文章 §9.2；我们没有 FAQ 类型，省略 ChunkType 位）。

### WU11 精排 remote 后端实机（§7）

`HttpReranker` 之前只有 httpx mock 单测，从没连过真实 socket。本轮补：

- `.ai-runtime-artifacts/verifications/knowledge-e2e/rerank_service.py`
  —— 本地 cohere 兼容服务（`POST /rerank`），内部用本地 cross-encoder 打分；
- `.ai-runtime-artifacts/verifications/knowledge-e2e/rerank_remote_probe.py`
  —— 客户端探针，跑 `HttpReranker` 打真实 HTTP；
- 证据：`.ai-runtime-artifacts/verifications/knowledge-e2e/rerank-remote-probe.json`。

实机结果（4 个候选，查询「线上版本出问题了怎么回滚」）：

```
index=3 score=0.9768  回滚预案：异常时执行 kubectl rollout undo…
index=0 score=0.9694  生产回滚流程：先冻结流量再回滚版本…
index=2 score=0.0027  发布流程：灰度放量 10%…
index=1 score=0.0001  退款与发票：订单完成后可在订单详情页申请开票。
elapsed=0.13s
```

跑法：`python rerank_service.py --port 8801 &` 然后 `python rerank_remote_probe.py`。

### WU13 中文分词器（§4.2）

**结论**：Lindera-py **没有 Windows wheel**（需 Rust 编译，本机无 MSVC/Rust），
改用 **jieba**（纯 Python、MIT、词典 + HMM）——与 Lindera 中文模块的「词典优先、
未登录词交给 HMM」路线一致。

改动：

| 位置 | 内容 |
| --- | --- |
| `nanobot/knowledge/tokenizer.py` | 新增 `jieba` 模式（默认）；旧 `unigram_bigram` / `bigram` 保留可回退 |
| `nanobot/config/schema.py` | `tokenizer_mode` 默认 `jieba`（原 `unigram_bigram`）——**这个字段此前是死的**，没接进任何代码 |
| `nanobot/knowledge/ingestor.py` | `Ingestor(tokenizer_mode=...)`；`index_version_for(mode)` → `norm1:tok2:chunk1`（分词变 → 必须重建索引） |
| `nanobot/knowledge/retriever.py` | `Retriever(tokenizer_mode=...)`，查询侧用同一模式（铁律：建索引与查询必须同一分词器） |
| `nanobot/knowledge/service.py` | 从 `knowledge.chunking.tokenizer_mode` 串到两侧 |
| `pyproject.toml` | 新增 `jieba>=0.42.1` |

**关键设计（踩过的坑，勿回退）**：

1. **ASCII 编号/型号整段保留**，不被中文切词拆散：`E-1024` → `e` / `1024` / `e1024`
   （各段 + compact 双 token）。文章 §4.1.4：编号是向量的死区，只能靠倒排精确兜底。
   jieba 直接切会把 `E1024` 变成 `e` + `1024`，倒排也兜不住。
2. **不能用带 `-` 的 token 查 FTS**：FTS5 表是 `detail=default`，**不支持短语查询**
   （实测报 `fts5: phrase queries are not supported`）。compact 形态（`e1024`）
   是单 token，不触发该限制。
3. 分词效果：`研究生命起源` → `研究 生命 起源`；`错误码 E1001` → `错误码 e1001`。
4. `tokenize_query` 的清洗函数改为 `_clean_query_token`（保留 CJK 词与 ASCII alnum）。

副作用：`tests/knowledge/test_golden_queries.py` 的关键词命中率用例**从失败变通过**（jieba 更准）。

### WU12 多 KB 扇出 + bestRanks（§3.1 / §5.4）— 代码写完，待收尾

**fusion.py 新增**：

```python
@dataclass(frozen=True, slots=True)
class RouteSpec:
    weight: float
    lists: tuple[tuple[str, ...], ...]

def best_ranks(lists) -> dict[str, int]      # 跨列表取最小名次（bestRanks）
def weighted_rrf_multi(routes, *, k=60)       # 多路加权 RRF
def single_vector_scores_multi(lists)        # 跨列表取最大 cosine
def single_keyword_scores_multi(lists)       # 每张列表各自除 max，再跨列表取最大
```

`weighted_rrf(vector_ids, keyword_ids, ...)` 保留为两路便捷形式，内部走 `weighted_rrf_multi`。

**retriever.py 改动**：

- 新增 `VectorGroup`（identity + kb_ids + embedder + store）；
- `_vector_scope` / `_group_kbs` / `_identity_groups` → **`_vector_groups`**：
  按 KB 自己的 embedding 身份分组；每组自带 embedder 与向量库；
- 构造函数新增 `identity_resolver: Callable[[KnowledgeBase], tuple[Embedder, VectorStore] | None]`
  （给了就走「每 KB 独立 embedding 模型」；没给则退回单 embedder 兼容路径，
  身份不一致的库标 `embedding_mismatch`）；
- 关键词路改为**按组查**：每个向量组一张关键词列表 + 「向量不可用/无向量」的库自成一组；
  **每个可见库必须恰好落进一个关键词组**（否则索引过期的库连关键词路也搜不到）；
- 池公式按**组内** KB 数算：`max(topK*5, 下限) * len(组内 KB)`，上限 500。

**service.py**：`_build_service` 还没接 `identity_resolver`——**这是 WU12 剩下的活**：

```python
class _EmbedderRegistry:
    """按 (backend, model, dimensions) 缓存 embedder 与向量库（§3.1）。"""
    def resolve(self, kb: KnowledgeBase) -> tuple[Embedder, VectorStore] | None:
        # 用 kb.embedding_backend / embedding_model / embedding_dimensions 建
        # build_embedder(...) + VectorStore(vector_path, backend, model, dims)
        # 身份为 ("none", ...) 或维度 ≤ 0 → 返回 None
```
然后 `KnowledgeService.local(..., identity_resolver=registry.resolve)` → 传给 `Retriever`。

## 3. WU12 剩下的 2 个失败用例

```
FAILED tests/knowledge/test_retriever.py::test_keyword_only_is_keyword_mode
FAILED tests/knowledge/test_retriever.py::test_partial_identity_mismatch_queries_consistent_subset
```

原因都是**行为变更导致的期望过期**，不是实现 bug：

1. `test_keyword_only_is_keyword_mode`：断言 `repo.fts_calls[0]["kb_ids"] == ["kb_a", "kb_b", "kb_c"]`
   —— 现在关键词路按组查，`fts_calls` 会有**多次**调用（每组一次）。
   改期望：断言「所有 KB 都恰好被覆盖一次」。
2. `test_partial_identity_mismatch_queries_consistent_subset`：同理，断言 fts 的 kb_ids 列表。
   **同时要确认**：索引过期的库（`embedding_mismatch`）仍能被关键词路搜到——
   这是本轮特意修的（`covered` / `leftover` 逻辑），测试要把它钉住。

## 4. WU14 doc2query —— 调研结论（已查 WeKnora 源码，未实现）

来源：`Tencent/WeKnora`（commit `cbdcebab`）的 `internal/application/service/knowledge_process.go`
与 `internal/types/generated_question_retrieval.go`。

**WeKnora 的做法**：

| 环节 | 实现 |
| --- | --- |
| 触发 | 入库时对每个 text chunk 生成（KB 级开关 `QuestionGenerationConfig.Enabled`；FAQ 类型库不用这套） |
| 输入 | 块正文 + **前后邻居块**（`<surrounding_context>`）+ 文档名 + 语言 |
| Prompt | 配置项 `Conversation.GenerateQuestionsPrompt`，占位符 `{question_count} {content} {context} {doc_name} {language}`；temperature 0.7，max_tokens 2048，`thinking=false` |
| 解析 | 先剥 `<think>` 块 → 支持纯文本列表或 JSON（`{"questions": [...]}` / `[...]`）→ 逐行剥编号前缀 → 长度 > 5 才留 → 最多 N 条 |
| 存储/索引 | **每条生成问题写成独立的索引行**：`source_id = chunk_id + "-" + question_id`，`source_type = chunk`；问题本身也做 embedding 并进 FTS |
| 检索 | 命中「问题行」→ 回填到父 chunk（chunk_id 相同）→ 参与精排与答案 |
| 重生成 | `RegenerateChunkQuestions` 按内容修订号（`ContentRevision`）原子替换检索条目 |

**要落到 Zhixu 需要**（我还没动）：

1. 存储：`document_chunks` 加 `generated_questions` 列，或新建 `chunk_questions` 表；
2. 入库：`Ingestor` 在写向量/写 FTS 之外，为每个块生成 N 个问题并**单独写入向量与 FTS**
   （source_id 形如 `chk_xxx-q0`）；
3. 检索：命中问题行时回填父块正文（否则模型看到的是问题而不是答案）；
4. LLM 调用：入库期要调 LLM——**这块需要确认走哪个 provider**（当前 `Ingestor` 是同步纯函数，
   没有 LLM 通道；`nanobot/providers/` 有 provider 体系但知识域没接）。
   **建议**：先按「可选开关 + 失败不阻断入库」实现，模型与 key 从配置读，
   拿不到模型就跳过生成（降级，不报错）。
5. 成本：每篇文档 N 次 LLM 调用，入库会明显变慢——必须可选、异步、可重试。

> **建议先问用户三件事**：(a) 入库期问题生成是否默认开启；(b) 用哪个模型/provider；
> (c) 每块生成几条（WeKnora 默认我还没查到确切值，代码里是 `questionCount` 参数）。

## 5. 其他遗留

1. **`nanobot/knowledge/rerank.py` 有 17 个 basedpyright strict 错误**（**改前就存在**，
   上一轮提交没跑类型检查；CI 跑 `uv run --no-sync basedpyright`，会让 CI 红）。
   修法：`_parse` 里 `raw` 标注 `list[Any]`、`item` 转 `dict[str, Any]`；
   `_split_batches` 的 lambda 换成带类型的局部函数。
   我本轮把 `RerankerUnavailable` 改名成 `RerankerUnavailableError`（ruff N818 要求 Error 后缀）。
2. `knowledge_common.py` 的 `history_chunk_ids_provider_from` 在函数体内 import
   `current_request_session_key`（避免模块级循环导入）——保持这个写法。
3. 装配（`assemble.py`）会让每次检索多 N 次 `fetch_neighbors`（N ≤ 10，可接受）。
4. 索引版本从 `tok1` 变 `tok2` → **已有文档需要 reindex**（`index_version` 不匹配的会被
   `stats_summary` 统计为 stale；CLI 有 reindex 入口，验证前记得重跑）。

## 6. Git 沙箱与红线

- **分支** `feature/desktop-app`，**未推送**（前三个 commit 也未 push）。
- **不要 push、不要开 PR**（`harness-kit/project.git.md`：Leader 不自动 push）。
- **不要 `git add -A`**：工作区有大量与本任务无关的未跟踪历史产物
  （`eval-*` / `pytest-of-Huangqh/` / `node_modules/` 等）。
- 提交粒度建议：
  1. `feat(knowledge): 分词改为词典优先的中文分析器`（tokenizer/ingestor/retriever/service/config/pyproject）
  2. `feat(knowledge): 按 embedding 空间分组扇出并支持 bestRanks`（fusion/retriever/service）
  3. `feat(knowledge): 确定性排序键补 kb 与块序号`（fusion/retriever）
  4. `docs(verification): 记录精排 remote 实机与分词器验证`（verifications）
  5. `docs(review): 回写 WeKnora 对齐缺口状态`（reviews）
- **密钥红线**：配置里的 `api_key` 一律不外传、不写进产物。

## 7. 给下一个 Agent 的指令

```text
从 HANDOFF-2 恢复，继续 WeKnora 对齐第二轮。

顺序：
1. 收尾 WU12：
   a) service.py 加 _EmbedderRegistry（按 KB 的 embedding 身份缓存 embedder + VectorStore），
      经 KnowledgeService.local(identity_resolver=...) 接到 Retriever —— 这样「每个库配自己的
      embedding 模型」才真正可用（存储层早就支持，只是检索层没用上）。
   b) 改 tests/knowledge/test_retriever.py 的 2 个失败用例：关键词路现在按组多次查 FTS，
      断言改成「每个可见库恰好被覆盖一次」；并钉住「向量索引过期的库仍能被关键词路搜到」。
   c) 补新用例：多组扇出（两个不同 embedding 身份的库各自被检索到）+ bestRanks
      （同一 chunk 出现在多张列表时取最小名次）。
2. 实现 WU14 doc2query：按 HANDOFF-2 §4 的 WeKnora 口径（问题单独入向量与 FTS、
   命中问题回填父块、入库期失败不阻断）。实现前先确认：默认是否开启、用哪个模型/provider、
   每块生成几条。
3. 修 nanobot/knowledge/rerank.py 的 17 个 basedpyright strict 错误（改前就存在，会让 CI 红）。
4. 跑 pytest tests/knowledge tests/tools/test_knowledge_tools.py（基线：457 passed / 10 failed，
   其中 8 个是 Windows 基线）与 basedpyright nanobot/。
5. 实机复验：本地 cross-encoder 跑通新链路（实例配置已开启 knowledge.rerank，
   backend=local，model=BAAI/bge-reranker-base）；注意索引版本已升到 tok2，旧文档要 reindex。

红线：不 push、不开 PR、不 git add -A、api_key 不外传；另一个 Agent 同时在改
chunker.py/database.py/repository.py，动这几个文件前先 git status。
```

## 8. 收尾记录（2026-11-10 晚，第三个 Agent）

- WU12 已收尾：`service._EmbedderRegistry` 同时接 `Retriever` 与 `Ingestor`；
  2 个过期用例改为「每库恰好覆盖一次」，新增扇出 / bestRanks / 组内失败用例。
- WU14 已实现：`knowledge/questions.py`、`chunk_questions` 表（schema v2）+ 独立 FTS、
  问题独立入向量、命中回填父块；口径取自本地 WeKnora 源码（默认关、每块 3 条、1..10、
  KB 级开关的全局近似）。未做 KB 级开关。
- `rerank.py` 的 basedpyright 报错在接手时已不存在（本轮复跑 0 errors）。
- 验证：`485 passed / 8 failed`（8 个 Windows 基线）；实机 `doc2query_probe.py` 通过。
  详见 `verifications/2026-11-10-knowledge-weknora-alignment-round2-verification.md`。
- 仍未提交（工作区混有另一 Agent 的 chunker/database/repository 改动，提交前需按路径拆分）。
