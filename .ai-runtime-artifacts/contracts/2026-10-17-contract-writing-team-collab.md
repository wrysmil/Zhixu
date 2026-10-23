---
artifact: contract
route: api-and-interface-design -> superpowers:writing-plans
skills:
  - api-and-interface-design
  - writing-plans
skills_evidence:
  - .claude/skills/api-and-interface-design/SKILL.md
  - /Users/mima0000/.agents/skills/writing-plans/SKILL.md
source:
  - .ai-runtime-artifacts/plans/2026-10-17-writing-team-sidebar-plan.md（§2 契约冻结）
  - .ai-runtime-artifacts/specs/2026-10-17-writing-team-sidebar-spec.md（§4/§7/§9/§13.6）
  - .ai-runtime-artifacts/contracts/2026-10-12-contract-writing-collaboration.md（继承项）
  - .ai-runtime-artifacts/contracts/2026-10-12-contract-writing-article-store.md（A 契约：错误体 / CAS / digest）
created_at: 2026-10-17
status: approved
approved: true
approval_evidence: 由已批准计划（.ai-runtime-artifacts/plans/2026-10-17-writing-team-sidebar-plan.md，approved: true）§2 派生
---

# 创作空间固定团队 · 协作契约（v1）

**与旧契约的关系：** 继承 `.ai-runtime-artifacts/contracts/2026-10-12-contract-writing-collaboration.md` 的①定位口径（§3.1 归一化映射，Python 码点）、②`article_requests` 幂等表、③A 契约错误体格式与 CAS 语义；**替换**其动作名（单 Agent → 固定团队批次）、任务模型（task → collaboration/batch/artifact/handoff）与产物表。

**实施基线：** `nanobot/writing/`（schema v1，3 张表，无 ALTER 迁移路径）、`nanobot/teams/`（无 builtin 工厂）、`nanobot/webui/`（HTTP 读 + 认证 WS mutation 写 + 事件投影）。行号为 2026-10-17 侦查位置，实施前复核。

---

## 1. 领域对象与枚举（持久化即协议，snake_case）

| 对象 | 字段 |
| --- | --- |
| Collaboration | `id, article_id, chat_id, session_key, goal, status, stage, active_batch_id, cancel_generation, version, seq, last_handoff_id, created_at, updated_at` |
| Batch | `id, collaboration_id, attempt, team_run_id, base_revision, snapshot_digest, action, instruction, status, cancel_generation, frozen, error_message, created_at, finished_at` |
| Artifact | `id, collaboration_id, batch_id, attempt, kind, role_id, version, content, content_digest, source_ids, gaps, status, created_at` |
| Handoff | `id, collaboration_id, batch_id, artifact_id, kind, artifact_version, status, created_at, resolved_at` |
| Suggestion | `id, collaboration_id, artifact_id, article_id, base_revision, snapshot_digest, target, before, after, summary, notes, source_material_ids, status, created_at, applied_revision?` |

枚举（英文；展示走 i18n，不得硬编码中文）：

- `collaboration.status`：`idle | queued | running | waiting_author | completed | failed | cancelled | interrupted`
- `collaboration.stage`：`'' | brief | research | outline | draft | review | rewrite`
- `batch.status`：`queued | running | waiting_author | completed | failed | cancelled | interrupted`
- `artifact.kind`：`brief | material_list | outline | draft | rewrite | review`
- `artifact.role_id`：`writing-editor | writing-researcher | writing-architect | writing-author | writing-reviewer`
- `handoff.kind`：`outline_confirm | materials_request | final_accept`
- `handoff.status`：`pending | confirmed | superseded`
- `suggestion.status`：`pending | applied | rejected | stale | reverted`
- `action`：`start_full | ideate | research | outline | draft | rewrite_selection | review_all | reply`
- `target.kind`：`replace_range | insert_at | outline_item`
- `frozen`（batch）：`{skills: [{name, path, sha256}], model_id, source_scope, workspace_key, article_id, collaboration_id, batch_id}`

## 2. 持久化 DDL（schema v2）

追加进 `nanobot/writing/database.py::_SCHEMA_STATEMENTS`，`SCHEMA_VERSION` 由 `"1"` 改 `"2"`。**只新增表，不给 `articles` 加列**（该库无 ALTER 迁移）。

```sql
CREATE TABLE IF NOT EXISTS writing_builtin_assets (
  workspace_key   TEXT NOT NULL,
  asset_kind      TEXT NOT NULL,        -- team | agent | skill | mcp
  logical_id      TEXT NOT NULL,
  builtin_version TEXT NOT NULL,        -- v1
  physical_id     TEXT NOT NULL,
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

幂等：协作写动作复用 `article_requests`（PK `workspace_key,request_id`，`op` = 动作名）；`writing.start` 在新建批次前按 `client_request_id` 查重，重放返回既有 `collaboration_id/batch_id`，不二次执行。

会话键：`session_key = "websocket:" + chat_id`；`chat_id` 必须匹配 `^[A-Za-z0-9_:-]{1,64}$`（会话键 ≤74 字符）。

## 3. WS 写动作（仅认证 WS mutation；HTTP 直连 405）

| action | 路径 | 必需输入 | 成功结果 |
| --- | --- | --- | --- |
| `writing.start` | `/api/writing/collab/start` | `client_request_id, article_id, chat_id, action, base_revision, snapshot_digest, instruction?, selection?, material_ids?` | `{collaboration_id, batch_id, status, stage, session_key, active_batch_id}` |
| `writing.reply` | `/api/writing/collab/reply` | `client_request_id, collaboration_id, feedback, expect_version` | `{collaboration_id, batch_id, status}` |
| `writing.confirm` | `/api/writing/collab/confirm` | `client_request_id, collaboration_id, handoff_id, expected_handoff_version, outline?, article_revision, article_digest` | `{collaboration_id, batch_id, article_revision, article_digest, handoff_status}` |
| `writing.cancel` | `/api/writing/collab/cancel` | `client_request_id, collaboration_id, active_batch_id` | `{collaboration_id, status, cancel_generation}` |
| `writing.retry` | `/api/writing/collab/retry` | `client_request_id, collaboration_id, batch_id, expect_version` | `{collaboration_id, batch_id, attempt, status}` |
| `writing.apply` | `/api/writing/collab/apply` | `client_request_id, suggestion_id, article_id, base_revision` | `{suggestion_id, article_id, revision, digest, staled_suggestion_ids, undo_id}` |
| `writing.undo` | `/api/writing/collab/undo` | `client_request_id, undo_id, base_revision` | `{undo_id, article_id, revision, digest, suggestion_id}` |

- **循环绑定动作**（必须在事件循环上执行，参照 `teams_domain.TEAMS_CONTROL_ACTION_NAMES` 分支）：`writing.start` / `writing.cancel` / `writing.retry`；其余走 `asyncio.to_thread`。sync 入口调用控制动作 → 500 `<action> must run on the event loop`。
- 读动作（认证 HTTP GET，`check_api_token`）：`writing.collab.get`（`GET /api/writing/collab?article_id=`）、`writing.artifact.get`（`GET /api/writing/collab/artifact?artifact_id=`）。
- `writing.collab.get` 返回：`{collaboration, batches, artifacts, handoffs, suggestions, last_version, last_seq, next_actions, blocked_reason}`（列表投影**不含**产物全文；详情查询才返回）。
- **不接受** `teamId` / `agentId` / `skillPath`；团队、角色、Skill、模型一律服务端解析。
- 新增动作需同步登记路由三表：`settings_routes.py::_SYSTEM_ROUTES`、`_WRITING_MUTATION_PATHS`→`_SETTINGS_MUTATION_PATHS`、`ws_http.py::_WEBUI_MUTATION_PATHS`，并更新 `tests/webui/test_writing_wiring.py`。

## 4. 错误（纯文本 message，体格式沿用 A 契约 §6）

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
| 建议目标非法（`OffsetError.code`） | 400 | `suggestion target is invalid: <code>`（code ∈ `out_of_range` / `expected_before_mismatch` / `insert_range_not_empty` / `outline_item_not_found` / `unsupported_kind`） |
| 批量补丁相互重叠 | 409 | `suggestion targets overlap` |
| 撤销条目不存在 | 404 | `undo entry not found` |
| 撤销被后续编辑挡住 | 409 | `undo blocked by newer edits` |
| 撤销已用 | 409 | `undo already applied` |
| 版本冲突 | 409 | `article_conflict current_revision=<int> article_id=<id>` |
| 上下文超限 | 413 | `collaboration context too large; shrink the selection or materials` |
| 内置资源受保护 | 409 | `builtin resource is protected: <asset_kind>/<logical_id>`（业务码 `BUILTIN_RESOURCE_PROTECTED`） |
| 团队被关闭 | 409 | `writing team is disabled for this workspace` |
| 模型不可用 | 409 | `writing model is not available: <model_id>` |
| 协作层未装配 | 503 | `writing collaboration service is not configured` |
| 未知动作 | 404 | `unknown writing collaboration action: <action>` |
| 控制动作走 sync 入口 | 500 | `<action> must run on the event loop` |
| 缺 `client_request_id` | 400 | `client_request_id is required` |
| 缺 `article_id` / `chat_id` / `suggestion_id` / `undo_id` / `handoff_id` | 400 | `<field> is required` |

保留既有 A 契约错误：`article not found`(404)、`article deleted`(409)、`article_conflict …`(409)、`request_id was already used for a different WebUI mutation`(409)。

## 5. 事件

`nanobot/bus/outbound_events.py` 新增 frozen 基类 `WritingEvent`（含 `event_name` + `body`）与子类；`nanobot/webui/outbound_projection.py` **一个** `isinstance(event, WritingEvent)` 分支；`nanobot/channels/websocket/runtime.py` 一个 `send_writing_event(chat_id, name, body)`。帧：`{"event": <event_name>, "chat_id": <协作会话 chatId>, ...body}`。

| event_name | body |
| --- | --- |
| `writing_collab` | `{collaboration_id, status, stage, active_batch_id, version, seq, blocked_reason?}` |
| `writing_artifact` | `{collaboration_id, artifact_id, kind, version, summary}` |
| `writing_handoff` | `{collaboration_id, handoff_id, kind, artifact_id, artifact_version, status}` |

状态推进每个跃迁各推一次；重连先读 `writing.collab.get` 快照；前端按 `version`/`seq` 幂等归并，旧事件不得倒退状态。

## 6. 前端类型（`webui/src/lib/writing-collab-api.ts`）

```ts
export type CollabStatus = "idle" | "queued" | "running" | "waiting_author"
  | "completed" | "failed" | "cancelled" | "interrupted";
export type CollabStage = "" | "brief" | "research" | "outline" | "draft" | "review" | "rewrite";
export type CollabAction = "start_full" | "ideate" | "research" | "outline"
  | "draft" | "rewrite_selection" | "review_all" | "reply";
export type ArtifactKind = "brief" | "material_list" | "outline" | "draft" | "rewrite" | "review";
export interface CollabSnapshot {
  collaboration: Collaboration | null;
  batches: CollabBatch[]; artifacts: CollabArtifact[]; handoffs: CollabHandoff[];
  suggestions: CollabSuggestion[];
  lastVersion: number; lastSeq: number; nextActions: CollabAction[]; blockedReason: string | null;
}
export interface StartCollabRequest {
  clientRequestId: string; articleId: string; chatId: string; action: CollabAction;
  baseRevision: number; snapshotDigest: string; instruction?: string;
  selection?: { start: number; end: number; text: string }; materialIds?: string[];
}
export interface SelectionRange { start: number; end: number }   // Python 码点
export function utf16RangeToCodepoints(body: string, start: number, end: number): SelectionRange;
export const collabApi: {
  getSnapshot(articleId: string): Promise<CollabSnapshot>;
  getArtifact(artifactId: string): Promise<CollabArtifact>;
  start(req: StartCollaborationRequest): Promise<{ collaborationId: string; batchId: string; status: CollabStatus }>;
  // reply / confirm / cancel / retry / apply / undo 同 §3 字段，snake_case 仅存在于传输层
};
```

## 7. 内置资产清单（`WRITING_BUILTIN_VERSION = "v1"`）

| 资产 | logical_id |
| --- | --- |
| Team | `writing-team`（产品名「创作团队」） |
| Agent | `writing-editor` / `writing-researcher` / `writing-architect` / `writing-author` / `writing-reviewer`（`type="system"`、`category_id="writing"`） |
| Skill | `article-brief` / `article-research` / `article-outline` / `article-draft` / `article-rewrite` / `article-expand` / `article-review` |
| MCP | 首批不提供假连接；若提供则同受保护（`asset_kind="mcp"`） |

Skill→角色：`writing-editor` ← `article-brief, article-review`；`writing-researcher` ← `article-research`；`writing-architect` ← `article-outline`；`writing-author` ← `article-draft, article-rewrite, article-expand`；`writing-reviewer` ← `article-review`。

管理投影新增字段（四类资源统一）：`ownerModule="writing"`、`origin="builtin"`、`builtinVersion="v1"`、`deletable=false`、`protectionReason`。

**allocator 协议（WU-03 实现后于 2026-10-17 冻结，WU-06/WU-10 必须遵循）：** `BuiltinAssetRegistry.prepare(kind, logical_id, allocator=...)` 的 `allocator(candidate) -> str` 返回**空串**表示「候选物理 ID 已被用户对象占用，拒绝占用」；返回**非空字符串**表示以该 ID 创建/复用内置记录并返回实际物理 ID。registry 收到空串时按 `"<logical_id>-builtin"`、`"<logical_id>-builtin-2"`… 追加后缀重试（上限 8 次），**始终保留用户对象原样**。WU-06/WU-10 实现 allocator 时必须按此协议，不得改为「返回非空即成功」的读法（那会与「物理 ID 恰等于逻辑 ID」的正常路径冲突）。

**schema 归属口径（WU-03/WU-04 边界）：** `writing_builtin_assets` 属于 `nanobot/writing/database.py::_SCHEMA_STATEMENTS`（WU-03 拥有）；协作四张表属于 `nanobot/writing/collab/database.py::COLLAB_SCHEMA_STATEMENTS`（WU-04 拥有）。因此 `ensure_schema()` 之后 `writing_builtin_assets` **必然存在**，任何断言「整库不含该表」的测试都是错误的。

## 8. 前端 i18n 键（冻结，10 个 locale 必须齐备）

前缀 `creative.partner.`：`title`、`builtinBadge`、`process.toggle`、`input.placeholder`、`requirements.toggle`；
`creative.partner.stage.{idle,queued,running,waiting_author,completed,failed,cancelled,interrupted}`；
`creative.partner.action.{start_full,ideate,research,outline,draft,rewrite_selection,review_all,reply}`；
`creative.partner.artifact.{brief,material_list,outline,draft,rewrite,review}`；
`creative.partner.artifactAction.{useAngle,addMaterial,writeFromOutline,insertDraft,applyRewrite,viewIssues}`；
`creative.partner.handoff.{outline_confirm,materials_request,final_accept}`；
`creative.partner.progress.{researching,outlining,drafting,reviewing,waitingAuthor,stopped}`；
`creative.partner.error.{selectionRequired,versionConflict,notPending,serviceUnavailable,requestTooLarge}`；
`creative.partner.next.{writeFromOutline,insertToArticle,viewChanges,addMaterials}`。

同时修复既有结构缺口：`settings.teams.rosterCount_one` / `rosterCount_other` 在 zh-CN 与 zh-TW 缺失（英文 `en/common.json:277-278` 已有）。

**实施期扩键（2026-10-17 Leader 裁决，冻结）：** 侧栏面板（WU-12）实际使用了 `creative.partner.ui.*` 子命名空间共 37 个键，经裁决**接受并纳入契约**（面板以 `t(key, { defaultValue })` 兜底，但 10 个 locale 必须齐备真实翻译）：

`after, angles, before, busy, copyRewrite, dismissMaterial, empty, errorUnknown, failed, handoffUnknown, inputLabel, insertIntoArticle, insertSection, issues, materials, nextStep, nextStepUnknown, noProgress, outlineConfirm, outlinePoint, pending, plan, quickTasks, rejectRewrite, replaceArticle, retry, retryRewrite, sections, send, showFewerIssues, showMoreIssues, stop, stopping, untitled, viewAndInsert, viewMaterials, viewOutline`

（连同 §8 上文 51 个键，`creative.partner.*` 冻结总量为 88 个键；WU-12 不得再扩展新键，需要新文案时先改本节。）

## 9. 批次与限额（固定默认，写入 `writing/collab/recipe.py`）

最多并发 2 个成员、每批最多 8 个成员任务、最多 2 轮自动派工、单成员 5 分钟、单次自动批次 15 分钟；受既有全局配额共同约束；作者等待时间不计入模型批次超时。

## 10. 实施期裁决与已知缺口（2026-10-17，Leader 记录）

| # | 事项 | 裁决 / 现状 | 影响面 |
| --- | --- | --- | --- |
| 1 | 协作表 DDL 落在哪 | 主库 `_SCHEMA_STATEMENTS` 只放 `writing_builtin_assets`（WU-03）；协作六表由 `collab/database.py::COLLAB_SCHEMA_STATEMENTS` + `ensure_collab_schema` 自管，`CollabRepository.__init__` 会幂等调用 `db.ensure_schema()` + `ensure_collab_schema(db)` | 任何入口拿到 repository 即可用；两边均 `IF NOT EXISTS`，行为等价 |
| 2 | `allocator` 协议 | 空串=候选被用户对象占用 → registry 追加 `-builtin` 后缀重试，永不覆盖用户对象 | WU-06/WU-10 必须遵循 |
| 3 | handoff CAS 口径 | 整数 `artifact_version`（不用 `status`）；`writing.confirm` 的 `expected_handoff_version` 直接透传整数 | WU-09 请求体校验 |
| 4 | 幂等 `op` 键 | 实现采用 `writing.<action>:<载荷指纹>`（比本节 §2 的「op=动作名」更严，可挡同 request_id 异载荷） | WU-09 读接口无需感知 |
| 5 | `apply_batch` 语义 | 多补丁单事务只写一条撤销条目（`suggestion_id` = 首个），undo 以 `applied_revision == result_revision` 归并同批建议 | 单条 `writing.apply` 不受影响 |
| 6 | **已知缺口：提纲建议撤销不还原 outline** | `writing_collab_undo_entries` 只有 `before_body` 列，契约 §5.4 只要求还原 body → 提纲类建议撤销后提纲保持采纳后的状态 | 需在尾盘（T15）与 spec §10 验收记录中显式登记；若要求还原须扩表（本批不做） |
| 7 | `OffsetError` 映射 | 已冻结为 400 `suggestion target is invalid: <code>` | WU-09 错误翻译 |
| 8 | 协作级 `cancel_generation` 持久化 | **已修（Leader 收口）**：`finish_batch(cancel_generation=N)` 现在同时把协作行写成 `MAX(cancel_generation, N)`，`writing.cancel` 的响应因此真实（此前恒为 0）。取 max 是为了容忍进程内计数器重启后从头计数：代数只增不减，晚到旧 run 仍拦得住 | 残留：进程内代数计数器重启后从 0 起算（列只增） |
| 9 | **已知缺口：`batch.team_run_id` 与落库 `frozen.batch_id` 为空** | 仓储无 `set_batch_run`，运行绑定只活进程内；`create_batch` 生成 id 在冻结之后，故落库 `frozen_json.batch_id=""`，运行期交给 Team 的那一份已补齐真实 id | `writing.collab.get` 展示与审计口径 |
| 10 | **已知缺口：`collaboration.stage` 不推进** | 契约与 plan 未冻结 action→stage 映射，WU-08 按「宁缺勿造」处理（`stage=None` 不改） | 侧栏阶段展示须先在本契约冻结映射表 |
| 11 | **已知缺口：面板 `chatId` 回退值** | WU-12 用 `${articleId}:partner` 作本地回退；真实服务端绑定在 WU-13 集成时替换（`chat_id` 需满足 `^[A-Za-z0-9_:-]{1,64}$` 且经 `caller_chats` 归属校验） | 传输层归属校验（安全审查 F3） |
| 12 | **已知缺口：artifact `content` 的 JSON 形状未冻结** | 读接口按宽松解析返回；需要真实模型产出后按实际 payload 回填形状 | 前端渲染与后续审计 |
| 13 | **已知缺口：低置信度翻译** | ja/ko/vi/id 与 zh-TW（「調研」）部分文案为低置信度直译，已按 §8 齐备；需母语复核 | 文案质量 |
| 14 | **已知缺口：真实模型端到端（plan W15）可能不可用** | 若环境无可用模型配额，按「记录缺口」处理，不计入本批验收阻塞 | 验收口径 |
| 15 | **已知缺口：`confirm` 非原子** | 先 `save_snapshot`（独立事务）后 `resolve_handoff`；两者之间崩溃会留下「文章已改、交接仍 pending」 | 后续硬化：交接 CAS 前置或合并事务 |
| 16 | **已知缺口：采纳未校验 snapshot 摘要** | `apply_batch` 落库 `snapshot_digest`，但采纳时只用 `base_revision` 比较，未校验 `suggestion.snapshot_digest` 与当前文章摘要 | 契约未冻结该比较语义 |
| 17 | **已知缺口：`Handoff` docstring 与 CAS 口径矛盾** | `collab/models.py:401` 仍写「`status` 自身充当交接版本」；实际冻结口径是整数 `artifact_version`（本表 #3） | 文档订正 |
| 18 | 生产装配状态 | **已完成（Leader 收口）**：`gateway_services.build_writing_collab_runner` 装配 `TeamWritingPort` + `EnvelopeArtifactSink` + `CollabRunner`，并把写事件投影成契约 §5 三类帧；`build_gateway_services` 在有团队执行栈时注入 runner。没有执行栈的入口（CLI/无 host 的测试）保持 503 —— 不假装能起跑 | 有装配回归用例锁定（`test_collab_runner_assembly_binds_ports_and_event_sink`） |
| 19 | **已知缺口：`writing.confirm` 无可用重试路径**（WU-17 实测） | `runner.confirm` 用固定 request_id `confirm-outline:<handoff_id>:<expected>`，而 `ArticleRepository.save_snapshot` 的 op 指纹把整份快照（含 `revision`/`updated_at`）算进去 → 首次成功后 revision 前进，同载荷重试得 `article_conflict current_revision=2`，用新版本重试得 A 契约的 `request_id was already used for a different article mutation`（非 §4 collab 文案） | 首次确认路径正常；重试（网络抖动后重发）失败。修法：WU-19 把 `writing.confirm` 改走 `runner.confirm` 并让 runner 侧可重放（命中已确认交接即返回同一结果、不改写 `resolved_at`） |
| 20 | **已知缺口：`resolve_handoff` 的 CAS 不校验 `status`**（WU-17 实测） | CAS 只看整数 `artifact_version`，不看 `status` → 直接调用该仓储方法的路径（WU-09 在途层 `confirm_handoff`）重复 confirm 会再次 200 并改写 `resolved_at`；且该层丢弃 `client_request_id`（`:585/:617/:645` 只校验不落库） | 与 #19 同一修复片：confirm 统一经 runner；若保留直连路径，至少要拒绝非 `pending` 交接 |
| 21 | **已知缺口：`ArticleRepository._record_request` 仍是裸 `INSERT OR REPLACE`** | 与 M-1 同类（写路径无主键守卫）；WU-17 只修了协作域（`collab/repository.py` + `apply.py`） | 文章域写动作同 request_id 异载荷会被静默覆盖而非 409 |
| 22 | **裁决（纠正审查建议）：收口桥接不得放进 `to_thread`** | 代码审查 M-4 建议「端口侧包 `asyncio.to_thread`」。实测该建议**不可行**：冻结的收口路径在提纲交付时走到 `CollabRunner._release_handle`，那里用 `asyncio.create_task` 停团队运行 —— 在无运行中事件循环的线程上直接 `RuntimeError`。最终口径：**同步短事务 + 每条事件之间 `await asyncio.sleep(0)` 让出循环**，并用用例锁定「在 loop 线程上按序交付且期间 ticker 有推进」 | 改这条口径前必须先能证明收口路径不再需要 loop |
| 23 | **裁决（纠正计划措辞）：`writing.retry` 生成新一代 attempt 行** | 计划 Task 8 的 docstring 写「以同一 `batch_id` 发起重试（attempt 由仓储递增）」，实现是**新建批次行**（新 `batch_id`，`attempt = MAX(attempt)+1`，复用原批次的 action/基线/冻结上下文）。契约 §3 只冻结响应字段、未冻结是否复用行号，故以实现为准并在此登记 | 前端不得假设 retry 返回原 `batch_id` |
| 24 | **已知缺口：`writing.confirm` 重试路径** | 仍按 #19/#20 处理（未执行 WU-19）：真重放语义在 `runner.confirm` 内部键上不成立；当前 `confirm` 走传输层直连 `resolve_handoff`（有 handoff↔collaboration 绑定校验 + 整数 CAS），**重复 confirm 会再次 200 并改写 `resolved_at`** | 首次确认路径正常；网络抖动后的重发不保证幂等 |

## Next

- 接口变更 → 先改本文件，再同步 plan §2 与 `*-dispatch.md`。
- WU 不得自行扩展动作名、错误 message、事件名或 i18n 键前缀。
