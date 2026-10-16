---
artifact: implementation-plan
route: superpowers:writing-plans -> orchestration:dispatcher-workflow
skills:
  - writing-plans
  - source-driven-development
  - api-and-interface-design
  - orchestration
skills_evidence:
  - /Users/mima0000/.agents/skills/writing-plans/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
  - .claude/skills/api-and-interface-design/SKILL.md
  - .claude/skills/orchestration/SKILL.md
dispatch: .ai-runtime-artifacts/plans/2026-10-17-writing-team-sidebar-dispatch.md
source:
  - .ai-runtime-artifacts/specs/2026-10-17-writing-team-sidebar-spec.md
  - .ai-runtime-artifacts/research/2026-10-17-writing-agent-workflow-research.md
  - .ai-runtime-artifacts/research/writing-skill-assets-v2/README.md
  - .ai-runtime-artifacts/stack/2026-10-17-writing-agent-workflow-stack.md
  - .ai-runtime-artifacts/contracts/2026-10-12-contract-writing-collaboration.md
  - .ai-runtime-artifacts/contracts/2026-10-12-contract-writing-article-store.md
  - .ai-runtime-artifacts/verifications/2026-10-17-writing-frontend-baseline-v2.md
  - .ai-runtime-artifacts/verifications/2026-10-17-writing-browser-v2.md
  - .ai-runtime-artifacts/HANDOFF.md
  - harness-kit/project.profile.md
  - harness-kit/project.verification.md
  - harness-kit/core/routing.md
created_at: 2026-10-17
status: approved
approved: true
approval_evidence: >-
  用户在任务开头一次性授权：「根据方案完成任务，根据需求完成任务开始吧」（2026-10-17）。
  按 routing.md §阶段门禁 的「任务开头一次性授权且 FM 写 approved: true + 引用原话」口径置为已批准，
  本计划落盘后由 goal 自动续轮进入编排实施，不再单独等待一次「开始实现」。
tier: 2
---

# 创作空间侧栏内置创作团队 · 实施计划

> **For agentic workers:** 本计划由 Leader 按 WU 编排执行。**每个 Task 必须 TDD**：
> 先写失败测试 → 跑一次确认失败 → 写最小实现 → 跑通 → 在 worktree 内提交。
> 实施入口为 `*-dispatch.md` 的执行图；Task 内步骤用 `- [ ]` 勾选跟踪。

**Goal：** 让作者打开文章即可使用产品内置的固定创作团队（`writing-team`），由主编自动协调资料研究、结构设计、写作与审校，产物先进入建议区，经服务端版本校验后由作者采纳写入文章。

**Architecture：** 复用已落地的写作域（`nanobot/writing/`，SQLite schema v1，文章快照 + revision CAS + `article_requests` 幂等）与团队运行时（`nanobot/teams/`，scheduler/executor/`execute_frozen` + 共享 Runner）。创作域新增四类持久化实体（collaboration / batch / artifact+suggestion / handoff）与固定内置资产清单；写作执行适配包住 `TeamMemberExecutor`，把成员输出收敛为类型化 envelope 落库，再向 Team 返回摘要引用；传输沿用「HTTP 读 + 认证 WS mutation 写 + 事件推送」既有机制，不新造 REST 写协议；前端在文章编辑器右侧把阶段 B 的 AI 占位区替换为「创作搭档」侧栏。

**Tech Stack：** Python 3.13（`≥3.11`）+ stdlib `sqlite3` + asyncio；React 18 + TypeScript 5.9 + Vite 5 + Tailwind 3 + Vitest 2；Node 24 / npm 11（**本机无 Bun**，前端命令一律用 `npm run …`）；pytest 9（`asyncio_mode=auto`）+ ruff + basedpyright。

**TDD Required：** YES（每个产出生产代码的 Task 都走 RED→GREEN→REFACTOR）

---

## 0. 基线与前置事实

| 项 | 事实 | 来源 |
| --- | --- | --- |
| 分支 | `feature/desktop-app`，HEAD `81b4937`；worktree 基座在 `/Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/zhixu/` | `git branch --show-current` / `git worktree list` |
| 写作域 | `nanobot/writing/{__init__,database,models,repository}.py`；`SCHEMA_VERSION="1"`；仅 3 张表 `articles` / `article_requests` / `_schema_meta`；**无 ALTER 迁移分支**，`ensure_schema()` 幂等重跑 `CREATE … IF NOT EXISTS` | `nanobot/writing/database.py:14,16-52,93-104` |
| 写作仓储 | 公开 `get_article` / `list_articles` / `create_article` / `save_snapshot` / `duplicate_article` / `delete_article`；一个写方法一个 `with self._db.connect()` 事务；幂等 `_replay_response` / `_record_request`（PK `workspace_key,request_id`，**无 payload hash**） | `nanobot/writing/repository.py:80,91,136,178,235,281,311,378` |
| 写作传输 | `writing_routes.py` 动作集 `WRITING_ACTION_NAMES` / `WRITING_MUTATION_ACTION_NAMES`；`writing_api.py::_translated` 是 repository 异常→HTTP 的**唯一**翻译点；装配 `WritingServices.for_workspace` | `nanobot/webui/writing_routes.py:44-53,63-98,104-161`、`writing_api.py:45-59`、`writing_services.py:18-37` |
| 路由三表 | 新增一个 WS 动作必须同时改：`_SYSTEM_ROUTES`、`_WRITING_MUTATION_PATHS`→`_SETTINGS_MUTATION_PATHS`、`ws_http.py::_WEBUI_MUTATION_PATHS`，并更新接线测试 | `nanobot/webui/settings_routes.py:205-210,254-259,297`、`ws_http.py:230-233`、`tests/webui/test_writing_wiring.py:1-11` |
| 幂等约定 | 传输层 `request_id` **不透传**领域层，业务幂等必须由 payload 内 `client_request_id` 承担 | `nanobot/webui/writing_routes.py:38-40` |
| 事件推送 | 全仓**没有** team run 的 WS 推送；写作域新增事件需走既有广播入口 | `nanobot/webui/inbound_commands.py:1486-1490`（`broadcast_webui_event`） |
| Team 装配缺陷（**必须修**） | `gateway_services.py` 用 `enabled=bool(settings.config.load().agents.teams)`——对象真值恒为 `True`，`agents.teams.enabled=false` 的回滚开关实际失效；`TeamService` 内部才读 `.enabled` | `nanobot/webui/gateway_services.py:284`、`nanobot/teams/service.py:246` |
| 内置保护现状 | Agent：`type=system` 删除 409、reset 409、团队引用 409，保存时按 factory 重算类型；Skill：`source=builtin` 删除 **403**、payload 已有 `deletable`；Team：**无内置概念**，只有 `archived`；MCP：**无删除/覆盖保护**，仅 manifest `trust.level="builtin"` | `nanobot/agents/store.py:163-196`、`skills_api.py:98-104,174`、`teams/service.py:122,166,171`、`mcp_presets_api.py:806-811,1452-1491,1564` |
| 归属字段 | `ownerModule` / `origin` / `builtinVersion` / `protectionReason` **全仓不存在**；`deletable` 仅 Skill 三处 | 管理面只读探查（2026-10-17） |
| Python 基线 | `pytest tests/writing tests/webui/test_writing_api.py tests/webui/test_writing_routes.py tests/webui/test_writing_wiring.py tests/test_writing_paths.py -q` → `125 passed` | `2026-10-17-writing-team-sidebar-verification.md:29` |
| 前端基线 | Vitest 全量 `1636 passed / 1 failed / 2 skipped`；唯一失败为 i18n `settings.teams.rosterCount_one/_other` 缺失；lint + build 通过 | `2026-10-17-writing-frontend-baseline-v2.md:30,45-52` |
| 前端既有缺陷 B01 | 预览模式单次新建出现重复文章：preview transport 已插入 + `handleCreate` 再次 prepend | `2026-10-17-writing-browser-v2.md:41-51`（`CreativeWorkspace.tsx:414,548`） |

### 0.1 Team 运行时接入事实（2026-10-17 只读取证）

| 项 | 事实 | 来源 |
| --- | --- | --- |
| 无内置工厂 | `TeamService` 只有 `save_team` / `clone_team` / `prepare_new_run` / `recover_after_restart`，**没有 builtin 播种入口**——写作固定团队必须自备 TeamDefinition + AgentProfile 播种器 | `nanobot/teams/service.py:104,122,171,243,298` |
| 可复用装配链 | `build_team_turns(service, host, workspace)` → `bind_team_turns`；绑定可直接写 `session.metadata["team_binding"]`（`TeamBinding.to_dict()`），**绕过 TTL 暂存**（暂存 TTL 600s / 上限 256，不能当权威来源） | `nanobot/teams/turn.py:209,173`、`nanobot/teams/binding.py:23,26,29,124` |
| 角色与限额 | `definition.instructions` + `TeamMember.role` → `render_role_prompt`；`TeamDispatchTool`；`definition.limits`（默认 `max_concurrency=3`） | `nanobot/teams/leader.py:61-91`、`turn.py:162`、`models.py:285-330` |
| 会话键 | `"websocket:" + chat_id`；chatId 正则 `^[A-Za-z0-9_:-]{1,64}$` → 会话键 ≤74 字符 | `nanobot/webui/session_identity.py:8,9,17-19` |
| 长稿传递缺口 | 下游成员只拿 `summary`（`UPSTREAM_CONTEXT_BUDGET=8000`、`MAX_TASK_SUMMARY_CHARS=8000`）；`fullResult` 仅超长时落 DB，读回时被丢弃；`check_full_result_length` 无调用点 | `nanobot/teams/scheduler.py:57,476-497,553,557`、`repository.py:969,1360`、`models.py:79,250` |
| Skill 冻结缺口 | `execute_frozen` 只冻结 `ResolvedAgent`；`SkillsLoader` 每次按名称实时重建，档案 skills 仅固化为 `skill_exclude` | `nanobot/agent/subagent.py:641,902,903-906`、`nanobot/agent/skills.py:100-130` |
| 取消隐患 | `finish_task` 只拒绝终态；`cancelling` 属非终态 → **仓储层不拦晚到成功结果**，`runTimeout` 也只在任务起跑前检查 | `nanobot/teams/repository.py:71-77,937`、`scheduler.py:457,472,714-715` |
| 重启收敛 | `recover_after_restart` 把未完成 task/run 标 `interrupted` 并补 `run.finished`，不重放 | `nanobot/teams/service.py:298`、`repository.py:565-601` |

**结论（约束 Task 6–8、10）：** 写作层必须自备「固定团队播种 + 权威文章绑定 + 完整产物落库 + 独立取消/收口闸门」，Team 侧只做**最小**适配（补 `fullResult` 读取与 `cancelling` 守卫），不改造为通用工作流平台。

### 0.2 前端落点（2026-10-17 只读取证）

| 项 | 事实 | 来源 |
| --- | --- | --- |
| 右栏落点 | 「AI 建议」占位区在 `ArticleEditor.tsx:413-426`（容器 `w-[260px] shrink-0 overflow-y-auto border-l border-border bg-settings-surface/60 p-3`，占位块 `data-testid="suggestions-placeholder"`） | `webui/src/components/creative/ArticleEditor.tsx:413-426` |
| 三栏容器 | `flex min-h-0 flex-1` `:326`；左素材/提纲 `w-[220px]` `:328-366`；中栏 `flex-1` `:369-411`（split 用 `grid-cols-2` `:404`）；**右栏无折叠逻辑** | 同上 |
| 死代码 | `SuggestionPanel.tsx` 在 `src/` 零引用 | `webui/src/components/creative/SuggestionPanel.tsx:16` |
| 可复用范例 | 右栏断点 `ThreadFrame.tsx:24`（`hidden lg:block lg:w-[340px] xl:w-[380px]`）；宽度折叠动画 `FilePreviewPanel.tsx:100-122`（CSS 变量 + `transition-[width]`，关闭置 0px）；进度/待处理卡 `TeamProgressPanel.tsx:36-142`、`AskQuestionCard.tsx:41-110` | 前端只读探查 |
| 保存流出口 | `use-article-save-flow.ts`：`saveNow:326`、`setField:342`、`acknowledgeConflict:360`、`forceConflict:415`、`abandon:429`、`tryLeave:434`；串行队列 `:322`，409/404→conflict `:262-270`，503→offline `:271-274` | `use-article-save-flow.ts` |
| WS 客户端 | `requestMutation` 帧 `{type:"webui_request", request_id, action, payload}` `:938-975`；订阅 `onChat:799`；事件归并范例 `useTeamProgress.ts:139-166`（parseTeamFrame→applyTeamEvent/applyTeamSnapshot，缺口补拉 `:78-115`） | `webui/src/lib/nanobot-client.ts`、`hooks/useTeamProgress.ts` |
| 预览降级 | preview 模式无 gateway，走内存 transport 降级 | `CreativeWorkspace.tsx:408-423` |
| i18n | 10 个 locale；`en/common.json:277-278` 有 `rosterCount_one/_other`，zh-CN:276 / zh-TW:1345 缺失 → 形状断言失败 | `webui/src/tests/i18n.test.tsx:582-603` |

**结论（约束 Task 11–14）：** 整块替换 `ArticleEditor.tsx:413-426`，保留 `w-[260px] shrink-0 border-l`（中栏 `flex-1` 不受影响）；折叠仿 `FilePreviewPanel`；新事件订阅按 `useTeamProgress` 的归并范式实现。

**不进入本批（spec §1「不进入本次范围」）：** 用户可编辑工作流、画布、团队选择器、节点/角色配置向导、小说设定、自动发布、批量生成、定时写作、新增账号体系与依赖。

**本批不做真实模型效果承诺：** W15（真实模型端到端）在具备已配置模型的工作区执行；若环境无法提供，`Task 15` 必须显式记录缺口，不得宣称真实协作已验收。

---

## 1. 文件结构（决定 WU 拆分的边界）

### 新建

| 路径 | 责任 | 归属 Task |
| --- | --- | --- |
| `nanobot/skills/article-brief/SKILL.md` 等 7 个目录 | 七个内置写作 Skill 正文（用途/输入/步骤/产物格式/边界/失败处理） | T1 |
| `nanobot/writing/builtin.py` | 创作域固定资产清单（team/agent/skill/mcp 逻辑 ID + 版本 + 保护事实）与 `(workspace, assetKind, logicalId, builtinVersion)` → 物理 ID 的幂等 preparation | T3 |
| `nanobot/writing/collab/__init__.py` | 协作域导出面 | T4 |
| `nanobot/writing/collab/database.py` | 协作表 DDL（并入 `_SCHEMA_STATEMENTS` 的唯一入口）与 schema v2 升级 | T4 |
| `nanobot/writing/collab/models.py` | Collaboration / Batch / Artifact / Suggestion / Handoff 数据类与枚举校验 | T4 |
| `nanobot/writing/collab/repository.py` | 协作持久化：创建/推进批次、产物落库、快照查询、状态收敛（单事务 + 幂等） | T4 |
| `nanobot/writing/collab/offsets.py` | 正文归一化与「归一化下标 ↔ 原文下标」映射（UTF-16/Unicode 口径） | T5 |
| `nanobot/writing/collab/apply.py` | 建议校验、采纳事务、撤销事务（复用 `ArticleRepository.save_snapshot` CAS） | T5 |
| `nanobot/writing/collab/recipe.py` | 动作 → 角色/Skill/产物类型 的固定配方与白名单 | T6 |
| `nanobot/writing/collab/preparation.py` | 冻结准备：解析内置 Skill 正文 + hash、检查禁用/依赖、固定模型与来源范围 | T6 |
| `nanobot/writing/collab/envelope.py` | 成员输出的版本化类型化 envelope 解析与校验（只接受 kind/content/sourceIds/issues） | T7 |
| `nanobot/writing/collab/runner.py` | 协作批次生命周期：启动、等待作者、继续、取消世代、重试、超时、重启收敛 | T8 |
| `nanobot/webui/writing_collab_api.py` | 协作 payload 层：形状校验 + 错误翻译（沿用 `writing_api.py` 口径） | T9 |
| `nanobot/webui/writing_collab_routes.py` | 协作动作 handler 与 `WRITING_COLLAB_ACTION_NAMES` / `*_CONTROL_ACTION_NAMES` | T9 |
| `webui/src/lib/writing-collab-api.ts` | 前端类型化协作 API（动作 + 查询 + 事件归并） | T11 |
| `webui/src/components/creative/use-writing-collab.ts` | 协作状态 hook（快照优先、事件幂等、选区与请求标识） | T12 |
| `webui/src/components/creative/WritingPartnerPanel.tsx` | 「创作搭档」侧栏（主操作、快捷任务、输入框、进度、产物区） | T12 |
| `webui/src/components/creative/ArtifactCard.tsx` | 六类产物卡（选题/资料/提纲/初稿/改写/审稿）与作者写入动作 | T12 |
| `webui/src/components/creative/collab-labels.ts` | 状态/阶段/动作 → i18n key 映射（单一出口） | T12 |

### 修改

| 路径 | 变更 | 归属 Task |
| --- | --- | --- |
| `nanobot/agents/catalog.py` | `FACTORY_PROFILES` 追加 5 个写作档案（`writing-editor` / `-researcher` / `-architect` / `-author` / `-reviewer`，`category_id="writing"`、`type="system"`、非空 prompt、工具白名单） | T2 |
| `nanobot/writing/database.py` | 追加协作与内置映射表 DDL；`SCHEMA_VERSION` → `"2"` | T3/T4 |
| `nanobot/writing/models.py` | 若需新增上限常量（产物分节、instruction、选区）集中在此 | T4 |
| `nanobot/agents/store.py`、`nanobot/webui/skills_api.py`、`nanobot/webui/skills_marketplace.py`、`nanobot/teams/service.py`、`nanobot/webui/mcp_presets_api.py` | 归属字段投影 + 受保护写入口拦截（统一业务码） | T10 |
| `nanobot/webui/settings_routes.py`、`nanobot/webui/ws_http.py` | 新动作的路由三表登记 | T9 |
| `nanobot/bus/outbound_events.py`、`nanobot/webui/outbound_projection.py`、`nanobot/channels/websocket/runtime.py` | 一个 `WritingEvent` 基类 + 一个 `isinstance` 投影分支 + 一个 `send_writing_event` | T9 |
| `nanobot/cli/gateway_runtime.py`、`nanobot/webui/gateway_services.py` | 协作层装配、`.enabled` 修复、启动收敛 | T8/T10 |
| `webui/src/components/creative/ArticleEditor.tsx` | 右侧占位区 → `<WritingPartnerPanel/>`；选区追踪 | T13 |
| `webui/src/components/creative/CreativeWorkspace.tsx` | 断点布局（折叠素材栏）、面板切换、**B01 预览重复修复** | T13 |
| `webui/src/i18n/locales/<10 locales>/common.json` | `creative.partner.*` 全套文案 + 修 `settings.teams.rosterCount_one/_other` | T14 |
| `tests/webui/test_writing_wiring.py` 等 | 接线测试与回归覆盖 | T9/T13/T14 |

---

## 2. 契约冻结（跨 WU 接口，实施前不得各写一套）

> Task 0 把本节全文抽成 `.ai-runtime-artifacts/contracts/2026-10-17-contract-writing-team-collab.md`（含 FM），作为所有 WU 的 L4 输入。派生契约的**唯一上游**是本节 + spec v2 §13.6；两处冲突时以本节为准并回头改 spec。

### 2.1 领域对象与枚举（持久化即协议，snake_case）

| 对象 | 字段 |
| --- | --- |
| Collaboration | `id, article_id, chat_id, session_key, goal, status, stage, active_batch_id, cancel_generation, version, seq, last_handoff_id, created_at, updated_at` |
| Batch | `id, collaboration_id, attempt, team_run_id, base_revision, snapshot_digest, action, instruction, status, cancel_generation, frozen, error_message, created_at, finished_at` |
| Artifact | `id, collaboration_id, batch_id, attempt, kind, role_id, version, content, content_digest, source_ids, gaps, status, created_at` |
| Handoff | `id, collaboration_id, batch_id, artifact_id, kind, artifact_version, status, created_at, resolved_at` |
| Suggestion | 沿用旧契约 §3 字段 + 新增 `collaboration_id, artifact_id` |

枚举：

- `collaboration.status`：`idle | queued | running | waiting_author | completed | failed | cancelled | interrupted`
- `collaboration.stage`：`'' | brief | research | outline | draft | review | rewrite`
- `batch.status`：`queued | running | waiting_author | completed | failed | cancelled | interrupted`
- `artifact.kind`：`brief | material_list | outline | draft | rewrite | review`
- `artifact.role_id`：`writing-editor | writing-researcher | writing-architect | writing-author | writing-reviewer`
- `handoff.kind`：`outline_confirm | materials_request | final_accept`
- `handoff.status` / `suggestion.status`：`pending | confirmed | superseded` / `pending | applied | rejected | stale | reverted`
- `action`（作者动作）：`start_full | ideate | research | outline | draft | rewrite_selection | review_all | reply`

### 2.2 持久化 DDL（schema v2）

追加进 `nanobot/writing/database.py::_SCHEMA_STATEMENTS`（**只新增表，不给 `articles` 加列**——该库没有 ALTER 迁移路径）：

```sql
CREATE TABLE IF NOT EXISTS writing_builtin_assets (
  workspace_key   TEXT NOT NULL,
  asset_kind      TEXT NOT NULL,        -- team | agent | skill | mcp
  logical_id      TEXT NOT NULL,        -- writing-team | writing-editor | article-brief | …
  builtin_version TEXT NOT NULL,        -- v1
  physical_id     TEXT NOT NULL,        -- 实际存储记录 ID（可与 logical_id 不同）
  created_at      TEXT NOT NULL,
  PRIMARY KEY (workspace_key, asset_kind, logical_id, builtin_version)
);

CREATE TABLE IF NOT EXISTS writing_collaborations (
  id                TEXT    NOT NULL,
  workspace_key     TEXT    NOT NULL,
  article_id        TEXT    NOT NULL,
  chat_id           TEXT    NOT NULL,
  session_key       TEXT    NOT NULL,
  goal_json         TEXT    NOT NULL DEFAULT '{}',
  status            TEXT    NOT NULL DEFAULT 'idle',
  stage             TEXT    NOT NULL DEFAULT '',
  active_batch_id   TEXT,
  cancel_generation INTEGER NOT NULL DEFAULT 0,
  version           INTEGER NOT NULL DEFAULT 1,
  seq               INTEGER NOT NULL DEFAULT 0,
  last_handoff_id   TEXT,
  created_at        TEXT    NOT NULL,
  updated_at        TEXT    NOT NULL,
  PRIMARY KEY (workspace_key, id)
);
CREATE INDEX IF NOT EXISTS writing_collaborations_article_idx
  ON writing_collaborations (workspace_key, article_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS writing_collab_batches (
  id                TEXT    NOT NULL,
  workspace_key     TEXT    NOT NULL,
  collaboration_id  TEXT    NOT NULL,
  attempt           INTEGER NOT NULL DEFAULT 1,
  team_run_id       TEXT    NOT NULL DEFAULT '',
  base_revision     INTEGER NOT NULL,
  snapshot_digest   TEXT    NOT NULL,
  action            TEXT    NOT NULL,
  instruction       TEXT    NOT NULL DEFAULT '',
  status            TEXT    NOT NULL DEFAULT 'queued',
  cancel_generation INTEGER NOT NULL DEFAULT 0,
  frozen_json       TEXT    NOT NULL DEFAULT '{}',
  error_message     TEXT    NOT NULL DEFAULT '',
  created_at        TEXT    NOT NULL,
  finished_at       TEXT,
  PRIMARY KEY (workspace_key, id)
);
CREATE INDEX IF NOT EXISTS writing_collab_batches_collab_idx
  ON writing_collab_batches (workspace_key, collaboration_id, created_at);

CREATE TABLE IF NOT EXISTS writing_collab_artifacts (
  id               TEXT    NOT NULL,
  workspace_key    TEXT    NOT NULL,
  collaboration_id TEXT    NOT NULL,
  batch_id         TEXT    NOT NULL,
  attempt          INTEGER NOT NULL DEFAULT 1,
  kind             TEXT    NOT NULL,
  role_id          TEXT    NOT NULL,
  version          INTEGER NOT NULL DEFAULT 1,
  content_json     TEXT    NOT NULL DEFAULT '{}',
  content_digest   TEXT    NOT NULL,
  source_ids       TEXT    NOT NULL DEFAULT '[]',
  gaps             TEXT    NOT NULL DEFAULT '[]',
  status           TEXT    NOT NULL DEFAULT 'ready',
  created_at       TEXT    NOT NULL,
  PRIMARY KEY (workspace_key, id)
);
CREATE INDEX IF NOT EXISTS writing_collab_artifacts_collab_idx
  ON writing_collab_artifacts (workspace_key, collaboration_id, kind, created_at);

CREATE TABLE IF NOT EXISTS writing_collab_handoffs (
  id               TEXT    NOT NULL,
  workspace_key    TEXT    NOT NULL,
  collaboration_id TEXT    NOT NULL,
  batch_id         TEXT    NOT NULL,
  artifact_id      TEXT    NOT NULL,
  kind             TEXT    NOT NULL,
  artifact_version INTEGER NOT NULL,
  status           TEXT    NOT NULL DEFAULT 'pending',
  created_at       TEXT    NOT NULL,
  resolved_at      TEXT,
  PRIMARY KEY (workspace_key, id)
);

CREATE TABLE IF NOT EXISTS writing_collab_suggestions (
  id                  TEXT    NOT NULL,
  workspace_key       TEXT    NOT NULL,
  collaboration_id    TEXT    NOT NULL,
  artifact_id         TEXT    NOT NULL DEFAULT '',
  article_id          TEXT    NOT NULL,
  base_revision       INTEGER NOT NULL,
  snapshot_digest     TEXT    NOT NULL,
  target_json         TEXT    NOT NULL DEFAULT '{}',
  before_text         TEXT    NOT NULL DEFAULT '',
  after_text          TEXT    NOT NULL,
  summary             TEXT    NOT NULL DEFAULT '',
  notes               TEXT    NOT NULL DEFAULT '',
  source_material_ids TEXT    NOT NULL DEFAULT '[]',
  status              TEXT    NOT NULL DEFAULT 'pending',
  applied_revision    INTEGER,
  created_at          TEXT    NOT NULL,
  applied_at          TEXT,
  PRIMARY KEY (workspace_key, id)
);
CREATE INDEX IF NOT EXISTS writing_collab_suggestions_article_idx
  ON writing_collab_suggestions (workspace_key, article_id, status);

CREATE TABLE IF NOT EXISTS writing_collab_undo_entries (
  id              TEXT    NOT NULL,
  workspace_key   TEXT    NOT NULL,
  article_id      TEXT    NOT NULL,
  suggestion_id   TEXT    NOT NULL,
  before_body     TEXT    NOT NULL,
  before_revision INTEGER NOT NULL,
  result_revision INTEGER NOT NULL,
  result_digest   TEXT    NOT NULL,
  reverted        INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT    NOT NULL,
  reverted_at     TEXT,
  PRIMARY KEY (workspace_key, id)
);
```

幂等：协作写动作复用 `article_requests`（PK `workspace_key,request_id`），`op` 取动作名；`writing.start` 额外在 `writing_collab_batches.client_request_id` 语义上查重（实现放在 `collab/repository.py`，**不改** `articles` 结构）。

### 2.3 WS 动作与 HTTP 读

写动作（仅认证 WS mutation；HTTP 直连 405）：

| action | 路径 | 必需输入 | 成功结果 |
| --- | --- | --- | --- |
| `writing.start` | `/api/writing/collab/start` | `client_request_id, article_id, chat_id, action, base_revision, snapshot_digest, instruction?, selection?, material_ids?, request_options?` | `{collaboration_id, batch_id, status, stage, session_key, active_batch_id}` |
| `writing.reply` | `/api/writing/collab/reply` | `client_request_id, collaboration_id, feedback, expect_version` | `{collaboration_id, batch_id, status}` |
| `writing.confirm` | `/api/writing/collab/confirm` | `client_request_id, collaboration_id, handoff_id, expected_handoff_version, outline?, article_revision, article_digest` | `{collaboration_id, batch_id, article_revision, article_digest, handoff_status}` |
| `writing.cancel` | `/api/writing/collab/cancel` | `client_request_id, collaboration_id, active_batch_id` | `{collaboration_id, status, cancel_generation}` |
| `writing.retry` | `/api/writing/collab/retry` | `client_request_id, collaboration_id, batch_id, expect_version` | `{collaboration_id, batch_id, attempt, status}` |
| `writing.apply` | `/api/writing/collab/apply` | `client_request_id, suggestion_id, article_id, base_revision` | `{suggestion_id, article_id, revision, digest, staled_suggestion_ids, undo_id}` |
| `writing.undo` | `/api/writing/collab/undo` | `client_request_id, undo_id, base_revision` | `{undo_id, article_id, revision, digest, suggestion_id}` |

循环绑定动作（必须在事件循环上执行，参照 `teams_domain.TEAMS_CONTROL_ACTION_NAMES` 分支）：`writing.start`、`writing.cancel`、`writing.retry`；其余走 `asyncio.to_thread`。

读（认证 HTTP GET，action 名同名）：

| 路径 | action | query | 返回 |
| --- | --- | --- | --- |
| `/api/writing/collab` | `writing.collab.get` | `article_id` | `{collaboration, batches, artifacts, handoffs, suggestions, last_version, last_seq, next_actions, blocked_reason}` |
| `/api/writing/collab/artifact` | `writing.artifact.get` | `artifact_id` | `{artifact}`（完整正文，支持分节） |

约束：**不接受** `teamId` / `agentId` / `skillPath`；团队成员、Skill 与模型一律服务端解析（spec §9「请求边界」）。

### 2.4 错误（纯文本 message，body 格式沿用 A 契约 §6）

| 场景 | status | message |
| --- | --- | --- |
| 该文章已有活动批次 | 409 | `collaboration already has an active batch for article_id=<id>` |
| 协作不存在 | 404 | `collaboration not found` |
| 协作版本过期 | 409 | `collaboration version conflict expected=<n> actual=<n>` |
| 交接版本过期 | 409 | `handoff version conflict expected=<n> actual=<n>` |
| 选区缺失或为空 | 400 | `collaboration target selection is required` |
| 素材不属于该文章 | 400 | `article material not found: <material_id>` |
| 无活动批次可取消 | 409 | `no active batch for collaboration_id=<id>` |
| 建议不可采纳 | 409 | `suggestion is not pending` |
| 撤销被后续编辑挡住 | 409 | `undo blocked by newer edits` |
| 撤销已用 | 409 | `undo already applied` |
| 版本冲突 | 409 | `article_conflict current_revision=<int> article_id=<id>`（A 契约原文） |
| 上下文超限 | 413 | `collaboration context too large; shrink the selection or materials` |
| 内置资源受保护 | 409 | `builtin resource is protected: <asset_kind>/<logical_id>`（业务码 `BUILTIN_RESOURCE_PROTECTED`） |
| 团队被关闭 | 409 | `writing team is disabled for this workspace` |
| 模型不可用 | 409 | `writing model is not available: <model_id>` |
| 协作层未装配 | 503 | `writing collaboration service is not configured` |
| 未知动作 | 404 | `unknown writing collaboration action: <action>` |
| 控制动作走 sync 入口 | 500 | `<action> must run on the event loop` |

### 2.5 事件

新增 `nanobot/bus/outbound_events.py::WritingEvent`（frozen 基类，带 `event_name` + `body`）与其子类；`outbound_projection.py` **一个** `isinstance(event, WritingEvent)` 分支；`channels/websocket/runtime.py` 一个 `send_writing_event(chat_id, name, body)`。帧形如 `{"event": <event_name>, "chat_id": <协作会话 chatId>, ...body}`：

- `writing_collab`：`{collaboration_id, status, stage, active_batch_id, version, seq, blocked_reason?}`
- `writing_artifact`：`{collaboration_id, artifact_id, kind, version, summary}`
- `writing_handoff`：`{collaboration_id, handoff_id, kind, artifact_id, artifact_version, status}`

乱序/重复事件不得倒退状态：前端按 `collaboration.version` / `seq` 幂等归并，重连先读 `writing.collab.get`。

### 2.6 前端类型

```ts
// webui/src/lib/writing-collab-api.ts
export type CollabStatus = "idle" | "queued" | "running" | "waiting_author"
  | "completed" | "failed" | "cancelled" | "interrupted";
export type CollabAction = "start_full" | "ideate" | "research" | "outline"
  | "draft" | "rewrite_selection" | "review_all" | "reply";
export type ArtifactKind = "brief" | "material_list" | "outline" | "draft" | "rewrite" | "review";
export interface CollabSnapshot {
  collaboration: Collaboration | null; batches: CollabBatch[]; artifacts: CollabArtifact[];
  handoffs: CollabHandoff[]; suggestions: CollabSuggestion[];
  lastVersion: number; lastSeq: number; nextActions: CollabAction[]; blockedReason: string | null;
}
export interface StartCollabRequest {
  clientRequestId: string; articleId: string; chatId: string; action: CollabAction;
  baseRevision: number; snapshotDigest: string; instruction?: string;
  selection?: { start: number; end: number; text: string }; materialIds?: string[];
}
export const collabApi: {
  getSnapshot(articleId: string): Promise<CollabSnapshot>;
  getArtifact(artifactId: string): Promise<CollabArtifact>;
  start(req: StartCollabRequest): Promise<{ collaborationId: string; batchId: string; status: CollabStatus }>;
  reply(req: {...}): Promise<...>; confirm(req: {...}): Promise<...>; cancel(req: {...}): Promise<...>;
  retry(req: {...}): Promise<...>; apply(req: {...}): Promise<...>; undo(req: {...}): Promise<...>;
};
```

### 2.7 内置资产清单（`writingVersion = "v1"`）

| 资产 | logical_id | 说明 |
| --- | --- | --- |
| Team | `writing-team` | 产品名「创作团队」，5 名成员，无用户选择器 |
| Agent | `writing-editor` / `writing-researcher` / `writing-architect` / `writing-author` / `writing-reviewer` | 出厂档案，`type="system"`，`category_id="writing"` |
| Skill | `article-brief` / `article-research` / `article-outline` / `article-draft` / `article-rewrite` / `article-expand` / `article-review` | 目录 `nanobot/skills/<name>/SKILL.md`，`name` 必须等于目录名 |
| MCP（可选，首批不提供假连接） | — | 保持 `web_search`/`web_fetch` 与素材即可工作 |

Skill→角色配套：`writing-editor` ← `article-brief, article-review`；`writing-researcher` ← `article-research`；`writing-architect` ← `article-outline`；`writing-author` ← `article-draft, article-rewrite, article-expand`；`writing-reviewer` ← `article-review`。

---

## 3. 批次、依赖与交付门槛

| 批次 | Task | 依赖 | 交付门槛 |
| --- | --- | --- | --- |
| 批次 0 | T0 契约落盘（Leader 直做） | — | `contracts/2026-10-17-contract-writing-team-collab.md` 存在且与 §2 一致 |
| 批次 1 | T1–T5（GROUP-1，并行） | T0 | 每 WU 自身 pytest 全绿 + `ruff check nanobot/` 无新增告警 |
| 批次 2 | T6–T10（GROUP-2） | 批次 1 合并 | 协作层单测 + `tests/webui/test_writing_wiring.py` 更新后全绿 |
| 批次 3 | T11–T14（GROUP-3，前端） | 批次 2 的 action/事件名冻结 | Vitest 全绿（含 i18n）、`npm run lint`、`npm run build` |
| 批次 4 | T15 尾盘 | 全部 | 集体测试 + 代码审查 + 安全审查 + 浏览器验收 + execution-log |

**真实模型验收（W15）**是唯一允许“条件阻塞”的项：环境无可用的已配置模型时，T15 必须写明缺口，其余项照常交付。

---

## 4. Task 明细

> 每个 Task 的 TDD 步骤统一为五步（下称「TDD 五步」）：
> 1. 写失败测试（文件与用例见各 Task「测试用例」）
> 2. 跑一次确认失败（命令与期望输出见各 Task「验证」）
> 3. 写最小实现
> 4. 跑测试确认通过
> 5. 在 worktree 内提交：`git add <files> && git commit -m "<type>(<scope>): <desc>"`
> 未列出完整测试源码的任务，测试用例表即为该步的验收口径（用例名必须逐字落地）。

### Task 0：契约落盘（Leader 直做，不委派）

**文件：** Create `.ai-runtime-artifacts/contracts/2026-10-17-contract-writing-team-collab.md`

- [ ] 步骤 1：把本计划 §2 全文抽成契约正文，FM 写 `artifact: contract`、`route: api-and-interface-design`、`skills: [api-and-interface-design, writing-plans]`、`source` 引用 spec v2 与旧契约、`status: approved`。
- [ ] 步骤 2：在契约开头写明与旧契约 `2026-10-12-contract-writing-collaboration.md` 的关系：定位口径（§3.1 归一化映射）、幂等表、错误体格式**继承**；动作名、批次/交接、产物表**替换**。
- [ ] 步骤 3：`Read` 回读校验：DDL 与计划 §2.2 逐字一致、动作表 7 条齐全。
- [ ] 步骤 4：提交 `docs(artifacts): 冻结创作团队协作契约`。

**完成定义：** 后续所有 WU 的 L4 输入可只引该契约，不再各自解释 spec §13。

---

### Task 1：七个内置写作 Skill 正文

**Files：** Create `nanobot/skills/article-{brief,research,outline,draft,rewrite,expand,review}/SKILL.md`；Test `tests/writing/test_builtin_skills.py`

**上下文（L2/L3）：** `nanobot/skills/<name>/SKILL.md` 既有格式（参照 `nanobot/skills/summarize/SKILL.md`）；spec §4 的 Skill 边界表；`.ai-runtime-artifacts/research/writing-skill-assets-v2/prepared/*.prompt.md` 与 `upstream/*/SKILL.md` 为**方法素材**（不得整段搬运含署名/推广的内容）。

**测试用例：**

| 用例 | 断言 |
| --- | --- |
| `test_writing_skills_exist_and_names_match_dirs` | 7 个目录都存在 `SKILL.md`，且 front matter `name` == 目录名 |
| `test_writing_skill_description_length` | 每个 `description` 长度落在 1–1024 |
| `test_writing_skill_sections_present` | 正文含「用途 / 输入 / 步骤 / 产物格式 / 边界 / 失败处理」六节标题 |
| `test_writing_skills_have_no_upstream_attribution` | 正文不含 `humanizer-zh`、`op7418`、`ComposioHQ`、`skillhub`、`http` 推广链接与个人路径 `~/` |
| `test_article_review_skill_loaded_by_loader` | `SkillsLoader` 能按名加载 `article-review` 并返回完整正文（非摘要） |

**验证：** `.venv/bin/python -m pytest tests/writing/test_builtin_skills.py -v` → 先 FAIL（文件不存在），后 PASS。

**禁止：** 安装第三方 Skill 到活跃目录；执行上游脚本；把 `prepared/*.prompt.md` 原文直接复制为 SKILL.md（必须按七类边界重写）。

**完成定义：** 7 个 Skill 可被 `SkillsLoader` 完整加载；内容边界与 spec §4 一致。

---

### Task 2：五个写作出厂档案

**Files：** Modify `nanobot/agents/catalog.py`（`FACTORY_PROFILES`）；Test `tests/webui/test_agents_catalog.py`（追加用例）或新建 `tests/writing/test_writing_profiles.py`

**上下文：** `FACTORY_PROFILES` 现有档案写法（`nanobot/agents/catalog.py:84-141`）；`AgentProfile` 字段（`nanobot/agents/models.py:105-130`）；`type` 由服务端按 factory 重算（`nanobot/agents/store.py:163-176`）。

**测试用例：**

| 用例 | 断言 |
| --- | --- |
| `test_writing_profiles_registered` | 5 个 id 全部可由 `factory_for()` 命中 |
| `test_writing_profiles_are_system_category_writing` | `type == "system"`、`category_id == "writing"`、`prompt` 非空且 ≥ 80 字 |
| `test_writing_profiles_skills_whitelist` | 五个档案的 `skills`（include 模式）与 §2.7 配套表逐字一致 |
| `test_writing_profiles_tools_whitelist` | `tools` 为 include 模式且**不含**文件写入 / shell / 发布类工具 |
| `test_writing_profiles_not_deletable_after_projection` | 投影字段含 `origin="builtin"`、`builtinVersion="v1"`、`deletable=False`、`ownerModule="writing"`（T10 落地后生效；本任务先断言结构与 id） |

**验证：** `.venv/bin/python -m pytest tests/webui/test_agents_catalog.py -v`。

**禁止：** 改动既有档案的 id / 字段语义；把写作档案设为通用聊天默认团队。

---

### Task 3：创作内置资产清单与幂等 preparation

**Files：** Create `nanobot/writing/builtin.py`；Modify `nanobot/writing/database.py`（追加 `writing_builtin_assets`，`SCHEMA_VERSION` → `"2"`）；Test `tests/writing/test_writing_builtin.py`

**接口（冻结）：**

```python
# nanobot/writing/builtin.py
WRITING_BUILTIN_VERSION = "v1"

@dataclass(frozen=True)
class BuiltinAsset:
    kind: str          # team | agent | skill | mcp
    logical_id: str
    display_name: str
    protected: bool = True

WRITING_BUILTIN_ASSETS: tuple[BuiltinAsset, ...]      # §2.7 全量清单
WRITING_TEAM_LOGICAL_ID = "writing-team"

class BuiltinAssetRegistry:
    def __init__(self, db: WritingDatabase) -> None: ...
    def is_protected(self, kind: str, logical_id: str) -> bool: ...
    def physical_id(self, kind: str, logical_id: str) -> str | None: ...
    def prepare(self, kind: str, logical_id: str, *, allocator: Callable[[str], str]) -> str: ...
    def project(self, kind: str, logical_id: str) -> dict[str, Any]: ...
```

**测试用例：**

| 用例 | 断言 |
| --- | --- |
| `test_registry_lists_team_five_agents_seven_skills` | 清单数量与 §2.7 一致，logical_id 唯一 |
| `test_prepare_is_idempotent` | 两次 `prepare` 返回同一 `physical_id`，表内仅一行 |
| `test_prepare_avoids_conflict_with_user_asset` | allocator 返回与 logical_id 相同但已被用户占用时，物理 ID 追加后缀且不改动用户对象 |
| `test_protected_write_is_rejected` | 对 `protected=True` 资产调用删除/覆盖判定返回 `BUILTIN_RESOURCE_PROTECTED` |
| `test_ensure_schema_upgrades_v1_db` | 用 v1 库（仅三表）打开后 `ensure_schema()` 补齐新表且 `articles` 数据不变，`_schema_meta.version == "2"` |
| `test_user_asset_conflict_preserved` | 用户已有同名自定义对象时，`project()` 同时给出内置与用户来源，不覆盖 |

**验证：** `.venv/bin/python -m pytest tests/writing/test_writing_builtin.py tests/writing/test_database.py -v`。

**禁止：** 给 `articles` 加列（无 ALTER 路径）；删除或重命名用户自定义对象。

---

### Task 4：协作持久化（collaboration / batch / artifact / handoff / suggestion）

**Files：** Create `nanobot/writing/collab/{__init__,database,models,repository}.py`；Modify `nanobot/writing/database.py`（追加 §2.2 其余表）；Test `tests/writing/test_collab_store.py`

**参照范例：** `nanobot/teams/database.py:109-132`（`team_runs`）、`:178-188`（`team_events` 的 `run_id,seq` 序号）、`:95-108`（append-only 触发器）；`nanobot/teams/repository.py:428-487`（`create_run`）。会话键按 `"websocket:" + chat_id` 构造，chatId 必须落在 `^[A-Za-z0-9_:-]{1,64}$`（`nanobot/webui/session_identity.py:8,9,17-19`），非法即 400 `chat_id is required`。

**接口（冻结）：**

```python
# nanobot/writing/collab/repository.py
class CollabRepository:
    def get_collaboration(self, collaboration_id: str) -> Collaboration: ...
    def get_by_article(self, article_id: str) -> Collaboration | None: ...
    def create_collaboration(self, *, article_id, chat_id, session_key, goal, request_id) -> Collaboration: ...
    def create_batch(self, *, collaboration_id, action, base_revision, snapshot_digest,
                     instruction, frozen, expect_version, request_id) -> Batch: ...
    def finish_batch(self, *, batch_id, status, error_message=None, cancel_generation=None) -> Batch: ...
    def set_collaboration_status(self, *, collaboration_id, status, stage=None,
                                 active_batch_id=..., blocked_reason=None, expect_version) -> Collaboration: ...
    def add_artifact(self, *, collaboration_id, batch_id, attempt, kind, role_id,
                     content, source_ids, gaps) -> Artifact: ...
    def add_handoff(self, *, collaboration_id, batch_id, artifact_id, kind, artifact_version) -> Handoff: ...
    def resolve_handoff(self, *, handoff_id, expected_version, status) -> Handoff: ...
    def snapshot(self, article_id: str, *, limit: int = 50) -> CollabSnapshot: ...
    def advance_seq(self, collaboration_id: str) -> int: ...
    def converge_interrupted(self, workspace_key: str) -> int: ...   # 进程启动时调用
```

**测试用例：**

| 用例 | 断言 |
| --- | --- |
| `test_create_collaboration_binds_article_session` | 一篇文章一个协作；`session_key == f"websocket:{chat_id}"` 且 chat_id 长度受限 |
| `test_same_request_id_replays_without_second_batch` | 同 `client_request_id` 重放返回同一 batch，行数不变 |
| `test_active_batch_uniqueness` | 已有 `queued|running|waiting_author` 批次时新建批次抛 409 语义异常 |
| `test_version_conflict` | `expect_version` 不匹配时抛版本冲突异常（含 expected/actual） |
| `test_advance_seq_monotonic` | `seq` 严格递增，跨批次不合并成虚假全局顺序 |
| `test_artifact_append_only` | 产物 `version` 递增、旧版本保留；`content_digest` 稳定（同内容同值） |
| `test_snapshot_projection_excludes_full_body` | 列表投影不返回全部产物正文，仅详情查询返回 |
| `test_converge_interrupted_marks_inflight` | 启动收敛把 `queued|running` 批次与协作置 `interrupted`，已交付产物保留 |
| `test_cross_workspace_isolation` | 另一 workspace_key 读不到任何行 |

**验证：** `.venv/bin/python -m pytest tests/writing/test_collab_store.py -v`。

**禁止：** 让 SQLite 事务跨越 LLM 或网络等待；在事件循环里直接调用（调用方负责 `to_thread`）。

---

### Task 5：正文定位、建议采纳与撤销

**Files：** Create `nanobot/writing/collab/{offsets,apply}.py`；Test `tests/writing/test_collab_offsets.py`、`tests/writing/test_collab_apply.py`

**口径（继承旧契约 §3.1）：** 位置单位为 **Python 码点**（`len()`），基于 `\r\n`/`\r` → `\n` 归一化文本；`normalize_with_map(body) -> tuple[str, list[int]]` 返回「归一化下标 → 原始下标」映射；采纳时用映射切原始串。前端选区用 JS UTF-16，转换在 T11/T13 负责，服务端只认码点。

**测试用例（offsets）：**

| 用例 | 断言 |
| --- | --- |
| `test_normalize_crlf_and_cr` | `\r\n` 与 `\r` 都归一为 `\n`，映射回原文位置正确 |
| `test_map_handles_cjk_and_emoji` | 中文、emoji（代理对）、重复段落定位切片与原文一致 |
| `test_replace_range_requires_expected_before` | `expected_before` 不等于切片时判废 |
| `test_insert_at_requires_empty_range` | `start != end` 判废 |
| `test_outline_item_target_requires_existing_id` | 提纲项 id 不存在时判废 |

**测试用例（apply）：**

| 用例 | 断言 |
| --- | --- |
| `test_apply_advances_revision_once` | 采纳后 `revision + 1`，`digest` = 新快照 digest |
| `test_apply_marks_same_base_suggestions_stale` | 同 `base_revision` 的其余 pending 建议置 `stale`，返回 `staled_suggestion_ids` |
| `test_apply_rejects_non_pending` | `status != pending` → 409 `suggestion is not pending` |
| `test_apply_rejects_stale_base_revision` | 文章 revision 已前进 → 409 `article_conflict …` |
| `test_batch_apply_single_transaction` | 多个不重叠补丁一次提交，revision 只前进一次 |
| `test_undo_blocks_on_newer_edit` | 当前 revision/digest 不等于采纳结果 → 409 `undo blocked by newer edits` |
| `test_undo_creates_new_revision` | 撤销产生 `result_revision + 1`，不回拨 revision |
| `test_apply_request_id_idempotent` | 同 `client_request_id` 重放不二次推进 revision |

**验证：** `.venv/bin/python -m pytest tests/writing/test_collab_offsets.py tests/writing/test_collab_apply.py -v`。

---

### Task 6：写作任务配方与冻结准备

**Files：** Create `nanobot/writing/collab/{recipe,preparation}.py`；Test `tests/writing/test_collab_recipe.py`

**接口（冻结）：**

```python
@dataclass(frozen=True)
class TaskRecipe:
    action: str
    roles: tuple[str, ...]          # 允许参与的角色白名单
    lead_role: str                  # 主编
    artifact_kinds: tuple[str, ...]
    skills: tuple[str, ...]

RECIPES: dict[str, TaskRecipe]      # §2.1 action → recipe
MAX_MEMBERS = 5
MAX_MEMBER_TASKS_PER_BATCH = 8
MAX_AUTO_DISPATCH_ROUNDS = 2
MEMBER_TIMEOUT_SECONDS = 300
BATCH_TIMEOUT_SECONDS = 900

@dataclass(frozen=True)
class FrozenContext:
    skills: tuple[FrozenSkill, ...]     # name + 正文 + sha256
    model_id: str
    source_scope: dict[str, Any]
    workspace_key: str
    article_id: str
    collaboration_id: str
    batch_id: str

def prepare_frozen(*, loader, recipe, article_snapshot, material_ids, model_id) -> FrozenContext: ...
```

**测试用例：**

| 用例 | 断言 |
| --- | --- |
| `test_recipe_rewrite_selection_only_author` | 局部改写只含 `writing-author` |
| `test_recipe_review_all_includes_reviewer` | 审全文含 `writing-reviewer`，需要核实时可含 `writing-researcher` |
| `test_recipe_full_creation_stages` | `start_full` 覆盖 research/outline/draft/review 四类产物 |
| `test_unknown_role_rejected` | 配方外角色一律拒绝 |
| `test_prepare_inlines_skill_body_and_hash` | `FrozenSkill.body` 是完整正文，`sha256` 与文件内容一致（摘要或仅名称即失败） |
| `test_prepare_rejects_disabled_or_missing_skill` | 禁用/缺失/被档案排除时不降级，返回明确原因 |
| `test_prepare_rejects_unavailable_model` | 显式失效 preset 报错，不用默认模型冒充 |
| `test_batch_waits_release_handles` | 等待作者时无存活模型任务句柄（配合 T8 断言） |

**验证：** `.venv/bin/python -m pytest tests/writing/test_collab_recipe.py -v`。

---

### Task 7：成员产物 envelope 与完整长稿落库

**Files：** Create `nanobot/writing/collab/envelope.py`；Modify `nanobot/teams/repository.py`（① 读回时携带完整 `result` 而非丢弃，供写作域取全文；② `finish_task` 拒绝 `cancelling` 状态下的晚到成功结果）、`nanobot/teams/scheduler.py`（仅在**下游准备阶段**对写作任务暴露完整产物引用，不改通用 8000 字符摘要语义）、`nanobot/agent/subagent.py`（**最小**接入点，允许宿主提供已冻结的 Skill 正文）；Test `tests/writing/test_collab_envelope.py`、`tests/webui/test_writing_adapter.py`、`tests/teams/test_repository.py`（追加晚到结果用例）

**问题（spec §13.4）：** 现有下游输入只拼 `result.summary` 并截断到 `UPSTREAM_CONTEXT_BUDGET=8000`（`nanobot/teams/scheduler.py:57,476-497,553`），`fullResult` 超长时虽落 DB 但读回被丢弃（`nanobot/teams/repository.py:969,1360`），且不在 wire 白名单；`execute_frozen` 只冻结 `ResolvedAgent`（`nanobot/agent/subagent.py:641`）。初稿与资料包必须成为文章域的类型化产物，**不改** Team 的通用摘要协议语义。

**测试用例：**

| 用例 | 断言 |
| --- | --- |
| `test_envelope_accepts_allowed_kinds_per_role` | 每个角色只接受其允许的 `kind` |
| `test_envelope_rejects_identity_fields` | 输出中的 `articleId/runId/version/revision/digest` 一律丢弃并由服务端补齐 |
| `test_envelope_rejects_unknown_source_ids` | 引用不存在的 `sourceIds` → 校验失败，不产生可采纳卡 |
| `test_envelope_length_limits` | 超限拒绝或分节，不静默裁切；超限 message 固定 |
| `test_artifact_persists_full_content_not_summary` | 落库 `content_json` 含完整分节正文，Team 侧仅收到摘要 + 产物引用 |
| `test_malformed_output_is_diagnostic_only` | 无法解析的输出只作为失败诊断/只读原文，不生成采纳卡；允许一次格式修正尝试 |
| `test_review_receives_full_draft` | 审校任务准备阶段展开完整初稿（>8000 字符仍完整） |
| `test_finish_task_rejects_late_success_after_cancelling` | `finish_task` 在 `cancelling` 状态下拒绝 `succeeded`/`completed` 晚到结果（仓库层守卫） |
| `test_full_result_readable_for_writing_adapter` | 写作适配可读回完整 `result`；通用聊天的下游摘要行为不变（回归） |

**验证：** `.venv/bin/python -m pytest tests/writing/test_collab_envelope.py tests/webui/test_writing_adapter.py -v`。

---

### Task 8：协作批次运行器（启动/交接/取消/超时/重启）

**Files：** Create `nanobot/writing/collab/runner.py`；Modify `nanobot/cli/gateway_runtime.py`（启动收敛装配）、`nanobot/webui/gateway_services.py`（把协作 runner 与 Team 栈一起装配）；Test `tests/writing/test_collab_runner.py`

**复用与约束（见 §0.1）：**
- 启动批次：`TeamService.save_team` 播种内置团队 → `build_team_turns(service, host, workspace)` → 直接写 `session.metadata["team_binding"]`（`TeamBinding.to_dict()`），**不使用** TTL 暂存表（`binding.py:23,26,29,124`、`turn.py:209,173`）。
- 批次期限由**自动批次自持的期限任务**保证（`BATCH_TIMEOUT_SECONDS=900`），超时走同一 cancel/清理链；不能只依赖 `TeamScheduler._past_run_deadline`（只在起跑前检查，`scheduler.py:457,472,714-715`）。
- 取消：调用既有 `TeamRunController.cancel_run` / `scheduler.cancel`，并在文章域独立执行「`cancel_generation` + 活动状态」闸门——仓储层不拦 `cancelling → succeeded`（`repository.py:71-77,937`）。
- 重启：`TeamService.recover_after_restart` 之外，协作域 `converge_interrupted()` 把在途批次标 `interrupted`；不重放可能产生副作用的工具调用（`service.py:298`）。

**接口（冻结）：**

```python
class CollabRunner:
    async def start(self, request: StartCollabRequest) -> BatchHandle: ...
    async def reply(self, *, collaboration_id, feedback, expect_version) -> Batch: ...
    async def confirm(self, *, collaboration_id, handoff_id, expected_handoff_version,
                      outline, article_revision, article_digest) -> ConfirmResult: ...
    async def cancel(self, *, collaboration_id, active_batch_id) -> Collaboration: ...
    async def retry(self, *, collaboration_id, batch_id, expect_version) -> Batch: ...
    def on_team_event(self, event: TeamEvent) -> None: ...        # 收口：校验闸门后落产物
    async def on_batch_timeout(self, batch_id: str) -> None: ...
```

**测试用例：**

| 用例 | 断言 |
| --- | --- |
| `test_waiting_author_ends_batch_without_live_handle` | 提纲交接时批次结束、协作 `waiting_author`，无存活模型任务与锁 |
| `test_confirm_creates_next_batch_in_same_collaboration` | 确认后新建批次，关联同一协作，使用新提纲版本 |
| `test_confirm_rejects_stale_handoff_version` | 旧交接版本 → 409 `handoff version conflict` |
| `test_cancel_generation_blocks_late_results` | 取消后晚到 `task.finished` 不产生可采纳建议，状态不回落 |
| `test_batch_timeout_uses_same_cancel_chain` | 15 分钟期限触发 cancel/清理链（不是只填配置字段） |
| `test_retry_reuses_valid_artifacts` | 重试创建新 attempt，复用仍有效产物，不重复采纳 |
| `test_restart_marks_interrupted` | 装配时把在途批次标 `interrupted`，已交付产物保留，不重放副作用工具调用 |
| `test_second_active_batch_rejected` | 同文章第二个活动批次 409 |

**验证：** `.venv/bin/python -m pytest tests/writing/test_collab_runner.py -v`。

---

### Task 9：WS 动作、事件与读接口

**Files：** Create `nanobot/webui/writing_collab_{api,routes}.py`；Modify `nanobot/webui/settings_routes.py`、`nanobot/webui/ws_http.py`、`nanobot/bus/outbound_events.py`、`nanobot/webui/outbound_projection.py`、`nanobot/channels/websocket/runtime.py`、`nanobot/webui/gateway_services.py`（操作三元组）、`tests/webui/test_writing_wiring.py`；Test `tests/webui/test_writing_collab_api.py`、`tests/webui/test_writing_collab_routes.py`、`tests/webui/test_writing_collab_wiring.py`

**改动面（已核实为 8 处）：** ① `writing_collab_routes.py` 动作集 ② `_SYSTEM_ROUTES` ③ `_WRITING_MUTATION_PATHS` → `_SETTINGS_MUTATION_PATHS` ④ `ws_http.py::_WEBUI_MUTATION_PATHS` ⑤ handler dispatch 分支 ⑥ operations 三元组（routes + `gateway_services.build_writing_operations` + router 503 兜底） ⑦ 前端 `writing-collab-api.ts`（T11） ⑧ `settings_routes.py` 的 to_thread / 事件循环分支。

**事件推送缺口（已核实）：** 全仓没有 team run 的 WS 推送，`event→帧` 只能走既有广播入口 `nanobot/webui/inbound_commands.py:1486-1490`（`broadcast_webui_event`）+ `nanobot/channels/websocket/runtime.py` 的 `send_*` fan-out 范式；`outbound_projection.py` 现有 `isinstance` 分支链是事件上线的必经点，**不是可选优化**。

**测试用例：**

| 用例 | 断言 |
| --- | --- |
| `test_all_collab_actions_registered` | 7 个写动作在路由三表齐全；HTTP 直连 → 405 |
| `test_control_actions_run_on_event_loop` | `writing.start/cancel/retry` 走事件循环分支；sync 入口 → 500 |
| `test_read_actions_over_authenticated_http` | 无 token → 401；跨工作区 → 404/403 |
| `test_client_request_id_required` | 缺 `client_request_id` → 400 |
| `test_rejects_client_supplied_team_or_agent` | payload 含 `teamId`/`agentId`/`skillPath` → 400/忽略且不影响固定团队 |
| `test_events_projected_to_frames` | 三种 `WritingEvent` 投影为对应帧名，`chat_id` 为协作会话 |
| `test_event_idempotent_merge` | 重复/乱序事件不倒退状态（前端侧由 T11 覆盖，服务端只发一次状态跃迁） |
| `test_wiring_locks_route_tables` | 接线测试覆盖新动作（防漏登记回归） |

**验证：** `.venv/bin/python -m pytest tests/webui/test_writing_collab_api.py tests/webui/test_writing_collab_routes.py tests/webui/test_writing_collab_wiring.py tests/webui/test_writing_wiring.py -v`。

---

### Task 10：内置归属投影与受保护写入口 + `.enabled` 修复

**Files：** Modify `nanobot/agents/store.py`、`nanobot/webui/agents_api.py`、`nanobot/webui/skills_api.py`、`nanobot/webui/skills_marketplace.py`、`nanobot/teams/service.py`、`nanobot/webui/mcp_presets_api.py`、`nanobot/webui/gateway_services.py`、`nanobot/webui/settings_routes.py`（Team 业务码）；Test `tests/webui/test_builtin_protection.py`

**字段（spec §7.1）：** 管理投影统一增加 `ownerModule="writing"`、`origin="builtin"`、`builtinVersion="v1"`、`deletable=false`、`protectionReason`；保留既有 `type=system`、Skill `source=builtin` 兼容语义。

**强制点清单（只读取证已定位，逐点落地）：**

| 资源 | 强制点 | 现状 |
| --- | --- | --- |
| Agent | `nanobot/agents/store.py:163-176`（save 按 factory 重算 type）、`:178-189`（delete 409）、`:195-196`（reset 409）、`nanobot/webui/agents_api.py:104-107`（团队引用 409） | 已有 system 保护，缺「创作空间归属」维度 |
| Team | `nanobot/teams/service.py:122`（save）、`:166`（archive）、`:171`（clone）；业务码表 `nanobot/webui/teams_routes.py:152-157` | **无内置概念**，需新增 |
| Skill | `nanobot/webui/skills_api.py:98-104`（delete 403）、`:174`（`deletable`）、`nanobot/webui/skills_marketplace.py:426-435`（同名 install 未拒绝） | 有 builtin 保护，缺同名覆盖拒绝与创作归属 |
| MCP | `nanobot/webui/mcp_presets_api.py:1564`（remove）、`:1471-1491`（import 覆盖）、`:1552-1562`（enable 覆盖 preset）、`:806-811`（`trust.level="builtin"`） | **无保护**，需补（首批未提供内置预设时按 skip 用例处理） |
| 装配 | `nanobot/webui/gateway_services.py:284` `bool(...agents.teams)` → 改为 `.agents.teams.enabled` | 已确认为真实缺陷 |

**测试用例：**

| 用例 | 断言 |
| --- | --- |
| `test_agent_projection_has_writing_builtin_fields` | 5 个写作档案投影含全部新字段 |
| `test_delete_builtin_agent_rejected_409` | 删除内置写作 Agent → 409 `builtin resource is protected: agent/writing-editor` |
| `test_reset_builtin_agent_rejected` | reset 内置写作 Agent → 409 |
| `test_team_archive_clone_save_guards` | 内置团队不可归档/覆盖；可复制为自定义 |
| `test_skill_same_name_install_does_not_shadow_builtin` | 同名导入不覆盖保护身份，运行记录显示实际来源 |
| `test_mcp_remove_import_guards_if_preset_provided` | 若首批提供内置 MCP 预设则 remove/import 受保护；未提供时该用例标记 skip 并说明 |
| `test_user_assets_unaffected` | 普通用户资源删除/覆盖语义不变（无 403/409 回归） |
| `test_teams_enabled_flag_respected` | `agents.teams.enabled=false` 时装配的 `TeamService.enabled is False`（修 `bool(obj)` 缺陷） |
| `test_protected_code_is_uniform` | 统一业务码 `BUILTIN_RESOURCE_PROTECTED` 出现于所有受保护写入口 |

**验证：** `.venv/bin/python -m pytest tests/webui/test_builtin_protection.py tests/webui/test_agents_store.py tests/webui/test_skills_api.py tests/webui/test_teams_routes.py tests/webui/test_mcp_presets_api.py -v`。

**禁止：** 借本任务改变全部既有系统 Agent 的可编辑语义；把 403/409 行为强行迁移到其他资源。

---

### Task 11：前端协作 API 与事件归并

**Files：** Create `webui/src/lib/writing-collab-api.ts`；Test `webui/src/tests/writing-collab-api.test.ts`

**参照：** `webui/src/lib/writing-api.ts`（HTTP 读 + WS mutation 封装的既有模式：读 `fetchArticleList:245`/`fetchArticle:271`，写 `createArticle:282`/`saveArticle:305`）、`webui/src/lib/api.ts:83-89,91,139`（`WebUIMutationTransport` / `request` / `mutation`）、`webui/src/lib/nanobot-client.ts:938-975`（`requestMutation` 帧，重连同 id 重放）与 `:799`（`onChat` 订阅）、`webui/src/hooks/useTeamProgress.ts:139-166`（事件归并范式：`parseTeamFrame` → `applyTeamEvent`/`applyTeamSnapshot`，`:78-115` 缺口补拉）。

**事件订阅口径：** 协作事件天然带 `chat_id`（协作绑定会话），因此可直接复用 `onChat(chatId)` + T11 自己的 reducer；若后续需要文章级入口，再按 `onChat` 仿写，不在本批新增第二套订阅机制。

**测试用例：**

| 用例 | 断言 |
| --- | --- |
| `test_start_sends_client_request_id` | 每次调用生成稳定 `clientRequestId`，重试复用同一 id |
| `test_snapshot_maps_snake_case` | snake_case → camelCase 映射完整，未知字段不炸 |
| `test_event_merge_ignores_stale_version` | 旧 `version/seq` 事件被忽略 |
| `test_event_merge_is_idempotent` | 重复事件不产生重复产物卡 |
| `test_utf16_to_codepoint_selection` | 选区含中文/emoji 时转换后码点范围正确 |
| `test_apply_and_undo_payloads` | 采纳/撤销 payload 带 `baseRevision` 与 `clientRequestId` |
| `test_snapshot_reset_on_reconnect` | 重连先拉快照并按游标补拉或 reset |

---

### Task 12：创作搭档侧栏与产物卡

**Files：** Create `webui/src/components/creative/{use-writing-collab.ts,WritingPartnerPanel.tsx,ArtifactCard.tsx,collab-labels.ts}`；Test `webui/src/tests/writing-partner-panel.test.tsx`、`webui/src/tests/writing-artifact-card.test.tsx`

**结构（spec §12.1）：** 顶部「创作搭档 · 内置团队」+ 本篇阶段；中部产物与主操作；底部固定输入框；快捷任务可折叠；成员活动收进「查看协作过程」。**不出现团队选择器、节点编辑、工作流配置**。

**可直接复用（只读取证已定位）：** 进度与任务列表 `components/thread/TeamProgressPanel.tsx:36-142`（状态色 `:9-17`、停止/重试 `:109-140`）；成员产物卡 `TeamMemberCard.tsx`；待作者处理卡 `AskQuestionCard.tsx:41-110`；活动流 `AgentActivityCluster.tsx` / `activity/ActivityStep.tsx`；右栏宽度与断点见 §0.2。

**测试用例：**

| 用例 | 断言 |
| --- | --- |
| `test_panel_renders_without_team_selector` | 无任何团队/角色/Skill 选择控件 |
| `test_primary_action_uses_article_context` | 主题已填时直接发起，不要求填写团队需求表 |
| `test_artifact_cards_actions` | 六类卡片各自主操作与边界（提纲「按此提纲写」、改写先展示原文/改写与理由） |
| `test_task_progress_states` | 显示「正在查资料」等作者可理解状态；无伪造百分比/预计时间 |
| `test_selection_required_for_rewrite` | 无选区时提示选择范围，不发请求 |
| `test_error_preserves_input` | 失败时保留作者输入与选区 |
| `test_focus_not_stolen_by_stream` | 流式更新不抢输入框焦点；切换产物保留已输入反馈 |
| `test_next_step_always_present` | 任务结束展示一个明确下一步，不用泛问结尾 |
| `test_a11y_live_region_and_keyboard` | 状态有 live region 且关键动作可键盘触达 |

---

### Task 13：编辑器与工作区集成 + 预览重复修复

**Files：** Modify `webui/src/components/creative/ArticleEditor.tsx`（右侧占位 → 面板、选区追踪）、`webui/src/components/creative/CreativeWorkspace.tsx`（断点布局、面板切换、**B01 修复**）；Test `webui/src/tests/creative-partner-integration.test.tsx`、`webui/src/tests/creative-workspace.test.tsx`（追加）

**落点（只读取证）：** 整块替换 `ArticleEditor.tsx:413-426`（保留 `w-[260px] shrink-0 overflow-y-auto border-l border-border`，中栏 `flex-1:369-411` 不受影响）；三栏容器 `:326`；折叠仿 `FilePreviewPanel.tsx:100-122`（CSS 变量 + `transition-[width]`，关闭置 0px），断点仿 `ThreadFrame.tsx:24`；需在 `ArticleEditor` props（`:38-61`）与 EditorHost 透传（`CreativeWorkspace.tsx:342-368`）新增 client/token/transport 与 `articleId/revision`；preview 模式无 gateway，按 `CreativeWorkspace.tsx:408-423` 的内存 transport 降级（面板显示只读占位，不发真实请求）。

**B01 根因（已取证）：** preview transport 的 `applyPreviewMutation` 已更新集合并 `writePreview`（`CreativeWorkspace.tsx:414`），`handleCreate` 又 prepend 同一对象（`:548`）。修复要求：**预览列表更新责任唯一**，并补回归用例。

**测试用例：**

| 用例 | 断言 |
| --- | --- |
| `test_create_once_returns_single_card` | 预览模式一次新建 → 返回列表只有一张对应卡（B01 回归） |
| `test_editor_right_panel_is_partner` | 右侧渲染创作搭档面板，不再渲染 AI 占位 |
| `test_selection_tracks_codepoints` | 选区变化把 UTF-16 范围转换为码点范围后再发起 |
| `test_layout_breakpoints` | 1024–1439px 折叠素材栏；<1024px 切换文章/搭档面板；390px 全高面板 |
| `test_close_panel_does_not_stop_task` | 收起/切换面板不停止任务，返回后恢复状态 |
| `test_unsaved_guard_before_send_and_apply` | 有未保存改动时先走既有保存队列；冲突时保留输入与选区 |

---

### Task 14：i18n 全量同步与既有失败修复

**Files：** Modify `webui/src/i18n/locales/<10 locales>/common.json`；Test `webui/src/tests/i18n.test.tsx`（既有结构断言）

**要求：** 新增 `creative.partner.*`（标题、内置标识、六类产物、状态、错误、按钮）在**全部 locale**（`webui/src/i18n/locales/<code>/common.json`，10 种，`config.ts:3-14`）同步；同时修复既有结构失败：英文 `en/common.json:277-278` 有 `settings.teams.rosterCount_one/_other`，`zh-CN:276` 与 `zh-TW:1345` 缺失，被 `webui/src/tests/i18n.test.tsx:582-603` 判 missing（使用点 `TeamEditorDialog.tsx:380`）。**新增复数键必须在所有语言的形状上与英文一致**（缺一即失败）。

**测试用例：**

| 用例 | 断言 |
| --- | --- |
| `test_i18n_structure_aligned` | 既有 `keeps every locale aligned with the English resource shape` 通过（missing/extra/interpolationMismatches 全空） |
| `test_partner_keys_present_in_all_locales` | 新增 key 在每个 locale 存在且非空 |
| `test_no_hardcoded_partner_copy` | 面板组件不硬编码中文文案（走 `t(key, default)`） |

---

### Task 15：尾盘（Leader 落盘，不委派实现）

**产出：** `.ai-runtime-artifacts/verifications/2026-10-17-writing-team-sidebar-collective-test.md`、`.ai-runtime-artifacts/reviews/2026-10-17-writing-team-sidebar-code-review.md`、`.ai-runtime-artifacts/reviews/2026-10-17-writing-team-sidebar-security-review.md`、`.ai-runtime-artifacts/execution-logs/2026-10-17-writing-team-sidebar-execution-log.md`、更新 `tracking/DISPATCH-TRACK-2026-10-17-writing-refinement.md` 或新建同批 track。

- [ ] 集体测试：按下表全量跑一次并保存原始日志。
- [ ] 并行扇出 `requesting-code-review` + `security-and-hardening`（+ 按需 `performance-optimization`）。
- [ ] 浏览器验收：390×844 / 768×1024 / 1280×720 / 1440×900，覆盖窄屏面板、键盘、停止/重连/返回文章、正文不被覆盖。
- [ ] W15 真实模型端到端：空白文章 → 提纲编辑确认 → 初稿 → 审校 → 采纳；有稿局部改写；仅用作者素材；真实角色与 Skill 证据。**无可用模型时明确记录缺口。**
- [ ] 更新 spec v2 的 `status`（实现完成后由 draft/approved:false 改为 implemented）与 `## Next`。

---

## 5. 验证矩阵（spec §15.2 落地口径）

| 层次 | 命令 | 期望 |
| --- | --- | --- |
| 纯逻辑 | `.venv/bin/python -m pytest tests/writing -q` | 全绿（含新增 offsets/envelope/recipe 用例） |
| API/数据库 | `.venv/bin/python -m pytest tests/webui/test_writing_collab_api.py tests/webui/test_writing_collab_routes.py tests/webui/test_writing_collab_wiring.py tests/webui/test_writing_wiring.py -q` | 全绿；接线测试覆盖 7 个新动作 |
| 管理面保护 | `.venv/bin/python -m pytest tests/webui/test_builtin_protection.py tests/webui/test_agents_store.py tests/webui/test_skills_api.py tests/webui/test_teams_routes.py -q` | 全绿；普通资源无回归 |
| Team 集成 | `.venv/bin/python -m pytest tests/teams tests/webui/test_teams_e2e.py tests/webui/test_writing_adapter.py -q` | 全绿 |
| Python 静态 | `.venv/bin/python -m ruff check nanobot/`；`.venv/bin/python -m basedpyright` | 无新增告警 |
| 前端单测 | `cd webui && npm run test` | 全绿（含 i18n，0 failed） |
| 前端静态 | `cd webui && npm run lint && npm run build` | exit 0 |
| gateway 冒烟 | 启动 gateway 后 `/api/writing/collab` 认证读 + 一次 `writing.start`（可用 mock 模型） | 401/200/事件帧符合契约 |
| 浏览器 | `agent-browser` 或 `browser-testing-with-devtools` 四档视口 | 见 Task 15 |
| 真实模型 | 工作区已配置模型 | 见 Task 15（否则记录缺口） |

---

## 6. 计划自检

**Spec 覆盖：** §1 目标/边界→T1–T14；§3 侧栏→T12/T13；§4 团队与 Skill→T1/T2/T6；§5 流程与限制→T6/T8；§6 上下文与采纳→T5/T7；§7 内置保护→T3/T10；§8 MCP→T10（可选预设，未提供则 skip 用例）；§9 实现边界→§2 契约 + T3–T10；§10 W01–W15→§5 验证矩阵 + T15；§12 作者旅程→T12/T13；§13 后端配合→T4/T6/T7/T8/T9/T10；§14 素材清理→T1；§15 门槛→T13（B01）/T14（i18n）/T15。

**已核实的既有缺口（本计划已纳入修复）：** ① `gateway_services.py:284` `bool(...agents.teams)`（T10）② 预览重复插入 B01（T13）③ i18n `rosterCount_one/_other`（T14）④ 写作库无 ALTER 迁移 → 只新增表（T3/T4）。

**类型一致性：** `collaboration_id` / `batch_id` / `artifact_id` / `handoff_id` / `suggestion_id` / `client_request_id` / `base_revision` / `snapshot_digest` 在本计划 §2 与 T4–T13 保持同名；前端 camelCase 映射集中在 T11 一处。

**TDD 合规：** 每个产出生产代码的 Task 都以测试用例表开头，并执行 TDD 五步；测试用例覆盖 happy path、边界（空选、超限、重复段落、emoji）与错误路径（409/413/503）。
**已知偏差（如实声明）：** 本计划未内联全部测试源码与实现源码（14 个 WU 的等价信息分布在 §2 契约、各 Task 用例表与接口签名中）。执行时 WU 必须先落测试并跑到失败，再写实现；Plan 的「无占位符」要求以「用例名 + 断言 + 接口签名逐字冻结」满足，不允许 WU 自行改接口。

**未决项：** ① 首批是否提供内置 MCP 预设（spec §8 允许不提供）；② W15 是否有可用模型；③ `writing.start` 的 `snapshot_digest` 计算口径沿用 A 契约 `snapshot_digest()`，跨端 digest 一致性需在 T9 用真实 payload 校验一次。

## Next

- 计划已落盘并置 `approved: true`（依据用户在任务开头的「开始吧」一次性授权）。
- 实施入口：`.ai-runtime-artifacts/plans/2026-10-17-writing-team-sidebar-dispatch.md` 的执行图；按批次 0 → 1 → 2 → 3 → 尾盘 顺序执行。
- 需要调整 WU 拆分或依赖 → 只改 `*-dispatch.md`；需要调整接口或验收口径 → 改本计划 §2 / Task 并同步契约。
