---
artifact: code-review
route: orchestration.dispatch -> batch-closeout (performance lane)
plan: .ai-runtime-artifacts/plans/2026-10-27-memory-workspace-scoping-plan.md
created_at: 2026-10-27
reviewer: native-subagent (perf lane)
verdict: APPROVE
---

# 记忆工作区隔离改造 — 性能审查

## 初次结论：BLOCK（1×P1、2×P2、2×P3、0×P0）

### P1（已修）工作区过滤把 FTS5 查询计划换成「索引驱动 + 逐行全扫 FTS5」

- 位置：`nanobot/memory/repository.py::search_semantic_scored` / `search_memories`（`JOIN` → `CROSS JOIN`）
- 机制：加上 `m.workspace_id IN (...)` 后，SQLite 改用 `idx_memories_owner` 作驱动表，
  对命中集里的每一行再全量扫一次 FTS5 虚表（`SCAN f VIRTUAL TABLE INDEX 0:=M4`）。
- 量化（5 万行 / 10 工作区，生产函数实测）：

  | 可见集合 | 修复前 | 修复后 |
  | --- | --- | --- |
  | 不过滤 | 23.3ms | 23.3ms |
  | 1 个工作区 | **4866.6ms** | **9.2ms** |
  | 生产形态（`default` + 1 工作区） | — | **10.5ms** |

- 修法：`CROSS JOIN` 在 SQLite 里锁死连接顺序，强制 FTS5 始终驱动，工作区过滤降级为行级判定。
- 回归：`tests/memory/test_fts_workspace_filter_plan.py`（3 用例）——**回退到旧 JOIN 即变红（已实测）**。
  注意断言对象必须是**带参 SQL**：trace 回调给出的是参数已展开的语句，而 `LIMIT 45` 字面量与 `LIMIT ?`
  会得到不同的查询计划，因此测试用连接代理记录 `execute(sql, params)`。
- 附带修正：plan §5.2 的「FTS5 过滤零成本」结论基于 Windows 上 `ORDER BY rank` 的旧脚本，
  与生产 `ORDER BY bm25(...)` 形态不同；本机实测过滤后反而**更快**（命中集变小）。

### P2（不改）先截断、后过滤，可见候选被丢弃且不补齐

- 位置：`nanobot/memory/retrieval/store_adapter.py::search_semantic_scored`
- 量化：向量返回 135 条、前 45 条不可见时，`limit=45` 执行 45 次回查后返回 0 条，后面的 90 条可见候选未被检查。
- 判定：不改。plan §6 R2/R3 已明确接受该折中（向量层默认关闭，`memory_search_backend="fts5"`），
  本批不扩大范围；补召回需改采样上限，属独立立项。

### P2（不改）编辑普通工作区记忆会额外全量读取并序列化记忆库

- 位置：`nanobot/webui/memory_api.py::update_memory`
- 量化（前端每次保存都带 `workspace`）：1k 行 +12.56ms、10k 行 +120.74ms、50k 行 +631.41ms。
- 判定：不改。设置页成本、非对话热路径；优化需改 `update_memory` 的取数路径，属独立立项。

### P3（不改）向量独有候选的 N+1 回查

- 量化：10 万行热库下，30/45/135 条候选逐条 SELECT 分别 0.54/0.84/2.66ms，批量 `IN` 为 0.27/0.47/1.24ms。
- 判定：不改。该形态 HEAD 已存在（本批只新增可见性判定）；正常 engine 路径最多回查 45 条，成本 <1ms。

### P3（不改）每轮与每次工具调用重复解析可见集合

- 量化：热态 `_canonical_key` 2.83µs、`workspace_identity_id` 5.76µs、`memory_workspace_id` 12.84µs、
  完整 WebSocket 轮内集合解析约 77.14µs。
- 判定：不改。`_CACHE` 确实命中（连续 1000 次调用 `_resolve` 次数为 0），无需引入 TTL/负缓存。

## 写路径结论

`_persist` 的身份解析在循环外，**每批一次而非每条一次**；`_load_existing_memories` 原本就带工作区条件，
本批只是替换参数，未增加查询次数。探针实测：20 条 1.91ms、100 条 7.26ms（HEAD 1.86/6.93ms），
10k 库读取 500 条既有记忆 5.90ms —— 无写放大。

## 结论

**verdict: APPROVE** —— P1 已修复并配会变红的回归测试；其余发现经复核判定为 plan 已接受的折或非热路径成本。
