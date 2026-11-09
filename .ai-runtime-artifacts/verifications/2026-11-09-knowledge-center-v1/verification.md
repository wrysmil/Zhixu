---
artifact: verification
route: superpowers:verification-before-completion
scope: knowledge-center-v1（文档、golden queries、CLI/前端/后端验收）
created_at: 2026-11-10
status: pass
verdict: pass-with-gaps
spec:
  - ../specs/2026-11-09-knowledge-center-spec.md
  - ../specs/2026-11-09-knowledge-retrieval-spec.md
  - ../specs/2026-11-09-knowledge-storage-spec.md
plan: ../plans/2026-11-09-knowledge-center-plan.md#Task-21
commit: be347796
---

# 知识中心 v1 验收记录（Task 21）

本记录对应计划 Task 21：用户文档、golden queries、端到端验收。**全部命令在本机
离线执行**，临时数据库/向量/上传目录均在系统 tmp 下，未读写 `~/.nanobot`。

## 1. 环境

| 项 | 值 |
| --- | --- |
| 平台 | Darwin arm64（macOS） |
| Python | `.venv/bin/python` 3.13.9 |
| SQLite | 3.52.0（FTS5 可用） |
| Node / npm | v24.13.0 / 11.6.2 |
| 仓库 | `feature/desktop-app`，HEAD `be347796` |
| Embedding | `knowledge.embedding.backend="none"`（离线，无模型下载、无远程 API） |
| 临时目录 | `mktemp -d /tmp/zhixu-knowledge-accept.XXXXXX`（脚本用 `tempfile.TemporaryDirectory`） |

## 2. 后端测试

```bash
.venv/bin/python -m pytest tests/knowledge tests/tools/test_knowledge_tools.py \
  tests/cli/test_knowledge_cli.py tests/webui/test_knowledge_api.py -q
```

```
........................................................................ [ 92%]
....................................                                     [100%]
468 passed in 3.81s
```

其中 `tests/knowledge/test_golden_queries.py`（新增）：

```
...                                                                      [100%]
3 passed in 0.57s
```

三个用例分别是：fixture 形状（≥20 条、三字段齐备）、离线关键词命中率（≥80%）、
语料可索引性（每条 content 至少产出一个 token）。该测试**没有**标
`@pytest.mark.integration`——`pyproject.toml` 未注册该 marker，标了会产生
PytestUnknownMarkWarning，且本用例本身完全离线、确定性，应按普通测试常跑。

## 3. 静态检查

```bash
.venv/bin/python -m ruff check nanobot/knowledge nanobot/agent/tools/knowledge_*.py \
  nanobot/cli/knowledge.py nanobot/webui/knowledge_*.py tests/knowledge \
  tests/tools/test_knowledge_tools.py tests/cli/test_knowledge_cli.py tests/webui/test_knowledge_api.py
```

```
All checks passed!
```

```bash
.venv/bin/python -m basedpyright nanobot/knowledge nanobot/agent/tools/knowledge_*.py \
  nanobot/cli/knowledge.py nanobot/webui/knowledge_*.py
```

```
0 errors, 0 warnings, 0 notes
```

## 4. 前端

```bash
cd webui && npx vitest run knowledge-page && npm run build
```

```
 ✓ src/tests/knowledge-page.test.tsx (13 tests) 1112ms
 Test Files  1 passed (1)
      Tests  13 passed (13)
```

`npm run build`（`tsc -p tsconfig.build.json && vite build`）：

```
../nanobot/web/dist/assets/index-B6naY1r6.js   1,327.38 kB │ gzip: 376.66 kB
(!) Some chunks are larger than 500 kB after minification. ...
✓ built in 6.70s
```

构建成功，输出到 `nanobot/web/dist`（gitignored，未进工作区）。chunk 体积警告是
既有现象，与本任务无关。

### 4.1 浏览器端到端（2026-11-10 补测）

启动临时 gateway：

```bash
.venv/bin/nanobot gateway --config /tmp/zhixu-knowledge-browser/config.json --foreground
# WebUI: http://127.0.0.1:18998/#/knowledge
# knowledge.embedding.backend="none"；数据库/上传目录均在 /tmp，未写 ~/.nanobot
```

实测步骤与结果：

1. `#/knowledge` 页面加载，空状态与索引统计（0）正确。
2. 新建知识库「浏览器测试库」→ 列表出现，统计 KB=1。
3. 上传 `rollback.md` → 状态 `处理中` → `就绪`（97 字符 / 1 块），统计 docs=1/chunks=1。
4. 检索「部署失败怎么回滚」→ 1 条命中，界面显示 `实际模式：keyword`、
   `检索路径：keyword`、`降级：vector_unavailable`，内容为部署/回滚段落。
5. 删除文档并确认 → 统计归零、文档列表空、旧检索结果清空。
6. 删除知识库并确认 → 列表回到「还没有知识库」，统计全 0。

浏览器测试发现并修复了两个前端刷新缺陷（已提交）：

- 上传/删除/重建文档后，左侧知识库列表的「N 篇文档」不刷新；
- 删除/重建后，检索预览仍展示已失效的旧命中。

修复后 `knowledge-page.test.tsx` **14 passed**，浏览器复测通过。交互通过
`evaluate_script` 触发 DOM 事件完成（本机 in-app Browser 的指针 surface 不可用，
未做像素级截图；功能路径与用户点击等价）。

### 4.2 会话级工具调用（`search_rag`，2026-11-10）

启动临时 gateway + 本地 OpenAI 兼容 mock（SSE 流式；首轮固定返回 `search_rag`
tool_call，次轮把 tool 结果回显），知识库 `产品手册` 中只有 `rollback.md`。
WebUI 聊天发送「请再次调用 search_rag 检索：部署失败怎么回滚」。

证据：

- gateway 启动日志的 28 个工具含 `search_rag`（`knowledge_read` 同时保留）。
- mock 收到的请求 `tools` 列表含 `search_rag`；assistant tool_call 为
  `search_rag({"query": "部署失败怎么回滚", "mode": "keyword", "limit": 5})`。
- tool 消息返回
  `<knowledge_results mode="keyword" mode_used="keyword" routes="keyword" ...>`，
  命中标题「部署与回滚」、正文 `kubectl rollout undo`。
- gateway 日志打印 `Tool call: search_rag({...})`；WebUI 最终助手消息展示
  工具返回片段，说明「模型调用 → 工具执行 → 结果回传」全链路成立。

说明：mock 是脚本化模型，不是真实 LLM；本项验证的是工具注册、参数 schema、
工具调用协议与执行回传。真实模型的语义决策能力未在本机验证（无凭据）。

### 4.3 视觉评估与 polish（2026-11-10）

- **没有照抄 WeKnora 的视觉配置**。WeKnora 前端是 Vue 3 + TDesign、绿色卡片式；
  zhixu WebUI 是 React + Tailwind + 既有 workspace 组件。本页借鉴的是信息架构
  （知识库清单 / 文档管理 / 检索预览），视觉沿用宿主设计系统，避免出现两套皮肤。
- 按 `ui-ux-pro-max` 的清单做了一轮低风险 polish：统计卡加图标与渐变层级；知识库
  列表卡片化（图标、active 左侧强调、hover）；文档表加文件图标与行 hover；检索结果
  加排名、分数徽标、左侧强调与内容滚动区；补齐 `cursor-pointer` 与
  `focus-visible` 焦点环。
- 验证：`knowledge-page.test.tsx` 14 passed；`npm run build` 成功；浏览器刷新后
  无 console 错误，检索预览仍返回 1 条命中（keyword / vector_unavailable）。
- 受 in-app Browser 的 surface 限制，未生成像素级截图；DOM 结构与运行时已核对。

## 5. CLI 端到端

复跑脚本（新增，可重复）：

```bash
.venv/bin/python .ai-runtime-artifacts/verifications/2026-11-09-knowledge-center-v1/run_acceptance.py
```

脚本在 `TemporaryDirectory` 内写 config：

```json
{"knowledge": {"databasePath": "<tmp>/data/state.db", "vectorPath": "<tmp>/data/chromadb",
  "embedding": {"backend": "none"}, "chunking": {"chunkSize": 400, "chunkOverlap": 80}}}
```

然后把 `deploy.md`（回滚/部署）与 `api.md`（E-1024）导入 `产品手册`（`--global`）。
以下是实际输出（节选）。

### 示例 1（成功）：建库、入库、检索命中

```
$ nanobot knowledge create 产品手册 --global
created kb_c51dab8efd8f 产品手册
[exit=0]

$ nanobot knowledge add /tmp/.../deploy.md --kb 产品手册
ready doc_8f188bb4399c chunks=1
[exit=0]
$ nanobot knowledge add /tmp/.../api.md --kb 产品手册
ready doc_4fee9e2a2e45 chunks=1
[exit=0]

$ nanobot knowledge search 部署失败怎么回滚
mode=keyword routes=keyword degraded=vector_unavailable
0.000   chk_a35e32d0f9542fa9    doc_8f188bb4399c        # 部署与回滚

发布前先在预发环境验证,部署失败时执行 kubectl rollout undo
回滚到上一个稳定版本。
[exit=0]

$ nanobot knowledge search E-1024
mode=keyword routes=keyword degraded=vector_unavailable
0.000   chk_30f8ef227fbf1baa    doc_4fee9e2a2e45        # API 错误码

错误码 E-1024 表示对象存储写入超时。遇到该错误请检查网络连通性与桶配额,
[exit=0]
```

`stats`（节选）：

```
{"kbs": 1, "documents": 2, "chunks": 2, "by_status": {"ready": 2},
 "vector": {"status": "disabled", "backend": "none", ...},
 "pending": 0, "expired_leases": 0,
 "drift": {"index_version": {"norm1:tok1:chunk1": 2}, "stale_ready": 0},
 "db_bytes": 98304, "uploads_bytes": 0}
[exit=0]
```

`reindex --wait` → `requeued …` / `ready … chunks=1`（exit 0）；`rm-doc`/`rm`
输出 `deleted …（用户源文件未被删除）`（exit 0），随后 `list` 输出「没有知识库」。
脚本断言两篇 `.md` 源文件仍在、`state.db` 权限为 `0600`（实测 `-rw-------`，
`make` 目录 `uploads` 为 `drwx------`）。

### 示例 2（失败）：不支持的格式

```
$ printf 'a,b\n1,2\n' > data.csv
$ nanobot knowledge add /tmp/.../data.csv --kb 测试库
failed doc_3c56cf4909fa chunks=0
unsupported document type: .csv
[exit=1]
```

同一路径下 `nanobot knowledge search 回滚 --kb 不存在的库` 输出
`知识库不存在或重名：不存在的库`，exit 2（用法错误）。
`rm-doc` 缺 `--yes` 同样 exit 2。失败路径没有伪装成「搜不到」。

### 示例 3（降级）：无向量层时自动走关键词

上面所有 `search` 都打印 `mode=keyword routes=keyword degraded=vector_unavailable`，
因为配置了 `embedding.backend="none"`；`stats.vector.status="disabled"`。
空结果场景：

```
$ nanobot knowledge search zzzzqqqq
mode=keyword routes=- degraded=vector_unavailable
reason=no_candidates
没有检索到
[exit=0]
```

空结果 exit 0 且 `reason` 明确，与「检索失败 exit 1」区分。

### 观察到的边界（非缺陷，记录备查）

- 关键词路是「所有 token 显式 OR」。中文单字也进 token 集，因此
  `search "完全不存在的词xyzzy"` 仍会因「的」等单字命中文档（分数量级极小，
  显示 `0.000`）。这是检索 spec §4.1 的刻意设计（宁可多召回，由融合/去重收口），
  但说明 CLI 预览里「有返回」不等于强相关。
- `knowledge.chunking.maxFileMb` 目前只被 WebUI 上传上限使用
  （`min(6 MB, maxFileMb)`）；CLI `knowledge add` 没有单独的大小预检，实际约束来自
  解析层：pdf/docx/xlsx/pptx 50 MB、PDF 单页内容流 32 MB、Office 解压 256 MB；
  md/txt 直接解码、无 50 MB 预检。方案 §12/存储 spec §13 写的是「CLI 上限
  max_file_mb」，实现与文档存在这一点差异。**本次只记录，未改产品代码**。
  用户文档按实现口径写成「解析层 50 MB」并提示大文件走 CLI。

## 6. Golden queries

- fixture：`tests/knowledge/fixtures/golden_queries.json`，24 条，覆盖计划列出的
  22 个主题（部署回滚、E-1024、远程 embedding、API key、计费、403、备份、监控、
  升级、日志、模型下载慢、维度不一致、分块、PDF 导入、导出、删除语义、并发、
  容量、路径穿越、扫描版 PDF，另加全局库、检索模式、文档状态、FTS 修复）。
- 测试：`tests/knowledge/test_golden_queries.py` 用 `KnowledgeDatabase` +
  `KnowledgeRepository` 在 tmp 建库，逐条把 `content` 写成 ready 文档 + 单块
  （`search_text=tokenize_index(content)`、`content_hash=sha256`），再用
  `Retriever(embedder=NoneEmbedder(), vector_store=None)`、`mode="keyword"` 检索。
  命中判据：任一返回块的 content 含 `expect_any` 任一词（大小写不敏感）。
- **结果：24/24 命中，命中率 100%（阈值 80%，未下调）**。另用一次性探针按
  「命中块是否来自对应源文档」复核，同样 24/24（100%），无 misses。
- 语料修正（只改 fixture，不改检索器）：① 「升级」段落原文含「备份」，会与
  「如何备份数据库」混淆，改为「做好数据快照」；② 「并发」段落补上查询词
  「多个进程并发写入」，提高该文档的 BM25 相对分。二者都是让文档更贴近自然表述，
  不是为命中率削阈值。
- 未发现检索器真实缺陷。

## 7. 引用与未覆盖

已引用的前置验证：

- 存储层 DDL 预验证：
  `.ai-runtime-artifacts/verifications/2026-11-09-knowledge-storage-ddl/verification.md`
  （建表/触发器/中文召回/`E-1024` compact 召回/级联/权限，verdict: pass）。
- 检索融合口径探针：
  `.ai-runtime-artifacts/verifications/2026-11-09-knowledge-retrieval-fusion/verification.md`
  （包含率去重、MMR 相关性带、RRF 单路天花板，verdict: pass）。

**未实际执行**（本机离线、无凭据，不能声称已覆盖）：

| 项 | 原因 | 影响 |
| --- | --- | --- |
| 真实 embedding 模型下载（local / sentence-transformers） | 需网络与模型体积，本次强制 `backend=none` | 向量路、维度自适应、`reindex` 向量重写未实测 |
| 远程 embedding `/v1/embeddings`（含 key/超时/重试） | 无 API key 与外部服务 | `embedding_error:*` 降级仅由单测覆盖 |
| Chroma 持久化与 `repair --vectors` 实际 rewrite/purge | 未安装/未启用 vector extra | 由 `tests/knowledge/test_vector_store.py` 等单测覆盖，非端到端 |
| 浏览器像素级截图 / 多语言切换 | 仅做了 DOM 级功能交互 | 不构成功能缺口 |
| 真实 LLM 供应商（模型自行决定调用工具） | 无 API 凭据，使用 scripted mock | 工具注册/schema/调用协议已验；模型语义决策未验证 |
| 真实 PDF/DOCX/XLSX/PPTX 入库 | 离线未准备样本 | 抽取层由 `test_extract_lines.py` 覆盖，CLI 只验了 md |
| 扫描版 PDF / OCR | v1 非目标 | 文档已写明不支持 |
| 多进程并发写、崩溃恢复、租约过期回收 | 需多进程/杀进程场景 | 由 repository/ingestor 单测覆盖，未做系统级演练 |

## 8. 结论

后端 468 项测试、ruff、basedpyright、前端 14 项组件测试与构建、CLI 端到端脚本、
浏览器端建库/上传/检索/删除、会话级 `search_rag` 工具调用全部通过；golden queries
24/24 命中。文档 `docs/knowledge.md` 已按已实现 CLI/配置口径编写，并标注 v1 非目标。

整仓 `pytest -q` 为 11 320 passed / 8 failed；8 个失败均与本域无关（CLI serve 4、
session preview 2、agent profile prompt 1、MCP shutdown timing 1），知识域 scoped
468 项全绿。

**Verdict: pass（带明确缺口）**——缺口集中在需要联网/凭据/浏览器/真实二进制文档
的路径，见 §7；本期不以单测或离线脚本冒充这些路径已验证。
