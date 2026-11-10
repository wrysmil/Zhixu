---
artifact: verification
route: implementation-handoff
type: knowledge-recall-thresholds-and-protection
date: 2026-11-10
skills:
  - verification-before-completion
source:
  - .ai-runtime-artifacts/reviews/2026-11-10-knowledge-weknora-gap-third-pass.md
  - D:\studyspace\源码学习\WeKnora（retrieval_config.go、query_expansion.go、chunker/protected_spans.go）
files:
  - nanobot/knowledge/retriever.py
  - nanobot/knowledge/chunker.py
  - nanobot/knowledge/service.py
  - nanobot/config/schema.py
  - tests/knowledge/test_retriever.py
  - tests/knowledge/test_chunker.py
  - tests/knowledge/test_config.py
---

# 知识域第三轮补齐（召回阈值 / 分块保护 / 查询扩展）— 验证

## 范围

| 项 | 内容 | 状态 |
| --- | --- | --- |
| P0-1 | 召回绝对阈值：`vector_threshold=0.15`（cosine）、`keyword_threshold=0.3`（组内相对） | 完成 |
| P0-3 | 分块保护：围栏代码 / markdown 表格 / `$$` 块；围栏内伪标题不开节 | 完成 |
| P0-2 | 低召回本地查询扩展：候选 < `pool_size` 时生成 ≤5 个变体，只走关键词路、阈值×0.8 | 完成 |

## 行为变化（都对配置可见、均可回退）

| 参数 | 默认 | 语义 |
| --- | --- | --- |
| `retrieval.vector_threshold` | 0.15 | cosine 低于此值在回表前丢弃；0 = 不过滤 |
| `retrieval.keyword_threshold` | 0.3 | 相对该组最高分；0 = 不过滤（SQLite `-bm25()` 量纲不可比） |
| `retrieval.expand_on_low_recall` | true | 候选数 < `pool_size` 时做本地查询扩展 |
| `retrieval.expansion_limit` | 5 | 查询变体上限 |

阈值把候选全部裁掉时沿用 `reason="filtered"`（四态语义不变）。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `pytest tests/knowledge tests/tools/test_knowledge_tools.py` | **497 passed / 8 failed**；8 个为既有 Windows chmod/symlink/CRLF 基线 |
| `ruff check nanobot/knowledge nanobot/config/schema.py tests/knowledge` | All checks passed |
| `basedpyright`（retriever/chunker/service/schema） | **0 errors, 0 warnings** |

新增用例（18 个）：

- 检索：向量阈值丢弃低分、阈值 0 保留全部、全被裁 → `reason=filtered`；关键词相对阈值；变体生成规则；低召回触发扩展；扩展可关闭。
- 分块：围栏代码不被切开、表格不被切开、`$$` 块不被切开、围栏内 `# 注释` 不开小节、超长代码块回退硬切。
- 配置：新字段默认值与 camelCase 往返。

## 实机（真实 extractor + chunker）

输入：临时 markdown，含 80 段正文 + 12 行 bash 代码块 + 6 行表格 + 尾段；
`chunk_size=300, chunk_overlap=40, paragraph_mode=blank_line`。

```
chunks 4 sizes [246, 283, 298, 140]
fence_blocks [(206, 489)] fence_end 489
table_blocks [(489, 787)] table_end 587
OK
```

代码块正好在块尾闭合，表格完整落在下一块内，四块均 ≤300（末块 140）。

## WebUI 全流程实机（2026-11-10 晚）

隔离实例：WebUI `127.0.0.1:8766` / health `8767`（独立配置目录，未影响 18790/8765 上的既有 gateway）。

| 步骤 | 结果 |
| --- | --- |
| 新建知识库 | UI 创建「E2E 全流程测试」成功 |
| 入库 | `e2e-rollback.md`（1101 字符）→ ready，6 块，`norm1:tok2:chunk1`，`vector_status=ok` |
| 分块预览 | 代码块 8 行完整落在 `chk_ebd526cc5afbe235`；表格完整落在 `chk_44503a15d2a15b07` |
| 检索预览 | 「线上版本出问题了怎么回滚」→ hybrid / vector+keyword / 6 条，top1 回滚流程 0.838 |
| 检索模式 | keyword「kubectl rollout undo」命中常用命令；semantic 命中回滚流程；hybrid「退款多久到账」命中退款说明 |
| 聊天 | Agent 调用知识检索并引用 `doc://doc_65498c275bf7` 与 3 个 `chunk://` 句柄，答案覆盖回滚 6 步 |

证据：`knowledge-e2e/webui-fullflow-summary.json`。

**测试中发现的两个问题（均非本轮后端改动引入）**：

1. 知识概览「向量」永远显示 0：后端 `stats.vector` 是对象，前端 `webui/src/lib/api.ts` 按 number 读取。
2. 知识写操作走 WebSocket mutation；HTTP POST 知识路由不可用（读接口正常）。上传因此改用官方 CLI `knowledge add` 验证。

## 已知限制

1. 关键词阈值是相对值：与 WeKnora 原始 BM25 阈值的数字不完全同义（这是 SQLite FTS 量纲决定的）。
2. 查询扩展触发线沿用 WeKnora 的 `EmbeddingTopK=50` 口径（我们映射到 `pool_size`），小库上基本每次检索都会扩一次；只查 FTS，成本为毫秒级。
3. 受保护区间超过 1.5×`chunk_size` 时放弃保护，回退硬切——否则单块会无限变大。
4. 分块保护只覆盖围栏代码、markdown 表格、`$$` 块；HTML 表格、LaTeX `\[...\]` 未覆盖。
