---
artifact: evidence
type: knowledge-weknora-alignment-e2e
date: 2026-11-10
round: v2
instance: ~/.nanobot（符号链接到 .ai-runtime-artifacts/nanobot-data）
model: BAAI/bge-reranker-base（本地 cross-encoder，CPU）
---

# WU9 实机证据（v2）

## 环境

- 配置：`knowledge.rerank.enabled=true`、`backend=local`、`model=BAAI/bge-reranker-base`、
  `threshold=0.3`、`maxCandidates=200`、`maxPassageChars=1200`
- 测试知识库：`青鸾发布手册 WU9`（临时，验证后已删除）
- 文档：
  - `青鸾发布手册.md`（10 个小节，11 块）
  - `分点清单.md`（1 个标题 + 26 条分点，2 块）
- 输入文件保留在 `knowledge-e2e/wu9-v2/`，可复现。

## 1. CLI 检索：精排生效

```
$ nanobot knowledge search "回滚流程怎么执行" --kb "青鸾发布手册 WU9" --limit 5
mode=hybrid routes=vector,keyword rerank=ok degraded=-
0.837   chk_b84102860378a526    doc_e76d1aa64314        ## 生产回滚流程
```

top1 = 生产回滚流程（不精排时 top1 是备份策略，见上一轮证据 `rerank-local-model.txt`）。

## 2. 分点切法（新增）：块尾落在完整分点

```
idx=0 chars=809 offsets=0-809   ends_with_newline=True  last_line_is_item=True
                                last='- 发布前确认跨团队依赖已经就绪,上...'
idx=1 chars=411 offsets=689-1100 ends_with_newline=False last_line_is_item=True
                                last='- 发布前确认发布负责人与备份负责人...'
```

- 块 0 在 809 处断开，切点是下一条分点的行首（`\n- 发布前…`），没有把分点从中间切开。
- 块 1 从 689 开始是 120 字 overlap（含上一条分点尾部），属预期。

## 3. 工具层：截断声明 / 父块回填 / 历史注入 / 预算

真实 service + 真实精排模型，直接执行 `KnowledgeSearchTool.execute`：

```
SEARCH: hybrid ('vector','keyword') rerank=ok degraded=() in=13 kept=9 hits=9
  0.814 chk_b0c8d0bf6f8cd30b chars=1221 merged=2 发布检查分点清单
  0.760 chk_b2f2bbc11c4c758f chars=242  merged=0 青鸾发布手册 > 附录:术语
  ...
```

- **父块回填**：`chars=1221 merged=2` —— 命中 411 字的短块后合成父块（补齐相邻块），
  工具输出带 `blocks="2"` 属性。
- **截断声明**（`limit=20`，输出 4571 字符）：

```
<notice kind="rerank_cut">检索从 13 条候选中保留了 10 条结果，以下内容只是候选的子集，不代表知识库里只有这些。</notice>
<omitted count="2" reason="budget"/>
<notice kind="budget">因输出预算（4800 字符）省略了 2 条结果，可用 knowledge_read 按句柄扩读被省略的部分。</notice>
```

- **历史引用注入**（`limit=5`，历史句柄 `chk_e3cc2351bf9af9c5`）：

```
<notice kind="rerank_cut">检索从 13 条候选中保留了 5 条结果，…</notice>
<result rank="5" score="0.000" chunk="chunk://chk_e3cc2351bf9af9c5" … chars="230">…
```

  rank5 = 注入的历史块（score=0.000），作用域内可见，未参与排序。

## 4. 发现并修正的问题

初版实现的 `candidates_kept` 只统计「过阈值条数」：本次实机 `in=13 kept=13`，
但最终只返回 9 条，`rerank_cut` notice 永远不会出现。已改为在 top_k / MMR / 装配之后
用最终返回条数覆盖，`candidates_in` 改为截断前候选总数，并改文案为
「检索从 N 条候选中保留了 M 条结果」。

## 5. 清理

- 临时知识库已删除（`list` 输出「没有知识库」）；输入文件保留在 `wu9-v2/`。
- 配置未改动（验证前后均为 rerank 开启）。
