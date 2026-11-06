# 知序记忆系统

知序有两条互不复制的主存储：`MEMORY.md` 保存长期规则与项目约定（直接写正文、每轮注入），SQLite 保存沉淀知识（按需检索）。同一条内容只进一个存储，不做文件/SQLite 同步。

沉淀知识按 `kind` 分为五类，全部存在 `episodes.metadata.kind`，不新增表：
`experience`（任务经过）、`skill`（可复用步骤）、`pitfall`（踩坑：症状 → 修复）、
`decision`（方案取舍，支持 supersede）、`reference`（外部指针，不复制正文）。

## 目录结构

| 作用域 | 路径 | 内容 |
| --- | --- | --- |
| 全局长期记忆 | `~/.nanobot/memory/MEMORY.md` | 与项目无关的个人偏好、协作习惯、长期规则 |
| 项目长期记忆 | `~/.nanobot/projects/<workspace-id>/memory/MEMORY.md` | 只对当前项目成立的约定与事实 |
| 经验库 | `<agent_workspace>/memory/state.db` → `episodes` | 五类沉淀知识；`source=experience`，类型在 `metadata.kind` |
| 画像 | `<agent_workspace>/identity/USER.md` | 独立身份文件，用户维护；不再从记忆正文推导 |

`<workspace-id>` 由工作区内的 `.nanobot/workspace-id` 决定（`memory_workspace_id()`），
改名/移动后不变，复制目录会换新 id。

## MEMORY.md 正文格式

`MEMORY.md` 直接保存正文，不再维护“一行一条”的索引。按二级标题分小节：

```markdown
# 项目记忆

## 项目约定
- 构建用 bun（2026-11-06）
- 端口固定 8765

## 构建与运行
详见 build-and-run.md
```

- 全局 `MEMORY.md` 上限 3000 字符，项目 `MEMORY.md` 上限 6000 字符。
- 超预算时把长文拆到同目录独立文件（如 `conventions.md`），在 `MEMORY.md` 留一行 `详见 conventions.md`。
- 不要用 `write_file` 直接改 `MEMORY.md`；用 `memory_write`，它负责上限校验和原子写入。

## 写入分工

| 角色 | 写 `MEMORY.md` | 写 SQLite |
| --- | --- | --- |
| 对话内主动写（用户要求「记住…」） | ✅ `memory_write` | ❌ |
| 经验/踩坑/技能/决策/参考（用户确认或主动沉淀） | ❌ | ✅ `experience_record(kind=...)` → `episodes` |
| 定时抽取（cron `memory_extract`，默认 30 分钟） | ✅ 长期事实写成 bullet | ❌（experience/error/skill 仍走原路径） |
| Dream（cron `dream`，默认 2 小时） | ✅ 整理正文 / 拆分长文 | ❌（只读复盘经验） |
| `memory_consolidate`（每日 3 点） | ❌ | ✅ 只处理 `memories`，不碰 `episodes` |

- 长期事实作用域：`preference` / `rule` → 全局；`fact` → 项目。
- 经验不设过期时间、不参与自动衰减；冷数据只影响排序。
- 文件写入共用一把写锁（`memory_file_lock()`），定时抽取与 Dream 不会互相覆盖。

## 注入

每轮 system prompt 里与记忆相关的部分：

1. 全局 + 项目 `MEMORY.md` 正文（超过预算的长文按需 `read_file` 读同目录独立文件）。
2. `USER.md`（独立画像文件）。
3. 工作记忆（scratchpad）与经验检索命中（`memory_search`）。

SQLite 的旧「核心事实」投影**不再注入**；`memories` 表迁移后只保留归档行。

## 开关与迁移

| 配置 | 默认 | 作用 |
| --- | --- | --- |
| `memoryFileMode` | `true` | 新链路只在文件模式启用；关闭后回到旧 SQLite 路径 |
| `memoryExtractIntervalMinutes` | `30` | 定时抽取间隔（最小 5） |
| `memorySearchBackend` | `fts5` | 设为 `chromadb` 时启用向量层（memories + episodes 两个集合） |
| `memoryPitfallHint` | `false` | 工具报错时主动检索 active pitfall，附一行“仅供参考”提示 |
| `memoryVector.syncOnStartup` | `true` | 启动时后台回填 memories / episodes 向量索引 |

旧 `memories` 表迁移（先预演，再执行）：

```bash
python -m nanobot.memory.migrate_memories --dry-run
python -m nanobot.memory.migrate_memories --apply --workspace-id default
```

迁移规则：

- `fact/preference/rule` → `MEMORY.md`；`experience/error` → 经验库；`skill` 只报告。
- 短期待办/已过期行只归档。
- 旧行不物理删除：`expires_at` 设为 `1970-01-01T00:00:00Z`，`metadata.migrated` 保留文件/经验指针。

## 记忆库（SQLite）

默认路径：`<agent_workspace>/memory/state.db`。

| 表 | 用途 |
| --- | --- |
| `episodes` | 一次任务/会话的过程与五类沉淀知识；`source=experience` 且 `metadata.kind` 区分类型 |
| `memories` | 旧长期事实表；迁移后只保留归档行，不再作为长期记忆主本 |
| `scratchpad` | 当前工作状态、聚焦任务和下一步 |

`episodes.metadata` 的公共字段：`title` / `kind` / `status`（active / deprecated / superseded）；
类型专有字段：skill 的 `capability`、`prerequisites`、`steps`、`version`，pitfall 的
`symptom`、`fix`，decision 的 `context`、`options`、`choice`、`rationale`，reference 的
`pointer`、`what_for`。旧行没有 `kind` 时按 `experience` 处理。

`memory_search` 支持 `kind` / `status` / `outcome` 过滤，三个条件共用同一条检索管道；
带过滤时只走 episodes 通道。长期规则直接读 `MEMORY.md`，不走经验检索。

### 经验向量

`memorySearchBackend=chromadb` 时，ChromaDB 使用两个集合（同一个 client/模型）：

| 集合 | 内容 |
| --- | --- |
| `memories` | 旧长期事实（仅归档数据） |
| `episodes` | `source=experience` 的沉淀知识，文档为 summary |

- 写入：`record_experience` 成功后 best-effort upsert；更新/删除同步维护。
- 回填：启动时后台按集合 id 与 SQLite 差集补 missing / 删 stale。
- 召回：episodes 通道先取词法与向量两路，RRF 融合后回表按 workspace / kind /
  status / outcome 过滤；向量不可用时自动退化为纯词法。
- 集合可随时删除重建，SQLite 始终是本体。中文召回靠该向量模型（`bge-small-zh-v1.5`）。

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
4. 情节召回：词法投影 ∪ 向量（启用时），先做通道内 RRF，再与语义通道做
   跨通道 RRF、排序、预算裁剪。
5. 注入内容带类型标签（`[踩坑]` / `[技能]` 等），并明确标记为不可信数据。
6. `kind` / `status` / `outcome` 过滤只约束 episodes 通道；带过滤时不召回路
   `memories`，避免类型语义被旧数据污染。

`memory_search` 的召回结果与记忆文件是否为空无关。

## history.jsonl

`memory/history.jsonl` 是压缩历史摘要的追加式归档，用于搜索过去会话。它不是长期
事实的唯一来源，也不决定 `memory_search` 是否命中。
