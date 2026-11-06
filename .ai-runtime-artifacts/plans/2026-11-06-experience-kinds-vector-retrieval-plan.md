---
artifact: implementation-plan
route: superpowers:writing-plans
skills: []
skills_evidence:
  - ~/.agents/skills/writing-plans/SKILL.md — 本机未安装，按 harness-kit/artifact-templates/plan.harness-overlay.md 手工落盘
dispatch: n/a
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - docs/memory.md
  - .ai-runtime-artifacts/verifications/2026-11-06-memory-experience-full-implementation.md
created_at: 2026-11-06
status: approved
approved: true
---

# 经验库类型扩展 + Episodes 向量召回 实施计划

## Goal

在**不新增任何 SQLite 表**的前提下，把现有 `episodes`（`source=experience`）扩展成可区分类型的经验库，并给它补上语义召回：

| kind | 回答的问题 | 检索方式 | 生命周期 |
| --- | --- | --- | --- |
| experience 经验 | 上次类似任务怎么做的 | 任务相似度 + outcome | 长期保留，可合并去重 |
| skill 技能 | 这类事的标准步骤 | 按能力/前置条件召回 | 多次成功经验升级而来，需版本与弃用 |
| pitfall 踩坑 | 出现这个症状先查什么 | 按错误文本/症状召回，命中时主动提示 | 可升级成项目规则写进 MEMORY.md |
| decision 决策 | 为什么选了这个方案 | 按项目 + 主题召回 | 支持 supersede，新旧并存 |
| reference 参考 | 细节去哪看 | 按标签/主题 | 只存指针，不存正文 |

同时把 episodes 从“纯 LIKE 子串”升级为“词法 ∪ 向量，RRF 融合”，复用现有 `BAAI/bge-small-zh-v1.5`（512 维，本地已下载）与 ChromaDB 基础设施。

## 硬约束（用户明确要求）

1. **不新增表**：不建 `knowledge` 表、不建 episodes FTS 虚拟表、不建新的同步状态表。类型只写进 `episodes.metadata`，过滤用表达式索引。
2. **不重复存储**：稳定规则仍写 `MEMORY.md`；可执行技能仍是 `SKILL.md`；episodes 只存“发生过什么 + 怎么解的”证据。经验升级为规则必须用户确认。
3. **向后兼容**：现有 `source='experience'` 行没有 `metadata.kind`，读取时一律按 `experience` 处理，不做数据迁移。
4. **向量只做派生索引**：SQLite 仍是本体，向量集合可随时重建/删除；向量不可用时自动降级为词法召回。
5. **不采用 trigram FTS**：SQLite FTS5 需要新建虚拟表，违反约束 1；中文召回交给向量，LIKE 作为无模型时的兜底。

## 现状与代码锚点

| 能力 | 现状 | 位置 |
| --- | --- | --- |
| 经验写入 | `record_experience` 固定写 `source=experience`，字段塞 `metadata` | `nanobot/memory/experience.py` |
| 经验列表/过滤 | `list_experiences` 只支持 workspace / outcome / LIKE 查询 | 同上 |
| 检索通道 | semantic（memories） + episodes（LIKE 投影），RRF 融合 | `nanobot/memory/retrieval/channels/` |
| 工具检索 | `memory_search` 的 `outcome` 分支绕过引擎，直接 `list_experiences` | `nanobot/agent/tools/memory_search.py` |
| 向量 | 单一 collection `memories`，只索引 `memories` 表 | `nanobot/memory/vector/store.py`、`indexer.py` |
| 会话注入 | 每轮注入 MEMORY.md + 工作记忆 + 检索命中 | `nanobot/agent/context.py` |
| Dream | `experience_query` 只读复盘 | `nanobot/agent/tools/experience.py`、`agent/memory.py` |

## 设计决策

### D1. 类型存哪里

- `source` 保持 `'experience'` 作为“人工沉淀库”标记，避免改枚举和迁移旧数据。
- `metadata.kind ∈ {experience, skill, pitfall, decision, reference}`，缺省按 `experience`。
- `metadata.status ∈ {active, deprecated, superseded}`，缺省 `active`。
- 公共字段：`title`、`kind`、`status`、`tags`、`verification`、`version`、`supersedes`、`superseded_by`。
- 每类专有字段见 §D2。

### D2. 每类的必填字段与 summary 组成

| kind | 必填 | summary 组成（供 FTS/LIKE/向量） |
| --- | --- | --- |
| experience | title, situation, solution | situation + 根因 + 解法 |
| skill | title, capability, steps | capability + 前置条件 + 步骤 + 验证 |
| pitfall | title, symptom, fix | 症状 + 根因 + 修复 + 验证 |
| decision | title, context, choice | 背景 + 选项 + 结论 + 理由 |
| reference | title, pointer | 用途 + 指针（不复制正文） |

`record_experience` 保留为 `kind="experience"` 的兼容入口；新增通用参数，按 kind 校验必填项。

### D3. 检索语义

- `kind` / `outcome` / `status` 是**同一管道的过滤维度**，不是新的检索路径。
- 带过滤条件时只走 episodes 通道（semantic 通道检索的是 `memories`，没有 kind），结果必须带类型标签。
- 不带过滤时保持现状：semantic + episodes 两通道，RRF 融合。
- `memory_search` 不再保留独立的 `list_experiences` 快捷分支；`list_experiences` 只服务 WebUI / Dream。

### D4. Episodes 向量

- 新增 ChromaDB collection `episodes`，与 `memories` 共用同一个 client、模型和 index path（避免双实例撞锁）。
- 文档内容按 D2 的 summary 组成，截断到模型可接受长度；`reference` 的摘要只嵌用途，不嵌外部正文。
- 写入：`record_experience` 成功后 best-effort upsert；失败只记日志。
- 删除：`delete_episode` 路径同步删向量；对账按 collection 内 id 与 `episodes` 表 id 求差集。
- 回填：启动后后台执行一次（collection 为空或显式触发）；不落同步状态表，靠集合 id 列表判断。
- 召回：向量取 `limit*3` 候选 → 回表 `episodes` 做 workspace / kind / outcome / status / source 过滤 → 与词法结果做 RRF。

## 接口契约

### 工具 `experience_record`

```json
{
  "kind": "experience | skill | pitfall | decision | reference",
  "title": "string",
  "status": "active | deprecated | superseded",
  "situation": "string",
  "action": "string",
  "outcome": "failed | partial | completed",
  "root_cause": "string",
  "solution": "string",
  "verification": "string",
  "capability": "string",
  "prerequisites": ["string"],
  "steps": ["string"],
  "symptom": "string",
  "fix": "string",
  "context": "string",
  "options": [{"name": "string", "pros": "string", "cons": "string"}],
  "choice": "string",
  "rationale": "string",
  "pointer": "string",
  "what_for": "string",
  "version": "string",
  "supersedes": "string",
  "tags": ["string"]
}
```

必填项按 kind 校验（D2）；`reference` 不要求 situation/outcome。

### 工具 `memory_search`

新增可选参数：`kind`、`status`；与现有 `outcome` 可组合。输出每条带 `[kind/status]` 标签。

### Dream 工具 `experience_query`

新增 `kind`、`status` 过滤；仍然只读。

### WebUI

`GET /api/settings/memory/experiences?kind=&status=&outcome=&q=`；列表卡片显示类型徽标与状态，详情按 kind 渲染不同字段。

## 实施阶段

### Phase 0 — 基线与索引（0.5 天）

1. 在 `nanobot/memory/database.py::init_schema` 增加表达式索引：
   `CREATE INDEX IF NOT EXISTS idx_episodes_kind ON episodes(json_extract(metadata,'$.kind'))`。
2. 跑现有 `tests/memory/test_memory_experience_mvp.py`、`tests/webui/test_memory_api.py` 记录基线。
3. 用临时库验证 SQLite 版本支持该表达式索引。

**验收**：现有测试全绿；`EXPLAIN QUERY PLAN` 命中新索引。

### Phase 1 — 写入模型（1 天）

文件：`nanobot/memory/experience.py`、`nanobot/agent/tools/experience.py`、`nanobot/templates/AGENT.md`、`nanobot/templates/agent/identity.md`。

1. 增加 `KINDS` / `STATUSES` 常量与 `kind` 校验。
2. `record_experience` 增加 kind 与专有参数，按 D2 组装 summary/metadata/tags；`tags` 额外带 kind/status。
3. `experience_record` 工具增加参数与描述，明确“什么情况记哪类”。
4. 提示词补充分流规则与主动询问边界：
   - 失败且定位到根因 → pitfall；
   - 同类任务第二次成功且步骤稳定 → 提议 skill；
   - 架构/方案取舍 → decision；
   - 用户给出外部文档/路径 → reference；
   - 其余一次性任务 → experience。
5. `migrate_memories` 保持默认 `experience`，不改迁移逻辑。

**验收**：每类最小样例可写入并读回；缺必填项报可读错误；旧调用不传 kind 行为不变。

### Phase 2 — 检索过滤与标注（1.5 天）

文件：`nanobot/memory/experience.py`、`nanobot/memory/repository.py`、`nanobot/memory/retrieval/channels/episodes.py`、`nanobot/memory/retrieval/engine.py`、`nanobot/agent/tools/memory_search.py`、`nanobot/agent/tools/experience.py`、`nanobot/webui/memory_api.py`、`nanobot/webui/memory_routes.py`。

1. `list_experiences` 支持 `kind`、`status`；`ExperienceRow` 增加 kind/status/version。
2. `repository.search_episodes` 增加同样的可选过滤（SQL 层 `json_extract`）。
3. `RetrievalEngine.retrieve/search_hits` 增加 `kind`、`status`、`outcome`；带过滤时只启用 episodes 通道。
4. `hit_from_episode` / formatter 输出 `[pitfall/active]` 标签与 title。
5. `memory_search` 删除独立 outcome 分支，统一走引擎；`experience_query` 增加过滤。
6. WebUI `experience-list` 增加 kind/status 参数；先只做后端，UI 在 Phase 5。

**验收**：同一 query 下 `kind=pitfall` 只返回踩坑；自动注入与工具检索结果一致；旧 `outcome` 行为不变。

### Phase 3 — Episodes 向量（2 天）

文件：`nanobot/memory/vector/store.py`、`nanobot/memory/vector/indexer.py`（或新增 `episode_indexer.py`）、`nanobot/memory/experience.py`、`nanobot/memory/retrieval/channels/episodes.py`、`nanobot/cli/gateway_runtime.py`、`nanobot/webui/memory_api.py`。

1. `VectorStore` 支持按名字取集合，新增 `upsert_episode`、`search_episodes`、`delete_episode_ids`、`list_episode_ids`，共用已加载模型。
2. 新增 `EpisodeVectorIndex`：索引/删除/回填；回填分批（每批 50），失败只记日志。
3. `record_experience` 写库后 best-effort 索引；`delete_episode` 的调用方同步删向量。
4. episodes 通道：词法结果 ∪ 向量结果，RRF 融合；向量候选回表过滤 workspace/kind/outcome/status。
5. 启动回填：`memory_search_backend=chromadb` 时后台执行；不新增状态表，按集合 id 差集判断。
6. 扩展 `vector-sync` 接口支持 `target=episodes`（复用现有端点与前端按钮）。

**验收**：不同措辞的同一故障可命中；向量不可用时自动降级；跨工作区不串；删除后不再召回。

### Phase 4 — 踩坑主动提示（1 天，默认关闭）

文件：`nanobot/agent/loop.py` / `runner.py`（复用现有 hook 机制）、配置 schema、提示词。

1. 工具调用返回错误时，用错误文本查 `kind=pitfall,status=active` 的 top-1（向量优先，词法兜底）。
2. 以固定格式追加到下一轮上下文：`经验库提示（仅供参考）：症状 → 根因 → 修复`，≤300 字符。
3. 同一错误签名每轮只提示一次；相似度低于阈值不提示。
4. 配置项 `memoryPitfallHint`（默认 `false`），开启后生效。

**验收**：命中时能出现在下一轮上下文；误报可控；关闭时零副作用。

### Phase 5 — Dream 与 WebUI（1.5 天）

文件：`nanobot/agent/memory.py`、`nanobot/templates/agent/dream.md`、`webui/src/components/settings/memory/ExperienceListView.tsx`、`webui/src/lib/api.ts`、i18n。

1. Dream 按 kind 复盘：
   - 合并仅限同 kind 的重复 experience；
   - 同类成功经验 ≥2 条时提议升级为 skill（需用户确认）；
   - pitfall 提议升级为 `MEMORY.md` 规则（需用户确认）；
   - 长期未再命中的 skill 标记 `deprecated`；
   - decision 用 `superseded` 链接新旧决策，不删除。
2. WebUI：类型筛选 chips、类型/状态徽标、按 kind 渲染详情（步骤、选项、指针）。
3. 经验库列表默认按时间倒序，类型筛选与搜索组合生效。

**验收**：Dream 报告包含“新增/合并/升级/弃用/跳过”计数；UI 可筛选五类并正确展示细节。

### Phase 6 — 验证与文档（1 天）

1. 单测：kind 校验、过滤、索引、向量 upsert/delete/回填、RRF、workspace 隔离、降级。
2. 集成：`memory_search(kind=pitfall)`、自动注入标签、Dream 提议。
3. E2E（真实 MiniMax + WebUI）：
   - 记录一条“数据库连接超时”踩坑；
   - 换一种措辞提问，能命中同一条；
   - 记录两条同类成功经验，Dream 提议升级 skill；
   - decision 被新决策 supersede 后默认不返回、可显式查看。
4. 性能：写入 embedding 延迟、1k 条 episodes 回填耗时、检索 P95。
5. 文档：更新 `docs/memory.md`（类型表、向量集合、降级策略）与 `AGENTS.md`。
6. 产出 `.ai-runtime-artifacts/verifications/2026-11-06-experience-kinds-vector.md`。

## 测试矩阵

| 层 | 用例 |
| --- | --- |
| 存储 | 五类必填校验；旧行默认 experience；tags 带 kind/status |
| 检索 | kind/outcome/status 组合；过滤时只走 episodes；标签正确 |
| 向量 | upsert/delete/回填；模型不可用降级；候选回表隔离 |
| 融合 | 词法+向量 RRF 去重；同 id 不重复 |
| 提示 | 错误触发、阈值、每轮去重、开关关闭零副作用 |
| Dream | 同 kind 才合并；skill 升级门槛；supersede 不删除 |
| UI | 筛选、徽标、按 kind 渲染 |

## 风险与回滚

| 风险 | 应对 |
| --- | --- |
| `json_extract` 过滤变慢 | 表达式索引；候选先按时间/workspace 收窄 |
| 写入 embedding 拖慢工具 | best-effort + 后台队列；失败不影响落库 |
| ChromaDB 单进程锁 | 复用单一 VectorStore 实例与 client |
| 首次回填耗时 | 分批 + 可中断；集合为空才自动回填 |
| 类型被滥用 | 严格必填校验 + 提示词分流 + Dream 纠偏 |
| 与 MEMORY.md / SKILL.md 重复 | 升级必须用户确认；不自动写规则文件 |
| 版本/弃用语义膨胀 | 只存 `version`/`supersedes`/`status`，不做完整版本树 |

回滚路径：

- 关闭 `memory_search_backend=chromadb` → 回到纯词法，功能不报错。
- 忽略 `metadata.kind` → 旧读取逻辑等价于全部 experience。
- 删除 `episodes` 向量集合 → 下次启动自动重建（或关闭开关不重建）。

## 非目标

- 不新增表、不建知识库表、不建 FTS5 虚拟表。
- 不自动把经验写成 MEMORY.md 规则。
- 不生成/管理可执行技能文件（`SKILL.md` 仍由技能系统负责）。
- 不做跨用户的权限模型。

## 成功标准

1. 五类可写入、可按类型检索，输出带类型标签。
2. 同一故障换措辞后能命中已有 pitfall（人工评测命中率高于纯词法基线）。
3. 带 kind 过滤时结果不混入其他类型，也不跨工作区。
4. 向量不可用、模型未下载、ChromaDB 异常时，检索自动降级且不阻断对话。
5. 数据库 schema 变化只有索引，没有新表。

## Next

计划已写入。请确认后再说「开始实现」；如需拆分并行，我可以另写 `*-dispatch.md`。
