---
artifact: verification
route: orchestration:dispatcher-workflow
skills: [verification-before-completion, test-driven-development]
group: 3
head: 40ea493
base: 2700025
worktree: /Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/zhixu/wt-agent-team
branch: harness/wt-agent-team
created_at: 2026-10-16
status: verified
---

# G3 集体测试（WU-04 / WU-05 / WU-09）

Leader 主线程真实执行命令并逐条记录输出；本批**没有**子代理参与（用户明确拒绝 `Agent` 派发），
所以「独立验证者」这一角色由 Leader 在实现之后的独立一遍通读替代，局限见 §7。

## 1. 命令与结果（全部为本 head 复跑）

| 门 | 命令（worktree 根） | 结果 |
| --- | --- | --- |
| 后端主门 | `.venv/bin/python -m pytest tests/teams tests/webui tests/agent tests/test_webui_terminal.py -q` | **2815 passed, 1 failed**（71.77s） |
| 唯一失败归因 | `tests/agent/test_mcp_reconnect_crash.py::test_mcp_reconnect_during_shutdown_does_not_crash` | **基线既有**：在主 checkout 原始 `2700025` 同样失败（G2 已记录），本批不修 |
| Python lint | `.venv/bin/python -m ruff check <本批 23 个触及文件>` | **All checks passed!** |
| Python 类型 | `.venv/bin/python -m basedpyright nanobot/teams nanobot/webui tests/teams` | 41 errors，**全部在未触及文件**（见 §6）；本批触及文件 0 errors |
| 前端单测 | `cd webui && npx vitest run` | **118 files / 1573 passed**（28.06s） |
| 前端类型 | `cd webui && npx tsc -p tsconfig.build.json --noEmit` | 无输出（exit 0） |
| 前端构建 | `cd webui && npm run build` | exit 0，`✓ built in 6.87s` |
| 前端 lint | `npx eslint --config webui/eslint.config.js webui/src --max-warnings 0` | exit 0 |

`bun` 在本机不可用，`webui/package.json` 的 `bun run test/build/lint` 用其等价命令执行（vitest / tsc / vite / eslint 同源）。

## 2. 契约逐条核对（G3 覆盖面）

| 契约条目 | 证据（测试名） | 结论 |
| --- | --- | --- |
| §3 读动作走认证 HTTP GET、写动作只走 WS `requestMutation` 白名单 | `test_teams_routes.py::test_*_mutation_path`（`settings_routes._SYSTEM_ROUTES`、`is_mutation_path`、`ws_http._WEBUI_MUTATION_PATHS` 三处同时断言） | 通过 |
| §3 `teams.save → {team}` / `archive → {id,archived:true,revision}` / `clone → {team}` | `test_teams_routes.py` 真 `TeamService` 集成用例（创建→列表→重放相等→revisions==[1]→冲突 409→归档→归档后 save 404） | 通过 |
| §4 错误码前缀 | 参数化翻译用例 + `test_unexpected_error_is_not_leaked_as_a_success`（500 且不含内部文本） | 通过 |
| §4 Agent 删除被未归档团队引用 → 409 `AGENT_IN_USE_BY_TEAM` + 团队 ID 列表 | `test_agents_api.py` 新增 2 用例（有引用 / 无 lookup / lookup 空） | 通过 |
| §4 `new_chat` 扩展 `{team_id?, team_revision?}`，省略 revision 由服务端解析 | `test_teams_chat_binding.py::test_new_chat_forwards_an_explicit_revision`（断言 `service.prepared == [("content-team", 3)]` 与 `("content-team", None)`） | 通过 |
| §4 准入失败不建聊天 | `test_new_chat_rejects_admission_without_creating_a_chat`（`attached` 空、`webui_attach` 未调、`webui_hydrate` 未调） | 通过 |
| §4 首条消息前完成持久化才能启动 Leader | `test_leader_context.py::test_first_team_message_persists_the_binding_before_the_leader`（provider 看到的 `session.metadata` 已含绑定 + 磁盘回读一致） | 通过 |
| §4 首次消息不得携带/覆盖可信 TeamSnapshot | `test_persist_message_binding_never_overrides_an_existing_binding` + `test_a_bound_session_is_not_overridden_by_the_message` | 通过 |
| §4 空聊天重连暂存失效 → `TEAM_BINDING_EXPIRED`，不静默退回普通模式 | `test_expired_staging_fails_the_first_message_instead_of_plain_chat`（`dispatched == []`）、`test_reconnecting_an_empty_expired_chat_reports_the_binding_expiry`（一次性告知） | 通过 |
| §4 `attached` / `session_updated` / 列表新增可空 `team_binding` | `test_session_projection.py`（含 null 与已绑定两态）、`test_session_list_index.py::test_webui_session_list_indexes_the_team_binding`、`chat-list.test.tsx::marks a team-bound chat` | 通过 |
| §4 缺字段按普通聊天解释 | 列表 payload 不带 null 键（`ws_http._sessions_list_payload`，与 `recovery_state` 同一口径）；前端 `s.teamBinding ?? null` | 通过 |
| §4 fork 继承团队修订、不带 runId | `test_fork_inherits_the_team_but_not_any_run`（真 `SessionManager.fork_session_before_user_index`） | 通过 |
| §4 临时聊天不显示团队选择 | `team-selector.test.tsx::已绑定与临时聊天各自决定选择器形态`（纯函数口径） | 通过 |
| §5 Leader 白名单不含 `spawn`、per-turn registry 是新对象、空集合不等于全量 | `test_leader_context.py` 的 `TEAM_HIDDEN_TOOLS` / `test_empty_registry_stays_empty` / `test_restore_turn_keeps_the_caller_registry_identity` | 通过 |
| §5 Leader preset 不可用时整回合失败 | `test_unavailable_leader_preset_fails_the_turn_instead_of_falling_back`（`provider.calls == 0`） | 通过 |
| §1.2 领域 camelCase / 信封 snake_case | `test_persist_writes_the_camel_case_binding_into_session_metadata`；`team-chat-creation.test.tsx` 断言 `{type:"new_chat", team_id, team_revision}` 帧 | 通过 |

## 3. 前端「服务端确认绑定是唯一真值」的机器证据

- `nanobot-client.test`（新文件内）：`attached` 不带 `team_binding` 时 `teamBindingFor()` 保持原值；带 `{...}` 时更新；`session_updated(scope="metadata")` 也更新。
- `team-chat-creation.test.tsx::乐观行带的是服务端确认的绑定`：`createChat(..., {teamId})` 后乐观行的 `teamBinding` 等于**服务端回读值**，不是本地选择对象。
- `准入失败时不插入乐观行`：`TEAM_NOT_FOUND` → promise reject 且 `sessions == []`。
- 全链路里客户端**没有任何**写入 `session.metadata["team_binding"]` 的通道（写只发生在 `loop._restore_turn` 的 `persist_message_binding`）。

## 4. 竞态口径的实测说明

`claim` 用 `pop` + TTL 双条件，一次性和过期判定共用同一把锁；`test_concurrent_claims_only_one_wins` 用两线程 barrier 实测只有一人拿到。
绑定的落盘发生在回合内、Leader 装配之前（`_restore_turn` 顺序：`persist_message_binding` → `sessions.save` → `_team_context_for_turn` → `build_leader_registry`），所以并发首消息的两条都携带同一份可信绑定，后落盘的那条不会覆盖先绑定的会话。

## 5. 本批**没有**闭环的一项（必须显眼记录）

**`loop.team_context_provider` 仍未接线**，因此「选了团队 → 这条聊天真的按 Leader 的提示词/工具白名单/skill 排除/模型跑」目前在生产路径上**不成立**：

- 已完成：绑定的暂存、首消息持久化、会话读数、前端选择与展示、Leader 装配的全部纯逻辑（`build_leader_registry` / `render_role_prompt` / `resolve_leader_runtime`）以及它们的回合级测试（测试里注入 provider）。
- 未完成：把 `TeamRevisionSnapshot` 解析成 `LeaderTurnContext`（`leader_context_from_snapshot`：按已安装工具名解析 `CapabilitySelection`、算 `skill_exclude`、取 `model_id`）并在 gateway 装配处赋给 `loop.team_context_provider`。
- 归属：`dispatch` 表把 loop 变更判给 **WU-06**（"service/loop 修改由所属整合者合入"），本批按该约束没有跨范围写 loop。
- 影响：WU-06 完成前，团队聊天与 `team_dispatch` 都还不可用；WU-11 的 e2e 必须覆盖「绑定聊天 → Leader 系统提示含 `# Team Mode`」。

## 6. basedpyright 41 处错误的分布（均不在本批触及范围）

`pty_terminal.py` 15、`memory_api.py` 10、`terminal_bridge.py` 4、`memory_services.py` 3、`workspace_files.py` 2 —— 本批未修改这些文件。
`inbound_commands.py` 5 与 `gateway_services.py` 2 出现在本批**曾修改**的文件里，但逐条为基线既有：前者用 `git show HEAD:nanobot/webui/inbound_commands.py` 写成同目录临时文件、跑同一份 pyright 配置复验得同样 5 条（terminal 帧的 `TerminalStartedWirePayload` / 可选属性访问）；后者两行 `MemoryServices.for_workspace` partially unknown，`git blame` 归因 `a38a1531`（2026-09-18）。
结论：**本批新写的模块（`nanobot/teams/*`、`teams_api.py`、`teams_routes.py`、`binding.py`、`session_projection.py`、`session_list_index.py`、`ws_http.py`、`settings_routes.py`、`loop.py` 的团队分支、`tests/teams`、团队相关 `tests/webui`）为 0 error**。

## 7. 审查方式与局限

- 用户在本会话明确拒绝了一次 `Agent` 派发调用；`orchestration` skill 的禁止项含「派发子 Agent 做全项目编排」。因此 G3 的代码审查与安全审查由 **Leader 分离式自审**完成：实现阶段结束后重新独立通读源码取证，不复用实现时的判断。
- 这**不等于**独立审查。`requesting-code-review` 的「代码写入者不审自己的 patch」在本批未满足，已在两份审查产物的开头逐字记录，并要求 WU-11 补真正的独立 reviewer + security reviewer。
- 未做的事：没有跑真实模型（不发付费调用）、没有读本机 `~/.nanobot` 配置或密钥、没有真实浏览器操作 UI（前端结论只到组件/帧级断言，见 §8）、没有 push / PR / 合并回 `feature/desktop-app`（须用户确认）。

## 8. UI 真实性的诚实边界

按 `verification-before-completion` 的口径：本批 UI 变更**没有**在浏览器里点过。判定依据是 `thread-shell.test.tsx` / `chat-list.test.tsx` / `team-selector.test.tsx` 的渲染与事件断言、以及 `npm run build` 通过。以下项需要人工或 e2e 复核：
选择弹层在窄屏/触屏上的位置与滚动、chip 与项目选择器并排时的截断、团队名很长时的换行、绑定后 chip 的视觉权重是否会被误认为可点。
