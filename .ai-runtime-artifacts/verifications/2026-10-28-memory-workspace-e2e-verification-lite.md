---
artifact: verification-lite
route: leader-direct | 验证/跑命令
skills:
  - verification-before-completion
  - git-xywh
skills_evidence:
  - harness-kit/.agents/skills/git-xywh/SKILL.md
  - nanobot/cli/commands.py:29-40（loguru sink 口径）
source:
  - "进行测试，用浏览器工具，再前端测试下功能；同时你要看看，我要测试记忆能力，日志有没有打好"
created_at: 2026-10-28
tier: 1
---

# 记忆工作区隔离 — 真机前端实测 + 记忆链路日志审计

> **纪律：** 先跑命令、再给结论。以下全部为 Leader 本机实跑输出，不是自报。
> 补齐 `2026-10-27-memory-workspace-scoping-collective-test.md` §「真机场景未做（需用户参与）」。

## 范围

- 被验证代码：`d43c65c feat(memory): 记忆按工作区隔离`（拉取自 origin）
- 验证对象：WebUI 记忆中心（列表/筛选/编辑/新建/删除）、记忆抽取写入链路、检索可见集合
- 路由判定：Tier 1（Leader 直做，验证类，不改业务代码）
- 未改动任何仓库业务代码；仅新增本报告与 vite 缓存目录

## 环境搭建（隔离实例，避开并行会话）

| 项 | 值 | 原因 |
| --- | --- | --- |
| 实例目录 | `C:/Users/Huangqh/AppData/Local/Temp/zhixu-mem-e2e/` | `.ai-runtime-artifacts/` 是**已跟踪**目录（741 文件）且未 gitignore，配置内含真实 API Key，放仓库内有泄密风险 |
| 配置 | 复制 `~/.nanobot/config.json` 后改写 workspace/端口 | 保留真实 provider，抽取能真跑 |
| 网关 | WS 18771 / HTTP 18772 | 避开主网关 18790/8765 与并行 Codex 实例 18767/18768 |
| 前端 | vite dev server 5173，`NANOBOT_API_URL=http://127.0.0.1:18771` | **WebUI HTTP 路由在 WS 端口上**，gateway 端口只有 `/health` |
| 日志 | 网关 stderr 重定向到 `<instance>/logs/gateway.log` | CLI 的 loguru sink 只有 stderr（`nanobot/cli/commands.py:29-40`），`--foreground` 不落文件；仓库里那份 `gateway.log` 停在 10-20 属此类 |

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `git pull --ff-only` | `2b447c6 → d43c65c`，43 files +1865/-111，无冲突（已跟踪文件零改动） |
| 网关启动 | ✅ WS 18771 / HTTP 18772 监听，26 个工具注册 |
| vite 启动 | ✅ 首次因 esbuild 写 `%TEMP%` 被拒崩溃（受限令牌），改 cacheDir 到仓库内后正常 |
| 浏览器打开 `#/memory` | ✅ 页面渲染完整，控制台无报错 |
| 工作区筛选 | ✅ All=5 / alpha-app=2 / beta-service=2 / 全局=1 / 复位=5，内容逐条核对正确 |
| 搜索 | ✅ "FastAPI" → 1 条，仅 beta 那条 |
| 归属编辑 | ✅ 全局 → alpha-app，库里 `workspace_id` 与 provenance 快照同步更新 |
| 新建记忆 | ✅ 5→6，"Manual" 计数 1 |
| Episodes 页签 | ✅ 正常切换（本实例无 episode） |
| 真实抽取 | ✅ 空闲 120s 后触发，59 条消息 → **2 条记忆 + 1 个情节** |
| 检索隔离探针 | ✅ alpha 视角 3 条（含全局，不含 beta）；beta 视角 1 条（不含 alpha） |

### 抽取与归属实证（真实链路）

```
10:12:44 idle timer fired for session websocket:8047b950... after 120.0s idle; starting incremental extraction (59 messages)
10:12:45 idle extraction start ...: messages=59 last_extracted=0
10:13:13 idle extraction ...: 59 new messages -> 2 memories, 1 episodes (skipped=0 failed_tracks=[])
10:13:13 idle memory extraction finished ...: memories=['90556a9d-...','98ed4de4-...'] episodes=['a2bf7132-...']
```

落库归属核对：两条记忆 `workspace_id=52b68c894e474e6be9b5004e9157a99e`，与
`<workspace>/.nanobot/workspace-id` 标记文件一致；provenance 快照 name/path 同源。

### 跨工作区隔离实证（检索探针，生产栈 MemoryStoreAdapter → RetrievalEngine）

| 视角 | 可见集合 | query | 命中 |
| --- | --- | --- | --- |
| alpha-app | `('default', 301ae182…)` | pnpm vitest 工具链 | 3 条（2 alpha + 1 全局），**不含 beta** |
| alpha-app | `('default', 301ae182…)` | FastAPI PostgreSQL 端口 | 3 条，**不含 beta 的 FastAPI 记忆** |
| beta-service | `('default', 1cc31654…)` | FastAPI PostgreSQL 端口 | 1 条（beta），**不含 alpha** |
| beta-service | `('default', 1cc31654…)` | pnpm vitest 工具链 | 1 条，**不含 alpha** |

## 缺陷

### D1（前端，P1）直接打开 `#/memory` 时「Auto-remember」开关状态错误

- 现象：后端 `runtime.memory_enabled=true`，页面开关显示 `（Off）`；点一次发出 `memory_enabled: true`（空写），要点第二次才能真正关闭。
- 根因：`webui/src/components/workspace/MemoryPage.tsx:22` 用 `useState(settingsSnapshot)` 拷贝一次，**没有把后续异步加载完成的 `settingsSnapshot` 同步进 `payload`**。`settingsSnapshot` 由 `App.tsx:1181` 异步拉取，直接深链/刷新时挂载瞬间为 `null`，于是 `MemorySection` 收到的 `settings` 恒为 `null`：
  - `MemorySection.tsx:30` → `initialEnabled = settings?.runtime?.memory_enabled ?? false` → 恒 false
  - `MemorySection.tsx:37-40` 的采纳 effect 因 `!settings` 直接 return，永不跟随后端
- 影响：用户看到「关」而实际是「开」；首点为无效写，**在记忆页无法一次关闭自动记忆**。
- 对照：`useSettingsController.ts:186-189` 有 `applyPayload(initialSettings)` 的同步写法，`MemoryPage` 缺同款；`MemorySection` 目前只被 `MemoryPage` 挂载（`SettingsPage.tsx:111` 注释：已随 MemorySection 迁到独立页），故 Settings 页不复现。
- 复现：`http://127.0.0.1:5173/?r=4#/memory` 直接刷新；先经 `#/` 再 hash 跳转到 `#/memory` 则正常（settings 已就位）。

### D2（后端，P2）`memory_search` 工具异常被静默吞掉

- `nanobot/agent/tools/memory_search.py:117-118`：`except Exception: return ""`，无任何日志。
- 后果：检索链路一旦抛异常，模型只看到空字符串，日志里也无痕迹。

### D3（后端，P2）工作区过滤与「无命中」在日志里不可区分 —— 本次真机撞见

- 现场：10:08:33 会话调用 `memory_search("部署流程 pnpm install vitest deploy.sh")`，日志输出
  `· FTS5(BM25) 0 条` / `· 向量(余弦) 0 条` / `· 近期通道: 0 条`。
- 真相：该会话 scope 是实例工作区，可见集合为 `('default', 52b68c89…)`；alpha/beta 的记忆被**工作区过滤**挡住，FTS5 侧是 SQL 预过滤（`repository.py:802-805`），被挡行不进命中列表 → 日志显示「0 条」。
- 后果：**日志无法区分「文本没命中」与「命中了但被工作区挡掉」**。本次 agent 因此误判为「记忆里没有」，转而用 11 次 `list_dir`/`exec` 全盘搜索 `deploy.sh`。
- 叠加：`trace` 的 `start`/`recall` 不打 `workspace_ids`（`retrieval/trace.py:65-82,113-136`），本轮生效的可见集合在日志里完全不可见。

### D4（后端，P2）写入侧归属判定全程无日志

- `provenance.py:118 memory_workspace_id`、`:145 session_memory_workspace_id`、`extractor.py:1303 _row_workspace_id`、`:1555 _persist` 算出的 `row_workspace_id` **从不落日志**；仅异常时 `provenance.py:136` 有兜底告警。
- 后果：本次抽取日志只给条数与 ID，要确认归属必须拿 ID 回查数据库（本次即如此）。

### D5（后端，P3）记忆链路无耗时打点

- `nanobot/memory/` 全域无 `perf_counter`/elapsed。本次抽取实耗 **28s**（10:12:45 → 10:13:13），检索各阶段耗时同样不可测。

## 日志覆盖度小结

| 环节 | 覆盖 | 说明 |
| --- | --- | --- |
| 检索 trace | **好** | start/混合检索/召回/去重/重排/注入 六段齐全，含每条 id、相关/新鲜/重要/频次分、阈值公式、注入块全文 |
| 抽取写入 | 中 | 有 idle 触发、消息数、条数、记忆 ID；但无归属、无耗时、无逐条丢弃原因 |
| 归属判定 | **无** | D4 |
| 面板读（列表/筛选） | **无** | `memory_api.py:172/207`、`memory_presentation.py:184/195` 零日志 |
| 工具异常 | **无** | D2 |
| 噪音 | 需收敛 | `trace.py:92-110` 把整段注入块原文打进一条 INFO；`extractor.py:1261-1265` 打出 LLM 原始输出前 400 字符 |
| 日志级别 | 硬伤 | CLI sink 固定 `level="INFO"`（`nanobot/cli/commands.py:29-40`），`logger.debug` 全被丢弃；提取关键判断恰好在 debug 级 |

## 未验证项

- 项目选择器（`Choose project`）走服务端原生目录选择（`ThreadShell.tsx:1812` → WS mutation `workspace.pick_folder`），需人工在 OS 对话框选目录，自动化环境无法完成。故「会话绑定到 alpha-app/beta-service 项目」未走通；跨工作区隔离改由检索探针在生产栈上验证（结果见上表）。
- 记忆删除（Delete）按钮未点；Episodes 页签无数据。
- 未跑 pytest / vitest / ruff（本次不改业务代码）；引用的是 2026-10-27 collective-test 已有的后端测试结论。
- 未验证真实模型下的抽取质量（本次 LLM 抽取 2 条，内容正确，但样本量 1）。

## Next

- 建议优先级：修 D1（独立页开关状态，一行 useEffect 同步）→ D3（`trace.start` 打印可见集合 + 工作区过滤命中数）→ D4（`_persist` 打印 `row_workspace_id`）→ D2/D5。
- 报告与缓存目录：`.ai-runtime-artifacts/verifications/2026-10-28-memory-workspace-e2e-verification-lite.md`、`.ai-runtime-artifacts/verifications/memory-workspace-e2e/vite-cache/`。
- 测试实例仍在运行（网关 18771/18772、前端 5173），需清理时结束本会话后台任务即可。
