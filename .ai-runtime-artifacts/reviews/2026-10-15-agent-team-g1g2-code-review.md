---
feature: agent-team
phase: G1+G2 尾盘代码审查
baseline: 2700025
head: 03377c4
branch: harness/wt-agent-team
scope: WU-01 / WU-02 / WU-03 / WU-08
status: reviewed-with-fixes
reviewer: Leader（自审，见「审查方式与局限」）
date: 2026-10-15
---

# Agent Team G1+G2 代码审查

> 范围：`2700025..03377c4` 共 7 个提交，41 个文件、+8419/-36。
> 严重度：BLOCK = 必须修后重新审查；WARNING = 建议修但不阻塞；INFO = 知会。

## 审查方式与局限（必须先读）

**本批次没有做独立子代理审查。** 用户在本会话中明确拒绝了一次 `Agent`（子代理派发）工具调用，且
`orchestration` skill 的禁止项里列有「派发子 Agent 做全项目编排」。因此 `dispatcher-workflow`
尾盘要求的「并行扇出 reviewer + security-auditor」被替换为 **Leader 分离式自审**：实现与审查分成
两次独立通读，审查阶段重新读源码（不看实现时的笔记），并逐条以可运行命令取证。

这不等价于独立审查：同一个人写的代码由同一个人复核，系统性盲区（尤其「我以为契约这么写」类误读）
无法被发现。**WU-11（Task 11）必须补一次真正的独立审查**，本文件的结论只作为 G1+G2 的阶段性口径。

证据来源：本批 WU 的测试输出 + `2026-10-15-agent-team-g1g2-collective-test.md`。

## 1. 正确性（correctness）

| # | 主题 | 严重度 | 处置 |
| --- | --- | --- | --- |
| 1.1 | 团队编辑器保存后 `useEffect` 以 `team` 对象为依赖，回填基线把用户正在输入的草稿冲掉 | BLOCK | **已修** `42e89aa`。依赖改为 `[open, teamId]`（身份而非内容），保存后不再重置；补回归测试「保存成功后继续编辑不被基线回填覆盖」。 |
| 1.2 | 编辑器只暴露 `maxConcurrency`，但提交时把 `limits` 整体换成 `defaultTeamLimits()`，`maxTasksPerRun` / `maxTokens` 等被静默重置 | BLOCK | **已修** `daf6e3e`。改为 `{ ...(team ? team.limits : defaultTeamLimits()), maxConcurrency }`，补回归测试钉住「其余限额原样带回」。属静默数据损坏，比 UI 缺陷严重。 |
| 1.3 | 创建路径把带 `revision`/`createdAt`/`updatedAt` 的完整 `TeamDefinition` 发给服务端，Python `from_create_payload` 会以「must not carry server-owned fields」拒绝 | BLOCK | **已修**（WU-08 内）。新增 `toCreatePayload()`，仅在 `expectedRevision === null` 时剥离服务端字段并深拷贝 `members`/`limits`；`team-api.test.ts` 钉住「创建载荷不得携带服务端字段」。 |
| 1.4 | 成员 `role` 可能为空串（自动填充取 `agent.description`，为空时违反 Python `min_chars=1`） | WARNING | **已修**：回退到 `agent.name`，并按 `TEAM_MEMBER_ROLE_MAX_LENGTH` 截断。 |
| 1.5 | 并行上限输入用 `Number(event.target.value)`，清空输入框得到 `NaN` 并原样提交 | WARNING | **已修**：草稿存字符串，提交时 `Number.parseInt`，另加 `errorConcurrency` 范围校验。 |
| 1.6 | `events_page` 复用 `list_events`，加作用域守卫后会对同一 run 查两遍 | INFO | **已重构**：抽出私有 `_events_after()`，`list_events` = 守卫 + 裸读，`events_page` = `get_run` + 裸读。行为不变。 |
| 1.7 | `prune_activity_events` 按 `type='task.activity'` 全库删除，跨 workspace 生效 | WARNING | **已收口**（`03377c4`）：删除范围加 `run_id IN (SELECT … WHERE workspace_key = ?)`。**该函数目前无调用方**，因此这条修复没有专属测试，只有 SQL 层面的静态正确性；WU-07 接线时必须补测。 |

## 2. 可读性（readability）

| # | 主题 | 严重度 | 处置 |
| --- | --- | --- | --- |
| 2.1 | 团队页复用 Agent 域 `StatusBlock`，但其 `empty` 文案属 Agent 域（「暂无 Agent」） | INFO | **已修**：只借 `loading`/`error` 两态，空态与「无匹配」文案由团队页自己渲染；`status` 从三态归约成两态。 |
| 2.2 | 同一句冲突文案在页面告警与对话框告警各渲染一遍，测试因「找到多个元素」失败 | INFO | **已修**：`error={loadError ?? (editing ? null : actionError)}` —— 编辑器开着就归对话框。注释写明了「为什么」而不是「做了什么」。 |
| 2.3 | 成员可选集合的校验在 TS 侧另写一份 | INFO | **已改**：复用 Agent 域的 `resolveSelection(leader.subAgents, memberIds)`，允许集只有一个真相来源。 |
| 2.4 | `TeamEditorDialog` 442 行，校验分支密集 | INFO | 不拆。校验项与契约 §1 的字段一一对应，拆开反而要在两个文件间来回对照；错误项以 `field` 字符串索引、由 `errorFor()` 单点取用。 |

## 3. 架构（architecture）

| # | 主题 | 严重度 | 处置 |
| --- | --- | --- | --- |
| 3.1 | 后端团队域按 5 文件切分（models / database / repository / service / execution） | INFO | 与 writing / memory / identity 同构；`repository` 是唯一 SQL 出口，`service` 不碰 SQL。 |
| 3.2 | 「teams」入口走 `SettingsSidebar` + `SettingsPage` 的 switch，没有动 `App.tsx` | INFO | 有意为之：`App.tsx` 是 dispatch 指定的前端独占文件，由并行会话持有（plan §8 已预警）。WU-09 需要动 `ThreadShell.tsx` 时同样只能由唯一整合者执行。 |
| 3.3 | `webui/src/lib/teams/{types,api}.ts` 与契约 §1 一比一镜像，读走 HTTP、写走 WS mutation 白名单 | INFO | 与契约口径一致。`PreviewApp.tsx` 的 section 归约同步加了 `"teams"`（否则 build 报错）。 |
| 3.4 | 限额区间（`TEAM_LIMIT_RANGES` 等）在 TS 与 Python 各写一份字面量 | WARNING | **本批不修**。缺跨语言锚点，两边漂移不会被任何测试发现。建议：把区间写进契约，并在 WU-11 加一个读 fixture 的双侧对齐测试（fixture 已经是共用文件，成本低）。 |
| 3.5 | `listTeams(limit: 100)` 忽略返回的 `nextCursor` | WARNING | **本批不修**。团队数量超过 100 才会出问题，当前不可达；WU-05 接通真实分页参数时一并处理。 |

## 4. 安全概览（security overview）

完整安全审查见 `2026-10-15-agent-team-g1g2-security-review.md`。此处只列结论：1 项 High（子表跨
workspace 读取）**已修并带回归测试**（`03377c4`）；其余为 INFO/WARNING，无未处置的 BLOCK。

## 5. 测试与验证

| 门 | 命令 | 结果 |
| --- | --- | --- |
| 后端单测 | `.venv/bin/python -m pytest tests/teams -q` | 284 passed |
| 后端 lint | `.venv/bin/python -m ruff check nanobot/teams tests/teams` | All checks passed |
| 后端类型 | `.venv/bin/python -m basedpyright nanobot/teams` | 0 errors, 0 warnings, 0 notes |
| 前端单测 | `npx vitest run` | 见集体测试产物 V4 |
| 前端 lint | `npm run lint` | 见集体测试产物 V5 |
| 前端构建 | `npm run build` | 见集体测试产物 V6 |

注：`bun` 本机未安装，前端一律用 `npx vitest run`（与 `bun run test` 等价，已在集体测试产物中说明）。

## 6. 遗留与移交

1. **WU-11 必须补独立审查**（本文件是 Leader 自审，见开头「审查方式与局限」）。
2. 创建时撞已存在 id，报的是「已被其他改动更新」这类修订冲突文案 —— 措辞对不上场景，需要一个
   `TEAM_ID_TAKEN` 口径或前端预判。
3. 冲突后对话框里没有「加载最新版本」入口，用户只能关掉重开（草稿会保留）。
4. `prune_activity_events` 无调用方、无专属测试（1.7）。
5. 限额区间缺跨语言锚点（3.4）、团队列表分页未接（3.5）。
6. 基线既有失败（`test_serve_*`、`tests/agent` 的 F401、`test_mcp_reconnect_crash`）不属本批，
   WU-11 全量验证时统一归因，不得记为本批回归。
