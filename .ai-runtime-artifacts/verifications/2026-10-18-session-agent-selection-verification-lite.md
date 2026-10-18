---
artifact: verification-lite
route: 设计（brainstorming, bounded）→ 实现（Tier 1 Leader 直做，用户「直接执行」「这个还是要做」）
skills:
  - brainstorming
  - frontend-ui-engineering
  - verification-before-completion
skills_evidence:
  - /Users/mima0000/.qoder-cn/plugins/cache/qoder-marketplace/superpowers/6.3.0/skills/brainstorming/SKILL.md
  - harness-kit/.agents/skills/verification-before-completion/SKILL.md
source:
  - 用户原话：「我希望聊天时的select team ，有一个二级选择，可以选择Agent。然后再那个模型旁边，会显示当前的Agent。默认的就是那个 默认Leader Agent」「这个也改一下」
  - 用户对 4 个设计问题的选择：接管本会话主回复 / 随时可改 / 团队内 Agent（未选团队列全部）/ 可点击胶囊
  - 用户追加：「（本期不做按 chat 持久化）这个还是要做」
created_at: 2026-10-18
tier: 1
---

# 会话 Agent 选择（团队二级 + 模型旁胶囊 + 本地持久化）— 轻量验证

## 范围

- 改动文件：
  - `webui/src/lib/teams/types.ts`（`TeamOption` 增 `leaderAgentId` / `memberAgentIds`）
  - `webui/src/hooks/useTeamOptions.ts`（从 `listTeams` 已有定义填充，不新增请求）
  - `webui/src/lib/session-agent-preference.ts`（新增：本地存储读写 + 草稿槽常量）
  - `webui/src/components/thread/AgentPicker.tsx`（新增：`AgentPickerList` + `AgentChip` + `resolveSessionAgent`）
  - `webui/src/components/thread/TeamSelector.tsx`（二级 Agent 列表）
  - `webui/src/components/thread/ThreadComposer.tsx`（模型左侧胶囊；发送优先级；改选时改写草稿里的 @）
  - `webui/src/components/thread/ThreadShell.tsx`（会话级选择状态 + 本地存储回写）
  - `webui/src/i18n/locales/*/common.json` × 10（`thread.composer.agent.{chip,empty,defaultTag}`）
- 测试：`session-agent-preference.test.ts`（新增 5）、`thread-shell.test.tsx`（新增 1 + 修 1 个时序 flake）、`team-selector.test.tsx`（+4）、`thread-composer.test.tsx`（+5）

## 语义与已知约束

- 二级选择/胶囊写入「会话默认接管者」，默认落点 = `DEFAULT_MAIN_AGENT_ID`（`commander`）。草稿里的 `@档案` 本轮优先；从胶囊改选会同步改写草稿里最后一段 `@`，避免胶囊显示（生效值）与文本互相矛盾。
- 后端零改动：发送沿用既有帧字段 `agentId`（`nanobot/agent/loop.py::_resolve_turn_agent` 已有 commander 回退）。
- **持久化边界**：本地存储键 `nanobot.webui.sessionAgent.v1`（`{ chatId: agentId }`）。刷新后仍记得；**不**跨设备/跨渠道同步（没有后端会话字段）。
- 新会话兜底：还没建出 chat 的草稿槽同时充当读取兜底，所以新会话沿用你最近一次的选择，而不是每次跳回 `commander`。显式给某个 chat 选过就按该 chat 的记录走。
- 团队选择器在已有消息的会话里本来就被契约 §4 隐藏（`ThreadShell.tsx` 的 `teamPickerVisibility`），「随时可改」由模型旁胶囊承担。

## 命令与结果

| 命令 | 结果 |
| --- | --- |
| `npx vitest run src/tests/team-selector.test.tsx src/tests/thread-composer.test.tsx`（改前） | FAIL 8/112 — 含 `option.memberAgentIds is not iterable`，符合红态预期 |
| `npx vitest run`（webui 全量） | PASS 1796 passed / 2 skipped（140 files passed） |
| `npx tsc -p tsconfig.build.json` | exit 0 |
| `npx vite build` | exit 0，`nanobot/web/dist` 已更新 |

## 时序 flake 归因与修复（`thread-shell.test.tsx`）

`retries a failed hydration when the page returns to the foreground` 在本改动下变成 ~25% 失败（12 次挂 3，均在 `waitFor(historyCalls === 2)` 处超时 ~1025ms）。

- 归因：`git stash push <本批 9 个文件>` 后重跑同一文件 **14 次全过**，恢复改动后 12 次挂 3 → 确属本批引入的时序扰动，不是既有基线。
- 根因在用例自身：它在「首次 500 的 fetch 被调用」后立刻切换前后台，而 `refreshOnReturn` 的闭包里此刻可能还没有 `historyError`（失败态尚未提交），于是直接 `return` 不重试、且不会有第二个 visibility 事件兜底 → 必然超时。
- 修复：切前后台之前先等失败态真的落地 —— `await act(async () => { await screen.findByText(HERO_GREETING_PATTERN); })`（`emptyState` 在 `loading === false` 时才渲染欢迎态）。
- 复验：修复后连续 **14 次运行全过**（其中 8 次为 74 用例配置、6 次为 75 用例最终配置）。按失败率 25% 估算，14 连过的概率约 1.7%。

## 真机验证（本机 gateway :8765，读 dist）

1. 上一轮已用无障碍树确认：胶囊在模型左侧且默认 `指挥官`；团队二级只列该团队 Leader+成员、选中后胶囊自动落到团队 Leader；改选成员后胶囊跟着变；点胶囊列出全部档案（默认档案带「默认」标记、隐藏档案不出现）。
2. **本轮未完成「刷新后仍记得」的端到端复核**：脚本点击时发现该浏览器页停在 `#/agents` 并开着「编辑 · 代码评审」对话框，判断该标签页与你本人的操作共用，继续脚本点击/刷新会打断你正在编辑的内容，故主动停止。该路径由单测覆盖：`thread-shell.test.tsx`「会话级 Agent 选择按 chat 记在本地存储里，刷新后仍然生效」（预置存储 → 胶囊显示存储值；改选 → 只写回该 chat 一条）。

## 未验证项

- eslint 未跑：仓库根 `bun run lint` 依赖的 eslint 未装在本机（`npx eslint` 拉到的是 v10 非项目版本），只以 `tsc` 作门禁。
- 真机未发消息验证「帧里 agentId 被后端接受」（需真实模型调用），单测覆盖 options 构造与 `@` 优先级。
- 未验证多标签/跨设备场景（本设计明确不做同步）。
