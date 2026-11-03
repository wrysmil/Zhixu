# P2 — 每日盘账任务 + 衰减 + 预算

> 状态：已完成（2026-11-03）｜ 前置：P1（整理动作复用同一批工具与审计表）
>
> 实施说明：第 4 项「全局桶」按 P1 提交（4abd8364）的决策不实施——本仓 `scope`
> 全库恒 `global`，不引入 `workspace/global` 三态；其余三项均已落地，验证见
> `.ai-runtime-artifacts/verifications/2026-11-03-memory-refactor-p2-p3-verification-lite.md`。

## 目标

让记忆库从「只进不出」变成「有进有出有界」：

1. 每天一次的全库整理任务，出现在定时任务管理页面。
2. 衰减算法落地（字段早就有了，没人算）。
3. 注入侧设硬预算，防止海报无限变长挤爆上下文。
4. 加一个跨项目生效的全局桶。

## 现状与问题

| 事实 | 位置 |
|---|---|
| `decay_rate` / `expires_at` / `importance_score` 字段齐全，全仓无衰减计算 | `nanobot/memory/models.py` |
| `grep consolidat` 只命中枚举与 provenance，无整理实现 | — |
| Dream 每 2 小时跑，但只做增量，不做全库去重 | `nanobot/config/schema.py::DreamConfig` |
| 记忆按 `workspace_id` 隔离，无 global 概念 | `MemoryLifecycle.for_workspace` |
| `MEMORY_MD_MAX_CHARS` 已定义，但是否用于注入裁剪待确认 | `nanobot/identity/catalog.py::CHAR_LIMIT` |
| 定时任务页面已有「系统」筛选，可显示内置 job | `webui/src/components/settings/system/AutomationsSettings.tsx` |
| Dream job 不可删除、不可改名 | `nanobot/agent/tools/cron.py` 中 `job.name == "dream"` 的硬编码 |

## 改动清单

### 1. 新增日整理 job

在 `nanobot/cron/service.py` 注册内置 job `memory_consolidate`，默认每天 03:00
（本地时区，与 Dream 的 every 2h 完全独立）。

**不要改 Dream 的 `interval_h`。** Dream 靠 `.dream_cursor` 做增量，频率从 2h 降到 24h
意味着单次要吞 12 倍对话量，容易超出上下文窗口被截断，而且跑不完无法续跑。

两个任务的分工：

| | Dream（每 2h） | memory_consolidate（每天 1 次） |
|---|---|---|
| 输入 | history.jsonl 增量（cursor 之后） | 整个 `memories` 表 |
| 干什么 | 补抽候选、发现重复模式、生成技能 | 全库去重、衰减、过期归档、LLM 抽查 |
| 产出 | 记忆条目 + `skills/` | 记忆条目变更 + 审计记录 |
| 可中断 | 否（增量小） | 是，带 checkpoint 续跑 |

`memory_consolidate` 要有时限与续跑：`time_budget_seconds=1500`，
每处理 N 条写一次 checkpoint，超时后下次从 checkpoint 继续。openakita 就是因为
没做续跑，凌晨任务跑一半挂掉后永远停在半路。

### 2. 衰减算法

新增 `nanobot/memory/decay.py`，每日盘账时执行：

```
effective = importance_score - decay_rate * days_since_last_access
```

- `days_since_last_access` 按 `last_accessed_at` 算，无值按 `created_at`。
- `effective` 低于阈值（建议 0.15）→ 状态改为 `archived`，**不删除**。
- `type=PREFERENCE` 或 `type=RULE` 不参与衰减（用户偏好与规则没有「过期」概念）。
- `importance_score >= 0.9` 的条目豁免（对应 P1-7 那条教训：永久化的门槛要高）。
- 被检索命中即刷新 `last_accessed_at`，`access_count += 1`——
  这正好复用现有 `memory_search` 的热路径，让「常用的东西自动变重要」。

衰减必须写审计：每次盘账记录「衰减 N 条、归档 M 条」，页面上要能看到
「昨天有 12 条记忆因为 60 天没被用到被归档」，否则用户会觉得记忆凭空消失。

### 3. 预算裁剪

| 层 | 上限 | 超限怎么办 |
|---|---|---|
| `MEMORY.md` 注入 | 1500 字符 / 200 行，取先到 | 按段落优先级截断，**规则类优先保留**，偏好事实类后砍 |
| USER.md 注入 | 600 字符（已有 `user_profile_core` 上限） | 编译层已处理，本批只需确认 refresh 后必然触发编译 |
| 检索结果注入 | 约 500 token | `retrieval` 侧按综合分裁剪，超预算直接丢弃尾部 |

`select_core_facts` 取数时就要带上限，不要等渲染出来再截——截断会把半句话留在文件里。

### 4. 全局桶（scope）

`memories` 表增加 `scope` 语义（若已有则补全枚举）：

- `workspace`（默认）：只在当前项目可见
- `global`：跨项目可见，仅 `PREFERENCE` / `RULE` / 用户身份类可标 global
- 检索时按 `scope IN ('workspace', 'global')` 召回
- 用户显式说「所有项目都适用 / 永久记住」时由 `add_memory` 标 global

这是产品决策，需要你确认默认值。建议默认 `workspace`，宁可漏共享也不要串项目。

## 明确不做

- 不做向量库降级策略（P2 期间向量不可用时按关键词检索走，不新增降级链路）
- 不改 Dream 频率
- 不引入 LLM 逐条审查（openakita 的 daily_consolidator 有这步，成本高，
  本批只做规则级去重 + 衰减，LLM 抽查留到观察一阶段后再评估）

## 验收标准

### 自动

1. 新增 `tests/memory/test_decay.py`，不少于 8 条：
   - 60 天未访问、`importance=0.3`、`decay_rate=0.1` → 被归档
   - 同类但 `type=PREFERENCE` → 不衰减
   - `importance>=0.9` → 不衰减
   - 最近被检索命中 → `effective` 上升且 `last_accessed_at` 刷新
   - 盘账超时后能从 checkpoint 续跑，不重复处理已处理批次
   - `MEMORY.md` 渲染超 1500 字符时规则段仍在、尾部事实段被裁
2. `memory_consolidate` job 注册后可被 cron service 列出，`/api/automations` 返回该 job。
3. 归档操作全部落审计表。

### 手动

1. 打开「设置 → 自动化」，能看到 `memory_consolidate`，显示「每天 03:00」与上次运行结果。
2. 手动触发一次 → 面板显示：处理 N 条、合并 M 条、衰减 K 条、归档 J 条。
3. 灌一批构造数据（同一事实 3 种措辞）→ 跑一次 → 库里只剩 1 条，另 2 条 `superseded_by` 指向它。
4. 在项目 A 记一条 `scope=global` 的偏好 → 切到项目 B → `memory_search` 能召回。
5. 检查 `MEMORY.md` 字符数在任何情况下不超过 1500。

### 回归风险点

- 衰减跑错一次就是批量归档，**上线前必须 dry-run 模式**：先输出「将要归档哪些」不落库，
  人工确认后再开真跑。这个开关要留在配置里。
- `select_core_facts` 加预算后，`refresh_memory_md_sync` 与 `refresh_user_md_sync`
  两条投影路径都要跟着改，漏一条就会出现「MEMORY.md 短了但 USER.md 没短」。
- checkpoint 续跑与 `_derive_lock` 的交互要确认，避免盘账中途阻塞聊天侧的 refresh。

## 回滚

job 从 cron service 摘除；`decay.py` 加特性开关，关闭后不执行；
归档状态可通过 `archived = 0` 批量还原（归档不删除，数据仍在）。
