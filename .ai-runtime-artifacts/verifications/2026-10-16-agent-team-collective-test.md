# Agent Team 集体测试与验证证据（WU-11）

- 分支：`feature/agent-team`（2026-10-16 合并进 `feature/desktop-app`，见 §8）
- 基准：`.ai-runtime-artifacts/contracts/2026-10-15-agent-team-contract.md`（冻结）
- 计划：`.ai-runtime-artifacts/plans/2026-10-15-agent-team-plan.md` §7
- 执行：主 Agent（patch 作者）；审查由两个独立子 Agent 完成（作者不审自己的 patch）
- 日期：2026-10-16

## 1. 结论

功能链路与审查发现的缺陷均已修复并复验通过；**已知未完成项 1 条（历史运行按 turn 呈现，契约 §7）**，其余计划内检查全部通过。
不满足「零失败全绿」的唯一原因是仓库既有的、与本功能无关的失败（见 §4），已用基线对照证明。

## 2. 计划 §7 命令逐条结果

| 命令 | 结果 |
| --- | --- |
| `pytest tests/teams/ tests/webui/test_teams_{routes,binding,projection,e2e}.py tests/agent/tools/test_team.py -q` | **447 passed** |
| `pytest tests/agent/test_subagent_lifecycle.py tests/webui/test_subagent_{profile_fail_closed,spawn_e2e}.py tests/agent/test_session_model_runtime.py tests/webui/test_session_projection.py tests/utils/test_webui_event_projection_equivalence.py -q` | **75 passed** |
| `ruff check nanobot/ tests/teams/` | 10 处 F401/N806，全部在 `nanobot/memory/**`（另一分支的既有欠账）；`nanobot/teams/` **干净** |
| `basedpyright`（全仓） | 1867 errors 为仓库既有基线；按本次改动文件过滤后仅剩 10 处，全是 `MemoryServices.for_workspace` 的 `reportUnknownMemberType`（同属 `nanobot/memory/**`）。teams 模块与其余改动文件 **0 error** |
| webui `test` | 1613 项，1608 passed；3 项失败见 §4 |
| webui `lint`（`eslint --max-warnings 0`） | 通过 |
| webui `build`（`vite build`） | 通过（14.9s） |
| webui `tsc -p tsconfig.build.json --noEmit` | 通过 |
| tui `bun run check` / `bun run test` | **未能执行**：本机无 `bun`，`tui/node_modules` 未安装；本分支未改动 `tui/` 任何文件（`git status tui` 为空）。等价的「旧消费者忽略未知 Team 类型」在两侧另有覆盖：Python 侧 `tests/utils/test_webui_event_projection_equivalence.py`，TS 侧 `webui/src/tests/team-flow.test.tsx` 的「看不懂的帧是空气」组 |
| `pytest tests/ -q`（全量） | 修复前 7466 passed / 15 failed；15 项全部在 `tests/cli/`、`tests/test_webui_terminal_shells.py`、`tests/tools/`、`tests/agent/test_mcp_reconnect_crash.py`，用 `git stash` 取基线复跑同样失败 → **既有失败，非本分支引入** |

## 3. 覆盖与变异验证

- 新增 teams 模块行覆盖 **93%**（`--cov=nanobot.teams --cov=nanobot.agent.tools.team`），逐模块最低 87%，高于计划要求的 85%。
- 关键测试都做过**变异检查**（把被测行为改坏，确认测试变红，再还原）：

| 被破坏的行为 | 变红的测试 |
| --- | --- |
| 快照帧名改成 `broken` | `test_reconnect_hydrates_open_runs_and_replays_events`、`test_restart_interrupts_open_run_without_repeating_work` |
| 重启不再标记 interrupted | 同上两条 |
| 会话归属校验直接 `return` | `test_control_from_another_session_is_forbidden`、`test_cancel_from_a_foreign_session_is_refused` |
| 恢复回合不再拦派活 | `test_recovery_continuation_never_gets_dispatch_tool` |

## 4. 既有失败（不在本分支范围，未修）

- **Python 全量 15 项**：`tests/cli/test_commands.py`（gateway health / serve / 本地触发队列 / MCP provider 关闭）、`tests/test_webui_terminal_shells.py`、`tests/tools/test_shell_reap.py`、`tests/tools/test_exec_session_tools.py`、`tests/agent/test_mcp_reconnect_crash.py`。证据：`git stash push` 后在同一工作树上复跑，失败集合一致；本分支未触碰 PTY/shell/CLI 进程路径。
- **WebUI 3 项**：`add-project-dialog`、`preview-thread`、`settings-providers`。失败用例名每次运行都不同（并行 flake），单独跑通过。`preview-thread` 已做对照：把 `ThreadShell.tsx` 还原成 HEAD 版本后**同样失败**，故与本分支对 `ThreadShell` 的增量无关。
- 全程会有一条 `EPERM ... node_modules/.vite/vitest/results.json` 的 unhandled error：本机 Windows 文件锁问题，不影响测试结论。

## 5. 独立审查与处置

两份审查报告（作者之外的独立审查者）：

- `.ai-runtime-artifacts/reviews/2026-10-16-agent-team-code-review.md`
- `.ai-runtime-artifacts/reviews/2026-10-16-agent-team-security-review.md`

处置结果（全部已实现并有对应测试）：

| 编号 | 问题 | 处置 |
| --- | --- | --- |
| B1 / I1 | `run.started` 前后端白名单不一致，前端静默丢弃全部 `run.started` | 已修：TS 按契约 §6 拆分为 `run.started={binding,startedAt}`、`run.finished={run}`；reducer 新增「无快照时以 run.started 立起运行」；`team-events.test.ts` 改为真读共享 fixture `tests/fixtures/teams/event.json`（此前只有 Python 侧读，两端各绿） |
| B2 / H1 | 运行取消/重试不带调用方会话身份 | 已修：身份由服务端从连接推导（`inbound_commands` → `ws_http` → `SettingsRequest.caller_chats` → `teams_routes` → `TeamRunController`），payload 自称的 chatId 覆盖不了；`run_context` 比对不一致即 403，推导不出归属按拒绝 |
| H2 | 三条读动作无会话归属校验 | 已修：`service.require_run_session` + `require_caller_session`；读动作在身份可推导时校验，HTTP GET（无聊天身份，边界是工作空间）不拦 |
| B3 | 恢复续接会重放 `team_dispatch` | 已修：`TeamLeaderTurns.__call__` 识别 `_webui_recovery_id` 直接返回 None——不开新运行、不挂派活工具；要重新派活走面板上的重试入口 |
| R1 | `maxTasks` 按批计，可换 batchId 绕过 | 已修：改为按运行累计（`repository.task_count`），并加测试 |
| R2 | `IllegalTaskTransition` / `StorageUnavailable` 会打崩 Leader 回合 | 已修：转成工具错误回给模型 |
| R3 | 快照与 `lastSeq` 不在同一读事务（契约 §6 明写） | 已修：单连接单事务读完 run 行与 tasks |
| R5 | `loadTeamRunEvents` 无消费方，`resetRequired` 协议端到端没跑过 | 已修：缺口先按 seq 补拉，收到 `resetRequired` 才回快照；顺带修掉一个真 bug——增量回放不清 `needsReplay`，面板会一直挂「Syncing…」 |
| L1 | `batchId` 无长度上限 | 已修：按 `MAX_ID_CHARS`(128) 拒绝 |
| R4 | 历史运行拉到但只渲染 `activeRun`，未按 turn 呈现（契约 §7） | **未做**，见 §6 |
| M1 | 写集锁由 Leader 模型自行声明 | 按契约 §7「显式声明互不相交才放宽」属既定设计，不改；记录为已知风险 |
| O1–O5 | 可选项（事件 seq 与 `data.run.lastSeq` 关系、取消时抹 `last_action`、自证式断言、`cancelTask` 不更新本地状态、`run_pending` 无取消收口分支） | 未改，记录在审查报告 |

## 6. 已知未完成项

**R4：历史运行按 turn 呈现（契约 §7「历史分页加载 run 快照，在对应 turn 下呈现」）。**
现状：`useTeamProgress` 已按 `runId` 分页拉取历史并用同一个 reducer 归并，`TeamProgressPanel` 也在跑；`ThreadShell` 只渲染 `activeRun`，且固定在输入区上方，不在对应 turn 位置。
未做的原因：需要把 runs 按 `turnId` 索引后插进消息流对应位置，是一处涉及线程渲染结构的改动，本轮先做不影响正确性的部分，避免半成品。
后续做法：新增 `TeamRunHistory` 组件按 `turnId` 分组渲染 `TeamProgressPanel`（折叠为一行摘要），实时与历史共用同一 reducer——契约要求的「实时和历史走同一组件与 reducer」已满足，缺的是插入位置。

## 7. 未验证项（如实记录）

- 未做 gateway 真实进程冒烟（计划 §7 提到的「临时端口 + 临时数据目录」WS 全链路）：本轮以 `tests/webui/test_teams_e2e.py` 的真实栈（真 repository / 真调度器 / 真事件投影，只替掉 LLM 边界 `execute_frozen`）替代，端到端覆盖绑定→派单→成员并行→事件投递→重连→取消→重启。
- tui 的 `bun run check` / `bun run test` 未执行，原因见 §2。
- 安全审查者自述未能验证的两处：`nanobot/agent/subagent.py` 中活动事件 payload 是否含工具参数/输出、契约 §5「Team 模式移除普通 spawn」在 `loop.py` 的改动。前者本轮由 §7 白名单投影测试覆盖到事件层，后者未专项复核。

## 8. 合并到 feature/desktop-app 与合并后复验

- 合并：`git merge --no-commit --no-ff feature/agent-team`（对方 tip `91c3dd2` ← 本分支 `16aa36c`）；合并前已 `git fetch`。
- 4 处冲突全部在前端设置域，按「保留 desktop-app 已有的裁剪，只把 teams 接回去」处置：
  - `webui/src/components/settings/contracts.ts`：分组保留 desktop-app 的 `agents | teams | runtime | advanced`（其已移除 memory/identity）；
  - `SettingsPage.tsx` / `SettingsSidebar.tsx`：仅保留 `teams` 入口与 `TeamsView`；
  - `webui/src/App.tsx`：删除 desktop-app 已移除的 `onAddPane` 块；对方 `onCreateChat(workspaceScope, _initialMessage, modelPreset, team?)` 已经带团队参数。
- **补跟踪一个遗漏文件**：`nanobot/agent/tools/team.py` 从未被 git 跟踪（`.gitignore` 无匹配规则，属漏 `git add`），但分支上的 `nanobot/teams/turn.py` 与 `tests/agent/tools/test_team.py` 都 import 它——干净检出会直接 ImportError。随本次合并入库。
- 合并后复验（merged tree）：
  - `pytest tests/ -q -p no:randomly`：**7471 passed / 15 failed / 91 skipped**（8:24），15 项失败与 §4 的既有失败清单**逐条一致**（cli/terminal/shell/exec_session/mcp_reconnect），无新增失败；
  - teams 相关套件 **447 passed**；
  - webui vitest：1613 项，4 failed，均为 §4 的轮转 flake（逐条隔离复跑通过）；
  - `tsc -p tsconfig.build.json --noEmit`、`eslint --max-warnings 0`、`vite build` 全部通过；10 个 locale 键数一致（1908），`thread.teamProgress` 保留；`ThreadShell.tsx` 同时保留 `useTeamProgress` 面板与 desktop-app 工作台改动。
- 合并中发现并修复：`tests/webui/test_settings_routes.py` 的 fake 未跟上 B2 的 `caller_chats` 参数（4 failed）→ 修复后 **26 passed**。
