---
artifact: handoff
route: implementation-handoff
batch: knowledge-weknora-alignment
date: 2026-11-10
worktree: （无：Leader 在主 checkout 直接改）
branch: feature/desktop-app
base_ref: 024c9a78
status: 半成品交接（WU1–WU8 代码已写完，16 个测试待改口径，实机验证未做）
skills:
  - source-driven-development
  - verification-before-completion
source:
  - .ai-runtime-artifacts/reviews/2026-11-10-knowledge-weknora-alignment.md
  - .ai-runtime-artifacts/plans/2026-11-10-knowledge-weknora-alignment-impl-plan.md
created_at: 2026-11-10
platform: qoder-desktop
---

# HANDOFF — 知识检索 WeKnora 对齐补齐（14 项缺口）

## 中断点

- 最后完成 WU：**WU8（代码写完，未提交）**
- 当前 GROUP：WU1–WU8 的**代码已落地**，卡在「测试口径未同步 + 实机验证未做」
- 下一步：**先改 16 个测试的期望口径 → 跑绿 → 补新增行为的测试 → 实机验证 → 提交**

> 接手者请**直接接着做**，不要重写已完成的实现。实现层的判断依据见 §3「已做决策」。

## 0. 一句话状态

用户要求「全部都改」——把审查文档 §3.1 的 14 项缺口（图谱除外）全部对齐 WeKnora。
**实现代码基本写完了**（8 个 WU），但：

1. **16 个测试仍按旧口径断言**（多数是行为变更导致的合理变更，少数要改实现）；
2. **实机验证没做**（本地 cross-encoder `BAAI/bge-reranker-base` 尚未跑通新链路）；
3. **工作区未提交**（含 2 个新文件未纳入 git）。

`pytest tests/knowledge tests/tools/test_knowledge_tools.py` 当前：**397 passed / 24 failed**
（其中 8 个是 Windows 基线失败，与本次无关；16 个是本次口径变更导致的）。

## 1. 已提交（不要动）

| 提交 | 内容 |
| --- | --- |
| `227903ac` | feat(knowledge): 接入 cross-encoder 精排与三档阈值降级 |
| `2d1ad363` | feat(agent): 知识检索工具支持查询改写与意图门控 |
| `024c9a78` | docs(review): 记录知识检索与 WeKnora 的对齐审查 |

基线：`git log --oneline -3` 可见；审查矩阵在
`.ai-runtime-artifacts/reviews/2026-11-10-knowledge-weknora-alignment.md` §3。

## 2. 未提交的工作区状态

```
 M nanobot/agent/tools/knowledge_common.py     渲染层：精排进出通知 + 4/5 预算收缩
 M nanobot/agent/tools/knowledge_search.py     历史引用注入（§9.1 ③）
 M nanobot/config/schema.py                    新增配置字段
 M nanobot/knowledge/fusion.py                 MMR 对齐 §8 + SearchHit 加字段
 M nanobot/knowledge/rerank.py                 请求字符上限分批 + outcome 区分
 M nanobot/knowledge/retriever.py              主链路重排 + 装配接线
 M nanobot/knowledge/repository.py              list_document_titles 收 Iterable
 M nanobot/knowledge/service.py                新参数透传
 M tests/knowledge/test_retriever.py           FakeRepo 补 list_document_titles/fetch_neighbors
?? nanobot/knowledge/assemble.py               新文件：上下文装配（未纳入 git）
?? nanobot/knowledge/text.py                   新文件：清洗与 passage 构造（未纳入 git）
```

**两个新文件还没 `git add`**——提交时别漏。

`ruff check nanobot/knowledge nanobot/agent/tools/knowledge_*.py` → **All checks passed**。

## 3. 已做决策（别推翻，除非有更好理由）

| 决策 | 内容 | 理由 |
| --- | --- | --- |
| 父块不落库 | 不加父块表、不做 DB 迁移；父块**按需合成**：命中块短于 `parent_min_chars`（默认 600）时，用同文档同 heading 的相邻块补齐，上限 `parent_max_chars`（2400） | 达成「子块召回准但上下文不足」的修复目标，同时不动存储层与既有索引 |
| 自定参数废弃 | `relevance_band` / `adjacent_similarity_floor` **不再参与判定**；配置字段保留为 deprecated（避免旧配置加载失败），与既有 `dedup_jaccard` 处理一致 | 文章里没有这两项，且邻块下限与「邻块合并」方向相反 |
| 精排 query 同源 | 工具把 `effective_query`（改写后）传给 `service.search`，召回与精排用同一句 | 文章 §7.0 ③ 明确要求送模型的是 RewriteQuery |
| 历史引用注入位置 | 在工具层读会话历史里的 `chunk://` 句柄 → 组装成 `SearchHit` → 经 `service.search(extra_context=...)` → 在**装配阶段**并入并参与最终去重 | 文章 §9.1 ③ 在装配阶段注入；作用域校验与检索一致 |
| 确定性排序键 | Score → kb_id → chunk_index → chunk_id（文章 §9.2 的 ChunkType 位我们没有 FAQ 类型，省略） | 同样的请求连跑两次顺序必须一致 |

## 4. 新链路（改完后的实际形态）

```
作用域校验 → 分词 → 向量召回(池=max(topK*5,50)*len(KBs)≤500)
           → 关键词召回(同公式) → 融合(_fuse 三分支) → 去重(三级)
           → 精排(截断200 → passage=标题+面包屑+清洗正文 → 分批打分
                  → 三档阈值 → 复合分0.6/0.3/0.1)
           → 截到 rerank_top_k(10) → MMR(λ=0.7, enriched token, 倒排加速)
           → 装配(父块回填 + 连续正文合并 + 最终去重 + 历史引用并入)
```

关键签名变化（**改测试时会用到**）：

- `Retriever.search(...)` 新增 `extra_context: Sequence[SearchHit] = ()`
- `Retriever._apply_rerank(...)` 返回 **5 元组**：`(hits, labels, outcome, candidates_in, candidates_kept)`
  （原来是 3 元组——这是 4 个 rerank 测试失败的直接原因）
- `mmr_rerank(hits, *, limit, lambda_)` 删掉了 `relevance_band` 与 `adjacent_similarity_floor`
- `SearchResult` 新增 `candidates_in` / `candidates_kept`
- `SearchHit` 新增 `title: str = ""` 与 `source_chunk_ids: tuple[str, ...] = ()`
- `RerankSettings` 新增 `max_request_chars: int = 8000`
- `RetrieverSettings` 新增 `rerank_top_k=10` / `parent_max_chars=2400` / `parent_min_chars=600` / `short_chunk_chars=200`；
  **删除** `relevance_band` / `adjacent_similarity_floor`
- 新异常：`RerankerUnavailableError`（模型不可用，区别于调用失败 `RerankerError`）

## 5. 待完成 WU

| WU | 状态 | 剩余动作 |
| --- | --- | --- |
| WU1 文本清洗与 passage 构造 | code_done | 补 `tests/knowledge/test_text.py` |
| WU2 精排协议增强 | code_done | 补「按字符切批」「model_unavailable 区分」用例 |
| WU3 精排进出统计 | code_done | 补「工具结果含 rerank_cut notice」用例 |
| WU4 MMR 对齐 | code_done | 删/改 2 个旧用例（relevance_band / 邻块下限），补 enriched token 与 k=1 用例 |
| WU5 上下文装配 | code_done | 新建 `tests/knowledge/test_assemble.py`（父块回填 / 连续合并 / 最终去重） |
| WU6 历史引用注入 | code_done | 补「会话历史句柄 → 注入块」用例 |
| WU7 粗排预算对齐 | code_done | 改池公式相关断言；补 `rerank_top_k` 截断用例 |
| WU8 Agent 输出预算 4/5 | code_done | 补「预算收缩 + omitted notice」用例 |
| WU9 实机验证与文档 | **not_started** | 见 §7 |

## 6. 当前 16 个失败用例（逐条处理指引）

**A. 配置往返（2 个）— 改期望**
`tests/knowledge/test_config.py::test_camel_case_dict_round_trips`、`::test_camel_case_json_round_trips`
→ `CAMEL_PAYLOAD` 少了新字段：`retrieval.rerankTopK / parentMaxChars / parentMinChars / shortChunkChars`、
`rerank.maxRequestChars`。

**B. MMR 旧口径（2 个）— 删或反写**
`tests/knowledge/test_fusion.py::test_mmr_adjacent_block_floor_suppresses_neighbor`、
`::test_mmr_relevance_band_drops_far_below_top`
→ 这两个参数已不存在。建议改成「**不再**压制邻块 / **不再**按相关性带裁剪」的正向断言。

**C. 精排返回元组（4 个）— 改期望**
`tests/knowledge/test_rerank.py` 的
`test_rerank_reorders_by_model_score_and_reports_outcome`、
`test_rerank_failure_falls_back_to_recall_order`、
`test_rerank_without_model_reports_no_model`、
`test_rerank_all_below_threshold_returns_empty`
→ 解包 5 元组；同时注意 `no_model` 分支现在返回 `candidates_in=0, candidates_kept=0`，
`model_error` 分支返回 `candidates_in=len(pool), candidates_kept=len(pool)`。

**D. 检索层（8 个）— 多数改期望，1 个要查**
- `test_pool_hard_caps`：`assert repo.fts_calls[0]["limit"] == 300` → 新公式 500
- `test_limit_clamped_to_max` / `test_limit_clamped_to_min`：夹取后 pool 也按新公式重算
- `test_retriever_settings_defaults`：断言 `relevance_band` / `adjacent_similarity_floor` → 换成新字段
- `test_ordering_is_deterministic`：`RetrieverSettings(relevance_band=...)` 传参报错 → 删该参数
- `test_hybrid_merges_both_routes` / `test_hybrid_union_when_disjoint_routes` /
  `test_partial_identity_mismatch_queries_consistent_subset` /
  `test_orphan_vector_hits_are_filtered_out`：**装配改变了返回条数与内容**
  （短块会父块回填，相邻块会合并）。要么改期望，要么把夹具的 `chunk_index` 拉开、
  正文加长到 ≥ `parent_min_chars` 以避开回填。**注意**：夹具里两个块的 `chunk_index` 都是 0，
  会被当成同一段的邻块而合并——建议改成 0 / 1。

**E. Windows 基线（8 个，与本次无关，别去修）**
`tests/knowledge/storage/test_extract_lines.py`（1）、
`tests/knowledge/storage/test_files.py`（5）、`tests/knowledge/test_service.py`（2）
——chmod 0600 / symlink / CRLF 抽取，Windows 恒失败。

## 7. WU9：实机验证怎么做

实例配置（`~/.nanobot/config.json`，注意 `~/.nanobot` 是 `.ai-runtime-artifacts/nanobot-data` 的符号链接）已开启：

```json
"knowledge": { "rerank": { "enabled": true, "backend": "local",
  "model": "BAAI/bge-reranker-base", "device": "cpu", "timeoutSeconds": 60.0,
  "batchSize": 16, "maxConcurrency": 2, "maxCandidates": 200,
  "maxPassageChars": 1200, "threshold": 0.3, "fallbackMinScore": 0.15 } }
```

- 模型缓存在 HF cache（约 1.1 GB），首次加载约 15 s；gateway 常驻只加载一次，CLI 每次重载。
- 上次基线（改前）：同一查询不精排 top1 是「备份策略」（答非所问），开精排后 top1 是「生产回滚流程」。
- **本次要复验的**：① `rerank="ok"` 仍出现；② 工具结果里出现 `<notice kind="rerank_cut">`；
  ③ 装配后的块内容变长（父块回填生效）；④ `<omitted reason="budget">` 与预算 notice 出现时机正确。
- 证据目录：`.ai-runtime-artifacts/verifications/knowledge-e2e/`（上一轮证据在同目录，**不要覆盖**，
  新一轮用 `*-v2` 前缀）。
- WebUI 端口 8765，gateway HTTP 18790；headless 浏览器用本机 Playwright（msedge channel），
  URL 必须带 `?bootstrapSecret=`。

## 8. 已知遗留问题（接手后要处理）

1. **`nanobot/knowledge/rerank.py` 有 17 个 basedpyright strict 错误**（**改前就存在**，
   上一轮提交没跑类型检查）：`_parse` 里 `payload.get`/`item.get` 的 unknown 类型、
   `_post_batch` 的 lambda 参数 unknown。CI 跑 `uv run --no-sync basedpyright`（strict），
   **现在这个文件会让 CI 红**。修法：给 `_parse` 的 `raw` 标注 `list[Any]`，
   把 `item` 显式转成 `dict[str, Any]`；`_split_batches` 的 lambda 换成带类型的局部函数。
2. `nanobot/knowledge/assemble.py` 的 `_merge_consecutive` 只在「同文档 + 同 heading +
   chunk_index 连续」时合并；父块回填后 `chunk_index` 不变，合并判定用的是**回填后**的块，
   语义基本正确，但**没有测试覆盖**——补 `test_assemble.py` 时要覆盖这一条。
3. `nanobot/knowledge/assemble.py` 的 `TitleLoader` / `NeighborLoader` 类型别名在
   `retriever.py` 里用闭包适配（`fetch_neighbors` 的 before/after 是 keyword-only）。
4. `knowledge_common.py` 的 `history_chunk_ids_provider_from` 在函数体内 import
   `current_request_session_key`（避免模块级循环导入）——保持这个写法。
5. 装配会让每次检索多 N 次 `fetch_neighbors` 查询（N = 命中条数）。命中条数 ≤ 10 时可接受；
   若评测台发现延迟上升，再考虑批量化。

## 9. Git 沙箱与红线

- **分支**：`feature/desktop-app`（未推送）。上一轮 3 个 commit 也**未 push**。
- **不要 push、不要开 PR**——按 `harness-kit/project.git.md`，Leader 不自动 push，等用户确认。
- **不要提交** `.ai-runtime-artifacts/` 下与本任务无关的历史遗留产物（工作区里有大量未跟踪的
  旧 `eval-*` / `pytest-of-Huangqh/` / `node_modules/` 等目录，**不要 `git add -A`**）。
- 提交粒度建议：
  1. `feat(knowledge): 对齐 WeKnora 的 MMR、装配与粗排预算`（nanobot/knowledge/*）
  2. `feat(agent): 知识检索输出精排子集声明与历史引用注入`（nanobot/agent/tools/*、config）
  3. `test(knowledge): 覆盖 WeKnora 对齐后的检索口径`（tests/*）
  4. `docs(verification): 记录对齐后的实机验证`（verifications）
- Angular 中文提交信息，格式 `type(scope): 主题`。
- **密钥红线**：配置文件里的 `api_key` 一律不外传、不写进产物。

## 10. 给下一个 Agent 的指令

```text
从 HANDOFF 恢复，继续 WeKnora 对齐补齐。

先做这三件事，按顺序：
1. 读 .ai-runtime-artifacts/plans/2026-11-10-knowledge-weknora-alignment-impl-plan.md 与
   .ai-runtime-artifacts/reviews/2026-11-10-knowledge-weknora-alignment.md §3.1，确认 14 项缺口范围。
2. 按 HANDOFF §6 逐条处理 16 个失败用例（配置往返 2 + MMR 旧口径 2 + 精排元组 4 + 检索层 8），
   注意区分「改期望」与「改实现」：夹具 chunk_index 撞车导致的合并是预期行为，改夹具不要改实现。
   然后补新行为的测试：test_text.py、test_assemble.py、以及 rerank 分批/outcome/装配/历史引用/预算收缩用例。
3. 修 nanobot/knowledge/rerank.py 的 17 个 basedpyright strict 错误（改前就存在，但会让 CI 红）。
   跑通 `pytest tests/knowledge tests/tools/test_knowledge_tools.py` 与 `basedpyright nanobot/`。

之后做 WU9 实机验证（HANDOFF §7 有实例配置与端口），证据写
.ai-runtime-artifacts/verifications/knowledge-e2e/（用 *-v2 前缀，不覆盖旧证据），
再回写审查文档 §3.1 的缺口状态。

红线：不 push、不开 PR、不 git add -A、配置文件里的 api_key 不外传。
```
