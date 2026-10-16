# 执行日志：Agent Team WU-11（端到端验证与独立审查）

- 分支：`feature/agent-team`
- 计划：`.ai-runtime-artifacts/plans/2026-10-15-agent-team-plan.md` Task 11
- 日期：2026-10-16
- 执行者：主 Agent（patch 作者）

## 本轮做了什么

1. **后端端到端测试** `tests/webui/test_teams_e2e.py`（6 条）：真 repository / 真调度器 / 真事件投影，
   只把 LLM 边界 `execute_frozen` 换成替身，因此 `TeamMemberExecutor` 的 fail-closed 守卫、活动转发、
   节流、事件投影都是真的在跑。覆盖：全成功链路（并发峰值 2、依赖顺序、seq 严格递增、只出现 `team_event`
   与 `team_snapshot` 两种帧）、成员失败 → partial + 依赖者 blocked、取消幂等且回报真实状态、
   重连 hydrate + 事件补拉、重启打断且不重放、跨会话取消被拒。
2. **前端整链路测试** `webui/src/tests/team-flow.test.tsx`（9 条）：帧 → `parseTeamFrame` → seq reducer →
   `useTeamProgress` → 面板 DOM。覆盖首帧立起面板、重连快照开局、`run.finished` 整份快照、终态不被迟到活动推翻、
   断号补拉、`resetRequired` 回快照、旧快照不回退、未知帧是空气（与普通聊天共用一个订阅）。
3. **独立审查**：派了两个子 Agent（代码审查、安全审查），作者不审自己的 patch。两份报告已落盘。
4. **按审查结论修复**：3 条阻塞项全部修复并各自做了变异检查；另修 5 条应修/低危。
5. **计划 §7 全量验证**：命令与证据见 `verifications/2026-10-16-agent-team-collective-test.md`。

## 修复清单（审查 → 处置）

| 编号 | 修复要点 | 主要文件 |
| --- | --- | --- |
| B1/I1 | `run.started` 按契约解析 `{binding,startedAt}`；reducer 支持无快照 bootstrap；共享 fixture 真正被 TS 侧消费 | `webui/src/lib/teams/events.ts`、`reducer.ts`、`tests/team-events.test.ts` |
| B2/H1 | 调用方会话身份由服务端从连接推导并一路带到控制器，跨会话控制 403 | `inbound_commands.py`、`ws_http.py`、`settings_contracts.py`、`settings_routes.py`、`teams_routes.py`、`teams_api.py`、`teams/runs.py` |
| H2 | 读动作补会话归属校验 | `teams/service.py`、`teams_api.py` |
| B3 | 恢复续接不挂 `team_dispatch`、不开新运行 | `teams/turn.py` |
| R1 | 任务数预算按运行累计 | `teams/repository.py`、`teams/scheduler.py` |
| R2 | 存储/状态转移异常转工具错误，不打崩 Leader 回合 | `agent/tools/team.py` |
| R3 | 快照与 `lastSeq` 同一读事务 | `teams/repository.py` |
| R5 | 缺口按 seq 补拉、`resetRequired` 回快照；修掉增量回放不清 `needsReplay` 的 bug | `webui/src/hooks/useTeamProgress.ts`、`reducer.ts` |
| L1 | `batchId` 长度上限 128 | `agent/tools/team.py` |
| 顺带 | 面板只在运行终态且无任务时才说「这回合 Leader 自己答了」 | `TeamProgressPanel.tsx` |

## 有意偏离契约/计划的地方

**重试走后台 asyncio 任务，不走常规 Leader 回合排队。**
`TeamRunController.retry_run` 落盘新运行后用 `self._start()` 起一个后台任务，turn id 形如 `retry:<hex>`
（`nanobot/teams/runs.py`）。原因：重试只复制失败/中断子图，不经过模型重新派活，因此不需要 Leader 回合上下文。

- 已满足的部分：新运行仍由同一个 `TeamScheduler` 接手，`_run_slots` 共享并发配额与同成员互斥锁，
  事件照常投影与投递，取消走同一套控制器。
- 缺口：新运行没有常规回合的 `turn_finalizer` 兜底；`retry:` 开头的 turn id 不出现在消息流的用户可见轮次里，
  面板只通过 `run.started` 事件感知。若后续要求重试也在时间线上留可见轮次，需要在 `turn.py` 侧补。

**读动作在 HTTP GET 下降级为工作空间边界。**
契约 §5 要求 `GET /api/team-runs` 做「会话访问检查」，但普通 HTTP GET 这条路上没有聊天身份可推导
（身份只存在于已认证 WS 连接的挂载聊天集合里）。实现为：能推导时严格比对，推导不出时不拦，
信任边界退到工作空间。控制动作不走这条退路——它们是 WS-only，推导不出归属一律 403。

## 遗留

- R4：历史运行按 turn 呈现（契约 §7）未做，原因与后续做法见集体测试报告 §6。
- M1：写集锁由 Leader 模型自行声明，按契约 §7 属既定设计，记录为已知风险。
- 本轮未做真实 gateway 进程冒烟；tui 的 `bun run check` / `bun run test` 因本机无 bun 未执行（本分支未改 tui）。

## 产物

- `verifications/2026-10-16-agent-team-collective-test.md`
- `reviews/2026-10-16-agent-team-code-review.md`
- `reviews/2026-10-16-agent-team-security-review.md`
