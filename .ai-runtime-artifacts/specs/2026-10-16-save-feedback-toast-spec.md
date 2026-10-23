---
artifact: spec
route: brainstorming + source-driven-development
skills: [brainstorming, source-driven-development]
skills_evidence:
  - harness-kit/.agents/skills/brainstorming/SKILL.md
  - harness-kit/.agents/skills/source-driven-development/SKILL.md
source:
  - 用户反馈「很多保存按钮点了之后没有任何提示/通知，体验差」
  - 用户确认范围为方案 B（系统化：统一 Toaster + 收敛既有分散提示）
  - 用户确认三项实现口径：显式调用 + 共享 helper / 只接管「刚保存成功」/ CustomEvent + 根节点 Toaster
  - AGENTS.md
  - harness-kit/core/routing.md
  - harness-kit/project.profile.md
  - .ai-runtime-artifacts/stack/2026-10-16-save-feedback-toast-stack.md
  - webui/src/components/settings/shared/SettingsControls.tsx
  - webui/src/components/settings/capabilities/useCapabilitySettingsActions.ts
  - webui/src/components/settings/models/useModelSettingsActions.ts
  - webui/src/components/settings/useSettingsController.ts
  - webui/src/components/workspace/IdentityPage.tsx
  - webui/src/components/settings/teams/TeamsView.tsx
  - webui/src/components/settings/agents/AgentsView.tsx
  - webui/src/components/settings/agents/AgentEditorDialog.tsx
  - webui/src/App.tsx
  - webui/src/lib/skill-events.ts
  - webui/src/tests/i18n.test.tsx
  - webui/src/tests/identity-page-editor.test.tsx
created_at: 2026-10-16
status: draft
approved: false
---

# 保存反馈：统一通知通道（Toaster）

## 目标与范围

用户反馈：项目里不少「保存」按钮点下去没有任何提示，无法确认是否成功。

**目标**：为「保存成功」提供统一、可见、瞬时的通知；消除所有「点了没反应」的静默缺口；把当前分散的三套浮动提示收敛到同一条通道。

**范围内**

1. 新增零依赖的全局 Toaster（CustomEvent 通道 + 挂载在 App 根的组件）。
2. 用一个共享 helper `notifySaved()` 覆盖全部 settings 保存动作（能力区 4 个、模型/供应商 8 个、记忆总开关）。
3. 收敛既有分散实现：`App.tsx` 的 `restartToast`、`IdentityPage` 的固定浮层、`TeamsView` 与 `AgentsView`/`AgentEditorDialog` 的行内「已保存」。

**范围外（明确不做）**

- 不改后端协议、不动任何 mutation 端点与 `applyPayload` 的调用时机。
- 不收敛行内「未保存改动 / 待重启」状态文案（`SettingsStatusMessage`），语义与「刚保存成功」不同。
- 不动 `creative/ArticleEditor.tsx:442` 的 `save-phase-label`：自动保存会按 1.5s 防抖频繁触发，弹 toast 是噪音。
- 不动 C 类对话框（`AddProjectDialog` / `RenameChatDialog` / `DeleteConfirm`）：成功后关框本身即反馈。
- 不处理 `settings/identity/IdentityView.tsx` 死代码（`SettingsPage.tsx` 无 `identity` 路由，生产不可达，仅被测试引用）——单独开单。
- 不新增任何第三方依赖（评估过 sonner / react-hot-toast，均不引入）。

## 当前问题与依据

### 根因

全仓没有任何统一的通知设施：`webui/package.json` 无 toast 依赖，`webui/src/i18n/locales/**` 无相关 key，唯一的浮动提示是 `App.tsx:1052 / 2193-2194 / 3077-3089` 的一次性 `restartToast`。于是「保存成功」被表达成四套互不相同的形态，且多数场景没有表达。

### A 类：成功时零反馈

| 位置 | 机制 |
| --- | --- |
| `settings/shared/SettingsControls.tsx:271-277` | `RestartSettingsFooter` 的 `statusMessage` 在 `!pendingRestart && !dirty` 分支为 `undefined`，`:334` 处 `SettingsStatusMessage` 直接 `return null` → 无提示 |
| `capabilities/WebSettings.tsx:268`、`TranscriptionSettings.tsx:163`、`ImageGenerationSettings.tsx:193` | 复用上述 footer，若服务端未回 `restart_required_sections` 即静默 |
| `capabilities/SecuritySettings.tsx:112-119` | 复用 footer 且未传 `message` / `dirtyMessage` / `pendingMessage`，静默更彻底 |
| `models/useModelSettingsActions.ts:373-437`（`saveProvider`） | 仅 `payload.requires_restart` 为真才在 `:416` 置 pending，否则成功无提示 |
| `models/useModelSettingsActions.ts:151-259`（`saveModelSettings`） | 预设新建/更新成功后只有按钮变灰，无提示 |
| `settings/memory/MemorySection.tsx:33-48` | 总开关即时保存，只有失败 `setError`，成功静默 |

### B 类：同一件事四种表达

| 位置 | 形态 | 时长 |
| --- | --- | --- |
| `App.tsx:3077-3089` | 顶部居中浮动胶囊 | 3.5s（`App.tsx:2194`） |
| `workspace/IdentityPage.tsx:1022-1043` | 顶部居中浮动胶囊（另一份实现，带图标） | 2.6s（`IdentityPage.tsx:675`，只复位按钮态，浮层跟 `saveState` 走） |
| `teams/TeamsView.tsx:99-102 / 191-195`、`agents/AgentsView.tsx:58-61 / 168-170` + `AgentEditorDialog.tsx:290-295` | 行内绿色小字 | 3s |
| `creative/ArticleEditor.tsx:442` | 常驻行内文字 | 无（范围外） |

## 方案对比

| 方案 | 做法 | 取舍 |
| --- | --- | --- |
| A 最小面 | 只给 footer 加「已保存」态，堵 A 类缺口 | 改动最小，但 B 类四套表达继续并存，问题只解决一半 |
| **B 系统化（采纳）** | 新增全局 Toaster + 共享 helper，A 类全改走它，B 类三套浮动/行内提示收敛 | 一次到位，之后任何保存一行接入；改动面约 15 个文件 + i18n |
| C 仅统一样式 | 把三套浮层合并成同一组件，不新增反馈覆盖 | 改动小、收益小 |

**用户已确认采纳 B。** 三项实现口径：

1. **触发方式**：显式调用 + 共享 helper（不用 `applyPayload` 做集中钩子——`applyPayload` 共约 20 个调用点，含初次加载 `useSettingsController.ts:187/198`、refresh `:337/367`、OAuth 回调 `useModelSettingsEffects.ts:60`，集中钩子必须额外做白名单，容易误报）。
2. **分工**：只接管「刚保存成功」。footer 已显示 `settings.status.savedRestartApply`（见 `SettingsControls.tsx:274`）时不再弹 toast 去重；失败则 toast + 保留行内错误（失败信息需留痕）；「未保存改动」行内状态保留。
3. **形态**：CustomEvent 通道 + 挂在 App 根的 `<Toaster/>`，与 `lib/skill-events.ts:13-18` 既有约定一致，非 React 模块也能发通知，不改组件树。

## 设计

### 组件与数据流

```
save* action 成功/失败
        │
        ▼
lib/save-feedback.ts        ← notifySaved() / notifySaveFailed()
        │  （判定是否需重启 → 决定弹不弹）
        ▼
lib/toast-events.ts         ← notify(input): CustomEvent "zhixu:toast"
        │  window.dispatchEvent
        ▼
components/ui/toaster.tsx   ← <Toaster/> 挂 App 根，监听 → 队列 → 定时移除 → 渲染
```

### 1. `webui/src/lib/toast-events.ts`（新增）

沿用 `lib/skill-events.ts` 的形状与守卫：

```ts
export const TOAST_EVENT = "zhixu:toast";
export type ToastTone = "success" | "error";
export interface ToastPayload { id: string; message: string; tone: ToastTone; durationMs: number }
export interface ToastInput { message: string; tone?: ToastTone; durationMs?: number }

const DEFAULT_DURATION_MS: Record<ToastTone, number> = { success: 2600, error: 4200 };

export function notify(input: ToastInput): void        // 生成 id → dispatch CustomEvent<ToastPayload>
export function isToastPayload(value: unknown): value is ToastPayload
```

- `id` 用 `crypto.randomUUID()`；`main.tsx` 已为非安全上下文 shim 过该 API，无需再写降级。
- 函数入口保留 `typeof window === "undefined"` 守卫（测试/SSR 安全）。

### 2. `webui/src/components/ui/toaster.tsx`（新增）

```tsx
export function Toaster(): JSX.Element
```

- `useEffect` 订阅 `TOAST_EVENT`，写入本地队列；每条到期自动移除；同时最多 3 条，超出丢最旧。
- **视觉直接复刻既成品**：外层用 `App.tsx:3077-3089` 的定位/宽度（`fixed left-1/2 top-[calc(0.75rem+env(safe-area-inset-top))] -translate-x-1/2 z-50`）+ `floatingSurfaceElevationClassName` 圆角胶囊；成功态复用 `IdentityPage.tsx:1029-1039` 的图标胶囊。不新造视觉语言。
- **无障碍**：容器 `role="status"` + `aria-live="polite"` + `aria-atomic="true"`。`role="status"` 的 `polite` / `atomic` 是隐式值，显式写出只为可读性与测试断言稳定。
  - 依据：MDN，`role="status"`「defines a live region containing advisory information for the user that is not important enough to be an alert」，「have an implicit `aria-live` value of `polite` and an implicit `aria-atomic` value of `true`」，且「Do not give focus to the status when its content updates」。
  - 来源：https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Roles/status_role
- 容器整体 `pointer-events-none`，不拦截点击（与 `IdentityPage.tsx:1026` 一致）。

### 3. `webui/src/lib/save-feedback.ts`（新增）

非组件层，直接取 `i18next.t`（与 `lib/format.ts` 的 `currentLocale()` 同思路），避免把 hook 传进 action：

```ts
export function notifySaved(options?: { restartRequired?: boolean }): void
export function notifySaveFailed(message: string): void
```

判定规则：

| 条件 | 行为 |
| --- | --- |
| `restartRequired === true` | **不弹**（footer 行内已显示「已保存，重启后生效」，去重） |
| 其余成功 | `notify({ message: t("toast.saved"), tone: "success" })` |
| 失败 | `notify({ message: t("toast.saveFailed", { message }), tone: "error" })` |

**失败只从「已发起保存请求」后的 `catch` 触发**：纯前置校验（`useModelSettingsActions.ts:382` 的 `apiKeyRequired`、`:176-179` 的 preset 重名冲突）不弹 toast，仍走行内提示，避免校验噪音。

### 4. 触发点改造

| 文件 | 行 | 改动 |
| --- | --- | --- |
| `capabilities/useCapabilitySettingsActions.ts` | 76 / 94 / 112 / 165（`applyPayload` 之后） | `notifySaved({ restartRequired: payload.requires_restart })` |
| 同上 | 83 / 101 / 119 / 182（`catch`） | `notifySaveFailed((err as Error).message)` |
| `models/useModelSettingsActions.ts` | 194 / 203 / 254 / 341 / 363 / 414 / 457 / 510 / 536 | `notifySaved(...)`；`:414`（`saveProvider` 成功）用 `payload.requires_restart` |
| 同上 | 各 `catch` 分支 | `notifySaveFailed(...)`（排除前置校验分支） |
| `settings/memory/MemorySection.tsx` | 成功分支 | `notifySaved()` |
| `teams/TeamsView.tsx` | 99-102（`flashSaved`）、191-195（行内节点） | 删本地 `savedNotice` 状态与定时器，成功处改 `notifySaved()` |
| `agents/AgentsView.tsx` | 58-61 / 168-170 | 同上 |
| `agents/AgentEditorDialog.tsx` | 290-295 | 删 `savedNotice` 行内分支，保留「有未保存的修改」 |
| `workspace/IdentityPage.tsx` | 674-675、1022-1043 | 删本地固定浮层；按钮 2.6s 态保留；改 `notifySaved()` |
| `App.tsx` | 1052、2193-2194、3077-3089 | 删 `restartToast` state 与旧 DOM，改 `notify({ message: t("app.restart.completed", …), tone: "success", durationMs: 3500 })` |

**IdentityPage 文案保持原文**：`tests/identity-page-editor.test.tsx:100/104` 断言 `"Identity saved — live now"`，因此该处继续使用 `zh ? "身份设定已保存，已立即生效" : "Identity saved — live now"` 作为 message，只是渲染容器换成共享 Toaster。

### 5. i18n

在 `common.json` 新增 `toast` 命名空间，**10 个 locale 全部补齐**（en / es / fr / id / ja / ko / pt-BR / vi / zh-CN / zh-TW）：

| key | en | zh-CN |
| --- | --- | --- |
| `toast.saved` | `Saved` | `已保存` |
| `toast.saveFailed` | `Save failed: {{message}}` | `保存失败：{{message}}` |

**强制约束**：`tests/i18n.test.tsx` 的 `keeps every locale aligned with the English resource shape` 要求其余 9 个 locale 相对 en **missing 为空、extra 为空、插值变量一致**。漏一个 locale 即测试失败。

> 注：`tests/i18n.test.tsx` 当前已有一条既有失败（zh-CN 缺 `settings.teams.rosterCount_one/_other`），与本改动无关，不追。

### 测试与验证

新增 `webui/src/tests/toast.test.tsx`：

- `notify()` → `<Toaster/>` 渲染出消息；用 fake timers 断言到期后自动消失。
- 同时最多 3 条，第 4 条挤掉最旧。
- 容器具备 `role="status"` / `aria-live="polite"` / `aria-atomic="true"`。
- `notifySaved({ restartRequired: true })` **不产生** toast；`restartRequired: false` 产生。

回归面（须全绿）：

- `identity-page-editor.test.tsx`（`:100/104` 的 `Identity saved — live now`）
- `app-layout.test.tsx`（`:2765/2780/2831/2884/2919` 等 Save 相关断言、`Unsaved changes.`、`Save directory`）
- `agents-view-integration.test.tsx`（`:76-79` 改完提示词保存必须打到后端 `save`）
- `i18n.test.tsx`（locale shape parity）
- `article-editor-ime` / `creative-*`（确认未触碰 ArticleEditor）

验证命令：`cd webui && npx vitest run`（或 `bun run test`）；构建按 `project.profile.md` 的交付口径补 `bun run build`。视觉按项目既有办法用 Electron 桌面壳 + CDP 目视（见 `memory/zhixu-desktop-ui-verify.md`）。

## 风险与回滚

| 风险 | 缓解 |
| --- | --- |
| 实施前 `app-layout.test.tsx` 等已有失败，混淆新旧问题 | 第一步先跑一次基线，记录既有失败清单（参照 `memory/zhixu-desktop-ui-verify.md`） |
| i18n 漏补 locale 导致 shape parity 失败 | key 新增清单固定为 2 个，逐个 locale 补齐后再跑 `i18n.test.tsx` |
| 新旧浮层并存导致重复提示 | `App.tsx:3077-3089` 旧 DOM 与 IdentityPage 浮层必须同批删除，不留兼容分支 |
| toast 抢焦点或干扰输入 | `role="status"` 不抢焦点（MDN 明确），容器 `pointer-events-none` |
| 校验类错误被误弹成「保存失败」 | 只从「已发起请求」后的 `catch` 触发，前置校验排除 |

**回滚**：纯前端、无协议/数据变更，`git revert` 单次提交即可完全回退。

## 待确认项

1. 失败提示是否需要同时保留行内错误与 toast 两处（当前设计：是，失败信息需留痕）。若嫌重复可在实施时只留 toast。
2. toast 默认时长（成功 2600ms / 失败 4200ms）是否合适。
3. `settings/identity/IdentityView.tsx` 死代码的处理方式：删除，还是接回 SettingsPage 的 `identity` 路由。→ 单独开单，本次不动。

## Next

**（写入后须暂停，等用户明确继续 — 见 `harness-kit/core/routing.md` § 阶段门禁）**

- 确认方案无误 → 说「写计划」或「制定实施计划」
- 变更范围小、无需计划 → 说「直接实现」或「直接做」
- 需要调整方案 → 直接说修改意见
