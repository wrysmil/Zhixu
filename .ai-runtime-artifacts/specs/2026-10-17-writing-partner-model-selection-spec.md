---
artifact: spec
route: superpowers:brainstorming
skills:
  - brainstorming
  - source-driven-development
skills_evidence:
  - ~/.agents/skills/brainstorming/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
source:
  - 用户原话：「然后我想的是这聊天侧栏，最好可以加上可以选择模型」（附创作搭档侧栏截图，输入框被红框标出）
  - 用户选择：语义 = A「侧栏切换这篇文章的会话模型」；通道 = A1「复用聊天已有的 /model 系统命令」
  - .ai-runtime-artifacts/stack/2026-10-17-writing-agent-workflow-stack.md（复用，本需求不引入新依赖）
  - .ai-runtime-artifacts/specs/2026-10-17-writing-team-sidebar-spec.md
  - .ai-runtime-artifacts/contracts/2026-10-17-contract-writing-team-collab.md
created_at: 2026-10-17
updated_at: 2026-10-17
document_version: 1
status: approved
approved: true
approved_utterance: 「直接实现」（2026-10-17 本会话用户原话，跳过 plan 阶段）
tier: 2
---

# 创作搭档侧栏：切换这篇文章的模型

## 1. 目标与非目标

**目标：** 作者在创作搭档侧栏能直接切换**这篇文章协作会话**所用的模型 preset，选完立即在侧栏看到当前生效的模型；之后新起的批次按该模型跑，并可追溯（批次 `frozen.model_id`）。

**非目标（本次明确不做）：**

- 不做「写作专属模型」字段：`writing.start` 的 payload **不新增**任何模型字段（契约 §3 冻结口径）。
- 不做逐成员（主编/研究员/写手/审校各自）在侧栏选模型；那是内置写作团队智能体档案的职责（`nanobot/agents/catalog.py` 的 `AgentProfile.model_id`，入口在智能体/团队设置页）。
- 不改数据库 schema、不改 `/api/writing/*` 路由三表。
- 不做运行中批次的模型热切换。

## 2. 现状与约束（取证）

| 事实 | 证据 |
| --- | --- |
| 客户端**不得**影响模型：`不接受 teamId / agentId / skillPath；团队、角色、Skill、模型一律服务端解析` | `.ai-runtime-artifacts/contracts/2026-10-17-contract-writing-team-collab.md:202` |
| v2 spec 口径：模型沿用当前可用的全局配置，不硬编码供应商或型号；失效的显式 preset 明确报错 | `.ai-runtime-artifacts/specs/2026-10-17-writing-team-sidebar-spec.md:101` |
| 会话级模型 preset 是既有机制：会话 metadata 的 `_nanobot_model_preset` | `nanobot/session/model_selection.py:9-23` |
| 聊天里切模型走系统命令 `/model <preset>`（写会话 + 广播） | `webui/src/components/thread/ThreadShell.tsx:999-1004`（`client.sendSystemCommand(chatId, "/model " + name)`）；`nanobot/command/builtin.py:423-466` |
| 服务端写入实现：`resolve_preset` → `sessions.get_or_create` → 落 metadata → `save` | `nanobot/agent/loop.py:904-916`（`set_session_model_preset`） |
| 按会话解析 runtime：`model_preset_from_metadata` → `resolve_preset`，preset 被删则回落默认 | `nanobot/agent/loop.py:865-891`（`runtime_for_session`） |
| **当前缺口**：写作批次的 runtime 取的是**全局** `runtime_resolver.runtime`，不是协作会话的 | `nanobot/teams/turn.py:238-239`（`current_runtime`）→ `nanobot/webui/gateway_services.py:249`（`runtime_provider=`）→ `nanobot/writing/collab/team_port.py:513`（`runtime=self._runtime_provider()`） |
| 冻结记录的是全局 `agent.model` | `nanobot/cli/gateway_runtime.py:763-764`（`_webui_runtime_model_name`）→ `nanobot/webui/gateway_services.py:266`（`model_id=`）→ `nanobot/writing/collab/preparation.py:159-163` |
| 协作会话键 = `websocket:<chat_id>`，`chat_id = <article_id>:partner` | `nanobot/writing/collab/runner.py:420-423`；`webui/src/components/creative/CreativeWorkspace.tsx:361-362` |
| 内置写作团队 5 个角色档案目前**都没钉** `model_id`（默认 `None`），所以成员跑的是宿主当前 runtime | `nanobot/agents/catalog.py:142/166/214/238`（`AgentProfile(...)` 无 `model_id`）；`nanobot/agents/models.py:115` |
| 可复用的模型选择器 UI | `webui/src/components/thread/ModelPresetBadge.tsx:85-121`（`ModelPresetOption` 与 `modelPresets/onPresetChange/onManageModels` props） |
| 侧栏头部有徽章位、且面板 docstring 只禁止「团队选择器」 | `webui/src/components/creative/WritingPartnerPanel.tsx:296-306`；文件头注释第 4 行 |

**结论：** 语义 A 完全可落在既有机制上——客户端只切**会话**（既有、已授权的能力），模型仍由服务端解析；需要补的是「批次按协作会话解析 runtime」这一处后端缺口，否则切换只是装饰。

## 3. 方案对比

| 方案 | 说明 | 取舍 |
| --- | --- | --- |
| **A1（选中）复用 `/model` 系统命令** | 侧栏发送既有系统命令；服务端 `set_session_model_preset` 落会话 metadata 并广播 `session_updated` | 零新契约、与聊天同一真相源、无新写路径；面板需订阅既有会话更新帧来对齐 |
| A2 新增 `writing.model.set` 写动作 | 仅认证 WS 白名单动作，带幂等键直接写会话 metadata | 语义更硬，但要新增第二套写路径 + 同步路由三表，且与 `/model` 形成双真相源 |
| B 写作专属模型字段 | `writing.start` 接受 `model_preset` 落 `frozen.model_id` | 直接违反契约 §3；同一协作中途换模型会出现两种 frozen 模型 |
| C 只读显示 + 跳设置 | 侧栏只显示、点击跳设置页 | 零风险但用户诉求（侧栏能直接选）未满足 |

## 4. 设计

### 4.1 数据流

```
侧栏 chip 选 preset
  → client.sendSystemCommand("<article_id>:partner", "/model <preset>")   # 既有通道
  → AgentLoop.set_session_model_preset(session_key="websocket:<article_id>:partner", name)
      校验 preset（不存在 → 明确文案）→ get_or_create 会话 → 写 _nanobot_model_preset → save
      → 广播 session_updated(scope="metadata")
  → 侧栏 chip 依会话当前 preset 刷新（读回值而非乐观值）
  → 下次起批次：TeamWritingPort.start_batch(session_key) 按会话解析 runtime（见 4.2）
      → frozen.model_id 记录同一份解析结果（model_preset + 实际 model）
  → 成员任务在该 runtime 上跑
```

### 4.2 后端改动（本次唯一的行为变更）

1. `nanobot/writing/collab/team_port.py`：`start_batch` 已经拿到 `session_key`（:473），把 `runtime=self._runtime_provider()`（:513）改为「按 `session_key` 解析」；`runtime_provider` 契约从 `Callable[[], Any]` 升级为 `Callable[[str], Any]`（保留零参兼容：调用方若未提供按会话解析能力，回落全局 runtime）。
2. `nanobot/teams/turn.py`：`TeamLeaderTurns.runtime_provider` 与 `runs.py::_drive`（:121-124，重试路径同样拿到 `session_key`）一起升级，保证**批次路径与重试路径同口径**。
3. `nanobot/webui/gateway_services.py`：接线时传入按会话解析器（`AgentLoop.runtime_for_session(AgentLoop.sessions.get_or_create(session_key))`），并让 `frozen_preparer(model_id=...)` 用它替换 `runtime_model_name`。
4. `nanobot/writing/collab/preparation.py` 的 `model_id` 语义不变（仍是「本批冻结的模型标识」），只是来源从全局变为会话解析结果；`ModelUnavailableError` 路径不变。

### 4.3 前端改动

- `WritingPartnerPanel.tsx`：头部标题行加模型 chip（`ModelPresetBadge`，`isHero={false}` 一行紧凑态），显示当前 preset 名与 model；`needsSetup` 时点击跳模型设置（复用 `onManageModels`）。
- `CreativeWorkspace.tsx`：把 `settings.model_presets` 映射为 `ModelPresetOption[]` 注入面板；`onPresetChange` → `client.sendSystemCommand(chatId, "/model <name>")`；当前值来自该协作会话的 `model_preset`（会话不存在时显示全局默认 preset，不假装已选）。
- i18n：新增键落在 `creative.partner.ui.model*`，沿用 `collab-labels.ts` 的 `defaultValue` 兜底约定（不在组件里硬编码中文）。

### 4.4 行为口径（需冻结）

| 场景 | 口径 |
| --- | --- |
| 切换时机 | 只影响**之后新建**的批次；运行中批次沿用其 `frozen.model_id`（面板要能说明这一点） |
| 会话尚不存在 | chip 显示全局默认 preset；首次切换会顺带创建该协作会话 |
| preset 不存在/已被删 | `/model` 回明确文案，chip 保留旧值；起批次时若会话 preset 失效 → 回落默认（`runtime_for_session` 既有行为）并在面板显示回落后的模型 |
| 角色档案显式钉了 `model_id` | 以**档案**为准（服务端真值），chip 仍显示会话解析结果，需在 spec/UI 文案里说明该优先级 |
| 权威来源 | `writing.start` payload 不含任何模型字段；`frozen.model_id` 是唯一可追溯记录 |

## 5. 接口面（无新契约）

| 接口 | 复用/变更 |
| --- | --- |
| 系统命令 `/model <preset>` | 复用（`nanobot/command/builtin.py:423`）；不改协议 |
| 帧 `session_updated(scope="metadata")` | 复用；面板按既有 `onChat(chatId)` 订阅对齐 |
| `settings` 载荷的 `model_presets` | 复用（`SettingsPayload["model_presets"]`） |
| `writing.start` / `writing.collab.get` / `frozen.model_id` | 字段不变，仅 `frozen.model_id` 的**取值来源**变化 |
| 新增端点/字段 | 无 |

> 因不新增端点与字段，本次**不新写** `.ai-runtime-artifacts/contracts/` 产物；上述复用清单即接口边界。

## 6. 测试与验收

| 层 | 用例 |
| --- | --- |
| Python 单测 | 批次 runtime 按 `session_key` 解析（含会话 preset 失效 → 回落默认）；`frozen.model_id` 取会话解析结果而非全局；重试路径同口径；零参 `runtime_provider` 的旧装配不回归 |
| Vitest | chip 渲染当前 preset/model；切换触发 `/model` 系统命令且参数正确；会话不存在时显示默认而非误报已选；`needsSetup` 跳设置 |
| 静态 | `ruff check nanobot/`（改动文件零新增）、`basedpyright nanobot/`（改动文件零新增）、`bun run lint`、`bun run build` |
| 真机冒烟 | 重启桌面端 → 在《Redis的原理》侧栏切换 preset → 跑「启动完整流程」→ 面板显示、`frozen.model_id`、Team run 实际模型三者一致；再切一次确认只影响新批次 |

## 7. WU 拆分建议（实施阶段）

| WU | 目标 | 文件 |
| --- | --- | --- |
| WU-1 | 后端按协作会话解析 runtime + 冻结取会话模型 | `nanobot/writing/collab/team_port.py`、`nanobot/teams/turn.py`、`nanobot/teams/runs.py`、`nanobot/webui/gateway_services.py` |
| WU-2 | 侧栏模型 chip + 会话对齐 + i18n | `webui/src/components/creative/WritingPartnerPanel.tsx`、`CreativeWorkspace.tsx`、`collab-labels.ts`、locale 文件 |
| WU-3 | 收尾：真机冒烟 + 集体测试/审查 | `.ai-runtime-artifacts/verifications/`、`reviews/` |

WU-1 与 WU-2 只经「会话 preset」这一个既有接口耦合，可并行；WU-3 依赖两者。

## 8. 风险与待确认项

1. **面板对齐依赖会话帧**：`session_updated` 广播是否覆盖 `/model` 这条路径的 chat_id（写作会话不是普通聊天 UI 的当前会话）——实施时需真机确认；若不覆盖，退化为「切换后下一次快照/重新打开时刷新」并记录。
2. **`runtime_provider` 契约升级**：`nanobot/teams/` 侧还有别的调用方（CLI/TUI/测试），需逐一确认零参兼容分支，避免跨端回归。
3. **`frozen.model_id` 格式**：保持与现状同为「模型名」字符串（不引入 `preset:model` 复合格式），面板另从 settings 反查 preset 名。
4. **档案优先级**：内置写作团队将来若钉 `model_id`，会话切换将被档案覆盖——需在设置页/侧栏文案里说明，否则用户会认为切换失效。

## 9. Spec 自检

- 占位符：无 TBD/TODO。
- 一致性：§1 非目标与 §4.4 行为口径一致（不改 payload、不做逐成员、不做热切换）。
- 范围：单一实现计划可覆盖（1 个后端 WU + 1 个前端 WU + 收尾）。
- 歧义：已明确「只影响之后的新批次」「档案优先于会话」「会话不存在显示全局默认」。

## Next

**（写入后须暂停，等用户明确继续 — 见 `harness-kit/core/routing.md` § 阶段门禁）**

- 确认方案无误 → 说「写计划」或「制定实施计划」
- 变更范围小、无需计划 → 说「直接实现」或「直接做」
- 需要调整方案 → 直接说修改意见
