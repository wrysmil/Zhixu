# 知序记忆系统

知序的长期记忆以**文件为本体**：一条记忆一个 md 文件，同目录的 `MEMORY.md` 是**目录索引**。
SQLite 退为检索索引，只服务 `memory_search`（翻历史档案），不再驱动注入内容。

## 目录结构

| 作用域 | 路径 | 内容 |
| --- | --- | --- |
| 全局 | `~/.nanobot/memory/` | 与项目无关的个人偏好、协作习惯、长期规则 |
| 项目 | `~/.nanobot/projects/<workspace-id>/memory/` | 只对当前项目成立的事实与约定 |
| 检索库 | `~/.nanobot/workspace/memory/state.db` | 单库 + `workspace_id` 列，只服务 `memory_search` |
| 画像 | `~/.nanobot/workspace/identity/USER.md` | 手写区（用户维护）+ 自动区（从全局记忆文件整理） |

`<workspace-id>` 由工作区内的 `.nanobot/workspace-id` 决定（`memory_workspace_id()`），
改名/移动后不变，复制目录会换新 id。

## 记忆文件格式

```markdown
---
name: prefer-direct
description: 用户希望解释机制时说清因果
metadata:
  type: user
---

解释机制先讲为什么，再讲怎么做。
```

- `name` = 文件名（kebab-case；全中文名称退化为内容哈希，保证同名追加语义）。
- `description` = 一句话钩子，进索引行。
- `metadata.type` = `user` / `feedback` / `project` / `reference`。

同名文件**追加**正文（不整份覆盖）；删除即删文件；新增/删除都要同步同目录索引。

## MEMORY.md 索引

每个记忆目录一份 `MEMORY.md`，一行一条：

```markdown
- [解释机制先讲为什么](prefer-direct.md) — 用户希望解释机制时说清因果
```

每轮对话注入两份索引（全局 + 当前项目，合计 ≤ 2000 字符）+ `USER.md` 全文。
索引只给标题和钩子，**正文要模型按需 `read_file`**；更早的记忆用 `memory_search`。

## 写入分工

| 角色 | 写记忆文件 | 写 SQLite |
| --- | --- | --- |
| 对话内主动写（用户要求「记住…」） | ✅ 模型用 `write_file` / `edit_file` | ❌ |
| 定时抽取（cron `memory_extract`，默认 30 分钟） | ✅ 增量追加 | ✅ 同步一条记录 |
| Dream（cron `dream`，默认 2 小时） | ✅ 整理（合并 / 替换 / 修剪 / 归类 / 索引） | ❌ |
| `memory_consolidate`（每日 3 点） | ❌ | ✅ 衰减 / 归档 |

- 定时抽取的作用域判定：`preference` / `rule` → 全局目录；其余 → 项目目录。
- 重要性门槛：全局目录要求 `importance ≥ 0.7`，项目目录要求 `≥ 0.5`（抽取产物没有
  `confidence` 字段，用 `importance` 代替）。
- 文件写入共用一把写锁（`memory_file_lock()`），定时抽取与 Dream 不会互相覆盖。

## 注入

每轮 system prompt 里与记忆相关的部分：

1. `USER.md` 全文（画像；自动区由全局记忆文件渲染，手写区由用户维护）。
2. 工作记忆（scratchpad）与检索命中（`memory_search` 结果）。
3. 两份 `MEMORY.md` 索引 + 目录绝对路径。

SQLite 的「核心事实」投影**不再注入**（`select_core_facts` 只留给 WebUI 面板与评测）。

## 开关与回滚

| 配置 | 默认 | 作用 |
| --- | --- | --- |
| `memoryFileMode` | `true` | 关闭后回到旧的 SQLite 注入路径 |
| `memoryExtractIntervalMinutes` | `30` | 定时抽取间隔（最小 5） |

旧版 `<workspace>/memory/MEMORY.md` 会在启动时**一次性迁移**进记忆目录：旧文件备份到
`~/.nanobot/memory/legacy/MEMORY.md`，核心视图按分区拆成记忆文件，手写笔记区落成一条
全局记忆。迁移幂等，重复启动不会重复导入。

## 记忆库（SQLite）

默认路径：`<agent_workspace>/memory/state.db`。

| 表 | 用途 |
| --- | --- |
| `memories` | 跨会话复用的事实、偏好、规则、经验（检索用） |
| `episodes` | 一次任务/会话的目标、过程、结果摘要 |
| `scratchpad` | 当前工作状态、聚焦任务和下一步 |

检索工具 `memory_search` 读取 SQLite；自动检索也会直接查询 SQLite，不依赖记忆文件。

## 自动抽取边界

必须保存：

- 用户明确陈述的长期身份、背景、偏好和稳定约束。
- 用户明确确认的项目状态、架构决策、业务边界。
- 有用户确认或工具结果证据的可复用经验、成功流程和失败教训。

必须丢弃：

- 问题、回忆请求、知识测试、任务指令、假设和待验证方案。
- 助手自己的计划、建议、推测和复述。
- `memory_search` 结果、系统状态、scratchpad 和工具输出本身。
- 一次性事故经过、临时文件名、任务产物和寒暄；这类内容由 `episodes` 承载。

重要区分：用户说“我们以前处理过类似问题，你能找回当时确认的方案吗？”时，
前半句是**问题前提/回忆请求**，不是新的用户画像事实，不能据此生成
“用户此前处理过……”的记忆。

未独立验证的用户陈述应以“用户报告/用户表示”保留归属，不能写成客观事实。

## 召回

`memory_search` 和自动注入共用 `RetrievalEngine`：

1. 查询预处理：过滤空输入和控制词。
2. 查询准备：保留原句、路径、文件名和英文标识符；中文不做模型改写。
3. 语义召回：FTS5 + LIKE 回退；FTS5 不分中文词时，回退会用有界的四字窗口
   匹配自然语言中的项目名/实体。
4. 情节召回与 RRF 融合、排序、预算裁剪。
5. 注入内容明确标记为不可信数据。

`memory_search` 的召回结果与记忆文件是否为空无关。

## history.jsonl

`memory/history.jsonl` 是压缩历史摘要的追加式归档，用于搜索过去会话。它不是长期
事实的唯一来源，也不决定 `memory_search` 是否命中。
