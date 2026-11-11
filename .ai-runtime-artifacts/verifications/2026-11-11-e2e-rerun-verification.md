---
artifact: verification
route: verification-before-completion
skills:
  - verification-before-completion
created_at: 2026-11-11
status: passed-with-observations
source:
  - .ai-runtime-artifacts/verifications/2026-11-11-graph-weknora-alignment-verification-lite.md
  - .ai-runtime-artifacts/specs/2026-11-10-knowledge-graph-spec.md
  - .ai-runtime-artifacts/contracts/2026-11-11-contract-knowledge-graph.md
evidence:
  - .ai-runtime-artifacts/verifications/2026-11-11-e2e-rerun/graph-rerun.png
  - .ai-runtime-artifacts/verifications/2026-11-11-e2e-rerun/chat-rerun.png
  - .ai-runtime-artifacts/verifications/2026-11-11-e2e-rerun/verify_chat_flow.cjs
  - .ai-runtime-artifacts/verifications/2026-11-11-e2e-rerun/webui-rerank.log
  - .ai-runtime-artifacts/verifications/2026-11-11-e2e-rerun/webui-vector.log
---

# 知识检索全流程 + 对话界面 E2E（2026-11-11）

## 环境

| 项 | 值 |
| --- | --- |
| 知识数据 | 复用上一轮 `state.db`（Graph E2E / Graph Complex / Graph Novel，10 篇 ready 文档） |
| 图后端 | 临时 Neo4j 5.26，SQLite 全量 `GraphSyncService.rebuild_kb` 同步 |
| Embedding | 本地 `BAAI/bge-small-zh-v1.5`，512 维；14 个 chunk 直接补向量（不改分块/图谱） |
| 精排 | 本地 `BAAI/bge-reranker-base`（CPU，缓存模型） |
| Gateway/WebUI | 隔离端口 18821 / 8782；WebSocket 8782；评估 18822 |
| 隔离 | `config.json` 运行后删除；临时 Neo4j/WebUI 已停止 |

## 结果

### 1. 直接检索（无精排）

```text
routes: ('keyword', 'graph')  mode: hybrid  degraded: ('vector_unavailable',)
rerank: no_model  in/kept: 0 0
```

返回 7 条命中，前 5 条带图证据路径，例如：

- `chk_516b...` `graph=True` path=`灰度发布流程 —发布→ 新版本`
- `chk_f392...` `graph=True` path=`灰度发布流程 —负责→ 值班二号`

### 2. 直接检索（开启本地精排）

```text
routes: ('keyword', 'graph')  mode: hybrid
rerank: ok  in/kept: 9 8
```

精排输出前两条：

- `chk_47a0...` score=0.8170 `graph=True` path=`值班二号 —执行→ 回滚`
- `chk_516b...` score=0.8386 `graph=False`

温启动后再次检索（同进程）：

```text
routes: ('keyword', 'graph')  rerank: ok  in/kept: 9 8
graph hits: 7
```

### 3. 三路检索（向量 + 关键词 + 图谱，开启精排）

```text
routes: ('vector', 'keyword', 'graph')  mode: hybrid  degraded: ()
rerank: ok  in/kept: 14 7
```

前 5 条同时带向量/图谱证据：

- `chk_47a0...` score=0.8371 `graph=True` path=`灰度发布流程 —首批放量→ web-api 网关`
- `chk_516b...` score=0.8352 `graph=True` path=`灰度发布流程 —发布→ 新版本`
- `chk_f392...` score=0.8289 `graph=True` path=`灰度发布流程 —负责→ 值班二号`

向量路已参与融合与精排，`degraded=()`；聊天回答也不再出现“向量检索不可用”的降级提示。

### 4. 浏览器 E2E（7/7 PASS）

脚本：`verify_chat_flow.cjs`

| 检查 | 结果 |
| --- | --- |
| 图谱 tab 真实实体 | 14 个实体 |
| 实体详情/子图 | 5 个节点、4 条边 |
| 聊天回答包含「值班二号」 | PASS |
| 回答引用知识来源 | PASS（含 `doc://` / `chunk://` 与文档标题） |
| 浏览器控制台无非预期 error | PASS |
| 非 workbench API 404 | PASS |
| 图谱关系佐证 | PASS（回答中出现 `值班二号 —负责/执行→ 回滚`） |

聊天日志确认实际走的是：

```text
knowledge_search({"query":"灰度发布 回滚 负责人", ...})
knowledge_graph({"query":"灰度发布 回滚 负责人","hops":1,"limit":10})
knowledge_read(...)
```

截图：
- `.ai-runtime-artifacts/verifications/2026-11-11-e2e-rerun/graph-rerun.png`
- `.ai-runtime-artifacts/verifications/2026-11-11-e2e-rerun/chat-rerun.png`

### 5. 回归

| 命令 | 结果 |
| --- | --- |
| 定向 9 文件 | **138 passed** |
| `pytest tests/knowledge tests/tools/test_knowledge_tools.py tests/evaluation/knowledge -q`（`NO_PROXY=` 清空） | **758 passed / 8 failed / 1 skipped**；8 个为既有 Windows chmod/symlink/CRLF 基线 |
| `ruff check <changed files>` | All checks passed |

## 发现与修复

### 已修：MiniMax thinking 导致查询侧抽实体失败

首次真实检索出现：

```text
knowledge graph query entity extraction failed: TimeoutError()
...
GraphExtractionError('parse')
```

单独调用模型看到原始输出是 `<think>...长篇推理...</think>` + 散文，不是 JSON。关闭 thinking 后：

```text
elapsed: 1.38s  raw: '[{"entity":"发布负责人"}]'
```

修复：

- `knowledge.graph.extraction.thinking` 默认 `disabled`，请求体带 `thinking: {"type":"disabled"}`；不支持该参数的供应商可设 `auto`。
- 查询 prompt 增加 `# Output contract`：不要回答问题，只返回 `[{"entity": ...}]`。
- 本地 reranker 首次加载约 20–35s，属模型冷启动；温启动后检索约 8s。
- 查询侧抽实体仍可能返回泛化名；图路已加“无种子则本地回退”，见下文修复。

### 观察 1：图扩展 0.8s 冷启动超时

开启精排后的第一次检索出现一个 KB：

```text
knowledge graph route failed: kb=... error=TimeoutError()
degraded=('vector_unavailable','graph_unavailable')
```

其他 KB 图路正常，随后温启动检索两条 KB 均产出图命中。当前 0.8s 是检索硬超时，建议按冷启动/大图场景评估是否提高到 2s，或加一次轻量预热；本次未改默认值，避免未评估地抬高热路径延迟。

### 观察 2：websockets 握手 AssertionError

浏览器上下文切换/关闭时服务端日志出现一次：

```text
AssertionError: assert not self.eof_sent
```

发生在 websockets 握手阶段；浏览器端无 console error、无 API 404，聊天与图谱功能正常。判定为非阻断的库级连接关闭告警，建议后续单独排查连接复用/关闭路径。

### 已修：LLM 实体名匹配不到图时图路静默缺席

三路检索首次验证时，查询侧 LLM 抽出泛化名「发布负责人」，图里没有对应实体，`routes=('vector','keyword')` 且无任何降级标签——图路静默消失。修复：LLM 实体名在某 KB 无种子时，回退该 KB 的本地子串/分词链接；新增 `test_unmatched_llm_entities_fall_back_to_local_linker`。修复后 `routes=('vector','keyword','graph')`、`degraded=()`。

### 观察：向量/精排模型冷启动

本地 embedding 与 reranker 首次加载分别约 16s / 20–35s；温启动后三路检索约 23s（含查询侧抽实体与精排）。这是本地模型冷启动成本，不是检索逻辑错误。

## Cleanup

- 运行配置 `config.json` 已删除，仅保留脱敏 `config.example.json`。
- 临时 Neo4j 容器已删除；WebUI/gateway 已停止；隔离端口无监听。
- 未执行 commit（用户未要求）。
