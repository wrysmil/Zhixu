---
artifact: verification-lite
route: Tier 1 Leader 直做（用户明确要求「方案 + 本轮直接实现」）
source:
  - .ai-runtime-artifacts/specs/2026-11-02-memory-observability-and-test-suites-spec.md
created_at: 2026-11-02
status: pass
---

# 记忆链路观测层实现验证（lite）

## 改动文件

| 文件 | 改动 |
| --- | --- |
| `nanobot/memory/obs.py`（新增） | 文本 + JSONL 双轨出口、配置 provider、截断、轮转、失败隔离 |
| `nanobot/config/schema.py` | 新增 `MemoryLoggingConfig`；`agents.defaults.memoryLogging` |
| `nanobot/memory/retrieval/trace.py` | 改走 `obs`；新增 `trace_id` 与结构化事件；截断/开关改为配置驱动；新增 `shortlist`（结构化命中） |
| `nanobot/memory/retrieval/engine.py` | `search_hits` 补 `retrieval_started` / `reranked(surface=hits)` / 耗时，自动注入路径也能关联 |
| `nanobot/memory/extractor.py` | 批次开始/完成/失败/跳过、`memory_changed` 逐条事件；`PersistenceResult.outcomes` |
| `nanobot/memory/context.py` | `RecalledMemorySection.query` 透传 |
| `nanobot/agent/context.py` | 注入环节 `injected` 事件（included/dropped/hint + 完整注入块） |
| `nanobot/cli/gateway_runtime.py` / `cli/agent.py` / `cli/commands.py` | 装配入口调用 `configure_from_config` |
| `tests/test_memory_obs.py`（新增） | 7 个单测：配置解析、截断、关闭、sink 重建、trace 关联、坏路径不崩 |

## 命令与结果

```text
$ uv run --no-sync ruff check <changed files>
All checks passed!

$ uv run --no-sync pytest -q tests/test_memory_obs.py
7 passed in 1.78s

$ uv run --no-sync pytest -q tests/memory tests/config
1058 passed, 10 warnings in 16.60s

$ uv run --no-sync python /tmp/verify_memory_obs.py   # 真实 MemoryExtractor + 真 SQLite
extraction: completed memories= 1
event kinds: ['extraction_started', 'extraction_completed', 'memory_changed',
              'retrieval_started', 'retrieval_skipped', 'injected']
extraction fields: {'session': 'websocket:batch', 'batch_id': '647d...', 'seq_start': 0, 'message_count': 2}
memory_changed: ['added']
text log lines: 27
```

## 证据要点

- 一次真实批次：`extraction_started` 带 seq 区间与消息数 → `extraction_completed` 带 memories/episodes/skipped → `memory_changed` 逐条 `added`。
- 检索：`retrieval_started` 生成 8 位 `trace_id`，`retrieval_skipped` 复用同一 id。
- 注入：`injected` 事件含 `ids / dropped / hint / chars`，文本日志打印完整注入块（含去重丢弃项）。
- `enabled=false`：文本与事件文件均不创建（单测 `test_disabled_writes_nothing`）。

## 实测日志回归（2026-11-02 21:48–21:51，桌面端真实会话）

现象与结论：

1. 提取链路完整：idle 120s 后 `【记忆提取·开始】→ 完成 → 4 条【记忆提取·写入】added`，与 `extraction_completed memories=4 episodes=1` 一致。
2. 暴露缺口：自动注入走的 `search_hits` 只发 `recall/dedupe`，没有 `retrieval_started`/`reranked`，导致注入侧 `trace=-`，无法与检索关联；首轮空结果时注入行缺失。
3. 已修复：`search_hits` 补 start/shortlist/elapsed；`RecalledMemorySection` 透传 `trace_id`；注入日志优先用载体上的 id（避免同回合工具检索覆盖 ContextVar）。
4. 复测：`ruff` 通过；`pytest tests/test_memory_obs.py tests/memory/retrieval tests/memory/test_memory_context.py` **226 passed**（新增 `shortlist` 用例）。

## WebUI 删除记忆报错修复（2026-11-02 22:5x）

真机现象：记忆中心点删除 → 红色 `WebUI mutation failed`。

复现与结论：

1. 直接调 `memory_api.delete_memory` 成功；经 WebSocket `memory.delete` 在无竞争时也成功。
2. 用独立连接 `BEGIN IMMEDIATE` 持写锁后，再发 `memory.delete`：等待 **5.2s** 返回 `500 WebUI mutation failed` —— 与截图一致。
3. 根因：SQLite 单写者 + `sqlite3.connect` 默认 5s busy timeout；idle 提取正在提交时删除撞锁，`OperationalError` 被上层兜成 500。

修复：

- `MemoryDatabase.connect`：`timeout=15.0` + `PRAGMA busy_timeout=15000`，让 UI 写操作等提取事务结束。
- `MemorySettingsHandler.handle`：`sqlite3.OperationalError`（locked/busy）→ 503 `memory store is busy; retry in a moment`；其他 `sqlite3.Error` → 500 带原因，不再是无信息 500。
- 回归：`tests/webui/test_memory_routes.py` 新增锁竞争映射用例。

验证：`tests/memory tests/test_memory_obs.py` **983 passed**；`tests/webui/test_memory_routes.py tests/webui/test_memory_api.py` **51 passed**。重启 gateway 后生效。

补充（同日 23:0x）：

- `DeleteConfirm` 增加 `titleIsSentence`：记忆/情节删除显示「删除这条记忆？」「删除这条对话记录？」，会话删除按名字套「删除「xxx」？」；`webui` 相关测试 10 passed，`tsc` 改动文件无错误。
- 清理 3 条同义重复记忆（保留最早 4 条）；另删除锁测试残留与模板 core-note。现库：memories=4、episodes=2、scratchpad=2、suppressions=0。备份 `~/.nanobot/workspace/memory-backup-2026-11-02-2304`。

已知无关项：`DeleteConfirm` 的 zh/en 文案固定为「删除这个话题？」（未使用传入 title），记忆/情节删除时标题会显示错；属 UI 文案问题，未改。混合运行 webui+memory 测试时存在既有 import 顺序问题（`ToolsConfig` forward ref 未 rebuild），与本修复无关。

## 已知边界

1. WebUI 设置页尚未暴露 `memoryLogging`（当前通过 `~/.nanobot/config.json` 配置；改配置文件后需重启 gateway；同一 config 对象被就地修改时免重启）。
2. `eventsEnabled` 默认关闭（按方案询问选择 A；需要机器可读证据时手动开启）。
3. 评估 worker 未装配 `obs`：不写产品日志文件，证据仍走平台 `memory.event`。
4. `nanobot/cli/commands.py` 的 import 顺序告警为 HEAD 已有问题，本次未改动该 import 块。

## Next

- 桌面端按 spec §3（A01–B08）跑手动集，重点看 `memory.log` 与面板是否一致。
- 需要 WebUI 开关/查看器时另开 spec（不改本轮代码）。
