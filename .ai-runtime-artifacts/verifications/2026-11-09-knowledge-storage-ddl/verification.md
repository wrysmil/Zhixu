---
artifact: verification
route: superpowers:brainstorming
spec: .ai-runtime-artifacts/specs/2026-11-09-knowledge-storage-spec.md
skills:
  - verification-before-completion
source:
  - .ai-runtime-artifacts/specs/2026-11-09-knowledge-storage-spec.md
  - .ai-runtime-artifacts/specs/2026-11-09-knowledge-center-spec.md
created_at: 2026-11-09
verdict: pass
scope: 方案期预验证（DDL 与查询 SQL 可执行性），不含实现代码
---

# 知识存储方案 — DDL 与查询预验证

目的：存储方案 §6 的建表语句、§7 的分词/查询 SQL 不能只停留在纸面。
在写实施计划之前，把 DDL 原文抽出来在真实 SQLite 上跑一遍，确认它可执行、
触发器行为符合设计、查询计划与方案里的性能主张一致。

**这不是实现验证**：仓库里还没有 `nanobot/knowledge/` 代码，本次验证的是
「方案里写的 SQL 是否成立」。正式实现仍需把这些断言落成 pytest（见方案 §19）。

## 1. 环境

| 项 | 值 |
| --- | --- |
| 解释器 | `.venv/bin/python`（Python 3.13.9） |
| SQLite | 3.52.0 |
| 数据库 | 每次运行新建临时库（WAL），不复用 |
| DDL 来源 | 直接从方案 §6 的 `sql` 代码块抽取，不手抄、不改写 |

`verify_ddl.py` 用正则找出同时包含 `_schema_meta` 与 `document_chunks_fts`
的代码块；`verify_plan.py` 复用同一份抽取逻辑与同一份分词函数，
保证测的是方案原文而不是代码副本。

> **必须用 `.venv/bin/python`。** 系统 `/usr/bin/python3`（3.9.6 / SQLite 3.43.2）
> 对普通 `JOIN` 会选 FTS 驱动的计划，E15 的 1.7 s 对比无法复现；这不是方案问题，
> 而是查询计划随 SQLite 版本变化——恰是 §7.3 要求 `CROSS JOIN` 的理由之一。

## 2. DDL 执行结果

| 检查项 | 断言 | 结果 |
| --- | --- | --- |
| 建 schema | 3 张实体表 + FTS 虚表 + 7 索引 + 3 触发器一次建齐 | 通过 |
| 插入同步 | `document_chunks` 3 行 → `document_chunks_fts` 3 行 | 通过 |
| 中文单字召回 | `创作` → 命中含「用户热爱创作」的块 | 通过 |
| 型号 compact 召回 | `E-1024` → `e1024`，命中含该串的块 | 通过 |
| 状态过滤 | `parse_status='failed'` 的块不可被检索到 | 通过 |
| 工作区隔离 | 另一工作区查询只看到 `visibility='global'`，本例为 0 行 | 通过 |
| 块不可变 | `UPDATE ... SET content` 被 `CHUNK_IMMUTABLE` ABORT | 通过 |
| 可变列 | `UPDATE ... SET is_enabled=0` 正常提交 | 通过 |
| 文档级联 | 删 1 篇文档 → 块与 FTS 同为 2 行；被删块不可再匹配 | 通过 |
| 知识库级联 | 删知识库 → 文档/块/FTS 全部为 0 | 通过 |
| 维护命令 | `optimize`、`rebuild` 均可执行 | 通过 |
| CHECK 约束 | `chunk_overlap >= chunk_size` 被拒绝 | 通过 |

运行输出：

```
OK  schema created
OK  seed
OK  fts auto-synced rows=3
OK  CJK substring recall 创作 -> ['chk_1']
OK  compact ascii recall E-1024 -> ['chk_2']
OK  parse_status filter holds
OK  workspace isolation holds
OK  content update blocked
OK  mutable column (is_enabled) still writable
OK  cascade delete kept FTS in sync rows=2
OK  deleted chunk no longer matchable
OK  kb cascade clears fts
OK  optimize + rebuild accepted
OK  overlap>=size rejected: CHECK constraint failed: chunk_overlap < chunk_size
ALL CHECKS PASSED
```

执行过程中暴露并已修正的两个脚本层问题（不影响方案内容）：

1. `executescript()` 会先隐式提交，`BEGIN IMMEDIATE` 必须写进脚本本身才有效。
   结论已同步到方案 §5 的实现注意点。
2. 触发器体内部的 `;` 不能用 `re.split(r";\s*\n")` 切语句，否则
   `CREATE TRIGGER ... BEGIN ... ; END` 被截断，报 `incomplete input`。

## 3. 查询计划与耗时对比（方案 E15）

数据集：1 250 篇文档 × 40 块 = 50 000 块；其中 200 块包含查询词，其余不含；
`ORDER BY rank LIMIT 20`；取 5 次最优。

| 写法 | 执行计划首行 | 耗时（两次独立运行） |
| --- | --- | --- |
| 方案写法（`CROSS JOIN document_chunks`） | `SCAN document_chunks_fts VIRTUAL TABLE INDEX 32:M4` | **0.6 ms / 0.6 ms** |
| 普通 `JOIN document_chunks` | `SEARCH d USING INDEX idx_docs_vector (parse_status=?)` | **1694.4 ms / 1687.8 ms** |

DDL 修订后（删 `knowledge_bases.metadata`、加 `idx_docs_path`、删与 UNIQUE 重复的
`idx_chunks_doc` 与无查询路径的 `idx_chunks_hash`）复跑多次，结论不变：`CROSS JOIN` 0.6 ms，
普通 `JOIN` 1748.7 / 1760.6 / 1793.6 / 1806.2 ms；执行计划除块表索引改由 UNIQUE 自动索引
承担外与上表一致。

完整计划：

```
--- spec(CROSS JOIN)
    SCAN document_chunks_fts VIRTUAL TABLE INDEX 32:M4
    SEARCH c USING INTEGER PRIMARY KEY (rowid=?)
    SEARCH d USING INDEX sqlite_autoindex_documents_1 (id=?)
    SEARCH kb USING INDEX sqlite_autoindex_knowledge_bases_1 (id=?)
--- plain JOIN
    SEARCH d USING INDEX idx_docs_vector (parse_status=?)
    SEARCH c USING INDEX sqlite_autoindex_document_chunks_2 (doc_id=?)
    SEARCH kb USING INDEX sqlite_autoindex_knowledge_bases_1 (id=?)
    SCAN document_chunks_fts VIRTUAL TABLE INDEX 32:=M4
    USE TEMP B-TREE FOR ORDER BY
```

解读：普通 JOIN 下优化器改用 `idx_docs_vector` 驱动，对每个块再扫一次 FTS 虚表，
并额外付出临时 B 树排序；`CROSS JOIN` 让 FTS 成为唯一驱动表，过滤在召回之后逐行判定。
这与记忆域 `nanobot/memory/repository.py` 的实测结论（25 ms → 4867 ms）同因，
但本次数据形态更接近知识库检索，故作为方案 E15 单列。

### 3.1 单块存储开销（E16）

`measure_size.py`：30 篇文档 × 10 块 = 3000 块，每块 800 个 CJK 字符，`dbstat` 按表/索引分项计量。
两套语料：`repeated`（重复片段，token 多样性低）与 `diverse`（随机字表，接近真实文档）。

```
chunks=3000 chunk_chars=800
corpus      tokens   content    search  chunk_tab  fts_data  fts_other  db/chunk  10k chunks
repeated      1458     2.34K     7.76K     12.01K     0.51K      0.02K     12.64K        123 MB
diverse       1540     2.34K     8.24K     12.01K     3.83K      0.03K     16.16K        158 MB
```

- `chunk_tab` 已包含 `content` 与 `search_text`，**不能再相加**（旧容量表就错在这里）。
- FTS 倒排是唯一额外的 SQLite 开销，真实语料约 3.8 KB/块。
- Chroma 512 维另计 ≈5.9 KB/块（E5）→ 1 万块总计约 181–216 MB。

## 4. 未被本次覆盖

| 项 | 原因 | 归属 |
| --- | --- | --- |
| 真实 Chroma 读写、collection 指纹隔离 | 方案 §8 已单独实测（E5/E10），本次不重复 | S-04 测试 |
| 并发 claim / 租约回收 | 需要实现代码与多线程调度 | S-05 测试 |
| PDF `continuation` 循环 | 需要构造 250 页样例 | S-02 测试 |
| WebUI / CLI 端到端 | 尚无实现 | 父方案验收 |

## 5. 复现

```bash
cd <repo>
.venv/bin/python .ai-runtime-artifacts/verifications/2026-11-09-knowledge-storage-ddl/verify_ddl.py
.venv/bin/python .ai-runtime-artifacts/verifications/2026-11-09-knowledge-storage-ddl/verify_plan.py
.venv/bin/python .ai-runtime-artifacts/verifications/2026-11-09-knowledge-storage-ddl/measure_size.py
```

三个脚本都会读取方案原文；若方案 §6/§7 被改动而脚本断言失败，说明文档与可执行事实已漂移。
`measure_size.py` 的耗时约数秒，结果应与 §3.1 的表一致（±0.05 KB 舍入误差）。
