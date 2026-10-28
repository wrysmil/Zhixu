---
artifact: verification-lite
route: leader-direct | 修复实现
skills:
  - verification-before-completion
  - git-xywh
skills_evidence:
  - harness-kit/.agents/skills/git-xywh/SKILL.md
  - nanobot/cli/commands.py:29-40（loguru sink 口径）
source:
  - "修复"（针对 2026-10-28-memory-workspace-e2e-verification-lite.md 的 D1~D5）
created_at: 2026-10-28
tier: 1
---

# 记忆链路日志补齐 + 记忆页开关修复 — 轻量验证（Tier 1）

> **纪律：** 先跑命令、再给结论。下表全部为 Leader 本机实跑输出。
> 上游问题清单见 `.ai-runtime-artifacts/verifications/2026-10-28-memory-workspace-e2e-verification-lite.md`。

## 范围

12 个文件，+260/-21：

| 文件 | 改的是什么 |
| --- | --- |
| `webui/src/components/workspace/MemoryPage.tsx` | D1：把异步加载到的 `settingsSnapshot` 同步进本地 state |
| `webui/src/tests/memory-presentation.test.tsx` | D1 回归测试（回退即变红，已验证） |
| `nanobot/memory/retrieval/trace.py` | D3/D5：`start` 打可见集合；`hybrid` 拆分丢弃原因 + 隔离诊断；新增 `elapsed()` |
| `nanobot/memory/retrieval/engine.py` | D3/D5：`start` 传可见集合；检索全流程耗时 |
| `nanobot/memory/retrieval/store_adapter.py` | D3/D5：拆分丢弃原因；空结果时补诊断计数；FTS5 查询耗时 |
| `nanobot/memory/repository.py` | D3：新增 `count_semantic_matches_unfiltered()`（忽略可见性的命中计数，封顶 500） |
| `nanobot/memory/extractor.py` | D4：落库时打归属结论 + 归属项目路径；跳过原因分项统计；抽取耗时 |
| `nanobot/memory/retrieval/decomposer.py` | D2：LLM 拆解失败降级到规则不再静默 |
| `nanobot/memory/retrieval/channels/recent.py` | D2：近期通道「太旧丢弃」不再静默 |
| `nanobot/agent/tools/memory_search.py` | D2：检索异常、可见集合解析异常都留痕 |
| `nanobot/agent/context.py` | D2：Layer 4 注入失败的 warning 带上异常类型与内容 |
| `nanobot/agent/loop.py` | D2：同上 |

未改任何业务行为：新增的都是日志与计数；`search_semantic_scored` 的过滤判定逻辑逐条等价
（仅把丢弃原因从「一栏」拆成「两栏」）。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `pytest tests/ -q`（全量，后台补跑） | **9082 passed / 95 skipped / 4 failed**（735.85s） |
| `pytest tests/memory -q` | **953 passed** |
| `pytest tests/memory tests/agent -q` | 2629 passed / 1 skipped / **1 failed**（`test_mcp_reconnect_crash`，既有红灯，见下） |
| `npx vitest run`（webui 全量） | **1929 passed / 2 skipped / 0 failed** |
| `ruff check nanobot/` | 11 errors —— **与基线一致，0 新增**（基线同为 11） |
| `basedpyright`（10 个改动文件） | 消息级 diff **无差异，0 新增**（修掉了自己引入的 7 条） |
| 检索探针（生产栈 + 真实 DB） | 见「修复前后日志对比」 |
| 归属探针 | 见「修复前后日志对比」 |
| 浏览器复验 D1（深链直开 `#/memory`） | 开关 `aria-checked="true"`、标题「（On）」，与后端 `memory_enabled=true` 一致；再点一次 → UI `false` + 后端 `false`（**一次点击即真正关闭**，修复前首点是空写），再点恢复 `true` |

**4 个失败用例全部为既有/环境原因**（`git stash` 掉本批改动后复跑，逐一仍失败）：

| 失败用例 | 判定 |
| --- | --- |
| `tests/agent/test_mcp_reconnect_crash.py::test_mcp_reconnect_during_shutdown_does_not_crash` | 既有红灯（2026-10-27 collective-test 已记录同一条） |
| `tests/gateway/test_runtime.py::test_default_instance_preserves_released_gateway_paths` | 环境相关：本机主网关（PID 12296，10-27 17:24 起）仍持有 `~/.nanobot/run/gateway.json` |
| `tests/tools/test_exec_session_tools.py::test_exec_session_manager_shutdown_terminates_child_processes` | 环境相关：需真实起停子进程，与并行 Codex 会话抢进程资源 |
| `tests/tools/test_shell_reap.py::test_kill_process_tree_kills_descendant_after_root_exits` | 环境相关：同上（进程树回收） |

顺带确认隔离实例没有污染主运行时目录：实例网关的运行时描述符写在
`C:/Users/Huangqh/AppData/Local/Temp/zhixu-mem-e2e/run/gateway.3a72cbedd5fc669f.json`，
`~/.nanobot/run/gateway.json` 仍是 10-27 17:24 的原文件。

## 修复前后日志对比

### D3 —— 工作区隔离挡掉召回，从「看不出来」到「一眼看出来」

修复前（同一查询、同一 DB）：

```
  · FTS5(BM25) 0 条
      （无命中：FTS5 用 unicode61 分词器，不切分中文词，中文查询通常只能靠向量路）
  · 向量(余弦) 0 条
  · 并集 0 条（两路都命中 0 / 仅 FTS5 0 / 仅向量 0）
  · 按分排序取前 45 → 返回 0 条
```

修复后（alpha-app 视角查 beta 的记忆）：

```
【记忆检索·开始】
  入参 query  : 'FastAPI PostgreSQL 端口'
  可见集合    : default, 301ae182f96aece35fe34addf0049213
【混合检索·语义路】查询='FastAPI PostgreSQL 端口'（FTS5 ∪ 向量，同 id 取最高分）
  · FTS5(BM25) 0 条
  · 向量(余弦) 0 条
  · 并集 0 条
  · 诊断: 文本命中 FTS5=1 / LIKE=2（忽略可见集合）→ 命中但被工作区可见性挡掉
  · 按分排序取前 45 → 返回 0 条
【记忆检索·耗时】语义路 FTS5 查询 1.8ms
…
【记忆检索·耗时】检索全流程 29.0ms
```

诊断行把「文本没匹配上」和「匹配上了但被隔离挡掉」彻底分开——这正是真机复现里
agent 误判「记忆里没有」并转去全盘 `exec` 搜索 `deploy.sh` 的根因。

### D4 —— 归属判定结论进日志

```
INFO | memory attribution resolved: session=probe-alpha-app
      workspace_id=301ae182f96aece35fe34addf0049213
      project_path=C:\...\projects\alpha-app source=probe
INFO | memory extraction persisted: workspace_id=301ae182f96aece35fe34addf0049213
      memories=1 ids=['241b00a3-d534-4530-a741-7045a645c04d'] episodes=0
INFO | memory attribution resolved: session=probe-global workspace_id=default
      project_path=<none/global> source=probe
```

无工作区的会话明确打出 `default` + `<none/global>`，不再需要回查数据库才知道落哪了。

## 热路径开销（先实测再上）

新增的 `count_semantic_matches_unfiltered` 只在**可见集合内结果为空**时触发
（结果非空时零开销）。5 万行 FTS5 合成库实测：

| 场景 | 耗时（中位 / p95） | 计数结果 |
| --- | --- | --- |
| 宽泛词（命中全部行） | 0.388 / 0.342 ms | fts5=500, like=500（撞封顶） |
| 窄词 | 0.383 / 0.337 ms | fts5=500, like=500（撞封顶） |
| 词完全不存在 | 8.598 / 8.199 ms | fts5=0, like=0 |

最坏情况 8.6ms 来自 LIKE 全表扫（词一个都不匹配时无法提前退出），
且**只在空结果路径**发生——而空结果路径原本正是最容易被误判的路径。
对照：主查询的 LIKE 回退本来就在扫同一张表，诊断计数相当于把该段成本翻倍。
一个回合的 LLM 调用在秒级，8.6ms 占比 <1%。

参考对照（同一 5 万行库）：现有「工作区过滤 + 取行」21.7ms；不过滤的 COUNT 2.6ms。

## 截图证据

真机 Chrome（CDP 驱动，1440×1200）截取，落在 `.ai-runtime-artifacts/verifications/2026-10-28-memory-workspace-e2e/`：

| 截图 | 证明什么 |
| --- | --- |
| `01-memory-center-all.jpg` | 记忆中心全量：**Auto-remember 开关为 On**（D1 修复后与后端 `memory_enabled=true` 一致）、10 条记忆、每条卡片带**工作区归属标签**（Global memory / alpha-app / beta-service / workspace） |
| `02-memory-workspace-alpha.jpg` | 工作区筛选到 alpha-app：**4 条且全部归属 alpha-app**，beta-service 的记忆不再出现 |
| `03-memory-detail-dialog.jpg` | 记忆详情弹窗：来源 / 触发方式 / **工作区归属** / 创建更新时间 / 引用次数 |

（内置浏览器无可视视口、截不了图，故改用 CDP 驱动本机 Chrome；原始 PNG 在
`C:/Users/Huangqh/AppData/Local/Temp/zhixu-mem-e2e/shots/`。）

## 未验证项

- 未在**真实对话**里重跑一次完整抽取来确认新日志串起来的效果（需要 LLM + 120s 空闲触发）；
  归属日志已用探针在真实 DB 上验证等价路径。
- 未验证 `memory_search` 工具异常分支的日志（需构造注入故障）。
- webui 全量 vitest 在 HEAD 上有 1 个失败（本次改动后为 0 失败），未追查该失败项归属。

## Next

- 建议按项目规范由 Leader 提交（Angular 风格，中文标题），**不自动 push**。
- 遗留（未在本批处理）：CLI 的 loguru sink 固定 `level="INFO"`，`logger.debug` 全被丢弃；
  `trace.py` 的注入块全文进日志（单条可达数千字符），噪音仍偏大。
