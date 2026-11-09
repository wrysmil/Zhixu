---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - harness-kit/.agents/skills/writing-plans/SKILL.md
dispatch: n/a
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/specs/2026-11-09-knowledge-center-spec.md
  - .ai-runtime-artifacts/specs/2026-11-09-knowledge-storage-spec.md
  - .ai-runtime-artifacts/specs/2026-11-09-knowledge-retrieval-spec.md
  - .ai-runtime-artifacts/plans/2026-11-09-knowledge-center-plan.md
  - .ai-runtime-artifacts/verifications/2026-11-09-knowledge-retrieval-fusion/verification.md
created_at: 2026-11-10
status: parked
approved: false
parked_at: 2026-11-10
parked_reason: 用户验收改为 `search_rag` 工具 + 会话调用；本对齐计划暂缓，不阻塞 v1 验收
---

# 知序知识中心 · 实现对齐收口计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已落地的 v1 实现与三份方案之间的 26 条偏差（`AL-*`）收口，让代码与方案一致，并让每条不变量都有可执行的测试证据。

**Architecture:** v1 功能已全部落地（commit 至 `1532b9b8`，468 个 knowledge 测试通过）。本计划**不新增功能**，只做「方案 ↔ 代码」的对齐修复：把审计登记的偏差按优先级转成 14 个可执行任务，其中 6 条是「破坏不变量」的高优先级修复（向量路可见性过滤、认领状态列、向量批重试、embedding 错误码落库、KB 缺失卡死、SQLite 能力探测），其余是口径统一、健壮性与导出补齐。

**Tech Stack:** Python 3.11+ / asyncio / SQLite 3.35+（FTS5 + `RETURNING`）/ Chroma / pytest（`asyncio_mode = "auto"`）/ ruff / basedpyright。

---

## 0. 执行前置（每个 Worker 必读）

**必读文档**（改动前读完再写代码）：

1. `.ai-runtime-artifacts/specs/2026-11-09-knowledge-center-spec.md`（父方案，§19 是本轮的偏差索引）
2. `.ai-runtime-artifacts/specs/2026-11-09-knowledge-storage-spec.md`（§23 存储侧偏差登记 `AL-S*` / `AL-I*`）
3. `.ai-runtime-artifacts/specs/2026-11-09-knowledge-retrieval-spec.md`（§14 检索侧偏差登记 `AL-R*`）
4. `.ai-runtime-artifacts/plans/2026-11-09-knowledge-center-plan.md`（v1 原始实施计划，本计划是它的收口补丁）

**硬约束**

- 本计划只做**对齐修复**：不改检索口径、不改 schema、不改工具签名、不加 feature。
  若发现某条 `AL-*` 的正确处置与本计划不同，**停下报告**，不要自行发挥。
- 每个 Task 严格 TDD：先写失败测试 → 跑测试确认失败 → 最小实现 → 跑测试确认通过 → 提交。
- 不改用户源文件、不改 `~/.nanobot/` 下的真实数据；测试一律用 tmp 目录与假 embedder。
- 提交信息用 Angular 风格（`fix(knowledge): ...` / `test(knowledge): ...`），一次提交一个 Task。
- 已有实现是「事实基线」：本计划中「改方案」的条目**已经在方案里改完了**，只剩「改代码」的条目需要动代码。

**验证命令**（Task 14 会全量跑）

```bash
.venv/bin/python -m pytest tests/knowledge tests/tools/test_knowledge_tools.py \
  tests/cli/test_knowledge_cli.py tests/webui/test_knowledge_api.py -q
.venv/bin/python -m ruff check nanobot/knowledge nanobot/cli/knowledge.py tests/knowledge
.venv/bin/python -m basedpyright nanobot/knowledge
```

---

## 1. 偏差总表（`AL-*` → 任务）

| 优先级 | 编号 | 一句话 | 任务 |
| --- | --- | --- | --- |
| 高 | AL-R1 | 向量路回表未过滤 `documents.parse_status='ready'`，失败文档的残留旧块会从向量路漏出 | Task 1 |
| 高 | AL-S1 | `init_schema()` 无 SQLite 能力探测（FTS5 / `RETURNING`） | Task 4 |
| 高 | AL-I1 | 认领不写 `vector_status='pending'` | Task 5 |
| 高 | AL-I2 | 向量批写失败无重试 | Task 6 |
| 高 | AL-I6 | embedding 失败不落 `documents.error_code` | Task 6 |
| 高 | AL-I8 | KB 缺失时文档卡在 `processing` 直到租约过期 | Task 8 |
| 中 | AL-R2 | `mode_used` 由「哪路有候选」决定，hybrid 下误报 `semantic` | Task 2 |
| 中 | AL-R3 | 相关性带按分支 C 标定却无条件套到分支 A/B | Task 2 |
| 中 | AL-R11 | 向量路泛 `except Exception` 吞掉编程错误；`assert` 做类型收窄 | Task 3 |
| 中 | AL-I4 | 无 60 s 周期租约回收 | Task 7 |
| 中 | AL-I7 | 队列去重有洞：出队即 discard，处理中可重复入队 | Task 7 |
| 中 | AL-I9 | 改 KB 的 embedding 配置未先清旧向量/旧 collection | Task 9 |
| 中 | AL-I3 | 修复扫描只做 id 对账，`vector_status` 异常的文档不会被重投 | Task 9 |
| 中 | AL-S2 | CLI `knowledge add` 无 `max_file_mb` 校验 | Task 10 |
| 中 | AL-I11 | 远端 4xx 被归为 `EmbeddingUnavailable`（应为 `EmbeddingError`） | Task 11 |
| 中 | AL-I10 | 远端自适应维度未写回 KB 行 | Task 11 |
| 低 | AL-S4 | 分块器缺 `end_i <= end_{i+1}` 不变量 | Task 12 |
| 低 | AL-I13 | `knowledge/__init__.py` 未导出单例 | Task 13 |
| 中 | AL-T1 | `tool_output_chars` 配置从未被读取，工具层硬编码 6000 | Task 15 |
| 中 | AL-T2 | 预算检查在收尾标签之前，实际输出可超 6000 | Task 15 |
| 中 | AL-T3 | 单条 1000 字符切在转义前，转义后可超 | Task 15 |
| 中 | AL-T4 | `knowledge_read` 向模型暴露绝对 `source_path` | Task 15 |
| 中 | AL-T6 | `knowledge_read` 扩读前后不对称（焦点在文首时多给后继） | Task 15 |
| 中 | AL-T7 | WebUI 检索预览未复用工具层口径（无截断/原始码/limit 50） | Task 15 |
| 中 | AL-T8 | WebUI 丢失失败语义 → 500，无法区分「检索失败」与「空结果」 | Task 15 |
| 低 | AL-T5 | 句柄未绑定会话/轮次 | 已登记为已知差异（改方案，v2 再评估） |
| 低 | AL-T9 | `mode` 参数无 enum，两端口径不一 | Task 15 |
| 低 | AL-T10 | 工具层每条 hit 单独 `get_document`（N+1） | Task 15 |
| 低 | AL-T11 | 工具测试仍断言 `routes="k"`，实现已输出 `vector,keyword` | Task 15 |
| 低 | AL-T12 | WebUI 禁用态 `degraded=["disabled"]` 不在降级词表 | Task 15 |
| 低 | AL-T13 | `no_kb_message()` 缺「没有执行检索」字样 | Task 15 |
| 低 | AL-R4 / AL-R5 / AL-R6 / AL-R7 / AL-R8 / AL-R9 / AL-R10 | 口径澄清类 | 已改方案（Task 14 只做一致性核对） |
| 低 | AL-S3 / AL-S5 / AL-S6 | 口径澄清类 | 已改方案（Task 14 只做一致性核对） |

> 已改方案的条目（AL-R4/R5/R6/R7/R8/R9/R10、AL-S3/S5/S6、AL-I5、AL-T5）在三份 spec 里已同步，
> 本计划不再重复改文档；Task 14 只核对「文档 ↔ 代码 ↔ 测试」三者一致。
> `AL-T*` 来自工具层与 WeKnora `05-Agent检索与上下文装配原理` 的逐条对照（检索 spec §15.1）。

---

## 2. 文件地图

| 文件 | 职责 | 本计划改动 |
| --- | --- | --- |
| `nanobot/knowledge/repository.py` | KB / 文档 / 块 CRUD、FTS 查询、状态机 SQL | Task 1（新增可见块查询）、Task 5（认领 SQL） |
| `nanobot/knowledge/retriever.py` | 两路召回、融合分支、降级标注 | Task 1（回表改用可见查询）、Task 2（`mode_used`/相关性带）、Task 3（异常分类） |
| `nanobot/knowledge/fusion.py` | 纯函数层：RRF / 去重 / MMR | Task 2（相关性带按分支生效） |
| `nanobot/knowledge/database.py` | 连接、迁移、schema 初始化 | Task 4（能力探测） |
| `nanobot/knowledge/ingestor.py` | 入库编排：认领 → 抽取 → 分块 → embedding → 写库 → 写向量 | Task 6（向量重试 + 错误码）、Task 8（KB 缺失即失败） |
| `nanobot/knowledge/service.py` | 服务门面、队列、worker、恢复、修复 | Task 7（周期回收 + 队列去重）、Task 9（配置变更清向量 + 修复重投） |
| `nanobot/knowledge/embedding.py` | local / remote / none 三后端 | Task 11（4xx 分类 + 维度写回） |
| `nanobot/knowledge/chunker.py` | 切分、重叠、校验器 | Task 12（补不变量） |
| `nanobot/knowledge/__init__.py` | 包导出 | Task 13（导出单例） |
| `nanobot/cli/knowledge.py` | CLI 子命令 | Task 10（大小上限） |
| `nanobot/agent/tools/knowledge_common.py` | 工具层渲染：XML 输出、预算、降级词表 | Task 15（预算口径、降级词表、无库文案） |
| `nanobot/agent/tools/knowledge_search.py` | `knowledge_search` 工具 | Task 15（enum、N+1、预算取值） |
| `nanobot/agent/tools/knowledge_read.py` | `knowledge_read` 工具 | Task 15（路径暴露、扩读对称） |
| `nanobot/webui/knowledge_api.py` | WebUI 后端动作 | Task 15（预览口径统一、失败语义翻译） |
| `tests/knowledge/test_retriever.py` | 检索层测试 | Task 1 / 2 / 3 |
| `tests/knowledge/test_fusion.py` | 融合层测试 | Task 2 |
| `tests/knowledge/storage/test_database.py` | 连接与迁移测试 | Task 4 |
| `tests/knowledge/storage/test_repository.py` | 仓储层测试 | Task 1 / 5 |
| `tests/knowledge/test_ingestor.py` | 入库编排测试 | Task 6 / 8 |
| `tests/knowledge/test_service.py` | 服务层测试 | Task 7 / 9 |
| `tests/knowledge/test_embedding.py` | 嵌入后端测试 | Task 11 |
| `tests/knowledge/test_chunker.py` | 分块测试 | Task 12 |
| `tests/cli/test_knowledge_cli.py` | CLI 测试 | Task 10 |

---

### Task 1: 向量路回表补齐文档可见性过滤（AL-R1）

**Files:**
- Modify: `nanobot/knowledge/repository.py`（`fetch_chunk` 之后新增 `fetch_visible_chunk`）
- Modify: `nanobot/knowledge/retriever.py:361-365`（`_run_vector_route` 改用新方法）
- Test: `tests/knowledge/storage/test_repository.py`、`tests/knowledge/test_retriever.py`

- [ ] **Step 1: 写失败测试（仓储层）**

追加到 `tests/knowledge/storage/test_repository.py`（沿用文件里已有的 `repo` fixture 与
`_insert_doc` 辅助函数；块通过 `repo.commit_chunks` 写入）：

```python
def _chunk_record(chunk_id: str, content: str = "残留正文"):
    return ChunkRecord(
        row_id=0,
        id=chunk_id,
        doc_id="",
        kb_id="",
        workspace_id="",
        chunk_index=0,
        content=content,
        heading_path="",
        page=None,
        locator="",
        start_offset=0,
        end_offset=len(content),
        char_count=len(content),
        content_hash="h_failed",
    )


def test_fetch_visible_chunk_hides_failed_document(repo):
    """失败文档的残留旧块不得被检索侧回表拿到（AL-R1）。"""
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    record = _chunk_record("chk_failed_doc")
    record = replace(record, doc_id=doc.id, kb_id=kb.id, workspace_id="ws1")
    assert repo.commit_chunks(doc.id, attempt=1, chunks=[record]) is True
    assert repo.fetch_chunk("chk_failed_doc") is not None
    repo.fail_document(
        doc.id, attempt=1, error_code="X", error_message="y"
    )
    assert repo.fetch_visible_chunk("chk_failed_doc") is None


def test_fetch_visible_chunk_returns_ready_document_chunk(repo):
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    record = _chunk_record("chk_ok")
    record = replace(record, doc_id=doc.id, kb_id=kb.id, workspace_id="ws1")
    repo.commit_chunks(doc.id, attempt=1, chunks=[record])
    found = repo.fetch_visible_chunk("chk_ok")
    assert found is not None
    assert found.id == "chk_ok"
```

> `ChunkRecord` 的字段与 `dataclasses.replace` 的用法以 `nanobot/knowledge/repository.py`
> 里 `ChunkRecord` 的真实定义为准（若为 frozen dataclass 则 `replace` 可用；若为普通类则改为构造新对象）。
> `fail_document` 的参数是关键字参数（`attempt` / `error_code` / `error_message`）。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/knowledge/storage/test_repository.py -k visible_chunk -q
```

Expected: FAIL — `AttributeError: 'KnowledgeRepository' object has no attribute 'fetch_visible_chunk'`

> **同时要改测试替身**：`tests/knowledge/test_retriever.py` 的 `FakeRepo` 只有 `fetch_chunk`。
> 检索层改用 `fetch_visible_chunk` 后，`FakeRepo` 必须补上同名方法，否则 Task 1 的检索层用例会全红：
>
> ```python
> # tests/knowledge/test_retriever.py —— FakeRepo 内追加
>     def fetch_visible_chunk(self, chunk_id: str) -> StoredChunk | None:
>         self.fetch_calls.append(chunk_id)
>         return self.chunks.get(chunk_id)
> ```

- [ ] **Step 3: 写最小实现**

在 `nanobot/knowledge/repository.py` 的 `fetch_chunk` 方法之后追加：

```python
    def fetch_visible_chunk(self, chunk_id: str) -> StoredChunk | None:
        """只返回「块启用 + 文档 ready + 文档启用」的块。

        向量路的回表必须与 FTS 路同一套可见性过滤（存储 spec §13 第 3 条、
        检索 spec §4.2 / AL-R1）：失败文档（parse_status='failed'）残留的旧块
        不得只从向量路漏出。
        """
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT c.row_id, c.id, c.doc_id, c.kb_id, c.workspace_id,"
                " c.chunk_index, c.content, c.heading_path, c.page, c.locator,"
                " c.start_offset, c.end_offset, c.char_count, c.content_hash,"
                " c.is_enabled"
                " FROM document_chunks c"
                " JOIN documents d ON d.id = c.doc_id"
                " WHERE c.id=? AND c.is_enabled=1"
                " AND d.parse_status='ready' AND d.is_enabled=1",
                (chunk_id,),
            ).fetchone()
        return self._row_to_chunk(row) if row else None
```

把 `nanobot/knowledge/retriever.py` 的 `_run_vector_route` 里的回表调用改为：

```python
            for chunk_id, cosine in raw_hits:
                stored = self._repo.fetch_visible_chunk(chunk_id)
                if stored is None:
                    continue
                hits.append(_from_chunk(stored, cosine))
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/knowledge/storage/test_repository.py tests/knowledge/test_retriever.py -q
```

Expected: PASS（新增 2 个用例 + 原有用例全绿）

- [ ] **Step 5: 提交**

```bash
git add nanobot/knowledge/repository.py nanobot/knowledge/retriever.py \
  tests/knowledge/storage/test_repository.py tests/knowledge/test_retriever.py
git commit -m "fix(knowledge): 向量路回表补齐文档可见性过滤"
```

---

### Task 2: `mode_used` 语义与相关性带按分支生效（AL-R2、AL-R3）

**Files:**
- Modify: `nanobot/knowledge/fusion.py:270-291`（`mmr_rerank` 接受 `relevance_band=None` 表示不启用带）
- Modify: `nanobot/knowledge/retriever.py:237`、`:369-387`
- Test: `tests/knowledge/test_fusion.py`、`tests/knowledge/test_retriever.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/knowledge/test_fusion.py`（沿用文件里已有的 `SearchHit` 构造辅助函数；没有则用下面的完整写法）：

```python
def _hit(chunk_id: str, content: str, score: float) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        doc_id="doc_1",
        kb_id="kb_1",
        chunk_index=0,
        heading_path="",
        page=None,
        locator="",
        content=content,
        char_count=len(content),
        score=score,
    )


def test_mmr_without_band_keeps_all_candidates():
    """分支 A/B 的分数尺度与 RRF 不同，相关性带必须可关闭（AL-R3）。"""
    hits = [_hit("chk_a", "内容甲", 0.90), _hit("chk_b", "内容乙", 0.62)]
    ranked = mmr_rerank(hits, limit=2, lambda_=0.7, relevance_band=None)
    assert {hit.chunk_id for hit in ranked} == {"chk_a", "chk_b"}


def test_mmr_with_band_drops_far_related_candidate():
    hits = [_hit("chk_a", "内容甲", 1.0), _hit("chk_b", "内容乙", 0.50)]
    ranked = mmr_rerank(hits, limit=2, lambda_=0.7, relevance_band=0.15)
    assert [hit.chunk_id for hit in ranked] == ["chk_a"]
```

追加到 `tests/knowledge/test_retriever.py`（沿用文件里的 `_kb()` / `_fts()` / `_stored()` /
`_retriever()` / `FakeRepo` / `FakeVector`）：

```python
def test_hybrid_request_reports_mode_used_hybrid_even_without_keyword_hits():
    """请求 hybrid 且两路都跑通 → mode_used='hybrid'，routes 只标实际产出的路（AL-R2）。"""
    repo = FakeRepo(
        kbs=[_kb()],
        fts=[],
        chunks={"chk_v": _stored("chk_v", "delta epsilon zeta", content_hash="hv")},
    )
    vector = FakeVector(hits=[("chk_v", 0.82)])
    result = _retriever(repo, vector=vector).search(
        "alpha delta", workspace_ids=["ws1"], mode="hybrid", limit=5
    )
    assert result.mode_used == "hybrid"
    assert result.routes == ("vector",)
```

> `_stored()` 的 `content_hash` 参数名以 `tests/knowledge/test_retriever.py` 的真实签名为准
> （若为可选参数则省略）。`FakeRepo` 的字段名是 `fts_hits` / `chunks`，不是 `fts` / `vector_hits`。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/knowledge/test_fusion.py tests/knowledge/test_retriever.py -k "band or mode_used" -q
```

Expected: FAIL — `mmr_rerank()` 不接受 `relevance_band=None`；`mode_used` 仍返回 `semantic`

- [ ] **Step 3: 写最小实现**

`nanobot/knowledge/fusion.py` 的 `mmr_rerank` 签名与带计算改为：

```python
def mmr_rerank(
    hits: Sequence[SearchHit],
    *,
    limit: int,
    lambda_: float = 0.7,
    relevance_band: float | None = 0.15,
    adjacent_similarity_floor: float = 0.25,
) -> list[SearchHit]:
    """相关性带过滤 + MMR 贪心选择；展示顺序按融合分降序（spec §5.5/§5.6/§5.7）。

    `relevance_band=None` 表示不启用相关性带——0.15 是按**分支 C（RRF 归一化分）**
    的压缩分布标定的，分支 A（cosine）/B（BM25 相对分）尺度不同，直接套用没有
    标定依据（AL-R3）。
    """
    if limit <= 0 or not hits:
        return []
    ordered = sorted(hits, key=lambda hit: (-hit.score, hit.chunk_id))
    if relevance_band is None:
        candidates = list(ordered)
    else:
        cutoff = round(round(ordered[0].score, 9) - relevance_band, 9)
        candidates = [hit for hit in ordered if round(hit.score, 9) >= cutoff]
```

`nanobot/knowledge/retriever.py` 的 `_fuse` 改为返回「分支 + 分数」，由 `search` 决定 `mode_used`：

```python
    def _fuse(
        self, vector_hits: list[SearchHit], keyword_hits: list[FtsHit]
    ) -> tuple[dict[str, float], tuple[str, ...], str]:
        """§5.2 三条互斥归一化路径。

        返回 `(scores, routes, branch)`；`branch` 取值 `hybrid` / `semantic` / `keyword`，
        表示**分数口径来源**，`mode_used` 由 `search` 按「实际跑通的路」另行判定（AL-R2）。
        """
        settings = self._settings
        if vector_hits and keyword_hits:
            scores = weighted_rrf(
                [hit.chunk_id for hit in vector_hits],
                [hit.chunk_id for hit in keyword_hits],
                w_v=settings.vector_weight,
                w_k=settings.keyword_weight,
                k=settings.rrf_k,
            )
            return scores, ("vector", "keyword"), "hybrid"
        if vector_hits:
            scores = single_vector_scores([(hit.chunk_id, hit.score) for hit in vector_hits])
            return scores, ("vector",), "semantic"
        scores = single_keyword_scores(keyword_hits)
        return scores, ("keyword",), "keyword"
```

在 `search()` 中把 `scores, routes, mode_used = self._fuse(...)` 改成：

```python
        scores, routes, branch = self._fuse(vector_hits, keyword_hits)
        mode_used = self._mode_used(normalized_mode, vector_ok, keyword_ok, branch)
```

并新增：

```python
    @staticmethod
    def _mode_used(
        requested: str, vector_ok: bool, keyword_ok: bool, branch: str
    ) -> str:
        """`mode_used` = 实际跑通的路，不是「哪路有结果」（AL-R2 / 检索 spec §6）。

        - 请求 hybrid 且两路都执行成功 → `hybrid`（即使其中一路 0 命中）
        - 只有向量路可执行 → `semantic`
        - 退回关键词 → `keyword`
        """
        if requested == "keyword":
            return "keyword"
        if requested == "semantic":
            return "semantic" if vector_ok else "keyword"
        if vector_ok and keyword_ok:
            return "hybrid"
        if vector_ok:
            return "semantic"
        return "keyword"
```

同时把 MMR 调用处的 `relevance_band=settings.relevance_band` 改为按分支传参：

```python
        ranked = mmr_rerank(
            deduped,
            limit=effective_limit,
            lambda_=settings.mmr_lambda,
            relevance_band=settings.relevance_band if branch == "hybrid" else None,
            adjacent_similarity_floor=settings.adjacent_similarity_floor,
        )
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/knowledge/test_fusion.py tests/knowledge/test_retriever.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/knowledge/fusion.py nanobot/knowledge/retriever.py \
  tests/knowledge/test_fusion.py tests/knowledge/test_retriever.py
git commit -m "fix(knowledge): mode_used 按实际跑通的路判定，相关性带按分支生效"
```

---

### Task 3: 收紧向量路异常分类（AL-R11）

**Files:**
- Modify: `nanobot/knowledge/retriever.py:186-189`、`:355-356`
- Test: `tests/knowledge/test_retriever.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/knowledge/test_retriever.py`（沿用文件里的 `FakeRepo` / `FakeVector` / `_kb()` / `_retriever()`）：

```python
def test_unexpected_vector_error_is_not_swallowed_as_degradation():
    """编程错误不该伪装成降级：向量路抛 RuntimeError 必须上抛（AL-R11）。"""
    repo = FakeRepo(kbs=[_kb()], fts=[], chunks={})
    vector = FakeVector(error=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        _retriever(repo, vector=vector).search(
            "alpha", workspace_ids=["ws1"], mode="semantic", limit=5
        )
```

> `FakeVector` 已有 `error` 参数：`query()` 会把该异常原样抛出（`tests/knowledge/test_retriever.py:184-216`）。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/knowledge/test_retriever.py -k unexpected_vector -q
```

Expected: FAIL — 当前泛 `except Exception` 会把它降级成 `vector_unavailable` 并返回空结果

- [ ] **Step 3: 写最小实现**

把 `nanobot/knowledge/retriever.py` 向量路的最后一个 `except` 改为只捕获派生层异常，并去掉 `assert`：

```python
            except (EmbeddingUnavailable, EmbeddingError, VectorStoreError) as exc:
                vector_error = exc
                degraded.append("vector_unavailable")
                logger.warning("knowledge vector route failed: {}", exc)
```

并在文件顶部补 import：

```python
from nanobot.knowledge.vector import VectorStore, VectorStoreError
```

把 `_run_vector_route` 里的

```python
            query_vector = self._embedder.embed_query(query)
            assert self._vector is not None
```

改为

```python
            query_vector = self._embedder.embed_query(query)
            if self._vector is None:
                raise VectorStoreError("vector store is not configured")
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/knowledge/test_retriever.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/knowledge/retriever.py tests/knowledge/test_retriever.py
git commit -m "fix(knowledge): 向量路只捕获派生层异常，编程错误不再伪装成降级"
```

### Task 4: SQLite 能力探测（AL-S1）

**Files:**
- Modify: `nanobot/knowledge/database.py`（新增 `KnowledgeSqliteUnsupportedError` 与 `_assert_capabilities`；`init_schema` 调用）
- Test: `tests/knowledge/storage/test_database.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/knowledge/storage/test_database.py`（沿用文件里已有的 `db` fixture）：

```python
def test_assert_capabilities_rejects_sqlite_without_fts5(db, monkeypatch):
    """FTS5 不可用时必须在建表前失败，而不是建到一半报错（AL-S1）。"""
    from nanobot.knowledge import database as db_module

    original_connect = db.connect

    class _FakeConn:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args, **kwargs):
            if "sqlite_compileoption_used" in sql:
                return self._inner.execute("SELECT 0", *args, **kwargs)
            return self._inner.execute(sql, *args, **kwargs)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

    def patched_connect(*args, **kwargs):
        return _FakeConn(original_connect(*args, **kwargs))

    monkeypatch.setattr(db, "connect", patched_connect)
    with pytest.raises(db_module.KnowledgeSqliteUnsupportedError):
        db._assert_capabilities()


def test_assert_capabilities_accepts_supported_sqlite(db):
    """正常环境下探测必须通过（否则所有启动都会失败）。"""
    db._assert_capabilities()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/knowledge/storage/test_database.py -k capabilities -q
```

Expected: FAIL — `AttributeError: 'KnowledgeDatabase' object has no attribute '_assert_capabilities'`

- [ ] **Step 3: 写最小实现**

在 `nanobot/knowledge/database.py` 的 `KnowledgeSchemaTooNewError` 之后追加：

```python
class KnowledgeSqliteUnsupportedError(RuntimeError):
    """当前 SQLite 缺少知识库依赖的能力（FTS5 / RETURNING）。"""
```

并把 `init_schema` 改为先探测再迁移：

```python
    def init_schema(self) -> None:
        self._assert_capabilities()
        with self.connect(immediate=True) as conn:
            ...

    def _assert_capabilities(self) -> None:
        """启动即校验 SQLite 能力：不满足就明确失败，不建到一半才报错。

        需要 FTS5（3.9+，external content + detail=column）与 `RETURNING`（3.35+，
        `claim_document` 依赖它）。
        """
        with self.connect() as conn:
            row = conn.execute(
                "SELECT sqlite_compileoption_used('ENABLE_FTS5')"
            ).fetchone()
            fts5 = bool(row[0]) if row else False
            if not fts5:
                raise KnowledgeSqliteUnsupportedError(
                    "当前 SQLite 未启用 FTS5，无法创建知识库；"
                    f"sqlite_version={sqlite3.sqlite_version}"
                )
            try:
                conn.execute("SELECT 1 AS x LIMIT 1 RETURNING x")
            except sqlite3.OperationalError as exc:
                raise KnowledgeSqliteUnsupportedError(
                    "当前 SQLite 不支持 RETURNING（需 3.35+）；"
                    f"sqlite_version={sqlite3.sqlite_version}"
                ) from exc
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/knowledge/storage/test_database.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/knowledge/database.py tests/knowledge/storage/test_database.py
git commit -m "fix(knowledge): 启动时探测 SQLite 的 FTS5 与 RETURNING 能力"
```

### Task 5: 认领时置 `vector_status='pending'`（AL-I1）

**Files:**
- Modify: `nanobot/knowledge/repository.py:43-51`（`_CLAIM_SQL`）
- Test: `tests/knowledge/storage/test_repository.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/knowledge/storage/test_repository.py`（沿用文件里的 `repo` fixture 与 `_insert_doc` 辅助函数）：

```python
def test_claim_resets_vector_status_to_pending(repo):
    """重新认领失败文档时，vector_status 必须回到 pending（AL-I1）。

    修复扫描按 `vector_status IN ('pending','failed','partial')` 判断「向量待写」，
    认领不重置就会让重试中的文档带着旧状态被扫描。
    """
    kb = repo.create_kb(name="a", workspace_id="ws1")
    doc = _insert_doc(repo, kb)
    repo.claim_document(doc.id, lease_owner="w1")
    repo.commit_document(
        doc.id, attempt=1, chunk_count=1, char_count=5,
        index_version="norm1:tok1:chunk1", vector_status="failed",
    )
    repo.fail_document(doc.id, attempt=1, error_code="X", error_message="y")
    assert repo.get_document(doc.id).vector_status == "failed"
    assert repo.claim_document(doc.id, lease_owner="w2") == 2
    assert repo.get_document(doc.id).vector_status == "pending"
```

> `insert_document` 的默认 `vector_status` 本来就是 `pending`（见 `test_insert_document_defaults`），
> 所以这个用例必须先把它改成 `failed` 再重新认领，才能观察到差异。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/knowledge/storage/test_repository.py -k resets_vector_status -q
```

Expected: FAIL — 重新认领后 `vector_status` 仍是 `failed`

- [ ] **Step 3: 写最小实现**

把 `nanobot/knowledge/repository.py` 的 `_CLAIM_SQL` 改为：

```python
_CLAIM_SQL = (
    "UPDATE documents"
    " SET parse_status='processing', attempt=attempt+1, lease_owner=?,"
    " lease_expires_at=?, error_code='', error_message='',"
    " vector_status='pending', updated_at=?"
    " WHERE id=?"
    " AND (parse_status IN ('pending','failed')"
    " OR (parse_status='processing' AND lease_expires_at < ?))"
    " RETURNING attempt"
)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/knowledge/storage/test_repository.py tests/knowledge/test_ingestor.py -q
```

Expected: PASS（入库结束时 ingestor 会把 `vector_status` 改写为 `ok`/`failed`/`skipped`/`partial`，
认领阶段的 `pending` 是中间态，不影响终态断言）

- [ ] **Step 5: 提交**

```bash
git add nanobot/knowledge/repository.py tests/knowledge/storage/test_repository.py
git commit -m "fix(knowledge): 认领文档时把 vector_status 置为 pending"
```

### Task 6: 向量批写重试 + embedding 失败错误码落库（AL-I2、AL-I6）

**Files:**
- Modify: `nanobot/knowledge/ingestor.py`（`_write_vectors` 增加重试；`_embed` 失败路径落 error_code）
- Test: `tests/knowledge/test_ingestor.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/knowledge/test_ingestor.py`（沿用文件里的 `_prepare()` / `_ingestor()` / `FakeVector` / `FakeEmbedder`）：

```python
def test_vector_batch_failure_is_retried_three_times(tmp_path, repo, monkeypatch):
    """批写失败先重试 3 次（AL-I2）。"""
    _kb, doc, _attempt = _prepare(tmp_path, repo, "正文内容")
    vector = FakeVector(fail=True)
    ingestor = _ingestor(repo, vector=vector)
    monkeypatch.setattr(ingestor, "_sleep", lambda _seconds: None, raising=False)
    ingestor.process_document(doc.id)
    # FakeVector.calls 统计 upsert 调用次数；未加重试时为 1
    assert vector.calls == 3
    assert repo.get_document(doc.id).vector_status == "failed"


def test_embedding_failure_records_error_code(tmp_path, repo):
    """embedding 失败必须落 documents.error_code（AL-I6）。"""
    _kb, doc, _attempt = _prepare(tmp_path, repo, "正文内容")
    ingestor = _ingestor(repo, embedder=FakeEmbedder(fail=True))
    ingestor.process_document(doc.id)
    document = repo.get_document(doc.id)
    assert document is not None
    assert document.error_code == "KNOWLEDGE_EMBEDDING_FAILED"
    assert document.parse_status == "ready"
```

> `FakeVector(fail=True)` 的 `upsert()` 每次调用都抛 `VectorStoreError` 并把 `calls` 加一，
> 因此 `vector.calls == 3` 精确表达「每批最多 3 次尝试」。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/knowledge/test_ingestor.py -k "retried_three or records_error_code" -q
```

Expected: FAIL — `vector.calls == 1`（无重试）；`error_code` 为空

- [ ] **Step 3: 写最小实现**

`nanobot/knowledge/ingestor.py` 的 `_write_vectors` 改为带重试：

```python
    def _write_vectors(
        self, doc_id: str, records: list[ChunkRecord], vectors: list[list[float]]
    ) -> str:
        """向量批写：每批最多 3 次指数退避重试；仍失败记 partial/failed（AL-I2）。"""
        vector = self._vector
        if vector is None:
            return "failed"
        written = 0
        for start in range(0, len(records), self._batch_size):
            batch = records[start : start + self._batch_size]
            batch_vectors = vectors[start : start + self._batch_size]
            if self._upsert_with_retry(vector, batch, batch_vectors):
                written += len(batch)
            else:
                break
        if written == len(records):
            return "ok"
        return "partial" if written else "failed"

    def _upsert_with_retry(
        self,
        vector: VectorStore,
        batch: list[ChunkRecord],
        batch_vectors: list[list[float]],
    ) -> bool:
        """单批 upsert 的重试包装：1s / 2s / 4s 退避，共 3 次尝试。"""
        for attempt in range(3):
            try:
                vector.upsert(batch, batch_vectors)
                return True
            except VectorStoreError as exc:
                logger.warning(
                    "knowledge vector batch failed: attempt={} error={}",
                    attempt + 1,
                    exc,
                )
                if attempt < 2:
                    self._sleep(2**attempt)
        return False

    def _sleep(self, seconds: float) -> None:
        """可替换的休眠点（测试里替换掉，避免真实等待）。"""
        time.sleep(seconds)
```

并在 `nanobot/knowledge/ingestor.py` 顶部补 `import time`。

在 `_embed` 的 `EmbeddingError` 分支落错误码（`EmbeddingUnavailable` 不算失败，只算后端不可用）：

```python
        except EmbeddingError as exc:
            logger.warning(
                "knowledge {}: doc={} error={}", EMBEDDING_FAILED, document.id, exc
            )
            self._fail(
                document.id,
                attempt,
                EMBEDDING_FAILED,
                "嵌入向量生成失败：文档已入库，但向量检索不可用",
            )
            return [], "failed"
```

> `_embed` 需要拿到当前 attempt：在 `_embed` 的签名上增加 `attempt: int` 参数，
> 调用点（`_process` 内）传入已有的 `attempt` 变量。

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/knowledge/test_ingestor.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/knowledge/ingestor.py tests/knowledge/test_ingestor.py
git commit -m "fix(knowledge): 向量批写加重试，embedding 失败落错误码"
```

### Task 7: 周期租约回收 + 队列去重（AL-I4、AL-I7）

**Files:**
- Modify: `nanobot/knowledge/service.py`（`start` 增加回收循环；`_queued` / `_processing` 分离）
- Test: `tests/knowledge/test_service.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/knowledge/test_service.py`（沿用文件里的 `_build_service()`；文件已启用
`asyncio_mode = "auto"`，异步用例可直接写 `async def`）：

```python
async def test_lease_recovery_runs_periodically(tmp_path, monkeypatch):
    """过期租约必须周期回收，不只靠启动时那一次（AL-I4）。"""
    service, repo = _build_service(tmp_path)
    calls: list[int] = []
    monkeypatch.setattr(repo, "reset_expired_leases", lambda: calls.append(1) or [])
    service._recover_interval_seconds = 0.01
    await service.start()
    try:
        await asyncio.sleep(0.05)
    finally:
        await service.stop()
    assert calls


async def test_document_being_processed_is_not_enqueued_twice(tmp_path):
    """处理中的文档不得被重复入队（AL-I7）。"""
    service, _repo = _build_service(tmp_path)
    await service.start()
    try:
        service._processing.add("doc_1")
        service.enqueue_document("doc_1")
        assert "doc_1" not in service._queued
    finally:
        await service.stop()
```

> `enqueue_document` 在没有运行中的 worker 时会直接返回，所以这个用例必须先 `await service.start()`。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/knowledge/test_service.py -k "periodically or twice" -q
```

Expected: FAIL — 没有周期回收任务；`service._processing` 属性不存在

- [ ] **Step 3: 写最小实现**

在 `nanobot/knowledge/service.py` 的 `__init__` 中新增状态与配置：

```python
        self._processing: set[str] = set()
        self._recover_interval_seconds = 60.0
        self._recover_task: asyncio.Task[None] | None = None
```

`enqueue_document` 改为：

```python
    def enqueue_document(self, doc_id: str) -> None:
        """线程安全入队；没有运行中的 worker 时只依赖 DB 的 `pending` 状态。

        已在队列或**正在处理中**的文档不再入队（AL-I7）——否则同一文档会被并发处理，
        靠 attempt 守卫互相回滚。
        """
        loop = self._loop
        if loop is None or loop.is_closed() or self._worker is None:
            return
        with self._queued_lock:
            if doc_id in self._queued or doc_id in self._processing:
                return
            self._queued.add(doc_id)
        try:
            loop.call_soon_threadsafe(self._queue.put_nowait, doc_id)
        except RuntimeError as exc:
            with self._queued_lock:
                self._queued.discard(doc_id)
            logger.debug("knowledge enqueue skipped: doc={} error={}", doc_id, exc)
```

在处理入口持有 `_processing`（`_process_queued` 或等价位置）：

```python
    def _process_queued(self, doc_id: str) -> None:
        with self._queued_lock:
            self._queued.discard(doc_id)
        self._processing.add(doc_id)
        try:
            self._ingestor.process_document(doc_id)
        finally:
            self._processing.discard(doc_id)
```

新增周期回收：

```python
    def _start_recovery_loop(self) -> None:
        """每 `_recover_interval_seconds` 秒回收一次过期租约（AL-I4）。"""
        if self._recover_task is not None:
            return

        async def _loop() -> None:
            while True:
                await asyncio.sleep(self._recover_interval_seconds)
                try:
                    self._recover_pending()
                except Exception as exc:  # noqa: BLE001 - 回收失败不应中断循环
                    logger.warning("knowledge lease recovery failed: {}", exc)

        self._recover_task = asyncio.create_task(_loop(), name="knowledge-lease-recovery")

    def _stop_recovery_loop(self) -> None:
        if self._recover_task is None:
            return
        self._recover_task.cancel()
        self._recover_task = None
```

在 `start()` 里调用 `self._start_recovery_loop()`，在 `stop()` 里调用 `self._stop_recovery_loop()`。

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/knowledge/test_service.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/knowledge/service.py tests/knowledge/test_service.py
git commit -m "fix(knowledge): 周期回收过期租约，处理中文档不再重复入队"
```

### Task 8: KB 缺失时立即失败（AL-I8）

**Files:**
- Modify: `nanobot/knowledge/ingestor.py:107-110`
- Test: `tests/knowledge/test_ingestor.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/knowledge/test_ingestor.py`：

```python
def test_missing_kb_fails_document_immediately(ingestor, monkeypatch):
    """KB 缺失是不可恢复错误：文档必须立刻 failed，而不是卡在 processing（AL-I8）。"""
    monkeypatch.setattr(ingestor._repo, "get_kb", lambda kb_id: None)
    ingestor.process_document("doc_1", attempt=1)
    document = ingestor._repo.get_document("doc_1")
    assert document is not None
    assert document.parse_status == "failed"
    assert document.error_code == "KNOWLEDGE_KB_MISSING"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/knowledge/test_ingestor.py -k missing_kb -q
```

Expected: FAIL — 文档仍停在 `processing`

- [ ] **Step 3: 写最小实现**

在 `nanobot/knowledge/ingestor.py` 的常量区追加：

```python
KB_MISSING = "KNOWLEDGE_KB_MISSING"
```

把 `_process` 中的

```python
        kb = self._repo.get_kb(document.kb_id)
        if kb is None:
            return
```

改为：

```python
        kb = self._repo.get_kb(document.kb_id)
        if kb is None:
            self._fail(
                doc_id,
                attempt,
                KB_MISSING,
                "知识库不存在或已被删除：请重新创建知识库后重试",
            )
            return
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/knowledge/test_ingestor.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/knowledge/ingestor.py tests/knowledge/test_ingestor.py
git commit -m "fix(knowledge): 知识库缺失时立即把文档置为失败"
```

---

### Task 9: 改 embedding 配置先清旧向量 + 修复扫描补重投（AL-I9、AL-I3）

**Files:**
- Modify: `nanobot/knowledge/service.py:307-308`（`update_kb`）、`:727-748`（`_repair_vectors`）
- Modify: `nanobot/knowledge/repository.py`（新增 `list_documents_needing_vector_rebuild`）
- Test: `tests/knowledge/test_service.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/knowledge/test_service.py`（沿用文件里的 `_build_service()` 与 `FakeVector`；
`FakeVector.deleted` 记录被删的 doc/kb id）：

```python
def test_update_kb_embedding_clears_old_vectors_first(tmp_path):
    """改 embedding 配置必须先删旧向量，再更新 KB 行（AL-I9）。"""
    vector = FakeVector()
    service, _repo = _build_service(tmp_path, vector_store=vector)
    kb = service.create_kb(name="手册", workspace_id="ws1")
    service.update_kb(
        kb.id,
        embedding_backend="remote",
        embedding_model="m2",
        embedding_dimensions=8,
    )
    assert kb.id in vector.deleted
    assert service.get_kb(kb.id).embedding_model == "m2"


def test_repair_requeues_documents_with_failed_vector_status(tmp_path):
    """vector_status=failed 的文档必须被重投，而不只是做 id 对账（AL-I3）。"""
    service, repo = _build_service(tmp_path)
    kb = service.create_kb(name="手册", workspace_id="ws1")
    document = repo.insert_document(
        kb_id=kb.id,
        workspace_id="ws1",
        title="a",
        source_kind="external",
        source_path="/tmp/a.md",
        file_hash="h1",
    )
    repo.claim_document(document.id, lease_owner="w1")
    repo.commit_document(
        document.id, attempt=1, chunk_count=0, char_count=0,
        index_version="norm1:tok1:chunk1", vector_status="failed",
    )
    service.repair(workspace_ids=["ws1"])
    assert document.id in service._queued
```

> `service.repair()` 的参数以 `nanobot/knowledge/service.py:564` 的真实签名为准
> （若为 `repair(*, workspace_ids=None, ...)` 则按实际传参）；`repo.insert_document` 的必填参数
> 以 `tests/knowledge/storage/test_repository.py::_insert_doc` 为准。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/knowledge/test_service.py -k "clears_old_vectors or requeues" -q
```

Expected: FAIL — `update_kb` 不删旧向量；`repair` 不重投 failed 文档

- [ ] **Step 3: 写最小实现**

`nanobot/knowledge/service.py` 的 `update_kb` 改为先清向量：

```python
    def update_kb(self, kb_id: str, **fields: Any) -> KnowledgeBase:
        """更新知识库；改 embedding 配置时先删旧向量，再更新行（AL-I9）。

        顺序不能反：旧 collection 里的向量与新身份（backend/model/dimensions）
        不对应，留着就是脏召回（存储 spec §8.1）。
        """
        current = self.get_kb(kb_id)
        if current is None:
            raise KeyError(kb_id)
        embedding_changed = any(
            key in fields
            for key in ("embedding_backend", "embedding_model", "embedding_dimensions")
        )
        if embedding_changed:
            self._delete_kb_vectors(kb_id)
        return self._repo.update_kb(kb_id, **fields)
```

`_repair_vectors` 之后补「异常状态文档重投」：

```python
    def _requeue_failed_vector_documents(self, result: dict[str, Any]) -> None:
        """把 vector_status 异常的文档重新入队（AL-I3）。

        id 对账只能发现「集合不一致」，发现不了「id 相同但内容过期」；
        因此凡是 ready 且 vector_status ∈ (pending, failed, partial) 的文档都要重投。
        """
        doc_ids = self._repo.list_documents_needing_vector_rebuild()
        for doc_id in doc_ids:
            self.enqueue_document(doc_id)
        result["vector_requeued"] = len(doc_ids)
```

在 `repair()` 里调用 `self._requeue_failed_vector_documents(result)`。

并在 `nanobot/knowledge/repository.py` 新增：

```python
    def list_documents_needing_vector_rebuild(self) -> list[str]:
        """需要重投向量的文档：ready 且 vector_status ∈ (pending, failed, partial)。"""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM documents"
                " WHERE parse_status='ready'"
                " AND vector_status IN ('pending','failed','partial')"
                " ORDER BY id"
            ).fetchall()
        return [str(row["id"]) for row in rows]
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/knowledge/test_service.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/knowledge/service.py nanobot/knowledge/repository.py tests/knowledge/test_service.py
git commit -m "fix(knowledge): 改 embedding 配置先清旧向量，修复扫描重投异常状态文档"
```

### Task 10: CLI 文件大小上限（AL-S2）

**Files:**
- Modify: `nanobot/cli/knowledge.py`（新增 `_max_file_bytes()`；`add_file` 命令校验）
- Test: `tests/cli/test_knowledge_cli.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/cli/test_knowledge_cli.py`（沿用文件里的 `_run()` 与 `FakeService`）：

```python
def test_add_rejects_oversized_file(monkeypatch, tmp_path):
    """超过上限的文件必须在入库前被拒（AL-S2）。"""
    source = tmp_path / "big.md"
    source.write_text("内容" * 100, encoding="utf-8")
    monkeypatch.setattr(cli, "_max_file_bytes", lambda config=None: 10)
    result, service = _run(monkeypatch, ["add", str(source), "--kb", "kb_1"])
    assert result.exit_code == 2
    assert service.add_calls == []
```

> `_run()` 会把 `cli._load_config` 换成 `object()`，所以上限必须通过
> `cli._max_file_bytes(config)` 这个**独立函数**取，并且对缺字段的 config 有默认值，
> 否则既有 CLI 用例会因 `object()` 没有 `knowledge` 属性而崩。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/cli/test_knowledge_cli.py -k oversized -q
```

Expected: FAIL — 当前没有这个拦截（`_max_file_bytes` 还不存在，monkeypatch 会报错）

- [ ] **Step 3: 写最小实现**

在 `nanobot/cli/knowledge.py` 的 `_service()` 之后新增：

```python
def _max_file_bytes(config: str | None = None) -> int:
    """入库文件大小上限：min(knowledge.chunking.max_file_mb, 50) MB。

    配置缺失或字段缺失时回退 50 MB（与 `KnowledgeChunkingConfig.max_file_mb` 默认一致）。
    """
    try:
        loaded = _load_config(config)
    except Exception:  # noqa: BLE001 - 配置不可用时用默认上限
        return 50 * 1024 * 1024
    knowledge = getattr(loaded, "knowledge", None)
    chunking = getattr(knowledge, "chunking", None)
    megabytes = int(getattr(chunking, "max_file_mb", 50))
    return min(megabytes, 50) * 1024 * 1024
```

在 `add_file` 命令里，`if not path.is_file():` 之后加：

```python
    limit = _max_file_bytes(config)
    size = path.stat().st_size
    if size > limit:
        _abort(f"文件过大：{size} 字节，上限 {limit} 字节（knowledge.chunking.max_file_mb）")
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/cli/test_knowledge_cli.py -q
```

Expected: PASS（既有 CLI 用例继续通过：`_load_config` 被替换时走默认 50 MB 分支）

- [ ] **Step 5: 提交**

```bash
git add nanobot/cli/knowledge.py tests/cli/test_knowledge_cli.py
git commit -m "fix(knowledge): CLI 入库前校验文件大小上限"
```

### Task 11: 远端嵌入的 4xx 分类与维度写回（AL-I11、AL-I10）

**Files:**
- Modify: `nanobot/knowledge/embedding.py`（`RemoteEmbedder._request` 状态码分支）
- Modify: `nanobot/knowledge/ingestor.py`（维度自适应后回写 KB 行）
- Test: `tests/knowledge/test_embedding.py`（**改现有用例**）、`tests/knowledge/test_ingestor.py`

- [ ] **Step 1: 改现有用例并补失败测试**

**改现有用例**：`tests/knowledge/test_embedding.py:106` 的 `test_remote_4xx_is_not_retried`
当前断言 `pytest.raises(EmbeddingUnavailable)`——4xx 是请求错误而不是「后端不可用」，
断言要改成 `EmbeddingError`（AL-I11）：

```python
def test_remote_4xx_is_not_retried():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(400, json={"error": "bad"})

    embedder = RemoteEmbedder(
        base_url="https://example.test/v1",
        model="m",
        dimensions=2,
        api_key="k",
        api_key_env="",
        batch_size=8,
        timeout_seconds=5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    with pytest.raises(EmbeddingError):
        embedder.embed_query("q")
    assert attempts["n"] == 1
```

**补新用例**（同一文件）：

```python
def test_remote_exposes_resolved_dimensions():
    """自适应维度必须可被调用方读到并写回 KB 行（AL-I10）。"""
    def handler(request):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]})

    embedder = RemoteEmbedder(
        base_url="https://example.test/v1",
        model="m",
        dimensions=0,
        api_key="k",
        api_key_env="",
        batch_size=8,
        timeout_seconds=5,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    embedder.embed_query("q")
    assert embedder.resolved_dimensions == 3
```

**补入库侧用例**（`tests/knowledge/test_ingestor.py`）：

```python
def test_adaptive_dimension_is_written_back_to_kb(tmp_path, repo):
    """KB 未记录维度时，入库后必须把实际维度写回 KB 行（AL-I10）。"""
    kb, doc, _attempt = _prepare(tmp_path, repo, "正文内容")
    repo.update_kb(kb.id, embedding_dimensions=0)
    embedder = FakeEmbedder(dims=2)
    ingestor = _ingestor(repo, embedder=embedder)
    ingestor.process_document(doc.id)
    assert repo.get_kb(kb.id).embedding_dimensions == 2
```

> `FakeEmbedder(dims=2)` 的 `identity` 是 `("local", "m", 2)`，因此 embedder 侧的
> `resolved_dimensions` 可直接取 `identity[2]`。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/knowledge/test_embedding.py tests/knowledge/test_ingestor.py -k "4xx or resolved_dimensions or adaptive_dimension" -q
```

Expected: FAIL — 4xx 仍抛 `EmbeddingUnavailable`；`resolved_dimensions` 属性不存在；KB 维度未写回

- [ ] **Step 3: 写最小实现**

`nanobot/knowledge/embedding.py`：`RemoteEmbedder` 增加属性与 4xx 分类。

```python
class RemoteEmbedder:
    def __init__(self, ...):
        ...
        #: 实际返回的向量维度；dimensions=0 时自适应（AL-I10），由调用方写回 KB 行
        self.resolved_dimensions: int = dimensions
```

`_request` 的状态码分支改为：

```python
        if response.status_code == 429 or response.status_code >= 500:
            raise EmbeddingError(
                f"embedding endpoint retryable error: {response.status_code}"
            )
        if response.status_code >= 400:
            # 4xx 是请求错误（参数/鉴权/模型名），不是「后端不可用」（AL-I11）
            raise EmbeddingError(
                f"embedding endpoint rejected request: {response.status_code}"
            )
```

解析响应时更新维度：

```python
    def _parse(self, response: httpx.Response, expected_count: int) -> list[list[float]]:
        payload = response.json()
        data = payload.get("data") or []
        ...
        vectors = [self._validated_vector(item["embedding"], context="embedding response")
                   for item in sorted(data, key=lambda item: item.get("index", 0))]
        if vectors:
            self.resolved_dimensions = len(vectors[0])
        return vectors
```

`nanobot/knowledge/ingestor.py`：入库时把自适应维度写回 KB 行（**embedder 不碰数据库**，
写回由入库侧完成）：

```python
        # 提交前把自适应维度写回 KB 行（AL-I10）：否则检索期身份比对必然 mismatch
        kb_row = self._repo.get_kb(document.kb_id)
        if kb_row is not None and kb_row.embedding_dimensions == 0:
            resolved = getattr(self._embedder, "resolved_dimensions", None)
            if resolved:
                self._repo.update_kb(kb_row.id, embedding_dimensions=int(resolved))
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/knowledge/test_embedding.py tests/knowledge/test_ingestor.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/knowledge/embedding.py nanobot/knowledge/ingestor.py   tests/knowledge/test_embedding.py tests/knowledge/test_ingestor.py
git commit -m "fix(knowledge): 远端 4xx 归为请求错误，自适应维度写回知识库"
```

### Task 12: 分块器补 `end_i <= end_{i+1}` 不变量（AL-S4）

**Files:**
- Modify: `nanobot/knowledge/chunker.py`（`validate_chunks`）
- Test: `tests/knowledge/test_chunker.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/knowledge/test_chunker.py`（沿用文件里的 `lines()` 辅助函数与
`ChunkDraft` / `validate_chunks`）：

```python
def test_validate_chunks_rejects_non_monotonic_end():
    """块尾必须单调不降：end_i <= end_{i+1}（AL-S4 / 父方案 §7.1）。"""
    text = "第一段。\n\n第二段。"
    drafts = [
        ChunkDraft(
            content="第一段。",
            heading_path="",
            page=None,
            locator="",
            start_offset=0,
            end_offset=4,
            char_count=4,
            chunk_index=0,
        ),
        ChunkDraft(
            content="第二段。",
            heading_path="",
            page=None,
            locator="",
            start_offset=3,
            end_offset=3,
            char_count=4,
            chunk_index=1,
        ),
    ]
    with pytest.raises(KnowledgeChunkError):
        validate_chunks(drafts, chunk_size=800, chunk_overlap=120, text=text)
```

> `ChunkDraft` 的字段以 `nanobot/knowledge/chunker.py` 的真实定义为准（若字段名不同，
> 按实际构造；关键是造出「后一块的 end 小于前一块的 end」这一情形）。
> 第二个 draft 的 `end_offset=3 < 4`，因此 `end` 逆序。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/knowledge/test_chunker.py -k non_monotonic_end -q
```

Expected: FAIL — `validate_chunks` 只校验 `start` 单调与重叠长度，不校验 `end` 单调

- [ ] **Step 3: 写最小实现**

在 `nanobot/knowledge/chunker.py` 的 `validate_chunks` 里，与 `non-monotonic start` 相邻的位置追加：

```python
            if previous.end_offset > draft.end_offset:
                raise KnowledgeChunkError(
                    "KNOWLEDGE_CHUNK_INVALID", "non-monotonic end"
                )
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/knowledge/test_chunker.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/knowledge/chunker.py tests/knowledge/test_chunker.py
git commit -m "fix(knowledge): 分块器补块尾单调不降不变量"
```

### Task 13: 导出知识服务单例（AL-I13）

**Files:**
- Modify: `nanobot/knowledge/__init__.py`
- Test: `tests/knowledge/test_service.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/knowledge/test_service.py`：

```python
def test_package_exports_service_singletons():
    """包级导出 get_knowledge_service / reset_knowledge_service（AL-I13）。"""
    import nanobot.knowledge as knowledge_pkg

    assert hasattr(knowledge_pkg, "get_knowledge_service")
    assert hasattr(knowledge_pkg, "reset_knowledge_service")
    assert "get_knowledge_service" in knowledge_pkg.__all__
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/knowledge/test_service.py -k exports_service -q
```

Expected: FAIL — `AttributeError: module 'nanobot.knowledge' has no attribute 'get_knowledge_service'`

- [ ] **Step 3: 写最小实现**

把 `nanobot/knowledge/__init__.py` 改为：

```python
"""知序知识中心：文档入库、混合检索与 RAG 工具。"""

from nanobot.knowledge.service import get_knowledge_service, reset_knowledge_service

__all__ = ["get_knowledge_service", "reset_knowledge_service"]
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/knowledge -q
```

Expected: PASS（注意导入环：`service.py` 若反向 import `nanobot.knowledge`，需改成
`from nanobot.knowledge.service import ...` 的模块级导入或延迟导入）

- [ ] **Step 5: 提交**

```bash
git add nanobot/knowledge/__init__.py tests/knowledge/test_service.py
git commit -m "feat(knowledge): 包级导出知识服务单例"
```

---

### Task 14: 一致性核对与验收收口

**Files:**
- Verify only（不改代码；若核对发现方案与代码仍有出入，先改方案再改代码）

- [ ] **Step 1: 跑全量测试**

```bash
.venv/bin/python -m pytest tests/knowledge tests/tools/test_knowledge_tools.py \
  tests/cli/test_knowledge_cli.py tests/webui/test_knowledge_api.py -q
```

Expected: 全部 PASS（Task 1 前为 468 passed，之后用例数只增不减）

- [ ] **Step 2: 跑 lint 与类型检查**

```bash
.venv/bin/python -m ruff check nanobot/knowledge nanobot/cli/knowledge.py tests/knowledge
.venv/bin/python -m basedpyright nanobot/knowledge
```

Expected: 两者均无报错

- [ ] **Step 3: 核对「方案 ↔ 代码 ↔ 测试」三者一致**

逐条打开并确认（用 `grep -n` 定位）：

```bash
grep -n "fetch_visible_chunk" nanobot/knowledge/repository.py nanobot/knowledge/retriever.py
grep -n "vector_status='pending'" nanobot/knowledge/repository.py
grep -n "relevance_band" nanobot/knowledge/fusion.py nanobot/knowledge/retriever.py
grep -n "_mode_used" nanobot/knowledge/retriever.py
grep -n "KnowledgeSqliteUnsupportedError" nanobot/knowledge/database.py
grep -n "_upsert_with_retry" nanobot/knowledge/ingestor.py
grep -n "KB_MISSING" nanobot/knowledge/ingestor.py
grep -n "list_documents_needing_vector_rebuild" nanobot/knowledge/repository.py
grep -n "max_file_mb" nanobot/cli/knowledge.py
grep -n "resolved_dimensions" nanobot/knowledge/embedding.py nanobot/knowledge/ingestor.py
```

每条都必须同时满足：代码里有实现、spec 里有对应条款、测试里有对应用例。
若某条 spec 条款与代码仍不一致，**先改 spec**（在对应 spec 的 `AL-*` 表里标记「已修复」），
再改代码，再补测试。

- [ ] **Step 4: 跑 golden queries 回归并记录指标**

```bash
.venv/bin/python -m pytest tests/knowledge/test_golden_queries.py -q -s
```

Expected: PASS；把 Recall@5 / MRR@10 / exact-hit 的实测值抄进
`.ai-runtime-artifacts/verifications/2026-11-10-knowledge-alignment/verification.md`
（新建目录与文件，格式参照 `2026-11-09-knowledge-retrieval-fusion/verification.md`）

- [ ] **Step 5: 跑 WebUI 测试与构建**

```bash
cd webui && bun run test && bun run build
```

Expected: 测试通过、构建成功（构建产物写入 `nanobot/web/dist`）

- [ ] **Step 6: 提交验收记录**

```bash
git add .ai-runtime-artifacts/verifications/2026-11-10-knowledge-alignment/
git commit -m "docs(knowledge): 记录实现对齐收口的验收结果"
```

---

### Task 15: 工具层与 WeKnora `05` 契约对齐（AL-T1～AL-T4、AL-T6～AL-T13）

**Files:**
- Modify: `nanobot/agent/tools/knowledge_common.py`（预算口径、降级词表、无库文案）
- Modify: `nanobot/agent/tools/knowledge_search.py`（schema enum、标题批量取、预算取值）
- Modify: `nanobot/agent/tools/knowledge_read.py`（不暴露绝对路径、扩读对称）
- Modify: `nanobot/webui/knowledge_api.py`（预览复用工具层渲染、失败语义翻译）
- Test: `tests/tools/test_knowledge_tools.py`、`tests/webui/test_knowledge_api.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/tools/test_knowledge_tools.py`：

```python
def test_output_budget_counts_closing_tag():
    """收尾标签必须计入预算，实际输出不得超限（AL-T2）。"""
    result = make_result(limit=20)
    output = render_search_result(result, mode_requested="hybrid", total=600)
    assert len(output) <= 600


def test_no_kb_message_states_search_not_executed():
    """无库文案必须与「没有执行检索」口径一致（AL-T13）。"""
    assert "没有执行检索" in no_kb_message()


def test_read_tool_hides_absolute_source_path(tmp_path, kb):
    """knowledge_read 不得向模型暴露绝对路径（AL-T4）。"""
    ...


def test_read_context_is_symmetric_around_focus(kb, doc_with_three_chunks):
    """扩读必须是「前后各 context 个」（AL-T6）。"""
    ...
```

> 现有测试文件已有一组 `make_result` / `render_search_result` 风格的 helper，
> 新用例复用它们；`no_kb_message` 等函数名以 `nanobot/agent/tools/knowledge_common.py` 的实际导出为准。

追加到 `tests/webui/test_knowledge_api.py`：

```python
def test_search_preview_reports_failure_distinctly(webui_client):
    """检索失败必须是明确错误，不能落成 500「服务不可用」（AL-T8）。"""
    ...
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/tools/test_knowledge_tools.py tests/webui/test_knowledge_api.py -q
```

Expected: FAIL — 预算超限、无库文案缺关键词、WebUI 失败落 500

- [ ] **Step 3: 写最小实现**

`nanobot/agent/tools/knowledge_common.py`：

```python
#: 一次检索工具输出的总预算；由 knowledge.retrieval.tool_output_chars 覆盖（AL-T1）
DEFAULT_TOOL_OUTPUT_CHARS = 6000

#: WebUI 禁用态等非检索降级标记的中文说明（AL-T12）
_DEGRADED_NOTES["disabled"] = "知识库功能已禁用，未执行检索。"
```

预算计算把收尾标签计入（AL-T2、AL-T3）：

```python
    closing = "</knowledge_results>"
    budget = max(total - len(closing), 0)
    ...
    for index, block in enumerate(blocks):
        if used + len(block) > budget:
            omitted = len(blocks) - index
            break
        lines.append(block)
        used += len(block)

    if omitted:
        marker = f'<omitted count="{omitted}" reason="budget"/>'
        if used + len(marker) > budget:
            marker = '<omitted count="1" reason="budget"/>'
        lines.append(marker)
        used += len(marker)
    lines.append(closing)
```

单条截断改为**按转义后长度**判定（AL-T3）：

```python
        content = hit.content or ""
        escaped = escape_xml(content)
        truncated = len(escaped) > per_item
        if truncated:
            escaped = escaped[:per_item]
            truncated_any = True
```

`no_kb_message()` 补字样（AL-T13）：

```python
def no_kb_message() -> str:
    return (
        "当前没有可用的知识库，没有执行检索。"
        "这不是检索失败——请先创建知识库并导入文档。"
    )
```

`nanobot/agent/tools/knowledge_search.py`：`mode` 用 enum 收敛（AL-T9）：

```python
    mode=StringSchema(
        "检索模式：hybrid|semantic|keyword，默认 hybrid",
        enum=["hybrid", "semantic", "keyword"],
        default="hybrid",
    ),
```

标题批量取（AL-T10）：把逐条 `get_document` 换成一次批量查询——实现侧提供
`service.get_documents(doc_ids) -> dict[str, Document]`，工具层一次调用后建映射。

`nanobot/agent/tools/knowledge_read.py`：不暴露绝对路径（AL-T4）、扩读对称（AL-T6）：

```python
        # 只给文件名，不给绝对路径（AL-T4）
        path_value = Path(document.source_path).name if document.source_path else ""
```

```python
        # 前后各 context 块，边界处也要补足另一侧（AL-T6）
        before = list(reversed(blocks[max(0, index - context) : index]))
        after = blocks[index + 1 : index + 1 + context]
        missing = context - len(before)
        if missing > 0:
            after = blocks[index + 1 : index + 1 + context + missing]
        neighbours = list(reversed(before)) + after
```

`nanobot/webui/knowledge_api.py`：检索预览复用工具层渲染（AL-T7）、补失败翻译（AL-T8）：

```python
    except KnowledgeSearchError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"检索失败（没有执行检索）：{exc}",
        ) from exc
```

并把预览的 `limit` 上限从 50 收到 20（与 retriever / 工具一致），`degraded` 渲染改用
`knowledge_common` 的降级词表。

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/tools/test_knowledge_tools.py tests/webui/test_knowledge_api.py -q
```

Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add nanobot/agent/tools/knowledge_common.py nanobot/agent/tools/knowledge_search.py \
  nanobot/agent/tools/knowledge_read.py nanobot/webui/knowledge_api.py \
  tests/tools/test_knowledge_tools.py tests/webui/test_knowledge_api.py
git commit -m "fix(knowledge): 工具层对齐 WeKnora 契约，修预算口径与失败语义"
```


## 3. 任务依赖与执行顺序

```
Task 1（向量路可见性）──┐
Task 2（mode_used/带） ─┼─→ Task 3（异常分类）→ Task 14（验收）
Task 4（SQLite 探测） ─┤
Task 5（认领状态列） ───┤
Task 6（向量重试） ─────┤
Task 7（租约/队列） ────┤
Task 8（KB 缺失） ──────┤
Task 9（配置变更/修复） ─┤
Task 10（CLI 上限） ────┤
Task 11（嵌入分类） ────┤
Task 12（分块不变量） ───┤
Task 13（单例导出） ────┤
Task 15（工具层对齐） ───┘
```

- Task 1–13 之间**无相互依赖**（改的是不同模块的不同函数），可并行；
- Task 3 依赖 Task 2（同一文件 `retriever.py` 的同一函数簇，避免冲突）；
- Task 14 必须在 1–13 与 15 全部完成后执行；
- 每个 Task 内部严格串行（TDD 顺序）。

## 4. 风险与回滚

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| Task 5 改认领 SQL 影响入库流程 | 认领失败会让所有入库卡住 | 测试里已有入库全链路用例；失败立即回滚该 Task 的提交 |
| Task 6 重试让失败路径变慢 | 单批 3 次退避最长 7 s | 只对 `VectorStoreError` 重试；embedding 失败不重试 |
| Task 7 周期任务引入后台协程 | 测试挂起 | 回收循环挂在 `start()`/`stop()` 生命周期内；测试里用可替换的间隔与 `_stop_recovery_loop` 收尾 |
| Task 2 改 `mode_used` 影响工具输出 | 工具文案变化 | 工具层按 `mode_used` 渲染，不改协议字段；回归跑 `tests/tools/test_knowledge_tools.py` |
| Task 11 维度写回涉及两层 | 写回位置错会重复更新 | 写回只在 `embedding_dimensions == 0` 时发生一次；Task 11 的 Step 1 已要求先确认写回通道 |

## 5. 计划自检（写完即执行）

- [ ] **Spec 覆盖**：`AL-S1`、`AL-I1`、`AL-I2`、`AL-I3`、`AL-I4`、`AL-I6`、`AL-I7`、`AL-I8`、`AL-I9`、
  `AL-I10`、`AL-I11`、`AL-I13`、`AL-R1`、`AL-R2`、`AL-R3`、`AL-R11`、`AL-S2`、`AL-S4`、
  `AL-T1`、`AL-T2`、`AL-T3`、`AL-T4`、`AL-T6`、`AL-T7`、`AL-T8`、`AL-T9`、`AL-T10`、`AL-T11`、`AL-T12`、`AL-T13`
  全部有对应 Task；已在方案侧关闭的 `AL-S3`、`AL-S5`、`AL-S6`、`AL-I5`、`AL-T5`、`AL-R4`～`AL-R10`
  由 Task 14 的 Step 3 核对。
- [ ] **占位符扫描**：本文无 TBD / TODO / 「类似 Task N」；每个代码步骤都有完整代码块。
- [ ] **类型一致性**：`SearchHit` / `SearchResult` 字段与 `nanobot/knowledge/fusion.py` 一致；
  `mmr_rerank(relevance_band: float | None)`；`StoredChunk` / `FtsHit` / `ChunkRecord` /
  `VectorStoreError` / `EmbeddingError` / `EmbeddingUnavailable` / `VectorStore` 均按现有模块导出名书写。
- [ ] **测试替身同步**：本计划引入的新接口必须同步到测试替身，否则既有用例会红——
  Task 1 的 `FakeRepo.fetch_visible_chunk`（已在该 Task 的 Step 2 注明）；
  Task 3 的 `FakeVector(error=...)`（文件已支持）。
- [ ] **既有用例影响**：Task 11 会改动 `test_remote_4xx_is_not_retried` 的断言类型（`EmbeddingUnavailable`
  → `EmbeddingError`），这是**唯一**需要改既有断言的地方，其余均为新增用例。
- [ ] **命令可用性**：所有 `pytest` / `ruff` / `basedpyright` 命令已在 v1 实现上跑通；
  `bun run test` / `bun run build` 沿用 WebUI 既有命令。

## Next

**（plan 已写入，须暂停等用户确认 — 见 `harness-kit/core/routing.md` § 阶段门禁）**

- 确认计划 → 说「开始实现」或「并行执行」
- 需要调整范围/顺序 → 直接说修改意见
- 想先做某几个 Task → 指定编号
