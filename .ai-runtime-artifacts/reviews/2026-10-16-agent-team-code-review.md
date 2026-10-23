---
artifact: review
route: code-review
scope: Agent Team 实现对冻结契约的忠实度
baseline_ref: .ai-runtime-artifacts/contracts/2026-10-15-agent-team-contract.md
plan_ref: .ai-runtime-artifacts/plans/2026-10-15-agent-team-plan.md
dispatch_ref: .ai-runtime-artifacts/plans/2026-10-15-agent-team-dispatch.md
branch: feature/agent-team
created_at: 2026-10-16
reviewer: 独立审查 Agent（未参与编码）
status: complete
---

# Agent Team 实现代码审查（对照冻结契约）

## 0. 审查方式与证据

- 只读审查：未修改任何源码/测试，未 commit/push，未开分支。
- 基线三件套已读：契约 `2026-10-15-agent-team-contract.md`（status: frozen）、计划
  `2026-10-15-agent-team-plan.md`、调度 `2026-10-15-agent-team-dispatch.md`。
- 审查范围按 `git status --porcelain` / `git diff --stat` 确定；`.ai-runtime-artifacts/*agent-evaluation*`
  与 `research/2026-10-15-memory-extraction-blog/` 未触碰。
- 实跑证据（只读执行）：
  - `uv run --no-sync python -m pytest tests/teams/ tests/webui/test_teams_routes.py
    tests/webui/test_teams_projection.py tests/webui/test_teams_e2e.py tests/agent/tools/test_team.py -q`
    → **428 passed**（35.36s）。
  - 用 `project_team_event()` 实跑 `tests/fixtures/teams/event.json` 的 `run.started`，输出
    `{"binding": {...}, "startedAt": "..."}` —— 这是下面 B1 的硬证据。
- 未确认项：前端 `bun` 在本机不可用（`bun: No such file or directory`），因此
  `webui/src/tests/team-*.test.*` **没有实际执行**，前端结论全部来自静态阅读，凡涉及前端运行期
  行为的判断都标了「未运行验证」。

## 1. 结论

**需修改。** 阻塞项 3 条（均为契约明文条款、与「线上行为」直接相关），另有 5 条应修。

整体工程质量高于平均：契约 §5/§6/§7 的多数硬边界实现得干净且有真实断言（见 §4），
问题集中在**跨语言锚点**和**授权面**这两处最容易在真实环境出事的缝隙。

阻塞项：

| # | 一句话 | 契约条款 |
| --- | --- | --- |
| B1 | `run.started` 前后端 data 白名单不一致，前端把每一条 `run.started` 解析成 `null` 丢弃 | §6（`run.started={binding,startedAt}`）、§6「Python/TS 用同一组 JSON fixture 测边界一致性」 |
| B2 | 运行查询/取消/重试没有任何会话归属校验，跨聊天可读、可取消、可重试 | §3（`teamTasks.cancel` 禁止跨会话；run 查询从认证会话推导身份）、A18 |
| B3 | 恢复路径未阻断 `team_dispatch` 重放，`session/recovery.py` 一行未改 | §6 末句「阻断既有主回合恢复重放 team_dispatch，显示手动重试入口」；plan Task 7 文件清单 |

## 2. 逐条对照表

| 契约条目 | 实现位置 (file:line) | 符合 | 备注 |
| --- | --- | --- | --- |
| §6 事件 data 判别形状（Python `EVENT_DATA_FIELDS`） | `nanobot/teams/events.py:30-40` | ✅ | 9 种 type 各只有一个白名单键，与 `tests/fixtures/teams/event.json` 逐字一致 |
| §6 `run.started={binding,startedAt}` / `run.finished={run}` | `nanobot/teams/repository.py:454-456`、`673-675`；`events.py:30-40` | ✅ | 服务端侧正确，实跑输出已验证 |
| §6 同一套白名单（TS 侧） | `webui/src/lib/teams/events.ts:264-267` | ❌ | `run.started` 被当成要 `data.run`，与契约/服务端相反 → B1 |
| §6 `seq` 单调、状态+事件同事务 | `nanobot/teams/repository.py:1096-1134`；`database.py:232-258` | ✅ | `last_seq` 只在 `_append_event` 内递增，与事件 INSERT 同连接同事务 |
| §6 广播失败不回滚、不在事务内 await | `nanobot/teams/scheduler.py:718-726`、`events.py:186-197` | ✅ | 先 `to_thread` 提交再投递，投递异常只 warning |
| §6 序号只有一个真值来源 | `scheduler.py:728-750` | ✅ | 调度器不造 seq，按 `team_runs.last_seq` 增量读库投递 |
| §6 `task.activity` ≤4 条/秒/任务、≤4KiB | `events.py:24-26,175-184,126-144`；`repository.py:847-871` | ✅ | 终态事件不走节流；每 run 5000 条上限后只更新快照 |
| §6 终态不可回退 | `repository.py:832-833,937-938`（`IllegalTaskTransition`）；`reducer.ts:112-122,137-140` | ✅ | 服务端与前端双向保护 |
| §6 快照与 lastSeq 同一读事务 | `repository.py:1288-1310`（`conn=None` 分支两次独立读）、`1037-1058` | ⚠️ | 见 R3 |
| §6 重启只标 interrupted、不重放 | `repository.py:565-601`；`service.py:272-283`；`teams_api.py:75-84` | ✅ | 无任何模型/工具调用点，纯落库 |
| §6 第二个 writer 拒绝团队调度 | `database.py:294-309,326-332`；`service.py:240-242`；`teams_api.py:75-84` | ✅ | `prepare_new_run` 抛 `StorageUnavailable`→503 |
| §6 阻断恢复重放 team_dispatch | `session/recovery.py`（**未修改**）、`teams/turn.py:86-110` | ❌ | 见 B3 |
| §6 取消：先 cancelling 再 cancelled，不伪造停止 | `scheduler.py:625-636,662-666`；`runs.py:47-58`；`reducer.ts:146-147` | ✅ | 在场句柄只回 `cancelling`，由收口写终态 |
| §6 取消与成功竞争以首个持久化为准 | `scheduler.py:591-612` + `repository.py:937-938` | ✅ | 先读快照再决定，终态后拒绝改写 |
| §7 不得持久化凭据 | `service.py:57-79`（`ProfileRecord` 无凭据字段）、`teams_api.py:25-58` | ✅ | 只冻结 id/name/prompt/modelId/selections |
| §7 工具参数/原始输出不进事件 | `events.py:42-93`（task/run 白名单）、`repository.py:1338-1355`（快照无 instruction） | ✅ | `test_task_event_never_carries_private_fields` 是真断言 |
| §7 `lastAction` 只由 `render_activity_action` 渲染 | `events.py:147-157`；唯一写点 `scheduler.py:755-759` → `repository.py:847-871` | ✅ | 旁路瑕疵见 O2 |
| §7 不生成百分比 | `webui/src/components/thread/TeamProgressPanel.tsx:1-143` | ✅ | 全文件无 `%`/percent |
| §7 并发：团队配额 + 同成员 1 + 全局 `_run_slots` | `scheduler.py:189-190,455-470`；`nanobot/agent/subagent.py:629` | ✅ | `execute_frozen` 与 `spawn` 共用同一个 `_run_slots` |
| §7 声明写集冲突串行 | `scheduler.py:145-148,461-462` | ✅ | 排序取锁防死锁；未声明 → `workspace-write` |
| §7 历史分页在同一组件/reducer 呈现 | `useTeamProgress.ts:91-111`（拉历史）→ `ThreadShell.tsx:1614-1617`（只渲染 `activeRun`） | ⚠️ | 见 R4 |
| §5 `team_dispatch` 不被普通聊天自动发现 | `nanobot/agent/tools/team.py:80`；`tools/loader.py:59,84` | ✅ | 有真实 loader 发现测试 |
| §5 Leader 与成员工具目录不同 | `teams/turn.py:198-213`；`subagent.py:379-381` | ✅ | Leader=core registry，成员=`scope="subagent"` 实目录 |
| §5 成员 scope 不含调度工具 | `SnapshotMemberCatalog.resolve`（`scheduler.py:103-128`）+ `_plugin_discoverable=False` | ✅ | |
| §5 整批校验不过则零启动 | `scheduler.py:334-359,384-431` | ✅ | 有真实 repository 断言（`executor.started == []`） |
| §5 `batchId` 幂等 / 异载荷 409 | `scheduler.py:241-252`；`repository.py:1138-1173` | ✅ | |
| §5 `tool_names is None` fail-closed | `nanobot/teams/execution.py:78-84` | ✅ | |
| §5 冻结档案，不按 agentId 重读盘 | `execution.py:94-104`；`scheduler.py:77-128` | ✅ | |
| §5 旧 spawn 行为不变 | `subagent.py:379-408`（只新增只读方法） | ✅ | 回归 428 项全绿 |
| §3 `requestId` 领域幂等 | `repository.py:1206-1229`、`runs.py:73-103` | ✅ | retry 重放返回同一 `newRunId` |
| §3 跨会话控制拒绝（403） | `runs.py:60-71,137-140`（只校验「任务是否属于该 run」） | ❌ | 见 B2 |
| §3 preset 失效拒绝、不静默默认 | `leader.py:112-122`；`runs.py:125-135` | ✅ | |
| §3 归档后禁新运行、历史可读 | `service.py:244-245,272-283` | ✅ | |
| §6 30 天/5000 条保留与剪枝 | `repository.py:1060-1094`（`prune_activity_events`，需 writer） | ✅ | 跨 workspace 范围有防护 |
| §6 未知 schema 降级到快照 | `events.py:105`；`events.ts:242-244,296-310` | ✅ | |
| §2 修订只追加、归属不可改 | `database.py:96-108,157-161`（触发器） | ✅ | 数据库级不变量 |
| §2 CAS `rowcount != 1` | `repository.py:253-264` | ✅ | |
| §2 同步 sqlite 走 `to_thread` 短事务 | `scheduler.py:718-720`；`repository.py:1233-1238` | ✅ | |
| §2 读路径不建库 | `database.py:232-239`；`repository.py:1233-1238` | ✅ | |
| plan Task 4「三处 `tools or self.tools` 改显式 None」 | `loop.py:1134,1530,2201` | ✅ | 已全是显式 `is None` |

## 3. 发现清单

### 阻塞

**B1 — `run.started` 前后端白名单不一致，前端静默丢弃全部 `run.started`**
- 位置：`nanobot/teams/events.py:30-40`（Python 白名单）↔ `webui/src/lib/teams/events.ts:264-267`（TS 解析）↔ `tests/fixtures/teams/event.json`（seq=1 那条）
- 问题：Python 的 `EVENT_DATA_FIELDS["run.started"] = ("binding", "startedAt")`，实跑投影输出就是
  `{"binding": {...}, "startedAt": "..."}`（无 `run` 键）；而 TS 把 `run.started` 和 `run.finished` 归为同一分支，
  要求 `data.run` 是完整 run 快照，否则 `parseTeamEvent` 返回 `null`。
  `useTeamProgress.ts:116-117` 对 `null` 直接 `return`，于是**每条 `run.started` 都被丢掉**；
  `reducer.ts:132,143` 里为 `run.started` 准备的「携带快照、可跳 seq 缺口」分支永远不会命中。
- 为什么是问题：这是契约点名的跨语言锚点（§6「边缘仅投影白名单字段」「Python/TS 用同一组 JSON fixture 测边界一致性」）。
  线上后果是团队面板在运行刚建立时拿不到 binding/leader 起点信息，全靠 hydrate 快照兜；
  更糟的是两端对同一 type 的解释已经分叉，后续任何人按 TS 理解去改 Python 都会静默错。
- 为什么测试没抓到：`webui/src/tests/team-events.test.ts:3` 的注释声称「跨语言锚点是 `tests/fixtures/teams/event.json`」，
  但全文（第 16-60 行）用的是**手写常量**，没有 import 任何 fixture；`grep fixtures/teams` 全仓只有
  Python 侧（`tests/teams/conftest.py`、`tests/webui/test_teams_projection.py`）读取。也就是说契约 §6
  要求的「同一组 fixture 测两侧一致性」根本没做，两边各自绿。
- 建议改法：按冻结契约（契约写死 `run.started={binding,startedAt}`），改 TS：
  1. `events.ts` 拆开分支——`run.started` 解析 `data.binding` + `data.startedAt` 成
     `{...envelope, binding, startedAt}`；`run.finished` 保持 `data.run`；
  2. `reducer.ts:132,143-145` 对 `run.started` 走「只更新 binding/leader 起点 + 接受 seq」，
     不要当快照应用；
  3. 在 `webui` 里 import `tests/fixtures/teams/event.json`（或加一条 CI 断言：把
     `EVENT_DATA_FIELDS` 导出成 JSON，由 TS 侧逐字比对），让 `team-events.test.ts` 真的吃这份 fixture。

**B2 — 运行读取/控制/重试缺少会话归属校验**
- 位置：`nanobot/teams/runs.py:47-58`（`cancel_run`）、`60-71`（`cancel_task`）、
  `79-96`（`retry_run`）；`nanobot/webui/teams_routes.py:228-249,152-160`
- 问题：`cancel_run(run_id)` 只按 runId 找句柄或落库，**完全不比对请求方会话**；`cancel_task` 的
  `_require_task` 只校验「taskId 是否属于这条 run」；`retry_run` 更是用 `repository.run_context(run_id)`
  取出的**原运行** chatId/sessionKey 去建新运行。`teams_routes` 只透传 payload 的 `runId/taskId`，
  认证会话的 chatId 从未参与判定。
- 为什么是问题：契约 §3 写明 `teamTasks.cancel`「禁止跨会话取消」、运行查询/mutation 从认证会话推导身份，
  验收矩阵 A18 要求「API/帧伪造 runId → 拒绝，不能读/取消其他会话任务」。当前同一 workspace 内
  一个已认证连接可以取消/重试另一个聊天的团队运行，重试还会把新运行的 turn 写进对方会话。
  这是本 patch 里唯一的**授权面缺口**，其余权限面（工具目录、fail-closed、writer 锁）都做得很严。
- 为什么测试没抓到：`tests/teams/test_run_control.py:225-239` 的 `test_cancel_task_not_in_run_is_forbidden`
  注释写着「任务不属于所声明的运行就是跨会话控制」，但它只覆盖了「task 不在这条 run 里」，
  没有第二个会话的用例——名字和语义都比断言宽。
- 建议改法：路由层把认证会话的 `chatId`/`sessionKey` 传进 controller；
  `TeamRunController.cancel_run/cancel_task/retry_run` 先 `repository.run_context(run_id)`，
  与调用方比对，不一致抛 `CrossSessionForbidden`（已有 403 映射，`teams_routes.py:156`）；
  新增用例：另一个 chatId 取消同 run → 403。

**B3 — 恢复路径未阻断 `team_dispatch` 重放**
- 位置：`nanobot/session/recovery.py:784-811`（`_queue_continuation`，本次**未修改**，
  `git status` 显示该文件不在改动列表）、`nanobot/teams/turn.py:86-110`、`nanobot/agent/loop.py:2213`
- 问题：恢复续接会以 `webui_turn_id = recovery:<id>` 发布一条新 inbound（`recovery.py:800-807`），
  走正常回合路径；`_team_context_for_turn` 看到 `session.metadata.team_binding` 仍在，
  就 `scheduler.start_run()` 开一条**新运行**并把 `team_dispatch` 挂进 Leader registry（`loop.py:2213-2216`）。
  全仓没有任何一处针对 recovery 的团队短路（`grep team_dispatch nanobot/session/` 无命中）。
- 为什么是问题：契约 §6 最后一句明写「阻断既有主回合恢复重放 team_dispatch，显示手动重试入口」，
  plan Task 7 的文件清单也把 `nanobot/session/recovery.py` 列为必改文件。现状是「用户点继续」
  之后模型可以再次派活，已经跑过的成员可能被重复触发副作用。
- 说明（避免夸大）：默认路径是 `awaiting_user`，不是重启后静默自动重放（`recovery.py:757-780`），
  所以需要一次用户点击；但契约要求的是**阻断**，现在既没阻断，也没有「手动重试入口」的引导文案。
- 建议改法：在 `_queue_continuation` 或 loop 的恢复分支上，对带 `RECOVERY_INBOUND_METADATA_KEY`
  且 session 有 `team_binding` 的回合不挂 `team_dispatch`（Leader 照常回答，但不得派活），
  并在响应里回一个指向 `teamRuns.retry` 的手动入口；补一条测试断言恢复回合的 registry 里没有该工具。

### 应修

**R1 — `maxTasks` 按批计，不是按运行计**
- `nanobot/teams/scheduler.py:338` 只校验 `len(declared) > max_tasks`，即单批上限。
- 契约/计划写的是「每次运行任务数 16，最高 64，**包括返工任务，不能靠重命名绕过**」。
  4 轮 × 16 = 最多 64 任务绕过「每次运行 16」的意图（若 limits 允许 maxDispatchRounds=8，可达 128）。
- 建议：改成 `self._persisted_task_count(self.run_id) + len(declared) > max_tasks`，
  或至少在 run 级累计计数。

**R2 — `TeamDispatchTool` 只把两种异常转成工具错误**
- `nanobot/agent/tools/team.py:129-132` 只捕获 `(TeamValidationError, DispatchKeyConflict)`。
  契约 §5/plan Task 6 的口径是「整批校验失败不打断回合」；但 `_persist_batch` /
  `finish_task` 抛的 `IllegalTaskTransition`、`StorageUnavailable`（DB 忙）会穿透工具层，
  直接把 Leader 回合打崩。回合最终仍会被 `_finish_team_turn` 收口，但 Leader 拿不到可解释的错误文本。
- 建议：把 `TeamValidationError / DispatchKeyConflict / IllegalTaskTransition / StorageUnavailable`
  一并转成 `ToolResult.error`，或在 dispatch 内兜底并回一条「本批未能落地」的结构化结果。

**R3 — 快照与 `lastSeq` 不在同一读事务**
- `nanobot/teams/repository.py:1288-1310`：`conn=None` 分支用 `self._read(...)` 读 run 行、
  再用**另一个连接**读 task 行（`1233-1238` 每次新开 `connect()`）。并发事件追加时，
  返回的 `lastSeq` 可能落后于 `tasks`。`events_page`（`1037-1058`）同样是「先读 run 再读事件」两个连接，
  `hasMore`/`resetRequired` 在竞态下可能不准。
- 契约 §6：「快照与 `lastSeq` 从同一数据库读事务获得」。
- 建议：`_run_snapshot` 的 `conn=None` 分支改成一个连接内顺序读完（或显式 `BEGIN`/`COMMIT` 的一次读事务）；
  `events_page` 用同一连接读两段。

**R4 — 历史运行拉到了但不呈现**
- `webui/src/hooks/useTeamProgress.ts:91-111` 分页拉了历史 run，`useTeamProgress.ts:213-221` 也返回了 `runs`，
  但 `webui/src/components/thread/ThreadShell.tsx:1614-1617` 只渲染 `activeRun`（且固定在输入区上方，
  不在对应 turn 下面）。
- 契约 §7：「历史分页加载 run 快照，在对应 turn 下呈现，实时和历史走同一组件与 reducer」；
  plan Task 10 Done：「页面可看到……历史」。
- 建议：`runs` 按 `turnId` 索引后，在对应轮次位置渲染同一个 `TeamProgressPanel`（折叠为摘要）。

**R5 — 前端补拉只回快照，`events` 接口与 `resetRequired` 协议无人消费**
- `webui/src/lib/teams/api.ts:236` 定义了 `loadTeamRunEvents`，全仓无调用点；
  `useTeamProgress.ts:70-89` 的 `resync` 只调 `loadTeamRun`。
- 行为上不算错（快照是更强的真值），但契约 §6 的补拉协议（`afterSeq/hasMore/resetRequired`）
  端到端没有任何消费方与服务端断言，`tests/webui/test_teams_projection.py` 也没测这条路径。
- 建议：至少在缺口场景走 `loadTeamRunEvents`，收到 `resetRequired` 时回快照；否则把该 API 与
  服务端接口一起标为未接线，避免「接口存在但没人用」。

### 可选

- **O1** `repository.py:560-562`：`run.finished` 的 run 快照在 `_append_event` **之前**构造，
  其 `lastSeq` 比事件自身 seq 小 1；fixture（`event.json` seq=8 / lastSeq=8）与真实行为不一致。
  reducer 的 `applyRunSnapshot`（`reducer.ts:103`）因此会把 `lastSeq` 停在 seq-1。建议补一条
  「事件 seq 与 data.run.lastSeq 关系」的测试并统一口径。
- **O2** `repository.py:836-839`：`update_task_status` 无条件覆盖 `last_action`。
  任务进入 `cancelling` 时会把刚记录的「最近公开动作」抹成 `null`。建议改成
  `last_action = COALESCE(?, last_action)`。
- **O3** `tests/teams/test_run_control.py:423` 的 `assert fresh_executor.started == []` 是**自证式断言**：
  这个 FakeExecutor 从头到尾没有任何调用点，跑不跑恢复它都是空的。它给人「已验证不重放」的错觉，
  但实际证明不了。建议改为断言恢复路径的调用图（例如 monkeypatch 记录「恢复期间没有 executor 调用」）
  或至少在断言旁注明这是防回归哨兵而非证据。
- **O4** `useTeamProgress.ts:197-203` `cancelTask` 丢弃服务端返回的 `{taskId,status}`；
  `cancelRun` 会用响应更新本地状态（`184-192`），两者不对称，单任务停止后卡片要等下一个事件才变。
- **O5** `scheduler.py:315-332` `run_pending` 没有 `dispatch` 那样的 `CancelledError` 收口分支，
  被取消时结果集丢失，重试运行可能以 partial/failed 收口而没有 cancelled 语义。

## 4. 我核对过且确认没问题的清单

（避免只报问题不报覆盖。以下每条都读过实际代码，且多数有真实断言支撑。）

**事件与投影**
- `EVENT_DATA_FIELDS`（`events.py:30-40`）与 `tests/fixtures/teams/event.json` 的 9 种 type 逐字一致；
  `TASK_WIRE_FIELDS` / `RUN_WIRE_FIELDS` 与契约 `TeamTaskSnapshot` / `TeamRunSnapshot` 字段一一对应，无多余字段。
- 未知 schema 版本 / 未知 type 一律返回 `None`（`events.py:105`），前端对应 null（`events.ts:242,296-310`），
  普通聊天帧不会被误判为团队帧（`team-events.test.ts:145-151` 有真断言）。
- 超预算活动先削 `result` 再削 `lastAction`，仍超限整条丢弃，且**只削投影副本不改动持久事件**
  （`test_activity_over_budget_drops_the_rebuildable_part:149-167` 断言了这一点，是好测试）。
- 节流 4 条/秒/任务（`test_activity_gate_is_four_per_second_per_task:183-192` 用假时钟，非 sleep）。
- seq 唯一真值、终态事件不丢、投递类型集合 == 落库类型集合
  （`test_handle_publishes_exactly_the_persisted_events:294-302` 把线上投递与 `repository.list_events` 对比，是**服务端真断言**而非自证）。

**存储与并发**
- `last_seq` 只在 `_append_event` 递增且与事件 INSERT 同连接同事务（`repository.py:1096-1134`）。
- CAS 用 `rowcount != 1`，不是「先读再写」（`repository.py:253-264`）。
- 修订只追加、任务归属不可改，由 SQLite 触发器在库层兜住（`database.py:96-108,157-161`）——
  这是少数「不依赖调用方守规矩」的设计，值得肯定。
- 第二 writer：`hold_writer()` 抢不到 → `writer_owned=False` → `prepare_new_run` 抛
  `StorageUnavailable`（`teams_api.py:75-84`、`service.py:240-242`），并且
  `mark_interrupted_runs` / `prune_activity_events` 都要求 writer（`test_startup_cleanup_requires_the_writer:446-452`）。
- 重启清理只写库、不调任何执行器（`repository.py:565-601`），且产出 `run.finished` 让 UI 拿到重试入口。
- 跨 workspace 作用域防护：`team_events` / `team_dispatches` 无 `workspace_key` 列，
  `record_dispatch` 把作用域守卫放在幂等预检**之前**（`repository.py:1148-1151`），避免泄露他 workspace 的 batch 存在性——这个细节是对的。
- 资源锁排序取锁（`scheduler.py:145-148`）、未声明写集共用 `workspace-write`；
  `test_disjoint_write_sets_run_in_parallel:220-232` 用 `asyncio.Event` 屏障断言 `peak == 2`，
  `test_undeclared_write_set_is_serialized:235-242` 断言 `peak == 1` —— 真并行/真串行都被覆盖。
- 同成员串行、依赖阻塞、上游结果进下游 instruction（`test_scheduler.py:245-284`）。
- 失败不牵连成功兄弟、blocked 传播（`test_scheduler.py:259-274`）。
- 取消：`cancelling` 不等于已停（`test_run_control.py:203-222` 断言接口先回 `cancelling`、
  收口后才 `cancelled`）；单任务取消不动成功兄弟（`test_run_control.py:242+`）。
- 重试只复制 failed/interrupted + blocked 下游、成功前置变只读引用、`requestId` 幂等、
  非终态拒绝、preset 失效拒绝（`test_run_control.py:304-385` 全部基于 repository 真实状态）。

**权限面**
- `team_dispatch` 不被 loader 自动发现，且有**真 loader 发现测试**
  （`test_tool_is_not_auto_loaded_into_normal_chats:163-167` 调 `ToolLoader().discover()`）。
- 模型可见 schema 只有 `batchId/tasks`，`additionalProperties=false`；多传 `runId` 被 `validate_params` 挡掉
  （`test_team.py:140-160`）。
- Leader 与成员用两份不同目录：Leader=主循环 registry，成员=`subagent_tool_names()`（`scope="subagent"` 实目录，
  `turn.py:198-213` / `subagent.py:379-381`）。成员档案里写了一个已停用的工具名不会被凭空造出来
  （`SnapshotMemberCatalog` 求交，`scheduler.py:103-128`）。
- Team 模式移除普通 `spawn`（`leader.py:28,100-102`），且 `build_leader_registry` 建**新 registry**，
  不 `unregister` 全局工具 —— 这是契约 §5 最贵的失败模式，实现正确。
- `tool_names is None` fail-closed（`execution.py:78-84`）；Team 模式与 session `disabled_tools` 取交集
  （`loop.py:2202-2216`）。
- 三处 `tools or self.tools` 已全部改为显式 `is None`（`loop.py:1134,1530,2201`），空 registry 不再静默扩权。
- 子执行共用同一个 `_run_slots`（`subagent.py:629`），没有第二条 LLM 循环（`execution.py` 全文无 provider 调用）。

**边界与脱敏**
- 凭据不进快照：`ProfileRecord` 无任何凭据字段，`AgentProfileDirectory` 显式不搬展示字段与凭据
  （`service.py:57-79`、`teams_api.py:25-58`）。
- 工具参数/原始 shell 输出不进事件：`test_task_event_never_carries_private_fields:108-124` 用一个
  带 `instruction`/`arguments.token` 的假事件断言投影后全部消失，是真断言。
- 面板无百分比、无危险 HTML（`TeamProgressPanel.tsx` 全文无 `%`/`dangerously`）。
- 重连 hydrate 只补未收口运行，且走同一个白名单投影（`channels/websocket/runtime.py:435-449`）。
- 团队事件不进 transcript（`outbound_wire.py:216-219` persistence=transient），状态真值在 DB。

**取消/收口语义**
- 终态判定顺序「取消 > Leader 未交付 > 全成功 > 混合 partial > 无结果 failed」
  （`scheduler.py:688-699`）与契约 §6 一致；无派单直接作答 → completed 有测试
  （`test_scheduler.py:422-428`）。
- 回合异常/未交付也会收口（`loop.py:2087-2117`），不会留下永远 running 的运行。
