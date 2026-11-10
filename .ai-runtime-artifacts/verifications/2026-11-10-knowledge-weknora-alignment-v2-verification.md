---
artifact: verification
route: implementation-handoff
type: knowledge-weknora-alignment-v2
date: 2026-11-10
skills:
  - verification-before-completion
source:
  - .ai-runtime-artifacts/plans/2026-11-10-knowledge-weknora-alignment-impl-plan.md
  - .ai-runtime-artifacts/reviews/2026-11-10-knowledge-weknora-alignment.md §3.1
  - .ai-runtime-artifacts/execution-logs/2026-11-10-knowledge-weknora-alignment-handoff.md
evidence:
  - .ai-runtime-artifacts/verifications/knowledge-e2e/wu9-v2-evidence.md
base_ref: 024c9a78
---

# 知识检索 WeKnora 对齐（WU1–WU10）— 验证

## 范围

| WU | 内容 | 状态 |
| --- | --- | --- |
| WU1 | 正文清洗 + passage 构造（`knowledge/text.py`） | 完成 + `test_text.py` |
| WU2 | 精排按字符上限分批、`model_unavailable` 区分 | 完成 + 用例 |
| WU3 | 精排「N 进 M 出」透出（§7 RecordRetrievalCut） | 完成（M 语义已修正，见偏差 1） |
| WU4 | MMR 对齐 §8：enriched token、移除自定参数、k=1 | 完成 + 用例 |
| WU5 | 上下文装配（`knowledge/assemble.py`） | 完成 + `test_assemble.py` |
| WU6 | 历史引用注入（§9.1 ③） | 完成 + 工具用例 + 实机 |
| WU7 | 粗排池公式 ×len(KBs)/500、rerank_top_k | 完成 + 用例 |
| WU8 | Agent 输出预算 ×4/5 + budget notice | 完成 + 用例 + 实机 |
| WU9 | 实机验证 + 文档回写 | 完成（本文） |
| WU10 | 分点切法（用户追加，参考 WeKnora legacy `\n` 分隔符） | 完成 + 用例 + 实机 |

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `pytest tests/knowledge tests/tools/test_knowledge_tools.py` | **457 passed / 8 failed**；8 个为已知 Windows chmod/symlink/CRLF 基线失败（`storage/test_extract_lines` 1、`storage/test_files` 5、`test_service` 2） |
| `pytest tests/knowledge/storage/test_database.py tests/knowledge/storage/test_repository.py` | 67 passed（含删除瞬时错误重试用例） |
| `ruff check <改动路径>` | All checks passed |
| `basedpyright nanobot/knowledge nanobot/agent/tools/knowledge_*.py` | 0 errors（含此前 17 个 `rerank.py` strict 错误） |
| 实机 CLI 检索（本地 BAAI/bge-reranker-base） | `mode=hybrid routes=vector,keyword rerank=ok degraded=-`，top1=生产回滚流程 |
| 实机工具层（真实 service + 真实模型） | 父块回填 `chars=1221 blocks="2"`；`rerank_cut` 13→10（limit=20）/13→5（limit=5）；历史块 rank5 score=0.000；budget notice + `omitted count="2"` |
| 实机分块（分点清单） | 块 0 在 809 处按「下一条分点行首」断开，块尾为完整分点 |

实机细节见 `knowledge-e2e/wu9-v2-evidence.md`。

## 与手交接口径的偏差（有意）

1. **`candidates_kept` 改为「最终返回条数」**。原实现只统计过阈值条数：实机出现
   `in=13 kept=13` 但最终只返回 9 条，`rerank_cut` notice 永远不出现，缺口 #2 实际未闭合。
   现在 `candidates_in` = 截断前候选总数，`candidates_kept` = top_k / MMR / 装配后的返回条数；
   文案改为「检索从 N 条候选中保留了 M 条结果」。工具层仍用 `<notice kind="rerank_cut">`。
2. **新增分点切点**（WU10）。按用户要求参考原项目 WeKnora：
   `heading`（标题）→ `heuristic`（结构线索）→ `legacy` 递归切分（`\n\n` → `\n` → 句末）。
   我们原有前两级，缺 `\n` 这一级；在 `chunker._cut` 的段落切点之后、句末标点之前
   增加「列表项行首」（`-` / `*` / `+` / `1.` / `1)` + 空白）切点，并同步 spec §7.1。
3. **移除两个自定参数与死代码**：`relevance_band` / `adjacent_similarity_floor` 不再参与
   MMR（配置字段保留为 deprecated）；删除未使用的 `_similarity` / `_to_hit`。

## 未验证 / 已知限制

- **WebUI 浏览器会话未重跑**：本轮用 `KnowledgeSearchTool.execute` 直调真实 service 得到
  工具输出（notice / 历史 / 预算 / 回填都有真实数据），未再走 headless 浏览器截图。
- **全量 `basedpyright`（include=nanobot, strict）仍有 2745 个预存错误**，分布在
  `nanobot/writing`、`nanobot/memory` 等本次未触碰的模块；本次改动文件为 0 错误。
- 纯标题块（10 字）仍可能被召回；装配对「标题行块」不会跨 heading 回填。属既有权重/装配细节，
  本次未处理。

## 顺带修复：`knowledge rm` 的 OperationalError

- **现象**：库内有文档时，`nanobot knowledge rm` / `rm-doc` 偶发
  `sqlite3.OperationalError: unable to open database file`，失败点是
  `repository.delete_document` 的 `DELETE FROM documents`（级联删块并触发 FTS 删除触发器）。
- **根因**：不是 SQL 写错，而是 Windows 上该语句产生的临时/journal 文件被瞬时占用
  （杀毒/索引器），SQLite 报 CANTOPEN；同一操作重试即成功。
- **修复**：
  - `KnowledgeDatabase.connect` 增加 `PRAGMA temp_store = MEMORY`，级联删除不再落临时文件；
  - 新增 `KnowledgeDatabase.execute_write`：单语句写事务，对
    `unable to open database file` / `database is locked` / `disk i/o error` 做 3 次有界重试
    （失败事务已回滚，重试安全）；非瞬时错误仍一次抛出；
  - `repository.delete_document` / `delete_kb` 改走 `execute_write`。
- **验证**：单测模拟前两次连接抛瞬时 CANTOPEN，第 3 次成功；非瞬时错误只尝试 1 次；
  实机 `rm-doc` + `rm` 连跑通过，临时库已清理。

## Next

- 需要时提交：代码 → 测试 → 文档可分 3 个 commit（未 push）。
- 剩余可选优化：CLI 删除路径的 OperationalError；标题块合并进首个子块；WebUI 端到端复验。
