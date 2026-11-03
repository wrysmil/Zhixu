# P1 — 记忆工具补全 + Dream 收编 + 删除 draft

> 状态：**已完成**（2026-11-03）｜ 前置：P0
>
> 末尾「实施记录」一节列出与原始计划的差异，先读那节。

## 目标

1. 给主 Agent 补「写 / 删 / 统计 / 立即整理」四个工具，让它能真正记事。
2. 把 Dream 从「文件编辑 Agent」改造成「记忆整理引擎」——产出全部进库，不再碰任何记忆文件。
3. 删掉 `MEMORY.md.draft` 这个孤儿产物。

## 现状与问题

| 事实 | 位置 |
|---|---|
| 记忆工具只有一个 `memory_search`，只读 | `nanobot/agent/tools/memory_search.py` |
| 写入与去重逻辑早已存在，缺的是工具壳 | `nanobot/memory/writer.py::apply_memory_changes` |
| Dream 拿的是文件工具，可写 SOUL.md / USER.md / MEMORY.md.draft | `nanobot/agent/memory.py::build_dream_tools` |
| draft 生成后没有任何「应用」入口，只有查询 | `nanobot/webui/memory_api.py` 四个 draft 查询函数 |
| Dream 与 extractor 对同一段对话重复劳动 | `build_dream_prompt` 读 history.jsonl；extractor 读 session turns |
| Dream 改文件的回滚依赖 git | `GitStore` + `/dream-log` + `/dream-restore` |

`writer.py` 已有的能力直接复用，不要重写：`_find_exact_duplicate`（内容哈希）、
`_active_same_attribute`（属性级比对）、`_apply_explicit_update`、`_forget`、
`_with_superseded_chain`、`_record_evidence`。

## 改动清单

### 1. 四个工具

`nanobot/agent/tools/` 新增，参照 `memory_search.py` 的 `Tool` 基类与 `ToolContext` 接入方式。

| 工具 | 参数 | 行为 | 风险等级 |
|---|---|---|---|
| `add_memory` | `content`、`type`、`importance`、`scope` | 走 `apply_memory_changes`，自动过写入侧去重 | 低 |
| `memory_delete` | `query`、`dry_run`、`confirm_token` | `dry_run=True` 先返回预览列表与 token；`dry_run=False` 带 token 才真删，走 RiskGate | 高 |
| `memory_stats` | 无 | 总数、类型分布、重要性分布、上次整理时间 | 低 |
| `consolidate_memory` | 无 | 立即触发一次整理（P2 落地前先调用现有的 refresh + 去重） | 中 |

工具描述里必须写明的两条（openakita 踩过的坑）：

- 用户说「永久记住 / 以后都要」时，保留时长必须显式传 `long_term`，不能留在默认值上——
  默认值的启发式只能识别少量句式，会漏判。
- 删记忆必须二次确认，模型不得自行跳过 `dry_run`。

**已定：不新增 `scope` 参数，改用 `retention`。** 本仓 `scope` 列全库恒为 `global`
（真实库 17/17），抽一个没人读的 `auto/global/session` 三态只会制造死配置。
`retention` 直接映射到已有的 `priority` + `expires_at`：`long_term` → 长期有效；
`session` → `short_term` + 24 小时后过期，而 `current_memory_sql` 本来就会把
`expires_at` 早于现在的记录判为无效——语义是现成的，不用新造。

**不要抄的**：`search_relational_memory`（图遍历，本仓无图数据模型）、
`trace_memory`（跨层导航，缺层与层之间的关联）、`get_session_context`
（`agent/tools/session_messages.py` 已有等价能力）。前端已有搜索面板，不必再给模型一个搜索工具。

### 2. Dream 换工具

`nanobot/agent/memory.py::build_dream_tools`

- 移除 `ReadFileTool` / `EditFileTool` / `WriteFileTool` / `ApplyPatchTool` 对 SOUL.md 与
  draft 的写权限，只保留 `skills/` 目录的读写（技能生成是 Dream 唯一的文件出口）。
- 注册第 1 步的四个工具，其中 `add_memory` 的 `provenance` 固定为 `dream`，
  让审计能区分「会话中记的」和「整理时补的」。
- `MemoryStore.build_dream_prompt` 的入参不变（仍读 history.jsonl 增量），
  **但 prompt 末尾追加当前核心记忆摘要**，替代原来靠 agent system context 注入全文的方式。

### 3. 删 draft（6 处）

| 位置 | 动作 |
|---|---|
| `agent/memory.py` `MemoryStore.__init__` | 删 `self.draft_file` |
| `agent/memory.py::build_dream_tools` | `editable_files` 只剩 `soul_file` |
| `memory/lifecycle.py::MemoryLifecycle.__init__` | 删 `self.draft_file` |
| `command/builtin.py` | 删「Dream 草稿已生成」提示，改为「Dream 已整理 N 条记忆」 |
| `webui/memory_api.py` | 删 `_draft_exists` / `_draft_age_seconds` / `_read_draft_content` / `get_memory_md_content` 中的 draft 分支 |
| 前端 MEMORY.md 卡片 | 删草稿提示区块 |

存量清理：启动时把 workspace 里的 `memory/MEMORY.md.draft` 归档到 `memory/legacy/`，不要直接删。

### 4. 审计表（补回 git 丢掉的能力）

新增 `memory_dream_runs` 表，一次 Dream 跑一批记录：

```
run_id, workspace_id, trigger, status,
entries_read, added, updated, deleted, skipped,
model, error, started_at, finished_at
```

- 登记时机：**跑之前**就插一行 `status='running'`。整理跑到一半崩了也要留下
  `failed`，否则「昨晚没动静」和「昨晚崩了」在页面上长得一样。
- 计数来源：记忆工具把 writer 的真实 outcome 累加进活跃运行句柄
  （`added` / `updated` / `deleted` / `skipped`），**不是模型的自述**。
- 主 Agent 的写入不计数：`active_run(workspace_id)` 查不到活跃运行就什么都不记。

`/dream-log` 改为读这张表；`/dream-restore` 已标记为不可用并说明替代做法
（见实施记录）。

### 5. WebUI

- 记忆面板删除：**本来就已有确认弹窗**（`MemoryListView` 的 `deleteCandidate` +
  `DeleteConfirm`），无需改动。
- `stats_payload` 的 `memory_md` 块：去掉 `draft_exists` / `draft_age_seconds`，
  新增 `last_consolidation`（时间 + 增删条数，来自审计表）。
- 前端没有消费 `memory-md/content` 这个端点（全仓 grep 无命中），所以
  「前端 MEMORY.md 卡片去草稿区」实际是空操作——已记录，不留待办。

## 明确不做

- 不加 `update_user_profile` / `skip_profile_question` / `get_user_profile`。
  USER.md 已是投影，给模型一个直写档案的工具等于开 bypass 后门，绕过去重与预算。
  用户的档案变更一律走 `add_memory(type=preference)`。
- 不改 Dream 的调度频率（`interval_h` 保持 2 小时），日整理是 P2 的新 job。
- 不抄 openakita 的 `search_relational_memory` / `trace_memory` / `get_session_context`。

## 实施记录（2026-11-03）

### 与计划的差异

| 计划 | 实际 | 原因 |
|---|---|---|
| `scope` 参数 auto/global/session | `retention` 参数 long_term/session | 本仓 `scope` 全库恒 `global`，三态是死配置；`retention` 映射到已有的 `priority` + `expires_at` |
| Dream 保留 `SOUL.md` 写权限 | **一并移除** | 价值观文件不该由每两小时的定时任务自己改；提示词里「Never delete: behavioral rules in SOUL.md」同步改为 `rule` 型记忆 |
| 不动 `dream.md` | 做了 5 处最小改写 | 原计划担心「提示词和工具对不上」，但删 draft 不改提示词正是对不上——所以只改路由表/draft 段/Editing/Verification/结尾五处，MECE 与删除规则整段保留 |
| 审计表放在独立模块 | 同计划，但活跃运行用模块级表 | Dream 有两个执行入口（cron 回调 + `/dream`），逐层透传要改三处签名；Dream 每进程串行、小时级频率，按 workspace 的活跃表足够 |
| `memory_delete` 走 RiskGate | 自建 `confirm_token` | 全仓无 RiskGate / confirm_token 基础设施；token 由「工作区 + 查询 + 候选 id 集合」派生，候选集一变即失效 |

### 一处需要留意的设计取舍

`add_memory` 的 `evidence_key` 是
`trigger:session_key:turn_id:内容哈希`——同一轮里重试同一条不重复入库。
代价是**跨轮重复调用会被 writer 的 L1 精确去重挡住**（outcome=`unchanged`），
这正是想要的行为；但如果你希望「用户三天内强调三次」能累加 confidence，
需要 P2 的整理任务按语义合并，工具层不做这件事。

### 顺带修掉的死代码

`command/builtin.py` 里 `_extract_changed_files` / `_format_changed_files` 与
`CommitInfo` 导入只服务于 `/dream-log` 的 git diff 输出，随该命令重写一并删除。

## 验证结果（2026-11-03）

### 自动

新增/改写的用例：

| 文件 | 条数 | 钉住什么 |
|---|---|---|
| `tests/agent/test_memory_admin_tools.py` | 22 | 写库不写文件、幂等、`retention` 语义、删除两段确认、token 失效、门控开关、schema |
| `tests/agent/test_dream_audit.py` | 13 | 审计表记账、workspace 隔离、`summary()`、Dream 工具白名单、draft 归档、dream.md 改写 |
| `tests/agent/test_dream.py` | 49 | Dream 工具集合（4 文件 + 4 记忆）、三个记忆文件全部拒写、经 `add_memory` 落库并投影回 MEMORY.md |
| `tests/command/test_builtin_dream.py` | 18 | `/dream` 文案改为整理摘要、`/dream-log` 读审计表、`/dream-restore` 说明按条删除 |
| `tests/memory/test_lifecycle_integration.py` | 7 | draft 属性已移除、legacy draft 归档、`get_memory_md_content` 无 draft 字段 |
| `tests/webui/test_memory_api.py` | 35 | `memory_md` 块字段集变化、`last_consolidation` |

全量：`tests/` 8236 passed / 11 failed，**11 条全部在改动前的 HEAD 上可复现**
（4 条 cli serve + 1 条 gateway 路径 + 4 条既有失败 + 2 条进程回收测试），
与本批无关。

### 真数据干跑

拿真实 workspace（17 条记忆）的副本跑完整链路：

1. `add_memory` 写入 → 库中 18 条，返回「已记住」；
2. 同内容再写 → 「这条已经在记忆库里了，没有重复添加」；
3. `memory_stats` → 类型/来源分布 + 上次刷新时间；
4. `memory_delete` 预览列出候选 + token；不带 token 真删被拒；带 token 删掉 1 条；
5. Dream 口径（`source=consolidation`）写一条 → 审计行 `added=1`、`status=completed`；
6. `consolidate_memory` → 两个投影文件重算，MEMORY.md 出现新写入的「项目用 pnpm 管包」。

### 工具注册验证

`ToolLoader` 自动发现：开关打开时注册 `add_memory` / `memory_delete` /
`memory_stats` / `consolidate_memory`；`memory_write_enabled` 返回 False 时
一个都不注册（与 `memory_search` 同模式）。

### 仍未手工验证（需要你启动应用过一遍）

1. 对话里说「记住我不吃香菜」→ 库中出现该条、面板能搜到、USER.md「协作偏好」出现该行；
2. 说「整理一下记忆」→ 返回新增/更新条数；
3. 面板删除一条 → 下次刷新后 MEMORY.md / USER.md 该行消失；
4. 触发一次 `/dream` → `git status` 只有 `skills/` 可能变化，三个身份文件无变化；
5. WebUI 设置页记忆统计里能看到「上次整理」一行。

> 注意：老 workspace 根的 `USER.md` 与 `memory/MEMORY.md.draft` 都不会被删除。
> draft 会在下次构造 `MemoryStore` 时归档到 `memory/legacy/`。

## 回滚

四个工具从 `ToolLoader` 的自动发现范围移除即可（删 `memory_admin.py`）。
Dream 工具白名单恢复需连带恢复 `command/builtin.py` 的文案与
`templates/agent/dream.md` 的路由表，否则提示词与工具会对不上。
审计表 `memory_dream_runs` 可留：无写入方时它只是张空表。

### 自动

1. `pytest tests/` 全绿，新增用例不少于 8 条：
   - `add_memory` 写入后库中可查、`memory_search` 能召回
   - 同一内容连续两次 `add_memory` 只产生一条记录，`confidence` 上升而非新增行
   - `memory_delete` 不带 `dry_run=True` 的预览 token 直接删除 → 被拒绝
   - `dry_run=True` 不产生任何写入
   - Dream 跑完后 workspace 内不出现 `MEMORY.md.draft`
   - Dream 跑完后 `SOUL.md` 与 `identity/USER.md` 的 mtime 不变
   - 审计表每次 Dream 运行新增一行，计数与实际变更一致
   - 迁移把历史 draft 归档到 `memory/legacy/` 且不删除原文件
2. `build_dream_tools()` 返回的注册表内不含任何可写 SOUL.md 的工具。

### 手动

1. 对话里说「记住我不吃香菜」→ 库里出现该条，面板能搜到。
2. 说「整理一下记忆」→ 后台跑一次 → 面板显示新增 / 合并 / 删除条数。
3. 在面板删除一条 → 走预览 → 确认 → 库里标记 superseded，`MEMORY.md` 下次刷新后该行消失。
4. 触发一次 Dream → `git status` 显示只有 `skills/` 下可能有变化，三个身份文件无变化。

### 回归风险点

- Dream 从「有文件工具」变成「只有记忆工具」后，如果 `add_memory` 的参数校验过严，
  它会反复重试同一批次，烧 token。需要在 `add_memory` 的返回里给出明确的失败原因文案。
- `apply_memory_changes` 原本由 extractor 内部调用，改为工具暴露后要确认线程安全
  （`writer.py` 的连接生命周期与工具调用线程是否同一）。
- `command/builtin.py` 里 `/dream-*` 系列命令的文案与测试断言需同步更新。

## 回滚

工具从注册表摘除即可，无数据迁移。Dream 工具白名单与 draft 恢复为原样需连带恢复
`command/builtin.py` 的文案，否则 `/dream-log` 输出与实现不符。
