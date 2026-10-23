---
artifact: execution-log
route: orchestration
created_at: 2026-10-16
status: completed
---

# Agent Team WU-06 执行日志（调度器 + team_dispatch + 回合启停桥接）

## 交付内容

- `nanobot/teams/scheduler.py`：`TeamRunHandle` 持有批次计划表（`_planned` / `_results` / `_batches`），
  整批校验走 Kahn 拓扑序落库、声明序返回；`open_run(session_key, turn_id)` 让内部续接复用同一条运行；
  `start_run(snapshot=…, runtime=…)` 支持沿用准入快照与按回合注入默认运行时。
- `nanobot/teams/turn.py`（新增）：`TeamLeaderTurns` 是 loop 的唯一注入点，负责绑定→运行→回合上下文，
  以及 `cancel_runs` 停止桥接；`build_team_turns(...)` 是 gateway 组装层用的工厂；`bind_team_turns(...)`
  在无团队栈时一字不改普通 gateway。
- `nanobot/agent/tools/team.py`（新增）：`team_dispatch` 工具，模型可见字段 `batchId` / `tasks`，
  `_plugin_discoverable = False` 保证它不进 `ToolLoader.discover()`，普通聊天 registry 里不存在。
- `nanobot/agent/loop.py`：`team_context_provider(msg, session, turn_id)` 装配、`turn_finalizer` 收口
  （正常路径按「有没有交付」判定，`BaseException` 路径一律未交付）、`team_run_cancel_hook` 停止桥接。
- `nanobot/agent/subagent.py`：新增 `subagent_tool_names()`，暴露 `scope="subagent"` 的真实工具目录。
- `nanobot/webui/gateway_services.py` + `nanobot/channels/manager.py` + `nanobot/cli/gateway_runtime.py`：
  gateway 里唯一一份 `TeamService` 装配出团队栈，`ChannelManager.webui_gateway` 回捞后 `bind_team_turns`。

## 本轮验收

| 项 | 结果 |
| --- | --- |
| `pytest tests/teams tests/agent/tools/test_team.py` | 346 passed |
| `pytest tests/webui tests/channels` | 1056 passed（随机序下 1 例既有跨文件污染，见下） |
| `ruff check nanobot/teams tests/teams` | 通过 |
| `basedpyright nanobot/teams tests/teams/test_leader_*.py` | 0 errors |

## 验收中发现并修复的真实缺陷

1. **成员起跑顺序随机**：`queued` 落库在成员锁之前，一次 `to_thread` 的耗时让同成员三任务里
   不是声明首任务先拿到锁（`test_same_member_is_serialized`、`test_single_task_cancel_does_not_touch_siblings`
   在跨文件混跑时翻车）。改为先取成员锁再落 `queued`。
2. **`dependsOn` 只是排序标签**：上游结果没有进入下游指令。新增 `_instruction_for()`，按预算把已成功的
   前置 `summary` + 产出拼进指令。
3. **`cancelling` 无合法事件映射**：`TASK_EVENT_BY_STATUS` 缺该状态导致 `update_task_status` 直接拒绝。
   按契约 §6 复用 `task.activity`，不从调度器绕过仓储。
4. **落库顺序 = 声明顺序 → 环检测缺失**：`_require_acyclic` 换成 `_toposort`，排不完的部分即环，
   同层保持声明顺序；建任务因此总能拿到已存在的前置 `taskId`。
5. **装配时抓死的工具目录会过期**：MCP reload 与技能开关发生在 gateway 启动之后，
   `SnapshotMemberCatalog` 改为接受可调用目录，Leader/成员各自现取。

## 与计划的偏差（需 Leader 确认）

- 计划只列了 `scheduler.py` / `agent/tools/team.py` 两个新增文件；实际多了 `nanobot/teams/turn.py`
  与 `tests/teams/test_leader_turn.py`。理由：运行「谁创建、谁收口」不属于 loop（不能让 loop 认识团队概念），
  也不属于 scheduler（scheduler 不管会话绑定与准入），单独一层最省。
- `team_context_provider` 由计划的 `(msg, session)` 改为 `(msg, session, turn_id)`。内部续接必须复用同一条
  运行，而 `team_runs` 上 `(session_key, turn_id)` 有唯一索引，不给内部轮次号就无法区分「续接」与「新提问」。
  已同步 `tests/teams/test_leader_context.py` 的 4 处装配点。
- `AgentLoop.tool_names` 是 property 而非方法：工厂边界两种都接受，避免启动即坏。

## 遗留风险

- `tests/channels/test_channel_plugins.py::test_enable_optional_feature_refreshes_feishu_identity`
  仅在 pytest-randomly 乱序且 `tests/webui` 先跑时失败，单跑与固定序均通过；非本轮改动引入，留给 WU-11 复现定位。
- 停止桥接目前 await 整条会话的运行取消，未来若单轮取消耗时过长需要超时兜底（契约 §6 未给数值）。
- gateway 装配未做真实进程冒烟（不发付费模型调用）；`build_team_turns` 对真实 `AgentLoop` 的表面
  由 `test_team_stack_factory_matches_the_real_loop_surface` 钉住。
