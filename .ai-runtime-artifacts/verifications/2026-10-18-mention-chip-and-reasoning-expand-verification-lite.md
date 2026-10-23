---
artifact: verification-lite
route: Tier 1 Leader 直做（样式改造 + 交互增强）
skills:
  - source-driven-development
  - test-driven-development
  - verification-before-completion
source:
  - 用户截图：输入框 `⊘ @commander` 图标与 @ 重复（"丑有点"）、要求 mention 有背景色（"可以有个背景色那样"）
  - 用户诉求：思考内容在执行过程中看不到具体内容
  - AskUserQuestion 确认：chip 两处都要 / 思考行自身可展开看全文 / 范围只覆盖主会话执行过程 / 标识=正文 @ 提及带身份图标
supersedes:
  - .ai-runtime-artifacts/verifications/2026-10-18-composer-agent-mention-caret-verification-lite.md
created_at: 2026-10-18
tier: 1
---

# Mention 胶囊 + 思考内容可展开 — 轻量验证（Tier 1）

> 本产物**推翻**同日 `2026-10-18-composer-agent-mention-caret-verification-lite.md` 的 composer 渲染结论：
> `@` 恢复可见、图标不再压在 `@` 槽位上。光标对齐的根因分析与量测方法仍然有效。

## 决策

| 议题 | 结论 |
| --- | --- |
| mention chip 落点 | 消息气泡 + 输入框都要；输入框用「padding + 等值负 margin」保净宽度，不放身份图标 |
| Agent/Leader 标识 | 指**正文 `@` 提及**带图标（不是消息列表的发送方身份行，后者本轮不做） |
| 思考内容 | `ReasoningRow` 自身可展开看全文；流式中默认展开、结束后自动收起（用户手动展开则保留） |
| 范围 | 只覆盖主会话执行过程（`AgentActivityCluster` + `MessageBubble` 共用 `ReasoningRow`）；子 Agent / 团队 Leader 本轮不动 |

## 改动

### Mention 胶囊（`webui/src/components/CliAppMentionText.tsx`）

- 新增共享 `MentionToken`：`@` 一律用 `text-muted-foreground/70` 可见，名字用身份色。
- message 变体 → 中性浅底胶囊（`rounded-full bg-foreground/[0.055]`）+ 行内身份图标（Agent 用 `AgentGlyph`，CLI/MCP 用 logo，session 无图标）。
- composer 变体 → 同款底色但 `px-[0.32em]` 配 `-mx-[0.32em]`，净行内宽度为 0，原生光标不漂；**不渲染图标**（图标 + `@` 语义重复且观感差）。
- 删除 `isHero` 形参链路（`CapabilityMentionToken` → `ComposerCliMentionOverlay` → 调用点），因为 composer 不再需要按 hero 调图标尺寸。

### 正文提及接入 Agent（prop 透传）

`UserMessageText` → `MessageBubble` → `ThreadMessages`（含 `ThreadDisplayUnitProps` 与 memo 比较器）→ `ThreadViewport` → `ThreadShell`（复用已有 `composerAgents`）。
此前消息正文里的 `@commander` 完全不渲染成 mention（`MessageBubble` 没有 `agents` 入参）。

### 思考内容展开

- `ActivityStep`：新增 `detailExpanded` / `onDetailExpandedChange`（受控，不传则维持原内部 state）与 `detailFollowBottom`（流式时把详情框钉在最新内容）。
- `ReasoningRow`：把完整思考正文作为 `detail` 传入；`streaming` 期间默认展开，结束后收起；用户点过之后不再自动改状态。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `vitest run src/tests/agent-activity-cluster.test.tsx -t "full reasoning text"`（改前） | 1 failed（找不到 `activity-step-details-toggle`） |
| `vitest run src/tests/thread-composer.test.tsx` | 96 passed |
| `vitest run src/tests/agent-activity-cluster.test.tsx src/tests/message-bubble.test.tsx` | 95 passed |
| `vitest run`（webui 全量） | 1781 passed, 2 skipped, 0 failed |
| `tsc -p tsconfig.build.json --noEmit` | TSC OK |
| `eslint --config webui/eslint.config.js webui/src --max-warnings 0` | exit 0 |

新增/调整用例：

- `AgentActivityCluster > expands the full reasoning text while thinking and collapses it once done`（新）
- `MessageBubble > renders agent mentions as identity chips in sent user messages`（新）
- `ThreadComposer > keeps the composer agent mention width-identical to the textarea text`（新）
- `MessageBubble > renders streaming reasoning as one compact line that already exposes its full text`（改：label 与 detail 文本重复，查询作用域收到 `activity-line`）
- `MessageBubble` 的 CLI 用例（改：`not.toContain("rounded"/"px-")` → 断言 `rounded-full` + `px-[0.32em]` 与 `-mx-[0.32em]` 成对）
- `ThreadComposer` 的 CLI / agent 用例（改：composer 不再渲染图标；`@code-helper` 恢复断言完整 `@code-helper`）

## 浏览器实测（vite dev :5175 → gateway :8765，首页 hero composer）

量测法同前：与 textarea 同字体的镜像 span 复算光标 x。

| 指标 | 数值 |
| --- | --- |
| textarea 纯文本 `@commander` 宽 | 101.0px |
| 覆盖层 token **净推进**（盒宽 111.3 − 两侧 padding 10.24） | **101.0px** |
| 光标 vs 可见文字末端（`@commander`） | 0.0px |
| 光标 vs 可见文字末端（`@commander `，选完候选词的真实态） | +4.2px（正好一个空格） |
| 胶囊高度 vs 行盒 | 22.2px vs 24px（不顶行） |
| 胶囊左缘距容器左缘 | 14.9px（在 `px-5` 内边距内，未被 `overflow-hidden` 裁掉） |
| `@` 计算色 | `rgba(102,117,107,0.7)`（可见，非 transparent） |
| 名字计算色 | `rgb(217,119,6)` = 档案色 `#D97706` |
| composer 内 `composer-agent-mention-glyph-*` | 不存在（图标只在消息胶囊里） |

消息胶囊的几何用同 class 串 + 真实 lucide compass 路径的探针量测：胶囊 22.3×114.5px、`border-radius: 9999px`、底色 `rgba(37,50,44,0.055)`；含胶囊的行 46.7px vs 纯文本行 48px → **没有顶开行高**。

截图：`/tmp/zhixu-composer-pill.png`（输入框胶囊 + 光标贴末位）、`/tmp/zhixu-chip-probe.png`（消息胶囊类级预览，含 commander / ui-designer / zoom 三种）。

## 未验证项

- **真实流式 E2E 未做**：验证思考展开需要发一条真实消息，而当前 composer 处于「完全访问权限」，agent 回合可能真的执行工具并写入工作区会话 —— 未擅自触发，等用户明确同意或自行在 UI 里跑一轮。
- 消息胶囊的**实机渲染**未截图（探针用的是同一份 class 常量，但不是真实消息节点）；且本轮 `agents` 透传后，历史会话里的 `@` 提及需重新打开会话才能看到胶囊。
- 非 hero（会话页）composer 未单独量测：字号同为 16px，`leading-5`（20px）下胶囊高 22.2px 会略微超出行盒 —— 若观感有问题再收 `py`。
- 子 Agent 执行面板与团队 Leader 回合的思考展示按约定本轮不动。

## Next

- 用户同意后补一次真实流式验证（建议先把访问模式切出「完全访问」）。
- 若要消息列表也显示发送方身份（参考图 2 的「👑 指挥官」行），需后端在出站消息带 `agent_id` 并透到前端，另起一轮。
