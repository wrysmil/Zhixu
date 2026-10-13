---
artifact: code-review
route: orchestration:collective-closeout
plan: .ai-runtime-artifacts/plans/2026-09-15-memory-retrieval-rca-fix-plan.md
skills:
  - code-review-and-quality
  - requesting-code-review
source:
  - .ai-runtime-artifacts/specs/2026-09-15-memory-retrieval-rca.md
created_at: 2026-09-15
verdict: BLOCK
reviewed_commit: c6f66ac
remediation_commits:
  - d726dfa
  - e7bd551
re_review: pending（WU-REVIEW-02，冻结快照 /tmp/rca-fix-review-e7bd551 @ e7bd551）
---

# 代码审查（第 1 轮）— 记忆检索恒空修复

**审查者：** `reviewer`（独立实例，只读，与 coder 不同实例）
**被审对象：** `c6f66ac`
**原始 verdict：** **BLOCK**
**Leader 处置：** 三条指控**逐条实测复核，全部成立**；已修复于 `d726dfa` / `e7bd551`；
按门禁「BLOCK → 修复后重新审查」已派 WU-REVIEW-02 在**冻结快照**上复验。

---

## 1. BLOCK 理由与 Leader 复核

### C-2 [可复现性] 头号验收测试不守住它所声称的修复 —— **成立，已修**

**审查者指控：** 把 `repository._search_memories_like` 替换为 `lambda *a, **k: []` 后，
`test_retrieve_with_ids_returns_chinese_memories` 仍然通过。

**Leader 独立复现（`/tmp/verify_review.py`）：**

```
[对照] 原样     : ids=['m1', 'm2']
[变异] 禁 LIKE 回退: ids=['m1', 'm2']
  => 验收测试是否仍通过（block 非空）: True   <<< True 即证明守不住根因 5
```

**根因：** 该用例的 `recent_messages` 非空 → 门禁放行 → `recent` 通道
（`query_semantic`：`importance ≥ 0.6` + 3 天窗口）**与 query 文本无关**，
单靠它就足以产出非空块。**中文 query 在该用例中是无关变量**。

→ **Leader 此前对用户「验收测试证明修复」的表述对根因 5 而言是过头的**，此处更正。
该用例实际只守住**根因 1**（适配层）——审查者的 mut3（旧装配 `store=MemoryDatabase`
→ `block=''`）已证。

**修复（`e7bd551`）：**
- 新增 `test_semantic_channel_alone_recovers_chinese_substring`：把记忆 `updated_at`
  设为 30 天前使其**超出 recent 通道窗口**，episodes（无实体）/attachments（无媒体词）
  通道均不产出 → 四路里**只有 semantic** 可能命中。
- 判别力经**变异实验自证**：

```
[原样]       ids=['old1'] block_nonempty=True
[禁 LIKE 回退] ids=[]      block_nonempty=False
=> 判别力: ✅ 成立
```

- 原用例 docstring 已更正为「只覆盖根因 1」，并指向新用例与
  `tests/memory/test_search.py::TestChineseSubstringFallback`。

### I-1 [正确性/门禁] 放宽同时过度与不足 —— **成立，已结构性修复**

**审查者指控（Leader 实测复现，`/tmp/verify_review.py`，均首轮 `recent_messages=[]`）：**

| 输入 | 修复前 | 判定 |
| --- | --- | --- |
| `查一下我的记忆` | INJECT | ✅ 目标 |
| `项目进度如何` | **SKIP** | ❌ 不足 |
| `推荐几个选题方向` | **SKIP** | ❌ 不足 |
| `总结一下我们讨论的排期` | **SKIP** | ❌ 不足 |
| `我正在规划创作选题` | **SKIP** | ❌ 不足 |
| `我的天哪` | INJECT | ❌ 过度 |
| `个人觉得还行` | INJECT | ❌ 过度 |
| `习惯就好` | INJECT | ❌ 过度 |

即：**用户报告的原始症状（首轮不检索）并未修干净**——只有「恰好含白名单关键词」
的那一类被修好；而所有格/口语词又把寒暄误判为记忆意图，配合 **reranker 的冷启动
豁免**（`recency_score ≥ 0.99` 时绕过 `_MIN_COMPOSITE`），会把真实记忆注入 system prompt。

**Leader 采纳审查者的结构性建议（不往旧分支上再贴白名单）：**
- **删除** `short_without_context` 分支。理由不是参数问题，而是其前提**恒真**：
  `loop.py` 每轮传 `list(ctx.history)`，而**会话首轮 history 必为空** → 该分支在每条
  会话的第一条消息上无条件生效。「首轮无历史」是**必然**而非「缺乏上下文」，
  用它当跳过依据在逻辑上就是错的。
- `_MEMORY_INTENT_HINTS` 收窄为强锚点（记忆/记得/回忆/我说过/提到过/偏好），
  且**只作用于 ≤3 字路径**（`memory_search` 工具常传这类短词）。

**修复后复测（同脚本）：**

```
查一下我的记忆 / 上次那个方案定了吗 / 帮我看看数据库连接为什么超时 /
我正在规划创作选题 / 总结一下我们讨论的排期 / 项目进度如何 / 推荐几个选题方向
  → 全部 INJECT   （under-trigger 消除）
```

**遗留的 over-trigger（如实记录，未修）：** `我的天哪`/`习惯就好` 仍会检索并注入。
这是**删除分支的必然代价**——短消息现在一律进入检索（Layer 4 本就是「每轮自动注入」，
短消息的取舍应交给下游 reranker 阈值与 formatter token 预算）。
**放大该代价的机制是 reranker 的冷启动豁免**（新记忆 `recency ≈ 1.0` → 绕过最小阈值），
属**既有设计、非本批引入**，已登记 follow-up。

### C-3 [正确性] 语义通道对自然中文 query 仍空 —— **成立，Leader 判定为「已知限制」，登记 follow-up**

**Leader 实测复现：**

```
semantic('创作')            -> 1 条
semantic('灵感')            -> 2 条
semantic('我正在规划创作选题')  -> 0 条
semantic('用户偏爱深色主题')    -> 0 条
```

**根因：** `channels/semantic.py` 把**整条 query** 交给 `search_semantic_scored`，
而 LIKE 回退是 `%query%` 子串匹配 —— 整句自然语言不可能成为某条记忆的子串。
`QueryDecomposer` 产出的 `keywords` 只流向 `recent` / `attachments`，**不流向 semantic**。

**Leader 判定：不阻塞本批，但必须如实登记。**
- **非回归**：修复前 semantic 对中文恒空，现在短词可用。
- 但「中文召回已修复」的说法**只对短 query 成立**；生产的实际主召回来自
  `recent` 通道（**时间窗依赖**：记忆超出 3 天窗口即失效）。
- 已登记为 **follow-up（最高优先级）**：把 keywords 也喂给语义通道（OR 合并或
  逐词召回 + 去重），并定义与 FTS 结果集的合并语义（与 I-2 同一处改动）。

### I-3 [一致性] `search_episodes` LIKE 未转义 —— **成立，已在 `d726dfa` 修复**

审查者审查的是 `c6f66ac`，未见到 `d726dfa`。该条与安全审查 M-4 同源，已在
`d726dfa` 加上 `ESCAPE '\'` + `_escape_like(entity)`。

### I-2 [正确性] FTS 有命中即整体跳过 LIKE；LIKE 只覆盖 content —— **成立，非回归，已登记**

`if rows: return ...` 提前返回 → 「FTS 命中一行则其余仅子串命中的行全部丢失」。
且 FTS 索引 4 列（content/subject/predicate/tags）而 LIKE 只查 content。
**非回归**（修复前同样只有 FTS 结果），docstring 已声明为已知不完整；
Leader 已同时把该不对称写入 collective-test §3.3。登记 follow-up（与 C-3 同处改动）。

---

## 2. C-1 [流程] 审查目标被取代 + 工作树污染 —— **成立，Leader 认责**

审查期间 Leader 提交了 `d726dfa`，导致审查对象在工作树中被取代；审查者并观测到
半写状态的假失败（`1 failed` → 3 分钟后 `681 passed`）。

**Leader 认责：** 在审查运行期间向同一 worktree 提交，违反了「审查期间冻结」的隐含要求。
**处置：**
- 第 2 轮审查（WU-REVIEW-02）改为在 **`git archive e7bd551` 生成的冻结快照**
  （`/tmp/rca-fix-review-e7bd551`，纯文件、无 `.git`）上进行，并在 prompt 中
  明确要求审查者**先自证 `nanobot.__file__` 指向快照**再下结论。
- 残留 `memory/state.db` 已清理；`webui/package-lock.json` 全程未纳入任何提交。

---

## 3. 审查者给出的其他有效成果（Leader 采信，均已要求其自行实测）

- `_escape_like` 转义顺序（先 `\` 后 `%`/`_`）与 `ESCAPE '\'` 配对正确，对照实验
  证明不转义时 `%_%` 会全表返回（5/5），转义后 `'_'`/`'%'` 只命中字面量行。
- `search_episodes` 改 `ended_at` 契约正确（`_EpisodeRow.updated_at` 承载 `ended_at`，
  与通道 `getattr(ep,"updated_at",...)` 兼容）。
- `ToolContext.sessions` 注入链路走**真实 ToolLoader** 验证成立（非只读推断）。
- `_CONTROL_ONLY` 24 条逐条在 `recent=0/1` 下全部跳过；`clean_query` 仍在门禁之前。
- `query_semantic` 的 EXPLAIN QUERY PLAN A/B：新旧均走 `idx_memories_importance`，无索引回退。
- 基线 A/B（用 `git archive` 干净导出，不在活动 worktree 内）：ruff 12 错零新增
  （唯一差异是 N806 行号位移 486→488）；全量 13 失败逐条一致，净增 +24 passed
  与 `tests/memory` 647→671 吻合。

## 4. plan 自身的缺陷（审查者指出，Leader 确认）

| 项 | 问题 | 处置 |
| --- | --- | --- |
| T8 Step2 | `tests/test_loop_wiring.py` **不存在**，是条空指令 | 记入本产物，plan 不再作为可执行依据 |
| T8 Step3/4 | 写「全绿 / `All checks passed!`」，但基线本就有 13 失败 / 12 lint 错 | 口径改为「**零新增**」（DECISION-DC3），已在 collective-test §2 记录 |
| T5 Step3 | 写 `6 passed`，实际 `8 passed`（coder 主动补强） | 陈旧计数，不影响验收 |
| T9 | 真实库冒烟被权限分类器拦截 | 见 collective-test §3（Leader 首轮已跑出并落盘；最终提交上的复跑被拦截，**未绕过**） |
| `_MEMORY_INTENT_HINTS` 命中率「约 3%」 | 审查者指出该数字无证据支撑 | 采纳：以构造反例（「我的天哪」）而非统计口径评估代价；该数字不再引用 |

---

## 5. 结论

- **第 1 轮 verdict：BLOCK**，Leader 确认有效。
- C-2 / I-1 已修复并**自证判别力**；I-3 已在 `d726dfa` 修复；
  C-3 / I-2 判定为**已知限制 + follow-up**（非回归，但不按「已完成」收口）。
- **第 2 轮审查（WU-REVIEW-02）进行中**，冻结快照 `e7bd551`。
  本批次在 WU-REVIEW-02 给出 APPROVE 之前**不得声称交付完成**。
